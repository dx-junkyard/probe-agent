"""Tests for Issue #432 -- the Objective Map / Gap Workbench / Overview
`objective` section projection (Epic #427).

`docs/product-objective-lineage.md` §9 (plus §0, §5.4, §5.10, §6) is the
canonical contract this file is organized around. Coverage map:

1. The deterministic Gap ordering ladder (`priority_band` -> `lifecycle` ->
   `milestone.sequence_hint` -> `gap_key`): same underlying facts always
   produce the same order, and a Gap's source-ref COUNT never moves it
   (`TestGapOrderingLadder`).
2. Every §9.3 `next_step` row is reachable, in order
   (`TestNextStepReachability`).
3. `waiting`/`unavailable` carry no action (`TestNextStepReachability::
   test_unavailable_carries_no_action`).
4. A System with no Product Objective -- §11's graceful empty state, never
   `degraded` (`TestNextStepReachability::test_no_objective_is_not_degraded`).
5. Per-section degradation: one Objective's/Gap's failure never blanks the
   rest of the projection (`TestPartialDegradation`).
6. `deep_link_state='unavailable'` for `node_anomaly`, which has no
   Dashboard screen (§5.8) (`TestDeepLinks`).
7. System isolation (`TestSystemIsolation`).
8. No function in this module ever writes (`TestNoWrites`).

`app/product_objective_projection.py` is a pure read layer over
`app/product_objective.py`, so every fixture here builds rows directly via
that module's public functions (mirroring `test_product_gap_sources.py`'s
fixture style) rather than going through HTTP routes, except the small
`TestRoutes` class that exercises the actual `/objective-map` /
`/gap-workbench` endpoints end to end.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional

import pytest

from app import product_objective as po
from app import product_objective_projection as pop
from app.models import GapWorkbenchOut, ObjectiveMapOut, OverviewObjectiveOut
from app.understanding_brief import BriefClaim, BriefResult


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-product-objective-projection-test.db"))
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


def _fake_brief(*, confirmed: bool = True) -> BriefResult:
    vision = BriefClaim(
        kind="vision",
        name="v",
        summary="s",
        confirmation="confirmed" if confirmed else "unconfirmed",
        provenance="developer_intent",
        confirmation_label="",
        provenance_label="",
    )
    return BriefResult(
        session_id=1,
        built=True,
        vision=vision,
        vision_missing_information=[],
        system_purpose=[],
        core_capabilities=[],
        core_capability_initial_count=0,
        key_unconfirmed=[],
        detail_counts={},
        readiness_state="ready",
        readiness_label="",
        readiness_description="",
        readiness_reasons=[],
        changes_since_confirmation=[],
        confirmed_at=_now(),
        confirmed_revision_id=1,
        revision_id=1,
        snapshot_id=1,
    )


def _make_objective(
    conn, system_id: int, objective_key: str, *, confirm: bool = True, activate: bool = True
) -> None:
    po.create_objective(conn, system_id=system_id, objective_key=objective_key, created_by="dev")
    po.add_objective_revision(conn, system_id=system_id, objective_key=objective_key, title=objective_key, created_by="dev")
    if confirm:
        po.record_objective_decision(conn, system_id=system_id, objective_key=objective_key, decision="confirm", decided_by="dev")
    if activate:
        po.record_objective_decision(conn, system_id=system_id, objective_key=objective_key, decision="activate", decided_by="dev")


def _make_milestone(
    conn, system_id: int, objective_key: str, milestone_key: str, *, sequence_hint: int = 0, confirm: bool = True
) -> None:
    po.create_milestone(conn, system_id=system_id, objective_key=objective_key, milestone_key=milestone_key, created_by="dev")
    po.add_milestone_revision(
        conn, system_id=system_id, milestone_key=milestone_key, title=milestone_key,
        sequence_hint=sequence_hint, created_by="dev",
    )
    if confirm:
        # Capture the REAL current digest, matching how a developer's own
        # confirm click would (the route always resolves it from the
        # current revision before calling `record_milestone_decision`).
        # Leaving `captured_digest` empty here would produce
        # `recheck_state='not_captured'` forever, never `'stale'` -- a
        # different, fail-closed axis (§4.2), not what a staleness test
        # needs.
        digest = conn.execute(
            """SELECT pmr.content_digest FROM product_milestone pm
               JOIN product_milestone_revision pmr ON pmr.id = pm.current_revision_id
               WHERE pm.system_id = ? AND pm.milestone_key = ?""",
            (system_id, milestone_key),
        ).fetchone()["content_digest"]
        po.record_milestone_decision(
            conn, system_id=system_id, milestone_key=milestone_key, decision="confirm",
            captured_digest=digest, decided_by="dev",
        )


def _make_gap(
    conn, system_id: int, milestone_key: str, gap_key: str, *,
    priority_band: str = "unset", lifecycle_decision: Optional[str] = None,
) -> None:
    po.create_gap(conn, system_id=system_id, milestone_key=milestone_key, gap_key=gap_key, created_by="dev")
    po.add_gap_revision(conn, system_id=system_id, gap_key=gap_key, title=gap_key, created_by="dev")
    if priority_band != "unset":
        po.record_gap_decision(conn, system_id=system_id, gap_key=gap_key, decision="prioritize", priority_band=priority_band, decided_by="dev")
    if lifecycle_decision:
        po.record_gap_decision(conn, system_id=system_id, gap_key=gap_key, decision=lifecycle_decision, decided_by="dev")


def _link_gap_to_journey(conn, system_id: int, gap_key: str, journey_key: str) -> None:
    """§5.11: a Gap's Journey connection has exactly ONE writable home,
    `ux_journey_upstream_ref(ref_kind='product_gap')` on the JOURNEY side --
    never `product_gap_artifact_link`. Creates the Journey row if it does
    not exist yet (the FK requires a real `journey_id`)."""
    now = _now()
    journey = conn.execute(
        "SELECT id FROM ux_journey WHERE system_id = ? AND journey_key = ?", (system_id, journey_key)
    ).fetchone()
    if journey is None:
        journey_id = conn.execute(
            """INSERT INTO ux_journey (system_id, journey_key, perspective, created_at, updated_at)
               VALUES (?, ?, 'to_be', ?, ?)""",
            (system_id, journey_key, now, now),
        ).lastrowid
    else:
        journey_id = journey["id"]
    conn.execute(
        """INSERT INTO ux_journey_upstream_ref
               (system_id, journey_id, ref_kind, target_ref, created_by, created_at)
           VALUES (?, ?, 'product_gap', ?, 'dev', ?)""",
        (system_id, journey_id, gap_key, now),
    )


def _full_scaffold(conn, system_id: int) -> None:
    """One Objective / Milestone -- the minimum a `next_step` walk needs
    past row #8."""
    _make_objective(conn, system_id, "obj1")
    _make_milestone(conn, system_id, "obj1", "m1", sequence_hint=1)


