"""Joint Understanding session API (Epic #328 Phase A / Issue #329).

「わからない」 is where this flow STARTS. Instead of handing the developer
back a fresh hypothesis to confirm, a Joint Understanding session opens a
shared workspace on the item they could not answer, and accumulates:

- ``findings`` -- append-only statements, each tagged with WHO produced it
  (``origin_role``: investigation / translation / developer) and WHAT kind of
  statement it is (``claim_kind``: fact / inference / hypothesis / unknown /
  conflict). The rules that keep those provenances from being conflated live
  in ``app/joint_understanding.py`` and are enforced before any insert.
- ``actions`` -- the finite next understanding actions the developer chose
  (request_investigation / explain_reasoning / compare_options /
  adopt_hypothesis / revise_intent / hold / handoff / decide).

The boundary this module must not cross (Epic #328: 「わからない」という入力を
開発者の意図として混入させない):

- The origin confirmation item is only ever READ. Creating a session,
  appending a finding, recording an action, and closing with an outcome all
  leave ``interview_qa`` / ``interview_intent_item`` / ``alignment_item`` /
  ``interview_inquiry`` byte-for-byte untouched -- including ``status``.
  Unlike Issue #287's Inquiry integration, no 'inquiry'-style status is
  mirrored onto the origin row; how these two flows integrate is Phase D's
  (#332) decision, and Phase A deliberately runs alongside them rather than
  reaching into them.
- ``question_text`` lives on the session row only. It is never copied into an
  answer field, and creating a session never writes a developer finding --
  "I don't know" is not a statement about the system.
- Closing with ``outcome='hypothesis_adopted'`` is explicitly PROVISIONAL
  (``outcome_is_provisional=true``); only ``decided`` is a final human value
  judgement, and neither approves, adopts, or executes anything.

Phase A is deterministic end to end: no reasoning model is called from this
module, and every persisted ``decision_method`` is ``manual`` (developer
records) or comes from the caller's validated finding payload. Phase B
(#330, iterative investigation) and Phase C (#331, translation) are the
reasoning producers that will POST findings here.

probe-agent:
  role: API boundary for Joint Understanding sessions, their append-only
    findings, and the developer's finite next-action records
  capability: interactive-system-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify no endpoint here writes the origin interview_qa/interview_intent_item/alignment_item/interview_inquiry row, that unknown vocabulary values are rejected 422 and out-of-table transitions 409, and that a translation finding cannot invent evidence or cite another session's findings.
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import Principal, get_principal, get_system_id
from ..db import get_conn
from ..joint_premise import (
    ASSERTABLE_PREMISE_STATES,
    BasisFinding,
    JointPremiseError,
    adoption_state,
    capture_premise_bundle,
    developer_producer,
    evaluate_session_premise,
    resolve_premise_facts,
    system_producer,
    validate_provenance,
)
from ..joint_lineage import bulk_approval_readiness, derive_lineage
from ..joint_understanding import (
    ORIGIN_KINDS,
    SCHEMA_VERSION,
    JointUnderstandingValidationError,
    available_action_kinds,
    can_reflux,
    outcome_is_provisional,
    reflux_target_kind,
    validate_finding,
    validate_outcome_basis,
    validate_transition,
)
from ..investigation_loop import (
    InvestigationLoopBudget,
    InvestigationLoopResult,
    LoopCarryOver,
    outcome_class_for,
    run_investigation_loop,
)
from ..snapshot_explorers import EXPLORATION_SOURCE_KINDS
from ..interview_language import get_interview_language, resolve_message_language
from ..llm import LLMConfig, LLMError, create_llm_client
from ..models import (
    JointUnderstandingActionCreate,
    JointUnderstandingActionMenuEntryOut,
    JointUnderstandingActionOut,
    JointUnderstandingAdoptionOut,
    JointUnderstandingCloseRequest,
    JointUnderstandingCreate,
    JointUnderstandingDetailOut,
    JointUnderstandingEvidenceOut,
    JointUnderstandingExplorationSourceOut,
    JointUnderstandingFindingCreate,
    JointUnderstandingFindingOut,
    JointUnderstandingHoldRequest,
    InterviewQaUnknownOut,
    InterviewQaUnknownRequest,
    JointUnderstandingBulkApprovalCriterionOut,
    JointUnderstandingInvestigateOut,
    JointUnderstandingInvestigateRequest,
    JointUnderstandingLineageEventOut,
    JointUnderstandingLineageOut,
    JointUnderstandingSessionBurdenOut,
    JointUnderstandingListOut,
    JointUnderstandingOptionOut,
    JointUnderstandingOut,
    JointUnderstandingRefluxOut,
    JointUnderstandingRefluxResultOut,
    JointUnderstandingRoundOut,
    JointUnderstandingRuntimeEvidenceOut,
    JointUnderstandingStatementOut,
    JointUnderstandingTranslateOut,
    JointUnderstandingTranslateRequest,
    JointUnderstandingTranslationOut,
)
from ..understanding_evidence_feed import publish_joint_understanding_finding
from ..understanding_translator import (
    FindingSummary,
    TranslationResult,
    ask_developer,
    build_action_menu,
    translate_findings,
)

router = APIRouter()


# --- Reads --------------------------------------------------------------------


def _get_interview_session_or_404(conn, session_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview session not found")
    return row


def _get_or_404(conn, ju_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM joint_understanding_session WHERE id = ? AND system_id = ?",
        (ju_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Joint Understanding session not found")
    return row


def _require_origin_exists(
    conn, *, origin_kind: str, origin_id: int, session_id: int, system_id: int
) -> None:
    """404 unless the origin item exists in THIS interview session and System.

    Read-only by construction: this is the only place the origin tables are
    touched at all, and it is a SELECT.
    """
    queries = {
        "qa": "SELECT id FROM interview_qa WHERE id = ? AND session_id = ? AND system_id = ?",
        "intent": (
            "SELECT id FROM interview_intent_item "
            "WHERE id = ? AND session_id = ? AND system_id = ?"
        ),
        "review_item": (
            "SELECT id FROM alignment_item WHERE id = ? AND session_id = ? AND system_id = ?"
        ),
        "inquiry": (
            "SELECT id FROM interview_inquiry WHERE id = ? AND session_id = ? AND system_id = ?"
        ),
    }
    row = conn.execute(queries[origin_kind], (origin_id, session_id, system_id)).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Origin item not found for origin_kind='{origin_kind}'",
        )


def _column(row, name: str):
    """Value of an optional (additively migrated) column, else None."""
    return row[name] if name in row.keys() else None


def _premise_verdict(conn, ju_row):
    """The session's finite premise verdict, from the shared #308 bundle.

    Issue #337 replaced this function's original body -- a comparison of
    ``interview_session.snapshot_id`` against the pinned id -- with
    ``app/joint_premise.evaluate_session_premise``. The old version reported
    ``fresh`` in three cases where the premise was in fact unknown or gone
    (nothing pinned, the interview session unresolvable, the pinned snapshot
    deleted), and it could not see an Intent correction, an Alignment rebuild,
    or a superseded Inquiry at all -- none of which move the snapshot id.
    Every consumer here now reads the same verdict from the same rows.
    """
    return evaluate_session_premise(conn, ju_row)


def _session_out(row, verdict=None) -> JointUnderstandingOut:
    state = verdict.state if verdict is not None else "invalid"
    reason = verdict.reason_code if verdict is not None else "premise_not_captured"
    return JointUnderstandingOut(
        id=row["id"],
        session_id=row["session_id"],
        system_id=row["system_id"],
        origin_kind=row["origin_kind"],
        origin_id=row["origin_id"],
        trigger=row["trigger"],
        question_text=row["question_text"],
        status=row["status"],
        outcome=row["outcome"],
        outcome_is_provisional=outcome_is_provisional(row["outcome"]),
        outcome_reason=row["outcome_reason"],
        outcome_finding_ids=json.loads(_column(row, "outcome_finding_ids") or "[]"),
        outcome_premise_state=_column(row, "outcome_premise_state"),
        outcome_premise_reason=_column(row, "outcome_premise_reason"),
        closed_by_actor_kind=_column(row, "closed_by_actor_kind"),
        closed_by_username=_column(row, "closed_by_username"),
        premise_state=state,
        premise_reason=reason,
        premise_snapshot_id=row["premise_snapshot_id"],
        premise_commit_sha=_column(row, "premise_commit_sha"),
        premise_revision_id=_column(row, "premise_revision_id"),
        premise_tracking_version=_column(row, "premise_tracking_version"),
        premise_captured_at=_column(row, "premise_captured_at"),
        schema_version=row["schema_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        closed_at=row["closed_at"],
    )


def _finding_out(row) -> JointUnderstandingFindingOut:
    return JointUnderstandingFindingOut(
        id=row["id"],
        joint_understanding_id=row["joint_understanding_id"],
        system_id=row["system_id"],
        origin_role=row["origin_role"],
        claim_kind=row["claim_kind"],
        statement=row["statement"],
        producer_kind=_column(row, "producer_kind") or "legacy",
        actor_kind=_column(row, "actor_kind") or "legacy",
        actor_username=_column(row, "actor_username"),
        evidence=[
            JointUnderstandingEvidenceOut(**e) for e in json.loads(row["evidence_json"])
        ],
        runtime_evidence=[
            JointUnderstandingRuntimeEvidenceOut(**e)
            for e in json.loads(row["runtime_evidence_json"])
        ],
        supports_finding_ids=json.loads(row["supports_finding_ids"]),
        competing_explanations=json.loads(row["competing_explanations"]),
        refutation_conditions=json.loads(row["refutation_conditions"]),
        next_investigation=row["next_investigation"],
        uncertainty=row["uncertainty"],
        supersedes_finding_id=row["supersedes_finding_id"],
        decision_method=row["decision_method"],
        intelligence_run_id=row["intelligence_run_id"],
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
    )


def _action_out(row) -> JointUnderstandingActionOut:
    return JointUnderstandingActionOut(
        id=row["id"],
        joint_understanding_id=row["joint_understanding_id"],
        system_id=row["system_id"],
        action_kind=row["action_kind"],
        actor=row["actor"],
        actor_kind=_column(row, "actor_kind") or "legacy",
        actor_username=_column(row, "actor_username"),
        note=row["note"],
        decision_method="manual",
        created_at=row["created_at"],
    )


def _adoption_out(row, *, premise_state: str, basis_superseded: bool) -> JointUnderstandingAdoptionOut:
    return JointUnderstandingAdoptionOut(
        id=row["id"],
        joint_understanding_id=row["joint_understanding_id"],
        system_id=row["system_id"],
        finding_id=row["finding_id"],
        state=adoption_state(
            premise_state=premise_state, basis_superseded=basis_superseded,
        ),
        adopted_by_actor_kind=row["adopted_by_actor_kind"],
        adopted_by_username=row["adopted_by_username"],
        adoption_reason=row["adoption_reason"] or "",
        premise_snapshot_id=row["premise_snapshot_id"],
        premise_commit_sha=row["premise_commit_sha"],
        premise_revision_id=row["premise_revision_id"],
        decision_method="manual",
        adopted_at=row["adopted_at"],
    )


def _superseded_finding_ids(findings) -> set:
    """Findings a later finding in the same session has corrected."""
    return {
        r["supersedes_finding_id"]
        for r in findings
        if r["supersedes_finding_id"] is not None
    }


def _basis_findings(findings) -> Dict[int, BasisFinding]:
    """The subset of each finding that decides outcome-basis eligibility."""
    superseded = _superseded_finding_ids(findings)
    return {
        row["id"]: BasisFinding(
            id=row["id"],
            origin_role=row["origin_role"],
            claim_kind=row["claim_kind"],
            is_mock=bool(row["is_mock"]),
            superseded=row["id"] in superseded,
            intelligence_run_id=row["intelligence_run_id"],
            has_evidence=bool(
                json.loads(row["evidence_json"]) or json.loads(row["runtime_evidence_json"])
            ),
        )
        for row in findings
    }


def _detail(conn, ju_row) -> JointUnderstandingDetailOut:
    ju_id = ju_row["id"]
    findings = conn.execute(
        "SELECT * FROM joint_understanding_finding "
        "WHERE joint_understanding_id = ? ORDER BY id",
        (ju_id,),
    ).fetchall()
    actions = conn.execute(
        "SELECT * FROM joint_understanding_action "
        "WHERE joint_understanding_id = ? ORDER BY id",
        (ju_id,),
    ).fetchall()
    rounds = conn.execute(
        "SELECT * FROM joint_understanding_investigation_round "
        "WHERE joint_understanding_id = ? ORDER BY id",
        (ju_id,),
    ).fetchall()
    sources_by_round: Dict[int, list] = {}
    for source in conn.execute(
        "SELECT * FROM joint_understanding_exploration_source "
        "WHERE joint_understanding_id = ? ORDER BY id",
        (ju_id,),
    ).fetchall():
        sources_by_round.setdefault(source["round_id"], []).append(source)
    translations = conn.execute(
        "SELECT * FROM joint_understanding_translation "
        "WHERE joint_understanding_id = ? ORDER BY id",
        (ju_id,),
    ).fetchall()
    reflux_rows = conn.execute(
        "SELECT * FROM joint_understanding_reflux "
        "WHERE joint_understanding_id = ? ORDER BY id",
        (ju_id,),
    ).fetchall()
    adoption_rows = conn.execute(
        "SELECT * FROM joint_understanding_hypothesis_adoption "
        "WHERE joint_understanding_id = ? ORDER BY id",
        (ju_id,),
    ).fetchall()
    verdict = _premise_verdict(conn, ju_row)
    superseded = _superseded_finding_ids(findings)
    return JointUnderstandingDetailOut(
        session=_session_out(ju_row, verdict),
        findings=[_finding_out(f) for f in findings],
        actions=[_action_out(a) for a in actions],
        investigation_rounds=[
            _round_out(r, sources_by_round.get(r["id"], [])) for r in rounds
        ],
        translations=[_translation_out(t) for t in translations],
        reflux=[_reflux_out(r) for r in reflux_rows],
        hypothesis_adoptions=[
            _adoption_out(
                r,
                premise_state=verdict.state,
                basis_superseded=r["finding_id"] in superseded,
            )
            for r in adoption_rows
        ],
        available_actions=available_action_kinds(ju_row["status"]),
    )


# --- Create / list / detail ----------------------------------------------------


@router.post(
    "/interview/sessions/{session_id}/joint-understanding",
    response_model=JointUnderstandingDetailOut,
    status_code=201,
)
def create_joint_understanding(
    session_id: int,
    payload: JointUnderstandingCreate,
    system_id: int = Depends(get_system_id),
) -> JointUnderstandingDetailOut:
    """Open a Joint Understanding session on a confirmation item.

    The origin item is validated to exist and is otherwise untouched: no
    answer, no intent value, no decision, and no status is written. The new
    session starts ``open`` with ZERO findings -- in particular
    ``trigger='unknown_answer'`` does not create a developer finding, because
    "I don't know" is not a statement about the system (Epic #328's
    「わからない」を開発者の意図として混入させない boundary).

    The session captures the shared Issue #308 premise bundle (Issue #337):
    the pinned snapshot AND its commit, the origin revision, the origin's own
    content digest, the confirmed Capability scope digest, the linked Intent
    digest, and the review-subject anchor where the origin has one. Pinning
    only the snapshot id was not enough -- an Intent correction, an Alignment
    rebuild, or a superseded Inquiry all move the ground the conversation
    stands on without moving the snapshot.
    """
    now = time.time()
    if payload.trigger != "explicit_request":
        # Issue #336: `trigger` records WHICH PATH ran, so it cannot be a claim
        # in a request body. 'unknown_answer' is written only by
        # POST .../qa/{qa_id}/unknown, where the developer really did answer
        # 「わからない」 and the router really did decide to investigate.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "joint_understanding_trigger_not_settable",
                "message": (
                    "Only 'explicit_request' may be started here; "
                    "'unknown_answer' is recorded by the 「わからない」 flow itself"
                ),
            },
        )
    with get_conn() as conn:
        interview_session = _get_interview_session_or_404(conn, session_id, system_id)
        _require_origin_exists(
            conn,
            origin_kind=payload.origin_kind,
            origin_id=payload.origin_id,
            session_id=session_id,
            system_id=system_id,
        )
        premise = capture_premise_bundle(
            conn,
            origin_kind=payload.origin_kind,
            origin_id=payload.origin_id,
            session_row=interview_session,
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
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, system_id, payload.origin_kind, payload.origin_id,
                payload.trigger, payload.question_text,
                premise["premise_snapshot_id"], premise["premise_commit_sha"],
                premise["premise_revision_id"], premise["premise_content_hash"],
                premise["premise_capability_digest"], premise["premise_intent_digest"],
                premise["premise_review_subject_id"],
                premise["premise_tracking_version"], premise["premise_captured_at"],
                SCHEMA_VERSION, now, now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (cur.lastrowid,),
        ).fetchone()
        return _detail(conn, row)


@router.get(
    "/interview/sessions/{session_id}/joint-understanding",
    response_model=JointUnderstandingListOut,
)
def list_joint_understanding(
    session_id: int,
    status: Optional[str] = Query(default=None),
    origin_kind: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> JointUnderstandingListOut:
    """List a session's Joint Understanding sessions, oldest first.

    Both filters are finite-set values; an unknown one is a 422 rather than a
    silently empty list (Principle 6 -- an unrecognised value is never
    treated as "no filter" or as a match).
    """
    if status is not None and status not in ("open", "held", "closed"):
        raise HTTPException(status_code=422, detail=f"Invalid status filter: {status!r}")
    if origin_kind is not None and origin_kind not in ORIGIN_KINDS:
        raise HTTPException(
            status_code=422, detail=f"Invalid origin_kind filter: {origin_kind!r}",
        )
    clauses = ["session_id = ?", "system_id = ?"]
    params: List[object] = [session_id, system_id]
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if origin_kind is not None:
        clauses.append("origin_kind = ?")
        params.append(origin_kind)

    with get_conn() as conn:
        _get_interview_session_or_404(conn, session_id, system_id)
        rows = conn.execute(
            f"SELECT * FROM joint_understanding_session WHERE {' AND '.join(clauses)} ORDER BY id",
            tuple(params),
        ).fetchall()
        return JointUnderstandingListOut(
            session_id=session_id, system_id=system_id,
            items=[_session_out(r, _premise_verdict(conn, r)) for r in rows],
        )


@router.get(
    "/joint-understanding/{ju_id}",
    response_model=JointUnderstandingDetailOut,
)
def get_joint_understanding(
    ju_id: int, system_id: int = Depends(get_system_id),
) -> JointUnderstandingDetailOut:
    with get_conn() as conn:
        row = _get_or_404(conn, ju_id, system_id)
        return _detail(conn, row)


# --- Findings (append-only) ----------------------------------------------------


@router.post(
    "/joint-understanding/{ju_id}/findings",
    response_model=JointUnderstandingFindingOut,
    status_code=201,
)
def append_finding(
    ju_id: int,
    payload: JointUnderstandingFindingCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> JointUnderstandingFindingOut:
    """Append one DEVELOPER finding to an open Joint Understanding session.

    There is no update or delete counterpart: a correction is a new finding
    carrying ``supersedes_finding_id``, so an explanation can always be
    traced back to the exact claim it came from even after it was revised.

    Issue #337 narrowed this endpoint to ``origin_role='developer'``. The
    ``investigation`` and ``translation`` roles are written exclusively by
    their internal producers (``/investigate``, ``/translate``), which validate
    every citation against the pinned snapshot and every reasoning run against
    this session's premise. Accepting those roles from a request body meant an
    unverifiable "fact" with fabricated citations could be stored beside real
    ones -- and accepting ``developer`` from any caller meant a request body
    could record a sentence as the human's own judgement, with
    ``decision_method='manual'`` attached. Both are 403 with a finite code
    rather than a silent reinterpretation of what the caller asked for.

    The recorded provenance comes from the ROUTE and the authenticated
    ``Principal``, never from the payload: ``producer_kind='developer_api'``,
    ``actor_kind='user'``, plus the caller's user id and username.

    The role contract (``app/joint_understanding.validate_finding``) is still
    enforced, fail-closed with 422 and nothing persisted: a developer finding
    is always ``manual``, never carries an intelligence run or mock flag, and
    never carries snapshot evidence of its own (a judgement is not evidence).
    """
    now = time.time()
    if payload.origin_role != "developer":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "joint_understanding_producer_internal",
                "message": (
                    f"origin_role='{payload.origin_role}' is written only by its "
                    "internal producer; use /investigate or /translate"
                ),
            },
        )
    producer = developer_producer(
        user_id=principal.user_id, username=principal.username,
    )
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        if ju["status"] != "open":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "joint_understanding_not_open",
                    "message": f"Session is '{ju['status']}', not open",
                },
            )
        known_ids = {
            r["id"]
            for r in conn.execute(
                "SELECT id FROM joint_understanding_finding WHERE joint_understanding_id = ?",
                (ju_id,),
            ).fetchall()
        }
        # The run-provenance checks that used to live here (run belongs to this
        # System, is a completed reasoning run, its is_mock matches, its
        # snapshot is this session's) are gone with Issue #337, not weakened:
        # this endpoint now only accepts `developer` findings, which must carry
        # `intelligence_run_id IS NULL` (validate_finding enforces that below).
        # The two internal producers create the run they reference themselves,
        # from this session's own system_id and pinned snapshot_id, so the same
        # invariants hold structurally there. A guard that can no longer fire is
        # worse than none: it reads as a live defence of a path that is closed.
        try:
            validate_finding(
                origin_role=payload.origin_role,
                claim_kind=payload.claim_kind,
                decision_method=payload.decision_method,
                evidence=payload.evidence,
                runtime_evidence=payload.runtime_evidence,
                supports_finding_ids=payload.supports_finding_ids,
                competing_explanations=payload.competing_explanations,
                refutation_conditions=payload.refutation_conditions,
                next_investigation=payload.next_investigation,
                intelligence_run_id=payload.intelligence_run_id,
                is_mock=payload.is_mock,
                known_finding_ids=known_ids,
                supersedes_finding_id=payload.supersedes_finding_id,
            )
            validate_provenance(
                producer_kind=producer.producer_kind,
                origin_role=payload.origin_role,
                actor_kind=producer.actor_kind,
                actor_user_id=producer.actor_user_id,
            )
        except JointUnderstandingValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except JointPremiseError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        cur = conn.execute(
            """INSERT INTO joint_understanding_finding
                (joint_understanding_id, system_id, origin_role, claim_kind,
                 statement, evidence_json, runtime_evidence_json,
                 supports_finding_ids, competing_explanations,
                 refutation_conditions, next_investigation, uncertainty,
                 supersedes_finding_id, decision_method, intelligence_run_id,
                 is_mock, producer_kind, actor_kind, actor_user_id,
                 actor_username, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ju_id, system_id, payload.origin_role, payload.claim_kind,
                payload.statement,
                json.dumps([e.model_dump() for e in payload.evidence], ensure_ascii=False),
                json.dumps(
                    [e.model_dump() for e in payload.runtime_evidence], ensure_ascii=False,
                ),
                json.dumps(list(payload.supports_finding_ids)),
                json.dumps(list(payload.competing_explanations), ensure_ascii=False),
                json.dumps(list(payload.refutation_conditions), ensure_ascii=False),
                payload.next_investigation, payload.uncertainty,
                payload.supersedes_finding_id, payload.decision_method,
                payload.intelligence_run_id, 1 if payload.is_mock else 0,
                producer.producer_kind, producer.actor_kind,
                producer.actor_user_id, producer.actor_username, now,
            ),
        )
        conn.execute(
            "UPDATE joint_understanding_session SET updated_at = ? WHERE id = ?",
            (now, ju_id),
        )
        row = conn.execute(
            "SELECT * FROM joint_understanding_finding WHERE id = ?", (cur.lastrowid,),
        ).fetchone()
        return _finding_out(row)


