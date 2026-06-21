"""Validation runner for baseline/probed comparison.

Executes explicit commands from probe-agent.yml configuration in a worktree.
Network is disabled by default; environment variables are allowlisted.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from .git_ops import GitError

MAX_OUTPUT_BYTES = 64 * 1024  # 64 KiB stdout/stderr truncation


@dataclass
class CommandResult:
    command: str
    exit_code: int
    duration_ms: float
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False


@dataclass
class ValidationResult:
    variant: str  # "baseline" | "probed"
    worktree_path: str
    results: List[CommandResult] = field(default_factory=list)
    overall_success: bool = False
    total_duration_ms: float = 0.0
    error: Optional[str] = None
    network_isolation: str = "not_requested"
    trace_received: Optional[bool] = None
    trace_status: str = "not_checked"


@dataclass
class ValidationConfig:
    install_commands: List[str] = field(default_factory=list)
    test_commands: List[str] = field(default_factory=list)
    smoke_commands: List[str] = field(default_factory=list)
    timeout_seconds: int = 60
    network: bool = False
    env_allowlist: Dict[str, str] = field(default_factory=dict)


def load_validation_config_text(raw_text: str) -> ValidationConfig:
    raw = yaml.safe_load(raw_text)

    if not isinstance(raw, dict):
        raise GitError("probe-agent.yml must be a YAML mapping")

    commands = raw.get("commands", {})
    if not isinstance(commands, dict):
        raise GitError("commands section must be a mapping")

    runtime = raw.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}

    install = commands.get("install", [])
    test = commands.get("test", [])
    smoke = commands.get("smoke", [])

    if not isinstance(install, list):
        install = [str(install)] if install else []
    if not isinstance(test, list):
        test = [str(test)] if test else []
    if not isinstance(smoke, list):
        smoke = [str(smoke)] if smoke else []

    install = [str(c) for c in install if c]
    test = [str(c) for c in test if c]
    smoke = [str(c) for c in smoke if c]

    timeout = max(1, min(int(runtime.get("timeout_seconds", 60)), 300))
    network = bool(runtime.get("network", False))

    env_allowlist = {}
    env_raw = runtime.get("env", {})
    if isinstance(env_raw, dict):
        for k, v in env_raw.items():
            env_allowlist[str(k)] = str(v)

    return ValidationConfig(
        install_commands=install,
        test_commands=test,
        smoke_commands=smoke,
        timeout_seconds=timeout,
        network=network,
        env_allowlist=env_allowlist,
    )


def load_validation_config(config_path: str) -> ValidationConfig:
    if not os.path.isfile(config_path):
        raise GitError(f"probe-agent.yml not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return load_validation_config_text(f.read())


def _build_env(config: ValidationConfig, worktree_path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    env["HOME"] = os.environ.get("HOME", "/tmp")
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    env["LANG"] = os.environ.get("LANG", "C.UTF-8")
    env["TERM"] = "dumb"

    for key, value in config.env_allowlist.items():
        env[key] = value

    env["PWD"] = worktree_path
    return env


def _truncate(text: str, max_bytes: int = MAX_OUTPUT_BYTES) -> tuple:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated + "\n... [truncated]", True


def _run_command(
    command: str,
    worktree_path: str,
    env: Dict[str, str],
    timeout: int,
    network: bool = True,
) -> CommandResult:
    start = time.monotonic()
    timed_out = False
    try:
        argv: Any = command
        use_shell = True
        if not network:
            if platform.system() == "Darwin" and shutil.which("sandbox-exec"):
                argv = [
                    "sandbox-exec",
                    "-p",
                    "(version 1) (allow default) (deny network*)",
                    "/bin/sh",
                    "-c",
                    command,
                ]
                use_shell = False
            elif shutil.which("bwrap"):
                argv = [
                    "bwrap", "--unshare-net", "--dev-bind", "/", "/",
                    "/bin/sh", "-c", command,
                ]
                use_shell = False
            elif shutil.which("unshare"):
                argv = ["unshare", "--user", "--map-root-user", "--net", "/bin/sh", "-c", command]
                use_shell = False
            else:
                return CommandResult(
                    command=command,
                    exit_code=-1,
                    duration_ms=0.0,
                    stdout="",
                    stderr=(
                        "Network isolation was requested but no supported "
                        "sandbox backend is available"
                    ),
                )
        result = subprocess.run(
            argv,
            shell=use_shell,
            cwd=worktree_path,
            env=env,
            capture_output=True,
            timeout=timeout,
        )
        duration_ms = (time.monotonic() - start) * 1000

        stdout_raw = result.stdout.decode("utf-8", errors="replace")
        stderr_raw = result.stderr.decode("utf-8", errors="replace")
        stdout, stdout_truncated = _truncate(stdout_raw)
        stderr, stderr_truncated = _truncate(stderr_raw)

        return CommandResult(
            command=command,
            exit_code=result.returncode,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
    except subprocess.TimeoutExpired:
        duration_ms = (time.monotonic() - start) * 1000
        return CommandResult(
            command=command,
            exit_code=-1,
            duration_ms=duration_ms,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            timed_out=True,
        )
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        return CommandResult(
            command=command,
            exit_code=-1,
            duration_ms=duration_ms,
            stdout="",
            stderr=str(exc),
        )


def run_validation(
    variant: str,
    worktree_path: str,
    config: ValidationConfig,
) -> ValidationResult:
    if not os.path.isdir(worktree_path):
        return ValidationResult(
            variant=variant,
            worktree_path=worktree_path,
            error=f"Worktree does not exist: {worktree_path}",
        )

    env = _build_env(config, worktree_path)
    results: List[CommandResult] = []
    total_start = time.monotonic()

    all_commands = (
        [(cmd, "install") for cmd in config.install_commands]
        + [(cmd, "test") for cmd in config.test_commands]
        + [(cmd, "smoke") for cmd in config.smoke_commands]
    )

    if not config.test_commands:
        return ValidationResult(
            variant=variant,
            worktree_path=worktree_path,
            error="No test commands configured in probe-agent.yml",
        )

    overall_success = True
    isolation_failed = False
    for command, phase in all_commands:
        cmd_result = _run_command(
            command, worktree_path, env, config.timeout_seconds, config.network,
        )
        results.append(cmd_result)
        if not config.network and (
            "unshare failed" in cmd_result.stderr
            or "sandbox backend is available" in cmd_result.stderr
            or "sandbox-exec" in cmd_result.stderr.lower()
            and "operation not permitted" in cmd_result.stderr.lower()
        ):
            isolation_failed = True
        if cmd_result.exit_code != 0:
            overall_success = False
            if phase in ("install", "test"):
                break

    total_duration_ms = (time.monotonic() - total_start) * 1000

    return ValidationResult(
        variant=variant,
        worktree_path=worktree_path,
        results=results,
        overall_success=overall_success,
        total_duration_ms=total_duration_ms,
        network_isolation=(
            "disabled" if config.network
            else ("failed" if isolation_failed else "enforced")
        ),
    )
