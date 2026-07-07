import sys
from typing import List

import pytest


@pytest.fixture
def sdk(monkeypatch):
    """Reload probe_agent with a stub ControlClient and fresh contextvars."""
    monkeypatch.setenv("PROBE_ENABLED", "true")
    monkeypatch.setenv("PROBE_DEFAULT_MODE", "trace")
    monkeypatch.setenv("PROBE_POLICY_TTL", "0.0")

    for mod in [
        "probe_agent.decorator",
        "probe_agent.policy",
        "probe_agent.client",
        "probe_agent.config",
        "probe_agent.context",
        "probe_agent",
    ]:
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

    return {
        "probe_agent": probe_agent,
        "decorator_mod": decorator_mod,
        "traces": sent_traces,
        "shadows": sent_shadows,
        "set_mode": lambda m: policy_value.update(mode=m),
    }


def test_backward_compat_no_context(sdk):
    """Without a probe_context, only span_id is attached; no correlation/flow."""
    probe = sdk["decorator_mod"].probe

    @probe(component_id="c")
    def f(x):
        return x

    f(1)
    t = sdk["traces"][0]
    assert t["span_id"]  # always present when tracing
    assert "correlation_id" not in t
    assert "flow_id" not in t
    assert "entities" not in t
    assert "parent_span_id" not in t


def test_probe_context_shares_correlation_and_flow(sdk):
    probe = sdk["decorator_mod"].probe
    probe_context = sdk["probe_agent"].probe_context

    @probe(component_id="a")
    def a(x):
        return x

    @probe(component_id="b")
    def b(x):
        return x

    with probe_context(correlation_id="corr-1", flow_id="flow-1"):
        a(1)
        b(2)

    t0, t1 = sdk["traces"]
    assert t0["correlation_id"] == "corr-1" == t1["correlation_id"]
    assert t0["flow_id"] == "flow-1" == t1["flow_id"]
    # Different spans for different probe invocations.
    assert t0["span_id"] != t1["span_id"]


def test_probe_context_autogenerates_ids(sdk):
    probe = sdk["decorator_mod"].probe
    probe_context = sdk["probe_agent"].probe_context

    @probe(component_id="a")
    def a(x):
        return x

    with probe_context():
        a(1)
        a(2)

    t0, t1 = sdk["traces"]
    assert t0["correlation_id"] and t0["correlation_id"] == t1["correlation_id"]
    assert t0["flow_id"] and t0["flow_id"] == t1["flow_id"]


def test_nested_probe_sets_parent_span(sdk):
    probe = sdk["decorator_mod"].probe
    probe_context = sdk["probe_agent"].probe_context

    @probe(component_id="inner")
    def inner(x):
        return x + 1

    @probe(component_id="outer")
    def outer(x):
        return inner(x) * 2

    with probe_context(correlation_id="c"):
        outer(1)

    inner_t = next(t for t in sdk["traces"] if t["component_id"] == "inner")
    outer_t = next(t for t in sdk["traces"] if t["component_id"] == "outer")
    assert inner_t["parent_span_id"] == outer_t["span_id"]
    assert inner_t["correlation_id"] == "c" == outer_t["correlation_id"]


def test_entities_from_decorator_and_context(sdk):
    probe = sdk["decorator_mod"].probe
    probe_context = sdk["probe_agent"].probe_context
    add_entity = sdk["probe_agent"].add_entity

    @probe(component_id="order", entities=[{"type": "order", "id": "o-1", "role": "source"}])
    def handle(x):
        return x

    with probe_context(entities=[{"type": "tenant", "id": "t-9", "role": "related"}]):
        add_entity("user", "u-2", "related")
        handle(1)

    t = sdk["traces"][0]
    ents = {(e["type"], e["id"], e["role"]) for e in t["entities"]}
    assert ("order", "o-1", "source") in ents
    assert ("tenant", "t-9", "related") in ents
    assert ("user", "u-2", "related") in ents


def test_malformed_entity_is_dropped_not_fatal(sdk):
    probe = sdk["decorator_mod"].probe

    # Missing id -> dropped; unknown role -> coerced to related.
    @probe(component_id="c", entities=[{"type": "x"}, {"type": "y", "id": "1", "role": "weird"}])
    def f(x):
        return x

    assert f(1) == 1
    t = sdk["traces"][0]
    ents = {(e["type"], e["id"], e["role"]) for e in t.get("entities", [])}
    assert ("y", "1", "related") in ents
    assert all(e["type"] != "x" for e in t.get("entities", []))


def test_off_mode_adds_no_lineage(sdk):
    sdk["set_mode"]("off")
    probe = sdk["decorator_mod"].probe
    probe_context = sdk["probe_agent"].probe_context

    @probe(component_id="c")
    def f(x):
        return x

    with probe_context(correlation_id="c"):
        assert f(1) == 1
    assert sdk["traces"] == []


def test_shadow_thread_inherits_lineage(sdk):
    """A candidate's nested probe must stay on the same lineage as the
    original trace (contextvars are copied into the shadow thread)."""
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate
    probe_context = sdk["probe_agent"].probe_context
    flush = sdk["decorator_mod"].flush

    @probe(component_id="nested")
    def nested(x):
        return x + 1

    def candidate(x):
        return nested(x) * 3

    set_candidate("root", candidate)
    sdk["set_mode"]("shadow")

    @probe(component_id="root")
    def root(x):
        return x * 2

    with probe_context(correlation_id="corr-xyz"):
        assert root(5) == 10

    flush(timeout=2.0)

    root_t = next(t for t in sdk["traces"] if t["component_id"] == "root")
    nested_t = next(t for t in sdk["traces"] if t["component_id"] == "nested")
    # nested trace ran inside the shadow thread but shares the lineage.
    assert nested_t["correlation_id"] == "corr-xyz"
    assert nested_t["parent_span_id"] == root_t["span_id"]
