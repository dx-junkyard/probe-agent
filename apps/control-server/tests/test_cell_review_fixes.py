"""Regression tests for the PR #306 review fixes (Probe Cell Fabric,
Issue #297). Each test pins one reported safety gap so it cannot silently
regress. Helpers are intentionally local and mirror the conventions in the
per-sub-issue test files (cookie login, X-Probe-System-Id headers, direct
row inserts for evidence fixtures)."""

import time
import sqlite3

import pytest

from app import cell_binding, cell_quality, cell_tasks, db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-review.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("CELL_MODEL_ALIAS_WORKER_DEFAULT", "mock:worker-mock")
    monkeypatch.setenv("CELL_MODEL_ALIAS_AUDITOR_DEFAULT", "mock:auditor-mock")
    monkeypatch.setenv("CELL_MODEL_ALIAS_AUDITOR_CLONE", "mock:worker-mock")
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


# --- shared helpers ---------------------------------------------------------


def _login(client, username="root", password="s3cret"):
    r = client.post(
        "/auth/login", json={"username": username, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": "d"},
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


def _create_cell(client, headers, cell_id="cell-1", role_key="worker-generic",
                 version="1.0.0", model_alias="worker-default"):
    r = client.post(
        "/cell-fabric/role-cards",
        json=_card_payload(role_key=role_key, version=version, model_alias=model_alias),
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/cell-fabric/cells",
        json={
            "cell_id": cell_id, "roster": None,
            "role_card_ref": {"role_key": role_key, "version": version},
            "status": "active", "mission": None, "schema_version": "1.0",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _cell_row(system_id, cell_id):
    from app.db import get_conn
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cell_definitions WHERE system_id = ? AND cell_id = ?",
            (system_id, cell_id),
        ).fetchone()


def _insert_row(system_id, table, values):
    from app.db import get_conn
    cols = list(values)
    with get_conn() as conn:
        conn.execute("BEGIN")
        cur = conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
            [values[c] for c in cols],
        )
        conn.execute("COMMIT")
        return cur.lastrowid


def _goal(client, headers, title="g"):
    r = client.post("/cell-fabric/goals", json={"title": title}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- P1-1: suspended intake blocks new task delegation ----------------------


def test_delegate_task_refused_when_intake_suspended(client):
    token = _login(client)
    system = _system(client, token, "IntakeSuspend")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1")
    cell_row = _cell_row(system["id"], "cell-1")
    goal_id = _goal(client, headers)

    # Directly suspend this cell's intake (as the quality floor breach would).
    now = time.time()
    _insert_row(system["id"], "cell_intake_states", {
        "system_id": system["id"], "cell_definition_id": cell_row["id"],
        "intake_status": "suspended", "reason": "floor breach", "changed_at": now,
    })

    r = client.post(
        "/cell-fabric/tasks",
        json={"goal_id": goal_id, "owner_cell_id": "cell-1", "title": "t",
              "acceptance": ["ok"]},
        headers=headers,
    )
    assert r.status_code == 409, r.text
    assert "suspended" in r.json()["detail"].lower()


# --- P1-6: an audit cannot use the worker's own alias (self-audit) ----------


def test_audit_rejects_worker_alias_as_auditor(client):
    token = _login(client)
    system = _system(client, token, "SelfAudit")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1", model_alias="worker-default")
    cell_row = _cell_row(system["id"], "cell-1")

    # One sampled trace + sample row so we reach the auditor check.
    now = time.time()
    _insert_row(system["id"], "traces", {
        "system_id": system["id"], "trace_id": "t1", "component_id": "cell-1",
        "output_text": "x", "timestamp": now,
    })
    # sample_rate=1.0 so the single trace is deterministically selected.
    r = client.put(
        "/cell-fabric/cells/cell-1/quality-config",
        json={"sample_rate": 1.0}, headers=headers,
    )
    assert r.status_code == 200, r.text
    r = client.post("/cell-fabric/cells/cell-1/quality-samples/select", json={}, headers=headers)
    assert r.status_code == 200, r.text
    from app.db import get_conn
    with get_conn() as conn:
        sample = conn.execute(
            "SELECT id FROM cell_quality_samples WHERE system_id = ? AND cell_definition_id = ?",
            (system["id"], cell_row["id"]),
        ).fetchone()
    assert sample is not None

    r = client.post(
        f"/cell-fabric/quality-samples/{sample['id']}/audit",
        json={"auditor_alias": "worker-default"}, headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "separate model" in r.json()["detail"]

    # A different alias string is still a self-audit when it resolves to the
    # exact same provider/model endpoint as the worker.
    r = client.post(
        f"/cell-fabric/quality-samples/{sample['id']}/audit",
        json={"auditor_alias": "auditor-clone"}, headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "separate model" in r.json()["detail"]


def test_approval_actor_is_authenticated_identity_not_payload(client):
    token = _login(client)
    system = _system(client, token, "ApprovalIdentity")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1")
    improvement_id = _full_manual_improvement(client, headers, "cell-1")

    r = client.post(
        f"/cell-fabric/improvements/{improvement_id}/parent-approve",
        json={"actor": "forged-parent"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["parent_approved_by"] == "root"

    r = client.post(
        f"/cell-fabric/improvements/{improvement_id}/human-approve",
        json={"actor": "forged-human"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["human_approved_by"] == "root"


def test_non_admin_cannot_record_root_approval(client):
    root_token = _login(client)
    r = client.post(
        "/users",
        json={"username": "alice", "password": "pw", "role": "user"},
        headers=_bearer(root_token),
    )
    assert r.status_code == 201, r.text

    alice_token = _login(client, "alice", "pw")
    system = _system(client, alice_token, "UserOwnedRootCell")
    headers = _headers(alice_token, system["id"])
    _create_cell(client, headers, cell_id="cell-1")
    improvement_id = _full_manual_improvement(client, headers, "cell-1")

    r = client.post(
        f"/cell-fabric/improvements/{improvement_id}/parent-approve",
        json={"actor": "root"}, headers=headers,
    )
    assert r.status_code == 409, r.text
    assert "administrator Root approval" in r.json()["detail"]


# --- P1-7: re-auditing the same sample does not inflate the rolling rate ----


def test_repeated_audit_of_same_sample_does_not_inflate_floor(client):
    """A cell below the floor cannot be resumed by re-auditing one passing
    sample many times: the rolling rate dedupes to the latest audit per
    sample."""
    token = _login(client)
    system = _system(client, token, "ReauditInflate")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1")
    cell_row = _cell_row(system["id"], "cell-1")
    cdid = cell_row["id"]

    from app.db import get_conn
    now = time.time()
    with get_conn() as conn:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO cell_quality_configs
                 (system_id, cell_definition_id, sample_rate, strata_json,
                  audit_rate, quality_floor, floor_window, daily_audit_budget,
                  created_at, updated_at)
               VALUES (?, ?, 1.0, '[]', 0.1, 0.7, 10, 1000, ?, ?)""",
            (system["id"], cdid, now, now),
        )
        # Two samples: 5 fails + 1 pass = 1/6 pass rate (below floor).
        config_id = conn.execute(
            "SELECT id FROM cell_quality_configs WHERE system_id = ? AND cell_definition_id = ?",
            (system["id"], cdid),
        ).fetchone()["id"]
        sample_ids = []
        for i in range(6):
            conn.execute(
                """INSERT INTO cell_quality_samples
                     (system_id, cell_definition_id, config_id, stratum,
                      target_kind, target_id, selection_seed, selected_at)
                   VALUES (?, ?, ?, '', 'trace', ?, ?, ?)""",
                (system["id"], cdid, config_id, f"t{i}", f"seed{i}", now),
            )
            sample_ids.append(conn.execute("SELECT last_insert_rowid() AS r").fetchone()["r"])
        verdicts = ["pass"] + ["fail"] * 5
        for sid, verdict in zip(sample_ids, verdicts):
            conn.execute(
                """INSERT INTO cell_quality_audits
                     (system_id, sample_id, auditor_model_alias, verdict,
                      verdict_decision_method, is_blind, failed_criteria_json,
                      verbatim_example, explanation, created_at)
                   VALUES (?, ?, 'auditor-default', ?, 'deterministic', 0, '[]', '', '', ?)""",
                (system["id"], sid, verdict, now),
            )
        conn.execute("COMMIT")

    # Floor is breached.
    with get_conn() as conn:
        rate, denom = cell_quality._rolling_pass_rate(conn, system["id"], cdid, 10)
    assert denom == 6 and rate == pytest.approx(1 / 6)

    # Re-audit the single passing sample 20 more times (all 'pass').
    with get_conn() as conn:
        conn.execute("BEGIN")
        for _ in range(20):
            conn.execute(
                """INSERT INTO cell_quality_audits
                     (system_id, sample_id, auditor_model_alias, verdict,
                      verdict_decision_method, is_blind, failed_criteria_json,
                      verbatim_example, explanation, created_at)
                   VALUES (?, ?, 'auditor-default', 'pass', 'deterministic', 0, '[]', '', '', ?)""",
                (system["id"], sample_ids[0], now),
            )
        conn.execute("COMMIT")

    # Rolling rate is unchanged: still 1/6 (dedup by sample), NOT inflated.
    with get_conn() as conn:
        rate2, denom2 = cell_quality._rolling_pass_rate(conn, system["id"], cdid, 10)
    assert denom2 == 6 and rate2 == pytest.approx(1 / 6)

    # And resume is still refused.
    r = client.post("/cell-fabric/cells/cell-1/intake/resume", json={}, headers=headers)
    assert r.status_code == 409, r.text


# --- P1-9: only an active probe pattern can back a binding ------------------


def test_binding_rejected_from_non_active_pattern(client):
    token = _login(client)
    system = _system(client, token, "PatternStatus")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="comp-1")  # cell_id == component_id
    now = time.time()
    snapshot_id = _insert_row(system["id"], "repository_snapshots", {
        "system_id": system["id"], "commit_sha": "deadbeef", "status": "ready",
        "created_at": now,
    })
    pattern_id = _insert_row(system["id"], "probe_patterns", {
        "system_id": system["id"], "name": "p", "status": "archived",
        "source_snapshot_id": snapshot_id, "source_commit_sha": "deadbeef",
        "created_at": now, "updated_at": now,
    })
    point_id = _insert_row(system["id"], "probe_pattern_points", {
        "system_id": system["id"], "pattern_id": pattern_id, "component_id": "comp-1",
        "path": "app/foo.py", "symbol": "foo", "status": "saved",
        "created_at": now, "updated_at": now,
    })
    cell_row = _cell_row(system["id"], "comp-1")

    from app.db import get_conn
    with get_conn() as conn:
        with pytest.raises(cell_binding.CellBindingConflictError):
            cell_binding.create_binding(
                conn, system_id=system["id"], cell_definition_id=cell_row["id"],
                probe_pattern_point_id=point_id,
            )


# --- P1-8: cell_state task counts reflect the real ledger -------------------


def test_cell_state_reports_real_task_counts(client):
    token = _login(client)
    system = _system(client, token, "StateCounts")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1")
    goal_id = _goal(client, headers)
    for i in range(3):
        r = client.post(
            "/cell-fabric/tasks",
            json={"goal_id": goal_id, "owner_cell_id": "cell-1",
                  "title": f"t{i}", "acceptance": ["ok"]},
            headers=headers,
        )
        assert r.status_code == 201, r.text

    r = client.get("/cell-fabric/cells/cell-1/state", headers=headers)
    assert r.status_code == 200, r.text
    counts = r.json()["state"]["tasks"]["counts"]
    assert counts["todo"] == 3


def test_cell_state_reports_all_runtime_source_counts(client):
    token = _login(client)
    system = _system(client, token, "StateRuntimeSources")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1")
    now = time.time()

    for index, status in enumerate(("pass", "fail")):
        _insert_row(system["id"], "evaluation_results", {
            "system_id": system["id"], "trace_id": f"eval-{index}",
            "component_id": "cell-1", "criterion_id": index + 1,
            "status": status, "created_at": now,
        })
    _insert_row(system["id"], "shadow_results", {
        "system_id": system["id"], "trace_id": "shadow-1",
        "component_id": "cell-1", "timestamp": now,
    })

    snapshot_id = _snapshot(system["id"])
    replay_set_id = _insert_row(system["id"], "replay_sets", {
        "system_id": system["id"], "component_id": "cell-1",
        "name": "state-set", "trace_ids_json": "[]",
        "source": "manual", "created_at": now,
    })
    for index, status in enumerate(("completed", "failed")):
        _insert_row(system["id"], "replay_runs", {
            "system_id": system["id"], "replay_set_id": replay_set_id,
            "component_id": "cell-1", "snapshot_id": snapshot_id,
            "commit_sha": "deadbeef", "symbol_path": "app/foo.py",
            "symbol_qualified_name": "foo", "status": status,
            "trace_set_hash": f"hash-{index}", "created_at": now,
        })
    _completed_experiment(system["id"], snapshot_id, "cell-1")

    r = client.get("/cell-fabric/cells/cell-1/state", headers=headers)
    assert r.status_code == 200, r.text
    health = r.json()["state"]["health"]
    assert health["evaluation_count"] == 2
    assert health["evaluation_pass_rate"] == pytest.approx(0.5)
    assert health["shadow_result_count"] == 1
    assert health["replay_run_count"] == 2
    assert health["replay_completed_count"] == 1
    assert health["experiment_count"] == 1
    assert health["experiment_completed_count"] == 1


# --- P2: ask sync requires a user session -----------------------------------


def test_ask_sync_requires_user(client):
    token = _login(client)
    system = _system(client, token, "AskAuth")
    headers = _headers(token, system["id"])
    # With a valid user session it works.
    r = client.post("/cell-fabric/asks/sync", headers=headers)
    assert r.status_code == 200, r.text
    # The route declares require_user; assert the dependency is present so a
    # data-plane-only principal is rejected by the same guard used elsewhere.
    from app.routes import cell_root as cell_root_routes
    import inspect
    sig = inspect.signature(cell_root_routes.sync_asks)
    assert "principal" in sig.parameters


# --- P1-3 + P1-2: adoption gates (active card, major bump, approval pin) -----


def _full_manual_improvement(client, headers, cell_id, target_kind="role_card",
                             parent_cell_id=None):
    r = client.post(
        f"/cell-fabric/cells/{cell_id}/improvements",
        json={
            "target_kind": target_kind,
            "hypothesis": "h", "expected_effect": "e", "risk": "r",
            "rollback_plan": "rb", "observed_facts_refs": [],
            "parent_cell_id": parent_cell_id,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _snapshot(system_id):
    return _insert_row(system_id, "repository_snapshots", {
        "system_id": system_id, "commit_sha": "deadbeef", "status": "ready",
        "created_at": time.time(),
    })


def _completed_experiment(system_id, snapshot_id, feature_id):
    now = time.time()
    experiment_id = _insert_row(system_id, "experiments", {
        "system_id": system_id, "feature_id": feature_id, "objective": "o",
        "snapshot_id": snapshot_id, "baseline_commit": "deadbeef",
        "config_revision": "1", "execution_config": "{}", "status": "completed",
        "human_decision": "adopted",
        "human_decision_variant_key": "variant-1",
        "created_at": now,
    })
    _insert_row(system_id, "experiment_variants", {
        "experiment_id": experiment_id, "variant_key": "variant-1",
        "label": "candidate", "is_baseline": 0,
        "patch_hash": f"hash-{experiment_id}", "status": "completed",
    })
    return experiment_id


def _advance(client, headers, improvement_id, evidence_ref, version=None):
    client.post(f"/cell-fabric/improvements/{improvement_id}/transition",
                json={"new_status": "proposed"}, headers=headers)
    body = {"new_status": "canary_ready", "canary_evidence_refs": [evidence_ref]}
    if version is not None:
        body["proposed_role_card_version"] = version
    r = client.post(f"/cell-fabric/improvements/{improvement_id}/transition",
                    json=body, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/cell-fabric/improvements/{improvement_id}/transition",
                    json={"new_status": "canary_running"}, headers=headers)
    assert r.status_code == 200, r.text


def test_adopt_incompatible_bump_requires_authenticated_root_override(client):
    token = _login(client)
    system = _system(client, token, "MajorBump")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1", role_key="worker-generic", version="1.0.0")
    # Incompatible major version.
    r = client.post("/cell-fabric/role-cards",
                    json=_card_payload(role_key="worker-generic", version="2.0.0"),
                    headers=headers)
    assert r.status_code == 201, r.text
    snap = _snapshot(system["id"])
    exp = _completed_experiment(system["id"], snap, "cell-1")
    imp = _full_manual_improvement(client, headers, "cell-1", target_kind="role_card",
                                   parent_cell_id=None)
    _advance(client, headers, imp, f"experiment:{exp}", version="2.0.0")
    client.post(f"/cell-fabric/improvements/{imp}/parent-approve",
                json={"actor": "p"}, headers=headers)
    client.post(f"/cell-fabric/improvements/{imp}/human-approve",
                json={"actor": "h"}, headers=headers)

    # Without allow_major_bump -> 409.
    r = client.post(f"/cell-fabric/improvements/{imp}/transition",
                    json={"new_status": "adopted"}, headers=headers)
    assert r.status_code == 409, r.text
    assert "semver-compatible" in r.json()["detail"]

    # The authenticated admin may explicitly exercise the Root override.
    r = client.post(f"/cell-fabric/improvements/{imp}/transition",
                    json={"new_status": "adopted", "allow_major_bump": True}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "adopted"


def test_non_admin_cannot_override_incompatible_role_card(client):
    root_token = _login(client)
    r = client.post(
        "/users",
        json={"username": "bob", "password": "pw", "role": "user"},
        headers=_bearer(root_token),
    )
    assert r.status_code == 201, r.text
    user_token = _login(client, "bob", "pw")
    system = _system(client, user_token, "UserMajorBump")
    headers = _headers(user_token, system["id"])
    _create_cell(
        client, headers, cell_id="cell-1",
        role_key="worker-generic", version="1.0.0",
    )
    assert _make_orchestrator(
        client, headers, "parent", ["cell-1"],
    ).status_code == 201
    r = client.post(
        "/cell-fabric/role-cards",
        json=_card_payload(role_key="worker-generic", version="2.0.0"),
        headers=headers,
    )
    assert r.status_code == 201, r.text
    snap = _snapshot(system["id"])
    exp = _completed_experiment(system["id"], snap, "cell-1")
    imp = _full_manual_improvement(
        client, headers, "cell-1", target_kind="role_card",
        parent_cell_id="parent",
    )
    _advance(client, headers, imp, f"experiment:{exp}", version="2.0.0")
    assert client.post(
        f"/cell-fabric/improvements/{imp}/parent-approve",
        json={"actor": "parent"}, headers=headers,
    ).status_code == 200
    assert client.post(
        f"/cell-fabric/improvements/{imp}/human-approve",
        json={"actor": "bob"}, headers=headers,
    ).status_code == 200

    r = client.post(
        f"/cell-fabric/improvements/{imp}/transition",
        json={"new_status": "adopted", "allow_major_bump": True},
        headers=headers,
    )
    assert r.status_code == 409, r.text
    assert "administrator Root override" in r.json()["detail"]


def test_adopt_rejects_draft_card(client):
    token = _login(client)
    system = _system(client, token, "DraftCard")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1", role_key="worker-generic", version="1.0.0")
    # A compatible-but-DRAFT candidate card.
    r = client.post("/cell-fabric/role-cards",
                    json=_card_payload(role_key="worker-generic", version="1.1.0", status="draft"),
                    headers=headers)
    assert r.status_code == 201, r.text
    snap = _snapshot(system["id"])
    exp = _completed_experiment(system["id"], snap, "cell-1")
    imp = _full_manual_improvement(client, headers, "cell-1", target_kind="role_card")
    _advance(client, headers, imp, f"experiment:{exp}", version="1.1.0")
    client.post(f"/cell-fabric/improvements/{imp}/parent-approve",
                json={"actor": "p"}, headers=headers)
    client.post(f"/cell-fabric/improvements/{imp}/human-approve",
                json={"actor": "h"}, headers=headers)
    r = client.post(f"/cell-fabric/improvements/{imp}/transition",
                    json={"new_status": "adopted"}, headers=headers)
    assert r.status_code == 409, r.text
    assert "active" in r.json()["detail"]


def test_changing_version_after_approval_invalidates_it(client):
    token = _login(client)
    system = _system(client, token, "ApprovalPin")
    headers = _headers(token, system["id"])
    _create_cell(client, headers, cell_id="cell-1", role_key="worker-generic", version="1.0.0")
    for v in ("1.1.0", "1.2.0"):
        r = client.post("/cell-fabric/role-cards",
                        json=_card_payload(role_key="worker-generic", version=v),
                        headers=headers)
        assert r.status_code == 201, r.text
    snap = _snapshot(system["id"])
    exp = _completed_experiment(system["id"], snap, "cell-1")
    imp = _full_manual_improvement(client, headers, "cell-1", target_kind="role_card")
    _advance(client, headers, imp, f"experiment:{exp}", version="1.1.0")
    client.post(f"/cell-fabric/improvements/{imp}/parent-approve",
                json={"actor": "p"}, headers=headers)
    client.post(f"/cell-fabric/improvements/{imp}/human-approve",
                json={"actor": "h"}, headers=headers)

    # Swap the proposed version at the adopt call -> approvals invalidated -> 409.
    r = client.post(f"/cell-fabric/improvements/{imp}/transition",
                    json={"new_status": "adopted", "proposed_role_card_version": "1.2.0"},
                    headers=headers)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "approval" in detail.lower()

    # The approvals were cleared on the improvement row.
    from app.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT parent_approved_by, human_approved_by FROM cell_improvements WHERE id = ?",
            (imp,),
        ).fetchone()
    assert row["parent_approved_by"] is None
    assert row["human_approved_by"] is None


# --- P1-12(a): a child roster update cannot push an ancestor past depth 3 ---


def _make_orchestrator(client, headers, cell_id, roster):
    r = client.post(
        "/cell-fabric/role-cards",
        json=_card_payload(role_key=f"rk-{cell_id}", version="1.0.0"),
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/cell-fabric/cells",
        json={
            "cell_id": cell_id, "roster": roster,
            "role_card_ref": {"role_key": f"rk-{cell_id}", "version": "1.0.0"},
            "status": "active", "mission": None, "schema_version": "1.0",
        },
        headers=headers,
    )
    return r


def test_child_roster_update_cannot_exceed_depth_via_ancestor(client):
    token = _login(client)
    system = _system(client, token, "DepthAncestor")
    headers = _headers(token, system["id"])
    # Chain A -> B -> C with C a worker leaf: A has depth 2 (valid, <= 3).
    _create_cell(client, headers, cell_id="C", role_key="rk-C")
    assert _make_orchestrator(client, headers, "B", ["C"]).status_code == 201
    assert _make_orchestrator(client, headers, "A", ["B"]).status_code == 201
    # A separate orchestrator D -> E (D has downward depth 1).
    _create_cell(client, headers, cell_id="E", role_key="rk-E")
    assert _make_orchestrator(client, headers, "D", ["E"]).status_code == 201

    # Adding D under C makes A -> B -> C -> D -> E (depth 4). The old
    # downward-only check (proposed_depth([D]) = 2) alone would not see the
    # two ancestors above C; the ancestor-aware guard rejects it.
    r = client.put(
        "/cell-fabric/cells/C/roster", json={"roster": ["D"]}, headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "depth" in r.json()["detail"].lower()


def test_legacy_improvement_event_check_is_migrated(tmp_path):
    conn = sqlite3.connect(
        tmp_path / "legacy-events.db", isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE systems (id INTEGER PRIMARY KEY);
        CREATE TABLE cell_improvements (id INTEGER PRIMARY KEY);
        INSERT INTO systems (id) VALUES (1);
        INSERT INTO cell_improvements (id) VALUES (1);
        CREATE TABLE cell_improvement_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system_id INTEGER NOT NULL,
            improvement_id INTEGER NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'created', 'status_transition', 'parent_approval',
                'human_approval', 'shadow_proposed',
                'live_shadow_approval_requested', 'live_shadow_approved',
                'suspended', 'resumed', 'rolled_back'
            )),
            from_status TEXT,
            to_status TEXT,
            actor TEXT,
            detail TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE INDEX idx_cell_improvement_events_improvement
            ON cell_improvement_events (system_id, improvement_id, id ASC);
        INSERT INTO cell_improvement_events
            (system_id, improvement_id, event_type, created_at)
            VALUES (1, 1, 'created', 1.0);
        """
    )

    try:
        db._migrate_cell_improvement_event_types(conn)
        sql = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'cell_improvement_events'"
        ).fetchone()["sql"]
        assert "approvals_invalidated" in sql
        assert conn.execute(
            "SELECT event_type FROM cell_improvement_events"
        ).fetchone()["event_type"] == "created"
        conn.execute(
            """INSERT INTO cell_improvement_events
                   (system_id, improvement_id, event_type, created_at)
               VALUES (1, 1, 'approvals_invalidated', 2.0)"""
        )
    finally:
        conn.close()


def test_legacy_ask_decision_note_is_backfilled(tmp_path):
    conn = sqlite3.connect(
        tmp_path / "legacy-asks.db", isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE cell_asks (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            decision TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO cell_asks (id, status, decision)
            VALUES (1, 'accepted', 'go ahead');
        INSERT INTO cell_asks (id, status, decision)
            VALUES (2, 'open', '');
        """
    )
    try:
        db._migrate_cell_ask_decision_note(conn)
        decided = conn.execute(
            "SELECT decision, decision_note FROM cell_asks WHERE id = 1"
        ).fetchone()
        assert dict(decided) == {
            "decision": "accepted",
            "decision_note": "go ahead",
        }
        open_ask = conn.execute(
            "SELECT decision, decision_note FROM cell_asks WHERE id = 2"
        ).fetchone()
        assert dict(open_ask) == {"decision": "", "decision_note": ""}
    finally:
        conn.close()
