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


def test_evaluate_trace_with_sdk_repr_output(client):
    # Mirrors what @probe stores: the function returned a JSON string, so the
    # SDK repr wraps it in single quotes.
    trace = _trace(trace_id="eval-repr")
    trace["output"] = "'{\"a\": 2, \"b\": 1}'"
    client.post("/traces", json=trace)

    client.post(
        "/components/summarizer/criteria",
        json={
            "name": "json equal",
            "criterion_type": "json_equal",
            "expected_value": '{"b": 1, "a": 2}',
        },
    )
    client.post(
        "/components/summarizer/criteria",
        json={
            "name": "keys",
            "criterion_type": "required_keys",
            "expected_value": '["a", "b"]',
        },
    )

    r = client.post("/traces/eval-repr/evaluate")
    assert r.status_code == 200
    statuses = sorted(row["status"] for row in r.json())
    assert statuses == ["ok", "ok"]


def test_evaluate_trace_with_dict_repr_output(client):
    # A function returning a dict: the SDK stores its Python repr.
    trace = _trace(trace_id="eval-dict")
    trace["output"] = "{'a': 2, 'b': 1}"
    client.post("/traces", json=trace)

    client.post(
        "/components/summarizer/criteria",
        json={
            "name": "json equal",
            "criterion_type": "json_equal",
            "expected_value": '{"a": 2, "b": 1}',
        },
    )
    client.post(
        "/components/summarizer/criteria",
        json={
            "name": "keys",
            "criterion_type": "required_keys",
            "expected_value": '["a", "b"]',
        },
    )

    r = client.post("/traces/eval-dict/evaluate")
    assert r.status_code == 200
    statuses = sorted(row["status"] for row in r.json())
    assert statuses == ["ok", "ok"]


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


