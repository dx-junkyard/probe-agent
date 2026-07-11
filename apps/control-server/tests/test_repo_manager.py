"""Tests for the managed repository manager (Issue #216, sub-task 2).

Covers: fail-closed behavior when GIT_REPOSITORY_ROOT is unset, mirror
clone/fetch against a local `file://` bare "remote", credential handling
(no token ever lands in `.git/config` or `git remote -v`, and sanitized
error messages), pinned-SHA job worktrees, cleanup (single job and
expired-job sweep), per-connection mutual exclusion, the `sync` /
`repository-status` API endpoints, `git_ops._allowed_repository_roots`'s
additive GIT_REPOSITORY_ROOT behavior, and that the "remote" bare repo is
never mutated by worktree/cleanup operations.
"""

import json
import os
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import repo_manager
from app.repo_manager import RepoManagerError


# --- git fixture helpers -----------------------------------------------------


def _init_bare_remote(bare_dir):
    bare_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare_dir)],
        check=True, capture_output=True,
    )


def _seed_remote(bare_dir, work_dir):
    subprocess.run(["git", "clone", str(bare_dir), str(work_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work_dir), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work_dir), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    (work_dir / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(work_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work_dir), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work_dir), "push", "origin", "main"], check=True, capture_output=True)


def _add_commit_and_push(work_dir, filename, content, message):
    (work_dir / filename).write_text(content)
    subprocess.run(["git", "-C", str(work_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work_dir), "commit", "-m", message], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work_dir), "push", "origin", "main"], check=True, capture_output=True)


