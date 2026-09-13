"""Canonical Understanding head and Interview premise API (Issue #464).

The ownership boundary this Issue introduces, exposed as endpoints:

* ``GET /understanding-head`` -- the System's canonical Understanding. The
  ONE answer to 「この System は今なにを理解していることになっているか」.
  ``revision_id: null`` means no human has promoted anything yet, which is a
  real answer and never an invitation to fall back to the newest session.
* ``GET /interview/sessions/{id}/premise`` -- whether that conversation may
  still be continued as-is, plus the finite set of actions when it may not.
* ``POST .../premise/rebase`` | ``/branch`` | ``/adopt-current`` -- the three
  explicit choices. None of them rewrites the original conversation or its
  premise (#464 non-goal 2).
* ``POST .../promote-understanding`` -- the human confirmation that moves the
  head, with a compare-and-swap expectation. A promotion made against a head
  that has since moved is REJECTED and audited, never merged.
* ``GET /understanding-canonical-events`` / ``-metrics`` -- the audit trail
  and the derived counts for premise mismatch, stale sessions and promotion
  conflicts.

probe-agent:
  role: API boundary for the canonical Understanding head and Interview premise
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard, control-server]
  operation_kind: write
  state_effects: [database-read, database-write]
  probe_value: Verify the head moves only through an explicit human promotion with a compare-and-swap, that a stale premise is refused rather than merged, and that every refusal is auditable.
"""

from __future__ import annotations

import json
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import canonical_understanding as canonical
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    InterviewPremiseDecisionRequest,
    InterviewPremiseOut,
    InterviewPremiseRebaseOut,
    UnderstandingCanonicalEventListOut,
    UnderstandingCanonicalEventOut,
    UnderstandingCanonicalMetricsOut,
    UnderstandingHeadOut,
    UnderstandingPromotionOut,
    UnderstandingPromotionRequest,
)

router = APIRouter()


def _actor(principal: Principal) -> str:
    """Who stands behind a manual decision (Principle 7's actor convention)."""
    return principal.username or f"user:{principal.user_id}"


def _session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _latest_candidate_revision_id(conn, session_id: int, system_id: int) -> Optional[int]:
    """The newest promotable revision of this session.

    ``candidate`` only: an already-canonical or superseded revision is not a
    pending proposal, and offering it as one would let a developer "confirm"
    something that was decided long ago.
    """
    row = conn.execute(
        """SELECT id FROM understanding_revision
           WHERE session_id = ? AND system_id = ?
             AND COALESCE(status, 'candidate') = 'candidate'
             AND current_understanding IS NOT NULL
           ORDER BY id DESC LIMIT 1""",
        (session_id, system_id),
    ).fetchone()
    return row["id"] if row else None


def _available_actions(verdict: canonical.PremiseVerdict) -> List[str]:
    """The finite set of moves offered for this premise state.

    Never a free-form suggestion and never empty for a blocked session: a
    developer who cannot continue must always be able to see the exact ways
    out (#464 required behaviour for resume).
    """
    if verdict.state == "current":
        return ["continue"]
    if verdict.disposition == "rebased":
        # The successor session is where the conversation moved to.
        return ["open_successor_session"]
    if verdict.state == "invalid":
        # A legacy session never recorded what it stood on, so re-pinning it
        # explicitly is the honest repair -- alongside the normal choices.
        return ["adopt_current_premise", "start_new_session", "branch"]
    return ["start_new_session", "rebase", "branch"]


