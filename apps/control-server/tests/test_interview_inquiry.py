"""Tests for Issue #285: Inquiry lifecycle (保留・疑問解消・復帰).

Reworked for Issue #286 (Question Router / read-only Investigation Agent):
``app/inquiry_answering.py``'s ``generate_inquiry_answer`` no longer makes a
single reasoning call -- it routes the question first
(``app/question_router.py``), then (for system_researchable/hybrid) runs
the read-only Investigation Agent (``app/investigation_agent.py``) and
composes the final answer deterministically (``app/response_composer.py``).
The unit tests below were adapted accordingly (Issue #286 sub-issue brief:
"if you change inquiry_answering internals, adapt its unit-test mocks but
keep the lifecycle/contract tests intact").

Covers:
1. app/inquiry_answering.py's generate_inquiry_answer: fail-closed on
   mock/non-reasoning clients and routing/investigation LLM failures, the
   three route categories (human_only/system_researchable/hybrid), and
   unverifiable evidence citations dropped (not fatal, via the
   Investigation Agent's own pruning).
2. Route lifecycle: create/list/detail/message/resolve/unresolved/hold/
   resume/cancel/reopen-doubt, invalid transitions -> 409, origin-untouched
   assertions (「解消を同意と誤認しない」), held_draft round-trip,
   unresolved/hold/cancel audit rows, LLM failure fail-closed (no assistant
   message, run recorded failed, inquiry stays open), answerable=false
   path, and System isolation. These route-level tests stub
   ``generate_inquiry_answer`` wholesale (unaffected by the Issue #286
   internal rework) so they stay exactly as Issue #285 wrote them.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.inquiry_answering import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    InquiryAnswerResult,
    generate_inquiry_answer,
)
from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from app.models import InterviewContextPack, InterviewEvidenceLocation, InterviewSymbolItem


# --- Unit tests: app/inquiry_answering.py ------------------------------------


def _make_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


def _mock_config():
    return LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30)


def _evidence(path="src/summarize.py"):
    return InterviewEvidenceLocation(
        snapshot_id=1, path=path, qualified_name="summarize.summarize_text",
        start_line=1, end_line=10,
    )


def _context_pack() -> InterviewContextPack:
    symbols = [
        InterviewSymbolItem(
            symbol_id=1, path="src/summarize.py",
            qualified_name="summarize.summarize_text", kind="function",
            start_line=1, end_line=10, classification="unclassified",
            has_metadata=False, evidence=_evidence(),
        ),
    ]
    return InterviewContextPack(
        system_id=1, snapshot_id=1, total_symbols=len(symbols),
        total_entrypoints=0, classified_count=0, unclassified_count=len(symbols),
        budget_max_chars=60000, budget_used_chars=1000, truncated=False, symbols=symbols,
    )


class FakeLLMClient(LLMClient):
    """Single fixed response/error regardless of call -- only useful for the
    Question Router call, since it always runs first."""

    def __init__(self, response: Any = None, error: Optional[str] = None):
        self._response = response
        self._error = error

    def generate_text(
        self, messages: List[Dict[str, str]], *, temperature=None, max_tokens=None,
    ) -> str:
        if self._error:
            raise LLMError(self._error)
        return json.dumps(self._response)


class RoutingFakeLLMClient(LLMClient):
    """Dispatches by system-prompt marker: Question Router vs Investigation
    Agent are two independent reasoning calls in the Issue #286 pipeline."""

    def __init__(
        self, *, route_response=None, route_error=None,
        investigation_response=None, investigation_error=None,
    ):
        self.route_response = route_response
        self.route_error = route_error
        self.investigation_response = investigation_response
        self.investigation_error = investigation_error
        self.calls: List[str] = []

    def generate_text(
        self, messages: List[Dict[str, str]], *, temperature=None, max_tokens=None,
    ) -> str:
        system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
        # Investigation's own prompt mentions "the Question Router" in its
        # instructions, so it must be checked first -- its own marker
        # ("Investigation Agent") never appears in the router's prompt.
        if "Investigation Agent" in system:
            self.calls.append("investigation")
            if self.investigation_error:
                raise LLMError(self.investigation_error)
            return json.dumps(self.investigation_response)
        if "Question Router" in system:
            self.calls.append("route")
            if self.route_error:
                raise LLMError(self.route_error)
            return json.dumps(self.route_response)
        raise AssertionError(f"Unexpected system prompt: {system[:80]!r}")


def _init_repo(tmp_path, files: Dict[str, str]) -> "tuple[str, str]":
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], check=True, capture_output=True)
    for path, content in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    return repo, sha


