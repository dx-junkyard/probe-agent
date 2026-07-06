"""Tests for Issue #149 (Phase 4b): LLM-assisted analyzer proposal."""

import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "probe-propose.db")
    monkeypatch.setenv("PROBE_DB_PATH", db_path)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    from app.main import app
    with TestClient(app) as c:
        yield c, db_path


def _seed(client):
    client.post("/traces", json={
        "trace_id": "t1", "component_id": "validate", "mode": "trace",
        "duration_ms": 1.0, "timestamp": time.time(),
        "entities": [{"type": "order", "id": "o-1", "role": "source"}],
        "projections": [{"projection_name": "orders", "phase": "output",
                         "fields": {"status": "pending"}}],
    })


def _runs(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM intelligence_runs WHERE run_type = 'analyzer_proposal' ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- mock smoke path --------------------------------------------------------

def test_mock_proposal_is_marked_mock(env):
    client, db_path = env
    _seed(client)
    r = client.post("/trace-analyzers/propose", json={"intent": "show order status flow"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_mock"] is True
    assert body["decision_method"] == "reasoning_llm"
    assert body["review_status"] == "proposed"
    # audit persisted (success) with is_mock + reasoning_llm decision method
    runs = _runs(db_path)
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["is_mock"] == 1
    assert runs[0]["decision_method"] == "reasoning_llm"
    assert runs[0]["snapshot_id"] is None


def test_proposed_analyzer_cannot_run_until_approved(env):
    client, _ = env
    _seed(client)
    aid = client.post("/trace-analyzers/propose", json={"intent": "x"}).json()["id"]
    assert client.post(f"/trace-analyzers/{aid}/runs").status_code == 409
    client.put(f"/trace-analyzers/{aid}/review", json={"review_status": "approved"})
    assert client.post(f"/trace-analyzers/{aid}/runs").status_code == 201


# --- fail-closed real-provider paths ---------------------------------------

class _FakeClient:
    def __init__(self, text):
        self._text = text

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return self._text


def _use_real_provider(monkeypatch):
    monkeypatch.setenv("INTELLIGENCE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "o1")


def test_invalid_json_fails_closed(env, monkeypatch):
    client, db_path = env
    _seed(client)
    _use_real_provider(monkeypatch)
    import app.routes.trace_analyzers as mod
    monkeypatch.setattr(mod, "create_llm_client", lambda cfg: _FakeClient("not json"))

    r = client.post("/trace-analyzers/propose", json={"intent": "x"})
    assert r.status_code == 422
    runs = _runs(db_path)
    assert runs[-1]["status"] == "failed"
    assert runs[-1]["is_mock"] == 0
    # No analyzer saved on failure.
    assert client.get("/trace-analyzers").json() == []


def test_schema_validation_fails_closed(env, monkeypatch):
    client, db_path = env
    _seed(client)
    _use_real_provider(monkeypatch)
    import app.routes.trace_analyzers as mod
    # Valid JSON but bad spec (empty select).
    monkeypatch.setattr(mod, "create_llm_client",
                        lambda cfg: _FakeClient(json.dumps({"source": "trace_projections", "select": []})))

    r = client.post("/trace-analyzers/propose", json={"intent": "x"})
    assert r.status_code == 422
    assert _runs(db_path)[-1]["status"] == "failed"


def test_unknown_identifier_rejected(env, monkeypatch):
    client, db_path = env
    _seed(client)
    _use_real_provider(monkeypatch)
    import app.routes.trace_analyzers as mod
    spec = {"source": "trace_projections",
            "filter": {"components": ["does-not-exist"]},
            "select": [{"name": "t", "path": "$.trace_id"}]}
    monkeypatch.setattr(mod, "create_llm_client", lambda cfg: _FakeClient(json.dumps(spec)))

    r = client.post("/trace-analyzers/propose", json={"intent": "x"})
    assert r.status_code == 422
    assert "unknown component" in r.json()["detail"]
    assert _runs(db_path)[-1]["status"] == "failed"


def test_unknown_field_rejected(env, monkeypatch):
    client, _ = env
    _seed(client)
    _use_real_provider(monkeypatch)
    import app.routes.trace_analyzers as mod
    spec = {"source": "trace_projections",
            "select": [{"name": "s", "path": "$.fields.nonexistent"}]}
    monkeypatch.setattr(mod, "create_llm_client", lambda cfg: _FakeClient(json.dumps(spec)))

    r = client.post("/trace-analyzers/propose", json={"intent": "x"})
    assert r.status_code == 422
    assert "unknown projection field" in r.json()["detail"]


def test_valid_real_proposal_saved(env, monkeypatch):
    client, db_path = env
    _seed(client)
    _use_real_provider(monkeypatch)
    import app.routes.trace_analyzers as mod
    spec = {"source": "trace_projections",
            "filter": {"components": ["validate"], "entity": {"type": "order", "id": "o-1"}},
            "select": [{"name": "status", "path": "$.fields.status"}]}
    monkeypatch.setattr(mod, "create_llm_client", lambda cfg: _FakeClient(json.dumps(spec)))

    r = client.post("/trace-analyzers/propose", json={"intent": "order status", "name": "flow"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_mock"] is False
    assert body["decision_method"] == "reasoning_llm"
    assert body["provider"] == "openai"
    assert body["review_status"] == "proposed"
    assert _runs(db_path)[-1]["status"] == "completed"
