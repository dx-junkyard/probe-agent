"""Runtime Reality Check (Issue #135).

Reconciles approved System Interview metadata/probe plans (role,
probe_value, state_effects, recommended_mode) against deterministic runtime
trace aggregates for the same ``component_id``, and asks the developer to
confirm or correct any surprising mismatch.

Two clearly separated steps (Principle 6):

- ``aggregate_component_facts`` — numeric-only aggregation over the
  ``traces`` table (call count, error rate, duration percentiles, last
  observed timestamp, and whether any traces exist at all). Deterministic,
  reproducible, System-scoped, never interpreted here.
- ``generate_runtime_reality_check`` — a reasoning-model call that is handed
  the facts plus the declared understanding and must choose, in structured
  output, which mismatches are worth a confirmation question. Fails closed
  on any configuration/provider/validation error; never falls back to a
  heuristic "error rate above N% => ask" rule (that would violate Principle
  6's ban on threshold heuristics deciding drift).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Shared JSON-response helpers (same fence-stripping / budget-trimming the
# dialogue passes use) and the shared positive-int env parser, instead of
# re-declaring local copies of either.
from .interview_agent import _strip_fences, _trim_json
from .interview_evidence import _positive_int_env
from .interview_language import language_directive
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .models import RuntimeRealityCheckItemOut, RuntimeTraceFactsOut

PROMPT_VERSION = "runtime-reality-v1"
SCHEMA_VERSION = "runtime-reality-v1"

DEFAULT_WINDOW_DAYS = 7
DEFAULT_MAX_CHARS = 8_000
MAX_RUNTIME_QUESTIONS = 5


def window_days() -> int:
    return _positive_int_env("RUNTIME_REALITY_CHECK_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)


def _max_chars() -> int:
    return _positive_int_env("RUNTIME_REALITY_CHECK_MAX_CHARS", DEFAULT_MAX_CHARS)


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = pct * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


def aggregate_component_facts(
    conn: sqlite3.Connection,
    system_id: int,
    component_id: str,
    *,
    window_days_value: Optional[int] = None,
) -> RuntimeTraceFactsOut:
    """Deterministic numeric aggregation of traces for one component_id.

    Scoped strictly by ``system_id`` — traces from other Systems never enter
    the aggregate (isolation). ``has_traces=False`` with null numeric fields
    is the "0 traces for an approved probe plan" signal from the issue; this
    function only reports the fact, it never decides whether that is
    surprising.
    """
    days = window_days_value if window_days_value is not None else window_days()
    since = time.time() - days * 86_400

    # Counts/max are aggregated in SQL so only duration values (needed for
    # the percentile math) cross the SQLite boundary, instead of every row.
    summary = conn.execute(
        """SELECT COUNT(*) AS call_count,
                  SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END)
                      AS error_count,
                  MAX(timestamp) AS last_observed_at
           FROM traces
           WHERE system_id = ? AND component_id = ? AND timestamp >= ?""",
        (system_id, component_id, since),
    ).fetchone()

    call_count = summary["call_count"]
    if call_count == 0:
        return RuntimeTraceFactsOut(
            component_id=component_id,
            window_days=days,
            call_count=0,
            error_count=0,
            error_rate=None,
            duration_p50_ms=None,
            duration_p90_ms=None,
            duration_p99_ms=None,
            last_observed_at=None,
            has_traces=False,
        )

    error_count = summary["error_count"] or 0
    durations = [
        float(r["duration_ms"])
        for r in conn.execute(
            """SELECT duration_ms FROM traces
               WHERE system_id = ? AND component_id = ? AND timestamp >= ?
                 AND duration_ms IS NOT NULL
               ORDER BY duration_ms""",
            (system_id, component_id, since),
        )
    ]

    return RuntimeTraceFactsOut(
        component_id=component_id,
        window_days=days,
        call_count=call_count,
        error_count=error_count,
        error_rate=error_count / call_count,
        duration_p50_ms=_percentile(durations, 0.50) if durations else None,
        duration_p90_ms=_percentile(durations, 0.90) if durations else None,
        duration_p99_ms=_percentile(durations, 0.99) if durations else None,
        last_observed_at=summary["last_observed_at"],
        has_traces=True,
    )


# --- Reasoning reconciliation step -------------------------------------------

# One approved element's declared understanding + facts. The API response
# model doubles as the reconciliation input so the two cannot drift; the
# old dataclass name is kept for callers/tests.
RuntimeCheckInputItem = RuntimeRealityCheckItemOut


def declared_block(item: RuntimeRealityCheckItemOut) -> Dict[str, Any]:
    """The declared-understanding projection of an item.

    Single source for both the reconciliation prompt payload and the
    persisted runtime_evidence provenance, so what is stored always matches
    what the model was shown.
    """
    return {
        "role": item.role,
        "probe_value": item.probe_value,
        "state_effects": item.state_effects,
        "recommended_mode": item.recommended_mode,
    }


@dataclass
class RuntimeCheckQuestion:
    component_id: str
    qualified_name: str
    question_text: str
    hypothesis: str
    answer_options: List[str] = field(default_factory=list)


@dataclass
class RuntimeRealityCheckResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    questions: List[RuntimeCheckQuestion] = field(default_factory=list)
    error: Optional[str] = None


class _RawRuntimeQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(..., min_length=1)
    qualified_name: str = Field(..., min_length=1)
    question_text: str = Field(..., min_length=1, max_length=2_000)
    hypothesis: str = Field(default="", max_length=4_000)
    answer_options: List[str] = Field(default_factory=list, max_length=4)


class _RawRuntimeRealityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: List[_RawRuntimeQuestion] = Field(
        default_factory=list, max_length=MAX_RUNTIME_QUESTIONS
    )


_SYSTEM_PROMPT = """\
You are the Runtime Reality Check step of probe-agent's system-understanding \
interview. You compare a developer's APPROVED understanding of software \
elements (role, probe_value, state_effects, recommended_mode) against \
DETERMINISTIC runtime trace aggregates for the exact same component_id \
(call_count, error_rate, duration percentiles, last_observed_at, has_traces), \
gathered over a recent time window.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "questions": [
    {
      "component_id": "module_function_name",
      "qualified_name": "module.function_name",
      "question_text": "...",
      "hypothesis": "...",
      "answer_options": ["..."]
    }
  ]
}

