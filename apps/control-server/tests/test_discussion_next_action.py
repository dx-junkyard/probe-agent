"""Tests for Issue #459 (Epic #457): `app/discussion_next_action.py`, the
single server-owned "全体 next_action" projection for the Gap discussion
workflow (docs/01-specifications/ux/decision-discussion-workflow.md §3, DD-UX-01).

`evaluate_gap_discussion_next_action` is pure -- these tests exercise the
9-row priority table directly (dataclass in, dataclass out), no database.
Acceptance criteria under test:

1. Priority order is exactly 対象/権限エラー -> 必要根拠のstale/取得失敗 ->
   処理中 -> 保存結果不明 -> 未保存編集 -> 提案レビュー -> 調査結果 ->
   仮説レビュー -> 照合, verbatim (``TestPriorityOrder``).
2. `match` is always reachable as the safe fallback (``TestFallback``).
3. A DB-backed fact group's own unavailability (`proposal_status_available`
   / `ju_status_available` False) skips ONLY the rows it feeds, recorded in
   `degraded_sections`, and never blocks an earlier or later determinable
   row (``TestDegradedSections``).
4. `gather_gap_discussion_facts` reads existing owner tables (Proposal
   items/hypotheses, hypothesis promotions + their Joint Understanding
   session) and degrades each fact group independently on its own read
   failure (``TestGatherFacts``), exercised through a live DB connection.
"""

from __future__ import annotations

import time

import pytest

from app.discussion_next_action import (
    NEXT_ACTION_KINDS,
    GapDiscussionFacts,
    evaluate_gap_discussion_next_action,
    gather_gap_discussion_facts,
)


# ---------------------------------------------------------------------------
# 1. Priority order, row by row -- a higher-priority fact wins even when a
#    lower-priority one is ALSO true.
# ---------------------------------------------------------------------------


class TestPriorityOrder:
    def test_kinds_are_exactly_the_documented_priority_order(self):
        assert NEXT_ACTION_KINDS == (
            "target_error", "evidence_stale", "processing", "save_unknown",
            "unsaved_edit", "review_proposal", "investigation_result",
            "review_hypothesis", "match",
        )

    def test_target_error_beats_everything_else(self):
        facts = GapDiscussionFacts(
            target_error=True, target_error_reason="not found",
            bundle_stale=True, investigation_running=True, save_result_unknown=True,
            has_unsaved_ui_draft=True, open_proposal_exists=True,
            investigation_result_ready=True, hypothesis_pending_promotion=True,
        )
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "target_error"
        assert result.reason == "not found"

    def test_evidence_stale_beats_processing_and_below(self):
        facts = GapDiscussionFacts(
            bundle_stale=True, investigation_running=True, save_result_unknown=True,
            has_unsaved_ui_draft=True, open_proposal_exists=True,
        )
        assert evaluate_gap_discussion_next_action(facts).kind == "evidence_stale"

    def test_bundle_unavailable_section_also_reports_evidence_stale(self):
        facts = GapDiscussionFacts(
            bundle_has_unavailable_section=True, bundle_unavailable_reason="読めなかった",
        )
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "evidence_stale"
        assert result.reason == "読めなかった"

    def test_processing_beats_save_unknown_and_below(self):
        facts = GapDiscussionFacts(
            investigation_running=True, ju_session_id=7, save_result_unknown=True,
            has_unsaved_ui_draft=True, open_proposal_exists=True,
        )
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "processing"
        assert result.target_ref == "7"

    def test_save_unknown_beats_unsaved_edit_and_below(self):
        facts = GapDiscussionFacts(
            save_result_unknown=True, save_reference_id="req-9",
            has_unsaved_ui_draft=True, open_proposal_exists=True,
        )
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "save_unknown"
        assert result.target_ref == "req-9"

    def test_unsaved_edit_beats_proposal_review_and_below(self):
        facts = GapDiscussionFacts(
            has_unsaved_ui_draft=True, open_proposal_exists=True,
            investigation_result_ready=True, hypothesis_pending_promotion=True,
        )
        assert evaluate_gap_discussion_next_action(facts).kind == "unsaved_edit"

    def test_review_proposal_beats_investigation_result_and_hypothesis(self):
        facts = GapDiscussionFacts(
            open_proposal_exists=True, open_proposal_id=3,
            investigation_result_ready=True, hypothesis_pending_promotion=True,
        )
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "review_proposal"
        assert result.target_ref == "3"

    def test_investigation_result_beats_hypothesis_review(self):
        facts = GapDiscussionFacts(
            investigation_result_ready=True, ju_session_id=5, hypothesis_pending_promotion=True,
        )
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "investigation_result"
        assert result.target_ref == "5"

    def test_hypothesis_review_beats_match(self):
        facts = GapDiscussionFacts(hypothesis_pending_promotion=True, hypothesis_id=2)
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "review_hypothesis"
        assert result.target_ref == "2"


# ---------------------------------------------------------------------------
# 2. Fallback.
# ---------------------------------------------------------------------------


class TestFallback:
    def test_default_facts_yield_match(self):
        result = evaluate_gap_discussion_next_action(GapDiscussionFacts())
        assert result.kind == "match"
        assert result.degraded_sections == ()


# ---------------------------------------------------------------------------
# 3. Degraded sections: a failed read skips only ITS rows.
# ---------------------------------------------------------------------------


