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
    # Most legacy assertions exercise the pre-#271 raw telemetry contract.
    # Full is now an explicit opt-in; dedicated tests below cover the new
    # redacted default and explicit metadata mode.
    monkeypatch.setenv("PROBE_PAYLOAD_MODE", "full")

    # Reload modules so the patched env / fresh state apply.
    for mod in [
        "probe_agent.decorator",
        "probe_agent.policy",
        "probe_agent.client",
        "probe_agent.config",
        "probe_agent.replay_capture",
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


def test_payload_mode_defaults_to_redacted_with_mandatory_masks(sdk, monkeypatch):
    monkeypatch.delenv("PROBE_PAYLOAD_MODE", raising=False)
    probe = sdk["decorator_mod"].probe

    @probe(component_id="safe-default")
    def login(password, options):
        return {"token": "returned-secret", "status": "ok"}

    assert login("positional-secret", {"cookie": "nested-secret"})["status"] == "ok"
    trace = sdk["traces"][0]
    assert trace["input"] is not None
    assert trace["output"] is not None
    assert trace["input"]["args"][0] == "██redacted██"
    assert "status" in trace["output"]
    serialized = repr(trace)
    assert "positional-secret" not in serialized
    assert "nested-secret" not in serialized
    assert "returned-secret" not in serialized


def test_invalid_payload_mode_falls_back_to_redacted(sdk, monkeypatch):
    monkeypatch.setenv("PROBE_PAYLOAD_MODE", "everything")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="invalid-mode")
    def f(secret, value):
        return {"token": secret, "value": value}

    assert f("hidden", "visible") == {"token": "hidden", "value": "visible"}
    trace = sdk["traces"][0]
    assert trace["input"]["args"] == ["██redacted██", "'visible'"]
    assert "hidden" not in repr(trace)
    assert "visible" in trace["output"]


def test_open_transport_runs_only_function_before_trace_work(sdk, monkeypatch):
    probe = sdk["decorator_mod"].probe
    calls = []

    sdk["decorator_mod"]._client.transport_is_open = lambda: True
    sdk["decorator_mod"]._client.get_policy = lambda _cid: pytest.fail(
        "policy lookup must be skipped while breaker is open"
    )
    monkeypatch.setattr(
        sdk["decorator_mod"].uuid,
        "uuid4",
        lambda: pytest.fail("trace id must not be generated"),
    )
    monkeypatch.setattr(
        sdk["decorator_mod"],
        "_sampled_in",
        lambda *_args: pytest.fail("sampling must not run"),
    )

    @probe(component_id="breaker-open")
    def f(value):
        calls.append(value)
        return value + 1

    assert f(4) == 5
    assert calls == [4]
    assert sdk["traces"] == []


def test_slow_transport_never_blocks_return_or_exception(sdk):
    import threading
    import time

    from probe_agent.client import ControlClient

    started = threading.Event()
    release = threading.Event()

    def slow_sender(_path, _payload):
        started.set()
        release.wait(timeout=2)
        return True

    client = ControlClient(sender=slow_sender, queue_max=2)
    sdk["decorator_mod"]._client = client
    probe = sdk["decorator_mod"].probe

    @probe(component_id="nonblocking-return")
    def f(value):
        return value + 1

    before = time.perf_counter()
    assert f(4) == 5
    assert time.perf_counter() - before < 0.05
    assert started.wait(timeout=1)

    @probe(component_id="nonblocking-error")
    def boom():
        raise LookupError("original")

    before = time.perf_counter()
    with pytest.raises(LookupError, match="original"):
        boom()
    assert time.perf_counter() - before < 0.05

    release.set()
    sdk["decorator_mod"].flush(timeout=2)


def test_public_transport_stats_has_fake_client_fallback(sdk):
    assert sdk["decorator_mod"].transport_stats() == {
        "dropped_count": 0,
        "failure_count": 0,
        "state": "closed",
        "consecutive_failures": 0,
        "queue_size": 0,
    }


def test_redacted_mode_masks_nested_and_named_positional_values(sdk, monkeypatch):
    monkeypatch.setenv("PROBE_PAYLOAD_MODE", "redacted")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="redacted")
    def f(password, body=None):
        return {"Authorization": "Bearer output", "status": "ok"}

    assert f("positional-secret", body={"items": ({"TOKEN": "nested-secret"},)})
    trace = sdk["traces"][0]
    serialized = repr(trace)
    assert "positional-secret" not in serialized
    assert "nested-secret" not in serialized
    assert "Bearer output" not in serialized
    assert "status" in trace["output"]


