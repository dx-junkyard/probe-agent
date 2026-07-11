"""Tests for Issue #86: System Understanding unified API.

Covers: GET /repository/system-understanding and POST /repository/system-understanding/build.
"""

import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-su-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


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


def _build_and_wait(client, hdrs, timeout=10.0):
    """Trigger a system understanding build and poll until it settles.

    Issue #106: the build endpoint is asynchronous (returns 202 immediately
    with a build id) so tests must poll the build-status endpoint instead of
    expecting the aggregated result inline.
    """
    r = client.post("/repository/system-understanding/build", headers=hdrs)
    assert r.status_code == 202, r.text
    build = r.json()
    build_id = build["id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        status_r = client.get(
            f"/repository/system-understanding/build/{build_id}", headers=hdrs
        )
        assert status_r.status_code == 200, status_r.text
        build = status_r.json()
        if build["status"] in ("completed", "partial", "failed", "cancelled"):
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"Build {build_id} did not settle within {timeout}s: {build}")

    return build


def _init_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    readme = repo / "README.md"
    readme.write_text("# Test Project\nA test project for system understanding.\n")
    src = repo / "src"
    src.mkdir()
    main_py = src / "main.py"
    main_py.write_text(
        'from fastapi import APIRouter\n\nrouter = APIRouter()\n\n'
        '@router.get("/items")\ndef list_items():\n    """List all items."""\n    return []\n\n'
        '@router.post("/items")\ndef create_item(data: dict):\n    """Create a new item."""\n    return data\n'
    )
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo), check=True, capture_output=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


class TestSystemUnderstandingGetWithoutSnapshot:
    def test_returns_missing_steps(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "test-sys")
        hdrs = _headers(token, sys["id"])

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        assert data["system_id"] == sys["id"]
        assert data["snapshot_id"] is None
        assert data["commit_sha"] is None

        pipeline = {s["step"]: s["status"] for s in data["pipeline"]}
        assert pipeline["repository_configured"] == "missing"
        assert pipeline["snapshot_ready"] == "missing"
        assert pipeline["symbols_indexed"] == "missing"

        assert len(data["next_actions"]) > 0
        assert data["next_actions"][0]["action"] == "Configure repository"

    def test_primary_action_matches_configure_repository_next_action(self, admin_client, tmp_path):
        """Issue #201: GET /repository/system-understanding exposes primary_action,
        matching the first next_action (Configure repository, action_kind=navigate)
        when no repository is configured yet."""
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "test-sys-primary")
        hdrs = _headers(token, sys["id"])

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        assert data["primary_action"] is not None
        assert data["primary_action"]["action"] == "Configure repository"
        assert data["primary_action"]["action_kind"] == "navigate"
        assert data["primary_action"] == data["next_actions"][0]

    def test_returns_missing_snapshot_after_config(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "test-sys-2")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        pipeline = {s["step"]: s["status"] for s in data["pipeline"]}
        assert pipeline["repository_configured"] == "complete"
        assert pipeline["snapshot_ready"] == "missing"


class TestSystemUnderstandingBuild:
    def test_build_uses_existing_snapshot_and_symbols(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "build-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap_r = admin_client.post(
            "/repository/snapshots",
            json={"commit_sha": sha},
            headers=hdrs,
        )
        assert snap_r.status_code == 201

        build = _build_and_wait(admin_client, hdrs)
        # Deterministic steps complete, but the reasoning steps stay blocked
        # (mock provider), so the job settles as partial — not completed.
        assert build["status"] == "partial"

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        assert data["snapshot_id"] is not None
        assert data["commit_sha"] == sha

        pipeline = {s["step"]: s["status"] for s in data["pipeline"]}
        assert pipeline["repository_configured"] == "complete"
        assert pipeline["snapshot_ready"] == "complete"
        assert pipeline["symbols_indexed"] == "complete"
        assert pipeline["entrypoints_discovered"] == "complete"


class TestSystemUnderstandingReportsReasoningModelBlocked:
    def test_reasoning_steps_not_heuristic_fallback(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "reasoning-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots",
            json={"commit_sha": sha},
            headers=hdrs,
        )

        _build_and_wait(admin_client, hdrs)
        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        pipeline = {s["step"]: s["status"] for s in data["pipeline"]}
        # Documentation indexing is deterministic; claim scanning is the first
        # documentation step that requires a reasoning model.
        assert pipeline["documentation_indexed"] == "complete"
        assert pipeline["documentation_claims_scanned"] == "blocked"
        # Capability hierarchy has a deterministic base that runs without reasoning.
        # Issue #210: the fixture repo carries no `probe-agent:` docstring
        # metadata, so the run completes with zero capability nodes, which is
        # reported as "warning" (not silently "complete") rather than blocked
        # on the (unrelated) reasoning-model requirement.
        assert pipeline["capability_hierarchy_ready"] in ("warning", "blocked")

    def test_documentation_indexed_reflects_build_step_not_draft_generation(
        self, admin_client, tmp_path
    ):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "doc-index-step-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots",
            json={"commit_sha": sha},
            headers=hdrs,
        )

        build = _build_and_wait(admin_client, hdrs)
        steps = {s["step"]: s for s in build["steps"]}
        assert steps["documentation_index"]["status"] == "completed"

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        pipeline = {s["step"]: s["status"] for s in r.json()["pipeline"]}
        assert pipeline["documentation_indexed"] == "complete"


