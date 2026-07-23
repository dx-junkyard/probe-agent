"""Probe Cell Fabric API routes: Cell contract / Role Card / common state
schema (Issue #298, Sub 1 of the Probe Cell Fabric epic, Issue #297).

Only the contract-layer surface is implemented here: versioned Agent Role
Cards, Cell Definitions (worker and orchestrator share one contract, roster
presence is the only distinguisher), and a minimal ``cell_state`` document
built from a Cell Definition alone. Goal/Task ledger persistence, Cell
worker activation, and LLM-generated Role Cards are Issue #300 and later
subs' scope -- not implemented here.

probe-agent:
  role: API boundary for Agent Role Card and Cell Definition lifecycle
  capability: probe-cell-fabric
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify Role Card versions are append-only per (system_id, role_key, version), a Cell can only bind to an ACTIVE Role Card in its own System, and every table stays System-scoped.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, get_system_id, require_user
from ..cell_fabric import (
    AgentRoleCard,
    CellDefinitionContract,
    build_minimal_cell_state,
)
from ..db import get_conn
from ..models import (
    AgentRoleCardOut,
    AgentRoleCardsListOut,
    CellDefinitionOut,
    CellDetailOut,
    CellsListOut,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _role_card_out(row) -> AgentRoleCardOut:
    return AgentRoleCardOut(
        id=row["id"],
        system_id=row["system_id"],
        role_key=row["role_key"],
        version=row["version"],
        status=row["status"],
        mission=row["mission"],
        scope=json.loads(row["scope_json"] or "[]"),
        out_of_scope=json.loads(row["out_of_scope_json"] or "[]"),
        model_alias=row["model_alias"],
        tool_policy=json.loads(row["tool_policy_json"] or "{}"),
        acceptance_template=json.loads(row["acceptance_template_json"] or "[]"),
        rubric_ref=row["rubric_ref"],
        changelog=row["changelog"],
        schema_version=row["schema_version"],
        decision_method=row["decision_method"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _get_role_card_row(conn, card_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM agent_role_cards WHERE id = ? AND system_id = ?",
        (card_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Role card not found")
    return row


def _cell_definition_out(conn, row) -> CellDefinitionOut:
    # Uses the caller's open connection: db.get_conn() holds a non-reentrant
    # lock, so opening a nested connection here would deadlock.
    role_card_row = conn.execute(
        "SELECT role_key, version FROM agent_role_cards WHERE id = ?",
        (row["role_card_id"],),
    ).fetchone()
    roster = (
        json.loads(row["roster_json"]) if row["roster_json"] is not None else None
    )
    return CellDefinitionOut(
        id=row["id"],
        system_id=row["system_id"],
        cell_id=row["cell_id"],
        roster=roster,
        role_card_ref={
            "role_key": role_card_row["role_key"] if role_card_row else "",
            "version": role_card_row["version"] if role_card_row else "",
        },
        status=row["status"],
        mission=row["mission"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_cell_row(conn, cell_id: str, system_id: int):
    row = conn.execute(
        "SELECT * FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
        (cell_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Cell not found")
    return row


def _principal_label(principal: Principal) -> Optional[str]:
    return principal.username


# ---------------------------------------------------------------------------
# Agent Role Cards
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/role-cards",
    response_model=AgentRoleCardOut,
    status_code=201,
)
def create_role_card(
    payload: AgentRoleCard,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> AgentRoleCardOut:
    """Create a new (append-only) Agent Role Card version.

    ``role_key`` + ``version`` is unique per System: a duplicate is a 409,
    never an overwrite of the existing version's content.
    """
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            """SELECT id FROM agent_role_cards
               WHERE system_id = ? AND role_key = ? AND version = ?""",
            (system_id, payload.role_key, payload.version),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Role card {payload.role_key!r} version "
                    f"{payload.version!r} already exists"
                ),
            )
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO agent_role_cards
                       (system_id, role_key, version, status, mission,
                        scope_json, out_of_scope_json, model_alias,
                        tool_policy_json, acceptance_template_json,
                        rubric_ref, changelog, schema_version,
                        decision_method, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'manual', ?, ?)""",
                (
                    system_id, payload.role_key, payload.version,
                    payload.status, payload.mission,
                    json.dumps(payload.scope), json.dumps(payload.out_of_scope),
                    payload.model_alias,
                    json.dumps(payload.tool_policy.model_dump()),
                    json.dumps(payload.acceptance_template),
                    payload.rubric_ref, payload.changelog, payload.schema_version,
                    _principal_label(principal), now,
                ),
            )
            card_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM agent_role_cards WHERE id = ?", (card_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return _role_card_out(row)


