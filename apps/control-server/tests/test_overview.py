"""Tests for Issues #380-#384: the Overview decision cockpit projection.

Part 1 -- the pure derivations, without a database:
  * every row of the `decide_next_action` first-match table, in isolation,
    plus the row priorities that actually matter;
  * finding extraction, the same-cause and same-subject collapses, the fixed
    ranking, and the three distinct empty states;
  * the `new` / `ongoing` / `not_compared` status axis and the baseline it is
    measured against;
  * the loop rail's reached / current / future derivation.

Part 2 -- the API:
  * `GET /overview` on a bare System, on a System with a repository, and on a
    System that is receiving traces;
  * System isolation;
  * a degraded section not blanking the response.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Dict, List

import pytest
from fastapi.testclient import TestClient

from app.overview_projection import (
    ACTION_KEYS,
    FINDING_KINDS,
    FINDING_PROVENANCES,
    FINDING_SEVERITIES,
    INITIAL_FINDING_LIMIT,
    Finding,
    FindingInputs,
    NextActionFacts,
    PendingExperiment,
    PendingPublish,
    aggregate_provenance,
    build_loop_stages,
    classify_status,
    collect_findings,
    decide_next_action,
    findings_state,
    freshness_onset,
    select_findings,
)


# ---------------------------------------------------------------------------
# Part 1: the pure derivations
# ---------------------------------------------------------------------------


def _healthy() -> Dict[str, object]:
    """Facts that fall through every row to the last one (row 14)."""
    return dict(
        repository_configured=True,
        latest_snapshot_status="ready",
        ready_snapshot_exists=True,
        readiness_state="ready",
        workflow_state="W7",
        interview_session_id=7,
        connectivity_state="receiving",
        connectivity_freshness="receiving_now",
        undecided_experiment=None,
        undecided_experiment_count=0,
        pending_publish=None,
        completed_variant_run_exists=True,
        decided_experiment_exists=True,
        brief_available=True,
        runtime_available=True,
        workflow_available=True,
    )


def test_action_rule_rows_each_reachable():
    """Every row of the table produces its own action key, in isolation."""
    rows = [
        (1, dict(repository_configured=False), "prepare_repository"),
        (2, dict(ready_snapshot_exists=False, latest_snapshot_status="failed"), "prepare_repository"),
        (4, dict(readiness_state="blocked"), "resolve_understanding_blocker"),
        (5, dict(readiness_state="not_built"), "build_understanding"),
        (6, dict(readiness_state="needs_confirmation", workflow_state="W3"), "answer_interview_questions"),
        (7, dict(readiness_state="needs_confirmation"), "confirm_understanding"),
        (7, dict(readiness_state="recheck_required"), "confirm_understanding"),
        (8, dict(connectivity_state="no_signal"), "connect_sdk"),
        (9, dict(connectivity_state="smoke_only", connectivity_freshness="never_received"), "start_observation"),
        (10, dict(connectivity_freshness="stale"), "restore_observation"),
        (10, dict(connectivity_freshness="delayed"), "restore_observation"),
        (
            11,
            dict(undecided_experiment=PendingExperiment(experiment_id=4, count=2)),
            "record_experiment_decision",
        ),
        (12, dict(pending_publish=PendingPublish(patch_id=9)), "publish_change"),
        (
            13,
            dict(completed_variant_run_exists=False, decided_experiment_exists=False),
            "create_candidate",
        ),
        (14, {}, "start_next_cycle"),
    ]
    for expected_row, overrides, expected_key in rows:
        facts = NextActionFacts(**{**_healthy(), **overrides})
        action, state, _message = decide_next_action(facts)
        assert action is not None, (expected_row, overrides)
        assert action.key == expected_key, (expected_row, overrides, action.key)
        assert action.rule_row == expected_row, (overrides, action.rule_row)
        assert state in ("available", "complete")


def test_every_action_key_is_in_the_finite_vocabulary():
    """No rule row may invent a key the response schema cannot express."""
    for overrides in (
        dict(repository_configured=False),
        dict(ready_snapshot_exists=False, latest_snapshot_status="failed"),
        dict(readiness_state="blocked"),
        dict(readiness_state="not_built"),
        dict(workflow_state="W3"),
        dict(readiness_state="needs_confirmation"),
        dict(connectivity_state="no_signal"),
        dict(connectivity_freshness="never_received", connectivity_state="smoke_only"),
        dict(connectivity_freshness="stale"),
        dict(undecided_experiment=PendingExperiment(experiment_id=1)),
        dict(pending_publish=PendingPublish(patch_id=1)),
        dict(completed_variant_run_exists=False, decided_experiment_exists=False),
        {},
    ):
        action, _state, _msg = decide_next_action(NextActionFacts(**{**_healthy(), **overrides}))
        if action is not None:
            assert action.key in ACTION_KEYS


def test_action_is_always_zero_or_one():
    """The contract is a single value, never a list -- there is nothing to
    truncate and no second-best action to leak."""
    action, _state, _msg = decide_next_action(NextActionFacts(**_healthy()))
    assert action is None or isinstance(action.key, str)


def test_building_and_running_yield_no_action_but_a_waiting_message():
    """`waiting` carries no action on purpose: a disabled control the developer
    cannot use is explicitly forbidden (#383)."""
    for overrides in (
        dict(readiness_state="building"),
        dict(workflow_state="W1"),
    ):
        action, state, message = decide_next_action(
            NextActionFacts(**{**_healthy(), **overrides})
        )
        assert action is None
        assert state == "waiting"
        assert message

    action, state, message = decide_next_action(
        NextActionFacts(**{**_healthy(), "ready_snapshot_exists": False, "latest_snapshot_status": "indexing"})
    )
    assert action is None and state == "waiting" and message


def test_repository_row_beats_every_later_row():
    """A System with no repository is never told to record an evaluation
    decision, however many experiments happen to be sitting undecided."""
    facts = NextActionFacts(
        repository_configured=False,
        undecided_experiment=PendingExperiment(experiment_id=1, count=5),
        pending_publish=PendingPublish(patch_id=1),
        readiness_state="blocked",
    )
    action, _state, _msg = decide_next_action(facts)
    assert action.key == "prepare_repository"


def test_open_questions_outrank_confirmation():
    """`W3` sits above the confirmation row because it is exactly the state in
    which the understanding is not yet confirmable."""
    facts = NextActionFacts(
        **{**_healthy(), "workflow_state": "W3", "readiness_state": "needs_confirmation"}
    )
    action, _state, _msg = decide_next_action(facts)
    assert action.key == "answer_interview_questions"


def test_stale_reception_outranks_a_pending_decision():
    """A decision judged against observations that stopped updating is a
    decision made on stale evidence, so recovery comes first."""
    facts = NextActionFacts(
        **{
            **_healthy(),
            "connectivity_freshness": "stale",
            "undecided_experiment": PendingExperiment(experiment_id=2, count=3),
        }
    )
    action, _state, _msg = decide_next_action(facts)
    assert action.key == "restore_observation"


# --- fail-closed availability (review P1-1) ---------------------------------


def test_unavailable_brief_never_reads_as_not_built():
    """The whole point of the availability flags: an unreadable Brief must not
    produce 「システムを理解する」 for a system that is already understood."""
    facts = NextActionFacts(**{**_healthy(), "brief_available": False})
    action, state, message = decide_next_action(facts)
    assert action is None
    assert state == "unavailable"
    assert message


def test_unavailable_runtime_never_offers_a_connection_cta():
    """Rows 8-10 read connectivity. An unreadable Runtime section must not
    become 「まだ接続していません」 for a system receiving traces."""
    facts = NextActionFacts(**{**_healthy(), "runtime_available": False})
    action, state, _msg = decide_next_action(facts)
    assert action is None
    assert state == "unavailable"


def test_unavailable_workflow_never_guesses_a_cta():
    facts = NextActionFacts(**{**_healthy(), "workflow_available": False})
    action, state, _msg = decide_next_action(facts)
    assert action is None and state == "unavailable"


def test_availability_does_not_suppress_rows_that_do_not_need_it():
    """Fail-closed, not fail-blank: rows 1-2 read repository/snapshot facts
    only, so they still answer even when everything later is unreadable."""
    facts = NextActionFacts(
        repository_configured=False,
        brief_available=False,
        runtime_available=False,
        workflow_available=False,
    )
    action, state, _msg = decide_next_action(facts)
    assert action is not None
    assert action.key == "prepare_repository"
    assert state == "available"


# --- provenance (review P1-2) ------------------------------------------------


def test_developer_intent_survives_into_a_finding():
    """A Vision the developer wrote and confirmed must never be reported as the
    AI's guess. The Overview vocabulary is a superset of the Brief's precisely
    so this needs no translation."""
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(
                vision=_Claim("vision", "V", "conflicting", provenance="developer_intent"),
            ),
            revision_created_at=2.0,
        )
    )
    conflict = [f for f in findings if f.kind == "claim_conflict"][0]
    assert conflict.provenance == "developer_intent"


