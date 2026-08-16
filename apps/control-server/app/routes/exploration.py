"""Exploration Workbench API: modality-neutral comparison of implementations
against one Node contract (Issue #398, Phase 3 of Epic #394).

The domain layer is `app/exploration_workbench.py`. This boundary adds
nothing of its own -- in particular it does not rank, does not decide a
winner, and does not touch a Node's maturity. Completing a comparison is not
adopting one: adoption is #399's establishment gate plus a human approval
(ADR-9), and a comparison that quietly promoted its winner would be the
automatic adoption this Epic exists to prevent.

There is no endpoint that accepts source, a patch, or a command. A variant
references an implementation row and the existing Replay/Experiment run that
produced its numbers, so every execution stays inside the pinned-snapshot,
network-off sandbox those features already enforce (Principle 8).

probe-agent:
  role: API boundary for exploration runs, modality variants, per-dimension measurements and non-composited comparison
  capability: evolution-control-plane
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify no endpoint accepts executable content, ranking requires an explicit dimension, completing a run changes no Node maturity, and unmeasured or differently-covered dimensions are never reported as a numeric difference.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import exploration_workbench as workbench
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    ExplorationExecutionIn,
    ExplorationMeasurementIn,
    ExplorationMeasurementOut,
    ExplorationRankingOut,
    ExplorationRunCompleteIn,
    ExplorationRunCreateIn,
    ExplorationRunOut,
    ExplorationVariantCreateIn,
    ExplorationVariantOut,
)

router = APIRouter(prefix="/exploration", tags=["exploration"])

_ERROR_STATUS = {
    workbench.ExplorationNotFoundError: 404,
    workbench.ExplorationConflictError: 409,
    workbench.ExplorationValidationError: 422,
}


def _raise_domain_error(exc: workbench.ExplorationError) -> None:
    for exc_type, status_code in _ERROR_STATUS.items():
        if isinstance(exc, exc_type):
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/runs", response_model=ExplorationRunOut, status_code=201)
def create_exploration_run(
    payload: ExplorationRunCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ExplorationRunOut:
    with get_conn() as conn:
        try:
            run = workbench.create_run(
                conn,
                system_id=system_id,
                node_id=payload.node_id,
                node_version_id=payload.node_version_id,
                objective=payload.objective,
                dataset_kind=payload.dataset_kind,
                dataset_ref=payload.dataset_ref,
                snapshot_id=payload.snapshot_id,
                commit_sha=payload.commit_sha,
                environment_ref=payload.environment_ref,
                evaluation_policy_ids=payload.evaluation_policy_ids,
                handoff_id=payload.handoff_id,
                created_by=principal.username,
            )
            projection = workbench.build_run_projection(
                conn, system_id=system_id, run_id=run["id"]
            )
        except workbench.ExplorationError as exc:
            _raise_domain_error(exc)
    return ExplorationRunOut(**projection)


@router.get("/runs/{run_id}", response_model=ExplorationRunOut)
def get_exploration_run(
    run_id: int,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExplorationRunOut:
    with get_conn() as conn:
        try:
            projection = workbench.build_run_projection(
                conn, system_id=system_id, run_id=run_id
            )
        except workbench.ExplorationError as exc:
            _raise_domain_error(exc)
    return ExplorationRunOut(**projection)


@router.post("/runs/{run_id}/variants", response_model=ExplorationVariantOut, status_code=201)
def add_exploration_variant(
    run_id: int,
    payload: ExplorationVariantCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ExplorationVariantOut:
    """Register one implementation in the comparison.

    An implementation belonging to a different contract version is refused:
    comparing implementations of two different promises is the one thing a
    modality comparison must not do.
    """
    with get_conn() as conn:
        try:
            workbench.add_variant(
                conn,
                system_id=system_id,
                run_id=run_id,
                variant_key=payload.variant_key,
                modality=payload.modality,
                label=payload.label,
                is_baseline=payload.is_baseline,
                implementation_id=payload.implementation_id,
                config=payload.config,
                provenance=payload.provenance,
                generator=payload.generator,
                applicability_envelope=payload.applicability_envelope,
                created_by=principal.username,
            )
            projection = workbench.build_run_projection(
                conn, system_id=system_id, run_id=run_id
            )
        except workbench.ExplorationError as exc:
            _raise_domain_error(exc)
    for variant in projection["variants"]:
        if variant["variant_key"] == payload.variant_key:
            return ExplorationVariantOut(**variant)
    raise HTTPException(status_code=500, detail="Variant was created but not read back")


@router.post("/variants/{variant_id}/execution", response_model=ExplorationVariantOut)
def attach_variant_execution(
    variant_id: int,
    payload: ExplorationExecutionIn,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExplorationVariantOut:
    with get_conn() as conn:
        try:
            row = workbench.attach_execution(
                conn,
                system_id=system_id,
                variant_id=variant_id,
                execution_state=payload.execution_state,
                execution_ref_kind=payload.execution_ref_kind,
                execution_ref_id=payload.execution_ref_id,
                note=payload.note,
            )
            projection = workbench.build_run_projection(
                conn, system_id=system_id, run_id=row["run_id"]
            )
        except workbench.ExplorationError as exc:
            _raise_domain_error(exc)
    for variant in projection["variants"]:
        if variant["id"] == variant_id:
            return ExplorationVariantOut(**variant)
    raise HTTPException(status_code=500, detail="Variant could not be read back")


@router.post(
    "/variants/{variant_id}/measurements",
    response_model=ExplorationMeasurementOut,
    status_code=201,
)
def record_variant_measurement(
    variant_id: int,
    payload: ExplorationMeasurementIn,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExplorationMeasurementOut:
    """Record ONE dimension. There is no bulk endpoint that takes a metrics
    blob: a blob invites a caller to write a combined figure alongside the
    real ones."""
    with get_conn() as conn:
        try:
            row = workbench.record_measurement(
                conn,
                system_id=system_id,
                variant_id=variant_id,
                dimension=payload.dimension,
                metric_name=payload.metric_name,
                value_state=payload.value_state,
                numeric_value=payload.numeric_value,
                unit=payload.unit,
                covered_case_count=payload.covered_case_count,
                total_case_count=payload.total_case_count,
                source=payload.source,
                note=payload.note,
            )
        except workbench.ExplorationError as exc:
            _raise_domain_error(exc)
        measurement = workbench._measurement_doc(row)
    return ExplorationMeasurementOut(**measurement)


@router.get("/runs/{run_id}/ranking", response_model=ExplorationRankingOut)
def rank_exploration_run(
    run_id: int,
    dimension: str = Query(...),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExplorationRankingOut:
    """Order the run's variants by ONE named dimension.

    `dimension` is required, and there is deliberately no endpoint that
    ranks overall. A caller that wants an order has to say what it is
    ordering by, so a latency ranking can never be presented as "the best
    variant" (#398).
    """
    if dimension not in workbench.MEASUREMENT_DIMENSIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unknown_dimension",
                "message": "dimension must be one of: "
                + ", ".join(workbench.MEASUREMENT_DIMENSIONS),
            },
        )
    with get_conn() as conn:
        try:
            projection = workbench.build_run_projection(
                conn, system_id=system_id, run_id=run_id
            )
        except workbench.ExplorationError as exc:
            _raise_domain_error(exc)
    ranking = workbench.rank_by_dimension(projection["variants"], dimension)
    return ExplorationRankingOut(**ranking)


@router.post("/runs/{run_id}/complete", response_model=ExplorationRunOut)
def complete_exploration_run(
    run_id: int,
    payload: ExplorationRunCompleteIn,
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> ExplorationRunOut:
    """Close the comparison.

    This changes no Node's maturity, pins no implementation, and approves
    nothing. Completing a comparison is not adopting one.
    """
    with get_conn() as conn:
        try:
            workbench.complete_run(
                conn,
                system_id=system_id,
                run_id=run_id,
                conclusion_note=payload.conclusion_note,
            )
            projection = workbench.build_run_projection(
                conn, system_id=system_id, run_id=run_id
            )
        except workbench.ExplorationError as exc:
            _raise_domain_error(exc)
    return ExplorationRunOut(**projection)
