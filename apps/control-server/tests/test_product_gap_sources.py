"""Tests for Issue #430 -- the Gap source federation layer (Epic #427).

`docs/product-objective-lineage.md` §5.4/§5.10 is the canonical contract this
file is organized around. `app/product_gap_sources.py` is a pure READ layer:
it calls the 14 existing gap/divergence producers by reference and never
persists anything, so every fixture here builds rows directly with
`get_conn()` (mirroring `test_ux_design.py`'s / `test_snapshot_explorers.py`'s
fixture style) rather than going through HTTP routes -- there are none to
exercise, `product_gap_sources.py` owns no route.

Coverage map (the acceptance list from the task brief):

1. Each `source_kind` gets its own fixture.
2. All five `ProductGapSourceState` values are reached somewhere in the
   suite: `current` / `changed` / `contradicted` / `disappeared` /
   `unavailable`. Where a kind cannot structurally reach a given state
   (documented in §5.4's own table), that is stated in a comment on the
   relevant test class instead of being faked.
3. One resolver failing does not prevent the others from resolving
   (`TestPartialFailureIsolation`).
4. `resolve_source` never raises for a data reason
   (`TestNeverRaisesForDataReasons`).
5. An out-of-vocabulary `source_kind` raises `ValueError`
   (`TestUnknownSourceKind`).
6. Severity is carried verbatim between vocabularies, never rewritten
   (`TestSeverityVocabularySeparation`).
7. System isolation: a ref belonging to another System resolves as
   `disappeared`, never leaking the other System's content
   (`TestSystemIsolation`).
"""

from __future__ import annotations

import json
import time
from typing import Optional

import pytest

from app import gap_triage, product_gap_sources as pgs
from app import system_understanding_service as sus
from app.models import ProductGapSourceKind, ProductGapSourceState
from typing import get_args


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-gap-sources-test.db"))
    from app.db import get_conn, init_db

    init_db()
    return get_conn


def _now() -> float:
    return time.time()


def _make_system(conn, name: str = "S") -> int:
    now = _now()
    return conn.execute(
        "INSERT INTO systems (name, environment, created_at, updated_at) VALUES (?, 'test', ?, ?)",
        (name, now, now),
    ).lastrowid


def _make_snapshot(conn, system_id: int, commit_sha: str = "abc123", status: str = "ready") -> int:
    now = _now()
    return conn.execute(
        """INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, created_at, completed_at)
           VALUES (?, '/tmp/repo', ?, ?, ?, ?)""",
        (system_id, commit_sha, status, now, now),
    ).lastrowid


def _make_session(conn, system_id: int, snapshot_id: int) -> int:
    now = _now()
    return conn.execute(
        """INSERT INTO interview_session (system_id, snapshot_id, created_at, updated_at)
           VALUES (?, ?, ?, ?)""",
        (system_id, snapshot_id, now, now),
    ).lastrowid


def _make_user(conn, username: str = "tester") -> int:
    return conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, 'x', 'admin', ?)",
        (username, _now()),
    ).lastrowid


def _make_intelligence_run(conn, system_id: int, snapshot_id: Optional[int], run_type: str = "capability_hierarchy") -> int:
    now = _now()
    return conn.execute(
        """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model, prompt_version,
                schema_version, decision_method, status, started_at, completed_at)
           VALUES (?, ?, ?, 'mock', 'mock', 'v1', 'v1', 'deterministic', 'completed', ?, ?)""",
        (system_id, snapshot_id, run_type, now, now),
    ).lastrowid


# ---------------------------------------------------------------------------
# Finite vocabularies / dispatch table shape
# ---------------------------------------------------------------------------


class TestVocabulariesAndDispatch:
    def test_source_kinds_matches_models_literal(self):
        assert set(pgs.SOURCE_KINDS) == set(get_args(ProductGapSourceKind))
        assert len(pgs.SOURCE_KINDS) == 14

    def test_source_states_matches_models_literal(self):
        assert set(pgs.SOURCE_STATES) == set(get_args(ProductGapSourceState))
        assert set(pgs.SOURCE_STATES) == {"current", "changed", "contradicted", "disappeared", "unavailable"}

    def test_every_source_kind_has_exactly_one_resolver(self):
        assert set(pgs._RESOLVERS) == set(pgs.SOURCE_KINDS)

    def test_every_source_kind_has_a_deep_link_entry(self):
        assert set(pgs._DEEP_LINKS) == set(pgs.SOURCE_KINDS)


class TestUnknownSourceKind:
    def test_out_of_vocabulary_source_kind_raises_value_error(self, db):
        with db() as conn:
            system_id = _make_system(conn)
            with pytest.raises(ValueError):
                pgs.resolve_source(conn, system_id=system_id, source_kind="not_a_real_kind", source_ref="x")


# ---------------------------------------------------------------------------
# manual -- always `current`, no external canon, no deep link (§5.4)
# ---------------------------------------------------------------------------


class TestManual:
    """`manual` structurally can reach ONLY `current` -- there is no external
    canon to disappear, be contradicted, or drift from (§5.4: "なし")."""

    def test_manual_is_always_current_with_no_deep_link(self, db):
        with db() as conn:
            system_id = _make_system(conn)
            result = pgs.resolve_source(conn, system_id=system_id, source_kind="manual", source_ref="")
        assert result.source_state == "current"
        assert result.deep_link is None
        assert result.deep_link_state == "unavailable"
        assert result.severity is None
        assert result.current_digest == ""


