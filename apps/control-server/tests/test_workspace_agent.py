"""Tests for Issue #37 Decision Workspace structured LLM dialogue."""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-workspace-agent-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_MODEL", raising=False)
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "production", "description": ""},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _setup(admin_client, name="System A"):
    admin_token = _login(admin_client)
    system = _create_system(admin_client, admin_token, name)
    return admin_token, system["id"]


def _create_workspace(admin_client, headers, **kwargs):
    payload = {"title": "Theme"}
    payload.update(kwargs)
    r = admin_client.post("/workspaces", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _seed_component(system_id, component_id="summarizer"):
    import time as time_mod

    from app.db import get_conn

    now = time_mod.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO component_profiles
                   (system_id, component_id, purpose, responsibility, expected_input,
                    expected_output, failure_impact, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id,
                component_id,
                "Summarize text",
                "Turns long text into a short summary",
                "raw text",
                "short summary",
                "low",
                "",
                now,
                now,
            ),
        )


def _enable_reasoning(monkeypatch, fake_client):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr(
        "app.routes.workspaces.create_llm_client",
        lambda config: fake_client,
    )


class _SuccessClient:
    def __init__(self, evidence_source_type, evidence_source_id):
        self.evidence_source_type = evidence_source_type
        self.evidence_source_id = evidence_source_id

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps(
            {
                "assistant_message": "Here is what I found.",
                "grounded_findings": [
                    {
                        "claim": "The component summarizes text.",
                        "source_type": self.evidence_source_type,
                        "source_id": self.evidence_source_id,
                    }
                ],
                "assumptions": ["Traffic patterns are stable."],
                "missing_information": [],
                "proposals": [
                    {
                        "type": "experiment_draft",
                        "title": "Try a shorter summary",
                        "body": {
                            "feature_id": "summarizer-feature",
                            "objective": "Reduce summary length",
                            "variant_summaries": ["baseline", "shorter"],
                        },
                    }
                ],
                "next_questions": ["What is the target length?"],
            }
        )


class _MalformedJsonClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return "not json"


class _UngroundedClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps(
            {
                "assistant_message": "Here is what I found.",
                "grounded_findings": [
                    {
                        "claim": "Fabricated claim.",
                        "source_type": "component_profile",
                        "source_id": "does-not-exist",
                    }
                ],
                "assumptions": [],
                "missing_information": [],
                "proposals": [],
                "next_questions": [],
            }
        )


class _UnknownProposalTypeClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps(
            {
                "assistant_message": "Here is what I found.",
                "grounded_findings": [],
                "assumptions": [],
                "missing_information": [],
                "proposals": [{"type": "deploy_now", "title": "x", "body": {}}],
                "next_questions": [],
            }
        )


class _BadProposalBodyClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return json.dumps(
            {
                "assistant_message": "Here is what I found.",
                "grounded_findings": [],
                "assumptions": [],
                "missing_information": [],
                "proposals": [
                    {"type": "experiment_draft", "title": "x", "body": {"objective": "y"}}
                ],
                "next_questions": [],
            }
        )


def _evidence_ref(admin_client, headers, workspace_id):
    r = admin_client.get(f"/workspaces/{workspace_id}/context-pack", headers=headers)
    assert r.status_code == 200, r.text
    pack = r.json()
    assert pack["evidence"], "expected at least one evidence ref in the context pack"
    ref = pack["evidence"][0]
    return ref["source_type"], ref["source_id"]


