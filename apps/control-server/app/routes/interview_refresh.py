"""Automatic-refresh status/retry API (Issue #288).

Kept in its own module (like Issues #284/#285/#287) rather than growing
``routes/interview.py`` further, per CLAUDE.md's guidance for this
sub-issue. The actual orchestration lives in ``app/interview_refresh.py``;
this module only exposes it over HTTP.

probe-agent:
  role: API boundary for automatic-refresh job status and manual retry
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify refresh-status reflects the latest job for the session and retry is rejected for a non-failed job.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..interview_refresh import (
    RefreshJobConflict,
    RefreshJobNotFound,
    retry_refresh_job,
)
from ..models import RefreshJobOut, RefreshStatusOut

router = APIRouter()


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _job_out(row) -> RefreshJobOut:
    return RefreshJobOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        trigger_kind=row["trigger_kind"],
        status=row["status"],
        error=row["error"],
        intelligence_run_id=row["intelligence_run_id"],
        result_revision_id=row["result_revision_id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


@router.get(
    "/interview/sessions/{session_id}/refresh-status",
    response_model=RefreshStatusOut,
)
def get_refresh_status(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> RefreshStatusOut:
    """The latest automatic-refresh job for this session, plus how many are
    still queued (0 or 1 by the dedupe rule, never more)."""
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        latest = conn.execute(
            """SELECT * FROM interview_refresh_job
               WHERE session_id = ? AND system_id = ?
               ORDER BY id DESC LIMIT 1""",
            (session_id, system_id),
        ).fetchone()
        pending_count = conn.execute(
            """SELECT COUNT(*) FROM interview_refresh_job
               WHERE session_id = ? AND system_id = ? AND status = 'pending'""",
            (session_id, system_id),
        ).fetchone()[0]
        return RefreshStatusOut(
            session_id=session_id,
            system_id=system_id,
            latest_job=_job_out(latest) if latest is not None else None,
            pending_count=pending_count,
        )


@router.post(
    "/interview/sessions/{session_id}/refresh-jobs/{job_id}/retry",
    response_model=RefreshJobOut,
)
def retry_refresh_job_route(
    session_id: int,
    job_id: int,
    system_id: int = Depends(get_system_id),
) -> RefreshJobOut:
    """Manually retry a failed automatic-refresh job (障害復旧・診断用).

    This -- and the existing manual 「理解を更新」 endpoint -- are the only
    retry paths; a successful automatic refresh needs no manual action.
    """
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)

    try:
        new_job_id = retry_refresh_job(job_id, session_id, system_id)
    except RefreshJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RefreshJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM interview_refresh_job WHERE id = ?", (new_job_id,)
        ).fetchone()
        return _job_out(row)
