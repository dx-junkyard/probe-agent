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
        # Capability hierarchy has a deterministic base that runs without reasoning
        assert pipeline["capability_hierarchy_ready"] in ("complete", "blocked")

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
        assert actions[0].reason == "Pipeline completed, but no system purpose is defined yet."
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