def test_ai_and_runtime_provenance_are_preserved_too():
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(
                core_capabilities=[
                    _Claim("core_capability", "C", "conflicting", provenance="ai_hypothesis"),
                ],
            ),
            revision_created_at=2.0,
            runtime_checks=({"runtime_check": "mismatch", "created_at": 1, "updated_at": 2},),
        )
    )
    by_kind = {f.kind: f for f in findings}
    assert by_kind["claim_conflict"].provenance == "ai_hypothesis"
    assert by_kind["runtime_mismatch"].provenance == "runtime_observation"


def test_pending_decision_finding_is_a_human_judgement_record():
    findings = collect_findings(
        FindingInputs(undecided_experiment_count=1, undecided_experiment_id=3)
    )
    assert findings[0].provenance == "developer_decision"


def test_change_finding_inherits_the_changed_claims_provenance():
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(
                vision=_Claim("vision", "V", "confirmed", provenance="developer_intent"),
                changes=[_Change("vision", "Vision", "V", "説明が変わりました。")],
                confirmed_at=1.0,
            ),
            revision_created_at=2.0,
        )
    )
    changed = [f for f in findings if f.kind == "understanding_changed"][0]
    assert changed.provenance == "developer_intent"


def test_change_finding_reports_mixed_when_sources_disagree():
    """Aggregation never picks a winner: naming one source would imply the
    others agreed with it."""
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(
                core_capabilities=[
                    _Claim("core_capability", "A", "confirmed", provenance="implementation_fact"),
                    _Claim("core_capability", "B", "confirmed", provenance="runtime_observation"),
                ],
                changes=[
                    _Change("core_capabilities", "主要機能", "A", "説明が変わりました。"),
                    _Change("core_capabilities", "主要機能", "B", "説明が変わりました。"),
                ],
                confirmed_at=1.0,
            ),
            revision_created_at=2.0,
        )
    )
    changed = [f for f in findings if f.kind == "understanding_changed"][0]
    assert changed.provenance == "mixed"


