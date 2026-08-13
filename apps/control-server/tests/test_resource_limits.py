"""Issue #273: deterministic rate, LLM, storage and SDK transport limits."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


def _trace(trace_id: str, *, output: str = "ok", sdk_transport=None):
    payload = {
        "trace_id": trace_id,
        "component_id": "limited-component",
        "mode": "trace",
        "input": {"value": trace_id},
        "output": output,
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }
    if sdk_transport is not None:
        payload["sdk_transport"] = sdk_transport
    return payload


@pytest.fixture
def legacy_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "resource-limits.db"))
    monkeypatch.setenv("CONTROL_API_KEYS", "legacy-a,legacy-b")
    monkeypatch.setenv("CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE", "600")
    monkeypatch.setenv("CONTROL_LLM_DAILY_EXECUTION_LIMIT", "1000")
    monkeypatch.setenv("CONTROL_TRACE_MAX_ROWS_PER_SYSTEM", "1000000")
    monkeypatch.setenv("CONTROL_TRACE_MAX_BYTES_PER_SYSTEM", "1073741824")
    from app.resource_limits import reset_in_memory_rate_limits

    reset_in_memory_rate_limits()
    from app.main import app

    with TestClient(app) as client:
        yield client


def test_fixed_window_boundary_and_switch():
    from app.resource_limits import FixedWindowLimiter

    now = [100.0]
    limiter = FixedWindowLimiter(lambda: 2, 1, clock=lambda: now[0])
    assert limiter.allow("token") is True
    assert limiter.allow("token") is True
    assert limiter.allow("token") is False
    now[0] = 101.0
    assert limiter.allow("token") is True


def test_in_memory_limit_keys_include_database_identity(tmp_path, monkeypatch):
    from app.resource_limits import (
        ResourceLimitExceeded,
        enforce_management_rate_limit,
        reset_in_memory_rate_limits,
    )

    reset_in_memory_rate_limits()
    monkeypatch.setenv("CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "first.db"))
    enforce_management_rate_limit(1)
    with pytest.raises(ResourceLimitExceeded):
        enforce_management_rate_limit(1)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "second.db"))
    enforce_management_rate_limit(1)


def test_trace_rate_limit_is_per_legacy_token(legacy_client, monkeypatch):
    monkeypatch.setenv("CONTROL_TRACE_RATE_LIMIT_PER_SECOND", "2")
    # Pin the limiter's clock. The trace limiter is a 1-SECOND fixed window
    # keyed on `int(clock() // 1)`, so on a slow or loaded runner the third
    # request can land in the next window and be legitimately allowed -- the
    # assertion below would then fail while the limiter was working correctly.
    # `FixedWindowLimiter` exposes the clock precisely so a test can decide
    # which window it is testing; this one is about the per-token count within
    # ONE window, not about when the window rolls over.
    from app import resource_limits

    monkeypatch.setattr(resource_limits._trace_limiter, "_clock", lambda: 1_000.0)

    first = legacy_client.post(
        "/traces", json=_trace("rate-1"), headers={"X-Api-Key": "legacy-a"}
    )
    second = legacy_client.post(
        "/traces", json=_trace("rate-2"), headers={"X-Api-Key": "legacy-a"}
    )
    rejected = legacy_client.post(
        "/traces", json=_trace("rate-3"), headers={"X-Api-Key": "legacy-a"}
    )
    other_token = legacy_client.post(
        "/traces", json=_trace("rate-4"), headers={"X-Api-Key": "legacy-b"}
    )
    assert [first.status_code, second.status_code] == [201, 201]
    assert rejected.status_code == 429
    assert rejected.json()["detail"]["code"] == "trace_rate_limit_exceeded"
    assert other_token.status_code == 201


def test_management_rate_limit_is_user_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "management-limit.db"))
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "local-password")
    monkeypatch.setenv("CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE", "2")
    from app.resource_limits import reset_in_memory_rate_limits

    reset_in_memory_rate_limits()
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"username": "root", "password": "local-password"}
        )
        token = login.cookies.get("probe_session")
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/systems", headers=headers).status_code == 200
        assert client.get("/systems", headers=headers).status_code == 200
        rejected = client.get("/systems", headers=headers)
    assert rejected.status_code == 429
    assert rejected.json()["detail"]["code"] == "management_rate_limit_exceeded"


def test_llm_daily_quota_is_system_isolated_and_context_required(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "llm-limit.db"))
    monkeypatch.setenv("CONTROL_LLM_DAILY_EXECUTION_LIMIT", "1")
    from app.db import get_conn, init_db
    from app.llm import (
        LLMConfig,
        LLMQuotaExceeded,
        LLMSystemContextMissing,
        create_llm_client,
    )
    from app.resource_limits import reset_current_system_id, set_current_system_id

    init_db()
    with get_conn() as conn:
        now = time.time()
        first_system = conn.execute(
            "INSERT INTO systems (name, environment, description, created_at, updated_at) "
            "VALUES ('quota-a', '', '', ?, ?)", (now, now)
        ).lastrowid
        second_system = conn.execute(
            "INSERT INTO systems (name, environment, description, created_at, updated_at) "
            "VALUES ('quota-b', '', '', ?, ?)", (now, now)
        ).lastrowid

    client = create_llm_client(
        LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=1)
    )
    with pytest.raises(LLMSystemContextMissing):
        client.generate_text([{"role": "user", "content": "x"}])

    context = set_current_system_id(first_system)
    try:
        client.generate_text([{"role": "user", "content": "x"}])
        with pytest.raises(LLMQuotaExceeded, match="Daily LLM execution limit"):
            client.generate_text([{"role": "user", "content": "x"}])
    finally:
        reset_current_system_id(context)

    context = set_current_system_id(second_system)
    try:
        client.generate_text([{"role": "user", "content": "x"}])
    finally:
        reset_current_system_id(context)


def test_api_llm_overage_returns_explicit_429_without_persisted_fallback(
    legacy_client, monkeypatch
):
    monkeypatch.setenv("CONTROL_TRACE_RATE_LIMIT_PER_SECOND", "1000")
    monkeypatch.setenv("CONTROL_LLM_DAILY_EXECUTION_LIMIT", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from app.db import get_conn
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    trace = legacy_client.post(
        "/traces", json=_trace("llm-overage"), headers={"X-Api-Key": "legacy-a"}
    )
    assert trace.status_code == 201
    response = legacy_client.post(
        "/generation-runs",
        json={
            "component_id": "limited-component",
            "trace_id": "llm-overage",
            "objective": "improve",
        },
        headers={"X-Api-Key": "legacy-a"},
    )
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "llm_daily_limit_exceeded"
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_runs").fetchone()[0] == 0
    get_llm_client.cache_clear()


def test_trace_storage_rejects_atomically_and_projects_warning(
    legacy_client, monkeypatch
):
    monkeypatch.setenv("CONTROL_TRACE_RATE_LIMIT_PER_SECOND", "1000")
    monkeypatch.setenv("CONTROL_TRACE_MAX_ROWS_PER_SYSTEM", "1")

    def send(trace_id):
        return legacy_client.post(
            "/traces", json=_trace(trace_id), headers={"X-Api-Key": "legacy-a"}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(send, ("concurrent-a", "concurrent-b")))
    assert sorted(statuses) == [201, 429]

    state = legacy_client.get(
        "/system-state", headers={"X-Api-Key": "legacy-a"}
    )
    assert state.status_code == 200
    state_body = state.json()
    quota_items = [
        item for item in state_body["items"]
        if item["state_id"] == "runtime.trace_storage.quota_exceeded"
    ]
    assert len(quota_items) == 1
    assert quota_items[0]["evidence"]["trace_rows"] == 1
    assert any(
        item["state_id"] == "runtime.trace_storage.quota_exceeded"
        for item in state_body["notification_items"]
    )
    assert state_body["page_items"]["/components"][0]["state_id"] == (
        "runtime.trace_storage.quota_exceeded"
    )


def test_trace_storage_quota_is_system_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "storage-isolation.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "local-password")
    monkeypatch.setenv("CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE", "600")
    monkeypatch.setenv("CONTROL_TRACE_MAX_ROWS_PER_SYSTEM", "1")
    monkeypatch.setenv("CONTROL_TRACE_MAX_BYTES_PER_SYSTEM", "1073741824")
    from app.resource_limits import reset_in_memory_rate_limits

    reset_in_memory_rate_limits()
    from app.main import app

    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"username": "root", "password": "local-password"}
        )
        token = login.cookies.get("probe_session")
        auth = {"Authorization": f"Bearer {token}"}
        system_a = client.post("/systems", json={"name": "quota-a"}, headers=auth).json()["id"]
        system_b = client.post("/systems", json={"name": "quota-b"}, headers=auth).json()["id"]

        def headers(system_id):
            return {**auth, "X-Probe-System-Id": str(system_id)}

        assert client.post("/traces", json=_trace("a-1"), headers=headers(system_a)).status_code == 201
        assert client.post("/traces", json=_trace("a-2"), headers=headers(system_a)).status_code == 429
        assert client.post("/traces", json=_trace("b-1"), headers=headers(system_b)).status_code == 201
        state_b = client.get("/system-state", headers=headers(system_b)).json()
    assert not any(
        item["state_id"] == "runtime.trace_storage.quota_exceeded"
        for item in state_b["items"]
    )


def test_trace_storage_byte_limit_rejects_before_insert(legacy_client, monkeypatch):
    monkeypatch.setenv("CONTROL_TRACE_RATE_LIMIT_PER_SECOND", "1000")
    monkeypatch.setenv("CONTROL_TRACE_MAX_ROWS_PER_SYSTEM", "100")
    monkeypatch.setenv("CONTROL_TRACE_MAX_BYTES_PER_SYSTEM", "100")
    response = legacy_client.post(
        "/traces",
        json=_trace("too-large", output="x" * 500),
        headers={"X-Api-Key": "legacy-a"},
    )
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "trace_storage_byte_limit_exceeded"
    from app.db import get_conn

    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0


def test_sdk_transport_summary_is_validated_persisted_and_observable(
    legacy_client, monkeypatch
):
    monkeypatch.setenv("CONTROL_TRACE_RATE_LIMIT_PER_SECOND", "1000")
    observed = legacy_client.post(
        "/traces",
        json=_trace(
            "transport-1",
            sdk_transport={
                "dropped_count": 3,
                "failure_count": 2,
                "state": "half_open",
            },
        ),
        headers={"X-Api-Key": "legacy-a"},
    )
    assert observed.status_code == 201
    assert observed.json()["sdk_transport_observed"] is True

    status = legacy_client.get(
        "/sdk-transport/status", headers={"X-Api-Key": "legacy-a"}
    )
    assert status.status_code == 200
    assert status.json()["dropped_count"] == 3
    assert status.json()["failure_count"] == 2
    assert status.json()["latest"]["state"] == "half_open"

    invalid = legacy_client.post(
        "/traces",
        json=_trace(
            "transport-bad",
            sdk_transport={"dropped_count": 0, "failure_count": 0, "state": "bad"},
        ),
        headers={"X-Api-Key": "legacy-a"},
    )
    assert invalid.status_code == 422


def test_production_requires_explicit_resource_limits(monkeypatch):
    from app.resource_limits import validate_resource_limit_config

    for name in (
        "CONTROL_TRACE_RATE_LIMIT_PER_SECOND",
        "CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE",
        "CONTROL_LLM_DAILY_EXECUTION_LIMIT",
        "CONTROL_TRACE_MAX_ROWS_PER_SYSTEM",
        "CONTROL_TRACE_MAX_BYTES_PER_SYSTEM",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="requires explicit resource limits"):
        validate_resource_limit_config(production=True)
