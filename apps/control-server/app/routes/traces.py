import json
import time
from typing import List

from fastapi import APIRouter

from ..db import get_conn
from ..models import TraceEvent

router = APIRouter()


def _ensure_component(conn, component_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO components (component_id, mode, updated_at) VALUES (?, 'trace', ?)",
        (component_id, time.time()),
    )


@router.post("/traces", status_code=201)
def post_trace(event: TraceEvent) -> dict:
    with get_conn() as conn:
        _ensure_component(conn, event.component_id)
        conn.execute(
            """
            INSERT OR REPLACE INTO traces
                (trace_id, component_id, mode, input_json, output_text,
                 error, duration_ms, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.trace_id,
                event.component_id,
                event.mode,
                json.dumps(event.input, ensure_ascii=False) if event.input is not None else None,
                event.output,
                event.error,
                event.duration_ms,
                event.timestamp,
            ),
        )
    return {"ok": True, "trace_id": event.trace_id}


@router.get("/components/{component_id}/traces")
def list_traces(component_id: str, limit: int = 50) -> List[dict]:
    limit = max(1, min(limit, 500))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trace_id, component_id, mode, input_json, output_text,
                   error, duration_ms, timestamp
            FROM traces
            WHERE component_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (component_id, limit),
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
        result.append(d)
    return result
