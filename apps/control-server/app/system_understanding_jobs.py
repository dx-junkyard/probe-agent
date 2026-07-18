"""System Understanding build job orchestration (Issue #109).

Replaces the single opaque background build with an explicit job/step model:

- one job (``system_understanding_builds`` row) per Build/Refresh request
- one step row per pipeline stage, with a deterministic dependency graph
- per-step status / started_at / completed_at / error / artifact provenance
- chunk-level LLM tasks for ``claim_scan`` with unified retry/backoff/cancel
- heartbeat columns so stuck jobs are detectable after a crash or restart
- step-level retry / cancel and whole-job resume from persisted DB state

Completed steps are never re-executed; retry targets only missing/failed
steps. LLM failures fail the ``claim_scan`` step visibly (Principle 6: no
heuristic fallback) while deterministic steps still complete.

probe-agent:
  role: Step-level build orchestrator for System Understanding
  capability: repository-understanding
  element_type: core
  consumers: [dashboard, control-server]
  operation_kind: orchestration
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify step statuses, dependency blocking, retry/cancel/resume,
    and heartbeat/stuck detection stay consistent with persisted artifacts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .db import get_conn

logger = logging.getLogger(__name__)


# Ordered job steps and their dependency graph (Issue #109).
STEP_ORDER: List[str] = [
    "symbol_index",
    "entrypoint_index",
    "documentation_index",
    "claim_scan",
    "understanding_graph",
    "docs_code_reconcile",
    "capability_hierarchy",
]

STEP_DEPENDS: Dict[str, List[str]] = {
    "symbol_index": [],
    "entrypoint_index": ["symbol_index"],
    "documentation_index": [],
    "claim_scan": ["documentation_index"],
    "understanding_graph": ["claim_scan"],
    "docs_code_reconcile": ["understanding_graph", "symbol_index"],
    "capability_hierarchy": ["symbol_index", "entrypoint_index"],
}

# Steps that require a reasoning model; they are marked blocked (never
# heuristically approximated) when no reasoning model is configured.
REASONING_STEPS = {"claim_scan"}

ACTIVE_JOB_STATUSES = ("queued", "running")
TERMINAL_JOB_STATUSES = ("completed", "partial", "failed", "cancelled")


def _stuck_after_seconds() -> float:
    try:
        return float(os.getenv("SYSTEM_UNDERSTANDING_STUCK_AFTER_SECONDS", "300"))
    except ValueError:
        return 300.0


def _llm_max_attempts() -> int:
    try:
        return max(1, int(os.getenv("SYSTEM_UNDERSTANDING_LLM_MAX_ATTEMPTS", "3")))
    except ValueError:
        return 3


def _llm_backoff_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("SYSTEM_UNDERSTANDING_LLM_BACKOFF_SECONDS", "2")))
    except ValueError:
        return 2.0


class JobNotFound(Exception):
    pass


class JobConflict(Exception):
    pass


class StepFailed(Exception):
    def __init__(self, message: str, provenance: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.provenance = provenance or {}


class StepCancelled(Exception):
    """The current step was cancelled; the job continues with later steps."""


class BuildCancelled(Exception):
    """The whole job was cancelled."""


@dataclass
class StepContext:
    build_id: int
    step_id: int
    step: str
    system_id: int
    snapshot_id: int

    def heartbeat(self) -> None:
        now = time.time()
        with get_conn() as conn:
            conn.execute(
                "UPDATE system_understanding_builds SET heartbeat_at = ? WHERE id = ?",
                (now, self.build_id),
            )
            conn.execute(
                "UPDATE system_understanding_build_steps SET heartbeat_at = ? WHERE id = ?",
                (now, self.step_id),
            )

    def check_cancelled(self) -> None:
        with get_conn() as conn:
            job = conn.execute(
                "SELECT cancel_requested FROM system_understanding_builds WHERE id = ?",
                (self.build_id,),
            ).fetchone()
            step = conn.execute(
                "SELECT cancel_requested FROM system_understanding_build_steps WHERE id = ?",
                (self.step_id,),
            ).fetchone()
        if job and job["cancel_requested"]:
            raise BuildCancelled()
        if step and step["cancel_requested"]:
            raise StepCancelled()


def _is_stale(row) -> bool:
    """A queued/running job with no recent heartbeat has lost its worker."""
    if row["status"] not in ACTIVE_JOB_STATUSES:
        return False
    last = row["heartbeat_at"] or row["started_at"] or row["created_at"]
    if last is None:
        return True
    return (time.time() - last) > _stuck_after_seconds()


def _reasoning_available() -> bool:
    # Resolved through the service module so tests (and the existing #106
    # regression test) can monkeypatch a single attribute.
    from . import system_understanding_service as service

    return service._is_reasoning_model_available()


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------


def _open_run(conn, build_id: int, system_id: int, trigger: str) -> int:
    """Create the run row for one worker execution of a build job."""
    now = time.time()
    return conn.execute(
        """INSERT INTO system_understanding_build_runs
            (build_id, system_id, trigger, status, started_at, created_at)
        VALUES (?, ?, ?, 'running', ?, ?)""",
        (build_id, system_id, trigger, now, now),
    ).lastrowid


def _close_open_runs(conn, build_id: int, status: str) -> None:
    conn.execute(
        """UPDATE system_understanding_build_runs
           SET status = ?, completed_at = COALESCE(completed_at, ?)
           WHERE build_id = ? AND status = 'running'""",
        (status, time.time(), build_id),
    )


def start_system_understanding_build(system_id: int) -> int:
    """Create (or reuse) a build job and run it on a background thread.

    Returns the job id immediately. If an active, healthy job already exists
    for this system, its id is returned instead of enqueueing a duplicate.
    A stale active job (lost worker) is marked failed and superseded.
    """
    from .system_understanding_service import _get_latest_ready_snapshot

    now = time.time()
    with get_conn() as conn:
        active = conn.execute(
            "SELECT * FROM system_understanding_builds "
            "WHERE system_id = ? AND status IN ('queued', 'running') "
            "ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
        if active is not None:
            if not _is_stale(active):
                return active["id"]
            conn.execute(
                """UPDATE system_understanding_builds
                   SET status = 'failed',
                       error = 'Worker heartbeat lost (stuck); superseded by a new build',
                       completed_at = ?
                   WHERE id = ?""",
                (now, active["id"]),
            )
            conn.execute(
                """UPDATE system_understanding_build_steps
                   SET status = 'failed', error = 'Worker heartbeat lost (stuck)',
                       completed_at = ?
                   WHERE build_id = ? AND status = 'running'""",
                (now, active["id"]),
            )
            _close_open_runs(conn, active["id"], "failed")

        snapshot_row = _get_latest_ready_snapshot(conn, system_id)
        snapshot_id = snapshot_row["id"] if snapshot_row else None
        build_id = conn.execute(
            """INSERT INTO system_understanding_builds
                (system_id, snapshot_id, status, heartbeat_at, created_at)
            VALUES (?, ?, 'queued', ?, ?)""",
            (system_id, snapshot_id, now, now),
        ).lastrowid
        for step in STEP_ORDER:
            conn.execute(
                """INSERT INTO system_understanding_build_steps
                    (build_id, system_id, snapshot_id, step, depends_on, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (build_id, system_id, snapshot_id, step, json.dumps(STEP_DEPENDS[step]), now),
            )
        _open_run(conn, build_id, system_id, "build")

    _spawn(build_id, system_id)
    return build_id


