import json
import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..models import EvaluationUpdate, ShadowResult
from ..trace_redaction import redact_projection, redact_shadow_outputs

router = APIRouter()


def _write_shadow_projections(conn, system_id: int, result: ShadowResult) -> None:
    """Persist shadow_current / shadow_candidate projections (Issue #150),
    keyed by the existing trace_id. Never stores the raw payload.

    Issue #367: redacted on the write path, on the same contract as the trace
    route's projections — this endpoint reaches the same table.
    """
    if not result.projections:
        return
    for proj in result.projections:
        redaction = redact_projection(
            fields=proj.fields, metrics=proj.metrics, samples=proj.samples
        )
        data_json = json.dumps(redaction.values, ensure_ascii=False)
        conn.execute(
            """
            INSERT OR REPLACE INTO trace_projections
                (system_id, trace_id, component_id, projection_name, phase,
                 data_json, data_hash, truncated, extract_error, created_at,
                 redaction_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                result.trace_id,
                result.component_id,
                proj.projection_name,
                proj.phase,
                data_json,
                proj.data_hash,
                1 if proj.truncated else 0,
                proj.error,
                result.timestamp,
                json.dumps(redaction.summary(), ensure_ascii=False),
            ),
        )


@router.post("/components/{component_id}/shadow-results", status_code=201)
def post_shadow_result(
    component_id: str,
    result: ShadowResult,
    system_id: int = Depends(get_system_id),
) -> dict:
    if result.component_id != component_id:
        raise HTTPException(400, "component_id mismatch")

    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO components
                (system_id, component_id, mode, updated_at)
            VALUES (?, ?, 'trace', ?)
            """,
            (system_id, component_id, time.time()),
        )
        # Issue #367: the current/candidate reprs are exactly the shape the
        # audited leak took on the trace side, arriving through another route.
        redaction = redact_shadow_outputs(
            current_output=result.current_output,
            candidate_output=result.candidate_output,
            candidate_error=result.candidate_error,
        )
        cur = conn.execute(
            """
            INSERT INTO shadow_results
                (system_id, trace_id, component_id, current_output, candidate_output,
                 candidate_error, candidate_duration_ms, evaluation, timestamp,
                 redaction_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                system_id,
                result.trace_id,
                result.component_id,
                redaction.values["current_output"],
                redaction.values["candidate_output"],
                redaction.values["candidate_error"],
                result.candidate_duration_ms,
                result.timestamp,
                json.dumps(redaction.summary(), ensure_ascii=False),
            ),
        )
        _write_shadow_projections(conn, system_id, result)
    return {"ok": True, "id": cur.lastrowid}


@router.get("/components/{component_id}/shadow-results")
def list_shadow_results(
    component_id: str,
    limit: int = 50,
    system_id: int = Depends(get_system_id),
) -> List[dict]:
    limit = max(1, min(limit, 500))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, trace_id, component_id, current_output, candidate_output,
                   candidate_error, candidate_duration_ms, evaluation, timestamp
            FROM shadow_results
            WHERE system_id = ? AND component_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (system_id, component_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/shadow-results/{result_id}/evaluation")
def set_evaluation(
    result_id: int,
    update: EvaluationUpdate,
    system_id: int = Depends(get_system_id),
) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE shadow_results SET evaluation = ?
            WHERE id = ? AND system_id = ?
            """,
            (update.evaluation, result_id, system_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "shadow result not found")
    return {"ok": True, "id": result_id, "evaluation": update.evaluation}
