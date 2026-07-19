"""Inquiry lifecycle API (Issue #285).

When a developer has a doubt about a confirmation item (an
``interview_qa`` question, an ``interview_intent_item``, or -- from Issue
#287 onward -- an ``alignment_item`` Review Queue item), the original item
is held pending and a separate Inquiry conversation starts. Resolving the
doubt ("疑問は解消した") is strictly separate from answering/confirming the
original item: creating, messaging, and closing (resolve/unresolved/hold/
cancel) an Inquiry never writes to ``interview_qa`` or
``interview_intent_item``. The developer still has to submit the origin
item's own answer/confirm endpoint afterward -- resolving an Inquiry is
never mistaken for consent (the regression this module's tests guard
against).

``origin_kind='review_item'`` is the one documented exception to "never
writes to the origin table": Issue #287's brief requires the
``alignment_item`` row itself to reflect "an Inquiry is open on this item"
(``status='inquiry'``) so the Review Queue UI can show it as blocked, and to
revert to ``status='open'`` (never ``'answered'``) once the Inquiry closes --
see ``_set_review_item_status`` below. This still preserves the same
Principle-2 guarantee: only ``status`` is ever touched here, never
``user_decision``, and the developer must still call the item's own
``/answer``/``/correct``/``/hold`` endpoint (``routes/interview_alignment.py``)
to actually decide it.

Kept in its own module (like Issue #284's ``interview_intent.py``) rather
than growing ``routes/interview.py`` further, per CLAUDE.md's guidance for
this sub-issue.

Answer generation is isolated in ``app/inquiry_answering.py`` so Issue #286
(Question Router / Investigation Agent) can replace the reasoning-model call
without touching this lifecycle/transition logic.

probe-agent:
  role: API boundary for the Inquiry side-conversation lifecycle (held
    pending -> resolved/unresolved/held/cancelled)
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify resolving/holding/cancelling an Inquiry never mutates the origin interview_qa/interview_intent_item row, and that LLM failure leaves the inquiry open with no assistant message.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_system_id
from ..db import get_conn
from ..interview_context import build_interview_context
from ..interview_language import interview_message, resolve_message_language
from ..inquiry_answering import InquiryAnswerResult, generate_inquiry_answer
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import (
    InterviewInquiryCreate,
    InterviewInquiryDetailOut,
    InterviewInquiryEvidenceOut,
    InterviewInquiryListOut,
    InterviewInquiryMessageCreate,
    InterviewInquiryMessageDetailOut,
    InterviewInquiryMessageOut,
    InterviewInquiryOut,
    InterviewInquiryTransitionRequest,
)

router = APIRouter()

# Finite transition table (Principle 6): any pair not listed here is
# rejected with 409. 'open' -> 'open' is an explicit no-op transition used
# by /reopen-doubt ("解消していない" -- the developer keeps the Inquiry open
# to ask more, without any real state change beyond the audit row).
_TRANSITIONS: Dict[str, Set[str]] = {
    "open": {"resolved", "unresolved", "held", "cancelled", "open"},
    "held": {"open"},
}

# Statuses considered "closed" -- closed_at is stamped; 'held' and 'open'
# are not closed (held is explicitly resumable).
_CLOSED_STATUSES = {"resolved", "unresolved", "cancelled"}

ORIGIN_KINDS = ("qa", "intent", "review_item")


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _get_inquiry_or_404(conn, inquiry_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_inquiry WHERE id = ? AND system_id = ?",
        (inquiry_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return row


def _validate_origin_exists(
    conn, origin_kind: str, origin_id: int, session_id: int, system_id: int
) -> Optional[str]:
    """Validate the origin item exists and return a short prompt summary of it."""
    if origin_kind == "qa":
        row = conn.execute(
            "SELECT question_text, answer_text FROM interview_qa "
            "WHERE id = ? AND session_id = ? AND system_id = ?",
            (origin_id, session_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Origin Q&A item not found")
        summary = f"Q&A question under discussion: {row['question_text']}"
        if row["answer_text"]:
            summary += f"\nExisting (unconfirmed-by-this-Inquiry) answer: {row['answer_text']}"
        return summary
    if origin_kind == "intent":
        row = conn.execute(
            "SELECT field, value_text FROM interview_intent_item "
            "WHERE id = ? AND session_id = ? AND system_id = ?",
            (origin_id, session_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Origin Intent item not found")
        return f"Intent Brief field '{row['field']}' under discussion: {row['value_text']}"
    # origin_kind == "review_item" (Issue #287): a Review Queue alignment
    # item. Unlike qa/intent, opening/closing an Inquiry on a review_item
    # DOES touch the origin row's own status (see
    # _set_review_item_inquiry_status below) -- the item is "held pending"
    # server-side, not just in the dashboard's local state.
    row = conn.execute(
        "SELECT current_claim, alignment_state, gap_summary FROM alignment_item "
        "WHERE id = ? AND session_id = ? AND system_id = ?",
        (origin_id, session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Origin review item not found")
    summary = f"Review item under discussion (alignment_state={row['alignment_state']}): {row['current_claim']}"
    if row["gap_summary"]:
        summary += f"\nGap summary: {row['gap_summary']}"
    return summary


def _set_review_item_status(
    conn, *, origin_kind: str, origin_id: int, session_id: int, system_id: int, status: str, now: float,
) -> None:
    """Mirror an Inquiry's open/closed state onto its origin alignment_item.

    Only origin_kind='review_item' is affected (Issue #287); qa/intent rows
    are never written by the Inquiry lifecycle (module docstring). Called
    with status='inquiry' when the Inquiry opens, and status='open' (never
    'answered') when it closes -- the developer must still explicitly use
    the item's own answer/correct/hold endpoint (Principle 2).
    """
    if origin_kind != "review_item":
        return
    conn.execute(
        """UPDATE alignment_item SET status = ?, updated_at = ?
           WHERE id = ? AND session_id = ? AND system_id = ?""",
        (status, now, origin_id, session_id, system_id),
    )


def _message_out(row) -> InterviewInquiryMessageOut:
    detail_out = None
    if row["detail"]:
        detail_json = json.loads(row["detail"])
        detail_out = InterviewInquiryMessageDetailOut(
            key_points=detail_json.get("key_points", []),
            evidence=[
                InterviewInquiryEvidenceOut(**e) for e in detail_json.get("evidence", [])
            ],
            uncertainty=detail_json.get("uncertainty", ""),
            route_category=detail_json.get("route_category"),
            decision_question=detail_json.get("decision_question"),
        )
    return InterviewInquiryMessageOut(
        id=row["id"],
        inquiry_id=row["inquiry_id"],
        system_id=row["system_id"],
        role=row["role"],
        content=row["content"],
        detail=detail_out,
        intelligence_run_id=row["intelligence_run_id"],
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
    )


def _inquiry_out(row) -> InterviewInquiryOut:
    return InterviewInquiryOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        origin_kind=row["origin_kind"],
        origin_id=row["origin_id"],
        held_draft=row["held_draft"],
        status=row["status"],
        status_reason=row["status_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row["closed_at"],
    )


@dataclass
class _AnswerOutcome:
    run_id: int
    message_id: Optional[int]
    error: Optional[str]


def _persist_route_run(conn, *, system_id: int, snapshot_id: int, route, now: float, completed_at: float) -> int:
    """Persist the Question Router sub-run (Issue #286) as its own audit row."""
    cur = conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             error_details, is_mock, started_at, completed_at)
        VALUES (?, ?, 'question_route', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)
        """,
        (
            system_id, snapshot_id, route.provider, route.model,
            route.prompt_version, route.schema_version,
            "failed" if route.error else "completed", route.error,
            1 if route.is_mock else 0, now, completed_at,
        ),
    )
    return cur.lastrowid


def _persist_investigation_run(
    conn, *, system_id: int, snapshot_id: int, investigation, now: float, completed_at: float,
) -> int:
    """Persist the Investigation Agent sub-run + its evidence rows (Issue #286).

    Every snippet actually read from the pinned snapshot is recorded here,
    regardless of whether the final answer cited it -- mirroring the
    interview dialogue's pass-1 evidence-audit pattern (Issue #137). Budget
    usage (files/chars/llm-calls/elapsed) is recorded on the run row itself
    for auditability.
    """
    cur = conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             error_details, is_mock, started_at, completed_at,
             budget_files_read, budget_chars_read, budget_llm_calls,
             budget_elapsed_seconds)
        VALUES (?, ?, 'investigation', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            system_id, snapshot_id, investigation.provider, investigation.model,
            investigation.prompt_version, investigation.schema_version,
            "failed" if investigation.error else "completed", investigation.error,
            1 if investigation.is_mock else 0, now, completed_at,
            investigation.files_read, investigation.chars_read, investigation.llm_calls,
            investigation.elapsed_seconds,
        ),
    )
    run_id = cur.lastrowid
    for snippet in investigation.read_snippets:
        conn.execute(
            """INSERT INTO intelligence_run_evidence
                (system_id, intelligence_run_id, path, start_line,
                 end_line, char_count, truncated, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, run_id, snippet.path, snippet.start_line,
                snippet.end_line, snippet.char_count,
                1 if snippet.truncated else 0, completed_at,
            ),
        )
    return run_id


def _generate_and_store_answer(
    conn,
    *,
    system_id: int,
    snapshot_id: int,
    inquiry_id: int,
    question_text: str,
    conversation: List[Dict[str, str]],
    origin_summary: Optional[str],
    now: float,
) -> _AnswerOutcome:
    """Run inquiry answer generation and persist the audit + message rows.

    Issue #286 reworked ``generate_inquiry_answer`` into a Question Router +
    Investigation Agent + Response Composer pipeline; each reasoning call
    (``result.route``, ``result.investigation``) is persisted here as its
    own ``intelligence_runs`` audit row before the overall 'inquiry_answer'
    row that records the composed outcome (Principle 7 -- every reasoning
    call is independently auditable).

    Fail-closed: on any error (mock/non-reasoning client, LLM call failure,
    invalid structured output, or a failed investigation), the
    'inquiry_answer' intelligence_runs row is recorded as 'failed' and NO
    assistant message is inserted -- the inquiry stays open with only the
    user's question. ``answerable=false`` is not an error: a fixed,
    non-LLM-fabricated message is stored instead of the model's own
    conclusion text (never fabricate, per Issue #285's brief).
    """
    context_pack = build_interview_context(conn, system_id, snapshot_id)
    snapshot_row = conn.execute(
        "SELECT repo_path, commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
        (snapshot_id, system_id),
    ).fetchone()
    config = LLMConfig.intelligence_from_env()
    try:
        client = create_llm_client(config)
        result = generate_inquiry_answer(
            client,
            config,
            context_pack=context_pack,
            question_text=question_text,
            conversation=conversation,
            origin_summary=origin_summary,
            repo_path=snapshot_row["repo_path"] if snapshot_row else None,
            commit_sha=snapshot_row["commit_sha"] if snapshot_row else None,
        )
    except LLMError as exc:
        result = InquiryAnswerResult(
            provider=config.provider,
            model=config.model,
            is_mock=config.provider == "mock",
            error=str(exc),
        )

    completed_at = time.time()

    if result.route is not None:
        _persist_route_run(
            conn, system_id=system_id, snapshot_id=snapshot_id, route=result.route,
            now=now, completed_at=completed_at,
        )
    if result.investigation is not None:
        _persist_investigation_run(
            conn, system_id=system_id, snapshot_id=snapshot_id,
            investigation=result.investigation, now=now, completed_at=completed_at,
        )

    run_status = "failed" if result.error else "completed"
    run_cur = conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             error_details, is_mock, started_at, completed_at)
        VALUES (?, ?, 'inquiry_answer', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)
        """,
        (
            system_id,
            snapshot_id,
            result.provider,
            result.model,
            result.prompt_version,
            result.schema_version,
            run_status,
            result.error,
            1 if result.is_mock else 0,
            now,
            completed_at,
        ),
    )
    run_id = run_cur.lastrowid

    if result.error:
        return _AnswerOutcome(run_id=run_id, message_id=None, error=result.error)

    if result.answerable:
        content = result.conclusion
        detail = {
            "key_points": result.key_points,
            "evidence": [
                {
                    "path": e.path, "start_line": e.start_line,
                    "end_line": e.end_line, "summary": e.summary,
                }
                for e in result.evidence
            ],
            "uncertainty": result.uncertainty,
            "route_category": result.route.category if result.route else None,
            "decision_question": result.decision_question,
        }
    else:
        content = interview_message(
            "inquiry_insufficient_information", resolve_message_language(),
        )
        detail = {
            "key_points": [], "evidence": [], "uncertainty": result.uncertainty,
            "route_category": result.route.category if result.route else None,
            "decision_question": None,
        }

    msg_cur = conn.execute(
        """INSERT INTO interview_inquiry_message
            (inquiry_id, system_id, role, content, detail, intelligence_run_id,
             is_mock, created_at)
        VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?)""",
        (
            inquiry_id,
            system_id,
            content,
            json.dumps(detail, ensure_ascii=False),
            run_id,
            1 if result.is_mock else 0,
            completed_at,
        ),
    )
    return _AnswerOutcome(run_id=run_id, message_id=msg_cur.lastrowid, error=None)


def _apply_transition(
    conn, inquiry_row, target_status: str, now: float, *, actor: Optional[str], reason: Optional[str],
):
    current = inquiry_row["status"]
    allowed = _TRANSITIONS.get(current, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invalid_inquiry_transition",
                "message": f"Cannot transition Inquiry from '{current}' to '{target_status}'",
            },
        )
    closed_at = now if target_status in _CLOSED_STATUSES else None
    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE interview_inquiry
               SET status = ?, status_reason = ?, updated_at = ?, closed_at = ?
               WHERE id = ?""",
            (target_status, reason, now, closed_at, inquiry_row["id"]),
        )
        conn.execute(
            """INSERT INTO interview_inquiry_transition
                (inquiry_id, system_id, from_status, to_status, actor, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (inquiry_row["id"], inquiry_row["system_id"], current, target_status, actor, reason, now),
        )
        # Issue #287: a closing transition (resolved/unresolved/cancelled)
        # releases a review_item back to 'open' -- never 'answered'. 'held'
        # is deliberately excluded (not in _CLOSED_STATUSES): the Inquiry is
        # only paused, not closed, so the item stays 'inquiry' until it
        # actually resolves/unresolves/cancels.
        if target_status in _CLOSED_STATUSES:
            _set_review_item_status(
                conn,
                origin_kind=inquiry_row["origin_kind"],
                origin_id=inquiry_row["origin_id"],
                session_id=inquiry_row["session_id"],
                system_id=inquiry_row["system_id"],
                status="open",
                now=now,
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return conn.execute(
        "SELECT * FROM interview_inquiry WHERE id = ?", (inquiry_row["id"],)
    ).fetchone()


# --- Create / list / detail ---------------------------------------------------


@router.post(
    "/interview/sessions/{session_id}/inquiries",
    response_model=InterviewInquiryDetailOut,
    status_code=201,
)
def create_inquiry(
    session_id: int,
    payload: InterviewInquiryCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewInquiryDetailOut:
    """Open an Inquiry about a confirmation item.

    The origin ``interview_qa`` / ``interview_intent_item`` row is NOT
    modified by this call -- it stays exactly as it was.
    ``origin_kind='review_item'`` (Issue #287) is the one exception: its
    ``alignment_item.status`` is set to 'inquiry' so the Review Queue shows
    it as blocked pending this Inquiry (see module docstring). The Inquiry
    itself starts 'open' with the developer's question as its first
    message; the initial assistant answer is generated immediately (see
    ``app/inquiry_answering.py``). On LLM failure the Inquiry and the user's
    question are still persisted (so the developer can retry via
    ``/message``); only the assistant message is withheld, and the response
    is a 502 whose detail carries the created ``inquiry_id``.
    """
    if payload.origin_kind not in ORIGIN_KINDS:
        raise HTTPException(status_code=422, detail="Invalid origin_kind")
    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        origin_summary = _validate_origin_exists(
            conn, payload.origin_kind, payload.origin_id, session_id, system_id,
        )

        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO interview_inquiry
                    (session_id, system_id, origin_kind, origin_id, held_draft,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
                (
                    session_id, system_id, payload.origin_kind, payload.origin_id,
                    payload.held_draft, now, now,
                ),
            )
            inquiry_id = cur.lastrowid
            conn.execute(
                """INSERT INTO interview_inquiry_message
                    (inquiry_id, system_id, role, content, created_at)
                VALUES (?, ?, 'user', ?, ?)""",
                (inquiry_id, system_id, payload.question_text, now),
            )
            _set_review_item_status(
                conn,
                origin_kind=payload.origin_kind,
                origin_id=payload.origin_id,
                session_id=session_id,
                system_id=system_id,
                status="inquiry",
                now=now,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        outcome = _generate_and_store_answer(
            conn,
            system_id=system_id,
            snapshot_id=session["snapshot_id"],
            inquiry_id=inquiry_id,
            question_text=payload.question_text,
            conversation=[{"role": "user", "content": payload.question_text}],
            origin_summary=origin_summary,
            now=now,
        )
        if outcome.error:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "inquiry_answer_failed",
                    "message": outcome.error,
                    "inquiry_id": inquiry_id,
                },
            )

        inquiry_row = conn.execute(
            "SELECT * FROM interview_inquiry WHERE id = ?", (inquiry_id,),
        ).fetchone()
        messages = conn.execute(
            "SELECT * FROM interview_inquiry_message WHERE inquiry_id = ? ORDER BY id",
            (inquiry_id,),
        ).fetchall()
        return InterviewInquiryDetailOut(
            inquiry=_inquiry_out(inquiry_row),
            messages=[_message_out(m) for m in messages],
        )


@router.get(
    "/interview/sessions/{session_id}/inquiries",
    response_model=InterviewInquiryListOut,
)
def list_inquiries(
    session_id: int,
    status: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> InterviewInquiryListOut:
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        if status is not None:
            rows = conn.execute(
                """SELECT * FROM interview_inquiry
                   WHERE session_id = ? AND system_id = ? AND status = ?
                   ORDER BY id""",
                (session_id, system_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM interview_inquiry
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY id""",
                (session_id, system_id),
            ).fetchall()
        return InterviewInquiryListOut(
            session_id=session_id, system_id=system_id,
            items=[_inquiry_out(r) for r in rows],
        )


@router.get(
    "/interview/inquiries/{inquiry_id}",
    response_model=InterviewInquiryDetailOut,
)
def get_inquiry(
    inquiry_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewInquiryDetailOut:
    """Fetch an Inquiry with its full message history.

    Returns everything needed to restore the dashboard's Inquiry panel after
    a refresh/resume: ``held_draft``, ``origin_kind``/``origin_id``, status,
    and every message in order.
    """
    with get_conn() as conn:
        inquiry = _get_inquiry_or_404(conn, inquiry_id, system_id)
        messages = conn.execute(
            "SELECT * FROM interview_inquiry_message WHERE inquiry_id = ? AND system_id = ? ORDER BY id",
            (inquiry_id, system_id),
        ).fetchall()
        return InterviewInquiryDetailOut(
            inquiry=_inquiry_out(inquiry),
            messages=[_message_out(m) for m in messages],
        )


@router.post(
    "/interview/inquiries/{inquiry_id}/message",
    response_model=InterviewInquiryDetailOut,
)
def post_inquiry_message(
    inquiry_id: int,
    payload: InterviewInquiryMessageCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewInquiryDetailOut:
    """Add a follow-up question and generate a new assistant answer.

    Only allowed while the Inquiry is 'open' (409 otherwise) -- once
    resolved/unresolved/held/cancelled, the conversation is closed; 'held'
    must be resumed first via ``/resume``.
    """
    now = time.time()
    with get_conn() as conn:
        inquiry = _get_inquiry_or_404(conn, inquiry_id, system_id)
        if inquiry["status"] != "open":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "inquiry_not_open",
                    "message": f"Inquiry is '{inquiry['status']}', not open",
                },
            )
        session = _get_session_or_404(conn, inquiry["session_id"], system_id)
        origin_summary = _validate_origin_exists(
            conn, inquiry["origin_kind"], inquiry["origin_id"], inquiry["session_id"], system_id,
        )

        prior_rows = conn.execute(
            "SELECT role, content FROM interview_inquiry_message WHERE inquiry_id = ? ORDER BY id",
            (inquiry_id,),
        ).fetchall()
        conversation = [{"role": r["role"], "content": r["content"]} for r in prior_rows]

        conn.execute(
            """INSERT INTO interview_inquiry_message
                (inquiry_id, system_id, role, content, created_at)
            VALUES (?, ?, 'user', ?, ?)""",
            (inquiry_id, system_id, payload.content, now),
        )
        conn.execute(
            "UPDATE interview_inquiry SET updated_at = ? WHERE id = ?", (now, inquiry_id),
        )
        conversation.append({"role": "user", "content": payload.content})

        outcome = _generate_and_store_answer(
            conn,
            system_id=system_id,
            snapshot_id=session["snapshot_id"],
            inquiry_id=inquiry_id,
            question_text=payload.content,
            conversation=conversation,
            origin_summary=origin_summary,
            now=now,
        )
        if outcome.error:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "inquiry_answer_failed",
                    "message": outcome.error,
                    "inquiry_id": inquiry_id,
                },
            )

        inquiry_row = conn.execute(
            "SELECT * FROM interview_inquiry WHERE id = ?", (inquiry_id,),
        ).fetchone()
        messages = conn.execute(
            "SELECT * FROM interview_inquiry_message WHERE inquiry_id = ? ORDER BY id",
            (inquiry_id,),
        ).fetchall()
        return InterviewInquiryDetailOut(
            inquiry=_inquiry_out(inquiry_row),
            messages=[_message_out(m) for m in messages],
        )