def premise_out(conn, session_row, system_id: int) -> InterviewPremiseOut:
    verdict = canonical.evaluate_session_premise(conn, session_row)
    return InterviewPremiseOut(
        session_id=session_row["id"],
        system_id=system_id,
        state=verdict.state,
        reason_code=verdict.reason_code,
        message=verdict.message,
        label=verdict.label,
        continuable=verdict.continuable,
        disposition=verdict.disposition,
        disposition_label=canonical.PREMISE_DISPOSITION_LABELS[verdict.disposition],
        base_revision_id=verdict.base_revision_id,
        base_premise_digest=verdict.base_premise_digest,
        head_revision_id=verdict.head_revision_id,
        head_premise_digest=verdict.head_premise_digest,
        head_version=verdict.head_version,
        origin_kind=verdict.origin_kind,
        origin_session_id=verdict.origin_session_id,
        successor_session_id=canonical.successor_session_id(conn, session_row),
        result_revision_id=verdict.result_revision_id,
        promotable_revision_id=_latest_candidate_revision_id(
            conn, session_row["id"], system_id
        ),
        available_actions=_available_actions(verdict),
    )


@router.get("/understanding-head", response_model=UnderstandingHeadOut)
def get_understanding_head(
    system_id: int = Depends(get_system_id),
) -> UnderstandingHeadOut:
    """The System's canonical Understanding, or an explicit "none yet".

    probe-agent:
      role: API boundary for reading the canonical Understanding head
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard, control-server]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify the head is System-isolated and that an unpromoted System reports null rather than its newest session's state
    """
    with get_conn() as conn:
        head = canonical.load_head(conn, system_id)
        if head is None:
            return UnderstandingHeadOut(system_id=system_id)
        revision = canonical.load_revision(conn, system_id, head.revision_id)
        understanding = None
        source_session_id = None
        content_digest = None
        premise_digest = None
        if revision is not None:
            raw = revision["current_understanding"]
            understanding = json.loads(raw) if raw else None
            source_session_id = revision["session_id"]
            content_digest = revision["content_digest"]
            premise_digest = revision["premise_digest"]
        return UnderstandingHeadOut(
            system_id=system_id,
            revision_id=head.revision_id,
            head_version=head.head_version,
            updated_at=head.updated_at,
            updated_by=head.updated_by,
            source_session_id=source_session_id,
            content_digest=content_digest,
            premise_digest=premise_digest,
            current_understanding=understanding,
        )


@router.get(
    "/interview/sessions/{session_id}/premise", response_model=InterviewPremiseOut
)
def get_interview_premise(
    session_id: int,
    system_id: int = Depends(get_system_id),
) -> InterviewPremiseOut:
    """May this Interview be continued on its own premise?

    Read-only by construction: the verdict is derived from the pinned digests
    every time it is asked for, so looking at a screen never records anything.

    probe-agent:
      role: API boundary for a session's premise verdict
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify a session whose System head moved reports 'stale' with the finite recovery actions, and that reading it writes nothing
    """
    with get_conn() as conn:
        session = _session_or_404(conn, session_id, system_id)
        return premise_out(conn, session, system_id)


