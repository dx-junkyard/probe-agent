import math
import os
from typing import Optional


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class ProbeConfig:
    """Runtime configuration read from environment variables.

    Values are evaluated lazily via classmethods so tests can mutate
    `os.environ` between runs without reloading the module.
    """

    ENV_ENABLED = "PROBE_ENABLED"
    ENV_SERVER_URL = "PROBE_SERVER_URL"
    ENV_DEFAULT_MODE = "PROBE_DEFAULT_MODE"
    ENV_POLICY_TTL = "PROBE_POLICY_TTL"
    ENV_TIMEOUT = "PROBE_HTTP_TIMEOUT"
    ENV_SHUTDOWN_TIMEOUT = "PROBE_SHUTDOWN_TIMEOUT"
    ENV_API_KEY = "PROBE_API_KEY"
    ENV_PAYLOAD_MODE = "PROBE_PAYLOAD_MODE"
    ENV_REPLAY_CAPTURE_MAX_BYTES = "PROBE_REPLAY_CAPTURE_MAX_BYTES"
    ENV_SEND_QUEUE_MAX = "PROBE_SEND_QUEUE_MAX"
    ENV_BREAKER_FAILURE_THRESHOLD = "PROBE_BREAKER_FAILURE_THRESHOLD"
    ENV_BREAKER_RESET_SECONDS = "PROBE_BREAKER_RESET_SECONDS"
    # Issue #290 Finding 5: optional deployment provenance tags. Unset/blank
    # means "unknown" (None) -- never fabricated or defaulted, so the
    # Control Server's Runtime Reality Check provenance stays honest about
    # what it actually observed.
    ENV_ENVIRONMENT = "PROBE_ENVIRONMENT"
    ENV_GIT_SHA = "PROBE_GIT_SHA"

    PAYLOAD_MODES = ("metadata", "redacted", "full")

    @classmethod
    def enabled(cls) -> bool:
        return _env_bool(cls.ENV_ENABLED, True)

    @classmethod
    def server_url(cls) -> str:
        return os.getenv(cls.ENV_SERVER_URL, "http://localhost:8000").rstrip("/")

    @classmethod
    def default_mode(cls) -> str:
        return os.getenv(cls.ENV_DEFAULT_MODE, "trace")

    @classmethod
    def policy_ttl(cls) -> float:
        try:
            return float(os.getenv(cls.ENV_POLICY_TTL, "10"))
        except ValueError:
            return 10.0

    @classmethod
    def http_timeout(cls) -> float:
        try:
            return float(os.getenv(cls.ENV_TIMEOUT, "2"))
        except ValueError:
            return 2.0

    @classmethod
    def shutdown_timeout(cls) -> float:
        """Max seconds to wait at interpreter exit for shadow threads to finish."""
        try:
            return float(os.getenv(cls.ENV_SHUTDOWN_TIMEOUT, "10"))
        except ValueError:
            return 10.0

    @classmethod
    def api_key(cls) -> Optional[str]:
        return os.getenv(cls.ENV_API_KEY) or None

    @classmethod
    def payload_mode(cls) -> str:
        """Telemetry payload mode; unknown values use mandatory redaction."""
        mode = os.getenv(cls.ENV_PAYLOAD_MODE, "redacted").strip().lower()
        return mode if mode in cls.PAYLOAD_MODES else "redacted"

    @classmethod
    def replay_capture_max_bytes(cls) -> int:
        """Max serialized byte size of a structured input capture (Issue #243).

        A capture whose canonical JSON exceeds this is dropped entirely
        (a truncated capture cannot round-trip), classifying the trace as
        ``unreplayable`` with reason ``size_limit_exceeded``.
        """
        try:
            return int(os.getenv(cls.ENV_REPLAY_CAPTURE_MAX_BYTES, "65536"))
        except ValueError:
            return 65536

    @classmethod
    def send_queue_max(cls) -> int:
        try:
            return max(1, int(os.getenv(cls.ENV_SEND_QUEUE_MAX, "1000")))
        except ValueError:
            return 1000

    @classmethod
    def breaker_failure_threshold(cls) -> int:
        try:
            return max(
                1,
                int(os.getenv(cls.ENV_BREAKER_FAILURE_THRESHOLD, "5")),
            )
        except ValueError:
            return 5

    @classmethod
    def breaker_reset_seconds(cls) -> float:
        try:
            value = float(os.getenv(cls.ENV_BREAKER_RESET_SECONDS, "30"))
            if not math.isfinite(value):
                return 30.0
            return max(0.1, value)
        except ValueError:
            return 30.0

    @classmethod
    def environment(cls) -> Optional[str]:
        """Deployment environment tag (e.g. 'production'), or None when unset/blank."""
        value = os.getenv(cls.ENV_ENVIRONMENT)
        if value is None:
            return None
        value = value.strip()
        return value or None

    @classmethod
    def git_sha(cls) -> Optional[str]:
        """Deployed commit sha, or None when unset/blank."""
        value = os.getenv(cls.ENV_GIT_SHA)
        if value is None:
            return None
        value = value.strip()
        return value or None
