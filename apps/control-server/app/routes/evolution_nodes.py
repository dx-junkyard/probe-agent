"""Evolution Node API: contract, maturity lifecycle and lineage
(Issue #396, Phase 1 of the evolution control plane Epic #394).

The canonical contract is `docs/evolutionary-pipeline.md`; the domain layer
is `app/evolution_node.py`. This module is a thin boundary over it and
deliberately owns no decision of its own:

- it never re-derives a maturity, and never pre-checks a transition and then
  trusts its own answer -- `apply_transition` re-validates server-side, so
  the route only translates the domain layer's finite `reason_code` into an
  HTTP response;
- a REJECTED transition is a 422 whose `detail.code` is that exact code, not
  a re-worded sentence. The Dashboard branches on the finite code; prose is
  for the human next to it;
- a repeated `idempotency_key` is a 200 with `duplicate: true`. A retry that
  changed nothing is a success. Returning 409 here would make every network
  retry look like a conflict the developer has to resolve.

There is no reasoning-model call anywhere on any of these paths. Phase 1 has
no LLM step at all, so the `get_conn()`-across-an-external-call hazard
(CLAUDE.md Implementation Constraints) cannot arise here -- but the
connection is still opened once per request and closed before returning, so
that stays true when later phases add one.

probe-agent:
  role: API boundary for the Evolution Node contract, maturity transitions and append-only lineage
  capability: evolution-control-plane
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify a rejected transition returns the domain layer's own finite reason code rather than a re-worded message, a repeated idempotency key is a 200 duplicate rather than a second event, every node/link/evidence stays inside one System, and the maturity / improvement_status / policy_mode axes are returned as three independent fields.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import evolution_node
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    EvolutionNodeCreateIn,
    EvolutionNodeEventOut,
    EvolutionNodeEventsOut,
    EvolutionNodeImplementationCreateIn,
    EvolutionNodeImplementationOut,
    EvolutionNodeLegacyProjectionOut,
    EvolutionNodeLinkCreateIn,
    EvolutionNodeLinkOut,
    EvolutionNodeProjectionOut,
    EvolutionNodeStablePinIn,
    EvolutionNodeSummaryOut,
    EvolutionNodeTransitionIn,
    EvolutionNodeTransitionOut,
    EvolutionNodeVersionCreateIn,
    EvolutionNodeVersionOut,
    EvolutionNodesListOut,
)

router = APIRouter(prefix="/evolution-nodes", tags=["evolution-nodes"])

# A node in another System must be indistinguishable from one that does not
# exist: `_require_node` raises NotFound for both, so the two cases can never
# be told apart by probing ids across Systems.
_ERROR_STATUS = {
    evolution_node.EvolutionNodeNotFoundError: 404,
    evolution_node.EvolutionNodeConflictError: 409,
    evolution_node.EvolutionNodeValidationError: 422,
}

_MAX_EVENT_LIMIT = 200


def _raise_domain_error(exc: evolution_node.EvolutionNodeError) -> None:
    for exc_type, status_code in _ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Row -> response helpers
# ---------------------------------------------------------------------------


def _node_summary(row) -> EvolutionNodeSummaryOut:
    return EvolutionNodeSummaryOut(
        id=row["id"],
        system_id=row["system_id"],
        node_key=row["node_key"],
        display_name=row["display_name"],
        maturity=row["maturity"],
        current_version_id=row["current_version_id"],
        current_implementation_id=row["current_implementation_id"],
        stable_implementation_id=row["stable_implementation_id"],
        rollback_implementation_id=row["rollback_implementation_id"],
        monitoring_contract_ref=row["monitoring_contract_ref"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Node lifecycle
# ---------------------------------------------------------------------------


@router.post("", response_model=EvolutionNodeSummaryOut, status_code=201)
def create_evolution_node(
    payload: EvolutionNodeCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> EvolutionNodeSummaryOut:
    with get_conn() as conn:
        try:
            row = evolution_node.create_node(
                conn,
                system_id=system_id,
                node_key=payload.node_key,
                display_name=payload.display_name,
                created_by=principal.username,
            )
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)
    return _node_summary(row)


@router.get("", response_model=EvolutionNodesListOut)
def list_evolution_nodes(
    maturity: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> EvolutionNodesListOut:
    # An unknown filter value is a 422, never a silently empty list: a typo
    # that returns "no nodes" reads as "this System has none", which is a
    # different and wrong answer.
    if maturity is not None and maturity not in evolution_node.MATURITY_STATES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unknown_maturity_filter",
                "message": (
                    "maturity must be one of: "
                    + ", ".join(evolution_node.MATURITY_STATES)
                ),
            },
        )
    with get_conn() as conn:
        rows = evolution_node.list_nodes(conn, system_id=system_id)
    summaries = [_node_summary(row) for row in rows]
    if maturity is not None:
        summaries = [node for node in summaries if node.maturity == maturity]
    return EvolutionNodesListOut(nodes=summaries)


@router.get("/{node_id}", response_model=EvolutionNodeProjectionOut)
def get_evolution_node(
    node_id: int,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> EvolutionNodeProjectionOut:
    with get_conn() as conn:
        try:
            projection = evolution_node.build_node_projection(
                conn, system_id=system_id, node_id=node_id
            )
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)
    return EvolutionNodeProjectionOut(**projection)


@router.get("/{node_id}/legacy-projection", response_model=EvolutionNodeLegacyProjectionOut)
def get_evolution_node_legacy_projection(
    node_id: int,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> EvolutionNodeLegacyProjectionOut:
    """ADR-8's compatibility seam: the Component/Cell-shaped view of a Node,
    so a consumer written against those fields keeps working during
    migration. Nothing is deleted in this Epic's early phases."""
    with get_conn() as conn:
        try:
            projection = evolution_node.build_legacy_projection(
                conn, system_id=system_id, node_id=node_id
            )
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)
    return EvolutionNodeLegacyProjectionOut(**projection)


