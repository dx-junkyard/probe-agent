"""Tests for Issue #226: publish job retry / recovery under the same job id
and the same server-generated branch.

Fixture setup mirrors `tests/test_publish_jobs.py` / `test_publish_disconnect.py`
(local `file://` bare "remote", fake GitHub API urlopen, synchronous phase
functions via `spawn=False`). Everything here is synchronous: `create_publish_job`
/ `approve_publish_job` / `retry_publish_job` are all called with
`spawn=False`, and the phase functions (`_run_prepare_phase` /
`_run_publish_phase` / `_run_reconcile_phase`) are invoked directly.

A "push-succeeded-then-PR-crash" is manufactured by monkeypatching
`app.github_app.create_pull_request` to raise once (capturing the real
function first so it can be restored for the retry).
"""

import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app import github_app, publish_guards, publish_job, publish_recovery, repo_manager
from app.db import get_conn
from app.publish_job import PublishJobConflict, PublishJobNotFound


# --- git fixture helpers (mirrors tests/test_publish_jobs.py) ---------------


def _init_bare_remote(bare_dir):
    bare_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare_dir)],
        check=True, capture_output=True,
    )


def _git_identity(work_dir):
    subprocess.run(
        ["git", "-C", str(work_dir), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work_dir), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )


def _seed_remote(bare_dir, work_dir):
    subprocess.run(["git", "clone", str(bare_dir), str(work_dir)], check=True, capture_output=True)
    _git_identity(work_dir)
    (work_dir / "README.md").write_text("hello\n")
    (work_dir / "app.py").write_text("def handler(x):\n    return x + 1\n")
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