Rules:
- "questions" may contain at most %d entries. Only include an item when the \
runtime facts plausibly conflict with, or are surprising given, the declared \
understanding (e.g. a component declared side-effect-free with high \
error_rate, a component with state_effects: [none] but nonzero call volume, \
or an approved probe plan with has_traces: false).
- Never invent a component_id or qualified_name; only use ones that appear \
in the supplied list, exactly as given.
- Phrase each question as a confirm-or-correct question the developer can \
answer, referencing the concrete numbers as evidence in "hypothesis".
- You never decide, adopt, apply, or change any metadata, probe plan, or \
policy yourself. This step only proposes confirmation questions; a human \
answers them separately.
- If nothing is surprising, return an empty "questions" list. Do not invent \
a question just to have output.
- Do not use a fixed numeric threshold rule; judge each item on its own \
context (declared role/probe_value vs. observed facts).
""" % MAX_RUNTIME_QUESTIONS


def _system_prompt(language: str) -> str:
    return _SYSTEM_PROMPT + "\n" + language_directive(language) + "\n"


def _build_user_prompt(items: List[RuntimeCheckInputItem], max_chars: int) -> str:
    payload = [
        {
            "component_id": item.component_id,
            "qualified_name": item.qualified_name,
            "path": item.path,
            "declared": declared_block(item),
            "facts": item.facts.model_dump(),
        }
        for item in items
    ]
    return (
        "Approved elements with their declared understanding and runtime "
        "trace facts:\n" + _trim_json(payload, max_chars)
    )


def generate_runtime_reality_check(
    client: LLMClient,
    config: LLMConfig,
    *,
    items: List[RuntimeCheckInputItem],
    language: str,
) -> RuntimeRealityCheckResult:
    """Reasoning-model reconciliation over approved metadata vs trace facts.

    Fail-closed: mock/non-reasoning models, LLM errors, malformed structured
    output, and references to a component_id/qualified_name not in ``items``
    all return a result with ``error`` set and no questions — never a
    heuristic substitute (Principle 6).
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return RuntimeRealityCheckResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Runtime Reality Check requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    try:
        max_chars = _max_chars()
    except ValueError as exc:
        return RuntimeRealityCheckResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc)
        )

    prompt = _build_user_prompt(items, max_chars)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _system_prompt(language)},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
    except LLMError as exc:
        return RuntimeRealityCheckResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc)
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawRuntimeRealityResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return RuntimeRealityCheckResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    known = {(i.component_id, i.qualified_name) for i in items}
    questions: List[RuntimeCheckQuestion] = []
    for q in validated.questions:
        if (q.component_id, q.qualified_name) not in known:
            return RuntimeRealityCheckResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=(
                    f"Question referenced unknown component "
                    f"'{q.component_id}'/'{q.qualified_name}' not in the supplied set"
                ),
            )
        questions.append(
            RuntimeCheckQuestion(
                component_id=q.component_id,
                qualified_name=q.qualified_name,
                question_text=q.question_text,
                hypothesis=q.hypothesis,
                answer_options=q.answer_options,
            )
        )

    return RuntimeRealityCheckResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        questions=questions,
    )
