"""Tests for Issue #35 Decision Workspace persistence and CRUD API."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-workspace-test.db"))
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


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "production", "description": ""},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _setup(admin_client, name="System A"):
    admin_token = _login(admin_client)
    system = _create_system(admin_client, admin_token, name)
    return admin_token, system["id"]


def test_create_list_and_get_workspace(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)

    r = admin_client.post(
        "/workspaces",
        json={"title": "Improve summarizer", "focus": "summarizer", "summary": ""},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    workspace = r.json()
    assert workspace["status"] == "active"
    assert workspace["system_id"] == system_id

    r = admin_client.get("/workspaces", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["messages"] == []
    assert detail["context_items"] == []
    assert detail["proposals"] == []


def test_workspace_messages_are_stored_in_order(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/messages",
        json={"role": "user", "content": "summarizerの品質を上げたい"},
        headers=headers,
    )
    assert r.status_code == 201
    r = admin_client.post(
        f"/workspaces/{workspace['id']}/messages",
        json={
            "role": "assistant",
            "content": "提案を作成しました",
            "context_metadata": {"used_context": ["feature:summarization"]},
            "proposals": [
                {
                    "proposal_type": "experiment_draft",
                    "title": "Try a longer prompt",
                    "body": {
                        "feature_id": "summarization",
                        "objective": "compare prompt variants",
                        "variant_summaries": ["longer-prompt"],
                    },
                }
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201

    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["context_metadata"]["used_context"] == [
        "feature:summarization"
    ]
    assert len(detail["proposals"]) == 1
    proposal = detail["proposals"][0]
    assert proposal["status"] == "proposed"
    assert proposal["proposal_type"] == "experiment_draft"
    assert proposal["decisions"] == []


def test_context_items_add_dedupe_and_delete(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()
    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO components (system_id, component_id, mode, updated_at)
               VALUES (?, 'summarizer', 'trace', 1)""",
            (system_id,),
        )

    payload = {"item_type": "component", "item_id": "summarizer", "label": "Summarizer"}
    r = admin_client.post(
        f"/workspaces/{workspace['id']}/context", json=payload, headers=headers
    )
    assert r.status_code == 201
    item_id = r.json()["id"]

    # Adding the same reference again is idempotent rather than duplicating it.
    r = admin_client.post(
        f"/workspaces/{workspace['id']}/context", json=payload, headers=headers
    )
    assert r.status_code == 201
    assert r.json()["id"] == item_id

    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    assert len(detail["context_items"]) == 1

    r = admin_client.delete(
        f"/workspaces/{workspace['id']}/context/{item_id}", headers=headers
    )
    assert r.status_code == 204
    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    assert detail["context_items"] == []

    r = admin_client.delete(
        f"/workspaces/{workspace['id']}/context/{item_id}", headers=headers
    )
    assert r.status_code == 404


def _create_proposal(admin_client, headers, workspace_id, proposal_type="experiment_draft"):
    if proposal_type == "experiment_draft":
        body = {
            "feature_id": "summarization",
            "objective": "compare variants",
            "variant_summaries": [],
        }
    else:
        body = {
            "feature_id": "summarization",
            "objective": "observe behavior",
            "target_components": [],
        }
    r = admin_client.post(
        f"/workspaces/{workspace_id}/messages",
        json={
            "role": "assistant",
            "content": "proposal message",
            "proposals": [
                {"proposal_type": proposal_type, "title": "t", "body": body}
            ],
        },
        headers=headers,
    )
    assert r.status_code == 201
    detail = admin_client.get(f"/workspaces/{workspace_id}", headers=headers).json()
    return detail["proposals"][-1]


def test_proposal_accept_creates_decision_history(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()
    proposal = _create_proposal(admin_client, headers, workspace["id"])

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept",
        json={"reason": "looks safe to try"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted"
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["decision"] == "accepted"
    assert body["decisions"][0]["reason"] == "looks safe to try"


def test_proposal_reject_then_accept_conflicts(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()
    proposal = _create_proposal(admin_client, headers, workspace["id"])

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/reject",
        json={"reason": "not worth the risk"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    # A proposal already decided cannot transition to a conflicting state.
    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept",
        json={"reason": "changed my mind"},
        headers=headers,
    )
    assert r.status_code == 409

    # Re-rejecting the same proposal is idempotent and keeps the original reason.
    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/reject",
        json={"reason": "duplicate request"},
        headers=headers,
    )
    assert r.status_code == 200
    assert len(r.json()["decisions"]) == 1
    assert r.json()["decisions"][0]["reason"] == "not worth the risk"


def test_proposal_can_be_deferred_with_decision_history(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()
    proposal = _create_proposal(admin_client, headers, workspace["id"])

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/defer",
        json={"reason": "need more traces"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "deferred"
    assert r.json()["decisions"][0]["decision"] == "deferred"


def test_invalid_proposal_is_rejected_at_message_boundary(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/messages",
        json={
            "role": "assistant",
            "content": "bad proposal",
            "proposals": [
                {
                    "proposal_type": "experiment_draft",
                    "title": "invalid",
                    "body": {"objective": "missing feature"},
                }
            ],
        },
        headers=headers,
    )
    assert r.status_code == 422
    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    assert detail["messages"] == []
    assert detail["proposals"] == []


def test_proposal_patch_only_allowed_while_proposed(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()
    proposal = _create_proposal(admin_client, headers, workspace["id"])

    r = admin_client.patch(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}",
        json={"title": "Updated title"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Updated title"

    admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept",
        json={"reason": "ok"},
        headers=headers,
    )
    r = admin_client.patch(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}",
        json={"title": "Should fail"},
        headers=headers,
    )
    assert r.status_code == 409


def test_proposal_patch_revalidates_structured_body(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()
    proposal = _create_proposal(admin_client, headers, workspace["id"])

    r = admin_client.patch(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}",
        json={"body": {"feature_id": "summarizer"}},
        headers=headers,
    )

    assert r.status_code == 422


def test_workspaces_and_context_are_system_scoped(admin_client):
    token, system_a = _setup(admin_client, "System A")
    _, system_b = _setup(admin_client, "System B")
    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b)

    workspace = admin_client.post(
        "/workspaces", json={"title": "A-only"}, headers=headers_a
    ).json()

    # System B cannot see or fetch System A's workspace.
    assert admin_client.get("/workspaces", headers=headers_b).json() == []
    r = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers_b)
    assert r.status_code == 404

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/messages",
        json={"role": "user", "content": "hello"},
        headers=headers_b,
    )
    assert r.status_code == 404

    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO components (system_id, component_id, mode, updated_at)
               VALUES (?, 'b-only', 'trace', 1)""",
            (system_b,),
        )
    r = admin_client.post(
        f"/workspaces/{workspace['id']}/context",
        json={"item_type": "component", "item_id": "b-only"},
        headers=headers_a,
    )
    assert r.status_code == 404


def test_workspace_not_found_returns_404(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    r = admin_client.get("/workspaces/9999", headers=headers)
    assert r.status_code == 404
