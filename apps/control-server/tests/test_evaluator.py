"""Unit tests for the rule-based evaluator normalization.

These cover SDK ``repr()``-serialized outputs (issue #11): a function's
return value is stored after ``repr()``, so JSON strings arrive wrapped in
Python quotes and dicts arrive as Python dict literals. The evaluator must
normalize these before applying deterministic criteria.
"""

import pytest

from app.evaluator import _normalize, evaluate


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"a": 2, "b": 1}', {"a": 2, "b": 1}),  # raw JSON object
        ("'{\"a\": 2, \"b\": 1}'", {"a": 2, "b": 1}),  # repr of a JSON string
        ("{'a': 2, 'b': 1}", {"a": 2, "b": 1}),  # repr of a dict
        ("'hello'", "hello"),  # repr of a plain string
        ("hello", "hello"),  # plain unparseable string -> fallback
        ("'[1, 2]'", [1, 2]),  # repr of a JSON array string
    ],
)
def test_normalize_values(text, expected):
    value, _ = _normalize(text)
    assert value == expected


def test_normalize_plain_string_is_unparsed():
    value, parsed = _normalize("hello")
    assert value == "hello"
    assert parsed is False


def test_normalize_none_and_empty():
    assert _normalize(None) == (None, False)
    assert _normalize("   ") == ("", False)


# --- exact_match ---

@pytest.mark.parametrize(
    "actual,expected",
    [
        ("'hello'", "hello"),  # SDK repr of expected string
        ("hello", "hello"),  # raw match
        ("{'a': 1}", "{'a': 1}"),  # identical raw text
    ],
)
def test_exact_match_ok(actual, expected):
    status, score, _ = evaluate("exact_match", expected, actual)
    assert status == "ok"
    assert score == 1.0


def test_exact_match_ng():
    status, score, _ = evaluate("exact_match", "world", "'hello'")
    assert status == "ng"
    assert score == 0.0


# --- json_equal ---

def test_json_equal_repr_wrapped_json_string():
    # actual is repr() of a JSON string; expected is JSON with reordered keys
    status, _, _ = evaluate("json_equal", '{"b":1,"a":2}', "'{\"a\":2,\"b\":1}'")
    assert status == "ok"


def test_json_equal_python_dict_repr():
    status, _, _ = evaluate("json_equal", '{"a":2,"b":1}', "{'a': 2, 'b': 1}")
    assert status == "ok"


def test_json_equal_repr_empty_object():
    status, _, _ = evaluate("json_equal", "{}", "'{}'")
    assert status == "ok"


def test_json_equal_invalid_expected_needs_review():
    status, score, _ = evaluate("json_equal", "not json", '{"a":1}')
    assert status == "needs_review"
    assert score is None


def test_json_equal_unparseable_actual_is_ng():
    status, score, _ = evaluate("json_equal", '{"a":1}', "not json at all")
    assert status == "ng"
    assert score == 0.0


# --- required_keys ---

def test_required_keys_repr_wrapped_json_object():
    status, _, _ = evaluate("required_keys", '["a","b"]', "'{\"a\":2,\"b\":1}'")
    assert status == "ok"


def test_required_keys_python_dict_repr():
    status, _, _ = evaluate("required_keys", '["a","b"]', "{'a': 2, 'b': 1}")
    assert status == "ok"


def test_required_keys_missing_key_is_ng():
    status, score, _ = evaluate("required_keys", '["a","b"]', "'{\"a\":2}'")
    assert status == "ng"
    assert score == 0.0


def test_required_keys_non_array_expected_needs_review():
    status, score, _ = evaluate("required_keys", '{"a":1}', '{"a":1}')
    assert status == "needs_review"
    assert score is None


# --- regressions for unchanged behavior ---

def test_natural_language_needs_review():
    status, score, _ = evaluate("natural_language", None, "anything")
    assert status == "needs_review"
    assert score is None


def test_invalid_regex_needs_review():
    status, score, _ = evaluate("regex", "[unclosed", "abc")
    assert status == "needs_review"
    assert score is None


def test_contains_uses_raw_text():
    status, _, _ = evaluate("contains", "hello", "'hello world'")
    assert status == "ok"
