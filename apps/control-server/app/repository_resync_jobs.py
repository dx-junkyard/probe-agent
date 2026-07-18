"""Persisted snapshot -> symbol-index orchestration (Issue #277).

Each job is scoped to one System and pins the symbol-index stage to the exact
snapshot created by the first stage. The configured target repository remains
read-only: the shared snapshot service only reads committed Git objects; all
job state and derived artifacts are written to probe-agent's database.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any, Dict, Optional

from .db import get_conn
from . import repository_refresh_service as refresh_service

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "snapshotting", "indexing")
TERMINAL_STATUSES = ("completed", "snapshot_failed", "index_failed")


class ResyncJobConflict(Exception):
    def __init__(self, job_id: int):
        super().__init__(f"Repository resync job #{job_id} is already active")
        self.job_id = job_id


def recover_interrupted_repository_resync_jobs() -> int:
    """Mark in-process jobs interrupted by a restart as terminal failures."""
    now = time.time()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT id, status, snapshot_id FROM repository_resync_jobs "
                "WHERE status IN ('queued', 'snapshotting', 'indexing')"
            ).fetchall()
            for row in rows:
                index_stage = row["status"] == "indexing" or row["snapshot_id"] is not None
                status = "index_failed" if index_stage else "snapshot_failed"
                stage = "symbol index" if index_stage else "snapshot"
                conn.execute(
                    "UPDATE repository_resync_jobs "
                    "SET status = ?, error = ?, completed_at = ? WHERE id = ?",
                    (
                        status,
                        f"Control Server restarted before the {stage} stage completed",
                        now,
                        row["id"],
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return len(rows)


def _row_dict(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def get_repository_resync_job(
    system_id: int, job_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        if job_id is None:
            row = conn.execute(
                "SELECT * FROM repository_resync_jobs "
                "WHERE system_id = ? ORDER BY id DESC LIMIT 1",
                (system_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM repository_resync_jobs WHERE id = ? AND system_id = ?",
                (job_id, system_id),
            ).fetchone()
    result = _row_dict(row)
    return _refresh_effective_stale_capability_count(result) if result else None


def start_repository_resync_job(system_id: int) -> int:
    """Create one job, rejecting a concurrent job for the same System."""
    now = time.time()
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            active = conn.execute(
                "SELECT id FROM repository_resync_jobs "
                "WHERE system_id = ? AND status IN ('queued', 'snapshotting', 'indexing') "
                "ORDER BY id DESC LIMIT 1",
                (system_id,),
            ).fetchone()
            if active is not None:
                conn.execute("ROLLBACK")
                raise ResyncJobConflict(active["id"])
            try:
                job_id = conn.execute(
                    "INSERT INTO repository_resync_jobs "
                    "(system_id, status, created_at) VALUES (?, 'queued', ?)",
                    (system_id, now),
                ).lastrowid
            except sqlite3.IntegrityError:
                active = conn.execute(
                    "SELECT id FROM repository_resync_jobs "
                    "WHERE system_id = ? AND status IN ('queued', 'snapshotting', 'indexing') "
                    "ORDER BY id DESC LIMIT 1",
                    (system_id,),
                ).fetchone()
                conn.execute("ROLLBACK")
                if active is not None:
                    raise ResyncJobConflict(active["id"])
                raise
            conn.execute("COMMIT")
        except ResyncJobConflict:
            raise
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise

    try:
        _spawn(job_id, system_id)
    except Exception as exc:
        _set_job(
            job_id,
            system_id,
            "snapshot_failed",
            error=f"Could not start repository resync worker: {exc}",
            completed=True,
        )
    return job_id


def _spawn(job_id: int, system_id: int) -> None:
    thread = threading.Thread(
        target=_run_safely, args=(job_id, system_id), daemon=True
    )
    thread.start()


def _set_job(
    job_id: int,
    system_id: int,
    status: str,
    *,
    snapshot_id: Optional[int] = None,
    error: Optional[str] = None,
    stale_capability_count: Optional[int] = None,
    started: bool = False,
    completed: bool = False,
) -> None:
    now = time.time()
    sets = ["status = ?", "error = ?"]
    args = [status, error]
    if snapshot_id is not None:
        sets.append("snapshot_id = ?")
        args.append(snapshot_id)
    if stale_capability_count is not None:
        sets.append("stale_capability_count = ?")
        args.append(stale_capability_count)
    if started:
        sets.append("started_at = COALESCE(started_at, ?)")
        args.append(now)
    if completed:
        sets.append("completed_at = ?")
        args.append(now)
    args.extend([job_id, system_id])
    with get_conn() as conn:
        conn.execute(
            f"UPDATE repository_resync_jobs SET {', '.join(sets)} "
            "WHERE id = ? AND system_id = ?",
            args,
        )


def _stale_capability_count_with_conn(
    conn, system_id: int, snapshot_id: int, *, require_latest_ready: bool = False
) -> int:
    """Count capabilities in the newest successful hierarchy for old code."""
    if require_latest_ready:
        latest = conn.execute(
            "SELECT id FROM repository_snapshots "
            "WHERE system_id = ? AND status = 'ready' ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
        if latest is None or latest["id"] != snapshot_id:
            return 0
    run = conn.execute(
        "SELECT id, snapshot_id FROM intelligence_runs "
        "WHERE system_id = ? AND run_type = 'capability_hierarchy' "
        "AND status = 'completed' ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    if run is None or run["snapshot_id"] == snapshot_id:
        return 0
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM capability_hierarchy_nodes "
        "WHERE system_id = ? AND intelligence_run_id = ? AND node_type = 'capability'",
        (system_id, run["id"]),
    ).fetchone()
    return int(row["cnt"])


def _stale_capability_count(system_id: int, snapshot_id: int) -> int:
    with get_conn() as conn:
        return _stale_capability_count_with_conn(conn, system_id, snapshot_id)


def _refresh_effective_stale_capability_count(row: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh persisted guidance after understanding catches up or HEAD moves."""
    if row["status"] != "completed" or row["snapshot_id"] is None:
        return row
    with get_conn() as conn:
        count = _stale_capability_count_with_conn(
            conn,
            row["system_id"],
            row["snapshot_id"],
            require_latest_ready=True,
        )
        row["stale_capability_count"] = count
    return row


