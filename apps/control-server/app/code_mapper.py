"""Feature-to-Code mapping using reasoning model.

Generates FeatureCodeLink candidates by sending feature context and code
symbols to a reasoning model.  The mock provider returns deterministic
results for testing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .code_indexer import CodeSymbol
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"

LinkSource = str  # "reasoning_llm" | "manual"
ReviewStatus = str  # "proposed" | "accepted" | "rejected"


@dataclass
class FeatureCodeLinkResult:
    feature_id: str
    symbol_qualified_name: str
    symbol_path: str
    relation_reason: str
    confidence: float
    source: str  # "reasoning_llm"


@dataclass
class MappingResult:
    provider: str
    model: str
    is_mock: bool
    links: List[FeatureCodeLinkResult]
    error: Optional[str] = None


class MappingValidationError(ValueError):
    pass


@dataclass
class FeatureContext:
    feature_id: str
    name: str
    summary: str
    user_value: str
    success_criteria: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    evidence_keywords: List[str] = field(default_factory=list)


def _build_symbol_context(symbols: List[CodeSymbol], max_symbols: int = 500) -> str:
    parts = []
    for sym in symbols[:max_symbols]:
        decorators = ", ".join(sym.decorators) if sym.decorators else ""
        route = ""
        if sym.route_path:
            route = f" route={sym.route_method or '?'} {sym.route_path}"
        doc = f' doc="{sym.docstring[:100]}"' if sym.docstring else ""
        test = " [test]" if sym.is_test else ""
        pydantic = " [pydantic]" if sym.is_pydantic_model else ""
        component = f" component_id={sym.component_id}" if sym.component_id else ""
        imports = f" imports={','.join(sym.imports)}" if sym.imports else ""
        parts.append(
            f"- {sym.qualified_name} ({sym.kind}) "
            f"in {sym.path}:{sym.start_line}-{sym.end_line}"
            f"{' @' + decorators if decorators else ''}"
            f"{route}{doc}{test}{pydantic}{component}{imports}"
        )
    return "\n".join(parts)


def _build_feature_context(features: List[FeatureContext]) -> str:
    parts = []
    for f in features:
        keywords = ", ".join(f.evidence_keywords) if f.evidence_keywords else "none"
        parts.append(
            f"- Feature: {f.name} (id: {f.feature_id})\n"
            f"  Summary: {f.summary}\n"
            f"  User value: {f.user_value}\n"
            f"  Success criteria: {', '.join(f.success_criteria) or 'none'}\n"
            f"  Risks/core-flow constraints: {', '.join(f.risks) or 'none'}\n"
            f"  Evidence keywords: {keywords}"
        )
    return "\n".join(parts)


_SYSTEM_PROMPT = """\
You are a software analysis assistant. You map user-facing features to source
code symbols. Each mapping must include a clear reason and confidence score."""

_MAPPING_PROMPT_TEMPLATE = """\
Given the following features and code symbols from a Python repository,
identify which symbols implement or support each feature.

For each link, provide:
- feature_id: the feature being mapped
- symbol_qualified_name: the exact qualified name from the symbol list
- symbol_path: the file path of the symbol
- relation_reason: why this symbol is related to the feature
- confidence: 0.0 to 1.0 (1.0 = definitive implementation, 0.5 = supporting, <0.3 = weak)

Only include links where there is a meaningful relationship. Prefer fewer
high-confidence links over many weak ones. Do not invent symbol names that
are not in the list.

Respond with ONLY valid JSON matching this schema:
{{
  "links": [
    {{
      "feature_id": "string",
      "symbol_qualified_name": "string",
      "symbol_path": "string",
      "relation_reason": "string",
      "confidence": number
    }}
  ]
}}

## Features

{feature_context}

## Code Symbols

{symbol_context}"""


def _parse_mapping_response(
    raw_json: str,
    valid_feature_ids: set,
    valid_symbols: set[Tuple[str, str]],
) -> List[FeatureCodeLinkResult]:
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise MappingValidationError("LLM response must be an object")

    links_data = data.get("links", [])
    if not isinstance(links_data, list):
        raise MappingValidationError("links must be an array")

    results = []
    seen = set()
    for item in links_data:
        if not isinstance(item, dict):
            raise MappingValidationError("link items must be objects")

        feature_id = str(item.get("feature_id", "")).strip()
        if not feature_id:
            raise MappingValidationError("feature_id is required")
        if feature_id not in valid_feature_ids:
            raise MappingValidationError(f"unknown feature_id: {feature_id}")

        sym_name = str(item.get("symbol_qualified_name", "")).strip()
        if not sym_name:
            raise MappingValidationError("symbol_qualified_name is required")
        response_path = str(item.get("symbol_path", "")).strip()
        if (response_path, sym_name) not in valid_symbols:
            raise MappingValidationError(
                f"unknown symbol: {response_path}:{sym_name}"
            )

        sym_path = response_path

        reason = str(item.get("relation_reason", "")).strip()
        if not reason:
            raise MappingValidationError("relation_reason is required")

        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError) as exc:
            raise MappingValidationError("confidence must be a number") from exc
        if not 0.0 <= confidence <= 1.0:
            raise MappingValidationError("confidence must be between 0 and 1")

        key = (feature_id, sym_path, sym_name)
        if key in seen:
            continue
        seen.add(key)

        results.append(FeatureCodeLinkResult(
            feature_id=feature_id,
            symbol_qualified_name=sym_name,
            symbol_path=sym_path,
            relation_reason=reason,
            confidence=confidence,
            source="reasoning_llm",
        ))

    return results


def generate_code_mapping(
    client: LLMClient,
    config: LLMConfig,
    features: List[FeatureContext],
    symbols: List[CodeSymbol],
) -> MappingResult:
    is_mock = isinstance(client, MockLLMClient)
    if is_mock:
        return MappingResult(
            provider="mock",
            model="mock",
            is_mock=True,
            links=[],
            error=(
                "Feature-to-code mapping requires a real reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    if not features:
        return MappingResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            links=[],
            error="No features to map",
        )

    if not symbols:
        return MappingResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            links=[],
            error="No code symbols to map against",
        )

    symbol_context = _build_symbol_context(symbols)
    feature_context = _build_feature_context(features)
    prompt = _MAPPING_PROMPT_TEMPLATE.format(
        feature_context=feature_context,
        symbol_context=symbol_context,
    )

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
        return MappingResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            links=[],
            error=str(exc),
        )

    valid_feature_ids = {f.feature_id for f in features}
    valid_symbols = {(s.path, s.qualified_name) for s in symbols}

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        links = _parse_mapping_response(cleaned, valid_feature_ids, valid_symbols)
    except (json.JSONDecodeError, MappingValidationError, KeyError, TypeError) as exc:
        return MappingResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            links=[],
            error=f"Failed to parse LLM response: {exc}",
        )

    return MappingResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        links=links,
    )
