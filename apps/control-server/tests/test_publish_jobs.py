"""Tests for the publish job state machine (Issue #216, sub-task 3).

Covers: the prepare phase (authenticating -> ... -> awaiting_approval) and
publish phase (committing -> ... -> completed) end to end against a local
`file://` bare "remote"; the pre-creation validation gate (409 without a
green baseline+probed validation); that nothing is committed/pushed before
an explicit approval; the pre-push staleness re-check (fail closed, no
auto-rebase); branch/force-push safety (server-generated branch only,
explicit refspec, no `--force`); patch-diff file-path staging guards
(outside-diff files, `.git/`, workflows, secret-candidate names, path
traversal, symlinks); approve/cancel idempotency and worktree cleanup on
every terminal state; system isolation; and that an installation token
never reaches a persisted `error` string.

Threads are not used here: `publish_job.create_publish_job(..., spawn=False)`
and `publish_job.approve_publish_job(..., spawn=False)` skip the background
thread, and the phase functions (`_run_prepare_phase` / `_run_publish_phase`)
are called directly and synchronously.
"""

import json
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import publish_guards, publish_job, repo_manager
from app.db import get_conn
from app.publish_job import PublishJobConflict, PublishJobNotFound


# --- git fixture helpers (mirrors tests/test_repo_manager.py) ---------------


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


def _make_probe_diff(work_dir):
    """Edit app.py (uncommitted) and capture the unified diff -- the same
    shape `patch_generator.generate_patch` produces: a decorator line added
    above an existing function, never committed to the source clone."""
    path = work_dir / "app.py"
    original = path.read_text()
    patched = 'from probe_agent import probe\n\n\n@probe(component_id="handler")\n' + original
    path.write_text(patched)
    diff = subprocess.run(
        ["git", "-C", str(work_dir), "diff"], check=True, capture_output=True, text=True,
    ).stdout
    subprocess.run(["git", "-C", str(work_dir), "checkout", "--", "app.py"], check=True, capture_output=True)
    return diff


def _make_new_file_diff(path_rel, content):
    lines = content.splitlines(keepends=True)
    body = "".join(f"+{line}" if line.endswith("\n") else f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path_rel} b/{path_rel}\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        f"+++ b/{path_rel}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}"
    )


def _job_row(job_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()


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
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "publish-jobs-test.db"))
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
    return r.json()["access_token"]


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


