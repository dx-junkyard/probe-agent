"""Tests for Issue #388 -- Purpose Chain canonical contract and lineage.

`docs/purpose-chain.md` §1.7 is the acceptance list this file is organized
around:

* all 4 element kinds are reachable, and every source is an existing row;
* a confirmed Intent Brief `goal` outranks the reviewer's Vision claim
  (reused from the Understanding Brief, not reclassified);
* an AI-authored relation never becomes `confirmed` without an explicit
  manual decision;
* `unknown` / `conflicting` / `unavailable` / missing-information are kept
  distinct, never collapsed into one rendering;
* all 5 relation statuses are reachable, in first-match order;
* the change-propagation table's rows hold (downstream-only propagation,
  a Capability change never touching `intervention`);
* a System with no decision rows is never `confirmed` (legacy safety);
* changing an endpoint after a decision makes the relation `stale` while the
  decision row itself survives;
* System separation (no session_id/relation_id leak across Systems);
* a relation-derivation failure still returns the frame (guarded loader);
* `element_digest` is stable across a reload.

Part 1 below unit-tests the pure functions directly (mirrors
`test_understanding_brief.py`'s own style of reaching into private module
attributes for classifier-level coverage). Part 2 drives the real HTTP API.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import purpose_chain
from app.purpose_chain import (
    ELEMENT_KINDS,
    ELEMENT_STATES,
    FRAME_STATES,
    RECHECK_STATES,
    RELATION_KINDS,
    RELATION_STATUSES,
    RESOLUTION_LEVELS,
    STALE_REASONS,
    PurposeElement,
    _recheck_relation,
    _relation_status,
    _resolution_level,
    _weaker_provenance,
    _worst_relation_status,
    element_digest,
    relation_id,
)


# ---------------------------------------------------------------------------
# Part 1: pure functions
# ---------------------------------------------------------------------------


def _elem(**overrides) -> PurposeElement:
    base = dict(
        id="x",
        kind="core_capability",
        state="present",
        statement="s",
        confirmation="ai_hypothesis",
        provenance="implementation_fact",
        source_ids=["claim:core_capabilities:x"],
    )
    base.update(overrides)
    return PurposeElement(**base)


def test_finite_vocabularies_match_the_spec():
    assert set(ELEMENT_KINDS) == {
        "beneficiary_problem", "desired_change", "intervention", "core_capability",
    }
    assert set(ELEMENT_STATES) == {"present", "unknown", "unavailable"}
    assert set(RELATION_KINDS) == {
        "problem_to_change", "change_to_intervention", "intervention_to_capability",
    }
    assert set(RELATION_STATUSES) == {
        "confirmed", "hypothesis", "conflicting", "unknown", "unavailable",
    }
    assert set(RECHECK_STATES) == {"current", "stale"}
    assert set(STALE_REASONS) == {
        "source_changed", "target_changed", "both_changed", "upstream_changed", "snapshot_changed",
    }
    assert set(RESOLUTION_LEVELS) == {"L0", "L1", "L2", "L3"}
    assert set(FRAME_STATES) == {"complete", "partial", "empty", "unavailable"}


def test_relation_status_first_match_all_five_values_reachable():
    present_a = _elem(id="a", state="present", confirmation="ai_hypothesis")
    present_b = _elem(id="b", state="present", confirmation="ai_hypothesis")
    da, db = element_digest(present_a), element_digest(present_b)

    # 1. either endpoint unavailable -> unavailable, even if the other is unknown.
    unavailable = _elem(id="u", state="unavailable")
    unknown = _elem(id="k", state="unknown")
    assert _relation_status(unavailable, unknown, None, "d1", "d2") == "unavailable"

    # 2. either endpoint unknown (and neither unavailable) -> unknown.
    assert _relation_status(unknown, present_b, None, "d1", db) == "unknown"

    # 3a. a conflicting endpoint -> conflicting.
    conflicting = _elem(id="c", state="present", confirmation="conflicting")
    assert _relation_status(conflicting, present_b, None, "d1", db) == "conflicting"

    # 3b. a currently valid rejected decision -> conflicting, even with two
    #     otherwise-fine endpoints.
    rejected = {"decision": "rejected", "source_digest": da, "target_digest": db}
    assert _relation_status(present_a, present_b, rejected, da, db) == "conflicting"

    # 4. a confirmed decision whose captured digests still match -> confirmed.
    confirmed = {"decision": "confirmed", "source_digest": da, "target_digest": db}
    assert _relation_status(present_a, present_b, confirmed, da, db) == "confirmed"

    # 5. no decision (or a stale one) -> hypothesis.
    assert _relation_status(present_a, present_b, None, da, db) == "hypothesis"
    stale_confirmed = {"decision": "confirmed", "source_digest": "old", "target_digest": db}
    assert _relation_status(present_a, present_b, stale_confirmed, da, db) == "hypothesis"


def test_a_stale_confirmed_decision_reads_as_hypothesis_not_confirmed():
    """A relation must never claim `confirmed` against content the decision
    no longer describes -- `status` and `recheck_state` must agree on this."""
    a = _elem(id="a")
    b = _elem(id="b")
    decision = {"decision": "confirmed", "source_digest": "old", "target_digest": element_digest(b)}
    status = _relation_status(a, b, decision, element_digest(a), element_digest(b))
    recheck, reason = _recheck_relation(
        a, b, decision, element_digest(a), element_digest(b), upstream_stale=False
    )
    assert status == "hypothesis"
    assert recheck == "stale" and reason == "source_changed"


def test_recheck_state_requires_a_decision_or_upstream_or_evidence_stale():
    a = _elem(id="a")
    b = _elem(id="b")
    # No decision, no upstream, no stale evidence -> current, even though the
    # relation's STATUS is only `hypothesis` (nothing was ever pinned).
    state, reason = _recheck_relation(a, b, None, "d1", "d2", upstream_stale=False)
    assert (state, reason) == ("current", None)

    # Upstream propagation wins even without this relation's own decision.
    state, reason = _recheck_relation(a, b, None, "d1", "d2", upstream_stale=True)
    assert (state, reason) == ("stale", "upstream_changed")

    # evidence_stale is the last-resort reason, only for an
    # implementation_fact-provenance element, and only once decision/upstream
    # are both silent.
    stale_evidence = _elem(id="c", evidence_stale=True)
    state, reason = _recheck_relation(
        stale_evidence, b, None, element_digest(stale_evidence), element_digest(b),
        upstream_stale=False,
    )
    assert (state, reason) == ("stale", "snapshot_changed")


def test_upstream_propagation_outranks_this_relations_own_direct_mismatch():
    """Design decision 2 (module docstring): when the upstream relation is
    stale, a relation reports `upstream_changed` even when its OWN endpoint
    digests would also independently mismatch its captured decision."""
    a = _elem(id="a")
    b = _elem(id="b")
    decision = {"decision": "confirmed", "source_digest": "stale-digest", "target_digest": element_digest(b)}
    state, reason = _recheck_relation(
        a, b, decision, element_digest(a), element_digest(b), upstream_stale=True
    )
    assert (state, reason) == ("stale", "upstream_changed")


def test_both_changed_when_both_endpoints_moved():
    a = _elem(id="a")
    b = _elem(id="b")
    decision = {"decision": "confirmed", "source_digest": "old-a", "target_digest": "old-b"}
    state, reason = _recheck_relation(
        a, b, decision, element_digest(a), element_digest(b), upstream_stale=False
    )
    assert (state, reason) == ("stale", "both_changed")


def test_resolution_level_first_match():
    confirmed = _elem(state="present", confirmation="confirmed")
    present = _elem(state="present", confirmation="ai_hypothesis")
    unknown = _elem(state="unknown")
    assert _resolution_level(confirmed, "confirmed") == "L2"
    assert _resolution_level(confirmed, "hypothesis") == "L1"  # present, relation not unknown/unavailable
    assert _resolution_level(present, None) == "L1"  # no relation at all is not "unknown"
    assert _resolution_level(present, "unknown") == "L0"
    assert _resolution_level(present, "unavailable") == "L0"
    assert _resolution_level(unknown, "confirmed") == "L0"
    # L3 is structurally unreachable until Issue #391.
    assert "L3" not in {
        _resolution_level(confirmed, status)
        for status in (None, "confirmed", "hypothesis", "unknown", "unavailable", "conflicting")
    }


def test_worst_relation_status_picks_the_least_resolved():
    from app.purpose_chain import PurposeRelation

    def rel(status):
        return PurposeRelation(
            id="r", kind="intervention_to_capability", source_id="i", target_id="c",
            status=status, status_label="", recheck_state="current", stale_reason=None,
            provenance="ai_hypothesis", provenance_label="",
        )

    assert _worst_relation_status([rel("confirmed"), rel("unknown"), rel("hypothesis")]) == "unknown"
    assert _worst_relation_status([]) is None


def test_weaker_provenance_uses_the_literals_declared_order():
    assert _weaker_provenance("developer_intent", "ai_hypothesis") == "ai_hypothesis"
    assert _weaker_provenance("implementation_fact", "developer_intent") == "implementation_fact"
    assert _weaker_provenance("ai_hypothesis", "ai_hypothesis") == "ai_hypothesis"


def test_element_digest_ignores_id_and_resolution_level_but_not_meaning():
    a = _elem(id="a", statement="S", resolution_level="L0")
    same_meaning_different_id = _elem(id="b", statement="S", resolution_level="L2")
    different_meaning = _elem(id="a", statement="T", resolution_level="L0")
    assert element_digest(a) == element_digest(same_meaning_different_id)
    assert element_digest(a) != element_digest(different_meaning)


def test_relation_id_is_derived_from_kind_and_endpoint_ids_not_a_row_id():
    assert relation_id("problem_to_change", "beneficiary_problem", "desired_change") == (
        "problem_to_change:beneficiary_problem->desired_change"
    )


# ---------------------------------------------------------------------------
# Part 2: derivation + the HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-purpose-chain-test.db"))
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


def _setup(client, tmp_path, name="System PC"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    repo, sha = _init_repo(tmp_path, f"repo-{name.replace(' ', '-')}")
    snapshot_id = _insert_snapshot(system_id, repo, sha)
    return token, system_id, snapshot_id


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
            finish_process_run(conn, row["id"], ok=ok, error=None if ok else "build failed")


def _create_session(client, headers, snapshot_id):
    r = client.post("/interview/sessions", json={"snapshot_id": snapshot_id}, headers=headers)
    assert r.status_code == 201, r.text
    session_id = r.json()["id"]
    _settle_initial_build(session_id)
    return session_id


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


def _set_pain(client, headers, session_id, text, status="confirmed"):
    r = client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "pain", "value_text": text, "status": status},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _set_goal(client, headers, session_id, text, status="confirmed"):
    r = client.post(
        f"/interview/sessions/{session_id}/intent",
        json={"field": "goal", "value_text": text, "status": status},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _correct_intent(client, headers, item_id, text):
    r = client.post(f"/interview/intent/{item_id}/correct", json={"value_text": text}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _chain(client, headers, session_id=None):
    params = {} if session_id is None else {"session_id": session_id}
    r = client.get("/purpose-chain", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _decide(client, headers, rel_id, session_id, decision, rationale="", *, expect=200):
    r = client.post(
        f"/purpose-chain/relations/{quote(rel_id, safe='')}/decision",
        json={"session_id": session_id, "decision": decision, "rationale": rationale},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect == 200 else r


def _by_kind(chain, kind):
    return [e for e in chain["elements"] if e["kind"] == kind]


def _relation(chain, kind, source_id, target_id):
    rid = f"{kind}:{source_id}->{target_id}"
    match = [r for r in chain["relations"] if r["id"] == rid]
    assert match, f"{rid} not found in {[r['id'] for r in chain['relations']]}"
    return match[0]


# --- element reachability / reuse --------------------------------------------


def test_all_four_element_kinds_are_reachable_and_come_from_existing_rows(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Frame")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "小規模チームの開発者が変更の影響を把握できない")
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(
            system_purpose=[_item("実行時の挙動を観測可能にする")],
            core_capabilities=[_item("Trace 収集")],
        ),
    )

    chain = _chain(admin_client, headers, session_id)
    kinds_present = {e["kind"] for e in chain["elements"]}
    assert kinds_present == {"beneficiary_problem", "desired_change", "intervention", "core_capability"}

    beneficiary = chain["frame"]["beneficiary_problem"]
    assert beneficiary["state"] == "present"
    assert beneficiary["source_kind"] == "intent_item"
    assert beneficiary["source_ids"][0].startswith("intent_item:")

    intervention = chain["frame"]["intervention"]
    assert intervention["source_kind"] == "understanding_claim"
    assert intervention["source_ids"] == ["claim:system_purpose:実行時の挙動を観測可能にする"]

    capability = _by_kind(chain, "core_capability")[0]
    assert capability["source_ids"] == ["claim:core_capabilities:Trace 収集"]


def test_confirmed_intent_goal_outranks_the_reviewer_vision(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Vision")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(vision=[_item("AI が推定した Vision", level="uncertain", evidence=False)]),
    )

    before = _chain(admin_client, headers, session_id)["frame"]["desired_change"]
    assert before["statement"] == "AI が推定した Vision\n説明"
    assert before["provenance"] == "ai_hypothesis"

    _set_goal(admin_client, headers, session_id, "開発者が変更の影響を説明できる状態にする")

    after = _chain(admin_client, headers, session_id)["frame"]["desired_change"]
    assert after["statement"] == "開発者が変更の影響を説明できる状態にする"
    assert after["provenance"] == "developer_intent"
    assert after["confirmation"] == "confirmed"
    assert after["source_kind"] == "intent_item"


def test_intervention_frame_slot_is_first_claim_rest_become_additional_elements(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Multi Purpose")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("主目的"), _item("副次的な目的")]),
    )

    chain = _chain(admin_client, headers, session_id)
    assert chain["frame"]["intervention"]["statement"] == "主目的\n説明"
    intervention_elements = _by_kind(chain, "intervention")
    assert len(intervention_elements) == 2
    extra = [e for e in intervention_elements if e["id"] != "intervention"][0]
    assert extra["statement"] == "副次的な目的\n説明"
    assert extra["id"] != "intervention"  # hashed id, not the frame-slot id


# --- unknown / conflicting / unavailable / missing stay distinct -------------


def test_missing_pain_is_unknown_with_fixed_missing_information(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System No Pain")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    chain = _chain(admin_client, headers, session_id)
    beneficiary = chain["frame"]["beneficiary_problem"]
    assert beneficiary["state"] == "unknown"
    assert beneficiary["statement"] == ""
    assert beneficiary["missing_information"]
    assert beneficiary["source_kind"] == "none"


def test_conflicting_capability_yields_conflicting_relation_status(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Conflict")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(
            system_purpose=[_item("目的")],
            core_capabilities=[_item("矛盾する機能", level="conflicting")],
        ),
    )

    chain = _chain(admin_client, headers, session_id)
    capability = _by_kind(chain, "core_capability")[0]
    assert capability["confirmation"] == "conflicting"
    rel = _relation(chain, "intervention_to_capability", "intervention", capability["id"])
    assert rel["status"] == "conflicting"


def test_a_frame_derivation_failure_is_unavailable_not_missing(admin_client, tmp_path, monkeypatch):
    """A read FAILURE (`unavailable`) must never render like an empty answer
    (`unknown`/`missing`) -- #380's discipline, applied to Purpose Chain."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Broken Brief")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)

    from app import purpose_chain as pc_module

    def _boom(*a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(pc_module.understanding_brief, "build_understanding_brief", _boom)

    chain = _chain(admin_client, headers, session_id)
    assert "frame" in chain["degraded_sections"]
    assert "capabilities" in chain["degraded_sections"]
    # The Brief read itself failed, so `frame_state` reports `unavailable`
    # even though each individual slot still constructed gracefully as
    # `unknown` (brief=None is a valid, non-raising input to the element
    # builders) -- the degraded-section flag is what tells the caller this
    # `unknown` is not to be trusted the way a genuine "nothing extracted"
    # `unknown` would be.
    assert chain["frame_state"] == "unavailable"
    assert chain["frame"]["desired_change"]["state"] == "unknown"
    assert chain["frame"]["intervention"]["state"] == "unknown"


def test_relation_derivation_failure_still_returns_the_frame(admin_client, tmp_path, monkeypatch):
    """§1.7 guarded-loader requirement: relations failing must not blank the
    frame that was already successfully derived."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Relation Fail")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的")], core_capabilities=[_item("機能")]),
    )

    from app import purpose_chain as pc_module

    def _boom(*a, **kw):
        raise RuntimeError("simulated relation failure")

    monkeypatch.setattr(pc_module, "_build_relation", _boom)

    chain = _chain(admin_client, headers, session_id)
    assert chain["degraded_sections"] == ["relations"]
    assert chain["relations"] == []
    assert chain["frame"]["beneficiary_problem"]["state"] == "present"
    assert chain["frame"]["intervention"]["state"] == "present"
    assert len(_by_kind(chain, "core_capability")) == 1


# --- relation decision endpoint / AI candidates never auto-confirm ----------


def test_relation_is_hypothesis_until_a_manual_decision_confirms_it(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Hyp")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的")]),
    )
    _set_goal(admin_client, headers, session_id, "望ましい変化")

    chain = _chain(admin_client, headers, session_id)
    rel = _relation(chain, "problem_to_change", "beneficiary_problem", "desired_change")
    assert rel["status"] == "hypothesis"
    assert rel["decision_id"] is None

    decided = _decide(admin_client, headers, rel["id"], session_id, "confirmed", "明確な因果関係")
    assert decided["status"] == "confirmed"
    assert decided["decision_id"] is not None
    assert decided["decided_by"] == "root"
    assert decided["provenance"] == "developer_intent"
    assert decided["recheck_state"] == "current"

    reread = _chain(admin_client, headers, session_id)
    rel_again = _relation(reread, "problem_to_change", "beneficiary_problem", "desired_change")
    assert rel_again["status"] == "confirmed"
    assert rel_again["decision_id"] == decided["decision_id"]


def test_unknown_endpoint_relation_cannot_be_decided(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Undecidable")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(session_id, system_id, snapshot_id, _understanding(system_purpose=[_item("目的")]))
    # beneficiary_problem has no pain row -> "unknown" -- problem_to_change
    # must refuse a decision.
    chain = _chain(admin_client, headers, session_id)
    rel = _relation(chain, "problem_to_change", "beneficiary_problem", "desired_change")
    assert rel["status"] == "unknown"

    resp = _decide(admin_client, headers, rel["id"], session_id, "confirmed", expect=422)
    assert resp.json()["detail"]["code"] == "purpose_relation_not_decidable"


def test_unknown_relation_id_is_404(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System 404")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    resp = _decide(
        admin_client, headers, "problem_to_change:nope->nope", session_id, "confirmed", expect=404
    )
    assert resp.status_code == 404


def test_legacy_system_with_no_decision_rows_is_never_confirmed(admin_client, tmp_path):
    """A relation with zero `purpose_relation_decision` rows must read
    `hypothesis`, never `confirmed` -- confirmation is never a default."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Legacy")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _set_goal(admin_client, headers, session_id, "変化")
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的")], core_capabilities=[_item("機能")]),
    )

    chain = _chain(admin_client, headers, session_id)
    assert chain["relations"], "expected relations to exist"
    assert all(r["status"] != "confirmed" for r in chain["relations"])


