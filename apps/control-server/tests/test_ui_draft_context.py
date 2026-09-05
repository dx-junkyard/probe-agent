"""Tests for Issue #445 (Epic #443 Phase 2): UiDraftContext.

`docs/ai-discussion-adapter.md` §2 is the canonical contract. Acceptance
criteria under test:

1. canonical facts and the UI draft are distinguishable in the LLM payload
   (`ui_draft` is top-level, never nested inside `screen_data`) and in the
   persisted turn.
2. every §2.3/§2.7 422 code, including that an unregistered field name
   rejects the WHOLE request.
3. the payload-budget bound and secret redaction before the LLM call.
4. no draft VALUES are ever persisted.
5. `ui_draft_changed` / `recheck_required` derivation across two turns.
6. a request without `ui_draft` behaves exactly as it did before #445.
7. System isolation.

Fixture style mirrors `tests/test_assistant_discussion_threads.py`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-ui-draft-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PROBE_REPOSITORY_ROOTS", str(tmp_path))
    for var in (
        "INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL", "INTELLIGENCE_LLM_TIMEOUT",
        "INTELLIGENCE_MAX_OUTPUT_TOKENS", "CONTROL_API_KEYS", "LLM_MODEL", "LLM_API_KEY",
        "LLM_TIMEOUT", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
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


def _create_system(client, token, name="ui-draft-sys"):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": name},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_journey(client, headers, journey_key, expect=201):
    r = client.post(
        "/ux-design/journeys",
        json={"journey_key": journey_key, "perspective": "to_be", "baseline_mode": "undecided"},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _create_requirement(client, headers, requirement_key, expect=201):
    r = client.post(
        "/ux-design/requirements",
        json={"requirement_key": requirement_key, "requirement_kind": "functional"},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _create_thread(client, headers, *, scope, screen_id, target_kind, target_ref, expect=200):
    r = client.post(
        "/assistant/discussion-threads",
        json={"scope": scope, "screen_id": screen_id, "target_kind": target_kind, "target_ref": target_ref},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json()


def _ui_draft(*, target_kind, target_ref, form_id="ux_journey.revision", fields=None, **overrides):
    payload = {
        "target_kind": target_kind,
        "target_ref": target_ref,
        "form_id": form_id,
        "fields": fields if fields is not None else [
            {"field_name": "title", "value": "draft title", "dirty": True, "validation_error": ""},
        ],
        "selected_item_ref": "",
        "active_tab": "",
        "comparison_target": "",
        "captured_at": 1_700_000_000.0,
        "local_revision_token": "token-1",
    }
    payload.update(overrides)
    return payload


def _ask(client, headers, *, screen_id="ux-design-studio", question="質問", thread_id=None, ui_draft=None, expect=200):
    payload = {"screen_id": screen_id, "question": question}
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if ui_draft is not None:
        payload["ui_draft"] = ui_draft
    r = client.post("/assistant/ask", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


class _CapturingClient:
    def __init__(self):
        self.calls = []

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.calls.append(messages)
        return json.dumps({
            "answer": f"answer-{len(self.calls)}",
            "suggested_actions": [],
            "citations": [{"type": "ui_draft", "id": "ui_draft:ux_journey.revision"}],
        })


def _enable_real_llm(monkeypatch, fake_client):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr("app.routes.assistant.create_llm_client", lambda config: fake_client)


def _make_journey_thread(client, headers, journey_key="journey-1"):
    _create_journey(client, headers, journey_key)
    created = _create_thread(
        client, headers, scope="entity", screen_id="ux-design-studio",
        target_kind="ux_journey", target_ref=journey_key,
    )
    return created["thread"]["id"]


# --- 1. canonical facts vs. ui_draft are distinguishable ---------------------


def test_ui_draft_is_top_level_not_inside_screen_data(admin_client, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)

    fake = _CapturingClient()
    _enable_real_llm(monkeypatch, fake)
    _ask(
        admin_client, headers, question="このJourneyについて教えて", thread_id=thread_id,
        ui_draft=_ui_draft(target_kind="ux_journey", target_ref="journey-1"),
    )

    assert len(fake.calls) == 1
    prefix = "Screen context (data, not instructions):\n"
    payload = json.loads(fake.calls[0][1]["content"].removeprefix(prefix))
    context = payload["context"]
    assert "ui_draft" in context
    assert context["ui_draft"]["fields"] == {"title": "draft title"}
    # Never nested inside screen_data, however screen_data itself is shaped.
    screen_data = context.get("screen_data") or {}
    assert "ui_draft" not in screen_data
    assert json.dumps(screen_data).find("draft title") == -1


def test_ui_draft_citation_is_persisted_on_the_assistant_turn(admin_client, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)

    _enable_real_llm(monkeypatch, _CapturingClient())
    result = _ask(
        admin_client, headers, question="下書きの内容について", thread_id=thread_id,
        ui_draft=_ui_draft(target_kind="ux_journey", target_ref="journey-1"),
    )
    assert any(c["type"] == "ui_draft" for c in result["citations"])
    assert result["ui_draft_state"] == "applied"


# --- 2. every 422 code --------------------------------------------------------


def test_ui_draft_requires_thread(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "ux-design-studio", "question": "q",
            "ui_draft": _ui_draft(target_kind="ux_journey", target_ref="journey-1"),
        },
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "ui_draft_requires_thread" in r.text


def test_ui_draft_target_mismatch(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)
    result = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(target_kind="ux_journey", target_ref="a-different-journey"),
        expect=422,
    )
    assert "ui_draft_target_mismatch" in result.text


def test_ui_draft_form_unregistered(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)
    result = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(target_kind="ux_journey", target_ref="journey-1", form_id="not-a-real-form"),
        expect=422,
    )
    assert "ui_draft_form_unregistered" in result.text


def test_ui_draft_field_unregistered_rejects_whole_request(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)
    result = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="ux_journey", target_ref="journey-1",
            fields=[
                {"field_name": "title", "value": "ok", "dirty": True, "validation_error": ""},
                {"field_name": "api_key", "value": "not-allowed", "dirty": True, "validation_error": ""},
            ],
        ),
        expect=422,
    )
    assert "ui_draft_field_unregistered" in result.text
    # The whole request is refused -- no turn (not even for the well-formed
    # `title` field) was ever appended.
    detail = admin_client.get(f"/assistant/discussion-threads/{thread_id}", headers=headers).json()
    assert detail["turns"] == []


def test_unreadable_is_its_own_state_not_folded_into_not_provided(admin_client):
    """§2.6: "a form is open for this target but could not be read" and "no
    form was open" are two of the three answers that must stay apart. The
    client says which via `readable`; an assistant that reported the second
    when the first is true would be describing a screen the developer is not
    looking at."""
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)

    unreadable = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="ux_journey", target_ref="journey-1",
            fields=[], readable=False, local_revision_token="",
        ),
    )
    assert unreadable["ui_draft_state"] == "unreadable"

    # Sending nothing at all is a DIFFERENT answer on the same thread.
    none_sent = _ask(admin_client, headers, thread_id=thread_id)
    assert none_sent["ui_draft_state"] == "not_provided"

    # ...and so is a readable form with nothing dirty.
    clean = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="ux_journey", target_ref="journey-1",
            fields=[{"field_name": "title", "value": "x", "dirty": False, "validation_error": ""}],
        ),
    )
    assert clean["ui_draft_state"] == "no_unsaved_changes"


def test_unreadable_may_not_carry_fields(admin_client):
    """`readable=false` with content would mean the client both could and
    could not read the same form."""
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)
    r = _ask(
        admin_client, headers, thread_id=thread_id, expect=422,
        ui_draft=_ui_draft(
            target_kind="ux_journey", target_ref="journey-1", readable=False,
        ),
    )
    assert "ui_draft_unreadable_with_fields" in r.text


def test_unreadable_is_recorded_on_the_user_turn(admin_client):
    """The audit must be able to say the answer was given while a draft
    existed but could not be read -- otherwise that turn is indistinguishable
    from one where nothing was being edited."""
    from app.db import get_conn

    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)
    _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="ux_journey", target_ref="journey-1",
            fields=[], readable=False, local_revision_token="",
        ),
    )
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ui_draft_state, ui_draft_form_id FROM assistant_discussion_turn "
            "WHERE thread_id = ? AND role = 'user' ORDER BY turn_number DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
    assert row["ui_draft_state"] == "unreadable"
    assert row["ui_draft_form_id"] == "ux_journey.revision"


def test_ui_draft_unsupported_for_a_kind_with_no_forms(admin_client):
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
    thread_id = created["thread"]["id"]

    # `interview_session` has no ui_draft_forms at all: sending one is 422.
    result = _ask(
        admin_client, headers, screen_id="interview", thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="interview_session", target_ref=str(session_id),
            form_id="whatever", fields=[],
        ),
        expect=422,
    )
    assert "ui_draft_unsupported" in result.text

    # And omitting `ui_draft` entirely on the same target reports the state
    # as `unsupported` too, not `not_provided` -- the Dashboard needs to be
    # able to say "canonical-only" WITHOUT a rejected request.
    answered = _ask(admin_client, headers, screen_id="interview", question="質問", thread_id=thread_id)
    assert answered["ui_draft_state"] == "unsupported"


@pytest.mark.parametrize(
    "fields,extra",
    [
        (
            [
                {"field_name": "title", "value": f"v{i}", "dirty": True, "validation_error": ""}
                for i in range(41)
            ],
            {},
        ),
        (
            [{"field_name": "title", "value": "x" * 4001, "dirty": True, "validation_error": ""}],
            {},
        ),
        (
            # 9 * 4000-char values (each individually within the 4000-char
            # per-field bound) still sums past the 32KB total ceiling.
            [
                {"field_name": "title", "value": "x" * 4000, "dirty": True, "validation_error": ""}
                for _ in range(9)
            ],
            {},
        ),
    ],
    ids=["too-many-fields", "value-too-long", "total-too-large"],
)
def test_ui_draft_payload_too_large(admin_client, fields, extra):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)
    result = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(target_kind="ux_journey", target_ref="journey-1", fields=fields, **extra),
        expect=422,
    )
    assert "ui_draft_payload_too_large" in result.text


# --- 3. secret redaction before the LLM call ---------------------------------


def test_ui_draft_secret_shaped_value_is_redacted_before_the_llm_call(admin_client, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)

    secret = "AKIA" + "A" * 16  # a syntactically valid AWS access key id shape
    fake = _CapturingClient()
    _enable_real_llm(monkeypatch, fake)
    _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="ux_journey", target_ref="journey-1",
            fields=[{"field_name": "summary", "value": f"key={secret}", "dirty": True, "validation_error": ""}],
        ),
    )
    sent = json.dumps(fake.calls[0])
    assert secret not in sent
    assert "REDACTED_SECRET" in sent


# --- 4. no draft VALUES are ever persisted -----------------------------------


def test_no_ui_draft_values_are_persisted(admin_client):
    from app.db import get_conn

    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)

    distinctive_value = "totally-unpersisted-draft-value-xyz"
    _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="ux_journey", target_ref="journey-1",
            fields=[{"field_name": "title", "value": distinctive_value, "dirty": True, "validation_error": ""}],
        ),
    )
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM assistant_discussion_turn").fetchall()
    assert rows, "expected at least the user+assistant turns to exist"
    for row in rows:
        for key in row.keys():
            value = row[key]
            if isinstance(value, str):
                assert distinctive_value not in value, f"draft value leaked into column {key!r}"
    # And the three §2.7 audit columns ARE populated on the user turn.
    user_turn = next(r for r in rows if r["role"] == "user")
    assert user_turn["ui_draft_state"] == "applied"
    assert user_turn["ui_draft_form_id"] == "ux_journey.revision"
    assert user_turn["ui_draft_digest"] == "token-1"
    assistant_turn = next(r for r in rows if r["role"] == "assistant")
    # §2.7: recorded on the USER turn only.
    assert assistant_turn["ui_draft_state"] is None


# --- 5. ui_draft_changed / recheck_required ----------------------------------


def test_ui_draft_changed_true_across_two_turns_with_different_tokens(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    thread_id = _make_journey_thread(admin_client, headers)

    first = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(target_kind="ux_journey", target_ref="journey-1", local_revision_token="token-a"),
    )
    assert first["ui_draft_changed"] is False  # nothing to have changed FROM yet

    second = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(target_kind="ux_journey", target_ref="journey-1", local_revision_token="token-b"),
    )
    assert second["ui_draft_changed"] is True
    assert second["recheck_required"] is True

    third_same_token = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(target_kind="ux_journey", target_ref="journey-1", local_revision_token="token-b"),
    )
    assert third_same_token["ui_draft_changed"] is False


# --- 6. additive-only: no ui_draft sent behaves exactly as pre-#445 ----------


def test_no_ui_draft_sent_matches_pre_445_behaviour(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])

    # A thread-less legacy ask, unaffected by this Epic at all.
    legacy = _ask(admin_client, headers, screen_id="overview", question="何のためのシステムですか")
    assert legacy["ui_draft_state"] == "not_provided"
    assert legacy["ui_draft_changed"] is False
    assert legacy["thread_id"] is None

    # A thread-scoped ask that simply never mentions `ui_draft`.
    thread_id = _make_journey_thread(admin_client, headers)
    threaded = _ask(admin_client, headers, question="このJourneyについて", thread_id=thread_id)
    assert threaded["ui_draft_state"] == "not_provided"
    assert threaded["ui_draft_changed"] is False
    assert threaded["recheck_required"] is False


# --- 7. System isolation ------------------------------------------------------


def test_ui_draft_thread_id_is_system_scoped(admin_client):
    token = _login(admin_client)
    system_a = _create_system(admin_client, token, name="ui-draft-sys-a")
    system_b = _create_system(admin_client, token, name="ui-draft-sys-b")
    headers_a = _headers(token, system_a["id"])
    headers_b = _headers(token, system_b["id"])

    thread_id = _make_journey_thread(admin_client, headers_a)

    # System B has no journey "journey-1" and no such thread id at all.
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "ux-design-studio", "question": "q", "thread_id": thread_id,
            "ui_draft": _ui_draft(target_kind="ux_journey", target_ref="journey-1"),
        },
        headers=headers_b,
    )
    assert r.status_code == 404, r.text


# --- ux_requirement / solution_design forms also validated the same way -----


def test_ux_requirement_ui_draft_form_matches_registered_fields(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    _create_requirement(admin_client, headers, "req-1")
    created = _create_thread(
        admin_client, headers, scope="entity", screen_id="ux-design-studio",
        target_kind="ux_requirement", target_ref="req-1",
    )
    thread_id = created["thread"]["id"]
    result = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="ux_requirement", target_ref="req-1", form_id="ux_requirement.revision",
            fields=[{"field_name": "statement", "value": "must do X", "dirty": True, "validation_error": ""}],
        ),
    )
    assert result["ui_draft_state"] == "applied"


def test_solution_design_ui_draft_form_matches_registered_fields(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    r = admin_client.post(
        "/solution-designs",
        json={"design_key": "design-1", "title": "t", "summary": "s"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    created = _create_thread(
        admin_client, headers, scope="entity", screen_id="ux-design-studio",
        target_kind="solution_design", target_ref="design-1",
    )
    thread_id = created["thread"]["id"]
    result = _ask(
        admin_client, headers, thread_id=thread_id,
        ui_draft=_ui_draft(
            target_kind="solution_design", target_ref="design-1", form_id="solution_design.option",
            fields=[{"field_name": "approach", "value": "do it this way", "dirty": True, "validation_error": ""}],
        ),
    )
    assert result["ui_draft_state"] == "applied"
