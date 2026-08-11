"""Deterministic, SDK-side telemetry redaction.

Only exact, case-insensitive key matches from the finite denylist below are
treated as sensitive. There is intentionally no fuzzy matching or inference.
The functions here do not mutate their inputs.

Two layers exist, and Issue #367 requires both:

1. **Key-based structural redaction** (this module).  ``redact_sensitive``
   traverses the standard containers; ``redact_for_repr`` additionally
   traverses *user-defined objects*, because a payload's leak path is usually
   an ordinary settings/config object whose ``repr`` prints every attribute it
   holds -- a mapping key denylist never sees those attributes.
2. **Value-shaped credential signatures**
   (:mod:`probe_agent.secret_patterns`), applied to the rendered text, for the
   cases no key name can cover: a custom ``__repr__``, an exception message, a
   traceback, a URL with inline credentials.
"""

import dataclasses
import datetime
import decimal
import enum
import inspect
import pathlib
import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


REDACTION_MARKER = "██redacted██"

SENSITIVE_KEYS = frozenset({
    # Issue #367 named api_key / token / password / authorization / cookie
    # explicitly; the rest are the exact spellings those same credentials are
    # commonly bound under. Every entry is an exact, lowercase match -- adding
    # a name here can only over-redact, never under-redact.
    "password",
    "passwd",
    "passphrase",
    "secret",
    "secret_key",
    "secretkey",
    "client_secret",
    "private_key",
    "privatekey",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "bearer_token",
    "authorization",
    "auth",
    "api_key",
    "apikey",
    "api-key",
    "x-api-key",
    "access_key",
    "access_key_id",
    "secret_access_key",
    "credential",
    "credentials",
    "cookie",
    "cookies",
    "set-cookie",
    "session",
    "session_id",
    "sessionid",
    "session_key",
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


# --- object-aware redaction for repr payloads (Issue #367) ------------------
#
# ``redact_sensitive`` deliberately stops at user-defined objects: it feeds
# ``replay_capture``, where an unknown object is already encoded as an opaque
# ``unsupported`` marker that carries no data. The repr payload has the
# opposite problem -- ``repr(config)`` prints whatever the object holds -- so
# the repr path uses ``redact_for_repr`` below, which descends into objects and
# rebuilds them as repr-preserving surrogates with sensitive attributes masked.

#: Depth beyond which an object is rendered as an opaque type marker rather
#: than traversed. Bounds both work and payload size on cyclic/deep graphs.
MAX_REPR_DEPTH = 8

#: Types whose ``repr`` is a fixed, value-only rendering with no attribute
#: printing, so traversing them would lose information and gain no safety.
_OPAQUE_REPR_TYPES = (
    datetime.date,
    datetime.time,
    datetime.datetime,
    datetime.timedelta,
    datetime.timezone,
    decimal.Decimal,
    uuid.UUID,
    pathlib.PurePath,
    enum.Enum,
    complex,
    range,
    re.Pattern,
)


class _ObjectSurrogate:
    """Stands in for a user-defined object during repr rendering.

    Keeps the original type name so a trace still identifies *what* the
    argument was, while every attribute value has passed through redaction.
    """

    __slots__ = ("type_name", "fields")

    def __init__(self, type_name: str, fields: "Dict[str, Any]") -> None:
        self.type_name = type_name
        self.fields = fields

    def __repr__(self) -> str:
        body = ", ".join(f"{key}={value!r}" for key, value in self.fields.items())
        return f"{self.type_name}({body})"


class _OpaqueObject:
    """Stands in for an object that must not be rendered at all.

    Used for depth-limit cutoffs, cycles, and objects whose state could not be
    inspected -- rendering their real ``repr`` would defeat the traversal.
    """

    __slots__ = ("type_name",)

    def __init__(self, type_name: str) -> None:
        self.type_name = type_name

    def __repr__(self) -> str:
        return f"<{self.type_name}>"


def _object_fields(value: Any) -> "Optional[Dict[str, Any]]":
    """Return an object's inspectable attribute mapping, or None.

    ``None`` means "this object's state cannot be read", which is treated as
    unsafe to render rather than as safe to pass through.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        try:
            return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
        except Exception:  # noqa: BLE001 — a property may raise
            return None
    fields: Dict[str, Any] = {}
    found = False
    instance_dict = getattr(value, "__dict__", None)
    if isinstance(instance_dict, dict):
        found = True
        fields.update(instance_dict)
    for klass in type(value).__mro__:
        for name in getattr(klass, "__slots__", ()) or ():
            if not isinstance(name, str) or name.startswith("__"):
                continue
            found = True
            try:
                fields[name] = getattr(value, name)
            except AttributeError:
                continue
    return fields if found else None


def redact_for_repr(
    value: Any, depth: int = 0, _memo: Optional[Set[int]] = None
) -> Any:
    """Return a repr-safe stand-in for ``value``.

    Containers are traversed as in :func:`redact_sensitive`; user-defined
    objects additionally become :class:`_ObjectSurrogate` instances so that
    ``repr`` cannot print an attribute the denylist would have masked. Objects
    whose state cannot be inspected become :class:`_OpaqueObject` -- their real
    ``repr`` is never rendered, because that is precisely the path this
    function exists to close.
    """
    if _memo is None:
        _memo = set()
    if value is REDACTED:
        return value
    if value is None or isinstance(value, (bool, int, float, str, bytes, bytearray)):
        return value
    if isinstance(value, _OPAQUE_REPR_TYPES):
        return value
    if depth >= MAX_REPR_DEPTH:
        return _OpaqueObject(type(value).__name__)

    identity = id(value)
    if identity in _memo:
        return _OpaqueObject(f"recursive {type(value).__name__}")
    _memo = _memo | {identity}

    if isinstance(value, dict):
        return {
            key: (
                REDACTED
                if is_sensitive_key(key)
                else redact_for_repr(item, depth + 1, _memo)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_for_repr(item, depth + 1, _memo) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_for_repr(item, depth + 1, _memo) for item in value)
    if isinstance(value, (set, frozenset)):
        # Surrogates are unhashable, so a set is rendered as a list-backed
        # surrogate rather than reconstructed as a set.
        return _ObjectSurrogate(
            type(value).__name__,
            {"items": [redact_for_repr(item, depth + 1, _memo) for item in value]},
        )
    if isinstance(value, type) or inspect.isroutine(value):
        # Classes, functions and methods are identified by name. Their default
        # reprs also embed a memory address, which is noise in a trace. A
        # merely *callable* instance is not included here -- it is an ordinary
        # object whose attributes are worth showing once masked.
        return _OpaqueObject(
            getattr(value, "__qualname__", None)
            or getattr(value, "__name__", None)
            or type(value).__name__
        )

    fields = _object_fields(value)
    if fields is None:
        return _OpaqueObject(type(value).__name__)
    return _ObjectSurrogate(
        type(value).__name__,
        {
            name: (
                REDACTED
                if is_sensitive_key(name)
                else redact_for_repr(item, depth + 1, _memo)
            )
            for name, item in fields.items()
        },
    )


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
