"""Iterative read-only investigation loop (Epic #328 Phase B / Issue #330).

Issue #286's :func:`investigation_agent.investigate` answers a question in
ONE round: keyword-match candidate paths, read them, make a single reasoning
call, done. That is the right shape for an Inquiry answer, and it stays
exactly as it is. It is the wrong shape for 「わからない」, where nobody --
not the developer, not the first round -- knows the answer yet: the useful
move there is to keep going, carrying forward what is still missing.

This module adds that loop on top of the same read-only machinery:

1. **Cross-source candidate retrieval** (deterministic; narrows candidates
   only, never decides -- Principle 6). Beyond Issue #286's file-path keyword
   match, a round also pulls candidates from the indexed symbol table
   (qualified names and docstrings), the entrypoint index (labels, routes,
   handlers), and a content scan of the snapshot's indexed files -- so a
   question never fails just because no FILE NAME happened to contain the
   keyword.
2. **Carry-over between rounds and between retries.** Every round returns
   ``search_leads`` (identifiers/paths worth reading next), ``open_hypotheses``
   and ``missing_evidence``; those are fed into the next round's retrieval and
   prompt, and the caller persists them so a later retry resumes from the
   leads already earned instead of starting from the question again.
3. **Explicit budgets and a finite stop reason.** ``answered`` |
   ``budget_exhausted`` | ``no_new_evidence`` | ``unresolved`` | ``failed`` --
   the loop never continues on the model's say-so alone: a round that reads no
   NEW file stops deterministically.

Findings come back in the Epic #328 Phase A shape (``claim_kind`` of fact /
inference / hypothesis / unknown / conflict) so the caller can persist them
directly as ``origin_role='investigation'`` findings. A hypothesis without
competing explanations and refutation conditions is dropped (counted in
``pruned_findings``), exactly as an unverifiable evidence citation is --
the Phase A contract does not accept a hypothesis that is merely a
low-confidence claim.

Read-only boundary (Principle 5, Principle 8): every file read goes through
``git_ops`` against the PINNED ``commit_sha``; the index/content queries read
rows captured for that snapshot. No writes, no subprocess beyond read-only
git plumbing, no network beyond the LLM call.

Fail-closed (Principle 6/7): a mock client, a non-reasoning model, a git
failure, an API failure or invalid structured output all yield
``stop_reason='failed'``. A failure in round 1 produces no findings at all; a
failure in a later round keeps the rounds already validated and stops -- it
never fabricates a conclusion from the failed round.

probe-agent:
  role: iterative read-only investigation over a pinned snapshot with
    cross-source candidate retrieval, carried-over search leads, and a
    finite stop reason
  capability: interactive-system-understanding
  element_type: core
  operation_kind: io
  state_effects: [database-read, external-api]
  probe_value: Verify rounds stop deterministically when no new evidence is read, that carried-over search leads survive a retry, that a later-round failure keeps earlier findings, and that only the pinned snapshot is ever read.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .git_ops import GitError, list_tree_entries, read_file_at_commit  # noqa: F401
from .interview_language import language_directive
from .investigation_agent import (
    InvestigationBudget,
    InvestigationEvidenceItem,
    InvestigationRuntimeEvidenceItem,
    ReadSnippet,
    RuntimeFactCandidate,
    _allowed_ranges,
    _clamp,
    _gather_runtime_candidates,
    _hint_keywords,
    _is_verifiable,
    _keywords,
    _read_candidates,
    _score_path,
    _strip_fences,
)
from .joint_understanding import CLAIM_KINDS
from .snapshot_explorers import (
    ExplorerResult,
    call_graph_candidates,
    dependency_candidates,
    git_history_candidates,
    merge_candidates,
    runtime_discovery_candidates,
)
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .runtime_alignment import RUNTIME_CHECK_STATES

PROMPT_VERSION = "investigation-loop-v2"
SCHEMA_VERSION = "investigation-loop-v2"

# Finite stop reasons (Principle 6). Every loop ends with exactly one:
#   answered         -- a round concluded with grounded findings and nothing
#                       it still needed
#   budget_exhausted -- rounds/LLM calls/files/chars/time ran out first
#   no_new_evidence  -- a round could not read any file it had not already
#                       read; continuing would re-ask the same question of
#                       the same text
#   unresolved       -- ran to a stop without a grounded conclusion
#   failed           -- fail-closed (config/git/API/schema)
STOP_REASONS = (
    "answered", "budget_exhausted", "no_new_evidence", "unresolved", "failed",
)

# Issue #339: `failed` lumped every way a run could end badly into one value,
# so a RESEARCH LIMITATION (the system looked and could not establish the
# answer) and an EXECUTION FAILURE (the system could not look at all) were
# indistinguishable. They mean opposite things to the developer and they have
# opposite consequences:
#
# - a limitation is a real, evidence-backed result. "We could not determine X"
#   is a first-class finding, and the round that produced it is trustworthy.
# - a failure is the ABSENCE of a result. It must not produce a finding at all,
#   because a finding would assert something about the system that nothing was
#   read to support -- only the audit event and the recovery action remain.
RESEARCH_LIMITATIONS = ("budget_exhausted", "no_new_evidence", "unresolved")

# Finite execution-failure classes. Each names WHERE the run broke, because the
# recovery differs: configuration and auth are operator actions, a missing
# snapshot cannot be retried as-is, and an API/schema/timeout failure is a
# retry.
EXECUTION_FAILURE_CLASSES = (
    "config_invalid",
    "snapshot_unavailable",
    "api_failure",
    "schema_invalid",
    "timeout",
)

# The finite outcome class a caller (and the UI) reads instead of inspecting
# `stop_reason` and guessing which side of the split it is on.
OUTCOME_CLASSES = ("answered", "research_limitation", "execution_failure")


def outcome_class_for(stop_reason: str) -> str:
    """The finite outcome class for a stop reason (deterministic)."""
    if stop_reason == "answered":
        return "answered"
    if stop_reason in RESEARCH_LIMITATIONS:
        return "research_limitation"
    return "execution_failure"

DEFAULT_MAX_ROUNDS = 3
DEFAULT_TOTAL_LLM_CALLS = 3
DEFAULT_TOTAL_FILES = 30
DEFAULT_TOTAL_SNIPPET_CHARS = 90_000
DEFAULT_FILES_PER_ROUND = 10
DEFAULT_TOTAL_TIMEOUT_SECONDS = 120

_ROUNDS_BOUNDS = (1, 5)
_TOTAL_LLM_CALLS_BOUNDS = (1, 10)
_TOTAL_FILES_BOUNDS = (1, 80)
_TOTAL_CHARS_BOUNDS = (1_000, 400_000)
_FILES_PER_ROUND_BOUNDS = (1, 40)
_TOTAL_TIMEOUT_BOUNDS = (1, 600)

# Deterministic caps on how many carried-over items are kept between rounds.
# Carry-over exists to keep a retry from losing its leads, not to grow
# without bound.
MAX_CARRIED_LEADS = 20
MAX_CARRIED_HYPOTHESES = 10
MAX_CARRIED_MISSING = 10
MAX_FINDINGS_PER_ROUND = 10


@dataclass
class InvestigationLoopBudget:
    """Deterministic caps over the WHOLE loop, clamped on construction."""

    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_llm_calls: int = DEFAULT_TOTAL_LLM_CALLS
    max_files: int = DEFAULT_TOTAL_FILES
    max_snippet_chars: int = DEFAULT_TOTAL_SNIPPET_CHARS
    max_files_per_round: int = DEFAULT_FILES_PER_ROUND
    max_runtime_facts: int = 10
    timeout_seconds: int = DEFAULT_TOTAL_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        self.max_rounds = _clamp(int(self.max_rounds), _ROUNDS_BOUNDS)
        self.max_llm_calls = _clamp(int(self.max_llm_calls), _TOTAL_LLM_CALLS_BOUNDS)
        self.max_files = _clamp(int(self.max_files), _TOTAL_FILES_BOUNDS)
        self.max_snippet_chars = _clamp(int(self.max_snippet_chars), _TOTAL_CHARS_BOUNDS)
        self.max_files_per_round = _clamp(
            int(self.max_files_per_round), _FILES_PER_ROUND_BOUNDS
        )
        self.max_runtime_facts = _clamp(int(self.max_runtime_facts), (0, 20))
        self.timeout_seconds = _clamp(int(self.timeout_seconds), _TOTAL_TIMEOUT_BOUNDS)


@dataclass
class LoopCarryOver:
    """What survives between rounds -- and between a run and its retry.

    ``read_paths`` is what makes ``no_new_evidence`` decidable: a round that
    can only offer files already read has nothing left to contribute.
    """

    search_leads: List[str] = field(default_factory=list)
    open_hypotheses: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    read_paths: List[str] = field(default_factory=list)


@dataclass
class LoopFinding:
    """One validated finding, already in the Phase A contract's shape."""

    claim_kind: str
    statement: str
    evidence: List[InvestigationEvidenceItem] = field(default_factory=list)
    runtime_evidence: List[InvestigationRuntimeEvidenceItem] = field(default_factory=list)
    competing_explanations: List[str] = field(default_factory=list)
    refutation_conditions: List[str] = field(default_factory=list)
    next_investigation: Optional[str] = None
    uncertainty: str = ""
    round_index: int = 1


