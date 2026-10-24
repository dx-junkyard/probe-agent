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

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..instrumentation_coverage import build_coverage, recent_traces, request_waterfall
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


@router.get("/instrumentation-coverage/requests/{correlation_id}")
def get_request_waterfall(correlation_id: str, system_id: int = Depends(get_system_id)) -> Dict[str, Any]:
    """The traced calls of one request (one correlation id), as a waterfall."""
    with get_conn() as conn:
        snapshot = _latest_ready_snapshot(conn, system_id)
        out = request_waterfall(conn, system_id, snapshot["id"] if snapshot else None, correlation_id)
    if out is None:
        raise HTTPException(status_code=404, detail="No traces for this request")
    return out


@router.get("/instrumentation-coverage/traces/{trace_id}/request")
def get_request_of_trace(trace_id: str, system_id: int = Depends(get_system_id)) -> Dict[str, Any]:
    """The waterfall of the request a given trace belongs to (from the trace detail)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT correlation_id FROM trace_spans WHERE system_id = ? AND trace_id = ?", (system_id, trace_id)
        ).fetchone()
        if row is None or not row["correlation_id"]:
            raise HTTPException(status_code=404, detail="This trace was not recorded inside a request context")
        snapshot = _latest_ready_snapshot(conn, system_id)
        out = request_waterfall(conn, system_id, snapshot["id"] if snapshot else None, row["correlation_id"])
    if out is None:
        raise HTTPException(status_code=404, detail="No traces for this request")
    return out
