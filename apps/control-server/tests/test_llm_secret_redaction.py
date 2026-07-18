import copy
from typing import Any

import pytest

from app.llm_secret_redaction import (
    AWS_ACCESS_KEY_ID_MARKER,
    GITHUB_FINE_GRAINED_PAT_MARKER,
    GITHUB_TOKEN_MARKER,
    PEM_PRIVATE_KEY_MARKER,
    redact_messages,
    redact_secret_text,
)


AWS_KEY = "AKIA" + "A" * 16
AWS_TEMP_KEY = "ASIA" + "B" * 16
GITHUB_TOKEN = "ghp_" + "c" * 36
GITHUB_STATELESS_TOKEN = "ghs_" + (
    "12345_eyJhbGciOiJIUzI1NiJ9.eyJhcHBfaWQiOjEyMzQ1fQ.signature-part_~"
)
GITHUB_FINE_GRAINED_PAT = "github_pat_" + "D" * 82
PEM_BODY = "c2Vuc2l0aXZlLXByaXZhdGUta2V5"
PEM_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\n"
    f"{PEM_BODY}\n"
    "-----END PRIVATE KEY-----"
)


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def test_known_signatures_are_redacted_deterministically_and_idempotently():
    github_tokens = [f"gh{kind}_{kind * 36}" for kind in "pousr"]
    second_pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\r\n"
        "b3BlbnNzaC1wcml2YXRlLWtleQ==\r\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    text = "\n".join(
        [
            AWS_KEY,
            AWS_TEMP_KEY,
            *github_tokens,
            GITHUB_STATELESS_TOKEN,
            GITHUB_FINE_GRAINED_PAT,
            PEM_PRIVATE_KEY,
            second_pem,
        ]
    )

    redacted = redact_secret_text(text)

    assert AWS_KEY not in redacted
    assert AWS_TEMP_KEY not in redacted
    assert all(token not in redacted for token in github_tokens)
    assert GITHUB_STATELESS_TOKEN not in redacted
    assert GITHUB_FINE_GRAINED_PAT not in redacted
    assert PEM_BODY not in redacted
    assert "b3BlbnNzaC1wcml2YXRlLWtleQ==" not in redacted
    assert redacted.count(AWS_ACCESS_KEY_ID_MARKER) == 2
    assert redacted.count(GITHUB_TOKEN_MARKER) == 6
    assert redacted.count(GITHUB_FINE_GRAINED_PAT_MARKER) == 1
    assert redacted.count(PEM_PRIVATE_KEY_MARKER) == 2
    assert redact_secret_text(redacted) == redacted


@pytest.mark.parametrize(
    "near_miss",
    [
        "AKIA" + "A" * 15,
        "AKIA" + "A" * 17,
        "X" + AWS_KEY,
        AWS_KEY + "X",
        "akia" + "a" * 16,
        "ABIA" + "A" * 16,
        "ghp_" + "a" * 19,
        "ghp_" + "a" * 513,
        "X" + GITHUB_TOKEN,
        "ghx_" + "a" * 36,
        "github_pat_" + "a" * 19,
        "github_pat_" + "a" * 256,
        "X" + GITHUB_FINE_GRAINED_PAT,
        "-----BEGIN PRIVATE KEY-----\nbody without an end",
        (
            "-----BEGIN RSA PRIVATE KEY-----\nbody\n"
            "-----END EC PRIVATE KEY-----"
        ),
        "-----BEGIN PUBLIC KEY-----\nbody\n-----END PUBLIC KEY-----",
        "-----BEGIN CERTIFICATE-----\nbody\n-----END CERTIFICATE-----",
        "9f86d081884c7d659a2feaa0c55ad015" * 3,
        "password token secret authorization",
    ],
)
def test_boundaries_and_near_misses_are_not_redacted(near_miss):
    assert redact_secret_text(near_miss) == near_miss


def test_github_token_sentence_punctuation_is_preserved():
    assert redact_secret_text(f"token={GITHUB_STATELESS_TOKEN}.") == (
        f"token={GITHUB_TOKEN_MARKER}."
    )


