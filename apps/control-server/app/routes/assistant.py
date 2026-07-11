"""Per-screen assistant API (Issue #102).

- GET /assistant/settings-metadata: static, code-managed settings explanations.
- GET /assistant/screen-context/{screen_id}: static screen context plus the
  current deterministic diagnostics subset and suggested questions.
- POST /assistant/ask: grounded answer for a natural-language question about
  one screen. Uses the intelligence LLM when a real provider is configured,
  otherwise a deterministic fallback visibly marked `used_fallback: true`.

probe-agent:
  role: API boundary for the per-screen assistant
  capability: system-configuration-help
  element_type: boundary
  consumers: [dashboard]
  operation_kind: orchestration
  state_effects: [database-read, external-api]
  probe_value: Verify screen contexts and answers stay grounded in static metadata plus deterministic diagnostics, with a marked fallback when the LLM is unavailable.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..assistant import (
    answer_question,
    checks_for_screen,
    get_screen_context,
    suggested_questions,
    REAL_PROVIDERS,
)
from ..auth import get_system_id
from ..llm import LLMClient, LLMConfig, create_llm_client
from ..models import (
    AssistantActionOut,
    AssistantAskOut,
    AssistantAskRequest,
    AssistantCitationOut,
    AssistantScreenContextOut,
    AssistantSuggestedQuestionOut,
    DiagnosticLastObservedErrorOut,
    SettingMetadataOut,
    SettingsMetadataOut,
    SystemDiagnosticCheckOut,
)
from ..settings_metadata import SETTINGS_METADATA
from ..system_diagnostics import (
    DiagnosticCheck,
    _worst_severity,
    run_system_diagnostics,
)
from ..system_state import build_system_state

router = APIRouter()


def _check_out(check: DiagnosticCheck) -> SystemDiagnosticCheckOut:
    return SystemDiagnosticCheckOut(
        check_id=check.check_id,
        category=check.category,
        title=check.title,
        severity=check.severity,
        detail=check.detail,
        impact=check.impact,
        remediation=check.remediation,
        related_env=check.related_env,
        related_paths=check.related_paths,
        related_pages=check.related_pages,
        related_pipeline_steps=check.related_pipeline_steps,
        last_observed_error=(
            DiagnosticLastObservedErrorOut(
                source=check.last_observed_error.source,
                status=check.last_observed_error.status,
                error=check.last_observed_error.error,
                observed_at=check.last_observed_error.observed_at,
            )
            if check.last_observed_error
            else None
        ),
        decision_method="deterministic",
        fix_kind=check.fix_kind,
        fix_page=check.fix_page,
        fix_anchor=check.fix_anchor,
    )


@router.get("/assistant/settings-metadata", response_model=SettingsMetadataOut)
def get_settings_metadata() -> SettingsMetadataOut:
    return SettingsMetadataOut(
        settings=[
            SettingMetadataOut(
                key=s.key,
                display_name=s.display_name,
                category=s.category,
                requiredness=s.requiredness,
                description=s.description,
                impact=s.impact,
                remediation=s.remediation,
                valid_values=s.valid_values,
                validation_rule=s.validation_rule,
                related_checks=s.related_checks,
                related_pages=s.related_pages,
                related_pipeline_steps=s.related_pipeline_steps,
                docs_link=s.docs_link,
            )
            for s in SETTINGS_METADATA
        ]
    )


@router.get(
    "/assistant/screen-context/{screen_id}",
    response_model=AssistantScreenContextOut,
)
def get_assistant_screen_context(
    screen_id: str,
    system_id: int = Depends(get_system_id),
) -> AssistantScreenContextOut:
    ctx = get_screen_context(screen_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"Unknown screen id: {screen_id}")
    report = run_system_diagnostics(system_id)
    screen_checks = checks_for_screen(ctx, report)
    severities = [c.severity for c in screen_checks]
    return AssistantScreenContextOut(
        screen_id=ctx.screen_id,
        title=ctx.title,
        route=ctx.route,
        purpose=ctx.purpose,
        primary_data_sources=ctx.primary_data_sources,
        visible_sections=ctx.visible_sections,
        common_questions=ctx.common_questions,
        related_settings=ctx.related_settings,
        related_checks=ctx.related_checks,
        related_pipeline_steps=ctx.related_pipeline_steps,
        related_endpoints=ctx.related_endpoints,
        state_severity=_worst_severity(severities) if severities else "ok",
        screen_checks=[_check_out(c) for c in screen_checks],
        suggested_questions=[
            AssistantSuggestedQuestionOut(**q)
            for q in suggested_questions(ctx, screen_checks)
        ],
    )


def _usable_llm_client(config: LLMConfig) -> Optional[LLMClient]:
    """A client only for real, keyed providers; mock/unknown answer via fallback."""
    if config.provider not in REAL_PROVIDERS or not config.api_key:
        return None
    return create_llm_client(config)


@router.post("/assistant/ask", response_model=AssistantAskOut)
def assistant_ask(
    payload: AssistantAskRequest,
    system_id: int = Depends(get_system_id),
) -> AssistantAskOut:
    ctx = get_screen_context(payload.screen_id)
    if ctx is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown screen id: {payload.screen_id}"
        )
    report = run_system_diagnostics(system_id)
    assessment = build_system_state(system_id)
    state_by_id = {item.state_id: item for item in assessment.items}
    visible_state_ids = list(dict.fromkeys(payload.visible_state_ids))
    state_items = [state_by_id[state_id] for state_id in visible_state_ids if state_id in state_by_id]
    focused_state_id = payload.focused_state_id if payload.focused_state_id in state_by_id else None
    if focused_state_id and focused_state_id not in {item.state_id for item in state_items}:
        state_items.insert(0, state_by_id[focused_state_id])
    config = LLMConfig.intelligence_from_env()
    client = _usable_llm_client(config)
    result = answer_question(
        ctx,
        payload.question,
        report,
        config,
        client,
        visible_check_ids=payload.visible_check_ids,
        state_items=state_items,
        focused_state_id=focused_state_id,
    )
    return AssistantAskOut(
        screen_id=ctx.screen_id,
        answer=result.answer,
        suggested_actions=[
            AssistantActionOut(
                label=a.label, kind=a.kind, target=a.target, detail=a.detail
            )
            for a in result.suggested_actions
        ],
        citations=[
            AssistantCitationOut(
                type=c.type, id=c.id, title=c.title, detail=c.detail
            )
            for c in result.citations
        ],
        used_fallback=result.used_fallback,
        fallback_reason=result.fallback_reason,
        decision_method=result.decision_method,
        provider=result.provider,
        model=result.model,
        prompt_version=result.prompt_version,
        schema_version=result.schema_version,
        generated_at=time.time(),
    )
