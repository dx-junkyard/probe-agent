"""Tests for Issue #349: the state-driven System Interview workflow.

Covers `app/interview_workflow.py` (the canonical two-stage evaluation and
the process-run lifecycle) and `app/routes/interview_workflow.py` (the API
that persists facts A-D), against `docs/system-interview-workflow-ux.md`.

Part 1 -- the pure engine:
  * every one of the 13 first-match rule rows, in isolation;
  * the row priorities that actually matter (a blocking failure never hides
    work the developer can still do earlier in the flow);
  * the unordered states `W0-A` / `W0-B` / `W1` never being held back and
    never moving `reached_state`;
  * the backward hold, and the per-request acknowledgement.

Part 2 -- persistence and API:
  * a running record producing `W1` and surviving a reload;
  * a failed run landing on its finite target state (`W2` / `W4` / `W5`);
  * a diff review bound to the diff it reviewed;
  * suspend (`status=closed`) beating the failure rows without resolving the
    failure, and resume bringing it back;
  * the approval-set/diff correspondence keeping `W5` from falling to `W7`.

Part 3 -- the spec's §6 walkthrough scenarios end to end.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Dict

import pytest
from fastapi.testclient import TestClient

from app.interview_workflow import (
    ORDERED_STATES,
    PRIMARY_ACTION_BY_STATE,
    UNORDERED_STATES,
    WorkflowFacts,
    apply_backward_hold,
    evaluate_candidate_state,
    state_rank,
    terminal_kind_for,
)


# ---------------------------------------------------------------------------
# Part 1: the pure engine
# ---------------------------------------------------------------------------


def _ready() -> Dict[str, object]:
    """Facts that reach the last row (W7): everything done, nothing pending."""
    return dict(
        has_snapshot=True,
        has_session=True,
        session_closed=False,
        running_process_kinds=(),
        blocking_failure_states=frozenset(),
        understanding_unconfirmed=False,
        open_required_questions=0,
        outstanding_alignment_items=0,
        proposals_needing_review=0,
        proposals_generatable=False,
        approved_proposal_count=0,
        diff_matches_approval_set=False,
        diff_review_complete=False,
    )


def test_rule_row_1_no_snapshot():
    facts = WorkflowFacts(**{**_ready(), "has_snapshot": False})
    assert evaluate_candidate_state(facts) == ("W0-A", 1)


def test_rule_row_2_no_session():
    facts = WorkflowFacts(**{**_ready(), "has_session": False})
    assert evaluate_candidate_state(facts) == ("W0-B", 2)


def test_rule_row_3_closed_session():
    facts = WorkflowFacts(**{**_ready(), "session_closed": True})
    assert evaluate_candidate_state(facts) == ("W7", 3)


def test_rule_row_4_running_process():
    facts = WorkflowFacts(
        **{**_ready(), "running_process_kinds": ("alignment_build",)}
    )
    assert evaluate_candidate_state(facts) == ("W1", 4)


def test_rule_row_5_blocking_failure_targets_w2():
    facts = WorkflowFacts(
        **{**_ready(), "blocking_failure_states": frozenset({"W2"})}
    )
    assert evaluate_candidate_state(facts) == ("W2", 5)


def test_rule_row_6_unconfirmed_understanding():
    facts = WorkflowFacts(**{**_ready(), "understanding_unconfirmed": True})
    assert evaluate_candidate_state(facts) == ("W2", 6)


def test_rule_row_7_open_question():
    facts = WorkflowFacts(**{**_ready(), "open_required_questions": 1})
    assert evaluate_candidate_state(facts) == ("W3", 7)


def test_rule_row_8_blocking_failure_targets_w4():
    facts = WorkflowFacts(
        **{**_ready(), "blocking_failure_states": frozenset({"W4"})}
    )
    assert evaluate_candidate_state(facts) == ("W4", 8)


def test_rule_row_9_outstanding_alignment_items():
    facts = WorkflowFacts(**{**_ready(), "outstanding_alignment_items": 2})
    assert evaluate_candidate_state(facts) == ("W4", 9)


def test_rule_row_10_blocking_failure_targets_w5():
    facts = WorkflowFacts(
        **{**_ready(), "blocking_failure_states": frozenset({"W5"})}
    )
    assert evaluate_candidate_state(facts) == ("W5", 10)


@pytest.mark.parametrize(
    "overrides",
    [
        {"proposals_needing_review": 1},
        {"proposals_generatable": True},
        # Approved but the diff does not correspond to the current approval
        # set -- the spec's third W5 condition (§2.3 W5 開始条件).
        {"approved_proposal_count": 1, "diff_matches_approval_set": False},
    ],
)
def test_rule_row_11_proposal_review(overrides):
    facts = WorkflowFacts(**{**_ready(), **overrides})
    assert evaluate_candidate_state(facts) == ("W5", 11)


def test_rule_row_12_diff_awaiting_review():
    facts = WorkflowFacts(
        **{
            **_ready(),
            "approved_proposal_count": 1,
            "diff_matches_approval_set": True,
            "diff_review_complete": False,
        }
    )
    assert evaluate_candidate_state(facts) == ("W6", 12)


def test_rule_row_13_fallthrough():
    facts = WorkflowFacts(**_ready())
    assert evaluate_candidate_state(facts) == ("W7", 13)


def test_closed_session_wins_over_running_and_failure_rows():
    """Row 3 above rows 4/5/8/10: a suspend must be able to move the display
    to W7 even while a blocking failure is unresolved (spec §5.3-6)."""
    facts = WorkflowFacts(
        **{
            **_ready(),
            "session_closed": True,
            "running_process_kinds": ("understanding_build",),
            "blocking_failure_states": frozenset({"W2", "W4", "W5"}),
        }
    )
    assert evaluate_candidate_state(facts) == ("W7", 3)


def test_earlier_work_wins_over_a_later_blocking_failure():
    """Row 9 (outstanding Alignment) is above row 10 (E12 blocking W5): a
    diff-generation failure must not hide work the developer can still do."""
    facts = WorkflowFacts(
        **{
            **_ready(),
            "outstanding_alignment_items": 1,
            "blocking_failure_states": frozenset({"W5"}),
        }
    )
    assert evaluate_candidate_state(facts) == ("W4", 9)


def test_running_record_wins_over_every_later_row():
    facts = WorkflowFacts(
        **{
            **_ready(),
            "running_process_kinds": ("diff_generation",),
            "understanding_unconfirmed": True,
            "open_required_questions": 3,
            "outstanding_alignment_items": 4,
            "proposals_needing_review": 5,
        }
    )
    assert evaluate_candidate_state(facts) == ("W1", 4)


def test_alignment_outstanding_beats_proposal_review():
    """Row 9 above row 11: approving proposals while intent drift is
    unsettled would break the premise of the approval (spec §2.2 note)."""
    facts = WorkflowFacts(
        **{**_ready(), "outstanding_alignment_items": 1, "proposals_needing_review": 1}
    )
    assert evaluate_candidate_state(facts) == ("W4", 9)


def test_evaluation_is_deterministic_for_identical_facts():
    facts = WorkflowFacts(**{**_ready(), "open_required_questions": 1})
    results = {evaluate_candidate_state(facts) for _ in range(20)}
    assert results == {("W3", 7)}


# --- Stage 2: the backward hold ---------------------------------------------


def test_unordered_states_are_never_held_and_never_move_the_checkpoint():
    for candidate_facts, expected in (
        ({"has_snapshot": False}, "W0-A"),
        ({"has_session": False}, "W0-B"),
        ({"running_process_kinds": ("alignment_build",)}, "W1"),
    ):
        facts = WorkflowFacts(**{**_ready(), **candidate_facts})
        decision = apply_backward_hold(facts, reached_state="W6")
        assert decision.state == expected
        assert decision.backward_hold is False
        assert decision.next_reached_state is None


def test_normal_forward_path_w3_w1_w4_is_never_held():
    """Spec §8.3: the healthy W3 -> W1 -> W4 sequence must not be caught by
    the hold just because W1 has no workflow position."""
    at_w3 = WorkflowFacts(**{**_ready(), "open_required_questions": 1})
    d3 = apply_backward_hold(at_w3, reached_state="W2")
    assert (d3.state, d3.next_reached_state) == ("W3", "W3")

    running = WorkflowFacts(**{**_ready(), "running_process_kinds": ("understanding_update",)})
    d1 = apply_backward_hold(running, reached_state="W3")
    assert (d1.state, d1.backward_hold, d1.next_reached_state) == ("W1", False, None)

    at_w4 = WorkflowFacts(**{**_ready(), "outstanding_alignment_items": 1})
    d4 = apply_backward_hold(at_w4, reached_state="W3")
    assert (d4.state, d4.next_reached_state) == ("W4", "W4")


def test_forward_candidate_advances_the_checkpoint():
    facts = WorkflowFacts(**{**_ready(), "outstanding_alignment_items": 1})
    decision = apply_backward_hold(facts, reached_state="W3")
    assert decision.state == "W4"
    assert decision.next_reached_state == "W4"
    assert decision.backward_hold is False


def test_backward_candidate_is_held_at_the_checkpoint():
    """A newly reopened question while the developer is at W5 must keep
    showing W5 and report the backward request (spec §6.5)."""
    facts = WorkflowFacts(**{**_ready(), "open_required_questions": 1})
    decision = apply_backward_hold(facts, reached_state="W5")
    assert decision.candidate_state == "W3"
    assert decision.state == "W5"
    assert decision.backward_hold is True
    assert decision.cause_kind == "question_reopened"
    assert decision.next_reached_state is None


def test_acknowledgement_is_modelled_as_lowering_the_checkpoint():
    facts = WorkflowFacts(**{**_ready(), "open_required_questions": 1})
    held = apply_backward_hold(facts, reached_state="W5")
    assert held.state == "W5"
    # The acknowledge endpoint lowers reached_state to the acknowledged
    # candidate; re-evaluating then shows the candidate itself.
    after = apply_backward_hold(facts, reached_state=held.candidate_state)
    assert after.state == "W3"
    assert after.backward_hold is False


def test_hold_still_reports_the_blocking_failure_it_is_holding_back():
    facts = WorkflowFacts(
        **{**_ready(), "blocking_failure_states": frozenset({"W2"})}
    )
    decision = apply_backward_hold(facts, reached_state="W5")
    assert decision.state == "W5"
    assert decision.candidate_state == "W2"
    assert decision.cause_kind == "blocking_failure"


def test_closed_session_shows_w7_without_advancing_the_checkpoint():
    facts = WorkflowFacts(**{**_ready(), "session_closed": True, "open_required_questions": 2})
    decision = apply_backward_hold(facts, reached_state="W3")
    assert decision.state == "W7"
    assert decision.next_reached_state is None
    assert decision.backward_hold is False


def test_every_state_has_exactly_one_primary_action():
    for state in UNORDERED_STATES + ORDERED_STATES:
        if state == "W7":
            continue
        assert isinstance(PRIMARY_ACTION_BY_STATE[state], str)
    # W1 alone has none (spec §4.3.1).
    assert PRIMARY_ACTION_BY_STATE["W1"] == "none"


def test_state_rank_orders_only_the_ordered_states():
    assert [state_rank(s) for s in ORDERED_STATES] == [0, 1, 2, 3, 4, 5]
    assert all(state_rank(s) == -1 for s in UNORDERED_STATES)


def test_terminal_kinds_are_first_match_exclusive():
    completed = WorkflowFacts(
        **{
            **_ready(),
            "diff_matches_approval_set": True,
            "diff_review_complete": True,
            "pending_handoff_count": 2,
        }
    )
    assert terminal_kind_for(completed) == "completed"

    handoff = WorkflowFacts(**{**_ready(), "pending_handoff_count": 1})
    assert terminal_kind_for(handoff) == "handoff"

    assert terminal_kind_for(WorkflowFacts(**_ready())) == "suspended"


# ---------------------------------------------------------------------------
# Part 2: persistence and API
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-workflow-test.db"))
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
    return {
        "Authorization": f"Bearer {token}",
        "X-Probe-System-Id": str(system_id),
    }


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


def _setup(client, tmp_path, name="System W"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _init_repo(tmp_path, f"repo-{name.replace(' ', '-')}")
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    return token, system_id, snapshot_id


def _state(client, headers, session_id=None):
    params = {} if session_id is None else {"session_id": session_id}
    r = client.get("/interview/workflow-state", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _create_session(client, headers, snapshot_id, *, settle_initial_build=True):
    """Create a session and, by default, close its initial-build run record.

    Creating a session opens that record on purpose (Issue #349: `W0-B`
    completes by "session created AND the system started investigating"), so
    a brand-new session is `W1`. Tests that exercise a LATER state have to
    let that build finish first -- exactly like the real flow, where the
    build request closes the record it adopted.
    """
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]
    if settle_initial_build:
        _settle_initial_build(session_id)
    return session_id


def _settle_initial_build(session_id: int, *, ok: bool = True):
    """Close the run record session creation opened, as the build would."""
    from app.db import get_conn
    from app.interview_workflow import finish_process_run

    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM interview_process_run
               WHERE session_id = ? AND status = 'running'
               ORDER BY id LIMIT 1""",
            (session_id,),
        ).fetchone()
        if row is not None:
            finish_process_run(conn, row["id"], ok=ok, error=None if ok else "build failed")


def test_w0a_without_snapshot_and_w0b_with_one(admin_client, tmp_path):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "No Snapshot")
    headers = _headers(token, system_id)

    state = _state(admin_client, headers)
    assert state["state"] == "W0-A"
    assert state["rule_row"] == 1
    assert state["primary_action"] == "open_repository"
    assert [e["code"] for e in state["exceptions"]] == ["E1"]

    repo, sha = _init_repo(tmp_path, "late-repo")
    _insert_snapshot(system_id, repo, sha)
    state = _state(admin_client, headers)
    assert state["state"] == "W0-B"
    assert state["primary_action"] == "start_session"
    assert state["exceptions"] == []


def test_session_of_another_system_is_treated_as_not_selected(admin_client, tmp_path):
    token, system_a, snapshot_a = _setup(admin_client, tmp_path, "Sys A")
    headers_a = _headers(token, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)

    system_b = _create_system(admin_client, token, "Sys B")
    repo_b, sha_b = _init_repo(tmp_path, "repo-b")
    _insert_snapshot(system_b, repo_b, sha_b)
    headers_b = _headers(token, system_b)

    state = _state(admin_client, headers_b, session_a)
    assert state["state"] == "W0-B"
    assert state["session_id"] is None


def test_running_record_produces_w1_and_survives_a_reload(admin_client, tmp_path):
    from app.db import get_conn
    from app.interview_workflow import finish_process_run, start_process_run

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    with get_conn() as conn:
        run_id = start_process_run(conn, session_id, system_id, "alignment_build")

    first = _state(admin_client, headers, session_id)
    assert first["state"] == "W1"
    assert first["rule_row"] == 4
    assert first["primary_action"] == "none"
    assert first["facts"]["running_process_kinds"] == ["alignment_build"]

    # Reload == a second independent evaluation over the same persisted rows.
    assert _state(admin_client, headers, session_id)["state"] == "W1"

    with get_conn() as conn:
        finish_process_run(conn, run_id, ok=True)
    assert _state(admin_client, headers, session_id)["state"] != "W1"


def test_failed_run_lands_on_its_target_state_and_a_later_success_resolves_it(
    admin_client, tmp_path
):
    from app.db import get_conn
    from app.interview_workflow import finish_process_run, start_process_run

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    with get_conn() as conn:
        run_id = start_process_run(conn, session_id, system_id, "understanding_build")
        finish_process_run(conn, run_id, ok=False, error="LLM設定が不正です")

    state = _state(admin_client, headers, session_id)
    assert state["state"] == "W2"
    assert state["rule_row"] == 5
    assert state["facts"]["blocking_failure_states"] == ["W2"]
    blocking = [e for e in state["exceptions"] if e["severity"] == "blocking"]
    assert [e["code"] for e in blocking] == ["E3-a"]
    assert blocking[0]["recovery_process_kind"] == "understanding_build"

    with get_conn() as conn:
        ok_run = start_process_run(conn, session_id, system_id, "understanding_build")
        finish_process_run(conn, ok_run, ok=True)

    state = _state(admin_client, headers, session_id)
    assert state["facts"]["blocking_failure_states"] == []
    assert not [e for e in state["exceptions"] if e["code"] == "E3-a"]


def test_understanding_failure_is_degraded_when_a_question_remains(
    admin_client, tmp_path
):
    """Spec §5.1 / `E3-b`: the class depends on the material left behind."""
    from app.db import get_conn
    from app.interview_workflow import finish_process_run, start_process_run

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "目的は?", "question_category": "purpose", "question_source": "reviewer"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    with get_conn() as conn:
        run_id = start_process_run(conn, session_id, system_id, "understanding_build")
        finish_process_run(conn, run_id, ok=False, error="failed")

    state = _state(admin_client, headers, session_id)
    # Degraded: the open question still lets the developer make progress at W3.
    assert state["state"] == "W3"
    assert state["rule_row"] == 7
    codes = {(e["code"], e["severity"]) for e in state["exceptions"]}
    assert ("E3-b", "degraded") in codes


