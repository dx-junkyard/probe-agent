"""Tests for Epic #328 Phase C / Issue #331: translation and option presentation.

Covers:

1. ``app/understanding_translator.py``: mandatory finding-level traceability
   (a statement citing an unsupplied id fails the WHOLE call closed), the
   restricted translation claim kinds (a translation never asserts a new
   fact), the finite statement layers, and fail-closed on mock /
   non-reasoning / malformed output / no findings at all.
2. The deterministic parts: the action menu comes from the fixed server
   catalog in ``ACTION_KINDS`` order (never model output), and the
   ask-the-developer gate only fires when the developer has material to
   decide with -- remaining unknowns are a reason to investigate, not to ask.
3. The API: translated sentences are persisted as
   ``origin_role='translation'`` findings carrying ``supports_finding_ids``,
   a failure persists only the failed audit row, and the origin confirmation
   item is never written.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.joint_understanding import ACTION_KINDS
from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from app.understanding_translator import (
    FindingSummary,
    TranslatedStatement,
    ask_developer,
    build_action_menu,
    translate_findings,
)


def _make_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


def _mock_config():
    return LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30)


class FakeLLMClient(LLMClient):
    def __init__(self, response: Any = None, error: Optional[str] = None):
        self._response = response
        self._error = error
        self.prompts: List[str] = []

    def generate_text(self, messages, *, temperature=None, max_tokens=None) -> str:
        self.prompts.append(next((m["content"] for m in messages if m["role"] == "user"), ""))
        if self._error:
            raise LLMError(self._error)
        if isinstance(self._response, str):
            return self._response
        return json.dumps(self._response)


def _findings() -> List[FindingSummary]:
    return [
        FindingSummary(
            id=11, origin_role="investigation", claim_kind="fact",
            statement="リトライは3回で打ち切られる", evidence_count=1,
        ),
        FindingSummary(
            id=12, origin_role="investigation", claim_kind="unknown",
            statement="打ち切り後の通知先は特定できなかった",
        ),
    ]


def _response(**overrides) -> Dict[str, Any]:
    base = {
        "purpose_summary": "この処理は、外部が一時的に落ちても利用者の操作を失わないためのものです。",
        "statements": [
            {
                "layer": "impact", "claim_kind": "inference",
                "text": "3回失敗すると利用者には失敗として見えます",
                "supports_finding_ids": [11],
            },
        ],
        "options": [],
        "open_unknowns": ["通知先が未確認です"],
        "decision_question": None,
    }
    base.update(overrides)
    return base


# --- Traceability is mandatory --------------------------------------------------


def test_statement_citing_an_unsupplied_finding_fails_the_whole_call():
    client = FakeLLMClient(_response(statements=[
        {
            "layer": "impact", "claim_kind": "inference", "text": "根拠不明の説明",
            "supports_finding_ids": [999],
        },
    ]))
    result = translate_findings(
        client, _make_config(), question="影響は?", findings=_findings(),
    )
    assert result.error is not None
    assert result.statements == []
    assert result.purpose_summary == ""


def test_statement_without_any_reference_fails_closed():
    client = FakeLLMClient(_response(statements=[
        {"layer": "purpose", "claim_kind": "inference", "text": "出典のない一般論"},
    ]))
    result = translate_findings(
        client, _make_config(), question="影響は?", findings=_findings(),
    )
    assert result.error is not None
    assert result.statements == []


def test_option_citing_an_unsupplied_finding_fails_closed():
    client = FakeLLMClient(_response(options=[
        {"label": "案A", "what_changes": "…", "supports_finding_ids": [42]},
    ]))
    result = translate_findings(
        client, _make_config(), question="影響は?", findings=_findings(),
    )
    assert result.error is not None


def test_option_without_any_reference_fails_closed():
    client = FakeLLMClient(_response(options=[
        {"label": "案A", "what_changes": "挙動が変わる", "supports_finding_ids": []},
    ]))
    result = translate_findings(
        client, _make_config(), question="影響は?", findings=_findings(),
    )
    assert result.error is not None


def test_translation_cannot_assert_a_new_fact_or_hypothesis():
    for claim_kind in ("fact", "hypothesis"):
        client = FakeLLMClient(_response(statements=[
            {
                "layer": "gap", "claim_kind": claim_kind, "text": "新しい主張",
                "supports_finding_ids": [11],
            },
        ]))
        result = translate_findings(
            client, _make_config(), question="影響は?", findings=_findings(),
        )
        assert result.error is not None, claim_kind
        assert "may not claim" in result.error


def test_unknown_statement_layer_fails_closed():
    client = FakeLLMClient(_response(statements=[
        {
            "layer": "conclusion", "claim_kind": "inference", "text": "…",
            "supports_finding_ids": [11],
        },
    ]))
    result = translate_findings(
        client, _make_config(), question="影響は?", findings=_findings(),
    )
    assert result.error is not None
    assert "layer" in result.error


def test_valid_translation_keeps_its_references():
    client = FakeLLMClient(_response())
    result = translate_findings(
        client, _make_config(), question="影響は?", findings=_findings(),
    )
    assert result.error is None
    assert result.statements[0].supports_finding_ids == [11]
    assert result.open_unknowns == ["通知先が未確認です"]
    # The prompt is built from findings only -- no snapshot excerpts are
    # handed to the translation step, so it cannot invent a citation.
    assert "リトライは3回で打ち切られる" in client.prompts[0]
    assert "#11" in client.prompts[0]


# --- Fail-closed -----------------------------------------------------------------


def test_translation_fails_closed_on_mock_and_non_reasoning():
    assert translate_findings(
        MockLLMClient(), _mock_config(), question="q", findings=_findings(),
    ).error is not None
    assert translate_findings(
        FakeLLMClient(_response()), _make_config(model="gpt-4o-mini"),
        question="q", findings=_findings(),
    ).error is not None


def test_translation_without_findings_fails_closed():
    result = translate_findings(
        FakeLLMClient(_response()), _make_config(), question="q", findings=[],
    )
    assert result.error is not None
    assert "no findings" in result.error


def test_translation_fails_closed_on_llm_error_and_bad_json():
    assert translate_findings(
        FakeLLMClient(error="boom"), _make_config(), question="q", findings=_findings(),
    ).error == "boom"
    assert "parse" in translate_findings(
        FakeLLMClient("not json"), _make_config(), question="q", findings=_findings(),
    ).error


# --- Deterministic assembly -------------------------------------------------------


def test_action_menu_is_fixed_server_copy_in_finite_order():
    menu = build_action_menu("ja")
    assert [entry.action_kind for entry in menu] == list(ACTION_KINDS)
    assert all(entry.label and entry.what_changes for entry in menu)
    # Same input, same menu.
    assert [e.label for e in build_action_menu("ja")] == [e.label for e in menu]
    # Both configured languages resolve.
    assert [entry.action_kind for entry in build_action_menu("en")] == list(ACTION_KINDS)


def test_provisional_adoption_menu_entry_says_it_is_not_a_fact():
    entry = next(e for e in build_action_menu("ja") if e.action_kind == "adopt_hypothesis")
    assert "暫定" in entry.what_changes
    english = next(e for e in build_action_menu("en") if e.action_kind == "adopt_hypothesis")
    assert "PROVISIONAL" in english.what_changes


def test_ask_developer_gate():
    decision_statement = TranslatedStatement(
        layer="decision", claim_kind="inference", text="…", supports_finding_ids=[11],
    )
    impact_statement = TranslatedStatement(
        layer="impact", claim_kind="inference", text="…", supports_finding_ids=[11],
    )
    # No question -> never ask.
    assert ask_developer(
        statements=[decision_statement], decision_question=None, open_unknowns=[],
    ) is False
    # A question with decision material -> ask.
    assert ask_developer(
        statements=[decision_statement], decision_question="どちらを優先しますか?",
        open_unknowns=["まだ不明な点"],
    ) is True
    # A question with no decision material and things still investigable ->
    # do not hand it back; investigate instead.
    assert ask_developer(
        statements=[impact_statement], decision_question="どちらを優先しますか?",
        open_unknowns=["まだ調べられる"],
    ) is False
    assert ask_developer(
        statements=[impact_statement], decision_question="どちらを優先しますか?",
        open_unknowns=[],
    ) is True


# --- Route-level ------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ju-translation-test.db"))
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


def _setup(client):
    token = _login(client)
    system = client.post(
        "/systems", json={"name": "Sys", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    headers = _headers(token, system["id"])

    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        snapshot_id = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
            (system["id"], now, now),
        ).lastrowid
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model, prompt_version,
                 schema_version, decision_method, status, is_mock, started_at)
               VALUES (?, ?, 'joint_investigation', 'anthropic', 'claude', 'p', 's',
                       'reasoning_llm', 'completed', 0, ?)""",
            (system["id"], snapshot_id, now),
        ).lastrowid

    session_id = client.post(
        "/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers,
    ).json()["id"]
    qa = client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "リトライ回数の根拠は?"}, headers=headers,
    ).json()
    ju_id = client.post(
        f"/interview/sessions/{session_id}/joint-understanding",
        json={
            "origin_kind": "qa", "origin_id": qa["id"], "trigger": "unknown_answer",
            "question_text": "リトライ回数の根拠が分かりません",
        },
        headers=headers,
    ).json()["session"]["id"]

    finding_id = client.post(
        f"/joint-understanding/{ju_id}/findings",
        json={
            "origin_role": "investigation", "claim_kind": "fact",
            "statement": "リトライは3回で打ち切られる",
            "evidence": [{"path": "app/retry.py", "start_line": 1, "end_line": 5}],
            "decision_method": "reasoning_llm", "intelligence_run_id": run_id,
        },
        headers=headers,
    ).json()["id"]
    return headers, system["id"], qa, ju_id, finding_id


