"""System-understanding interview API (Issues #67, #68, #69, #70, #71).

Issue #67 — persistence + contract layer for sessions, messages, proposals.
Issue #68 — deterministic, no-LLM context-pack builder.
Issue #69 — reasoning-model dialogue endpoint that produces structured
assistant turns and per-symbol combined proposals (docstring metadata +
probe plan), validated against #54 vocabulary and the safety denylist.
Issue #70 — per-item approval gate with manual decision record.
Issue #71 — worktree materialization of approved set into reviewable diff.

probe-agent:
  role: Interactive system-understanding interview API
  capability: interactive-system-understanding
  element_type: element
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify that interview proposals use reasoning model and approval records decision_method as manual.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from ..auth import get_system_id
from ..db import get_conn
from ..interview_context import build_interview_context
from ..interview_agent import (
    EVIDENCE_PROMPT_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    PROMPT_VERSION as INTERVIEW_PROMPT_VERSION,
    SCHEMA_VERSION as INTERVIEW_SCHEMA_VERSION,
    EvidenceSelectionResult,
    InterviewTurnResult,
    generate_interview_turn,
    select_evidence_targets,
)
from ..interview_evidence import (
    EvidenceConfigError,
    EvidenceReadError,
    read_evidence_snippets,
)
from ..interview_language import (
    INTERVIEW_MESSAGES,
    get_interview_language,
    interview_message,
    resolve_message_language,
)
from ..llm import LLMConfig, LLMError, create_llm_client, is_reasoning_model
from ..runtime_reality import (
    RuntimeCheckInputItem,
    RuntimeRealityCheckResult,
    aggregate_component_facts,
    declared_block,
    generate_runtime_reality_check,
    window_days as runtime_window_days,
)
from ..understanding_diff import (
    DEFAULT_REVISION_LIMIT,
    diff_understanding,
    revision_limit,
)
from ..models import (
    InterviewApprovedItemOut,
    InterviewApprovedSetOut,
    InterviewConfirmUnderstandingRequest,
    InterviewContextPack,
    InterviewDialogueProposalOut,
    InterviewDialogueTurnOut,
    InterviewDialogueTurnRequest,
    InterviewMaterializeOut,
    InterviewMaterializeRequest,
    InterviewMessageCreate,
    InterviewMessageOut,
    InterviewProposalApproveRequest,
    InterviewProposalDecisionOut,
    InterviewProposalEditRequest,
    InterviewProposalMetadataBlock,
    InterviewProposalOut,
    InterviewProposalProbePlan,
    InterviewProposalRejectRequest,
    InterviewSnapshotRebaseOut,
    InterviewSnapshotRebaseRequest,
    InterviewProposalsCreate,
    InterviewQaActorRequest,
    InterviewQaAnswerOut,
    InterviewQaAnswerRequest,
    InterviewQaCreate,
    InterviewQaEvidenceRefOut,
    InterviewQaListOut,
    InterviewQaOut,
    InterviewSessionCreate,
    InterviewSessionDetailOut,
    InterviewSessionOut,
    InterviewStructuredQuestion,
    IntelligenceRunEvidenceListOut,
    IntelligenceRunEvidenceOut,
    IntelligenceRunOut,
    RuntimeRealityCheckRunOut,
    RuntimeRealityFactsOut,
    UnderstandingDiffOut,
    UnderstandingDiffSectionOut,
    UnderstandingRevisionListOut,
    UnderstandingRevisionOut,
)
from ..probe_planner import check_denylist

STAGE_ORDER = [
    "understanding_initialized",
    "purpose_confirmation",
    "capability_confirmation",
    "element_classification",
    "api_boundary_mapping",
    "probe_flow_selection",
    "proposal_generation",
]


def _component_id_for(qualified_name: str) -> str:
    """Deterministic component_id for an interview-authored probe.

    Single definition shared by worktree materialization (Issue #71), which
    stamps this id onto the generated ``@probe`` decorator, and the Runtime
    Reality Check (Issue #135), which aggregates traces by the same id. If
    the derivations drifted, runtime facts would silently match zero traces.
    """
    return qualified_name.replace(".", "_")


def _advance_stage(current_stage: str, target: str) -> str:
    """Advance to target stage if it's ahead of current."""
    try:
        current_idx = STAGE_ORDER.index(current_stage)
        target_idx = STAGE_ORDER.index(target)
    except ValueError:
        return current_stage
    return target if target_idx > current_idx else current_stage


def _review_history(rows) -> List[dict]:
    """Return conversation evidence only for an understanding review.

    Confirmation and snapshot events are workflow-control/audit records, not
    developer-provided evidence. Older sessions wrote the confirmation text as
    a user message, so filter every localized legacy value as well.
    """
    confirmation_messages = set(
        INTERVIEW_MESSAGES["confirm_understanding_message"].values()
    )
    return [
        {"role": row["role"], "content": row["content"]}
        for row in rows
        if row["role"] in ("user", "assistant")
        and row["content"] not in confirmation_messages
    ]


def _load_qa_pairs(
    conn, session_id: int, system_id: int
) -> tuple[Optional[List[dict]], List[dict]]:
    """Fetch and shape the session's answered/unconfirmed interview_qa rows.

    Single source of the Issue #129/#142 fetch+shaping rule, shared by the
    conversational interview-turn route and the understanding-review rebuild
    (Issue #263) so an answer given only through the Q&A panel reaches the
    review the same way a conversational answer already does. Returns
    ``(answered_qa_pairs, unconfirmed_qa_pairs)``: ``answered_qa_pairs`` is
    ``None`` when there are none (matching the existing prompt-injection
    convention); ``unconfirmed_qa_pairs`` is always a list so callers may
    merge additional turn-local entries (e.g. the current turn's "I don't
    know" answer) before use.
    """
    answered_qa_rows = conn.execute(
        """SELECT question_text, answer_text FROM interview_qa
           WHERE session_id = ? AND system_id = ?
             AND superseded_by_id IS NULL AND status = 'answered'
           ORDER BY id""",
        (session_id, system_id),
    ).fetchall()
    answered_qa_pairs = [
        {"question": r["question_text"], "answer": r["answer_text"]}
        for r in answered_qa_rows
    ] or None

    unconfirmed_qa_rows = conn.execute(
        """SELECT question_text, answer_text FROM interview_qa
           WHERE session_id = ? AND system_id = ?
             AND superseded_by_id IS NULL AND status = 'unconfirmed'
           ORDER BY id""",
        (session_id, system_id),
    ).fetchall()
    unconfirmed_qa_pairs = [
        {"question": r["question_text"], "answer": r["answer_text"]}
        for r in unconfirmed_qa_rows
    ]
    return answered_qa_pairs, unconfirmed_qa_pairs


def _question_entry(question) -> dict:
    """Normalize a turn question (structured object or legacy string) into
    the open_questions JSON entry shape."""
    if isinstance(question, str):
        return {"question": question, "category": "followup", "priority": "medium"}
    entry = {
        "question": question.question_text,
        "category": "followup",
        "priority": "medium",
    }
    if question.hypothesis:
        entry["hypothesis"] = question.hypothesis
    if question.evidence_refs:
        entry["evidence_refs"] = [r.model_dump() for r in question.evidence_refs]
    if question.answer_options:
        entry["answer_options"] = list(question.answer_options)
    return entry


def _question_out(question) -> InterviewStructuredQuestion:
    if isinstance(question, str):
        return InterviewStructuredQuestion(question_text=question)
    return question

router = APIRouter()


def _session_out(row) -> InterviewSessionOut:
    import json as _json
    return InterviewSessionOut(
        id=row["id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        title=row["title"],
        focus=row["focus"],
        status=row["status"],
        stage=row["stage"] if row["stage"] else "understanding_initialized",
        current_understanding=_json.loads(row["current_understanding"]) if row["current_understanding"] else None,
        gap_analysis=_json.loads(row["gap_analysis"]) if row["gap_analysis"] else None,
        open_questions=_json.loads(row["open_questions"]) if row["open_questions"] else None,
        user_intent=row["user_intent"],
        last_error=row["last_error"] if "last_error" in row.keys() else None,
        understanding_confirmed_at=(
            row["understanding_confirmed_at"]
            if "understanding_confirmed_at" in row.keys() else None
        ),
        understanding_confirmed_by=(
            row["understanding_confirmed_by"]
            if "understanding_confirmed_by" in row.keys() else None
        ),
        answers_revised_at=(
            row["answers_revised_at"] if "answers_revised_at" in row.keys() else None
        ),
        materialization_diff=row["materialization_diff"],
        materialization_ref=row["materialization_ref"],
        materialized_at=row["materialized_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _message_out(row) -> InterviewMessageOut:
    return InterviewMessageOut(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        intelligence_run_id=row["intelligence_run_id"],
        created_at=row["created_at"],
    )


def _intelligence_run_out(row) -> IntelligenceRunOut:
    return IntelligenceRunOut(
        id=row["id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        run_type=row["run_type"],
        provider=row["provider"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        decision_method=row["decision_method"],
        status=row["status"],
        error_details=row["error_details"],
        is_mock=bool(row["is_mock"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


# --- Structured Interview Q&A (Issue #129) ----------------------------------


def _qa_out(row) -> InterviewQaOut:
    return InterviewQaOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        question_text=row["question_text"],
        question_category=row["question_category"],
        question_source=row["question_source"],
        hypothesis=row["hypothesis"],
        evidence_refs=(
            [InterviewQaEvidenceRefOut(**e) for e in json.loads(row["evidence_refs"])]
            if row["evidence_refs"] else []
        ),
        runtime_evidence=(
            json.loads(row["runtime_evidence"])
            if ("runtime_evidence" in row.keys() and row["runtime_evidence"])
            else None
        ),
        answer_text=row["answer_text"],
        status=row["status"],
        answered_by=row["answered_by"],
        superseded_by_id=row["superseded_by_id"],
        created_at=row["created_at"],
        answered_at=row["answered_at"],
    )


# Deterministic priority mapping (Principle 6): purpose/capability questions
# gate the rest of the interview, so they are treated as high priority.
_HIGH_PRIORITY_QA_CATEGORIES = frozenset({"purpose", "capability"})


def _get_qa_or_404(conn, qa_id: int, session_id: int, system_id: int):
    row = conn.execute(
        """SELECT * FROM interview_qa
           WHERE id = ? AND session_id = ? AND system_id = ?""",
        (qa_id, session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return row


def _insert_qa_row(
    conn,
    session_id: int,
    system_id: int,
    *,
    question_text: str,
    question_category: str,
    question_source: str,
    hypothesis: Optional[str],
    evidence_refs: List[InterviewQaEvidenceRefOut],
    now: float,
    answer_text: Optional[str] = None,
    status: str = "open",
    answered_by: Optional[str] = None,
    answered_at: Optional[float] = None,
    runtime_evidence: Optional[dict] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO interview_qa
            (session_id, system_id, question_text, question_category,
             question_source, hypothesis, evidence_refs, runtime_evidence,
             answer_text, status, answered_by, created_at, answered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            system_id,
            question_text,
            question_category,
            question_source,
            hypothesis,
            json.dumps([e.model_dump() for e in evidence_refs], ensure_ascii=False)
            if evidence_refs else None,
            json.dumps(runtime_evidence, ensure_ascii=False) if runtime_evidence else None,
            answer_text,
            status,
            answered_by,
            now,
            answered_at,
        ),
    )
    return cur.lastrowid


def _proposal_out(conn, row) -> InterviewProposalOut:
    run_row = conn.execute(
        "SELECT * FROM intelligence_runs WHERE id = ?",
        (row["intelligence_run_id"],),
    ).fetchone()
    return InterviewProposalOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        message_id=row["message_id"],
        intelligence_run_id=row["intelligence_run_id"],
        symbol_id=row["symbol_id"],
        path=row["path"],
        qualified_name=row["qualified_name"],
        metadata=InterviewProposalMetadataBlock(
            role=row["md_role"],
            capability=row["md_capability"],
            system_purpose=row["md_system_purpose"],
            probe_value=row["md_probe_value"],
            element_type=row["md_element_type"],
            operation_kind=row["md_operation_kind"],
            consumers=json.loads(row["md_consumers"] or "[]"),
            state_effects=json.loads(row["md_state_effects"] or "[]"),
        ),
        probe_plan=InterviewProposalProbePlan(
            feature_id=row["feature_id"],
            objective=row["objective"],
            reason=row["probe_reason"],
            recommended_mode=row["recommended_mode"],
            side_effect_risk=row["side_effect_risk"],
            replayability=row["replayability"],
        ),
        graph_node_id=row["graph_node_id"] if "graph_node_id" in row.keys() else None,
        capability_name=row["capability_name"] if "capability_name" in row.keys() else None,
        evidence_summary=row["evidence_summary"] if "evidence_summary" in row.keys() else None,
        proposal_confidence=row["proposal_confidence"] if "proposal_confidence" in row.keys() else None,
        decision_method=row["decision_method"],
        approval_state=row["approval_state"],
        is_mock=bool(row["is_mock"]),
        intelligence_run=_intelligence_run_out(run_row) if run_row else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


@router.get("/interview/sessions", response_model=List[InterviewSessionOut])
def list_interview_sessions(
    system_id: int = Depends(get_system_id),
) -> List[InterviewSessionOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM interview_session WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        return [_session_out(r) for r in rows]


@router.post(
    "/interview/sessions",
    response_model=InterviewSessionOut,
    status_code=201,
)
def create_interview_session(
    payload: InterviewSessionCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewSessionOut:
    """Create a new interview session for a system.

    probe-agent:
      role: API boundary for creating a new interview session
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify session creation persists correct fields and returns valid session
    """
    now = time.time()
    with get_conn() as conn:
        snapshot = conn.execute(
            "SELECT id FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (payload.snapshot_id, system_id),
        ).fetchone()
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail="Snapshot not found for this system",
            )
        cur = conn.execute(
            """
            INSERT INTO interview_session
                (system_id, snapshot_id, title, focus, status, stage, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'open', 'understanding_initialized', ?, ?)
            """,
            (system_id, payload.snapshot_id, payload.title, payload.focus, now, now),
        )
        row = _get_session_or_404(conn, cur.lastrowid, system_id)
        return _session_out(row)


@router.get(
    "/interview/sessions/{session_id}",
    response_model=InterviewSessionDetailOut,
)
def get_interview_session(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewSessionDetailOut:
    with get_conn() as conn:
        row = _get_session_or_404(conn, session_id, system_id)
        snapshot_row = conn.execute(
            "SELECT commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (row["snapshot_id"], system_id),
        ).fetchone()
        message_rows = conn.execute(
            "SELECT * FROM interview_message WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        proposal_rows = conn.execute(
            "SELECT * FROM interview_proposal WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        session_fields = _session_out(row).model_dump()
        session_fields["snapshot_commit_sha"] = (
            snapshot_row["commit_sha"] if snapshot_row else None
        )
        return InterviewSessionDetailOut(
            **session_fields,
            messages=[_message_out(m) for m in message_rows],
            proposals=[_proposal_out(conn, p) for p in proposal_rows],
        )


def _latest_ready_snapshot_id(conn, system_id: int) -> Optional[int]:
    row = conn.execute(
        """SELECT id FROM repository_snapshots
           WHERE system_id = ? AND status = 'ready'
           ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()
    return row["id"] if row else None


def _symbol_anchor(conn, system_id: int, snapshot_id: int, path: str, qualified_name: str):
    return conn.execute(
        """SELECT id, symbol_source_hash, symbol_body_hash, start_line, end_line
           FROM code_symbols
           WHERE system_id = ? AND snapshot_id = ? AND path = ? AND qualified_name = ?
           ORDER BY id LIMIT 1""",
        (system_id, snapshot_id, path, qualified_name),
    ).fetchone()


@router.post(
    "/interview/sessions/{session_id}/rebase-snapshot",
    response_model=InterviewSnapshotRebaseOut,
)
def rebase_interview_snapshot(
    session_id: int,
    payload: InterviewSnapshotRebaseRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewSnapshotRebaseOut:
    """Rebase an existing Interview session onto a newer ready snapshot.

    This is deterministic and conservative: previously approved/edited
    proposals are preserved only when the same path + qualified name exists in
    the target snapshot and its source hash is unchanged. Changed or missing
    targets are moved to ``needs_review``, which excludes them from
    materialization until a human approves or edits them again.
    """
    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        from_snapshot_id = session["snapshot_id"]
        target_snapshot_id = payload.target_snapshot_id or _latest_ready_snapshot_id(conn, system_id)
        if target_snapshot_id is None:
            raise HTTPException(status_code=404, detail="No ready target snapshot found")

        target = conn.execute(
            """SELECT id FROM repository_snapshots
               WHERE id = ? AND system_id = ? AND status = 'ready'""",
            (target_snapshot_id, system_id),
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="Target ready snapshot not found")

        if target_snapshot_id == from_snapshot_id:
            return InterviewSnapshotRebaseOut(
                session_id=session_id,
                system_id=system_id,
                from_snapshot_id=from_snapshot_id,
                to_snapshot_id=target_snapshot_id,
                message="Session is already pinned to the target snapshot.",
                session=_session_out(session),
            )

        proposal_rows = conn.execute(
            "SELECT * FROM interview_proposal WHERE session_id = ? AND system_id = ? ORDER BY id",
            (session_id, system_id),
        ).fetchall()

        preserved = 0
        needs_review = 0
        missing_source = 0
        changed_source = 0
        details: List[dict] = []

        for proposal in proposal_rows:
            old_anchor = _symbol_anchor(
                conn, system_id, from_snapshot_id,
                proposal["path"], proposal["qualified_name"],
            )
            new_anchor = _symbol_anchor(
                conn, system_id, target_snapshot_id,
                proposal["path"], proposal["qualified_name"],
            )

            new_state = proposal["approval_state"]
            reason = "preserved"
            new_symbol_id = new_anchor["id"] if new_anchor else None

            if new_anchor is None:
                missing_source += 1
                reason = "missing_source"
                if proposal["approval_state"] in ("approved", "edited", "needs_review"):
                    new_state = "needs_review"
                    needs_review += 1
            elif proposal["approval_state"] in ("approved", "edited", "needs_review"):
                old_hash = old_anchor["symbol_source_hash"] if old_anchor else None
                new_hash = new_anchor["symbol_source_hash"]
                if old_hash and new_hash and old_hash == new_hash:
                    preserved += 1
                    reason = "unchanged_source"
                    new_state = proposal["approval_state"]
                else:
                    changed_source += 1
                    needs_review += 1
                    reason = "changed_source"
                    new_state = "needs_review"
            else:
                # Pending/rejected proposals are not materialization-eligible.
                # Move their snapshot anchor forward when the symbol still
                # resolves, but do not create new review obligations.
                reason = "unreviewed_or_rejected"

            conn.execute(
                """UPDATE interview_proposal
                   SET snapshot_id = ?, symbol_id = ?, approval_state = ?, updated_at = ?
                   WHERE id = ? AND system_id = ?""",
                (
                    target_snapshot_id, new_symbol_id, new_state, now,
                    proposal["id"], system_id,
                ),
            )
            details.append({
                "proposal_id": proposal["id"],
                "path": proposal["path"],
                "qualified_name": proposal["qualified_name"],
                "previous_state": proposal["approval_state"],
                "new_state": new_state,
                "reason": reason,
            })

        conn.execute(
            """UPDATE interview_session
               SET snapshot_id = ?, materialization_diff = NULL,
                   materialization_ref = NULL, materialized_at = NULL,
                   updated_at = ?
               WHERE id = ? AND system_id = ?""",
            (target_snapshot_id, now, session_id, system_id),
        )
        conn.execute(
            """INSERT INTO interview_snapshot_rebase
                (session_id, system_id, from_snapshot_id, to_snapshot_id, actor,
                 proposals_preserved, proposals_marked_needs_review,
                 proposals_missing_source, proposals_changed_source,
                 details_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, system_id, from_snapshot_id, target_snapshot_id,
                payload.actor, preserved, needs_review, missing_source,
                changed_source, json.dumps(details, ensure_ascii=False), now,
            ),
        )
        conn.execute(
            """INSERT INTO interview_message
                (session_id, system_id, role, content, created_at)
               VALUES (?, ?, 'system', ?, ?)""",
            (
                session_id,
                system_id,
                (
                    f"Interview snapshot updated from #{from_snapshot_id} to "
                    f"#{target_snapshot_id}. Preserved {preserved} reviewed "
                    f"proposal(s); marked {needs_review} for re-review."
                ),
                now,
            ),
        )

        row = _get_session_or_404(conn, session_id, system_id)
        return InterviewSnapshotRebaseOut(
            session_id=session_id,
            system_id=system_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=target_snapshot_id,
            proposals_preserved=preserved,
            proposals_marked_needs_review=needs_review,
            proposals_missing_source=missing_source,
            proposals_changed_source=changed_source,
            message=(
                f"Updated session to snapshot #{target_snapshot_id}; "
                f"{needs_review} proposal(s) require re-review."
            ),
            session=_session_out(row),
        )


@router.get(
    "/interview/sessions/{session_id}/context-pack",
    response_model=InterviewContextPack,
)
def get_interview_context_pack(
    session_id: int,
    system_id: int = Depends(get_system_id),
    budget: Optional[int] = Query(default=None, ge=1000, le=500_000),
) -> InterviewContextPack:
    """Deterministic, no-LLM context pack for a pinned interview session.

    Assembles symbols, entrypoints, and existing metadata from the session's
    pinned snapshot and flags which items are classified vs. unclassified,
    all within an explicit LLM context budget (Issue #68).

    probe-agent:
      role: API boundary for retrieving the deterministic context pack
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify context pack assembly respects budget and returns correct classification state
    """
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        return build_interview_context(
            conn, system_id, session["snapshot_id"], budget_chars=budget,
        )


@router.post(
    "/interview/sessions/{session_id}/messages",
    response_model=InterviewMessageOut,
    status_code=201,
)
def create_interview_message(
    session_id: int,
    payload: InterviewMessageCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewMessageOut:
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        if payload.intelligence_run_id is not None:
            run = conn.execute(
                "SELECT id FROM intelligence_runs WHERE id = ? AND system_id = ?",
                (payload.intelligence_run_id, system_id),
            ).fetchone()
            if run is None:
                raise HTTPException(
                    status_code=404,
                    detail="Referenced reasoning run not found for this system",
                )
        cur = conn.execute(
            """
            INSERT INTO interview_message
                (session_id, system_id, role, content, intelligence_run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                system_id,
                payload.role,
                payload.content,
                payload.intelligence_run_id,
                now,
            ),
        )
        conn.execute(
            "UPDATE interview_session SET updated_at = ? WHERE id = ? AND system_id = ?",
            (now, session_id, system_id),
        )
        row = conn.execute(
            "SELECT * FROM interview_message WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _message_out(row)


@router.get(
    "/interview/sessions/{session_id}/proposals",
    response_model=List[InterviewProposalOut],
)
def list_interview_proposals(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> List[InterviewProposalOut]:
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            "SELECT * FROM interview_proposal WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [_proposal_out(conn, r) for r in rows]


@router.post(
    "/interview/sessions/{session_id}/proposals",
    response_model=List[InterviewProposalOut],
    status_code=201,
)
def create_interview_proposals(
    session_id: int,
    payload: InterviewProposalsCreate,
    system_id: int = Depends(get_system_id),
) -> List[InterviewProposalOut]:
    """Persist a batch of combined per-symbol proposals.

    No LLM call happens here; the caller (a later dialogue issue, or a test)
    supplies the already-produced payload and the audit metadata of the
    reasoning run that produced it. The audit metadata is stored as one
    ``intelligence_runs`` row that every proposal in the batch links to.

    probe-agent:
      role: API boundary for persisting interview proposals
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify proposals are persisted with correct intelligence run linkage and stage/confirmation guards
    """
    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        current_stage = session["stage"] or "understanding_initialized"
        if current_stage != "proposal_generation":
            raise HTTPException(
                status_code=422,
                detail=f"Proposals can only be created in proposal_generation stage (current: {current_stage})",
            )
        # Same understanding gate as dialogue-turn (Issues #83, #123): the
        # stage alone (reachable via advance-stage) must not unlock proposal
        # persistence without a built understanding or the developer's
        # explicit confirmation of the interview context.
        understanding_confirmed = (
            "understanding_confirmed_at" in session.keys()
            and session["understanding_confirmed_at"] is not None
        )
        if session["current_understanding"] is None and not understanding_confirmed:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Proposals are locked until understanding is confirmed: "
                    "build an understanding or confirm the interview context first"
                ),
            )
        snapshot_id = session["snapshot_id"]
        message_id = payload.message_id
        if message_id is not None:
            message = conn.execute(
                """SELECT id FROM interview_message
                   WHERE id = ? AND session_id = ? AND system_id = ?""",
                (message_id, session_id, system_id),
            ).fetchone()
            if message is None:
                raise HTTPException(
                    status_code=404,
                    detail="Referenced message not found for this session",
                )
        conn.execute("BEGIN")
        try:
            run_cur = conn.execute(
                """
                INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method, status,
                     is_mock, started_at, completed_at)
                VALUES (?, ?, 'interview_proposal', ?, ?, ?, ?, 'reasoning_llm',
                        'completed', ?, ?, ?)
                """,
                (
                    system_id,
                    snapshot_id,
                    payload.audit.provider,
                    payload.audit.model,
                    payload.audit.prompt_version,
                    payload.audit.schema_version,
                    1 if payload.audit.is_mock else 0,
                    now,
                    now,
                ),
            )
            run_id = run_cur.lastrowid
            new_ids: List[int] = []
            for item in payload.proposals:
                cur = conn.execute(
                    """
                    INSERT INTO interview_proposal
                        (session_id, system_id, snapshot_id, message_id,
                         intelligence_run_id, symbol_id, path, qualified_name,
                         md_role, md_capability, md_system_purpose, md_probe_value,
                         md_element_type, md_operation_kind, md_consumers,
                         md_state_effects, feature_id, objective, probe_reason,
                         recommended_mode, side_effect_risk, replayability,
                         graph_node_id, capability_name, evidence_summary,
                         proposal_confidence,
                         decision_method, approval_state, is_mock,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, 'reasoning_llm', 'proposed', ?, ?, ?)
                    """,
                    (
                        session_id,
                        system_id,
                        snapshot_id,
                        message_id,
                        run_id,
                        item.symbol_id,
                        item.path,
                        item.qualified_name,
                        item.metadata.role,
                        item.metadata.capability,
                        item.metadata.system_purpose,
                        item.metadata.probe_value,
                        item.metadata.element_type,
                        item.metadata.operation_kind,
                        json.dumps(item.metadata.consumers, ensure_ascii=False),
                        json.dumps(item.metadata.state_effects, ensure_ascii=False),
                        item.probe_plan.feature_id,
                        item.probe_plan.objective,
                        item.probe_plan.reason,
                        item.probe_plan.recommended_mode,
                        item.probe_plan.side_effect_risk,
                        item.probe_plan.replayability,
                        item.graph_node_id,
                        item.capability_name,
                        item.evidence_summary,
                        item.proposal_confidence,
                        1 if payload.audit.is_mock else 0,
                        now,
                        now,
                    ),
                )
                new_ids.append(cur.lastrowid)
            conn.execute(
                "UPDATE interview_session SET updated_at = ? WHERE id = ? AND system_id = ?",
                (now, session_id, system_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        rows = conn.execute(
            "SELECT * FROM interview_proposal WHERE id IN (%s) ORDER BY id"
            % ",".join("?" for _ in new_ids),
            new_ids,
        ).fetchall()
        return [_proposal_out(conn, r) for r in rows]


# --- Dialogue Turn (Issue #69) -----------------------------------------------


@router.post(
    "/interview/sessions/{session_id}/dialogue-turn",
    response_model=InterviewDialogueTurnOut,
)
def interview_dialogue_turn(
    session_id: int,
    payload: InterviewDialogueTurnRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewDialogueTurnOut:
    """Generate a reasoning-model dialogue turn for the interview (Issue #69).

    1. Builds a deterministic context pack from the session's pinned snapshot.
    2. Assembles conversation history from stored messages.
    3. Calls the reasoning model for a structured response.
    4. Validates proposals against #54 vocabulary and the safety denylist.
    5. On success: persists user message, assistant message, intelligence run,
       and any proposals. On failure: persists the failure and returns error.

    probe-agent:
      role: API boundary for generating a reasoning-model dialogue turn
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: orchestration
      state_effects: [database-write, external-api]
      probe_value: Verify dialogue turn persists messages and proposals, and handles LLM failures gracefully
    """
    now = time.time()
    config = LLMConfig.intelligence_from_env()
    client_error: Optional[str] = None
    try:
        client = create_llm_client(config)
    except LLMError as exc:
        client = None
        client_error = str(exc)

    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        snapshot_id = session["snapshot_id"]
        session_stage = session["stage"] or "understanding_initialized"

        context_pack = build_interview_context(
            conn, system_id, snapshot_id, budget_chars=payload.budget,
            stage=session_stage,
        )

        message_rows = conn.execute(
            "SELECT role, content FROM interview_message WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        history = [{"role": r["role"], "content": r["content"]} for r in message_rows]

        # Issue #128: the built understanding is the model's working
        # hypothesis — inject it so questions confirm/correct instead of
        # asking from scratch.
        session_understanding = (
            json.loads(session["current_understanding"])
            if session["current_understanding"] else None
        )
        session_gaps = (
            json.loads(session["gap_analysis"]) if session["gap_analysis"] else None
        )
        session_open_questions = (
            json.loads(session["open_questions"]) if session["open_questions"] else None
        )

        # Issue #129: the latest revisions of already-answered Q&A pairs are
        # injected into the prompt with a do-not-re-ask rule, so semantic
        # re-asking is suppressed by the reasoning model (never by fuzzy
        # text matching — Principle 6). Issue #142: rows the developer
        # explicitly could not confirm ("I don't know") are valid, recorded
        # input — fed back as open hypotheses the model must re-confirm,
        # never as established facts. Shared with the update-understanding
        # review via _load_qa_pairs (Issue #263).
        answered_qa_pairs, unconfirmed_qa_pairs = _load_qa_pairs(
            conn, session_id, system_id
        )

        # Issue #142: when THIS turn is an explicit "I don't know" answer, the
        # question being answered is still 'open' (it is only marked
        # 'unconfirmed' AFTER the LLM call, below), so it is not yet in
        # unconfirmed_qa_pairs. Inject it into this turn's reasoning context so
        # the model forms a hypothesis and re-confirms in the SAME turn from
        # the explicit flag — not from a free-text guess about user_message.
        current_unknown_qa: List[dict] = []
        if payload.answer_unknown:
            unknown_question_text: Optional[str] = None
            if payload.answered_qa_id is not None:
                q_row = conn.execute(
                    """SELECT question_text FROM interview_qa
                       WHERE id = ? AND session_id = ? AND system_id = ?""",
                    (payload.answered_qa_id, session_id, system_id),
                ).fetchone()
                if q_row is not None:
                    unknown_question_text = q_row["question_text"]
            if unknown_question_text is None and payload.answered_question:
                unknown_question_text = payload.answered_question
            current_unknown_qa.append({
                "question": unknown_question_text or "(the question just answered)",
                "answer": payload.user_message,
            })

        # Merge the current unknown answer ahead of already-persisted
        # unconfirmed rows, de-duplicated by question text.
        merged_unconfirmed = list(current_unknown_qa)
        seen_unconfirmed = {u["question"] for u in merged_unconfirmed}
        for u in unconfirmed_qa_pairs:
            if u["question"] not in seen_unconfirmed:
                merged_unconfirmed.append(u)
                seen_unconfirmed.add(u["question"])
        unconfirmed_qa_for_turn = merged_unconfirmed or None

        # Proposal gate (Issues #83, #123), computed BEFORE the reasoning
        # call: proposal_generation stage + explicit request + a built
        # understanding or the developer's manual confirmation. The same
        # value both tells the model that proposals were requested this turn
        # (so an empty-proposal reply must carry narrowing questions instead
        # of a plain answer — prompt interview-v6) and gates persistence
        # below.
        understanding_confirmed = (
            "understanding_confirmed_at" in session.keys()
            and session["understanding_confirmed_at"] is not None
        )
        proposals_requested = (
            session_stage == "proposal_generation"
            and payload.generate_proposals
            and (
                session["current_understanding"] is not None
                or understanding_confirmed
            )
        )

        # Issue #130: pass 1 selects (or declines) evidence to read from the
        # pinned snapshot before pass 2 asks the next question. Both passes
        # are audited as separate intelligence_runs rows below.
        evidence_audit: Optional[EvidenceSelectionResult] = None
        evidence_snippets: list = []
        # Issue #137: the pass-1 reasoning call can succeed (valid targets) yet
        # the deterministic read of those targets can still fail closed. That
        # failure belongs on the evidence-selection run's audit record, not
        # only on the turn — track it so the run is marked failed, not
        # "completed", even though partial snippets were read.
        evidence_read_error: Optional[str] = None

        if client_error:
            turn = InterviewTurnResult(
                provider=config.provider,
                model=config.model,
                is_mock=config.provider == "mock",
                error=client_error,
            )
        else:
            evidence_audit = select_evidence_targets(
                client,
                config,
                context_pack=context_pack,
                history=history,
                user_message=payload.user_message,
                current_understanding=session_understanding,
            )
            turn = None
            if evidence_audit.error:
                turn = InterviewTurnResult(
                    provider=config.provider,
                    model=config.model,
                    is_mock=evidence_audit.is_mock,
                    error=evidence_audit.error,
                )
            elif evidence_audit.need_evidence:
                snapshot_row = conn.execute(
                    "SELECT repo_path, commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
                    (snapshot_id, system_id),
                ).fetchone()
                try:
                    if snapshot_row is None or not snapshot_row["repo_path"]:
                        raise EvidenceReadError(
                            "Snapshot repository path is unavailable for evidence reads"
                        )
                    evidence_snippets = read_evidence_snippets(
                        snapshot_row["repo_path"],
                        snapshot_row["commit_sha"],
                        evidence_audit.targets,
                    )
                except EvidenceReadError as exc:
                    # Fail-closed: no "continue without the snippet" fallback.
                    # Whatever snippets were read before the failing target
                    # are kept so they can still be audited (Issue #137).
                    evidence_snippets = exc.partial_snippets
                    evidence_read_error = str(exc)
                    turn = InterviewTurnResult(
                        provider=config.provider,
                        model=config.model,
                        is_mock=False,
                        error=str(exc),
                    )
                except EvidenceConfigError as exc:
                    # Invalid INTERVIEW_EVIDENCE_* configuration is a recorded
                    # turn failure, not an unaudited HTTP 500. The read phase
                    # did not complete, so the selection run is failed too.
                    evidence_read_error = str(exc)
                    turn = InterviewTurnResult(
                        provider=config.provider,
                        model=config.model,
                        is_mock=False,
                        error=str(exc),
                    )

            if turn is None:
                turn = generate_interview_turn(
                    client,
                    config,
                    context_pack=context_pack,
                    history=history,
                    user_message=payload.user_message,
                    current_understanding=session_understanding,
                    gap_analysis=session_gaps,
                    open_questions=session_open_questions,
                    answered_qa=answered_qa_pairs,
                    unconfirmed_qa=unconfirmed_qa_for_turn,
                    evidence_snippets=evidence_snippets,
                    proposals_requested=proposals_requested,
                )

        conn.execute("BEGIN")
        try:
            # Store user message.
            conn.execute(
                """INSERT INTO interview_message
                    (session_id, system_id, role, content, intelligence_run_id, created_at)
                VALUES (?, ?, 'user', ?, NULL, ?)""",
                (session_id, system_id, payload.user_message, now),
            )

            # Store the pass-1 evidence-selection run (Issue #130), when it ran.
            evidence_run_out: Optional[IntelligenceRunOut] = None
            if evidence_audit is not None:
                # The selection run is failed if either the reasoning call
                # failed or the deterministic read of its targets failed
                # (Issue #137): the audit must show "what was attempted and
                # failed", not a misleading completed.
                ev_error = evidence_audit.error or evidence_read_error
                ev_status = "failed" if ev_error else "completed"
                ev_cur = conn.execute(
                    """INSERT INTO intelligence_runs
                        (system_id, snapshot_id, run_type, provider, model,
                         prompt_version, schema_version, decision_method, status,
                         error_details, is_mock, started_at, completed_at)
                    VALUES (?, ?, 'interview_evidence_selection', ?, ?, ?, ?,
                            'reasoning_llm', ?, ?, ?, ?, ?)""",
                    (
                        system_id,
                        snapshot_id,
                        evidence_audit.provider,
                        evidence_audit.model,
                        evidence_audit.prompt_version,
                        evidence_audit.schema_version,
                        ev_status,
                        ev_error,
                        1 if evidence_audit.is_mock else 0,
                        now,
                        now,
                    ),
                )
                evidence_run_row = conn.execute(
                    "SELECT * FROM intelligence_runs WHERE id = ?", (ev_cur.lastrowid,),
                ).fetchone()
                evidence_run_out = _intelligence_run_out(evidence_run_row)

            # Issue #137: persist every snippet actually read during pass 1,
            # linked to its evidence-selection run, regardless of whether the
            # eventual question cites it or pass 2 later fails. Snippet
            # content itself is never stored.
            evidence_reads_out: List[IntelligenceRunEvidenceOut] = []
            if evidence_audit is not None and evidence_snippets:
                for snippet in evidence_snippets:
                    er_cur = conn.execute(
                        """INSERT INTO intelligence_run_evidence
                            (system_id, intelligence_run_id, path, start_line,
                             end_line, char_count, truncated, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            system_id,
                            ev_cur.lastrowid,
                            snippet.path,
                            snippet.start_line,
                            snippet.end_line,
                            snippet.char_count,
                            1 if snippet.truncated else 0,
                            now,
                        ),
                    )
                    evidence_reads_out.append(IntelligenceRunEvidenceOut(
                        id=er_cur.lastrowid,
                        system_id=system_id,
                        intelligence_run_id=ev_cur.lastrowid,
                        path=snippet.path,
                        start_line=snippet.start_line,
                        end_line=snippet.end_line,
                        char_count=snippet.char_count,
                        truncated=snippet.truncated,
                        created_at=now,
                    ))

            # Store intelligence run (success or failure).
            run_status = "failed" if turn.error else "completed"
            run_cur = conn.execute(
                """INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method, status,
                     error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'interview_dialogue', ?, ?, ?, ?, 'reasoning_llm',
                        ?, ?, ?, ?, ?)""",
                (
                    system_id,
                    snapshot_id,
                    turn.provider,
                    turn.model,
                    turn.prompt_version,
                    turn.schema_version,
                    run_status,
                    turn.error,
                    1 if turn.is_mock else 0,
                    now,
                    now,
                ),
            )
            run_id = run_cur.lastrowid

            run_row = conn.execute(
                "SELECT * FROM intelligence_runs WHERE id = ?", (run_id,),
            ).fetchone()
            intelligence_run_out = _intelligence_run_out(run_row)

            if turn.error:
                conn.execute(
                    """UPDATE interview_session
                       SET last_error = ?, updated_at = ?
                       WHERE id = ? AND system_id = ?""",
                    (turn.error, now, session_id, system_id),
                )
                conn.execute("COMMIT")
                return InterviewDialogueTurnOut(
                    error=turn.error,
                    intelligence_run=intelligence_run_out,
                    evidence_run=evidence_run_out,
                    evidence_reads=evidence_reads_out,
                    proposals_requested=proposals_requested,
                )

            # Store assistant message.
            asst_cur = conn.execute(
                """INSERT INTO interview_message
                    (session_id, system_id, role, content, intelligence_run_id, created_at)
                VALUES (?, ?, 'assistant', ?, ?, ?)""",
                (session_id, system_id, turn.assistant_message, run_id, now),
            )
            asst_message_id = asst_cur.lastrowid

            # Persistence gate = the same proposal gate computed before the
            # reasoning call (Issues #83, #123). Kept as defense-in-depth: even
            # if the model emits proposals on a turn that did not request
            # them, nothing is persisted.
            current_stage = session["stage"] or "understanding_initialized"

            proposal_outs: List[InterviewDialogueProposalOut] = []
            gated_proposals = turn.proposals if proposals_requested else []
            for p in gated_proposals:
                p_graph_node_id = getattr(p, "graph_node_id", None)
                p_capability_name = getattr(p, "capability_name", None)
                p_evidence_summary = getattr(p, "evidence_summary", None)
                p_proposal_confidence = getattr(p, "proposal_confidence", None)
                conn.execute(
                    """INSERT INTO interview_proposal
                        (session_id, system_id, snapshot_id, message_id,
                         intelligence_run_id, symbol_id, path, qualified_name,
                         md_role, md_capability, md_system_purpose, md_probe_value,
                         md_element_type, md_operation_kind, md_consumers,
                         md_state_effects, feature_id, objective, probe_reason,
                         recommended_mode, side_effect_risk, replayability,
                         graph_node_id, capability_name, evidence_summary,
                         proposal_confidence,
                         decision_method, approval_state, is_mock,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, 'reasoning_llm', 'proposed', ?, ?, ?)""",
                    (
                        session_id,
                        system_id,
                        snapshot_id,
                        asst_message_id,
                        run_id,
                        p.symbol_id,
                        p.path,
                        p.qualified_name,
                        p.metadata.role,
                        p.metadata.capability,
                        p.metadata.system_purpose,
                        p.metadata.probe_value,
                        p.metadata.element_type,
                        p.metadata.operation_kind,
                        json.dumps(p.metadata.consumers, ensure_ascii=False),
                        json.dumps(p.metadata.state_effects, ensure_ascii=False),
                        p.probe_plan.feature_id,
                        p.probe_plan.objective,
                        p.probe_plan.reason,
                        p.probe_plan.recommended_mode,
                        p.probe_plan.side_effect_risk,
                        p.probe_plan.replayability,
                        p_graph_node_id,
                        p_capability_name,
                        p_evidence_summary,
                        p_proposal_confidence,
                        1 if turn.is_mock else 0,
                        now,
                        now,
                    ),
                )
                proposal_outs.append(InterviewDialogueProposalOut(
                    path=p.path,
                    qualified_name=p.qualified_name,
                    symbol_id=p.symbol_id,
                    metadata=p.metadata,
                    probe_plan=p.probe_plan,
                    graph_node_id=p_graph_node_id,
                    capability_name=p_capability_name,
                    evidence_summary=p_evidence_summary,
                    proposal_confidence=p_proposal_confidence,
                    denylist_hit=p.denylist_hit,
                ))

            stage_updates = ["updated_at = ?"]
            stage_params: list = [now]

            # Issue #129: persist next_questions as ID-addressable interview_qa
            # rows first, so the legacy open_questions JSON entries below can
            # carry each row's qa_id and the dashboard can answer by ID.
            # A question whose exact text already exists as a current row is
            # not re-inserted (structural exact-text dedupe); its existing ID
            # is reused. Issue #130: attach the char_count of any evidence
            # snippet a question's evidence_refs actually cite, so the
            # dashboard can show what was read for that question.
            existing_qa_by_text = {
                r["question_text"]: r["id"]
                for r in conn.execute(
                    """SELECT id, question_text FROM interview_qa
                       WHERE session_id = ? AND system_id = ?
                         AND superseded_by_id IS NULL
                       ORDER BY id""",
                    (session_id, system_id),
                ).fetchall()
            }
            created_qa_ids: List[int] = []
            new_entries: List[dict] = []
            for raw_question in turn.next_questions:
                question = _question_out(raw_question)
                if question.question_text in existing_qa_by_text:
                    qa_id = existing_qa_by_text[question.question_text]
                else:
                    qa_evidence_refs = [
                        InterviewQaEvidenceRefOut(
                            path=ref.path,
                            start_line=ref.start_line,
                            end_line=ref.end_line,
                            char_count=next(
                                (
                                    s.char_count for s in evidence_snippets
                                    if s.path == ref.path
                                    and s.start_line <= ref.start_line
                                    and s.end_line >= ref.end_line
                                ),
                                None,
                            ),
                        )
                        for ref in question.evidence_refs
                    ]
                    qa_id = _insert_qa_row(
                        conn, session_id, system_id,
                        question_text=question.question_text,
                        question_category="general",
                        question_source="dialogue",
                        hypothesis=question.hypothesis,
                        evidence_refs=qa_evidence_refs,
                        now=now,
                    )
                    existing_qa_by_text[question.question_text] = qa_id
                    created_qa_ids.append(qa_id)
                entry = _question_entry(raw_question)
                entry["qa_id"] = qa_id
                new_entries.append(entry)

            # Consume the answered open question and record the model's
            # follow-up questions so the UI never re-asks what was already
            # answered (Issues #123, #129). Consumption is structural:
            # answered_qa_id matches an entry's qa_id; answered_question is
            # the legacy exact-text match, honored during the migration
            # period. Both roads also mark the interview_qa row answered.
            consumed_qa_ids: set = set()
            if payload.answered_qa_id is not None:
                consumed_qa_ids.add(payload.answered_qa_id)
            existing_questions: List[dict] = list(session_open_questions or [])
            remaining_questions: List[dict] = []
            for q in existing_questions:
                text_match = bool(
                    payload.answered_question
                    and q.get("question") == payload.answered_question
                )
                id_match = (
                    q.get("qa_id") is not None
                    and q.get("qa_id") == payload.answered_qa_id
                )
                if text_match or id_match:
                    if q.get("qa_id") is not None:
                        consumed_qa_ids.add(q["qa_id"])
                    continue
                remaining_questions.append(q)
            known_texts = {q.get("question") for q in remaining_questions}
            for entry in new_entries:
                if entry["question"] and entry["question"] not in known_texts:
                    remaining_questions.append(entry)
                    known_texts.add(entry["question"])
            remaining_questions = remaining_questions[:20]
            if remaining_questions != existing_questions:
                stage_updates.append("open_questions = ?")
                stage_params.append(
                    json.dumps(remaining_questions, ensure_ascii=False)
                    if remaining_questions else None
                )

            # Issue #129: record the user's message as the answer on each
            # consumed Q&A row. Silently ignored if a row is missing or
            # already answered — the dialogue turn itself must never fail
            # because of a stale Q&A reference.
            # Issue #142: when the developer answered "I don't know"
            # (answer_unknown), the row is recorded as 'unconfirmed' rather
            # than 'answered' — a valid but non-confirming input that later
            # turns re-confirm via a hypothesis question.
            consumed_status = "unconfirmed" if payload.answer_unknown else "answered"
            for consumed_id in sorted(consumed_qa_ids):
                answered_row = conn.execute(
                    """SELECT id, status FROM interview_qa
                       WHERE id = ? AND session_id = ? AND system_id = ?""",
                    (consumed_id, session_id, system_id),
                ).fetchone()
                if answered_row is not None and answered_row["status"] in ("open", "skipped"):
                    conn.execute(
                        """UPDATE interview_qa
                           SET answer_text = ?, status = ?,
                               answered_by = ?, answered_at = ?
                           WHERE id = ?""",
                        (payload.user_message, consumed_status, payload.actor, now, consumed_id),
                    )

            if payload.user_message.strip():
                existing_intent = session["user_intent"] or ""
                new_intent = (existing_intent + "\n" + payload.user_message.strip()).strip() if existing_intent else payload.user_message.strip()
                if len(new_intent) > 2000:
                    new_intent = new_intent[-2000:]
                stage_updates.append("user_intent = ?")
                stage_params.append(new_intent)

            if current_stage != "proposal_generation":
                try:
                    current_idx = STAGE_ORDER.index(current_stage)
                    if current_idx < len(STAGE_ORDER) - 1:
                        next_stage = STAGE_ORDER[current_idx + 1]
                        stage_updates.append("stage = ?")
                        stage_params.append(next_stage)
                except ValueError:
                    pass

            stage_updates.append("last_error = NULL")
            stage_params.extend([session_id, system_id])
            conn.execute(
                f"UPDATE interview_session SET {', '.join(stage_updates)} WHERE id = ? AND system_id = ?",
                stage_params,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        # Re-read session for latest understanding state.
        updated_row = _get_session_or_404(conn, session_id, system_id)
        updated_session = _session_out(updated_row)

        return InterviewDialogueTurnOut(
            assistant_message=turn.assistant_message,
            proposals=proposal_outs,
            proposals_requested=proposals_requested,
            next_questions=[_question_out(q) for q in turn.next_questions],
            intelligence_run=intelligence_run_out,
            stage=updated_session.stage,
            current_understanding=updated_session.current_understanding,
            gap_analysis=updated_session.gap_analysis,
            open_questions_structured=updated_session.open_questions,
            created_qa_ids=created_qa_ids,
            evidence_run=evidence_run_out,
            evidence_used=[
                InterviewQaEvidenceRefOut(
                    path=s.path, start_line=s.start_line, end_line=s.end_line,
                    char_count=s.char_count,
                )
                for s in evidence_snippets
            ],
            evidence_reads=evidence_reads_out,
            evidence_refs_dropped=turn.evidence_refs_dropped,
        )


# --- Structured Interview Q&A (Issue #129) -----------------------------------


@router.get(
    "/interview/sessions/{session_id}/qa",
    response_model=InterviewQaListOut,
)
def list_interview_qa(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewQaListOut:
    """List the current Q&A rows for a session.

    "Current" means the latest revision in each chain (rows with no
    superseded_by_id). Superseded rows remain in the database for audit but
    are reachable only via the ``previous`` field of the answer endpoint's
    response, not this list — this keeps the list one row per question.

    probe-agent:
      role: API boundary for the structured interview Q&A list
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify the list excludes superseded rows and reports open/high-priority counts correctly
    """
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            """SELECT * FROM interview_qa
               WHERE session_id = ? AND system_id = ? AND superseded_by_id IS NULL
               ORDER BY id""",
            (session_id, system_id),
        ).fetchall()
        items = [_qa_out(r) for r in rows]
        open_items = [i for i in items if i.status == "open"]
        return InterviewQaListOut(
            session_id=session_id,
            system_id=system_id,
            items=items,
            open_count=len(open_items),
            high_priority_open_count=sum(
                1 for i in open_items if i.question_category in _HIGH_PRIORITY_QA_CATEGORIES
            ),
            answers_revised_at=(
                session["answers_revised_at"]
                if "answers_revised_at" in session.keys() else None
            ),
        )


@router.post(
    "/interview/sessions/{session_id}/qa",
    response_model=InterviewQaOut,
    status_code=201,
)
def create_interview_qa(
    session_id: int,
    payload: InterviewQaCreate,
    system_id: int = Depends(get_system_id),
) -> InterviewQaOut:
    """Create a Q&A row for a question from any of the three sources.

    Dialogue-turn questions are created automatically by the dialogue-turn
    endpoint; this endpoint additionally lets the reviewer flow and the
    zero-base fixed questions register themselves as structured rows.

    probe-agent:
      role: API boundary for registering a new structured interview question
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify a new question persists with status open and no answer
    """
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        qa_id = _insert_qa_row(
            conn, session_id, system_id,
            question_text=payload.question_text,
            question_category=payload.question_category,
            question_source=payload.question_source,
            hypothesis=payload.hypothesis,
            evidence_refs=payload.evidence_refs,
            now=now,
        )
        row = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone()
        return _qa_out(row)


@router.post(
    "/interview/sessions/{session_id}/qa/{qa_id}/answer",
    response_model=InterviewQaAnswerOut,
)
def answer_interview_qa(
    session_id: int,
    qa_id: int,
    payload: InterviewQaAnswerRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewQaAnswerOut:
    """Answer a question, or correct its existing answer.

    First answer (status 'open' or 'skipped'): recorded on the same row —
    there is nothing to supersede yet. Correction (status 'answered'): never
    overwrites; inserts a new 'answered' row holding the correction and marks
    the old row 'revised' with superseded_by_id pointing at the new row
    (Principle 7 audit trail). Corrections also set the session's
    answers_revised_at flag so the dashboard can recommend rebuilding the
    understanding, and — without auto-invalidating or regenerating anything —
    report whether the session already has generated proposals.

    probe-agent:
      role: API boundary for answering or correcting an interview question
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify corrections create a new revision row, link the old row, and never overwrite a prior answer
    """
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        qa = _get_qa_or_404(conn, qa_id, session_id, system_id)

        if qa["superseded_by_id"] is not None or qa["status"] == "revised":
            raise HTTPException(
                status_code=409,
                detail="This question has already been superseded by a newer revision",
            )

        proposal_row = conn.execute(
            "SELECT id FROM interview_proposal WHERE session_id = ? LIMIT 1",
            (session_id,),
        ).fetchone()
        has_proposals = proposal_row is not None

        # Issue #142: an "I don't know" answer records the row as
        # 'unconfirmed' (valid but non-confirming) instead of 'answered'.
        new_status = "unconfirmed" if payload.answer_unknown else "answered"

        conn.execute("BEGIN")
        try:
            if qa["status"] in ("open", "skipped"):
                conn.execute(
                    """UPDATE interview_qa
                       SET answer_text = ?, status = ?, answered_by = ?,
                           answered_at = ?
                       WHERE id = ?""",
                    (payload.answer_text, new_status, payload.actor, now, qa_id),
                )
                conn.execute("COMMIT")
                updated = conn.execute(
                    "SELECT * FROM interview_qa WHERE id = ?", (qa_id,)
                ).fetchone()
                return InterviewQaAnswerOut(
                    qa=_qa_out(updated),
                    previous=None,
                    regeneration_recommended=False,
                )

            # status is 'answered' or 'unconfirmed': this is a correction.
            # Insert a new
            # revision row, then link the old row forward. runtime_evidence
            # (Issue #135) is carried onto the new current row just like
            # evidence_refs, so correcting a runtime question's answer never
            # drops the trace-fact provenance that justified the question.
            new_id = _insert_qa_row(
                conn, session_id, system_id,
                question_text=qa["question_text"],
                question_category=qa["question_category"],
                question_source=qa["question_source"],
                hypothesis=qa["hypothesis"],
                evidence_refs=(
                    [InterviewQaEvidenceRefOut(**e) for e in json.loads(qa["evidence_refs"])]
                    if qa["evidence_refs"] else []
                ),
                now=now,
                answer_text=payload.answer_text,
                status=new_status,
                answered_by=payload.actor,
                answered_at=now,
                runtime_evidence=(
                    json.loads(qa["runtime_evidence"])
                    if ("runtime_evidence" in qa.keys() and qa["runtime_evidence"])
                    else None
                ),
            )
            conn.execute(
                "UPDATE interview_qa SET status = 'revised', superseded_by_id = ? WHERE id = ?",
                (new_id, qa_id),
            )
            conn.execute(
                """UPDATE interview_session SET answers_revised_at = ?, updated_at = ?
                   WHERE id = ? AND system_id = ?""",
                (now, now, session_id, system_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        new_row = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (new_id,)).fetchone()
        old_row = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone()
        return InterviewQaAnswerOut(
            qa=_qa_out(new_row),
            previous=_qa_out(old_row),
            regeneration_recommended=has_proposals,
        )


@router.post(
    "/interview/sessions/{session_id}/qa/{qa_id}/skip",
    response_model=InterviewQaOut,
)
def skip_interview_qa(
    session_id: int,
    qa_id: int,
    payload: InterviewQaActorRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewQaOut:
    """Skip an open question so it can be answered later (state: open -> skipped)."""
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        qa = _get_qa_or_404(conn, qa_id, session_id, system_id)
        if qa["status"] != "open":
            raise HTTPException(
                status_code=409,
                detail=f"Only 'open' questions can be skipped (current: {qa['status']})",
            )
        conn.execute("UPDATE interview_qa SET status = 'skipped' WHERE id = ?", (qa_id,))
        row = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone()
        return _qa_out(row)


@router.post(
    "/interview/sessions/{session_id}/qa/{qa_id}/resume",
    response_model=InterviewQaOut,
)
def resume_interview_qa(
    session_id: int,
    qa_id: int,
    payload: InterviewQaActorRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewQaOut:
    """Reopen a skipped question (state: skipped -> open)."""
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        qa = _get_qa_or_404(conn, qa_id, session_id, system_id)
        if qa["status"] != "skipped":
            raise HTTPException(
                status_code=409,
                detail=f"Only 'skipped' questions can be resumed (current: {qa['status']})",
            )
        conn.execute("UPDATE interview_qa SET status = 'open' WHERE id = ?", (qa_id,))
        row = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone()
        return _qa_out(row)


# --- Proposal Approval Gate (Issue #70) ---------------------------------------


def _get_proposal_or_404(conn, proposal_id: int, session_id: int, system_id: int):
    row = conn.execute(
        """SELECT * FROM interview_proposal
           WHERE id = ? AND session_id = ? AND system_id = ?""",
        (proposal_id, session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return row


def _decision_out(row) -> InterviewProposalDecisionOut:
    edited_metadata = None
    edited_probe_plan = None
    if row["decision"] == "edited":
        edited_metadata = InterviewProposalMetadataBlock(
            role=row["edited_md_role"],
            capability=row["edited_md_capability"],
            system_purpose=row["edited_md_system_purpose"],
            probe_value=row["edited_md_probe_value"],
            element_type=row["edited_md_element_type"],
            operation_kind=row["edited_md_operation_kind"],
            consumers=json.loads(row["edited_md_consumers"] or "[]"),
            state_effects=json.loads(row["edited_md_state_effects"] or "[]"),
        )
        edited_probe_plan = InterviewProposalProbePlan(
            feature_id=row["edited_feature_id"] or "",
            objective=row["edited_objective"] or "",
            reason=row["edited_probe_reason"] or "",
            recommended_mode=row["edited_recommended_mode"] or "trace",
            side_effect_risk=row["edited_side_effect_risk"] or "low",
            replayability=row["edited_replayability"] or "safe",
        )
    return InterviewProposalDecisionOut(
        id=row["id"],
        proposal_id=row["proposal_id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        decision=row["decision"],
        decision_method=row["decision_method"],
        actor=row["actor"],
        edited_metadata=edited_metadata,
        edited_probe_plan=edited_probe_plan,
        denylist_hit=row["denylist_hit"],
        decided_at=row["decided_at"],
    )


def _check_proposal_state(proposal_row) -> None:
    """Only unreviewed or re-review items can be transitioned."""
    state = proposal_row["approval_state"]
    if state not in ("proposed", "needs_review"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Proposal is already '{state}'; only 'proposed' or "
                "'needs_review' items can be reviewed"
            ),
        )


def _persist_decision(
    conn,
    proposal_row,
    session_id: int,
    system_id: int,
    decision: str,
    actor: str,
    now: float,
    *,
    edited_metadata: Optional[InterviewProposalMetadataBlock] = None,
    edited_probe_plan: Optional[InterviewProposalProbePlan] = None,
    denylist_hit: Optional[str] = None,
) -> InterviewProposalDecisionOut:
    proposal_id = proposal_row["id"]

    cur = conn.execute(
        """INSERT INTO interview_proposal_decision
            (proposal_id, session_id, system_id, decision, decision_method,
             actor,
             edited_md_role, edited_md_capability, edited_md_system_purpose,
             edited_md_probe_value, edited_md_element_type, edited_md_operation_kind,
             edited_md_consumers, edited_md_state_effects,
             edited_feature_id, edited_objective, edited_probe_reason,
             edited_recommended_mode, edited_side_effect_risk, edited_replayability,
             denylist_hit, decided_at)
        VALUES (?, ?, ?, ?, 'manual', ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?)""",
        (
            proposal_id,
            session_id,
            system_id,
            decision,
            actor,
            edited_metadata.role if edited_metadata else None,
            edited_metadata.capability if edited_metadata else None,
            edited_metadata.system_purpose if edited_metadata else None,
            edited_metadata.probe_value if edited_metadata else None,
            edited_metadata.element_type if edited_metadata else None,
            edited_metadata.operation_kind if edited_metadata else None,
            json.dumps(edited_metadata.consumers, ensure_ascii=False) if edited_metadata else None,
            json.dumps(edited_metadata.state_effects, ensure_ascii=False) if edited_metadata else None,
            edited_probe_plan.feature_id if edited_probe_plan else None,
            edited_probe_plan.objective if edited_probe_plan else None,
            edited_probe_plan.reason if edited_probe_plan else None,
            edited_probe_plan.recommended_mode if edited_probe_plan else None,
            edited_probe_plan.side_effect_risk if edited_probe_plan else None,
            edited_probe_plan.replayability if edited_probe_plan else None,
            denylist_hit,
            now,
        ),
    )
    decision_id = cur.lastrowid

    conn.execute(
        """UPDATE interview_proposal
           SET approval_state = ?, updated_at = ?
           WHERE id = ?""",
        (decision, now, proposal_id),
    )
    conn.execute(
        "UPDATE interview_session SET updated_at = ? WHERE id = ? AND system_id = ?",
        (now, session_id, system_id),
    )

    row = conn.execute(
        "SELECT * FROM interview_proposal_decision WHERE id = ?",
        (decision_id,),
    ).fetchone()
    return _decision_out(row)


@router.post(
    "/interview/sessions/{session_id}/proposals/{proposal_id}/approve",
    response_model=InterviewProposalDecisionOut,
)
def approve_interview_proposal(
    session_id: int,
    proposal_id: int,
    payload: InterviewProposalApproveRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewProposalDecisionOut:
    """Approve a pending interview proposal for materialization.

    probe-agent:
      role: API boundary for approving an interview proposal
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify approval persists decision and enforces proposal state guard
    """
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        proposal = _get_proposal_or_404(conn, proposal_id, session_id, system_id)
        _check_proposal_state(proposal)
        return _persist_decision(
            conn, proposal, session_id, system_id,
            decision="approved", actor=payload.actor, now=now,
        )


@router.post(
    "/interview/sessions/{session_id}/proposals/{proposal_id}/reject",
    response_model=InterviewProposalDecisionOut,
)
def reject_interview_proposal(
    session_id: int,
    proposal_id: int,
    payload: InterviewProposalRejectRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewProposalDecisionOut:
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        proposal = _get_proposal_or_404(conn, proposal_id, session_id, system_id)
        _check_proposal_state(proposal)
        return _persist_decision(
            conn, proposal, session_id, system_id,
            decision="rejected", actor=payload.actor, now=now,
        )


@router.post(
    "/interview/sessions/{session_id}/proposals/{proposal_id}/edit",
    response_model=InterviewProposalDecisionOut,
)
def edit_interview_proposal(
    session_id: int,
    proposal_id: int,
    payload: InterviewProposalEditRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewProposalDecisionOut:
    """Edit a proposal with corrected values and mark as approved.

    Re-runs the safety denylist on the edited qualified_name and probe-plan
    values. If the symbol is denylisted, the edit is rejected with 422.
    """
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        proposal = _get_proposal_or_404(conn, proposal_id, session_id, system_id)
        _check_proposal_state(proposal)

        extra_text_parts = []
        if payload.metadata:
            for field in ("role", "capability", "system_purpose", "probe_value"):
                val = getattr(payload.metadata, field, None)
                if val:
                    extra_text_parts.append(val)
        if payload.probe_plan:
            for field in ("feature_id", "objective", "reason"):
                val = getattr(payload.probe_plan, field, None)
                if val:
                    extra_text_parts.append(val)
        extra_text = " ".join(extra_text_parts) if extra_text_parts else None

        hit = check_denylist(proposal["qualified_name"], extra_text)
        if hit:
            raise HTTPException(
                status_code=422,
                detail=f"Edit rejected: denylisted content ({hit})",
            )

        return _persist_decision(
            conn, proposal, session_id, system_id,
            decision="edited", actor=payload.actor, now=now,
            edited_metadata=payload.metadata,
            edited_probe_plan=payload.probe_plan,
            denylist_hit=hit,
        )


@router.get(
    "/interview/sessions/{session_id}/approved-set",
    response_model=InterviewApprovedSetOut,
)
def get_interview_approved_set(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewApprovedSetOut:
    """Return the approved set for a session: items eligible for materialization.

    Only proposals with an explicit 'approved' or 'edited' decision are
    included. 'proposed' and 'rejected' items are excluded.
    """
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        snapshot_id = session["snapshot_id"]

        all_proposals = conn.execute(
            "SELECT * FROM interview_proposal WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()

        total = len(all_proposals)
        approved_count = 0
        rejected_count = 0
        pending_count = 0
        items: List[InterviewApprovedItemOut] = []

        for p in all_proposals:
            state = p["approval_state"]
            if state in ("proposed", "needs_review"):
                pending_count += 1
                continue
            elif state == "rejected":
                rejected_count += 1
                continue

            # approved or edited — get the decision record.
            decision_row = conn.execute(
                """SELECT * FROM interview_proposal_decision
                   WHERE proposal_id = ? ORDER BY id DESC LIMIT 1""",
                (p["id"],),
            ).fetchone()
            if decision_row is None:
                pending_count += 1
                continue

            approved_count += 1

            if state == "edited" and decision_row["decision"] == "edited":
                metadata = InterviewProposalMetadataBlock(
                    role=decision_row["edited_md_role"],
                    capability=decision_row["edited_md_capability"],
                    system_purpose=decision_row["edited_md_system_purpose"],
                    probe_value=decision_row["edited_md_probe_value"],
                    element_type=decision_row["edited_md_element_type"],
                    operation_kind=decision_row["edited_md_operation_kind"],
                    consumers=json.loads(decision_row["edited_md_consumers"] or "[]"),
                    state_effects=json.loads(decision_row["edited_md_state_effects"] or "[]"),
                )
                probe_plan = InterviewProposalProbePlan(
                    feature_id=decision_row["edited_feature_id"] or "",
                    objective=decision_row["edited_objective"] or "",
                    reason=decision_row["edited_probe_reason"] or "",
                    recommended_mode=decision_row["edited_recommended_mode"] or "trace",
                    side_effect_risk=decision_row["edited_side_effect_risk"] or "low",
                    replayability=decision_row["edited_replayability"] or "safe",
                )
            else:
                metadata = InterviewProposalMetadataBlock(
                    role=p["md_role"],
                    capability=p["md_capability"],
                    system_purpose=p["md_system_purpose"],
                    probe_value=p["md_probe_value"],
                    element_type=p["md_element_type"],
                    operation_kind=p["md_operation_kind"],
                    consumers=json.loads(p["md_consumers"] or "[]"),
                    state_effects=json.loads(p["md_state_effects"] or "[]"),
                )
                probe_plan = InterviewProposalProbePlan(
                    feature_id=p["feature_id"],
                    objective=p["objective"],
                    reason=p["probe_reason"],
                    recommended_mode=p["recommended_mode"],
                    side_effect_risk=p["side_effect_risk"],
                    replayability=p["replayability"],
                )

            items.append(InterviewApprovedItemOut(
                proposal_id=p["id"],
                path=p["path"],
                qualified_name=p["qualified_name"],
                symbol_id=p["symbol_id"],
                metadata=metadata,
                probe_plan=probe_plan,
                decision=decision_row["decision"],
                decision_id=decision_row["id"],
                actor=decision_row["actor"],
                decided_at=decision_row["decided_at"],
            ))

        return InterviewApprovedSetOut(
            session_id=session_id,
            system_id=system_id,
            snapshot_id=snapshot_id,
            items=items,
            total_proposals=total,
            approved_count=approved_count,
            rejected_count=rejected_count,
            pending_count=pending_count,
        )


@router.post(
    "/interview/sessions/{session_id}/materialize",
    response_model=InterviewMaterializeOut,
)
def materialize_interview_session(
    session_id: int,
    payload: InterviewMaterializeRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewMaterializeOut:
    """Materialize approved proposals into a single reviewable diff.

    Creates an isolated worktree from the pinned commit, writes approved
    ``probe-agent:`` docstring blocks and ``@probe`` instrumentation, and
    returns the unified diff.  The target repository's tracked branches
    are never written to; the worktree is cleaned up after diff capture.

    probe-agent:
      role: API boundary for materializing approved proposals into a diff
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: orchestration
      state_effects: [database-write, filesystem]
      probe_value: Verify materialization produces a valid diff from approved proposals without modifying tracked branches
    """
    import tempfile
    from ..docstring_writer import MetadataValues
    from ..interview_materializer import (
        MaterializationItem,
        materialize_approved_set,
    )

    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        snapshot_id = session["snapshot_id"]

        snapshot = conn.execute(
            "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Snapshot not found")

        repo_path = snapshot["repo_path"]
        commit_sha = snapshot["commit_sha"]

    approved_set = get_interview_approved_set(session_id, system_id)
    if not approved_set.items:
        raise HTTPException(
            status_code=422,
            detail="No approved items to materialize",
        )

    mat_items: List[MaterializationItem] = []
    for item in approved_set.items:
        md = item.metadata
        pp = item.probe_plan
        mat_items.append(MaterializationItem(
            path=item.path,
            qualified_name=item.qualified_name,
            metadata=MetadataValues(
                role=md.role,
                capability=md.capability,
                system_purpose=md.system_purpose,
                probe_value=md.probe_value,
                element_type=md.element_type,
                operation_kind=md.operation_kind,
                consumers=md.consumers if md.consumers else None,
                state_effects=md.state_effects if md.state_effects else None,
            ),
            component_id=_component_id_for(item.qualified_name),
            recommended_mode=pp.recommended_mode,
            line_start=0,
            line_end=0,
        ))

    worktree_base = payload.worktree_base or tempfile.mkdtemp(
        prefix="probe-interview-"
    )

    result = materialize_approved_set(
        repo_path=repo_path,
        commit_sha=commit_sha,
        items=mat_items,
        worktree_base=worktree_base,
    )

    if result.items_applied == 0:
        raise HTTPException(
            status_code=500,
            detail=f"Materialization failed: {result.error or 'no items applied'}",
        )
    if result.error:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Partial materialization: {result.items_applied}/{result.items_total} "
                f"items applied. {result.error}"
            ),
        )

    with get_conn() as conn:
        conn.execute(
            """UPDATE interview_session
               SET materialization_diff = ?, materialized_at = ?, updated_at = ?
               WHERE id = ? AND system_id = ?""",
            (result.diff, now, now, session_id, system_id),
        )

    return InterviewMaterializeOut(
        session_id=session_id,
        system_id=system_id,
        snapshot_id=snapshot_id,
        commit_sha=commit_sha,
        diff=result.diff,
        files_changed=result.files_changed,
        items_materialized=result.items_applied,
        skipped=result.skipped,
        materialized_at=now,
    )


# --- Understanding Confirmation (Issue #123) ---------------------------------


@router.post(
    "/interview/sessions/{session_id}/confirm-understanding",
    response_model=InterviewSessionOut,
)
def confirm_interview_understanding(
    session_id: int,
    payload: InterviewConfirmUnderstandingRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewSessionOut:
    """Record the developer's manual confirmation of the interview context.

    In the zero-base fallback no structured understanding exists, so the
    Issue #83 proposal gate can never be satisfied by ``current_understanding``
    alone. This endpoint records an explicit manual decision that the
    conversation gathered enough context, advances the session to
    ``proposal_generation``, and thereby unlocks proposal generation. The
    confirmation is a human decision (Principle 7 ``manual``), never an LLM
    output.

    probe-agent:
      role: API boundary for manually confirming interview understanding
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify manual confirmation unlocks the proposal gate and is persisted with actor and timestamp
    """
    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)

        # The Dashboard hides the confirmation control once complete. Keep a
        # direct/retried API request idempotent so it cannot append another
        # workflow event or alter the recorded decision.
        if session["understanding_confirmed_at"] is not None:
            return _session_out(session)

        user_turns = conn.execute(
            "SELECT COUNT(*) AS n FROM interview_message WHERE session_id = ? AND role = 'user'",
            (session_id,),
        ).fetchone()["n"]
        if session["current_understanding"] is None and user_turns == 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Nothing to confirm: the session has no built understanding "
                    "and no interview answers yet"
                ),
            )

        new_stage = _advance_stage(
            session["stage"] or "understanding_initialized",
            "proposal_generation",
        )
        conn.execute(
            """UPDATE interview_session
               SET understanding_confirmed_at = ?, understanding_confirmed_by = ?,
                   stage = ?, updated_at = ?
               WHERE id = ? AND system_id = ?""",
            (now, payload.actor, new_stage, now, session_id, system_id),
        )
        conn.execute(
            """INSERT INTO interview_message
                (session_id, system_id, role, content, created_at)
            VALUES (?, ?, 'system', ?, ?)""",
            (
                session_id,
                system_id,
                interview_message("confirm_understanding_message", resolve_message_language()),
                now,
            ),
        )
        row = _get_session_or_404(conn, session_id, system_id)
        return _session_out(row)


# --- Stage Advancement (Issue #82) -------------------------------------------


class _AdvanceStageRequest(BaseModel):
    stage: str
    user_intent: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


@router.post(
    "/interview/sessions/{session_id}/advance-stage",
    response_model=InterviewSessionOut,
)
def advance_interview_stage(
    session_id: int,
    payload: _AdvanceStageRequest,
    system_id: int = Depends(get_system_id),
) -> InterviewSessionOut:
    """Advance the interview session to a specified stage."""
    if payload.stage not in STAGE_ORDER:
        raise HTTPException(status_code=422, detail=f"Invalid stage: {payload.stage}")

    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        new_stage = _advance_stage(session["stage"] or "understanding_initialized", payload.stage)

        updates = ["stage = ?", "updated_at = ?"]
        params: list = [new_stage, now]

        if payload.user_intent is not None:
            updates.append("user_intent = ?")
            params.append(payload.user_intent)

        params.extend([session_id, system_id])
        conn.execute(
            f"UPDATE interview_session SET {', '.join(updates)} WHERE id = ? AND system_id = ?",
            params,
        )
        row = _get_session_or_404(conn, session_id, system_id)
        return _session_out(row)


@router.post(
    "/interview/sessions/{session_id}/update-understanding",
    response_model=InterviewSessionOut,
)
def update_interview_understanding(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewSessionOut:
    """Update the session's current understanding from its graph/reconciliation data.

    This endpoint is the entry point for generating an initial understanding
    when Start Interview is clicked, or for refreshing after new turns.

    probe-agent:
      role: API boundary for updating session understanding from graph data
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: orchestration
      state_effects: [database-write]
      probe_value: Verify understanding update rebuilds graph and reconciliation correctly
    """
    now = time.time()
    # Issue #138: fixed-text messages this route composes itself follow
    # INTERVIEW_LANGUAGE. Falls back to ja only for these messages if the
    # setting is invalid, so a misconfiguration doesn't prevent the failure
    # message itself from being composed; reasoning calls below still fail
    # closed through get_interview_language() unchanged.
    msg_lang = resolve_message_language()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        snapshot_id = session["snapshot_id"]

        # After manual confirmation, rebuilding is meaningful only when an
        # answer correction is waiting to be reflected. Keep this guard at
        # the API boundary as well as in the Dashboard.
        #
        # Issue #263: `answers_revised_at` alone only covers the correction
        # path (an already-answered question re-answered). It misses a
        # first-time answer given after confirmation and a newly issued
        # Runtime Reality Check question answered after confirmation — both
        # only ever touch `interview_qa`, never `answers_revised_at`. Widen
        # the gate with a deterministic, structural check (Principle 6): any
        # interview_qa row for this session created OR answered strictly
        # after understanding_confirmed_at means there is new developer
        # input the confirmed understanding has not seen yet.
        confirmed_at = session["understanding_confirmed_at"]
        if (
            (session["stage"] or "understanding_initialized") == "proposal_generation"
            and confirmed_at is not None
            and session["answers_revised_at"] is None
        ):
            new_qa_since_confirmation = conn.execute(
                """SELECT 1 FROM interview_qa
                   WHERE session_id = ? AND system_id = ?
                     AND (
                       created_at > ?
                       OR (answered_at IS NOT NULL AND answered_at > ?)
                     )
                   LIMIT 1""",
                (session_id, system_id, confirmed_at, confirmed_at),
            ).fetchone()
            if new_qa_since_confirmation is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "understanding_update_not_available",
                        "message": (
                            "Understanding is already confirmed. Update it after "
                            "correcting an interview answer."
                        ),
                        "next_action": "generate_or_review_proposals",
                    },
                )

        from ..docs_code_reconciler import reconcile
        from ..system_understanding_service import _load_graph_for_snapshot
        from ..system_understanding_reviewer import (
            PROMPT_VERSION as REVIEW_PROMPT_VERSION,
            SCHEMA_VERSION as REVIEW_SCHEMA_VERSION,
            generate_understanding_review,
        )

        # Principle 7: the understanding review is a reasoning run; record it
        # in intelligence_runs for success and failure alike so the reviewer's
        # prompt_version stays auditable (Issue #127).
        def _record_review_run(
            status: str,
            error_details: Optional[str],
            provider: str,
            model: str,
            is_mock: bool,
        ) -> int:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method, status,
                     error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'understanding_review', ?, ?, ?, ?,
                        'reasoning_llm', ?, ?, ?, ?, ?)""",
                (
                    system_id,
                    snapshot_id,
                    provider,
                    model,
                    REVIEW_PROMPT_VERSION,
                    REVIEW_SCHEMA_VERSION,
                    status,
                    error_details,
                    1 if is_mock else 0,
                    now,
                    now,
                ),
            )
            return cur.lastrowid

        config = LLMConfig.intelligence_from_env()
        try:
            client = create_llm_client(config)
        except LLMError as exc:
            error = str(exc)
            run_id = _record_review_run(
                "failed", error, config.provider, config.model,
                config.provider == "mock",
            )
            conn.execute(
                """UPDATE interview_session
                   SET last_error = ?, updated_at = ?
                   WHERE id = ? AND system_id = ?""",
                (error, now, session_id, system_id),
            )
            conn.execute(
                """INSERT INTO interview_message
                    (session_id, system_id, role, content, intelligence_run_id, created_at)
                VALUES (?, ?, 'assistant', ?, ?, ?)""",
                (
                    session_id, system_id,
                    interview_message("understanding_update_failed", msg_lang, error=error),
                    run_id, now,
                ),
            )
            row = _get_session_or_404(conn, session_id, system_id)
            return _session_out(row)

        graph = _load_graph_for_snapshot(conn, system_id, snapshot_id)
        if graph is None:
            error = interview_message("graph_not_built", msg_lang)
            conn.execute(
                """UPDATE interview_session
                   SET last_error = ?, updated_at = ?
                   WHERE id = ? AND system_id = ?""",
                (error, now, session_id, system_id),
            )
            conn.execute(
                """INSERT INTO interview_message
                    (session_id, system_id, role, content, created_at)
                VALUES (?, ?, 'assistant', ?, ?)""",
                (
                    session_id, system_id,
                    interview_message("understanding_update_failed", msg_lang, error=error),
                    now,
                ),
            )
            row = _get_session_or_404(conn, session_id, system_id)
            return _session_out(row)

        reconciliation = reconcile(conn, system_id, snapshot_id, graph)

        # Issue #128: feed the interview answers back into the review so
        # the rebuilt understanding reflects what the developer already said.
        history_rows = conn.execute(
            "SELECT role, content FROM interview_message WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        history = _review_history(history_rows)

        # Issue #263: also feed the Q&A-panel answers into the review, same
        # as the conversational interview turn already does — an answer
        # given only via the Q&A panel never becomes an interview_message.
        answered_qa_pairs, unconfirmed_qa_pairs = _load_qa_pairs(
            conn, session_id, system_id
        )

        review = generate_understanding_review(
            client, config,
            graph=graph,
            reconciliation=reconciliation,
            history=history or None,
            answered_qa=answered_qa_pairs,
            unconfirmed_qa=unconfirmed_qa_pairs or None,
        )

        if review.error:
            run_id = _record_review_run(
                "failed", review.error, review.provider, review.model,
                review.is_mock,
            )
            conn.execute(
                """UPDATE interview_session
                   SET last_error = ?, updated_at = ?
                   WHERE id = ? AND system_id = ?""",
                (review.error, now, session_id, system_id),
            )
            conn.execute(
                """INSERT INTO interview_message
                    (session_id, system_id, role, content, intelligence_run_id, created_at)
                VALUES (?, ?, 'assistant', ?, ?, ?)""",
                (
                    session_id, system_id,
                    interview_message("review_failed", msg_lang, error=review.error),
                    run_id, now,
                ),
            )
            row = _get_session_or_404(conn, session_id, system_id)
            return _session_out(row)

        run_id = _record_review_run(
            "completed", None, review.provider, review.model, review.is_mock,
        )

        # Issue #129: register the reviewer's open questions as
        # ID-addressable interview_qa rows (question_source "reviewer") and
        # carry each row's qa_id in the open_questions JSON so the dashboard
        # answers by ID. Questions whose exact text already exists as a
        # current row are not re-inserted (structural exact-text dedupe,
        # e.g. across repeated rebuilds).
        existing_qa_by_text = {
            r["question_text"]: r["id"]
            for r in conn.execute(
                """SELECT id, question_text FROM interview_qa
                   WHERE session_id = ? AND system_id = ?
                     AND superseded_by_id IS NULL
                   ORDER BY id""",
                (session_id, system_id),
            ).fetchall()
        }
        open_question_entries: List[dict] = []
        for q in review.open_questions or []:
            entry = dict(q)
            text = entry.get("question") or ""
            if not text:
                continue
            if text in existing_qa_by_text:
                qa_id = existing_qa_by_text[text]
            else:
                category = entry.get("category")
                if category not in ("purpose", "capability", "api", "probe_flow", "general"):
                    category = "general"
                qa_id = _insert_qa_row(
                    conn, session_id, system_id,
                    question_text=text,
                    question_category=category,
                    question_source="reviewer",
                    hypothesis=None,
                    evidence_refs=[],
                    now=now,
                )
                existing_qa_by_text[text] = qa_id
            entry["qa_id"] = qa_id
            open_question_entries.append(entry)

        understanding_json = json.dumps(review.current_understanding) if review.current_understanding else None
        gap_json = json.dumps(review.gap_analysis) if review.gap_analysis else None
        questions_json = (
            json.dumps(open_question_entries, ensure_ascii=False)
            if open_question_entries else None
        )

        new_stage = _advance_stage(
            session["stage"] or "understanding_initialized",
            "purpose_confirmation",
        )

        conn.execute(
            """UPDATE interview_session
               SET current_understanding = ?, gap_analysis = ?, open_questions = ?,
                   stage = ?, last_error = NULL, answers_revised_at = NULL, updated_at = ?
               WHERE id = ? AND system_id = ?""",
            (understanding_json, gap_json, questions_json, new_stage, now, session_id, system_id),
        )

        # Issue #136: an existing session whose understanding was built before
        # this feature has a current_understanding but no revision rows. Seed
        # that prior state as the initial revision (no run produced it in the
        # revision history, so intelligence_run_id is NULL) *before* appending
        # the new one, so this update's diff has a real "previous" to compare
        # against instead of collapsing to has_previous:false and losing the
        # pre-existing understanding. No blanket backfill of other sessions.
        has_revision = conn.execute(
            """SELECT 1 FROM understanding_revision
               WHERE session_id = ? AND system_id = ? LIMIT 1""",
            (session_id, system_id),
        ).fetchone()
        if has_revision is None and session["current_understanding"]:
            conn.execute(
                """INSERT INTO understanding_revision
                    (session_id, system_id, snapshot_id, intelligence_run_id,
                     current_understanding, gap_analysis, created_at)
                VALUES (?, ?, ?, NULL, ?, ?, ?)""",
                (
                    session_id, system_id, snapshot_id,
                    session["current_understanding"], session["gap_analysis"], now,
                ),
            )

        # Issue #136: append (never overwrite) an understanding revision,
        # linked to the understanding_review run that produced it, so the
        # Dashboard can show a deterministic diff against the previous one.
        conn.execute(
            """INSERT INTO understanding_revision
                (session_id, system_id, snapshot_id, intelligence_run_id,
                 current_understanding, gap_analysis, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, system_id, snapshot_id, run_id, understanding_json, gap_json, now),
        )
        try:
            limit = revision_limit()
        except ValueError:
            limit = DEFAULT_REVISION_LIMIT
        conn.execute(
            """DELETE FROM understanding_revision
               WHERE session_id = ? AND system_id = ? AND id NOT IN (
                   SELECT id FROM understanding_revision
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY id DESC LIMIT ?
               )""",
            (session_id, system_id, session_id, system_id, limit),
        )

        if review.current_understanding:
            unknown_name = interview_message("unknown_name", msg_lang)
            purpose_label = interview_message("system_purpose_label", msg_lang)
            capability_label = interview_message("core_capability_label", msg_lang)
            summary_parts = []
            for purpose in review.current_understanding.get("system_purpose", []):
                summary_parts.append(f"{purpose_label}: {purpose.get('name') or unknown_name}")
            for cap in review.current_understanding.get("core_capabilities", []):
                summary_parts.append(f"{capability_label}: {cap.get('name') or unknown_name}")
            if summary_parts:
                asst_content = (
                    interview_message("understanding_built_intro", msg_lang) + "\n\n"
                    + "\n".join(f"- {p}" for p in summary_parts)
                )
                if review.open_questions:
                    asst_content += (
                        "\n\n" + interview_message("key_questions_heading", msg_lang) + "\n"
                    )
                    for q in review.open_questions[:5]:
                        asst_content += f"- {q.get('question', '')}\n"
                asst_content += (
                    f"\n{interview_message('suggested_next_action_label', msg_lang)}: "
                    f"{review.suggested_next_action}"
                )

                conn.execute(
                    """INSERT INTO interview_message
                        (session_id, system_id, role, content, intelligence_run_id, created_at)
                    VALUES (?, ?, 'assistant', ?, ?, ?)""",
                    (session_id, system_id, asst_content, run_id, now),
                )

        row = _get_session_or_404(conn, session_id, system_id)
        return _session_out(row)


# --- Evidence read audit (Issue #137) ----------------------------------------


@router.get(
    "/interview/evidence-runs/{run_id}/evidence",
    response_model=IntelligenceRunEvidenceListOut,
)
def list_intelligence_run_evidence(
    run_id: int,
    system_id: int = Depends(get_system_id),
) -> IntelligenceRunEvidenceListOut:
    """List every snippet read for a pass-1 evidence-selection run.

    Includes snippets not cited by the resulting question — the read audit
    is independent of citation (Principle 7 raw-fact/interpretation split).
    Scoped by System; the run must also be an interview_evidence_selection
    run for this System, or 404.

    probe-agent:
      role: API boundary for the persisted evidence-read audit
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify reads are listed regardless of citation and are System-isolated
    """
    with get_conn() as conn:
        run_row = conn.execute(
            """SELECT id FROM intelligence_runs
               WHERE id = ? AND system_id = ? AND run_type = 'interview_evidence_selection'""",
            (run_id, system_id),
        ).fetchone()
        if run_row is None:
            raise HTTPException(status_code=404, detail="Evidence-selection run not found")

        rows = conn.execute(
            """SELECT * FROM intelligence_run_evidence
               WHERE intelligence_run_id = ? AND system_id = ?
               ORDER BY id""",
            (run_id, system_id),
        ).fetchall()

    return IntelligenceRunEvidenceListOut(
        intelligence_run_id=run_id,
        system_id=system_id,
        items=[
            IntelligenceRunEvidenceOut(
                id=r["id"],
                system_id=r["system_id"],
                intelligence_run_id=r["intelligence_run_id"],
                path=r["path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                char_count=r["char_count"],
                truncated=bool(r["truncated"]),
                created_at=r["created_at"],
            )
            for r in rows
        ],
    )


# --- Understanding Revisions (Issue #136) ------------------------------------


def _revision_out(row) -> UnderstandingRevisionOut:
    return UnderstandingRevisionOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        intelligence_run_id=row["intelligence_run_id"],
        current_understanding=(
            json.loads(row["current_understanding"]) if row["current_understanding"] else None
        ),
        gap_analysis=json.loads(row["gap_analysis"]) if row["gap_analysis"] else None,
        created_at=row["created_at"],
    )


@router.get(
    "/interview/sessions/{session_id}/understanding-revisions",
    response_model=UnderstandingRevisionListOut,
)
def list_understanding_revisions(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> UnderstandingRevisionListOut:
    """List understanding revisions for a session, newest first.

    probe-agent:
      role: API boundary for listing understanding revisions
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify revisions are System-isolated and appended in order
    """
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            """SELECT * FROM understanding_revision
               WHERE session_id = ? AND system_id = ?
               ORDER BY id DESC""",
            (session_id, system_id),
        ).fetchall()

    return UnderstandingRevisionListOut(
        session_id=session_id,
        system_id=system_id,
        items=[_revision_out(r) for r in rows],
    )


@router.get(
    "/interview/sessions/{session_id}/understanding-diff",
    response_model=UnderstandingDiffOut,
)
def get_understanding_diff(
    session_id: int,
    system_id: int = Depends(get_system_id),
    to: Optional[int] = Query(default=None),
    from_: Optional[int] = Query(default=None, alias="from"),
) -> UnderstandingDiffOut:
    """Deterministic structural diff between two understanding revisions.

    ``to`` defaults to the latest revision; ``from`` defaults to the
    revision immediately before ``to``. When there is no earlier revision to
    compare against (first revision, or no revisions at all), returns
    ``has_previous: false`` with empty sections rather than diffing against
    an empty understanding.

    probe-agent:
      role: API boundary for the deterministic understanding-revision diff
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify the diff is deterministic, name-matched, and System-isolated
    """
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)

        if to is not None:
            to_row = conn.execute(
                """SELECT * FROM understanding_revision
                   WHERE id = ? AND session_id = ? AND system_id = ?""",
                (to, session_id, system_id),
            ).fetchone()
            if to_row is None:
                raise HTTPException(status_code=404, detail="Revision not found (to)")
        else:
            to_row = conn.execute(
                """SELECT * FROM understanding_revision
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (session_id, system_id),
            ).fetchone()
            if to_row is None:
                return UnderstandingDiffOut(
                    session_id=session_id, system_id=system_id, has_previous=False,
                )

        if from_ is not None:
            from_row = conn.execute(
                """SELECT * FROM understanding_revision
                   WHERE id = ? AND session_id = ? AND system_id = ?""",
                (from_, session_id, system_id),
            ).fetchone()
            if from_row is None:
                raise HTTPException(status_code=404, detail="Revision not found (from)")
            if from_row["id"] >= to_row["id"]:
                # A reversed range would silently swap added/removed and
                # invert confidence before/after — reject it instead.
                raise HTTPException(
                    status_code=422,
                    detail="'from' must be an earlier revision than 'to'",
                )
        else:
            from_row = conn.execute(
                """SELECT * FROM understanding_revision
                   WHERE session_id = ? AND system_id = ? AND id < ?
                   ORDER BY id DESC LIMIT 1""",
                (session_id, system_id, to_row["id"]),
            ).fetchone()

    if from_row is None:
        return UnderstandingDiffOut(
            session_id=session_id,
            system_id=system_id,
            to_revision_id=to_row["id"],
            has_previous=False,
        )

    previous = json.loads(from_row["current_understanding"]) if from_row["current_understanding"] else None
    current = json.loads(to_row["current_understanding"]) if to_row["current_understanding"] else None
    sections = diff_understanding(previous, current)

    return UnderstandingDiffOut(
        session_id=session_id,
        system_id=system_id,
        from_revision_id=from_row["id"],
        to_revision_id=to_row["id"],
        has_previous=True,
        sections=[UnderstandingDiffSectionOut(**s) for s in sections],
    )


# --- Runtime Reality Check (Issue #135) --------------------------------------


def _build_runtime_check_items(
    conn, system_id: int, approved_set
) -> List[RuntimeCheckInputItem]:
    """Approved items for this session paired with deterministic trace facts.

    component_id is derived from qualified_name exactly as materialization
    (#71) writes it (``qualified_name.replace(".", "_")``), so aggregation
    matches by construction rather than fuzzy lookup (Principle 6).

    Takes an already-fetched ``approved_set`` (rather than fetching it here)
    because ``get_interview_approved_set`` opens its own ``get_conn()`` and
    the SQLite connection lock is not reentrant — this must never be called
    while ``conn``'s own ``with get_conn()`` block is open.
    """
    items: List[RuntimeCheckInputItem] = []
    for approved in approved_set.items:
        component_id = _component_id_for(approved.qualified_name)
        facts = aggregate_component_facts(conn, system_id, component_id)
        items.append(
            RuntimeCheckInputItem(
                proposal_id=approved.proposal_id,
                decision_id=approved.decision_id,
                path=approved.path,
                qualified_name=approved.qualified_name,
                component_id=component_id,
                role=approved.metadata.role,
                probe_value=approved.metadata.probe_value,
                state_effects=approved.metadata.state_effects,
                recommended_mode=approved.probe_plan.recommended_mode,
                facts=facts,
            )
        )
    return items


@router.get(
    "/interview/sessions/{session_id}/runtime-facts",
    response_model=RuntimeRealityFactsOut,
)
def get_runtime_reality_facts(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> RuntimeRealityFactsOut:
    """Deterministic trace aggregates for the session's approved elements.

    No reasoning model is called here; this is numeric aggregation only
    (Principle 6), scoped to this System's traces.

    probe-agent:
      role: API boundary for deterministic runtime trace aggregation
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify aggregation is System-isolated and deterministic for the same trace set
    """
    approved_set = get_interview_approved_set(session_id, system_id)
    # A misconfigured RUNTIME_REALITY_CHECK_* env var is a client-visible
    # configuration error (422), not an unhandled 500.
    try:
        window = runtime_window_days()
        with get_conn() as conn:
            items = _build_runtime_check_items(conn, system_id, approved_set)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return RuntimeRealityFactsOut(
        session_id=session_id,
        system_id=system_id,
        snapshot_id=approved_set.snapshot_id,
        window_days=window,
        items=items,
    )


@router.post(
    "/interview/sessions/{session_id}/runtime-reality-check",
    response_model=RuntimeRealityCheckRunOut,
)
def run_runtime_reality_check(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> RuntimeRealityCheckRunOut:
    """Reconcile approved metadata/probe plans against runtime trace facts.

    Manual-only trigger (no scheduling). Suppresses re-running while
    unanswered ``question_source: 'runtime'`` questions exist for this
    session (noise control from the issue notes) — that check is
    deterministic and happens before any reasoning call. On both success and
    failure the run is recorded in intelligence_runs; on failure no
    interview_qa rows are created (fail-closed, Principle 6/7).

    probe-agent:
      role: API boundary for the runtime reality check reasoning run
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: orchestration
      state_effects: [database-read, database-write, external-api]
      probe_value: Verify the run fails closed on reasoning errors and is suppressed while unanswered runtime questions remain
    """
    now = time.time()
    approved_set = get_interview_approved_set(session_id, system_id)
    snapshot_id = approved_set.snapshot_id

    # First connection: suppression check + deterministic aggregation only.
    # get_conn() holds the process-wide DB lock, so the reasoning call below
    # must happen after this block closes — holding the lock across an
    # external LLM round trip would stall every other request.
    try:
        with get_conn() as conn:
            open_runtime = conn.execute(
                """SELECT id FROM interview_qa
                   WHERE session_id = ? AND system_id = ?
                     AND question_source = 'runtime' AND status = 'open'
                   LIMIT 1""",
                (session_id, system_id),
            ).fetchone()
            if open_runtime is not None:
                return RuntimeRealityCheckRunOut(
                    session_id=session_id,
                    system_id=system_id,
                    snapshot_id=snapshot_id,
                    skipped=True,
                    skipped_reason=(
                        "Unanswered runtime reality check questions already exist "
                        "for this session; answer or skip them before re-running."
                    ),
                )

            items = _build_runtime_check_items(conn, system_id, approved_set)
    except ValueError as exc:
        # Misconfigured RUNTIME_REALITY_CHECK_* env var: client-visible 422,
        # consistent with the runtime-facts endpoint.
        raise HTTPException(status_code=422, detail=str(exc))

    if not items:
        raise HTTPException(
            status_code=422,
            detail="No approved elements to check against runtime traces",
        )

    config = LLMConfig.intelligence_from_env()
    result: Optional[RuntimeRealityCheckResult] = None
    try:
        client = create_llm_client(config)
        language = get_interview_language()
    except (LLMError, ValueError) as exc:
        result = RuntimeRealityCheckResult(
            provider=config.provider,
            model=config.model,
            is_mock=config.provider == "mock",
            error=str(exc),
        )

    if result is None:
        result = generate_runtime_reality_check(
            client, config, items=items, language=language
        )

    # Second connection: persist the audit run and any generated questions
    # atomically — a mid-loop failure must not leave a completed run with a
    # partial question set (which would also wrongly suppress the next run).
    with get_conn() as conn:
        run_status = "failed" if result.error else "completed"
        conn.execute("BEGIN")
        try:
            run_cur = conn.execute(
                """INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method, status,
                     error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'runtime_reality_check', ?, ?, ?, ?,
                        'reasoning_llm', ?, ?, ?, ?, ?)""",
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
                    now,
                ),
            )
            run_id = run_cur.lastrowid

            by_key = {(i.component_id, i.qualified_name): i for i in items}
            created_qa_ids: List[int] = []
            if not result.error:
                for q in result.questions:
                    item = by_key[(q.component_id, q.qualified_name)]
                    # Raw facts + declared-metadata provenance only. The LLM's
                    # own output (question_text/hypothesis) lives in the
                    # dedicated interview_qa columns, never in this blob
                    # (raw facts vs interpretation separation).
                    runtime_evidence = {
                        "component_id": item.component_id,
                        "qualified_name": item.qualified_name,
                        "path": item.path,
                        "metadata_source": {
                            "proposal_id": item.proposal_id,
                            "decision_id": item.decision_id,
                            "session_id": session_id,
                        },
                        "declared": declared_block(item),
                        "facts": item.facts.model_dump(),
                    }
                    qa_id = _insert_qa_row(
                        conn,
                        session_id,
                        system_id,
                        question_text=q.question_text,
                        question_category="probe_flow",
                        question_source="runtime",
                        hypothesis=q.hypothesis or None,
                        evidence_refs=[],
                        now=now,
                        runtime_evidence=runtime_evidence,
                    )
                    created_qa_ids.append(qa_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        run_row = conn.execute(
            "SELECT * FROM intelligence_runs WHERE id = ?", (run_id,)
        ).fetchone()

    if result.error:
        return RuntimeRealityCheckRunOut(
            session_id=session_id,
            system_id=system_id,
            snapshot_id=snapshot_id,
            intelligence_run=_intelligence_run_out(run_row),
            items_considered=len(items),
            error=result.error,
        )

    return RuntimeRealityCheckRunOut(
        session_id=session_id,
        system_id=system_id,
        snapshot_id=snapshot_id,
        intelligence_run=_intelligence_run_out(run_row),
        items_considered=len(items),
        created_qa_ids=created_qa_ids,
    )