def _spawn(build_id: int, system_id: int) -> None:
    thread = threading.Thread(
        target=_run_job_safely, args=(build_id, system_id), daemon=True
    )
    thread.start()


def _run_job_safely(build_id: int, system_id: int) -> None:
    from .resource_limits import reset_current_system_id, set_current_system_id

    quota_context = set_current_system_id(system_id)
    try:
        _execute_job(build_id, system_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("system_understanding job %s crashed", build_id)
        with get_conn() as conn:
            conn.execute(
                """UPDATE system_understanding_builds
                   SET status = 'failed', error = ?, current_step = NULL, completed_at = ?
                   WHERE id = ?""",
                (str(exc), time.time(), build_id),
            )
            _close_open_runs(conn, build_id, "failed")
    finally:
        reset_current_system_id(quota_context)


def _mark_step(
    step_id: int,
    status: str,
    *,
    error: Optional[str] = None,
    provenance: Optional[Dict[str, Any]] = None,
    reused: bool = False,
    started: bool = False,
    completed: bool = False,
) -> None:
    now = time.time()
    sets = ["status = ?", "heartbeat_at = ?"]
    args: List[Any] = [status, now]
    sets.append("error = ?")
    args.append(error)
    if provenance is not None:
        sets.append("artifact_provenance = ?")
        args.append(json.dumps(provenance))
    if reused:
        sets.append("reused_existing = 1")
    if started:
        sets.append("started_at = ?")
        args.append(now)
    if completed:
        sets.append("completed_at = ?")
        args.append(now)
    args.append(step_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE system_understanding_build_steps SET {', '.join(sets)} WHERE id = ?",
            args,
        )


def _fail_job_without_snapshot(build_id: int, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE system_understanding_build_steps
               SET status = 'blocked', error = 'No ready snapshot'
               WHERE build_id = ? AND status = 'pending'""",
            (build_id,),
        )
        conn.execute(
            """UPDATE system_understanding_builds
               SET status = 'failed', error = ?, current_step = NULL, completed_at = ?
               WHERE id = ?""",
            (error, time.time(), build_id),
        )
        _close_open_runs(conn, build_id, "failed")


def _execute_job(build_id: int, system_id: int) -> None:
    from .system_understanding_service import _get_latest_ready_snapshot

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """UPDATE system_understanding_builds
               SET status = 'running', started_at = COALESCE(started_at, ?),
                   heartbeat_at = ?, error = NULL, completed_at = NULL
               WHERE id = ?""",
            (now, now, build_id),
        )
        job = conn.execute(
            "SELECT snapshot_id FROM system_understanding_builds WHERE id = ?",
            (build_id,),
        ).fetchone()
        # The snapshot the job was created with is authoritative: a retry or
        # resume must run against the same pinned snapshot even if a newer
        # ready snapshot has appeared since (Issue #109: jobs bind to a
        # snapshot_id). Only jobs created without any ready snapshot fall
        # back to the latest one available now.
        snapshot_id = job["snapshot_id"]
        if snapshot_id is not None:
            snapshot_row = conn.execute(
                "SELECT * FROM repository_snapshots "
                "WHERE id = ? AND system_id = ? AND status = 'ready'",
                (snapshot_id, system_id),
            ).fetchone()
        else:
            snapshot_row = _get_latest_ready_snapshot(conn, system_id)
            if snapshot_row is not None:
                conn.execute(
                    "UPDATE system_understanding_builds SET snapshot_id = ? WHERE id = ?",
                    (snapshot_row["id"], build_id),
                )
        if snapshot_row is not None:
            conn.execute(
                "UPDATE system_understanding_build_steps SET snapshot_id = COALESCE(snapshot_id, ?) WHERE build_id = ?",
                (snapshot_row["id"], build_id),
            )

    if snapshot_row is None:
        if snapshot_id is not None:
            _fail_job_without_snapshot(
                build_id,
                f"Snapshot {snapshot_id} is no longer available; start a new build",
            )
        else:
            _fail_job_without_snapshot(
                build_id,
                "No ready repository snapshot; create a snapshot first",
            )
        return

    snapshot_id = snapshot_row["id"]
    logger.info(
        "system_understanding job %s starting for system_id=%s snapshot_id=%s",
        build_id, system_id, snapshot_id,
    )

    cancelled = False
    for step in STEP_ORDER:
        with get_conn() as conn:
            job = conn.execute(
                "SELECT * FROM system_understanding_builds WHERE id = ?", (build_id,)
            ).fetchone()
            step_row = conn.execute(
                "SELECT * FROM system_understanding_build_steps WHERE build_id = ? AND step = ?",
                (build_id, step),
            ).fetchone()
            dep_rows = {
                r["step"]: r["status"]
                for r in conn.execute(
                    "SELECT step, status FROM system_understanding_build_steps WHERE build_id = ?",
                    (build_id,),
                ).fetchall()
            }
        if job["cancel_requested"]:
            cancelled = True
            break
        if step_row["status"] in ("completed", "cancelled"):
            continue
        if step_row["cancel_requested"]:
            _mark_step(step_row["id"], "cancelled", error="Cancelled before start", completed=True)
            continue

        unmet = [
            dep for dep in STEP_DEPENDS[step] if dep_rows.get(dep) != "completed"
        ]
        if unmet:
            details = ", ".join(f"{d} is {dep_rows.get(d, 'missing')}" for d in unmet)
            _mark_step(step_row["id"], "blocked", error=f"Dependency not satisfied: {details}")
            continue

        if step in REASONING_STEPS and not _reasoning_available():
            _mark_step(step_row["id"], "blocked", error="Reasoning model not configured")
            continue

        ctx = StepContext(
            build_id=build_id,
            step_id=step_row["id"],
            step=step,
            system_id=system_id,
            snapshot_id=snapshot_id,
        )

        existing = _EXISTING_CHECKS[step](system_id, snapshot_id)
        if existing is not None:
            _mark_step(
                step_row["id"], "completed",
                provenance=existing, reused=True, completed=True,
            )
            continue

        with get_conn() as conn:
            conn.execute(
                "UPDATE system_understanding_builds SET current_step = ?, heartbeat_at = ? WHERE id = ?",
                (step, time.time(), build_id),
            )
        _mark_step(step_row["id"], "running", started=True)
        started = time.time()
        try:
            provenance = _STEP_RUNNERS[step](ctx)
        except BuildCancelled:
            _mark_step(step_row["id"], "cancelled", error="Cancelled", completed=True)
            cancelled = True
            break
        except StepCancelled:
            _mark_step(step_row["id"], "cancelled", error="Cancelled", completed=True)
            continue
        except StepFailed as exc:
            logger.warning(
                "system_understanding job %s step=%s failed: %s", build_id, step, exc
            )
            _mark_step(
                step_row["id"], "failed",
                error=str(exc), provenance=exc.provenance or None, completed=True,
            )
            continue
        except Exception as exc:
            logger.warning(
                "system_understanding job %s step=%s raised unexpectedly",
                build_id, step, exc_info=True,
            )
            _mark_step(step_row["id"], "failed", error=str(exc), completed=True)
            continue
        logger.info(
            "system_understanding job %s step=%s completed in %.2fs",
            build_id, step, time.time() - started,
        )
        _mark_step(step_row["id"], "completed", provenance=provenance, completed=True)

    _finalize_job(build_id, cancelled)


def _record_gap_history(
    conn, system_id: int, snapshot_id: Optional[int], build_id: int, now: float
) -> None:
    """Persist per-gap_type counts for a settled build (Issue #203).

    Reuses the exact same gap computation the read path uses
    (`_load_gaps_from_reconciler` + `_compute_gap_summary`) so history never
    diverges from what the Hub displays for the same build/snapshot. Only
    called for completed/partial jobs. A build with no open gaps stores one
    reserved marker row so it remains the current side of an N -> 0 trend;
    the read path filters that marker from user-visible gap types.
    """
    if snapshot_id is None:
        return
    from .system_understanding_service import (
        GAP_HISTORY_EMPTY_SENTINEL,
        _compute_gap_summary,
        _load_gaps_from_reconciler,
        _open_gaps,
    )

    gaps = _load_gaps_from_reconciler(conn, system_id, snapshot_id)
    from .gap_triage import materialize_automatic_reopens

    # Build settlement is the explicit write point for automatic reopen. GET
    # projections remain read-only, while the open event becomes durable and
    # auditable before this build's open-only trend counts are stored.
    materialize_automatic_reopens(conn, system_id, snapshot_id, gaps)
    # Issue #276 policy: trend measures untriaged work, not every detected
    # item. Acknowledged/dismissed/resolved gaps stay visible in the all-items
    # worklist but do not make the trend look perpetually noisy.
    gap_summary = _compute_gap_summary(_open_gaps(gaps))
    if not gap_summary:
        conn.execute(
            """INSERT INTO system_understanding_gap_history
                (system_id, snapshot_id, build_id, gap_type, count, created_at)
            VALUES (?, ?, ?, ?, 0, ?)""",
            (system_id, snapshot_id, build_id, GAP_HISTORY_EMPTY_SENTINEL, now),
        )
    for gs in gap_summary:
        conn.execute(
            """INSERT INTO system_understanding_gap_history
                (system_id, snapshot_id, build_id, gap_type, count, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (system_id, snapshot_id, build_id, gs.gap_type, gs.count, now),
        )


def _finalize_job(build_id: int, cancelled: bool) -> None:
    now = time.time()
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM system_understanding_builds WHERE id = ?", (build_id,)
        ).fetchone()
        system_id = job["system_id"]
        snapshot_id = job["snapshot_id"]
        cancelled = cancelled or bool(job["cancel_requested"])
        if cancelled:
            conn.execute(
                """UPDATE system_understanding_build_steps
                   SET status = 'cancelled', error = COALESCE(error, 'Cancelled'),
                       completed_at = COALESCE(completed_at, ?)
                   WHERE build_id = ? AND status IN ('pending', 'running')""",
                (now, build_id),
            )
            conn.execute(
                """UPDATE system_understanding_llm_tasks
                   SET status = 'cancelled', completed_at = COALESCE(completed_at, ?)
                   WHERE build_id = ? AND status IN ('pending', 'running')""",
                (now, build_id),
            )
        steps = conn.execute(
            "SELECT step, status, error FROM system_understanding_build_steps WHERE build_id = ?",
            (build_id,),
        ).fetchall()

    # Any step left failed/blocked/cancelled means the job did NOT fully
    # complete: report `partial` (or `failed` when nothing completed) so the
    # blocked/failed steps stay visible and retryable in the UI instead of
    # hiding behind a `completed` job (Issue #109 review).
    completed_n = sum(1 for s in steps if s["status"] == "completed")
    incomplete = [s for s in steps if s["status"] != "completed"]
    failed = [s for s in steps if s["status"] == "failed"]
    if cancelled:
        status = "cancelled"
        error = None
    elif not incomplete:
        status = "completed"
        error = None
    else:
        status = "partial" if completed_n else "failed"
        first = failed[0] if failed else incomplete[0]
        error = f"{first['step']}: {first['error']}"

    with get_conn() as conn:
        conn.execute(
            """UPDATE system_understanding_builds
               SET status = ?, error = ?, current_step = NULL,
                   heartbeat_at = ?, completed_at = ?
               WHERE id = ?""",
            (status, error, now, now, build_id),
        )
        _close_open_runs(conn, build_id, status)
        if status in ("completed", "partial"):
            _record_gap_history(conn, system_id, snapshot_id, build_id, now)
    logger.info("system_understanding job %s finished with status=%s", build_id, status)


# ---------------------------------------------------------------------------
# Retry / cancel / resume
# ---------------------------------------------------------------------------


def _dependents_closure(step: str) -> List[str]:
    """All steps that transitively depend on `step` (excluding itself)."""
    result: List[str] = []
    for candidate in STEP_ORDER:
        deps = set(STEP_DEPENDS[candidate])
        if step in deps or any(d in result for d in deps):
            result.append(candidate)
    return result


def retry_build(system_id: int, build_id: int, step: Optional[str] = None) -> None:
    """Resume a settled (or stuck) job, re-running only non-completed steps.

    With `step`, resets just that step plus its non-completed dependents.
    Completed steps are never reset or re-executed (Issue #109).
    """
    now = time.time()
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM system_understanding_builds WHERE id = ? AND system_id = ?",
            (build_id, system_id),
        ).fetchone()
        if job is None:
            raise JobNotFound(f"Build {build_id} not found")
        if job["status"] in ACTIVE_JOB_STATUSES and not _is_stale(job):
            raise JobConflict("Build is still running; wait or cancel it first")

        if step is not None:
            if step not in STEP_ORDER:
                raise JobNotFound(f"Unknown step '{step}'")
            step_row = conn.execute(
                "SELECT * FROM system_understanding_build_steps WHERE build_id = ? AND step = ?",
                (build_id, step),
            ).fetchone()
            if step_row is None:
                raise JobNotFound(f"Step '{step}' not found for build {build_id}")
            if step_row["status"] == "completed":
                raise JobConflict(
                    f"Step '{step}' is already completed; completed steps are not re-executed"
                )
            targets = [step] + _dependents_closure(step)
        else:
            targets = list(STEP_ORDER)

        placeholders = ",".join("?" for _ in targets)
        conn.execute(
            f"""UPDATE system_understanding_build_steps
               SET status = 'pending', error = NULL, cancel_requested = 0,
                   reused_existing = 0, artifact_provenance = '{{}}',
                   started_at = NULL, completed_at = NULL, heartbeat_at = NULL
               WHERE build_id = ? AND step IN ({placeholders})
               AND status != 'completed'""",
            [build_id, *targets],
        )
        # Failed/cancelled chunk tasks become retryable; completed chunk
        # results are kept so only missing chunks are re-scanned.
        conn.execute(
            """UPDATE system_understanding_llm_tasks
               SET status = 'pending', attempts = 0, last_error = NULL,
                   started_at = NULL, completed_at = NULL
               WHERE build_id = ? AND status IN ('failed', 'cancelled', 'running')""",
            (build_id,),
        )
        conn.execute(
            """UPDATE system_understanding_builds
               SET status = 'queued', cancel_requested = 0, error = NULL,
                   completed_at = NULL, heartbeat_at = ?
               WHERE id = ?""",
            (now, build_id),
        )
        # A retried stuck job may still have its previous run open.
        _close_open_runs(conn, build_id, "failed")
        _open_run(conn, build_id, system_id, "step_retry" if step else "retry")

    _spawn(build_id, system_id)


def cancel_build(system_id: int, build_id: int) -> None:
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM system_understanding_builds WHERE id = ? AND system_id = ?",
            (build_id, system_id),
        ).fetchone()
        if job is None:
            raise JobNotFound(f"Build {build_id} not found")
        if job["status"] not in ACTIVE_JOB_STATUSES:
            raise JobConflict(f"Build already settled with status '{job['status']}'")
        conn.execute(
            "UPDATE system_understanding_builds SET cancel_requested = 1 WHERE id = ?",
            (build_id,),
        )
        stale = _is_stale(job)
    if stale:
        # No live worker will observe the flag; finalize immediately.
        _finalize_job(build_id, cancelled=True)