def _remote_branch_sha(bare_dir, branch):
    return subprocess.run(
        ["git", "-C", str(bare_dir), "rev-parse", branch],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _make_probe_diff(work_dir):
    path = work_dir / "app.py"
    original = path.read_text()
    patched = 'from probe_agent import probe\n\n\n@probe(component_id="handler")\n' + original
    path.write_text(patched)
    diff = subprocess.run(
        ["git", "-C", str(work_dir), "diff"], check=True, capture_output=True, text=True,
    ).stdout
    subprocess.run(["git", "-C", str(work_dir), "checkout", "--", "app.py"], check=True, capture_output=True)
    return diff


def _job_row(job_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()


def _audit_events(job_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM publish_audit_events WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()


def _status_transitions(job_id):
    return [
        (json.loads(e["detail"])["from"], json.loads(e["detail"])["to"])
        for e in _audit_events(job_id)
        if e["event_type"] == "publish_job_status_transition"
    ]


# --- GitHub API mocking ------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def _make_fake_urlopen(*, default_branch="main", token="ghs_faketoken0123456789", pr_number=42):
    existing_prs = []
    calls = {"create_pr": 0, "list_pr": 0, "token": 0}

    def fake_urlopen(request, timeout=30):
        url = request.full_url
        method = request.get_method()

        if "access_tokens" in url:
            calls["token"] += 1
            return _FakeResponse({"token": token, "expires_at": "2099-01-01T00:00:00Z"})
        if "/pulls?head=" in url:
            calls["list_pr"] += 1
            return _FakeResponse(list(existing_prs))
        if url.endswith("/pulls") and method == "POST":
            calls["create_pr"] += 1
            pr = {
                "html_url": f"https://github.com/acme/widgets/pull/{pr_number}",
                "number": pr_number,
            }
            existing_prs.append(pr)
            return _FakeResponse(pr)
        return _FakeResponse({"default_branch": default_branch})

    return fake_urlopen, calls


def _fail_once_then_delegate(real_fn, error_cls, message="boom"):
    state = {"n": 0}

    def wrapper(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise error_cls(message)
        return real_fn(*args, **kwargs)

    return wrapper, state


# --- app fixtures ------------------------------------------------------------


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
def admin_client(tmp_path, monkeypatch, rsa_private_key_path):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "publish-retry-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.setenv("GIT_REPOSITORY_ROOT", str(tmp_path / "managed-root"))
    monkeypatch.setenv("GIT_CLONE_TIMEOUT", "60")
    monkeypatch.setenv("GIT_FETCH_TIMEOUT", "60")
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", rsa_private_key_path)
    monkeypatch.delenv("GITHUB_API_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_WEB_BASE_URL", raising=False)
    monkeypatch.delenv("GIT_ALLOW_DIRECT_PUSH", raising=False)
    monkeypatch.delenv("GIT_ALLOW_FORCE_PUSH", raising=False)
    monkeypatch.delenv("GIT_ALLOW_WORKFLOW_CHANGES", raising=False)
    monkeypatch.setenv("GIT_BRANCH_PREFIX", "probe/")
    # Recovery worker never ticks during a short-lived TestClient lifespan
    # anyway (first tick only after a full interval), but disable it
    # explicitly so tests only exercise repair/retry functions directly.
    monkeypatch.setenv("PUBLISH_RECOVERY_INTERVAL_SECONDS", "0")
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name="pub-system"):
    r = client.post(
        "/systems", json={"name": name, "environment": "test", "description": ""}, headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _assign_test_installation(system_id, installation_id=1):
    now = "2026-01-01T00:00:00+00:00"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO github_installations
                (installation_id, github_account_login, github_account_type, status,
                 verified_at, created_at, updated_at)
            VALUES (?, 'acme', 'Organization', 'active', ?, ?, ?)
            """,
            (installation_id, now, now, now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO github_installation_systems
                (installation_id, system_id, created_at)
            VALUES (?, ?, ?)
            """,
            (installation_id, system_id, now),
        )


def _setup_ready_patch(
    admin_client, tmp_path, monkeypatch, *, name="pub-sys", token="ghs_faketoken0123456789", pr_number=42
):
    login_token = _login(admin_client)
    system = _create_system(admin_client, login_token, name)
    _assign_test_installation(system["id"])
    h = _headers(login_token, system["id"])

    remote_root = tmp_path / f"{name}-remote-root"
    bare_dir = remote_root / "acme" / "widgets.git"
    work_dir = tmp_path / f"{name}-remote-work"
    _init_bare_remote(bare_dir)
    _seed_remote(bare_dir, work_dir)
    head_sha = _head_sha(work_dir)
    diff = _make_probe_diff(work_dir)

    fake_urlopen, calls = _make_fake_urlopen(default_branch="main", token=token, pr_number=pr_number)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    r = admin_client.post(
        "/github/connections",
        json={"owner": "acme", "repo": "widgets", "installation_id": 1},
        headers=h,
    )
    assert r.status_code == 201, r.text
    connection_id = r.json()["id"]
    local_clone_url = f"file://{bare_dir}"
    with get_conn() as conn:
        conn.execute(
            "UPDATE github_connections SET clone_url = ? WHERE id = ?",
            (local_clone_url, connection_id),
        )
    monkeypatch.setattr(repo_manager, "_connection_clone_url", lambda row: row["clone_url"])
    monkeypatch.setattr(repo_manager, "_validate_existing_remote", lambda *_args: None)
    real_run_git = repo_manager._run_git
    monkeypatch.setattr(
        repo_manager,
        "_run_git",
        lambda cwd, args, **kwargs: real_run_git(
            cwd, ["-c", "protocol.file.allow=always"] + args, **kwargs
        ),
    )

    r = admin_client.post(f"/github/connections/{connection_id}/verify", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "connected"

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at)
               VALUES (?, ?, ?, 'ready', 0)""",
            (system["id"], str(work_dir), head_sha),
        )
        snapshot_id = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model,
                    prompt_version, schema_version, decision_method,
                    status, is_mock, started_at, completed_at)
               VALUES (?, ?, 'probe_plan', 'mock', 'mock-model', 'v1', 'v1',
                       'reasoning_llm', 'completed', 1, 0, 0)""",
            (system["id"], snapshot_id),
        )
        run_id = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO probe_plans
                   (system_id, snapshot_id, intelligence_run_id, feature_id,
                    objective, status, origin, created_at, updated_at)
               VALUES (?, ?, ?, 'feat-1', 'Add tracing to the request handler',
                       'approved', 'manual', 0, 0)""",
            (system["id"], snapshot_id, run_id),
        )
        plan_id = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO probe_patches
                   (plan_id, system_id, snapshot_id, commit_sha, diff,
                    skipped, status, cleanup_state, created_at)
               VALUES (?, ?, ?, ?, ?, '[]', 'generated', 'not_attempted', 0)""",
            (plan_id, system["id"], snapshot_id, head_sha, diff),
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
                (patch_id, system["id"], variant),
            )

    return {
        "system_id": system["id"],
        "headers": h,
        "connection_id": connection_id,
        "patch_id": patch_id,
        "plan_id": plan_id,
        "work_dir": work_dir,
        "bare_dir": bare_dir,
        "remote_root": remote_root,
        "fake_calls": calls,
        "token": token,
    }


def _prepare_and_approve(ctx):
    job_id = publish_job.create_publish_job(
        ctx["system_id"], ctx["connection_id"], ctx["patch_id"], requested_by_user_id=1, spawn=False,
    )
    publish_job._run_prepare_phase(job_id)
    assert _job_row(job_id)["status"] == "awaiting_approval"
    publish_job.approve_publish_job(job_id, ctx["system_id"], approved_by_user_id=1, spawn=False)
    assert _job_row(job_id)["status"] == "committing"
    return job_id


def _crash_at_pr_creation(job_id):
    """Run the publish phase with `create_pull_request` failing once (after
    a real commit + push already happened). Restores the real function
    before returning so a subsequent retry can succeed."""
    real_create_pr = github_app.create_pull_request
    wrapper, state = _fail_once_then_delegate(real_create_pr, github_app.GitHubAppError)
    import app.publish_job as publish_job_module  # noqa: F401  (module reference for clarity)

    original = github_app.create_pull_request
    github_app.create_pull_request = wrapper
    try:
        publish_job._run_publish_phase(job_id)
    finally:
        github_app.create_pull_request = original
    return state


# --- retry endpoint 404/409 matrix -------------------------------------


class TestRetryEndpoint:
    def test_retry_404_wrong_system(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="retry-wrong-system")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        token = _login(admin_client)
        system_b = _create_system(admin_client, token, "retry-wrong-system-b")
        h_b = _headers(token, system_b["id"])
        r = admin_client.post(f"/github/publish-jobs/{job_id}/retry", headers=h_b)
        assert r.status_code == 404

    def test_retry_409_non_retryable_status(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="retry-bad-status")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False,
        )
        publish_job._run_prepare_phase(job_id)
        assert _job_row(job_id)["status"] == "awaiting_approval"

        r = admin_client.post(f"/github/publish-jobs/{job_id}/retry", headers=ctx["headers"])
        assert r.status_code == 409, r.text

    def test_retry_409_disconnected_connection(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="retry-disconnected")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        with get_conn() as conn:
            conn.execute(
                "UPDATE github_connections SET status = 'disconnected' WHERE id = ?",
                (ctx["connection_id"],),
            )

        r = admin_client.post(f"/github/publish-jobs/{job_id}/retry", headers=ctx["headers"])
        assert r.status_code == 409, r.text

    def test_retry_404_unknown_job(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="retry-unknown")
        r = admin_client.post("/github/publish-jobs/999999/retry", headers=ctx["headers"])
        assert r.status_code == 404


