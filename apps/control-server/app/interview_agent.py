"""System-understanding interview dialogue agent (Issue #69).

Generates structured assistant turns for the system-understanding interview,
producing combined per-symbol proposals (docstring metadata + probe plan)
grounded in the #68 context pack.  Follows the workspace_agent.py pattern:
structured JSON response, no heuristic fallback, proposals only marked
``proposed``.

The safety denylist from ``probe_planner.py`` deterministically overrides
any probe suggestion from the model — denylisted symbols are excluded from
proposals even if the model proposes them.

This module never calls a provider SDK directly; it only uses the
provider-neutral ``llm.py`` adapter.
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
SCHEMA_VERSION = "v1"

MAX_RECENT_MESSAGES = 20


# --- Raw response schema (what we require the model to return) ---------------


class _RawInterviewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    qualified_name: str = Field(..., min_length=1)
    symbol_id: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    probe_plan: Dict[str, Any] = Field(default_factory=dict)


class _RawInterviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_message: str = Field(..., min_length=1, max_length=20_000)
    findings: List[str] = Field(default_factory=list, max_length=20)
    proposals: List[_RawInterviewProposal] = Field(
        default_factory=list, max_length=50
    )
    next_questions: List[str] = Field(default_factory=list, max_length=10)


# --- Validated result --------------------------------------------------------


@dataclass
class InterviewProposalResult:
    path: str
    qualified_name: str
    symbol_id: Optional[int]
    metadata: InterviewProposalMetadataBlock
    probe_plan: InterviewProposalProbePlan


@dataclass
class InterviewTurnResult:
    provider: str
    model: str
    is_mock: bool
    assistant_message: str = ""
    findings: List[str] = field(default_factory=list)
    proposals: List[InterviewProposalResult] = field(default_factory=list)
    denied_symbols: List[str] = field(default_factory=list)
    next_questions: List[str] = field(default_factory=list)
    error: Optional[str] = None


_SYSTEM_PROMPT = """\
You are a system-understanding interview assistant for probe-agent. Your job is
to help a developer understand and document their codebase by examining symbols
and entrypoints from a pinned repository snapshot.

You produce structured JSON output that proposes `probe-agent:` docstring
metadata blocks and associated probe plans for discussed code symbols.

Respond with a single JSON object and nothing else (no markdown fences,
no commentary), matching exactly this shape:

{
  "assistant_message": "Your conversational reply to the developer...",
  "findings": ["Observation about the codebase grounded in the context pack..."],
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
        "element_type": "system|core|capability|element|supporting|boundary",
        "operation_kind": "analysis|read|write|mutation|io|orchestration|validation|other",
        "consumers": ["..."],
        "state_effects": ["none|database-read|database-write|network|filesystem|cache|external-api|queue"]
      },
      "probe_plan": {
        "feature_id": "...",
        "objective": "...",
        "reason": "...",
        "recommended_mode": "trace|shadow",
        "side_effect_risk": "none|low|medium|high",
        "replayability": "safe|caution|unsafe"
      }
    }
  ],
  "next_questions": ["Follow-up question for the developer..."]
}

Rules:
- proposals[].path and qualified_name must reference symbols present in the
  context pack. Do not invent symbols.
- metadata.element_type must be one of: system, core, capability, element,
  supporting, boundary.
- metadata.operation_kind must be one of: analysis, read, write, mutation, io,
  orchestration, validation, other.
- metadata.state_effects entries must each be one of: none, database-read,
  database-write, network, filesystem, cache, external-api, queue.
- probe_plan.recommended_mode must be one of: trace, shadow.
- probe_plan.side_effect_risk must be one of: none, low, medium, high.
- probe_plan.replayability must be one of: safe, caution, unsafe.
- You never decide, adopt, or execute anything. Proposals are always reviewed
  by a human; do not claim a proposal has been accepted.
- Focus on unclassified symbols first -- they are the blank-page regions most
  in need of documentation and instrumentation planning.
