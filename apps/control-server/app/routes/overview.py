"""Overview / System Intelligence Brief API (Issues #380-#384).

One read-only endpoint. `GET /overview` is the only place the Dashboard's
Overview learns what the AI thinks the system is for, what changed since the
developer last confirmed that understanding, which single operation comes
next, where the improvement loop currently stands, and how the runtime is
doing.

Why one composed endpoint rather than the Dashboard stitching five: the same
reason #349 moved the workflow state and #351 moved Decision Readiness to the
server. A CTA chosen from client state disappears on reload, and two surfaces
computing "what should I do next" from the same rows will eventually disagree.
The Overview renders this response; it re-derives nothing (#380 principle 6).

What this endpoint deliberately does NOT do:

* It does not decide any other surface's canonical state. Readiness comes from
  `understanding_brief`, the interview position from `interview_workflow`, the
  phase from `system_state`. This endpoint composes them and adds no sixth
  opinion.
* It does not gate. The single primary action is guidance; every human gate
  (理解の確認 / 提案の承認 / 差分の適用 / 観測の開始 / 採否の記録 / publish)
  stays exactly where it already lives, with its own approval record.
* It writes nothing. Opening the Overview is not a decision, so no view, no
  acknowledgement and no "last seen" marker is persisted (#382).

probe-agent:
  role: API boundary for the Overview decision cockpit
  capability: system-state-assessment
  element_type: boundary
  consumers: [dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify the Overview returns exactly zero or one primary action, that findings are capped and deterministically ordered, that a failed section degrades alone, and that the response is System-scoped.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from fastapi import APIRouter, Depends

from .. import overview_projection
from ..auth import get_system_id
from ..models import (
    OverviewActionOut,
    OverviewFindingOut,
    OverviewLoopStageOut,
    OverviewObjectiveOut,
    OverviewOut,
    OverviewRuntimeHealthOut,
    OverviewTargetOut,
    ProductGapOut,
    ProductMilestoneOut,
    ProductObjectiveOut,
    PurposeChainOut,
    UnderstandingBriefClaimOut,
    UnderstandingBriefOut,
)
# `_question_out` is `routes/purpose_chain.py`'s own conversion from a
# `purpose_needs.PurposeQuestion` to its wire model -- reused here rather
# than redefined, the same precedent `routes/purpose_chain.py` itself sets
# by importing `_insert_item` from `routes/interview_intent.py`. One
# conversion, so the Overview's embedded question can never render
# differently from `GET /purpose-chain/next-question`'s own response.
from .purpose_chain import _question_out as _purpose_question_out

router = APIRouter()


def _target_out(target) -> Optional[OverviewTargetOut]:
    if target is None:
        return None
    return OverviewTargetOut(
        route=target.route,
        label=target.label,
        params=dict(target.params),
        anchor=target.anchor,
    )


def _finding_out(finding, status: str) -> OverviewFindingOut:
    return OverviewFindingOut(
        id=finding.id,
        kind=finding.kind,
        kind_label=overview_projection.FINDING_KIND_LABELS[finding.kind],
        severity=finding.severity,
        severity_label=overview_projection.FINDING_SEVERITY_LABELS[finding.severity],
        status=status,
        status_label=overview_projection.FINDING_STATUS_LABELS[status],
        summary=finding.summary,
        decision_impact=finding.decision_impact,
        provenance=finding.provenance,
        provenance_label=overview_projection.FINDING_PROVENANCE_LABELS[
            finding.provenance
        ],
        snapshot_id=finding.snapshot_id,
        revision_id=finding.revision_id,
        runtime_window_seconds=finding.runtime_window_seconds,
        first_seen=finding.first_seen,
        last_updated=finding.last_updated,
        target=_target_out(finding.target),
        evidence=[dict(item) for item in finding.evidence],
        occurrence_count=finding.occurrence_count,
    )


def _objective_section_out(section) -> Optional[OverviewObjectiveOut]:
    """Convert `overview_projection.OverviewResult.objective`
    (`product_objective_projection.ObjectiveOverviewResult`) into the wire
    model. `active_objective` / `next_milestone` / `primary_gap` are already
    exactly `ProductObjectiveOut` / `ProductMilestoneOut` / `ProductGapOut`
    -shaped dicts -- `product_objective_projection` builds them straight off
    `product_objective.get_*_summary`, so no re-derivation happens here."""
    if section is None:
        return None
    return OverviewObjectiveOut(
        vision=(
            UnderstandingBriefClaimOut(**asdict(section.vision))
            if section.vision is not None
            else None
        ),
        active_objective=(
            ProductObjectiveOut(**section.active_objective)
            if section.active_objective is not None
            else None
        ),
        active_objective_count=section.active_objective_count,
        next_milestone=(
            ProductMilestoneOut(**section.next_milestone)
            if section.next_milestone is not None
            else None
        ),
        primary_gap=(
            ProductGapOut(**section.primary_gap) if section.primary_gap is not None else None
        ),
        objective_state=section.objective_state,
        next_step=section.next_step,
        next_step_state=section.next_step_state,
        next_step_reason=section.next_step_reason,
        next_step_completion=section.next_step_completion,
        next_step_value=section.next_step_value,
        next_step_requirement_key=section.next_step_requirement_key,
        degraded_sections=list(section.degraded_sections),
        degraded_detail=dict(section.degraded_detail),
    )


def _action_out(action) -> Optional[OverviewActionOut]:
    if action is None:
        return None
    return OverviewActionOut(
        key=action.key,
        label=action.label,
        reason=action.reason,
        completion_condition=action.completion_condition,
        value=action.value,
        target=_target_out(action.target),
        rule_row=action.rule_row,
        source_state_ids=list(action.source_state_ids),
        source_finding_ids=list(action.source_finding_ids),
        blockers=list(action.blockers),
    )


@router.get("/overview", response_model=OverviewOut)
def get_overview(system_id: int = Depends(get_system_id)) -> OverviewOut:
    """The whole Overview projection for the caller's System.

    Every section is System-scoped by construction: the projection reads only
    rows carrying this `system_id`, and the Interview session it reads the
    Brief from is resolved within the same System.
    """
    result = overview_projection.build_overview(system_id)

    findings: List[OverviewFindingOut] = [
        _finding_out(finding, status)
        for finding, status in zip(result.findings, result.finding_statuses)
    ]
    loop_stages: List[OverviewLoopStageOut] = [
        OverviewLoopStageOut(**asdict(stage)) for stage in result.loop_stages
    ]

    return OverviewOut(
        system_id=system_id,
        generated_at=result.generated_at,
        interview_session_id=result.interview_session_id,
        brief=(
            UnderstandingBriefOut(system_id=system_id, **asdict(result.brief))
            if result.brief is not None
            else None
        ),
        snapshot_id=result.snapshot_id,
        snapshot_commit_sha=result.snapshot_commit_sha,
        latest_ready_snapshot_id=result.latest_ready_snapshot_id,
        snapshot_freshness=result.snapshot_freshness,
        understanding_revision_id=result.understanding_revision_id,
        understanding_confirmed_at=result.understanding_confirmed_at,
        findings=findings,
        findings_initial_count=result.findings_initial_count,
        findings_state=result.findings_state,
        findings_baseline_state=result.findings_baseline_state,
        findings_baseline_label=result.findings_baseline_label,
        findings_baseline_at=result.findings_baseline_at,
        next_action=_action_out(result.next_action),
        next_action_state=result.next_action_state,
        next_action_message=result.next_action_message,
        loop_stages=loop_stages,
        user_phase=result.user_phase,
        runtime=(
            OverviewRuntimeHealthOut(**asdict(result.runtime))
            if result.runtime is not None
            else None
        ),
        degraded_sections=result.degraded_sections,
        degraded_detail=result.degraded_detail,
        # The Purpose Chain (#388) is composed, not re-derived: the same
        # dataclass-field-name-matches-model-field-name trick the Brief
        # embedding above uses, since `PurposeChainResult`'s own fields
        # (including `system_id`) already match `PurposeChainOut`'s.
        purpose_chain=(
            PurposeChainOut(**asdict(result.purpose_chain))
            if result.purpose_chain is not None
            else None
        ),
        # §4.5/#390: the Purpose Frame's single adaptive question, composed
        # from that SAME `purpose_chain` value -- never a second,
        # independently-derived one.
        purpose_question=(
            _purpose_question_out(result.purpose_question)
            if result.purpose_question is not None
            else None
        ),
        objective=_objective_section_out(result.objective),
    )
