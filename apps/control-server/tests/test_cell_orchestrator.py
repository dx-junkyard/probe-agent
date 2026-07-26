"""Tests for Issue #301: Probe Cell Fabric Sub 4 -- 領域オーケストレーター
(domain orchestrators).

Covers:
1. Guardrails at Cell creation (`POST /cell-fabric/cells`): roster size > 7,
   a missing roster member, a Cell rostering itself, and orchestrator nesting
   depth > 3 are all rejected 422.
2. The roster update endpoint (`PUT /cell-fabric/cells/{cell_id}/roster`)
   applies the same guardrails (including cycle rejection) and writes an
   append-only `cell_roster_events` audit row on success.
3. The deterministic digest (`GET /cell-fabric/orchestrators/{cell_id}/digest`):
   roster/task/escalation counts, and each of the four bottleneck-candidate
   rules (queue_depth, stuck_task, blocked_chain, retry_churn) with verbatim
   supporting facts.
4. Multi-context: the same worker Cell referenced from two different
   orchestrators' rosters contributes its (single-owner) tasks to both
   digests without any ownership duplication.
5. Reasoning triage (`POST /cell-fabric/orchestrators/{cell_id}/triage`) with
   `LLM_PROVIDER=mock`: success persists `cell_triage_results` +
   `intelligence_runs` completed, `is_mock=True` in the response.
6. Triage fail-closed: an LLM error, invalid JSON, and an out-of-roster
   `affected_cell_ids` entry all persist `intelligence_runs` failed with NO
   `cell_triage_results` row, while the deterministic digest is still
   returned (`triage=None`, `triage_error` set) -- no heuristic fallback.
7. System isolation across orchestrators/digests/triage.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from app.llm import LLMError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-cell-orchestrator-test.db"))
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


def _card_payload(role_key="worker-generic", **overrides):
    payload = {
        "role_key": role_key,
        "version": "1.0.0",
        "status": "active",
        "mission": "Do the thing",
        "scope": ["generic"],
        "out_of_scope": [],
        "model_alias": "worker-default",
        "tool_policy": {"allowed_tools": ["read"], "network_allowed": False},
        "acceptance_template": ["must be correct"],
        "rubric_ref": None,
        "changelog": "initial",
        "schema_version": "1.0",
    }
    payload.update(overrides)
    return payload


def _create_role_card(client, headers, role_key="worker-generic"):
    r = client.post("/cell-fabric/role-cards", json=_card_payload(role_key=role_key), headers=headers)
    assert r.status_code == 201, r.text
    return role_key


def _create_cell(client, headers, cell_id, role_key="worker-generic", roster=None, status="active"):
    r = client.post(
        "/cell-fabric/cells",
        json={
            "cell_id": cell_id,
            "roster": roster,
            "role_card_ref": {"role_key": role_key, "version": "1.0.0"},
            "status": status,
            "mission": None,
            "schema_version": "1.0",
        },
        headers=headers,
    )
    return r


def _create_goal(client, headers, title="Goal 1", **overrides):
    payload = {"title": title, "description": "", "parent_goal_id": None, "owner_cell_id": None}
    payload.update(overrides)
    r = client.post("/cell-fabric/goals", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _cell_row_id(system_id, cell_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM cell_definitions WHERE system_id = ? AND cell_id = ?",
            (system_id, cell_id),
        ).fetchone()
    return row["id"]


def _insert_task(system_id, goal_id, owner_cell_id, *, status="todo",
                  retry_count=0, updated_at=None, blocked_by=None):
    owner_row_id = _cell_row_id(system_id, owner_cell_id)
    now = time.time()
    updated_at = now if updated_at is None else updated_at
    with get_conn() as conn:
        conn.execute("BEGIN")
        cur = conn.execute(
            """INSERT INTO cell_tasks
                   (system_id, goal_id, owner_cell_id, delegated_by_cell_id, title,
                    acceptance_json, context_refs_json, budget_json, deadline,
                    priority, status, retry_count, retry_limit, blocked_by_json,
                    acceptance_met, evidence_json, returned_to_parent,
                    idempotency_key, created_at, updated_at)
               VALUES (?, ?, ?, NULL, 'Task', '["done"]', '[]', NULL, NULL,
                       'normal', ?, ?, 3, ?, 0, '[]', 0, NULL, ?, ?)""",
            (
                system_id, goal_id, owner_row_id, status, retry_count,
                json.dumps(blocked_by or []), now, updated_at,
            ),
        )
        conn.execute("COMMIT")
        return cur.lastrowid


def _post_trace(client, headers, trace_id, component_id, timestamp=None):
    r = client.post(
        "/traces",
        json={
            "trace_id": trace_id,
            "component_id": component_id,
            "mode": "trace",
            "input": {"args": [], "kwargs": {}},
            "output": "'ok'",
            "error": None,
            "duration_ms": 1.0,
            "timestamp": timestamp if timestamp is not None else time.time(),
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text


def _submit_escalation(client, headers, cell_definition_id, severity="sev2"):
    r = client.post(
        "/cell-fabric/reports",
        json={
            "cell_definition_id": cell_definition_id,
            "task_id": None,
            "kind": "escalation",
            "severity": severity,
            "fact": [{"text": "something is wrong", "evidence_refs": []}],
            "interpretation": [],
            "ask": [],
            "idempotency_key": None,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _intelligence_runs(run_type="cell_triage"):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM intelligence_runs WHERE run_type = ? ORDER BY id",
            (run_type,),
        ).fetchall()
    return [dict(r) for r in rows]


def _triage_rows(system_id, cell_definition_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM cell_triage_results WHERE system_id = ? AND cell_definition_id = ?",
            (system_id, cell_definition_id),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 1: Creation-time guardrails
# ---------------------------------------------------------------------------


class TestCreationGuardrails:
    def test_roster_too_large_rejected(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-roster-size")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        worker_ids = []
        for i in range(8):
            cid = f"worker-{i}"
            r = _create_cell(client, headers, cid)
            assert r.status_code == 201, r.text
            worker_ids.append(cid)

        r = _create_cell(client, headers, "orch-too-big", roster=worker_ids)
        assert r.status_code == 422, r.text
        assert "span-of-control" in r.json()["detail"] or "7" in r.json()["detail"]

    def test_missing_roster_member_rejected(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-missing-member")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)

        r = _create_cell(client, headers, "orch-missing", roster=["does-not-exist"])
        assert r.status_code == 422, r.text
        assert "does-not-exist" in r.json()["detail"]

    def test_self_roster_rejected(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-self-roster")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)

        r = _create_cell(client, headers, "orch-self", roster=["orch-self"])
        assert r.status_code == 422, r.text
        assert "cannot roster itself" in r.json()["detail"]

    def test_depth_exceeds_limit_rejected(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-depth")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)

        assert _create_cell(client, headers, "o4", roster=[]).status_code == 201
        assert _create_cell(client, headers, "o3", roster=["o4"]).status_code == 201
        assert _create_cell(client, headers, "o2", roster=["o3"]).status_code == 201
        # o2's own depth is 3 (o2 -> o3 -> o4); rostering o2 under a new o1
        # would make depth 4, exceeding MAX_ORCHESTRATOR_DEPTH=3.
        r = _create_cell(client, headers, "o1", roster=["o2"])
        assert r.status_code == 422, r.text
        assert "depth" in r.json()["detail"]

    def test_valid_orchestrator_created(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-valid-orch")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "w1").status_code == 201
        assert _create_cell(client, headers, "w2").status_code == 201
        r = _create_cell(client, headers, "orch-1", roster=["w1", "w2"])
        assert r.status_code == 201, r.text
        assert r.json()["roster"] == ["w1", "w2"]


# ---------------------------------------------------------------------------
# 2: Roster update endpoint
# ---------------------------------------------------------------------------


class TestRosterUpdate:
    def test_update_applies_guardrails_and_writes_audit_row(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-roster-update")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "w1").status_code == 201
        assert _create_cell(client, headers, "w2").status_code == 201
        assert _create_cell(client, headers, "orch", roster=["w1"]).status_code == 201

        r = client.put(
            "/cell-fabric/cells/orch/roster",
            json={"roster": ["w1", "w2"], "changed_by": "tester"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["old_roster"] == ["w1"]
        assert body["new_roster"] == ["w1", "w2"]
        assert body["changed_by"] == "tester"

        digest = client.get("/cell-fabric/orchestrators/orch/digest", headers=headers)
        assert digest.status_code == 200, digest.text
        assert sorted(c["cell_id"] for c in digest.json()["digest"]["roster"]) == ["w1", "w2"]

    def test_update_rejects_oversized_roster(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-roster-update-size")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        worker_ids = []
        for i in range(8):
            cid = f"worker-{i}"
            assert _create_cell(client, headers, cid).status_code == 201
            worker_ids.append(cid)
        assert _create_cell(client, headers, "orch", roster=[]).status_code == 201

        r = client.put(
            "/cell-fabric/cells/orch/roster",
            json={"roster": worker_ids},
            headers=headers,
        )
        assert r.status_code == 422, r.text

    def test_update_rejects_missing_member(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-roster-update-missing")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "orch", roster=[]).status_code == 201

        r = client.put(
            "/cell-fabric/cells/orch/roster",
            json={"roster": ["ghost"]},
            headers=headers,
        )
        assert r.status_code == 422, r.text

    def test_update_rejects_self_roster(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-roster-update-self")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "orch", roster=[]).status_code == 201

        r = client.put(
            "/cell-fabric/cells/orch/roster",
            json={"roster": ["orch"]},
            headers=headers,
        )
        assert r.status_code == 422, r.text

    def test_update_rejects_cycle(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-roster-cycle")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        # B is an (empty) orchestrator; A rosters B.
        assert _create_cell(client, headers, "b", roster=[]).status_code == 201
        assert _create_cell(client, headers, "a", roster=["b"]).status_code == 201

        # Setting B's roster to [A] would create a cycle A -> B -> A.
        r = client.put(
            "/cell-fabric/cells/b/roster",
            json={"roster": ["a"]},
            headers=headers,
        )
        assert r.status_code == 422, r.text
        assert "cycle" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 3: Deterministic digest
# ---------------------------------------------------------------------------


class TestDigest:
    def _setup_roster(self, client, headers, orch_id="orch-1"):
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "worker-a").status_code == 201
        assert _create_cell(client, headers, "worker-b").status_code == 201
        r = _create_cell(client, headers, orch_id, roster=["worker-a", "worker-b"])
        assert r.status_code == 201, r.text
        return orch_id

    def test_digest_not_found_for_missing_or_non_orchestrator_cell(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-404")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "just-a-worker").status_code == 201

        assert client.get(
            "/cell-fabric/orchestrators/does-not-exist/digest", headers=headers,
        ).status_code == 404
        assert client.get(
            "/cell-fabric/orchestrators/just-a-worker/digest", headers=headers,
        ).status_code == 404

    def test_digest_roster_task_and_escalation_counts(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-counts")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)

        goal = _create_goal(client, headers)
        _insert_task(system["id"], goal["id"], "worker-a", status="todo")
        _insert_task(system["id"], goal["id"], "worker-a", status="done")
        _insert_task(system["id"], goal["id"], "worker-b", status="doing")

        _submit_escalation(client, headers, "worker-a", severity="sev2")
        _submit_escalation(client, headers, "worker-b", severity="sev1")

        r = client.get(f"/cell-fabric/orchestrators/{orch_id}/digest", headers=headers)
        assert r.status_code == 200, r.text
        digest = r.json()["digest"]

        roster_ids = {entry["cell_id"] for entry in digest["roster"]}
        assert roster_ids == {"worker-a", "worker-b"}
        for entry in digest["roster"]:
            assert entry["role_card"]["role_key"] == "worker-generic"
            assert entry["health"] is not None  # worker cells always get a health block

        by_cell = digest["tasks"]["by_cell"]
        assert by_cell["worker-a"]["todo"] == 1
        assert by_cell["worker-a"]["done"] == 1
        assert by_cell["worker-b"]["doing"] == 1
        total = digest["tasks"]["total"]
        assert total["todo"] == 1
        assert total["done"] == 1
        assert total["doing"] == 1

        assert digest["escalations"]["open_by_severity"]["sev2"] == 1
        assert digest["escalations"]["open_by_severity"]["sev1"] == 1

    def test_bottleneck_queue_depth(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-queue")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)

        goal = _create_goal(client, headers)
        for i in range(10):
            _insert_task(system["id"], goal["id"], "worker-a", status="todo")
        # worker-b stays at queue_length 0.

        r = client.get(f"/cell-fabric/orchestrators/{orch_id}/digest", headers=headers)
        digest = r.json()["digest"]
        candidates = [c for c in digest["bottleneck_candidates"] if c["kind"] == "queue_depth"]
        assert len(candidates) == 1
        assert candidates[0]["cell_id"] == "worker-a"
        assert candidates[0]["facts"]["queue_length"] == 10

    def test_bottleneck_stuck_task(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-stuck")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)

        goal = _create_goal(client, headers)
        old_task_id = _insert_task(
            system["id"], goal["id"], "worker-a", status="doing",
            updated_at=time.time() - 7200,
        )
        _insert_task(system["id"], goal["id"], "worker-b", status="doing")  # fresh, not stuck

        r = client.get(f"/cell-fabric/orchestrators/{orch_id}/digest", headers=headers)
        digest = r.json()["digest"]
        candidates = [c for c in digest["bottleneck_candidates"] if c["kind"] == "stuck_task"]
        assert len(candidates) == 1
        assert candidates[0]["task_id"] == old_task_id
        assert candidates[0]["cell_id"] == "worker-a"
        assert candidates[0]["facts"]["status"] == "doing"
        assert candidates[0]["facts"]["age_seconds"] > 3600

    def test_bottleneck_retry_churn(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-retry")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)

        goal = _create_goal(client, headers)
        churny_id = _insert_task(system["id"], goal["id"], "worker-b", status="todo", retry_count=2)
        _insert_task(system["id"], goal["id"], "worker-a", status="todo", retry_count=1)

        r = client.get(f"/cell-fabric/orchestrators/{orch_id}/digest", headers=headers)
        digest = r.json()["digest"]
        candidates = [c for c in digest["bottleneck_candidates"] if c["kind"] == "retry_churn"]
        assert len(candidates) == 1
        assert candidates[0]["task_id"] == churny_id
        assert candidates[0]["facts"]["retry_count"] == 2

    def test_bottleneck_blocked_chain(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-blocked")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)

        goal = _create_goal(client, headers)
        tail_id = _insert_task(system["id"], goal["id"], "worker-b", status="blocked", blocked_by=[])
        head_id = _insert_task(
            system["id"], goal["id"], "worker-a", status="blocked", blocked_by=[tail_id],
        )

        r = client.get(f"/cell-fabric/orchestrators/{orch_id}/digest", headers=headers)
        digest = r.json()["digest"]

        edge_map = {e["task_id"]: e["blocked_by_task_ids"] for e in digest["blocked_graph"]}
        assert edge_map[head_id] == [tail_id]
        assert edge_map[tail_id] == []

        candidates = [c for c in digest["bottleneck_candidates"] if c["kind"] == "blocked_chain"]
        assert len(candidates) == 1
        assert candidates[0]["facts"]["length"] == 2
        assert set(candidates[0]["facts"]["chain_task_ids"]) == {head_id, tail_id}

    def test_digest_is_deterministic_no_llm_calls(self, admin_client, monkeypatch):
        """Calling the digest endpoint must never touch the LLM layer."""
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-no-llm")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)

        import app.cell_orchestrator as mod

        def _boom(*args, **kwargs):
            raise AssertionError("digest must not call the LLM layer")

        monkeypatch.setattr(mod, "create_llm_client", _boom)
        r = client.get(f"/cell-fabric/orchestrators/{orch_id}/digest", headers=headers)
        assert r.status_code == 200, r.text
        assert _intelligence_runs("cell_triage") == []


# ---------------------------------------------------------------------------
# 4: Multi-context (same worker in two orchestrators' rosters)
# ---------------------------------------------------------------------------


class TestMultiContext:
    def test_worker_in_two_rosters_no_ownership_duplication(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-multi-context")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "shared-worker").status_code == 201
        assert _create_cell(client, headers, "orch-a", roster=["shared-worker"]).status_code == 201
        assert _create_cell(client, headers, "orch-b", roster=["shared-worker"]).status_code == 201

        goal = _create_goal(client, headers)
        _insert_task(system["id"], goal["id"], "shared-worker", status="todo")
        _insert_task(system["id"], goal["id"], "shared-worker", status="done")

        digest_a = client.get(
            "/cell-fabric/orchestrators/orch-a/digest", headers=headers,
        ).json()["digest"]
        digest_b = client.get(
            "/cell-fabric/orchestrators/orch-b/digest", headers=headers,
        ).json()["digest"]

        assert digest_a["tasks"]["by_cell"]["shared-worker"] == {
            "todo": 1, "doing": 0, "review": 0, "done": 1, "failed": 0, "blocked": 0,
        }
        assert digest_b["tasks"]["by_cell"]["shared-worker"] == digest_a["tasks"]["by_cell"]["shared-worker"]
        # Task ownership stays single: exactly one owner row per task, and
        # both digests read that same owner rather than a duplicated copy.
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(DISTINCT owner_cell_id) AS n FROM cell_tasks WHERE system_id = ?",
                (system["id"],),
            ).fetchone()
        assert rows["n"] == 1


# ---------------------------------------------------------------------------
# 5 & 6: Reasoning triage
# ---------------------------------------------------------------------------


class TestTriage:
    def _setup_roster(self, client, headers, orch_id="orch-1"):
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "worker-a").status_code == 201
        assert _create_cell(client, headers, "worker-b").status_code == 201
        r = _create_cell(client, headers, orch_id, roster=["worker-a", "worker-b"])
        assert r.status_code == 201, r.text
        return orch_id

    def test_mock_triage_success_persists_and_marks_mock(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-triage-success")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)

        r = client.post(f"/cell-fabric/orchestrators/{orch_id}/triage", headers=headers)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["is_mock"] is True
        assert body["triage_error"] is None
        assert body["triage"] is not None
        assert body["triage"]["classification"] == "individual"
        assert body["triage"]["is_mock"] is True
        assert body["digest"]["cell_id"] == orch_id

        runs = _intelligence_runs("cell_triage")
        assert len(runs) == 1
        assert runs[0]["status"] == "completed"
        assert runs[0]["is_mock"] == 1
        assert runs[0]["decision_method"] == "reasoning_llm"

        orch_row_id = _cell_row_id(system["id"], orch_id)
        triage_rows = _triage_rows(system["id"], orch_row_id)
        assert len(triage_rows) == 1
        assert triage_rows[0]["classification"] == "individual"

        history = client.get(
            f"/cell-fabric/orchestrators/{orch_id}/triage-results", headers=headers,
        )
        assert history.status_code == 200, history.text
        assert len(history.json()["results"]) == 1
        assert history.json()["results"][0]["is_mock"] is True

    def _force_llm_failure(self, monkeypatch, *, text=None, error=None):
        import app.cell_orchestrator as mod

        class _FakeClient:
            def generate_text(self, messages, *, temperature=None, max_tokens=None):
                if error is not None:
                    raise error
                return text

        monkeypatch.setattr(mod, "create_llm_client", lambda cfg: _FakeClient())

    def test_triage_fails_closed_on_llm_error(self, admin_client, monkeypatch):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-triage-llm-error")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)
        self._force_llm_failure(monkeypatch, error=LLMError("provider is down"))

        r = client.post(f"/cell-fabric/orchestrators/{orch_id}/triage", headers=headers)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["triage"] is None
        assert body["triage_error"] == "provider is down"
        assert body["digest"]["cell_id"] == orch_id  # facts still returned

        runs = _intelligence_runs("cell_triage")
        assert runs[-1]["status"] == "failed"
        assert runs[-1]["error_details"] == "provider is down"

        orch_row_id = _cell_row_id(system["id"], orch_id)
        assert _triage_rows(system["id"], orch_row_id) == []

    def test_triage_fails_closed_on_invalid_json(self, admin_client, monkeypatch):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-triage-bad-json")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)
        self._force_llm_failure(monkeypatch, text="not json at all")

        r = client.post(f"/cell-fabric/orchestrators/{orch_id}/triage", headers=headers)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["triage"] is None
        assert body["triage_error"] is not None

        runs = _intelligence_runs("cell_triage")
        assert runs[-1]["status"] == "failed"
        orch_row_id = _cell_row_id(system["id"], orch_id)
        assert _triage_rows(system["id"], orch_row_id) == []

    def test_triage_fails_closed_on_out_of_roster_affected_cell_ids(self, admin_client, monkeypatch):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-triage-out-of-roster")
        headers = _headers(token, system["id"])
        orch_id = self._setup_roster(client, headers)
        bad_response = json.dumps({
            "classification": "systemic",
            "reasoning_summary": "Looks systemic.",
            "affected_cell_ids": ["not-in-roster"],
            "proposed_ask": "Investigate further.",
        })
        self._force_llm_failure(monkeypatch, text=bad_response)

        r = client.post(f"/cell-fabric/orchestrators/{orch_id}/triage", headers=headers)
        assert r.status_code == 201, r.text
        body = r.json()
        # No heuristic fallback -- the invalid classification is never
        # synthesized into a result.
        assert body["triage"] is None
        assert "not-in-roster" in body["triage_error"]

        runs = _intelligence_runs("cell_triage")
        assert runs[-1]["status"] == "failed"
        orch_row_id = _cell_row_id(system["id"], orch_id)
        assert _triage_rows(system["id"], orch_row_id) == []

    def test_triage_not_found_for_non_orchestrator(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-triage-404")
        headers = _headers(token, system["id"])
        _create_role_card(client, headers)
        assert _create_cell(client, headers, "just-a-worker").status_code == 201

        r = client.post("/cell-fabric/orchestrators/just-a-worker/triage", headers=headers)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 7: System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_orchestrator_digest_and_triage_isolated_per_system(self, admin_client):
        client = admin_client
        token = _login(client)
        system_a = _create_system(client, token, "sys-iso-a")
        system_b = _create_system(client, token, "sys-iso-b")
        headers_a = _headers(token, system_a["id"])
        headers_b = _headers(token, system_b["id"])

        _create_role_card(client, headers_a)
        assert _create_cell(client, headers_a, "worker-a").status_code == 201
        assert _create_cell(client, headers_a, "orch", roster=["worker-a"]).status_code == 201

        # Same cell_id string does not exist in System B.
        assert client.get(
            "/cell-fabric/orchestrators/orch/digest", headers=headers_b,
        ).status_code == 404
        assert client.post(
            "/cell-fabric/orchestrators/orch/triage", headers=headers_b,
        ).status_code == 404
        assert client.get(
            "/cell-fabric/orchestrators/orch/triage-results", headers=headers_b,
        ).status_code == 404

        # System A still works.
        assert client.get(
            "/cell-fabric/orchestrators/orch/digest", headers=headers_a,
        ).status_code == 200
