"""Tests for Issue #23: Repository Understanding MVP.

Covers: repository configuration, committed-files-only snapshots,
evidence-backed draft generation, intelligence-run audit persistence,
and safety boundaries.
"""

import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-repo-test.db"))
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


@pytest.fixture
def git_repo(tmp_path):
    """Create a small git repo with committed files for snapshot testing."""
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )

    (repo / "README.md").write_text("# Test Project\n\nA test project for probing.\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("def hello():\n    return 'world'\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_main.py").write_text("def test_hello():\n    assert True\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("# Design\n\nFeature overview.\n")
    (repo / "pyproject.toml").write_text("[project]\nname = 'test'\n")

    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True, capture_output=True,
    )
    return repo


@pytest.fixture
def git_repo_with_secrets(git_repo):
    """A repo that also has a .env file committed (should be excluded)."""
    (git_repo / ".env").write_text("SECRET_KEY=abc123\n")
    (git_repo / "credentials.json").write_text('{"key": "secret"}\n')
    subprocess.run(
        ["git", "-C", str(git_repo), "add", "."],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "add secrets"],
        check=True, capture_output=True,
    )
    return git_repo


# ---------------------------------------------------------------------------
# Repository configuration tests
# ---------------------------------------------------------------------------


class TestRepositoryConfig:
    def test_get_returns_none_when_not_configured(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Unconfigured")
        r = admin_client.get(
            "/repository", headers=_headers(token, system["id"])
        )
        assert r.status_code == 200
        assert r.json() is None

    def test_put_creates_config(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Configured")
        r = admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**"],
                "exclude_patterns": [".env"],
            },
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["repo_path"] == str(git_repo)
        assert body["include_patterns"] == ["README.md", "src/**"]
        assert body["system_id"] == system["id"]

    def test_put_updates_existing_config(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Update")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo)},
            headers=h,
        )
        r = admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["docs/**"]},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["include_patterns"] == ["docs/**"]

    def test_get_returns_saved_config(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Saved")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["src/**"]},
            headers=h,
        )
        r = admin_client.get("/repository", headers=h)
        assert r.status_code == 200
        assert r.json()["include_patterns"] == ["src/**"]

    def test_config_is_system_scoped(self, admin_client, git_repo):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "A")
        sys_b = _create_system(admin_client, token, "B")
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["src/**"]},
            headers=_headers(token, sys_a["id"]),
        )
        r = admin_client.get(
            "/repository", headers=_headers(token, sys_b["id"])
        )
        assert r.json() is None


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------


class TestSnapshots:
    def test_snapshot_requires_config(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NoConfig")
        r = admin_client.post(
            "/repository/snapshots",
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 400
        assert "not configured" in r.json()["detail"].lower()

    def test_snapshot_reads_committed_files(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Snapshot")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**", "docs/**", "tests/**"],
            },
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "ready"
        assert body["file_count"] >= 4
        assert body["commit_sha"] != ""
        paths = {f["path"] for f in body["files"]}
        assert "README.md" in paths
        assert "src/main.py" in paths
        assert "tests/test_main.py" in paths
        assert "docs/design.md" in paths

    def test_snapshot_classifies_source_types(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Classify")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**", "docs/**", "tests/**"],
            },
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        files = {f["path"]: f["source_type"] for f in r.json()["files"]}
        assert files["README.md"] == "documentation"
        assert files["docs/design.md"] == "documentation"
        assert files["src/main.py"] == "source"
        assert files["tests/test_main.py"] == "test"

    def test_snapshot_excludes_secrets(self, admin_client, git_repo_with_secrets):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Secrets")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo_with_secrets),
                "include_patterns": [],
            },
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        paths = {f["path"] for f in r.json()["files"]}
        assert ".env" not in paths
        assert "credentials.json" not in paths

    def test_snapshot_excludes_uncommitted_changes(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Uncommitted")
        h = _headers(token, system["id"])

        (git_repo / "src" / "main.py").write_text(
            "def hello():\n    return 'MODIFIED'\n"
        )
        (git_repo / "untracked.py").write_text("print('should not appear')\n")

        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["src/**"],
            },
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        body = r.json()
        assert body["status"] == "ready"
        paths = {f["path"] for f in body["files"]}
        assert "untracked.py" not in paths

        from app.git_ops import read_file_at_commit

        content = read_file_at_commit(
            str(git_repo), body["commit_sha"], "src/main.py"
        )
        assert b"MODIFIED" not in content
        assert b"world" in content

    def test_snapshot_invalid_path(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "InvalidPath")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(tmp_path / "nonexistent")},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        body = r.json()
        assert body["status"] == "failed"
        assert body["error_summary"] is not None

    def test_snapshot_non_git_directory(self, admin_client, tmp_path):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NonGit")
        h = _headers(token, system["id"])
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        admin_client.put(
            "/repository",
            json={"repo_path": str(plain_dir)},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        assert r.json()["status"] == "failed"

    def test_get_latest_snapshot(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Latest")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=h,
        )
        r = admin_client.get("/repository/snapshots/latest", headers=h)
        assert r.json() is None

        admin_client.post("/repository/snapshots", headers=h)
        r = admin_client.get("/repository/snapshots/latest", headers=h)
        assert r.status_code == 200
        assert r.json()["status"] == "ready"

    def test_snapshots_are_system_scoped(self, admin_client, git_repo):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "ScopeA")
        sys_b = _create_system(admin_client, token, "ScopeB")
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=_headers(token, sys_a["id"]),
        )
        admin_client.post(
            "/repository/snapshots", headers=_headers(token, sys_a["id"])
        )
        r = admin_client.get(
            "/repository/snapshots/latest",
            headers=_headers(token, sys_b["id"]),
        )
        assert r.json() is None


# ---------------------------------------------------------------------------
# Draft generation tests
# ---------------------------------------------------------------------------


class TestDraftGeneration:
    def test_generate_requires_snapshot(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NoSnap")
        r = admin_client.post(
            "/repository/drafts/generate",
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 400

    def test_generate_with_mock_provider(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MockDraft")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**", "docs/**"],
            },
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)

        r = admin_client.post("/repository/drafts/generate", headers=h)
        assert r.status_code == 201
        body = r.json()

        run = body["intelligence_run"]
        assert run["status"] == "completed"
        assert run["provider"] == "mock"
        assert run["is_mock"] is True
        assert run["decision_method"] == "reasoning_llm"
        assert run["prompt_version"] == "v1"

        sp = body["system_profile_draft"]
        assert sp is not None
        assert sp["is_mock"] is True
        assert len(sp["evidence"]) > 0

        features = body["feature_drafts"]
        assert len(features) > 0
        for f in features:
            assert f["is_mock"] is True
            assert len(f["evidence"]) > 0

    def test_evidence_paths_exist_in_snapshot(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "EvidenceCheck")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**", "docs/**"],
            },
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        snap = admin_client.get(
            "/repository/snapshots/latest", headers=h
        ).json()
        snapshot_paths = {f["path"] for f in snap["files"]}

        r = admin_client.post("/repository/drafts/generate", headers=h)
        body = r.json()

        for ev in body["system_profile_draft"]["evidence"]:
            assert ev["path"] in snapshot_paths, f"Evidence path {ev['path']} not in snapshot"

        for feature in body["feature_drafts"]:
            for ev in feature["evidence"]:
                assert ev["path"] in snapshot_paths

    def test_get_latest_drafts(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "LatestDraft")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md", "src/**"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        admin_client.post("/repository/drafts/generate", headers=h)

        r = admin_client.get("/repository/drafts/latest", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["system_id"] == system["id"]
        assert body["snapshot"] is not None
        assert body["intelligence_run"] is not None
        assert body["system_profile_draft"] is not None
        assert len(body["feature_drafts"]) > 0

    def test_drafts_do_not_overwrite_system_profile(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NoOverwrite")
        h = _headers(token, system["id"])

        admin_client.put(
            "/system-profile",
            json={
                "name": "Original Profile",
                "purpose": "manually set",
                "target_users": [],
                "stakeholder_value": "",
                "constraints": [],
                "success_criteria": [],
            },
            headers=h,
        )

        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        admin_client.post("/repository/drafts/generate", headers=h)

        r = admin_client.get("/system-profile", headers=h)
        assert r.json()["name"] == "Original Profile"
        assert r.json()["purpose"] == "manually set"


# ---------------------------------------------------------------------------
# Intelligence run audit tests
# ---------------------------------------------------------------------------


class TestIntelligenceRunAudit:
    def test_run_metadata_persisted(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Audit")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        r = admin_client.post("/repository/drafts/generate", headers=h)
        run = r.json()["intelligence_run"]
        assert run["provider"] == "mock"
        assert run["model"] == "mock"
        assert run["prompt_version"] == "v1"
        assert run["schema_version"] == "v1"
        assert run["decision_method"] == "reasoning_llm"
        assert run["started_at"] > 0
        assert run["completed_at"] >= run["started_at"]

    def test_mock_result_visibly_marked(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MockMark")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        r = admin_client.post("/repository/drafts/generate", headers=h)
        body = r.json()
        assert body["intelligence_run"]["is_mock"] is True
        assert body["system_profile_draft"]["is_mock"] is True
        for f in body["feature_drafts"]:
            assert f["is_mock"] is True


# ---------------------------------------------------------------------------
# Git safety tests
# ---------------------------------------------------------------------------


class TestGitSafety:
    def test_path_traversal_rejected(self):
        from app.git_ops import _is_safe_path

        assert not _is_safe_path("../etc/passwd", "/repo")
        assert not _is_safe_path("src/../../etc/passwd", "/repo")
        assert not _is_safe_path("/absolute/path", "/repo")

    def test_classify_source_type(self):
        from app.git_ops import classify_source_type

        assert classify_source_type("README.md") == "documentation"
        assert classify_source_type("docs/design.md") == "documentation"
        assert classify_source_type("src/main.py") == "source"
        assert classify_source_type("tests/test_main.py") == "test"
        assert classify_source_type("pyproject.toml") == "configuration"

    def test_default_exclude_covers_secrets(self):
        from app.git_ops import DEFAULT_EXCLUDE, _matches_patterns

        assert _matches_patterns(".env", DEFAULT_EXCLUDE)
        assert _matches_patterns(".env.local", DEFAULT_EXCLUDE)
        assert _matches_patterns("server.pem", DEFAULT_EXCLUDE)
        assert _matches_patterns("private.key", DEFAULT_EXCLUDE)
        assert _matches_patterns("credentials.json", DEFAULT_EXCLUDE)
        assert _matches_patterns("secrets/api.txt", DEFAULT_EXCLUDE)


# ---------------------------------------------------------------------------
# Legacy mock endpoint
# ---------------------------------------------------------------------------


class TestLegacyMockEndpoint:
    def test_project_intelligence_mock_contract(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "MockContract")
        r = admin_client.get(
            "/project-intelligence",
            headers=_headers(token, system["id"]),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["mock"] is True
        assert body["reasoning_model_required"] is True
        assert body["repository"]["read_policy"] == "committed_files_only"
        assert body["probe_plans"][0]["probe_points"][0]["status"] == "proposed"
        assert body["experiments"][0]["status"] == "draft"
