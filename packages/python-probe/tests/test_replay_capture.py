"""Unit tests for structured replay capture (Issue #242 Phase A / #243)."""

import json
import math

import pytest

from probe_agent import projection as P
from probe_agent import replay_capture as RC
from probe_agent.replay_capture import (
    MARKER,
    PARTIAL,
    REPLAYABLE,
    UNREPLAYABLE,
    ReplayCaptureError,
    capture_input,
    compile_spec,
    decode_value,
    encode_value,
)


def _capture(args=(), kwargs=None, spec=True):
    return capture_input(args, dict(kwargs or {}), compile_spec(spec))


# --- spec validation (fail-closed) ------------------------------------------

def test_spec_true_and_redact_dict_accepted():
    assert compile_spec(True).redact == []
    spec = compile_spec({"redact": ["$.kwargs.password"]})
    assert len(spec.redact) == 1


def test_invalid_spec_rejected():
    with pytest.raises(ReplayCaptureError):
        compile_spec("yes")
    with pytest.raises(ReplayCaptureError):
        compile_spec({"bogus": 1})
    with pytest.raises(ReplayCaptureError):
        compile_spec({"redact": "$.kwargs.password"})  # not a list
    with pytest.raises(ValueError):  # ProjectionError from parse_path
        compile_spec({"redact": ["not-a-path"]})


# --- encode / decode round-trips ---------------------------------------------

@pytest.mark.parametrize("value", [
    None,
    True,
    False,
    0,
    -42,
    3.5,
    "plain",
    "こんにちは🎌",  # unicode survives ensure_ascii=False round-trips
    [1, "a", None],
    {"k": [1, {"n": 2}]},
    (1, 2, "x"),
    {1, 2, 3},
    frozenset({"a", "b"}),
    b"\x00\xff bytes",
    {1: "one", (2, 3): "pair"},          # non-string dict keys
    {"__probe__": "collision", "a": 1},  # literal marker key in a dict
    {"nested": ({"deep": [{4, 5}, (6,)]},)},
])
def test_round_trip_supported_values(value):
    flags = set()
    encoded = encode_value(value, flags)
    assert flags == set()
    # The encoding must be pure JSON.
    rehydrated = json.loads(json.dumps(encoded, ensure_ascii=False))
    decoded = decode_value(rehydrated)
    assert decoded == value
    assert type(decoded) is type(value)


def test_round_trip_non_finite_floats():
    flags = set()
    encoded = encode_value([float("nan"), float("inf"), float("-inf")], flags)
    assert flags == set()
    decoded = decode_value(json.loads(json.dumps(encoded)))
    assert math.isnan(decoded[0])
    assert decoded[1] == float("inf")
    assert decoded[2] == float("-inf")


def test_plain_decoded_dict_never_contains_marker():
    encoded = encode_value({"__probe__": "x"}, set())
    assert encoded[MARKER] == "dict"  # explicit encoding, not a plain object
    assert decode_value(encoded) == {"__probe__": "x"}


def test_set_encoding_is_deterministic_across_insertion_orders():
    a = set()
    for v in [3, 1, 2, "z", "a"]:
        a.add(v)
    b = set()
    for v in ["a", "z", 2, 1, 3]:
        b.add(v)
    enc_a = json.dumps(encode_value(a, set()), ensure_ascii=False)
    enc_b = json.dumps(encode_value(b, set()), ensure_ascii=False)
    assert enc_a == enc_b


def test_dict_encoding_is_canonical_across_insertion_orders():
    plain_a = {"z": 1, "a": {"y": 2, "b": 3}}
    plain_b = {"a": {"b": 3, "y": 2}, "z": 1}
    assert encode_value(plain_a, set()) == encode_value(plain_b, set())

    keyed_a = {2: "two", 1: "one"}
    keyed_b = {1: "one", 2: "two"}
    assert encode_value(keyed_a, set()) == encode_value(keyed_b, set())


def test_unsupported_type_marker_never_embeds_value_or_repr():
    class Secretive:
        def __repr__(self):
            return "SECRET-VALUE-XYZ"

    flags = set()
    encoded = encode_value({"v": Secretive()}, flags)
    assert flags == {RC.REASON_UNSUPPORTED_TYPE}
    dumped = json.dumps(encoded, ensure_ascii=False)
    assert "SECRET-VALUE-XYZ" not in dumped
    assert encoded["v"] == {MARKER: "unsupported", "type": "Secretive"}
    assert decode_value(encoded)["v"] is RC.UNDECODABLE


def test_depth_limit_marks_and_flags():
    v = 1
    for _ in range(RC.MAX_DEPTH + 5):
        v = [v]
    flags = set()
    encoded = encode_value(v, flags)
    assert RC.REASON_DEPTH_LIMIT in flags
    assert "depth_limit" in json.dumps(encoded)


