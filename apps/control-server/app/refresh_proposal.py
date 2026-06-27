"""Reasoning-model explanation refresh proposals (Issue #59).

Issue #57 deterministically detects when a source-backed explanation should be
reviewed (a hash drifted). Detection alone does not help a developer update the
explanation layer. This module closes that loop with an explicit, reviewable
refresh proposal: given a stale hierarchy node / API role card, it builds a
context pack from

- the old source-authored explanation block,
- the changed source anchors and old/new hashes,
- the current source snippet from the pinned snapshot, and
- deterministic flow/API structural facts,

then asks a reasoning model to *propose* updated explanation wording or
metadata.

It honours the codebase rules:

- The open-ended judgment (what wording/metadata should change) is made by a
  reasoning model; mock and non-reasoning models fail closed with no heuristic
  fallback (CLAUDE.md principle 6 / reasoning-llm skill).
- The proposal is a **suggestion only**. It never edits the target repository.
  A developer must review it and update the source docstring by hand; the next
  snapshot re-indexes the corrected explanation.
- Proposed enum metadata is validated against the same explicit finite
  vocabulary used when indexing source metadata (#54), so a proposal can never
  introduce an unknown ``element_type``/``operation_kind``/``state_effects``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .code_indexer import _validate_metadata_fields
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient

PROMPT_VERSION = "refresh-v1"
SCHEMA_VERSION = "refresh-v1"

#: Shown by the API/UI on every proposal. The target repository stays the
#: source of truth; probe-agent never edits it.
REVIEW_REQUIRED_NOTE = (
    "This is a suggestion only. The target source repository remains the source "
    "of truth: review this proposal and, if it is correct, update the source "
    "docstring probe-agent metadata by hand (or via a future explicit "
    "source-edit workflow). probe-agent never edits the target repository, and "
    "the next snapshot re-indexes the corrected explanation."
)


@dataclass
class RefreshContext:
    """The deterministic context pack fed to the reasoning model."""

    node_id: int
    node_type: str
    name: str
    path: Optional[str]
    qualified_name: Optional[str]
    drift_status: str
    changed_hashes: List[str]
    old_explanation: str  # raw probe-agent block (verbatim), '' when none
    old_metadata: Dict[str, object] = field(default_factory=dict)
    captured_hashes: Dict[str, Optional[str]] = field(default_factory=dict)
    current_hashes: Dict[str, Optional[str]] = field(default_factory=dict)
    source_snippet: str = ""  # current source span; '' when source is gone
    structural_facts: Dict[str, object] = field(default_factory=dict)


@dataclass
class RefreshProposal:
    provider: str
    model: str
    is_mock: bool
    proposed_explanation: Optional[str] = None
    proposed_metadata: Optional[Dict[str, object]] = None
    summary_of_changes: Optional[str] = None
    confidence: Optional[float] = None
    error: Optional[str] = None


class RefreshValidationError(ValueError):
    pass


_SYSTEM_PROMPT = """\
You help maintain source-authored explanation metadata for a software system.
A source symbol changed since its explanation was written, so the explanation
may be stale. Propose an updated explanation and/or metadata grounded ONLY in
the provided current source and structural facts. Do not invent behavior that
is not visible in the source. You are producing a SUGGESTION that a developer
will review and apply to the source by hand; you never edit the repository.
Respond with ONLY valid JSON."""


def _drift_reason(ctx: RefreshContext) -> str:
    if ctx.drift_status == "missing_source":
        return (
            "The source this explanation depended on is gone (deleted or "
            "renamed). The explanation may need to be relocated or removed."
        )
    changed = ", ".join(ctx.changed_hashes) or "unknown"
    return f"Source hashes changed since the explanation was written: {changed}."


def _build_prompt(ctx: RefreshContext) -> str:
    facts_lines = [f"- {k}: {v}" for k, v in sorted(ctx.structural_facts.items()) if v]
    old_meta_lines = [
        f"- {k}: {v}" for k, v in sorted(ctx.old_metadata.items()) if v
    ]
    snippet = ctx.source_snippet.strip() or "(source is no longer present in the snapshot)"
    return (
        f"Symbol: {ctx.qualified_name or ctx.name}\n"
        f"File: {ctx.path or '(unknown)'}\n"
        f"Node type: {ctx.node_type}\n"
        f"Drift status: {ctx.drift_status}\n"
        f"Drift reason: {_drift_reason(ctx)}\n"
        f"Changed hashes: {', '.join(ctx.changed_hashes) or '(none)'}\n\n"
        "Old source-authored explanation block (verbatim):\n"
        f"```\n{ctx.old_explanation.strip() or '(none)'}\n```\n\n"
        "Old parsed metadata fields:\n"
        + ("\n".join(old_meta_lines) or "(none)")
        + "\n\nStructural facts (deterministic):\n"
        + ("\n".join(facts_lines) or "(none)")
        + "\n\nCurrent source snippet (pinned snapshot):\n"
        f"```\n{snippet}\n```\n\n"
        "Return JSON of the form:\n"
        "{\n"
        '  "proposed_explanation": "string (a concise human-readable role/'
        'behavior description for the updated source)",\n'
        '  "proposed_metadata": {"role": "string", "capability": "string", '
        '"element_type": "core|element|supporting|...", "operation_kind": '
        '"read|write|...", "state_effects": ["database-read", ...], '
        '"consumers": ["string"], "probe_value": "string"},\n'
        '  "summary_of_changes": "string (what changed and why the wording '
        'should change)",\n'
        '  "confidence": number between 0 and 1\n'
        "}\n"
        "Omit metadata keys you are not confident about. Only use enum values "
        "that appear valid for this system; never invent new enum values."
    )


def parse_refresh_response(raw_json: str) -> Dict[str, object]:
    """Validate a refresh response against the explicit contract.

    Enumerated metadata fields are validated against the same finite vocabulary
    used when indexing #54 source metadata, so a proposal can never introduce
    an unknown enum value. Free-text fields are copied verbatim, never
    interpreted.
    """
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise RefreshValidationError("response must be a JSON object")

    proposed_explanation = data.get("proposed_explanation")
    if proposed_explanation is not None:
        if not isinstance(proposed_explanation, str) or not proposed_explanation.strip():
            raise RefreshValidationError(
                "proposed_explanation must be a non-empty string when present"
            )
        proposed_explanation = proposed_explanation.strip()

    proposed_metadata_raw = data.get("proposed_metadata")
    proposed_metadata: Optional[Dict[str, object]] = None
    if proposed_metadata_raw is not None:
        if not isinstance(proposed_metadata_raw, dict):
            raise RefreshValidationError("proposed_metadata must be an object")
        valid, warnings = _validate_metadata_fields(proposed_metadata_raw)
        if warnings:
            # Reject rather than silently dropping: a proposal that wants to set
            # an unknown enum value is not a trustworthy suggestion.
            raise RefreshValidationError(
                "proposed_metadata is invalid: " + "; ".join(warnings)
            )
        proposed_metadata = valid or None

    if proposed_explanation is None and not proposed_metadata:
        raise RefreshValidationError(
            "a refresh proposal must include proposed_explanation or "
            "proposed_metadata"
        )

    summary = data.get("summary_of_changes")
    if not isinstance(summary, str) or not summary.strip():
        raise RefreshValidationError("summary_of_changes is required")

    confidence: Optional[float] = None
    if data.get("confidence") is not None:
        try:
            confidence = float(data["confidence"])
        except (TypeError, ValueError) as exc:
            raise RefreshValidationError("confidence must be a number") from exc
        if not 0.0 <= confidence <= 1.0:
            raise RefreshValidationError("confidence must be between 0 and 1")

    return {
        "proposed_explanation": proposed_explanation,
        "proposed_metadata": proposed_metadata,
        "summary_of_changes": summary.strip(),
        "confidence": confidence,
    }


def _strip_json_fence(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def propose_refresh(
    client: LLMClient,
    config: LLMConfig,
    ctx: RefreshContext,
) -> RefreshProposal:
    """Ask a reasoning model to propose an updated explanation.

    Fails closed for the mock provider: refresh proposals require a real
    reasoning model, exactly like feature-to-code mapping and API scanning.
    Any provider/parse/validation failure is returned as an error so the caller
    can mark the run failed and never persist a guessed proposal.
    """
    if isinstance(client, MockLLMClient):
        return RefreshProposal(
            provider="mock",
            model="mock",
            is_mock=True,
            error=(
                "Explanation refresh proposals require a real reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    prompt = _build_prompt(ctx)
    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
    except LLMError as exc:
        return RefreshProposal(
            provider=config.provider, model=config.model, is_mock=False,
            error=str(exc),
        )

    try:
        parsed = parse_refresh_response(_strip_json_fence(raw))
    except (json.JSONDecodeError, RefreshValidationError, TypeError) as exc:
        return RefreshProposal(
            provider=config.provider, model=config.model, is_mock=False,
            error=f"Failed to parse refresh response: {exc}",
        )

    return RefreshProposal(
        provider=config.provider, model=config.model, is_mock=False,
        proposed_explanation=parsed["proposed_explanation"],
        proposed_metadata=parsed["proposed_metadata"],
        summary_of_changes=parsed["summary_of_changes"],
        confidence=parsed["confidence"],
    )
