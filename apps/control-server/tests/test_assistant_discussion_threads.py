"""Tests for Issue #438 (Epic #436): target-scoped assistant discussion
thread persistence.

`docs/assistant-discussion.md` §1 is the canonical contract. Acceptance
criteria under test:

1. Requirement A/B and Journey Step A/B conversations never mix
   (`TestBoundedConversations`).
2. After a reload the thread for a target can be restored
   (`TestReloadRestoresThread`).
3. Old turns recorded before a revision change are not treated as current
   fact (`TestStaleTargetExcludesOldTurns`).
4. System isolation (`TestSystemIsolation`) and bounded-context behavior
   (`TestBoundedConversations` / `TestStaleTargetExcludesOldTurns`) are
   covered by dedicated tests.

Fixture style mirrors `tests/test_assistant.py` (`admin_client` / `_login` /
`_create_system` / `_headers`) and `tests/test_ux_design.py` (`_create_journey`
/ `_add_journey_revision` / `_create_requirement`) so the fixtures under test
are exactly what the real API would produce.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-assistant-discussion-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER",
        "INTELLIGENCE_LLM_MODEL",
        "INTELLIGENCE_LLM_TIMEOUT",
        "INTELLIGENCE_MAX_OUTPUT_TOKENS",
        "CONTROL_API_KEYS",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIMEOUT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _create_system(client, token, name="assistant-discussion-sys"):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {
        "Authorization": f"Bearer {token}",
        "X-Probe-System-Id": str(system_id),
    }


def _create_journey(client, headers, journey_key, expect=201):
    r = client.post(
        "/ux-design/journeys",
        json={"journey_key": journey_key, "perspective": "to_be", "baseline_mode": "undecided"},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _step(step_key, order, **overrides):
    base = {
        "step_key": step_key,
        "step_order": order,
        "user_intent": f"intent-{step_key}",
        "system_response": f"response-{step_key}",
        "success_criteria": "criteria",
        "failure_mode": "",
        "recovery_path": "",
        "evidence_expectation": "",
        "evidence_source_kind": "none",
    }
    base.update(overrides)
    return base


def _add_journey_revision(client, headers, journey_key, *, steps=None, expect=201, **fields):
    payload = {
        "title": "", "beneficiary": "", "usage_context": "", "entry_trigger": "",
        "value_arrival": "", "summary": "", "change_note": "", "steps": steps or [],
    }
    payload.update(fields)
    r = client.post(f"/ux-design/journeys/{journey_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_requirement(client, headers, requirement_key, expect=201):
    r = client.post(
        "/ux-design/requirements",
        json={"requirement_key": requirement_key, "requirement_kind": "functional"},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_thread(client, headers, *, scope, screen_id, target_kind, target_ref, expect=200):
    r = client.post(
        "/assistant/discussion-threads",
        json={"scope": scope, "screen_id": screen_id, "target_kind": target_kind, "target_ref": target_ref},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _ask(client, headers, *, screen_id, question, thread_id=None, expect=200):
    payload = {"screen_id": screen_id, "question": question}
    if thread_id is not None:
        payload["thread_id"] = thread_id
    r = client.post("/assistant/ask", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


class _CapturingClient:
    """Records every `generate_text` call's messages, mirrors
    `test_assistant.py`'s `_DiscussionCaptureClient`."""

    def __init__(self):
        self.calls = []

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.calls.append(messages)
        return json.dumps({"answer": f"answer-{len(self.calls)}", "suggested_actions": [], "citations": []})


def _enable_real_llm(monkeypatch, fake_client):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr("app.routes.assistant.create_llm_client", lambda config: fake_client)


# --- Finite vocabulary / scope table -----------------------------------------


def test_scope_target_kind_table_matches_contract():
    from app.assistant_discussion import DISCUSSION_SCOPES, DISCUSSION_TARGET_KINDS, SCOPE_TARGET_KINDS

    assert set(DISCUSSION_SCOPES) == {"screen", "entity", "element"}
    assert SCOPE_TARGET_KINDS["screen"] == ("screen",)
    assert set(SCOPE_TARGET_KINDS["entity"]) == {
        "interview_session", "ux_journey", "ux_requirement", "solution_design",
    }
    assert set(SCOPE_TARGET_KINDS["element"]) == {
        "understanding_claim", "overview_finding", "ux_journey_step", "blueprint_lane_cell",
    }
    # Every target kind is reachable from exactly one scope (first-match, no overlap).
    all_kinds = [k for kinds in SCOPE_TARGET_KINDS.values() for k in kinds]
    assert sorted(all_kinds) == sorted(DISCUSSION_TARGET_KINDS)
    assert len(all_kinds) == len(set(all_kinds))


