import atexit
import contextvars
import copy
import functools
import hashlib
import inspect
import logging
import threading
import time
import traceback
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import context as _lineage
from . import projection as _projection
from . import redaction as _redaction
from . import replay_capture as _replay
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


def _sampled_in(trace_id: str, sample_rate: Optional[float]) -> bool:
    """Deterministic, trace_id-hash-based sampling decision (Issue #152).

    ``None`` keeps everything. The same trace_id always yields the same
    decision, so a trace's input/output/shadow projections and lineage are all
    kept or all dropped together. The trace body itself is never sampled out —
    only lineage/projection enrichment is.
    """
    if sample_rate is None or sample_rate >= 1.0:
        return True
    if sample_rate <= 0.0:
        return False
    digest = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()[:8]
    fraction = int(digest, 16) / 0xFFFFFFFF
    return fraction < sample_rate

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


def _sensitive_arg_policy(
    fn: Callable[..., Any],
) -> Tuple[Set[int], Optional[int]]:
    """Compile denied positional indexes once, at decoration time.

    The second result is the start index of a denied ``*args`` parameter.
    """
    indexes: Set[int] = set()
    sensitive_vararg_start: Optional[int] = None
    try:
        cursor = 0
        for parameter in inspect.signature(fn).parameters.values():
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                if _redaction.is_sensitive_key(parameter.name):
                    indexes.add(cursor)
                cursor += 1
            elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                if _redaction.is_sensitive_key(parameter.name):
                    sensitive_vararg_start = cursor
                break
    except (TypeError, ValueError):
        # Some C-extension callables do not expose a signature. Dict/kwargs
        # redaction still applies; telemetry must never break the host call.
        pass
    return indexes, sensitive_vararg_start


def _payload_repr(value: Any) -> str:
    try:
        return _safe_repr(_redaction.redact_sensitive(value))
    except Exception:  # noqa: BLE001 — telemetry must never break the host call
        logger.debug("payload redaction failed", exc_info=True)
        return "<payload-redaction-failed>"


