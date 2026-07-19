"""Question Router (Issue #286).

Before any investigation work happens, a developer's follow-up question
(either the free-standing ``interview_qa`` question or an Inquiry's doubt
question) is classified into a finite routing category so the system never
silently "investigates" something only the developer can decide, and never
silently skips investigation for something the codebase can actually answer:

- ``human_only``: the question is a decision only the developer can make
  (priorities, intent, business tradeoffs). No code investigation is useful.
- ``system_researchable``: the question can be answered by reading the
  pinned snapshot (and, indirectly, runtime facts) -- no human judgement
  call is needed once the facts are found.
- ``hybrid``: part of the question is answerable from the codebase, but it
  also requires the developer's own decision on top of the facts found.

This module makes exactly one reasoning-model call and nothing else: no
file reads, no DB access, no retrieval. Category classification is not a
direct structural check (there is no deterministic rule that tells
"business decision" apart from "implementation fact" from free text), so
per Principle 6 this is a reasoning-model decision, not a keyword heuristic.

Fail-closed (Principle 6): a mock client, a non-reasoning model, an API
failure, or a structured-output validation failure (including a category
outside the finite set) all return an error result -- callers must not
persist a route decision or treat the question as routed. Never a heuristic
substitute (e.g. never fall back to a keyword-based guess).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .interview_language import language_directive
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "question-router-v1"
SCHEMA_VERSION = "question-router-v1"

RouteCategory = Literal["human_only", "system_researchable", "hybrid"]
ROUTE_CATEGORIES = ("human_only", "system_researchable", "hybrid")


# --- Raw response schema (what we require the model to return) --------------


class _RawRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=1, max_length=50)
    reason: str = Field(..., min_length=1, max_length=1_000)
    research_focus: Optional[str] = Field(default=None, max_length=1_000)


# --- Public result type -------------------------------------------------------


@dataclass
class RouteResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    category: Optional[str] = None
    reason: str = ""
    research_focus: Optional[str] = None
    error: Optional[str] = None


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


_SYSTEM_PROMPT = """\
You are the Question Router of probe-agent's system-understanding Inquiry \
flow. A developer asked a follow-up question while confirming an item during \
a system-understanding interview. Classify it into exactly one of three \
categories before any investigation happens:

- "human_only": the question is a decision, priority, business tradeoff, or \
intent only the developer can make. Reading the codebase cannot answer it.
- "system_researchable": the question can be answered by reading the pinned \
source snapshot (and, indirectly, runtime facts) -- no human judgement call \
is needed once the facts are found.
- "hybrid": part of the question is answerable from the codebase, but it \
also needs the developer's own decision on top of whatever facts are found.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "category": "human_only | system_researchable | hybrid",
  "reason": "a short reason for this classification",
  "research_focus": "what a read-only code investigation should look for, or null for human_only"
}

Rules:
- "category" must be exactly one of the three values above.
- "research_focus" must be null for "human_only" (there is nothing to \
investigate). For "system_researchable" and "hybrid" it should name the \
concrete thing to look for in the code (e.g. a symbol, a file area, a \
behavior) -- never a restatement of "look for the answer".
- You never decide, adopt, apply, or answer anything yourself here. This \
step only classifies the question.
"""


def _system_prompt(language: str) -> str:
    return _SYSTEM_PROMPT + language_directive(language) + "\n"


def _build_user_prompt(question_text: str, context: str) -> str:
    parts = ["## Context", context or "(no additional context)", "## Developer's question", question_text]
    return "\n\n".join(parts)


def route_question(
    client: LLMClient,
    config: LLMConfig,
    *,
    question_text: str,
    context: str = "",
    language: str = "ja",
) -> RouteResult:
    """Classify a question into human_only | system_researchable | hybrid.

    Fail-closed: a mock client, a non-reasoning model, an API failure, or an
    invalid/out-of-set structured response all return a result with ``error``
    set and ``category=None`` -- callers must not persist a route decision or
    proceed to investigation for these.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return RouteResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Question routing requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    prompt = _build_user_prompt(question_text, context)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _system_prompt(language)},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=1024,
        )
    except LLMError as exc:
        return RouteResult(provider=config.provider, model=config.model, is_mock=False, error=str(exc))

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawRouteResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return RouteResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    if validated.category not in ROUTE_CATEGORIES:
        return RouteResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Model returned an invalid category: {validated.category!r}",
        )

    research_focus = validated.research_focus if validated.category != "human_only" else None

    return RouteResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        category=validated.category,
        reason=validated.reason,
        research_focus=research_focus,
    )