# ---------------------------------------------------------------------------
# 1. Deterministic ordering ladder
# ---------------------------------------------------------------------------


class TestGapOrderingLadder:
    def test_priority_band_outranks_everything(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            _make_milestone(conn, sid, "obj1", "m1", sequence_hint=1)
            _make_gap(conn, sid, "m1", "g_watch", priority_band="watch")
            _make_gap(conn, sid, "m1", "g_now", priority_band="now")
            _make_gap(conn, sid, "m1", "g_next", priority_band="next")
            _make_gap(conn, sid, "m1", "g_unset")
            conn.commit()

            result = pop.build_gap_workbench(conn, sid)
            order = [e["gap_key"] for e in result["entries"]]
            assert order == ["g_now", "g_next", "g_watch", "g_unset"]

    def test_lifecycle_is_the_second_gate(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            _make_milestone(conn, sid, "obj1", "m1", sequence_hint=1)
            _make_gap(conn, sid, "m1", "g_open")
            _make_gap(conn, sid, "m1", "g_ack", lifecycle_decision="acknowledge")
            _make_gap(conn, sid, "m1", "g_deferred", lifecycle_decision="defer")
            _make_gap(conn, sid, "m1", "g_resolved", lifecycle_decision="resolve")
            _make_gap(conn, sid, "m1", "g_rejected", lifecycle_decision="reject")
            _make_gap(conn, sid, "m1", "g_obsolete", lifecycle_decision="retire")
            conn.commit()

            result = pop.build_gap_workbench(conn, sid)
            order = [e["gap_key"] for e in result["entries"]]
            assert order == [
                "g_open", "g_ack", "g_deferred", "g_resolved", "g_rejected", "g_obsolete",
            ]

    def test_milestone_sequence_hint_then_gap_key_break_ties(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            _make_milestone(conn, sid, "obj1", "m_later", sequence_hint=5)
            _make_milestone(conn, sid, "obj1", "m_earlier", sequence_hint=1)
            _make_gap(conn, sid, "m_later", "g_z")
            _make_gap(conn, sid, "m_earlier", "g_a")
            _make_gap(conn, sid, "m_earlier", "g_b")
            conn.commit()

            result = pop.build_gap_workbench(conn, sid)
            order = [e["gap_key"] for e in result["entries"]]
            # m_earlier (sequence_hint=1) sorts before m_later (5); within
            # m_earlier, gap_key breaks the tie alphabetically.
            assert order == ["g_a", "g_b", "g_z"]

    def test_source_ref_count_never_moves_a_gap(self, db):
        """§0 invariant 7 / §5.7: counts are display-only. A Gap with many
        source refs attached must rank identically to one with none, all
        else equal."""
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            _make_milestone(conn, sid, "obj1", "m1", sequence_hint=1)
            _make_gap(conn, sid, "m1", "g_many", priority_band="now")
            _make_gap(conn, sid, "m1", "g_none", priority_band="now")
            for i in range(5):
                po.add_gap_source_ref(
                    conn, system_id=sid, gap_key="g_many", source_kind="manual",
                    source_ref=f"ref-{i}", created_by="dev",
                )
            conn.commit()

            result = pop.build_gap_workbench(conn, sid)
            order = [e["gap_key"] for e in result["entries"]]
            # Same priority_band/lifecycle/sequence_hint -> gap_key alone
            # decides, regardless of either Gap's source-ref count.
            assert order == ["g_many", "g_none"]

    def test_same_facts_always_produce_the_same_order(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            _make_milestone(conn, sid, "obj1", "m1", sequence_hint=1)
            _make_gap(conn, sid, "m1", "g1", priority_band="next")
            _make_gap(conn, sid, "m1", "g2", priority_band="now")
            _make_gap(conn, sid, "m1", "g3", priority_band="watch")
            conn.commit()

            first = [e["gap_key"] for e in pop.build_gap_workbench(conn, sid)["entries"]]
            second = [e["gap_key"] for e in pop.build_gap_workbench(conn, sid)["entries"]]
            assert first == second == ["g2", "g1", "g3"]

    def test_no_response_field_is_a_numeric_score(self, db):
        """§0 invariant 7: counts are fine, but nothing in the response is a
        weighted/aggregated score."""
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            _make_milestone(conn, sid, "obj1", "m1", sequence_hint=1)
            _make_gap(conn, sid, "m1", "g1", priority_band="now")
            po.add_gap_source_ref(conn, system_id=sid, gap_key="g1", source_kind="manual", source_ref="x", created_by="dev")
            conn.commit()

            gm = pop.build_objective_map(conn, sid)
            gw = pop.build_gap_workbench(conn, sid)
            # Every numeric field present anywhere in these responses is a
            # named COUNT (ends in _count) or an identity/sequence value --
            # never an unlabelled composite score.
            summary = gm["nodes"][0]["milestones"][0]["gap_summary"]
            for key in summary:
                assert key.endswith("_count")
            assert set(gw["source_kind_breakdown"][0].keys()) == {"source_kind", "gap_count"}


# ---------------------------------------------------------------------------
# 2/3/4. next_step reachability
# ---------------------------------------------------------------------------


class TestNextStepReachability:
    def test_row1_unavailable_with_no_brief(self, db):
        with db() as conn:
            sid = _make_system(conn)
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=None)
            assert result.next_step == "unavailable"
            assert result.next_step_state == "unavailable"

    def test_unavailable_carries_no_action(self, db):
        with db() as conn:
            sid = _make_system(conn)
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=None)
            assert result.next_step_state in ("unavailable", "waiting")
            assert result.next_step_completion == ""
            assert result.next_step_value == ""

    def test_row2_confirm_vision(self, db):
        with db() as conn:
            sid = _make_system(conn)
            conn.commit()
            brief = _fake_brief(confirmed=False)
            result = pop.build_objective_overview(conn, sid, brief=brief)
            assert result.next_step == "confirm_vision"
            assert result.next_step_state == "available"

    def test_row3_create_objective_is_not_degraded(self, db):
        with db() as conn:
            sid = _make_system(conn)
            conn.commit()
            brief = _fake_brief()
            result = pop.build_objective_overview(conn, sid, brief=brief)
            assert result.next_step == "create_objective"
            assert result.objective_state is None
            assert result.degraded_sections == []

    def test_row4_confirm_objective(self, db):
        with db() as conn:
            sid = _make_system(conn)
            po.create_objective(conn, system_id=sid, objective_key="obj1", created_by="dev")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "confirm_objective"

    def test_row5_activate_objective(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1", activate=False)
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "activate_objective"

    def test_row6_create_milestone(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "create_milestone"
            assert result.active_objective["objective_key"] == "obj1"
            assert result.active_objective_count == 1

    def test_row7_confirm_milestone(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            po.create_milestone(conn, system_id=sid, objective_key="obj1", milestone_key="m1", created_by="dev")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "confirm_milestone"

    def test_row8_recheck_stale_decision(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            _make_milestone(conn, sid, "obj1", "m1", sequence_hint=1)
            # Move the Milestone's content after confirmation -> stale.
            po.add_milestone_revision(
                conn, system_id=sid, milestone_key="m1", title="changed",
                sequence_hint=1, created_by="dev",
            )
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "recheck_stale_decision"

    def test_row9_review_gap_source(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            po.create_gap(conn, system_id=sid, milestone_key="m1", gap_key="g1", created_by="dev")
            po.add_gap_revision(conn, system_id=sid, gap_key="g1", title="g1", created_by="dev")
            now = _now()
            draft_id = conn.execute(
                """INSERT INTO issue_drafts (system_id, title, body_markdown, status, created_at, updated_at)
                   VALUES (?, 'draft title', 'body', 'draft', ?, ?)""",
                (sid, now, now),
            ).lastrowid
            # `add_gap_source_ref` captures the digest AT THIS POINT.
            po.add_gap_source_ref(
                conn, system_id=sid, gap_key="g1", source_kind="issue_draft",
                source_ref=str(draft_id), created_by="dev",
            )
            # Move the detector's own content -- `resolve_source` now
            # returns `source_state='changed'` for this ref (§5.10 step 4),
            # deterministically, no need to fake a detector.
            conn.execute("UPDATE issue_drafts SET title = 'changed title' WHERE id = ?", (draft_id,))
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "review_gap_source"

    def test_row10_create_gap(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "create_gap"
            assert result.next_milestone["milestone_key"] == "m1"

    def test_row11_prioritize_gap(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "prioritize_gap"
            assert result.primary_gap["gap_key"] == "g1"

    def test_row12_link_gap_to_journey(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="now")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "link_gap_to_journey"

    def test_row13_link_requirement_to_feature(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="now")
            _link_gap_to_journey(conn, sid, "g1", "journey-x")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "link_requirement_to_feature"

    def test_row13_names_the_requirement_whose_feature_link_is_missing(self, db):
        """§9.3: the CTA must land ON the Requirement, not merely open the
        Studio's Requirement tab and leave the developer to find it."""
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="now")
            _link_gap_to_journey(conn, sid, "g1", "journey-x")
            now = _now()
            journey_id = conn.execute(
                "SELECT id FROM ux_journey WHERE system_id = ? AND journey_key = 'journey-x'", (sid,)
            ).fetchone()["id"]
            # Two Requirements on the Journey, neither reaching a Feature.
            # The named one is the first by `requirement_key`, so the same
            # state always points the CTA at the same Requirement.
            for key in ("req-b", "req-a"):
                requirement_id = conn.execute(
                    """INSERT INTO ux_requirement (system_id, requirement_key, requirement_kind, created_at, updated_at)
                       VALUES (?, ?, 'functional', ?, ?)""",
                    (sid, key, now, now),
                ).lastrowid
                conn.execute(
                    """INSERT INTO ux_requirement_step_link
                           (system_id, requirement_id, journey_id, step_key, created_at)
                       VALUES (?, ?, ?, 'step-1', ?)""",
                    (sid, requirement_id, journey_id, now),
                )
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "link_requirement_to_feature"
            assert result.next_step_requirement_key == "req-a"

    def test_row13_leaves_the_requirement_unnamed_when_the_journey_has_none(self, db):
        """No Requirement to name -> `None`, so the CTA falls back to the
        plain tab rather than guessing one."""
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="now")
            _link_gap_to_journey(conn, sid, "g1", "journey-x")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "link_requirement_to_feature"
            assert result.next_step_requirement_key is None

    def test_no_other_row_names_a_requirement(self, db):
        """It is row #13's subject only -- never a general-purpose target."""
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="now")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "link_gap_to_journey"
            assert result.next_step_requirement_key is None

    def test_row13_clears_once_requirement_reaches_a_feature(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="now", lifecycle_decision="resolve")
            _link_gap_to_journey(conn, sid, "g1", "journey-x")
            now = _now()
            journey_id = conn.execute(
                "SELECT id FROM ux_journey WHERE system_id = ? AND journey_key = 'journey-x'", (sid,)
            ).fetchone()["id"]
            requirement_id = conn.execute(
                """INSERT INTO ux_requirement (system_id, requirement_key, requirement_kind, created_at, updated_at)
                   VALUES (?, 'req-x', 'functional', ?, ?)""",
                (sid, now, now),
            ).lastrowid
            conn.execute(
                """INSERT INTO ux_requirement_step_link
                       (system_id, requirement_id, journey_id, step_key, created_at)
                   VALUES (?, ?, ?, 'step-1', ?)""",
                (sid, requirement_id, journey_id, now),
            )
            feature_id = conn.execute(
                "INSERT INTO product_feature (system_id, feature_key, created_at, updated_at) VALUES (?, 'feat-x', ?, ?)",
                (sid, now, now),
            ).lastrowid
            conn.execute(
                """INSERT INTO product_feature_requirement_link
                       (system_id, feature_id, requirement_key, requirement_id, created_at)
                   VALUES (?, ?, 'req-x', ?, ?)""",
                (sid, feature_id, requirement_id, now),
            )
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            # Gap resolved, Journey now reaches a Feature via Requirement ->
            # row #14 (assess_milestone) is the next unmet condition.
            assert result.next_step == "assess_milestone"

    def test_row14_assess_milestone(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="watch", lifecycle_decision="resolve")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "assess_milestone"

    def test_row15_none_is_complete(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="watch", lifecycle_decision="resolve")
            po.record_milestone_assessment(conn, system_id=sid, milestone_key="m1", assessment="met", assessed_by="dev")
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            assert result.next_step == "none"
            assert result.next_step_state == "complete"

    def test_response_validates_against_the_wire_model(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            conn.commit()
            result = pop.build_objective_overview(conn, sid, brief=_fake_brief())
            OverviewObjectiveOut(
                vision=None,
                active_objective=result.active_objective,
                active_objective_count=result.active_objective_count,
                next_milestone=result.next_milestone,
                primary_gap=result.primary_gap,
                objective_state=result.objective_state,
                next_step=result.next_step,
                next_step_state=result.next_step_state,
                next_step_reason=result.next_step_reason,
                next_step_completion=result.next_step_completion,
                next_step_value=result.next_step_value,
                degraded_sections=result.degraded_sections,
                degraded_detail=result.degraded_detail,
            )


# ---------------------------------------------------------------------------
# 5. Partial degradation
# ---------------------------------------------------------------------------


class TestPartialDegradation:
    def test_one_objectives_milestones_failure_leaves_the_others(self, db, monkeypatch):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj_ok")
            _make_objective(conn, sid, "obj_broken")
            conn.commit()

            real_list_milestones = po.list_milestones

            def flaky_list_milestones(conn, system_id, objective_key):
                if objective_key == "obj_broken":
                    raise sqlite3.OperationalError("boom")
                return real_list_milestones(conn, system_id, objective_key)

            monkeypatch.setattr(po, "list_milestones", flaky_list_milestones)
            result = pop.build_objective_map(conn, sid)

            keys = {n["objective_key"]: n for n in result["nodes"]}
            assert keys["obj_ok"]["milestones"] == []  # no Milestones created, legitimately empty
            assert any(sec.startswith("milestones:obj_broken") for sec in result["degraded_sections"])
            assert not any(sec.startswith("milestones:obj_ok") for sec in result["degraded_sections"])
            # Both Objectives still render despite one's Milestones failing.
            assert set(keys) == {"obj_ok", "obj_broken"}

    def test_one_gaps_source_ref_resolution_failure_leaves_other_gaps(self, db, monkeypatch):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g_ok")
            _make_gap(conn, sid, "m1", "g_broken")
            conn.commit()

            real_get_gap_detail = po.get_gap_detail

            def flaky_get_gap_detail(conn, system_id, gap_key):
                if gap_key == "g_broken":
                    raise RuntimeError("boom")
                return real_get_gap_detail(conn, system_id, gap_key)

            monkeypatch.setattr(po, "get_gap_detail", flaky_get_gap_detail)
            result = pop.build_gap_workbench(conn, sid)

            keys = {e["gap_key"] for e in result["entries"]}
            assert keys == {"g_ok", "g_broken"}
            assert any(sec.startswith("source_refs:g_broken") for sec in result["degraded_sections"])
            assert not any(sec.startswith("source_refs:g_ok") for sec in result["degraded_sections"])

    def test_objectives_read_failure_degrades_the_whole_map(self, db, monkeypatch):
        with db() as conn:
            sid = _make_system(conn)
            _make_objective(conn, sid, "obj1")
            conn.commit()

            def broken_list_objectives(conn, system_id):
                raise RuntimeError("boom")

            monkeypatch.setattr(po, "list_objectives", broken_list_objectives)
            result = pop.build_objective_map(conn, sid)
            assert result["nodes"] == []
            assert "objectives" in result["degraded_sections"]


# ---------------------------------------------------------------------------
# 6. Deep links -- node_anomaly has no Dashboard screen
# ---------------------------------------------------------------------------


class TestDeepLinks:
    def test_node_anomaly_deep_link_is_always_unavailable(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            po.create_gap(conn, system_id=sid, milestone_key="m1", gap_key="g1", created_by="dev")
            po.add_gap_revision(conn, system_id=sid, gap_key="g1", title="g1", created_by="dev")
            po.add_gap_source_ref(
                conn, system_id=sid, gap_key="g1", source_kind="node_anomaly",
                source_ref="anomaly-x", created_by="dev",
            )
            conn.commit()

            result = pop.build_gap_workbench(conn, sid)
            entry = next(e for e in result["entries"] if e["gap_key"] == "g1")
            links = [l for l in entry["deep_links"] if l["source_kind"] == "node_anomaly"]
            assert len(links) == 1
            assert links[0]["deep_link_state"] == "unavailable"
            assert links[0]["route"] is None

    def test_a_kind_with_a_screen_reports_available(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            po.create_gap(conn, system_id=sid, milestone_key="m1", gap_key="g1", created_by="dev")
            po.add_gap_revision(conn, system_id=sid, gap_key="g1", title="g1", created_by="dev")
            po.add_gap_source_ref(
                conn, system_id=sid, gap_key="g1", source_kind="issue_draft",
                source_ref="1", created_by="dev",
            )
            conn.commit()

            result = pop.build_gap_workbench(conn, sid)
            entry = next(e for e in result["entries"] if e["gap_key"] == "g1")
            link = next(l for l in entry["deep_links"] if l["source_kind"] == "issue_draft")
            assert link["deep_link_state"] == "available"
            assert link["route"] == "/system-understanding"


# ---------------------------------------------------------------------------
# 7. System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_objective_map_never_crosses_systems(self, db):
        with db() as conn:
            sid_a = _make_system(conn, "A")
            sid_b = _make_system(conn, "B")
            _make_objective(conn, sid_a, "shared-key")
            _make_objective(conn, sid_b, "shared-key")
            conn.commit()

            map_a = pop.build_objective_map(conn, sid_a)
            map_b = pop.build_objective_map(conn, sid_b)
            assert len(map_a["nodes"]) == 1
            assert len(map_b["nodes"]) == 1
            assert map_a["nodes"][0]["id"] != map_b["nodes"][0]["id"]

    def test_gap_workbench_never_crosses_systems(self, db):
        with db() as conn:
            sid_a = _make_system(conn, "A")
            sid_b = _make_system(conn, "B")
            _full_scaffold(conn, sid_a)
            _make_objective(conn, sid_b, "obj1")
            _make_milestone(conn, sid_b, "obj1", "m1", sequence_hint=1)
            _make_gap(conn, sid_a, "m1", "shared-gap-key")
            _make_gap(conn, sid_b, "m1", "shared-gap-key")
            conn.commit()

            gw_a = pop.build_gap_workbench(conn, sid_a)
            gw_b = pop.build_gap_workbench(conn, sid_b)
            assert len(gw_a["entries"]) == 1
            assert len(gw_b["entries"]) == 1
            assert gw_a["entries"][0]["id"] != gw_b["entries"][0]["id"]

    def test_overview_section_never_crosses_systems(self, db):
        with db() as conn:
            sid_a = _make_system(conn, "A")
            sid_b = _make_system(conn, "B")
            _make_objective(conn, sid_a, "obj1")
            conn.commit()

            result_a = pop.build_objective_overview(conn, sid_a, brief=_fake_brief())
            result_b = pop.build_objective_overview(conn, sid_b, brief=_fake_brief())
            assert result_a.next_step != "create_objective"
            assert result_b.next_step == "create_objective"


# ---------------------------------------------------------------------------
# 8. No writes
# ---------------------------------------------------------------------------


def _row_snapshot(conn, tables):
    return {
        table: [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
        for table in tables
    }


_PRODUCT_TABLES = (
    "product_objective", "product_objective_revision", "product_objective_parent_link",
    "product_objective_upstream_ref", "product_objective_decision",
    "product_milestone", "product_milestone_revision", "product_milestone_dependency",
    "product_milestone_decision", "product_milestone_assessment",
    "product_gap", "product_gap_revision", "product_gap_source_ref",
    "product_gap_evidence_ref", "product_gap_artifact_link", "product_gap_decision",
)


class TestNoWrites:
    def test_build_objective_map_writes_nothing(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="now")
            conn.commit()
            before = _row_snapshot(conn, _PRODUCT_TABLES)
            pop.build_objective_map(conn, sid)
            after = _row_snapshot(conn, _PRODUCT_TABLES)
            assert before == after

    def test_build_gap_workbench_writes_nothing(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            _make_gap(conn, sid, "m1", "g1", priority_band="now")
            po.add_gap_source_ref(conn, system_id=sid, gap_key="g1", source_kind="manual", source_ref="x", created_by="dev")
            conn.commit()
            before = _row_snapshot(conn, _PRODUCT_TABLES)
            pop.build_gap_workbench(conn, sid)
            after = _row_snapshot(conn, _PRODUCT_TABLES)
            assert before == after

    def test_build_objective_overview_writes_nothing(self, db):
        with db() as conn:
            sid = _make_system(conn)
            _full_scaffold(conn, sid)
            conn.commit()
            before = _row_snapshot(conn, _PRODUCT_TABLES)
            pop.build_objective_overview(conn, sid, brief=_fake_brief())
            after = _row_snapshot(conn, _PRODUCT_TABLES)
            assert before == after


# ---------------------------------------------------------------------------
# Route-level: top-level path registration + GET wiring
# ---------------------------------------------------------------------------


class TestRoutes:
    @pytest.fixture
    def admin_client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-product-lineage-routes-test.db"))
        monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
        monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
        monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
        from app import resource_limits
        from app.main import app
        from fastapi.testclient import TestClient

        resource_limits.reset_in_memory_rate_limits()
        with TestClient(app) as c:
            yield c

    def _login(self, client):
        r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
        assert r.status_code == 200, r.text
        return r.cookies.get("probe_session")

    def _headers(self, token, system_id):
        return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}

    def _create_system(self, client, token, name):
        r = client.post(
            "/systems",
            json={"name": name, "environment": "test", "description": ""},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_objective_map_endpoint_reachable_at_its_own_top_level_path(self, admin_client):
        token = self._login(admin_client)
        system_id = self._create_system(admin_client, token, "S")
        r = admin_client.get("/objective-map", headers=self._headers(token, system_id))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["system_id"] == system_id
        assert body["nodes"] == []

    def test_gap_workbench_endpoint_reachable_at_its_own_top_level_path(self, admin_client):
        token = self._login(admin_client)
        system_id = self._create_system(admin_client, token, "S")
        r = admin_client.get("/gap-workbench", headers=self._headers(token, system_id))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["system_id"] == system_id
        assert body["entries"] == []

    def test_product_objectives_detail_route_still_owns_its_own_path(self, admin_client):
        """The routing-collision regression #338 hit: registering
        `/objective-map` as a sibling of `/product-objectives/{objective_key}`
        must never make `{objective_key}` swallow it, and vice versa."""
        token = self._login(admin_client)
        system_id = self._create_system(admin_client, token, "S")
        r = admin_client.get(
            "/product-objectives/does-not-exist", headers=self._headers(token, system_id)
        )
        assert r.status_code == 404

    def test_overview_includes_the_objective_section(self, admin_client):
        token = self._login(admin_client)
        system_id = self._create_system(admin_client, token, "S")
        r = admin_client.get("/overview", headers=self._headers(token, system_id))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["objective"] is not None
        assert body["objective"]["objective_state"] is None
        assert body["objective"]["next_step"] in ("create_objective", "confirm_vision", "unavailable")