# ---------------------------------------------------------------------------
# Contract versions and implementations (ADR-3: two separate axes)
# ---------------------------------------------------------------------------


@router.post("/{node_id}/versions", response_model=EvolutionNodeVersionOut, status_code=201)
def create_evolution_node_version(
    node_id: int,
    payload: EvolutionNodeVersionCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> EvolutionNodeVersionOut:
    with get_conn() as conn:
        try:
            row = evolution_node.add_version(
                conn,
                system_id=system_id,
                node_id=node_id,
                mission=payload.mission,
                input_contract=payload.input_contract,
                output_contract=payload.output_contract,
                side_effect_class=payload.side_effect_class,
                trust_boundary=payload.trust_boundary,
                scope=payload.scope,
                out_of_scope=payload.out_of_scope,
                establishment_criteria=payload.establishment_criteria,
                reopen_criteria=payload.reopen_criteria,
                evaluation_policy_refs=payload.evaluation_policy_refs,
                created_by=principal.username,
            )
            doc = evolution_node.version_doc(row)
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)
    return EvolutionNodeVersionOut(**doc)


@router.post(
    "/{node_id}/implementations",
    response_model=EvolutionNodeImplementationOut,
    status_code=201,
)
def create_evolution_node_implementation(
    node_id: int,
    payload: EvolutionNodeImplementationCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> EvolutionNodeImplementationOut:
    with get_conn() as conn:
        try:
            row = evolution_node.add_implementation(
                conn,
                system_id=system_id,
                node_id=node_id,
                node_version_id=payload.node_version_id,
                modality=payload.modality,
                config=payload.config,
                snapshot_id=payload.snapshot_id,
                commit_sha=payload.commit_sha,
                environment_ref=payload.environment_ref,
                provenance=payload.provenance,
                created_by=principal.username,
            )
            doc = evolution_node.implementation_doc(row)
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)
    return EvolutionNodeImplementationOut(**doc)


