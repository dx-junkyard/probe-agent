"""Tests for Issue #101: deterministic system settings diagnostics.

Covers GET /system-diagnostics: env/path validation, provider/model
consistency, last observed run failures, pipeline prerequisites, and
System isolation. No check may call an LLM.
"""

import json
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
    return r.cookies.get("probe_session")


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


def _commit_file(repo, path, content, message="change"):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(repo), check=True, capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout.strip()


def _insert_confirmed_understanding(system_id, snapshot_id):
    from app.db import get_conn

    now = time.time()
    understanding = {
        "system_purpose": [{
            "name": "Probe repository inspection",
            "summary": "Inspect repositories and plan probes.",
            "confidence": {"level": "confirmed", "reason": "manual"},
            "evidence": [],
            "why_core": "",
            "related_docs": [],
            "related_apis": [],
            "children": [],
        }],
        "core_capabilities": [{
            "name": "Repository scanning",
            "summary": "Create snapshots and index code.",
            "confidence": {"level": "confirmed", "reason": "manual"},
            "evidence": [],
            "why_core": "",
            "related_docs": [],
            "related_apis": [],
            "children": [],
        }],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
        "probe_flow_candidates": [],
    }
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO interview_session
                (system_id, snapshot_id, title, focus, status, stage,
                 current_understanding, understanding_confirmed_at,
                 understanding_confirmed_by, created_at, updated_at)
            VALUES (?, ?, 'baseline', '', 'open', 'proposal_generation',
                    ?, ?, 'tester', ?, ?)
            """,
            (
                system_id, snapshot_id, json.dumps(understanding),
                now, now, now,
            ),
        )


def _insert_unconfirmed_understanding(system_id, snapshot_id):
    from app.db import get_conn

    now = time.time()
    understanding = {
        "system_purpose": [{
            "name": "Probe repository inspection",
            "summary": "Inspect repositories and plan probes.",
            "confidence": {"level": "likely", "reason": "reasoning"},
            "evidence": [],
            "why_core": "",
            "related_docs": [],
            "related_apis": [],
            "children": [],
        }],
        "core_capabilities": [{
            "name": "Repository scanning",
            "summary": "Create snapshots and index code.",
            "confidence": {"level": "likely", "reason": "reasoning"},
            "evidence": [],
            "why_core": "",
            "related_docs": [],
            "related_apis": [],
            "children": [],
        }],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
        "probe_flow_candidates": [],
    }
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO interview_session
                (system_id, snapshot_id, title, focus, status, stage,
                 current_understanding, understanding_confirmed_at,
                 understanding_confirmed_by, created_at, updated_at)
            VALUES (?, ?, 'candidate', '', 'open', 'purpose_confirmation',
                    ?, NULL, NULL, ?, ?)
            """,
            (system_id, snapshot_id, json.dumps(understanding), now, now),
        )


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

    def test_checks_carry_fix_navigation(self, admin_client):
        """Issue #115: every check has a finite fix_kind, and navigate checks
        point at a Dashboard page."""
        _, _, hdrs = _setup(admin_client)
        _, checks = _get_checks(admin_client, hdrs)

        for c in checks.values():
            assert c["fix_kind"] in ("navigate", "dialog"), c["check_id"]
            if c["fix_kind"] == "navigate":
                assert c["fix_page"], c["check_id"]

        # Repository config (unconfigured) routes to the repository config form.
        repo = checks["repository_config"]
        assert repo["fix_kind"] == "navigate"
        assert repo["fix_page"] == "/repository"
        assert repo["fix_anchor"] == "repo-config"

        # Snapshot creation routes to the snapshot creation control.
        snap = checks["snapshot_status"]
        assert snap["fix_kind"] == "navigate"
        assert snap["fix_anchor"] == "snapshot-create"

        # Env-only LLM configuration is fixed via a dialog, not a page.
        assert checks["intelligence_llm_config"]["fix_kind"] == "dialog"
        assert checks["llm_base_config"]["fix_kind"] == "dialog"

    def test_messages_are_japanese(self, admin_client):
        """Diagnostics remediation/impact text is Japanese (Issue #115)."""
        _, _, hdrs = _setup(admin_client)
        _, checks = _get_checks(admin_client, hdrs)

        repo = checks["repository_config"]
        assert "リポジトリ" in repo["remediation"]
        assert "設定されていません" in repo["detail"]


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
        assert "0 件" in c["detail"]


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
        assert "INTELLIGENCE_LLM_PROVIDER が未設定" in c["detail"]

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
        assert doc["severity"] == "warning"
        assert "実行されていません" in doc["detail"]

    def test_evidence_validation_failure_has_specific_remediation(
        self, admin_client, tmp_path
    ):
        _, sys, hdrs = _setup(admin_client)
        _insert_snapshot_and_run(
            str(tmp_path / "probe-diag-test.db"), sys["id"],
            run_type="interview_dialogue", status="failed",
            error_details=(
                "Question evidence validation failed: question evidence lines "
                "100-182 are not contained in any known span in "
                "'apps/control-server/app/documentation_indexer.py'"
            ),
        )

        _, checks = _get_checks(admin_client, hdrs)
        c = checks["llm_last_run"]
        assert c["severity"] == "error"
        assert "evidence_refs" in c["remediation"]
        assert "API キー・モデル ID・タイムアウト設定ではなく" in c["remediation"]
        assert c["related_env"] == []
        assert c["related_pages"] == ["/interview"]
        assert c["related_pipeline_steps"] == ["interview_dialogue"]
        assert c["fix_page"] == "/interview"

    def test_no_reasoning_run_is_unknown(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        _, checks = _get_checks(admin_client, hdrs)
        assert checks["llm_last_run"]["severity"] == "unknown"

    def test_no_reasoning_run_is_presented_as_informational(
        self, admin_client, tmp_path,
    ):
        """The "no run recorded yet" state is a connectivity note, not a
        defect: it must not present itself as the cause of other warnings
        (e.g. an empty capability hierarchy, whose build is deterministic),
        must name the operations that actually record reasoning runs, and
        must be scoped to the reasoning-dependent pipeline steps so
        consumers can rank it below actionable checks sharing its anchor."""
        _, sys, hdrs = _setup(admin_client)
        # A ready snapshot with only a deterministic run: reasoning is still
        # unrecorded for this snapshot.
        _insert_snapshot_and_run(
            str(tmp_path / "probe-diag-test.db"), sys["id"],
            run_type="capability_hierarchy", status="completed",
            decision_method="deterministic",
        )
        _, checks = _get_checks(admin_client, hdrs)
        c = checks["llm_last_run"]
        assert "他の warning / error 診断の原因を示すものではありません" in c["detail"]
        assert "ドラフト生成" in c["remediation"]
        assert "Interview" in c["remediation"]
        assert "先にそちらの『次の操作』を実施してください" in c["remediation"]
        assert c["related_pipeline_steps"] == [
            "documentation_claims_scanned",
            "docs_code_reconciled",
        ]

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

    def test_failed_reasoning_run_from_older_snapshot_is_not_current_error(
        self, admin_client, tmp_path,
    ):
        """A stale Interview failure must not make a newer build red."""
        _, sys, hdrs = _setup(admin_client)
        _insert_snapshot_and_run(
            str(tmp_path / "probe-diag-test.db"), sys["id"],
            run_type="understanding_review", status="failed",
            error_details="old response validation failure",
        )
        conn = sqlite3.connect(str(tmp_path / "probe-diag-test.db"))
        try:
            now = time.time()
            conn.execute(
                """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/repo', 'newer-snapshot', 'ready', ?, ?)""",
                (sys["id"], now, now),
            )
            conn.commit()
        finally:
            conn.close()

        _, checks = _get_checks(admin_client, hdrs)
        assert checks["llm_last_run"]["severity"] == "unknown"


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
        assert checks["pipeline_documentation_index"]["severity"] == "warning"
        # Reasoning-required steps stay blocked under the mock provider.
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
            if status_r.json()["status"] in ("completed", "partial", "failed", "cancelled"):
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"Build {build_id} did not settle in time")
        # Reasoning steps stay blocked with the mock provider (Issue #109),
        # so the job is partial while the deterministic runs completed.
        assert status_r.json()["status"] == "partial"

        _, checks = _get_checks(admin_client, hdrs)
        sym = checks["pipeline_symbol_index"]
        assert sym["severity"] == "ok"
        assert sym["related_pipeline_steps"] == ["symbols_indexed"]
        assert checks["pipeline_entrypoint_index"]["severity"] == "ok"


