import sys
from typing import List

import pytest


@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setenv("PROBE_ENABLED", "true")
    monkeypatch.setenv("PROBE_DEFAULT_MODE", "trace")
    monkeypatch.setenv("PROBE_POLICY_TTL", "0.0")
    monkeypatch.setenv("PROBE_PAYLOAD_MODE", "full")
    for mod in [
        "probe_agent.decorator", "probe_agent.policy", "probe_agent.client",
        "probe_agent.config", "probe_agent.context", "probe_agent.projection",
        "probe_agent",
    ]:
        sys.modules.pop(mod, None)
    import probe_agent  # noqa: F401
    from probe_agent import decorator as decorator_mod
    from probe_agent.policy import PolicyCache

    traces: List[dict] = []

    class FakeClient:
        def send_trace(self, t):
            traces.append(t)

        def send_shadow_result(self, s):
            pass

        def get_policy(self, _cid):
            return {"mode": "trace"}

    fake = FakeClient()
    decorator_mod._client = fake
    decorator_mod._policy_cache = PolicyCache(client=fake, ttl=0.0)
    decorator_mod._candidates.clear()
    decorator_mod._projections.clear()
    return {"probe_agent": probe_agent, "decorator_mod": decorator_mod, "traces": traces}


def test_sampled_in_is_deterministic():
    from probe_agent.decorator import _sampled_in
    # Same trace_id => same decision, regardless of how many times we ask.
    tid = "abc-123"
    assert _sampled_in(tid, 0.5) == _sampled_in(tid, 0.5)
    assert _sampled_in(tid, None) is True
    assert _sampled_in(tid, 0.0) is False
    assert _sampled_in(tid, 1.0) is True


def test_rate_zero_drops_lineage_and_projection_but_keeps_trace(sdk):
    probe = sdk["decorator_mod"].probe
    probe_context = sdk["probe_agent"].probe_context

    @probe(component_id="c", sample_rate=0.0, projection={
        "name": "p", "output": {"fields": {"v": "$.v"}}})
    def f(x):
        return {"v": x}

    with probe_context(correlation_id="corr-1"):
        assert f(1) == {"v": 1}  # trace body unaffected

    t = sdk["traces"][0]
    # Trace body kept in full...
    assert t["component_id"] == "c"
    assert t["output"] is not None
    # ...but lineage + projection sampled out.
    assert "correlation_id" not in t
    assert "span_id" not in t
    assert "projections" not in t


def test_rate_one_keeps_everything(sdk):
    probe = sdk["decorator_mod"].probe
    probe_context = sdk["probe_agent"].probe_context

    @probe(component_id="c", sample_rate=1.0, projection={
        "name": "p", "output": {"fields": {"v": "$.v"}}})
    def f(x):
        return {"v": x}

    with probe_context(correlation_id="corr-1"):
        f(1)

    t = sdk["traces"][0]
    assert t["correlation_id"] == "corr-1"
    assert t["projections"][0]["fields"] == {"v": 1}


def test_approx_fraction_kept():
    from probe_agent.decorator import _sampled_in
    kept = sum(1 for i in range(2000) if _sampled_in(f"trace-{i}", 0.25))
    # Hash-based sampling should land near the target rate.
    assert 300 < kept < 700
