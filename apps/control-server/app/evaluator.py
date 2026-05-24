"""Deterministic, rule-based evaluation of trace outputs against criteria.

The MVP intentionally avoids any LLM-based judgement. ``natural_language``
criteria are never decided automatically; they are recorded as
``needs_review`` for a human to inspect.
"""

import ast
import json
import re
from typing import Any, Optional, Tuple


def _coerce(text: Optional[str]) -> Tuple[Any, bool]:
    """Best-effort parse of a serialized output into a Python object.

    The SDK serializes outputs with ``repr()``; JSON payloads are also
    common. Returns ``(value, ok)`` where ``ok`` is False if the text
    could not be parsed into structured data.
    """
    if text is None:
        return None, False
    raw = text.strip()
    if not raw:
        return None, False
    try:
        return json.loads(raw), True
    except (ValueError, TypeError):
        pass
    try:
        return ast.literal_eval(raw), True
    except (ValueError, SyntaxError, TypeError):
        pass
    return None, False


def evaluate(
    criterion_type: str,
    expected_value: Optional[str],
    actual_output: Optional[str],
) -> Tuple[str, Optional[float], str]:
    """Evaluate one criterion against an output.

    Returns ``(status, score, reason)`` where status is one of
    ``ok`` / ``ng`` / ``needs_review`` and score is in ``[0.0, 1.0]``
    for deterministic checks (``None`` for ``needs_review``).
    """
    actual = actual_output if actual_output is not None else ""

    if criterion_type == "natural_language":
        return "needs_review", None, "natural_language requires manual review"

    if criterion_type == "exact_match":
        expected = expected_value or ""
        if actual.strip() == expected.strip():
            return "ok", 1.0, "output matches expected value exactly"
        return "ng", 0.0, "output does not match expected value"

    if criterion_type == "contains":
        needle = expected_value or ""
        if needle in actual:
            return "ok", 1.0, "output contains expected substring"
        return "ng", 0.0, "output does not contain expected substring"

    if criterion_type == "regex":
        pattern = expected_value or ""
        try:
            if re.search(pattern, actual):
                return "ok", 1.0, "output matches regex"
            return "ng", 0.0, "output does not match regex"
        except re.error as exc:
            return "needs_review", None, f"invalid regex: {exc}"

    if criterion_type == "json_equal":
        expected_obj, exp_ok = _coerce(expected_value)
        if not exp_ok:
            return "needs_review", None, "expected_value is not valid JSON"
        actual_obj, act_ok = _coerce(actual)
        if not act_ok:
            return "ng", 0.0, "output is not parseable as JSON"
        if actual_obj == expected_obj:
            return "ok", 1.0, "output is structurally equal to expected JSON"
        return "ng", 0.0, "output differs from expected JSON"

    if criterion_type == "required_keys":
        keys_obj, keys_ok = _coerce(expected_value)
        if not keys_ok or not isinstance(keys_obj, list):
            return "needs_review", None, "expected_value must be a JSON array of keys"
        actual_obj, act_ok = _coerce(actual)
        if not act_ok or not isinstance(actual_obj, dict):
            return "ng", 0.0, "output is not a JSON object"
        missing = [k for k in keys_obj if k not in actual_obj]
        if not missing:
            return "ok", 1.0, "all required keys present"
        return "ng", 0.0, f"missing keys: {', '.join(str(k) for k in missing)}"

    return "needs_review", None, f"unknown criterion_type: {criterion_type}"