def test_aggregate_provenance_rules():
    assert aggregate_provenance(["developer_intent"]) == "developer_intent"
    assert aggregate_provenance(["ai_hypothesis", "ai_hypothesis"]) == "ai_hypothesis"
    assert aggregate_provenance(["ai_hypothesis", "runtime_observation"]) == "mixed"
    # Every changed claim was removed, so none survives to carry a provenance;
    # the removal is only observable by comparing two code readings.
    assert aggregate_provenance([]) == "implementation_fact"


def test_every_finding_provenance_is_in_the_finite_vocabulary():
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(
                vision=_Claim("vision", "V", "conflicting", provenance="developer_intent"),
                core_capabilities=[_Claim("core_capability", "C", "ai_hypothesis")],
                changes=[_Change("vision", "Vision", "V", "説明が変わりました。")],
                confirmed_at=1.0,
            ),
            revision_created_at=2.0,
            blocking_failures=({"process_kind": "understanding_build", "finished_at": 1.0},),
            snapshot_stale=True,
            connectivity_freshness="stale",
            last_real_trace_at=1.0,
            undecided_experiment_count=1,
            runtime_checks=({"runtime_check": "mismatch", "created_at": 1, "updated_at": 2},),
        )
    )
    assert findings
    for finding in findings:
        assert finding.provenance in FINDING_PROVENANCES


# --- publish identity (review P1-3) ------------------------------------------


def test_publish_row_is_per_patch_not_per_system():
    """A patch with no completed publish job of its own still needs publishing,
    however many earlier publishes succeeded."""
    facts = NextActionFacts(
        **{**_healthy(), "pending_publish": PendingPublish(patch_id=42, count=1)}
    )
    action, _state, _msg = decide_next_action(facts)
    assert action.key == "publish_change"
    assert action.target.params == {"patch": "42"}


def test_no_pending_patch_means_no_publish_cta():
    """Every applied patch published -> the cycle is closed, and the CTA moves
    on instead of asking for a publish that has already happened."""
    action, state, _msg = decide_next_action(NextActionFacts(**_healthy()))
    assert action.key == "start_next_cycle"
    assert state == "complete"


def test_pending_decision_outranks_pending_publish():
    facts = NextActionFacts(
        **{
            **_healthy(),
            "undecided_experiment": PendingExperiment(experiment_id=1),
            "pending_publish": PendingPublish(patch_id=2),
        }
    )
    action, _state, _msg = decide_next_action(facts)
    assert action.key == "record_experiment_decision"


# --- experiment deep link (review P2-7) --------------------------------------


def test_decision_cta_carries_the_target_experiment():
    facts = NextActionFacts(
        **{
            **_healthy(),
            "undecided_experiment": PendingExperiment(experiment_id=17, count=3),
        }
    )
    action, _state, _msg = decide_next_action(facts)
    assert action.target.route == "/experiments"
    assert action.target.params == {"experiment": "17"}
    assert "3 件" in action.reason


# --- connectivity onset (review P2-6) ----------------------------------------


def test_freshness_onset_is_the_threshold_crossing_not_the_last_trace():
    assert freshness_onset(
        "delayed", last_real_trace_at=1000.0, delayed_after_seconds=900, stale_after_seconds=86400
    ) == 1900.0
    assert freshness_onset(
        "stale", last_real_trace_at=1000.0, delayed_after_seconds=900, stale_after_seconds=86400
    ) == 87400.0
    assert freshness_onset(
        "receiving_now", last_real_trace_at=None, delayed_after_seconds=900, stale_after_seconds=1
    ) is None


def test_an_outage_that_began_after_the_baseline_is_new():
    """The last trace predates the confirmation, but the outage does not: it is
    the threshold crossing that dates the finding."""
    inputs = FindingInputs(
        brief=_Brief(confirmed_at=1500.0),
        connectivity_freshness="delayed",
        last_real_trace_at=1000.0,
        delayed_after_seconds=900,
        stale_after_seconds=86400,
    )
    findings = collect_findings(inputs)
    lost = [f for f in findings if f.kind == "connectivity_lost"][0]
    assert lost.first_seen == 1900.0
    assert classify_status(lost.first_seen, 1500.0) == "new"
    # Dated from the last trace instead, the same outage would have read
    # 「前回の確認時点から継続」.
    assert classify_status(1000.0, 1500.0) == "ongoing"


def test_an_outage_that_began_before_the_baseline_is_ongoing():
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(confirmed_at=5000.0),
            connectivity_freshness="stale",
            last_real_trace_at=1000.0,
            delayed_after_seconds=900,
            stale_after_seconds=2000,
        )
    )
    lost = [f for f in findings if f.kind == "connectivity_lost"][0]
    assert lost.first_seen == 3000.0
    assert classify_status(lost.first_seen, 5000.0) == "ongoing"


