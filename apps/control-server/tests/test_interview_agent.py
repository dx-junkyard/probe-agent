"""Tests for Issue #69: system-understanding interview reasoning dialogue.

Covers:
1. Proposal generation from a snapshot with no existing metadata yields
   validated combined proposals (mock reasoning client, marked mock).
2. Reasoning-model failure → run fails closed, failure persisted, no proposal
   stored, no heuristic fallback.
3. A denylisted symbol (e.g. payment/auth/email) is excluded from probe
   suggestions even if the model proposes it.
4. Structured-output validation rejects an invalid element_type / operation_kind
   / state_effects value.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.interview_agent import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    InterviewTurnResult,
    generate_interview_turn,
)
from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from app.models import InterviewContextPack, InterviewSymbolItem, InterviewEvidenceLocation


# --- Helpers -----------------------------------------------------------------


def _make_config(provider="openai", model="o3"):
    return LLMConfig(
        provider=provider, api_key="test-key", model=model,
        base_url=None, timeout=30,
    )


def _mock_config():
    return LLMConfig(
        provider="mock", api_key=None, model="mock",
        base_url=None, timeout=30,
    )


def _evidence(snapshot_id=1, path="src/summarize.py", qname="summarize.summarize_text"):
    return InterviewEvidenceLocation(
        snapshot_id=snapshot_id, path=path, qualified_name=qname,
        start_line=1, end_line=10,
    )


def _context_pack(symbols=None) -> InterviewContextPack:
    if symbols is None:
        symbols = [
            InterviewSymbolItem(
                symbol_id=1, path="src/summarize.py",
                qualified_name="summarize.summarize_text", kind="function",
                start_line=1, end_line=10, classification="unclassified",
                has_metadata=False, evidence=_evidence(),
            ),
        ]
    return InterviewContextPack(
        system_id=1, snapshot_id=1, total_symbols=len(symbols),
        total_entrypoints=0, classified_count=0,
        unclassified_count=len(symbols), budget_max_chars=60000,
        budget_used_chars=1000, truncated=False, symbols=symbols,
    )


class FakeLLMClient(LLMClient):
    """Test double that returns a canned JSON response."""

    def __init__(self, response: Any = None, error: Optional[str] = None):
        self._response = response
        self._error = error

    def generate_text(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        if self._error:
            raise LLMError(self._error)
        return json.dumps(self._response)


def _valid_response(proposals=None):
    if proposals is None:
        proposals = [_valid_proposal()]
    return {
        "assistant_message": "I have analyzed the summarizer function.",
        "proposals": proposals,
        "next_questions": ["What is the expected latency budget?"],
    }


def _valid_proposal(
    path="src/summarize.py",
    qualified_name="summarize.summarize_text",
    symbol_id=1,
    element_type="core",
    operation_kind="analysis",
    state_effects=None,
    recommended_mode="trace",
    side_effect_risk="low",
    replayability="safe",
):
    return {
        "path": path,
        "qualified_name": qualified_name,
        "symbol_id": symbol_id,
        "metadata": {
            "role": "Summarize free text into a short abstract",
            "capability": "summarization",
            "system_purpose": "Help users digest long documents",
            "probe_value": "Validate summary quality and latency",
            "element_type": element_type,
            "operation_kind": operation_kind,
            "consumers": ["api"],
            "state_effects": state_effects or ["none"],
        },
        "probe_plan": {
            "feature_id": "summarization",
            "objective": "Trace summarizer inputs/outputs",
            "reason": "Pure-ish transformation, safe to trace",
            "recommended_mode": recommended_mode,
            "side_effect_risk": side_effect_risk,
            "replayability": replayability,
        },
    }


# --- Test 1: Valid proposal generation (mock marked as mock) -----------------


def test_valid_proposals_from_reasoning_model():
    """Proposal generation from a snapshot with no existing metadata yields
    validated combined proposals."""
    config = _make_config()
    client = FakeLLMClient(response=_valid_response())

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Please analyze the summarizer and propose metadata.",
    )

    assert result.error is None
    assert result.is_mock is False
    assert result.assistant_message == "I have analyzed the summarizer function."
    assert len(result.proposals) == 1

    p = result.proposals[0]
    assert p.path == "src/summarize.py"
    assert p.qualified_name == "summarize.summarize_text"
    assert p.metadata.element_type == "core"
    assert p.metadata.operation_kind == "analysis"
    assert p.metadata.state_effects == ["none"]
    assert p.probe_plan.recommended_mode == "trace"
    assert p.probe_plan.side_effect_risk == "low"
    assert p.denylist_hit is None
    assert len(result.next_questions) == 1


def test_mock_client_fails_closed():
    """Mock LLM client is rejected — interview requires reasoning model."""
    config = _mock_config()
    client = MockLLMClient()

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Please analyze.",
    )

    assert result.error is not None
    assert "reasoning model" in result.error.lower()
    assert result.is_mock is True
    assert result.proposals == []


def test_non_reasoning_model_fails_closed():
    """Non-reasoning model (e.g. gpt-4o-mini) is rejected."""
    config = _make_config(model="gpt-4o-mini")
    client = FakeLLMClient(response=_valid_response())

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Please analyze.",
    )

    assert result.error is not None
    assert "reasoning model" in result.error.lower()
    assert result.proposals == []


# --- Test 2: Reasoning failure → fails closed --------------------------------


def test_llm_api_error_fails_closed():
    """LLM API failure → error returned, no proposals."""
    config = _make_config()
    client = FakeLLMClient(error="Connection timeout")

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Analyze please.",
    )

    assert result.error is not None
    assert "Connection timeout" in result.error
    assert result.proposals == []


def test_malformed_json_fails_closed():
    """Unparseable LLM response → error returned, no proposals."""
    config = _make_config()
    client = FakeLLMClient.__new__(FakeLLMClient)
    client._error = None
    client._response = None

    class BrokenClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            return "not valid json {{"

    result = generate_interview_turn(
        BrokenClient(), config,
        context_pack=_context_pack(),
        history=[],
        user_message="Analyze.",
    )

    assert result.error is not None
    assert "parse" in result.error.lower()
    assert result.proposals == []


# --- Test 3: Denylist overrides model proposals ------------------------------


def test_denylisted_payment_symbol_excluded():
    """A symbol matching the safety denylist (payment) gets its probe plan
    overridden to high risk and marked as excluded."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(
            path="src/billing.py",
            qualified_name="billing.process_payment",
            symbol_id=2,
            side_effect_risk="low",
        ),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata for the billing module.",
    )

    assert result.error is None
    assert len(result.proposals) == 1

    p = result.proposals[0]
    assert p.denylist_hit is not None
    assert "payment" in p.denylist_hit.lower()
    assert p.probe_plan.side_effect_risk == "high"
    assert p.probe_plan.replayability == "unsafe"
    assert "denylist" in p.probe_plan.reason.lower()


