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

        # Issue #239: `next_actions` / `primary_action` were removed from this
        # response. The canonical "what should the user do next" projection
        # is `GET /system-state`'s `primary_item` -- see
        # TestPrimaryRecommendationParity in test_next_step_parity.py for the
        # "Configure repository" case, and test_system_state.py for
        # `repository.configuration.missing` item coverage.

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
            purpose_defined=True,
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
        stages = self._derive(pipeline, purpose_defined=False, capabilities=[])

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
        stages = self._derive(pipeline, purpose_defined=False, capabilities=[{"name": "Cap"}])

        assert stages["understand"].status == "in_progress"

    def test_understand_in_progress_when_pipeline_complete_but_no_capabilities(self):
        pipeline = self._complete_pipeline()
        stages = self._derive(
            pipeline, purpose_defined=True, capabilities=[],
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

    # Issue #239: `understanding_refresh_recommended` was removed from the
    # GET /repository/system-understanding response (the canonical projection
    # is now the `interview.materialized.rebuild_required` StateItem from
    # GET /system-state -- see test_system_state.py /
    # test_next_step_parity.py's TestUnderstandingRefreshRecommendedMatchesStateItem
    # for that side). `_check_understanding_refresh_recommended` itself is
    # unchanged and still lives in system_understanding_service.py (system_state.py
    # calls it directly to build that StateItem), so these scenario tests now
    # call it directly instead of reading it off the deleted API field.

    def _refresh_recommended(self, system_id):
        from app.db import get_conn
        from app.system_understanding_service import _check_understanding_refresh_recommended

        with get_conn() as conn:
            return _check_understanding_refresh_recommended(conn, system_id)

    def test_refresh_recommended_true_after_materialize_before_rebuild(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "refresh-true-sys")
        self._insert_build(system_id, snapshot_id, "completed", completed_at=10)
        self._insert_materialized_session(system_id, snapshot_id, materialized_at=20)

        assert self._refresh_recommended(system_id) is True

    def test_refresh_recommended_false_after_rebuild_postdates_materialize(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "refresh-false-sys")
        self._insert_materialized_session(system_id, snapshot_id, materialized_at=10)
        self._insert_build(system_id, snapshot_id, "completed", completed_at=20)

        assert self._refresh_recommended(system_id) is False

    def test_refresh_recommended_false_without_materialized_session(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "refresh-no-session-sys")
        self._insert_build(system_id, snapshot_id, "completed", completed_at=10)

        assert self._refresh_recommended(system_id) is False

    def test_refresh_recommended_false_without_completed_build(self, admin_client, tmp_path):
        system_id, hdrs, snapshot_id = self._setup_system(admin_client, tmp_path, "refresh-no-build-sys")
        self._insert_materialized_session(system_id, snapshot_id, materialized_at=10)

        assert self._refresh_recommended(system_id) is False

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
        system_b = sys_b["id"]

        r_a = admin_client.get("/repository/system-understanding", headers=hdrs_a)
        assert r_a.status_code == 200, r_a.text
        assert r_a.json()["gap_trend"] != []
        assert self._refresh_recommended(system_a) is True

        # System B has none of System A's history/materialize state.
        r_b = admin_client.get("/repository/system-understanding", headers=hdrs_b)
        assert r_b.status_code == 200, r_b.text
        assert r_b.json()["gap_trend"] == []
        assert self._refresh_recommended(system_b) is False


class TestIssue236FactsExtractionRegression:
    """Issue #236: state-fact retrieval was consolidated into app/state_facts.py
    and system_understanding_service's own local ``purpose_defined`` check was
    replaced by a reduction of system_state.evaluate_understanding's 5-way
    classification. These tests pin GET /repository/system-understanding's
    response shape across representative scenarios (unconfigured / snapshot
    only / pipeline complete) so a regression in the shared facts layer or in
    the purpose_defined reduction is caught here, not just in unit tests of
    the private helpers.
    """

    def _insert_intelligence_run(self, system_id, snapshot_id, run_type, status):
        from app.db import get_conn

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, is_mock, started_at, completed_at)
                   VALUES (?, ?, ?, 'deterministic', 'none',
                           'v1', 'v1', 'deterministic', ?, 0, 0, 0)""",
                (system_id, snapshot_id, run_type, status),
            )
            return cur.lastrowid

    def _insert_build_step(self, system_id, snapshot_id, step, status):
        from app.db import get_conn

        with get_conn() as conn:
            build = conn.execute(
                """INSERT INTO system_understanding_builds
                       (system_id, snapshot_id, status, current_step, heartbeat_at, started_at, created_at)
                   VALUES (?, ?, 'completed', NULL, 0, 0, 0)""",
                (system_id, snapshot_id),
            )
            conn.execute(
                """INSERT INTO system_understanding_build_steps
                       (build_id, system_id, snapshot_id, step, status, artifact_provenance,
                        heartbeat_at, started_at, created_at)
                   VALUES (?, ?, ?, ?, ?, '{"chunk_count": 1}', 0, 0, 0)""",
                (build.lastrowid, system_id, snapshot_id, step, status),
            )

    def _insert_understanding_graph_snapshot(self, system_id, snapshot_id):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO understanding_graph_snapshots
                       (system_id, snapshot_id, graph_json, source_hash, claim_count,
                        confidence_summary, created_at)
                   VALUES (?, ?, '{}', '', 0, '{}', 0)""",
                (system_id, snapshot_id),
            )

    def _insert_code_symbol(self, system_id, snapshot_id):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO code_symbols
                       (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line)
                   VALUES (?, ?, 'src/main.py', 'list_items', 'function', 1, 2)""",
                (snapshot_id, system_id),
            )

    def _insert_code_entrypoint(self, system_id, snapshot_id):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO code_entrypoints
                       (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                        handler_path, handler_qualified_name, line_start, line_end, created_at)
                   VALUES (?, ?, 'api', 'GET /items', 'api', 'List items',
                           'src/main.py', 'list_items', 1, 2, 0)""",
                (system_id, snapshot_id),
            )

    def _insert_purpose_and_capability_nodes(self, system_id, snapshot_id, run_id):
        from app.db import get_conn

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO capability_hierarchy_nodes
                       (system_id, snapshot_id, intelligence_run_id, node_type, name, summary, created_at)
                   VALUES (?, ?, ?, 'purpose', 'Test system', 'Serves test items.', 0)""",
                (system_id, snapshot_id, run_id),
            )
            conn.execute(
                """INSERT INTO capability_hierarchy_nodes
                       (system_id, snapshot_id, intelligence_run_id, node_type, name, summary,
                        entrypoint_id, created_at)
                   VALUES (?, ?, ?, 'capability', 'Item management', 'Manages items.',
                           (SELECT id FROM code_entrypoints WHERE system_id = ? AND snapshot_id = ? LIMIT 1), 0)""",
                (system_id, snapshot_id, run_id, system_id, snapshot_id),
            )

    def test_unconfigured_system_response_shape(self, admin_client):
        """未設定: no repository configured at all."""
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "issue236-unconfigured")
        hdrs = _headers(token, sys["id"])

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["snapshot_id"] is None
        assert data["purpose"] is None
        assert data["capabilities"] == []
        pipeline = {s["step"]: s["status"] for s in data["pipeline"]}
        assert pipeline["repository_configured"] == "missing"
        assert pipeline["snapshot_ready"] == "missing"
        assert data["stages"][0]["stage"] == "understand"
        assert data["stages"][0]["status"] == "not_started"

        # Issue #239: the canonical "next step" guidance projection lives at
        # GET /system-state now, not on this response.
        state_r = admin_client.get("/system-state", headers=hdrs)
        assert state_r.status_code == 200, state_r.text
        state_ids = {i["state_id"] for i in state_r.json()["items"]}
        assert "repository.configuration.missing" in state_ids

    def test_snapshot_only_response_shape(self, admin_client, tmp_path):
        """snapshot のみ: repository configured and a ready snapshot exists,
        but no build has run yet -- pipeline steps stay missing/blocked."""
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "issue236-snapshot-only")
        hdrs = _headers(token, sys["id"])
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["snapshot_id"] == snap.json()["id"]
        assert data["commit_sha"] == sha
        assert data["purpose"] is None
        assert data["capabilities"] == []
        pipeline = {s["step"]: s["status"] for s in data["pipeline"]}
        assert pipeline["repository_configured"] == "complete"
        assert pipeline["snapshot_ready"] == "complete"
        assert pipeline["symbols_indexed"] == "missing"
        assert data["stages"][0]["stage"] == "understand"
        # documentation_claims_scanned is blocked (no reasoning model under
        # the mock provider), which _derive_stage_statuses ranks above
        # in_progress -- both are "not complete yet", which is what this
        # scenario asserts; the exact branch is covered by
        # TestDeriveStageStatuses's unit tests.
        assert data["stages"][0]["status"] in ("in_progress", "blocked")

        # /system-state agrees: no ready-snapshot blocker, but pipeline items remain.
        state_r = admin_client.get("/system-state", headers=hdrs)
        assert state_r.status_code == 200, state_r.text
        state_ids = {i["state_id"] for i in state_r.json()["items"]}
        assert "snapshot.ready.missing" not in state_ids
        assert "understanding.purpose.missing_baseline" in state_ids

    def test_pipeline_complete_response_shape(self, admin_client, tmp_path):
        """pipeline 完了: every pipeline step is complete and Purpose/Core
        Capabilities are defined for the current snapshot. This is the
        highest-risk scenario for the purpose_defined reduction (Issue #236)
        since it is the only branch where next_actions/stages consult it."""
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "issue236-pipeline-complete")
        system_id = sys["id"]
        hdrs = _headers(token, system_id)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        snapshot_id = snap.json()["id"]

        self._insert_intelligence_run(system_id, snapshot_id, "symbol_index", "completed")
        self._insert_code_symbol(system_id, snapshot_id)
        self._insert_code_entrypoint(system_id, snapshot_id)
        self._insert_build_step(system_id, snapshot_id, "documentation_index", "completed")
        self._insert_understanding_graph_snapshot(system_id, snapshot_id)
        run_id = self._insert_intelligence_run(system_id, snapshot_id, "capability_hierarchy", "completed")
        self._insert_purpose_and_capability_nodes(system_id, snapshot_id, run_id)

        r = admin_client.get("/repository/system-understanding", headers=hdrs)
        assert r.status_code == 200, r.text
        data = r.json()
        pipeline = {s["step"]: s["status"] for s in data["pipeline"]}
        assert pipeline["repository_configured"] == "complete"
        assert pipeline["snapshot_ready"] == "complete"
        assert pipeline["documentation_indexed"] == "complete"
        assert pipeline["symbols_indexed"] == "complete"
        assert pipeline["entrypoints_discovered"] == "complete"
        assert pipeline["docs_code_reconciled"] == "complete"
        assert pipeline["capability_hierarchy_ready"] == "complete"

        assert data["purpose"]["name"] == "Test system"
        assert len(data["capabilities"]) == 1

        stages = {s["stage"]: s for s in data["stages"]}
        # documentation_claims_scanned is the one reasoning-only step and
        # stays blocked under the mock provider, so "understand" cannot
        # reach complete purely from this fixture -- but purpose_defined
        # being wrong would make this fail differently (below, via
        # /system-state's understanding.purpose.satisfied item, since Issue
        # #239 removed this response's own next_actions projection).
        assert stages["understand"].get("status") in ("in_progress", "blocked", "complete")

        # /system-state agrees: Purpose/Capabilities are satisfied, not
        # missing/unconfirmed/impacted.
        state_r = admin_client.get("/system-state", headers=hdrs)
        assert state_r.status_code == 200, state_r.text
        state_ids = {i["state_id"] for i in state_r.json()["items"]}
        assert "understanding.purpose.satisfied" in state_ids
        assert "understanding.capabilities.satisfied" in state_ids
        assert not any(s.startswith("pipeline.symbol_index.") for s in state_ids)
        assert not any(s.startswith("pipeline.capability_hierarchy.") for s in state_ids)