def test_full_mode_keeps_non_sensitive_raw_but_forces_denylist(sdk):
    probe = sdk["decorator_mod"].probe

    @probe(component_id="full")
    def f(payload):
        return {"result": "visible", "session": "hidden-output"}

    assert f({"value": "visible-input", "api_key": "hidden-input"})["result"] == "visible"
    trace = sdk["traces"][0]
    serialized = repr(trace)
    assert "visible-input" in serialized
    assert "visible" in trace["output"]
    assert "hidden-input" not in serialized
    assert "hidden-output" not in serialized


def test_non_full_error_keeps_type_but_suppresses_message_and_traceback(sdk, monkeypatch):
    monkeypatch.setenv("PROBE_PAYLOAD_MODE", "redacted")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="safe-error")
    def f():
        raise ValueError("secret exception message")

    with pytest.raises(ValueError, match="secret exception message"):
        f()
    assert sdk["traces"][0]["error"] == "ValueError"
    assert "secret exception message" not in repr(sdk["traces"][0])


def test_marker_literal_and_mask_have_distinct_trace_repr(sdk, monkeypatch):
    monkeypatch.setenv("PROBE_PAYLOAD_MODE", "redacted")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="marker")
    def f(payload):
        return payload

    f({"value": "██redacted██", "secret": "real-secret"})
    input_repr = sdk["traces"][0]["input"]["args"][0]
    assert "'██redacted██'" in input_repr  # user literal is quoted
    assert "'secret': ██redacted██" in input_repr  # sentinel is not


def test_redaction_failure_does_not_change_return_value(sdk, monkeypatch):
    monkeypatch.setenv("PROBE_PAYLOAD_MODE", "redacted")
    probe = sdk["decorator_mod"].probe

    def fail_closed(_value):
        raise RuntimeError("redactor failure")

    monkeypatch.setattr(sdk["decorator_mod"]._redaction, "redact_sensitive", fail_closed)

    @probe(component_id="redaction-failure")
    def f(value):
        return value

    expected = {"value": 1}
    assert f(expected) is expected
    trace = sdk["traces"][0]
    assert trace["input"]["args"] == ["<payload-redaction-failed>"]
    assert trace["output"] == "<payload-redaction-failed>"


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
            # This legacy assertion intentionally opts into the pre-#271
            # current/candidate raw output contract.
            "PROBE_PAYLOAD_MODE": "full",
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


# --- structured replay capture (Issue #242 Phase A / #243) -------------------

_REPLAY_KEYS = ("input_capture", "replayability", "replay_reasons")


def test_replay_capture_opt_in_records_structured_input(sdk):
    import json

    probe = sdk["decorator_mod"].probe

    @probe(component_id="capt", replay_capture=True)
    def f(point, tag=None):
        return "ok"

    assert f((1, 2), tag={"ids": {3, 1}}) == "ok"

    t = sdk["traces"][0]
    assert t["replayability"] == "replayable"
    assert t["replay_reasons"] == []
    cap = t["input_capture"]
    assert cap["args"] == [{"__probe__": "tuple", "items": [1, 2]}]
    assert cap["kwargs"]["tag"]["ids"] == {"__probe__": "set", "items": [1, 3]}
    # Existing repr-based fields are unchanged.
    assert t["input"]["args"] == ["(1, 2)"]
    assert json.dumps(cap)  # capture is pure JSON


def test_replay_capture_opt_out_has_no_keys_and_is_not_invoked(sdk, monkeypatch):
    from probe_agent import replay_capture as rc

    def boom(*_a, **_k):
        raise AssertionError("capture_input must not run when opted out")

    monkeypatch.setattr(rc, "capture_input", boom)
    probe = sdk["decorator_mod"].probe

    @probe(component_id="noopt")
    def f(x):
        return x + 1

    assert f(1) == 2
    t = sdk["traces"][0]
    for key in _REPLAY_KEYS:
        assert key not in t


