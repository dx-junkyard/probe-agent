"""Product Objective / Milestone API (Issue #429, Epic #427).

`docs/product-objective-lineage.md` §10 is the endpoint contract this module
implements against `app/product_objective.py`'s deterministic domain
service. Follows `routes/ux_design.py` exactly:

* Every write derives its actor from the authenticated `Principal` via
  `_principal_actor` -- never from the request body. Every write `Request`
  model is `ConfigDict(extra="forbid")` and carries no `created_by` /
  `decided_by` / `decision_method` / `authored_by_kind` field.
* GET writes nothing -- a page view is never a confirmation.
* Cross-System references are 404, never a distinguishable error (§10:
  "存在を漏らさない").

This module exports TWO routers: `router` (`/product-objectives`) and
`milestone_router` (`/product-milestones`) -- §10's Milestone endpoints are
declared here, in the SAME file as the Objective endpoints, because a
Milestone's identity is inseparable from its owning Objective
(`objective_id` on the identity row, §4.4) and the two share every typed
exception and the `_MESSAGES`/`_reject`/`_raise_for_error` dispatch below.
Both routers are registered on the app by `app/main.py` (not this module --
see the note in `product_objective.py`'s test suite about registering them
directly on a fresh `FastAPI()` instance for route-level tests).

probe-agent:
  role: API boundary for Product Objective / Milestone
  capability: product-objective-lineage
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify every write derives its actor from the authenticated Principal rather than the request body, that GET never mutates state, and that a cross-System objective/milestone/parent/dependency reference returns 404 rather than leaking existence.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from .. import product_objective
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    ProductMilestoneAssessmentCreateRequest,
    ProductMilestoneAssessmentOut,
    ProductMilestoneCreateRequest,
    ProductMilestoneDecisionCreateRequest,
    ProductMilestoneDecisionOut,
    ProductMilestoneDependencyCreateRequest,
    ProductMilestoneDetailOut,
    ProductMilestoneListOut,
    ProductMilestoneOut,
    ProductMilestoneRevisionCreateRequest,
    ProductObjectiveCreateRequest,
    ProductObjectiveDecisionCreateRequest,
    ProductObjectiveDecisionOut,
    ProductObjectiveDetailOut,
    ProductObjectiveListOut,
    ProductObjectiveOut,
    ProductObjectiveParentSetRequest,
    ProductObjectiveRefCreateRequest,
    ProductObjectiveRefOut,
    ProductObjectiveRevisionCreateRequest,
)
from ..product_objective import (
    ArtifactDuplicate,
    DecisionStaleDigest,
    DependencyCycle,
    DependencyDuplicate,
    DependencySelfReference,
    KeyConflict,
    KeyRequired,
    MilestoneNotAssessable,
    NotDecidable,
    NotFound,
    ParentCycle,
    ParentSelfReference,
    ProductObjectiveValidationError,
    RefKindInvalid,
    SourceDuplicate,
    SourceRefUnresolvable,
)

router = APIRouter(prefix="/product-objectives", tags=["product-objective"])
milestone_router = APIRouter(prefix="/product-milestones", tags=["product-objective"])


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for a write. Never a body-supplied
    value (see the module docstring)."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


# --- Finite reject-code mapping (§10.1) ------------------------------------------

_MESSAGES = {
    "product_objective_key_required": "objective_key を指定してください。",
    "product_milestone_key_required": "milestone_key を指定してください。",
    "product_gap_key_required": "gap_key を指定してください。",
    "product_feature_key_required": "feature_key を指定してください。",
    "product_objective_key_conflict": "同じ objective_key が既にこの System に存在します。",
    "product_milestone_key_conflict": "同じ milestone_key が既にこの System に存在します。",
    "product_gap_key_conflict": "同じ gap_key が既にこの System に存在します。",
    "product_feature_key_conflict": "同じ feature_key が既にこの System に存在します。",
    "product_objective_parent_self": "Objective を自分自身の親には設定できません。",
    "product_objective_parent_cycle": "この親付けは循環を作るため設定できません。",
    "product_milestone_dependency_self": "Milestone を自分自身に依存させることはできません。",
    "product_milestone_dependency_cycle": "この依存関係は循環を作るため設定できません。",
    "product_milestone_dependency_duplicate": "同じ依存関係が既に存在します。",
    "product_gap_source_duplicate": "同じ検出元参照が既にこの Gap に存在します。",
    "product_gap_artifact_duplicate": "同じ下流 link が既にこの Gap に存在します。",
    "product_objective_decision_stale_digest": "指定された digest が現在の内容と一致しません。",
    "product_milestone_decision_stale_digest": "指定された digest が現在の内容と一致しません。",
    "product_gap_decision_stale_digest": "指定された digest が現在の内容と一致しません。",
    "product_feature_decision_stale_digest": "指定された digest が現在の内容と一致しません。",
    "product_objective_not_decidable": "この状態からはその決定を記録できません。",
    "product_milestone_not_decidable": "この状態からはその決定を記録できません。",
    "product_gap_not_decidable": "この状態からはその決定を記録できません。",
    "product_feature_not_decidable": "この状態からはその決定を記録できません。",
    "product_milestone_not_assessable": "定義が確定していない Milestone には達成判定を記録できません。",
    "product_ref_kind_invalid": "ref_kind が不正です。",
    "product_source_kind_invalid": "source_kind が不正です。",
    "product_gap_source_ref_unresolvable":
        "この gap code は Functional Lineage の Objective 層でのみ検出されるため、"
        "Gap の検出元としては解決できません。Functional Lineage 画面で確認してください。",
    "product_link_kind_invalid": "link_kind が不正です。",
}


def _reject(code: str, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": _MESSAGES[code]})


def _raise_for_error(exc: Exception) -> None:
    """Translate one `app.product_objective` exception into the exact
    §10.1 HTTP response. Subclasses of `NotFound` are checked before the
    generic `NotFound` fallback. Re-raises anything unrecognized."""
    if isinstance(exc, KeyRequired):
        raise _reject(f"product_{exc.kind}_key_required", 422)
    if isinstance(exc, KeyConflict):
        raise _reject(f"product_{exc.kind}_key_conflict", 409)
    if isinstance(exc, ParentSelfReference):
        raise _reject("product_objective_parent_self", 422)
    if isinstance(exc, ParentCycle):
        raise _reject("product_objective_parent_cycle", 422)
    if isinstance(exc, DependencySelfReference):
        raise _reject("product_milestone_dependency_self", 422)
    if isinstance(exc, DependencyCycle):
        raise _reject("product_milestone_dependency_cycle", 422)
    if isinstance(exc, DependencyDuplicate):
        raise _reject("product_milestone_dependency_duplicate", 409)
    if isinstance(exc, SourceRefUnresolvable):
        raise _reject("product_gap_source_ref_unresolvable", 422)
    if isinstance(exc, SourceDuplicate):
        raise _reject("product_gap_source_duplicate", 409)
    if isinstance(exc, ArtifactDuplicate):
        raise _reject("product_gap_artifact_duplicate", 409)
    if isinstance(exc, MilestoneNotAssessable):
        raise _reject("product_milestone_not_assessable", 422)
    if isinstance(exc, DecisionStaleDigest):
        raise _reject(f"product_{exc.kind}_decision_stale_digest", 409)
    if isinstance(exc, NotDecidable):
        raise _reject(f"product_{exc.kind}_not_decidable", 422)
    if isinstance(exc, RefKindInvalid):
        raise _reject("product_ref_kind_invalid", 422)
    if isinstance(exc, NotFound):
        raise HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, ProductObjectiveValidationError):
        raise HTTPException(status_code=422, detail=str(exc))
    raise


# --- Objectives --------------------------------------------------------------------


@router.get("", response_model=ProductObjectiveListOut)
def list_objectives_endpoint(system_id: int = Depends(get_system_id)) -> ProductObjectiveListOut:
    with get_conn() as conn:
        result = product_objective.list_objectives(conn, system_id)
    return ProductObjectiveListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("", response_model=ProductObjectiveOut, status_code=201)
def create_objective_endpoint(
    payload: ProductObjectiveCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductObjectiveOut:
    with get_conn() as conn:
        try:
            product_objective.create_objective(
                conn, system_id=system_id, objective_key=payload.objective_key, created_by=_principal_actor(principal)
            )
            detail = product_objective.get_objective_detail(conn, system_id, payload.objective_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductObjectiveOut(**detail)


@router.get("/{objective_key}", response_model=ProductObjectiveDetailOut)
def get_objective_endpoint(objective_key: str, system_id: int = Depends(get_system_id)) -> ProductObjectiveDetailOut:
    with get_conn() as conn:
        try:
            detail = product_objective.get_objective_detail(conn, system_id, objective_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductObjectiveDetailOut(**detail)


@router.post("/{objective_key}/revisions", response_model=ProductObjectiveOut, status_code=201)
def add_objective_revision_endpoint(
    objective_key: str,
    payload: ProductObjectiveRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductObjectiveOut:
    with get_conn() as conn:
        try:
            detail = product_objective.add_objective_revision(
                conn,
                system_id=system_id,
                objective_key=objective_key,
                title=payload.title,
                intent=payload.intent,
                contribution=payload.contribution,
                scope_note=payload.scope_note,
                summary=payload.summary,
                change_note=payload.change_note,
                authored_by_kind="developer",
                decision_method="manual",
                intelligence_run_id=None,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductObjectiveOut(**detail)


@router.post("/{objective_key}/parent", response_model=ProductObjectiveOut, status_code=201)
def set_objective_parent_endpoint(
    objective_key: str,
    payload: ProductObjectiveParentSetRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductObjectiveOut:
    with get_conn() as conn:
        try:
            out = product_objective.set_objective_parent(
                conn,
                system_id=system_id,
                objective_key=objective_key,
                parent_objective_key=payload.parent_objective_key,
                rationale=payload.rationale,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductObjectiveOut(**out)


@router.delete("/{objective_key}/parent", response_model=ProductObjectiveOut)
def clear_objective_parent_endpoint(
    objective_key: str,
    # Detaching an Objective is a product decision, so it is recorded like
    # one (§4.4): the tombstone row carries this rationale alongside the
    # actor and the timestamp. It rides as a query parameter because a
    # DELETE body is not reliably transported; it is optional because the
    # WHO and WHEN are captured either way.
    rationale: str = "",
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductObjectiveOut:
    with get_conn() as conn:
        try:
            out = product_objective.clear_objective_parent(
                conn,
                system_id=system_id,
                objective_key=objective_key,
                rationale=rationale,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductObjectiveOut(**out)


@router.post("/{objective_key}/upstream-refs", response_model=ProductObjectiveRefOut, status_code=201)
def add_objective_upstream_ref_endpoint(
    objective_key: str,
    payload: ProductObjectiveRefCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductObjectiveRefOut:
    with get_conn() as conn:
        try:
            out = product_objective.add_objective_upstream_ref(
                conn,
                system_id=system_id,
                objective_key=objective_key,
                ref_kind=payload.ref_kind,
                target_ref=payload.target_ref,
                note=payload.note,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductObjectiveRefOut(**out)


@router.post("/{objective_key}/decisions", response_model=ProductObjectiveDecisionOut, status_code=201)
def record_objective_decision_endpoint(
    objective_key: str,
    payload: ProductObjectiveDecisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductObjectiveDecisionOut:
    with get_conn() as conn:
        try:
            row = product_objective.record_objective_decision(
                conn,
                system_id=system_id,
                objective_key=objective_key,
                decision=payload.decision,
                rationale=payload.rationale,
                captured_digest=payload.captured_digest,
                decided_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductObjectiveDecisionOut(**row)


@router.get("/{objective_key}/milestones", response_model=ProductMilestoneListOut)
def list_milestones_endpoint(objective_key: str, system_id: int = Depends(get_system_id)) -> ProductMilestoneListOut:
    with get_conn() as conn:
        try:
            result = product_objective.list_milestones(conn, system_id, objective_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductMilestoneListOut(system_id=system_id, generated_at=time.time(), **result)


# --- Milestones ----------------------------------------------------------------


@milestone_router.post("", response_model=ProductMilestoneOut, status_code=201)
def create_milestone_endpoint(
    payload: ProductMilestoneCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductMilestoneOut:
    with get_conn() as conn:
        try:
            product_objective.create_milestone(
                conn,
                system_id=system_id,
                objective_key=payload.objective_key,
                milestone_key=payload.milestone_key,
                created_by=_principal_actor(principal),
            )
            detail = product_objective.get_milestone_detail(conn, system_id, payload.milestone_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductMilestoneOut(**detail)


@milestone_router.get("/{milestone_key}", response_model=ProductMilestoneDetailOut)
def get_milestone_endpoint(milestone_key: str, system_id: int = Depends(get_system_id)) -> ProductMilestoneDetailOut:
    with get_conn() as conn:
        try:
            detail = product_objective.get_milestone_detail(conn, system_id, milestone_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductMilestoneDetailOut(**detail)


@milestone_router.post("/{milestone_key}/revisions", response_model=ProductMilestoneOut, status_code=201)
def add_milestone_revision_endpoint(
    milestone_key: str,
    payload: ProductMilestoneRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductMilestoneOut:
    with get_conn() as conn:
        try:
            detail = product_objective.add_milestone_revision(
                conn,
                system_id=system_id,
                milestone_key=milestone_key,
                title=payload.title,
                target_state=payload.target_state,
                verification_method=payload.verification_method,
                verification_note=payload.verification_note,
                sequence_hint=payload.sequence_hint,
                summary=payload.summary,
                change_note=payload.change_note,
                authored_by_kind="developer",
                decision_method="manual",
                intelligence_run_id=None,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductMilestoneOut(**detail)


@milestone_router.post("/{milestone_key}/dependencies", response_model=ProductMilestoneOut, status_code=201)
def add_milestone_dependency_endpoint(
    milestone_key: str,
    payload: ProductMilestoneDependencyCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductMilestoneOut:
    with get_conn() as conn:
        try:
            detail = product_objective.add_milestone_dependency(
                conn,
                system_id=system_id,
                milestone_key=milestone_key,
                depends_on_milestone_key=payload.depends_on_milestone_key,
                rationale=payload.rationale,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductMilestoneOut(**detail)


@milestone_router.post("/{milestone_key}/decisions", response_model=ProductMilestoneDecisionOut, status_code=201)
def record_milestone_decision_endpoint(
    milestone_key: str,
    payload: ProductMilestoneDecisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductMilestoneDecisionOut:
    with get_conn() as conn:
        try:
            row = product_objective.record_milestone_decision(
                conn,
                system_id=system_id,
                milestone_key=milestone_key,
                decision=payload.decision,
                rationale=payload.rationale,
                captured_digest=payload.captured_digest,
                decided_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductMilestoneDecisionOut(**row)


@milestone_router.post("/{milestone_key}/assessments", response_model=ProductMilestoneAssessmentOut, status_code=201)
def record_milestone_assessment_endpoint(
    milestone_key: str,
    payload: ProductMilestoneAssessmentCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductMilestoneAssessmentOut:
    with get_conn() as conn:
        try:
            row = product_objective.record_milestone_assessment(
                conn,
                system_id=system_id,
                milestone_key=milestone_key,
                assessment=payload.assessment,
                rationale=payload.rationale,
                evidence_note=payload.evidence_note,
                captured_digest=payload.captured_digest,
                assessed_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductMilestoneAssessmentOut(**row)
