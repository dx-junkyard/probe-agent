"""Shared replay/candidate execution harness (Issue #242 Phase B / #244).

probe-agent:
  role: Standalone sandbox harness script and its server-side helpers
  capability: replay-simulation
  element_type: core
  consumers: [replay-runner, generation]
  operation_kind: io
  state_effects: [filesystem]
  probe_value: Verify replay cases execute deterministically in isolation and
    the generation candidate path preserves its legacy behavior.

This module owns the standalone harness SCRIPT (a Python source string,
version :data:`REPLAY_HARNESS_VERSION`) plus small server-side helpers. The
harness is written into an isolated workspace and executed there — it never
imports probe-agent server code. Contract:

  * ``argv[1]`` is a JSON payload file, ``argv[2]`` is the JSON result file.
    Results are ALWAYS communicated through the result file; stdout/stderr
    are diagnostics only (the runner truncates them).
  * Payload: ``{"target": {...}, "cases": [...]}``.
  * Target kinds (finite):
      - ``{"kind": "symbol", "path": "<repo-relative .py>",
         "qualified_name": "func | Class.method"}`` — the module is loaded
        via ``importlib.util.spec_from_file_location`` with the workspace
        root (the harness working directory) inserted at ``sys.path[0]`` so
        intra-repo absolute imports resolve; the qualified name parts are
        then resolved by getattr chaining. Symbol targets get the real
        Python environment because isolation comes from the sandboxed
        subprocess, not a builtins jail.
      - ``{"kind": "inline_code", "code": "..."}`` — LLM-authored code keeps
        the tighter jail: it is exec'd with the same restricted
        ``SAFE_BUILTINS`` namespace the generation endpoint has always used
        and must define a callable ``candidate``.
  * Case input kinds (finite):
      - ``{"input_kind": "structured", "capture": {...}}`` — the Phase A
        (#243) canonical ``"__probe__"`` encoding, decoded by a standalone
        decoder embedded in the script. Any ``unsupported`` marker (or an
        unknown marker / malformed encoding) means the case carries no
        restorable data: the function is NOT called and the case is skipped
        with reason ``undecodable_input``.
      - ``{"input_kind": "repr", "args": [...], "kwargs": {...}}`` — string
        values are ``ast.literal_eval``'d inside the harness; any parse
        failure skips the case with reason ``repr_parse_failed``. Non-string
        values (hand-posted structured JSON) pass through as-is.
      - ``{"input_kind": "values", "args": [...], "kwargs": {...}}`` —
        pre-decoded JSON values passed verbatim. Used ONLY by the generation
        migration so its legacy server-side repr parsing (literal_eval with
        raw-string fallback) keeps producing byte-identical behavior; the
        replay engine never uses this kind.
  * Per case the harness records: ``status`` (``ok`` / ``error`` /
    ``skipped``), ``output`` normalized exactly like the SDK's
    ``_safe_repr`` (repr(), ``<unrepr-able: ...>`` on failure, truncated at
    4000 chars with ``...<truncated>``), ``error`` as the first-line format
    ``"Type: msg"``, ``traceback`` (``traceback.format_exc(limit=8)``, kept
    for the generation path's legacy error strings), ``skip_reason``, and
    ``duration_ms``. A per-case try/except guarantees one case's exception
    never kills the batch; cases run in payload order in one process and no
    global state is mutated between cases.
  * Target resolution failure (import error, syntax error, missing symbol,
    non-callable) is a RUN-level failure: the result file carries
    ``target_error`` / ``target_traceback`` and no cases are executed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

REPLAY_HARNESS_VERSION = "1"

HARNESS_FILENAME = "harness.py"
PAYLOAD_FILENAME = "payload.json"
RESULT_FILENAME = "result.json"

# Timeout for the legacy candidate-generation path (unchanged: 5 seconds).
CANDIDATE_TIMEOUT_SECONDS = 5

HARNESS_SCRIPT = r'''"""probe-agent replay harness (standalone; version 1).

