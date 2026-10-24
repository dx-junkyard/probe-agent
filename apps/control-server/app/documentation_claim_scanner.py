"""Documentation claim scanner with evidence-bound structured output (Issue #78).

Scans individual Markdown chunks for local documentation claims using a
small/cheap LLM. Each claim must include evidence line ranges within the
chunk. Claims outside the chunk are rejected. Results are cacheable by
chunk content hash and prompt/schema version.

This module performs local extraction only — it does not decide final
System Purpose or Core Capability hierarchy.

Two vocabularies, never mixed (vision-to-feature-research.md VF-D1 / §3.2):

- ``SYSTEM_CLAIM_TYPES`` describe the target system itself and are extracted
  from the project's own documentation.
- ``SOURCE_CLAIM_TYPES`` are the words of an external source (a Sponsor's
  plan, a survey, ...) registered in the source register
  (``source_register.py``). A chunk from a registered source accepts only
  these types, and every other chunk rejects them. The claim keeps the
  ``source_id``; the source attributes are joined at read time.

probe-agent:
  role: Evidence-bound documentation claim extractor
  capability: documentation-understanding
  element_type: element
  consumers: [system-understanding, interview]
  operation_kind: analysis
  state_effects: [external-api]
  probe_value: Compare extracted claims against expected System Purpose and Capability evidence.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .documentation_chunker import MarkdownChunk
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

#: Output budget per chunk. For a reasoning model the budget also covers its
#: hidden reasoning, so 4096 left long chunks with an empty answer whenever
#: the model reasoned at length (seen on a 118-line table chunk with
#: gpt-6-luna: 4075 of 4096 tokens used on a successful try, empty on others).
DEFAULT_MAX_OUTPUT_TOKENS = 4096
DEFAULT_REASONING_MAX_OUTPUT_TOKENS = 16384


def scan_max_output_tokens(config: LLMConfig) -> int:
    """`INTELLIGENCE_CLAIM_SCAN_MAX_OUTPUT_TOKENS` if set, else a larger
    default for reasoning models."""
    raw = os.getenv("INTELLIGENCE_CLAIM_SCAN_MAX_OUTPUT_TOKENS", "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    if is_reasoning_model(config.provider or "", config.model or ""):
        return DEFAULT_REASONING_MAX_OUTPUT_TOKENS
    return DEFAULT_MAX_OUTPUT_TOKENS

PROMPT_VERSION = "claim-scanner-v1"
SCHEMA_VERSION = "claim-scanner-v1"
#: Source chunks use their own prompt and schema; system chunks keep v1 so
#: existing scan results stay reusable.
SOURCE_PROMPT_VERSION = "claim-scanner-source-v3"
SOURCE_SCHEMA_VERSION = "claim-scanner-source-v3"

SYSTEM_CLAIM_TYPES = {
    "system_purpose",
    "core_capability",
    "capability_element",
    "supporting_element",
    "api_boundary",
    "probe_flow",
    "implementation_note",
    "open_question",
    "risk",
    "mismatch_hint",
}

SOURCE_CLAIM_TYPES = {
    "aspiration",
    "policy_goal",
    "indicator",
    "initiative",
    "problem_statement",
    "target_group",
    "constraint",
    # A view the source attributes to someone other than its publisher
    # (committee members, survey free comments, ...). Kept apart so it is
    # never read as the publisher's own goal (VF-D1).
    "opinion",
}

CLAIM_TYPES = SYSTEM_CLAIM_TYPES | SOURCE_CLAIM_TYPES


def versions_for(is_source: bool) -> Tuple[str, str]:
    """(prompt_version, schema_version) used to scan a chunk."""
    if is_source:
        return SOURCE_PROMPT_VERSION, SOURCE_SCHEMA_VERSION
    return PROMPT_VERSION, SCHEMA_VERSION


class ClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int
    end_line: int
    #: Set only for a chunk of a registered external source. Attributes are
    #: never copied here; resolve them from the register at read time.
    source_id: Optional[str] = None


class IndicatorDetail(BaseModel):
    """Structured part of an ``indicator`` claim. Every value is the source's
    own text (no unit conversion, no inference); "" means not stated."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    unit: str = ""
    baseline: str = ""
    baseline_year: str = ""
    target: str = ""
    target_year: str = ""


class DocumentationClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_type: str
    summary: str = Field(..., min_length=1, max_length=2000)
    evidence: ClaimEvidence
    confidence: float = Field(ge=0.0, le=1.0)
    mentioned_apis: List[str] = Field(default_factory=list)
    mentioned_symbols: List[str] = Field(default_factory=list)
    indicator: Optional[IndicatorDetail] = None
    is_valid: bool = True
    invalid_reason: Optional[str] = None

    @field_validator("claim_type")
    @classmethod
    def validate_claim_type(cls, v: str) -> str:
        if v not in CLAIM_TYPES:
            raise ValueError(f"Invalid claim_type '{v}'; must be one of {sorted(CLAIM_TYPES)}")
        return v


class ChunkScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    chunk_content_hash: str
    prompt_version: str
    schema_version: str
    claims: List[DocumentationClaim] = Field(default_factory=list)
    error: Optional[str] = None
    is_cached: bool = False


class _RawClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_type: str
    summary: str = ""
    evidence_start_line: int
    evidence_end_line: int
    confidence: float = 0.5
    mentioned_apis: List[str] = Field(default_factory=list)
    mentioned_symbols: List[str] = Field(default_factory=list)
    indicator: Optional[IndicatorDetail] = None


class _RawScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: List[_RawClaim] = Field(default_factory=list, max_length=50)


_SYSTEM_PROMPT = """\
You are a documentation claim extractor for probe-agent.
You scan a single Markdown documentation chunk and extract structured claims.

Respond with a single JSON object and nothing else (no markdown fences,
no commentary), matching exactly this shape:

{
  "claims": [
    {
      "claim_type": "<one of: system_purpose, core_capability, capability_element, supporting_element, api_boundary, probe_flow, implementation_note, open_question, risk, mismatch_hint>",
      "summary": "Brief factual claim extracted from this chunk",
      "evidence_start_line": <absolute line number in the source file>,
      "evidence_end_line": <absolute line number in the source file>,
      "confidence": <0.0 to 1.0>,
      "mentioned_apis": ["GET /path", "POST /other"],
      "mentioned_symbols": ["module.function_name"]
    }
  ]
}

Rules:
- Extract only claims supported by text in this chunk.
- Every claim must have evidence_start_line and evidence_end_line within
  the chunk's line range.
- Do not make claims about content not present in the chunk.
- Use the exact claim_type enum values listed above.
- Be conservative: prefer fewer high-confidence claims over many weak ones.
- Extract mentioned API paths (like GET /users, POST /api/v1/items) and
  symbols (like module.function or ClassName.method) when visible in text.
- Do not infer system-wide conclusions from a single chunk.
"""


