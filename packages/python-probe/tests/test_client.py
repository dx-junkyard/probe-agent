import json
import threading
import time
import urllib.error

import pytest


@pytest.fixture(autouse=True)
def _reset_modules():
    import sys
    for mod in ["probe_agent.client", "probe_agent.config"]:
        sys.modules.pop(mod, None)
    yield
    for mod in ["probe_agent.client", "probe_agent.config"]:
        sys.modules.pop(mod, None)


def test_headers_include_api_key_when_set(monkeypatch):
    monkeypatch.setenv("PROBE_API_KEY", "my-secret-key")
    from probe_agent.client import ControlClient

    client = ControlClient(base_url="http://localhost:9999")
    headers = client._headers()
    assert headers["X-Api-Key"] == "my-secret-key"
    assert headers["Content-Type"] == "application/json"


def test_headers_omit_api_key_when_not_set(monkeypatch):
    monkeypatch.delenv("PROBE_API_KEY", raising=False)
    from probe_agent.client import ControlClient

    client = ControlClient(base_url="http://localhost:9999")
    headers = client._headers()
    assert "X-Api-Key" not in headers
    assert headers["Content-Type"] == "application/json"


def test_headers_omit_api_key_when_empty_string(monkeypatch):
    monkeypatch.setenv("PROBE_API_KEY", "")
    from probe_agent.client import ControlClient

    client = ControlClient(base_url="http://localhost:9999")
    headers = client._headers()
    assert "X-Api-Key" not in headers


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_http_post_treats_empty_2xx_as_success(monkeypatch):
    from probe_agent.client import ControlClient

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b""

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    client = ControlClient(base_url="http://control")
    assert client._http_post("/traces", {"trace_id": "t1"}) is True


def test_http_post_treats_429_as_failure(monkeypatch):
    from probe_agent.client import ControlClient

    def rate_limited(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://control/traces",
            429,
            "rate limited",
            {},
            None,
        )

    monkeypatch.setattr("urllib.request.urlopen", rate_limited)
    client = ControlClient(base_url="http://control")
    assert client._http_post("/traces", {"trace_id": "t1"}) is False


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timeout"), urllib.error.URLError("offline")],
)
def test_http_post_treats_timeout_and_urlerror_as_failure(monkeypatch, error):
    from probe_agent.client import ControlClient

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fail)
    client = ControlClient(base_url="http://control")
    assert client._http_post("/traces", {"trace_id": "t1"}) is False


def test_bounded_fifo_queue_drops_newest_without_blocking():
    from probe_agent.client import ControlClient

    started = threading.Event()
    release = threading.Event()
    received = []

    def sender(_path, payload):
        received.append(payload["n"])
        if payload["n"] == 1:
            started.set()
            release.wait(timeout=2)
        return True

    client = ControlClient(sender=sender, queue_max=1)
    assert client.send_trace({"n": 1}) is True
    assert started.wait(timeout=1)
    assert client.send_trace({"n": 2}) is True

    before = time.perf_counter()
    assert client.send_trace({"n": 3}) is False
    latency = time.perf_counter() - before
    assert latency < 0.05
    assert client.transport_stats()["dropped_count"] == 1

    release.set()
    assert client.flush(timeout=2)
    assert received == [1, 2]


def test_flush_wait_is_bounded_when_sender_is_stuck():
    from probe_agent.client import ControlClient

    started = threading.Event()
    release = threading.Event()

    def sender(_path, _payload):
        started.set()
        release.wait(timeout=2)
        return True

    client = ControlClient(sender=sender)
    client.send_trace({"n": 1})
    assert started.wait(timeout=1)
    before = time.perf_counter()
    assert client.flush(timeout=0.02) is False
    assert time.perf_counter() - before < 0.1
    release.set()
    assert client.flush(timeout=1) is True


def test_worker_start_failure_is_non_fatal_and_drops_accepted_event(monkeypatch):
    from probe_agent.client import ControlClient

    client = ControlClient(sender=lambda *_args: True)

    def fail_start():
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(client, "_ensure_worker", fail_start)
    assert client.send_trace({"n": 1}) is False
    assert client.flush(timeout=0.1) is True
    stats = client.transport_stats()
    assert stats["dropped_count"] == 1
    assert stats["queue_size"] == 0


