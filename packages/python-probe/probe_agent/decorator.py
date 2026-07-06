import atexit
import contextvars
import copy
import functools
import logging
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from . import context as _lineage
from . import projection as _projection
from .client import ControlClient
from .config import ProbeConfig
from .policy import PolicyCache

logger = logging.getLogger("probe_agent.decorator")

_client = ControlClient()
_policy_cache = PolicyCache(client=_client)
_candidates: Dict[str, Callable[..., Any]] = {}
_candidates_lock = threading.Lock()

_projections: Dict[str, "_projection.ProjectionSpec"] = {}
_projections_lock = threading.Lock()


def set_projection(component_id: str, spec: Any) -> None:
    """Register a projection spec for a component.

    Validates the spec immediately (fail-closed): an invalid spec raises
    ``ProjectionError`` here, at registration, rather than at trace time.
    """
    compiled = _projection.compile_spec(spec)
    with _projections_lock:
        _projections[component_id] = compiled


def _get_projection(component_id: str) -> "Optional[_projection.ProjectionSpec]":
    with _projections_lock:
        return _projections.get(component_id)

_inflight: Set[threading.Thread] = set()
_inflight_lock = threading.Lock()
_atexit_registered = False
_atexit_lock = threading.Lock()


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


def _snapshot(value: Any) -> Any:
    """Deep-copy a value so the shadow candidate sees the same input the
    current function saw, even if the caller mutates it afterwards.

    Falls back to the original reference if deepcopy fails (e.g. file
    handles, sockets). The host application must never break because of
    shadow bookkeeping.
    """
    try:
        return copy.deepcopy(value)
    except Exception as e:  # noqa: BLE001
        logger.debug("deepcopy fallback (%s); using reference", type(value).__name__)
        return value


def flush(timeout: float = 10.0) -> None:
    """Wait for in-flight shadow threads to finish (best-effort).

    Useful for short-lived scripts; an ``atexit`` hook calls this
    automatically with ``PROBE_SHUTDOWN_TIMEOUT`` (default 10s).
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        with _inflight_lock:
            pending = list(_inflight)
        if not pending:
            return
        for t in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            t.join(timeout=remaining)


def _ensure_atexit() -> None:
    global _atexit_registered
    with _atexit_lock:
        if _atexit_registered:
            return
        atexit.register(lambda: flush(ProbeConfig.shutdown_timeout()))
        _atexit_registered = True


def probe(
    component_id: str,
    candidate: Optional[Callable[..., Any]] = None,
    entities: Optional[List[Any]] = None,
    projection: Optional[Any] = None,
):
    """Wrap a function so its input/output/error/duration are reported.

    Modes (driven by Control Server policy):
      * ``off``    – decorator is a no-op; original function runs as-is.
      * ``trace``  – original function runs; trace is sent best-effort.
      * ``shadow`` – original function runs and is returned; the registered
                     candidate runs in a background thread on a snapshot
                     of the inputs and its output is sent as a shadow
                     result for comparison.

    ``entities`` are explicit business-entity references (``{"type","id",
    "role"}`` or ``(type, id[, role])``) attached to every trace this probe
    emits, in addition to any entities on the active ``probe_context``. Values
    are supplied by the caller; no extraction is performed here (Phase 2).
    """
    if candidate is not None:
        set_candidate(component_id, candidate)
    if projection is not None:
        # Fail-closed at decoration time on an invalid spec.
        set_projection(component_id, projection)
    static_entities = _lineage._normalize_entities(entities)

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
            # Lineage snapshot (span/parent/correlation/flow/entities). Cheap
            # contextvar reads; only reached once tracing is active.
            lineage = _lineage.current_lineage(extra_entities=static_entities)
            span_token = _lineage.enter_span(lineage["span_id"])

            start = time.perf_counter()
            error_repr: Optional[str] = None
            output: Any = None
            raised: Optional[BaseException] = None

            # Snapshot inputs BEFORE running fn so that:
            #   1. trace input == candidate input == current input
            #   2. if fn mutates its arguments, candidate still sees the
            #      pristine values.
            run_shadow = (mode == "shadow") and (_get_candidate(component_id) is not None)
            args_snap = tuple(_snapshot(a) for a in args) if run_shadow else args
            kwargs_snap = {k: _snapshot(v) for k, v in kwargs.items()} if run_shadow else kwargs

            # Capture the context (with this span active) for the shadow
            # thread so the candidate's nested probes stay on the same
            # lineage. contextvars are not inherited by threads otherwise.
            shadow_ctx = contextvars.copy_context() if run_shadow else None

            try:
                try:
                    output = fn(*args, **kwargs)
                except BaseException as e:  # noqa: BLE001
                    raised = e
                    error_repr = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            finally:
                _lineage.exit_span(span_token)

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
            trace.update(lineage)

            spec = _get_projection(component_id)
            if spec is not None:
                # Extraction is non-fatal: a bad projection must not break the
                # host function. Input root is a stable {args, kwargs} envelope;
                # output root is the return value (absent when fn raised).
                try:
                    input_root = {"args": list(args), "kwargs": dict(kwargs)}
                    output_root = _projection._MISSING if raised else output
                    payloads, proj_entities = _projection.extract(
                        spec, input_root=input_root, output_root=output_root
                    )
                    if payloads:
                        trace["projections"] = payloads
                    if proj_entities:
                        trace["entities"] = list(trace.get("entities", [])) + proj_entities
                except Exception:  # noqa: BLE001
                    logger.debug("projection extraction failed", exc_info=True)

            try:
                _client.send_trace(trace)
            except Exception:  # noqa: BLE001
                logger.debug("send_trace failed", exc_info=True)

            if run_shadow and raised is None:
                cand = _get_candidate(component_id)
                if cand is not None:
                    current_output_repr = _safe_repr(output)
                    _spawn_shadow(
                        component_id, trace_id, cand, args_snap, kwargs_snap,
                        current_output_repr, shadow_ctx,
                    )

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
    current_output_repr: str,
    shadow_ctx: Optional[contextvars.Context] = None,
) -> None:
    _ensure_atexit()

    def run() -> None:
        try:
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
                "current_output": current_output_repr,
                "candidate_output": None if c_error else _safe_repr(c_output),
                "candidate_error": c_error,
                "candidate_duration_ms": c_duration,
                "timestamp": time.time(),
            }
            try:
                _client.send_shadow_result(payload)
            except Exception:  # noqa: BLE001
                logger.debug("send_shadow_result failed", exc_info=True)
        finally:
            with _inflight_lock:
                _inflight.discard(threading.current_thread())

    # Run inside a copy of the spawning context so the candidate's nested
    # probes inherit correlation_id / flow_id / entities and become children
    # of this probe's span (threads do not inherit contextvars).
    target = run if shadow_ctx is None else (lambda: shadow_ctx.run(run))
    t = threading.Thread(target=target, daemon=True, name=f"probe-shadow-{component_id}")
    with _inflight_lock:
        _inflight.add(t)
    t.start()
