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

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
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

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        assert r.status_code == 200
        data = r.json()

        pipeline = {s["step"]: s["status"] for s in data["pipeline"]}
        # Documentation indexed and claims scanned require reasoning model
        # They should be blocked when no reasoning model is configured
        assert pipeline["documentation_indexed"] == "blocked"
        assert pipeline["documentation_claims_scanned"] == "blocked"
        # Capability hierarchy has a deterministic base that runs without reasoning
        assert pipeline["capability_hierarchy_ready"] in ("complete", "blocked")


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

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
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

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
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

        r1 = admin_client.post("/repository/system-understanding/build", headers=hdrs)
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

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
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
        r = admin_client.post(
            "/repository/system-understanding/build", headers=hdrs
        )
        assert r.status_code == 200
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
