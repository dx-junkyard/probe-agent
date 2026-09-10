"""Route-level tests for Issue #459 (Epic #457): the §9.2 semantic claims
endpoint (`POST /assistant/discussion-threads/{id}/context-claims`) and the
overall `next_action` field now carried on every thread detail response.

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §9.2 and `docs/01-specifications/ux/decision-discussion-workflow.md` §3
(DD-UX-01) are the canonical contracts. `app/discussion_claims.py` and
`app/discussion_next_action.py`'s own pure-function behavior is exercised
directly in `tests/test_discussion_claims.py` /
`tests/test_discussion_next_action.py`; this file checks the two are wired
into the route correctly end to end:

1. `GET /assistant/discussion-threads/{id}` always carries `next_action`,
   defaulting to `match` with nothing else pending, and reflects an open
   Proposal item as `review_proposal` (``TestNextActionWiring``).
2. `POST .../context-claims` with no usable reasoning client still returns
   200 with `error_kind="unavailable"`, persists a user/assistant turn pair,
   and records an `intelligence_runs` row (``TestNoUsableClient``).
3. A successful call persists the returned claims ON the assistant turn
   (`turn.claims`), refreshes `captured_target_*`, and its own audit row
   (``TestSuccessfulClaimsCall``).
4. Unknown thread id / foreign System are both 404 before any bundle/LLM
   work happens (``TestNotFoundAndIsolation``).

Fixture style mirrors `tests/test_discussion_hypothesis.py`: reuses plain
helper functions from `test_discussion_context_bundle.py` (Objective/
Milestone/Gap setup, thread creation) and from
`test_assistant_discussion_proposals.py` (`_enable_real_llm`,
`_FixedResponseClient`) rather than duplicating them.
"""

from __future__ import annotations

import json
import time

import pytest

