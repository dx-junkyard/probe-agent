"""Draft generation for System Profile and Feature Map.

Uses the LLM layer to produce evidence-backed drafts from committed repository
content. Deterministic mock fixtures are available only when LLM_PROVIDER=mock.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .git_ops import IndexedFile
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"
DEFAULT_MAX_OUTPUT_TOKENS = 128_000


@dataclass
class EvidenceItem:
    path: str
    start_line: int
    end_line: int
    summary: str


@dataclass
class SystemProfileDraft:
    name: str
    purpose: str
    target_users: List[str]
    stakeholder_value: str
    constraints: List[str]
    success_criteria: List[str]
    evidence: List[EvidenceItem]


@dataclass
class FeatureDraft:
    feature_id: str
    name: str
    summary: str
    user_value: str
    success_criteria: List[str]
    risks: List[str]
    evidence: List[EvidenceItem]
    decision_method: str = "reasoning_llm"


@dataclass
class GenerationResult:
    provider: str
    model: str
    is_mock: bool
    system_profile: Optional[SystemProfileDraft]
    features: List[FeatureDraft]
    error: Optional[str] = None


class DraftValidationError(ValueError):
    pass


def _build_file_context(
    files: List[IndexedFile], max_chars: int = 200_000
) -> Tuple[str, List[IndexedFile]]:
    sections: Dict[str, List[IndexedFile]] = {}
    for f in files:
        sections.setdefault(f.source_type, []).append(f)

    parts = []
    selected = []
    total = 0
    for source_type in ["documentation", "source", "test", "configuration"]:
        for f in sections.get(source_type, []):
            if b"\x00" in f.content:
                continue
            try:
                text = f.content.decode("utf-8", errors="replace")
            except Exception:
                continue
            if total + len(text) > max_chars:
                continue
            total += len(text)
            selected.append(f)
            parts.append(
                f"### File: {f.path} (type: {source_type})\n"
                f"<repository_file>\n{text}\n</repository_file>"
            )

    return "\n\n".join(parts), selected


_SYSTEM_PROMPT = """\
You are a software analysis assistant. You analyze repository contents and
produce structured JSON output. Every claim must include evidence referencing
specific files and line ranges from the repository snapshot provided."""

_DRAFT_PROMPT_TEMPLATE = """\
Analyze the following repository snapshot and produce:
1. A System Profile Draft describing the system's purpose, users, value,
   constraints, and success criteria.
2. A Feature Map listing user-facing features with summaries, user value,
   success criteria, risks, and evidence.

Every claim in the System Profile and every Feature MUST include evidence
with: path (file path), start_line (1-based), end_line (1-based), and summary.

Respond with ONLY valid JSON matching this schema:
{{
  "system_profile": {{
    "name": "string",
    "purpose": "string",
    "target_users": ["string"],
    "stakeholder_value": "string",
    "constraints": ["string"],
    "success_criteria": ["string"],
    "evidence": [{{"path": "string", "start_line": int, "end_line": int, "summary": "string"}}]
  }},
  "features": [
    {{
      "feature_id": "string (kebab-case)",
      "name": "string",
      "summary": "string",
      "user_value": "string",
      "success_criteria": ["string"],
      "risks": ["string"],
      "evidence": [{{"path": "string", "start_line": int, "end_line": int, "summary": "string"}}]
    }}
  ]
}}

Repository contents:

{file_context}"""


def _parse_evidence(raw: Any, line_counts: Dict[str, int]) -> List[EvidenceItem]:
    if not isinstance(raw, list):
        raise DraftValidationError("evidence must be an array")
    items = []
    for e in raw:
        if not isinstance(e, dict):
            raise DraftValidationError("evidence items must be objects")
        path = str(e.get("path", ""))
        if not path:
            raise DraftValidationError("evidence path is required")
        if path not in line_counts:
            raise DraftValidationError(f"evidence path is not in snapshot: {path}")
        try:
            start_line = int(e.get("start_line"))
            end_line = int(e.get("end_line"))
        except (TypeError, ValueError) as exc:
            raise DraftValidationError(
                f"evidence line range must be integers: {path}"
            ) from exc
        if start_line < 1 or end_line < start_line:
            raise DraftValidationError(f"invalid evidence line range: {path}")
        if end_line > line_counts[path]:
            raise DraftValidationError(
                f"evidence line range exceeds snapshot content: {path}"
            )
        summary = str(e.get("summary", "")).strip()
        if not summary:
            raise DraftValidationError(f"evidence summary is required: {path}")
        items.append(
            EvidenceItem(
                path=path,
                start_line=start_line,
                end_line=end_line,
                summary=summary,
            )
        )
    if not items:
        raise DraftValidationError("at least one evidence item is required")
    return items


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DraftValidationError(f"{field_name} is required")
    return text


def _string_list(value: Any, field_name: str) -> List[str]:
    if not isinstance(value, list):
        raise DraftValidationError(f"{field_name} must be an array")
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_draft_response(
    raw_json: str, line_counts: Dict[str, int]
) -> Tuple[Optional[SystemProfileDraft], List[FeatureDraft]]:
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise DraftValidationError("LLM response must be an object")

    sp_data = data.get("system_profile")
    if not isinstance(sp_data, dict):
        raise DraftValidationError("system_profile must be an object")
    sp_draft = SystemProfileDraft(
        name=_required_text(sp_data.get("name"), "system_profile.name"),
        purpose=_required_text(sp_data.get("purpose"), "system_profile.purpose"),
        target_users=_string_list(
            sp_data.get("target_users"), "system_profile.target_users"
        ),
        stakeholder_value=_required_text(
            sp_data.get("stakeholder_value"), "system_profile.stakeholder_value"
        ),
        constraints=_string_list(
            sp_data.get("constraints"), "system_profile.constraints"
        ),
        success_criteria=_string_list(
            sp_data.get("success_criteria"), "system_profile.success_criteria"
        ),
        evidence=_parse_evidence(sp_data.get("evidence"), line_counts),
    )

    features_data = data.get("features", [])
    if not isinstance(features_data, list) or not features_data:
        raise DraftValidationError("features must be a non-empty array")
    features = []
    for fd in features_data:
        if not isinstance(fd, dict):
            raise DraftValidationError("feature items must be objects")
        features.append(
            FeatureDraft(
                feature_id=_required_text(fd.get("feature_id"), "feature_id"),
                name=_required_text(fd.get("name"), "feature.name"),
                summary=_required_text(fd.get("summary"), "feature.summary"),
                user_value=_required_text(fd.get("user_value"), "feature.user_value"),
                success_criteria=_string_list(
                    fd.get("success_criteria"), "feature.success_criteria"
                ),
                risks=_string_list(fd.get("risks"), "feature.risks"),
                evidence=_parse_evidence(fd.get("evidence"), line_counts),
                decision_method="reasoning_llm",
            )
        )

    return sp_draft, features


def _mock_drafts(files: List[IndexedFile]) -> Tuple[SystemProfileDraft, List[FeatureDraft]]:
    doc_files = [f for f in files if f.source_type == "documentation"]
    src_files = [f for f in files if f.source_type == "source"]
    first_doc = doc_files[0].path if doc_files else (files[0].path if files else "unknown")

    sp = SystemProfileDraft(
        name="System Profile (mock draft)",
        purpose="Drafted from committed documentation by mock provider.",
        target_users=["developers"],
        stakeholder_value="Evidence-based system understanding.",
        constraints=["Mock provider: no real LLM analysis performed"],
        success_criteria=["Repository snapshot created", "Evidence attached to claims"],
        evidence=[EvidenceItem(
            path=first_doc,
            start_line=1,
            end_line=min(10, 1),
            summary="Mock evidence from first documentation file.",
        )],
    )

    features = []
    if doc_files:
        features.append(FeatureDraft(
            feature_id="documentation-overview",
            name="Documentation Overview",
            summary="The repository contains documentation files.",
            user_value="Developers can understand the system from docs.",
            success_criteria=["Documentation files are indexed"],
            risks=["Mock analysis may miss real features"],
            evidence=[EvidenceItem(
                path=doc_files[0].path,
                start_line=1,
                end_line=1,
                summary="Documentation file present in snapshot.",
            )],
            decision_method="reasoning_llm",
        ))
    if src_files:
        features.append(FeatureDraft(
            feature_id="source-implementation",
            name="Source Implementation",
            summary="The repository contains source code.",
            user_value="Core functionality is implemented in source files.",
            success_criteria=["Source files are indexed"],
            risks=["Mock analysis may miss real features"],
            evidence=[EvidenceItem(
                path=src_files[0].path,
                start_line=1,
                end_line=1,
                summary="Source file present in snapshot.",
            )],
            decision_method="reasoning_llm",
        ))

    return sp, features


def generate_drafts(
    client: LLMClient,
    config: LLMConfig,
    files: List[IndexedFile],
) -> GenerationResult:
    is_mock = isinstance(client, MockLLMClient)
    if is_mock:
        sp, features = _mock_drafts(files)
        return GenerationResult(
            provider="mock",
            model="mock",
            is_mock=True,
            system_profile=sp,
            features=features,
        )

    file_context, context_files = _build_file_context(files)
    if not file_context:
        return GenerationResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            system_profile=None,
            features=[],
            error="Snapshot contains no readable text content",
        )
    prompt = _DRAFT_PROMPT_TEMPLATE.format(file_context=file_context)

    try:
        max_output_tokens = int(
            os.getenv(
                "INTELLIGENCE_MAX_OUTPUT_TOKENS",
                str(DEFAULT_MAX_OUTPUT_TOKENS),
            )
        )
        if max_output_tokens < 1:
            raise ValueError("INTELLIGENCE_MAX_OUTPUT_TOKENS must be positive")
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=max_output_tokens,
        )
    except (LLMError, ValueError) as exc:
        return GenerationResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            system_profile=None,
            features=[],
            error=str(exc),
        )

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        line_counts = {
            file.path: max(1, len(file.content.decode("utf-8", errors="replace").splitlines()))
            for file in context_files
        }
        sp, features = _parse_draft_response(cleaned, line_counts)
    except (json.JSONDecodeError, DraftValidationError, KeyError, TypeError) as exc:
        return GenerationResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            system_profile=None,
            features=[],
            error=f"Failed to parse LLM response: {exc}",
        )

    return GenerationResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        system_profile=sp,
        features=features,
    )
