"""Tests for Issue #302: Probe Cell Fabric Sub 6 -- 品質サンプリング・独立監
査・quality floor.

Covers:
1. Deterministic stratified selection: reproducible across re-runs with no
   duplicate rows, sample-rate honored against an independently computed
   hash, and a rare stratum's guaranteed minimum sample.
2. SDK/Control-Server separation: this module never imports the Python
   Probe SDK, and `packages/python-probe` is untouched by this change.
3. Auditor-model separation: the recorded audit row's auditor alias is
   independent of the worker Cell's Role Card model alias.
4. Verdict determinism: exact_match pass/fail criteria, and a component
   with no criteria at all yields `no_criteria`.
5. Explanation fail-closed: mock LLM success stores an explanation and a
   completed intelligence_runs row; a forced LLM failure still leaves the
   deterministic verdict persisted with an empty explanation and a failed
   run.
6. Blind re-audit never depends on a prior (even tampered) audit row.
7. Quality floor: a breach suspends only the offending Cell and raises
   exactly one sev1 escalation via the existing cell_tasks report path;
   recovery is refused below the floor and succeeds once it recovers.
8. Daily audit budget: a per-Cell budget gates a System-shared usage
   counter; exhaustion is a deterministic 409 with no audit performed.
9. System isolation for configs/samples/audits/intake state.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-cell-quality-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("CELL_MODEL_ALIAS_WORKER_DEFAULT", "mock:worker-mock")
    monkeypatch.setenv("CELL_MODEL_ALIAS_AUDITOR_DEFAULT", "mock:auditor-mock")
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
    r = client.post(
        "/cell-fabric/role-cards", json=_card_payload(role_key=role_key), headers=headers,
    )
    assert r.status_code in (201, 409), r.text
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


def _insert_trace(
    system_id, trace_id, component_id, *, error=None, output="ok",
    timestamp=None, input_json="{}",
):
    from app.db import get_conn

    now = timestamp if timestamp is not None else time.time()
    with get_conn() as conn:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO traces
                   (system_id, trace_id, component_id, mode, input_json,
                    output_text, error, duration_ms, timestamp)
               VALUES (?, ?, ?, 'trace', ?, ?, ?, 1.0, ?)""",
            (system_id, trace_id, component_id, input_json, output, error, now),
        )
        conn.execute("COMMIT")