def test_generate_inquiry_answer_fails_closed_on_mock_client():
    result = generate_inquiry_answer(
        MockLLMClient(), _mock_config(),
        context_pack=_context_pack(), question_text="なぜこの実装なのですか?",
    )
    assert result.error is not None
    assert result.answerable is False


def test_generate_inquiry_answer_fails_closed_on_llm_error():
    client = FakeLLMClient(error="upstream timeout")
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="なぜこの実装なのですか?",
    )
    assert result.error == "upstream timeout"


def test_generate_inquiry_answer_fails_closed_on_invalid_json():
    client = FakeLLMClient(response={"conclusion": 123})  # wrong type
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="なぜこの実装なのですか?",
    )
    assert result.error is not None


def test_generate_inquiry_answer_human_only_needs_no_repo(tmp_path):
    """human_only never triggers investigation -- no repo_path/commit_sha
    needed, and result.investigation stays None."""
    client = RoutingFakeLLMClient(route_response={
        "category": "human_only",
        "reason": "これは優先順位の判断であり、コードからは決められません。",
        "research_focus": None,
    })
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="この機能を今四半期に優先すべきですか?",
    )
    assert result.error is None
    assert result.answerable is True
    assert result.route is not None and result.route.category == "human_only"
    assert result.investigation is None
    assert "この機能を今四半期に優先すべきですか?" in result.conclusion
    assert client.calls == ["route"]


def test_generate_inquiry_answer_system_researchable_completed(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n"})
    client = RoutingFakeLLMClient(
        route_response={
            "category": "system_researchable",
            "reason": "実装を読めば分かります。",
            "research_focus": "summarize function",
        },
        investigation_response={
            "status": "completed",
            "conclusion": "この関数はテキストを要約するために存在します。",
            "key_points": ["入力は自由文", "出力は短い要約"],
            "evidence": [{"path": "src/summarize.py", "start_line": 1, "end_line": 10, "summary": "定義箇所"}],
            "uncertainty": "",
            "confidence": "confirmed",
            "decision_question": None,
        },
    )
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="なぜこの実装なのですか?",
        repo_path=repo, commit_sha=sha,
    )
    assert result.error is None
    assert result.answerable is True
    assert result.conclusion == "この関数はテキストを要約するために存在します。"
    assert len(result.evidence) == 1
    assert result.evidence[0].path == "src/summarize.py"
    assert result.evidence_dropped == 0
    assert client.calls == ["route", "investigation"]


def test_generate_inquiry_answer_drops_unverifiable_evidence(tmp_path):
    """An evidence citation outside every excerpt the Investigation Agent
    actually read is dropped, not fatal -- the conclusion still comes back
    (Issue #142 pattern, now enforced inside investigation_agent.py)."""
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "\n".join(f"line{i}" for i in range(1, 21)) + "\n"})
    client = RoutingFakeLLMClient(
        route_response={
            "category": "system_researchable", "reason": "r", "research_focus": "summarize",
        },
        investigation_response={
            "status": "completed",
            "conclusion": "結論です。",
            "key_points": [],
            "evidence": [
                {"path": "src/summarize.py", "start_line": 1, "end_line": 10, "summary": "ok"},
                {"path": "src/unknown.py", "start_line": 1, "end_line": 5, "summary": "invented"},
            ],
            "uncertainty": "",
            "confidence": "likely",
            "decision_question": None,
        },
    )
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="q", repo_path=repo, commit_sha=sha,
    )
    assert result.error is None
    assert len(result.evidence) == 1
    assert result.evidence[0].path == "src/summarize.py"
    assert result.evidence_dropped == 1


def test_generate_inquiry_answer_answerable_false_is_not_an_error(tmp_path):
    """system_researchable + an unresolved investigation is not an error --
    the caller substitutes the fixed template instead of fabricating."""
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "line1\nline2\n"})
    client = RoutingFakeLLMClient(
        route_response={
            "category": "system_researchable", "reason": "r", "research_focus": "summarize",
        },
        investigation_response={
            "status": "unresolved",
            "conclusion": "判断できません。",
            "key_points": [],
            "evidence": [],
            "uncertainty": "根拠となるコードが見つかりません",
            "confidence": "uncertain",
            "decision_question": None,
        },
    )
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="q", repo_path=repo, commit_sha=sha,
    )
    assert result.error is None
    assert result.answerable is False
    assert result.uncertainty == "根拠となるコードが見つかりません"


def test_generate_inquiry_answer_hybrid_always_answerable_with_decision_question(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "line1\nline2\n"})
    client = RoutingFakeLLMClient(
        route_response={
            "category": "hybrid", "reason": "r", "research_focus": "summarize",
        },
        investigation_response={
            "status": "completed",
            "conclusion": "現在は同期処理です。",
            "key_points": [],
            "evidence": [],
            "uncertainty": "",
            "confidence": "likely",
            "decision_question": "非同期化を優先すべきですか?",
        },
    )
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="q", repo_path=repo, commit_sha=sha,
    )
    assert result.error is None
    assert result.answerable is True
    assert result.decision_question == "非同期化を優先すべきですか?"
    assert "非同期化を優先すべきですか?" in result.conclusion


