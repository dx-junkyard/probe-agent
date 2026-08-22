"""Flow explanation API (Epic #412, Issue #414).

The domain layer is `app/flow_explanation.py`; this module is a thin boundary
over it and owns no judgement of its own.

**GET only, and reading writes nothing.** Both endpoints recompute their whole
answer on every call. No view marker, no checkpoint, no "last seen" row: a page
view is never a human decision (#380/#382), and §6.1 states the projection is
read-only. `evaluate_session_workflow` -- which persists a workflow checkpoint
and can open a backward request -- is never on this path, the same exclusion
`app/overview_projection.py` makes for the same reason.

No reasoning-model call happens here either. §6.6's optional natural-language
summary is deliberately out of scope for this pass, so the whole path stays
deterministic and the `get_conn()`-across-an-external-call hazard cannot arise.

probe-agent:
  role: API boundary for the read-only Flow explanation projection
  capability: flow-explanation
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read]
  probe_value: Verify the endpoints never write, that a failed section degrades alone, and that another System's Flow subjects are never visible.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import flow_explanation
from ..auth import Principal, get_system_id, require_user
from ..models import (
    FlowBaselineSectionOut,
    FlowEvidenceOut,
    FlowExperimentSummaryOut,
    FlowExperimentsSectionOut,
    FlowExplanationNodeOut,
    FlowExplanationOut,
    FlowMembershipOut,
    FlowNodeBaselineOut,
    FlowOpenItemOut,
    FlowOpenItemsSectionOut,
    FlowPurposeSectionOut,
    FlowResponsibilitySectionOut,
    FlowSubjectListOut,
    FlowSubjectOut,
)

router = APIRouter(prefix="/flow-explanation", tags=["flow-explanation"])


# ---------------------------------------------------------------------------
# Domain value -> response helpers
# ---------------------------------------------------------------------------


def _evidence_out(item: flow_explanation.Evidence) -> FlowEvidenceOut:
    return FlowEvidenceOut(
        id=item.id,
        kind=item.kind,
        ref=item.ref,
        label=item.label,
        node_key=item.node_key,
        recorded_at=item.recorded_at,
    )


def _node_out(node: flow_explanation.NodeExplanation) -> FlowExplanationNodeOut:
    """Five axes, five fields. Nothing is combined on the way out."""
    return FlowExplanationNodeOut(
        node_id=node.node_id,
        node_key=node.node_key,
        display_name=node.display_name,
        membership_basis=node.membership_basis,
        execution_mode=node.execution_mode,
        mode_source=node.mode_source,
        mode_reason=node.mode_reason,
        mode_source_ref=node.mode_source_ref,
        mode_state=node.mode_state,
        mode_divergence=node.mode_divergence,
        observed_mode=node.observed_mode,
        maturity=node.maturity,
        maturity_state=node.maturity_state,
        folded_maturity=node.folded_maturity,
        maturity_consistent=node.maturity_consistent,
        implementation_modality=node.implementation_modality,
        implementation_modality_state=node.implementation_modality_state,
        improvement_status=node.improvement_status,
        improvement_status_state=node.improvement_status_state,
        sdk_policy_mode=node.sdk_policy_mode,
        sdk_policy_mode_state=node.sdk_policy_mode_state,
        observation=node.observation,
        observation_state=node.observation_state,
        monitoring_contract_declared=node.monitoring_contract_declared,
        evidence=[_evidence_out(item) for item in node.evidence],
        capability_refs=list(node.capability_refs),
        purpose_element_refs=list(node.purpose_element_refs),
        feature_refs=list(node.feature_refs),
    )


def _explanation_out(result: flow_explanation.FlowExplanation) -> FlowExplanationOut:
    return FlowExplanationOut(
        system_id=result.system_id,
        schema_version=result.schema_version,
        generated_at=result.generated_at,
        subject=FlowSubjectOut(
            subject_kind=result.subject.subject_kind,
            subject_ref=result.subject.subject_ref,
            label=result.subject.label,
            resolution=result.subject.resolution,
            snapshot_state=result.subject.snapshot_state,
            snapshot_id=result.subject.snapshot_id,
            commit_sha=result.subject.commit_sha,
            detail=result.subject.detail,
        ),
        membership=FlowMembershipOut(
            state=result.membership.state,
            node_keys=list(result.membership.node_keys),
            basis=dict(result.membership.basis),
            detail=result.membership.detail,
        ),
        # A degraded section stays `None` here on purpose: `[]` would claim
        # the section is empty, which is a different answer from "it could not
        # be read" (#356).
        purpose=(
            FlowPurposeSectionOut(
                purpose_elements=result.purpose.purpose_elements,
                capabilities=result.purpose.capabilities,
                features=result.purpose.features,
                purpose_chain_state=result.purpose.purpose_chain_state,
                detail=result.purpose.detail,
            )
            if result.purpose is not None
            else None
        ),
        responsibility=(
            FlowResponsibilitySectionOut(
                edge_source=result.responsibility.edge_source,
                node_order=result.responsibility.node_order,
                edges=result.responsibility.edges,
                contracts=result.responsibility.contracts,
                external_boundaries=result.responsibility.external_boundaries,
                entry_ref=result.responsibility.entry_ref,
                entry_state=result.responsibility.entry_state,
                truncated=result.responsibility.truncated,
                diagnostics=result.responsibility.diagnostics,
            )
            if result.responsibility is not None
            else None
        ),
        nodes=(
            [_node_out(node) for node in result.nodes]
            if result.nodes is not None
            else None
        ),
        open_items=(
            FlowOpenItemsSectionOut(
                items=[
                    FlowOpenItemOut(
                        id=item.id,
                        kind=item.kind,
                        label=item.label,
                        detail=item.detail,
                        node_key=item.node_key,
                        missing_state=item.missing_state,
                        evidence_ids=list(item.evidence_ids),
                    )
                    for item in result.open_items.items
                ]
            )
            if result.open_items is not None
            else None
        ),
        experiments=(
            FlowExperimentsSectionOut(
                proposals=[
                    FlowExperimentSummaryOut(
                        proposal_id=proposal.proposal_id,
                        proposal_key=proposal.proposal_key,
                        title=proposal.title,
                        comparison_scope=proposal.comparison_scope,
                        status=proposal.status,
                        status_state=proposal.status_state,
                        target_node_keys=list(proposal.target_node_keys),
                        evidence_refs=list(proposal.evidence_refs),
                        execution_refs=list(proposal.execution_refs),
                        isolation_strategy=proposal.isolation_strategy,
                        expires_at=proposal.expires_at,
                        created_at=proposal.created_at,
                    )
                    for proposal in result.experiments.proposals
                ],
                status_source=result.experiments.status_source,
            )
            if result.experiments is not None
            else None
        ),
        baseline=(
            FlowBaselineSectionOut(
                nodes=[
                    FlowNodeBaselineOut(
                        node_key=node.node_key,
                        stable_implementation=node.stable_implementation,
                        stable_state=node.stable_state,
                        rollback_implementation=node.rollback_implementation,
                        rollback_state=node.rollback_state,
                        approval=node.approval,
                        approval_state=node.approval_state,
                    )
                    for node in result.baseline.nodes
                ]
            )
            if result.baseline is not None
            else None
        ),
        drilldown=result.drilldown,
        rollup=result.rollup,
        degraded_sections=list(result.degraded_sections),
        degraded_detail=dict(result.degraded_detail),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get("/subjects", response_model=FlowSubjectListOut)
def list_subjects(
    snapshot_id: Optional[int] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> FlowSubjectListOut:
    """The Flow subjects this System can explain.

    The two kinds are returned in two separate lists. A runtime Flow is an
    observed `trace_spans.flow_id`; a static Flow is a pinned snapshot's
    `code_entrypoints.entrypoint_id`. Merging them into one list would be
    exactly the one-word-two-facts collapse §2.1 forbids.
    """
    listing = flow_explanation.list_flow_subjects(
        system_id, snapshot_id=snapshot_id, limit=limit
    )
    return FlowSubjectListOut(
        system_id=listing.system_id,
        generated_at=listing.generated_at,
        runtime_flows=listing.runtime_flows,
        static_flows=listing.static_flows,
        snapshot_id=listing.snapshot_id,
        snapshot_state=listing.snapshot_state,
        degraded_sections=listing.degraded_sections,
        degraded_detail=listing.degraded_detail,
    )


@router.get("", response_model=FlowExplanationOut)
def explain_flow(
    subject_kind: str = Query(...),
    subject_ref: str = Query(...),
    snapshot_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
    _principal: Principal = Depends(require_user),
) -> FlowExplanationOut:
    """Explain one Flow: purpose / responsibility / Nodes / open items /
    experiments / baseline, plus the §6.5 lineage in both directions.

    A subject that does not resolve is NOT a 404: the response still carries
    the subject's own `resolution` and the sections it could still read. "This
    Flow has no observed spans" is an answer about the Flow, not a missing
    endpoint.
    """
    if subject_kind not in flow_explanation.FLOW_SUBJECT_KINDS:
        raise HTTPException(
            status_code=422,
            detail=(
                "subject_kind must be one of "
                f"{list(flow_explanation.FLOW_SUBJECT_KINDS)}"
            ),
        )
    if not (subject_ref or "").strip():
        raise HTTPException(status_code=422, detail="subject_ref must not be empty")

    result = flow_explanation.build_flow_explanation(
        system_id,
        subject_kind=subject_kind,
        subject_ref=subject_ref.strip(),
        snapshot_id=snapshot_id,
    )
    return _explanation_out(result)
