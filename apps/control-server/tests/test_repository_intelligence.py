"""Tests for Issue #23: Repository Understanding MVP.

Covers: repository configuration, committed-files-only snapshots,
evidence-backed draft generation, intelligence-run audit persistence,
and safety boundaries.
"""

import json
import os
import sqlite3
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
    def test_lists_git_repositories_below_allowed_root(
        self, admin_client, git_repo
    ):
        token = _login(admin_client)
        r = admin_client.get(
            "/repository-candidates",
            headers=_bearer(token),
        )
        assert r.status_code == 200
        assert {
            item["path"] for item in r.json()
        } == {str(git_repo.resolve())}
        assert r.json()[0]["name"] == git_repo.name

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
        files = {f["path"]: f for f in r.json()["files"]}
        assert files[".env"]["inclusion_status"] == "excluded"
        assert files["credentials.json"]["inclusion_status"] == "excluded"
        assert "exclusion policy" in files[".env"]["exclusion_reason"]

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

    def test_snapshot_excludes_staged_uncommitted_files(self, admin_client, git_repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "Staged")
        h = _headers(token, system["id"])

        (git_repo / "staged-secret.txt").write_text("must not be indexed\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "staged-secret.txt"],
            check=True,
            capture_output=True,
        )
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": []},
            headers=h,
        )

        r = admin_client.post("/repository/snapshots", headers=h)
        assert r.status_code == 201
        paths = {f["path"] for f in r.json()["files"]}
        assert "staged-secret.txt" not in paths

    def test_snapshot_rejects_repository_outside_allowed_roots(
        self, admin_client, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "OutsideRoot")
        h = _headers(token, system["id"])
        outside = tmp_path.parent / f"{tmp_path.name}-outside"
        outside.mkdir()
        subprocess.run(["git", "init", str(outside)], check=True, capture_output=True)
        admin_client.put(
            "/repository",
            json={"repo_path": str(outside)},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        assert r.json()["status"] == "failed"
        assert "PROBE_REPOSITORY_ROOTS" in r.json()["error_summary"]

    def test_snapshot_rejects_git_directory_outside_allowed_roots(
        self, admin_client, git_repo, tmp_path
    ):
        linked_worktree = tmp_path / "linked-worktree"
        outside_git_dir = tmp_path.parent / f"{tmp_path.name}-external-git"
        subprocess.run(
            [
                "git",
                "clone",
                "--separate-git-dir",
                str(outside_git_dir),
                str(git_repo),
                str(linked_worktree),
            ],
            check=True,
            capture_output=True,
        )

        token = _login(admin_client)
        system = _create_system(admin_client, token, "ExternalGitDir")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(linked_worktree)},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        assert r.json()["status"] == "failed"
        assert "Git directory is outside" in r.json()["error_summary"]

    def test_snapshot_records_oversized_file_as_too_large(
        self, admin_client, git_repo, monkeypatch
    ):
        import app.git_ops as git_ops

        (git_repo / "large.txt").write_text("x" * 128)
        (git_repo / "small.txt").write_text("small file")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "large"],
            check=True,
            capture_output=True,
        )
        monkeypatch.setattr(git_ops, "MAX_FILE_SIZE", 64)

        token = _login(admin_client)
        system = _create_system(admin_client, token, "Oversized")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["large.txt", "small.txt"],
            },
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        body = r.json()
        assert body["status"] == "ready"
        assert body["metadata_only_count"] == 1
        assert len(body["warnings"]) > 0

        file_map = {f["path"]: f for f in body["files"]}
        assert file_map["large.txt"]["inclusion_status"] == "too_large"
        assert "SNAPSHOT_MAX_FILE_SIZE" in file_map["large.txt"]["exclusion_reason"]
        assert file_map["small.txt"]["inclusion_status"] == "indexed"

    def test_snapshot_skips_committed_symlinks(
        self, admin_client, git_repo, tmp_path
    ):
        outside = tmp_path / "outside-secret.txt"
        outside.write_text("secret outside repository\n")
        os.symlink(outside, git_repo / "linked-secret.txt")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "linked-secret.txt"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "symlink"],
            check=True,
            capture_output=True,
        )

        token = _login(admin_client)
        system = _create_system(admin_client, token, "Symlink")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": []},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        files = {f["path"]: f for f in r.json()["files"]}
        assert files["linked-secret.txt"]["inclusion_status"] == "unsupported"
        assert "symlink" in files["linked-secret.txt"]["exclusion_reason"].lower()

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