def test_scope_mismatch_is_rejected_fail_closed(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    r = admin_client.post(
        "/assistant/discussion-threads",
        json={"scope": "screen", "screen_id": "overview", "target_kind": "ux_journey", "target_ref": "j1"},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "discussion_target_scope_mismatch" in r.text


def test_resolve_or_create_is_idempotent(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    first = _create_thread(
        admin_client, headers, scope="screen", screen_id="overview",
        target_kind="screen", target_ref="overview",
    )
    second = _create_thread(
        admin_client, headers, scope="screen", screen_id="overview",
        target_kind="screen", target_ref="overview",
    )
    assert first["thread"]["id"] == second["thread"]["id"]
    assert first["thread"]["thread_key"] == "overview|screen|screen|overview"
    # `screen` has no digest source: always `not_tracked`, never `stale`.
    assert first["target_state"] == "not_tracked"


# --- Acceptance 1: Requirement A/B and Journey Step A/B never mix -----------


class TestBoundedConversations:
    def test_requirement_a_and_b_threads_never_mix(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-a")
        _create_requirement(admin_client, headers, "req-b")

        thread_a = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-a",
        )["thread"]
        thread_b = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-b",
        )["thread"]
        assert thread_a["id"] != thread_b["id"]
        assert thread_a["thread_key"] != thread_b["thread_key"]

        client = _CapturingClient()
        _enable_real_llm(monkeypatch, client)

        _ask(admin_client, headers, screen_id="ux-design-studio",
             question="Req-A question one", thread_id=thread_a["id"])
        _ask(admin_client, headers, screen_id="ux-design-studio",
             question="Req-A question two", thread_id=thread_a["id"])
        # The second req-a call's context must include req-a's own first
        # turn -- but never anything from req-b (which has no turns yet, so
        # this also proves the two threads are not sharing one context pool).
        second_call_text = json.dumps(client.calls[1])
        assert "Req-A question one" in second_call_text

        _ask(admin_client, headers, screen_id="ux-design-studio",
             question="Req-B question one", thread_id=thread_b["id"])
        third_call_text = json.dumps(client.calls[2])
        assert "Req-A question one" not in third_call_text
        assert "Req-A question two" not in third_call_text

        detail_a = admin_client.get(
            f"/assistant/discussion-threads/{thread_a['id']}", headers=headers
        ).json()
        detail_b = admin_client.get(
            f"/assistant/discussion-threads/{thread_b['id']}", headers=headers
        ).json()
        a_contents = [t["content"] for t in detail_a["turns"]]
        b_contents = [t["content"] for t in detail_b["turns"]]
        assert "Req-A question one" in a_contents
        assert "Req-A question two" in a_contents
        assert "Req-B question one" in b_contents
        assert not (set(a_contents) & set(b_contents))

    def test_journey_step_a_and_b_threads_never_mix(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(
            admin_client, headers, "checkout",
            steps=[_step("s1", 1), _step("s2", 2)],
        )

        thread_s1 = _create_thread(
            admin_client, headers, scope="element", screen_id="journey-blueprint",
            target_kind="ux_journey_step", target_ref="checkout#s1",
        )["thread"]
        thread_s2 = _create_thread(
            admin_client, headers, scope="element", screen_id="journey-blueprint",
            target_kind="ux_journey_step", target_ref="checkout#s2",
        )["thread"]
        assert thread_s1["id"] != thread_s2["id"]

        client = _CapturingClient()
        _enable_real_llm(monkeypatch, client)

        _ask(admin_client, headers, screen_id="journey-blueprint",
             question="Step-1 question", thread_id=thread_s1["id"])
        _ask(admin_client, headers, screen_id="journey-blueprint",
             question="Step-2 question", thread_id=thread_s2["id"])
        second_call_text = json.dumps(client.calls[1])
        assert "Step-1 question" not in second_call_text

        detail_s1 = admin_client.get(
            f"/assistant/discussion-threads/{thread_s1['id']}", headers=headers
        ).json()
        detail_s2 = admin_client.get(
            f"/assistant/discussion-threads/{thread_s2['id']}", headers=headers
        ).json()
        assert [t["content"] for t in detail_s1["turns"]] == ["Step-1 question", "answer-1"]
        assert [t["content"] for t in detail_s2["turns"]] == ["Step-2 question", "answer-2"]


# --- Acceptance 2: reload restores the thread for a target -------------------


class TestReloadRestoresThread:
    def test_resolve_or_create_restores_the_same_thread_and_turns(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-x")

        client = _CapturingClient()
        _enable_real_llm(monkeypatch, client)

        created = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-x",
        )
        thread_id = created["thread"]["id"]
        _ask(admin_client, headers, screen_id="ux-design-studio",
             question="What is missing?", thread_id=thread_id)

        # Simulate a page reload: resolve-or-create for the SAME target again.
        reloaded = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-x",
        )
        assert reloaded["thread"]["id"] == thread_id
        assert [t["content"] for t in reloaded["turns"]] == ["What is missing?", "answer-1"]
        assert reloaded["turns"][0]["turn_number"] == 1
        assert reloaded["turns"][1]["turn_number"] == 2
        assert reloaded["turns"][0]["decision_method"] == "manual"
        assert reloaded["turns"][1]["decision_method"] == "reasoning_llm"

        # And the dedicated GET-by-id endpoint restores the same thing.
        by_id = admin_client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers).json()
        assert [t["content"] for t in by_id["turns"]] == ["What is missing?", "answer-1"]

    def test_list_threads_is_system_scoped_and_newest_first(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_requirement(admin_client, headers, "req-1")
        _create_requirement(admin_client, headers, "req-2")
        t1 = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-1",
        )["thread"]
        t2 = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_requirement", target_ref="req-2",
        )["thread"]

        r = admin_client.get("/assistant/discussion-threads", headers=headers)
        assert r.status_code == 200, r.text
        ids = [t["id"] for t in r.json()["threads"]]
        assert ids[:2] == [t2["id"], t1["id"]]

        filtered = admin_client.get(
            "/assistant/discussion-threads?target_kind=ux_requirement&target_ref=req-1",
            headers=headers,
        ).json()["threads"]
        assert [t["id"] for t in filtered] == [t1["id"]]


