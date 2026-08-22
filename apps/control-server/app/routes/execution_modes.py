"""Execution mode API (Epic #412, Issue #413).

The domain layer is `app/execution_mode.py`; this module is a thin boundary
over it and owns no judgement of its own. Four boundary properties matter:

- **`GET` never decides anything and never writes.** Resolving a mode,
  reading the projection and reading divergence all recompute their answer on
  every call. Looking at a System's modes must not change one (#380).

- **Provenance comes from the route and the principal, never the body**
  (#337). `actor` on an assignment or a revocation is the authenticated user,
  and `decision_method` is fixed to `manual` server-side: the subject that
  changes an execution mode over HTTP is a human (§5.1). Accepting either
  from a request body would let any caller forge a decision in someone else's
  name.

- **A refused capability is a 409 that says why and where** (§4.1). The body
  carries the finite `denial_code`, the effective mode and the `source_scope`,
  so a developer reads "why was I refused" and "which setting is in effect"
  in one answer instead of two round trips.

- **Recording an observation is NOT gated on `observation_record`.** An
  observation exists to expose a configured/runtime disagreement, and the
  disagreement that matters most is a Node that ran in a MORE permissive mode
  than it was configured for. Gating the audit write on the configured mode
  would hide exactly that case.

No reasoning-model call happens on any of these paths, so the
`get_conn()`-across-an-external-call hazard cannot arise here -- but the
connection is still opened once per request and closed before returning, so
that stays true if a later phase adds one.

probe-agent:
  role: API boundary for execution-mode resolution, assignment audit, runtime observations and divergence
  capability: execution-mode-control
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify a denied capability returns 409 with its finite denial_code and the source scope, that the assignment actor is the authenticated principal rather than the body, that a read endpoint writes nothing, and that no assignment or observation of another System is ever visible.
"""

from __future__ import annotations

import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import execution_mode, execution_target
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    ExecutionModeAssignIn,
    ExecutionModeAssignmentOut,
    ExecutionModeDecisionOut,
    ExecutionModeDenialOut,
    ExecutionModeDivergenceListOut,
    ExecutionModeDivergenceOut,
    ExecutionModeNodeProjectionOut,
    ExecutionModeObservationIn,
    ExecutionModeObservationOut,
    ExecutionModeProjectionOut,
    ExecutionModeRevokeIn,
    ExecutionModeScopeReadingOut,
)

router = APIRouter(prefix="/execution-modes", tags=["execution-modes"])

# A row in another System must be indistinguishable from one that does not
# exist: the domain layer raises NotFound for both, so cross-System ids stay
# unprobeable.
_ERROR_STATUS = {
    execution_mode.ExecutionModeNotFoundError: 404,
    execution_mode.ExecutionModeConflictError: 409,
    execution_mode.ExecutionModeValidationError: 422,
}


def _raise_domain_error(exc: execution_mode.ExecutionModeError) -> None:
    for exc_type, status_code in _ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def raise_execution_mode_denied(exc: execution_mode.ExecutionModeDenied) -> None:
    """Map a refused capability gate onto its 409 (§4.1).

    Exported so every later gated path (#415's execution records, the Replay /
    offline-shadow entry points) produces the SAME body instead of inventing a
    second shape for the same refusal.
    """
    decision = exc.decision
    body = ExecutionModeDenialOut(
        denial_code=exc.denial_code,
        message=str(exc),
        mode=decision.mode if decision is not None else None,
        source_scope=decision.source_scope if decision is not None else None,
        reason=decision.reason if decision is not None else None,
        decision=_decision_out(decision) if decision is not None else None,
    )
    raise HTTPException(status_code=409, detail=body.model_dump()) from exc


