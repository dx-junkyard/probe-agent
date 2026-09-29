"""Issue #467/#468/#472/#473: LLMError kinds, usage/finish, assistant config."""

from __future__ import annotations

import io
import json
import socket
import urllib.error

import pytest

from app import llm
from app.llm import (
    AnthropicClient,
    GeminiClient,
    LLMConfig,
    LLMError,
    OpenAIChatClient,
    _QuotaLLMClient,
)

SECRET = "sk-test-SECRET123"
MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def _cfg(provider="openai", model="gpt-4o-mini"):
    return LLMConfig(provider=provider, api_key="k", model=model, base_url=None, timeout=5)


class _Resp:
    def __init__(self, data):
        self._raw = json.dumps(data).encode() if not isinstance(data, bytes) else data

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(monkeypatch, *, result=None, error=None):
    def fake(request, timeout=None):
        if error is not None:
            raise error
        return _Resp(result)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake)


def _http_error(code):
    return urllib.error.HTTPError(
        "http://x", code, "err", {}, io.BytesIO(f'{{"error":"{SECRET}"}}'.encode())
    )


@pytest.mark.parametrize(
    "code,kind",
    [(401, "auth"), (403, "auth"), (429, "rate_limited"), (500, "provider_error"), (400, "provider_error")],
)
def test_http_status_kind(monkeypatch, code, kind):
    _patch_urlopen(monkeypatch, error=_http_error(code))
    with pytest.raises(LLMError) as ei:
        OpenAIChatClient(_cfg()).generate_text(MSGS)
    exc = ei.value
    assert exc.kind == kind and exc.http_status == code
    assert SECRET in str(exc)  # audit string unchanged
    assert str(exc) == f'LLM request failed: HTTP {code}: {{"error":"{SECRET}"}}'
    assert SECRET not in exc.safe_message
    assert exc.safe_message == f"LLM request failed: HTTP {code}"


@pytest.mark.parametrize(
    "error,kind",
    [
        (socket.timeout("timed out"), "timeout"),
        (TimeoutError("t"), "timeout"),
        (urllib.error.URLError(socket.timeout("timed out")), "timeout"),
        (urllib.error.URLError("dns failure"), "network"),
        (ConnectionResetError("reset"), "network"),
    ],
)
def test_transport_kind(monkeypatch, error, kind):
    _patch_urlopen(monkeypatch, error=error)
    with pytest.raises(LLMError) as ei:
        OpenAIChatClient(_cfg()).generate_text(MSGS)
    assert ei.value.kind == kind
    assert ei.value.http_status is None
    assert str(ei.value).startswith("LLM request failed: ")


def test_non_json_and_shape_are_malformed(monkeypatch):
    _patch_urlopen(monkeypatch, result=b"<html>" + SECRET.encode())
    with pytest.raises(LLMError) as ei:
        OpenAIChatClient(_cfg()).generate_text(MSGS)
    assert ei.value.kind == "malformed_response"
    assert SECRET not in ei.value.safe_message

    _patch_urlopen(monkeypatch, result={"unexpected": SECRET})
    with pytest.raises(LLMError) as ei:
        OpenAIChatClient(_cfg()).generate_text(MSGS)
    assert ei.value.kind == "malformed_response"
    assert SECRET not in ei.value.safe_message


def test_missing_key_is_config():
    cfg = LLMConfig("openai", None, "m", None, 5)
    for cls in (OpenAIChatClient, AnthropicClient, GeminiClient):
        with pytest.raises(LLMError) as ei:
            cls(cfg)
        assert ei.value.kind == "config"


def test_constructor_backward_compatible():
    exc = LLMError("plain")
    assert str(exc) == "plain"
    assert exc.kind == "provider_error" and exc.http_status is None
    assert LLMError("x", kind="bogus").kind == "provider_error"
    assert set(llm.LLM_ERROR_KINDS) == {
        "timeout", "network", "auth", "rate_limited",
        "provider_error", "malformed_response", "config",
    }


