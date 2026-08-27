"""Issue #368: an unusable token must never render as a healthy one.

The audited bug was `status` decided from `revoked` alone, so a token whose
`expires_at` was already in the past still showed a green `active`. These
tests pin the classification against the clock, its exact boundaries, and the
separation of SDK API tokens from login sessions.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.token_status import (
    TOKEN_EXPIRING_SOON_SECONDS,
    classify_token_status,
    token_expires_in_seconds,
)

NOW = 1_800_000_000.0


# --- pure classification -----------------------------------------------------


@pytest.mark.parametrize(
    "revoked, expires_at, expected",
    [
        # No expiry column means "never expires", not "already expired".
        (False, None, "active"),
        (False, NOW + 400 * 86400, "active"),
        # Inclusive at the threshold, on both edges.
        (False, NOW + TOKEN_EXPIRING_SOON_SECONDS + 1, "active"),
        (False, NOW + TOKEN_EXPIRING_SOON_SECONDS, "expiring_soon"),
        (False, NOW + 60, "expiring_soon"),
        (False, NOW, "expired"),
        (False, NOW - 1, "expired"),
        # Revocation outranks expiry: "someone turned this off" is the fact
        # the operator needs, and it does not stop being true.
        (True, None, "revoked"),
        (True, NOW - 86400, "revoked"),
        (True, NOW + 86400, "revoked"),
    ],
)
def test_status_is_decided_against_the_clock(revoked, expires_at, expected):
    assert classify_token_status(
        revoked=revoked, expires_at=expires_at, now=NOW
    ) == expected


def test_expiry_countdown_never_goes_negative():
    assert token_expires_in_seconds(expires_at=NOW - 5000, now=NOW) == 0.0
    assert token_expires_in_seconds(expires_at=None, now=NOW) is None
    assert token_expires_in_seconds(expires_at=NOW + 120, now=NOW) == 120.0


def test_classification_is_timezone_independent():
    """POSIX timestamps carry no timezone, so a local-time change cannot move
    a token between statuses."""
    import os

    expires_at = NOW + 3600
    results = []
    original = os.environ.get("TZ")
    try:
        for tz in ("UTC", "Asia/Tokyo", "America/Los_Angeles"):
            os.environ["TZ"] = tz
            time.tzset()
            results.append(
                classify_token_status(revoked=False, expires_at=expires_at, now=NOW)
            )
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()
    assert len(set(results)) == 1


# --- API surface -------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-tokens.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "admin-password-123")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


class _AuthedClient:
    """Cookie-session client that carries the CSRF header on unsafe methods."""

    def __init__(self, client):
        self._client = client

    def _csrf(self):
        return {"X-CSRF-Token": self._client.cookies.get("probe_csrf")}

    def get(self, url, **kwargs):
        return self._client.get(url, **kwargs)

    def post(self, url, **kwargs):
        headers = {**self._csrf(), **kwargs.pop("headers", {})}
        return self._client.post(url, headers=headers, **kwargs)


@pytest.fixture
def auth(client):
    client.post(
        "/auth/login", json={"username": "admin", "password": "admin-password-123"}
    )
    return _AuthedClient(client)


def _set_expiry(db_path, token_id, expires_at, revoked=0):
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE api_tokens SET expires_at = ?, revoked = ? WHERE id = ?",
            (expires_at, revoked, token_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_expired_api_token_is_not_reported_active(auth, tmp_path):
    db_path = str(tmp_path / "probe-tokens.db")
    created = auth.post("/tokens/me", json={"name": "sdk", "expires_in_days": 30}).json()
    assert created["status"] == "active"

    _set_expiry(db_path, created["id"], time.time() - 3600)
    listed = {t["id"]: t for t in auth.get("/tokens/me").json()}
    assert listed[created["id"]]["status"] == "expired"
    assert listed[created["id"]]["expires_in_seconds"] == 0.0


def test_revoked_and_expired_reports_revoked(auth, tmp_path):
    db_path = str(tmp_path / "probe-tokens.db")
    created = auth.post("/tokens/me", json={"name": "sdk", "expires_in_days": 1}).json()
    _set_expiry(db_path, created["id"], time.time() - 3600, revoked=1)
    listed = {t["id"]: t for t in auth.get("/tokens/me").json()}
    assert listed[created["id"]]["status"] == "revoked"


def test_token_without_expiry_stays_active(auth):
    created = auth.post("/tokens/me", json={"name": "sdk"}).json()
    assert created["expires_at"] is None
    assert created["status"] == "active"
    assert created["expires_in_seconds"] is None


def test_expiring_soon_is_reported_before_expiry(auth, tmp_path):
    db_path = str(tmp_path / "probe-tokens.db")
    created = auth.post("/tokens/me", json={"name": "sdk", "expires_in_days": 30}).json()
    _set_expiry(db_path, created["id"], time.time() + 3600)
    listed = {t["id"]: t for t in auth.get("/tokens/me").json()}
    assert listed[created["id"]]["status"] == "expiring_soon"
    # Still usable -- the warning must not read as "already broken".
    assert listed[created["id"]]["expires_in_seconds"] > 0


def test_login_session_is_a_separate_kind_from_the_sdk_credential(auth):
    """The SDK flow must be able to exclude the login session deterministically."""
    auth.post("/tokens/me", json={"name": "sdk"})
    tokens = auth.get("/tokens/me").json()
    kinds = {t["kind"] for t in tokens}
    assert "session" in kinds and "api" in kinds
    sessions = [t for t in tokens if t["kind"] == "session"]
    assert sessions and all(t["name"] == "login session" for t in sessions)


def test_revoking_a_token_flips_its_reported_status(auth):
    created = auth.post("/tokens/me", json={"name": "sdk"}).json()
    revoked = auth.post(f"/tokens/me/{created['id']}/revoke").json()
    assert revoked["status"] == "revoked"


def test_admin_listing_classifies_the_same_way(auth, tmp_path):
    db_path = str(tmp_path / "probe-tokens.db")
    created = auth.post("/tokens/me", json={"name": "sdk", "expires_in_days": 30}).json()
    _set_expiry(db_path, created["id"], time.time() - 1)
    admin_view = {t["id"]: t for t in auth.get("/tokens").json()}
    assert admin_view[created["id"]]["status"] == "expired"
