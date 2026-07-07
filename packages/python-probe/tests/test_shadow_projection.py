import sys
from typing import List

import pytest


@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setenv("PROBE_ENABLED", "true")
    monkeypatch.setenv("PROBE_DEFAULT_MODE", "shadow")
    monkeypatch.setenv("PROBE_POLICY_TTL", "0.0")
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
    shadows: List[dict] = []
    policy = {"mode": "shadow"}

    class FakeClient:
        def send_trace(self, t):
            traces.append(t)

        def send_shadow_result(self, s):
            shadows.append(s)

        def get_policy(self, _cid):
            return dict(policy)

    fake = FakeClient()
    decorator_mod._client = fake
    decorator_mod._policy_cache = PolicyCache(client=fake, ttl=0.0)
    decorator_mod._candidates.clear()
    decorator_mod._projections.clear()
    return {"probe_agent": probe_agent, "decorator_mod": decorator_mod,
            "traces": traces, "shadows": shadows}


def _phases(shadow):
    return {p["phase"] for p in shadow.get("projections", [])}


def test_shadow_current_and_candidate_projections(sdk):
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate
    flush = sdk["decorator_mod"].flush

    set_candidate("svc", lambda x: {"status": "charged", "id": x})

    @probe(component_id="svc", projection={
        "name": "orders", "output": {"fields": {"status": "$.status"}}})
    def svc(x):
        return {"status": "pending", "id": x}

    assert svc("o-1") == {"status": "pending", "id": "o-1"}  # production unchanged
    flush(timeout=2.0)

    assert len(sdk["shadows"]) == 1
    s = sdk["shadows"][0]
    assert _phases(s) == {"shadow_current", "shadow_candidate"}
    by_phase = {p["phase"]: p for p in s["projections"]}
    assert by_phase["shadow_current"]["fields"] == {"status": "pending"}
    assert by_phase["shadow_candidate"]["fields"] == {"status": "charged"}


def test_candidate_error_yields_no_candidate_projection(sdk):
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate
    flush = sdk["decorator_mod"].flush

    def boom(_x):
        raise RuntimeError("candidate broke")

    set_candidate("svc", boom)

    @probe(component_id="svc", projection={
        "name": "orders", "output": {"fields": {"status": "$.status"}}})
    def svc(x):
        return {"status": "pending"}

    assert svc("o-1") == {"status": "pending"}  # unaffected by candidate error
    flush(timeout=2.0)

    s = sdk["shadows"][0]
    assert s["candidate_error"] is not None
    assert _phases(s) == {"shadow_current"}  # no shadow_candidate on error


def test_input_projection_reflects_precall_values_when_fn_mutates(sdk):
    """Issue #146 deepcopy interaction: the input projection is extracted
    before the function runs, so current and candidate share the same input
    projection even when the function mutates its arguments."""
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate
    flush = sdk["decorator_mod"].flush

    def candidate(order):
        # Candidate sees the pre-call snapshot: status must still be "new".
        return {"seen_status": order["status"]}

    set_candidate("svc", candidate)

    @probe(component_id="svc", projection={
        "name": "orders",
        "input": {"fields": {"status_in": "$.args[0].status"}},
        "output": {"fields": {"seen_status": "$.seen_status"}},
    })
    def svc(order):
        order["status"] = "mutated"
        return {"seen_status": order["status"]}

    svc({"status": "new"})
    flush(timeout=2.0)

    t = sdk["traces"][0]
    by_phase = {p["phase"]: p for p in t["projections"]}
    assert by_phase["input"]["fields"] == {"status_in": "new"}  # pre-call value
    s = sdk["shadows"][0]
    shadow_phases = {p["phase"]: p for p in s["projections"]}
    # Candidate ran on the pristine snapshot, unaffected by the mutation.
    assert shadow_phases["shadow_candidate"]["fields"] == {"seen_status": "new"}
    assert shadow_phases["shadow_current"]["fields"] == {"seen_status": "mutated"}


def test_no_projection_no_extra_cost(sdk):
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate
    flush = sdk["decorator_mod"].flush

    set_candidate("svc", lambda x: x)

    @probe(component_id="svc")
    def svc(x):
        return x

    svc("o-1")
    flush(timeout=2.0)
    assert "projections" not in sdk["shadows"][0]
