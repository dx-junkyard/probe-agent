"""Question Router API (Issue #286).

Exposes on-demand routing of a structured ``interview_qa`` question into
``human_only`` | ``system_researchable`` | ``hybrid`` via
``app/question_router.py``. This is intentionally separate from the
automatic routing Issue #286 wires into Inquiry answer generation
(``app/inquiry_answering.py``) -- routing a ``interview_qa`` question here
never triggers investigation and never touches
``interview_agent.py``'s dialogue turn (out of scope by design; see
CLAUDE.md's Issue #286 section).

Kept in its own module rather than growing ``routes/interview.py`` further,
matching the pattern Issue #284/#285 already established for their own
sub-scopes.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import InterviewQaOut
from ..question_router import RouteResult, route_question
from .interview import _qa_out

router = APIRouter()


def _qa_context(qa_row) -> str:
    parts = [f"question_category: {qa_row['question_category']}"]
    if qa_row["hypothesis"]:
        parts.append(f"hypothesis: {qa_row['hypothesis']}")
    if qa_row["answer_text"]:
        parts.append(f"existing (unconfirmed) answer: {qa_row['answer_text']}")
    return "; ".join(parts)


@router.post("/interview/qa/{qa_id}/route", response_model=InterviewQaOut)
def route_interview_qa(
    qa_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewQaOut:
    """Classify a structured Q&A question and persist the route decision.

    Fail-closed (Principle 6): a mock client, a non-reasoning model, an API
    failure, or invalid structured output all return 502 with no
    ``interview_qa`` row change -- only the failed ``intelligence_runs``
    audit row is persisted.

    probe-agent:
      role: API boundary for on-demand Question Router classification of a
        structured interview question
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: io
      state_effects: [database-read, database-write, external-api]
      probe_value: Verify a routed question persists route_category/route_run_id/knowledge_area and that LLM failure leaves the question unrouted with a failed audit row.
    """
    now = time.time()
    with get_conn() as conn:
        qa = conn.execute(
            "SELECT * FROM interview_qa WHERE id = ? AND system_id = ?",
            (qa_id, system_id),
        ).fetchone()
        if qa is None:
            raise HTTPException(status_code=404, detail="Question not found")
        session = conn.execute(
            "SELECT snapshot_id FROM interview_session WHERE id = ? AND system_id = ?",
            (qa["session_id"], system_id),
        ).fetchone()
        snapshot_id = session["snapshot_id"] if session else None

        config = LLMConfig.intelligence_from_env()
        try:
            client = create_llm_client(config)
            result = route_question(
                client, config, question_text=qa["question_text"], context=_qa_context(qa),
            )
        except LLMError as exc:
            result = RouteResult(
                provider=config.provider, model=config.model,
                is_mock=config.provider == "mock", error=str(exc),
            )

        completed_at = time.time()
        run_status = "failed" if result.error else "completed"
        run_cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 error_details, is_mock, started_at, completed_at)
            VALUES (?, ?, 'question_route', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                snapshot_id,
                result.provider,
                result.model,
                result.prompt_version,
                result.schema_version,
                run_status,
                result.error,
                1 if result.is_mock else 0,
                now,
                completed_at,
            ),
        )
        run_id = run_cur.lastrowid

        if result.error:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "question_route_failed",
                    "message": result.error,
                    "run_id": run_id,
                },
            )

        conn.execute(
            "UPDATE interview_qa "
            "SET route_category = ?, route_run_id = ?, knowledge_area = ? WHERE id = ?",
            (result.category, run_id, result.knowledge_area, qa_id),
        )
        row = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone()
        return _qa_out(row)