def test_delayed_and_stale_share_one_finding_identity():
    """The subject is the same outage, so the id is stable across the
    escalation; the summary and the timestamp are what change."""
    def _lost(freshness: str) -> Finding:
        return [
            f
            for f in collect_findings(
                FindingInputs(
                    connectivity_freshness=freshness,
                    last_real_trace_at=1000.0,
                    delayed_after_seconds=900,
                    stale_after_seconds=2000,
                )
            )
            if f.kind == "connectivity_lost"
        ][0]

    delayed, stale = _lost("delayed"), _lost("stale")
    assert delayed.id == stale.id
    assert delayed.summary != stale.summary
    assert stale.last_updated > delayed.last_updated


def test_action_always_explains_itself():
    """#383: 操作名だけでなく、選定理由・完了条件・完了後の価値を示す。"""
    for overrides in (
        dict(repository_configured=False),
        dict(readiness_state="not_built"),
        dict(undecided_experiment=PendingExperiment(experiment_id=1)),
        {},
    ):
        action, _state, _msg = decide_next_action(
            NextActionFacts(**{**_healthy(), **overrides})
        )
        assert action.reason
        assert action.completion_condition
        assert action.value
        assert action.target.route.startswith("/")


# --- findings ---------------------------------------------------------------


class _Claim:
    def __init__(self, kind, name, confirmation, provenance="ai_hypothesis", evidence=None):
        self.kind = kind
        self.name = name
        self.confirmation = confirmation
        self.provenance = provenance
        self.evidence = evidence or []


class _Change:
    def __init__(self, section, section_label, name, detail):
        self.section = section
        self.section_label = section_label
        self.name = name
        self.detail = detail


class _Brief:
    def __init__(
        self,
        *,
        vision=None,
        system_purpose=None,
        core_capabilities=None,
        changes=None,
        readiness_reasons=None,
        confirmed_at=None,
        snapshot_id=3,
        revision_id=9,
    ):
        self.vision = vision
        self.system_purpose = system_purpose or []
        self.core_capabilities = core_capabilities or []
        self.changes_since_confirmation = changes or []
        self.readiness_reasons = readiness_reasons or []
        self.confirmed_at = confirmed_at
        self.snapshot_id = snapshot_id
        self.revision_id = revision_id


def test_finding_kinds_are_all_reachable():
    """Every kind in the finite vocabulary has an extractor that can produce
    it -- an unreachable kind is a label nothing can ever explain."""
    produced: set = set()

    class _Reason:
        code = "capability_composition_stale"

    brief = _Brief(
        vision=_Claim("vision", "V", "conflicting"),
        core_capabilities=[_Claim("core_capability", "C", "ai_hypothesis")],
        changes=[_Change("core_capabilities", "主要機能", "C", "説明が変わりました。")],
        readiness_reasons=[_Reason()],
        confirmed_at=100.0,
    )
    produced |= {
        f.kind
        for f in collect_findings(
            FindingInputs(
                brief=brief,
                revision_created_at=200.0,
                blocking_failures=(
                    {"process_kind": "understanding_build", "finished_at": 150.0, "error": "boom"},
                ),
                snapshot_stale=True,
                latest_ready_snapshot_at=180.0,
                runtime_checks=(
                    {"runtime_check": "mismatch", "created_at": 1.0, "updated_at": 2.0},
                    {"runtime_check": "unobserved", "created_at": 1.0, "updated_at": 2.0},
                ),
                connectivity_freshness="stale",
                last_real_trace_at=50.0,
                undecided_experiment_count=1,
                undecided_experiment_at=190.0,
                undecided_experiment_id=4,
                session_id=1,
            )
        )
    }
    produced |= {
        f.kind
        for f in collect_findings(
            FindingInputs(completed_variant_count=2, completed_variant_at=10.0)
        )
    }
    assert produced == set(FINDING_KINDS)


def test_findings_are_capped_at_three_and_ranked_by_severity():
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(
                vision=_Claim("vision", "V", "conflicting"),
                core_capabilities=[_Claim("core_capability", "C", "ai_hypothesis")],
                changes=[_Change("system_purpose", "System Purpose", "P", "説明が変わりました。")],
                confirmed_at=100.0,
            ),
            revision_created_at=200.0,
            runtime_checks=(
                {"runtime_check": "mismatch", "created_at": 1.0, "updated_at": 2.0},
            ),
            connectivity_freshness="stale",
            last_real_trace_at=50.0,
            undecided_experiment_count=1,
            undecided_experiment_at=190.0,
            session_id=1,
        )
    )
    ordered, statuses = select_findings(findings, baseline_at=100.0)
    assert len(ordered) > INITIAL_FINDING_LIMIT
    assert len(statuses) == len(ordered)
    # The blocker is first; the informative entries never outrank it.
    assert ordered[0].severity == "blocker"
    ranks = [FINDING_SEVERITIES.index(f.severity) for f in ordered]
    assert ranks == sorted(ranks)
    assert ordered[:INITIAL_FINDING_LIMIT] == ordered[:3]