# ---------------------------------------------------------------------------
# system_understanding_gap
# ---------------------------------------------------------------------------


class TestSystemUnderstandingGap:
    def _fixture(self, conn):
        system_id = _make_system(conn)
        snapshot_id = _make_snapshot(conn, system_id)
        now = _now()
        conn.execute(
            """INSERT INTO code_entrypoints
                   (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                    handler_path, handler_qualified_name, line_start, line_end, route_method, route_path,
                    created_at)
               VALUES (?, ?, 'api', 'GET /widgets', 'http', 'widgets', 'app/widgets.py',
                       'app.widgets.list_widgets', 1, 5, 'GET', '/widgets', ?)""",
            (system_id, snapshot_id, _now()),
        )
        gaps = sus._load_gaps_from_reconciler(conn, system_id, snapshot_id)
        gap_triage.annotate_gaps(conn, system_id, snapshot_id, gaps)
        assert len(gaps) == 1
        gap = gaps[0]
        ref = gap_triage.gap_key(gap)
        fingerprint = gap["content_fingerprint"]
        return system_id, snapshot_id, gap, ref, fingerprint

    def test_current_when_captured_digest_matches(self, db):
        with db() as conn:
            system_id, _snap, _gap, ref, fingerprint = self._fixture(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="system_understanding_gap",
                source_ref=ref, captured_digest=fingerprint,
            )
        assert result.source_state == "current"
        assert result.severity_vocabulary == "gap_triage"
        assert result.severity == "info"
        assert result.current_digest == fingerprint

    def test_changed_when_captured_digest_differs(self, db):
        with db() as conn:
            system_id, _snap, _gap, ref, _fp = self._fixture(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="system_understanding_gap",
                source_ref=ref, captured_digest="not-the-real-fingerprint",
            )
        assert result.source_state == "changed"

    def test_disappeared_when_ref_no_longer_detected(self, db):
        with db() as conn:
            system_id, _snap, _gap, _ref, _fp = self._fixture(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="system_understanding_gap",
                source_ref="unclassified_entrypoint|entrypoint|api:GET /nonexistent",
            )
        assert result.source_state == "disappeared"

    def test_contradicted_when_triage_resolves_the_gap(self, db):
        with db() as conn:
            system_id, snapshot_id, gap, ref, fingerprint = self._fixture(conn)
            user_id = _make_user(conn)
            gap_triage.transition_gap(
                conn, system_id=system_id, snapshot_id=snapshot_id, gap=gap,
                expected_fingerprint=fingerprint, target_status="acknowledged", user_id=user_id,
            )
            gap_triage.annotate_gaps(conn, system_id, snapshot_id, [gap])
            gap_triage.transition_gap(
                conn, system_id=system_id, snapshot_id=snapshot_id, gap=gap,
                expected_fingerprint=gap["content_fingerprint"], target_status="resolved", user_id=user_id,
            )
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="system_understanding_gap", source_ref=ref,
            )
        assert result.source_state == "contradicted"
        assert result.extra["triage_status"] == "resolved"

    def test_unavailable_when_no_ready_snapshot(self, db):
        with db() as conn:
            system_id = _make_system(conn)
            # No repository_snapshots row at all for this System.
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="system_understanding_gap", source_ref="anything",
            )
        assert result.source_state == "unavailable"


# ---------------------------------------------------------------------------
# understanding_review_gap -- §5.4: no `contradicted` condition, only
# disappeared/current/changed/unavailable (its weakness is honest, §0-9).
# ---------------------------------------------------------------------------


class TestUnderstandingReviewGap:
    def _fixture(self, conn, gap_analysis=None):
        system_id = _make_system(conn)
        snapshot_id = _make_snapshot(conn, system_id)
        session_id = _make_session(conn, system_id, snapshot_id)
        now = _now()
        items = gap_analysis if gap_analysis is not None else [
            {"gap_type": "docs_only", "name": "widget flow", "summary": "no docs", "severity": "medium"},
        ]
        conn.execute(
            """INSERT INTO understanding_revision
                   (session_id, system_id, snapshot_id, current_understanding, gap_analysis, created_at)
               VALUES (?, ?, ?, '{}', ?, ?)""",
            (session_id, system_id, snapshot_id, json.dumps(items), now),
        )
        return system_id

    def test_current_when_present_and_digest_matches(self, db):
        with db() as conn:
            system_id = self._fixture(conn)
            first = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_review_gap",
                source_ref="docs_only|widget flow",
            )
            assert first.source_state == "current"
            second = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_review_gap",
                source_ref="docs_only|widget flow", captured_digest=first.current_digest,
            )
        assert second.source_state == "current"
        assert second.severity == "medium"
        assert second.severity_vocabulary == "understanding_review"

    def test_changed_when_captured_digest_differs(self, db):
        with db() as conn:
            system_id = self._fixture(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_review_gap",
                source_ref="docs_only|widget flow", captured_digest="stale-digest",
            )
        assert result.source_state == "changed"

    def test_disappeared_when_name_no_longer_present(self, db):
        with db() as conn:
            system_id = self._fixture(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_review_gap",
                source_ref="docs_only|renamed claim",
            )
        assert result.source_state == "disappeared"

    def test_unavailable_when_no_interview_session(self, db):
        with db() as conn:
            system_id = _make_system(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_review_gap", source_ref="docs_only|x",
            )
        assert result.source_state == "unavailable"


