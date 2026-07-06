"""Tests for Issue #152 (Phase 7): sampling determinism is covered in the SDK;
here we test retention policy + application + audit + isolation."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-retention.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _trace(client, trace_id, ts, entity_id="o-1"):
    client.post("/traces", json={
        "trace_id": trace_id, "component_id": "c", "mode": "trace",
        "duration_ms": 1.0, "timestamp": ts,
        "correlation_id": "k", "span_id": trace_id,
        "entities": [{"type": "order", "id": entity_id}],
        "projections": [{"projection_name": "p", "phase": "output", "fields": {"v": 1}}],
    })


def _spans(client):
    return len(client.get("/trace-lineage/correlations/k").json()["steps"])


def test_default_never_deletes(client):
    _trace(client, "t1", 1.0)
    _trace(client, "t2", 2.0)
    r = client.post("/retention/apply")
    assert r.status_code == 200
    assert r.json()["results"] == []  # no policy => nothing applied
    assert _spans(client) == 2


def test_max_count_keeps_newest(client):
    for i in range(5):
        _trace(client, f"t{i}", float(i))
    client.put("/retention/policies", json={"policies": [
        {"target_table": "trace_spans", "max_count": 2},
        {"target_table": "trace_projections", "max_count": 2},
        {"target_table": "trace_entities", "max_count": 2},
    ]})
    r = client.post("/retention/apply").json()
    deleted = {x["target_table"]: x["deleted_count"] for x in r["results"]}
    assert deleted["trace_spans"] == 3
    # Newest two spans (t3, t4) survive.
    steps = client.get("/trace-lineage/correlations/k").json()["steps"]
    assert {s["trace_id"] for s in steps} == {"t3", "t4"}


def test_max_age_deletes_old(client):
    now = time.time()
    _trace(client, "old", now - 100 * 86400)  # 100 days old
    _trace(client, "new", now)
    client.put("/retention/policies", json={"policies": [
        {"target_table": "trace_spans", "max_age_days": 30},
        {"target_table": "trace_projections", "max_age_days": 30},
    ]})
    client.post("/retention/apply")
    steps = client.get("/trace-lineage/correlations/k").json()["steps"]
    assert {s["trace_id"] for s in steps} == {"new"}


def test_audit_records_deletions(client):
    for i in range(3):
        _trace(client, f"t{i}", float(i))
    client.put("/retention/policies", json={"policies": [
        {"target_table": "trace_spans", "max_count": 1}]})
    client.post("/retention/apply")
    audit = client.get("/retention/audit").json()
    assert any(a["target_table"] == "trace_spans" and a["deleted_count"] == 2 for a in audit)


def test_clearing_bounds_removes_policy(client):
    client.put("/retention/policies", json={"policies": [
        {"target_table": "trace_spans", "max_count": 1}]})
    assert len(client.get("/retention/policies").json()) == 1
    client.put("/retention/policies", json={"policies": [
        {"target_table": "trace_spans"}]})  # both bounds None => remove
    assert client.get("/retention/policies").json() == []


def test_analyzer_run_flagged_when_projections_expired(client):
    _trace(client, "t1", time.time())
    a = client.post("/trace-analyzers", json={"name": "x", "spec": {
        "source": "trace_projections",
        "select": [{"name": "t", "path": "$.trace_id"}]}}).json()
    client.put(f"/trace-analyzers/{a['id']}/review", json={"review_status": "approved"})
    run = client.post(f"/trace-analyzers/{a['id']}/runs").json()
    assert run["data_expired"] is False  # no policy yet

    # Simulate an old run (predates any retention window).
    from app.db import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE trace_analysis_runs SET started_at = ? WHERE id = ?",
            (time.time() - 100 * 86400, run["id"]),
        )

    client.put("/retention/policies", json={"policies": [
        {"target_table": "trace_projections", "max_age_days": 30}]})
    # An old run predating the 30-day cutoff -> flagged as possibly pruned.
    refreshed = client.get(f"/trace-analyzers/{a['id']}/runs/{run['id']}").json()
    assert refreshed["data_expired"] is True
    assert "retention" in (refreshed["data_expired_note"] or "").lower()


# --- isolation --------------------------------------------------------------

@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-retention-iso.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_retention_is_system_scoped(admin_client):
    token = admin_client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    ).json()["access_token"]

    def mk(name):
        return admin_client.post("/systems", json={"name": name},
                                 headers={"Authorization": f"Bearer {token}"}).json()["id"]

    a, b = mk("A"), mk("B")

    def h(sid):
        return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(sid)}

    for sid in (a, b):
        for i in range(3):
            admin_client.post("/traces", json={
                "trace_id": f"t{i}", "component_id": "c", "mode": "trace",
                "timestamp": float(i), "correlation_id": "k", "span_id": f"s{i}",
                "projections": [{"projection_name": "p", "phase": "output", "fields": {"v": 1}}],
            }, headers=h(sid))

    # Apply aggressive retention only to system A.
    admin_client.put("/retention/policies", json={"policies": [
        {"target_table": "trace_spans", "max_count": 1},
        {"target_table": "trace_projections", "max_count": 1}]}, headers=h(a))
    admin_client.post("/retention/apply", headers=h(a))

    a_steps = admin_client.get("/trace-lineage/correlations/k", headers=h(a)).json()["steps"]
    b_steps = admin_client.get("/trace-lineage/correlations/k", headers=h(b)).json()["steps"]
    assert len(a_steps) == 1  # pruned
    assert len(b_steps) == 3  # untouched
