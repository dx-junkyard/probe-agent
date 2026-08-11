"""Issue #370: "ever connected" and "still receiving" are different questions.

The audited failure: one cumulative trace made the connectivity state
``receiving`` permanently, so a system whose last trace was ~14 days old still
showed a green 受信中. These tests pin that the two axes move independently,
that the freshness boundaries are exact, and that a disagreeing clock cannot
be mistaken for silence.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.state_facts import (
    DEFAULT_DELAYED_AFTER_SECONDS,
    DEFAULT_STALE_AFTER_SECONDS,
    classify_connectivity_freshness,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-freshness.db"))
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _trace(component_id, trace_id, timestamp=None):
    return {
        "trace_id": trace_id,
        "component_id": component_id,
        "mode": "trace",
        "input": {"args": [], "kwargs": {}},
        "output": "'ok'",
        "error": None,
        "duration_ms": 1.0,
        "timestamp": timestamp if timestamp is not None else time.time(),
    }


# --- pure classification: boundaries are exact --------------------------------


def test_never_received_when_nothing_has_arrived():
    assert classify_connectivity_freshness(last_trace_at=None, now=1000.0) == (
        "never_received"
    )


@pytest.mark.parametrize(
    "age, expected",
    [
        (0, "receiving_now"),
        (DEFAULT_DELAYED_AFTER_SECONDS - 1, "receiving_now"),
        # Inclusive at the threshold: the transition is testable at one point.
        (DEFAULT_DELAYED_AFTER_SECONDS, "delayed"),
        (DEFAULT_STALE_AFTER_SECONDS - 1, "delayed"),
        (DEFAULT_STALE_AFTER_SECONDS, "stale"),
        (14 * 86400, "stale"),
    ],
)
def test_boundaries_are_inclusive_at_the_threshold(age, expected):
    now = 1_000_000.0
    assert classify_connectivity_freshness(last_trace_at=now - age, now=now) == expected


def test_clock_skew_ahead_of_the_server_is_not_reported_as_silence():
    """A future-dated trace means data arrived, not that it stopped."""
    now = 1_000_000.0
    assert classify_connectivity_freshness(last_trace_at=now + 600, now=now) == (
        "receiving_now"
    )


def test_custom_thresholds_are_honoured():
    now = 1_000_000.0
    result = classify_connectivity_freshness(
        last_trace_at=now - 90,
        now=now,
        delayed_after_seconds=60,
        stale_after_seconds=300,
    )
    assert result == "delayed"


# --- the two axes move independently ------------------------------------------


def test_an_old_trace_keeps_the_milestone_but_loses_freshness(client):
    """The exact audited bug, as a regression test."""
    fourteen_days_ago = time.time() - 14 * 86400
    client.post("/traces", json=_trace("summarizer", "t1", fourteen_days_ago))

    body = client.get("/connectivity/status").json()
    # Milestone: this system HAS connected. That cannot un-happen.
    assert body["state"] == "receiving"
    # Live reading: it is not receiving now, and must not claim to be.
    assert body["freshness"] == "stale"
    assert body["seconds_since_last_trace"] > 13 * 86400


def test_a_fresh_trace_reports_both_axes_positively(client):
    client.post("/traces", json=_trace("summarizer", "t1"))
    body = client.get("/connectivity/status").json()
    assert body["state"] == "receiving"
    assert body["freshness"] == "receiving_now"


def test_never_received_reports_both_axes_negatively(client):
    body = client.get("/connectivity/status").json()
    assert body["state"] == "no_signal"
    assert body["freshness"] == "never_received"
    assert body["seconds_since_last_trace"] is None


# --- windowed counts ----------------------------------------------------------


def test_window_counts_partition_by_age(client):
    now = time.time()
    client.post("/traces", json=_trace("summarizer", "recent", now - 60))
    client.post("/traces", json=_trace("summarizer", "hourish", now - 1800))
    client.post("/traces", json=_trace("summarizer", "dayish", now - 40000))
    client.post("/traces", json=_trace("summarizer", "ancient", now - 200000))

    body = client.get("/connectivity/status").json()
    assert body["real_trace_count_5m"] == 1
    assert body["real_trace_count_1h"] == 2
    assert body["real_trace_count_24h"] == 3
    # The cumulative count still counts everything -- that is the difference.
    assert body["real_trace_count"] == 4


def test_window_counts_exclude_smoke_traces(client):
    """A smoke check proves the transport works, not that the workload runs."""
    client.post("/traces", json=_trace("probe-smoke-check", "s1"))
    body = client.get("/connectivity/status").json()
    assert body["real_trace_count_5m"] == 0
    assert body["real_trace_count_24h"] == 0
    # ...but it still moves the milestone off no_signal.
    assert body["state"] == "smoke_only"
    # And freshness reflects that *something* arrived: the transport is live.
    assert body["freshness"] == "receiving_now"


def test_clock_skew_is_reported_as_a_fact(client):
    client.post("/traces", json=_trace("summarizer", "t1", time.time() + 3600))
    body = client.get("/connectivity/status").json()
    assert body["clock_skew_seconds"] > 3000
    assert body["freshness"] == "receiving_now"


def test_thresholds_are_returned_so_the_state_is_explainable(client):
    body = client.get("/connectivity/status").json()
    assert body["delayed_after_seconds"] == DEFAULT_DELAYED_AFTER_SECONDS
    assert body["stale_after_seconds"] == DEFAULT_STALE_AFTER_SECONDS
    assert body["thresholds_customized"] is False
    assert body["evaluated_at"] > 0


# --- per-System policy --------------------------------------------------------


def test_policy_defaults_before_any_customization(client):
    body = client.get("/connectivity/freshness-policy").json()
    assert body["customized"] is False
    assert body["delayed_after_seconds"] == DEFAULT_DELAYED_AFTER_SECONDS


def test_custom_policy_changes_the_reported_freshness(client):
    client.post("/traces", json=_trace("summarizer", "t1", time.time() - 120))
    assert client.get("/connectivity/status").json()["freshness"] == "receiving_now"

    client.put(
        "/connectivity/freshness-policy",
        json={"delayed_after_seconds": 60, "stale_after_seconds": 300},
    )
    body = client.get("/connectivity/status").json()
    assert body["freshness"] == "delayed"
    assert body["thresholds_customized"] is True
    assert body["delayed_after_seconds"] == 60


def test_policy_rejects_thresholds_that_make_delayed_unreachable(client):
    r = client.put(
        "/connectivity/freshness-policy",
        json={"delayed_after_seconds": 600, "stale_after_seconds": 600},
    )
    assert r.status_code == 422


def test_policy_rejects_non_positive_thresholds(client):
    r = client.put(
        "/connectivity/freshness-policy",
        json={"delayed_after_seconds": 0, "stale_after_seconds": 600},
    )
    assert r.status_code == 422
