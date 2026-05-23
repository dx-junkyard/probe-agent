import functools
import logging
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, Optional

from .client import ControlClient
from .config import ProbeConfig
from .policy import PolicyCache

logger = logging.getLogger("probe_agent.decorator")

_client = ControlClient()
_policy_cache = PolicyCache(client=_client)
_candidates: Dict[str, Callable[..., Any]] = {}
_candidates_lock = threading.Lock()


def set_candidate(component_id: str, fn: Callable[..., Any]) -> None:
    """Register a candidate (alternative) implementation for shadow mode."""
    with _candidates_lock:
        _candidates[component_id] = fn


def _get_candidate(component_id: str) -> Optional[Callable[..., Any]]:
    with _candidates_lock:
        return _candidates.get(component_id)


def _safe_repr(value: Any, limit: int = 4000) -> str:
    try:
        text = repr(value)
    except Exception as e:  # noqa: BLE001
        text = f"<unrepr-able: {type(value).__name__}: {e}>"
    if len(text) > limit:
        text = text[:limit] + "...<truncated>"
    return text


def _serialize_input(args: tuple, kwargs: dict) -> Dict[str, Any]:
    return {
        "args": [_safe_repr(a) for a in args],
        "kwargs": {k: _safe_repr(v) for k, v in kwargs.items()},
    }


def probe(component_id: str, candidate: Optional[Callable[..., Any]] = None):
    """Wrap a function so its input/output/error/duration are reported.

    Modes (driven by Control Server policy):
      * ``off``    – decorator is a no-op; original function runs as-is.
      * ``trace``  – original function runs; trace is sent best-effort.
      * ``shadow`` – original function runs and is returned; the registered
                     candidate runs in a background thread and its output
                     is sent as a shadow result for comparison.
    """
    if candidate is not None:
        set_candidate(component_id, candidate)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not ProbeConfig.enabled():
                return fn(*args, **kwargs)

            policy = _policy_cache.get(component_id)
            mode = (policy or {}).get("mode", ProbeConfig.default_mode())

            if mode == "off":
                return fn(*args, **kwargs)

            trace_id = str(uuid.uuid4())
            start = time.perf_counter()
            error_repr: Optional[str] = None
            output: Any = None
            raised: Optional[BaseException] = None

            try:
                output = fn(*args, **kwargs)
            except BaseException as e:  # noqa: BLE001
                raised = e
                error_repr = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

            duration_ms = (time.perf_counter() - start) * 1000.0

            trace = {
                "trace_id": trace_id,
                "component_id": component_id,
                "mode": mode,
                "input": _serialize_input(args, kwargs),
                "output": None if raised else _safe_repr(output),
                "error": error_repr,
                "duration_ms": duration_ms,
                "timestamp": time.time(),
            }
            try:
                _client.send_trace(trace)
            except Exception:  # noqa: BLE001
                logger.debug("send_trace failed", exc_info=True)

            if mode == "shadow" and raised is None:
                cand = _get_candidate(component_id)
                if cand is not None:
                    _spawn_shadow(component_id, trace_id, cand, args, kwargs, output)

            if raised is not None:
                raise raised
            return output

        return wrapper

    return decorator


def _spawn_shadow(
    component_id: str,
    trace_id: str,
    candidate: Callable[..., Any],
    args: tuple,
    kwargs: dict,
    current_output: Any,
) -> None:
    def run() -> None:
        c_start = time.perf_counter()
        c_error: Optional[str] = None
        c_output: Any = None
        try:
            c_output = candidate(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001
            c_error = f"{type(e).__name__}: {e}"
        c_duration = (time.perf_counter() - c_start) * 1000.0

        payload = {
            "trace_id": trace_id,
            "component_id": component_id,
            "current_output": _safe_repr(current_output),
            "candidate_output": None if c_error else _safe_repr(c_output),
            "candidate_error": c_error,
            "candidate_duration_ms": c_duration,
            "timestamp": time.time(),
        }
        try:
            _client.send_shadow_result(payload)
        except Exception:  # noqa: BLE001
            logger.debug("send_shadow_result failed", exc_info=True)

    threading.Thread(target=run, daemon=True, name=f"probe-shadow-{component_id}").start()