def cancel_step(system_id: int, build_id: int, step: str) -> None:
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM system_understanding_builds WHERE id = ? AND system_id = ?",
            (build_id, system_id),
        ).fetchone()
        if job is None:
            raise JobNotFound(f"Build {build_id} not found")
        step_row = conn.execute(
            "SELECT * FROM system_understanding_build_steps WHERE build_id = ? AND step = ?",
            (build_id, step),
        ).fetchone()
        if step_row is None:
            raise JobNotFound(f"Step '{step}' not found for build {build_id}")
        if step_row["status"] not in ("pending", "running"):
            raise JobConflict(f"Step already settled with status '{step_row['status']}'")
        conn.execute(
            "UPDATE system_understanding_build_steps SET cancel_requested = 1 WHERE id = ?",
            (step_row["id"],),
        )
        job_active = job["status"] in ACTIVE_JOB_STATUSES and not _is_stale(job)
        if not job_active:
            conn.execute(
                """UPDATE system_understanding_build_steps
                   SET status = 'cancelled', error = 'Cancelled', completed_at = ?
                   WHERE id = ?""",
                (time.time(), step_row["id"]),
            )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def _artifact_counts(conn, system_id: int, snapshot_id: Optional[int]) -> Dict[str, int]:
    if snapshot_id is None:
        return {"symbols": 0, "entrypoints": 0, "understanding_graph_claims": 0,
                "capability_hierarchy_nodes": 0}
    symbols = conn.execute(
        "SELECT COUNT(*) FROM code_symbols WHERE system_id = ? AND snapshot_id = ?",
        (system_id, snapshot_id),
    ).fetchone()[0]
    entrypoints = conn.execute(
        "SELECT COUNT(*) FROM code_entrypoints WHERE system_id = ? AND snapshot_id = ?",
        (system_id, snapshot_id),
    ).fetchone()[0]
    graph = conn.execute(
        "SELECT claim_count FROM understanding_graph_snapshots "
        "WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    hierarchy = conn.execute(
        "SELECT COUNT(*) FROM capability_hierarchy_nodes WHERE system_id = ? AND snapshot_id = ?",
        (system_id, snapshot_id),
    ).fetchone()[0]
    return {
        "symbols": symbols,
        "entrypoints": entrypoints,
        "understanding_graph_claims": graph["claim_count"] if graph else 0,
        "capability_hierarchy_nodes": hierarchy,
    }


