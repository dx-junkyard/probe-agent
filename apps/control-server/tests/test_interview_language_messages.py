"""Tests for Issue #138: server fixed-text messages follow INTERVIEW_LANGUAGE.

Covers:
1. INTERVIEW_MESSAGES table completeness: every key has ja and en entries.
2. interview_message() is table lookup only (no translation/inference).
3. INTERVIEW_LANGUAGE=en renders update-understanding failure/success and
   confirm-understanding messages in English.
4. Default ja is preserved (existing Japanese assertions keep passing).
5. An invalid INTERVIEW_LANGUAGE falls back to ja for fixed-text messages
   only, without crashing message composition; the reasoning-required
   review call still fails closed through get_interview_language().
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.interview_language import (
    INTERVIEW_MESSAGES,
    SUPPORTED_INTERVIEW_LANGUAGES,
    interview_message,
    resolve_message_language,
)


# --- Table completeness and lookup -------------------------------------------


def test_every_message_key_defined_for_every_supported_language():
    for key, table in INTERVIEW_MESSAGES.items():
        for language in SUPPORTED_INTERVIEW_LANGUAGES:
            assert language in table, f"{key!r} missing {language!r} entry"
            assert table[language], f"{key!r}/{language!r} is empty"


def test_interview_message_formats_placeholders():
    assert interview_message("understanding_update_failed", "ja", error="boom") == (
        "理解の更新に失敗しました: boom"
    )
    assert interview_message("understanding_update_failed", "en", error="boom") == (
        "Failed to update understanding: boom"
    )


def test_resolve_message_language_defaults_to_ja(monkeypatch):
    monkeypatch.delenv("INTERVIEW_LANGUAGE", raising=False)
    assert resolve_message_language() == "ja"


def test_resolve_message_language_respects_en(monkeypatch):
    monkeypatch.setenv("INTERVIEW_LANGUAGE", "en")
    assert resolve_message_language() == "en"


def test_resolve_message_language_falls_back_to_ja_on_invalid_config(monkeypatch):
    """Fixed-text composition must never crash just because INTERVIEW_LANGUAGE
    is misconfigured — that failure is reported through the reasoning-call
    path (get_interview_language), not by breaking the failure message
    itself."""
    monkeypatch.setenv("INTERVIEW_LANGUAGE", "fr")
    assert resolve_message_language() == "ja"


# --- Route-level: end-to-end message language ---------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-lang-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _setup(client):
    token = _login(client)
    system = client.post(
        "/systems", json={"name": "Sys", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
            VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
            (system["id"], now, now),
        )
        snapshot_id = cur.lastrowid
    return token, system["id"], snapshot_id


def _insert_understanding_graph(system_id, snapshot_id):
    from app.documentation_claim_scanner import ChunkScanResult, ClaimEvidence, DocumentationClaim
    from app.understanding_graph import build_understanding_graph, save_graph_snapshot
    from app.db import get_conn

    result = ChunkScanResult(
        chunk_id="chunk-1",
        chunk_content_hash="hash-1",
        prompt_version="claim-scanner-v1",
        schema_version="claim-scanner-v1",
        claims=[
            DocumentationClaim(
                claim_type="system_purpose",
                summary="System helps inspect probe agent repositories",
                evidence=ClaimEvidence(path="README.md", start_line=1, end_line=5),
                confidence=0.9,
            )
        ],
    )
    graph = build_understanding_graph([result])
    with get_conn() as conn:
        return save_graph_snapshot(conn, system_id, graph, snapshot_id=snapshot_id)


def test_graph_not_built_message_is_english_when_configured(admin_client, monkeypatch):
    monkeypatch.setenv("INTERVIEW_LANGUAGE", "en")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()

    # No understanding graph built for this snapshot.
    r = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding", headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "has not been built yet" in body["last_error"]
    assert "未構築" not in body["last_error"]

    detail = admin_client.get(f"/interview/sessions/{session['id']}", headers=headers).json()
    assert "Failed to update understanding" in detail["messages"][-1]["content"]
    assert "has not been built yet" in detail["messages"][-1]["content"]


def test_graph_not_built_message_is_japanese_by_default(admin_client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "o3-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding", headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "理解グラフが未構築です" in body["last_error"]

    detail = admin_client.get(f"/interview/sessions/{session['id']}", headers=headers).json()
    assert "理解の更新に失敗しました" in detail["messages"][-1]["content"]


def test_confirm_understanding_message_is_english_when_configured(admin_client, monkeypatch):
    monkeypatch.setenv("INTERVIEW_LANGUAGE", "en")
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()
    admin_client.post(
        f"/interview/sessions/{session['id']}/messages",
        json={"role": "user", "content": "purpose confirmed"},
        headers=headers,
    )

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/confirm-understanding",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    detail = admin_client.get(f"/interview/sessions/{session['id']}", headers=headers).json()
    assert detail["messages"][-1]["content"] == (
        "Confirmed the answers so far; proceeding to proposal generation."
    )


def test_confirm_understanding_message_is_japanese_by_default(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()
    admin_client.post(
        f"/interview/sessions/{session['id']}/messages",
        json={"role": "user", "content": "目的を確認しました"},
        headers=headers,
    )

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/confirm-understanding",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    detail = admin_client.get(f"/interview/sessions/{session['id']}", headers=headers).json()
    assert detail["messages"][-1]["content"] == "これまでの回答内容を確定し、提案生成に進みます。"


def test_invalid_interview_language_still_fails_closed_via_review(admin_client, monkeypatch):
    """An invalid INTERVIEW_LANGUAGE must still fail the reasoning-required
    review closed (unchanged from Issue #127), while the wrapping failure
    message itself is composed without crashing (falls back to ja)."""
    monkeypatch.setenv("INTERVIEW_LANGUAGE", "fr")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    token, system_id, snapshot_id = _setup(admin_client)
    _insert_understanding_graph(system_id, snapshot_id)
    headers = _headers(token, system_id)
    session = admin_client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()

    r = admin_client.post(
        f"/interview/sessions/{session['id']}/update-understanding", headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["last_error"]
    assert "INTERVIEW_LANGUAGE" in body["last_error"]

    detail = admin_client.get(f"/interview/sessions/{session['id']}", headers=headers).json()
    # The wrapping message is composed in the ja fallback, not raising.
    assert detail["messages"][-1]["content"].startswith("理解のレビューに失敗しました")