# --- capture_input: classification -------------------------------------------

def test_capture_replayable_full_shape():
    cap, replayability, reasons = _capture(
        args=(1, "a", (2, 3)), kwargs={"opts": {"tags": {"x"}}},
    )
    assert replayability == REPLAYABLE
    assert reasons == []
    assert cap["args"][0] == 1
    assert cap["args"][2] == {MARKER: "tuple", "items": [2, 3]}
    assert cap["kwargs"]["opts"]["tags"] == {MARKER: "set", "items": ["x"]}


def test_capture_unsupported_value_is_partial():
    cap, replayability, reasons = _capture(args=(object(),))
    assert replayability == PARTIAL
    assert reasons == [RC.REASON_UNSUPPORTED_TYPE]
    assert cap["args"][0] == {MARKER: "unsupported", "type": "object"}


def test_capture_depth_limit_is_partial():
    v = 1
    for _ in range(RC.MAX_DEPTH + 5):
        v = [v]
    cap, replayability, reasons = _capture(args=(v,))
    assert replayability == PARTIAL
    assert reasons == [RC.REASON_DEPTH_LIMIT]
    assert cap is not None


def test_capture_determinism_same_input_same_result():
    args = ({3, 1, 2}, (1, 2))
    kwargs = {"b": frozenset({"y", "x"}), "a": 1}
    r1 = _capture(args=args, kwargs=kwargs)
    r2 = _capture(args=args, kwargs=kwargs)
    assert r1 == r2
    assert json.dumps(r1[0], ensure_ascii=False) == json.dumps(r2[0], ensure_ascii=False)


def test_capture_internal_failure_degrades_to_capture_failed():
    class EvilDict(dict):
        def items(self):
            raise RuntimeError("boom")

    cap, replayability, reasons = _capture(args=(EvilDict({"k": "v"}),))
    assert cap is None
    assert replayability == UNREPLAYABLE
    assert reasons == [RC.REASON_CAPTURE_FAILED]


# --- size limit ---------------------------------------------------------------

def test_size_limit_boundary(monkeypatch):
    cap, replayability, _ = _capture(args=("a" * 200,))
    assert replayability == REPLAYABLE
    exact = len(json.dumps(cap, ensure_ascii=False).encode("utf-8"))

    # Just at the cap: kept.
    monkeypatch.setenv("PROBE_REPLAY_CAPTURE_MAX_BYTES", str(exact))
    cap2, replayability2, reasons2 = _capture(args=("a" * 200,))
    assert replayability2 == REPLAYABLE
    assert cap2 == cap
    assert reasons2 == []

    # One byte under: the whole capture is dropped, never truncated.
    monkeypatch.setenv("PROBE_REPLAY_CAPTURE_MAX_BYTES", str(exact - 1))
    cap3, replayability3, reasons3 = _capture(args=("a" * 200,))
    assert cap3 is None
    assert replayability3 == UNREPLAYABLE
    assert reasons3 == [RC.REASON_SIZE_LIMIT]


# --- redaction ------------------------------------------------------------------

def test_redaction_masks_value_before_encoding():
    cap, replayability, reasons = _capture(
        kwargs={"password": "hunter2", "user": "u1"},
        spec={"redact": ["$.kwargs.password"]},
    )
    assert replayability == PARTIAL
    assert reasons == [RC.REASON_REDACTED]
    assert cap["kwargs"]["password"] == P._REDACTED
    assert cap["kwargs"]["user"] == "u1"
    assert "hunter2" not in json.dumps(cap, ensure_ascii=False)


def test_redact_path_absent_from_input_keeps_replayable():
    cap, replayability, reasons = _capture(
        kwargs={"user": "u1"},
        spec={"redact": ["$.kwargs.password"]},
    )
    assert replayability == REPLAYABLE
    assert reasons == []
    assert cap["kwargs"] == {"user": "u1"}


def test_blocked_redact_path_drops_capture_fail_closed():
    class Creds:
        def __init__(self):
            self.secret = "sk-LEAK"

    cap, replayability, reasons = _capture(
        kwargs={"creds": Creds()},
        spec={"redact": ["$.kwargs.creds.secret"]},
    )
    # The attribute cannot be masked structurally -> the entire capture is
    # dropped rather than stored with a possibly-leaking value.
    assert cap is None
    assert replayability == UNREPLAYABLE
    assert reasons == [RC.REASON_REDACTION_BLOCKED]


def test_redaction_inside_nested_structures():
    cap, replayability, reasons = _capture(
        args=([{"token": "tok-123", "id": 7}],),
        spec={"redact": ["$.args[0][*].token"]},
    )
    assert replayability == PARTIAL
    assert RC.REASON_REDACTED in reasons
    assert "tok-123" not in json.dumps(cap, ensure_ascii=False)
    assert cap["args"][0][0]["id"] == 7
