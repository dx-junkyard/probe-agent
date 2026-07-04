"""Tests for Issue #109: System Understanding build job orchestration.

Covers step-level status/provenance, completed-step reuse, LLM chunk task
retry/backoff, cancel, resume from DB state, heartbeat/stuck detection,
active job listing, and System isolation.
"""

import subprocess
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-su-jobs-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    monkeypatch.setenv("SYSTEM_UNDERSTANDING_LLM_BACKOFF_SECONDS", "0")
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


def _init_git_repo(tmp_path, readme_text=None):
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
    readme.write_text(
        readme_text
        or "# Test Project\nA test project for system understanding jobs.\n"
    )
    src = repo / "src"
    src.mkdir()
    (src / "main.py").write_text(
        'from fastapi import APIRouter\n\nrouter = APIRouter()\n\n'
        '@router.get("/items")\ndef list_items():\n    """List all items."""\n    return []\n'
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


TWO_SECTION_README = (
    "# Test Project\n\nOverview text.\n\n"
    "## SectionA\n\nSectionA describes the ingestion capability in detail.\n\n"
    "## SectionB\n\nSectionB describes the reporting capability in detail.\n"
)


def _setup_repo(client, token, tmp_path, name, readme_text=None):
    sys = _create_system(client, token, name)
    hdrs = _headers(token, sys["id"])
    repo, sha = _init_git_repo(tmp_path, readme_text=readme_text)
    client.put(
        "/repository",
        json={"repo_path": str(repo), "include_patterns": ["**"], "exclude_patterns": []},
        headers=hdrs,
    )
    snap = client.post(
        "/repository/snapshots", json={"commit_sha": sha}, headers=hdrs
    )
    assert snap.status_code == 201, snap.text
    return sys, hdrs


def _wait_job(client, hdrs, job_id, timeout=15.0):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        r = client.get(
            f"/repository/system-understanding/jobs/{job_id}", headers=hdrs
        )
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in ("completed", "partial", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    pytest.fail(f"Job {job_id} did not settle within {timeout}s: {job}")


def _steps_by_name(job):
    return {s["step"]: s for s in job["steps"]}


def _ok_scan_result(scanner_module, chunk):
    return scanner_module.ChunkScanResult(
        chunk_id=chunk.chunk_id,
        chunk_content_hash=chunk.content_hash,
        prompt_version=scanner_module.PROMPT_VERSION,
        schema_version=scanner_module.SCHEMA_VERSION,
        claims=[],
    )


def _failed_scan_result(scanner_module, chunk, error="simulated LLM timeout"):
    return scanner_module.ChunkScanResult(
        chunk_id=chunk.chunk_id,
        chunk_content_hash=chunk.content_hash,
        prompt_version=scanner_module.PROMPT_VERSION,
        schema_version=scanner_module.SCHEMA_VERSION,
        error=error,
    )


class TestJobCreation:
    def test_build_returns_job_with_steps_immediately(self, admin_client, tmp_path):
        token = _login(admin_client)
        _, hdrs = _setup_repo(admin_client, token, tmp_path, "job-create-sys")

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["job_id"] == job["id"]
        assert job["status"] in ("queued", "running", "completed", "partial")
        step_names = [s["step"] for s in job["steps"]]
        assert step_names == [
            "symbol_index",
            "entrypoint_index",
            "documentation_index",
            "claim_scan",
            "understanding_graph",
            "docs_code_reconcile",
            "capability_hierarchy",
        ]
        _wait_job(admin_client, hdrs, job["id"])

    def test_no_snapshot_fails_with_clear_error(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "no-snap-sys")
        hdrs = _headers(token, sys["id"])

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        assert r.status_code == 202, r.text
        job = _wait_job(admin_client, hdrs, r.json()["id"])
        assert job["status"] == "failed"
        assert "snapshot" in job["error"].lower()
        for step in job["steps"]:
            assert step["status"] == "blocked"


class TestStepStatusAndProvenance:
    def test_step_statuses_durations_and_provenance(self, admin_client, tmp_path):
        token = _login(admin_client)
        _, hdrs = _setup_repo(admin_client, token, tmp_path, "job-steps-sys")

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        job = _wait_job(admin_client, hdrs, r.json()["id"])
        steps = _steps_by_name(job)

        # Deterministic steps complete even though the LLM step is blocked.
        assert job["status"] == "completed"
        for name in ("symbol_index", "entrypoint_index", "documentation_index",
                     "capability_hierarchy"):
            step = steps[name]
            assert step["status"] == "completed", step
            assert step["started_at"] is not None
            assert step["completed_at"] is not None
            assert step["duration_ms"] is not None and step["duration_ms"] >= 0

        assert steps["symbol_index"]["artifact_provenance"]["symbol_count"] >= 1
        assert "intelligence_run_id" in steps["symbol_index"]["artifact_provenance"]
        assert steps["entrypoint_index"]["artifact_provenance"]["entrypoint_count"] >= 1
        assert steps["documentation_index"]["artifact_provenance"]["chunk_count"] >= 1

        # Reasoning step is blocked (mock provider), never heuristically run.
        assert steps["claim_scan"]["status"] == "blocked"
        assert "Reasoning model" in steps["claim_scan"]["error"]
        # Dependents of the blocked step are blocked, not silently skipped.
        assert steps["understanding_graph"]["status"] == "blocked"
        assert steps["docs_code_reconcile"]["status"] == "blocked"

        # Deterministic artifact counts are reported alongside run status.
        counts = job["artifact_counts"]
        assert counts["symbols"] >= 1
        assert counts["entrypoints"] >= 1

    def test_completed_steps_reused_on_second_build(self, admin_client, tmp_path):
        token = _login(admin_client)
        _, hdrs = _setup_repo(admin_client, token, tmp_path, "job-reuse-sys")

        r1 = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        _wait_job(admin_client, hdrs, r1.json()["id"])

        r2 = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        job2 = _wait_job(admin_client, hdrs, r2.json()["id"])
        assert job2["id"] != r1.json()["id"]
        steps = _steps_by_name(job2)
        assert steps["symbol_index"]["status"] == "completed"
        assert steps["symbol_index"]["reused_existing"] is True
        assert steps["entrypoint_index"]["reused_existing"] is True
        assert steps["capability_hierarchy"]["reused_existing"] is True

        from app.db import get_conn

        with get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM intelligence_runs WHERE run_type = 'symbol_index'"
            ).fetchone()[0]
        assert n == 1, "completed symbol_index must not be re-executed"


class TestLlmChunkQueue:
    def test_llm_failure_keeps_deterministic_steps_complete(
        self, admin_client, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        _, hdrs = _setup_repo(
            admin_client, token, tmp_path, "job-llm-fail-sys",
            readme_text=TWO_SECTION_README,
        )

        import app.system_understanding_service as sus
        import app.documentation_claim_scanner as scanner_module

        monkeypatch.setattr(sus, "_is_reasoning_model_available", lambda: True)
        monkeypatch.setenv("SYSTEM_UNDERSTANDING_LLM_MAX_ATTEMPTS", "2")

        scanned = []

        def _selective_scan(client, config, chunk, cache=None):
            scanned.append(chunk.chunk_id)
            if "SectionB" in chunk.content:
                return _failed_scan_result(scanner_module, chunk)
            return _ok_scan_result(scanner_module, chunk)

        monkeypatch.setattr(scanner_module, "scan_chunk", _selective_scan)

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        job = _wait_job(admin_client, hdrs, r.json()["id"])
        steps = _steps_by_name(job)

        assert job["status"] == "partial"
        assert steps["claim_scan"]["status"] == "failed"
        assert "simulated LLM timeout" in steps["claim_scan"]["error"]
        prov = steps["claim_scan"]["artifact_provenance"]
        assert prov["chunks_failed"] >= 1
        assert prov["chunks_completed"] >= 1
        # Deterministic steps stay visibly complete despite the LLM failure.
        for name in ("symbol_index", "entrypoint_index", "documentation_index",
                     "capability_hierarchy"):
            assert steps[name]["status"] == "completed", steps[name]
        # Failed chunk was retried with backoff up to max_attempts.
        tasks = job["llm_tasks"]
        assert tasks["failed"] >= 1
        assert tasks["completed"] >= 1

        from app.db import get_conn

        with get_conn() as conn:
            attempts = conn.execute(
                "SELECT attempts FROM system_understanding_llm_tasks "
                "WHERE build_id = ? AND status = 'failed'",
                (job["id"],),
            ).fetchall()
        assert all(row["attempts"] == 2 for row in attempts)

        # --- Retry: only failed chunks are re-scanned. ---
        scanned.clear()

        def _always_ok(client, config, chunk, cache=None):
            scanned.append(chunk.chunk_id)
            return _ok_scan_result(scanner_module, chunk)

        monkeypatch.setattr(scanner_module, "scan_chunk", _always_ok)

        retry = admin_client.post(
            f"/repository/system-understanding/jobs/{job['id']}/retry", headers=hdrs
        )
        assert retry.status_code == 202, retry.text
        job2 = _wait_job(admin_client, hdrs, job["id"])
        steps2 = _steps_by_name(job2)

        assert job2["status"] == "completed"
        assert steps2["claim_scan"]["status"] == "completed"
        assert steps2["understanding_graph"]["status"] == "completed"
        assert steps2["docs_code_reconcile"]["status"] == "completed"
        failed_chunks = job["llm_tasks"]["failed"]
        assert len(scanned) == failed_chunks, (
            f"retry must re-scan only the {failed_chunks} failed chunk(s), "
            f"but scanned {scanned}"
        )
        # Deterministic steps were not re-executed on retry.
        assert steps2["symbol_index"]["status"] == "completed"

        from app.db import get_conn

        with get_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM intelligence_runs WHERE run_type = 'symbol_index'"
            ).fetchone()[0]
        assert n == 1


class TestCancel:
    def test_cancel_running_job(self, admin_client, tmp_path, monkeypatch):
        token = _login(admin_client)
        _, hdrs = _setup_repo(
            admin_client, token, tmp_path, "job-cancel-sys",
            readme_text=TWO_SECTION_README,
        )

        import app.system_understanding_service as sus
        import app.documentation_claim_scanner as scanner_module

        monkeypatch.setattr(sus, "_is_reasoning_model_available", lambda: True)

        def _slow_scan(client, config, chunk, cache=None):
            time.sleep(0.5)
            return _ok_scan_result(scanner_module, chunk)

        monkeypatch.setattr(scanner_module, "scan_chunk", _slow_scan)

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        job_id = r.json()["id"]

        # Wait until the job is visibly running, then request cancellation.
        deadline = time.time() + 10.0
        while time.time() < deadline:
            active = admin_client.get(
                "/repository/system-understanding/jobs/active", headers=hdrs
            )
            assert active.status_code == 200
            if any(j["id"] == job_id for j in active.json()):
                break
            time.sleep(0.05)

        cancel = admin_client.post(
            f"/repository/system-understanding/jobs/{job_id}/cancel", headers=hdrs
        )
        assert cancel.status_code == 200, cancel.text

        job = _wait_job(admin_client, hdrs, job_id)
        assert job["status"] == "cancelled"
        statuses = {s["status"] for s in job["steps"]}
        assert "pending" not in statuses and "running" not in statuses

        # The cancelled job no longer appears active.
        active = admin_client.get(
            "/repository/system-understanding/jobs/active", headers=hdrs
        )
        assert all(j["id"] != job_id for j in active.json())

    def test_cancel_settled_job_conflicts(self, admin_client, tmp_path):
        token = _login(admin_client)
        _, hdrs = _setup_repo(admin_client, token, tmp_path, "job-cancel-done-sys")
        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        job = _wait_job(admin_client, hdrs, r.json()["id"])

        cancel = admin_client.post(
            f"/repository/system-understanding/jobs/{job['id']}/cancel", headers=hdrs
        )
        assert cancel.status_code == 409


class TestRetrySemantics:
    def test_retry_completed_step_is_refused(self, admin_client, tmp_path):
        token = _login(admin_client)
        _, hdrs = _setup_repo(admin_client, token, tmp_path, "job-retry-done-sys")
        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        job = _wait_job(admin_client, hdrs, r.json()["id"])
        assert _steps_by_name(job)["symbol_index"]["status"] == "completed"

        retry = admin_client.post(
            f"/repository/system-understanding/jobs/{job['id']}/steps/symbol_index/retry",
            headers=hdrs,
        )
        assert retry.status_code == 409
        assert "not re-executed" in retry.json()["detail"]

    def test_retry_unknown_job_returns_404(self, admin_client, tmp_path):
        token = _login(admin_client)
        sys = _create_system(admin_client, token, "job-retry-404-sys")
        hdrs = _headers(token, sys["id"])
        retry = admin_client.post(
            "/repository/system-understanding/jobs/99999/retry", headers=hdrs
        )
        assert retry.status_code == 404


class TestStuckDetectionAndResume:
    def test_stale_heartbeat_marks_job_stuck_and_retry_resumes(
        self, admin_client, tmp_path
    ):
        token = _login(admin_client)
        _, hdrs = _setup_repo(admin_client, token, tmp_path, "job-stuck-sys")
        r = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        job = _wait_job(admin_client, hdrs, r.json()["id"])
        job_id = job["id"]

        # Simulate a worker that died mid-build: the job row says running but
        # nothing updates the heartbeat anymore.
        from app.db import get_conn

        stale = time.time() - 100000
        with get_conn() as conn:
            conn.execute(
                "UPDATE system_understanding_builds SET status = 'running', "
                "completed_at = NULL, heartbeat_at = ? WHERE id = ?",
                (stale, job_id),
            )
            conn.execute(
                "UPDATE system_understanding_build_steps SET status = 'failed', "
                "error = 'interrupted' WHERE build_id = ? AND step = 'documentation_index'",
                (job_id,),
            )

        detail = admin_client.get(
            f"/repository/system-understanding/jobs/{job_id}", headers=hdrs
        )
        assert detail.status_code == 200
        assert detail.json()["is_stuck"] is True

        active = admin_client.get(
            "/repository/system-understanding/jobs/active", headers=hdrs
        )
        assert any(j["id"] == job_id and j["is_stuck"] for j in active.json())

        # A stuck job is retryable; resume re-runs only the failed step.
        retry = admin_client.post(
            f"/repository/system-understanding/jobs/{job_id}/retry", headers=hdrs
        )
        assert retry.status_code == 202, retry.text
        job2 = _wait_job(admin_client, hdrs, job_id)
        assert job2["status"] == "completed"
        assert job2["is_stuck"] is False
        assert _steps_by_name(job2)["documentation_index"]["status"] == "completed"

    def test_duplicate_build_returns_existing_active_job(
        self, admin_client, tmp_path, monkeypatch
    ):
        token = _login(admin_client)
        _, hdrs = _setup_repo(
            admin_client, token, tmp_path, "job-dup-sys",
            readme_text=TWO_SECTION_README,
        )

        import app.system_understanding_service as sus
        import app.documentation_claim_scanner as scanner_module

        monkeypatch.setattr(sus, "_is_reasoning_model_available", lambda: True)

        def _slow_scan(client, config, chunk, cache=None):
            time.sleep(0.5)
            return _ok_scan_result(scanner_module, chunk)

        monkeypatch.setattr(scanner_module, "scan_chunk", _slow_scan)

        r1 = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        r2 = admin_client.post("/repository/system-understanding/build", headers=hdrs)
        assert r1.json()["id"] == r2.json()["id"]
        _wait_job(admin_client, hdrs, r1.json()["id"])


class TestSystemIsolation:
    def test_jobs_are_scoped_by_system(self, admin_client, tmp_path):
        token = _login(admin_client)
        _, hdrs_a = _setup_repo(admin_client, token, tmp_path, "job-iso-a")
        sys_b = _create_system(admin_client, token, "job-iso-b")
        hdrs_b = _headers(token, sys_b["id"])

        r = admin_client.post("/repository/system-understanding/build", headers=hdrs_a)
        job = _wait_job(admin_client, hdrs_a, r.json()["id"])

        other = admin_client.get(
            f"/repository/system-understanding/jobs/{job['id']}", headers=hdrs_b
        )
        assert other.status_code == 404

        active_b = admin_client.get(
            "/repository/system-understanding/jobs/active", headers=hdrs_b
        )
        assert active_b.status_code == 200
        assert active_b.json() == []

        latest_b = admin_client.get(
            "/repository/system-understanding/build/latest", headers=hdrs_b
        )
        assert latest_b.status_code == 200
        assert latest_b.json() is None

        cancel = admin_client.post(
            f"/repository/system-understanding/jobs/{job['id']}/cancel", headers=hdrs_b
        )
        assert cancel.status_code == 404
