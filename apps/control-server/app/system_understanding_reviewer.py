"""Large-model System Understanding Review (Issue #81).

Reviews the claim graph, important evidence snippets, and code intelligence
facts to produce hierarchical understanding and focused questions. Does NOT
read raw documentation wholesale — operates on the structured claim graph
and reconciliation result.

Does NOT generate metadata/probe proposals in this step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .docs_code_reconciler import ReconciliationResult, ReconciliationMapping
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .understanding_graph import UnderstandingGraph, GraphNode, EvidenceRef

PROMPT_VERSION = "understanding-review-v1"
SCHEMA_VERSION = "understanding-review-v1"


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
"""


def _build_review_prompt(
    graph: UnderstandingGraph,
    reconciliation: ReconciliationResult,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Build the review prompt from graph + code facts."""
    parts: List[str] = []

    parts.append("## Understanding Graph Nodes\n")
    for nid, node in sorted(graph.nodes.items()):
        ev_summaries = [f"  - {e.path}:{e.start_line}-{e.end_line} ({e.summary[:80]})" for e in node.evidence[:5]]
        parts.append(
            f"- [{node.node_type}] {node.name} (confidence={node.confidence:.2f}, "
            f"weak={node.is_weak})\n"
            + "\n".join(ev_summaries)
        )
        if node.mentioned_apis:
            parts.append(f"  APIs: {', '.join(node.mentioned_apis)}")
        if node.mentioned_symbols:
            parts.append(f"  Symbols: {', '.join(node.mentioned_symbols)}")

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
            parts.append(f"- [{gap.gap_type}] {gap.node_name}: {gap.notes}")

    if history:
        parts.append("\n## Interview History\n")
        for msg in history[-10:]:
            parts.append(f"{msg['role']}: {msg['content'][:500]}")

    return "\n".join(parts)


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


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
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=8192,
        )
    except LLMError as exc:
        return ReviewResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawReviewResponse.model_validate(parsed)
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
                    question=f"No evidence for {section_name} item: {item.name}",
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