_SOURCE_SYSTEM_PROMPT = """\
You are an extractor of claims from an EXTERNAL SOURCE document (for example
a public plan, strategy, or survey report) for probe-agent. The source is not
the target system's own documentation: extract what the source itself says,
in its own terms. Do not describe any software system and do not interpret
how a product should respond.

Respond with a single JSON object and nothing else (no markdown fences,
no commentary), matching exactly this shape:

{
  "claims": [
    {
      "claim_type": "<one of: aspiration, policy_goal, indicator, initiative, problem_statement, target_group, constraint, opinion>",
      "summary": "What the source states, close to its own wording, in the source's language",
      "evidence_start_line": <absolute line number in the source file>,
      "evidence_end_line": <absolute line number in the source file>,
      "confidence": <0.0 to 1.0>,
      "indicator": {"name": "", "unit": "", "baseline": "", "baseline_year": "", "target": "", "target_year": ""}
    }
  ]
}

claim_type meanings:
- aspiration: a desired future state of the area / organisation as a whole
- policy_goal: a goal or direction for one field or policy area
- indicator: a named measure, optionally with baseline / target values and years
- initiative: a concrete measure, programme or project (with owner or target group if stated)
- problem_statement: a problem or current condition as the source recognises it
- target_group: a group of people the source names as affected or targeted
- constraint: a principle, condition or prohibition the source sets
- opinion: a view the source attributes to someone other than its publisher
  (for example committee members' comments, quoted free-text answers,
  "との意見"). Use opinion instead of any other type for such statements,
  even when they propose a goal or a measure: they are not the publisher's
  own position.
  Aggregated results the publisher reports as measured numbers (survey
  shares, counts, rates) are indicator, not opinion, even when respondents
  were asked what they want.

Rules:
- Extract only what is written in this chunk. Never add numbers, years or
  targets that are not written.
- Include "indicator" only for claim_type "indicator"; copy values exactly as
  written and leave a field "" when the source does not state it.
- Ignore YAML front matter (lines between the leading --- markers).
- Every claim must have evidence_start_line and evidence_end_line within
  the chunk's line range.
- Prefer fewer, precise claims; one claim per distinct goal, indicator or initiative.
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


def _validate_claim_bounds(
    claim: _RawClaim,
    chunk: MarkdownChunk,
) -> Tuple[bool, Optional[str]]:
    """Check that claim evidence lines are within the chunk boundaries."""
    if claim.evidence_start_line < chunk.start_line:
        return False, f"evidence_start_line {claim.evidence_start_line} < chunk start {chunk.start_line}"
    if claim.evidence_end_line > chunk.end_line:
        return False, f"evidence_end_line {claim.evidence_end_line} > chunk end {chunk.end_line}"
    if claim.evidence_start_line > claim.evidence_end_line:
        return False, "evidence_start_line > evidence_end_line"
    return True, None


def _extract_api_paths(text: str) -> List[str]:
    """Extract API paths mentioned in text (GET /path, POST /path, etc.)."""
    pattern = r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s,)}\]\"']+)"
    return list(set(re.findall(pattern, text, re.IGNORECASE)))


def _extract_symbols(text: str) -> List[str]:
    """Extract Python-like symbol references from text."""
    pattern = r"\b([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)+)\b"
    candidates = set(re.findall(pattern, text))
    excluded = {"e.g", "i.e", "etc.", "vs.", "0.0", "1.0"}
    return [s for s in sorted(candidates) if s not in excluded and not s[0].isdigit()]


def _get_evidence_text(chunk: MarkdownChunk, start_line: int, end_line: int) -> str:
    """Extract the text from a chunk corresponding to evidence line range."""
    chunk_lines = chunk.content.split("\n")
    offset = chunk.start_line
    rel_start = max(0, start_line - offset)
    rel_end = min(len(chunk_lines), end_line - offset + 1)
    return "\n".join(chunk_lines[rel_start:rel_end])


def _merge_unique(model_list: List[str], deterministic_list: List[str]) -> List[str]:
    """Merge two lists, preserving order and removing duplicates."""
    seen: Set[str] = set()
    result: List[str] = []
    for item in model_list + deterministic_list:
        lower = item.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(item)
    return result


def scan_chunk(
    client: LLMClient,
    config: LLMConfig,
    chunk: MarkdownChunk,
    cache: Optional[Dict[str, ChunkScanResult]] = None,
    source: Optional[Dict[str, str]] = None,
) -> ChunkScanResult:
    """Scan a single chunk for documentation claims.

    ``source`` is the register attributes (including ``source_id``) when the
    chunk belongs to a registered external source; such a chunk is scanned
    with the source prompt and accepts only ``SOURCE_CLAIM_TYPES``.

    If a cache dict is provided and contains a result for the chunk's
    content_hash + prompt/schema version, return the cached result.
    """
    is_source = bool(source and source.get("source_id"))
    prompt_version, schema_version = versions_for(is_source)
    allowed_types = SOURCE_CLAIM_TYPES if is_source else SYSTEM_CLAIM_TYPES
    source_id = source.get("source_id") if is_source and source else None
    cache_key = f"{chunk.content_hash}:{chunk.path}:{chunk.start_line}:{prompt_version}:{schema_version}"

    if cache is not None and cache_key in cache:
        cached = cache[cache_key]
        return ChunkScanResult(
            chunk_id=chunk.chunk_id,
            chunk_content_hash=chunk.content_hash,
            prompt_version=prompt_version,
            schema_version=schema_version,
            claims=cached.claims,
            is_cached=True,
        )

    if isinstance(client, MockLLMClient):
        return ChunkScanResult(
            chunk_id=chunk.chunk_id,
            chunk_content_hash=chunk.content_hash,
            prompt_version=prompt_version,
            schema_version=schema_version,
            error="Claim scanning requires a configured LLM; mock fallback is prohibited",
        )

    source_lines = ""
    if is_source and source:
        source_lines = "".join(
            f"- source {k}: {source.get(k, '')}\n"
            for k in ("source_id", "layer", "title", "publisher", "issued", "period_start", "period_end", "edition", "status")
            if source.get(k)
        )
    user_prompt = (
        f"## Chunk metadata\n"
        f"{source_lines}"
        f"- path: {chunk.path}\n"
        f"- heading_path: {'/'.join(chunk.heading_path)}\n"
        f"- line range: {chunk.start_line}-{chunk.end_line}\n"
        f"- doc_role: {chunk.doc_role_hint}\n\n"
        f"## Chunk content\n\n{chunk.content}"
    )

    max_output_tokens = scan_max_output_tokens(config)
    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SOURCE_SYSTEM_PROMPT if is_source else _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=max_output_tokens,
        )
    except LLMError as exc:
        return ChunkScanResult(
            chunk_id=chunk.chunk_id,
            chunk_content_hash=chunk.content_hash,
            prompt_version=prompt_version,
            schema_version=schema_version,
            error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawScanResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        finish = getattr(client, "last_finish_reason", None)
        if finish in ("length", "max_tokens"):
            message = (
                f"Model output hit the output token limit ({max_output_tokens}) before the answer was complete; "
                "raise INTELLIGENCE_CLAIM_SCAN_MAX_OUTPUT_TOKENS or split the section"
            )
        else:
            message = f"Failed to parse scan response: {exc}"
        return ChunkScanResult(
            chunk_id=chunk.chunk_id,
            chunk_content_hash=chunk.content_hash,
            prompt_version=prompt_version,
            schema_version=schema_version,
            error=message,
        )

    claims: List[DocumentationClaim] = []
    for raw_claim in validated.claims:
        in_bounds, reason = _validate_claim_bounds(raw_claim, chunk)

        claim_type_valid = raw_claim.claim_type in allowed_types

        if not claim_type_valid:
            if raw_claim.claim_type in CLAIM_TYPES:
                reason_text = (
                    f"claim_type {raw_claim.claim_type} is not allowed in "
                    f"{'an external source' if is_source else 'system documentation'}"
                )
            else:
                reason_text = f"Invalid claim_type: {raw_claim.claim_type}"
            claims.append(DocumentationClaim(
                claim_type="open_question" if is_source else "implementation_note",
                summary=raw_claim.summary or "Invalid claim type",
                evidence=ClaimEvidence(
                    path=chunk.path,
                    source_id=source_id,
                    start_line=max(raw_claim.evidence_start_line, chunk.start_line),
                    end_line=min(raw_claim.evidence_end_line, chunk.end_line),
                ),
                confidence=0.0,
                mentioned_apis=raw_claim.mentioned_apis,
                mentioned_symbols=raw_claim.mentioned_symbols,
                is_valid=False,
                invalid_reason=reason_text,
            ))
            continue

        if not in_bounds:
            claims.append(DocumentationClaim(
                claim_type=raw_claim.claim_type,
                summary=raw_claim.summary or "Out of bounds claim",
                evidence=ClaimEvidence(
                    path=chunk.path,
                    source_id=source_id,
                    start_line=max(raw_claim.evidence_start_line, chunk.start_line),
                    end_line=min(raw_claim.evidence_end_line, chunk.end_line),
                ),
                confidence=0.0,
                mentioned_apis=raw_claim.mentioned_apis,
                mentioned_symbols=raw_claim.mentioned_symbols,
                is_valid=False,
                invalid_reason=reason,
            ))
            continue

        if not raw_claim.summary or len(raw_claim.summary.strip()) < 3:
            claims.append(DocumentationClaim(
                claim_type=raw_claim.claim_type,
                summary=raw_claim.summary or "empty",
                evidence=ClaimEvidence(
                    path=chunk.path,
                    source_id=source_id,
                    start_line=raw_claim.evidence_start_line,
                    end_line=raw_claim.evidence_end_line,
                ),
                confidence=0.0,
                is_valid=False,
                invalid_reason="Missing or too-short evidence summary",
            ))
            continue

        clamped_confidence = max(0.0, min(1.0, raw_claim.confidence))

        evidence_text = _get_evidence_text(chunk, raw_claim.evidence_start_line, raw_claim.evidence_end_line)
        merged_apis = _merge_unique(raw_claim.mentioned_apis, _extract_api_paths(evidence_text))
        merged_symbols = _merge_unique(raw_claim.mentioned_symbols, _extract_symbols(evidence_text))

        try:
            claim = DocumentationClaim(
                claim_type=raw_claim.claim_type,
                summary=raw_claim.summary,
                evidence=ClaimEvidence(
                    path=chunk.path,
                    source_id=source_id,
                    start_line=raw_claim.evidence_start_line,
                    end_line=raw_claim.evidence_end_line,
                ),
                confidence=clamped_confidence,
                mentioned_apis=[] if is_source else merged_apis,
                mentioned_symbols=[] if is_source else merged_symbols,
                indicator=raw_claim.indicator if raw_claim.claim_type == "indicator" else None,
            )
        except ValidationError as exc:
            claims.append(DocumentationClaim(
                claim_type="implementation_note",
                summary=raw_claim.summary[:200] if raw_claim.summary else "Validation failed",
                evidence=ClaimEvidence(
                    path=chunk.path,
                    source_id=source_id,
                    start_line=max(raw_claim.evidence_start_line, chunk.start_line),
                    end_line=min(raw_claim.evidence_end_line, chunk.end_line),
                ),
                confidence=0.0,
                is_valid=False,
                invalid_reason=f"Claim validation failed: {exc}",
            ))
            continue
        claims.append(claim)

    result = ChunkScanResult(
        chunk_id=chunk.chunk_id,
        chunk_content_hash=chunk.content_hash,
        prompt_version=prompt_version,
        schema_version=schema_version,
        claims=claims,
    )

    if cache is not None:
        cache[cache_key] = result

    return result


def scan_all_chunks(
    client: LLMClient,
    config: LLMConfig,
    chunks: List[MarkdownChunk],
    cache: Optional[Dict[str, ChunkScanResult]] = None,
    sources_by_path: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[ChunkScanResult]:
    """Scan all chunks and return results in chunk order."""
    results: List[ChunkScanResult] = []
    scan_cache = cache if cache is not None else {}
    for chunk in chunks:
        source = (sources_by_path or {}).get(chunk.path)
        results.append(scan_chunk(client, config, chunk, cache=scan_cache, source=source))
    return results
