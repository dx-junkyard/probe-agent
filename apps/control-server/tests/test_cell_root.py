"""Tests for Issue #303: Probe Cell Fabric Sub 5 -- Root Orchestrator と
統合ダイジェスト UX.

Covers:
1. Digest contract: 1 orchestrator + 2 workers + tasks + escalations
   (sev1/sev2/sev3) -- conclusion has <=3 top risks with the sev1 group
   first, sev2 appears in key_points as a pending-decision entry, sev3 only
   in evidence, and all 4 progressive-disclosure levels are present.
2. Canonical reuse: the digest's audit block embeds
   `system_state.build_system_state`'s own `user_phase`/`overall_severity`
   verbatim rather than re-deriving them.
3. Deterministic dedupe: two escalations sharing the same
   (cell, "escalation", evidence-or-summary) root cause collapse into one
   entry carrying a `sources` list of both ids.
4. Evidence resolvability: every `evidence_refs` entry anywhere in the
   digest resolves via `cell_tasks.resolve_evidence_ref` (all 6 ref kinds),
   kept separate from system_state's own (differently-shaped) evidence.
5. Ask lifecycle: idempotent sync, `accepted` unblocks a blocked owning
   task (with an audit event), `rejected` acknowledges the source
   escalation, `held` has no side effect, deciding twice is 409,
   `execution_approved` stays 0 no matter what, and an unknown decision is
   422.
6. Staleness: a binding forced `stale` via the existing refresh-drift
   endpoint is flagged in the digest's audit block.
7. System isolation of the digest and Asks.
8. No-LLM regression: root-digest and Ask endpoints succeed even when the
   LLM layer would raise if called.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.cell_tasks import resolve_evidence_ref
from app.db import get_conn


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-cell-root-test.db"))
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
    assert r.status_code == 201, r.text
    return r.json()


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
    r = client.post("/cell-fabric/tasks", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _transition_task(client, headers, task_id, **payload):
    r = client.post(f"/cell-fabric/tasks/{task_id}/transition", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _submit_escalation(client, headers, cell_definition_id, severity="sev2", summary_facts=None, task_id=None):
    r = client.post(
        "/cell-fabric/reports",
        json={
            "cell_definition_id": cell_definition_id,
            "task_id": task_id,
            "kind": "escalation",
            "severity": severity,
            "fact": summary_facts if summary_facts is not None else [
                {"text": "something is wrong", "evidence_refs": []},
            ],
            "interpretation": [],
            "ask": [],
            "idempotency_key": None,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


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
    return trace_id


def _insert_row(system_id, table, columns_values):
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
        system_id, "repository_snapshots",
        {
            "system_id": system_id, "repo_path": "/repo", "commit_sha": commit_sha,
            "status": "ready", "created_at": now,
        },
    )


def _insert_snapshot_file(snapshot_id, path="app/foo.py"):
    with get_conn() as conn:
        conn.execute("BEGIN")
        cur = conn.execute(
            """INSERT INTO snapshot_files (snapshot_id, path, source_type, size_bytes)
               VALUES (?, ?, 'text', 10)""",
            (snapshot_id, path),
        )
        conn.execute("COMMIT")
        return cur.lastrowid


def _insert_evaluation_result(system_id, trace_id="t1", component_id="comp-1"):
    now = time.time()
    return _insert_row(
        system_id, "evaluation_results",
        {
            "system_id": system_id, "trace_id": trace_id, "component_id": component_id,
            "criterion_id": 1, "status": "pass", "score": 1.0, "reason": "ok",
            "actual_output": "ok", "expected_value": "ok", "created_at": now,
        },
    )


def _insert_shadow_result(system_id, trace_id="t1", component_id="comp-1"):
    now = time.time()
    return _insert_row(
        system_id, "shadow_results",
        {
            "system_id": system_id, "trace_id": trace_id, "component_id": component_id,
            "current_output": "a", "candidate_output": "b", "candidate_error": None,
            "candidate_duration_ms": 1.0, "evaluation": None, "timestamp": now,
        },
    )


def _insert_replay_run(system_id, snapshot_id, component_id="comp-1"):
    now = time.time()
    replay_set_id = _insert_row(
        system_id, "replay_sets",
        {
            "system_id": system_id, "component_id": component_id, "name": "set-1",
            "trace_ids_json": "[]", "source": "manual", "created_at": now,
        },
    )
    return _insert_row(
        system_id, "replay_runs",
        {
            "system_id": system_id, "replay_set_id": replay_set_id, "component_id": component_id,
            "snapshot_id": snapshot_id, "commit_sha": "deadbeef", "symbol_path": "app/foo.py",
            "symbol_qualified_name": "foo", "status": "completed", "trace_set_hash": "hash",
            "sandbox_config_json": "{}", "created_at": now,
        },
    )


def _insert_experiment(system_id, snapshot_id, feature_id="feat-1"):
    now = time.time()
    return _insert_row(
        system_id, "experiments",
        {
            "system_id": system_id, "feature_id": feature_id, "objective": "improve",
            "snapshot_id": snapshot_id, "baseline_commit": "deadbeef", "config_revision": "1",
            "execution_config": "{}", "status": "draft", "created_at": now,
        },
    )


def _setup_orchestrator_with_workers(client, headers, orch_id="orch-1"):
    _create_role_card(client, headers)
    _create_cell(client, headers, "worker-a")
    _create_cell(client, headers, "worker-b")
    _create_cell(client, headers, orch_id, roster=["worker-a", "worker-b"])
    return orch_id


def _walk_evidence_refs(node):
    """Recursively collect every string found under an ``evidence_refs`` key
    anywhere in the digest -- these must all resolve via
    ``resolve_evidence_ref``. system_state's own ``evidence`` dicts use a
    different key/shape entirely, so they are never picked up here."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "evidence_refs" and isinstance(value, list):
                found.extend(value)
            else:
                found.extend(_walk_evidence_refs(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_evidence_refs(item))
    return found


# ---------------------------------------------------------------------------
# 1: Digest contract
# ---------------------------------------------------------------------------


class TestDigestContract:
    def test_severity_routing_and_four_levels(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-contract")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)
        quality = client.put(
            "/cell-fabric/cells/worker-a/quality-config",
            json={"sample_rate": 0.5},
            headers=headers,
        )
        assert quality.status_code == 200, quality.text

        # Distinct summaries (and cells where possible) so these three
        # escalations never collide on the same root-cause dedupe key --
        # that dedupe behavior is exercised deliberately in TestDedupe below.
        _submit_escalation(
            client, headers, "worker-a", severity="sev1",
            summary_facts=[{"text": "sev1 root cause", "evidence_refs": []}],
        )
        _submit_escalation(
            client, headers, "worker-b", severity="sev2",
            summary_facts=[{"text": "sev2 root cause", "evidence_refs": []}],
        )
        _submit_escalation(
            client, headers, "worker-a", severity="sev3",
            summary_facts=[{"text": "sev3 root cause", "evidence_refs": []}],
        )

        r = client.get("/cell-fabric/root-digest", headers=headers)
        assert r.status_code == 200, r.text
        digest = r.json()["digest"]

        for key in ("conclusion", "key_points", "evidence", "audit"):
            assert key in digest

        top_risks = digest["conclusion"]["top_risks"]
        assert len(top_risks) <= 3
        assert len(top_risks) == 1
        assert top_risks[0]["severity"] == "sev1"

        sev2_points = [
            kp for kp in digest["key_points"]
            if kp.get("type") == "escalation_pending_decision"
        ]
        assert len(sev2_points) == 1
        assert sev2_points[0]["severity"] == "sev2"

        # sev3 never appears in conclusion/key_points, only in evidence.
        assert all(tr["severity"] != "sev3" for tr in top_risks)
        assert all(kp.get("severity") != "sev3" for kp in digest["key_points"])
        sev3_evidence = digest["evidence"]["sev3_escalations"]
        assert len(sev3_evidence) == 1
        assert sev3_evidence[0]["severity"] == "sev3"

        orchestrator_points = [kp for kp in digest["key_points"] if kp["type"] == "orchestrator"]
        assert len(orchestrator_points) == 1
        assert orchestrator_points[0]["cell_id"] == "orch-1"
        quality_points = orchestrator_points[0]["quality"]
        assert quality_points == [{
            "cell_id": "worker-a",
            "pass_rate": None,
            "audited_count": 0,
            "intake_status": "accepting",
            "sample_rate": 0.5,
        }]
        assert {
            entry["cell_id"] for entry in orchestrator_points[0]["topology"]
        } == {"worker-a", "worker-b"}
        for entry in orchestrator_points[0]["topology"]:
            assert set(entry) == {
                "cell_id", "feature_refs", "capability_refs", "entrypoint_refs",
            }

    def test_no_llm_call_anywhere(self, admin_client, monkeypatch):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-no-llm")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)

        import app.cell_orchestrator as orch_mod
        import app.llm as llm_mod

        def _boom(*args, **kwargs):
            raise AssertionError("root digest must not call the LLM layer")

        monkeypatch.setattr(orch_mod, "create_llm_client", _boom)
        monkeypatch.setattr(llm_mod, "create_llm_client", _boom)

        r = client.get("/cell-fabric/root-digest", headers=headers)
        assert r.status_code == 200, r.text

        r = client.post("/cell-fabric/asks/sync", headers=headers)
        assert r.status_code == 200, r.text
        r = client.get("/cell-fabric/asks", headers=headers)
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 2: Canonical reuse of system_state
# ---------------------------------------------------------------------------


class TestCanonicalReuse:
    def test_audit_embeds_system_state_facts_verbatim(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-canonical")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)

        state = client.get("/system-state", headers=headers)
        assert state.status_code == 200, state.text
        state_body = state.json()

        r = client.get("/cell-fabric/root-digest", headers=headers)
        assert r.status_code == 200, r.text
        digest = r.json()["digest"]

        audit_state = digest["audit"]["system_state"]
        assert audit_state["user_phase"] == state_body["user_phase"]
        assert audit_state["overall_severity"] == state_body["overall_severity"]


# ---------------------------------------------------------------------------
# 3: Deterministic dedupe
# ---------------------------------------------------------------------------


class TestDedupe:
    def test_same_root_cause_collapses_with_two_sources(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-dedupe")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)

        facts = [{"text": "duplicate root cause", "evidence_refs": []}]
        e1 = _submit_escalation(client, headers, "worker-a", severity="sev1", summary_facts=facts)
        e2 = _submit_escalation(client, headers, "worker-a", severity="sev1", summary_facts=facts)
        assert e1["escalation_id"] != e2["escalation_id"]

        r = client.get("/cell-fabric/root-digest", headers=headers)
        digest = r.json()["digest"]
        top_risks = digest["conclusion"]["top_risks"]
        assert len(top_risks) == 1
        assert sorted(top_risks[0]["sources"]) == sorted([e1["escalation_id"], e2["escalation_id"]])


# ---------------------------------------------------------------------------
# 4: Evidence resolvability
# ---------------------------------------------------------------------------


class TestEvidenceResolvability:
    def test_every_evidence_ref_resolves(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-evidence")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)

        snapshot_id = _insert_snapshot(system["id"])
        _insert_snapshot_file(snapshot_id, "app/foo.py")
        trace_id = _post_trace(client, headers, "t1", "worker-a")
        _insert_evaluation_result(system["id"], trace_id=trace_id, component_id="worker-a")
        _insert_shadow_result(system["id"], trace_id=trace_id, component_id="worker-a")
        replay_run_id = _insert_replay_run(system["id"], snapshot_id, component_id="worker-a")
        experiment_id = _insert_experiment(system["id"], snapshot_id)

        all_refs = [
            f"trace:{trace_id}",
            "evaluation:1",
            "shadow_result:1",
            f"replay_run:{replay_run_id}",
            f"experiment:{experiment_id}",
            f"snapshot_file:{snapshot_id}:app/foo.py",
        ]

        goal = _create_goal(client, headers)
        task = _delegate_task(client, headers, goal["id"], "worker-a")
        _transition_task(client, headers, task["id"], new_status="doing")
        _transition_task(client, headers, task["id"], new_status="review")
        _transition_task(
            client, headers, task["id"], new_status="done",
            acceptance_met=True, evidence_refs=all_refs,
        )

        # An escalation whose report fact carries a resolvable evidence ref too.
        _submit_escalation(
            client, headers, "worker-b", severity="sev3",
            summary_facts=[{"text": "detail only", "evidence_refs": [f"trace:{trace_id}"]}],
        )

        r = client.get("/cell-fabric/root-digest", headers=headers)
        digest = r.json()["digest"]

        collected = _walk_evidence_refs(digest)
        assert collected, "expected at least one evidence_refs entry in the digest"
        with get_conn() as conn:
            for ref in collected:
                assert resolve_evidence_ref(conn, system["id"], ref) is True


# ---------------------------------------------------------------------------
# 5: Ask lifecycle
# ---------------------------------------------------------------------------


class TestAskLifecycle:
    def test_sync_is_idempotent(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-asks-idempotent")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)
        _submit_escalation(client, headers, "worker-a", severity="sev1")
        _submit_escalation(client, headers, "worker-b", severity="sev2")

        r1 = client.post("/cell-fabric/asks/sync", headers=headers)
        assert r1.status_code == 200, r1.text
        assert r1.json()["created"] == 2
        assert r1.json()["deduped"] == 0

        r2 = client.post("/cell-fabric/asks/sync", headers=headers)
        assert r2.status_code == 200, r2.text
        assert r2.json()["created"] == 0
        assert r2.json()["deduped"] == 2

        listed = client.get("/cell-fabric/asks", headers=headers)
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["asks"]) == 2

    def test_triage_proposed_ask_is_synced(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-asks-triage")
        headers = _headers(token, system["id"])
        orch_id = _setup_orchestrator_with_workers(client, headers)

        triage = client.post(f"/cell-fabric/orchestrators/{orch_id}/triage", headers=headers)
        assert triage.status_code == 201, triage.text
        assert triage.json()["triage"] is not None

        r = client.post("/cell-fabric/asks/sync", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 1

        asks = client.get("/cell-fabric/asks", headers=headers).json()["asks"]
        assert len(asks) == 1
        assert asks[0]["source_kind"] == "triage"
        assert "Mock proposed ask" in asks[0]["ask_text"]

    def test_accepted_unblocks_blocked_task(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-asks-accept")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)
        goal = _create_goal(client, headers)
        task = _delegate_task(client, headers, goal["id"], "worker-a")
        _transition_task(client, headers, task["id"], new_status="blocked", detail="waiting on X")

        _submit_escalation(client, headers, "worker-a", severity="sev1", task_id=task["id"])

        client.post("/cell-fabric/asks/sync", headers=headers)
        asks = client.get("/cell-fabric/asks", headers=headers).json()["asks"]
        ask = asks[0]
        # sync derives task_id from the escalation's originating report.
        assert ask["task_id"] == task["id"]

        r = client.post(
            f"/cell-fabric/asks/{ask['id']}/decide", json={"decision": "accepted", "note": "go ahead"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "accepted"
        assert body["decision"] == "accepted"
        assert body["decision_note"] == "go ahead"
        assert body["decision_method"] == "manual"
        assert body["execution_approved"] is False

        task_detail = client.get(f"/cell-fabric/tasks/{task['id']}", headers=headers).json()
        assert task_detail["task"]["status"] == "todo"
        event_types = [e["event_type"] for e in task_detail["events"]]
        assert "unblocked" in event_types

    def test_rejected_acknowledges_source_escalation(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-asks-reject")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)
        goal = _create_goal(client, headers)
        task = _delegate_task(client, headers, goal["id"], "worker-a")
        _transition_task(client, headers, task["id"], new_status="blocked", detail="waiting")
        escalation = _submit_escalation(
            client, headers, "worker-a", severity="sev1", task_id=task["id"],
        )
        client.post("/cell-fabric/asks/sync", headers=headers)
        ask = client.get("/cell-fabric/asks", headers=headers).json()["asks"][0]

        r = client.post(f"/cell-fabric/asks/{ask['id']}/decide", json={"decision": "rejected"}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"
        assert r.json()["decision"] == "rejected"
        assert r.json()["execution_approved"] is False

        task_detail = client.get(
            f"/cell-fabric/tasks/{task['id']}", headers=headers,
        ).json()
        assert task_detail["task"]["status"] == "failed"
        assert task_detail["events"][-1]["to_status"] == "failed"
        assert f"cell_ask {ask['id']} rejected" in task_detail["events"][-1]["detail"]

        esc = client.get("/cell-fabric/escalations", headers=headers).json()["escalations"]
        matching = [e for e in esc if e["id"] == escalation["escalation_id"]]
        assert matching[0]["status"] == "acknowledged"

    def test_held_has_no_side_effect(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-asks-hold")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)
        escalation = _submit_escalation(client, headers, "worker-a", severity="sev1")
        client.post("/cell-fabric/asks/sync", headers=headers)
        ask = client.get("/cell-fabric/asks", headers=headers).json()["asks"][0]

        r = client.post(f"/cell-fabric/asks/{ask['id']}/decide", json={"decision": "held"}, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "held"
        assert r.json()["execution_approved"] is False

        esc = client.get("/cell-fabric/escalations", headers=headers).json()["escalations"]
        matching = [e for e in esc if e["id"] == escalation["escalation_id"]]
        assert matching[0]["status"] == "open"

    def test_second_decide_is_409(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-asks-conflict")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)
        _submit_escalation(client, headers, "worker-a", severity="sev1")
        client.post("/cell-fabric/asks/sync", headers=headers)
        ask = client.get("/cell-fabric/asks", headers=headers).json()["asks"][0]

        r1 = client.post(f"/cell-fabric/asks/{ask['id']}/decide", json={"decision": "held"}, headers=headers)
        assert r1.status_code == 200, r1.text
        r2 = client.post(f"/cell-fabric/asks/{ask['id']}/decide", json={"decision": "accepted"}, headers=headers)
        assert r2.status_code == 409, r2.text

    def test_unknown_decision_is_422(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-asks-bad-decision")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)
        _submit_escalation(client, headers, "worker-a", severity="sev1")
        client.post("/cell-fabric/asks/sync", headers=headers)
        ask = client.get("/cell-fabric/asks", headers=headers).json()["asks"][0]

        r = client.post(
            f"/cell-fabric/asks/{ask['id']}/decide", json={"decision": "maybe"}, headers=headers,
        )
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 6: Staleness
# ---------------------------------------------------------------------------


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


def _insert_probe_point(conn, system_id, plan_id, component_id, path, symbol, status="approved"):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO probe_points
               (plan_id, system_id, component_id, feature_id, path, symbol,
                line_start, line_end, reason, recommended_mode, side_effect_risk,
                replayability, denylist_hit, status, created_at, updated_at)
           VALUES (?, ?, ?, 'feat-1', ?, ?, 1, 10, 'reason', 'trace', 'low',
                   'replayable', NULL, ?, ?, ?)""",
        (plan_id, system_id, component_id, path, symbol, status, now, now),
    )
    return cur.lastrowid


def _insert_code_symbol(conn, snapshot_id, system_id, path, qualified_name):
    conn.execute(
        """INSERT INTO code_symbols
               (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line)
           VALUES (?, ?, ?, ?, 'function', 1, 10)""",
        (snapshot_id, system_id, path, qualified_name),
    )


class TestStaleness:
    def test_stale_binding_flags_audit(self, admin_client):
        client = admin_client
        token = _login(client)
        system = _create_system(client, token, "sys-digest-stale")
        headers = _headers(token, system["id"])
        _setup_orchestrator_with_workers(client, headers)

        # Build a probe-point-provenance binding for worker-a, then advance
        # the snapshot so refresh-drift marks it 'stale' (same pattern as
        # tests/test_cell_binding.py::TestDrift). _insert_snapshot manages
        # its own connection (db.get_conn()'s lock is non-reentrant), so it
        # is never called from inside a `with get_conn()` block.
        snap1 = _insert_snapshot(system["id"], commit_sha="commit-1")
        with get_conn() as conn:
            run_id = _insert_intelligence_run(conn, system["id"], snap1)
            plan_id = _insert_probe_plan(conn, system["id"], snap1, run_id)
            point_id = _insert_probe_point(conn, system["id"], plan_id, "worker-a", "src/a.py", "a.fn")
            conn.commit()

        r = client.post(
            "/cell-fabric/cells/worker-a/bindings",
            json={"probe_point_id": point_id},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        snap2 = _insert_snapshot(system["id"], commit_sha="commit-2")
        with get_conn() as conn:
            _insert_code_symbol(conn, snap2, system["id"], "src/a.py", "a.fn")
            conn.commit()

        drift = client.post("/cell-fabric/cells/worker-a/bindings/refresh-drift", headers=headers)
        assert drift.status_code == 200, drift.text
        assert drift.json()["status"] == "stale"

        r = client.get("/cell-fabric/root-digest", headers=headers)
        digest = r.json()["digest"]
        assert digest["audit"]["is_stale"] is True
        assert "orch-1" in digest["audit"]["stale_orchestrator_cell_ids"]


# ---------------------------------------------------------------------------
# 7: System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_digest_and_asks_scoped_per_system(self, admin_client):
        client = admin_client
        token = _login(client)
        system_a = _create_system(client, token, "sys-iso-root-a")
        system_b = _create_system(client, token, "sys-iso-root-b")
        headers_a = _headers(token, system_a["id"])
        headers_b = _headers(token, system_b["id"])

        _setup_orchestrator_with_workers(client, headers_a)
        _submit_escalation(client, headers_a, "worker-a", severity="sev1")
        client.post("/cell-fabric/asks/sync", headers=headers_a)

        digest_b = client.get("/cell-fabric/root-digest", headers=headers_b).json()["digest"]
        assert digest_b["conclusion"]["counts"]["orchestrators"] == 0
        assert digest_b["conclusion"]["top_risks"] == []

        asks_b = client.get("/cell-fabric/asks", headers=headers_b).json()["asks"]
        assert asks_b == []

        asks_a = client.get("/cell-fabric/asks", headers=headers_a).json()["asks"]
        assert len(asks_a) == 1