def _serialize_input(
    args: tuple, kwargs: dict, sensitive_arg_indexes: Optional[Set[int]] = None
) -> Dict[str, Any]:
    sensitive_arg_indexes = sensitive_arg_indexes or set()
    return {
        "args": [
            _redaction.REDACTION_MARKER if index in sensitive_arg_indexes else _payload_repr(arg)
            for index, arg in enumerate(args)
        ],
        "kwargs": {
            key: (
                _redaction.REDACTION_MARKER
                if _redaction.is_sensitive_key(key)
                else _payload_repr(value)
            )
            for key, value in kwargs.items()
        },
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
    """Wait for shadow work and accepted telemetry POSTs (best-effort).

    Useful for short-lived scripts; an ``atexit`` hook calls this
    automatically with ``PROBE_SHUTDOWN_TIMEOUT`` (default 10s).
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        with _inflight_lock:
            pending = list(_inflight)
        if not pending:
            break
        for t in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            t.join(timeout=remaining)

    client_flush = getattr(_client, "flush", None)
    if callable(client_flush):
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                client_flush(timeout=remaining)
            except Exception:  # noqa: BLE001 — telemetry is non-fatal
                logger.debug("transport flush failed", exc_info=True)


def transport_stats() -> Dict[str, Any]:
    """Return the active client's transport snapshot (FakeClient-safe)."""
    snapshot = getattr(_client, "transport_stats", None)
    if callable(snapshot):
        try:
            return dict(snapshot())
        except Exception:  # noqa: BLE001
            logger.debug("transport stats failed", exc_info=True)
    return {
        "dropped_count": 0,
        "failure_count": 0,
        "state": "closed",
        "consecutive_failures": 0,
        "queue_size": 0,
    }


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
    sample_rate: Optional[float] = None,
    replay_capture: Optional[Any] = None,
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

    ``projection`` is a declarative extraction spec (Issue #146), validated
    fail-closed at decoration time. The input phase is extracted before the
    function runs (reflecting the arguments as received); the output phase
    after it returns. ``sample_rate`` (Issue #152) deterministically thins
    lineage + projections by trace_id hash; the trace body is always sent.

    ``replay_capture`` (Issue #242 Phase A / #243) opts this component into
    structured, JSON round-trip-able input capture. Pass ``True`` for the
    defaults or a mapping like ``{"redact": ["$.kwargs.password"]}``
    (projection path grammar; fail-closed validation at decoration time).
    ``None`` / ``False`` (the default) disables capture entirely: the trace
    payload carries none of the new keys and no capture code runs. Capture is
    best-effort and never affects the wrapped function's return value,
    exceptions, or trace sending.

    Raw telemetry is controlled separately by ``PROBE_PAYLOAD_MODE``. The
    default ``redacted`` mode exposes recursively key-redacted repr values;
    ``metadata`` omits input/output and exception traceback, and ``full``
    additionally exposes the exception message/traceback. The finite sensitive
    key denylist is mandatory in both payload-bearing modes and replay capture.
    """
    if candidate is not None:
        set_candidate(component_id, candidate)
    if projection is not None:
        # Fail-closed at decoration time on an invalid spec.
        set_projection(component_id, projection)
    # Fail-closed at decoration time on an invalid replay_capture spec.
    replay_spec = (
        _replay.compile_spec(replay_capture)
        if replay_capture is not None and replay_capture is not False
        else None
    )
    static_entities = _lineage._normalize_entities(entities)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        sensitive_fixed_indexes, sensitive_vararg_start = _sensitive_arg_policy(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not ProbeConfig.enabled():
                return fn(*args, **kwargs)

            # When the POST breaker is open, skip policy lookup, trace-id
            # generation, sampling and serialization. Older/Fake clients do
            # not implement the gate and retain their synchronous behaviour.
            transport_open = getattr(_client, "transport_is_open", None)
            if callable(transport_open):
                try:
                    if transport_open():
                        return fn(*args, **kwargs)
                except Exception:  # noqa: BLE001 — FakeClient compatibility
                    logger.debug("transport gate failed", exc_info=True)

            policy = _policy_cache.get(component_id)
            mode = (policy or {}).get("mode", ProbeConfig.default_mode())

            if mode == "off":
                return fn(*args, **kwargs)

            payload_mode = ProbeConfig.payload_mode()
            if payload_mode in ("redacted", "full") or replay_spec is not None:
                sensitive_arg_indexes = {
                    index for index in sensitive_fixed_indexes if index < len(args)
                }
                if sensitive_vararg_start is not None:
                    sensitive_arg_indexes.update(
                        range(sensitive_vararg_start, len(args))
                    )
            else:
                sensitive_arg_indexes = set()

            trace_id = str(uuid.uuid4())
            # Deterministic sampling (Issue #152): the trace body is always
            # sent; only lineage + projections are sampled by trace_id hash.
            keep_enrichment = _sampled_in(trace_id, sample_rate)
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

            # Structured input capture (Issue #242 Phase A / #243). Opt-in
            # only: when not opted in, this is a single None check and the
            # trace payload carries none of the new keys. Runs BEFORE fn so
            # the capture reflects the arguments as received (pre-mutation,
            # same rationale as the shadow input snapshot). Best-effort: any
            # failure degrades to unreplayable/capture_failed and never
            # affects fn's return value, exceptions, or trace sending.
            capture_payload = None
            capture_replayability: Optional[str] = None
            capture_reasons: Optional[List[str]] = None
            if replay_spec is not None and mode in ("trace", "shadow"):
                try:
                    capture_payload, capture_replayability, capture_reasons = (
                        _replay.capture_input(
                            args,
                            kwargs,
                            replay_spec,
                            sensitive_arg_indexes=sensitive_arg_indexes,
                        )
                    )
                except Exception:  # noqa: BLE001 — capture must never break fn
                    logger.debug("replay capture failed", exc_info=True)
                    capture_payload = None
                    capture_replayability = _replay.UNREPLAYABLE
                    capture_reasons = [_replay.REASON_CAPTURE_FAILED]

            spec = _get_projection(component_id) if keep_enrichment else None
            proj_payloads: List[Dict[str, Any]] = []
            proj_entities: List[Dict[str, str]] = []
            if spec is not None:
                # Input projection is extracted BEFORE fn runs so it reflects
                # the arguments as received — the same values a shadow
                # candidate sees via the pre-call snapshot — even when fn
                # mutates its arguments. Non-fatal like all extraction.
                try:
                    payloads, ents = _projection.extract(
                        spec, input_root={"args": list(args), "kwargs": dict(kwargs)}
                    )
                    proj_payloads.extend(payloads)
                    proj_entities.extend(ents)
                except Exception:  # noqa: BLE001
                    logger.debug("input projection extraction failed", exc_info=True)

            try:
                try:
                    output = fn(*args, **kwargs)
                except BaseException as e:  # noqa: BLE001
                    raised = e
                    if payload_mode == "full":
                        error_repr = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                    else:
                        # Exception messages and tracebacks are free-form and
                        # cannot be structurally key-redacted.
                        error_repr = type(e).__name__
            finally:
                _lineage.exit_span(span_token)

            duration_ms = (time.perf_counter() - start) * 1000.0

            expose_payload = payload_mode in ("redacted", "full")
            trace = {
                "trace_id": trace_id,
                "component_id": component_id,
                "mode": mode,
                "input": (
                    _serialize_input(args, kwargs, sensitive_arg_indexes)
                    if expose_payload else None
                ),
                "output": (
                    None if raised is not None or not expose_payload else _payload_repr(output)
                ),
                "error": error_repr,
                "duration_ms": duration_ms,
                "timestamp": time.time(),
            }
            if keep_enrichment:
                trace.update(lineage)
            if capture_replayability is not None:
                trace["input_capture"] = capture_payload
                trace["replayability"] = capture_replayability
                trace["replay_reasons"] = capture_reasons

            if spec is not None and raised is None:
                # Output projection (the input phase was extracted pre-call).
                try:
                    payloads, ents = _projection.extract(spec, output_root=output)
                    proj_payloads.extend(payloads)
                    proj_entities.extend(ents)
                except Exception:  # noqa: BLE001
                    logger.debug("output projection extraction failed", exc_info=True)
            if proj_payloads:
                trace["projections"] = proj_payloads
            if proj_entities:
                trace["entities"] = list(trace.get("entities", [])) + proj_entities

            try:
                _ensure_atexit()
                _client.send_trace(trace)
            except Exception:  # noqa: BLE001
                logger.debug("send_trace failed", exc_info=True)

            if run_shadow and raised is None:
                cand = _get_candidate(component_id)
                if cand is not None:
                    current_output_repr = _payload_repr(output) if expose_payload else None
                    # shadow_current is projected here, in the caller's thread,
                    # so a caller mutating the returned object cannot race the
                    # shadow thread into a spurious current-vs-candidate diff.
                    current_projection = (
                        _projection.extract_phase(spec, output, "shadow_current")
                        if spec is not None else None
                    )
                    _spawn_shadow(
                        component_id, trace_id, cand, args_snap, kwargs_snap,
                        current_output_repr, shadow_ctx, spec, current_projection,
                        payload_mode,
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
    current_output_repr: Optional[str],
    shadow_ctx: Optional[contextvars.Context] = None,
    spec: "Optional[_projection.ProjectionSpec]" = None,
    current_projection: Optional[Dict[str, Any]] = None,
    payload_mode: str = "redacted",
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
                c_error = (
                    f"{type(e).__name__}: {e}"
                    if payload_mode == "full" else type(e).__name__
                )
            c_duration = (time.perf_counter() - c_start) * 1000.0

            payload = {
                "trace_id": trace_id,
                "component_id": component_id,
                "current_output": current_output_repr,
                "candidate_output": (
                    None
                    if c_error or payload_mode == "metadata"
                    else _payload_repr(c_output)
                ),
                "candidate_error": c_error,
                "candidate_duration_ms": c_duration,
                "timestamp": time.time(),
            }
            # Shadow projections (Issue #150): shadow_current was projected in
            # the caller's thread; the candidate output is projected here. Only
            # when a spec is registered, so unprojected components incur zero
            # extra cost. Non-fatal.
            projections = []
            if current_projection is not None:
                projections.append(current_projection)
            if spec is not None and c_error is None:
                try:
                    cand_proj = _projection.extract_phase(spec, c_output, "shadow_candidate")
                    if cand_proj is not None:
                        projections.append(cand_proj)
                except Exception:  # noqa: BLE001
                    logger.debug("shadow projection failed", exc_info=True)
            if projections:
                payload["projections"] = projections
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