# --- Acceptance 3: old turns are not treated as current fact after a revision


class TestStaleTargetExcludesOldTurns:
    def test_journey_revision_change_marks_thread_stale_and_excludes_old_turns(
        self, admin_client, monkeypatch
    ):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        _create_journey(admin_client, headers, "checkout")
        _add_journey_revision(
            admin_client, headers, "checkout", steps=[_step("s1", 1, user_intent="find item v1")],
        )

        client = _CapturingClient()
        _enable_real_llm(monkeypatch, client)

        created = _create_thread(
            admin_client, headers, scope="entity", screen_id="ux-design-studio",
            target_kind="ux_journey", target_ref="checkout",
        )
        thread_id = created["thread"]["id"]
        assert created["target_state"] == "current"

        first = _ask(admin_client, headers, screen_id="ux-design-studio",
                      question="Describe step 1", thread_id=thread_id)
        assert first["target_state"] == "current"
        assert first["recheck_required"] is False

        # The journey's content changes -- a new revision with different
        # step content changes `content_digest` (§1.2).
        _add_journey_revision(
            admin_client, headers, "checkout", steps=[_step("s1", 1, user_intent="find item v2")],
        )

        reread = admin_client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers).json()
        assert reread["target_state"] == "stale"
        # The history is still readable...
        assert [t["content"] for t in reread["turns"]] == ["Describe step 1", "answer-1"]

        # ...but is NOT auto-inherited as current fact for the next answer.
        second = _ask(admin_client, headers, screen_id="ux-design-studio",
                       question="What changed?", thread_id=thread_id)
        assert second["target_state"] == "stale"
        assert second["recheck_required"] is True
        second_call_text = json.dumps(client.calls[-1])
        assert "Describe step 1" not in second_call_text
        assert "answer-1" not in second_call_text
        # Only the system/context + the new question are sent -- no
        # inherited conversation turns.
        assert len(client.calls[-1]) == 3
        assert client.calls[-1][-1]["content"] == "What changed?"

        # Answering against the now-current content re-syncs the thread.
        final_state = admin_client.get(
            f"/assistant/discussion-threads/{thread_id}", headers=headers
        ).json()
        assert final_state["target_state"] == "current"
        assert [t["content"] for t in final_state["turns"]] == [
            "Describe step 1", "answer-1", "What changed?", "answer-2",
        ]

    def test_interview_session_understanding_change_marks_thread_stale(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        system_id = system["id"]

        with get_conn() as conn:
            now = 1_700_000_000.0
            conn.execute(
                """INSERT INTO repository_snapshots
                       (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/repo', 'a' * 40, 'ready', ?, ?)""",
                (system_id, now, now),
            )
            snapshot_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.execute(
                """INSERT INTO interview_session
                       (system_id, snapshot_id, title, current_understanding, created_at, updated_at)
                   VALUES (?, ?, 'session', ?, ?, ?)""",
                (system_id, snapshot_id, json.dumps({"vision": []}), now, now),
            )
            session_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        created = _create_thread(
            admin_client, headers, scope="entity", screen_id="interview",
            target_kind="interview_session", target_ref=str(session_id),
        )
        assert created["target_state"] == "current"
        thread_id = created["thread"]["id"]

        with get_conn() as conn:
            conn.execute(
                "UPDATE interview_session SET current_understanding = ? WHERE id = ?",
                (json.dumps({"vision": [{"name": "changed"}]}), session_id),
            )

        reread = admin_client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers).json()
        assert reread["target_state"] == "stale"


