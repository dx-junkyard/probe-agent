"""System settings diagnostics API (Issue #101).

probe-agent:
  role: API boundary for deterministic system settings diagnostics
  capability: system-configuration-health
  element_type: boundary
  consumers: [dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify diagnostics report missing/invalid configuration and last observed run failures without calling an LLM.
"""

from fastapi import APIRouter, Depends

from ..auth import get_system_id
from ..models import (
    DiagnosticLastObservedErrorOut,
    SystemDiagnosticCheckOut,
    SystemDiagnosticsOut,
)
from ..system_diagnostics import run_system_diagnostics

router = APIRouter()


@router.get("/system-diagnostics", response_model=SystemDiagnosticsOut)
def get_system_diagnostics(
    system_id: int = Depends(get_system_id),
) -> SystemDiagnosticsOut:
    report = run_system_diagnostics(system_id)
    checks = [
        SystemDiagnosticCheckOut(
            check_id=c.check_id,
            category=c.category,
            title=c.title,
            severity=c.severity,
            detail=c.detail,
            impact=c.impact,
            remediation=c.remediation,
            related_env=c.related_env,
            related_paths=c.related_paths,
            related_pages=c.related_pages,
            related_pipeline_steps=c.related_pipeline_steps,
            last_observed_error=(
                DiagnosticLastObservedErrorOut(
                    source=c.last_observed_error.source,
                    status=c.last_observed_error.status,
                    error=c.last_observed_error.error,
                    observed_at=c.last_observed_error.observed_at,
                )
                if c.last_observed_error
                else None
            ),
            decision_method="deterministic",
            fix_kind=c.fix_kind,
            fix_page=c.fix_page,
            fix_anchor=c.fix_anchor,
        )
        for c in report.checks
    ]
    return SystemDiagnosticsOut(
        system_id=report.system_id,
        generated_at=report.generated_at,
        overall_severity=report.overall_severity,
        severity_counts=report.severity_counts,
        checks=checks,
    )