# --- Status transitions --------------------------------------------------------


@router.post("/interview/inquiries/{inquiry_id}/resolve", response_model=InterviewInquiryOut)
def resolve_inquiry(
    inquiry_id: int, system_id: int = Depends(get_system_id),
) -> InterviewInquiryOut:
    """「疑問は解消した」. Closes the Inquiry -- never touches the origin item.

    The response carries origin_kind/origin_id/held_draft (already part of
    InterviewInquiryOut) so the dashboard can return to the original item
    and restore the held draft into its input. The developer must still
    explicitly submit that item's own answer/confirm afterward -- resolving
    an Inquiry is never treated as answering or confirming it.
    """
    now = time.time()
    with get_conn() as conn:
        inquiry = _get_inquiry_or_404(conn, inquiry_id, system_id)
        row = _apply_transition(conn, inquiry, "resolved", now, actor="user", reason=None)
        return _inquiry_out(row)


@router.post("/interview/inquiries/{inquiry_id}/unresolved", response_model=InterviewInquiryOut)
def mark_inquiry_unresolved(
    inquiry_id: int,
    payload: Optional[InterviewInquiryTransitionRequest] = None,
    system_id: int = Depends(get_system_id),
) -> InterviewInquiryOut:
    """The assistant could not answer; record why (e.g. 'research_required')."""
    now = time.time()
    reason = payload.status_reason if payload else None
    actor = (payload.actor if payload and payload.actor else "user")
    with get_conn() as conn:
        inquiry = _get_inquiry_or_404(conn, inquiry_id, system_id)
        row = _apply_transition(conn, inquiry, "unresolved", now, actor=actor, reason=reason)
        return _inquiry_out(row)


