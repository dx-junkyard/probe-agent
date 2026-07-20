"""Tests for Issue #135: Runtime Reality Check deterministic + reasoning layers.

Covers:
1. Deterministic trace aggregation: reproducibility, System isolation, and
   the "0 traces for an approved probe plan" signal.
2. Reasoning reconciliation fail-closed: mock/non-reasoning model rejected,
   malformed structured output rejected, a question referencing an unknown
   component_id/qualified_name rejected — never a heuristic substitute.
3. A successful reconciliation call returns validated questions tied to the
   supplied items.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import pytest

from app.llm import LLMClient, LLMConfig, LLMError, MockLLMClient
from app.models import RuntimeTraceFactsOut
from app.runtime_reality import (
    RuntimeCheckInputItem,
    aggregate_component_facts,
    generate_runtime_reality_check,
)


# --- Deterministic aggregation ------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-runtime-reality-test.db"))
    from app import db as db_module

    db_module.init_db()
    return db_module


def _insert_system(db_module, name="System A") -> int:
    with db_module.get_conn() as conn:
        now = time.time()
        cur = conn.execute(
            "INSERT INTO systems (name, environment, description, created_at, updated_at) "
            "VALUES (?, 'test', '', ?, ?)",
            (name, now, now),
        )
        return cur.lastrowid


def _insert_trace(
    db_module, system_id, component_id, *, error=None, duration_ms=10.0, timestamp=None,
    trace_id=None, environment=None, git_sha=None,
):
    now = timestamp if timestamp is not None else time.time()
    with db_module.get_conn() as conn:
        conn.execute(
            """INSERT INTO traces
                (system_id, trace_id, component_id, mode, input_json, output_text,
                 error, duration_ms, timestamp, environment, git_sha)
            VALUES (?, ?, ?, 'trace', '{}', 'out', ?, ?, ?, ?, ?)""",
            (
                system_id,
                trace_id or f"trace-{time.time_ns()}",
                component_id,
                error,
                duration_ms,
                now,
                environment,
                git_sha,
            ),
        )


def test_zero_traces_for_approved_component(db):
    system_id = _insert_system(db)
    with db.get_conn() as conn:
        facts = aggregate_component_facts(conn, system_id, "summarize_summarize_text")
    assert facts.has_traces is False
    assert facts.call_count == 0
    assert facts.error_rate is None
    assert facts.duration_p50_ms is None
    assert facts.last_observed_at is None


def test_aggregation_is_deterministic_for_same_trace_set(db):
    system_id = _insert_system(db)
    now = time.time()
    for i in range(5):
        _insert_trace(
            db, system_id, "summarize_summarize_text",
            error="boom" if i == 0 else None,
            duration_ms=float(10 * (i + 1)),
            timestamp=now,
        )

    with db.get_conn() as conn:
        first = aggregate_component_facts(conn, system_id, "summarize_summarize_text")
        second = aggregate_component_facts(conn, system_id, "summarize_summarize_text")

    assert first == second
    assert first.has_traces is True
    assert first.call_count == 5
    assert first.error_count == 1
    assert first.error_rate == pytest.approx(0.2)
    assert first.duration_p50_ms == pytest.approx(30.0)


def test_aggregation_is_system_isolated(db):
    system_a = _insert_system(db, "System A")
    system_b = _insert_system(db, "System B")

    for i in range(3):
        _insert_trace(db, system_a, "shared_component_id", duration_ms=1.0)
    for i in range(7):
        _insert_trace(db, system_b, "shared_component_id", duration_ms=2.0)

    with db.get_conn() as conn:
        facts_a = aggregate_component_facts(conn, system_a, "shared_component_id")
        facts_b = aggregate_component_facts(conn, system_b, "shared_component_id")

    assert facts_a.call_count == 3
    assert facts_b.call_count == 7


def test_aggregation_respects_window(db):
    system_id = _insert_system(db)
    old_ts = time.time() - 30 * 86_400  # 30 days ago
    _insert_trace(db, system_id, "comp", timestamp=old_ts)

    with db.get_conn() as conn:
        facts = aggregate_component_facts(
            conn, system_id, "comp", window_days_value=7,
        )
    assert facts.has_traces is False
    assert facts.call_count == 0


# --- Observed environment/git_sha (Issue #290 Finding 5) ----------------------


def test_aggregation_observed_environment_and_git_sha_none_when_no_traces(db):
    system_id = _insert_system(db)
    with db.get_conn() as conn:
        facts = aggregate_component_facts(conn, system_id, "comp")
    assert facts.observed_environment is None
    assert facts.observed_git_sha is None


def test_aggregation_observed_environment_and_git_sha_none_when_never_reported(db):
    system_id = _insert_system(db)
    _insert_trace(db, system_id, "comp", timestamp=time.time())
    with db.get_conn() as conn:
        facts = aggregate_component_facts(conn, system_id, "comp")
    assert facts.observed_environment is None
    assert facts.observed_git_sha is None


def test_aggregation_returns_latest_observed_environment_and_git_sha(db):
    """Deterministic 'greatest timestamp with a non-null, non-empty value'
    pick -- an older trace's values are superseded by a newer trace's."""
    system_id = _insert_system(db)
    now = time.time()
    _insert_trace(
        db, system_id, "comp", timestamp=now - 20, environment="staging", git_sha="old111",
    )
    _insert_trace(
        db, system_id, "comp", timestamp=now - 10, environment="production", git_sha="new222",
    )

    with db.get_conn() as conn:
        facts = aggregate_component_facts(conn, system_id, "comp")
    assert facts.observed_environment == "production"
    assert facts.observed_git_sha == "new222"


def test_aggregation_latest_trace_with_blank_fields_does_not_blank_out_earlier_values(db):
    """A later trace that never set environment/git_sha (NULL) must not hide
    an earlier trace's actually-observed values -- the pick is over non-null
    rows only, not simply 'the most recent trace's own columns'."""
    system_id = _insert_system(db)
    now = time.time()
    _insert_trace(
        db, system_id, "comp", timestamp=now - 10, environment="production", git_sha="abc123",
    )
    _insert_trace(db, system_id, "comp", timestamp=now)  # latest trace, no environment/git_sha

    with db.get_conn() as conn:
        facts = aggregate_component_facts(conn, system_id, "comp")
    assert facts.observed_environment == "production"
    assert facts.observed_git_sha == "abc123"


