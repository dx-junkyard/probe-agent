"""Purpose Chain API (Issues #388 and #389).

`GET /purpose-chain` is the only place the Dashboard reads the Purpose Frame
/ Purpose Chain -- three elements (beneficiary_problem / desired_change /
intervention), their Core Capabilities, and the three relations connecting
them, all recomputed on every read from existing rows (`docs/purpose-chain.md`
§0: no new understanding model, no cached projection). `POST
/purpose-chain/relations/{relation_id}/decision` is #388's one write: a human
confirming or rejecting one relation.

Issue #389 adds the adaptive next-question layer on top, in its own section
below: `GET /purpose-chain/next-question` (writes nothing -- a page view is
never an answer) and `POST /purpose-chain/needs/{need_id}/respond` (the
developer's confirm/correct/unknown/defer/investigate response to one
derived need). See `app/purpose_needs.py`'s module docstring for the
deterministic derivation rules and why the fixed answerability table does
not replace `app/question_router.py`'s reasoning-model router.

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

import time
from typing import Any, Dict, List, Optional, Sequence, Set

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import purpose_needs, purpose_verification
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from .interview_intent import _insert_item as _insert_intent_item
from ..joint_premise import capture_premise_bundle
from ..joint_understanding import SCHEMA_VERSION as JU_SCHEMA_VERSION
from ..models import (
    PurposeChainOut,
    PurposeElementOut,
    PurposeExperienceHypothesisCreateRequest,
    PurposeExperienceHypothesisOut,
    PurposeFrameOut,
    PurposeHypothesisRetireRequest,
    PurposeNeedRespondRequest,
    PurposeNeedResponseOut,
    PurposeOutcomeCriterionCreateRequest,
    PurposeOutcomeCriterionLinkRequest,
    PurposeOutcomeCriterionOut,
    PurposeOutcomeResultRequest,
    PurposeQuestionOut,
    PurposeRelationDecisionRequest,
    PurposeRelationOut,
    PurposeReuseHypothesisCreateRequest,
    PurposeReuseHypothesisOut,
    PurposeRoutedNeedOut,
    PurposeSuggestedAnswerOut,
    PurposeVerificationPromptOut,
    PurposeVerificationSessionRequest,
    PurposeVerificationStateOut,
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
from ..purpose_needs import PurposeNeed, PurposeQuestion
from ..purpose_verification import NotCreatable, NotFound as VerificationNotFound

router = APIRouter()

#: The only 2 frame element kinds a Purpose Need's `confirm`/`correct` may
#: resolve through the EXISTING Intent Brief write path (§2.6). `intervention`
#: is deliberately absent -- it has no Intent field of its own (§0/§1.2's own
#: source table), and its `frame_missing` need is `system_researchable`
#: anyway (never presented to the developer as something to confirm/correct
#: here). See `respond_to_purpose_need`'s docstring.
_INTENT_FIELD_BY_FRAME_SLOT: Dict[str, str] = {
    "beneficiary_problem": "pain",
    "desired_change": "goal",
}


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


# --- Purpose Needs / adaptive next-question (Issue #389) ---------------------
#
# `docs/purpose-chain.md` §2 is the specification; `app/purpose_needs.py`'s
# module docstring is the design-decision record. Everything DETERMINISTIC
# (need derivation, the answerability table, question selection, tie-break)
# lives there as pure functions; this module is the thin I/O boundary: read
# the chain, read the session's current responses, call the pure functions,
# and -- for `respond_to_purpose_need` -- perform the ONE write per response
# kind, reusing an existing implementation rather than a second one (see that
# function's docstring for exactly which one, per target kind).


def _current_responses(conn, system_id: int, session_id: int) -> Dict[str, Any]:
    """The current (non-superseded) response row for each need_id, in this
    session. At most one per need_id by construction (`respond_to_purpose_need`
    always supersedes the prior current row before inserting a new one)."""
    rows = conn.execute(
        """SELECT * FROM purpose_need_response
           WHERE system_id = ? AND session_id = ? AND superseded_by_id IS NULL""",
        (system_id, session_id),
    ).fetchall()
    return {row["need_id"]: row for row in rows}


def _needs_for_session(conn, system_id: int, chain: PurposeChainResult) -> List[PurposeNeed]:
    if chain.session_id is None:
        return purpose_needs.derive_needs(chain)
    responses = _current_responses(conn, system_id, chain.session_id)
    return purpose_needs.apply_response_state(purpose_needs.derive_needs(chain), responses)


def _suggested_answer_out(
    answer: Optional["purpose_needs.PurposeSuggestedAnswer"],
) -> Optional[PurposeSuggestedAnswerOut]:
    if answer is None:
        return None
    return PurposeSuggestedAnswerOut(
        text=answer.text,
        provenance=answer.provenance,
        source_kind=answer.source_kind,
        source_ids=list(answer.source_ids),
        is_mock=answer.is_mock,
    )


def _routed_need_out(need: PurposeNeed) -> PurposeRoutedNeedOut:
    return PurposeRoutedNeedOut(
        need_id=need.id,
        need_code=need.code,
        target_kind=need.target_kind,
        target_id=need.target_id,
        target_label=need.target_label,
    )


def _question_out(question: PurposeQuestion) -> PurposeQuestionOut:
    return PurposeQuestionOut(
        need_id=question.need_id,
        need_code=question.need_code,
        rule_row=question.rule_row,
        prompt=question.prompt,
        why_now=question.why_now,
        blocked_decision=question.blocked_decision,
        unlocks=question.unlocks,
        defer_impact=question.defer_impact,
        target_kind=question.target_kind,
        target_id=question.target_id,
        target_label=question.target_label,
        answerability=question.answerability,
        suggested_answer=_suggested_answer_out(question.suggested_answer),
        state=question.state,
        source_revision_ids=list(question.source_revision_ids),
        fallback_reason=question.fallback_reason,
        routed_needs=[_routed_need_out(n) for n in question.routed_needs],
    )


def _fallback_reason(
    conn,
    system_id: int,
    chain: PurposeChainResult,
    needs: Sequence[PurposeNeed],
    need_id: str,
    resolved_need: Optional[PurposeNeed],
) -> str:
    """§2.7's finite fallback reason for a `need_id` deep link that did not
    resolve to an answerable question, first-match.

    `resolved_need is not None` means the need still exists in the CURRENT
    derivation but is not currently answerable as a question -- `deferred`
    covers exactly that state; every other non-eligible state (`answered` /
    `waiting` / `unavailable`, or a `system_researchable` need that is still
    technically `available`) is reported as `resolved`, the closest fit in
    this 4-value finite set: the need is not gone, but it is not something to
    ask the developer right now either, which is close enough to "resolved"
    for a deep link that is only trying to reach an open question.

    `resolved_need is None` means the need_id is not in the current
    derivation at all. Its target_id is checked against the CURRENT chain's
    elements/relations: still present -> the SPECIFIC need cleared
    (`resolved`); absent -> check whether ANY System ever recorded a response
    for this exact need_id string (`other_system`) before giving up
    (`not_found`).
    """
    if resolved_need is not None:
        return "deferred" if resolved_need.state == "deferred" else "resolved"

    _code, target_id = purpose_needs.parse_need_id(need_id)
    elements_by_id = {e.id: e for e in chain.elements}
    relations_by_id = {r.id: r for r in chain.relations}
    if target_id in elements_by_id or target_id in relations_by_id:
        return "resolved"

    row = conn.execute(
        "SELECT 1 FROM purpose_need_response WHERE need_id = ? AND system_id != ? LIMIT 1",
        (need_id, system_id),
    ).fetchone()
    return "other_system" if row is not None else "not_found"


@router.get("/purpose-chain/next-question", response_model=Optional[PurposeQuestionOut])
def get_next_purpose_question(
    session_id: Optional[int] = Query(default=None),
    need_id: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> Optional[PurposeQuestionOut]:
    """§2.7: the single next adaptive question, or `null` for 「質問なし」.

    Writes nothing -- a page view is never recorded as an answer (§0
    invariant 4). With `need_id` omitted, this is exactly
    `purpose_needs.select_question` over the freshly derived needs. With
    `need_id` given (a Dashboard deep link, e.g. from the Overview's
    `/interview?purpose_need=<need_id>`), that SPECIFIC need is returned when
    it is still answerable; otherwise the response falls back to the current
    selected question (or `null`) and reports why via `fallback_reason` --
    never a 5th silent state.
    """
    with get_conn() as conn:
        chain = derive_purpose_chain(conn, system_id, session_id)
        needs = _needs_for_session(conn, system_id, chain)
        routed = purpose_needs.routed_needs_for(needs)

        if need_id is not None:
            target_need = purpose_needs.resolve_need(needs, need_id)
            if (
                target_need is not None
                and target_need.state == "available"
                and target_need.answerability == "human_judgement"
            ):
                return _question_out(purpose_needs.build_question(target_need, chain, routed_needs=routed))

            reason = _fallback_reason(conn, system_id, chain, needs, need_id, target_need)
            selected = purpose_needs.select_question(needs)
            if selected is None:
                return None
            return _question_out(
                purpose_needs.build_question(
                    selected, chain, fallback_reason=reason, routed_needs=routed
                )
            )

        selected = purpose_needs.select_question(needs)
        if selected is None:
            return None
        return _question_out(purpose_needs.build_question(selected, chain, routed_needs=routed))


def _insert_purpose_need_response(
    conn,
    *,
    system_id: int,
    session_id: int,
    need: PurposeNeed,
    response_kind: str,
    value_text: str,
    responded_by: Optional[str],
    now: float,
    linked_intent_item_id: Optional[int] = None,
    linked_relation_decision_id: Optional[int] = None,
) -> int:
    """The one write shared by every response kind (§2.6). Always supersedes
    the prior current row for the same need_id first -- append-only, the same
    discipline `purpose_relation_decision` / `interview_intent_item` use."""
    prior = conn.execute(
        """SELECT id FROM purpose_need_response
           WHERE system_id = ? AND session_id = ? AND need_id = ? AND superseded_by_id IS NULL""",
        (system_id, session_id, need.id),
    ).fetchone()
    cur = conn.execute(
        """INSERT INTO purpose_need_response
               (system_id, session_id, need_id, need_code, response_kind, value_text,
                target_kind, target_id, target_digest, decision_method, responded_by,
                linked_intent_item_id, linked_relation_decision_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?)""",
        (
            system_id, session_id, need.id, need.code, response_kind, value_text,
            need.target_kind, need.target_id, need.target_digest, responded_by,
            linked_intent_item_id, linked_relation_decision_id, now,
        ),
    )
    new_id = cur.lastrowid
    if prior is not None:
        conn.execute(
            "UPDATE purpose_need_response SET superseded_by_id = ? WHERE id = ?",
            (new_id, prior["id"]),
        )
    return new_id


def _open_purpose_need_investigation(
    conn, *, system_id: int, session_row, need: PurposeNeed, response_id: int, now: float
) -> int:
    """Open a Joint Understanding session with `trigger='purpose_need'`
    (§2.6) for `response_kind` `unknown`/`investigate`.

    Deliberately does NOT run the reasoning investigation loop itself -- this
    module calls no LLM (Principle 6 / the module boundary this file shares
    with `purpose_needs.py`). It opens the shared workspace exactly the way
    `routes/joint_understanding.py`'s own create path does (reusing
    `capture_premise_bundle`, never re-deriving the premise contract), and
    the developer runs the investigation from the existing Joint
    Understanding panel afterward -- one investigation implementation
    (`investigate_joint_understanding`), one audit shape, whichever origin
    opened the session.

    `origin_id` is the just-inserted `purpose_need_response.id`, not a row in
    any of the four original origin tables -- see
    `JointUnderstandingOriginKind`'s docstring in `app/models.py` for why a
    need's computed, string-identified target cannot be one of those.
    """
    premise = capture_premise_bundle(
        conn,
        origin_kind="purpose_need",
        origin_id=response_id,
        session_row=session_row,
        system_id=system_id,
        now=now,
    )
    cur = conn.execute(
        """INSERT INTO joint_understanding_session
               (session_id, system_id, origin_kind, origin_id, trigger,
                question_text, status, premise_snapshot_id, premise_commit_sha,
                premise_revision_id, premise_content_hash,
                premise_capability_digest, premise_intent_digest,
                premise_review_subject_id, premise_tracking_version,
                premise_captured_at, schema_version, created_at, updated_at)
           VALUES (?, ?, 'purpose_need', ?, 'purpose_need', ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_row["id"], system_id, response_id, need.target_label,
            premise["premise_snapshot_id"], premise["premise_commit_sha"],
            premise["premise_revision_id"], premise["premise_content_hash"],
            premise["premise_capability_digest"], premise["premise_intent_digest"],
            premise["premise_review_subject_id"], premise["premise_tracking_version"],
            premise["premise_captured_at"], JU_SCHEMA_VERSION, now, now,
        ),
    )
    return cur.lastrowid


