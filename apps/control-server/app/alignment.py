"""Alignment Review / Review Queue (Issue #287).

Contrasts confirmed/proposed Intent Brief items (``interview_intent_item``,
Issue #284) against the evidence-backed Current System understanding (the
latest ``understanding_revision``, Issue #136) and produces "alignment
items": one row per contrast point, each carrying its own claim + evidence
and a *deterministic* review classification.

Split per Principle 6:

- Reasoning model (``generate_alignment_proposal``, ``prompt_version`` /
  ``schema_version`` = ``alignment-v1``): proposes *content* -- which Intent
  field a current-system claim relates to, the claim text, its evidence,
  the alignment state (aligned/gap/unknown/conflict/not_applicable), risk
  flags (from a finite vocabulary), confidence, and a gap/interpretation
  summary. Every finite-set field (``alignment_state`` / ``confidence`` /
  ``risk_flags`` / ``intent_field``) is schema-validated against its finite
  set here; any value outside the set fails the whole build closed, exactly
  like Issue #286's Question Router.
- ``validate_evidence_against_snapshot``: deterministic structural check
  (path exists in the pinned snapshot's tree, line range inside the file)
  -- never a reasoning decision. Mirrors ``investigation_agent``'s evidence
  check: unverifiable citations are pruned from an item (recorded); an item
  left with zero evidence is dropped entirely.
- ``classify_alignment_item``: a pure, data-driven rule table (first match
  wins) mapping the reasoning model's finite output fields to
  ``review_category`` / ``reason_code``. No numeric scoring, no LLM
  ordering -- this is direct structural classification into an explicit
  finite set (Principle 6), so it is implemented as plain Python data
  (``_RULES``) that tests can enumerate exhaustively.
- ``USER_REASON_TEMPLATES`` / ``user_reason_for``: a fixed Japanese template
  per ``reason_code`` -- never LLM free text (Principle 7's "concise
  reasons" requirement, made deterministic here since the reason a category
  was assigned is itself a structural fact, not an interpretation).
- ``review_sort_key``: deterministic queue ordering (category rank, then
  reason-code rank, then id) -- no numeric score multiplication.

Persistence (the rebuild-merge strategy, evidence-against-snapshot lookups,
and the ``intelligence_runs`` audit row) lives in
``routes/interview_alignment.py``, matching how ``routes/interview_intent.py``
and ``routes/interview_inquiry.py`` keep DB orchestration in the route layer
while the reasoning/validation logic stays in its own importable module.

probe-agent:
  role: Reasoning-model Alignment proposal + deterministic review
    classification rule table (Intent vs Current System)
  capability: interactive-system-understanding
  element_type: element
  consumers: [interview-alignment-routes]
  operation_kind: analysis
  state_effects: [external-api]
  probe_value: Verify classify_alignment_item's rule table stays exhaustive and deterministic, and that generate_alignment_proposal fails closed on mock/non-reasoning models or any out-of-finite-set field.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .git_ops import GitError, read_file_at_commit
from .interview_intent_agent import INTENT_FIELDS
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "alignment-v1"
SCHEMA_VERSION = "alignment-v1"

ALIGNMENT_STATES = ("aligned", "gap", "unknown", "conflict", "not_applicable")
RISK_FLAGS = ("security", "high_risk", "core_intent")
CONFIDENCE_LEVELS = ("confirmed", "likely", "uncertain", "conflicting")

# Ordered (rank == index): "first" is the highest-priority category/reason
# for queue ordering (review_sort_key below).
REVIEW_CATEGORIES = (
    "must_review", "batch_reviewable", "no_review_required", "unchanged", "informational",
)
REASON_CODES = (
    "security_related", "high_risk", "core_intent", "conflict_detected",
    "low_confidence", "runtime_mismatch", "routine_update", "no_change",
    "informational_only", "unchanged_since_confirmation",
)

_CATEGORY_RANK: Dict[str, int] = {name: i for i, name in enumerate(REVIEW_CATEGORIES)}
_REASON_RANK: Dict[str, int] = {name: i for i, name in enumerate(REASON_CODES)}

# Fixed Japanese template per reason_code (Principle 6/7: deterministic,
# never LLM free text). Every REASON_CODES value must have an entry here
# (checked by tests).
USER_REASON_TEMPLATES: Dict[str, str] = {
    "security_related": "セキュリティに関わるため個別確認が必要です",
    "high_risk": "影響が大きい変更のため個別確認が必要です",
    "core_intent": "目標(goal)に関わる内容のため個別確認が必要です",
    "conflict_detected": "意図と現状の理解が矛盾しているため確認が必要です",
    "low_confidence": "AIの確信度が低いため個別確認が必要です",
    "runtime_mismatch": "コード上の理解と実行時の観測が一致していません",
    "routine_update": "軽微な差分です。まとめて確認してください",
    "no_change": "意図と現状の理解は一致しています。対応は不要です",
    "informational_only": "参考情報です。対応は不要です",
    "unchanged_since_confirmation": "前回の確認から内容に変更はありません。対応は不要です",
}


def user_reason_for(reason_code: str) -> str:
    return USER_REASON_TEMPLATES[reason_code]


# --- Deterministic review classification rule table (Principle 6) -----------
#
# First match wins. Every branch's predicate reads only finite-set fields
# already validated by generate_alignment_proposal (alignment_state,
# risk_flags, confidence, intent_field) plus, from Issue #290,
# runtime_check -- itself a finite state already validated deterministically
# by app/runtime_alignment.py's compare_claim_to_runtime, never free text.
# Kept as plain data (not nested if/elif) so tests can enumerate every rule
# and assert on it directly, and so the priority order is visible at a
# glance.

_RulePredicate = Callable[[str, List[str], str, Optional[str], Optional[str]], bool]

_RULES: List[Tuple[_RulePredicate, str, str]] = [
    (lambda state, risk, conf, ifield, runtime_check: "security" in risk,
     "must_review", "security_related"),
    (lambda state, risk, conf, ifield, runtime_check: "high_risk" in risk,
     "must_review", "high_risk"),
    (lambda state, risk, conf, ifield, runtime_check: (
        "core_intent" in risk or (ifield == "goal" and state in ("gap", "conflict"))
     ), "must_review", "core_intent"),
    (lambda state, risk, conf, ifield, runtime_check: state == "conflict",
     "must_review", "conflict_detected"),
    # Issue #290: a deterministic runtime/current-system mismatch is its own
    # must_review reason, inserted after conflict_detected (an intent-vs-code
    # conflict is a stronger, more specific signal) and before low_confidence
    # (a runtime mismatch is a structural fact, not a confidence problem).
    # stale/unobserved deliberately do NOT match here -- they never force
    # must_review by themselves (brief).
    (lambda state, risk, conf, ifield, runtime_check: runtime_check == "mismatch",
     "must_review", "runtime_mismatch"),
    (lambda state, risk, conf, ifield, runtime_check: conf in ("uncertain", "conflicting"),
     "must_review", "low_confidence"),
    (lambda state, risk, conf, ifield, runtime_check: state == "unknown",
     "must_review", "low_confidence"),
    (lambda state, risk, conf, ifield, runtime_check: state == "gap",
     "batch_reviewable", "routine_update"),
    (lambda state, risk, conf, ifield, runtime_check: state == "aligned",
     "no_review_required", "no_change"),
    (lambda state, risk, conf, ifield, runtime_check: state == "not_applicable",
     "informational", "informational_only"),
]


def classify_alignment_item(
    *,
    alignment_state: str,
    risk_flags: List[str],
    confidence: str,
    intent_field: Optional[str],
    runtime_check: Optional[str] = None,
) -> Tuple[str, str]:
    """Deterministic (review_category, reason_code) for one alignment item.

    Requires ``alignment_state`` in ``ALIGNMENT_STATES`` and ``confidence``
    in ``CONFIDENCE_LEVELS`` (callers validate this before classifying, see
    ``generate_alignment_proposal``). ``runtime_check`` (Issue #290), when
    given, must be one of ``runtime_alignment.RUNTIME_CHECK_STATES``; it is
    ``None`` whenever the item has no deterministic component mapping (the
    default, and the case for every pre-#290 caller/test). Every valid
    ``alignment_state`` is covered by exactly one terminal rule below
    (conflict / unknown / gap / aligned / not_applicable) even when
    ``runtime_check`` is ``None``, so the rule table is exhaustive for any
    already-validated input; the ``ValueError`` below only guards against a
    future rule-table edit accidentally leaving a state uncovered.
    """
    for predicate, category, reason_code in _RULES:
        if predicate(alignment_state, risk_flags, confidence, intent_field, runtime_check):
            return category, reason_code
    raise ValueError(f"No review rule matched alignment_state={alignment_state!r}")


def review_sort_key(*, review_category: str, reason_code: str, item_id: int) -> tuple:
    """Deterministic queue ordering: category rank, then reason rank, then id.

    No numeric score multiplication, no LLM-provided ordering (Principle 6).
    """
    return (_CATEGORY_RANK[review_category], _REASON_RANK[reason_code], item_id)


# --- Deterministic content hash for unchanged-item carry-over (Issue #295) --
#
# ``unchanged`` (Issue #287's reserved-but-unreachable review_category) is
# realized here: a rebuild that produces an item whose content_hash exactly
# matches a terminal (answered/corrected, non-superseded) row from the
# immediately preceding build carries that row's identity forward instead of
# re-running it through ``classify_alignment_item``'s rule table. This is an
# EXACT structural match, never a similarity/heuristic comparison (Principle
# 6) -- a single differing character anywhere in the hashed payload produces
# a completely different hash and the item is reclassified normally.
#
# The hashed payload is current_claim + normalized evidence (the item's
# "content", per the Issue #295 brief) plus every field that feeds
# classify_alignment_item's rule table (alignment_state / risk_flags /
# confidence / intent_field / runtime_check), so a change in
# classification-relevant state can never be masked as "unchanged" even when
# the claim text itself happens to repeat verbatim.


def compute_content_hash(
    *,
    current_claim: str,
    current_evidence: List[Dict[str, object]],
    alignment_state: str,
    risk_flags: List[str],
    confidence: str,
    intent_field: Optional[str],
    runtime_check: Optional[str],
) -> str:
    """Deterministic sha256 over one alignment item's identity-bearing fields."""
    normalized_evidence = sorted(
        (
            {
                "path": e.get("path"),
                "start_line": e.get("start_line"),
                "end_line": e.get("end_line"),
                "summary": e.get("summary", ""),
            }
            for e in current_evidence
        ),
        key=lambda e: (e["path"], e["start_line"], e["end_line"], e["summary"]),
    )
    payload = {
        "current_claim": current_claim,
        "current_evidence": normalized_evidence,
        "alignment_state": alignment_state,
        "risk_flags": sorted(risk_flags),
        "confidence": confidence,
        "intent_field": intent_field,
        "runtime_check": runtime_check,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Raw LLM response schema (what we require the model to return) ----------


class _RawEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=500)
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    summary: str = Field(default="", max_length=1_000)


class _RawAlignmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_field: Optional[str] = Field(default=None, max_length=50)
    intent_ref_hint: Optional[str] = Field(default=None, max_length=500)
    current_claim: str = Field(..., min_length=1, max_length=2_000)
    evidence: List[_RawEvidenceItem] = Field(default_factory=list, max_length=10)
    alignment_state: str = Field(..., min_length=1, max_length=30)
    risk_flags: List[str] = Field(default_factory=list, max_length=5)
    confidence: str = Field(..., min_length=1, max_length=20)
    gap_summary: Optional[str] = Field(default=None, max_length=1_000)
    proposed_interpretation: Optional[str] = Field(default=None, max_length=1_000)


class _RawAlignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[_RawAlignmentItem] = Field(default_factory=list, max_length=40)


# --- Public result types ------------------------------------------------------


@dataclass
class AlignmentEvidenceItem:
    path: str
    start_line: int
    end_line: int
    summary: str = ""


@dataclass
class AlignmentProposalItem:
    intent_field: Optional[str]
    intent_ref_hint: Optional[str]
    current_claim: str
    evidence: List[AlignmentEvidenceItem]
    alignment_state: str
    risk_flags: List[str]
    confidence: str
    gap_summary: Optional[str] = None
    proposed_interpretation: Optional[str] = None


@dataclass
class AlignmentProposalResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    items: List[AlignmentProposalItem] = field(default_factory=list)
    error: Optional[str] = None


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


_SYSTEM_PROMPT = """\
You are the Alignment Reviewer of probe-agent's system-understanding \
Interview flow. You are given the developer's confirmed/proposed Intent \
Brief (goal / pain / success_criteria / priority / constraints / non_goals) \
and the evidence-backed Current System understanding (already derived from \
the pinned source snapshot). Contrast them and propose "alignment items": \
points where the current system's behavior aligns with, deviates from, or \
says nothing about the developer's intent.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "items": [
    {
      "intent_field": "goal | pain | success_criteria | priority | constraints | non_goals | null",
      "intent_ref_hint": "a short paraphrase of the related Intent Brief text, or null if none",
      "current_claim": "the Current System claim this item is about",
      "evidence": [
        {"path": "src/module.py", "start_line": 1, "end_line": 20, "summary": "what this shows"}
      ],
      "alignment_state": "aligned | gap | unknown | conflict | not_applicable",
      "risk_flags": ["security" | "high_risk" | "core_intent", ...],
      "confidence": "confirmed | likely | uncertain | conflicting",
      "gap_summary": "what is missing or different, or null",
      "proposed_interpretation": "a short suggested resolution, or null"
    }
  ]
}

Rules:
- Only cite "path"/"start_line"/"end_line" that appear verbatim in the \
evidence already attached to a Current System understanding item supplied \
below. Never invent a path or a line range.
- "risk_flags" must only contain values from the finite set shown above (it \
may be empty). Only include "security" when the claim concerns \
authentication/authorization/secrets/data protection, "high_risk" when the \
claim concerns payment/irreversible side effects/production data \
mutation, and "core_intent" when the claim is central to the developer's \
stated goal.
- "alignment_state": "aligned" when the current system matches the intent; \
"gap" when the intent is not (yet) reflected in the current system; \
"conflict" when the current system contradicts the intent; "unknown" when \
there is not enough evidence to tell; "not_applicable" for a Current System \
claim with no related intent (informational only).
- "intent_field" identifies which Intent Brief field this item relates to, \
or null when the claim is purely about the current system with no related \
intent (typically paired with alignment_state "not_applicable").
- Do not propose more than one item per distinct current-system claim.
- Never decide, adopt, or apply anything yourself here; you only propose \
content for a human to review.
"""


def _build_user_prompt(
    intent_items: List[Dict[str, object]],
    current_understanding: Optional[Dict[str, object]],
    gap_analysis: Optional[List[Dict[str, object]]],
) -> str:
    parts = [
        "## Intent Brief (current, non-superseded items; only the user can decide these)",
        json.dumps(intent_items, ensure_ascii=False),
        "\n## Current System understanding (evidence-backed, from the latest build)",
        json.dumps(current_understanding or {}, ensure_ascii=False),
        "\n## Known gaps (docs/code reconciliation)",
        json.dumps(gap_analysis or [], ensure_ascii=False),
    ]
    return "\n".join(parts)


def generate_alignment_proposal(
    client: LLMClient,
    config: LLMConfig,
    *,
    intent_items: List[Dict[str, object]],
    current_understanding: Optional[Dict[str, object]],
    gap_analysis: Optional[List[Dict[str, object]]],
) -> AlignmentProposalResult:
    """Propose alignment items contrasting Intent vs Current System.

    Fail-closed (Principle 6): a mock client, a non-reasoning model, an API
    failure, a structured-output validation failure, or any item field
    outside its finite set (alignment_state / confidence / risk_flags /
    intent_field) all return a result with ``error`` set and no items --
    callers must not persist a partial/best-effort batch.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return AlignmentProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Alignment build requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    prompt = _build_user_prompt(intent_items, current_understanding, gap_analysis)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
    except LLMError as exc:
        return AlignmentProposalResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawAlignmentResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return AlignmentProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    items: List[AlignmentProposalItem] = []
    for raw_item in validated.items:
        if raw_item.alignment_state not in ALIGNMENT_STATES:
            return AlignmentProposalResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model returned an invalid alignment_state: {raw_item.alignment_state!r}",
            )
        if raw_item.confidence not in CONFIDENCE_LEVELS:
            return AlignmentProposalResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model returned an invalid confidence: {raw_item.confidence!r}",
            )
        invalid_flags = [f for f in raw_item.risk_flags if f not in RISK_FLAGS]
        if invalid_flags:
            return AlignmentProposalResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model returned invalid risk_flags: {invalid_flags!r}",
            )
        if raw_item.intent_field is not None and raw_item.intent_field not in INTENT_FIELDS:
            return AlignmentProposalResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model returned an invalid intent_field: {raw_item.intent_field!r}",
            )
        items.append(
            AlignmentProposalItem(
                intent_field=raw_item.intent_field,
                intent_ref_hint=raw_item.intent_ref_hint,
                current_claim=raw_item.current_claim,
                evidence=[
                    AlignmentEvidenceItem(
                        path=e.path, start_line=e.start_line, end_line=e.end_line,
                        summary=e.summary,
                    )
                    for e in raw_item.evidence
                ],
                alignment_state=raw_item.alignment_state,
                risk_flags=list(raw_item.risk_flags),
                confidence=raw_item.confidence,
                gap_summary=raw_item.gap_summary,
                proposed_interpretation=raw_item.proposed_interpretation,
            )
        )

    return AlignmentProposalResult(
        provider=config.provider, model=config.model, is_mock=False, items=items,
    )


# --- Deterministic evidence validation against the pinned snapshot ----------
#
# Mirrors investigation_agent's evidence check (Principle 5/6): a citation
# is verifiable only if its path exists in the pinned commit and its line
# range fits inside that file's actual line count. Never a reasoning
# decision. ``line_count_cache`` lets a caller amortize repeated paths
# across many items in one build.


def _line_count(
    repo_path: str, commit_sha: str, path: str, cache: Dict[str, Optional[int]],
) -> Optional[int]:
    if path in cache:
        return cache[path]
    try:
        raw = read_file_at_commit(repo_path, commit_sha, path)
    except GitError:
        cache[path] = None
        return None
    text = raw.decode("utf-8", errors="replace")
    if "\x00" in text:
        cache[path] = None
        return None
    count = len(text.splitlines())
    cache[path] = count
    return count


def validate_evidence_against_snapshot(
    repo_path: str,
    commit_sha: str,
    evidence: List[AlignmentEvidenceItem],
    line_count_cache: Optional[Dict[str, Optional[int]]] = None,
) -> Tuple[List[AlignmentEvidenceItem], List[Dict[str, object]]]:
    """Split ``evidence`` into (valid, pruned) against the pinned snapshot.

    A citation is valid when its path exists in the pinned commit and
    ``1 <= start_line <= end_line <= <file's line count>``. Invalid
    citations are pruned (returned, not raised) so the caller can decide
    whether the owning item still has enough evidence to keep.
    """
    cache: Dict[str, Optional[int]] = line_count_cache if line_count_cache is not None else {}
    valid: List[AlignmentEvidenceItem] = []
    pruned: List[Dict[str, object]] = []
    for item in evidence:
        total = _line_count(repo_path, commit_sha, item.path, cache)
        ok = (
            total is not None
            and item.start_line >= 1
            and item.end_line >= item.start_line
            and item.end_line <= total
        )
        if ok:
            valid.append(item)
        else:
            pruned.append({"path": item.path, "start_line": item.start_line, "end_line": item.end_line})
    return valid, pruned
