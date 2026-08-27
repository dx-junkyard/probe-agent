"""Probe Cell Fabric API routes: Goal/Task ledger + delegate/report/escalate
protocol (Issue #300, Sub 3 of the Probe Cell Fabric epic, Issue #297).

Routes are thin: all validation and persistence logic lives in
``app/cell_tasks.py``; this module only resolves the System, calls the core
function, translates ``app.cell_tasks`` errors to HTTP status codes
(``NotFoundError`` -> 404, ``ConflictError`` -> 409,
``ValidationFailedError`` -> 422), and shapes the response models.

probe-agent:
  role: API boundary for the Goal/Task ledger and delegate/report/escalate protocol
  capability: probe-cell-fabric
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify every route maps app.cell_tasks errors to the right status code, evidence/context refs are resolved before a row is written, and idempotent delegate/report resend never creates a duplicate row.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import cell_tasks
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    CellEscalationOut,
    CellEscalationsListOut,
    CellGoalCreate,
    CellGoalDetailOut,
    CellGoalOut,
    CellGoalStatusUpdate,
    CellGoalsListOut,
    CellReportCreate,
    CellReportOut,
    CellReportsListOut,
    CellTaskDelegateCreate,
    CellTaskDetailOut,
    CellTaskEventOut,
    CellTaskOut,
    CellTaskReturnToParentRequest,
    CellTasksListOut,
    CellTaskTransitionRequest,
)

router = APIRouter()


def _translate(exc: cell_tasks.CellTaskError) -> HTTPException:
    if isinstance(exc, cell_tasks.NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, cell_tasks.ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# Output shaping
# ---------------------------------------------------------------------------


def _goal_out(row) -> CellGoalOut:
    return CellGoalOut(
        id=row["id"],
        system_id=row["system_id"],
        parent_goal_id=row["parent_goal_id"],
        title=row["title"],
        description=row["description"] or "",
        owner_cell_id=row["owner_cell_id_business"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _goal_row_with_business_owner(conn, row) -> dict:
    """sqlite3.Row doesn't support item assignment; build a plain dict with
    the owner Cell's business ``cell_id`` resolved alongside the raw row."""
    data = dict(row)
    data["owner_cell_id_business"] = cell_tasks.cell_business_id(
        conn, row["owner_cell_id"]
    )
    return data


