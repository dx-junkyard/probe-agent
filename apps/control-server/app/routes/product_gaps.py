"""Product Gap API (Issue #429/#430, Epic #427).

`docs/product-objective-lineage.md` §10 is the endpoint contract this
module implements against `app/product_objective.py`'s deterministic domain
service. Follows `routes/ux_design.py` / `routes/product_objectives.py`
exactly: `Depends(get_system_id)` on every route, `Depends(require_user)` on
every write, actor derived from `Principal` and never from the request
body, cross-System reference -> 404, GET never writes.

`app/product_gap_sources.py` (Issue #430) does the actual per-`source_kind`
resolution -- this module never calls it directly; it goes through
`app.product_objective`'s already-guarded wrappers.

probe-agent:
  role: API boundary for Product Gap
  capability: product-objective-lineage
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify every write derives its actor from the authenticated Principal rather than the request body, that GET never mutates state, and that a Gap source-ref resolution failure degrades only that one entry rather than failing the whole request.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import product_objective
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    ProductGapArtifactLinkCreateRequest,
    ProductGapArtifactOut,
    ProductGapCreateRequest,
    ProductGapDecisionCreateRequest,
    ProductGapDecisionOut,
    ProductGapDetailOut,
    ProductGapEvidenceOut,
    ProductGapEvidenceRefCreateRequest,
    ProductGapListOut,
    ProductGapOut,
    ProductGapRevisionCreateRequest,
    ProductGapSourceOut,
    ProductGapSourceRefCreateRequest,
)
from ..product_objective import (
    ArtifactDuplicate,
    DecisionStaleDigest,
    KeyConflict,
    KeyRequired,
    NotDecidable,
    NotFound,
    ProductObjectiveValidationError,
    SourceDuplicate,
    SourceRefUnresolvable,
)

router = APIRouter(prefix="/product-gaps", tags=["product-gap"])


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for a write. Never a body-supplied
    value (see the module docstring)."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


# --- Finite reject-code mapping (§10.1, shared with routes/product_objectives.py) -----

_MESSAGES = {
    "product_gap_key_required": "gap_key を指定してください。",
    "product_gap_key_conflict": "同じ gap_key が既にこの System に存在します。",
    "product_gap_source_duplicate": "同じ検出元参照が既にこの Gap に存在します。",
    "product_gap_artifact_duplicate": "同じ下流 link が既にこの Gap に存在します。",
    "product_gap_decision_stale_digest": "指定された digest が現在の内容と一致しません。",
    "product_gap_not_decidable": "この状態からはその決定を記録できません。",
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
    §10.1 HTTP response. Re-raises anything unrecognized."""
    if isinstance(exc, KeyRequired):
        raise _reject(f"product_{exc.kind}_key_required", 422)
    if isinstance(exc, KeyConflict):
        raise _reject(f"product_{exc.kind}_key_conflict", 409)
    if isinstance(exc, SourceRefUnresolvable):
        raise _reject("product_gap_source_ref_unresolvable", 422)
    if isinstance(exc, SourceDuplicate):
        raise _reject("product_gap_source_duplicate", 409)
    if isinstance(exc, ArtifactDuplicate):
        raise _reject("product_gap_artifact_duplicate", 409)
    if isinstance(exc, DecisionStaleDigest):
        raise _reject(f"product_{exc.kind}_decision_stale_digest", 409)
    if isinstance(exc, NotDecidable):
        raise _reject(f"product_{exc.kind}_not_decidable", 422)
    if isinstance(exc, NotFound):
        raise HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, ProductObjectiveValidationError):
        raise HTTPException(status_code=422, detail=str(exc))
    raise


