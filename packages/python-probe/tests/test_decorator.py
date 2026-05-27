import importlib
import os
import sys
from typing import List

import pytest


@pytest.fixture
def sdk(monkeypatch):
    """Reload probe_agent with a stub ControlClient for each test."""
    monkeypatch.setenv("PROBE_ENABLED", "true")
    monkeypatch.setenv("PROBE_DEFAULT_MODE", "trace")
    monkeypatch.setenv("PROBE_POLICY_TTL", "0.0")

    # Reload modules so the patched env / fresh state apply.
    for mod in [
        "probe_agent.decorator",
        "probe_agent.policy",
        "probe_agent.client",
        "probe_agent.config",
        "probe_agent",
    ]:
        sys.modules.pop(mod, None)

    import probe_agent  # noqa: F401  (re-imported for side effects)
    from probe_agent import decorator as decorator_mod
    from probe_agent.policy import PolicyCache

    sent_traces: List[dict] = []
    sent_shadows: List[dict] = []
    policy_value = {"mode": "trace"}

    class FakeClient:
        def send_trace(self, t):
            sent_traces.append(t)

        def send_shadow_result(self, s):
            sent_shadows.append(s)

        def get_policy(self, _cid):
            return dict(policy_value)

    fake = FakeClient()
    decorator_mod._client = fake
    decorator_mod._policy_cache = PolicyCache(client=fake, ttl=0.0)
    decorator_mod._candidates.clear()

    return {
        "decorator_mod": decorator_mod,
        "traces": sent_traces,
        "shadows": sent_shadows,
        "set_mode": lambda m: policy_value.update(mode=m),
    }


def test_trace_records_input_output(sdk):
    probe = sdk["decorator_mod"].probe

    @probe(component_id="adder")
    def add(a, b):
        return a + b

    assert add(2, 3) == 5

    assert len(sdk["traces"]) == 1
    t = sdk["traces"][0]
    assert t["component_id"] == "adder"
    assert t["mode"] == "trace"
    assert t["error"] is None
    assert "5" in t["output"]
    assert t["input"]["args"] == ["2", "3"]
    assert t["duration_ms"] >= 0


def test_off_mode_skips_trace(sdk):
    sdk["set_mode"]("off")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="adder")
    def add(a, b):
        return a + b

    assert add(1, 2) == 3
    assert sdk["traces"] == []


def test_disabled_via_env(monkeypatch, sdk):
    monkeypatch.setenv("PROBE_ENABLED", "false")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="adder")
    def add(a, b):
        return a + b

    assert add(1, 2) == 3
    assert sdk["traces"] == []


def test_error_is_recorded_and_reraised(sdk):
    probe = sdk["decorator_mod"].probe

    @probe(component_id="boom")
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()

    assert len(sdk["traces"]) == 1
    assert sdk["traces"][0]["error"] is not None
    assert "ValueError" in sdk["traces"][0]["error"]


def test_shadow_runs_candidate(sdk):
    import time

    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate

    def candidate(x):
        return x + 100

    set_candidate("doubler", candidate)

    @probe(component_id="doubler")
    def doubler(x):
        return x * 2

    assert doubler(5) == 10  # current return value unchanged

    # candidate runs in a background thread; wait briefly
    for _ in range(50):
        if sdk["shadows"]:
            break
        time.sleep(0.02)

    assert len(sdk["shadows"]) == 1
    s = sdk["shadows"][0]
    assert s["component_id"] == "doubler"
    assert "10" in s["current_output"]
    assert "105" in s["candidate_output"]
    assert s["candidate_error"] is None


def test_shadow_candidate_failure_does_not_break_current(sdk):
    import time

    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate

    def bad(_):
        raise RuntimeError("candidate broken")

    set_candidate("safe", bad)

    @probe(component_id="safe")
    def safe(x):
        return x

    assert safe(7) == 7  # current is unaffected

    for _ in range(50):
        if sdk["shadows"]:
            break
        time.sleep(0.02)

    assert len(sdk["shadows"]) == 1
    assert sdk["shadows"][0]["candidate_error"] is not None