# --- reconcile: same branch, push-succeeded-then-PR-crash -------------------


class TestReconcileRecoversPr:
    def test_same_branch_name_across_retry(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="same-branch")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        row = _job_row(job_id)
        assert row["status"] == "retryable_failed", row["error"]
        branch_before = row["branch_name"]
        assert branch_before

        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        publish_job._run_reconcile_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "completed", row["error"]
        assert row["branch_name"] == branch_before

    def test_push_succeeded_then_pr_crash_retry_completes_without_second_push(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="pr-crash")
        job_id = _prepare_and_approve(ctx)
        state = _crash_at_pr_creation(job_id)
        assert state["n"] == 1

        row = _job_row(job_id)
        assert row["status"] == "retryable_failed", row["error"]
        branch_name = row["branch_name"]
        commit_sha = row["commit_sha"]
        assert branch_name and commit_sha
        assert _remote_branch_sha(ctx["bare_dir"], branch_name) == commit_sha

        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        assert _job_row(job_id)["status"] == "reconciling"
        publish_job._run_reconcile_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "completed", row["error"]
        assert row["commit_sha"] == commit_sha
        assert row["pr_url"]

        # No second push: remote branch SHA unchanged, exactly one probe
        # commit on top of the base commit.
        assert _remote_branch_sha(ctx["bare_dir"], branch_name) == commit_sha
        count = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "rev-list", "--count", commit_sha],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert count == "2"  # base commit + exactly one probe commit

    def test_existing_pr_is_recovered_without_creating_a_duplicate(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="existing-pr")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        existing_pr = {"html_url": "https://github.com/acme/widgets/pull/999", "number": 999}
        monkeypatch.setattr(
            github_app, "list_open_pull_requests_for_branch", lambda *a, **k: [existing_pr]
        )

        def _must_not_be_called(*_a, **_k):
            raise AssertionError("create_pull_request must not be called when an existing PR is found")

        monkeypatch.setattr(github_app, "create_pull_request", _must_not_be_called)

        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        publish_job._run_reconcile_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "completed", row["error"]
        assert row["pr_url"] == existing_pr["html_url"]
        assert row["pr_number"] == existing_pr["number"]

    def test_branch_only_exists_resumes_at_pr_creation(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="branch-only")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False,
        )
        publish_job._run_prepare_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "awaiting_approval"
        base_commit_sha = row["base_commit_sha"]
        branch_name = publish_guards.generate_branch_name(job_id, base_commit_sha)

        # Simulate a prior attempt that pushed the branch/commit directly
        # (out of band, no `_run_publish_phase` involved) and then crashed
        # before ever recording it as a completed job.
        clone_dir = tmp_path / "branch-only-clone"
        subprocess.run(["git", "clone", str(ctx["bare_dir"]), str(clone_dir)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", "-b", branch_name], check=True, capture_output=True
        )
        _git_identity(clone_dir)
        app_py = clone_dir / "app.py"
        app_py.write_text('from probe_agent import probe\n\n\n@probe(component_id="handler")\n' + app_py.read_text())
        subprocess.run(["git", "-C", str(clone_dir), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone_dir), "commit", "-m", "probe"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(clone_dir), "push", "origin", branch_name], check=True, capture_output=True
        )
        pushed_sha = _head_sha(clone_dir)

        with get_conn() as conn:
            conn.execute(
                "UPDATE publish_jobs SET status = 'retryable_failed', branch_name = ?, commit_sha = ? "
                "WHERE id = ?",
                (branch_name, pushed_sha, job_id),
            )

        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        publish_job._run_reconcile_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "completed", row["error"]
        assert row["pr_url"]
        assert ctx["fake_calls"]["create_pr"] == 1


