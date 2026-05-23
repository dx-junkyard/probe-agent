import os


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
