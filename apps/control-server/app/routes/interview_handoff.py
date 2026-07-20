"""Handoff API (Issue #291).

A developer picks which knowledge areas they can answer NOW (answerable
knowledge areas live on ``interview_session`` and are updated via
``routes/interview.py``'s ``PUT .../answerable-areas``). An out-of-area (or
otherwise deferred) question or Review Queue item can be handed off to
another assignee for a decision, without ever silently treating the
assignee's eventual answer as the original developer's own.

Lifecycle (finite transition table, Principle 6):
    pending -> answered | cancelled
    answered -> returned
    anything else -> 409

Creating a handoff never writes into the origin row's answer/decision
fields. For ``origin_kind='qa'`` the origin ``interview_qa`` row's own
``status`` is left untouched (Issue #285/#287's "never overload an
existing finite set" discipline) -- only its additive ``handoff_id`` column
is set. For ``origin_kind='review_item'`` the origin ``alignment_item`` row
IS updated: ``status='held'`` (the same value ``/hold`` already uses) plus
its additive ``handoff_id`` column, mirroring how Issue #285's
``origin_kind='review_item'`` Inquiry integration already touches
``alignment_item.status``.

``/answer`` records the ASSIGNEE's own answer on the handoff row itself
(never on the origin row). ``/return`` flips the handoff to 'returned' so
the Dashboard can surface it back on the original item for EXPLICIT
confirmation -- the developer still has to submit their own answer via the
origin item's existing answer endpoint (``interview_qa``'s ``/answer``
accepts an optional ``handoff_id`` for this, Principle 2).

Kept in its own module (like Issues #284/#285/#287) rather than growing
``routes/interview.py`` further, per CLAUDE.md's guidance for sub-issues of
Epic #282.

probe-agent:
  role: API boundary for the answerable-knowledge-area handoff lifecycle
    (pending -> answered -> returned, or cancelled)
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify creating a handoff never writes into the origin qa/alignment row's answer/decision fields, that invalid transitions 409, and that returning a handoff never auto-confirms an answer on the origin item.
"""

from __future__ import annotations

import json
import time
from typing import Dict, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_system_id
from ..db import get_conn
from ..models import (
    QuestionHandoffAnswerRequest,
    QuestionHandoffCreate,
    QuestionHandoffEvidenceRef,
    QuestionHandoffListOut,
    QuestionHandoffOut,
)

router = APIRouter()

ORIGIN_KINDS = ("qa", "review_item")

# Finite transition table (Principle 6): any pair not listed here is
# rejected with 409. 'returned' and 'cancelled' are terminal.
_TRANSITIONS: Dict[str, Set[str]] = {
    "pending": {"answered", "cancelled"},
    "answered": {"returned"},
}