def test_aggregation_treats_blank_environment_and_git_sha_as_unset(db):
    system_id = _insert_system(db)
    _insert_trace(db, system_id, "comp", timestamp=time.time(), environment="", git_sha="")
    with db.get_conn() as conn:
        facts = aggregate_component_facts(conn, system_id, "comp")
    assert facts.observed_environment is None
    assert facts.observed_git_sha is None


def test_aggregation_observed_environment_and_git_sha_are_system_isolated(db):
    system_a = _insert_system(db, "System A")
    system_b = _insert_system(db, "System B")
    _insert_trace(db, system_a, "shared_component_id", environment="staging", git_sha="a1")
    _insert_trace(db, system_b, "shared_component_id", environment="production", git_sha="b2")

    with db.get_conn() as conn:
        facts_a = aggregate_component_facts(conn, system_a, "shared_component_id")
        facts_b = aggregate_component_facts(conn, system_b, "shared_component_id")
    assert facts_a.observed_environment == "staging"
    assert facts_a.observed_git_sha == "a1"
    assert facts_b.observed_environment == "production"
    assert facts_b.observed_git_sha == "b2"


# --- Reasoning reconciliation (fail-closed) -----------------------------------


class FakeLLMClient(LLMClient):
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


def _reasoning_config(provider="anthropic", model="claude-sonnet-4-5"):
    return LLMConfig(
        provider=provider, api_key="test-key", model=model, base_url=None, timeout=30,
    )


def _item(component_id="summarize_summarize_text", qualified_name="summarize.summarize_text"):
    return RuntimeCheckInputItem(
        proposal_id=1,
        decision_id=1,
        path="src/summarize.py",
        qualified_name=qualified_name,
        component_id=component_id,
        role="Summarize text with no side effects",
        probe_value="Validate latency",
        state_effects=["none"],
        recommended_mode="trace",
        facts=RuntimeTraceFactsOut(
            component_id=component_id,
            window_days=7,
            call_count=100,
            error_count=12,
            error_rate=0.12,
            duration_p50_ms=20.0,
            duration_p90_ms=40.0,
            duration_p99_ms=80.0,
            last_observed_at=time.time(),
            has_traces=True,
        ),
    )


def test_fails_closed_for_mock_client():
    result = generate_runtime_reality_check(
        MockLLMClient(),
        LLMConfig(provider="mock", api_key=None, model="mock", base_url=None, timeout=30),
        items=[_item()],
        language="ja",
    )
    assert result.error is not None
    assert result.questions == []


def test_fails_closed_for_non_reasoning_model():
    config = LLMConfig(
        provider="openai", api_key="k", model="gpt-4o-mini", base_url=None, timeout=30,
    )
    result = generate_runtime_reality_check(
        FakeLLMClient(response={"questions": []}), config, items=[_item()], language="ja",
    )
    assert result.error is not None
    assert "reasoning model" in result.error


def test_fails_closed_on_malformed_json():
    config = _reasoning_config()
    client = FakeLLMClient(response=None)
    client.generate_text = lambda *a, **k: "not json"  # type: ignore[assignment]
    result = generate_runtime_reality_check(client, config, items=[_item()], language="ja")
    assert result.error is not None


def test_fails_closed_on_llm_error():
    config = _reasoning_config()
    result = generate_runtime_reality_check(
        FakeLLMClient(error="provider timeout"), config, items=[_item()], language="ja",
    )
    assert result.error is not None
    assert "timeout" in result.error


def test_rejects_question_for_unknown_component():
    config = _reasoning_config()
    response = {
        "questions": [
            {
                "component_id": "nonexistent_component",
                "qualified_name": "nonexistent.function",
                "question_text": "Is this really side-effect free?",
                "hypothesis": "Declared none but sees writes",
                "answer_options": [],
            }
        ]
    }
    result = generate_runtime_reality_check(
        FakeLLMClient(response=response), config, items=[_item()], language="ja",
    )
    assert result.error is not None
    assert "unknown component" in result.error


def test_successful_reconciliation_returns_validated_question():
    config = _reasoning_config()
    item = _item()
    response = {
        "questions": [
            {
                "component_id": item.component_id,
                "qualified_name": item.qualified_name,
                "question_text": "12% of recent calls errored; still side-effect free?",
                "hypothesis": "Declared state_effects: [none] but error_rate is 0.12",
                "answer_options": ["はい、正しいです", "いいえ(修正を入力)"],
            }
        ]
    }
    result = generate_runtime_reality_check(
        FakeLLMClient(response=response), config, items=[item], language="ja",
    )
    assert result.error is None
    assert len(result.questions) == 1
    q = result.questions[0]
    assert q.component_id == item.component_id
    assert q.qualified_name == item.qualified_name
    assert q.answer_options == ["はい、正しいです", "いいえ(修正を入力)"]


def test_empty_questions_is_a_valid_success_result():
    config = _reasoning_config()
    result = generate_runtime_reality_check(
        FakeLLMClient(response={"questions": []}), config, items=[_item()], language="ja",
    )
    assert result.error is None
    assert result.questions == []
