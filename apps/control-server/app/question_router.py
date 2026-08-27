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

Issue #291 extends the schema additively with ``knowledge_area``: which of a
fixed set of knowledge areas (product_intent | domain_rule | operations |
implementation | security, or null when none clearly fits) the question
belongs to. This is used only to group out-of-area questions in the
Dashboard (routes/interview.py's askable-view filter) -- it never hides a
question, and it is always the LLM's classification, never a deterministic
guess from title/repository text (Principle 6). Adding this field bumped
both ``PROMPT_VERSION`` and ``SCHEMA_VERSION`` to 'question-router-v2'
(Principle 7: any prompt/schema change is a new version).

A later review fix on top of Issue #286 adds ``search_keywords``: a small
list of ASCII code identifiers / symbol names / file-path fragments the
model expects to help
retrieve relevant files, returned only for ``system_researchable`` /
``hybrid`` (forced to ``[]`` for ``human_only``, same rule as
``research_focus`` -> ``None``). This exists because
``investigation_agent._select_candidates`` matches keywords against repo
FILE PATHS, which are almost always ASCII identifiers -- a Japanese-only
question tokenizes to no ASCII keywords at all (Unicode/CJK tokens from the
question text can match Japanese-named paths/docs, but not ASCII source
paths), so without an explicit ASCII hint from the router a
non-English question can select zero candidate files and end the
investigation ``unresolved`` before any reasoning-model call happens. This
bumped both ``PROMPT_VERSION`` and ``SCHEMA_VERSION`` again, to
'question-router-v3' (Principle 7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .interview_language import language_directive
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "question-router-v3"
SCHEMA_VERSION = "question-router-v3"

RouteCategory = Literal["human_only", "system_researchable", "hybrid"]
ROUTE_CATEGORIES = ("human_only", "system_researchable", "hybrid")

# Issue #291: finite knowledge-area set (Principle 6). Kept here (rather than
# only in models.py) since the router's own schema validation must reject an
# out-of-set value fail-closed, the same discipline as ROUTE_CATEGORIES.
KnowledgeArea = Literal[
    "product_intent", "domain_rule", "operations", "implementation", "security",
]
KNOWLEDGE_AREAS = ("product_intent", "domain_rule", "operations", "implementation", "security")


# --- Raw response schema (what we require the model to return) --------------


class _RawRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=1, max_length=50)
    reason: str = Field(..., min_length=1, max_length=1_000)
    research_focus: Optional[str] = Field(default=None, max_length=1_000)
    # Issue #291: additive, nullable. Absent/omitted by older prompt
    # callers would fail model_validate under extra="forbid" only if an
    # UNKNOWN field were sent; a missing optional field is fine (defaults to
    # None), so this is a backward-compatible addition to the schema.
    knowledge_area: Optional[str] = Field(default=None, max_length=50)
    # search_keywords fix (question-router-v3): additive, defaults to [] so
    # an older/partial model reply that omits it entirely still validates
    # (same backward-compatible pattern as knowledge_area above). At most 10
    # keywords, each 1-50 chars; non-ASCII strings are accepted (harmless --
    # investigation_agent tokenizes with its own regex, which simply yields
    # nothing useful for a non-ASCII hint rather than erroring).
    search_keywords: List[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default_factory=list,
        max_length=10,
        description="ASCII code identifiers/paths a code search should try",
    )


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
    # Issue #291: null when the model found no clearly-matching area -- never
    # hidden from the list, only used for out-of-area grouping.
    knowledge_area: Optional[str] = None
    # search_keywords fix (question-router-v3): [] for human_only (forced
    # server-side, same rule as research_focus -> None) and whenever the
    # model omitted the field. investigation_agent.investigate() tokenizes
    # and prepends these ahead of its own question/research_focus keywords.
    search_keywords: List[str] = field(default_factory=list)
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
  "research_focus": "what a read-only code investigation should look for, or null for human_only",
  "knowledge_area": "product_intent | domain_rule | operations | implementation | security | null",
  "search_keywords": ["a few ASCII code identifiers/paths, or [] for human_only"]
}

Rules:
- "category" must be exactly one of the three values above.
- "research_focus" must be null for "human_only" (there is nothing to \
investigate). For "system_researchable" and "hybrid" it should name the \
concrete thing to look for in the code (e.g. a symbol, a file area, a \
behavior) -- never a restatement of "look for the answer".
- "knowledge_area" classifies which single knowledge area the question \
belongs to, used only to group who should answer it -- never to hide it:
  - "product_intent": product goals, priorities, business tradeoffs
  - "domain_rule": business/domain rules the system must follow
  - "operations": how the system is run, deployed, monitored day to day
  - "implementation": how the code itself is built/structured
  - "security": security, auth, or access-control specific questions
  Return null when no single area clearly fits -- never guess.
- "search_keywords" must be [] for "human_only" (there is nothing to search \
for). For "system_researchable" and "hybrid", return up to 10 short, \
LIKELY-ASCII code search hints: identifiers, function/class/symbol names, or \
file/path fragments a keyword search over the repository's file paths could \
plausibly match -- never a restatement of the question. The developer's \
question may be written in a language other than English (e.g. Japanese) \
while the codebase's paths and identifiers are English, so translate the \
concept into what the code would actually be named. Example: a Japanese \
question about 認証 ("authentication") should suggest something like \
["auth", "login", "token", "session"], not Japanese words.
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

    # Issue #291: fail-closed on an out-of-set knowledge_area too (Principle
    # 6) -- null is valid (no area fits), an unknown string is not.
    if validated.knowledge_area is not None and validated.knowledge_area not in KNOWLEDGE_AREAS:
        return RouteResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Model returned an invalid knowledge_area: {validated.knowledge_area!r}",
        )

    research_focus = validated.research_focus if validated.category != "human_only" else None
    # search_keywords fix (question-router-v3): forced to [] for human_only,
    # the same rule as research_focus -> None above (nothing to search for).
    search_keywords = list(validated.search_keywords) if validated.category != "human_only" else []

    return RouteResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        category=validated.category,
        reason=validated.reason,
        research_focus=research_focus,
        knowledge_area=validated.knowledge_area,
        search_keywords=search_keywords,
    )