def test_shadow_uses_snapshot_when_caller_mutates_input(sdk):
    """Caller mutates the input list AFTER calling current; candidate must
    still see the snapshot taken at call time."""
    import threading

    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate

    candidate_gate = threading.Event()
    candidate_saw: list = []

    def candidate(items: list) -> int:
        candidate_gate.wait(timeout=2.0)
        candidate_saw.append(list(items))
        return sum(items)

    set_candidate("summer", candidate)

    @probe(component_id="summer")
    def summer(items: list) -> int:
        return sum(items)

    payload = [1, 2, 3]
    result = summer(payload)
    assert result == 6

    # Caller mutates the original list before candidate gets a chance to run.
    payload.append(999)
    candidate_gate.set()

    flush = sdk["decorator_mod"].flush
    flush(timeout=2.0)

    assert candidate_saw == [[1, 2, 3]], f"candidate saw mutated input: {candidate_saw}"
    assert sdk["shadows"][0]["candidate_output"] == "6"


def test_snapshot_falls_back_for_uncopyable_input(sdk):
    """Uncopyable inputs (sockets/locks) must not break the host call."""
    import threading

    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate

    set_candidate("identity", lambda _lock: "candidate-ok")

    @probe(component_id="identity")
    def identity(_lock):
        return "current-ok"

    # threading.Lock is not deepcopy-able — must not raise.
    result = identity(threading.Lock())
    assert result == "current-ok"

    flush = sdk["decorator_mod"].flush
    flush(timeout=2.0)
    assert sdk["shadows"][0]["candidate_output"] == "'candidate-ok'"


def test_flush_waits_for_in_flight_shadows(sdk):
    """Short-lived processes must be able to deliver shadow results."""
    import threading
    import time

    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate

    delivered = threading.Event()
    real_send = sdk["decorator_mod"]._client.send_shadow_result

    def slow_send(payload):
        # Simulate a slow Control Server.
        time.sleep(0.2)
        real_send(payload)
        delivered.set()

    sdk["decorator_mod"]._client.send_shadow_result = slow_send

    set_candidate("slow", lambda x: x + 1)

    @probe(component_id="slow")
    def slow(x):
        return x

    assert slow(1) == 1
    # Without flush, this test could race; flush must block until done.
    sdk["decorator_mod"].flush(timeout=3.0)
    assert delivered.is_set()
    assert len(sdk["shadows"]) == 1


def test_shadow_in_subprocess_delivers_result(tmp_path):
    """End-to-end: a short-lived python process running @probe in shadow
    mode must deliver the shadow result via atexit hook."""
    import http.server
    import json as _json
    import socket
    import subprocess
    import sys
    import threading

    received: list = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.endswith("/policy"):
                body = _json.dumps({"mode": "shadow"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length)
            received.append((self.path, _json.loads(data)))
            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_):  # silence
            return

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        sdk_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = tmp_path / "run.py"
        script.write_text(
            "from probe_agent import probe, set_candidate\n"
            "set_candidate('sub', lambda x: x * 10)\n"
            "@probe(component_id='sub')\n"
            "def f(x):\n"
            "    return x + 1\n"
            "print(f(5))\n"
        )
        env = {
            **os.environ,
            "PYTHONPATH": sdk_path,
            "PROBE_SERVER_URL": f"http://127.0.0.1:{port}",
            "PROBE_DEFAULT_MODE": "shadow",
            "PROBE_POLICY_TTL": "0",
            "PROBE_SHUTDOWN_TIMEOUT": "5",
        }
        out = subprocess.run(
            [sys.executable, str(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "6"
    finally:
        srv.shutdown()
        th.join(timeout=2)

    paths = [p for p, _ in received]
    assert any("/traces" in p for p in paths), f"trace missing: {paths}"
    assert any("/shadow-results" in p for p in paths), f"shadow missing: {paths}"
    shadow_payload = next(payload for path, payload in received if "/shadow-results" in path)
    assert shadow_payload["current_output"] == "6"
    assert shadow_payload["candidate_output"] == "50"
