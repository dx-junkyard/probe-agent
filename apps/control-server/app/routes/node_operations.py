"""Operations API: monitoring contracts, drift, anomalies and local reopen
(Issue #400, Phase 5 of the evolution control plane Epic #394).

The domain layer is `app/node_operations.py`; this module is a thin boundary
over it and owns no judgement of its own. Four boundary properties matter:

- **`GET` never decides anything.** Both read endpoints (the reopen scope
  proposal and the operations projection) recompute their answer on every
  call and write nothing -- looking at a Node must not move it (#380).

- **Provenance comes from the route and the principal, never the body**
  (#337). `approved_by` on a reopen is the authenticated user, and an
  anomaly recorded through HTTP is `decision_method='manual'`: an HTTP caller
  IS a human-driven client, and letting a request claim `deterministic` or
  `reasoning_llm` would let any POST forge a system- or model-produced
  classification. A reasoning classification is recorded by server-side code
  calling the domain layer directly, with its own `intelligence_runs` row.

- **Approving a reopen is a named human's decision** (ADR-9). There is no
  automatic maturity transition anywhere on these paths, and the stable
  implementation stays pinned throughout -- reopening starts exploration, it
  does not unpin production (ADR-5).

- **A repeated condition is a 200, not a conflict.** `POST .../anomalies`
  returns the existing anomaly with `created: false` when a still-open
  `dedupe_key` matches. A polling client re-reporting one continuing
  condition has not done anything wrong, and turning that into a 409 would
  make every poll look like an error the developer must resolve.

There is no reasoning-model call on any of these paths, so the
`get_conn()`-across-an-external-call hazard cannot arise here -- but the
connection is still opened once per request and closed before returning, so
that stays true if a later phase adds one.

probe-agent:
  role: API boundary for monitoring contracts, drift observations, the anomaly taxonomy and the local reopen loop
  capability: evolution-control-plane
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify reading a projection changes nothing, the reopen approver is taken from the authenticated principal rather than the body, a deduplicated anomaly is a 200 that creates nothing, an established Node with dead telemetry is reported as two independent readings, and every row stays inside one System.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import node_operations
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    NodeAnomalyOut,
    NodeAnomalyRecordIn,
    NodeAnomalyRecordOut,
    NodeDriftObservationIn,
    NodeDriftObservationOut,
    NodeMonitoringContractCreateIn,
    NodeMonitoringContractOut,
    NodeOperationsProjectionOut,
    NodeReopenApprovalOut,
    NodeReopenDecisionIn,
    NodeReopenPlanCreateIn,
    NodeReopenPlanOut,
    NodeReopenScopeOut,
)

router = APIRouter(prefix="/node-operations", tags=["node-operations"])

# A row in another System must be indistinguishable from one that does not
# exist: the domain layer raises NotFound for both, so cross-System ids stay
# unprobeable.
_ERROR_STATUS = {
    node_operations.OperationsNotFoundError: 404,
    node_operations.OperationsConflictError: 409,
    node_operations.OperationsValidationError: 422,
}


def _raise_domain_error(exc: node_operations.OperationsError) -> None:
    for exc_type, status_code in _ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _json_list(text: Optional[str]) -> List[Any]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# Row -> response helpers
# ---------------------------------------------------------------------------


def _contract_out(row) -> NodeMonitoringContractOut:
    return NodeMonitoringContractOut(
        id=row["id"],
        system_id=row["system_id"],
        node_id=row["node_id"],
        version_number=row["version_number"],
        observed_environment_ref=row["observed_environment_ref"],
        deployed_commit_sha=row["deployed_commit_sha"],
        sampling_note=row["sampling_note"],
        freshness_budget_seconds=row["freshness_budget_seconds"],
        minimum_sample_count=row["minimum_sample_count"],
        indicators=_json_list(row["indicators_json"]),
        reopen_conditions=_json_list(row["reopen_conditions_json"]),
        escalation_owner=row["escalation_owner"],
        active=bool(row["active"]),
        decision_method=row["decision_method"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        superseded_by_id=row["superseded_by_id"],
    )


def _observation_out(row) -> NodeDriftObservationOut:
    return NodeDriftObservationOut(
        id=row["id"],
        system_id=row["system_id"],
        node_id=row["node_id"],
        contract_id=row["contract_id"],
        indicator=row["indicator"],
        indicator_kind=row["indicator_kind"],
        observation_state=row["observation_state"],
        observed_value=row["observed_value"],
        reference_value=row["reference_value"],
        sample_count=row["sample_count"],
        window_seconds=row["window_seconds"],
        last_observed_at=row["last_observed_at"],
        detail=row["detail"],
        created_at=row["created_at"],
    )


def _anomaly_out(row) -> NodeAnomalyOut:
    return NodeAnomalyOut(
        id=row["id"],
        system_id=row["system_id"],
        node_id=row["node_id"],
        contract_id=row["contract_id"],
        classification=row["classification"],
        severity=row["severity"],
        summary=row["summary"],
        observation_ids=_json_list(row["observation_ids_json"]),
        decision_method=row["decision_method"],
        intelligence_run_id=row["intelligence_run_id"],
        classification_error=row["classification_error"],
        dedupe_key=row["dedupe_key"],
        status=row["status"],
        # Computed from the domain layer's own finite set, never re-derived
        # from a re-worded label here.
        frame_breaking=row["classification"]
        in node_operations.FRAME_BREAKING_CLASSIFICATIONS,
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def _plan_out(row) -> NodeReopenPlanOut:
    return NodeReopenPlanOut(
        id=row["id"],
        system_id=row["system_id"],
        origin_node_id=row["origin_node_id"],
        anomaly_id=row["anomaly_id"],
        scope_node_ids=_json_list(row["scope_node_ids_json"]),
        scope_rationale=_json_list(row["scope_rationale_json"]),
        excluded_node_ids=_json_list(row["excluded_node_ids_json"]),
        reason=row["reason"],
        budget_note=row["budget_note"],
        stable_implementation_retained=bool(row["stable_implementation_retained"]),
        status=row["status"],
        decision_method=row["decision_method"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        decision_note=row["decision_note"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Monitoring contract and deterministic readings
# ---------------------------------------------------------------------------


@router.post(
    "/nodes/{node_id}/monitoring-contracts",
    response_model=NodeMonitoringContractOut,
    status_code=201,
)
def create_monitoring_contract(
    node_id: int,
    payload: NodeMonitoringContractCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> NodeMonitoringContractOut:
    """Declare what watching this Node means, as a new contract version.

    The Node must already be `established` or `monitoring`: a contract for a
    Node still being explored would describe observation of something with no
    pinned implementation to observe (409). Creating a contract does NOT move
    the Node to `monitoring` -- that transition is a separate, explicitly
    recorded step, because a declared contract and an actually-observed Node
    are two different facts (ADR-5).
    """
    indicators: List[Dict[str, Any]] = [
        indicator.model_dump() for indicator in payload.indicators
    ]
    with get_conn() as conn:
        try:
            row = node_operations.create_monitoring_contract(
                conn,
                system_id=system_id,
                node_id=node_id,
                observed_environment_ref=payload.observed_environment_ref,
                deployed_commit_sha=payload.deployed_commit_sha,
                sampling_note=payload.sampling_note,
                freshness_budget_seconds=payload.freshness_budget_seconds,
                minimum_sample_count=payload.minimum_sample_count,
                indicators=indicators,
                reopen_conditions=list(payload.reopen_conditions),
                escalation_owner=payload.escalation_owner,
                created_by=principal.username,
            )
        except node_operations.OperationsError as exc:
            _raise_domain_error(exc)
    return _contract_out(row)


@router.post(
    "/monitoring-contracts/{contract_id}/observations",
    response_model=NodeDriftObservationOut,
    status_code=201,
)
def record_drift_observation(
    contract_id: int,
    payload: NodeDriftObservationIn,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> NodeDriftObservationOut:
    """Record ONE deterministic reading against a contract.

    No interpretation is accepted here. What was measured and what somebody
    concluded from it are separate rows on purpose, so a structural fact and a
    reading of it can never be confused.
    """
    with get_conn() as conn:
        try:
            row = node_operations.record_observation(
                conn,
                system_id=system_id,
                contract_id=contract_id,
                indicator=payload.indicator,
                indicator_kind=payload.indicator_kind,
                observation_state=payload.observation_state,
                observed_value=payload.observed_value,
                reference_value=payload.reference_value,
                sample_count=payload.sample_count,
                window_seconds=payload.window_seconds,
                last_observed_at=payload.last_observed_at,
                detail=payload.detail,
            )
        except node_operations.OperationsError as exc:
            _raise_domain_error(exc)
    return _observation_out(row)


@router.post("/nodes/{node_id}/anomalies", response_model=NodeAnomalyRecordOut)
def record_node_anomaly(
    node_id: int,
    payload: NodeAnomalyRecordIn,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> NodeAnomalyRecordOut:
    """Record an interpretation of one or more observations.

    `decision_method` is fixed to `manual` server-side and is not a request
    field (#337): an anomaly filed over HTTP is a person's reading, and
    letting the body claim `deterministic` or `reasoning_llm` would let any
    caller forge a system- or model-produced classification -- with no
    `intelligence_runs` row behind it.

    A still-open anomaly with the same `dedupe_key` is returned unchanged with
    `created: false` and status 200. One continuing condition must not produce
    a new anomaly, and then a new reopen, on every polling cycle.
    """
    with get_conn() as conn:
        try:
            row, created = node_operations.record_anomaly(
                conn,
                system_id=system_id,
                node_id=node_id,
                classification=payload.classification,
                summary=payload.summary,
                severity=payload.severity,
                contract_id=payload.contract_id,
                observation_ids=list(payload.observation_ids),
                decision_method="manual",
                classification_error=payload.classification_error,
                dedupe_key=payload.dedupe_key,
            )
        except node_operations.OperationsError as exc:
            _raise_domain_error(exc)
    return NodeAnomalyRecordOut(anomaly=_anomaly_out(row), created=created)


# ---------------------------------------------------------------------------
# Local reopen
# ---------------------------------------------------------------------------


@router.get("/nodes/{node_id}/reopen-scope", response_model=NodeReopenScopeOut)
def get_reopen_scope(
    node_id: int,
    include_neighbours: bool = Query(default=True),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> NodeReopenScopeOut:
    """Which Nodes a reopen WOULD touch, and which it deliberately excludes.

    Read-only: proposing a scope reopens nothing and writes nothing. The
    excluded list is part of the answer, not an omission -- it is the only way
    a reader can check that unrelated Nodes were left out.
    """
    with get_conn() as conn:
        try:
            scope = node_operations.propose_reopen_scope(
                conn,
                system_id=system_id,
                origin_node_id=node_id,
                include_neighbours=include_neighbours,
            )
        except node_operations.OperationsError as exc:
            _raise_domain_error(exc)
    return NodeReopenScopeOut(
        origin_node_id=scope.origin_node_id,
        scope_node_ids=list(scope.scope_node_ids),
        rationale=list(scope.rationale),
        excluded_node_ids=list(scope.excluded_node_ids),
    )


@router.post(
    "/nodes/{node_id}/reopen-plans",
    response_model=NodeReopenPlanOut,
    status_code=201,
)
def create_node_reopen_plan(
    node_id: int,
    payload: NodeReopenPlanCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> NodeReopenPlanOut:
    """Record a proposed reopen. Moves no Node.

    Proposing is not reopening: the maturity transition happens only at
    approval, with a named human (ADR-9). A reopen must state why -- an
    unexplained one cannot be reviewed (422).
    """
    with get_conn() as conn:
        try:
            # The scope is resolved here rather than left to the domain
            # default so `include_neighbours=false` actually narrows the plan
            # that gets stored, instead of silently widening at approval time.
            scope = node_operations.propose_reopen_scope(
                conn,
                system_id=system_id,
                origin_node_id=node_id,
                include_neighbours=payload.include_neighbours,
            )
            row = node_operations.create_reopen_plan(
                conn,
                system_id=system_id,
                origin_node_id=node_id,
                reason=payload.reason,
                anomaly_id=payload.anomaly_id,
                scope=scope,
                budget_note=payload.budget_note,
                created_by=principal.username,
            )
        except node_operations.OperationsError as exc:
            _raise_domain_error(exc)
    return _plan_out(row)


@router.post(
    "/reopen-plans/{plan_id}/approve", response_model=NodeReopenApprovalOut
)
def approve_node_reopen_plan(
    plan_id: int,
    payload: NodeReopenDecisionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> NodeReopenApprovalOut:
    """Approve a reopen and transition every in-scope Node to `reopened`.

    The approver comes from the authenticated principal, never the body
    (#337): accepting a name from a request would let any caller record
    someone else's approval of a maturity transition ADR-9 requires a human
    for. An unidentified account is refused.

    The stable implementation stays pinned throughout -- production keeps
    running the established implementation while exploration proceeds, which
    is what makes starting a re-exploration safe at all (ADR-5). A Node that
    cannot legally transition comes back in `results` with its finite refusal
    code rather than being forced or silently dropped.
    """
    if not principal.username:
        raise HTTPException(
            status_code=403,
            detail="Approving a reopen requires an identified user account",
        )
    with get_conn() as conn:
        try:
            row, results = node_operations.approve_reopen_plan(
                conn,
                system_id=system_id,
                plan_id=plan_id,
                approved_by=principal.username,
                note=payload.note,
            )
        except node_operations.OperationsError as exc:
            _raise_domain_error(exc)
    return NodeReopenApprovalOut(plan=_plan_out(row), results=results)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


@router.get("/nodes/{node_id}", response_model=NodeOperationsProjectionOut)
def get_node_operations(
    node_id: int,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> NodeOperationsProjectionOut:
    """One Node's operational picture. Writes nothing.

    `maturity` and `observation` are returned as two independent readings and
    are never merged (ADR-5): an `established` Node whose telemetry stopped
    reads as `established` + `unobserved`, and a Node with no contract at all
    reads as `observation: null` + `monitoring_contract_declared: false` --
    "nothing is wired" and "monitoring failed" are different answers.
    """
    with get_conn() as conn:
        try:
            projection = node_operations.build_operations_projection(
                conn, system_id=system_id, node_id=node_id
            )
        except node_operations.OperationsError as exc:
            _raise_domain_error(exc)
    return NodeOperationsProjectionOut(**projection)