# --- reconcile: fail-closed cases -------------------------------------------


class TestReconcileFailsClosed:
    def test_remote_branch_mismatch_requires_manual_intervention(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="sha-mismatch")
        job_id = _prepare_and_approve(ctx)
        publish_job._run_publish_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "completed", row["error"]
        branch_name = row["branch_name"]
        commit_before = row["commit_sha"]

        # Push an out-of-band commit directly onto the job's branch --
        # probe-agent must never overwrite or force-push over this.
        clone_dir = tmp_path / "mismatch-clone"
        subprocess.run(["git", "clone", str(ctx["bare_dir"]), str(clone_dir)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", branch_name], check=True, capture_output=True
        )
        _git_identity(clone_dir)
        (clone_dir / "extra.txt").write_text("out of band\n")
        subprocess.run(["git", "-C", str(clone_dir), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(clone_dir), "commit", "-m", "out of band"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "push", "origin", branch_name], check=True, capture_output=True
        )
        out_of_band_sha = _head_sha(clone_dir)
        assert out_of_band_sha != commit_before

        with get_conn() as conn:
            conn.execute("UPDATE publish_jobs SET status = 'retryable_failed' WHERE id = ?", (job_id,))

        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        publish_job._run_reconcile_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "manual_intervention_required", row["error"]
        assert branch_name in row["error"]
        assert _remote_branch_sha(ctx["bare_dir"], branch_name) == out_of_band_sha

    def test_base_branch_moved_during_reconcile_is_terminal_failed(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="base-moved-reconcile")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        _add_commit_and_push(ctx["work_dir"], "extra.txt", "x\n", "moved during reconcile window")

        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        publish_job._run_reconcile_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "failed", row["error"]
        assert "regenerate" in row["error"].lower() or "moved" in row["error"].lower()
        assert row["cleanup_state"] == "removed"

    def test_lease_contention_defers_without_git_work(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="lease-contention")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO publish_connection_leases (connection_id, owner, acquired_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (ctx["connection_id"], "someone-else:1:1", time.time(), time.time() + 3600),
            )

        calls_before = dict(ctx["fake_calls"])
        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        assert _job_row(job_id)["status"] == "reconciling"
        publish_job._run_reconcile_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "retryable_failed"
        # No new token/list/create_pr calls happened -- the lease was
        # checked before any git/API work.
        assert dict(ctx["fake_calls"]) == calls_before


# --- startup / periodic recovery --------------------------------------------


class TestRecoveryRepair:
    def test_repair_marks_stuck_prepare_job_failed(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="stuck-prepare")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False,
        )
        publish_job._run_prepare_phase(job_id)
        assert _job_row(job_id)["status"] == "awaiting_approval"

        stale = time.time() - 10000
        with get_conn() as conn:
            conn.execute(
                "UPDATE publish_jobs SET status = 'validating', heartbeat_at = ? WHERE id = ?",
                (stale, job_id),
            )

        repaired = publish_recovery.repair_interrupted_jobs(reason="stuck_detected")
        assert repaired >= 1
        row = _job_row(job_id)
        assert row["status"] == "failed"
        assert "Interrupted" in row["error"]

    def test_repair_marks_stuck_publish_job_retryable_failed(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="stuck-publish")
        job_id = _prepare_and_approve(ctx)

        stale = time.time() - 10000
        with get_conn() as conn:
            conn.execute(
                "UPDATE publish_jobs SET status = 'pushing', heartbeat_at = ? WHERE id = ?",
                (stale, job_id),
            )

        repaired = publish_recovery.repair_interrupted_jobs(reason="stuck_detected")
        assert repaired >= 1
        row = _job_row(job_id)
        assert row["status"] == "retryable_failed"
        assert "Interrupted" in row["error"]

    def test_repair_clears_expired_leases(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="expired-lease")
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO publish_connection_leases (connection_id, owner, acquired_at, expires_at) "
                "VALUES (?, 'stale-owner', ?, ?)",
                (ctx["connection_id"], time.time() - 1000, time.time() - 500),
            )
        publish_recovery.repair_interrupted_jobs(reason="stuck_detected")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM publish_connection_leases WHERE connection_id = ?",
                (ctx["connection_id"],),
            ).fetchone()
        assert row is None


