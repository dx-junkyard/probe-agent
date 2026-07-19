"""Alignment Review / Review Queue API (Issue #287).

Contrasts confirmed/proposed Intent Brief items (Issue #284,
``interview_intent_item``) against the evidence-backed Current System
understanding (Issue #136, the latest ``understanding_revision``) and
produces "alignment items": one row per contrast point, each with a
DETERMINISTIC review classification (``review_category`` / ``reason_code``,
see ``app/alignment.py``'s rule table -- classification itself is never a
reasoning decision, Principle 6). Only ``review_category IN (must_review,
batch_reviewable)`` ever surfaces as an action-required Review Queue card;
the rest (``no_review_required`` / ``unchanged`` / ``informational``) are
collapsed/informational.

Kept in its own module (like Issues #284/#285) rather than growing
``routes/interview.py`` further, per CLAUDE.md's guidance for this
sub-issue.

Rebuild-merge (POST .../alignment/build): a build DELETEs and recreates only
rows with ``status='open' AND user_decision IS NULL`` for the session --
untouched suggestions with no user progress. Any row with a different status
(``answered``/``corrected``/``held``/``inquiry``) or a recorded
``user_decision`` is always kept, regardless of how the base revision
changed (Principle 2 -- a rebuild must never lose a human decision).

Inquiry integration (Issue #285's ``origin_kind='review_item'``) lives in
``routes/interview_inquiry.py``: opening an Inquiry on an alignment item sets
its ``status='inquiry'``; the Inquiry closing (resolved/unresolved/cancelled)
sets it back to ``'open'`` -- never ``'answered'``, so the developer must
still explicitly call one of this module's answer/correct/hold endpoints
(Principle 2).

probe-agent:
  role: API boundary for the Alignment Review / Review Queue (Intent vs
    Current System contrast, deterministic review classification)
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify a rebuild never deletes/overwrites an alignment_item with user progress, that the review-queue endpoint only ever returns must_review/batch_reviewable items in deterministic order, and that build fails closed (no rows written) when every proposed item's evidence fails snapshot validation.
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..alignment import (
    AlignmentProposalResult,
    classify_alignment_item,
    generate_alignment_proposal,
    review_sort_key,
    user_reason_for,
    validate_evidence_against_snapshot,
)
from ..auth import get_system_id
from ..db import get_conn
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import (
    AlignmentAnswerRequest,
    AlignmentBuildOut,
    AlignmentCorrectRequest,
    AlignmentEvidenceOut,
    AlignmentItemOut,
    AlignmentListOut,
    AlignmentReviewQueueOut,
    AlignmentUserDecisionOut,
)

router = APIRouter()

# Only these two categories ever surface as action-required Review Queue
# cards (Principle 6 -- a fixed finite subset, not a heuristic filter).
_ACTIONABLE_CATEGORIES = ("must_review", "batch_reviewable")


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _get_item_or_404(conn, item_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM alignment_item WHERE id = ? AND system_id = ?",
        (item_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Alignment item not found")
    return row


def _item_out(row) -> AlignmentItemOut:
    evidence = json.loads(row["current_evidence"]) if row["current_evidence"] else []
    risk_flags = json.loads(row["risk_flags"]) if row["risk_flags"] else []
    user_decision = json.loads(row["user_decision"]) if row["user_decision"] else None
    return AlignmentItemOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        revision_id=row["revision_id"],
        snapshot_id=row["snapshot_id"],
        intent_item_id=row["intent_item_id"],
        intent_summary=row["intent_summary"],
        current_claim=row["current_claim"],
        current_evidence=[AlignmentEvidenceOut(**e) for e in evidence],
        gap_summary=row["gap_summary"],
        proposed_interpretation=row["proposed_interpretation"],
        alignment_state=row["alignment_state"],
        risk_flags=risk_flags,
        confidence=row["confidence"],
        review_category=row["review_category"],
        reason_code=row["reason_code"],
        user_reason=row["user_reason"],
        status=row["status"],
        user_decision=AlignmentUserDecisionOut(**user_decision) if user_decision else None,
        intelligence_run_id=row["intelligence_run_id"],
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _sorted_items(rows) -> List[AlignmentItemOut]:
    items = [_item_out(r) for r in rows]
    items.sort(key=lambda it: review_sort_key(
        review_category=it.review_category, reason_code=it.reason_code, item_id=it.id,
    ))
    return items


# --- Build ---------------------------------------------------------------------


def run_alignment_build(conn, session_id: int, system_id: int) -> AlignmentBuildOut:
    """Build alignment items contrasting Intent Brief vs Current System.

    Core of ``POST .../alignment/build`` (below), extracted so the automatic
    refresh job (``app/interview_refresh.py``, Issue #288) can rebuild
    Alignment on the exact same code path right after Understanding is
    rebuilt, instead of duplicating this orchestration.

    Requires at least one ``understanding_revision`` for this session (409
    otherwise -- build/refresh System Understanding first; no reasoning call
    is attempted and no ``intelligence_runs`` row is created for this
    precondition, mirroring how a missing session/item is a plain 404
    elsewhere in this API surface). Once a reasoning attempt is made, every
    outcome (success or failure) is recorded as an ``intelligence_runs``
    row (``run_type='alignment_build'``).

    Fail-closed (Principle 6): LLM/schema failures, or every proposed item's
    evidence failing snapshot validation, both raise ``HTTPException`` (502)
    with no ``alignment_item`` rows written or replaced. See module
    docstring for the rebuild-merge rule. Callers that want to treat a
    failure as non-fatal (the refresh job does, for the Alignment step
    specifically) must catch ``HTTPException`` themselves.
    """
    now = time.time()
    _get_session_or_404(conn, session_id, system_id)

    revision = conn.execute(
        """SELECT * FROM understanding_revision
           WHERE session_id = ? AND system_id = ?
           ORDER BY id DESC LIMIT 1""",
        (session_id, system_id),
    ).fetchone()
    if revision is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No understanding revision found for this session; "
                "build/refresh System Understanding first."
            ),
        )

    snapshot_row = conn.execute(
        "SELECT repo_path, commit_sha FROM repository_snapshots WHERE id = ? AND system_id = ?",
        (revision["snapshot_id"], system_id),
    ).fetchone()
    if snapshot_row is None:
        raise HTTPException(
            status_code=409,
            detail="The pinned snapshot for this session's latest understanding revision no longer exists.",
        )

    intent_rows = conn.execute(
        """SELECT * FROM interview_intent_item
           WHERE session_id = ? AND system_id = ? AND superseded_by_id IS NULL
           ORDER BY field, id""",
        (session_id, system_id),
    ).fetchall()
    intent_payload = [
        {
            "id": r["id"], "field": r["field"], "value_text": r["value_text"],
            "status": r["status"],
        }
        for r in intent_rows
    ]
    # Deterministic FK resolution (Principle 6): the reasoning model only
    # proposes a *field name*; the concrete interview_intent_item id is
    # resolved here by an exact structural match against the current
    # (non-superseded) row for that field -- never a fuzzy/text match.
    intent_item_id_by_field: Dict[str, int] = {}
    for r in intent_rows:
        intent_item_id_by_field.setdefault(r["field"], r["id"])

    current_understanding = (
        json.loads(revision["current_understanding"]) if revision["current_understanding"] else None
    )
    gap_analysis = json.loads(revision["gap_analysis"]) if revision["gap_analysis"] else None

    config = LLMConfig.intelligence_from_env()
    client_error: Optional[str] = None
    try:
        client = create_llm_client(config)
    except LLMError as exc:
        client = None
        client_error = str(exc)

    if client_error is not None:
        proposal = AlignmentProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=config.provider == "mock",
            error=client_error,
        )
    else:
        proposal = generate_alignment_proposal(
            client, config,
            intent_items=intent_payload,
            current_understanding=current_understanding,
            gap_analysis=gap_analysis,
        )

    completed_at = time.time()
    final_items: List[dict] = []
    if proposal.error is None:
        line_count_cache: Dict[str, Optional[int]] = {}
        had_raw_items = len(proposal.items) > 0
        for item in proposal.items:
            valid_evidence, _pruned = validate_evidence_against_snapshot(
                snapshot_row["repo_path"], snapshot_row["commit_sha"],
                item.evidence, line_count_cache,
            )
            if not valid_evidence:
                # Dropped, not fatal on its own -- see the "fail only if
                # none valid" rule below.
                continue
            review_category, reason_code = classify_alignment_item(
                alignment_state=item.alignment_state,
                risk_flags=item.risk_flags,
                confidence=item.confidence,
                intent_field=item.intent_field,
            )
            final_items.append({
                "intent_item_id": intent_item_id_by_field.get(item.intent_field)
                    if item.intent_field else None,
                "intent_summary": item.intent_ref_hint,
                "current_claim": item.current_claim,
                "current_evidence": [
                    {"path": e.path, "start_line": e.start_line, "end_line": e.end_line, "summary": e.summary}
                    for e in valid_evidence
                ],
                "gap_summary": item.gap_summary,
                "proposed_interpretation": item.proposed_interpretation,
                "alignment_state": item.alignment_state,
                "risk_flags": item.risk_flags,
                "confidence": item.confidence,
                "review_category": review_category,
                "reason_code": reason_code,
            })
        if had_raw_items and not final_items:
            proposal = AlignmentProposalResult(
                provider=proposal.provider, model=proposal.model, is_mock=proposal.is_mock,
                error="Every proposed alignment item's evidence failed snapshot validation",
            )

    run_status = "failed" if proposal.error else "completed"
    run_cur = conn.execute(
        """
        INSERT INTO intelligence_runs
            (system_id, snapshot_id, run_type, provider, model,
             prompt_version, schema_version, decision_method, status,
             error_details, is_mock, started_at, completed_at)
        VALUES (?, ?, 'alignment_build', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)
        """,
        (
            system_id, revision["snapshot_id"], proposal.provider, proposal.model,
            proposal.prompt_version, proposal.schema_version, run_status, proposal.error,
            1 if proposal.is_mock else 0, now, completed_at,
        ),
    )
    run_id = run_cur.lastrowid

    if proposal.error:
        raise HTTPException(status_code=502, detail=proposal.error)

    conn.execute("BEGIN")
    try:
        # Rebuild-merge (Principle 2): only rows with no user progress
        # are ever replaced.
        conn.execute(
            """DELETE FROM alignment_item
               WHERE session_id = ? AND system_id = ?
                 AND status = 'open' AND user_decision IS NULL""",
            (session_id, system_id),
        )
        for it in final_items:
            reason_code = it["reason_code"]
            conn.execute(
                """INSERT INTO alignment_item
                    (session_id, system_id, revision_id, snapshot_id, intent_item_id,
                     intent_summary, current_claim, current_evidence, gap_summary,
                     proposed_interpretation, alignment_state, risk_flags, confidence,
                     review_category, reason_code, user_reason, status, user_decision,
                     intelligence_run_id, is_mock, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL, ?, ?, ?, ?)""",
                (
                    session_id, system_id, revision["id"], revision["snapshot_id"],
                    it["intent_item_id"], it["intent_summary"], it["current_claim"],
                    json.dumps(it["current_evidence"], ensure_ascii=False),
                    it["gap_summary"], it["proposed_interpretation"], it["alignment_state"],
                    json.dumps(it["risk_flags"]), it["confidence"], it["review_category"],
                    reason_code, user_reason_for(reason_code), run_id,
                    1 if proposal.is_mock else 0, completed_at, completed_at,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    rows = conn.execute(
        "SELECT * FROM alignment_item WHERE session_id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchall()
    return AlignmentBuildOut(
        session_id=session_id,
        system_id=system_id,
        revision_id=revision["id"],
        intelligence_run_id=run_id,
        is_mock=proposal.is_mock,
        items=_sorted_items(rows),
    )


@router.post(
    "/interview/sessions/{session_id}/alignment/build",
    response_model=AlignmentBuildOut,
)
def build_alignment_items(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> AlignmentBuildOut:
    """API boundary for ``run_alignment_build`` -- see its docstring."""
    with get_conn() as conn:
        return run_alignment_build(conn, session_id, system_id)


# --- Read ------------------------------------------------------------------


@router.get(
    "/interview/sessions/{session_id}/alignment",
    response_model=AlignmentListOut,
)
def list_alignment_items(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> AlignmentListOut:
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            "SELECT * FROM alignment_item WHERE session_id = ? AND system_id = ?",
            (session_id, system_id),
        ).fetchall()
        items = _sorted_items(rows)
        items_by_category: Dict[str, List[AlignmentItemOut]] = {c: [] for c in (
            "must_review", "batch_reviewable", "no_review_required", "unchanged", "informational",
        )}
        counts: Dict[str, int] = {c: 0 for c in items_by_category}
        for item in items:
            items_by_category.setdefault(item.review_category, []).append(item)
            counts[item.review_category] = counts.get(item.review_category, 0) + 1
        return AlignmentListOut(
            session_id=session_id, system_id=system_id,
            items_by_category=items_by_category, counts=counts,
        )


@router.get(
    "/interview/sessions/{session_id}/review-queue",
    response_model=AlignmentReviewQueueOut,
)
def get_review_queue(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> AlignmentReviewQueueOut:
    """Only action-required items (must_review + batch_reviewable), ordered
    deterministically by category rank, then reason-code rank, then id."""
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        placeholders = ",".join("?" for _ in _ACTIONABLE_CATEGORIES)
        rows = conn.execute(
            f"""SELECT * FROM alignment_item
                WHERE session_id = ? AND system_id = ? AND review_category IN ({placeholders})""",
            (session_id, system_id, *_ACTIONABLE_CATEGORIES),
        ).fetchall()
        return AlignmentReviewQueueOut(
            session_id=session_id, system_id=system_id, items=_sorted_items(rows),
        )


# --- Manual decisions --------------------------------------------------------


def _reject_if_inquiry_locked(item) -> None:
    if item["status"] == "inquiry":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "alignment_item_inquiry_open",
                "message": "This item has an open Inquiry; resolve it before answering.",
            },
        )


@router.post(
    "/interview/alignment/{item_id}/answer",
    response_model=AlignmentItemOut,
)
def answer_alignment_item(
    item_id: int,
    payload: AlignmentAnswerRequest,
    system_id: int = Depends(get_system_id),
) -> AlignmentItemOut:
    """Manual decision on an alignment item (Principle 2 -- never automatic).

    Server never auto-sets ``user_decision`` -- this endpoint (plus
    ``/correct`` and ``/hold`` below) is the only write path for it.
    """
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        _reject_if_inquiry_locked(item)
        decision = {
            "action": payload.decision, "note": payload.note,
            "decided_at": now, "decided_by": None,
        }
        conn.execute(
            """UPDATE alignment_item
               SET status = 'answered', user_decision = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(decision, ensure_ascii=False), now, item_id),
        )
        row = conn.execute("SELECT * FROM alignment_item WHERE id = ?", (item_id,)).fetchone()
        result = _item_out(row)

    # Issue #288: refresh Understanding/Alignment/Review Queue now that the
    # decision is durably committed (see interview.answer_interview_qa's
    # comment for why this call sits outside the `with get_conn()` block).
    from ..interview_refresh import request_refresh

    request_refresh(result.session_id, system_id, "alignment_answer")
    return result


