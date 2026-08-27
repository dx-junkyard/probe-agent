"""Execution backend contract and caller-injection regression tests."""

import json
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from app.execution_backend import ExecutionRequest, RawExecutionResult


class RecordingBackend:
    def __init__(self, result=None, on_execute=None):
        self.requests = []
        self.result = result or RawExecutionResult(
            returncode=0,
            duration_ms=12.5,
            stdout=b"ok\n",
            stderr=b"",
        )
        self.on_execute = on_execute

    def execute(self, request: ExecutionRequest) -> RawExecutionResult:
        self.requests.append(request)
        if self.on_execute:
            self.on_execute(request)
        return self.result


def test_run_command_adapts_backend_result_without_contract_drift(tmp_path):
    from app.validation_runner import MAX_OUTPUT_BYTES, _run_command

    backend = RecordingBackend(
        RawExecutionResult(
            returncode=7,
            duration_ms=18.25,
            stdout=(b"x" * (MAX_OUTPUT_BYTES + 1)),
            stderr=b"problem",
        )
    )
    env = {"PATH": "/usr/bin", "ONLY_ALLOWED": "yes"}

    result = _run_command(
        "do-work", str(tmp_path), env, 23, network=False, backend=backend
    )

    assert result.command == "do-work"
    assert result.exit_code == 7
    assert result.duration_ms == 18.25
    assert result.stdout.endswith("\n... [truncated]")
    assert result.stdout_truncated is True
    assert result.stderr == "problem"
    assert result.stderr_truncated is False
    assert result.timed_out is False
    request = backend.requests[0]
    assert request.command == "do-work"
    assert request.worktree_path == str(tmp_path)
    assert request.env == env
    assert request.timeout_seconds == 23
    assert request.shell is True
    assert request.text is False
    assert request.network_off is True


def test_run_command_preserves_timeout_and_network_prohibition(tmp_path):
    from app.validation_runner import _run_command

    backend = RecordingBackend(
        RawExecutionResult(
            returncode=-1,
            duration_ms=1001.0,
            stdout=b"ignored",
            stderr=b"ignored",
            timed_out=True,
        )
    )
    timed_out = _run_command("slow", str(tmp_path), {}, 1, backend=backend)
    assert timed_out.exit_code == -1
    assert timed_out.duration_ms == 1001.0
    assert timed_out.stdout == ""
    assert timed_out.stderr == "Command timed out after 1s"
    assert timed_out.timed_out is True

    prohibited = _run_command(
        "curl example.invalid", str(tmp_path), {}, 1, network=True, backend=backend
    )
    assert prohibited.stderr == "Network-enabled execution is prohibited"
    assert len(backend.requests) == 1


def test_run_command_adds_legacy_marker_to_worker_truncated_output(tmp_path):
    from app.validation_runner import MAX_OUTPUT_BYTES, _run_command

    backend = RecordingBackend(
        RawExecutionResult(
            0,
            2.0,
            b"x" * MAX_OUTPUT_BYTES,
            b"e" * MAX_OUTPUT_BYTES,
            stdout_truncated=True,
            stderr_truncated=True,
        )
    )
    result = _run_command("large", str(tmp_path), {}, 1, backend=backend)
    assert result.stdout.endswith("\n... [truncated]")
    assert result.stderr.endswith("\n... [truncated]")
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def test_run_validation_uses_injected_backend_and_allowlisted_env(
    tmp_path, monkeypatch
):
    from app.validation_runner import ValidationConfig, run_validation

    monkeypatch.setenv("PATH", "/base/bin")
    backend = RecordingBackend()
    config = ValidationConfig(
        test_commands=["test-one"],
        smoke_commands=["smoke-one"],
        timeout_seconds=17,
        env_allowlist={"EXPLICIT": "value"},
    )

    result = run_validation(
        "baseline", str(tmp_path), config, backend=backend
    )

    assert result.overall_success is True
    assert [request.command for request in backend.requests] == [
        "test-one",
        "smoke-one",
    ]
    assert all(request.env["EXPLICIT"] == "value" for request in backend.requests)
    assert all(request.env["PATH"] == "/base/bin" for request in backend.requests)
    assert all(request.network_off is True for request in backend.requests)