_PURPOSE_MODULE = (
    '"""Module docstring.\n'
    '\n'
    'probe-agent:\n'
    '  role: Coordinates the whole system\n'
    '  capability: core-flow\n'
    '  element_type: system\n'
    '  system_purpose: Automate the thing end to end\n'
    '"""\n'
    '\n'
    'def run():\n'
    '    """Run it.\n'
    '\n'
    '    probe-agent:\n'
    '      role: Runs the core flow\n'
    '      capability: core-flow\n'
    '      element_type: core\n'
    '    """\n'
    '    return 1\n'
)


def _make_purpose_repo(tmp_path, name="purpose-repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "core.py").write_text(_PURPOSE_MODULE)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return repo


class TestSystemPurposeAndCapabilities:
    """Issue #120: System Purpose / Core Capabilities are health checks,

    not just optional page content, so an all-complete pipeline with no
    purpose or capabilities still surfaces a warning instead of `ok`.
    """

    def test_no_snapshot_is_blocked(self, admin_client):
        _, _, hdrs = _setup(admin_client)
        _, checks = _get_checks(admin_client, hdrs)
        assert checks["system_purpose"]["severity"] == "blocked"
        assert checks["system_capabilities"]["severity"] == "blocked"

    def test_snapshot_without_hierarchy_is_warning(self, admin_client, tmp_path):
        _, _, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)

        _, checks = _get_checks(admin_client, hdrs)
        purpose = checks["system_purpose"]
        caps = checks["system_capabilities"]
        assert purpose["severity"] == "warning"
        assert purpose["fix_kind"] == "navigate"
        assert purpose["fix_page"] == "/interview"
        assert purpose["fix_anchor"] == "interview-purpose"
        assert caps["severity"] == "warning"
        assert caps["fix_anchor"] == "interview-capabilities"

    def test_unconfirmed_understanding_is_reported_as_confirmation_needed(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap.status_code == 201, snap.text
        _insert_unconfirmed_understanding(sys["id"], snap.json()["id"])

        _, checks = _get_checks(admin_client, hdrs)
        purpose = checks["system_purpose"]
        caps = checks["system_capabilities"]
        assert purpose["severity"] == "warning"
        assert "未確認" in purpose["detail"]
        assert "候補" in purpose["detail"]
        assert purpose["fix_anchor"] == "interview-purpose"
        assert caps["severity"] == "warning"
        assert "未確認" in caps["detail"]
        assert "候補" in caps["detail"]
        assert caps["fix_anchor"] == "interview-capabilities"

    def test_confirmed_baseline_survives_unrelated_source_change(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap1 = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap1.status_code == 201, snap1.text
        _insert_confirmed_understanding(sys["id"], snap1.json()["id"])

        sha2 = _commit_file(repo, "main.py", "def run():\n    return 2\n", "implementation")
        snap2 = admin_client.post("/repository/snapshots", json={"commit_sha": sha2}, headers=hdrs)
        assert snap2.status_code == 201, snap2.text

        _, checks = _get_checks(admin_client, hdrs)
        purpose = checks["system_purpose"]
        caps = checks["system_capabilities"]
        assert purpose["severity"] == "ok"
        assert "確認済み理解を再利用できます" in purpose["detail"]
        assert caps["severity"] == "ok"
        assert "確認済み理解を再利用できます" in caps["detail"]

    def test_confirmed_baseline_warns_when_docs_change(self, admin_client, tmp_path):
        _, sys, hdrs = _setup(admin_client)
        repo, sha = _init_git_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
            headers=hdrs,
        )
        snap1 = admin_client.post("/repository/snapshots", json={"commit_sha": sha}, headers=hdrs)
        assert snap1.status_code == 201, snap1.text
        _insert_confirmed_understanding(sys["id"], snap1.json()["id"])

        sha2 = _commit_file(repo, "README.md", "# Test\n\nNew purpose wording.\n", "docs")
        snap2 = admin_client.post("/repository/snapshots", json={"commit_sha": sha2}, headers=hdrs)
        assert snap2.status_code == 201, snap2.text

        _, checks = _get_checks(admin_client, hdrs)
        purpose = checks["system_purpose"]
        caps = checks["system_capabilities"]
        assert purpose["severity"] == "warning"
        assert "README.md" in purpose["detail"]
        assert purpose["fix_anchor"] == "interview-purpose"
        assert caps["severity"] == "warning"
        assert "README.md" in caps["detail"]
        assert caps["fix_anchor"] == "interview-capabilities"

    def test_source_authored_purpose_and_capability_is_ok(self, admin_client, tmp_path):
        _, _, hdrs = _setup(admin_client)
        repo = _make_purpose_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["src/**"]},
            headers=hdrs,
        )
        admin_client.post("/repository/snapshots", headers=hdrs)
        idx = admin_client.post("/repository/symbols/index", headers=hdrs)
        assert idx.status_code == 201, idx.text
        gen = admin_client.post("/repository/capability-hierarchy/generate", headers=hdrs)
        assert gen.status_code == 201, gen.text

        _, checks = _get_checks(admin_client, hdrs)
        assert checks["system_purpose"]["severity"] == "ok"
        assert checks["system_capabilities"]["severity"] == "ok"

    def test_isolated_per_system(self, admin_client, tmp_path):
        token, _, hdrs_a = _setup(admin_client, name="purpose-a")
        sys_b = _create_system(admin_client, token, "purpose-b")
        hdrs_b = _headers(token, sys_b["id"])

        repo = _make_purpose_repo(tmp_path)
        admin_client.put(
            "/repository",
            json={"repo_path": str(repo), "include_patterns": ["src/**"]},
            headers=hdrs_a,
        )
        admin_client.post("/repository/snapshots", headers=hdrs_a)
        admin_client.post("/repository/symbols/index", headers=hdrs_a)
        admin_client.post("/repository/capability-hierarchy/generate", headers=hdrs_a)

        _, checks_a = _get_checks(admin_client, hdrs_a)
        _, checks_b = _get_checks(admin_client, hdrs_b)
        assert checks_a["system_purpose"]["severity"] == "ok"
        assert checks_b["system_purpose"]["severity"] == "blocked"
