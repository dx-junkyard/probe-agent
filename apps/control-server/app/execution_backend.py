"""Execution backends shared by validation, replay, experiments, and candidates.

The in-process backend preserves development compatibility.  The production
worker backend publishes jobs through a dedicated filesystem spool, keeping
Docker control and caller-specific result shapes outside this boundary.
"""

from __future__ import annotations

import os
import platform
import base64
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Sequence, Union


Command = Union[str, Sequence[str]]


@dataclass(frozen=True)
class ExecutionRequest:
    """Backend-neutral description of one process execution.

    ``network_off`` requests the existing fail-closed OS sandbox.  It is
    false only for the legacy inline-candidate path, whose restricted Python
    builtins jail and direct-host execution are retained for compatibility.
    """

    command: Command
    worktree_path: Optional[str]
    env: Optional[Dict[str, str]]
    timeout_seconds: float
    shell: bool
    text: bool = False
    network_off: bool = True
    # Files that the command needs live below this root.  It is normally the
    # cwd, but the legacy inline-candidate command keeps cwd=None while using
    # absolute harness paths from this workspace.
    workspace_path: Optional[str] = None


@dataclass(frozen=True)
class RawExecutionResult:
    """Raw backend result, before caller-specific result adaptation."""

    returncode: int
    duration_ms: float
    stdout: Union[bytes, str]
    stderr: Union[bytes, str]
    timed_out: bool = False
    error: Optional[str] = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class ExecutionBackend(Protocol):
    """Replaceable execution boundary for local or worker execution."""

    def execute(self, request: ExecutionRequest) -> RawExecutionResult:
        ...