@router.post("/interview/inquiries/{inquiry_id}/hold", response_model=InterviewInquiryOut)
def hold_inquiry(
    inquiry_id: int,
    payload: Optional[InterviewInquiryTransitionRequest] = None,
    system_id: int = Depends(get_system_id),
) -> InterviewInquiryOut:
    """「今回は保留する」. Held Inquiries can be resumed later via /resume."""
    now = time.time()
    reason = payload.status_reason if payload else None
    actor = (payload.actor if payload and payload.actor else "user")
    with get_conn() as conn:
        inquiry = _get_inquiry_or_404(conn, inquiry_id, system_id)
        row = _apply_transition(conn, inquiry, "held", now, actor=actor, reason=reason)
        return _inquiry_out(row)


@router.post("/interview/inquiries/{inquiry_id}/resume", response_model=InterviewInquiryOut)
def resume_inquiry(
    inquiry_id: int, system_id: int = Depends(get_system_id),
) -> InterviewInquiryOut:
    """Resume a held Inquiry back to 'open' so /message can be used again."""
    now = time.time()
    with get_conn() as conn:
        inquiry = _get_inquiry_or_404(conn, inquiry_id, system_id)
        row = _apply_transition(conn, inquiry, "open", now, actor="user", reason=None)
        return _inquiry_out(row)


