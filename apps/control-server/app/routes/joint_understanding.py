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
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_system_id
from ..db import get_conn
from ..joint_understanding import (
    ORIGIN_KINDS,
    SCHEMA_VERSION,
    JointUnderstandingValidationError,
    available_action_kinds,
    outcome_is_provisional,
    validate_finding,
    validate_transition,
)
from ..models import (
    JointUnderstandingActionCreate,
    JointUnderstandingActionOut,
    JointUnderstandingCloseRequest,
    JointUnderstandingCreate,
    JointUnderstandingDetailOut,
    JointUnderstandingEvidenceOut,
    JointUnderstandingFindingCreate,
    JointUnderstandingFindingOut,
    JointUnderstandingHoldRequest,
    JointUnderstandingListOut,
    JointUnderstandingOut,
    JointUnderstandingRuntimeEvidenceOut,
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


def _session_out(row) -> JointUnderstandingOut:
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
        premise_snapshot_id=row["premise_snapshot_id"],
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
        note=row["note"],
        decision_method="manual",
        created_at=row["created_at"],
    )


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
    return JointUnderstandingDetailOut(
        session=_session_out(ju_row),
        findings=[_finding_out(f) for f in findings],
        actions=[_action_out(a) for a in actions],
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

    The session pins the interview session's current snapshot as its premise
    so later rounds (Phase B) are never silently rebased onto a newer one.
    """
    now = time.time()
    with get_conn() as conn:
        interview_session = _get_interview_session_or_404(conn, session_id, system_id)
        _require_origin_exists(
            conn,
            origin_kind=payload.origin_kind,
            origin_id=payload.origin_id,
            session_id=session_id,
            system_id=system_id,
        )
        cur = conn.execute(
            """INSERT INTO joint_understanding_session
                (session_id, system_id, origin_kind, origin_id, trigger,
                 question_text, status, premise_snapshot_id, schema_version,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
            (
                session_id, system_id, payload.origin_kind, payload.origin_id,
                payload.trigger, payload.question_text,
                interview_session["snapshot_id"], SCHEMA_VERSION, now, now,
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
            items=[_session_out(r) for r in rows],
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
) -> JointUnderstandingFindingOut:
    """Append one finding to an open Joint Understanding session.

    There is no update or delete counterpart: a correction is a new finding
    carrying ``supersedes_finding_id``, so an explanation can always be
    traced back to the exact claim it came from even after it was revised.

    The role contract (``app/joint_understanding.validate_finding``) is
    enforced here, fail-closed with 422 and nothing persisted:

    - a translation may not carry evidence and must reference at least one
      finding OF THIS SESSION (traceability -- generalized wording always
      resolves back to a technical claim and its evidence),
    - a developer finding is always ``manual`` and never carries an
      intelligence run or mock flag (a model can never speak in the
      developer's name),
    - an investigation hypothesis must list competing explanations and
      refutation conditions (a hypothesis is not a low-confidence claim).
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
        known_ids = {
            r["id"]
            for r in conn.execute(
                "SELECT id FROM joint_understanding_finding WHERE joint_understanding_id = ?",
                (ju_id,),
            ).fetchall()
        }
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
                intelligence_run_id=payload.intelligence_run_id,
                is_mock=payload.is_mock,
                known_finding_ids=known_ids,
                supersedes_finding_id=payload.supersedes_finding_id,
            )
        except JointUnderstandingValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        cur = conn.execute(
            """INSERT INTO joint_understanding_finding
                (joint_understanding_id, system_id, origin_role, claim_kind,
                 statement, evidence_json, runtime_evidence_json,
                 supports_finding_ids, competing_explanations,
                 refutation_conditions, next_investigation, uncertainty,
                 supersedes_finding_id, decision_method, intelligence_run_id,
                 is_mock, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                payload.intelligence_run_id, 1 if payload.is_mock else 0, now,
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
) -> JointUnderstandingDetailOut:
    """Record the next understanding action the developer chose.

    This is a record of intent to continue the conversation, never an
    approval: ``adopt_hypothesis`` here does not adopt anything and
    ``decide`` here does not decide the origin item -- both only become an
    outcome through ``/close``, and the origin item's own endpoint remains
    the only way to answer it.
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
                (joint_understanding_id, system_id, action_kind, actor, note,
                 decision_method, created_at)
            VALUES (?, ?, ?, ?, ?, 'manual', ?)""",
            (ju_id, system_id, payload.action_kind, payload.actor, payload.note, now),
        )
        conn.execute(
            "UPDATE joint_understanding_session SET updated_at = ? WHERE id = ?",
            (now, ju_id),
        )
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        return _detail(conn, row)


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
        return _session_out(row)


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
        return _session_out(row)


@router.post("/joint-understanding/{ju_id}/close", response_model=JointUnderstandingOut)
def close_joint_understanding(
    ju_id: int,
    payload: JointUnderstandingCloseRequest,
    system_id: int = Depends(get_system_id),
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
    """
    now = time.time()
    with get_conn() as conn:
        ju = _get_or_404(conn, ju_id, system_id)
        _transition(conn, ju, "closed", now)
        conn.execute(
            """UPDATE joint_understanding_session
               SET status = 'closed', outcome = ?, outcome_reason = ?,
                   updated_at = ?, closed_at = ?
               WHERE id = ?""",
            (payload.outcome, payload.outcome_reason, now, now, ju_id),
        )
        row = conn.execute(
            "SELECT * FROM joint_understanding_session WHERE id = ?", (ju_id,),
        ).fetchone()
        return _session_out(row)