def test_agent_turn_requires_reasoning_model_and_does_not_persist_partial_data(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = _create_workspace(admin_client, headers)

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/agent-turns",
        json={"message": "What should we try next?", "context_refs": []},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["error"] is not None
    assert data["assistant_message"] is None
    assert data["proposals"] == []
    assert data["user_message"]["content"] == "What should we try next?"

    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    assert len(detail["messages"]) == 1
    assert detail["proposals"] == []


def test_agent_turn_success_persists_assistant_message_and_proposal(admin_client, monkeypatch):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _seed_component(system_id)
    workspace = _create_workspace(admin_client, headers)
    pin_r = admin_client.post(
        f"/workspaces/{workspace['id']}/context",
        json={"item_type": "component", "item_id": "summarizer", "label": ""},
        headers=headers,
    )
    assert pin_r.status_code == 201, pin_r.text

    source_type, source_id = _evidence_ref(admin_client, headers, workspace["id"])
    _enable_reasoning(monkeypatch, _SuccessClient(source_type, source_id))

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/agent-turns",
        json={"message": "What should we try next?", "context_refs": []},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["error"] is None
    assert data["assistant_message"]["content"] == "Here is what I found."
    assert data["assistant_message"]["context_metadata"]["assumptions"] == [
        "Traffic patterns are stable."
    ]
    assert data["assistant_message"]["context_metadata"]["used_context"]
    assert len(data["proposals"]) == 1
    proposal = data["proposals"][0]
    assert proposal["status"] == "proposed"
    assert proposal["proposal_type"] == "experiment_draft"
    assert proposal["body"]["feature_id"] == "summarizer-feature"

    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    assert len(detail["messages"]) == 2
    assert len(detail["proposals"]) == 1


def test_agent_turn_malformed_json_fails_closed(admin_client, monkeypatch):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = _create_workspace(admin_client, headers)
    _enable_reasoning(monkeypatch, _MalformedJsonClient())

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/agent-turns",
        json={"message": "Hello", "context_refs": []},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["error"] is not None
    assert data["assistant_message"] is None
    assert data["proposals"] == []


def test_agent_turn_rejects_ungrounded_finding(admin_client, monkeypatch):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = _create_workspace(admin_client, headers)
    _enable_reasoning(monkeypatch, _UngroundedClient())

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/agent-turns",
        json={"message": "Hello", "context_refs": []},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert "not present in the context pack evidence" in data["error"]
    assert data["assistant_message"] is None
    assert data["proposals"] == []


def test_agent_turn_rejects_unknown_proposal_type(admin_client, monkeypatch):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = _create_workspace(admin_client, headers)
    _enable_reasoning(monkeypatch, _UnknownProposalTypeClient())

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/agent-turns",
        json={"message": "Hello", "context_refs": []},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert "unknown proposal type" in data["error"]
    assert data["proposals"] == []


def test_agent_turn_rejects_invalid_proposal_body(admin_client, monkeypatch):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace = _create_workspace(admin_client, headers)
    _enable_reasoning(monkeypatch, _BadProposalBodyClient())

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/agent-turns",
        json={"message": "Hello", "context_refs": []},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert "failed validation" in data["error"]
    assert data["proposals"] == []


def test_agent_turn_pins_requested_context_refs(admin_client, monkeypatch):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _seed_component(system_id)
    workspace = _create_workspace(admin_client, headers)
    _enable_reasoning(monkeypatch, _MalformedJsonClient())

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/agent-turns",
        json={
            "message": "Hello",
            "context_refs": [{"type": "component", "id": "summarizer"}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text

    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    assert len(detail["context_items"]) == 1
    assert detail["context_items"][0]["item_id"] == "summarizer"
    assert detail["messages"][0]["context_metadata"]["requested_context_refs"] == [
        {"type": "component", "id": "summarizer"}
    ]


def test_agent_turn_does_not_partially_pin_invalid_context_refs(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _seed_component(system_id)
    workspace = _create_workspace(admin_client, headers)

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/agent-turns",
        json={
            "message": "Hello",
            "context_refs": [
                {"type": "component", "id": "summarizer"},
                {"type": "component", "id": "missing-component"},
            ],
        },
        headers=headers,
    )
    assert r.status_code == 404

    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    assert detail["context_items"] == []
    assert detail["messages"] == []
