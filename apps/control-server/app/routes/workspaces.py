"""Decision Workspace persistence and CRUD API (Issue #35).

This module only stores conversation turns, attached context references,
structured proposals, and human decisions. It never calls an LLM and never
changes a proposal's status except through the explicit accept/reject
endpoints below.
"""

from __future__ import annotations

import json
import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, get_principal, get_system_id
from ..db import get_conn
from ..models import (
    WorkspaceContextItemCreate,
    WorkspaceContextItemOut,
    WorkspaceContextPack,
    WorkspaceCreate,
    WorkspaceDecisionCreate,
    WorkspaceDecisionOut,
    WorkspaceDetailOut,
    WorkspaceMessageCreate,
    WorkspaceMessageOut,
    WorkspaceOut,
    WorkspaceProposalOut,
    WorkspaceProposalUpdate,
)
from ..workspace_context import build_context_pack

router = APIRouter()


def _message_out(row) -> WorkspaceMessageOut:
    return WorkspaceMessageOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        role=row["role"],
        content=row["content"],
        context_metadata=json.loads(row["context_metadata"] or "{}"),
        created_at=row["created_at"],
    )


def _context_item_out(row) -> WorkspaceContextItemOut:
    return WorkspaceContextItemOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        item_type=row["item_type"],
        item_id=row["item_id"],
        label=row["label"],
        created_at=row["created_at"],
    )


def _decision_out(row) -> WorkspaceDecisionOut:
    return WorkspaceDecisionOut(
        id=row["id"],
        proposal_id=row["proposal_id"],
        decision=row["decision"],
        reason=row["reason"],
        decided_by_user_id=row["decided_by_user_id"],
        created_at=row["created_at"],
    )


