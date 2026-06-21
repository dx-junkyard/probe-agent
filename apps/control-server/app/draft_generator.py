"""Draft generation for System Profile and Feature Map.

Uses the LLM layer to produce evidence-backed drafts from committed repository
content.  Falls back to deterministic mock fixtures when LLM_PROVIDER=mock.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .git_ops import IndexedFile
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

PROMPT_VERSION = "v1"
SCHEMA_VERSION = "v1"


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


def _build_file_context(files: List[IndexedFile], max_chars: int = 200_000) -> str:
    sections: Dict[str, List[IndexedFile]] = {}
    for f in files:
        sections.setdefault(f.source_type, []).append(f)

    parts = []
    total = 0
    for source_type in ["documentation", "source", "test", "configuration"]:
        for f in sections.get(source_type, []):
            try:
                text = f.content.decode("utf-8", errors="replace")
            except Exception:
                continue
            if total + len(text) > max_chars:
                continue
            total += len(text)
            parts.append(f"### File: {f.path} (type: {source_type})\n```\n{text}\n```")

    return "\n\n".join(parts)


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


def _parse_evidence(raw: Any) -> List[EvidenceItem]:
    if not isinstance(raw, list):
        return []
    items = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        path = e.get("path", "")
        if not path:
            continue
        items.append(EvidenceItem(
            path=str(path),
            start_line=int(e.get("start_line", 0)),
            end_line=int(e.get("end_line", 0)),
            summary=str(e.get("summary", "")),
        ))
    return items


def _validate_evidence(
    evidence: List[EvidenceItem], file_paths: set
) -> List[EvidenceItem]:
    return [e for e in evidence if e.path in file_paths]


def _parse_draft_response(
    raw_json: str, file_paths: set
) -> Tuple[Optional[SystemProfileDraft], List[FeatureDraft]]:
    data = json.loads(raw_json)

    sp_data = data.get("system_profile")
    sp_draft = None
    if isinstance(sp_data, dict):
        evidence = _validate_evidence(
            _parse_evidence(sp_data.get("evidence")), file_paths
        )
        sp_draft = SystemProfileDraft(
            name=str(sp_data.get("name", "")),
            purpose=str(sp_data.get("purpose", "")),
            target_users=[str(u) for u in sp_data.get("target_users", []) if u],
            stakeholder_value=str(sp_data.get("stakeholder_value", "")),
            constraints=[str(c) for c in sp_data.get("constraints", []) if c],
            success_criteria=[str(s) for s in sp_data.get("success_criteria", []) if s],
            evidence=evidence,
        )

    features_data = data.get("features", [])
    features = []
    if isinstance(features_data, list):
        for fd in features_data:
            if not isinstance(fd, dict):
                continue
            evidence = _validate_evidence(
                _parse_evidence(fd.get("evidence")), file_paths
            )
            features.append(FeatureDraft(
                feature_id=str(fd.get("feature_id", "")),
                name=str(fd.get("name", "")),
                summary=str(fd.get("summary", "")),
                user_value=str(fd.get("user_value", "")),
                success_criteria=[str(s) for s in fd.get("success_criteria", []) if s],
                risks=[str(r) for r in fd.get("risks", []) if r],
                evidence=evidence,
                decision_method="reasoning_llm",
            ))

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
    file_paths = {f.path for f in files}

    if is_mock:
        sp, features = _mock_drafts(files)
        return GenerationResult(
            provider="mock",
            model="mock",
            is_mock=True,
            system_profile=sp,
            features=features,
        )

    file_context = _build_file_context(files)
    prompt = _DRAFT_PROMPT_TEMPLATE.format(file_context=file_context)

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
        sp, features = _parse_draft_response(cleaned, file_paths)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
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