class TestCapabilityHierarchyReadyStatus:
    """Issue #210: capability_hierarchy_ready must not report "complete" for a
    completed run that produced zero capability nodes (no `probe-agent:`
    docstring metadata found in the target repo), since that used to
    contradict the SystemStateBanner's "generate the capability hierarchy"
    warning for the same build.
    """

    def _setup_with_snapshot(self, admin_client, tmp_path, name):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, name)
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )
        assert snap.status_code == 201, snap.text
        return sys, hdrs, snap.json()["id"]

    def _insert_capability_run(self, system_id, snapshot_id, status):
        from app.db import get_conn

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'capability_hierarchy', 'deterministic', 'none',
                           'v1', 'v1', 'deterministic', ?, 0, 0, 0)""",
                (system_id, snapshot_id, status),
            )
            return cur.lastrowid

    def _insert_capability_node(self, system_id, snapshot_id, run_id):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO capability_hierarchy_nodes
                       (system_id, snapshot_id, intelligence_run_id, node_type, name, created_at)
                   VALUES (?, ?, ?, 'capability', 'Some capability', 0)""",
                (system_id, snapshot_id, run_id),
            )

    def test_no_run_and_reasoning_available_is_missing(self, admin_client, tmp_path, monkeypatch):
        """Regression: no run + reasoning available -> plain "missing"."""
        monkeypatch.setattr(
            "app.system_understanding_service._is_reasoning_model_available",
            lambda: True,
        )
        sys, hdrs, snapshot_id = self._setup_with_snapshot(admin_client, tmp_path, "chr-no-run-sys")
        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        pipeline = {s["step"]: s["status"] for s in r.json()["pipeline"]}
        assert pipeline["capability_hierarchy_ready"] == "missing"

    def test_no_run_and_reasoning_unavailable_is_blocked(self, admin_client, tmp_path):
        """Regression: no run + no reasoning model configured -> "blocked"
        (the admin_client fixture defaults to LLM_PROVIDER=mock)."""
        sys, hdrs, snapshot_id = self._setup_with_snapshot(admin_client, tmp_path, "chr-no-run-blocked-sys")
        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        pipeline = {s["step"]: s["status"] for s in r.json()["pipeline"]}
        assert pipeline["capability_hierarchy_ready"] == "blocked"

    def test_completed_with_zero_capabilities_is_warning(self, admin_client, tmp_path):
        sys, hdrs, snapshot_id = self._setup_with_snapshot(admin_client, tmp_path, "chr-zero-sys")
        self._insert_capability_run(sys["id"], snapshot_id, "completed")

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        pipeline = {s["step"]: s for s in r.json()["pipeline"]}
        step = pipeline["capability_hierarchy_ready"]
        assert step["status"] == "warning"
        assert "probe-agent" in step["detail"]

    def test_completed_with_at_least_one_capability_is_complete(self, admin_client, tmp_path):
        sys, hdrs, snapshot_id = self._setup_with_snapshot(admin_client, tmp_path, "chr-nonzero-sys")
        run_id = self._insert_capability_run(sys["id"], snapshot_id, "completed")
        self._insert_capability_node(sys["id"], snapshot_id, run_id)

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        pipeline = {s["step"]: s["status"] for s in r.json()["pipeline"]}
        assert pipeline["capability_hierarchy_ready"] == "complete"

        # And system_state must not surface a capability-hierarchy item for a
        # genuinely completed, non-empty run.
        state_r = admin_client.get("/system-state", headers=hdrs)
        assert state_r.status_code == 200, state_r.text
        state_ids = [i["state_id"] for i in state_r.json()["items"]]
        assert not any(s.startswith("pipeline.capability_hierarchy.") for s in state_ids)


class TestSystemUnderstandingReportsMetadataCoverage:
    def test_metadata_coverage_returned(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "meta-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots",
            json={"commit_sha": sha},
            headers=hdrs,
        )

        _build_and_wait(admin_client, hdrs)
        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        mc = data["metadata_coverage"]
        assert mc is not None
        assert mc["symbol_count"] >= 0
        assert mc["symbols_with_source_metadata"] >= 0
        assert mc["entrypoint_count"] >= 0
        assert mc["entrypoints_with_capability_link"] >= 0


class TestSystemUnderstandingNextActions:
    def test_next_actions_are_deterministic(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "actions-sys")
        hdrs = _headers(token, sys["id"])

        r1 = admin_client.get("/repository/system-understanding", headers=hdrs)
        r2 = admin_client.get("/repository/system-understanding", headers=hdrs)

        assert r1.status_code == 200
        assert r2.status_code == 200

        assert r1.json()["next_actions"] == r2.json()["next_actions"]

    def test_next_actions_change_with_state(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "actions-sys-2")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        r_before = admin_client.get("/repository/system-understanding", headers=hdrs)
        before_actions = [a["action"] for a in r_before.json()["next_actions"]]
        assert "Configure repository" in before_actions

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )

        r_after = admin_client.get("/repository/system-understanding", headers=hdrs)
        after_actions = [a["action"] for a in r_after.json()["next_actions"]]
        assert "Configure repository" not in after_actions
        assert "Create snapshot" in after_actions


