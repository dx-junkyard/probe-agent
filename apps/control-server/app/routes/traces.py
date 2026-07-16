import json
import time
from typing import List

from fastapi import APIRouter, Depends

from ..auth import get_system_id
from ..db import get_conn
from ..models import ProjectionOut, TraceEvent

router = APIRouter()


def _ensure_component(conn, system_id: int, component_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO components
            (system_id, component_id, mode, updated_at)
        VALUES (?, ?, 'trace', ?)
        """,
        (system_id, component_id, time.time()),
    )


def _write_lineage(conn, system_id: int, event: TraceEvent) -> None:
    """Persist optional lineage metadata (Issue #145) in dedicated tables.

    Span and entity rows are re-materialized per trace so a re-post stays
    consistent with the INSERT OR REPLACE ``traces`` row: a re-post without
    span metadata deletes the stale span, so old correlation/flow ids never
    survive onto a trace that no longer carries them.
    """
    has_span = any(
        v is not None
        for v in (event.span_id, event.parent_span_id, event.flow_id, event.correlation_id)
    )
    # Re-materialize the span row for this trace (idempotent on re-post).
    conn.execute(
        "DELETE FROM trace_spans WHERE system_id = ? AND trace_id = ?",
        (system_id, event.trace_id),
    )
    if has_span:
        conn.execute(
            """
            INSERT INTO trace_spans
                (system_id, trace_id, component_id, span_id, parent_span_id,
                 flow_id, correlation_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                event.trace_id,
                event.component_id,
                event.span_id,
                event.parent_span_id,
                event.flow_id,
                event.correlation_id,
                event.timestamp,
            ),
        )

    # Re-materialize entities for this trace (idempotent on re-post).
    conn.execute(
        "DELETE FROM trace_entities WHERE system_id = ? AND trace_id = ?",
        (system_id, event.trace_id),
    )
    for ent in event.entities or []:
        conn.execute(
            """
            INSERT INTO trace_entities
                (system_id, trace_id, component_id, entity_type, entity_id, role, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                event.trace_id,
                event.component_id,
                ent.type,
                ent.id,
                ent.role,
                event.timestamp,
            ),
        )


def _write_projections(conn, system_id: int, event: TraceEvent) -> None:
    """Persist optional projections (Issue #146). Only the bounded, structured
    slice is stored — never the raw payload. Idempotent on re-post.

    The trace's own input/output projections are re-materialized per trace so a
    re-post with fewer (or no) projections cannot leave stale rows that then
    look like they belong to the new trace. Shadow projections
    (shadow_current/shadow_candidate) are owned by the shadow-results route and
    use a disjoint phase namespace, so they are deliberately left untouched.
    """
    conn.execute(
        "DELETE FROM trace_projections WHERE system_id = ? AND trace_id = ? "
        "AND phase NOT IN ('shadow_current', 'shadow_candidate')",
        (system_id, event.trace_id),
    )
    for proj in event.projections or []:
        data_json = json.dumps(
            {"fields": proj.fields, "metrics": proj.metrics, "samples": proj.samples},
            ensure_ascii=False,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO trace_projections
                (system_id, trace_id, component_id, projection_name, phase,
                 data_json, data_hash, truncated, extract_error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                event.trace_id,
                event.component_id,
                proj.projection_name,
                proj.phase,
                data_json,
                proj.data_hash,
                1 if proj.truncated else 0,
                proj.error,
                event.timestamp,
            ),
        )


@router.post("/traces", status_code=201)
def post_trace(
    event: TraceEvent, system_id: int = Depends(get_system_id)
) -> dict:
    with get_conn() as conn:
        _ensure_component(conn, system_id, event.component_id)
        conn.execute(
            """
            INSERT OR REPLACE INTO traces
                (system_id, trace_id, component_id, mode, input_json, output_text,
                 error, duration_ms, timestamp,
                 input_capture_json, replayability, replay_reasons_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                event.trace_id,
                event.component_id,
                event.mode,
                json.dumps(event.input, ensure_ascii=False) if event.input is not None else None,
                event.output,
                event.error,
                event.duration_ms,
                event.timestamp,
                # Replay capture (Issue #242 Phase A / #243): stored verbatim as
                # JSON; NULL when the SDK did not opt this component in.
                json.dumps(event.input_capture, ensure_ascii=False)
                if event.input_capture is not None else None,
                event.replayability,
                json.dumps(event.replay_reasons, ensure_ascii=False)
                if event.replay_reasons is not None else None,
            ),
        )
        _write_lineage(conn, system_id, event)
        _write_projections(conn, system_id, event)
    return {"ok": True, "trace_id": event.trace_id}


def _row_to_projection(row) -> ProjectionOut:
    try:
        data = json.loads(row["data_json"]) if row["data_json"] else {}
    except json.JSONDecodeError:
        data = {}
    return ProjectionOut(
        trace_id=row["trace_id"],
        component_id=row["component_id"],
        projection_name=row["projection_name"],
        phase=row["phase"],
        fields=data.get("fields", {}) or {},
        metrics=data.get("metrics", {}) or {},
        samples=data.get("samples", {}) or {},
        data_hash=row["data_hash"],
        truncated=bool(row["truncated"]),
        error=row["extract_error"],
        created_at=row["created_at"],
    )


@router.get("/traces/{trace_id}/projections", response_model=List[ProjectionOut])
def list_trace_projections(
    trace_id: str, system_id: int = Depends(get_system_id)
) -> List[ProjectionOut]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trace_id, component_id, projection_name, phase, data_json,
                   data_hash, truncated, extract_error, created_at
            FROM trace_projections
            WHERE system_id = ? AND trace_id = ?
            ORDER BY projection_name, phase
            """,
            (system_id, trace_id),
        ).fetchall()
    return [_row_to_projection(r) for r in rows]


@router.get("/components/{component_id}/projections", response_model=List[ProjectionOut])
def list_component_projections(
    component_id: str,
    limit: int = 100,
    system_id: int = Depends(get_system_id),
) -> List[ProjectionOut]:
    limit = max(1, min(limit, 1000))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trace_id, component_id, projection_name, phase, data_json,
                   data_hash, truncated, extract_error, created_at
            FROM trace_projections
            WHERE system_id = ? AND component_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (system_id, component_id, limit),
        ).fetchall()
    return [_row_to_projection(r) for r in rows]


@router.get("/components/{component_id}/traces")
def list_traces(
    component_id: str,
    limit: int = 50,
    system_id: int = Depends(get_system_id),
) -> List[dict]:
    limit = max(1, min(limit, 500))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trace_id, component_id, mode, input_json, output_text,
                   error, duration_ms, timestamp,
                   input_capture_json, replayability, replay_reasons_json
            FROM traces
            WHERE system_id = ? AND component_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (system_id, component_id, limit),
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        if d.get("input_json"):
            try:
                d["input"] = json.loads(d["input_json"])
            except json.JSONDecodeError:
                d["input"] = d["input_json"]
        else:
            d["input"] = None
        d.pop("input_json", None)
        d["output"] = d.pop("output_text", None)
        # Replay capture (Issue #242 Phase A / #243): NULL columns (pre-Phase-A
        # rows or components not opted in) surface as explicit nulls.
        d["input_capture"] = _load_json_or_none(d.pop("input_capture_json", None))
        d["replay_reasons"] = _load_json_or_none(d.pop("replay_reasons_json", None))
        result.append(d)
    return result


def _load_json_or_none(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
