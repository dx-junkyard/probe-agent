"""Declarative projection engine (Issue #146, Phase 2).

Extracts a bounded, structured, safe slice of input/output payloads for a
probe point without ever storing the raw payload. Everything here is
deterministic — a fixed path grammar and a fixed operation set, no ``eval`` and
no arbitrary code execution.

Spec validation is fail-closed: a malformed spec raises ``ValueError`` at
registration time. Extraction at runtime is non-fatal: any error is captured as
a diagnostic on the projection result so the host function keeps working.
"""

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from . import redaction as _redaction

_ALLOWED_OPS = ("len", "count", "exists", "sha256")
_ENTITY_ROLES = ("source", "derived", "related")
_PHASES = ("input", "output")
_REDACTED = _redaction.REDACTION_MARKER

_MISSING = object()

# path grammar: $ then a sequence of .key / [int] / [*]
_SEGMENT_RE = re.compile(r"\.([A-Za-z0-9_\-]+)|\[([0-9]+)\]|\[(\*)\]")


class ProjectionError(ValueError):
    """Raised for an invalid projection spec (fail-closed at registration)."""


# --- limits (env-tunable, conservative defaults) ---------------------------

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def max_bytes() -> int:
    return _int_env("PROBE_PROJECTION_MAX_BYTES", 8192)


def max_fields() -> int:
    return _int_env("PROBE_PROJECTION_MAX_FIELDS", 64)


def max_samples() -> int:
    return _int_env("PROBE_PROJECTION_MAX_SAMPLES", 20)


# --- path parsing ----------------------------------------------------------

def parse_path(path: str) -> List[tuple]:
    if not isinstance(path, str) or not path.startswith("$"):
        raise ProjectionError(f"path must start with '$': {path!r}")
    rest = path[1:]
    segments: List[tuple] = []
    pos = 0
    while pos < len(rest):
        m = _SEGMENT_RE.match(rest, pos)
        if not m:
            raise ProjectionError(f"invalid path segment in {path!r} at {rest[pos:]!r}")
        key, index, wild = m.groups()
        if key is not None:
            segments.append(("key", key))
        elif index is not None:
            segments.append(("index", int(index)))
        else:
            segments.append(("wild",))
        pos = m.end()
    return segments


def _path_has_wildcard(segments: List[tuple]) -> bool:
    return any(s[0] == "wild" for s in segments)


# --- resolution ------------------------------------------------------------

def _get_key(val: Any, key: str) -> Any:
    if isinstance(val, dict):
        return val.get(key, _MISSING)
    # object attribute: prefer __dict__ to avoid triggering descriptors;
    # fall back to getattr for slots. Callables (methods) are not fields.
    d = getattr(val, "__dict__", None)
    if isinstance(d, dict) and key in d:
        return d[key]
    try:
        attr = getattr(val, key, _MISSING)
    except Exception:  # noqa: BLE001 — a broken property must not break tracing
        return _MISSING
    if attr is _MISSING or callable(attr):
        return _MISSING
    return attr


def _resolve(segments: List[tuple], root: Any) -> List[Any]:
    current: List[Any] = [root]
    for seg in segments:
        nxt: List[Any] = []
        for val in current:
            if seg[0] == "key":
                v = _get_key(val, seg[1])
                if v is not _MISSING:
                    nxt.append(v)
            elif seg[0] == "index":
                if isinstance(val, (list, tuple)):
                    i = seg[1]
                    if -len(val) <= i < len(val):
                        nxt.append(val[i])
            else:  # wildcard
                if isinstance(val, (list, tuple)):
                    nxt.extend(val)
                elif isinstance(val, dict):
                    nxt.extend(val.values())
        current = nxt
    return current


# --- redaction (copy-on-write along redacted paths only) -------------------
#
# Structural redaction only rewrites dict/list trees. Extraction, however, can
# also read object *attributes* (_get_key), which a copy-on-write rewrite
# cannot reach. Any redact path whose traversal hits a non-dict/list node with
# segments remaining is therefore reported as "blocked", and extraction
# fail-closes by masking every extracted value whose path overlaps a blocked
# redact path (over-redaction is safe; under-redaction is not).

def _redact_one(node: Any, segs: List[tuple]) -> Tuple[Any, bool]:
    """Compatibility wrapper around the shared pure redaction function."""
    return _redaction.redact_path(node, segs, marker=_REDACTED)


def _apply_redaction(root: Any, redact_segments: List[List[tuple]]) -> Tuple[Any, List[List[tuple]]]:
    """Apply structural redaction; return (root, blocked_paths)."""
    return _redaction.apply_path_redactions(root, redact_segments, marker=_REDACTED)


def _segments_overlap(a: List[tuple], b: List[tuple]) -> bool:
    """True when one path is a (wildcard-tolerant) prefix of the other, i.e.
    the extraction could read at/below/above the redacted location."""
    for sa, sb in zip(a, b):
        if sa[0] == "wild" or sb[0] == "wild":
            continue
        if sa != sb:
            return False
    return True


