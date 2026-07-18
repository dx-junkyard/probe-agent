"""Tests for Issue #227: explicit Disconnect must revoke publish permission
immediately, not just soft-delete the connection row.

Covers: auto-cancelling non-terminal publish jobs (and their worktree
cleanup + audit trail) on disconnect; refusing disconnect while a job is in
an in-flight publish phase (committing/pushing/creating_pr); 409s from
approve/create/sync/verify against a disconnected connection; the
`approve_publish_job` compare-and-set losing a race against a disconnect
that bypassed the route guards directly via SQL; the phase-entry/pre-push
re-validation failing a job whose connection was disconnected mid-flight;
`pr_url`/`branch_name` surviving disconnect on a completed job; and
reconnecting as a brand new connection row.

Fixture setup mirrors `tests/test_publish_jobs.py` (local `file://` bare
"remote", fake GitHub API urlopen, synchronous phase functions via
`spawn=False`).
"""

import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import publish_job, repo_manager
from app.db import get_conn
from app.publish_job import PublishJobConflict


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


def _head_sha(work_dir):
    return subprocess.run(
        ["git", "-C", str(work_dir), "rev-parse", "HEAD"],
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


def _connection_row(connection_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM github_connections WHERE id = ?", (connection_id,)
        ).fetchone()


def _audit_events(system_id, event_type=None):
    with get_conn() as conn:
        if event_type is None:
            return conn.execute(
                "SELECT * FROM publish_audit_events WHERE system_id = ? ORDER BY id",
                (system_id,),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM publish_audit_events WHERE system_id = ? AND event_type = ? ORDER BY id",
            (system_id, event_type),
        ).fetchall()


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
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "publish-disconnect-test.db"))
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
    """Build a system with a connected GitHub connection (pointed at a local
    bare 'remote'), and a probe patch with a green baseline+probed
    validation -- everything `create_publish_job`'s gate requires."""
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


def _create_and_prepare_job(ctx):
    job_id = publish_job.create_publish_job(
        ctx["system_id"], ctx["connection_id"], ctx["patch_id"], requested_by_user_id=1, spawn=False,
    )
    publish_job._run_prepare_phase(job_id)
    assert _job_row(job_id)["status"] == "awaiting_approval"
    return job_id


# --- disconnect cancels non-terminal jobs -----------------------------------


class TestDisconnectCancelsJobs:
    def test_disconnect_cancels_awaiting_approval_job_and_audits(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="disc-awaiting")
        job_id = _create_and_prepare_job(ctx)
        worktree = repo_manager.job_path(job_id)
        assert __import__("os").path.exists(worktree)

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "disconnected"

        row = _job_row(job_id)
        assert row["status"] == "cancelled"
        assert "disconnect" in row["error"].lower()
        assert row["cleanup_state"] == "removed"
        assert not __import__("os").path.exists(worktree)

        conn_events = _audit_events(ctx["system_id"], "connection_disconnected")
        assert len(conn_events) == 1
        detail = json.loads(conn_events[0]["detail"])
        assert detail["cancelled_job_ids"] == [job_id]
        assert detail["cancelled_job_count"] == 1

        job_events = _audit_events(ctx["system_id"], "publish_job_cancelled")
        assert len(job_events) == 1
        assert job_events[0]["job_id"] == job_id
        assert json.loads(job_events[0]["detail"])["reason"] == "connection_disconnected"

        cleanup_events = _audit_events(ctx["system_id"], "publish_job_cleanup")
        assert len(cleanup_events) == 1
        assert cleanup_events[0]["job_id"] == job_id
        assert json.loads(cleanup_events[0]["detail"])["cleanup_state"] == "removed"

        # No secret/path ever lands in the audit trail.
        for event in _audit_events(ctx["system_id"]):
            assert "ghs_" not in (event["detail"] or "")
            assert str(tmp_path) not in (event["detail"] or "")

    def test_disconnect_cancels_pending_job_before_prepare_runs(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="disc-pending")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], requested_by_user_id=1, spawn=False,
        )
        assert _job_row(job_id)["status"] == "pending"

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 200, r.text
        assert _job_row(job_id)["status"] == "cancelled"


# --- disconnect refused during an in-flight publish phase -------------------


class TestDisconnectBlockedDuringPublish:
    @pytest.mark.parametrize("in_flight_status", ["committing", "pushing", "creating_pr"])
    def test_disconnect_refused_while_publishing(
        self, admin_client, tmp_path, monkeypatch, in_flight_status
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name=f"disc-inflight-{in_flight_status}")
        job_id = _create_and_prepare_job(ctx)
        with get_conn() as conn:
            conn.execute(
                "UPDATE publish_jobs SET status = ? WHERE id = ?", (in_flight_status, job_id)
            )

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 409, r.text
        assert "progress" in r.json()["detail"].lower() or "publish" in r.json()["detail"].lower()

        assert _connection_row(ctx["connection_id"])["status"] == "connected"
        assert _job_row(job_id)["status"] == in_flight_status
        assert _audit_events(ctx["system_id"], "connection_disconnected") == []


