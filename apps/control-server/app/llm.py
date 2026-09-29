"""Provider-neutral LLM adapter layer.

Application code calls only the small interface in this module. Provider
differences such as request shape, response extraction, and reasoning-model
parameter handling stay here.
"""

from __future__ import annotations

import json
import os
import re
import socket
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

# Default model per provider (unknown providers fall back to the OpenAI one).
DEFAULT_MODELS: Dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "gemini": "gemini-1.5-flash",
    "mock": "mock",
}
_FALLBACK_MODEL = "gpt-4o-mini"

# Assistant output/time knobs (Issue #467 / #468).
ASSISTANT_DEFAULT_MAX_OUTPUT_TOKENS = 2048
ASSISTANT_MAX_OUTPUT_TOKENS_CEILING = 8192


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
        default_model = DEFAULT_MODELS.get(provider, _FALLBACK_MODEL)
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
            model = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(
                provider, _FALLBACK_MODEL
            )
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

    @classmethod
    def assistant_from_env(cls) -> "LLMConfig":
        """Config for the Dashboard AI Assistant (Issue #467).

        Starts from ``intelligence_from_env`` and overrides each field only
        when its ``ASSISTANT_LLM_*`` variable is set and non-blank, so with no
        assistant variables the result equals ``intelligence_from_env()``.

        When ``ASSISTANT_LLM_PROVIDER`` changes the provider:
        - the model defaults to that provider's default model (NOT the
          intelligence model, which belongs to another provider) unless
          ``ASSISTANT_LLM_MODEL`` is set;
        - the API key is ``ASSISTANT_LLM_API_KEY``, else that provider's
          specific key env. The generic ``LLM_API_KEY`` is NOT used: it was
          configured for the analysis provider, and sending it to another
          provider's host would leak a credential (fail closed with None);
        - the base URL is ``ASSISTANT_LLM_BASE_URL`` or the provider default,
          never the analysis provider's custom endpoint.
        """
        base = cls.intelligence_from_env()

        def _env(name: str) -> str:
            return (os.getenv(name) or "").strip()

        provider = base.provider
        provider_changed = False
        raw_provider = _env("ASSISTANT_LLM_PROVIDER").lower()
        if raw_provider:
            provider_changed = raw_provider != base.provider
            provider = raw_provider

        model = base.model
        raw_model = _env("ASSISTANT_LLM_MODEL")
        if raw_model:
            model = raw_model
        elif provider_changed:
            model = DEFAULT_MODELS.get(provider, _FALLBACK_MODEL)

        api_key = base.api_key
        raw_key = _env("ASSISTANT_LLM_API_KEY")
        if raw_key:
            api_key = raw_key
        elif provider_changed:
            specific_env = PROVIDER_KEY_ENV.get(provider)
            api_key = (_env(specific_env) if specific_env else "") or None

        base_url = _env("ASSISTANT_LLM_BASE_URL") or (
            None if provider_changed else base.base_url
        )

        timeout = base.timeout
        raw_timeout = _env("ASSISTANT_LLM_TIMEOUT")
        if raw_timeout:
            try:
                timeout = float(raw_timeout)
            except ValueError:
                pass
        return cls(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
        )


def assistant_max_output_tokens() -> int:
    """``ASSISTANT_LLM_MAX_OUTPUT_TOKENS`` (default 2048, clamped to 8192)."""
    raw = (os.getenv("ASSISTANT_LLM_MAX_OUTPUT_TOKENS") or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return ASSISTANT_DEFAULT_MAX_OUTPUT_TOKENS
    if value <= 0:
        return ASSISTANT_DEFAULT_MAX_OUTPUT_TOKENS
    return min(value, ASSISTANT_MAX_OUTPUT_TOKENS_CEILING)


def assistant_request_budget_seconds() -> Optional[float]:
    """``ASSISTANT_REQUEST_BUDGET_SECONDS``; None when unset/invalid/<=0."""
    raw = (os.getenv("ASSISTANT_REQUEST_BUDGET_SECONDS") or "").strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    if value != value or value <= 0 or value == float("inf"):
        return None
    return value


class LLMClient(ABC):
    # Set by each real client after every ``generate_text`` (reset to None at
    # the start of the call). ``last_usage`` is provider-reported token counts
    # or None; ``last_finish_reason`` is one of FINISH_REASONS or None.
    last_usage: Optional[Dict[str, Optional[int]]] = None
    last_finish_reason: Optional[str] = None

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


# Finite failure kinds (Issue #467 / #472).
LLM_ERROR_KINDS = (
    "timeout",
    "network",
    "auth",
    "rate_limited",
    "provider_error",
    "malformed_response",
    "config",
)

# Finite normalized finish reasons; ``None`` means unknown / not reported.
FINISH_REASONS = ("stop", "length", "other")


class LLMError(RuntimeError):
    """A failed LLM call.

    ``str(exc)`` is the full audit message (may embed a provider response body
    -- other features persist it, Principle 7). Callers that show or record the
    failure to end users must use ``kind`` / ``http_status`` / ``safe_message``
    instead, which never contain a provider response body.
    """

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "provider_error",
        http_status: Optional[int] = None,
        safe_message: Optional[str] = None,
    ):
        super().__init__(message)
        self.kind = kind if kind in LLM_ERROR_KINDS else "provider_error"
        self.http_status = http_status
        self.safe_message = safe_message or _default_safe_message(
            self.kind, http_status
        )


