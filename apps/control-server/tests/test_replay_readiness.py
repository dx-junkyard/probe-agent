"""Issue #372: a candidate must not be generated against traces that cannot
be replayed.

The audited component had 11 traces, every one of them `not captured`. The
Candidate Studio start screen said `11 traces`, auto-selected the most recent
50, and only discovered the problem after the reasoning-model call had already
been spent.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.replay_readiness import (
    NOT_CAPTURED,
    ReplayCounts,
    count_replayability,
    evaluate_readiness,
    primary_reason,
)


def _ready_kwargs(**overrides):
    base = dict(
        component_id="summarizer",
        counts=ReplayCounts(total=0),
        selected=ReplayCounts(total=0),
        selection_limit=50,
        selection_is_automatic=True,
        symbol_resolved=True,
        approval_active=True,
        sandbox_available=True,
    )
    base.update(overrides)
    return base


# --- counting ----------------------------------------------------------------


def test_not_captured_is_counted_separately_from_unreplayable():
    """Different remediations: one needs a code change, one needs another trace."""
    rows = [
        {"replayability": "replayable"},
        {"replayability": "partial"},
        {"replayability": "unreplayable"},
        {"replayability": None},
        {"replayability": None},
    ]
    counts = count_replayability(rows)
    assert counts.total == 5
    assert counts.replayable == 1
    assert counts.partial == 1
    assert counts.unreplayable == 1
    assert counts.not_captured == 2
    # `partial` still produces a real comparison, so it counts as usable.
    assert counts.usable == 2


def test_unknown_classification_falls_into_not_captured():
    assert count_replayability([{"replayability": "weird"}]).not_captured == 1


def test_primary_reason_picks_the_most_actionable_code():
    assert primary_reason(["redacted", "capture_failed"]) == "capture_failed"
    assert primary_reason([]) is None
    assert primary_reason(None) is None


# --- verdicts ----------------------------------------------------------------


def test_zero_usable_traces_blocks_with_the_capture_remediation():
    readiness = evaluate_readiness(
        **_ready_kwargs(
            counts=ReplayCounts(total=11, not_captured=11),
            selected=ReplayCounts(total=11, not_captured=11),
        )
    )
    assert readiness.verdict == "blocked"
    blocking = [c for c in readiness.checks if c.status == "blocking"]
    assert blocking
    check = next(c for c in blocking if c.check_id == "replayable_traces")
    assert "0件" in check.summary
    # The concrete recovery step, naming the actual component.
    assert "replay_capture=True" in check.remediation
    assert "summarizer" in check.remediation


def test_no_traces_at_all_is_a_different_message_than_no_captures():
    readiness = evaluate_readiness(**_ready_kwargs())
    assert readiness.verdict == "blocked"
    assert any(c.check_id == "traces" for c in readiness.checks)
    assert not any(c.check_id == "replayable_traces" for c in readiness.checks)


def test_all_replayable_is_ready():
    readiness = evaluate_readiness(
        **_ready_kwargs(
            counts=ReplayCounts(total=5, replayable=5),
            selected=ReplayCounts(total=5, replayable=5),
        )
    )
    assert readiness.verdict == "ready"
    assert all(c.status == "ok" for c in readiness.checks)


def test_only_partial_captures_states_the_comparison_limit():
    readiness = evaluate_readiness(
        **_ready_kwargs(
            counts=ReplayCounts(total=3, partial=3),
            selected=ReplayCounts(total=3, partial=3),
        )
    )
    assert readiness.verdict == "attention"
    check = next(c for c in readiness.checks if c.check_id == "replayable_traces")
    assert "部分再現" in check.detail


def test_mixed_replayable_and_excluded_traces_requires_attention():
    readiness = evaluate_readiness(
        **_ready_kwargs(
            counts=ReplayCounts(total=3, replayable=1, unreplayable=1, not_captured=1),
            selected=ReplayCounts(total=3, replayable=1, unreplayable=1, not_captured=1),
        )
    )
    assert readiness.verdict == "attention"
    check = next(c for c in readiness.checks if c.check_id == "replayable_traces")
    assert "2件は評価母数から除外" in check.detail


def test_the_auto_selection_window_is_disclosed_when_it_truncates():
    readiness = evaluate_readiness(
        **_ready_kwargs(
            counts=ReplayCounts(total=120, replayable=120),
            selected=ReplayCounts(total=50, replayable=50),
        )
    )
    window = next(c for c in readiness.checks if c.check_id == "selection_window")
    assert "120" in window.detail and "50" in window.detail


def test_an_explicit_selection_reports_no_window_note():
    readiness = evaluate_readiness(
        **_ready_kwargs(
            counts=ReplayCounts(total=120, replayable=120),
            selected=ReplayCounts(total=2, replayable=2),
            selection_is_automatic=False,
        )
    )
    assert not any(c.check_id == "selection_window" for c in readiness.checks)


def test_missing_approval_is_attention_not_blocking():
    """A session may be prepared before approval; only the run itself refuses."""
    readiness = evaluate_readiness(
        **_ready_kwargs(
            counts=ReplayCounts(total=2, replayable=2),
            selected=ReplayCounts(total=2, replayable=2),
            approval_active=False,
        )
    )
    assert readiness.verdict == "attention"
    approval = next(c for c in readiness.checks if c.check_id == "approval")
    assert approval.status == "attention"


@pytest.mark.parametrize("field", ["symbol_resolved", "sandbox_available"])
def test_missing_preconditions_block(field):
    readiness = evaluate_readiness(
        **_ready_kwargs(
            counts=ReplayCounts(total=2, replayable=2),
            selected=ReplayCounts(total=2, replayable=2),
            **{field: False},
        )
    )
    assert readiness.verdict == "blocked"


def test_every_blocking_check_carries_a_remediation():
    readiness = evaluate_readiness(
        **_ready_kwargs(symbol_resolved=False, sandbox_available=False)
    )
    for check in readiness.checks:
        if check.status == "blocking":
            assert check.remediation, check.check_id


# --- endpoint and enforcement ------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-readiness.db"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _trace(trace_id, replayability=None, capture=None):
    event = {
        "trace_id": trace_id,
        "component_id": "summarizer",
        "mode": "trace",
        "input": {"args": [], "kwargs": {}},
        "duration_ms": 1.0,
        "timestamp": time.time(),
    }
    if replayability is not None:
        event["input_capture"] = capture or {"args": [], "kwargs": {}}
        event["replayability"] = replayability
        event["replay_reasons"] = []
    return event


def test_endpoint_reports_the_split_for_an_uncaptured_component(client):
    for index in range(11):
        client.post("/traces", json=_trace(f"t{index}"))

    body = client.get("/replay-readiness?component_id=summarizer").json()
    assert body["counts"]["total"] == 11
    assert body["counts"]["not_captured"] == 11
    assert body["counts"]["usable"] == 0
    assert body["verdict"] == "blocked"
    assert body["selection_limit"] == 50
    assert body["selection_is_automatic"] is True


def test_endpoint_reports_usable_counts_when_capture_is_on(client):
    client.post("/traces", json=_trace("a", "replayable"))
    client.post("/traces", json=_trace("b", "partial"))
    client.post("/traces", json=_trace("c", "unreplayable"))
    client.post("/traces", json=_trace("d"))

    body = client.get("/replay-readiness?component_id=summarizer").json()
    assert body["counts"] == {
        "total": 4, "replayable": 1, "partial": 1,
        "unreplayable": 1, "not_captured": 1, "usable": 2,
    }


def test_endpoint_scopes_to_explicitly_selected_traces(client):
    client.post("/traces", json=_trace("a", "replayable"))
    client.post("/traces", json=_trace("b"))

    body = client.get(
        "/replay-readiness?component_id=summarizer&trace_ids=b"
    ).json()
    assert body["selected"]["total"] == 1
    assert body["selected"]["usable"] == 0
    assert body["selection_is_automatic"] is False
    # The component-wide counts are still reported alongside.
    assert body["counts"]["total"] == 2


def test_a_component_with_no_traces_reports_zero_not_an_error(client):
    body = client.get("/replay-readiness?component_id=nothing-here").json()
    assert body["counts"]["total"] == 0
    assert body["verdict"] == "blocked"