@dataclass
class LoopRound:
    round_index: int
    status: str  # completed | unresolved | failed
    conclusion: str = ""
    findings: List[LoopFinding] = field(default_factory=list)
    search_leads: List[str] = field(default_factory=list)
    open_hypotheses: List[str] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    read_snippets: List[ReadSnippet] = field(default_factory=list)
    candidate_paths: List[str] = field(default_factory=list)
    unread_candidates: List[str] = field(default_factory=list)
    pruned_evidence: List[Dict[str, object]] = field(default_factory=list)
    pruned_findings: int = 0
    # Issue #339: one entry per exploration source this round used, each with
    # its own revision, budget consumption, and failure. Per source because
    # "the round only read the pinned revision" has to be checkable for each
    # source separately, not asserted for the round as a whole.
    source_audits: List["ExplorerResult"] = field(default_factory=list)
    failure_class: Optional[str] = None
    files_read: int = 0
    chars_read: int = 0
    llm_calls: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


@dataclass
class InvestigationLoopResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    stop_reason: str = "unresolved"
    rounds: List[LoopRound] = field(default_factory=list)
    findings: List[LoopFinding] = field(default_factory=list)
    carry_over: LoopCarryOver = field(default_factory=LoopCarryOver)
    # Issue #339: set only for an EXECUTION failure -- never for a research
    # limitation, which is a real result rather than a broken run.
    failure_class: Optional[str] = None
    error: Optional[str] = None

    @property
    def total_llm_calls(self) -> int:
        return sum(r.llm_calls for r in self.rounds)

    @property
    def outcome_class(self) -> str:
        return outcome_class_for(self.stop_reason)

    @property
    def is_research_limitation(self) -> bool:
        return self.outcome_class == "research_limitation"


