"""Decision Workspace persistence and CRUD API (Issue #35), plus the
structured LLM agent-turn endpoint (Issue #37).

The CRUD endpoints below only store conversation turns, attached context
references, structured proposals, and human decisions; they never call an
LLM and never change a proposal's status except through the explicit
accept/reject endpoints. The `/agent-turns` endpoint is the one path that
calls a reasoning-model LLM, and it does so only through `workspace_agent`,
never directly.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from ..auth import Principal, get_principal, get_system_id
from ..db import get_conn
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import (
    WorkspaceAgentTurnCreate,
    WorkspaceAgentTurnOut,
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
    WorkspaceProposalDraftOut,
    WorkspaceProposalOut,
    WorkspaceProposalUpdate,
)
from ..workspace_actions import UnsupportedProposalTypeError, build_proposal_draft
from ..workspace_agent import (
    PROPOSAL_BODY_MODELS,
    AgentTurnResult,
    generate_agent_turn,
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


def _proposal_draft_out(row) -> WorkspaceProposalDraftOut:
    return WorkspaceProposalDraftOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        proposal_id=row["proposal_id"],
        system_id=row["system_id"],
        draft_type=row["draft_type"],
        target_screen=row["target_screen"],
        payload=json.loads(row["payload"] or "{}"),
        missing_fields=json.loads(row["missing_fields"] or "[]"),
        created_at=row["created_at"],
    )


def _get_workspace_or_404(conn, workspace_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM workspaces WHERE id = ? AND system_id = ?",
        (workspace_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return row


def _pin_context_item(
    conn,
    workspace_id: int,
    system_id: int,
    item_type: str,
    item_id: str,
    label: str,
    now: float,
) -> None:
    """Idempotently attach a context reference to a workspace."""
    existing = conn.execute(
        """
        SELECT id FROM workspace_context_items
        WHERE workspace_id = ? AND item_type = ? AND item_id = ?
        """,
        (workspace_id, item_type, item_id),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO workspace_context_items
            (workspace_id, system_id, item_type, item_id, label, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (workspace_id, system_id, item_type, item_id, label, now),
    )


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


def _validate_context_ref(
    conn,
    system_id: int,
    item_type: str,
    item_id: str,
) -> None:
    if item_type == "feature":
        row = conn.execute(
            """SELECT 1 FROM feature_drafts
               WHERE system_id = ? AND feature_id = ? LIMIT 1""",
            (system_id, item_id),
        ).fetchone()
    elif item_type in {"component", "trace"}:
        row = conn.execute(
            """SELECT 1 FROM components
               WHERE system_id = ? AND component_id = ?
               UNION
               SELECT 1 FROM component_profiles
               WHERE system_id = ? AND component_id = ?
               LIMIT 1""",
            (system_id, item_id, system_id, item_id),
        ).fetchone()
    elif item_type == "experiment":
        row = (
            conn.execute(
                "SELECT 1 FROM experiments WHERE system_id = ? AND id = ?",
                (system_id, int(item_id)),
            ).fetchone()
            if item_id.isdigit()
            else None
        )
    elif item_type == "probe_plan":
        row = (
            conn.execute(
                "SELECT 1 FROM probe_plans WHERE system_id = ? AND id = ?",
                (system_id, int(item_id)),
            ).fetchone()
            if item_id.isdigit()
            else None
        )
    else:  # The Pydantic input model should make this unreachable.
        row = None
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"{item_type} context item not found for this system",
        )


def _validated_proposal_body(proposal_type: str, body: dict) -> dict:
    body_model = PROPOSAL_BODY_MODELS.get(proposal_type)
    if body_model is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported workspace proposal type: {proposal_type}",
        )
    try:
        return body_model.model_validate(body).model_dump()
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {proposal_type} proposal body: {exc}",
        ) from exc


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


def _resolve_intelligence_llm_config() -> LLMConfig:
    config = LLMConfig.from_env()
    intelligence_provider = os.getenv("INTELLIGENCE_LLM_PROVIDER", "").strip()
    intelligence_model = os.getenv("INTELLIGENCE_LLM_MODEL", "").strip()
    if intelligence_provider or intelligence_model:
        config = replace(
            config,
            provider=intelligence_provider or config.provider,
            model=intelligence_model or config.model,
        )
    return config


@router.post(
    "/workspaces/{workspace_id}/agent-turns",
    response_model=WorkspaceAgentTurnOut,
    status_code=201,
)
def create_workspace_agent_turn(
    workspace_id: int,
    payload: WorkspaceAgentTurnCreate,
    system_id: int = Depends(get_system_id),
) -> WorkspaceAgentTurnOut:
    """Pin requested context, persist the user's message, run the
    structured Decision Workspace dialogue against the deterministic
    Context Pack (Issue #36), and persist the assistant turn and any
    proposals only if the LLM's structured response passes validation
    (Issue #37). On any failure, only the user's message is persisted --
    no partial assistant message or proposal is ever stored."""
    now = time.time()
    with get_conn() as conn:
        workspace = _get_workspace_or_404(conn, workspace_id, system_id)
        for ref in payload.context_refs:
            _validate_context_ref(conn, system_id, ref.type, ref.id)
        for ref in payload.context_refs:
            _pin_context_item(conn, workspace_id, system_id, ref.type, ref.id, "", now)

        history_rows = conn.execute(
            "SELECT * FROM workspace_messages WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        ).fetchall()
        history = [{"role": row["role"], "content": row["content"]} for row in history_rows]

        cur = conn.execute(
            """
            INSERT INTO workspace_messages
                (workspace_id, system_id, role, content, context_metadata, created_at)
            VALUES (?, ?, 'user', ?, ?, ?)
            """,
            (
                workspace_id,
                system_id,
                payload.message,
                json.dumps(
                    {
                        "requested_context_refs": [
                            ref.model_dump() for ref in payload.context_refs
                        ]
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        user_message_row = conn.execute(
            "SELECT * FROM workspace_messages WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        user_message_out = _message_out(user_message_row)

        context_items = conn.execute(
            "SELECT * FROM workspace_context_items WHERE workspace_id = ? ORDER BY id",
            (workspace_id,),
        ).fetchall()
        context_pack = build_context_pack(conn, system_id, workspace, context_items)
        workspace_summary = workspace["summary"] or ""

    config = _resolve_intelligence_llm_config()
    try:
        client = create_llm_client(config)
    except LLMError as exc:
        result = AgentTurnResult(provider=config.provider, model=config.model, is_mock=False, error=str(exc))
    else:
        result = generate_agent_turn(
            client,
            config,
            context_pack=context_pack,
            workspace_summary=workspace_summary,
            history=history,
            user_message=payload.message,
        )

    if result.error:
        return WorkspaceAgentTurnOut(
            user_message=user_message_out,
            assistant_message=None,
            proposals=[],
            error=result.error,
        )

    now = time.time()
    with get_conn() as conn:
        _get_workspace_or_404(conn, workspace_id, system_id)
        conn.execute("BEGIN")
        try:
            assistant_metadata = {
                "grounded_findings": [
                    {
                        "claim": f.claim,
                        "source_type": f.source_type,
                        "source_id": f.source_id,
                    }
                    for f in result.grounded_findings
                ],
                "assumptions": result.assumptions,
                "missing_information": result.missing_information,
                "next_questions": result.next_questions,
                "used_context": [
                    ref.model_dump(mode="json") for ref in context_pack.evidence
                ],
                "context_missing_information": context_pack.missing_information,
                "provider": result.provider,
                "model": result.model,
            }
            cur = conn.execute(
                """
                INSERT INTO workspace_messages
                    (workspace_id, system_id, role, content, context_metadata, created_at)
                VALUES (?, ?, 'assistant', ?, ?, ?)
                """,
                (
                    workspace_id,
                    system_id,
                    result.assistant_message,
                    json.dumps(assistant_metadata, ensure_ascii=False),
                    now,
                ),
            )
            assistant_message_id = cur.lastrowid
            proposal_outs: List[WorkspaceProposalOut] = []
            for proposal in result.proposals:
                body = _validated_proposal_body(
                    proposal.proposal_type, proposal.body
                )
                cur = conn.execute(
                    """
                    INSERT INTO workspace_proposals
                        (workspace_id, system_id, message_id, proposal_type, title,
                         body, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
                    """,
                    (
                        workspace_id,
                        system_id,
                        assistant_message_id,
                        proposal.proposal_type,
                        proposal.title,
                        json.dumps(body, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                proposal_row = conn.execute(
                    "SELECT * FROM workspace_proposals WHERE id = ?", (cur.lastrowid,)
                ).fetchone()
                proposal_outs.append(_proposal_out(conn, proposal_row))

            assistant_message_row = conn.execute(
                "SELECT * FROM workspace_messages WHERE id = ?", (assistant_message_id,)
            ).fetchone()
            conn.execute(
                "UPDATE workspaces SET updated_at = ? WHERE id = ? AND system_id = ?",
                (now, workspace_id, system_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return WorkspaceAgentTurnOut(
        user_message=user_message_out,
        assistant_message=_message_out(assistant_message_row),
        proposals=proposal_outs,
        error=None,
    )


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
        if payload.proposals and payload.role != "assistant":
            raise HTTPException(
                status_code=422,
                detail="Only assistant messages may contain proposals",
            )
        validated_proposals = [
            (
                proposal,
                _validated_proposal_body(proposal.proposal_type, proposal.body),
            )
            for proposal in payload.proposals
        ]
        conn.execute("BEGIN")
        try:
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
            for proposal, body in validated_proposals:
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
                        json.dumps(body, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            conn.execute(
                "UPDATE workspaces SET updated_at = ? WHERE id = ? AND system_id = ?",
                (now, workspace_id, system_id),
            )
            row = conn.execute(
                "SELECT * FROM workspace_messages WHERE id = ?", (message_id,)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
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
        _validate_context_ref(
            conn, system_id, payload.item_type, payload.item_id
        )
        _pin_context_item(
            conn, workspace_id, system_id, payload.item_type, payload.item_id,
            payload.label, now,
        )
        row = conn.execute(
            """
            SELECT * FROM workspace_context_items
            WHERE workspace_id = ? AND item_type = ? AND item_id = ?
            """,
            (workspace_id, payload.item_type, payload.item_id),
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
        body_value = (
            json.loads(proposal["body"] or "{}")
            if payload.body is None
            else _validated_proposal_body(proposal["proposal_type"], payload.body)
        )
        body = json.dumps(body_value, ensure_ascii=False)
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
    # Serialize competing decisions before reading status. A deferred SQLite
    # transaction can let two callers both observe "proposed".
    conn.execute("BEGIN IMMEDIATE")
    try:
        proposal = _get_proposal_or_404(conn, workspace_id, proposal_id, system_id)
        if proposal["status"] == target_status:
            conn.execute("COMMIT")
            return _proposal_out(conn, proposal)
        if proposal["status"] != "proposed":
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is already {proposal['status']} and cannot be {decision_type}",
            )
        updated = conn.execute(
            """UPDATE workspace_proposals
               SET status = ?, updated_at = ?
               WHERE id = ? AND workspace_id = ? AND system_id = ?
                 AND status = 'proposed'""",
            (target_status, now, proposal_id, workspace_id, system_id),
        )
        if updated.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail="Proposal status changed concurrently; reload and retry",
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
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
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


@router.post(
    "/workspaces/{workspace_id}/proposals/{proposal_id}/defer",
    response_model=WorkspaceProposalOut,
)
def defer_workspace_proposal(
    workspace_id: int,
    proposal_id: int,
    payload: WorkspaceDecisionCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> WorkspaceProposalOut:
    with get_conn() as conn:
        _get_workspace_or_404(conn, workspace_id, system_id)
        return _resolve_decision(
            conn, workspace_id, proposal_id, system_id, "deferred", "deferred",
            payload, principal,
        )


@router.post(
    "/workspaces/{workspace_id}/proposals/{proposal_id}/draft",
    response_model=WorkspaceProposalDraftOut,
    status_code=201,
)
def create_workspace_proposal_draft(
    workspace_id: int,
    proposal_id: int,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> WorkspaceProposalDraftOut:
    """Build (or return the existing) deterministic prefill draft for an
    accepted proposal, for the destination screen (Probe Planner or
    Experiments) to read. Only accepted proposals may be drafted, and a
    proposal can only ever have one draft (idempotent via the unique index
    on `proposal_id`)."""
    with get_conn() as conn:
        _get_workspace_or_404(conn, workspace_id, system_id)
        proposal = _get_proposal_or_404(conn, workspace_id, proposal_id, system_id)

        existing = conn.execute(
            "SELECT * FROM workspace_proposal_drafts WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if existing is not None:
            return _proposal_draft_out(existing)

        if proposal["status"] != "accepted":
            raise HTTPException(
                status_code=409,
                detail="Only an accepted proposal can be drafted",
            )

        try:
            draft = build_proposal_draft(
                conn, system_id, proposal["proposal_type"], json.loads(proposal["body"] or "{}")
            )
        except UnsupportedProposalTypeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        now = time.time()
        cur = conn.execute(
            """
            INSERT INTO workspace_proposal_drafts
                (workspace_id, proposal_id, system_id, draft_type, target_screen,
                 payload, missing_fields, created_by_user_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                proposal_id,
                system_id,
                proposal["proposal_type"],
                draft.target_screen,
                json.dumps(draft.payload, ensure_ascii=False),
                json.dumps(draft.missing_fields, ensure_ascii=False),
                principal.user_id,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM workspace_proposal_drafts WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _proposal_draft_out(row)


@router.get(
    "/workspace-drafts/{draft_id}",
    response_model=WorkspaceProposalDraftOut,
)
def get_workspace_proposal_draft(
    draft_id: int,
    system_id: int = Depends(get_system_id),
) -> WorkspaceProposalDraftOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM workspace_proposal_drafts WHERE id = ? AND system_id = ?",
            (draft_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        return _proposal_draft_out(row)