def test_denylisted_auth_symbol_excluded():
    """Auth symbol matches the denylist."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(
            path="src/auth.py",
            qualified_name="auth.authenticate",
            symbol_id=3,
        ),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata.",
    )

    assert result.error is None
    assert len(result.proposals) == 1
    p = result.proposals[0]
    assert p.denylist_hit is not None
    assert p.probe_plan.side_effect_risk == "high"


def test_denylisted_email_symbol_excluded():
    """Email symbol matches the denylist."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(
            path="src/notifications.py",
            qualified_name="notifications.send_email",
            symbol_id=4,
        ),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata.",
    )

    assert result.error is None
    p = result.proposals[0]
    assert p.denylist_hit is not None
    assert p.probe_plan.side_effect_risk == "high"


def test_safe_symbol_not_denylisted():
    """A safe symbol passes through without denylist hit."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(
            path="src/summarize.py",
            qualified_name="summarize.summarize_text",
        ),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata.",
    )

    assert result.error is None
    p = result.proposals[0]
    assert p.denylist_hit is None
    assert p.probe_plan.side_effect_risk == "low"


# --- Test 4: Structured-output validation rejects invalid enums --------------


def test_invalid_element_type_rejected():
    """Invalid element_type value → error, no proposals."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(element_type="INVALID_TYPE"),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata.",
    )

    assert result.error is not None
    assert "metadata" in result.error.lower()
    assert result.proposals == []