def _need_response_out(row) -> PurposeNeedResponseOut:
    return PurposeNeedResponseOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        need_id=row["need_id"],
        need_code=row["need_code"],
        response_kind=row["response_kind"],
        value_text=row["value_text"],
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        target_digest=row["target_digest"],
        decision_method=row["decision_method"],
        responded_by=row["responded_by"],
        linked_intent_item_id=row["linked_intent_item_id"],
        linked_relation_decision_id=row["linked_relation_decision_id"],
        linked_joint_session_id=row["linked_joint_session_id"],
        superseded_by_id=row["superseded_by_id"],
        created_at=row["created_at"],
    )


@router.post(
    "/purpose-chain/needs/{need_id}/respond",
    response_model=PurposeNeedResponseOut,
)
def respond_to_purpose_need(
    need_id: str,
    payload: PurposeNeedRespondRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeNeedResponseOut:
    """§2.6: record the developer's response to ONE need.

    `need_id` must resolve in the CURRENT derivation for `payload.session_id`
    (404 otherwise -- a need that already resolved on its own, or never
    existed, has nothing to respond to; the `GET .../next-question?need_id=`
    deep link is where a stale link's `fallback_reason` belongs, not here).

    `confirm`/`correct` reuse an EXISTING implementation, never a second
    revision-chain write:

    * a `relation` target goes through `purpose_chain.record_relation_decision`
      -- `confirm` decides `'confirmed'`, `correct` decides `'rejected'` with
      `value_text` as the rationale (a relation has no free-text VALUE to
      correct, only a judgement to revise -- "correcting" it is stating it
      does not hold, with the reason why).
    * an `element` target only ever supports `beneficiary_problem` /
      `desired_change` (`_INTENT_FIELD_BY_FRAME_SLOT`) -- the only two frame
      elements with an Intent field to write, and by construction (see
      `purpose_needs`'s module docstring) the ONLY situation in which an
      element target reaches this endpoint at all is `frame_missing`, i.e.
      `element.state == "unknown"`: there is never an existing Intent row to
      correct, only one to CREATE. `confirm` is therefore never valid here
      (422 `purpose_need_nothing_to_confirm` -- there is nothing yet to
      confirm); `correct` inserts a new `interview_intent_item` row through
      the exact same `_insert_item` helper `POST .../intent` uses
      (`origin='user'`, `decision_method='manual'`, `status='confirmed'`).
      Any other element target (e.g. `human_value_judgement_required` on a
      Capability claim) has no supported write path and is refused 422
      `purpose_need_not_correctable` -- inventing one would mean a second,
      undocumented confirmation mechanism for Understanding claims.

    `unknown` / `investigate` both open a Joint Understanding session with
    `trigger='purpose_need'` (never running an investigation themselves --
    see `_open_purpose_need_investigation`). `defer` writes only the response
    row: §2.6's rule that a deferred need does not reappear until its target
    digest moves is `purpose_needs.apply_response_state`'s job on the next
    read, not something this endpoint enforces itself.
    """
    now = time.time()
    with get_conn() as conn:
        session_row = conn.execute(
            "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
            (payload.session_id, system_id),
        ).fetchone()
        if session_row is None:
            raise HTTPException(status_code=404, detail="Interview session not found")

        chain = derive_purpose_chain(conn, system_id, payload.session_id)
        needs = purpose_needs.derive_needs(chain)
        need = purpose_needs.resolve_need(needs, need_id)
        if need is None:
            raise HTTPException(status_code=404, detail="Purpose Chain need not found")

        actor = _principal_actor(principal)
        linked_intent_item_id: Optional[int] = None
        linked_relation_decision_id: Optional[int] = None

        if payload.response_kind in ("confirm", "correct"):
            if need.target_kind == "relation":
                decision = "confirmed" if payload.response_kind == "confirm" else "rejected"
                try:
                    relation = record_relation_decision(
                        conn, system_id, payload.session_id, need.target_id,
                        decision=decision, rationale=payload.value_text,
                        decided_by=actor, now=now,
                    )
                except RelationNotFound:
                    raise HTTPException(
                        status_code=404, detail="Purpose Chain relation not found"
                    )
                except RelationNotDecidable:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "purpose_relation_not_decidable",
                            "message": (
                                "One or both endpoints of this relation have no "
                                "content yet; it cannot be confirmed or rejected."
                            ),
                        },
                    )
                linked_relation_decision_id = relation.decision_id
            else:
                field = _INTENT_FIELD_BY_FRAME_SLOT.get(need.target_id)
                if field is None:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "purpose_need_not_correctable",
                            "message": (
                                "This need's target has no supported confirm/"
                                "correct write path."
                            ),
                        },
                    )
                if payload.response_kind == "confirm":
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "purpose_need_nothing_to_confirm",
                            "message": (
                                "There is no existing value to confirm; use "
                                "'correct' with value_text to provide one."
                            ),
                        },
                    )
                if not payload.value_text.strip():
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "purpose_need_value_required",
                            "message": "value_text is required to correct this need.",
                        },
                    )
                linked_intent_item_id = _insert_intent_item(
                    conn, payload.session_id, system_id,
                    field=field, value_text=payload.value_text, status="confirmed",
                    origin="user", decision_method="manual", now=now,
                )
        elif payload.response_kind not in ("defer", "unknown", "investigate"):
            raise HTTPException(status_code=422, detail="Unsupported response_kind")

        response_id = _insert_purpose_need_response(
            conn,
            system_id=system_id,
            session_id=payload.session_id,
            need=need,
            response_kind=payload.response_kind,
            value_text=payload.value_text,
            responded_by=actor,
            now=now,
            linked_intent_item_id=linked_intent_item_id,
            linked_relation_decision_id=linked_relation_decision_id,
        )

        if payload.response_kind in ("unknown", "investigate"):
            joint_session_id = _open_purpose_need_investigation(
                conn, system_id=system_id, session_row=session_row,
                need=need, response_id=response_id, now=now,
            )
            conn.execute(
                "UPDATE purpose_need_response SET linked_joint_session_id = ? WHERE id = ?",
                (joint_session_id, response_id),
            )

        row = conn.execute(
            "SELECT * FROM purpose_need_response WHERE id = ?", (response_id,)
        ).fetchone()
        return _need_response_out(row)