def test_generate_inquiry_answer_fails_closed_when_investigation_fails(tmp_path):
    repo, sha = _init_repo(tmp_path, {"src/summarize.py": "line1\nline2\n"})
    client = RoutingFakeLLMClient(
        route_response={
            "category": "system_researchable", "reason": "r", "research_focus": "summarize",
        },
        investigation_error="upstream timeout",
    )
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="q", repo_path=repo, commit_sha=sha,
    )
    assert result.error == "upstream timeout"
    assert result.route is not None
    assert result.investigation is not None and result.investigation.status == "failed"


def test_generate_inquiry_answer_system_researchable_requires_pinned_snapshot():
    """No repo_path/commit_sha -- investigation cannot run; fails closed."""
    client = RoutingFakeLLMClient(route_response={
        "category": "system_researchable", "reason": "r", "research_focus": "summarize",
    })
    result = generate_inquiry_answer(
        client, _make_config(),
        context_pack=_context_pack(), question_text="q",
    )
    assert result.error is not None
    assert result.investigation is None


# --- Route-level lifecycle tests ----------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-interview-inquiry-test.db"))
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
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_qa(client, headers, session_id, question_text="この関数の目的は?"):
    r = client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": question_text},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _create_intent(client, headers, session_id):
    r = client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "トレース収集を効率化したい"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _stub_answer(monkeypatch, *, answerable=True, conclusion="結論です", error=None, is_mock=False,
                  key_points=None, evidence=None, uncertainty="", runtime_evidence=None):
    from app.routes import interview_inquiry as inquiry_routes

    def fake_create_llm_client(config):
        return object()

    def fake_generate_inquiry_answer(client, config, **kwargs):
        return InquiryAnswerResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=is_mock,
            conclusion=conclusion, key_points=key_points or [], evidence=[],
            runtime_evidence=runtime_evidence or [],
            uncertainty=uncertainty, answerable=answerable, error=error,
        )

    monkeypatch.setattr(inquiry_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(inquiry_routes, "generate_inquiry_answer", fake_generate_inquiry_answer)


def _open_inquiry(client, headers, session_id, origin_kind, origin_id, **overrides):
    body = {
        "origin_kind": origin_kind, "origin_id": origin_id,
        "question_text": "なぜこの実装なのですか?",
    }
    body.update(overrides)
    return client.post(f"/interview/sessions/{session_id}/inquiries", json=body, headers=headers)


# --- Create: origin untouched, initial answer generated ----------------------


def test_create_inquiry_from_qa_does_not_touch_origin_row(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch, conclusion="この関数は入力を要約します。")
    r = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"], held_draft=json.dumps({"draft": "途中の回答"}))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["inquiry"]["status"] == "open"
    assert body["inquiry"]["origin_kind"] == "qa"
    assert body["inquiry"]["origin_id"] == qa["id"]
    assert body["inquiry"]["held_draft"] == json.dumps({"draft": "途中の回答"})
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["content"] == "この関数は入力を要約します。"

    # The regression this issue guards: opening/answering an Inquiry never
    # changes the origin interview_qa row's state, answer, or timestamps.
    qa_after = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    origin = next(i for i in qa_after["items"] if i["id"] == qa["id"])
    assert origin["status"] == "open"
    assert origin["answer_text"] is None
    assert origin["answered_at"] is None


def test_create_inquiry_from_intent_does_not_touch_origin_row(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    intent = _create_intent(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    r = _open_inquiry(admin_client, headers, session_id, "intent", intent["id"])
    assert r.status_code == 201, r.text

    listing = admin_client.get(f"/interview/sessions/{session_id}/intent", headers=headers).json()
    origin = listing["items_by_field"]["goal"][0]
    assert origin["id"] == intent["id"]
    assert origin["status"] == intent["status"]  # unchanged ('confirmed')
    assert origin["value_text"] == intent["value_text"]


def test_create_inquiry_review_item_origin_kind_rejects_unknown_id(admin_client, monkeypatch):
    """Issue #287: unlike qa/intent's original placeholder behavior, a
    review_item origin_id must now resolve to a real alignment_item row."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    _stub_answer(monkeypatch)
    r = _open_inquiry(admin_client, headers, session_id, "review_item", 999)
    assert r.status_code == 404, r.text


def test_create_inquiry_rejects_unknown_qa_origin(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = _open_inquiry(admin_client, headers, session_id, "qa", 999999)
    assert r.status_code == 404, r.text


def test_create_inquiry_rejects_unknown_intent_origin(admin_client):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = _open_inquiry(admin_client, headers, session_id, "intent", 999999)
    assert r.status_code == 404, r.text


def test_create_inquiry_is_mock_propagated(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch, is_mock=True)
    r = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert r.status_code == 201, r.text
    assert r.json()["messages"][1]["is_mock"] is True


# --- Issue #287: review_item <-> alignment_item.status linkage ---------------


def _insert_alignment_item(system_id, session_id, snapshot_id, *, status="open"):
    """Insert a minimal alignment_item row directly (bypassing the #287
    build endpoint, which is out of scope for this module's tests)."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run_cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 is_mock, started_at, completed_at)
            VALUES (?, ?, 'alignment_build', 'anthropic', 'claude-sonnet-4-5',
                    'alignment-v1', 'alignment-v1', 'reasoning_llm', 'completed',
                    0, ?, ?)
            """,
            (system_id, snapshot_id, now, now),
        )
        run_id = run_cur.lastrowid
        cur = conn.execute(
            """
            INSERT INTO alignment_item
                (session_id, system_id, revision_id, snapshot_id, current_claim,
                 current_evidence, alignment_state, risk_flags, confidence,
                 review_category, reason_code, user_reason, status,
                 intelligence_run_id, is_mock, created_at, updated_at)
            VALUES (?, ?, NULL, ?, 'テストクレーム',
                    '[{"path": "src/a.py", "start_line": 1, "end_line": 2, "summary": "s"}]',
                    'gap', '[]', 'likely', 'batch_reviewable', 'routine_update',
                    '軽微な差分です。まとめて確認してください', ?, ?, 0, ?, ?)
            """,
            (session_id, system_id, snapshot_id, status, run_id, now, now),
        )
        return cur.lastrowid


def _get_alignment_item(system_id, item_id):
    from app.db import get_conn

    with get_conn() as conn:
        return dict(conn.execute(
            "SELECT * FROM alignment_item WHERE id = ? AND system_id = ?", (item_id, system_id),
        ).fetchone())


def test_review_item_inquiry_open_sets_item_status_inquiry(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item_id = _insert_alignment_item(system_id, session_id, snapshot_id)

    _stub_answer(monkeypatch)
    r = _open_inquiry(admin_client, headers, session_id, "review_item", item_id)
    assert r.status_code == 201, r.text

    assert _get_alignment_item(system_id, item_id)["status"] == "inquiry"


def test_review_item_inquiry_resolve_sets_item_back_to_open_not_answered(admin_client, monkeypatch):
    """The regression this issue's brief calls for explicitly: resolving the
    Inquiry must never itself count as answering the review item."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item_id = _insert_alignment_item(system_id, session_id, snapshot_id)

    _stub_answer(monkeypatch)
    created = _open_inquiry(admin_client, headers, session_id, "review_item", item_id)
    inquiry_id = created.json()["inquiry"]["id"]
    assert _get_alignment_item(system_id, item_id)["status"] == "inquiry"

    r = admin_client.post(f"/interview/inquiries/{inquiry_id}/resolve", headers=headers)
    assert r.status_code == 200, r.text

    after = _get_alignment_item(system_id, item_id)
    assert after["status"] == "open"
    assert after["user_decision"] is None

    # The developer must still explicitly answer via the item's own endpoint.
    answer = admin_client.post(
        f"/interview/alignment/{item_id}/answer",
        json={"decision": "accept_current"},
        headers=headers,
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "answered"


def test_review_item_inquiry_held_keeps_item_status_inquiry(admin_client, monkeypatch):
    """held is a pause, not a close -- the item must stay blocked, not
    silently revert to 'open' while the Inquiry itself is still pending."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item_id = _insert_alignment_item(system_id, session_id, snapshot_id)

    _stub_answer(monkeypatch)
    created = _open_inquiry(admin_client, headers, session_id, "review_item", item_id)
    inquiry_id = created.json()["inquiry"]["id"]

    r = admin_client.post(f"/interview/inquiries/{inquiry_id}/hold", headers=headers)
    assert r.status_code == 200, r.text
    assert _get_alignment_item(system_id, item_id)["status"] == "inquiry"


def test_review_item_inquiry_cancel_and_unresolved_also_release_item(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    item_a = _insert_alignment_item(system_id, session_id, snapshot_id)
    item_b = _insert_alignment_item(system_id, session_id, snapshot_id)

    _stub_answer(monkeypatch)
    inquiry_a = _open_inquiry(admin_client, headers, session_id, "review_item", item_a).json()["inquiry"]["id"]
    inquiry_b = _open_inquiry(admin_client, headers, session_id, "review_item", item_b).json()["inquiry"]["id"]

    admin_client.post(f"/interview/inquiries/{inquiry_a}/cancel", headers=headers)
    admin_client.post(f"/interview/inquiries/{inquiry_b}/unresolved", headers=headers)

    assert _get_alignment_item(system_id, item_a)["status"] == "open"
    assert _get_alignment_item(system_id, item_b)["status"] == "open"


def test_review_item_inquiry_answer_locked_while_inquiry_open(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item_id = _insert_alignment_item(system_id, session_id, snapshot_id)

    _stub_answer(monkeypatch)
    _open_inquiry(admin_client, headers, session_id, "review_item", item_id)

    r = admin_client.post(
        f"/interview/alignment/{item_id}/answer",
        json={"decision": "accept_current"},
        headers=headers,
    )
    assert r.status_code == 409, r.text


# --- Finding 7: at most one active Inquiry per origin ------------------------


def _insert_inquiry_row(system_id, session_id, origin_kind, origin_id, *, status="open"):
    """Insert a minimal interview_inquiry row directly via SQL, bypassing
    create_inquiry's uniqueness check -- simulates pre-fix duplicate data so
    _apply_transition's defense-in-depth guard (item 2 of the Finding 7 fix)
    can be exercised on its own."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO interview_inquiry
                (session_id, system_id, origin_kind, origin_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, system_id, origin_kind, origin_id, status, now, now),
        )
        return cur.lastrowid


def test_create_inquiry_rejects_second_open_inquiry_for_same_origin(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    first = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert first.status_code == 201, first.text
    first_id = first.json()["inquiry"]["id"]

    second = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "inquiry_already_active"
    assert detail["inquiry_id"] == first_id

    # The rejected attempt must not have created a second row.
    listing = admin_client.get(
        f"/interview/sessions/{session_id}/inquiries", headers=headers,
    ).json()
    assert len(listing["items"]) == 1


def test_create_inquiry_rejects_second_inquiry_while_first_is_held(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    first = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    first_id = first.json()["inquiry"]["id"]
    held = admin_client.post(f"/interview/inquiries/{first_id}/hold", headers=headers)
    assert held.status_code == 200, held.text

    second = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "inquiry_already_active"


def test_create_inquiry_review_item_origin_rejects_duplicate_too(admin_client, monkeypatch):
    """The uniqueness rule is origin-generic, not qa-specific -- it also
    covers origin_kind='review_item' (Issue #287's alignment_item origin)."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item_id = _insert_alignment_item(system_id, session_id, snapshot_id)

    _stub_answer(monkeypatch)
    first = _open_inquiry(admin_client, headers, session_id, "review_item", item_id)
    assert first.status_code == 201, first.text

    second = _open_inquiry(admin_client, headers, session_id, "review_item", item_id)
    assert second.status_code == 409, second.text
    assert second.json()["detail"]["code"] == "inquiry_already_active"


def test_create_inquiry_allowed_again_after_first_resolves(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    first = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    first_id = first.json()["inquiry"]["id"]
    resolved = admin_client.post(f"/interview/inquiries/{first_id}/resolve", headers=headers)
    assert resolved.status_code == 200, resolved.text

    second = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert second.status_code == 201, second.text


def test_create_inquiry_duplicate_check_is_scoped_per_origin(admin_client, monkeypatch):
    """A second Inquiry on a DIFFERENT origin is unaffected by an open one on
    another origin."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item_a = _insert_alignment_item(system_id, session_id, snapshot_id)
    item_b = _insert_alignment_item(system_id, session_id, snapshot_id)

    _stub_answer(monkeypatch)
    a = _open_inquiry(admin_client, headers, session_id, "review_item", item_a)
    assert a.status_code == 201, a.text
    b = _open_inquiry(admin_client, headers, session_id, "review_item", item_b)
    assert b.status_code == 201, b.text


def test_closing_one_of_two_duplicate_inquiries_keeps_item_locked_until_last_closes(
    admin_client, monkeypatch,
):
    """Defense in depth (item 2 of the Finding 7 fix) for pre-existing
    duplicate rows, seeded directly via SQL to simulate data that predates
    this fix (create_inquiry's own uniqueness check would now prevent this
    from happening through the API): closing one of two active Inquiries on
    the same origin must NOT release the origin item while the other active
    Inquiry remains open; only closing the last active one does."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    item_id = _insert_alignment_item(system_id, session_id, snapshot_id, status="inquiry")

    inquiry_1 = _insert_inquiry_row(system_id, session_id, "review_item", item_id)
    inquiry_2 = _insert_inquiry_row(system_id, session_id, "review_item", item_id)

    r1 = admin_client.post(f"/interview/inquiries/{inquiry_1}/resolve", headers=headers)
    assert r1.status_code == 200, r1.text
    assert _get_alignment_item(system_id, item_id)["status"] == "inquiry"

    r2 = admin_client.post(f"/interview/inquiries/{inquiry_2}/resolve", headers=headers)
    assert r2.status_code == 200, r2.text
    assert _get_alignment_item(system_id, item_id)["status"] == "open"


# --- LLM failure: fail-closed --------------------------------------------------


def test_create_inquiry_fails_closed_on_llm_error_no_assistant_message(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch, error="reasoning model call failed")
    r = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "inquiry_answer_failed"
    inquiry_id = detail["inquiry_id"]

    # Inquiry stays open with no assistant message despite the failure.
    detail_resp = admin_client.get(f"/interview/inquiries/{inquiry_id}", headers=headers).json()
    assert detail_resp["inquiry"]["status"] == "open"
    assert [m["role"] for m in detail_resp["messages"]] == ["user"]

    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM intelligence_runs WHERE run_type = 'inquiry_answer'"
        ).fetchone()
    assert run["status"] == "failed"
    assert run["error_details"] == "reasoning model call failed"
    assert run["prompt_version"] == PROMPT_VERSION
    assert run["schema_version"] == SCHEMA_VERSION


def test_followup_message_fails_closed_on_llm_error(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]

    _stub_answer(monkeypatch, error="rate limited")
    r = admin_client.post(
        f"/interview/inquiries/{inquiry_id}/message", json={"content": "もう少し詳しく教えて"},
        headers=headers,
    )
    assert r.status_code == 502, r.text

    detail_resp = admin_client.get(f"/interview/inquiries/{inquiry_id}", headers=headers).json()
    # Still open, and the follow-up user message is kept even though the
    # assistant failed to answer it.
    assert detail_resp["inquiry"]["status"] == "open"
    roles = [m["role"] for m in detail_resp["messages"]]
    assert roles == ["user", "assistant", "user"]


# --- answerable=false: fixed template, never fabricated -----------------------


def test_answerable_false_stores_fixed_template_message(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch, answerable=False, conclusion="LLM が書いた本文(使われないはず)")
    r = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assistant_msg = body["messages"][1]
    assert assistant_msg["content"] == "回答に必要な情報が不足しています。「解消していない」から追加の質問をするか、「今回は保留する」を選んでください。"
    assert "LLM が書いた本文" not in assistant_msg["content"]


# --- Follow-up message: only while open ---------------------------------------


def test_followup_message_requires_open_status(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]
    admin_client.post(f"/interview/inquiries/{inquiry_id}/resolve", headers=headers)

    r = admin_client.post(
        f"/interview/inquiries/{inquiry_id}/message", json={"content": "追加の質問"}, headers=headers,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "inquiry_not_open"


def test_followup_message_appends_conversation(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch, conclusion="最初の回答")
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]

    _stub_answer(monkeypatch, conclusion="2回目の回答")
    r = admin_client.post(
        f"/interview/inquiries/{inquiry_id}/message", json={"content": "もう少し詳しく"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    roles = [m["role"] for m in r.json()["messages"]]
    contents = [m["content"] for m in r.json()["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert contents[-1] == "2回目の回答"


# --- Transitions: finite set, invalid -> 409, audit rows ----------------------


def test_resolve_never_touches_origin_and_returns_origin_refs(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    created = _open_inquiry(
        admin_client, headers, session_id, "qa", qa["id"],
        held_draft=json.dumps({"answer_text": "下書き"}),
    ).json()
    inquiry_id = created["inquiry"]["id"]

    r = admin_client.post(f"/interview/inquiries/{inquiry_id}/resolve", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "resolved"
    assert body["closed_at"] is not None
    # 「解消を同意と誤認しない」: resolving carries origin refs back to the
    # dashboard but the origin item itself is untouched, and 'resolved' is
    # never itself an answer.
    assert body["origin_kind"] == "qa"
    assert body["origin_id"] == qa["id"]
    assert body["held_draft"] == json.dumps({"answer_text": "下書き"})

    qa_after = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    origin = next(i for i in qa_after["items"] if i["id"] == qa["id"])
    assert origin["status"] == "open"
    assert origin["answer_text"] is None


def test_resolve_twice_is_rejected_409(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]
    admin_client.post(f"/interview/inquiries/{inquiry_id}/resolve", headers=headers)

    r = admin_client.post(f"/interview/inquiries/{inquiry_id}/resolve", headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "invalid_inquiry_transition"


def test_unresolved_records_status_reason_and_audit_row(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]

    r = admin_client.post(
        f"/interview/inquiries/{inquiry_id}/unresolved",
        json={"status_reason": "research_required"}, headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "unresolved"
    assert body["status_reason"] == "research_required"
    assert body["closed_at"] is not None

    from app.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM interview_inquiry_transition WHERE inquiry_id = ? ORDER BY id",
            (inquiry_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["from_status"] == "open"
    assert rows[0]["to_status"] == "unresolved"
    assert rows[0]["reason"] == "research_required"


def test_hold_then_resume_round_trip(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]

    held = admin_client.post(f"/interview/inquiries/{inquiry_id}/hold", headers=headers)
    assert held.status_code == 200, held.text
    assert held.json()["status"] == "held"
    assert held.json()["closed_at"] is None  # held is resumable, not closed

    # Held Inquiries cannot take a follow-up message until resumed.
    blocked = admin_client.post(
        f"/interview/inquiries/{inquiry_id}/message", json={"content": "x"}, headers=headers,
    )
    assert blocked.status_code == 409, blocked.text

    resumed = admin_client.post(f"/interview/inquiries/{inquiry_id}/resume", headers=headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "open"

    from app.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT from_status, to_status FROM interview_inquiry_transition "
            "WHERE inquiry_id = ? ORDER BY id",
            (inquiry_id,),
        ).fetchall()
    assert [(r["from_status"], r["to_status"]) for r in rows] == [("open", "held"), ("held", "open")]


def test_cancel_records_audit_row(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]

    r = admin_client.post(f"/interview/inquiries/{inquiry_id}/cancel", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert r.json()["closed_at"] is not None

    from app.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT to_status FROM interview_inquiry_transition WHERE inquiry_id = ?",
            (inquiry_id,),
        ).fetchall()
    assert [r["to_status"] for r in rows] == ["cancelled"]


def test_reopen_doubt_is_a_noop_transition_with_audit_row(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]

    r = admin_client.post(f"/interview/inquiries/{inquiry_id}/reopen-doubt", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "open"

    from app.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT from_status, to_status, reason FROM interview_inquiry_transition WHERE inquiry_id = ?",
            (inquiry_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["from_status"] == "open"
    assert rows[0]["to_status"] == "open"


def test_reopen_doubt_rejected_when_not_open(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]
    admin_client.post(f"/interview/inquiries/{inquiry_id}/hold", headers=headers)

    r = admin_client.post(f"/interview/inquiries/{inquiry_id}/reopen-doubt", headers=headers)
    assert r.status_code == 409, r.text


@pytest.mark.parametrize("terminal_action", ["resolve", "cancel"])
def test_invalid_transitions_from_terminal_states_are_409(admin_client, monkeypatch, terminal_action):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch)
    inquiry_id = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"]).json()["inquiry"]["id"]
    admin_client.post(f"/interview/inquiries/{inquiry_id}/{terminal_action}", headers=headers)

    for action in ("hold", "resume", "unresolved"):
        r = admin_client.post(f"/interview/inquiries/{inquiry_id}/{action}", headers=headers)
        assert r.status_code == 409, f"{action} should be rejected after {terminal_action}"


# --- List / refresh-resume -----------------------------------------------------


def test_list_inquiries_filters_by_status(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa1 = _create_qa(admin_client, headers, session_id, "質問1")
    qa2 = _create_qa(admin_client, headers, session_id, "質問2")

    _stub_answer(monkeypatch)
    id1 = _open_inquiry(admin_client, headers, session_id, "qa", qa1["id"]).json()["inquiry"]["id"]
    id2 = _open_inquiry(admin_client, headers, session_id, "qa", qa2["id"]).json()["inquiry"]["id"]
    admin_client.post(f"/interview/inquiries/{id1}/resolve", headers=headers)

    all_items = admin_client.get(f"/interview/sessions/{session_id}/inquiries", headers=headers).json()
    assert {i["id"] for i in all_items["items"]} == {id1, id2}

    open_items = admin_client.get(
        f"/interview/sessions/{session_id}/inquiries?status=open", headers=headers,
    ).json()
    assert [i["id"] for i in open_items["items"]] == [id2]

    resolved_items = admin_client.get(
        f"/interview/sessions/{session_id}/inquiries?status=resolved", headers=headers,
    ).json()
    assert [i["id"] for i in resolved_items["items"]] == [id1]


def test_get_inquiry_detail_restores_full_state_for_refresh(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(
        monkeypatch, conclusion="根拠つきの回答",
        key_points=["ポイント1"], uncertainty="一部不明",
    )
    inquiry_id = _open_inquiry(
        admin_client, headers, session_id, "qa", qa["id"],
        held_draft=json.dumps({"draft": "入力中だった内容"}),
    ).json()["inquiry"]["id"]

    detail = admin_client.get(f"/interview/inquiries/{inquiry_id}", headers=headers).json()
    assert detail["inquiry"]["held_draft"] == json.dumps({"draft": "入力中だった内容"})
    assert detail["inquiry"]["origin_kind"] == "qa"
    assert detail["inquiry"]["origin_id"] == qa["id"]
    assistant = detail["messages"][1]
    assert assistant["content"] == "根拠つきの回答"
    assert assistant["detail"]["key_points"] == ["ポイント1"]
    assert assistant["detail"]["uncertainty"] == "一部不明"


# --- System isolation ----------------------------------------------------------


def test_inquiry_isolation_across_systems(admin_client, monkeypatch):
    token, system_a, snapshot_a = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")
    snapshot_b = _insert_snapshot(system_b["id"], commit_sha="bbb")

    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b["id"])

    session_a = _create_session(admin_client, headers_a, snapshot_a)
    qa_a = _create_qa(admin_client, headers_a, session_a)

    _stub_answer(monkeypatch)
    inquiry = _open_inquiry(admin_client, headers_a, session_a, "qa", qa_a["id"]).json()

    assert admin_client.get(
        f"/interview/sessions/{session_a}/inquiries", headers=headers_b,
    ).status_code == 404
    assert admin_client.get(
        f"/interview/inquiries/{inquiry['inquiry']['id']}", headers=headers_b,
    ).status_code == 404
    assert admin_client.post(
        f"/interview/inquiries/{inquiry['inquiry']['id']}/resolve", headers=headers_b,
    ).status_code == 404
    assert admin_client.post(
        f"/interview/sessions/{session_a}/inquiries",
        json={"origin_kind": "qa", "origin_id": qa_a["id"], "question_text": "乗っ取り"},
        headers=headers_b,
    ).status_code == 404


# --- Migration / table presence -----------------------------------------------


def test_migration_creates_inquiry_tables(admin_client):
    from app.db import get_conn, init_db

    init_db()
    with get_conn() as conn:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "interview_inquiry" in tables
    assert "interview_inquiry_message" in tables
    assert "interview_inquiry_transition" in tables


# --- Issue #290: runtime_fact evidence / progressive disclosure --------------


def _runtime_evidence_item(runtime_check="match", component_id="mod_fn"):
    from app.investigation_agent import InvestigationRuntimeEvidenceItem
    from app.models import RuntimeFactProvenanceOut

    return InvestigationRuntimeEvidenceItem(
        component_id=component_id,
        provenance=RuntimeFactProvenanceOut(
            environment=None, first_observed_at=1_000.0, last_observed_at=1_000.0,
            snapshot_ref=None, source="trace_aggregation", freshness="fresh",
        ),
        runtime_check=runtime_check,
        summary="call_count=42 の呼び出しを観測",
    )


def test_runtime_evidence_never_appears_in_message_content(admin_client, monkeypatch):
    """Progressive disclosure: content stays conclusion-first; raw
    provenance/runtime data lives only in detail.runtime_evidence."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(
        monkeypatch, conclusion="この関数は summarize を実行します。",
        runtime_evidence=[_runtime_evidence_item()],
    )
    created = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert created.status_code == 201, created.text

    assistant = created.json()["messages"][1]
    assert "trace_aggregation" not in assistant["content"]
    assert "42" not in assistant["content"]
    assert "call_count" not in assistant["content"]
    assert assistant["content"] == "この関数は summarize を実行します。"

    detail = assistant["detail"]
    assert len(detail["runtime_evidence"]) == 1
    entry = detail["runtime_evidence"][0]
    assert entry["component_id"] == "mod_fn"
    assert entry["runtime_check"] == "match"
    assert entry["provenance"]["source"] == "trace_aggregation"
    assert entry["provenance"]["freshness"] == "fresh"
    assert entry["provenance"]["environment"] is None


def test_suggested_observation_proposal_present_when_unobserved(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(
        monkeypatch, conclusion="この関数についての観測データがありません。",
        runtime_evidence=[_runtime_evidence_item(runtime_check="unobserved")],
    )
    created = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert created.status_code == 201, created.text

    detail = created.json()["messages"][1]["detail"]
    assert detail["suggested_observation_proposal"] == {
        "target_component": "mod_fn", "reason": "unobserved",
    }


def test_suggested_observation_proposal_absent_when_all_match(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_answer(monkeypatch, runtime_evidence=[_runtime_evidence_item(runtime_check="match")])
    created = _open_inquiry(admin_client, headers, session_id, "qa", qa["id"])
    assert created.status_code == 201, created.text

    detail = created.json()["messages"][1]["detail"]
    assert detail["suggested_observation_proposal"] is None
