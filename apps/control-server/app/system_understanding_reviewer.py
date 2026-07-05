"""Large-model System Understanding Review (Issue #81).

Reviews the claim graph, important evidence snippets, and code intelligence
facts to produce hierarchical understanding and focused questions. Does NOT
read raw documentation wholesale — operates on the structured claim graph
and reconciliation result.

Does NOT generate metadata/probe proposals in this step.

probe-agent:
  role: Reasoning-model system understanding reviewer
  capability: repository-understanding
  element_type: element
  consumers: [interactive-system-understanding, system-understanding]
  operation_kind: analysis
  state_effects: [external-api]
  probe_value: Verify that review uses reasoning model and fails closed on mock/non-reasoning models.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .docs_code_reconciler import ReconciliationResult, ReconciliationMapping
from .interview_language import get_interview_language, language_directive
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .understanding_graph import UnderstandingGraph, GraphNode, EvidenceRef

# v2: configurable output language for questions/summaries (Issue #127).
PROMPT_VERSION = "understanding-review-v2"
SCHEMA_VERSION = "understanding-review-v1"
DEFAULT_REVIEW_MAX_OUTPUT_TOKENS = 32_768
DEFAULT_REVIEW_MAX_NODES_PER_TYPE = 5
DEFAULT_REVIEW_MAX_PROMPT_CHARS = 30_000
DEFAULT_REVIEW_MAX_EVIDENCE_PER_NODE = 2


CONFIDENCE_LEVELS = {"confirmed", "likely", "uncertain", "conflicting"}
GAP_TYPE_VALUES = {
    "docs_only", "code_only", "source_doc_mismatch", "stale_explanation",
    "ambiguous_ownership", "unclassified_entrypoint", "missing_probe_flow",
}
SEVERITY_VALUES = {"low", "medium", "high"}
CATEGORY_VALUES = {"purpose", "capability", "api", "probe_flow", "general"}
PRIORITY_VALUES = {"high", "medium", "low"}

NEXT_ACTION_VALUES = {
    "confirm_purpose",
    "review_capabilities",
    "review_elements",
    "review_api_boundaries",
    "review_probe_flows",
    "resolve_conflicts",
    "resolve_open_questions",
    "ready_for_proposal",
}

_PROPOSAL_PATTERN = re.compile(
    r"\b(generat|creat|propos|instrument|patch|metadata|probe.plan|probe.propos)\w*\b",
    re.IGNORECASE,
)


class _ConfidenceLevel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["confirmed", "likely", "uncertain", "conflicting"] = "uncertain"
    reason: str = ""


class _EvidenceRefOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = ""
    start_line: int = 0
    end_line: int = 0
    summary: str = ""


class _UnderstandingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    summary: str = ""
    confidence: _ConfidenceLevel = Field(default_factory=_ConfidenceLevel)
    evidence: List[_EvidenceRefOut] = Field(default_factory=list)
    why_core: str = ""
    related_docs: List[str] = Field(default_factory=list)
    related_apis: List[str] = Field(default_factory=list)
    children: List[str] = Field(default_factory=list)


class _GapItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gap_type: Literal[
        "docs_only", "code_only", "source_doc_mismatch", "stale_explanation",
        "ambiguous_ownership", "unclassified_entrypoint", "missing_probe_flow",
    ]
    name: str
    summary: str = ""
    severity: Literal["low", "medium", "high"] = "low"


class _OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    category: Literal["purpose", "capability", "api", "probe_flow", "general"] = "general"
    priority: Literal["high", "medium", "low"] = "medium"


class _RawReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_purpose: List[_UnderstandingItem] = Field(default_factory=list)
    core_capabilities: List[_UnderstandingItem] = Field(default_factory=list)
    capability_elements: List[_UnderstandingItem] = Field(default_factory=list)
    supporting_elements: List[_UnderstandingItem] = Field(default_factory=list)
    api_boundaries: List[_UnderstandingItem] = Field(default_factory=list)
    probe_flow_candidates: List[_UnderstandingItem] = Field(default_factory=list)
    gap_analysis: List[_GapItem] = Field(default_factory=list)
    open_questions: List[_OpenQuestion] = Field(default_factory=list)
    suggested_next_action: Literal[
        "confirm_purpose",
        "review_capabilities",
        "review_elements",
        "review_api_boundaries",
        "review_probe_flows",
        "resolve_conflicts",
        "resolve_open_questions",
        "ready_for_proposal",
        "",
    ] = ""


@dataclass
class ReviewResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    current_understanding: Optional[Dict[str, Any]] = None
    gap_analysis: Optional[List[Dict[str, Any]]] = None
    open_questions: Optional[List[Dict[str, Any]]] = None
    suggested_next_action: str = ""
    error: Optional[str] = None


_SYSTEM_PROMPT = """\
You are a system understanding reviewer for probe-agent.
You review a structured claim graph and code intelligence facts to produce
a hierarchical understanding of the system.

