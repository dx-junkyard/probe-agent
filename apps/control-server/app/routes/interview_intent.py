"""Intent Brief API (Issue #284).

Separates user intent (only the user can decide: goal / pain /
success_criteria / priority / constraints / non_goals) from the
implementation-fact "current understanding" the interview already builds.
Kept in its own module (rather than growing routes/interview.py further) per
CLAUDE.md's guidance for this sub-issue.

An ai_proposed item never becomes 'confirmed' except through the explicit
confirm/correct endpoints below, each of which records decision_method
'manual' (Principle 2). 'undecided' and 'not_applicable' are first-class,
user-settable answers, not errors -- they let a developer express "the goal
is only to understand the current state" or "not applicable to this system"
without inventing free text they don't have yet.

probe-agent:
  role: API boundary for the Intent Brief (structured user intent, separate
    from implementation-fact understanding)
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify ai_proposed items never become confirmed without an explicit user confirm/correct call, and that propose fails closed with no rows created on LLM failure.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_system_id
from ..db import get_conn
from ..interview_intent_agent import (
    INTENT_FIELDS,
    IntentProposalResult,
    generate_intent_proposal,
)
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import (
    InterviewIntentCorrectRequest,
    InterviewIntentItemCreate,
    InterviewIntentItemOut,
    InterviewIntentListOut,
)

router = APIRouter()


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _get_item_or_404(conn, item_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_intent_item WHERE id = ? AND system_id = ?",
        (item_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Intent item not found")
    return row


def _item_out(row) -> InterviewIntentItemOut:
    return InterviewIntentItemOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        field=row["field"],
        value_text=row["value_text"],
        status=row["status"],
        origin=row["origin"],
        source_statement=row["source_statement"],
        decision_method=row["decision_method"],
        intelligence_run_id=row["intelligence_run_id"],
        is_mock=bool(row["is_mock"]),
        superseded_by_id=row["superseded_by_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _insert_item(
    conn,
    session_id: int,
    system_id: int,
    *,
    field: str,
    value_text: str,
    status: str,
    origin: str,
    decision_method: str,
    now: float,
    source_statement: Optional[str] = None,
    intelligence_run_id: Optional[int] = None,
    is_mock: bool = False,
) -> int:
    cur = conn.execute(
        """INSERT INTO interview_intent_item
            (session_id, system_id, field, value_text, status, origin,
             source_statement, decision_method, intelligence_run_id, is_mock,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            system_id,
            field,
            value_text,
            status,
            origin,
            source_statement,
            decision_method,
            intelligence_run_id,
            1 if is_mock else 0,
            now,
            now,
        ),
    )
    return cur.lastrowid


