"""Observation proposal lifecycle API (Issue #290).

A developer's request to start capturing NEW runtime observation for a
component (as opposed to reading facts that already exist -- Runtime
Reality Check, #135, extended by this issue) is never auto-started
(Principle 5/8). This module only ever records a proposal row and its
manual approve/reject decision (``decision_method: manual``, Principle 2);
approving a proposal does NOT itself start anything -- no code path here (or
anywhere else in the interview/investigation modules) writes to
``components.mode`` or otherwise touches probe policy. The approve response
only carries a fixed, deterministic pointer back at the existing
``PUT /components/{component_id}/policy`` endpoint, which the developer must
call themselves.

Kept in its own module (like Issues #284/#285/#287) rather than growing
``routes/interview.py`` further, per CLAUDE.md's guidance.

probe-agent:
  role: API boundary for the approval-gated runtime observation proposal
    lifecycle (proposed -> approved/rejected, manual-only)
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify approving/rejecting a proposal never writes to components.mode or any other policy table, and that status transitions are manual-only (decision_by/decision_at set only by these two endpoints).
"""

from __future__ import annotations

import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..interview_language import interview_message, resolve_message_language
from ..models import (
    RuntimeObservationProposalCreate,
    RuntimeObservationProposalDecisionRequest,
    RuntimeObservationProposalOut,
)

router = APIRouter()

_DECIDABLE_STATUSES = {"proposed"}


def _get_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _get_proposal_or_404(conn, proposal_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM runtime_observation_proposal WHERE id = ? AND system_id = ?",
        (proposal_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Observation proposal not found")
    return row


def _proposal_out(row) -> RuntimeObservationProposalOut:
    policy_pointer = None
    if row["status"] == "approved":
        policy_pointer = interview_message(
            "observation_proposal_policy_hint",
            resolve_message_language(),
            component_id=row["target_component"],
        )
    return RuntimeObservationProposalOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        origin_inquiry_id=row["origin_inquiry_id"],
        origin_alignment_item_id=row["origin_alignment_item_id"],
        target_component=row["target_component"],
        purpose=row["purpose"],
        expected_cost=row["expected_cost"],
        risk_note=row["risk_note"],
        retention_note=row["retention_note"],
        status=row["status"],
        decision_by=row["decision_by"],
        decision_at=row["decision_at"],
        created_at=row["created_at"],
        policy_pointer=policy_pointer,
    )


@router.post(
    "/interview/sessions/{session_id}/observation-proposals",
    response_model=RuntimeObservationProposalOut,
    status_code=201,
)
def create_observation_proposal(
    session_id: int,
    payload: RuntimeObservationProposalCreate,
    system_id: int = Depends(get_system_id),
) -> RuntimeObservationProposalOut:
    """Record a proposal to start NEW runtime observation for a component.

    Never starts anything by itself -- only inserts a ``status='proposed'``
    row. ``origin_inquiry_id``/``origin_alignment_item_id`` are optional
    audit links (validated to belong to this session/System when given).
    """
    now = time.time()
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        if payload.origin_inquiry_id is not None:
            origin = conn.execute(
                "SELECT id FROM interview_inquiry WHERE id = ? AND session_id = ? AND system_id = ?",
                (payload.origin_inquiry_id, session_id, system_id),
            ).fetchone()
            if origin is None:
                raise HTTPException(status_code=404, detail="Origin Inquiry not found")
        if payload.origin_alignment_item_id is not None:
            origin = conn.execute(
                "SELECT id FROM alignment_item WHERE id = ? AND session_id = ? AND system_id = ?",
                (payload.origin_alignment_item_id, session_id, system_id),
            ).fetchone()
            if origin is None:
                raise HTTPException(status_code=404, detail="Origin alignment item not found")

        cur = conn.execute(
            """INSERT INTO runtime_observation_proposal
                (session_id, system_id, origin_inquiry_id, origin_alignment_item_id,
                 target_component, purpose, expected_cost, risk_note, retention_note,
                 status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)""",
            (
                session_id, system_id, payload.origin_inquiry_id, payload.origin_alignment_item_id,
                payload.target_component, payload.purpose, payload.expected_cost,
                payload.risk_note, payload.retention_note, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM runtime_observation_proposal WHERE id = ?", (cur.lastrowid,),
        ).fetchone()
        return _proposal_out(row)


@router.get(
    "/interview/sessions/{session_id}/observation-proposals",
    response_model=List[RuntimeObservationProposalOut],
)
def list_observation_proposals(
    session_id: int,
    status: Optional[str] = None,
    system_id: int = Depends(get_system_id),
) -> List[RuntimeObservationProposalOut]:
    with get_conn() as conn:
        _get_session_or_404(conn, session_id, system_id)
        if status is not None:
            rows = conn.execute(
                """SELECT * FROM runtime_observation_proposal
                   WHERE session_id = ? AND system_id = ? AND status = ?
                   ORDER BY id DESC""",
                (session_id, system_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM runtime_observation_proposal
                   WHERE session_id = ? AND system_id = ?
                   ORDER BY id DESC""",
                (session_id, system_id),
            ).fetchall()
        return [_proposal_out(r) for r in rows]


def _decide(
    proposal_id: int, system_id: int, payload: Optional[RuntimeObservationProposalDecisionRequest],
    target_status: str,
) -> RuntimeObservationProposalOut:
    now = time.time()
    decision_by = payload.decision_by if payload else None
    with get_conn() as conn:
        row = _get_proposal_or_404(conn, proposal_id, system_id)
        if row["status"] not in _DECIDABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "observation_proposal_not_decidable",
                    "message": f"Proposal is '{row['status']}', not 'proposed'",
                },
            )
        # Manual-only (Principle 2): decision_by/decision_at/status are set
        # here and ONLY here (plus /reject's mirror call below) -- no other
        # code path writes to this table's decision columns, and this
        # UPDATE never touches ``components`` or any other policy table.
        conn.execute(
            """UPDATE runtime_observation_proposal
               SET status = ?, decision_by = ?, decision_at = ?
               WHERE id = ?""",
            (target_status, decision_by, now, proposal_id),
        )
        row = conn.execute(
            "SELECT * FROM runtime_observation_proposal WHERE id = ?", (proposal_id,),
        ).fetchone()
        return _proposal_out(row)


@router.post(
    "/interview/observation-proposals/{proposal_id}/approve",
    response_model=RuntimeObservationProposalOut,
)
def approve_observation_proposal(
    proposal_id: int,
    payload: Optional[RuntimeObservationProposalDecisionRequest] = None,
    system_id: int = Depends(get_system_id),
) -> RuntimeObservationProposalOut:
    """Manual approval only. Never starts observation -- see module docstring."""
    return _decide(proposal_id, system_id, payload, "approved")


@router.post(
    "/interview/observation-proposals/{proposal_id}/reject",
    response_model=RuntimeObservationProposalOut,
)
def reject_observation_proposal(
    proposal_id: int,
    payload: Optional[RuntimeObservationProposalDecisionRequest] = None,
    system_id: int = Depends(get_system_id),
) -> RuntimeObservationProposalOut:
    return _decide(proposal_id, system_id, payload, "rejected")
