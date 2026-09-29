"""Issue #473 (Epic #467): assistant LLM config, output cap, request budget.

Contract: docs/01-specifications/capabilities/assistant-answer-quality.md §6.
Defaults (no ASSISTANT_* env) must behave exactly as before.
"""
from __future__ import annotations

import json

import pytest

from app.llm import LLMConfig
from tests.test_assistant_failure_response import (  # noqa: F401  (fixtures + helpers)
    DESIGN_QUESTION,
    SETTING_QUESTION,
    _ask,
    _metric_rows,
    env,
)
from tests.test_assistant_context_selection import admin_client  # noqa: F401

ASSISTANT_ENV = (
    "ASSISTANT_LLM_PROVIDER", "ASSISTANT_LLM_MODEL", "ASSISTANT_LLM_API_KEY",
    "ASSISTANT_LLM_BASE_URL", "ASSISTANT_LLM_TIMEOUT",
    "ASSISTANT_LLM_MAX_OUTPUT_TOKENS", "ASSISTANT_REQUEST_BUDGET_SECONDS",
)
GOOD = json.dumps({"conclusion": "確認してください", "points": [], "missing_information": [],
                   "suggested_actions": [], "citations": []}, ensure_ascii=False)


class _Recorder:
    def __init__(self):
        self.calls = []
        self.last_usage = None
        self.last_finish_reason = "stop"

    def generate_text(self, messages, *, temperature=None, max_tokens=None, timeout=None):
        self.calls.append({"max_tokens": max_tokens, "timeout": timeout})
        return GOOD


@pytest.fixture
def rec(monkeypatch):
    for name in ASSISTANT_ENV:
        monkeypatch.delenv(name, raising=False)
    fake = _Recorder()
    configs = []
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("INTELLIGENCE_LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "unused")

    def factory(config):
        configs.append(config)
        return fake

    monkeypatch.setattr("app.routes.assistant.create_llm_client", factory)
    fake.configs = configs
    return fake


def test_default_env_is_unchanged(env, rec):
    client, _, system_id, headers = env
    assert LLMConfig.assistant_from_env() == LLMConfig.intelligence_from_env()
    body = _ask(client, headers)
    assert rec.calls == [{"max_tokens": 2048, "timeout": None}]
    assert rec.configs[-1] == LLMConfig.intelligence_from_env()
    assert body["model"] == "gpt-5"
    usage = _metric_rows(system_id)[-1]["usage_json"]
    u = json.loads(usage)
    assert u["llm_config_source"] == "intelligence_default"
    assert u["max_output_tokens"] == 2048
    assert "request_budget_seconds" not in u


def test_assistant_model_override_only_affects_ask(env, rec, monkeypatch):
    client, _, system_id, headers = env
    monkeypatch.setenv("ASSISTANT_LLM_MODEL", "gpt-5-mini")
    body = _ask(client, headers)
    assert rec.configs[-1].model == "gpt-5-mini"
    assert body["model"] == "gpt-5-mini"
    u = json.loads(_metric_rows(system_id)[-1]["usage_json"])
    assert u["llm_config_source"] == "assistant_override"

    # A non-ask feature (discussion proposal generation) keeps the analysis model.
    r = client.post(
        "/assistant/discussion-threads",
        json={"scope": "screen", "screen_id": "overview", "target_kind": "screen", "target_ref": "overview"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    before = len(rec.configs)
    client.post(f"/assistant/discussion-threads/{r.json()['thread']['id']}/proposals", headers=headers)
    assert len(rec.configs) > before
    assert all(c.model == "gpt-5" for c in rec.configs[before:])


def test_max_output_tokens_override(env, rec, monkeypatch):
    client, _, system_id, headers = env
    monkeypatch.setenv("ASSISTANT_LLM_MAX_OUTPUT_TOKENS", "512")
    _ask(client, headers)
    assert rec.calls[-1]["max_tokens"] == 512
    assert json.loads(_metric_rows(system_id)[-1]["usage_json"])["max_output_tokens"] == 512


def test_ample_budget_passes_bounded_timeout(env, rec, monkeypatch):
    client, _, system_id, headers = env
    monkeypatch.setenv("ASSISTANT_REQUEST_BUDGET_SECONDS", "60")
    _ask(client, headers)
    timeout = rec.calls[-1]["timeout"]
    assert timeout is not None and 0 < timeout <= 60
    u = json.loads(_metric_rows(system_id)[-1]["usage_json"])
    assert u["request_budget_seconds"] == 60.0
    assert 0 < u["budget_remaining_seconds"] <= 60


@pytest.fixture
def exhausted(monkeypatch):
    """Every ask starts with its budget already spent."""
    monkeypatch.setenv("ASSISTANT_REQUEST_BUDGET_SECONDS", "5")
    monkeypatch.setattr(
        "app.assistant_metrics.AskTrace.elapsed_seconds", lambda self: 4.5
    )


def test_exhausted_budget_skips_llm_unmatched_question(env, rec, exhausted):
    client, _, system_id, headers = env
    body = _ask(client, headers, DESIGN_QUESTION)
    assert rec.calls == []
    assert body["answer_status"] == "failed"
    assert body["failure"]["failure_class"] == "budget_exceeded"
    row = _metric_rows(system_id)[-1]
    assert row["failure_class"] == "budget_exceeded"
    assert json.loads(row["counters_json"])["llm_calls"] == 0


def test_exhausted_budget_keeps_deterministic_answer_for_matched_question(env, rec, exhausted):
    client, _, system_id, headers = env
    body = _ask(client, headers, SETTING_QUESTION)
    assert rec.calls == []
    assert body["answer_status"] == "deterministic_answer"
    row = _metric_rows(system_id)[-1]
    assert row["failure_class"] == "budget_exceeded"
    assert json.loads(row["counters_json"])["llm_calls"] == 0


def test_metric_holds_no_secret_or_url(env, rec, monkeypatch):
    client, _, system_id, headers = env
    monkeypatch.setenv("ASSISTANT_LLM_MODEL", "gpt-5-mini")
    monkeypatch.setenv("ASSISTANT_LLM_API_KEY", "sk-assistant-SECRET")
    monkeypatch.setenv("ASSISTANT_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ASSISTANT_REQUEST_BUDGET_SECONDS", "30")
    _ask(client, headers)
    row = _metric_rows(system_id)[-1]
    dump = json.dumps({k: row[k] for k in row.keys() if k.endswith("_json")})
    assert "sk-assistant-SECRET" not in dump
    assert "example.invalid" not in dump
    assert "http" not in dump
