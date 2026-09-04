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
from dataclasses import asdict
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .. import assistant_discussion, assistant_discussion_proposal
from ..assistant import (
    answer_question,
    checks_for_screen,
    get_screen_context,
    suggested_questions,
    REAL_PROVIDERS,
)
from ..assistant_discussion_context import build_screen_discussion_context
from ..auth import Principal, get_principal, get_system_id
from ..db import get_conn
from ..llm import LLMClient, LLMConfig, create_llm_client
from ..models import (
    AssistantActionOut,
    AssistantAskOut,
    AssistantAskRequest,
    AssistantCitationOut,
    AssistantDiscussionProposalApplyOut,
    AssistantDiscussionProposalApplyRequest,
    AssistantDiscussionProposalOut,
    AssistantDiscussionProposalRejectOut,
    AssistantDiscussionProposalRejectRequest,
    AssistantDiscussionProposalsListOut,
    AssistantDiscussionTargetIn,
    AssistantDiscussionThreadDetailOut,
    AssistantDiscussionThreadOut,
    AssistantDiscussionThreadsListOut,
    AssistantDiscussionTurnOut,
    AssistantScreenContextOut,
    AssistantSpeechRequest,
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
from ..ui_help_registry import HELP_BY_ID, UI_HELP_REGISTRY_VERSION
from ..voice_speech import SpeechGenerationError, project_spoken_answer, stream_speech

router = APIRouter()


@router.post("/assistant/speech")
def assistant_speech(payload: AssistantSpeechRequest) -> StreamingResponse:
    """Render a bounded spoken answer through OpenAI without exposing keys."""
    try:
        audio = stream_speech(payload.text)
        # Advance once here so configuration/upstream connection failures are
        # returned as JSON HTTP errors instead of a broken 200 audio stream.
        first = next(audio)
    except StopIteration as exc:
        raise HTTPException(status_code=502, detail="OpenAI speech returned no audio.") from exc
    except SpeechGenerationError as exc:
        status = 503 if "not configured" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    def with_first_chunk():
        yield first
        yield from audio

    return StreamingResponse(
        with_first_chunk(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _principal_actor(principal: Principal) -> str:
    """The authenticated audit identity for a write. Never a body-supplied
    value -- mirrors `routes/ux_design.py`'s `_principal_actor`."""
    if principal.username:
        return principal.username
    return f"user:{principal.user_id}"


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


# --- Discussion threads (Issue #438) ------------------------------------------


def _thread_out(row: Dict[str, Any]) -> AssistantDiscussionThreadOut:
    return AssistantDiscussionThreadOut(
        id=row["id"],
        system_id=row["system_id"],
        thread_key=row["thread_key"],
        scope=row["scope"],
        screen_id=row["screen_id"],
        target_kind=row["target_kind"],
        target_ref=row["target_ref"],
        target_title=row["target_title"] or "",
        captured_target_revision_id=row["captured_target_revision_id"],
        captured_target_digest=row["captured_target_digest"] or "",
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        schema_version=row["schema_version"],
    )


def _turn_out(row: Dict[str, Any]) -> AssistantDiscussionTurnOut:
    return AssistantDiscussionTurnOut(
        id=row["id"],
        thread_id=row["thread_id"],
        turn_number=row["turn_number"],
        role=row["role"],
        content=row["content"],
        citations=[AssistantCitationOut(**c) for c in row.get("citations") or []],
        target_revision_id=row.get("target_revision_id"),
        target_digest=row.get("target_digest") or "",
        used_fallback=bool(row.get("used_fallback")),
        decision_method=row["decision_method"],
        input_mode=row.get("input_mode") or "text",
        provider=row.get("provider") or "",
        model=row.get("model") or "",
        prompt_version=row.get("prompt_version") or "",
        schema_version=row.get("schema_version") or "assistant-discussion-turn-v1",
        created_by=row.get("created_by"),
        created_at=row["created_at"],
    )


def _thread_detail_out(data: Dict[str, Any]) -> AssistantDiscussionThreadDetailOut:
    return AssistantDiscussionThreadDetailOut(
        thread=_thread_out(data["thread"]),
        target_state=data["target_state"],
        turns=[_turn_out(t) for t in data["turns"]],
    )


@router.post("/assistant/discussion-threads", response_model=AssistantDiscussionThreadDetailOut)
def create_or_resolve_discussion_thread(
    payload: AssistantDiscussionTargetIn,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> AssistantDiscussionThreadDetailOut:
    """§1.5: resolve-or-create, idempotent. `thread_key` (not a body-supplied
    id) is the identity, so calling this twice for the same target returns
    the SAME thread."""
    try:
        data = assistant_discussion.resolve_or_create_thread(
            system_id,
            scope=payload.scope,
            screen_id=payload.screen_id,
            target_kind=payload.target_kind,
            target_ref=payload.target_ref,
            created_by=_principal_actor(principal),
        )
    except assistant_discussion.DiscussionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _thread_detail_out(data)


@router.get("/assistant/discussion-threads", response_model=AssistantDiscussionThreadsListOut)
def list_discussion_threads(
    screen_id: Optional[str] = None,
    scope: Optional[str] = None,
    target_kind: Optional[str] = None,
    target_ref: Optional[str] = None,
    system_id: int = Depends(get_system_id),
) -> AssistantDiscussionThreadsListOut:
    rows = assistant_discussion.list_threads(
        system_id,
        screen_id=screen_id,
        scope=scope,
        target_kind=target_kind,
        target_ref=target_ref,
    )
    return AssistantDiscussionThreadsListOut(threads=[_thread_out(r) for r in rows])


@router.get(
    "/assistant/discussion-threads/{thread_id}",
    response_model=AssistantDiscussionThreadDetailOut,
)
def get_discussion_thread(
    thread_id: int,
    system_id: int = Depends(get_system_id),
) -> AssistantDiscussionThreadDetailOut:
    data = assistant_discussion.get_thread(system_id, thread_id)
    if data is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown discussion thread id: {thread_id}"
        )
    return _thread_detail_out(data)


# --- Discussion proposals (Issue #439) ----------------------------------------


def _proposal_detail_out(data: Dict[str, Any]) -> AssistantDiscussionProposalOut:
    return AssistantDiscussionProposalOut(**data)


@router.post(
    "/assistant/discussion-threads/{thread_id}/proposals",
    response_model=AssistantDiscussionProposalOut,
    status_code=201,
)
def create_discussion_proposal(
    thread_id: int,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> AssistantDiscussionProposalOut:
    """§2.2: summarize a thread's turns into a reviewable proposal.

    Read -> reason -> persist (CLAUDE.md Implementation Constraints): the
    deterministic reads happen first, the reasoning call runs with NO
    `get_conn()` connection open, and the audit `intelligence_runs` row is
    written whether the run succeeded or failed (Principle 7) -- but the
    proposal row itself is written only on success.
    """
    thread_data = assistant_discussion.get_thread(system_id, thread_id)
    if thread_data is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown discussion thread id: {thread_id}"
        )
    thread_row = thread_data["thread"]

    config = LLMConfig.intelligence_from_env()
    client = _usable_llm_client(config)

    with get_conn() as conn:
        recent = assistant_discussion.recent_turns(conn, thread_id)
        target_facts = assistant_discussion_proposal.gather_target_context(
            conn, system_id, thread_row["target_kind"], thread_row["target_ref"]
        )
    resolved = assistant_discussion.resolve_target(
        system_id, thread_row["target_kind"], thread_row["target_ref"]
    )

    result = assistant_discussion_proposal.generate_proposal(
        client, config,
        target_kind=thread_row["target_kind"], target_ref=thread_row["target_ref"],
        target_title=thread_row["target_title"] or thread_row["target_ref"],
        turns=recent, target_facts=target_facts,
    )
    completed_at = time.time()

    with get_conn() as conn:
        run_status = "failed" if result.error else "completed"
        run_cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model, prompt_version,
                    schema_version, decision_method, status, error_details, is_mock,
                    started_at, completed_at)
               VALUES (?, NULL, 'discussion_proposal', ?, ?, ?, ?, 'reasoning_llm', ?, ?, ?, ?, ?)""",
            (
                system_id, result.provider, result.model, result.prompt_version,
                result.schema_version, run_status, result.error, 1 if result.is_mock else 0,
                completed_at, completed_at,
            ),
        )
        run_id = run_cur.lastrowid

        if result.error:
            status_code = 503 if result.error_kind == "unavailable" else 502
            code = "reasoning_unavailable" if status_code == 503 else "discussion_proposal_generation_failed"
            raise HTTPException(
                status_code=status_code, detail={"code": code, "message": result.error}
            )

        row = assistant_discussion_proposal.create_proposal(
            conn, system_id=system_id, thread_id=thread_id, screen_id=thread_row["screen_id"],
            target_kind=thread_row["target_kind"], target_ref=thread_row["target_ref"],
            captured_target_revision_id=resolved.revision_id, captured_target_digest=resolved.digest,
            result=result, intelligence_run_id=run_id, created_by=_principal_actor(principal),
        )
        proposal_id = row["id"]

    detail = assistant_discussion_proposal.get_proposal_detail(system_id, proposal_id)
    assert detail is not None
    return _proposal_detail_out(detail)


@router.get(
    "/assistant/discussion-threads/{thread_id}/proposals",
    response_model=AssistantDiscussionProposalsListOut,
)
def list_discussion_proposals(
    thread_id: int,
    system_id: int = Depends(get_system_id),
) -> AssistantDiscussionProposalsListOut:
    thread_data = assistant_discussion.get_thread(system_id, thread_id)
    if thread_data is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown discussion thread id: {thread_id}"
        )
    rows = assistant_discussion_proposal.list_proposals(system_id, thread_id)
    return AssistantDiscussionProposalsListOut(proposals=[_proposal_detail_out(r) for r in rows])


@router.get(
    "/assistant/discussion-proposals/{proposal_id}",
    response_model=AssistantDiscussionProposalOut,
)
def get_discussion_proposal(
    proposal_id: int,
    system_id: int = Depends(get_system_id),
) -> AssistantDiscussionProposalOut:
    data = assistant_discussion_proposal.get_proposal_detail(system_id, proposal_id)
    if data is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown discussion proposal id: {proposal_id}"
        )
    return _proposal_detail_out(data)


@router.post(
    "/assistant/discussion-proposals/{proposal_id}/apply",
    response_model=AssistantDiscussionProposalApplyOut,
)
def apply_discussion_proposal(
    proposal_id: int,
    payload: AssistantDiscussionProposalApplyRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> AssistantDiscussionProposalApplyOut:
    try:
        detail, applied_ids = assistant_discussion_proposal.apply_items(
            system_id, proposal_id, payload.item_ids,
            rationale=payload.rationale, actor=_principal_actor(principal),
        )
    except assistant_discussion_proposal.ApplyRejected as exc:
        raise HTTPException(
            status_code=422, detail={"code": exc.code, "message": str(exc)}
        ) from exc
    except assistant_discussion_proposal.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except assistant_discussion_proposal.DiscussionProposalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AssistantDiscussionProposalApplyOut(
        proposal=_proposal_detail_out(detail), applied_item_ids=applied_ids,
    )


@router.post(
    "/assistant/discussion-proposals/{proposal_id}/reject",
    response_model=AssistantDiscussionProposalRejectOut,
)
def reject_discussion_proposal(
    proposal_id: int,
    payload: AssistantDiscussionProposalRejectRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> AssistantDiscussionProposalRejectOut:
    try:
        detail, rejected_ids = assistant_discussion_proposal.reject_items(
            system_id, proposal_id, payload.item_ids,
            rationale=payload.rationale, actor=_principal_actor(principal),
        )
    except assistant_discussion_proposal.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except assistant_discussion_proposal.DiscussionProposalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AssistantDiscussionProposalRejectOut(
        proposal=_proposal_detail_out(detail), rejected_item_ids=rejected_ids,
    )


@router.post("/assistant/ask", response_model=AssistantAskOut)
def assistant_ask(
    payload: AssistantAskRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> AssistantAskOut:
    ctx = get_screen_context(payload.screen_id)
    if ctx is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown screen id: {payload.screen_id}"
        )

    thread_row: Optional[Dict[str, Any]] = None
    thread_target_state: Optional[str] = None
    if payload.thread_id is not None:
        thread_data = assistant_discussion.get_thread(system_id, payload.thread_id)
        if thread_data is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown discussion thread id: {payload.thread_id}"
            )
        thread_row = thread_data["thread"]
        thread_target_state = thread_data["target_state"]

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

    effective_route_params: Dict[str, str] = dict(payload.route_params)
    conversation_messages = [message.model_dump() for message in payload.conversation]
    if thread_row is not None:
        # The thread's own target always grounds the context pack, even if
        # the client's route_params drifted (§1.5: "thread の対象を...route
        # params へ注入").
        effective_route_params.update(
            assistant_discussion.route_params_for_target(
                thread_row["target_kind"], thread_row["target_ref"]
            )
        )
        if thread_target_state in ("current", "not_tracked"):
            # §1.3: bounded LLM context is only ever taken from a thread
            # whose target still matches what was captured -- a stale or
            # unresolvable thread's history stays readable but is never
            # auto-inherited as current fact.
            with get_conn() as conn:
                recent = assistant_discussion.recent_turns(conn, thread_row["id"])
            conversation_messages = [
                {"role": t["role"], "content": t["content"]} for t in recent
            ]
        else:
            conversation_messages = []

    discussion = build_screen_discussion_context(
        payload.screen_id, system_id, effective_route_params
    )
    screen_data: Optional[Dict[str, Any]] = dict(discussion.facts) if discussion else None
    screen_data_sources = list(discussion.sources) if discussion else []
    # Issue #441 element-scope voice turns carry the deterministic help id in
    # route params.  It is useful only after the server validates an exact
    # registry match for this screen; arbitrary or cross-screen ids are never
    # promoted into LLM context.
    voice_help_id = effective_route_params.get("voice_element_help_id", "").strip()
    voice_help = HELP_BY_ID.get(voice_help_id)
    if voice_help is not None and voice_help.screen_id == payload.screen_id:
        if screen_data is None:
            screen_data = {}
        screen_data["ui_help_target"] = {
            "help_id": voice_help.help_id,
            "screen_id": voice_help.screen_id,
            "scope": voice_help.scope,
            "title": voice_help.title,
            "summary": voice_help.summary,
            "usage": voice_help.usage,
            "doc_refs": [asdict(ref) for ref in voice_help.doc_refs],
            "registry_version": UI_HELP_REGISTRY_VERSION,
            "context_kind": "product_documentation",
        }
        screen_data_sources.append({
            "id": f"ui_help:{voice_help.help_id}",
            "title": f"UI help: {voice_help.title}",
        })
    if thread_row is not None:
        if screen_data is None:
            screen_data = {}
        # The thread's own target facts, so the model knows what this
        # conversation is scoped to, and a citable source id for it (§1.5).
        screen_data["discussion_target"] = {
            "scope": thread_row["scope"],
            "target_kind": thread_row["target_kind"],
            "target_ref": thread_row["target_ref"],
            "target_title": thread_row["target_title"],
            "target_state": thread_target_state,
        }
        screen_data_sources.append(
            {
                "id": f"discussion_target:{thread_row['target_kind']}:{thread_row['target_ref']}",
                "title": f"Discussion target: {thread_row['target_title'] or thread_row['target_ref']}",
            }
        )

    result = answer_question(
        ctx,
        payload.question,
        report,
        config,
        client,
        visible_check_ids=payload.visible_check_ids,
        state_items=state_items,
        focused_state_id=focused_state_id,
        screen_data=screen_data,
        screen_data_sources=screen_data_sources if screen_data is not None else None,
        route_params=effective_route_params,
        conversation=conversation_messages,
        voice_mode=payload.input_mode == "voice",
        voice_continuation=payload.voice_continuation,
        voice_spoken_history=payload.voice_spoken_history,
    )

    thread_id_out: Optional[int] = None
    turn_number_out: Optional[int] = None
    recheck_required = False
    if thread_row is not None:
        # Re-resolve AFTER the LLM call (never hold a `get_conn()` across an
        # external call) so what gets persisted reflects the target as of
        # answering, not as of the read at the top of this request.
        resolved = assistant_discussion.resolve_target(
            system_id, thread_row["target_kind"], thread_row["target_ref"]
        )
        citations_payload = [asdict(c) for c in result.citations]
        with get_conn() as conn:
            conn.execute("BEGIN")
            try:
                assistant_discussion.append_turn(
                    conn,
                    system_id=system_id,
                    thread_id=thread_row["id"],
                    role="user",
                    content=payload.question,
                    decision_method="manual",
                    # Issue #441: the entry mode belongs to the human's turn.
                    # The assistant turn keeps the default `text`: it did not
                    # speak into a microphone, and reading its answer aloud is
                    # a client playback choice, not a fact about the turn.
                    input_mode=payload.input_mode,
                    created_by=_principal_actor(principal),
                )
                assistant_turn = assistant_discussion.append_turn(
                    conn,
                    system_id=system_id,
                    thread_id=thread_row["id"],
                    role="assistant",
                    content=result.answer,
                    citations=citations_payload,
                    target_revision_id=resolved.revision_id,
                    target_digest=resolved.digest,
                    used_fallback=result.used_fallback,
                    decision_method=result.decision_method,
                    provider=result.provider,
                    model=result.model,
                    prompt_version=result.prompt_version,
                )
                assistant_discussion.touch_thread_captured_target(
                    conn, thread_row["id"], resolved
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        thread_id_out = thread_row["id"]
        turn_number_out = assistant_turn["turn_number"]
        recheck_required = thread_target_state not in ("current", "not_tracked")

    voice_projection = (
        project_spoken_answer(result.answer, payload.voice_spoken_history)
        if payload.input_mode == "voice"
        else None
    )
    return AssistantAskOut(
        screen_id=ctx.screen_id,
        answer=result.answer,
        spoken_answer=voice_projection.text if voice_projection else None,
        voice_follow_up_expected=bool(
            voice_projection and voice_projection.expects_reply
        ),
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
        thread_id=thread_id_out,
        target_state=thread_target_state,
        recheck_required=recheck_required,
        turn_number=turn_number_out,
    )
