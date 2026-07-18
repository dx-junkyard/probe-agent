"""Deterministic, SDK-side telemetry redaction.

Only exact, case-insensitive key matches from the finite denylist below are
treated as sensitive. There is intentionally no fuzzy matching or inference.
The functions here do not mutate their inputs.
"""

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


REDACTION_MARKER = "██redacted██"

SENSITIVE_KEYS = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "cookie",
    "session",
})


class _RedactedValue:
    __slots__ = ()

    def __repr__(self) -> str:
        return REDACTION_MARKER

    def __str__(self) -> str:
        return REDACTION_MARKER


# A sentinel (rather than the public marker string) makes marker collisions
# distinguishable until the final serializer boundary.
REDACTED = _RedactedValue()


def is_sensitive_key(key: Any) -> bool:
    """Return whether ``key`` is an exact, lowercase denylist match."""
    return isinstance(key, str) and key.lower() in SENSITIVE_KEYS


def redact_sensitive(value: Any, _memo: Optional[Dict[int, Any]] = None) -> Any:
    """Return a recursively redacted copy of supported containers.

    dict/list/tuple/set/frozenset are traversed. Values below a sensitive
    mapping key are replaced wholesale; user-defined objects are not inspected.
    """
    if _memo is None:
        _memo = {}
    if isinstance(value, dict):
        existing = _memo.get(id(value))
        if existing is not None:
            return existing
        copied: Dict[Any, Any] = {}
        _memo[id(value)] = copied
        for key, item in value.items():
            copied[key] = REDACTED if is_sensitive_key(key) else redact_sensitive(item, _memo)
        return copied
    if isinstance(value, list):
        existing = _memo.get(id(value))
        if existing is not None:
            return existing
        copied_list: List[Any] = []
        _memo[id(value)] = copied_list
        copied_list.extend(redact_sensitive(item, _memo) for item in value)
        return copied_list
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item, _memo) for item in value)
    if isinstance(value, set):
        return {redact_sensitive(item, _memo) for item in value}
    if isinstance(value, frozenset):
        return frozenset(redact_sensitive(item, _memo) for item in value)
    return value


def contains_redacted(value: Any, _seen: Optional[Set[int]] = None) -> bool:
    """Return whether a redacted sentinel occurs in a supported container."""
    if value is REDACTED:
        return True
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        if id(value) in _seen:
            return False
        _seen.add(id(value))
        return any(contains_redacted(item, _seen) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        if id(value) in _seen:
            return False
        _seen.add(id(value))
        return any(contains_redacted(item, _seen) for item in value)
    return False


def redact_path(
    node: Any, segs: List[tuple], marker: Any = REDACTION_MARKER
) -> Tuple[Any, bool]:
    """Copy-on-write redaction for one parsed projection path."""
    if not segs:
        return marker, False
    seg, rest = segs[0], segs[1:]
    kind = seg[0]
    if kind == "key":
        if isinstance(node, dict):
            if seg[1] in node:
                copied = dict(node)
                copied[seg[1]], blocked = redact_path(node[seg[1]], rest, marker)
                return copied, blocked
            return node, False
        return node, True
    if kind == "index":
        if isinstance(node, (list, tuple)):
            index = seg[1]
            if -len(node) <= index < len(node):
                copied_list = list(node)
                copied_list[index], blocked = redact_path(node[index], rest, marker)
                return copied_list, blocked
            return node, False
        return node, True
    if kind == "wild":
        if isinstance(node, dict):
            copied_dict = dict(node)
            any_blocked = False
            for key, item in node.items():
                copied_dict[key], blocked = redact_path(item, rest, marker)
                any_blocked = any_blocked or blocked
            return copied_dict, any_blocked
        if isinstance(node, (list, tuple)):
            copied_values = list(node)
            any_blocked = False
            for index, item in enumerate(node):
                copied_values[index], blocked = redact_path(item, rest, marker)
                any_blocked = any_blocked or blocked
            return copied_values, any_blocked
        return node, True
    return node, True


def apply_path_redactions(
    root: Any, redact_segments: Iterable[List[tuple]], marker: Any = REDACTION_MARKER
) -> Tuple[Any, List[List[tuple]]]:
    """Apply parsed redact paths without mutating ``root``."""
    blocked_paths: List[List[tuple]] = []
    for segs in redact_segments:
        root, blocked = redact_path(root, segs, marker)
        if blocked:
            blocked_paths.append(segs)
    return root, blocked_paths