# --- Iterative investigation (Phase B / #330) ----------------------------------


def _exploration_source_out(row) -> JointUnderstandingExplorationSourceOut:
    return JointUnderstandingExplorationSourceOut(
        id=row["id"],
        round_id=row["round_id"],
        system_id=row["system_id"],
        source_kind=row["source_kind"],
        revision=row["revision"],
        candidates_found=row["candidates_found"],
        queries_run=row["queries_run"],
        elapsed_seconds=row["elapsed_seconds"],
        truncated=bool(row["truncated"]),
        error_details=row["error_details"],
        created_at=row["created_at"],
    )


def _round_out(row, sources=()) -> JointUnderstandingRoundOut:
    return JointUnderstandingRoundOut(
        id=row["id"],
        joint_understanding_id=row["joint_understanding_id"],
        system_id=row["system_id"],
        round_index=row["round_index"],
        status=row["status"],
        stop_reason=row["stop_reason"],
        conclusion=row["conclusion"],
        search_leads=json.loads(row["search_leads"]),
        open_hypotheses=json.loads(row["open_hypotheses"]),
        missing_evidence=json.loads(row["missing_evidence"]),
        read_paths=json.loads(row["read_paths"]),
        unread_candidates=json.loads(row["unread_candidates"]),
        pruned_findings=row["pruned_findings"],
        files_read=row["files_read"],
        chars_read=row["chars_read"],
        llm_calls=row["llm_calls"],
        elapsed_seconds=row["elapsed_seconds"],
        intelligence_run_id=row["intelligence_run_id"],
        error_details=row["error_details"],
        failure_class=_column(row, "failure_class"),
        outcome_class=outcome_class_for(row["stop_reason"] or "unresolved"),
        sources=[_exploration_source_out(source) for source in sources],
        created_at=row["created_at"],
    )


