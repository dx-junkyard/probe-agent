"""Startup / periodic recovery for publish jobs interrupted by a crash or
server restart, and bounded automatic retry (Issue #226).

Two independent concerns:

* ``repair_interrupted_jobs`` fails over jobs stuck in an in-flight status
  whose heartbeat has gone stale (the worker thread that was driving them is
  gone -- a crash or restart): prepare-phase statuses
  (authenticating/fetching/checking_out/applying_patch/validating) become
  terminal ``failed`` (nothing was ever pushed, so there is nothing to
  reconcile); publish-phase statuses (committing/pushing/creating_pr) and
  ``reconciling`` become the resting ``retryable_failed`` (a push may
  already have happened -- retry re-derives the correct next step from the
  actual remote state instead of blindly redoing it). Also opportunistically
  clears expired connection leases. Called once synchronously from
  ``app/main.py``'s lifespan at startup (``reason="startup_recovery"``) and
  once per tick from the periodic worker below (``reason="stuck_detected"``)
  -- both honor the same heartbeat staleness threshold, so a job that just
  started before an instant restart is not immediately misclassified; the
  periodic worker's next tick(s) will catch it once its heartbeat is
  genuinely stale.
* ``auto_retry_eligible_jobs`` retries ``retryable_failed`` jobs one at a
  time, fully synchronously, capped at ``PUBLISH_AUTO_RETRY_MAX`` attempts
  per job. ``manual_intervention_required`` jobs are never auto-retried --
  that status means the remote branch does not match what probe-agent
  expects, and only a human retry (or cancel) may proceed.

The worker thread (``start_worker`` / ``stop_worker``) is wired up from
``app/main.py``'s lifespan: started after ``init_db()``, stopped on
shutdown. It only ticks once per ``PUBLISH_RECOVERY_INTERVAL_SECONDS`` (the
first tick happens only after a full interval has elapsed, so a short-lived
TestClient lifespan in tests never ticks); a value <= 0 disables the thread
entirely.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import List, Optional

from . import publish_job
from .db import get_conn
from .publish_audit import record_publish_audit_event

logger = logging.getLogger(__name__)

_PREPARE_INFLIGHT_STATUSES = (
    "authenticating",
    "fetching",
    "checking_out",
    "applying_patch",
    "validating",
)
_PUBLISH_INFLIGHT_STATUSES = ("committing", "pushing", "creating_pr", "reconciling")

_INTERRUPTED_PREPARE_ERROR = "Interrupted publish preparation (server restart or stall)"
_INTERRUPTED_PUBLISH_ERROR = "Interrupted publish (server restart or stall); retry to resume"


def _now() -> float:
    return time.time()


def stuck_threshold_seconds() -> float:
    try:
        return float(os.getenv("PUBLISH_STUCK_THRESHOLD_SECONDS", "900"))
    except (TypeError, ValueError):
        return 900.0


def auto_retry_max() -> int:
    try:
        return int(os.getenv("PUBLISH_AUTO_RETRY_MAX", "3"))
    except (TypeError, ValueError):
        return 3


def recovery_interval_seconds() -> float:
    try:
        return float(os.getenv("PUBLISH_RECOVERY_INTERVAL_SECONDS", "300"))
    except (TypeError, ValueError):
        return 300.0


def _stale_job_ids(conn, statuses) -> List[int]:
    cutoff = _now() - stuck_threshold_seconds()
    placeholders = ", ".join("?" for _ in statuses)
    rows = conn.execute(
        f"""
        SELECT id FROM publish_jobs
        WHERE status IN ({placeholders})
          AND (heartbeat_at IS NULL OR heartbeat_at < ?)
        """,
        (*statuses, cutoff),
    ).fetchall()
    return [row["id"] for row in rows]


def repair_interrupted_jobs(*, reason: str) -> int:
    """Fail over jobs whose worker thread is gone (crash/restart) so they
    become retryable instead of stuck active forever. Returns the number of
    jobs repaired. Also opportunistically clears expired connection leases
    (a crashed reconcile phase would otherwise leave one behind until its
    natural expiry)."""
    with get_conn() as conn:
        prepare_ids = _stale_job_ids(conn, _PREPARE_INFLIGHT_STATUSES)
        publish_ids = _stale_job_ids(conn, _PUBLISH_INFLIGHT_STATUSES)
        conn.execute("DELETE FROM publish_connection_leases WHERE expires_at < ?", (_now(),))

    repaired = 0
    for job_id in prepare_ids:
        job = publish_job._get_job_row(job_id)
        if job is None:
            continue
        cleanup = publish_job._safe_cleanup_worktree(job["connection_id"], job_id)
        applied = publish_job._set_status(
            job_id,
            "failed",
            error=_INTERRUPTED_PREPARE_ERROR,
            completed_at=_now(),
            cleanup_state=cleanup.state,
            cleanup_error=cleanup.error,
            audit_reason=reason,
        )
        if applied:
            repaired += 1

    for job_id in publish_ids:
        job = publish_job._get_job_row(job_id)
        if job is None:
            continue
        cleanup = publish_job._safe_cleanup_worktree(job["connection_id"], job_id)
        applied = publish_job._set_status(
            job_id,
            "retryable_failed",
            error=_INTERRUPTED_PUBLISH_ERROR,
            cleanup_state=cleanup.state,
            cleanup_error=cleanup.error,
            audit_reason=reason,
        )
        if applied:
            repaired += 1

    return repaired


def auto_retry_eligible_jobs() -> int:
    """Retry ``retryable_failed`` jobs under ``PUBLISH_AUTO_RETRY_MAX``, one
    at a time and fully synchronously (this already runs on the recovery
    worker thread, never spawning another). ``manual_intervention_required``
    jobs are never selected here. Returns the number of jobs retried."""
    max_retries = auto_retry_max()
    retried = 0
    while True:
        now = _now()
        with get_conn() as conn:
            candidate = conn.execute(
                """
                SELECT pj.id AS id FROM publish_jobs AS pj
                JOIN github_connections AS gc ON gc.id = pj.connection_id
                WHERE pj.status = 'retryable_failed' AND pj.retry_count < ?
                  AND gc.status = 'connected'
                ORDER BY pj.id
                LIMIT 1
                """,
                (max_retries,),
            ).fetchone()
            if candidate is None:
                break
            job_id = candidate["id"]
            before = conn.execute(
                "SELECT * FROM publish_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            cur = conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'reconciling', retry_count = retry_count + 1, last_attempt_at = ?,
                    updated_at = ?, heartbeat_at = ?
                WHERE id = ? AND status = 'retryable_failed' AND retry_count < ?
                  AND (SELECT status FROM github_connections
                       WHERE id = publish_jobs.connection_id) = 'connected'
                """,
                (now, now, now, job_id, max_retries),
            )
            if cur.rowcount == 0:
                # Lost a race (e.g. a manual retry/cancel/disconnect just
                # landed) -- move on rather than looping on the same job.
                continue
            after = conn.execute(
                "SELECT * FROM publish_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            record_publish_audit_event(
                conn,
                system_id=before["system_id"],
                connection_id=before["connection_id"],
                job_id=job_id,
                event_type="publish_job_status_transition",
                detail={"from": "retryable_failed", "to": "reconciling"},
            )
            record_publish_audit_event(
                conn,
                system_id=before["system_id"],
                connection_id=before["connection_id"],
                job_id=job_id,
                event_type="publish_job_auto_retry",
                detail={"retry_count": after["retry_count"]},
            )
        publish_job._run_reconcile_phase(job_id)
        retried += 1
    return retried


# ---------------------------------------------------------------------------
# Periodic worker thread
# ---------------------------------------------------------------------------

_worker_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None


def _worker_loop(stop_event: "threading.Event") -> None:
    interval = recovery_interval_seconds()
    # Wait a full interval before the first tick, so a short-lived process
    # (e.g. a test's TestClient lifespan) never ticks.
    while not stop_event.wait(interval):
        try:
            repair_interrupted_jobs(reason="stuck_detected")
            auto_retry_eligible_jobs()
        except Exception:  # pragma: no cover - defensive
            logger.exception("publish recovery worker tick failed")


def start_worker() -> Optional[threading.Thread]:
    """Start the periodic recovery worker. A non-positive
    ``PUBLISH_RECOVERY_INTERVAL_SECONDS`` disables it entirely (returns
    None)."""
    global _worker_thread, _stop_event
    if recovery_interval_seconds() <= 0:
        return None
    stop_event = threading.Event()
    thread = threading.Thread(target=_worker_loop, args=(stop_event,), daemon=True)
    _worker_thread = thread
    _stop_event = stop_event
    thread.start()
    return thread


def stop_worker(timeout: float = 5.0) -> None:
    global _worker_thread, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=timeout)
    _worker_thread = None
    _stop_event = None