@router.get("", response_model=ProductGapListOut)
def list_gaps_endpoint(
    milestone_key: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> ProductGapListOut:
    with get_conn() as conn:
        try:
            result = product_objective.list_gaps(conn, system_id, milestone_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductGapListOut(system_id=system_id, generated_at=time.time(), **result)


@router.post("", response_model=ProductGapOut, status_code=201)
def create_gap_endpoint(
    payload: ProductGapCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductGapOut:
    with get_conn() as conn:
        try:
            product_objective.create_gap(
                conn,
                system_id=system_id,
                milestone_key=payload.milestone_key,
                gap_key=payload.gap_key,
                created_by=_principal_actor(principal),
            )
            detail = product_objective.get_gap_detail(conn, system_id, payload.gap_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductGapOut(**detail)


@router.get("/{gap_key}", response_model=ProductGapDetailOut)
def get_gap_endpoint(gap_key: str, system_id: int = Depends(get_system_id)) -> ProductGapDetailOut:
    with get_conn() as conn:
        try:
            detail = product_objective.get_gap_detail(conn, system_id, gap_key)
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductGapDetailOut(**detail)


@router.post("/{gap_key}/revisions", response_model=ProductGapOut, status_code=201)
def add_gap_revision_endpoint(
    gap_key: str,
    payload: ProductGapRevisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductGapOut:
    with get_conn() as conn:
        try:
            detail = product_objective.add_gap_revision(
                conn,
                system_id=system_id,
                gap_key=gap_key,
                title=payload.title,
                current_state=payload.current_state,
                target_state=payload.target_state,
                target_state_mode=payload.target_state_mode,
                interpretation=payload.interpretation,
                suggested_priority_note=payload.suggested_priority_note,
                change_note=payload.change_note,
                authored_by_kind="developer",
                decision_method="manual",
                intelligence_run_id=None,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductGapOut(**detail)


@router.post("/{gap_key}/source-refs", response_model=ProductGapSourceOut, status_code=201)
def add_gap_source_ref_endpoint(
    gap_key: str,
    payload: ProductGapSourceRefCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductGapSourceOut:
    with get_conn() as conn:
        try:
            out = product_objective.add_gap_source_ref(
                conn,
                system_id=system_id,
                gap_key=gap_key,
                source_kind=payload.source_kind,
                source_ref=payload.source_ref,
                note=payload.note,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductGapSourceOut(**out)


@router.post("/{gap_key}/evidence-refs", response_model=ProductGapEvidenceOut, status_code=201)
def add_gap_evidence_ref_endpoint(
    gap_key: str,
    payload: ProductGapEvidenceRefCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductGapEvidenceOut:
    with get_conn() as conn:
        try:
            out = product_objective.add_gap_evidence_ref(
                conn,
                system_id=system_id,
                gap_key=gap_key,
                evidence_kind=payload.evidence_kind,
                evidence_ref=payload.evidence_ref,
                note=payload.note,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductGapEvidenceOut(**out)


@router.post("/{gap_key}/artifact-links", response_model=ProductGapArtifactOut, status_code=201)
def add_gap_artifact_link_endpoint(
    gap_key: str,
    payload: ProductGapArtifactLinkCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductGapArtifactOut:
    with get_conn() as conn:
        try:
            out = product_objective.add_gap_artifact_link(
                conn,
                system_id=system_id,
                gap_key=gap_key,
                link_kind=payload.link_kind,
                target_ref=payload.target_ref,
                note=payload.note,
                created_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductGapArtifactOut(**out)


@router.post("/{gap_key}/decisions", response_model=ProductGapDecisionOut, status_code=201)
def record_gap_decision_endpoint(
    gap_key: str,
    payload: ProductGapDecisionCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProductGapDecisionOut:
    with get_conn() as conn:
        try:
            row = product_objective.record_gap_decision(
                conn,
                system_id=system_id,
                gap_key=gap_key,
                decision=payload.decision,
                priority_band=payload.priority_band,
                rationale=payload.rationale,
                captured_digest=payload.captured_digest,
                decided_by=_principal_actor(principal),
            )
        except Exception as exc:
            _raise_for_error(exc)
            raise
    return ProductGapDecisionOut(**row)