class TestAutoRetry:
    def test_auto_retry_respects_max_but_manual_retry_still_allowed(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="auto-retry-max")
        job_id = _prepare_and_approve(ctx)
        with get_conn() as conn:
            conn.execute(
                "UPDATE publish_jobs SET status = 'retryable_failed', retry_count = 3 WHERE id = ?",
                (job_id,),
            )

        retried = publish_recovery.auto_retry_eligible_jobs()
        assert retried == 0
        row = _job_row(job_id)
        assert row["status"] == "retryable_failed"
        assert row["retry_count"] == 3

        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        row = _job_row(job_id)
        assert row["status"] == "reconciling"
        assert row["retry_count"] == 4

    def test_auto_retry_never_picks_manual_intervention_required(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="auto-retry-mir")
        job_id = _prepare_and_approve(ctx)
        with get_conn() as conn:
            conn.execute(
                "UPDATE publish_jobs SET status = 'manual_intervention_required', retry_count = 0 "
                "WHERE id = ?",
                (job_id,),
            )

        retried = publish_recovery.auto_retry_eligible_jobs()
        assert retried == 0
        assert _job_row(job_id)["status"] == "manual_intervention_required"

    def test_auto_retry_completes_an_eligible_job(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="auto-retry-ok")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        retried = publish_recovery.auto_retry_eligible_jobs()
        assert retried == 1
        row = _job_row(job_id)
        assert row["status"] == "completed", row["error"]
        assert row["retry_count"] == 1

        events = [e["event_type"] for e in _audit_events(job_id)]
        assert "publish_job_auto_retry" in events


