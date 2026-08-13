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
    OverviewOut,
    OverviewRuntimeHealthOut,
    OverviewTargetOut,
    UnderstandingBriefOut,
)

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
        findings=findings,
        findings_initial_count=result.findings_initial_count,
        findings_state=result.findings_state,
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
    )