def test_stale_running_record_is_swept_into_a_failure(admin_client, tmp_path, monkeypatch):
    from app.db import get_conn
    from app.interview_workflow import start_process_run

    monkeypatch.setenv("INTERVIEW_PROCESS_STUCK_AFTER_SECONDS", "0")
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    with get_conn() as conn:
        run_id = start_process_run(conn, session_id, system_id, "diff_generation")
        conn.execute(
            "UPDATE interview_process_run SET started_at = ?, heartbeat_at = ? WHERE id = ?",
            (time.time() - 10_000, time.time() - 10_000, run_id),
        )

    state = _state(admin_client, headers, session_id)
    assert state["state"] != "W1"
    assert state["facts"]["blocking_failure_states"] == ["W5"]


def _approve_and_materialize(client, headers, session_id, system_id, snapshot_id):
    """Persist one approved proposal and a diff for it, without an LLM call."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        run = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model, prompt_version,
                    schema_version, decision_method, status, is_mock, started_at, completed_at)
               VALUES (?, ?, 'interview_dialogue', 'mock', 'mock', 'v1', 'v1',
                       'reasoning_llm', 'completed', 1, ?, ?)""",
            (system_id, snapshot_id, now, now),
        )
        proposal = conn.execute(
            """INSERT INTO interview_proposal
                   (session_id, system_id, snapshot_id, intelligence_run_id, path,
                    qualified_name, approval_state, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'a.py', 'a', 'approved', ?, ?)""",
            (session_id, system_id, snapshot_id, run.lastrowid, now, now),
        )
        conn.execute(
            """INSERT INTO interview_proposal_decision
                   (proposal_id, session_id, system_id, decision, decision_method,
                    actor, decided_at)
               VALUES (?, ?, ?, 'approved', 'manual', 'root', ?)""",
            (proposal.lastrowid, session_id, system_id, now),
        )
        conn.execute(
            """UPDATE interview_session
                   SET materialization_diff = ?, materialized_at = ?, updated_at = ?
                 WHERE id = ?""",
            ("--- a/a.py\n+++ b/a.py\n", now + 1, now + 1, session_id),
        )
        return proposal.lastrowid


