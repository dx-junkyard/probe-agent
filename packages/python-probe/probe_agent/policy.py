import threading
import time
from typing import Any, Dict, Optional

from .client import ControlClient
from .config import ProbeConfig


class PolicyCache:
    """Per-component policy cache with a small TTL.

    A failed fetch returns the last known value (or the default) so that
    Control Server outages never block the host application.
    """

    def __init__(self, client: Optional[ControlClient] = None, ttl: Optional[float] = None):
        self._client = client or ControlClient()
        self._ttl = ttl if ttl is not None else ProbeConfig.policy_ttl()
        self._lock = threading.Lock()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._fetched_at: Dict[str, float] = {}

    def _default(self) -> Dict[str, Any]:
        return {"mode": ProbeConfig.default_mode()}

    def get(self, component_id: str) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            fetched = self._fetched_at.get(component_id, 0.0)
            cached = self._cache.get(component_id)
            if cached is not None and (now - fetched) < self._ttl:
                return cached

        policy = self._client.get_policy(component_id)
        with self._lock:
            if policy is None:
                if component_id not in self._cache:
                    self._cache[component_id] = self._default()
            else:
                self._cache[component_id] = policy
            self._fetched_at[component_id] = now
            return self._cache[component_id]

    def invalidate(self, component_id: Optional[str] = None) -> None:
        with self._lock:
            if component_id is None:
                self._cache.clear()
                self._fetched_at.clear()
            else:
                self._cache.pop(component_id, None)
                self._fetched_at.pop(component_id, None)
