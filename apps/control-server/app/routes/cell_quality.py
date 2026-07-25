"""Probe Cell Fabric API routes: 品質サンプリング・独立監査・quality floor
(Issue #302, Sub 6 of the Probe Cell Fabric epic, Issue #297).

Routes are thin: deterministic stratified sampling, the deterministic-verdict
audit (with its optional fail-closed reasoning explanation), the daily audit
budget gate, and the quality-floor suspend/resume logic all live in
``app/cell_quality.py``; this module only resolves the System/Cell, calls the
core function, translates ``app.cell_quality`` errors to HTTP status codes
(``NotFoundError`` -> 404, ``ConflictError`` -> 409,
``ValidationFailedError`` -> 422), and shapes the response models.

probe-agent:
  role: API boundary for quality-config, sample selection, audits, quality-floor evaluation, and intake state
  capability: probe-cell-fabric
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify every route maps app.cell_quality errors to the right status code, an exhausted daily audit budget returns 409 without writing an audit row, and a quality-floor breach's suspend + sev1 escalation are both visible through these endpoints.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import cell_quality
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    CellIntakeResumeIn,
    CellIntakeStateOut,
    CellQualityAuditOut,
    CellQualityAuditRequestIn,
    CellQualityAuditsListOut,
    CellQualityConfigIn,
    CellQualityConfigOut,
    CellQualityFloorEvaluateOut,
    CellQualitySampleOut,
    CellQualitySampleSelectIn,
    CellQualitySamplesListOut,
)

router = APIRouter()


def _translate(exc: cell_quality.CellQualityError) -> HTTPException:
    if isinstance(exc, cell_quality.NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, cell_quality.ConflictError):
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


# ---------------------------------------------------------------------------
# Output shaping
# ---------------------------------------------------------------------------


def _config_out(cell_id: str, data: dict) -> CellQualityConfigOut:
    return CellQualityConfigOut(
        system_id=data["system_id"],
        cell_definition_id=data["cell_definition_id"],
        sample_rate=data["sample_rate"],
        strata=data["strata"],
        audit_rate=data["audit_rate"],
        quality_floor=data["quality_floor"],
        floor_window=data["floor_window"],
        daily_audit_budget=data["daily_audit_budget"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


def _sample_out(row) -> CellQualitySampleOut:
    return CellQualitySampleOut(
        id=row["id"],
        system_id=row["system_id"],
        cell_definition_id=row["cell_definition_id"],
        config_id=row["config_id"],
        stratum=row["stratum"] or "",
        target_kind=row["target_kind"],
        target_id=str(row["target_id"]),
        selection_seed=row["selection_seed"],
        selected_at=row["selected_at"],
    )


def _audit_out(conn, row) -> CellQualityAuditOut:
    is_mock = False
    if row["explanation_run_id"] is not None:
        run_row = conn.execute(
            "SELECT is_mock FROM intelligence_runs WHERE id = ?",
            (row["explanation_run_id"],),
        ).fetchone()
        is_mock = bool(run_row["is_mock"]) if run_row is not None else False
    return CellQualityAuditOut(
        id=row["id"],
        system_id=row["system_id"],
        sample_id=row["sample_id"],
        auditor_model_alias=row["auditor_model_alias"],
        verdict=row["verdict"],
        verdict_decision_method=row["verdict_decision_method"],
        is_blind=bool(row["is_blind"]),
        failed_criteria=json.loads(row["failed_criteria_json"] or "[]"),
        verbatim_example=row["verbatim_example"] or "",
        explanation=row["explanation"] or "",
        explanation_run_id=row["explanation_run_id"],
        is_mock=is_mock,
        created_at=row["created_at"],
    )


def _intake_out(cell_id: str, data: dict) -> CellIntakeStateOut:
    return CellIntakeStateOut(
        system_id=data["system_id"],
        cell_id=cell_id,
        intake_status=data["intake_status"],
        reason=data["reason"] or "",
        escalation_id=data["escalation_id"],
        changed_at=data["changed_at"],
    )


# ---------------------------------------------------------------------------
# Quality config
# ---------------------------------------------------------------------------


@router.put(
    "/cell-fabric/cells/{cell_id}/quality-config",
    response_model=CellQualityConfigOut,
)
def put_quality_config(
    cell_id: str,
    payload: CellQualityConfigIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellQualityConfigOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            data = cell_quality.upsert_quality_config(
                conn,
                system_id=system_id,
                cell_definition_id=cell_row["id"],
                sample_rate=payload.sample_rate,
                strata=(
                    [s.model_dump() for s in payload.strata]
                    if payload.strata is not None else None
                ),
                audit_rate=payload.audit_rate,
                quality_floor=payload.quality_floor,
                floor_window=payload.floor_window,
                daily_audit_budget=payload.daily_audit_budget,
            )
        except cell_quality.CellQualityError as exc:
            raise _translate(exc)
    return _config_out(cell_id, data)


@router.get(
    "/cell-fabric/cells/{cell_id}/quality-config",
    response_model=CellQualityConfigOut,
)
def get_quality_config(
    cell_id: str, system_id: int = Depends(get_system_id),
) -> CellQualityConfigOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        data = cell_quality.get_quality_config(conn, system_id, cell_row["id"])
    return _config_out(cell_id, data)


# ---------------------------------------------------------------------------
# Quality samples
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/cells/{cell_id}/quality-samples/select",
    response_model=CellQualitySamplesListOut,
)
def select_quality_samples(
    cell_id: str,
    payload: Optional[CellQualitySampleSelectIn] = None,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellQualitySamplesListOut:
    window_seconds = payload.window_seconds if payload is not None else None
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            rows = cell_quality.select_samples(
                conn, system_id=system_id, cell_definition_id=cell_row["id"],
                window_seconds=window_seconds,
            )
        except cell_quality.CellQualityError as exc:
            raise _translate(exc)
        samples = [_sample_out(r) for r in rows]
    return CellQualitySamplesListOut(system_id=system_id, cell_id=cell_id, samples=samples)


@router.get(
    "/cell-fabric/cells/{cell_id}/quality-samples",
    response_model=CellQualitySamplesListOut,
)
def list_quality_samples(
    cell_id: str, system_id: int = Depends(get_system_id),
) -> CellQualitySamplesListOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        rows = cell_quality.list_quality_samples(conn, system_id, cell_row["id"])
        samples = [_sample_out(r) for r in rows]
    return CellQualitySamplesListOut(system_id=system_id, cell_id=cell_id, samples=samples)


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/quality-samples/{sample_id}/audit",
    response_model=CellQualityAuditOut,
    status_code=201,
)
def audit_quality_sample(
    sample_id: int,
    payload: Optional[CellQualityAuditRequestIn] = None,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellQualityAuditOut:
    blind = payload.blind if payload is not None else False
    auditor_alias = payload.auditor_alias if payload is not None else None
    # cell_quality.run_audit manages its own connections: the LLM explanation
    # call (only attempted for a 'fail' verdict) happens with NO get_conn()
    # held, matching cell_orchestrator.run_triage's pattern.
    try:
        row, _run_id, _is_mock, _error = cell_quality.run_audit(
            system_id=system_id, sample_id=sample_id, blind=blind,
            auditor_alias=auditor_alias,
        )
    except cell_quality.CellQualityError as exc:
        raise _translate(exc)
    with get_conn() as conn:
        out = _audit_out(conn, row)
    return out


@router.post(
    "/cell-fabric/cells/{cell_id}/quality-audits/blind-parent-run",
    response_model=CellQualityAuditsListOut,
)
def run_blind_parent_audits(
    cell_id: str,
    auditor_alias: Optional[str] = None,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellQualityAuditsListOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        cell_definition_id = cell_row["id"]
    try:
        cell_quality.run_parent_blind_audits(
            system_id=system_id, cell_definition_id=cell_definition_id,
            auditor_alias=auditor_alias,
        )
    except cell_quality.CellQualityError as exc:
        raise _translate(exc)
    with get_conn() as conn:
        rows, counts = cell_quality.list_quality_audits(conn, system_id, cell_definition_id)
        audits = [_audit_out(conn, r) for r in rows]
    return CellQualityAuditsListOut(
        system_id=system_id, cell_id=cell_id, counts=counts, audits=audits,
    )


@router.get(
    "/cell-fabric/cells/{cell_id}/quality-audits",
    response_model=CellQualityAuditsListOut,
)
def get_quality_audits(
    cell_id: str, system_id: int = Depends(get_system_id),
) -> CellQualityAuditsListOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        rows, counts = cell_quality.list_quality_audits(conn, system_id, cell_row["id"])
        audits = [_audit_out(conn, r) for r in rows]
    return CellQualityAuditsListOut(
        system_id=system_id, cell_id=cell_id, counts=counts, audits=audits,
    )


# ---------------------------------------------------------------------------
# Quality floor / intake state
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/cells/{cell_id}/quality-floor/evaluate",
    response_model=CellQualityFloorEvaluateOut,
)
def evaluate_quality_floor(
    cell_id: str,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellQualityFloorEvaluateOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            result = cell_quality.evaluate_quality_floor(
                conn, system_id=system_id, cell_definition_id=cell_row["id"],
            )
        except cell_quality.CellQualityError as exc:
            raise _translate(exc)
    return CellQualityFloorEvaluateOut(
        system_id=system_id, cell_id=cell_id, pass_rate=result["pass_rate"],
        floor=result["floor"], denominator=result["denominator"],
        suspended=result["suspended"], escalation_id=result["escalation_id"],
    )


@router.get(
    "/cell-fabric/cells/{cell_id}/intake-state",
    response_model=CellIntakeStateOut,
)
def get_intake_state(
    cell_id: str, system_id: int = Depends(get_system_id),
) -> CellIntakeStateOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        data = cell_quality.get_intake_state(conn, system_id, cell_row["id"])
    return _intake_out(cell_id, data)


@router.post(
    "/cell-fabric/cells/{cell_id}/intake/resume",
    response_model=CellIntakeStateOut,
)
def resume_intake(
    cell_id: str,
    payload: Optional[CellIntakeResumeIn] = None,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellIntakeStateOut:
    reason = payload.reason if payload is not None else ""
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            data = cell_quality.resume_intake(
                conn, system_id=system_id, cell_definition_id=cell_row["id"],
                reason=reason,
            )
        except cell_quality.CellQualityError as exc:
            raise _translate(exc)
    return _intake_out(cell_id, data)