@router.post(
    "/interview/sessions/{session_id}/premise/rebase",
    response_model=InterviewPremiseRebaseOut,
    status_code=201,
)
def rebase_interview_premise(
    session_id: int,
    payload: InterviewPremiseDecisionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> InterviewPremiseRebaseOut:
    """Carry this line of inquiry onto the current head, as a NEW session.

    The original conversation and its premise are left byte-for-byte
    unchanged; it is marked ``rebased`` so it is no longer offered as the
    place to keep talking, and the new session links back to it.

    probe-agent:
      role: API boundary for rebasing an Interview onto the canonical head
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify the original session's history and premise are unchanged and the new session pins the current head
    """
    now = time.time()
    with get_conn() as conn:
        _session_or_404(conn, session_id, system_id)
        conn.execute("BEGIN IMMEDIATE")
        try:
            new_session_id = canonical.rebase_session(
                conn,
                system_id=system_id,
                session_id=session_id,
                actor=_actor(principal),
                actor_user_id=principal.user_id,
                note=payload.note,
                now=now,
            )
            new_session = _session_or_404(conn, new_session_id, system_id)
            result = InterviewPremiseRebaseOut(
                session_id=session_id,
                system_id=system_id,
                new_session_id=new_session_id,
                premise=premise_out(conn, new_session, system_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result


@router.post(
    "/interview/sessions/{session_id}/premise/branch",
    response_model=InterviewPremiseOut,
)
def branch_interview_premise(
    session_id: int,
    payload: InterviewPremiseDecisionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> InterviewPremiseOut:
    """Keep this conversation explicitly as an OLD-premise exploration.

    The verdict still reads ``stale`` -- nothing about the System changed
    back. What changes is that the developer acknowledged it, so the
    conversation may continue. Its candidate still cannot be promoted.

    probe-agent:
      role: API boundary for acknowledging an old-premise Interview branch
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify a branched session stays 'stale', becomes continuable, and can still never promote its candidate
    """
    now = time.time()
    with get_conn() as conn:
        _session_or_404(conn, session_id, system_id)
        conn.execute("BEGIN IMMEDIATE")
        try:
            canonical.branch_session(
                conn,
                system_id=system_id,
                session_id=session_id,
                actor=_actor(principal),
                actor_user_id=principal.user_id,
                note=payload.note,
                now=now,
            )
            session = _session_or_404(conn, session_id, system_id)
            result = premise_out(conn, session, system_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result


@router.post(
    "/interview/sessions/{session_id}/premise/adopt-current",
    response_model=InterviewPremiseOut,
)
def adopt_current_premise(
    session_id: int,
    payload: InterviewPremiseDecisionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> InterviewPremiseOut:
    """Explicitly re-pin a legacy session onto the current canonical premise.

    Only meaningful for a session the migration left ``legacy-unbased``: it
    never recorded what it stood on, and guessing would be exactly the
    implicit continuation this Issue forbids. This is the developer saying so
    on the record.

    probe-agent:
      role: API boundary for adopting the current premise on a legacy Interview
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify a legacy session becomes 'current' only through this explicit manual action and that the adoption is audited
    """
    now = time.time()
    with get_conn() as conn:
        _session_or_404(conn, session_id, system_id)
        conn.execute("BEGIN IMMEDIATE")
        try:
            canonical.adopt_current_premise(
                conn,
                system_id=system_id,
                session_id=session_id,
                actor=_actor(principal),
                actor_user_id=principal.user_id,
                note=payload.note,
                now=now,
            )
            session = _session_or_404(conn, session_id, system_id)
            result = premise_out(conn, session, system_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result


@router.post(
    "/interview/sessions/{session_id}/promote-understanding",
    response_model=UnderstandingPromotionOut,
)
def promote_understanding(
    session_id: int,
    payload: UnderstandingPromotionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UnderstandingPromotionOut:
    """Promote this Interview's candidate to the System's canonical head.

    The whole decision -- revision statuses, the head pointer, the session's
    new baseline and the audit row -- is one transaction, so the System can
    never point at a revision the rest of the rows disagree with.

    A refused promotion is a first-class outcome: 409 with the finite
    rejection code, plus an audit row, because a conflict that leaves no trace
    cannot be counted afterwards.

    probe-agent:
      role: API boundary for the human promotion of a candidate Understanding
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: write
      state_effects: [database-write]
      probe_value: Verify a candidate built on an older head is rejected as a conflict rather than overwriting the newer head, and that the rejection is audited
    """
    now = time.time()
    with get_conn() as conn:
        # Both preconditions are read BEFORE the write transaction opens, so a
        # 404/409 rejection never leaves a half-open transaction behind.
        _session_or_404(conn, session_id, system_id)
        revision_id = payload.revision_id
        if revision_id is None:
            revision_id = _latest_candidate_revision_id(conn, session_id, system_id)
        if revision_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "revision_not_found",
                    "message": "昇格できる候補リビジョンがありません。",
                },
            )
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = canonical.promote_revision(
                conn,
                system_id=system_id,
                session_id=session_id,
                revision_id=revision_id,
                expected_head_revision_id=payload.expected_head_revision_id,
                expected_head_version=payload.expected_head_version,
                actor=_actor(principal),
                actor_user_id=principal.user_id,
                note=payload.note,
                now=now,
            )
            conn.execute("COMMIT")
        except canonical.PromotionConflict as conflict:
            conn.execute("ROLLBACK")
            # The refusal itself is a fact somebody produced; it cannot be
            # re-derived from state later, so it is recorded outside the
            # rolled-back transaction.
            canonical.record_event(
                conn,
                system_id=system_id,
                event_kind="promotion_conflict",
                session_id=session_id,
                revision_id=payload.revision_id,
                expected_head_revision_id=payload.expected_head_revision_id,
                expected_head_version=payload.expected_head_version,
                rejection_code=conflict.code,
                head_version=conflict.head_version,
                actor=_actor(principal),
                actor_user_id=principal.user_id,
                note=payload.note,
                now=now,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": conflict.code,
                    "message": conflict.message,
                    "head_revision_id": conflict.head_revision_id,
                    "head_version": conflict.head_version,
                },
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return UnderstandingPromotionOut(
        system_id=system_id,
        session_id=session_id,
        revision_id=result.revision_id,
        previous_revision_id=result.previous_revision_id,
        head_version=result.head_version,
        content_digest=result.content_digest,
        premise_digest=result.premise_digest,
        promoted_at=result.promoted_at,
        promoted_by=result.promoted_by,
    )


@router.get(
    "/understanding-canonical-events",
    response_model=UnderstandingCanonicalEventListOut,
)
def list_canonical_events(
    limit: int = Query(default=50, ge=1, le=500),
    system_id: int = Depends(get_system_id),
) -> UnderstandingCanonicalEventListOut:
    """The audit trail of head moves, refusals, rebases and branches.

    probe-agent:
      role: API boundary for the canonical Understanding audit trail
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify every head move and every refused promotion is listed and System-isolated
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM understanding_canonical_event
               WHERE system_id = ? ORDER BY id DESC LIMIT ?""",
            (system_id, limit),
        ).fetchall()
    return UnderstandingCanonicalEventListOut(
        system_id=system_id,
        items=[
            UnderstandingCanonicalEventOut(
                id=row["id"],
                system_id=row["system_id"],
                event_kind=row["event_kind"],
                session_id=row["session_id"],
                revision_id=row["revision_id"],
                previous_revision_id=row["previous_revision_id"],
                related_session_id=row["related_session_id"],
                head_version=row["head_version"],
                expected_head_revision_id=row["expected_head_revision_id"],
                expected_head_version=row["expected_head_version"],
                rejection_code=row["rejection_code"],
                premise_state=row["premise_state"],
                decision_method=row["decision_method"],
                actor=row["actor"] or "",
                note=row["note"] or "",
                created_at=row["created_at"],
            )
            for row in rows
        ],
    )


@router.get(
    "/understanding-canonical-metrics",
    response_model=UnderstandingCanonicalMetricsOut,
)
def get_canonical_metrics(
    system_id: int = Depends(get_system_id),
) -> UnderstandingCanonicalMetricsOut:
    """Derived counts for premise mismatch, stale sessions and head conflicts.

    probe-agent:
      role: API boundary for canonical Understanding observability
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify stale/premise-mismatch counts are recomputed from persisted digests and conflict counts come from the audit table
    """
    with get_conn() as conn:
        metrics = canonical.build_metrics(conn, system_id)
    return UnderstandingCanonicalMetricsOut(
        system_id=metrics.system_id,
        head_revision_id=metrics.head_revision_id,
        head_version=metrics.head_version,
        head_updated_at=metrics.head_updated_at,
        session_premise_counts=metrics.session_premise_counts,
        disposition_counts=metrics.disposition_counts,
        event_counts=metrics.event_counts,
        promotion_conflict_codes=metrics.promotion_conflict_codes,
        candidate_revision_count=metrics.candidate_revision_count,
    )
