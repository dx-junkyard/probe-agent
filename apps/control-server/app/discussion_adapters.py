"""The single DiscussionAdapter registry (Issue #444, Epic #443 Phase 1).

`docs/ai-discussion-adapter.md` §1 is the canonical contract. Before this
module existed, adding one `target_kind` meant touching SIX parallel
per-kind tables spread across `assistant_discussion.py` and
`assistant_discussion_proposal.py` (`SCOPE_TARGET_KINDS` /
`_TARGET_RESOLVERS` / `route_params_for_target` / `PROPOSAL_TARGET_SCHEMA` /
`gather_target_context` / `_apply_field` + `_apply_relation`). Forgetting one
of the six did not fail loudly -- it silently degraded that target (a
resolver with no route-params entry just never gets its own facts injected
into the context pack). This module makes the registry the single place a
`target_kind` is described, so `tests/test_discussion_adapter_registry.py`
can assert directly that every `DISCUSSION_TARGET_KINDS` member has exactly
one adapter and that no per-kind branch survives outside it.

This is a Phase 1 move, not a redesign: every resolver, route-param mapping,
field/relation registry entry, context provider, and field/relation applier
below is the SAME logic `assistant_discussion.py` /
`assistant_discussion_proposal.py` carried before this module existed, only
relocated. `assistant_discussion.py` and `assistant_discussion_proposal.py`
now DERIVE their public module-level constants (`SCOPE_TARGET_KINDS`,
`DISCUSSION_TARGET_KINDS`, `PROPOSAL_TARGET_SCHEMA`) from `DISCUSSION_ADAPTERS`
here rather than declaring them by hand, and delegate `resolve_target` /
`route_params_for_target` / `gather_target_context` to the matching
adapter -- but every one of those functions keeps its own exact public
signature and never-raises/degrades-to-empty behaviour.

`ChildSpec` and `UiDraftFormSpec` are declared now, in Phase 1, with every
adapter's `children` and `ui_draft_forms` left as empty tuples and
`joint_understanding_bridge` left `False`. This is deliberate: declaring the
shape now is what lets Phases 2 (#445 UI draft), 3 (#446 prefill), 5 (#448
nested/list changes) and 6 (#449 Joint Understanding bridge) be ADDITIVE --
populating a field on an existing adapter -- rather than another schema
change that touches every call site again.

Import direction (this is what avoids a circular import): this module has
NO top-level dependency on `assistant_discussion.py` or
`assistant_discussion_proposal.py` -- it is a leaf. Those two modules import
this one instead. The one place this module needs `assistant_discussion_
proposal`'s `_apply_field` / `_apply_relation` (the field/relation applier
dispatchers `apply_items` already calls directly and unchanged) is deferred
to CALL TIME via a local import inside `_delegate_apply_field` /
`_delegate_apply_relation` -- by the time those run, both modules have long
finished loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from .db import get_conn

# --- §1.1: the 4 discussion-enabled screens -----------------------------------
# Moved here (not duplicated) because the `screen` adapter's own `screen_ids`
# is exactly this tuple -- every discussion-enabled screen has a "whole
# screen" conversation. `assistant_discussion.py` re-exports this name so
# existing importers are unaffected.
DISCUSSION_SCREEN_IDS: Tuple[str, ...] = (
    "overview", "interview", "ux-design-studio", "journey-blueprint",
)


# --- ResolvedTarget + digest helpers (moved from assistant_discussion.py, unchanged) ---


@dataclass(frozen=True)
class ResolvedTarget:
    title: str
    revision_id: Optional[int]
    digest: str
    resolution: str  # "resolved" | "unresolved" | "not_tracked"


def _canonical_digest(payload: Any) -> str:
    import hashlib
    import json

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_json_digest(text: Optional[str]) -> str:
    import json

    if not text:
        canonical: Any = None
    else:
        try:
            canonical = json.loads(text)
        except (TypeError, ValueError):
            canonical = text
    return _canonical_digest(canonical)


# --- §5.1 / §2.2 declared-now, populated-later shapes -------------------------


@dataclass(frozen=True)
class ChildSpec:
    """§5.1 (Issue #448). A nested/list collection a proposal item may target
    (e.g. a Journey's steps, a Requirement's acceptance criteria). Every
    adapter's `children` stays `()` through Phase 1-4 -- declaring the shape
    now means a later phase populates a tuple instead of adding a new field
    to every adapter and every call site that reads one."""

    child_kind: str
    key_field: str
    order_field: str
    fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UiDraftFormSpec:
    """§2.2/§2.3 (Issue #445/#446). Which unsaved Dashboard form a discussion
    may read as a UI draft, and the field allowlist that bounds it. Every
    adapter's `ui_draft_forms` stays `()` through Phase 1 -- no capability
    reads or writes a UI draft yet, so declaring an empty tuple here is what
    keeps `capabilities_for`'s `read_ui_draft` / `prefill_form` derivation
    correctly `False` for every target_kind today."""

    form_id: str
    fields: Tuple[str, ...] = ()


# --- Type aliases for the callables an adapter carries ------------------------

Resolver = Callable[[int, str], ResolvedTarget]
ContextProvider = Callable[[Any, int, str], Dict[str, Any]]
RouteParamsFn = Callable[[str], Dict[str, str]]
# (conn, system_id, target_kind, target_ref, item, actor, resolved) -> applied_ref
FieldApplier = Callable[[Any, int, str, str, Dict[str, Any], Optional[str], Any], str]
# (conn, system_id, target_kind, target_ref, item, actor) -> applied_ref
RelationApplier = Callable[[Any, int, str, str, Dict[str, Any], Optional[str]], str]


@dataclass(frozen=True)
class DiscussionAdapter:
    """§1.4's single per-`target_kind` record. `scope` is fixed per kind
    (never chosen by the caller); `screen_ids` is which screen(s) may open a
    thread on this kind (§1.7's `discussion_target_screen_mismatch` gate)."""

    target_kind: str
    scope: str  # "screen" | "entity" | "element"
    screen_ids: Tuple[str, ...]
    label: str  # Japanese display name (singular noun)
    resolver: Resolver
    context_provider: Optional[ContextProvider]
    route_params: RouteParamsFn
    fields: Tuple[str, ...] = ()
    relations: Tuple[str, ...] = ()
    children: Tuple[ChildSpec, ...] = field(default_factory=tuple)
    ui_draft_forms: Tuple[UiDraftFormSpec, ...] = field(default_factory=tuple)
    field_applier: Optional[FieldApplier] = None
    relation_applier: Optional[RelationApplier] = None
    joint_understanding_bridge: bool = False


# --- §1.2/§1.3 per-kind resolvers (moved verbatim from assistant_discussion.py) ---


def _resolve_screen(system_id: int, target_ref: str) -> ResolvedTarget:
    # `screen` has no digest source (§1.2): never `stale`, always `not_tracked`.
    return ResolvedTarget(title=target_ref, revision_id=None, digest="", resolution="not_tracked")


def _resolve_interview_session(system_id: int, target_ref: str) -> ResolvedTarget:
    try:
        session_id = int(target_ref)
    except (TypeError, ValueError):
        return ResolvedTarget("", None, "", "unresolved")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, title, current_understanding FROM interview_session "
            "WHERE id = ? AND system_id = ?",
            (session_id, system_id),
        ).fetchone()
    if row is None:
        return ResolvedTarget("", None, "", "unresolved")
    digest = _normalized_json_digest(row["current_understanding"])
    title = row["title"] or f"Interview session #{session_id}"
    return ResolvedTarget(title=title, revision_id=session_id, digest=digest, resolution="resolved")


_CLAIM_SECTIONS: Tuple[str, ...] = ("vision", "system_purpose", "core_capabilities")


def _resolve_understanding_claim(system_id: int, target_ref: str) -> ResolvedTarget:
    section, sep, name = target_ref.partition(":")
    if not sep or section not in _CLAIM_SECTIONS or not name:
        return ResolvedTarget("", None, "", "unresolved")

    from . import understanding_brief
    import json as _json

    with get_conn() as conn:
        session_row = conn.execute(
            "SELECT id FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
        session_id = session_row["id"] if session_row is not None else None
        try:
            brief = understanding_brief.build_understanding_brief(conn, system_id, session_id)
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")

        claims = {
            "vision": [brief.vision] if brief.vision is not None else [],
            "system_purpose": brief.system_purpose,
            "core_capabilities": brief.core_capabilities,
        }[section]
        claim = next((c for c in claims if c.name == name), None)
        if claim is None:
            return ResolvedTarget("", None, "", "unresolved")

        raw_item: Optional[Dict[str, Any]] = None
        if session_id is not None:
            understanding_row = conn.execute(
                "SELECT current_understanding FROM interview_session WHERE id = ?",
                (session_id,),
            ).fetchone()
            if understanding_row is not None and understanding_row["current_understanding"]:
                try:
                    parsed = _json.loads(understanding_row["current_understanding"])
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    for item in parsed.get(section) or []:
                        if isinstance(item, dict) and str(item.get("name")) == name:
                            raw_item = item
                            break

    # A resolved claim with no findable raw item (defensive: the claim
    # resolved successfully because the NAME matched) degrades the digest to
    # "" rather than fabricating one that was never truly captured -- the
    # same rule `product_objective._resolve_vision_target` applies.
    digest = understanding_brief.claim_digest(raw_item) if raw_item is not None else ""
    return ResolvedTarget(title=claim.name, revision_id=session_id, digest=digest, resolution="resolved")


def _resolve_overview_finding(system_id: int, target_ref: str) -> ResolvedTarget:
    from .overview_projection import build_overview

    try:
        overview = build_overview(system_id)
    except Exception:  # pragma: no cover - defensive
        return ResolvedTarget("", None, "", "unresolved")
    finding = next((f for f in overview.findings if f.dedupe_key == target_ref), None)
    if finding is None:
        return ResolvedTarget("", None, "", "unresolved")
    payload = {
        "kind": finding.kind,
        "severity": finding.severity,
        "summary": finding.summary,
        "decision_impact": finding.decision_impact,
        "provenance": finding.provenance,
        "dedupe_key": finding.dedupe_key,
    }
    return ResolvedTarget(
        title=finding.summary,
        revision_id=finding.revision_id,
        digest=_canonical_digest(payload),
        resolution="resolved",
    )


def _resolve_ux_journey(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import ux_design

    with get_conn() as conn:
        try:
            detail = ux_design.get_journey_detail(conn, system_id, target_ref)
        except ux_design.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
    revision = detail.get("current_revision")
    digest = revision["content_digest"] if revision else ""
    return ResolvedTarget(
        title=detail.get("title") or target_ref,
        revision_id=detail.get("current_revision_id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_ux_journey_step(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import ux_design

    journey_key, sep, step_key = target_ref.partition("#")
    if not sep or not journey_key or not step_key:
        return ResolvedTarget("", None, "", "unresolved")
    with get_conn() as conn:
        journey = ux_design._get_journey_row(conn, system_id, journey_key)  # noqa: SLF001 - established cross-module reuse (see product_objective.py)
        if journey is None:
            return ResolvedTarget("", None, "", "unresolved")
        resolved = ux_design._resolve_step_target(conn, system_id, journey["id"], step_key)  # noqa: SLF001
    if resolved["resolution"] != "resolved":
        return ResolvedTarget("", None, "", "unresolved")
    title = resolved.get("label") or step_key
    return ResolvedTarget(
        title=title,
        revision_id=journey.get("current_revision_id"),
        digest=resolved.get("digest") or "",
        resolution="resolved",
    )


def _resolve_ux_requirement(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import ux_design

    with get_conn() as conn:
        try:
            detail = ux_design.get_requirement_detail(conn, system_id, target_ref)
        except ux_design.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
    revision = detail.get("current_revision")
    digest = revision["content_digest"] if revision else ""
    return ResolvedTarget(
        title=detail.get("statement") or target_ref,
        revision_id=detail.get("current_revision_id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_solution_design(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import solution_design

    with get_conn() as conn:
        try:
            detail = solution_design.get_design_detail(conn, system_id=system_id, design_key=target_ref)
        except solution_design.SolutionDesignNotFoundError:
            return ResolvedTarget("", None, "", "unresolved")
    # solution_design carries no revision table (flat identity row); its
    # content digest is over the row's own meaning-bearing fields, using its
    # own canonicalization function (never a separate one here).
    digest = solution_design.content_digest(
        {"title": detail.get("title") or "", "summary": detail.get("summary") or ""}
    )
    return ResolvedTarget(
        title=detail.get("title") or target_ref,
        revision_id=detail.get("id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_blueprint_lane_cell(system_id: int, target_ref: str) -> ResolvedTarget:
    from . import journey_blueprint

    parts = target_ref.split("#")
    if len(parts) != 3 or not all(parts):
        return ResolvedTarget("", None, "", "unresolved")
    journey_key, step_key, lane_kind = parts
    if lane_kind not in journey_blueprint.LANE_KINDS:
        return ResolvedTarget("", None, "", "unresolved")
    with get_conn() as conn:
        try:
            blueprint = journey_blueprint.build_blueprint(conn, system_id, journey_key)
        except journey_blueprint.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
    step = next((s for s in blueprint["steps"] if s["step_key"] == step_key), None)
    if step is None:
        return ResolvedTarget("", None, "", "unresolved")
    cell = step.get("lanes", {}).get(lane_kind)
    if cell is None:
        return ResolvedTarget("", None, "", "unresolved")
    title = f"{lane_kind} / {step.get('user_intent') or step_key}"
    return ResolvedTarget(
        title=title, revision_id=None, digest=_canonical_digest(cell), resolution="resolved",
    )


# --- route params for the target's own canonical facts (moved from
#     assistant_discussion.route_params_for_target's per-kind branches) -------


def _route_params_screen(target_ref: str) -> Dict[str, str]:
    return {}


def _route_params_interview_session(target_ref: str) -> Dict[str, str]:
    return {"session": target_ref}


def _route_params_understanding_claim(target_ref: str) -> Dict[str, str]:
    return {}


def _route_params_overview_finding(target_ref: str) -> Dict[str, str]:
    return {}


def _route_params_ux_journey(target_ref: str) -> Dict[str, str]:
    return {"journey": target_ref}


def _route_params_ux_journey_step(target_ref: str) -> Dict[str, str]:
    journey_key, _, _step_key = target_ref.partition("#")
    return {"journey": journey_key} if journey_key else {}


def _route_params_ux_requirement(target_ref: str) -> Dict[str, str]:
    return {"requirement": target_ref}


def _route_params_solution_design(target_ref: str) -> Dict[str, str]:
    return {"design": target_ref}


def _route_params_blueprint_lane_cell(target_ref: str) -> Dict[str, str]:
    journey_key = target_ref.split("#", 1)[0]
    return {"journey": journey_key} if journey_key else {}


# --- §2.1 field/relation registries (moved from assistant_discussion_proposal.
#     PROPOSAL_TARGET_SCHEMA -- the SAME domain-function-derived tuples) ------

# `ux_design.add_journey_revision`'s own content keyword parameters
# (excluding `change_note`, `steps`, and the authorship/audit params a
# public write endpoint never exposes either).
_UX_JOURNEY_FIELDS: Tuple[str, ...] = (
    "title", "beneficiary", "usage_context", "entry_trigger",
    "value_arrival", "summary",
)
_UX_JOURNEY_RELATIONS: Tuple[str, ...] = ("upstream_ref",)

# The per-step dict keys `add_journey_revision` reads via `step.get(...)`
# (excluding `step_key` / `step_order` / `evidence_source_kind`, which are
# identity/ordering/classification, not free-text content).
_UX_JOURNEY_STEP_FIELDS: Tuple[str, ...] = (
    "user_intent", "system_response", "success_criteria",
    "failure_mode", "recovery_path", "evidence_expectation",
)

# `ux_design.add_requirement_revision`'s own content keyword parameters.
_UX_REQUIREMENT_FIELDS: Tuple[str, ...] = (
    "statement", "rationale", "constraint_text", "out_of_scope_note",
)
_UX_REQUIREMENT_RELATIONS: Tuple[str, ...] = ("journey_step_link",)

# A Solution Design carries no design-level revision table (its identity row's
# `title`/`summary` are set once at creation with no update path); a field
# proposal therefore addresses an OPTION (`solution_design.add_option`'s own
# content keyword parameters), keyed by `subject_ref=option_key`.
_SOLUTION_DESIGN_FIELDS: Tuple[str, ...] = ("title", "approach", "tradeoffs", "risks")
_SOLUTION_DESIGN_RELATIONS: Tuple[str, ...] = ("requirement_link", "target_link")

# A lane cell has no field of its own -- only the three link kinds
# `journey_blueprint.py` owns can move it out of `unknown`.
_BLUEPRINT_LANE_CELL_RELATIONS: Tuple[str, ...] = (
    "delivery_link", "stakeholder_link", "exchange_link",
)

# `understanding_brief.BriefClaim`'s own editable content (`name` / `summary`
# / `contribution`, the last surfaced under its raw-item key `why_core`).
# Applying always goes through the Intent Brief's own propose-style path
# (never auto-confirmed, §2.2 of docs/assistant-discussion.md).
_UNDERSTANDING_CLAIM_FIELDS: Tuple[str, ...] = ("summary", "why_core", "name")


# --- context providers (moved from assistant_discussion_proposal.
#     gather_target_context's per-kind branches, unchanged logic) ------------


def _context_ux_journey(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import ux_design

    detail = ux_design.get_journey_detail(conn, system_id, target_ref)
    rev = detail.get("current_revision") or {}
    return {k: rev.get(k, "") for k in _UX_JOURNEY_FIELDS}


def _context_ux_journey_step(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import ux_design

    journey_key, _, step_key = target_ref.partition("#")
    detail = ux_design.get_journey_detail(conn, system_id, journey_key)
    rev = detail.get("current_revision") or {}
    for step in rev.get("steps", []):
        if step.get("step_key") == step_key:
            return {k: step.get(k, "") for k in _UX_JOURNEY_STEP_FIELDS}
    return {}


def _context_ux_requirement(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import ux_design

    detail = ux_design.get_requirement_detail(conn, system_id, target_ref)
    rev = detail.get("current_revision") or {}
    return {k: rev.get(k, "") for k in _UX_REQUIREMENT_FIELDS}


def _context_solution_design(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import solution_design

    detail = solution_design.get_design_detail(conn, system_id=system_id, design_key=target_ref)
    return {
        "options": [
            {"option_key": opt.get("option_key", ""), **{k: opt.get(k, "") for k in _SOLUTION_DESIGN_FIELDS}}
            for opt in detail.get("options", [])
        ]
    }


def _context_blueprint_lane_cell(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import journey_blueprint

    parts = target_ref.split("#")
    if len(parts) != 3:
        return {}
    journey_key, step_key, lane_kind = parts
    blueprint = journey_blueprint.build_blueprint(conn, system_id, journey_key)
    for step in blueprint.get("steps", []):
        if step.get("step_key") == step_key:
            return {"lane_kind": lane_kind, "cell": step.get("lanes", {}).get(lane_kind, {})}
    return {}


def _context_understanding_claim(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
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


# --- field/relation appliers: deferred-import delegates -----------------------
# `apply_items` in `assistant_discussion_proposal.py` calls `_apply_field` /
# `_apply_relation` directly and unchanged (they are big per-target_kind
# dispatchers that keep living there, per docs/ai-discussion-adapter.md §1.1:
# "Keep the existing `_apply_*` helper functions; the registry just names
# which one handles which kind"). These two adapters exist so the registry
# itself carries a non-`None` `field_applier`/`relation_applier` for every
# kind that supports one -- the import of `assistant_discussion_proposal` is
# deferred to CALL TIME specifically so this module never depends on it at
# import time (that module imports THIS one to derive `PROPOSAL_TARGET_
# SCHEMA`, so a top-level import here would be circular).


def _delegate_apply_field(
    conn: Any, system_id: int, target_kind: str, target_ref: str,
    item: Dict[str, Any], actor: Optional[str], resolved: Any,
) -> str:
    from . import assistant_discussion_proposal as _proposal

    return _proposal._apply_field(conn, system_id, target_kind, target_ref, item, actor, resolved)  # noqa: SLF001


def _delegate_apply_relation(
    conn: Any, system_id: int, target_kind: str, target_ref: str,
    item: Dict[str, Any], actor: Optional[str],
) -> str:
    from . import assistant_discussion_proposal as _proposal

    return _proposal._apply_relation(conn, system_id, target_kind, target_ref, item, actor)  # noqa: SLF001


# --- §1.4 the registry itself ---------------------------------------------------

_UX_DESIGN_STUDIO_AND_BLUEPRINT: Tuple[str, ...] = ("ux-design-studio", "journey-blueprint")

DISCUSSION_ADAPTERS: Dict[str, DiscussionAdapter] = {
    "screen": DiscussionAdapter(
        target_kind="screen",
        scope="screen",
        screen_ids=DISCUSSION_SCREEN_IDS,
        label="画面",
        resolver=_resolve_screen,
        context_provider=None,
        route_params=_route_params_screen,
    ),
    "interview_session": DiscussionAdapter(
        target_kind="interview_session",
        scope="entity",
        screen_ids=("interview",),
        label="セッション",
        resolver=_resolve_interview_session,
        context_provider=None,
        route_params=_route_params_interview_session,
    ),
    # BOTH screens that render the Understanding Brief, not just the one a
    # Dashboard candidate is currently derived from. `screen_ids` says where
    # a target legitimately LIVES, which is not the same question as which
    # screens happen to auto-select it today -- deriving the gate from the
    # latter would 422 a developer discussing a Vision claim from the
    # Interview screen, the very screen where they confirm it. The Brief's
    # canonical source is `GET /interview/understanding-brief` and it heads
    # the Interview main column (#351/#356); the Overview embeds the same
    # projection (#380). Both `_interview_context` and `_overview_context`
    # therefore ground this kind.
    "understanding_claim": DiscussionAdapter(
        target_kind="understanding_claim",
        scope="element",
        screen_ids=("overview", "interview"),
        label="理解の主張",
        resolver=_resolve_understanding_claim,
        context_provider=_context_understanding_claim,
        route_params=_route_params_understanding_claim,
        fields=_UNDERSTANDING_CLAIM_FIELDS,
        field_applier=_delegate_apply_field,
    ),
    # Same reachability note as `understanding_claim`: findings render on the
    # Overview only today.
    "overview_finding": DiscussionAdapter(
        target_kind="overview_finding",
        scope="element",
        screen_ids=("overview",),
        label="発見事項",
        resolver=_resolve_overview_finding,
        context_provider=None,
        route_params=_route_params_overview_finding,
    ),
    "ux_journey": DiscussionAdapter(
        target_kind="ux_journey",
        scope="entity",
        screen_ids=_UX_DESIGN_STUDIO_AND_BLUEPRINT,
        label="Journey",
        resolver=_resolve_ux_journey,
        context_provider=_context_ux_journey,
        route_params=_route_params_ux_journey,
        fields=_UX_JOURNEY_FIELDS,
        relations=_UX_JOURNEY_RELATIONS,
        field_applier=_delegate_apply_field,
        relation_applier=_delegate_apply_relation,
    ),
    "ux_journey_step": DiscussionAdapter(
        target_kind="ux_journey_step",
        scope="element",
        screen_ids=_UX_DESIGN_STUDIO_AND_BLUEPRINT,
        label="ステップ",
        resolver=_resolve_ux_journey_step,
        context_provider=_context_ux_journey_step,
        route_params=_route_params_ux_journey_step,
        fields=_UX_JOURNEY_STEP_FIELDS,
        field_applier=_delegate_apply_field,
    ),
    "ux_requirement": DiscussionAdapter(
        target_kind="ux_requirement",
        scope="entity",
        screen_ids=("ux-design-studio",),
        label="Requirement",
        resolver=_resolve_ux_requirement,
        context_provider=_context_ux_requirement,
        route_params=_route_params_ux_requirement,
        fields=_UX_REQUIREMENT_FIELDS,
        relations=_UX_REQUIREMENT_RELATIONS,
        field_applier=_delegate_apply_field,
        relation_applier=_delegate_apply_relation,
    ),
    "solution_design": DiscussionAdapter(
        target_kind="solution_design",
        scope="entity",
        screen_ids=("ux-design-studio",),
        label="Solution Design",
        resolver=_resolve_solution_design,
        context_provider=_context_solution_design,
        route_params=_route_params_solution_design,
        fields=_SOLUTION_DESIGN_FIELDS,
        relations=_SOLUTION_DESIGN_RELATIONS,
        field_applier=_delegate_apply_field,
        relation_applier=_delegate_apply_relation,
    ),
    "blueprint_lane_cell": DiscussionAdapter(
        target_kind="blueprint_lane_cell",
        scope="element",
        screen_ids=("journey-blueprint",),
        label="レーンセル",
        resolver=_resolve_blueprint_lane_cell,
        context_provider=_context_blueprint_lane_cell,
        route_params=_route_params_blueprint_lane_cell,
        relations=_BLUEPRINT_LANE_CELL_RELATIONS,
        relation_applier=_delegate_apply_relation,
    ),
}

DISCUSSION_TARGET_KINDS: Tuple[str, ...] = tuple(DISCUSSION_ADAPTERS.keys())


def get_adapter(target_kind: str) -> Optional[DiscussionAdapter]:
    return DISCUSSION_ADAPTERS.get(target_kind)


# --- §1.3 capabilities: derived, never a stored/duplicated list --------------

DISCUSSION_CAPABILITIES: Tuple[str, ...] = (
    "read_canonical", "read_ui_draft", "propose_fields",
    "propose_relations", "prefill_form", "promote_joint_understanding",
)


def capabilities_for(adapter: DiscussionAdapter) -> Tuple[str, ...]:
    """§1.3's derivation table. Never read from a stored column or a second
    constant -- an adapter's capability set is a pure function of what it
    actually declares, so a capability can never drift from the adapter that
    is supposed to back it (the same discipline #337/#338/#349 apply to
    lifecycle values)."""
    caps: list[str] = []
    if adapter.context_provider is not None:
        caps.append("read_canonical")
    if adapter.ui_draft_forms:
        caps.append("read_ui_draft")
    can_propose_fields = bool(adapter.fields) or bool(adapter.children)
    can_propose_relations = bool(adapter.relations)
    if can_propose_fields:
        caps.append("propose_fields")
    if can_propose_relations:
        caps.append("propose_relations")
    if adapter.ui_draft_forms and (can_propose_fields or can_propose_relations):
        caps.append("prefill_form")
    if adapter.joint_understanding_bridge:
        caps.append("promote_joint_understanding")
    return tuple(caps)
