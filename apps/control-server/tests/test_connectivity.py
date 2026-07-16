"""Connectivity status endpoint (Issue #165).

The endpoint reports deterministic signal-reception facts only: finite state
classification (no_signal / smoke_only / receiving), exact-match smoke-trace
detection, and system isolation.
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-conn.db"))
    from app.main import app  # noqa: WPS433
    with TestClient(app) as c:
        yield c


def _trace(component_id, trace_id):
    return {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": [], "kwargs": {}},
        "output": "'ok'",
        "error": None,
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }


def test_no_signal_when_no_traces(client):
    r = client.get("/connectivity/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "no_signal"
    assert body["total_trace_count"] == 0
    assert body["smoke_trace_count"] == 0
    assert body["real_trace_count"] == 0
    assert body["first_trace_at"] is None
    assert body["last_trace_at"] is None
    assert body["last_trace_component_id"] is None
    assert body["smoke_component_id"] == "probe-smoke-check"
    assert body["materialized_session_ids"] == []


def test_smoke_only_when_only_smoke_component(client):
    client.post("/traces", json=_trace("probe-smoke-check", "s1"))
    body = client.get("/connectivity/status").json()
    assert body["state"] == "smoke_only"
    assert body["total_trace_count"] == 1
    assert body["smoke_trace_count"] == 1
    assert body["real_trace_count"] == 0
    assert body["last_trace_component_id"] == "probe-smoke-check"


def test_receiving_when_real_trace_arrives(client):
    client.post("/traces", json=_trace("probe-smoke-check", "s1"))
    client.post("/traces", json=_trace("summarizer", "t1"))
    body = client.get("/connectivity/status").json()
    assert body["state"] == "receiving"
    assert body["total_trace_count"] == 2
    assert body["smoke_trace_count"] == 1
    assert body["real_trace_count"] == 1
    assert body["first_trace_at"] is not None
    assert body["last_trace_at"] is not None


def test_smoke_detection_is_exact_match_only(client):
    # A component merely containing the smoke id is a real trace — the
    # classification is exact match, never substring heuristics.
    client.post("/traces", json=_trace("probe-smoke-check-extra", "t1"))
    body = client.get("/connectivity/status").json()
    assert body["state"] == "receiving"
    assert body["smoke_trace_count"] == 0
    assert body["real_trace_count"] == 1


def test_materialized_session_ids_reflect_non_empty_materialization_diff(client):
    # Issue #236 regression: get_connectivity_facts (app/state_facts.py) took
    # over this query from routes/connectivity.py verbatim -- pin that a real
    # materialized session round-trips through the response.
    from app.db import get_conn

    client.post("/traces", json=_trace("summarizer", "t1"))
    with get_conn() as conn:
        system_id = conn.execute(
            "SELECT system_id FROM traces WHERE trace_id = 't1'"
        ).fetchone()["system_id"]
        now = time.time()
        snap = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, file_count,
                    total_size, indexed_size, created_at, completed_at)
               VALUES (?, '', 'sha', 'ready', 0, 0, 0, ?, ?)""",
            (system_id, now, now),
        )
        conn.execute(
            """INSERT INTO interview_session
                   (system_id, snapshot_id, title, focus, status, stage,
                    materialization_diff, created_at, updated_at)
               VALUES (?, ?, '', '', 'open', 'understanding_initialized', 'diff --git a b', ?, ?)""",
            (system_id, snap.lastrowid, now, now),
        )
        empty_diff_session = conn.execute(
            """INSERT INTO interview_session
                   (system_id, snapshot_id, title, focus, status, stage,
                    materialization_diff, created_at, updated_at)
               VALUES (?, ?, '', '', 'open', 'understanding_initialized', '', ?, ?)""",
            (system_id, snap.lastrowid, now, now),
        )

    body = client.get("/connectivity/status").json()
    assert len(body["materialized_session_ids"]) == 1
    assert empty_diff_session.lastrowid not in body["materialized_session_ids"]


# --- System isolation -------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-conn-iso.db"))
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
    r = client.post(
        "/systems", json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_connectivity_is_system_scoped(admin_client):
    token = _login(admin_client)
    sys_a = _mk_system(admin_client, token, "A")
    sys_b = _mk_system(admin_client, token, "B")

    def h(sid):
        return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(sid)}

    admin_client.post("/traces", json=_trace("worker", "ta"), headers=h(sys_a))

    a = admin_client.get("/connectivity/status", headers=h(sys_a)).json()
    b = admin_client.get("/connectivity/status", headers=h(sys_b)).json()
    assert a["state"] == "receiving"
    assert a["system_id"] == sys_a
    # System B has received nothing; A's traces must not leak into it.
    assert b["state"] == "no_signal"
    assert b["total_trace_count"] == 0
    assert b["system_id"] == sys_b
