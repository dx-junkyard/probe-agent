"""Probe Cell Fabric API routes: Cell contract / Role Card / common state
schema (Issue #298, Sub 1) plus versioned Cell Binding and the read-only
Probe Cell pilot (Issue #299, Sub 2) of the Probe Cell Fabric epic
(Issue #297).

Sub 1 (unchanged): versioned Agent Role Cards, Cell Definitions (worker and
orchestrator share one contract, roster presence is the only distinguisher),
and a minimal ``cell_state`` document built from a Cell Definition alone.

Sub 2 adds: creating/listing versioned Cell Bindings from an approved Probe
Point or a Probe Pattern point, deterministic drift re-evaluation against
the latest ready snapshot, explicit/aggregation-window activation records,
and a read-only Cell state endpoint (health + current binding + recent
activations -- no LLM call anywhere on this path). Goal/Task ledger
persistence, Cell worker execution, and LLM-generated Role Cards remain
Issue #300 and later subs' scope -- not implemented here.

probe-agent:
  role: API boundary for Agent Role Card / Cell Definition / Cell Binding / Cell Activation lifecycle
  capability: probe-cell-fabric
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify Role Card versions are append-only per (system_id, role_key, version), a Cell can only bind to an ACTIVE Role Card in its own System, a Cell Binding can only be created from an approved Probe Point or a Probe Pattern point, binding versions are append-only with the prior version superseded, drift evaluation is purely structural, and every table stays System-scoped.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import cell_binding
from ..auth import Principal, get_system_id, require_user
from ..cell_fabric import (
    AgentRoleCard,
    CellDefinitionContract,
)
from ..db import get_conn
from ..state_facts import get_latest_ready_snapshot_id
from ..models import (
    AgentRoleCardOut,
    AgentRoleCardsListOut,
    CellActivationCreateIn,
    CellActivationOut,
    CellActivationsListOut,
    CellBindingCreateIn,
    CellBindingOut,
    CellBindingsListOut,
    CellDefinitionOut,
    CellDetailOut,
    CellsListOut,
    CellStateOut,
)

router = APIRouter()

_BINDING_ERROR_STATUS = {
    cell_binding.CellBindingNotFoundError: 404,
    cell_binding.CellBindingConflictError: 409,
    cell_binding.CellBindingValidationError: 422,
}


def _raise_binding_error(exc: cell_binding.CellBindingError) -> None:
    for exc_type, status_code in _BINDING_ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    """Return the Cell Definition plus its ``cell_state`` document, health
    filled in from real Trace/Cell-Activation facts (Issue #299). No LLM
    call is made anywhere on this path."""
    with get_conn() as conn:
        row = _get_cell_row(conn, cell_id, system_id)
        card_row = conn.execute(
            "SELECT * FROM agent_role_cards WHERE id = ?", (row["role_card_id"],),
        ).fetchone()
        definition = _cell_definition_out(conn, row)
        state = cell_binding.build_cell_state(conn, system_id=system_id, cell_row=row, card_row=card_row)
    return CellDetailOut(definition=definition, state=state.model_dump())


# ---------------------------------------------------------------------------
# Cell Bindings (Issue #299, Sub 2)
# ---------------------------------------------------------------------------


def _binding_out(row) -> CellBindingOut:
    return CellBindingOut(
        id=row["id"],
        system_id=row["system_id"],
        cell_definition_id=row["cell_definition_id"],
        cell_id=row["component_id"],
        version=row["version"],
        snapshot_id=row["snapshot_id"],
        commit_sha=row["commit_sha"],
        path=row["path"],
        qualified_symbol=row["qualified_symbol"],
        component_id=row["component_id"],
        probe_point_id=row["probe_point_id"],
        probe_pattern_id=row["probe_pattern_id"],
        feature_refs=json.loads(row["feature_refs_json"] or "[]"),
        capability_refs=json.loads(row["capability_refs_json"] or "[]"),
        entrypoint_refs=json.loads(row["entrypoint_refs_json"] or "[]"),
        status=row["status"],
        status_reason=row["status_reason"],
        created_at=row["created_at"],
    )


def _activation_out(row, cell_id: str) -> CellActivationOut:
    return CellActivationOut(
        id=row["id"],
        system_id=row["system_id"],
        cell_definition_id=row["cell_definition_id"],
        cell_id=cell_id,
        trigger_kind=row["trigger_kind"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        requested_by=row["requested_by"],
        used_llm=bool(row["used_llm"]),
        intelligence_run_id=row["intelligence_run_id"],
        status=row["status"],
        detail=row["detail"],
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _latest_binding_row(conn, *, system_id: int, cell_definition_id: int):
    return conn.execute(
        """SELECT * FROM cell_bindings
           WHERE system_id = ? AND cell_definition_id = ?
           ORDER BY version DESC LIMIT 1""",
        (system_id, cell_definition_id),
    ).fetchone()


def _current_binding_row(conn, *, system_id: int, cell_definition_id: int):
    """The binding row currently considered "the" binding: the latest
    non-superseded row if one exists, else the latest version of any
    status (e.g. all versions superseded)."""
    row = conn.execute(
        """SELECT * FROM cell_bindings
           WHERE system_id = ? AND cell_definition_id = ? AND status != 'superseded'
           ORDER BY version DESC LIMIT 1""",
        (system_id, cell_definition_id),
    ).fetchone()
    if row is not None:
        return row
    return _latest_binding_row(conn, system_id=system_id, cell_definition_id=cell_definition_id)


@router.post(
    "/cell-fabric/cells/{cell_id}/bindings",
    response_model=CellBindingOut,
    status_code=201,
)
def create_cell_binding(
    cell_id: str,
    payload: CellBindingCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellBindingOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            row = cell_binding.create_binding(
                conn,
                system_id=system_id,
                cell_definition_id=cell_row["id"],
                probe_point_id=payload.probe_point_id,
                probe_pattern_point_id=payload.probe_pattern_point_id,
                feature_refs=payload.feature_refs,
                capability_refs=payload.capability_refs,
                entrypoint_refs=payload.entrypoint_refs,
            )
        except cell_binding.CellBindingError as exc:
            _raise_binding_error(exc)
    return _binding_out(row)


@router.get(
    "/cell-fabric/cells/{cell_id}/bindings",
    response_model=CellBindingsListOut,
)
def list_cell_bindings(
    cell_id: str,
    system_id: int = Depends(get_system_id),
) -> CellBindingsListOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        rows = conn.execute(
            """SELECT * FROM cell_bindings
               WHERE system_id = ? AND cell_definition_id = ?
               ORDER BY version DESC""",
            (system_id, cell_row["id"]),
        ).fetchall()
    return CellBindingsListOut(
        system_id=system_id, cell_id=cell_id,
        bindings=[_binding_out(r) for r in rows],
    )


@router.post(
    "/cell-fabric/cells/{cell_id}/bindings/refresh-drift",
    response_model=CellBindingOut,
)
def refresh_cell_binding_drift(
    cell_id: str,
    system_id: int = Depends(get_system_id),
) -> CellBindingOut:
    """Re-evaluate the Cell's current binding against the latest ready
    snapshot's ``code_symbols`` (purely structural, Principle 6). Never
    creates a new binding version and never changes ``path`` /
    ``qualified_symbol`` -- only ``status``/``status_reason`` may change."""
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        binding_row = _current_binding_row(conn, system_id=system_id, cell_definition_id=cell_row["id"])
        if binding_row is None:
            raise HTTPException(status_code=404, detail="Cell has no binding yet")
        if binding_row["status"] == "superseded":
            raise HTTPException(
                status_code=409,
                detail="Cell's latest binding version has already been superseded",
            )
        latest_ready_snapshot_id = get_latest_ready_snapshot_id(conn, system_id)
        new_status, reason = cell_binding.evaluate_binding_drift(
            conn, system_id=system_id, binding_row=binding_row,
            latest_ready_snapshot_id=latest_ready_snapshot_id,
        )
        conn.execute("BEGIN")
        try:
            conn.execute(
                "UPDATE cell_bindings SET status = ?, status_reason = ? WHERE id = ?",
                (new_status, reason, binding_row["id"]),
            )
            row = conn.execute(
                "SELECT * FROM cell_bindings WHERE id = ?", (binding_row["id"],),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return _binding_out(row)


# ---------------------------------------------------------------------------
# Cell Activations (Issue #299, Sub 2)
# ---------------------------------------------------------------------------


@router.post(
    "/cell-fabric/cells/{cell_id}/activations",
    response_model=CellActivationOut,
    status_code=201,
)
def create_cell_activation(
    cell_id: str,
    payload: CellActivationCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> CellActivationOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        try:
            row = cell_binding.create_activation(
                conn,
                system_id=system_id,
                cell_definition_id=cell_row["id"],
                window_start=payload.window_start,
                window_end=payload.window_end,
                requested_by=payload.requested_by or _principal_label(principal),
            )
        except cell_binding.CellBindingError as exc:
            _raise_binding_error(exc)
    return _activation_out(row, cell_id)


@router.get(
    "/cell-fabric/cells/{cell_id}/activations",
    response_model=CellActivationsListOut,
)
def list_cell_activations(
    cell_id: str,
    system_id: int = Depends(get_system_id),
) -> CellActivationsListOut:
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        rows = conn.execute(
            """SELECT * FROM cell_activations
               WHERE system_id = ? AND cell_definition_id = ?
               ORDER BY id DESC""",
            (system_id, cell_row["id"]),
        ).fetchall()
    return CellActivationsListOut(
        system_id=system_id, cell_id=cell_id,
        activations=[_activation_out(r, cell_id) for r in rows],
    )


# ---------------------------------------------------------------------------
# Cell state (read-only pilot; Issue #299, Sub 2)
# ---------------------------------------------------------------------------


@router.get(
    "/cell-fabric/cells/{cell_id}/state",
    response_model=CellStateOut,
)
def get_cell_state(
    cell_id: str,
    system_id: int = Depends(get_system_id),
) -> CellStateOut:
    """Read-only Probe Cell pilot state: ``cell_state`` (health filled in)
    plus the current binding and recent activations. Deterministic only --
    no LLM call is made anywhere on this path."""
    with get_conn() as conn:
        cell_row = _get_cell_row(conn, cell_id, system_id)
        card_row = conn.execute(
            "SELECT * FROM agent_role_cards WHERE id = ?", (cell_row["role_card_id"],),
        ).fetchone()
        state = cell_binding.build_cell_state(conn, system_id=system_id, cell_row=cell_row, card_row=card_row)
        binding_row = _current_binding_row(conn, system_id=system_id, cell_definition_id=cell_row["id"])
        activation_rows = conn.execute(
            """SELECT * FROM cell_activations
               WHERE system_id = ? AND cell_definition_id = ?
               ORDER BY id DESC LIMIT 20""",
            (system_id, cell_row["id"]),
        ).fetchall()
    return CellStateOut(
        cell_id=cell_id,
        state=state.model_dump(),
        binding=_binding_out(binding_row) if binding_row is not None else None,
        recent_activations=[_activation_out(r, cell_id) for r in activation_rows],
    )
