"""Secretless filesystem-spool execution worker.

The worker is deliberately passive: it receives no Docker socket, database,
repository root, or application secrets.  It claims atomically-published job
directories and executes each target inside a second bwrap boundary that
hides the spool and every workspace other than the selected one.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Dict, Optional, Union

from .execution_backend import (
    RawExecutionResult,
    _JOB_ID_RE,
    _is_within,
    _job_path,
    _real_directory,
    request_from_payload,
    result_to_payload,
)

MAX_REQUEST_BYTES = 64 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024


def _empty(text: bool) -> Union[bytes, str]:
    return "" if text else b""


def _truncate(
    value: Union[bytes, str], max_bytes: int
) -> tuple[Union[bytes, str], bool]:
    if isinstance(value, bytes):
        return (value, False) if len(value) <= max_bytes else (value[:max_bytes], True)
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="replace"), True


def _translate_path(path: str, workspace: str) -> str:
    real = os.path.realpath(path)
    if not _is_within(real, workspace):
        return path
    relative = os.path.relpath(real, workspace)
    return "/workspace" if relative == "." else os.path.join("/workspace", relative)


def execute_request(
    request,
    *,
    spool_root: str,
    workspace_root: str,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> RawExecutionResult:
    workspace_value = request.workspace_path or request.worktree_path
    if not workspace_value or not _is_within(workspace_value, workspace_root):
        return RawExecutionResult(
            -1, 0.0, _empty(request.text), _empty(request.text),
            error="Execution workspace is outside the shared workspace root",
        )
    workspace = os.path.realpath(workspace_value)
    if not os.path.isdir(workspace):
        return RawExecutionResult(
            -1, 0.0, _empty(request.text), _empty(request.text),
            error="Execution workspace does not exist",
        )
    if request.worktree_path and not _is_within(request.worktree_path, workspace):
        return RawExecutionResult(
            -1, 0.0, _empty(request.text), _empty(request.text),
            error="Execution working directory is outside its workspace",
        )

    bwrap = shutil.which("bwrap")
    if not bwrap:
        return RawExecutionResult(
            -1, 0.0, _empty(request.text), _empty(request.text),
            error="Execution worker sandbox backend is unavailable",
        )
    cwd = (
        _translate_path(request.worktree_path, workspace)
        if request.worktree_path
        else "/workspace"
    )
    command = request.command
    if isinstance(command, str):
        command_argv = ["/bin/sh", "-c", command] if request.shell else [command]
    else:
        command_argv = [
            _translate_path(part, workspace) if os.path.isabs(part) else part
            for part in command
        ]
    env: Dict[str, str] = dict(request.env or {})
    if not env:
        env = {
            "HOME": "/tmp",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "TERM": "dumb",
        }
    env["PWD"] = cwd

    argv = [
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-net",
        "--unshare-pid",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        workspace,
        "/workspace",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        spool_root,
        "--tmpfs",
        workspace_root,
        "--tmpfs",
        "/proc",
        "--tmpfs",
        "/data",
        "--tmpfs",
        "/root",
        "--chdir",
        cwd,
        *command_argv,
    ]
    start = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd="/",
            env=env,
            capture_output=True,
            timeout=request.timeout_seconds,
            text=request.text,
        )
        stdout, stdout_truncated = _truncate(completed.stdout, max_output_bytes)
        stderr, stderr_truncated = _truncate(completed.stderr, max_output_bytes)
        return RawExecutionResult(
            completed.returncode,
            (time.monotonic() - start) * 1000,
            stdout,
            stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
    except subprocess.TimeoutExpired:
        return RawExecutionResult(
            -1,
            (time.monotonic() - start) * 1000,
            _empty(request.text),
            _empty(request.text),
            timed_out=True,
        )
    except Exception as exc:
        return RawExecutionResult(
            -1,
            (time.monotonic() - start) * 1000,
            _empty(request.text),
            _empty(request.text),
            error=str(exc),
        )


def _read_request(path: str) -> dict:
    stat = os.lstat(path)
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("execution request is not a regular file")
    if stat.st_size > MAX_REQUEST_BYTES:
        raise ValueError("execution request exceeds size limit")
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("execution request must be a JSON object")
    return value


def _write_result(job_dir: str, result: RawExecutionResult) -> None:
    fd, temp_path = tempfile.mkstemp(prefix=".result-", dir=job_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(result_to_payload(result), handle, separators=(",", ":"), ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, os.path.join(job_dir, "result.json"))
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def process_job(
    jobs_root: str,
    job_id: str,
    *,
    spool_root: str,
    workspace_root: str,
    max_output_bytes: int,
) -> bool:
    job_dir = _job_path(jobs_root, job_id)
    claim_path = os.path.join(job_dir, "claimed")
    try:
        claim_fd = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(claim_fd)
    except (FileExistsError, FileNotFoundError):
        return False
    try:
        try:
            payload = _read_request(os.path.join(job_dir, "request.json"))
            if payload.get("job_id") != job_id:
                raise ValueError("execution request job id mismatch")
            request = request_from_payload(payload)
            result = execute_request(
                request,
                spool_root=spool_root,
                workspace_root=workspace_root,
                max_output_bytes=max_output_bytes,
            )
        except Exception as exc:
            result = RawExecutionResult(-1, 0.0, b"", b"", error=str(exc))
        if os.path.isdir(job_dir):
            _write_result(job_dir, result)
        return True
    except FileNotFoundError:
        # The client timed out and removed the job while it was running.
        return True


def run_once(
    spool_root: str,
    workspace_root: str,
    *,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> int:
    spool = _real_directory(spool_root, "PROBE_EXECUTION_SPOOL_ROOT")
    workspace = _real_directory(workspace_root, "PROBE_EXECUTION_WORKSPACE_ROOT")
    jobs_root = os.path.join(spool, "jobs")
    os.makedirs(jobs_root, mode=0o700, exist_ok=True)
    processed = 0
    for job_id in sorted(os.listdir(jobs_root)):
        if _JOB_ID_RE.fullmatch(job_id) and process_job(
            jobs_root,
            job_id,
            spool_root=spool,
            workspace_root=workspace,
            max_output_bytes=max_output_bytes,
        ):
            processed += 1
    return processed


def main() -> None:
    spool_root = os.getenv("PROBE_EXECUTION_SPOOL_ROOT", "")
    workspace_root = os.getenv("PROBE_EXECUTION_WORKSPACE_ROOT", "")
    poll_seconds = max(0.01, float(os.getenv("PROBE_EXECUTION_WORKER_POLL_SECONDS", "0.05")))
    max_output_bytes = max(
        1024,
        int(os.getenv("PROBE_EXECUTION_MAX_OUTPUT_BYTES", str(DEFAULT_MAX_OUTPUT_BYTES))),
    )
    while True:
        run_once(spool_root, workspace_root, max_output_bytes=max_output_bytes)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
