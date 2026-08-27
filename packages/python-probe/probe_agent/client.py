"""Control Server client with bounded asynchronous telemetry transport."""

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from .config import ProbeConfig

logger = logging.getLogger("probe_agent.client")

_CLOSED = "closed"
_OPEN = "open"
_HALF_OPEN = "half_open"
BREAKER_STATES = (_CLOSED, _OPEN, _HALF_OPEN)

_Sender = Callable[[str, Dict[str, Any]], bool]
_Clock = Callable[[], float]


class ControlClient:
    """Stdlib-only HTTP client with one bounded POST worker.

    Trace and shadow POSTs use a fixed single daemon worker and a bounded FIFO
    queue. ``get_policy`` remains synchronous and is deliberately outside the
    queue and circuit breaker.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        *,
        sender: Optional[_Sender] = None,
        clock: Optional[_Clock] = None,
        queue_max: Optional[int] = None,
        failure_threshold: Optional[int] = None,
        reset_seconds: Optional[float] = None,
    ):
        self._base_url = (base_url or ProbeConfig.server_url()).rstrip("/")
        self._timeout = timeout if timeout is not None else ProbeConfig.http_timeout()
        self._clock = clock or time.monotonic
        self._queue_max = max(
            1,
            queue_max if queue_max is not None else ProbeConfig.send_queue_max(),
        )
        self._failure_threshold = max(
            1,
            failure_threshold
            if failure_threshold is not None
            else ProbeConfig.breaker_failure_threshold(),
        )
        self._reset_seconds = max(
            0.1,
            reset_seconds
            if reset_seconds is not None
            else ProbeConfig.breaker_reset_seconds(),
        )
        self._sender = sender or self._http_post
        self._queue: "queue.Queue[Tuple[str, Dict[str, Any]]]" = queue.Queue(
            maxsize=self._queue_max
        )

        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._worker_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._pending = 0

        self._state = _CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_reserved = False

        # Unacknowledged counters. A successful POST acknowledges only the
        # snapshot attached to that POST, preserving concurrent increments.
        self._dropped_count = 0
        self._failure_count = 0

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        key = ProbeConfig.api_key()
        if key:
            headers["X-Api-Key"] = key
        return headers

    def _http_post(self, path: str, payload: Dict[str, Any]) -> bool:
        """Return true for every HTTP 2xx, including an empty response body."""
        url = f"{self._base_url}{path}"
        try:
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                response.read()
                return status is not None and 200 <= int(status) < 300
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
            TypeError,
        ) as error:
            logger.debug("probe client POST %s failed: %s", url, error)
            return False

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        url = f"{self._base_url}{path}"
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read()
                if not body:
                    return None
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            logger.debug("probe client GET %s failed: %s", url, error)
            return None

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            worker = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name="probe-transport",
            )
            self._worker = worker
            try:
                worker.start()
            except Exception:
                self._worker = None
                raise

    def transport_is_open(self) -> bool:
        """Cheap early gate used before trace IDs or payloads are built."""
        with self._lock:
            return (
                self._state == _OPEN
                and self._clock() - self._opened_at < self._reset_seconds
            )

    def _reserve_enqueue(self) -> bool:
        with self._lock:
            if self._state == _OPEN:
                if self._clock() - self._opened_at < self._reset_seconds:
                    self._dropped_count += 1
                    return False
                self._state = _HALF_OPEN
                self._half_open_reserved = False
            if self._state == _HALF_OPEN:
                if self._half_open_reserved:
                    self._dropped_count += 1
                    return False
                self._half_open_reserved = True
            return True

    def _release_failed_reservation(self) -> None:
        with self._lock:
            if self._state == _HALF_OPEN:
                self._half_open_reserved = False

    def _enqueue(self, path: str, payload: Dict[str, Any]) -> bool:
        """Non-blocking drop-newest enqueue; never raises to the caller."""
        try:
            if not self._reserve_enqueue():
                return False
            # Account before publishing to the queue: an already-running
            # worker may consume immediately after put_nowait returns.
            with self._idle:
                self._pending += 1
            self._queue.put_nowait((path, payload))
        except queue.Full:
            self._release_failed_reservation()
            with self._idle:
                self._pending = max(0, self._pending - 1)
                self._dropped_count += 1
                self._idle.notify_all()
            return False
        except Exception:  # noqa: BLE001 — host application isolation
            self._release_failed_reservation()
            with self._idle:
                self._pending = max(0, self._pending - 1)
                self._dropped_count += 1
                self._idle.notify_all()
            logger.debug("probe transport enqueue failed", exc_info=True)
            return False

        try:
            self._ensure_worker()
        except Exception:  # noqa: BLE001 — thread creation must not escape
            logger.debug("probe transport worker start failed", exc_info=True)
            self._drain_queue_as_dropped()
            return False
        return True

    def _summary_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "dropped_count": self._dropped_count,
                "failure_count": self._failure_count,
                "state": self._state,
            }

    def transport_stats(self) -> Dict[str, Any]:
        """Return a thread-safe, read-only snapshot of transport state."""
        with self._lock:
            return {
                "dropped_count": self._dropped_count,
                "failure_count": self._failure_count,
                "state": self._state,
                "consecutive_failures": self._consecutive_failures,
                "queue_size": self._queue.qsize(),
            }

    @staticmethod
    def _payload_with_summary(
        payload: Dict[str, Any], summary: Dict[str, Any]
    ) -> Dict[str, Any]:
        if summary["dropped_count"] == 0 and summary["failure_count"] == 0:
            return payload
        enriched = dict(payload)
        enriched["sdk_transport"] = summary
        return enriched

    def _ack_summary(self, summary: Dict[str, Any]) -> None:
        with self._lock:
            self._dropped_count = max(
                0,
                self._dropped_count - summary["dropped_count"],
            )
            self._failure_count = max(
                0,
                self._failure_count - summary["failure_count"],
            )

    def _record_success(self, summary: Dict[str, Any]) -> None:
        self._ack_summary(summary)
        with self._lock:
            self._state = _CLOSED
            self._consecutive_failures = 0
            self._half_open_reserved = False

    def _drain_queue_as_dropped(self) -> None:
        drained = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()
                drained += 1
        if drained:
            with self._idle:
                self._dropped_count += drained
                self._pending = max(0, self._pending - drained)
                self._idle.notify_all()

    def _record_failure(self) -> None:
        opened = False
        with self._lock:
            self._failure_count += 1
            self._consecutive_failures += 1
            if (
                self._state == _HALF_OPEN
                or self._consecutive_failures >= self._failure_threshold
            ):
                self._state = _OPEN
                self._opened_at = self._clock()
                self._half_open_reserved = False
                opened = True
        if opened:
            self._drain_queue_as_dropped()

    def _worker_loop(self) -> None:
        while True:
            path, payload = self._queue.get()
            summary = self._summary_snapshot()
            outbound = self._payload_with_summary(payload, summary)
            try:
                success = bool(self._sender(path, outbound))
            except Exception:  # noqa: BLE001 — sender is an isolation boundary
                logger.debug("probe transport sender failed", exc_info=True)
                success = False
            if success:
                self._record_success(summary)
            else:
                self._record_failure()
            self._queue.task_done()
            with self._idle:
                self._pending = max(0, self._pending - 1)
                self._idle.notify_all()

    def send_trace(self, trace: Dict[str, Any]) -> bool:
        return self._enqueue("/traces", trace)

    def send_shadow_result(self, result: Dict[str, Any]) -> bool:
        path = f"/components/{result['component_id']}/shadow-results"
        return self._enqueue(path, result)

    def get_policy(self, component_id: str) -> Optional[Dict[str, Any]]:
        return self._get(f"/components/{component_id}/policy")

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait up to ``timeout`` for all accepted events to be processed."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._idle:
            while self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(timeout=remaining)
        return True
