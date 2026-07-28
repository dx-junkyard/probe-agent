"""Question Router API (Issue #286; batch wiring added by a review fix, Finding 1).

Exposes on-demand routing of a structured ``interview_qa`` question into
``human_only`` | ``system_researchable`` | ``hybrid`` via
``app/question_router.py``, plus (Finding 1) a batch endpoint that also runs
the read-only Investigation Agent (``app/investigation_agent.py``) for
``system_researchable``/``hybrid`` questions -- wiring both into the NORMAL
Q&A flow, not just the Inquiry side-conversation
(``app/inquiry_answering.py``). Neither endpoint here ever touches
``interview_agent.py``'s dialogue turn or writes ``answer_text``/``status``/
``answered_by``: routing/investigating produces a finding to review, never an
auto-confirmed answer (#286 acceptance criterion; #284's "AI proposals never
auto-confirm").

Kept in its own module rather than growing ``routes/interview.py`` further,
matching the pattern Issue #284/#285 already established for their own
sub-scopes.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..interview_language import get_interview_language
from ..investigation_agent import investigate
from ..investigation_persistence import persist_investigation_run, persist_route_run
from ..llm import LLMConfig, LLMError, MockLLMClient, create_llm_client, is_reasoning_model
from ..models import (
    InterviewQaOut,
    InterviewQaRouteInvestigateBatchOut,
    InterviewQaRouteInvestigateBatchRequest,
    InterviewQaRouteInvestigateCountsOut,
    InterviewQaRouteInvestigateItemOut,
)
from ..question_router import RouteResult, route_question
from .interview import _get_session_or_404, _qa_out

router = APIRouter()

# Deterministic cap on questions processed per batch call (Principle 6):
# keeps one request bounded regardless of how many open questions a session
# has accumulated. Callers needing more simply call again -- the selection
# query always re-picks the oldest still-eligible questions first.
MAX_BATCH_QUESTIONS = 10


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

    # Phase 1 (DB lock held): read the question and its snapshot.
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

    # Phase 2 (DB lock released): the reasoning call. The LLM client opens its
    # own connection for System quota, so it must not run while this thread
    # holds one (see app/db.py).
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

    # Phase 3 (DB lock re-acquired): persist the audit run and the decision.
    with get_conn() as conn:
        run_id = persist_route_run(
            conn, system_id=system_id, snapshot_id=snapshot_id, route=result,
            now=now, completed_at=completed_at,
        )

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


def _reasoning_config_error(client, config: LLMConfig) -> Optional[str]:
    """The same fail-closed check ``route_question``/``investigate`` apply internally.

    Checked once, up front, for the batch endpoint so a mock/non-reasoning
    configuration 502s before any row is touched -- unlike the single-question
    endpoint above (which only ever acts on one question and so can afford to
    let ``route_question`` return an error result and persist exactly one
    failed audit row), a batch call must not leave a partially-processed mix
    of touched/untouched rows behind for a config problem that will fail
    every question identically.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return (
            "Question routing requires a configured reasoning model; "
            "mock/heuristic fallback is prohibited"
        )
    return None


def _investigation_json(investigation) -> dict:
    return {
        "status": investigation.status,
        "conclusion": investigation.conclusion,
        "key_points": list(investigation.key_points),
        "evidence": [
            {
                "path": e.path, "start_line": e.start_line,
                "end_line": e.end_line, "summary": e.summary,
            }
            for e in investigation.evidence
        ],
        "uncertainty": investigation.uncertainty,
        "confidence": investigation.confidence,
        "decision_question": investigation.decision_question,
    }


