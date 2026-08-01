"""State-driven System Interview workflow API (Issue #349).

The HTTP surface of `docs/system-interview-workflow-ux.md`. Five endpoints:

* ``GET  /interview/workflow-state`` -- the canonical two-stage evaluation
  (§2.2). The ONLY place the Dashboard learns which of `W0-A` / `W0-B` /
  `W1`..`W7` to show, which single primary action belongs to it, and which
  exceptions are currently blocking or degrading it.
* ``POST /interview/sessions/{id}/diff-review`` -- fact A, the developer's
  explicit "I reviewed this diff" (`decision_method: manual`). Downloading
  the patch never writes it (§4.3).
* ``POST /interview/sessions/{id}/back-requests/{rid}/acknowledge`` -- fact
  D, the developer agreeing to move back into a completed state.
* ``POST /interview/sessions/{id}/close`` / ``.../reopen`` -- `OP-D14`, the
  safe terminal exit and resume. These flip the EXISTING session `status`
  and add a manual audit row; they add no state-decision fact, resolve no
  failure, and pass no gate (§5.3-6).

Human gates are unchanged: this module never confirms an understanding,
never settles an Alignment item, never approves a proposal, never applies a
diff, and never starts an observation. It writes exactly two developer
decisions -- the diff review and the backward acknowledgement -- both as
`decision_method: manual`, and both only when the developer calls them.

probe-agent:
  role: API boundary for the state-driven interview workflow
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: orchestration
  state_effects: [database-read, database-write]
  probe_value: Verify the displayed workflow state, the diff-review record, and the backward acknowledgement are decided and persisted server-side so a reload restores exactly the same state.
"""

from __future__ import annotations

import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_system_id
from ..db import get_conn
from ..interview_workflow import (
    diff_digest,
    evaluate_session_workflow,
    state_rank,
)
from ..models import (
    InterviewBackAcknowledgeIn,
    InterviewBackRequestOut,
    InterviewDiffReviewIn,
    InterviewDiffReviewOut,
    InterviewProcessRunOut,
    InterviewSessionCloseIn,
    InterviewSessionReopenIn,
    InterviewSessionStatusAuditOut,
    InterviewWorkflowExceptionOut,
    InterviewWorkflowFactsOut,
    InterviewWorkflowStateOut,
)

router = APIRouter()


# --- Japanese developer-facing copy ------------------------------------------
#
# Kept next to the finite vocabularies they label (the same convention as
# app/state_messages.py): server-supplied state strings are Japanese and are
# the canonical source of truth for the Dashboard (CLAUDE.md UI language
# rule). Technical identifiers (W2, E3-a, process kinds) stay canonical.

PROCESS_LABELS = {
    "understanding_build": "システム理解を構築しています",
    "understanding_update": "理解を更新しています",
    "alignment_build": "意図とのズレを突き合わせています",
    "question_routing": "質問を分類して調査しています",
    "intent_candidates": "意図の候補を作成しています",
    "proposal_generation": "計測の提案を作成しています",
    "diff_generation": "差分を生成しています",
    "runtime_reality_check": "実態チェックを実行しています",
}

_FAILURE_EXCEPTION_CODE = {
    ("understanding_build", "blocking"): "E3-a",
    ("understanding_update", "blocking"): "E3-a",
    ("understanding_build", "degraded"): "E3-b",
    ("understanding_update", "degraded"): "E3-b",
    ("alignment_build", "blocking"): "E4-a",
    ("alignment_build", "degraded"): "E4-b",
    ("diff_generation", "blocking"): "E12",
    ("diff_generation", "degraded"): "E12",
}

_FAILURE_MESSAGE = {
    "E3-a": "システム理解を構築できず、代わりに提示できる質問もありません。",
    "E3-b": "自動でのシステム理解を構築できませんでした。以降は質問で理解を組み立てます。",
    "E4-a": "意図とのズレの突き合わせに失敗し、確認できる項目がまだ 1 件もありません。",
    "E4-b": "突き合わせの再実行に失敗しました。表示中の項目は前回の結果です。",
    "E12": "差分を生成できませんでした。",
    "E14": "処理に失敗しました。",
}

