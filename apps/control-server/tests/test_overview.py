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
    FINDING_SEVERITIES,
    INITIAL_FINDING_LIMIT,
    Finding,
    FindingInputs,
    NextActionFacts,
    build_loop_stages,
    classify_status,
    collect_findings,
    decide_next_action,
    findings_state,
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
        undecided_experiment_count=0,
        adopted_experiment_exists=False,
        publish_job_succeeded=False,
        completed_variant_run_exists=True,
        decided_experiment_exists=True,
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
        (11, dict(undecided_experiment_count=2), "record_experiment_decision"),
        (12, dict(adopted_experiment_exists=True), "publish_change"),
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
        dict(undecided_experiment_count=1),
        dict(adopted_experiment_exists=True),
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
        undecided_experiment_count=5,
        adopted_experiment_exists=True,
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
        **{**_healthy(), "connectivity_freshness": "stale", "undecided_experiment_count": 3}
    )
    action, _state, _msg = decide_next_action(facts)
    assert action.key == "restore_observation"


def test_action_always_explains_itself():
    """#383: 操作名だけでなく、選定理由・完了条件・完了後の価値を示す。"""
    for overrides in (
        dict(repository_configured=False),
        dict(readiness_state="not_built"),
        dict(undecided_experiment_count=1),
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


def test_a_failed_section_degrades_alone(admin_client, monkeypatch):
    """#384: 部分失敗で画面全体が空にならない。"""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "Degraded")

    from app import overview_projection

    def _boom(*_args, **_kwargs):
        raise RuntimeError("brief unavailable")

    monkeypatch.setattr(
        overview_projection.understanding_brief, "build_understanding_brief", _boom
    )
    body = admin_client.get("/overview", headers=_headers(token, system_id)).json()
    assert "brief" in body["degraded_sections"]
    assert body["brief"] is None
    # Everything else still renders.
    assert body["next_action"] is not None
    assert body["runtime"] is not None
    assert body["loop_stages"]
    assert body["degraded_detail"]["brief"]


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