class TestNextActionsSpanPlanAndExperimentStatus:
    """Issue #174: Next Actions must surface probe plan / experiment status,
    not just the System Understanding pipeline."""

    def _setup_system(self, admin_client, tmp_path, name):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, name)
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap_r = admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )
        snapshot_id = snap_r.json()["id"]
        return sys["id"], hdrs, snapshot_id

    def _insert_intelligence_run(self, system_id, snapshot_id):
        from app.db import get_conn

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'probe_plan', 'mock', 'mock-model', 'v1', 'v1',
                           'reasoning_llm', 'completed', 1, 0, 0)""",
                (system_id, snapshot_id),
            )
            return cur.lastrowid

    def test_proposed_plan_surfaces_review_action(self, admin_client, tmp_path):
        from app.db import get_conn

        system_id, hdrs, snapshot_id = self._setup_system(
            admin_client, tmp_path, "plan-review-sys"
        )
        run_id = self._insert_intelligence_run(system_id, snapshot_id)
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id, feature_id,
                        objective, status, origin, created_at, updated_at)
                   VALUES (?, ?, ?, 'feat-1', 'obj', 'proposed', 'manual', 0, 0)""",
                (system_id, snapshot_id, run_id),
            )
            plan_id = cur.lastrowid

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        matching = [
            a for a in r.json()["next_actions"] if a["action"] == "Review probe plan"
        ]
        assert len(matching) == 1
        assert matching[0]["category"] == "observe"
        assert matching[0]["link"] == f"/probe-planner?plan={plan_id}"

    def test_approved_plan_without_validated_patch_surfaces_instrument_action(
        self, admin_client, tmp_path
    ):
        from app.db import get_conn

        system_id, hdrs, snapshot_id = self._setup_system(
            admin_client, tmp_path, "plan-patch-sys"
        )
        run_id = self._insert_intelligence_run(system_id, snapshot_id)
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id, feature_id,
                        objective, status, origin, created_at, updated_at)
                   VALUES (?, ?, ?, 'feat-1', 'obj', 'approved', 'manual', 0, 0)""",
                (system_id, snapshot_id, run_id),
            )
            plan_id = cur.lastrowid

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        matching = [
            a for a in r.json()["next_actions"]
            if a["action"] == "Generate / validate probe patch"
        ]
        assert len(matching) == 1
        assert matching[0]["category"] == "instrument"
        assert matching[0]["link"] == f"/probe-planner?plan={plan_id}"

    def test_approved_plan_with_validated_patch_has_no_pending_action(
        self, admin_client, tmp_path
    ):
        from app.db import get_conn

        system_id, hdrs, snapshot_id = self._setup_system(
            admin_client, tmp_path, "plan-validated-sys"
        )
        run_id = self._insert_intelligence_run(system_id, snapshot_id)
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id, feature_id,
                        objective, status, origin, created_at, updated_at)
                   VALUES (?, ?, ?, 'feat-1', 'obj', 'approved', 'manual', 0, 0)""",
                (system_id, snapshot_id, run_id),
            )
            plan_id = cur.lastrowid
            cur = conn.execute(
                """INSERT INTO probe_patches
                       (plan_id, system_id, snapshot_id, commit_sha, diff,
                        skipped, status, cleanup_state, created_at)
                   VALUES (?, ?, ?, 'deadbeef', 'diff', '[]', 'generated',
                           'not_attempted', 0)""",
                (plan_id, system_id, snapshot_id),
            )
            patch_id = cur.lastrowid
            for variant in ("baseline", "probed"):
                conn.execute(
                    """INSERT INTO validation_runs
                           (patch_id, system_id, variant, worktree_path,
                            overall_success, trace_status, network_isolation,
                            cleanup_state, created_at)
                       VALUES (?, ?, ?, '/tmp/x', 1, 'not_checked',
                               'not_requested', 'not_attempted', 0)""",
                    (patch_id, system_id, variant),
                )

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        labels = [a["action"] for a in r.json()["next_actions"]]
        assert "Generate / validate probe patch" not in labels

    def test_completed_undecided_experiment_surfaces_evaluate_action(
        self, admin_client, tmp_path
    ):
        from app.db import get_conn

        system_id, hdrs, snapshot_id = self._setup_system(
            admin_client, tmp_path, "experiment-decision-sys"
        )
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO experiments
                       (system_id, feature_id, objective, snapshot_id,
                        baseline_commit, config_revision, execution_config,
                        status, human_decision, created_at)
                   VALUES (?, 'feat-1', 'obj', ?, 'deadbeef', 'v1', '{}',
                           'completed', 'undecided', 0)""",
                (system_id, snapshot_id),
            )

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        matching = [
            a for a in r.json()["next_actions"]
            if a["action"] == "Review experiment decision"
        ]
        assert len(matching) == 1
        assert matching[0]["category"] == "evaluate"
        assert matching[0]["link"] == "/experiments"

    def test_decided_experiment_has_no_pending_action(self, admin_client, tmp_path):
        from app.db import get_conn

        system_id, hdrs, snapshot_id = self._setup_system(
            admin_client, tmp_path, "experiment-decided-sys"
        )
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO experiments
                       (system_id, feature_id, objective, snapshot_id,
                        baseline_commit, config_revision, execution_config,
                        status, human_decision, created_at)
                   VALUES (?, 'feat-1', 'obj', ?, 'deadbeef', 'v1', '{}',
                           'completed', 'adopted', 0)""",
                (system_id, snapshot_id),
            )

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        labels = [a["action"] for a in r.json()["next_actions"]]
        assert "Review experiment decision" not in labels


class TestPipelineStepStatuses:
    def test_all_pipeline_steps_present(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "pipeline-sys")
        hdrs = _headers(token, sys["id"])

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        step_names = [s["step"] for s in data["pipeline"]]
        expected = [
            "repository_configured",
            "snapshot_ready",
            "documentation_indexed",
            "documentation_claims_scanned",
            "symbols_indexed",
            "entrypoints_discovered",
            "docs_code_reconciled",
            "capability_hierarchy_ready",
        ]
        assert step_names == expected

    def test_step_statuses_are_valid(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "valid-sys")
        hdrs = _headers(token, sys["id"])

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        data = r.json()

        valid_statuses = {"complete", "missing", "warning", "blocked", "failed"}
        for step in data["pipeline"]:
            assert step["status"] in valid_statuses, f"Invalid status for {step['step']}: {step['status']}"


class TestGapWorklist:
    def test_gaps_include_structured_fields(self, admin_client, tmp_path):
        """After build, gaps should include severity, title, and next_actions."""
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "gap-struct-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots",
            json={"commit_sha": sha},
            headers=hdrs,
        )

        _build_and_wait(admin_client, hdrs)
        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        for gap in data["gaps"]:
            assert "gap_type" in gap
            assert "severity" in gap
            assert gap["severity"] in ("info", "warning", "error")
            assert "title" in gap
            assert "next_actions" in gap
            assert isinstance(gap["next_actions"], list)
            assert "doc_refs" in gap
            assert "symbol_refs" in gap
            assert "entrypoint_refs" in gap

    def test_gap_next_actions_are_deterministic(self, admin_client, tmp_path):
        """Same state should produce same gap next_actions."""
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "gap-det-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots",
            json={"commit_sha": sha},
            headers=hdrs,
        )

        _build_and_wait(admin_client, hdrs)
        r1 = admin_client.get("/repository/system-understanding", headers=hdrs)
        r2 = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r1.status_code == 200
        assert r2.status_code == 200

        gaps1 = r1.json()["gaps"]
        gaps2 = r2.json()["gaps"]
        actions1 = [
            [a["action"] for a in g["next_actions"]]
            for g in gaps1
        ]
        actions2 = [
            [a["action"] for a in g["next_actions"]]
            for g in gaps2
        ]
        assert actions1 == actions2

    def test_unclassified_entrypoints_detected(self, admin_client, tmp_path):
        """Entrypoints without capability links should appear as unclassified_entrypoint gaps."""
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "gap-unclass-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots",
            json={"commit_sha": sha},
            headers=hdrs,
        )

        _build_and_wait(admin_client, hdrs)
        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        ep_count = data["metadata_coverage"]["entrypoint_count"]
        ep_linked = data["metadata_coverage"]["entrypoints_with_capability_link"]
        unclassified_gaps = [g for g in data["gaps"] if g["gap_type"] == "unclassified_entrypoint"]

        if ep_count > 0 and ep_linked == 0:
            assert len(unclassified_gaps) > 0
            for ug in unclassified_gaps:
                assert ug["severity"] == "info"
                assert any(a["action"] == "Open Interview" for a in ug["next_actions"])

    def test_no_gaps_returns_empty_list(self, admin_client, tmp_path):
        """When no snapshot exists, gaps should be an empty list."""
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "gap-empty-sys")
        hdrs = _headers(token, sys["id"])

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        assert data["gaps"] == []
        assert data["gap_summary"] == []


