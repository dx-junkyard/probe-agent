"""Tests for Issue #299: Probe Cell Fabric Sub 2 -- versioned Cell Binding
and the read-only Probe Cell pilot.

Covers:
1. Binding provenance from an approved Probe Point (snapshot/commit/path/
   symbol/provenance ids stored verbatim from the source row).
2. Binding provenance from a Probe Pattern point.
3. Version sequence: a second binding is version 2, the first becomes
   `superseded`, and version 1's content is never mutated.
4. Rejections: non-approved probe point (409), a probe point belonging to
   another System (404, not leaked), a non-worker (roster) Cell (409), and a
   Cell whose cell_id does not match the provenance component_id (409).
5. Drift evaluation: symbol present + same snapshot -> active; newer ready
   snapshot with symbol still present -> stale; symbol absent ->
   review_required; the binding never re-targets a different path/symbol.
6. Health facts: traces with/without error and duration produce a
   deterministic error_rate/latency/heartbeat; no traces -> those fields are
   None (queue_length stays a real 0 count).
7. Activations: explicit vs aggregation_window trigger_kind, audit list
   ordering, used_llm always False.
8. The state endpoint is read-only (no LLM call, no intelligence_runs row
   created) and returns health + binding + activations together.
9. Cross-System isolation for bindings/activations/state.
10. SDK host-isolation regression is run separately (see the report, not in
    this file) -- packages/python-probe is untouched by this change.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-cell-binding-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _create_card(client, headers, role_key="worker-probe"):
    payload = {
        "role_key": role_key,
        "version": "1.0.0",
        "status": "active",
        "mission": "Run the probed component",
        "scope": ["probe"],
        "out_of_scope": ["payment"],
        "model_alias": "worker-default",
        "tool_policy": {"allowed_tools": ["read"], "network_allowed": False},
        "acceptance_template": ["must be concise"],
        "rubric_ref": None,
        "changelog": "initial",
        "schema_version": "1.0",
    }
    r = client.post("/cell-fabric/role-cards", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _create_worker_cell(client, headers, cell_id, role_key="worker-probe", mission=None):
    payload = {
        "cell_id": cell_id,
        "roster": None,
        "role_card_ref": {"role_key": role_key, "version": "1.0.0"},
        "status": "active",
        "mission": mission,
        "schema_version": "1.0",
    }
    r = client.post("/cell-fabric/cells", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _create_orchestrator_cell(client, headers, cell_id, roster, role_key="worker-probe"):
    payload = {
        "cell_id": cell_id,
        "roster": roster,
        "role_card_ref": {"role_key": role_key, "version": "1.0.0"},
        "status": "active",
        "mission": None,
        "schema_version": "1.0",
    }
    r = client.post("/cell-fabric/cells", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- direct DB fixture builders (mirrors tests/test_publish_jobs.py) --------


def _insert_snapshot(conn, system_id, commit_sha="abc123", status="ready"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO repository_snapshots
               (system_id, repo_path, commit_sha, status, created_at, completed_at)
           VALUES (?, '/tmp/repo', ?, ?, ?, ?)""",
        (system_id, commit_sha, status, now, now),
    )
    return cur.lastrowid


def _insert_intelligence_run(conn, system_id, snapshot_id, run_type="probe_plan"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model, prompt_version,
                schema_version, decision_method, status, is_mock, started_at, completed_at)
           VALUES (?, ?, ?, 'mock', 'mock-model', 'v1', 'v1', 'reasoning_llm',
                   'completed', 1, ?, ?)""",
        (system_id, snapshot_id, run_type, now, now),
    )
    return cur.lastrowid


def _insert_probe_plan(conn, system_id, snapshot_id, run_id, feature_id="feat-1", status="approved"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO probe_plans
               (system_id, snapshot_id, intelligence_run_id, feature_id, objective,
                status, origin, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'objective', ?, 'manual', ?, ?)""",
        (system_id, snapshot_id, run_id, feature_id, status, now, now),
    )
    return cur.lastrowid


