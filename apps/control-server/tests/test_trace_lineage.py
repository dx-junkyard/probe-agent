"""Tests for Issue #145 (Phase 1): trace lineage metadata + query API."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-lineage.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _trace(trace_id, component_id="c", ts=None, **lineage):
    ev = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": [], "kwargs": {}},
        "output": "'ok'",
        "error": None,
        "duration_ms": 1.0,
        "timestamp": ts if ts is not None else time.time(),
    }
    ev.update(lineage)
    return ev


# --- Backward compatibility -------------------------------------------------

def test_legacy_trace_without_lineage(client):
    r = client.post("/traces", json=_trace("t1"))
    assert r.status_code == 201
    r = client.get("/components/c/traces")
    assert r.status_code == 200
    assert r.json()[0]["trace_id"] == "t1"


def test_lineage_endpoints_empty_when_unused(client):
    client.post("/traces", json=_trace("t1"))
    assert client.get("/trace-lineage/entities/order/o1").json()["steps"] == []
    assert client.get("/trace-lineage/correlations/none").json()["steps"] == []
    assert client.get("/trace-lineage/flows/none").json()["steps"] == []


# --- Lineage queries --------------------------------------------------------

def test_entity_lineage_time_ordered(client):
    client.post("/traces", json=_trace(
        "t2", ts=200.0, entities=[{"type": "order", "id": "o1", "role": "source"}]))
    client.post("/traces", json=_trace(
        "t1", ts=100.0, entities=[{"type": "order", "id": "o1", "role": "derived"}]))
    client.post("/traces", json=_trace(
        "t3", ts=150.0, entities=[{"type": "order", "id": "o2"}]))

    steps = client.get("/trace-lineage/entities/order/o1").json()["steps"]
    assert [s["trace_id"] for s in steps] == ["t1", "t2"]  # time-ordered
    assert steps[0]["entities"][0]["role"] == "derived"


def test_time_window_filters_steps(client):
    """Issue #147: optional start/end (unix seconds) bound the lineage query."""
    for tid, ts in (("t1", 100.0), ("t2", 200.0), ("t3", 300.0)):
        client.post("/traces", json=_trace(
            tid, ts=ts, correlation_id="corr-w",
            entities=[{"type": "order", "id": "o1", "role": "source"}]))

    steps = client.get(
        "/trace-lineage/correlations/corr-w", params={"start": 150.0, "end": 250.0}
    ).json()["steps"]
    assert [s["trace_id"] for s in steps] == ["t2"]

    steps = client.get(
        "/trace-lineage/entities/order/o1", params={"start": 150.0}
    ).json()["steps"]
    assert [s["trace_id"] for s in steps] == ["t2", "t3"]


def test_correlation_lineage(client):
    client.post("/traces", json=_trace("b", ts=2.0, correlation_id="corr-1"))
    client.post("/traces", json=_trace("a", ts=1.0, correlation_id="corr-1"))
    client.post("/traces", json=_trace("c", ts=3.0, correlation_id="corr-2"))

    steps = client.get("/trace-lineage/correlations/corr-1").json()["steps"]
    assert [s["trace_id"] for s in steps] == ["a", "b"]


def test_flow_lineage_with_spans(client):
    client.post("/traces", json=_trace(
        "parent", ts=1.0, flow_id="f1", span_id="s1", correlation_id="k"))
    client.post("/traces", json=_trace(
        "child", ts=2.0, flow_id="f1", span_id="s2", parent_span_id="s1", correlation_id="k"))

    steps = client.get("/trace-lineage/flows/f1").json()["steps"]
    assert [s["trace_id"] for s in steps] == ["parent", "child"]
    assert steps[1]["parent_span_id"] == "s1"
    assert steps[1]["span_id"] == "s2"


def test_lineage_includes_projections(client):
    """Phase 3 (#147): lineage steps carry projected fields for the UI."""
    client.post("/traces", json=_trace(
        "t1", ts=1.0, correlation_id="k",
        projections=[{"projection_name": "orders", "phase": "output",
                      "fields": {"status": "pending"}}]))
    client.post("/traces", json=_trace(
        "t2", ts=2.0, correlation_id="k",
        projections=[{"projection_name": "orders", "phase": "output",
                      "fields": {"status": "charged"}}]))

    steps = client.get("/trace-lineage/correlations/k").json()["steps"]
    assert steps[0]["projections"][0]["fields"] == {"status": "pending"}
    assert steps[1]["projections"][0]["fields"] == {"status": "charged"}