class TestSnapshotSizeLimits:
    """Tests for Issue #34: Snapshot storage limits separated from LLM context."""

    def test_large_repo_exceeding_old_limit_creates_snapshot(
        self, admin_client, git_repo, monkeypatch
    ):
        """Repos exceeding the old 5 MiB MAX_TOTAL_SIZE must still snapshot."""
        import app.git_ops as git_ops

        monkeypatch.setattr(git_ops, "MAX_FILE_SIZE", 1024 * 1024)
        for i in range(10):
            (git_repo / f"file_{i}.py").write_text(f"content = 'data-{i}' * 100\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "many files"],
            check=True, capture_output=True,
        )

        token = _login(admin_client)
        system = _create_system(admin_client, token, "LargeRepo")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": []},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        body = r.json()
        assert body["status"] == "ready"
        assert body["file_count"] > 0

    def test_binary_file_recorded_without_content(
        self, admin_client, git_repo
    ):
        (git_repo / "image.bin").write_bytes(b"\x89PNG\x00\x00\x01\x02")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "binary"],
            check=True, capture_output=True,
        )

        token = _login(admin_client)
        system = _create_system(admin_client, token, "BinaryFile")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": []},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        body = r.json()
        assert body["status"] == "ready"

        file_map = {f["path"]: f for f in body["files"]}
        assert file_map["image.bin"]["inclusion_status"] == "binary"
        assert "binary" in file_map["image.bin"]["exclusion_reason"].lower()
        assert body["metadata_only_count"] >= 1

    def test_indexed_size_tracks_content_stored_files_only(
        self, admin_client, git_repo, monkeypatch
    ):
        import app.git_ops as git_ops

        (git_repo / "big.txt").write_text("x" * 200)
        (git_repo / "ok.txt").write_text("small")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "mixed"],
            check=True, capture_output=True,
        )
        monkeypatch.setattr(git_ops, "MAX_FILE_SIZE", 100)

        token = _login(admin_client)
        system = _create_system(admin_client, token, "IndexedSize")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["big.txt", "ok.txt"],
            },
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        body = r.json()
        assert body["status"] == "ready"
        assert body["indexed_size"] < body["total_size"]
        assert body["indexed_size"] == 5  # len("small")

    def test_draft_generation_skips_non_indexed_files(
        self, admin_client, git_repo, monkeypatch
    ):
        """Draft generation must only use files with inclusion_status='indexed'."""
        import app.git_ops as git_ops

        (git_repo / "huge_doc.md").write_text("# Doc\n" + "x" * 200)
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "large doc"],
            check=True, capture_output=True,
        )
        monkeypatch.setattr(git_ops, "MAX_FILE_SIZE", 50)

        token = _login(admin_client)
        system = _create_system(admin_client, token, "SkipNonIndexed")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={
                "repo_path": str(git_repo),
                "include_patterns": ["README.md", "src/**", "huge_doc.md"],
            },
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)
        r = admin_client.post("/repository/drafts/generate", headers=h)
        assert r.status_code == 201
        assert r.json()["intelligence_run"]["status"] == "completed"

    def test_snapshot_warnings_populated(
        self, admin_client, git_repo, monkeypatch
    ):
        import app.git_ops as git_ops

        (git_repo / "big.txt").write_text("x" * 200)
        (git_repo / "data.bin").write_bytes(b"\x00\x01\x02")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "mixed"],
            check=True, capture_output=True,
        )
        monkeypatch.setattr(git_ops, "MAX_FILE_SIZE", 100)

        token = _login(admin_client)
        system = _create_system(admin_client, token, "Warnings")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": []},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        body = r.json()
        assert body["status"] == "ready"
        assert len(body["warnings"]) >= 2
        warning_text = " ".join(body["warnings"])
        assert "size limit" in warning_text.lower() or "exceeded" in warning_text.lower()
        assert "binary" in warning_text.lower()

    def test_snapshot_status_failed_not_shown_as_success(
        self, admin_client, tmp_path
    ):
        """HTTP 201 with status='failed' must not be treated as success."""
        token = _login(admin_client)
        system = _create_system(admin_client, token, "FailedNotSuccess")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(tmp_path / "nonexistent")},
            headers=h,
        )
        r = admin_client.post("/repository/snapshots", headers=h)
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "failed"
        assert body["error_summary"] is not None


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

    def test_generation_uses_persisted_snapshot_content(
        self, admin_client, git_repo
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "PersistedContent")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)

        # Draft generation must not re-read the configured repository.
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo.parent / "missing-repo")},
            headers=h,
        )
        r = admin_client.post("/repository/drafts/generate", headers=h)
        assert r.status_code == 201
        assert r.json()["intelligence_run"]["status"] == "completed"

    def test_generation_does_not_fall_back_to_older_snapshot(
        self, admin_client, git_repo
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NoStaleSnapshot")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)

        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo.parent / "missing-repo")},
            headers=h,
        )
        failed = admin_client.post("/repository/snapshots", headers=h).json()
        assert failed["status"] == "failed"

        r = admin_client.post("/repository/drafts/generate", headers=h)
        assert r.status_code == 400
        assert "Latest snapshot is not ready" in r.json()["detail"]

    def test_non_reasoning_model_is_rejected_and_audited(
        self, admin_client, git_repo, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token, "NonReasoning")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)

        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("LLM_API_KEY", "unused")
        r = admin_client.post("/repository/drafts/generate", headers=h)
        assert r.status_code == 201
        run = r.json()["intelligence_run"]
        assert run["status"] == "failed"
        assert "reasoning model" in run["error_details"]
        assert r.json()["system_profile_draft"] is None
        assert r.json()["feature_drafts"] == []

        latest = admin_client.get("/repository/drafts/latest", headers=h).json()
        assert latest["intelligence_run"]["id"] == run["id"]
        assert latest["intelligence_run"]["status"] == "failed"
        assert latest["system_profile_draft"] is None

    def test_invalid_evidence_range_fails_without_persisting_drafts(
        self, admin_client, git_repo, monkeypatch
    ):
        from app.llm import LLMClient
        from app.routes import project_intelligence

        class InvalidEvidenceClient(LLMClient):
            def generate_text(self, messages, *, temperature=None, max_tokens=None):
                return json.dumps(
                    {
                        "system_profile": {
                            "name": "Invalid",
                            "purpose": "Invalid evidence test",
                            "target_users": ["developers"],
                            "stakeholder_value": "validation",
                            "constraints": [],
                            "success_criteria": [],
                            "evidence": [
                                {
                                    "path": "README.md",
                                    "start_line": 1,
                                    "end_line": 9999,
                                    "summary": "outside file bounds",
                                }
                            ],
                        },
                        "features": [
                            {
                                "feature_id": "invalid",
                                "name": "Invalid",
                                "summary": "Invalid",
                                "user_value": "Invalid",
                                "success_criteria": [],
                                "risks": [],
                                "evidence": [
                                    {
                                        "path": "README.md",
                                        "start_line": 1,
                                        "end_line": 1,
                                        "summary": "valid range",
                                    }
                                ],
                            }
                        ],
                    }
                )

        token = _login(admin_client)
        system = _create_system(admin_client, token, "InvalidEvidence")
        h = _headers(token, system["id"])
        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=h,
        )
        admin_client.post("/repository/snapshots", headers=h)

        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-5.4")
        monkeypatch.setenv("LLM_API_KEY", "unused")
        monkeypatch.setattr(
            project_intelligence,
            "create_llm_client",
            lambda _config: InvalidEvidenceClient(),
        )
        r = admin_client.post("/repository/drafts/generate", headers=h)
        assert r.status_code == 201
        body = r.json()
        assert body["intelligence_run"]["status"] == "failed"
        assert "line range exceeds" in body["intelligence_run"]["error_details"]
        assert body["system_profile_draft"] is None
        assert body["feature_drafts"] == []

    def test_drafts_are_system_scoped(self, admin_client, git_repo):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "DraftScopeA")
        sys_b = _create_system(admin_client, token, "DraftScopeB")
        headers_a = _headers(token, sys_a["id"])
        headers_b = _headers(token, sys_b["id"])

        admin_client.put(
            "/repository",
            json={"repo_path": str(git_repo), "include_patterns": ["README.md"]},
            headers=headers_a,
        )
        admin_client.post("/repository/snapshots", headers=headers_a)
        admin_client.post("/repository/drafts/generate", headers=headers_a)

        other = admin_client.get(
            "/repository/drafts/latest", headers=headers_b
        ).json()
        assert other["system_id"] == sys_b["id"]
        assert other["snapshot"] is None
        assert other["intelligence_run"] is None
        assert other["system_profile_draft"] is None
        assert other["feature_drafts"] == []


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
        from app.git_ops import _is_safe_git_path

        assert not _is_safe_git_path("../etc/passwd")
        assert not _is_safe_git_path("src/../../etc/passwd")
        assert not _is_safe_git_path("/absolute/path")

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


