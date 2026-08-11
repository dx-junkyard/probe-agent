"""Control-Server-side trace redaction (Issue #367).

The SDK redacts before it sends.  This module redacts again before the
Control Server *stores*, for the three cases the SDK cannot cover:

* an older SDK is deployed against a newer server,
* a trace was posted by something other than the SDK (the API is a plain
  HTTP endpoint), and
* a value only became recognisable as a credential after rendering.

Everything here is deterministic and finite (Principle 6): exact, lowercase
key matches from ``probe_agent.redaction.SENSITIVE_KEYS`` and the documented
credential signatures in ``probe_agent.secret_patterns``.  The Control Server
already depends on ``probe_agent`` (see ``apps/control-server/Dockerfile``),
so both boundaries share one rule table rather than two drifting copies.

Storage is the boundary that matters: redaction happens *before* the row is
written, so every downstream consumer -- the API, the Dashboard, Workspaces,
AI Candidate Studio, Replay -- reads already-redacted data and cannot
reintroduce the leak by forgetting to mask.  Presentation-layer masking would
leave the plaintext on disk and in every export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from probe_agent import secret_patterns as _secret_patterns
from probe_agent.redaction import REDACTION_MARKER, is_sensitive_key

__all__ = [
    "REDACTION_MARKER",
    "TraceRedactionResult",
    "RedactedField",
    "redact_trace_payload",
    "redact_value",
    "redact_text",
]

#: Bound on traversal depth. Deeper nodes are replaced by an opaque marker
#: rather than trusted, so a hostile or pathological payload cannot use depth
#: to smuggle a value past the scan.
MAX_DEPTH = 24

#: Marker written in place of a node deeper than :data:`MAX_DEPTH`.
DEPTH_LIMIT_MARKER = "[REDACTED_SECRET:depth_limit]"


@dataclass(frozen=True)
class RedactedField:
    """One redaction that was applied, for the audit summary.

    ``path`` is a dotted/indexed location inside the field (``kwargs.api_key``,
    ``args[0]``) or the empty string for a whole-field replacement.  ``rule``
    is either ``sensitive_key`` (a denylisted name) or a
    ``probe_agent.secret_patterns`` rule name.
    """

    field: str
    path: str
    rule: str


@dataclass
class TraceRedactionResult:
    """Redacted payload plus the finite audit summary the UI renders."""

    input: Any = None
    output: Optional[str] = None
    error: Optional[str] = None
    input_capture: Any = None
    fields: List[RedactedField] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return bool(self.fields)

    @property
    def rules(self) -> List[str]:
        return sorted({entry.rule for entry in self.fields})

    def summary(self) -> Dict[str, Any]:
        """Serialisable summary of this scan.

        Always a dict, including when nothing matched: a stored ``NULL`` has
        to mean exactly one thing -- "this row was written before
        ingestion-time redaction existed" -- or the operational rescan cannot
        tell an unverified row from a verified-clean one.
        """
        return {
            "redacted": bool(self.fields),
            "rules": self.rules,
            "fields": [
                {"field": entry.field, "path": entry.path, "rule": entry.rule}
                for entry in self.fields
            ],
        }


def redact_text(
    text: Optional[str], *, field_name: str, path: str = ""
) -> Tuple[Optional[str], List[RedactedField]]:
    """Apply credential-signature redaction to one free-form string."""
    if not isinstance(text, str) or not text:
        return text, []
    replaced, rules = _secret_patterns.redact_secret_values(text)
    if not rules:
        return text, []
    return replaced, [RedactedField(field_name, path, rule) for rule in rules]


def _join(path: str, segment: str) -> str:
    return segment if not path else f"{path}{segment}" if segment.startswith("[") else f"{path}.{segment}"


def redact_value(
    value: Any, *, field_name: str, path: str = "", depth: int = 0
) -> Tuple[Any, List[RedactedField]]:
    """Recursively redact a decoded JSON value.

    Mapping values under a denylisted key are replaced wholesale; every string
    is additionally scanned for credential signatures.  Lists and nested
    objects are traversed.  The input is never mutated.
    """
    entries: List[RedactedField] = []
    if depth > MAX_DEPTH:
        return DEPTH_LIMIT_MARKER, [RedactedField(field_name, path, "depth_limit")]

    if isinstance(value, dict):
        result: Dict[Any, Any] = {}
        for key, item in value.items():
            child = _join(path, str(key))
            if is_sensitive_key(key):
                result[key] = REDACTION_MARKER
                entries.append(RedactedField(field_name, child, "sensitive_key"))
                continue
            result[key], child_entries = redact_value(
                item, field_name=field_name, path=child, depth=depth + 1
            )
            entries.extend(child_entries)
        return result, entries

    if isinstance(value, list):
        items: List[Any] = []
        for index, item in enumerate(value):
            child = _join(path, f"[{index}]")
            redacted_item, child_entries = redact_value(
                item, field_name=field_name, path=child, depth=depth + 1
            )
            items.append(redacted_item)
            entries.extend(child_entries)
        return items, entries

    if isinstance(value, str):
        return redact_text(value, field_name=field_name, path=path)

    return value, entries


def redact_trace_payload(
    *,
    input_value: Any,
    output: Optional[str],
    error: Optional[str],
    input_capture: Any = None,
) -> TraceRedactionResult:
    """Redact every free-form slot of one trace event before it is stored.

    ``input_capture`` is included even though the SDK already redacts it: a
    capture posted by an older SDK, or by a non-SDK client, reaches the same
    replay harness as any other, and replay is exactly the path that would
    hand a stored credential to a candidate implementation.
    """
    result = TraceRedactionResult()

    result.input, entries = redact_value(input_value, field_name="input")
    result.fields.extend(entries)

    result.output, entries = redact_text(output, field_name="output")
    result.fields.extend(entries)

    result.error, entries = redact_text(error, field_name="error")
    result.fields.extend(entries)

    result.input_capture, entries = redact_value(
        input_capture, field_name="input_capture"
    )
    result.fields.extend(entries)

    return result
