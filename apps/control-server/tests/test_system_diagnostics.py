"""Tests for Issue #101: deterministic system settings diagnostics.

Covers GET /system-diagnostics: env/path validation, provider/model
consistency, last observed run failures, pipeline prerequisites, and
System isolation. No check may call an LLM.
"""

import sqlite3
import subprocess
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-diag-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER",
        "INTELLIGENCE_LLM_MODEL",
        "INTELLIGENCE_LLM_TIMEOUT",
        "INTELLIGENCE_MAX_OUTPUT_TOKENS",
        "CONTROL_API_KEYS",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIMEOUT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {
        "Authorization": f"Bearer {token}",
        "X-Probe-System-Id": str(system_id),
    }


def _init_git_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=str(repo), check=True, capture_output=True,
    )
    (repo / "README.md").write_text("# Test\n")
    (repo / "main.py").write_text("def run():\n    return 1\n")
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


def _get_checks(client, hdrs):
    r = client.get("/system-diagnostics", headers=hdrs)
    assert r.status_code == 200, r.text
    data = r.json()
    return data, {c["check_id"]: c for c in data["checks"]}


def _setup(client, name="diag-sys"):
    token = _login(client)
    sys = _create_system(client, token, name)
    return token, sys, _headers(token, sys["id"])


class TestDiagnosticsBasics:
    def test_returns_deterministic_checks(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        data, checks = _get_checks(admin_client, hdrs)

        assert data["overall_severity"] in ("ok", "warning", "error", "blocked", "unknown")
        assert set(data["severity_counts"]) >= {"ok", "warning", "error", "blocked", "unknown"}
        expected_ids = {
            "repository_roots", "repository_config", "snapshot_status",
            "database_storage", "auth_scope",
            "llm_base_config", "intelligence_llm_config", "llm_last_run",
            "pipeline_symbol_index", "pipeline_entrypoint_index",
            "pipeline_documentation_index", "pipeline_understanding_graph",
            "pipeline_capability_hierarchy",
        }
        assert expected_ids <= set(checks)
        assert all(c["decision_method"] == "deterministic" for c in checks.values())

    def test_requires_auth(self, admin_client):
        r = admin_client.get("/system-diagnostics")
        assert r.status_code == 401

    def test_mock_provider_marked_and_blocks_reasoning(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        _, checks = _get_checks(admin_client, hdrs)

        assert checks["llm_base_config"]["severity"] == "warning"
        assert "mock" in checks["llm_base_config"]["detail"]
        assert checks["intelligence_llm_config"]["severity"] == "blocked"


class TestRepositoryChecks:
    def test_missing_repository_roots_is_error(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.delenv("PROBE_REPOSITORY_ROOTS", raising=False)
        _, checks = _get_checks(admin_client, hdrs)

        c = checks["repository_roots"]
        assert c["severity"] == "error"
        assert "PROBE_REPOSITORY_ROOTS" in c["related_env"]
        assert c["remediation"]

    def test_nonexistent_root_is_error(self, admin_client, monkeypatch, tmp_path):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path / "does-not-exist"))
        _, checks = _get_checks(admin_client, hdrs)
        assert checks["repository_roots"]["severity"] == "error"

    def test_unconfigured_repository_is_warning(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        _, checks = _get_checks(admin_client, hdrs)

        c = checks["repository_config"]
        assert c["severity"] == "warning"
        assert "repository_configured" in c["related_pipeline_steps"]
        assert "/repository" in c["related_pages"]

    def test_deleted_repository_path_is_error(self, admin_client, tmp_path):
        import shutil

        _, _, hdrs = _setup(admin_client)
        repo, _sha = _init_git_repo(tmp_path)
        r = admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        assert r.status_code == 200, r.text
        shutil.rmtree(repo)

        _, checks = _get_checks(admin_client, hdrs)
        c = checks["repository_config"]
        assert c["severity"] == "error"
        assert str(repo) in c["related_paths"]

    def test_zero_indexed_files_is_warning(self, admin_client, tmp_path):
        _, _, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(repo),
                "include_patterns": ["**"],
                "exclude_patterns": ["**"],
            },
            headers=hdrs,
        )
        snap = admin_client.post(
            "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
        )
        assert snap.status_code == 201, snap.text

        _, checks = _get_checks(admin_client, hdrs)
        c = checks["snapshot_status"]
        assert c["severity"] == "warning"
        assert "0 indexed" in c["detail"]