class TestIntelligenceRunStatusContract:
    """Regression tests: intelligence_runs.status must stay within the shared
    schema vocabulary ('pending' / 'completed' / 'failed'). Build previously
    wrote 'success', which broke IntelligenceRunOut serialization downstream."""

    def _build(self, admin_client, tmp_path, name):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, name)
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )
        build = _build_and_wait(admin_client, hdrs)
        # Reasoning steps stay blocked with the mock provider, so the job is
        # partial while every deterministic artifact is still persisted.
        assert build["status"] == "partial"
        return hdrs

    def test_build_writes_contract_statuses_only(self, admin_client, tmp_path):
        self._build(admin_client, tmp_path, "status-contract-sys")
        from app.db import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT status FROM intelligence_runs"
            ).fetchall()
        statuses = {row["status"] for row in rows}
        assert statuses <= {"pending", "completed", "failed"}, statuses

    def test_hierarchy_endpoints_work_after_build(self, admin_client, tmp_path):
        hdrs = self._build(admin_client, tmp_path, "post-build-sys")

        hierarchy = admin_client.get(
            "/repository/capability-hierarchy", headers=hdrs
        )
        assert hierarchy.status_code == 200, hierarchy.text
        assert hierarchy.json()["intelligence_run"]["status"] == "completed"

        drift = admin_client.get(
            "/repository/capability-hierarchy/drift", headers=hdrs
        )
        assert drift.status_code == 200, drift.text

        cards = admin_client.get("/repository/api-role-cards", headers=hdrs)
        assert cards.status_code == 200, cards.text

    def test_symbols_indexed_complete_after_explicit_index(
        self, admin_client, tmp_path
    ):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "explicit-index-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )
        index_r = admin_client.post("/repository/symbols/index", headers=hdrs)
        assert index_r.status_code in (200, 201), index_r.text

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200
        pipeline = {s["step"]: s["status"] for s in r.json()["pipeline"]}
        assert pipeline["symbols_indexed"] == "complete"

    def test_init_db_repairs_legacy_success_rows(self, admin_client, tmp_path):
        hdrs = self._build(admin_client, tmp_path, "legacy-repair-sys")
        from app.db import get_conn, init_db

        with get_conn() as conn:
            conn.execute(
                "UPDATE intelligence_runs SET status = 'success' "
                "WHERE run_type = 'capability_hierarchy'"
            )
        init_db()
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT status FROM intelligence_runs"
            ).fetchall()
        assert {row["status"] for row in rows} <= {"pending", "completed", "failed"}

        hierarchy = admin_client.get(
            "/repository/capability-hierarchy", headers=hdrs
        )
        assert hierarchy.status_code == 200, hierarchy.text


class TestBuildDoesNotBlockOtherRequests:
    """Regression tests for Issue #106: a slow/hanging build step must not
    make /health, /auth/me, or /systems become unresponsive."""

    def test_build_runs_in_background_and_stays_responsive(
        self, admin_client, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "async-build-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)

        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )

        # Simulate a slow/hanging reasoning-model call during the claim scan
        # step: this used to run inside the single get_conn() block held for
        # the whole build, starving every other request of the shared sqlite
        # lock. Claim scanning is chunk-level since Issue #109, so patch
        # scan_chunk (the per-chunk LLM call).
        import app.system_understanding_service as sus
        import app.documentation_claim_scanner as scanner_module

        monkeypatch.setattr(sus, "_is_reasoning_model_available", lambda: True)

        def _slow_scan_chunk(client, config, chunk, cache=None):
            time.sleep(1.0)
            return scanner_module.ChunkScanResult(
                chunk_id=chunk.chunk_id,
                chunk_content_hash=chunk.content_hash,
                prompt_version=scanner_module.PROMPT_VERSION,
                schema_version=scanner_module.SCHEMA_VERSION,
                claims=[],
            )

        monkeypatch.setattr(scanner_module, "scan_chunk", _slow_scan_chunk)

        build_r = admin_client.post(
            "/repository/system-understanding/build", headers=hdrs
        )
        assert build_r.status_code == 202, build_r.text
        build_id = build_r.json()["id"]

        # Give the background thread a moment to reach the slow step, then
        # verify unrelated endpoints (including DB-backed ones) respond
        # quickly instead of queueing behind a held connection lock.
        time.sleep(0.2)
        for _ in range(5):
            started = time.time()
            health_r = admin_client.get("/health")
            me_r = admin_client.get("/auth/me", headers=_bearer(token))
            systems_r = admin_client.get("/systems", headers=_bearer(token))
            elapsed = time.time() - started

            assert health_r.status_code == 200
            assert me_r.status_code == 200
            assert systems_r.status_code == 200
            assert elapsed < 0.5, (
                f"unrelated requests took {elapsed:.2f}s while a build was "
                "running; the shared DB lock is likely held across the slow step"
            )

        # The build itself should still complete once the slow step returns.
        deadline = time.time() + 10.0
        status = None
        while time.time() < deadline:
            status_r = admin_client.get(
                f"/repository/system-understanding/build/{build_id}", headers=hdrs
            )
            assert status_r.status_code == 200
            status = status_r.json()["status"]
            if status in ("completed", "failed"):
                break
            time.sleep(0.05)
        assert status == "completed"