# --- cancel / disconnect integration (#227) ----------------------------


class TestCancelAndDisconnectIntegration:
    def test_cancel_allowed_from_retryable_failed(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="cancel-retryable")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        publish_job.cancel_publish_job(job_id, ctx["system_id"])
        row = _job_row(job_id)
        assert row["status"] == "cancelled"

    def test_disconnect_auto_cancels_retryable_failed_job(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="disc-retryable")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 200, r.text
        assert _job_row(job_id)["status"] == "cancelled"

    def test_disconnect_blocked_while_reconciling(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="disc-reconciling")
        job_id = _prepare_and_approve(ctx)
        _crash_at_pr_creation(job_id)
        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        assert _job_row(job_id)["status"] == "reconciling"

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 409, r.text
        assert _job_row(job_id)["status"] == "reconciling"


# --- audit trail ---------------------------------------------------------


class TestAuditTrail:
    def test_full_lifecycle_audit_trail(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="audit-lifecycle")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], requested_by_user_id=1, spawn=False,
        )
        publish_job._run_prepare_phase(job_id)
        publish_job.approve_publish_job(job_id, ctx["system_id"], approved_by_user_id=1, spawn=False)
        _crash_at_pr_creation(job_id)
        assert _job_row(job_id)["status"] == "retryable_failed"

        publish_job.retry_publish_job(job_id, ctx["system_id"], actor_user_id=1, spawn=False)
        publish_job._run_reconcile_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "completed", row["error"]

        events = _audit_events(job_id)
        event_types = [e["event_type"] for e in events]
        assert "publish_job_requested" in event_types
        assert "publish_job_approved" in event_types
        assert "publish_job_retry_requested" in event_types

        requested = next(e for e in events if e["event_type"] == "publish_job_requested")
        approved = next(e for e in events if e["event_type"] == "publish_job_approved")
        retried = next(e for e in events if e["event_type"] == "publish_job_retry_requested")
        assert requested["actor_user_id"] == 1
        assert approved["actor_user_id"] == 1
        assert retried["actor_user_id"] == 1

        transitions = _status_transitions(job_id)
        assert ("awaiting_approval", "committing") in transitions
        assert ("retryable_failed", "reconciling") in transitions
        assert any(to == "retryable_failed" for _frm, to in transitions)
        assert any(to == "completed" for _frm, to in transitions)

        # Never a token or filesystem path in any audit row.
        for event in events:
            assert ctx["token"] not in (event["detail"] or "")
            assert str(tmp_path) not in (event["detail"] or "")

        r = admin_client.get(f"/github/publish-jobs/{job_id}/events", headers=ctx["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) == len(events)
        assert body[0]["event_type"] == "publish_job_requested"

    def test_events_endpoint_is_system_scoped(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="audit-iso")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False,
        )

        token = _login(admin_client)
        system_b = _create_system(admin_client, token, "audit-iso-b")
        h_b = _headers(token, system_b["id"])
        r = admin_client.get(f"/github/publish-jobs/{job_id}/events", headers=h_b)
        assert r.status_code == 404