def _insert_probe_point(
    conn, system_id, plan_id, component_id, path, symbol,
    status="approved", denylist_hit=None, feature_id="feat-1",
):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO probe_points
               (plan_id, system_id, component_id, feature_id, path, symbol,
                line_start, line_end, reason, recommended_mode, side_effect_risk,
                replayability, denylist_hit, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, 10, 'reason', 'trace', 'low',
                   'replayable', ?, ?, ?, ?)""",
        (plan_id, system_id, component_id, feature_id, path, symbol, denylist_hit, status, now, now),
    )
    return cur.lastrowid


def _insert_code_symbol(conn, snapshot_id, system_id, path, qualified_name, component_id=None):
    conn.execute(
        """INSERT INTO code_symbols
               (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line, component_id)
           VALUES (?, ?, ?, ?, 'function', 1, 10, ?)""",
        (snapshot_id, system_id, path, qualified_name, component_id),
    )


def _insert_pattern(conn, system_id, snapshot_id, commit_sha, feature_id="feat-1"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO probe_patterns
               (system_id, name, feature_id, capability, objective, description,
                status, origin, source_snapshot_id, source_commit_sha, created_at, updated_at)
           VALUES (?, 'pattern-1', ?, '', '', '', 'active', 'manual', ?, ?, ?, ?)""",
        (system_id, feature_id, snapshot_id, commit_sha, now, now),
    )
    return cur.lastrowid


