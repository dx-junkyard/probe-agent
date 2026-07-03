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
    return r.json()["access_token"]


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
    assert "documentation_indexed" in model_meta["related_pipeline_steps"]

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
        payload = json.loads(messages[-1]["content"])
        assert set(payload) == {"context", "question"}
        assert set(payload["context"]) == {
            "screen",
            "settings",
            "diagnostics",
            "pipeline_steps",
            "navigable_routes",
        }
        return json.dumps(
            {
                "answer": "Documentation indexing needs a reasoning model.",
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
