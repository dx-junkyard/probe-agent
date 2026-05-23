import time
from typing import List

from fastapi import APIRouter, HTTPException

from ..db import get_conn
from ..models import EvaluationUpdate, ShadowResult

router = APIRouter()


@router.post("/components/{component_id}/shadow-results", status_code=201)
def post_shadow_result(component_id: str, result: ShadowResult) -> dict:
    if result.component_id != component_id:
        raise HTTPException(400, "component_id mismatch")

    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO components (component_id, mode, updated_at) VALUES (?, 'trace', ?)",
            (component_id, time.time()),
        )
        cur = conn.execute(
            """
            INSERT INTO shadow_results
                (trace_id, component_id, current_output, candidate_output,
                 candidate_error, candidate_duration_ms, evaluation, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                result.trace_id,
                result.component_id,
                result.current_output,
                result.candidate_output,
                result.candidate_error,
                result.candidate_duration_ms,
                result.timestamp,
            ),
        )
    return {"ok": True, "id": cur.lastrowid}


@router.get("/components/{component_id}/shadow-results")
def list_shadow_results(component_id: str, limit: int = 50) -> List[dict]:
    limit = max(1, min(limit, 500))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, trace_id, component_id, current_output, candidate_output,
                   candidate_error, candidate_duration_ms, evaluation, timestamp
            FROM shadow_results
            WHERE component_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (component_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/shadow-results/{result_id}/evaluation")
def set_evaluation(result_id: int, update: EvaluationUpdate) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE shadow_results SET evaluation = ? WHERE id = ?",
            (update.evaluation, result_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "shadow result not found")
    return {"ok": True, "id": result_id, "evaluation": update.evaluation}
