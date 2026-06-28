"""System-understanding interview API (Issues #67, #68, #69, #70).

Issue #67 — persistence + contract layer for sessions, messages, proposals.
Issue #68 — deterministic, no-LLM context-pack builder.
Issue #69 — reasoning-model dialogue endpoint that produces structured
assistant turns and per-symbol combined proposals (docstring metadata +
probe plan), validated against #54 vocabulary and the safety denylist.
Issue #70 — per-item approval gate with manual decision record.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from fastapi import Query

from ..auth import get_system_id
from ..db import get_conn
from ..interview_context import build_interview_context
from ..interview_agent import (
    PROMPT_VERSION as INTERVIEW_PROMPT_VERSION,
    SCHEMA_VERSION as INTERVIEW_SCHEMA_VERSION,
    generate_interview_turn,
)
from ..llm import LLMConfig, create_llm_client, is_reasoning_model
from ..models import (
    InterviewApprovedItemOut,
    InterviewApprovedSetOut,
    InterviewContextPack,
    InterviewDialogueProposalOut,
    InterviewDialogueTurnOut,
    InterviewDialogueTurnRequest,
    InterviewMessageCreate,
    InterviewMessageOut,
    InterviewProposalApproveRequest,
    InterviewProposalDecisionOut,
    InterviewProposalEditRequest,
    InterviewProposalMetadataBlock,
    InterviewProposalOut,
    InterviewProposalProbePlan,
    InterviewProposalRejectRequest,
    InterviewProposalsCreate,
    InterviewSessionCreate,
    InterviewSessionDetailOut,
    InterviewSessionOut,
    IntelligenceRunOut,
)
from ..probe_planner import check_denylist

router = APIRouter()


def _session_out(row) -> InterviewSessionOut:
    return InterviewSessionOut(
        id=row["id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        title=row["title"],
        focus=row["focus"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _message_out(row) -> InterviewMessageOut:
    return InterviewMessageOut(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        intelligence_run_id=row["intelligence_run_id"],
        created_at=row["created_at"],
    )


def _intelligence_run_out(row) -> IntelligenceRunOut:
    return IntelligenceRunOut(
        id=row["id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        run_type=row["run_type"],
        provider=row["provider"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        decision_method=row["decision_method"],
        status=row["status"],
        error_details=row["error_details"],
        is_mock=bool(row["is_mock"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _proposal_out(conn, row) -> InterviewProposalOut:
    run_row = conn.execute(
        "SELECT * FROM intelligence_runs WHERE id = ?",
        (row["intelligence_run_id"],),
    ).fetchone()
    return InterviewProposalOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        message_id=row["message_id"],
        intelligence_run_id=row["intelligence_run_id"],
        symbol_id=row["symbol_id"],
        path=row["path"],
        qualified_name=row["qualified_name"],
        metadata=InterviewProposalMetadataBlock(
            role=row["md_role"],
            capability=row["md_capability"],
            system_purpose=row["md_system_purpose"],
            probe_value=row["md_probe_value"],
            element_type=row["md_element_type"],
            operation_kind=row["md_operation_kind"],
            consumers=json.loads(row["md_consumers"] or "[]"),
            state_effects=json.loads(row["md_state_effects"] or "[]"),
        ),
        probe_plan=InterviewProposalProbePlan(
            feature_id=row["feature_id"],
            objective=row["objective"],
            reason=row["probe_reason"],
            recommended_mode=row["recommended_mode"],
            side_effect_risk=row["side_effect_risk"],
            replayability=row["replayability"],
        ),
        decision_method=row["decision_method"],
        approval_state=row["approval_state"],
        is_mock=bool(row["is_mock"]),
        intelligence_run=_intelligence_run_out(run_row) if run_row else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


@router.get("/interview/sessions", response_model=List[InterviewSessionOut])
def list_interview_sessions(
    system_id: int = Depends(get_system_id),
) -> List[InterviewSessionOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM interview_session WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        return [_session_out(r) for r in rows]


@router.post(
    "/interview/sessions",
    response_model=InterviewSessionOut,
    status_code=201,
)
def create_interview_session(
    payload: InterviewSessionCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewSessionOut:
    now = time.time()
    with get_conn() as conn:
        snapshot = conn.execute(
            "SELECT id FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (payload.snapshot_id, system_id),
        ).fetchone()
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail="Snapshot not found for this system",
            )
        cur = conn.execute(
            """
            INSERT INTO interview_session
                (system_id, snapshot_id, title, focus, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'open', ?, ?)
            """,
            (system_id, payload.snapshot_id, payload.title, payload.focus, now, now),
        )
        row = _get_session_or_404(conn, cur.lastrowid, system_id)
        return _session_out(row)


@router.get(
    "/interview/sessions/{session_id}",
    response_model=InterviewSessionDetailOut,
)
def get_interview_session(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewSessionDetailOut:
    with get_conn() as conn:
        row = _get_session_or_404(conn, session_id, system_id)
        message_rows = conn.execute(
            "SELECT * FROM interview_message WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        proposal_rows = conn.execute(
            "SELECT * FROM interview_proposal WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return InterviewSessionDetailOut(
            **_session_out(row).model_dump(),
            messages=[_message_out(m) for m in message_rows],
            proposals=[_proposal_out(conn, p) for p in proposal_rows],
        )


@router.get(
    "/interview/sessions/{session_id}/context-pack",
    response_model=InterviewContextPack,
)
def get_interview_context_pack(
    session_id: int,
    system_id: int = Depends(get_system_id),
    budget: Optional[int] = Query(default=None, ge=1000, le=500_000),
) -> InterviewContextPack:
    """Deterministic, no-LLM context pack for a pinned interview session.

    Assembles symbols, entrypoints, and existing metadata from the session's
    pinned snapshot and flags which items are classified vs. unclassified,
    all within an explicit LLM context budget (Issue #68).
    """
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        return build_interview_context(
            conn, system_id, session["snapshot_id"], budget_chars=budget,
        )


@router.post(
    "/interview/sessions/{session_id}/messages",
    response_model=InterviewMessageOut,
    status_code=201,
)
def create_interview_message(
    session_id: int,
    payload: InterviewMessageCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewMessageOut:
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        if payload.intelligence_run_id is not None:
            run = conn.execute(
                "SELECT id FROM intelligence_runs WHERE id = ? AND system_id = ?",
                (payload.intelligence_run_id, system_id),
            ).fetchone()
            if run is None:
                raise HTTPException(
                    status_code=404,
                    detail="Referenced reasoning run not found for this system",
                )
        cur = conn.execute(
            """
            INSERT INTO interview_message
                (session_id, system_id, role, content, intelligence_run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                system_id,
                payload.role,
                payload.content,
                payload.intelligence_run_id,
                now,
            ),
        )
        conn.execute(
            "UPDATE interview_session SET updated_at = ? WHERE id = ? AND system_id = ?",
            (now, session_id, system_id),
        )
        row = conn.execute(
            "SELECT * FROM interview_message WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _message_out(row)


@router.get(
    "/interview/sessions/{session_id}/proposals",
    response_model=List[InterviewProposalOut],
)
def list_interview_proposals(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> List[InterviewProposalOut]:
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            "SELECT * FROM interview_proposal WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [_proposal_out(conn, r) for r in rows]


@router.post(
    "/interview/sessions/{session_id}/proposals",
    response_model=List[InterviewProposalOut],
    status_code=201,
)
def create_interview_proposals(
    session_id: int,
    payload: InterviewProposalsCreate,
    system_id: int = Depends(get_system_id),
) -> List[InterviewProposalOut]:
    """Persist a batch of combined per-symbol proposals.

    No LLM call happens here; the caller (a later dialogue issue, or a test)
    supplies the already-produced payload and the audit metadata of the
    reasoning run that produced it. The audit metadata is stored as one
    ``intelligence_runs`` row that every proposal in the batch links to.
    """
    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        snapshot_id = session["snapshot_id"]
        message_id = payload.message_id
        if message_id is not None:
            message = conn.execute(
                """SELECT id FROM interview_message
                   WHERE id = ? AND session_id = ? AND system_id = ?""",
                (message_id, session_id, system_id),
            ).fetchone()
            if message is None:
                raise HTTPException(
                    status_code=404,
                    detail="Referenced message not found for this session",
                )
        conn.execute("BEGIN")
        try:
            run_cur = conn.execute(
                """
                INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method, status,
                     is_mock, started_at, completed_at)
                VALUES (?, ?, 'interview_proposal', ?, ?, ?, ?, 'reasoning_llm',
                        'completed', ?, ?, ?)
                """,
                (
                    system_id,
                    snapshot_id,
                    payload.audit.provider,
                    payload.audit.model,
                    payload.audit.prompt_version,
                    payload.audit.schema_version,
                    1 if payload.audit.is_mock else 0,
                    now,
                    now,
                ),
            )
            run_id = run_cur.lastrowid
            new_ids: List[int] = []
            for item in payload.proposals:
                cur = conn.execute(
                    """
                    INSERT INTO interview_proposal
                        (session_id, system_id, snapshot_id, message_id,
                         intelligence_run_id, symbol_id, path, qualified_name,
                         md_role, md_capability, md_system_purpose, md_probe_value,
                         md_element_type, md_operation_kind, md_consumers,
                         md_state_effects, feature_id, objective, probe_reason,
                         recommended_mode, side_effect_risk, replayability,
                         decision_method, approval_state, is_mock,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, 'reasoning_llm', 'proposed', ?, ?, ?)
                    """,
                    (
                        session_id,
                        system_id,
                        snapshot_id,
                        message_id,
                        run_id,
                        item.symbol_id,
                        item.path,
                        item.qualified_name,
                        item.metadata.role,
                        item.metadata.capability,
                        item.metadata.system_purpose,
                        item.metadata.probe_value,
                        item.metadata.element_type,
                        item.metadata.operation_kind,
                        json.dumps(item.metadata.consumers, ensure_ascii=False),
                        json.dumps(item.metadata.state_effects, ensure_ascii=False),
                        item.probe_plan.feature_id,
                        item.probe_plan.objective,
                        item.probe_plan.reason,
                        item.probe_plan.recommended_mode,
                        item.probe_plan.side_effect_risk,
                        item.probe_plan.replayability,
                        1 if payload.audit.is_mock else 0,
                        now,
                        now,
                    ),
                )
                new_ids.append(cur.lastrowid)
            conn.execute(
                "UPDATE interview_session SET updated_at = ? WHERE id = ? AND system_id = ?",
                (now, session_id, system_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        rows = conn.execute(
            "SELECT * FROM interview_proposal WHERE id IN (%s) ORDER BY id"
            % ",".join("?" for _ in new_ids),
            new_ids,
        ).fetchall()
        return [_proposal_out(conn, r) for r in rows]


# --- Dialogue Turn (Issue #69) -----------------------------------------------


def _get_intelligence_llm_config() -> LLMConfig:
    """Build LLMConfig preferring INTELLIGENCE_LLM_* over generic LLM_*."""
    import os

    provider = os.getenv("INTELLIGENCE_LLM_PROVIDER") or os.getenv("LLM_PROVIDER", "openai")
    provider = provider.strip().lower()
    model = os.getenv("INTELLIGENCE_LLM_MODEL") or os.getenv("LLM_MODEL")
    if not model:
        defaults = {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-latest",
            "gemini": "gemini-1.5-flash",
            "mock": "mock",
        }
        model = defaults.get(provider, "gpt-4o-mini")
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )
    try:
        timeout = float(os.getenv("LLM_TIMEOUT", "120"))
    except ValueError:
        timeout = 120.0
    return LLMConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=os.getenv("LLM_BASE_URL") or None,
        timeout=timeout,
    )


@router.post(
    "/interview/sessions/{session_id}/dialogue-turn",
    response_model=InterviewDialogueTurnOut,
)
def interview_dialogue_turn(
    session_id: int,
    payload: InterviewDialogueTurnRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewDialogueTurnOut:
    """Generate a reasoning-model dialogue turn for the interview (Issue #69).

    1. Builds a deterministic context pack from the session's pinned snapshot.
    2. Assembles conversation history from stored messages.
    3. Calls the reasoning model for a structured response.
    4. Validates proposals against #54 vocabulary and the safety denylist.
    5. On success: persists user message, assistant message, intelligence run,
       and any proposals. On failure: persists the failure and returns error.
    """
    now = time.time()
    config = _get_intelligence_llm_config()
    client = create_llm_client(config)

    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        snapshot_id = session["snapshot_id"]

        context_pack = build_interview_context(
            conn, system_id, snapshot_id, budget_chars=payload.budget,
        )

        message_rows = conn.execute(
            "SELECT role, content FROM interview_message WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in message_rows]

        turn = generate_interview_turn(
            client,
            config,
            context_pack=context_pack,
            history=history,
            user_message=payload.user_message,
        )

        conn.execute("BEGIN")
        try:
            # Store user message.
            conn.execute(
                """INSERT INTO interview_message
                    (session_id, system_id, role, content, intelligence_run_id, created_at)
                VALUES (?, ?, 'user', ?, NULL, ?)""",
                (session_id, system_id, payload.user_message, now),
            )

            # Store intelligence run (success or failure).
            run_status = "failed" if turn.error else "completed"
            run_cur = conn.execute(
                """INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method, status,
                     error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'interview_dialogue', ?, ?, ?, ?, 'reasoning_llm',
                        ?, ?, ?, ?, ?)""",
                (
                    system_id,
                    snapshot_id,
                    turn.provider,
                    turn.model,
                    turn.prompt_version,
                    turn.schema_version,
                    run_status,
                    turn.error,
                    1 if turn.is_mock else 0,
                    now,
                    now,
                ),
            )
            run_id = run_cur.lastrowid

            run_row = conn.execute(
                "SELECT * FROM intelligence_runs WHERE id = ?", (run_id,),
            ).fetchone()
            intelligence_run_out = _intelligence_run_out(run_row)

            if turn.error:
                conn.execute(
                    "UPDATE interview_session SET updated_at = ? WHERE id = ? AND system_id = ?",
                    (now, session_id, system_id),
                )
                conn.execute("COMMIT")
                return InterviewDialogueTurnOut(
                    error=turn.error,
                    intelligence_run=intelligence_run_out,
                )

            # Store assistant message.
            asst_cur = conn.execute(
                """INSERT INTO interview_message
                    (session_id, system_id, role, content, intelligence_run_id, created_at)
                VALUES (?, ?, 'assistant', ?, ?, ?)""",
                (session_id, system_id, turn.assistant_message, run_id, now),
            )
            asst_message_id = asst_cur.lastrowid

            # Persist proposals if any.
            proposal_outs: List[InterviewDialogueProposalOut] = []
            for p in turn.proposals:
                conn.execute(
                    """INSERT INTO interview_proposal
                        (session_id, system_id, snapshot_id, message_id,
                         intelligence_run_id, symbol_id, path, qualified_name,
                         md_role, md_capability, md_system_purpose, md_probe_value,
                         md_element_type, md_operation_kind, md_consumers,
                         md_state_effects, feature_id, objective, probe_reason,
                         recommended_mode, side_effect_risk, replayability,
                         decision_method, approval_state, is_mock,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, 'reasoning_llm', 'proposed', ?, ?, ?)""",
                    (
                        session_id,
                        system_id,
                        snapshot_id,
                        asst_message_id,
                        run_id,
                        p.symbol_id,
                        p.path,
                        p.qualified_name,
                        p.metadata.role,
                        p.metadata.capability,
                        p.metadata.system_purpose,
                        p.metadata.probe_value,
                        p.metadata.element_type,
                        p.metadata.operation_kind,
                        json.dumps(p.metadata.consumers, ensure_ascii=False),
                        json.dumps(p.metadata.state_effects, ensure_ascii=False),
                        p.probe_plan.feature_id,
                        p.probe_plan.objective,
                        p.probe_plan.reason,
                        p.probe_plan.recommended_mode,
                        p.probe_plan.side_effect_risk,
                        p.probe_plan.replayability,
                        1 if turn.is_mock else 0,
                        now,
                        now,
                    ),
                )
                proposal_outs.append(InterviewDialogueProposalOut(
                    path=p.path,
                    qualified_name=p.qualified_name,
                    symbol_id=p.symbol_id,
                    metadata=p.metadata,
                    probe_plan=p.probe_plan,
                    denylist_hit=p.denylist_hit,
                ))

            conn.execute(
                "UPDATE interview_session SET updated_at = ? WHERE id = ? AND system_id = ?",
                (now, session_id, system_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        return InterviewDialogueTurnOut(
            assistant_message=turn.assistant_message,
            proposals=proposal_outs,
            next_questions=turn.next_questions,
            intelligence_run=intelligence_run_out,
        )


# --- Proposal Approval Gate (Issue #70) ---------------------------------------


def _get_proposal_or_404(conn, proposal_id: int, session_id: int, system_id: int):
    row = conn.execute(
        """SELECT * FROM interview_proposal
           WHERE id = ? AND session_id = ? AND system_id = ?""",
        (proposal_id, session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return row


def _decision_out(row) -> InterviewProposalDecisionOut:
    edited_metadata = None
    edited_probe_plan = None
    if row["decision"] == "edited":
        edited_metadata = InterviewProposalMetadataBlock(
            role=row["edited_md_role"],
            capability=row["edited_md_capability"],
            system_purpose=row["edited_md_system_purpose"],
            probe_value=row["edited_md_probe_value"],
            element_type=row["edited_md_element_type"],
            operation_kind=row["edited_md_operation_kind"],
            consumers=json.loads(row["edited_md_consumers"] or "[]"),
            state_effects=json.loads(row["edited_md_state_effects"] or "[]"),
        )
        edited_probe_plan = InterviewProposalProbePlan(
            feature_id=row["edited_feature_id"] or "",
            objective=row["edited_objective"] or "",
            reason=row["edited_probe_reason"] or "",
            recommended_mode=row["edited_recommended_mode"] or "trace",
            side_effect_risk=row["edited_side_effect_risk"] or "low",
            replayability=row["edited_replayability"] or "safe",
        )
    return InterviewProposalDecisionOut(
        id=row["id"],
        proposal_id=row["proposal_id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        decision=row["decision"],
        decision_method=row["decision_method"],
        actor=row["actor"],
        edited_metadata=edited_metadata,
        edited_probe_plan=edited_probe_plan,
        denylist_hit=row["denylist_hit"],
        decided_at=row["decided_at"],
    )


def _check_proposal_state(proposal_row) -> None:
    """Only 'proposed' items can be transitioned."""
    state = proposal_row["approval_state"]
    if state != "proposed":
        raise HTTPException(
            status_code=409,
            detail=f"Proposal is already '{state}'; only 'proposed' items can be reviewed",
        )


def _persist_decision(
    conn,
    proposal_row,
    session_id: int,
    system_id: int,
    decision: str,
    actor: str,
    now: float,
    *,
    edited_metadata: Optional[InterviewProposalMetadataBlock] = None,
    edited_probe_plan: Optional[InterviewProposalProbePlan] = None,
    denylist_hit: Optional[str] = None,
) -> InterviewProposalDecisionOut:
    proposal_id = proposal_row["id"]

    cur = conn.execute(
        """INSERT INTO interview_proposal_decision
            (proposal_id, session_id, system_id, decision, decision_method,
             actor,
             edited_md_role, edited_md_capability, edited_md_system_purpose,
             edited_md_probe_value, edited_md_element_type, edited_md_operation_kind,
             edited_md_consumers, edited_md_state_effects,
             edited_feature_id, edited_objective, edited_probe_reason,
             edited_recommended_mode, edited_side_effect_risk, edited_replayability,
             denylist_hit, decided_at)
        VALUES (?, ?, ?, ?, 'manual', ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?)""",
        (
            proposal_id,
            session_id,
            system_id,
            decision,
            actor,
            edited_metadata.role if edited_metadata else None,
            edited_metadata.capability if edited_metadata else None,
            edited_metadata.system_purpose if edited_metadata else None,
            edited_metadata.probe_value if edited_metadata else None,
            edited_metadata.element_type if edited_metadata else None,
            edited_metadata.operation_kind if edited_metadata else None,
            json.dumps(edited_metadata.consumers, ensure_ascii=False) if edited_metadata else None,
            json.dumps(edited_metadata.state_effects, ensure_ascii=False) if edited_metadata else None,
            edited_probe_plan.feature_id if edited_probe_plan else None,
            edited_probe_plan.objective if edited_probe_plan else None,
            edited_probe_plan.reason if edited_probe_plan else None,
            edited_probe_plan.recommended_mode if edited_probe_plan else None,
            edited_probe_plan.side_effect_risk if edited_probe_plan else None,
            edited_probe_plan.replayability if edited_probe_plan else None,
            denylist_hit,
            now,
        ),
    )
    decision_id = cur.lastrowid

    conn.execute(
        """UPDATE interview_proposal
           SET approval_state = ?, updated_at = ?
           WHERE id = ?""",
        (decision, now, proposal_id),
    )
    conn.execute(
        "UPDATE interview_session SET updated_at = ? WHERE id = ? AND system_id = ?",
        (now, session_id, system_id),
    )

    row = conn.execute(
        "SELECT * FROM interview_proposal_decision WHERE id = ?",
        (decision_id,),
    ).fetchone()
    return _decision_out(row)


@router.post(
    "/interview/sessions/{session_id}/proposals/{proposal_id}/approve",
    response_model=InterviewProposalDecisionOut,
)
def approve_interview_proposal(
    session_id: int,
    proposal_id: int,
    payload: InterviewProposalApproveRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewProposalDecisionOut:
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        proposal = _get_proposal_or_404(conn, proposal_id, session_id, system_id)
        _check_proposal_state(proposal)
        return _persist_decision(
            conn, proposal, session_id, system_id,
            decision="approved", actor=payload.actor, now=now,
        )


@router.post(
    "/interview/sessions/{session_id}/proposals/{proposal_id}/reject",
    response_model=InterviewProposalDecisionOut,
)
def reject_interview_proposal(
    session_id: int,
    proposal_id: int,
    payload: InterviewProposalRejectRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewProposalDecisionOut:
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        proposal = _get_proposal_or_404(conn, proposal_id, session_id, system_id)
        _check_proposal_state(proposal)
        return _persist_decision(
            conn, proposal, session_id, system_id,
            decision="rejected", actor=payload.actor, now=now,
        )


@router.post(
    "/interview/sessions/{session_id}/proposals/{proposal_id}/edit",
    response_model=InterviewProposalDecisionOut,
)
def edit_interview_proposal(
    session_id: int,
    proposal_id: int,
    payload: InterviewProposalEditRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewProposalDecisionOut:
    """Edit a proposal with corrected values and mark as approved.

    Re-runs the safety denylist on the edited qualified_name and probe-plan
    values. If the symbol is denylisted, the edit is rejected with 422.
    """
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        proposal = _get_proposal_or_404(conn, proposal_id, session_id, system_id)
        _check_proposal_state(proposal)

        extra_text_parts = []
        if payload.metadata:
            for field in ("role", "capability", "system_purpose", "probe_value"):
                val = getattr(payload.metadata, field, None)
                if val:
                    extra_text_parts.append(val)
        if payload.probe_plan:
            for field in ("feature_id", "objective", "reason"):
                val = getattr(payload.probe_plan, field, None)
                if val:
                    extra_text_parts.append(val)
        extra_text = " ".join(extra_text_parts) if extra_text_parts else None

        hit = check_denylist(proposal["qualified_name"], extra_text)
        if hit:
            raise HTTPException(
                status_code=422,
                detail=f"Edit rejected: denylisted content ({hit})",
            )

        return _persist_decision(
            conn, proposal, session_id, system_id,
            decision="edited", actor=payload.actor, now=now,
            edited_metadata=payload.metadata,
            edited_probe_plan=payload.probe_plan,
            denylist_hit=hit,
        )


@router.get(
    "/interview/sessions/{session_id}/approved-set",
    response_model=InterviewApprovedSetOut,
)
def get_interview_approved_set(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewApprovedSetOut:
    """Return the approved set for a session: items eligible for materialization.

    Only proposals with an explicit 'approved' or 'edited' decision are
    included. 'proposed' and 'rejected' items are excluded.
    """
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        snapshot_id = session["snapshot_id"]

        all_proposals = conn.execute(
            "SELECT * FROM interview_proposal WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()

        total = len(all_proposals)
        approved_count = 0
        rejected_count = 0
        pending_count = 0
        items: List[InterviewApprovedItemOut] = []

        for p in all_proposals:
            state = p["approval_state"]
            if state == "proposed":
                pending_count += 1
                continue
            elif state == "rejected":
                rejected_count += 1
                continue

            # approved or edited — get the decision record.
            decision_row = conn.execute(
                """SELECT * FROM interview_proposal_decision
                   WHERE proposal_id = ? ORDER BY id DESC LIMIT 1""",
                (p["id"],),
            ).fetchone()
            if decision_row is None:
                pending_count += 1
                continue

            approved_count += 1

            if state == "edited" and decision_row["decision"] == "edited":
                metadata = InterviewProposalMetadataBlock(
                    role=decision_row["edited_md_role"],
                    capability=decision_row["edited_md_capability"],
                    system_purpose=decision_row["edited_md_system_purpose"],
                    probe_value=decision_row["edited_md_probe_value"],
                    element_type=decision_row["edited_md_element_type"],
                    operation_kind=decision_row["edited_md_operation_kind"],
                    consumers=json.loads(decision_row["edited_md_consumers"] or "[]"),
                    state_effects=json.loads(decision_row["edited_md_state_effects"] or "[]"),
                )
                probe_plan = InterviewProposalProbePlan(
                    feature_id=decision_row["edited_feature_id"] or "",
                    objective=decision_row["edited_objective"] or "",
                    reason=decision_row["edited_probe_reason"] or "",
                    recommended_mode=decision_row["edited_recommended_mode"] or "trace",
                    side_effect_risk=decision_row["edited_side_effect_risk"] or "low",
                    replayability=decision_row["edited_replayability"] or "safe",
                )
            else:
                metadata = InterviewProposalMetadataBlock(
                    role=p["md_role"],
                    capability=p["md_capability"],
                    system_purpose=p["md_system_purpose"],
                    probe_value=p["md_probe_value"],
                    element_type=p["md_element_type"],
                    operation_kind=p["md_operation_kind"],
                    consumers=json.loads(p["md_consumers"] or "[]"),
                    state_effects=json.loads(p["md_state_effects"] or "[]"),
                )
                probe_plan = InterviewProposalProbePlan(
                    feature_id=p["feature_id"],
                    objective=p["objective"],
                    reason=p["probe_reason"],
                    recommended_mode=p["recommended_mode"],
                    side_effect_risk=p["side_effect_risk"],
                    replayability=p["replayability"],
                )

            items.append(InterviewApprovedItemOut(
                proposal_id=p["id"],
                path=p["path"],
                qualified_name=p["qualified_name"],
                symbol_id=p["symbol_id"],
                metadata=metadata,
                probe_plan=probe_plan,
                decision=decision_row["decision"],
                decision_id=decision_row["id"],
                actor=decision_row["actor"],
                decided_at=decision_row["decided_at"],
            ))

        return InterviewApprovedSetOut(
            session_id=session_id,
            system_id=system_id,
            snapshot_id=snapshot_id,
            items=items,
            total_proposals=total,
            approved_count=approved_count,
            rejected_count=rejected_count,
            pending_count=pending_count,
        )