# --- Raw response schema ------------------------------------------------------


class _RawEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=500)
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    summary: str = Field(default="", max_length=1_000)


class _RawRuntimeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(..., min_length=1, max_length=500)
    runtime_check: str = Field(..., min_length=1, max_length=20)
    summary: str = Field(default="", max_length=1_000)


class _RawFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_kind: str = Field(..., min_length=1, max_length=20)
    statement: str = Field(..., min_length=1, max_length=4_000)
    evidence: List[_RawEvidence] = Field(default_factory=list, max_length=20)
    runtime_evidence: List[_RawRuntimeEvidence] = Field(default_factory=list, max_length=20)
    competing_explanations: List[str] = Field(default_factory=list, max_length=10)
    refutation_conditions: List[str] = Field(default_factory=list, max_length=10)
    next_investigation: Optional[str] = Field(default=None, max_length=2_000)
    uncertainty: str = Field(default="", max_length=2_000)


class _RawRoundResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., min_length=1, max_length=20)
    conclusion: str = Field(default="", max_length=2_000)
    findings: List[_RawFinding] = Field(default_factory=list, max_length=10)
    search_leads: List[str] = Field(default_factory=list, max_length=20)
    open_hypotheses: List[str] = Field(default_factory=list, max_length=10)
    missing_evidence: List[str] = Field(default_factory=list, max_length=10)


_RAW_STATUSES = ("completed", "unresolved")


_SYSTEM_PROMPT = """\
You are the read-only Investigation Agent of probe-agent's JOINT \
UNDERSTANDING flow: the developer said they do not know the answer either, \
so your job is to establish what is actually true about the system, round by \
round, and to be explicit about what you could not establish.

You are given the developer's question, a bounded set of source excerpts read \
from a PINNED git snapshot (never the mutable working tree), optionally a set \
of DETERMINISTIC runtime trace facts, and -- from round 2 on -- what earlier \
rounds already established, still suspect, and still lack. You have no write \
access, cannot run code, and cannot look beyond the supplied excerpts/facts.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "status": "completed | unresolved",
  "conclusion": "1-3 sentences answering the question, or stating what could not be determined",
  "findings": [
    {
      "claim_kind": "fact | inference | hypothesis | unknown | conflict",
      "statement": "one self-contained statement",
      "evidence": [{"path": "src/x.py", "start_line": 1, "end_line": 20, "summary": "what this span shows"}],
      "runtime_evidence": [{"component_id": "x_handler", "runtime_check": "match | mismatch", "summary": "..."}],
      "competing_explanations": ["another explanation the same evidence would allow"],
      "refutation_conditions": ["what you would have to observe for this to be wrong"],
      "next_investigation": "the read that would distinguish those explanations, or null",
      "uncertainty": "what is still unverified about THIS statement"
    }
  ],
  "search_leads": ["identifier, symbol, or path worth reading next"],
  "open_hypotheses": ["hypothesis still worth testing"],
  "missing_evidence": ["evidence you needed but did not have"]
}

Rules:
- Separate the KINDS of statement honestly. "fact" is what the supplied \
excerpts/facts plainly show. "inference" is a conclusion drawn from several \
pieces of evidence. "hypothesis" is a candidate explanation that still needs \
checking. "unknown" records something you could NOT determine -- it is a \
first-class result, never something to fill in with a plausible guess. \
"conflict" records two sources that cannot both be right.
- Every "hypothesis" MUST carry at least one "competing_explanations" entry \
and at least one "refutation_conditions" entry. A hypothesis without them \
will be discarded: a hypothesis is not merely a low-confidence claim.
- Only cite "path"/"start_line"/"end_line" that appear in the supplied \
excerpts, within the line range actually shown for that path. Never invent a \
path, a line range, or a file that was not supplied.
- "runtime_evidence" may only cite a "component_id" from the supplied runtime \
facts section, and only when those facts were actually FRESH (never claim \
"match" for facts marked stale or unobserved -- omit them instead).
- "search_leads" is what makes the NEXT round useful: name concrete \
identifiers, symbols, modules or paths, not topics. Do not repeat leads that \
the supplied excerpts already answered.
- Set "status" to "completed" only when the supplied material answers the \
question; otherwise "unresolved" with the findings you could ground plus what \
is missing. Never guess merely to appear helpful.
"""