def test_breaker_closed_open_half_open_success_and_summary_ack():
    from probe_agent.client import ControlClient

    clock = _Clock()
    attempts = []
    outcomes = iter([False, False, True])

    def sender(_path, payload):
        attempts.append(payload)
        return next(outcomes)

    client = ControlClient(
        sender=sender,
        clock=clock,
        failure_threshold=2,
        reset_seconds=10,
    )
    client.send_trace({"n": 1})
    assert client.flush(timeout=1)
    assert client.transport_stats()["state"] == "closed"
    assert client.transport_stats()["failure_count"] == 1

    client.send_trace({"n": 2})
    assert client.flush(timeout=1)
    stats = client.transport_stats()
    assert stats["state"] == "open"
    assert stats["failure_count"] == 2
    assert client.transport_is_open() is True

    # Direct calls while open are rejected without invoking the sender.
    assert client.send_trace({"n": 99}) is False
    assert len(attempts) == 2

    clock.advance(10)
    assert client.transport_is_open() is False
    assert client.send_trace({"n": 3}) is True
    assert client.flush(timeout=1)
    assert attempts[-1]["sdk_transport"] == {
        "dropped_count": 1,
        "failure_count": 2,
        "state": "half_open",
    }
    stats = client.transport_stats()
    assert stats["state"] == "closed"
    assert stats["failure_count"] == 0
    assert stats["dropped_count"] == 0


def test_half_open_allows_exactly_one_trial_and_failure_reopens():
    from probe_agent.client import ControlClient

    clock = _Clock()
    half_started = threading.Event()
    release_half = threading.Event()
    calls = 0

    def sender(_path, _payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        half_started.set()
        release_half.wait(timeout=2)
        return False

    client = ControlClient(
        sender=sender,
        clock=clock,
        failure_threshold=1,
        reset_seconds=5,
    )
    client.send_trace({"n": 1})
    assert client.flush(timeout=1)
    assert client.transport_stats()["state"] == "open"

    clock.advance(5)
    assert client.send_trace({"n": 2}) is True
    assert half_started.wait(timeout=1)
    assert client.transport_stats()["state"] == "half_open"
    assert client.send_trace({"n": 3}) is False
    release_half.set()
    assert client.flush(timeout=1)
    assert calls == 2
    assert client.transport_stats()["state"] == "open"


def test_failed_summary_is_not_acknowledged_until_success():
    from probe_agent.client import ControlClient

    sent = []
    outcomes = iter([False, True])

    def sender(_path, payload):
        sent.append(json.loads(json.dumps(payload)))
        return next(outcomes)

    client = ControlClient(sender=sender, failure_threshold=5)
    client.send_trace({"n": 1})
    assert client.flush(timeout=1)
    assert client.transport_stats()["failure_count"] == 1
    client.send_trace({"n": 2})
    assert client.flush(timeout=1)
    assert sent[1]["sdk_transport"]["failure_count"] == 1
    assert client.transport_stats()["failure_count"] == 0


def test_policy_get_is_not_queued_or_blocked_by_open_breaker(monkeypatch):
    from probe_agent.client import ControlClient

    client = ControlClient(sender=lambda *_args: False, failure_threshold=1)
    client.send_trace({"n": 1})
    assert client.flush(timeout=1)
    assert client.transport_is_open()
    monkeypatch.setattr(client, "_get", lambda path: {"mode": "trace", "path": path})
    policy = client.get_policy("comp")
    assert policy["mode"] == "trace"
    assert policy["path"].endswith("/policy")


def test_transport_env_values_use_defaults_and_lower_bounds(monkeypatch):
    monkeypatch.setenv("PROBE_SEND_QUEUE_MAX", "invalid")
    monkeypatch.setenv("PROBE_BREAKER_FAILURE_THRESHOLD", "0")
    monkeypatch.setenv("PROBE_BREAKER_RESET_SECONDS", "-4")
    from probe_agent.config import ProbeConfig

    assert ProbeConfig.send_queue_max() == 1000
    assert ProbeConfig.breaker_failure_threshold() == 1
    assert ProbeConfig.breaker_reset_seconds() == 0.1
    monkeypatch.setenv("PROBE_BREAKER_RESET_SECONDS", "nan")
    assert ProbeConfig.breaker_reset_seconds() == 30.0