Respond with a single JSON object and nothing else (no markdown fences),
matching exactly this shape:

{
  "system_purpose": [{"name": "...", "summary": "...", "confidence": {"level": "confirmed|likely|uncertain|conflicting", "reason": "..."}, "evidence": [{"path": "...", "start_line": 0, "end_line": 0, "summary": "..."}], "why_core": "", "related_docs": [], "related_apis": [], "children": []}],
  "core_capabilities": [...same shape...],
  "capability_elements": [...same shape...],
  "supporting_elements": [...same shape...],
  "api_boundaries": [...same shape...],
  "probe_flow_candidates": [...same shape...],
  "gap_analysis": [{"gap_type": "docs_only|code_only|source_doc_mismatch|stale_explanation|ambiguous_ownership|unclassified_entrypoint|missing_probe_flow", "name": "...", "summary": "...", "severity": "low|medium|high"}],
  "open_questions": [{"question": "...", "category": "purpose|capability|api|probe_flow|general", "priority": "high|medium|low"}],
  "suggested_next_action": "..."
}

Rules:
- Keep confirmed, likely, uncertain, and conflicting claims separate using the confidence level.
- Preserve evidence and provenance for all major understanding items.
- Order open questions from top-level purpose toward API/probe flow details.
- Do NOT generate metadata or probe proposals — this is understanding only.
- Use only the evidence provided in the input; do not invent facts.
- Keep each top-level list to the most important 8 items.
- Keep evidence to the strongest 3 references per item.
- Keep summaries and reasons concise.
- Phrase each open question as a focused confirmation the developer can
  answer briefly; avoid broad "explain the system" questions when the
  graph already contains a hypothesis to confirm.
