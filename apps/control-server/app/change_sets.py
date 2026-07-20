"""Natural-language bulk correction -> structured change set (Issue #289).

A developer writes one free-text correction that may touch several
Understanding items at once (e.g. "実は目標はAではなくBです。あと、認証まわ
りの説明も間違っています"). NL text is never applied to state directly
(Principle 2/6): a reasoning LLM turns it into itemized, structured edit
proposals grounded in a finite candidate list (``generate_change_set_proposal``,
fail-closed like ``interview_intent_agent``/``alignment``), a deterministic
post-pass resolves each proposal's ``target_hint`` against that same
candidate list (``resolve_change_set_items`` -- exact-string match only,
never fuzzy/semantic), and only items a human explicitly selects are ever
applied (``routes/interview_change_sets.py``).

Split per Principle 6, mirroring ``app/alignment.py``:

- Reasoning model: proposes *content* only -- which candidate an edit
  targets (by its exact, server-supplied ``hint`` string), the corrected
  text, and a short reason. It never decides applicability.
- ``resolve_change_set_items``: pure, deterministic classification into the
  finite ``resolution_state`` set (resolved/ambiguous/forbidden -- 'stale'
  and 'conflict' are re-validated later, at preview/apply time, by
  ``effective_resolution_state`` below, since they depend on state that can
  keep changing after this change set was created).
- Scope is enforced by construction, not by a denylist: the only two
  addressable (target_kind, field) pairs are (intent_item, value_text) and
  (understanding_claim, summary). User decisions, evidence refs,
  confirmed-at audit fields, and alignment classification have no
  corresponding target_kind at all, so an LLM attempt to describe one simply
  fails to match the whitelist and is rejected with resolution_state
  'forbidden' -- never silently coerced into a nearby field.

probe-agent:
  role: Reasoning-model NL-correction proposal + deterministic target
    resolution and re-validation for the structured change-set flow
  capability: interactive-system-understanding
  element_type: element
  consumers: [interview-change-set-routes]
  operation_kind: analysis
  state_effects: [external-api]
  probe_value: Verify resolve_change_set_items only ever resolves an item against a verbatim candidate hint match (never fuzzy), that an out-of-whitelist (target_kind, field) pair always yields 'forbidden', and that generate_change_set_proposal fails closed on mock/non-reasoning models or invalid structured output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "nl-change-set-v1"
SCHEMA_VERSION = "nl-change-set-v1"

# The only sections of current_understanding JSON a change item may address
# (system_understanding_reviewer.py's five top-level lists). Nested
# `children` entries are plain name strings, not addressable items, so they
# are intentionally out of scope here.
UNDERSTANDING_SECTIONS = (
    "system_purpose", "core_capabilities", "capability_elements",
    "supporting_elements", "api_boundaries",
)

# Enforce-by-construction whitelist (Principle 6): the only (target_kind,
# field) pair that is ever addressable. Anything else -- including every
# alignment_item field (user_decision, status, etc., which have no
# target_kind here at all) -- can never resolve past 'forbidden'.
ALLOWED_TARGET_FIELDS: Dict[str, str] = {
    "intent_item": "value_text",
    "understanding_claim": "summary",
}

RESOLUTION_STATES = ("resolved", "ambiguous", "conflict", "stale", "forbidden")


# --- Raw LLM response schema (what we require the model to return) ----------


class _RawChangeSetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # target_kind is schema-validated to the finite set here (like
    # Alignment validates alignment_state/confidence/risk_flags/
    # intent_field): an out-of-set value fails the WHOLE proposal closed,
    # exactly like an invalid finite-set field anywhere else in this
    # codebase. "field", by contrast, stays a plain string and is checked
    # per item against the (target_kind, field) whitelist in
    # ``resolve_change_set_items`` -- an otherwise-valid target_kind with a
    # disallowed field (e.g. intent_item + "status") is the realistic
    # "the model tried to touch something out of scope" case this flow
    # must reject per item as 'forbidden', not fail the whole batch over.
    target_kind: Literal["intent_item", "understanding_claim"]
    target_hint: str = Field(..., min_length=1, max_length=300)
    field: str = Field(..., max_length=50)
    after_value: str = Field(..., min_length=1, max_length=4_000)
    reason: str = Field(..., min_length=1, max_length=1_000)


class _RawChangeSetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[_RawChangeSetItem] = Field(default_factory=list, max_length=20)


# --- Public result types ------------------------------------------------------


@dataclass
class ChangeSetProposalItem:
    target_kind: str
    target_hint: str
    field: str
    after_value: str
    reason: str


@dataclass
class ChangeSetProposalResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    items: List[ChangeSetProposalItem] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ResolvedChangeItem:
    target_kind: str
    target_ref: Dict[str, Any]
    field: str
    before_value: Optional[str]
    after_value: str
    reason: str
    resolution_state: str


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
You are converting a developer's free-text bulk correction about a \
system-understanding interview into a structured, itemized change set. You \
NEVER apply anything yourself -- you only propose individual field-level \
edits for a human to review and selectively approve.

You may target only two kinds of item, each with exactly one editable field:
- "intent_item": the developer's own stated intent (goal / pain / \
success_criteria / priority / constraints / non_goals). The editable field \
is always "value_text".
- "understanding_claim": one item inside the evidence-backed Current System \
understanding. The editable field is always "summary".

You are given a finite "candidates" list below -- each candidate has \
"kind", "hint", and "current_value". You MUST set "target_hint" to a \
candidate's "hint" string VERBATIM (character for character, copied from \
the list) whenever you propose an edit to that candidate. Never invent a \
hint that is not in the candidates list, and never guess when the \
developer's text does not clearly correspond to exactly one candidate --\
omit that edit instead.

You must never propose edits to anything else (user decisions, evidence \
references, confirmed-at audit fields, alignment classification, or any \
other kind of item) -- these have no "target_kind" here at all, so simply \
do not attempt to describe them.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "items": [
    {
      "target_kind": "intent_item | understanding_claim",
      "target_hint": "the exact candidate hint string this edit targets",
      "field": "value_text | summary",
      "after_value": "the corrected text",
      "reason": "a short justification grounded in the developer's own text"
    }
  ]
}

Rules:
- Only propose edits clearly grounded in the developer's text below; never \
invent a value.
- If nothing in the text warrants a structured edit, return {"items": []}.
"""


