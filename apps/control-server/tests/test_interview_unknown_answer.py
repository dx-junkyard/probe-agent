"""Tests for Issue #142: "I don't know" answers and graceful evidence fallback.

Covers the two intertwined behaviors from the issue:

1. A dialogue turn where the developer answered "わからない"/"I don't know"
   (``answer_unknown``) does NOT fail. The consumed Q&A row is recorded as
   ``unconfirmed`` (valid input, not an error), and that row is fed back into
   the next turn's reasoning context as an open hypothesis to re-confirm —
   never as an established fact.
2. The dedicated Q&A answer endpoint records an ``answer_unknown`` answer as
   ``unconfirmed`` (with a possibly-blank answer_text), instead of ``answered``.

The graceful drop of unverifiable question ``evidence_refs`` (so a bad line
range never stops the interview) is unit-tested in test_interview_agent.py.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-unknown-answer-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _setup_session(client):
    token = _login(client)
    system = client.post(
        "/systems", json={"name": "Sys", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    headers = _headers(token, system["id"])

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system["id"], "/nonexistent/repo", "abc123", now, now),
        )
        snapshot_id = cur.lastrowid

    session = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()
    return headers, session["id"], snapshot_id


def _stub_two_pass(monkeypatch, *, turn_result, capture=None):
    from app.interview_agent import EvidenceSelectionResult
    from app.routes import interview as interview_routes

    evidence_result = EvidenceSelectionResult(
        provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
        need_evidence=False,
    )

    def fake_create_llm_client(config):
        return object()

    def fake_select_evidence_targets(client, config, **kwargs):
        return evidence_result

    def fake_generate_interview_turn(client, config, **kwargs):
        if capture is not None:
            capture.update(kwargs)
        return turn_result

    monkeypatch.setattr(interview_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(
        interview_routes, "select_evidence_targets", fake_select_evidence_targets
    )
    monkeypatch.setattr(
        interview_routes, "generate_interview_turn", fake_generate_interview_turn
    )


# --- Dialogue-turn "I don't know" -------------------------------------------


def test_unknown_dialogue_answer_records_unconfirmed_not_error(admin_client, monkeypatch):
    from app.interview_agent import InterviewTurnResult

    headers, session_id, _ = _setup_session(admin_client)

    # Register an open question the developer will answer with "I don't know".
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "この関数の役割は?", "question_category": "general"},
        headers=headers,
    ).json()
    assert qa["status"] == "open"

    _stub_two_pass(
        monkeypatch,
        turn_result=InterviewTurnResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            assistant_message="承知しました。仮説を立てて確認します。",
        ),
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={
            "user_message": "わかりません",
            "answered_qa_id": qa["id"],
            "answer_unknown": True,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["error"] is None

    # The consumed row is recorded as 'unconfirmed', not 'answered'.
    qa_list = admin_client.get(
        f"/interview/sessions/{session_id}/qa", headers=headers,
    ).json()
    row = next(i for i in qa_list["items"] if i["id"] == qa["id"])
    assert row["status"] == "unconfirmed"
    assert row["answer_text"] == "わかりません"
    # Unconfirmed rows are not counted as open (they are addressed), and are
    # not answered/confirmed facts either.
    assert row["id"] not in [i["id"] for i in qa_list["items"] if i["status"] == "answered"]


def test_unknown_answer_injected_into_same_turn_context(admin_client, monkeypatch):
    """P1: an explicit "I don't know" answer must reach the SAME turn's LLM
    prompt as structured input, not only the next turn. The question being
    answered is still 'open' at prompt-build time, so the route injects it
    into unconfirmed_qa for this turn from the answer_unknown flag."""
    from app.interview_agent import InterviewTurnResult

    headers, session_id, _ = _setup_session(admin_client)

    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "この関数の役割は?", "question_category": "general"},
        headers=headers,
    ).json()

    capture: dict = {}
    _stub_two_pass(
        monkeypatch,
        turn_result=InterviewTurnResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            assistant_message="仮説を立てます",
        ),
        capture=capture,
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={
            "user_message": "わかりません",
            "answered_qa_id": qa["id"],
            "answer_unknown": True,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    # The current (still-open) question is present in THIS turn's context.
    unconfirmed = capture.get("unconfirmed_qa")
    assert unconfirmed is not None
    assert any(u["question"] == "この関数の役割は?" for u in unconfirmed)


def test_unconfirmed_qa_fed_back_to_next_turn_context(admin_client, monkeypatch):
    from app.interview_agent import InterviewTurnResult

    headers, session_id, _ = _setup_session(admin_client)

    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "この関数の役割は?", "question_category": "general"},
        headers=headers,
    ).json()

    _stub_two_pass(
        monkeypatch,
        turn_result=InterviewTurnResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            assistant_message="OK",
        ),
    )
    admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "不明です", "answered_qa_id": qa["id"], "answer_unknown": True},
        headers=headers,
    )

    # The next turn must receive the unconfirmed row in its reasoning context.
    capture: dict = {}
    _stub_two_pass(
        monkeypatch,
        turn_result=InterviewTurnResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            assistant_message="続けます",
        ),
        capture=capture,
    )
    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "続けてください"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    unconfirmed = capture.get("unconfirmed_qa")
    assert unconfirmed is not None
    assert any(u["question"] == "この関数の役割は?" for u in unconfirmed)


def test_evidence_refs_dropped_surfaced_in_response(admin_client, monkeypatch):
    """The dropped-ref count is exposed on the turn response for operator
    visibility (auditability), not just kept on the internal object."""
    from app.interview_agent import InterviewTurnResult

    headers, session_id, _ = _setup_session(admin_client)

    _stub_two_pass(
        monkeypatch,
        turn_result=InterviewTurnResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            assistant_message="OK", evidence_refs_dropped=2,
        ),
    )

    r = admin_client.post(
        f"/interview/sessions/{session_id}/dialogue-turn",
        json={"user_message": "続けてください"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["evidence_refs_dropped"] == 2


# --- Q&A answer endpoint "I don't know" -------------------------------------


def test_answer_endpoint_marks_unconfirmed(admin_client):
    headers, session_id, _ = _setup_session(admin_client)

    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "APIの境界は?", "question_category": "api"},
        headers=headers,
    ).json()

    # Blank answer_text is allowed when answer_unknown is set.
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "", "actor": "dev", "answer_unknown": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qa"]["status"] == "unconfirmed"
    assert body["qa"]["answered_by"] == "dev"


def test_answer_endpoint_requires_text_when_not_unknown(admin_client):
    headers, session_id, _ = _setup_session(admin_client)

    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "APIの境界は?", "question_category": "api"},
        headers=headers,
    ).json()

    # A normal (confirming) answer still requires non-empty text.
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa/{qa['id']}/answer",
        json={"answer_text": "  ", "actor": "dev"},
        headers=headers,
    )
    assert r.status_code == 422, r.text