def test_same_subject_collapses_across_kinds():
    """A claim that is both conflicting and unconfirmed must not occupy two of
    the three slots."""
    conflicting = _Claim("core_capability", "C", "conflicting")
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(core_capabilities=[conflicting], confirmed_at=1.0),
            revision_created_at=2.0,
            session_id=1,
        )
    )
    ordered, _statuses = select_findings(findings, baseline_at=1.0)
    subjects = [f.subject_key for f in ordered]
    assert len(subjects) == len(set(subjects))


def test_same_cause_aggregates_rather_than_repeating():
    """Five changed capabilities are one finding with a count, not five."""
    changes = [
        _Change("core_capabilities", "主要機能", f"C{i}", "説明が変わりました。")
        for i in range(5)
    ]
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(changes=changes, confirmed_at=1.0),
            revision_created_at=2.0,
            session_id=1,
        )
    )
    changed = [f for f in findings if f.kind == "understanding_changed"]
    assert len(changed) == 1
    assert changed[0].occurrence_count == 5


def test_finding_id_is_stable_across_rebuilds():
    """Ids come from the cause, not from a row id a rebuild renumbers -- a
    finding whose id changed every rebuild could never read as 継続."""
    a = collect_findings(
        FindingInputs(
            brief=_Brief(core_capabilities=[_Claim("core_capability", "C", "conflicting")]),
            revision_created_at=1.0,
        )
    )
    b = collect_findings(
        FindingInputs(
            brief=_Brief(
                core_capabilities=[_Claim("core_capability", "C", "conflicting")],
                revision_id=999,
            ),
            revision_created_at=5.0,
        )
    )
    assert [f.id for f in a] == [f.id for f in b]


def test_every_finding_states_its_decision_impact_and_provenance():
    """#382: 「何が起きたか」だけでなく「なぜ判断に重要か」が分かる。"""
    findings = collect_findings(
        FindingInputs(
            brief=_Brief(
                vision=_Claim("vision", "V", "conflicting"),
                core_capabilities=[_Claim("core_capability", "C", "ai_hypothesis")],
                changes=[_Change("vision", "Vision", "V", "説明が変わりました。")],
                confirmed_at=1.0,
            ),
            revision_created_at=2.0,
            connectivity_freshness="stale",
            snapshot_stale=True,
            undecided_experiment_count=1,
            runtime_checks=({"runtime_check": "mismatch", "created_at": 1, "updated_at": 2},),
            session_id=1,
        )
    )
    assert findings
    for finding in findings:
        assert finding.summary
        assert finding.decision_impact
        assert finding.provenance
        assert finding.kind in FINDING_KINDS


def test_status_distinguishes_new_ongoing_and_not_compared():
    assert classify_status(200.0, 100.0) == "new"
    assert classify_status(50.0, 100.0) == "ongoing"
    assert classify_status(None, 100.0) == "ongoing"
    # No baseline at all: newness is not knowable, and saying 「継続」 would be
    # a guess about a comparison that never happened.
    assert classify_status(200.0, None) == "not_compared"
    assert classify_status(None, None) == "not_compared"


def test_empty_states_are_three_different_answers():
    assert findings_state([], baseline_at=100.0) == "no_findings"
    assert findings_state([], baseline_at=None) == "not_compared"
    one = Finding(
        kind="snapshot_stale",
        severity="material_change",
        summary="s",
        decision_impact="d",
        provenance="implementation_fact",
        dedupe_key="snapshot_stale",
    )
    assert findings_state([one], baseline_at=100.0) == "has_findings"


def test_ranking_is_total_and_reproducible():
    """The same facts always yield the same order -- no set iteration order,
    no timestamp tie that can flip between two requests."""
    inputs = FindingInputs(
        brief=_Brief(
            vision=_Claim("vision", "V", "conflicting"),
            system_purpose=[_Claim("system_purpose", "P", "conflicting")],
            confirmed_at=1.0,
        ),
        revision_created_at=2.0,
        session_id=1,
    )
    first, _ = select_findings(collect_findings(inputs), baseline_at=1.0)
    second, _ = select_findings(collect_findings(inputs), baseline_at=1.0)
    assert [f.id for f in first] == [f.id for f in second]


def test_never_received_is_not_reported_as_a_regression():
    """`never_received` is the setup state, not 「受信が止まった」 -- reporting
    it as a loss would tell a brand-new System that something broke."""
    findings = collect_findings(FindingInputs(connectivity_freshness="never_received"))
    assert not [f for f in findings if f.kind == "connectivity_lost"]


# --- the loop rail ----------------------------------------------------------


class _Phase:
    def __init__(self, phase, complete):
        self.phase = phase
        self.complete = complete


def test_loop_stages_mark_reached_current_and_future():
    phases = [
        _Phase("setup", True),
        _Phase("preparation", True),
        _Phase("instrumentation", False),
        _Phase("observation", False),
        _Phase("evaluation", False),
        _Phase("publish", False),
    ]
    stages = build_loop_stages("instrumentation", phases)
    by_stage = {s.stage: s for s in stages}
    assert by_stage["setup"].status == "reached"
    assert by_stage["preparation"].status == "reached"
    assert by_stage["instrumentation"].status == "current"
    assert by_stage["observation"].status == "future"
    # Only the current stage names the next semantic milestone.
    assert by_stage["instrumentation"].next_milestone
    assert by_stage["observation"].next_milestone == ""
    assert all(s.label and s.meaning for s in stages)


