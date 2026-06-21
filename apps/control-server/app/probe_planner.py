"""Probe Plan generation using reasoning model with deterministic safety denylist.

Generates ProbePoint candidates from features and accepted code links.
The safety denylist is deterministic and overrides LLM output — it cannot be
unlocked by model suggestions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .code_indexer import CodeSymbol
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"

SAFETY_DENYLIST_KEYWORDS: List[str] = [
    "payment", "pay", "billing", "charge", "invoice",
    "email", "send_email", "send_mail", "smtp", "mailer",
    "delete_file", "remove_file", "rmtree", "shutil.rmtree", "os.remove", "os.unlink",
    "drop_table", "truncate", "delete_all", "bulk_delete",
    "db_write", "db.commit", "session.commit", "cursor.execute",
    "deploy", "push_to_production", "publish",
    "credential", "secret", "token_gen", "api_key_gen",
    "auth_login", "authenticate", "verify_password",
    "transfer", "withdraw", "deposit",
    "webhook_send", "notify_external",
]

SAFETY_DENYLIST_PATTERNS: List[re.Pattern] = [
    re.compile(r"(?i)\b(pay|bill|charge|invoice|refund)\b"),
    re.compile(r"(?i)\b(send_?email|send_?mail|smtp)\b"),
    re.compile(r"(?i)\b(delete|remove|drop|truncate)_(file|table|database|collection)\b"),
    re.compile(r"(?i)\b(deploy|publish|push_to_prod)\b"),
    re.compile(r"(?i)\b(credential|secret|api_?key)_(gen|create|rotate)\b"),
]


@dataclass
class ProbePointResult:
    component_id: str
    feature_id: str
    path: str
    symbol: str
    line_start: int
    line_end: int
    reason: str
    recommended_mode: str  # "trace" | "shadow"
    side_effect_risk: str  # "low" | "medium" | "high"
    replayability: str
    denylist_hit: Optional[str] = None


@dataclass
class PlanResult:
    provider: str
    model: str
    is_mock: bool
    feature_id: str
    objective: str
    probe_points: List[ProbePointResult]
    avoid_reasons: List[str]
    error: Optional[str] = None


class PlanValidationError(ValueError):
    pass


@dataclass
class AcceptedLink:
    feature_id: str
    symbol_qualified_name: str
    symbol_path: str
    symbol_kind: str
    start_line: int
    end_line: int
    decorators: List[str] = field(default_factory=list)
    component_id: Optional[str] = None
    is_test: bool = False
    docstring: Optional[str] = None
    relation_reason: str = ""


def check_denylist(symbol_name: str, docstring: Optional[str] = None) -> Optional[str]:
    lower_name = symbol_name.lower()
    for keyword in SAFETY_DENYLIST_KEYWORDS:
        if keyword.lower() in lower_name:
            return f"symbol name matches safety denylist keyword: {keyword}"
    combined = lower_name + " " + (docstring or "").lower()
    for pattern in SAFETY_DENYLIST_PATTERNS:
        match = pattern.search(combined)
        if match:
            return f"matches safety denylist pattern: {match.group()}"
    return None


def _build_link_context(links: List[AcceptedLink]) -> str:
    parts = []
    for link in links:
        decorators = ", ".join(link.decorators) if link.decorators else "none"
        doc = f' doc="{link.docstring[:100]}"' if link.docstring else ""
        component = f" component_id={link.component_id}" if link.component_id else ""
        test = " [test]" if link.is_test else ""
        parts.append(
            f"- {link.symbol_qualified_name} ({link.symbol_kind}) "
            f"in {link.symbol_path}:{link.start_line}-{link.end_line} "
            f"decorators=[{decorators}]{doc}{component}{test}\n"
            f"  Mapping reason: {link.relation_reason}"
        )
    return "\n".join(parts)


_SYSTEM_PROMPT = """\
You are a software instrumentation planner. You decide which code symbols
should be instrumented with @probe decorators for runtime observation.
Your recommendations must include clear reasons, side-effect risk assessment,
and replayability analysis."""

_PLAN_PROMPT_TEMPLATE = """\
Given the following feature and its accepted code-to-feature links, propose
which symbols should be instrumented with @probe for runtime observation.

Feature: {feature_name} (id: {feature_id})
Summary: {feature_summary}
User value: {feature_user_value}
Success criteria: {feature_success_criteria}
Risks: {feature_risks}

Accepted code links:
{link_context}

For each proposed probe point:
- component_id: a short kebab-case identifier for the component
- symbol_qualified_name: exact qualified name from the accepted links
- symbol_path: file path of the symbol
- reason: why this symbol should be probed
- recommended_mode: "trace" (default) or "shadow" (only if comparison is needed and safe)
- side_effect_risk: "low" | "medium" | "high"
  - DB writes, payments, emails, file deletions, external API calls = high
  - Logging, caching, internal state = medium
  - Pure computation, data transformation, read-only = low
- replayability: brief assessment of whether the function can be safely called
  multiple times with the same input