_RECOVERY_CONDITION = {
    "E3-a": "再試行が成功する、または質問を提示できるようになると通常フローへ戻ります。",
    "E3-b": "以降の理解更新が成功すると解消します。",
    "E4-a": "再試行が成功すると通常フローへ戻ります。",
    "E4-b": "再試行が成功すると解消します。",
    "E12": "原因を解消して差分を生成し直すと通常フローへ戻ります。",
    "E14": "同じ処理の再試行が成功すると解消します。",
}

_BACK_REQUEST_MESSAGE = {
    "blocking_failure": "処理の失敗により、手前の状態で確認が必要になりました。",
    "understanding_recheck": "前提が変わったため、システム理解の確認が必要になりました。",
    "question_reopened": "回答の修正により、確認が必要な質問が発生しました。",
    "alignment_item_added": "意図とのズレに、確認が必要な項目が発生しました。",
    "proposal_review_reopened": "提案に再確認が必要な変更が発生しました。",
    "diff_review_pending": "差分の確認が未完了のまま残っています。",
    "unknown": "手前の状態で確認が必要になりました。",
}


def _acknowledge_back_request_row(conn, request_row, *, actor: str, now: float) -> None:
    """Persist fact D for one backward request and consume it.

    Consuming means lowering the checkpoint to the acknowledged candidate:
    the pure engine then sees a candidate that is no longer earlier than
    `reached_state`, so the displayed state moves without any "was it
    acknowledged?" branch existing anywhere (spec §2.2 stage 2-4/2-5).
    """
    conn.execute(
        """INSERT INTO interview_back_acknowledgement
               (back_request_id, session_id, system_id, actor,
                decision_method, created_at)
           VALUES (?, ?, ?, ?, 'manual', ?)""",
        (
            request_row["id"],
            request_row["session_id"],
            request_row["system_id"],
            actor,
            now,
        ),
    )
    conn.execute(
        "UPDATE interview_back_request SET status = 'acknowledged', updated_at = ? WHERE id = ?",
        (now, request_row["id"]),
    )
    candidate = request_row["candidate_state"]
    if state_rank(candidate) >= 0:
        conn.execute(
            """INSERT INTO interview_workflow_checkpoint
                   (session_id, system_id, reached_state, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   reached_state = excluded.reached_state,
                   updated_at = excluded.updated_at""",
            (request_row["session_id"], request_row["system_id"], candidate, now),
        )


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _run_out(row: dict) -> InterviewProcessRunOut:
    return InterviewProcessRunOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        process_kind=row["process_kind"],
        status=row["status"],
        failure_class=row["failure_class"],
        target_state=row["target_state"],
        error=row["error"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _back_request_out(row: dict) -> InterviewBackRequestOut:
    return InterviewBackRequestOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        cause_kind=row["cause_kind"],
        candidate_state=row["candidate_state"],
        reached_state=row["reached_state"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _build_exceptions(decision, gathered, pending_back_request) -> List[InterviewWorkflowExceptionOut]:
    """Only exceptions that are CURRENTLY blocking or degrading, plus the
    informational branches the primary work card has to render (spec §5.3-4:
    a past error that no longer affects the current work never appears here).
    """
    out: List[InterviewWorkflowExceptionOut] = []
    facts = gathered.facts

    if not facts.has_snapshot:
        out.append(
            InterviewWorkflowExceptionOut(
                code="E1",
                severity="blocking",
                target_state="W0-A",
                message="対象リポジトリの snapshot がまだありません。",
                recovery_condition="Repository 画面で snapshot を作成すると開始できます。",
            )
        )

    if gathered.snapshot_stale:
        out.append(
            InterviewWorkflowExceptionOut(
                code="E2",
                severity="blocking",
                target_state=decision.state,
                message="Repository の HEAD がセッションの snapshot より進んでいます。",
                detail="これまでの回答・確定は保持されます。",
                recovery_condition="新しい snapshot に更新すると解消します。",
            )
        )

    for row in gathered.blocking_failures + gathered.degraded_failures:
        failure_class = row["failure_class"]
        code = _FAILURE_EXCEPTION_CODE.get((row["process_kind"], failure_class), "E14")
        out.append(
            InterviewWorkflowExceptionOut(
                code=code,
                severity=failure_class,
                target_state=row["target_state"],
                message=_FAILURE_MESSAGE.get(code, _FAILURE_MESSAGE["E14"]),
                detail=row["error"],
                recovery_process_kind=row["process_kind"],
                recovery_condition=_RECOVERY_CONDITION.get(
                    code, _RECOVERY_CONDITION["E14"]
                ),
            )
        )

    if pending_back_request is not None:
        cause = pending_back_request["cause_kind"]
        out.append(
            InterviewWorkflowExceptionOut(
                code="E8",
                severity="informational",
                target_state=pending_back_request["candidate_state"],
                message=_BACK_REQUEST_MESSAGE.get(
                    cause, _BACK_REQUEST_MESSAGE["unknown"]
                ),
                recovery_condition="「先に確認する」を選ぶと該当の状態へ戻ります。",
            )
        )

    if gathered.proposals_needing_rereview > 0:
        out.append(
            InterviewWorkflowExceptionOut(
                code="E9",
                severity="informational",
                target_state="W5",
                message=(
                    f"{gathered.proposals_needing_rereview} 件の提案が再レビュー対象になりました。"
                ),
                recovery_condition="対象の提案を確定し直すと解消します。",
            )
        )

    return out


def _state_out(conn, system_id: int, session_id: Optional[int]) -> InterviewWorkflowStateOut:
    decision, gathered, pending = evaluate_session_workflow(conn, system_id, session_id)
    facts = gathered.facts
    return InterviewWorkflowStateOut(
        system_id=system_id,
        session_id=(gathered.session["id"] if gathered.session else None),
        state=decision.state,
        candidate_state=decision.candidate_state,
        rule_row=decision.rule_row,
        # The checkpoint as it stands AFTER this evaluation persisted it --
        # "the judgement state currently reached", not the pre-update value.
        reached_state=(decision.next_reached_state or decision.reached_state),
        backward_hold=decision.backward_hold,
        pending_back_request=(_back_request_out(pending) if pending else None),
        terminal_kind=decision.terminal_kind,
        primary_action=decision.primary_action,
        facts=InterviewWorkflowFactsOut(
            has_snapshot=facts.has_snapshot,
            has_session=facts.has_session,
            session_closed=facts.session_closed,
            running_process_kinds=list(facts.running_process_kinds),
            blocking_failure_states=sorted(facts.blocking_failure_states),
            understanding_unconfirmed=facts.understanding_unconfirmed,
            open_required_questions=facts.open_required_questions,
            outstanding_alignment_items=facts.outstanding_alignment_items,
            proposals_needing_review=facts.proposals_needing_review,
            proposals_generatable=facts.proposals_generatable,
            approved_proposal_count=facts.approved_proposal_count,
            diff_matches_approval_set=facts.diff_matches_approval_set,
            diff_review_complete=facts.diff_review_complete,
            pending_handoff_count=facts.pending_handoff_count,
        ),
        running_processes=[_run_out(r) for r in gathered.running_runs],
        unresolved_failures=[
            _run_out(r) for r in gathered.blocking_failures + gathered.degraded_failures
        ],
        exceptions=_build_exceptions(decision, gathered, pending),
        diff_materialized_at=(
            gathered.session["materialized_at"] if gathered.session else None
        ),
        latest_ready_snapshot_id=gathered.latest_ready_snapshot_id,
    )


@router.get("/interview/workflow-state", response_model=InterviewWorkflowStateOut)
def get_interview_workflow_state(
    session_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> InterviewWorkflowStateOut:
    """Evaluate the canonical two-stage workflow state (spec §2.2).

    `session_id` is optional on purpose: `W0-A` (no snapshot) and `W0-B` (no
    session selected) are page-level states that exist before any session
    does. A `session_id` that does not belong to this System is treated the
    same as none selected -- the state is always evaluated against facts this
    System actually owns.
    """
    with get_conn() as conn:
        return _state_out(conn, system_id, session_id)


@router.post(
    "/interview/sessions/{session_id}/diff-review",
    response_model=InterviewDiffReviewOut,
    status_code=201,
)
def record_interview_diff_review(
    session_id: int,
    payload: InterviewDiffReviewIn,
    system_id: int = Depends(get_system_id),
) -> InterviewDiffReviewOut:
    """Record fact A -- the developer confirmed THIS diff (`W6` completion).

    Bound to the diff it reviewed via `interview_session.materialized_at`, so
    regenerating the diff never inherits the previous confirmation. This is
    the `W6` primary action; downloading the patch or opening the diff view
    deliberately does not write it (spec §4.3, §8.1 fact A).
    """
    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        materialized_at = session["materialized_at"]
        if materialized_at is None or not session["materialization_diff"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "no_diff_to_review",
                    "message": "確認できる差分がまだ生成されていません。",
                },
            )
        existing = conn.execute(
            """SELECT * FROM interview_diff_review
               WHERE session_id = ? AND system_id = ? AND diff_materialized_at = ?
               ORDER BY id DESC LIMIT 1""",
            (session_id, system_id, materialized_at),
        ).fetchone()
        if existing is not None:
            return InterviewDiffReviewOut(**dict(existing))
        digest = diff_digest(session["materialization_diff"])
        cur = conn.execute(
            """INSERT INTO interview_diff_review
                   (session_id, system_id, diff_materialized_at, diff_digest,
                    reviewed_by, decision_method, note, created_at)
               VALUES (?, ?, ?, ?, ?, 'manual', ?, ?)""",
            (
                session_id,
                system_id,
                materialized_at,
                digest,
                payload.reviewed_by,
                payload.note,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM interview_diff_review WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        # Re-evaluate so the checkpoint advances to W7 in the same request the
        # developer completed W6 in (a system-recorded progress fact only).
        evaluate_session_workflow(conn, system_id, session_id)
        return InterviewDiffReviewOut(**dict(row))


@router.post(
    "/interview/sessions/{session_id}/back-requests/{request_id}/acknowledge",
    response_model=InterviewWorkflowStateOut,
)
def acknowledge_back_request(
    session_id: int,
    request_id: int,
    payload: InterviewBackAcknowledgeIn,
    system_id: int = Depends(get_system_id),
) -> InterviewWorkflowStateOut:
    """Record fact D and let the display move back (spec §2.2 stage 2-4).

    The acknowledgement is per request: it lowers the checkpoint to exactly
    the candidate state that request named, and is never reused for a later
    backward request (stage 2-5).
    """
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        request_row = conn.execute(
            """SELECT * FROM interview_back_request
               WHERE id = ? AND session_id = ? AND system_id = ?""",
            (request_id, session_id, system_id),
        ).fetchone()
        if request_row is None:
            raise HTTPException(status_code=404, detail="Back request not found")
        if request_row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "back_request_not_pending",
                    "message": (
                        f"この戻り要求は既に '{request_row['status']}' です。"
                    ),
                },
            )
        _acknowledge_back_request_row(
            conn, request_row, actor=payload.actor, now=now
        )
        return _state_out(conn, system_id, session_id)


@router.post(
    "/interview/sessions/{session_id}/close",
    response_model=InterviewWorkflowStateOut,
)
def close_interview_session(
    session_id: int,
    payload: InterviewSessionCloseIn,
    system_id: int = Depends(get_system_id),
) -> InterviewWorkflowStateOut:
    """`OP-D14` -- leave safely to the suspend / handoff terminal (§5.3-6).

    Flips the existing session `status` to `closed` and records the manual
    audit. It deliberately does NOT resolve any blocking failure and does not
    advance `reached_state`: suspending is not progress, and reopening must
    surface the same unresolved failures again.
    """
    if payload.terminal_kind not in ("suspended", "handoff"):
        raise HTTPException(
            status_code=422,
            detail="terminal_kind must be 'suspended' or 'handoff'",
        )
    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        if session["status"] == "closed":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "session_already_closed",
                    "message": "このセッションは既に終了しています。",
                },
            )
        conn.execute(
            "UPDATE interview_session SET status = 'closed', updated_at = ? WHERE id = ? AND system_id = ?",
            (now, session_id, system_id),
        )
        conn.execute(
            """INSERT INTO interview_session_status_audit
                   (session_id, system_id, action, terminal_kind, reason, actor,
                    decision_method, created_at)
               VALUES (?, ?, 'close', ?, ?, ?, 'manual', ?)""",
            (
                session_id,
                system_id,
                payload.terminal_kind,
                payload.reason,
                payload.actor,
                now,
            ),
        )
        return _state_out(conn, system_id, session_id)


