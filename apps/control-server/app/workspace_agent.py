"""Decision Workspace structured LLM dialogue (Issue #36/#37).

Generates a single assistant turn as structured JSON: grounded findings
(traceable to the Context Pack's evidence), assumptions, missing
information, proposals, and follow-up questions -- never free-form prose
that mixes facts and guesses.

This module never calls a provider SDK directly; it only uses the
provider-neutral `llm.py` adapter. It never accepts a mock/non-reasoning
model as a substitute for real inference (no heuristic fallback), and it
never marks a proposal as anything other than `proposed`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .models import WorkspaceContextPack

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"

MAX_RECENT_MESSAGES = 20


# --- Allowed proposal types and their body schemas --------------------------
#
# Unknown proposal types or schema-invalid bodies are rejected outright; the
# assistant turn fails closed rather than storing a malformed proposal.


class ProposalContextRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1, max_length=100)
    id: str = Field(..., min_length=1, max_length=200)


class ProposalEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(..., min_length=1, max_length=100)
    source_id: str = Field(..., min_length=1, max_length=200)
    snapshot_id: Optional[int] = Field(default=None, ge=1)
    path: Optional[str] = Field(default=None, max_length=1000)
    start_line: Optional[int] = Field(default=None, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)


class ExperimentDraftProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: str = Field(..., min_length=1, max_length=200)
    objective: str = Field(..., min_length=1, max_length=2000)
    variant_summaries: List[str] = Field(default_factory=list, max_length=10)
    snapshot_id: Optional[int] = Field(default=None, ge=1)
    constraints: List[str] = Field(default_factory=list, max_length=20)
    evaluation_criteria: List[str] = Field(default_factory=list, max_length=20)
    context_refs: List[ProposalContextRef] = Field(default_factory=list, max_length=20)
    evidence_refs: List[ProposalEvidenceRef] = Field(default_factory=list, max_length=20)


class ProbePlanDraftProposalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    focus: Optional[str] = Field(default=None, min_length=1, max_length=500)
    objective: str = Field(..., min_length=1, max_length=2000)
    target_components: List[str] = Field(default_factory=list, max_length=10)
    constraints: List[str] = Field(default_factory=list, max_length=20)
    observation_points: List[str] = Field(default_factory=list, max_length=20)
    evaluation_criteria: List[str] = Field(default_factory=list, max_length=20)
    context_refs: List[ProposalContextRef] = Field(default_factory=list, max_length=20)
    evidence_refs: List[ProposalEvidenceRef] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_feature_or_focus(self):
        if not self.feature_id and not self.focus:
            raise ValueError("feature_id or focus is required")
        return self


PROPOSAL_BODY_MODELS: Dict[str, Type[BaseModel]] = {
    "experiment_draft": ExperimentDraftProposalBody,
    "probe_plan_draft": ProbePlanDraftProposalBody,
}


# --- Raw response schema (what we require the model to return) -------------


class _RawGroundedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(..., min_length=1, max_length=2000)
    source_type: str = Field(..., min_length=1, max_length=100)
    source_id: str = Field(..., min_length=1, max_length=200)


class _RawProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1, max_length=100)
    title: str = Field(default="", max_length=300)
    body: Dict[str, Any] = Field(default_factory=dict)


class _RawAgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_message: str = Field(..., min_length=1, max_length=20_000)
    grounded_findings: List[_RawGroundedFinding] = Field(default_factory=list, max_length=20)
    assumptions: List[str] = Field(default_factory=list, max_length=20)
    missing_information: List[str] = Field(default_factory=list, max_length=20)
    proposals: List[_RawProposal] = Field(default_factory=list, max_length=10)
    next_questions: List[str] = Field(default_factory=list, max_length=10)


# --- Validated result --------------------------------------------------------


@dataclass
class GroundedFindingResult:
    claim: str
    source_type: str
    source_id: str


@dataclass
class ProposalResult:
    proposal_type: str
    title: str
    body: Dict[str, Any]


@dataclass
class AgentTurnResult:
    provider: str
    model: str
    is_mock: bool
    assistant_message: str = ""
    grounded_findings: List[GroundedFindingResult] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    missing_information: List[str] = field(default_factory=list)
    proposals: List[ProposalResult] = field(default_factory=list)
    next_questions: List[str] = field(default_factory=list)
    error: Optional[str] = None


_SYSTEM_PROMPT = """\
You are a Decision Workspace assistant for software evolution decisions.
You separate verified facts from guesses and never invent evidence.

