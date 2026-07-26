"""Interview UX measurement API (Issue #309).

The event endpoint accepts only a finite, content-free schema for UI facts
that domain tables cannot reconstruct. The aggregate endpoint reads local
SQLite state for the selected System; it never calls an LLM or sends data to
an external analytics service.
"""

from __future__ import annotations

import time
from typing import Dict, Set

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..interview_metrics import build_interview_metrics
from ..models import (
    InterviewMetricEventCreate,
    InterviewMetricEventOut,
    InterviewMetricsOut,
)

router = APIRouter()

_EVENT_TARGETS: Dict[str, Set[str]] = {
    "review_started": {"session"},
    "review_completed": {"session"},
    "review_abandoned": {"session"},
    "evidence_available": {"qa", "alignment_item", "inquiry_message"},
    "evidence_expanded": {"qa", "alignment_item", "inquiry_message"},
    "unchanged_item_presented": {"alignment_item"},
    "unchanged_item_reconfirmed": {"alignment_item"},
    "question_presented": {"qa"},
}

_EVENT_PREREQUISITES: Dict[str, str] = {
    "review_completed": "review_started",
    "review_abandoned": "review_started",
    "evidence_expanded": "evidence_available",
    "unchanged_item_reconfirmed": "unchanged_item_presented",
}


def _event_out(row) -> InterviewMetricEventOut:
    return InterviewMetricEventOut(
        id=row["id"],
        event_key=row["event_key"],
        system_id=row["system_id"],
        session_id=row["session_id"],
        event_type=row["event_type"],
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        recorded_at=row["recorded_at"],
    )


def _validate_event_target(conn, payload: InterviewMetricEventCreate, system_id: int) -> None:
    allowed_targets = _EVENT_TARGETS[payload.event_type]
    if payload.target_kind not in allowed_targets:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_metric_event_target",
                "message": (
                    f"{payload.event_type} does not accept target_kind "
                    f"{payload.target_kind}"
                ),
            },
        )

    session = conn.execute(
        """SELECT id FROM interview_session
           WHERE id = ? AND system_id = ?""",
        (payload.session_id, system_id),
    ).fetchone()
    if session is None:
        raise HTTPException(status_code=404, detail="Interview session not found")

    if payload.target_kind == "session":
        if payload.target_id != payload.session_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "metric_event_session_target_mismatch",
                    "message": "session target_id must equal session_id",
                },
            )
        return

    if payload.target_kind == "qa":
        row = conn.execute(
            """SELECT id FROM interview_qa
               WHERE id = ? AND session_id = ? AND system_id = ?""",
            (payload.target_id, payload.session_id, system_id),
        ).fetchone()
    elif payload.target_kind == "alignment_item":
        row = conn.execute(
            """SELECT id, review_category FROM alignment_item
               WHERE id = ? AND session_id = ? AND system_id = ?""",
            (payload.target_id, payload.session_id, system_id),
        ).fetchone()
        if (
            row is not None
            and payload.event_type.startswith("unchanged_item_")
            and row["review_category"] != "unchanged"
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "metric_event_target_not_unchanged",
                    "message": "unchanged-item events require an unchanged alignment item",
                },
            )
    else:
        row = conn.execute(
            """SELECT m.id
               FROM interview_inquiry_message m
               JOIN interview_inquiry i
                 ON i.id = m.inquiry_id
                AND i.system_id = m.system_id
               WHERE m.id = ? AND i.session_id = ? AND i.system_id = ?""",
            (payload.target_id, payload.session_id, system_id),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Metric event target not found")


def _find_semantic_event(conn, payload: InterviewMetricEventCreate, system_id: int):
    """Return the one persisted fact for this semantic observation, if any.

    ``event_key`` is a transport retry key. It is deliberately not part of the
    semantic identity: two clients/remounts observing the same event for the
    same target must not create two metric facts merely because their keys
    differ.
    """

    return conn.execute(
        """SELECT * FROM interview_metric_event
           WHERE system_id = ? AND session_id = ? AND event_type = ?
             AND target_kind = ? AND target_id = ?
           ORDER BY id
           LIMIT 1""",
        (
            system_id,
            payload.session_id,
            payload.event_type,
            payload.target_kind,
            payload.target_id,
        ),
    ).fetchone()


def _validate_event_prerequisite(
    conn,
    payload: InterviewMetricEventCreate,
    system_id: int,
) -> None:
    prerequisite = _EVENT_PREREQUISITES.get(payload.event_type)
    if prerequisite is None:
        return
    row = conn.execute(
        """SELECT 1 FROM interview_metric_event
           WHERE system_id = ? AND session_id = ? AND event_type = ?
             AND target_kind = ? AND target_id = ?
           LIMIT 1""",
        (
            system_id,
            payload.session_id,
            prerequisite,
            payload.target_kind,
            payload.target_id,
        ),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "metric_event_prerequisite_missing",
                "message": (
                    f"{payload.event_type} requires a prior {prerequisite} "
                    "event for the same target"
                ),
                "required_event_type": prerequisite,
            },
        )


@router.post(
    "/interview/metric-events",
    response_model=InterviewMetricEventOut,
    status_code=201,
)
def create_interview_metric_event(
    payload: InterviewMetricEventCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewMetricEventOut:
    """Append one validated event with transport and semantic idempotency."""

    with get_conn() as conn:
        # Serialize the read-before-insert sequence across SQLite connections.
        # SCHEMA intentionally remains unchanged; BEGIN IMMEDIATE closes the
        # multi-worker race that an application-only semantic lookup would
        # otherwise leave open.
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                """SELECT * FROM interview_metric_event
                   WHERE system_id = ? AND event_key = ?""",
                (system_id, payload.event_key),
            ).fetchone()
            if existing is not None:
                matches = (
                    existing["session_id"] == payload.session_id
                    and existing["event_type"] == payload.event_type
                    and existing["target_kind"] == payload.target_kind
                    and existing["target_id"] == payload.target_id
                )
                if not matches:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "metric_event_key_conflict",
                            "message": "event_key is already used by a different metric event",
                        },
                    )
                conn.execute("COMMIT")
                return _event_out(existing)

            _validate_event_target(conn, payload, system_id)

            semantic_existing = _find_semantic_event(conn, payload, system_id)
            if semantic_existing is not None:
                conn.execute("COMMIT")
                return _event_out(semantic_existing)

            _validate_event_prerequisite(conn, payload, system_id)
            recorded_at = time.time()
            cur = conn.execute(
                """INSERT INTO interview_metric_event
                    (event_key, system_id, session_id, event_type,
                     target_kind, target_id, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload.event_key,
                    system_id,
                    payload.session_id,
                    payload.event_type,
                    payload.target_kind,
                    payload.target_id,
                    recorded_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM interview_metric_event WHERE id = ?",
                (cur.lastrowid,),
            ).fetchone()
            conn.execute("COMMIT")
            return _event_out(row)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


@router.get("/interview/metrics", response_model=InterviewMetricsOut)
def get_interview_metrics(
    system_id: int = Depends(get_system_id),
) -> InterviewMetricsOut:
    """Return the fixed deterministic v1 metric catalog for one System."""

    with get_conn() as conn:
        return build_interview_metrics(conn, system_id)