def _build_user_prompt(source_text: str, candidates: List[Dict[str, Any]]) -> str:
    parts = [
        "developer's correction text:",
        source_text,
        "\ncandidates (JSON array; copy \"hint\" verbatim as target_hint):",
        json.dumps(candidates, ensure_ascii=False),
    ]
    return "\n".join(parts)


def generate_change_set_proposal(
    client: LLMClient,
    config: LLMConfig,
    *,
    source_text: str,
    candidates: List[Dict[str, Any]],
) -> ChangeSetProposalResult:
    """Propose structured edits for a free-text bulk correction.

    Fail-closed (Principle 6): a mock client, a non-reasoning model, an API
    failure, or a structured-output validation failure all return a result
    with ``error`` set and no items -- callers must not persist a
    partial/best-effort batch (the change set itself still gets recorded,
    with ``status='failed'``, so the attempt stays in the audit trail; see
    ``routes/interview_change_sets.py``).
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return ChangeSetProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Change-set proposal requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    if not candidates:
        return ChangeSetProposalResult(
            provider=config.provider, model=config.model, is_mock=False, items=[],
        )

    prompt = _build_user_prompt(source_text, candidates)

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
        return ChangeSetProposalResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawChangeSetResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return ChangeSetProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    items = [
        ChangeSetProposalItem(
            target_kind=raw_item.target_kind,
            target_hint=raw_item.target_hint,
            field=raw_item.field,
            after_value=raw_item.after_value,
            reason=raw_item.reason,
        )
        for raw_item in validated.items
    ]

    return ChangeSetProposalResult(
        provider=config.provider, model=config.model, is_mock=False, items=items,
    )


# --- Deterministic target resolution (Principle 6) --------------------------


def resolve_change_set_items(
    proposal_items: List[ChangeSetProposalItem],
    candidates: List[Dict[str, Any]],
) -> List[ResolvedChangeItem]:
    """Resolve each proposed edit's ``target_hint`` against ``candidates``.

    Structural, exact-match only -- never fuzzy/semantic (Principle 6):

    - the (target_kind, field) pair must be exactly the one whitelisted
      entry for that target_kind (``ALLOWED_TARGET_FIELDS``), otherwise the
      item is rejected as 'forbidden' regardless of hint;
    - otherwise the hint must match exactly one candidate of that kind
      (``(kind, hint)`` as the join key) -- zero or more-than-one matches
      both yield 'ambiguous' (an unresolvable/typo'd hint looks the same as
      a genuinely ambiguous one from here, and both require a human to
      resubmit rather than a guess).

    Each candidate dict must carry ``kind``/``hint``/``current_value`` plus
    the concrete addressing fields: ``id`` for ``intent_item`` candidates,
    ``section``/``name`` for ``understanding_claim`` candidates (see
    ``routes/interview_change_sets.py``'s candidate builder).
    """
    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for c in candidates:
        by_key.setdefault((c["kind"], c["hint"]), []).append(c)

    resolved: List[ResolvedChangeItem] = []
    for item in proposal_items:
        allowed_field = ALLOWED_TARGET_FIELDS.get(item.target_kind)
        if allowed_field is None or item.field != allowed_field:
            resolved.append(ResolvedChangeItem(
                target_kind=item.target_kind,
                target_ref={"hint": item.target_hint},
                field=item.field,
                before_value=None,
                after_value=item.after_value,
                reason=item.reason,
                resolution_state="forbidden",
            ))
            continue

        matches = by_key.get((item.target_kind, item.target_hint), [])
        if len(matches) != 1:
            resolved.append(ResolvedChangeItem(
                target_kind=item.target_kind,
                target_ref={"hint": item.target_hint},
                field=allowed_field,
                before_value=None,
                after_value=item.after_value,
                reason=item.reason,
                resolution_state="ambiguous",
            ))
            continue

        match = matches[0]
        if item.target_kind == "intent_item":
            target_ref: Dict[str, Any] = {"intent_item_id": match["id"]}
        else:
            target_ref = {"section": match["section"], "name": match["name"]}
        resolved.append(ResolvedChangeItem(
            target_kind=item.target_kind,
            target_ref=target_ref,
            field=allowed_field,
            before_value=match["current_value"],
            after_value=item.after_value,
            reason=item.reason,
            resolution_state="resolved",
        ))
    return resolved


def effective_resolution_state(
    *,
    stored_state: str,
    target_kind: str,
    before_value: Optional[str],
    base_revision_id: Optional[int],
    latest_revision_id: Optional[int],
    current_value: Optional[str],
) -> str:
    """Re-validate a 'resolved' item's target against CURRENT state.

    Called both by the read-only preview (``GET .../change-sets/{id}``,
    display only, never persisted) and by apply (which persists the
    transition for an item it actually skips because of it) -- always the
    same rule, so preview and apply can never disagree about whether an
    item is still applicable.

    'ambiguous'/'forbidden' are structural facts fixed at creation time and
    can never change. A 'resolved' item is re-checked for two possible
    regressions every time this is called:

    - 'stale' (``understanding_claim`` only): the session's Understanding
      has been rebuilt since this change set's ``base_revision_id`` was
      captured, so the recorded ``before_value`` may no longer be the real
      "current" text even if it happens to still match by coincidence.
      Intent Brief items are not revisioned this way, so this check does
      not apply to ``intent_item`` targets.
    - 'conflict': the target's current value no longer matches the
      recorded ``before_value`` (someone -- or an earlier item in the same
      apply batch -- already changed it), or (``intent_item``) the target
      row no longer exists / was superseded (``current_value`` is None in
      both cases -- the caller resolves "does this target still exist"
      before calling this function).
    """
    if stored_state in ("ambiguous", "forbidden"):
        return stored_state
    if stored_state != "resolved":
        return stored_state
    if target_kind == "understanding_claim" and base_revision_id != latest_revision_id:
        return "stale"
    if current_value is None or current_value != before_value:
        return "conflict"
    return "resolved"