def test_w6_requires_a_diff_and_the_diff_review_records_fact_a(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    # No diff yet: W6 is unreachable (spec §8.3 "W6 が差分の存在なしに成立しない").
    r = admin_client.post(
        f"/interview/sessions/{session_id}/diff-review", json={}, headers=headers
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_diff_to_review"

    _approve_and_materialize(admin_client, headers, session_id, system_id, snapshot_id)
    state = _state(admin_client, headers, session_id)
    assert state["state"] == "W6"
    assert state["rule_row"] == 12
    assert state["primary_action"] == "record_diff_review"

    r = admin_client.post(
        f"/interview/sessions/{session_id}/diff-review",
        json={"reviewed_by": "root"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    review = r.json()
    assert review["decision_method"] == "manual"
    assert review["diff_materialized_at"] == state["diff_materialized_at"]

    state = _state(admin_client, headers, session_id)
    assert state["state"] == "W7"
    assert state["terminal_kind"] == "completed"
    assert state["primary_action"] == "open_connection_setup"


def test_a_new_diff_does_not_inherit_the_previous_review(admin_client, tmp_path):
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _approve_and_materialize(admin_client, headers, session_id, system_id, snapshot_id)
    admin_client.post(
        f"/interview/sessions/{session_id}/diff-review", json={}, headers=headers
    )
    assert _state(admin_client, headers, session_id)["state"] == "W7"

    # Regenerating the diff produces a new identity -> review is not carried.
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET materialization_diff = ?, materialized_at = ? WHERE id = ?",
            ("--- a/a.py\n+++ b/a.py\n@@\n+x\n", time.time() + 50, session_id),
        )
    state = _state(admin_client, headers, session_id)
    assert state["facts"]["diff_review_complete"] is False
    assert state["candidate_state"] == "W6"
    # W7 was already reached, so re-entering W6 is a backward transition and
    # needs the developer's acknowledgement first (spec §2.3 W6 戻り).
    assert state["backward_hold"] is True
    request_id = state["pending_back_request"]["id"]
    assert state["pending_back_request"]["cause_kind"] == "diff_review_pending"
    acked = admin_client.post(
        f"/interview/sessions/{session_id}/back-requests/{request_id}/acknowledge",
        json={}, headers=headers,
    ).json()
    assert acked["state"] == "W6"


def test_approving_after_a_diff_keeps_w5_instead_of_falling_to_w7(admin_client, tmp_path):
    """Spec §8.3 承認セットと差分の対応: a decision newer than the diff means
    the diff no longer corresponds to the approval set."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    proposal_id = _approve_and_materialize(
        admin_client, headers, session_id, system_id, snapshot_id
    )
    assert _state(admin_client, headers, session_id)["state"] == "W6"

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO interview_proposal_decision
                   (proposal_id, session_id, system_id, decision, decision_method,
                    actor, decided_at)
               VALUES (?, ?, ?, 'approved', 'manual', 'root', ?)""",
            (proposal_id, session_id, system_id, time.time() + 500),
        )
    state = _state(admin_client, headers, session_id)
    assert state["candidate_state"] == "W5"
    assert state["rule_row"] == 11
    assert state["facts"]["diff_matches_approval_set"] is False
    # Never falls through to W7; the display is either W5 or held at the
    # checkpoint pending the developer's acknowledgement.
    assert state["state"] != "W7"


def test_backward_hold_persists_across_reloads_and_needs_an_acknowledgement(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _approve_and_materialize(admin_client, headers, session_id, system_id, snapshot_id)
    admin_client.post(
        f"/interview/sessions/{session_id}/diff-review", json={}, headers=headers
    )
    assert _state(admin_client, headers, session_id)["reached_state"] == "W7"

    # A newly reopened question would send the candidate back to W3.
    r = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "本当にこれで良い?", "question_category": "purpose", "question_source": "reviewer"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    state = _state(admin_client, headers, session_id)
    assert state["candidate_state"] == "W3"
    assert state["state"] == "W7"
    assert state["backward_hold"] is True
    request_id = state["pending_back_request"]["id"]
    assert state["pending_back_request"]["cause_kind"] == "question_reopened"
    assert any(e["code"] == "E8" for e in state["exceptions"])

    # Reload: the hold is restored from persisted facts (spec §6.5/§6.7-5).
    assert _state(admin_client, headers, session_id)["state"] == "W7"

    r = admin_client.post(
        f"/interview/sessions/{session_id}/back-requests/{request_id}/acknowledge",
        json={"actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "W3"
    assert r.json()["backward_hold"] is False
    assert _state(admin_client, headers, session_id)["state"] == "W3"

    from app.db import get_conn

    with get_conn() as conn:
        ack = conn.execute(
            "SELECT * FROM interview_back_acknowledgement WHERE back_request_id = ?",
            (request_id,),
        ).fetchone()
    assert ack is not None
    assert ack["decision_method"] == "manual"


def test_an_acknowledgement_is_never_reused_for_a_later_backward_request(
    admin_client, tmp_path
):
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _approve_and_materialize(admin_client, headers, session_id, system_id, snapshot_id)
    admin_client.post(
        f"/interview/sessions/{session_id}/diff-review", json={}, headers=headers
    )

    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "Q1", "question_category": "purpose", "question_source": "reviewer"},
        headers=headers,
    ).json()
    first = _state(admin_client, headers, session_id)["pending_back_request"]["id"]
    admin_client.post(
        f"/interview/sessions/{session_id}/back-requests/{first}/acknowledge",
        json={}, headers=headers,
    )
    # Resolve it directly in the store: the automatic refresh job the answer
    # endpoint schedules would itself be a running process (a legitimate W1),
    # which is not what this test is about.
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_qa SET status = 'answered', answer_text = 'はい' WHERE id = ?",
            (qa["id"],),
        )
    assert _state(admin_client, headers, session_id)["state"] == "W7"

    # A brand-new backward cause must be held again, not waved through by the
    # earlier acknowledgement (spec §2.2 stage 2-5).
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO interview_qa
                   (session_id, system_id, question_text, question_category,
                    question_source, status, created_at)
               VALUES (?, ?, 'Q2', 'purpose', 'reviewer', 'open', ?)""",
            (session_id, system_id, time.time()),
        )
    state = _state(admin_client, headers, session_id)
    assert state["state"] == "W7"
    assert state["backward_hold"] is True
    assert state["pending_back_request"]["id"] != first

    r = admin_client.post(
        f"/interview/sessions/{session_id}/back-requests/{first}/acknowledge",
        json={}, headers=headers,
    )
    assert r.status_code == 409


def test_suspend_shows_w7_without_progress_and_resume_restores_the_failure(
    admin_client, tmp_path
):
    """Spec §8.3 安全な終端."""
    from app.db import get_conn
    from app.interview_workflow import finish_process_run, start_process_run

    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    with get_conn() as conn:
        run_id = start_process_run(conn, session_id, system_id, "understanding_build")
        finish_process_run(conn, run_id, ok=False, error="推論モデルの設定に失敗")

    blocked = _state(admin_client, headers, session_id)
    assert blocked["state"] == "W2"
    reached_before = blocked["reached_state"]

    r = admin_client.post(
        f"/interview/sessions/{session_id}/close",
        json={"terminal_kind": "suspended", "reason": "復旧できないため中断", "actor": "root"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    closed = r.json()
    assert closed["state"] == "W7"
    assert closed["rule_row"] == 3
    assert closed["terminal_kind"] == "suspended"
    assert closed["primary_action"] == "resume_session"
    # The failure is NOT resolved by suspending -- it is still recorded.
    assert closed["facts"]["blocking_failure_states"] == ["W2"]
    # And the checkpoint did not advance.
    assert closed["reached_state"] == reached_before

    audit = admin_client.get(
        f"/interview/sessions/{session_id}/status-audit", headers=headers
    ).json()
    assert audit[0]["action"] == "close"
    assert audit[0]["decision_method"] == "manual"

    r = admin_client.post(
        f"/interview/sessions/{session_id}/reopen", json={"actor": "root"}, headers=headers
    )
    assert r.status_code == 200, r.text
    reopened = r.json()
    assert reopened["state"] == "W2"
    assert reopened["rule_row"] == 5
    assert any(e["code"] == "E3-a" for e in reopened["exceptions"])


def test_close_rejects_an_unknown_terminal_kind_and_a_double_close(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    r = admin_client.post(
        f"/interview/sessions/{session_id}/close",
        json={"terminal_kind": "completed"},
        headers=headers,
    )
    assert r.status_code == 422

    assert admin_client.post(
        f"/interview/sessions/{session_id}/close",
        json={"terminal_kind": "handoff"},
        headers=headers,
    ).status_code == 200
    assert admin_client.post(
        f"/interview/sessions/{session_id}/close",
        json={"terminal_kind": "handoff"},
        headers=headers,
    ).status_code == 409


def test_snapshot_stale_is_reported_as_e2_without_blocking_the_state(
    admin_client, tmp_path
):
    """Spec §6.6: E2 sits next to the affected operation and never stops the
    current work by itself."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _approve_and_materialize(admin_client, headers, session_id, system_id, snapshot_id)

    repo, sha = _init_repo(tmp_path, "repo-newer")
    _insert_snapshot(system_id, repo, sha)

    state = _state(admin_client, headers, session_id)
    assert state["state"] == "W6"
    assert any(e["code"] == "E2" for e in state["exceptions"])


def test_process_runs_are_isolated_per_session_and_system(admin_client, tmp_path):
    from app.db import get_conn
    from app.interview_workflow import start_process_run

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Sys One")
    headers = _headers(token, system_id)
    session_a = _create_session(admin_client, headers, snapshot_id)
    session_b = _create_session(admin_client, headers, snapshot_id)

    with get_conn() as conn:
        start_process_run(conn, session_a, system_id, "alignment_build")

    assert _state(admin_client, headers, session_a)["state"] == "W1"
    assert _state(admin_client, headers, session_b)["state"] != "W1"

    other_system = _create_system(admin_client, token, "Sys Two")
    repo, sha = _init_repo(tmp_path, "repo-two")
    other_snapshot = _insert_snapshot(other_system, repo, sha)
    other_headers = _headers(token, other_system)
    other_session = _create_session(admin_client, other_headers, other_snapshot)
    assert _state(admin_client, other_headers, other_session)["state"] != "W1"

    r = admin_client.get(
        f"/interview/sessions/{session_a}/process-runs", headers=other_headers
    )
    assert r.status_code == 404


def test_diff_review_is_idempotent_for_the_same_diff(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path)
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _approve_and_materialize(admin_client, headers, session_id, system_id, snapshot_id)

    first = admin_client.post(
        f"/interview/sessions/{session_id}/diff-review", json={}, headers=headers
    ).json()
    second = admin_client.post(
        f"/interview/sessions/{session_id}/diff-review", json={}, headers=headers
    ).json()
    assert first["id"] == second["id"]


# ---------------------------------------------------------------------------
# Part 3: spec §6 walkthrough scenarios
# ---------------------------------------------------------------------------


def test_scenario_6_1_happy_path_state_sequence(admin_client, tmp_path):
    """§6.1 -- W0-A -> W0-B -> W1 -> ... -> W6 -> W7(完了), with each step
    decided only by persisted facts."""
    from app.db import get_conn
    from app.interview_workflow import finish_process_run, start_process_run

    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Scenario 6.1")
    headers = _headers(token, system_id)
    assert _state(admin_client, headers)["state"] == "W0-A"

    repo, sha = _init_repo(tmp_path, "repo-6-1")
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    assert _state(admin_client, headers)["state"] == "W0-B"

    session_id = _create_session(admin_client, headers, snapshot_id)
    with get_conn() as conn:
        run_id = start_process_run(conn, session_id, system_id, "understanding_build")
    assert _state(admin_client, headers, session_id)["state"] == "W1"

    with get_conn() as conn:
        finish_process_run(conn, run_id, ok=True)
        conn.execute(
            "UPDATE interview_session SET current_understanding = ? WHERE id = ?",
            ('{"system_purpose": [{"name": "P"}]}', session_id),
        )
    assert _state(admin_client, headers, session_id)["state"] == "W2"

    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET understanding_confirmed_at = ?, understanding_confirmed_by = 'root' WHERE id = ?",
            (time.time(), session_id),
        )
        conn.execute(
            """INSERT INTO interview_qa
                   (session_id, system_id, question_text, question_category,
                    question_source, status, created_at)
               VALUES (?, ?, '目的は?', 'purpose', 'reviewer', 'open', ?)""",
            (session_id, system_id, time.time()),
        )
    state = _state(admin_client, headers, session_id)
    assert (state["state"], state["primary_action"]) == ("W3", "submit_answer")

    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_qa SET status = 'answered', answer_text = 'はい' WHERE session_id = ?",
            (session_id,),
        )
        run = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model,
                    prompt_version, schema_version, decision_method, status,
                    is_mock, started_at, completed_at)
               VALUES (?, ?, 'alignment_build', 'mock', 'mock', 'v1', 'v1',
                       'reasoning_llm', 'completed', 1, ?, ?)""",
            (system_id, snapshot_id, time.time(), time.time()),
        )
        conn.execute(
            """INSERT INTO alignment_item
                   (session_id, system_id, snapshot_id, intelligence_run_id,
                    current_claim, alignment_state, confidence, review_category,
                    reason_code, user_reason, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, '現状はこうです', 'gap', 'medium', 'must_review',
                       'gap_detected', '意図とのズレを確認してください', 'open', ?, ?)""",
            (session_id, system_id, snapshot_id, run.lastrowid, time.time(), time.time()),
        )
    state = _state(admin_client, headers, session_id)
    assert (state["state"], state["primary_action"]) == ("W4", "confirm_alignment_item")

    with get_conn() as conn:
        conn.execute(
            "UPDATE alignment_item SET status = 'answered' WHERE session_id = ?",
            (session_id,),
        )
    state = _state(admin_client, headers, session_id)
    assert (state["state"], state["primary_action"]) == ("W5", "approve_proposal")

    _approve_and_materialize(admin_client, headers, session_id, system_id, snapshot_id)
    state = _state(admin_client, headers, session_id)
    assert (state["state"], state["primary_action"]) == ("W6", "record_diff_review")

    admin_client.post(
        f"/interview/sessions/{session_id}/diff-review", json={}, headers=headers
    )
    state = _state(admin_client, headers, session_id)
    assert (state["state"], state["terminal_kind"]) == ("W7", "completed")


def test_scenario_6_7_resume_restores_the_same_state(admin_client, tmp_path):
    """§6.7 -- reload and session switching restore the same state; nothing
    depends on client-only values."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Scenario 6.7")
    headers = _headers(token, system_id)
    session_a = _create_session(admin_client, headers, snapshot_id)
    session_b = _create_session(admin_client, headers, snapshot_id)
    _approve_and_materialize(admin_client, headers, session_a, system_id, snapshot_id)

    before = _state(admin_client, headers, session_a)
    assert before["state"] == "W6"

    # switch away and back
    _state(admin_client, headers, session_b)
    after = _state(admin_client, headers, session_a)
    assert after["state"] == before["state"]
    assert after["rule_row"] == before["rule_row"]

    with get_conn() as conn:
        pending_handoffs = conn.execute(
            "SELECT COUNT(*) FROM question_handoff WHERE session_id = ?", (session_a,)
        ).fetchone()[0]
    assert pending_handoffs == 0


