"""Provider-neutral LLM adapter layer.

Application code calls only the small interface in this module. Provider
differences such as request shape, response extraction, and reasoning-model
parameter handling stay here.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional

from .llm_secret_redaction import redact_messages


Message = Dict[str, str]

# Provider-specific API key env vars (also used by system_diagnostics).
PROVIDER_KEY_ENV: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Finite set of recognized `LLM_PROVIDER` values (also used by
# system_diagnostics and bootstrap_status). "mock" is a real, supported
# value (deterministic test/local-smoke output), not an error state.
KNOWN_PROVIDERS = frozenset(PROVIDER_KEY_ENV) | {"mock"}


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    api_key: Optional[str]
    model: str
    base_url: Optional[str]
    timeout: float

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
        api_key = (
            os.getenv("LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("GEMINI_API_KEY")
        )
        default_model = {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-latest",
            "gemini": "gemini-1.5-flash",
            "mock": "mock",
        }.get(provider, "gpt-4o-mini")
        try:
            timeout = float(os.getenv("LLM_TIMEOUT", "120"))
        except ValueError:
            timeout = 120.0
        return cls(
            provider=provider,
            api_key=api_key,
            model=os.getenv("LLM_MODEL", default_model),
            base_url=os.getenv("LLM_BASE_URL") or None,
            timeout=timeout,
        )

    @classmethod
    def intelligence_from_env(cls) -> "LLMConfig":
        """Config preferring INTELLIGENCE_LLM_* over the generic LLM_* vars.

        Unlike ``from_env``, the API key must match the effective provider
        (generic LLM_API_KEY or that provider's specific key); a key that
        belongs to a different provider is not usable and yields
        ``api_key=None`` so callers fail closed instead of making a doomed
        external call.
        """
        base = cls.from_env()
        provider = (
            os.getenv("INTELLIGENCE_LLM_PROVIDER") or base.provider
        ).strip().lower()
        model = (os.getenv("INTELLIGENCE_LLM_MODEL") or "").strip()
        if not model:
            model = os.getenv("LLM_MODEL") or {
                "openai": "gpt-4o-mini",
                "anthropic": "claude-3-5-haiku-latest",
                "gemini": "gemini-1.5-flash",
                "mock": "mock",
            }.get(provider, "gpt-4o-mini")
        specific_env = PROVIDER_KEY_ENV.get(provider)
        api_key = (
            (os.getenv("LLM_API_KEY") or "").strip()
            or ((os.getenv(specific_env) or "").strip() if specific_env else "")
        ) or None
        timeout = base.timeout
        raw_timeout = os.getenv("INTELLIGENCE_LLM_TIMEOUT")
        if raw_timeout and raw_timeout.strip():
            try:
                timeout = float(raw_timeout)
            except ValueError:
                pass
        return cls(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base.base_url,
            timeout=timeout,
        )


class LLMClient(ABC):
    @abstractmethod
    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """Generate a text response from chat-style messages.

        ``timeout`` overrides the provider's configured socket timeout for
        THIS call only (Issue #339). An iterative loop with an overall time
        budget needs the individual call to be interruptible: checking the
        clock between rounds bounds the loop's own bookkeeping, not the round
        trip that actually consumes the time, so a single hung call could
        overrun the whole budget while every between-round check passed.
        """


class LLMError(RuntimeError):
    pass


def _effective_timeout(config: LLMConfig, override: Optional[float]) -> float:
    """The socket timeout for one call: the override when it is usable.

    A non-positive override means the caller has no time left, which is a bug
    on their side (they should not call at all) -- falling back to the full
    configured timeout would silently turn a spent budget into a fresh one, so
    the smallest positive value is used instead and the call fails fast.
    """
    if override is None:
        return config.timeout
    return max(0.001, min(float(override), config.timeout))


class LLMResourceLimitError(RuntimeError):
    code = "llm_resource_limit_error"


class LLMQuotaExceeded(LLMResourceLimitError):
    """The current System exhausted its durable daily LLM allowance."""

    code = "llm_daily_limit_exceeded"


class LLMSystemContextMissing(LLMResourceLimitError):
    code = "llm_system_context_required"


def is_reasoning_model(provider: str, model: str) -> bool:
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip().lower()
    patterns = {
        "openai": r"^(o1|o3|o4|gpt-5)",
        "anthropic": r"^(claude-(3-7|4)|claude-(opus|sonnet)-4)",
        "gemini": r"^gemini-(2\.5|3)",
    }
    pattern = patterns.get(normalized_provider)
    return bool(pattern and re.match(pattern, normalized_model))


def _adapt_openai_messages(messages: List[Message], model: str) -> List[Message]:
    if not is_reasoning_model("openai", model):
        return messages
    return [
        {"role": "developer", "content": msg["content"]}
        if msg.get("role") == "system"
        else msg
        for msg in messages
    ]


def _request_json(
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Dict[str, str],
    timeout: float,
) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"LLM request failed: HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM response was not JSON: {raw[:500]}") from exc


class OpenAIChatClient(LLMClient):
    def __init__(self, config: LLMConfig):
        if not config.api_key:
            raise LLMError("LLM_API_KEY or OPENAI_API_KEY is required for OpenAI")
        self.config = config

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": _adapt_openai_messages(messages, self.config.model),
        }
        if is_reasoning_model("openai", self.config.model):
            if max_tokens is not None:
                payload["max_completion_tokens"] = max_tokens
        else:
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
        response = _request_json(
            (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
            + "/chat/completions",
            payload,
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            timeout=_effective_timeout(self.config, timeout),
        )
        try:
            return response["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenAI response: {response}") from exc


class AnthropicClient(LLMClient):
    def __init__(self, config: LLMConfig):
        if not config.api_key:
            raise LLMError("LLM_API_KEY or ANTHROPIC_API_KEY is required for Anthropic")
        self.config = config

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": non_system,
            "max_tokens": max_tokens or 2048,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if temperature is not None:
            payload["temperature"] = temperature
        response = _request_json(
            (self.config.base_url or "https://api.anthropic.com").rstrip("/")
            + "/v1/messages",
            payload,
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=_effective_timeout(self.config, timeout),
        )
        try:
            parts = response.get("content") or []
            return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        except AttributeError as exc:
            raise LLMError(f"Unexpected Anthropic response: {response}") from exc


class GeminiClient(LLMClient):
    def __init__(self, config: LLMConfig):
        if not config.api_key:
            raise LLMError("LLM_API_KEY or GEMINI_API_KEY is required for Gemini")
        self.config = config

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        system_instructions = [
            {"parts": [{"text": m["content"]}]}
            for m in messages
            if m.get("role") == "system"
        ]
        
        contents = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue
            
            api_role = "model" if role in ("assistant", "developer") else "user"
            contents.append({"role": api_role, "parts": [{"text": m.get("content", "")}]})

        generation_config: Dict[str, Any] = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens
            
        payload: Dict[str, Any] = {"contents": contents}
        if generation_config:
            payload["generationConfig"] = generation_config
        if system_instructions:
            payload["systemInstruction"] = system_instructions[0]

        base = self.config.base_url or "https://generativelanguage.googleapis.com/v1beta"
        response = _request_json(
            f"{base.rstrip('/')}/models/{self.config.model}:generateContent?key={self.config.api_key}",
            payload,
            headers={},
            timeout=_effective_timeout(self.config, timeout),
        )
        try:
            # When finishReason is MAX_TOKENS, content can be missing or empty.
            candidate = response.get("candidates", [{}])[0]
            content = candidate.get("content", {})
            if not content:
                return ""
            parts = content.get("parts", [])
            return "".join(part.get("text", "") for part in parts)
        except (IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Gemini response format: {response}") from exc


class MockLLMClient(LLMClient):
    """Deterministic provider used by tests and local UI smoke checks."""

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        joined = "\n".join(m.get("content", "") for m in messages)
        if "CANDIDATE_STUDIO_PROPOSAL_JSON" in joined:
            return json.dumps(
                {
                    "summary": "Mock candidate: uppercase the first argument.",
                    "assumptions": [
                        "No external LLM was called; this is deterministic mock output."
                    ],
                    "changed_symbols": ["candidate"],
                    "generated_code": (
                        "def candidate(*args, **kwargs):\n"
                        "    text = args[0] if args else ''\n"
                        "    return str(text).upper()\n"
                    ),
                    "risks": ["Mock proposal — review captured inputs before adoption."],
                    "suggested_tests": [
                        "Assert candidate('a') == 'A' against recorded traces."
                    ],
                }
            )
        if "CELL_TRIAGE_RESPONSE_JSON" in joined:
            return json.dumps(
                {
                    "classification": "individual",
                    "reasoning_summary": (
                        "Mock triage: no external LLM was called; this is "
                        "deterministic mock output."
                    ),
                    "affected_cell_ids": [],
                    "proposed_ask": (
                        "Mock proposed ask -- review the digest facts manually "
                        "before acting."
                    ),
                }
            )
        if "CELL_IMPROVEMENT_RESPONSE_JSON" in joined:
            return json.dumps(
                {
                    "hypothesis": (
                        "Mock hypothesis: no external LLM was called; this is "
                        "deterministic mock output grounded only in the "
                        "observed facts refs supplied."
                    ),
                    "expected_effect": (
                        "Mock expected effect: improved outcome on the "
                        "sampled failure pattern."
                    ),
                    "risk": "Mock risk: review canary evidence before adoption.",
                    "rollback_plan": (
                        "Mock rollback plan: revert to the previously pinned "
                        "Role Card version / patch."
                    ),
                }
            )
        if "CELL_QUALITY_AUDIT_RESPONSE_JSON" in joined:
            return json.dumps(
                {
                    "explanation": (
                        "Mock quality-audit explanation: no external LLM was "
                        "called; this is deterministic mock output describing "
                        "the failed criteria pattern."
                    )
                }
            )
        if "REGRESSION_SCAFFOLD_RESPONSE_JSON" in joined:
            return json.dumps(
                {
                    "scaffold": (
                        "def test_replay_regression():\n"
                        "    # Mock reasoning output; review captured inputs.\n"
                        "    assert True\n"
                    )
                }
            )
        if "EVALUATION_RESPONSE_JSON" in joined:
            return json.dumps(
                {
                    "verdict": "better",
                    "reason": "Mock evaluation: candidate output is acceptable.",
                    "risks": "No external LLM was called.",
                    "recommendation": "Review with real traces before adoption.",
                }
            )
        return json.dumps(
            {
                "generated_code": (
                    "def candidate(*args, **kwargs):\n"
                    "    text = args[0] if args else ''\n"
                    "    return str(text).upper()\n"
                ),
                "notes": "Mock candidate uppercases the first positional argument.",
            }
        )


class _QuotaLLMClient(LLMClient):
    """Consume the current request/job System quota immediately before a call."""

    def __init__(self, delegate: LLMClient):
        self._delegate = delegate

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        _consume_current_system_quota()
        return self._delegate.generate_text(
            redact_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


class _QuotaMockLLMClient(MockLLMClient):
    """Mock-preserving wrapper so existing isinstance audit logic stays valid."""

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        _consume_current_system_quota()
        return super().generate_text(
            redact_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )


def _consume_current_system_quota() -> None:
    from .resource_limits import (
        ResourceLimitExceeded,
        consume_llm_execution,
        current_system_id,
    )

    system_id = current_system_id()
    if system_id is None:
        raise LLMSystemContextMissing(
            "A System quota context is required before every LLM execution"
        )
    try:
        consume_llm_execution(system_id)
    except ResourceLimitExceeded as exc:
        raise LLMQuotaExceeded(str(exc)) from exc


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    config = LLMConfig.from_env()
    return create_llm_client(config)


def create_llm_client(config: LLMConfig) -> LLMClient:
    if config.provider == "mock":
        return _QuotaMockLLMClient()
    if config.provider == "anthropic":
        return _QuotaLLMClient(AnthropicClient(config))
    if config.provider == "gemini":
        return _QuotaLLMClient(GeminiClient(config))
    return _QuotaLLMClient(OpenAIChatClient(config))
