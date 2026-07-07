"""Tests for Issue #146 (Phase 2): trace_projections ingest + query API."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-proj.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _trace(trace_id, component_id="c", projections=None, **extra):
    ev = {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "output": "'ok'",
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }
    if projections is not None:
        ev["projections"] = projections
    ev.update(extra)
    return ev


def test_backward_compat_no_projections(client):
    r = client.post("/traces", json=_trace("t1"))
    assert r.status_code == 201
    assert client.get("/traces/t1/projections").json() == []


def test_projection_ingest_and_get(client):
    proj = {
        "projection_name": "orders",
        "phase": "output",
        "fields": {"id": "o-1"},
        "metrics": {"n": 3},
        "samples": {"skus": ["s0", "s1"]},
        "data_hash": "abc",
        "truncated": False,
    }
    client.post("/traces", json=_trace("t1", projections=[proj]))
    rows = client.get("/traces/t1/projections").json()
    assert len(rows) == 1
    assert rows[0]["fields"] == {"id": "o-1"}
    assert rows[0]["metrics"] == {"n": 3}
    assert rows[0]["samples"] == {"skus": ["s0", "s1"]}
    assert rows[0]["data_hash"] == "abc"


def test_component_projections_listing(client):
    for i in range(3):
        client.post("/traces", json=_trace(
            f"t{i}", projections=[{"projection_name": "p", "phase": "output", "fields": {"i": i}}]))
    rows = client.get("/components/c/projections").json()
    assert len(rows) == 3
    assert all(r["projection_name"] == "p" for r in rows)


def test_input_and_output_phases(client):
    client.post("/traces", json=_trace("t1", projections=[
        {"projection_name": "p", "phase": "input", "fields": {"a": 1}},
        {"projection_name": "p", "phase": "output", "fields": {"b": 2}},
    ]))
    rows = client.get("/traces/t1/projections").json()
    phases = {r["phase"] for r in rows}
    assert phases == {"input", "output"}


def test_repost_replaces_projection(client):
    client.post("/traces", json=_trace("t1", projections=[
        {"projection_name": "p", "phase": "output", "fields": {"v": "old"}}]))
    client.post("/traces", json=_trace("t1", projections=[
        {"projection_name": "p", "phase": "output", "fields": {"v": "new"}}]))
    rows = client.get("/traces/t1/projections").json()
    assert len(rows) == 1
    assert rows[0]["fields"] == {"v": "new"}


def test_extract_error_recorded(client):
    client.post("/traces", json=_trace("t1", projections=[
        {"projection_name": "p", "phase": "output", "error": "boom", "data_hash": None}]))
    rows = client.get("/traces/t1/projections").json()
    assert rows[0]["error"] == "boom"


# --- isolation --------------------------------------------------------------

@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-proj-iso.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_projections_system_scoped(admin_client):
    r = admin_client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    token = r.json()["access_token"]

    def mk(name):
        rr = admin_client.post("/systems", json={"name": name},
                               headers={"Authorization": f"Bearer {token}"})
        return rr.json()["id"]

    a, b = mk("A"), mk("B")

    def h(sid):
        return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(sid)}

    admin_client.post("/traces", json=_trace(
        "t1", projections=[{"projection_name": "p", "phase": "output", "fields": {"s": "A"}}]),
        headers=h(a))
    # Same trace_id in system B must not collide or leak.
    admin_client.post("/traces", json=_trace(
        "t1", projections=[{"projection_name": "p", "phase": "output", "fields": {"s": "B"}}]),
        headers=h(b))

    assert admin_client.get("/traces/t1/projections", headers=h(a)).json()[0]["fields"] == {"s": "A"}
    assert admin_client.get("/traces/t1/projections", headers=h(b)).json()[0]["fields"] == {"s": "B"}