def _job_to_dict(conn, job) -> Dict[str, Any]:
    steps = conn.execute(
        "SELECT * FROM system_understanding_build_steps WHERE build_id = ? ORDER BY id",
        (job["id"],),
    ).fetchall()
    task_rows = conn.execute(
        """SELECT status, COUNT(*) AS n, SUM(reused_existing) AS reused
           FROM system_understanding_llm_tasks WHERE build_id = ? GROUP BY status""",
        (job["id"],),
    ).fetchall()
    task_summary = {
        "total": 0, "pending": 0, "running": 0,
        "completed": 0, "failed": 0, "cancelled": 0, "reused": 0,
    }
    for tr in task_rows:
        task_summary["total"] += tr["n"]
        if tr["status"] in task_summary:
            task_summary[tr["status"]] += tr["n"]
        task_summary["reused"] += tr["reused"] or 0

    step_dicts = []
    for s in steps:
        duration_ms = None
        if s["started_at"] is not None and s["completed_at"] is not None:
            duration_ms = (s["completed_at"] - s["started_at"]) * 1000.0
        try:
            provenance = json.loads(s["artifact_provenance"] or "{}")
        except (json.JSONDecodeError, TypeError):
            provenance = {}
        try:
            depends_on = json.loads(s["depends_on"] or "[]")
        except (json.JSONDecodeError, TypeError):
            depends_on = []
        step_dicts.append({
            "id": s["id"],
            "step": s["step"],
            "status": s["status"],
            "depends_on": depends_on,
            "reused_existing": bool(s["reused_existing"]),
            "cancel_requested": bool(s["cancel_requested"]),
            "error": s["error"],
            "artifact_provenance": provenance,
            "duration_ms": duration_ms,
            "heartbeat_at": s["heartbeat_at"],
            "started_at": s["started_at"],
            "completed_at": s["completed_at"],
        })

    latest_run = conn.execute(
        "SELECT id FROM system_understanding_build_runs "
        "WHERE build_id = ? ORDER BY id DESC LIMIT 1",
        (job["id"],),
    ).fetchone()

    return {
        "id": job["id"],
        "job_id": job["id"],
        "run_id": latest_run["id"] if latest_run else None,
        "system_id": job["system_id"],
        "snapshot_id": job["snapshot_id"],
        "status": job["status"],
        "current_step": job["current_step"],
        "error": job["error"],
        "cancel_requested": bool(job["cancel_requested"]),
        "is_stuck": _is_stale(job),
        "heartbeat_at": job["heartbeat_at"],
        "started_at": job["started_at"],
        "completed_at": job["completed_at"],
        "created_at": job["created_at"],
        "steps": step_dicts,
        "llm_tasks": task_summary,
        "artifact_counts": _artifact_counts(conn, job["system_id"], job["snapshot_id"]),
    }


