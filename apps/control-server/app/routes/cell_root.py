"""Probe Cell Fabric API routes: Root Orchestrator と統合ダイジェスト (Issue
#303, Sub 5 of the Probe Cell Fabric epic, Issue #297).

Routes are thin: the deterministic digest builder and Ask lifecycle live in
``app/cell_root.py``; this module only resolves the System, calls the core
function, translates ``app.cell_root`` errors to HTTP status codes
(``NotFoundError`` -> 404, ``ConflictError`` -> 409,
``ValidationFailedError`` -> 422), and shapes the response models.

``GET /cell-fabric/root-digest`` is the one endpoint in this module that
needs two separate connections rather than one: ``system_state.build_system_state``
manages its own ``get_conn()`` internally, and ``db.get_conn()``'s lock is
non-reentrant, so it MUST be called before this route opens its own
connection for ``cell_root.build_root_digest``'s Cell-Fabric reads -- never
nested inside that connection's ``with`` block.

There is NO LLM call anywhere on any path in this module (the digest is pure
aggregation; the one upstream reasoning step, cell_triage, already ran in
#301 by the time its ``proposed_ask`` is read here).

probe-agent:
  role: API boundary for the Root Orchestrator digest and Ask lifecycle
  capability: probe-cell-fabric
  element_type: boundary
  consumers: [dashboard]
  operation_kind: mixed
  state_effects: [database-read, database-write]
  probe_value: Verify the digest endpoint never calls an LLM, Ask sync is idempotent across repeated calls, deciding an already-decided Ask returns 409, an unknown decision returns 422, and execution_approved stays 0 after every decision path.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import cell_root, cell_tasks
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    CellAskDecideRequest,
    CellAskOut,
    CellAskSyncOut,
    CellAsksListOut,
    CellRootDigestOut,
)
from ..system_state import build_system_state

router = APIRouter()


def _translate(exc: cell_root.CellRootError) -> HTTPException:
    if isinstance(exc, cell_root.NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, cell_root.ConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


def _ask_out(conn, row) -> CellAskOut:
    return CellAskOut(
        id=row["id"],
        system_id=row["system_id"],
        source_kind=row["source_kind"],
        source_id=row["source_id"],
        cell_definition_id=cell_tasks.cell_business_id(conn, row["cell_definition_id"]),
        goal_id=row["goal_id"],
        task_id=row["task_id"],
        ask_text=row["ask_text"],
        severity=row["severity"],
        status=row["status"],
        decision=row["decision"] or "",
        decision_method=row["decision_method"] or "",
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        execution_approved=bool(row["execution_approved"]),
        dedupe_key=row["dedupe_key"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Root digest (deterministic; no LLM call anywhere on this path)
# ---------------------------------------------------------------------------


@router.get("/cell-fabric/root-digest", response_model=CellRootDigestOut)
def get_root_digest(system_id: int = Depends(get_system_id)) -> CellRootDigestOut:
    # build_system_state manages its own get_conn() -- must run BEFORE the
    # connection below is opened (db.get_conn()'s lock is non-reentrant).
    assessment = build_system_state(system_id)
    with get_conn() as conn:
        digest = cell_root.build_root_digest(
            conn, system_id=system_id, system_state_assessment=assessment,
        )
    return CellRootDigestOut(
        system_id=system_id, digest=digest, generated_at=digest["generated_at"],
    )


# ---------------------------------------------------------------------------
# Ask lifecycle
# ---------------------------------------------------------------------------


@router.post("/cell-fabric/asks/sync", response_model=CellAskSyncOut)
def sync_asks(system_id: int = Depends(get_system_id)) -> CellAskSyncOut:
    with get_conn() as conn:
        result = cell_root.sync_asks_from_sources(conn, system_id=system_id)
    return CellAskSyncOut(system_id=system_id, created=result["created"], deduped=result["deduped"])


@router.get("/cell-fabric/asks", response_model=CellAsksListOut)
def list_asks(
    status: Optional[str] = None, system_id: int = Depends(get_system_id),
) -> CellAsksListOut:
    with get_conn() as conn:
        rows = cell_root.list_asks(conn, system_id=system_id, status=status)
        asks = [_ask_out(conn, r) for r in rows]
    return CellAsksListOut(system_id=system_id, asks=asks)


@router.post("/cell-fabric/asks/{ask_id}/decide", response_model=CellAskOut)
def decide_ask(
    ask_id: int,
    payload: CellAskDecideRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellAskOut:
    with get_conn() as conn:
        try:
            row = cell_root.decide_ask(
                conn,
                system_id=system_id,
                ask_id=ask_id,
                decision=payload.decision,
                decided_by=principal.username,
                note=payload.note,
            )
            out = _ask_out(conn, row)
        except cell_root.CellRootError as exc:
            raise _translate(exc)
    return out
