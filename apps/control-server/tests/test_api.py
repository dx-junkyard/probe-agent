import os
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-test.db"))
    from app.main import app  # noqa: WPS433
    with TestClient(app) as c:
        yield c


def _trace(component_id="summarizer", trace_id="t1"):
    return {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": ["'hi'"], "kwargs": {}},
        "output": "'HI'",
        "error": None,
        "duration_ms": 1.23,
        "timestamp": time.time(),
    }


def test_post_trace_and_list(client):
    r = client.post("/traces", json=_trace())
    assert r.status_code == 201

    r = client.get("/components/summarizer/traces")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["trace_id"] == "t1"
    assert rows[0]["input"] == {"args": ["'hi'"], "kwargs": {}}


def test_components_lists_aggregates(client):
    client.post("/traces", json=_trace(trace_id="a"))
    client.post("/traces", json=_trace(trace_id="b"))

    r = client.get("/components")
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["component_id"] == "summarizer"
    assert rows[0]["trace_count"] == 2
    assert rows[0]["mode"] == "trace"


def test_policy_default_and_update(client):
    r = client.get("/components/new-one/policy")
    assert r.json()["mode"] == "trace"

    r = client.put("/components/new-one/policy", json={"mode": "shadow"})
    assert r.status_code == 200
    assert r.json()["mode"] == "shadow"

    r = client.get("/components/new-one/policy")
    assert r.json()["mode"] == "shadow"


def test_shadow_lifecycle(client):
    payload = {
        "trace_id": "tx",
        "component_id": "summarizer",
        "current_output": "'A'",
        "candidate_output": "'B'",
        "candidate_error": None,
        "candidate_duration_ms": 0.5,
        "timestamp": time.time(),
    }
    r = client.post("/components/summarizer/shadow-results", json=payload)
    assert r.status_code == 201
    rid = r.json()["id"]

    r = client.get("/components/summarizer/shadow-results")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["candidate_output"] == "'B'"
    assert rows[0]["evaluation"] is None

    r = client.put(f"/shadow-results/{rid}/evaluation", json={"evaluation": "better"})
    assert r.status_code == 200

    r = client.get("/components/summarizer/shadow-results")
    assert r.json()[0]["evaluation"] == "better"


def test_policy_invalid_mode_rejected(client):
    r = client.put("/components/x/policy", json={"mode": "yolo"})
    assert r.status_code == 422


# --- Evaluation context tests ---


def test_system_profile_default_and_update(client):
    r = client.get("/system-profile")
    assert r.status_code == 200
    assert r.json()["name"] == ""

    payload = {
        "name": "Support Assistant",
        "purpose": "answer user questions",
        "target_users": ["end users", "support staff"],
        "stakeholder_value": "faster resolution",
        "constraints": ["no PII leakage"],
        "success_criteria": ["accurate answers"],
    }
    r = client.put("/system-profile", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["target_users"] == ["end users", "support staff"]
    assert body["updated_at"] is not None

    r = client.get("/system-profile")
    assert r.json()["name"] == "Support Assistant"
    assert r.json()["constraints"] == ["no PII leakage"]


def test_component_profile_default_and_update(client):
    r = client.get("/components/summarizer/profile")
    assert r.status_code == 200
    assert r.json()["component_id"] == "summarizer"
    assert r.json()["purpose"] == ""

    payload = {
        "purpose": "summarize text",
        "responsibility": "produce a short summary",
        "expected_input": "raw text",
        "expected_output": "summary string",
        "failure_impact": "user sees no summary",
        "notes": "keep under 100 words",
    }
    r = client.put("/components/summarizer/profile", json=payload)
    assert r.status_code == 200
    assert r.json()["responsibility"] == "produce a short summary"

    r = client.get("/components/summarizer/profile")
    assert r.json()["expected_output"] == "summary string"


def test_criteria_crud(client):
    r = client.post(
        "/components/summarizer/criteria",
        json={
            "name": "must mention topic",
            "criterion_type": "contains",
            "expected_value": "weather",
        },
    )
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["enabled"] is True

    r = client.get("/components/summarizer/criteria")
    assert len(r.json()) == 1

    r = client.put(
        f"/criteria/{cid}",
        json={
            "name": "must mention topic",
            "criterion_type": "contains",
            "expected_value": "forecast",
            "enabled": False,
        },
    )
    assert r.status_code == 200
    assert r.json()["expected_value"] == "forecast"
    assert r.json()["enabled"] is False

    r = client.put(
        "/criteria/9999",
        json={"name": "x", "criterion_type": "contains"},
    )
    assert r.status_code == 404


def test_evaluate_trace_rule_based(client):
    trace = _trace(trace_id="eval-1")
    trace["output"] = '{"summary": "hello", "lang": "en"}'
    client.post("/traces", json=trace)

    client.post(
        "/components/summarizer/criteria",
        json={
            "name": "contains hello",
            "criterion_type": "contains",
            "expected_value": "hello",
        },
    )
    client.post(
        "/components/summarizer/criteria",
        json={
            "name": "has required keys",
            "criterion_type": "required_keys",
            "expected_value": '["summary", "lang"]',
        },
    )
    client.post(
        "/components/summarizer/criteria",
        json={
            "name": "missing key",
            "criterion_type": "required_keys",
            "expected_value": '["summary", "author"]',
        },
    )
    client.post(
        "/components/summarizer/criteria",
        json={"name": "human check", "criterion_type": "natural_language"},
    )

    r = client.post("/traces/eval-1/evaluate")
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 4
    statuses = [row["status"] for row in results]
    assert statuses.count("ok") == 2
    assert statuses.count("ng") == 1
    assert statuses.count("needs_review") == 1

    r = client.get("/traces/eval-1/evaluations")
    assert r.status_code == 200
    assert len(r.json()) == 4


def test_evaluate_is_idempotent(client):
    trace = _trace(trace_id="eval-2")
    trace["output"] = "the quick brown fox"
    client.post("/traces", json=trace)
    client.post(
        "/components/summarizer/criteria",
        json={
            "name": "contains fox",
            "criterion_type": "contains",
            "expected_value": "fox",
        },
    )
    client.post("/traces/eval-2/evaluate")
    client.post("/traces/eval-2/evaluate")
    r = client.get("/traces/eval-2/evaluations")
    assert len(r.json()) == 1


def test_evaluate_missing_trace(client):
    r = client.post("/traces/nope/evaluate")
    assert r.status_code == 404


# --- Authentication tests ---


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-auth-test.db"))
    monkeypatch.setenv("CONTROL_API_KEYS", "good-key,also-good")
    from app.main import app  # noqa: WPS433
    with TestClient(app) as c:
        yield c


def test_auth_disabled_allows_all(client):
    r = client.post("/traces", json=_trace())
    assert r.status_code == 201


def test_auth_rejects_missing_key(auth_client):
    r = auth_client.post("/traces", json=_trace())
    assert r.status_code == 401


def test_auth_rejects_invalid_key(auth_client):
    r = auth_client.post("/traces", json=_trace(), headers={"X-Api-Key": "wrong"})
    assert r.status_code == 401


def test_auth_accepts_valid_key(auth_client):
    r = auth_client.post("/traces", json=_trace(), headers={"X-Api-Key": "good-key"})
    assert r.status_code == 201


def test_auth_accepts_second_valid_key(auth_client):
    r = auth_client.post("/traces", json=_trace(), headers={"X-Api-Key": "also-good"})
    assert r.status_code == 201


def test_health_always_accessible(auth_client):
    r = auth_client.get("/health")
    assert r.status_code == 200


# --- User / token management tests ---


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-admin-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433
    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_admin_bootstrapped_and_password_hashed(admin_client):
    # Password is never stored in plaintext.
    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT password_hash, role FROM users WHERE username = 'root'"
        ).fetchone()
    assert row is not None
    assert row["role"] == "admin"
    assert "s3cret" not in row["password_hash"]
    assert row["password_hash"].startswith("pbkdf2_sha256$")


