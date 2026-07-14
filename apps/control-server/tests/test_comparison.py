"""Direct unit tests for app/comparison.py (extracted from Issue #150 for
Issue #245's Replay Phase C, per app/comparison.py's module docstring).

test_shadow_diff.py exercises the same rules end-to-end through the analyzer
API and must keep passing unchanged; these tests hit the primitives
directly.
"""

import math

from app.comparison import ABSENT, diff_fields, field_equal, value_equal


def test_two_missing_are_equal():
    assert field_equal(ABSENT, ABSENT) is True


def test_missing_vs_null_are_not_equal():
    assert field_equal(ABSENT, None) is False
    assert field_equal(None, ABSENT) is False


def test_null_vs_null_are_equal():
    assert field_equal(None, None) is True


def test_nan_never_equal_including_itself():
    nan_a = float("nan")
    nan_b = float("nan")
    assert field_equal(nan_a, nan_b) is False
    assert field_equal(nan_a, nan_a) is False
    assert field_equal(nan_a, 1.0) is False
    assert field_equal(1.0, nan_a) is False


def test_ordinary_equality():
    assert field_equal(1, 1) is True
    assert field_equal(1, 2) is False
    assert field_equal("a", "a") is True
    assert field_equal([1, 2], [1, 2]) is True
    assert field_equal({"x": 1}, {"x": 1}) is True
    assert field_equal({"x": 1}, {"x": 2}) is False


def test_value_equal_matches_field_equal_for_present_values():
    assert value_equal(1, 1) is True
    assert value_equal(1, 2) is False
    assert value_equal(float("nan"), float("nan")) is False
    assert value_equal("a", "a") is True


def test_diff_fields_missing_key_vs_null():
    diffs = diff_fields({"status": None}, {})
    assert diffs == ["status"]


def test_diff_fields_two_missing_keys_are_not_a_diff():
    diffs = diff_fields({"a": 1}, {"a": 1})
    assert diffs == []


def test_diff_fields_union_of_keys():
    diffs = diff_fields({"a": 1, "b": 2}, {"a": 1, "c": 3})
    assert diffs == ["b", "c"]


def test_diff_fields_nan_value():
    diffs = diff_fields({"value": float("nan")}, {"value": float("nan")})
    assert diffs == ["value"]


def test_diff_fields_sorted_and_deterministic():
    a = {"z": 1, "a": 1, "m": 1}
    b = {"z": 2, "a": 1, "m": 2}
    assert diff_fields(a, b) == ["m", "z"]
    # Repeated calls are stable (no reliance on dict iteration order).
    assert diff_fields(a, b) == diff_fields(a, b)


def test_diff_fields_no_diff_returns_empty_list():
    assert diff_fields({"a": 1, "b": 2}, {"b": 2, "a": 1}) == []


def test_math_isnan_sanity_matches_field_equal_nan_handling():
    # Cross-check against math.isnan so the float(x) != float(x) trick isn't
    # accidentally relying on something Python-version-specific.
    assert math.isnan(float("nan"))
    assert not field_equal(float("nan"), float("nan"))
