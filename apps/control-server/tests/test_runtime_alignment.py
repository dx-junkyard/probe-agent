"""Tests for Issue #290: Runtime Reality Check <-> Inquiry/Review Queue integration.

Covers:
1. app/runtime_reality.py's freshness_for/build_provenance: fresh/stale/
   unobserved classification, RUNTIME_FACT_FRESH_SECONDS override, provenance
   envelope shape (environment always null, snapshot_ref, source constant).
2. app/runtime_alignment.py's compare_claim_to_runtime: deterministic finite
   result (never parses claim text), unobserved/stale priority over
   environment mismatch, match as the fallback, a stale fact never returning
   'match'.
3. app/runtime_alignment.py's resolve_component_for_evidence: unique match,
   ambiguous (multiple distinct component_id) -> None, no match -> None,
   against a real code_symbols fixture.
"""

from __future__ import annotations

import time

import pytest

from app.models import RuntimeFactProvenanceOut, RuntimeFactSnapshotRefOut, RuntimeTraceFactsOut
from app.runtime_alignment import (
    RUNTIME_CHECK_STATES,
    compare_claim_to_runtime,
    resolve_component_for_evidence,
)
from app.runtime_reality import DEFAULT_FRESH_SECONDS, build_provenance, fresh_seconds, freshness_for


# --- freshness_for / fresh_seconds -------------------------------------------


def _fact(**overrides) -> RuntimeTraceFactsOut:
    base = dict(
        component_id="mod_fn", window_days=7, call_count=5, error_count=0,
        error_rate=0.0, duration_p50_ms=1.0, duration_p90_ms=2.0, duration_p99_ms=3.0,
        first_observed_at=1_000.0, last_observed_at=1_000.0, has_traces=True,
    )
    base.update(overrides)
    return RuntimeTraceFactsOut(**base)


def test_fresh_seconds_default():
    assert fresh_seconds() == DEFAULT_FRESH_SECONDS == 7 * 86_400


def test_fresh_seconds_env_override(monkeypatch):
    monkeypatch.setenv("RUNTIME_FACT_FRESH_SECONDS", "3600")
    assert fresh_seconds() == 3600


def test_fresh_seconds_rejects_invalid_value(monkeypatch):
    monkeypatch.setenv("RUNTIME_FACT_FRESH_SECONDS", "not_a_number")
    with pytest.raises(ValueError):
        fresh_seconds()


def test_freshness_for_unobserved_when_no_traces():
    fact = _fact(has_traces=False, call_count=0, last_observed_at=None, first_observed_at=None)
    assert freshness_for(fact, now=2_000.0) == "unobserved"


def test_freshness_for_fresh_within_threshold(monkeypatch):
    monkeypatch.setenv("RUNTIME_FACT_FRESH_SECONDS", "1000")
    fact = _fact(last_observed_at=1_000.0)
    assert freshness_for(fact, now=1_500.0) == "fresh"


def test_freshness_for_stale_beyond_threshold(monkeypatch):
    monkeypatch.setenv("RUNTIME_FACT_FRESH_SECONDS", "1000")
    fact = _fact(last_observed_at=1_000.0)
    assert freshness_for(fact, now=2_500.0) == "stale"


def test_freshness_for_boundary_is_fresh(monkeypatch):
    """Exactly at the threshold is still fresh (<=, not <)."""
    monkeypatch.setenv("RUNTIME_FACT_FRESH_SECONDS", "500")
    fact = _fact(last_observed_at=1_000.0)
    assert freshness_for(fact, now=1_500.0) == "fresh"


def test_freshness_for_defaults_now_to_current_time():
    fact = _fact(last_observed_at=time.time())
    assert freshness_for(fact) == "fresh"


# --- build_provenance (Issue #290 Finding 5) -----------------------------------
#
# environment/snapshot_ref now come ONLY from what was actually observed on
# traces (RuntimeTraceFactsOut.observed_environment/observed_git_sha) --
# never invented, and never the caller's pinned snapshot (that was the
# fabrication bug: Finding 5(b)).


def test_build_provenance_environment_none_when_never_observed():
    fact = _fact()  # observed_environment defaults to None
    prov = build_provenance(fact, now=1_000.0)
    assert prov.environment is None


def test_build_provenance_environment_from_observed_fact():
    fact = _fact(observed_environment="production")
    prov = build_provenance(fact, now=1_000.0)
    assert prov.environment == "production"


def test_build_provenance_shape():
    fact = _fact(
        first_observed_at=500.0, last_observed_at=1_000.0,
        observed_environment="production", observed_git_sha="abc123",
    )
    prov = build_provenance(fact, now=1_000.0)
    assert prov.first_observed_at == 500.0
    assert prov.last_observed_at == 1_000.0
    assert prov.environment == "production"
    assert prov.snapshot_ref == RuntimeFactSnapshotRefOut(snapshot_id=None, git_sha="abc123")
    assert prov.source == "trace_aggregation"
    assert prov.freshness == "fresh"


def test_build_provenance_no_snapshot_ref_when_no_sha_observed():
    fact = _fact(observed_git_sha=None)
    prov = build_provenance(fact, now=1_000.0)
    assert prov.snapshot_ref is None


