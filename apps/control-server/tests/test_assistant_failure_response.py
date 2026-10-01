"""Issue #472 (Epic #467): failure classes and non-misleading failure answers.

Contract: docs/01-specifications/capabilities/assistant-answer-quality.md §5.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.assistant_failure import FAILURE_CLASSES, FAILURE_TABLE
from app.db import get_conn
from app.llm import LLMError, LLMQuotaExceeded, LLMSystemContextMissing
from tests.test_assistant_context_selection import (  # noqa: F401  (fixtures + helpers)
    _create_system,
    _headers,
    _login,
    admin_client,
)

SECRET = "sk-test-SECRET123"
SCREEN = "system-understanding"
DESIGN_QUESTION = "この画面の設計方針についてどう思いますか?"
SETTING_QUESTION = "What should LLM_API_KEY be set to?"


class _Fake:
    def __init__(self, *, raises=None, text=None, finish=None):
        self._raises = raises
        self._text = text
        self.last_usage = None
        self.last_finish_reason = finish
        self.calls = []

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.calls.append(messages)
        if self._raises is not None:
            raise self._raises
        return self._text


def _enable(monkeypatch, fake):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr("app.routes.assistant.create_llm_client", lambda config: fake)


@pytest.fixture
def env(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "failure-sys")
    return admin_client, token, system_id, _headers(token, system_id)


def _ask(client, headers, question=DESIGN_QUESTION, screen_id=SCREEN, **extra):
    r = client.post(
        "/assistant/ask", json={"screen_id": screen_id, "question": question, **extra}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()


def _metric_rows(system_id):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM assistant_ask_metric WHERE system_id = ? ORDER BY id", (system_id,)
        ).fetchall()]


def _assert_failed(body, failure_class, *, retryable, recovery_kinds):
    assert body["answer_status"] == "failed"
    assert body["used_fallback"] is True and body["decision_method"] == "deterministic"
    f = body["failure"]
    assert f["failure_class"] == failure_class
    assert f["retryable"] is retryable
    assert f["message"] == FAILURE_TABLE[failure_class]["message"]
    assert {r["kind"] for r in f["recovery"]} == set(recovery_kinds)
    assert any("ぁ" <= ch <= "ヿ" or "一" <= ch <= "鿿" for ch in f["message"])  # Japanese
    assert body["fallback_reason"] == f["message"]
    assert body["citations"] == [] and body["suggested_actions"] == []
    # The answer is the reason + next operations only.
    assert body["answer"].startswith(f["message"])
    assert "Main sections" not in body["answer"] and "Current problems" not in body["answer"]


CLASS_CASES = [
    (LLMError("boom " + SECRET, kind="timeout"), "timeout", True, {"retry", "rephrase"}),
    (LLMError("HTTP 429 " + SECRET, kind="rate_limited", http_status=429), "rate_limited", True, {"retry"}),
    (LLMError("HTTP 401 " + SECRET, kind="auth", http_status=401), "auth", False, {"configure", "open_settings"}),
    (LLMError("conn " + SECRET, kind="network"), "network", True, {"retry"}),
    (LLMError("HTTP 500 " + SECRET, kind="provider_error", http_status=500), "provider_error", True, {"retry"}),
    (LLMQuotaExceeded("daily " + SECRET), "budget_exceeded", False, {"open_settings", "rephrase"}),
    (LLMSystemContextMissing("ctx " + SECRET), "provider_error", True, {"retry"}),
]


@pytest.mark.parametrize("exc,failure_class,retryable,kinds", CLASS_CASES)
def test_provider_failures_are_classified_and_leak_nothing(
    env, monkeypatch, exc, failure_class, retryable, kinds
):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Fake(raises=exc))
    body = _ask(client, headers)
    _assert_failed(body, failure_class, retryable=retryable, recovery_kinds=kinds)
    assert SECRET not in json.dumps(body)
    with get_conn() as conn:
        turns = [dict(r) for r in conn.execute("SELECT * FROM assistant_discussion_turn").fetchall()]
    dump = json.dumps([turns, _metric_rows(system_id)], default=str)
    assert SECRET not in dump
    row = _metric_rows(system_id)[-1]
    assert row["outcome"] == "failed" and row["failure_class"] == failure_class


def test_malformed_output_is_classified(env, monkeypatch):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Fake(text="JSONではありません " + SECRET))
    body = _ask(client, headers)
    _assert_failed(body, "malformed_output", retryable=True, recovery_kinds={"retry", "rephrase"})
    assert SECRET not in json.dumps(body)
    assert _metric_rows(system_id)[-1]["failure_class"] == "malformed_output"


def test_truncated_output_is_classified_by_finish_reason(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(text='{"conclusion": "途中で', finish="length"))
    body = _ask(client, headers)
    _assert_failed(body, "truncated_output", retryable=True, recovery_kinds={"retry", "rephrase"})


def test_no_client_mock_provider_is_test_only(env):
    client, _, system_id, headers = env  # fixture provider is `mock`
    body = _ask(client, headers)
    _assert_failed(body, "provider_test_only", retryable=False, recovery_kinds={"configure", "open_settings"})
    assert _metric_rows(system_id)[-1]["outcome"] == "failed"


def test_no_api_key_is_provider_not_configured(env, monkeypatch):
    client, _, _, headers = env
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _boom(config):
        raise AssertionError("no LLM call may be attempted")

    monkeypatch.setattr("app.routes.assistant.create_llm_client", _boom)
    body = _ask(client, headers)
    _assert_failed(
        body, "provider_not_configured", retryable=False, recovery_kinds={"configure", "open_settings"}
    )
    targets = {r["target"] for r in body["failure"]["recovery"]}
    assert "LLM_API_KEY" in targets and "/settings" in targets


def test_failure_table_is_complete_and_fixed():
    assert set(FAILURE_TABLE) == set(FAILURE_CLASSES)
    for entry in FAILURE_TABLE.values():
        assert SECRET not in entry["message"]
        assert entry["recovery"]
    retryable = {c for c, e in FAILURE_TABLE.items() if e["retryable"]}
    assert retryable == {
        "timeout", "network", "rate_limited", "provider_error",
        "malformed_output", "truncated_output", "context_unavailable",
    }


# --- deterministic answer vs failed ------------------------------------------


def test_design_question_on_llm_failure_is_failed_without_screen_overview(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Fake(raises=LLMError("x", kind="timeout")))
    body = _ask(client, headers)
    assert body["answer_status"] == "failed"
    from app.assistant import get_screen_context

    ctx = get_screen_context(SCREEN)
    assert ctx.purpose not in body["answer"]
    for section in ctx.visible_sections:
        assert section not in body["answer"]
    assert "INTELLIGENCE_LLM_MODEL" not in body["answer"]
    assert "Current problems" not in body["answer"]


def test_setting_mention_on_llm_failure_is_a_deterministic_answer(env, monkeypatch):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Fake(raises=LLMError("x", kind="auth", http_status=401)))
    body = _ask(client, headers, question=SETTING_QUESTION)
    assert body["answer_status"] == "deterministic_answer"
    assert body["used_fallback"] is True and body["decision_method"] == "deterministic"
    assert "LLM_API_KEY" in body["answer"]
    assert [c["id"] for c in body["citations"]] == ["LLM_API_KEY"]
    assert body["failure"]["failure_class"] == "auth"
    assert body["fallback_reason"] == body["failure"]["message"]
    row = _metric_rows(system_id)[-1]
    assert row["outcome"] == "deterministic_answer" and row["failure_class"] == "auth"


def test_check_id_mention_is_a_deterministic_answer(env):
    client, _, _, headers = env
    body = _ask(client, headers, question="database_storage が失敗する理由は?", screen_id="overview")
    assert body["answer_status"] == "deterministic_answer"
    assert "database_storage" in {c["id"] for c in body["citations"]}


# --- history ------------------------------------------------------------------


def _journey_thread(client, headers, jkey="fj"):
    r = client.post("/ux-design/journeys",
                    json={"journey_key": jkey, "perspective": "to_be", "baseline_mode": "undecided"},
                    headers=headers)
    assert r.status_code == 201, r.text
    r = client.post(
        "/assistant/discussion-threads",
        json={"scope": "entity", "screen_id": "ux-design-studio",
              "target_kind": "ux_journey", "target_ref": jkey},
        headers=headers,
    )
    return r.json()["thread"]["id"]


def test_failed_turn_is_kept_in_thread_but_excluded_from_next_llm_history(env, monkeypatch):
    client, _, _, headers = env
    thread_id = _journey_thread(client, headers)
    _enable(monkeypatch, _Fake(raises=LLMError("x", kind="timeout")))
    failed = _ask(client, headers, question="FAILEDQ-472", screen_id="ux-design-studio", thread_id=thread_id)
    assert failed["answer_status"] == "failed"
    ok = _Fake(text=json.dumps({"conclusion": "OK", "points": [], "missing_information": [],
                                "suggested_actions": [], "citations": []}))
    _enable(monkeypatch, ok)
    _ask(client, headers, question="SECONDQ-472", screen_id="ux-design-studio", thread_id=thread_id)
    history = ok.calls[-1]
    contents = [m["content"] for m in history if m["role"] in ("user", "assistant")]
    assert any("FAILEDQ-472" in c for c in contents)  # the user turn is kept
    assert not any(FAILURE_TABLE["timeout"]["message"] in c for c in contents)  # the failure notice is not
    detail = client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers).json()
    roles = [(t["role"], (t["answer_structure"] or {}).get("answer_status")) for t in detail["turns"]]
    assert roles[:3] == [("user", None), ("assistant", "failed"), ("user", None)]
    assert detail["turns"][1]["answer_structure"]["failure_class"] == "timeout"


def test_no_automatic_retry(env, monkeypatch):
    client, _, system_id, headers = env
    fake = _Fake(raises=LLMError("x", kind="timeout"))
    _enable(monkeypatch, fake)
    _ask(client, headers)
    assert len(fake.calls) == 1
    counters = json.loads(_metric_rows(system_id)[-1]["counters_json"])
    assert counters["llm_calls"] == 1 and counters["llm_retries"] == 0


# --- interview client_turn_id -------------------------------------------------


def test_interview_replay_after_failed_turn_is_identical_and_new_turn_id_retries(tmp_path, monkeypatch):
    from app import assistant_discussion, interview_discussion_jobs as jobs

    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "fail-replay.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    for name in ("CONTROL_API_KEYS", "INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(jobs, "start_worker", lambda: threading.Event())
    from app.main import app

    with TestClient(app) as client:
        token = client.post("/auth/login", json={"username": "root", "password": "secret"}).cookies.get(
            "probe_session"
        )
        base = {"Authorization": f"Bearer {token}"}
        system = client.post("/systems", json={"name": "fr", "environment": "test"}, headers=base).json()["id"]
        headers = {**base, "X-Probe-System-Id": str(system)}
        with get_conn() as conn:
            snap = conn.execute(
                "INSERT INTO repository_snapshots(system_id,repo_path,commit_sha,status,created_at) "
                "VALUES(?,'/tmp/test','aaa','ready',?)", (system, time.time())).lastrowid
            content = json.dumps({"system_purpose": [{"name": "利用者支援", "summary": "管理者が利用する"}]})
            sid = conn.execute(
                "INSERT INTO interview_session(system_id,snapshot_id,title,current_understanding,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)", (system, snap, "t", content, time.time(), time.time())).lastrowid
            conn.execute(
                "INSERT INTO understanding_revision(system_id,session_id,snapshot_id,current_understanding,created_at,status) "
                "VALUES(?,?,?,?,?,'candidate')", (system, sid, snap, content, time.time()))
        tid = assistant_discussion.resolve_or_create_thread(
            system, scope="entity", screen_id="interview",
            target_kind="interview_session", target_ref=str(sid))["thread"]["id"]
        body = {"screen_id": "interview", "thread_id": tid, "client_turn_id": "t-1", "question": "対象ユーザー"}
        first = client.post("/assistant/ask", headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert first.json()["answer_status"] == "failed"  # mock provider
        turns_after_first = _turn_count(tid)
        again = client.post("/assistant/ask", headers=headers, json=body)
        assert again.status_code == 200 and again.json() == first.json()
        assert _turn_count(tid) == turns_after_first  # no duplicated turns
        # A NEW client_turn_id is an explicit retry: a fresh ask, new turns.
        retry = client.post("/assistant/ask", headers=headers, json={**body, "client_turn_id": "t-2"})
        assert retry.status_code == 200
        assert retry.json()["request_id"] != first.json()["request_id"]
        assert _turn_count(tid) == turns_after_first + 2


def _turn_count(thread_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM assistant_discussion_turn WHERE thread_id = ?", (thread_id,)
        ).fetchone()["n"]
