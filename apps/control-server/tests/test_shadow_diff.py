"""Tests for Issue #150 (Phase 5): shadow projections + subset diff compare."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-shadowdiff.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _trace(client, trace_id, component="svc", entity_id="o-1"):
    client.post("/traces", json={
        "trace_id": trace_id, "component_id": component, "mode": "shadow",
        "duration_ms": 1.0, "timestamp": time.time(),
        "entities": [{"type": "order", "id": entity_id, "role": "source"}],
    })


def _shadow(client, trace_id, current, candidate, component="svc", candidate_error=None):
    projections = [
        {"projection_name": "orders", "phase": "shadow_current", "fields": current},
    ]
    if candidate_error is None:
        projections.append(
            {"projection_name": "orders", "phase": "shadow_candidate", "fields": candidate})
    client.post(f"/components/{component}/shadow-results", json={
        "trace_id": trace_id, "component_id": component,
        "current_output": str(current), "candidate_output": str(candidate),
        "candidate_error": candidate_error, "candidate_duration_ms": 1.0,
        "timestamp": time.time(), "projections": projections,
    })


def test_shadow_projections_stored(client):
    _trace(client, "t1")
    _shadow(client, "t1", {"status": "pending"}, {"status": "charged"})
    rows = client.get("/traces/t1/projections").json()
    phases = {r["phase"] for r in rows}
    assert phases == {"shadow_current", "shadow_candidate"}


def _analyzer(client, compare, entity=None):
    spec = {
        "source": "trace_projections",
        "select": [{"name": "t", "path": "$.trace_id"}],
        "compare": compare,
    }
    if entity:
        spec["filter"] = {"entity": entity}
    a = client.post("/trace-analyzers", json={"name": "diff", "spec": spec}).json()
    client.put(f"/trace-analyzers/{a['id']}/review", json={"review_status": "approved"})
    return client.post(f"/trace-analyzers/{a['id']}/runs").json()


def test_compare_counts_field_diffs(client):
    _trace(client, "t1", entity_id="o-1")
    _trace(client, "t2", entity_id="o-2")
    _shadow(client, "t1", {"status": "pending"}, {"status": "charged"})   # diff
    _shadow(client, "t2", {"status": "done"}, {"status": "done"})         # same

    run = _analyzer(client, {
        "phases": ["shadow_current", "shadow_candidate"],
        "fields": ["status"], "entity_type": "order",
    })
    assert run["status"] == "completed"
    cmp = run["result"]["compare"]
    assert cmp["diff_fields"]["status"] == 1
    assert cmp["entity_count"] == 2
    assert cmp["diff_entity_count"] == 1
    assert cmp["components_with_diff"] == ["svc"]
    assert cmp["examples"]["status::svc"] == ["t1"]


def test_missing_key_vs_null_are_different(client):
    _trace(client, "t1")
    # current has status=null, candidate omits status entirely -> presence differs
    _shadow(client, "t1", {"status": None}, {})
    run = _analyzer(client, {"phases": ["shadow_current", "shadow_candidate"], "fields": ["status"]})
    assert run["result"]["compare"]["diff_fields"]["status"] == 1


def test_candidate_error_counted_separately(client):
    _trace(client, "t1")
    _shadow(client, "t1", {"status": "pending"}, None, candidate_error="RuntimeError: boom")
    run = _analyzer(client, {"phases": ["shadow_current", "shadow_candidate"], "fields": ["status"]})
    cmp = run["result"]["compare"]
    assert cmp["candidate_error_count"] == 1
    assert cmp["diff_fields"]["status"] == 0  # error is not a field diff


def test_compare_respects_entity_filter(client):
    _trace(client, "t1", entity_id="o-1")
    _trace(client, "t2", entity_id="o-2")
    _shadow(client, "t1", {"status": "a"}, {"status": "b"})   # o-1 diff
    _shadow(client, "t2", {"status": "a"}, {"status": "b"})   # o-2 diff

    run = _analyzer(client, {
        "phases": ["shadow_current", "shadow_candidate"], "fields": ["status"],
    }, entity={"type": "order", "id": "o-1"})
    cmp = run["result"]["compare"]
    assert cmp["diff_fields"]["status"] == 1  # only o-1 in scope
    assert cmp["examples"]["status::svc"] == ["t1"]


# --- workspace pin ----------------------------------------------------------

@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-shadowdiff-ws.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_analyzer_run_can_be_pinned_to_workspace(admin_client):
    token = admin_client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    ).json()["access_token"]
    sid = admin_client.post(
        "/systems", json={"name": "A"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["id"]

    def h():
        return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(sid)}

    a = admin_client.post("/trace-analyzers", json={"name": "d", "spec": {
        "source": "trace_projections", "select": [{"name": "t", "path": "$.trace_id"}]}},
        headers=h()).json()
    admin_client.put(f"/trace-analyzers/{a['id']}/review",
                     json={"review_status": "approved"}, headers=h())
    run = admin_client.post(f"/trace-analyzers/{a['id']}/runs", headers=h()).json()

    ws = admin_client.post("/workspaces", json={"title": "T"}, headers=h()).json()
    r = admin_client.post(
        f"/workspaces/{ws['id']}/context",
        json={"item_type": "analyzer_run", "item_id": str(run["id"]), "label": "run"},
        headers=h(),
    )
    assert r.status_code == 201, r.text
    assert r.json()["item_type"] == "analyzer_run"
