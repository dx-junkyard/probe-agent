"""Explicit repository resync job tests (Issue #277)."""

import subprocess
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-resync.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "root", "password": "s3cret"}
    )
    assert response.status_code == 200, response.text
    return response.cookies.get("probe_session")


def _headers(token, system_id):
    return {
        "Authorization": f"Bearer {token}",
        "X-Probe-System-Id": str(system_id),
    }


def _create_system(client, token, name="sys"):
    response = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "main.py").write_text("def hello():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _configure(client, token, system_id, repo):
    response = client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": [], "exclude_patterns": []},
        headers=_headers(token, system_id),
    )
    assert response.status_code in (200, 201), response.text


def _ready_snapshot_row(system_id):
    from app.db import get_conn

    with get_conn() as conn:
        return conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/repo', ?, 'ready', ?, ?)""",
            (system_id, f"{system_id:040x}"[-40:], time.time(), time.time()),
        ).lastrowid


def _run_jobs_inline(monkeypatch):
    from app import repository_resync_jobs as jobs

    monkeypatch.setattr(
        jobs, "_spawn", lambda job_id, system_id: jobs._execute_job(job_id, system_id)
    )


def test_startup_recovery_releases_active_jobs_and_preserves_system_scope(
    admin_client, monkeypatch
):
    from app import repository_resync_jobs as jobs
    from app.db import get_conn

    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "recovery-a")
    system_b = _create_system(admin_client, token, "recovery-b")
    snapshot_b = _ready_snapshot_row(system_b)
    now = time.time()
    with get_conn() as conn:
        queued = conn.execute(
            "INSERT INTO repository_resync_jobs (system_id, status, created_at) "
            "VALUES (?, 'queued', ?)",
            (system_a, now),
        ).lastrowid
        indexing = conn.execute(
            """INSERT INTO repository_resync_jobs
                (system_id, snapshot_id, status, created_at, started_at)
               VALUES (?, ?, 'indexing', ?, ?)""",
            (system_b, snapshot_b, now, now),
        ).lastrowid

    assert jobs.recover_interrupted_repository_resync_jobs() == 2
    with get_conn() as conn:
        a = conn.execute(
            "SELECT * FROM repository_resync_jobs WHERE id = ?", (queued,)
        ).fetchone()
        b = conn.execute(
            "SELECT * FROM repository_resync_jobs WHERE id = ?", (indexing,)
        ).fetchone()
    assert a["system_id"] == system_a and a["status"] == "snapshot_failed"
    assert b["system_id"] == system_b and b["status"] == "index_failed"
    assert a["completed_at"] is not None and b["completed_at"] is not None
    assert "restarted" in a["error"] and "restarted" in b["error"]

    monkeypatch.setattr(jobs, "_spawn", lambda _job_id, _system_id: None)
    assert jobs.start_repository_resync_job(system_a) != queued
    assert jobs.start_repository_resync_job(system_b) != indexing


def test_worker_spawn_failure_is_terminal_and_does_not_hold_active_lock(
    admin_client, monkeypatch
):
    from app import repository_resync_jobs as jobs
    from app.db import get_conn

    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "spawn-failure")

    def fail_spawn(_job_id, _system_id):
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(jobs, "_spawn", fail_spawn)
    failed_id = jobs.start_repository_resync_job(system_id)
    with get_conn() as conn:
        failed = conn.execute(
            "SELECT * FROM repository_resync_jobs WHERE system_id = ? ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
    assert failed["id"] == failed_id
    assert failed["status"] == "snapshot_failed"
    assert failed["completed_at"] is not None
    assert "Could not start" in failed["error"]

    monkeypatch.setattr(jobs, "_spawn", lambda _job_id, _system_id: None)
    assert jobs.start_repository_resync_job(system_id) != failed["id"]


def test_resync_runs_snapshot_then_index_without_mutating_repository(
    admin_client, git_repo, monkeypatch
):
    _run_jobs_inline(monkeypatch)
    token = _login(admin_client)
    system_id = _create_system(admin_client, token)
    _configure(admin_client, token, system_id, git_repo)
    headers = _headers(token, system_id)

    head_before = _git(git_repo, "rev-parse", "HEAD")
    (git_repo / "untracked.txt").write_text("keep me untracked\n")
    status_before = _git(git_repo, "status", "--porcelain")

    response = admin_client.post("/repository/resync", headers=headers)

    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "completed"
    assert job["snapshot_id"] is not None
    assert job["stale_capability_count"] == 0
    assert _git(git_repo, "rev-parse", "HEAD") == head_before
    assert _git(git_repo, "status", "--porcelain") == status_before

    latest = admin_client.get("/repository/resync/latest", headers=headers).json()
    assert latest["id"] == job["id"]
    symbols = admin_client.get("/repository/symbols", headers=headers).json()
    assert symbols["snapshot_id"] == job["snapshot_id"]
    assert symbols["symbol_count"] >= 1


def test_active_job_conflicts_and_jobs_are_system_scoped(
    admin_client, git_repo, monkeypatch
):
    from app import repository_resync_jobs as jobs

    monkeypatch.setattr(jobs, "_spawn", lambda _job_id, _system_id: None)
    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "a")
    system_b = _create_system(admin_client, token, "b")
    _configure(admin_client, token, system_a, git_repo)
    _configure(admin_client, token, system_b, git_repo)
    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b)

    first = admin_client.post("/repository/resync", headers=headers_a)
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "queued"
    conflict = admin_client.post("/repository/resync", headers=headers_a)
    assert conflict.status_code == 409, conflict.text

    other = admin_client.post("/repository/resync", headers=headers_b)
    assert other.status_code == 202, other.text
    assert other.json()["id"] != first.json()["id"]
    hidden = admin_client.get(
        f"/repository/resync/{first.json()['id']}", headers=headers_b
    )
    assert hidden.status_code == 404
    assert admin_client.get(
        "/repository/resync/latest", headers=headers_b
    ).json()["id"] == other.json()["id"]