# ---------------------------------------------------------------------------
# understanding_claim_change
# ---------------------------------------------------------------------------


class TestUnderstandingClaimChange:
    def _fixture(self, conn, *, previous_summary: Optional[str], current_summary: str):
        system_id = _make_system(conn)
        snapshot_id = _make_snapshot(conn, system_id)
        session_id = _make_session(conn, system_id, snapshot_id)
        now = _now()
        if previous_summary is not None:
            prev_doc = {"system_purpose": [{"name": "Checkout", "summary": previous_summary}]}
            conn.execute(
                """INSERT INTO understanding_revision
                       (session_id, system_id, snapshot_id, current_understanding, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, system_id, snapshot_id, json.dumps(prev_doc), now - 10),
            )
        current_doc = {"system_purpose": [{"name": "Checkout", "summary": current_summary}]}
        conn.execute(
            """INSERT INTO understanding_revision
                   (session_id, system_id, snapshot_id, current_understanding, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, system_id, snapshot_id, json.dumps(current_doc), now),
        )
        return system_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id = self._fixture(conn, previous_summary="old text", current_summary="new text")
            first = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_claim_change",
                source_ref="system_purpose|Checkout",
            )
        assert first.source_state == "current"
        assert "summary_changed" in first.extra["buckets"]

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_claim_change",
                source_ref="system_purpose|Checkout", captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_contradicted_when_claim_is_now_unchanged(self, db):
        with db() as conn:
            system_id = self._fixture(conn, previous_summary="same text", current_summary="same text")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_claim_change",
                source_ref="system_purpose|Checkout",
            )
        assert result.source_state == "contradicted"
        assert result.extra["buckets"] == []

    def test_disappeared_when_claim_no_longer_in_current_revision(self, db):
        with db() as conn:
            system_id = self._fixture(conn, previous_summary="old", current_summary="new")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_claim_change",
                source_ref="system_purpose|Renamed Claim",
            )
        assert result.source_state == "disappeared"

    def test_unavailable_when_no_interview_session(self, db):
        with db() as conn:
            system_id = _make_system(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="understanding_claim_change",
                source_ref="system_purpose|Checkout",
            )
        assert result.source_state == "unavailable"


# ---------------------------------------------------------------------------
# functional_lineage_gap -- §5.4: contradicted maps onto "gap no longer in
# the projection", i.e. the same event as `disappeared`; this producer has
# no independent "resolved" flag, so `contradicted` is not separately
# reachable for this kind (documented, not faked).
# ---------------------------------------------------------------------------


class TestFunctionalLineageGap:
    def _fixture(self, conn):
        # A Need with no exchange produces a deterministic, cheap-to-set-up
        # `need_without_exchange` gap without needing the rest of the
        # Stakeholder Value Network graph populated.
        from app import stakeholder_network as sn

        system_id = _make_system(conn)
        sn.create_stakeholder(conn, system_id=system_id, stakeholder_key="buyer", display_name="Buyer", created_by="dev")
        sn.create_need(
            conn, system_id=system_id, need_key="faster-checkout", stakeholder_key="buyer",
            statement="wants a faster checkout", created_by="dev",
        )
        return system_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id = self._fixture(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="functional_lineage_gap",
                source_ref="need_without_exchange|stakeholder_need|faster-checkout",
            )
        assert result.source_state == "current"
        assert result.severity == "attention"
        assert result.severity_vocabulary == "functional_lineage"

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="functional_lineage_gap",
                source_ref="need_without_exchange|stakeholder_need|faster-checkout",
                captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_disappeared_when_ref_not_in_projection(self, db):
        with db() as conn:
            system_id = self._fixture(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="functional_lineage_gap",
                source_ref="need_without_exchange|stakeholder_need|no-such-need",
            )
        assert result.source_state == "disappeared"

    def test_unavailable_when_the_producer_raises(self, db, monkeypatch):
        with db() as conn:
            system_id = self._fixture(conn)

            def _boom(_conn, _system_id):
                raise RuntimeError("simulated read failure")

            monkeypatch.setattr(pgs.functional_lineage, "build_functional_lineage", _boom)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="functional_lineage_gap",
                source_ref="need_without_exchange|stakeholder_need|faster-checkout",
            )
        assert result.source_state == "unavailable"


# ---------------------------------------------------------------------------
# value_network_notice -- structurally the same shape as functional_lineage_gap:
# no independent "resolved" flag, so `contradicted` is not reachable
# (documented, not faked). No severity field either (§5.4's own producer).
# ---------------------------------------------------------------------------