@router.get("/cell-fabric/role-cards", response_model=AgentRoleCardsListOut)
def list_role_cards(
    role_key: Optional[str] = None,
    system_id: int = Depends(get_system_id),
) -> AgentRoleCardsListOut:
    with get_conn() as conn:
        if role_key:
            rows = conn.execute(
                """SELECT * FROM agent_role_cards
                   WHERE system_id = ? AND role_key = ? ORDER BY id DESC""",
                (system_id, role_key),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_role_cards WHERE system_id = ? ORDER BY id DESC",
                (system_id,),
            ).fetchall()
    return AgentRoleCardsListOut(
        system_id=system_id,
        role_cards=[_role_card_out(r) for r in rows],
    )


@router.get(
    "/cell-fabric/role-cards/{card_id}",
    response_model=AgentRoleCardOut,
)
def get_role_card(
    card_id: int,
    system_id: int = Depends(get_system_id),
) -> AgentRoleCardOut:
    with get_conn() as conn:
        row = _get_role_card_row(conn, card_id, system_id)
    return _role_card_out(row)


@router.post(
    "/cell-fabric/role-cards/{card_id}/deprecate",
    response_model=AgentRoleCardOut,
)
def deprecate_role_card(
    card_id: int,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> AgentRoleCardOut:
    """Status-only transition to ``deprecated``. Never touches version
    content -- versioned rows stay append-only."""
    now = time.time()
    with get_conn() as conn:
        row = _get_role_card_row(conn, card_id, system_id)
        if row["status"] == "deprecated":
            raise HTTPException(
                status_code=409, detail="Role card is already deprecated",
            )
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE agent_role_cards SET status = 'deprecated' WHERE id = ?",
                (card_id,),
            )
            row = conn.execute(
                "SELECT * FROM agent_role_cards WHERE id = ?", (card_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return _role_card_out(row)


# ---------------------------------------------------------------------------
# Cell Definitions
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/cells",
    response_model=CellDefinitionOut,
    status_code=201,
)
def create_cell(
    payload: CellDefinitionContract,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellDefinitionOut:
    """Create a Cell Definition. ``role_card_ref`` must resolve to an
    existing ACTIVE Role Card version in the same System; ``cell_id`` must be
    unique per System. Roster distinctness is already enforced by the
    ``CellDefinitionContract`` request model itself."""
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM cell_definitions WHERE system_id = ? AND cell_id = ?",
            (system_id, payload.cell_id),
        ).fetchone()
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Cell {payload.cell_id!r} already exists in this System",
            )
        card_row = conn.execute(
            """SELECT * FROM agent_role_cards
               WHERE system_id = ? AND role_key = ? AND version = ?""",
            (system_id, payload.role_card_ref.role_key, payload.role_card_ref.version),
        ).fetchone()
        if card_row is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Role card {payload.role_card_ref.role_key!r} version "
                    f"{payload.role_card_ref.version!r} not found in this System"
                ),
            )
        if card_row["status"] != "active":
            raise HTTPException(
                status_code=400,
                detail="role_card_ref must resolve to an ACTIVE role card",
            )
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO cell_definitions
                       (system_id, cell_id, roster_json, role_card_id, status,
                        mission, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, payload.cell_id,
                    json.dumps(payload.roster) if payload.roster is not None else None,
                    card_row["id"], payload.status, payload.mission or "",
                    now, now,
                ),
            )
            cell_row_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM cell_definitions WHERE id = ?", (cell_row_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return _cell_definition_out(conn, row)


@router.get("/cell-fabric/cells", response_model=CellsListOut)
def list_cells(
    system_id: int = Depends(get_system_id),
) -> CellsListOut:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM cell_definitions WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        cells = [_cell_definition_out(conn, r) for r in rows]
    return CellsListOut(system_id=system_id, cells=cells)


@router.get(
    "/cell-fabric/cells/{cell_id}",
    response_model=CellDetailOut,
)
def get_cell(
    cell_id: str,
    system_id: int = Depends(get_system_id),
) -> CellDetailOut:
    """Return the Cell Definition plus a minimal ``cell_state`` document
    built from the definition alone -- tasks/health/quality/improvement stay
    empty/null at this phase (Issue #299 fills ``health`` from real
    Trace/Evaluation/Shadow/Replay/Experiment facts)."""
    with get_conn() as conn:
        row = _get_cell_row(conn, cell_id, system_id)
        card_row = conn.execute(
            "SELECT * FROM agent_role_cards WHERE id = ?", (row["role_card_id"],),
        ).fetchone()
        definition = _cell_definition_out(conn, row)
    roster = json.loads(row["roster_json"]) if row["roster_json"] is not None else None
    state = build_minimal_cell_state(
        cell_id=row["cell_id"],
        role_key=card_row["role_key"],
        role_version=card_row["version"],
        model_alias=card_row["model_alias"],
        mission=row["mission"] or card_row["mission"],
        roster=roster,
    )
    return CellDetailOut(definition=definition, state=state.model_dump())
