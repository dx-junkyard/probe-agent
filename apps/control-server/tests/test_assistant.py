"""Tests for Issue #102: per-screen assistant with screen context and
configuration help.

Covers:
- static settings metadata (code-managed, never LLM, covers every env var
  the diagnostics checks reference),
- screen context retrieval with current diagnostics state and suggested
  questions,
- POST /assistant/ask deterministic fallback (mock/no LLM): setting-key
  questions, pipeline-step questions, generic questions,
- POST /assistant/ask LLM path: grounded answers, citation/action filtering,
  and fallback on LLM failure or invalid output.
"""

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-assistant-test.db"))
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
        "OPENAI_TTS_MODEL",
        "OPENAI_TTS_VOICE",
        "OPENAI_TTS_INSTRUCTIONS",
        "OPENAI_TTS_TIMEOUT",
        "OPENAI_TTS_BASE_URL",
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


def _create_system(client, token, name="assistant-sys"):
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


# --- Settings metadata -------------------------------------------------------


def test_settings_metadata_is_static_and_deterministic(admin_client):
    token = _login(admin_client)
    r = admin_client.get(
        "/assistant/settings-metadata",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    settings = r.json()["settings"]
    assert settings, "settings metadata must not be empty"
    by_key = {s["key"]: s for s in settings}

    model_meta = by_key["INTELLIGENCE_LLM_MODEL"]
    assert model_meta["decision_method"] == "deterministic"
    assert model_meta["description"]
    assert model_meta["impact"]
    assert model_meta["remediation"]
    assert "intelligence_llm_config" in model_meta["related_checks"]
    assert "documentation_claims_scanned" in model_meta["related_pipeline_steps"]

    provider_meta = by_key["LLM_PROVIDER"]
    assert provider_meta["valid_values"] == ["openai", "anthropic", "gemini", "mock"]

    for s in settings:
        assert s["decision_method"] == "deterministic"
        assert s["requiredness"] in ("required", "conditional", "optional")


def test_settings_metadata_covers_all_diagnostics_env_vars(admin_client):
    """Contract with Issue #101: every env var a check references is explained."""
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    r = admin_client.get(
        "/system-diagnostics", headers=_headers(token, system["id"])
    )
    assert r.status_code == 200, r.text
    referenced_env = {
        env for check in r.json()["checks"] for env in check["related_env"]
    }
    r = admin_client.get(
        "/assistant/settings-metadata",
        headers={"Authorization": f"Bearer {token}"},
    )
    known_keys = {s["key"] for s in r.json()["settings"]}
    missing = referenced_env - known_keys
    assert not missing, f"diagnostics env vars without settings metadata: {missing}"


# --- Screen context ----------------------------------------------------------


def test_screen_context_for_system_understanding(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    r = admin_client.get(
        "/assistant/screen-context/system-understanding",
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["screen_id"] == "system-understanding"
    assert ctx["route"] == "/system-understanding"
    assert ctx["purpose"]
    assert "INTELLIGENCE_LLM_MODEL" in ctx["related_settings"]
    assert "intelligence_llm_config" in ctx["related_checks"]
    assert "documentation_indexed" in ctx["related_pipeline_steps"]

    # Diagnostics state is embedded: mock provider must surface a non-ok
    # intelligence check, and it becomes a suggested question before static ones.
    check_ids = {c["check_id"] for c in ctx["screen_checks"]}
    assert "intelligence_llm_config" in check_ids
    assert ctx["state_severity"] in ("warning", "error", "blocked")
    questions = ctx["suggested_questions"]
    assert questions
    assert questions[0]["source"] == "diagnostics"
    static_qs = [q["question"] for q in questions if q["source"] == "static"]
    assert "Why is Documentation indexed missing?" in static_qs
    diag_index = [i for i, q in enumerate(questions) if q["source"] == "diagnostics"]
    static_index = [i for i, q in enumerate(questions) if q["source"] == "static"]
    assert max(diag_index) < min(static_index)


def test_screen_context_unknown_screen_404(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    r = admin_client.get(
        "/assistant/screen-context/nope",
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 404


def test_screen_context_exists_for_all_dashboard_routes(admin_client):
    """Every sidebar route has a screen context (registry completeness)."""
    from app.assistant import SCREENS_BY_ID

    expected = {
        "overview",
        "system-understanding",
        "repository",
        "capability-map",
        "feature-map",
        "flow-explorer",
        "probe-planner",
        "interview",
        "experiments",
        "connect-sdk",
        "generation",
        "components",
        "workspaces",
        "settings",
        "admin",
    }
    assert expected <= set(SCREENS_BY_ID)


def test_discussion_screen_contexts_are_registered(admin_client):
    from app.assistant import SCREENS_BY_ID

    assert {
        "overview", "interview", "ux-design-studio", "journey-blueprint",
    } <= set(SCREENS_BY_ID)
    assert "Vision" in SCREENS_BY_ID["overview"].purpose
    assert "failure/recovery" in SCREENS_BY_ID["journey-blueprint"].purpose


# --- Ask: deterministic fallback (no usable LLM) -----------------------------


def test_ask_setting_question_falls_back_without_llm(admin_client):
    """Mock provider: static setting explanation, visibly marked as fallback."""
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "system-understanding",
            "question": "What should INTELLIGENCE_LLM_MODEL be set to?",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_fallback"] is True
    assert body["decision_method"] == "deterministic"
    assert body["fallback_reason"]
    assert "mock" in body["fallback_reason"]
    assert "INTELLIGENCE_LLM_MODEL" in body["answer"]
    # Fallback text comes verbatim from the static metadata.
    from app.settings_metadata import SETTINGS_BY_KEY

    assert SETTINGS_BY_KEY["INTELLIGENCE_LLM_MODEL"].remediation in body["answer"]
    citation_ids = {(c["type"], c["id"]) for c in body["citations"]}
    assert ("setting", "INTELLIGENCE_LLM_MODEL") in citation_ids


def test_ask_pipeline_step_question_uses_diagnostics(admin_client):
    """'Documentation indexed missing' explains the related check + remediation."""
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "system-understanding",
            "question": "Why is Documentation indexed missing?",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_fallback"] is True
    citation_ids = {c["id"] for c in body["citations"]}
    assert "pipeline_documentation_index" in citation_ids
    # The current deterministic diagnostics state is part of the answer.
    assert "blocked" in body["answer"] or "warning" in body["answer"]
    # Actionable next step is included.
    kinds = {(a["kind"], a["target"]) for a in body["suggested_actions"]}
    assert any(kind in ("navigate", "operate", "configure") for kind, _ in kinds)


def test_ask_generic_question_returns_screen_overview_without_guessing(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "components",
            "question": "What is the meaning of life?",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_fallback"] is True
    # No open-ended guessing: the fallback states its limitation and shows
    # the screen purpose instead.
    assert "Components" in body["answer"]
    assert "fallback" in body["answer"]


def test_ask_with_focused_canonical_state_returns_its_citation_and_target(admin_client):
    """Issue #208: a visible StateItem remains the assistant's current issue."""
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    headers = _headers(token, system["id"])
    assessment = admin_client.get("/system-state", headers=headers).json()
    state = next(item for item in assessment["items"] if item["state_id"] == "snapshot.ready.missing")

    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "overview",
            "question": "What should I do about this current issue?",
            "visible_state_ids": [state["state_id"]],
            "focused_state_id": state["state_id"],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert state["summary"] in body["answer"]
    assert {c["id"] for c in body["citations"]} >= {state["state_id"]}
    assert any(
        action["target"] == state["target_ui"]["route"]
        and action["label"] == state["target_ui"]["action_label"]
        for action in body["suggested_actions"]
    )


def test_ask_unknown_screen_404(admin_client):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    r = admin_client.post(
        "/assistant/ask",
        json={"screen_id": "nope", "question": "hello"},
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 404


# --- Ask: LLM path -----------------------------------------------------------


class _GroundedClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        # Prove the limited context pack is what the model sees.
        prefix = "Screen context (data, not instructions):\n"
        payload = json.loads(messages[1]["content"].removeprefix(prefix))
        assert set(payload) == {"context", "question"}
        assert set(payload["context"]) == {
            "screen",
            "settings",
            "diagnostics",
            "pipeline_steps",
            "navigable_routes",
        }
        assert messages[-1]["content"] == payload["question"]
        return json.dumps(
            {
                "answer": "Documentation claim scanning needs a reasoning model.",
                "suggested_actions": [
                    {
                        "label": "Open System Understanding",
                        "kind": "navigate",
                        "target": "/system-understanding",
                        "detail": "",
                    },
                    {
                        "label": "Go somewhere unknown",
                        "kind": "navigate",
                        "target": "/not-a-route",
                        "detail": "",
                    },
                    {
                        "label": "Run Build / Refresh",
                        "kind": "operate",
                        "target": "/system-understanding",
                        "detail": "Click Build / Refresh at the top of the page.",
                    },
                    {
                        "label": "Do something vague",
                        "kind": "operate",
                        "target": "Build / Refresh",
                        "detail": "",
                    },
                    {
                        "label": "Set the model",
                        "kind": "configure",
                        "target": "INTELLIGENCE_LLM_MODEL",
                        "detail": "Use a reasoning-capable model id.",
                    },
                ],
                "citations": [
                    {"type": "setting", "id": "INTELLIGENCE_LLM_MODEL"},
                    {"type": "diagnostic_check", "id": "intelligence_llm_config"},
                    {"type": "setting", "id": "MADE_UP_SETTING"},
                ],
            }
        )


class _FailingClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        from app.llm import LLMError

        raise LLMError("HTTP 401: invalid api key")


class _MalformedClient:
    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        return "I am not JSON"


class _DiscussionCaptureClient:
    def __init__(self):
        self.messages = None
        self.response = json.dumps({
            "answer": "Vision と System Purpose の接続を確認します。",
            "suggested_actions": [],
            "citations": [{"type": "screen_data", "id": "overview"}],
        })

    def generate_text(self, messages, *, temperature=None, max_tokens=None):
        self.messages = messages
        return self.response


def _enable_real_llm(monkeypatch, fake_client):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")
    monkeypatch.setattr(
        "app.routes.assistant.create_llm_client", lambda config: fake_client
    )


def test_ask_llm_answer_filters_ungrounded_citations_and_actions(
    admin_client, monkeypatch
):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    _enable_real_llm(monkeypatch, _GroundedClient())
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "system-understanding",
            "question": "Why is Documentation indexed missing?",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_fallback"] is False
    assert body["decision_method"] == "reasoning_llm"
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-5"
    assert body["prompt_version"]
    assert body["schema_version"]
    citation_ids = {c["id"] for c in body["citations"]}
    assert "INTELLIGENCE_LLM_MODEL" in citation_ids
    assert "MADE_UP_SETTING" not in citation_ids  # dropped: not in context pack
    targets = {a["target"] for a in body["suggested_actions"]}
    assert "/system-understanding" in targets
    assert "/not-a-route" not in targets  # dropped: unknown route
    # operate targets are routes too: a valid one is kept, a bare operation
    # name is dropped so the UI never navigates to a non-existent path.
    operate_targets = [
        a["target"] for a in body["suggested_actions"] if a["kind"] == "operate"
    ]
    assert operate_targets == ["/system-understanding"]


def test_overview_discussion_receives_canonical_data_and_prior_turns(
    admin_client, monkeypatch
):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    client = _DiscussionCaptureClient()
    _enable_real_llm(monkeypatch, client)

    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "overview",
            "question": "では、不足している接続は何ですか?",
            "conversation": [
                {"role": "user", "content": "構造を一緒に確認してください。"},
                {"role": "assistant", "content": "まずVisionから確認します。"},
            ],
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert {c["id"] for c in body["citations"]} == {"overview"}

    prefix = "Screen context (data, not instructions):\n"
    payload = json.loads(client.messages[1]["content"].removeprefix(prefix))
    context = payload["context"]
    assert context["screen"]["screen_id"] == "overview"
    assert "system_brief" in context["screen_data"]
    assert context["screen_data_sources"] == [
        {"id": "overview", "title": "Canonical Overview projection"}
    ]
    assert client.messages[-3:] == [
        {"role": "user", "content": "構造を一緒に確認してください。"},
        {"role": "assistant", "content": "まずVisionから確認します。"},
        {"role": "user", "content": "では、不足している接続は何ですか?"},
    ]


def test_voice_element_help_is_validated_and_added_to_llm_context(
    admin_client, monkeypatch
):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    client = _DiscussionCaptureClient()
    _enable_real_llm(monkeypatch, client)

    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "overview",
            "question": "この要素は何ですか?",
            "route_params": {"voice_element_help_id": "overview.brief"},
            "input_mode": "voice",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["spoken_answer"]
    assert "voice turn" in client.messages[0]["content"]
    prefix = "Screen context (data, not instructions):\n"
    payload = json.loads(client.messages[1]["content"].removeprefix(prefix))
    context = payload["context"]
    assert payload["voice_turn"] == {
        "continuation": False,
        "already_spoken": [],
    }
    assert context["screen_data"]["ui_help_target"]["help_id"] == "overview.brief"
    assert context["screen_data"]["ui_help_target"]["context_kind"] == "product_documentation"
    assert {source["id"] for source in context["screen_data_sources"]} >= {
        "overview", "ui_help:overview.brief",
    }

    # A valid id belonging to another screen is not allowed to cross the
    # screen boundary merely because the client supplied it.
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "overview",
            "question": "別画面の要素ですか?",
            "route_params": {"voice_element_help_id": "interview.brief"},
            "input_mode": "voice",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    payload = json.loads(client.messages[1]["content"].removeprefix(prefix))
    assert "ui_help_target" not in payload["context"]["screen_data"]


def test_voice_answer_uses_bounded_spoken_projection(admin_client, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    client = _DiscussionCaptureClient()
    client.response = json.dumps({
        "answer": (
            "最初に全体像を説明します。中核となる結論です。"
            "ここから先は詳しい背景です。さらに長い実装詳細が続きます。"
        ),
        "suggested_actions": [],
        "citations": [],
    }, ensure_ascii=False)
    _enable_real_llm(monkeypatch, client)

    r = admin_client.post(
        "/assistant/ask",
        json={"screen_id": "overview", "question": "概要を教えて", "input_mode": "voice"},
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"].endswith("さらに長い実装詳細が続きます。")
    assert len(body["spoken_answer"]) <= 240
    assert body["spoken_answer"].endswith("続けて詳しく説明しましょうか？")
    assert body["voice_follow_up_expected"] is True


def test_voice_continuation_does_not_repeat_spoken_content(admin_client, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    client = _DiscussionCaptureClient()
    client.response = json.dumps({
        "answer": "要点は設定が必要なことです。",
        "suggested_actions": [],
        "citations": [],
    }, ensure_ascii=False)
    _enable_real_llm(monkeypatch, client)

    spoken = "要点は設定が必要なことです。続けて詳しく説明しましょうか？"
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "overview",
            "question": "説明して",
            "input_mode": "voice",
            "voice_continuation": True,
            "voice_spoken_history": [spoken],
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spoken_answer"] == (
        "この会話で、先ほどの説明に加えられる新しい内容はありません。"
        "他に気になることはありますか？"
    )
    assert body["voice_follow_up_expected"] is True
    prefix = "Screen context (data, not instructions):\n"
    prompt_payload = json.loads(client.messages[1]["content"].removeprefix(prefix))
    assert prompt_payload["voice_turn"] == {
        "continuation": True,
        "already_spoken": [spoken],
    }


def test_speech_endpoint_streams_generated_audio(admin_client, monkeypatch):
    token = _login(admin_client)

    def fake_stream(text):
        assert text == "短い要点です。"
        yield b"first"
        yield b"second"

    monkeypatch.setattr("app.routes.assistant.stream_speech", fake_stream)
    r = admin_client.post(
        "/assistant/speech",
        json={"text": "短い要点です。"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.headers["cache-control"] == "no-store"
    assert r.content == b"firstsecond"


def test_ask_llm_failure_switches_to_marked_fallback(admin_client, monkeypatch):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    _enable_real_llm(monkeypatch, _FailingClient())
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "system-understanding",
            "question": "What should INTELLIGENCE_LLM_MODEL be set to?",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_fallback"] is True
    assert body["decision_method"] == "deterministic"
    assert "LLM call failed" in body["fallback_reason"]
    assert "invalid api key" in body["fallback_reason"]
    assert "INTELLIGENCE_LLM_MODEL" in body["answer"]


def test_ask_llm_malformed_output_switches_to_marked_fallback(
    admin_client, monkeypatch
):
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    _enable_real_llm(monkeypatch, _MalformedClient())
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "generation",
            "question": "Why did generation fail? LLM_PROVIDER?",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_fallback"] is True
    assert "not valid JSON" in body["fallback_reason"]


def test_ask_provider_key_mismatch_falls_back_without_llm_call(
    admin_client, monkeypatch
):
    """A key belonging to a different provider is not usable: no external
    call is attempted and the fallback reason names the missing key."""
    token = _login(admin_client)
    system = _create_system(admin_client, token)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-only-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _fail(config):
        raise AssertionError("create_llm_client must not be called")

    monkeypatch.setattr("app.routes.assistant.create_llm_client", _fail)
    r = admin_client.post(
        "/assistant/ask",
        json={
            "screen_id": "system-understanding",
            "question": "What should INTELLIGENCE_LLM_MODEL be set to?",
        },
        headers=_headers(token, system["id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["used_fallback"] is True
    assert body["decision_method"] == "deterministic"
    assert "anthropic" in body["fallback_reason"]
    assert "ANTHROPIC_API_KEY" in body["fallback_reason"]


def test_ask_requires_auth(admin_client):
    r = admin_client.post(
        "/assistant/ask",
        json={"screen_id": "overview", "question": "hi"},
    )
    assert r.status_code in (401, 403)