def test_invalid_operation_kind_rejected():
    """Invalid operation_kind value → error, no proposals."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(operation_kind="WRONG_KIND"),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata.",
    )

    assert result.error is not None
    assert "metadata" in result.error.lower()
    assert result.proposals == []


def test_invalid_state_effects_rejected():
    """Invalid state_effects value → error, no proposals."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(state_effects=["INVALID_EFFECT"]),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata.",
    )

    assert result.error is not None
    assert "metadata" in result.error.lower()
    assert result.proposals == []


def test_invalid_recommended_mode_rejected():
    """Invalid recommended_mode → error, no proposals."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(recommended_mode="INVALID"),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata.",
    )

    assert result.error is not None
    assert "probe_plan" in result.error.lower()
    assert result.proposals == []


def test_invalid_side_effect_risk_rejected():
    """Invalid side_effect_risk → error, no proposals."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(side_effect_risk="CRITICAL"),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata.",
    )

    assert result.error is not None
    assert "probe_plan" in result.error.lower()
    assert result.proposals == []


# --- Additional edge cases ---------------------------------------------------


def test_empty_proposals_is_valid():
    """A response with no proposals is valid (just a conversation turn)."""
    config = _make_config()
    response = _valid_response(proposals=[])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Tell me about this system.",
    )

    assert result.error is None
    assert result.proposals == []
    assert result.assistant_message != ""


def test_history_is_included_in_prompt():
    """Conversation history is forwarded to the LLM."""
    config = _make_config()
    captured_messages = []

    class CapturingClient(LLMClient):
        def generate_text(self, messages, *, temperature=None, max_tokens=None):
            captured_messages.extend(messages)
            return json.dumps(_valid_response(proposals=[]))

    result = generate_interview_turn(
        CapturingClient(), config,
        context_pack=_context_pack(),
        history=[
            {"role": "user", "content": "What does this system do?"},
            {"role": "assistant", "content": "It processes documents."},
        ],
        user_message="Now propose metadata.",
    )

    assert result.error is None
    user_prompt = captured_messages[-1]["content"]
    assert "What does this system do?" in user_prompt
    assert "It processes documents." in user_prompt
    assert "Now propose metadata." in user_prompt


def test_multiple_proposals_with_mixed_denylist():
    """Multiple proposals: safe ones pass, denylisted ones get overridden."""
    config = _make_config()
    response = _valid_response(proposals=[
        _valid_proposal(
            path="src/summarize.py",
            qualified_name="summarize.summarize_text",
            symbol_id=1,
        ),
        _valid_proposal(
            path="src/billing.py",
            qualified_name="billing.process_payment",
            symbol_id=2,
        ),
    ])
    client = FakeLLMClient(response=response)

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Propose metadata for both.",
    )

    assert result.error is None
    assert len(result.proposals) == 2

    safe = result.proposals[0]
    assert safe.denylist_hit is None
    assert safe.probe_plan.side_effect_risk == "low"

    denylisted = result.proposals[1]
    assert denylisted.denylist_hit is not None
    assert denylisted.probe_plan.side_effect_risk == "high"


def test_prompt_version_and_schema_version():
    """Turn result carries prompt and schema versions for audit."""
    config = _make_config()
    client = FakeLLMClient(response=_valid_response(proposals=[]))

    result = generate_interview_turn(
        client, config,
        context_pack=_context_pack(),
        history=[],
        user_message="Hello.",
    )

    assert result.prompt_version == PROMPT_VERSION
    assert result.schema_version == SCHEMA_VERSION
