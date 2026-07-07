"""Tests for the Trace Analyzer builder context endpoint (Issue #157).

The context endpoint is a deterministic, read-only projection of the finite
sets of identifiers a declarative analyzer spec may reference, so the dashboard
builder can offer choices instead of asking for hand-written JSON.
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "probe-ctx.db")
    monkeypatch.setenv("PROBE_DB_PATH", db_path)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from app.main import app
    with TestClient(app) as c:
        yield c


def _seed(client):
    client.post("/traces", json={
        "trace_id": "t1", "component_id": "validate", "mode": "trace",
        "duration_ms": 1.0, "timestamp": time.time(),
        "entities": [{"type": "order", "id": "o-1", "role": "source"}],
        "projections": [{"projection_name": "orders", "phase": "output",
                         "fields": {"status": "pending", "total": 10}}],
    })
    client.post("/traces", json={
        "trace_id": "t2", "component_id": "charge", "mode": "trace",
        "duration_ms": 2.0, "timestamp": time.time(),
        "entities": [{"type": "order", "id": "o-2", "role": "source"}],
        "projections": [{"projection_name": "orders", "phase": "input",
                         "fields": {"status": "paid"}}],
    })


def test_context_reports_candidate_values(env):
    client = env
    _seed(client)
    r = client.get("/trace-analyzers/context")
    assert r.status_code == 200, r.text
    body = r.json()
    # Components come from the components table (registered on trace ingest).
    assert "validate" in body["components"]
    assert "charge" in body["components"]
    assert body["entity_types"] == ["order"]
    assert {"entity_type": "order", "entity_id": "o-1"} in body["entities"]
    assert {"entity_type": "order", "entity_id": "o-2"} in body["entities"]
    assert body["projection_names"] == ["orders"]
    assert set(body["field_names"]) == {"status", "total"}
    assert set(body["phases"]) == {"input", "output"}
    assert body["entities_truncated"] is False


def test_context_empty_when_no_data(env):
    client = env
    r = client.get("/trace-analyzers/context")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["entities"] == []
    assert body["entity_types"] == []
    assert body["field_names"] == []


def test_context_route_does_not_collide_with_analyzer_id(env):
    """'context' must resolve to the context endpoint, not be parsed as an id."""
    client = env
    _seed(client)
    r = client.get("/trace-analyzers/context")
    assert r.status_code == 200
    # An unknown numeric id still 404s through the {analyzer_id} route.
    assert client.get("/trace-analyzers/999999").status_code == 404
