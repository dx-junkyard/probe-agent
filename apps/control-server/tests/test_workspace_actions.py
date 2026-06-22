"""Tests for Issue #39 proposal-to-draft handoff.

Covers: 409 when drafting a non-accepted proposal, correct payload and
missing_fields for both draft types, idempotent duplicate drafting (no
duplicate row), and system-scope isolation on the draft-by-id endpoint.
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-workspace-actions-test.db"))
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


def _create_workspace_with_proposal(admin_client, headers, proposal_type, body):
    workspace = admin_client.post(
        "/workspaces", json={"title": "Theme"}, headers=headers
    ).json()
    r = admin_client.post(
        f"/workspaces/{workspace['id']}/messages",
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
    detail = admin_client.get(f"/workspaces/{workspace['id']}", headers=headers).json()
    proposal = detail["proposals"][-1]
    return workspace, proposal


def test_draft_rejected_for_non_accepted_proposal(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace, proposal = _create_workspace_with_proposal(
        admin_client,
        headers,
        "experiment_draft",
        {"feature_id": "summarizer", "objective": "improve quality", "variant_summaries": []},
    )

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/draft",
        headers=headers,
    )
    assert r.status_code == 409, r.text


def test_experiment_draft_payload_and_missing_fields(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace, proposal = _create_workspace_with_proposal(
        admin_client,
        headers,
        "experiment_draft",
        {
            "feature_id": "summarizer",
            "objective": "improve quality",
            "variant_summaries": ["longer prompt"],
        },
    )
    admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept",
        json={"reason": "worth trying"},
        headers=headers,
    )

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/draft",
        headers=headers,
    )
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["target_screen"] == "experiments"
    assert draft["payload"] == {
        "feature_id": "summarizer",
        "objective": "improve quality",
        "variant_summaries": ["longer prompt"],
    }
    assert sorted(draft["missing_fields"]) == ["patch_text", "snapshot_id"]

    # Idempotent: drafting again returns the same row, not a duplicate.
    r2 = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/draft",
        headers=headers,
    )
    assert r2.status_code == 201, r2.text
    assert r2.json()["id"] == draft["id"]


def test_probe_plan_draft_missing_fields_when_nothing_set_up(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace, proposal = _create_workspace_with_proposal(
        admin_client,
        headers,
        "probe_plan_draft",
        {
            "feature_id": "summarizer",
            "objective": "find a probe point",
            "target_components": ["summarizer"],
        },
    )
    admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept",
        json={"reason": "worth probing"},
        headers=headers,
    )

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/draft",
        headers=headers,
    )
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["target_screen"] == "probe_planner"
    assert draft["payload"]["feature_id"] == "summarizer"
    assert "ready_repository_snapshot" in draft["missing_fields"]


def test_probe_plan_draft_missing_fields_when_prerequisites_satisfied(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace, proposal = _create_workspace_with_proposal(
        admin_client,
        headers,
        "probe_plan_draft",
        {
            "feature_id": "summarizer",
            "objective": "find a probe point",
            "target_components": [],
        },
    )
    admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept",
        json={"reason": "worth probing"},
        headers=headers,
    )

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at)
               VALUES (?, '', 'deadbeef', 'ready', ?)""",
            (system_id, now),
        )
        snapshot_id = cur.lastrowid
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model,
                    prompt_version, schema_version, decision_method, status,
                    is_mock, started_at)
               VALUES (?, ?, 'feature_drafts', 'mock', 'mock', 'v1', 'v1',
                       'reasoning_llm', 'completed', 1, ?)""",
            (system_id, snapshot_id, now),
        )
        run_id = cur.lastrowid
        conn.execute(
            """INSERT INTO feature_drafts
                   (system_id, intelligence_run_id, snapshot_id, feature_id,
                    name, created_at)
               VALUES (?, ?, ?, 'summarizer', 'Summarizer', ?)""",
            (system_id, run_id, snapshot_id, now),
        )
        cur = conn.execute(
            """INSERT INTO code_symbols
                   (snapshot_id, system_id, path, qualified_name, kind,
                    start_line, end_line)
               VALUES (?, ?, 'src/utils.py', 'summarize', 'function', 1, 5)""",
            (snapshot_id, system_id),
        )
        symbol_id = cur.lastrowid
        conn.execute(
            """INSERT INTO feature_code_links
                   (system_id, snapshot_id, intelligence_run_id, feature_id,
                    symbol_id, relation_reason, review_status, created_at, updated_at)
               VALUES (?, ?, ?, 'summarizer', ?, 'implements summarizer', 'accepted', ?, ?)""",
            (system_id, snapshot_id, run_id, symbol_id, now, now),
        )

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/draft",
        headers=headers,
    )
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["missing_fields"] == []


def test_invalid_proposal_body_rejected(admin_client):
    token, system_id = _setup(admin_client)
    headers = _headers(token, system_id)
    workspace, proposal = _create_workspace_with_proposal(
        admin_client, headers, "experiment_draft", {"k": "v"},
    )
    admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept",
        json={"reason": "go"},
        headers=headers,
    )

    r = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/draft",
        headers=headers,
    )
    assert r.status_code == 400, r.text


def test_draft_lookup_is_system_scoped(admin_client):
    token, system_id_a = _setup(admin_client, "System A")
    headers_a = _headers(token, system_id_a)
    workspace, proposal = _create_workspace_with_proposal(
        admin_client,
        headers_a,
        "experiment_draft",
        {"feature_id": "summarizer", "objective": "improve quality", "variant_summaries": []},
    )
    admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/accept",
        json={"reason": "go"},
        headers=headers_a,
    )
    draft = admin_client.post(
        f"/workspaces/{workspace['id']}/proposals/{proposal['id']}/draft",
        headers=headers_a,
    ).json()

    r = admin_client.get(f"/workspace-drafts/{draft['id']}", headers=headers_a)
    assert r.status_code == 200

    system_b = _create_system(admin_client, token, "System B")
    headers_b = _headers(token, system_b["id"])
    r = admin_client.get(f"/workspace-drafts/{draft['id']}", headers=headers_b)
    assert r.status_code == 404
