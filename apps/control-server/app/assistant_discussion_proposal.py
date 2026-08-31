"""Conversation-to-proposal changeset generation (Issue #439, Epic #436).

`docs/assistant-discussion.md` §2 is the canonical contract. This module
owns:

- `PROPOSAL_TARGET_SCHEMA`, the ONLY definition of which `field_name` /
  `relation_kind` a discussion target (`assistant_discussion.
  DISCUSSION_TARGET_KINDS`) may carry. Every value in it is taken straight
  from the real domain function it is applied through (`ux_design.
  add_journey_revision`'s keyword parameters, `solution_design.add_option`'s,
  ...) -- `tests/test_assistant_discussion_proposals.py` asserts that
  correspondence directly so the registry cannot silently drift from the API
  it applies through. A `field_name` / `relation_kind` outside this registry
  is refused, fail-closed, at BOTH generation time (the whole LLM call fails
  rather than silently dropping the offending item -- a proposal containing
  an invented field is not a partially-correct proposal) and apply time
  (defense in depth for a row that reached this table some other way).
- `generate_proposal`, the reasoning-model structured-output call. It is a
  pure `(client, config, ...) -> ProposalGenerationResult` function -- no
  database access -- so callers can run it with no `get_conn()` connection
  open (CLAUDE.md: never hold a connection across an LLM round trip). A
  mock/unusable/non-reasoning provider or a failed/invalid response never
  produces a proposal (Principle 6): the result's `error` /
  `error_kind` tell the route which HTTP status to use.
- `PROPOSAL_ITEM_ELIGIBILITY` and `evaluate_item_eligibility`, the read-time,
  first-match (`forbidden` -> `stale` -> `conflict` -> `appliable`)
  derivation §2.2 requires. Never a stored column, so a target that changed
  after generation cannot keep reading as appliable forever.
- `apply_items` / `reject_items`, the only writers. Applying NEVER writes
  this module's own tables' content into a canonical row directly -- every
  field/relation change is applied through the SAME existing domain service
  a human-authored write would use (`ux_design.add_journey_revision`,
  `journey_blueprint.add_delivery_link`, ...), always with
  `decision_method="manual"` on the resulting row (and, where the service
  accepts it, `authored_by_kind="reasoning_model"` -- the content came from
  the model, but a human decided to apply it). This never confirms anything:
  the target's own decision ledger (a Journey's `design_status`, a Solution
  Design Option's `option_status`, ...) is untouched by an apply, exactly as
  it would be untouched by a developer typing the same edit through the
  normal write endpoint. `apply_items` validates EVERY selected item's
  eligibility BEFORE writing any of them -- if any is not `appliable`,
  nothing is applied (§2.2's all-or-nothing gate).

No LLM call happens anywhere except inside `generate_proposal`, and that
function itself never touches the database -- the read -> reason -> persist
split CLAUDE.md's Implementation Constraints require for every endpoint that
combines a DB read with an LLM call.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field as PydanticField, ValidationError

from . import journey_blueprint, solution_design, ux_design
from .db import get_conn
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "discussion-proposal-v1"
SCHEMA_VERSION = "discussion-proposal-v1"

PROPOSAL_ITEM_KINDS: Tuple[str, ...] = ("field", "relation")
PROPOSAL_ITEM_STATUSES: Tuple[str, ...] = ("proposed", "applied", "rejected")
#: §2.2's first-match eligibility order. `evaluate_item_eligibility` returns
#: exactly one of these, checked in this priority.
PROPOSAL_ITEM_ELIGIBILITY: Tuple[str, ...] = ("forbidden", "stale", "conflict", "appliable")

MAX_LISTED_PROPOSALS = 50

#: §2.1's registry: `target_kind -> {"fields": (...), "relations": (...)}`.
#: This is the ONLY place these tuples are declared -- every value is taken
#: directly from the real domain function's own keyword parameters (see the
#: per-target comment below), and the correspondence is asserted by
#: `tests/test_assistant_discussion_proposals.py`.
PROPOSAL_TARGET_SCHEMA: Dict[str, Dict[str, Tuple[str, ...]]] = {
    # `ux_design.add_journey_revision`'s own content keyword parameters
    # (excluding `change_note`, `steps`, and the authorship/audit params a
    # public write endpoint never exposes either).
    "ux_journey": {
        "fields": (
            "title", "beneficiary", "usage_context", "entry_trigger",
            "value_arrival", "summary",
        ),
        "relations": ("upstream_ref",),
    },
    # The per-step dict keys `add_journey_revision` reads via `step.get(...)`
    # (excluding `step_key` / `step_order` / `evidence_source_kind`, which
    # are identity/ordering/classification, not free-text content).
    "ux_journey_step": {
        "fields": (
            "user_intent", "system_response", "success_criteria",
            "failure_mode", "recovery_path", "evidence_expectation",
        ),
        "relations": (),
    },
    # `ux_design.add_requirement_revision`'s own content keyword parameters.
    "ux_requirement": {
        "fields": ("statement", "rationale", "constraint_text", "out_of_scope_note"),
        "relations": ("journey_step_link",),
    },
    # A Solution Design carries no design-level revision table (its identity
    # row's `title`/`summary` are set once at creation with no update path);
    # a field proposal therefore addresses an OPTION
    # (`solution_design.add_option`'s own content keyword parameters), keyed
    # by `subject_ref=option_key`.
    "solution_design": {
        "fields": ("title", "approach", "tradeoffs", "risks"),
        "relations": ("requirement_link", "target_link"),
    },
    # A lane cell has no field of its own -- only the three link kinds
    # `journey_blueprint.py` owns can move it out of `unknown`.
    "blueprint_lane_cell": {
        "fields": (),
        "relations": ("delivery_link", "stakeholder_link", "exchange_link"),
    },
    # `understanding_brief.BriefClaim`'s own editable content (`name` /
    # `summary` / `contribution`, the last surfaced under its raw-item key
    # `why_core`). Applying always goes through the Intent Brief's own
    # propose-style path (never auto-confirmed, §2.2).
    "understanding_claim": {
        "fields": ("summary", "why_core", "name"),
        "relations": (),
    },
    # Discussion-only targets (§2.1): no field or relation is proposable.
    "overview_finding": {"fields": (), "relations": ()},
    "interview_session": {"fields": (), "relations": ()},
    "screen": {"fields": (), "relations": ()},
}

#: Which relation_kind a blueprint lane_kind may carry (§2.2's "unknown lane
#: -> link proposal" rule, narrowed one level further than
#: `PROPOSAL_TARGET_SCHEMA` can express since it does not vary by
#: target_kind alone here -- it varies by the LANE embedded in target_ref).
#: The three lanes fed only by free text on the Step itself (requirement /
#: evidence / failure_recovery) carry no link-based relation at all.
_LANE_RELATION_COMPAT: Dict[str, Tuple[str, ...]] = {
    "stakeholder_action": ("stakeholder_link",),
    "touchpoint": ("exchange_link",),
    "frontstage": ("delivery_link",),
    "backstage": ("delivery_link",),
    "support": ("delivery_link",),
    "external": ("delivery_link",),
    "requirement": (),
    "evidence": (),
    "failure_recovery": (),
}

_JOURNEY_STEP_KEYS: Tuple[str, ...] = (
    "step_key", "step_order", "user_intent", "system_response", "success_criteria",
    "failure_mode", "recovery_path", "evidence_expectation", "evidence_source_kind",
)
_CRITERION_KEYS: Tuple[str, ...] = (
    "criterion_key", "criterion_order", "statement", "verification_method", "verification_note",
)


class DiscussionProposalError(ValueError):
    """Base class for every failure this module raises."""


class NotFound(DiscussionProposalError):
    """A proposal / item does not exist, or belongs to another System."""


class InvalidField(DiscussionProposalError):
    """A field/relation proposal cannot be applied as addressed (missing
    `subject_ref`, an unresolvable step, ...) -- 422."""


class ApplyRejected(DiscussionProposalError):
    """One selected item was not `appliable` (§2.2's all-or-nothing gate).
    Carries the finite rejection `code` (`proposal_item_forbidden` /
    `proposal_item_stale` / `proposal_item_conflict`) and the offending
    `item_id` so the route can report both."""

    def __init__(self, code: str, item_id: int):
        super().__init__(f"{code}: item {item_id}")
        self.code = code
        self.item_id = item_id


def _subject_ref_required(target_kind: str, item_kind: str, relation_kind: str = "") -> bool:
    """Only a Solution Design field, or its `target_link` relation, needs a
    sub-address (an Option's `option_key`) -- every other combination
    addresses its target directly."""
    if target_kind != "solution_design":
        return False
    if item_kind == "field":
        return True
    return relation_kind == "target_link"


# --- §2.2 generation: structured output, fail-closed --------------------------


@dataclass
class ProposedFieldChange:
    field_name: str
    subject_ref: str = ""
    current_value: str = ""
    proposed_value: str = ""
    rationale: str = ""


@dataclass
class ProposedRelationChange:
    relation_kind: str
    subject_ref: str = ""
    relation_target_kind: str = ""
    relation_target_ref: str = ""
    proposed_value: str = ""
    rationale: str = ""


@dataclass
class ProposalGenerationResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    summary: str = ""
    confirmed_points: List[str] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    field_changes: List[ProposedFieldChange] = field(default_factory=list)
    relation_changes: List[ProposedRelationChange] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    error: Optional[str] = None
    #: One of "unavailable" (no usable reasoning-model client -- 503
    #: `reasoning_unavailable`), "call_error" / "invalid_response" /
    #: "invalid_registry" (a real attempt failed -- 502), or `None` (success).
    error_kind: Optional[str] = None


class _RawFieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    subject_ref: str = ""
    current_value: str = ""
    proposed_value: str = PydanticField(default="", max_length=8000)
    rationale: str = PydanticField(default="", max_length=2000)


class _RawRelationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_kind: str
    subject_ref: str = ""
    relation_target_kind: str = ""
    relation_target_ref: str = ""
    proposed_value: str = PydanticField(default="", max_length=2000)
    rationale: str = PydanticField(default="", max_length=2000)


class _RawProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    confirmed_points: List[str] = PydanticField(default_factory=list, max_length=20)
    unresolved_questions: List[str] = PydanticField(default_factory=list, max_length=20)
    assumptions: List[str] = PydanticField(default_factory=list, max_length=20)
    evidence_refs: List[str] = PydanticField(default_factory=list, max_length=20)
    field_changes: List[_RawFieldChange] = PydanticField(default_factory=list, max_length=20)
    relation_changes: List[_RawRelationChange] = PydanticField(default_factory=list, max_length=20)


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
You are summarizing a scoped discussion thread about ONE specific design
target into a REVIEWABLE, STRUCTURED change proposal. You never invent a
fact the conversation does not support, and you never propose a field name
or relation kind outside the ones explicitly listed for this target -- an
unlisted name fails the ENTIRE proposal, so when in doubt, omit it rather
than guess.

Respond with a single JSON object and nothing else (no markdown fences, no
commentary), matching exactly this shape:

{
  "summary": "...",
  "confirmed_points": ["..."],
  "unresolved_questions": ["..."],
  "assumptions": ["..."],
  "evidence_refs": ["..."],
  "field_changes": [
    {"field_name": "...", "subject_ref": "", "current_value": "...",
     "proposed_value": "...", "rationale": "..."}
  ],
  "relation_changes": [
    {"relation_kind": "...", "subject_ref": "", "relation_target_kind": "...",
     "relation_target_ref": "...", "proposed_value": "...", "rationale": "..."}
  ]
}

Rules:
- "field_name" must be exactly one of "allowed_fields" below. Never invent one.
- "relation_kind" must be exactly one of "allowed_relations" below.
- "subject_ref" is a sub-address INSIDE the target (only meaningful for a
  Solution Design Option's own key); leave it "" unless the target facts
  name one.
- Never propose a change the conversation does not actually support.
- If nothing can be proposed, return empty "field_changes" and
  "relation_changes" arrays -- do not guess.
"""


def _build_user_prompt(
    target_kind: str,
    target_ref: str,
    target_title: str,
    turns: Sequence[Dict[str, Any]],
    target_facts: Dict[str, Any],
    schema: Dict[str, Tuple[str, ...]],
) -> str:
    convo = "\n".join(f"{t.get('role', '?')}: {t.get('content', '')}" for t in turns)
    parts = [
        f"target_kind: {target_kind}",
        f"target_ref: {target_ref}",
        f"target_title: {target_title}",
        f"allowed_fields: {json.dumps(list(schema.get('fields', ())))}",
        f"allowed_relations: {json.dumps(list(schema.get('relations', ())))}",
        f"target_current_facts: {json.dumps(target_facts, ensure_ascii=False)}",
        "conversation:",
        convo or "(no messages yet)",
    ]
    return "\n\n".join(parts)


def generate_proposal(
    client: Optional[LLMClient],
    config: LLMConfig,
    *,
    target_kind: str,
    target_ref: str,
    target_title: str,
    turns: Sequence[Dict[str, Any]],
    target_facts: Dict[str, Any],
) -> ProposalGenerationResult:
    """§2.2's generation step. Pure -- no database access -- so callers hold
    no `get_conn()` connection while this runs. Fail-closed throughout
    (Principle 6): a mock/unusable/non-reasoning client, a failed API call,
    an invalid structured response, or a field/relation name outside
    `PROPOSAL_TARGET_SCHEMA` all fail the WHOLE call -- a proposal containing
    one invented field is not a partially-correct proposal.
    """
    is_mock = config.provider == "mock" or isinstance(client, MockLLMClient)
    if client is None or is_mock or not is_reasoning_model(config.provider, config.model):
        return ProposalGenerationResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Discussion proposal generation requires a configured reasoning "
                "model; mock/heuristic fallback is prohibited"
            ),
            error_kind="unavailable",
        )

    schema = PROPOSAL_TARGET_SCHEMA.get(target_kind, {"fields": (), "relations": ()})
    prompt = _build_user_prompt(target_kind, target_ref, target_title, turns, target_facts, schema)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=3000,
        )
    except LLMError as exc:
        return ProposalGenerationResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=str(exc), error_kind="call_error",
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawProposalResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return ProposalGenerationResult(
            provider=config.provider, model=config.model, is_mock=False,
            error=f"Failed to parse structured response: {exc}", error_kind="invalid_response",
        )

    field_changes: List[ProposedFieldChange] = []
    for raw_item in validated.field_changes:
        if raw_item.field_name not in schema["fields"]:
            return ProposalGenerationResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model proposed a field outside the registry: {raw_item.field_name!r}",
                error_kind="invalid_registry",
            )
        if _subject_ref_required(target_kind, "field") and not raw_item.subject_ref.strip():
            return ProposalGenerationResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"{target_kind} field {raw_item.field_name!r} requires a subject_ref",
                error_kind="invalid_registry",
            )
        field_changes.append(
            ProposedFieldChange(
                field_name=raw_item.field_name, subject_ref=raw_item.subject_ref,
                current_value=raw_item.current_value, proposed_value=raw_item.proposed_value,
                rationale=raw_item.rationale,
            )
        )

    relation_changes: List[ProposedRelationChange] = []
    for raw_item in validated.relation_changes:
        if raw_item.relation_kind not in schema["relations"]:
            return ProposalGenerationResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model proposed a relation outside the registry: {raw_item.relation_kind!r}",
                error_kind="invalid_registry",
            )
        if target_kind == "blueprint_lane_cell":
            lane_kind = target_ref.rsplit("#", 1)[-1]
            if raw_item.relation_kind not in _LANE_RELATION_COMPAT.get(lane_kind, ()):
                return ProposalGenerationResult(
                    provider=config.provider, model=config.model, is_mock=False,
                    error=(
                        f"relation {raw_item.relation_kind!r} is not valid for lane "
                        f"{lane_kind!r}"
                    ),
                    error_kind="invalid_registry",
                )
        if (
            _subject_ref_required(target_kind, "relation", raw_item.relation_kind)
            and not raw_item.subject_ref.strip()
        ):
            return ProposalGenerationResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"{target_kind} relation {raw_item.relation_kind!r} requires a subject_ref",
                error_kind="invalid_registry",
            )
        relation_changes.append(
            ProposedRelationChange(
                relation_kind=raw_item.relation_kind, subject_ref=raw_item.subject_ref,
                relation_target_kind=raw_item.relation_target_kind,
                relation_target_ref=raw_item.relation_target_ref,
                proposed_value=raw_item.proposed_value, rationale=raw_item.rationale,
            )
        )

    return ProposalGenerationResult(
        provider=config.provider, model=config.model, is_mock=False,
        summary=validated.summary, confirmed_points=list(validated.confirmed_points),
        unresolved_questions=list(validated.unresolved_questions),
        assumptions=list(validated.assumptions), evidence_refs=list(validated.evidence_refs),
        field_changes=field_changes, relation_changes=relation_changes,
    )


