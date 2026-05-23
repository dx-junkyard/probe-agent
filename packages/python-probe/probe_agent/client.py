import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from .config import ProbeConfig

logger = logging.getLogger("probe_agent.client")


class ControlClient:
    """Tiny HTTP client for the Control Server.

    Uses only the stdlib so the SDK has zero runtime dependencies.
    All errors are swallowed and logged — the SDK must never break the
    host application if the Control Server is unreachable.
    """

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None):
        self._base_url = (base_url or ProbeConfig.server_url()).rstrip("/")
        self._timeout = timeout if timeout is not None else ProbeConfig.http_timeout()

    def _post(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self._base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read()
                if not body:
                    return None
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug("probe client POST %s failed: %s", url, e)
            return None

    def _get(self, path: str) -> Optional[Dict[str, Any]]:
        url = f"{self._base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                body = resp.read()
                if not body:
                    return None
                return json.loads(body)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.debug("probe client GET %s failed: %s", url, e)
            return None

    def send_trace(self, trace: Dict[str, Any]) -> None:
        self._post("/traces", trace)

    def send_shadow_result(self, result: Dict[str, Any]) -> None:
        self._post(f"/components/{result['component_id']}/shadow-results", result)

    def get_policy(self, component_id: str) -> Optional[Dict[str, Any]]:
        return self._get(f"/components/{component_id}/policy")