def test_scenario_handoff_terminal(admin_client, tmp_path):
    """§5.4 terminal 2 -- only handoffs are left, so the terminal is 引き継ぎ."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Handoff")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO question_handoff
                   (session_id, system_id, origin_kind, origin_id, assignee,
                    background, needed_decision, priority, status, created_by,
                    created_at, updated_at)
               VALUES (?, ?, 'qa', 1, 'other', 'bg', 'decide', 'high', 'pending',
                       'root', ?, ?)""",
            (session_id, system_id, now, now),
        )
    state = _state(admin_client, headers, session_id)
    assert state["state"] == "W7"
    assert state["terminal_kind"] == "handoff"
    assert state["primary_action"] == "view_handoff_status"


# ---------------------------------------------------------------------------
# Part 4: review findings (PR #350)
# ---------------------------------------------------------------------------


def test_a_new_session_is_w1_and_never_falls_through_to_w7(admin_client, tmp_path):
    """Review finding 3. `W0-B` completes by "session created AND the system
    started investigating" (spec §2.3), so the initial build's run record is
    opened in the SAME request that creates the session.

    Before this fix the record was opened by a SECOND client call, so a
    reload in between left a session with no understanding, no questions, no
    proposals and no diff -- which fell through the whole rule table to `W7`,
    a terminal with no way back because every build control is
    exception-only.
    """
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Fresh Session")
    headers = _headers(token, system_id)
    session_id = _create_session(
        admin_client, headers, snapshot_id, settle_initial_build=False
    )

    state = _state(admin_client, headers, session_id)
    assert state["state"] == "W1"
    assert state["rule_row"] == 4
    assert state["facts"]["running_process_kinds"] == ["understanding_build"]
    # And it survives a reload -- the record is persisted, not client state.
    assert _state(admin_client, headers, session_id)["state"] == "W1"


