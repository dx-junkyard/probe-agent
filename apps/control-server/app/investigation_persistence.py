"""Shared persistence helpers for Question Router / Investigation Agent runs.

Extracted from ``routes/interview_inquiry.py`` (Issue #286) so the normal
Q&A batch routing endpoint (``routes/question_router.py``'s
``POST /interview/sessions/{session_id}/qa/route-and-investigate``, Issue
#286 review fix -- Finding 1) can persist ``route_question``/``investigate``
audit rows exactly the same way the Inquiry flow does, instead of
duplicating the ``intelligence_runs``/``intelligence_run_evidence`` insert
logic.

Both helpers are pure ``(conn, ...) -> run_id`` writers: they never decide
anything (Principle 6/7 stay with the callers, which is where the
route/investigate results themselves are produced) and never call an LLM.
"""

from __future__ import annotations


def persist_route_run(conn, *, system_id: int, snapshot_id: int, route, now: float, completed_at: float) -> int:
    """Persist a ``question_router.RouteResult`` as its own audit row."""
    cur = conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             error_details, is_mock, started_at, completed_at)
        VALUES (?, ?, 'question_route', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)
        """,
        (
            system_id, snapshot_id, route.provider, route.model,
            route.prompt_version, route.schema_version,
            "failed" if route.error else "completed", route.error,
            1 if route.is_mock else 0, now, completed_at,
        ),
    )
    return cur.lastrowid


def persist_investigation_run(
    conn, *, system_id: int, snapshot_id: int, investigation, now: float, completed_at: float,
) -> int:
    """Persist an ``investigation_agent.InvestigationResult`` + its evidence rows.

    Every snippet actually read from the pinned snapshot is recorded here,
    regardless of whether the final answer cited it -- mirroring the
    interview dialogue's pass-1 evidence-audit pattern (Issue #137). Budget
    usage (files/chars/llm-calls/elapsed) is recorded on the run row itself
    for auditability.
    """
    cur = conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             error_details, is_mock, started_at, completed_at,
             budget_files_read, budget_chars_read, budget_llm_calls,
             budget_elapsed_seconds)
        VALUES (?, ?, 'investigation', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            system_id, snapshot_id, investigation.provider, investigation.model,
            investigation.prompt_version, investigation.schema_version,
            "failed" if investigation.error else "completed", investigation.error,
            1 if investigation.is_mock else 0, now, completed_at,
            investigation.files_read, investigation.chars_read, investigation.llm_calls,
            investigation.elapsed_seconds,
        ),
    )
    run_id = cur.lastrowid
    for snippet in investigation.read_snippets:
        conn.execute(
            """INSERT INTO intelligence_run_evidence
                (system_id, intelligence_run_id, path, start_line,
                 end_line, char_count, truncated, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, run_id, snippet.path, snippet.start_line,
                snippet.end_line, snippet.char_count,
                1 if snippet.truncated else 0, completed_at,
            ),
        )
    return run_id
