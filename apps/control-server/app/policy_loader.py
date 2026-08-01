"""Shared strict-YAML helpers for reviewable policy artifacts.

Both the Alignment review policy (Issue #313) and the Interview metric
attention policy (Issue #341) are external YAML files that must fail closed:
no expressions, no unknown keys, no duplicate keys, no silent defaults.  These
helpers hold the common validation vocabulary so each policy module only has
to describe its own finite fields.

Every helper takes the caller's own error class, so a failed load raises the
error type that module already documents as fail-closed.
"""

from __future__ import annotations

from typing import Any, Mapping, Set, Tuple, Type

import yaml


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader which rejects duplicate mapping keys."""


class _DuplicatePolicyKey(yaml.YAMLError):
    """Raised while parsing.

    It subclasses ``yaml.YAMLError`` so every policy loader's existing
    ``except yaml.YAMLError`` turns a duplicate key into that module's own
    fail-closed policy error.
    """


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise _DuplicatePolicyKey(f"duplicate policy key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping,
)


def require_mapping(value: Any, label: str, error_cls: Type[Exception]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise error_cls(f"{label} must be an object with string keys")
    return value


def require_exact_keys(
    value: Mapping[str, Any], required: Set[str], label: str, error_cls: Type[Exception],
) -> None:
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        unexpected = sorted(actual - required)
        raise error_cls(
            f"{label} must contain exactly {sorted(required)!r}; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )


def require_nonempty_string(value: Any, label: str, error_cls: Type[Exception]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_cls(f"{label} must be a non-empty string")
    return value


def require_enum(
    value: Any, allowed: Tuple[str, ...], label: str, error_cls: Type[Exception],
) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise error_cls(f"{label} must be one of {list(allowed)!r}")
    return value


def parse_enum_list(
    value: Any, allowed: Tuple[str, ...], label: str, error_cls: Type[Exception],
) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise error_cls(f"{label} must be a non-empty list")
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise error_cls(f"{label} must be drawn from {allowed!r}")
    if len(set(value)) != len(value):
        raise error_cls(f"{label} must not contain duplicate values")
    return tuple(value)
