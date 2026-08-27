"""Probe Cell Fabric API routes: 領域オーケストレーター (Issue #301, Sub 4 of
the Probe Cell Fabric epic, Issue #297).

Routes are thin: guardrail validation, the deterministic digest builder, and
the reasoning triage all live in ``app/cell_orchestrator.py``; this module
only resolves the System, calls the core function, translates
``app.cell_orchestrator`` errors to HTTP status codes
(``NotFoundError`` -> 404, ``ValidationFailedError`` -> 422), and shapes the
response models.

- ``GET /cell-fabric/orchestrators/{cell_id}/digest`` -- deterministic only
  (no LLM call anywhere on this path).
- ``POST /cell-fabric/orchestrators/{cell_id}/triage`` -- digest + reasoning
  triage; the digest is always returned even when triage fails.
- ``GET /cell-fabric/orchestrators/{cell_id}/triage-results`` -- persisted
  triage history, newest first.
- ``PUT /cell-fabric/cells/{cell_id}/roster`` -- the only way to change a
  Cell's roster after creation; same guardrails as creation, plus an
  append-only ``cell_roster_events`` audit row.

probe-agent:
  role: API boundary for orchestrator digest, reasoning triage, and roster updates
  capability: probe-cell-fabric
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify guardrail violations return 422 with the offending detail, the digest endpoint never calls an LLM, triage failure still returns the deterministic digest with triage=null, and every roster change writes an audit row.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException

from .. import cell_orchestrator
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    CellOrchestratorDigestOut,
    CellRosterEventOut,
    CellRosterUpdateIn,
    CellTriageResultOut,
    CellTriageResultsListOut,
    CellTriageRunOut,
)

router = APIRouter()


def _get_orchestrator_row(conn, cell_id: str, system_id: int):
    row = conn.execute(
        "SELECT * FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
        (cell_id, system_id),
    ).fetchone()
    if row is None or row["roster_json"] is None:
        raise HTTPException(status_code=404, detail="Orchestrator Cell not found")
    return row


def _get_cell_row(conn, cell_id: str, system_id: int):
    row = conn.execute(
        "SELECT * FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
        (cell_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Cell not found")
    return row


def _triage_out(conn, row) -> CellTriageResultOut:
    run_row = conn.execute(
        "SELECT is_mock FROM intelligence_runs WHERE id = ?",
        (row["intelligence_run_id"],),
    ).fetchone()
    return CellTriageResultOut(
        id=row["id"],
        system_id=row["system_id"],
        intelligence_run_id=row["intelligence_run_id"],
        classification=row["classification"],
        reasoning_summary=row["reasoning_summary"] or "",
        affected_cell_ids=json.loads(row["affected_cell_ids_json"] or "[]"),
        proposed_ask=row["proposed_ask"] or "",
        is_mock=bool(run_row["is_mock"]) if run_row is not None else False,
        created_at=row["created_at"],
    )


def _roster_event_out(row) -> CellRosterEventOut:
    return CellRosterEventOut(
        id=row["id"],
        system_id=row["system_id"],
        cell_definition_id=row["cell_definition_id"],
        old_roster=(
            json.loads(row["old_roster_json"])
            if row["old_roster_json"] is not None else None
        ),
        new_roster=json.loads(row["new_roster_json"]),
        changed_by=row["changed_by"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Digest (deterministic; no LLM call anywhere on this path)
# ---------------------------------------------------------------------------


@router.get(
    "/cell-fabric/orchestrators/{cell_id}/digest",
    response_model=CellOrchestratorDigestOut,
)
def get_orchestrator_digest(
    cell_id: str,
    system_id: int = Depends(get_system_id),
) -> CellOrchestratorDigestOut:
    with get_conn() as conn:
        row = _get_orchestrator_row(conn, cell_id, system_id)
        digest = cell_orchestrator.build_orchestrator_digest(
            conn, system_id=system_id, cell_row=row,
        )
    return CellOrchestratorDigestOut(system_id=system_id, cell_id=cell_id, digest=digest)


# ---------------------------------------------------------------------------
# Reasoning triage (fail-closed; digest and triage error are always
# returned together so facts survive a triage failure)
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/orchestrators/{cell_id}/triage",
    response_model=CellTriageRunOut,
    status_code=201,
)
def create_orchestrator_triage(
    cell_id: str,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellTriageRunOut:
    with get_conn() as conn:
        row = _get_orchestrator_row(conn, cell_id, system_id)
    # run_triage manages its own connections: the LLM quota wrapper opens
    # get_conn() internally and the DB lock is non-reentrant, so no
    # connection may be held across this call.
    digest, triage_row, is_mock, run_id, error = cell_orchestrator.run_triage(
        system_id=system_id, cell_row=row,
    )
    triage_out = None
    if triage_row is not None:
        with get_conn() as conn:
            triage_out = _triage_out(conn, triage_row)
    return CellTriageRunOut(
        system_id=system_id,
        cell_id=cell_id,
        digest=digest,
        triage=triage_out,
        triage_error=error,
        is_mock=is_mock,
        intelligence_run_id=run_id,
    )


@router.get(
    "/cell-fabric/orchestrators/{cell_id}/triage-results",
    response_model=CellTriageResultsListOut,
)
def list_orchestrator_triage_results(
    cell_id: str,
    system_id: int = Depends(get_system_id),
) -> CellTriageResultsListOut:
    with get_conn() as conn:
        row = _get_orchestrator_row(conn, cell_id, system_id)
        rows = conn.execute(
            """SELECT * FROM cell_triage_results
               WHERE system_id = ? AND cell_definition_id = ?
               ORDER BY id DESC""",
            (system_id, row["id"]),
        ).fetchall()
        results = [_triage_out(conn, r) for r in rows]
    return CellTriageResultsListOut(system_id=system_id, cell_id=cell_id, results=results)


# ---------------------------------------------------------------------------
# Roster updates (the only way to change a roster after creation)
# ---------------------------------------------------------------------------


@router.put(
    "/cell-fabric/cells/{cell_id}/roster",
    response_model=CellRosterEventOut,
)
def update_cell_roster(
    cell_id: str,
    payload: CellRosterUpdateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellRosterEventOut:
    with get_conn() as conn:
        row = _get_cell_row(conn, cell_id, system_id)
        try:
            cell_orchestrator.validate_roster(
                conn, system_id=system_id, cell_id=cell_id, roster=payload.roster,
            )
        except cell_orchestrator.ValidationFailedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        old_roster_json = row["roster_json"]
        new_roster_json = json.dumps(payload.roster)
        now = time.time()
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE cell_definitions SET roster_json = ?, updated_at = ? WHERE id = ?",
                (new_roster_json, now, row["id"]),
            )
            cur = conn.execute(
                """INSERT INTO cell_roster_events
                       (system_id, cell_definition_id, old_roster_json,
                        new_roster_json, changed_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    system_id, row["id"], old_roster_json, new_roster_json,
                    payload.changed_by or principal.username, now,
                ),
            )
            event_id = cur.lastrowid
            event_row = conn.execute(
                "SELECT * FROM cell_roster_events WHERE id = ?", (event_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return _roster_event_out(event_row)