Also provide reasons for symbols that should be AVOIDED (e.g., side effects,
credentials, payments).

Respond with ONLY valid JSON:
{{
  "objective": "string describing the observation goal",
  "probe_points": [
    {{
      "component_id": "string",
      "symbol_qualified_name": "string",
      "symbol_path": "string",
      "reason": "string",
      "recommended_mode": "trace",
      "side_effect_risk": "low",
      "replayability": "string"
    }}
  ],
  "avoid_reasons": ["string describing what to avoid and why"]
}}"""


def _parse_plan_response(
    raw_json: str,
    feature_id: str,
    valid_symbols: Dict[Tuple[str, str], AcceptedLink],
) -> Tuple[str, List[ProbePointResult], List[str]]:
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise PlanValidationError("LLM response must be an object")

    objective = str(data.get("objective", "")).strip()
    if not objective:
        raise PlanValidationError("objective is required")

    points_data = data.get("probe_points", [])
    if not isinstance(points_data, list):
        raise PlanValidationError("probe_points must be an array")

    avoid = data.get("avoid_reasons", [])
    if not isinstance(avoid, list):
        raise PlanValidationError("avoid_reasons must be an array")
    avoid_reasons = [str(r).strip() for r in avoid if str(r).strip()]

    results = []
    seen = set()
    for item in points_data:
        if not isinstance(item, dict):
            raise PlanValidationError("probe_point items must be objects")

        sym_name = str(item.get("symbol_qualified_name", "")).strip()
        sym_path = str(item.get("symbol_path", "")).strip()
        if not sym_name or not sym_path:
            raise PlanValidationError("symbol_qualified_name and symbol_path are required")

        key = (sym_path, sym_name)
        if key not in valid_symbols:
            raise PlanValidationError(f"unknown symbol: {sym_path}:{sym_name}")
        if key in seen:
            continue
        seen.add(key)

        link = valid_symbols[key]

        component_id = str(item.get("component_id", "")).strip()
        if not component_id:
            raise PlanValidationError("component_id is required")

        reason = str(item.get("reason", "")).strip()
        if not reason:
            raise PlanValidationError("reason is required")

        mode = str(item.get("recommended_mode", "trace")).strip().lower()
        if mode not in ("trace", "shadow"):
            mode = "trace"

        risk = str(item.get("side_effect_risk", "low")).strip().lower()
        if risk not in ("low", "medium", "high"):
            risk = "low"

        replayability = str(item.get("replayability", "")).strip()
        if not replayability:
            raise PlanValidationError("replayability is required")

        denylist_hit = check_denylist(sym_name, link.docstring)

        results.append(ProbePointResult(
            component_id=component_id,
            feature_id=feature_id,
            path=sym_path,
            symbol=sym_name,
            line_start=link.start_line,
            line_end=link.end_line,
            reason=reason,
            recommended_mode=mode,
            side_effect_risk="high" if denylist_hit else risk,
            replayability=replayability,
            denylist_hit=denylist_hit,
        ))

    return objective, results, avoid_reasons


def generate_probe_plan(
    client: LLMClient,
    config: LLMConfig,
    feature_id: str,
    feature_name: str,
    feature_summary: str,
    feature_user_value: str,
    feature_success_criteria: List[str],
    feature_risks: List[str],
    accepted_links: List[AcceptedLink],
) -> PlanResult:
    is_mock = isinstance(client, MockLLMClient)
    if is_mock:
        return PlanResult(
            provider="mock",
            model="mock",
            is_mock=True,
            feature_id=feature_id,
            objective="",
            probe_points=[],
            avoid_reasons=[],
            error=(
                "Probe plan generation requires a real reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    if not accepted_links:
        return PlanResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            feature_id=feature_id,
            objective="",
            probe_points=[],
            avoid_reasons=[],
            error="No accepted code links for this feature",
        )

    link_context = _build_link_context(accepted_links)
    prompt = _PLAN_PROMPT_TEMPLATE.format(
        feature_id=feature_id,
        feature_name=feature_name,
        feature_summary=feature_summary,
        feature_user_value=feature_user_value,
        feature_success_criteria=", ".join(feature_success_criteria) or "none",
        feature_risks=", ".join(feature_risks) or "none",
        link_context=link_context,
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
        return PlanResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            feature_id=feature_id,
            objective="",
            probe_points=[],
            avoid_reasons=[],
            error=str(exc),
        )

    valid_symbols = {
        (link.symbol_path, link.symbol_qualified_name): link
        for link in accepted_links
    }

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        objective, points, avoid_reasons = _parse_plan_response(
            cleaned, feature_id, valid_symbols,
        )
    except (json.JSONDecodeError, PlanValidationError, KeyError, TypeError) as exc:
        return PlanResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            feature_id=feature_id,
            objective="",
            probe_points=[],
            avoid_reasons=[],
            error=f"Failed to parse LLM response: {exc}",
        )

    return PlanResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        feature_id=feature_id,
        objective=objective,
        probe_points=points,
        avoid_reasons=avoid_reasons,
    )
