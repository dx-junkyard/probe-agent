"""The DB connection lock must never be held across an external LLM call.

``db.get_conn()`` holds a process-wide, NON-reentrant lock for the whole
lifetime of the connection it yields, and every LLM client consumes System
quota through ``resource_limits.consume_llm_execution()``, which opens its
own connection. Calling an LLM inside a ``with get_conn()`` block therefore
made the thread wait on a lock only it could release: a permanent, whole-
process deadlock that no timeout could break, taking every other endpoint
(and the ``/health`` check) down with it until the server was restarted.

The pre-existing interview tests could not catch this by construction. They
all run with ``LLM_PROVIDER=mock``, and the reasoning entry points fail
closed on a mock/non-reasoning client BEFORE calling ``generate_text()``
(Principle 6: no mock fallback for system understanding). The deadlock was
reachable only with a real reasoning model configured -- i.e. only in
production. Every test here therefore configures a REASONING model
(``openai``/``o3`` satisfies ``llm.is_reasoning_model``) and stubs the HTTP
layer, so the LLM code path is actually entered.

Two invariants are asserted:

1. ``get_conn()`` re-entry raises ``DatabaseReentrancyError`` instead of
   deadlocking, so the offending stack is visible.
2. At the moment any endpoint calls ``generate_text()``, the calling thread
   holds NO database connection -- checked for every LLM-calling endpoint,
   and demonstrated end to end by a second request being served while a
   reasoning call is in flight.

The LLM stub deliberately returns unparseable output: these tests assert the
locking invariant, not the happy path, so the endpoints are free to fail
closed afterwards (which the feature tests already cover).
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-lock-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    # A REASONING model, so the reasoning entry points do not fail closed
    # before reaching generate_text() the way a mock provider does.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _setup(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    token = r.cookies.get("probe_session")
    r = client.post(
        "/systems",
        json={"name": "Lock System", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    system_id = r.json()["id"]

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
            (system_id, now, now),
        )
        snapshot_id = cur.lastrowid
    return token, system_id, snapshot_id


class _CallRecorder:
    """Stands in for the LLM HTTP round trip and records lock state."""

    def __init__(self):
        self.connection_open_during_call = []
        self.calls = 0

    def __call__(self, *args, **kwargs):
        from app import db

        self.calls += 1
        self.connection_open_during_call.append(db.connection_is_open())
        # Unparseable on purpose: these tests assert locking, not parsing.
        return {"choices": [{"message": {"content": "not-json"}}]}


@pytest.fixture
def llm_calls(monkeypatch):
    recorder = _CallRecorder()
    from app import llm

    monkeypatch.setattr(llm, "_request_json", recorder)
    return recorder


# --- 1. Re-entrancy is an error, not a hang ---------------------------------


def test_nested_get_conn_raises_instead_of_deadlocking(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "reentrant.db"))
    from app import db

    db.init_db()
    with db.get_conn():
        with pytest.raises(db.DatabaseReentrancyError):
            with db.get_conn():
                pass
    # The outer block released the lock normally, so the next open succeeds.
    with db.get_conn() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert db.connection_is_open() is False


def test_llm_client_call_inside_get_conn_raises(tmp_path, monkeypatch):
    """The exact production stack that used to deadlock the whole server:
    generate_text -> _consume_current_system_quota -> consume_llm_execution
    -> get_conn, all inside a caller's own `with get_conn()` block."""
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "quota.db"))
    from app import db, llm
    from app.resource_limits import reset_current_system_id, set_current_system_id

    db.init_db()
    monkeypatch.setattr(llm, "_request_json", _CallRecorder())
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO systems (name, environment, description, created_at, updated_at)
               VALUES ('s', 'test', '', ?, ?)""",
            (time.time(), time.time()),
        )
        system_id = cur.lastrowid

    config = llm.LLMConfig(
        provider="openai", api_key="k", model="o3", base_url=None, timeout=5.0,
    )
    client = llm.create_llm_client(config)
    # The quota context is a ContextVar: always reset it, or it leaks into
    # later tests that assert an unset context raises LLMSystemContextMissing.
    context = set_current_system_id(system_id)
    try:
        with db.get_conn():
            with pytest.raises(db.DatabaseReentrancyError):
                client.generate_text([{"role": "user", "content": "hi"}])
    finally:
        reset_current_system_id(context)


# --- 2. No endpoint holds a connection across its reasoning call ------------


def _session(client, headers, snapshot_id):
    r = client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "lock"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_dialogue_turn_holds_no_connection_during_llm_call(admin_client, llm_calls):
    """The reported failure: 送信して提案を生成 wedged the whole server."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "このシステムの最上位の目的を教えてください"},
        headers=headers,
    )
    assert r.status_code != 500, r.text
    assert llm_calls.calls > 0, "the reasoning path was never reached"
    assert llm_calls.connection_open_during_call == [False] * llm_calls.calls


