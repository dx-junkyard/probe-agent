"""Read-only Investigation Agent (Issue #286).

Researches a developer's question against the PINNED repository snapshot
only, within an explicit, deterministically-enforced budget. Used for
questions the Question Router classified ``system_researchable`` or
``hybrid``.

Read-only boundary (Principle 5, Principle 8):

- Every file read goes through ``git_ops.read_file_at_commit`` /
  ``git_ops.list_tree_entries`` against the pinned ``commit_sha`` -- never
  the working tree, never untracked/uncommitted content.
- No file writes, no patch, no subprocess beyond that read-only git
  plumbing, no network beyond the LLM call itself.

Split per Principle 6: candidate file *retrieval* is deterministic keyword
matching over tracked paths (a heuristic that only narrows the candidate
set); the actual *selection* of what is relevant and the conclusion are
always a reasoning-model decision, never a heuristic final answer.

Budgets are explicit and enforced deterministically (never inferred): a
budget too small to gather any evidence yields ``status="unresolved"`` with
an uncertainty note -- the agent never fabricates a conclusion from nothing,
and never spends an LLM call when there is nothing to show it.

Fail-closed: a mock client, a non-reasoning model, an LLM API failure, or a
structured-output validation failure yields ``status="failed"``. Evidence
citations that do not resolve inside the excerpts actually read are pruned
(with a recorded note) as long as at least one citation remains valid; if
every citation is invalid, the run fails closed rather than silently
returning an unfounded conclusion.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .git_ops import GitError, list_tree_entries, read_file_at_commit
from .interview_language import language_directive
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "investigation-v1"
SCHEMA_VERSION = "investigation-v1"

DEFAULT_MAX_FILES = 20
DEFAULT_MAX_SNIPPET_CHARS = 40_000
DEFAULT_MAX_LLM_CALLS = 3
DEFAULT_MAX_EVIDENCE_ITEMS = 10
DEFAULT_TIMEOUT_SECONDS = 60

# Hard clamps: whatever a caller passes, the effective budget never exceeds
# (or falls below) these bounds (Principle 6 -- deterministic caps, not a
# heuristic guess at "reasonable").
_FILES_BOUNDS = (1, 50)
_CHARS_BOUNDS = (1_000, 200_000)
_LLM_CALLS_BOUNDS = (1, 5)
_EVIDENCE_BOUNDS = (1, 20)
_TIMEOUT_BOUNDS = (1, 300)

InvestigationStatus = ("completed", "unresolved", "failed")

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "this", "that", "of", "to",
    "for", "and", "or", "in", "on", "with", "what", "why", "how", "does",
    "did", "not", "but", "you", "your", "it", "its", "be", "as", "by", "from",
})


def _clamp(value: int, bounds: "tuple[int, int]") -> int:
    lo, hi = bounds
    return max(lo, min(hi, value))


@dataclass
class InvestigationBudget:
    """Deterministic caps on one investigation run.

    Values are clamped to a fixed range on construction -- a caller can
    override any field, but never past the hard bounds above.
    """

    max_files: int = DEFAULT_MAX_FILES
    max_snippet_chars: int = DEFAULT_MAX_SNIPPET_CHARS
    max_llm_calls: int = DEFAULT_MAX_LLM_CALLS
    max_evidence_items: int = DEFAULT_MAX_EVIDENCE_ITEMS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        self.max_files = _clamp(int(self.max_files), _FILES_BOUNDS)
        self.max_snippet_chars = _clamp(int(self.max_snippet_chars), _CHARS_BOUNDS)
        self.max_llm_calls = _clamp(int(self.max_llm_calls), _LLM_CALLS_BOUNDS)
        self.max_evidence_items = _clamp(int(self.max_evidence_items), _EVIDENCE_BOUNDS)
        self.timeout_seconds = _clamp(int(self.timeout_seconds), _TIMEOUT_BOUNDS)


@dataclass
class InvestigationEvidenceItem:
    path: str
    start_line: int
    end_line: int
    summary: str = ""


@dataclass
class ReadSnippet:
    """A file excerpt actually read from the pinned snapshot (audit fact).

    ``content`` is never persisted to the database (mirrors
    ``interview_evidence.EvidenceSnippet``) -- it exists only to build the
    LLM prompt for this call.
    """

    path: str
    start_line: int
    end_line: int
    content: str
    char_count: int
    truncated: bool = False


@dataclass
class InvestigationResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    status: str = "unresolved"  # completed | unresolved | failed
    conclusion: str = ""
    key_points: List[str] = field(default_factory=list)
    evidence: List[InvestigationEvidenceItem] = field(default_factory=list)
    uncertainty: str = ""
    confidence: str = "uncertain"  # confirmed | likely | uncertain
    decision_question: Optional[str] = None
    read_snippets: List[ReadSnippet] = field(default_factory=list)
    pruned_evidence: List[Dict[str, object]] = field(default_factory=list)
    files_read: int = 0
    chars_read: int = 0
    llm_calls: int = 0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None


# --- Raw response schema (what we require the model to return) --------------


class _RawInvestigationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=500)
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    summary: str = Field(default="", max_length=1_000)


class _RawInvestigationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., min_length=1, max_length=20)
    conclusion: str = Field(..., min_length=1, max_length=2_000)
    key_points: List[str] = Field(default_factory=list, max_length=10)
    evidence: List[_RawInvestigationEvidence] = Field(default_factory=list, max_length=20)
    uncertainty: str = Field(default="", max_length=1_000)
    confidence: str = Field(default="uncertain", max_length=20)
    decision_question: Optional[str] = Field(default=None, max_length=1_000)


_RAW_STATUSES = ("completed", "unresolved")
_RAW_CONFIDENCES = ("confirmed", "likely", "uncertain")


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


# --- Deterministic candidate retrieval (Principle 6: candidates only) -------


def _keywords(*texts: Optional[str]) -> List[str]:
    combined = " ".join(t for t in texts if t)
    tokens = re.findall(r"[A-Za-z0-9_]+", combined.lower())
    return list(dict.fromkeys(t for t in tokens if len(t) >= 3 and t not in _STOPWORDS))


def _score_path(path: str, keywords: List[str]) -> int:
    lower = path.lower()
    return sum(1 for kw in keywords if kw in lower)


def _select_candidates(paths: List[str], keywords: List[str], limit: int) -> List[str]:
    if not keywords:
        return []
    scored = [(_score_path(p, keywords), p) for p in paths]
    scored = [(score, p) for score, p in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [p for _, p in scored[:limit]]


def _read_candidates(
    repo_path: str,
    commit_sha: str,
    candidates: List[str],
    budget: InvestigationBudget,
    start_time: float,
) -> "tuple[List[ReadSnippet], bool]":
    """Read candidates from the pinned commit within the char/file/time budget.

    Returns the snippets actually read and whether the timeout was hit.
    Unreadable (deleted/binary/null-byte) candidates are skipped, not fatal
    -- they simply do not count toward ``files_read``.
    """
    snippets: List[ReadSnippet] = []
    remaining_chars = budget.max_snippet_chars
    timed_out = False

    for path in candidates:
        if len(snippets) >= budget.max_files:
            break
        if remaining_chars <= 0:
            break
        if time.monotonic() - start_time > budget.timeout_seconds:
            timed_out = True
            break
        try:
            raw = read_file_at_commit(repo_path, commit_sha, path)
        except GitError:
            continue
        text = raw.decode("utf-8", errors="replace")
        if "\x00" in text:
            continue

        lines = text.splitlines()
        included: List[str] = []
        used = 0
        truncated = False
        for line in lines:
            add_len = len(line) + 1
            if used + add_len > remaining_chars:
                truncated = True
                break
            included.append(line)
            used += add_len
        if not included:
            # Not even one line fit in the remaining budget; try the next
            # (possibly smaller) candidate instead of aborting entirely.
            continue

        content = "\n".join(included)
        char_count = len(content)
        remaining_chars -= char_count
        snippets.append(ReadSnippet(
            path=path, start_line=1, end_line=len(included),
            content=content, char_count=char_count, truncated=truncated,
        ))

    return snippets, timed_out


def _allowed_ranges(snippets: List[ReadSnippet]) -> Dict[str, int]:
    return {s.path: s.end_line for s in snippets}


def _is_verifiable(item: _RawInvestigationEvidence, allowed: Dict[str, int]) -> bool:
    end = allowed.get(item.path)
    if end is None:
        return False
    if item.start_line < 1 or item.end_line < item.start_line:
        return False
    return item.end_line <= end


_SYSTEM_PROMPT = """\
You are the read-only Investigation Agent of probe-agent's system-understanding \
Inquiry flow. You are given a developer's question, an optional research \
focus from the Question Router, and a bounded set of source file excerpts \
read from a PINNED git snapshot (never the mutable working tree). You have \
no write access, cannot run code, and cannot look beyond the supplied \
excerpts.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "status": "completed | unresolved",
  "conclusion": "a short 1-3 sentence answer, or a short note about what could not be determined",
  "key_points": ["..."],
  "evidence": [
    {"path": "src/module.py", "start_line": 1, "end_line": 20, "summary": "what this span shows"}
  ],
  "uncertainty": "what remains uncertain or unverified, or empty string if none",
  "confidence": "confirmed | likely | uncertain",
  "decision_question": "a short question only the developer can decide, or null"
}

