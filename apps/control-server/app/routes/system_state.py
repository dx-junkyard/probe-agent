"""System State Assessment API (Issue #193).

probe-agent:
  role: API boundary for the deterministic System State Assessment layer
  capability: system-state-assessment
  element_type: boundary
  consumers: [dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify the normalized state model (severity, status, user_action_kind, intervention_timing, target_ui) is returned without calling an LLM.
"""

from fastapi import APIRouter, Depends

from ..auth import get_system_id
from ..models import (
    SystemStateAssessmentOut,
    SystemStateItemOut,
    SystemStatePhaseCompletionOut,
    SystemStateTargetUiOut,
)
from ..system_state import build_system_state

router = APIRouter()


def _item_out(item) -> SystemStateItemOut:
    return SystemStateItemOut(
        state_id=item.state_id, state_group=item.state_group, severity=item.severity,
        status=item.status, user_action_kind=item.user_action_kind,
        intervention_timing=item.intervention_timing, subject=item.subject,
        summary=item.summary, detail=item.detail, impact=item.impact,
        remediation=item.remediation, evidence=item.evidence,
        target_ui=(SystemStateTargetUiOut(route=item.target_ui.route, anchor=item.target_ui.anchor,
                                          action_label=item.target_ui.action_label) if item.target_ui else None),
        display_routes=item.display_routes,
        related_checks=item.related_checks, related_pipeline_steps=item.related_pipeline_steps,
        source=item.source, dedupe_key=item.dedupe_key, scope=item.scope,
        phase=item.phase,
    )


@router.get("/system-state", response_model=SystemStateAssessmentOut)
def get_system_state_assessment(
    system_id: int = Depends(get_system_id),
) -> SystemStateAssessmentOut:
    assessment = build_system_state(system_id)
    return SystemStateAssessmentOut(
        system_id=assessment.system_id,
        generated_at=assessment.generated_at,
        overall_severity=assessment.overall_severity,
        severity_counts=assessment.severity_counts,
        items=[_item_out(item) for item in assessment.items],
        primary_item=_item_out(assessment.primary_item) if assessment.primary_item else None,
        notification_items=[_item_out(item) for item in assessment.notification_items],
        page_items={route: [_item_out(item) for item in items] for route, items in assessment.page_items.items()},
        user_phase=assessment.user_phase,
        phases=[
            SystemStatePhaseCompletionOut(phase=p.phase, complete=p.complete, label=p.label)
            for p in assessment.phases
        ],
    )
