"""Flow experiment orchestration API (Epic #412, Issue #415).

The domain layer is `app/flow_orchestration.py`; this module is a thin
boundary over it and owns no judgement of its own. Five boundary properties
matter:

- **Provenance comes from the route and the principal, never the body**
  (#337). `actor` on a proposal, an approval, a rejection, a withdrawal, an
  execution record, a result, a promotion candidate and a rollback is the
  authenticated user, and the EVENT's `decision_method` is fixed to `manual`
  server-side (§10). A body-supplied actor would let any caller forge someone
  else's approval.

- **A refusal names WHICH element or WHICH rule** (§7.1 / §7.4). A
  completeness or structural refusal is a 422 carrying one of the nineteen
  finite codes plus the specific Nodes / levels / keys involved; an illegal
  transition is a 409 carrying one of the five lifecycle codes. "Invalid
  proposal" would carry twelve facts at once (#366).

- **The two 409s on `POST /{id}/executions` are deliberately different
  bodies** (§7.5). Approval and execution mode are two independent facts, so
  "nobody approved this" (`FlowExperimentLifecycleRejectionOut`) and "the
  mode does not permit candidate execution"
  (`ExecutionModeDenialOut`, produced by #413's own
  `raise_execution_mode_denied` so the shape is identical everywhere) are
  never merged. The developer's next action differs.

- **`GET` never decides anything and never writes.** The status is folded
  from the ledger on every read (§7.4) and reference resolution is recomputed
  on every read (§7.6): looking at a proposal must not move it (#380).

- **`POST /flow-experiments/draft` creates no proposal.** It returns a
  reasoning-model DRAFT and an `intelligence_runs` audit row, and nothing
  else; a human puts the content into the queue by posting it to
  `POST /flow-experiments` (§7.7, Principle 7). The route holds NO database
  connection across that call -- `propose_flow_experiment` owns its own
  connections and its shape is read -> close -> reason -> reopen -> persist,
  because `get_conn()`'s lock is process-wide and non-reentrant.

Nothing on any of these paths adopts, promotes, merges, deploys, applies a
patch, changes `components.mode` or `evolution_node.maturity`, or writes to a
target repository (§7.6).

probe-agent:
  role: API boundary for Flow experiment proposals, the human approval gate, execution references and promotion candidates
  capability: execution-mode-control
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify each completeness and structural refusal returns its own finite 422 code, that an unapproved proposal and an approved proposal whose mode fell back to `propose` both get a 409 with distinguishable bodies, that the proposal actor is the authenticated principal rather than the body, that a draft creates no proposal, and that no row of another System is ever visible.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import execution_mode, flow_orchestration
from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    FlowExperimentCreateIn,
    FlowExperimentDecisionIn,
    FlowExperimentDraftIn,
    FlowExperimentDraftOut,
    FlowExperimentExecutionIn,
    FlowExperimentLifecycleRejectionOut,
    FlowExperimentListOut,
    FlowExperimentProposalOut,
    FlowExperimentPromotionCandidateIn,
    FlowExperimentRejectionOut,
    FlowExperimentResultIn,
    FlowExperimentRollbackIn,
    FlowExperimentStatus,
)
# `raise_execution_mode_denied` is #413's own refusal body, imported rather
# than re-implemented so a mode denial has ONE shape everywhere (§4.1);
# `_decision_out` is its field mapping, for the same reason.
from .execution_modes import _decision_out, raise_execution_mode_denied

router = APIRouter(prefix="/flow-experiments", tags=["flow-experiments"])


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _raise_domain_error(exc: flow_orchestration.FlowOrchestrationError) -> None:
    """Map a domain failure onto its HTTP answer.

    A row in another System is indistinguishable from one that does not exist
    (the domain layer raises NotFound for both), so cross-System ids stay
    unprobeable.
    """
    if isinstance(exc, flow_orchestration.FlowExperimentNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, flow_orchestration.FlowExperimentRejected):
        body = FlowExperimentRejectionOut(
            code=exc.code, message=str(exc), detail=list(exc.detail)
        )
        raise HTTPException(status_code=422, detail=body.model_dump()) from exc
    if isinstance(exc, flow_orchestration.FlowExperimentLifecycleError):
        body = FlowExperimentLifecycleRejectionOut(code=exc.code, message=str(exc))
        raise HTTPException(status_code=409, detail=body.model_dump()) from exc
    if isinstance(exc, flow_orchestration.FlowExperimentReasoningError):
        # Principle 6: the run failed and there is no heuristic fallback. The
        # only thing it left behind is the failed `intelligence_runs` row.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _proposal_out(doc: Dict[str, Any]) -> FlowExperimentProposalOut:
    return FlowExperimentProposalOut(**doc)


# ---------------------------------------------------------------------------
# Reasoning-model drafting (§7.7)
#
# Registered BEFORE every `/{proposal_id}` route: a literal path segment that
# comes after an int path parameter is captured by the parameter and 422s
# (the bug CLAUDE.md documents for `/interview/joint-understanding/lineage`).
# ---------------------------------------------------------------------------


@router.post("/draft", response_model=FlowExperimentDraftOut)
def draft_proposal(
    payload: FlowExperimentDraftIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentDraftOut:
    """Draft a Flow experiment with the experiment reasoning model.

    Reachable only in `propose` / `shadow`, and the gate runs before the
    first line that could read a credential (EM-ADR-3) -- in `fixed` /
    `observe` this is a 409 with no credential ever read.

    **It creates no proposal, no `proposed` event and no queue entry.** The
    draft is `decision_method: reasoning_llm` output that a human reads,
    edits and posts to `POST /flow-experiments` themselves; an LLM
    recommendation never enters an approval queue by itself (Principle 7).

    No database connection is held across the round trip: the domain function
    owns its own connections precisely so this route cannot deadlock the
    server.
    """
    try:
        result = flow_orchestration.propose_flow_experiment(
            system_id=system_id,
            flow_subject_kind=payload.flow_subject_kind,
            flow_subject_ref=payload.flow_subject_ref,
            node_keys=list(payload.node_keys),
            goal=payload.goal,
        )
    except execution_mode.ExecutionModeDenied as exc:
        raise_execution_mode_denied(exc)
    except execution_mode.ExecutionModeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except flow_orchestration.FlowOrchestrationError as exc:
        _raise_domain_error(exc)

    return FlowExperimentDraftOut(
        draft=dict(result.draft),
        intelligence_run_id=result.intelligence_run_id,
        is_mock=result.is_mock,
        provider=result.provider,
        model=result.model,
        prompt_version=result.prompt_version,
        schema_version=result.schema_version,
        decision=_decision_out(result.decision),
        # Stated, never implied: a produced plan and a plan awaiting approval
        # are the two facts §7.7 keeps apart.
        created_proposal=False,
    )


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


@router.post("", response_model=FlowExperimentProposalOut, status_code=201)
def create_flow_experiment(
    payload: FlowExperimentCreateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """Create a proposal, or refuse it with a finite §7.1 code.

    `decision_method` on the ROW describes the provenance of the CONTENT (a
    draft carries `reasoning_llm` plus its `intelligence_run_id`); the
    `proposed` EVENT is always `manual` with the authenticated principal as
    its actor. A model may have written the words, but a human put the
    proposal into the queue (§7.7 / #337's three provenance axes).
    """
    content = flow_orchestration.ProposalContent(
        proposal_key=payload.proposal_key,
        flow_subject_kind=payload.flow_subject_kind,
        flow_subject_ref=payload.flow_subject_ref,
        comparison_scope=payload.comparison_scope,
        title=payload.title,
        purpose=payload.purpose,
        hypothesis=payload.hypothesis,
        baseline_ref=payload.baseline_ref,
        candidate_refs=tuple(payload.candidate_refs),
        evaluation_axes=tuple(axis.model_dump() for axis in payload.evaluation_axes),
        quality_floor=dict(payload.quality_floor),
        isolation_strategy=payload.isolation_strategy,
        isolation_detail=payload.isolation_detail,
        cost_cap=dict(payload.cost_cap),
        stop_conditions=tuple(payload.stop_conditions),
        rollback_plan=payload.rollback_plan,
        evidence_refs=tuple(payload.evidence_refs),
        captured_snapshot_id=payload.captured_snapshot_id,
        expires_at=payload.expires_at,
    )
    targets = [
        {
            "node_key": target.node_key,
            "target_role": target.target_role,
            "position": position if target.position is None else target.position,
            "note": target.note,
        }
        for position, target in enumerate(payload.targets)
    ]
    with get_conn() as conn:
        try:
            doc = flow_orchestration.create_proposal(
                conn,
                system_id=system_id,
                content=content,
                targets=targets,
                actor=principal.username,
                reason=payload.reason,
                decision_method=(
                    "reasoning_llm" if payload.intelligence_run_id is not None else "manual"
                ),
                intelligence_run_id=payload.intelligence_run_id,
            )
        except flow_orchestration.FlowOrchestrationError as exc:
            _raise_domain_error(exc)
    return _proposal_out(doc)


@router.get("", response_model=FlowExperimentListOut)
def list_flow_experiments(
    status: Optional[FlowExperimentStatus] = Query(
        default=None,
        description="Filter on the DERIVED status. There is no status column to filter in SQL.",
    ),
    flow_subject_ref: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentListOut:
    """This System's proposals, newest first. Writes nothing."""
    now = time.time()
    with get_conn() as conn:
        try:
            docs = flow_orchestration.list_proposals(
                conn,
                system_id=system_id,
                status=status,
                flow_subject_ref=flow_subject_ref,
                limit=limit,
                now=now,
            )
        except flow_orchestration.FlowOrchestrationError as exc:
            _raise_domain_error(exc)
    return FlowExperimentListOut(
        system_id=system_id,
        generated_at=now,
        proposals=[_proposal_out(doc) for doc in docs],
    )


@router.get("/{proposal_id}", response_model=FlowExperimentProposalOut)
def get_flow_experiment(
    proposal_id: int,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """One proposal with its derived status, targets, ledger and references."""
    with get_conn() as conn:
        try:
            doc = flow_orchestration.get_proposal(
                conn, system_id=system_id, proposal_id=proposal_id
            )
        except flow_orchestration.FlowOrchestrationError as exc:
            _raise_domain_error(exc)
    return _proposal_out(doc)


# ---------------------------------------------------------------------------
# The human decision gate (§7.5, §10)
# ---------------------------------------------------------------------------


def _record_decision(
    *,
    system_id: int,
    proposal_id: int,
    action: str,
    principal: Principal,
    reason: str,
) -> FlowExperimentProposalOut:
    with get_conn() as conn:
        try:
            doc = flow_orchestration.record_decision(
                conn,
                system_id=system_id,
                proposal_id=proposal_id,
                action=action,
                actor=principal.username,
                reason=reason,
            )
        except flow_orchestration.FlowOrchestrationError as exc:
            _raise_domain_error(exc)
    return _proposal_out(doc)


@router.post("/{proposal_id}/approve", response_model=FlowExperimentProposalOut)
def approve_flow_experiment(
    proposal_id: int,
    payload: FlowExperimentDecisionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """Record a named human's approval.

    Approval alone permits nothing: an execution additionally needs every
    target Node's effective mode to permit `candidate_execution`, and those
    two facts are never derived from one another (§7.5).
    """
    return _record_decision(
        system_id=system_id,
        proposal_id=proposal_id,
        action="approve",
        principal=principal,
        reason=payload.reason,
    )


@router.post("/{proposal_id}/reject", response_model=FlowExperimentProposalOut)
def reject_flow_experiment(
    proposal_id: int,
    payload: FlowExperimentDecisionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """Reject a proposal. `reason` is required -- a rejection ends a plan and
    an unexplained one cannot be reviewed later."""
    return _record_decision(
        system_id=system_id,
        proposal_id=proposal_id,
        action="reject",
        principal=principal,
        reason=payload.reason,
    )


@router.post("/{proposal_id}/withdraw", response_model=FlowExperimentProposalOut)
def withdraw_flow_experiment(
    proposal_id: int,
    payload: FlowExperimentDecisionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """Retract a proposal that has not been executed. `reason` is required."""
    return _record_decision(
        system_id=system_id,
        proposal_id=proposal_id,
        action="withdraw",
        principal=principal,
        reason=payload.reason,
    )


# ---------------------------------------------------------------------------
# Execution references and their results (§7.6)
# ---------------------------------------------------------------------------


@router.post("/{proposal_id}/executions", response_model=FlowExperimentProposalOut, status_code=201)
def record_flow_experiment_execution(
    proposal_id: int,
    payload: FlowExperimentExecutionIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """Attach an execution that already exists in its own canonical table.

    Two independent facts are required and neither implies the other (§7.5):
    the proposal must be approved (409 `not_approved` otherwise) AND every
    target Node's effective mode must permit `candidate_execution` (409 with
    #413's denial body otherwise). This endpoint runs nothing and creates no
    row in `replay_variants` / `experiments` / `shadow_results`; it points at
    one and refuses a pointer that does not resolve in this System.
    """
    with get_conn() as conn:
        try:
            doc = flow_orchestration.record_execution(
                conn,
                system_id=system_id,
                proposal_id=proposal_id,
                execution_kind=payload.execution_kind,
                execution_ref=payload.execution_ref,
                actor=principal.username,
                note=payload.note,
            )
        except execution_mode.ExecutionModeDenied as exc:
            raise_execution_mode_denied(exc)
        except execution_mode.ExecutionModeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except flow_orchestration.FlowOrchestrationError as exc:
            _raise_domain_error(exc)
    return _proposal_out(doc)


@router.post("/{proposal_id}/results", response_model=FlowExperimentProposalOut, status_code=201)
def record_flow_experiment_result(
    proposal_id: int,
    payload: FlowExperimentResultIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """Record what an execution produced. Adopts nothing and applies nothing.

    `metrics` is keyed by the three evaluation contracts and no level is ever
    computed from another (ADR-7): what was not measured stays absent.
    """
    with get_conn() as conn:
        try:
            doc = flow_orchestration.record_result(
                conn,
                system_id=system_id,
                proposal_id=proposal_id,
                summary=payload.summary,
                actor=principal.username,
                execution_kind=payload.execution_kind,
                execution_ref=payload.execution_ref,
                metrics=dict(payload.metrics),
            )
        except flow_orchestration.FlowOrchestrationError as exc:
            _raise_domain_error(exc)
    return _proposal_out(doc)


@router.post(
    "/{proposal_id}/promotion-candidates",
    response_model=FlowExperimentProposalOut,
    status_code=201,
)
def record_flow_experiment_promotion_candidate(
    proposal_id: int,
    payload: FlowExperimentPromotionCandidateIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """Record a promotion CANDIDATE -- which is not a promotion (§7.6).

    Nothing here adopts an Experiment variant, moves a Node's maturity,
    creates a publish job or writes a line of source. The actual promotion
    still goes through the existing Experiment adoption / Stabilization /
    publish human gates, each of which keeps its own record.
    """
    with get_conn() as conn:
        try:
            doc = flow_orchestration.record_promotion_candidate(
                conn,
                system_id=system_id,
                proposal_id=proposal_id,
                candidate_ref=payload.candidate_ref,
                rationale=payload.rationale,
                actor=principal.username,
            )
        except flow_orchestration.FlowOrchestrationError as exc:
            _raise_domain_error(exc)
    return _proposal_out(doc)


@router.post("/{proposal_id}/rollbacks", response_model=FlowExperimentProposalOut, status_code=201)
def record_flow_experiment_rollback(
    proposal_id: int,
    payload: FlowExperimentRollbackIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> FlowExperimentProposalOut:
    """Record that the proposal's rollback plan was carried out elsewhere.

    Like every other row here this is an audit fact about work performed by
    the canonical path that owns the change: this layer can revert nothing.
    """
    with get_conn() as conn:
        try:
            doc = flow_orchestration.record_rollback(
                conn,
                system_id=system_id,
                proposal_id=proposal_id,
                detail=payload.detail,
                actor=principal.username,
            )
        except flow_orchestration.FlowOrchestrationError as exc:
            _raise_domain_error(exc)
    return _proposal_out(doc)