def _insert_pattern_point(conn, system_id, pattern_id, component_id, path, symbol, status="saved"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO probe_pattern_points
               (pattern_id, system_id, component_id, path, symbol, line_start, line_end,
                reason, recommended_mode, side_effect_risk, replayability, signature,
                status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 1, 10, 'reason', 'trace', 'low', 'replayable', 'sig',
                   ?, ?, ?)""",
        (pattern_id, system_id, component_id, path, symbol, status, now, now),
    )
    return cur.lastrowid


def _insert_trace(conn, system_id, component_id, timestamp, error=None, duration_ms=None):
    conn.execute(
        """INSERT INTO traces
               (system_id, trace_id, component_id, mode, input_json, output_text,
                error, duration_ms, timestamp)
           VALUES (?, ?, ?, 'trace', '{}', 'out', ?, ?, ?)""",
        (system_id, str(uuid.uuid4()), component_id, error, duration_ms, timestamp),
    )


def _setup_approved_point(system_id, component_id="comp-a", path="src/a.py", symbol="a.fn", commit_sha="abc123"):
    """Insert snapshot + intelligence_run + probe_plan + approved probe_point.
    Returns (snapshot_id, plan_id, point_id)."""
    with get_conn() as conn:
        snapshot_id = _insert_snapshot(conn, system_id, commit_sha=commit_sha)
        run_id = _insert_intelligence_run(conn, system_id, snapshot_id)
        plan_id = _insert_probe_plan(conn, system_id, snapshot_id, run_id)
        point_id = _insert_probe_point(conn, system_id, plan_id, component_id, path, symbol)
        conn.commit()
    return snapshot_id, plan_id, point_id


# ---------------------------------------------------------------------------
# 1-2: Binding provenance
# ---------------------------------------------------------------------------


class TestBindingProvenance:
    def test_create_from_approved_probe_point(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindProv")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")
        snapshot_id, _plan_id, point_id = _setup_approved_point(system["id"])

        r = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point_id},
            headers=h,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["version"] == 1
        assert body["status"] == "active"
        assert body["snapshot_id"] == snapshot_id
        assert body["commit_sha"] == "abc123"
        assert body["path"] == "src/a.py"
        assert body["qualified_symbol"] == "a.fn"
        assert body["component_id"] == "comp-a"
        assert body["probe_point_id"] == point_id
        assert body["probe_pattern_id"] is None

    def test_create_from_pattern_point(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindPattern")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-b")

        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system["id"], commit_sha="def456")
            pattern_id = _insert_pattern(conn, system["id"], snapshot_id, "def456")
            pattern_point_id = _insert_pattern_point(
                conn, system["id"], pattern_id, "comp-b", "src/b.py", "b.fn",
            )
            conn.commit()

        r = admin_client.post(
            "/cell-fabric/cells/comp-b/bindings",
            json={"probe_pattern_point_id": pattern_point_id},
            headers=h,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["snapshot_id"] == snapshot_id
        assert body["commit_sha"] == "def456"
        assert body["path"] == "src/b.py"
        assert body["qualified_symbol"] == "b.fn"
        assert body["probe_point_id"] is None
        assert body["probe_pattern_id"] == pattern_id


# ---------------------------------------------------------------------------
# 3: Version sequence
# ---------------------------------------------------------------------------


class TestVersionSequence:
    def test_second_binding_supersedes_first(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindVersion")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")
        _snap1, plan_id, point_id = _setup_approved_point(system["id"], commit_sha="commit-1")

        r1 = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point_id},
            headers=h,
        )
        assert r1.status_code == 201, r1.text
        v1 = r1.json()

        # A second, later probe_point for the same component/cell (e.g. the
        # symbol moved within a later snapshot) creates version 2.
        with get_conn() as conn:
            snapshot2_id = _insert_snapshot(conn, system["id"], commit_sha="commit-2")
            run_id = _insert_intelligence_run(conn, system["id"], snapshot2_id)
            plan2_id = _insert_probe_plan(conn, system["id"], snapshot2_id, run_id)
            point2_id = _insert_probe_point(
                conn, system["id"], plan2_id, "comp-a", "src/a.py", "a.fn_moved",
            )
            conn.commit()

        r2 = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point2_id},
            headers=h,
        )
        assert r2.status_code == 201, r2.text
        v2 = r2.json()
        assert v2["version"] == 2
        assert v2["status"] == "active"

        r_list = admin_client.get("/cell-fabric/cells/comp-a/bindings", headers=h)
        assert r_list.status_code == 200
        bindings = r_list.json()["bindings"]
        assert [b["version"] for b in bindings] == [2, 1]

        by_version = {b["version"]: b for b in bindings}
        assert by_version[1]["status"] == "superseded"
        # Version 1's content must never be mutated.
        assert by_version[1]["path"] == v1["path"]
        assert by_version[1]["qualified_symbol"] == v1["qualified_symbol"]
        assert by_version[1]["commit_sha"] == v1["commit_sha"]
        assert by_version[2]["status"] == "active"


# ---------------------------------------------------------------------------
# 4: Rejections
# ---------------------------------------------------------------------------


class TestRejections:
    def test_neither_provenance_given_is_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindNoProv")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        r = admin_client.post("/cell-fabric/cells/comp-a/bindings", json={}, headers=h)
        assert r.status_code == 422, r.text

    def test_both_provenance_given_is_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindBothProv")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")
        _snap, _plan, point_id = _setup_approved_point(system["id"])

        r = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point_id, "probe_pattern_point_id": 999},
            headers=h,
        )
        assert r.status_code == 422, r.text

    def test_non_approved_probe_point_is_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindUnapproved")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system["id"])
            run_id = _insert_intelligence_run(conn, system["id"], snapshot_id)
            plan_id = _insert_probe_plan(conn, system["id"], snapshot_id, run_id)
            point_id = _insert_probe_point(
                conn, system["id"], plan_id, "comp-a", "src/a.py", "a.fn", status="proposed",
            )
            conn.commit()

        r = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point_id},
            headers=h,
        )
        assert r.status_code == 409, r.text

    def test_cross_system_probe_point_is_not_found(self, admin_client):
        token = _login(admin_client)
        system1 = _create_system(admin_client, token, "BindCrossA")
        system2 = _create_system(admin_client, token, "BindCrossB")
        h1 = _headers(token, system1["id"])
        h2 = _headers(token, system2["id"])
        _create_card(admin_client, h1)
        _create_card(admin_client, h2)
        _create_worker_cell(admin_client, h2, "comp-a")

        _snap, _plan, point_id_in_system1 = _setup_approved_point(system1["id"])

        r = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point_id_in_system1},
            headers=h2,
        )
        assert r.status_code == 404, r.text
        assert "BindCrossA" not in r.text
        assert str(system1["id"]) not in r.text

    def test_orchestrator_cell_is_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindOrch")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "child-a")
        _create_orchestrator_cell(admin_client, h, "comp-a", roster=["child-a"])
        _snap, _plan, point_id = _setup_approved_point(system["id"])

        r = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point_id},
            headers=h,
        )
        assert r.status_code == 409, r.text

    def test_cell_id_mismatch_is_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindMismatch")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "not-comp-a")
        _snap, _plan, point_id = _setup_approved_point(system["id"], component_id="comp-a")

        r = admin_client.post(
            "/cell-fabric/cells/not-comp-a/bindings",
            json={"probe_point_id": point_id},
            headers=h,
        )
        assert r.status_code == 409, r.text

    def test_pattern_point_removed_from_production_is_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "BindRemoved")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system["id"])
            pattern_id = _insert_pattern(conn, system["id"], snapshot_id, "abc123")
            pattern_point_id = _insert_pattern_point(
                conn, system["id"], pattern_id, "comp-a", "src/a.py", "a.fn",
                status="removed_from_production",
            )
            conn.commit()

        r = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_pattern_point_id": pattern_point_id},
            headers=h,
        )
        assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# 5: Drift evaluation
# ---------------------------------------------------------------------------


class TestDrift:
    def _bind(self, admin_client, h, cell_id, point_id):
        r = admin_client.post(
            f"/cell-fabric/cells/{cell_id}/bindings",
            json={"probe_point_id": point_id},
            headers=h,
        )
        assert r.status_code == 201, r.text
        return r.json()

    def test_symbol_present_same_snapshot_is_active(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftActive")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")
        snapshot_id, _plan, point_id = _setup_approved_point(system["id"])
        with get_conn() as conn:
            _insert_code_symbol(conn, snapshot_id, system["id"], "src/a.py", "a.fn")
            conn.commit()
        self._bind(admin_client, h, "comp-a", point_id)

        r = admin_client.post("/cell-fabric/cells/comp-a/bindings/refresh-drift", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "active"
        assert body["path"] == "src/a.py"
        assert body["qualified_symbol"] == "a.fn"

    def test_newer_snapshot_symbol_present_is_stale(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftStale")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")
        snapshot_id, _plan, point_id = _setup_approved_point(system["id"], commit_sha="commit-1")
        binding = self._bind(admin_client, h, "comp-a", point_id)

        with get_conn() as conn:
            snapshot2_id = _insert_snapshot(conn, system["id"], commit_sha="commit-2")
            _insert_code_symbol(conn, snapshot2_id, system["id"], "src/a.py", "a.fn")
            conn.commit()

        r = admin_client.post("/cell-fabric/cells/comp-a/bindings/refresh-drift", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "stale"
        assert body["id"] == binding["id"]
        assert body["path"] == binding["path"]
        assert body["qualified_symbol"] == binding["qualified_symbol"]

    def test_symbol_absent_is_review_required(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DriftReview")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")
        snapshot_id, _plan, point_id = _setup_approved_point(system["id"], commit_sha="commit-1")
        binding = self._bind(admin_client, h, "comp-a", point_id)

        with get_conn() as conn:
            snapshot2_id = _insert_snapshot(conn, system["id"], commit_sha="commit-2")
            # No code_symbols row inserted -- symbol is gone.
            conn.commit()

        r = admin_client.post("/cell-fabric/cells/comp-a/bindings/refresh-drift", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "review_required"
        # Never re-binds to a different symbol/path.
        assert body["path"] == binding["path"]
        assert body["qualified_symbol"] == binding["qualified_symbol"]


# ---------------------------------------------------------------------------
# 6: Health facts
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_computed_from_traces(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "HealthOk")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        now = time.time()
        with get_conn() as conn:
            _insert_trace(conn, system["id"], "comp-a", now - 10, error=None, duration_ms=100.0)
            _insert_trace(conn, system["id"], "comp-a", now - 5, error="boom", duration_ms=300.0)
            conn.commit()

        r = admin_client.get("/cell-fabric/cells/comp-a/state", headers=h)
        assert r.status_code == 200, r.text
        health = r.json()["state"]["health"]
        assert health["heartbeat_at"] == pytest.approx(now - 5, abs=1.0)
        # Trace throughput is not pending queue depth.
        assert health["queue_length"] == 0
        assert health["error_rate"] == pytest.approx(0.5)
        assert health["latency_p50_ms"] is not None
        assert health["latency_p95_ms"] is not None

    def test_health_no_traces_is_none(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "HealthEmpty")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        r = admin_client.get("/cell-fabric/cells/comp-a/state", headers=h)
        assert r.status_code == 200, r.text
        health = r.json()["state"]["health"]
        assert health["heartbeat_at"] is None
        assert health["error_rate"] is None
        assert health["latency_p50_ms"] is None
        assert health["latency_p95_ms"] is None
        assert health["queue_length"] == 0


# ---------------------------------------------------------------------------
# 7: Activations
# ---------------------------------------------------------------------------


class TestActivations:
    def test_explicit_activation(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ActExplicit")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        r = admin_client.post("/cell-fabric/cells/comp-a/activations", json={}, headers=h)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["trigger_kind"] == "explicit"
        assert body["used_llm"] is False
        assert body["window_start"] is None
        assert body["window_end"] is None

    def test_aggregation_window_activation(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ActWindow")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        now = time.time()
        r = admin_client.post(
            "/cell-fabric/cells/comp-a/activations",
            json={"window_start": now - 3600, "window_end": now},
            headers=h,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["trigger_kind"] == "aggregation_window"
        assert body["used_llm"] is False

    def test_activations_list_newest_first(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ActList")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        r1 = admin_client.post("/cell-fabric/cells/comp-a/activations", json={}, headers=h)
        r2 = admin_client.post("/cell-fabric/cells/comp-a/activations", json={}, headers=h)
        assert r1.status_code == 201 and r2.status_code == 201

        r_list = admin_client.get("/cell-fabric/cells/comp-a/activations", headers=h)
        assert r_list.status_code == 200
        ids = [a["id"] for a in r_list.json()["activations"]]
        assert ids == sorted(ids, reverse=True)
        assert all(a["used_llm"] is False for a in r_list.json()["activations"])

    def test_partial_window_is_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ActPartial")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        r = admin_client.post(
            "/cell-fabric/cells/comp-a/activations",
            json={"window_start": time.time()},
            headers=h,
        )
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 8: State endpoint (read-only, no LLM)
# ---------------------------------------------------------------------------


class TestStateEndpoint:
    def test_state_returns_health_binding_activations_no_llm(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "StateReadOnly")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")
        _snap, _plan, point_id = _setup_approved_point(system["id"])

        with get_conn() as conn:
            before_runs = conn.execute(
                "SELECT COUNT(*) AS c FROM intelligence_runs WHERE system_id = ?",
                (system["id"],),
            ).fetchone()["c"]

        binding = admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point_id},
            headers=h,
        ).json()
        admin_client.post("/cell-fabric/cells/comp-a/activations", json={}, headers=h)

        r = admin_client.get("/cell-fabric/cells/comp-a/state", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"]["health"] is not None
        assert body["binding"]["id"] == binding["id"]
        assert len(body["recent_activations"]) == 1

        with get_conn() as conn:
            after_runs = conn.execute(
                "SELECT COUNT(*) AS c FROM intelligence_runs WHERE system_id = ?",
                (system["id"],),
            ).fetchone()["c"]
        # Only the intelligence_run inserted directly by the fixture (for the
        # probe plan) exists -- the state endpoint itself never calls an LLM
        # or creates an intelligence_runs row.
        assert after_runs == before_runs

    def test_state_no_binding_is_null(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "StateNoBinding")
        h = _headers(token, system["id"])
        _create_card(admin_client, h)
        _create_worker_cell(admin_client, h, "comp-a")

        r = admin_client.get("/cell-fabric/cells/comp-a/state", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["binding"] is None
        assert r.json()["recent_activations"] == []


# ---------------------------------------------------------------------------
# 9: Cross-System isolation
# ---------------------------------------------------------------------------


class TestCrossSystemIsolation:
    def test_bindings_activations_state_are_system_scoped(self, admin_client):
        token = _login(admin_client)
        system1 = _create_system(admin_client, token, "IsoA")
        system2 = _create_system(admin_client, token, "IsoB")
        h1 = _headers(token, system1["id"])
        h2 = _headers(token, system2["id"])
        _create_card(admin_client, h1)
        _create_worker_cell(admin_client, h1, "comp-a")
        _snap, _plan, point_id = _setup_approved_point(system1["id"])
        admin_client.post(
            "/cell-fabric/cells/comp-a/bindings",
            json={"probe_point_id": point_id},
            headers=h1,
        )
        admin_client.post("/cell-fabric/cells/comp-a/activations", json={}, headers=h1)

        # system2 has no Cell "comp-a" at all.
        for path in (
            "/cell-fabric/cells/comp-a/bindings",
            "/cell-fabric/cells/comp-a/activations",
            "/cell-fabric/cells/comp-a/state",
        ):
            r = admin_client.get(path, headers=h2)
            assert r.status_code == 404, (path, r.text)

        # system1 sees its own data.
        r = admin_client.get("/cell-fabric/cells/comp-a/bindings", headers=h1)
        assert r.status_code == 200
        assert len(r.json()["bindings"]) == 1
        r = admin_client.get("/cell-fabric/cells/comp-a/activations", headers=h1)
        assert r.status_code == 200
        assert len(r.json()["activations"]) == 1
