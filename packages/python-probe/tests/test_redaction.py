from probe_agent import redaction as R


def test_recursive_exact_case_insensitive_denylist_is_non_mutating():
    source = {
        "Password": "p1",
        "password_hint": "not denied",
        "nested": [
            {"TOKEN": "t1", "access_token": "not denied"},
            ({"api_key": "k1", "id": 7},),
        ],
    }

    result = R.redact_sensitive(source)

    assert result["Password"] is R.REDACTED
    assert result["password_hint"] == "not denied"
    assert result["nested"][0]["TOKEN"] is R.REDACTED
    assert result["nested"][0]["access_token"] == "not denied"
    assert result["nested"][1][0]["api_key"] is R.REDACTED
    assert result["nested"][1][0]["id"] == 7
    assert source["Password"] == "p1"
    assert source["nested"][0]["TOKEN"] == "t1"


def test_non_mapping_values_pass_through_without_guessing():
    values = ["password=plain-text", 42, b"token", object()]
    result = R.redact_sensitive(values)
    assert result == values


def test_marker_literal_is_distinct_from_redacted_sentinel():
    result = R.redact_sensitive({
        "value": R.REDACTION_MARKER,
        "secret": R.REDACTION_MARKER,
    })
    assert result["value"] == R.REDACTION_MARKER
    assert result["value"] is not R.REDACTED
    assert result["secret"] is R.REDACTED


def test_path_redaction_is_shared_copy_on_write():
    root = {"items": [{"secret": "s", "id": 1}]}
    redacted, blocked = R.apply_path_redactions(
        root,
        [[("key", "items"), ("wild",), ("key", "secret")]],
    )
    assert blocked == []
    assert redacted["items"][0]["secret"] == R.REDACTION_MARKER
    assert root["items"][0]["secret"] == "s"