def _task_out(conn, row) -> CellTaskOut:
    return CellTaskOut(
        id=row["id"],
        system_id=row["system_id"],
        goal_id=row["goal_id"],
        owner_cell_id=cell_tasks.cell_business_id(conn, row["owner_cell_id"]) or "",
        delegated_by_cell_id=cell_tasks.cell_business_id(
            conn, row["delegated_by_cell_id"]
        ),
        title=row["title"],
        acceptance=json.loads(row["acceptance_json"] or "[]"),
        context_refs=json.loads(row["context_refs_json"] or "[]"),
        budget=json.loads(row["budget_json"]) if row["budget_json"] else None,
        deadline=row["deadline"],
        priority=row["priority"],
        status=row["status"],
        retry_count=row["retry_count"],
        retry_limit=row["retry_limit"],
        blocked_by=json.loads(row["blocked_by_json"] or "[]"),
        acceptance_met=bool(row["acceptance_met"]),
        evidence=json.loads(row["evidence_json"] or "[]"),
        returned_to_parent=bool(row["returned_to_parent"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_out(row) -> CellTaskEventOut:
    return CellTaskEventOut(
        id=row["id"],
        task_id=row["task_id"],
        event_type=row["event_type"],
        from_status=row["from_status"],
        to_status=row["to_status"],
        detail=row["detail"] or "",
        created_at=row["created_at"],
    )


def _report_out(conn, row) -> CellReportOut:
    return CellReportOut(
        id=row["id"],
        system_id=row["system_id"],
        cell_definition_id=cell_tasks.cell_business_id(
            conn, row["cell_definition_id"]
        )
        or "",
        task_id=row["task_id"],
        kind=row["kind"],
        severity=row["severity"],
        fact=json.loads(row["fact_json"] or "[]"),
        interpretation=json.loads(row["interpretation_json"] or "[]"),
        ask=json.loads(row["ask_json"] or "[]"),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        escalation_id=cell_tasks.get_report_escalation_id(conn, row["id"]),
    )


def _escalation_out(conn, row) -> CellEscalationOut:
    return CellEscalationOut(
        id=row["id"],
        system_id=row["system_id"],
        report_id=row["report_id"],
        cell_definition_id=cell_tasks.cell_business_id(
            conn, row["cell_definition_id"]
        )
        or "",
        severity=row["severity"],
        status=row["status"],
        summary=row["summary"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


@router.post("/cell-fabric/goals", response_model=CellGoalOut, status_code=201)
def create_goal(
    payload: CellGoalCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellGoalOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.create_goal(
                conn,
                system_id=system_id,
                title=payload.title,
                description=payload.description,
                parent_goal_id=payload.parent_goal_id,
                owner_cell_id=payload.owner_cell_id,
            )
            data = _goal_row_with_business_owner(conn, row)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
    return _goal_out(data)


@router.get("/cell-fabric/goals", response_model=CellGoalsListOut)
def list_goals(system_id: int = Depends(get_system_id)) -> CellGoalsListOut:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM cell_goals WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        goals = [_goal_out(_goal_row_with_business_owner(conn, r)) for r in rows]
    return CellGoalsListOut(system_id=system_id, goals=goals)


@router.get("/cell-fabric/goals/{goal_id}", response_model=CellGoalDetailOut)
def get_goal(
    goal_id: int, system_id: int = Depends(get_system_id),
) -> CellGoalDetailOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.get_goal_row(conn, system_id, goal_id)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
        goal = _goal_out(_goal_row_with_business_owner(conn, row))
        task_rows = cell_tasks.list_goal_tasks(conn, system_id, goal_id)
        tasks = [_task_out(conn, r) for r in task_rows]
    return CellGoalDetailOut(goal=goal, tasks=tasks)


@router.post("/cell-fabric/goals/{goal_id}/status", response_model=CellGoalOut)
def set_goal_status(
    goal_id: int,
    payload: CellGoalStatusUpdate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellGoalOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.update_goal_status(
                conn, system_id=system_id, goal_id=goal_id, new_status=payload.status,
            )
            data = _goal_row_with_business_owner(conn, row)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
    return _goal_out(data)


# ---------------------------------------------------------------------------
# Tasks (P1 delegate)
# ---------------------------------------------------------------------------


@router.post("/cell-fabric/tasks", response_model=CellTaskOut, status_code=201)
def create_task(
    payload: CellTaskDelegateCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellTaskOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.delegate_task(
                conn,
                system_id=system_id,
                goal_id=payload.goal_id,
                owner_cell_id=payload.owner_cell_id,
                title=payload.title,
                acceptance=payload.acceptance,
                context_refs=payload.context_refs,
                budget=payload.budget,
                deadline=payload.deadline,
                priority=payload.priority,
                delegated_by_cell_id=payload.delegated_by_cell_id,
                idempotency_key=payload.idempotency_key,
            )
            out = _task_out(conn, row)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
    return out


@router.get("/cell-fabric/tasks", response_model=CellTasksListOut)
def list_tasks(
    goal_id: Optional[int] = None,
    owner_cell_id: Optional[str] = None,
    status: Optional[str] = None,
    system_id: int = Depends(get_system_id),
) -> CellTasksListOut:
    query = "SELECT * FROM cell_tasks WHERE system_id = ?"
    params = [system_id]
    if goal_id is not None:
        query += " AND goal_id = ?"
        params.append(goal_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    with get_conn() as conn:
        owner_row_id = None
        if owner_cell_id is not None:
            row = conn.execute(
                "SELECT id FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
                (owner_cell_id, system_id),
            ).fetchone()
            owner_row_id = row["id"] if row is not None else -1
            query += " AND owner_cell_id = ?"
            params.append(owner_row_id)
        query += " ORDER BY id DESC"
        rows = conn.execute(query, params).fetchall()
        tasks = [_task_out(conn, r) for r in rows]
    return CellTasksListOut(system_id=system_id, tasks=tasks)


@router.get("/cell-fabric/tasks/{task_id}", response_model=CellTaskDetailOut)
def get_task(
    task_id: int, system_id: int = Depends(get_system_id),
) -> CellTaskDetailOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.get_task_row(conn, system_id, task_id)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
        task = _task_out(conn, row)
        events = [_event_out(r) for r in cell_tasks.list_task_events(conn, system_id, task_id)]
    return CellTaskDetailOut(task=task, events=events)


@router.post(
    "/cell-fabric/tasks/{task_id}/transition", response_model=CellTaskOut,
)
def transition_task(
    task_id: int,
    payload: CellTaskTransitionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellTaskOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.transition_task(
                conn,
                system_id=system_id,
                task_id=task_id,
                new_status=payload.new_status,
                acceptance_met=payload.acceptance_met,
                evidence_refs=payload.evidence_refs,
                blocked_by=payload.blocked_by,
                detail=payload.detail,
            )
            out = _task_out(conn, row)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
    return out


@router.post(
    "/cell-fabric/tasks/{task_id}/return-to-parent", response_model=CellTaskOut,
)
def return_task_to_parent(
    task_id: int,
    payload: Optional[CellTaskReturnToParentRequest] = None,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellTaskOut:
    detail = payload.detail if payload is not None else ""
    with get_conn() as conn:
        try:
            row = cell_tasks.return_to_parent(
                conn, system_id=system_id, task_id=task_id, detail=detail,
            )
            out = _task_out(conn, row)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
    return out


# ---------------------------------------------------------------------------
# Reports (P2)
# ---------------------------------------------------------------------------


@router.post("/cell-fabric/reports", response_model=CellReportOut, status_code=201)
def create_report(
    payload: CellReportCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellReportOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.submit_report(
                conn,
                system_id=system_id,
                cell_definition_id=payload.cell_definition_id,
                kind=payload.kind,
                severity=payload.severity,
                task_id=payload.task_id,
                fact=[item.model_dump() for item in payload.fact],
                interpretation=[item.model_dump() for item in payload.interpretation],
                ask=[item.model_dump() for item in payload.ask],
                idempotency_key=payload.idempotency_key,
            )
            out = _report_out(conn, row)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
    return out


@router.get("/cell-fabric/reports", response_model=CellReportsListOut)
def list_reports(
    cell_definition_id: Optional[str] = None,
    kind: Optional[str] = None,
    system_id: int = Depends(get_system_id),
) -> CellReportsListOut:
    query = "SELECT * FROM cell_reports WHERE system_id = ?"
    params = [system_id]
    with get_conn() as conn:
        if cell_definition_id is not None:
            row = conn.execute(
                "SELECT id FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
                (cell_definition_id, system_id),
            ).fetchone()
            query += " AND cell_definition_id = ?"
            params.append(row["id"] if row is not None else -1)
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY id DESC"
        rows = conn.execute(query, params).fetchall()
        reports = [_report_out(conn, r) for r in rows]
    return CellReportsListOut(system_id=system_id, reports=reports)


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


@router.get("/cell-fabric/escalations", response_model=CellEscalationsListOut)
def list_escalations(
    status: Optional[str] = None, system_id: int = Depends(get_system_id),
) -> CellEscalationsListOut:
    query = "SELECT * FROM cell_escalations WHERE system_id = ?"
    params = [system_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        escalations = [_escalation_out(conn, r) for r in rows]
    return CellEscalationsListOut(system_id=system_id, escalations=escalations)


@router.post(
    "/cell-fabric/escalations/{escalation_id}/acknowledge",
    response_model=CellEscalationOut,
)
def acknowledge_escalation(
    escalation_id: int,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellEscalationOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.acknowledge_escalation(
                conn, system_id=system_id, escalation_id=escalation_id,
            )
            out = _escalation_out(conn, row)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
    return out


@router.post(
    "/cell-fabric/escalations/{escalation_id}/resolve",
    response_model=CellEscalationOut,
)
def resolve_escalation(
    escalation_id: int,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellEscalationOut:
    with get_conn() as conn:
        try:
            row = cell_tasks.resolve_escalation(
                conn, system_id=system_id, escalation_id=escalation_id,
            )
            out = _escalation_out(conn, row)
        except cell_tasks.CellTaskError as exc:
            raise _translate(exc)
    return out