# --- Acceptance 4: System isolation -------------------------------------------


class TestSystemIsolation:
    def test_foreign_system_gets_404_on_get_and_ask(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, name="sys-a")
        system_b = _create_system(admin_client, token, name="sys-b")
        headers_a = _headers(token, system_a["id"])
        headers_b = _headers(token, system_b["id"])

        created = _create_thread(
            admin_client, headers_a, scope="screen", screen_id="overview",
            target_kind="screen", target_ref="overview",
        )
        thread_id = created["thread"]["id"]

        r = admin_client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers_b)
        assert r.status_code == 404, r.text

        r = admin_client.post(
            "/assistant/ask",
            json={"screen_id": "overview", "question": "hi", "thread_id": thread_id},
            headers=headers_b,
        )
        assert r.status_code == 404, r.text

        # System A itself can still read it.
        r = admin_client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers_a)
        assert r.status_code == 200, r.text

    def test_listing_is_scoped_per_system(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, name="sys-a2")
        system_b = _create_system(admin_client, token, name="sys-b2")
        headers_a = _headers(token, system_a["id"])
        headers_b = _headers(token, system_b["id"])

        _create_thread(
            admin_client, headers_a, scope="screen", screen_id="overview",
            target_kind="screen", target_ref="overview",
        )
        threads_b = admin_client.get("/assistant/discussion-threads", headers=headers_b).json()["threads"]
        assert threads_b == []

    def test_unknown_thread_id_is_404(self, admin_client):
        token = _login(admin_client)
        system = _create_system(admin_client, token)
        headers = _headers(token, system["id"])
        r = admin_client.get("/assistant/discussion-threads/999999", headers=headers)
        assert r.status_code == 404, r.text
        r = admin_client.post(
            "/assistant/ask",
            json={"screen_id": "overview", "question": "hi", "thread_id": 999999},
            headers=headers,
        )
        assert r.status_code == 404, r.text


# --- thread_id / conversation mutual exclusion -------------------------------


