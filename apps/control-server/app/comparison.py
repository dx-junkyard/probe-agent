"""Shared deterministic field-equality primitives (Issue #150, extracted for #245).

probe-agent:
  role: Shared deterministic value/field equality rules
  capability: replay-simulation
  element_type: core
  consumers: [trace-analyzer, replay-variants]
  operation_kind: pure
  state_effects: []
  probe_value: Verify shadow-diff and replay-variant comparisons keep the
    exact same missing-key/null/NaN rules after extraction into one module.

This module was originally ``_field_equal`` / ``_ABSENT`` inside
``app/trace_analyzer.py`` (Issue #150's shadow-projection subset diff).
Issue #245 (Replay Phase C) needs the identical finite equality rules for
comparing baseline-replay output against candidate-replay output, so the
primitives were extracted here and ``trace_analyzer.py`` now imports them
instead of defining its own copy. ``tests/test_shadow_diff.py`` exercises
this behavior end-to-end through the analyzer API and must keep passing
unchanged.

Deterministic only (Principle 6): every function here is a pure structural
comparison over already-decoded JSON values. No reasoning model, no
heuristic scoring — just the documented finite rules below.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Sentinel meaning "this key/side was not present at all" (distinct from an
# explicit JSON ``null`` / Python ``None``).
ABSENT = object()


def field_equal(a: Any, b: Any) -> bool:
    """Deterministic field equality (documented finite rules):

      * a missing key (``ABSENT``) and an explicit ``null`` are NOT equal
        (presence differs);
      * two missing keys are equal;
      * NaN is never equal to anything, including another NaN;
      * otherwise standard ``==`` on the already-decoded values.
    """
    a_present = a is not ABSENT
    b_present = b is not ABSENT
    if a_present != b_present:
        return False
    if not a_present:
        return True
    if isinstance(a, float) and a != a:  # NaN
        return False
    if isinstance(b, float) and b != b:
        return False
    return a == b


def value_equal(a: Any, b: Any) -> bool:
    """Whole-value equality using the same NaN-never-equal rule.

    Both arguments are assumed always-present (no ``ABSENT`` concept) — this
    is what callers use for non-dict structured values, where there is no
    "missing key" axis, only "is this value equal to that value".
    """
    return field_equal(a, b)


def diff_fields(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    """Sorted field names that differ across the union of top-level keys of
    two dicts, using :func:`field_equal`'s missing-vs-null / NaN rules.

    ``dict.get(key, ABSENT)`` correctly distinguishes "key missing" from
    "key present with value None" because ``dict.get`` only returns the
    default when the key is absent.
    """
    keys = sorted(set(a.keys()) | set(b.keys()))
    return [key for key in keys if not field_equal(a.get(key, ABSENT), b.get(key, ABSENT))]
