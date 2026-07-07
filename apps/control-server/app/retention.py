"""Deterministic, system-scoped retention for lineage/projection/analyzer data
(Issue #152).

Default behaviour with no configured policy is "never delete". Deletion is
oldest-first, batched (to avoid long SQLite locks), and audited. All queries are
scoped by ``system_id`` so one system never touches another's data.
"""

import os
import time
from typing import Any, Dict, List, Optional

# target_table -> age column used for both age-cutoff and newest-N ordering.
RETENTION_TABLES: Dict[str, str] = {
    "trace_spans": "timestamp",
    "trace_entities": "timestamp",
    "trace_projections": "created_at",
    "trace_analysis_runs": "started_at",
}


def batch_size() -> int:
    try:
        return max(1, int(os.getenv("RETENTION_BATCH_SIZE", "1000")))
    except (TypeError, ValueError):
        return 1000


def _delete_batched(conn, target: str, where: str, params: list) -> int:
    """Delete rows matching ``where`` in bounded batches (by rowid)."""
    bs = batch_size()
    total = 0
    while True:
        cur = conn.execute(
            f"DELETE FROM {target} WHERE rowid IN "
            f"(SELECT rowid FROM {target} WHERE {where} LIMIT ?)",
            [*params, bs],
        )
        n = cur.rowcount or 0
        total += n
        if n < bs:
            break
    return total


def _apply_one(conn, system_id: int, target: str, age_col: str,
               max_age_days: Optional[float], max_count: Optional[int]) -> int:
    deleted = 0
    if max_age_days is not None:
        cutoff = time.time() - max_age_days * 86400.0
        deleted += _delete_batched(
            conn, target, f"system_id = ? AND {age_col} < ?", [system_id, cutoff]
        )
    if max_count is not None:
        # Keep the newest max_count rows for this system; delete the rest.
        where = (
            f"system_id = ? AND rowid NOT IN "
            f"(SELECT rowid FROM {target} WHERE system_id = ? "
            f"ORDER BY {age_col} DESC, rowid DESC LIMIT {int(max_count)})"
        )
        deleted += _delete_batched(conn, target, where, [system_id, system_id])
    return deleted


def apply_retention(conn, system_id: int, reason: str = "manual") -> List[Dict[str, Any]]:
    """Apply all configured policies for a system. Returns per-target results
    and writes an audit row per applied target."""
    now = time.time()
    results: List[Dict[str, Any]] = []
    policies = conn.execute(
        "SELECT target_table, max_age_days, max_count FROM retention_policies "
        "WHERE system_id = ?",
        (system_id,),
    ).fetchall()
    for pol in policies:
        target = pol["target_table"]
        age_col = RETENTION_TABLES.get(target)
        if age_col is None:
            continue  # unknown target — never touch arbitrary tables
        if pol["max_age_days"] is None and pol["max_count"] is None:
            continue  # policy present but unbounded => keep everything
        deleted = _apply_one(
            conn, system_id, target, age_col, pol["max_age_days"], pol["max_count"]
        )
        conn.execute(
            "INSERT INTO retention_audit (system_id, target_table, deleted_count, "
            "reason, executed_at) VALUES (?, ?, ?, ?, ?)",
            (system_id, target, deleted, reason, now),
        )
        results.append({"target_table": target, "deleted_count": deleted})
    return results


def projection_expiry_cutoff(conn, system_id: int) -> Optional[float]:
    """The age cutoff (unix ts) below which projections may have been pruned,
    or None if no age-based projection policy exists. Used to flag analyzer runs
    whose referenced data may be gone."""
    row = conn.execute(
        "SELECT max_age_days FROM retention_policies "
        "WHERE system_id = ? AND target_table = 'trace_projections'",
        (system_id,),
    ).fetchone()
    if row is None or row["max_age_days"] is None:
        return None
    return time.time() - row["max_age_days"] * 86400.0