def _setup_ready_patch(
    admin_client, tmp_path, monkeypatch, *, name="pub-sys", token="ghs_faketoken0123456789", pr_number=42
):
    """Build a system with a connected GitHub connection (pointed at a local
    bare 'remote'), and a probe patch with a green baseline+probed
    validation -- everything `create_publish_job`'s gate requires."""
    login_token = _login(admin_client)
    system = _create_system(admin_client, login_token, name)
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
        json={
            "owner": "acme", "repo": "widgets", "installation_id": 1,
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    connection_id = r.json()["id"]
    local_clone_url = f"file://{bare_dir}"
    from app.db import get_conn
    from app import repo_manager
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


# --- happy path ----------------------------------------------------------


class TestHappyPath:
    def test_prepare_then_approve_publishes_and_creates_pr(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="happy")

        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], requested_by_user_id=1, spawn=False,
        )
        row = _job_row(job_id)
        assert row["status"] == "pending"

        publish_job._run_prepare_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "awaiting_approval", row["error"]
        assert row["base_commit_sha"] == _head_sha(ctx["work_dir"])
        assert row["commit_sha"] is None
        assert row["pr_url"] is None

        # Nothing pushed yet -- the bare "remote" only has the original main branch.
        refs_before = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "show-ref"], capture_output=True, text=True, check=True,
        ).stdout
        assert "probe/job-" not in refs_before

        publish_job.approve_publish_job(job_id, ctx["system_id"], approved_by_user_id=1, spawn=False)
        assert _job_row(job_id)["status"] == "committing"

        publish_job._run_publish_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "completed", row["error"]
        assert row["branch_name"].startswith("probe/job-")
        assert row["commit_sha"]
        assert row["pr_url"] == "https://github.com/acme/widgets/pull/42"
        assert row["pr_number"] == 42
        assert row["cleanup_state"] == "removed"
        assert not os.path.exists(repo_manager.job_path(job_id))

        validation_summary = json.loads(row["validation_summary"])
        assert validation_summary["baseline"]["overall_success"] is True
        assert validation_summary["probed"]["overall_success"] is True

        refs_after = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "show-ref"], capture_output=True, text=True, check=True,
        ).stdout
        assert row["branch_name"] in refs_after

        commit_msg = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "log", "-1", "--format=%B", row["commit_sha"]],
            capture_output=True, text=True, check=True,
        ).stdout
        assert commit_msg.startswith("chore(probe):")
        assert f"Probe-Agent-Patch-Id: {ctx['patch_id']}" in commit_msg
        assert f"Probe-Agent-Snapshot: {row['base_commit_sha']}" in commit_msg
        assert "Probe-Agent-Validation: passed" in commit_msg
        assert f"Probe-Agent-System-Id: {ctx['system_id']}" in commit_msg

        author = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "log", "-1", "--format=%an <%ae>", row["commit_sha"]],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert author == "probe-agent[bot] <probe-agent[bot]@users.noreply.github.com>"

        changed_files = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "diff", "--name-only", f"{row['commit_sha']}~1", row["commit_sha"]],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        assert changed_files == ["app.py"]

        # API surface
        r = admin_client.get(f"/github/publish-jobs/{job_id}", headers=ctx["headers"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "completed"
        assert body["pr_url"] == row["pr_url"]
        assert body["validation_summary"]["baseline"]["overall_success"] is True


# --- creation gate ---------------------------------------------------------


class TestCreationGate:
    def test_requires_green_baseline_and_probed_validation(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="novalid")
        with get_conn() as conn:
            conn.execute(
                "UPDATE validation_runs SET overall_success = 0 WHERE patch_id = ? AND variant = 'probed'",
                (ctx["patch_id"],),
            )
        with pytest.raises(PublishJobConflict):
            publish_job.create_publish_job(
                ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
            )

    def test_api_returns_409_without_green_validation(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="novalid-api")
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM validation_runs WHERE patch_id = ? AND variant = 'probed'",
                (ctx["patch_id"],),
            )
        r = admin_client.post(
            f"/github/connections/{ctx['connection_id']}/publish-jobs",
            json={"patch_id": ctx["patch_id"]},
            headers=ctx["headers"],
        )
        assert r.status_code == 409, r.text

    def test_requires_connected_connection(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="notconnected")
        with get_conn() as conn:
            conn.execute(
                "UPDATE github_connections SET status = 'pending' WHERE id = ?",
                (ctx["connection_id"],),
            )
        with pytest.raises(PublishJobConflict):
            publish_job.create_publish_job(
                ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
            )

    def test_unknown_patch_is_404(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="badpatch")
        with pytest.raises(PublishJobNotFound):
            publish_job.create_publish_job(
                ctx["system_id"], ctx["connection_id"], 999999, 1, spawn=False
            )


# --- staleness (fail closed, no auto-rebase) --------------------------------


class TestStaleness:
    def test_prepare_fails_when_base_branch_already_moved(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="stale-prepare")
        _add_commit_and_push(ctx["work_dir"], "extra.txt", "x\n", "moved on")

        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "failed"
        assert "regenerate" in row["error"].lower()
        assert row["cleanup_state"] == "removed"

    def test_push_fails_when_base_branch_moves_after_prepare(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="stale-push")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        assert _job_row(job_id)["status"] == "awaiting_approval"

        _add_commit_and_push(ctx["work_dir"], "extra.txt", "x\n", "moved after prepare")

        publish_job.approve_publish_job(job_id, ctx["system_id"], 1, spawn=False)
        publish_job._run_publish_phase(job_id)

        row = _job_row(job_id)
        assert row["status"] == "failed"
        assert "stale" in row["error"].lower() or "moved" in row["error"].lower()
        assert row["cleanup_state"] == "removed"

        refs = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "show-ref"], capture_output=True, text=True, check=True,
        ).stdout
        assert "probe/job-" not in refs


# --- branch / push safety ---------------------------------------------------