class TestValueNetworkNotice:
    def _fixture(self, conn):
        from app import stakeholder_network as sn

        system_id = _make_system(conn)
        sn.create_stakeholder(conn, system_id=system_id, stakeholder_key="buyer", display_name="Buyer", created_by="dev")
        return system_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id = self._fixture(conn)
            # A Stakeholder with no role assigned deterministically raises
            # `stakeholder_without_role`.
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="value_network_notice",
                source_ref="stakeholder_without_role|stakeholder|buyer",
            )
        assert result.source_state == "current"
        assert result.severity is None

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="value_network_notice",
                source_ref="stakeholder_without_role|stakeholder|buyer", captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_disappeared_when_ref_not_in_notices(self, db):
        with db() as conn:
            system_id = self._fixture(conn)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="value_network_notice",
                source_ref="stakeholder_without_role|stakeholder|nobody",
            )
        assert result.source_state == "disappeared"

    def test_unavailable_when_the_producer_raises(self, db, monkeypatch):
        with db() as conn:
            system_id = self._fixture(conn)

            def _boom(_conn, _system_id):
                raise RuntimeError("simulated read failure")

            monkeypatch.setattr(pgs.stakeholder_value_network, "build_value_network", _boom)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="value_network_notice",
                source_ref="stakeholder_without_role|stakeholder|buyer",
            )
        assert result.source_state == "unavailable"


# ---------------------------------------------------------------------------
# journey_baseline_diff
# ---------------------------------------------------------------------------


class TestJourneyBaselineDiff:
    def _fixture(self, conn, *, as_is_summary: str, to_be_summary: str):
        from app import ux_design

        system_id = _make_system(conn)
        ux_design.create_journey(conn, system_id=system_id, journey_key="as-is", perspective="as_is", created_by="dev")
        ux_design.add_journey_revision(
            conn, system_id=system_id, journey_key="as-is",
            steps=[{"step_key": "checkout", "user_intent": as_is_summary}],
            created_by="dev",
        )
        as_is = conn.execute(
            "SELECT id FROM ux_journey WHERE system_id = ? AND journey_key = 'as-is'", (system_id,)
        ).fetchone()
        ux_design.create_journey(
            conn, system_id=system_id, journey_key="to-be", perspective="to_be",
            baseline_mode="linked", baseline_journey_id=as_is["id"], created_by="dev",
        )
        ux_design.add_journey_revision(
            conn, system_id=system_id, journey_key="to-be",
            steps=[{"step_key": "checkout", "user_intent": to_be_summary}],
            created_by="dev",
        )
        return system_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id = self._fixture(conn, as_is_summary="old flow", to_be_summary="new faster flow")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="journey_baseline_diff", source_ref="to-be|checkout",
            )
        assert result.source_state == "current"
        assert result.extra == {}

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="journey_baseline_diff",
                source_ref="to-be|checkout", captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_contradicted_when_step_is_unchanged(self, db):
        with db() as conn:
            system_id = self._fixture(conn, as_is_summary="same flow", to_be_summary="same flow")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="journey_baseline_diff", source_ref="to-be|checkout",
            )
        assert result.source_state == "contradicted"

    def test_disappeared_when_journey_or_step_does_not_exist(self, db):
        with db() as conn:
            system_id = self._fixture(conn, as_is_summary="a", to_be_summary="b")
            missing_journey = pgs.resolve_source(
                conn, system_id=system_id, source_kind="journey_baseline_diff", source_ref="no-such-journey|checkout",
            )
            missing_step = pgs.resolve_source(
                conn, system_id=system_id, source_kind="journey_baseline_diff", source_ref="to-be|no-such-step",
            )
        assert missing_journey.source_state == "disappeared"
        assert missing_step.source_state == "disappeared"

    def test_unavailable_when_the_producer_raises(self, db, monkeypatch):
        with db() as conn:
            system_id = self._fixture(conn, as_is_summary="a", to_be_summary="b")

            def _boom(_conn, _system_id, _journey_key):
                raise RuntimeError("simulated read failure")

            monkeypatch.setattr(pgs.ux_design, "baseline_diff_journey", _boom)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="journey_baseline_diff", source_ref="to-be|checkout",
            )
        assert result.source_state == "unavailable"


# ---------------------------------------------------------------------------
# requirement_diff
# ---------------------------------------------------------------------------


class TestRequirementDiff:
    def _fixture(self, conn, *, first_statement: str, second_statement: Optional[str]):
        from app import ux_design

        system_id = _make_system(conn)
        ux_design.create_requirement(conn, system_id=system_id, requirement_key="req-1", requirement_kind="functional", created_by="dev")
        ux_design.add_requirement_revision(
            conn, system_id=system_id, requirement_key="req-1",
            statement="v1",
            acceptance_criteria=[{"criterion_key": "c1", "statement": first_statement}],
            created_by="dev",
        )
        if second_statement is not None:
            ux_design.add_requirement_revision(
                conn, system_id=system_id, requirement_key="req-1",
                statement="v2",
                acceptance_criteria=[{"criterion_key": "c1", "statement": second_statement}],
                created_by="dev",
            )
        return system_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id = self._fixture(conn, first_statement="must respond under 2s", second_statement="must respond under 1s")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="requirement_diff", source_ref="req-1|c1",
            )
        assert result.source_state == "current"

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="requirement_diff",
                source_ref="req-1|c1", captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_contradicted_when_criterion_is_unchanged(self, db):
        with db() as conn:
            system_id = self._fixture(conn, first_statement="same", second_statement=None)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="requirement_diff", source_ref="req-1|c1",
            )
        # Single revision: the whole criterion is "added" relative to an
        # empty predecessor, which is a change, not "unchanged" -- add a
        # second identical revision to exercise the true unchanged path.
        assert result.source_state in ("current", "changed")

        with db() as conn:
            system_id = self._fixture(conn, first_statement="same", second_statement="same")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="requirement_diff", source_ref="req-1|c1",
            )
        assert result.source_state == "contradicted"

    def test_disappeared_when_requirement_or_criterion_does_not_exist(self, db):
        with db() as conn:
            system_id = self._fixture(conn, first_statement="a", second_statement="b")
            missing_req = pgs.resolve_source(
                conn, system_id=system_id, source_kind="requirement_diff", source_ref="no-such-req|c1",
            )
            missing_criterion = pgs.resolve_source(
                conn, system_id=system_id, source_kind="requirement_diff", source_ref="req-1|no-such-c",
            )
        assert missing_req.source_state == "disappeared"
        assert missing_criterion.source_state == "disappeared"

    def test_unavailable_when_the_producer_raises(self, db, monkeypatch):
        with db() as conn:
            system_id = self._fixture(conn, first_statement="a", second_statement="b")

            def _boom(_conn, _system_id, _requirement_key, **_kw):
                raise RuntimeError("simulated read failure")

            monkeypatch.setattr(pgs.ux_design, "diff_requirement_revisions", _boom)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="requirement_diff", source_ref="req-1|c1",
            )
        assert result.source_state == "unavailable"


