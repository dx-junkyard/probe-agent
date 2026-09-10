"""The single DiscussionAdapter registry (Issue #444, Epic #443 Phase 1).

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §1 is the canonical contract. Before this
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

`ChildSpec` and `UiDraftFormSpec` were declared in Phase 1 with every
adapter's `children` and `ui_draft_forms` left as empty tuples and
`joint_understanding_bridge` left `False`. This is what let Phase 2 (#445 UI
draft) populate `ui_draft_forms` for the four kinds whose Dashboard forms
exist today (`ux_journey` / `ux_journey_step` / `ux_requirement` /
`solution_design`) ADDITIVELY -- filling in a field on an existing adapter --
rather than another schema change that touches every call site again.
Phase 3 (#446/#452 prefill) wired the first real `prefill_handler_id`
(`ux_requirement`); Phase 5 (#448/#454) populated the FIRST `ChildSpec`
(`ux_requirement`'s `acceptance_criterion`) and `fields`/`relations` for
`product_objective`/`product_milestone`/`product_gap`/`product_feature`.
Phase 6 (#449 Joint Understanding bridge) is still ahead of this module as
written.

Import direction (this is what avoids a circular import): this module has
NO top-level dependency on `assistant_discussion.py` or
`assistant_discussion_proposal.py` -- it is a leaf. Those two modules import
this one instead. The one place this module needs `assistant_discussion_
proposal`'s `_apply_field` / `_apply_relation` (the field/relation applier
dispatchers reached through the registered delegates) is deferred
to CALL TIME via a local import inside `_delegate_apply_field` /
`_delegate_apply_relation` -- by the time those run, both modules have long
finished loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from .db import get_conn

# --- §1.1: the discussion-enabled screens ---------------------------------
# Moved here (not duplicated) because the `screen` adapter's own `screen_ids`
# is exactly this tuple -- every discussion-enabled screen has a "whole
# screen" conversation. `assistant_discussion.py` re-exports this name so
# existing importers are unaffected.
#
# Issue #453 (Epic #443 Phase 4, §4.1's Decisions) adds three more:
# `objective-map` / `stakeholder-value-network` / `capability-map`. Gap
# Workbench is a VIEW inside `objective-map` (`?view=gaps`), never a screen
# of its own (docs/01-specifications/capabilities/ai-discussion-adapter.md §4.1). `capability-map` carries no
# entity/element target from this Issue -- none of the 8 new kinds render
# there (the old #56/#57 AST-derived Capability Hierarchy this screen shows
# is a different model entirely) -- so it only gains the whole-screen
# conversation the `screen` adapter already provides to every member of this
# tuple, which is what "Capability Map を対象とする" (the Decisions) means
# here.
DISCUSSION_SCREEN_IDS: Tuple[str, ...] = (
    "overview", "interview", "ux-design-studio", "journey-blueprint",
    "objective-map", "stakeholder-value-network", "capability-map",
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
    """§5.1 (Issue #448, populated by #454). A nested/list collection a
    proposal item may target (e.g. a Requirement's Acceptance Criteria).

    `context_list_key` (added by #454) names the key under this adapter's
    `context_provider` result that carries the CURRENT list of this child
    kind's rows (each a dict containing at least `key_field`) -- this is
    what lets both generation-time (`assistant_discussion_proposal.
    generate_proposal`) and a future re-check validate an `update`/`remove`
    proposal's `child_key` against a REAL existing key rather than trusting
    the model. It is never used for `add`: an add's key is server-reserved
    (see `assistant_discussion_proposal.create_proposal`'s
    `_reserve_child_key`), not looked up here.
    """

    child_kind: str
    key_field: str
    order_field: str
    fields: Tuple[str, ...] = ()
    context_list_key: str = ""


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
    # Issue #455 (§6.2): whether a hypothesis raised in a discussion on this
    # kind may be promoted into a Joint Understanding session. `True` for
    # every kind -- `POST /assistant/discussion-proposals/{id}/hypotheses/
    # {hid}/promote` does not special-case `target_kind` at all; the bridge
    # opens an `owner_scope='discussion'` session for ANY discussion target,
    # regardless of what canonical facts (if any) that target has.
    joint_understanding_bridge: bool = True
    # Issue #456: the versioned id of the IMPLEMENTATION handler that can
    # actually carry out a prefill dispatch for this kind's `ui_draft_forms`
    # (navigate -> mount -> deliver -> ack, #452's job). `None` means "no
    # handler is wired yet" -- declaring a `UiDraftFormSpec` only says a
    # destination form EXISTS, not that anything can deliver to it. This is
    # the server's own half of the parity `tests/test_discussion_contract_
    # parity.py` checks against the Dashboard's `prefillHandlerId` -- the
    # capability boolean below is derived from THIS field alone, never from
    # anything a client claims at request time (§1.3: "client の自己申告
    # だけでは有効化しない").
    prefill_handler_id: Optional[str] = None


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


# --- Issue #453 (Epic #443 Phase 4, #447's follow-up): Vision-to-Feature ------
# `docs/01-specifications/capabilities/ai-discussion-adapter.md` §4.1's table. Every resolver below reads an
# EXISTING owning module's canonical read path -- never a duplicated query or
# a new id scheme (§4.2's "既存の canonical service / projection を読む").
# None of the eight raises: an exception or a not-found row both degrade to
# `resolution="unresolved"`, exactly like every resolver above.


def _resolve_purpose_element(system_id: int, target_ref: str) -> ResolvedTarget:
    """`app/purpose_chain.py` owns this kind (§4.1). The element id itself is
    `purpose_chain`'s own stable id (a frame-slot kind name, or
    `_hashed_element_id` for a repeating kind like `core_capability`) -- this
    resolver invents no id of its own, and its digest is `purpose_chain.
    element_digest` verbatim, never a local re-derivation."""
    from . import purpose_chain

    with get_conn() as conn:
        try:
            chain = purpose_chain.derive_purpose_chain(conn, system_id, None)
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")
    element = next((e for e in chain.elements if e.id == target_ref), None)
    if element is None:
        return ResolvedTarget("", None, "", "unresolved")
    title = element.display_statement or element.statement or element.id
    return ResolvedTarget(
        title=title,
        revision_id=chain.understanding_revision_id,
        digest=purpose_chain.element_digest(element),
        resolution="resolved",
    )


def _purpose_relation_digest(relation: Any) -> str:
    """A normalized digest over a Purpose Chain RELATION's meaning-bearing
    fields -- `purpose_chain` exposes `element_digest` for elements but no
    relation-level digest of its own. Identical in shape to
    `stakeholder_network._purpose_relation_digest` /
    `ux_design._purpose_relation_digest` (the same established pattern kept
    local rather than cross-imported, per those modules' own docstrings) --
    not a fourth, differently-shaped digest algorithm."""
    return _canonical_digest(
        {
            "kind": relation.kind,
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "status": relation.status,
            "provenance": relation.provenance,
        }
    )


def _resolve_purpose_relation(system_id: int, target_ref: str) -> ResolvedTarget:
    """`app/purpose_chain.py` owns this kind (§4.1). `target_ref` is
    `purpose_chain.relation_id(kind, source_id, target_id)` -- also not a new
    id scheme."""
    from . import purpose_chain

    with get_conn() as conn:
        try:
            chain = purpose_chain.derive_purpose_chain(conn, system_id, None)
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")
    relation = next((r for r in chain.relations if r.id == target_ref), None)
    if relation is None:
        return ResolvedTarget("", None, "", "unresolved")
    return ResolvedTarget(
        title=target_ref,
        revision_id=chain.understanding_revision_id,
        digest=_purpose_relation_digest(relation),
        resolution="resolved",
    )


def _resolve_stakeholder(system_id: int, target_ref: str) -> ResolvedTarget:
    """`app/stakeholder_network.py` owns this kind (§4.1): `stakeholder_key`
    + its current revision's `content_digest`, resolved through that
    module's own `_resolve_target` (the same private-helper reuse
    `_resolve_ux_journey_step` already establishes for `ux_design`) rather
    than a duplicated query."""
    from . import stakeholder_network as sn

    with get_conn() as conn:
        try:
            resolved = sn._resolve_target(conn, system_id, "stakeholder", target_ref)  # noqa: SLF001
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")
        if resolved["resolution"] != "resolved":
            return ResolvedTarget("", None, "", "unresolved")
        row = conn.execute(
            "SELECT current_revision_id FROM stakeholder WHERE system_id = ? AND stakeholder_key = ?",
            (system_id, target_ref),
        ).fetchone()
    return ResolvedTarget(
        title=resolved.get("name") or target_ref,
        revision_id=row["current_revision_id"] if row is not None else None,
        digest=resolved.get("digest") or "",
        resolution="resolved",
    )


def _resolve_stakeholder_need(system_id: int, target_ref: str) -> ResolvedTarget:
    """`app/stakeholder_network.py` owns this kind (§4.1): `need_key` + its
    current revision's `content_digest`."""
    from . import stakeholder_network as sn

    with get_conn() as conn:
        try:
            resolved = sn._resolve_target(conn, system_id, "stakeholder_need", target_ref)  # noqa: SLF001
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")
        if resolved["resolution"] != "resolved":
            return ResolvedTarget("", None, "", "unresolved")
        row = conn.execute(
            "SELECT current_revision_id FROM stakeholder_need WHERE system_id = ? AND need_key = ?",
            (system_id, target_ref),
        ).fetchone()
    return ResolvedTarget(
        title=resolved.get("name") or target_ref,
        revision_id=row["current_revision_id"] if row is not None else None,
        digest=resolved.get("digest") or "",
        resolution="resolved",
    )


def _resolve_product_objective(system_id: int, target_ref: str) -> ResolvedTarget:
    """`app/product_objective.py` owns this kind (§4.1): `objective_key` +
    `_objective_current_digest` (the table's own named digest source)."""
    from . import product_objective

    with get_conn() as conn:
        try:
            detail = product_objective.get_objective_detail(conn, system_id, target_ref)
        except product_objective.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")
    revision = detail.get("current_revision")
    digest = revision["content_digest"] if revision else ""
    return ResolvedTarget(
        title=detail.get("title") or target_ref,
        revision_id=detail.get("current_revision_id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_product_milestone(system_id: int, target_ref: str) -> ResolvedTarget:
    """`app/product_objective.py` owns this kind (§4.1): `milestone_key` +
    `_milestone_current_digest`."""
    from . import product_objective

    with get_conn() as conn:
        try:
            detail = product_objective.get_milestone_detail(conn, system_id, target_ref)
        except product_objective.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")
    revision = detail.get("current_revision")
    digest = revision["content_digest"] if revision else ""
    return ResolvedTarget(
        title=detail.get("title") or target_ref,
        revision_id=detail.get("current_revision_id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_product_gap(system_id: int, target_ref: str) -> ResolvedTarget:
    """`app/product_objective.py` owns this kind (§4.1): `gap_key` +
    `_gap_current_digest` -- the Gap's EFFECTIVE digest, not the raw revision
    digest, so an `inherited_from_milestone` Gap goes `stale` when the
    Milestone's own target moves even though the Gap's own revision row did
    not change (`product_objective._gap_current_digest`'s own docstring).
    This is exactly why the table names this function rather than "the
    current revision's content_digest" the way `product_objective` /
    `product_milestone` above read it."""
    from . import product_objective

    with get_conn() as conn:
        try:
            detail = product_objective.get_gap_detail(conn, system_id, target_ref)
        except product_objective.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")
        gap_row = product_objective._get_gap_row(conn, system_id, target_ref)  # noqa: SLF001
        digest = product_objective._gap_current_digest(conn, gap_row) if gap_row is not None else ""  # noqa: SLF001
    return ResolvedTarget(
        title=detail.get("title") or target_ref,
        revision_id=detail.get("current_revision_id"),
        digest=digest,
        resolution="resolved",
    )


def _resolve_product_feature(system_id: int, target_ref: str) -> ResolvedTarget:
    """`app/product_feature.py` owns this kind (§4.1): `feature_key` + its
    current revision's `content_digest`."""
    from . import product_feature

    with get_conn() as conn:
        try:
            detail = product_feature.get_feature_detail(conn, system_id, target_ref)
        except product_feature.NotFound:
            return ResolvedTarget("", None, "", "unresolved")
        except Exception:  # pragma: no cover - defensive
            return ResolvedTarget("", None, "", "unresolved")
    revision = detail.get("current_revision")
    digest = revision["content_digest"] if revision else ""
    return ResolvedTarget(
        title=detail.get("title") or target_ref,
        revision_id=detail.get("current_revision_id"),
        digest=digest,
        resolution="resolved",
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


# Issue #453: none of the 8 new kinds route-param-inject into an existing
# SCREEN-level context provider the way `ux_journey`'s `{"journey": ...}`
# does -- their own `context_provider` below (via `gather_context`) is what
# supplies THEIR facts to a thread opened on them, and `objective-map` /
# `stakeholder-value-network`'s own screen-level context providers
# (`assistant_discussion_context.py`) read the SAME `objective`/`milestone`/
# `gap`/`node`/`edge` params directly from the URL rather than through this
# indirection. Matches `understanding_claim`/`overview_finding`'s existing
# `{}` precedent exactly.
def _route_params_empty(target_ref: str) -> Dict[str, str]:
    return {}


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

# Issue #454 (Epic #443 §5.1): the ONE ChildSpec this Issue wires end to
# end. `fields` is a strict SUBSET of `ux_design._CRITERION_KEYS` /
# `assistant_discussion_proposal._CRITERION_KEYS` -- `criterion_key` and
# `criterion_order` are addressed through `child_key`/`child_order`, never
# through this tuple (§5.1's own table: "key" and "order" are structural
# columns on the proposal item, not proposable content).
_UX_REQUIREMENT_ACCEPTANCE_CRITERION_FIELDS: Tuple[str, ...] = (
    "statement", "verification_method", "verification_note",
)
_UX_REQUIREMENT_CHILDREN: Tuple[ChildSpec, ...] = (
    ChildSpec(
        child_kind="acceptance_criterion",
        key_field="criterion_key",
        order_field="criterion_order",
        fields=_UX_REQUIREMENT_ACCEPTANCE_CRITERION_FIELDS,
        context_list_key="acceptance_criteria",
    ),
)

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
# (never auto-confirmed, §2.2 of docs/01-specifications/capabilities/assistant-discussion.md).
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
    facts = {k: rev.get(k, "") for k in _UX_REQUIREMENT_FIELDS}
    # Issue #454: the CURRENT Acceptance Criteria list, keyed the same way
    # `ChildSpec(child_kind="acceptance_criterion").context_list_key` names
    # it -- this is what lets generation-time validation check an
    # `update`/`remove` proposal's `child_key` against a real existing key
    # (§5.4), and what lets the model see existing criteria to correct
    # rather than blindly re-propose.
    facts["acceptance_criteria"] = [
        {
            "criterion_key": c.get("criterion_key", ""),
            "criterion_order": c.get("criterion_order", 0),
            **{k: c.get(k, "") for k in _UX_REQUIREMENT_ACCEPTANCE_CRITERION_FIELDS},
        }
        for c in rev.get("acceptance_criteria", [])
    ][:_MAX_CONTEXT_ITEMS]
    return facts


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


# --- Issue #453: context providers for the 8 new kinds ------------------------
# §4.2's "upstream/downstream link と、その未解決・stale 状態を context に載せる
# -- ただし本文をコピーせず参照と digest で持つ": each provider below returns
# the TARGET's own content (that IS what a discussion about it needs), plus
# related entities as (kind, ref, resolution/status) references only -- never
# a second copy of that related entity's own text.

#: Mirrors `assistant_discussion_context.MAX_LIST_ITEMS` (kept local so this
#: module has no import-time dependency on that one, matching the existing
#: import-direction rule this module's own docstring documents).
_MAX_CONTEXT_ITEMS = 50


def _context_purpose_element(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import purpose_chain

    chain = purpose_chain.derive_purpose_chain(conn, system_id, None)
    element = next((e for e in chain.elements if e.id == target_ref), None)
    if element is None:
        return {}
    related_relations = [
        {
            "id": r.id, "kind": r.kind, "source_id": r.source_id, "target_id": r.target_id,
            "status": r.status, "recheck_state": r.recheck_state,
        }
        for r in chain.relations
        if r.source_id == target_ref or r.target_id == target_ref
    ][:_MAX_CONTEXT_ITEMS]
    return {
        "kind": element.kind,
        "state": element.state,
        "display_statement": element.display_statement,
        "statement": element.statement,
        "confirmation": element.confirmation,
        "provenance": element.provenance,
        "resolution_level": element.resolution_level,
        "source_kind": element.source_kind,
        "source_ids": list(element.source_ids),
        "evidence": list(element.evidence)[:_MAX_CONTEXT_ITEMS],
        "missing_information": list(element.missing_information),
        "related_relations": related_relations,
    }


def _context_purpose_relation(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import purpose_chain

    chain = purpose_chain.derive_purpose_chain(conn, system_id, None)
    relation = next((r for r in chain.relations if r.id == target_ref), None)
    if relation is None:
        return {}
    source = next((e for e in chain.elements if e.id == relation.source_id), None)
    target = next((e for e in chain.elements if e.id == relation.target_id), None)
    return {
        "kind": relation.kind,
        "status": relation.status,
        "recheck_state": relation.recheck_state,
        "stale_reason": relation.stale_reason,
        "provenance": relation.provenance,
        "rationale": relation.rationale,
        "source_id": relation.source_id,
        "source_state": source.state if source is not None else "unknown",
        "target_id": relation.target_id,
        "target_state": target.state if target is not None else "unknown",
    }


def _context_stakeholder(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import stakeholder_network as sn

    detail = sn.get_stakeholder_detail(conn, system_id, target_ref)
    revision = detail.get("current_revision") or {}
    refs_result = sn.list_refs(conn, system_id, source_kind="stakeholder", source_key=target_ref)
    return {
        "display_name": revision.get("display_name", ""),
        "stakeholder_kind": revision.get("stakeholder_kind", ""),
        "description": revision.get("description", ""),
        "context_note": revision.get("context_note", ""),
        "design_status": detail.get("design_status"),
        "recheck_state": detail.get("recheck_state"),
        "roles": [
            {"role": r.get("role"), "scope_kind": r.get("scope_kind"), "scope_ref": r.get("scope_ref")}
            for r in detail.get("roles", [])
        ][:_MAX_CONTEXT_ITEMS],
        "refs": [
            {
                "ref_kind": r.get("ref_kind"), "target_ref": r.get("target_ref"),
                "target_resolution": r.get("target_resolution"), "recheck_state": r.get("recheck_state"),
            }
            for r in refs_result.get("refs", [])
        ][:_MAX_CONTEXT_ITEMS],
    }


def _context_stakeholder_need(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import stakeholder_network as sn

    detail = sn.get_need_detail(conn, system_id, target_ref)
    revision = detail.get("current_revision") or {}
    refs_result = sn.list_refs(conn, system_id, source_kind="stakeholder_need", source_key=target_ref)
    return {
        "statement": revision.get("statement", ""),
        "need_kind": revision.get("need_kind", ""),
        "rationale": revision.get("rationale", ""),
        "stakeholder_key": detail.get("stakeholder_key"),
        "design_status": detail.get("design_status"),
        "recheck_state": detail.get("recheck_state"),
        "refs": [
            {
                "ref_kind": r.get("ref_kind"), "target_ref": r.get("target_ref"),
                "target_resolution": r.get("target_resolution"), "recheck_state": r.get("recheck_state"),
            }
            for r in refs_result.get("refs", [])
        ][:_MAX_CONTEXT_ITEMS],
    }


# --- Issue #454 (Epic #443 §5.2): Objective/Milestone/Gap/Feature content --
# `docs/01-specifications/capabilities/ai-discussion-adapter.md` §5.2's rule, applied here: `priority_band` /
# `achievement` / `lifecycle` / `design_status` / `option_status` /
# `resolved` / `adopted` -- every human decision-ledger axis these four
# kinds carry -- are STRUCTURALLY absent from every tuple below. Not
# filtered out at apply time; never written into the tuple in the first
# place, so there is no `field_name`/`relation_kind` a model or a prefill
# dispatch could ever address them through (the same "registering nothing
# is the enforcement" discipline #427 applies to Gap's own severity column).
#
# Every tuple is a strict subset of its domain revision function's own
# keyword parameters (verified by `tests/test_discussion_adapter_registry.py`,
# the same registry-drift protection `TestRegistryCorrespondence` already
# gives `ux_journey`/`ux_requirement`/`solution_design`).
_PRODUCT_OBJECTIVE_FIELDS: Tuple[str, ...] = ("title", "intent", "contribution", "scope_note", "summary")
_PRODUCT_OBJECTIVE_RELATIONS: Tuple[str, ...] = ("upstream_ref",)

_PRODUCT_MILESTONE_FIELDS: Tuple[str, ...] = (
    "title", "target_state", "verification_method", "verification_note", "summary",
)
#: A milestone dependency is an ORDERING relationship (§4.4 of
#: `docs/01-specifications/product/product-objective-lineage.md`), never an achievement gate -- proposing/
#: applying one through this registry moves nothing on `achievement`.
_PRODUCT_MILESTONE_RELATIONS: Tuple[str, ...] = ("milestone_dependency",)

_PRODUCT_GAP_FIELDS: Tuple[str, ...] = (
    "title", "current_state", "target_state", "interpretation", "suggested_priority_note",
)

_PRODUCT_FEATURE_FIELDS: Tuple[str, ...] = ("title", "statement", "rationale", "scope_note", "summary")
#: The three link kinds §4.1's "Requirement/Capability/Solution/Flow/
#: Component link" decision names -- `target_link`'s own `relation_target_
#: kind` carries WHICH of Solution/Flow/Component (any
#: `product_feature.TARGET_LINK_KINDS` member; the domain function's own
#: `_check_membership` is the finite gate, never re-narrowed here).
_PRODUCT_FEATURE_RELATIONS: Tuple[str, ...] = ("requirement_link", "capability_link", "target_link")


def _context_product_objective(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import product_objective

    detail = product_objective.get_objective_detail(conn, system_id, target_ref)
    revision = detail.get("current_revision") or {}
    return {
        "title": detail.get("title", ""),
        "intent": revision.get("intent", ""),
        "contribution": revision.get("contribution", ""),
        "scope_note": revision.get("scope_note", ""),
        "summary": revision.get("summary", ""),
        "objective_state": detail.get("objective_state"),
        "recheck_state": detail.get("recheck_state"),
        "parent_objective_key": detail.get("parent_objective_key"),
        "upstream_refs": [
            {
                "ref_kind": r.get("ref_kind"), "target_ref": r.get("target_ref"),
                "target_resolution": r.get("target_resolution"), "recheck_state": r.get("recheck_state"),
            }
            for r in detail.get("upstream_refs", [])
        ][:_MAX_CONTEXT_ITEMS],
    }


def _context_product_milestone(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import product_objective

    detail = product_objective.get_milestone_detail(conn, system_id, target_ref)
    revision = detail.get("current_revision") or {}
    return {
        "title": detail.get("title", ""),
        "objective_key": detail.get("objective_key"),
        "design_status": detail.get("design_status"),
        "achievement": detail.get("achievement"),
        "recheck_state": detail.get("recheck_state"),
        "target_state": revision.get("target_state", ""),
        "verification_method": revision.get("verification_method", ""),
        "verification_note": revision.get("verification_note", ""),
        "summary": revision.get("summary", ""),
        "dependencies": [
            {"depends_on_milestone_key": d.get("depends_on_milestone_key")}
            for d in detail.get("dependencies", [])
        ][:_MAX_CONTEXT_ITEMS],
    }


def _context_product_gap(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import product_objective

    detail = product_objective.get_gap_detail(conn, system_id, target_ref)
    revision = detail.get("current_revision") or {}
    return {
        "title": detail.get("title", ""),
        "lifecycle": detail.get("lifecycle"),
        "priority_band": detail.get("priority_band"),
        "recheck_state": detail.get("recheck_state"),
        "current_state": revision.get("current_state", ""),
        "interpretation": revision.get("interpretation", ""),
        "suggested_priority_note": revision.get("suggested_priority_note", ""),
        "effective_target_state": detail.get("effective_target_state"),
        "effective_target_availability": detail.get("effective_target_availability"),
        "milestone_key": detail.get("milestone_key"),
        "source_refs": [
            {"source_kind": r.get("source_kind"), "source_state": r.get("source_state")}
            for r in detail.get("source_refs", [])
        ][:_MAX_CONTEXT_ITEMS],
        "journey_links": [
            {"journey_key": j.get("journey_key")} for j in detail.get("journey_links", [])
        ][:_MAX_CONTEXT_ITEMS],
        "evidence_refs": [
            {"evidence_kind": e.get("evidence_kind"), "deep_link_state": e.get("deep_link_state")}
            for e in detail.get("evidence_refs", [])
        ][:_MAX_CONTEXT_ITEMS],
        "artifact_links": [
            {"link_kind": a.get("link_kind"), "deep_link_state": a.get("deep_link_state")}
            for a in detail.get("artifact_links", [])
        ][:_MAX_CONTEXT_ITEMS],
    }


def _context_product_feature(conn: Any, system_id: int, target_ref: str) -> Dict[str, Any]:
    from . import product_feature

    detail = product_feature.get_feature_detail(conn, system_id, target_ref)
    revision = detail.get("current_revision") or {}
    return {
        "title": detail.get("title", ""),
        "statement": revision.get("statement", ""),
        "rationale": revision.get("rationale", ""),
        "scope_note": revision.get("scope_note", ""),
        "summary": revision.get("summary", ""),
        "design_status": detail.get("design_status"),
        "recheck_state": detail.get("recheck_state"),
        "requirement_links": [
            {"requirement_key": r.get("requirement_key"), "target_resolution": r.get("target_resolution")}
            for r in detail.get("requirement_links", [])
        ][:_MAX_CONTEXT_ITEMS],
        "capability_links": [
            {"capability_entity_id": c.get("capability_entity_id"), "capability_name": c.get("capability_name")}
            for c in detail.get("capability_links", [])
        ][:_MAX_CONTEXT_ITEMS],
        "target_links": [
            {
                "link_kind": t.get("link_kind"), "target_ref": t.get("target_ref"),
                "target_resolution": t.get("target_resolution"),
            }
            for t in detail.get("target_links", [])
        ][:_MAX_CONTEXT_ITEMS],
    }


# --- field/relation appliers: deferred-import delegates -----------------------
# `apply_items` in `assistant_discussion_proposal.py` calls these registered
# delegates after checking every selected item against the current registry.
# The existing `_apply_*` helpers keep their domain-service dispatch logic;
# the registry selects which handler is permitted for each kind. These
# delegates ensure the registry
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
        # Issue #455: the one kind whose JU bridge is deliberately `False`.
        # `app/discussion_hypothesis._target_digest_with_conn` cannot verify
        # this kind's root content from an already-open connection
        # (`_resolve_overview_finding` calls `overview_projection.
        # build_overview(system_id)`, which is not conn-parametrized and
        # re-derives a whole System-wide projection) -- declaring the
        # capability `True` here without a working premise check would be
        # exactly the "declares supported, cannot actually back it" defect
        # #456 exists to prevent, one layer further out.
        joint_understanding_bridge=False,
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
        # Issue #445: the SAME field tuple as `fields` above, on purpose --
        # `components/ux-design/journey-panel.tsx`'s `JourneyRevisionForm`
        # binds these exact names to `ux_design.add_journey_revision`'s own
        # keyword params, which is what lets a future #446 prefill land
        # without a name translation layer.
        ui_draft_forms=(UiDraftFormSpec(form_id="ux_journey.revision", fields=_UX_JOURNEY_FIELDS),),
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
        # Issue #445: the step rows inside the SAME `JourneyRevisionForm` --
        # a different `target_kind` (and so a different discussion thread)
        # from `ux_journey` above, but one physical form on the Dashboard.
        ui_draft_forms=(UiDraftFormSpec(form_id="ux_journey_step.revision", fields=_UX_JOURNEY_STEP_FIELDS),),
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
        # Issue #454: Acceptance Criteria are addressed as a ChildSpec, not
        # a top-level field -- this is what makes `add`/`update`/`remove`
        # and a reorder-only change distinct, individually selectable
        # proposal items (§5.1) instead of one opaque "acceptance_criteria"
        # blob field.
        children=_UX_REQUIREMENT_CHILDREN,
        field_applier=_delegate_apply_field,
        relation_applier=_delegate_apply_relation,
        # Issue #445: `components/ux-design/requirement-panel.tsx`'s
        # `RequirementRevisionForm`. Acceptance criteria stay OUT of this
        # form's `fields` allowlist (prefill dispatch is #446/#452 scope,
        # not extended to children by this Issue) -- they are proposable
        # (§5.1's ChildSpec) but not yet prefillable.
        ui_draft_forms=(UiDraftFormSpec(form_id="ux_requirement.revision", fields=_UX_REQUIREMENT_FIELDS),),
        # Issue #452: the FIRST real prefill delivery handler wired end to
        # end (navigate -> mount -> deliver -> ack) -- `ux_requirement` is
        # the representative target the Issue's Decisions name ("既存
        # Requirementから一往復を完成させる"). This is the ONLY adapter whose
        # `prefill_form` capability derives `true` today; every other kind's
        # `ui_draft_forms` still declares a destination form with nothing
        # that can deliver to it (`#454` extends the same handler shape to
        # the remaining three). `tests/test_discussion_contract_parity.py`'s
        # `test_prefill_handler_id_parity_between_server_and_dashboard`
        # pins this exact id against `lib/discussion-adapters.ts`'s matching
        # `prefillHandlerId` -- a mismatch (or a one-sided change) fails that
        # test, never silently shipping a half-wired capability (§1.3: "client
        # の自己申告だけでは有効化しない").
        prefill_handler_id="ux_requirement.revision@v1",
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
        # Issue #445: `components/ux-design/solution-design-panel.tsx`'s
        # `AddOptionForm` -- it drafts a NEW option, so `selected_item_ref`
        # (the in-progress `option_key`) carries the identity, not a field.
        ui_draft_forms=(UiDraftFormSpec(form_id="solution_design.option", fields=_SOLUTION_DESIGN_FIELDS),),
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
    # --- Issue #453 (Epic #443 Phase 4, #447's follow-up): Vision-to-Feature.
    # §4.1's table. No `fields`/`relations`/appliers on any of the eight --
    # generation/confirmation for Objective/UX/Feature content is explicitly
    # out of this Issue's scope (its own Scope/Ownership section); each
    # adapter here carries read-only canonical context plus the direct
    # up/downstream refs its own `get_*_detail` already resolves.
    "purpose_element": DiscussionAdapter(
        target_kind="purpose_element",
        scope="element",
        # Rendered today by `PurposeFrameCard` (Overview) and
        # `PurposeFramePanel` (Interview) -- the exact reachability note
        # `understanding_claim` above already documents for the same pair of
        # screens.
        screen_ids=("overview", "interview"),
        label="Purpose 要素",
        resolver=_resolve_purpose_element,
        context_provider=_context_purpose_element,
        route_params=_route_params_empty,
    ),
    "purpose_relation": DiscussionAdapter(
        target_kind="purpose_relation",
        scope="element",
        screen_ids=("overview", "interview"),
        label="Purpose 関係",
        resolver=_resolve_purpose_relation,
        context_provider=_context_purpose_relation,
        route_params=_route_params_empty,
    ),
    "stakeholder": DiscussionAdapter(
        target_kind="stakeholder",
        scope="entity",
        screen_ids=("stakeholder-value-network",),
        label="Stakeholder",
        resolver=_resolve_stakeholder,
        context_provider=_context_stakeholder,
        route_params=_route_params_empty,
    ),
    "stakeholder_need": DiscussionAdapter(
        target_kind="stakeholder_need",
        scope="entity",
        screen_ids=("stakeholder-value-network",),
        label="Need",
        resolver=_resolve_stakeholder_need,
        context_provider=_context_stakeholder_need,
        route_params=_route_params_empty,
    ),
    "product_objective": DiscussionAdapter(
        target_kind="product_objective",
        scope="entity",
        # Gap Workbench is a VIEW inside `objective-map` (`?view=gaps`), not
        # a second screen (§4.1) -- so all three Objective/Milestone/Gap
        # kinds share this one screen_id.
        screen_ids=("objective-map",),
        label="Objective",
        resolver=_resolve_product_objective,
        context_provider=_context_product_objective,
        route_params=_route_params_empty,
        # Issue #454: content is proposable; `objective_state` (the
        # confirm/decision axis §4.3 owns) is not in this tuple and never
        # will be (§5.2).
        fields=_PRODUCT_OBJECTIVE_FIELDS,
        relations=_PRODUCT_OBJECTIVE_RELATIONS,
        field_applier=_delegate_apply_field,
        relation_applier=_delegate_apply_relation,
    ),
    "product_milestone": DiscussionAdapter(
        target_kind="product_milestone",
        scope="entity",
        screen_ids=("objective-map",),
        label="Milestone",
        resolver=_resolve_product_milestone,
        context_provider=_context_product_milestone,
        route_params=_route_params_empty,
        # Issue #454: `achievement` (the assessment axis) is absent (§5.2).
        fields=_PRODUCT_MILESTONE_FIELDS,
        relations=_PRODUCT_MILESTONE_RELATIONS,
        field_applier=_delegate_apply_field,
        relation_applier=_delegate_apply_relation,
    ),
    "product_gap": DiscussionAdapter(
        target_kind="product_gap",
        scope="entity",
        screen_ids=("objective-map",),
        label="Gap",
        resolver=_resolve_product_gap,
        context_provider=_context_product_gap,
        route_params=_route_params_empty,
        # Issue #454: `lifecycle`/`priority_band` (the decision axes §5.6/
        # §5.7 own) are absent (§5.2). Gap's detection-derived reference
        # lists (`source_refs`/`evidence_refs`/`artifact_links`) stay
        # read-only context here -- their own finite per-list vocabularies
        # are this Issue's explicitly deferred scope, not a #454 relation.
        fields=_PRODUCT_GAP_FIELDS,
        field_applier=_delegate_apply_field,
    ),
    "product_feature": DiscussionAdapter(
        target_kind="product_feature",
        scope="entity",
        # Referenced (read-only link lists) from both screens today --
        # `requirement-panel.tsx` (ux-design-studio) and
        # `gap-workbench-panel.tsx` (objective-map) -- neither of which has a
        # `feature` selection route param of its own yet, so this kind's
        # Dashboard `resolveFromRoute` stays `null` like `understanding_claim`
        # / `overview_finding` (no live-selection UI is added by this Issue).
        screen_ids=("ux-design-studio", "objective-map"),
        label="Feature",
        resolver=_resolve_product_feature,
        context_provider=_context_product_feature,
        route_params=_route_params_empty,
        fields=_PRODUCT_FEATURE_FIELDS,
        relations=_PRODUCT_FEATURE_RELATIONS,
        field_applier=_delegate_apply_field,
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
    # Issue #456: a declared `ui_draft_forms` entry only says a destination
    # form EXISTS -- it is not proof anything can deliver to it. `prefill_
    # form` additionally requires a registered `prefill_handler_id`, which is
    # `None` for every kind as of this Issue (see the field's own docstring):
    # the correct, honest state today is that NO adapter derives this
    # capability true yet. #452 wires the first real handler; from that
    # point on this same rule flips it true for that one kind, with no
    # further change here.
    if (
        adapter.ui_draft_forms
        and (can_propose_fields or can_propose_relations)
        and adapter.prefill_handler_id is not None
    ):
        caps.append("prefill_form")
    if adapter.joint_understanding_bridge:
        caps.append("promote_joint_understanding")
    return tuple(caps)


# --- §9's operation-result contract (Issue #456) ------------------------------
# `docs/01-specifications/capabilities/ai-discussion-adapter.md` §1.3/§1.7's target: every discussion-adapter
# READ operation reports a `DiscussionOperationResult` (`app/models.py`)
# ALONGSIDE its own facts, never folded into them and never merged with
# `DiscussionTargetState` (freshness). The two functions below are the first
# two operations this applies to; `evaluate_item_eligibility` (proposal
# apply/prefill gating) keeps its own pre-existing, separately tested
# `forbidden` / `stale` / `conflict` / `appliable` vocabulary unchanged --
# that is a DIFFERENT question ("can THIS item be written") from "did the
# read succeed", and folding the two would erase the distinction #456 exists
# to keep.


@dataclass(frozen=True)
class TargetContextResult:
    """The result of gathering a target's canonical context for a prompt.
    `operation_state` and `facts` are deliberately separate fields -- an
    `unavailable` read and a genuinely empty-but-successful read must never
    look the same to a caller that only inspects `facts`."""

    operation_state: str  # DiscussionOperationResult
    facts: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def gather_context(conn: Any, system_id: int, target_kind: str, target_ref: str) -> TargetContextResult:
    """Replaces the old "adapter missing / no provider / provider raised ->
    all collapse to `{}`" behaviour with three DISTINCT, honestly labelled
    outcomes (§1.3's assignment rule):

    - no adapter registered for `target_kind` (defense in depth -- a live
      thread's own `target_kind` was validated at creation, but the registry
      can be narrowed in between, exactly as `tests/test_assistant_discussion_
      proposals.py`'s `test_a_narrowed_registry_forbids_an_already_stored_
      item` already exercises for eligibility) -> `unsupported`.
    - the adapter is registered but declares no `context_provider` at all
      (`screen` / `interview_session` / `overview_finding` today) ->
      `unsupported`. This is a structural gap in the CURRENT catalog, not a
      permanent architectural impossibility -- a future phase could add one.
    - the registered provider raises -> `unavailable`. The read was
      attempted and failed; the caller should not treat the empty result as
      "there is nothing to say" the way `unsupported` means.
    - the registered provider succeeds -> `available`, with its facts.

    A deleted/inaccessible target is NOT reclassified into this vocabulary
    (§1.3's own rule): a provider reading a gone target typically raises, so
    it degrades to `unavailable` here -- exactly the existing resolver/404
    contract's own territory, not a new fourth meaning.
    """
    adapter = DISCUSSION_ADAPTERS.get(target_kind)
    if adapter is None:
        return TargetContextResult(
            operation_state="unsupported", facts={}, reason="discussion_target_kind_unregistered",
        )
    if adapter.context_provider is None:
        return TargetContextResult(
            operation_state="unsupported", facts={}, reason="discussion_context_provider_not_registered",
        )
    try:
        facts = adapter.context_provider(conn, system_id, target_ref)
    except Exception:
        # Exercised directly by `tests/test_discussion_operation_result.py`'s
        # `TestGatherContextOperationStates` (a monkeypatched provider that
        # raises, a deleted target, and a foreign-System read all reach this
        # branch) -- not `pragma: no cover`.
        return TargetContextResult(
            operation_state="unavailable", facts={}, reason="discussion_context_provider_error",
        )
    return TargetContextResult(operation_state="available", facts=facts, reason="")


def resolve_prefill_operation_state(
    adapter: DiscussionAdapter, form_id: Optional[str] = None,
) -> Tuple[str, str]:
    """§1.3/§1.7's prefill readiness, as a `(DiscussionOperationResult,
    reason)` pair -- structural, static, and independent of whether any
    Dashboard form happens to be MOUNTED right now (§1.3: "mount 状態を
    capability 条件にしない"; a transient delivery failure at dispatch time
    is `unavailable`, a #452 concern this function does not decide).

    First match:
    - `adapter.scope == "screen"` -> `not_applicable`. A whole-screen
      conversation is not addressed at one entity, so it can never carry a
      single form to prefill -- true regardless of how much of #452 gets
      built later, unlike the other branches below.
    - no `ui_draft_forms` registered at all -> `unsupported` (reuses the
      existing `prefill_unsupported` code `PrefillUnsupported` already
      raises in `assistant_discussion_proposal.prefill_items`).
    - a `form_id` was given and is not one of the adapter's registered forms
      -> `not_applicable` (reuses `prefill_form_unregistered`): the adapter
      DOES support prefill in general, but this specific request addresses a
      form that structurally does not belong to this target.
    - no `prefill_handler_id` registered -> `unsupported`
      (`prefill_handler_not_registered`): the form's SHAPE is declared, but
      nothing can deliver to it yet.
    - otherwise -> `available`.
    """
    if adapter.scope == "screen":
        return "not_applicable", "discussion_prefill_not_applicable_to_screen_scope"
    if not adapter.ui_draft_forms:
        return "unsupported", "prefill_unsupported"
    if form_id is not None and not any(f.form_id == form_id for f in adapter.ui_draft_forms):
        return "not_applicable", "prefill_form_unregistered"
    if adapter.prefill_handler_id is None:
        return "unsupported", "prefill_handler_not_registered"
    return "available", ""