def test_build_provenance_no_snapshot_ref_when_no_sha_observed_even_with_pinned_snapshot(
    system_and_snapshot,
):
    """Fabrication regression (Issue #290 Finding 5(b)): a session may have a
    pinned snapshot, but if no trace ever reported a git_sha, snapshot_ref
    must stay None -- it must NEVER silently become the pinned snapshot."""
    conn, system_id, _snapshot_id = system_and_snapshot
    fact = _fact(observed_git_sha=None)
    prov = build_provenance(fact, conn=conn, system_id=system_id, now=1_000.0)
    assert prov.snapshot_ref is None


def test_build_provenance_snapshot_ref_carries_raw_sha_without_conn():
    """Without conn/system_id, the observed sha is still carried verbatim
    (never resolved, never dropped)."""
    fact = _fact(observed_git_sha="abc123")
    prov = build_provenance(fact, now=1_000.0)
    assert prov.snapshot_ref == RuntimeFactSnapshotRefOut(snapshot_id=None, git_sha="abc123")


def test_build_provenance_snapshot_ref_resolves_on_exact_sha_match(system_and_snapshot):
    """system_and_snapshot's fixture snapshot has commit_sha='abc123'."""
    conn, system_id, snapshot_id = system_and_snapshot
    fact = _fact(observed_git_sha="abc123")
    prov = build_provenance(fact, conn=conn, system_id=system_id, now=1_000.0)
    assert prov.snapshot_ref == RuntimeFactSnapshotRefOut(snapshot_id=snapshot_id, git_sha="abc123")


def test_build_provenance_snapshot_ref_keeps_raw_sha_when_unresolved(system_and_snapshot):
    """An observed sha with no matching repository_snapshots row still keeps
    the raw sha; snapshot_id stays None (never guessed)."""
    conn, system_id, _snapshot_id = system_and_snapshot
    fact = _fact(observed_git_sha="does-not-match-anything")
    prov = build_provenance(fact, conn=conn, system_id=system_id, now=1_000.0)
    assert prov.snapshot_ref == RuntimeFactSnapshotRefOut(
        snapshot_id=None, git_sha="does-not-match-anything",
    )


def test_build_provenance_unobserved_when_no_traces():
    fact = _fact(has_traces=False, call_count=0, last_observed_at=None, first_observed_at=None)
    prov = build_provenance(fact, now=1_000.0)
    assert prov.freshness == "unobserved"


def test_build_provenance_stale_never_labeled_fresh(monkeypatch):
    monkeypatch.setenv("RUNTIME_FACT_FRESH_SECONDS", "100")
    fact = _fact(last_observed_at=1_000.0)
    prov = build_provenance(fact, now=5_000.0)
    assert prov.freshness == "stale"
    assert prov.freshness != "fresh"


# --- compare_claim_to_runtime --------------------------------------------------


def _provenance(**overrides) -> RuntimeFactProvenanceOut:
    base = dict(environment=None, first_observed_at=1.0, last_observed_at=1.0, freshness="fresh")
    base.update(overrides)
    return RuntimeFactProvenanceOut(**base)


def test_compare_claim_to_runtime_states_are_finite():
    assert set(RUNTIME_CHECK_STATES) == {"match", "mismatch", "unobserved", "stale"}


def test_compare_claim_to_runtime_unobserved():
    result = compare_claim_to_runtime("claim text", _fact(), _provenance(freshness="unobserved"))
    assert result == "unobserved"


def test_compare_claim_to_runtime_stale():
    result = compare_claim_to_runtime("claim text", _fact(), _provenance(freshness="stale"))
    assert result == "stale"


def test_compare_claim_to_runtime_never_returns_match_for_stale():
    """A stale fact must never be presented as current, regardless of claim text."""
    for claim in ("component exists", "component is deprecated", ""):
        assert compare_claim_to_runtime(claim, _fact(), _provenance(freshness="stale")) != "match"


def test_compare_claim_to_runtime_match_when_fresh_and_no_environment_conflict():
    result = compare_claim_to_runtime("claim text", _fact(), _provenance(freshness="fresh"))
    assert result == "match"


def test_compare_claim_to_runtime_match_when_expected_environment_not_given():
    prov = _provenance(freshness="fresh", environment="production")
    result = compare_claim_to_runtime("claim", _fact(), prov, expected_environment=None)
    assert result == "match"


def test_compare_claim_to_runtime_environment_mismatch():
    prov = _provenance(freshness="fresh", environment="staging")
    result = compare_claim_to_runtime("claim", _fact(), prov, expected_environment="production")
    assert result == "mismatch"


def test_compare_claim_to_runtime_environment_match_is_not_mismatch():
    prov = _provenance(freshness="fresh", environment="production")
    result = compare_claim_to_runtime("claim", _fact(), prov, expected_environment="production")
    assert result == "match"


def test_compare_claim_to_runtime_unobserved_beats_environment_mismatch():
    """Freshness checks are evaluated before the environment check."""
    prov = _provenance(freshness="unobserved", environment="staging")
    result = compare_claim_to_runtime("claim", _fact(), prov, expected_environment="production")
    assert result == "unobserved"


