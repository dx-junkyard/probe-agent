"""Replay run orchestration helpers (Issue #242 Phase B / #244).

probe-agent:
  role: Replay execution wrapper — worktree lifecycle, sandboxed harness
    execution, deterministic input restoration and output comparison
  capability: replay-simulation
  element_type: core
  consumers: [control-server]
  operation_kind: io
  state_effects: [filesystem]
  probe_value: Verify replay runs execute in isolated worktrees with network
    off, always clean up, and classify results into the finite comparison set.

Everything in this module is deterministic (Principle 6): input restoration
and output comparison classify into small explicit finite sets and no
reasoning model is involved anywhere in Phase B.

Isolation (Principle 8): the target repository is only ever read through a
temporary git worktree pinned to the snapshot commit; execution goes through
``validation_runner._run_command`` so the bwrap/network-off/no-sandbox
fail-closed semantics are inherited unchanged; ``PROBE_ENABLED=false`` is
injected so any @probe decorators in the target repository are inert during
replay; the worktree is always cleaned up (cleanup state recorded).

Environment variables:
  * ``PROBE_REPLAY_WORKSPACE_BASE`` — base directory for per-run worktrees
    (default ``/tmp/probe-replays``); each run uses ``<base>/<run_id>``.
  * ``PROBE_REPLAY_TIMEOUT_SECONDS`` — harness execution timeout (default
    60, clamped to 1..300).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .execution_backend import ExecutionBackend
from .experiment_runner import _apply_patch, _load_artifact
from .patch_generator import cleanup_worktree, create_worktree
from .replay_harness import (
    PAYLOAD_FILENAME,
    HARNESS_FILENAME,
    RESULT_FILENAME,
    REPLAY_HARNESS_VERSION,
    write_harness_files,
)
from .validation_runner import ValidationConfig, _build_env, _run_command

# --- constants / finite sets (Principle 6) -----------------------------------

DEFAULT_TIMEOUT_SECONDS = 60
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 300

HARNESS_DIRNAME = ".probe-replay"
TRUNCATION_MARKER = "...<truncated>"

INPUT_SOURCE_STRUCTURED = "structured"
INPUT_SOURCE_REPR = "repr_partial"
INPUT_SOURCES = (INPUT_SOURCE_STRUCTURED, INPUT_SOURCE_REPR)

SKIP_UNREPLAYABLE = "unreplayable_capture"
SKIP_REPR_PARSE_FAILED = "repr_parse_failed"
SKIP_UNDECODABLE = "undecodable_input"
SKIP_TRACE_MISSING = "trace_missing"
SKIP_REASONS = (
    SKIP_UNREPLAYABLE,
    SKIP_REPR_PARSE_FAILED,
    SKIP_UNDECODABLE,
    SKIP_TRACE_MISSING,
)

CASE_MATCH = "match"
CASE_MISMATCH = "mismatch"
CASE_ERROR = "error"
CASE_SKIPPED = "skipped"
CASE_STATUSES = (CASE_MATCH, CASE_MISMATCH, CASE_ERROR, CASE_SKIPPED)

COMPARISON_MODE = "repr"


def replay_timeout_seconds() -> int:
    raw = os.getenv("PROBE_REPLAY_TIMEOUT_SECONDS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(value, MAX_TIMEOUT_SECONDS))


def replay_workspace_base() -> str:
    return os.getenv("PROBE_REPLAY_WORKSPACE_BASE", "/tmp/probe-replays")


# --- deterministic input restoration -----------------------------------------


def restoration_for_trace(row) -> Tuple[Optional[str], Optional[str]]:
    """Deterministic per-trace input-source decision (finite sets only).

    Returns ``(input_source, skip_reason)`` — exactly one is non-None.
    Shared between the runner and the replay-set preview API so Phase D can
    render badges that agree with what a run would actually do.

      * missing trace row -> skip ``trace_missing``
      * structured capture stored and replayability replayable/partial ->
        ``structured``
      * replayability ``unreplayable`` (or a stored classification without a
        usable capture — inconsistent row, fail closed) -> skip
        ``unreplayable_capture``
      * capture columns NULL (pre-Phase-A / component not opted in) ->
        ``repr_partial`` from the recorded repr input; a missing/malformed
        recorded input skips with ``repr_parse_failed``
    """
    if row is None:
        return None, SKIP_TRACE_MISSING
    capture_raw = row["input_capture_json"]
    replayability = row["replayability"]
    if capture_raw and replayability in ("replayable", "partial"):
        return INPUT_SOURCE_STRUCTURED, None
    if replayability is not None:
        # 'unreplayable', or an inconsistent row (classification stored but
        # no usable capture). Fail closed: never guess inputs.
        return None, SKIP_UNREPLAYABLE
    input_payload = _repr_input_payload(row["input_json"])
    if input_payload is None:
        return None, SKIP_REPR_PARSE_FAILED
    return INPUT_SOURCE_REPR, None


def _repr_input_payload(input_json: Optional[str]) -> Optional[Dict[str, Any]]:
    """Recorded repr input as ``{"args": [...], "kwargs": {...}}`` or None.

    Only the SDK's ``_serialize_input`` shape is restorable; anything else
    (missing input, non-dict input) cannot be mechanically restored and the
    caller skips the case (fail closed — no silent degradation).
    """
    if not input_json:
        return None
    try:
        value = json.loads(input_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    args = value.get("args", [])
    kwargs = value.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        return None
    return {"args": args, "kwargs": {str(k): v for k, v in kwargs.items()}}


@dataclass
class CasePlan:
    """Server-side plan for one trace of a replay set (payload order)."""

    trace_id: str
    position: int
    input_source: Optional[str]
    skip_reason: Optional[str]
    recorded_output: Optional[str]
    recorded_error_first_line: Optional[str]
    harness_case: Optional[Dict[str, Any]]  # None when skipped server-side


def _error_first_line(error: Optional[str]) -> Optional[str]:
    if error is None:
        return None
    lines = error.splitlines()
    return lines[0] if lines else ""


def build_case_plans(
    conn, system_id: int, component_id: str, trace_ids: List[str]
) -> Tuple[List[CasePlan], str]:
    """Build per-trace case plans plus the audit ``trace_set_hash``.

    The hash is sha256 over the ordered trace ids and each trace's input
    payload (structured capture when it would be used, otherwise the recorded
    repr input), so a run is verifiably tied to the exact inputs it replayed.
    """
    plans: List[CasePlan] = []
    hasher = hashlib.sha256()
    for position, trace_id in enumerate(trace_ids):
        row = conn.execute(
            """
            SELECT trace_id, input_json, output_text, error,
                   input_capture_json, replayability
            FROM traces
            WHERE system_id = ? AND trace_id = ? AND component_id = ?
            """,
            (system_id, trace_id, component_id),
        ).fetchone()
        input_source, skip_reason = restoration_for_trace(row)
        recorded_output = row["output_text"] if row is not None else None
        recorded_error_first = (
            _error_first_line(row["error"]) if row is not None else None
        )
        harness_case: Optional[Dict[str, Any]] = None
        input_payload_for_hash: Any = None
        if input_source == INPUT_SOURCE_STRUCTURED:
            try:
                capture = json.loads(row["input_capture_json"])
            except json.JSONDecodeError:
                capture = None
            if capture is None:
                # Malformed stored capture: fail closed at the server.
                input_source, skip_reason = None, SKIP_UNREPLAYABLE
            else:
                harness_case = {"input_kind": "structured", "capture": capture}
                input_payload_for_hash = row["input_capture_json"]
        elif input_source == INPUT_SOURCE_REPR:
            payload = _repr_input_payload(row["input_json"])
            harness_case = {
                "input_kind": "repr",
                "args": payload["args"],
                "kwargs": payload["kwargs"],
            }
            input_payload_for_hash = row["input_json"]
        hasher.update(
            json.dumps(
                {"trace_id": trace_id, "input": input_payload_for_hash},
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        )
        plans.append(
            CasePlan(
                trace_id=trace_id,
                position=position,
                input_source=input_source,
                skip_reason=skip_reason,
                recorded_output=recorded_output,
                recorded_error_first_line=recorded_error_first,
                harness_case=harness_case,
            )
        )
    return plans, hasher.hexdigest()


# --- sandboxed harness execution ----------------------------------------------


@dataclass
class ReplayExecution:
    status: str = "failed"  # 'completed' | 'failed' | 'invalid_patch'
    error: Optional[str] = None
    workspace_path: Optional[str] = None
    cleanup_state: str = "not_attempted"
    cleanup_error: Optional[str] = None
    case_results: List[Dict[str, Any]] = field(default_factory=list)


def build_sandbox_config(timeout_seconds: int, env: Dict[str, str]) -> Dict[str, Any]:
    return {
        "timeout_seconds": timeout_seconds,
        "network": False,
        "network_isolation": "requested_off",
        "harness_version": REPLAY_HARNESS_VERSION,
        "env_keys": sorted(env.keys()),
    }


def build_replay_env(workspace: str) -> Dict[str, str]:
    """Minimal allowlisted env for the replay harness.

    ``PROBE_ENABLED=false`` makes any @probe decorators in the target
    repository inert during replay; ``PYTHONHASHSEED=0`` pins hash-order-
    dependent reprs (sets) so replay runs are reproducible against each
    other.
    """
    config = ValidationConfig(env_allowlist={})
    env = _build_env(config, workspace)
    env["PROBE_ENABLED"] = "false"
    env["PYTHONHASHSEED"] = "0"
    return env


def execute_harness(
    *,
    repo_path: str,
    commit_sha: str,
    run_workspace_base: str,
    target: Dict[str, Any],
    harness_cases: List[Dict[str, Any]],
    timeout_seconds: int,
    patch_text: Optional[str] = None,
    backend: Optional[ExecutionBackend] = None,
) -> ReplayExecution:
    """Run the harness against a fresh worktree; always clean the worktree up.

    ``patch_text`` (Issue #245 Phase C) is applied to the worktree AFTER
    ``create_worktree`` and BEFORE the harness is written, via the same
    ``git apply --check`` -> ``git apply`` convention
    ``experiment_runner._apply_patch`` uses. A patch that fails to apply
    never runs the harness: the execution is reported ``status='invalid_patch'``
    with the apply error, so one variant's bad patch can never affect the
    baseline or any other variant (each gets its own independent worktree and
    its own ``execute_harness`` call). Passing no ``patch_text`` (the
    default) reproduces Phase B's baseline-only behavior exactly.
    """
    execution = ReplayExecution()
    workspace: Optional[str] = None
    try:
        workspace = create_worktree(repo_path, commit_sha, run_workspace_base)
        execution.workspace_path = workspace
        if patch_text:
            apply_error = _apply_patch(workspace, patch_text)
            if apply_error:
                execution.status = "invalid_patch"
                execution.error = apply_error
                return execution
        harness_dir = os.path.join(workspace, HARNESS_DIRNAME)
        write_harness_files(harness_dir, target, harness_cases)
        env = build_replay_env(workspace)
        command = (
            f"python3 -I {HARNESS_DIRNAME}/{HARNESS_FILENAME} "
            f"{HARNESS_DIRNAME}/{PAYLOAD_FILENAME} "
            f"{HARNESS_DIRNAME}/{RESULT_FILENAME}"
        )
        result = _run_command(
            command,
            workspace,
            env,
            timeout_seconds,
            network=False,
            backend=backend,
        )
        payload, load_error = _load_artifact(
            workspace, f"{HARNESS_DIRNAME}/{RESULT_FILENAME}"
        )
        if load_error:
            execution.error = f"replay harness result unreadable: {load_error}"
            return execution
        if not payload:
            stderr = (result.stderr or result.stdout or "").strip()
            execution.error = (
                "replay harness produced no result file: "
                + (stderr or f"exit code {result.exit_code}")
            )
            return execution
        if payload.get("target_error"):
            detail = payload.get("target_traceback") or ""
            execution.error = (
                "replay target could not be resolved: "
                + str(payload["target_error"])
                + (("\n" + detail) if detail else "")
            )
            return execution
        cases = payload.get("cases")
        if not isinstance(cases, list) or len(cases) != len(harness_cases):
            execution.error = "replay harness returned a malformed case list"
            return execution
        execution.status = "completed"
        execution.case_results = cases
        return execution
    except Exception as exc:
        execution.error = str(exc)
        return execution
    finally:
        if workspace:
            cleanup = cleanup_worktree(repo_path, workspace)
            execution.cleanup_state = cleanup.state
            execution.cleanup_error = cleanup.error


# --- deterministic comparison ---------------------------------------------------


def classify_case(plan: CasePlan, harness_case: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic finite classification of one replay case.

    Matrix (comparison_mode 'repr'; recorded error compared by FIRST LINE):
      * server-side skip / harness skip -> ``skipped`` (+ finite skip_reason)
      * both succeeded -> repr equality -> ``match`` / ``mismatch``
      * recorded success + replay raised -> ``error``
      * recorded error + replay raised -> first lines equal -> ``match`` else
        ``mismatch``
      * recorded error + replay succeeded -> ``mismatch``

    ``output_truncated`` flags prefix-bounded honesty: when either side ends
    with the SDK truncation marker, repr equality only compares the stored
    4000-char prefix.
    """
    result: Dict[str, Any] = {
        "trace_id": plan.trace_id,
        "position": plan.position,
        "input_source": plan.input_source,
        "skip_reason": plan.skip_reason,
        "replay_output": None,
        "replay_error": None,
        "recorded_output": plan.recorded_output,
        "recorded_error": plan.recorded_error_first_line,
        "duration_ms": None,
        "output_truncated": False,
        "comparison_mode": COMPARISON_MODE,
    }
    if plan.harness_case is None or harness_case is None:
        result["case_status"] = CASE_SKIPPED
        return result

    status = harness_case.get("status")
    if status == "skipped":
        skip_reason = harness_case.get("skip_reason")
        if skip_reason not in SKIP_REASONS:
            # Outside the finite set — fail closed as an error, never guess.
            result["case_status"] = CASE_ERROR
            result["replay_error"] = f"unknown harness skip reason: {skip_reason!r}"
            return result
        result["case_status"] = CASE_SKIPPED
        result["skip_reason"] = skip_reason
        return result

    result["duration_ms"] = harness_case.get("duration_ms")
    if status == "ok":
        replay_output = harness_case.get("output")
        result["replay_output"] = replay_output
        result["output_truncated"] = _is_truncated(replay_output) or _is_truncated(
            plan.recorded_output
        )
        if plan.recorded_error_first_line is None:
            result["case_status"] = (
                CASE_MATCH if replay_output == plan.recorded_output else CASE_MISMATCH
            )
        else:
            # Recorded error, replay succeeded.
            result["case_status"] = CASE_MISMATCH
        return result
    if status == "error":
        replay_error = harness_case.get("error") or ""
        result["replay_error"] = replay_error
        replay_first = _error_first_line(replay_error) or ""
        result["output_truncated"] = _is_truncated(plan.recorded_output)
        if plan.recorded_error_first_line is None:
            result["case_status"] = CASE_ERROR
        else:
            result["case_status"] = (
                CASE_MATCH
                if replay_first == plan.recorded_error_first_line
                else CASE_MISMATCH
            )
        return result

    result["case_status"] = CASE_ERROR
    result["replay_error"] = f"malformed harness case status: {status!r}"
    return result


def _is_truncated(text: Optional[str]) -> bool:
    return bool(text and text.endswith(TRUNCATION_MARKER))


def summarize_cases(case_rows: List[Dict[str, Any]]) -> Dict[str, int]:
    summary = {status: 0 for status in CASE_STATUSES}
    for case in case_rows:
        status = case.get("case_status")
        if status in summary:
            summary[status] += 1
    summary["total"] = len(case_rows)
    return summary
