"""Tests for GitHub App auth broker and connection management (Issue #216).

Covers: App JWT signing, Installation Access Token exchange (success/HTTP
error), that no secret (token/JWT/private key) ever leaks into an exception
message or the database, GitHub connection CRUD with system isolation and
owner/admin authorization, the verify action's success/failure paths, and
fail-closed behavior when the GitHub App is not configured.
"""

import json
import sqlite3
import urllib.error

import pytest
from fastapi.testclient import TestClient


# --- App-level fixtures ------------------------------------------------------


@pytest.fixture
def rsa_private_key_path(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "github-app-key.pem"
    path.write_bytes(pem)
    return str(path)


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "github-app-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("GITHUB_APP_ALLOWED_ORGANIZATION", raising=False)
    monkeypatch.delenv("GITHUB_API_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_WEB_BASE_URL", raising=False)
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name="acme-system"):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    system = r.json()
    # Existing Issue #216 tests exercise connection mechanics.  Give each
    # test System two explicit, active Issue #222 assignments; authorization
    # negative cases are covered separately below.
    from app.db import get_conn

    now = "2026-01-01T00:00:00+00:00"
    with get_conn() as conn:
        for installation_id in (1, 7):
            conn.execute(
                """
                INSERT OR IGNORE INTO github_installations
                    (installation_id, github_account_login, github_account_type, status,
                     verified_at, created_at, updated_at)
                VALUES (?, 'acme', 'Organization', 'active', ?, ?, ?)
                """,
                (installation_id, now, now, now),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO github_installation_systems
                    (installation_id, system_id, created_at)
                VALUES (?, ?, ?)
                """,
                (installation_id, system["id"], now),
            )
    return system


def _create_user(client, admin_token, username, password="pw123456", role="user"):
    r = client.post(
        "/users",
        json={"username": username, "password": password, "role": role},
        headers=_bearer(admin_token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _configure_app(monkeypatch, key_path, app_id="123"):
    monkeypatch.setenv("GITHUB_APP_ID", app_id)
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", key_path)


# --- generate_app_jwt / github_app_configured --------------------------------


class TestAppConfiguration:
    def test_not_configured_without_app_id(self, tmp_path, monkeypatch, rsa_private_key_path):
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", rsa_private_key_path)
        from app.github_app import github_app_configured

        assert github_app_configured() is False

    def test_not_configured_with_missing_key_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_APP_ID", "123")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(tmp_path / "missing.pem"))
        from app.github_app import github_app_configured

        assert github_app_configured() is False

    def test_configured_when_both_present(self, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path)
        from app.github_app import github_app_configured

        assert github_app_configured() is True

    def test_not_configured_with_empty_key_file(self, tmp_path, monkeypatch):
        # Issue #224: docker-compose.prod.yml mounts the Compose secret from
        # /dev/null by default when the publish workflow is not enabled, so
        # an empty (but existing) key file must read as "not configured",
        # not crash later inside JWT signing.
        empty_key = tmp_path / "empty.pem"
        empty_key.write_bytes(b"")
        monkeypatch.setenv("GITHUB_APP_ID", "123")
        monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(empty_key))
        from app.github_app import github_app_configured

        assert github_app_configured() is False

    def test_generate_app_jwt_fails_closed_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
        from app.github_app import GitHubAppError, generate_app_jwt

        with pytest.raises(GitHubAppError):
            generate_app_jwt()

    def test_generate_app_jwt_signs_rs256(self, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path, app_id="999")
        import jwt as pyjwt

        from app.github_app import generate_app_jwt

        token = generate_app_jwt()
        assert isinstance(token, str)
        header = pyjwt.get_unverified_header(token)
        assert header["alg"] == "RS256"
        payload = pyjwt.decode(token, options={"verify_signature": False})
        assert payload["iss"] == "999"
        assert payload["exp"] > payload["iat"]


# --- create_installation_token -----------------------------------------------


class TestCreateInstallationToken:
    @pytest.mark.parametrize(
        "url",
        [
            "http://api.github.com",
            "https://localhost",
            "https://127.0.0.1",
            "https://169.254.169.254",
            "https://10.0.0.1",
            "file:///etc/passwd",
            "ssh://git@github.com/acme/repo.git",
        ],
    )
    def test_rejects_non_github_api_destinations(self, monkeypatch, rsa_private_key_path, url):
        _configure_app(monkeypatch, rsa_private_key_path)
        from app.github_app import GitHubAppError, create_installation_token

        with pytest.raises(GitHubAppError):
            create_installation_token(42, api_base_url=url)

    def test_cross_host_redirect_is_rejected(self):
        from app.github_app import GitHubAppError, _SameHostRedirectHandler

        handler = _SameHostRedirectHandler()
        request = __import__("urllib.request").request.Request(
            "https://api.github.com/app/installations/1/access_tokens",
            headers={"Authorization": "Bearer secret"},
        )
        with pytest.raises(GitHubAppError):
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://127.0.0.1/steal"
            )

    def test_success(self, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path)
        from app.github_app import create_installation_token

        def fake_urlopen(request, timeout=30):
            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return json.dumps(
                        {"token": "ghs_abcdefghijklmnopqrstuvwxyz012345", "expires_at": "2099-01-01T00:00:00Z"}
                    ).encode("utf-8")

            assert request.get_header("Authorization").startswith("Bearer ")
            return FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        result = create_installation_token(42)
        assert result.token == "ghs_abcdefghijklmnopqrstuvwxyz012345"
        assert result.expires_at == "2099-01-01T00:00:00Z"

    def test_http_error_does_not_leak_jwt_or_token(self, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path)
        from app.github_app import GitHubAppError, create_installation_token, generate_app_jwt

        issued_jwt = generate_app_jwt()

        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("url", 401, "Unauthorized", {}, None)

            def read(self):
                return f'{{"message": "Bad credentials {issued_jwt}"}}'.encode("utf-8")

        def fake_urlopen(request, timeout=30):
            raise FakeHTTPError()

        # Re-issue the same JWT deterministically is not guaranteed (iat may
        # differ by a second), so patch generate_app_jwt to return a fixed
        # value we can assert is redacted.
        monkeypatch.setattr("app.github_app.generate_app_jwt", lambda: issued_jwt)
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(GitHubAppError) as excinfo:
            create_installation_token(42)
        message = str(excinfo.value)
        assert issued_jwt not in message
        assert "***" in message

    def test_invalid_installation_id_rejected(self, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path)
        from app.github_app import GitHubAppError, create_installation_token

        with pytest.raises(GitHubAppError):
            create_installation_token("42; rm -rf")  # type: ignore[arg-type]

    def test_fails_closed_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GITHUB_APP_ID", raising=False)
        monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
        from app.github_app import GitHubAppError, create_installation_token

        with pytest.raises(GitHubAppError):
            create_installation_token(1)


class TestGetRepository:
    def test_rejects_invalid_owner_repo(self, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path)
        from app.github_app import GitHubAppError, get_repository

        with pytest.raises(GitHubAppError):
            get_repository("../etc", "passwd", "tok")
        with pytest.raises(GitHubAppError):
            get_repository("acme", "widgets --evil", "tok")
        with pytest.raises(GitHubAppError):
            get_repository("", "widgets", "tok")

    def test_success_returns_default_branch(self, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path)
        from app.github_app import get_repository

        def fake_urlopen(request, timeout=30):
            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return json.dumps({"default_branch": "main"}).encode("utf-8")

            return FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        data = get_repository("acme", "widgets", "tok")
        assert data["default_branch"] == "main"


# --- GitHub connection persistence and API -----------------------------------


class TestConnectionAPI:
    @pytest.mark.parametrize("field", ["api_base_url", "web_base_url"])
    def test_create_rejects_connection_url_override(self, admin_client, field):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        payload = {"owner": "acme", "repo": "widgets", "installation_id": 1, field: "https://evil.example"}
        response = admin_client.post(
            "/github/connections", json=payload, headers=_headers(token, system["id"])
        )
        assert response.status_code == 422

    def test_app_status_reports_configured(self, admin_client, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path, app_id="555")
        token = _login(admin_client)
        r = admin_client.get("/github/app-status", headers=_bearer(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["configured"] is True
        assert body["app_id"] == "555"
        assert body["api_base_url"] == "https://api.github.com"
        assert body["web_base_url"] == "https://github.com"

    def test_app_status_not_configured(self, admin_client):
        token = _login(admin_client)
        r = admin_client.get("/github/app-status", headers=_bearer(token))
        assert r.status_code == 200, r.text
        assert r.json()["configured"] is False

    def test_create_list_get_connection(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])

        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h,
        )
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["status"] == "pending"
        assert created["clone_url"] == "https://github.com/acme/widgets.git"
        assert "token" not in created
        assert "installation_token" not in created

        r = admin_client.get("/github/connections", headers=h)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1

        r = admin_client.get(f"/github/connections/{created['id']}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["owner"] == "acme"

    def test_duplicate_owner_repo_conflicts(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        payload = {"owner": "acme", "repo": "widgets", "installation_id": 1}
        r = admin_client.post("/github/connections", json=payload, headers=h)
        assert r.status_code == 201, r.text
        r = admin_client.post("/github/connections", json=payload, headers=h)
        assert r.status_code == 409, r.text

    def test_reconnect_after_disconnect_creates_new_row(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        payload = {"owner": "acme", "repo": "widgets", "installation_id": 1}
        r = admin_client.post("/github/connections", json=payload, headers=h)
        first_id = r.json()["id"]

        r = admin_client.delete(f"/github/connections/{first_id}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "disconnected"

        r = admin_client.post("/github/connections", json=payload, headers=h)
        assert r.status_code == 201, r.text
        assert r.json()["id"] != first_id
        assert r.json()["status"] == "pending"

        r = admin_client.get("/github/connections", headers=h)
        assert len(r.json()) == 2

    @pytest.mark.parametrize(
        "owner,repo",
        [
            ("../etc", "widgets"),
            ("acme", "../../etc"),
            ("acme", "widgets --evil"),
            ("", "widgets"),
            ("acme", ""),
        ],
    )
    def test_invalid_owner_repo_rejected(self, admin_client, owner, repo):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": owner, "repo": repo, "installation_id": 1},
            headers=h,
        )
        assert r.status_code == 422, r.text

    def test_other_system_cannot_see_connection(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "system-a")
        system_b = _create_system(admin_client, token, "system-b")
        h_a = _headers(token, system_a["id"])
        h_b = _headers(token, system_b["id"])

        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h_a,
        )
        connection_id = r.json()["id"]

        r = admin_client.get(f"/github/connections/{connection_id}", headers=h_b)
        assert r.status_code == 403 or r.status_code == 404

        r = admin_client.get("/github/connections", headers=h_b)
        assert r.status_code == 200
        assert r.json() == []

    def test_non_owner_forbidden_owner_and_admin_allowed(self, admin_client):
        admin_token = _login(admin_client)
        _create_user(admin_client, admin_token, "owner-user")
        _create_user(admin_client, admin_token, "other-user")

        owner_token = _login(admin_client, "owner-user", "pw123456")
        other_token = _login(admin_client, "other-user", "pw123456")

        system = _create_system(admin_client, owner_token, "owned-system")
        h_owner = _headers(owner_token, system["id"])
        h_other = _headers(other_token, system["id"])
        h_admin = _headers(admin_token, system["id"])

        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h_owner,
        )
        assert r.status_code == 201, r.text

        r = admin_client.get("/github/connections", headers=h_other)
        assert r.status_code == 403, r.text

        r = admin_client.get("/github/connections", headers=h_admin)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1

    def test_verify_fails_closed_when_not_configured(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h,
        )
        connection_id = r.json()["id"]

        r = admin_client.post(f"/github/connections/{connection_id}/verify", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "error"
        assert body["last_error"]
        assert "GITHUB_APP" in body["last_error"] or "not configured" in body["last_error"]

    def test_verify_success_sets_default_branch_and_status(
        self, admin_client, monkeypatch, rsa_private_key_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 7},
            headers=h,
        )
        connection_id = r.json()["id"]

        def fake_urlopen(request, timeout=30):
            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    if "access_tokens" in request.full_url:
                        return json.dumps(
                            {"token": "ghs_verifytoken0123456789", "expires_at": "2099-01-01T00:00:00Z"}
                        ).encode("utf-8")
                    return json.dumps({"default_branch": "trunk"}).encode("utf-8")

            return FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        r = admin_client.post(f"/github/connections/{connection_id}/verify", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "connected"
        assert body["default_branch"] == "trunk"
        assert body["last_error"] is None
        assert "ghs_verifytoken0123456789" not in json.dumps(body)

    def test_verify_failure_sets_error_status_without_secret(
        self, admin_client, monkeypatch, rsa_private_key_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 7},
            headers=h,
        )
        connection_id = r.json()["id"]

        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("url", 404, "Not Found", {}, None)

            def read(self):
                return b'{"message": "Not Found"}'

        def fake_urlopen(request, timeout=30):
            raise FakeHTTPError()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        r = admin_client.post(f"/github/connections/{connection_id}/verify", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "error"
        assert "404" in body["last_error"]
        assert "ghs_" not in body["last_error"]

    def test_delete_soft_deletes_connection(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 1},
            headers=h,
        )
        connection_id = r.json()["id"]

        r = admin_client.delete(f"/github/connections/{connection_id}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "disconnected"

        r = admin_client.get(f"/github/connections/{connection_id}", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "disconnected"


class TestTokenNeverPersisted:
    def test_no_token_or_key_column_in_github_connections(
        self, admin_client, monkeypatch, rsa_private_key_path, tmp_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 7},
            headers=h,
        )
        connection_id = r.json()["id"]

        def fake_urlopen(request, timeout=30):
            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    if "access_tokens" in request.full_url:
                        return json.dumps(
                            {"token": "ghs_supersecrettoken0123456789", "expires_at": "2099-01-01T00:00:00Z"}
                        ).encode("utf-8")
                    return json.dumps({"default_branch": "main"}).encode("utf-8")

            return FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        r = admin_client.post(f"/github/connections/{connection_id}/verify", headers=h)
        assert r.status_code == 200, r.text

        import os

        db_path = os.environ["PROBE_DB_PATH"]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cols = [c[1] for c in conn.execute("PRAGMA table_info(github_connections)")]
        assert "token" not in [c.lower() for c in cols]
        assert "installation_token" not in [c.lower() for c in cols]
        assert "private_key" not in [c.lower() for c in cols]

        rows = conn.execute("SELECT * FROM github_connections").fetchall()
        assert rows
        for row in rows:
            for value in tuple(row):
                if isinstance(value, str):
                    assert "ghs_supersecrettoken0123456789" not in value
        conn.close()


# --- Installation repositories listing (Issue #216, sub-task 4) -------------


class TestInstallationRepositoriesAPI:
    def _fake_urlopen(self, *, token="ghs_faketoken0123456789"):
        def fake_urlopen(request, timeout=30):
            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    if "access_tokens" in request.full_url:
                        return json.dumps(
                            {"token": token, "expires_at": "2099-01-01T00:00:00Z"}
                        ).encode("utf-8")
                    if request.full_url.endswith("/installation/repositories"):
                        return json.dumps(
                            {
                                "repositories": [
                                    {
                                        "owner": {"login": "acme"},
                                        "name": "widgets",
                                        "default_branch": "main",
                                        "private": True,
                                    },
                                    {
                                        "owner": {"login": "acme"},
                                        "name": "gadgets",
                                        "default_branch": "trunk",
                                        "private": False,
                                    },
                                ]
                            }
                        ).encode("utf-8")
                    return json.dumps({}).encode("utf-8")

            return FakeResponse()

        return fake_urlopen

    def test_lists_repositories_without_leaking_token(
        self, admin_client, monkeypatch, rsa_private_key_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])

        monkeypatch.setattr("urllib.request.urlopen", self._fake_urlopen())
        r = admin_client.get("/github/installations/1/repositories", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == [
            {"owner": "acme", "name": "widgets", "default_branch": "main", "private": True},
            {"owner": "acme", "name": "gadgets", "default_branch": "trunk", "private": False},
        ]
        assert "ghs_faketoken0123456789" not in r.text

    def test_fails_closed_when_app_not_configured(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        h = _headers(token, system["id"])

        r = admin_client.get("/github/installations/1/repositories", headers=h)
        assert r.status_code == 502, r.text
        assert "ghs_" not in r.text

    def test_non_owner_forbidden(self, admin_client, monkeypatch, rsa_private_key_path):
        _configure_app(monkeypatch, rsa_private_key_path)
        admin_token = _login(admin_client)
        system = _create_system(admin_client, admin_token)
        other_user = _create_user(admin_client, admin_token, "bob")
        other_token = _login(admin_client, "bob", "pw123456")

        r = admin_client.get(
            "/github/installations/1/repositories",
            headers=_headers(other_token, system["id"]),
        )
        assert r.status_code == 403, r.text

    def test_requires_authentication(self, admin_client):
        r = admin_client.get("/github/installations/1/repositories")
        assert r.status_code in (401, 403)


# --- Installation registration / System assignment (Issue #222) ------------


class TestInstallationAuthorization:
    def _installation_response(self, login="acme", account_type="Organization"):
        def fake_urlopen(request, timeout=30):
            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    if "/app/installations/" in request.full_url:
                        return json.dumps(
                            {"id": 42, "account": {"login": login, "type": account_type}}
                        ).encode("utf-8")
                    raise AssertionError(f"unexpected token/API request: {request.full_url}")

            return FakeResponse()

        return fake_urlopen

    def test_only_admin_can_register_and_assign_installations(
        self, admin_client, monkeypatch, rsa_private_key_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        monkeypatch.setenv("GITHUB_APP_ALLOWED_ORGANIZATION", "acme")
        monkeypatch.setattr("urllib.request.urlopen", self._installation_response())
        admin_token = _login(admin_client)
        other = _create_user(admin_client, admin_token, "installation-user")
        other_token = _login(admin_client, "installation-user", "pw123456")
        system = _create_system(admin_client, other_token)

        r = admin_client.post(
            "/github/installations", json={"installation_id": 42}, headers=_bearer(other_token)
        )
        assert r.status_code == 403, r.text

        r = admin_client.post(
            "/github/installations", json={"installation_id": 42}, headers=_bearer(admin_token)
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["github_account_login"] == "acme"
        assert body["github_account_type"] == "Organization"
        assert body["status"] == "active"
        assert body["registered_by_user_id"] is not None

        r = admin_client.post(
            f"/github/installations/42/systems/{system['id']}", headers=_bearer(other_token)
        )
        assert r.status_code == 403, r.text
        r = admin_client.post(
            f"/github/installations/42/systems/{system['id']}", headers=_bearer(admin_token)
        )
        assert r.status_code == 200, r.text
        assert r.json()["assigned_system_ids"] == [system["id"]]

    def test_registration_rejects_non_configured_organization(
        self, admin_client, monkeypatch, rsa_private_key_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        monkeypatch.setenv("GITHUB_APP_ALLOWED_ORGANIZATION", "acme")
        monkeypatch.setattr(
            "urllib.request.urlopen", self._installation_response(login="other-org")
        )
        token = _login(admin_client)
        r = admin_client.post(
            "/github/installations", json={"installation_id": 42}, headers=_bearer(token)
        )
        assert r.status_code == 422, r.text
        assert "configured Organization" in r.json()["detail"]

    def test_unregistered_or_cross_system_installation_cannot_issue_token(
        self, admin_client, monkeypatch, rsa_private_key_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        monkeypatch.setenv("GITHUB_APP_ALLOWED_ORGANIZATION", "acme")
        monkeypatch.setattr("urllib.request.urlopen", self._installation_response())
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "installation-system-a")
        system_b = _create_system(admin_client, token, "installation-system-b")
        h_a = _headers(token, system_a["id"])
        h_b = _headers(token, system_b["id"])

        # An arbitrary, unregistered ID is rejected before any token request.
        r = admin_client.get("/github/installations/999/repositories", headers=h_a)
        assert r.status_code == 403, r.text
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 999},
            headers=h_a,
        )
        assert r.status_code == 403, r.text

        assert admin_client.post(
            "/github/installations", json={"installation_id": 42}, headers=_bearer(token)
        ).status_code == 201
        assert admin_client.post(
            f"/github/installations/42/systems/{system_a['id']}", headers=_bearer(token)
        ).status_code == 200

        # Assignment to System A does not grant System B either repository
        # enumeration or connection creation access.
        r = admin_client.get("/github/installations/42/repositories", headers=h_b)
        assert r.status_code == 403, r.text
        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 42},
            headers=h_b,
        )
        assert r.status_code == 403, r.text

        r = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 42},
            headers=h_a,
        )
        assert r.status_code == 201, r.text

    def test_disabled_installation_cannot_verify_or_sync_existing_connection(
        self, admin_client, monkeypatch, rsa_private_key_path
    ):
        _configure_app(monkeypatch, rsa_private_key_path)
        monkeypatch.setenv("GITHUB_APP_ALLOWED_ORGANIZATION", "acme")
        monkeypatch.setattr("urllib.request.urlopen", self._installation_response())
        token = _login(admin_client)
        system = _create_system(admin_client, token, "disabled-installation-system")
        headers = _headers(token, system["id"])
        assert admin_client.post(
            "/github/installations", json={"installation_id": 42}, headers=_bearer(token)
        ).status_code == 201
        assert admin_client.post(
            f"/github/installations/42/systems/{system['id']}", headers=_bearer(token)
        ).status_code == 200
        connection = admin_client.post(
            "/github/connections",
            json={"owner": "acme", "repo": "widgets", "installation_id": 42},
            headers=headers,
        ).json()
        assert admin_client.post(
            "/github/installations/42/disable", headers=_bearer(token)
        ).status_code == 200

        r = admin_client.post(f"/github/connections/{connection['id']}/verify", headers=headers)
        assert r.status_code == 403, r.text
        r = admin_client.post(f"/github/connections/{connection['id']}/sync", headers=headers)
        assert r.status_code == 403, r.text
