"""Connectivity status for the caller's system (Issue #165).

Deterministic, LLM-free signal-reception facts backing the Dashboard's
connectivity warning badge and the setup-guide page. The state vocabulary is
a finite set (``no_signal`` / ``smoke_only`` / ``receiving``); smoke traces
are recognized only by exact ``component_id`` match against the documented
``probe-smoke-check`` convention. This endpoint never infers *why* nothing
arrived — it only reports what was observed.
"""

from fastapi import APIRouter, Depends

from ..auth import get_system_id
from ..db import get_conn
from ..models import (
    SMOKE_CHECK_COMPONENT_ID,
    ConnectivityStatusOut,
)

router = APIRouter()


@router.get("/connectivity/status", response_model=ConnectivityStatusOut)
def get_connectivity_status(
    system_id: int = Depends(get_system_id),
) -> ConnectivityStatusOut:
    with get_conn() as conn:
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN component_id = ? THEN 1 ELSE 0 END) AS smoke,
                MIN(timestamp) AS first_at,
                MAX(timestamp) AS last_at
            FROM traces
            WHERE system_id = ?
            """,
            (SMOKE_CHECK_COMPONENT_ID, system_id),
        ).fetchone()

        total = counts["total"] or 0
        smoke = counts["smoke"] or 0
        real = total - smoke

        last_component = None
        if total > 0:
            last_row = conn.execute(
                """
                SELECT component_id FROM traces
                WHERE system_id = ?
                ORDER BY timestamp DESC, rowid DESC
                LIMIT 1
                """,
                (system_id,),
            ).fetchone()
            last_component = last_row["component_id"] if last_row else None

        materialized_rows = conn.execute(
            """
            SELECT id FROM interview_session
            WHERE system_id = ?
              AND materialization_diff IS NOT NULL
              AND materialization_diff != ''
            ORDER BY id
            """,
            (system_id,),
        ).fetchall()

    if real > 0:
        state = "receiving"
    elif smoke > 0:
        state = "smoke_only"
    else:
        state = "no_signal"

    return ConnectivityStatusOut(
        system_id=system_id,
        state=state,
        total_trace_count=total,
        smoke_trace_count=smoke,
        real_trace_count=real,
        first_trace_at=counts["first_at"],
        last_trace_at=counts["last_at"],
        last_trace_component_id=last_component,
        materialized_session_ids=[r["id"] for r in materialized_rows],
    )