def test_thread_id_with_nonempty_conversation_is_rejected(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    created = _create_thread(
        admin_client, headers, scope="screen", screen_id="overview",
        target_kind="screen", target_ref="overview",
    )
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "overview",
            "question": "hi",
            "thread_id": created["thread"]["id"],
            "conversation": [{"role": "user", "content": "leftover"}],
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "conversation_not_settable_with_thread" in r.text


# --- Resolvers never raise on a missing/unresolvable target -------------------


def test_resolvers_degrade_to_unresolved_never_raise(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    system_id = system["id"]

    from app.assistant_discussion import resolve_target

    assert resolve_target(system_id, "overview_finding", "does-not-exist").resolution == "unresolved"
    assert resolve_target(system_id, "understanding_claim", "vision:nope").resolution == "unresolved"
    assert resolve_target(system_id, "understanding_claim", "not-a-valid-ref").resolution == "unresolved"
    assert resolve_target(system_id, "ux_journey", "no-such-journey").resolution == "unresolved"
    assert resolve_target(system_id, "ux_requirement", "no-such-requirement").resolution == "unresolved"
    assert resolve_target(system_id, "solution_design", "no-such-design").resolution == "unresolved"
    assert resolve_target(system_id, "ux_journey_step", "no-such-journey#s1").resolution == "unresolved"
    assert resolve_target(system_id, "blueprint_lane_cell", "no-such-journey#s1#frontstage").resolution == "unresolved"
    assert resolve_target(system_id, "interview_session", "999999").resolution == "unresolved"
    assert resolve_target(system_id, "interview_session", "not-an-int").resolution == "unresolved"
    # `screen` never has a digest source.
    resolved_screen = resolve_target(system_id, "screen", "overview")
    assert resolved_screen.resolution == "not_tracked"


# --- Bounded LLM context (§1.5: 直近 12 turn) ---------------------------------


def test_llm_context_is_bounded_to_the_most_recent_turns(admin_client, monkeypatch):
    """A long conversation sends a bounded slice, and it is the RECENT one.

    The bound is what keeps a long-running discussion from growing an
    unbounded prompt; asserting only "<= 12" would also pass if the server
    sent the OLDEST 12, which is the opposite of a conversation context.
    """
    from app.assistant_discussion import MAX_CONTEXT_TURNS

    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    created = _create_thread(
        admin_client, headers, scope="screen", screen_id="overview",
        target_kind="screen", target_ref="overview",
    )
    thread_id = created["thread"]["id"]

    client = _CapturingClient()
    _enable_real_llm(monkeypatch, client)

    # Each ask appends 2 turns (user + assistant), so 10 asks is well past
    # the bound in both turns and questions.
    for i in range(10):
        _ask(admin_client, headers, screen_id="overview",
             question=f"question {i}", thread_id=thread_id)

    # The messages the client received: system prompt, the context pack, the
    # thread's bounded history, then the current question.
    last_call = client.calls[-1]
    history = [m for m in last_call if m.get("role") in ("user", "assistant")]
    # -2 for the context-pack message and the current question, both of which
    # are not thread turns.
    thread_history = history[1:-1]
    assert len(thread_history) <= MAX_CONTEXT_TURNS

    text = json.dumps(thread_history, ensure_ascii=False)
    # The most recent question before this one is present...
    assert "question 8" in text
    # ...and the oldest ones have fallen out of the window.
    assert "question 0" not in text


# --- Issue #441: the entry mode is recorded on the human's turn --------------


def test_voice_input_mode_is_recorded_on_the_user_turn_only(admin_client, monkeypatch):
    """`input_mode` says how the DEVELOPER entered the question.

    The assistant turn keeps `text` deliberately: it did not speak into a
    microphone, and whether the client read its answer aloud is a playback
    choice rather than a fact about the turn. Recording `voice` on both would
    give one column two meanings.
    """
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    created = _create_thread(
        admin_client, headers, scope="screen", screen_id="overview",
        target_kind="screen", target_ref="overview",
    )
    thread_id = created["thread"]["id"]

    client = _CapturingClient()
    _enable_real_llm(monkeypatch, client)

    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "overview",
            "question": "声で聞いた質問",
            "thread_id": thread_id,
            "input_mode": "voice",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text

    detail = admin_client.get(
        f"/assistant/discussion-threads/{thread_id}", headers=headers
    ).json()
    by_role = {t["role"]: t for t in detail["turns"]}
    assert by_role["user"]["input_mode"] == "voice"
    assert by_role["assistant"]["input_mode"] == "text"


def test_input_mode_defaults_to_text_and_rejects_anything_else(admin_client, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    created = _create_thread(
        admin_client, headers, scope="screen", screen_id="overview",
        target_kind="screen", target_ref="overview",
    )
    thread_id = created["thread"]["id"]

    client = _CapturingClient()
    _enable_real_llm(monkeypatch, client)

    _ask(admin_client, headers, screen_id="overview", question="文字で聞いた", thread_id=thread_id)
    detail = admin_client.get(
        f"/assistant/discussion-threads/{thread_id}", headers=headers
    ).json()
    assert all(t["input_mode"] == "text" for t in detail["turns"])

    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "overview", "question": "x",
            "thread_id": thread_id, "input_mode": "telepathy",
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text
