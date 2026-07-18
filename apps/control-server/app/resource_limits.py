"""Deterministic ingress and resource quotas (Issue #273).

Rate limits are intentionally process-local fixed windows. Durable daily LLM
usage and trace-storage rejection state live in SQLite and are System scoped.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple

from .db import db_path, get_conn


TRACE_RATE_LIMIT_ENV = "CONTROL_TRACE_RATE_LIMIT_PER_SECOND"
MANAGEMENT_RATE_LIMIT_ENV = "CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE"
LLM_DAILY_LIMIT_ENV = "CONTROL_LLM_DAILY_EXECUTION_LIMIT"
TRACE_MAX_ROWS_ENV = "CONTROL_TRACE_MAX_ROWS_PER_SYSTEM"
TRACE_MAX_BYTES_ENV = "CONTROL_TRACE_MAX_BYTES_PER_SYSTEM"

DEFAULT_TRACE_RATE_LIMIT = 1_000
DEFAULT_MANAGEMENT_RATE_LIMIT = 600
DEFAULT_LLM_DAILY_LIMIT = 1_000
DEFAULT_TRACE_MAX_ROWS = 1_000_000
DEFAULT_TRACE_MAX_BYTES = 1_073_741_824

_PRODUCTION_REQUIRED = (
    TRACE_RATE_LIMIT_ENV,
    MANAGEMENT_RATE_LIMIT_ENV,
    LLM_DAILY_LIMIT_ENV,
    TRACE_MAX_ROWS_ENV,
    TRACE_MAX_BYTES_ENV,
)


class ResourceLimitExceeded(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def validate_resource_limit_config(*, production: bool) -> None:
    if production:
        missing = [name for name in _PRODUCTION_REQUIRED if not os.getenv(name, "").strip()]
        if missing:
            raise RuntimeError(
                "CONTROL_ENV=production requires explicit resource limits: "
                + ", ".join(missing)
            )
    _positive_int(TRACE_RATE_LIMIT_ENV, DEFAULT_TRACE_RATE_LIMIT)
    _positive_int(MANAGEMENT_RATE_LIMIT_ENV, DEFAULT_MANAGEMENT_RATE_LIMIT)
    _positive_int(LLM_DAILY_LIMIT_ENV, DEFAULT_LLM_DAILY_LIMIT)
    _positive_int(TRACE_MAX_ROWS_ENV, DEFAULT_TRACE_MAX_ROWS)
    _positive_int(TRACE_MAX_BYTES_ENV, DEFAULT_TRACE_MAX_BYTES)


@dataclass
class _Window:
    number: int
    count: int


class FixedWindowLimiter:
    """Thread-safe fixed-window limiter with an injectable clock for tests."""

    def __init__(
        self,
        limit: Callable[[], int],
        window_seconds: int,
        *,
        clock=time.time,
        max_keys: int = 100_000,
    ):
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._windows: Dict[str, _Window] = {}

    def allow(self, key: str) -> bool:
        window_number = int(self._clock() // self._window_seconds)
        with self._lock:
            current = self._windows.get(key)
            if current is None or current.number != window_number:
                if current is None and len(self._windows) >= self._max_keys:
                    # Discard only inactive windows. If every key is active,
                    # reject a new identity rather than growing without bound.
                    self._windows = {
                        existing_key: window
                        for existing_key, window in self._windows.items()
                        if window.number == window_number
                    }
                    if len(self._windows) >= self._max_keys:
                        return False
                self._windows[key] = _Window(window_number, 1)
                return True
            if current.count >= self._limit():
                return False
            current.count += 1
            return True

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()


_trace_limiter = FixedWindowLimiter(
    lambda: _positive_int(TRACE_RATE_LIMIT_ENV, DEFAULT_TRACE_RATE_LIMIT), 1
)
_management_limiter = FixedWindowLimiter(
    lambda: _positive_int(MANAGEMENT_RATE_LIMIT_ENV, DEFAULT_MANAGEMENT_RATE_LIMIT), 60
)


def enforce_trace_rate_limit(token_key: str) -> None:
    if not _trace_limiter.allow(f"db:{_database_identity()}:token:{token_key}"):
        raise ResourceLimitExceeded(
            "trace_rate_limit_exceeded",
            "Trace ingestion rate limit exceeded for this API token",
        )


def enforce_management_rate_limit(user_id: int) -> None:
    if not _management_limiter.allow(f"db:{_database_identity()}:user:{user_id}"):
        raise ResourceLimitExceeded(
            "management_rate_limit_exceeded",
            "Management API rate limit exceeded for this user",
        )


def _database_identity() -> str:
    """Separate equal numeric ids belonging to different SQLite databases."""
    canonical = os.path.realpath(os.path.abspath(db_path()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def reset_in_memory_rate_limits() -> None:
    """Test hook; startup does not need to call this."""
    _trace_limiter.clear()
    _management_limiter.clear()


_current_system_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "resource_limit_system_id", default=None
)


def set_current_system_id(system_id: int):
    return _current_system_id.set(system_id)


def reset_current_system_id(token) -> None:
    _current_system_id.reset(token)


def current_system_id() -> Optional[int]:
    return _current_system_id.get()


def _utc_day(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc).date().isoformat()


def consume_llm_execution(system_id: int, *, now: Optional[float] = None) -> Tuple[int, int]:
    """Atomically consume one System-scoped daily execution allowance."""
    limit = _positive_int(LLM_DAILY_LIMIT_ENV, DEFAULT_LLM_DAILY_LIMIT)
    day = _utc_day(now)
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT execution_count FROM llm_daily_usage "
                "WHERE system_id = ? AND usage_date = ?",
                (system_id, day),
            ).fetchone()
            used = row["execution_count"] if row else 0
            if used >= limit:
                conn.execute("ROLLBACK")
                raise ResourceLimitExceeded(
                    "llm_daily_limit_exceeded",
                    f"Daily LLM execution limit ({limit}) exceeded for this System",
                )
            used += 1
            conn.execute(
                """INSERT INTO llm_daily_usage
                       (system_id, usage_date, execution_count, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(system_id, usage_date) DO UPDATE SET
                       execution_count = excluded.execution_count,
                       updated_at = excluded.updated_at""",
                (system_id, day, used, time.time()),
            )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return used, limit


def trace_storage_limits() -> Tuple[int, int]:
    return (
        _positive_int(TRACE_MAX_ROWS_ENV, DEFAULT_TRACE_MAX_ROWS),
        _positive_int(TRACE_MAX_BYTES_ENV, DEFAULT_TRACE_MAX_BYTES),
    )
