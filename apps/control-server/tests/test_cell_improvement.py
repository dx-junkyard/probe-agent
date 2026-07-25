"""Tests for Issue #304: Probe Cell Fabric Sub 7 -- 改善仮説・カナリア・
shadow実行承認ゲート.

Covers:
1. State machine: every allowed/disallowed transition; rejected history is
   retained (rows and events are never deleted -- there is no DELETE
   endpoint anywhere in this module); every transition writes exactly one
   event.
2. Canary gate: transition to canary_ready without evidence is 422; with an
   unresolvable or disallowed-kind ref is 422; with valid
   replay_run/experiment/evaluation fixture refs it passes.
3. Approvals: adopted without parent approval is 409; without human approval
   is 409; with both it adopts. role_card adoption re-pins the Cell's
   role_card_id to the new version; candidate_patch adoption writes nothing
   outside the improvement tables.
4. Rubric ownership: a role_card proposal that changes rubric_ref without
   parent_cell_id set is 422 ("Cellが自己採点基準を変更できない").
5. Shadow gates: shadow_proposal and live_shadow_execution_approval are
   separate rows/statuses; requesting live-shadow before the proposal is
   approved is 409; approving execution writes no policy/candidate row;
   both decisions are decision_method='manual'.
6. Reasoning draft: mock success creates an 'observed' improvement with a
   run id + is_mock; forced LLM failure fails the run and creates NO
   improvement row (no heuristic hypothesis).
7. Suspension: 3 consecutive rejected improvements (no intervening adopted)
   blocks new creation (409) and suspends the Cell's existing non-terminal
   improvements; the resume endpoint un-suspends them.
8. Rollback: an adopted role_card improvement can roll back the pin;
   the rollback is recorded as an event.
9. Evidence ref extension: covered directly in tests/test_cell_tasks.py
   (improvement:<id> now resolves; quality_sample:<id> stays reserved).
10. System isolation.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-cell-improvement-test.db"))
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


def _create_role_card(client, headers, **overrides):
    r = client.post("/cell-fabric/role-cards", json=_card_payload(**overrides), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _create_cell(client, headers, cell_id="cell-1", role_key="worker-generic", version="1.0.0"):
    r = client.post(
        "/cell-fabric/role-cards",
        json=_card_payload(role_key=role_key, version=version),
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/cell-fabric/cells",
        json={
            "cell_id": cell_id,
            "roster": None,
            "role_card_ref": {"role_key": role_key, "version": version},
            "status": "active",
            "mission": None,
            "schema_version": "1.0",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return cell_id


def _get_cell_row(system_id, cell_id):
    from app.db import get_conn

    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cell_definitions WHERE system_id = ? AND cell_id = ?",
            (system_id, cell_id),
        ).fetchone()


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


def _insert_experiment(system_id, snapshot_id, feature_id="feat-1", status="completed"):
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
            "status": status,
            "created_at": now,
        },
    )


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


def _draft(client, headers, cell_id, observed_facts_refs=None, target_kind="role_card", parent_cell_id=None):
    payload = {
        "observed_facts_refs": observed_facts_refs or [],
        "target_kind": target_kind,
        "parent_cell_id": parent_cell_id,
    }
    return client.post(
        f"/cell-fabric/cells/{cell_id}/improvements/draft", json=payload, headers=headers,
    )


def _create_manual(client, headers, cell_id, **overrides):
    payload = {
        "target_kind": "role_card",
        "hypothesis": "",
        "expected_effect": "",
        "risk": "",
        "rollback_plan": "",
        "observed_facts_refs": [],
        "parent_cell_id": None,
    }
    payload.update(overrides)
    return client.post(
        f"/cell-fabric/cells/{cell_id}/improvements", json=payload, headers=headers,
    )


def _full_hypothesis_manual(client, headers, cell_id, **overrides):
    payload = {
        "hypothesis": "Model X underperforms on edge case Y",
        "expected_effect": "Reduce failure rate on edge case Y",
        "risk": "May regress case Z",
        "rollback_plan": "Revert to previous pinned version",
    }
    payload.update(overrides)
    return _create_manual(client, headers, cell_id, **payload)


def _transition(client, headers, improvement_id, new_status, **overrides):
    payload = {"new_status": new_status}
    payload.update(overrides)
    return client.post(
        f"/cell-fabric/improvements/{improvement_id}/transition", json=payload, headers=headers,
    )


def _parent_approve(client, headers, improvement_id, actor="parent-op"):
    return client.post(
        f"/cell-fabric/improvements/{improvement_id}/parent-approve",
        json={"actor": actor}, headers=headers,
    )


def _human_approve(client, headers, improvement_id, actor="human-op"):
    return client.post(
        f"/cell-fabric/improvements/{improvement_id}/human-approve",
        json={"actor": actor}, headers=headers,
    )


def _get_improvement(client, headers, improvement_id):
    return client.get(f"/cell-fabric/improvements/{improvement_id}", headers=headers)


def _list_improvements(client, headers, cell_id):
    return client.get(f"/cell-fabric/cells/{cell_id}/improvements", headers=headers)


def _propose_shadow(client, headers, improvement_id, note=""):
    return client.post(
        f"/cell-fabric/improvements/{improvement_id}/shadow-proposal",
        json={"note": note}, headers=headers,
    )


def _request_live_shadow(client, headers, improvement_id, note=""):
    return client.post(
        f"/cell-fabric/improvements/{improvement_id}/live-shadow-request",
        json={"note": note}, headers=headers,
    )


def _decide_shadow(client, headers, decision_id, decision, decided_by="reviewer"):
    return client.post(
        f"/cell-fabric/shadow-decisions/{decision_id}/decide",
        json={"decision": decision, "decided_by": decided_by}, headers=headers,
    )


def _rollback(client, headers, improvement_id, actor="rollback-op"):
    return client.post(
        f"/cell-fabric/improvements/{improvement_id}/rollback",
        json={"actor": actor}, headers=headers,
    )


def _suspend(client, headers, cell_id, reason="manual suspend"):
    return client.post(
        f"/cell-fabric/cells/{cell_id}/improvements/suspend",
        json={"reason": reason}, headers=headers,
    )


def _resume(client, headers, cell_id):
    return client.post(f"/cell-fabric/cells/{cell_id}/improvements/resume", headers=headers)


def _advance_to_canary_running(
    client, headers, cell_id, evidence_refs, target_kind="role_card",
    proposed_role_card_version=None,
):
    """Create a fully-specified manual improvement and advance it through
    observed -> proposed -> canary_ready -> canary_running. Returns the
    improvement id.

    ``proposed_role_card_version`` is pinned at the ``canary_ready`` step --
    i.e. BEFORE approval -- so that a later parent/human approval covers the
    exact proposed version (Issue #304 P1-2: changing the proposed version
    after approval invalidates the approval)."""
    r = _full_hypothesis_manual(client, headers, cell_id, target_kind=target_kind)
    assert r.status_code == 201, r.text
    improvement_id = r.json()["id"]
    r = _transition(client, headers, improvement_id, "proposed")
    assert r.status_code == 200, r.text
    r = _transition(
        client, headers, improvement_id, "canary_ready",
        canary_evidence_refs=evidence_refs,
        proposed_role_card_version=proposed_role_card_version,
    )
    assert r.status_code == 200, r.text
    r = _transition(client, headers, improvement_id, "canary_running")
    assert r.status_code == 200, r.text
    return improvement_id


def _table_counts(system_id, tables):
    from app.db import get_conn

    with get_conn() as conn:
        return {
            t: conn.execute(f"SELECT COUNT(*) AS n FROM {t} WHERE system_id = ?", (system_id,)).fetchone()["n"]
            for t in tables
        }


# ---------------------------------------------------------------------------
# 1. State machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_create_manual_is_observed(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SM-Create")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)

        r = _create_manual(admin_client, headers, cell_id, target_kind="candidate_patch")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "observed"
        assert body["target_kind"] == "candidate_patch"
        assert body["cell_id"] == cell_id

    def test_observed_to_proposed_without_hypothesis_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SM-NoHypothesis")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _create_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]

        r = _transition(admin_client, headers, improvement_id, "proposed")
        assert r.status_code == 422, r.text

    def test_observed_to_proposed_with_hypothesis_ok(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SM-WithHypothesis")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]

        r = _transition(admin_client, headers, improvement_id, "proposed")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "proposed"

    def test_illegal_transition_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SM-Illegal")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]

        # observed -> canary_ready directly is not allowed.
        r = _transition(admin_client, headers, improvement_id, "canary_ready")
        assert r.status_code == 422, r.text

        # observed -> adopted directly is not allowed.
        r = _transition(admin_client, headers, improvement_id, "adopted")
        assert r.status_code == 422, r.text

    def test_terminal_statuses_have_no_outgoing_transitions(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SM-Terminal")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]
        r = _transition(admin_client, headers, improvement_id, "proposed")
        assert r.status_code == 200
        r = _transition(admin_client, headers, improvement_id, "rejected")
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

        r = _transition(admin_client, headers, improvement_id, "proposed")
        assert r.status_code == 422, r.text

    def test_rejected_history_never_deleted(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SM-KeepHistory")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]
        _transition(admin_client, headers, improvement_id, "proposed")
        r = _transition(admin_client, headers, improvement_id, "rejected")
        assert r.status_code == 200

        r = _get_improvement(admin_client, headers, improvement_id)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["improvement"]["status"] == "rejected"
        event_types = [e["event_type"] for e in body["events"]]
        assert "created" in event_types
        assert event_types.count("status_transition") == 2

        r = _list_improvements(admin_client, headers, cell_id)
        assert any(i["id"] == improvement_id for i in r.json()["improvements"])

    def test_every_transition_writes_exactly_one_event(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "SM-EventCount")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)

        r = _full_hypothesis_manual(admin_client, headers, cell_id, target_kind="candidate_patch")
        improvement_id = r.json()["id"]
        _transition(admin_client, headers, improvement_id, "proposed")
        _transition(
            admin_client, headers, improvement_id, "canary_ready",
            canary_evidence_refs=[f"experiment:{experiment_id}"],
        )

        r = _get_improvement(admin_client, headers, improvement_id)
        events = r.json()["events"]
        # created + 2 status_transitions
        assert len(events) == 3


# ---------------------------------------------------------------------------
# 2. Canary gate
# ---------------------------------------------------------------------------


class TestCanaryGate:
    def _proposed(self, client, headers, cell_id, target_kind="candidate_patch"):
        r = _full_hypothesis_manual(client, headers, cell_id, target_kind=target_kind)
        improvement_id = r.json()["id"]
        r = _transition(client, headers, improvement_id, "proposed")
        assert r.status_code == 200, r.text
        return improvement_id

    def test_canary_ready_without_evidence_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Canary-NoEvidence")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        improvement_id = self._proposed(admin_client, headers, cell_id)

        r = _transition(admin_client, headers, improvement_id, "canary_ready")
        assert r.status_code == 422, r.text

    def test_canary_ready_with_unresolvable_ref_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Canary-Unresolvable")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        improvement_id = self._proposed(admin_client, headers, cell_id)

        r = _transition(
            admin_client, headers, improvement_id, "canary_ready",
            canary_evidence_refs=["experiment:999999"],
        )
        assert r.status_code == 422, r.text

    def test_canary_ready_with_disallowed_kind_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Canary-DisallowedKind")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        improvement_id = self._proposed(admin_client, headers, cell_id)
        trace_id = _post_trace(admin_client, headers, component_id=cell_id)

        # trace:<id> is a valid evidence ref elsewhere, but NOT an allowed
        # canary evidence kind (only replay_run/experiment/evaluation).
        r = _transition(
            admin_client, headers, improvement_id, "canary_ready",
            canary_evidence_refs=[f"trace:{trace_id}"],
        )
        assert r.status_code == 422, r.text

    def test_canary_ready_with_valid_refs_passes(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Canary-Valid")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        improvement_id = self._proposed(admin_client, headers, cell_id)

        snapshot_id = _insert_snapshot(system["id"])
        replay_run_id = _insert_replay_run(system["id"], snapshot_id, component_id=cell_id)
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        _post_trace(admin_client, headers, trace_id="t1", component_id=cell_id)
        evaluation_id = _insert_evaluation_result(system["id"], trace_id="t1", component_id=cell_id)

        r = _transition(
            admin_client, headers, improvement_id, "canary_ready",
            canary_evidence_refs=[
                f"replay_run:{replay_run_id}",
                f"experiment:{experiment_id}",
                f"evaluation:{evaluation_id}",
            ],
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "canary_ready"
        assert set(body["canary_evidence_refs"]) == {
            f"replay_run:{replay_run_id}", f"experiment:{experiment_id}", f"evaluation:{evaluation_id}",
        }


# ---------------------------------------------------------------------------
# 3. Approvals / adoption
# ---------------------------------------------------------------------------


class TestApprovalsAndAdoption:
    def test_adopt_without_parent_approval_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Adopt-NoParent")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        improvement_id = _advance_to_canary_running(
            admin_client, headers, cell_id, [f"experiment:{experiment_id}"],
            target_kind="candidate_patch",
        )
        _human_approve(admin_client, headers, improvement_id)

        r = _transition(admin_client, headers, improvement_id, "adopted")
        assert r.status_code == 409, r.text

    def test_adopt_without_human_approval_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Adopt-NoHuman")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        improvement_id = _advance_to_canary_running(
            admin_client, headers, cell_id, [f"experiment:{experiment_id}"],
            target_kind="candidate_patch",
        )
        _parent_approve(admin_client, headers, improvement_id)

        r = _transition(admin_client, headers, improvement_id, "adopted")
        assert r.status_code == 409, r.text

    def test_adopt_role_card_repins_cell(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Adopt-RoleCard")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        cell_row_before = _get_cell_row(system["id"], cell_id)
        old_role_card_id = cell_row_before["role_card_id"]

        new_card = _create_role_card(admin_client, headers, version="1.1.0")

        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        # Pin the proposed version BEFORE approval so the approval covers it
        # (P1-2). Adoption then does not change the proposed content.
        improvement_id = _advance_to_canary_running(
            admin_client, headers, cell_id, [f"experiment:{experiment_id}"],
            target_kind="role_card", proposed_role_card_version="1.1.0",
        )
        _parent_approve(admin_client, headers, improvement_id)
        _human_approve(admin_client, headers, improvement_id)

        r = _transition(
            admin_client, headers, improvement_id, "adopted",
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "adopted"
        assert body["proposed_role_card_version"] == "1.1.0"

        cell_row_after = _get_cell_row(system["id"], cell_id)
        assert cell_row_after["role_card_id"] == new_card["id"]
        assert cell_row_after["role_card_id"] != old_role_card_id

    def test_adopt_candidate_patch_writes_nothing_outside_improvement_tables(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Adopt-CandidatePatch")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        improvement_id = _advance_to_canary_running(
            admin_client, headers, cell_id, [f"experiment:{experiment_id}"],
            target_kind="candidate_patch",
        )
        _parent_approve(admin_client, headers, improvement_id)
        _human_approve(admin_client, headers, improvement_id)

        watched_tables = ["components", "replay_runs", "experiments", "cell_bindings", "cell_activations"]
        before = _table_counts(system["id"], watched_tables)
        cell_row_before = _get_cell_row(system["id"], cell_id)

        r = _transition(admin_client, headers, improvement_id, "adopted")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "adopted"

        after = _table_counts(system["id"], watched_tables)
        assert before == after
        cell_row_after = _get_cell_row(system["id"], cell_id)
        assert cell_row_after["role_card_id"] == cell_row_before["role_card_id"]


# ---------------------------------------------------------------------------
# 4. Rubric ownership
# ---------------------------------------------------------------------------


class TestRubricOwnership:
    def test_rubric_change_without_parent_cell_id_422(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Rubric-NoParent")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        _create_role_card(admin_client, headers, version="1.1.0", rubric_ref="rubric:new")

        r = _full_hypothesis_manual(admin_client, headers, cell_id, target_kind="role_card")
        improvement_id = r.json()["id"]
        r = _transition(admin_client, headers, improvement_id, "proposed")
        assert r.status_code == 200

        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        r = _transition(
            admin_client, headers, improvement_id, "canary_ready",
            canary_evidence_refs=[f"experiment:{experiment_id}"],
            proposed_role_card_version="1.1.0",
        )
        assert r.status_code == 422, r.text
        assert "自己採点基準" in r.json()["detail"]

    def test_rubric_change_with_parent_cell_id_ok(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Rubric-WithParent")
        headers = _headers(token, system["id"])
        parent_cell_id = _create_cell(
            admin_client, headers, cell_id="parent-cell", role_key="worker-parent",
        )
        cell_id = _create_cell(
            admin_client, headers, cell_id="child-cell", role_key="worker-child",
        )
        _create_role_card(
            admin_client, headers, role_key="worker-child", version="1.1.0",
            rubric_ref="rubric:new",
        )

        r = _full_hypothesis_manual(
            admin_client, headers, cell_id, target_kind="role_card", parent_cell_id=parent_cell_id,
        )
        assert r.status_code == 201, r.text
        improvement_id = r.json()["id"]
        _transition(admin_client, headers, improvement_id, "proposed")

        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        r = _transition(
            admin_client, headers, improvement_id, "canary_ready",
            canary_evidence_refs=[f"experiment:{experiment_id}"],
            proposed_role_card_version="1.1.0",
        )
        assert r.status_code == 200, r.text
        assert r.json()["proposed_role_card_version"] == "1.1.0"


# ---------------------------------------------------------------------------
# 5. Shadow gates
# ---------------------------------------------------------------------------


class TestShadowGates:
    def test_proposal_and_execution_approval_are_separate_rows(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Shadow-Separate")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]
        _transition(admin_client, headers, improvement_id, "proposed")

        # Replay-first (P1-5): a completed isolated Replay run must back the
        # improvement before a live shadow may be requested.
        snapshot_id = _insert_snapshot(system["id"])
        replay_run_id = _insert_replay_run(system["id"], snapshot_id, component_id=cell_id)
        r = _transition(
            admin_client, headers, improvement_id, "canary_ready",
            canary_evidence_refs=[f"replay_run:{replay_run_id}"],
        )
        assert r.status_code == 200, r.text

        r = _propose_shadow(admin_client, headers, improvement_id, note="proposal note")
        assert r.status_code == 201, r.text
        proposal = r.json()
        assert proposal["kind"] == "shadow_proposal"
        assert proposal["status"] == "proposed"

        r = _decide_shadow(admin_client, headers, proposal["id"], "approved")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"
        assert r.json()["decision_method"] == "manual"

        r = _request_live_shadow(admin_client, headers, improvement_id, note="execution note")
        assert r.status_code == 201, r.text
        execution = r.json()
        assert execution["kind"] == "live_shadow_execution_approval"
        assert execution["status"] == "proposed"
        assert execution["id"] != proposal["id"]

        # Approving execution never flips the proposal's own status.
        r = _get_improvement(admin_client, headers, improvement_id)
        decisions = r.json()["shadow_decisions"]
        proposal_row = next(d for d in decisions if d["id"] == proposal["id"])
        assert proposal_row["status"] == "approved"

    def test_live_shadow_request_before_proposal_approved_409(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Shadow-TooEarly")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]
        _transition(admin_client, headers, improvement_id, "proposed")

        # No shadow_proposal at all yet.
        r = _request_live_shadow(admin_client, headers, improvement_id)
        assert r.status_code == 409, r.text

        # A proposed-but-not-yet-approved proposal still blocks the request.
        r = _propose_shadow(admin_client, headers, improvement_id)
        assert r.status_code == 201, r.text
        r = _request_live_shadow(admin_client, headers, improvement_id)
        assert r.status_code == 409, r.text

    def test_approving_execution_writes_no_policy_or_candidate_rows(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Shadow-NoSideEffect")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]
        _transition(admin_client, headers, improvement_id, "proposed")

        # Replay-first evidence (P1-5) so the live shadow request is admissible.
        snapshot_id = _insert_snapshot(system["id"])
        replay_run_id = _insert_replay_run(system["id"], snapshot_id, component_id=cell_id)
        _transition(
            admin_client, headers, improvement_id, "canary_ready",
            canary_evidence_refs=[f"replay_run:{replay_run_id}"],
        )

        # Set an explicit policy row for this component so we can prove it
        # never gets touched by shadow-execution approval.
        put_r = admin_client.put(
            f"/components/{cell_id}/policy", json={"mode": "trace"}, headers=headers,
        )
        assert put_r.status_code == 200, put_r.text
        policy_before = admin_client.get(f"/components/{cell_id}/policy", headers=headers).json()

        proposal = _propose_shadow(admin_client, headers, improvement_id).json()
        _decide_shadow(admin_client, headers, proposal["id"], "approved")
        execution = _request_live_shadow(admin_client, headers, improvement_id).json()

        watched_tables = ["components", "replay_runs", "experiments", "cell_bindings"]
        before = _table_counts(system["id"], watched_tables)

        r = _decide_shadow(admin_client, headers, execution["id"], "approved")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"
        assert r.json()["decision_method"] == "manual"

        after = _table_counts(system["id"], watched_tables)
        assert before == after
        policy_after = admin_client.get(f"/components/{cell_id}/policy", headers=headers).json()
        assert policy_before == policy_after


# ---------------------------------------------------------------------------
# 6. Reasoning draft (fail-closed)
# ---------------------------------------------------------------------------


class TestReasoningDraft:
    def test_mock_success_creates_observed_improvement(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Draft-MockOk")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        trace_id = _post_trace(admin_client, headers, component_id=cell_id)

        r = _draft(admin_client, headers, cell_id, observed_facts_refs=[f"trace:{trace_id}"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["draft_error"] is None
        assert body["is_mock"] is True
        assert body["intelligence_run_id"] is not None
        improvement = body["improvement"]
        assert improvement is not None
        assert improvement["status"] == "observed"
        assert improvement["hypothesis"] != ""
        assert improvement["expected_effect"] != ""
        assert improvement["risk"] != ""
        assert improvement["rollback_plan"] != ""
        assert improvement["proposal_run_id"] == body["intelligence_run_id"]
        assert improvement["is_mock"] is True

        from app.db import get_conn

        with get_conn() as conn:
            run_row = conn.execute(
                "SELECT run_type, decision_method, status FROM intelligence_runs WHERE id = ?",
                (body["intelligence_run_id"],),
            ).fetchone()
        assert run_row["run_type"] == "cell_improvement_draft"
        assert run_row["decision_method"] == "reasoning_llm"
        assert run_row["status"] == "completed"

    def test_unresolvable_observed_fact_ref_422_before_any_llm_call(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Draft-BadRef")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)

        r = _draft(admin_client, headers, cell_id, observed_facts_refs=["trace:does-not-exist"])
        assert r.status_code == 422, r.text

        from app.db import get_conn

        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM intelligence_runs WHERE system_id = ? AND run_type = 'cell_improvement_draft'",
                (system["id"],),
            ).fetchone()["n"]
        assert count == 0

    def test_forced_llm_failure_creates_no_improvement_row(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Draft-Forced-Failure")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        trace_id = _post_trace(admin_client, headers, component_id=cell_id)

        import app.cell_improvement as cell_improvement_module
        from app.llm import LLMError

        class _BoomClient:
            def generate_text(self, *args, **kwargs):
                raise LLMError("forced failure for test")

        monkeypatch.setattr(cell_improvement_module, "create_llm_client", lambda config: _BoomClient())

        before_count = _table_counts(system["id"], ["cell_improvements"])["cell_improvements"]

        r = _draft(admin_client, headers, cell_id, observed_facts_refs=[f"trace:{trace_id}"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["improvement"] is None
        assert body["draft_error"] is not None
        run_id = body["intelligence_run_id"]

        after_count = _table_counts(system["id"], ["cell_improvements"])["cell_improvements"]
        assert after_count == before_count

        from app.db import get_conn

        with get_conn() as conn:
            run_row = conn.execute(
                "SELECT status, error_details FROM intelligence_runs WHERE id = ?", (run_id,),
            ).fetchone()
        assert run_row["status"] == "failed"
        assert run_row["error_details"]


# ---------------------------------------------------------------------------
# 7. Suspension / auto-suspend circuit breaker
# ---------------------------------------------------------------------------


class TestSuspension:
    def test_three_consecutive_rejections_blocks_creation_and_suspends_open(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Suspend-Circuit")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)

        # An existing, still-open (non-terminal) improvement that should get
        # auto-suspended once the 3rd rejection lands.
        r = _full_hypothesis_manual(admin_client, headers, cell_id, target_kind="candidate_patch")
        open_improvement_id = r.json()["id"]

        # Reject 3 improvements in a row (none of them adopted in between).
        for _ in range(3):
            r = _full_hypothesis_manual(admin_client, headers, cell_id, target_kind="candidate_patch")
            assert r.status_code == 201, r.text
            improvement_id = r.json()["id"]
            _transition(admin_client, headers, improvement_id, "proposed")
            r = _transition(admin_client, headers, improvement_id, "rejected")
            assert r.status_code == 200, r.text

        # The pre-existing open improvement should now be suspended.
        r = _get_improvement(admin_client, headers, open_improvement_id)
        assert r.json()["improvement"]["suspended"] is True

        # New improvement creation is refused.
        r = _full_hypothesis_manual(admin_client, headers, cell_id, target_kind="candidate_patch")
        assert r.status_code == 409, r.text

        r = _draft(admin_client, headers, cell_id, observed_facts_refs=[])
        assert r.status_code == 409, r.text

        # A suspended improvement refuses transitions.
        r = _transition(admin_client, headers, open_improvement_id, "proposed")
        assert r.status_code == 409, r.text

    def test_resume_unsuspends_and_allows_transition(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Suspend-Resume")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)

        r = _full_hypothesis_manual(admin_client, headers, cell_id, target_kind="candidate_patch")
        open_improvement_id = r.json()["id"]

        for _ in range(3):
            r = _full_hypothesis_manual(admin_client, headers, cell_id, target_kind="candidate_patch")
            improvement_id = r.json()["id"]
            _transition(admin_client, headers, improvement_id, "proposed")
            _transition(admin_client, headers, improvement_id, "rejected")

        r = _get_improvement(admin_client, headers, open_improvement_id)
        assert r.json()["improvement"]["suspended"] is True

        r = _resume(admin_client, headers, cell_id)
        assert r.status_code == 200, r.text

        r = _get_improvement(admin_client, headers, open_improvement_id)
        assert r.json()["improvement"]["suspended"] is False

        r = _transition(admin_client, headers, open_improvement_id, "proposed")
        assert r.status_code == 200, r.text

    def test_manual_suspend_endpoint(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Suspend-Manual")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id)
        improvement_id = r.json()["id"]

        r = _suspend(admin_client, headers, cell_id, reason="manual pause for review")
        assert r.status_code == 200, r.text
        improvements = {i["id"]: i for i in r.json()["improvements"]}
        assert improvements[improvement_id]["suspended"] is True
        assert improvements[improvement_id]["suspension_reason"] == "manual pause for review"

        r = _transition(admin_client, headers, improvement_id, "proposed")
        assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# 8. Rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rollback_adopted_role_card_restores_previous_pin(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Rollback-RoleCard")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        cell_row_before = _get_cell_row(system["id"], cell_id)
        original_role_card_id = cell_row_before["role_card_id"]

        new_card = _create_role_card(admin_client, headers, version="1.1.0")
        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        improvement_id = _advance_to_canary_running(
            admin_client, headers, cell_id, [f"experiment:{experiment_id}"],
            target_kind="role_card", proposed_role_card_version="1.1.0",
        )
        _parent_approve(admin_client, headers, improvement_id)
        _human_approve(admin_client, headers, improvement_id)
        r = _transition(
            admin_client, headers, improvement_id, "adopted",
        )
        assert r.status_code == 200, r.text

        cell_row_after_adopt = _get_cell_row(system["id"], cell_id)
        assert cell_row_after_adopt["role_card_id"] == new_card["id"]

        r = _rollback(admin_client, headers, improvement_id)
        assert r.status_code == 200, r.text

        cell_row_after_rollback = _get_cell_row(system["id"], cell_id)
        assert cell_row_after_rollback["role_card_id"] == original_role_card_id

        r = _get_improvement(admin_client, headers, improvement_id)
        event_types = [e["event_type"] for e in r.json()["events"]]
        assert "rolled_back" in event_types

    def test_rollback_refused_for_non_adopted(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Rollback-NotAdopted")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        r = _full_hypothesis_manual(admin_client, headers, cell_id, target_kind="role_card")
        improvement_id = r.json()["id"]

        r = _rollback(admin_client, headers, improvement_id)
        assert r.status_code == 409, r.text

    def test_rollback_refused_for_candidate_patch(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Rollback-CandidatePatch")
        headers = _headers(token, system["id"])
        cell_id = _create_cell(admin_client, headers)
        snapshot_id = _insert_snapshot(system["id"])
        experiment_id = _insert_experiment(system["id"], snapshot_id)
        improvement_id = _advance_to_canary_running(
            admin_client, headers, cell_id, [f"experiment:{experiment_id}"],
            target_kind="candidate_patch",
        )
        _parent_approve(admin_client, headers, improvement_id)
        _human_approve(admin_client, headers, improvement_id)
        _transition(admin_client, headers, improvement_id, "adopted")

        r = _rollback(admin_client, headers, improvement_id)
        assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# 9. System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_improvements_isolated_across_systems(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "Isolation-A")
        sys_b = _create_system(admin_client, token, "Isolation-B")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])
        cell_id = _create_cell(admin_client, headers_a, cell_id="shared-name")
        _create_cell(admin_client, headers_b, cell_id="shared-name")

        r = _full_hypothesis_manual(admin_client, headers_a, cell_id)
        improvement_id = r.json()["id"]

        r = _get_improvement(admin_client, headers_b, improvement_id)
        assert r.status_code == 404, r.text

        r = _list_improvements(admin_client, headers_b, "shared-name")
        assert r.json()["improvements"] == []

    def test_cross_system_parent_cell_id_404(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "Isolation-ParentA")
        sys_b = _create_system(admin_client, token, "Isolation-ParentB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])
        parent_cell_id = _create_cell(admin_client, headers_b, cell_id="parent-in-b")
        cell_id = _create_cell(admin_client, headers_a, cell_id="child-in-a")

        r = _full_hypothesis_manual(
            admin_client, headers_a, cell_id, parent_cell_id=parent_cell_id,
        )
        assert r.status_code == 404, r.text
