"""Trace analyzer API (Issue #148, Phase 4a).

Manual creation + review + read-only execution of declarative analyzers over
saved projections. Everything here is deterministic; LLM proposal is Issue #149.
"""

import json
import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..models import (
    AnalysisRunOut,
    AnalyzerReviewUpdate,
    TraceAnalyzerCreate,
    TraceAnalyzerOut,
)
from ..trace_analyzer import (
    AnalyzerLimitError,
    AnalyzerSpecError,
    compile_spec,
    run_analyzer,
)

router = APIRouter()


def _row_to_analyzer(row) -> TraceAnalyzerOut:
    try:
        spec = json.loads(row["spec_json"]) if row["spec_json"] else {}
    except json.JSONDecodeError:
        spec = {}
    return TraceAnalyzerOut(
        id=row["id"],
        name=row["name"],
        intent=row["intent"],
        spec=spec,
        source=row["source"],
        review_status=row["review_status"],
        decision_method=row["decision_method"],
        provider=row["provider"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _fetch_analyzer(conn, system_id: int, analyzer_id: int):
    row = conn.execute(
        "SELECT * FROM trace_analyzers WHERE id = ? AND system_id = ?",
        (analyzer_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "analyzer not found")
    return row


@router.post("/trace-analyzers", status_code=201, response_model=TraceAnalyzerOut)
def create_analyzer(
    body: TraceAnalyzerCreate, system_id: int = Depends(get_system_id)
) -> TraceAnalyzerOut:
    # Fail-closed schema validation: an invalid spec never gets saved.
    try:
        compile_spec(body.spec)
    except AnalyzerSpecError as e:
        raise HTTPException(422, f"invalid analyzer spec: {e}")

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO trace_analyzers
                (system_id, name, intent, spec_json, source, review_status,
                 decision_method, is_mock, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'trace_projections', 'proposed', 'manual', 0, ?, ?)
            """,
            (
                system_id,
                body.name,
                body.intent,
                json.dumps(body.spec, ensure_ascii=False),
                now,
                now,
            ),
        )
        row = _fetch_analyzer(conn, system_id, cur.lastrowid)
    return _row_to_analyzer(row)


@router.get("/trace-analyzers", response_model=List[TraceAnalyzerOut])
def list_analyzers(system_id: int = Depends(get_system_id)) -> List[TraceAnalyzerOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trace_analyzers WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
    return [_row_to_analyzer(r) for r in rows]


@router.get("/trace-analyzers/{analyzer_id}", response_model=TraceAnalyzerOut)
def get_analyzer(
    analyzer_id: int, system_id: int = Depends(get_system_id)
) -> TraceAnalyzerOut:
    with get_conn() as conn:
        row = _fetch_analyzer(conn, system_id, analyzer_id)
    return _row_to_analyzer(row)


@router.put("/trace-analyzers/{analyzer_id}/review", response_model=TraceAnalyzerOut)
def review_analyzer(
    analyzer_id: int,
    body: AnalyzerReviewUpdate,
    system_id: int = Depends(get_system_id),
) -> TraceAnalyzerOut:
    with get_conn() as conn:
        row = _fetch_analyzer(conn, system_id, analyzer_id)
        if row["review_status"] not in ("proposed", "approved", "rejected"):
            raise HTTPException(409, "analyzer is not in a reviewable state")
        conn.execute(
            "UPDATE trace_analyzers SET review_status = ?, updated_at = ? "
            "WHERE id = ? AND system_id = ?",
            (body.review_status, time.time(), analyzer_id, system_id),
        )
        row = _fetch_analyzer(conn, system_id, analyzer_id)
    return _row_to_analyzer(row)


def _row_to_run(row) -> AnalysisRunOut:
    result = None
    if row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError:
            result = None
    return AnalysisRunOut(
        id=row["id"],
        analyzer_id=row["analyzer_id"],
        status=row["status"],
        result=result,
        error_details=row["error_details"],
        row_count=row["row_count"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


@router.post("/trace-analyzers/{analyzer_id}/runs", status_code=201, response_model=AnalysisRunOut)
def create_run(
    analyzer_id: int, system_id: int = Depends(get_system_id)
) -> AnalysisRunOut:
    with get_conn() as conn:
        row = _fetch_analyzer(conn, system_id, analyzer_id)
        if row["review_status"] != "approved":
            raise HTTPException(409, "analyzer must be approved before it can run")

        started = time.time()
        cur = conn.execute(
            "INSERT INTO trace_analysis_runs (system_id, analyzer_id, status, started_at) "
            "VALUES (?, ?, 'pending', ?)",
            (system_id, analyzer_id, started),
        )
        run_id = cur.lastrowid

        try:
            spec = compile_spec(json.loads(row["spec_json"]))
            result = run_analyzer(conn, system_id, spec)
        except (AnalyzerLimitError, AnalyzerSpecError) as e:
            conn.execute(
                "UPDATE trace_analysis_runs SET status = 'failed', error_details = ?, "
                "completed_at = ? WHERE id = ? AND system_id = ?",
                (str(e), time.time(), run_id, system_id),
            )
        else:
            conn.execute(
                "UPDATE trace_analysis_runs SET status = 'completed', result_json = ?, "
                "row_count = ?, completed_at = ? WHERE id = ? AND system_id = ?",
                (
                    json.dumps(result, ensure_ascii=False, default=repr),
                    result.get("row_count"),
                    time.time(),
                    run_id,
                    system_id,
                ),
            )
        run = conn.execute(
            "SELECT * FROM trace_analysis_runs WHERE id = ? AND system_id = ?",
            (run_id, system_id),
        ).fetchone()
    return _row_to_run(run)


@router.get("/trace-analyzers/{analyzer_id}/runs", response_model=List[AnalysisRunOut])
def list_runs(
    analyzer_id: int, system_id: int = Depends(get_system_id)
) -> List[AnalysisRunOut]:
    with get_conn() as conn:
        _fetch_analyzer(conn, system_id, analyzer_id)
        rows = conn.execute(
            "SELECT * FROM trace_analysis_runs WHERE system_id = ? AND analyzer_id = ? "
            "ORDER BY id DESC",
            (system_id, analyzer_id),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


@router.get("/trace-analyzers/{analyzer_id}/runs/{run_id}", response_model=AnalysisRunOut)
def get_run(
    analyzer_id: int, run_id: int, system_id: int = Depends(get_system_id)
) -> AnalysisRunOut:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM trace_analysis_runs WHERE id = ? AND analyzer_id = ? AND system_id = ?",
            (run_id, analyzer_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "run not found")
    return _row_to_run(row)