# ---------------------------------------------------------------------------
# Part 2: the API
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-overview-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id):
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _init_repo(tmp_path, name="repo"):
    repo = str(tmp_path / name)
    os.makedirs(repo)
    subprocess.run(["git", "init", repo], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "T"], check=True, capture_output=True)
    with open(os.path.join(repo, "a.py"), "w") as f:
        f.write("def a():\n    return 1\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", "init"], check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def _insert_snapshot(system_id, repo_path, commit_sha):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, ?, ?, 'ready', ?, ?)""",
            (system_id, repo_path, commit_sha, now, now),
        )
        return cur.lastrowid


def _applied_patch(system_id, snapshot_id, commit_sha, applied_at):
    """An applied probe patch, with the minimum lineage its FKs require."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run_id = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model, prompt_version,
                    schema_version, decision_method, status, started_at, completed_at)
               VALUES (?, ?, 'probe_plan', 'mock', 'mock', 'v1', 'v1', 'reasoning_llm',
                       'completed', ?, ?)""",
            (system_id, snapshot_id, now, now),
        ).lastrowid
        plan_id = conn.execute(
            """INSERT INTO probe_plans
                   (system_id, snapshot_id, intelligence_run_id, feature_id, status,
                    created_at, updated_at)
               VALUES (?, ?, ?, 'f', 'approved', ?, ?)""",
            (system_id, snapshot_id, run_id, now, now),
        ).lastrowid
        return conn.execute(
            """INSERT INTO probe_patches
                   (plan_id, system_id, snapshot_id, commit_sha, apply_status,
                    applied_at, created_at)
               VALUES (?, ?, ?, ?, 'applied', ?, ?)""",
            (plan_id, system_id, snapshot_id, commit_sha, applied_at, now),
        ).lastrowid


def _github_connection(system_id):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        return conn.execute(
            """INSERT INTO github_connections
                   (system_id, api_base_url, web_base_url, owner, repo, clone_url,
                    installation_id, default_branch, status, created_at, updated_at)
               VALUES (?, 'https://api.github.com', 'https://github.com', 'o', 'r',
                       'https://github.com/o/r.git', 1, 'main', 'connected', ?, ?)""",
            (system_id, str(now), str(now)),
        ).lastrowid


def _publish_job(system_id, connection_id, patch_id, snapshot_id, status):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO publish_jobs
                   (system_id, connection_id, patch_id, snapshot_id, base_branch,
                    status, created_at, updated_at, completed_at)
               VALUES (?, ?, ?, ?, 'main', ?, ?, ?, ?)""",
            (system_id, connection_id, patch_id, snapshot_id, status, now, now, now),
        )


def test_overview_on_a_bare_system(admin_client):
    """The very first state: no repository at all. Every section is present
    and honest -- the screen never renders a placeholder Vision."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Bare")

    r = admin_client.get("/overview", headers=_headers(token, system_id))
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["system_id"] == system_id
    assert body["next_action"]["key"] == "prepare_repository"
    assert body["next_action_state"] == "available"
    assert body["next_action"]["reason"]
    assert body["next_action"]["completion_condition"]
    assert body["next_action"]["value"]
    # No understanding, so no Vision and no comparison baseline.
    assert body["brief"]["vision"] is None
    assert body["brief"]["readiness_state"] == "not_built"
    assert body["findings_state"] == "not_compared"
    assert body["findings_baseline_at"] is None
    assert body["findings_baseline_label"]
    assert body["user_phase"] == "setup"
    assert [s["stage"] for s in body["loop_stages"]] == [
        "setup", "preparation", "instrumentation", "observation", "evaluation", "publish",
    ]
    assert body["runtime"]["state"] == "no_signal"
    assert body["runtime"]["freshness"] == "never_received"
    assert body["degraded_sections"] == []


def test_overview_reports_ready_snapshot_and_understanding_next(admin_client, tmp_path):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Repo")
    repo, sha = _init_repo(tmp_path, "repo-overview")
    r = admin_client.put(
        "/repository",
        json={"repo_path": repo},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 200, r.text
    snapshot_id = _insert_snapshot(system_id, repo, sha)

    r = admin_client.get("/overview", headers=_headers(token, system_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["latest_ready_snapshot_id"] == snapshot_id
    # Repository done, understanding not built -> the understanding row.
    assert body["next_action"]["key"] == "build_understanding"
    assert body["next_action"]["target"]["route"] == "/interview"


def test_overview_findings_are_capped_at_three(admin_client, tmp_path):
    """`findings_initial_count` is a cap, never a pad."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Cap")
    r = admin_client.get("/overview", headers=_headers(token, system_id))
    body = r.json()
    assert body["findings_initial_count"] <= INITIAL_FINDING_LIMIT
    assert body["findings_initial_count"] == min(len(body["findings"]), 3)