# Handoff statuses that still "hold" the origin item pending resolution
# (Issue #291's duplicate-suppression rule in routes/interview.py).
_OPEN_HANDOFF_STATUSES = ("pending", "answered")


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _get_handoff_or_404(conn, handoff_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM question_handoff WHERE id = ? AND system_id = ?",
        (handoff_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Handoff not found")
    return row


def _validate_origin(conn, origin_kind: str, origin_id: int, session_id: int, system_id: int):
    """Validate the origin item exists and has no other in-flight handoff."""
    if origin_kind == "qa":
        row = conn.execute(
            "SELECT * FROM interview_qa WHERE id = ? AND session_id = ? AND system_id = ?",
            (origin_id, session_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Origin Q&A item not found")
    else:
        row = conn.execute(
            "SELECT * FROM alignment_item WHERE id = ? AND session_id = ? AND system_id = ?",
            (origin_id, session_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Origin review item not found")

    existing_handoff_id = row["handoff_id"] if "handoff_id" in row.keys() else None
    if existing_handoff_id is not None:
        existing = conn.execute(
            "SELECT status FROM question_handoff WHERE id = ?", (existing_handoff_id,),
        ).fetchone()
        if existing is not None and existing["status"] in _OPEN_HANDOFF_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "handoff_already_in_flight",
                    "message": "This item already has a pending/answered handoff",
                    "handoff_id": existing_handoff_id,
                },
            )
    return row


def _mark_origin_held(
    conn, *, origin_kind: str, origin_id: int, handoff_id: int, now: float,
) -> None:
    """Link the origin row to its handoff (Principle 2: no answer/decision write).

    'qa': only the additive handoff_id column is set -- the row's own
    `status` is left untouched. 'review_item': status is also set to
    'held' (the same value /hold already uses), mirroring how Issue #285's
    Inquiry integration already mutates alignment_item.status for
    origin_kind='review_item'.
    """
    if origin_kind == "qa":
        conn.execute(
            "UPDATE interview_qa SET handoff_id = ? WHERE id = ?",
            (handoff_id, origin_id),
        )
    else:
        conn.execute(
            "UPDATE alignment_item SET status = 'held', handoff_id = ?, updated_at = ? "
            "WHERE id = ?",
            (handoff_id, now, origin_id),
        )


def _handoff_out(row) -> QuestionHandoffOut:
    keys = row.keys()
    evidence = row["evidence"] if "evidence" in keys else None
    return QuestionHandoffOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        origin_kind=row["origin_kind"],
        origin_id=row["origin_id"],
        assignee=row["assignee"],
        background=row["background"],
        needed_decision=row["needed_decision"],
        evidence=(
            [QuestionHandoffEvidenceRef(**e) for e in json.loads(evidence)]
            if evidence else None
        ),
        due_note=row["due_note"],
        priority=row["priority"],
        status=row["status"],
        answer_text=row["answer_text"],
        answered_by=row["answered_by"],
        answered_at=row["answered_at"],
        created_by=row["created_by"] if "created_by" in keys else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.post(
    "/interview/sessions/{session_id}/handoffs",
    response_model=QuestionHandoffOut,
    status_code=201,
)
def create_handoff(
    session_id: int,
    payload: QuestionHandoffCreate,
    system_id: int = Depends(get_system_id),
) -> QuestionHandoffOut:
    """Hand an item off to an assignee. Starts 'pending'.

    Never writes into the origin item's own answer/decision fields -- see
    the module docstring.
    """
    if payload.origin_kind not in ORIGIN_KINDS:
        raise HTTPException(status_code=422, detail="Invalid origin_kind")
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        _validate_origin(conn, payload.origin_kind, payload.origin_id, session_id, system_id)

        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO question_handoff
                    (session_id, system_id, origin_kind, origin_id, assignee,
                     background, needed_decision, evidence, due_note, priority,
                     status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    session_id, system_id, payload.origin_kind, payload.origin_id,
                    payload.assignee, payload.background, payload.needed_decision,
                    json.dumps([e.model_dump() for e in payload.evidence], ensure_ascii=False)
                        if payload.evidence else None,
                    payload.due_note, payload.priority, payload.created_by, now, now,
                ),
            )
            handoff_id = cur.lastrowid
            _mark_origin_held(
                conn, origin_kind=payload.origin_kind, origin_id=payload.origin_id,
                handoff_id=handoff_id, now=now,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        row = conn.execute("SELECT * FROM question_handoff WHERE id = ?", (handoff_id,)).fetchone()
        return _handoff_out(row)


@router.get(
    "/interview/sessions/{session_id}/handoffs",
    response_model=QuestionHandoffListOut,
)
def list_handoffs(
    session_id: int,
    status: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> QuestionHandoffListOut:
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        if status is not None:
            rows = conn.execute(
                """SELECT * FROM question_handoff
                   WHERE session_id = ? AND system_id = ? AND status = ?
                   ORDER BY id DESC""",
                (session_id, system_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM question_handoff
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY id DESC""",
                (session_id, system_id),
            ).fetchall()
        return QuestionHandoffListOut(
            session_id=session_id, system_id=system_id,
            items=[_handoff_out(r) for r in rows],
        )


def _apply_transition(conn, row, target_status: str) -> None:
    current = row["status"]
    allowed = _TRANSITIONS.get(current, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invalid_handoff_transition",
                "message": f"Cannot transition handoff from '{current}' to '{target_status}'",
            },
        )


@router.post(
    "/interview/handoffs/{handoff_id}/answer",
    response_model=QuestionHandoffOut,
)
def answer_handoff(
    handoff_id: int,
    payload: QuestionHandoffAnswerRequest,
    system_id: int = Depends(get_system_id),
) -> QuestionHandoffOut:
    """Record the ASSIGNEE's own answer. Never written into the origin row.

    Only valid while 'pending' (409 otherwise). This is not the original
    developer's confirmed answer -- see ``/return``.
    """
    now = time.time()
    with get_conn() as conn:
        row = _get_handoff_or_404(conn, handoff_id, system_id)
        _apply_transition(conn, row, "answered")
        conn.execute(
            """UPDATE question_handoff
               SET status = 'answered', answer_text = ?, answered_by = ?,
                   answered_at = ?, updated_at = ?
               WHERE id = ?""",
            (payload.answer_text, payload.answered_by, now, now, handoff_id),
        )
        updated = conn.execute(
            "SELECT * FROM question_handoff WHERE id = ?", (handoff_id,),
        ).fetchone()
        return _handoff_out(updated)


@router.post(
    "/interview/handoffs/{handoff_id}/return",
    response_model=QuestionHandoffOut,
)
def return_handoff(
    handoff_id: int,
    system_id: int = Depends(get_system_id),
) -> QuestionHandoffOut:
    """Surface the assignee's answer back on the origin item for explicit
    confirmation.

    This does NOT write any answer into interview_qa / alignment_item --
    the original developer must still submit their own answer via that
    item's existing answer endpoint (optionally prefilled client-side with
    this handoff's ``answer_text``, and carrying ``handoff_id`` as
    provenance for ``interview_qa``'s ``/answer``).
    """
    now = time.time()
    with get_conn() as conn:
        row = _get_handoff_or_404(conn, handoff_id, system_id)
        _apply_transition(conn, row, "returned")
        conn.execute(
            "UPDATE question_handoff SET status = 'returned', updated_at = ? WHERE id = ?",
            (now, handoff_id),
        )
        updated = conn.execute(
            "SELECT * FROM question_handoff WHERE id = ?", (handoff_id,),
        ).fetchone()
        return _handoff_out(updated)


@router.post(
    "/interview/handoffs/{handoff_id}/cancel",
    response_model=QuestionHandoffOut,
)
def cancel_handoff(
    handoff_id: int,
    system_id: int = Depends(get_system_id),
) -> QuestionHandoffOut:
    """Cancel a still-pending handoff. Only valid while 'pending' (409 otherwise)."""
    now = time.time()
    with get_conn() as conn:
        row = _get_handoff_or_404(conn, handoff_id, system_id)
        _apply_transition(conn, row, "cancelled")
        conn.execute(
            "UPDATE question_handoff SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, handoff_id),
        )
        updated = conn.execute(
            "SELECT * FROM question_handoff WHERE id = ?", (handoff_id,),
        ).fetchone()
        return _handoff_out(updated)