def _default_safe_message(kind: str, http_status: Optional[int]) -> str:
    if http_status is not None:
        return f"LLM request failed: HTTP {http_status}"
    return {
        "timeout": "LLM request timed out",
        "network": "LLM request failed: network error",
        "auth": "LLM request failed: authentication error",
        "rate_limited": "LLM request failed: rate limited",
        "malformed_response": "LLM response was malformed",
        "config": "LLM is not configured",
    }.get(kind, "LLM request failed")


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, (TimeoutError, socket.timeout))
    return False


def _usage(input_tokens: Any, output_tokens: Any) -> Optional[Dict[str, Optional[int]]]:
    def _num(v: Any) -> Optional[int]:
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    i, o = _num(input_tokens), _num(output_tokens)
    if i is None and o is None:
        return None
    return {"input_tokens": i, "output_tokens": o}


def _finish(raw: Any, length_value: str, stop_values: tuple) -> Optional[str]:
    if not isinstance(raw, str) or not raw:
        return None
    if raw == length_value:
        return "length"
    if raw in stop_values:
        return "stop"
    return "other"


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
        if exc.code in (401, 403):
            kind = "auth"
        elif exc.code == 429:
            kind = "rate_limited"
        else:
            kind = "provider_error"
        raise LLMError(
            f"LLM request failed: HTTP {exc.code}: {detail}",
            kind=kind,
            http_status=exc.code,
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(
            f"LLM request failed: {exc}",
            kind="timeout" if _is_timeout(exc) else "network",
        ) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"LLM response was not JSON: {raw[:500]}", kind="malformed_response"
        ) from exc


class OpenAIChatClient(LLMClient):
    def __init__(self, config: LLMConfig):
        if not config.api_key:
            raise LLMError(
                "LLM_API_KEY or OPENAI_API_KEY is required for OpenAI", kind="config"
            )
        self.config = config

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        self.last_usage = None
        self.last_finish_reason = None
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
            choice = response["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"Unexpected OpenAI response: {response}", kind="malformed_response"
            ) from exc
        usage = response.get("usage")
        if isinstance(usage, dict):
            self.last_usage = _usage(
                usage.get("prompt_tokens"), usage.get("completion_tokens")
            )
        self.last_finish_reason = _finish(
            choice.get("finish_reason") if isinstance(choice, dict) else None,
            "length",
            ("stop",),
        )
        return text


class AnthropicClient(LLMClient):
    def __init__(self, config: LLMConfig):
        if not config.api_key:
            raise LLMError(
                "LLM_API_KEY or ANTHROPIC_API_KEY is required for Anthropic",
                kind="config",
            )
        self.config = config

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        self.last_usage = None
        self.last_finish_reason = None
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
            text = "".join(
                p.get("text", "") for p in parts if p.get("type") == "text"
            )
        except (AttributeError, TypeError) as exc:
            raise LLMError(
                f"Unexpected Anthropic response: {response}",
                kind="malformed_response",
            ) from exc
        usage = response.get("usage")
        if isinstance(usage, dict):
            self.last_usage = _usage(
                usage.get("input_tokens"), usage.get("output_tokens")
            )
        self.last_finish_reason = _finish(
            response.get("stop_reason"), "max_tokens", ("end_turn", "stop_sequence")
        )
        return text