class TestPushSafety:
    def test_validate_push_target_rejects_base_branch(self):
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.validate_push_target("main", "main", "main")

    def test_validate_push_target_rejects_default_branch(self):
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.validate_push_target("probe/job-1-abcdef01", "develop", "probe/job-1-abcdef01")

    def test_validate_push_target_accepts_generated_branch(self):
        publish_guards.validate_push_target("probe/job-1-abcdef01", "main", "main")

    def test_allow_direct_push_true_still_fails_closed(self, monkeypatch):
        monkeypatch.setenv("GIT_ALLOW_DIRECT_PUSH", "true")
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.assert_no_unsafe_push_config()

    def test_allow_force_push_true_still_fails_closed(self, monkeypatch):
        monkeypatch.setenv("GIT_ALLOW_FORCE_PUSH", "true")
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.assert_no_unsafe_push_config()

    def test_allow_direct_push_false_is_fine(self, monkeypatch):
        monkeypatch.setenv("GIT_ALLOW_DIRECT_PUSH", "false")
        monkeypatch.setenv("GIT_ALLOW_FORCE_PUSH", "false")
        publish_guards.assert_no_unsafe_push_config()

    def test_push_uses_explicit_refspec_and_never_forces(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="pushspy")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        publish_job.approve_publish_job(job_id, ctx["system_id"], 1, spawn=False)

        captured = {}
        original_run_git = repo_manager._run_git

        def spy(cwd, args, **kwargs):
            if args and args[0] == "push":
                captured["args"] = list(args)
            return original_run_git(cwd, args, **kwargs)

        monkeypatch.setattr(repo_manager, "_run_git", spy)
        publish_job._run_publish_phase(job_id)

        assert _job_row(job_id)["status"] == "completed"
        assert captured["args"][0] == "push"
        assert captured["args"][1] == f"file://{ctx['bare_dir']}"
        assert "--force" not in captured["args"]
        assert "-f" not in captured["args"]
        assert captured["args"][2].startswith("HEAD:refs/heads/probe/job-")


# --- diff file-path staging guards ------------------------------------------


class TestDiffPathGuards:
    def test_extract_diff_paths(self):
        diff = (
            "diff --git a/pkg/foo.py b/pkg/foo.py\n"
            "index 111..222 100644\n"
            "--- a/pkg/foo.py\n"
            "+++ b/pkg/foo.py\n"
            "@@ -1,2 +1,3 @@\n"
        )
        assert publish_guards.extract_diff_paths(diff) == ["pkg/foo.py"]

    def test_rejects_path_traversal(self, tmp_path):
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.validate_patch_file_path(
                "../outside.py", str(tmp_path), allow_workflow_changes=False
            )

    def test_rejects_absolute_path(self, tmp_path):
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.validate_patch_file_path(
                "/etc/passwd", str(tmp_path), allow_workflow_changes=False
            )

    def test_rejects_dot_git(self, tmp_path):
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.validate_patch_file_path(
                ".git/config", str(tmp_path), allow_workflow_changes=False
            )

    def test_rejects_workflow_by_default_but_allowed_when_enabled(self, tmp_path):
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        (tmp_path / ".github" / "workflows" / "ci.yml").write_text("x")
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.validate_patch_file_path(
                ".github/workflows/ci.yml", str(tmp_path), allow_workflow_changes=False
            )
        publish_guards.validate_patch_file_path(
            ".github/workflows/ci.yml", str(tmp_path), allow_workflow_changes=True
        )

    @pytest.mark.parametrize(
        "name",
        [".env", ".env.local", "id_rsa", "id_rsa.pub", "credentials.json", ".netrc", "server.pem", "priv.key"],
    )
    def test_rejects_secret_candidates(self, tmp_path, name):
        (tmp_path / name).write_text("x")
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.validate_patch_file_path(name, str(tmp_path), allow_workflow_changes=False)

    def test_rejects_new_symlink(self, tmp_path):
        target = tmp_path / "real.txt"
        target.write_text("x")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        with pytest.raises(publish_guards.PublishGuardError):
            publish_guards.validate_patch_file_path("link.txt", str(tmp_path), allow_workflow_changes=False)

    def test_accepts_normal_file(self, tmp_path):
        (tmp_path / "app.py").write_text("x")
        result = publish_guards.validate_patch_file_path(
            "app.py", str(tmp_path), allow_workflow_changes=False
        )
        assert result == "app.py"

    def test_prepare_fails_when_diff_touches_secret_candidate_file(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="secretdiff")
        bad_diff = _make_new_file_diff(".env", "SECRET=1\n")
        with get_conn() as conn:
            conn.execute("UPDATE probe_patches SET diff = ? WHERE id = ?", (bad_diff, ctx["patch_id"]))

        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "failed"
        assert ".env" in row["error"]
        assert row["cleanup_state"] == "removed"

    def test_prepare_fails_when_diff_touches_workflow_file(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="workflowdiff")
        bad_diff = _make_new_file_diff(".github/workflows/ci.yml", "name: ci\n")
        with get_conn() as conn:
            conn.execute("UPDATE probe_patches SET diff = ? WHERE id = ?", (bad_diff, ctx["patch_id"]))

        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "failed"
        assert "workflows" in row["error"].lower()


# --- approve / re-run idempotency --------------------------------------