def _create_system(client, token, name, environment="production"):
    r = client.post(
        "/systems",
        json={
            "name": name,
            "environment": environment,
            "description": f"{name} description",
        },
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _issue_system_token(client, login_token, system_id, name):
    r = client.post(
        "/tokens/me",
        json={"name": name, "system_id": system_id},
        headers=_bearer(login_token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_system_tokens_isolate_components_traces_and_policy(admin_client):
    admin_token = _login(admin_client)
    system_a = _create_system(admin_client, admin_token, "Support API")
    system_b = _create_system(admin_client, admin_token, "Document Pipeline")
    token_a = _issue_system_token(
        admin_client, admin_token, system_a["id"], "support-production"
    )
    token_b = _issue_system_token(
        admin_client, admin_token, system_b["id"], "documents-production"
    )

    # The same component and trace ids are valid in separate systems.
    r = admin_client.post(
        "/traces",
        json=_trace(component_id="shared", trace_id="same-trace"),
        headers={"X-Api-Key": token_a["token"]},
    )
    assert r.status_code == 201
    trace_b = _trace(component_id="shared", trace_id="same-trace")
    trace_b["output"] = "'SYSTEM_B'"
    r = admin_client.post(
        "/traces", json=trace_b, headers={"X-Api-Key": token_b["token"]}
    )
    assert r.status_code == 201

    r = admin_client.put(
        "/components/shared/policy",
        json={"mode": "shadow"},
        headers={"X-Api-Key": token_a["token"]},
    )
    assert r.status_code == 200

    policy_a = admin_client.get(
        "/components/shared/policy", headers={"X-Api-Key": token_a["token"]}
    ).json()
    policy_b = admin_client.get(
        "/components/shared/policy", headers={"X-Api-Key": token_b["token"]}
    ).json()
    assert policy_a["mode"] == "shadow"
    assert policy_b["mode"] == "trace"

    rows_a = admin_client.get(
        "/components/shared/traces", headers={"X-Api-Key": token_a["token"]}
    ).json()
    rows_b = admin_client.get(
        "/components/shared/traces", headers={"X-Api-Key": token_b["token"]}
    ).json()
    assert rows_a[0]["output"] == "'HI'"
    assert rows_b[0]["output"] == "'SYSTEM_B'"

    systems = admin_client.get("/systems", headers=_bearer(admin_token)).json()
    summaries = {system["id"]: system for system in systems}
    assert summaries[system_a["id"]]["trace_count"] == 1
    assert summaries[system_b["id"]]["trace_count"] == 1


def test_user_cannot_select_or_issue_token_for_another_users_system(admin_client):
    admin_token = _login(admin_client)
    admin_system = _create_system(admin_client, admin_token, "Admin System")
    _create_user(admin_client, admin_token)
    user_token = _login(admin_client, "alice", "pw")
    user_system = _create_system(admin_client, user_token, "Alice System")

    visible = admin_client.get("/systems", headers=_bearer(user_token)).json()
    assert [system["id"] for system in visible] == [user_system["id"]]

    headers = {
        **_bearer(user_token),
        "X-Probe-System-Id": str(admin_system["id"]),
    }
    assert admin_client.get("/components", headers=headers).status_code == 403

    r = admin_client.post(
        "/tokens/me",
        json={"name": "forbidden", "system_id": admin_system["id"]},
        headers=_bearer(user_token),
    )
    assert r.status_code == 403


def test_delete_system_removes_its_tokens_and_data(admin_client):
    admin_token = _login(admin_client)
    system = _create_system(admin_client, admin_token, "Disposable")
    api_token = _issue_system_token(
        admin_client, admin_token, system["id"], "disposable-token"
    )
    admin_client.post(
        "/traces",
        json=_trace(trace_id="disposable-trace"),
        headers={"X-Api-Key": api_token["token"]},
    )

    r = admin_client.delete(
        f"/systems/{system['id']}", headers=_bearer(admin_token)
    )
    assert r.status_code == 204
    assert (
        admin_client.post(
            "/traces",
            json=_trace(trace_id="after-delete"),
            headers={"X-Api-Key": api_token["token"]},
        ).status_code
        == 401
    )
    systems = admin_client.get("/systems", headers=_bearer(admin_token)).json()
    assert all(row["id"] != system["id"] for row in systems)


def test_generation_run_uses_trace_input_and_mock_llm(admin_client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    admin_token = _login(admin_client)
    system = _create_system(admin_client, admin_token, "Generation System")
    api_token = _issue_system_token(
        admin_client, admin_token, system["id"], "generation-token"
    )
    trace = _trace(component_id="summarizer", trace_id="gen-trace")
    trace["input"] = {"args": ["'hello'"], "kwargs": {}}
    trace["output"] = "'hello'"
    r = admin_client.post(
        "/traces", json=trace, headers={"X-Api-Key": api_token["token"]}
    )
    assert r.status_code == 201

    r = admin_client.post(
        "/generation-runs",
        json={
            "component_id": "summarizer",
            "trace_id": "gen-trace",
            "objective": "uppercase the output",
        },
        headers=_bearer(admin_token) | {"X-Probe-System-Id": str(system["id"])},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["component_id"] == "summarizer"
    assert body["candidate_output"] == "'HELLO'"
    assert body["llm_verdict"] == "better"
    assert "def candidate" in body["generated_code"]

    r = admin_client.get(
        "/generation-runs?component_id=summarizer&trace_id=gen-trace",
        headers=_bearer(admin_token) | {"X-Probe-System-Id": str(system["id"])},
    )
    assert r.status_code == 200
    assert r.json()[0]["id"] == body["id"]


def test_generation_run_is_system_scoped(admin_client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    admin_token = _login(admin_client)
    system_a = _create_system(admin_client, admin_token, "Generation A")
    system_b = _create_system(admin_client, admin_token, "Generation B")
    token_a = _issue_system_token(admin_client, admin_token, system_a["id"], "a")
    trace = _trace(component_id="shared", trace_id="same-id")
    trace["input"] = {"args": ["'a'"], "kwargs": {}}
    admin_client.post("/traces", json=trace, headers={"X-Api-Key": token_a["token"]})

    r = admin_client.post(
        "/generation-runs",
        json={
            "component_id": "shared",
            "trace_id": "same-id",
            "objective": "uppercase",
        },
        headers=_bearer(admin_token) | {"X-Probe-System-Id": str(system_b["id"])},
    )
    assert r.status_code == 404


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


def test_delete_user_removes_account_and_tokens(admin_client):
    admin_token = _login(admin_client)
    r = admin_client.post(
        "/users",
        json={"username": "dave", "password": "pw"},
        headers=_bearer(admin_token),
    )
    uid = r.json()["id"]
    user_token = _login(admin_client, "dave", "pw")

    r = admin_client.delete(f"/users/{uid}", headers=_bearer(admin_token))
    assert r.status_code == 204

    # The user is gone from the listing.
    r = admin_client.get("/users", headers=_bearer(admin_token))
    assert all(u["username"] != "dave" for u in r.json())

    # Their existing session token no longer authenticates.
    r = admin_client.get("/auth/me", headers=_bearer(user_token))
    assert r.status_code == 401

    # And they can no longer log in.
    r = admin_client.post("/auth/login", json={"username": "dave", "password": "pw"})
    assert r.status_code == 401


def test_delete_missing_user_returns_404(admin_client):
    admin_token = _login(admin_client)
    r = admin_client.delete("/users/9999", headers=_bearer(admin_token))
    assert r.status_code == 404


def test_non_admin_cannot_delete_user(admin_client):
    admin_token = _login(admin_client)
    r = admin_client.post(
        "/users",
        json={"username": "erin", "password": "pw"},
        headers=_bearer(admin_token),
    )
    target_id = r.json()["id"]
    admin_client.post(
        "/users",
        json={"username": "frank", "password": "pw"},
        headers=_bearer(admin_token),
    )
    user_token = _login(admin_client, "frank", "pw")
    r = admin_client.delete(f"/users/{target_id}", headers=_bearer(user_token))
    assert r.status_code == 403


def test_admin_cannot_delete_self(admin_client):
    admin_token = _login(admin_client)
    me = admin_client.get("/auth/me", headers=_bearer(admin_token)).json()
    my_id = me["user"]["id"]
    r = admin_client.delete(f"/users/{my_id}", headers=_bearer(admin_token))
    assert r.status_code == 409


def test_cannot_delete_last_active_admin(admin_client):
    admin_token = _login(admin_client)
    # Create a second admin, then delete the bootstrapped one so only the new
    # admin remains. Deleting that last admin must be refused.
    r = admin_client.post(
        "/users",
        json={"username": "second-admin", "password": "pw", "role": "admin"},
        headers=_bearer(admin_token),
    )
    second_id = r.json()["id"]
    second_token = _login(admin_client, "second-admin", "pw")

    me = admin_client.get("/auth/me", headers=_bearer(admin_token)).json()
    root_id = me["user"]["id"]
    r = admin_client.delete(f"/users/{root_id}", headers=_bearer(second_token))
    assert r.status_code == 204

    # second-admin is now the only active admin and cannot delete itself either,
    # but a delete by another admin is impossible — confirm the guard via
    # deactivate, which also protects the last admin.
    r = admin_client.post(
        f"/users/{second_id}/deactivate", headers=_bearer(second_token)
    )
    assert r.status_code == 409


def test_logout_revokes_session_token(admin_client):
    token = _login(admin_client)
    r = admin_client.post("/auth/logout", headers=_bearer(token))
    assert r.status_code == 204
    r = admin_client.get("/auth/me", headers=_bearer(token))
    assert r.status_code == 401


def _create_user(client, admin_token, username="alice", password="pw", role="user"):
    r = client.post(
        "/users",
        json={"username": username, "password": password, "role": role},
        headers=_bearer(admin_token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_self_token_issue_list_and_revoke(admin_client):
    admin_token = _login(admin_client)
    uid = _create_user(admin_client, admin_token)
    user_token = _login(admin_client, "alice", "pw")

    # A non-admin user can issue their own API token.
    r = admin_client.post(
        "/tokens/me", json={"name": "my sdk token"}, headers=_bearer(user_token)
    )
    assert r.status_code == 201
    body = r.json()
    assert body["user_id"] == uid
    assert body["kind"] == "api"
    raw = body["token"]
    token_id = body["id"]

    # The issued token authenticates SDK-style requests.
    r = admin_client.post("/traces", json=_trace(), headers={"X-Api-Key": raw})
    assert r.status_code == 201

    # The listing contains only the caller's tokens (session + api).
    r = admin_client.get("/tokens/me", headers=_bearer(user_token))
    assert r.status_code == 200
    tokens = r.json()
    assert {t["user_id"] for t in tokens} == {uid}
    assert any(t["id"] == token_id for t in tokens)
    # Raw token material is never included in listings.
    assert all("token" not in t for t in tokens)

    # The user can revoke their own token, after which it stops working.
    r = admin_client.post(
        f"/tokens/me/{token_id}/revoke", headers=_bearer(user_token)
    )
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    r = admin_client.post("/traces", json=_trace(), headers={"X-Api-Key": raw})
    assert r.status_code == 401


def test_self_token_cannot_revoke_others_token(admin_client):
    admin_token = _login(admin_client)
    _create_user(admin_client, admin_token)
    user_token = _login(admin_client, "alice", "pw")

    r = admin_client.post(
        "/tokens/me", json={"name": "admin token"}, headers=_bearer(admin_token)
    )
    admin_owned_id = r.json()["id"]

    r = admin_client.post(
        f"/tokens/me/{admin_owned_id}/revoke", headers=_bearer(user_token)
    )
    assert r.status_code == 404
    # The admin's token is untouched.
    r = admin_client.get("/tokens", headers=_bearer(admin_token))
    target = next(t for t in r.json() if t["id"] == admin_owned_id)
    assert target["revoked"] is False


def test_self_token_endpoints_reject_legacy_key(auth_client):
    r = auth_client.get("/tokens/me", headers={"X-Api-Key": "good-key"})
    assert r.status_code == 403
    r = auth_client.post(
        "/tokens/me", json={"name": "x"}, headers={"X-Api-Key": "good-key"}
    )
    assert r.status_code == 403


def test_self_token_endpoints_reject_anonymous(client):
    # Auth disabled (no users, no legacy keys): there is no user account to
    # attach a token to.
    r = client.get("/tokens/me")
    assert r.status_code == 403


def test_admin_tokens_endpoints_reject_non_admin(admin_client):
    admin_token = _login(admin_client)
    _create_user(admin_client, admin_token)
    user_token = _login(admin_client, "alice", "pw")

    assert admin_client.get("/tokens", headers=_bearer(user_token)).status_code == 403
    r = admin_client.post(
        "/tokens", json={"name": "x"}, headers=_bearer(user_token)
    )
    assert r.status_code == 403


def test_admin_password_reset(admin_client):
    admin_token = _login(admin_client)
    uid = _create_user(admin_client, admin_token)
    old_session = _login(admin_client, "alice", "pw")
    r = admin_client.post(
        "/tokens/me", json={"name": "sdk"}, headers=_bearer(old_session)
    )
    api_raw = r.json()["token"]

    r = admin_client.post(
        f"/users/{uid}/password",
        json={"password": "newpw"},
        headers=_bearer(admin_token),
    )
    assert r.status_code == 200

    # Old password no longer works; the new one does.
    r = admin_client.post("/auth/login", json={"username": "alice", "password": "pw"})
    assert r.status_code == 401
    _login(admin_client, "alice", "newpw")

    # Existing sessions are revoked, but API tokens keep working.
    r = admin_client.get("/auth/me", headers=_bearer(old_session))
    assert r.status_code == 401
    r = admin_client.get("/auth/me", headers={"X-Api-Key": api_raw})
    assert r.status_code == 200


def test_password_reset_requires_admin_and_existing_user(admin_client):
    admin_token = _login(admin_client)
    _create_user(admin_client, admin_token)
    user_token = _login(admin_client, "alice", "pw")

    r = admin_client.post(
        "/users/1/password", json={"password": "x"}, headers=_bearer(user_token)
    )
    assert r.status_code == 403
    r = admin_client.post(
        "/users/9999/password", json={"password": "x"}, headers=_bearer(admin_token)
    )
    assert r.status_code == 404


def test_admin_role_change(admin_client):
    admin_token = _login(admin_client)
    uid = _create_user(admin_client, admin_token)
    user_token = _login(admin_client, "alice", "pw")
    assert admin_client.get("/users", headers=_bearer(user_token)).status_code == 403

    r = admin_client.put(
        f"/users/{uid}/role", json={"role": "admin"}, headers=_bearer(admin_token)
    )
    assert r.status_code == 200
    assert r.json()["role"] == "admin"
    assert admin_client.get("/users", headers=_bearer(user_token)).status_code == 200

    r = admin_client.put(
        f"/users/{uid}/role", json={"role": "user"}, headers=_bearer(admin_token)
    )
    assert r.status_code == 200
    assert admin_client.get("/users", headers=_bearer(user_token)).status_code == 403


def test_cannot_demote_last_active_admin(admin_client):
    admin_token = _login(admin_client)
    me = admin_client.get("/auth/me", headers=_bearer(admin_token)).json()
    root_id = me["user"]["id"]
    r = admin_client.put(
        f"/users/{root_id}/role", json={"role": "user"}, headers=_bearer(admin_token)
    )
    assert r.status_code == 409


def test_role_change_requires_admin(admin_client):
    admin_token = _login(admin_client)
    uid = _create_user(admin_client, admin_token)
    user_token = _login(admin_client, "alice", "pw")
    r = admin_client.put(
        f"/users/{uid}/role", json={"role": "admin"}, headers=_bearer(user_token)
    )
    assert r.status_code == 403


def test_cannot_deactivate_last_active_admin(admin_client):
    admin_token = _login(admin_client)
    me = admin_client.get("/auth/me", headers=_bearer(admin_token)).json()
    root_id = me["user"]["id"]
    r = admin_client.post(
        f"/users/{root_id}/deactivate", headers=_bearer(admin_token)
    )
    assert r.status_code == 409
