"""Tests for Issue #286: Question Router (app/question_router.py).

Covers:
1. route_question: each category parsed, research_focus forced null for
   human_only, fail-closed on mock/non-reasoning clients, LLM errors,
   malformed JSON, and an out-of-set category.
2. POST /interview/qa/{qa_id}/route: persists route_category/route_run_id
   on success, records a failed intelligence_runs row and leaves the
   question unrouted on failure, and System isolation.
3. Issue #291: additive knowledge_area field (schema/prompt version bumped
   to 'question-router-v2' at the time) -- null and each valid enum value
   parse, an invalid value fails closed, and the route endpoint persists it.
4. Review fix: additive search_keywords field bumped prompt/schema version
   again to 'question-router-v3' -- parsed for system_researchable/hybrid,
   forced to [] for human_only, and defaults to [] when the model omits the
   field (backward compatible).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from app.question_router import (
    KNOWLEDGE_AREAS,
    PROMPT_VERSION,
    ROUTE_CATEGORIES,
    SCHEMA_VERSION,
    route_question,
)


def _make_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


def _mock_config():
    return LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30)


class FakeLLMClient(LLMClient):
    def __init__(self, response: Any = None, error: Optional[str] = None):
        self._response = response
        self._error = error

    def generate_text(self, messages: List[Dict[str, str]], *, temperature=None, max_tokens=None) -> str:
        if self._error:
            raise LLMError(self._error)
        return json.dumps(self._response)


# --- Unit tests: route_question ------------------------------------------------


def test_route_question_fails_closed_on_mock_client():
    result = route_question(MockLLMClient(), _mock_config(), question_text="q")
    assert result.error is not None
    assert result.category is None


def test_route_question_fails_closed_on_non_reasoning_model():
    client = FakeLLMClient(response={"category": "human_only", "reason": "r", "research_focus": None})
    result = route_question(client, _make_config(model="claude-3-5-haiku-latest"), question_text="q")
    assert result.error is not None
    assert result.category is None


def test_route_question_fails_closed_on_llm_error():
    client = FakeLLMClient(error="upstream timeout")
    result = route_question(client, _make_config(), question_text="q")
    assert result.error == "upstream timeout"


def test_route_question_fails_closed_on_invalid_json():
    client = FakeLLMClient(response={"category": 123})
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is not None


def test_route_question_fails_closed_on_unknown_category():
    client = FakeLLMClient(response={"category": "not_a_real_category", "reason": "r", "research_focus": None})
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is not None
    assert "invalid category" in result.error


@pytest.mark.parametrize("category", ROUTE_CATEGORIES)
def test_route_question_parses_each_category(category):
    client = FakeLLMClient(response={
        "category": category, "reason": "根拠",
        "research_focus": None if category == "human_only" else "the summarize function",
    })
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is None
    assert result.category == category
    assert result.reason == "根拠"
    assert result.prompt_version == PROMPT_VERSION
    assert result.schema_version == SCHEMA_VERSION


def test_route_question_prompt_and_schema_version_bumped_for_search_keywords():
    """A later review fix added search_keywords additively -- both versions
    bumped again to 'question-router-v3' (Principle 7: any prompt/schema
    change is a new version)."""
    assert PROMPT_VERSION == "question-router-v3"
    assert SCHEMA_VERSION == "question-router-v3"


def test_route_question_accepts_null_knowledge_area():
    client = FakeLLMClient(response={
        "category": "human_only", "reason": "r", "research_focus": None, "knowledge_area": None,
    })
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is None
    assert result.knowledge_area is None


@pytest.mark.parametrize("area", KNOWLEDGE_AREAS)
def test_route_question_parses_each_knowledge_area(area):
    client = FakeLLMClient(response={
        "category": "system_researchable", "reason": "r",
        "research_focus": "focus", "knowledge_area": area,
    })
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is None
    assert result.knowledge_area == area


def test_route_question_fails_closed_on_unknown_knowledge_area():
    client = FakeLLMClient(response={
        "category": "system_researchable", "reason": "r",
        "research_focus": "focus", "knowledge_area": "not_a_real_area",
    })
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is not None
    assert "invalid knowledge_area" in result.error


def test_route_question_missing_knowledge_area_defaults_to_none():
    """Backward compatible: an older prompt reply that omits the field
    entirely is still valid, not a parse failure."""
    client = FakeLLMClient(response={
        "category": "human_only", "reason": "r", "research_focus": None,
    })
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is None
    assert result.knowledge_area is None


def test_route_question_forces_research_focus_null_for_human_only():
    """Even if the model returns a research_focus for human_only, it is
    discarded -- there is nothing to investigate for a pure decision."""
    client = FakeLLMClient(response={
        "category": "human_only", "reason": "r", "research_focus": "should be discarded",
    })
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is None
    assert result.research_focus is None


# --- search_keywords (review fix) ----------------------------------------------


@pytest.mark.parametrize("category", ["system_researchable", "hybrid"])
def test_route_question_parses_search_keywords(category):
    client = FakeLLMClient(response={
        "category": category, "reason": "r", "research_focus": "focus",
        "search_keywords": ["auth", "login", "token", "session"],
    })
    result = route_question(client, _make_config(), question_text="認証はどのように実装されていますか")
    assert result.error is None
    assert result.search_keywords == ["auth", "login", "token", "session"]


def test_route_question_forces_search_keywords_empty_for_human_only():
    """Even if the model returns search_keywords for human_only, they are
    discarded -- same rule as research_focus -> None (nothing to search
    for when investigation never runs)."""
    client = FakeLLMClient(response={
        "category": "human_only", "reason": "r", "research_focus": None,
        "search_keywords": ["should", "be", "discarded"],
    })
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is None
    assert result.search_keywords == []


def test_route_question_missing_search_keywords_defaults_to_empty_list():
    """Backward compatible: an older prompt reply that omits the field
    entirely is still valid, not a parse failure."""
    client = FakeLLMClient(response={
        "category": "system_researchable", "reason": "r", "research_focus": "focus",
    })
    result = route_question(client, _make_config(), question_text="q")
    assert result.error is None
    assert result.search_keywords == []


# --- Route-level: POST /interview/qa/{qa_id}/route -----------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-question-router-test.db"))
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


def _stub_route(
    monkeypatch, *, category="system_researchable", reason="r", research_focus="focus",
    knowledge_area=None, error=None,
):
    from app.routes import question_router as router_routes
    from app.question_router import RouteResult

    def fake_create_llm_client(config):
        return object()

    def fake_route_question(client, config, **kwargs):
        return RouteResult(
            provider="anthropic", model="claude-sonnet-4-5", is_mock=False,
            category=None if error else category, reason=reason,
            research_focus=research_focus, knowledge_area=knowledge_area, error=error,
        )

    monkeypatch.setattr(router_routes, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(router_routes, "route_question", fake_route_question)


def test_route_qa_persists_category_and_run_id(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_route(monkeypatch, category="hybrid")
    r = admin_client.post(f"/interview/qa/{qa['id']}/route", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["route_category"] == "hybrid"
    assert body["route_run_id"] is not None

    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM intelligence_runs WHERE id = ?", (body["route_run_id"],),
        ).fetchone()
    assert run["run_type"] == "question_route"
    assert run["status"] == "completed"


def test_route_qa_persists_knowledge_area(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_route(monkeypatch, category="hybrid", knowledge_area="implementation")
    r = admin_client.post(f"/interview/qa/{qa['id']}/route", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["knowledge_area"] == "implementation"

    listed = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    item = next(i for i in listed["items"] if i["id"] == qa["id"])
    assert item["knowledge_area"] == "implementation"


def test_route_qa_null_knowledge_area_stays_askable(admin_client, monkeypatch):
    """An unrouted (or routed-but-no-area) question is never hidden."""
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_route(monkeypatch, category="human_only", knowledge_area=None)
    r = admin_client.post(f"/interview/qa/{qa['id']}/route", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["knowledge_area"] is None

    admin_client.put(
        f"/interview/sessions/{session_id}/answerable-areas",
        json={"areas": ["security"]},
        headers=headers,
    )
    askable = admin_client.get(
        f"/interview/sessions/{session_id}/qa?view=askable", headers=headers,
    ).json()
    assert any(i["id"] == qa["id"] for i in askable["items"])


def test_route_qa_fails_closed_leaves_question_unrouted(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = _create_qa(admin_client, headers, session_id)

    _stub_route(monkeypatch, error="upstream timeout")
    r = admin_client.post(f"/interview/qa/{qa['id']}/route", headers=headers)
    assert r.status_code == 502, r.text
    assert r.json()["detail"]["code"] == "question_route_failed"

    listed = admin_client.get(f"/interview/sessions/{session_id}/qa", headers=headers).json()
    item = next(i for i in listed["items"] if i["id"] == qa["id"])
    assert item["route_category"] is None
    assert item["route_run_id"] is None

    from app.db import get_conn

    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM intelligence_runs WHERE run_type = 'question_route'"
        ).fetchone()
    assert run["status"] == "failed"
    assert run["error_details"] == "upstream timeout"


def test_route_qa_unknown_question_404(admin_client, monkeypatch):
    token, system_id, snapshot_id = _setup(admin_client)
    headers = _headers(token, system_id)
    _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post("/interview/qa/999999/route", headers=headers)
    assert r.status_code == 404, r.text


def test_route_qa_isolation_across_systems(admin_client, monkeypatch):
    token, system_a, snapshot_a = _setup(admin_client, "System A")
    system_b = _create_system(admin_client, token, "System B")

    headers_a = _headers(token, system_a)
    headers_b = _headers(token, system_b["id"])

    session_a = _create_session(admin_client, headers_a, snapshot_a)
    qa_a = _create_qa(admin_client, headers_a, session_a)

    _stub_route(monkeypatch)
    r = admin_client.post(f"/interview/qa/{qa_a['id']}/route", headers=headers_b)
    assert r.status_code == 404, r.text