@router.post(
    "/interview/sessions/{session_id}/reopen",
    response_model=InterviewWorkflowStateOut,
)
def reopen_interview_session(
    session_id: int,
    payload: InterviewSessionReopenIn,
    system_id: int = Depends(get_system_id),
) -> InterviewWorkflowStateOut:
    """`OP-D14` -- resume a suspended/handed-off session (§5.4 terminal 3).

    Restores `status = 'open'` and re-evaluates §2.2 normally, which brings
    any still-unresolved blocking failure back into view. A suspend never
    counted as passing a gate, so nothing is skipped on the way back in.
    """
    now = time.time()
    with get_conn() as conn:
        session = _get_session_or_404(conn, session_id, system_id)
        if session["status"] != "closed":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "session_not_closed",
                    "message": "このセッションは終了していません。",
                },
            )
        conn.execute(
            "UPDATE interview_session SET status = 'open', updated_at = ? WHERE id = ? AND system_id = ?",
            (now, session_id, system_id),
        )
        conn.execute(
            """INSERT INTO interview_session_status_audit
                   (session_id, system_id, action, terminal_kind, reason, actor,
                    decision_method, created_at)
               VALUES (?, ?, 'reopen', NULL, ?, ?, 'manual', ?)""",
            (session_id, system_id, payload.reason, payload.actor, now),
        )
        # Spec §5.4 terminal 3: 「再開する」 is ONE operation that returns to the
        # earliest resumable state. When a backward request was pending when
        # the session was suspended, resuming is itself the developer agreeing
        # to go back, so the acknowledgement (fact D) is recorded here rather
        # than making them press 「先に確認する」 immediately afterwards.
        # It stays a per-request acknowledgement: only the request that is
        # pending right now is consumed, and a backward request raised later
        # is held again from scratch (§2.2 stage 2-5).
        pending = conn.execute(
            """SELECT * FROM interview_back_request
               WHERE session_id = ? AND system_id = ? AND status = 'pending'
               ORDER BY id DESC LIMIT 1""",
            (session_id, system_id),
        ).fetchone()
        if pending is not None:
            _acknowledge_back_request_row(
                conn, pending, actor=payload.actor, now=now
            )
        return _state_out(conn, system_id, session_id)


@router.get(
    "/interview/sessions/{session_id}/status-audit",
    response_model=List[InterviewSessionStatusAuditOut],
)
def list_session_status_audit(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> List[InterviewSessionStatusAuditOut]:
    """Audit trail of `OP-D14` suspend/resume decisions (`R6`)."""
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            """SELECT * FROM interview_session_status_audit
               WHERE session_id = ? AND system_id = ? ORDER BY id DESC""",
            (session_id, system_id),
        ).fetchall()
        return [InterviewSessionStatusAuditOut(**dict(r)) for r in rows]


@router.get(
    "/interview/sessions/{session_id}/process-runs",
    response_model=List[InterviewProcessRunOut],
)
def list_interview_process_runs(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> List[InterviewProcessRunOut]:
    """Full run history for this session (`R6` -- history and audit only).

    The current-work surface reads `GET /interview/workflow-state`, which
    exposes only what is running now and what is still unresolved.
    """
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            """SELECT * FROM interview_process_run
               WHERE session_id = ? AND system_id = ? ORDER BY id DESC""",
            (session_id, system_id),
        ).fetchall()
        return [_run_out(dict(r)) for r in rows]