# --- staleness: decision survives, status/recheck reflect the new content ---


def test_changing_an_endpoint_after_confirmation_makes_it_stale_but_keeps_the_decision(
    admin_client, tmp_path
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Stale")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    pain = _set_pain(admin_client, headers, session_id, "元の課題")
    _store_understanding(session_id, system_id, snapshot_id, _understanding(system_purpose=[_item("目的")]))
    _set_goal(admin_client, headers, session_id, "変化")

    chain = _chain(admin_client, headers, session_id)
    rel = _relation(chain, "problem_to_change", "beneficiary_problem", "desired_change")
    decided = _decide(admin_client, headers, rel["id"], session_id, "confirmed")
    assert decided["status"] == "confirmed"

    _correct_intent(admin_client, headers, pain["id"], "変わった課題")

    after = _chain(admin_client, headers, session_id)
    rel_after = _relation(after, "problem_to_change", "beneficiary_problem", "desired_change")
    assert rel_after["status"] == "hypothesis"
    assert rel_after["recheck_state"] == "stale"
    assert rel_after["stale_reason"] == "source_changed"
    # The decision audit fact survives untouched.
    assert rel_after["decision_id"] == decided["decision_id"]
    assert rel_after["decided_by"] == "root"


# --- change propagation table (docs/purpose-chain.md §1.3) -------------------


def test_capability_change_only_staleifies_its_own_relation(admin_client, tmp_path):
    """§1.3 row 3: Capability 変更 → intervention_to_capability のみ
    target_changed。intervention 自体は変えない."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Row3")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的")], core_capabilities=[_item("機能A")]),
    )
    _set_goal(admin_client, headers, session_id, "変化")

    chain = _chain(admin_client, headers, session_id)
    capability = _by_kind(chain, "core_capability")[0]
    change_to_intervention = _relation(chain, "change_to_intervention", "desired_change", "intervention")
    cap_rel = _relation(chain, "intervention_to_capability", "intervention", capability["id"])
    _decide(admin_client, headers, change_to_intervention["id"], session_id, "confirmed")
    _decide(admin_client, headers, cap_rel["id"], session_id, "confirmed")

    # Rewrite the capability's content only.
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(
            system_purpose=[_item("目的")],
            core_capabilities=[_item("機能A", summary="別の説明")],
        ),
    )

    after = _chain(admin_client, headers, session_id)
    after_capability = _by_kind(after, "core_capability")[0]
    after_cap_rel = _relation(after, "intervention_to_capability", "intervention", after_capability["id"])
    after_change_to_intervention = _relation(
        after, "change_to_intervention", "desired_change", "intervention"
    )
    assert after_cap_rel["recheck_state"] == "stale"
    assert after_cap_rel["stale_reason"] == "target_changed"
    # intervention itself, and its own upstream relation, are unaffected --
    # propagation never travels upstream.
    assert after_change_to_intervention["recheck_state"] == "current"


def test_intervention_change_marks_its_own_relation_and_propagates_downstream_only(
    admin_client, tmp_path
):
    """§1.3 row 2: intervention 変更 → 自 relation が target_changed、
    下流が upstream_changed."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Row2")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的")], core_capabilities=[_item("機能A")]),
    )
    _set_goal(admin_client, headers, session_id, "変化")

    chain = _chain(admin_client, headers, session_id)
    capability = _by_kind(chain, "core_capability")[0]
    change_to_intervention = _relation(chain, "change_to_intervention", "desired_change", "intervention")
    cap_rel = _relation(chain, "intervention_to_capability", "intervention", capability["id"])
    _decide(admin_client, headers, change_to_intervention["id"], session_id, "confirmed")
    _decide(admin_client, headers, cap_rel["id"], session_id, "confirmed")

    # Rewrite intervention's (system_purpose's) content only.
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(
            system_purpose=[_item("目的", summary="別の説明")],
            core_capabilities=[_item("機能A")],
        ),
    )

    after = _chain(admin_client, headers, session_id)
    after_change_to_intervention = _relation(
        after, "change_to_intervention", "desired_change", "intervention"
    )
    assert after_change_to_intervention["recheck_state"] == "stale"
    assert after_change_to_intervention["stale_reason"] == "target_changed"

    after_capability = _by_kind(after, "core_capability")[0]
    after_cap_rel = _relation(after, "intervention_to_capability", "intervention", after_capability["id"])
    assert after_cap_rel["recheck_state"] == "stale"
    # Propagated, not a second direct detection -- design decision 2.
    assert after_cap_rel["stale_reason"] == "upstream_changed"