def _system_prompt(language: str) -> str:
    return _SYSTEM_PROMPT + language_directive(language) + "\n"


# --- Deterministic cross-source candidate retrieval ---------------------------


def _index_candidates(
    conn: "sqlite3.Connection",
    system_id: int,
    snapshot_id: int,
    keywords: Sequence[str],
    limit: int,
) -> List[str]:
    """Candidate paths from the symbol / entrypoint indexes and file content.

    Deterministic retrieval only (Principle 6): this narrows WHICH files the
    reasoning model may read, never what the answer is. It exists because
    Issue #286's path-name matching misses the common case where the concept
    lives in a symbol name, a route label, or the body of a file whose NAME
    says nothing (「ファイル名や冒頭部分だけに依存しない」, Epic #328).

    Ordering is by (match count desc, path asc) so the same snapshot and the
    same keywords always produce the same candidate list.
    """
    if not keywords:
        return []
    hits: Dict[str, int] = {}

    def _add(path: Optional[str], weight: int = 1) -> None:
        if path:
            hits[path] = hits.get(path, 0) + weight

    like_params = [f"%{kw}%" for kw in keywords]
    symbol_clause = " OR ".join(
        ["LOWER(qualified_name) LIKE ?"] * len(keywords)
        + ["LOWER(COALESCE(docstring, '')) LIKE ?"] * len(keywords)
    )
    for row in conn.execute(
        f"""SELECT path, qualified_name FROM code_symbols
            WHERE snapshot_id = ? AND system_id = ? AND ({symbol_clause})
            ORDER BY path, qualified_name
            LIMIT 200""",
        (snapshot_id, system_id, *like_params, *like_params),
    ).fetchall():
        _add(row["path"], 2)

    entry_clause = " OR ".join(
        ["LOWER(label) LIKE ?"] * len(keywords)
        + ["LOWER(COALESCE(route_path, '')) LIKE ?"] * len(keywords)
        + ["LOWER(handler_qualified_name) LIKE ?"] * len(keywords)
    )
    for row in conn.execute(
        f"""SELECT handler_path FROM code_entrypoints
            WHERE snapshot_id = ? AND system_id = ? AND ({entry_clause})
            ORDER BY handler_path
            LIMIT 200""",
        (snapshot_id, system_id, *like_params, *like_params, *like_params),
    ).fetchall():
        _add(row["handler_path"], 2)

    # Content scan over the snapshot's indexed files (committed content only).
    content_clause = " OR ".join(["LOWER(CAST(content AS TEXT)) LIKE ?"] * len(keywords))
    for row in conn.execute(
        f"""SELECT path FROM snapshot_files
            WHERE snapshot_id = ? AND inclusion_status = 'indexed' AND ({content_clause})
            ORDER BY path
            LIMIT 200""",
        (snapshot_id, *like_params),
    ).fetchall():
        _add(row["path"], 1)

    ordered = sorted(hits.items(), key=lambda item: (-item[1], item[0]))
    return [path for path, _ in ordered[:limit]]


def _round_candidates(
    *,
    paths: Sequence[str],
    keywords: Sequence[str],
    index_paths: Sequence[str],
    already_read: Set[str],
    limit: int,
    structural_paths: Sequence[str] = (),
) -> List[str]:
    """Merge the candidate sources, minus what was read, in priority order.

    Index/content hits come first: on a second round they are what carries the
    previous round's leads, whereas the path-name scan mostly re-proposes the
    same files.

    ``structural_paths`` (Issue #339's dependency / call-graph / git-history /
    runtime sources) comes LAST, and that ordering is the contract. Those
    sources answer "what is around these files", so they are only meaningful
    relative to the files the question itself pointed at -- putting them earlier
    would let them displace the direct match on a small per-round read budget,
    which is breadth bought by losing the thing that was actually asked about.
    """
    tracked = set(paths)
    ordered: List[str] = []
    seen: Set[str] = set()
    for path in (
        list(index_paths)
        + _path_name_candidates(paths, keywords, limit * 2)
        + list(structural_paths)
    ):
        if path in seen or path in already_read or path not in tracked:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered[:limit]