# ---------------------------------------------------------------------------
# capability_drift
# ---------------------------------------------------------------------------


class TestCapabilityDrift:
    def _fixture(self, conn, *, current_symbol_hash: Optional[str], current_file_hash: Optional[str] = "F1"):
        system_id = _make_system(conn)
        snapshot_id = _make_snapshot(conn, system_id)
        hierarchy_run_id = _make_intelligence_run(conn, system_id, snapshot_id, "capability_hierarchy")
        _make_intelligence_run(conn, system_id, snapshot_id, "symbol_index")
        now = _now()
        conn.execute(
            """INSERT INTO capability_hierarchy_nodes
                   (system_id, snapshot_id, intelligence_run_id, node_type, name, path, qualified_name,
                    file_content_hash, symbol_source_hash, explanation_hash, created_at)
               VALUES (?, ?, ?, 'element', 'Widget List', 'app/foo.py', 'foo.bar', 'F1', 'S1', 'E1', ?)""",
            (system_id, snapshot_id, hierarchy_run_id, now),
        )
        if current_file_hash is not None:
            conn.execute(
                """INSERT INTO snapshot_files (snapshot_id, path, source_type, size_bytes, content_hash, content, inclusion_status)
                   VALUES (?, 'app/foo.py', 'source', 10, ?, X'', 'indexed')""",
                (snapshot_id, current_file_hash),
            )
        if current_symbol_hash is not None:
            sym_id = conn.execute(
                """INSERT INTO code_symbols (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line, symbol_source_hash)
                   VALUES (?, ?, 'app/foo.py', 'foo.bar', 'function', 1, 2, ?)""",
                (snapshot_id, system_id, current_symbol_hash),
            ).lastrowid
            conn.execute(
                """INSERT INTO symbol_source_metadata
                       (snapshot_id, system_id, symbol_id, path, qualified_name, start_line, end_line, raw_block, explanation_hash)
                   VALUES (?, ?, ?, 'app/foo.py', 'foo.bar', 1, 2, '', 'E1')""",
                (snapshot_id, system_id, sym_id),
            )
        return system_id, hierarchy_run_id

    def test_contradicted_when_hashes_are_all_fresh(self, db):
        with db() as conn:
            system_id, run_id = self._fixture(conn, current_symbol_hash="S1", current_file_hash="F1")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="capability_drift", source_ref="app/foo.py|foo.bar",
                captured_run_id=run_id,
            )
        assert result.source_state == "contradicted"
        assert result.extra["drift_status"] == "fresh"

    def test_current_and_changed_when_stale(self, db):
        with db() as conn:
            system_id, run_id = self._fixture(conn, current_symbol_hash="S2", current_file_hash="F1")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="capability_drift", source_ref="app/foo.py|foo.bar",
                captured_run_id=run_id,
            )
        assert result.source_state == "current"
        assert result.extra["drift_status"] == "stale"

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="capability_drift", source_ref="app/foo.py|foo.bar",
                captured_run_id=run_id, captured_digest="stale-digest",
            )
        assert changed.source_state == "changed"

    def test_disappeared_when_source_file_is_gone(self, db):
        with db() as conn:
            system_id, run_id = self._fixture(conn, current_symbol_hash=None, current_file_hash=None)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="capability_drift", source_ref="app/foo.py|foo.bar",
                captured_run_id=run_id,
            )
        assert result.source_state == "disappeared"

    def test_unavailable_when_captured_run_id_missing(self, db):
        with db() as conn:
            system_id, _run_id = self._fixture(conn, current_symbol_hash="S1")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="capability_drift", source_ref="app/foo.py|foo.bar",
            )
        assert result.source_state == "unavailable"
        assert result.extra["reason"] == "missing_captured_run_id"

    def test_unavailable_when_anchor_not_found_in_captured_run(self, db):
        with db() as conn:
            system_id, run_id = self._fixture(conn, current_symbol_hash="S1")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="capability_drift", source_ref="app/other.py|other.fn",
                captured_run_id=run_id,
            )
        assert result.source_state == "unavailable"
        assert result.extra["reason"] == "anchor_not_captured"


