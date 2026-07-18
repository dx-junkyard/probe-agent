"""Issue #274: browser cookie sessions, double-submit CSRF, and CSP contracts."""

import time
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "cookie-auth.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "local-password")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.delenv("CONTROL_ENV", raising=False)
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(client):
    response = client.post(
        "/auth/login", json={"username": "root", "password": "local-password"}
    )
    assert response.status_code == 200, response.text
    return response


def _csrf(client):
    value = client.cookies.get("probe_csrf")
    assert value
    return {"X-CSRF-Token": value}


def test_login_sets_host_cookies_without_exposing_session(client):
    response = _login(client)
    body = response.json()
    assert set(body) == {"expires_at"}
    assert client.cookies.get("probe_session")
    assert client.cookies.get("probe_session") not in response.text
    assert response.headers["cache-control"] == "no-store"

    cookies = response.headers.get_list("set-cookie")
    session = next(value for value in cookies if value.startswith("probe_session="))
    csrf = next(value for value in cookies if value.startswith("probe_csrf="))
    assert "Path=/" in session and "SameSite=lax" in session
    assert "HttpOnly" in session
    assert "Domain=" not in session
    assert "Secure" not in session
    assert "Path=/" in csrf and "SameSite=lax" in csrf
    assert "HttpOnly" not in csrf


def test_production_cookies_are_secure(client, monkeypatch):
    monkeypatch.setattr("app.routes.auth.is_production", lambda: True)
    response = _login(client)
    cookies = response.headers.get_list("set-cookie")
    assert all("Secure" in value for value in cookies)


def test_cookie_auth_safe_method_and_transport(client):
    _login(client)
    response = client.get("/auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["user"]["username"] == "root"
    assert response.json()["transport"] == "cookie"


def test_cookie_unsafe_method_requires_matching_csrf(client):
    _login(client)
    csrf = client.cookies.get("probe_csrf")
    client.cookies.delete("probe_csrf")

    missing = client.post("/tokens/me", json={"name": "missing"})
    assert missing.status_code == 403
    assert missing.json()["detail"]["code"] == "csrf_validation_failed"

    client.cookies.set("probe_csrf", csrf)
    mismatch = client.post(
        "/tokens/me",
        json={"name": "mismatch"},
        headers={"X-CSRF-Token": "not-the-cookie"},
    )
    assert mismatch.status_code == 403

    accepted = client.post("/tokens/me", json={"name": "accepted"}, headers=_csrf(client))
    assert accepted.status_code == 201, accepted.text


def test_explicit_header_takes_precedence_and_is_csrf_exempt(client):
    _login(client)
    session = client.cookies.get("probe_session")
    client.cookies.delete("probe_csrf")

    accepted = client.post(
        "/tokens/me",
        json={"name": "header-session"},
        headers={"Authorization": f"Bearer {session}"},
    )
    assert accepted.status_code == 201, accepted.text

    # A present but invalid explicit credential never falls back to a valid
    # browser session.
    rejected = client.get(
        "/auth/me", headers={"Authorization": "Bearer definitely-invalid"}
    )
    assert rejected.status_code == 401


def test_x_api_key_is_csrf_exempt_and_cookie_rejects_sdk_token(client):
    _login(client)
    created = client.post("/tokens/me", json={"name": "sdk"}, headers=_csrf(client))
    assert created.status_code == 201
    sdk_token = created.json()["token"]

    explicit = client.get("/auth/me", headers={"X-Api-Key": sdk_token})
    assert explicit.status_code == 200
    assert explicit.json()["transport"] == "x_api_key"

    client.cookies.set("probe_session", sdk_token)
    rejected = client.get("/auth/me")
    assert rejected.status_code == 401


@pytest.mark.parametrize("change", ["revoke", "expire", "deactivate"])
def test_cookie_session_rejects_invalid_server_state(client, change):
    _login(client)
    session = client.cookies.get("probe_session")
    from app.db import get_conn
    from app.security import hash_token

    with get_conn() as conn:
        if change == "revoke":
            conn.execute(
                "UPDATE api_tokens SET revoked = 1 WHERE token_hash = ?",
                (hash_token(session),),
            )
        elif change == "expire":
            conn.execute(
                "UPDATE api_tokens SET expires_at = ? WHERE token_hash = ?",
                (time.time() - 1, hash_token(session)),
            )
        else:
            conn.execute("UPDATE users SET is_active = 0 WHERE username = 'root'")

    assert client.get("/auth/me").status_code == 401


def test_logout_requires_csrf_revokes_session_and_clears_cookies(client):
    _login(client)
    session = client.cookies.get("probe_session")
    assert client.post("/auth/logout").status_code == 403

    response = client.post("/auth/logout", headers=_csrf(client))
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    cookies = response.headers.get_list("set-cookie")
    assert any("probe_session=" in value and "Max-Age=0" in value for value in cookies)
    assert any("probe_csrf=" in value and "Max-Age=0" in value for value in cookies)
    assert client.cookies.get("probe_session") is None
    assert client.cookies.get("probe_csrf") is None

    client.cookies.set("probe_session", session)
    assert client.get("/auth/me").status_code == 401


def test_dashboard_has_no_session_storage_or_authorization_header():
    configured_root = os.getenv("PROBE_REPO_ROOT")
    if not configured_root and len(Path(__file__).resolve().parents) <= 3:
        pytest.skip("repository root is not mounted in this test container")
    root = Path(configured_root) if configured_root else Path(__file__).resolve().parents[3]
    if not (root / "apps" / "dashboard").exists():
        pytest.skip("repository root is not mounted in this test container")
    dashboard = root / "apps" / "dashboard" / "src"
    sources = "\n".join(
        path.read_text()
        for path in dashboard.rglob("*.ts*")
        if "__tests__" not in path.parts
    )
    assert "probe_session" + "_token" not in sources
    assert "setSession" + "Token" not in sources
    assert "getSession" + "Token" not in sources
    assert 'h["Authorization"]' not in sources
    client_source = (dashboard / "api" / "client.ts").read_text()
    assert 'credentials: "include"' in client_source
    assert 'h["X-CSRF-Token"]' in client_source


def test_caddy_csp_is_restrictive_and_applies_at_site_scope():
    configured_root = os.getenv("PROBE_REPO_ROOT")
    if not configured_root and len(Path(__file__).resolve().parents) <= 3:
        pytest.skip("repository root is not mounted in this test container")
    root = Path(configured_root) if configured_root else Path(__file__).resolve().parents[3]
    if not (root / "deploy" / "caddy" / "Caddyfile").exists():
        pytest.skip("repository root is not mounted in this test container")
    caddy = (root / "deploy" / "caddy" / "Caddyfile").read_text()
    assert "Content-Security-Policy" in caddy
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ):
        assert directive in caddy
    assert caddy.index("header {") < caddy.index("reverse_proxy")
