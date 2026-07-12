"""Append-only audit trail for the GitHub publish workflow (Issue #227).

``publish_audit_events`` is a single, additive, system-scoped table. This is
the audit contract for Issue #227 (connection disconnect / auto-cancel /
cleanup events); Issue #226 is expected to extend it with publish job status
transitions rather than adding a parallel table.

``detail`` must stay a small JSON object of structural facts only -- job
ids, counts, a fixed reason string, a ``cleanup_state`` value. Never write an
installation token, JWT, private key, or filesystem path into it
(Principle 5/8).

Callers pass an already-open ``sqlite3.Connection`` so the audit row is
written inside the caller's own transaction/lock rather than opening a new
one (``db.get_conn`` serializes on a single process-wide lock, so a nested
``get_conn`` call would deadlock).
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Optional


def record_publish_audit_event(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    connection_id: Optional[int] = None,
    job_id: Optional[int] = None,
    event_type: str,
    actor_user_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO publish_audit_events
            (system_id, connection_id, job_id, event_type, actor_user_id, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            system_id,
            connection_id,
            job_id,
            event_type,
            actor_user_id,
            json.dumps(detail) if detail is not None else None,
            time.time(),
        ),
    )