def test_message_redaction_is_copy_on_write_and_does_not_mutate_input():
    messages = [
        {"role": "system", "content": "ordinary instructions"},
        {
            "role": "user",
            "content": f"inspect {AWS_KEY} and {GITHUB_TOKEN}",
            "name": "kept-metadata",
        },
    ]
    original = copy.deepcopy(messages)

    redacted = redact_messages(messages)

    assert messages == original
    assert redacted is not messages
    assert redacted[0] is messages[0]
    assert redacted[1] is not messages[1]
    assert redacted[1]["role"] == "user"
    assert redacted[1]["name"] == "kept-metadata"
    assert AWS_KEY not in redacted[1]["content"]
    assert GITHUB_TOKEN not in redacted[1]["content"]
    assert redact_messages(redacted) is redacted


def test_quota_wrapper_redacts_immediately_before_delegate(monkeypatch):
    import app.llm as llm

    events = []

    class CapturingClient(llm.LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            events.append(("delegate", copy.deepcopy(messages)))
            return "ok"

    monkeypatch.setattr(
        llm, "_consume_current_system_quota", lambda: events.append(("quota", None))
    )
    messages = [{"role": "user", "content": f"credentials: {AWS_KEY}"}]
    original = copy.deepcopy(messages)

    result = llm._QuotaLLMClient(CapturingClient()).generate_text(messages)

    assert result == "ok"
    assert messages == original
    assert [event[0] for event in events] == ["quota", "delegate"]
    delegated = events[1][1]
    assert AWS_KEY not in delegated[0]["content"]
    assert AWS_ACCESS_KEY_ID_MARKER in delegated[0]["content"]


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-3-5-haiku-latest"),
        ("gemini", "gemini-1.5-flash"),
        ("mock", "mock"),
    ],
)
def test_every_provider_path_receives_only_redacted_messages(
    provider, model, monkeypatch
):
    import app.llm as llm

    captured = []
    quota_calls = []
    monkeypatch.setattr(
        llm, "_consume_current_system_quota", lambda: quota_calls.append(provider)
    )

    if provider == "mock":
        def capture_mock(self, messages, *, temperature=None, max_tokens=None):
            captured.append(copy.deepcopy(messages))
            return "ok"

        monkeypatch.setattr(llm.MockLLMClient, "generate_text", capture_mock)
    else:
        def capture_request(url, payload, *, headers, timeout):
            captured.append(copy.deepcopy(payload))
            if provider == "openai":
                return {"choices": [{"message": {"content": "ok"}}]}
            if provider == "anthropic":
                return {"content": [{"type": "text", "text": "ok"}]}
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        monkeypatch.setattr(llm, "_request_json", capture_request)

    messages = [
        {
            "role": "system",
            "content": f"AWS={AWS_KEY}\n{PEM_PRIVATE_KEY}",
        },
        {
            "role": "user",
            "content": (
                f"GitHub={GITHUB_TOKEN} stateless={GITHUB_STATELESS_TOKEN} "
                f"fine={GITHUB_FINE_GRAINED_PAT}"
            ),
        },
    ]
    original = copy.deepcopy(messages)
    client = llm.create_llm_client(
        llm.LLMConfig(
            provider=provider,
            api_key=None if provider == "mock" else "provider-api-key",
            model=model,
            base_url="https://llm.invalid",
            timeout=1,
        )
    )

    if provider == "mock":
        assert isinstance(client, llm.MockLLMClient)
    assert client.generate_text(messages) == "ok"

    assert quota_calls == [provider]
    assert messages == original
    outbound = "\n".join(_strings(captured))
    assert AWS_KEY not in outbound
    assert GITHUB_TOKEN not in outbound
    assert GITHUB_STATELESS_TOKEN not in outbound
    assert GITHUB_FINE_GRAINED_PAT not in outbound
    assert PEM_BODY not in outbound
    assert AWS_ACCESS_KEY_ID_MARKER in outbound
    assert GITHUB_TOKEN_MARKER in outbound
    assert GITHUB_FINE_GRAINED_PAT_MARKER in outbound
    assert PEM_PRIVATE_KEY_MARKER in outbound
