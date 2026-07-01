"""Documentation claim scanner with evidence-bound structured output (Issue #78).

Scans individual Markdown chunks for local documentation claims using a
small/cheap LLM. Each claim must include evidence line ranges within the
chunk. Claims outside the chunk are rejected. Results are cacheable by
chunk content hash and prompt/schema version.

This module performs local extraction only — it does not decide final
System Purpose or Core Capability hierarchy.

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
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .documentation_chunker import MarkdownChunk
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

PROMPT_VERSION = "claim-scanner-v1"
SCHEMA_VERSION = "claim-scanner-v1"

CLAIM_TYPES = {
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


class ClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int
    end_line: int


class DocumentationClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_type: str
    summary: str = Field(..., min_length=1, max_length=2000)
    evidence: ClaimEvidence
    confidence: float = Field(ge=0.0, le=1.0)
    mentioned_apis: List[str] = Field(default_factory=list)
    mentioned_symbols: List[str] = Field(default_factory=list)
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
) -> ChunkScanResult:
    """Scan a single chunk for documentation claims.

    If a cache dict is provided and contains a result for the chunk's
    content_hash + prompt/schema version, return the cached result.
    """
    cache_key = f"{chunk.content_hash}:{chunk.path}:{chunk.start_line}:{PROMPT_VERSION}:{SCHEMA_VERSION}"

    if cache is not None and cache_key in cache:
        cached = cache[cache_key]
        return ChunkScanResult(
            chunk_id=chunk.chunk_id,
            chunk_content_hash=chunk.content_hash,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            claims=cached.claims,
            is_cached=True,
        )

    if isinstance(client, MockLLMClient):
        return ChunkScanResult(
            chunk_id=chunk.chunk_id,
            chunk_content_hash=chunk.content_hash,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            error="Claim scanning requires a configured LLM; mock fallback is prohibited",
        )

    user_prompt = (
        f"## Chunk metadata\n"
        f"- path: {chunk.path}\n"
        f"- heading_path: {'/'.join(chunk.heading_path)}\n"
        f"- line range: {chunk.start_line}-{chunk.end_line}\n"
        f"- doc_role: {chunk.doc_role_hint}\n\n"
        f"## Chunk content\n\n{chunk.content}"
    )

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
    except LLMError as exc:
        return ChunkScanResult(
            chunk_id=chunk.chunk_id,
            chunk_content_hash=chunk.content_hash,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawScanResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return ChunkScanResult(
            chunk_id=chunk.chunk_id,
            chunk_content_hash=chunk.content_hash,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            error=f"Failed to parse scan response: {exc}",
        )

    claims: List[DocumentationClaim] = []
    for raw_claim in validated.claims:
        in_bounds, reason = _validate_claim_bounds(raw_claim, chunk)

        try:
            claim_type_valid = raw_claim.claim_type in CLAIM_TYPES
        except Exception:
            claim_type_valid = False

        if not claim_type_valid:
            claims.append(DocumentationClaim(
                claim_type="implementation_note",
                summary=raw_claim.summary or "Invalid claim type",
                evidence=ClaimEvidence(
                    path=chunk.path,
                    start_line=max(raw_claim.evidence_start_line, chunk.start_line),
                    end_line=min(raw_claim.evidence_end_line, chunk.end_line),
                ),
                confidence=0.0,
                mentioned_apis=raw_claim.mentioned_apis,
                mentioned_symbols=raw_claim.mentioned_symbols,
                is_valid=False,
                invalid_reason=f"Invalid claim_type: {raw_claim.claim_type}",
            ))
            continue

        if not in_bounds:
            claims.append(DocumentationClaim(
                claim_type=raw_claim.claim_type,
                summary=raw_claim.summary or "Out of bounds claim",
                evidence=ClaimEvidence(
                    path=chunk.path,
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
                    start_line=raw_claim.evidence_start_line,
                    end_line=raw_claim.evidence_end_line,
                ),
                confidence=clamped_confidence,
                mentioned_apis=merged_apis,
                mentioned_symbols=merged_symbols,
            )
        except ValidationError as exc:
            claims.append(DocumentationClaim(
                claim_type="implementation_note",
                summary=raw_claim.summary[:200] if raw_claim.summary else "Validation failed",
                evidence=ClaimEvidence(
                    path=chunk.path,
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
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
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
) -> List[ChunkScanResult]:
    """Scan all chunks and return results in chunk order."""
    results: List[ChunkScanResult] = []
    scan_cache = cache if cache is not None else {}
    for chunk in chunks:
        results.append(scan_chunk(client, config, chunk, cache=scan_cache))
    return results
