"""Tests for Issue #300: Probe Cell Fabric Sub 3 -- Goal/Task ledger and
delegate/report/escalate protocol.

Covers:
1. Goals: creation, listing, get-with-tasks, status update, cross-system
   parent rejection, and cycle rejection (the deterministic
   `would_create_cycle` checker -- there is no reparent endpoint at Sub 3, so
   the cycle guard is exercised directly, the same way #298 tests its
   contract functions directly).
2. Task delegation (P1): acceptance required, goal/owner must exist in the
   System, idempotent resend returns the existing row.
3. Task transitions: full happy path with resolvable evidence, `done`
   without acceptance/evidence, the retry ledger rule (`failed -> todo`
   capped by `retry_limit`), `blocked` requiring `blocked_by` or `detail`,
   `return_to_parent` only from `failed`/`blocked`, and every transition
   producing a `cell_task_events` row.
4. Owner/report-target shape: a task has exactly one `owner_cell_id`.
5. Evidence ref resolution: all 7 accepted formats (including
   ``improvement:<id>``, resolvable since Issue #304 added
   ``cell_improvements``), cross-system/missing rejection, malformed refs,
   and the still-reserved ``quality_sample`` ref kind.
6. Reports (P2): digest vs escalation severity rules, fail-closed unknown
   fields, fact/interpretation/ask kept separate, idempotent resend.
7. Escalations: auto-created from an escalation report, acknowledge/resolve.
8. System isolation across goals/tasks/reports/escalations.
9. Additive migration: the new tables appear via init_db() without touching
   pre-existing data.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-cell-tasks-test.db"))
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


def _card_payload(**overrides):
    payload = {
        "role_key": "worker-generic",
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


def _create_cell(client, headers, cell_id="cell-1", role_key="worker-generic"):
    """Create an active role card + a worker Cell bound to it, returning
    the Cell's business ``cell_id``."""
    r = client.post(
        "/cell-fabric/role-cards",
        json=_card_payload(role_key=role_key),
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/cell-fabric/cells",
        json={
            "cell_id": cell_id,
            "roster": None,
            "role_card_ref": {"role_key": role_key, "version": "1.0.0"},
            "status": "active",
            "mission": None,
            "schema_version": "1.0",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return cell_id


def _create_goal(client, headers, title="Goal 1", **overrides):
    payload = {"title": title, "description": "", "parent_goal_id": None, "owner_cell_id": None}
    payload.update(overrides)
    r = client.post("/cell-fabric/goals", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _delegate_task(client, headers, goal_id, owner_cell_id, **overrides):
    payload = {
        "goal_id": goal_id,
        "owner_cell_id": owner_cell_id,
        "title": "Do the work",
        "acceptance": ["must be correct"],
        "context_refs": [],
        "budget": None,
        "deadline": None,
        "priority": None,
        "delegated_by_cell_id": None,
        "idempotency_key": None,
    }
    payload.update(overrides)
    return client.post("/cell-fabric/tasks", json=payload, headers=headers)


def _setup_goal_and_owner(client, headers, cell_id="cell-1"):
    _create_cell(client, headers, cell_id=cell_id)
    goal = _create_goal(client, headers)
    return goal, cell_id


def _post_trace(client, headers, trace_id="t1", component_id="comp-1"):
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
            "timestamp": time.time(),
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return trace_id


def _insert_row(system_id, table, columns_values):
    from app.db import get_conn

    columns = list(columns_values.keys())
    placeholders = ", ".join(["?"] * len(columns))
    with get_conn() as conn:
        conn.execute("BEGIN")
        cur = conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [columns_values[c] for c in columns],
        )
        conn.execute("COMMIT")
        return cur.lastrowid


def _insert_evaluation_result(system_id, trace_id="t1", component_id="comp-1"):
    now = time.time()
    return _insert_row(
        system_id,
        "evaluation_results",
        {
            "system_id": system_id,
            "trace_id": trace_id,
            "component_id": component_id,
            "criterion_id": 1,
            "status": "pass",
            "score": 1.0,
            "reason": "ok",
            "actual_output": "ok",
            "expected_value": "ok",
            "created_at": now,
        },
    )


def _insert_shadow_result(system_id, trace_id="t1", component_id="comp-1"):
    now = time.time()
    return _insert_row(
        system_id,
        "shadow_results",
        {
            "system_id": system_id,
            "trace_id": trace_id,
            "component_id": component_id,
            "current_output": "a",
            "candidate_output": "b",
            "candidate_error": None,
            "candidate_duration_ms": 1.0,
            "evaluation": None,
            "timestamp": now,
        },
    )


def _insert_snapshot(system_id, commit_sha="deadbeef"):
    now = time.time()
    return _insert_row(
        system_id,
        "repository_snapshots",
        {
            "system_id": system_id,
            "repo_path": "/repo",
            "commit_sha": commit_sha,
            "status": "ready",
            "created_at": now,
        },
    )


def _insert_snapshot_file(snapshot_id, path="app/foo.py"):
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute("BEGIN")
        cur = conn.execute(
            """INSERT INTO snapshot_files (snapshot_id, path, source_type, size_bytes)
               VALUES (?, ?, 'text', 10)""",
            (snapshot_id, path),
        )
        conn.execute("COMMIT")
        return cur.lastrowid


def _insert_replay_run(system_id, snapshot_id, component_id="comp-1"):
    now = time.time()
    replay_set_id = _insert_row(
        system_id,
        "replay_sets",
        {
            "system_id": system_id,
            "component_id": component_id,
            "name": "set-1",
            "trace_ids_json": "[]",
            "source": "manual",
            "created_at": now,
        },
    )
    return _insert_row(
        system_id,
        "replay_runs",
        {
            "system_id": system_id,
            "replay_set_id": replay_set_id,
            "component_id": component_id,
            "snapshot_id": snapshot_id,
            "commit_sha": "deadbeef",
            "symbol_path": "app/foo.py",
            "symbol_qualified_name": "foo",
            "status": "completed",
            "trace_set_hash": "hash",
            "sandbox_config_json": "{}",
            "created_at": now,
        },
    )


def _get_cell_row_id(system_id, cell_id):
    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM cell_definitions WHERE system_id = ? AND cell_id = ?",
            (system_id, cell_id),
        ).fetchone()
        return row["id"]


def _insert_cell_improvement(system_id, cell_definition_row_id, target_kind="candidate_patch"):
    now = time.time()
    return _insert_row(
        system_id,
        "cell_improvements",
        {
            "system_id": system_id,
            "cell_definition_id": cell_definition_row_id,
            "status": "observed",
            "target_kind": target_kind,
            "hypothesis": "",
            "expected_effect": "",
            "risk": "",
            "rollback_plan": "",
            "observed_facts_json": "[]",
            "canary_evidence_json": "[]",
            "suspended": 0,
            "suspension_reason": "",
            "created_at": now,
            "updated_at": now,
        },
    )


def _insert_experiment(system_id, snapshot_id, feature_id="feat-1"):
    now = time.time()
    return _insert_row(
        system_id,
        "experiments",
        {
            "system_id": system_id,
            "feature_id": feature_id,
            "objective": "improve",
            "snapshot_id": snapshot_id,
            "baseline_commit": "deadbeef",
            "config_revision": "1",
            "execution_config": "{}",
            "status": "draft",
            "created_at": now,
        },
    )


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


class TestGoals:
    def test_create_list_get_root_goal(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GoalSys")
        headers = _headers(token, system["id"])

        goal = _create_goal(admin_client, headers, title="Root Goal")
        assert goal["parent_goal_id"] is None
        assert goal["status"] == "open"

        r = admin_client.get("/cell-fabric/goals", headers=headers)
        assert r.status_code == 200
        assert len(r.json()["goals"]) == 1

        r = admin_client.get(f"/cell-fabric/goals/{goal['id']}", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["goal"]["id"] == goal["id"]
        assert body["tasks"] == []

    def test_nested_goal(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GoalNested")
        headers = _headers(token, system["id"])
        root = _create_goal(admin_client, headers, title="Root")
        child = _create_goal(
            admin_client, headers, title="Child", parent_goal_id=root["id"],
        )
        assert child["parent_goal_id"] == root["id"]

    def test_parent_goal_missing_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GoalMissingParent")
        headers = _headers(token, system["id"])
        r = admin_client.post(
            "/cell-fabric/goals",
            json={"title": "Orphan", "parent_goal_id": 999999},
            headers=headers,
        )
        assert r.status_code == 404

    def test_cross_system_parent_goal_rejected_404(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "GoalXSysA")
        sys_b = _create_system(admin_client, token, "GoalXSysB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])
        goal_a = _create_goal(admin_client, headers_a, title="A-Root")

        r = admin_client.post(
            "/cell-fabric/goals",
            json={"title": "B-Child", "parent_goal_id": goal_a["id"]},
            headers=headers_b,
        )
        assert r.status_code == 404

    def test_get_missing_goal_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GoalGetMissing")
        headers = _headers(token, system["id"])
        r = admin_client.get("/cell-fabric/goals/999999", headers=headers)
        assert r.status_code == 404

    def test_status_update(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GoalStatus")
        headers = _headers(token, system["id"])
        goal = _create_goal(admin_client, headers)

        r = admin_client.post(
            f"/cell-fabric/goals/{goal['id']}/status",
            json={"status": "achieved"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "achieved"

    def test_status_update_unknown_value_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GoalStatusBad")
        headers = _headers(token, system["id"])
        goal = _create_goal(admin_client, headers)
        r = admin_client.post(
            f"/cell-fabric/goals/{goal['id']}/status",
            json={"status": "bogus"},
            headers=headers,
        )
        assert r.status_code == 422

    def test_cycle_rejection(self, admin_client):
        """No reparent endpoint exists at Sub 3, so the deterministic cycle
        checker is exercised directly, mirroring how #298 tests its contract
        functions (validate_task_transition etc.) directly."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "GoalCycle")
        headers = _headers(token, system["id"])
        goal_a = _create_goal(admin_client, headers, title="A")
        goal_b = _create_goal(admin_client, headers, title="B", parent_goal_id=goal_a["id"])
        goal_c = _create_goal(admin_client, headers, title="C", parent_goal_id=goal_b["id"])

        from app.cell_tasks import would_create_cycle
        from app.db import get_conn

        with get_conn() as conn:
            # Making A a child of its own descendant C would create a cycle.
            assert would_create_cycle(
                conn, system["id"], goal_a["id"], goal_c["id"]
            ) is True
            # A goal cannot be its own parent.
            assert would_create_cycle(
                conn, system["id"], goal_a["id"], goal_a["id"]
            ) is True
            # An unrelated re-parent is fine.
            assert would_create_cycle(
                conn, system["id"], goal_c["id"], goal_a["id"]
            ) is False
            # A brand new goal (no id yet) can never create a cycle.
            assert would_create_cycle(conn, system["id"], None, goal_c["id"]) is False


# ---------------------------------------------------------------------------
# Task delegation (P1)
# ---------------------------------------------------------------------------


class TestTaskDelegation:
    def test_delegate_happy_path(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateOk")
        headers = _headers(token, system["id"])
        goal, cell_id = _setup_goal_and_owner(admin_client, headers)

        r = _delegate_task(admin_client, headers, goal["id"], cell_id)
        assert r.status_code == 201, r.text
        task = r.json()
        assert task["status"] == "todo"
        assert task["owner_cell_id"] == cell_id
        assert task["acceptance"] == ["must be correct"]
        assert task["retry_count"] == 0
        assert task["retry_limit"] == 3

    def test_missing_goal_id_field_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateNoGoalField")
        headers = _headers(token, system["id"])
        _create_cell(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/tasks",
            json={"owner_cell_id": "cell-1", "title": "x", "acceptance": ["a"]},
            headers=headers,
        )
        assert r.status_code == 422

    def test_nonexistent_goal_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateBadGoal")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _delegate_task(admin_client, headers, 999999, cell_id)
        assert r.status_code == 404

    def test_nonexistent_owner_cell_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateBadOwner")
        headers = _headers(token, system["id"])
        goal = _create_goal(admin_client, headers)
        r = _delegate_task(admin_client, headers, goal["id"], "no-such-cell")
        assert r.status_code == 404

    def test_empty_acceptance_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateEmptyAcceptance")
        headers = _headers(token, system["id"])
        goal, cell_id = _setup_goal_and_owner(admin_client, headers)
        r = _delegate_task(admin_client, headers, goal["id"], cell_id, acceptance=[])
        assert r.status_code == 422

    def test_missing_acceptance_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateMissingAcceptance")
        headers = _headers(token, system["id"])
        goal, cell_id = _setup_goal_and_owner(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/tasks",
            json={"goal_id": goal["id"], "owner_cell_id": cell_id, "title": "x"},
            headers=headers,
        )
        assert r.status_code == 422

    def test_unresolvable_context_ref_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateBadContextRef")
        headers = _headers(token, system["id"])
        goal, cell_id = _setup_goal_and_owner(admin_client, headers)
        r = _delegate_task(
            admin_client, headers, goal["id"], cell_id,
            context_refs=["trace:no-such-trace"],
        )
        assert r.status_code == 422

    def test_idempotent_resend_returns_same_task_no_duplicate(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateIdempotent")
        headers = _headers(token, system["id"])
        goal, cell_id = _setup_goal_and_owner(admin_client, headers)

        r1 = _delegate_task(
            admin_client, headers, goal["id"], cell_id, idempotency_key="dedupe-1",
        )
        assert r1.status_code == 201
        r2 = _delegate_task(
            admin_client, headers, goal["id"], cell_id, idempotency_key="dedupe-1",
        )
        assert r2.status_code == 201
        assert r1.json()["id"] == r2.json()["id"]

        r = admin_client.get(
            "/cell-fabric/tasks", params={"goal_id": goal["id"]}, headers=headers,
        )
        assert len(r.json()["tasks"]) == 1

    def test_single_owner_and_single_goal_per_task(self, admin_client):
        """Sub 3 models exactly one owner Cell and one parent goal per task
        by construction -- there is no many-to-many membership table."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "DelegateSingleOwner")
        headers = _headers(token, system["id"])
        goal, cell_id = _setup_goal_and_owner(admin_client, headers)
        r = _delegate_task(admin_client, headers, goal["id"], cell_id)
        task = r.json()
        assert isinstance(task["owner_cell_id"], str)
        assert isinstance(task["goal_id"], int)
        assert "owner_cell_ids" not in task
        assert "goal_ids" not in task


# ---------------------------------------------------------------------------
# Task transitions
# ---------------------------------------------------------------------------


class TestTaskTransitions:
    def _delegated_task(self, client, headers, retry_limit=3):
        goal, cell_id = _setup_goal_and_owner(client, headers)
        r = _delegate_task(client, headers, goal["id"], cell_id)
        assert r.status_code == 201
        return r.json()

    def _transition(self, client, headers, task_id, new_status, **overrides):
        payload = {"new_status": new_status}
        payload.update(overrides)
        return client.post(
            f"/cell-fabric/tasks/{task_id}/transition", json=payload, headers=headers,
        )

    def test_happy_path_with_events(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "TransitionHappy")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)
        trace_id = _post_trace(admin_client, headers)

        r = self._transition(admin_client, headers, task["id"], "doing")
        assert r.status_code == 200, r.text
        r = self._transition(admin_client, headers, task["id"], "review")
        assert r.status_code == 200, r.text
        r = self._transition(
            admin_client, headers, task["id"], "done",
            acceptance_met=True, evidence_refs=[f"trace:{trace_id}"],
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "done"
        assert body["acceptance_met"] is True
        assert body["evidence"] == [f"trace:{trace_id}"]

        r = admin_client.get(f"/cell-fabric/tasks/{task['id']}", headers=headers)
        events = r.json()["events"]
        # created + 3 transitions
        assert len(events) == 4
        assert events[0]["event_type"] == "created"
        assert [e["to_status"] for e in events[1:]] == ["doing", "review", "done"]

    def test_done_without_acceptance_met_422_no_write(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "TransitionNoAcceptance")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)
        self._transition(admin_client, headers, task["id"], "doing")
        self._transition(admin_client, headers, task["id"], "review")

        r = self._transition(
            admin_client, headers, task["id"], "done", acceptance_met=False,
        )
        assert r.status_code in (409, 422)

        r = admin_client.get(f"/cell-fabric/tasks/{task['id']}", headers=headers)
        body = r.json()
        assert body["task"]["status"] == "review"
        assert body["task"]["acceptance_met"] is False
        # created + doing + review == 3 events, no 'done' event written
        assert len(body["events"]) == 3
        assert all(e["to_status"] != "done" for e in body["events"])

    def test_done_without_evidence_422_no_write(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "TransitionNoEvidence")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)
        self._transition(admin_client, headers, task["id"], "doing")
        self._transition(admin_client, headers, task["id"], "review")

        r = self._transition(
            admin_client, headers, task["id"], "done", acceptance_met=True,
            evidence_refs=[],
        )
        assert r.status_code in (409, 422)

        r = admin_client.get(f"/cell-fabric/tasks/{task['id']}", headers=headers)
        body = r.json()
        assert body["task"]["status"] == "review"
        assert len(body["events"]) == 3

    def test_retry_loop_then_exhausted_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "TransitionRetry")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)
        task_id = task["id"]
        retry_limit = task["retry_limit"]

        for i in range(retry_limit):
            r = self._transition(admin_client, headers, task_id, "doing")
            assert r.status_code == 200, r.text
            r = self._transition(admin_client, headers, task_id, "failed")
            assert r.status_code == 200, r.text
            r = self._transition(admin_client, headers, task_id, "todo")
            assert r.status_code == 200, r.text
            assert r.json()["retry_count"] == i + 1

        # retry_count now equals retry_limit; one more failed->todo is refused.
        self._transition(admin_client, headers, task_id, "doing")
        r = self._transition(admin_client, headers, task_id, "failed")
        assert r.status_code == 200
        r = self._transition(admin_client, headers, task_id, "todo")
        assert r.status_code == 409

        r = admin_client.get(f"/cell-fabric/tasks/{task_id}", headers=headers)
        retry_events = [e for e in r.json()["events"] if e["event_type"] == "retry"]
        assert len(retry_events) == retry_limit

    def test_blocked_requires_blocked_by_or_detail(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "TransitionBlocked")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)

        r = self._transition(admin_client, headers, task["id"], "blocked")
        assert r.status_code == 422

        r = self._transition(
            admin_client, headers, task["id"], "blocked",
            detail="waiting on upstream API",
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "blocked"

    def test_blocked_with_blocked_by_ids(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "TransitionBlockedBy")
        headers = _headers(token, system["id"])
        goal, cell_id = _setup_goal_and_owner(admin_client, headers)
        blocking = _delegate_task(admin_client, headers, goal["id"], cell_id).json()
        blocked = _delegate_task(
            admin_client, headers, goal["id"], cell_id, title="Blocked task",
        ).json()

        r = self._transition(
            admin_client, headers, blocked["id"], "blocked",
            blocked_by=[blocking["id"]],
        )
        assert r.status_code == 200, r.text
        assert r.json()["blocked_by"] == [blocking["id"]]

        r = admin_client.get(f"/cell-fabric/tasks/{blocked['id']}", headers=headers)
        event_types = [e["event_type"] for e in r.json()["events"]]
        assert "blocked" in event_types

    def test_return_to_parent_from_failed(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReturnFromFailed")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)
        self._transition(admin_client, headers, task["id"], "doing")
        self._transition(admin_client, headers, task["id"], "failed")

        r = admin_client.post(
            f"/cell-fabric/tasks/{task['id']}/return-to-parent",
            json={"detail": "giving up"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["returned_to_parent"] is True

        r = admin_client.get(f"/cell-fabric/tasks/{task['id']}", headers=headers)
        event_types = [e["event_type"] for e in r.json()["events"]]
        assert "returned_to_parent" in event_types

    def test_return_to_parent_from_blocked(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReturnFromBlocked")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)
        self._transition(admin_client, headers, task["id"], "blocked", detail="stuck")

        r = admin_client.post(
            f"/cell-fabric/tasks/{task['id']}/return-to-parent", headers=headers,
        )
        assert r.status_code == 200, r.text

    def test_return_to_parent_from_todo_conflict(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReturnFromTodo")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)

        r = admin_client.post(
            f"/cell-fabric/tasks/{task['id']}/return-to-parent", headers=headers,
        )
        assert r.status_code == 409

    def test_illegal_transition_rejected(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "TransitionIllegal")
        headers = _headers(token, system["id"])
        task = self._delegated_task(admin_client, headers)
        # todo -> review is not in TASK_TRANSITIONS["todo"]
        r = self._transition(admin_client, headers, task["id"], "review")
        assert r.status_code in (409, 422)

    def test_transition_missing_task_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "TransitionMissing")
        headers = _headers(token, system["id"])
        r = self._transition(admin_client, headers, 999999, "doing")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Evidence resolution
# ---------------------------------------------------------------------------


class TestEvidenceResolution:
    def test_trace_ref_resolves(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceTrace")
        headers = _headers(token, system["id"])
        trace_id = _post_trace(admin_client, headers)

        from app.cell_tasks import resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            assert resolve_evidence_ref(conn, system["id"], f"trace:{trace_id}") is True

    def test_trace_ref_missing_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceTraceMissing")
        headers = _headers(token, system["id"])
        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            with pytest.raises(ValidationFailedError):
                resolve_evidence_ref(conn, system["id"], "trace:no-such-trace")

    def test_evaluation_ref_resolves_and_cross_system_fails(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EvidenceEvalA")
        sys_b = _create_system(admin_client, token, "EvidenceEvalB")
        eval_id = _insert_evaluation_result(sys_a["id"])

        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            assert resolve_evidence_ref(
                conn, sys_a["id"], f"evaluation:{eval_id}"
            ) is True
            with pytest.raises(ValidationFailedError):
                resolve_evidence_ref(conn, sys_b["id"], f"evaluation:{eval_id}")

    def test_shadow_result_ref_resolves_and_missing_fails(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceShadow")
        shadow_id = _insert_shadow_result(system["id"])

        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            assert resolve_evidence_ref(
                conn, system["id"], f"shadow_result:{shadow_id}"
            ) is True
            with pytest.raises(ValidationFailedError):
                resolve_evidence_ref(conn, system["id"], "shadow_result:999999")

    def test_replay_run_ref_resolves(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceReplay")
        snapshot_id = _insert_snapshot(system["id"])
        replay_run_id = _insert_replay_run(system["id"], snapshot_id)

        from app.cell_tasks import resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            assert resolve_evidence_ref(
                conn, system["id"], f"replay_run:{replay_run_id}"
            ) is True

    def test_experiment_ref_resolves(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceExperiment")
        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)

        from app.cell_tasks import resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            assert resolve_evidence_ref(
                conn, system["id"], f"experiment:{experiment_id}"
            ) is True

    def test_snapshot_file_ref_resolves_and_missing_path_fails(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceSnapshotFile")
        snapshot_id = _insert_snapshot(system["id"])
        _insert_snapshot_file(snapshot_id, path="app/foo.py")

        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            assert resolve_evidence_ref(
                conn, system["id"], f"snapshot_file:{snapshot_id}:app/foo.py"
            ) is True
            with pytest.raises(ValidationFailedError):
                resolve_evidence_ref(
                    conn, system["id"], f"snapshot_file:{snapshot_id}:app/missing.py"
                )

    def test_snapshot_file_ref_cross_system_fails(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EvidenceSnapFileA")
        sys_b = _create_system(admin_client, token, "EvidenceSnapFileB")
        snapshot_id = _insert_snapshot(sys_a["id"])
        _insert_snapshot_file(snapshot_id, path="app/foo.py")

        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            with pytest.raises(ValidationFailedError):
                resolve_evidence_ref(
                    conn, sys_b["id"], f"snapshot_file:{snapshot_id}:app/foo.py"
                )

    def test_malformed_ref_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceMalformed")
        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            for bad in ["", "bogus", "trace", "trace:", "snapshot_file:not-an-int:x"]:
                with pytest.raises(ValidationFailedError):
                    resolve_evidence_ref(conn, system["id"], bad)

    def test_reserved_quality_sample_ref_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceReservedQuality")
        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            with pytest.raises(ValidationFailedError, match="not yet implemented"):
                resolve_evidence_ref(conn, system["id"], "quality_sample:1")

    def test_improvement_ref_now_resolves(self, admin_client):
        """Issue #304 added cell_improvements -- improvement:<id> is no
        longer reserved and resolves like any other System-scoped ref."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceImprovementResolves")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers, cell_id="cell-imp")
        cell_row_id = _get_cell_row_id(system["id"], cell_id)
        improvement_id = _insert_cell_improvement(system["id"], cell_row_id)

        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            assert resolve_evidence_ref(
                conn, system["id"], f"improvement:{improvement_id}"
            ) is True
            with pytest.raises(ValidationFailedError):
                resolve_evidence_ref(conn, system["id"], "improvement:999999")

    def test_improvement_ref_cross_system_fails(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EvidenceImprovementXSysA")
        sys_b = _create_system(admin_client, token, "EvidenceImprovementXSysB")
        headers_a = _headers(token, sys_a["id"])
        cell_id = _create_cell(admin_client, headers_a, cell_id="cell-imp-a")
        cell_row_id = _get_cell_row_id(sys_a["id"], cell_id)
        improvement_id = _insert_cell_improvement(sys_a["id"], cell_row_id)

        from app.cell_tasks import ValidationFailedError, resolve_evidence_ref
        from app.db import get_conn

        with get_conn() as conn:
            with pytest.raises(ValidationFailedError):
                resolve_evidence_ref(conn, sys_b["id"], f"improvement:{improvement_id}")


# ---------------------------------------------------------------------------
# Reports (P2)
# ---------------------------------------------------------------------------


class TestReports:
    def test_digest_without_severity_ok(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportDigest")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)

        r = admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id,
                "kind": "digest",
                "fact": [{"text": "5 tasks completed", "evidence_refs": []}],
                "interpretation": [{"text": "throughput looks steady"}],
                "ask": [],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["kind"] == "digest"
        assert body["severity"] is None
        assert body["escalation_id"] is None
        assert body["fact"] == [{"text": "5 tasks completed", "evidence_refs": []}]
        assert body["interpretation"] == [{"text": "throughput looks steady"}]

    def test_escalation_without_severity_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportEscalationNoSev")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)

        r = admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id,
                "kind": "escalation",
                "fact": [],
                "ask": [{"text": "please review"}],
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_digest_with_severity_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportDigestWithSev")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)

        r = admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id, "kind": "digest", "severity": "sev2",
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_escalation_creates_escalation_row(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportEscalation")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)

        r = admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id,
                "kind": "escalation",
                "severity": "sev1",
                "fact": [{"text": "error rate spiking", "evidence_refs": []}],
                "ask": [{"text": "should we roll back?"}],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["escalation_id"] is not None

        r = admin_client.get("/cell-fabric/escalations", headers=headers)
        escalations = r.json()["escalations"]
        assert len(escalations) == 1
        assert escalations[0]["severity"] == "sev1"
        assert escalations[0]["status"] == "open"
        assert escalations[0]["summary"] == "error rate spiking"

    def test_unknown_field_rejected_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportUnknownField")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id, "kind": "digest", "free_form": "nope",
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_unknown_kind_rejected_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportUnknownKind")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/reports",
            json={"cell_definition_id": cell_id, "kind": "status_update"},
            headers=headers,
        )
        assert r.status_code == 422

    def test_facts_interpretations_asks_kept_separate(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportSeparateFields")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        trace_id = _post_trace(admin_client, headers)

        r = admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id,
                "kind": "digest",
                "fact": [{"text": "observed X", "evidence_refs": [f"trace:{trace_id}"]}],
                "interpretation": [{"text": "this might mean Y"}],
                "ask": [{"text": "should we do Z?"}],
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["fact"][0]["text"] == "observed X"
        assert body["fact"][0]["evidence_refs"] == [f"trace:{trace_id}"]
        assert body["interpretation"][0]["text"] == "this might mean Y"
        assert body["ask"][0]["text"] == "should we do Z?"

    def test_unresolvable_fact_evidence_ref_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportBadEvidence")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id,
                "kind": "digest",
                "fact": [{"text": "x", "evidence_refs": ["trace:no-such-trace"]}],
            },
            headers=headers,
        )
        assert r.status_code == 422

    def test_idempotent_resend_no_duplicate(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportIdempotent")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        payload = {
            "cell_definition_id": cell_id,
            "kind": "digest",
            "fact": [],
            "idempotency_key": "report-dedupe-1",
        }
        r1 = admin_client.post("/cell-fabric/reports", json=payload, headers=headers)
        r2 = admin_client.post("/cell-fabric/reports", json=payload, headers=headers)
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r1.json()["id"] == r2.json()["id"]

        r = admin_client.get(
            "/cell-fabric/reports", params={"cell_definition_id": cell_id}, headers=headers,
        )
        assert len(r.json()["reports"]) == 1

    def test_list_filters(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "ReportListFilters")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        admin_client.post(
            "/cell-fabric/reports",
            json={"cell_definition_id": cell_id, "kind": "digest", "fact": []},
            headers=headers,
        )
        admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id, "kind": "escalation",
                "severity": "sev3", "fact": [],
            },
            headers=headers,
        )
        r = admin_client.get(
            "/cell-fabric/reports", params={"kind": "escalation"}, headers=headers,
        )
        assert len(r.json()["reports"]) == 1
        assert r.json()["reports"][0]["kind"] == "escalation"


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


class TestEscalations:
    def _create_escalation(self, client, headers, cell_id):
        r = client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_id, "kind": "escalation",
                "severity": "sev2", "fact": [{"text": "issue", "evidence_refs": []}],
            },
            headers=headers,
        )
        assert r.status_code == 201
        r = client.get("/cell-fabric/escalations", headers=headers)
        return r.json()["escalations"][0]

    def test_acknowledge_then_resolve(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EscalationLifecycle")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        escalation = self._create_escalation(admin_client, headers, cell_id)

        r = admin_client.post(
            f"/cell-fabric/escalations/{escalation['id']}/acknowledge", headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "acknowledged"

        r = admin_client.post(
            f"/cell-fabric/escalations/{escalation['id']}/resolve", headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "resolved"

    def test_resolve_directly_from_open(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EscalationDirectResolve")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        escalation = self._create_escalation(admin_client, headers, cell_id)

        r = admin_client.post(
            f"/cell-fabric/escalations/{escalation['id']}/resolve", headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "resolved"

    def test_acknowledge_twice_conflict(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EscalationAckTwice")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        escalation = self._create_escalation(admin_client, headers, cell_id)

        admin_client.post(
            f"/cell-fabric/escalations/{escalation['id']}/acknowledge", headers=headers,
        )
        r = admin_client.post(
            f"/cell-fabric/escalations/{escalation['id']}/acknowledge", headers=headers,
        )
        assert r.status_code == 409

    def test_resolve_twice_conflict(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EscalationResolveTwice")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        escalation = self._create_escalation(admin_client, headers, cell_id)

        admin_client.post(
            f"/cell-fabric/escalations/{escalation['id']}/resolve", headers=headers,
        )
        r = admin_client.post(
            f"/cell-fabric/escalations/{escalation['id']}/resolve", headers=headers,
        )
        assert r.status_code == 409

    def test_status_filter(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EscalationStatusFilter")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        escalation = self._create_escalation(admin_client, headers, cell_id)
        admin_client.post(
            f"/cell-fabric/escalations/{escalation['id']}/acknowledge", headers=headers,
        )
        r = admin_client.get(
            "/cell-fabric/escalations", params={"status": "open"}, headers=headers,
        )
        assert r.json()["escalations"] == []
        r = admin_client.get(
            "/cell-fabric/escalations", params={"status": "acknowledged"}, headers=headers,
        )
        assert len(r.json()["escalations"]) == 1

    def test_acknowledge_missing_escalation_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EscalationMissing")
        headers = _headers(token, system["id"])
        r = admin_client.post(
            "/cell-fabric/escalations/999999/acknowledge", headers=headers,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_goals_are_system_scoped(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "IsoGoalA")
        sys_b = _create_system(admin_client, token, "IsoGoalB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])
        goal_a = _create_goal(admin_client, headers_a)

        r = admin_client.get("/cell-fabric/goals", headers=headers_b)
        assert r.json()["goals"] == []
        r = admin_client.get(f"/cell-fabric/goals/{goal_a['id']}", headers=headers_b)
        assert r.status_code == 404

    def test_tasks_are_system_scoped(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "IsoTaskA")
        sys_b = _create_system(admin_client, token, "IsoTaskB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])
        goal_a, cell_a = _setup_goal_and_owner(admin_client, headers_a)
        task_a = _delegate_task(admin_client, headers_a, goal_a["id"], cell_a).json()

        r = admin_client.get("/cell-fabric/tasks", headers=headers_b)
        assert r.json()["tasks"] == []
        r = admin_client.get(f"/cell-fabric/tasks/{task_a['id']}", headers=headers_b)
        assert r.status_code == 404

    def test_reports_and_escalations_are_system_scoped(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "IsoReportA")
        sys_b = _create_system(admin_client, token, "IsoReportB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])
        cell_a = _create_cell(admin_client, headers_a)

        admin_client.post(
            "/cell-fabric/reports",
            json={
                "cell_definition_id": cell_a, "kind": "escalation",
                "severity": "sev1", "fact": [],
            },
            headers=headers_a,
        )

        r = admin_client.get("/cell-fabric/reports", headers=headers_b)
        assert r.json()["reports"] == []
        r = admin_client.get("/cell-fabric/escalations", headers=headers_b)
        assert r.json()["escalations"] == []


# ---------------------------------------------------------------------------
# Migration / additive schema
# ---------------------------------------------------------------------------


class TestMigration:
    def test_new_tables_appear_via_init_db(self, admin_client):
        from app.db import get_conn, init_db

        init_db()
        with get_conn() as conn:
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        for table in (
            "cell_goals", "cell_tasks", "cell_task_events",
            "cell_reports", "cell_escalations",
        ):
            assert table in tables

    def test_init_db_idempotent_preserves_existing_data(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MigrationCellTasks")
        headers = _headers(token, system["id"])
        goal, cell_id = _setup_goal_and_owner(admin_client, headers)
        task = _delegate_task(admin_client, headers, goal["id"], cell_id).json()

        from app.db import init_db

        init_db()
        init_db()

        r = admin_client.get(f"/cell-fabric/goals/{goal['id']}", headers=headers)
        assert r.status_code == 200
        r = admin_client.get(f"/cell-fabric/tasks/{task['id']}", headers=headers)
        assert r.status_code == 200
