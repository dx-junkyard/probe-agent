"""Isolated source-patch experiment execution and deterministic comparison.

probe-agent:
  role: Isolated experiment executor and comparator
  capability: variant-evaluation
  element_type: core
  consumers: [dashboard, probe-planning]
  operation_kind: io
  state_effects: [filesystem, external-api]
  probe_value: Verify that experiments run in isolated worktrees and deterministic metrics are preserved when interpretation fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .patch_generator import cleanup_worktree, create_worktree
from .validation_runner import (
    MAX_OUTPUT_BYTES,
    ValidationConfig,
    _build_env,
    _run_command,
)

MAX_ARTIFACT_BYTES = 1024 * 1024


@dataclass
class ExperimentCommandResult:
    phase: str
    command: str
    exit_code: int
    duration_ms: float
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool


@dataclass
class ExperimentVariantExecution:
    status: str
    workspace_path: Optional[str] = None
    error: Optional[str] = None
    commands: List[ExperimentCommandResult] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None


def patch_hash(patch_text: str) -> str:
    return hashlib.sha256(patch_text.encode("utf-8")).hexdigest()


def _apply_patch(workspace: str, patch_text: str) -> Optional[str]:
    if not patch_text:
        return None
    check = subprocess.run(
        ["git", "-C", workspace, "apply", "--check", "--whitespace=nowarn", "-"],
        input=patch_text.encode("utf-8"),
        capture_output=True,
        timeout=30,
    )
    if check.returncode != 0:
        return check.stderr.decode("utf-8", errors="replace").strip() or "invalid patch"
    apply_result = subprocess.run(
        ["git", "-C", workspace, "apply", "--whitespace=nowarn", "-"],
        input=patch_text.encode("utf-8"),
        capture_output=True,
        timeout=30,
    )
    if apply_result.returncode != 0:
        return (
            apply_result.stderr.decode("utf-8", errors="replace").strip()
            or "patch apply failed"
        )
    return None


def _safe_artifact_path(workspace: str, relative_path: str) -> Optional[str]:
    normalized = posixpath.normpath(relative_path.replace("\\", "/"))
    if (
        not relative_path
        or normalized.startswith("../")
        or normalized == ".."
        or normalized.startswith("/")
    ):
        return None
    candidate = os.path.realpath(os.path.join(workspace, normalized))
    root = os.path.realpath(workspace)
    if not candidate.startswith(root + os.sep):
        return None
    return candidate


def _load_artifact(workspace: str, relative_path: str) -> Tuple[Dict[str, Any], Optional[str]]:
    path = _safe_artifact_path(workspace, relative_path)
    if path is None:
        return {}, "result_artifact_path is unsafe"
    if not os.path.isfile(path):
        return {}, None
    if os.path.getsize(path) > MAX_ARTIFACT_BYTES:
        return {}, f"result artifact exceeds {MAX_ARTIFACT_BYTES} bytes"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, f"invalid result artifact: {exc}"
    if not isinstance(value, dict):
        return {}, "result artifact must be a JSON object"
    return value, None


def _deterministic_metrics(
    commands: List[ExperimentCommandResult],
    artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    tests = [item for item in commands if item.phase == "test"]
    failed_tests = [item.command for item in tests if item.exit_code != 0]
    evaluations = artifacts.get("evaluations", [])
    if not isinstance(evaluations, list):
        evaluations = []
    statuses = [
        item.get("status")
        for item in evaluations
        if isinstance(item, dict) and item.get("status") in ("ok", "ng", "needs_review")
    ]
    ok_count = statuses.count("ok")
    decided_count = ok_count + statuses.count("ng")
    traces = artifacts.get("traces", [])
    if not isinstance(traces, list):
        traces = []
    shadow_results = artifacts.get("shadow_results", [])
    if not isinstance(shadow_results, list):
        shadow_results = []
    warnings = artifacts.get("safety_warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    verdicts = artifacts.get("llm_verdicts", [])
    if not isinstance(verdicts, list):
        verdicts = []
    verdict_distribution: Dict[str, int] = {}
    for verdict in verdicts:
        key = str(verdict)
        verdict_distribution[key] = verdict_distribution.get(key, 0) + 1
    total_duration = sum(item.duration_ms for item in commands)
    runtime_failures = [item for item in commands if item.exit_code != 0]
    return {
        "test_pass_rate": (len(tests) - len(failed_tests)) / len(tests) if tests else 0.0,
        "failed_tests": failed_tests,
        "command_count": len(commands),
        "runtime_error_rate": len(runtime_failures) / len(commands) if commands else 0.0,
        "criteria_pass_rate": ok_count / decided_count if decided_count else None,
        "criteria_distribution": {
            "ok": ok_count,
            "ng": statuses.count("ng"),
            "needs_review": statuses.count("needs_review"),
        },
        "trace_count": len(traces),
        "trace_outputs": [
            item.get("output") for item in traces if isinstance(item, dict)
        ],
        "shadow_result_count": len(shadow_results),
        "duration_ms": total_duration,
        "safety_warnings": [str(item) for item in warnings],
        "llm_verdict_distribution": verdict_distribution,
        "timed_out": any(item.timed_out for item in commands),
        "output_limit_bytes": MAX_OUTPUT_BYTES,
    }


def execute_variant(
    *,
    repo_path: str,
    commit_sha: str,
    workspace_base: str,
    patch_text: str,
    execution_config: Dict[str, Any],
) -> ExperimentVariantExecution:
    workspace = None
    result = ExperimentVariantExecution(status="running")
    try:
        workspace = create_worktree(repo_path, commit_sha, workspace_base)
        result.workspace_path = workspace
        patch_error = _apply_patch(workspace, patch_text)
        if patch_error:
            result.status = "invalid_patch"
            result.error = patch_error
            result.metrics = _deterministic_metrics([], {})
            return result

        config = ValidationConfig(
            install_commands=list(execution_config.get("install_commands") or []),
            test_commands=list(execution_config.get("test_commands") or []),
            smoke_commands=list(execution_config.get("smoke_commands") or []),
            workload_commands=list(execution_config.get("workload_commands") or []),
            timeout_seconds=int(execution_config.get("timeout_seconds", 60)),
            network=bool(execution_config.get("network", False)),
            env_allowlist=dict(execution_config.get("env") or {}),
        )
        env = _build_env(config, workspace)
        phases = (
            [("install", command) for command in config.install_commands]
            + [("test", command) for command in config.test_commands]
            + [("smoke", command) for command in config.smoke_commands]
            + [("workload", command) for command in config.workload_commands]
        )
        for phase, command in phases:
            command_result = _run_command(
                command, workspace, env, config.timeout_seconds, config.network
            )
            result.commands.append(
                ExperimentCommandResult(
                    phase=phase,
                    command=command_result.command,
                    exit_code=command_result.exit_code,
                    duration_ms=command_result.duration_ms,
                    stdout=command_result.stdout,
                    stderr=command_result.stderr,
                    stdout_truncated=command_result.stdout_truncated,
                    stderr_truncated=command_result.stderr_truncated,
                    timed_out=command_result.timed_out,
                )
            )
            if command_result.exit_code != 0 and phase in ("install", "test"):
                break

        artifacts, artifact_error = _load_artifact(
            workspace,
            str(
                execution_config.get(
                    "result_artifact_path", config.result_artifact_path
                )
            ),
        )
        result.artifacts = artifacts
        if artifact_error:
            result.error = artifact_error
        result.metrics = _deterministic_metrics(result.commands, artifacts)
        if result.metrics["timed_out"]:
            result.status = "timed_out"
        elif any(item.exit_code != 0 for item in result.commands) or artifact_error:
            result.status = "failed"
        else:
            result.status = "completed"
        return result
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        result.metrics = _deterministic_metrics(result.commands, result.artifacts)
        return result
    finally:
        if workspace:
            cleanup = cleanup_worktree(repo_path, workspace)
            result.cleanup_state = cleanup.state
            result.cleanup_error = cleanup.error


def comparison_payload(variants: List[Dict[str, Any]]) -> Dict[str, Any]:
    baseline = next((item for item in variants if item.get("is_baseline")), None)
    baseline_outputs = ((baseline or {}).get("metrics") or {}).get("trace_outputs", [])
    rows = []
    for item in variants:
        metrics = item.get("metrics") or {}
        rows.append(
            {
                "variant_key": item.get("variant_key"),
                "label": item.get("label"),
                "status": item.get("status"),
                "test_pass_rate": metrics.get("test_pass_rate"),
                "failed_tests": metrics.get("failed_tests"),
                "runtime_error_rate": metrics.get("runtime_error_rate"),
                "criteria_pass_rate": metrics.get("criteria_pass_rate"),
                "criteria_distribution": metrics.get("criteria_distribution"),
                "duration_ms": metrics.get("duration_ms"),
                "trace_count": metrics.get("trace_count"),
                "trace_outputs": metrics.get("trace_outputs", []),
                "trace_output_changed": metrics.get("trace_outputs", []) != baseline_outputs,
                "shadow_result_count": metrics.get("shadow_result_count"),
                "llm_verdict_distribution": metrics.get(
                    "llm_verdict_distribution", {}
                ),
                "timed_out": metrics.get("timed_out"),
                "safety_warnings": metrics.get("safety_warnings", []),
            }
        )
    return {"baseline_variant_key": "baseline", "variants": rows}