class TestDegradedSections:
    def test_ju_unavailable_skips_processing_and_investigation_result_only(self):
        # proposal review still fires even though the JU group is unreadable.
        facts = GapDiscussionFacts(
            ju_status_available=False, open_proposal_exists=True, open_proposal_id=1,
        )
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "review_proposal"
        assert "investigation" in result.degraded_sections

    def test_proposal_unavailable_skips_review_proposal_and_hypothesis_only(self):
        # investigation_result still fires even though the proposal group is
        # unreadable (it comes AFTER review_proposal in priority, but that
        # row was skipped, not matched).
        facts = GapDiscussionFacts(
            proposal_status_available=False, investigation_result_ready=True, ju_session_id=9,
        )
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "investigation_result"
        assert "proposal" in result.degraded_sections

    def test_both_unavailable_falls_back_to_match_with_both_degraded(self):
        facts = GapDiscussionFacts(proposal_status_available=False, ju_status_available=False)
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "match"
        assert set(result.degraded_sections) == {"proposal", "investigation"}

    def test_ju_unavailable_never_blocks_an_earlier_row(self):
        facts = GapDiscussionFacts(target_error=True, ju_status_available=False)
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "target_error"
        # target_error returns before degraded_sections is even consulted.
        assert result.degraded_sections == ()


# ---------------------------------------------------------------------------
# 4. `gather_gap_discussion_facts` against a live DB.
# ---------------------------------------------------------------------------


@pytest.fixture
def conn_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-discussion-next-action-test.db"))
    from app import db

    db.init_db()
    return db.get_conn


def _make_system(conn) -> int:
    now = time.time()
    conn.execute(
        "INSERT INTO systems (name, environment, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("next-action-sys", "test", "", now, now),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def _make_thread(conn, system_id: int) -> int:
    now = time.time()
    conn.execute(
        """INSERT INTO assistant_discussion_thread
               (system_id, thread_key, scope, screen_id, target_kind, target_ref, target_title,
                captured_target_revision_id, captured_target_digest, status, created_at, updated_at,
                schema_version)
           VALUES (?, ?, 'entity', 'objective-map', 'product_gap', 'gap-1', 'Gap 1', NULL, '', 'open', ?, ?,
                   'assistant-discussion-thread-v1')""",
        (system_id, f"objective-map|entity|product_gap|gap-1|{system_id}", now, now),
    )
    return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


class TestGatherFacts:
    def test_no_rows_anywhere_yields_all_defaults(self, conn_factory):
        with conn_factory() as conn:
            system_id = _make_system(conn)
            thread_id = _make_thread(conn, system_id)
            facts = gather_gap_discussion_facts(
                conn, system_id, thread_id, target_state="current", self_operation_state="available",
            )
        assert facts.open_proposal_exists is False
        assert facts.hypothesis_pending_promotion is False
        assert facts.investigation_running is False
        assert facts.investigation_result_ready is False
        assert facts.proposal_status_available is True
        assert facts.ju_status_available is True
        assert evaluate_gap_discussion_next_action(facts).kind == "match"

    def test_open_proposal_item_is_detected(self, conn_factory):
        with conn_factory() as conn:
            system_id = _make_system(conn)
            thread_id = _make_thread(conn, system_id)
            now = time.time()
            conn.execute(
                """INSERT INTO assistant_discussion_proposal
                       (system_id, thread_id, screen_id, target_kind, target_ref,
                        captured_target_revision_id, captured_target_digest, summary,
                        confirmed_points_json, unresolved_questions_json, assumptions_json,
                        evidence_refs_json, decision_method, intelligence_run_id, provider, model,
                        prompt_version, schema_version, created_by, created_at)
                   VALUES (?, ?, 'objective-map', 'product_gap', 'gap-1', NULL, '', '',
                           '[]', '[]', '[]', '[]', 'reasoning_llm', NULL, 'openai', 'gpt-5', 'v1', 'v1',
                           NULL, ?)""",
                (system_id, thread_id, now),
            )
            proposal_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            conn.execute(
                """INSERT INTO assistant_discussion_proposal_item
                       (system_id, proposal_id, item_kind, field_name, current_value, proposed_value,
                        rationale, status, created_at)
                   VALUES (?, ?, 'field', 'title', 'old', 'new', 'because', 'proposed', ?)""",
                (system_id, proposal_id, now),
            )
            facts = gather_gap_discussion_facts(
                conn, system_id, thread_id, target_state="current", self_operation_state="available",
            )
        assert facts.open_proposal_exists is True
        assert facts.open_proposal_id == proposal_id
        assert evaluate_gap_discussion_next_action(facts).kind == "review_proposal"

    def test_read_failure_on_proposal_table_degrades_without_raising(self, conn_factory, monkeypatch):
        with conn_factory() as conn:
            system_id = _make_system(conn)
            thread_id = _make_thread(conn, system_id)
            now = time.time()
            conn.execute(
                """INSERT INTO assistant_discussion_proposal
                       (system_id, thread_id, screen_id, target_kind, target_ref,
                        captured_target_revision_id, captured_target_digest, summary,
                        confirmed_points_json, unresolved_questions_json, assumptions_json,
                        evidence_refs_json, decision_method, intelligence_run_id, provider, model,
                        prompt_version, schema_version, created_by, created_at)
                   VALUES (?, ?, 'objective-map', 'product_gap', 'gap-1', NULL, '', '',
                           '[]', '[]', '[]', '[]', 'reasoning_llm', NULL, 'openai', 'gpt-5', 'v1', 'v1',
                           NULL, ?)""",
                (system_id, thread_id, now),
            )
            # Dropping the item table simulates any unexpected read failure
            # for the proposal fact group -- the row above ensures the
            # gather loop actually attempts (and fails) an item query rather
            # than short-circuiting on an empty proposal list.
            conn.execute("DROP TABLE assistant_discussion_proposal_item")
            facts = gather_gap_discussion_facts(
                conn, system_id, thread_id, target_state="current", self_operation_state="available",
            )
        assert facts.proposal_status_available is False
        result = evaluate_gap_discussion_next_action(facts)
        assert result.kind == "match"
        assert "proposal" in result.degraded_sections


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