def raise_execution_target_ambiguous(
    exc: execution_target.ExecutionTargetAmbiguous,
) -> None:
    """Map an undecidable target mapping onto the same 409 shape (§4.3).

    Reported with its OWN finite `denial_code` rather than folded into
    `capability_not_permitted`: no mode was refused here, and telling a
    developer to change a mode when the fix is to supersede an
    `evolution_node_link` would be a wrong instruction, not merely a vague
    one. `mode` / `source_scope` / `reason` stay `None` because no mode
    reading was taken -- an unreadable fact is never a fact's default value
    (#380).
    """
    body = execution_target.ExecutionTargetDenialOut(
        denial_code=exc.denial_code,
        message=str(exc),
        node_keys=list(exc.node_keys),
    )
    raise HTTPException(status_code=409, detail=body.model_dump()) from exc


def gate_execution_target(
    conn,
    *,
    system_id: int,
    capability: str,
    target_kind: str,
    target_ref,
    response=None,
) -> execution_target.ExecutionTargetGate:
    """The single HTTP-boundary form of the §4.3 candidate-execution gate.

    Every pre-existing execution entry point (Experiment run, Replay run,
    Replay variant run, Candidate Studio replay/promote) calls THIS, so a
    refusal has one body shape and a permitted-but-`unmapped` target reports
    itself the same way everywhere.

    `unmapped` returns normally -- bulk-migrating existing Components onto
    Evolution Nodes is an explicit Epic non-goal, so a target no Node links
    keeps its pre-#413 behaviour exactly. It is still reported, on
    `X-Execution-Governance`: "not governed" must never be indistinguishable
    from "permitted".
    """
    try:
        gate = execution_target.require_target_capability(
            conn,
            system_id=system_id,
            capability=capability,
            target_kind=target_kind,
            target_ref=target_ref,
        )
    except execution_target.ExecutionTargetAmbiguous as exc:
        raise_execution_target_ambiguous(exc)
        raise  # pragma: no cover - the helper always raises
    except execution_mode.ExecutionModeDenied as exc:
        raise_execution_mode_denied(exc)
        raise  # pragma: no cover - the helper always raises
    execution_target.apply_governance_headers(response, gate)
    return gate


# ---------------------------------------------------------------------------
# Domain value -> response helpers
# ---------------------------------------------------------------------------


def _reading_out(reading: execution_mode.ScopeReading) -> ExecutionModeScopeReadingOut:
    return ExecutionModeScopeReadingOut(
        scope_kind=reading.scope_kind,
        scope_ref=reading.scope_ref,
        state=reading.state,
        mode=reading.mode,
        assignment_id=reading.assignment_id,
        effective_from=reading.effective_from,
        effective_until=reading.effective_until,
        open_row_count=reading.open_row_count,
    )


def _decision_out(
    decision: execution_mode.ExecutionModeDecision,
) -> ExecutionModeDecisionOut:
    return ExecutionModeDecisionOut(
        mode=decision.mode,
        source_scope=decision.source_scope,
        source_ref=decision.source_ref,
        reason=decision.reason,
        permitted_capabilities=list(decision.capabilities),
        scope_trace=[_reading_out(reading) for reading in decision.scope_trace],
    )


def _assignment_out(doc) -> ExecutionModeAssignmentOut:
    return ExecutionModeAssignmentOut(**doc)