class InProcessExecutionBackend:
    """Current host subprocess implementation, including network isolation."""

    def execute(self, request: ExecutionRequest) -> RawExecutionResult:
        start = time.monotonic()
        argv: Command = request.command
        use_shell = request.shell

        if request.network_off:
            worktree_path = request.worktree_path
            if worktree_path is None:
                return RawExecutionResult(
                    returncode=-1,
                    duration_ms=0.0,
                    stdout="" if request.text else b"",
                    stderr=(
                        "Network-off execution requires a worktree path"
                        if request.text
                        else b"Network-off execution requires a worktree path"
                    ),
                )
            if os.getenv("PROBE_UNSAFE_ALLOW_HOST_EXECUTION", "").lower() in (
                "1",
                "true",
                "yes",
            ):
                pass
            elif platform.system() == "Darwin" and shutil.which("sandbox-exec"):
                escaped = worktree_path.replace('"', '\\"')
                argv = [
                    "sandbox-exec",
                    "-p",
                    (
                        "(version 1) (deny default) "
                        "(allow process*) (allow file-read*) "
                        f'(allow file-write* (subpath "{escaped}") (subpath "/tmp")) '
                        "(deny network*)"
                    ),
                    "/bin/sh",
                    "-c",
                    str(request.command),
                ]
                use_shell = False
            elif shutil.which("bwrap"):
                argv = [
                    "bwrap",
                    "--die-with-parent",
                    "--new-session",
                    "--unshare-net",
                    "--ro-bind",
                    "/",
                    "/",
                    "--tmpfs",
                    "/tmp",
                    "--tmpfs",
                    "/data",
                    "--tmpfs",
                    "/root",
                    "--tmpfs",
                    "/repositories",
                    "--bind",
                    worktree_path,
                    "/workspace",
                    "--chdir",
                    "/workspace",
                    "/bin/sh",
                    "-c",
                    str(request.command),
                ]
                use_shell = False
            else:
                return RawExecutionResult(
                    returncode=-1,
                    duration_ms=0.0,
                    stdout="" if request.text else b"",
                    stderr=(
                        "Network isolation was requested but no supported "
                        "sandbox backend is available"
                    ) if request.text else (
                        b"Network isolation was requested but no supported "
                        b"sandbox backend is available"
                    ),
                )

        try:
            result = subprocess.run(
                argv,
                shell=use_shell,
                cwd=request.worktree_path,
                env=request.env,
                capture_output=True,
                timeout=request.timeout_seconds,
                text=request.text,
            )
            return RawExecutionResult(
                returncode=result.returncode,
                duration_ms=(time.monotonic() - start) * 1000,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except subprocess.TimeoutExpired:
            return RawExecutionResult(
                returncode=-1,
                duration_ms=(time.monotonic() - start) * 1000,
                stdout="" if request.text else b"",
                stderr="" if request.text else b"",
                timed_out=True,
            )
        except Exception as exc:
            return RawExecutionResult(
                returncode=-1,
                duration_ms=(time.monotonic() - start) * 1000,
                stdout="" if request.text else b"",
                stderr="" if request.text else b"",
                error=str(exc),
            )


_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_PROTOCOL_VERSION = 1
DEFAULT_MAX_RESULT_BYTES = 256 * 1024
DEFAULT_POLL_INTERVAL_SECONDS = 0.05
DEFAULT_RESULT_GRACE_SECONDS = 5.0


def _real_directory(path: str, setting: str) -> str:
    if not path or not os.path.isabs(path):
        raise RuntimeError(f"{setting} must be an existing absolute directory")
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise RuntimeError(f"{setting} must be an existing absolute directory")
    if not os.access(real, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError(f"{setting} must be readable and writable")
    return real


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((os.path.realpath(path), root)) == root
    except ValueError:
        return False


def _job_path(jobs_root: str, job_id: str) -> str:
    if not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid execution job id")
    path = os.path.join(jobs_root, job_id)
    if os.path.commonpath((os.path.realpath(path), jobs_root)) != jobs_root:
        raise ValueError("execution job path escapes spool root")
    return path


def request_to_payload(job_id: str, request: ExecutionRequest) -> Dict[str, Any]:
    command: Union[str, list]
    if isinstance(request.command, str):
        command = request.command
    else:
        command = [str(part) for part in request.command]
    return {
        "protocol_version": _PROTOCOL_VERSION,
        "job_id": job_id,
        "command": command,
        "worktree_path": request.worktree_path,
        "workspace_path": request.workspace_path,
        "env": request.env,
        "timeout_seconds": request.timeout_seconds,
        "shell": request.shell,
        "text": request.text,
        "network_off": request.network_off,
    }


def request_from_payload(payload: Dict[str, Any]) -> ExecutionRequest:
    if payload.get("protocol_version") != _PROTOCOL_VERSION:
        raise ValueError("unsupported execution protocol version")
    job_id = str(payload.get("job_id", ""))
    if not _JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid execution job id")
    command = payload.get("command")
    if not isinstance(command, (str, list)) or (
        isinstance(command, list) and not all(isinstance(v, str) for v in command)
    ):
        raise ValueError("invalid execution command")
    env = payload.get("env")
    if env is not None and (
        not isinstance(env, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
    ):
        raise ValueError("invalid execution environment")
    timeout = payload.get("timeout_seconds")
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ValueError("invalid execution timeout")
    worktree_path = payload.get("worktree_path")
    workspace_path = payload.get("workspace_path")
    if worktree_path is not None and not isinstance(worktree_path, str):
        raise ValueError("invalid execution working directory")
    if workspace_path is not None and not isinstance(workspace_path, str):
        raise ValueError("invalid execution workspace")
    return ExecutionRequest(
        command=command,
        worktree_path=worktree_path,
        workspace_path=workspace_path,
        env=env,
        timeout_seconds=float(timeout),
        shell=bool(payload.get("shell")),
        text=bool(payload.get("text")),
        network_off=bool(payload.get("network_off")),
    )


def result_to_payload(result: RawExecutionResult) -> Dict[str, Any]:
    def encode(value: Union[bytes, str]) -> Dict[str, str]:
        if isinstance(value, bytes):
            return {
                "encoding": "base64",
                "data": base64.b64encode(value).decode("ascii"),
            }
        return {"encoding": "text", "data": value}

    return {
        "protocol_version": _PROTOCOL_VERSION,
        "returncode": result.returncode,
        "duration_ms": result.duration_ms,
        "stdout": encode(result.stdout),
        "stderr": encode(result.stderr),
        "timed_out": result.timed_out,
        "error": result.error,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
    }


def result_from_payload(payload: Dict[str, Any]) -> RawExecutionResult:
    if payload.get("protocol_version") != _PROTOCOL_VERSION:
        raise ValueError("unsupported execution result protocol version")

    def decode(value: Any) -> Union[bytes, str]:
        if not isinstance(value, dict) or not isinstance(value.get("data"), str):
            raise ValueError("invalid execution output")
        if value.get("encoding") == "text":
            return value["data"]
        if value.get("encoding") == "base64":
            return base64.b64decode(value["data"], validate=True)
        raise ValueError("invalid execution output encoding")

    return RawExecutionResult(
        returncode=int(payload["returncode"]),
        duration_ms=float(payload["duration_ms"]),
        stdout=decode(payload.get("stdout")),
        stderr=decode(payload.get("stderr")),
        timed_out=bool(payload.get("timed_out")),
        error=(str(payload["error"]) if payload.get("error") is not None else None),
        stdout_truncated=bool(payload.get("stdout_truncated")),
        stderr_truncated=bool(payload.get("stderr_truncated")),
    )


class WorkerExecutionBackend:
    """Filesystem-spool client; never requires a Docker socket."""

    def __init__(
        self,
        spool_root: str,
        workspace_root: str,
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        result_grace_seconds: float = DEFAULT_RESULT_GRACE_SECONDS,
        max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
        worker_uid: Optional[int] = None,
    ) -> None:
        self.spool_root = _real_directory(spool_root, "PROBE_EXECUTION_SPOOL_ROOT")
        self.workspace_root = _real_directory(
            workspace_root, "PROBE_EXECUTION_WORKSPACE_ROOT"
        )
        if _is_within(self.spool_root, self.workspace_root) or _is_within(
            self.workspace_root, self.spool_root
        ):
            raise RuntimeError("execution spool and workspace roots must be separate")
        self.jobs_root = os.path.join(self.spool_root, "jobs")
        os.makedirs(self.jobs_root, mode=0o700, exist_ok=True)
        self.worker_uid = worker_uid
        if self.worker_uid is not None:
            if self.worker_uid < 1:
                raise RuntimeError("PROBE_EXECUTION_WORKER_UID must be a positive integer")
            # Named volumes may predate the worker service.  Control Server
            # runs as root in Compose and repairs only these two dedicated
            # roots so the non-root worker can traverse them.
            os.chown(self.spool_root, self.worker_uid, self.worker_uid)
            os.chown(self.jobs_root, self.worker_uid, self.worker_uid)
            os.chown(self.workspace_root, self.worker_uid, self.worker_uid)
        self.poll_interval_seconds = max(0.001, poll_interval_seconds)
        self.result_grace_seconds = max(0.0, result_grace_seconds)
        self.max_result_bytes = max(1024, max_result_bytes)

    def execute(self, request: ExecutionRequest) -> RawExecutionResult:
        workspace = request.workspace_path or request.worktree_path
        if not workspace or not _is_within(workspace, self.workspace_root):
            return RawExecutionResult(
                -1, 0.0, "" if request.text else b"", "" if request.text else b"",
                error="Execution workspace is outside PROBE_EXECUTION_WORKSPACE_ROOT",
            )
        if not os.path.isdir(os.path.realpath(workspace)):
            return RawExecutionResult(
                -1, 0.0, "" if request.text else b"", "" if request.text else b"",
                error="Execution workspace does not exist",
            )
        if self.worker_uid is not None:
            try:
                self._assign_workspace(workspace)
            except OSError as exc:
                return RawExecutionResult(
                    -1, 0.0, "" if request.text else b"", "" if request.text else b"",
                    error=f"Execution workspace ownership setup failed: {exc}",
                )

        job_id = secrets.token_hex(16)
        job_dir = _job_path(self.jobs_root, job_id)
        temp_dir = tempfile.mkdtemp(prefix=".publish-", dir=self.jobs_root)
        request_path = os.path.join(temp_dir, "request.json")
        try:
            payload = request_to_payload(job_id, request)
            with open(request_path, "x", encoding="utf-8") as handle:
                os.chmod(request_path, 0o600)
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            if self.worker_uid is not None:
                os.chown(request_path, self.worker_uid, self.worker_uid)
                os.chown(temp_dir, self.worker_uid, self.worker_uid)
            os.replace(temp_dir, job_dir)
            temp_dir = ""
            result_path = os.path.join(job_dir, "result.json")
            deadline = time.monotonic() + request.timeout_seconds + self.result_grace_seconds
            while time.monotonic() < deadline:
                try:
                    stat = os.lstat(result_path)
                except FileNotFoundError:
                    time.sleep(self.poll_interval_seconds)
                    continue
                if not os.path.isfile(result_path) or os.path.islink(result_path):
                    raise ValueError("execution result is not a regular file")
                if stat.st_size > self.max_result_bytes:
                    raise ValueError("execution result exceeds size limit")
                with open(result_path, "r", encoding="utf-8") as handle:
                    return result_from_payload(json.load(handle))
            return RawExecutionResult(
                -1,
                (request.timeout_seconds + self.result_grace_seconds) * 1000,
                "" if request.text else b"",
                "" if request.text else b"",
                error="Execution worker did not return a result before the backend deadline",
            )
        except Exception as exc:
            return RawExecutionResult(
                -1, 0.0, "" if request.text else b"", "" if request.text else b"",
                error=str(exc),
            )
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            shutil.rmtree(job_dir, ignore_errors=True)

    def _assign_workspace(self, workspace: str) -> None:
        assert self.worker_uid is not None
        os.chown(workspace, self.worker_uid, self.worker_uid, follow_symlinks=False)
        for root, directories, files in os.walk(workspace, followlinks=False):
            for name in directories + files:
                os.chown(
                    os.path.join(root, name),
                    self.worker_uid,
                    self.worker_uid,
                    follow_symlinks=False,
                )


_DEFAULT_BACKEND: Optional[ExecutionBackend] = None


def execution_backend_name() -> str:
    from .environment import is_production

    raw = os.getenv("PROBE_EXECUTION_BACKEND", "").strip().lower()
    if not raw:
        return "worker" if is_production() else "inprocess"
    if raw not in {"inprocess", "worker"}:
        raise RuntimeError(
            "PROBE_EXECUTION_BACKEND must be one of ['inprocess', 'worker']"
        )
    if is_production() and raw != "worker":
        raise RuntimeError("CONTROL_ENV=production requires PROBE_EXECUTION_BACKEND=worker")
    return raw


def _validate_workspace_bases(workspace_root: str) -> None:
    from .environment import is_production

    names = (
        "PROBE_WORKTREE_BASE",
        "PROBE_REPLAY_WORKSPACE_BASE",
        "PROBE_EXPERIMENT_WORKSPACE_BASE",
    )
    for name in names:
        value = os.getenv(name, "").strip()
        if is_production() and not value:
            raise RuntimeError(f"CONTROL_ENV=production requires {name}")
        if value and (not os.path.isabs(value) or not _is_within(value, workspace_root)):
            raise RuntimeError(f"{name} must be below PROBE_EXECUTION_WORKSPACE_ROOT")


def configure_execution_backend() -> ExecutionBackend:
    global _DEFAULT_BACKEND

    name = execution_backend_name()
    if name == "inprocess":
        backend: ExecutionBackend = InProcessExecutionBackend()
    else:
        spool_root = os.getenv("PROBE_EXECUTION_SPOOL_ROOT", "").strip()
        workspace_root = os.getenv("PROBE_EXECUTION_WORKSPACE_ROOT", "").strip()
        worker_uid_raw = os.getenv("PROBE_EXECUTION_WORKER_UID", "").strip()
        try:
            worker_uid = int(worker_uid_raw) if worker_uid_raw else None
        except ValueError as exc:
            raise RuntimeError("PROBE_EXECUTION_WORKER_UID must be a positive integer") from exc
        backend = WorkerExecutionBackend(
            spool_root, workspace_root, worker_uid=worker_uid
        )
        _validate_workspace_bases(backend.workspace_root)
    _DEFAULT_BACKEND = backend
    return backend


def validate_execution_backend_startup() -> None:
    configure_execution_backend()


def get_execution_backend() -> ExecutionBackend:
    """Return the process-wide backend used when a caller does not inject one."""

    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = configure_execution_backend()
    return _DEFAULT_BACKEND
