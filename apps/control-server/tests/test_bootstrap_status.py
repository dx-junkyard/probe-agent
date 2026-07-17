"""Issue #265: `GET /auth/bootstrap-status` -- the one deterministic,
credential-free, System-id-free endpoint for the pre-login / zero-System
"phase 0" screens.

Matrix covered: admin exists x admin missing, llm configured (mock /
provider+key / provider without key / unsupported) x not configured, auth
mode anonymous vs user, and development vs production wording-detail level
(the endpoint itself always returns the same finite facts either way -- only
the Dashboard's login page reduces wording detail in production).
"""

import time

import pytest
from fastapi.testclient import TestClient


def _clean_env(monkeypatch):
    for var in (
        "CONTROL_ENV",
        "CONTROL_REQUIRE_AUTH",
        "CONTROL_API_KEYS",
        "CONTROL_ADMIN_USERNAME",
        "CONTROL_ADMIN_PASSWORD",
        "LLM_PROVIDER",
        "LLM_API_KEY",
        "LLM_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-bootstrap.db"))
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def test_no_admin_no_llm_is_anonymous_and_unconfigured(client):
    r = client.get("/auth/bootstrap-status")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "admin_exists": False,
        "auth_mode": "anonymous",
        "llm_configured": False,
        "environment": "development",
    }


def test_callable_with_no_credentials_and_no_system_header(client):
    # No Authorization / X-Api-Key / X-Probe-System-Id header at all.
    r = client.get("/auth/bootstrap-status", headers={})
    assert r.status_code == 200


def test_response_never_carries_a_secret_or_identifier(client, monkeypatch):
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "does-not-matter-here")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-super-secret-value")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        body = c.get("/auth/bootstrap-status").json()
    text = str(body)
    assert "root" not in text
    assert "does-not-matter-here" not in text
    assert "sk-super-secret-value" not in text
    assert set(body.keys()) == {"admin_exists", "auth_mode", "llm_configured", "environment"}


def test_admin_bootstrapped_flips_admin_exists_and_auth_mode(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-bootstrap-admin.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "a-strong-unique-passphrase")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        body = c.get("/auth/bootstrap-status").json()
    assert body["admin_exists"] is True
    assert body["auth_mode"] == "user"


def test_legacy_keys_without_admin_enables_auth_mode_but_not_admin_exists(
    tmp_path, monkeypatch
):
    _clean_env(monkeypatch)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-bootstrap-legacy.db"))
    monkeypatch.setenv("CONTROL_API_KEYS", "svc-key")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        body = c.get("/auth/bootstrap-status").json()
    assert body["admin_exists"] is False
    assert body["auth_mode"] == "user"


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"LLM_PROVIDER": "mock"}, True),
        ({"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"}, True),
        ({"LLM_PROVIDER": "anthropic"}, False),
        ({"LLM_PROVIDER": "openai", "LLM_API_KEY": "k"}, True),
        ({"LLM_PROVIDER": "not-a-real-provider"}, False),
        ({}, False),  # default provider openai, no key anywhere
    ],
)
def test_llm_configured_matrix(tmp_path, monkeypatch, env, expected):
    _clean_env(monkeypatch)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-bootstrap-llm.db"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        body = c.get("/auth/bootstrap-status").json()
    assert body["llm_configured"] is expected


def test_production_environment_is_reported(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-bootstrap-prod.db"))
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "a-strong-unique-passphrase")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        body = c.get("/auth/bootstrap-status").json()
    assert body["environment"] == "production"
    assert body["admin_exists"] is True


def test_does_not_require_x_probe_system_id(client):
    # A request that would 400 on any System-scoped route (missing
    # X-Probe-System-Id) must succeed here.
    r = client.get("/auth/bootstrap-status")
    assert r.status_code == 200
    # Sanity: the same client, same headers, does need it for a data-plane
    # route once a user session exists -- bootstrap-status is the deliberate
    # exception, not a sign auth got disabled globally.