# --- Target context for the prompt (read-only, best-effort) -------------------


def gather_target_context(conn, system_id: int, target_kind: str, target_ref: str) -> Dict[str, Any]:
    """Best-effort, read-only canonical facts fed into the prompt. Never
    raises -- an unreadable/missing target degrades to `{}` (the actual gate
    on whether a target can be proposed against at all is the digest/
    staleness check in `evaluate_item_eligibility`, not this helper)."""
    try:
        if target_kind == "ux_journey":
            detail = ux_design.get_journey_detail(conn, system_id, target_ref)
            rev = detail.get("current_revision") or {}
            return {k: rev.get(k, "") for k in PROPOSAL_TARGET_SCHEMA["ux_journey"]["fields"]}
        if target_kind == "ux_journey_step":
            journey_key, _, step_key = target_ref.partition("#")
            detail = ux_design.get_journey_detail(conn, system_id, journey_key)
            rev = detail.get("current_revision") or {}
            for step in rev.get("steps", []):
                if step.get("step_key") == step_key:
                    return {k: step.get(k, "") for k in PROPOSAL_TARGET_SCHEMA["ux_journey_step"]["fields"]}
            return {}
        if target_kind == "ux_requirement":
            detail = ux_design.get_requirement_detail(conn, system_id, target_ref)
            rev = detail.get("current_revision") or {}
            return {k: rev.get(k, "") for k in PROPOSAL_TARGET_SCHEMA["ux_requirement"]["fields"]}
        if target_kind == "solution_design":
            detail = solution_design.get_design_detail(conn, system_id=system_id, design_key=target_ref)
            fields = PROPOSAL_TARGET_SCHEMA["solution_design"]["fields"]
            return {
                "options": [
                    {"option_key": opt.get("option_key", ""), **{k: opt.get(k, "") for k in fields}}
                    for opt in detail.get("options", [])
                ]
            }
        if target_kind == "blueprint_lane_cell":
            parts = target_ref.split("#")
            if len(parts) != 3:
                return {}
            journey_key, step_key, lane_kind = parts
            blueprint = journey_blueprint.build_blueprint(conn, system_id, journey_key)
            for step in blueprint.get("steps", []):
                if step.get("step_key") == step_key:
                    return {"lane_kind": lane_kind, "cell": step.get("lanes", {}).get(lane_kind, {})}
            return {}
        if target_kind == "understanding_claim":
            return _understanding_claim_context(conn, system_id, target_ref)
    except Exception:  # pragma: no cover - defensive, mirrors resolvers' own rule
        return {}
    return {}


