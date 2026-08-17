"""Stabilization Evidence Package API and the establishment decision
(Issue #399, Phase 4 of Epic #394).

The domain layer is `app/stabilization.py`. Two boundary properties matter:

- **`GET` never decides anything.** The gate verdict is recomputed on every
  read, but reading a package writes nothing and moves no Node -- looking at
  a package must not change it (#380).
- **`approved_by` comes from the authenticated principal, never the body.**
  Establishment is a named person's decision; accepting the name from a
  request would let any caller record someone else's approval (#337).

Approving establishes ONLY. It applies no source, changes no policy, deploys
nothing, and publishes nothing -- each of those keeps its own separate human
gate (Principle 5/8).

probe-agent:
  role: API boundary for Stabilization Evidence Packages and the establishment decision
  capability: evolution-control-plane
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify reading a package changes nothing, the approver is taken from the authenticated principal rather than the body, a refused gate returns its finite code, and approval establishes without applying, deploying or publishing anything.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import stabilization
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    StabilizationDecisionIn,
    StabilizationEvidenceIn,
    StabilizationEvidenceOut,
    StabilizationPackageCreateIn,
    StabilizationPackageOut,
)

router = APIRouter(prefix="/stabilization", tags=["stabilization"])

_ERROR_STATUS = {
    stabilization.StabilizationNotFoundError: 404,
    stabilization.StabilizationConflictError: 409,
    stabilization.StabilizationValidationError: 422,
}


def _raise_domain_error(exc: stabilization.StabilizationError) -> None:
    for exc_type, status_code in _ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/packages", response_model=StabilizationPackageOut, status_code=201)
def create_stabilization_package(
    payload: StabilizationPackageCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StabilizationPackageOut:
    with get_conn() as conn:
        try:
            package = stabilization.create_package(
                conn,
                system_id=system_id,
                node_id=payload.node_id,
                candidate_implementation_id=payload.candidate_implementation_id,
                exploration_run_id=payload.exploration_run_id,
                applicability_envelope=payload.applicability_envelope,
                known_limitations=payload.known_limitations,
                residual_risks=payload.residual_risks,
                required_case_count=payload.required_case_count,
                stability_window_seconds=payload.stability_window_seconds,
                observed_case_count=payload.observed_case_count,
                observed_window_seconds=payload.observed_window_seconds,
                outcome_unmeasured_reason=payload.outcome_unmeasured_reason,
                rollback_plan=payload.rollback_plan,
                created_by=principal.username,
            )
            projection = stabilization.build_package_projection(
                conn, system_id=system_id, package_id=package["id"]
            )
        except stabilization.StabilizationError as exc:
            _raise_domain_error(exc)
    return StabilizationPackageOut(**projection)


@router.get("/packages/{package_id}", response_model=StabilizationPackageOut)
def get_stabilization_package(
    package_id: int,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> StabilizationPackageOut:
    """The package plus its CURRENT gate verdict. Writes nothing."""
    with get_conn() as conn:
        try:
            projection = stabilization.build_package_projection(
                conn, system_id=system_id, package_id=package_id
            )
        except stabilization.StabilizationError as exc:
            _raise_domain_error(exc)
    return StabilizationPackageOut(**projection)


@router.post(
    "/packages/{package_id}/evidence",
    response_model=StabilizationEvidenceOut,
    status_code=201,
)
def add_stabilization_evidence(
    package_id: int,
    payload: StabilizationEvidenceIn,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> StabilizationEvidenceOut:
    with get_conn() as conn:
        try:
            row = stabilization.add_evidence(
                conn,
                system_id=system_id,
                package_id=package_id,
                evidence_level=payload.evidence_level,
                evidence_kind=payload.evidence_kind,
                name=payload.name,
                verdict=payload.verdict,
                ref_kind=payload.ref_kind,
                ref_id=payload.ref_id,
                evaluation_policy_id=payload.evaluation_policy_id,
                detail=payload.detail,
                is_mock=payload.is_mock,
                source=payload.source,
            )
        except stabilization.StabilizationError as exc:
            _raise_domain_error(exc)
        evidence = StabilizationEvidenceOut(
            id=row["id"],
            evidence_kind=row["evidence_kind"],
            name=row["name"],
            verdict=row["verdict"],
            ref_kind=row["ref_kind"],
            ref_id=row["ref_id"],
            evaluation_policy_id=row["evaluation_policy_id"],
            detail=row["detail"],
            is_mock=bool(row["is_mock"]),
            source=row["source"],
        )
    return evidence


@router.post("/packages/{package_id}/approve", response_model=StabilizationPackageOut)
def approve_stabilization_package(
    package_id: int,
    payload: StabilizationDecisionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StabilizationPackageOut:
    """Approve establishment.

    The gate is re-evaluated inside the domain layer -- a caller's earlier
    PASS is never trusted, because evidence, the Node's maturity, and the
    candidate can all change between reading and approving. A refused gate is
    a 409 carrying its finite reason code.

    This establishes only. Applying source, changing a policy, deploying and
    publishing all keep their own separate gates.
    """
    if not principal.username:
        raise HTTPException(
            status_code=403,
            detail="Establishment requires an identified user account",
        )
    with get_conn() as conn:
        try:
            stabilization.approve_package(
                conn,
                system_id=system_id,
                package_id=package_id,
                approved_by=principal.username,
                note=payload.note,
            )
            projection = stabilization.build_package_projection(
                conn, system_id=system_id, package_id=package_id
            )
        except stabilization.StabilizationError as exc:
            _raise_domain_error(exc)
    return StabilizationPackageOut(**projection)


@router.post("/packages/{package_id}/reject", response_model=StabilizationPackageOut)
def reject_stabilization_package(
    package_id: int,
    payload: StabilizationDecisionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> StabilizationPackageOut:
    """Reject the package. Moves the Node nowhere -- a rejection is a record
    that a human looked and said no, not a demotion."""
    with get_conn() as conn:
        try:
            stabilization.reject_package(
                conn,
                system_id=system_id,
                package_id=package_id,
                rejected_by=principal.username or "",
                note=payload.note,
            )
            projection = stabilization.build_package_projection(
                conn, system_id=system_id, package_id=package_id
            )
        except stabilization.StabilizationError as exc:
            _raise_domain_error(exc)
    return StabilizationPackageOut(**projection)