def test_login_and_me(admin_client):
    token = _login(admin_client)
    r = admin_client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "root"
    assert body["user"]["role"] == "admin"
    assert body["auth"] == "token"


def test_login_rejects_bad_password(admin_client):
    r = admin_client.post("/auth/login", json={"username": "root", "password": "nope"})
    assert r.status_code == 401


def test_unauthenticated_requires_credentials(admin_client):
    r = admin_client.post("/traces", json=_trace())
    assert r.status_code == 401


def test_admin_only_user_creation(admin_client):
    admin_token = _login(admin_client)
    r = admin_client.post(
        "/users",
        json={"username": "alice", "password": "pw", "role": "user"},
        headers=_bearer(admin_token),
    )
    assert r.status_code == 201

    # Non-admin cannot create users.
    user_token = _login(admin_client, "alice", "pw")
    r = admin_client.post(
        "/users",
        json={"username": "bob", "password": "pw"},
        headers=_bearer(user_token),
    )
    assert r.status_code == 403


def test_token_issue_use_and_revoke(admin_client):
    admin_token = _login(admin_client)

    r = admin_client.post(
        "/tokens", json={"name": "sdk-token"}, headers=_bearer(admin_token)
    )
    assert r.status_code == 201
    body = r.json()
    raw = body["token"]
    token_id = body["id"]

    # The issued token works as an X-Api-Key (SDK compatibility path).
    r = admin_client.post("/traces", json=_trace(), headers={"X-Api-Key": raw})
    assert r.status_code == 201

    # Revoke it.
    r = admin_client.post(
        f"/tokens/{token_id}/revoke", headers=_bearer(admin_token)
    )
    assert r.status_code == 200
    assert r.json()["revoked"] is True

    # Revoked token is rejected.
    r = admin_client.post("/traces", json=_trace(), headers={"X-Api-Key": raw})
    assert r.status_code == 401


def test_deactivated_user_cannot_authenticate(admin_client):
    admin_token = _login(admin_client)
    r = admin_client.post(
        "/users",
        json={"username": "carol", "password": "pw"},
        headers=_bearer(admin_token),
    )
    uid = r.json()["id"]
    user_token = _login(admin_client, "carol", "pw")

    # Deactivate the user.
    r = admin_client.post(
        f"/users/{uid}/deactivate", headers=_bearer(admin_token)
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # Their existing token is now revoked.
    r = admin_client.get("/auth/me", headers=_bearer(user_token))
    assert r.status_code == 401

    # And they can no longer log in.
    r = admin_client.post("/auth/login", json={"username": "carol", "password": "pw"})
    assert r.status_code == 403