# --- Purpose Verification: Experience / Outcome / Reuse (Issue #391) ---------
#
# `docs/purpose-chain.md` §4 is the specification; `app/purpose_verification.py`'s
# module docstring is the design-decision record -- the same split #389's
# section above uses (deterministic gating/lineage/derivation as pure/DB
# functions there, this module a thin I/O boundary). Every create endpoint
# below re-derives the Purpose Chain and its needs FRESH inside the same
# request rather than trusting a client-supplied need snapshot, so a need
# that resolved or went stale between the prompt being shown and the
# developer acting on it is refused (422 `purpose_verification_not_creatable`)
# rather than silently accepted.


def _existing_verification_targets(
    conn, system_id: int, session_id: int
) -> Dict[str, Set[str]]:
    """`concept_kind -> {f"{target_kind}:{target_id}", ...}` for every
    target that already has at least one row of that kind -- used only to
    keep `select_verification_prompt` from re-offering a target the
    developer already acted on (§4.5)."""
    out: Dict[str, Set[str]] = {kind: set() for kind in purpose_verification.CONCEPT_KINDS}
    for concept_kind, table in (
        ("experience_hypothesis", "purpose_experience_hypothesis"),
        ("outcome_criterion", "purpose_outcome_criterion"),
        ("reuse_hypothesis", "purpose_reuse_hypothesis"),
    ):
        rows = conn.execute(
            f"""SELECT DISTINCT target_kind, target_id FROM {table}
                WHERE system_id = ? AND session_id = ?""",
            (system_id, session_id),
        ).fetchall()
        out[concept_kind] = {f"{r['target_kind']}:{r['target_id']}" for r in rows}
    return out


