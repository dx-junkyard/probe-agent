"""Instrumentation coverage API: code-side instrumentation points with their
runtime reception status (see app/instrumentation_coverage.py).

probe-agent:
  role: API boundary listing every instrumented point and whether its traces arrive
  capability: probe-instrumentation-coverage
  element_type: boundary
  consumers: [dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify instrumented points without traces are listed instead of hidden
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from ..auth import get_system_id
from ..db import get_conn
from ..instrumentation_coverage import build_coverage, recent_traces
from .probe_patterns import _latest_ready_snapshot, _require_indexed_snapshot

router = APIRouter()


@router.get("/instrumentation-coverage")
def get_instrumentation_coverage(system_id: int = Depends(get_system_id)) -> Dict[str, Any]:
    with get_conn() as conn:
        snapshot = _require_indexed_snapshot(conn, system_id)
        return build_coverage(conn, system_id, snapshot["id"])


@router.get("/instrumentation-coverage/recent-traces")
def get_recent_traces(
    limit: int = 30,
    since: Optional[float] = None,
    system_id: int = Depends(get_system_id),
) -> Dict[str, Any]:
    """Live view feed: newest traces, each with the code location that sent it."""
    with get_conn() as conn:
        snapshot = _latest_ready_snapshot(conn, system_id)
        items = recent_traces(conn, system_id, snapshot["id"] if snapshot else None, limit=limit, since=since)
    return {"items": items}