def _create_criterion(client, headers, component_id, **overrides):
    payload = {
        "name": "must match",
        "description": "",
        "criterion_type": "exact_match",
        "expected_value": "ok",
        "weight": 1.0,
        "enabled": True,
    }
    payload.update(overrides)
    r = client.post(f"/components/{component_id}/criteria", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _select_samples(client, headers, cell_id, **body):
    r = client.post(
        f"/cell-fabric/cells/{cell_id}/quality-samples/select", json=body, headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _put_config(client, headers, cell_id, **overrides):
    r = client.put(f"/cell-fabric/cells/{cell_id}/quality-config", json=overrides, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _audit(client, headers, sample_id, **overrides):
    r = client.post(
        f"/cell-fabric/quality-samples/{sample_id}/audit", json=overrides, headers=headers,
    )
    return r


# ---------------------------------------------------------------------------
# 1. Deterministic stratified selection
# ---------------------------------------------------------------------------


def test_selection_is_deterministic_and_idempotent(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "sel-sys")
    headers = _headers(token, system["id"])
    cell_id = _create_cell(admin_client, headers)

    for i in range(30):
        _insert_trace(system["id"], f"t{i}", cell_id, error=("err" if i % 7 == 0 else None))

    _put_config(admin_client, headers, cell_id, sample_rate=0.3)

    first = _select_samples(admin_client, headers, cell_id)
    second = _select_samples(admin_client, headers, cell_id)

    first_ids = sorted(s["id"] for s in first["samples"])
    second_ids = sorted(s["id"] for s in second["samples"])
    assert first_ids == second_ids
    assert len(first_ids) == len(set(first_ids))

    listed = admin_client.get(f"/cell-fabric/cells/{cell_id}/quality-samples", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()["samples"]) == len(first_ids)


def test_selection_rate_matches_independent_hash_computation(admin_client):
    from app.cell_quality import selection_fraction

    token = _login(admin_client)
    system = _create_system(admin_client, token, "hash-sys")
    headers = _headers(token, system["id"])
    cell_id = _create_cell(admin_client, headers)

    trace_ids = [f"trace-{i}" for i in range(100)]
    for trace_id in trace_ids:
        _insert_trace(system["id"], trace_id, cell_id)

    _put_config(admin_client, headers, cell_id, sample_rate=0.2)

    # Resolve the numeric cell_definition_id the same way the core module
    # does, so the test's hash computation matches exactly.
    detail = admin_client.get(f"/cell-fabric/cells/{cell_id}", headers=headers)
    assert detail.status_code == 200
    cell_definition_id = detail.json()["definition"]["id"]

    expected = {
        trace_id for trace_id in trace_ids
        if selection_fraction(system["id"], cell_definition_id, trace_id) < 0.2
    }

    result = _select_samples(admin_client, headers, cell_id)
    got = {s["target_id"] for s in result["samples"]}
    assert got == expected


def test_rare_stratum_guarantees_minimum_one_sample(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "rare-sys")
    headers = _headers(token, system["id"])
    cell_id = _create_cell(admin_client, headers)

    _insert_trace(system["id"], "only-error", cell_id, error="boom")
    for i in range(5):
        _insert_trace(system["id"], f"ok-{i}", cell_id)

    _put_config(
        admin_client, headers, cell_id,
        sample_rate=0.0,
        strata=[{"name": "errors", "risk": "error", "rare": True}],
    )

    result = _select_samples(admin_client, headers, cell_id)
    got = {s["target_id"] for s in result["samples"]}
    assert got == {"only-error"}


def test_task_type_stratum_matches_trace_input_metadata(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "task-type-sys")
    headers = _headers(token, system["id"])
    cell_id = _create_cell(admin_client, headers)

    _insert_trace(
        system["id"], "api-task", cell_id,
        input_json='{"metadata":{"task_type":"api"}}',
    )
    _insert_trace(
        system["id"], "batch-task", cell_id,
        input_json='{"metadata":{"task_type":"batch"}}',
    )
    _put_config(
        admin_client, headers, cell_id,
        sample_rate=0.0,
        strata=[{"name": "api", "task_type": "api", "rare": True}],
    )

    result = _select_samples(admin_client, headers, cell_id)
    assert {sample["target_id"] for sample in result["samples"]} == {"api-task"}
    assert result["samples"][0]["stratum"] == "api"


# ---------------------------------------------------------------------------
# 2. SDK / Control-Server separation
# ---------------------------------------------------------------------------


def test_cell_quality_never_imports_the_sdk_and_sdk_is_untouched():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "apps" / "control-server" / "app" / "cell_quality.py"
    import_lines = [
        line for line in module_path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    for line in import_lines:
        assert "python_probe" not in line
        assert "probe_sdk" not in line

    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "packages/python-probe"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# 3. Auditor-model separation
# ---------------------------------------------------------------------------


def test_audit_records_auditor_alias_distinct_from_worker_alias(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "auditor-sys")
    headers = _headers(token, system["id"])
    cell_id = _create_cell(admin_client, headers)  # model_alias='worker-default'
    _put_config(admin_client, headers, cell_id, sample_rate=1.0)
    _insert_trace(system["id"], "t1", cell_id)
    result = _select_samples(admin_client, headers, cell_id)
    sample_id = result["samples"][0]["id"]

    r = _audit(admin_client, headers, sample_id, auditor_alias="auditor-default")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["auditor_model_alias"] == "auditor-default"
    assert body["auditor_model_alias"] != "worker-default"


# ---------------------------------------------------------------------------
# 4. Verdict determinism
# ---------------------------------------------------------------------------


def test_verdict_pass_fail_and_no_criteria(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "verdict-sys")
    headers = _headers(token, system["id"])
    cell_pass = _create_cell(admin_client, headers, cell_id="cell-pass")
    cell_fail = _create_cell(admin_client, headers, cell_id="cell-fail", role_key="worker-generic")
    cell_none = _create_cell(admin_client, headers, cell_id="cell-none", role_key="worker-generic")

    _create_criterion(admin_client, headers, cell_pass, expected_value="ok")
    _create_criterion(admin_client, headers, cell_fail, expected_value="ok")

    _insert_trace(system["id"], "tp", cell_pass, output="ok")
    _insert_trace(system["id"], "tf", cell_fail, output="not-ok")
    _insert_trace(system["id"], "tn", cell_none, output="whatever")

    sample_pass = _select_samples(admin_client, headers, cell_pass, window_seconds=None)
    sample_fail = _select_samples(admin_client, headers, cell_fail, window_seconds=None)
    sample_none = _select_samples(admin_client, headers, cell_none, window_seconds=None)

    # Force full selection regardless of the default sample_rate.
    _put_config(admin_client, headers, cell_pass, sample_rate=1.0)
    _put_config(admin_client, headers, cell_fail, sample_rate=1.0)
    _put_config(admin_client, headers, cell_none, sample_rate=1.0)
    sample_pass = _select_samples(admin_client, headers, cell_pass)
    sample_fail = _select_samples(admin_client, headers, cell_fail)
    sample_none = _select_samples(admin_client, headers, cell_none)

    id_pass = sample_pass["samples"][0]["id"]
    id_fail = sample_fail["samples"][0]["id"]
    id_none = sample_none["samples"][0]["id"]

    r_pass = _audit(admin_client, headers, id_pass)
    r_fail = _audit(admin_client, headers, id_fail)
    r_none = _audit(admin_client, headers, id_none)
    assert r_pass.status_code == 201
    assert r_fail.status_code == 201
    assert r_none.status_code == 201

    assert r_pass.json()["verdict"] == "pass"
    assert r_pass.json()["failed_criteria"] == []
    assert r_fail.json()["verdict"] == "fail"
    assert len(r_fail.json()["failed_criteria"]) == 1
    assert r_none.json()["verdict"] == "no_criteria"
    for body in (r_pass.json(), r_fail.json(), r_none.json()):
        assert body["verdict_decision_method"] == "deterministic"


# ---------------------------------------------------------------------------
# 5. Explanation fail-closed
# ---------------------------------------------------------------------------


def test_explanation_stored_on_fail_verdict_with_mock_llm(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "explain-ok-sys")
    headers = _headers(token, system["id"])
    cell_id = _create_cell(admin_client, headers)
    _create_criterion(admin_client, headers, cell_id, expected_value="ok")
    _put_config(admin_client, headers, cell_id, sample_rate=1.0)
    _insert_trace(system["id"], "t1", cell_id, output="wrong")
    result = _select_samples(admin_client, headers, cell_id)
    sample_id = result["samples"][0]["id"]

    r = _audit(admin_client, headers, sample_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["verdict"] == "fail"
    assert body["explanation"] != ""
    assert body["is_mock"] is True
    assert body["explanation_run_id"] is not None

    from app.db import get_conn

    with get_conn() as conn:
        run_row = conn.execute(
            "SELECT status, run_type, decision_method FROM intelligence_runs WHERE id = ?",
            (body["explanation_run_id"],),
        ).fetchone()
    assert run_row["status"] == "completed"
    assert run_row["run_type"] == "cell_quality_audit"
    assert run_row["decision_method"] == "reasoning_llm"


def test_explanation_failure_still_persists_deterministic_verdict(admin_client, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "explain-fail-sys")
    headers = _headers(token, system["id"])
    cell_id = _create_cell(admin_client, headers)
    _create_criterion(admin_client, headers, cell_id, expected_value="ok")
    _put_config(admin_client, headers, cell_id, sample_rate=1.0)
    _insert_trace(system["id"], "t1", cell_id, output="wrong")
    result = _select_samples(admin_client, headers, cell_id)
    sample_id = result["samples"][0]["id"]

    import app.cell_quality as cell_quality_module
    from app.llm import LLMError

    class _BoomClient:
        def generate_text(self, *args, **kwargs):
            raise LLMError("forced failure for test")

    monkeypatch.setattr(cell_quality_module, "create_llm_client", lambda config: _BoomClient())

    r = _audit(admin_client, headers, sample_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["verdict"] == "fail"
    assert body["explanation"] == ""
    assert body["explanation_run_id"] is not None

    from app.db import get_conn

    with get_conn() as conn:
        run_row = conn.execute(
            "SELECT status, error_details FROM intelligence_runs WHERE id = ?",
            (body["explanation_run_id"],),
        ).fetchone()
    assert run_row["status"] == "failed"
    assert run_row["error_details"]


# ---------------------------------------------------------------------------
# 6. Blind re-audit never depends on a prior audit row
# ---------------------------------------------------------------------------


def test_blind_audit_ignores_prior_tampered_audit_row(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "blind-sys")
    headers = _headers(token, system["id"])
    cell_id = _create_cell(admin_client, headers)
    _create_criterion(admin_client, headers, cell_id, expected_value="ok")
    _put_config(admin_client, headers, cell_id, sample_rate=1.0)
    _insert_trace(system["id"], "t1", cell_id, output="wrong")  # deterministically 'fail'
    result = _select_samples(admin_client, headers, cell_id)
    sample_id = result["samples"][0]["id"]

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO cell_quality_audits
                   (system_id, sample_id, auditor_model_alias, verdict,
                    verdict_decision_method, is_blind, failed_criteria_json,
                    verbatim_example, explanation, explanation_run_id, created_at)
               VALUES (?, ?, 'tamper', 'pass', 'deterministic', 0, '[]', '', '', NULL, ?)""",
            (system["id"], sample_id, now),
        )
        conn.execute("COMMIT")

    r = _audit(admin_client, headers, sample_id, blind=True)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_blind"] is True
    # The deterministic recomputation must still say 'fail', proving the
    # blind audit did not defer to the tampered 'pass' row.
    assert body["verdict"] == "fail"


# ---------------------------------------------------------------------------
# 7. Quality floor
# ---------------------------------------------------------------------------


def _insert_audit_row(system_id, sample_id, verdict, *, is_blind=0):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute("BEGIN")
        conn.execute(
            """INSERT INTO cell_quality_audits
                   (system_id, sample_id, auditor_model_alias, verdict,
                    verdict_decision_method, is_blind, failed_criteria_json,
                    verbatim_example, explanation, explanation_run_id, created_at)
               VALUES (?, ?, 'auditor-default', ?, 'deterministic', ?, '[]', '', '', NULL, ?)""",
            (system_id, sample_id, verdict, is_blind, now),
        )
        conn.execute("COMMIT")


def test_quality_floor_breach_suspends_only_that_cell_and_escalates(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token, "floor-sys")
    headers = _headers(token, system["id"])
    bad_cell = _create_cell(admin_client, headers, cell_id="bad-cell")
    good_cell = _create_cell(admin_client, headers, cell_id="good-cell", role_key="worker-generic")

    _put_config(admin_client, headers, bad_cell, floor_window=10, quality_floor=0.7)

    for i in range(6):
        _insert_trace(system["id"], f"bad-{i}", bad_cell)
    result = _select_samples(admin_client, headers, bad_cell, window_seconds=None)
    _put_config(admin_client, headers, bad_cell, sample_rate=1.0)
    result = _select_samples(admin_client, headers, bad_cell)
    sample_ids = [s["id"] for s in result["samples"]]
    assert len(sample_ids) >= 6

    # 1 pass, 5 fail => pass_rate ~0.167 < floor 0.7, denominator >= 5.
    _insert_audit_row(system["id"], sample_ids[0], "pass")
    for sid in sample_ids[1:6]:
        _insert_audit_row(system["id"], sid, "fail")

    r = admin_client.post(
        f"/cell-fabric/cells/{bad_cell}/quality-floor/evaluate", headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suspended"] is True
    assert body["escalation_id"] is not None
    assert body["pass_rate"] < 0.7

    intake = admin_client.get(f"/cell-fabric/cells/{bad_cell}/intake-state", headers=headers)
    assert intake.json()["intake_status"] == "suspended"

    other_intake = admin_client.get(f"/cell-fabric/cells/{good_cell}/intake-state", headers=headers)
    assert other_intake.json()["intake_status"] == "accepting"

    esc = admin_client.get("/cell-fabric/escalations", headers=headers)
    assert esc.status_code == 200
    escalations = [e for e in esc.json()["escalations"] if e["id"] == body["escalation_id"]]
    assert len(escalations) == 1
    assert escalations[0]["severity"] == "sev1"
    assert escalations[0]["cell_definition_id"] == bad_cell

    # Recovery refused while still below floor.
    resume = admin_client.post(
        f"/cell-fabric/cells/{bad_cell}/intake/resume", json={"reason": "too soon"}, headers=headers,
    )
    assert resume.status_code == 409

    # Push enough passing audits to recover.
    for i in range(6, 6 + 8):
        _insert_trace(system["id"], f"bad-{i}", bad_cell)
    result2 = _select_samples(admin_client, headers, bad_cell)
    new_sample_ids = [s["id"] for s in result2["samples"] if s["id"] not in sample_ids]
    for sid in new_sample_ids:
        _insert_audit_row(system["id"], sid, "pass")

    resume2 = admin_client.post(
        f"/cell-fabric/cells/{bad_cell}/intake/resume", json={"reason": "recovered"}, headers=headers,
    )
    assert resume2.status_code == 200, resume2.text
    assert resume2.json()["intake_status"] == "accepting"


# ---------------------------------------------------------------------------
# 8. Daily audit budget
# ---------------------------------------------------------------------------


def test_daily_audit_budget_gates_and_is_system_scoped(admin_client):
    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "budget-sys-a")
    system_b = _create_system(admin_client, token, "budget-sys-b")
    headers_a = _headers(token, system_a["id"])
    headers_b = _headers(token, system_b["id"])
    cell_a = _create_cell(admin_client, headers_a, cell_id="cell-a")
    cell_b = _create_cell(admin_client, headers_b, cell_id="cell-b")

    _put_config(admin_client, headers_a, cell_a, daily_audit_budget=1, sample_rate=1.0)
    _put_config(admin_client, headers_b, cell_b, daily_audit_budget=1, sample_rate=1.0)

    _insert_trace(system_a["id"], "a1", cell_a)
    _insert_trace(system_a["id"], "a2", cell_a)
    _insert_trace(system_b["id"], "b1", cell_b)

    result_a = _select_samples(admin_client, headers_a, cell_a)
    result_b = _select_samples(admin_client, headers_b, cell_b)
    sample_ids_a = [s["id"] for s in result_a["samples"]]
    sample_id_b = result_b["samples"][0]["id"]

    r1 = _audit(admin_client, headers_a, sample_ids_a[0])
    assert r1.status_code == 201, r1.text
    r2 = _audit(admin_client, headers_a, sample_ids_a[1])
    assert r2.status_code == 409

    # System B's own budget is untouched by System A's usage.
    rb = _audit(admin_client, headers_b, sample_id_b)
    assert rb.status_code == 201, rb.text


# ---------------------------------------------------------------------------
# 9. System isolation
# ---------------------------------------------------------------------------


def test_system_isolation_for_config_samples_audits_intake(admin_client):
    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "iso-sys-a")
    system_b = _create_system(admin_client, token, "iso-sys-b")
    headers_a = _headers(token, system_a["id"])
    headers_b = _headers(token, system_b["id"])
    cell_id = "shared-name-cell"
    _create_cell(admin_client, headers_a, cell_id=cell_id)
    _create_cell(admin_client, headers_b, cell_id=cell_id)

    _put_config(admin_client, headers_a, cell_id, sample_rate=0.9)
    config_b = admin_client.get(f"/cell-fabric/cells/{cell_id}/quality-config", headers=headers_b)
    assert config_b.status_code == 200
    assert config_b.json()["sample_rate"] != 0.9

    _insert_trace(system_a["id"], "ta1", cell_id)
    result_a = _select_samples(admin_client, headers_a, cell_id)
    assert len(result_a["samples"]) >= 1

    samples_b = admin_client.get(f"/cell-fabric/cells/{cell_id}/quality-samples", headers=headers_b)
    assert samples_b.status_code == 200
    assert samples_b.json()["samples"] == []

    intake_a = admin_client.get(f"/cell-fabric/cells/{cell_id}/intake-state", headers=headers_a)
    intake_b = admin_client.get(f"/cell-fabric/cells/{cell_id}/intake-state", headers=headers_b)
    assert intake_a.status_code == 200
    assert intake_b.status_code == 200