def test_overview_runtime_reflects_received_traces(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Live")
    now = time.time()
    r = admin_client.post(
        "/traces",
        json={
            "trace_id": "t-1",
            "component_id": "summarize",
            "mode": "trace",
            "input": {"a": 1},
            "output": "ok",
            "duration_ms": 3.0,
            "timestamp": now,
        },
        headers=_headers(token, system_id),
    )
    assert r.status_code in (200, 201), r.text

    body = admin_client.get("/overview", headers=_headers(token, system_id)).json()
    runtime = body["runtime"]
    assert runtime["state"] == "receiving"
    assert runtime["freshness"] == "receiving_now"
    assert runtime["freshness_label"]
    assert runtime["component_count"] == 1
    assert runtime["real_trace_count_24h"] == 1
    # The cumulative total is still reported, but as a separate field from the
    # windowed counts -- 「累積到達」 and 「いま動いているか」 are two facts.
    assert runtime["total_trace_count"] == 1
    assert runtime["window_seconds"] > 0
    # Reception exists but the repository does not, so the repository row still
    # wins: a later row never jumps the queue because a count moved.
    assert body["next_action"]["key"] == "prepare_repository"


def test_overview_is_system_scoped(admin_client):
    token = _login(admin_client)
    a = _create_system(admin_client, token, "A")
    b = _create_system(admin_client, token, "B")
    now = time.time()
    admin_client.post(
        "/traces",
        json={
            "trace_id": "t-a",
            "component_id": "c",
            "mode": "trace",
            "output": "ok",
            "timestamp": now,
        },
        headers=_headers(token, a),
    )
    body_a = admin_client.get("/overview", headers=_headers(token, a)).json()
    body_b = admin_client.get("/overview", headers=_headers(token, b)).json()
    assert body_a["runtime"]["total_trace_count"] == 1
    assert body_b["runtime"]["total_trace_count"] == 0
    assert body_b["runtime"]["state"] == "no_signal"


def test_a_failed_section_degrades_alone(admin_client, tmp_path, monkeypatch):
    """#384: 部分失敗で画面全体が空にならない。"""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Degraded")
    # A repository and a ready snapshot, so rows 1-2 do not answer first and
    # the evaluation actually reaches the Brief-dependent rows.
    repo, sha = _init_repo(tmp_path, "repo-degraded")
    admin_client.put(
        "/repository", json={"repo_path": repo}, headers=_headers(token, system_id)
    )
    _insert_snapshot(system_id, repo, sha)

    from app import overview_projection

    def _boom(*_args, **_kwargs):
        raise RuntimeError("brief unavailable")

    monkeypatch.setattr(
        overview_projection.understanding_brief, "build_understanding_brief", _boom
    )
    body = admin_client.get("/overview", headers=_headers(token, system_id)).json()
    assert "brief" in body["degraded_sections"]
    assert body["brief"] is None
    # The independent sections still render -- the screen does not go blank.
    assert body["runtime"] is not None
    assert body["loop_stages"]
    assert body["degraded_detail"]["brief"]
    # ...but the next action is NOT one of them. Every rule from row 3 on reads
    # the Brief, so guessing one here would be the「取得できない」と「まだして
    # いない」 conflation this whole epic exists to remove. An earlier version
    # of this test asserted `next_action is not None`, which locked the wrong
    # behaviour in.
    assert body["next_action"] is None
    assert body["next_action_state"] == "unavailable"
    assert body["next_action_message"]


def test_a_failed_runtime_section_does_not_offer_a_connection_cta(
    admin_client, tmp_path, monkeypatch
):
    """#383: API部分失敗時は推測でCTAを出さず、安全なdegraded状態にする。"""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "DegradedRuntime")
    repo, sha = _init_repo(tmp_path, "repo-degraded-runtime")
    admin_client.put(
        "/repository", json={"repo_path": repo}, headers=_headers(token, system_id)
    )
    _insert_snapshot(system_id, repo, sha)

    from app import overview_projection

    def _boom(*_args, **_kwargs):
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(overview_projection, "_runtime_health", _boom)
    body = admin_client.get("/overview", headers=_headers(token, system_id)).json()
    assert "runtime" in body["degraded_sections"]
    assert body["runtime"] is None
    # The Brief still renders, and the understanding rows can still answer --
    # this System genuinely has no understanding yet, and that fact was read
    # successfully. Fail-closed removes guesses, not everything.
    assert body["brief"] is not None
    assert body["next_action"]["key"] == "build_understanding"


def test_pending_publish_is_scoped_to_the_patch_not_the_system(admin_client, tmp_path):
    """A publish that succeeded for cycle A must not make cycle B look
    published.

    The resolver is exercised directly rather than through the rule table: the
    table's ordering is already covered by the pure tests above, and reaching
    row 12 through the API would require a fully understood, fully observed,
    fully decided System -- which would test the setup, not the identity.
    """
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Publish")
    repo, sha = _init_repo(tmp_path, "repo-publish")
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    connection_id = _github_connection(system_id)
    now = time.time()

    from app.db import get_conn
    from app.overview_projection import resolve_pending_publish

    def _pending():
        with get_conn() as conn:
            return resolve_pending_publish(conn, system_id)

    assert _pending() is None

    patch_a = _applied_patch(system_id, snapshot_id, sha, now - 100)
    assert _pending().patch_id == patch_a
    assert _pending().count == 1

    _publish_job(system_id, connection_id, patch_a, snapshot_id, "completed")
    assert _pending() is None

    # Cycle B: a new applied patch. The earlier success must not cover it --
    # the pre-fix check was "has any publish job ever succeeded", which made
    # this None forever.
    patch_b = _applied_patch(system_id, snapshot_id, sha, now)
    assert _pending().patch_id == patch_b

    _publish_job(system_id, connection_id, patch_b, snapshot_id, "completed")
    assert _pending() is None