def _proposal_out(conn, row) -> WorkspaceProposalOut:
    decision_rows = conn.execute(
        "SELECT * FROM workspace_decisions WHERE proposal_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    return WorkspaceProposalOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        message_id=row["message_id"],
        proposal_type=row["proposal_type"],
        title=row["title"],
        body=json.loads(row["body"] or "{}"),
        status=row["status"],
        decisions=[_decision_out(d) for d in decision_rows],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _workspace_out(row) -> WorkspaceOut:
    return WorkspaceOut(
        id=row["id"],
        system_id=row["system_id"],
        title=row["title"],
        focus=row["focus"],
        status=row["status"],
        summary=row["summary"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_workspace_or_404(conn, workspace_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM workspaces WHERE id = ? AND system_id = ?",
        (workspace_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return row


def _get_proposal_or_404(conn, workspace_id: int, proposal_id: int, system_id: int):
    row = conn.execute(
        """
        SELECT * FROM workspace_proposals
        WHERE id = ? AND workspace_id = ? AND system_id = ?
        """,
        (proposal_id, workspace_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return row


@router.get("/workspaces", response_model=List[WorkspaceOut])
def list_workspaces(system_id: int = Depends(get_system_id)) -> List[WorkspaceOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM workspaces WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        return [_workspace_out(row) for row in rows]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    system_id: int = Depends(get_system_id),
) -> WorkspaceOut:
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO workspaces (system_id, title, focus, status, summary,
                                     created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?, ?)
            """,
            (system_id, payload.title, payload.focus, payload.summary, now, now),
        )
        row = _get_workspace_or_404(conn, cur.lastrowid, system_id)
        return _workspace_out(row)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceDetailOut)
def get_workspace(
    workspace_id: int,
    system_id: int = Depends(get_system_id),
) -> WorkspaceDetailOut:
    with get_conn() as conn:
        row = _get_workspace_or_404(conn, workspace_id, system_id)
        message_rows = conn.execute(
            "SELECT * FROM workspace_messages WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        ).fetchall()
        context_rows = conn.execute(
            "SELECT * FROM workspace_context_items WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        ).fetchall()
        proposal_rows = conn.execute(
            "SELECT * FROM workspace_proposals WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        ).fetchall()
        return WorkspaceDetailOut(
            **_workspace_out(row).model_dump(),
            messages=[_message_out(m) for m in message_rows],
            context_items=[_context_item_out(c) for c in context_rows],
            proposals=[_proposal_out(conn, p) for p in proposal_rows],
        )


@router.get(
    "/workspaces/{workspace_id}/context-pack",
    response_model=WorkspaceContextPack,
)
def get_workspace_context_pack(
    workspace_id: int,
    system_id: int = Depends(get_system_id),
) -> WorkspaceContextPack:
    """Deterministic, no-LLM preview of the context that would be handed to
    the assistant for this workspace (Issue #36). Built only from the
    workspace's pinned context items -- never the full system dataset."""
    with get_conn() as conn:
        workspace = _get_workspace_or_404(conn, workspace_id, system_id)
        context_items = conn.execute(
            "SELECT * FROM workspace_context_items WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        ).fetchall()
        return build_context_pack(conn, system_id, workspace, context_items)


@router.post(
    "/workspaces/{workspace_id}/messages",
    response_model=WorkspaceMessageOut,
    status_code=201,
)
def create_workspace_message(
    workspace_id: int,
    payload: WorkspaceMessageCreate,
    system_id: int = Depends(get_system_id),
) -> WorkspaceMessageOut:
    now = time.time()
    with get_conn() as conn:
        _get_workspace_or_404(conn, workspace_id, system_id)
        cur = conn.execute(
            """
            INSERT INTO workspace_messages
                (workspace_id, system_id, role, content, context_metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                system_id,
                payload.role,
                payload.content,
                json.dumps(payload.context_metadata, ensure_ascii=False),
                now,
            ),
        )
        message_id = cur.lastrowid
        for proposal in payload.proposals:
            conn.execute(
                """
                INSERT INTO workspace_proposals
                    (workspace_id, system_id, message_id, proposal_type, title,
                     body, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
                """,
                (
                    workspace_id,
                    system_id,
                    message_id,
                    proposal.proposal_type,
                    proposal.title,
                    json.dumps(proposal.body, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        conn.execute(
            "UPDATE workspaces SET updated_at = ? WHERE id = ?",
            (now, workspace_id),
        )
        row = conn.execute(
            "SELECT * FROM workspace_messages WHERE id = ?", (message_id,)
        ).fetchone()
        return _message_out(row)


@router.post(
    "/workspaces/{workspace_id}/context",
    response_model=WorkspaceContextItemOut,
    status_code=201,
)
def add_workspace_context_item(
    workspace_id: int,
    payload: WorkspaceContextItemCreate,
    system_id: int = Depends(get_system_id),
) -> WorkspaceContextItemOut:
    now = time.time()
    with get_conn() as conn:
        _get_workspace_or_404(conn, workspace_id, system_id)
        existing = conn.execute(
            """
            SELECT * FROM workspace_context_items
            WHERE workspace_id = ? AND item_type = ? AND item_id = ?
            """,
            (workspace_id, payload.item_type, payload.item_id),
        ).fetchone()
        if existing is not None:
            return _context_item_out(existing)
        cur = conn.execute(
            """
            INSERT INTO workspace_context_items
                (workspace_id, system_id, item_type, item_id, label, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                system_id,
                payload.item_type,
                payload.item_id,
                payload.label,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM workspace_context_items WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _context_item_out(row)


@router.delete(
    "/workspaces/{workspace_id}/context/{context_item_id}",
    status_code=204,
)
def delete_workspace_context_item(
    workspace_id: int,
    context_item_id: int,
    system_id: int = Depends(get_system_id),
) -> None:
    with get_conn() as conn:
        _get_workspace_or_404(conn, workspace_id, system_id)
        row = conn.execute(
            """
            SELECT id FROM workspace_context_items
            WHERE id = ? AND workspace_id = ? AND system_id = ?
            """,
            (context_item_id, workspace_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Context item not found")
        conn.execute(
            "DELETE FROM workspace_context_items WHERE id = ?", (context_item_id,)
        )


@router.patch(
    "/workspaces/{workspace_id}/proposals/{proposal_id}",
    response_model=WorkspaceProposalOut,
)
def update_workspace_proposal(
    workspace_id: int,
    proposal_id: int,
    payload: WorkspaceProposalUpdate,
    system_id: int = Depends(get_system_id),
) -> WorkspaceProposalOut:
    now = time.time()
    with get_conn() as conn:
        proposal = _get_proposal_or_404(conn, workspace_id, proposal_id, system_id)
        if proposal["status"] != "proposed":
            raise HTTPException(
                status_code=409,
                detail="Only a proposed proposal can be edited",
            )
        title = proposal["title"] if payload.title is None else payload.title
        body = (
            proposal["body"]
            if payload.body is None
            else json.dumps(payload.body, ensure_ascii=False)
        )
        conn.execute(
            """
            UPDATE workspace_proposals
            SET title = ?, body = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, body, now, proposal_id),
        )
        row = _get_proposal_or_404(conn, workspace_id, proposal_id, system_id)
        return _proposal_out(conn, row)


def _resolve_decision(
    conn,
    workspace_id: int,
    proposal_id: int,
    system_id: int,
    target_status: str,
    decision_type: str,
    payload: WorkspaceDecisionCreate,
    principal: Principal,
) -> WorkspaceProposalOut:
    now = time.time()
    proposal = _get_proposal_or_404(conn, workspace_id, proposal_id, system_id)
    if proposal["status"] == target_status:
        # Idempotent re-request: do not duplicate decision history.
        return _proposal_out(conn, proposal)
    if proposal["status"] != "proposed":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is already {proposal['status']} and cannot be {decision_type}",
        )
    conn.execute(
        """
        INSERT INTO workspace_decisions
            (proposal_id, workspace_id, system_id, decision, reason,
             decided_by_user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proposal_id,
            workspace_id,
            system_id,
            decision_type,
            payload.reason,
            principal.user_id,
            now,
        ),
    )
    conn.execute(
        "UPDATE workspace_proposals SET status = ?, updated_at = ? WHERE id = ?",
        (target_status, now, proposal_id),
    )
    row = _get_proposal_or_404(conn, workspace_id, proposal_id, system_id)
    return _proposal_out(conn, row)


@router.post(
    "/workspaces/{workspace_id}/proposals/{proposal_id}/accept",
    response_model=WorkspaceProposalOut,
)
def accept_workspace_proposal(
    workspace_id: int,
    proposal_id: int,
    payload: WorkspaceDecisionCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> WorkspaceProposalOut:
    with get_conn() as conn:
        _get_workspace_or_404(conn, workspace_id, system_id)
        return _resolve_decision(
            conn, workspace_id, proposal_id, system_id, "accepted", "accepted",
            payload, principal,
        )


@router.post(
    "/workspaces/{workspace_id}/proposals/{proposal_id}/reject",
    response_model=WorkspaceProposalOut,
)
def reject_workspace_proposal(
    workspace_id: int,
    proposal_id: int,
    payload: WorkspaceDecisionCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> WorkspaceProposalOut:
    with get_conn() as conn:
        _get_workspace_or_404(conn, workspace_id, system_id)
        return _resolve_decision(
            conn, workspace_id, proposal_id, system_id, "rejected", "rejected",
            payload, principal,
        )
