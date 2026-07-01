"""System-understanding interview reasoning dialogue (Issue #69).

Generates structured assistant turns for the system-understanding interview,
grounded in #68's snapshot context pack. On request, produces per-symbol
combined proposals (docstring metadata + probe plan), validated against
#54's vocabulary and #25's probe-plan fields, with the safety denylist
from probe_planner.py overriding any probe suggestion.

Reuses the workspace_agent structured-turn pattern: JSON turns, no heuristic
fallback, proposals only marked ``proposed``. Adds a new combined
``docstring_metadata_probe_plan`` proposal type for the interview flow.

This module never calls a provider SDK directly; it only uses the
provider-neutral ``llm.py`` adapter. Mock/non-reasoning models fail closed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .models import (
    InterviewContextPack,
    InterviewProposalMetadataBlock,
    InterviewProposalProbePlan,
)
from .probe_planner import check_denylist

PROMPT_VERSION = "interview-v1"
SCHEMA_VERSION = "interview-v1"

MAX_RECENT_MESSAGES = 20


# --- Raw response schema (what we require the model to return) ---------------


class _RawProposalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Optional[str] = None
    capability: Optional[str] = None
    system_purpose: Optional[str] = None
    probe_value: Optional[str] = None
    element_type: Optional[str] = None
    operation_kind: Optional[str] = None
    consumers: List[str] = Field(default_factory=list)
    state_effects: List[str] = Field(default_factory=list)


class _RawProposalProbePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: str = ""
    objective: str = ""
    reason: str = ""
    recommended_mode: str = "trace"
    side_effect_risk: str = "low"
    replayability: str = "safe"


class _RawCombinedProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    qualified_name: str
    symbol_id: Optional[int] = None
    metadata: _RawProposalMetadata
    probe_plan: _RawProposalProbePlan


class _RawInterviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_message: str = Field(..., min_length=1, max_length=20_000)
    proposals: List[_RawCombinedProposal] = Field(default_factory=list, max_length=50)
    next_questions: List[str] = Field(default_factory=list, max_length=10)


# --- Validated result --------------------------------------------------------


@dataclass
class InterviewProposalResult:
    path: str
    qualified_name: str
    symbol_id: Optional[int]
    metadata: InterviewProposalMetadataBlock
    probe_plan: InterviewProposalProbePlan
    denylist_hit: Optional[str] = None


@dataclass
class InterviewTurnResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    assistant_message: str = ""
    proposals: List[InterviewProposalResult] = field(default_factory=list)
    next_questions: List[str] = field(default_factory=list)
    error: Optional[str] = None


_SYSTEM_PROMPT = """\
You are a system-understanding interview assistant for probe-agent.
You help developers understand their system's purpose, core capabilities,
and elements by discussing code symbols and entrypoints from a pinned
repository snapshot.

Respond with a single JSON object and nothing else (no markdown fences,
no commentary), matching exactly this shape:

{
  "assistant_message": "...",
  "proposals": [
    {
      "path": "src/module.py",
      "qualified_name": "module.function_name",
      "symbol_id": 42,
      "metadata": {
        "role": "...",
        "capability": "...",
        "system_purpose": "...",
        "probe_value": "...",
        "element_type": "system"|"core"|"capability"|"element"|"supporting"|"boundary",
        "operation_kind": "analysis"|"read"|"write"|"mutation"|"io"|"orchestration"|"validation"|"other",
        "consumers": ["..."],
        "state_effects": ["none"|"database-read"|"database-write"|"network"|"filesystem"|"cache"|"external-api"|"queue"]
      },
      "probe_plan": {
        "feature_id": "...",
        "objective": "...",
        "reason": "...",
        "recommended_mode": "trace"|"shadow",
        "side_effect_risk": "none"|"low"|"medium"|"high",
        "replayability": "safe"|"caution"|"unsafe"
      }
    }
  ],
  "next_questions": ["..."]
}