# ---------------------------------------------------------------------------
# runtime_alignment_mismatch
# ---------------------------------------------------------------------------


class TestRuntimeAlignmentMismatch:
    def _fixture(self, conn, *, runtime_check: Optional[str], review_subject_id: str = "subj-1", superseded: int = 0):
        system_id = _make_system(conn)
        snapshot_id = _make_snapshot(conn, system_id)
        session_id = _make_session(conn, system_id, snapshot_id)
        run_id = _make_intelligence_run(conn, system_id, snapshot_id, "alignment_build")
        now = _now()
        conn.execute(
            """INSERT INTO alignment_item
                   (session_id, system_id, snapshot_id, current_claim, alignment_state, confidence,
                    review_category, reason_code, user_reason, review_subject_id, runtime_check,
                    superseded, intelligence_run_id, created_at, updated_at)
               VALUES (?, ?, ?, 'Checkout completes in one step', 'aligned', 'medium',
                       'batch_reviewable', 'none', '', ?, ?, ?, ?, ?, ?)""",
            (session_id, system_id, snapshot_id, review_subject_id, runtime_check, superseded, run_id, now, now),
        )
        return system_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id = self._fixture(conn, runtime_check="mismatch")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="runtime_alignment_mismatch", source_ref="subj-1",
            )
        assert result.source_state == "current"

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="runtime_alignment_mismatch",
                source_ref="subj-1", captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_contradicted_when_runtime_check_matches(self, db):
        with db() as conn:
            system_id = self._fixture(conn, runtime_check="match")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="runtime_alignment_mismatch", source_ref="subj-1",
            )
        assert result.source_state == "contradicted"

    def test_disappeared_when_review_subject_id_not_found(self, db):
        with db() as conn:
            system_id = self._fixture(conn, runtime_check="mismatch")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="runtime_alignment_mismatch", source_ref="no-such-subject",
            )
        assert result.source_state == "disappeared"

    def test_disappeared_when_only_superseded_row_matches(self, db):
        with db() as conn:
            system_id = self._fixture(conn, runtime_check="mismatch", superseded=1)
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="runtime_alignment_mismatch", source_ref="subj-1",
            )
        assert result.source_state == "disappeared"


# ---------------------------------------------------------------------------
# node_anomaly -- §5.8: no Dashboard screen, deep_link is always None.
# ---------------------------------------------------------------------------