class TestNextActionsPriority:
    """Issue #120: purpose/capabilities take priority over pipeline-complete.

    Unit tests against ``_build_next_actions`` directly since a fully
    "complete" pipeline (including the reasoning-only claim-scan step)
    cannot be produced under the mock LLM provider used by the API tests
    above.
    """

    def _complete_pipeline(self):
        from app.system_understanding_service import PIPELINE_STEPS, PipelineStep

        return [PipelineStep(step, "complete") for step in PIPELINE_STEPS]

    def _incomplete_pipeline(self, missing_step):
        from app.system_understanding_service import PipelineStep

        steps = []
        for step in self._complete_pipeline():
            steps.append(
                PipelineStep(step.step, "missing" if step.step == missing_step else "complete")
            )
        return steps

    def test_incomplete_pipeline_step_takes_priority_over_purpose(self):
        from app.system_understanding_service import _build_next_actions

        actions = _build_next_actions(
            self._incomplete_pipeline("symbols_indexed"),
            purpose=None,
            capabilities=[],
            metadata_coverage=None,
            gap_count=0,
        )
        labels = [a.action for a in actions]
        assert "Index code symbols" in labels
        assert "Define System Purpose" not in labels

    def test_claim_scan_blocked_does_not_offer_exploration(self):
        from app.system_understanding_service import MetadataCoverage, _build_next_actions

        actions = _build_next_actions(
            self._incomplete_pipeline("documentation_claims_scanned"),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=MetadataCoverage(symbol_count=10, symbols_with_source_metadata=10),
            gap_count=0,
        )
        labels = [a.action for a in actions]
        assert labels == ["Scan documentation claims"]

    def test_docs_code_reconcile_missing_does_not_offer_exploration(self):
        from app.system_understanding_service import MetadataCoverage, _build_next_actions

        actions = _build_next_actions(
            self._incomplete_pipeline("docs_code_reconciled"),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=MetadataCoverage(symbol_count=10, symbols_with_source_metadata=10),
            gap_count=0,
        )
        labels = [a.action for a in actions]
        assert labels == ["Reconcile docs and code"]

    def test_complete_pipeline_without_purpose_is_top_priority(self):
        from app.system_understanding_service import _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose=None,
            capabilities=[{"name": "Do things"}],
            metadata_coverage=None,
            gap_count=3,
        )
        assert actions[0].action == "Define System Purpose"
        assert actions[0].reason.startswith(
            "Pipeline completed, but no system purpose is defined yet."
        )
        # Issue #211: the reason also explains why defining it matters.
        assert "evaluation basis" in actions[0].reason
        assert actions[0].link == "/interview"

    def test_complete_pipeline_without_capabilities_after_purpose(self):
        from app.system_understanding_service import _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[],
            metadata_coverage=None,
            gap_count=0,
        )
        assert actions[0].action == "Identify main system capabilities"
        assert actions[0].link == "/interview"

    def test_metadata_coverage_and_gaps_after_purpose_and_capabilities(self):
        from app.system_understanding_service import MetadataCoverage, _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=MetadataCoverage(symbol_count=10, symbols_with_source_metadata=0),
            gap_count=2,
        )
        labels = [a.action for a in actions]
        assert labels == ["Add source metadata", "Review docs-code gaps"]

    def test_gap_summary_surfaces_unclassified_api_and_probe_candidate_actions(self):
        from app.system_understanding_service import GapSummary, _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=None,
            gap_count=3,
            gap_summary=[
                GapSummary(gap_type="unclassified_entrypoint", count=1),
                GapSummary(gap_type="missing_probe_flow", count=2),
            ],
        )
        by_label = {a.action: a for a in actions}
        assert by_label["Unclassified API found"].category == "observe"
        assert by_label["Unclassified API found"].link == "/interview"
        assert by_label["Probe candidate available"].category == "observe"
        assert by_label["Probe candidate available"].link == "/flow-explorer"

    def test_gap_derived_actions_match_gap_next_actions_primary_link(self):
        """Issue #199: the top-level Next Action link for each gap-type-derived
        action must match GAP_NEXT_ACTIONS[gap_type][0]["link"] — the same
        primary resolution shown on that gap type's card, so the two never
        disagree on where to send the user."""
        from app.system_understanding_service import GAP_NEXT_ACTIONS, GapSummary, _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=None,
            gap_count=3,
            gap_summary=[
                GapSummary(gap_type="unclassified_entrypoint", count=1),
                GapSummary(gap_type="missing_probe_flow", count=2),
            ],
        )
        by_label = {a.action: a for a in actions}
        assert by_label["Unclassified API found"].link == GAP_NEXT_ACTIONS["unclassified_entrypoint"][0]["link"]
        assert by_label["Probe candidate available"].link == GAP_NEXT_ACTIONS["missing_probe_flow"][0]["link"]

    def test_fully_satisfied_pipeline_offers_exploration_actions(self):
        from app.system_understanding_service import MetadataCoverage, _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=MetadataCoverage(symbol_count=10, symbols_with_source_metadata=10),
            gap_count=0,
        )
        labels = [a.action for a in actions]
        assert labels == ["Start from Capability", "Start from Feature", "Open Flow Explorer"]

    def test_all_actions_carry_a_finite_category(self):
        from app.system_understanding_service import MetadataCoverage, _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose=None,
            capabilities=[],
            metadata_coverage=MetadataCoverage(symbol_count=10, symbols_with_source_metadata=0),
            gap_count=2,
            proposed_plan_ids=[7],
            approved_plan_ids_without_validated_patch=[8],
            undecided_completed_experiment_ids=[9],
        )
        assert actions
        for action in actions:
            assert action.category in ("understand", "observe", "instrument", "evaluate")

    def test_pipeline_incomplete_still_puts_remediation_first(self):
        from app.system_understanding_service import _build_next_actions

        actions = _build_next_actions(
            self._incomplete_pipeline("symbols_indexed"),
            purpose=None,
            capabilities=[],
            metadata_coverage=None,
            gap_count=0,
            proposed_plan_ids=[1],
            approved_plan_ids_without_validated_patch=[2],
            undecided_completed_experiment_ids=[3],
        )
        assert actions[0].action == "Index code symbols"
        assert actions[0].category == "understand"
        labels = [a.action for a in actions]
        assert "Review probe plan" in labels
        assert "Generate / validate probe patch" in labels
        assert "Review experiment decision" in labels

    def test_proposed_plan_triggers_review_action(self):
        from app.system_understanding_service import _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=None,
            gap_count=0,
            proposed_plan_ids=[42],
        )
        matching = [a for a in actions if a.action == "Review probe plan"]
        assert len(matching) == 1
        assert matching[0].category == "observe"
        assert matching[0].link == "/probe-planner?plan=42"

    def test_approved_plan_without_validated_patch_triggers_instrument_action(self):
        from app.system_understanding_service import _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=None,
            gap_count=0,
            approved_plan_ids_without_validated_patch=[13],
        )
        matching = [a for a in actions if a.action == "Generate / validate probe patch"]
        assert len(matching) == 1
        assert matching[0].category == "instrument"
        assert matching[0].link == "/probe-planner?plan=13"

    def test_completed_experiment_without_decision_triggers_evaluate_action(self):
        from app.system_understanding_service import _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=None,
            gap_count=0,
            undecided_completed_experiment_ids=[99],
        )
        matching = [a for a in actions if a.action == "Review experiment decision"]
        assert len(matching) == 1
        assert matching[0].category == "evaluate"
        assert matching[0].link == "/experiments"

    def test_no_pending_plan_or_experiment_actions_when_none_exist(self):
        from app.system_understanding_service import _build_next_actions

        actions = _build_next_actions(
            self._complete_pipeline(),
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=None,
            gap_count=0,
        )
        labels = [a.action for a in actions]
        assert "Review probe plan" not in labels
        assert "Generate / validate probe patch" not in labels
        assert "Review experiment decision" not in labels


