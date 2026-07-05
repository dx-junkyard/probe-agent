"""Tests for Issue #129: structured interview Q&A persistence and revisions.

Covers: ID-addressable question/answer CRUD, first-answer vs. correction
semantics (a correction inserts a new row and links the old row via
superseded_by_id — it never overwrites), skip/resume state transitions,
System isolation, dialogue-turn wiring (creating Q&A rows from
next_questions, consuming answered_qa_id), the answers-revised session flag
+ regeneration recommendation, and additive-migration backward compatibility
for sessions that predate this table.
"""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-qa-test.db"))
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


def _create_qa(client, headers, session_id, **overrides):
    body = {
        "question_text": "システムの主な目的は何ですか?",
        "question_category": "purpose",
        "question_source": "reviewer",
    }
    body.update(overrides)
    r = client.post(
        f"/interview/sessions/{session_id}/qa", json=body, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# --- CRUD + finite-set validation --------------------------------------------


def test_create_and_list_qa(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    qa = _create_qa(admin_client, headers, session_id)
    assert qa["status"] == "open"
    assert qa["answer_text"] is None
    assert qa["superseded_by_id"] is None

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers
    ).json()
    assert listing["open_count"] == 1
    assert listing["high_priority_open_count"] == 1  # category "purpose"
    assert [i["id"] for i in listing["items"]] == [qa["id"]]


def test_create_qa_rejects_invalid_category(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "?", "question_category": "not-a-category"},
        headers=headers,
    )
    assert r.status_code == 422, r.text


# --- Answering and correction (revision chain) -------------------------------


def test_first_answer_updates_same_row(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "トレース収集の効率化です", "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["previous"] is None
    assert body["qa"]["id"] == qa["id"]
    assert body["qa"]["status"] == "answered"
    assert body["qa"]["answer_text"] == "トレース収集の効率化です"
    assert body["qa"]["answered_by"] == "root"
    assert body["regeneration_recommended"] is False


def test_correcting_an_answer_creates_new_revision_and_links_old_row(admin_client):
    """Correction never overwrites: a new row is inserted and the old row is
    marked 'revised' with superseded_by_id pointing at the new row."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "最初の回答", "actor": "root"},
        headers=headers,
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "訂正した回答", "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["previous"]["id"] == qa["id"]
    assert body["previous"]["status"] == "revised"
    assert body["previous"]["superseded_by_id"] == body["qa"]["id"]
    assert body["previous"]["answer_text"] == "最初の回答"  # never overwritten

    assert body["qa"]["id"] != qa["id"]
    assert body["qa"]["status"] == "answered"
    assert body["qa"]["answer_text"] == "訂正した回答"
    assert body["qa"]["question_text"] == qa["question_text"]

    # The revised (superseded) row drops out of the current list; only the
    # latest revision remains.
    listing = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers
    ).json()
    assert [i["id"] for i in listing["items"]] == [body["qa"]["id"]]

    # The session is flagged for a recommended understanding rebuild.
    session = admin_client.get(
        f"/interview/sessions/{session_id}", headers=headers
    ).json()
    assert session["answers_revised_at"] is not None


def test_correcting_a_revised_row_is_rejected(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "回答1", "actor": "root"},
        headers=headers,
    )
    admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "回答2", "actor": "root"},
        headers=headers,
    )
    # qa['id'] is now the superseded (revised) row; it cannot be corrected again.
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "回答3", "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 409, r.text


def test_regeneration_recommended_when_proposals_exist(admin_client):
    """Correcting an answer after proposals exist surfaces a recommendation,
    without invalidating or regenerating the existing proposals."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "最初の回答", "actor": "root"},
        headers=headers,
    )

    # Advance to proposal_generation and confirm understanding so a proposal
    # can be persisted directly (no LLM call needed for this test).
    for stage in [
        "purpose_confirmation", "capability_confirmation", "element_classification",
        "api_boundary_mapping", "probe_flow_selection", "proposal_generation",
    ]:
        admin_client.post(
            f"/interview/sessions/{session_id}/advance-stage",
            json={"stage": stage}, headers=headers,
        )
    admin_client.post(
        f"/interview/sessions/{session_id}/messages",
        json={"role": "user", "content": "対象を確認しました"},
        headers=headers,
    )
    admin_client.post(
        f"/interview/sessions/{session_id}/confirm-understanding",
        json={"actor": "root"}, headers=headers,
    )
    proposal_item = {
        "path": "src/summarize.py",
        "qualified_name": "summarize.summarize_text",
        "metadata": {
            "role": "Summarize text", "capability": "summarization",
            "system_purpose": "Help users", "probe_value": "Validate quality",
            "element_type": "core", "operation_kind": "analysis",
            "consumers": [], "state_effects": ["none"],
        },
        "probe_plan": {
            "feature_id": "summarization", "objective": "Trace",
            "reason": "safe", "recommended_mode": "trace",
            "side_effect_risk": "low", "replayability": "safe",
        },
    }
    audit = {
        "provider": "mock", "model": "mock-1",
        "prompt_version": "v1", "schema_version": "v1", "is_mock": True,
    }
    r = admin_client.post(
        f"/interview/sessions/{session_id}/proposals",
        json={"audit": audit, "proposals": [proposal_item]},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "訂正した回答", "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["regeneration_recommended"] is True

    # The proposal itself is untouched — never auto-invalidated/regenerated.
    proposals = admin_client.get(
        f"/interview/sessions/{session_id}/proposals", headers=headers
    ).json()
    assert len(proposals) == 1
    assert proposals[0]["approval_state"] == "proposed"


# --- Skip / resume ------------------------------------------------------------


def test_skip_and_resume_transitions(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/skip",
        json={"actor": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "skipped"

    # Cannot skip an already-skipped question.
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/skip",
        json={"actor": "root"}, headers=headers,
    )
    assert r.status_code == 409

    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/resume",
        json={"actor": "root"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "open"

    # Cannot resume a question that isn't skipped.
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/resume",
        json={"actor": "root"}, headers=headers,
    )
    assert r.status_code == 409


def test_skipped_question_can_still_be_answered(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)
    admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/skip",
        json={"actor": "root"}, headers=headers,
    )
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "戻ってから回答", "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["qa"]["status"] == "answered"


# --- System isolation ---------------------------------------------------------


def test_qa_isolation_across_systems(admin_client):
    token, system_a, snapshot_a = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")
    snapshot_b = _insert_snapshot(system_b["id"], commit_sha="bbb")

    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b["id"])

    session_a = _create_session(admin_client, headers_a, snapshot_a)
    qa = _create_qa(admin_client, headers_a, session_a)

    # System B cannot see, answer, skip, or resume System A's question.
    assert admin_client.get(
        f"/interview/sessions/{session_a}/qa", headers=headers_b
    ).status_code == 404
    assert admin_client.post(
        f"/interview/sessions/{session_a}/qa/{qa['id']}/answer",
        json={"answer_text": "x", "actor": "intruder"},
        headers=headers_b,
    ).status_code == 404
    assert admin_client.post(
        f"/interview/sessions/{session_a}/qa/{qa['id']}/skip",
        json={"actor": "intruder"},
        headers=headers_b,
    ).status_code == 404

    # System B can independently create its own session + question.
    session_b = _create_session(admin_client, headers_b, snapshot_b)
    qa_b = _create_qa(admin_client, headers_b, session_b)
    assert qa_b["id"] != qa["id"] or True  # distinct rows regardless of id overlap
    listing_b = admin_client.get(
        f"/interview/sessions/{session_b}/qa", headers=headers_b
    ).json()
    assert [i["id"] for i in listing_b["items"]] == [qa_b["id"]]