class TestNodeAnomaly:
    def _fixture(self, conn, *, status: str, dedupe_key: str = "dedupe-1"):
        system_id = _make_system(conn)
        now = _now()
        node_id = conn.execute(
            """INSERT INTO evolution_node (system_id, node_key, display_name, created_at, updated_at)
               VALUES (?, 'checkout-node', 'Checkout', ?, ?)""",
            (system_id, now, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO node_anomaly
                   (system_id, node_id, classification, severity, summary, dedupe_key, status, created_at)
               VALUES (?, ?, 'implementation_defect', 'attention', 'retries exceed budget', ?, ?, ?)""",
            (system_id, node_id, dedupe_key, status, now),
        )
        return system_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id = self._fixture(conn, status="open")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="node_anomaly", source_ref="checkout-node|dedupe-1",
            )
        assert result.source_state == "current"
        assert result.severity == "attention"
        assert result.severity_vocabulary == "node_anomaly"
        assert result.deep_link is None
        assert result.deep_link_state == "unavailable"

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="node_anomaly",
                source_ref="checkout-node|dedupe-1", captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_contradicted_when_resolved(self, db):
        with db() as conn:
            system_id = self._fixture(conn, status="resolved")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="node_anomaly", source_ref="checkout-node|dedupe-1",
            )
        assert result.source_state == "contradicted"

    def test_disappeared_when_node_or_dedupe_key_not_found(self, db):
        with db() as conn:
            system_id = self._fixture(conn, status="open")
            missing_node = pgs.resolve_source(
                conn, system_id=system_id, source_kind="node_anomaly", source_ref="no-such-node|dedupe-1",
            )
            missing_dedupe = pgs.resolve_source(
                conn, system_id=system_id, source_kind="node_anomaly", source_ref="checkout-node|no-such-dedupe",
            )
        assert missing_node.source_state == "disappeared"
        assert missing_dedupe.source_state == "disappeared"


# ---------------------------------------------------------------------------
# joint_understanding_open
# ---------------------------------------------------------------------------


class TestJointUnderstandingOpen:
    def _fixture(self, conn, *, status: str) -> tuple:
        system_id = _make_system(conn)
        snapshot_id = _make_snapshot(conn, system_id)
        session_id = _make_session(conn, system_id, snapshot_id)
        now = _now()
        row_id = conn.execute(
            """INSERT INTO joint_understanding_session
                   (session_id, system_id, origin_kind, origin_id, trigger, question_text, status,
                    schema_version, created_at, updated_at)
               VALUES (?, ?, 'qa', 1, 'explicit_request', 'What handles refunds?', ?, 'v1', ?, ?)""",
            (session_id, system_id, status, now, now),
        ).lastrowid
        return system_id, row_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id, row_id = self._fixture(conn, status="open")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="joint_understanding_open", source_ref=str(row_id),
            )
        assert result.source_state == "current"
        assert result.title == "What handles refunds?"

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="joint_understanding_open",
                source_ref=str(row_id), captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_contradicted_when_closed(self, db):
        with db() as conn:
            system_id, row_id = self._fixture(conn, status="closed")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="joint_understanding_open", source_ref=str(row_id),
            )
        assert result.source_state == "contradicted"

    def test_disappeared_when_id_not_found_or_malformed(self, db):
        with db() as conn:
            system_id, _row_id = self._fixture(conn, status="open")
            missing = pgs.resolve_source(
                conn, system_id=system_id, source_kind="joint_understanding_open", source_ref="999999",
            )
            malformed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="joint_understanding_open", source_ref="not-a-number",
            )
        assert missing.source_state == "disappeared"
        assert malformed.source_state == "disappeared"


# ---------------------------------------------------------------------------
# inquiry_unresolved
# ---------------------------------------------------------------------------


class TestInquiryUnresolved:
    def _fixture(self, conn, *, status: str) -> tuple:
        system_id = _make_system(conn)
        snapshot_id = _make_snapshot(conn, system_id)
        session_id = _make_session(conn, system_id, snapshot_id)
        now = _now()
        row_id = conn.execute(
            """INSERT INTO interview_inquiry
                   (session_id, system_id, origin_kind, origin_id, status, created_at, updated_at)
               VALUES (?, ?, 'qa', 1, ?, ?, ?)""",
            (session_id, system_id, status, now, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO interview_inquiry_message (inquiry_id, system_id, role, content, created_at)
               VALUES (?, ?, 'developer', 'Why does refund retry three times?', ?)""",
            (row_id, system_id, now),
        )
        return system_id, row_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id, row_id = self._fixture(conn, status="open")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="inquiry_unresolved", source_ref=str(row_id),
            )
        assert result.source_state == "current"
        assert result.title == "Why does refund retry three times?"

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="inquiry_unresolved",
                source_ref=str(row_id), captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_contradicted_when_answered_or_superseded(self, db):
        with db() as conn:
            system_id, row_id = self._fixture(conn, status="answered")
            answered = pgs.resolve_source(
                conn, system_id=system_id, source_kind="inquiry_unresolved", source_ref=str(row_id),
            )
        assert answered.source_state == "contradicted"

        with db() as conn:
            system_id, row_id = self._fixture(conn, status="superseded")
            superseded = pgs.resolve_source(
                conn, system_id=system_id, source_kind="inquiry_unresolved", source_ref=str(row_id),
            )
        assert superseded.source_state == "contradicted"

    def test_disappeared_when_id_not_found(self, db):
        with db() as conn:
            system_id, _row_id = self._fixture(conn, status="open")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="inquiry_unresolved", source_ref="999999",
            )
        assert result.source_state == "disappeared"


# ---------------------------------------------------------------------------
# issue_draft
# ---------------------------------------------------------------------------


class TestIssueDraft:
    def _fixture(self, conn, *, status: str) -> tuple:
        system_id = _make_system(conn)
        now = _now()
        row_id = conn.execute(
            """INSERT INTO issue_drafts (system_id, title, body_markdown, status, severity, created_at, updated_at)
               VALUES (?, 'Docs missing for refund flow', 'Body text', ?, 'warning', ?, ?)""",
            (system_id, status, now, now),
        ).lastrowid
        return system_id, row_id

    def test_current_and_changed_via_captured_digest(self, db):
        with db() as conn:
            system_id, row_id = self._fixture(conn, status="draft")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="issue_draft", source_ref=str(row_id),
            )
        assert result.source_state == "current"
        assert result.severity == "warning"
        assert result.severity_vocabulary == "issue_draft"

        with db() as conn:
            changed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="issue_draft",
                source_ref=str(row_id), captured_digest="stale",
            )
        assert changed.source_state == "changed"

    def test_contradicted_when_closed_or_rejected(self, db):
        with db() as conn:
            system_id, row_id = self._fixture(conn, status="closed")
            closed = pgs.resolve_source(
                conn, system_id=system_id, source_kind="issue_draft", source_ref=str(row_id),
            )
        assert closed.source_state == "contradicted"

        with db() as conn:
            system_id, row_id = self._fixture(conn, status="rejected")
            rejected = pgs.resolve_source(
                conn, system_id=system_id, source_kind="issue_draft", source_ref=str(row_id),
            )
        assert rejected.source_state == "contradicted"

    def test_disappeared_when_id_not_found(self, db):
        with db() as conn:
            system_id, _row_id = self._fixture(conn, status="draft")
            result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="issue_draft", source_ref="999999",
            )
        assert result.source_state == "disappeared"


# ---------------------------------------------------------------------------
# Cross-cutting: partial failure isolation
# ---------------------------------------------------------------------------


class TestPartialFailureIsolation:
    def test_one_resolver_failing_does_not_prevent_others_resolving(self, db, monkeypatch):
        with db() as conn:
            system_id = _make_system(conn)

            def _boom(_conn, _system_id):
                raise RuntimeError("simulated read failure")

            monkeypatch.setattr(pgs.functional_lineage, "build_functional_lineage", _boom)

            broken = pgs.resolve_source(
                conn, system_id=system_id, source_kind="functional_lineage_gap", source_ref="x|y|z",
            )
            fine = pgs.resolve_source(conn, system_id=system_id, source_kind="manual", source_ref="")

        assert broken.source_state == "unavailable"
        assert fine.source_state == "current"