"""


def _system_prompt(language: str) -> str:
    return _SYSTEM_PROMPT + language_directive(language) + "\n"


# Localized text for the deterministic no-evidence fallback question.
_NO_EVIDENCE_QUESTION = {
    "ja": "「{name}」({section})の根拠がドキュメント・コードから見つかりませんでした。この項目は正しいですか?",
    "en": "No evidence for {section} item: {name}. Is this item correct?",
}

_COMPACT_RETRY_PROMPT = """\
Your previous response was not valid complete JSON, likely because it was too
long or truncated. Regenerate the same schema as one compact JSON object only.
Use at most 5 items in each top-level list, at most 2 evidence references per
item, and concise one-sentence summaries.
"""


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _review_node_score(node: GraphNode) -> tuple:
    return (
        1 if not node.is_weak else 0,
        node.confidence,
        len(node.evidence),
        1 if node.mentioned_apis else 0,
        1 if node.mentioned_symbols else 0,
        node.name,
    )


def _selected_review_nodes(graph: UnderstandingGraph) -> List[GraphNode]:
    max_per_type = _positive_int_env(
        "INTELLIGENCE_REVIEW_MAX_NODES_PER_TYPE",
        DEFAULT_REVIEW_MAX_NODES_PER_TYPE,
    )
    type_order = [
        "system_purpose",
        "core_capability",
        "capability_element",
        "api_boundary",
        "probe_flow",
        "supporting_element",
        "open_question",
        "conflict",
    ]
    by_type: Dict[str, List[GraphNode]] = {node_type: [] for node_type in type_order}
    extra_types: Dict[str, List[GraphNode]] = {}
    for node in graph.nodes.values():
        target = by_type if node.node_type in by_type else extra_types
        target.setdefault(node.node_type, []).append(node)

    selected: List[GraphNode] = []
    for node_type in type_order + sorted(extra_types):
        nodes = by_type.get(node_type) or extra_types.get(node_type) or []
        selected.extend(
            sorted(nodes, key=_review_node_score, reverse=True)[:max_per_type]
        )
    return selected


def _build_review_prompt(
    graph: UnderstandingGraph,
    reconciliation: ReconciliationResult,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Build the review prompt from graph + code facts."""
    parts: List[str] = []

    selected_nodes = _selected_review_nodes(graph)
    parts.append(
        "## Understanding Graph Nodes\n"
        f"- total_nodes: {len(graph.nodes)}\n"
        f"- included_nodes: {len(selected_nodes)}\n"
        f"- claim_count: {graph.claim_count}\n"
        f"- valid_claim_count: {graph.valid_claim_count}"
    )
    for node in selected_nodes:
        ev_summaries = [
            f"  - {e.path}:{e.start_line}-{e.end_line} ({_trim(e.summary, 100)})"
            for e in node.evidence[:DEFAULT_REVIEW_MAX_EVIDENCE_PER_NODE]
        ]
        parts.append(
            f"- [{node.node_type}] {_trim(node.name, 140)} (confidence={node.confidence:.2f}, "
            f"weak={node.is_weak})\n"
            + "\n".join(ev_summaries)
        )
        if node.summary and node.summary != node.name:
            parts.append(f"  Summary: {_trim(node.summary, 180)}")
        if node.mentioned_apis:
            parts.append(f"  APIs: {_trim(', '.join(node.mentioned_apis[:8]), 240)}")
        if node.mentioned_symbols:
            parts.append(f"  Symbols: {_trim(', '.join(node.mentioned_symbols[:8]), 240)}")

    if graph.conflicts:
        parts.append("\n## Conflicts\n")
        for n1, n2 in graph.conflicts:
            parts.append(f"- {n1} <-> {n2}")

    parts.append(f"\n## Confidence Summary\n")
    for nt, conf in graph.confidence_summary.items():
        parts.append(f"- {nt}: {conf:.2f}")

    parts.append(f"\n## Code Intelligence Reconciliation\n")
    parts.append(f"- Matched: {reconciliation.matched_count}")
    parts.append(f"- Docs-only: {reconciliation.docs_only_count}")
    parts.append(f"- Code-only: {reconciliation.code_only_count}")
    parts.append(f"- Mismatches: {reconciliation.mismatch_count}")

    if reconciliation.gaps:
        parts.append("\n### Gaps")
        for gap in reconciliation.gaps[:20]:
            parts.append(
                f"- [{gap.gap_type}] {_trim(gap.node_name, 140)}: {_trim(gap.notes, 180)}"
            )

    if history:
        parts.append("\n## Interview History\n")
        for msg in history[-10:]:
            parts.append(f"{msg['role']}: {msg['content'][:500]}")

    prompt = "\n".join(parts)
    max_prompt_chars = _positive_int_env(
        "INTELLIGENCE_REVIEW_MAX_PROMPT_CHARS",
        DEFAULT_REVIEW_MAX_PROMPT_CHARS,
    )
    if len(prompt) > max_prompt_chars:
        prompt = (
            prompt[: max_prompt_chars - 120].rstrip()
            + "\n\n[Prompt truncated to configured review budget; use included high-priority graph nodes only.]"
        )
    return prompt


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _review_max_output_tokens() -> int:
    return _positive_int_env(
        "INTELLIGENCE_REVIEW_MAX_OUTPUT_TOKENS",
        DEFAULT_REVIEW_MAX_OUTPUT_TOKENS,
    )