def test_pending_publish_ignores_failed_and_cancelled_jobs(admin_client, tmp_path):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "PublishFailed")
    repo, sha = _init_repo(tmp_path, "repo-publish-failed")
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    connection_id = _github_connection(system_id)
    patch_id = _applied_patch(system_id, snapshot_id, sha, time.time())
    for status in ("failed", "cancelled", "retryable_failed", "pending"):
        _publish_job(system_id, connection_id, patch_id, snapshot_id, status)

    from app.db import get_conn
    from app.overview_projection import resolve_pending_publish

    with get_conn() as conn:
        pending = resolve_pending_publish(conn, system_id)
    assert pending is not None and pending.patch_id == patch_id


def test_pending_publish_never_looks_across_systems(admin_client, tmp_path):
    token = _login(admin_client)
    other = _create_system(admin_client, token, "PublishOther")
    system_id = _create_system(admin_client, token, "PublishMine")
    repo, sha = _init_repo(tmp_path, "repo-publish-scope")
    other_snapshot = _insert_snapshot(other, repo, sha)
    snapshot_id = _insert_snapshot(system_id, repo, sha)

    other_patch = _applied_patch(other, other_snapshot, sha, time.time())
    _publish_job(
        other, _github_connection(other), other_patch, other_snapshot, "completed"
    )
    patch_id = _applied_patch(system_id, snapshot_id, sha, time.time())

    from app.db import get_conn
    from app.overview_projection import resolve_pending_publish

    with get_conn() as conn:
        assert resolve_pending_publish(conn, system_id).patch_id == patch_id
        # The other System's own pending state is unaffected in both
        # directions: its patch IS published, so it has nothing pending.
        assert resolve_pending_publish(conn, other) is None


def test_pending_publish_ordering_is_deterministic(admin_client, tmp_path):
    """Several unpublished patches -> the CTA points at the same one on every
    request (newest applied, id as a total tiebreak)."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "PublishOrder")
    repo, sha = _init_repo(tmp_path, "repo-publish-order")
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    now = time.time()
    _applied_patch(system_id, snapshot_id, sha, now - 200)
    newest = _applied_patch(system_id, snapshot_id, sha, now)
    _applied_patch(system_id, snapshot_id, sha, now - 100)

    from app.db import get_conn
    from app.overview_projection import resolve_pending_publish

    with get_conn() as conn:
        first = resolve_pending_publish(conn, system_id)
        second = resolve_pending_publish(conn, system_id)
    assert first.patch_id == newest
    assert first.patch_id == second.patch_id
    assert first.count == 3


def test_snapshot_freshness_and_context_are_server_decided(admin_client, tmp_path):
    """#381: the Dashboard must not compare two snapshot ids itself."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Context")
    body = admin_client.get("/overview", headers=_headers(token, system_id)).json()
    # No pinned snapshot: 「最新ではない」 would be a claim we cannot make.
    assert body["snapshot_freshness"] == "unavailable"
    assert body["understanding_revision_id"] is None
    assert body["understanding_confirmed_at"] is None


def test_runtime_reports_components_not_a_fake_capability_coverage(admin_client):
    """#384: 異なる entity を横並びにして coverage に見せない。"""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Coverage")
    now = time.time()
    for index in range(2):
        admin_client.post(
            "/traces",
            json={
                "trace_id": f"t-{index}",
                "component_id": f"c-{index}",
                "mode": "trace",
                "output": "ok",
                "timestamp": now,
            },
            headers=_headers(token, system_id),
        )
    runtime = admin_client.get("/overview", headers=_headers(token, system_id)).json()[
        "runtime"
    ]
    assert runtime["observed_component_count"] == 2
    assert runtime["known_component_count"] == 2
    # Both numbers above are components. Capability coverage is not derivable
    # today and says so rather than dividing components by Capabilities.
    assert runtime["capability_coverage_state"] == "not_computed"
    assert runtime["observed_capability_count"] is None
    assert runtime["observed_component_count"] <= runtime["known_component_count"]


def test_overview_writes_nothing(admin_client, tmp_path):
    """Opening the Overview is not a human decision, so it must not persist a
    workflow checkpoint, a backward request, or any acknowledgement (#382)."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "ReadOnly")
    repo, sha = _init_repo(tmp_path, "repo-readonly")
    admin_client.put(
        "/repository", json={"repo_path": repo}, headers=_headers(token, system_id)
    )
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    r = admin_client.post(
        "/interview/sessions",
        json={"snapshot_id": snapshot_id, "title": "t"},
        headers=_headers(token, system_id),
    )
    assert r.status_code in (200, 201), r.text

    from app.db import get_conn

    def _counts() -> List[int]:
        with get_conn() as conn:
            return [
                conn.execute("SELECT COUNT(*) FROM interview_workflow_checkpoint").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM interview_back_request").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM interview_back_acknowledgement").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM interview_diff_review").fetchone()[0],
            ]

    before = _counts()
    admin_client.get("/overview", headers=_headers(token, system_id))
    admin_client.get("/overview", headers=_headers(token, system_id))
    assert _counts() == before
