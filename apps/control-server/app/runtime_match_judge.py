"""Runtime Match Judge (Issue #290 Finding 5, Part 2).

Deterministic checks (``app/runtime_alignment.py``'s ``compare_claim_to_runtime``)
can only tell whether runtime facts exist and are fresh, plus the one
structural environment-equality signal -- they never read the alignment
item's free-text ``claim``. That means a fresh trace whose CONTENT actually
contradicts the claim always fell through to 'match', so the
``runtime_check == 'mismatch' -> must_review`` rule
(``app/alignment.py``'s ``_RULES``) was unreachable for a semantic
contradiction, only for the (rare, structural) environment-mismatch case.

This module adds the missing reasoning step, strictly scoped per Principle 6:

- Eligibility is decided deterministically by the caller
  (``routes/interview_alignment.py::run_alignment_build``), never here: only
  items whose deterministic baseline is already 'match' (fresh data, no
  environment conflict) are ever handed to ``judge_runtime_match``. The
  stale/unobserved/environment-mismatch guards stay absolute and are never
  second-guessed by the model.
- One batched LLM call judges every eligible item's claim against its
  runtime facts and returns a strict per-item match/mismatch verdict.
- Fail-closed: a mock/non-reasoning model, an LLM error, malformed
  structured output, or a response with an unknown/duplicate/missing index
  or an out-of-set ``runtime_check`` all invalidate the WHOLE response
  (never a partial best-effort result) -- the caller persists
  ``runtime_check = NULL`` for the affected items instead of guessing or
  falling back to the deterministic 'match' baseline.

probe-agent:
  role: Reasoning-model semantic match/mismatch judge for alignment items
    whose deterministic runtime baseline is 'match'
  capability: interactive-system-understanding
  element_type: element
  consumers: [interview-alignment-routes]
  operation_kind: analysis
  state_effects: [external-api]
  probe_value: Verify judge_runtime_match fails the whole batch closed on any out-of-set runtime_check or unknown/duplicate/missing index, and that a mock/non-reasoning client is rejected before any request is sent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .interview_agent import _strip_fences
from .interview_language import language_directive
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "runtime-match-v1"
SCHEMA_VERSION = "runtime-match-v1"

RUNTIME_MATCH_VALUES = ("match", "mismatch")


@dataclass
class RuntimeMatchJudgeInputItem:
    """One eligible alignment item's claim + runtime facts to judge.

    ``index`` is caller-assigned and only used to correlate the model's
    response back to the caller's item list; it carries no other meaning.
    """

    index: int
    claim: str
    component_id: str
    call_count: int
    error_rate: Optional[float]
    duration_p50_ms: Optional[float]
    duration_p90_ms: Optional[float]
    duration_p99_ms: Optional[float]
    freshness: str
    environment: Optional[str]


@dataclass
class RuntimeMatchJudgeItemResult:
    index: int
    runtime_check: str  # "match" | "mismatch"
    note: str = ""


@dataclass
class RuntimeMatchJudgeResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    items: List[RuntimeMatchJudgeItemResult] = field(default_factory=list)
    error: Optional[str] = None


class _RawRuntimeMatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(..., ge=0)
    runtime_check: str = Field(..., min_length=1, max_length=20)
    note: str = Field(default="", max_length=1_000)


class _RawRuntimeMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[_RawRuntimeMatchItem] = Field(default_factory=list, max_length=100)


_SYSTEM_PROMPT = """\
You are the Runtime Match Judge step of probe-agent's Alignment Review \
(Issue #290). For each item you are given a natural-language CLAIM about \
current system behavior, plus DETERMINISTIC runtime trace facts for the \
exact same component_id (call_count, error_rate, duration percentiles, \
freshness, environment). Every item shown to you has ALREADY passed a \
deterministic freshness/structural check (freshness == 'fresh', no \
environment conflict) — your ONLY job is to judge whether the claim's \
MEANING is still consistent with what the runtime facts show.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "items": [
    {"index": 0, "runtime_check": "match" | "mismatch", "note": "short reason"}
  ]
}

Rules:
- Return exactly one entry per input item, using its given "index" verbatim.
  Never invent, omit, or duplicate an index.
- "runtime_check" must be exactly "match" (the claim is still consistent \
with the observed runtime facts) or "mismatch" (the claim contradicts what \
the runtime facts show). No other value is accepted.
- "note" is a short (one sentence) reason citing the concrete facts that \
led to your judgement.
- You never decide, adopt, apply, or change any metadata, probe plan, or \
policy yourself. This step only labels match/mismatch; a human reviews the \
result through the normal Alignment Review flow.
"""


def _system_prompt(language: str) -> str:
    return _SYSTEM_PROMPT + "\n" + language_directive(language) + "\n"


def _build_user_prompt(items: List[RuntimeMatchJudgeInputItem]) -> str:
    payload = [
        {
            "index": it.index,
            "claim": it.claim,
            "component_id": it.component_id,
            "call_count": it.call_count,
            "error_rate": it.error_rate,
            "duration_p50_ms": it.duration_p50_ms,
            "duration_p90_ms": it.duration_p90_ms,
            "duration_p99_ms": it.duration_p99_ms,
            "freshness": it.freshness,
            "environment": it.environment,
        }
        for it in items
    ]
    return "Items to judge (claim vs. runtime facts):\n" + json.dumps(payload, ensure_ascii=False)


def judge_runtime_match(
    client: LLMClient,
    config: LLMConfig,
    items: List[RuntimeMatchJudgeInputItem],
    *,
    language: str,
) -> RuntimeMatchJudgeResult:
    """Semantic match/mismatch judge for a batch of eligible alignment items.

    Fail-closed (Principle 6): a mock client, a non-reasoning model, an API
    failure, a structured-output validation failure, or a response with any
    unknown/duplicate/missing index or an out-of-set ``runtime_check`` all
    return a result with ``error`` set and no items -- the caller must not
    persist a partial/best-effort batch, and must never fall back to the
    deterministic 'match' baseline these items were pre-filtered on.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return RuntimeMatchJudgeResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Runtime match judge requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    if not items:
        return RuntimeMatchJudgeResult(provider=config.provider, model=config.model, is_mock=False)

    prompt = _build_user_prompt(items)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _system_prompt(language)},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=4096,
        )
    except LLMError as exc:
        return RuntimeMatchJudgeResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawRuntimeMatchResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return RuntimeMatchJudgeResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    known_indices = {it.index for it in items}
    seen: set = set()
    results: List[RuntimeMatchJudgeItemResult] = []
    for raw_item in validated.items:
        if raw_item.index not in known_indices or raw_item.index in seen:
            return RuntimeMatchJudgeResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=f"Response referenced an unknown or duplicate index: {raw_item.index}",
            )
        if raw_item.runtime_check not in RUNTIME_MATCH_VALUES:
            return RuntimeMatchJudgeResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=f"Model returned an invalid runtime_check: {raw_item.runtime_check!r}",
            )
        seen.add(raw_item.index)
        results.append(
            RuntimeMatchJudgeItemResult(
                index=raw_item.index, runtime_check=raw_item.runtime_check, note=raw_item.note,
            )
        )

    if seen != known_indices:
        missing = sorted(known_indices - seen)
        return RuntimeMatchJudgeResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Response is missing a determination for item index(es): {missing}",
        )

    return RuntimeMatchJudgeResult(
        provider=config.provider, model=config.model, is_mock=False, items=results,
    )
