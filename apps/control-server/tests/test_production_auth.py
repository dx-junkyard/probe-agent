"""Issue #225: strict fail-closed production auth.

Startup-matrix tests call `app.db.init_db()` directly against a fresh SQLite
file (no FastAPI app/lifespan involved) so each RuntimeError path can be
asserted in isolation. API-matrix tests spin up a full `TestClient(app)` in
production mode and exercise the SDK-api-token-vs-session-token boundary
enforced by `require_user`/`require_admin`.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient


# --- Startup matrix -------------------------------------------------------


def _clean_auth_env(monkeypatch):
    monkeypatch.delenv("CONTROL_ENV", raising=False)
    monkeypatch.delenv("CONTROL_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.delenv("CONTROL_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("CONTROL_ADMIN_PASSWORD", raising=False)


def _init_db(tmp_path, monkeypatch, name="probe-prod-auth.db"):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / name))
    from app import db  # noqa: WPS433

    db.init_db()
    return db


def test_production_no_admin_env_no_users_fails(tmp_path, monkeypatch):
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p1.db"))
    from app import db  # noqa: WPS433

    with pytest.raises(RuntimeError, match="active admin"):
        db.init_db()


def test_production_with_legacy_keys_fails_even_with_good_admin(tmp_path, monkeypatch):
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "a-strong-unique-passphrase")
    monkeypatch.setenv("CONTROL_API_KEYS", "some-legacy-key")
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p2.db"))
    from app import db  # noqa: WPS433

    with pytest.raises(RuntimeError, match="CONTROL_API_KEYS"):
        db.init_db()


@pytest.mark.parametrize(
    "password",
    [
        "change-me",  # denylist, exact
        "PASSWORD",  # denylist, case-insensitive
        "password",  # denylist
        "fifteencharspwd",  # exactly 15 chars: below the 16-char minimum
        "root",  # equals username below (case-insensitive)
    ],
)
def test_production_rejects_weak_or_sample_password(tmp_path, monkeypatch, password):
    assert len("fifteencharspwd") == 15  # sanity: this case tests min-length only
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", password)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p3.db"))
    from app import db  # noqa: WPS433

    with pytest.raises(RuntimeError, match="CONTROL_ADMIN_PASSWORD"):
        db.init_db()


def test_production_password_equal_to_username_case_insensitive_fails(
    tmp_path, monkeypatch
):
    # Both >= 16 chars and not on the sample-value denylist, so only the
    # username-equality check (case-insensitive) is exercised.
    username = "myverylongadminname"
    password = "MyVeryLongAdminName"
    assert len(username) >= 16 and len(password) >= 16
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", username)
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", password)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p3b.db"))
    from app import db  # noqa: WPS433

    with pytest.raises(RuntimeError, match="CONTROL_ADMIN_PASSWORD"):
        db.init_db()


def test_production_valid_password_starts_and_audits_once(tmp_path, monkeypatch):
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "a-strong-unique-passphrase")
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p4.db"))
    from app import db  # noqa: WPS433

    db.init_db()

    with db.get_conn() as conn:
        user = conn.execute(
            "SELECT role, is_active FROM users WHERE username = 'root'"
        ).fetchone()
        assert user is not None
        assert user["role"] == "admin"
        assert user["is_active"] == 1

        events = conn.execute(
            "SELECT event_type, username, detail FROM auth_audit_events"
        ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "admin_bootstrapped"
    assert events[0]["username"] == "root"
    detail = json.loads(events[0]["detail"])
    assert detail == {"source": "env_bootstrap"}
    # Never leak the password or a hash into the audit trail.
    assert "a-strong-unique-passphrase" not in (events[0]["detail"] or "")

    # Re-running init_db (e.g. a restart) must not duplicate the bootstrap
    # audit event -- the user row already exists.
    db.init_db()
    with db.get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM auth_audit_events WHERE event_type = 'admin_bootstrapped'"
        ).fetchone()[0]
    assert count == 1


def test_production_require_auth_false_fails(tmp_path, monkeypatch):
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "a-strong-unique-passphrase")
    monkeypatch.setenv("CONTROL_REQUIRE_AUTH", "false")
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p5.db"))
    from app import db  # noqa: WPS433

    with pytest.raises(RuntimeError, match="CONTROL_REQUIRE_AUTH"):
        db.init_db()


def test_production_deactivated_admin_fails_on_restart(tmp_path, monkeypatch):
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "a-strong-unique-passphrase")
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p6.db"))
    from app import db  # noqa: WPS433

    db.init_db()
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE username = 'root'")

    with pytest.raises(RuntimeError, match="active admin"):
        db.init_db()


def test_development_default_starts_with_warning(tmp_path, monkeypatch, caplog):
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p7.db"))
    from app import db  # noqa: WPS433

    with caplog.at_level("WARNING", logger="app.db"):
        db.init_db()
    assert any("without authentication" in msg for msg in caplog.messages)


def test_unknown_control_env_fails(tmp_path, monkeypatch):
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("CONTROL_ENV", "staging")
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "p8.db"))
    from app import db  # noqa: WPS433

    with pytest.raises(RuntimeError, match="CONTROL_ENV"):
        db.init_db()


# --- API matrix ------------------------------------------------------------


def _trace(component_id="summarizer", trace_id="t1"):
    return {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": [], "kwargs": {}},
        "output": "'x'",
        "error": None,
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def prod_client(tmp_path, monkeypatch):
    _clean_auth_env(monkeypatch)
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-prod-api.db"))
    monkeypatch.setenv("CONTROL_ENV", "production")
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "a-strong-unique-passphrase")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="a-strong-unique-passphrase"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_unauthenticated_request_is_401(prod_client):
    assert prod_client.get("/components").status_code == 401
    assert prod_client.get("/users").status_code == 401


def test_api_token_rejected_by_management_routes_but_works_for_data_plane(
    prod_client,
):
    session_token = _login(prod_client)

    r = prod_client.post(
        "/tokens/me", json={"name": "sdk"}, headers=_bearer(session_token)
    )
    assert r.status_code == 201, r.text
    api_raw = r.json()["token"]

    # Management routes reject the SDK token outright.
    for method, path in [
        ("get", "/users"),
        ("get", "/tokens"),
        ("get", "/github/connections"),
    ]:
        r = getattr(prod_client, method)(path, headers=_bearer(api_raw))
        assert r.status_code == 403, f"{method} {path}: {r.text}"
        assert "SDK API token" in r.json()["detail"]

    # Same token as X-Api-Key also gets rejected on a management route.
    r = prod_client.get("/users", headers={"X-Api-Key": api_raw})
    assert r.status_code == 403

    # Data-plane routes (what the SDK actually calls) keep working.
    r = prod_client.post("/traces", json=_trace(), headers={"X-Api-Key": api_raw})
    assert r.status_code == 201, r.text
    r = prod_client.get("/components", headers=_bearer(api_raw))
    assert r.status_code == 200


def test_session_token_works_for_management_routes(prod_client):
    session_token = _login(prod_client)
    r = prod_client.get("/users", headers=_bearer(session_token))
    assert r.status_code == 200
    usernames = {u["username"] for u in r.json()}
    assert "root" in usernames


def test_legacy_key_rejected_in_production_even_if_set_at_runtime(
    prod_client, monkeypatch
):
    # Startup already forbids CONTROL_API_KEYS; simulate it being set only
    # after boot (e.g. an operator mistake) to prove the runtime path
    # (auth._legacy_keys / auth_enabled) also refuses it, not just the
    # startup check.
    monkeypatch.setenv("CONTROL_API_KEYS", "some-legacy-key")
    r = prod_client.post(
        "/traces", json=_trace(), headers={"X-Api-Key": "some-legacy-key"}
    )
    assert r.status_code == 401