def test_the_build_request_adopts_the_creation_record_instead_of_stacking(
    admin_client, tmp_path
):
    """Review finding 3, second half: the request that performs the initial
    build must CLOSE the record creation opened, not open a second one --
    otherwise the first is left to be swept as a phantom failure."""
    from app.db import get_conn
    from app.interview_workflow import ProcessRunTracker

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Adopt")
    headers = _headers(token, system_id)
    session_id = _create_session(
        admin_client, headers, snapshot_id, settle_initial_build=False
    )

    tracker = ProcessRunTracker(session_id, system_id, "understanding_build")
    tracker.start(adopt=True)
    tracker.succeed()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status FROM interview_process_run WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    assert [r["status"] for r in rows] == ["succeeded"]
    assert _state(admin_client, headers, session_id)["state"] != "W1"


def test_a_handed_off_question_is_not_outstanding_w3_work(admin_client, tmp_path):
    """Review finding 2. Spec §2.3's `W3` completion condition is
    「回答済み / 引き継ぎ済み / 保留(明示)」, and handing a question off
    deliberately leaves `interview_qa.status` alone -- so counting `status =
    'open'` alone made `W3` uncompletable once a question was handed off."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Handoff Q")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    qa = admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "この判断は誰が行いますか?", "question_category": "purpose",
              "question_source": "reviewer"},
        headers=headers,
    ).json()

    assert _state(admin_client, headers, session_id)["state"] == "W3"

    r = admin_client.post(
        f"/interview/sessions/{session_id}/handoffs",
        json={
            "origin_kind": "qa", "origin_id": qa["id"], "assignee": "other-dev",
            "background": "この領域は担当外です", "needed_decision": "方針を決めてほしい",
            "priority": "high", "created_by": "root",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    handoff_id = r.json()["id"]

    state = _state(admin_client, headers, session_id)
    assert state["facts"]["open_required_questions"] == 0
    assert state["state"] != "W3"

    # Cancelling the handoff makes the question the developer's work again.
    with get_conn() as conn:
        conn.execute(
            "UPDATE question_handoff SET status = 'cancelled' WHERE id = ?", (handoff_id,)
        )
    reopened = _state(admin_client, headers, session_id)
    assert reopened["facts"]["open_required_questions"] == 1
    # The question is the developer's work again. This session had already
    # reached a later checkpoint, so re-entering `W3` is a backward move and
    # waits for the acknowledgement (§2.2 stage 2) -- the point here is that
    # the question counts at all again.
    assert reopened["candidate_state"] == "W3"


def test_an_in_flight_refresh_job_shows_as_w1(admin_client, tmp_path):
    """Review finding 1 (server half). The automatic refresh (Issue #288) is
    a system process in flight, so `W3` -> `W1` -> `W4` must not depend on
    whether its background thread happened to open its own run record yet."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Refresh W1")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO interview_refresh_job
                   (session_id, system_id, trigger_kind, base_answer_marker,
                    status, created_at)
               VALUES (?, ?, 'answer_batch', ?, 'pending', ?)""",
            (session_id, system_id, now, now),
        )

    state = _state(admin_client, headers, session_id)
    assert state["state"] == "W1"
    assert state["facts"]["running_process_kinds"] == ["understanding_update"]

    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_refresh_job SET status = 'updated' WHERE session_id = ?",
            (session_id,),
        )
    assert _state(admin_client, headers, session_id)["state"] != "W1"


def test_a_later_finishing_run_resolves_an_earlier_failure(admin_client, tmp_path):
    """Review finding 7. Two runs of the same kind can overlap (a manual
    rebuild while the automatic refresh is already running), and the one that
    STARTED first can FINISH last. Resolution must follow completion order,
    not row id -- otherwise the failure stays unresolved forever and pins a
    blocking exception the work has already fixed."""
    from app.db import get_conn
    from app.interview_workflow import finish_process_run, start_process_run

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Overlap")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    with get_conn() as conn:
        first = start_process_run(conn, session_id, system_id, "understanding_build")
        second = start_process_run(conn, session_id, system_id, "understanding_build")
        # The later-started run fails first...
        finish_process_run(conn, second, ok=False, error="boom")
    assert _state(admin_client, headers, session_id)["facts"]["blocking_failure_states"] == ["W2"]

    with get_conn() as conn:
        # ...and the earlier-started run succeeds afterwards (lower id).
        finish_process_run(conn, first, ok=True)

    state = _state(admin_client, headers, session_id)
    assert state["facts"]["blocking_failure_states"] == []
    assert not [e for e in state["exceptions"] if e["code"] == "E3-a"]


def test_a_stale_swept_run_is_corrected_by_its_real_outcome(admin_client, tmp_path, monkeypatch):
    """Review finding 8. Nothing updates `heartbeat_at` while a process runs,
    so the stale sweep is a guess from elapsed time. A long-but-healthy build
    must not be frozen as a failure: when the process actually finishes, its
    own outcome replaces the sweep's guess."""
    from app.db import get_conn
    from app.interview_workflow import finish_process_run, start_process_run

    monkeypatch.setenv("INTERVIEW_PROCESS_STUCK_AFTER_SECONDS", "0")
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Stale Repair")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    with get_conn() as conn:
        run_id = start_process_run(conn, session_id, system_id, "understanding_build")
        conn.execute(
            "UPDATE interview_process_run SET started_at = ?, heartbeat_at = ? WHERE id = ?",
            (time.time() - 10_000, time.time() - 10_000, run_id),
        )
    # The sweep runs on evaluation and provisionally marks it failed.
    assert _state(admin_client, headers, session_id)["facts"]["blocking_failure_states"] == ["W2"]

    # The real process finishes successfully afterwards.
    with get_conn() as conn:
        finish_process_run(conn, run_id, ok=True)

    state = _state(admin_client, headers, session_id)
    assert state["facts"]["blocking_failure_states"] == []
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, error FROM interview_process_run WHERE id = ?", (run_id,)
        ).fetchone()
    assert row["status"] == "succeeded"
    assert row["error"] is None