@router.post("/{node_id}/links", response_model=EvolutionNodeLinkOut, status_code=201)
def create_evolution_node_link(
    node_id: int,
    payload: EvolutionNodeLinkCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> EvolutionNodeLinkOut:
    with get_conn() as conn:
        try:
            row = evolution_node.add_link(
                conn,
                system_id=system_id,
                node_id=node_id,
                link_kind=payload.link_kind,
                target_ref=payload.target_ref,
                target_row_id=payload.target_row_id,
                note=payload.note,
                created_by=principal.username,
            )
            doc = evolution_node.link_doc(row)
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)
    return EvolutionNodeLinkOut(**doc)


@router.post("/{node_id}/stable-implementation", response_model=EvolutionNodeSummaryOut)
def pin_evolution_node_stable_implementation(
    node_id: int,
    payload: EvolutionNodeStablePinIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> EvolutionNodeSummaryOut:
    """Pin the implementation production runs. The previous stable pin
    rotates into the rollback slot in the same transaction, so a Node that
    has ever had a second stable implementation always has an explicit
    rollback target -- `reopened` never clears either (ADR-5)."""
    with get_conn() as conn:
        try:
            row = evolution_node.pin_stable_implementation(
                conn,
                system_id=system_id,
                node_id=node_id,
                implementation_id=payload.implementation_id,
                actor=principal.username,
                reason=payload.reason,
            )
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)
    return _node_summary(row)


# ---------------------------------------------------------------------------
# Maturity transitions and lineage
# ---------------------------------------------------------------------------


@router.post("/{node_id}/transitions", response_model=EvolutionNodeTransitionOut)
def transition_evolution_node(
    node_id: int,
    payload: EvolutionNodeTransitionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> EvolutionNodeTransitionOut:
    """Request a maturity transition.

    The actor is taken from the authenticated `Principal`, never from the
    body: a body-supplied actor would let any caller record a transition as
    someone else's decision, which is exactly the provenance defect #337
    fixed for Joint Understanding findings. A caller may still identify a
    non-human actor through `actor_kind`, but only `system` is accepted
    there and the domain layer restricts which transitions that may drive.
    """
    with get_conn() as conn:
        try:
            result = evolution_node.apply_transition(
                conn,
                system_id=system_id,
                node_id=node_id,
                to_state=payload.to_state,
                decision_method=payload.decision_method,
                actor=principal.username,
                actor_kind=payload.actor_kind,
                reason=payload.reason,
                reason_code=payload.reason_code,
                evidence_refs=payload.evidence_refs,
                idempotency_key=payload.idempotency_key,
            )
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)

        if not result.decision.allowed:
            # The domain layer's own finite code, verbatim. Re-wording it
            # here would leave the Dashboard branching on prose.
            raise HTTPException(
                status_code=422,
                detail={
                    "code": result.decision.reason_code,
                    "message": result.decision.message,
                },
            )

        event_doc = (
            evolution_node.event_doc(result.event) if result.event is not None else None
        )
        maturity = result.node["maturity"]

    return EvolutionNodeTransitionOut(
        applied=result.applied,
        duplicate=result.duplicate,
        maturity=maturity,
        event=EvolutionNodeEventOut(**event_doc) if event_doc is not None else None,
    )


@router.get("/{node_id}/events", response_model=EvolutionNodeEventsOut)
def list_evolution_node_events(
    node_id: int,
    limit: int = Query(default=50, ge=1, le=_MAX_EVENT_LIMIT),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> EvolutionNodeEventsOut:
    """The append-only lineage, newest first.

    ADR-4: folding this log must reproduce the Node's stored maturity. The
    log is the reason a drifted stored value is detectable at all, so it is
    exposed in its own right rather than only as the projection's tail.
    """
    with get_conn() as conn:
        try:
            events = evolution_node.list_events(
                conn, system_id=system_id, node_id=node_id, limit=limit
            )
        except evolution_node.EvolutionNodeError as exc:
            _raise_domain_error(exc)
    return EvolutionNodeEventsOut(
        node_id=node_id,
        events=[EvolutionNodeEventOut(**event) for event in events],
    )
