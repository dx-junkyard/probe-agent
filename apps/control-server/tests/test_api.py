import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-test.db"))
    from app.main import app  # noqa: WPS433
    with TestClient(app) as c:
        yield c


def _trace(component_id="summarizer", trace_id="t1"):
    return {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": ["'hi'"], "kwargs": {}},
        "output": "'HI'",
        "error": None,
        "duration_ms": 1.23,
        "timestamp": time.time(),
    }


def test_post_trace_and_list(client):
    r = client.post("/traces", json=_trace())
    assert r.status_code == 201

    r = client.get("/components/summarizer/traces")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["trace_id"] == "t1"
    assert rows[0]["input"] == {"args": ["'hi'"], "kwargs": {}}


def test_components_lists_aggregates(client):
    client.post("/traces", json=_trace(trace_id="a"))
    client.post("/traces", json=_trace(trace_id="b"))

    r = client.get("/components")
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["component_id"] == "summarizer"
    assert rows[0]["trace_count"] == 2
    assert rows[0]["mode"] == "trace"


def test_policy_default_and_update(client):
    r = client.get("/components/new-one/policy")
    assert r.json()["mode"] == "trace"

    r = client.put("/components/new-one/policy", json={"mode": "shadow"})
    assert r.status_code == 200
    assert r.json()["mode"] == "shadow"

    r = client.get("/components/new-one/policy")
    assert r.json()["mode"] == "shadow"


def test_shadow_lifecycle(client):
    payload = {
        "trace_id": "tx",
        "component_id": "summarizer",
        "current_output": "'A'",
        "candidate_output": "'B'",
        "candidate_error": None,
        "candidate_duration_ms": 0.5,
        "timestamp": time.time(),
    }
    r = client.post("/components/summarizer/shadow-results", json=payload)
    assert r.status_code == 201
    rid = r.json()["id"]

    r = client.get("/components/summarizer/shadow-results")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["candidate_output"] == "'B'"
    assert rows[0]["evaluation"] is None

    r = client.put(f"/shadow-results/{rid}/evaluation", json={"evaluation": "better"})
    assert r.status_code == 200

    r = client.get("/components/summarizer/shadow-results")
    assert r.json()[0]["evaluation"] == "better"


def test_policy_invalid_mode_rejected(client):
    r = client.put("/components/x/policy", json={"mode": "yolo"})
    assert r.status_code == 422