def _execute_job(job_id: int, system_id: int) -> None:
    _set_job(job_id, system_id, "snapshotting", started=True)
    try:
        snapshot = refresh_service.create_snapshot_service(system_id)
    except Exception as exc:
        _set_job(
            job_id, system_id, "snapshot_failed", error=str(exc), completed=True
        )
        return

    _set_job(job_id, system_id, "snapshotting", snapshot_id=snapshot.id)
    if snapshot.status != "ready":
        _set_job(
            job_id,
            system_id,
            "snapshot_failed",
            snapshot_id=snapshot.id,
            error=snapshot.error_summary or "Snapshot creation failed",
            completed=True,
        )
        return

    _set_job(job_id, system_id, "indexing", snapshot_id=snapshot.id)
    try:
        refresh_service.index_symbols_service(system_id, snapshot.id)
    except Exception as exc:
        # The ready snapshot is intentionally retained for inspection/retry.
        _set_job(
            job_id,
            system_id,
            "index_failed",
            snapshot_id=snapshot.id,
            error=str(exc),
            completed=True,
        )
        return

    stale_count = _stale_capability_count(system_id, snapshot.id)
    _set_job(
        job_id,
        system_id,
        "completed",
        snapshot_id=snapshot.id,
        stale_capability_count=stale_count,
        completed=True,
    )


def _run_safely(job_id: int, system_id: int) -> None:
    try:
        _execute_job(job_id, system_id)
    except Exception as exc:  # pragma: no cover - final defensive boundary
        logger.exception("repository resync job %s crashed", job_id)
        row = get_repository_resync_job(system_id, job_id)
        failed_status = (
            "index_failed"
            if row is not None
            and (row["status"] == "indexing" or row["snapshot_id"] is not None)
            else "snapshot_failed"
        )
        _set_job(
            job_id, system_id, failed_status, error=str(exc), completed=True
        )
