import importlib
import os
import sys
from typing import List

import pytest


@pytest.fixture
def sdk(monkeypatch):
    """Reload probe_agent with a stub ControlClient for each test."""
    monkeypatch.setenv("PROBE_ENABLED", "true")
    monkeypatch.setenv("PROBE_DEFAULT_MODE", "trace")
    monkeypatch.setenv("PROBE_POLICY_TTL", "0.0")

    # Reload modules so the patched env / fresh state apply.
    for mod in [
        "probe_agent.decorator",
        "probe_agent.policy",
        "probe_agent.client",
        "probe_agent.config",
        "probe_agent",
    ]:
        sys.modules.pop(mod, None)

    import probe_agent  # noqa: F401  (re-imported for side effects)
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

    return {
        "decorator_mod": decorator_mod,
        "traces": sent_traces,
        "shadows": sent_shadows,
        "set_mode": lambda m: policy_value.update(mode=m),
    }


def test_trace_records_input_output(sdk):
    probe = sdk["decorator_mod"].probe

    @probe(component_id="adder")
    def add(a, b):
        return a + b

    assert add(2, 3) == 5

    assert len(sdk["traces"]) == 1
    t = sdk["traces"][0]
    assert t["component_id"] == "adder"
    assert t["mode"] == "trace"
    assert t["error"] is None
    assert "5" in t["output"]
    assert t["input"]["args"] == ["2", "3"]
    assert t["duration_ms"] >= 0


def test_off_mode_skips_trace(sdk):
    sdk["set_mode"]("off")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="adder")
    def add(a, b):
        return a + b

    assert add(1, 2) == 3
    assert sdk["traces"] == []


def test_disabled_via_env(monkeypatch, sdk):
    monkeypatch.setenv("PROBE_ENABLED", "false")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="adder")
    def add(a, b):
        return a + b

    assert add(1, 2) == 3
    assert sdk["traces"] == []


def test_error_is_recorded_and_reraised(sdk):
    probe = sdk["decorator_mod"].probe

    @probe(component_id="boom")
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()

    assert len(sdk["traces"]) == 1
    assert sdk["traces"][0]["error"] is not None
    assert "ValueError" in sdk["traces"][0]["error"]


def test_shadow_runs_candidate(sdk):
    import time

    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate

    def candidate(x):
        return x + 100

    set_candidate("doubler", candidate)

    @probe(component_id="doubler")
    def doubler(x):
        return x * 2

    assert doubler(5) == 10  # current return value unchanged

    # candidate runs in a background thread; wait briefly
    for _ in range(50):
        if sdk["shadows"]:
            break
        time.sleep(0.02)

    assert len(sdk["shadows"]) == 1
    s = sdk["shadows"][0]
    assert s["component_id"] == "doubler"
    assert "10" in s["current_output"]
    assert "105" in s["candidate_output"]
    assert s["candidate_error"] is None


def test_shadow_candidate_failure_does_not_break_current(sdk):
    import time

    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate

    def bad(_):
        raise RuntimeError("candidate broken")

    set_candidate("safe", bad)

    @probe(component_id="safe")
    def safe(x):
        return x

    assert safe(7) == 7  # current is unaffected

    for _ in range(50):
        if sdk["shadows"]:
            break
        time.sleep(0.02)

    assert len(sdk["shadows"]) == 1
    assert sdk["shadows"][0]["candidate_error"] is not None