class GeminiClient(LLMClient):
    def __init__(self, config: LLMConfig):
        if not config.api_key:
            raise LLMError(
                "LLM_API_KEY or GEMINI_API_KEY is required for Gemini", kind="config"
            )
        self.config = config

    def generate_text(
        self,
        messages: List[Message],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> str:
        self.last_usage = None
        self.last_finish_reason = None
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
        usage = response.get("usageMetadata") if isinstance(response, dict) else None
        if isinstance(usage, dict):
            self.last_usage = _usage(
                usage.get("promptTokenCount"), usage.get("candidatesTokenCount")
            )
        try:
            # When finishReason is MAX_TOKENS, content can be missing or empty.
            candidate = response.get("candidates", [{}])[0]
            self.last_finish_reason = _finish(
                candidate.get("finishReason"), "MAX_TOKENS", ("STOP",)
            )
            content = candidate.get("content", {})
            if not content:
                return ""
            parts = content.get("parts", [])
            return "".join(part.get("text", "") for part in parts)
        except (IndexError, TypeError, AttributeError) as exc:
            raise LLMError(
                f"Unexpected Gemini response format: {response}",
                kind="malformed_response",
            ) from exc


class MockLLMClient(LLMClient):
    """Deterministic provider used by tests and local UI smoke checks.

    ``last_usage`` stays None (no provider reported anything) and
    ``last_finish_reason`` is ``"stop"`` (the mock always completes).
    """

    last_finish_reason: Optional[str] = "stop"

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
        if "FLOW_EXPERIMENT_DRAFT_RESPONSE_JSON" in joined:
            # Issue #415 §7.7. The node keys are echoed back OUT OF THE
            # PROMPT rather than invented: `_parse_draft_response` refuses a
            # Node outside the requested set, and a mock that fabricated one
            # would exercise the refusal path instead of the success path.
            node_keys = [
                line[2:].split("/", 1)[0].strip()
                for line in joined.splitlines()
                if line.startswith("- ") and "/" in line
            ]
            node_keys = [key for key in node_keys if key]
            # Same discipline for the evidence catalogue (#415 defect 1):
            # `_parse_draft_response` refuses an `evidence_ref` the projection
            # never produced, so the mock echoes real ids back OUT OF THE
            # PROMPT. A mock that fabricated one would only ever exercise the
            # refusal path. The catalogue lines are the `* [<id>] ...` bullets.
            evidence_ids = [
                line.split("[", 1)[1].split("]", 1)[0].strip()
                for line in joined.splitlines()
                if line.startswith("* [") and "]" in line
            ]
            evidence_ids = [ref for ref in evidence_ids if ref][:3]
            return json.dumps(
                {
                    "title": "Mock Flow experiment draft",
                    "purpose": (
                        "Mock purpose: no external LLM was called; this is "
                        "deterministic mock output."
                    ),
                    "hypothesis": (
                        "Mock hypothesis: the candidate implementation keeps "
                        "the baseline contract at lower cost."
                    ),
                    "comparison_scope": (
                        "sub_pipeline" if len(node_keys) > 1 else "single_node"
                    ),
                    "target_node_keys": node_keys,
                    "baseline_ref": "baseline:current_stable_implementation",
                    "candidate_refs": ["candidate:mock-1"],
                    "evaluation_axes": [
                        {"level": "node", "name": "output_match", "metric": "match_rate"},
                        {
                            "level": "flow_capability",
                            "name": "flow_success",
                            "metric": "completed_flow_rate",
                        },
                    ],
                    "quality_floor": {"output_match": "no regression vs baseline"},
                    "isolation_strategy": "isolated_workspace",
                    "isolation_detail": (
                        "Network-off worktree sandbox; the baseline production "
                        "path is untouched."
                    ),
                    "cost_cap": {"max_runs": 20},
                    "stop_conditions": [
                        "Any quality floor is broken.",
                        "The cost cap is reached.",
                    ],
                    "rollback_plan": (
                        "Nothing is applied: discard the candidate and keep "
                        "the pinned stable implementation."
                    ),
                    # Deliberately falls back to a fabricated id when the
                    # prompt carried no catalogue: that call is refused, which
                    # is the correct outcome for an ungrounded draft.
                    "evidence_refs": evidence_ids or ["mock:evidence-1"],
                    "risks": [
                        "Mock draft -- review every element before proposing it."
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

    @property
    def last_usage(self) -> Optional[Dict[str, Optional[int]]]:  # type: ignore[override]
        return getattr(self._delegate, "last_usage", None)

    @property
    def last_finish_reason(self) -> Optional[str]:  # type: ignore[override]
        return getattr(self._delegate, "last_finish_reason", None)

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