@router.post("/interview/inquiries/{inquiry_id}/cancel", response_model=InterviewInquiryOut)
def cancel_inquiry(
    inquiry_id: int,
    payload: Optional[InterviewInquiryTransitionRequest] = None,
    system_id: int = Depends(get_system_id),
) -> InterviewInquiryOut:
    now = time.time()
    reason = payload.status_reason if payload else None
    actor = (payload.actor if payload and payload.actor else "user")
    with get_conn() as conn:
        inquiry = _get_inquiry_or_404(conn, inquiry_id, system_id)
        row = _apply_transition(conn, inquiry, "cancelled", now, actor=actor, reason=reason)
        return _inquiry_out(row)


@router.post("/interview/inquiries/{inquiry_id}/reopen-doubt", response_model=InterviewInquiryOut)
def reopen_inquiry_doubt(
    inquiry_id: int, system_id: int = Depends(get_system_id),
) -> InterviewInquiryOut:
    """「解消していない」(追加で質問する). No-op transition: stays 'open'.

    Only valid while already 'open' (a closed/held Inquiry has nothing to
    "still not be resolved" about; use /resume first for a held one). Exists
    to record the developer's explicit "not resolved yet" decision in the
    audit trail even when nothing about the status itself changes.
    """
    now = time.time()
    with get_conn() as conn:
        inquiry = _get_inquiry_or_404(conn, inquiry_id, system_id)
        if inquiry["status"] != "open":
            # Distinct from /resume: reopen-doubt only records "still not
            # resolved" on an already-open Inquiry, it never transitions a
            # held/closed one back to open by itself.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "invalid_inquiry_transition",
                    "message": f"Cannot reopen-doubt from '{inquiry['status']}'",
                },
            )
        row = _apply_transition(
            conn, inquiry, "open", now, actor="user", reason="doubt_not_resolved",
        )
        return _inquiry_out(row)