- If the context pack lacks enough information to classify a symbol confidently,
  say so in next_questions instead of guessing.
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
        "## Context Pack (snapshot-grounded symbols and entrypoints)",
        context_pack.model_dump_json(),
    ]
    if recent_history:
        parts.append("## Recent conversation history")
        for msg in recent_history:
            parts.append(f"{msg['role']}: {msg['content']}")
    parts.append("## Latest user message")
    parts.append(user_message)
    return "\n\n".join(parts)


def _build_mock_proposals(
    context_pack: InterviewContextPack,
) -> List[InterviewProposalResult]:
    """Build deterministic mock proposals from unclassified symbols."""
    proposals: List[InterviewProposalResult] = []
    for sym in context_pack.symbols:
        if sym.classification == "unclassified":
            denylist_reason = check_denylist(sym.qualified_name)
            if denylist_reason:
                continue
            proposals.append(
                InterviewProposalResult(
                    path=sym.path,
                    qualified_name=sym.qualified_name,
                    symbol_id=sym.symbol_id,
                    metadata=InterviewProposalMetadataBlock(
                        role=f"Mock role for {sym.qualified_name}",
                        capability="mock-capability",
                        element_type="element",
                        operation_kind="analysis",
                        state_effects=["none"],
                    ),
                    probe_plan=InterviewProposalProbePlan(
                        objective=f"Trace {sym.qualified_name} inputs and outputs",
                        reason=f"Mock probe plan for {sym.qualified_name}",
                        recommended_mode="trace",
                        side_effect_risk="low",
                        replayability="safe",
                    ),
                )
            )
    return proposals


def generate_interview_turn(
    client: LLMClient,
    config: LLMConfig,
    *,
    context_pack: InterviewContextPack,
    history: List[Dict[str, str]],
    user_message: str,
) -> InterviewTurnResult:
    """Generate a single structured interview turn.

    Mock clients produce deterministic mock proposals (marked ``is_mock``).
    Non-reasoning models are rejected. Real reasoning failures fail closed.
    Denylisted symbols are excluded from proposals.
    """
    is_mock = isinstance(client, MockLLMClient)

    if is_mock:
        proposals = _build_mock_proposals(context_pack)
        denied = [
            f"{sym.qualified_name}: {check_denylist(sym.qualified_name)}"
            for sym in context_pack.symbols
            if sym.classification == "unclassified"
            and check_denylist(sym.qualified_name)
        ]
        return InterviewTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=True,
            assistant_message=(
                "Based on the snapshot analysis, I've generated mock proposals "
                "for the unclassified symbols. [mock response]"
            ),
            findings=["Mock finding: snapshot contains unclassified symbols"],
            proposals=proposals,
            denied_symbols=denied,
            next_questions=[],
        )

    if not is_reasoning_model(config.provider, config.model):
        return InterviewTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=(
                "Interview dialogue requires a configured reasoning model; "
                "non-reasoning models are not permitted"
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
            max_tokens=4096,
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

    proposals: List[InterviewProposalResult] = []
    denied_symbols: List[str] = []

    for raw_proposal in validated.proposals:
        try:
            metadata = InterviewProposalMetadataBlock.model_validate(
                raw_proposal.metadata
            )
        except ValidationError as exc:
            return InterviewTurnResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=(
                    f"Proposal for {raw_proposal.qualified_name} "
                    f"has invalid metadata: {exc}"
                ),
            )

        try:
            probe_plan = InterviewProposalProbePlan.model_validate(
                raw_proposal.probe_plan
            )
        except ValidationError as exc:
            return InterviewTurnResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=(
                    f"Proposal for {raw_proposal.qualified_name} "
                    f"has invalid probe_plan: {exc}"
                ),
            )

        denylist_reason = check_denylist(raw_proposal.qualified_name)
        if denylist_reason:
            denied_symbols.append(
                f"{raw_proposal.qualified_name}: {denylist_reason}"
            )
            continue

        proposals.append(
            InterviewProposalResult(
                path=raw_proposal.path,
                qualified_name=raw_proposal.qualified_name,
                symbol_id=raw_proposal.symbol_id,
                metadata=metadata,
                probe_plan=probe_plan,
            )
        )

    return InterviewTurnResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        assistant_message=validated.assistant_message,
        findings=validated.findings,
        proposals=proposals,
        denied_symbols=denied_symbols,
        next_questions=validated.next_questions,
    )
