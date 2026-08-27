"""Tests for Issue #290: approval-gated runtime observation proposal lifecycle.

Covers:
1. Create -> list -> approve/reject lifecycle (routes/interview_observation.py).
2. Manual-only decisions: decision_by/decision_at/status are only ever set by
   the approve/reject endpoints; only a 'proposed' row can be decided (409
   otherwise).
3. Approving never starts anything: the response only carries a fixed
   policy_pointer template pointing at the existing policy endpoint, and the
   `components` (policy) table is provably unchanged before/after approval
   -- the negative test the brief requires.
4. System isolation.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-observation-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name):
    r = client.post(
        "/systems", json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {**_bearer(token), "X-Probe-System-Id": str(system_id)}


def _insert_snapshot(system_id, commit_sha="abc123"):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)""",
            (system_id, commit_sha, now, now),
        )
        return cur.lastrowid


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _setup(client):
    token = _login(client)
    system = _create_system(client, token, "System A")
    snapshot_id = _insert_snapshot(system["id"])
    headers = _headers(token, system["id"])
    session_id = _create_session(client, headers, snapshot_id)
    return token, system["id"], headers, session_id


def _create_proposal(client, headers, session_id, **overrides):
    body = {
        "target_component": "src_a_fn",
        "purpose": "エラー発生時の入力を観測して原因を切り分けたい",
        "expected_cost": "1週間、trace mode",
        "risk_note": "入力に個人情報を含む可能性あり",
        "retention_note": "7日で自動失効",
    }
    body.update(overrides)
    return client.post(
        f"/interview/sessions/{session_id}/observation-proposals", json=body, headers=headers,
    )


# --- Lifecycle -----------------------------------------------------------------


def test_create_proposal_is_proposed_status(admin_client):
    token, system_id, headers, session_id = _setup(admin_client)
    r = _create_proposal(admin_client, headers, session_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "proposed"
    assert body["target_component"] == "src_a_fn"
    assert body["decision_by"] is None
    assert body["decision_at"] is None
    assert body["policy_pointer"] is None


def test_list_proposals(admin_client):
    token, system_id, headers, session_id = _setup(admin_client)
    _create_proposal(admin_client, headers, session_id, target_component="a")
    _create_proposal(admin_client, headers, session_id, target_component="b")
    r = admin_client.get(f"/interview/sessions/{session_id}/observation-proposals", headers=headers)
    assert r.status_code == 200, r.text
    assert {p["target_component"] for p in r.json()} == {"a", "b"}


def test_approve_sets_manual_decision_and_policy_pointer(admin_client):
    token, system_id, headers, session_id = _setup(admin_client)
    proposal_id = _create_proposal(admin_client, headers, session_id).json()["id"]

    r = admin_client.post(
        f"/interview/observation-proposals/{proposal_id}/approve",
        json={"decision_by": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["decision_by"] == "root"
    assert body["decision_at"] is not None
    # Deterministic, fixed pointer -- never LLM free text -- naming the
    # existing policy endpoint and the target component.
    assert "src_a_fn" in body["policy_pointer"]
    assert "policy" in body["policy_pointer"] or "ポリシー" in body["policy_pointer"]


def test_reject_sets_manual_decision_no_policy_pointer(admin_client):
    token, system_id, headers, session_id = _setup(admin_client)
    proposal_id = _create_proposal(admin_client, headers, session_id).json()["id"]

    r = admin_client.post(
        f"/interview/observation-proposals/{proposal_id}/reject", headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "rejected"
    assert body["decision_at"] is not None
    assert body["policy_pointer"] is None


def test_approve_already_decided_proposal_is_409(admin_client):
    token, system_id, headers, session_id = _setup(admin_client)
    proposal_id = _create_proposal(admin_client, headers, session_id).json()["id"]
    admin_client.post(f"/interview/observation-proposals/{proposal_id}/approve", headers=headers)

    r = admin_client.post(f"/interview/observation-proposals/{proposal_id}/approve", headers=headers)
    assert r.status_code == 409, r.text
    r = admin_client.post(f"/interview/observation-proposals/{proposal_id}/reject", headers=headers)
    assert r.status_code == 409, r.text


def test_create_proposal_links_origin_inquiry(admin_client, monkeypatch):
    token, system_id, headers, session_id = _setup(admin_client)

    from app.routes import interview_inquiry as inquiry_routes
    from app.inquiry_answering import InquiryAnswerResult

    def fake_create_llm_client(config):
        return object()

    def fake_generate_inquiry_answer(client, config, **kwargs):
        return InquiryAnswerResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            conclusion="回答です", answerable=True,
        )

    monkeypatch.setattr(inquiry_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(inquiry_routes, "generate_inquiry_answer", fake_generate_inquiry_answer)

    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "テスト質問"},
        headers=headers,
    )
    assert qa.status_code == 201, qa.text
    qa_id = qa.json()["id"]

    inquiry = admin_client.post(
        f"/interview/sessions/{session_id}/inquiries",
        json={"origin_kind": "qa", "origin_id": qa_id, "question_text": "観測が必要では?"},
        headers=headers,
    )
    assert inquiry.status_code == 201, inquiry.text
    inquiry_id = inquiry.json()["inquiry"]["id"]

    r = _create_proposal(admin_client, headers, session_id, origin_inquiry_id=inquiry_id)
    assert r.status_code == 201, r.text
    assert r.json()["origin_inquiry_id"] == inquiry_id


def test_create_proposal_rejects_unknown_origin_inquiry(admin_client):
    token, system_id, headers, session_id = _setup(admin_client)
    r = _create_proposal(admin_client, headers, session_id, origin_inquiry_id=999999)
    assert r.status_code == 404, r.text


# --- Negative test: approving never starts observation ------------------------


def test_approve_never_writes_to_components_policy_table(admin_client):
    """The binding negative test: no interview/investigation/observation-
    approval code path writes to control policies or starts anything."""
    token, system_id, headers, session_id = _setup(admin_client)

    from app.db import get_conn

    with get_conn() as conn:
        before = {
            tuple(row)
            for row in conn.execute("SELECT system_id, component_id, mode FROM components")
        }

    proposal_id = _create_proposal(admin_client, headers, session_id).json()["id"]
    r = admin_client.post(
        f"/interview/observation-proposals/{proposal_id}/approve", headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    with get_conn() as conn:
        after = {
            tuple(row)
            for row in conn.execute("SELECT system_id, component_id, mode FROM components")
        }
    assert before == after
    # And specifically: no row exists yet for the target component (approval
    # alone never creates or mutates its policy row).
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM components WHERE system_id = ? AND component_id = ?",
            (system_id, "src_a_fn"),
        ).fetchone()
    assert row is None


# --- System isolation -----------------------------------------------------------


def test_observation_proposal_system_isolation(admin_client):
    token_a, system_a, headers_a, session_a = _setup(admin_client)
    system_b = _create_system(admin_client, token_a, "System B")
    headers_b = _headers(token_a, system_b["id"])

    proposal = _create_proposal(admin_client, headers_a, session_a).json()

    r = admin_client.get(
        f"/interview/sessions/{session_a}/observation-proposals", headers=headers_b,
    )
    assert r.status_code == 404, r.text

    r = admin_client.post(
        f"/interview/observation-proposals/{proposal['id']}/approve", headers=headers_b,
    )
    assert r.status_code == 404, r.text


# --- Migration -------------------------------------------------------------------


def test_migration_creates_observation_proposal_table(admin_client):
    from app.db import get_conn, init_db

    init_db()
    with get_conn() as conn:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "runtime_observation_proposal" in tables
