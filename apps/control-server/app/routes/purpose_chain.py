"""Purpose Chain API (Issue #388).

Two endpoints. `GET /purpose-chain` is the only place the Dashboard reads the
Purpose Frame / Purpose Chain -- three elements (beneficiary_problem /
desired_change / intervention), their Core Capabilities, and the three
relations connecting them, all recomputed on every read from existing rows
(`docs/purpose-chain.md` §0: no new understanding model, no cached
projection). `POST /purpose-chain/relations/{relation_id}/decision` is the
ONLY write this feature performs: a human confirming or rejecting one
relation.

What this endpoint boundary deliberately does NOT do:

* It does not decompose `pain` into "who"/"what", does not score a relation's
  plausibility, and calls no reasoning model anywhere (Principle 6). Every
  element and relation is a deterministic read of `understanding_brief` /
  Intent Brief rows plus the decision table this module owns.
* It does not let a request body set `decision_method` or `origin_role` --
  the decision is always `manual`, and `decided_by` always comes from the
  authenticated `Principal`, never the payload (the same lesson #337 records
  for Joint Understanding's provenance fields: an unverifiable body-supplied
  identity lets a caller fabricate an audit trail).
* It never confirms a relation whose endpoint is `unknown`/`unavailable` --
  422 `purpose_relation_not_decidable` refuses to let a human "confirm" a
  connection that does not yet have real content on both ends.

probe-agent:
  role: API boundary for the Purpose Chain projection and its one relation decision
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify the projection is System/session-scoped, that a relation whose endpoint is unknown or unavailable can never be decided, and that decided_by always comes from the authenticated Principal rather than the request body.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    PurposeChainOut,
    PurposeElementOut,
    PurposeFrameOut,
    PurposeRelationDecisionRequest,
    PurposeRelationOut,
)
from ..purpose_chain import (
    PurposeChainResult,
    PurposeElement,
    PurposeRelation,
    RelationNotDecidable,
    RelationNotFound,
    derive_purpose_chain,
    record_relation_decision,
)

router = APIRouter()


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for this decision. Never a
    body-supplied value (see the module docstring)."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


def _element_out(element: Optional[PurposeElement]) -> Optional[PurposeElementOut]:
    if element is None:
        return None
    return PurposeElementOut(
        id=element.id,
        kind=element.kind,
        state=element.state,
        display_statement=element.display_statement,
        statement=element.statement,
        confirmation=element.confirmation,
        confirmation_label=element.confirmation_label,
        provenance=element.provenance,
        provenance_label=element.provenance_label,
        resolution_level=element.resolution_level,
        source_kind=element.source_kind,
        source_ids=list(element.source_ids),
        intent_revision_id=element.intent_revision_id,
        understanding_revision_id=element.understanding_revision_id,
        snapshot_id=element.snapshot_id,
        evidence=[dict(item) for item in element.evidence],
        evidence_stale=element.evidence_stale,
        missing_information=list(element.missing_information),
        is_mock=element.is_mock,
    )


def _relation_out(relation: PurposeRelation) -> PurposeRelationOut:
    return PurposeRelationOut(
        id=relation.id,
        kind=relation.kind,
        source_id=relation.source_id,
        target_id=relation.target_id,
        status=relation.status,
        status_label=relation.status_label,
        recheck_state=relation.recheck_state,
        stale_reason=relation.stale_reason,
        provenance=relation.provenance,
        provenance_label=relation.provenance_label,
        decision_id=relation.decision_id,
        decided_at=relation.decided_at,
        decided_by=relation.decided_by,
        rationale=relation.rationale,
        evidence=[dict(item) for item in relation.evidence],
    )


def _chain_out(system_id: int, result: PurposeChainResult) -> PurposeChainOut:
    return PurposeChainOut(
        system_id=system_id,
        session_id=result.session_id,
        generated_at=result.generated_at,
        frame=PurposeFrameOut(
            beneficiary_problem=_element_out(result.frame.get("beneficiary_problem")),
            desired_change=_element_out(result.frame.get("desired_change")),
            intervention=_element_out(result.frame.get("intervention")),
        ),
        elements=[_element_out(e) for e in result.elements if e is not None],
        relations=[_relation_out(r) for r in result.relations],
        frame_resolution_level=result.frame_resolution_level,
        frame_state=result.frame_state,
        snapshot_id=result.snapshot_id,
        understanding_revision_id=result.understanding_revision_id,
        understanding_confirmed_at=result.understanding_confirmed_at,
        degraded_sections=list(result.degraded_sections),
        degraded_detail=dict(result.degraded_detail),
    )


@router.get("/purpose-chain", response_model=PurposeChainOut)
def get_purpose_chain(
    session_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> PurposeChainOut:
    """The Purpose Frame / Purpose Chain for one session, or the System's
    newest session when `session_id` is omitted (`docs/purpose-chain.md`
    §1.6). A `session_id` belonging to another System reads exactly like
    "unselected" -- the same rule `GET /interview/understanding-brief`
    already applies, so the two screens can never disagree.
    """
    with get_conn() as conn:
        result = derive_purpose_chain(conn, system_id, session_id)
    return _chain_out(system_id, result)


@router.post(
    "/purpose-chain/relations/{relation_id}/decision",
    response_model=PurposeRelationOut,
)
def decide_purpose_relation(
    relation_id: str,
    payload: PurposeRelationDecisionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeRelationOut:
    """Record a human's `confirmed`/`rejected` judgement about ONE relation.

    The relation must exist in the CURRENT projection for `payload.session_id`
    (404 otherwise) and both its endpoints must have real content (422
    `purpose_relation_not_decidable` otherwise -- §1.6: 存在しない前提を人に
    確定させない). The decision is appended, never overwritten (§1.5); a
    prior current decision for the same relation is superseded, not deleted.
    """
    with get_conn() as conn:
        session_row = conn.execute(
            "SELECT id FROM interview_session WHERE id = ? AND system_id = ?",
            (payload.session_id, system_id),
        ).fetchone()
        if session_row is None:
            raise HTTPException(status_code=404, detail="Interview session not found")

        try:
            relation = record_relation_decision(
                conn,
                system_id,
                payload.session_id,
                relation_id,
                decision=payload.decision,
                rationale=payload.rationale,
                decided_by=_principal_actor(principal),
            )
        except RelationNotFound:
            raise HTTPException(status_code=404, detail="Purpose Chain relation not found")
        except RelationNotDecidable:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "purpose_relation_not_decidable",
                    "message": (
                        "One or both endpoints of this relation have no content yet "
                        "(unknown or unavailable); it cannot be confirmed or rejected."
                    ),
                },
            )
    return _relation_out(relation)