def _head_sha(work_dir):
    return subprocess.run(
        ["git", "-C", str(work_dir), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _refs(bare_dir):
    return subprocess.run(
        ["git", "-C", str(bare_dir), "show-ref"],
        check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture
def bare_remote(tmp_path):
    bare_dir = tmp_path / "remote-root" / "acme" / "widgets.git"
    work_dir = tmp_path / "remote-work"
    _init_bare_remote(bare_dir)
    _seed_remote(bare_dir, work_dir)
    return bare_dir, work_dir


@pytest.fixture
def managed_root(tmp_path, monkeypatch):
    root = tmp_path / "managed-root"
    monkeypatch.setenv("GIT_REPOSITORY_ROOT", str(root))
    monkeypatch.setenv("GIT_CLONE_TIMEOUT", "60")
    monkeypatch.setenv("GIT_FETCH_TIMEOUT", "60")
    return root


@pytest.fixture
def connection_row(bare_remote):
    bare_dir, _work_dir = bare_remote
    return {"id": 1, "clone_url": f"file://{bare_dir}"}


DUMMY_TOKEN = "dummy-test-installation-token-abc123"


# --- fail closed --------------------------------------------------------------


class TestFailClosed:
    def test_mirror_path_fails_without_root(self, monkeypatch):
        monkeypatch.delenv("GIT_REPOSITORY_ROOT", raising=False)
        with pytest.raises(RepoManagerError):
            repo_manager.mirror_path(1)

    def test_ensure_mirror_fails_without_root(self, monkeypatch, connection_row):
        monkeypatch.delenv("GIT_REPOSITORY_ROOT", raising=False)
        with pytest.raises(RepoManagerError):
            repo_manager.ensure_mirror(connection_row, DUMMY_TOKEN)

    def test_create_job_worktree_fails_without_root(self, monkeypatch):
        monkeypatch.delenv("GIT_REPOSITORY_ROOT", raising=False)
        with pytest.raises(RepoManagerError):
            repo_manager.create_job_worktree(1, 1, "a" * 40)

    def test_cleanup_expired_jobs_fails_without_root(self, monkeypatch):
        monkeypatch.delenv("GIT_REPOSITORY_ROOT", raising=False)
        with pytest.raises(RepoManagerError):
            repo_manager.cleanup_expired_jobs()


# --- ensure_mirror -------------------------------------------------------


class TestEnsureMirror:
    def test_initial_clone_then_fetch_picks_up_new_commit(self, managed_root, bare_remote, connection_row):
        bare_dir, work_dir = bare_remote
        repo_manager.ensure_mirror(connection_row, DUMMY_TOKEN)
        mirror = repo_manager.mirror_path(1)
        assert os.path.isdir(os.path.join(mirror, ".git"))
        assert os.path.isfile(os.path.join(mirror, "README.md"))

        initial_sha = repo_manager.resolve_mirror_branch_sha(1, "main")
        assert initial_sha == _head_sha(work_dir)

        _add_commit_and_push(work_dir, "second.txt", "more\n", "second commit")

        repo_manager.ensure_mirror(connection_row, DUMMY_TOKEN)  # fetch path
        new_sha = repo_manager.resolve_mirror_branch_sha(1, "main")
        assert new_sha != initial_sha
        assert new_sha == _head_sha(work_dir)

    def test_resolve_remote_branch_sha_matches_local(self, managed_root, bare_remote, connection_row):
        _bare_dir, work_dir = bare_remote
        sha = repo_manager.resolve_remote_branch_sha(connection_row, DUMMY_TOKEN, "main")
        assert sha == _head_sha(work_dir)


# --- credential safety ---------------------------------------------------


class TestCredentialSafety:
    def test_git_config_and_remote_v_have_no_token(self, managed_root, bare_remote, connection_row):
        secret = "sekrit-test-token-should-not-leak-xyz987"
        repo_manager.ensure_mirror(connection_row, secret)
        mirror = repo_manager.mirror_path(1)

        config_text = open(os.path.join(mirror, ".git", "config")).read()
        assert secret not in config_text

        result = subprocess.run(
            ["git", "-C", mirror, "remote", "-v"], capture_output=True, text=True, check=True,
        )
        assert secret not in result.stdout

    def test_stderr_sanitized_before_raising(self):
        secret = "sekrit-in-fake-stderr-9988"

        class FakeResult:
            stderr = f"fatal: authentication failed with token {secret}".encode()

        message = repo_manager._stderr(FakeResult(), secret)
        assert secret not in message
        assert "***" in message

    def test_sync_style_failure_does_not_leak_token(self, managed_root, tmp_path):
        # clone_url points nowhere -> ensure_mirror's `git clone` fails.
        row = {"id": 2, "clone_url": f"file://{tmp_path / 'does-not-exist.git'}"}
        secret = "leaking-token-should-not-appear-in-error"
        with pytest.raises(RepoManagerError) as excinfo:
            repo_manager.ensure_mirror(row, secret)
        assert secret not in str(excinfo.value)


# --- job worktrees -------------------------------------------------------


class TestJobWorktree:
    def test_create_job_worktree_pinned_sha(self, managed_root, bare_remote, connection_row):
        repo_manager.ensure_mirror(connection_row, DUMMY_TOKEN)
        sha = repo_manager.resolve_mirror_branch_sha(1, "main")

        worktree = repo_manager.create_job_worktree(1, 100, sha)
        assert os.path.isdir(worktree)
        head = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert head == sha

    def test_create_job_worktree_requires_existing_mirror(self, managed_root):
        with pytest.raises(RepoManagerError):
            repo_manager.create_job_worktree(1, 101, "a" * 40)

    def test_worktree_and_cleanup_leave_remote_unchanged(self, managed_root, bare_remote, connection_row):
        bare_dir, _work_dir = bare_remote
        before = _refs(bare_dir)

        repo_manager.ensure_mirror(connection_row, DUMMY_TOKEN)
        sha = repo_manager.resolve_mirror_branch_sha(1, "main")
        repo_manager.create_job_worktree(1, 400, sha)
        repo_manager.cleanup_job_worktree(1, 400)

        after = _refs(bare_dir)
        assert before == after


# --- validation ------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize("bad_id", ["1", 1.5, True, -1, 0])
    def test_non_positive_int_id_rejected(self, managed_root, bad_id):
        with pytest.raises(RepoManagerError):
            repo_manager.mirror_path(bad_id)

    @pytest.mark.parametrize("bad_sha", ["not-a-sha", "abc", "", "G" * 40, "A" * 40])
    def test_invalid_sha_rejected(self, managed_root, bad_sha):
        with pytest.raises(RepoManagerError):
            repo_manager.create_job_worktree(1, 1, bad_sha)

    @pytest.mark.parametrize(
        "bad_branch",
        ["--upload-pack=evil", "..", "-x", "a/../b", "a/-x", "", "a b"],
    )
    def test_invalid_branch_rejected(self, managed_root, connection_row, bad_branch):
        with pytest.raises(RepoManagerError):
            repo_manager.resolve_remote_branch_sha(connection_row, DUMMY_TOKEN, bad_branch)


# --- cleanup -----------------------------------------------------------------


class TestCleanup:
    def test_cleanup_job_worktree_removes_directory_and_registration(
        self, managed_root, bare_remote, connection_row
    ):
        repo_manager.ensure_mirror(connection_row, DUMMY_TOKEN)
        sha = repo_manager.resolve_mirror_branch_sha(1, "main")
        worktree = repo_manager.create_job_worktree(1, 200, sha)

        repo_manager.cleanup_job_worktree(1, 200)
        assert not os.path.exists(worktree)

        mirror = repo_manager.mirror_path(1)
        listing = subprocess.run(
            ["git", "-C", mirror, "worktree", "list"], capture_output=True, text=True, check=True,
        ).stdout
        assert worktree not in listing

    def test_cleanup_job_worktree_idempotent_when_missing(self, managed_root, bare_remote, connection_row):
        repo_manager.ensure_mirror(connection_row, DUMMY_TOKEN)
        repo_manager.cleanup_job_worktree(1, 999)  # never created; must not raise

    def test_cleanup_expired_jobs_removes_only_stale(self, managed_root, bare_remote, connection_row):
        repo_manager.ensure_mirror(connection_row, DUMMY_TOKEN)
        sha = repo_manager.resolve_mirror_branch_sha(1, "main")

        stale_worktree = repo_manager.create_job_worktree(1, 300, sha)
        fresh_worktree = repo_manager.create_job_worktree(1, 301, sha)

        stale_dir = repo_manager.job_dir(300)
        old_time = time.time() - 999999
        os.utime(stale_dir, (old_time, old_time))

        removed = repo_manager.cleanup_expired_jobs()

        assert stale_dir in removed
        assert not os.path.exists(stale_worktree)
        assert os.path.exists(fresh_worktree)


# --- connection_lock -----------------------------------------------------


class TestConnectionLock:
    def test_serializes_concurrent_callers(self, managed_root):
        order = []
        lock_entered = threading.Event()
        proceed = threading.Event()

        def first():
            with repo_manager.connection_lock(1):
                order.append("first-start")
                lock_entered.set()
                proceed.wait(timeout=2)
                order.append("first-end")

        def second():
            lock_entered.wait(timeout=2)
            with repo_manager.connection_lock(1):
                order.append("second-start")

        t1 = threading.Thread(target=first)
        t2 = threading.Thread(target=second)
        t1.start()
        assert lock_entered.wait(timeout=2)
        t2.start()
        time.sleep(0.1)  # give second a chance to attempt to acquire and block
        assert order == ["first-start"]
        proceed.set()
        t1.join(timeout=2)
        t2.join(timeout=2)
        assert order == ["first-start", "first-end", "second-start"]


# --- git_ops additive roots ---------------------------------------------


class TestAllowedRepositoryRootsAdditive:
    def test_git_repository_root_added_when_set(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PROBE_REPOSITORY_ROOTS", raising=False)
        monkeypatch.setenv("GIT_REPOSITORY_ROOT", str(tmp_path))
        from app import git_ops

        roots = git_ops._allowed_repository_roots()
        assert os.path.realpath(str(tmp_path)) in roots

    def test_both_present_are_combined(self, monkeypatch, tmp_path):
        probe_root = tmp_path / "probe-root"
        probe_root.mkdir()
        managed_root = tmp_path / "managed-root"
        managed_root.mkdir()
        monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(probe_root))
        monkeypatch.setenv("GIT_REPOSITORY_ROOT", str(managed_root))
        from app import git_ops

        roots = git_ops._allowed_repository_roots()
        assert os.path.realpath(str(probe_root)) in roots
        assert os.path.realpath(str(managed_root)) in roots

    def test_both_unset_fails_closed(self, monkeypatch):
        monkeypatch.delenv("PROBE_REPOSITORY_ROOTS", raising=False)
        monkeypatch.delenv("GIT_REPOSITORY_ROOT", raising=False)
        from app import git_ops

        with pytest.raises(git_ops.GitError):
            git_ops._allowed_repository_roots()

    def test_only_probe_roots_set_still_works(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
        monkeypatch.delenv("GIT_REPOSITORY_ROOT", raising=False)
        from app import git_ops

        roots = git_ops._allowed_repository_roots()
        assert roots == [os.path.realpath(str(tmp_path))]


# --- API: sync / repository-status ----------------------------------------


@pytest.fixture
def rsa_private_key_path(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "github-app-key.pem"
    path.write_bytes(pem)
    return str(path)


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "repo-manager-api-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.setenv("GIT_REPOSITORY_ROOT", str(tmp_path / "managed-root"))
    monkeypatch.setenv("GIT_CLONE_TIMEOUT", "60")
    monkeypatch.setenv("GIT_FETCH_TIMEOUT", "60")
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("GITHUB_API_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_WEB_BASE_URL", raising=False)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name="acme-system"):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _configure_app(monkeypatch, key_path, app_id="123"):
    monkeypatch.setenv("GITHUB_APP_ID", app_id)
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", key_path)


def _fake_urlopen_factory(default_branch="main", token="ghs_faketoken0123456789"):
    def fake_urlopen(request, timeout=30):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                if "access_tokens" in request.full_url:
                    return json.dumps(
                        {"token": token, "expires_at": "2099-01-01T00:00:00Z"}
                    ).encode("utf-8")
                return json.dumps({"default_branch": default_branch}).encode("utf-8")

        return FakeResponse()

    return fake_urlopen


class TestSyncEndpoint:
    def test_requires_connected_status(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h,
        )
        connection_id = r.json()["id"]

        r = admin_client.post(f"/github/connections/{connection_id}/sync", headers=h)
        assert r.status_code == 409, r.text

    def test_other_system_gets_404(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "system-a")
        system_b = _create_system(admin_client, token, "system-b")
        h_a = _headers(token, system_a["id"])
        h_b = _headers(token, system_b["id"])

        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h_a,
        )
        connection_id = r.json()["id"]

        r = admin_client.post(f"/github/connections/{connection_id}/sync", headers=h_b)
        assert r.status_code == 404, r.text

    def test_sync_success_updates_last_synced_fields(
        self, admin_client, monkeypatch, rsa_private_key_path, tmp_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])

        remote_root = tmp_path / "remote-root"
        bare_dir = remote_root / "acme" / "widgets.git"
        work_dir = tmp_path / "remote-work"
        _init_bare_remote(bare_dir)
        _seed_remote(bare_dir, work_dir)
        expected_sha = _head_sha(work_dir)

        r = admin_client.post(
            "/github/connections",
            json={
                "owner": "acme",
                "repo": "widgets",
                "installation_id": 1,
                "web_base_url": f"file://{remote_root}",
            },
            headers=h,
        )
        assert r.status_code == 201, r.text
        connection_id = r.json()["id"]
        assert r.json()["clone_url"] == f"file://{remote_root}/acme/widgets.git"

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_factory("main"))

        r = admin_client.post(f"/github/connections/{connection_id}/verify", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "connected"

        r = admin_client.post(f"/github/connections/{connection_id}/sync", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "connected"
        assert body["last_error"] is None
        assert body["last_synced_commit_sha"] == expected_sha
        assert body["last_synced_at"]

        r = admin_client.get(f"/github/connections/{connection_id}/repository-status", headers=h)
        assert r.status_code == 200, r.text
        status = r.json()
        assert status["mirror_exists"] is True
        assert status["mirror_path"] == f"mirrors/{connection_id}"
        assert status["last_synced_commit_sha"] == expected_sha
        assert status["default_branch"] == "main"

    def test_repository_status_before_sync_reports_no_mirror(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h,
        )
        connection_id = r.json()["id"]

        r = admin_client.get(f"/github/connections/{connection_id}/repository-status", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mirror_exists"] is False
        assert body["last_synced_at"] is None

    def test_repository_status_gracefully_degrades_without_root(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h,
        )
        connection_id = r.json()["id"]

        monkeypatch.delenv("GIT_REPOSITORY_ROOT", raising=False)
        r = admin_client.get(f"/github/connections/{connection_id}/repository-status", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mirror_exists"] is False
        assert body["mirror_path"] is None

    def test_sync_failure_sets_error_status_without_token_leak(
        self, admin_client, monkeypatch, rsa_private_key_path, tmp_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])

        # web_base_url points at a directory with no such bare repo -> clone fails.
        r = admin_client.post(
            "/github/connections",
            json={
                "owner": "acme",
                "repo": "widgets",
                "installation_id": 1,
                "web_base_url": f"file://{tmp_path / 'nowhere'}",
            },
            headers=h,
        )
        connection_id = r.json()["id"]

        secret_token = "ghs_leaktoken_should_not_appear_0123456789"
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_factory("main", token=secret_token))

        r = admin_client.post(f"/github/connections/{connection_id}/verify", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "connected"

        r = admin_client.post(f"/github/connections/{connection_id}/sync", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "error"
        assert body["last_error"]
        assert secret_token not in body["last_error"]
        assert secret_token not in json.dumps(body)