def test_desired_change_change_marks_downstream_relation_and_propagates_further(
    admin_client, tmp_path
):
    """§1.3 row 1: desired_change 変更 → change_to_intervention が
    source_changed、intervention_to_capability が upstream_changed."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Row1")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的")], core_capabilities=[_item("機能A")]),
    )
    goal = _set_goal(admin_client, headers, session_id, "元の変化")

    chain = _chain(admin_client, headers, session_id)
    capability = _by_kind(chain, "core_capability")[0]
    change_to_intervention = _relation(chain, "change_to_intervention", "desired_change", "intervention")
    cap_rel = _relation(chain, "intervention_to_capability", "intervention", capability["id"])
    _decide(admin_client, headers, change_to_intervention["id"], session_id, "confirmed")
    _decide(admin_client, headers, cap_rel["id"], session_id, "confirmed")
    # problem_to_change deliberately left UNDECIDED here -- with no decision
    # it can never be "stale" (recheck_state requires a decision or an
    # upstream stale relation to inherit from), so it cannot mask which
    # relation this test is really about.

    _correct_intent(admin_client, headers, goal["id"], "変わった変化")

    after = _chain(admin_client, headers, session_id)
    after_change_to_intervention = _relation(
        after, "change_to_intervention", "desired_change", "intervention"
    )
    assert after_change_to_intervention["recheck_state"] == "stale"
    assert after_change_to_intervention["stale_reason"] == "source_changed"

    after_capability = _by_kind(after, "core_capability")[0]
    after_cap_rel = _relation(after, "intervention_to_capability", "intervention", after_capability["id"])
    assert after_cap_rel["recheck_state"] == "stale"
    assert after_cap_rel["stale_reason"] == "upstream_changed"


def test_snapshot_becoming_stale_marks_implementation_fact_elements_only(
    admin_client, tmp_path
):
    """§1.3 row 4: snapshot 変更 → implementation_fact 由来要素の
    evidence_stale のみ."""
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Snap")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的", evidence=True)]),
    )
    _set_goal(admin_client, headers, session_id, "変化")

    before = _chain(admin_client, headers, session_id)
    assert before["frame"]["intervention"]["evidence_stale"] is False
    assert before["frame"]["desired_change"]["evidence_stale"] is False  # developer_intent, never stale

    change_to_intervention = _relation(before, "change_to_intervention", "desired_change", "intervention")
    _decide(admin_client, headers, change_to_intervention["id"], session_id, "confirmed")

    # A newer ready snapshot appears -- the session's own pin is now behind.
    from app.db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT repo_path FROM repository_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
    with open(os.path.join(row["repo_path"], "b.py"), "w") as f:
        f.write("def b():\n    return 2\n")
    subprocess.run(["git", "-C", row["repo_path"], "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", row["repo_path"], "commit", "-m", "second"], check=True, capture_output=True
    )
    new_sha = subprocess.run(
        ["git", "-C", row["repo_path"], "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _insert_snapshot(system_id, row["repo_path"], new_sha)

    after = _chain(admin_client, headers, session_id)
    assert after["frame"]["intervention"]["evidence_stale"] is True
    assert after["frame"]["desired_change"]["evidence_stale"] is False
    after_change_to_intervention = _relation(
        after, "change_to_intervention", "desired_change", "intervention"
    )
    assert after_change_to_intervention["recheck_state"] == "stale"
    assert after_change_to_intervention["stale_reason"] == "snapshot_changed"


# --- System / session isolation ----------------------------------------------


def test_purpose_chain_is_system_scoped(admin_client, tmp_path):
    token, system_a, snapshot_a = _setup(admin_client, tmp_path, "System PC A")
    headers_a = _headers(token, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)
    _set_pain(admin_client, headers_a, session_a, "A の課題")
    _store_understanding(
        session_a, system_a, snapshot_a, _understanding(system_purpose=[_item("A の目的")])
    )

    system_b = _create_system(admin_client, token, "System PC B")
    headers_b = _headers(token, system_b)

    leaked = _chain(admin_client, headers_b, session_a)
    assert leaked["session_id"] is None
    assert leaked["frame"]["beneficiary_problem"]["state"] == "unknown"
    assert leaked["elements"] == [] or all(
        e["kind"] != "intervention" or e["statement"] != "A の目的" for e in leaked["elements"]
    )


def test_decision_does_not_leak_across_systems(admin_client, tmp_path):
    token, system_a, snapshot_a = _setup(admin_client, tmp_path, "System Dec A")
    headers_a = _headers(token, system_a)
    session_a = _create_session(admin_client, headers_a, snapshot_a)
    _set_pain(admin_client, headers_a, session_a, "課題")
    _set_goal(admin_client, headers_a, session_a, "変化")
    chain_a = _chain(admin_client, headers_a, session_a)
    rel_a = _relation(chain_a, "problem_to_change", "beneficiary_problem", "desired_change")
    _decide(admin_client, headers_a, rel_a["id"], session_a, "confirmed")

    token_b, system_b, snapshot_b = _setup(admin_client, tmp_path, "System Dec B")
    headers_b = _headers(token_b, system_b)
    session_b = _create_session(admin_client, headers_b, snapshot_b)
    _set_pain(admin_client, headers_b, session_b, "別の課題")
    _set_goal(admin_client, headers_b, session_b, "別の変化")

    chain_b = _chain(admin_client, headers_b, session_b)
    rel_b = _relation(chain_b, "problem_to_change", "beneficiary_problem", "desired_change")
    assert rel_b["status"] != "confirmed"
    assert rel_b["decision_id"] is None


def test_omitted_session_id_resolves_to_the_systems_newest_session(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Newest")
    headers = _headers(token, system_id)
    _create_session(admin_client, headers, snapshot_id)
    newest = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, newest, "最新セッションの課題")

    chain = _chain(admin_client, headers, None)
    assert chain["session_id"] == newest
    assert chain["frame"]["beneficiary_problem"]["statement"] == "最新セッションの課題"


# --- reload reproducibility --------------------------------------------------


def test_reload_reproduces_the_same_chain_and_stable_digests(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Reload")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _set_goal(admin_client, headers, session_id, "変化")
    _store_understanding(
        session_id, system_id, snapshot_id,
        _understanding(system_purpose=[_item("目的")], core_capabilities=[_item("機能")]),
    )

    first = _chain(admin_client, headers, session_id)
    second = _chain(admin_client, headers, session_id)
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second

    from app.db import get_conn
    from app.purpose_chain import derive_purpose_chain

    with get_conn() as conn:
        result = derive_purpose_chain(conn, system_id, session_id)
    digest_first = element_digest(result.frame["beneficiary_problem"])
    with get_conn() as conn:
        result_again = derive_purpose_chain(conn, system_id, session_id)
    digest_second = element_digest(result_again.frame["beneficiary_problem"])
    assert digest_first == digest_second


# --- Overview embedding (#380 composition) -----------------------------------


def test_overview_embeds_the_same_purpose_chain_verbatim(admin_client, tmp_path):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Overview PC")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _set_pain(admin_client, headers, session_id, "課題")
    _store_understanding(session_id, system_id, snapshot_id, _understanding(system_purpose=[_item("目的")]))

    chain = _chain(admin_client, headers, session_id)
    overview = admin_client.get("/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    embedded = overview.json()["purpose_chain"]
    assert embedded is not None
    assert embedded["session_id"] == chain["session_id"]
    assert embedded["frame"]["beneficiary_problem"]["statement"] == (
        chain["frame"]["beneficiary_problem"]["statement"]
    )


def test_overview_degrades_only_the_purpose_chain_section_on_failure(
    admin_client, tmp_path, monkeypatch
):
    token, system_id, snapshot_id = _setup(admin_client, tmp_path, "System Overview PC Fail")
    headers = _headers(token, system_id)
    session_id = _create_session(admin_client, headers, snapshot_id)
    _store_understanding(session_id, system_id, snapshot_id, _understanding(system_purpose=[_item("目的")]))

    from app import overview_projection

    def _boom(*a, **kw):
        raise RuntimeError("simulated purpose_chain failure")

    monkeypatch.setattr(overview_projection.purpose_chain, "derive_purpose_chain", _boom)

    overview = admin_client.get("/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["purpose_chain"] is None
    assert "purpose_chain" in body["degraded_sections"]
    # The rest of the Overview still renders.
    assert body["brief"] is not None