def test_openai_usage_and_finish(monkeypatch):
    _patch_urlopen(monkeypatch, result={
        "choices": [{"message": {"content": "hi"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 22},
    })
    c = OpenAIChatClient(_cfg())
    assert c.generate_text(MSGS) == "hi"
    assert c.last_usage == {"input_tokens": 11, "output_tokens": 22}
    assert c.last_finish_reason == "length"
    _patch_urlopen(monkeypatch, result={
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
    })
    c.generate_text(MSGS)
    assert c.last_usage is None and c.last_finish_reason == "stop"


def test_anthropic_usage_and_finish(monkeypatch):
    _patch_urlopen(monkeypatch, result={
        "content": [{"type": "text", "text": "yo"}],
        "usage": {"input_tokens": 3, "output_tokens": 4},
        "stop_reason": "max_tokens",
    })
    c = AnthropicClient(_cfg("anthropic", "claude-3-5-haiku-latest"))
    assert c.generate_text(MSGS) == "yo"
    assert c.last_usage == {"input_tokens": 3, "output_tokens": 4}
    assert c.last_finish_reason == "length"
    _patch_urlopen(monkeypatch, result={"content": [], "stop_reason": "refusal"})
    c.generate_text(MSGS)
    assert c.last_usage is None and c.last_finish_reason == "other"


def test_gemini_usage_and_finish(monkeypatch):
    _patch_urlopen(monkeypatch, result={
        "candidates": [{"content": {"parts": [{"text": "g"}]}, "finishReason": "MAX_TOKENS"}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 6},
    })
    c = GeminiClient(_cfg("gemini", "gemini-1.5-flash"))
    assert c.generate_text(MSGS) == "g"
    assert c.last_usage == {"input_tokens": 5, "output_tokens": 6}
    assert c.last_finish_reason == "length"
    _patch_urlopen(monkeypatch, result={"candidates": [{"finishReason": "STOP"}]})
    assert c.generate_text(MSGS) == ""
    assert c.last_finish_reason == "stop" and c.last_usage is None


def test_usage_reset_on_failure(monkeypatch):
    c = OpenAIChatClient(_cfg())
    c.last_usage = {"input_tokens": 1, "output_tokens": 1}
    c.last_finish_reason = "length"
    _patch_urlopen(monkeypatch, error=socket.timeout())
    with pytest.raises(LLMError):
        c.generate_text(MSGS)
    assert c.last_usage is None and c.last_finish_reason is None


def test_quota_wrapper_forwards_and_mock_defaults(monkeypatch):
    monkeypatch.setattr(llm, "_consume_current_system_quota", lambda: None)
    _patch_urlopen(monkeypatch, result={
        "choices": [{"message": {"content": "hi"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    })
    w = _QuotaLLMClient(OpenAIChatClient(_cfg()))
    assert w.last_usage is None
    w.generate_text(MSGS)
    assert w.last_usage == {"input_tokens": 1, "output_tokens": 2}
    assert w.last_finish_reason == "length"
    mock = llm.create_llm_client(LLMConfig("mock", None, "mock", None, 5))
    mock.generate_text(MSGS)
    assert mock.last_usage is None and mock.last_finish_reason == "stop"


_ENV = [
    "LLM_PROVIDER", "LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "LLM_TIMEOUT",
    "INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL", "INTELLIGENCE_LLM_TIMEOUT",
    "ASSISTANT_LLM_PROVIDER", "ASSISTANT_LLM_MODEL", "ASSISTANT_LLM_API_KEY",
    "ASSISTANT_LLM_BASE_URL", "ASSISTANT_LLM_TIMEOUT",
    "ASSISTANT_LLM_MAX_OUTPUT_TOKENS", "ASSISTANT_REQUEST_BUDGET_SECONDS",
]


@pytest.fixture
def clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_assistant_config_equals_intelligence_when_unset(clean_env):
    clean_env.setenv("LLM_PROVIDER", "anthropic")
    clean_env.setenv("ANTHROPIC_API_KEY", "ak")
    clean_env.setenv("LLM_BASE_URL", "http://b")
    clean_env.setenv("INTELLIGENCE_LLM_MODEL", "im")
    assert LLMConfig.assistant_from_env() == LLMConfig.intelligence_from_env()
    clean_env.setenv("ASSISTANT_LLM_MODEL", "  ")
    assert LLMConfig.assistant_from_env() == LLMConfig.intelligence_from_env()


def test_assistant_config_overrides(clean_env):
    clean_env.setenv("LLM_PROVIDER", "openai")
    clean_env.setenv("OPENAI_API_KEY", "ok")
    clean_env.setenv("ASSISTANT_LLM_MODEL", "am")
    clean_env.setenv("ASSISTANT_LLM_API_KEY", "aak")
    clean_env.setenv("ASSISTANT_LLM_BASE_URL", "http://a")
    clean_env.setenv("ASSISTANT_LLM_TIMEOUT", "7.5")
    cfg = LLMConfig.assistant_from_env()
    assert (cfg.provider, cfg.model, cfg.api_key, cfg.base_url, cfg.timeout) == (
        "openai", "am", "aak", "http://a", 7.5)
    clean_env.setenv("ASSISTANT_LLM_TIMEOUT", "bad")
    assert LLMConfig.assistant_from_env().timeout == LLMConfig.intelligence_from_env().timeout


def test_assistant_provider_switch_uses_provider_defaults(clean_env):
    clean_env.setenv("LLM_PROVIDER", "openai")
    clean_env.setenv("INTELLIGENCE_LLM_MODEL", "gpt-x")
    clean_env.setenv("OPENAI_API_KEY", "ok")
    clean_env.setenv("ANTHROPIC_API_KEY", "ak")
    clean_env.setenv("ASSISTANT_LLM_PROVIDER", "anthropic")
    cfg = LLMConfig.assistant_from_env()
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-3-5-haiku-latest"
    assert cfg.api_key == "ak"
    # Never reuse another provider's key.
    clean_env.delenv("ANTHROPIC_API_KEY")
    assert LLMConfig.assistant_from_env().api_key is None
    # The generic key belongs to the analysis provider; never sent elsewhere.
    clean_env.setenv("LLM_API_KEY", "generic")
    assert LLMConfig.assistant_from_env().api_key is None
    # Nor is the analysis provider's custom endpoint.
    clean_env.setenv("LLM_BASE_URL", "https://proxy.example/v1")
    assert LLMConfig.assistant_from_env().base_url is None
    clean_env.setenv("ASSISTANT_LLM_BASE_URL", "https://a.example")
    assert LLMConfig.assistant_from_env().base_url == "https://a.example"


def test_assistant_helpers(clean_env):
    assert llm.assistant_max_output_tokens() == 2048
    for raw, want in [("512", 512), ("0", 2048), ("-3", 2048), ("x", 2048), ("99999", 8192)]:
        clean_env.setenv("ASSISTANT_LLM_MAX_OUTPUT_TOKENS", raw)
        assert llm.assistant_max_output_tokens() == want
    assert llm.assistant_request_budget_seconds() is None
    for raw, want in [("30", 30.0), ("0", None), ("-1", None), ("x", None), ("nan", None)]:
        clean_env.setenv("ASSISTANT_REQUEST_BUDGET_SECONDS", raw)
        assert llm.assistant_request_budget_seconds() == want
