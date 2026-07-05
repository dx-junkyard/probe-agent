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
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .interview_language import get_interview_language, language_directive
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .models import (
    InterviewContextPack,
    InterviewProposalMetadataBlock,
    InterviewProposalProbePlan,
    InterviewQuestionEvidenceRef,
    InterviewStructuredQuestion,
)
from .probe_planner import check_denylist

# v3: configurable output language (Issue #127) + hypothesis-first questions
# grounded in the session's current understanding (Issue #128).
PROMPT_VERSION = "interview-v3"
# v2: next_questions items are structured {question_text, hypothesis,
# evidence_refs, answer_options} objects (Issue #128); plain strings are
# still accepted and normalized.
SCHEMA_VERSION = "interview-v2"

MAX_RECENT_MESSAGES = 20

DEFAULT_UNDERSTANDING_MAX_CHARS = 20_000
GAP_AND_QUESTION_MAX_CHARS = 4_000


def _understanding_max_chars() -> int:
    raw = os.getenv(
        "INTERVIEW_UNDERSTANDING_MAX_CHARS", str(DEFAULT_UNDERSTANDING_MAX_CHARS)
    )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("INTERVIEW_UNDERSTANDING_MAX_CHARS must be an integer") from exc
    if value < 1:
        raise ValueError("INTERVIEW_UNDERSTANDING_MAX_CHARS must be positive")
    return value


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


class _RawQuestionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    start_line: int = 0
    end_line: int = 0


class _RawStructuredQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: str = Field(..., min_length=1, max_length=2_000)
    hypothesis: str = Field(default="", max_length=4_000)
    evidence_refs: List[_RawQuestionEvidence] = Field(default_factory=list, max_length=5)
    answer_options: List[str] = Field(default_factory=list, max_length=4)


class _RawInterviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_message: str = Field(..., min_length=1, max_length=20_000)
    proposals: List[_RawCombinedProposal] = Field(default_factory=list, max_length=50)
    # Structured hypothesis-first questions (schema interview-v2). Plain
    # strings remain accepted and are normalized to question_text-only
    # objects — a structural conversion, not an interpretation.
    next_questions: List[Union[_RawStructuredQuestion, str]] = Field(
        default_factory=list, max_length=10
    )


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
    next_questions: List[InterviewStructuredQuestion] = field(default_factory=list)
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
  "next_questions": [
    {
      "question_text": "...",
      "hypothesis": "...",
      "evidence_refs": [{"path": "src/module.py", "start_line": 1, "end_line": 20}],
      "answer_options": ["..."]
    }
  ]
}

Rules:
- Only reference symbols and paths that appear in the supplied context pack
  or the supplied current understanding.
- "proposals" should be empty unless the user asks you to generate proposals.
  When asked, produce one combined proposal per discussed symbol.
- Each proposal's "metadata" must use only the enum values shown above for
  element_type, operation_kind, and state_effects. Unknown values are rejected.
- Each proposal's "probe_plan" must use only the enum values shown above.
- You never decide, adopt, or execute anything. Proposals are always
  reviewed by a human; do not claim a proposal has been accepted or run.
- Interview style: when a current understanding is supplied, do NOT ask
  open-ended questions about topics it already covers. Instead state your
  hypothesis in "hypothesis", back it with "evidence_refs" (path + line
  range taken from the context pack or current understanding — never
  invented), and ask ONE focused confirmation question the developer can
  answer with yes/no plus a correction. Offer short "answer_options" when
  the realistic answers form a small set.
- Ask about one topic per question; order questions from system purpose
  toward API/probe-flow details.
- Only ask open-ended questions for topics with no grounding in the
  supplied context. Never re-ask a question that the conversation history
  or the answered open-questions already covers.
- If you lack information to classify a symbol, say so in your message
  and ask a clarifying question in "next_questions".