def _restore_carry_over(conn, ju_id: int) -> LoopCarryOver:
    """Rebuild the loop state a previous run left behind.

    Search leads, open hypotheses and missing evidence come from the most
    recent round that produced any (a failed round produces none, and must
    not erase what the round before it earned); ``read_paths`` is the union
    across every round, so a retry never re-reads what has already been read.
    This is the continuity requirement of Epic #328 -- a retry resumes from
    the leads already paid for instead of starting over from the question.
    """
    rows = conn.execute(
        """SELECT status, search_leads, open_hypotheses, missing_evidence, read_paths
           FROM joint_understanding_investigation_round
           WHERE joint_understanding_id = ? ORDER BY id""",
        (ju_id,),
    ).fetchall()
    carry = LoopCarryOver()
    for row in rows:
        if row["status"] != "failed":
            # Empty is meaningful: a later successful round may have resolved
            # an earlier lead/hypothesis.  Only a failed round is ignored.
            carry.search_leads = json.loads(row["search_leads"])
            carry.open_hypotheses = json.loads(row["open_hypotheses"])
            carry.missing_evidence = json.loads(row["missing_evidence"])
        for path in json.loads(row["read_paths"]):
            if path not in carry.read_paths:
                carry.read_paths.append(path)
    return carry


def _persist_loop(
    conn,
    *,
    result: InvestigationLoopResult,
    ju_id: int,
    system_id: int,
    snapshot_id: Optional[int],
    started_at: float,
    completed_at: float,
) -> "tuple[List[JointUnderstandingRoundOut], List[JointUnderstandingFindingOut]]":
    """Persist every round as its own audit run plus its validated findings.

    One ``intelligence_runs`` row per round (Principle 7 -- each reasoning
    call is independently auditable, with its own budget usage), and each
    round's findings are appended as ``origin_role='investigation'`` rows
    referencing that run. A failed round still gets its audit row; it simply
    contributes no findings, and the rounds before it keep theirs.
    """
    round_outs: List[JointUnderstandingRoundOut] = []
    finding_outs: List[JointUnderstandingFindingOut] = []
    for persisted_index, loop_round in enumerate(result.rounds):
        run_cur = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 error_details, is_mock, started_at, completed_at,
                 budget_files_read, budget_chars_read, budget_llm_calls,
                 budget_elapsed_seconds)
            VALUES (?, ?, 'joint_investigation', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, snapshot_id, result.provider, result.model,
                result.prompt_version, result.schema_version,
                "failed" if loop_round.error else "completed", loop_round.error,
                1 if result.is_mock else 0, started_at, completed_at,
                loop_round.files_read, loop_round.chars_read, loop_round.llm_calls,
                loop_round.elapsed_seconds,
            ),
        )
        run_id = run_cur.lastrowid
        for snippet in loop_round.read_snippets:
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

        round_cur = conn.execute(
            """INSERT INTO joint_understanding_investigation_round
                (joint_understanding_id, system_id, round_index, status,
                 stop_reason, conclusion, search_leads, open_hypotheses,
                 missing_evidence, read_paths, unread_candidates,
                 pruned_findings, files_read, chars_read, llm_calls,
                 elapsed_seconds, intelligence_run_id, error_details,
                 failure_class, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ju_id, system_id, loop_round.round_index, loop_round.status,
                result.stop_reason if persisted_index == len(result.rounds) - 1 else None,
                loop_round.conclusion,
                json.dumps(loop_round.search_leads, ensure_ascii=False),
                json.dumps(loop_round.open_hypotheses, ensure_ascii=False),
                json.dumps(loop_round.missing_evidence, ensure_ascii=False),
                json.dumps([s.path for s in loop_round.read_snippets], ensure_ascii=False),
                json.dumps(loop_round.unread_candidates, ensure_ascii=False),
                loop_round.pruned_findings, loop_round.files_read, loop_round.chars_read,
                loop_round.llm_calls, loop_round.elapsed_seconds, run_id,
                loop_round.error, loop_round.failure_class, completed_at,
            ),
        )
        round_id = round_cur.lastrowid
        # Issue #339: one audit row per exploration source, with its own
        # revision and budget consumption. The round total cannot answer "did
        # each source stay on the pinned revision?", which is the boundary the
        # added structural sources have to respect.
        for source in loop_round.source_audits:
            if source.source_kind not in EXPLORATION_SOURCE_KINDS:
                continue
            conn.execute(
                """INSERT INTO joint_understanding_exploration_source
                    (round_id, joint_understanding_id, system_id, source_kind,
                     revision, candidates_found, queries_run, elapsed_seconds,
                     truncated, error_details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    round_id, ju_id, system_id, source.source_kind,
                    source.revision, len(source.candidates), source.queries_run,
                    source.elapsed_seconds, 1 if source.truncated else 0,
                    source.error, completed_at,
                ),
            )
        round_outs.append(_round_out(
            conn.execute(
                "SELECT * FROM joint_understanding_investigation_round WHERE id = ?",
                (round_id,),
            ).fetchone(),
            conn.execute(
                "SELECT * FROM joint_understanding_exploration_source "
                "WHERE round_id = ? ORDER BY id",
                (round_id,),
            ).fetchall(),
        ))

        for finding in loop_round.findings:
            known_ids = {
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM joint_understanding_finding "
                    "WHERE joint_understanding_id = ?",
                    (ju_id,),
                ).fetchall()
            }
            try:
                validate_finding(
                    origin_role="investigation",
                    claim_kind=finding.claim_kind,
                    decision_method="reasoning_llm",
                    evidence=finding.evidence,
                    runtime_evidence=finding.runtime_evidence,
                    supports_finding_ids=[],
                    competing_explanations=finding.competing_explanations,
                    refutation_conditions=finding.refutation_conditions,
                    next_investigation=finding.next_investigation,
                    intelligence_run_id=run_id,
                    is_mock=result.is_mock,
                    known_finding_ids=known_ids,
                )
            except JointUnderstandingValidationError:
                # The loop already applies this contract; anything that still
                # fails it is dropped rather than stored in a weaker form.
                continue
            cur = conn.execute(
                """INSERT INTO joint_understanding_finding
                    (joint_understanding_id, system_id, origin_role, claim_kind,
                     statement, evidence_json, runtime_evidence_json,
                     supports_finding_ids, competing_explanations,
                     refutation_conditions, next_investigation, uncertainty,
                     supersedes_finding_id, decision_method, intelligence_run_id,
                     is_mock, producer_kind, actor_kind, created_at)
                VALUES (?, ?, 'investigation', ?, ?, ?, ?, '[]', ?, ?, ?, ?, NULL,
                        'reasoning_llm', ?, ?, 'investigation_loop', 'system', ?)""",
                (
                    ju_id, system_id, finding.claim_kind, finding.statement,
                    json.dumps(
                        [
                            {
                                "path": e.path, "start_line": e.start_line,
                                "end_line": e.end_line, "summary": e.summary,
                            }
                            for e in finding.evidence
                        ],
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        [
                            {
                                "component_id": e.component_id,
                                "runtime_check": e.runtime_check,
                                "summary": e.summary,
                            }
                            for e in finding.runtime_evidence
                        ],
                        ensure_ascii=False,
                    ),
                    json.dumps(finding.competing_explanations, ensure_ascii=False),
                    json.dumps(finding.refutation_conditions, ensure_ascii=False),
                    finding.next_investigation, finding.uncertainty,
                    run_id, 1 if result.is_mock else 0, completed_at,
                ),
            )
            finding_outs.append(_finding_out(conn.execute(
                "SELECT * FROM joint_understanding_finding WHERE id = ?", (cur.lastrowid,),
            ).fetchone()))

    conn.execute(
        "UPDATE joint_understanding_session SET updated_at = ? WHERE id = ?",
        (completed_at, ju_id),
    )
    return round_outs, finding_outs


