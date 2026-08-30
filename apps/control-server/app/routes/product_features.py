"""Product Feature API (Issue #431, Epic #427).

`docs/product-objective-lineage.md` §7.2 / §10 is the endpoint contract this
module implements against `app/product_feature.py`'s deterministic domain
service. Modelled closely on `routes/ux_design.py`:

* It never accepts `created_by` / `decided_by` / `decision_method` from a
  request body -- every write request model is `ConfigDict(extra="forbid")`
  and carries none of those fields; they are always derived from the route
  path and the authenticated `Principal` (#337's rule: an unverifiable
  body-supplied identity lets a caller fabricate an audit trail).
* GET writes nothing -- a page view is never a confirmation (§0 invariant
  10).
* Cross-System references resolve as 404, never a distinguishable "exists
  in another System" response (§12's System isolation requirement).

**This router is not yet registered in `app/main.py`** -- that wiring
belongs to the orchestrating change across #429-#432's routers together
(see the task brief). Tests that exercise this router over HTTP register it
onto the shared app themselves.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from .. import product_feature
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    ProductFeatureCapabilityLinkCreateRequest,
    ProductFeatureCapabilityLinkOut,
    ProductFeatureCreateRequest,
    ProductFeatureDecisionCreateRequest,
    ProductFeatureDecisionOut,
    ProductFeatureDetailOut,
    ProductFeatureDraftLinkCreateRequest,
    ProductFeatureDraftLinkOut,
    ProductFeatureListOut,
    ProductFeatureOut,
    ProductFeatureRequirementLinkCreateRequest,
    ProductFeatureRequirementLinkOut,
    ProductFeatureRevisionCreateRequest,
    ProductFeatureTargetLinkCreateRequest,
    ProductFeatureTargetLinkOut,
)
from ..product_feature import (
    CapabilityNotFound,
    DecisionStaleDigest,
    DraftNotFound,
    KeyConflict,
    KeyRequired,
    LinkKindInvalid,
    NotDecidable,
    NotFound,
    ProductFeatureValidationError,
    RequirementNotFound,
    SubjectNotFound,
)

router = APIRouter(prefix="/product-features", tags=["product-feature"])


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for a write. Never a body-supplied
    value (see the module docstring)."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


# --- Finite reject-code mapping (§10.1) ------------------------------------------

_MESSAGES: Dict[str, str] = {
    "product_feature_key_required": "feature_key を指定してください。",
    "product_feature_key_conflict": "同じ feature_key が既にこの System に存在します。",
    "product_feature_decision_stale_digest": "指定された digest が現在の内容と一致しません。",
    "product_feature_not_decidable": "この状態からはその決定を記録できません。",
    "product_link_kind_invalid": "link_kind が不正です。",
}


def _reject(code: str, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": _MESSAGES[code]})


def _raise_for_error(exc: Exception) -> None:
    """Translate one `app.product_feature` exception into the exact §10.1
    HTTP response. Subclasses of `NotFound` are checked before the generic
    `NotFound` fallback. Re-raises anything unrecognized."""
    if isinstance(exc, KeyRequired):
        raise _reject("product_feature_key_required", 422)
    if isinstance(exc, KeyConflict):
        raise _reject("product_feature_key_conflict", 409)
    if isinstance(exc, DecisionStaleDigest):
        raise _reject("product_feature_decision_stale_digest", 409)
    if isinstance(exc, NotDecidable):
        raise _reject("product_feature_not_decidable", 422)
    if isinstance(exc, LinkKindInvalid):
        raise _reject("product_link_kind_invalid", 422)
    if isinstance(exc, (RequirementNotFound, CapabilityNotFound, DraftNotFound, SubjectNotFound)):
        raise HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, NotFound):
        raise HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, ProductFeatureValidationError):
        raise HTTPException(status_code=422, detail=str(exc))
    raise


# --- Features ----------------------------------------------------------------------


@router.get("", response_model=ProductFeatureListOut)
def list_features_endpoint(system_id: int = Depends(get_system_id)) -> ProductFeatureListOut:
    with get_conn() as conn:
        result = product_feature.list_features(conn, system_id)
    return ProductFeatureListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("", response_model=ProductFeatureOut, status_code=201)
def create_feature_endpoint(
    payload: ProductFeatureCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductFeatureOut:
    with get_conn() as conn:
        try:
            row = product_feature.create_feature(
                conn, system_id=system_id, feature_key=payload.feature_key,
                created_by=_principal_actor(principal),
            )
            detail = product_feature.get_feature_detail(conn, system_id, row["feature_key"])
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductFeatureOut(**detail)


@router.get("/{feature_key}", response_model=ProductFeatureDetailOut)
def get_feature_endpoint(feature_key: str, system_id: int = Depends(get_system_id)) -> ProductFeatureDetailOut:
    with get_conn() as conn:
        try:
            detail = product_feature.get_feature_detail(conn, system_id, feature_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductFeatureDetailOut(**detail)


@router.post("/{feature_key}/revisions", response_model=ProductFeatureOut, status_code=201)
def add_feature_revision_endpoint(
    feature_key: str,
    payload: ProductFeatureRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductFeatureOut:
    with get_conn() as conn:
        try:
            detail = product_feature.add_feature_revision(
                conn,
                system_id=system_id,
                feature_key=feature_key,
                title=payload.title,
                statement=payload.statement,
                rationale=payload.rationale,
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
    return ProductFeatureOut(**detail)


@router.post(
    "/{feature_key}/requirement-links", response_model=ProductFeatureRequirementLinkOut, status_code=201
)
def add_requirement_link_endpoint(
    feature_key: str,
    payload: ProductFeatureRequirementLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductFeatureRequirementLinkOut:
    with get_conn() as conn:
        try:
            out = product_feature.add_requirement_link(
                conn,
                system_id=system_id,
                feature_key=feature_key,
                requirement_key=payload.requirement_key,
                note=payload.note,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductFeatureRequirementLinkOut(**out)


@router.post(
    "/{feature_key}/capability-links", response_model=ProductFeatureCapabilityLinkOut, status_code=201
)
def add_capability_link_endpoint(
    feature_key: str,
    payload: ProductFeatureCapabilityLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductFeatureCapabilityLinkOut:
    with get_conn() as conn:
        try:
            out = product_feature.add_capability_link(
                conn,
                system_id=system_id,
                feature_key=feature_key,
                capability_entity_id=payload.capability_entity_id,
                note=payload.note,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductFeatureCapabilityLinkOut(**out)


@router.post("/{feature_key}/target-links", response_model=ProductFeatureTargetLinkOut, status_code=201)
def add_target_link_endpoint(
    feature_key: str,
    payload: ProductFeatureTargetLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductFeatureTargetLinkOut:
    with get_conn() as conn:
        try:
            out = product_feature.add_target_link(
                conn,
                system_id=system_id,
                feature_key=feature_key,
                link_kind=payload.link_kind,
                target_ref=payload.target_ref,
                note=payload.note,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductFeatureTargetLinkOut(**out)


@router.post("/{feature_key}/draft-links", response_model=ProductFeatureDraftLinkOut, status_code=201)
def add_draft_link_endpoint(
    feature_key: str,
    payload: ProductFeatureDraftLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductFeatureDraftLinkOut:
    with get_conn() as conn:
        try:
            out = product_feature.add_draft_link(
                conn,
                system_id=system_id,
                feature_key=feature_key,
                feature_draft_id=payload.feature_draft_id,
                note=payload.note,
                decision_method="manual",
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductFeatureDraftLinkOut(**out)


@router.post("/{feature_key}/decisions", response_model=ProductFeatureDecisionOut, status_code=201)
def record_feature_decision_endpoint(
    feature_key: str,
    payload: ProductFeatureDecisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductFeatureDecisionOut:
    with get_conn() as conn:
        try:
            row = product_feature.record_feature_decision(
                conn,
                system_id=system_id,
                feature_key=feature_key,
                decision=payload.decision,
                rationale=payload.rationale,
                captured_digest=payload.captured_digest,
                decided_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductFeatureDecisionOut(**row)
