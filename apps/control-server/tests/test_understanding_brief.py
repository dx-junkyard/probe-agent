"""Tests for Issues #351 / #352 / #353 / #354 -- Understanding Brief and
Decision Readiness.

Part 1 -- the pure classifiers (`app/understanding_brief.py`):
  * every 確認状態 and every 出所 is reachable, and their first-match order
    is the one the spec needs (a conflict outranks a stale confirmation,
    a stale confirmation outranks the confirmation);
  * the two axes are INDEPENDENT: confirming an AI-written sentence never
    turns its provenance into the developer's intent;
  * every Readiness state is reachable, blocking reasons are separated from
    attention/informational ones, and the same facts always give the same
    verdict.

Part 2 -- the derivation and the API:
  * a Vision the developer confirmed in the Intent Brief outranks the model's;
  * an evidence-less Vision can never surface as established;
  * confirming, then changing the understanding, produces 再確認が必要 with the
    concrete changes rather than a silent replacement;
  * an unresolved blocking failure produces 進行不可 -- and does NOT remove the
    workflow's primary action (Readiness explains, it never gates);
  * a reload reproduces the same verdict, and no claim crosses a System
    boundary.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.understanding_brief import (
    CONFIRMATION_STATES,
    PROVENANCE_KINDS,
    READINESS_STATES,
    ClaimFacts,
    ReadinessFacts,
    claim_digest,
    classify_confirmation,
    classify_provenance,
    evaluate_readiness,
)


# ---------------------------------------------------------------------------
# Part 1: the pure classifiers
# ---------------------------------------------------------------------------


def _claim(**overrides) -> ClaimFacts:
    base = dict(kind="core_capability", name="C", confidence_level="likely", evidence_count=1)
    base.update(overrides)
    return ClaimFacts(**base)


def test_every_confirmation_state_is_reachable():
    assert classify_confirmation(_claim(confidence_level="conflicting")) == "conflicting"
    assert classify_confirmation(_claim(previously_confirmed=True)) == "confirmed"
    assert classify_confirmation(_claim(developer_confirmed=True)) == "confirmed"
    assert (
        classify_confirmation(
            _claim(previously_confirmed=True, content_changed_since_confirmation=True)
        )
        == "recheck_required"
    )
    assert (
        classify_confirmation(_claim(confidence_level="uncertain", evidence_count=0))
        == "unknown"
    )
    assert classify_confirmation(_claim()) == "ai_hypothesis"
    # The vocabulary is closed.
    assert set(CONFIRMATION_STATES) == {
        "confirmed", "ai_hypothesis", "conflicting", "unknown", "recheck_required",
    }


def test_conflict_outranks_a_previous_confirmation():
    # A claim the developer confirmed, which the system now reports as
    # contradictory, must not keep reading 確認済み.
    facts = _claim(confidence_level="conflicting", previously_confirmed=True)
    assert classify_confirmation(facts) == "conflicting"


def test_changed_content_outranks_the_confirmation_it_invalidates():
    facts = _claim(previously_confirmed=True, content_changed_since_confirmation=True)
    assert classify_confirmation(facts) == "recheck_required"


def test_every_provenance_kind_is_reachable():
    assert classify_provenance(_claim(developer_authored=True)) == "developer_intent"
    assert classify_provenance(_claim(runtime_observed=True)) == "runtime_observation"
    assert classify_provenance(_claim(evidence_count=2)) == "implementation_fact"
    assert classify_provenance(_claim(evidence_count=0)) == "ai_hypothesis"
    assert set(PROVENANCE_KINDS) == {
        "developer_intent", "implementation_fact", "runtime_observation", "ai_hypothesis",
    }


def test_confirmation_does_not_change_provenance():
    """The #351 mix-up, as a test.

    A human pressing 確認 on a model-written claim makes it 確認済み. It does
    NOT make the developer its author, so the provenance stays 実装事実.
    """
    ai_written = _claim(evidence_count=1, previously_confirmed=True)
    assert classify_confirmation(ai_written) == "confirmed"
    assert classify_provenance(ai_written) == "implementation_fact"

    developer_written = _claim(evidence_count=0, developer_authored=True)
    assert classify_provenance(developer_written) == "developer_intent"
    # ...and authorship alone is not confirmation: writing a claim is not the
    # same act as settling it.
    assert classify_confirmation(developer_written) != "confirmed"


def test_claim_digest_ignores_the_name_but_not_the_content():
    a = {"name": "A", "summary": "s", "evidence": [], "related_apis": []}
    renamed = {"name": "B", "summary": "s", "evidence": [], "related_apis": []}
    edited = {"name": "A", "summary": "s2", "evidence": [], "related_apis": []}
    assert claim_digest(a) == claim_digest(renamed)
    assert claim_digest(a) != claim_digest(edited)
    # Evidence order is not a change.
    unordered = {
        "name": "A", "summary": "s",
        "evidence": [
            {"path": "b.py", "start_line": 1, "end_line": 2, "summary": ""},
            {"path": "a.py", "start_line": 1, "end_line": 2, "summary": ""},
        ],
    }
    ordered = {
        "name": "A", "summary": "s",
        "evidence": [
            {"path": "a.py", "start_line": 1, "end_line": 2, "summary": ""},
            {"path": "b.py", "start_line": 1, "end_line": 2, "summary": ""},
        ],
    }
    assert claim_digest(unordered) == claim_digest(ordered)


def _readiness(**overrides) -> ReadinessFacts:
    base = dict(
        has_understanding=True,
        purpose_present=True,
        purpose_confirmed=True,
        core_capability_count=2,
        unconfirmed_capability_count=0,
        vision_present=True,
        vision_confirmed=True,
        ever_confirmed=True,
    )
    base.update(overrides)
    return ReadinessFacts(**base)


def test_every_readiness_state_is_reachable():
    assert evaluate_readiness(ReadinessFacts())[0] == "not_built"
    assert evaluate_readiness(
        ReadinessFacts(building=True, running_process_kinds=("understanding_build",))
    )[0] == "building"
    assert evaluate_readiness(_readiness(blocking_failure=True))[0] == "blocked"
    assert evaluate_readiness(_readiness(change_count=2))[0] == "recheck_required"
    assert evaluate_readiness(_readiness(ever_confirmed=False))[0] == "needs_confirmation"
    assert evaluate_readiness(_readiness())[0] == "ready"
    assert set(READINESS_STATES) == {
        "not_built", "building", "needs_confirmation", "ready", "recheck_required", "blocked",
    }


def test_building_wins_over_everything_else():
    state, _ = evaluate_readiness(
        _readiness(
            building=True,
            running_process_kinds=("understanding_update",),
            blocking_failure=True,
            change_count=5,
        )
    )
    assert state == "building"


def test_blocking_and_attention_reasons_are_separated():
    state, reasons = evaluate_readiness(
        _readiness(
            conflicting_claims=("Shadow 比較",),
            unconfirmed_capability_count=1,
            vision_present=False,
        )
    )
    assert state == "blocked"
    by_severity = {r.severity for r in reasons}
    assert "blocking" in by_severity and "attention" in by_severity
    conflict = [r for r in reasons if r.code == "claim_conflict"]
    # A reason names its target -- a count alone is not a reason (#353).
    assert conflict and conflict[0].target_name == "Shadow 比較"
    # A missing Vision is worth surfacing but never blocks.
    vision = [r for r in reasons if r.code == "vision_missing"]
    assert vision and vision[0].severity == "informational"


def test_missing_purpose_and_capabilities_is_not_judgeable():
    state, reasons = evaluate_readiness(
        _readiness(purpose_present=False, purpose_confirmed=False, core_capability_count=0)
    )
    assert state == "blocked"
    assert any(r.code == "no_judgeable_content" for r in reasons)


def test_ready_states_its_reason_rather_than_only_a_verdict():
    state, reasons = evaluate_readiness(_readiness())
    assert state == "ready"
    assert any(r.code == "ready" for r in reasons)
    assert all(r.severity != "blocking" for r in reasons)


def test_readiness_is_deterministic_for_identical_facts():
    facts = _readiness(change_count=1, unconfirmed_capability_count=1)
    first = evaluate_readiness(facts)
    second = evaluate_readiness(facts)
    assert first[0] == second[0]
    assert [(r.code, r.severity, r.message) for r in first[1]] == [
        (r.code, r.severity, r.message) for r in second[1]
    ]


def test_reason_severities_are_finite():
    for facts in (
        ReadinessFacts(),
        _readiness(),
        _readiness(blocking_failure=True),
        _readiness(change_count=3),
        _readiness(ever_confirmed=False, purpose_confirmed=False),
    ):
        _, reasons = evaluate_readiness(facts)
        assert all(r.severity in ("blocking", "attention", "informational") for r in reasons)


# ---------------------------------------------------------------------------
# Part 2: derivation from persisted facts, and the API
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-brief-test.db"))
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


def _setup(client, tmp_path, name="System B"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _init_repo(tmp_path, f"repo-{name.replace(' ', '-')}")
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    return token, system_id, snapshot_id


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]
    _settle_initial_build(session_id)
    return session_id


def _settle_initial_build(session_id: int, *, ok: bool = True):
    from app.db import get_conn
    from app.interview_workflow import finish_process_run

    with get_conn() as conn:
        row = conn.execute(
            """SELECT id FROM interview_process_run
               WHERE session_id = ? AND status = 'running' ORDER BY id LIMIT 1""",
            (session_id,),
        ).fetchone()
        if row is not None:
            finish_process_run(
                conn, row["id"], ok=ok, error=None if ok else "build failed"
            )


def _item(name, *, level="likely", evidence=True, summary="説明"):
    return {
        "name": name,
        "summary": summary,
        "confidence": {"level": level, "reason": ""},
        "evidence": (
            [{"path": "a.py", "start_line": 1, "end_line": 2, "summary": "根拠"}]
            if evidence
            else []
        ),
        "why_core": "",
        "related_docs": [],
        "related_apis": [],
        "children": [],
    }


def _understanding(**sections):
    base = {
        "vision": [],
        "system_purpose": [],
        "core_capabilities": [],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
        "probe_flow_candidates": [],
    }
    base.update(sections)
    return base


def _store_understanding(session_id, system_id, snapshot_id, understanding):
    """Write an understanding + its revision, as a successful rebuild would."""
    from app.db import get_conn

    now = time.time()
    payload = json.dumps(understanding)
    with get_conn() as conn:
        conn.execute(
            "UPDATE interview_session SET current_understanding = ?, updated_at = ? WHERE id = ?",
            (payload, now, session_id),
        )
        cur = conn.execute(
            """INSERT INTO understanding_revision
                   (session_id, system_id, snapshot_id, current_understanding,
                    gap_analysis, created_at)
               VALUES (?, ?, ?, ?, '[]', ?)""",
            (session_id, system_id, snapshot_id, payload, now),
        )
        return cur.lastrowid


def _confirm(session_id, system_id, revision_id):
    """Record the human confirmation of `revision_id`, as the endpoint does.

    Written directly rather than through `POST .../confirm-understanding`
    because the Brief only needs the persisted outcome (the session timestamp
    plus the canonical Capability confirmation pointing at the revision); the
    endpoint's own gates are covered by its own tests.
    """
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """UPDATE interview_session
                   SET understanding_confirmed_at = ?, understanding_confirmed_by = 'root'
                 WHERE id = ?""",
            (now, session_id),
        )
        conn.execute(
            """INSERT INTO understanding_capability_confirmation
                   (system_id, session_id, source_revision_id, source_revision_at,
                    composition_digest, decided_by, decision_method, created_at)
               VALUES (?, ?, ?, ?, 'digest', 'root', 'manual', ?)""",
            (system_id, session_id, revision_id, now, now),
        )


def _brief(client, headers, session_id=None):
    params = {} if session_id is None else {"session_id": session_id}
    r = client.get("/interview/understanding-brief", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_no_session_reports_not_built_without_inventing_a_vision(admin_client, tmp_path):
    token, system_id, _ = _setup(admin_client, tmp_path, "System None")
    headers = _headers(token, system_id)

    brief = _brief(admin_client, headers)
    assert brief["built"] is False
    assert brief["vision"] is None
    assert brief["system_purpose"] == []
    assert brief["readiness_state"] == "not_built"
    assert brief["vision_missing_information"]


def test_unconfirmed_understanding_needs_confirmation_with_named_reasons(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System U")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id,
        system_id,
        snapshot_id,
        _understanding(
            system_purpose=[_item("実行時の挙動を観測可能にする")],
            core_capabilities=[_item("Trace 収集"), _item("Shadow 比較")],
        ),
    )

    brief = _brief(admin_client, headers, session_id)
    assert brief["built"] is True
    assert brief["readiness_state"] == "needs_confirmation"
    assert [c["name"] for c in brief["core_capabilities"]] == ["Trace 収集", "Shadow 比較"]
    # Nothing is presented as established before the developer confirmed it.
    assert all(c["confirmation"] != "confirmed" for c in brief["core_capabilities"])
    assert brief["system_purpose"][0]["provenance"] == "implementation_fact"
    codes = {r["code"] for r in brief["readiness_reasons"]}
    assert "purpose_unconfirmed" in codes and "capabilities_unconfirmed" in codes


def test_confirmation_then_change_asks_for_a_recheck_and_names_the_change(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System C")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    understanding = _understanding(
        system_purpose=[_item("実行時の挙動を観測可能にする")],
        core_capabilities=[_item("Trace 収集")],
    )
    revision_id = _store_understanding(session_id, system_id, snapshot_id, understanding)
    _confirm(session_id, system_id, revision_id)

    confirmed = _brief(admin_client, headers, session_id)
    assert confirmed["readiness_state"] == "ready"
    assert confirmed["system_purpose"][0]["confirmation"] == "confirmed"
    # ...but confirming an AI-written claim does not make it the developer's.
    assert confirmed["system_purpose"][0]["provenance"] == "implementation_fact"
    assert confirmed["changes_since_confirmation"] == []

    # A later rebuild adds a capability and rewrites the purpose summary.
    changed = _understanding(
        system_purpose=[_item("実行時の挙動を観測可能にする", summary="別の説明")],
        core_capabilities=[_item("Trace 収集"), _item("Shadow 比較")],
    )
    _store_understanding(session_id, system_id, snapshot_id, changed)

    after = _brief(admin_client, headers, session_id)
    assert after["readiness_state"] == "recheck_required"
    kinds = {(c["change_kind"], c["name"]) for c in after["changes_since_confirmation"]}
    assert ("added", "Shadow 比較") in kinds
    assert ("summary_changed", "実行時の挙動を観測可能にする") in kinds
    # The confirmed-then-edited claim is no longer 確認済み.
    assert after["system_purpose"][0]["confirmation"] == "recheck_required"


def test_confirmed_intent_goal_is_the_vision_and_outranks_the_model(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System V")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id,
        system_id,
        snapshot_id,
        _understanding(
            vision=[_item("AI が推定した Vision", level="uncertain", evidence=False)],
            system_purpose=[_item("P")],
        ),
    )

    # Before any Intent Brief entry, the model's Vision shows -- as a
    # hypothesis, never as an established fact.
    model_vision = _brief(admin_client, headers, session_id)["vision"]
    assert model_vision["name"] == "AI が推定した Vision"
    assert model_vision["confirmation"] == "unknown"
    assert model_vision["provenance"] == "ai_hypothesis"

    r = admin_client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": "開発者が自分のシステムを説明できる状態にする"},
        headers=headers,
    )
    assert r.status_code == 201, r.text

    vision = _brief(admin_client, headers, session_id)["vision"]
    assert vision["name"] == "開発者が自分のシステムを説明できる状態にする"
    assert vision["provenance"] == "developer_intent"
    assert vision["confirmation"] == "confirmed"


def test_missing_vision_names_the_information_it_lacks(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System MV")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id, _understanding(system_purpose=[_item("P")])
    )

    brief = _brief(admin_client, headers, session_id)
    assert brief["vision"] is None
    assert brief["vision_missing_information"]
    assert any(r["code"] == "vision_missing" for r in brief["readiness_reasons"])
    # A missing Vision is not a reason to stop.
    assert brief["readiness_state"] != "blocked"


def test_a_blocking_failure_blocks_the_brief_but_never_the_primary_action(
    admin_client, tmp_path
):
    """Readiness explains; it does not gate (#353 非目標).

    The workflow's single primary action is decided by Issue #349's engine and
    must be unaffected by anything this module concludes.
    """
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System F")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id,
        system_id,
        snapshot_id,
        _understanding(system_purpose=[_item("P")], core_capabilities=[_item("C")]),
    )

    before = admin_client.get(
        "/interview/workflow-state", params={"session_id": session_id}, headers=headers
    ).json()
    assert before["primary_action"] == "confirm_understanding"

    from app.db import get_conn
    from app.interview_workflow import finish_process_run, start_process_run

    with get_conn() as conn:
        run_id = start_process_run(conn, session_id, system_id, "understanding_update")
        finish_process_run(conn, run_id, ok=False, error="provider error")

    brief = _brief(admin_client, headers, session_id)
    assert brief["readiness_state"] == "blocked"
    assert any(
        r["code"] == "blocking_failure" and r["severity"] == "blocking"
        for r in brief["readiness_reasons"]
    )

    after = admin_client.get(
        "/interview/workflow-state", params={"session_id": session_id}, headers=headers
    ).json()
    assert after["primary_action"] == before["primary_action"]


def test_running_process_shows_building_rather_than_a_verdict(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System R")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id, _understanding(system_purpose=[_item("P")])
    )

    from app.db import get_conn
    from app.interview_workflow import start_process_run

    with get_conn() as conn:
        start_process_run(conn, session_id, system_id, "understanding_update")

    brief = _brief(admin_client, headers, session_id)
    assert brief["readiness_state"] == "building"
    assert any(r["code"] == "process_running" for r in brief["readiness_reasons"])


def test_reload_reproduces_the_same_verdict(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System D")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id,
        system_id,
        snapshot_id,
        _understanding(
            system_purpose=[_item("P")],
            core_capabilities=[_item("C1"), _item("C2", level="conflicting")],
        ),
    )

    first = _brief(admin_client, headers, session_id)
    second = _brief(admin_client, headers, session_id)
    assert first == second
    assert first["readiness_state"] == "blocked"
    assert [c["confirmation"] for c in first["core_capabilities"]] == [
        "ai_hypothesis",
        "conflicting",
    ]


def test_key_unconfirmed_puts_conflicts_first_and_is_capped(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System K")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id,
        system_id,
        snapshot_id,
        _understanding(
            system_purpose=[_item("P")],
            core_capabilities=[
                _item(f"C{i}") for i in range(6)
            ] + [_item("矛盾する機能", level="conflicting")],
        ),
    )

    brief = _brief(admin_client, headers, session_id)
    assert len(brief["key_unconfirmed"]) == 5
    assert brief["key_unconfirmed"][0]["name"] == "矛盾する機能"
    # 初期表示は 5 件までに絞るが、水増しはしない。
    assert brief["core_capability_initial_count"] == 5
    assert len(brief["core_capabilities"]) == 7


def test_brief_is_system_scoped(admin_client, tmp_path):
    token, system_a, snapshot_a = _setup(admin_client, tmp_path, "System A1")
    headers_a = _headers(token, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)
    _store_understanding(
        session_a,
        system_a,
        snapshot_a,
        _understanding(system_purpose=[_item("A の目的")]),
    )

    system_b = _create_system(admin_client, token, "System B1")
    headers_b = _headers(token, system_b)

    # System B asking for System A's session gets "no session", not A's claims.
    leaked = _brief(admin_client, headers_b, session_a)
    assert leaked["built"] is False
    assert leaked["system_id"] == system_b
    assert leaked["system_purpose"] == []


def test_recheck_is_not_reported_as_merely_unconfirmed():
    """A confirmed-then-changed claim tells one story, not two.

    Reporting it as 「未確認」 next to 「前回の確認後に変わりました」 describes
    the same claim in two incompatible ways.
    """
    state, reasons = evaluate_readiness(
        _readiness(change_count=1, recheck_claim_count=1, unconfirmed_capability_count=0)
    )
    assert state == "recheck_required"
    codes = {r.code for r in reasons}
    assert "understanding_changed" in codes
    assert "purpose_unconfirmed" not in codes
    assert "capabilities_unconfirmed" not in codes


def test_an_evidence_only_edit_to_a_confirmed_claim_still_asks_for_a_recheck():
    """The section diff only sees names / summaries / confidence.

    `claim_digest` also covers evidence, related docs and children, so a claim
    can be `recheck_required` while `change_count` is 0. Without the separate
    count that edit would read as 進行可能.
    """
    state, reasons = evaluate_readiness(_readiness(change_count=0, recheck_claim_count=1))
    assert state == "recheck_required"
    assert any(r.code == "confirmed_claim_edited" for r in reasons)


def test_a_mock_intent_vision_is_labelled_not_hidden(admin_client, tmp_path):
    """CLAUDE.md forbids presenting mock LLM output as analysed data.

    The Understanding reviewer fails closed on a mock client, so
    `current_understanding` is never mock -- but an `ai_proposed` Intent Brief
    item can be, and it can surface as the Vision.
    """
    from app.db import get_conn

    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Mock")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id, _understanding(system_purpose=[_item("P")])
    )
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO interview_intent_item
                   (session_id, system_id, field, value_text, status, origin,
                    decision_method, is_mock, created_at, updated_at)
               VALUES (?, ?, 'goal', 'モック提案の Vision', 'proposed',
                       'ai_proposed', 'reasoning_llm', 1, ?, ?)""",
            (session_id, system_id, now, now),
        )

    vision = _brief(admin_client, headers, session_id)["vision"]
    assert vision["name"] == "モック提案の Vision"
    assert vision["is_mock"] is True
    assert vision["confirmation"] == "ai_hypothesis"