# ---------------------------------------------------------------------------
# Cross-cutting: resolve_source never raises for data reasons
# ---------------------------------------------------------------------------


class TestNeverRaisesForDataReasons:
    def test_every_kind_survives_a_nonsense_ref_and_missing_pins(self, db):
        with db() as conn:
            system_id = _make_system(conn)
            for kind in pgs.SOURCE_KINDS:
                result = pgs.resolve_source(
                    conn, system_id=system_id, source_kind=kind, source_ref="totally|bogus|ref||with|pipes",
                )
                assert result.source_state in pgs.SOURCE_STATES

    def test_wrong_system_id_never_raises(self, db):
        with db() as conn:
            system_id = _make_system(conn)
            wrong_system_id = system_id + 999
            for kind in pgs.SOURCE_KINDS:
                result = pgs.resolve_source(
                    conn, system_id=wrong_system_id, source_kind=kind, source_ref="anything",
                )
                assert result.source_state in pgs.SOURCE_STATES


# ---------------------------------------------------------------------------
# Cross-cutting: severity vocabulary separation (#380 superset rule)
# ---------------------------------------------------------------------------


class TestSeverityVocabularySeparation:
    def test_different_kinds_keep_their_own_severity_vocabulary(self, db):
        with db() as conn:
            system_id = _make_system(conn)
            snapshot_id = _make_snapshot(conn, system_id)
            conn.execute(
                """INSERT INTO code_entrypoints
                       (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                        handler_path, handler_qualified_name, line_start, line_end, route_method, route_path,
                        created_at)
                   VALUES (?, ?, 'api', 'GET /widgets', 'http', 'widgets', 'app/widgets.py',
                           'app.widgets.list_widgets', 1, 5, 'GET', '/widgets', ?)""",
                (system_id, snapshot_id, _now()),
            )
            gaps = sus._load_gaps_from_reconciler(conn, system_id, snapshot_id)
            gap_triage.annotate_gaps(conn, system_id, snapshot_id, gaps)
            triage_ref = gap_triage.gap_key(gaps[0])
            triage_result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="system_understanding_gap", source_ref=triage_ref,
            )

            now = _now()
            node_id = conn.execute(
                """INSERT INTO evolution_node (system_id, node_key, display_name, created_at, updated_at)
                   VALUES (?, 'n1', 'N1', ?, ?)""",
                (system_id, now, now),
            ).lastrowid
            conn.execute(
                """INSERT INTO node_anomaly (system_id, node_id, classification, severity, summary, dedupe_key, status, created_at)
                   VALUES (?, ?, 'implementation_defect', 'blocking', 'x', 'd1', 'open', ?)""",
                (system_id, node_id, now),
            )
            anomaly_result = pgs.resolve_source(
                conn, system_id=system_id, source_kind="node_anomaly", source_ref="n1|d1",
            )

        # `info`/`warning` (gap_triage) is a disjoint vocabulary from
        # `blocking`/`attention`/`informational` (node_anomaly); neither
        # value is translated into the other's scale.
        assert triage_result.severity_vocabulary == "gap_triage"
        assert triage_result.severity in ("info", "warning")
        assert anomaly_result.severity_vocabulary == "node_anomaly"
        assert anomaly_result.severity == "blocking"
        assert triage_result.severity != anomaly_result.severity_vocabulary


# ---------------------------------------------------------------------------
# Cross-cutting: System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_a_ref_belonging_to_another_system_resolves_as_disappeared(self, db):
        with db() as conn:
            system_a, row_id = TestIssueDraft()._fixture(conn, status="draft")
            system_b = _make_system(conn, name="Other System")

            same_system = pgs.resolve_source(
                conn, system_id=system_a, source_kind="issue_draft", source_ref=str(row_id),
            )
            other_system = pgs.resolve_source(
                conn, system_id=system_b, source_kind="issue_draft", source_ref=str(row_id),
            )

        assert same_system.source_state == "current"
        assert other_system.source_state == "disappeared"
        # No leakage of System A's content into System B's read.
        assert other_system.title == ""
        assert other_system.detail == ""

    def test_node_anomaly_is_isolated_per_system(self, db):
        with db() as conn:
            system_a = _make_system(conn, name="A")
            system_b = _make_system(conn, name="B")
            now = _now()
            node_id = conn.execute(
                """INSERT INTO evolution_node (system_id, node_key, display_name, created_at, updated_at)
                   VALUES (?, 'shared-key', 'N', ?, ?)""",
                (system_a, now, now),
            ).lastrowid
            conn.execute(
                """INSERT INTO node_anomaly (system_id, node_id, classification, severity, summary, dedupe_key, status, created_at)
                   VALUES (?, ?, 'implementation_defect', 'attention', 'x', 'd1', 'open', ?)""",
                (system_a, node_id, now),
            )
            # A same-named node_key in System B must not resolve System A's anomaly.
            other = pgs.resolve_source(
                conn, system_id=system_b, source_kind="node_anomaly", source_ref="shared-key|d1",
            )
        assert other.source_state == "disappeared"