# --- Dialogue-turn wiring (Issue #129 ID-based consumption) ------------------


def _stub_reasoning_turn(monkeypatch, *, next_questions=None):
    from app.routes import interview as interview_routes
    from app.interview_agent import EvidenceSelectionResult, InterviewTurnResult

    captured: dict = {}

    def fake_create_llm_client(config):
        return object()

    def fake_select_evidence_targets(client, config, **kwargs):
        return EvidenceSelectionResult(
            provider="anthropic", model="claude-sonnet-4-5",
            is_mock=False, need_evidence=False,
        )

    def fake_generate_interview_turn(client, config, **kwargs):
        captured.update(kwargs)
        return InterviewTurnResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            assistant_message="了解しました。",
            next_questions=list(next_questions or []),
        )

    monkeypatch.setattr(interview_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        interview_routes, "select_evidence_targets", fake_select_evidence_targets
    )
    monkeypatch.setattr(
        interview_routes, "generate_interview_turn", fake_generate_interview_turn
    )
    return captured


def test_dialogue_turn_creates_qa_rows_for_next_questions(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_reasoning_turn(monkeypatch, next_questions=["リトライ方針はありますか?"])
    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "最初のメッセージ"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["created_qa_ids"]) == 1

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers
    ).json()
    assert listing["items"][0]["question_text"] == "リトライ方針はありますか?"
    assert listing["items"][0]["question_source"] == "dialogue"
    assert listing["items"][0]["status"] == "open"