Rules:
- Only cite "path"/"start_line"/"end_line" that appear in the supplied \
excerpts, within the line range actually shown for that path. Never invent \
a path, a line range, or cite a file that was not supplied.
- Set "status" to "unresolved" when the supplied excerpts do not contain \
enough grounding to answer -- do not guess or fabricate an answer merely to \
appear helpful. Keep "evidence" empty in that case unless a partial finding \
is genuinely grounded.
- "confidence" reflects how directly the cited evidence supports the \
conclusion: "confirmed" only when the code plainly shows the answer, \
"likely" for a well-grounded inference, "uncertain" otherwise.
{decision_question_rule}
"""

_HYBRID_RULE = (
    "- This question was classified as HYBRID: part of it is answerable "
    "from the code, part requires the developer's own decision. Always "
    "populate \"decision_question\" with a short question covering the "
    "part only the developer can decide."
)
_NON_HYBRID_RULE = (
    "- Set \"decision_question\" to null; this investigation is purely factual."
)


def _system_prompt(language: str, hybrid_decision_question: bool) -> str:
    rule = _HYBRID_RULE if hybrid_decision_question else _NON_HYBRID_RULE
    prompt = _SYSTEM_PROMPT.replace("{decision_question_rule}", rule)
    return prompt + language_directive(language) + "\n"


def _build_user_prompt(
    question: str, research_focus: Optional[str], snippets: List[ReadSnippet],
) -> str:
    parts = [f"Developer question: {question}"]
    if research_focus:
        parts.append(f"Question Router research focus: {research_focus}")
    parts.append("## Candidate source excerpts (pinned snapshot; the ONLY files you may cite)")
    for s in snippets:
        note = " (truncated)" if s.truncated else ""
        parts.append(f"### {s.path} (lines {s.start_line}-{s.end_line}{note})\n{s.content}")
    return "\n\n".join(parts)


def investigate(
    client: LLMClient,
    config: LLMConfig,
    *,
    repo_path: str,
    commit_sha: str,
    question: str,
    research_focus: Optional[str] = None,
    hybrid_decision_question: bool = False,
    language: str = "ja",
    budget: Optional[InvestigationBudget] = None,
) -> InvestigationResult:
    """Investigate ``question`` against the pinned snapshot only, within budget.

    Fail-closed: a mock/non-reasoning client, an LLM API failure, or invalid
    structured output yield ``status="failed"``. A budget too small to
    gather any candidate content yields ``status="unresolved"`` without
    spending an LLM call. Evidence citations outside the excerpts actually
    read are pruned (recorded, not fatal) as long as at least one citation
    remains valid when citations were given at all; if every citation is
    invalid, the run fails closed.
    """
    start_time = time.monotonic()
    budget = budget or InvestigationBudget()

    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return InvestigationResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            status="failed",
            error=(
                "Investigation requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
            elapsed_seconds=time.monotonic() - start_time,
        )

    try:
        entries = list_tree_entries(repo_path, commit_sha)
    except GitError as exc:
        return InvestigationResult(
            provider=config.provider, model=config.model, is_mock=False, status="failed",
            error=f"Failed to list the pinned snapshot: {exc}",
            elapsed_seconds=time.monotonic() - start_time,
        )
    paths = [e.path for e in entries if e.object_type == "blob" and e.mode != "120000"]

    keywords = _keywords(research_focus, question)
    candidates = _select_candidates(paths, keywords, budget.max_files)
    snippets, timed_out = _read_candidates(repo_path, commit_sha, candidates, budget, start_time)

    files_read = len(snippets)
    chars_read = sum(s.char_count for s in snippets)

    if files_read == 0:
        note = (
            "Investigation timed out before any candidate file could be read."
            if timed_out
            else "No candidate file in the pinned snapshot matched the question; "
                 "nothing could be investigated within budget."
        )
        return InvestigationResult(
            provider=config.provider, model=config.model, is_mock=False, status="unresolved",
            uncertainty=note, files_read=0, chars_read=0, llm_calls=0,
            elapsed_seconds=time.monotonic() - start_time,
        )

    prompt = _build_user_prompt(question, research_focus, snippets)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _system_prompt(language, hybrid_decision_question)},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2048,
        )
    except LLMError as exc:
        return InvestigationResult(
            provider=config.provider, model=config.model, is_mock=False, status="failed",
            error=str(exc), read_snippets=snippets, files_read=files_read, chars_read=chars_read,
            llm_calls=1, elapsed_seconds=time.monotonic() - start_time,
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawInvestigationResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return InvestigationResult(
            provider=config.provider, model=config.model, is_mock=False, status="failed",
            error=f"Failed to parse structured response: {exc}", read_snippets=snippets,
            files_read=files_read, chars_read=chars_read, llm_calls=1,
            elapsed_seconds=time.monotonic() - start_time,
        )

    if validated.status not in _RAW_STATUSES:
        return InvestigationResult(
            provider=config.provider, model=config.model, is_mock=False, status="failed",
            error=f"Model returned an invalid status: {validated.status!r}", read_snippets=snippets,
            files_read=files_read, chars_read=chars_read, llm_calls=1,
            elapsed_seconds=time.monotonic() - start_time,
        )
    confidence = validated.confidence if validated.confidence in _RAW_CONFIDENCES else "uncertain"

    allowed = _allowed_ranges(snippets)
    valid_evidence: List[InvestigationEvidenceItem] = []
    pruned: List[Dict[str, object]] = []
    for item in validated.evidence:
        if _is_verifiable(item, allowed):
            valid_evidence.append(InvestigationEvidenceItem(
                path=item.path, start_line=item.start_line, end_line=item.end_line,
                summary=item.summary,
            ))
        else:
            pruned.append({"path": item.path, "start_line": item.start_line, "end_line": item.end_line})

    if pruned and not valid_evidence:
        return InvestigationResult(
            provider=config.provider, model=config.model, is_mock=False, status="failed",
            error="Every evidence citation failed snapshot validation", read_snippets=snippets,
            pruned_evidence=pruned, files_read=files_read, chars_read=chars_read, llm_calls=1,
            elapsed_seconds=time.monotonic() - start_time,
        )

    valid_evidence = valid_evidence[: budget.max_evidence_items]
    uncertainty = validated.uncertainty
    if pruned:
        uncertainty = (
            f"{uncertainty} " if uncertainty else ""
        ) + f"({len(pruned)} evidence citation(s) could not be verified against the snapshot and were dropped.)"

    decision_question = validated.decision_question if hybrid_decision_question else None

    return InvestigationResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        status=validated.status,
        conclusion=validated.conclusion,
        key_points=list(validated.key_points),
        evidence=valid_evidence,
        uncertainty=uncertainty,
        confidence=confidence,
        decision_question=decision_question,
        read_snippets=snippets,
        pruned_evidence=pruned,
        files_read=files_read,
        chars_read=chars_read,
        llm_calls=1,
        elapsed_seconds=time.monotonic() - start_time,
    )