def test_experiment_variant_uses_injected_backend(tmp_path, monkeypatch):
    import app.experiment_runner as runner

    workspace = tmp_path / "experiment"
    workspace.mkdir()
    monkeypatch.setattr(runner, "create_worktree", lambda *_: str(workspace))
    monkeypatch.setattr(
        runner,
        "cleanup_worktree",
        lambda *_: SimpleNamespace(state="removed", error=None),
    )
    backend = RecordingBackend()

    result = runner.execute_variant(
        repo_path="unused",
        commit_sha="abc",
        workspace_base=str(tmp_path),
        patch_text="",
        execution_config={"test_commands": ["pytest -q"], "timeout_seconds": 11},
        backend=backend,
    )

    assert result.status == "completed"
    assert result.cleanup_state == "removed"
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.command == "pytest -q"
    assert request.worktree_path == str(workspace)
    assert request.timeout_seconds == 11
    assert request.network_off is True


def test_replay_harness_uses_injected_backend(tmp_path, monkeypatch):
    import app.replay_runner as runner

    workspace = tmp_path / "replay"
    workspace.mkdir()
    monkeypatch.setattr(runner, "create_worktree", lambda *_: str(workspace))
    monkeypatch.setattr(
        runner,
        "cleanup_worktree",
        lambda *_: SimpleNamespace(state="removed", error=None),
    )

    def write_result(request):
        result_path = workspace / runner.HARNESS_DIRNAME / runner.RESULT_FILENAME
        result_path.write_text(
            json.dumps(
                {
                    "target_error": None,
                    "target_traceback": None,
                    "cases": [{"status": "ok", "output": "'done'"}],
                }
            )
        )

    backend = RecordingBackend(on_execute=write_result)
    result = runner.execute_harness(
        repo_path="unused",
        commit_sha="abc",
        run_workspace_base=str(tmp_path),
        target={"kind": "symbol", "path": "a.py", "qualified_name": "f"},
        harness_cases=[{"input_kind": "values", "args": [], "kwargs": {}}],
        timeout_seconds=19,
        backend=backend,
    )

    assert result.status == "completed"
    assert result.case_results == [{"status": "ok", "output": "'done'"}]
    assert result.cleanup_state == "removed"
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.command.startswith("python3 -I .probe-replay/harness.py")
    assert request.worktree_path == str(workspace)
    assert request.timeout_seconds == 19
    assert request.env["PROBE_ENABLED"] == "false"
    assert request.env["PYTHONHASHSEED"] == "0"
    assert request.network_off is True


def test_inline_candidate_direct_process_is_behind_backend():
    from app.replay_harness import run_inline_candidate

    def write_result(request):
        result_path = request.command[-1]
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "target_error": None,
                    "target_traceback": None,
                    "cases": [{"status": "ok", "output": "42"}],
                },
                handle,
            )

    backend = RecordingBackend(
        result=RawExecutionResult(0, 3.0, "", ""),
        on_execute=write_result,
    )
    output, error = run_inline_candidate(
        "def candidate(): return 42", [], {}, timeout_seconds=5, backend=backend
    )

    assert output == "42"
    assert error is None
    request = backend.requests[0]
    assert list(request.command[:3]) == [sys.executable, "-I", "-S"]
    assert request.worktree_path is None
    assert request.env is None
    assert request.timeout_seconds == 5
    assert request.shell is False
    assert request.text is True
    assert request.network_off is False


def test_inline_candidate_projects_backend_error_to_bounded_result():
    from app.replay_harness import run_inline_candidate

    backend = RecordingBackend(
        result=RawExecutionResult(0, 1.0, "", "", error="backend unavailable")
    )
    output, error = run_inline_candidate(
        "def candidate(): return 1", [], {}, backend=backend
    )
    assert output is None
    assert error == "backend unavailable"


