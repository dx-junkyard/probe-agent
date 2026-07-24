"""Probe Cell Fabric API routes: 改善仮説・カナリア・shadow実行承認ゲート
(Issue #304, Sub 7 of the Probe Cell Fabric epic, Issue #297).

Routes are thin: the lifecycle state machine, the canary evidence gate,
parent/human approval gates, rubric ownership, the consecutive-rejection
auto-suspend circuit breaker, rollback, and the fail-closed reasoning
hypothesis draft all live in ``app/cell_improvement.py``; this module only
resolves the System/Cell (and business ``parent_cell_id`` strings to
``cell_definitions`` row ids), calls the core function, translates
``app.cell_improvement`` errors to HTTP status codes (``NotFoundError`` ->
404, ``ConflictError`` -> 409, ``ValidationFailedError`` -> 422), and shapes
the response models.

probe-agent:
  role: API boundary for improvement drafting/creation, transitions, approvals, shadow proposal/execution-approval decisions, rollback, and suspend/resume
  capability: probe-cell-fabric
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify every route maps app.cell_improvement errors to the right status code, the reasoning draft endpoint never creates an improvement row on LLM failure, and shadow-proposal vs live-shadow-execution-approval stay on separate endpoints/records all the way through the API surface.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import cell_improvement
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    CellApprovalIn,
    CellImprovementCreateIn,
    CellImprovementDetailOut,
    CellImprovementDraftIn,
    CellImprovementDraftOut,
    CellImprovementEventOut,
    CellImprovementOut,
    CellImprovementRollbackIn,
    CellImprovementsListOut,
    CellImprovementSuspendIn,
    CellImprovementTransitionIn,
    CellShadowDecideIn,
    CellShadowDecisionOut,
    CellShadowProposalIn,
    CellShadowRequestIn,
)

router = APIRouter()


def _translate(exc: cell_improvement.CellImprovementError) -> HTTPException:
    if isinstance(exc, cell_improvement.NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, cell_improvement.ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _get_cell_row(conn, cell_id: str, system_id: int):
    row = conn.execute(
        "SELECT * FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
        (cell_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Cell not found")
    return row


def _resolve_parent_cell_row_id(conn, system_id: int, parent_cell_id: Optional[str]) -> Optional[int]:
    if parent_cell_id is None:
        return None
    row = conn.execute(
        "SELECT id FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
        (parent_cell_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"parent Cell {parent_cell_id!r} not found",
        )
    return row["id"]


def _cell_business_id(conn, cell_definition_id: Optional[int]) -> Optional[str]:
    if cell_definition_id is None:
        return None
    row = conn.execute(
        "SELECT cell_id FROM cell_definitions WHERE id = ?", (cell_definition_id,),
    ).fetchone()
    return row["cell_id"] if row is not None else None


# ---------------------------------------------------------------------------
# Output shaping
# ---------------------------------------------------------------------------


def _improvement_out(conn, row) -> CellImprovementOut:
    is_mock = False
    if row["proposal_run_id"] is not None:
        run_row = conn.execute(
            "SELECT is_mock FROM intelligence_runs WHERE id = ?",
            (row["proposal_run_id"],),
        ).fetchone()
        is_mock = bool(run_row["is_mock"]) if run_row is not None else False
    return CellImprovementOut(
        id=row["id"],
        system_id=row["system_id"],
        cell_id=_cell_business_id(conn, row["cell_definition_id"]) or "",
        status=row["status"],
        target_kind=row["target_kind"],
        hypothesis=row["hypothesis"] or "",
        expected_effect=row["expected_effect"] or "",
        risk=row["risk"] or "",
        rollback_plan=row["rollback_plan"] or "",
        observed_facts_refs=json.loads(row["observed_facts_json"] or "[]"),
        proposal_run_id=row["proposal_run_id"],
        is_mock=is_mock,
        role_card_id=row["role_card_id"],
        proposed_role_card_version=row["proposed_role_card_version"],
        canary_evidence_refs=json.loads(row["canary_evidence_json"] or "[]"),
        parent_cell_id=_cell_business_id(conn, row["parent_cell_id"]),
        parent_approved_by=row["parent_approved_by"],
        parent_approved_at=row["parent_approved_at"],
        human_approved_by=row["human_approved_by"],
        human_approved_at=row["human_approved_at"],
        suspended=bool(row["suspended"]),
        suspension_reason=row["suspension_reason"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_out(row) -> CellImprovementEventOut:
    return CellImprovementEventOut(
        id=row["id"],
        system_id=row["system_id"],
        improvement_id=row["improvement_id"],
        event_type=row["event_type"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        actor=row["actor"],
        detail=row["detail"] or "",
        created_at=row["created_at"],
    )


def _shadow_decision_out(row) -> CellShadowDecisionOut:
    return CellShadowDecisionOut(
        id=row["id"],
        system_id=row["system_id"],
        improvement_id=row["improvement_id"],
        kind=row["kind"],
        status=row["status"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        decision_method=row["decision_method"] or "",
        note=row["note"] or "",
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Draft / manual creation
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/cells/{cell_id}/improvements/draft",
    response_model=CellImprovementDraftOut,
)
def draft_improvement(
    cell_id: str,
    payload: CellImprovementDraftIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellImprovementDraftOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        parent_row_id = _resolve_parent_cell_row_id(conn, system_id, payload.parent_cell_id)
        cell_definition_id = cell_row["id"]

    try:
        row, run_id, is_mock, error = cell_improvement.draft_improvement(
            system_id=system_id, cell_definition_id=cell_definition_id,
            target_kind=payload.target_kind, observed_facts_refs=payload.observed_facts_refs,
            parent_cell_id=parent_row_id,
        )
    except cell_improvement.CellImprovementError as exc:
        raise _translate(exc)

    with get_conn() as conn:
        out_improvement = _improvement_out(conn, row) if row is not None else None
    return CellImprovementDraftOut(
        system_id=system_id, cell_id=cell_id, improvement=out_improvement,
        draft_error=error, is_mock=is_mock, intelligence_run_id=run_id,
    )


@router.post(
    "/cell-fabric/cells/{cell_id}/improvements",
    response_model=CellImprovementOut,
    status_code=201,
)
def create_improvement(
    cell_id: str,
    payload: CellImprovementCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellImprovementOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        parent_row_id = _resolve_parent_cell_row_id(conn, system_id, payload.parent_cell_id)
        try:
            row = cell_improvement.create_improvement_manual(
                conn, system_id=system_id, cell_definition_id=cell_row["id"],
                target_kind=payload.target_kind, hypothesis=payload.hypothesis,
                expected_effect=payload.expected_effect, risk=payload.risk,
                rollback_plan=payload.rollback_plan,
                observed_facts_refs=payload.observed_facts_refs,
                parent_cell_id=parent_row_id,
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        out = _improvement_out(conn, row)
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(
    "/cell-fabric/cells/{cell_id}/improvements",
    response_model=CellImprovementsListOut,
)
def list_improvements(
    cell_id: str, system_id: int = Depends(get_system_id),
) -> CellImprovementsListOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            rows = cell_improvement.list_improvements(conn, system_id, cell_row["id"])
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        improvements = [_improvement_out(conn, r) for r in rows]
    return CellImprovementsListOut(system_id=system_id, cell_id=cell_id, improvements=improvements)


@router.get(
    "/cell-fabric/improvements/{improvement_id}",
    response_model=CellImprovementDetailOut,
)
def get_improvement(
    improvement_id: int, system_id: int = Depends(get_system_id),
) -> CellImprovementDetailOut:
    with get_conn() as conn:
        try:
            row = cell_improvement.get_improvement(conn, system_id, improvement_id)
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        events = cell_improvement.list_improvement_events(conn, system_id, improvement_id)
        shadow_decisions = cell_improvement.list_shadow_decisions(conn, system_id, improvement_id)
        return CellImprovementDetailOut(
            improvement=_improvement_out(conn, row),
            events=[_event_out(e) for e in events],
            shadow_decisions=[_shadow_decision_out(d) for d in shadow_decisions],
        )


# ---------------------------------------------------------------------------
# Transition / approvals / rollback
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/improvements/{improvement_id}/transition",
    response_model=CellImprovementOut,
)
def transition_improvement(
    improvement_id: int,
    payload: CellImprovementTransitionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellImprovementOut:
    with get_conn() as conn:
        try:
            row = cell_improvement.transition_improvement(
                conn, system_id=system_id, improvement_id=improvement_id,
                new_status=payload.new_status,
                canary_evidence_refs=payload.canary_evidence_refs,
                proposed_role_card_version=payload.proposed_role_card_version,
                detail=payload.detail, actor=payload.actor,
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        out = _improvement_out(conn, row)
    return out


@router.post(
    "/cell-fabric/improvements/{improvement_id}/parent-approve",
    response_model=CellImprovementOut,
)
def parent_approve(
    improvement_id: int,
    payload: CellApprovalIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellImprovementOut:
    with get_conn() as conn:
        try:
            row = cell_improvement.parent_approve(
                conn, system_id=system_id, improvement_id=improvement_id, actor=payload.actor,
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        out = _improvement_out(conn, row)
    return out


@router.post(
    "/cell-fabric/improvements/{improvement_id}/human-approve",
    response_model=CellImprovementOut,
)
def human_approve(
    improvement_id: int,
    payload: CellApprovalIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellImprovementOut:
    with get_conn() as conn:
        try:
            row = cell_improvement.human_approve(
                conn, system_id=system_id, improvement_id=improvement_id, actor=payload.actor,
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        out = _improvement_out(conn, row)
    return out


@router.post(
    "/cell-fabric/improvements/{improvement_id}/rollback",
    response_model=CellImprovementOut,
)
def rollback_improvement(
    improvement_id: int,
    payload: Optional[CellImprovementRollbackIn] = None,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellImprovementOut:
    actor = payload.actor if payload is not None else None
    with get_conn() as conn:
        try:
            cell_improvement.rollback_role_card(
                conn, system_id=system_id, improvement_id=improvement_id, actor=actor,
            )
            row = cell_improvement.get_improvement(conn, system_id, improvement_id)
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        out = _improvement_out(conn, row)
    return out


# ---------------------------------------------------------------------------
# Shadow proposal / live-shadow execution approval (SEPARATE endpoints and
# records -- approving one never approves or performs the other)
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/improvements/{improvement_id}/shadow-proposal",
    response_model=CellShadowDecisionOut,
    status_code=201,
)
def propose_shadow(
    improvement_id: int,
    payload: Optional[CellShadowProposalIn] = None,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellShadowDecisionOut:
    note = payload.note if payload is not None else ""
    actor = payload.actor if payload is not None else None
    with get_conn() as conn:
        try:
            row = cell_improvement.propose_shadow(
                conn, system_id=system_id, improvement_id=improvement_id, note=note, actor=actor,
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
    return _shadow_decision_out(row)


@router.post(
    "/cell-fabric/improvements/{improvement_id}/live-shadow-request",
    response_model=CellShadowDecisionOut,
    status_code=201,
)
def request_live_shadow(
    improvement_id: int,
    payload: Optional[CellShadowRequestIn] = None,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellShadowDecisionOut:
    note = payload.note if payload is not None else ""
    actor = payload.actor if payload is not None else None
    with get_conn() as conn:
        try:
            row = cell_improvement.request_live_shadow_approval(
                conn, system_id=system_id, improvement_id=improvement_id, note=note, actor=actor,
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
    return _shadow_decision_out(row)


@router.post(
    "/cell-fabric/shadow-decisions/{decision_id}/decide",
    response_model=CellShadowDecisionOut,
)
def decide_shadow(
    decision_id: int,
    payload: CellShadowDecideIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellShadowDecisionOut:
    with get_conn() as conn:
        try:
            row = cell_improvement.decide_shadow(
                conn, system_id=system_id, decision_id=decision_id,
                decision=payload.decision, decided_by=payload.decided_by, note=payload.note,
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
    return _shadow_decision_out(row)


# ---------------------------------------------------------------------------
# Suspend / resume
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/cells/{cell_id}/improvements/suspend",
    response_model=CellImprovementsListOut,
)
def suspend_improvements(
    cell_id: str,
    payload: CellImprovementSuspendIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellImprovementsListOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            cell_improvement.suspend_improvements(
                conn, system_id=system_id, cell_definition_id=cell_row["id"],
                reason=payload.reason,
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        rows = cell_improvement.list_improvements(conn, system_id, cell_row["id"])
        improvements = [_improvement_out(conn, r) for r in rows]
    return CellImprovementsListOut(system_id=system_id, cell_id=cell_id, improvements=improvements)


@router.post(
    "/cell-fabric/cells/{cell_id}/improvements/resume",
    response_model=CellImprovementsListOut,
)
def resume_improvements(
    cell_id: str,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellImprovementsListOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            cell_improvement.resume_improvements(
                conn, system_id=system_id, cell_definition_id=cell_row["id"],
            )
        except cell_improvement.CellImprovementError as exc:
            raise _translate(exc)
        rows = cell_improvement.list_improvements(conn, system_id, cell_row["id"])
        improvements = [_improvement_out(conn, r) for r in rows]
    return CellImprovementsListOut(system_id=system_id, cell_id=cell_id, improvements=improvements)
