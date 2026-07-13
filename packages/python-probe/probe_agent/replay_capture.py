"""Structured, replayable input capture (Issue #242 Phase A / #243).

Captures a probe's positional/keyword arguments as canonical, JSON
round-trip-able structures so later phases (replay engine, offline shadow)
can mechanically restore them. Everything here is deterministic — a fixed
encoding for a fixed set of Python types, direct structural checks only
(Principle 6: no LLM, no heuristics).

Encoding rules (canonical JSON):
  * JSON-native values pass through: ``None`` / ``bool`` / ``int`` / ``str``,
    finite ``float``, ``list``, ``dict`` with string keys.
  * Non-JSON-native values use a reserved single-marker-key object
    (``"__probe__"``):
      - ``tuple``     -> ``{"__probe__": "tuple", "items": [...]}``
      - ``set``       -> ``{"__probe__": "set", "items": [...]}`` (items
        sorted deterministically by their canonical JSON serialization)
      - ``frozenset`` -> ``{"__probe__": "frozenset", "items": [...]}``
      - ``bytes``     -> ``{"__probe__": "bytes", "b64": "..."}``
      - non-finite float -> ``{"__probe__": "float", "value": "nan"|"inf"|"-inf"}``
      - dict with non-string keys OR containing a literal ``"__probe__"`` key
        -> ``{"__probe__": "dict", "items": [[k_enc, v_enc], ...]}`` (so a
        plain decoded dict never contains ``"__probe__"`` and decoding is
        unambiguous)
      - anything else -> ``{"__probe__": "unsupported", "type": "<TypeName>"}``
        (never the raw value or its repr — either may embed sensitive data)

Redaction reuses the projection path grammar and semantics
(``probe_agent/projection.py``): redact paths are applied structurally to the
``{"args": [...], "kwargs": {...}}`` root BEFORE encoding, and a structurally
blocked redact path fails closed by dropping the entire capture.

Capture is best-effort: :func:`capture_input` never raises — any internal
failure degrades to ``unreplayable`` / ``capture_failed``. The decorator adds
its own guard on top so the wrapped function can never be affected.
"""

import base64
import json
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from . import projection as _projection
from .config import ProbeConfig

# Reserved marker key for explicit (non-JSON-native) encodings.
MARKER = "__probe__"

# Maximum nesting depth encoded; deeper nodes become an unsupported marker.
MAX_DEPTH = 20

# --- finite classification / reason sets (Principle 6) ----------------------

REPLAYABLE = "replayable"
PARTIAL = "partial"
UNREPLAYABLE = "unreplayable"
REPLAYABILITY_VALUES = (REPLAYABLE, PARTIAL, UNREPLAYABLE)

REASON_UNSUPPORTED_TYPE = "unsupported_type"
REASON_REDACTED = "redacted"
REASON_DEPTH_LIMIT = "depth_limit_exceeded"
REASON_SIZE_LIMIT = "size_limit_exceeded"
REASON_ROUND_TRIP = "round_trip_failed"
REASON_CAPTURE_FAILED = "capture_failed"
REASON_REDACTION_BLOCKED = "redaction_blocked"
REASON_CODES = (
    REASON_UNSUPPORTED_TYPE,
    REASON_REDACTED,
    REASON_DEPTH_LIMIT,
    REASON_SIZE_LIMIT,
    REASON_ROUND_TRIP,
    REASON_CAPTURE_FAILED,
    REASON_REDACTION_BLOCKED,
)


class ReplayCaptureError(ValueError):
    """Raised for an invalid replay_capture spec (fail-closed at decoration)."""