def test_resume_acknowledges_a_pending_back_request_in_one_operation(
    admin_client, tmp_path
):
    """Review finding 5. Spec §5.4 terminal 3: 「再開する」 is ONE operation, and
    when a backward request was pending at suspend time, resuming records the
    manual acknowledgement for it too -- the developer must not have to press
    「先に確認する」 immediately afterwards."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Resume Ack")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _approve_and_materialize(admin_client, headers, session_id, system_id, snapshot_id)
    admin_client.post(
        f"/interview/sessions/{session_id}/diff-review", json={}, headers=headers
    )
    assert _state(admin_client, headers, session_id)["reached_state"] == "W7"

    admin_client.post(
        f"/interview/sessions/{session_id}/qa",
        json={"question_text": "本当にこれで良い?", "question_category": "purpose",
              "question_source": "reviewer"},
        headers=headers,
    )
    held = _state(admin_client, headers, session_id)
    assert held["backward_hold"] is True
    request_id = held["pending_back_request"]["id"]

    admin_client.post(
        f"/interview/sessions/{session_id}/close",
        json={"terminal_kind": "suspended"}, headers=headers,
    )
    resumed = admin_client.post(
        f"/interview/sessions/{session_id}/reopen", json={"actor": "root"}, headers=headers
    ).json()

    assert resumed["state"] == "W3"
    assert resumed["backward_hold"] is False
    assert resumed["pending_back_request"] is None
    with get_conn() as conn:
        ack = conn.execute(
            "SELECT * FROM interview_back_acknowledgement WHERE back_request_id = ?",
            (request_id,),
        ).fetchone()
    assert ack is not None
    assert ack["decision_method"] == "manual"


def test_resume_without_a_pending_back_request_records_no_acknowledgement(
    admin_client, tmp_path
):
    """The acknowledgement stays per-request: resuming a session that had no
    pending backward request must not manufacture one (spec §2.2 stage 2-5)."""
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "Resume Plain")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    admin_client.post(
        f"/interview/sessions/{session_id}/close",
        json={"terminal_kind": "suspended"}, headers=headers,
    )
    admin_client.post(
        f"/interview/sessions/{session_id}/reopen", json={}, headers=headers
    )
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM interview_back_acknowledgement WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
    assert count == 0