@router.post(
    "/interview/sessions/{session_id}/qa/route-and-investigate",
    response_model=InterviewQaRouteInvestigateBatchOut,
)
def route_and_investigate_qa(
    session_id: int,
    payload: Optional[InterviewQaRouteInvestigateBatchRequest] = None,
    system_id: int = Depends(get_system_id),
) -> InterviewQaRouteInvestigateBatchOut:
    """Batch-route and investigate open questions in the NORMAL Q&A flow.

    Finding 1 fix: before this endpoint, Question Router / Investigation
    Agent were only reachable inside the Inquiry side-conversation
    (``app/inquiry_answering.py``) or one question at a time via
    ``POST /interview/qa/{qa_id}/route`` (which never investigates) -- so an
    implementation-answerable question in the normal flow was still handed
    raw to the developer, and ``knowledge_area`` never got populated outside
    Inquiry (weakening Issue #291's out-of-area filtering).

    Selects up to ``MAX_BATCH_QUESTIONS`` current, open questions that are
    either unrouted, or routed ``system_researchable``/``hybrid`` with no
    investigation yet, oldest first. For each: routes it if unrouted
    (persisting a ``question_route`` ``intelligence_runs`` row exactly like
    the single-question endpoint, via the shared
    ``investigation_persistence`` helpers); if now (or already)
    ``system_researchable``/``hybrid`` and uninvestigated, investigates it
    against the session's pinned snapshot (persisting an ``investigation``
    row + its evidence, exactly like the Inquiry flow's
    ``_generate_and_store_answer``).

    Finding 4 fix: an optional ``{"qa_ids": [...]}`` body restricts the batch
    to just those question ids (still subject to the same eligibility
    filter as the unrestricted case) -- so a single Review Queue card's
    「わからない」 action investigates only that one question instead of
    always kicking off a whole-session batch. Omitting the body (or sending
    ``qa_ids: null``) keeps the prior all-eligible-questions behavior
    (backward compatible). A requested id that does not exist, or belongs to
    a different session/system, is reported as a per-question error in
    ``results`` -- it never fails or silently drops the rest of the request
    (same fail-closed-per-question policy as the rest of this endpoint).

    Fail-closed per question, never per batch: a routing failure persists the
    failed audit row and leaves that question unrouted; an investigation
    failure (``status="failed"``) persists the failed audit row and leaves
    ``investigation_json``/``investigation_run_id`` NULL. Either way the loop
    continues with the next question -- one bad question never aborts the
    rest of the batch. The one exception is the reasoning-model
    configuration itself (mock provider / non-reasoning model): checked once
    up front, before touching any row, so a bad configuration 502s cleanly
    with zero row changes instead of failing every question individually.

    This endpoint NEVER writes ``answer_text``/``status``/``answered_by`` --
    routing and investigation only ever populate ``route_category``/
    ``knowledge_area``/``investigation_json``/``investigation_run_id``, never
    the answer itself (#286 acceptance criterion: investigation completing
    must never auto-confirm the original answer; #284: AI proposals never
    auto-confirm).

    probe-agent:
      role: API boundary that batches Question Router classification and
        read-only Investigation Agent research for the normal Q&A flow
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: io
      state_effects: [database-read, database-write, external-api]
      probe_value: Verify a batch call routes+investigates eligible questions, fails closed per question without aborting the batch, never writes answer_text/status, and 502s with zero row changes on a mock/non-reasoning configuration.
    """
    now = time.time()
    requested_qa_ids = payload.qa_ids if payload is not None else None
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        snapshot_id = session["snapshot_id"]

        config = LLMConfig.intelligence_from_env()
        try:
            client = create_llm_client(config)
        except LLMError as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "question_route_failed", "message": str(exc)},
            )
        config_error = _reasoning_config_error(client, config)
        if config_error:
            raise HTTPException(
                status_code=502,
                detail={"code": "question_route_failed", "message": config_error},
            )

        snapshot_row = conn.execute(
            "SELECT repo_path, commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
        repo_path = snapshot_row["repo_path"] if snapshot_row else None
        commit_sha = snapshot_row["commit_sha"] if snapshot_row else None

        # Finding 4: when qa_ids is given, restrict eligibility to that
        # explicit id set (deduped, order preserved) on top of the same
        # eligibility filter used for the unrestricted case -- never a
        # looser or different rule for the scoped call. An id that does not
        # resolve to a row in THIS session/system is reported as a
        # per-question error below rather than silently ignored or allowed
        # to fail the whole request.
        missing_qa_id_errors: List[InterviewQaRouteInvestigateItemOut] = []
        id_filter_sql = ""
        id_filter_params: tuple = ()
        if requested_qa_ids is not None:
            seen_requested: set = set()
            ordered_requested: List[int] = []
            for qid in requested_qa_ids:
                if qid not in seen_requested:
                    seen_requested.add(qid)
                    ordered_requested.append(qid)
            found_rows = conn.execute(
                f"""SELECT id FROM interview_qa
                    WHERE system_id = ? AND session_id = ?
                      AND id IN ({",".join("?" for _ in ordered_requested)})""",
                (system_id, session_id, *ordered_requested),
            ).fetchall() if ordered_requested else []
            found_ids = {r["id"] for r in found_rows}
            for qid in ordered_requested:
                if qid not in found_ids:
                    missing_qa_id_errors.append(InterviewQaRouteInvestigateItemOut(
                        qa_id=qid, route_category=None, knowledge_area=None,
                        investigation_status=None,
                        error="Question not found in this session",
                    ))
            if found_ids:
                id_filter_sql = f" AND id IN ({','.join('?' for _ in found_ids)})"
                id_filter_params = tuple(found_ids)
            else:
                # Nothing to select at all (every requested id was missing);
                # use an always-false clause so the query below returns [].
                id_filter_sql = " AND 1 = 0"
                id_filter_params = ()

        eligible = conn.execute(
            f"""
            SELECT * FROM interview_qa
            WHERE session_id = ? AND system_id = ?
              AND superseded_by_id IS NULL AND status = 'open'
              AND (
                route_category IS NULL
                OR (route_category IN ('system_researchable', 'hybrid')
                    AND investigation_run_id IS NULL)
              )
              {id_filter_sql}
            ORDER BY id
            """,
            (session_id, system_id, *id_filter_params),
        ).fetchall()
        selected = eligible[:MAX_BATCH_QUESTIONS]
        skipped_cap = max(0, len(eligible) - len(selected))

    # Phase 2/3 (DB lock released): up to MAX_BATCH_QUESTIONS questions, each
    # costing one routing call plus up to InvestigationBudget.max_llm_calls
    # investigation calls. Holding the process-wide DB lock across that many
    # external round trips would stall every other request for the whole
    # batch, so each question's reasoning runs unlocked and only its own
    # persistence re-acquires a connection (see app/db.py). Per-question
    # persistence also preserves the existing "fail closed per question
    # without aborting the batch" contract: a later failure never discards an
    # earlier question's recorded result.

    # Same language resolution as the Inquiry flow
    # (inquiry_answering.generate_inquiry_answer) so batch-routed
    # questions and Inquiry answers never diverge on INTERVIEW_LANGUAGE.
    language = get_interview_language()

    results: List[InterviewQaRouteInvestigateItemOut] = list(missing_qa_id_errors)
    counts = InterviewQaRouteInvestigateCountsOut(
        skipped_cap=skipped_cap, failed=len(missing_qa_id_errors),
    )

    for qa in selected:
        qa_id = qa["id"]
        route_category: Optional[str] = qa["route_category"]
        knowledge_area = qa["knowledge_area"] if "knowledge_area" in qa.keys() else None
        search_keywords: Optional[List[str]] = None
        research_focus: Optional[str] = None

        if route_category is None:
            route_result = route_question(
                client, config, question_text=qa["question_text"], context=_qa_context(qa),
                language=language,
            )
            route_completed_at = time.time()
            with get_conn() as conn:
                route_run_id = persist_route_run(
                    conn, system_id=system_id, snapshot_id=snapshot_id, route=route_result,
                    now=now, completed_at=route_completed_at,
                )
                if not route_result.error:
                    conn.execute(
                        "UPDATE interview_qa "
                        "SET route_category = ?, route_run_id = ?, knowledge_area = ? WHERE id = ?",
                        (route_result.category, route_run_id, route_result.knowledge_area, qa_id),
                    )
            if route_result.error:
                counts.failed += 1
                results.append(InterviewQaRouteInvestigateItemOut(
                    qa_id=qa_id, route_category=None, knowledge_area=None,
                    investigation_status=None, error=route_result.error,
                ))
                continue

            counts.routed += 1
            route_category = route_result.category
            knowledge_area = route_result.knowledge_area
            search_keywords = route_result.search_keywords
            research_focus = route_result.research_focus

        investigation_status: Optional[str] = None
        item_error: Optional[str] = None

        if route_category in ("system_researchable", "hybrid"):
            if not repo_path or not commit_sha:
                item_error = "Investigation requires a pinned snapshot repo_path/commit_sha"
            else:
                # No ``conn``: investigate() opens its own short-lived
                # connection for runtime facts and releases it before its
                # own reasoning call.
                investigation = investigate(
                    client, config,
                    repo_path=repo_path, commit_sha=commit_sha,
                    question=qa["question_text"],
                    research_focus=research_focus,
                    search_keywords=search_keywords,
                    hybrid_decision_question=(route_category == "hybrid"),
                    language=language,
                    system_id=system_id, snapshot_id=snapshot_id,
                )
                investigation_completed_at = time.time()
                with get_conn() as conn:
                    investigation_run_id = persist_investigation_run(
                        conn, system_id=system_id, snapshot_id=snapshot_id,
                        investigation=investigation, now=now,
                        completed_at=investigation_completed_at,
                    )
                    if investigation.status != "failed":
                        conn.execute(
                            "UPDATE interview_qa "
                            "SET investigation_json = ?, investigation_run_id = ? WHERE id = ?",
                            (
                                json.dumps(_investigation_json(investigation), ensure_ascii=False),
                                investigation_run_id,
                                qa_id,
                            ),
                        )
                investigation_status = investigation.status
                if investigation.status == "failed":
                    counts.failed += 1
                    item_error = investigation.error
                else:
                    counts.investigated += 1

        results.append(InterviewQaRouteInvestigateItemOut(
            qa_id=qa_id, route_category=route_category, knowledge_area=knowledge_area,
            investigation_status=investigation_status, error=item_error,
        ))

    return InterviewQaRouteInvestigateBatchOut(
        session_id=session_id, system_id=system_id, results=results, counts=counts,
    )