def _understanding_claim_context(conn, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import understanding_brief

    section, sep, name = target_ref.partition(":")
    if not sep:
        return {}
    session_row = conn.execute(
        "SELECT id FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    session_id = session_row["id"] if session_row is not None else None
    brief = understanding_brief.build_understanding_brief(conn, system_id, session_id)
    claims = {
        "vision": [brief.vision] if brief.vision is not None else [],
        "system_purpose": brief.system_purpose,
        "core_capabilities": brief.core_capabilities,
    }.get(section, [])
    claim = next((c for c in claims if c is not None and c.name == name), None)
    if claim is None:
        return {}
    return {"name": claim.name, "summary": claim.summary, "why_core": claim.contribution}


# --- Persistence ---------------------------------------------------------------


def _proposal_out(row: Any) -> Dict[str, Any]:
    d = dict(row)
    d["confirmed_points"] = json.loads(d.pop("confirmed_points_json") or "[]")
    d["unresolved_questions"] = json.loads(d.pop("unresolved_questions_json") or "[]")
    d["assumptions"] = json.loads(d.pop("assumptions_json") or "[]")
    d["evidence_refs"] = json.loads(d.pop("evidence_refs_json") or "[]")
    return d


def get_proposal_row(conn, system_id: int, proposal_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM assistant_discussion_proposal WHERE id = ? AND system_id = ?",
        (proposal_id, system_id),
    ).fetchone()
    return dict(row) if row is not None else None


def _items_for(conn, proposal_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM assistant_discussion_proposal_item WHERE proposal_id = ? ORDER BY id",
        (proposal_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _item_address(item: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        item.get("subject_ref") or "",
        item.get("field_name") or "",
        item.get("relation_kind") or "",
        item.get("relation_target_kind") or "",
        item.get("relation_target_ref") or "",
    )


def evaluate_item_eligibility(
    proposal: Dict[str, Any],
    item: Dict[str, Any],
    all_items: Sequence[Dict[str, Any]],
    resolved: Any,
) -> str:
    """§2.2's first-match table: `forbidden` -> `stale` -> `conflict` ->
    `appliable`. `resolved` is an `assistant_discussion.ResolvedTarget`
    freshly re-resolved against the target's CURRENT canonical source --
    never the proposal's own captured baseline."""
    target_kind = proposal["target_kind"]
    schema = PROPOSAL_TARGET_SCHEMA.get(target_kind, {"fields": (), "relations": ()})
    if item["item_kind"] == "field":
        allowed = bool(item["field_name"]) and item["field_name"] in schema["fields"]
    else:
        allowed = bool(item["relation_kind"]) and item["relation_kind"] in schema["relations"]
        if allowed and target_kind == "blueprint_lane_cell":
            lane_kind = proposal["target_ref"].rsplit("#", 1)[-1]
            allowed = item["relation_kind"] in _LANE_RELATION_COMPAT.get(lane_kind, ())
    if not allowed:
        return "forbidden"

    if (proposal.get("captured_target_digest") or "") != (getattr(resolved, "digest", "") or ""):
        return "stale"

    if item["status"] != "proposed":
        return "conflict"

    address = _item_address(item)
    for other in all_items:
        if other["id"] == item["id"]:
            continue
        if other["status"] == "applied" and _item_address(other) == address:
            return "conflict"

    return "appliable"


def create_proposal(
    conn,
    *,
    system_id: int,
    thread_id: int,
    screen_id: str,
    target_kind: str,
    target_ref: str,
    captured_target_revision_id: Optional[int],
    captured_target_digest: str,
    result: ProposalGenerationResult,
    intelligence_run_id: Optional[int],
    created_by: Optional[str],
) -> Dict[str, Any]:
    """Persist a successful `ProposalGenerationResult` (§2.3). Called on an
    already-open connection with no external call pending -- the LLM round
    trip already happened in `generate_proposal`."""
    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO assistant_discussion_proposal
                   (system_id, thread_id, screen_id, target_kind, target_ref,
                    captured_target_revision_id, captured_target_digest, summary,
                    confirmed_points_json, unresolved_questions_json, assumptions_json,
                    evidence_refs_json, intelligence_run_id, provider, model,
                    prompt_version, schema_version, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, thread_id, screen_id, target_kind, target_ref,
                captured_target_revision_id, captured_target_digest, result.summary,
                json.dumps(result.confirmed_points, ensure_ascii=False),
                json.dumps(result.unresolved_questions, ensure_ascii=False),
                json.dumps(result.assumptions, ensure_ascii=False),
                json.dumps(result.evidence_refs, ensure_ascii=False),
                intelligence_run_id, result.provider, result.model,
                result.prompt_version, result.schema_version, created_by, now,
            ),
        )
        proposal_id = cur.lastrowid
        for fc in result.field_changes:
            conn.execute(
                """INSERT INTO assistant_discussion_proposal_item
                       (system_id, proposal_id, item_kind, field_name, subject_ref,
                        current_value, proposed_value, rationale, created_at)
                   VALUES (?, ?, 'field', ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, proposal_id, fc.field_name, fc.subject_ref,
                    fc.current_value, fc.proposed_value, fc.rationale, now,
                ),
            )
        for rc in result.relation_changes:
            conn.execute(
                """INSERT INTO assistant_discussion_proposal_item
                       (system_id, proposal_id, item_kind, relation_kind, relation_target_kind,
                        relation_target_ref, subject_ref, proposed_value, rationale, created_at)
                   VALUES (?, ?, 'relation', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, proposal_id, rc.relation_kind, rc.relation_target_kind,
                    rc.relation_target_ref, rc.subject_ref, rc.proposed_value, rc.rationale, now,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    row = get_proposal_row(conn, system_id, proposal_id)
    assert row is not None
    return row


def get_proposal_detail(system_id: int, proposal_id: int) -> Optional[Dict[str, Any]]:
    """§2.2's `GET /assistant/discussion-proposals/{id}`: the proposal plus
    each item's read-time eligibility."""
    with get_conn() as conn:
        row = get_proposal_row(conn, system_id, proposal_id)
        if row is None:
            return None
        items = _items_for(conn, proposal_id)

    from . import assistant_discussion

    resolved = assistant_discussion.resolve_target(system_id, row["target_kind"], row["target_ref"])
    out = _proposal_out(row)
    out["items"] = [
        {**item, "eligibility": evaluate_item_eligibility(row, item, items, resolved)}
        for item in items
    ]
    return out


def list_proposals(system_id: int, thread_id: int) -> List[Dict[str, Any]]:
    """§2.2's `GET /assistant/discussion-threads/{id}/proposals`: newest
    first, capped at `MAX_LISTED_PROPOSALS`."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM assistant_discussion_proposal WHERE system_id = ? AND thread_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (system_id, thread_id, MAX_LISTED_PROPOSALS),
        ).fetchall()
        proposals = [(dict(r), _items_for(conn, r["id"])) for r in rows]

    from . import assistant_discussion

    out: List[Dict[str, Any]] = []
    for row, items in proposals:
        resolved = assistant_discussion.resolve_target(system_id, row["target_kind"], row["target_ref"])
        d = _proposal_out(row)
        d["items"] = [
            {**item, "eligibility": evaluate_item_eligibility(row, item, items, resolved)}
            for item in items
        ]
        out.append(d)
    return out


# --- Apply / reject -------------------------------------------------------------


def _next_option_order(conn, system_id: int, solution_design_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT option_key) AS n FROM solution_design_option "
        "WHERE system_id = ? AND solution_design_id = ?",
        (system_id, solution_design_id),
    ).fetchone()
    return int(row["n"] or 0)


def _apply_ux_journey_field(conn, system_id: int, journey_key: str, field_name: str, proposed_value: str, actor: Optional[str]) -> str:
    detail = ux_design.get_journey_detail(conn, system_id, journey_key)
    rev = detail.get("current_revision") or {}
    kwargs = {f: rev.get(f, "") for f in PROPOSAL_TARGET_SCHEMA["ux_journey"]["fields"]}
    kwargs[field_name] = proposed_value
    steps = [{k: s.get(k, "") for k in _JOURNEY_STEP_KEYS} for s in rev.get("steps", [])]
    new_detail = ux_design.add_journey_revision(
        conn, system_id=system_id, journey_key=journey_key, steps=steps,
        authored_by_kind="reasoning_model", decision_method="manual", created_by=actor, **kwargs,
    )
    return f"journey_revision:{new_detail['current_revision_id']}"


def _apply_ux_journey_step_field(conn, system_id: int, target_ref: str, field_name: str, proposed_value: str, actor: Optional[str]) -> str:
    journey_key, sep, step_key = target_ref.partition("#")
    if not sep:
        raise InvalidField(f"invalid ux_journey_step target_ref: {target_ref!r}")
    detail = ux_design.get_journey_detail(conn, system_id, journey_key)
    rev = detail.get("current_revision") or {}
    journey_fields = {f: rev.get(f, "") for f in PROPOSAL_TARGET_SCHEMA["ux_journey"]["fields"]}
    steps: List[Dict[str, Any]] = []
    found = False
    for s in rev.get("steps", []):
        step_dict = {k: s.get(k, "") for k in _JOURNEY_STEP_KEYS}
        if step_dict["step_key"] == step_key:
            step_dict[field_name] = proposed_value
            found = True
        steps.append(step_dict)
    if not found:
        raise NotFound(f"journey step {step_key!r} not found in {journey_key!r}")
    new_detail = ux_design.add_journey_revision(
        conn, system_id=system_id, journey_key=journey_key, steps=steps,
        authored_by_kind="reasoning_model", decision_method="manual", created_by=actor, **journey_fields,
    )
    return f"journey_revision:{new_detail['current_revision_id']}"


def _apply_ux_requirement_field(conn, system_id: int, requirement_key: str, field_name: str, proposed_value: str, actor: Optional[str]) -> str:
    detail = ux_design.get_requirement_detail(conn, system_id, requirement_key)
    rev = detail.get("current_revision") or {}
    kwargs = {f: rev.get(f, "") for f in PROPOSAL_TARGET_SCHEMA["ux_requirement"]["fields"]}
    kwargs[field_name] = proposed_value
    criteria = [{k: c.get(k, "") for k in _CRITERION_KEYS} for c in rev.get("acceptance_criteria", [])]
    new_detail = ux_design.add_requirement_revision(
        conn, system_id=system_id, requirement_key=requirement_key, acceptance_criteria=criteria,
        authored_by_kind="reasoning_model", decision_method="manual", created_by=actor, **kwargs,
    )
    return f"requirement_revision:{new_detail['current_revision_id']}"


def _apply_solution_design_field(conn, system_id: int, design_key: str, item: Dict[str, Any], actor: Optional[str]) -> str:
    option_key = (item.get("subject_ref") or "").strip()
    if not option_key:
        raise InvalidField("solution_design field proposal requires subject_ref (option_key)")
    design = solution_design._find_design_by_key(conn, system_id, design_key)  # noqa: SLF001 - established cross-module reuse (see ux_design._get_journey_row)
    current = solution_design._current_option_row(conn, system_id, design["id"], option_key)  # noqa: SLF001
    kwargs = {f: (current[f] if current is not None else "") for f in PROPOSAL_TARGET_SCHEMA["solution_design"]["fields"]}
    kwargs[item["field_name"]] = item["proposed_value"]
    option_order = current["option_order"] if current is not None else _next_option_order(conn, system_id, design["id"])
    row = solution_design.add_option(
        conn, system_id=system_id, solution_design_id=design["id"], option_key=option_key,
        option_order=option_order, authored_by_kind="reasoning_model", decision_method="manual",
        created_by=actor, **kwargs,
    )
    return f"solution_design_option:{row['id']}"


def _apply_understanding_claim_field(
    conn, system_id: int, target_ref: str, item: Dict[str, Any], actor: Optional[str], resolved: Any,
) -> str:
    """§2.2: understanding_claim goes through the Intent Brief's existing
    propose-style path -- a NEW `status='proposed'` / `origin='ai_proposed'`
    / `decision_method='reasoning_llm'` item, never auto-confirmed. There is
    no Intent Brief field corresponding 1:1 to a claim's own `summary` /
    `why_core` / `name`; the developer's own confirmed `goal` already
    outranks the reviewer's Vision claim (Issue #351), so an accepted
    discussion proposal about a claim is recorded the same way -- a
    candidate `goal` statement awaiting the developer's explicit
    confirm/correct decision (`routes/interview_intent.py`), never written
    directly.

    `resolved` is the SAME `ResolvedTarget` `apply_items` already computed
    OUTSIDE any open connection -- `assistant_discussion.resolve_target`
    opens its own `get_conn()` internally for this target_kind, and
    `get_conn()` is not reentrant, so re-resolving here (inside the caller's
    open connection) would silently degrade to "unresolved" instead of
    raising, which is exactly the kind of drift Principle 6 forbids.
    """
    session_id = resolved.revision_id
    if session_id is None:
        raise NotFound(f"no interview session backs understanding_claim {target_ref!r}")
    now = time.time()
    cur = conn.execute(
        """INSERT INTO interview_intent_item
               (session_id, system_id, field, value_text, status, origin,
                source_statement, decision_method, intelligence_run_id, is_mock,
                created_at, updated_at)
           VALUES (?, ?, 'goal', ?, 'proposed', 'ai_proposed', ?, 'reasoning_llm', NULL, 0, ?, ?)""",
        (
            session_id, system_id, item["proposed_value"],
            f"discussion:{target_ref}:{item['field_name']}", now, now,
        ),
    )
    return f"intent_item:{cur.lastrowid}"


def _apply_field(conn, system_id: int, target_kind: str, target_ref: str, item: Dict[str, Any], actor: Optional[str], resolved: Any) -> str:
    field_name = item["field_name"]
    proposed_value = item["proposed_value"]
    if target_kind == "ux_journey":
        return _apply_ux_journey_field(conn, system_id, target_ref, field_name, proposed_value, actor)
    if target_kind == "ux_journey_step":
        return _apply_ux_journey_step_field(conn, system_id, target_ref, field_name, proposed_value, actor)
    if target_kind == "ux_requirement":
        return _apply_ux_requirement_field(conn, system_id, target_ref, field_name, proposed_value, actor)
    if target_kind == "solution_design":
        return _apply_solution_design_field(conn, system_id, target_ref, item, actor)
    if target_kind == "understanding_claim":
        return _apply_understanding_claim_field(conn, system_id, target_ref, item, actor, resolved)
    raise InvalidField(f"{target_kind} has no proposable field {field_name!r}")


def _apply_relation(conn, system_id: int, target_kind: str, target_ref: str, item: Dict[str, Any], actor: Optional[str]) -> str:
    relation_kind = item["relation_kind"]
    relation_target_kind = item["relation_target_kind"]
    relation_target_ref = item["relation_target_ref"]
    rationale = item.get("rationale") or ""

    if target_kind == "ux_journey" and relation_kind == "upstream_ref":
        row = ux_design.add_upstream_ref(
            conn, system_id=system_id, journey_key=target_ref, ref_kind=relation_target_kind,
            target_ref=relation_target_ref, note=rationale, decision_method="manual", created_by=actor,
        )
        return f"upstream_ref:{row['id']}"

    if target_kind == "ux_requirement" and relation_kind == "journey_step_link":
        journey_key, sep, step_key = relation_target_ref.partition("#")
        if not sep or not journey_key or not step_key:
            raise InvalidField("journey_step_link relation_target_ref must be '<journey_key>#<step_key>'")
        row = ux_design.add_requirement_step_link(
            conn, system_id=system_id, requirement_key=target_ref, journey_key=journey_key,
            step_key=step_key, note=rationale, decision_method="manual", created_by=actor,
        )
        return f"requirement_step_link:{row['id']}"

    if target_kind == "solution_design":
        design = solution_design._find_design_by_key(conn, system_id, target_ref)  # noqa: SLF001
        if relation_kind == "requirement_link":
            row = solution_design.add_requirement_link(
                conn, system_id=system_id, solution_design_id=design["id"],
                requirement_key=relation_target_ref, note=rationale, decision_method="manual",
                created_by=actor,
            )
            return f"solution_design_requirement_link:{row['id']}"
        if relation_kind == "target_link":
            option_key = (item.get("subject_ref") or "").strip()
            if not option_key:
                raise InvalidField("target_link relation requires subject_ref (option_key)")
            row = solution_design.add_target_link(
                conn, system_id=system_id, solution_design_id=design["id"], option_key=option_key,
                target_kind=relation_target_kind, target_ref=relation_target_ref,
                note=rationale, decision_method="manual", created_by=actor,
            )
            return f"solution_design_target_link:{row['id']}"

    if target_kind == "blueprint_lane_cell":
        parts = target_ref.split("#")
        if len(parts) != 3:
            raise InvalidField(f"invalid blueprint_lane_cell target_ref: {target_ref!r}")
        journey_key, step_key, lane_kind = parts
        if relation_kind == "stakeholder_link":
            role = item["proposed_value"]
            row = journey_blueprint.add_stakeholder_link(
                conn, system_id=system_id, journey_key=journey_key, step_key=step_key,
                stakeholder_key=relation_target_ref, role=role, note=rationale,
                decision_method="manual", created_by=actor,
            )
            return f"journey_step_stakeholder_link:{row['id']}"
        if relation_kind == "delivery_link":
            row = journey_blueprint.add_delivery_link(
                conn, system_id=system_id, journey_key=journey_key, step_key=step_key,
                delivery_kind=lane_kind, target_kind=relation_target_kind,
                target_ref=relation_target_ref, note=rationale, decision_method="manual",
                created_by=actor,
            )
            return f"journey_step_delivery_link:{row['id']}"
        if relation_kind == "exchange_link":
            row = journey_blueprint.add_exchange_link(
                conn, system_id=system_id, journey_key=journey_key, step_key=step_key,
                exchange_key=relation_target_ref, note=rationale, decision_method="manual",
                created_by=actor,
            )
            return f"journey_step_exchange_link:{row['id']}"

    raise InvalidField(f"{target_kind}/{relation_kind} relation is not applicable")


def apply_items(
    system_id: int,
    proposal_id: int,
    item_ids: Sequence[int],
    *,
    rationale: str,
    actor: Optional[str],
) -> Tuple[Dict[str, Any], List[int]]:
    """§2.2's `POST /assistant/discussion-proposals/{id}/apply`. All-or-
    nothing: every selected item's eligibility is checked BEFORE any write,
    so a single non-`appliable` item leaves the proposal untouched
    (`ApplyRejected`). Applying goes through the SAME existing domain
    service a human write would use, `decision_method='manual'` on the
    resulting row -- this never confirms the target itself (§2.2)."""
    from . import assistant_discussion

    with get_conn() as conn:
        proposal = get_proposal_row(conn, system_id, proposal_id)
        if proposal is None:
            raise NotFound(f"discussion proposal {proposal_id} not found")
        all_items = _items_for(conn, proposal_id)
        by_id = {item["id"]: item for item in all_items}
        selected: List[Dict[str, Any]] = []
        for item_id in item_ids:
            item = by_id.get(item_id)
            if item is None:
                raise NotFound(f"proposal item {item_id} not found")
            selected.append(item)

    resolved = assistant_discussion.resolve_target(system_id, proposal["target_kind"], proposal["target_ref"])

    for item in selected:
        eligibility = evaluate_item_eligibility(proposal, item, all_items, resolved)
        if eligibility != "appliable":
            raise ApplyRejected(f"proposal_item_{eligibility}", item["id"])

    applied_ids: List[int] = []
    with get_conn() as conn:
        now = time.time()
        for item in selected:
            if item["item_kind"] == "field":
                applied_ref = _apply_field(conn, system_id, proposal["target_kind"], proposal["target_ref"], item, actor, resolved)
            else:
                applied_ref = _apply_relation(conn, system_id, proposal["target_kind"], proposal["target_ref"], item, actor)
            conn.execute(
                """UPDATE assistant_discussion_proposal_item
                       SET status = 'applied', applied_ref = ?, decided_by = ?,
                           decided_at = ?, decision_method = 'manual'
                   WHERE id = ?""",
                (applied_ref, actor, now, item["id"]),
            )
            applied_ids.append(item["id"])

    detail = get_proposal_detail(system_id, proposal_id)
    assert detail is not None
    return detail, applied_ids


def reject_items(
    system_id: int,
    proposal_id: int,
    item_ids: Sequence[int],
    *,
    rationale: str,
    actor: Optional[str],
) -> Tuple[Dict[str, Any], List[int]]:
    """§2.2's `POST /assistant/discussion-proposals/{id}/reject`: marks the
    selected items `rejected` (audit only, `decision_method='manual'`).
    Only a still-`proposed` item can be rejected."""
    rejected_ids: List[int] = []
    with get_conn() as conn:
        proposal = get_proposal_row(conn, system_id, proposal_id)
        if proposal is None:
            raise NotFound(f"discussion proposal {proposal_id} not found")
        now = time.time()
        for item_id in item_ids:
            row = conn.execute(
                "SELECT * FROM assistant_discussion_proposal_item WHERE id = ? AND proposal_id = ?",
                (item_id, proposal_id),
            ).fetchone()
            if row is None:
                raise NotFound(f"proposal item {item_id} not found")
            if row["status"] != "proposed":
                raise DiscussionProposalError(
                    f"proposal_item_not_proposed: item {item_id} is already {row['status']!r}"
                )
            conn.execute(
                """UPDATE assistant_discussion_proposal_item
                       SET status = 'rejected', decided_by = ?, decided_at = ?, decision_method = 'manual'
                   WHERE id = ?""",
                (actor, now, item_id),
            )
            rejected_ids.append(item_id)

    detail = get_proposal_detail(system_id, proposal_id)
    assert detail is not None
    return detail, rejected_ids
