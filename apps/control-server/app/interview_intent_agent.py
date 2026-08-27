"""Intent Brief AI proposal generation (Issue #284).

Separates user intent (only the user can decide) from implementation-fact
understanding. Given the interview session's conversation history and free
-text ``user_intent``, this module proposes candidate values for whichever of
the six Intent Brief fields (goal / pain / success_criteria / priority /
constraints / non_goals) are not already present, grounded in what the user
actually said.

Follows interview_agent.py's fail-closed structured-turn pattern: a mock
client or a non-reasoning model, an API failure, or a structured-output
validation failure all return an error result and propose nothing — never a
heuristic fallback guess (Principle 6). Every returned item is a *proposal*;
callers persist it with ``status='proposed'`` / ``origin='ai_proposed'`` /
``decision_method='reasoning_llm'``, and it only becomes ``confirmed``
through a human's explicit confirm/correct call (Principle 2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "intent-brief-v1"
SCHEMA_VERSION = "intent-brief-v1"

INTENT_FIELDS = (
    "goal", "pain", "success_criteria", "priority", "constraints", "non_goals"
)

MAX_RECENT_MESSAGES = 20


# --- Raw response schema (what we require the model to return) --------------


class _RawIntentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    value_text: str = Field(..., min_length=1, max_length=4_000)
    source_statement: Optional[str] = Field(default=None, max_length=4_000)


class _RawIntentProposalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[_RawIntentItem] = Field(default_factory=list, max_length=6)


# --- Public result types ------------------------------------------------------


@dataclass
class IntentProposalItem:
    field: str
    value_text: str
    source_statement: Optional[str] = None


@dataclass
class IntentProposalResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    items: List[IntentProposalItem] = field(default_factory=list)
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
You are analyzing a system-understanding interview conversation to propose a
structured Intent Brief. Only the user can decide intent -- you propose
candidate values grounded in what the user actually said or clearly implied.
Never invent a goal, priority, or constraint the user did not express.

Respond with a single JSON object and nothing else (no markdown fences, no
commentary), matching exactly this shape:

{
  "items": [
    {
      "field": "goal | pain | success_criteria | priority | constraints | non_goals",
      "value_text": "...",
      "source_statement": "the user's own statement this was derived from, or null"
    }
  ]
}

Rules:
- Only propose a field listed in "missing_fields" below. Never repeat a
  field already listed in "existing_fields".
- Propose at most one item per missing field.
- If nothing in the conversation grounds a candidate value for a missing
  field, omit that field entirely -- do not guess.
- "現状把握だけが目的です" (the goal is only to understand the current
  state; no solution decided yet) is a valid, complete goal value on its
  own -- propose it verbatim as the goal when that is what the user
  expressed, rather than omitting the field.
- If nothing can be grounded at all, return {"items": []}.
"""


def _build_user_prompt(
    history: List[Dict[str, str]],
    user_intent: Optional[str],
    existing_fields: List[str],
    missing_fields: List[str],
) -> str:
    recent = history[-MAX_RECENT_MESSAGES:]
    convo = "\n".join(f"{m.get('role', '?')}: {m.get('content', '')}" for m in recent)
    parts = [
        f"missing_fields: {json.dumps(missing_fields)}",
        f"existing_fields: {json.dumps(existing_fields)}",
        f"user_intent (free text, may be empty): {user_intent or '(none)'}",
        "conversation:",
        convo or "(no messages yet)",
    ]
    return "\n\n".join(parts)


def generate_intent_proposal(
    client: LLMClient,
    config: LLMConfig,
    *,
    history: List[Dict[str, str]],
    user_intent: Optional[str],
    existing_fields: List[str],
) -> IntentProposalResult:
    """Propose Intent Brief items for fields not already present.

    Fail-closed: if the client is mock, the model is not a reasoning model,
    the API call fails, or the structured output is invalid, the result
    carries an error and no items -- callers must not persist anything.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return IntentProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Intent proposal requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    missing_fields = [f for f in INTENT_FIELDS if f not in existing_fields]
    if not missing_fields:
        return IntentProposalResult(
            provider=config.provider, model=config.model, is_mock=False, items=[],
        )

    prompt = _build_user_prompt(history, user_intent, existing_fields, missing_fields)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2048,
        )
    except LLMError as exc:
        return IntentProposalResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawIntentProposalResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return IntentProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    items: List[IntentProposalItem] = []
    seen_fields = set(existing_fields)
    for raw_item in validated.items:
        if raw_item.field not in INTENT_FIELDS:
            return IntentProposalResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=f"Model proposed an invalid field: {raw_item.field!r}",
            )
        if raw_item.field in seen_fields:
            # Defensive: the prompt already excludes existing/duplicate
            # fields, but a model deviation must never overwrite/duplicate a
            # field silently (Principle 6 -- deterministic dedupe, not a
            # heuristic merge).
            continue
        seen_fields.add(raw_item.field)
        items.append(
            IntentProposalItem(
                field=raw_item.field,
                value_text=raw_item.value_text,
                source_statement=raw_item.source_statement,
            )
        )

    return IntentProposalResult(
        provider=config.provider, model=config.model, is_mock=False, items=items,
    )