def test_replay_capture_failure_is_non_fatal(sdk, monkeypatch):
    from probe_agent import replay_capture as rc

    def boom(*_a, **_k):
        raise RuntimeError("capture exploded")

    monkeypatch.setattr(rc, "capture_input", boom)
    probe = sdk["decorator_mod"].probe

    @probe(component_id="capt-fail", replay_capture=True)
    def f(x):
        return x * 2

    assert f(4) == 8  # return value preserved
    t = sdk["traces"][0]
    assert t["input_capture"] is None
    assert t["replayability"] == "unreplayable"
    assert t["replay_reasons"] == ["capture_failed"]


def test_replay_capture_preserves_exceptions(sdk):
    probe = sdk["decorator_mod"].probe

    @probe(component_id="capt-err", replay_capture=True)
    def f(x):
        raise ValueError("nope")

    with pytest.raises(ValueError):
        f(1)

    t = sdk["traces"][0]
    assert "ValueError" in t["error"]
    assert t["replayability"] == "replayable"
    assert t["input_capture"]["args"] == [1]


def test_replay_capture_redaction_never_leaks_raw_value(sdk):
    import json

    probe = sdk["decorator_mod"].probe

    @probe(component_id="capt-redact",
           replay_capture={"redact": ["$.kwargs.password"]})
    def login(user, password=None):
        return True

    assert login("u1", password="hunter2") is True
    t = sdk["traces"][0]
    assert t["replayability"] == "partial"
    assert t["replay_reasons"] == ["redacted"]
    assert "hunter2" not in json.dumps(t["input_capture"], ensure_ascii=False)
    # The SDK denylist also protects normal repr telemetry; the explicit
    # replay path is independently enforced in the structured capture.
    assert "hunter2" not in repr(t["input"])


def test_replay_capture_masks_denylisted_positional_parameter(sdk):
    probe = sdk["decorator_mod"].probe

    @probe(component_id="capt-positional-secret", replay_capture=True)
    def authenticate(password, user):
        return user

    assert authenticate("hunter-positional-42", "u1") == "u1"
    trace = sdk["traces"][0]
    assert trace["input_capture"]["args"] == ["██redacted██", "u1"]
    assert trace["replayability"] == "partial"
    assert trace["replay_reasons"] == ["redacted"]
    assert "hunter-positional-42" not in repr(trace)


def test_replay_capture_invalid_spec_raises_at_decoration(sdk):
    probe = sdk["decorator_mod"].probe
    from probe_agent import replay_capture as rc

    with pytest.raises(rc.ReplayCaptureError):
        @probe(component_id="bad-spec", replay_capture={"bogus": 1})
        def f(x):
            return x

    with pytest.raises(ValueError):  # invalid redact path (fail closed)
        @probe(component_id="bad-path", replay_capture={"redact": ["nope"]})
        def g(x):
            return x


def test_replay_capture_env_size_cap(sdk, monkeypatch):
    monkeypatch.setenv("PROBE_REPLAY_CAPTURE_MAX_BYTES", "16")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="capt-size", replay_capture=True)
    def f(payload):
        return len(payload)

    assert f("x" * 100) == 100
    t = sdk["traces"][0]
    assert t["input_capture"] is None
    assert t["replayability"] == "unreplayable"
    assert t["replay_reasons"] == ["size_limit_exceeded"]


def test_replay_capture_in_shadow_mode_keeps_production_value(sdk):
    sdk["set_mode"]("shadow")
    probe = sdk["decorator_mod"].probe
    set_candidate = sdk["decorator_mod"].set_candidate
    set_candidate("capt-shadow", lambda x: x + 100)

    @probe(component_id="capt-shadow", replay_capture=True)
    def f(x):
        return x * 2

    assert f(5) == 10  # production value unchanged
    sdk["decorator_mod"].flush(timeout=3.0)

    t = sdk["traces"][0]
    assert t["replayability"] == "replayable"
    assert t["input_capture"]["args"] == [5]
    # The shadow payload itself is unchanged by Phase A.
    s = sdk["shadows"][0]
    for key in _REPLAY_KEYS:
        assert key not in s


def test_replay_capture_off_mode_skips_capture(sdk, monkeypatch):
    from probe_agent import replay_capture as rc

    def boom(*_a, **_k):
        raise AssertionError("capture_input must not run in off mode")

    monkeypatch.setattr(rc, "capture_input", boom)
    sdk["set_mode"]("off")
    probe = sdk["decorator_mod"].probe

    @probe(component_id="capt-off", replay_capture=True)
    def f(x):
        return x

    assert f(1) == 1
    assert sdk["traces"] == []