def test_draft_generation_defaults_to_model_max_output_tokens(monkeypatch):
    from app.draft_generator import DEFAULT_MAX_OUTPUT_TOKENS, generate_drafts
    from app.git_ops import IndexedFile
    from app.llm import LLMClient, LLMConfig

    class CapturingClient(LLMClient):
        max_tokens = None

        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            self.max_tokens = max_tokens
            return "{}"

    monkeypatch.delenv("INTELLIGENCE_MAX_OUTPUT_TOKENS", raising=False)
    client = CapturingClient()
    generate_drafts(
        client,
        LLMConfig(
            provider="openai",
            model="gpt-5.4-mini",
            api_key="unused",
            base_url="",
            timeout=30,
        ),
        [
            IndexedFile(
                path="README.md",
                source_type="documentation",
                size_bytes=6,
                content_hash="hash",
                content=b"hello\n",
            )
        ],
    )

    assert DEFAULT_MAX_OUTPUT_TOKENS == 128_000
    assert client.max_tokens == 128_000


class TestRepositorySchemaMigration:
    def test_adds_snapshot_content_column(self, tmp_path, monkeypatch):
        db_file = tmp_path / "old-repository-schema.db"
        conn = sqlite3.connect(db_file)
        conn.execute(
            """
            CREATE TABLE snapshot_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                source_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT
            )
            """
        )
        conn.close()

        monkeypatch.setenv("PROBE_DB_PATH", str(db_file))
        from app.db import get_conn, init_db

        init_db()
        with get_conn() as migrated:
            columns = {
                row["name"]
                for row in migrated.execute("PRAGMA table_info(snapshot_files)")
            }
        assert "content" in columns
        assert "inclusion_status" in columns
        assert "exclusion_reason" in columns

    def test_adds_snapshot_indexed_size_column(self, tmp_path, monkeypatch):
        db_file = tmp_path / "old-snapshot-schema.db"
        conn = sqlite3.connect(db_file)
        conn.execute(
            """
            CREATE TABLE repository_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                system_id INTEGER NOT NULL,
                repo_path TEXT NOT NULL DEFAULT '',
                commit_sha TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'indexing',
                file_count INTEGER NOT NULL DEFAULT 0,
                total_size INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT,
                created_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE snapshot_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                source_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT,
                content BLOB NOT NULL DEFAULT X''
            )
            """
        )
        conn.close()

        monkeypatch.setenv("PROBE_DB_PATH", str(db_file))
        from app.db import get_conn, init_db

        init_db()
        with get_conn() as migrated:
            snap_columns = {
                row["name"]
                for row in migrated.execute(
                    "PRAGMA table_info(repository_snapshots)"
                )
            }
            file_columns = {
                row["name"]
                for row in migrated.execute("PRAGMA table_info(snapshot_files)")
            }
        assert "indexed_size" in snap_columns
        assert "metadata_only_count" in snap_columns
        assert "warnings" in snap_columns
        assert "inclusion_status" in file_columns
        assert "exclusion_reason" in file_columns
