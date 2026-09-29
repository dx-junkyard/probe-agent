"""Issue #468 (Epic #467): per-ask measurement (`assistant_ask_metric`).

Contract: docs/01-specifications/capabilities/assistant-answer-quality.md §1.
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from app.llm import LLMError

QUESTION_MARKER = "QUESTIONMARKER-xyz"
ANSWER_MARKER = "ANSWERMARKER-xyz"
DRAFT_MARKER = "DRAFTMARKER-xyz"
SECRET = "sk-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ask-metrics-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL", "INTELLIGENCE_LLM_TIMEOUT",
        "INTELLIGENCE_MAX_OUTPUT_TOKENS", "CONTROL_API_KEYS", "LLM_MODEL", "LLM_API_KEY",
        "LLM_TIMEOUT", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
        "ASSISTANT_METRIC_RETENTION_DAYS",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _create_system(client, token, name="metrics-sys"):
    r = client.post(
        "/systems", json={"name": name, "environment": "test", "description": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


class _Client:
    def __init__(self, *, usage=None, finish=None, raises=None, text=None):
        self.last_usage = usage
        self.last_finish_reason = finish
        self._raises = raises
        self._text = text

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        if self._raises is not None:
            raise self._raises
        if self._text is not None:
            return self._text
        return json.dumps({
            "answer": f"{ANSWER_MARKER} 回答です。",
            "suggested_actions": [],
            "citations": [],
        })


def _enable(monkeypatch, fake):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr("app.routes.assistant.create_llm_client", lambda config: fake)


def _ask(client, headers, question="質問です", screen_id="system-understanding", expect=200, **extra):
    r = client.post(
        "/assistant/ask", json={"screen_id": screen_id, "question": question, **extra},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _rows(system_id=None):
    with get_conn() as conn:
        q = "SELECT * FROM assistant_ask_metric"
        args = ()
        if system_id is not None:
            q += " WHERE system_id = ?"
            args = (system_id,)
        return [dict(r) for r in conn.execute(q + " ORDER BY id", args).fetchall()]


@pytest.fixture
def env(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    return admin_client, token, system["id"], _headers(token, system["id"])


def test_one_ask_creates_one_row_with_stages_and_counters(env, monkeypatch):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Client())
    body = _ask(client, headers)
    assert body["request_id"]
    rows = _rows(system_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == body["request_id"]
    assert row["outcome"] == "answered"
    stages = json.loads(row["stage_timings_json"])
    for name in ("request_validation", "diagnostics", "system_state", "screen_context",
                 "context_pack", "llm", "response_validation"):
        assert name in stages, stages
        assert stages[name]["count"] >= 1
    assert "persist" not in stages  # no thread -> no turn write: not_run, not 0
    counters = json.loads(row["counters_json"])
    assert counters["diagnostics_runs"] == 1
    assert counters["system_state_runs"] == 1
    assert counters["llm_calls"] == 1
    assert counters["llm_retries"] == 0
    sizes = json.loads(row["input_sizes_json"])
    assert sizes["total"] > 0 and "question" in sizes and "system_prompt" in sizes


def test_usage_provider_reported_vs_not_reported(env, monkeypatch):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Client(usage={"input_tokens": 120, "output_tokens": 30}, finish="stop"))
    _ask(client, headers)
    _enable(monkeypatch, _Client())
    _ask(client, headers)
    first, second = [json.loads(r["usage_json"]) for r in _rows(system_id)]
    assert first["source"] == "provider_reported"
    assert (first["input_tokens"], first["output_tokens"]) == (120, 30)
    assert first["finish_reason"] == "stop"
    assert second["source"] == "not_reported"
    assert second["input_tokens"] is None and second["output_tokens"] is None
    assert second["estimated_input_tokens"] > 0
    assert second["estimate_method"] == "chars_div_4"
    assert second["output_chars"] > 0


def _make_journey_thread(client, headers):
    r = client.post(
        "/ux-design/journeys",
        json={"journey_key": "journey-1", "perspective": "to_be", "baseline_mode": "undecided"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/assistant/discussion-threads",
        json={"scope": "entity", "screen_id": "ux-design-studio",
              "target_kind": "ux_journey", "target_ref": "journey-1"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["thread"]["id"]


def test_no_row_contains_question_answer_draft_or_secret(env, monkeypatch):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Client(text=json.dumps({
        "answer": f"{ANSWER_MARKER} {SECRET}", "suggested_actions": [],
        "citations": [{"type": "ui_draft", "id": "ui_draft:ux_journey.revision"}],
    })))
    thread_id = _make_journey_thread(client, headers)
    draft = {
        "target_kind": "ux_journey", "target_ref": "journey-1",
        "form_id": "ux_journey.revision",
        "fields": [{"field_name": "title", "value": DRAFT_MARKER, "dirty": True, "validation_error": ""}],
        "selected_item_ref": "", "active_tab": "", "comparison_target": "",
        "captured_at": 1_700_000_000.0, "local_revision_token": "token-1",
    }
    body = _ask(
        client, headers, question=f"{QUESTION_MARKER} {SECRET}", screen_id="ux-design-studio",
        thread_id=thread_id, ui_draft=draft,
    )
    assert body["ui_draft_state"] == "applied"
    rows = _rows(system_id)
    assert len(rows) == 1
    blob = json.dumps(rows, ensure_ascii=False)
    for forbidden in (QUESTION_MARKER, ANSWER_MARKER, DRAFT_MARKER, SECRET, "回答です"):
        assert forbidden not in blob
    row = rows[0]
    assert row["thread_id"] == thread_id and row["assistant_turn_id"]
    stages = json.loads(row["stage_timings_json"])
    assert "persist" in stages
    manifest = json.loads(row["manifest_json"])
    assert manifest["ui_draft_state"] == "applied"
    types = {(s["type"], s["id"]) for s in manifest["sources"]}
    assert ("ui_draft", "ui_draft:ux_journey.revision") in types
    assert any(s["id"].startswith("discussion_target:ux_journey:") and s["freshness"] in
               ("current", "not_tracked", "stale") for s in manifest["sources"])


@pytest.mark.parametrize(
    "fake, expected",
    [
        (_Client(raises=LLMError("HTTP 429", kind="rate_limited")), "rate_limited"),
        (_Client(text="I am not JSON"), "malformed_output"),
        (_Client(text="{ not json", finish="length"), "truncated_output"),
        (_Client(raises=LLMError("boom", kind="timeout")), "timeout"),
    ],
)
def test_llm_failure_records_failure_class(env, monkeypatch, fake, expected):
    client, _, system_id, headers = env
    _enable(monkeypatch, fake)
    body = _ask(client, headers)
    assert body["used_fallback"] is True
    row = _rows(system_id)[0]
    assert row["outcome"] == "deterministic_answer"
    assert row["failure_class"] == expected
    assert json.loads(row["counters_json"])["llm_calls"] == 1


def test_no_client_records_provider_class(env):
    client, _, system_id, headers = env  # LLM_PROVIDER=mock
    _ask(client, headers)
    row = _rows(system_id)[0]
    assert row["outcome"] == "deterministic_answer"
    assert row["failure_class"] == "provider_test_only"
    assert json.loads(row["counters_json"])["llm_calls"] == 0


def test_unknown_screen_records_rejected_row(env):
    client, _, system_id, headers = env
    _ask(client, headers, screen_id="no-such-screen", expect=404)
    rows = _rows(system_id)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "rejected" and rows[0]["failure_class"] is None


def test_failure_class_for_is_pure_and_finite():
    from app.assistant_metrics import ASSISTANT_FAILURE_CLASSES, failure_class_for

    kinds = ("timeout", "network", "auth", "rate_limited", "provider_error",
             "malformed_response", "config")
    got = {failure_class_for(error_kind=k) for k in kinds}
    assert got <= set(ASSISTANT_FAILURE_CLASSES)
    assert failure_class_for(error_kind="malformed_response") == "malformed_output"
    assert failure_class_for(error_kind="config") == "provider_not_configured"
    assert failure_class_for(client_available=False, provider="mock") == "provider_test_only"
    assert failure_class_for(client_available=False, provider="openai") == "provider_not_configured"
    assert failure_class_for(malformed_output=True, finish_reason="length") == "truncated_output"
    assert failure_class_for() is None


def test_unknown_stage_is_rejected():
    from app.assistant_metrics import AskTrace

    trace = AskTrace()
    with pytest.raises(ValueError):
        with trace.stage("nope"):
            pass


def test_summary_nearest_rank_unmeasured_and_failure_rate(env, monkeypatch):
    client, _, system_id, headers = env
    empty = client.get("/assistant/ask-metrics-summary", headers=headers).json()
    assert empty["total_requests"] == 0
    assert empty["stages"]["llm"]["p50"] is None and empty["stages"]["llm"]["state"] == "unmeasured"
    assert empty["failure_rate"]["value"] is None and empty["failure_rate"]["denominator"] == 0
    assert empty["provider_reported_tokens"]["state"] == "unmeasured"

    _enable(monkeypatch, _Client(usage={"input_tokens": 10, "output_tokens": 5}))
    for _ in range(3):
        _ask(client, headers)
    _ask(client, headers, screen_id="no-such-screen", expect=404)
    with get_conn() as conn:  # deterministic stage samples for nearest-rank
        rows = conn.execute(
            "SELECT id FROM assistant_ask_metric WHERE outcome = 'answered' ORDER BY id"
        ).fetchall()
        for i, r in enumerate(rows, start=1):
            conn.execute(
                "UPDATE assistant_ask_metric SET stage_timings_json = ? WHERE id = ?",
                (json.dumps({"llm": {"ms": i * 100.0, "count": 1}}), r["id"]),
            )
        conn.execute(
            "INSERT INTO assistant_ask_metric (system_id, request_id, started_at, finished_at, "
            "outcome, failure_class, created_at) VALUES (?, 'err1', ?, ?, 'error', NULL, ?)",
            (system_id, time.time(), time.time(), time.time()),
        )
    s = client.get("/assistant/ask-metrics-summary?window_hours=1", headers=headers).json()
    assert s["total_requests"] == 5
    llm = s["stages"]["llm"]
    assert llm["sample_count"] == 3 and llm["not_run_count"] == 2
    assert llm["p50"] == 200.0 and llm["p95"] == 300.0  # observed values only
    assert s["outcome_counts"]["answered"] == 3 and s["outcome_counts"]["rejected"] == 1
    assert s["failure_rate"] == {"numerator": 1, "denominator": 4, "value": 0.25, "state": "measured"}
    assert s["llm_calls_total"] == 3
    assert s["provider_reported_tokens"]["input_tokens"] == 30
    assert s["provider_reported_tokens"]["sample_count"] == 3
    assert s["estimated_tokens"]["estimated_input_tokens"] > 0
    assert s["client_first_visible_ms"]["state"] == "unmeasured"
    assert client.get("/assistant/ask-metrics-summary?window_hours=0", headers=headers).status_code == 422


def test_client_timing_once_then_409_then_404(env, monkeypatch):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Client())
    rid = _ask(client, headers)["request_id"]
    url = f"/assistant/ask-metrics/{rid}/client-timing"
    body = {"first_visible_ms": 120.5, "complete_ms": 900.0, "aborted": False}
    assert client.post(url, json=body, headers=headers).status_code == 200
    again = client.post(url, json=body, headers=headers)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "client_timing_already_recorded"
    assert client.post(
        "/assistant/ask-metrics/unknown/client-timing", json=body, headers=headers
    ).status_code == 404
    assert client.post(
        url.replace(rid, "x" + rid), json={**body, "extra": 1}, headers=headers
    ).status_code == 422
    assert client.post(url, json={**body, "complete_ms": -1}, headers=headers).status_code == 422
    got = client.get(f"/assistant/ask-metrics/{rid}", headers=headers).json()
    assert got["client_timing"]["first_visible_ms"] == 120.5
    s = client.get("/assistant/ask-metrics-summary", headers=headers).json()
    assert s["client_first_visible_ms"]["p50"] == 120.5 and s["client_complete_ms"]["p95"] == 900.0


def test_list_is_newest_first_and_limited(env, monkeypatch):
    client, _, _, headers = env
    _enable(monkeypatch, _Client())
    ids = [_ask(client, headers)["request_id"] for _ in range(3)]
    listed = client.get("/assistant/ask-metrics?limit=2", headers=headers).json()["metrics"]
    assert [m["request_id"] for m in listed] == [ids[2], ids[1]]
    assert client.get("/assistant/ask-metrics?limit=201", headers=headers).status_code == 422


def test_system_isolation(env, monkeypatch, admin_client):
    client, token, system_id, headers = env
    other = _create_system(client, token, name="other-sys")
    other_headers = _headers(token, other["id"])
    _enable(monkeypatch, _Client())
    rid = _ask(client, headers)["request_id"]
    assert client.get("/assistant/ask-metrics", headers=other_headers).json()["metrics"] == []
    assert client.get(f"/assistant/ask-metrics/{rid}", headers=other_headers).status_code == 404
    assert client.post(
        f"/assistant/ask-metrics/{rid}/client-timing",
        json={"first_visible_ms": 1, "complete_ms": 2, "aborted": False}, headers=other_headers,
    ).status_code == 404
    assert client.get("/assistant/ask-metrics-summary", headers=other_headers).json()["total_requests"] == 0
    assert client.get("/assistant/ask-metrics-summary", headers=headers).json()["total_requests"] == 1


def test_retention_deletes_old_rows_on_next_insert(env, monkeypatch):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Client())
    old = time.time() - 40 * 86400
    with get_conn() as conn:
        for sid in (system_id,):
            conn.execute(
                "INSERT INTO assistant_ask_metric (system_id, request_id, started_at, finished_at, "
                "outcome, created_at) VALUES (?, 'old-row', ?, ?, 'answered', ?)",
                (sid, old, old, old),
            )
    assert len(_rows(system_id)) == 1
    _ask(client, headers)
    ids = [r["request_id"] for r in _rows(system_id)]
    assert "old-row" not in ids and len(ids) == 1
    monkeypatch.setenv("ASSISTANT_METRIC_RETENTION_DAYS", "garbage")
    from app.assistant_metrics import _retention_days

    assert _retention_days() == 30


def test_persist_failure_never_breaks_the_answer(env, monkeypatch):
    client, _, system_id, headers = env
    _enable(monkeypatch, _Client())

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.assistant_metrics.get_conn", boom, raising=False)
    monkeypatch.setattr("app.db.get_conn", boom)
    body = _ask(client, headers)
    assert body["request_id"]


def test_interview_replay_creates_no_second_row(tmp_path, monkeypatch):
    from app import assistant_discussion, interview_discussion_jobs as jobs

    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "replay.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    for name in ("CONTROL_API_KEYS", "INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(jobs, "start_worker", lambda: threading.Event())
    from app.main import app

    with TestClient(app) as client:
        token = client.post("/auth/login", json={"username": "root", "password": "secret"}).cookies.get("probe_session")
        base = {"Authorization": f"Bearer {token}"}
        system = client.post("/systems", json={"name": "iid", "environment": "test"}, headers=base).json()["id"]
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
        body = {"screen_id": "interview", "thread_id": tid, "client_turn_id": "turn-once", "question": "対象ユーザー"}
        first = client.post("/assistant/ask", headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert len(_rows(system)) == 1
        again = client.post("/assistant/ask", headers=headers, json=body)
        assert again.status_code == 200 and again.json() == first.json()
        assert len(_rows(system)) == 1
        assert first.json()["request_id"] == _rows(system)[0]["request_id"]