class TestDerivePrimaryAction:
    """Issue #201: ``_derive_primary_action`` picks the single highest-priority
    action for the current state, using the finite rules documented in
    docs/system-understanding-navigation.md.
    """

    def _complete_pipeline(self):
        from app.system_understanding_service import PIPELINE_STEPS, PipelineStep

        return [PipelineStep(step, "complete") for step in PIPELINE_STEPS]

    def _incomplete_pipeline(self, *missing_steps):
        from app.system_understanding_service import PipelineStep

        return [
            PipelineStep(step.step, "missing" if step.step in missing_steps else "complete")
            for step in self._complete_pipeline()
        ]

    def test_repository_not_configured_uses_first_next_action(self):
        from app.system_understanding_service import (
            PipelineStep, _build_next_actions, _derive_primary_action,
        )

        pipeline = [
            PipelineStep("repository_configured", "missing"),
            PipelineStep("snapshot_ready", "missing"),
        ]
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(pipeline, next_actions, latest_build=None)

        assert primary is not None
        assert primary.action == "Configure repository"
        assert primary.action_kind == "navigate"

    def test_snapshot_not_ready_uses_first_next_action(self):
        from app.system_understanding_service import (
            PipelineStep, _build_next_actions, _derive_primary_action,
        )

        pipeline = [
            PipelineStep("repository_configured", "complete"),
            PipelineStep("snapshot_ready", "missing"),
        ]
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(pipeline, next_actions, latest_build=None)

        assert primary is not None
        assert primary.action == "Create snapshot"
        assert primary.action_kind == "navigate"

    def test_repository_not_configured_wins_even_if_build_is_running(self):
        """Rule 1 is evaluated before rule 2 (build-running check)."""
        from app.system_understanding_service import (
            PipelineStep, _build_next_actions, _derive_primary_action,
        )

        pipeline = [
            PipelineStep("repository_configured", "missing"),
            PipelineStep("snapshot_ready", "missing"),
        ]
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(
            pipeline, next_actions, latest_build={"status": "running"},
        )

        assert primary is not None
        assert primary.action == "Configure repository"

    def test_incomplete_step_with_no_build_running_offers_build_action(self):
        from app.system_understanding_service import _build_next_actions, _derive_primary_action

        pipeline = self._incomplete_pipeline("symbols_indexed")
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(pipeline, next_actions, latest_build=None)

        assert primary is not None
        assert primary.action == "Build system understanding"
        assert primary.action_kind == "build"
        assert primary.link is None
        assert "1" in primary.reason

    def test_incomplete_step_reason_counts_all_remaining_steps(self):
        from app.system_understanding_service import _build_next_actions, _derive_primary_action

        pipeline = self._incomplete_pipeline("symbols_indexed", "entrypoints_discovered")
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(pipeline, next_actions, latest_build=None)

        assert primary is not None
        assert primary.action_kind == "build"
        assert primary.reason.startswith("2 ")

    @pytest.mark.parametrize("status", ["queued", "running"])
    def test_build_running_or_queued_suppresses_primary_action(self, status):
        from app.system_understanding_service import _build_next_actions, _derive_primary_action

        pipeline = self._incomplete_pipeline("symbols_indexed")
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(
            pipeline, next_actions, latest_build={"status": status},
        )

        assert primary is None

    @pytest.mark.parametrize("status", ["completed", "failed", "partial", "cancelled"])
    def test_settled_build_does_not_suppress_primary_action(self, status):
        from app.system_understanding_service import _build_next_actions, _derive_primary_action

        pipeline = self._incomplete_pipeline("symbols_indexed")
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(
            pipeline, next_actions, latest_build={"status": status},
        )

        assert primary is not None
        assert primary.action_kind == "build"

    def test_pipeline_complete_without_purpose_uses_define_purpose(self):
        from app.system_understanding_service import _build_next_actions, _derive_primary_action

        pipeline = self._complete_pipeline()
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(pipeline, next_actions, latest_build=None)

        assert primary is not None
        assert primary.action == "Define System Purpose"
        assert primary.link == "/interview"
        assert primary.action_kind == "navigate"

    def test_pipeline_complete_without_purpose_with_settled_build_is_unaffected(self):
        from app.system_understanding_service import _build_next_actions, _derive_primary_action

        pipeline = self._complete_pipeline()
        next_actions = _build_next_actions(
            pipeline, purpose=None, capabilities=[], metadata_coverage=None, gap_count=0,
        )
        primary = _derive_primary_action(
            pipeline, next_actions, latest_build={"status": "completed"},
        )

        assert primary is not None
        assert primary.action == "Define System Purpose"

    def test_fully_satisfied_uses_first_next_action(self):
        from app.system_understanding_service import _build_next_actions, _derive_primary_action

        pipeline = self._complete_pipeline()
        next_actions = _build_next_actions(
            pipeline,
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            metadata_coverage=None,
            gap_count=0,
        )
        primary = _derive_primary_action(pipeline, next_actions, latest_build=None)

        assert primary is not None
        assert primary.action == "Start from Capability"
        assert primary.link == "/capability-map"
        assert primary.action_kind == "navigate"

    def test_no_next_actions_returns_none(self):
        from app.system_understanding_service import _derive_primary_action

        pipeline = self._complete_pipeline()
        primary = _derive_primary_action(pipeline, next_actions=[], latest_build=None)

        assert primary is None