- Do not invent symbols not present in the context pack.
"""


def _system_prompt(language: str) -> str:
    return _SYSTEM_PROMPT + language_directive(language) + "\n"


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _trim_json(value: Any, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 30)] + "…[truncated to budget]"


def _build_user_prompt(
    context_pack: InterviewContextPack,
    history: List[Dict[str, str]],
    user_message: str,
    *,
    current_understanding: Optional[Dict[str, Any]] = None,
    gap_analysis: Optional[List[Dict[str, Any]]] = None,
    open_questions: Optional[List[Dict[str, Any]]] = None,
    understanding_max_chars: int = DEFAULT_UNDERSTANDING_MAX_CHARS,
) -> str:
    recent_history = history[-MAX_RECENT_MESSAGES:]
    parts: List[str] = []
    if current_understanding:
        parts.append(
            "## Current understanding (your working hypothesis about this system; "
            "confirm or correct it with the developer instead of asking from scratch)"
        )
        parts.append(_trim_json(current_understanding, understanding_max_chars))
    if gap_analysis:
        parts.append("## Gap analysis (docs/code mismatches worth clarifying)")
        parts.append(_trim_json(gap_analysis, GAP_AND_QUESTION_MAX_CHARS))
    if open_questions:
        parts.append(
            "## Open questions still unanswered (prioritize these; never re-ask "
            "anything already answered in the history)"
        )
        parts.append(_trim_json(open_questions, GAP_AND_QUESTION_MAX_CHARS))
    parts.append(
        "## Context Pack (snapshot-grounded symbols and entrypoints; the only allowed reference source)"
    )
    parts.append(context_pack.model_dump_json())
    if recent_history:
        parts.append("## Recent conversation history")
        for msg in recent_history:
            parts.append(f"{msg['role']}: {msg['content']}")
    parts.append("## Latest user message")
    parts.append(user_message)
    return "\n\n".join(parts)


# --- Question normalization and deterministic evidence validation ------------


def _normalize_question(
    q: Union[_RawStructuredQuestion, str],
) -> InterviewStructuredQuestion:
    if isinstance(q, str):
        return InterviewStructuredQuestion(question_text=q)
    return InterviewStructuredQuestion(
        question_text=q.question_text,
        hypothesis=q.hypothesis or None,
        evidence_refs=[
            InterviewQuestionEvidenceRef(
                path=e.path, start_line=e.start_line, end_line=e.end_line
            )
            for e in q.evidence_refs
        ],
        answer_options=list(q.answer_options),
    )


def _allowed_evidence_spans(
    context_pack: InterviewContextPack,
    current_understanding: Optional[Dict[str, Any]],
) -> Dict[str, List[Tuple[int, int]]]:
    """Collect the snapshot-grounded (path → line spans) the model may cite."""
    spans: Dict[str, List[Tuple[int, int]]] = {}
    for sym in context_pack.symbols:
        spans.setdefault(sym.path, []).append((sym.start_line, sym.end_line))
    for ep in context_pack.entrypoints:
        spans.setdefault(ep.handler_path, []).append((ep.line_start, ep.line_end))
    if current_understanding:
        for section in current_understanding.values():
            if not isinstance(section, list):
                continue
            for item in section:
                if not isinstance(item, dict):
                    continue
                for ev in item.get("evidence") or []:
                    if not isinstance(ev, dict):
                        continue
                    path = ev.get("path")
                    if path:
                        spans.setdefault(str(path), []).append((
                            int(ev.get("start_line") or 0),
                            int(ev.get("end_line") or 0),
                        ))
    return spans


def _span_matches(ref: InterviewQuestionEvidenceRef, span: Tuple[int, int]) -> bool:
    start, end = span
    if start == 0 and end == 0:
        # Path-level evidence without a known line range accepts any lines.
        return True
    return ref.start_line <= end and ref.end_line >= start


def _validate_question_evidence(
    questions: List[InterviewStructuredQuestion],
    spans: Dict[str, List[Tuple[int, int]]],
) -> Optional[str]:
    """Structural validation of question evidence refs; returns an error or None."""
    for q in questions:
        for ref in q.evidence_refs:
            if ref.path not in spans:
                return (
                    f"question evidence references unknown path '{ref.path}' "
                    "(not in context pack or current understanding)"
                )
            if ref.start_line < 0 or ref.end_line < ref.start_line:
                return (
                    f"question evidence has invalid line range "
                    f"{ref.start_line}-{ref.end_line} for '{ref.path}'"
                )
            if (ref.start_line or ref.end_line) and not any(
                _span_matches(ref, span) for span in spans[ref.path]
            ):
                return (
                    f"question evidence lines {ref.start_line}-{ref.end_line} do "
                    f"not overlap any known span in '{ref.path}'"
                )
    return None


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
    current_understanding: Optional[Dict[str, Any]] = None,
    gap_analysis: Optional[List[Dict[str, Any]]] = None,
    open_questions: Optional[List[Dict[str, Any]]] = None,
) -> InterviewTurnResult:
    """Generate one structured assistant turn for the interview dialogue.

    When the session carries a built understanding, it is injected as the
    model's working hypothesis so questions confirm/correct it instead of
    starting from scratch (Issue #128).

    Fail-closed: if the client is mock, the model is not a reasoning model,
    the language/budget configuration is invalid, the API call fails, or
    validation (including deterministic evidence-ref checks) fails, the
    result carries an error and no proposals are stored.
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

    try:
        language = get_interview_language()
        understanding_max_chars = _understanding_max_chars()
    except ValueError as exc:
        return InterviewTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=str(exc),
        )

    prompt = _build_user_prompt(
        context_pack,
        history,
        user_message,
        current_understanding=current_understanding,
        gap_analysis=gap_analysis,
        open_questions=open_questions,
        understanding_max_chars=understanding_max_chars,
    )

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _system_prompt(language)},
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

    # Normalize questions and structurally validate their evidence refs
    # against the snapshot-grounded spans (deterministic; fail closed).
    questions = [_normalize_question(q) for q in validated.next_questions]
    evidence_error = _validate_question_evidence(
        questions, _allowed_evidence_spans(context_pack, current_understanding)
    )
    if evidence_error:
        return InterviewTurnResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Question evidence validation failed: {evidence_error}",
        )

    return InterviewTurnResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        assistant_message=validated.assistant_message,
        proposals=proposals,
        next_questions=questions,
    )