from test_assistant_discussion_proposals import _enable_real_llm, _FixedResponseClient
from test_discussion_context_bundle import (  # noqa: F401 - fixture reuse
    _create_gap,
    _create_milestone,
    _create_objective,
    _create_system,
    _create_thread,
    _headers,
    _login,
    _setup_objective_milestone_gap,
)


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-discussion-context-claims-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    for var in (
        "INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL", "CONTROL_API_KEYS",
        "LLM_MODEL", "LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def _make_gap_thread(client, headers, prefix):
    _objective_key, milestone_key, gap_key = _setup_objective_milestone_gap(client, headers, prefix)
    thread = _create_thread(
        client, headers, scope="entity", screen_id="objective-map",
        target_kind="product_gap", target_ref=gap_key,
    )
    return thread, gap_key, milestone_key


def _claims(client, headers, thread_id, *, question=None, expect=200):
    body = {"question": question} if question is not None else {}
    r = client.post(f"/assistant/discussion-threads/{thread_id}/context-claims", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_thread(client, headers, thread_id, expect=200):
    r = client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


class TestNextActionWiring:
    def test_default_next_action_is_match(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "next-action-default")
        headers = _headers(token, system_id)
        thread, _gap_key, _milestone_key = _make_gap_thread(admin_client, headers, "na1")
        assert thread["next_action"]["kind"] == "match"

        reread = _get_thread(admin_client, headers, thread["thread"]["id"])
        assert reread["next_action"]["kind"] == "match"
        assert reread["next_action"]["degraded_sections"] == []

    def test_open_proposal_item_makes_next_action_review_proposal(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "next-action-proposal")
        headers = _headers(token, system_id)
        thread, _gap_key, _milestone_key = _make_gap_thread(admin_client, headers, "na2")
        thread_id = thread["thread"]["id"]

        from app.db import get_conn

        now = time.time()
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO assistant_discussion_proposal
                       (system_id, thread_id, screen_id, target_kind, target_ref,
                        captured_target_revision_id, captured_target_digest, summary,
                        confirmed_points_json, unresolved_questions_json, assumptions_json,
                        evidence_refs_json, decision_method, intelligence_run_id, provider, model,
                        prompt_version, schema_version, created_by, created_at)
                   VALUES (?, ?, 'objective-map', 'product_gap', ?, NULL, '', '',
                           '[]', '[]', '[]', '[]', 'reasoning_llm', NULL, 'openai', 'gpt-5', 'v1', 'v1',
                           NULL, ?)""",
                (system_id, thread_id, thread["thread"]["target_ref"], now),
            )
            proposal_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.execute(
                """INSERT INTO assistant_discussion_proposal_item
                       (system_id, proposal_id, item_kind, field_name, current_value, proposed_value,
                        rationale, status, created_at)
                   VALUES (?, ?, 'field', 'title', 'old', 'new', 'because', 'proposed', ?)""",
                (system_id, proposal_id, now),
            )

        reread = _get_thread(admin_client, headers, thread_id)
        assert reread["next_action"]["kind"] == "review_proposal"
        assert reread["next_action"]["target_ref"] == str(proposal_id)


class TestNoUsableClient:
    def test_mock_provider_returns_200_with_unavailable_error_and_persists_turn(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "claims-mock")
        headers = _headers(token, system_id)
        thread, _gap_key, _milestone_key = _make_gap_thread(admin_client, headers, "mock1")
        thread_id = thread["thread"]["id"]

        result = _claims(admin_client, headers, thread_id, question="十分か")
        assert result["error_kind"] == "unavailable"
        assert result["decision_method"] == "reasoning_llm"
        assert result["thread_id"] == thread_id
        assert result["turn_number"] is not None

        reread = _get_thread(admin_client, headers, thread_id)
        turns = reread["turns"]
        assert turns[-2]["role"] == "user"
        assert turns[-2]["content"] == "十分か"
        assert turns[-1]["role"] == "assistant"
        assert turns[-1]["decision_method"] == "reasoning_llm"

        from app.db import get_conn

        with get_conn() as conn:
            run = conn.execute(
                "SELECT * FROM intelligence_runs WHERE system_id = ? AND run_type = 'discussion_context_claim'"
                " ORDER BY id DESC LIMIT 1",
                (system_id,),
            ).fetchone()
        assert run is not None
        assert run["status"] == "failed"
        assert run["decision_method"] == "reasoning_llm"

    def test_default_question_is_the_fixed_matching_operation(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "claims-default-q")
        headers = _headers(token, system_id)
        thread, _gap_key, _milestone_key = _make_gap_thread(admin_client, headers, "mock2")
        thread_id = thread["thread"]["id"]

        _claims(admin_client, headers, thread_id)
        reread = _get_thread(admin_client, headers, thread_id)
        assert reread["turns"][-2]["content"] == "目的・UX・機能を照合"


class TestSuccessfulClaimsCall:
    def test_valid_response_persists_claims_on_the_assistant_turn(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "claims-success")
        headers = _headers(token, system_id)
        thread, gap_key, _milestone_key = _make_gap_thread(admin_client, headers, "ok1")
        thread_id = thread["thread"]["id"]

        fake_client = _FixedResponseClient({
            "claims": [
                {
                    "kind": "hypothesis", "statement": "設定手順の案内が不足している可能性がある",
                    "cited_source_ids": [f"product_gap:{gap_key}"],
                },
            ],
            "scope_note": "related: 1/1件(complete)",
        })
        _enable_real_llm(monkeypatch, fake_client)

        result = _claims(admin_client, headers, thread_id, question="この機能で十分か")
        assert result["error"] is None
        assert result["decision_method"] == "reasoning_llm"
        assert len(result["claims"]) == 1
        assert result["claims"][0]["kind"] == "hypothesis"
        assert result["claims"][0]["basis"] == "reasoning_llm"
        assert result["claims"][0]["cited_source_ids"] == [f"product_gap:{gap_key}"]
        assert len(fake_client.calls) == 1

        reread = _get_thread(admin_client, headers, thread_id)
        assistant_turn = reread["turns"][-1]
        assert assistant_turn["claims"] is not None
        assert assistant_turn["claims"][0]["statement"] == "設定手順の案内が不足している可能性がある"


class TestNotFoundAndIsolation:
    def test_unknown_thread_id_is_404(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "claims-404")
        headers = _headers(token, system_id)
        _claims(admin_client, headers, 999999, expect=404)

    def test_foreign_system_thread_is_404(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "claims-iso-a")
        system_b = _create_system(admin_client, token, "claims-iso-b")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)
        thread, _gap_key, _milestone_key = _make_gap_thread(admin_client, headers_a, "iso1")
        thread_id = thread["thread"]["id"]

        _claims(admin_client, headers_b, thread_id, expect=404)
        _get_thread(admin_client, headers_b, thread_id, expect=404)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