def test_sdk_token_cannot_access_resync_management_endpoints(
    admin_client, git_repo, monkeypatch
):
    from app import repository_resync_jobs as jobs

    monkeypatch.setattr(jobs, "_spawn", lambda _job_id, _system_id: None)
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sdk-boundary")
    _configure(admin_client, token, system_id, git_repo)
    session_headers = _headers(token, system_id)
    job = admin_client.post("/repository/resync", headers=session_headers).json()
    sdk = admin_client.post(
        "/tokens/me",
        json={"name": "sdk", "system_id": system_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sdk.status_code == 201, sdk.text
    sdk_headers = {"X-Api-Key": sdk.json()["token"]}

    assert admin_client.post("/repository/resync", headers=sdk_headers).status_code == 403
    assert admin_client.get("/repository/resync/latest", headers=sdk_headers).status_code == 403
    assert admin_client.get(
        f"/repository/resync/{job['id']}", headers=sdk_headers
    ).status_code == 403


def test_index_failure_retains_ready_snapshot(admin_client, git_repo, monkeypatch):
    _run_jobs_inline(monkeypatch)
    token = _login(admin_client)
    system_id = _create_system(admin_client, token)
    _configure(admin_client, token, system_id, git_repo)
    headers = _headers(token, system_id)

    from app import repository_refresh_service

    def fail_index(_system_id, _snapshot_id=None):
        raise RuntimeError("deterministic index failed")

    monkeypatch.setattr(repository_refresh_service, "index_symbols_service", fail_index)
    response = admin_client.post("/repository/resync", headers=headers)

    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "index_failed"
    assert job["snapshot_id"] is not None
    assert "deterministic index failed" in job["error"]
    latest_snapshot = admin_client.get(
        "/repository/snapshots/latest", headers=headers
    ).json()
    assert latest_snapshot["id"] == job["snapshot_id"]
    assert latest_snapshot["status"] == "ready"


def test_snapshot_failure_is_a_distinct_terminal_state(
    admin_client, git_repo, monkeypatch
):
    _run_jobs_inline(monkeypatch)
    token = _login(admin_client)
    system_id = _create_system(admin_client, token)
    _configure(admin_client, token, system_id, git_repo)

    from app.git_ops import GitError
    from app import repository_refresh_service

    def fail_snapshot(*_args, **_kwargs):
        raise GitError("read failed")

    monkeypatch.setattr(repository_refresh_service, "create_snapshot", fail_snapshot)
    response = admin_client.post(
        "/repository/resync", headers=_headers(token, system_id)
    )

    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "snapshot_failed"
    assert job["snapshot_id"] is not None
    assert "read failed" in job["error"]


def test_completed_resync_counts_old_capabilities_and_projects_system_state(
    admin_client, git_repo, monkeypatch
):
    _run_jobs_inline(monkeypatch)
    token = _login(admin_client)
    system_id = _create_system(admin_client, token)
    _configure(admin_client, token, system_id, git_repo)
    headers = _headers(token, system_id)
    old_snapshot = admin_client.post(
        "/repository/snapshots", headers=headers
    ).json()

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model,
                prompt_version, schema_version, decision_method, status,
                is_mock, started_at, completed_at)
               VALUES (?, ?, 'capability_hierarchy', 'deterministic', 'test',
                       'test', 'test', 'deterministic', 'completed', 0, ?, ?)""",
            (system_id, old_snapshot["id"], now, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO capability_hierarchy_nodes
               (system_id, snapshot_id, intelligence_run_id, node_type, name, created_at)
               VALUES (?, ?, ?, 'capability', 'Old capability', ?)""",
            (system_id, old_snapshot["id"], run_id, now),
        )
        # A newer failed run must not hide the last usable hierarchy when the
        # resync job reports how much understanding is still based on old code.
        conn.execute(
            """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model,
                prompt_version, schema_version, decision_method, status,
                is_mock, started_at, completed_at, error_details)
               VALUES (?, ?, 'capability_hierarchy', 'deterministic', 'test',
                       'test', 'test', 'deterministic', 'failed', 0, ?, ?, 'failed')""",
            (system_id, old_snapshot["id"], now + 1, now + 1),
        )

    from app.repository_resync_jobs import _stale_capability_count

    with get_conn() as conn:
        successful = conn.execute(
            "SELECT id, snapshot_id FROM intelligence_runs "
            "WHERE system_id = ? AND run_type = 'capability_hierarchy' "
            "AND status = 'completed' ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
        assert successful is not None
        assert successful["snapshot_id"] == old_snapshot["id"]
        assert conn.execute(
            "SELECT COUNT(*) AS cnt FROM capability_hierarchy_nodes "
            "WHERE system_id = ? AND intelligence_run_id = ? AND node_type = 'capability'",
            (system_id, successful["id"]),
        ).fetchone()["cnt"] == 1
    assert _stale_capability_count(system_id, -1) == 1

    response = admin_client.post("/repository/resync", headers=headers)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["status"] == "completed"
    assert job["snapshot_id"] != old_snapshot["id"]
    assert job["stale_capability_count"] == 1

    state = admin_client.get("/system-state", headers=headers).json()
    item = next(
        item for item in state["items"]
        if item["state_id"] == "repository.resync.capabilities_stale"
    )
    assert item["evidence"]["stale_capability_count"] == 1
    assert item["target_ui"]["route"] == "/system-understanding"
    assert item["target_ui"]["anchor"] == "build"

    # Once a completed hierarchy exists for the resynced snapshot, read paths
    # recompute the effective stale count as zero without mutating job audit.
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model,
                prompt_version, schema_version, decision_method, status,
                is_mock, started_at, completed_at)
               VALUES (?, ?, 'capability_hierarchy', 'deterministic', 'test',
                       'test', 'test', 'deterministic', 'completed', 0, ?, ?)""",
            (system_id, job["snapshot_id"], now + 2, now + 2),
        )
    refreshed = admin_client.get("/repository/resync/latest", headers=headers).json()
    assert refreshed["stale_capability_count"] == 0
    with get_conn() as conn:
        persisted = conn.execute(
            "SELECT stale_capability_count FROM repository_resync_jobs WHERE id = ?",
            (job["id"],),
        ).fetchone()[0]
    assert persisted == 1
    refreshed_state = admin_client.get("/system-state", headers=headers).json()
    assert all(
        state_item["state_id"] != "repository.resync.capabilities_stale"
        for state_item in refreshed_state["items"]
    )


def test_stale_capability_guidance_ignores_non_latest_job_snapshot(
    admin_client, git_repo, monkeypatch
):
    _run_jobs_inline(monkeypatch)
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "newer-snapshot")
    _configure(admin_client, token, system_id, git_repo)
    headers = _headers(token, system_id)
    old_snapshot = admin_client.post("/repository/snapshots", headers=headers).json()

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model,
                prompt_version, schema_version, decision_method, status,
                is_mock, started_at, completed_at)
               VALUES (?, ?, 'capability_hierarchy', 'deterministic', 'test',
                       'test', 'test', 'deterministic', 'completed', 0, ?, ?)""",
            (system_id, old_snapshot["id"], now, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO capability_hierarchy_nodes
               (system_id, snapshot_id, intelligence_run_id, node_type, name, created_at)
               VALUES (?, ?, ?, 'capability', 'Old', ?)""",
            (system_id, old_snapshot["id"], run_id, now),
        )
    job = admin_client.post("/repository/resync", headers=headers).json()
    assert job["stale_capability_count"] == 1

    newer = admin_client.post("/repository/snapshots", headers=headers)
    assert newer.status_code == 201, newer.text
    effective = admin_client.get("/repository/resync/latest", headers=headers).json()
    assert effective["snapshot_id"] == job["snapshot_id"]
    assert effective["stale_capability_count"] == 0
    state = admin_client.get("/system-state", headers=headers).json()
    assert all(
        item["state_id"] != "repository.resync.capabilities_stale"
        for item in state["items"]
    )
