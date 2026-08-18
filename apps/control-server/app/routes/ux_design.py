"""UX Design Lineage: Journey / Requirement / Artifact API (Issue #407,
Epic #405).

`docs/ux-design-lineage.md` §2.10 is the endpoint contract this module
implements against `app/ux_design.py`'s deterministic domain service. What
this boundary deliberately does NOT do:

* It never accepts `created_by` / `decided_by` / `decision_method` /
  `authored_by_kind` from a request body -- every write request model is
  `ConfigDict(extra="forbid")` and carries none of those fields; they are
  always derived from the route path and the authenticated `Principal`
  (#337's rule: an unverifiable body-supplied identity lets a caller
  fabricate an audit trail).
* GET writes nothing -- a page view is never a confirmation (§0 invariant
  10 / #382's rule, applied here).
* `POST /ux-design/artifact-references` is called with NO `get_conn()`
  connection held open on this thread, because
  `ux_design.create_artifact_reference` shells out to `git` internally and
  manages its own short-lived connections around that call (see its
  docstring and `.claude/skills/control-server/SKILL.md`).

probe-agent:
  role: API boundary for UX Journey / Requirement / Artifact Reference / decision ledger
  capability: ux-design-lineage
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify every write derives created_by/decided_by/decision_method from the authenticated Principal rather than the request body, that GET never mutates state, and that artifact-reference creation never holds a database connection open across its git subprocess call.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import ux_design
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    UxArtifactReferenceCreateRequest,
    UxArtifactReferenceOut,
    UxDesignDecisionCreateRequest,
    UxDesignDecisionOut,
    UxJourneyCreateRequest,
    UxJourneyDetailOut,
    UxJourneyDiffOut,
    UxJourneyListOut,
    UxJourneyOut,
    UxJourneyRevisionCreateRequest,
    UxJourneyRevisionListOut,
    UxJourneyUpstreamRefCreateRequest,
    UxJourneyUpstreamRefOut,
    UxRequirementCreateRequest,
    UxRequirementDetailOut,
    UxRequirementDiffOut,
    UxRequirementListOut,
    UxRequirementOut,
    UxRequirementRevisionCreateRequest,
    UxRequirementRevisionListOut,
    UxRequirementStepLinkCreateRequest,
    UxRequirementStepLinkOut,
)
from ..ux_design import (
    ArtifactHashRequired,
    ArtifactUriInvalid,
    BaselineForeignSystem,
    BaselineNotAsIs,
    CriterionKeyDuplicated,
    DecisionStaleDigest,
    KeyConflict,
    KeyRequired,
    NotDecidable,
    NotFound,
    OutOfScopeNotVerifiable,
    StepKeyDuplicated,
    StepNotFound,
    SubjectNotFound,
    UxDesignValidationError,
)

router = APIRouter(prefix="/ux-design", tags=["ux-design"])


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for a write. Never a body-supplied
    value (see the module docstring)."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


# --- Finite reject-code mapping (§2.10) -----------------------------------------

_MESSAGES: Dict[str, str] = {
    "ux_design_key_required": "key を指定してください。",
    "ux_design_key_conflict": "同じ key が既にこの System に存在します。",
    "journey_baseline_not_as_is": "baseline には as_is の Journey のみ指定できます。",
    "journey_baseline_foreign_system": "指定された baseline Journey が見つかりません。",
    "journey_step_key_duplicated": "同じ revision 内に同じ step_key が指定されています。",
    "journey_step_not_found": "指定された step_key が現在の revision に見つかりません。",
    "ux_requirement_criterion_key_duplicated": "同じ revision 内に同じ criterion_key が指定されています。",
    "out_of_scope_requirement_not_verifiable": "out_of_scope の Requirement には受入条件を設定できません。",
    "artifact_uri_invalid": "artifact の uri が不正です。",
    "artifact_hash_required": "content_hash を指定してください。",
    "ux_design_subject_not_found": "決定の対象が見つかりません。",
    "ux_design_decision_stale_digest": "指定された digest が現在の内容と一致しません。",
    "ux_design_not_decidable": "この状態からはその決定を記録できません。",
}


def _reject(code: str, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": _MESSAGES[code]})


def _raise_for_ux_error(exc: Exception) -> None:
    """Translate one `app.ux_design` exception into the exact §2.10 HTTP
    response. Subclasses of `NotFound` are checked before the generic
    `NotFound` fallback. Re-raises anything unrecognized."""
    if isinstance(exc, KeyRequired):
        raise _reject("ux_design_key_required", 422)
    if isinstance(exc, KeyConflict):
        raise _reject("ux_design_key_conflict", 409)
    if isinstance(exc, BaselineNotAsIs):
        raise _reject("journey_baseline_not_as_is", 422)
    if isinstance(exc, BaselineForeignSystem):
        raise _reject("journey_baseline_foreign_system", 404)
    if isinstance(exc, StepKeyDuplicated):
        raise _reject("journey_step_key_duplicated", 422)
    if isinstance(exc, StepNotFound):
        raise _reject("journey_step_not_found", 404)
    if isinstance(exc, CriterionKeyDuplicated):
        raise _reject("ux_requirement_criterion_key_duplicated", 422)
    if isinstance(exc, OutOfScopeNotVerifiable):
        raise _reject("out_of_scope_requirement_not_verifiable", 422)
    if isinstance(exc, ArtifactUriInvalid):
        raise _reject("artifact_uri_invalid", 422)
    if isinstance(exc, ArtifactHashRequired):
        raise _reject("artifact_hash_required", 422)
    if isinstance(exc, SubjectNotFound):
        raise _reject("ux_design_subject_not_found", 404)
    if isinstance(exc, DecisionStaleDigest):
        raise _reject("ux_design_decision_stale_digest", 409)
    if isinstance(exc, NotDecidable):
        raise _reject("ux_design_not_decidable", 422)
    if isinstance(exc, NotFound):
        raise HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, UxDesignValidationError):
        raise HTTPException(status_code=422, detail=str(exc))
    raise


# --- Journeys --------------------------------------------------------------------


@router.get("/journeys", response_model=UxJourneyListOut)
def list_journeys_endpoint(system_id: int = Depends(get_system_id)) -> UxJourneyListOut:
    with get_conn() as conn:
        result = ux_design.list_journeys(conn, system_id)
    return UxJourneyListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("/journeys", response_model=UxJourneyOut, status_code=201)
def create_journey_endpoint(
    payload: UxJourneyCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UxJourneyOut:
    with get_conn() as conn:
        try:
            row = ux_design.create_journey(
                conn,
                system_id=system_id,
                journey_key=payload.journey_key,
                perspective=payload.perspective,
                baseline_mode=payload.baseline_mode,
                baseline_journey_id=payload.baseline_journey_id,
                created_by=_principal_actor(principal),
            )
            detail = ux_design.get_journey_detail(conn, system_id, row["journey_key"])
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxJourneyOut(**detail)


@router.get("/journeys/{journey_key}", response_model=UxJourneyDetailOut)
def get_journey_endpoint(journey_key: str, system_id: int = Depends(get_system_id)) -> UxJourneyDetailOut:
    with get_conn() as conn:
        try:
            detail = ux_design.get_journey_detail(conn, system_id, journey_key)
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxJourneyDetailOut(**detail)


@router.post("/journeys/{journey_key}/revisions", response_model=UxJourneyDetailOut, status_code=201)
def add_journey_revision_endpoint(
    journey_key: str,
    payload: UxJourneyRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UxJourneyDetailOut:
    with get_conn() as conn:
        try:
            detail = ux_design.add_journey_revision(
                conn,
                system_id=system_id,
                journey_key=journey_key,
                title=payload.title,
                beneficiary=payload.beneficiary,
                usage_context=payload.usage_context,
                entry_trigger=payload.entry_trigger,
                value_arrival=payload.value_arrival,
                summary=payload.summary,
                change_note=payload.change_note,
                steps=[s.model_dump() for s in payload.steps],
                authored_by_kind="developer",
                decision_method="manual",
                intelligence_run_id=None,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxJourneyDetailOut(**detail)


@router.get("/journeys/{journey_key}/revisions", response_model=UxJourneyRevisionListOut)
def list_journey_revisions_endpoint(
    journey_key: str, system_id: int = Depends(get_system_id)
) -> UxJourneyRevisionListOut:
    with get_conn() as conn:
        try:
            result = ux_design.list_journey_revisions(conn, system_id, journey_key)
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxJourneyRevisionListOut(
        system_id=system_id, journey_key=journey_key, generated_at=time.time(), **result
    )


@router.get("/journeys/{journey_key}/diff", response_model=UxJourneyDiffOut)
def diff_journey_endpoint(
    journey_key: str,
    from_revision: Optional[int] = Query(default=None),
    to_revision: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> UxJourneyDiffOut:
    with get_conn() as conn:
        try:
            result = ux_design.diff_journey_revisions(conn, system_id, journey_key, from_revision, to_revision)
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxJourneyDiffOut(system_id=system_id, journey_key=journey_key, generated_at=time.time(), **result)


@router.get("/journeys/{journey_key}/baseline-diff", response_model=UxJourneyDiffOut)
def baseline_diff_journey_endpoint(
    journey_key: str, system_id: int = Depends(get_system_id)
) -> UxJourneyDiffOut:
    with get_conn() as conn:
        try:
            result = ux_design.baseline_diff_journey(conn, system_id, journey_key)
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxJourneyDiffOut(system_id=system_id, journey_key=journey_key, generated_at=time.time(), **result)


@router.post("/journeys/{journey_key}/upstream-refs", response_model=UxJourneyUpstreamRefOut, status_code=201)
def add_upstream_ref_endpoint(
    journey_key: str,
    payload: UxJourneyUpstreamRefCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UxJourneyUpstreamRefOut:
    with get_conn() as conn:
        try:
            out = ux_design.add_upstream_ref(
                conn,
                system_id=system_id,
                journey_key=journey_key,
                ref_kind=payload.ref_kind,
                target_ref=payload.target_ref,
                note=payload.note,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxJourneyUpstreamRefOut(**out)


# --- Requirements ------------------------------------------------------------------


@router.get("/requirements", response_model=UxRequirementListOut)
def list_requirements_endpoint(system_id: int = Depends(get_system_id)) -> UxRequirementListOut:
    with get_conn() as conn:
        result = ux_design.list_requirements(conn, system_id)
    return UxRequirementListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("/requirements", response_model=UxRequirementOut, status_code=201)
def create_requirement_endpoint(
    payload: UxRequirementCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UxRequirementOut:
    with get_conn() as conn:
        try:
            row = ux_design.create_requirement(
                conn,
                system_id=system_id,
                requirement_key=payload.requirement_key,
                requirement_kind=payload.requirement_kind,
                created_by=_principal_actor(principal),
            )
            detail = ux_design.get_requirement_detail(conn, system_id, row["requirement_key"])
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxRequirementOut(**detail)


@router.get("/requirements/{requirement_key}", response_model=UxRequirementDetailOut)
def get_requirement_endpoint(
    requirement_key: str, system_id: int = Depends(get_system_id)
) -> UxRequirementDetailOut:
    with get_conn() as conn:
        try:
            detail = ux_design.get_requirement_detail(conn, system_id, requirement_key)
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxRequirementDetailOut(**detail)


@router.post(
    "/requirements/{requirement_key}/revisions", response_model=UxRequirementDetailOut, status_code=201
)
def add_requirement_revision_endpoint(
    requirement_key: str,
    payload: UxRequirementRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UxRequirementDetailOut:
    with get_conn() as conn:
        try:
            detail = ux_design.add_requirement_revision(
                conn,
                system_id=system_id,
                requirement_key=requirement_key,
                statement=payload.statement,
                rationale=payload.rationale,
                constraint_text=payload.constraint_text,
                out_of_scope_note=payload.out_of_scope_note,
                change_note=payload.change_note,
                acceptance_criteria=[c.model_dump() for c in payload.acceptance_criteria],
                authored_by_kind="developer",
                decision_method="manual",
                intelligence_run_id=None,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxRequirementDetailOut(**detail)


@router.get("/requirements/{requirement_key}/revisions", response_model=UxRequirementRevisionListOut)
def list_requirement_revisions_endpoint(
    requirement_key: str, system_id: int = Depends(get_system_id)
) -> UxRequirementRevisionListOut:
    with get_conn() as conn:
        try:
            result = ux_design.list_requirement_revisions(conn, system_id, requirement_key)
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxRequirementRevisionListOut(
        system_id=system_id, requirement_key=requirement_key, generated_at=time.time(), **result
    )


@router.get("/requirements/{requirement_key}/diff", response_model=UxRequirementDiffOut)
def diff_requirement_endpoint(
    requirement_key: str,
    from_revision: Optional[int] = Query(default=None),
    to_revision: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> UxRequirementDiffOut:
    with get_conn() as conn:
        try:
            result = ux_design.diff_requirement_revisions(
                conn, system_id, requirement_key, from_revision, to_revision
            )
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxRequirementDiffOut(
        system_id=system_id, requirement_key=requirement_key, generated_at=time.time(), **result
    )


@router.post(
    "/requirements/{requirement_key}/step-links", response_model=UxRequirementStepLinkOut, status_code=201
)
def add_requirement_step_link_endpoint(
    requirement_key: str,
    payload: UxRequirementStepLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UxRequirementStepLinkOut:
    with get_conn() as conn:
        try:
            out = ux_design.add_requirement_step_link(
                conn,
                system_id=system_id,
                requirement_key=requirement_key,
                journey_key=payload.journey_key,
                step_key=payload.step_key,
                note=payload.note,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxRequirementStepLinkOut(**out)


# --- Artifact references + the decision ledger ------------------------------------


@router.post("/artifact-references", response_model=UxArtifactReferenceOut, status_code=201)
def create_artifact_reference_endpoint(
    payload: UxArtifactReferenceCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UxArtifactReferenceOut:
    # No `get_conn()` block here: `create_artifact_reference` shells out to
    # `git` and manages its own short-lived connections internally (see its
    # docstring). Wrapping this call in a connection would hold the
    # process-wide DB lock across that subprocess call.
    try:
        row = ux_design.create_artifact_reference(
            system_id=system_id,
            subject_kind=payload.subject_kind,
            subject_key=payload.subject_key,
            artifact_kind=payload.artifact_kind,
            title=payload.title,
            uri=payload.uri,
            media_type=payload.media_type,
            content_hash=payload.content_hash,
            byte_size=payload.byte_size,
            decision_method="manual",
            created_by=_principal_actor(principal),
        )
    except Exception as exc:
        _raise_for_ux_error(exc)
        raise
    return UxArtifactReferenceOut(**row)


@router.post("/decisions", response_model=UxDesignDecisionOut, status_code=201)
def record_design_decision_endpoint(
    payload: UxDesignDecisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> UxDesignDecisionOut:
    with get_conn() as conn:
        try:
            row = ux_design.record_design_decision(
                conn,
                system_id=system_id,
                subject_kind=payload.subject_kind,
                subject_key=payload.subject_key,
                decision=payload.decision,
                rationale=payload.rationale,
                captured_digest=payload.captured_digest,
                decided_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_ux_error(exc)
            raise
    return UxDesignDecisionOut(**row)