def _stub_translation(monkeypatch, response=None, error=None):
    from app.routes import joint_understanding as ju_routes

    client = FakeLLMClient(response, error=error)
    monkeypatch.setattr(ju_routes, "create_llm_client", lambda config: client)
    monkeypatch.setattr(
        ju_routes, "LLMConfig",
        type("_C", (), {"intelligence_from_env": staticmethod(lambda: _make_config())}),
    )
    return client


def _qa_row(qa_id):
    from app.db import get_conn

    with get_conn() as conn:
        return dict(conn.execute("SELECT * FROM interview_qa WHERE id = ?", (qa_id,)).fetchone())


def test_translate_persists_statements_as_translation_findings(admin_client, monkeypatch):
    headers, system_id, qa, ju_id, finding_id = _setup(admin_client)
    before = _qa_row(qa["id"])
    _stub_translation(monkeypatch, {
        "purpose_summary": "外部が落ちても利用者の操作を失わないための処理です。",
        "statements": [
            {
                "layer": "impact", "claim_kind": "inference",
                "text": "3回失敗すると利用者には失敗として見えます",
                "supports_finding_ids": [finding_id],
            },
            {
                "layer": "decision", "claim_kind": "inference",
                "text": "回数を増やすか、失敗を早く見せるかの判断です",
                "supports_finding_ids": [finding_id],
            },
        ],
        "options": [
            {
                "label": "現状維持", "what_changes": "利用者は3回目まで待ちます",
                "tradeoffs": "遅延が残ります", "supports_finding_ids": [finding_id],
            },
        ],
        "open_unknowns": [],
        "decision_question": "遅延と成功率のどちらを優先しますか?",
    })

    r = admin_client.post(f"/joint-understanding/{ju_id}/translate", json={}, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    translation = body["translation"]
    assert translation["purpose_summary"].startswith("3回失敗すると")
    assert [s["layer"] for s in translation["statements"]] == ["impact", "decision"]
    assert all(s["supports_finding_ids"] == [finding_id] for s in translation["statements"])
    assert translation["ask_developer"] is True
    assert translation["decision_question"]
    assert [e["action_kind"] for e in body["action_menu"]] == list(ACTION_KINDS)

    detail = admin_client.get(f"/joint-understanding/{ju_id}", headers=headers).json()
    translated = [f for f in detail["findings"] if f["origin_role"] == "translation"]
    assert len(translated) == 2
    assert all(f["decision_method"] == "reasoning_llm" for f in translated)
    assert all(f["evidence"] == [] for f in translated)
    assert all(f["supports_finding_ids"] == [finding_id] for f in translated)
    # Every persisted statement points at its translation finding row.
    assert {s["finding_id"] for s in translation["statements"]} == {f["id"] for f in translated}
    assert len(detail["translations"]) == 1

    assert _qa_row(qa["id"]) == before


def test_translation_failure_persists_only_the_failed_audit_row(admin_client, monkeypatch):
    headers, system_id, qa, ju_id, finding_id = _setup(admin_client)
    _stub_translation(monkeypatch, {
        "purpose_summary": "…",
        "statements": [
            {
                "layer": "impact", "claim_kind": "inference", "text": "でっち上げ",
                "supports_finding_ids": [9_999],
            },
        ],
        "options": [], "open_unknowns": [], "decision_question": None,
    })

    r = admin_client.post(f"/joint-understanding/{ju_id}/translate", json={}, headers=headers)
    assert r.status_code == 502, r.text

    from app.db import get_conn

    with get_conn() as conn:
        translations = conn.execute(
            "SELECT COUNT(*) AS n FROM joint_understanding_translation "
            "WHERE joint_understanding_id = ?",
            (ju_id,),
        ).fetchone()
        assert translations["n"] == 0
        translated_findings = conn.execute(
            "SELECT COUNT(*) AS n FROM joint_understanding_finding "
            "WHERE joint_understanding_id = ? AND origin_role = 'translation'",
            (ju_id,),
        ).fetchone()
        assert translated_findings["n"] == 0
        run = conn.execute(
            "SELECT * FROM intelligence_runs WHERE run_type = 'joint_translation' "
            "AND system_id = ?",
            (system_id,),
        ).fetchone()
        assert run["status"] == "failed"
        assert run["error_details"]


def test_translate_without_findings_fails_closed(admin_client, monkeypatch):
    headers, system_id, qa, ju_id, finding_id = _setup(admin_client)

    from app.db import get_conn

    with get_conn() as conn:
        conn.execute(
            "DELETE FROM joint_understanding_finding WHERE joint_understanding_id = ?",
            (ju_id,),
        )

    _stub_translation(monkeypatch, {
        "purpose_summary": "…", "statements": [], "options": [],
        "open_unknowns": [], "decision_question": None,
    })
    r = admin_client.post(f"/joint-understanding/{ju_id}/translate", json={}, headers=headers)
    assert r.status_code == 502


def test_translate_requires_open_session_and_is_system_isolated(admin_client, monkeypatch):
    headers, system_id, qa, ju_id, finding_id = _setup(admin_client)
    _stub_translation(monkeypatch, _response(statements=[
        {
            "layer": "impact", "claim_kind": "inference", "text": "…",
            "supports_finding_ids": [finding_id],
        },
    ]))
    token = _login(admin_client)
    other = admin_client.post(
        "/systems", json={"name": "Other", "environment": "test", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/translate", json={},
        headers=_headers(token, other["id"]),
    ).status_code == 404

    admin_client.post(f"/joint-understanding/{ju_id}/hold", json={}, headers=headers)
    assert admin_client.post(
        f"/joint-understanding/{ju_id}/translate", json={}, headers=headers,
    ).status_code == 409


def test_unknown_findings_are_not_upgraded_and_suppress_the_question(admin_client, monkeypatch):
    """Remaining unknowns are a reason to investigate, not to hand the
    question back to the developer with no decision material."""
    headers, system_id, qa, ju_id, finding_id = _setup(admin_client)
    _stub_translation(monkeypatch, {
        "purpose_summary": "…",
        "statements": [
            {
                "layer": "impact", "claim_kind": "unknown",
                "text": "打ち切り後の通知先はまだ確認できていません",
                "supports_finding_ids": [finding_id],
            },
        ],
        "options": [],
        "open_unknowns": ["通知先が未確認"],
        "decision_question": "通知先はどこにすべきですか?",
    })
    r = admin_client.post(f"/joint-understanding/{ju_id}/translate", json={}, headers=headers)
    assert r.status_code == 201, r.text
    translation = r.json()["translation"]
    assert translation["ask_developer"] is False
    assert translation["decision_question"] is None
    assert translation["open_unknowns"] == ["通知先が未確認"]
