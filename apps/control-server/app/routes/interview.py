"""System-understanding interview persistence and CRUD API (Issues #67, #68).

Issue #67 — the #35 analogue for the #66 conversational metadata/probe
authoring flow: a pure persistence + contract layer. These endpoints only
store interview sessions, their ordered conversation turns, and the combined
per-symbol proposals.

Issue #68 — the #36 analogue: a deterministic, no-LLM context-pack builder
that assembles symbols, entrypoints, and existing metadata from a pinned
snapshot and flags which items are classified vs. unclassified, within an
explicit LLM context budget.

None of these endpoints call an LLM or write to a worktree.
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
from ..models import (
    InterviewContextPack,
    InterviewMessageCreate,
    InterviewMessageOut,
    InterviewProposalMetadataBlock,
    InterviewProposalOut,
    InterviewProposalProbePlan,
    InterviewProposalsCreate,
    InterviewSessionCreate,
    InterviewSessionDetailOut,
    InterviewSessionOut,
    IntelligenceRunOut,
)

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