def _divergence_out(
    reading: execution_mode.DivergenceReading,
) -> ExecutionModeDivergenceOut:
    return ExecutionModeDivergenceOut(
        node_key=reading.node_key,
        divergence=reading.divergence,
        effective_mode=reading.effective_mode,
        observed_mode=reading.observed_mode,
        observed_at=reading.observed_at,
        last_assignment_at=reading.last_assignment_at,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/resolve", response_model=ExecutionModeDecisionOut)
def resolve_mode(
    node_key: Optional[str] = Query(default=None),
    flow_ref: Optional[str] = Query(default=None),
    capability: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExecutionModeDecisionOut:
    """The effective mode for one subject, plus the trace that explains it.

    With `capability` supplied this additionally runs the real gate, so a
    caller can ask "may I do X, and if not why" before spending anything --
    and gets the same 409 body the gated write paths return. Without it the
    endpoint only reports, and a refusal is expressed as the capability simply
    being absent from `permitted_capabilities`.
    """
    with get_conn() as conn:
        try:
            if capability is not None:
                decision = execution_mode.require_capability(
                    conn,
                    system_id=system_id,
                    capability=capability,
                    node_key=node_key,
                    flow_ref=flow_ref,
                    now=None,
                )
            else:
                facts = execution_mode.load_mode_facts(
                    conn,
                    system_id=system_id,
                    node_key=node_key,
                    flow_refs=[flow_ref] if flow_ref else None,
                )
                decision = execution_mode.resolve_execution_mode(facts)
        except execution_mode.ExecutionModeDenied as exc:
            raise_execution_mode_denied(exc)
        except execution_mode.ExecutionModeError as exc:
            _raise_domain_error(exc)
    return _decision_out(decision)


@router.get("", response_model=ExecutionModeProjectionOut)
def get_projection(
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExecutionModeProjectionOut:
    """Current assignments and every Node's effective mode. Writes nothing.

    `mode_source` keeps `default` (the fail-closed `fixed` nobody chose)
    distinct from `system` + reason `system_assignment` (a human who chose
    `fixed`) -- two different facts (§4.4).
    """
    with get_conn() as conn:
        try:
            projection = execution_mode.build_mode_projection(
                conn, system_id=system_id
            )
        except execution_mode.ExecutionModeError as exc:
            _raise_domain_error(exc)
    return ExecutionModeProjectionOut(
        system_id=projection.system_id,
        schema_version=projection.schema_version,
        generated_at=projection.generated_at,
        system_decision=_decision_out(projection.system_decision),
        assignments=[_assignment_out(doc) for doc in projection.assignments],
        nodes=[
            ExecutionModeNodeProjectionOut(
                node_id=node.node_id,
                node_key=node.node_key,
                maturity=node.maturity,
                execution_mode=node.execution_mode,
                mode_source=node.mode_source,
                mode_reason=node.mode_reason,
                source_ref=node.source_ref,
                flow_refs=list(node.flow_refs),
                divergence=node.divergence,
                observed_mode=node.observed_mode,
                observed_at=node.observed_at,
            )
            for node in projection.nodes
        ],
    )


@router.get("/divergence", response_model=ExecutionModeDivergenceListOut)
def get_divergence(
    node_key: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExecutionModeDivergenceListOut:
    """Configured vs actually-observed mode, per Node (§5.2). Writes nothing."""
    with get_conn() as conn:
        try:
            if node_key:
                readings = [
                    execution_mode.evaluate_divergence(
                        conn, system_id=system_id, node_key=node_key
                    )
                ]
            else:
                rows = conn.execute(
                    "SELECT node_key FROM evolution_node WHERE system_id = ? "
                    "ORDER BY node_key ASC",
                    (system_id,),
                ).fetchall()
                readings = [
                    execution_mode.evaluate_divergence(
                        conn, system_id=system_id, node_key=row["node_key"]
                    )
                    for row in rows
                ]
        except execution_mode.ExecutionModeError as exc:
            _raise_domain_error(exc)
    return ExecutionModeDivergenceListOut(
        system_id=system_id,
        generated_at=time.time(),
        nodes=[_divergence_out(reading) for reading in readings],
    )


@router.get("/assignments", response_model=List[ExecutionModeAssignmentOut])
def list_mode_assignments(
    scope_kind: Optional[str] = Query(default=None),
    current_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> List[ExecutionModeAssignmentOut]:
    """The append-only assignment audit, newest first."""
    with get_conn() as conn:
        try:
            rows = execution_mode.list_assignments(
                conn,
                system_id=system_id,
                scope_kind=scope_kind,
                current_only=current_only,
                limit=limit,
            )
        except execution_mode.ExecutionModeError as exc:
            _raise_domain_error(exc)
        docs = [execution_mode.assignment_doc(row) for row in rows]
    return [_assignment_out(doc) for doc in docs]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@router.post("/assignments", response_model=ExecutionModeAssignmentOut, status_code=201)
def create_assignment(
    payload: ExecutionModeAssignIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ExecutionModeAssignmentOut:
    """Assign a mode to one scope. Append-only.

    `actor` is the authenticated principal and `decision_method` is fixed to
    `manual` -- neither is a request field (#337 / §5.1). `reason` is required
    because an unexplained permission change cannot be reviewed.
    """
    with get_conn() as conn:
        try:
            row = execution_mode.assign_mode(
                conn,
                system_id=system_id,
                scope_kind=payload.scope_kind,
                scope_ref=payload.scope_ref,
                mode=payload.mode,
                reason=payload.reason,
                actor=principal.username,
                actor_kind="user",
                effective_from=payload.effective_from,
                effective_until=payload.effective_until,
            )
        except execution_mode.ExecutionModeError as exc:
            _raise_domain_error(exc)
        doc = execution_mode.assignment_doc(row)
    return _assignment_out(doc)


@router.post(
    "/assignments/{assignment_id}/revoke",
    response_model=ExecutionModeAssignmentOut,
    status_code=201,
)
def revoke_assignment(
    assignment_id: int,
    payload: ExecutionModeRevokeIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ExecutionModeAssignmentOut:
    """End an assignment explicitly. Append-only: writes a `revoke` row.

    A revocation is a human saying "this assignment is over", so normal
    inheritance resumes. Letting a window elapse is the OTHER case and clamps
    to `fixed` instead, because nobody has decided what happens next
    (EM-ADR-2).
    """
    with get_conn() as conn:
        try:
            row = execution_mode.revoke_mode(
                conn,
                system_id=system_id,
                assignment_id=assignment_id,
                reason=payload.reason,
                actor=principal.username,
                actor_kind="user",
            )
        except execution_mode.ExecutionModeError as exc:
            _raise_domain_error(exc)
        doc = execution_mode.assignment_doc(row)
    return _assignment_out(doc)


@router.post("/observations", response_model=ExecutionModeObservationOut, status_code=201)
def create_observation(
    payload: ExecutionModeObservationIn,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExecutionModeObservationOut:
    """Record which mode a Node ACTUALLY ran under.

    Deliberately not gated on `observation_record` -- see the module
    docstring: gating the audit write on the configured mode would hide the
    very disagreement it exists to expose.
    """
    with get_conn() as conn:
        try:
            row = execution_mode.record_observation(
                conn,
                system_id=system_id,
                node_key=payload.node_key,
                observed_mode=payload.observed_mode,
                capability=payload.capability,
                run_ref=payload.run_ref,
                source=payload.source,
                detail=payload.detail,
            )
        except execution_mode.ExecutionModeError as exc:
            _raise_domain_error(exc)
        doc = execution_mode.observation_doc(row)
    return ExecutionModeObservationOut(**doc)


@router.get(
    "/target-governance",
    response_model=execution_target.ExecutionTargetGovernanceOut,
)
def get_target_governance(
    target_kind: str = Query(description="component | feature"),
    target_ref: str = Query(description="component_id or feature_id, matched exactly"),
    capability: Optional[str] = Query(
        default=None,
        description=(
            "Optional capability to evaluate; when given, capability_permitted "
            "reports whether an execution WOULD be allowed right now."
        ),
    ),
    system_id: int = Depends(get_system_id),
) -> execution_target.ExecutionTargetGovernanceOut:
    """Whether an execution target is under the execution-mode contract (§4.3).

    Reads only -- it resolves nothing that executing would resolve differently
    and writes nothing. It exists so `unmapped` is inspectable BEFORE a run:
    a developer can see that a Component is still on the legacy path (no
    Evolution Node links it) instead of inferring it from an execution that
    was not refused.
    """
    with get_conn() as conn:
        try:
            return execution_target.governance_out(
                conn,
                system_id=system_id,
                target_kind=target_kind,
                target_ref=target_ref,
                capability=capability,
            )
        except execution_mode.ExecutionModeError as exc:
            _raise_domain_error(exc)
            raise  # pragma: no cover - the helper always raises
