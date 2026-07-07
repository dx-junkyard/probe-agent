"""Tests for Issue #148 (Phase 4a): analyzer spec validation + read-only exec."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-analyzer.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed(client, trace_id, component, status, entity_id="o-1", ts=None):
    client.post("/traces", json={
        "trace_id": trace_id,
        "component_id": component,
        "mode": "trace",
        "duration_ms": 1.0,
        "timestamp": ts if ts is not None else time.time(),
        "entities": [{"type": "order", "id": entity_id, "role": "source"}],
        "projections": [{
            "projection_name": "orders", "phase": "output",
            "fields": {"status": status}, "metrics": {}, "samples": {},
        }],
    })


def _spec(**over):
    spec = {
        "source": "trace_projections",
        "select": [
            {"name": "status", "path": "$.fields.status"},
            {"name": "trace", "path": "$.trace_id"},
        ],
    }
    spec.update(over)
    return spec


def _create(client, spec=None, name="a"):
    r = client.post("/trace-analyzers", json={"name": name, "spec": spec or _spec()})
    assert r.status_code == 201, r.text
    return r.json()


def _approve(client, aid):
    r = client.put(f"/trace-analyzers/{aid}/review", json={"review_status": "approved"})
    assert r.status_code == 200, r.text


# --- validation -------------------------------------------------------------

def test_invalid_spec_rejected(client):
    r = client.post("/trace-analyzers", json={"name": "x", "spec": {"source": "bad", "select": []}})
    assert r.status_code == 422
    r = client.post("/trace-analyzers", json={"name": "x", "spec": {
        "source": "trace_projections", "select": [{"name": "s", "path": "not-a-path"}]}})
    assert r.status_code == 422
    r = client.post("/trace-analyzers", json={"name": "x", "spec": {
        "source": "trace_projections",
        "select": [{"name": "s", "path": "$.fields.status"}],
        "group_by": ["nonexistent"]}})
    assert r.status_code == 422


def test_create_is_proposed_manual(client):
    a = _create(client)
    assert a["review_status"] == "proposed"
    assert a["decision_method"] == "manual"


# --- review gate ------------------------------------------------------------

def test_cannot_run_unless_approved(client):
    a = _create(client)
    r = client.post(f"/trace-analyzers/{a['id']}/runs")
    assert r.status_code == 409
    _approve(client, a["id"])
    r = client.post(f"/trace-analyzers/{a['id']}/runs")
    assert r.status_code == 201


def test_review_transitions(client):
    a = _create(client)
    r = client.put(f"/trace-analyzers/{a['id']}/review", json={"review_status": "rejected"})
    assert r.status_code == 200
    assert r.json()["review_status"] == "rejected"


def test_review_records_manual_decision(client):
    """The approve/reject act itself is audited as a manual decision
    (Principle 7), separate from who authored the spec."""
    a = _create(client)
    assert a["reviewed_at"] is None
    assert a["review_decision_method"] is None
    _approve(client, a["id"])
    got = client.get(f"/trace-analyzers/{a['id']}").json()
    assert got["review_status"] == "approved"
    assert got["reviewed_at"] is not None
    assert got["review_decision_method"] == "manual"


# --- execution --------------------------------------------------------------

def test_select_and_filter(client):
    _seed(client, "t1", "validate", "pending", ts=1.0)
    _seed(client, "t2", "charge", "charged", ts=2.0)
    _seed(client, "t3", "validate", "pending", entity_id="o-2", ts=3.0)

    a = _create(client, _spec(filter={"entity": {"type": "order", "id": "o-1"}}))
    _approve(client, a["id"])
    run = client.post(f"/trace-analyzers/{a['id']}/runs").json()
    assert run["status"] == "completed"
    statuses = sorted(r["status"] for r in run["result"]["rows"])
    assert statuses == ["charged", "pending"]  # only o-1 traces


def test_component_filter_and_order(client):
    _seed(client, "t1", "validate", "b", ts=1.0)
    _seed(client, "t2", "validate", "a", ts=2.0)
    _seed(client, "t3", "charge", "z", ts=3.0)

    a = _create(client, _spec(
        filter={"components": ["validate"]},
        order_by=[{"name": "status", "dir": "asc"}]))
    _approve(client, a["id"])
    run = client.post(f"/trace-analyzers/{a['id']}/runs").json()
    assert [r["status"] for r in run["result"]["rows"]] == ["a", "b"]


def test_group_by(client):
    _seed(client, "t1", "validate", "pending", ts=1.0)
    _seed(client, "t2", "charge", "pending", ts=2.0)
    _seed(client, "t3", "charge", "done", ts=3.0)

    a = _create(client, _spec(group_by=["status"]))
    _approve(client, a["id"])
    run = client.post(f"/trace-analyzers/{a['id']}/runs").json()
    groups = {g["key"]["status"]: g["count"] for g in run["result"]["groups"]}
    assert groups == {"pending": 2, "done": 1}


def test_input_row_limit_fails_run(client, monkeypatch):
    monkeypatch.setenv("ANALYZER_MAX_INPUT_ROWS", "1")
    _seed(client, "t1", "validate", "a", ts=1.0)
    _seed(client, "t2", "validate", "b", ts=2.0)
    a = _create(client)
    _approve(client, a["id"])
    run = client.post(f"/trace-analyzers/{a['id']}/runs").json()
    assert run["status"] == "failed"
    assert "exceed" in (run["error_details"] or "")


def test_run_is_readonly(client):
    _seed(client, "t1", "validate", "pending", ts=1.0)
    a = _create(client)
    _approve(client, a["id"])
    client.post(f"/trace-analyzers/{a['id']}/runs")
    # Underlying projection data is unchanged after a run.
    rows = client.get("/traces/t1/projections").json()
    assert rows[0]["fields"] == {"status": "pending"}


# --- isolation --------------------------------------------------------------

@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-analyzer-iso.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_analyzers_system_scoped(admin_client):
    token = admin_client.post("/auth/login", json={"username": "root", "password": "s3cret"}).json()["access_token"]

    def mk(name):
        return admin_client.post("/systems", json={"name": name},
                                 headers={"Authorization": f"Bearer {token}"}).json()["id"]

    a, b = mk("A"), mk("B")

    def h(sid):
        return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(sid)}

    r = admin_client.post("/trace-analyzers", json={"name": "sysA", "spec": _spec()}, headers=h(a))
    aid = r.json()["id"]
    # System B cannot see system A's analyzer.
    assert admin_client.get(f"/trace-analyzers/{aid}", headers=h(b)).status_code == 404
    assert admin_client.get("/trace-analyzers", headers=h(b)).json() == []
