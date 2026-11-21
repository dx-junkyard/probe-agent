"""Draft generation for System Profile and Feature Map.

Uses the LLM layer to produce evidence-backed drafts from committed repository
content. Deterministic mock fixtures are available only when LLM_PROVIDER=mock.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .feature_registry import RegistryEntry, mint_feature_key, name_slug
from .git_ops import IndexedFile
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

# v3 / schema v2 (IK-0038): the model no longer writes feature_id. It answers
# each feature with {"same_as": "F<n>"} (a handle of the registry shown in the
# prompt) or {"new": true}; the system maps handles back to registry keys and
# mints keys for new features (app/feature_registry.py).
PROMPT_VERSION = "v3"
SCHEMA_VERSION = "v2"
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
    # IK-0038: what the model said about identity. ``same_as`` is the raw
    # handle (or key) it referenced; ``is_new`` its explicit "new" flag;
    # ``legacy_id`` a feature_id it still wrote (old contract, never minted).
    same_as: Optional[str] = None
    is_new: bool = False
    legacy_id: Optional[str] = None


@dataclass
class FeatureIdResolution:
    """How each drafted feature got its registry key (IK-0038; IK-0032 fields kept).

    ``reused``: keys matched to an existing registry entry. ``new``: keys
    minted this run. ``resolved``: kept for the IK-0032 response shape and
    always empty now (no slug rewriting). ``handle_matched``: ``{handle,
    feature_key}`` per matched feature. ``minted``: ``{feature_key,
    display_name}``. ``slug_matches``: safety net -- a ``new`` feature whose
    name slug equals an existing (registered / retired / historical) key; it
    is only logged, the model's "new" stands. ``dropped``: items removed by
    K-0001 normalisation (unknown handle, duplicate same_as). ``normalized``:
    adjustments that kept an item. ``not_referenced``: live keys this run did
    not mention (not retired automatically).
    """

    reused: List[str] = field(default_factory=list)
    resolved: List[Dict[str, str]] = field(default_factory=list)
    new: List[str] = field(default_factory=list)
    previous_run_id: Optional[int] = None
    handle_matched: List[Dict[str, str]] = field(default_factory=list)
    minted: List[Dict[str, str]] = field(default_factory=list)
    slug_matches: List[Dict[str, str]] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    normalized: List[Dict[str, Any]] = field(default_factory=list)
    not_referenced: List[str] = field(default_factory=list)


@dataclass
class GenerationResult:
    provider: str
    model: str
    is_mock: bool
    system_profile: Optional[SystemProfileDraft]
    features: List[FeatureDraft]
    error: Optional[str] = None
    id_resolution: Optional[FeatureIdResolution] = None
    # Registry rows to persist with the run: {feature_key: summary} for
    # matched entries and the minted entries (IK-0038).
    registry_matched: Dict[str, str] = field(default_factory=dict)
    registry_minted: List[Dict[str, str]] = field(default_factory=list)


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
            # Number every line so evidence ranges can be read off instead of
            # counted: unnumbered bodies made models overshoot, zero-base or
            # invert ranges and the whole draft was rejected (IK-0026). The
            # numbering must match line_counts (splitlines, trailing newline
            # not counted as a line).
            numbered = "\n".join(
                f"{n:4d}| {line}" for n, line in enumerate(text.splitlines(), 1)
            )
            parts.append(
                f"### File: {f.path} (type: {source_type})\n"
                f"<repository_file>\n{numbered}\n</repository_file>"
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
Each line of every file below is prefixed with its 1-based line number and
"| ". Use those numbers for start_line and end_line; do not count lines
yourself, and never cite a number larger than the last one shown for a file.

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
      "same_as": "F1 (handle of a registered feature) or null",
      "new": false,
      "name": "string",
      "summary": "string",
      "user_value": "string",
      "success_criteria": ["string"],
      "risks": ["string"],
      "evidence": [{{"path": "string", "start_line": int, "end_line": int, "summary": "string"}}]
    }}
  ]
}}

Feature identity: do NOT write a feature id. For each feature set exactly one
of "same_as" (the handle, e.g. "F3", of the registered feature below that
delivers the same user value) or "new": true (a feature not in the list).
Use each handle at most once. The system assigns and keeps feature ids.

{previous_features}Repository contents:

{file_context}"""