class TestDeriveStageStatuses:
    """Issue #202: ``_derive_stage_statuses`` derives a deterministic
    not_started / in_progress / blocked / complete status (+ counts) for each
    of the 4 Hub stages, using the finite rules documented in
    docs/system-understanding-navigation.md.
    """

    def _complete_pipeline(self):
        from app.system_understanding_service import PIPELINE_STEPS, PipelineStep

        return [PipelineStep(step, "complete") for step in PIPELINE_STEPS]

    def _pipeline_with(self, **overrides):
        from app.system_understanding_service import PIPELINE_STEPS, PipelineStep

        return [
            PipelineStep(step, overrides.get(step, "complete")) for step in PIPELINE_STEPS
        ]

    def _derive(self, pipeline, **overrides):
        from app.system_understanding_service import GapSummary, _derive_stage_statuses

        defaults = dict(
            purpose={"name": "Sys", "summary": "Does things"},
            capabilities=[{"name": "Cap"}],
            gap_count=0,
            gap_summary=[],
            entrypoint_count=0,
            proposed_plan_count=0,
            approved_without_patch_count=0,
            validated_plan_count=0,
            total_plan_count=0,
            undecided_experiment_count=0,
            decided_experiment_count=0,
            total_experiment_count=0,
        )
        defaults.update(overrides)
        stages = _derive_stage_statuses(pipeline, **defaults)
        return {s.stage: s for s in stages}

    def _gap_summary(self, **counts):
        from app.system_understanding_service import GapSummary

        return [GapSummary(gap_type=k, count=v) for k, v in counts.items()]

    # --- understand ---

    def test_understand_not_started_when_all_steps_missing(self):
        from app.system_understanding_service import PIPELINE_STEPS, PipelineStep

        pipeline = [PipelineStep(step, "missing") for step in PIPELINE_STEPS]
        stages = self._derive(pipeline, purpose=None, capabilities=[])

        assert stages["understand"].status == "not_started"

    def test_understand_blocked_when_any_step_blocked(self):
        pipeline = self._pipeline_with(symbols_indexed="blocked")
        stages = self._derive(pipeline)

        assert stages["understand"].status == "blocked"

    def test_understand_blocked_when_any_step_failed(self):
        pipeline = self._pipeline_with(documentation_indexed="failed")
        stages = self._derive(pipeline)

        assert stages["understand"].status == "blocked"

    def test_understand_in_progress_when_pipeline_incomplete(self):
        pipeline = self._pipeline_with(symbols_indexed="missing")
        stages = self._derive(pipeline)

        assert stages["understand"].status == "in_progress"

    def test_understand_in_progress_when_pipeline_complete_but_no_purpose(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(pipeline, purpose=None, capabilities=[{"name": "Cap"}])

        assert stages["understand"].status == "in_progress"

    def test_understand_in_progress_when_pipeline_complete_but_no_capabilities(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline, purpose={"name": "Sys", "summary": "s"}, capabilities=[],
        )

        assert stages["understand"].status == "in_progress"

    def test_understand_complete_when_pipeline_purpose_and_capabilities_all_present(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(pipeline)

        assert stages["understand"].status == "complete"

    def test_understand_counts_gaps(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(pipeline, gap_count=4)

        assert stages["understand"].counts == {"gaps": 4}

    # --- observe ---

    def test_observe_not_started_when_entrypoints_step_incomplete(self):
        pipeline = self._pipeline_with(entrypoints_discovered="missing")
        stages = self._derive(pipeline, entrypoint_count=0)

        assert stages["observe"].status == "not_started"

    def test_observe_in_progress_when_no_entrypoints_yet(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(pipeline, entrypoint_count=0)

        assert stages["observe"].status == "in_progress"

    def test_observe_in_progress_when_unclassified_entrypoints_remain(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline,
            entrypoint_count=5,
            gap_summary=self._gap_summary(unclassified_entrypoint=2),
        )

        assert stages["observe"].status == "in_progress"

    def test_observe_complete_when_entrypoints_exist_and_none_unclassified(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(pipeline, entrypoint_count=5)

        assert stages["observe"].status == "complete"

    def test_observe_counts_entrypoints_and_unclassified(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline,
            entrypoint_count=5,
            gap_summary=self._gap_summary(unclassified_entrypoint=2),
        )

        assert stages["observe"].counts == {"entrypoints": 5, "unclassified": 2}

    # --- instrument ---

    def test_instrument_not_started_when_no_plans(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(pipeline, total_plan_count=0)

        assert stages["instrument"].status == "not_started"

    def test_instrument_in_progress_when_plans_exist_but_none_validated(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline, total_plan_count=1, proposed_plan_count=1, validated_plan_count=0,
        )

        assert stages["instrument"].status == "in_progress"

    def test_instrument_in_progress_when_some_approved_plans_lack_validated_patch(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline,
            total_plan_count=2,
            validated_plan_count=1,
            approved_without_patch_count=1,
        )

        assert stages["instrument"].status == "in_progress"

    def test_instrument_complete_when_validated_plan_exists_and_none_pending(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline,
            total_plan_count=1,
            validated_plan_count=1,
            approved_without_patch_count=0,
        )

        assert stages["instrument"].status == "complete"

    def test_instrument_counts(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline,
            total_plan_count=3,
            proposed_plan_count=2,
            approved_without_patch_count=1,
            validated_plan_count=0,
        )

        assert stages["instrument"].counts == {
            "proposed": 2, "approved_without_patch": 1, "validated": 0,
        }

    # --- evaluate ---

    def test_evaluate_not_started_when_no_experiments(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(pipeline, total_experiment_count=0)

        assert stages["evaluate"].status == "not_started"

    def test_evaluate_in_progress_when_experiments_exist_but_none_decided(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline, total_experiment_count=1, undecided_experiment_count=1,
            decided_experiment_count=0,
        )

        assert stages["evaluate"].status == "in_progress"

    def test_evaluate_in_progress_when_some_completed_experiments_undecided(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline,
            total_experiment_count=2,
            decided_experiment_count=1,
            undecided_experiment_count=1,
        )

        assert stages["evaluate"].status == "in_progress"

    def test_evaluate_complete_when_decided_experiment_exists_and_none_pending(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline,
            total_experiment_count=1,
            decided_experiment_count=1,
            undecided_experiment_count=0,
        )

        assert stages["evaluate"].status == "complete"

    def test_evaluate_counts(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline,
            total_experiment_count=3,
            decided_experiment_count=2,
            undecided_experiment_count=1,
        )

        assert stages["evaluate"].counts == {"undecided": 1, "decided": 2}


class TestSystemUnderstandingStagesInApiResponse:
    """Issue #202: GET /repository/system-understanding includes ``stages``."""

    def test_new_system_reports_four_not_started_stages(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "stages-sys")
        hdrs = _headers(token, sys["id"])

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        stages = r.json()["stages"]

        assert {s["stage"] for s in stages} == {
            "understand", "observe", "instrument", "evaluate",
        }
        by_stage = {s["stage"]: s for s in stages}
        assert by_stage["understand"]["status"] == "not_started"
        assert by_stage["observe"]["status"] == "not_started"
        assert by_stage["instrument"]["status"] == "not_started"
        assert by_stage["evaluate"]["status"] == "not_started"
        assert by_stage["instrument"]["counts"] == {
            "proposed": 0, "approved_without_patch": 0, "validated": 0,
        }
        assert by_stage["evaluate"]["counts"] == {"undecided": 0, "decided": 0}

    def test_proposed_plan_surfaces_in_instrument_counts(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        sys = _create_system(admin_client, token, "stages-plan-sys")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap_r = admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )
        snapshot_id = snap_r.json()["id"]
        system_id = sys["id"]

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'probe_plan', 'mock', 'mock-model', 'v1', 'v1',
                           'reasoning_llm', 'completed', 1, 0, 0)""",
                (system_id, snapshot_id),
            )
            run_id = cur.lastrowid
            conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id, feature_id,
                        objective, status, origin, created_at, updated_at)
                   VALUES (?, ?, ?, 'feat-1', 'obj', 'proposed', 'manual', 0, 0)""",
                (system_id, snapshot_id, run_id),
            )

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        by_stage = {s["stage"]: s for s in r.json()["stages"]}
        assert by_stage["instrument"]["status"] == "in_progress"
        assert by_stage["instrument"]["counts"]["proposed"] == 1


class TestGapTrendAndRefreshRecommended:
    """Issue #203: GET /repository/system-understanding exposes ``gap_trend``
    (before/after gap counts across the last two settled builds, read back
    from system_understanding_gap_history) and
    ``understanding_refresh_recommended`` (a materialized Interview change
    postdating the latest completed build). Both are plain deterministic
    reads -- no reasoning model involved."""

    def _setup_system(self, admin_client, tmp_path, name):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, name)
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap_r = admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )
        snapshot_id = snap_r.json()["id"]
        return sys["id"], hdrs, snapshot_id

    def _insert_build(self, system_id, snapshot_id, status, completed_at=None):
        from app.db import get_conn

        with get_conn() as conn:
            return conn.execute(
                """INSERT INTO system_understanding_builds
                       (system_id, snapshot_id, status, completed_at, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (system_id, snapshot_id, status, completed_at, completed_at or 0),
            ).lastrowid

    def _insert_gap_history(self, system_id, snapshot_id, build_id, counts):
        from app.db import get_conn

        with get_conn() as conn:
            for gap_type, count in counts.items():
                conn.execute(
                    """INSERT INTO system_understanding_gap_history
                           (system_id, snapshot_id, build_id, gap_type, count, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (system_id, snapshot_id, build_id, gap_type, count, 0),
                )

    def _insert_materialized_session(self, system_id, snapshot_id, materialized_at):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO interview_session
                       (system_id, snapshot_id, materialized_at, created_at, updated_at)
                   VALUES (?, ?, ?, 0, 0)""",
                (system_id, snapshot_id, materialized_at),
            )

    def test_fewer_than_two_settled_builds_returns_empty_trend(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "gap-trend-one-build")
        build_id = self._insert_build(system_id, snapshot_id, "completed", completed_at=10)
        self._insert_gap_history(system_id, snapshot_id, build_id, {"docs_only": 5})

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        assert r.json()["gap_trend"] == []

    def test_two_builds_reports_current_previous_new_and_resolved_gap_types(
        self, admin_client, tmp_path
    ):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "gap-trend-two-builds")
        build1 = self._insert_build(system_id, snapshot_id, "completed", completed_at=10)
        self._insert_gap_history(
            system_id, snapshot_id, build1,
            {"docs_only": 12, "unclassified_entrypoint": 2},
        )
        build2 = self._insert_build(system_id, snapshot_id, "partial", completed_at=20)
        self._insert_gap_history(
            system_id, snapshot_id, build2,
            {"docs_only": 8, "code_only": 3},
        )

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        trend = {t["gap_type"]: t for t in r.json()["gap_trend"]}

        # docs_only: present in both builds -> improved (12 -> 8).
        assert trend["docs_only"]["previous"] == 12
        assert trend["docs_only"]["current"] == 8
        # unclassified_entrypoint: only in the older build -> resolved (current=0).
        assert trend["unclassified_entrypoint"]["previous"] == 2
        assert trend["unclassified_entrypoint"]["current"] == 0
        # code_only: only in the newer build -> newly appeared (previous=0).
        assert trend["code_only"]["previous"] == 0
        assert trend["code_only"]["current"] == 3

    def test_gap_trend_uses_the_two_most_recent_settled_builds(self, admin_client, tmp_path):
        """A third, most-recent build's history supersedes the first two --
        the comparison is always against the two most recent settled builds,
        not the first two ever recorded."""
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "gap-trend-three-builds")
        build1 = self._insert_build(system_id, snapshot_id, "completed", completed_at=10)
        self._insert_gap_history(system_id, snapshot_id, build1, {"docs_only": 20})
        build2 = self._insert_build(system_id, snapshot_id, "completed", completed_at=20)
        self._insert_gap_history(system_id, snapshot_id, build2, {"docs_only": 12})
        build3 = self._insert_build(system_id, snapshot_id, "completed", completed_at=30)
        self._insert_gap_history(system_id, snapshot_id, build3, {"docs_only": 4})

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        trend = {t["gap_type"]: t for t in r.json()["gap_trend"]}
        assert trend["docs_only"]["previous"] == 12
        assert trend["docs_only"]["current"] == 4

    def test_refresh_recommended_true_after_materialize_before_rebuild(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "refresh-true-sys")
        self._insert_build(system_id, snapshot_id, "completed", completed_at=10)
        self._insert_materialized_session(system_id, snapshot_id, materialized_at=20)

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        assert r.json()["understanding_refresh_recommended"] is True

    def test_refresh_recommended_false_after_rebuild_postdates_materialize(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "refresh-false-sys")
        self._insert_materialized_session(system_id, snapshot_id, materialized_at=10)
        self._insert_build(system_id, snapshot_id, "completed", completed_at=20)

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        assert r.json()["understanding_refresh_recommended"] is False

    def test_refresh_recommended_false_without_materialized_session(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "refresh-no-session-sys")
        self._insert_build(system_id, snapshot_id, "completed", completed_at=10)

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        assert r.json()["understanding_refresh_recommended"] is False

    def test_refresh_recommended_false_without_completed_build(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "refresh-no-build-sys")
        self._insert_materialized_session(system_id, snapshot_id, materialized_at=10)

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        assert r.json()["understanding_refresh_recommended"] is False

    def test_gap_trend_and_refresh_recommended_isolated_by_system(self, admin_client, tmp_path):
        system_a, hdrs_a, snap_a = self._setup_system(admin_client, tmp_path, "gap-trend-iso-a")
        build_a1 = self._insert_build(system_a, snap_a, "completed", completed_at=10)
        self._insert_gap_history(system_a, snap_a, build_a1, {"docs_only": 12})
        build_a2 = self._insert_build(system_a, snap_a, "completed", completed_at=20)
        self._insert_gap_history(system_a, snap_a, build_a2, {"docs_only": 8})
        self._insert_materialized_session(system_a, snap_a, materialized_at=30)

        token = _login(admin_client)
        sys_b = _create_system(admin_client, token, "gap-trend-iso-b")
        hdrs_b = _headers(token, sys_b["id"])

        r_a = admin_client.get("/repository/system-understanding", headers=hdrs_a)
        assert r_a.status_code == 200, r_a.text
        assert r_a.json()["gap_trend"] != []
        assert r_a.json()["understanding_refresh_recommended"] is True

        # System B has none of System A's history/materialize state.
        r_b = admin_client.get("/repository/system-understanding", headers=hdrs_b)
        assert r_b.status_code == 200, r_b.text
        assert r_b.json()["gap_trend"] == []
        assert r_b.json()["understanding_refresh_recommended"] is False