Reads a JSON payload from argv[1] and writes a JSON result to argv[2].
stdout/stderr are diagnostics only. See app/replay_harness.py for the
payload/result contract.
"""
import ast
import base64
import importlib.util
import json
import os
import sys
import time
import traceback

HARNESS_VERSION = "1"
MARKER = "__probe__"

# Same restricted builtins namespace the generation endpoint has always used
# for LLM-authored inline code. Symbol targets are NOT jailed (isolation
# comes from the sandboxed subprocess).
SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "len": len,
    "list": list, "max": max, "min": min, "range": range, "repr": repr,
    "round": round, "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "zip": zip,
}


class _Undecodable(object):
    def __repr__(self):
        return "<probe:undecodable>"


_UNDECODABLE = _Undecodable()


def _decode_value(encoded, unsupported):
    """Invert the Phase A (#243) canonical "__probe__" encoding.

    ``unsupported`` collects marker kinds that carry no restorable data
    (``unsupported`` placeholders and unknown future markers).
    """
    if isinstance(encoded, list):
        return [_decode_value(v, unsupported) for v in encoded]
    if isinstance(encoded, dict):
        if MARKER not in encoded:
            return dict((k, _decode_value(v, unsupported)) for k, v in encoded.items())
        kind = encoded[MARKER]
        if kind == "tuple":
            return tuple(_decode_value(v, unsupported) for v in encoded.get("items", []))
        if kind == "set":
            return set(_decode_value(v, unsupported) for v in encoded.get("items", []))
        if kind == "frozenset":
            return frozenset(_decode_value(v, unsupported) for v in encoded.get("items", []))
        if kind == "bytes":
            return base64.b64decode(encoded.get("b64", ""))
        if kind == "float":
            return {"nan": float("nan"), "inf": float("inf"), "-inf": float("-inf")}[
                encoded["value"]
            ]
        if kind == "dict":
            return dict(
                (_decode_value(k, unsupported), _decode_value(v, unsupported))
                for k, v in encoded.get("items", [])
            )
        # "unsupported" markers (including depth-limit placeholders) and any
        # unknown marker kind carry no restorable data; fail closed.
        unsupported.append(str(kind))
        return _UNDECODABLE
    return encoded


def _safe_repr(value, limit=4000):
    # Mirrors the SDK's probe_agent.decorator._safe_repr exactly.
    try:
        text = repr(value)
    except Exception as e:
        text = "<unrepr-able: %s: %s>" % (type(value).__name__, e)
    if len(text) > limit:
        text = text[:limit] + "...<truncated>"
    return text


def _error_first_line(exc):
    try:
        return "%s: %s" % (type(exc).__name__, exc)
    except Exception:
        return type(exc).__name__


def _resolve_target(target, workspace_root):
    kind = target.get("kind")
    if kind == "inline_code":
        namespace = {"__builtins__": dict(SAFE_BUILTINS)}
        exec(target.get("code") or "", namespace, namespace)
        candidate = namespace.get("candidate")
        if not callable(candidate):
            raise RuntimeError("generated code must define callable candidate")
        return candidate
    if kind == "symbol":
        rel_path = target["path"]
        module_path = os.path.join(workspace_root, rel_path)
        # Workspace root first so intra-repo absolute imports resolve.
        if workspace_root not in sys.path:
            sys.path.insert(0, workspace_root)
        module_name = "_probe_replay_target"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError("cannot load module from %s" % rel_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        obj = module
        for part in target["qualified_name"].split("."):
            obj = getattr(obj, part)
        if not callable(obj):
            raise RuntimeError(
                "resolved symbol %r is not callable" % target["qualified_name"]
            )
        return obj
    raise ValueError("unknown target kind %r" % kind)


def _skip(reason):
    return {
        "status": "skipped",
        "skip_reason": reason,
        "output": None,
        "error": None,
        "traceback": None,
        "duration_ms": 0.0,
    }


def _restore_inputs(case):
    """Return (args, kwargs) or a skip-result dict (fail closed, no call)."""
    input_kind = case.get("input_kind")
    if input_kind == "structured":
        unsupported = []
        try:
            decoded = _decode_value(case.get("capture"), unsupported)
        except Exception:
            return _skip("undecodable_input")
        if unsupported or not isinstance(decoded, dict):
            return _skip("undecodable_input")
        args = decoded.get("args")
        kwargs = decoded.get("kwargs")
        if args is None:
            args = []
        if kwargs is None:
            kwargs = {}
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            return _skip("undecodable_input")
        return args, kwargs
    if input_kind == "repr":
        try:
            args = [
                ast.literal_eval(v) if isinstance(v, str) else v
                for v in (case.get("args") or [])
            ]
            kwargs = dict(
                (k, ast.literal_eval(v) if isinstance(v, str) else v)
                for k, v in (case.get("kwargs") or {}).items()
            )
        except Exception:
            return _skip("repr_parse_failed")
        return args, kwargs
    if input_kind == "values":
        return list(case.get("args") or []), dict(case.get("kwargs") or {})
    raise ValueError("unknown input_kind %r" % input_kind)


def _run_case(func, case):
    restored = _restore_inputs(case)
    if isinstance(restored, dict):
        return restored
    args, kwargs = restored
    start = time.perf_counter()
    try:
        output = func(*args, **kwargs)
    except BaseException as exc:
        duration_ms = (time.perf_counter() - start) * 1000.0
        return {
            "status": "error",
            "skip_reason": None,
            "output": None,
            "error": _error_first_line(exc),
            "traceback": traceback.format_exc(limit=8),
            "duration_ms": duration_ms,
        }
    duration_ms = (time.perf_counter() - start) * 1000.0
    return {
        "status": "ok",
        "skip_reason": None,
        "output": _safe_repr(output),
        "error": None,
        "traceback": None,
        "duration_ms": duration_ms,
    }


def main():
    payload_path = sys.argv[1]
    result_path = sys.argv[2]
    with open(payload_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    result = {
        "harness_version": HARNESS_VERSION,
        "target_error": None,
        "target_traceback": None,
        "cases": [],
    }
    workspace_root = os.getcwd()
    try:
        func = _resolve_target(payload.get("target") or {}, workspace_root)
    except BaseException as exc:
        result["target_error"] = _error_first_line(exc)
        result["target_traceback"] = traceback.format_exc(limit=8)
        _write_result(result_path, result)
        return

    for case in payload.get("cases") or []:
        try:
            result["cases"].append(_run_case(func, case))
        except BaseException as exc:
            # Belt and braces: one case must never kill the batch.
            result["cases"].append(
                {
                    "status": "error",
                    "skip_reason": None,
                    "output": None,
                    "error": _error_first_line(exc),
                    "traceback": traceback.format_exc(limit=8),
                    "duration_ms": 0.0,
                }
            )
    _write_result(result_path, result)


def _write_result(result_path, result):
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
'''


def write_harness_files(
    directory: str,
    target: Dict[str, Any],
    cases: List[Dict[str, Any]],
) -> Tuple[str, str, str]:
    """Write the harness script and payload into ``directory``.

    Returns ``(harness_path, payload_path, result_path)`` (absolute paths).
    """
    os.makedirs(directory, exist_ok=True)
    harness_path = os.path.join(directory, HARNESS_FILENAME)
    payload_path = os.path.join(directory, PAYLOAD_FILENAME)
    result_path = os.path.join(directory, RESULT_FILENAME)
    with open(harness_path, "w", encoding="utf-8") as f:
        f.write(HARNESS_SCRIPT)
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump({"target": target, "cases": cases}, f, ensure_ascii=False)
    return harness_path, payload_path, result_path


def run_inline_candidate(
    code: str,
    args: List[Any],
    kwargs: Dict[str, Any],
    timeout_seconds: int = CANDIDATE_TIMEOUT_SECONDS,
) -> Tuple[Optional[str], Optional[str]]:
    """Execute LLM-authored candidate code through the shared harness.

    Generation-compat wrapper: preserves the legacy `_run_candidate`
    observable behavior exactly — a direct ``python -I -S`` subprocess (NOT
    the bwrap sandbox), the SAFE_BUILTINS jail, the 5s default timeout, and
    the legacy error strings ("candidate execution timed out", raw
    stderr/stdout on runner failure, traceback text on candidate errors).

    Returns ``(candidate_output_repr, execution_error)``.
    """
    with tempfile.TemporaryDirectory(prefix="probe-generation-") as workdir:
        harness_path, payload_path, result_path = write_harness_files(
            workdir,
            {"kind": "inline_code", "code": code},
            [{"input_kind": "values", "args": args, "kwargs": kwargs}],
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", harness_path, payload_path, result_path],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return None, "candidate execution timed out"
        if proc.returncode != 0:
            return None, (proc.stderr or proc.stdout or "candidate runner failed")[:4000]
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError) as exc:
            return None, f"candidate runner returned invalid JSON: {exc}"

    if result.get("target_error"):
        return None, result.get("target_traceback") or result.get("target_error")
    cases = result.get("cases") or []
    if not cases or not isinstance(cases[0], dict):
        return None, "candidate runner returned no case result"
    case = cases[0]
    if case.get("status") == "ok":
        return case.get("output"), None
    return None, case.get("traceback") or case.get("error") or "candidate execution failed"