@router.post(
    "/joint-understanding/{ju_id}/investigate",
    response_model=JointUnderstandingInvestigateOut,
)
def investigate_joint_understanding(
    ju_id: int,
    payload: Optional[JointUnderstandingInvestigateRequest] = None,
    system_id: int = Depends(get_system_id),
) -> JointUnderstandingInvestigateOut:
    """Run one iterative investigation over the session's pinned snapshot.

    Each call runs up to ``max_rounds`` read-only rounds
    (``app/investigation_loop.py``) against the snapshot pinned when the
    session was created -- never the working tree, never a newer snapshot.
    Calling it again RESUMES: the previous rounds' search leads, open
    hypotheses, missing evidence and already-read paths are restored, so a
    retry continues the investigation instead of restarting it.

    Findings are appended as ``origin_role='investigation'`` rows and every
    round is persisted as its own ``intelligence_runs`` audit row with its
    budget usage. Nothing here writes the origin confirmation item, and no
    finding is ever recorded as the developer's answer.

    Fail-closed (Principle 6/7): a mock/non-reasoning configuration, a git
    failure, an API failure or invalid structured output yields
    ``stop_reason='failed'``. When that leaves no findings at all the call is
    a 502 -- the audit rows are still persisted, but no partial conclusion is
    reported as if it had succeeded.

    probe-agent:
      role: API boundary that runs the iterative read-only investigation loop
        for a Joint Understanding session and persists its rounds/findings
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: io
      state_effects: [database-read, database-write, external-api]
      probe_value: Verify a retry restores carried-over leads instead of restarting, that a later-round failure keeps earlier findings, that rounds persist their own audit rows, and that a mock configuration fails closed with no findings.
    """
    started_at = time.time()
    request = payload or JointUnderstandingInvestigateRequest()

    # Phase 1 (DB lock held): deterministic reads only.
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        if ju["status"] != "open":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "joint_understanding_not_open",
                    "message": f"Session is '{ju['status']}', not open",
                },
            )
        snapshot_id = ju["premise_snapshot_id"]
        snapshot = conn.execute(
            "SELECT repo_path, commit_sha FROM repository_snapshots "
            "WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone() if snapshot_id is not None else None
        question_text = ju["question_text"]
        carry_over = _restore_carry_over(conn, ju_id)
        round_index_offset = int(conn.execute(
            "SELECT COALESCE(MAX(round_index), 0) AS n "
            "FROM joint_understanding_investigation_round "
            "WHERE joint_understanding_id = ?",
            (ju_id,),
        ).fetchone()["n"])

    if snapshot is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "joint_understanding_snapshot_missing",
                "message": "The session's pinned snapshot is no longer available",
            },
        )

    # Phase 2 (DB lock released): the reasoning loop. run_investigation_loop
    # opens and closes its own short-lived connections for index/runtime
    # reads, always before each round's LLM call (see app/db.py).
    config = LLMConfig.intelligence_from_env()
    budget = InvestigationLoopBudget(
        **({"max_rounds": request.max_rounds} if request.max_rounds else {})
    )
    try:
        client = create_llm_client(config)
        result = run_investigation_loop(
            client, config,
            repo_path=snapshot["repo_path"], commit_sha=snapshot["commit_sha"],
            question=question_text,
            research_focus=request.research_focus,
            search_keywords=request.search_keywords,
            language=get_interview_language(),
            budget=budget, carry_over=carry_over,
            system_id=system_id, snapshot_id=snapshot_id,
            round_index_offset=round_index_offset,
        )
    except (LLMError, ValueError) as exc:
        result = InvestigationLoopResult(
            provider=config.provider, model=config.model,
            is_mock=config.provider == "mock",
            stop_reason="failed", carry_over=carry_over,
            failure_class="config_invalid", error=str(exc),
        )
    completed_at = time.time()

    # Phase 3 (DB lock re-acquired): persist audit rows and findings.
    with get_conn() as conn:
        round_outs, finding_outs = _persist_loop(
            conn, result=result, ju_id=ju_id, system_id=system_id,
            snapshot_id=snapshot_id, started_at=started_at, completed_at=completed_at,
        )
        if not result.rounds and result.error:
            # Nothing ran at all (configuration/git failure): record the
            # failure itself so the fail-closed outcome is auditable.
            conn.execute(
                """INSERT INTO intelligence_runs
                    (system_id, snapshot_id, run_type, provider, model,
                     prompt_version, schema_version, decision_method, status,
                     error_details, is_mock, started_at, completed_at)
                VALUES (?, ?, 'joint_investigation', ?, ?, ?, ?, 'reasoning_llm',
                        'failed', ?, ?, ?, ?)""",
                (
                    system_id, snapshot_id, result.provider, result.model,
                    result.prompt_version, result.schema_version, result.error,
                    1 if result.is_mock else 0, started_at, completed_at,
                ),
            )

    if result.stop_reason == "failed" and not finding_outs:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "joint_investigation_failed",
                "message": result.error or "Investigation failed",
                "joint_understanding_id": ju_id,
            },
        )

    return JointUnderstandingInvestigateOut(
        joint_understanding_id=ju_id,
        system_id=system_id,
        stop_reason=result.stop_reason,
        rounds=round_outs,
        findings=finding_outs,
        error=result.error,
    )


# --- Translation (Phase C / #331) ----------------------------------------------


def _translation_out(row) -> JointUnderstandingTranslationOut:
    return JointUnderstandingTranslationOut(
        id=row["id"],
        joint_understanding_id=row["joint_understanding_id"],
        system_id=row["system_id"],
        purpose_summary=row["purpose_summary"],
        statements=[
            JointUnderstandingStatementOut(**s) for s in json.loads(row["statements_json"])
        ],
        options=[
            JointUnderstandingOptionOut(**o) for o in json.loads(row["options_json"])
        ],
        open_unknowns=json.loads(row["open_unknowns"]),
        decision_question=row["decision_question"],
        ask_developer=bool(row["ask_developer"]),
        intelligence_run_id=row["intelligence_run_id"],
        is_mock=bool(row["is_mock"]),
        created_at=row["created_at"],
    )


