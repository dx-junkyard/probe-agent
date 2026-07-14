"""Shared-schema contract tests (Principle 3, Issue #242 Phase A / #243).

Validates example payloads — and the payloads the SDK actually builds — against
shared/schemas/trace_event.schema.json and shadow_result.schema.json, and
cross-checks them against the server Pydantic models so SDK, schema, and server
stay one contract.
"""

import json
import sys
import time
from pathlib import Path
from typing import List

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "shared" / "schemas"
SDK_PATH = str(REPO_ROOT / "packages" / "python-probe")
if SDK_PATH not in sys.path:
    sys.path.insert(0, SDK_PATH)


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def trace_event_schema() -> dict:
    return _schema("trace_event.schema.json")


@pytest.fixture(scope="module")
def shadow_result_schema() -> dict:
    return _schema("shadow_result.schema.json")


# --- example payloads ---------------------------------------------------------

def test_old_style_trace_event_validates(trace_event_schema):
    event = {
        "trace_id": "t1",
        "component_id": "summarizer",
        "mode": "trace",
        "input": {"args": ["'hi'"], "kwargs": {}},
        "output": "'HI'",
        "error": None,
        "duration_ms": 1.23,
        "timestamp": time.time(),
    }
    jsonschema.validate(event, trace_event_schema)
    from app.models import TraceEvent
    parsed = TraceEvent(**event)
    assert parsed.input_capture is None
    assert parsed.replayability is None
    assert parsed.replay_reasons is None


def test_full_featured_trace_event_with_capture_validates(trace_event_schema):
    event = {
        "trace_id": "t2",
        "component_id": "orders",
        "mode": "shadow",
        "input": {"args": ["(1, 2)"], "kwargs": {"tag": "'x'"}},
        "output": "'ok'",
        "error": None,
        "duration_ms": 2.0,
        "timestamp": time.time(),
        "span_id": "s1",
        "parent_span_id": None,
        "flow_id": "checkout",
        "correlation_id": "req-1",
        "entities": [{"type": "order", "id": "o-1", "role": "source"}],
        "projections": [{
            "projection_name": "orders",
            "phase": "output",
            "fields": {"id": "o-1"},
            "metrics": {"n": 3},
            "samples": {},
            "data_hash": "abc",
            "truncated": False,
        }],
        "input_capture": {
            "args": [
                {"__probe__": "tuple", "items": [1, 2]},
                {"__probe__": "bytes", "b64": "AAE="},
                {"__probe__": "float", "value": "nan"},
                {"__probe__": "dict", "items": [[1, "one"]]},
                {"__probe__": "unsupported", "type": "Socket"},
            ],
            "kwargs": {"tag": {"__probe__": "set", "items": ["a", "b"]}},
        },
        "replayability": "partial",
        "replay_reasons": ["unsupported_type"],
    }
    jsonschema.validate(event, trace_event_schema)
    from app.models import TraceEvent
    parsed = TraceEvent(**event)
    assert parsed.replayability == "partial"
    assert parsed.replay_reasons == ["unsupported_type"]


def test_unknown_replay_enum_values_fail_schema(trace_event_schema):
    event = {
        "trace_id": "t3",
        "component_id": "c",
        "timestamp": time.time(),
        "replayability": "maybe",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(event, trace_event_schema)
    event["replayability"] = "partial"
    event["replay_reasons"] = ["not-a-reason"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(event, trace_event_schema)


def test_sdk_shaped_shadow_result_validates(shadow_result_schema):
    payload = {
        "trace_id": "t1",
        "component_id": "svc",
        "current_output": "'A'",
        "candidate_output": None,
        "candidate_error": "RuntimeError: boom",
        "candidate_duration_ms": 0.5,
        "timestamp": time.time(),
        "projections": [{
            "projection_name": "orders",
            "phase": "shadow_current",
            "fields": {"status": "pending"},
            "metrics": {},
            "samples": {},
            "data_hash": "abc",
            "truncated": False,
        }],
    }
    jsonschema.validate(payload, shadow_result_schema)
    from app.models import ShadowResult
    parsed = ShadowResult(**payload)
    assert parsed.candidate_error == "RuntimeError: boom"


# --- the SDK's real payloads validate against the shared schemas --------------

@pytest.fixture
def sdk(monkeypatch):
    """Reload probe_agent with a stub ControlClient (same pattern as the SDK's
    own tests) so we can capture the exact payloads the decorator builds."""
    monkeypatch.setenv("PROBE_ENABLED", "true")
    monkeypatch.setenv("PROBE_DEFAULT_MODE", "trace")
    monkeypatch.setenv("PROBE_POLICY_TTL", "0.0")
    for mod in list(sys.modules):
        if mod == "probe_agent" or mod.startswith("probe_agent."):
            sys.modules.pop(mod, None)

    import probe_agent  # noqa: F401
    from probe_agent import decorator as decorator_mod
    from probe_agent.policy import PolicyCache

    sent_traces: List[dict] = []
    sent_shadows: List[dict] = []
    policy_value = {"mode": "trace"}

    class FakeClient:
        def send_trace(self, t):
            sent_traces.append(t)

        def send_shadow_result(self, s):
            sent_shadows.append(s)

        def get_policy(self, _cid):
            return dict(policy_value)

    fake = FakeClient()
    decorator_mod._client = fake
    decorator_mod._policy_cache = PolicyCache(client=fake, ttl=0.0)
    decorator_mod._candidates.clear()
    decorator_mod._projections.clear()
    return {
        "decorator_mod": decorator_mod,
        "traces": sent_traces,
        "shadows": sent_shadows,
        "set_mode": lambda m: policy_value.update(mode=m),
    }


def test_actual_sdk_trace_dict_validates(sdk, trace_event_schema):
    probe = sdk["decorator_mod"].probe

    @probe(
        component_id="schema-check",
        replay_capture={"redact": ["$.kwargs.password"]},
        entities=[{"type": "order", "id": "o-1", "role": "source"}],
        projection={
            "name": "orders",
            "output": {"fields": {"ok": "$.ok"}},
        },
    )
    def handle(point, password=None, blob=None):
        return {"ok": True}

    handle((1, 2), password="hunter2", blob=b"\x00\x01")
    assert len(sdk["traces"]) == 1
    trace = sdk["traces"][0]
    # Must already be JSON-serialisable (the real client sends it as JSON).
    trace = json.loads(json.dumps(trace, ensure_ascii=False))
    jsonschema.validate(trace, trace_event_schema)
    assert trace["replayability"] == "partial"
    assert trace["replay_reasons"] == ["redacted"]
    assert "hunter2" not in json.dumps(trace["input_capture"])
    # And the server model accepts the exact same payload.
    from app.models import TraceEvent
    TraceEvent(**trace)


def test_actual_sdk_shadow_payload_validates(sdk, shadow_result_schema):
    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate
    set_candidate("shadow-schema", lambda x: {"ok": x + 1})

    @probe(
        component_id="shadow-schema",
        projection={"name": "p", "output": {"fields": {"ok": "$.ok"}}},
    )
    def f(x):
        return {"ok": x}

    assert f(1) == {"ok": 1}
    sdk["decorator_mod"].flush(timeout=3.0)
    assert len(sdk["shadows"]) == 1
    payload = json.loads(json.dumps(sdk["shadows"][0], ensure_ascii=False))
    jsonschema.validate(payload, shadow_result_schema)
    phases = {p["phase"] for p in payload["projections"]}
    assert phases == {"shadow_current", "shadow_candidate"}
    from app.models import ShadowResult
    ShadowResult(**payload)