def _parse_review_response(raw: str) -> _RawReviewResponse:
    parsed = json.loads(_strip_fences(raw))
    if (
        isinstance(parsed, dict)
        and parsed.get("suggested_next_action") not in NEXT_ACTION_VALUES
        and not _PROPOSAL_PATTERN.search(str(parsed.get("suggested_next_action") or ""))
    ):
        parsed["suggested_next_action"] = "resolve_open_questions"
    return _RawReviewResponse.model_validate(parsed)


def generate_understanding_review(
    client: LLMClient,
    config: LLMConfig,
    *,
    graph: UnderstandingGraph,
    reconciliation: ReconciliationResult,
    history: Optional[List[Dict[str, str]]] = None,
) -> ReviewResult:
    """Generate a system understanding review from graph + code facts.

    Fail-closed: mock clients and non-reasoning models return an error.
    No proposals are generated.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return ReviewResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error="Understanding review requires a configured reasoning model",
        )

    prompt = _build_review_prompt(graph, reconciliation, history)
    try:
        max_output_tokens = _review_max_output_tokens()
        language = get_interview_language()
    except ValueError as exc:
        return ReviewResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=str(exc),
        )
    system_prompt = _system_prompt(language)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=max_output_tokens,
        )
    except LLMError as exc:
        return ReviewResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=str(exc),
        )

    try:
        validated = _parse_review_response(raw)
    except json.JSONDecodeError:
        try:
            raw = client.generate_text(
                [
                    {"role": "system", "content": f"{system_prompt}\n\n{_COMPACT_RETRY_PROMPT}"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=max_output_tokens,
            )
            validated = _parse_review_response(raw)
        except (json.JSONDecodeError, ValidationError, LLMError) as exc:
            return ReviewResult(
                provider=config.provider,
                model=config.model,
                is_mock=False,
                error=f"Failed to parse review response: {exc}",
            )
    except (json.JSONDecodeError, ValidationError) as exc:
        return ReviewResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse review response: {exc}",
        )

    _EVIDENCE_REQUIRED_SECTIONS = (
        "system_purpose", "core_capabilities", "capability_elements", "api_boundaries",
    )
    for section_name in _EVIDENCE_REQUIRED_SECTIONS:
        items: List[_UnderstandingItem] = getattr(validated, section_name)
        for item in items:
            if not item.evidence:
                item.confidence = _ConfidenceLevel(level="uncertain", reason="No evidence provided")
                validated.open_questions.append(_OpenQuestion(
                    question=_NO_EVIDENCE_QUESTION[language].format(
                        section=section_name, name=item.name,
                    ),
                    category="general",
                    priority="high",
                ))

    current_understanding = {
        "system_purpose": [item.model_dump() for item in validated.system_purpose],
        "core_capabilities": [item.model_dump() for item in validated.core_capabilities],
        "capability_elements": [item.model_dump() for item in validated.capability_elements],
        "supporting_elements": [item.model_dump() for item in validated.supporting_elements],
        "api_boundaries": [item.model_dump() for item in validated.api_boundaries],
        "probe_flow_candidates": [item.model_dump() for item in validated.probe_flow_candidates],
    }

    gap_analysis = [item.model_dump() for item in validated.gap_analysis]
    open_questions = [item.model_dump() for item in validated.open_questions]

    return ReviewResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        current_understanding=current_understanding,
        gap_analysis=gap_analysis,
        open_questions=open_questions,
        suggested_next_action=validated.suggested_next_action,
    )