def _research_state(conn, ju_id: int, ju_row) -> "tuple[Optional[str], bool]":
    """The Question Router's classification and whether research is finished.

    Both come from persisted facts, never from the model's own opinion of how
    complete it is:

    - the classification is the ``interview_qa.route_category`` recorded by the
      「わからない」 entry point (Issue #336) for a ``qa`` origin; other origins
      have no routed question, so it is None and the gate falls back to its
      material rule.
    - research is "exhausted" when the latest round did NOT stop on a budget and
      left no leads or missing evidence behind. A budget stop means more reading
      is possible, which makes asking the developer premature.
    """
    route_category = None
    if ju_row["origin_kind"] == "qa":
        row = conn.execute(
            "SELECT route_category FROM interview_qa WHERE id = ? AND system_id = ?",
            (ju_row["origin_id"], ju_row["system_id"]),
        ).fetchone()
        if row is not None:
            route_category = row["route_category"]
    latest = conn.execute(
        """SELECT stop_reason, search_leads, missing_evidence
           FROM joint_understanding_investigation_round
           WHERE joint_understanding_id = ? ORDER BY id DESC LIMIT 1""",
        (ju_id,),
    ).fetchone()
    if latest is None:
        # No round has run: nothing has been researched, so nothing is
        # exhausted -- the gate holds the question back rather than asking
        # before the system has looked at all.
        return route_category, False
    if latest["stop_reason"] == "budget_exhausted":
        return route_category, False
    leads = json.loads(latest["search_leads"] or "[]")
    missing = json.loads(latest["missing_evidence"] or "[]")
    return route_category, not (leads or missing)


def _action_menu(language: str) -> List[JointUnderstandingActionMenuEntryOut]:
    return [
        JointUnderstandingActionMenuEntryOut(
            action_kind=entry.action_kind,
            label=entry.label,
            what_changes=entry.what_changes,
            formal_operation=entry.formal_operation,
            completes_outside_session=entry.completes_outside_session,
        )
        for entry in build_action_menu(language)
    ]


@router.post(
    "/joint-understanding/{ju_id}/translate",
    response_model=JointUnderstandingTranslateOut,
    status_code=201,
)
def translate_joint_understanding(
    ju_id: int,
    payload: Optional[JointUnderstandingTranslateRequest] = None,
    system_id: int = Depends(get_system_id),
) -> JointUnderstandingTranslateOut:
    """Restate this session's findings in terms of purpose, impact, and choices.

    The translation step may not invent technical facts: its only input is
    the findings already on this session, every sentence it produces must
    cite the finding ids it came from, and a citation the server did not
    supply fails the whole call closed (502, nothing persisted but the failed
    audit row). Each sentence is stored as an ``origin_role='translation'``
    finding, so the plain explanation always resolves back through a finding
    to the code evidence -- generalizing the wording never costs the
    technical provenance (Epic #328).

    The accompanying next-action menu is NOT model output: it is the finite
    ``ACTION_KINDS`` set with fixed server copy describing what choosing each
    one changes, assembled deterministically.

    probe-agent:
      role: API boundary that translates investigation findings into
        purpose/impact statements with mandatory finding traceability
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: io
      state_effects: [database-read, database-write, external-api]
      probe_value: Verify a translation citing an unknown finding id persists nothing, that translated sentences are stored as translation findings with supports_finding_ids, and that the action menu is deterministic server copy rather than model output.
    """
    started_at = time.time()
    request = payload or JointUnderstandingTranslateRequest()

    # Phase 1 (DB lock held): read the findings this translation may use.
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        if ju["status"] != "open":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "joint_understanding_not_open",
                    "message": f"Session is '{ju['status']}', not open",
                },
            )
        question_text = ju["question_text"]
        snapshot_id = ju["premise_snapshot_id"]
        rows = conn.execute(
            """SELECT * FROM joint_understanding_finding
               WHERE joint_understanding_id = ? AND origin_role = 'investigation'
               ORDER BY id""",
            (ju_id,),
        ).fetchall()
        summaries = [
            FindingSummary(
                id=r["id"],
                origin_role=r["origin_role"],
                claim_kind=r["claim_kind"],
                statement=r["statement"],
                uncertainty=r["uncertainty"],
                evidence_count=len(json.loads(r["evidence_json"])),
                competing_explanations=json.loads(r["competing_explanations"]),
            )
            for r in rows
        ]
        route_category, research_exhausted = _research_state(conn, ju_id, ju)

    # Phase 2 (DB lock released): the reasoning call.
    config = LLMConfig.intelligence_from_env()
    try:
        language = get_interview_language()
        client = create_llm_client(config)
        result = translate_findings(
            client, config,
            question=question_text, findings=summaries,
            goal_hint=request.goal_hint, language=language,
        )
    except (LLMError, ValueError) as exc:
        language = resolve_message_language()
        result = TranslationResult(
            provider=config.provider, model=config.model,
            is_mock=config.provider == "mock", error=str(exc),
        )
    completed_at = time.time()

    # Phase 3 (DB lock re-acquired): audit row always; content only on success.
    with get_conn() as conn:
        run_cur = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 error_details, is_mock, started_at, completed_at)
            VALUES (?, ?, 'joint_translation', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)""",
            (
                system_id, snapshot_id, result.provider, result.model,
                result.prompt_version, result.schema_version,
                "failed" if result.error else "completed", result.error,
                1 if result.is_mock else 0, started_at, completed_at,
            ),
        )
        run_id = run_cur.lastrowid

        if result.error:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "joint_translation_failed",
                    "message": result.error,
                    "joint_understanding_id": ju_id,
                    "run_id": run_id,
                },
            )

        known_ids = {
            r["id"]
            for r in conn.execute(
                "SELECT id FROM joint_understanding_finding WHERE joint_understanding_id = ?",
                (ju_id,),
            ).fetchall()
        }
        statements_json: List[Dict[str, object]] = []
        for statement in result.statements:
            try:
                validate_finding(
                    origin_role="translation",
                    claim_kind=statement.claim_kind,
                    decision_method="reasoning_llm",
                    evidence=[],
                    runtime_evidence=[],
                    supports_finding_ids=statement.supports_finding_ids,
                    competing_explanations=[],
                    refutation_conditions=[],
                    next_investigation=None,
                    intelligence_run_id=run_id,
                    is_mock=result.is_mock,
                    known_finding_ids=known_ids,
                )
            except JointUnderstandingValidationError as exc:
                # The translator already applies this contract, so reaching
                # here means the two disagree -- fail the whole call rather
                # than storing a partially-attributable explanation.
                raise HTTPException(
                    status_code=502,
                    detail={
                        "code": "joint_translation_failed",
                        "message": str(exc),
                        "joint_understanding_id": ju_id,
                        "run_id": run_id,
                    },
                )
            cur = conn.execute(
                """INSERT INTO joint_understanding_finding
                    (joint_understanding_id, system_id, origin_role, claim_kind,
                     statement, evidence_json, runtime_evidence_json,
                     supports_finding_ids, competing_explanations,
                     refutation_conditions, next_investigation, uncertainty,
                     supersedes_finding_id, decision_method, intelligence_run_id,
                     is_mock, producer_kind, actor_kind, created_at)
                VALUES (?, ?, 'translation', ?, ?, '[]', '[]', ?, '[]', '[]',
                        NULL, '', NULL, 'reasoning_llm', ?, ?,
                        'translator', 'system', ?)""",
                (
                    ju_id, system_id, statement.claim_kind, statement.text,
                    json.dumps(statement.supports_finding_ids),
                    run_id, 1 if result.is_mock else 0, completed_at,
                ),
            )
            statements_json.append({
                "layer": statement.layer,
                "claim_kind": statement.claim_kind,
                "text": statement.text,
                "supports_finding_ids": list(statement.supports_finding_ids),
                "finding_id": cur.lastrowid,
            })

        should_ask = ask_developer(
            statements=result.statements,
            decision_question=result.decision_question,
            open_unknowns=result.open_unknowns,
            # Issue #339: the gate reads the Question Router's classification
            # and whether investigation is actually finished, so the two stop
            # disagreeing about what the human is for. A `system_researchable`
            # question is never handed over, and a question is held back while
            # more reading could still answer it.
            route_category=route_category,
            research_exhausted=research_exhausted,
        )
        translation_cur = conn.execute(
            """INSERT INTO joint_understanding_translation
                (joint_understanding_id, system_id, purpose_summary,
                 statements_json, options_json, open_unknowns,
                 decision_question, ask_developer, intelligence_run_id,
                 is_mock, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ju_id, system_id, result.purpose_summary,
                json.dumps(statements_json, ensure_ascii=False),
                json.dumps(
                    [
                        {
                            "label": o.label, "what_changes": o.what_changes,
                            "tradeoffs": o.tradeoffs,
                            "supports_finding_ids": list(o.supports_finding_ids),
                        }
                        for o in result.options
                    ],
                    ensure_ascii=False,
                ),
                json.dumps(result.open_unknowns, ensure_ascii=False),
                # Only surfaced as a question when the deterministic gate says
                # the developer actually has something to decide with.
                result.decision_question if should_ask else None,
                1 if should_ask else 0, run_id,
                1 if result.is_mock else 0, completed_at,
            ),
        )
        conn.execute(
            "UPDATE joint_understanding_session SET updated_at = ? WHERE id = ?",
            (completed_at, ju_id),
        )
        row = conn.execute(
            "SELECT * FROM joint_understanding_translation WHERE id = ?",
            (translation_cur.lastrowid,),
        ).fetchone()
        return JointUnderstandingTranslateOut(
            translation=_translation_out(row),
            action_menu=_action_menu(language),
        )