def get_job(system_id: int, build_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM system_understanding_builds WHERE id = ? AND system_id = ?",
            (build_id, system_id),
        ).fetchone()
        if job is None:
            return None
        return _job_to_dict(conn, job)


def get_latest_job(system_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        job = conn.execute(
            "SELECT * FROM system_understanding_builds WHERE system_id = ? ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
        if job is None:
            return None
        return _job_to_dict(conn, job)


def list_active_jobs(system_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        jobs = conn.execute(
            "SELECT * FROM system_understanding_builds "
            "WHERE system_id = ? AND status IN ('queued', 'running') ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        return [_job_to_dict(conn, j) for j in jobs]


# ---------------------------------------------------------------------------
# Existing-artifact checks (deterministic; a hit means the step is reused)
# ---------------------------------------------------------------------------


def _existing_symbol_index(system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        run = conn.execute(
            "SELECT id FROM intelligence_runs WHERE system_id = ? AND run_type = 'symbol_index' "
            "AND snapshot_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()
        if run is None:
            return None
        count = conn.execute(
            "SELECT COUNT(*) FROM code_symbols WHERE system_id = ? AND snapshot_id = ?",
            (system_id, snapshot_id),
        ).fetchone()[0]
    return {"intelligence_run_id": run["id"], "symbol_count": count}


def _existing_entrypoint_index(system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        run = conn.execute(
            "SELECT id FROM intelligence_runs WHERE system_id = ? AND run_type = 'entrypoint_index' "
            "AND snapshot_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()
        if run is None:
            return None
        count = conn.execute(
            "SELECT COUNT(*) FROM code_entrypoints WHERE system_id = ? AND snapshot_id = ?",
            (system_id, snapshot_id),
        ).fetchone()[0]
    return {"intelligence_run_id": run["id"], "entrypoint_count": count}


def _existing_documentation_index(system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    # Deterministic chunking is cheap and not persisted as its own artifact;
    # always run it within a job (completed step rows are still never re-run).
    return None


def _existing_graph(system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, claim_count FROM understanding_graph_snapshots "
            "WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()
    if row is None:
        return None
    return {"understanding_graph_snapshot_id": row["id"], "claim_count": row["claim_count"]}


def _existing_docs_code_reconcile(system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    # Reconciliation is a pure computation over the graph and code index; it
    # has no persisted artifact of its own, so it always runs when reachable.
    return None


def _existing_capability_hierarchy(system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        run = conn.execute(
            "SELECT id FROM intelligence_runs WHERE system_id = ? AND run_type = 'capability_hierarchy' "
            "AND snapshot_id = ? AND status = 'completed' ORDER BY id DESC LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()
        if run is None:
            return None
        count = conn.execute(
            "SELECT COUNT(*) FROM capability_hierarchy_nodes WHERE system_id = ? AND snapshot_id = ?",
            (system_id, snapshot_id),
        ).fetchone()[0]
    return {"intelligence_run_id": run["id"], "hierarchy_node_count": count}


_EXISTING_CHECKS = {
    "symbol_index": _existing_symbol_index,
    "entrypoint_index": _existing_entrypoint_index,
    "documentation_index": _existing_documentation_index,
    "claim_scan": _existing_graph,
    "understanding_graph": _existing_graph,
    "docs_code_reconcile": _existing_docs_code_reconcile,
    "capability_hierarchy": _existing_capability_hierarchy,
}


# ---------------------------------------------------------------------------
# Step runners. Each opens short-lived connections only; CPU- or
# network-bound work never holds the shared database lock (Issue #106).
# ---------------------------------------------------------------------------


def _run_symbol_index(ctx: StepContext) -> Dict[str, Any]:
    from .code_indexer import index_snapshot_files

    with get_conn() as conn:
        file_rows = conn.execute(
            "SELECT path, content FROM snapshot_files "
            "WHERE snapshot_id = ? AND inclusion_status = 'indexed' ORDER BY path",
            (ctx.snapshot_id,),
        ).fetchall()

    started_at = time.time()
    files = [(fr["path"], bytes(fr["content"] or b"")) for fr in file_rows]
    result = index_snapshot_files(files)
    ctx.heartbeat()

    with get_conn() as conn:
        now = time.time()
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method,
                 status, is_mock, started_at, completed_at)
            VALUES (?, ?, 'symbol_index', 'deterministic', 'n/a',
                    'n/a', 'provenance-v1', 'deterministic',
                    'completed', 0, ?, ?)""",
            (ctx.system_id, ctx.snapshot_id, started_at, now),
        ).lastrowid
        for sym in result.symbols:
            conn.execute(
                """INSERT OR IGNORE INTO code_symbols
                    (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line,
                     docstring, decorators, imports, is_test, route_path, route_method,
                     component_id, symbol_source_hash, symbol_body_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ctx.snapshot_id, ctx.system_id, sym.path, sym.qualified_name,
                    sym.kind, sym.start_line, sym.end_line,
                    sym.docstring, json.dumps(sym.decorators), json.dumps(sym.imports),
                    1 if sym.is_test else 0, sym.route_path, sym.route_method,
                    sym.component_id,
                    sym.symbol_source_hash, sym.symbol_body_hash,
                ),
            )
    return {"intelligence_run_id": run_id, "symbol_count": len(result.symbols)}


def _run_entrypoint_index(ctx: StepContext) -> Dict[str, Any]:
    from .entrypoint_discovery import discover_entrypoints
    from .flow_graph import SymbolRecord

    with get_conn() as conn:
        symbols = conn.execute(
            "SELECT * FROM code_symbols WHERE system_id = ? AND snapshot_id = ?",
            (ctx.system_id, ctx.snapshot_id),
        ).fetchall()
        file_rows = conn.execute(
            "SELECT path, content FROM snapshot_files "
            "WHERE snapshot_id = ? AND inclusion_status = 'indexed' ORDER BY path",
            (ctx.snapshot_id,),
        ).fetchall()

    started_at = time.time()
    ep_files = [
        (fr["path"], (bytes(fr["content"] or b"")).decode("utf-8", errors="replace"))
        for fr in file_rows
    ]
    sym_records = [
        SymbolRecord(
            symbol_id=s["id"],
            path=s["path"],
            qualified_name=s["qualified_name"],
            kind=s["kind"],
            start_line=s["start_line"],
            end_line=s["end_line"],
            decorators=json.loads(s["decorators"]) if isinstance(s["decorators"], str) else (s["decorators"] or []),
            component_id=s["component_id"],
            route_path=s["route_path"],
            route_method=s["route_method"],
            docstring=s["docstring"],
            is_test=bool(s["is_test"]),
        )
        for s in symbols
    ]
    discovery = discover_entrypoints(sym_records, ep_files)
    ctx.heartbeat()

    entrypoint_count = len(discovery.entrypoints) + len(discovery.functions)
    with get_conn() as conn:
        now = time.time()
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method,
                 status, is_mock, started_at, completed_at)
            VALUES (?, ?, 'entrypoint_index', 'deterministic', 'n/a',
                    'n/a', 'provenance-v1', 'deterministic',
                    'completed', 0, ?, ?)""",
            (ctx.system_id, ctx.snapshot_id, started_at, now),
        ).lastrowid
        for ep in discovery.entrypoints + discovery.functions:
            sym_row = next(
                (s for s in symbols if s["qualified_name"] == ep.qualified_name),
                None,
            )
            handler_symbol_id = sym_row["id"] if sym_row else None
            conn.execute(
                """INSERT OR IGNORE INTO code_entrypoints
                    (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                     handler_symbol_id, handler_qualified_name, handler_path,
                     route_method, route_path, framework, operation, confidence,
                     line_start, line_end, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deterministic', ?)""",
                (
                    ctx.system_id, ctx.snapshot_id, ep.entrypoint_type, ep.entrypoint_id,
                    ep.category, ep.label,
                    handler_symbol_id, ep.qualified_name, ep.path,
                    ep.route_method, ep.route_path,
                    ep.framework, ep.operation, ep.confidence,
                    ep.line_start, ep.line_end,
                    now,
                ),
            )
    return {"intelligence_run_id": run_id, "entrypoint_count": entrypoint_count}


def _run_documentation_index(ctx: StepContext) -> Dict[str, Any]:
    from .documentation_indexer import build_documentation_index

    with get_conn() as conn:
        doc_index = build_documentation_index(conn, ctx.system_id, ctx.snapshot_id)
    return {
        "doc_file_count": len([f for f in doc_index.files if f.included]),
        "chunk_count": len(doc_index.chunks),
    }


def _sleep_backoff(ctx: StepContext, attempt: int) -> None:
    delay = _llm_backoff_seconds() * (2 ** (attempt - 1))
    deadline = time.time() + delay
    while time.time() < deadline:
        ctx.check_cancelled()
        time.sleep(min(0.1, max(0.0, deadline - time.time())))


def _run_claim_scan(ctx: StepContext) -> Dict[str, Any]:
    """Scan documentation chunks as individually retryable LLM tasks.

    Timeout comes from the LLM config (LLM_TIMEOUT / INTELLIGENCE_LLM_TIMEOUT);
    retry/backoff and cancellation are handled here per chunk. Completed chunk
    results persist so retries and later builds re-scan only what is missing.
    """
    from . import documentation_claim_scanner as scanner
    from .documentation_indexer import build_documentation_index
    from .llm import LLMConfig, create_llm_client

    with get_conn() as conn:
        doc_index = build_documentation_index(conn, ctx.system_id, ctx.snapshot_id)
    chunks = doc_index.chunks
    now = time.time()
    max_attempts = _llm_max_attempts()

    chunk_by_id = {c.chunk_id: c for c in chunks}
    with get_conn() as conn:
        for chunk in chunks:
            conn.execute(
                """INSERT OR IGNORE INTO system_understanding_llm_tasks
                    (build_id, step_id, system_id, snapshot_id, task_type, chunk_id,
                     chunk_content_hash, chunk_path, chunk_start_line,
                     prompt_version, schema_version, max_attempts, created_at)
                VALUES (?, ?, ?, ?, 'claim_scan_chunk', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ctx.build_id, ctx.step_id, ctx.system_id, ctx.snapshot_id,
                    chunk.chunk_id, chunk.content_hash, chunk.path, chunk.start_line,
                    scanner.PROMPT_VERSION, scanner.SCHEMA_VERSION,
                    max_attempts, now,
                ),
            )
        # Reuse completed results from any earlier build of this system when
        # chunk content, path, and prompt/schema versions are identical, even
        # if the snapshot changed (Issue #195). snapshot_id is deliberately
        # excluded from the match: a Refresh that leaves a documentation
        # section untouched still hashes to the same chunk_content_hash under
        # a new snapshot_id, and re-scanning it would be redundant LLM work.
        # chunk_id is not part of the match either (Issue #195 design note):
        # it is derived from path/start_line/heading and can shift when
        # unrelated content moves, while chunk_content_hash is stable for
        # unchanged text. Because result_json embeds absolute evidence line
        # numbers and the source chunk_id, a reused result whose chunk moved
        # is remapped by the start-line delta (a purely structural offset for
        # byte-identical text) so evidence still resolves against the current
        # snapshot. Deleted chunks never get a pending row for the current
        # build in the first place (they are not in the current snapshot's
        # documentation index), so they cannot be pulled into the graph via
        # this reuse path.
        reusable = conn.execute(
            "SELECT id, chunk_id FROM system_understanding_llm_tasks "
            "WHERE build_id = ? AND status = 'pending' ORDER BY id",
            (ctx.build_id,),
        ).fetchall()
        for row in reusable:
            chunk = chunk_by_id.get(row["chunk_id"])
            if chunk is None:
                continue
            prev = conn.execute(
                """SELECT result_json, chunk_id, chunk_start_line
                   FROM system_understanding_llm_tasks
                   WHERE system_id = ? AND chunk_content_hash = ? AND chunk_path = ?
                     AND prompt_version = ? AND schema_version = ?
                     AND status = 'completed' AND result_json IS NOT NULL
                     AND build_id != ?
                   ORDER BY id DESC LIMIT 1""",
                (ctx.system_id, chunk.content_hash, chunk.path,
                 scanner.PROMPT_VERSION, scanner.SCHEMA_VERSION, ctx.build_id),
            ).fetchone()
            if prev is None:
                continue
            if prev["chunk_start_line"] is None:
                # Row predates chunk_start_line: without the original position
                # the evidence lines can only be trusted when the chunk_id
                # (path + start_line + heading) is unchanged.
                if prev["chunk_id"] != chunk.chunk_id:
                    continue
                delta = 0
            else:
                delta = chunk.start_line - prev["chunk_start_line"]
            try:
                result = scanner.ChunkScanResult.model_validate_json(prev["result_json"])
            except ValueError:
                continue
            result.chunk_id = chunk.chunk_id
            if delta:
                for claim in result.claims:
                    claim.evidence.start_line += delta
                    claim.evidence.end_line += delta
            conn.execute(
                """UPDATE system_understanding_llm_tasks
                   SET status = 'completed', reused_existing = 1,
                       completed_at = ?, result_json = ?
                   WHERE id = ?""",
                (now, result.model_dump_json(), row["id"]),
            )
        pending = conn.execute(
            "SELECT * FROM system_understanding_llm_tasks "
            "WHERE build_id = ? AND status = 'pending' ORDER BY id",
            (ctx.build_id,),
        ).fetchall()

    if pending:
        config = LLMConfig.intelligence_from_env()
        client = create_llm_client(config)

        for task in pending:
            ctx.check_cancelled()
            chunk = chunk_by_id.get(task["chunk_id"])
            if chunk is None:
                with get_conn() as conn:
                    conn.execute(
                        """UPDATE system_understanding_llm_tasks
                           SET status = 'failed', last_error = 'Chunk no longer present in index',
                               completed_at = ? WHERE id = ?""",
                        (time.time(), task["id"]),
                    )
                continue

            attempts = 0
            last_error: Optional[str] = None
            succeeded = False
            while attempts < max_attempts:
                attempts += 1
                with get_conn() as conn:
                    conn.execute(
                        """UPDATE system_understanding_llm_tasks
                           SET status = 'running', attempts = ?,
                               started_at = COALESCE(started_at, ?)
                           WHERE id = ?""",
                        (attempts, time.time(), task["id"]),
                    )
                ctx.heartbeat()
                result = scanner.scan_chunk(client, config, chunk)
                if result.error is None:
                    with get_conn() as conn:
                        conn.execute(
                            """UPDATE system_understanding_llm_tasks
                               SET status = 'completed', result_json = ?, last_error = NULL,
                                   completed_at = ? WHERE id = ?""",
                            (result.model_dump_json(), time.time(), task["id"]),
                        )
                    succeeded = True
                    break
                last_error = result.error
                if attempts < max_attempts:
                    _sleep_backoff(ctx, attempts)
            if not succeeded:
                with get_conn() as conn:
                    conn.execute(
                        """UPDATE system_understanding_llm_tasks
                           SET status = 'failed', last_error = ?, completed_at = ?
                           WHERE id = ?""",
                        (last_error, time.time(), task["id"]),
                    )

    with get_conn() as conn:
        counts = {
            r["status"]: r["n"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM system_understanding_llm_tasks "
                "WHERE build_id = ? GROUP BY status",
                (ctx.build_id,),
            ).fetchall()
        }
        reused = conn.execute(
            "SELECT COUNT(*) FROM system_understanding_llm_tasks "
            "WHERE build_id = ? AND reused_existing = 1",
            (ctx.build_id,),
        ).fetchone()[0]
        first_error_row = conn.execute(
            "SELECT last_error FROM system_understanding_llm_tasks "
            "WHERE build_id = ? AND status = 'failed' ORDER BY id LIMIT 1",
            (ctx.build_id,),
        ).fetchone()

    total = sum(counts.values())
    failed = counts.get("failed", 0)
    provenance = {
        "chunks_total": total,
        "chunks_completed": counts.get("completed", 0),
        "chunks_failed": failed,
        "chunks_reused": reused,
    }
    if failed:
        first_error = first_error_row["last_error"] if first_error_row else "unknown"
        raise StepFailed(
            f"{failed}/{total} documentation chunks failed to scan "
            f"(retry re-scans only failed chunks). First error: {first_error}",
            provenance=provenance,
        )
    return provenance


def _run_understanding_graph(ctx: StepContext) -> Dict[str, Any]:
    from .documentation_claim_scanner import ChunkScanResult
    from .understanding_graph import build_understanding_graph, save_graph_snapshot

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT result_json FROM system_understanding_llm_tasks "
            "WHERE build_id = ? AND status = 'completed' AND result_json IS NOT NULL "
            "ORDER BY id",
            (ctx.build_id,),
        ).fetchall()

    scan_results = [ChunkScanResult.model_validate_json(r["result_json"]) for r in rows]
    graph = build_understanding_graph(scan_results)
    ctx.heartbeat()

    with get_conn() as conn:
        graph_snapshot_id = save_graph_snapshot(
            conn, ctx.system_id, graph, snapshot_id=ctx.snapshot_id
        )
    return {
        "understanding_graph_snapshot_id": graph_snapshot_id,
        "claim_count": graph.claim_count,
        "node_count": len(graph.nodes),
        "scanned_chunks": len(scan_results),
    }


def _run_docs_code_reconcile(ctx: StepContext) -> Dict[str, Any]:
    from .docs_code_reconciler import reconcile
    from .system_understanding_service import _load_graph_for_snapshot

    with get_conn() as conn:
        graph = _load_graph_for_snapshot(conn, ctx.system_id, ctx.snapshot_id)
        if graph is None:
            raise StepFailed("No understanding graph snapshot available to reconcile")
        result = reconcile(conn, ctx.system_id, ctx.snapshot_id, graph)
    return {"mapping_count": len(result.mappings), "gap_count": len(result.gaps)}


def _run_capability_hierarchy(ctx: StepContext) -> Dict[str, Any]:
    from .capability_hierarchy import (
        build_hierarchy,
        PROMPT_VERSION as HIERARCHY_PROMPT_VERSION,
        SCHEMA_VERSION as HIERARCHY_SCHEMA_VERSION,
    )
    from .routes.project_intelligence import (
        _hierarchy_symbol_records,
        _hierarchy_entrypoint_records,
        _persist_hierarchy_node,
    )

    with get_conn() as conn:
        symbols_h = _hierarchy_symbol_records(conn, ctx.snapshot_id, ctx.system_id)
        entrypoints_h = _hierarchy_entrypoint_records(conn, ctx.snapshot_id, ctx.system_id)
        draft_row = conn.execute(
            "SELECT id, name, purpose FROM system_profile_drafts "
            "WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
            (ctx.system_id, ctx.snapshot_id),
        ).fetchone()

    if not symbols_h:
        # Issue #210: still persist a completed intelligence_runs row so the
        # job-step table (this step "completed") and the intelligence_runs-
        # derived pipeline checklist / system_state item agree that the run
        # happened, instead of the checklist showing "missing"/"blocked" for
        # a build the job panel already reports as done.
        now = time.time()
        with get_conn() as conn:
            run_id = conn.execute(
                """INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method,
                     status, is_mock, started_at, completed_at)
                VALUES (?, ?, 'capability_hierarchy', 'deterministic', 'none',
                        ?, ?, 'deterministic', 'completed', 0, ?, ?)""",
                (ctx.system_id, ctx.snapshot_id, HIERARCHY_PROMPT_VERSION,
                 HIERARCHY_SCHEMA_VERSION, now, now),
            ).lastrowid
        return {
            "intelligence_run_id": run_id,
            "capability_count": 0,
            "note": "No code symbols to group",
        }

    sp_draft = (
        {"id": draft_row["id"], "name": draft_row["name"], "purpose": draft_row["purpose"]}
        if draft_row else None
    )
    started_at = time.time()
    built = build_hierarchy(symbols_h, entrypoints_h, sp_draft)
    ctx.heartbeat()

    with get_conn() as conn:
        now = time.time()
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method,
                 status, is_mock, started_at, completed_at)
            VALUES (?, ?, 'capability_hierarchy', 'deterministic', 'none',
                    ?, ?, 'deterministic', 'completed', 0, ?, ?)""",
            (ctx.system_id, ctx.snapshot_id, HIERARCHY_PROMPT_VERSION,
             HIERARCHY_SCHEMA_VERSION, started_at, now),
        ).lastrowid

        node_count = 0
        purpose_id = None
        if built.purpose is not None:
            purpose_id = _persist_hierarchy_node(
                conn, ctx.system_id, ctx.snapshot_id, run_id, built.purpose, None, now
            )
            node_count += 1
        for cap in built.capabilities:
            _persist_hierarchy_node(
                conn, ctx.system_id, ctx.snapshot_id, run_id, cap, purpose_id, now
            )
            node_count += 1
        for node in built.unclassified_elements:
            _persist_hierarchy_node(
                conn, ctx.system_id, ctx.snapshot_id, run_id, node, None, now
            )
            node_count += 1
        for node in built.unattached_supporting:
            _persist_hierarchy_node(
                conn, ctx.system_id, ctx.snapshot_id, run_id, node, None, now
            )
            node_count += 1
    return {
        "intelligence_run_id": run_id,
        "capability_count": len(built.capabilities),
        "hierarchy_node_count": node_count,
    }


_STEP_RUNNERS = {
    "symbol_index": _run_symbol_index,
    "entrypoint_index": _run_entrypoint_index,
    "documentation_index": _run_documentation_index,
    "claim_scan": _run_claim_scan,
    "understanding_graph": _run_understanding_graph,
    "docs_code_reconcile": _run_docs_code_reconcile,
    "capability_hierarchy": _run_capability_hierarchy,
}