def _hypothesis_out(row, cls):
    return cls(
        id=row["id"],
        system_id=row["system_id"],
        session_id=row["session_id"],
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        target_label=row["target_label"],
        target_digest=row["target_digest"],
        source_need_id=row["source_need_id"],
        source_need_code=row["source_need_code"],
        statement=row["statement"],
        state=row["state"],
        decision_method=row["decision_method"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"],
        retired_by=row["retired_by"],
        retired_at=row["retired_at"],
        retirement_reason=row["retirement_reason"],
    )


def _outcome_criterion_out(conn, system_id: int, row) -> PurposeOutcomeCriterionOut:
    lineage_state = purpose_verification.resolve_lineage(
        conn, system_id, row["experiment_id"], row["candidate_version_id"]
    )
    return PurposeOutcomeCriterionOut(
        id=row["id"],
        system_id=row["system_id"],
        session_id=row["session_id"],
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        target_label=row["target_label"],
        target_digest=row["target_digest"],
        source_need_id=row["source_need_id"],
        source_need_code=row["source_need_code"],
        measure=row["measure"],
        baseline_value=row["baseline_value"],
        target_value=row["target_value"],
        observation_window=row["observation_window"],
        state=row["state"],
        experiment_id=row["experiment_id"],
        candidate_version_id=row["candidate_version_id"],
        lineage_state=lineage_state,
        human_reported_evidence=row["human_reported_evidence"],
        human_reported_verdict=row["human_reported_verdict"],
        human_reported_at=row["human_reported_at"],
        human_reported_by=row["human_reported_by"],
        runtime_observation_text=row["runtime_observation_text"],
        runtime_observation_verdict=row["runtime_observation_verdict"],
        runtime_observed_at=row["runtime_observed_at"],
        runtime_observed_by=row["runtime_observed_by"],
        is_synthetic=bool(row["is_synthetic"]),
        decision_method=row["decision_method"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        confirmed_by=row["confirmed_by"],
        confirmed_at=row["confirmed_at"],
    )


def _require_session(conn, system_id: int, session_id: int):
    row = conn.execute(
        "SELECT id FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _resolve_creatable_need(
    conn, system_id: int, session_id: int, concept_kind: str, need_id: str
) -> PurposeNeed:
    chain = derive_purpose_chain(conn, system_id, session_id)
    # `_needs_for_session` (not the bare `derive_needs`) so a need the
    # developer already answered/deferred/is waiting on reports its REAL
    # current state -- `derive_needs` alone always reports `available` and
    # would let a stale/resolved need still justify a new creation.
    needs = _needs_for_session(conn, system_id, chain)
    try:
        return purpose_verification.creatable_need(needs, concept_kind, need_id)
    except NotCreatable:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "purpose_verification_not_creatable",
                "message": (
                    "This need is not currently available, or its code does "
                    f"not justify creating a {concept_kind} (§4.1: creation "
                    "is offered only alongside a real, currently-open need)."
                ),
            },
        )