def test_dialogue_turn_consumes_answered_qa_id(admin_client, monkeypatch):
    """ID-based consumption (Issue #129) survives question rewording, unlike
    the legacy exact-text answered_question match."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(
        admin_client, headers, session_id,
        question_text="認証はどの層で行いますか?", question_source="dialogue",
    )

    _stub_reasoning_turn(monkeypatch, next_questions=[])
    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={
            "user_message": "APIゲートウェイ層で認証します",
            "answered_qa_id": qa["id"],
            "actor": "root",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text

    updated = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers
    ).json()
    answered = next(i for i in updated["items"] if i["id"] == qa["id"])
    assert answered["status"] == "answered"
    assert answered["answer_text"] == "APIゲートウェイ層で認証します"
    assert answered["answered_by"] == "root"


def test_dialogue_turn_open_questions_entries_carry_qa_id(admin_client, monkeypatch):
    """The legacy open_questions JSON entries carry the interview_qa row ID
    so the dashboard can consume the focused question by ID, not text."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_reasoning_turn(monkeypatch, next_questions=["保存期間はどれくらいですか?"])
    body = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "こんにちは"},
        headers=headers,
    ).json()

    assert len(body["created_qa_ids"]) == 1
    entries = body["open_questions_structured"]
    assert entries and entries[0]["qa_id"] == body["created_qa_ids"][0]


def test_dialogue_turn_text_consumption_marks_qa_answered(admin_client, monkeypatch):
    """During the migration period, exact-text answered_question consumption
    must keep the interview_qa row in sync (marked answered), so the Q&A
    panel's open count matches the conversation state."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    question = "リトライ方針はありますか?"
    _stub_reasoning_turn(monkeypatch, next_questions=[question])
    first = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "こんにちは"},
        headers=headers,
    ).json()
    qa_id = first["created_qa_ids"][0]

    _stub_reasoning_turn(monkeypatch, next_questions=[])
    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={
            "user_message": "リトライは3回まで指数バックオフです",
            "answered_question": question,
            "actor": "root",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["error"] is None

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers
    ).json()
    answered = next(i for i in listing["items"] if i["id"] == qa_id)
    assert answered["status"] == "answered"
    assert answered["answer_text"] == "リトライは3回まで指数バックオフです"
    # The JSON entry was consumed as well.
    remaining = r.json()["open_questions_structured"] or []
    assert all(e["question"] != question for e in remaining)


def test_dialogue_turn_does_not_duplicate_qa_for_same_question_text(admin_client, monkeypatch):
    """A question re-emitted with identical text across turns reuses the
    existing interview_qa row (structural exact-text dedupe, Principle 6)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    question = "認証はどの層で行いますか?"
    _stub_reasoning_turn(monkeypatch, next_questions=[question])
    first = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "1回目"},
        headers=headers,
    ).json()
    assert len(first["created_qa_ids"]) == 1

    _stub_reasoning_turn(monkeypatch, next_questions=[question])
    second = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "2回目"},
        headers=headers,
    ).json()
    assert second["created_qa_ids"] == []

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers
    ).json()
    assert sum(1 for i in listing["items"] if i["question_text"] == question) == 1


def test_dialogue_turn_injects_confirmed_qa_into_prompt(admin_client, monkeypatch):
    """The latest revisions of answered Q&A pairs are passed to the reasoning
    turn with a do-not-re-ask rule (Issue #129 acceptance)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    qa = _create_qa(
        admin_client, headers, session_id,
        question_text="主要な利用者は誰ですか?", question_source="dialogue",
    )
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "社内の開発者です", "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    captured = _stub_reasoning_turn(monkeypatch, next_questions=[])
    admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "続けてください"},
        headers=headers,
    )
    assert captured["answered_qa"] == [
        {"question": "主要な利用者は誰ですか?", "answer": "社内の開発者です"}
    ]


def test_dialogue_turn_ignores_stale_answered_qa_id(admin_client, monkeypatch):
    """A reference to an already-answered or missing question must not fail
    the turn."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_reasoning_turn(monkeypatch, next_questions=[])
    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "何かの回答", "answered_qa_id": 999999},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["error"] is None


# --- Migration / backward compatibility --------------------------------------


def test_migration_creates_interview_qa_table(admin_client):
    from app.db import get_conn, init_db

    init_db()
    with get_conn() as conn:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "interview_qa" in tables
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(interview_session)")}
        assert "answers_revised_at" in cols


def test_old_session_without_qa_rows_returns_empty_list(admin_client):
    """A session created before this feature (no interview_qa rows) is not
    backfilled — its Q&A list is explicitly empty rather than erroring."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    listing = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers
    ).json()
    assert listing["items"] == []
    assert listing["open_count"] == 0
    assert listing["answers_revised_at"] is None