@router.post(
    "/interview/alignment/{item_id}/correct",
    response_model=AlignmentItemOut,
)
def correct_alignment_item(
    item_id: int,
    payload: AlignmentCorrectRequest,
    system_id: int = Depends(get_system_id),
) -> AlignmentItemOut:
    """Record the developer's own corrected interpretation.

    Unlike Intent Brief's /correct, this does not create a new revision row
    -- the alignment item itself stays the audit record, with the
    correction stored as ``user_decision.note`` (the LLM's own
    ``proposed_interpretation`` is left untouched alongside it for
    comparison).
    """
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        _reject_if_inquiry_locked(item)
        decision = {
            "action": "corrected", "note": payload.corrected_interpretation,
            "decided_at": now, "decided_by": None,
        }
        conn.execute(
            """UPDATE alignment_item
               SET status = 'corrected', user_decision = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(decision, ensure_ascii=False), now, item_id),
        )
        row = conn.execute("SELECT * FROM alignment_item WHERE id = ?", (item_id,)).fetchone()
        result = _item_out(row)

    # Issue #288: see answer_alignment_item's comment above.
    from ..interview_refresh import request_refresh

    request_refresh(result.session_id, system_id, "alignment_answer")
    return result


@router.post(
    "/interview/alignment/{item_id}/hold",
    response_model=AlignmentItemOut,
)
def hold_alignment_item(
    item_id: int,
    system_id: int = Depends(get_system_id),
) -> AlignmentItemOut:
    now = time.time()
    with get_conn() as conn:
        item = _get_item_or_404(conn, item_id, system_id)
        _reject_if_inquiry_locked(item)
        decision = {"action": "held", "note": None, "decided_at": now, "decided_by": None}
        conn.execute(
            """UPDATE alignment_item
               SET status = 'held', user_decision = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(decision, ensure_ascii=False), now, item_id),
        )
        row = conn.execute("SELECT * FROM alignment_item WHERE id = ?", (item_id,)).fetchone()
        return _item_out(row)