_PREVIOUS_FEATURES_TEMPLATE = """\
## Registered features

These features are registered for this system (handle | name | summary | main
evidence files). Experiments, probe plans and code links refer to them:
- When a feature you identify delivers the same user value as one listed here,
  answer "same_as": "<handle>".
- Answer "new": true only for a feature that is genuinely not listed.
- Do not split or merge listed features unless the repository contents require it.

{lines}

"""


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
        # Line numbers are a citation-precision problem, not a fabricated path
        # (IK-0026): models still miscount or invert ranges even with numbered
        # context. Normalise what can be normalised and only reject a range
        # that nothing in the file can back. Keep the raw values in the error
        # so a failed run says what the model actually wrote.
        line_count = line_counts[path]
        if start_line < 1:
            start_line = 1
        if end_line < start_line:
            start_line, end_line = end_line, start_line
        if start_line < 1 or start_line > line_count:
            raise DraftValidationError(
                "evidence line range exceeds snapshot content: "
                f"{path} ({e.get('start_line')}-{e.get('end_line')}, {line_count} lines)"
            )
        if end_line > line_count:
            end_line = line_count
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
        same_as = fd.get("same_as")
        same_as = str(same_as).strip() if same_as not in (None, "", False) else None
        new_flag = fd.get("new")
        is_new = new_flag is True or str(new_flag).strip().lower() == "true"
        legacy = str(fd.get("feature_id") or "").strip() or None
        features.append(
            FeatureDraft(
                feature_id="",
                name=_required_text(fd.get("name"), "feature.name"),
                summary=_required_text(fd.get("summary"), "feature.summary"),
                user_value=_required_text(fd.get("user_value"), "feature.user_value"),
                success_criteria=_string_list(
                    fd.get("success_criteria"), "feature.success_criteria"
                ),
                risks=_string_list(fd.get("risks"), "feature.risks"),
                evidence=_parse_evidence(fd.get("evidence"), line_counts),
                decision_method="reasoning_llm",
                same_as=same_as,
                is_new=is_new,
                legacy_id=legacy,
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
            feature_id="",
            legacy_id="documentation-overview",
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
            feature_id="",
            legacy_id="source-implementation",
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


_HANDLE_RE = re.compile(r"^\s*\[?\s*f?\s*(\d+)\s*\]?\s*$", re.IGNORECASE)


def _previous_features_block(registry: List[RegistryEntry]) -> str:
    if not registry:
        return ""
    lines = []
    for item in registry:
        paths = ", ".join(item.evidence_paths) or "none"
        summary = " ".join((item.summary or "").split())[:200] or "none"
        lines.append(
            f"- {item.handle} | name: {item.display_name} | summary: {summary} | evidence: {paths}"
        )
    return _PREVIOUS_FEATURES_TEMPLATE.format(lines="\n".join(lines))


def _resolve_reference(
    raw: str, by_handle: Dict[str, RegistryEntry], by_key: Dict[str, RegistryEntry]
) -> Optional[RegistryEntry]:
    """Normalise a same_as reference to a registry entry (K-0001).

    Accepts the handle in any case / with brackets ("f3", "[F3]", "3"), and
    the registry key itself (exact or slug-equal) when the model copied the
    key instead of the handle. Anything else is unresolvable.
    """
    match = _HANDLE_RE.match(raw)
    if match:
        return by_handle.get(f"F{int(match.group(1))}")
    if raw in by_key:
        return by_key[raw]
    slug = name_slug(raw)
    for key, entry in by_key.items():
        if name_slug(key) == slug:
            return entry
    return None


def resolve_feature_refs(
    features: List[FeatureDraft],
    registry: List[RegistryEntry],
    taken: Dict[str, str],
) -> Tuple[List[FeatureDraft], FeatureIdResolution]:
    """Map handles back to registry keys and mint keys for new features.

    Normalise -> resolve -> drop only the bad item (K-0001): an unknown handle
    or a second feature claiming an already-used handle is dropped and
    recorded; the other features are kept. Features with neither same_as nor
    new are treated as new, except a legacy feature_id equal to a live key,
    which is taken as a reference to that key (recorded in ``normalized``).
    ``taken`` maps every key the System has used to its kind
    (registered / retired / historical); minted keys avoid all of them.
    """
    resolution = FeatureIdResolution()
    by_handle = {entry.handle: entry for entry in registry}
    by_key = {entry.feature_key: entry for entry in registry}
    used_keys: Dict[str, int] = {}
    taken_now = set(taken)
    kept: List[FeatureDraft] = []

    for index, feature in enumerate(features):
        entry: Optional[RegistryEntry] = None
        if feature.same_as is not None:
            entry = _resolve_reference(feature.same_as, by_handle, by_key)
            if entry is None:
                resolution.dropped.append({
                    "index": index, "name": feature.name,
                    "same_as": feature.same_as, "reason": "unknown_handle",
                })
                continue
            if feature.is_new:
                resolution.normalized.append({
                    "index": index, "name": feature.name,
                    "note": "same_as and new both set; same_as used",
                })
        elif not feature.is_new and feature.legacy_id and feature.legacy_id in by_key:
            entry = by_key[feature.legacy_id]
            resolution.normalized.append({
                "index": index, "name": feature.name,
                "note": f"legacy feature_id {feature.legacy_id!r} taken as same_as",
            })

        if entry is not None:
            if entry.feature_key in used_keys:
                resolution.dropped.append({
                    "index": index, "name": feature.name,
                    "same_as": feature.same_as or entry.feature_key,
                    "reason": "duplicate_same_as",
                    "kept_index": used_keys[entry.feature_key],
                })
                continue
            used_keys[entry.feature_key] = index
            feature.feature_id = entry.feature_key
            resolution.reused.append(entry.feature_key)
            resolution.handle_matched.append(
                {"handle": entry.handle, "feature_key": entry.feature_key}
            )
            kept.append(feature)
            continue

        if not feature.is_new:
            resolution.normalized.append({
                "index": index, "name": feature.name,
                "note": "neither same_as nor new; treated as new",
            })
        key = mint_feature_key(feature.name, taken_now)
        taken_now.add(key)
        used_keys[key] = index
        feature.feature_id = key
        resolution.new.append(key)
        resolution.minted.append({"feature_key": key, "display_name": feature.name})
        slug = name_slug(feature.name)
        if slug and slug in taken:
            # Safety net only: the model said "new"; log that the name looks
            # like an existing key so a human can merge it if it is the same.
            resolution.slug_matches.append({
                "feature_key": key, "name_slug": slug,
                "matches": slug, "kind": taken[slug],
            })
        kept.append(feature)

    resolution.not_referenced = [
        entry.feature_key for entry in registry if entry.feature_key not in used_keys
    ]
    return kept, resolution


def generate_drafts(
    client: LLMClient,
    config: LLMConfig,
    files: List[IndexedFile],
    registry: Optional[List[RegistryEntry]] = None,
    taken: Optional[Dict[str, str]] = None,
) -> GenerationResult:
    registry = registry or []
    taken = taken or {}
    is_mock = isinstance(client, MockLLMClient)
    if is_mock:
        sp, features = _mock_drafts(files)
        features, resolution = resolve_feature_refs(features, registry, taken)
        return _with_registry(GenerationResult(
            provider="mock",
            model="mock",
            is_mock=True,
            system_profile=sp,
            features=features,
            id_resolution=resolution,
        ))

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
    prompt = _DRAFT_PROMPT_TEMPLATE.format(
        file_context=file_context,
        previous_features=_previous_features_block(registry),
    )

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
        features, resolution = resolve_feature_refs(features, registry, taken)
        if not features:
            reasons = sorted({d["reason"] for d in resolution.dropped})
            raise DraftValidationError(
                "no feature left after resolving feature references "
                f"({', '.join(reasons) or 'none'})"
            )
    except (json.JSONDecodeError, DraftValidationError, KeyError, TypeError) as exc:
        return GenerationResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            system_profile=None,
            features=[],
            error=f"Failed to parse LLM response: {exc}",
        )

    return _with_registry(GenerationResult(
        provider=config.provider,
        model=config.model,
        is_mock=False,
        system_profile=sp,
        features=features,
        id_resolution=resolution,
    ))


def _with_registry(result: GenerationResult) -> GenerationResult:
    """Fill the registry rows a completed run must persist."""
    resolution = result.id_resolution
    if resolution is None:
        return result
    summaries = {f.feature_id: f.summary for f in result.features}
    result.registry_matched = {key: summaries.get(key, "") for key in resolution.reused}
    result.registry_minted = [
        {**item, "summary": summaries.get(item["feature_key"], "")}
        for item in resolution.minted
    ]
    return result