@router.get("/purpose-chain/verification", response_model=PurposeVerificationStateOut)
def get_purpose_verification(
    session_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> PurposeVerificationStateOut:
    """Every Experience Hypothesis / Outcome Criterion / Reuse Hypothesis
    currently recorded for one session (§4.1). Read-only; creation and every
    transition are their own endpoints below."""
    with get_conn() as conn:
        chain = derive_purpose_chain(conn, system_id, session_id)
        resolved_session_id = chain.session_id
        experience_rows = purpose_verification.list_experience_hypotheses(
            conn, system_id, resolved_session_id
        )
        reuse_rows = purpose_verification.list_reuse_hypotheses(
            conn, system_id, resolved_session_id
        )
        outcome_rows = purpose_verification.list_outcome_criteria(
            conn, system_id, resolved_session_id
        )
        return PurposeVerificationStateOut(
            system_id=system_id,
            session_id=resolved_session_id,
            experience_hypotheses=[
                _hypothesis_out(r, PurposeExperienceHypothesisOut) for r in experience_rows
            ],
            outcome_criteria=[
                _outcome_criterion_out(conn, system_id, r) for r in outcome_rows
            ],
            reuse_hypotheses=[
                _hypothesis_out(r, PurposeReuseHypothesisOut) for r in reuse_rows
            ],
        )


@router.get(
    "/purpose-chain/verification-prompt",
    response_model=Optional[PurposeVerificationPromptOut],
)
def get_purpose_verification_prompt(
    session_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> Optional[PurposeVerificationPromptOut]:
    """§4.5: AT MOST ONE verification prompt, or `null` for 「検証条件はまだ
    必要ありません」. Writes nothing -- a page view is never an answer, the
    same rule `GET /purpose-chain/next-question` already follows."""
    with get_conn() as conn:
        chain = derive_purpose_chain(conn, system_id, session_id)
        resolved_session_id = chain.session_id
        if resolved_session_id is None:
            return None
        needs = _needs_for_session(conn, system_id, chain)
        existing = _existing_verification_targets(conn, system_id, resolved_session_id)
        candidate = purpose_verification.select_verification_prompt(needs, existing)
        if candidate is None:
            return None
        copy = purpose_needs.need_copy_for(candidate.need.code)
        target = candidate.need.target_label
        return PurposeVerificationPromptOut(
            concept_kind=candidate.concept_kind,
            need_id=candidate.need.id,
            need_code=candidate.need.code,
            target_kind=candidate.need.target_kind,
            target_id=candidate.need.target_id,
            target_label=target,
            prompt=purpose_verification.PROMPT_TEXT[candidate.concept_kind].format(target=target),
            why_now=copy["why_now"].format(target=target),
            blocked_decision=copy["blocked_decision"].format(target=target),
            observation_hint=purpose_verification.OBSERVATION_HINT[candidate.concept_kind],
        )


@router.post(
    "/purpose-chain/experience-hypotheses",
    response_model=PurposeExperienceHypothesisOut,
    status_code=201,
)
def create_experience_hypothesis_endpoint(
    payload: PurposeExperienceHypothesisCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeExperienceHypothesisOut:
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        need = _resolve_creatable_need(
            conn, system_id, payload.session_id, "experience_hypothesis", payload.need_id
        )
        new_id = purpose_verification.create_experience_hypothesis(
            conn,
            system_id=system_id,
            session_id=payload.session_id,
            need=need,
            statement=payload.statement,
            created_by=_principal_actor(principal),
        )
        row = conn.execute(
            "SELECT * FROM purpose_experience_hypothesis WHERE id = ?", (new_id,)
        ).fetchone()
        return _hypothesis_out(row, PurposeExperienceHypothesisOut)


@router.post(
    "/purpose-chain/experience-hypotheses/{concept_id}/confirm",
    response_model=PurposeExperienceHypothesisOut,
)
def confirm_experience_hypothesis_endpoint(
    concept_id: int,
    payload: PurposeVerificationSessionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeExperienceHypothesisOut:
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        try:
            purpose_verification.confirm_hypothesis(
                conn,
                "purpose_experience_hypothesis",
                system_id=system_id,
                session_id=payload.session_id,
                concept_id=concept_id,
                confirmed_by=_principal_actor(principal),
            )
        except VerificationNotFound:
            raise HTTPException(status_code=404, detail="Experience hypothesis not found")
        row = conn.execute(
            "SELECT * FROM purpose_experience_hypothesis WHERE id = ?", (concept_id,)
        ).fetchone()
        return _hypothesis_out(row, PurposeExperienceHypothesisOut)


@router.post(
    "/purpose-chain/experience-hypotheses/{concept_id}/retire",
    response_model=PurposeExperienceHypothesisOut,
)
def retire_experience_hypothesis_endpoint(
    concept_id: int,
    payload: PurposeHypothesisRetireRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeExperienceHypothesisOut:
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        try:
            purpose_verification.retire_hypothesis(
                conn,
                "purpose_experience_hypothesis",
                system_id=system_id,
                session_id=payload.session_id,
                concept_id=concept_id,
                reason=payload.reason,
                retired_by=_principal_actor(principal),
            )
        except VerificationNotFound:
            raise HTTPException(status_code=404, detail="Experience hypothesis not found")
        row = conn.execute(
            "SELECT * FROM purpose_experience_hypothesis WHERE id = ?", (concept_id,)
        ).fetchone()
        return _hypothesis_out(row, PurposeExperienceHypothesisOut)


@router.post(
    "/purpose-chain/reuse-hypotheses",
    response_model=PurposeReuseHypothesisOut,
    status_code=201,
)
def create_reuse_hypothesis_endpoint(
    payload: PurposeReuseHypothesisCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeReuseHypothesisOut:
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        need = _resolve_creatable_need(
            conn, system_id, payload.session_id, "reuse_hypothesis", payload.need_id
        )
        new_id = purpose_verification.create_reuse_hypothesis(
            conn,
            system_id=system_id,
            session_id=payload.session_id,
            need=need,
            statement=payload.statement,
            created_by=_principal_actor(principal),
        )
        row = conn.execute(
            "SELECT * FROM purpose_reuse_hypothesis WHERE id = ?", (new_id,)
        ).fetchone()
        return _hypothesis_out(row, PurposeReuseHypothesisOut)


@router.post(
    "/purpose-chain/reuse-hypotheses/{concept_id}/confirm",
    response_model=PurposeReuseHypothesisOut,
)
def confirm_reuse_hypothesis_endpoint(
    concept_id: int,
    payload: PurposeVerificationSessionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeReuseHypothesisOut:
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        try:
            purpose_verification.confirm_hypothesis(
                conn,
                "purpose_reuse_hypothesis",
                system_id=system_id,
                session_id=payload.session_id,
                concept_id=concept_id,
                confirmed_by=_principal_actor(principal),
            )
        except VerificationNotFound:
            raise HTTPException(status_code=404, detail="Reuse hypothesis not found")
        row = conn.execute(
            "SELECT * FROM purpose_reuse_hypothesis WHERE id = ?", (concept_id,)
        ).fetchone()
        return _hypothesis_out(row, PurposeReuseHypothesisOut)


@router.post(
    "/purpose-chain/reuse-hypotheses/{concept_id}/retire",
    response_model=PurposeReuseHypothesisOut,
)
def retire_reuse_hypothesis_endpoint(
    concept_id: int,
    payload: PurposeHypothesisRetireRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeReuseHypothesisOut:
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        try:
            purpose_verification.retire_hypothesis(
                conn,
                "purpose_reuse_hypothesis",
                system_id=system_id,
                session_id=payload.session_id,
                concept_id=concept_id,
                reason=payload.reason,
                retired_by=_principal_actor(principal),
            )
        except VerificationNotFound:
            raise HTTPException(status_code=404, detail="Reuse hypothesis not found")
        row = conn.execute(
            "SELECT * FROM purpose_reuse_hypothesis WHERE id = ?", (concept_id,)
        ).fetchone()
        return _hypothesis_out(row, PurposeReuseHypothesisOut)


@router.post(
    "/purpose-chain/outcome-criteria",
    response_model=PurposeOutcomeCriterionOut,
    status_code=201,
)
def create_outcome_criterion_endpoint(
    payload: PurposeOutcomeCriterionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeOutcomeCriterionOut:
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        need = _resolve_creatable_need(
            conn, system_id, payload.session_id, "outcome_criterion", payload.need_id
        )
        new_id = purpose_verification.create_outcome_criterion(
            conn,
            system_id=system_id,
            session_id=payload.session_id,
            need=need,
            measure=payload.measure,
            baseline_value=payload.baseline_value,
            target_value=payload.target_value,
            observation_window=payload.observation_window,
            created_by=_principal_actor(principal),
        )
        row = conn.execute(
            "SELECT * FROM purpose_outcome_criterion WHERE id = ?", (new_id,)
        ).fetchone()
        return _outcome_criterion_out(conn, system_id, row)


@router.post(
    "/purpose-chain/outcome-criteria/{concept_id}/confirm",
    response_model=PurposeOutcomeCriterionOut,
)
def confirm_outcome_criterion_endpoint(
    concept_id: int,
    payload: PurposeVerificationSessionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeOutcomeCriterionOut:
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        try:
            purpose_verification.confirm_outcome_criterion(
                conn,
                system_id=system_id,
                session_id=payload.session_id,
                concept_id=concept_id,
                confirmed_by=_principal_actor(principal),
            )
        except VerificationNotFound:
            raise HTTPException(status_code=404, detail="Outcome criterion not found")
        row = conn.execute(
            "SELECT * FROM purpose_outcome_criterion WHERE id = ?", (concept_id,)
        ).fetchone()
        return _outcome_criterion_out(conn, system_id, row)


def _require_system_scoped_lineage_targets(
    conn, system_id: int, experiment_id: Optional[int], candidate_version_id: Optional[int]
) -> None:
    """§4.3: an explicit lineage write must itself name a real, System-scoped
    row -- refusing an id that does not resolve keeps a bad link from ever
    being recorded in the first place (`resolve_lineage`'s `unresolved`
    value exists for a link that goes stale LATER, e.g. a deleted
    Experiment, never for one that was never valid)."""
    if experiment_id is not None:
        row = conn.execute(
            "SELECT 1 FROM experiments WHERE id = ? AND system_id = ?",
            (experiment_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Experiment not found")
    if candidate_version_id is not None:
        row = conn.execute(
            "SELECT 1 FROM candidate_versions WHERE id = ? AND system_id = ?",
            (candidate_version_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Candidate version not found")


@router.post(
    "/purpose-chain/outcome-criteria/{concept_id}/link",
    response_model=PurposeOutcomeCriterionOut,
)
def link_outcome_criterion_endpoint(
    concept_id: int,
    payload: PurposeOutcomeCriterionLinkRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeOutcomeCriterionOut:
    """§4.3: record the explicit `experiment_id`/`candidate_version_id`
    lineage. Never a System-wide existence check for READING it later
    (`resolve_lineage`) -- but WRITING a new link still requires the target
    to exist right now, refused 404 otherwise."""
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        _require_system_scoped_lineage_targets(
            conn, system_id, payload.experiment_id, payload.candidate_version_id
        )
        try:
            purpose_verification.link_outcome_criterion(
                conn,
                system_id=system_id,
                session_id=payload.session_id,
                concept_id=concept_id,
                experiment_id=payload.experiment_id,
                candidate_version_id=payload.candidate_version_id,
            )
        except VerificationNotFound:
            raise HTTPException(status_code=404, detail="Outcome criterion not found")
        row = conn.execute(
            "SELECT * FROM purpose_outcome_criterion WHERE id = ?", (concept_id,)
        ).fetchone()
        return _outcome_criterion_out(conn, system_id, row)


@router.post(
    "/purpose-chain/outcome-criteria/{concept_id}/result",
    response_model=PurposeOutcomeCriterionOut,
)
def record_outcome_result_endpoint(
    concept_id: int,
    payload: PurposeOutcomeResultRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> PurposeOutcomeCriterionOut:
    """Records the developer's OWN verdict against ONE evidence column pair
    (`payload.source` picks human-reported vs runtime-observed -- §4.2, never
    merged). This module computes nothing from `evidence_text`: `verdict` is
    always the caller's own judgement, exactly like `purpose_chain.py`'s
    relation decision never infers `confirmed`/`rejected` from the
    endpoints' content."""
    with get_conn() as conn:
        _require_session(conn, system_id, payload.session_id)
        try:
            purpose_verification.record_outcome_result(
                conn,
                system_id=system_id,
                session_id=payload.session_id,
                concept_id=concept_id,
                source=payload.source,
                verdict=payload.verdict,
                evidence_text=payload.evidence_text,
                is_synthetic=payload.is_synthetic,
                recorded_by=_principal_actor(principal),
            )
        except VerificationNotFound:
            raise HTTPException(status_code=404, detail="Outcome criterion not found")
        row = conn.execute(
            "SELECT * FROM purpose_outcome_criterion WHERE id = ?", (concept_id,)
        ).fetchone()
        return _outcome_criterion_out(conn, system_id, row)