class TestLLMConfigChecks:
    def test_invalid_provider_is_error(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "not-a-provider")
        _, checks = _get_checks(admin_client, hdrs)

        assert checks["llm_base_config"]["severity"] == "error"
        assert checks["intelligence_llm_config"]["severity"] == "error"

    def test_missing_api_key_is_error(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        _, checks = _get_checks(admin_client, hdrs)

        c = checks["llm_base_config"]
        assert c["severity"] == "error"
        assert "LLM_API_KEY" in c["detail"]

    def test_wrong_provider_key_is_error_with_hint(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        _, checks = _get_checks(admin_client, hdrs)

        c = checks["llm_base_config"]
        assert c["severity"] == "error"
        assert "ANTHROPIC_API_KEY" in c["detail"]

    def test_provider_model_family_mismatch_is_error(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_API_KEY", "sk-xxx")
        monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "claude-opus-4")
        _, checks = _get_checks(admin_client, hdrs)

        c = checks["intelligence_llm_config"]
        assert c["severity"] == "error"
        assert "claude-opus-4" in c["detail"]
        assert "INTELLIGENCE_LLM_PROVIDER is empty" in c["detail"]

    def test_unknown_model_family_is_warning(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_API_KEY", "sk-xxx")
        monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "totally-unknown-model")
        _, checks = _get_checks(admin_client, hdrs)

        assert checks["intelligence_llm_config"]["severity"] == "warning"

    def test_non_reasoning_model_is_blocked(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_API_KEY", "sk-xxx")
        monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-4o-mini")
        _, checks = _get_checks(admin_client, hdrs)

        c = checks["intelligence_llm_config"]
        assert c["severity"] == "blocked"
        assert "capability_hierarchy_ready" in c["related_pipeline_steps"]

    def test_reasoning_model_with_key_is_ok(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_API_KEY", "sk-xxx")
        monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5.4")
        _, checks = _get_checks(admin_client, hdrs)

        assert checks["intelligence_llm_config"]["severity"] == "ok"

    def test_invalid_timeout_is_warning(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_API_KEY", "sk-xxx")
        monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5.4")
        monkeypatch.setenv("INTELLIGENCE_LLM_TIMEOUT", "abc")
        _, checks = _get_checks(admin_client, hdrs)

        c = checks["intelligence_llm_config"]
        assert c["severity"] == "warning"
        assert "INTELLIGENCE_LLM_TIMEOUT" in c["detail"]

    def test_invalid_max_output_tokens_is_warning(self, admin_client, monkeypatch):
        _, _, hdrs = _setup(admin_client)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_API_KEY", "sk-xxx")
        monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5.4")
        monkeypatch.setenv("INTELLIGENCE_MAX_OUTPUT_TOKENS", "-1")
        _, checks = _get_checks(admin_client, hdrs)

        assert checks["intelligence_llm_config"]["severity"] == "warning"


def _insert_snapshot_and_run(
    db_path, system_id, *, run_type, status, error_details=None,
    decision_method="reasoning_llm",
):
    conn = sqlite3.connect(db_path)
    try:
        now = time.time()
        cur = conn.execute(
            "INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, created_at, completed_at) "
            "VALUES (?, '/tmp/repo', 'deadbeef', 'ready', ?, ?)",
            (system_id, now, now),
        )
        snapshot_id = cur.lastrowid
        conn.execute(
            "INSERT INTO intelligence_runs "
            "(system_id, snapshot_id, run_type, provider, model, prompt_version, "
            " schema_version, decision_method, status, error_details, is_mock, started_at, completed_at) "
            "VALUES (?, ?, ?, 'openai', 'gpt-5.4', 'v1', 'v1', ?, ?, ?, 0, ?, ?)",
            (system_id, snapshot_id, run_type, decision_method, status, error_details, now, now),
        )
        conn.commit()
        return snapshot_id
    finally:
        conn.close()


class TestLastObservedFailures:
    def test_failed_reasoning_run_is_surfaced(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        _insert_snapshot_and_run(
            str(tmp_path / "probe-diag-test.db"), sys["id"],
            run_type="repository_drafts", status="failed",
            error_details="LLM request failed: HTTP 401: invalid api key",
        )

        _, checks = _get_checks(admin_client, hdrs)
        c = checks["llm_last_run"]
        assert c["severity"] == "error"
        assert c["last_observed_error"]["status"] == "failed"
        assert "invalid api key" in c["last_observed_error"]["error"]

        doc = checks["pipeline_documentation_index"]
        assert doc["severity"] == "error"
        assert "invalid api key" in doc["last_observed_error"]["error"]

    def test_no_reasoning_run_is_unknown(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        _, checks = _get_checks(admin_client, hdrs)
        assert checks["llm_last_run"]["severity"] == "unknown"

    def test_failed_run_isolated_per_system(self, admin_client, tmp_path):
        token, sys_a, hdrs_a = _setup(admin_client, "diag-a")
        sys_b = _create_system(admin_client, token, "diag-b")
        hdrs_b = _headers(token, sys_b["id"])
        _insert_snapshot_and_run(
            str(tmp_path / "probe-diag-test.db"), sys_a["id"],
            run_type="repository_drafts", status="failed",
            error_details="timeout",
        )

        _, checks_a = _get_checks(admin_client, hdrs_a)
        _, checks_b = _get_checks(admin_client, hdrs_b)
        assert checks_a["llm_last_run"]["severity"] == "error"
        assert checks_b["llm_last_run"]["severity"] == "unknown"


class TestPipelinePrerequisites:
    def test_no_snapshot_blocks_pipeline_checks(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        _, checks = _get_checks(admin_client, hdrs)

        for check_id in (
            "pipeline_symbol_index",
            "pipeline_entrypoint_index",
            "pipeline_documentation_index",
            "pipeline_understanding_graph",
            "pipeline_capability_hierarchy",
        ):
            assert checks[check_id]["severity"] == "blocked", check_id

    def test_snapshot_without_runs_marks_steps_not_run(self, admin_client, tmp_path):
        _, _, hdrs = _setup(admin_client)
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

        _, checks = _get_checks(admin_client, hdrs)
        assert checks["pipeline_symbol_index"]["severity"] == "warning"
        assert checks["pipeline_entrypoint_index"]["severity"] == "warning"
        # Reasoning-required steps stay blocked under the mock provider.
        assert checks["pipeline_documentation_index"]["severity"] == "blocked"
        assert checks["pipeline_understanding_graph"]["severity"] == "blocked"
        assert checks["pipeline_capability_hierarchy"]["severity"] == "blocked"

    def test_completed_runs_are_ok_and_map_to_pipeline_steps(self, admin_client, tmp_path):
        _, _, hdrs = _setup(admin_client)
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
        build = admin_client.post(
            "/repository/system-understanding/build", headers=hdrs
        )
        assert build.status_code == 202, build.text
        build_id = build.json()["id"]

        # Issue #106: build runs asynchronously; poll until it settles.
        deadline = time.time() + 10.0
        while time.time() < deadline:
            status_r = admin_client.get(
                f"/repository/system-understanding/build/{build_id}", headers=hdrs
            )
            assert status_r.status_code == 200, status_r.text
            if status_r.json()["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"Build {build_id} did not settle in time")
        assert status_r.json()["status"] == "completed"

        _, checks = _get_checks(admin_client, hdrs)
        sym = checks["pipeline_symbol_index"]
        assert sym["severity"] == "ok"
        assert sym["related_pipeline_steps"] == ["symbols_indexed"]
        assert checks["pipeline_entrypoint_index"]["severity"] == "ok"
