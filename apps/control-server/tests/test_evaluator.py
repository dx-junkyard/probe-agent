"""Unit tests for the rule-based evaluator's normalization of SDK output.

The SDK serializes outputs with ``repr()``, so structured values arrive
double-wrapped. These tests pin the normalization behavior described in
issue #12 directly at the ``evaluate`` boundary.
"""

from app.evaluator import evaluate


def _status(criterion_type, expected, actual):
    return evaluate(criterion_type, expected, actual)[0]


# --- exact_match ----------------------------------------------------------


def test_exact_match_unwraps_repr_string():
    # repr("hello") == "'hello'"
    assert _status("exact_match", "hello", "'hello'") == "ok"


def test_exact_match_plain_string_still_matches():
    assert _status("exact_match", "hello", "hello") == "ok"


def test_exact_match_mismatch_is_ng():
    assert _status("exact_match", "hello", "'world'") == "ng"


# --- json_equal -----------------------------------------------------------


def test_json_equal_repr_of_json_string():
    # A function returning a JSON string; repr keeps the JSON intact but
    # wraps it in single quotes.
    actual = "'{\"a\":2,\"b\":1}'"
    assert _status("json_equal", '{"a":2,"b":1}', actual) == "ok"


def test_json_equal_python_dict_repr():
    assert _status("json_equal", '{"a":2,"b":1}', "{'a': 2, 'b': 1}") == "ok"


def test_json_equal_python_list_repr():
    assert _status("json_equal", "[1, 2, 3]", "[1, 2, 3]") == "ok"


def test_json_equal_differs_is_ng():
    assert _status("json_equal", '{"a":1}', "{'a': 2}") == "ng"


# --- required_keys --------------------------------------------------------


def test_required_keys_repr_of_json_object():
    actual = "'{\"a\":2,\"b\":1}'"
    assert _status("required_keys", '["a","b"]', actual) == "ok"


def test_required_keys_python_dict_repr():
    assert _status("required_keys", '["a","b"]', "{'a': 2, 'b': 1}") == "ok"


def test_required_keys_missing_is_ng():
    assert _status("required_keys", '["a","c"]', "{'a': 2, 'b': 1}") == "ng"


# --- contains / regex / natural_language ----------------------------------


def test_contains_behavior_unchanged():
    assert _status("contains", "ell", "'hello'") == "ok"
    assert _status("contains", "zzz", "'hello'") == "ng"


def test_regex_behavior_unchanged():
    assert _status("regex", r"^\d+$", "12345") == "ok"


def test_invalid_regex_needs_review():
    assert _status("regex", "[", "anything") == "needs_review"


def test_natural_language_needs_review():
    assert _status("natural_language", None, "whatever") == "needs_review"
