"""Tests for Issue #284: Intent Brief.

Covers: user create/confirm/correct/decline lifecycle, AI propose (mocked
reasoning call) creating 'proposed' rows that are never auto-confirmed,
invalid field/status rejected with 422, supersede/revision history retained,
System isolation, and the fail-closed propose path (LLM failure -> no rows
created, intelligence_runs failure recorded).
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-intent-test.db"))
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
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
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
            """
            INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, '/tmp/repo', ?, 'ready', ?, ?)
            """,
            (system_id, commit_sha, now, now),
        )
        return cur.lastrowid


def _setup(client, name="System A"):
    token = _login(client)
    system = _create_system(client, token, name)
    snapshot_id = _insert_snapshot(system["id"])
    return token, system["id"], snapshot_id


def _create_session(client, headers, snapshot_id):
    r = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_intent(client, headers, session_id, **overrides):
    body = {"field": "goal", "value_text": "トレース収集を効率化したい"}
    body.update(overrides)
    r = client.post(
        f"/interview/sessions/{session_id}/intent", json=body, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- User CRUD + finite-set validation ---------------------------------------


def test_create_and_list_intent_item(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    item = _create_intent(admin_client, headers, session_id)
    assert item["field"] == "goal"
    assert item["status"] == "confirmed"
    assert item["origin"] == "user"
    assert item["decision_method"] == "manual"
    assert item["superseded_by_id"] is None

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/intent", headers=headers
    ).json()
    assert [i["id"] for i in listing["items_by_field"]["goal"]] == [item["id"]]
    # All six canonical fields are present as keys, even when empty.
    assert set(listing["items_by_field"].keys()) == {
        "goal", "pain", "success_criteria", "priority", "constraints", "non_goals",
    }
    assert listing["items_by_field"]["pain"] == []


def test_create_intent_item_accepts_undecided_and_not_applicable(admin_client):
    """'undecided'/'not_applicable' are first-class answers, not errors."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    undecided = _create_intent(
        admin_client, headers, session_id,
        field="goal", value_text="現状把握だけが目的です。まだ解決策を決めていません",
        status="undecided",
    )
    assert undecided["status"] == "undecided"

    declined = _create_intent(
        admin_client, headers, session_id,
        field="priority", value_text="対象外", status="not_applicable",
    )
    assert declined["status"] == "not_applicable"


def test_create_intent_item_rejects_invalid_field(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "not-a-field", "value_text": "x"},
        headers=headers,
    )
    assert r.status_code == 422, r.text