def test_compare_claim_to_runtime_never_inspects_claim_text():
    """Identical fact/provenance -> identical result regardless of claim text."""
    prov = _provenance(freshness="fresh")
    fact = _fact()
    results = {
        compare_claim_to_runtime(text, fact, prov)
        for text in ("this component does not exist", "this component is called constantly", "")
    }
    assert results == {"match"}


# --- resolve_component_for_evidence -------------------------------------------


@pytest.fixture
def system_and_snapshot(tmp_path, monkeypatch):
    """A real System + repository_snapshots row (FK-satisfying) for code_symbols."""
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-runtime-alignment-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
        assert r.status_code == 200, r.text
        token = r.cookies.get("probe_session")
        r = client.post(
            "/systems",
            json={"name": "Sys", "environment": "test", "description": "d"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        system_id = r.json()["id"]

        from app.db import get_conn

        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO repository_snapshots
                    (system_id, repo_path, commit_sha, status, created_at, completed_at)
                VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
                (system_id, time.time(), time.time()),
            )
            snapshot_id = cur.lastrowid
            yield conn, system_id, snapshot_id


def _insert_symbol(conn, *, snapshot_id, system_id, path, start_line, end_line, component_id, name):
    conn.execute(
        """INSERT INTO code_symbols
            (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line, component_id)
        VALUES (?, ?, ?, ?, 'function', ?, ?, ?)""",
        (snapshot_id, system_id, path, name, start_line, end_line, component_id),
    )


def test_resolve_component_for_evidence_unique_match(system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    _insert_symbol(
        conn, snapshot_id=snapshot_id, system_id=system_id, path="src/a.py",
        start_line=1, end_line=10, component_id="src_a_fn", name="a.fn",
    )
    evidence = [{"path": "src/a.py", "start_line": 2, "end_line": 5}]
    assert resolve_component_for_evidence(conn, snapshot_id, evidence) == "src_a_fn"


def test_resolve_component_for_evidence_no_match_returns_none(system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    evidence = [{"path": "src/does_not_exist.py", "start_line": 1, "end_line": 5}]
    assert resolve_component_for_evidence(conn, snapshot_id, evidence) is None


def test_resolve_component_for_evidence_ambiguous_returns_none(system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    _insert_symbol(
        conn, snapshot_id=snapshot_id, system_id=system_id, path="src/a.py",
        start_line=1, end_line=20, component_id="src_a_fn1", name="a.fn1",
    )
    _insert_symbol(
        conn, snapshot_id=snapshot_id, system_id=system_id, path="src/a.py",
        start_line=1, end_line=20, component_id="src_a_fn2", name="a.fn2",
    )
    evidence = [{"path": "src/a.py", "start_line": 2, "end_line": 5}]
    assert resolve_component_for_evidence(conn, snapshot_id, evidence) is None


def test_resolve_component_for_evidence_null_component_id_is_ignored(system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    conn.execute(
        """INSERT INTO code_symbols
            (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line, component_id)
        VALUES (?, ?, 'src/a.py', 'a.unmapped', 'function', 1, 10, NULL)""",
        (snapshot_id, system_id),
    )
    evidence = [{"path": "src/a.py", "start_line": 1, "end_line": 5}]
    assert resolve_component_for_evidence(conn, snapshot_id, evidence) is None


def test_resolve_component_for_evidence_multiple_citations_same_component(system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    _insert_symbol(
        conn, snapshot_id=snapshot_id, system_id=system_id, path="src/a.py",
        start_line=1, end_line=10, component_id="src_a_fn", name="a.fn",
    )
    _insert_symbol(
        conn, snapshot_id=snapshot_id, system_id=system_id, path="src/b.py",
        start_line=1, end_line=10, component_id="src_a_fn", name="b.also_fn",
    )
    evidence = [
        {"path": "src/a.py", "start_line": 1, "end_line": 3},
        {"path": "src/b.py", "start_line": 1, "end_line": 3},
    ]
    assert resolve_component_for_evidence(conn, snapshot_id, evidence) == "src_a_fn"


def test_resolve_component_for_evidence_out_of_range_does_not_match(system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    _insert_symbol(
        conn, snapshot_id=snapshot_id, system_id=system_id, path="src/a.py",
        start_line=1, end_line=10, component_id="src_a_fn", name="a.fn",
    )
    evidence = [{"path": "src/a.py", "start_line": 50, "end_line": 60}]
    assert resolve_component_for_evidence(conn, snapshot_id, evidence) is None


def test_resolve_component_for_evidence_snapshot_scoped(system_and_snapshot):
    conn, system_id, snapshot_id = system_and_snapshot
    _insert_symbol(
        conn, snapshot_id=snapshot_id, system_id=system_id, path="src/a.py",
        start_line=1, end_line=10, component_id="src_a_fn", name="a.fn",
    )
    evidence = [{"path": "src/a.py", "start_line": 1, "end_line": 5}]
    assert resolve_component_for_evidence(conn, snapshot_id + 999, evidence) is None