def _path_name_candidates(
    paths: Sequence[str], keywords: Sequence[str], limit: int
) -> List[str]:
    if not keywords:
        return []
    scored = [(_score_path(p, list(keywords)), p) for p in paths]
    scored = [(score, p) for score, p in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [p for _, p in scored[:limit]]


def _dedupe(values: Sequence[str], limit: int) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


# The floor below which starting an LLM call cannot honour the remaining time
# budget. Not a soft hint: below it the loop stops rather than making a call it
# would have to abandon.
_MIN_CALL_SECONDS = 2.0

# How many already-plausible files seed the structural sources. They answer
# "what is around THESE files", so they need somewhere to start; the seed set is
# capped so the added breadth cannot grow the round's cost without bound.
_MAX_SEED_PATHS = 5


def _seed_paths(*, carry: LoopCarryOver) -> List[str]:
    """The files the structural sources explore around.

    ONLY files already established as relevant: what earlier rounds actually
    read, most recent first, since the newest read is the current lead.
    Deliberately NOT the path-name or index matches -- seeding from a keyword
    guess would make the structural sources a second guess about the same
    guess, and their whole value is being anchored to a file that is known to
    matter.

    An empty result means nothing is established yet, and the caller skips the
    structural sources entirely for that round.
    """
    seeds: List[str] = []
    for path in reversed(carry.read_paths):
        if path and path not in seeds:
            seeds.append(path)
        if len(seeds) >= _MAX_SEED_PATHS:
            break
    return seeds


def _structural_sources(
    conn,
    *,
    system_id: int,
    snapshot_id: int,
    seed_paths: Sequence[str],
    seed_symbols: Sequence[str],
    include_runtime: bool,
) -> List[ExplorerResult]:
    """Run the DB-backed structural sources, each independently audited.

    A source that raises is recorded as failed and skipped: one broken source
    must not fail the round, and it must not be replaced by an unbounded
    fallback search either (Issue #339).
    """
    results: List[ExplorerResult] = []
    plans = [
        ("dependency", lambda: dependency_candidates(
            conn, snapshot_id=snapshot_id, system_id=system_id, seed_paths=seed_paths,
        )),
        ("call_graph", lambda: call_graph_candidates(
            conn, snapshot_id=snapshot_id, system_id=system_id,
            seed_paths=seed_paths, seed_symbols=seed_symbols,
        )),
    ]
    if include_runtime:
        plans.append(("runtime_facts", lambda: runtime_discovery_candidates(
            conn, snapshot_id=snapshot_id, system_id=system_id,
        )))
    for source_kind, run in plans:
        started = time.monotonic()
        try:
            outcome = run()
        except sqlite3.Error as exc:
            outcome = ExplorerResult(
                source_kind=source_kind, revision=str(snapshot_id), error=str(exc),
            )
        outcome.elapsed_seconds = time.monotonic() - started
        results.append(outcome)
    return results


# --- Prompt assembly ----------------------------------------------------------


def _build_user_prompt(
    *,
    question: str,
    research_focus: Optional[str],
    round_index: int,
    snippets: Sequence[ReadSnippet],
    runtime_candidates: Sequence[RuntimeFactCandidate],
    carry_over: LoopCarryOver,
    established: Sequence[LoopFinding],
) -> str:
    parts = [f"Developer question: {question}", f"Investigation round: {round_index}"]
    if research_focus:
        parts.append(f"Research focus: {research_focus}")
    if established:
        lines = [
            f"- [{f.claim_kind}] {f.statement}" for f in established[-MAX_FINDINGS_PER_ROUND:]
        ]
        parts.append("## Already established in earlier rounds\n" + "\n".join(lines))
    if carry_over.open_hypotheses:
        parts.append("## Still-open hypotheses\n" + "\n".join(f"- {h}" for h in carry_over.open_hypotheses))
    if carry_over.missing_evidence:
        parts.append("## Evidence still missing\n" + "\n".join(f"- {m}" for m in carry_over.missing_evidence))
    if carry_over.read_paths:
        parts.append(
            "## Already read in earlier rounds (do not ask for these again)\n"
            + "\n".join(f"- {p}" for p in carry_over.read_paths[-40:])
        )
    parts.append("## Candidate source excerpts (pinned snapshot; the ONLY files you may cite)")
    for s in snippets:
        note = " (truncated)" if s.truncated else ""
        parts.append(f"### {s.path} (lines {s.start_line}-{s.end_line}{note})\n{s.content}")
    if runtime_candidates:
        parts.append(
            "## Runtime facts (deterministic aggregates; the ONLY component_id "
            "values you may cite in runtime_evidence)"
        )
        for c in runtime_candidates:
            parts.append(
                f"### {c.component_id}\n"
                f"freshness={c.provenance.freshness}, call_count={c.call_count}, "
                f"error_rate={c.error_rate}, "
                f"first_observed_at={c.provenance.first_observed_at}, "
                f"last_observed_at={c.provenance.last_observed_at}"
            )
    return "\n\n".join(parts)


# --- Round validation ---------------------------------------------------------


def _validate_round_findings(
    raw_findings: Sequence[_RawFinding],
    *,
    allowed: Dict[str, int],
    runtime_by_id: Dict[str, RuntimeFactCandidate],
    round_index: int,
) -> "tuple[List[LoopFinding], List[Dict[str, object]], int]":
    """Keep only findings that satisfy the Phase A contract.

    Drops (never rewrites): an unknown ``claim_kind``, a hypothesis without
    competing explanations/refutation conditions, and any finding whose
    evidence citations all fail snapshot validation. Individually
    unverifiable citations are pruned the same way Issue #286 prunes them.
    A finding with no citations at all is kept only when its ``claim_kind``
    is one that does not assert anything about the code (``unknown``).
    """
    findings: List[LoopFinding] = []
    pruned_evidence: List[Dict[str, object]] = []
    dropped = 0

    for raw in raw_findings:
        if raw.claim_kind not in CLAIM_KINDS:
            dropped += 1
            continue
        if raw.claim_kind == "hypothesis" and (
            not raw.competing_explanations
            or not raw.refutation_conditions
            or not raw.next_investigation
        ):
            dropped += 1
            continue

        valid_evidence: List[InvestigationEvidenceItem] = []
        had_citations = bool(raw.evidence)
        for item in raw.evidence:
            if _is_verifiable(item, allowed):
                valid_evidence.append(
                    InvestigationEvidenceItem(
                        path=item.path, start_line=item.start_line,
                        end_line=item.end_line, summary=item.summary,
                    )
                )
            else:
                pruned_evidence.append({
                    "path": item.path, "start_line": item.start_line,
                    "end_line": item.end_line,
                })

        valid_runtime: List[InvestigationRuntimeEvidenceItem] = []
        for item in raw.runtime_evidence:
            candidate = runtime_by_id.get(item.component_id)
            if candidate is None or item.runtime_check not in RUNTIME_CHECK_STATES:
                continue
            final_check = (
                candidate.baseline
                if candidate.baseline in ("unobserved", "stale")
                else item.runtime_check
            )
            valid_runtime.append(
                InvestigationRuntimeEvidenceItem(
                    component_id=item.component_id, provenance=candidate.provenance,
                    runtime_check=final_check, summary=item.summary,
                )
            )

        if not valid_evidence and not valid_runtime:
            # An ungrounded statement is only acceptable when it claims
            # nothing about the code ("I could not determine X").
            if raw.claim_kind != "unknown" or had_citations:
                dropped += 1
                continue

        findings.append(
            LoopFinding(
                claim_kind=raw.claim_kind,
                statement=raw.statement,
                evidence=valid_evidence,
                runtime_evidence=valid_runtime,
                competing_explanations=list(raw.competing_explanations),
                refutation_conditions=list(raw.refutation_conditions),
                next_investigation=raw.next_investigation,
                uncertainty=raw.uncertainty,
                round_index=round_index,
            )
        )

    return findings[:MAX_FINDINGS_PER_ROUND], pruned_evidence, dropped


# --- The loop -----------------------------------------------------------------


def run_investigation_loop(
    client: LLMClient,
    config: LLMConfig,
    *,
    repo_path: str,
    commit_sha: str,
    question: str,
    research_focus: Optional[str] = None,
    search_keywords: Optional[Sequence[str]] = None,
    language: str = "ja",
    budget: Optional[InvestigationLoopBudget] = None,
    carry_over: Optional[LoopCarryOver] = None,
    system_id: Optional[int] = None,
    snapshot_id: Optional[int] = None,
    round_index_offset: int = 0,
) -> InvestigationLoopResult:
    """Investigate ``question`` over multiple rounds against the pinned snapshot.

    ``carry_over`` is the state a previous run left behind (persisted by the
    caller): passing it in resumes from those leads instead of re-deriving
    them from the question -- the retry continuity Epic #328 asks for. The
    returned ``carry_over`` is what the caller should persist next.

    ``system_id``/``snapshot_id`` enable the index/content candidate sources
    and runtime facts. This function opens its own short-lived connections
    for those reads and always closes them BEFORE the reasoning call, so the
    process-wide DB lock is never held across an LLM round trip (app/db.py).
    Callers must have no connection open.
    """
    budget = budget or InvestigationLoopBudget()
    carry = LoopCarryOver(
        search_leads=list((carry_over.search_leads if carry_over else []) or []),
        open_hypotheses=list((carry_over.open_hypotheses if carry_over else []) or []),
        missing_evidence=list((carry_over.missing_evidence if carry_over else []) or []),
        read_paths=list((carry_over.read_paths if carry_over else []) or []),
    )
    start = time.monotonic()
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return InvestigationLoopResult(
            provider=config.provider, model=config.model, is_mock=is_mock,
            stop_reason="failed", carry_over=carry,
            failure_class="config_invalid",
            error=(
                "Joint Understanding investigation requires a configured reasoning "
                "model; mock/heuristic fallback is prohibited"
            ),
        )

    try:
        entries = list_tree_entries(
            repo_path,
            commit_sha,
            timeout=max(0.001, budget.timeout_seconds),
        )
    except GitError as exc:
        return InvestigationLoopResult(
            provider=config.provider, model=config.model, is_mock=False,
            stop_reason="failed", carry_over=carry,
            failure_class="snapshot_unavailable",
            error=f"Failed to list the pinned snapshot: {exc}",
        )
    paths = [e.path for e in entries if e.object_type == "blob" and e.mode != "120000"]

    result = InvestigationLoopResult(
        provider=config.provider, model=config.model, is_mock=False, carry_over=carry,
    )
    already_read: Set[str] = set(carry.read_paths)
    remaining_files = budget.max_files
    remaining_chars = budget.max_snippet_chars
    stop_reason: Optional[str] = None

    for local_round_index in range(1, budget.max_rounds + 1):
        round_index = round_index_offset + local_round_index
        round_start = time.monotonic()
        if len(result.rounds) >= budget.max_llm_calls:
            stop_reason = "budget_exhausted"
            break
        # _CHARS_BOUNDS clamps a per-round InvestigationBudget UP to 1_000
        # chars, so stopping below that keeps the total char budget honest
        # instead of quietly over-reading on the last round.
        if remaining_files <= 0 or remaining_chars < 1_000:
            stop_reason = "budget_exhausted"
            break
        if time.monotonic() - start > budget.timeout_seconds:
            stop_reason = "budget_exhausted"
            break

        keywords = list(dict.fromkeys(
            _hint_keywords(list(search_keywords or []) + carry.search_leads)
            + _keywords(research_focus, question)
        ))

        # DB reads happen here, strictly BEFORE this round's reasoning call.
        index_paths: List[str] = []
        source_audits: List[ExplorerResult] = []
        # Issue #339: the structural sources answer "what is AROUND these
        # files", so they need files that already matter. They are therefore
        # skipped while nothing has been read yet: the first round of a fresh
        # investigation answers the question as asked, from the direct matches,
        # and the added breadth follows the leads that round produced. Seeding
        # them from keywords instead would spend the first round's read budget
        # on files that are merely adjacent to a guess -- and on a small
        # per-round budget that costs the file the developer actually asked
        # about. A RESUMED run has carried-over read paths, so its first round
        # is lead-driven and does get them.
        structural_seeds = _seed_paths(carry=carry)
        if structural_seeds:
            if system_id is not None and snapshot_id is not None:
                from .db import get_conn

                with get_conn() as conn:
                    index_paths = _index_candidates(
                        conn, system_id, snapshot_id, keywords,
                        budget.max_files_per_round * 3,
                    )
                    source_audits.extend(
                        _structural_sources(
                            conn,
                            system_id=system_id,
                            snapshot_id=snapshot_id,
                            seed_paths=structural_seeds,
                            seed_symbols=carry.search_leads,
                            include_runtime=budget.max_runtime_facts > 0,
                        )
                    )
            # git history runs OUTSIDE the DB connection -- it is a subprocess,
            # and app/db.py's lock must never be held across one.
            remaining_for_history = budget.timeout_seconds - (time.monotonic() - start)
            if remaining_for_history > 0:
                source_audits.append(
                    git_history_candidates(
                        repo_path=repo_path, commit_sha=commit_sha,
                        seed_paths=structural_seeds,
                        timeout_seconds=remaining_for_history,
                    )
                )
        elif system_id is not None and snapshot_id is not None:
            from .db import get_conn

            with get_conn() as conn:
                index_paths = _index_candidates(
                    conn, system_id, snapshot_id, keywords,
                    budget.max_files_per_round * 3,
                )

        read_limit = min(budget.max_files_per_round, remaining_files)
        tracked = set(paths)
        structural = merge_candidates(
            source_audits,
            tracked_paths=tracked,
            already_read=already_read,
            limit=budget.max_files_per_round * 2,
        )
        candidates = _round_candidates(
            paths=paths, keywords=keywords,
            index_paths=index_paths,
            structural_paths=structural,
            already_read=already_read,
            limit=max(read_limit + 1, budget.max_files_per_round * 3),
        )
        if not candidates:
            stop_reason = "no_new_evidence" if result.rounds else "unresolved"
            break

        remaining_for_reads = budget.timeout_seconds - (time.monotonic() - start)
        if remaining_for_reads <= 0:
            stop_reason = "budget_exhausted"
            break
        round_budget = InvestigationBudget(
            max_files=read_limit,
            max_snippet_chars=remaining_chars,
            max_llm_calls=1,
            max_evidence_items=20,
            max_runtime_facts=budget.max_runtime_facts,
            timeout_seconds=remaining_for_reads,
        )
        snippets, _timed_out = _read_candidates(
            repo_path, commit_sha, candidates, round_budget, time.monotonic(),
        )
        if not snippets:
            stop_reason = "no_new_evidence" if result.rounds else "unresolved"
            break

        runtime_candidates: List[RuntimeFactCandidate] = []
        if system_id is not None and snapshot_id is not None and budget.max_runtime_facts > 0:
            from .db import get_conn

            with get_conn() as conn:
                runtime_candidates = _gather_runtime_candidates(
                    conn, system_id, snapshot_id,
                    [s.path for s in snippets], budget.max_runtime_facts,
                )

        prompt = _build_user_prompt(
            question=question, research_focus=research_focus, round_index=round_index,
            snippets=snippets, runtime_candidates=runtime_candidates,
            carry_over=carry, established=result.findings,
        )

        files_read = len(snippets)
        chars_read = sum(s.char_count for s in snippets)
        remaining_files -= files_read
        remaining_chars -= chars_read
        read_now = [s.path for s in snippets]
        already_read.update(read_now)
        carry.read_paths = _dedupe(carry.read_paths + read_now, 200)
        unread = [p for p in candidates if p not in set(read_now)]

        # Issue #339: the time budget is a HARD limit, applied to the call
        # itself. Checking the clock between rounds bounds the loop's own
        # bookkeeping, not the round trip that actually spends the time -- a
        # single hung call could overrun the whole budget while every
        # between-round check passed. Below the floor there is no time left to
        # make a call with, so the loop stops instead of starting one.
        remaining_seconds = budget.timeout_seconds - (time.monotonic() - start)
        if remaining_seconds < _MIN_CALL_SECONDS:
            stop_reason = "budget_exhausted"
            break
        try:
            raw = client.generate_text(
                [
                    {"role": "system", "content": _system_prompt(language)},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=3072,
                timeout=remaining_seconds,
            )
        except LLMError as exc:
            timed_out = time.monotonic() - start >= budget.timeout_seconds
            result.rounds.append(LoopRound(
                round_index=round_index, status="failed", error=str(exc),
                read_snippets=snippets, candidate_paths=candidates, unread_candidates=unread,
                files_read=files_read, chars_read=chars_read, llm_calls=1,
                elapsed_seconds=time.monotonic() - round_start,
                source_audits=source_audits,
                failure_class="timeout" if timed_out else "api_failure",
            ))
            stop_reason = "failed"
            result.failure_class = "timeout" if timed_out else "api_failure"
            result.error = str(exc)
            break

        try:
            validated = _RawRoundResponse.model_validate(json.loads(_strip_fences(raw)))
        except (json.JSONDecodeError, ValidationError) as exc:
            message = f"Failed to parse structured response: {exc}"
            result.rounds.append(LoopRound(
                round_index=round_index, status="failed", error=message,
                read_snippets=snippets, candidate_paths=candidates, unread_candidates=unread,
                files_read=files_read, chars_read=chars_read, llm_calls=1,
                elapsed_seconds=time.monotonic() - round_start,
                source_audits=source_audits, failure_class="schema_invalid",
            ))
            stop_reason = "failed"
            result.failure_class = "schema_invalid"
            result.error = message
            break

        if validated.status not in _RAW_STATUSES:
            message = f"Model returned an invalid status: {validated.status!r}"
            result.rounds.append(LoopRound(
                round_index=round_index, status="failed", error=message,
                read_snippets=snippets, candidate_paths=candidates, unread_candidates=unread,
                files_read=files_read, chars_read=chars_read, llm_calls=1,
                elapsed_seconds=time.monotonic() - round_start,
                source_audits=source_audits, failure_class="schema_invalid",
            ))
            stop_reason = "failed"
            result.failure_class = "schema_invalid"
            result.error = message
            break

        findings, pruned_evidence, dropped = _validate_round_findings(
            validated.findings,
            allowed=_allowed_ranges(snippets),
            runtime_by_id={c.component_id: c for c in runtime_candidates},
            round_index=round_index,
        )

        # A "completed" round with nothing verifiable is demoted, exactly as
        # investigation_agent demotes an ungrounded completion.
        round_status = validated.status
        if round_status == "completed" and not findings:
            round_status = "unresolved"

        round_leads = _dedupe(validated.search_leads, MAX_CARRIED_LEADS)
        round_hypotheses = _dedupe(validated.open_hypotheses, MAX_CARRIED_HYPOTHESES)
        round_missing = _dedupe(validated.missing_evidence, MAX_CARRIED_MISSING)

        result.rounds.append(LoopRound(
            round_index=round_index, status=round_status, conclusion=validated.conclusion,
            findings=findings, search_leads=round_leads, open_hypotheses=round_hypotheses,
            missing_evidence=round_missing, read_snippets=snippets,
            candidate_paths=candidates, unread_candidates=unread,
            pruned_evidence=pruned_evidence, pruned_findings=dropped,
            files_read=files_read, chars_read=chars_read, llm_calls=1,
            elapsed_seconds=time.monotonic() - round_start,
            source_audits=source_audits,
        ))
        result.findings.extend(findings)

        # "New evidence" means a newly validated snapshot/runtime citation,
        # not merely that another file happened to be opened.  An ungrounded
        # unknown is still preserved as the round result, but it must not keep
        # spending the investigation budget.
        if not any(f.evidence or f.runtime_evidence for f in findings):
            stop_reason = "no_new_evidence"
            break

        # Carry-over for the next round AND for a later retry: leads already
        # consumed as read paths are dropped so a retry does not re-propose
        # what it has read, but everything still open survives.
        carry.search_leads = _dedupe(round_leads + carry.search_leads, MAX_CARRIED_LEADS)
        carry.open_hypotheses = _dedupe(round_hypotheses, MAX_CARRIED_HYPOTHESES)
        carry.missing_evidence = _dedupe(round_missing, MAX_CARRIED_MISSING)

        if round_status == "completed" and not round_missing:
            stop_reason = "answered"
            break

    if stop_reason is None:
        stop_reason = "budget_exhausted" if len(result.rounds) >= budget.max_rounds else "unresolved"
    if stop_reason == "budget_exhausted" and not result.rounds:
        # Budget too small to run even one round -- nothing was investigated.
        stop_reason = "unresolved"

    result.stop_reason = stop_reason
    result.carry_over = carry
    return result