def _mask_if_blocked(segs: List[tuple], blocked: List[List[tuple]], value: Any) -> Any:
    if any(_segments_overlap(segs, b) for b in blocked):
        return _REDACTED
    return value


# --- canonicalisation / hashing --------------------------------------------

def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr)
    except Exception:  # noqa: BLE001
        return repr(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    """Coerce an extracted value into something JSON-serialisable."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


# --- spec validation (fail-closed) -----------------------------------------

class ProjectionSpec:
    """A validated, parsed projection spec."""

    def __init__(self, raw: Dict[str, Any]):
        if not isinstance(raw, dict):
            raise ProjectionError("projection spec must be a mapping")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ProjectionError("projection spec requires a non-empty 'name'")
        self.name = name
        self.sections: Dict[str, Dict[str, Any]] = {}
        for phase in _PHASES:
            if phase in raw and raw[phase] is not None:
                self.sections[phase] = self._parse_section(raw[phase], phase)
        self.entities = self._parse_entities(raw.get("entities") or [])
        self.redact = [parse_path(p) for p in (raw.get("redact") or [])]

        unknown = set(raw) - {"name", "input", "output", "entities", "redact"}
        if unknown:
            raise ProjectionError(f"unknown projection keys: {sorted(unknown)}")

    def _parse_section(self, section: Any, phase: str) -> Dict[str, Any]:
        if not isinstance(section, dict):
            raise ProjectionError(f"{phase} section must be a mapping")
        unknown = set(section) - {"fields", "metrics", "samples"}
        if unknown:
            raise ProjectionError(f"unknown {phase} keys: {sorted(unknown)}")
        fields = {}
        for fname, fpath in (section.get("fields") or {}).items():
            fields[fname] = parse_path(fpath)
        metrics = {}
        for mname, spec in (section.get("metrics") or {}).items():
            if not isinstance(spec, dict) or "op" not in spec or "path" not in spec:
                raise ProjectionError(f"metric {mname!r} needs 'op' and 'path'")
            op = spec["op"]
            if op not in _ALLOWED_OPS:
                raise ProjectionError(f"unknown metric op {op!r} (allowed: {_ALLOWED_OPS})")
            metrics[mname] = (op, parse_path(spec["path"]))
        samples = {}
        for sname, spec in (section.get("samples") or {}).items():
            if not isinstance(spec, dict) or "path" not in spec:
                raise ProjectionError(f"sample {sname!r} needs 'path'")
            limit = spec.get("limit")
            if limit is not None and (not isinstance(limit, int) or limit < 1):
                raise ProjectionError(f"sample {sname!r} limit must be a positive int")
            samples[sname] = (parse_path(spec["path"]), limit)
        return {"fields": fields, "metrics": metrics, "samples": samples}

    def _parse_entities(self, entities: Any) -> List[Dict[str, Any]]:
        if not isinstance(entities, list):
            raise ProjectionError("entities must be a list")
        result = []
        for ent in entities:
            if not isinstance(ent, dict) or "type" not in ent or "id_path" not in ent:
                raise ProjectionError("entity ref needs 'type' and 'id_path'")
            phase = ent.get("phase", "output")
            if phase not in _PHASES:
                raise ProjectionError(f"entity phase must be one of {_PHASES}")
            role = ent.get("role", "related")
            if role not in _ENTITY_ROLES:
                raise ProjectionError(f"entity role must be one of {_ENTITY_ROLES}")
            result.append({
                "type": str(ent["type"]),
                "id_path": parse_path(ent["id_path"]),
                "phase": phase,
                "role": role,
            })
        return result


def compile_spec(spec: Any) -> ProjectionSpec:
    """Validate a spec (dict or ProjectionSpec). Raises ProjectionError."""
    if isinstance(spec, ProjectionSpec):
        return spec
    return ProjectionSpec(spec)


# --- extraction ------------------------------------------------------------

def _extract_section(
    section: Dict[str, Any], root: Any, blocked: List[List[tuple]]
) -> Dict[str, Any]:
    out_fields: Dict[str, Any] = {}
    for fname, segs in section["fields"].items():
        matches = _resolve(segs, root)
        if _path_has_wildcard(segs):
            value = _jsonable(matches)
        else:
            value = _jsonable(matches[0]) if matches else None
        out_fields[fname] = _mask_if_blocked(segs, blocked, value)

    out_metrics: Dict[str, Any] = {}
    for mname, (op, segs) in section["metrics"].items():
        matches = _resolve(segs, root)
        out_metrics[mname] = _mask_if_blocked(segs, blocked, _apply_metric(op, matches))

    out_samples: Dict[str, Any] = {}
    for sname, (segs, limit) in section["samples"].items():
        matches = _resolve(segs, root)
        if len(matches) == 1 and isinstance(matches[0], (list, tuple)):
            items = list(matches[0])
        else:
            items = matches
        n = limit if limit is not None else max_samples()
        n = min(n, max_samples())
        out_samples[sname] = _mask_if_blocked(segs, blocked, _jsonable(items[:n]))

    return {"fields": out_fields, "metrics": out_metrics, "samples": out_samples}


def _apply_metric(op: str, matches: List[Any]) -> Any:
    if op == "exists":
        return len(matches) > 0
    if op == "count":
        return len(matches)
    if op == "len":
        if not matches:
            return None
        try:
            return len(matches[0])
        except TypeError:
            return None
    if op == "sha256":
        if not matches:
            return None
        target = matches[0] if len(matches) == 1 else matches
        return _hash(_jsonable(target))
    return None


def _apply_limits(data: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Enforce field-count and byte limits deterministically."""
    truncated = False
    fields = data["fields"]
    if len(fields) > max_fields():
        kept = dict(list(fields.items())[: max_fields()])
        data = {**data, "fields": kept}
        truncated = True

    limit = max_bytes()
    encoded = _canonical(data)
    if len(encoded.encode("utf-8")) <= limit:
        return data, truncated

    # Over budget: drop samples, then metrics, then fields (deterministic order)
    truncated = True
    for key in ("samples", "metrics", "fields"):
        while data[key] and len(_canonical(data).encode("utf-8")) > limit:
            k = list(data[key].keys())[-1]
            data = {**data, key: {kk: vv for kk, vv in data[key].items() if kk != k}}
        if len(_canonical(data).encode("utf-8")) <= limit:
            break
    return data, truncated


def extract_phase(spec: ProjectionSpec, root: Any, phase: str) -> Optional[Dict[str, Any]]:
    """Apply the spec's ``output`` section to ``root`` under an arbitrary phase
    label (used for shadow_current / shadow_candidate in Phase 5 / Issue #150).

    Returns ``None`` when the spec has no output section, or a diagnostic
    payload with ``error`` set if extraction fails (never raises).
    """
    if "output" not in spec.sections:
        return None
    try:
        redacted, blocked = _apply_redaction(root, spec.redact)
        data = _extract_section(spec.sections["output"], redacted, blocked)
        data, truncated = _apply_limits(data)
        return {
            "projection_name": spec.name,
            "phase": phase,
            "fields": data["fields"],
            "metrics": data["metrics"],
            "samples": data["samples"],
            "data_hash": _hash(data),
            "truncated": truncated,
        }
    except Exception as e:  # noqa: BLE001 — non-fatal diagnostic
        return {
            "projection_name": spec.name,
            "phase": phase,
            "fields": {},
            "metrics": {},
            "samples": {},
            "data_hash": None,
            "truncated": False,
            "error": f"{type(e).__name__}: {e}",
        }


def extract(
    spec: ProjectionSpec,
    input_root: Any = _MISSING,
    output_root: Any = _MISSING,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Return (projection_payloads, entities).

    Each payload is ``{projection_name, phase, fields, metrics, samples,
    data_hash, truncated}``; on extraction error a payload with ``error`` set is
    returned instead (non-fatal). Entities come from ``id_path`` refs.
    """
    roots = {"input": input_root, "output": output_root}
    payloads: List[Dict[str, Any]] = []

    for phase in _PHASES:
        if phase not in spec.sections:
            continue
        root = roots.get(phase, _MISSING)
        if root is _MISSING:
            continue
        try:
            redacted, blocked = _apply_redaction(root, spec.redact)
            data = _extract_section(spec.sections[phase], redacted, blocked)
            data, truncated = _apply_limits(data)
            payloads.append({
                "projection_name": spec.name,
                "phase": phase,
                "fields": data["fields"],
                "metrics": data["metrics"],
                "samples": data["samples"],
                "data_hash": _hash(data),
                "truncated": truncated,
            })
        except Exception as e:  # noqa: BLE001 — diagnostic, never fatal
            payloads.append({
                "projection_name": spec.name,
                "phase": phase,
                "fields": {},
                "metrics": {},
                "samples": {},
                "data_hash": None,
                "truncated": False,
                "error": f"{type(e).__name__}: {e}",
            })

    entities: List[Dict[str, str]] = []
    for ent in spec.entities:
        root = roots.get(ent["phase"], _MISSING)
        if root is _MISSING:
            continue
        # Entities resolve against the raw root, so a redacted value must never
        # become an entity id: skip any id_path that overlaps a redact path.
        if any(_segments_overlap(ent["id_path"], r) for r in spec.redact):
            continue
        try:
            matches = _resolve(ent["id_path"], root)
        except Exception:  # noqa: BLE001
            continue
        for match in matches:
            if match is None or match == _REDACTED:
                continue
            entities.append({"type": ent["type"], "id": str(match), "role": ent["role"]})

    return payloads, entities
