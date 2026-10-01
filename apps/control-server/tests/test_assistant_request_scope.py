"""Issue #469: one ask computes diagnostics / system state at most once."""

import pytest

from tests.test_assistant import (  # noqa: F401  (fixture re-export)
    _create_system,
    _headers,
    _login,
    admin_client,
)


@pytest.fixture
def counters(monkeypatch):
    from app import system_diagnostics, system_state
    import app.routes.system_state as route_state

    calls = {"diag": [], "state": []}
    real_diag = system_diagnostics.run_system_diagnostics
    real_state = system_state.build_system_state

    def diag(system_id):
        calls["diag"].append(system_id)
        return real_diag(system_id)

    def state(system_id, **kw):
        calls["state"].append(system_id)
        return real_state(system_id, **kw)

    monkeypatch.setattr(system_diagnostics, "run_system_diagnostics", diag)
    monkeypatch.setattr(system_state, "build_system_state", state)
    monkeypatch.setattr(route_state, "build_system_state", state)
    return calls


def _ask(client, token, sid, screen):
    r = client.post(
        "/assistant/ask",
        json={"screen_id": screen, "question": "現在の状況は?"},
        headers=_headers(token, sid),
    )
    assert r.status_code == 200, r.text
    return r


@pytest.mark.parametrize("screen", ["overview", "settings", "system-understanding"])
def test_one_ask_computes_each_once(admin_client, counters, screen):
    token = _login(admin_client)
    sid = _create_system(admin_client, token)["id"]
    counters["diag"].clear()
    counters["state"].clear()
    _ask(admin_client, token, sid, screen)
    assert counters["diag"] == [sid]
    assert counters["state"] == [sid]


def test_standalone_routes_run_their_own(admin_client, counters):
    token = _login(admin_client)
    sid = _create_system(admin_client, token)["id"]
    counters["diag"].clear()
    counters["state"].clear()
    r = admin_client.get("/system-state", headers=_headers(token, sid))
    assert r.status_code == 200
    assert counters["diag"] == [sid] and counters["state"] == [sid]
    counters["diag"].clear()
    counters["state"].clear()
    r = admin_client.get("/overview", headers=_headers(token, sid))
    assert r.status_code == 200
    assert counters["diag"] == [sid] and counters["state"] == [sid]


def test_no_cross_request_or_cross_system_reuse(admin_client, counters):
    token = _login(admin_client)
    a = _create_system(admin_client, token, "sys-a")["id"]
    b = _create_system(admin_client, token, "sys-b")["id"]
    counters["diag"].clear()
    counters["state"].clear()
    _ask(admin_client, token, a, "overview")
    _ask(admin_client, token, a, "overview")
    assert counters["diag"] == [a, a]
    _ask(admin_client, token, b, "overview")
    assert counters["diag"] == [a, a, b]
    assert counters["state"] == [a, a, b]


def test_injected_report_yields_equal_items(admin_client):
    from app.system_diagnostics import run_system_diagnostics
    from app.system_state import build_system_state

    token = _login(admin_client)
    sid = _create_system(admin_client, token)["id"]

    def items(a):
        return [i.model_dump() if hasattr(i, "model_dump") else i for i in a.items]

    plain = build_system_state(sid)
    injected = build_system_state(sid, diagnostics_report=run_system_diagnostics(sid))
    assert items(plain) == items(injected)


def test_scope_counters_memoize():
    from app.assistant_request_scope import AskRequestScope

    stages = []

    class _T:
        def __init__(self, n):
            self.n = n

        def __enter__(self):
            stages.append(self.n)

        def __exit__(self, *a):
            return False

    scope = AskRequestScope(999_999, timer=lambda n: _T(n))
    with pytest.raises(Exception):
        scope.system_state()  # unknown System: fails, nothing counted
    assert scope.system_state_runs == 0


def test_overview_guard_degrades_loop_when_provider_raises(admin_client):
    from app import overview_projection

    token = _login(admin_client)
    sid = _create_system(admin_client, token)["id"]

    def provider():
        raise RuntimeError("shared state failed")

    result = overview_projection.build_overview(sid, system_state_provider=provider)
    assert "loop" in result.degraded_sections