Rules:
- Only reference symbols and paths that appear in the supplied context pack.
- "proposals" should be empty unless the user asks you to generate proposals.
  When asked, produce one combined proposal per discussed symbol.
- Each proposal's "metadata" must use only the enum values shown above for
  element_type, operation_kind, and state_effects. Unknown values are rejected.
- Each proposal's "probe_plan" must use only the enum values shown above.
- You never decide, adopt, or execute anything. Proposals are always
  reviewed by a human; do not claim a proposal has been accepted or run.
- If you lack information to classify a symbol, say so in your message
  and ask a clarifying question in "next_questions".
- Do not invent symbols not present in the context pack.
"""


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _build_user_prompt(
    context_pack: InterviewContextPack,
    history: List[Dict[str, str]],
    user_message: str,
) -> str:
    recent_history = history[-MAX_RECENT_MESSAGES:]
    parts = [
        "## Context Pack (snapshot-grounded symbols and entrypoints; the only allowed reference source)",
        context_pack.model_dump_json(),
    ]
    if recent_history:
        parts.append("## Recent conversation history")
        for msg in recent_history:
            parts.append(f"{msg['role']}: {msg['content']}")
    parts.append("## Latest user message")
    parts.append(user_message)
    return "\n\n".join(parts)


def _apply_denylist(proposal: InterviewProposalResult) -> InterviewProposalResult:
    """Apply the deterministic safety denylist from probe_planner.py.

    If a symbol is denylisted, the probe plan is excluded (fields zeroed)
    and side_effect_risk is overridden to "high".
    """
    hit = check_denylist(proposal.qualified_name)
    if hit:
        proposal.denylist_hit = hit
        proposal.probe_plan = InterviewProposalProbePlan(
            feature_id=proposal.probe_plan.feature_id,
            objective=proposal.probe_plan.objective,
            reason=f"EXCLUDED by safety denylist: {hit}",
            recommended_mode="trace",
            side_effect_risk="high",
            replayability="unsafe",
        )
    return proposal


def generate_interview_turn(
    client: LLMClient,
    config: LLMConfig,
    *,
    context_pack: InterviewContextPack,
    history: List[Dict[str, str]],
    user_message: str,
) -> InterviewTurnResult:
    """Generate one structured assistant turn for the interview dialogue.

    Fail-closed: if the client is mock, the model is not a reasoning model,
    the API call fails, or validation fails, the result carries an error and
    no proposals are stored.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return InterviewTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Interview dialogue requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    prompt = _build_user_prompt(context_pack, history, user_message)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=8192,
        )
    except LLMError as exc:
        return InterviewTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawInterviewResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return InterviewTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    # Validate each proposal against #54 vocabulary and #25 probe-plan fields.
    proposals: List[InterviewProposalResult] = []
    for raw_proposal in validated.proposals:
        try:
            metadata = InterviewProposalMetadataBlock.model_validate(
                raw_proposal.metadata.model_dump()
            )
        except ValidationError as exc:
            return InterviewTurnResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=(
                    f"Proposal for '{raw_proposal.qualified_name}' has invalid "
                    f"metadata: {exc}"
                ),
            )

        try:
            probe_plan = InterviewProposalProbePlan.model_validate(
                raw_proposal.probe_plan.model_dump()
            )
        except ValidationError as exc:
            return InterviewTurnResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=(
                    f"Proposal for '{raw_proposal.qualified_name}' has invalid "
                    f"probe_plan: {exc}"
                ),
            )

        result = InterviewProposalResult(
            path=raw_proposal.path,
            qualified_name=raw_proposal.qualified_name,
            symbol_id=raw_proposal.symbol_id,
            metadata=metadata,
            probe_plan=probe_plan,
        )
        result = _apply_denylist(result)
        proposals.append(result)

    return InterviewTurnResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        assistant_message=validated.assistant_message,
        proposals=proposals,
        next_questions=validated.next_questions,
    )
