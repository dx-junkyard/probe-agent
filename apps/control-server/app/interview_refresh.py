"""Automatic refresh after an answer batch (Issue #288).

After a Q&A answer, an Intent confirm/correct, an Alignment answer/correct,
or an applied NL change set is durably committed, this module refreshes the
affected derived state without requiring the manual 「理解を更新」 action.
Q&A/Alignment feedback regenerates Understanding; Intent/change-set updates
reuse the latest persisted revision and rebuild Alignment/Review Queue.
Saved answers are never coupled to refresh success, execution is idempotent,
and stale/superseded results never overwrite newer state.

Design (see the Issue #288 section of ``docs/project-intelligence.md`` for
the full write-up):

- One ``interview_refresh_job`` row per refresh attempt.
  ``request_refresh()`` dedupes so at most one ``pending`` and one
  ``updating`` job exist per session at a time: a rapid burst of answers
  collapses into a single follow-up job that, once it runs, sees every
  answer saved so far (bounded batch, no revision explosion).
- ``run_refresh_job()`` reuses the *exact* same rebuild code paths as the
  manual endpoints (``routes.interview._rebuild_understanding`` and
  ``routes.interview_alignment.run_alignment_build``) rather than
  duplicating persistence/reasoning logic (Principle 6/7 auditability: the
  job's ``intelligence_run_id``/``result_revision_id`` are the same ids the
  manual path would have produced).
- Per-session execution is serialized by an in-process lock
  (``_lock_for_session``) so two jobs for the same session can never run
  concurrently, and a job that is superseded by a newer, already-completed
  job for the same session is marked ``stale`` and writes nothing (checked
  before any work starts, so a stale run never touches
  ``interview_session``/``understanding_revision``/``alignment_item``).
- Execution contract (Issue #315): the Control Server must run as exactly
  one OS process / one ASGI worker, with no second replica sharing its
  database.  Both ``_lock_for_session`` and ``db.get_conn()`` serialization
  are process-local, and the staleness check is intentionally performed only
  before work starts.  The production image pins Uvicorn to ``--workers 1``.
  A multi-worker deployment is unsupported until job claiming and
  per-session serialization are database/distributed-lock backed and every
  result is revalidated after inference immediately before its writes.
- ``PROBE_REFRESH_EAGER=1`` runs a newly enqueued job synchronously on the
  caller's thread instead of a background thread -- used by the test suite
  (see ``tests/conftest.py``) for deterministic assertions without racing a
  background thread.

probe-agent:
  role: Automatic Understanding/Alignment/Review-Queue refresh orchestration
    after an interview answer batch
  capability: interactive-system-understanding
  element_type: element
  consumers: [control-server]
  operation_kind: orchestration
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify dedupe never loses an answer batch, a stale job never
    overwrites a newer completed job's result, and a failed job leaves
    previously saved answers and understanding untouched.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from fastapi import HTTPException

from .db import get_conn
from .state_messages import refresh_job_message

logger = logging.getLogger(__name__)

TRIGGER_KINDS = ("qa_answer", "intent_update", "alignment_answer", "nl_change_set")
JOB_STATUSES = ("pending", "updating", "updated", "failed", "stale")

# A pending job represents a bounded batch, so a later trigger can be merged
# into its existing row.  Preserve the strongest processing requirement:
# Alignment feedback must rebuild Understanding even when the ordinary
# post-confirmation Q&A gate is closed; a Q&A answer also rebuilds
# Understanding; Intent/change-set updates only need Alignment rebuilt from
# the latest already-persisted revision.
_TRIGGER_PRIORITY = {
    "intent_update": 0,
    "nl_change_set": 0,
    "qa_answer": 1,
    "alignment_answer": 2,
}
_UNDERSTANDING_REBUILD_TRIGGERS = {"qa_answer", "alignment_answer"}

# One lock per session_id so refresh jobs for different sessions can run
# concurrently, while jobs for the *same* session are always serialized
# inside the supported single-process / single-ASGI-worker deployment
# contract.  This lock does NOT coordinate separate OS processes or replicas;
# see the module-level Issue #315 contract before changing the server topology.
_session_locks_guard = threading.Lock()
_session_locks: dict = {}


def _lock_for_session(session_id: int) -> threading.Lock:
    with _session_locks_guard:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _session_locks[session_id] = lock
        return lock


def _eager_mode() -> bool:
    return os.getenv("PROBE_REFRESH_EAGER", "0") == "1"


def _enqueue(conn, session_id: int, system_id: int, trigger_kind: str):
    """Insert a job row per the dedupe rule; returns (job_id, should_dispatch).

    - a 'pending' job already exists -> no new row; the existing job will
      pick up this trigger's answers when it runs (bounded batch), and its
      trigger_kind is promoted when the new input needs stronger processing.
    - an 'updating' job exists (no pending yet) -> insert exactly one
      'pending' follow-up job; do not dispatch now (the in-flight job's
      completion drains it, see ``run_refresh_job``).
    - neither exists -> insert a 'pending' job and dispatch immediately.
    """
    pending = conn.execute(
        """SELECT id, trigger_kind FROM interview_refresh_job
           WHERE session_id = ? AND status = 'pending'
           ORDER BY id DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    if pending is not None:
        merged_trigger = max(
            (pending["trigger_kind"], trigger_kind),
            key=lambda value: _TRIGGER_PRIORITY[value],
        )
        if merged_trigger != pending["trigger_kind"]:
            conn.execute(
                """UPDATE interview_refresh_job
                   SET trigger_kind = ?
                   WHERE id = ? AND status = 'pending'""",
                (merged_trigger, pending["id"]),
            )
        return pending["id"], False

    updating = conn.execute(
        """SELECT id FROM interview_refresh_job
           WHERE session_id = ? AND status = 'updating'
           ORDER BY id DESC LIMIT 1""",
        (session_id,),
    ).fetchone()

    base_revision = conn.execute(
        """SELECT id FROM understanding_revision
           WHERE session_id = ? AND system_id = ?
           ORDER BY id DESC LIMIT 1""",
        (session_id, system_id),
    ).fetchone()

    now = time.time()
    job_id = conn.execute(
        """INSERT INTO interview_refresh_job
            (session_id, system_id, trigger_kind, base_revision_id,
             base_answer_marker, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
        (
            session_id, system_id, trigger_kind,
            base_revision["id"] if base_revision else None,
            now, now,
        ),
    ).lastrowid
    return job_id, updating is None


def request_refresh(session_id: int, system_id: int, trigger_kind: str) -> Optional[int]:
    """Enqueue (and, unless a job is already running, dispatch) a refresh.

    Called AFTER the triggering answer/decision has already been committed
    by the caller's own connection/transaction -- answer persistence must
    never be coupled to refresh success (Principle 1). Opens its own
    connection rather than accepting one, so it must never be called from
    inside an existing ``with get_conn()`` block (``db.get_conn`` serializes
    all DB access through a single process-wide lock; nesting would
    deadlock).
    """
    if trigger_kind not in TRIGGER_KINDS:
        raise ValueError(f"Unknown interview_refresh_job trigger_kind: {trigger_kind!r}")

    with get_conn() as conn:
        job_id, should_dispatch = _enqueue(conn, session_id, system_id, trigger_kind)

    if should_dispatch:
        _dispatch(job_id)
    return job_id


def _dispatch(job_id: int) -> None:
    if _eager_mode():
        run_refresh_job(job_id)
        return
    thread = threading.Thread(target=_run_safely, args=(job_id,), daemon=True)
    thread.start()


def _run_safely(job_id: int) -> None:
    try:
        run_refresh_job(job_id)
    except Exception:  # pragma: no cover - defensive background-thread guard
        logger.exception("interview refresh job %s crashed", job_id)


def _job_session_id(job_id: int) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT session_id FROM interview_refresh_job WHERE id = ?", (job_id,)
        ).fetchone()
    return row["session_id"] if row is not None else None


def _next_pending_job_id(session_id: int) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM interview_refresh_job
               WHERE session_id = ? AND status = 'pending'
               ORDER BY id ASC LIMIT 1""",
            (session_id,),
        ).fetchone()
    return row["id"] if row is not None else None


