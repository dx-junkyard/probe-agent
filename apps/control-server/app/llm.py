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


Message = Dict[str, str]


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


class LLMClient(ABC):
    @abstractmethod
    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a text response from chat-style messages."""


class LLMError(RuntimeError):
    pass


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
            timeout=self.config.timeout,
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
            timeout=self.config.timeout,
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
            timeout=self.config.timeout,
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
    ) -> str:
        joined = "\n".join(m.get("content", "") for m in messages)
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


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    config = LLMConfig.from_env()
    return create_llm_client(config)


def create_llm_client(config: LLMConfig) -> LLMClient:
    if config.provider == "mock":
        return MockLLMClient()
    if config.provider == "anthropic":
        return AnthropicClient(config)
    if config.provider == "gemini":
        return GeminiClient(config)
    return OpenAIChatClient(config)