class _Undecodable:
    """Sentinel returned by :func:`decode_value` for values that were encoded
    as ``unsupported`` (including depth-limit placeholders). They carry no
    restorable data by design."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<probe:undecodable>"


UNDECODABLE = _Undecodable()


# --- spec validation (fail-closed) -----------------------------------------

class ReplayCaptureSpec:
    """A validated replay_capture spec.

    Accepts ``True`` (capture with defaults) or a mapping like
    ``{"redact": ["$.kwargs.password"]}``. Redact paths use the same path
    grammar as projections and are parsed (fail-closed) at registration.
    """

    def __init__(self, raw: Any):
        if raw is True:
            raw = {}
        if not isinstance(raw, dict):
            raise ReplayCaptureError(
                "replay_capture must be True or a mapping like {'redact': [...]}"
            )
        unknown = set(raw) - {"redact"}
        if unknown:
            raise ReplayCaptureError(
                f"unknown replay_capture keys: {sorted(unknown)}"
            )
        redact_raw = raw.get("redact") or []
        if not isinstance(redact_raw, list):
            raise ReplayCaptureError("replay_capture 'redact' must be a list of paths")
        # parse_path raises ProjectionError (a ValueError) on a bad path.
        self.redact = [_projection.parse_path(p) for p in redact_raw]


def compile_spec(spec: Any) -> ReplayCaptureSpec:
    """Validate a spec (True / dict / ReplayCaptureSpec). Raises ValueError."""
    if isinstance(spec, ReplayCaptureSpec):
        return spec
    return ReplayCaptureSpec(spec)


# --- canonical encoding ------------------------------------------------------

def _sort_key(encoded: Any) -> str:
    return json.dumps(encoded, sort_keys=True, ensure_ascii=False)


def encode_value(value: Any, flags: Optional[Set[str]] = None, depth: int = 0) -> Any:
    """Encode ``value`` into canonical, JSON-serialisable form.

    ``flags`` (a set) collects degradation reason codes
    (``unsupported_type`` / ``depth_limit_exceeded``) as they occur.
    """
    if flags is None:
        flags = set()
    if depth > MAX_DEPTH:
        flags.add(REASON_DEPTH_LIMIT)
        return {MARKER: "unsupported", "type": "depth_limit"}
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return {MARKER: "float", "value": "nan"}
        return {MARKER: "float", "value": "inf" if value > 0 else "-inf"}
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bytes):
        return {MARKER: "bytes", "b64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {
            MARKER: "tuple",
            "items": [encode_value(v, flags, depth + 1) for v in value],
        }
    if isinstance(value, (set, frozenset)):
        items = [encode_value(v, flags, depth + 1) for v in value]
        items.sort(key=_sort_key)
        kind = "set" if isinstance(value, set) else "frozenset"
        return {MARKER: kind, "items": items}
    if isinstance(value, list):
        return [encode_value(v, flags, depth + 1) for v in value]
    if isinstance(value, dict):
        string_keys = all(isinstance(k, str) for k in value.keys())
        if string_keys and MARKER not in value:
            return {k: encode_value(v, flags, depth + 1) for k, v in value.items()}
        return {
            MARKER: "dict",
            "items": [
                [encode_value(k, flags, depth + 1), encode_value(v, flags, depth + 1)]
                for k, v in value.items()
            ],
        }
    flags.add(REASON_UNSUPPORTED_TYPE)
    # Never embed the raw value or its repr — it may contain sensitive data.
    return {MARKER: "unsupported", "type": type(value).__name__}


def decode_value(encoded: Any) -> Any:
    """Invert :func:`encode_value`.

    ``unsupported`` markers (including depth-limit placeholders) decode to the
    :data:`UNDECODABLE` sentinel — they carry no restorable data by design.
    """
    if isinstance(encoded, list):
        return [decode_value(v) for v in encoded]
    if isinstance(encoded, dict):
        if MARKER not in encoded:
            return {k: decode_value(v) for k, v in encoded.items()}
        kind = encoded[MARKER]
        if kind == "tuple":
            return tuple(decode_value(v) for v in encoded["items"])
        if kind == "set":
            return {decode_value(v) for v in encoded["items"]}
        if kind == "frozenset":
            return frozenset(decode_value(v) for v in encoded["items"])
        if kind == "bytes":
            return base64.b64decode(encoded["b64"])
        if kind == "float":
            return {
                "nan": float("nan"),
                "inf": float("inf"),
                "-inf": float("-inf"),
            }[encoded["value"]]
        if kind == "dict":
            return {decode_value(k): decode_value(v) for k, v in encoded["items"]}
        if kind == "unsupported":
            return UNDECODABLE
        raise ReplayCaptureError(f"unknown encoding marker {kind!r}")
    return encoded


# --- round-trip verification -------------------------------------------------

def _roundtrip_equal(original: Any, decoded: Any) -> bool:
    """Structural equality between the (post-redaction) original and the
    decoded capture. ``UNDECODABLE`` placeholders compare equal by definition
    (they are already flagged as degraded)."""
    if decoded is UNDECODABLE:
        return True
    if isinstance(original, bool) or isinstance(decoded, bool):
        return (
            isinstance(original, bool)
            and isinstance(decoded, bool)
            and original == decoded
        )
    if isinstance(original, float) or isinstance(decoded, float):
        if not (isinstance(original, float) and isinstance(decoded, float)):
            return False
        if math.isnan(original) and math.isnan(decoded):
            return True
        return original == decoded
    if isinstance(original, tuple) or isinstance(decoded, tuple):
        return (
            isinstance(original, tuple)
            and isinstance(decoded, tuple)
            and len(original) == len(decoded)
            and all(_roundtrip_equal(a, b) for a, b in zip(original, decoded))
        )
    if isinstance(original, list) or isinstance(decoded, list):
        return (
            isinstance(original, list)
            and isinstance(decoded, list)
            and len(original) == len(decoded)
            and all(_roundtrip_equal(a, b) for a, b in zip(original, decoded))
        )
    if isinstance(original, (set, frozenset)) or isinstance(decoded, (set, frozenset)):
        if not (
            isinstance(original, (set, frozenset))
            and isinstance(decoded, (set, frozenset))
            and isinstance(original, set) == isinstance(decoded, set)
        ):
            return False
        # Members that decoded to UNDECODABLE are already flagged; the set as
        # a whole cannot be compared element-wise (unordered), so pass it.
        if any(m is UNDECODABLE for m in decoded):
            return len(original) == len(decoded)
        return original == decoded
    if isinstance(original, dict) or isinstance(decoded, dict):
        if not (isinstance(original, dict) and isinstance(decoded, dict)):
            return False
        if len(original) != len(decoded):
            return False
        if any(k is UNDECODABLE for k in decoded):
            # Unsupported keys are already flagged; sizes match, pass.
            return True
        for k, v in original.items():
            if k not in decoded:
                return False
            if not _roundtrip_equal(v, decoded[k]):
                return False
        return True
    if isinstance(original, bytes) or isinstance(decoded, bytes):
        return (
            isinstance(original, bytes)
            and isinstance(decoded, bytes)
            and original == decoded
        )
    return original == decoded


# --- capture ------------------------------------------------------------------

def capture_input(
    args: tuple, kwargs: dict, spec: ReplayCaptureSpec
) -> Tuple[Optional[Dict[str, Any]], str, List[str]]:
    """Capture ``(args, kwargs)`` as a structured, replayable payload.

    Returns ``(capture, replayability, reasons)``:
      * ``capture`` — ``{"args": [encoded...], "kwargs": {name: encoded...}}``
        or ``None`` when no usable capture could be stored.
      * ``replayability`` — one of :data:`REPLAYABILITY_VALUES` (finite set,
        deterministic structural classification only).
      * ``reasons`` — sorted list drawn from :data:`REASON_CODES`.

    Never raises: any internal failure returns
    ``(None, "unreplayable", ["capture_failed"])``.
    """
    try:
        root: Any = {"args": list(args), "kwargs": dict(kwargs)}
        flags: Set[str] = set()

        if spec.redact:
            # Flag paths that resolve to at least one value: those values are
            # about to be masked. Resolution errors fail closed like blocked
            # paths (over-caution is safe; leaking is not).
            for segs in spec.redact:
                try:
                    if _projection._resolve(segs, root):
                        flags.add(REASON_REDACTED)
                except Exception:  # noqa: BLE001 — fail closed
                    return None, UNREPLAYABLE, [REASON_REDACTION_BLOCKED]
            root, blocked = _projection._apply_redaction(root, spec.redact)
            if blocked:
                # Same semantics as projection's blocked paths, but stricter:
                # a capture is stored verbatim for replay, so a value that
                # could not be masked structurally must not be stored at all.
                return None, UNREPLAYABLE, [REASON_REDACTION_BLOCKED]

        encoded = encode_value(root, flags, 0)

        payload = json.dumps(encoded, ensure_ascii=False)
        if len(payload.encode("utf-8")) > ProbeConfig.replay_capture_max_bytes():
            # A truncated JSON capture cannot round-trip; never store one.
            return None, UNREPLAYABLE, [REASON_SIZE_LIMIT]

        try:
            if not _roundtrip_equal(root, decode_value(encoded)):
                flags.add(REASON_ROUND_TRIP)
        except Exception:  # noqa: BLE001 — comparison itself must not break capture
            flags.add(REASON_ROUND_TRIP)

        if not flags:
            return encoded, REPLAYABLE, []
        return encoded, PARTIAL, sorted(flags)
    except Exception:  # noqa: BLE001 — capture is best-effort by contract
        return None, UNREPLAYABLE, [REASON_CAPTURE_FAILED]