def run_refresh_job(job_id: int) -> None:
    """Run one refresh job to completion, then drain a queued follow-up.

    Idempotent: a job that is not (or no longer) 'pending' when it is
    actually picked up is a no-op -- calling this twice on the same job_id
    only does work once. Draining a follow-up 'pending' job after this one
    finishes keeps the dedupe bound (at most one extra job per burst)
    without needing a second caller to notice and dispatch it.
    """
    current: Optional[int] = job_id
    while current is not None:
        session_id = _job_session_id(current)
        if session_id is None:
            return
        lock = _lock_for_session(session_id)
        with lock:
            _run_one_locked(current)
            current = _next_pending_job_id(session_id)


def _run_one_locked(job_id: int) -> None:
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM interview_refresh_job WHERE id = ?", (job_id,)
        ).fetchone()
        if job is None or job["status"] != "pending":
            # Already handled by an earlier call (idempotency), or the job
            # was removed. Never re-run a job that already reached a
            # terminal state.
            return

        session_id = job["session_id"]
        system_id = job["system_id"]
        now = time.time()

        # Staleness: a strictly newer job for this session has already
        # completed. Under the supported single-process deployment, jobs for
        # one session are always serialized by `_lock_for_session`; the only
        # way an older job reaches this point after a newer one already
        # finished is a manual/out-of-order retry. Its result would regress
        # the session, so it writes nothing (Principle 2: never silently
        # regress persisted state). This pre-work check alone is NOT a
        # multi-process safety mechanism; Issue #315's migration prerequisites
        # require post-inference validation immediately before writes.
        newer_done = conn.execute(
            """SELECT 1 FROM interview_refresh_job
               WHERE session_id = ? AND id > ? AND status = 'updated'
               LIMIT 1""",
            (session_id, job_id),
        ).fetchone()
        if newer_done is not None:
            conn.execute(
                """UPDATE interview_refresh_job
                   SET status = 'stale', started_at = ?, finished_at = ?, error = ?
                   WHERE id = ?""",
                (now, now, refresh_job_message("superseded"), job_id),
            )
            return

        conn.execute(
            "UPDATE interview_refresh_job SET status = 'updating', started_at = ? WHERE id = ?",
            (now, job_id),
        )

        session = conn.execute(
            "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
            (session_id, system_id),
        ).fetchone()
        if session is None:
            conn.execute(
                """UPDATE interview_refresh_job
                   SET status = 'failed', error = ?, finished_at = ?
                   WHERE id = ?""",
                ("Interview session no longer exists", time.time(), job_id),
            )
            return

        # Deferred import: routes.interview imports interview_refresh at
        # call time too (see answer_interview_qa), so a module-level import
        # here would be circular.
        from .routes.interview import _rebuild_understanding, _understanding_update_blocked

        trigger_kind = job["trigger_kind"]
        rebuild_understanding = trigger_kind in _UNDERSTANDING_REBUILD_TRIGGERS

        # The Q&A trigger shares the manual endpoint's "nothing new" gate.
        # Other trigger kinds must never return here: Alignment decisions
        # are separate human evidence and intentionally bypass the Q&A-only
        # watermark, while Intent/change-set updates still need the
        # Alignment build below even though they do not regenerate System
        # Understanding.
        if (
            trigger_kind == "qa_answer"
            and _understanding_update_blocked(conn, session, system_id)
        ):
            latest_rev = conn.execute(
                """SELECT id FROM understanding_revision
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (session_id, system_id),
            ).fetchone()
            conn.execute(
                """UPDATE interview_refresh_job
                   SET status = 'updated', result_revision_id = ?, finished_at = ?, error = ?
                   WHERE id = ?""",
                (
                    latest_rev["id"] if latest_rev else None,
                    time.time(),
                    refresh_job_message("skipped_no_new_answers"),
                    job_id,
                ),
            )
            return

        intelligence_run_id: Optional[int] = None
        result_revision_id: Optional[int] = None
        if rebuild_understanding:
            result = _rebuild_understanding(conn, session, system_id)
            if not result.ok:
                conn.execute(
                    """UPDATE interview_refresh_job
                       SET status = 'failed', error = ?, intelligence_run_id = ?, finished_at = ?
                       WHERE id = ?""",
                    (result.error, result.intelligence_run_id, time.time(), job_id),
                )
                return
            intelligence_run_id = result.intelligence_run_id
            result_revision_id = result.revision_id
        else:
            # Intent updates affect the desired state, not facts about the
            # current system. NL change sets have already persisted their
            # selected claim edits as a new understanding_revision. Reusing
            # that latest revision avoids needlessly regenerating (and for a
            # change set, potentially overwriting) explicit human edits.
            latest_rev = conn.execute(
                """SELECT id FROM understanding_revision
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (session_id, system_id),
            ).fetchone()
            result_revision_id = latest_rev["id"] if latest_rev else None

        # Issue #287's Alignment build is best-effort here: Understanding
        # already rebuilt successfully (the primary deliverable), so a
        # follow-up Alignment failure is recorded as a note rather than
        # regressing the whole job to 'failed' and discarding the new
        # understanding revision that was just durably written.
        from .routes.interview_alignment import run_alignment_build

        alignment_note: Optional[str] = None
        try:
            run_alignment_build(conn, session_id, system_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else repr(exc.detail)
            alignment_note = f"Alignment refresh failed: {detail}"

        conn.execute(
            """UPDATE interview_refresh_job
               SET status = 'updated', intelligence_run_id = ?, result_revision_id = ?,
                   finished_at = ?, error = ?
               WHERE id = ?""",
            (
                intelligence_run_id, result_revision_id,
                time.time(), alignment_note, job_id,
            ),
        )


class RefreshJobNotFound(Exception):
    pass


class RefreshJobConflict(Exception):
    pass


def retry_refresh_job(job_id: int, session_id: int, system_id: int) -> int:
    """Manually retry a failed job: enqueues a fresh job via the same dedupe
    path as an ordinary trigger (never mutates the failed row itself, so the
    failure stays in the audit trail).

    Retryable states are 'failed' and the partial failure 'updated' with a
    residual ``error`` note (understanding rebuilt but the follow-up
    Alignment build failed) -- both leave the user with something broken to
    re-run. A cleanly 'updated' job stays non-retryable.
    """
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM interview_refresh_job WHERE id = ? AND session_id = ? AND system_id = ?",
            (job_id, session_id, system_id),
        ).fetchone()
        if job is None:
            raise RefreshJobNotFound(f"Refresh job {job_id} not found")
        partial_failure = job["status"] == "updated" and job["error"]
        if job["status"] != "failed" and not partial_failure:
            raise RefreshJobConflict(
                f"Only a 'failed' or partially failed job can be retried "
                f"(current status: {job['status']!r})"
            )
        trigger_kind = job["trigger_kind"]

    return request_refresh(session_id, system_id, trigger_kind)
