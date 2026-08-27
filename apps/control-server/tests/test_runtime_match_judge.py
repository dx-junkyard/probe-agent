"""Tests for Issue #290 Finding 5 (Part 2): app/runtime_match_judge.py.

Covers judge_runtime_match's fail-closed contract in isolation (mock/
non-reasoning client rejected, LLM error, malformed JSON, out-of-set
runtime_check, unknown/duplicate/missing index all invalidate the WHOLE
response) and a successful batch. Route-level integration (eligibility
gating, run persistence, classify_alignment_item interaction) is covered by
tests/test_interview_alignment.py's "Runtime Match Judge integration" tests.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from app.runtime_match_judge import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    RuntimeMatchJudgeInputItem,
    judge_runtime_match,
)


class FakeLLMClient(LLMClient):
    def __init__(self, response: Any = None, error: Optional[str] = None, raw: Optional[str] = None):
        self._response = response
        self._error = error
        self._raw = raw

    def generate_text(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        if self._error:
            raise LLMError(self._error)
        if self._raw is not None:
            return self._raw
        return json.dumps(self._response)


def _reasoning_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(provider=provider, api_key="test-key", model=model, base_url=None, timeout=30)


def _items(n=1):
    return [
        RuntimeMatchJudgeInputItem(
            index=i,
            claim=f"claim {i}",
            component_id=f"comp_{i}",
            call_count=10,
            error_rate=0.0,
            duration_p50_ms=5.0,
            duration_p90_ms=8.0,
            duration_p99_ms=12.0,
            freshness="fresh",
            environment="production",
        )
        for i in range(n)
    ]


def test_fails_closed_for_mock_client():
    result = judge_runtime_match(
        MockLLMClient(),
        LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30),
        _items(),
        language="ja",
    )
    assert result.error is not None
    assert result.items == []


def test_fails_closed_for_non_reasoning_model():
    config = LLMConfig(provider="openai", api_key="k", model="gpt-4o-mini", base_url=None, timeout=30)
    result = judge_runtime_match(
        FakeLLMClient(response={"items": []}), config, _items(), language="ja",
    )
    assert result.error is not None
    assert "reasoning model" in result.error


def test_fails_closed_on_llm_error():
    config = _reasoning_config()
    result = judge_runtime_match(
        FakeLLMClient(error="timeout"), config, _items(), language="ja",
    )
    assert result.error == "timeout"
    assert result.items == []


def test_fails_closed_on_malformed_json():
    config = _reasoning_config()
    client = FakeLLMClient(raw="not json")
    result = judge_runtime_match(client, config, _items(), language="ja")
    assert result.error is not None


def test_fails_closed_on_out_of_set_runtime_check():
    config = _reasoning_config()
    client = FakeLLMClient(response={"items": [{"index": 0, "runtime_check": "maybe", "note": ""}]})
    result = judge_runtime_match(client, config, _items(1), language="ja")
    assert result.error is not None
    assert result.items == []


def test_fails_closed_on_unknown_index():
    config = _reasoning_config()
    client = FakeLLMClient(response={"items": [{"index": 5, "runtime_check": "match", "note": ""}]})
    result = judge_runtime_match(client, config, _items(1), language="ja")
    assert result.error is not None
    assert result.items == []


def test_fails_closed_on_duplicate_index():
    config = _reasoning_config()
    client = FakeLLMClient(response={"items": [
        {"index": 0, "runtime_check": "match", "note": ""},
        {"index": 0, "runtime_check": "mismatch", "note": ""},
    ]})
    result = judge_runtime_match(client, config, _items(1), language="ja")
    assert result.error is not None


def test_fails_closed_on_missing_index():
    """Two items offered, but the model only judged one -- the whole batch
    is invalid rather than silently leaving the second unjudged."""
    config = _reasoning_config()
    client = FakeLLMClient(response={"items": [{"index": 0, "runtime_check": "match", "note": ""}]})
    result = judge_runtime_match(client, config, _items(2), language="ja")
    assert result.error is not None
    assert result.items == []


def test_successful_batch_returns_validated_verdicts():
    config = _reasoning_config()
    client = FakeLLMClient(response={"items": [
        {"index": 0, "runtime_check": "match", "note": "consistent"},
        {"index": 1, "runtime_check": "mismatch", "note": "contradicts claim"},
    ]})
    result = judge_runtime_match(client, config, _items(2), language="ja")
    assert result.error is None
    assert result.prompt_version == PROMPT_VERSION == "runtime-match-v1"
    assert result.schema_version == SCHEMA_VERSION == "runtime-match-v1"
    by_index = {r.index: r.runtime_check for r in result.items}
    assert by_index == {0: "match", 1: "mismatch"}


def test_empty_items_short_circuits_without_error():
    config = _reasoning_config()
    result = judge_runtime_match(FakeLLMClient(response={"items": []}), config, [], language="ja")
    assert result.error is None
    assert result.items == []