# --- 409s against a disconnected connection ---------------------------------


class TestDisconnectedConnectionRejections:
    def test_approve_create_sync_verify_all_409_after_disconnect(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="disc-rejections")
        job_id = _create_and_prepare_job(ctx)

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 200, r.text

        r = admin_client.post(f"/github/publish-jobs/{job_id}/approve", headers=ctx["headers"])
        assert r.status_code == 409, r.text

        r = admin_client.post(
            f"/github/connections/{ctx['connection_id']}/publish-jobs",
            json={"patch_id": ctx["patch_id"]},
            headers=ctx["headers"],
        )
        assert r.status_code == 409, r.text

        r = admin_client.post(f"/github/connections/{ctx['connection_id']}/sync", headers=ctx["headers"])
        assert r.status_code == 409, r.text

        r = admin_client.post(f"/github/connections/{ctx['connection_id']}/verify", headers=ctx["headers"])
        assert r.status_code == 409, r.text

    def test_double_disconnect_is_409(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="disc-twice")
        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 200, r.text
        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 409, r.text


# --- compare-and-set: approve loses a race against a raw-SQL disconnect ----


class TestApproveCompareAndSet:
    def test_approve_conflicts_when_connection_disconnected_via_sql(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="cas-approve")
        job_id = _create_and_prepare_job(ctx)

        # Bypass the route guards entirely -- simulates a disconnect that
        # raced ahead of `approve_publish_job`'s read.
        with get_conn() as conn:
            conn.execute(
                "UPDATE github_connections SET status = 'disconnected' WHERE id = ?",
                (ctx["connection_id"],),
            )

        with pytest.raises(PublishJobConflict):
            publish_job.approve_publish_job(job_id, ctx["system_id"], approved_by_user_id=1, spawn=False)

        assert _job_row(job_id)["status"] == "awaiting_approval"


# --- token/push-time re-validation -------------------------------------


class TestPhaseRevalidation:
    def test_publish_phase_fails_closed_when_connection_disconnected_after_approval(
        self, admin_client, tmp_path, monkeypatch
    ):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="revalidate-push")
        job_id = _create_and_prepare_job(ctx)
        publish_job.approve_publish_job(job_id, ctx["system_id"], approved_by_user_id=1, spawn=False)
        assert _job_row(job_id)["status"] == "committing"

        with get_conn() as conn:
            conn.execute(
                "UPDATE github_connections SET status = 'disconnected' WHERE id = ?",
                (ctx["connection_id"],),
            )

        publish_job._run_publish_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "failed"
        assert "connect" in row["error"].lower()
        assert row["cleanup_state"] == "removed"
        assert not __import__("os").path.exists(repo_manager.job_path(job_id))

        refs = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "show-ref"], capture_output=True, text=True, check=True,
        ).stdout
        assert "probe/job-" not in refs


# --- deterministic ordering instead of a flaky thread race -----------------


class TestApproveVsDisconnectOrdering:
    def test_approve_then_disconnect_blocks_disconnect(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="order-approve-first")
        job_id = _create_and_prepare_job(ctx)

        publish_job.approve_publish_job(job_id, ctx["system_id"], approved_by_user_id=1, spawn=False)
        assert _job_row(job_id)["status"] == "committing"

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 409, r.text
        assert _connection_row(ctx["connection_id"])["status"] == "connected"

    def test_disconnect_then_approve_blocks_approve(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="order-disconnect-first")
        job_id = _create_and_prepare_job(ctx)

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 200, r.text
        assert _job_row(job_id)["status"] == "cancelled"

        with pytest.raises(PublishJobConflict):
            publish_job.approve_publish_job(job_id, ctx["system_id"], approved_by_user_id=1, spawn=False)


# --- pr_url / branch_name preserved after disconnect ------------------------


class TestCompletedJobSurvivesDisconnect:
    def test_pr_url_preserved_after_disconnect(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="pr-preserved")
        job_id = _create_and_prepare_job(ctx)
        publish_job.approve_publish_job(job_id, ctx["system_id"], approved_by_user_id=1, spawn=False)
        publish_job._run_publish_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "completed"
        assert row["pr_url"]
        pr_url_before = row["pr_url"]
        branch_before = row["branch_name"]

        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 200, r.text

        row = _job_row(job_id)
        assert row["status"] == "completed"
        assert row["pr_url"] == pr_url_before
        assert row["branch_name"] == branch_before


# --- reconnect as a new row --------------------------------------------------


class TestReconnect:
    def test_reconnect_creates_a_new_connection_row(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="reconnect")
        r = admin_client.delete(f"/github/connections/{ctx['connection_id']}", headers=ctx["headers"])
        assert r.status_code == 200, r.text

        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=ctx["headers"],
        )
        assert r.status_code == 201, r.text
        new_connection_id = r.json()["id"]
        assert new_connection_id != ctx["connection_id"]
        assert r.json()["status"] == "pending"
