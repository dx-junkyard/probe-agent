"""Inquiry answer generation (Issue #285, reworked by Issue #286).

When a developer has a doubt about a confirmation item (a Q&A question, an
Intent Brief item, or — from Issue #287 onward — a review item), the
Inquiry conversation answers *that doubt*, strictly separate from answering
or confirming the original item (see ``routes/interview_inquiry.py``). This
module generates the assistant's answer inside that conversation.

Issue #286 splits that single reasoning call into three composed steps:

1. ``question_router.route_question`` classifies the Inquiry question into
   ``human_only`` | ``system_researchable`` | ``hybrid``.
2. For ``system_researchable``/``hybrid``, ``investigation_agent.investigate``
   researches the PINNED repository snapshot only, within an explicit budget
   (never the working tree, never unbounded reads).
3. ``response_composer`` deterministically assembles the final conclusion-
   first message from (1) and (2) -- no new wording is invented outside a
   fixed server template or a reasoning step's own validated output.

This module stays a pure orchestration function: it never touches the
database. ``route`` and ``investigation`` are returned as sub-results so the
caller (``routes/interview_inquiry.py``) can persist each reasoning call as
its own ``intelligence_runs`` audit row (Principle 7), exactly as the two
steps are two separate reasoning calls with their own prompt/schema
versions. Keeping this module DB-free also means Issue #287+ can keep
reusing it without threading write access through here.

Fail-closed (Principle 6): if question routing itself fails (mock client,
non-reasoning model, API failure, invalid structured output), the whole
answer fails closed -- callers must not persist an assistant message, only
the failed intelligence_runs audit row(s) (Principle 7). The same holds if
an ``investigate()`` call reports ``status="failed"``. ``answerable=false``
(the ``system_researchable``/unresolved-investigation case) is a distinct,
non-error outcome: the caller substitutes a fixed, server-authored message
(never LLM-fabricated text) instead of the model's own wording.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .interview_language import get_interview_language
from .investigation_agent import (
    InvestigationBudget,
    InvestigationEvidenceItem,
    InvestigationResult,
    InvestigationRuntimeEvidenceItem,
    investigate,
)
from .llm import LLMClient, LLMConfig, MockLLMClient, is_reasoning_model
from .models import InterviewContextPack
from .question_router import RouteResult, route_question
from .response_composer import compose_human_only, compose_hybrid, compose_system_researchable

PROMPT_VERSION = "inquiry-answer-v2"
SCHEMA_VERSION = "inquiry-answer-v2"

MAX_CONVERSATION_MESSAGES = 20


# --- Public result types ------------------------------------------------------


# Kept as an alias so existing imports (``from .investigation_agent import
# InvestigationEvidenceItem``) and this module's own public evidence type
# stay interchangeable for callers built against Issue #285's shape.
InquiryEvidenceItem = InvestigationEvidenceItem


@dataclass
class InquiryAnswerResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    conclusion: str = ""
    key_points: List[str] = field(default_factory=list)
    evidence: List[InquiryEvidenceItem] = field(default_factory=list)
    runtime_evidence: List[InvestigationRuntimeEvidenceItem] = field(default_factory=list)
    uncertainty: str = ""
    answerable: bool = False
    evidence_dropped: int = 0
    decision_question: Optional[str] = None
    # Sub-run audit results, so the caller can persist each reasoning call
    # as its own intelligence_runs row. None when that step never ran
    # (route: only on an early failure before it could be called;
    # investigation: always None for human_only).
    route: Optional[RouteResult] = None
    investigation: Optional[InvestigationResult] = None
    error: Optional[str] = None


def _build_router_context(
    context_pack: InterviewContextPack,
    conversation: List[Dict[str, str]],
    origin_summary: Optional[str],
) -> str:
    parts: List[str] = []
    if origin_summary:
        parts.append(f"Original item under discussion (context only): {origin_summary}")
    parts.append(
        f"Snapshot has {context_pack.total_symbols} indexed symbol(s) and "
        f"{context_pack.total_entrypoints} entrypoint(s) "
        f"({context_pack.unclassified_count} unclassified)."
    )
    recent = conversation[-MAX_CONVERSATION_MESSAGES:]
    if recent:
        convo = "\n".join(f"{m.get('role', '?')}: {m.get('content', '')}" for m in recent)
        parts.append(f"Inquiry conversation so far:\n{convo}")
    return "\n\n".join(parts)


def generate_inquiry_answer(
    client: LLMClient,
    config: LLMConfig,
    *,
    context_pack: InterviewContextPack,
    question_text: str,
    conversation: Optional[List[Dict[str, str]]] = None,
    origin_summary: Optional[str] = None,
    repo_path: Optional[str] = None,
    commit_sha: Optional[str] = None,
    investigation_budget: Optional[InvestigationBudget] = None,
    conn=None,
    system_id: Optional[int] = None,
    snapshot_id: Optional[int] = None,
) -> InquiryAnswerResult:
    """Generate one assistant answer for an Inquiry conversation turn.

    Routes the question first; ``human_only`` never triggers investigation.
    ``system_researchable``/``hybrid`` call ``investigate()`` against
    ``repo_path``/``commit_sha`` (the pinned snapshot's Git location) and
    compose the final message deterministically from its result.

    Fail-closed: a mock client, a non-reasoning model, invalid language
    configuration, a routing failure, or an investigation failure
    (``status="failed"``) all return a result with ``error`` set and no
    conclusion -- callers must not persist an assistant message for these.
    ``answerable=false`` is not an error: it is the
    ``system_researchable``/unresolved-investigation outcome, and the caller
    substitutes a fixed, non-fabricated message instead of ``conclusion``.
    """
    conversation = conversation or []
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return InquiryAnswerResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Inquiry answer generation requires a configured reasoning "
                "model; mock/heuristic fallback is prohibited"
            ),
        )

    try:
        language = get_interview_language()
    except ValueError as exc:
        return InquiryAnswerResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    router_context = _build_router_context(context_pack, conversation, origin_summary)
    route = route_question(client, config, question_text=question_text, context=router_context, language=language)

    if route.error:
        return InquiryAnswerResult(
            provider=config.provider, model=config.model, is_mock=False,
            route=route, error=route.error,
        )

    if route.category == "human_only":
        composed = compose_human_only(question_text=question_text, route_reason=route.reason, language=language)
        return InquiryAnswerResult(
            provider=config.provider, model=config.model, is_mock=False,
            conclusion=composed.conclusion, key_points=composed.key_points,
            evidence=composed.evidence, uncertainty=composed.uncertainty,
            answerable=composed.answerable, decision_question=composed.decision_question,
            route=route, investigation=None,
        )

    # system_researchable | hybrid: both require a pinned snapshot location.
    if not repo_path or not commit_sha:
        return InquiryAnswerResult(
            provider=config.provider, model=config.model, is_mock=False,
            route=route,
            error="Investigation requires a pinned snapshot repo_path/commit_sha",
        )

    investigation = investigate(
        client, config,
        repo_path=repo_path, commit_sha=commit_sha,
        question=question_text, research_focus=route.research_focus,
        search_keywords=route.search_keywords,
        hybrid_decision_question=(route.category == "hybrid"),
        language=language, budget=investigation_budget,
        conn=conn, system_id=system_id, snapshot_id=snapshot_id,
    )

    if investigation.status == "failed":
        return InquiryAnswerResult(
            provider=config.provider, model=config.model, is_mock=False,
            route=route, investigation=investigation, error=investigation.error,
        )

    if route.category == "hybrid":
        composed = compose_hybrid(investigation=investigation, question_text=question_text, language=language)
    else:
        composed = compose_system_researchable(investigation=investigation, language=language)

    return InquiryAnswerResult(
        provider=config.provider, model=config.model, is_mock=False,
        conclusion=composed.conclusion, key_points=composed.key_points,
        evidence=composed.evidence, runtime_evidence=composed.runtime_evidence,
        uncertainty=composed.uncertainty,
        answerable=composed.answerable, decision_question=composed.decision_question,
        evidence_dropped=len(investigation.pruned_evidence),
        route=route, investigation=investigation,
    )