def test_in_process_backend_preserves_direct_raw_result(tmp_path):
    from app.execution_backend import InProcessExecutionBackend

    request = ExecutionRequest(
        command=[sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        worktree_path=str(tmp_path),
        env=os.environ.copy(),
        timeout_seconds=5,
        shell=False,
        text=False,
        network_off=False,
    )
    result = InProcessExecutionBackend().execute(request)

    assert result.returncode == 0
    assert result.stdout == b"out\n"
    assert result.stderr == b"err\n"
    assert result.timed_out is False
    assert result.error is None


def _worker_roots(tmp_path):
    spool = tmp_path / "spool"
    workspace = tmp_path / "workspaces"
    spool.mkdir()
    workspace.mkdir()
    return spool, workspace


def test_worker_backend_atomic_round_trip_and_cleanup(tmp_path, monkeypatch):
    import app.execution_worker as worker
    from app.execution_backend import WorkerExecutionBackend

    spool, workspace_root = _worker_roots(tmp_path)
    workspace = workspace_root / "job"
    workspace.mkdir()
    monkeypatch.setattr(
        worker,
        "execute_request",
        lambda request, **_: RawExecutionResult(
            4,
            8.5,
            b"raw-out",
            b"raw-err",
            stdout_truncated=True,
        ),
    )
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            if worker.run_once(str(spool), str(workspace_root)):
                return
            time.sleep(0.001)

    thread = threading.Thread(target=serve)
    thread.start()
    backend = WorkerExecutionBackend(
        str(spool),
        str(workspace_root),
        poll_interval_seconds=0.001,
        result_grace_seconds=1,
    )
    result = backend.execute(
        ExecutionRequest(
            command="do-work",
            worktree_path=str(workspace),
            workspace_path=str(workspace),
            env={"ONLY": "explicit"},
            timeout_seconds=1,
            shell=True,
        )
    )
    stop.set()
    thread.join(timeout=2)

    assert result.returncode == 4
    assert result.duration_ms == 8.5
    assert result.stdout == b"raw-out"
    assert result.stderr == b"raw-err"
    assert result.stdout_truncated is True
    assert list((spool / "jobs").iterdir()) == []


def test_worker_backend_deadline_is_backend_error_not_command_timeout(tmp_path):
    from app.execution_backend import WorkerExecutionBackend

    spool, workspace_root = _worker_roots(tmp_path)
    workspace = workspace_root / "job"
    workspace.mkdir()
    backend = WorkerExecutionBackend(
        str(spool),
        str(workspace_root),
        poll_interval_seconds=0.001,
        result_grace_seconds=0.001,
    )
    result = backend.execute(
        ExecutionRequest(
            command="slow",
            worktree_path=str(workspace),
            workspace_path=str(workspace),
            env={},
            timeout_seconds=0.01,
            shell=True,
        )
    )
    assert result.timed_out is False
    assert "worker did not return" in result.error
    assert list((spool / "jobs").iterdir()) == []


def test_worker_backend_rejects_oversized_result_and_cleans_job(tmp_path):
    from app.execution_backend import WorkerExecutionBackend

    spool, workspace_root = _worker_roots(tmp_path)
    workspace = workspace_root / "job"
    workspace.mkdir()

    def publish_oversized_result():
        jobs = spool / "jobs"
        while not jobs.exists() or not list(jobs.iterdir()):
            time.sleep(0.001)
        job_dir = next(jobs.iterdir())
        (job_dir / "result.json").write_bytes(b"x" * 2048)

    thread = threading.Thread(target=publish_oversized_result)
    thread.start()
    backend = WorkerExecutionBackend(
        str(spool),
        str(workspace_root),
        poll_interval_seconds=0.001,
        result_grace_seconds=1,
        max_result_bytes=1024,
    )
    result = backend.execute(
        ExecutionRequest(
            command="true",
            worktree_path=str(workspace),
            workspace_path=str(workspace),
            env={},
            timeout_seconds=1,
            shell=True,
        )
    )
    thread.join(timeout=2)
    assert "exceeds size limit" in result.error
    assert list((spool / "jobs").iterdir()) == []


def test_worker_backend_rejects_workspace_escape_before_publish(tmp_path):
    from app.execution_backend import WorkerExecutionBackend

    spool, workspace_root = _worker_roots(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    backend = WorkerExecutionBackend(str(spool), str(workspace_root))
    result = backend.execute(
        ExecutionRequest(
            command="true",
            worktree_path=str(outside),
            workspace_path=str(outside),
            env={},
            timeout_seconds=1,
            shell=True,
        )
    )
    assert "outside PROBE_EXECUTION_WORKSPACE_ROOT" in result.error
    assert list((spool / "jobs").iterdir()) == []


def test_job_path_rejects_traversal(tmp_path):
    from app.execution_backend import _job_path

    with pytest.raises(ValueError, match="job id"):
        _job_path(str(tmp_path), "../../escape")


def test_worker_sandbox_hides_spool_and_other_workspaces(tmp_path, monkeypatch):
    import app.execution_worker as worker

    spool, workspace_root = _worker_roots(tmp_path)
    workspace = workspace_root / "selected"
    workspace.mkdir()
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(worker.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    result = worker.execute_request(
        ExecutionRequest(
            command="echo ok",
            worktree_path=str(workspace),
            workspace_path=str(workspace),
            env={"PATH": "/usr/bin", "EXPLICIT": "yes"},
            timeout_seconds=2,
            shell=True,
        ),
        spool_root=str(spool),
        workspace_root=str(workspace_root),
    )
    assert result.returncode == 0
    argv = captured["argv"]
    assert ["--bind", str(workspace), "/workspace"] == argv[
        argv.index("--bind") : argv.index("--bind") + 3
    ]
    tmpfs_targets = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "--tmpfs"]
    assert str(spool) in tmpfs_targets
    assert str(workspace_root) in tmpfs_targets
    assert "--unshare-net" in argv
    assert "--unshare-pid" in argv
    assert "/proc" in tmpfs_targets
    assert captured["env"] == {
        "PATH": "/usr/bin",
        "EXPLICIT": "yes",
        "PWD": "/workspace",
    }


def test_worker_fails_closed_when_bwrap_is_unavailable(tmp_path, monkeypatch):
    import app.execution_worker as worker

    spool, workspace_root = _worker_roots(tmp_path)
    workspace = workspace_root / "selected"
    workspace.mkdir()
    monkeypatch.setattr(worker.shutil, "which", lambda _: None)

    def must_not_execute(*args, **kwargs):
        raise AssertionError("target subprocess must not run without sandbox")

    monkeypatch.setattr(worker.subprocess, "run", must_not_execute)
    result = worker.execute_request(
        ExecutionRequest(
            command="touch should-not-exist",
            worktree_path=str(workspace),
            workspace_path=str(workspace),
            env={},
            timeout_seconds=1,
            shell=True,
        ),
        spool_root=str(spool),
        workspace_root=str(workspace_root),
    )
    assert result.returncode == -1
    assert "sandbox backend is unavailable" in result.error
    assert not (workspace / "should-not-exist").exists()


def test_execution_backend_startup_matrix(tmp_path, monkeypatch):
    import app.execution_backend as module

    spool, workspace = _worker_roots(tmp_path)
    monkeypatch.setattr(module, "_DEFAULT_BACKEND", None)
    monkeypatch.delenv("CONTROL_ENV", raising=False)
    monkeypatch.delenv("PROBE_EXECUTION_BACKEND", raising=False)
    assert module.execution_backend_name() == "inprocess"

    monkeypatch.setenv("PROBE_EXECUTION_BACKEND", "invalid")
    with pytest.raises(RuntimeError, match="PROBE_EXECUTION_BACKEND"):
        module.configure_execution_backend()

    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.delenv("PROBE_EXECUTION_BACKEND", raising=False)
    with pytest.raises(RuntimeError, match="PROBE_EXECUTION_SPOOL_ROOT"):
        module.configure_execution_backend()

    monkeypatch.setenv("PROBE_EXECUTION_BACKEND", "inprocess")
    with pytest.raises(RuntimeError, match="requires PROBE_EXECUTION_BACKEND=worker"):
        module.configure_execution_backend()

    monkeypatch.setenv("PROBE_EXECUTION_BACKEND", "worker")
    monkeypatch.setenv("PROBE_EXECUTION_SPOOL_ROOT", str(spool))
    monkeypatch.setenv("PROBE_EXECUTION_WORKSPACE_ROOT", str(workspace))
    for name, suffix in (
        ("PROBE_WORKTREE_BASE", "validation"),
        ("PROBE_REPLAY_WORKSPACE_BASE", "replay"),
        ("PROBE_EXPERIMENT_WORKSPACE_BASE", "experiments"),
    ):
        monkeypatch.setenv(name, str(workspace / suffix))
    configured = module.configure_execution_backend()
    assert isinstance(configured, module.WorkerExecutionBackend)