def test_create_intent_item_rejects_invalid_status(admin_client):
    """'proposed'/'needs_review' are system/AI states, never user-chosen."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "x", "status": "proposed"},
        headers=headers,
    )
    assert r.status_code == 422, r.text


def test_create_intent_item_rejects_empty_value_text(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": ""},
        headers=headers,
    )
    assert r.status_code == 422, r.text


# --- Confirm / correct / decline lifecycle -----------------------------------


def _stub_propose(monkeypatch, *, items=None, error=None, is_mock=False):
    from app.routes import interview_intent as intent_routes
    from app.interview_intent_agent import IntentProposalItem, IntentProposalResult

    def fake_create_llm_client(config):
        return object()

    def fake_generate_intent_proposal(client, config, **kwargs):
        return IntentProposalResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=is_mock,
            items=[IntentProposalItem(**i) for i in (items or [])],
            error=error,
        )

    monkeypatch.setattr(intent_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        intent_routes, "generate_intent_proposal", fake_generate_intent_proposal
    )


def test_propose_creates_proposed_rows_not_confirmed(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_propose(
        monkeypatch,
        items=[
            {
                "field": "goal",
                "value_text": "トレース収集の効率化",
                "source_statement": "トレースを楽に集めたいんです",
            },
            {"field": "pain", "value_text": "手動確認が多い", "source_statement": None},
        ],
    )
    r = admin_client.post(
        f"/interview/sessions/{session_id}/intent/propose", headers=headers,
    )
    assert r.status_code == 201, r.text
    items = r.json()
    assert len(items) == 2
    for item in items:
        assert item["status"] == "proposed"
        assert item["origin"] == "ai_proposed"
        assert item["decision_method"] == "reasoning_llm"
        assert item["intelligence_run_id"] is not None
        assert item["is_mock"] is False

    # Still 'proposed' until an explicit confirm call -- listing must not
    # show it as confirmed.
    listing = admin_client.get(
        f"/interview/sessions/{session_id}/intent", headers=headers
    ).json()
    goal_items = listing["items_by_field"]["goal"]
    assert len(goal_items) == 1
    assert goal_items[0]["status"] == "proposed"


def test_propose_marks_is_mock_from_llm_result(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_propose(
        monkeypatch,
        items=[{"field": "goal", "value_text": "mock goal", "source_statement": None}],
        is_mock=True,
    )
    r = admin_client.post(
        f"/interview/sessions/{session_id}/intent/propose", headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()[0]["is_mock"] is True


def test_confirm_ai_proposed_item(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_propose(
        monkeypatch,
        items=[{"field": "goal", "value_text": "候補の目標", "source_statement": None}],
    )
    proposed = admin_client.post(
        f"/interview/sessions/{session_id}/intent/propose", headers=headers,
    ).json()[0]
    assert proposed["status"] == "proposed"

    r = admin_client.post(
        f"/interview/intent/{proposed['id']}/confirm", headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["decision_method"] == "manual"
    # origin is untouched -- provenance of who authored the *value* is kept.
    assert body["origin"] == "ai_proposed"


def test_confirm_already_confirmed_item_is_rejected(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item = _create_intent(admin_client, headers, session_id)

    r = admin_client.post(f"/interview/intent/{item['id']}/confirm", headers=headers)
    assert r.status_code == 409, r.text


def test_correct_creates_new_revision_and_links_old_row(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item = _create_intent(admin_client, headers, session_id, value_text="最初の目標")

    r = admin_client.post(
        f"/interview/intent/{item['id']}/correct",
        json={"value_text": "訂正した目標"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    corrected = r.json()
    assert corrected["id"] != item["id"]
    assert corrected["value_text"] == "訂正した目標"
    assert corrected["origin"] == "user"
    assert corrected["status"] == "confirmed"
    assert corrected["decision_method"] == "manual"

    # Current listing only shows the latest revision.
    listing = admin_client.get(
        f"/interview/sessions/{session_id}/intent", headers=headers
    ).json()
    assert [i["id"] for i in listing["items_by_field"]["goal"]] == [corrected["id"]]

    # History is retained and reachable via include_superseded.
    history = admin_client.get(
        f"/interview/sessions/{session_id}/intent?include_superseded=true",
        headers=headers,
    ).json()
    goal_ids = {i["id"]: i for i in history["items_by_field"]["goal"]}
    assert item["id"] in goal_ids
    assert goal_ids[item["id"]]["superseded_by_id"] == corrected["id"]
    assert goal_ids[item["id"]]["value_text"] == "最初の目標"  # never overwritten


def test_correct_a_superseded_item_is_rejected(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item = _create_intent(admin_client, headers, session_id)

    admin_client.post(
        f"/interview/intent/{item['id']}/correct",
        json={"value_text": "2回目"}, headers=headers,
    )
    r = admin_client.post(
        f"/interview/intent/{item['id']}/correct",
        json={"value_text": "3回目"}, headers=headers,
    )
    assert r.status_code == 409, r.text


def test_decline_marks_not_applicable_and_keeps_row(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item = _create_intent(admin_client, headers, session_id, field="constraints")

    r = admin_client.post(f"/interview/intent/{item['id']}/decline", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == item["id"]
    assert body["status"] == "not_applicable"
    assert body["decision_method"] == "manual"


# --- Fail-closed propose ------------------------------------------------------


def test_propose_fails_closed_on_llm_error_and_creates_no_rows(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_propose(monkeypatch, error="reasoning model call failed")
    r = admin_client.post(
        f"/interview/sessions/{session_id}/intent/propose", headers=headers,
    )
    assert r.status_code == 502, r.text

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/intent", headers=headers
    ).json()
    assert all(v == [] for v in listing["items_by_field"].values())

    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM intelligence_runs WHERE run_type = 'intent_proposal'"
        ).fetchone()
    assert run is not None
    assert run["status"] == "failed"
    assert run["error_details"] == "reasoning model call failed"
    assert run["decision_method"] == "reasoning_llm"
    assert run["prompt_version"] == "intent-brief-v1"


def test_propose_with_unconfigured_llm_fails_closed(admin_client, monkeypatch):
    """No stub applied: a real (unconfigured, no API key) client raises
    LLMError at construction time -- still fails closed with no rows."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("INTELLIGENCE_LLM_PROVIDER", raising=False)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/intent/propose", headers=headers,
    )
    assert r.status_code == 502, r.text

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/intent", headers=headers
    ).json()
    assert all(v == [] for v in listing["items_by_field"].values())


# --- System isolation ---------------------------------------------------------


def test_intent_isolation_across_systems(admin_client):
    token, system_a, snapshot_a = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")
    snapshot_b = _insert_snapshot(system_b["id"], commit_sha="bbb")

    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b["id"])

    session_a = _create_session(admin_client, headers_a, snapshot_a)
    item = _create_intent(admin_client, headers_a, session_a)

    assert admin_client.get(
        f"/interview/sessions/{session_a}/intent", headers=headers_b
    ).status_code == 404
    assert admin_client.post(
        f"/interview/intent/{item['id']}/confirm", headers=headers_b
    ).status_code == 404
    assert admin_client.post(
        f"/interview/intent/{item['id']}/correct",
        json={"value_text": "乗っ取り"}, headers=headers_b,
    ).status_code == 404
    assert admin_client.post(
        f"/interview/intent/{item['id']}/decline", headers=headers_b
    ).status_code == 404
    assert admin_client.post(
        f"/interview/sessions/{session_a}/intent/propose", headers=headers_b
    ).status_code == 404

    # System B can independently create its own session + item.
    session_b = _create_session(admin_client, headers_b, snapshot_b)
    item_b = _create_intent(admin_client, headers_b, session_b)
    listing_b = admin_client.get(
        f"/interview/sessions/{session_b}/intent", headers=headers_b
    ).json()
    assert [i["id"] for i in listing_b["items_by_field"]["goal"]] == [item_b["id"]]


# --- Migration / backward compatibility --------------------------------------


def test_migration_creates_interview_intent_item_table(admin_client):
    from app.db import get_conn, init_db

    init_db()
    with get_conn() as conn:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "interview_intent_item" in tables


def test_old_session_without_intent_rows_returns_empty_groups(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/intent", headers=headers
    ).json()
    assert all(v == [] for v in listing["items_by_field"].values())