# --- The single 「わからない」 entry point (Issue #336) --------------------------


def _qa_router_context(qa_row) -> str:
    """The same deterministic context string the Question Router already gets."""
    parts = [f"question_category: {qa_row['question_category']}"]
    if qa_row["hypothesis"]:
        parts.append(f"hypothesis: {qa_row['hypothesis']}")
    if qa_row["answer_text"]:
        parts.append(f"existing (unconfirmed) answer: {qa_row['answer_text']}")
    return "; ".join(parts)


@router.post(
    "/interview/sessions/{session_id}/qa/{qa_id}/unknown",
    response_model=InterviewQaUnknownOut,
)
def answer_qa_unknown(
    session_id: int,
    qa_id: int,
    payload: InterviewQaUnknownRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> InterviewQaUnknownOut:
    """「わからない」: record it, then start the investigation it deserves.

    This is Issue #336's single flow. Before it, 「わからない」 was two
    unrelated things bolted together by the Dashboard: the #142 answer flow
    wrote ``status='unconfirmed'`` and knew nothing about Joint Understanding,
    while a separate ``route-and-investigate`` call did a one-shot
    investigation and knew nothing about the answer. A Joint Understanding
    session was never opened from a real 「わからない」 at all.

    The order here is the contract, not a convenience:

    1. **The unknown answer is recorded first, in its own committed
       transaction.** Everything after this point may fail -- the router, the
       reasoning configuration, the investigation -- and the developer keeps
       every option they had: the answer stands, and correcting it, handing it
       off, or answering it later are all still reachable (Issue #336's
       "failure must not cost an existing opportunity").
    2. **The Question Router decides whether to investigate at all.** Only
       ``system_researchable`` and ``hybrid`` open a session; ``human_only``
       does not, because there is nothing in the code to find and opening a
       conversation would just be a slower way of asking the same question
       back. This is the classification's finite output, never a keyword guess.
    3. **``hybrid`` investigates before it asks.** The researchable part is
       consumed first and the translation gate (``understanding_translator.
       ask_developer``) is what decides whether a question still has to reach
       the developer -- so the human is asked only what is left after the
       system did its part.

    ``trigger='unknown_answer'`` is written HERE and only here: the public
    create endpoint forces ``explicit_request``, so the audit distinction
    between an automatic and a deliberate start is a fact about which path ran,
    not a claim in a request body.

    ``next_step`` is a finite value the Dashboard maps to its own copy. The
    internal route names (``system_researchable`` / ``hybrid`` /
    ``human_only``) are deliberately not part of it.

    probe-agent:
      role: API boundary that turns a developer's 「わからない」 into a routed,
        auto-started joint investigation without ever losing the recorded answer
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: io
      state_effects: [database-read, database-write, external-api]
      probe_value: Verify the unknown answer is recorded even when routing or investigation fails, that human_only never opens a session, that system_researchable/hybrid do, and that trigger is recorded as unknown_answer only on this path.
    """
    from ..interview_language import get_interview_language
    from ..investigation_persistence import persist_route_run
    from ..question_router import route_question
    from .interview import _get_qa_or_404, _get_session_or_404, _qa_out, _write_first_qa_answer

    started_at = time.time()

    # Step 1 (committed before anything can fail): the unknown answer itself.
    with get_conn() as conn:
        interview_session = _get_session_or_404(conn, session_id, system_id)
        qa = _get_qa_or_404(conn, qa_id, session_id, system_id)
        if qa["superseded_by_id"] is not None or qa["status"] == "revised":
            raise HTTPException(
                status_code=409,
                detail="This question has already been superseded by a newer revision",
            )
        already_unknown = (
            qa["status"] == "unconfirmed" and bool(qa["answer_unknown"])
        )
        if qa["status"] not in ("open", "skipped") and not already_unknown:
            # A real answer exists. Correcting it is the answer endpoint's job
            # (it supersedes rather than overwrites); silently replacing it with
            # 「わからない」 here would discard an answer the developer gave.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "qa_already_answered",
                    "message": (
                        "This question already has an answer; correct it through "
                        "the answer endpoint instead"
                    ),
                },
            )
        if not already_unknown:
            conn.execute("BEGIN")
            try:
                _write_first_qa_answer(
                    conn, qa_id=qa_id, answer_text=payload.answer_text,
                    answer_unknown=True, status="unconfirmed",
                    actor=principal.username or payload.actor, now=started_at,
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        # Saying 「わからない」 again on an already-unknown question is a RETRY,
        # not a dead end: the recorded answer stands as it is, and the developer
        # gets another attempt at the routing and the investigation that may
        # have failed the first time. Rejecting it would strand exactly the
        # developer whose first attempt did not work.
        qa = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone()
        question_text = qa["question_text"]
        router_context = _qa_router_context(qa)
        snapshot_id = interview_session["snapshot_id"]
        recorded_qa = _qa_out(qa)

    def _out(**overrides) -> InterviewQaUnknownOut:
        base = dict(
            session_id=session_id, system_id=system_id, qa=recorded_qa,
            route_category=None, knowledge_area=None,
            joint_understanding_id=None, next_step="developer_answer_required",
            investigation_stop_reason=None, error=None,
        )
        base.update(overrides)
        return InterviewQaUnknownOut(**base)

    # Step 2 (DB lock released): classify. A failure here is reported, never
    # substituted with a guess (Principle 6) -- and it costs nothing already
    # recorded.
    config = LLMConfig.intelligence_from_env()
    try:
        client = create_llm_client(config)
        route = route_question(
            client, config,
            question_text=question_text, context=router_context,
            language=get_interview_language(),
        )
    except (LLMError, ValueError) as exc:
        return _out(next_step="routing_unavailable", error=str(exc))
    routed_at = time.time()

    # Step 3: persist the route audit row and the classification.
    with get_conn() as conn:
        run_id = persist_route_run(
            conn, system_id=system_id, snapshot_id=snapshot_id, route=route,
            now=started_at, completed_at=routed_at,
        )
        if route.error or route.category is None:
            return _out(next_step="routing_unavailable", error=route.error)
        conn.execute(
            "UPDATE interview_qa SET route_category = ?, route_run_id = ?, "
            "knowledge_area = ? WHERE id = ? AND system_id = ?",
            (route.category, run_id, route.knowledge_area, qa_id, system_id),
        )
        qa = conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone()
        recorded_qa = _qa_out(qa)

        if route.category == "human_only":
            # Nothing in the code can answer this, so opening a joint
            # investigation would only be a slower way of handing the same
            # question back. The normal answer / handoff path is what is next.
            return _out(
                route_category=route.category, knowledge_area=route.knowledge_area,
                next_step="developer_answer_required",
            )

        # Reuse an unfinished conversation on this question rather than opening
        # a second one. A retry has to continue the session that already carries
        # the earlier rounds' leads (#330's continuity), and two open sessions on
        # one question would each be investigating half the history.
        existing = conn.execute(
            """SELECT id FROM joint_understanding_session
               WHERE session_id = ? AND system_id = ? AND origin_kind = 'qa'
                 AND origin_id = ? AND status != 'closed'
               ORDER BY id DESC LIMIT 1""",
            (session_id, system_id, qa_id),
        ).fetchone()
        if existing is not None:
            ju_id = existing["id"]
            conn.execute(
                "UPDATE joint_understanding_session SET status = 'open', "
                "updated_at = ? WHERE id = ?",
                (routed_at, ju_id),
            )
            premise = None
        else:
            premise = capture_premise_bundle(
                conn,
                origin_kind="qa", origin_id=qa_id,
                session_row=interview_session, system_id=system_id, now=routed_at,
            )
        if premise is not None:
            cur = conn.execute(
                """INSERT INTO joint_understanding_session
                    (session_id, system_id, origin_kind, origin_id, trigger,
                     question_text, status, premise_snapshot_id,
                     premise_commit_sha, premise_revision_id,
                     premise_content_hash, premise_capability_digest,
                     premise_intent_digest, premise_review_subject_id,
                     premise_tracking_version, premise_captured_at,
                     schema_version, created_at, updated_at)
                VALUES (?, ?, 'qa', ?, 'unknown_answer', ?, 'open', ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, system_id, qa_id, question_text,
                    premise["premise_snapshot_id"], premise["premise_commit_sha"],
                    premise["premise_revision_id"], premise["premise_content_hash"],
                    premise["premise_capability_digest"],
                    premise["premise_intent_digest"],
                    premise["premise_review_subject_id"],
                    premise["premise_tracking_version"],
                    premise["premise_captured_at"],
                    SCHEMA_VERSION, routed_at, routed_at,
                ),
            )
            ju_id = cur.lastrowid

    if not payload.investigate:
        return _out(
            route_category=route.category, knowledge_area=route.knowledge_area,
            joint_understanding_id=ju_id, next_step="joint_understanding_opened",
        )

    # Step 4 (DB lock released): the investigation itself, through exactly the
    # same endpoint function the JU panel calls -- one investigation
    # implementation, one audit shape, one budget. Its 502 is caught: a failed
    # investigation must not undo the recorded answer or hide the session that
    # is now open and retryable.
    try:
        investigation = investigate_joint_understanding(
            ju_id,
            JointUnderstandingInvestigateRequest(
                research_focus=route.research_focus,
                search_keywords=list(route.search_keywords),
            ),
            system_id=system_id,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return _out(
            route_category=route.category, knowledge_area=route.knowledge_area,
            joint_understanding_id=ju_id, next_step="joint_understanding_opened",
            error=str(detail.get("message") or exc.detail),
        )
    return _out(
        route_category=route.category, knowledge_area=route.knowledge_area,
        joint_understanding_id=ju_id,
        next_step=(
            "joint_investigation_started"
            if investigation.findings
            else "joint_understanding_opened"
        ),
        investigation_stop_reason=investigation.stop_reason,
    )


# --- Developer actions ---------------------------------------------------------


@router.post(
    "/joint-understanding/{ju_id}/actions",
    response_model=JointUnderstandingDetailOut,
    status_code=201,
)
def record_action(
    ju_id: int,
    payload: JointUnderstandingActionCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> JointUnderstandingDetailOut:
    """Record the next understanding action the developer chose.

    This is a record of intent to continue the conversation, never an
    approval: ``adopt_hypothesis`` here does not adopt anything and
    ``decide`` here does not decide the origin item -- both only become an
    outcome through ``/close``, and the origin item's own endpoint remains
    the only way to answer it.

    Issue #337: ``payload.actor`` is kept as a display label, but the recorded
    IDENTITY (``actor_kind`` / ``actor_user_id`` / ``actor_username``) comes
    from the authenticated ``Principal``. A request body can no longer
    attribute a manual action to someone who did not make it.
    """
    now = time.time()
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        if ju["status"] != "open":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "joint_understanding_not_open",
                    "message": f"Session is '{ju['status']}', not open",
                },
            )
        conn.execute(
            """INSERT INTO joint_understanding_action
                (joint_understanding_id, system_id, action_kind, actor,
                 actor_kind, actor_user_id, actor_username, note,
                 decision_method, created_at)
            VALUES (?, ?, ?, ?, 'user', ?, ?, ?, 'manual', ?)""",
            (
                ju_id, system_id, payload.action_kind, payload.actor,
                principal.user_id, principal.username, payload.note, now,
            ),
        )
        conn.execute(
            "UPDATE joint_understanding_session SET updated_at = ? WHERE id = ?",
            (now, ju_id),
        )
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        return _detail(conn, row)


# --- Reflux into the understanding surface (Phase D / #332) --------------------


def _reflux_out(row) -> JointUnderstandingRefluxOut:
    return JointUnderstandingRefluxOut(
        id=row["id"],
        joint_understanding_id=row["joint_understanding_id"],
        system_id=row["system_id"],
        finding_id=row["finding_id"],
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        statement=row["statement"],
        evidence=[
            JointUnderstandingEvidenceOut(**e) for e in json.loads(row["evidence_json"])
        ],
        runtime_evidence=[
            JointUnderstandingRuntimeEvidenceOut(**e)
            for e in json.loads(row["runtime_evidence_json"])
        ],
        decision_method="reasoning_llm",
        intelligence_run_id=row["intelligence_run_id"],
        premise_snapshot_id=row["premise_snapshot_id"],
        created_at=row["created_at"],
    )


def _write_qa_investigation(conn, *, qa_id: int, system_id: int, rows) -> None:
    """Attach refluxed facts to interview_qa's investigation slot.

    This is the same non-answer slot Issue #286's route-and-investigate
    writes: ``investigation_json`` / ``investigation_run_id`` only. It never
    touches ``answer_text``, ``status``, or ``answered_by`` -- the whole point
    of Phase D is that a system-verified fact reaches the understanding
    surface WITHOUT being converted into a human answer first.
    """
    payload = {
        "status": "completed",
        "source": "joint_understanding",
        "conclusion": rows[0]["statement"],
        "key_points": [r["statement"] for r in rows],
        "evidence": [e for r in rows for e in json.loads(r["evidence_json"])],
        "uncertainty": "",
        "confidence": "confirmed",
        "decision_question": None,
    }
    conn.execute(
        "UPDATE interview_qa SET investigation_json = ?, investigation_run_id = ? "
        "WHERE id = ? AND system_id = ?",
        (
            json.dumps(payload, ensure_ascii=False),
            rows[-1]["intelligence_run_id"],
            qa_id, system_id,
        ),
    )


@router.post(
    "/joint-understanding/{ju_id}/reflux",
    response_model=JointUnderstandingRefluxResultOut,
    status_code=201,
)
def reflux_joint_understanding(
    ju_id: int,
    system_id: int = Depends(get_system_id),
) -> JointUnderstandingRefluxResultOut:
    """Attach this session's system-verified facts to the understanding surface.

    Epic #328 replaces the flow where an investigation result only reached
    System Understanding if a human retyped it into an answer field. This
    endpoint does that attachment directly, and specifically NOT as a human
    answer:

    - only ``origin_role='investigation'`` findings whose ``claim_kind`` is
      ``fact`` reflux. An inference, hypothesis, unknown or conflict stays
      inside the conversation -- confidence alone never promotes a hypothesis.
    - every reflux row records ``decision_method='reasoning_llm'``; it is
      never ``manual``, because nobody decided anything here.
    - for a ``qa`` origin the facts also land in ``interview_qa``'s existing
      investigation slot (``investigation_json``/``investigation_run_id``),
      never in ``answer_text``/``status``. Other origins have no such
      non-answer slot -- their built rows are owned by the Alignment /
      Understanding rebuild -- so the ledger row IS the attachment rather
      than a write that a rebuild would silently discard.
    - any premise verdict other than ``current`` refuses the whole call: an
      investigation whose ground has moved, disappeared, or was never
      comparable must not be attached as current understanding. Issue #337
      widened this from ``stale`` alone -- ``missing`` and ``invalid`` used to
      report as ``fresh`` and were let through.

    Repeating the call is idempotent per finding: already-attached facts are
    counted, not duplicated.

    probe-agent:
      role: API boundary that attaches system-verified facts to the
        understanding surface without recording them as a human answer
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: io
      state_effects: [database-read, database-write]
      probe_value: Verify reflux never writes answer_text/status/user_decision, that only investigation facts reflux, that decision_method is never manual, that a stale premise refuses the call, and that repeating it does not duplicate rows.
    """
    now = time.time()
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        verdict = _premise_verdict(conn, ju)
        premise_state = verdict.state
        if premise_state not in ASSERTABLE_PREMISE_STATES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "joint_understanding_premise_not_current",
                    "message": (
                        "The premise this session was investigated against is no "
                        "longer current; re-investigate before attaching its facts"
                    ),
                    "premise_state": premise_state,
                    "premise_reason": verdict.reason_code,
                },
            )
        target_kind = reflux_target_kind(ju["origin_kind"])
        # The Q&A row to attach to is the CURRENT one, not necessarily the one
        # the session pinned: `interview_qa` corrects additively, so an answer
        # revision between opening the session and refluxing leaves the pinned
        # row `revised` and invisible to the dashboard. The premise gate above
        # already confirmed the question itself is unchanged, so the end of
        # that chain is the same question -- writing to the pinned row would
        # attach the facts where nobody reads them.
        facts = resolve_premise_facts(conn, ju)
        target_id = None
        if target_kind == "qa_investigation":
            target_id = (
                facts.current_origin_id if facts is not None else None
            ) or ju["origin_id"]
        findings = conn.execute(
            "SELECT * FROM joint_understanding_finding "
            "WHERE joint_understanding_id = ? ORDER BY id",
            (ju_id,),
        ).fetchall()
        existing = {
            r["finding_id"]
            for r in conn.execute(
                "SELECT finding_id FROM joint_understanding_reflux "
                "WHERE joint_understanding_id = ?",
                (ju_id,),
            ).fetchall()
        }
        # A finding that has been corrected (superseded by a later one) is
        # not attached: the correction chain, not the original, is current.
        superseded = {
            r["supersedes_finding_id"]
            for r in findings
            if r["supersedes_finding_id"] is not None
        }

        skipped_not_fact = 0
        skipped_unverified = 0
        new_ids: List[int] = []
        for finding in findings:
            if not can_reflux(finding["origin_role"], finding["claim_kind"]):
                skipped_not_fact += 1
                continue
            if (
                finding["decision_method"] != "reasoning_llm"
                or finding["intelligence_run_id"] is None
                or bool(finding["is_mock"])
                or (
                    not json.loads(finding["evidence_json"])
                    and not json.loads(finding["runtime_evidence_json"])
                )
            ):
                skipped_unverified += 1
                continue
            if finding["id"] in existing or finding["id"] in superseded:
                continue
            cur = conn.execute(
                """INSERT INTO joint_understanding_reflux
                    (joint_understanding_id, system_id, finding_id, target_kind,
                     target_id, statement, evidence_json, runtime_evidence_json, decision_method,
                     intelligence_run_id, premise_snapshot_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?)""",
                (
                    ju_id, system_id, finding["id"], target_kind, target_id,
                    finding["statement"], finding["evidence_json"],
                    finding["runtime_evidence_json"],
                    finding["intelligence_run_id"], ju["premise_snapshot_id"], now,
                ),
            )
            new_ids.append(cur.lastrowid)
            # Issue #336: the same fact also enters the shared evidence feed,
            # which is what the Understanding / Alignment / Inquiry rebuilds
            # read. Before this, a non-'qa' origin's facts reached the reflux
            # ledger and stopped there -- recorded, attributable, and invisible
            # to every consumer that could have used them.
            publish_joint_understanding_finding(
                conn,
                system_id=system_id,
                session_id=ju["session_id"],
                joint_understanding_id=ju_id,
                origin_kind=ju["origin_kind"],
                origin_id=target_id or ju["origin_id"],
                finding_row=finding,
                premise_snapshot_id=_column(ju, "premise_snapshot_id"),
                premise_commit_sha=_column(ju, "premise_commit_sha"),
                now=now,
            )

        rows = [
            conn.execute(
                "SELECT * FROM joint_understanding_reflux WHERE id = ?", (rid,),
            ).fetchone()
            for rid in new_ids
        ]
        if rows and target_kind == "qa_investigation":
            current_rows = conn.execute(
                """SELECT r.* FROM joint_understanding_reflux AS r
                   JOIN joint_understanding_finding AS f ON f.id = r.finding_id
                   WHERE r.joint_understanding_id = ?
                     AND NOT EXISTS (
                       SELECT 1 FROM joint_understanding_finding AS newer
                       WHERE newer.joint_understanding_id = f.joint_understanding_id
                         AND newer.supersedes_finding_id = f.id
                     )
                   ORDER BY r.id""",
                (ju_id,),
            ).fetchall()
            _write_qa_investigation(
                conn, qa_id=target_id, system_id=system_id, rows=current_rows,
            )
        conn.execute(
            "UPDATE joint_understanding_session SET updated_at = ? WHERE id = ?",
            (now, ju_id),
        )
        return JointUnderstandingRefluxResultOut(
            joint_understanding_id=ju_id,
            system_id=system_id,
            target_kind=target_kind,
            premise_state=premise_state,
            refluxed=[_reflux_out(r) for r in rows],
            already_refluxed=len(existing),
            skipped_not_fact=skipped_not_fact,
            skipped_unverified=skipped_unverified,
        )


# --- Outcome lineage (Issue #338) ----------------------------------------------


# NOT "/joint-understanding/lineage": `GET /joint-understanding/{ju_id}` is
# registered earlier and takes an int path param, so that spelling would try to
# parse "lineage" as an id and 422 before reaching here.
@router.get(
    "/interview/joint-understanding/lineage",
    response_model=JointUnderstandingLineageOut,
)
def get_joint_understanding_lineage(
    session_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> JointUnderstandingLineageOut:
    """The finite outcome-lineage events for this System, plus per-session burden.

    Issue #338's observation surface. The existing metrics count utilization and
    close labels, which cannot answer "did understanding improve" -- an outcome
    label is a claim about the conversation, not an observation of what happened
    afterwards. These events are what happened: which gaps closed, which
    hypotheses were reversed rather than corrected, which questions had to be
    asked twice, which decisions did not hold.

    Everything here is DERIVED from persisted rows (``app/joint_lineage.py``),
    never written at transition time, so the same database always produces the
    same events -- and a stored lifecycle value cannot drift out of sync with
    the facts it describes.

    ``bulk_approval_readiness`` reports the two observation classes Issue #311's
    start condition waits on, as COUNTS with an explicitly unset threshold. It
    is deliberately not a go/no-go: the number that should gate #311 has to be
    decided from real data, and returning a verdict here would be exactly the
    self-reported readiness score Issue #338 forbids.

    probe-agent:
      role: API boundary exposing the derived outcome lineage and per-session
        burden without scoring or combining them
      capability: interactive-system-understanding
      element_type: boundary
      consumers: [dashboard]
      operation_kind: read
      state_effects: [database-read]
      probe_value: Verify the same rows always produce the same events, that no composite score is returned, that #311 readiness reports counts with an unset threshold rather than a verdict, and that another System's sessions never appear.
    """
    now = time.time()
    with get_conn() as conn:
        if session_id is not None:
            _get_interview_session_or_404(conn, session_id, system_id)
        summary = derive_lineage(conn, system_id=system_id, session_id=session_id)
    return JointUnderstandingLineageOut(
        system_id=system_id,
        generated_at=now,
        events=[
            JointUnderstandingLineageEventOut(
                event_kind=event.event_kind,
                subject_kind=event.subject_kind,
                subject_id=event.subject_id,
                session_id=event.session_id,
                joint_understanding_id=event.joint_understanding_id,
                at=event.at,
                supersedes_subject_id=event.supersedes_subject_id,
                detail=event.detail,
            )
            for event in summary.events
        ],
        burdens=[
            JointUnderstandingSessionBurdenOut(
                joint_understanding_id=burden.joint_understanding_id,
                session_id=burden.session_id,
                rounds=burden.rounds,
                developer_actions=burden.developer_actions,
                developer_findings=burden.developer_findings,
                questions_asked=burden.questions_asked,
                reasks=burden.reasks,
            )
            for burden in summary.burdens
        ],
        bulk_approval_readiness=[
            JointUnderstandingBulkApprovalCriterionOut(
                key=criterion.key,
                observed=criterion.observed,
                verdict=criterion.verdict,
                note=criterion.note,
            )
            for criterion in bulk_approval_readiness(summary)
        ],
    )


# --- Status transitions --------------------------------------------------------


def _transition(conn, ju_row, target: str, now: float) -> None:
    try:
        validate_transition(ju_row["status"], target)
    except JointUnderstandingValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_joint_understanding_transition", "message": str(exc)},
        )


@router.post("/joint-understanding/{ju_id}/hold", response_model=JointUnderstandingOut)
def hold_joint_understanding(
    ju_id: int,
    payload: Optional[JointUnderstandingHoldRequest] = None,
    system_id: int = Depends(get_system_id),
) -> JointUnderstandingOut:
    """「今は保留する」. Resumable; nothing about the origin item changes."""
    now = time.time()
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        _transition(conn, ju, "held", now)
        conn.execute(
            "UPDATE joint_understanding_session "
            "SET status = 'held', outcome_reason = COALESCE(?, outcome_reason), updated_at = ? "
            "WHERE id = ?",
            (payload.reason if payload else None, now, ju_id),
        )
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        return _session_out(row, _premise_verdict(conn, row))


@router.post("/joint-understanding/{ju_id}/resume", response_model=JointUnderstandingOut)
def resume_joint_understanding(
    ju_id: int, system_id: int = Depends(get_system_id),
) -> JointUnderstandingOut:
    now = time.time()
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        _transition(conn, ju, "open", now)
        conn.execute(
            "UPDATE joint_understanding_session SET status = 'open', updated_at = ? WHERE id = ?",
            (now, ju_id),
        )
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        return _session_out(row, _premise_verdict(conn, row))


@router.post("/joint-understanding/{ju_id}/close", response_model=JointUnderstandingOut)
def close_joint_understanding(
    ju_id: int,
    payload: JointUnderstandingCloseRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> JointUnderstandingOut:
    """Close the session with an explicitly typed outcome.

    The four meanings Epic #328 requires be kept apart are separate values
    here: ``understood`` / ``doubt_resolved`` / ``hypothesis_adopted``
    (PROVISIONAL -- ``outcome_is_provisional`` is true and it must not be
    reused as a fact) / ``decided`` (the developer's final value judgement),
    plus ``handed_off`` and ``abandoned``.

    None of them writes the origin confirmation item: even ``decided`` only
    records that the developer reached a decision in this conversation. The
    item's own answer/confirm endpoint stays the only way to record it, and
    a closed session is terminal -- continuing means opening a new one.

    Issue #337 made the close itself an audit record. Every close persists WHO
    closed it (from the authenticated ``Principal``, not the body), the
    developer's stated judgement (``outcome_reason``, now required), the basis
    findings, and the premise verdict with its reason code -- so an outcome
    read back after a reload can still say who decided what, on what grounds,
    against which premise. ``hypothesis_adopted`` additionally writes a
    ``joint_understanding_hypothesis_adoption`` row per basis finding, which is
    what lets the provisional adoption be brought back for re-confirmation
    when its premise later moves instead of quietly aging into a fact.
    """
    now = time.time()
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        _transition(conn, ju, "closed", now)
        verdict = _premise_verdict(conn, ju)
        premise_state = verdict.state
        findings = conn.execute(
            "SELECT * FROM joint_understanding_finding "
            "WHERE joint_understanding_id = ? ORDER BY id",
            (ju_id,),
        ).fetchall()
        basis = _basis_findings(findings)
        try:
            validate_outcome_basis(
                outcome=payload.outcome,
                outcome_finding_ids=payload.outcome_finding_ids,
                known_finding_ids=basis.keys(),
                premise_state=premise_state,
                basis_findings=basis,
            )
        except JointUnderstandingValidationError as exc:
            # A premise that is not current is a state conflict (409); a
            # missing, foreign, superseded, mock, or wrong-kind basis is a
            # malformed request (422).
            status_code = (
                409 if premise_state not in ASSERTABLE_PREMISE_STATES else 422
            )
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": "joint_understanding_outcome_rejected",
                    "message": str(exc),
                    "premise_state": premise_state,
                    "premise_reason": verdict.reason_code,
                },
            )
        conn.execute(
            """UPDATE joint_understanding_session
               SET status = 'closed', outcome = ?, outcome_reason = ?,
                   outcome_finding_ids = ?, outcome_premise_state = ?,
                   outcome_premise_reason = ?, closed_by_actor_kind = 'user',
                   closed_by_user_id = ?, closed_by_username = ?,
                   updated_at = ?, closed_at = ?
               WHERE id = ?""",
            (
                payload.outcome, payload.outcome_reason,
                json.dumps(list(payload.outcome_finding_ids)), premise_state,
                verdict.reason_code, principal.user_id, principal.username,
                now, now, ju_id,
            ),
        )
        if payload.outcome == "hypothesis_adopted":
            # One immutable adoption row per adopted hypothesis, capturing the
            # premise it was adopted against. INSERT OR IGNORE keeps a repeated
            # close attempt from duplicating rows; the session transition table
            # already makes 'closed' terminal, so this is belt-and-braces.
            for finding_id in payload.outcome_finding_ids:
                conn.execute(
                    """INSERT OR IGNORE INTO joint_understanding_hypothesis_adoption
                        (joint_understanding_id, system_id, finding_id,
                         adopted_by_actor_kind, adopted_by_user_id,
                         adopted_by_username, adoption_reason,
                         premise_snapshot_id, premise_commit_sha,
                         premise_revision_id, premise_content_hash,
                         premise_capability_digest, premise_intent_digest,
                         decision_method, adopted_at)
                    VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?)""",
                    (
                        ju_id, system_id, finding_id,
                        principal.user_id, principal.username, payload.outcome_reason,
                        _column(ju, "premise_snapshot_id"),
                        _column(ju, "premise_commit_sha"),
                        _column(ju, "premise_revision_id"),
                        _column(ju, "premise_content_hash"),
                        _column(ju, "premise_capability_digest"),
                        _column(ju, "premise_intent_digest"),
                        now,
                    ),
                )
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        return _session_out(row, _premise_verdict(conn, row))