def test_lineage_includes_replayability_for_workbench_actions(client):
    event = _trace(
        "replayable-trace",
        correlation_id="replay-correlation",
        replayability="partial",
        replay_reasons=["redacted"],
        input_capture={"args": ["[REDACTED]"], "kwargs": {}},
    )
    assert client.post("/traces", json=event).status_code == 201

    step = client.get(
        "/trace-lineage/correlations/replay-correlation"
    ).json()["steps"][0]
    assert step["replayability"] == "partial"
    assert step["replay_reasons"] == ["redacted"]


def test_repost_replaces_entities(client):
    client.post("/traces", json=_trace(
        "t1", entities=[{"type": "order", "id": "old"}]))
    client.post("/traces", json=_trace(
        "t1", entities=[{"type": "order", "id": "new"}]))
    # Old entity no longer resolves; new one does.
    assert client.get("/trace-lineage/entities/order/old").json()["steps"] == []
    assert len(client.get("/trace-lineage/entities/order/new").json()["steps"]) == 1


def test_repost_clears_stale_span(client):
    client.post("/traces", json=_trace("t1", correlation_id="c1", flow_id="f1"))
    assert len(client.get("/trace-lineage/correlations/c1").json()["steps"]) == 1
    # Re-post the same trace with no span metadata: the old correlation/flow
    # ids must not survive onto the new trace.
    client.post("/traces", json=_trace("t1"))
    assert client.get("/trace-lineage/correlations/c1").json()["steps"] == []
    assert client.get("/trace-lineage/flows/f1").json()["steps"] == []


def test_repost_clears_stale_projections(client):
    client.post("/traces", json=_trace("t1", projections=[
        {"projection_name": "orders", "phase": "output", "fields": {"status": "pending"}}]))
    assert len(client.get("/traces/t1/projections").json()) == 1
    # Re-post without projections: stale projection must not remain attached.
    client.post("/traces", json=_trace("t1"))
    assert client.get("/traces/t1/projections").json() == []


def test_repost_preserves_shadow_projections(client):
    # Shadow projections are written by a separate route on the same trace_id
    # and use a disjoint phase namespace; a /traces re-post must not clobber
    # them.
    client.post("/traces", json=_trace("t1"))
    client.post("/components/c/shadow-results", json={
        "trace_id": "t1", "component_id": "c",
        "current_output": "x", "candidate_output": "y",
        "candidate_error": None, "candidate_duration_ms": 1.0,
        "timestamp": time.time(),
        "projections": [
            {"projection_name": "orders", "phase": "shadow_current", "fields": {"status": "a"}},
            {"projection_name": "orders", "phase": "shadow_candidate", "fields": {"status": "b"}},
        ],
    })
    assert len(client.get("/traces/t1/projections").json()) == 2
    # Re-post the trace (no projections) — shadow projections stay intact.
    client.post("/traces", json=_trace("t1"))
    phases = {p["phase"] for p in client.get("/traces/t1/projections").json()}
    assert phases == {"shadow_current", "shadow_candidate"}


# --- System isolation -------------------------------------------------------

@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-lineage-iso.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _mk_system(client, token, name):
    r = client.post("/systems", json={"name": name}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_lineage_is_system_scoped(admin_client):
    token = _login(admin_client)
    sys_a = _mk_system(admin_client, token, "A")
    sys_b = _mk_system(admin_client, token, "B")

    def h(sid):
        return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(sid)}

    admin_client.post("/traces", json=_trace(
        "ta", correlation_id="shared", flow_id="fshared",
        entities=[{"type": "order", "id": "o1"}]), headers=h(sys_a))
    admin_client.post("/traces", json=_trace(
        "tb", correlation_id="shared", flow_id="fshared",
        entities=[{"type": "order", "id": "o1"}]), headers=h(sys_b))

    # Each system sees only its own lineage even with identical ids.
    a_ent = admin_client.get("/trace-lineage/entities/order/o1", headers=h(sys_a)).json()["steps"]
    b_ent = admin_client.get("/trace-lineage/entities/order/o1", headers=h(sys_b)).json()["steps"]
    assert [s["trace_id"] for s in a_ent] == ["ta"]
    assert [s["trace_id"] for s in b_ent] == ["tb"]

    a_corr = admin_client.get("/trace-lineage/correlations/shared", headers=h(sys_a)).json()["steps"]
    assert [s["trace_id"] for s in a_corr] == ["ta"]
    a_flow = admin_client.get("/trace-lineage/flows/fshared", headers=h(sys_a)).json()["steps"]
    assert [s["trace_id"] for s in a_flow] == ["ta"]