class TestIdempotency:
    def test_double_approve_conflicts(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="dblapprove")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        publish_job.approve_publish_job(job_id, ctx["system_id"], 1, spawn=False)
        with pytest.raises(PublishJobConflict):
            publish_job.approve_publish_job(job_id, ctx["system_id"], 1, spawn=False)

    def test_rerunning_publish_phase_does_not_duplicate_commit_or_pr(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="rerun")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        publish_job.approve_publish_job(job_id, ctx["system_id"], 1, spawn=False)
        publish_job._run_publish_phase(job_id)
        row1 = _job_row(job_id)
        assert row1["status"] == "completed"

        publish_job._run_publish_phase(job_id)  # direct re-invocation
        row2 = _job_row(job_id)
        assert row2["commit_sha"] == row1["commit_sha"]
        assert row2["pr_number"] == row1["pr_number"]
        assert ctx["fake_calls"]["create_pr"] == 1

        count = subprocess.run(
            ["git", "-C", str(ctx["bare_dir"]), "rev-list", "--count", row1["commit_sha"]],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert count == "2"  # base commit + exactly one probe commit


# --- cancel / cleanup --------------------------------------------------


class TestCancelAndCleanup:
    def test_cancel_awaiting_approval_cleans_up_worktree(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="cancel-awaiting")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        assert _job_row(job_id)["status"] == "awaiting_approval"
        worktree = repo_manager.job_path(job_id)
        assert os.path.exists(worktree)

        publish_job.cancel_publish_job(job_id, ctx["system_id"])
        row = _job_row(job_id)
        assert row["status"] == "cancelled"
        assert row["cleanup_state"] == "removed"
        assert not os.path.exists(worktree)

    def test_cancel_after_approval_conflicts(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="cancel-late")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        publish_job.approve_publish_job(job_id, ctx["system_id"], 1, spawn=False)
        with pytest.raises(PublishJobConflict):
            publish_job.cancel_publish_job(job_id, ctx["system_id"])

    def test_failed_job_cleans_up_worktree(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="cleanup-failed")
        _add_commit_and_push(ctx["work_dir"], "extra.txt", "x\n", "moved on")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "failed"
        assert row["cleanup_state"] == "removed"
        assert not os.path.exists(repo_manager.job_path(job_id))

    def test_completed_job_cleans_up_worktree(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="cleanup-completed")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        publish_job.approve_publish_job(job_id, ctx["system_id"], 1, spawn=False)
        publish_job._run_publish_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "completed"
        assert row["cleanup_state"] == "removed"
        assert not os.path.exists(repo_manager.job_path(job_id))


# --- system isolation --------------------------------------------------


class TestSystemIsolation:
    def test_publish_job_not_visible_from_other_system(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="iso-a")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )

        token = _login(admin_client)
        system_b = _create_system(admin_client, token, "iso-b")
        h_b = _headers(token, system_b["id"])

        r = admin_client.get(f"/github/publish-jobs/{job_id}", headers=h_b)
        assert r.status_code == 404

        r = admin_client.get("/github/publish-jobs", headers=h_b)
        assert r.status_code == 200
        assert r.json() == []

        r = admin_client.post(f"/github/publish-jobs/{job_id}/approve", headers=h_b)
        assert r.status_code == 404

    def test_list_scoped_to_own_system(self, admin_client, tmp_path, monkeypatch):
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="iso-list")
        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        r = admin_client.get("/github/publish-jobs", headers=ctx["headers"])
        assert r.status_code == 200, r.text
        ids = [row["id"] for row in r.json()]
        assert job_id in ids


# --- secret hygiene ----------------------------------------------------


class TestSecretHygiene:
    def test_prepare_failure_error_never_contains_installation_token(self, admin_client, tmp_path, monkeypatch):
        secret_token = "ghs_shouldnotleak0123456789abcdef"
        ctx = _setup_ready_patch(admin_client, tmp_path, monkeypatch, name="leaky", token=secret_token)
        with get_conn() as conn:
            conn.execute(
                "UPDATE github_connections SET clone_url = ? WHERE id = ?",
                (f"file://{tmp_path / 'does-not-exist.git'}", ctx["connection_id"]),
            )

        job_id = publish_job.create_publish_job(
            ctx["system_id"], ctx["connection_id"], ctx["patch_id"], 1, spawn=False
        )
        publish_job._run_prepare_phase(job_id)
        row = _job_row(job_id)
        assert row["status"] == "failed"
        assert secret_token not in (row["error"] or "")

        r = admin_client.get(f"/github/publish-jobs/{job_id}", headers=ctx["headers"])
        assert secret_token not in r.text