@pytest.mark.parametrize(
    "make_request",
    [
        pytest.param(
            lambda c, h, s: c.post(
                f"/interview/sessions/{s}/intent/propose", json={}, headers=h,
            ),
            id="intent-propose",
        ),
        pytest.param(
            lambda c, h, s: c.post(
                f"/interview/sessions/{s}/change-sets",
                json={"text": "目的を見直したい"},
                headers=h,
            ),
            id="change-set",
        ),
        pytest.param(
            lambda c, h, s: c.post(
                f"/interview/sessions/{s}/understanding", json={}, headers=h,
            ),
            id="update-understanding",
        ),
        pytest.param(
            lambda c, h, s: c.post(
                f"/interview/sessions/{s}/alignment/build", json={}, headers=h,
            ),
            id="alignment-build",
        ),
        pytest.param(
            lambda c, h, s: c.post(
                "/trace-analyzers/propose",
                json={"name": "a", "intent": "遅いトレースを探す"},
                headers=h,
            ),
            id="analyzer-propose",
        ),
    ],
)
def test_llm_endpoints_hold_no_connection_during_llm_call(
    admin_client, llm_calls, make_request,
):
    """Every endpoint that reaches an LLM must have released its connection.

    An endpoint that fails closed before reaching the reasoning call (missing
    preconditions for this minimal fixture) trivially satisfies the invariant
    and is skipped rather than asserted on.
    """
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _session(admin_client, headers, snapshot_id)

    r = make_request(admin_client, headers, session_id)
    # 502 is the correct fail-closed outcome for the unparseable stub reply;
    # 500 would mean the re-entrancy guard fired, i.e. a lock regression.
    assert r.status_code != 500, r.text
    if llm_calls.calls == 0:
        pytest.skip("endpoint failed closed before the reasoning call")
    assert llm_calls.connection_open_during_call == [False] * llm_calls.calls


# --- 3. End to end: other requests are served during a reasoning call -------


def test_other_endpoints_respond_while_llm_call_in_flight(admin_client, monkeypatch):
    """`/repository/status` returned 504 for minutes because dialogue-turn
    held the DB lock. It must now be served while the LLM call is running."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _session(admin_client, headers, snapshot_id)

    in_llm_call = threading.Event()
    release_llm_call = threading.Event()

    def _blocking_http(*args, **kwargs):
        in_llm_call.set()
        # Bounded so a regression fails the test instead of hanging the suite.
        assert release_llm_call.wait(timeout=30), "second request never completed"
        return {"choices": [{"message": {"content": "not-json"}}]}

    from app import llm

    monkeypatch.setattr(llm, "_request_json", _blocking_http)

    status_result = {}

    def _second_request():
        assert in_llm_call.wait(timeout=30), "reasoning call never started"
        try:
            r = admin_client.get("/repository/status", headers=headers)
            status_result["code"] = r.status_code
        finally:
            release_llm_call.set()

    worker = threading.Thread(target=_second_request, daemon=True)
    worker.start()
    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "確認したいことがあります"},
        headers=headers,
    )
    worker.join(timeout=60)
    assert not worker.is_alive()

    assert r.status_code != 500, r.text
    # Served on its own merits (404 when no repository is configured) rather
    # than blocked behind the reasoning call.
    assert status_result.get("code") is not None
    assert status_result["code"] != 500


# --- 4. Structural guard over every call site -------------------------------


def _llm_calls_inside_get_conn():
    """Every `with get_conn()` block that can reach an LLM call.

    A static check, because the dynamic tests above can only cover endpoints
    whose full preconditions a test fixture can build. This one covers all of
    them -- including code paths no test exercises yet, which is exactly where
    the original deadlock lived.
    """
    import ast
    import collections
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    calls = collections.defaultdict(set)
    by_name = collections.defaultdict(set)
    for path in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for fn in [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            key = f"{path}:{fn.name}"
            by_name[fn.name].add(key)
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Call):
                    name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                    if name:
                        calls[key].add(name)

    def reaches_llm(name, seen=None, depth=0):
        seen = seen if seen is not None else set()
        if name in seen or depth > 7:
            return None
        seen.add(name)
        if name == "generate_text":
            return ["generate_text"]
        for qualname in by_name.get(name, ()):
            for callee in sorted(calls[qualname]):
                found = reaches_llm(callee, seen, depth + 1)
                if found:
                    return [name] + found
        return None

    offenders = []
    for path in sorted(app_dir.rglob("*.py")):
        source = path.read_text()
        if "get_conn" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.With):
                continue
            if not any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", "") == "get_conn"
                for item in node.items
            ):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                if not name or name == "get_conn":
                    continue
                chain = reaches_llm(name)
                if chain:
                    rel = path.relative_to(app_dir.parent)
                    offenders.append(f"{rel}:{node.lineno} via {' -> '.join(chain)}")
    return sorted(set(offenders))


def test_no_llm_call_happens_inside_a_get_conn_block():
    offenders = _llm_calls_inside_get_conn()
    assert offenders == [], (
        "These `with get_conn()` blocks can reach an LLM call. The connection "
        "lock is process-wide and not reentrant, so this deadlocks the entire "
        "server permanently. Split the endpoint into read / reason / persist "
        "phases -- see app/db.py and routes/interview.py::run_runtime_reality_check:"
        + "".join("\n  " + o for o in offenders)
    )