@router.get(
    "/interview/sessions/{session_id}/intent",
    response_model=InterviewIntentListOut,
)
def list_intent_items(
    session_id: int,
    include_superseded: bool = Query(default=False),
    system_id: int = Depends(get_system_id),
) -> InterviewIntentListOut:
    """List Intent Brief items grouped by field.

    By default only current (non-superseded) rows are returned, one entry
    per field in the common case. ``include_superseded=true`` also returns
    superseded rows so the dashboard can show correction history -- they are
    never deleted (Principle 7 audit trail).
    """
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        if include_superseded:
            rows = conn.execute(
                """SELECT * FROM interview_intent_item
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY field, id""",
                (session_id, system_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM interview_intent_item
                   WHERE session_id = ? AND system_id = ? AND superseded_by_id IS NULL
                   ORDER BY field, id""",
                (session_id, system_id),
            ).fetchall()

        items_by_field: Dict[str, List[InterviewIntentItemOut]] = {f: [] for f in INTENT_FIELDS}
        for row in rows:
            items_by_field.setdefault(row["field"], []).append(_item_out(row))

        return InterviewIntentListOut(
            session_id=session_id,
            system_id=system_id,
            items_by_field=items_by_field,
        )


@router.post(
    "/interview/sessions/{session_id}/intent",
    response_model=InterviewIntentItemOut,
    status_code=201,
)
def create_intent_item(
    session_id: int,
    payload: InterviewIntentItemCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewIntentItemOut:
    """User creates an Intent Brief item directly.

    Always origin='user', decision_method='manual' -- a human typed this, so
    it is never a proposal awaiting confirmation. status defaults to
    'confirmed'; 'undecided' and 'not_applicable' are also accepted as
    first-class answers (invalid values are rejected with 422 by the
    request model's finite Literal type).
    """
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        item_id = _insert_item(
            conn, session_id, system_id,
            field=payload.field,
            value_text=payload.value_text,
            status=payload.status,
            origin="user",
            decision_method="manual",
            now=now,
        )
        row = conn.execute(
            "SELECT * FROM interview_intent_item WHERE id = ?", (item_id,)
        ).fetchone()
        return _item_out(row)


@router.post(
    "/interview/intent/{item_id}/confirm",
    response_model=InterviewIntentItemOut,
)
def confirm_intent_item(
    item_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewIntentItemOut:
    """User confirms an ai_proposed item as-is.

    The only way an ai_proposed row's status becomes 'confirmed' -- always a
    manual decision (Principle 2), never automatic.
    """
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        if item["superseded_by_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail="This item has already been superseded by a newer revision",
            )
        if item["status"] == "confirmed":
            raise HTTPException(status_code=409, detail="Item is already confirmed")
        conn.execute(
            """UPDATE interview_intent_item
               SET status = 'confirmed', decision_method = 'manual', updated_at = ?
               WHERE id = ?""",
            (now, item_id),
        )
        row = conn.execute(
            "SELECT * FROM interview_intent_item WHERE id = ?", (item_id,)
        ).fetchone()
        return _item_out(row)


@router.post(
    "/interview/intent/{item_id}/correct",
    response_model=InterviewIntentItemOut,
)
def correct_intent_item(
    item_id: int,
    payload: InterviewIntentCorrectRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewIntentItemOut:
    """Correct an item's value text.

    Never overwrites: inserts a new row (origin='user', status='confirmed',
    decision_method='manual') and marks the prior row's superseded_by_id,
    mirroring interview_qa's answer/correction revision chain.
    """
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        if item["superseded_by_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail="This item has already been superseded by a newer revision",
            )
        conn.execute("BEGIN")
        try:
            new_id = _insert_item(
                conn, item["session_id"], system_id,
                field=item["field"],
                value_text=payload.value_text,
                status="confirmed",
                origin="user",
                decision_method="manual",
                now=now,
            )
            conn.execute(
                "UPDATE interview_intent_item SET superseded_by_id = ?, updated_at = ? WHERE id = ?",
                (new_id, now, item_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        row = conn.execute(
            "SELECT * FROM interview_intent_item WHERE id = ?", (new_id,)
        ).fetchone()
        return _item_out(row)


@router.post(
    "/interview/intent/{item_id}/decline",
    response_model=InterviewIntentItemOut,
)
def decline_intent_item(
    item_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewIntentItemOut:
    """Mark an item 'not_applicable' via an explicit manual decision.

    The row is kept (never deleted) for audit; unlike correct, this does not
    create a new revision because no new value_text is supplied.
    """
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        if item["superseded_by_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail="This item has already been superseded by a newer revision",
            )
        conn.execute(
            """UPDATE interview_intent_item
               SET status = 'not_applicable', decision_method = 'manual', updated_at = ?
               WHERE id = ?""",
            (now, item_id),
        )
        row = conn.execute(
            "SELECT * FROM interview_intent_item WHERE id = ?", (item_id,)
        ).fetchone()
        return _item_out(row)


@router.post(
    "/interview/sessions/{session_id}/intent/propose",
    response_model=List[InterviewIntentItemOut],
    status_code=201,
)
def propose_intent_items(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> List[InterviewIntentItemOut]:
    """AI proposes items for whichever Intent Brief fields are still missing.

    Grounded in the session's conversation history and free-text
    user_intent. Fail-closed (Principle 6): any LLM/config/validation
    failure records a failed intelligence_runs row and raises 502 -- no
    interview_intent_item rows are created. Created rows are always
    status='proposed', origin='ai_proposed', decision_method='reasoning_llm'.
    """
    now = time.time()
    config = LLMConfig.intelligence_from_env()
    client_error: Optional[str] = None
    try:
        client = create_llm_client(config)
    except LLMError as exc:
        client = None
        client_error = str(exc)

    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)

        existing_rows = conn.execute(
            """SELECT DISTINCT field FROM interview_intent_item
               WHERE session_id = ? AND system_id = ? AND superseded_by_id IS NULL""",
            (session_id, system_id),
        ).fetchall()
        existing_fields = [r["field"] for r in existing_rows]

        message_rows = conn.execute(
            "SELECT role, content FROM interview_message WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in message_rows]

        if client_error is not None:
            proposal = IntentProposalResult(
                provider=config.provider,
                model=config.model,
                is_mock=config.provider == "mock",
                error=client_error,
            )
        else:
            proposal = generate_intent_proposal(
                client,
                config,
                history=history,
                user_intent=session["user_intent"],
                existing_fields=existing_fields,
            )

        completed_at = time.time()
        run_status = "failed" if proposal.error else "completed"
        run_cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 error_details, is_mock, started_at, completed_at)
            VALUES (?, ?, 'intent_proposal', ?, ?, ?, ?, 'reasoning_llm', ?,
                    ?, ?, ?, ?)
            """,
            (
                system_id,
                session["snapshot_id"],
                proposal.provider,
                proposal.model,
                proposal.prompt_version,
                proposal.schema_version,
                run_status,
                proposal.error,
                1 if proposal.is_mock else 0,
                now,
                completed_at,
            ),
        )
        run_id = run_cur.lastrowid

        if proposal.error:
            raise HTTPException(status_code=502, detail=proposal.error)

        new_ids: List[int] = []
        for item in proposal.items:
            item_id = _insert_item(
                conn, session_id, system_id,
                field=item.field,
                value_text=item.value_text,
                status="proposed",
                origin="ai_proposed",
                decision_method="reasoning_llm",
                now=completed_at,
                source_statement=item.source_statement,
                intelligence_run_id=run_id,
                is_mock=proposal.is_mock,
            )
            new_ids.append(item_id)

        if not new_ids:
            return []
        rows = conn.execute(
            "SELECT * FROM interview_intent_item WHERE id IN (%s) ORDER BY id"
            % ",".join("?" for _ in new_ids),
            new_ids,
        ).fetchall()
        return [_item_out(r) for r in rows]
