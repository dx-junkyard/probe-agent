"""Trace lineage query API (Issue #145, Phase 1).

Read-only, deterministic endpoints that return the traces related to a given
entity / correlation id / flow id in timestamp order, together with their span
metadata and entities. This is the minimal shape a client needs to assemble a
lineage graph; richer projections arrive in later phases.
"""

import json
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from ..auth import get_system_id
from ..db import get_conn
from ..models import (
    LineageEntityOut,
    LineageOut,
    LineageProjectionOut,
    LineageStepOut,
)

router = APIRouter()


def _assemble(conn, system_id: int, ordered: List[Tuple[str, float]]) -> List[LineageStepOut]:
    steps: List[LineageStepOut] = []
    for trace_id, _ts in ordered:
        trow = conn.execute(
            """
            SELECT trace_id, component_id, mode, output_text, error, duration_ms, timestamp
            FROM traces WHERE system_id = ? AND trace_id = ?
            """,
            (system_id, trace_id),
        ).fetchone()
        srow = conn.execute(
            """
            SELECT span_id, parent_span_id, flow_id, correlation_id
            FROM trace_spans WHERE system_id = ? AND trace_id = ?
            """,
            (system_id, trace_id),
        ).fetchone()
        erows = conn.execute(
            """
            SELECT entity_type, entity_id, role
            FROM trace_entities WHERE system_id = ? AND trace_id = ?
            ORDER BY id
            """,
            (system_id, trace_id),
        ).fetchall()
        prows = conn.execute(
            """
            SELECT projection_name, phase, data_json, data_hash, truncated, extract_error
            FROM trace_projections WHERE system_id = ? AND trace_id = ?
            ORDER BY projection_name, phase
            """,
            (system_id, trace_id),
        ).fetchall()

        entities = [
            LineageEntityOut(type=e["entity_type"], id=e["entity_id"], role=e["role"])
            for e in erows
        ]
        projections = []
        for p in prows:
            try:
                data = json.loads(p["data_json"]) if p["data_json"] else {}
            except json.JSONDecodeError:
                data = {}
            projections.append(LineageProjectionOut(
                projection_name=p["projection_name"],
                phase=p["phase"],
                fields=data.get("fields", {}) or {},
                metrics=data.get("metrics", {}) or {},
                samples=data.get("samples", {}) or {},
                data_hash=p["data_hash"],
                truncated=bool(p["truncated"]),
                error=p["extract_error"],
            ))
        if trow is not None:
            step = LineageStepOut(
                trace_id=trow["trace_id"],
                component_id=trow["component_id"],
                mode=trow["mode"],
                duration_ms=trow["duration_ms"],
                timestamp=trow["timestamp"],
                output=trow["output_text"],
                error=trow["error"],
                span_id=srow["span_id"] if srow else None,
                parent_span_id=srow["parent_span_id"] if srow else None,
                flow_id=srow["flow_id"] if srow else None,
                correlation_id=srow["correlation_id"] if srow else None,
                entities=entities,
                projections=projections,
            )
        else:
            # Span/entity exists but the trace row was pruned; still surface it.
            step = LineageStepOut(
                trace_id=trace_id,
                component_id="",
                timestamp=_ts,
                span_id=srow["span_id"] if srow else None,
                parent_span_id=srow["parent_span_id"] if srow else None,
                flow_id=srow["flow_id"] if srow else None,
                correlation_id=srow["correlation_id"] if srow else None,
                entities=entities,
                projections=projections,
            )
        steps.append(step)
    return steps


def _limit(limit: Optional[int]) -> int:
    if limit is None:
        return 500
    return max(1, min(limit, 2000))


def _window(col: str, start: Optional[float], end: Optional[float]) -> Tuple[str, list]:
    """Optional time-window filter (Issue #147): unix-seconds bounds on ``col``."""
    sql, params = "", []
    if start is not None:
        sql += f" AND {col} >= ?"
        params.append(start)
    if end is not None:
        sql += f" AND {col} <= ?"
        params.append(end)
    return sql, params


@router.get("/trace-lineage/entities/{entity_type}/{entity_id}", response_model=LineageOut)
def lineage_by_entity(
    entity_type: str,
    entity_id: str,
    limit: Optional[int] = Query(default=None),
    start: Optional[float] = Query(default=None),
    end: Optional[float] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> LineageOut:
    win_sql, win_params = _window("tr.timestamp", start, end)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT te.trace_id AS trace_id, tr.timestamp AS timestamp
            FROM trace_entities te
            JOIN traces tr ON tr.system_id = te.system_id AND tr.trace_id = te.trace_id
            WHERE te.system_id = ? AND te.entity_type = ? AND te.entity_id = ?{win_sql}
            ORDER BY tr.timestamp ASC, te.trace_id ASC
            LIMIT ?
            """,
            (system_id, entity_type, entity_id, *win_params, _limit(limit)),
        ).fetchall()
        ordered = [(r["trace_id"], r["timestamp"]) for r in rows]
        steps = _assemble(conn, system_id, ordered)
    return LineageOut(
        query={"kind": "entity", "entity_type": entity_type, "entity_id": entity_id},
        steps=steps,
    )


@router.get("/trace-lineage/correlations/{correlation_id}", response_model=LineageOut)
def lineage_by_correlation(
    correlation_id: str,
    limit: Optional[int] = Query(default=None),
    start: Optional[float] = Query(default=None),
    end: Optional[float] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> LineageOut:
    win_sql, win_params = _window("timestamp", start, end)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT trace_id, timestamp FROM trace_spans
            WHERE system_id = ? AND correlation_id = ?{win_sql}
            ORDER BY timestamp ASC, trace_id ASC
            LIMIT ?
            """,
            (system_id, correlation_id, *win_params, _limit(limit)),
        ).fetchall()
        ordered = [(r["trace_id"], r["timestamp"]) for r in rows]
        steps = _assemble(conn, system_id, ordered)
    return LineageOut(
        query={"kind": "correlation", "correlation_id": correlation_id}, steps=steps
    )


@router.get("/trace-lineage/flows/{flow_id}", response_model=LineageOut)
def lineage_by_flow(
    flow_id: str,
    limit: Optional[int] = Query(default=None),
    start: Optional[float] = Query(default=None),
    end: Optional[float] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> LineageOut:
    win_sql, win_params = _window("timestamp", start, end)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT trace_id, timestamp FROM trace_spans
            WHERE system_id = ? AND flow_id = ?{win_sql}
            ORDER BY timestamp ASC, trace_id ASC
            LIMIT ?
            """,
            (system_id, flow_id, *win_params, _limit(limit)),
        ).fetchall()
        ordered = [(r["trace_id"], r["timestamp"]) for r in rows]
        steps = _assemble(conn, system_id, ordered)
    return LineageOut(query={"kind": "flow", "flow_id": flow_id}, steps=steps)