Respond with a single JSON object and nothing else (no markdown fences,
no commentary), matching exactly this shape:

{
  "assistant_message": "...",
  "grounded_findings": [
    {"claim": "...", "source_type": "...", "source_id": "..."}
  ],
  "assumptions": ["..."],
  "missing_information": ["..."],
  "proposals": [
    {"type": "experiment_draft" | "probe_plan_draft", "title": "...", "body": {}}
  ],
  "next_questions": ["..."]
}

Rules:
- Every entry in grounded_findings must cite a (source_type, source_id) pair
  that appears verbatim in the "evidence" list of the supplied context pack.
  If you cannot cite real evidence for a claim, put it in "assumptions" or
  "missing_information" instead -- never present it as grounded.
- "proposals[].type" must be one of: experiment_draft, probe_plan_draft.
  - experiment_draft body: {"feature_id": str, "objective": str, "variant_summaries": [str],
    "snapshot_id": int|null, "constraints": [str], "evaluation_criteria": [str],
    "context_refs": [object], "evidence_refs": [object]}
  - probe_plan_draft body: {"feature_id": str|null, "focus": str|null, "objective": str,
    "target_components": [str], "constraints": [str], "observation_points": [str],
    "evaluation_criteria": [str], "context_refs": [object], "evidence_refs": [object]}
- You never decide, adopt, or execute anything. Proposals are always reviewed
  by a human; do not claim a proposal has been accepted or run.
- If the context pack lacks the information needed to answer confidently,
  say so in "missing_information" and ask a clarifying question in
  "next_questions" instead of guessing.
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
    context_pack: WorkspaceContextPack,
    workspace_summary: str,
    history: List[Dict[str, str]],
    user_message: str,
) -> str:
    recent_history = history[-MAX_RECENT_MESSAGES:]
    parts = [
        "## Context Pack (deterministic, no-LLM digest; the only allowed evidence source)",
        context_pack.model_dump_json(),
    ]
    if workspace_summary.strip():
        parts.append("## Previously decided / unresolved summary")
        parts.append(workspace_summary.strip())
    if recent_history:
        parts.append("## Recent conversation history")
        for msg in recent_history:
            parts.append(f"{msg['role']}: {msg['content']}")
    parts.append("## Latest user message")
    parts.append(user_message)
    return "\n\n".join(parts)


def generate_agent_turn(
    client: LLMClient,
    config: LLMConfig,
    *,
    context_pack: WorkspaceContextPack,
    workspace_summary: str,
    history: List[Dict[str, str]],
    user_message: str,
) -> AgentTurnResult:
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return AgentTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Decision Workspace dialogue requires a configured reasoning "
                "model; mock/heuristic fallback is prohibited"
            ),
        )

    prompt = _build_user_prompt(context_pack, workspace_summary, history, user_message)

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
        return AgentTurnResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc)
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawAgentResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return AgentTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    evidence_index = {
        (ref.source_type, ref.source_id) for ref in context_pack.evidence
    }
    grounded_findings: List[GroundedFindingResult] = []
    for finding in validated.grounded_findings:
        if (finding.source_type, finding.source_id) not in evidence_index:
            return AgentTurnResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=(
                    "grounded finding references a source not present in the "
                    f"context pack evidence: {finding.source_type}:{finding.source_id}"
                ),
            )
        grounded_findings.append(
            GroundedFindingResult(
                claim=finding.claim,
                source_type=finding.source_type,
                source_id=finding.source_id,
            )
        )

    proposals: List[ProposalResult] = []
    for proposal in validated.proposals:
        body_model = PROPOSAL_BODY_MODELS.get(proposal.type)
        if body_model is None:
            return AgentTurnResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=f"unknown proposal type: {proposal.type}",
            )
        try:
            body_model.model_validate(proposal.body)
        except ValidationError as exc:
            return AgentTurnResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=f"proposal '{proposal.type}' body failed validation: {exc}",
            )
        proposals.append(
            ProposalResult(proposal_type=proposal.type, title=proposal.title, body=proposal.body)
        )

    return AgentTurnResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        assistant_message=validated.assistant_message,
        grounded_findings=grounded_findings,
        assumptions=validated.assumptions,
        missing_information=validated.missing_information,
        proposals=proposals,
        next_questions=validated.next_questions,
    )
