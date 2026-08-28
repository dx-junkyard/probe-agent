"""Tests for Issue #429 -- Product Objective / Milestone (Epic #427).

`docs/product-objective-lineage.md` §12 is the acceptance list this file is
organized (together with `test_product_gap.py`) around. This file covers:

1. identity: key required (422) / key conflict (409) / cross-System 404.
2. append-only revisions: prior decisions survive, `recheck_state` goes
   `stale` while `objective_state` / `design_status` / `achievement` never
   move on their own.
3. every §4.3 transition table cell -- legal transitions succeed, everything
   outside the table is 422 `product_*_not_decidable`.
4. stale-digest fail-closed (§10.1): a non-empty mismatched `captured_digest`
   is 409; an empty one is never checked and reads back `not_captured`.
5. cycle rejection for the Objective parent link and the Milestone
   dependency graph, at 1/2/3 hops plus a deep chain that would blow a
   recursive implementation, all via an ITERATIVE walk.
6. "no auto success": all Milestones `met` never moves the Objective to
   `achieved`.
7. no numeric score/severity/priority/confidence field anywhere in a
   response.
8. `decision_method` on every decision/assessment ledger is fixed to
   `manual` -- the CHECK constraint AND the API never accept anything else.

`app/product_objective.py`'s routers are not yet wired into `app/main.py`
(Issue #429's task brief: that wiring stays with the orchestrating task) --
`_register_routers` below registers `product_objectives.router` /
`milestone_router` on the shared `app.main.app` instance exactly the way
`app/main.py`'s `create_app()` registers every other router
(`dependencies=[Depends(get_principal)]`), idempotently so re-running the
fixture across many tests in this session does not double-register routes.
"""

from __future__ import annotations

import time

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app import product_objective


def _register_routers(app) -> None:
    from app.auth import get_principal
    from app.routes import product_gaps, product_objectives

    _auth = [Depends(get_principal)]
    existing_prefixes = {getattr(r, "path", "") for r in app.routes}
    if not any(p.startswith("/product-objectives") for p in existing_prefixes):
        app.include_router(product_objectives.router, dependencies=_auth)
        app.include_router(product_objectives.milestone_router, dependencies=_auth)
    if not any(p.startswith("/product-gaps") for p in existing_prefixes):
        app.include_router(product_gaps.router, dependencies=_auth)


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-product-objective-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    from app.main import app  # noqa: WPS433

    _register_routers(app)
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
        "/systems", json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _setup(client, tmp_path, name="System Objective"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    return token, system_id


# --- Objective/Milestone helpers --------------------------------------------------


def _create_objective(client, headers, objective_key, expect=201):
    r = client.post("/product-objectives", json={"objective_key": objective_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_objective_revision(client, headers, objective_key, expect=201, **fields):
    body = {"title": "", "intent": "", "contribution": "", "scope_note": "", "summary": "", "change_note": ""}
    body.update(fields)
    r = client.post(f"/product-objectives/{objective_key}/revisions", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _decide_objective(client, headers, objective_key, decision, expect=201, rationale="", captured_digest=""):
    r = client.post(
        f"/product-objectives/{objective_key}/decisions",
        json={"decision": decision, "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_milestone(client, headers, objective_key, milestone_key, expect=201):
    r = client.post(
        "/product-milestones", json={"objective_key": objective_key, "milestone_key": milestone_key}, headers=headers
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_milestone_revision(client, headers, milestone_key, expect=201, **fields):
    body = {
        "title": "", "target_state": "", "verification_method": "unavailable", "verification_note": "",
        "sequence_hint": 0, "summary": "", "change_note": "",
    }
    body.update(fields)
    r = client.post(f"/product-milestones/{milestone_key}/revisions", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _decide_milestone(client, headers, milestone_key, decision, expect=201, rationale="", captured_digest=""):
    r = client.post(
        f"/product-milestones/{milestone_key}/decisions",
        json={"decision": decision, "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _assess_milestone(client, headers, milestone_key, assessment, expect=201, rationale="", evidence_note="", captured_digest=""):
    r = client.post(
        f"/product-milestones/{milestone_key}/assessments",
        json={
            "assessment": assessment, "rationale": rationale, "evidence_note": evidence_note,
            "captured_digest": captured_digest,
        },
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


# ---------------------------------------------------------------------------
# Finite vocabularies
# ---------------------------------------------------------------------------


class TestFiniteVocabularies:
    def test_vocabularies_match_the_documented_contract(self):
        assert set(product_objective.OBJECTIVE_STATES) == {
            "proposed", "confirmed", "active", "achieved", "rejected", "retired",
        }
        assert set(product_objective.RECHECK_STATES) == {"current", "stale", "not_captured"}
        assert set(product_objective.REVISION_STATES) == {"current", "superseded"}
        assert set(product_objective.DESIGN_STATUSES) == {"proposed", "confirmed", "rejected", "retired"}
        assert set(product_objective.OBJECTIVE_DECISION_KINDS) == {
            "confirm", "activate", "achieve", "reject", "retire", "reinstate",
        }
        assert set(product_objective.MILESTONE_DECISION_KINDS) == {"confirm", "reject", "retire", "reinstate"}
        assert set(product_objective.MILESTONE_ASSESSMENT_KINDS) == {"met", "not_met", "indeterminate", "withdraw"}
        assert set(product_objective.MILESTONE_ACHIEVEMENTS) == {"unassessed", "met", "not_met", "indeterminate"}
        assert set(product_objective.MILESTONE_ASSESSABILITIES) == {"assessable", "unavailable", "not_applicable"}
        assert set(product_objective.GAP_LIFECYCLES) == {
            "open", "acknowledged", "deferred", "resolved", "rejected", "obsolete",
        }
        assert set(product_objective.GAP_DECISION_KINDS) == {
            "acknowledge", "defer", "resolve", "reject", "retire", "reopen", "prioritize",
        }
        assert set(product_objective.GAP_PRIORITY_BANDS) == {"unset", "watch", "next", "now"}
        assert set(product_objective.REF_KINDS) == {
            "vision_claim", "purpose_element", "purpose_relation", "capability_entity", "stakeholder_need",
        }


# ---------------------------------------------------------------------------
# §4.1 identity
# ---------------------------------------------------------------------------


class TestObjectiveIdentity:
    def test_empty_key_is_422(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Key Required")
        headers = _headers(token, system_id)
        r = _create_objective(admin_client, headers, "", expect=422)
        assert r.json()["detail"]["code"] == "product_objective_key_required"

    def test_duplicate_key_is_409(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Key Conflict")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "grow-retention")
        r = _create_objective(admin_client, headers, "grow-retention", expect=409)
        assert r.json()["detail"]["code"] == "product_objective_key_conflict"

    def test_missing_objective_is_404(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Missing")
        headers = _headers(token, system_id)
        r = admin_client.get("/product-objectives/does-not-exist", headers=headers)
        assert r.status_code == 404

    def test_default_state_is_proposed_and_recheck_current(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Default State")
        headers = _headers(token, system_id)
        row = _create_objective(admin_client, headers, "grow-retention")
        assert row["objective_state"] == "proposed"
        assert row["recheck_state"] == "current"
        assert row["parent_objective_id"] is None

    def test_same_key_in_two_systems_does_not_collide(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "System Obj Iso A")
        sys_b = _create_system(admin_client, token, "System Obj Iso B")
        _create_objective(admin_client, _headers(token, sys_a), "shared-key")
        # Same key in a different System must succeed, not 409.
        _create_objective(admin_client, _headers(token, sys_b), "shared-key")
        # And each System sees only its own row.
        list_a = admin_client.get("/product-objectives", headers=_headers(token, sys_a)).json()
        list_b = admin_client.get("/product-objectives", headers=_headers(token, sys_b)).json()
        assert [o["objective_key"] for o in list_a["objectives"]] == ["shared-key"]
        assert [o["objective_key"] for o in list_b["objectives"]] == ["shared-key"]


# ---------------------------------------------------------------------------
# §4.5/§8 append-only revisions + digest
# ---------------------------------------------------------------------------


class TestObjectiveRevisions:
    def test_revision_digest_excludes_bookkeeping_fields(self):
        d1 = product_objective.objective_revision_digest(
            title="t", intent="i", contribution="c", scope_note="s", summary="sum"
        )
        d2 = product_objective.objective_revision_digest(
            title="t", intent="i", contribution="c", scope_note="s", summary="sum"
        )
        assert d1 == d2
        d3 = product_objective.objective_revision_digest(
            title="t", intent="i", contribution="c", scope_note="s", summary="different"
        )
        assert d1 != d3

    def test_append_only_revision_preserves_prior_decision_and_stales_recheck(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Recheck")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "grow-retention")
        row = _add_objective_revision(admin_client, headers, "grow-retention", title="v1")
        digest = row["current_revision"]["content_digest"]

        decision = _decide_objective(admin_client, headers, "grow-retention", "confirm", captured_digest=digest)
        assert decision["decision"] == "confirm"

        confirmed = admin_client.get("/product-objectives/grow-retention", headers=headers).json()
        assert confirmed["objective_state"] == "confirmed"
        assert confirmed["recheck_state"] == "current"
        assert len(confirmed["decisions"]) == 1

        # A new revision must NOT delete or alter the prior decision, and
        # must NOT move objective_state -- only recheck_state moves.
        _add_objective_revision(admin_client, headers, "grow-retention", title="v2")
        after = admin_client.get("/product-objectives/grow-retention", headers=headers).json()
        assert after["objective_state"] == "confirmed"
        assert after["recheck_state"] == "stale"
        assert len(after["decisions"]) == 1
        assert after["decisions"][0]["decision"] == "confirm"
        assert after["decisions"][0]["captured_digest"] == digest


# ---------------------------------------------------------------------------
# §4.3 transitions
# ---------------------------------------------------------------------------


class TestObjectiveTransitions:
    def test_confirm_only_legal_from_proposed(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Confirm")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _decide_objective(admin_client, headers, "o1", "confirm")
        r = _decide_objective(admin_client, headers, "o1", "confirm", expect=422)
        assert r.json()["detail"]["code"] == "product_objective_not_decidable"

    def test_achieve_requires_active_not_merely_confirmed(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Achieve Gate")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _decide_objective(admin_client, headers, "o1", "confirm")
        r = _decide_objective(admin_client, headers, "o1", "achieve", expect=422)
        assert r.json()["detail"]["code"] == "product_objective_not_decidable"

        _decide_objective(admin_client, headers, "o1", "activate")
        achieved = _decide_objective(admin_client, headers, "o1", "achieve")
        assert achieved["decision"] == "achieve"
        row = admin_client.get("/product-objectives/o1", headers=headers).json()
        assert row["objective_state"] == "achieved"

    def test_activate_is_idempotent_from_active(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Activate Idem")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _decide_objective(admin_client, headers, "o1", "confirm")
        _decide_objective(admin_client, headers, "o1", "activate")
        again = _decide_objective(admin_client, headers, "o1", "activate")
        assert again["decision"] == "activate"

    def test_reject_illegal_from_active(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Reject Gate")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _decide_objective(admin_client, headers, "o1", "confirm")
        _decide_objective(admin_client, headers, "o1", "activate")
        r = _decide_objective(admin_client, headers, "o1", "reject", expect=422)
        assert r.json()["detail"]["code"] == "product_objective_not_decidable"

    def test_retire_legal_from_confirmed_active_and_achieved(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Retire")
        headers = _headers(token, system_id)
        for key in ("from-confirmed", "from-active", "from-achieved"):
            _create_objective(admin_client, headers, key)
        _decide_objective(admin_client, headers, "from-confirmed", "confirm")
        _decide_objective(admin_client, headers, "from-confirmed", "retire")

        _decide_objective(admin_client, headers, "from-active", "confirm")
        _decide_objective(admin_client, headers, "from-active", "activate")
        _decide_objective(admin_client, headers, "from-active", "retire")

        _decide_objective(admin_client, headers, "from-achieved", "confirm")
        _decide_objective(admin_client, headers, "from-achieved", "activate")
        _decide_objective(admin_client, headers, "from-achieved", "achieve")
        _decide_objective(admin_client, headers, "from-achieved", "retire")

        for key in ("from-confirmed", "from-active", "from-achieved"):
            row = admin_client.get(f"/product-objectives/{key}", headers=headers).json()
            assert row["objective_state"] == "retired"

    def test_reinstate_only_legal_from_rejected_or_retired(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Reinstate")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        r = _decide_objective(admin_client, headers, "o1", "reinstate", expect=422)
        assert r.json()["detail"]["code"] == "product_objective_not_decidable"

        _decide_objective(admin_client, headers, "o1", "reject")
        reinstated = _decide_objective(admin_client, headers, "o1", "reinstate")
        assert reinstated["decision"] == "reinstate"
        row = admin_client.get("/product-objectives/o1", headers=headers).json()
        assert row["objective_state"] == "proposed"


class TestObjectiveStaleDigest:
    def test_mismatched_captured_digest_is_409(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Stale")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _add_objective_revision(admin_client, headers, "o1", title="v1")
        r = _decide_objective(admin_client, headers, "o1", "confirm", expect=409, captured_digest="not-the-real-digest")
        assert r.json()["detail"]["code"] == "product_objective_decision_stale_digest"

    def test_empty_captured_digest_is_never_checked_and_reads_not_captured(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj NotCaptured")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _add_objective_revision(admin_client, headers, "o1", title="v1")
        _decide_objective(admin_client, headers, "o1", "confirm", captured_digest="")
        row = admin_client.get("/product-objectives/o1", headers=headers).json()
        assert row["objective_state"] == "confirmed"
        assert row["recheck_state"] == "not_captured"


class TestDecisionMethodFixedToManual:
    def test_decision_method_is_always_manual_in_response(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Manual")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        decision = _decide_objective(admin_client, headers, "o1", "confirm")
        assert decision["decision_method"] == "manual"

    def test_decision_method_cannot_be_supplied_by_the_caller(self, admin_client):
        """Request models are `extra=\"forbid\"`; a body-supplied
        `decision_method` must 422 rather than being silently accepted."""
        token, system_id = _setup(admin_client, None, "System Obj Manual Forbid")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        r = admin_client.post(
            "/product-objectives/o1/decisions",
            json={"decision": "confirm", "decision_method": "reasoning_llm"},
            headers=headers,
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# §4.4 parent cycle rejection
# ---------------------------------------------------------------------------


class TestObjectiveParentCycles:
    def test_self_parent_is_422(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Parent Self")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        r = admin_client.post(
            "/product-objectives/o1/parent", json={"parent_objective_key": "o1"}, headers=headers
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "product_objective_parent_self"

    def test_two_hop_cycle_is_422(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Parent 2Hop")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "a")
        _create_objective(admin_client, headers, "b")
        r = admin_client.post("/product-objectives/a/parent", json={"parent_objective_key": "b"}, headers=headers)
        assert r.status_code == 201, r.text
        r2 = admin_client.post("/product-objectives/b/parent", json={"parent_objective_key": "a"}, headers=headers)
        assert r2.status_code == 422
        assert r2.json()["detail"]["code"] == "product_objective_parent_cycle"

    def test_three_hop_cycle_is_422(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Parent 3Hop")
        headers = _headers(token, system_id)
        for key in ("a", "b", "c"):
            _create_objective(admin_client, headers, key)
        admin_client.post("/product-objectives/a/parent", json={"parent_objective_key": "b"}, headers=headers)
        admin_client.post("/product-objectives/b/parent", json={"parent_objective_key": "c"}, headers=headers)
        r = admin_client.post("/product-objectives/c/parent", json={"parent_objective_key": "a"}, headers=headers)
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "product_objective_parent_cycle"

    def test_deep_chain_does_not_blow_a_recursive_implementation(self, admin_client):
        """A chain long enough to overflow Python's default recursion limit
        if the cycle check were written recursively (§4.4: 'depth 制限は
        設けない...ただし循環検査は必ず訪問済み集合を持つ反復で行い、再帰で
        書かない')."""
        token, system_id = _setup(admin_client, None, "System Obj Parent Deep")
        headers = _headers(token, system_id)
        depth = 400
        keys = [f"n{i}" for i in range(depth)]
        for key in keys:
            _create_objective(admin_client, headers, key)
        for child, parent in zip(keys[:-1], keys[1:]):
            r = admin_client.post(
                f"/product-objectives/{child}/parent", json={"parent_objective_key": parent}, headers=headers
            )
            assert r.status_code == 201, r.text
        # Closing the loop: last node's parent is the FIRST node -> a cycle
        # spanning the whole chain must still be detected without crashing.
        r = admin_client.post(
            f"/product-objectives/{keys[-1]}/parent", json={"parent_objective_key": keys[0]}, headers=headers
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "product_objective_parent_cycle"

    def test_reparenting_supersedes_the_prior_link_and_clear_returns_to_root(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Parent Clear")
        headers = _headers(token, system_id)
        for key in ("a", "b", "c"):
            _create_objective(admin_client, headers, key)
        admin_client.post("/product-objectives/a/parent", json={"parent_objective_key": "b"}, headers=headers)
        row = admin_client.get("/product-objectives/a", headers=headers).json()
        assert row["parent_objective_key"] == "b"

        # Re-parent to a different Objective -- append-only correction.
        admin_client.post("/product-objectives/a/parent", json={"parent_objective_key": "c"}, headers=headers)
        row = admin_client.get("/product-objectives/a", headers=headers).json()
        assert row["parent_objective_key"] == "c"

        r = admin_client.delete("/product-objectives/a/parent", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["parent_objective_id"] is None
        row = admin_client.get("/product-objectives/a", headers=headers).json()
        assert row["parent_objective_id"] is None

        # DELETE is idempotent when already at root.
        r2 = admin_client.delete("/product-objectives/a/parent", headers=headers)
        assert r2.status_code == 200

    def test_cross_system_parent_is_404(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "System Obj Parent Cross A")
        sys_b = _create_system(admin_client, token, "System Obj Parent Cross B")
        _create_objective(admin_client, _headers(token, sys_a), "a")
        _create_objective(admin_client, _headers(token, sys_b), "b")
        r = admin_client.post(
            "/product-objectives/a/parent", json={"parent_objective_key": "b"}, headers=_headers(token, sys_a)
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# §4/Milestone
# ---------------------------------------------------------------------------


class TestMilestoneIdentity:
    def test_empty_key_is_422(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Key Required")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        r = _create_milestone(admin_client, headers, "o1", "", expect=422)
        assert r.json()["detail"]["code"] == "product_milestone_key_required"

    def test_duplicate_key_is_409(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Key Conflict")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        r = _create_milestone(admin_client, headers, "o1", "m1", expect=409)
        assert r.json()["detail"]["code"] == "product_milestone_key_conflict"

    def test_missing_objective_is_404(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms No Objective")
        headers = _headers(token, system_id)
        r = _create_milestone(admin_client, headers, "does-not-exist", "m1", expect=404)

    def test_belonging_objective_is_reported_and_never_changes(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Belonging")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        row = _create_milestone(admin_client, headers, "o1", "m1")
        assert row["objective_key"] == "o1"
        assert row["design_status"] == "proposed"
        assert row["achievement"] == "unassessed"
        assert row["assessability"] == "unavailable"  # default verification_method is unavailable


class TestMilestoneDesignAndAssessment:
    def test_assessment_requires_confirmed_design_status(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Assess Gate")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        r = _assess_milestone(admin_client, headers, "m1", "met", expect=422)
        assert r.json()["detail"]["code"] == "product_milestone_not_assessable"

        _decide_milestone(admin_client, headers, "m1", "confirm")
        ok = _assess_milestone(admin_client, headers, "m1", "met")
        assert ok["assessment"] == "met"
        row = admin_client.get("/product-milestones/m1", headers=headers).json()
        assert row["achievement"] == "met"
        assert row["design_status"] == "confirmed"

    def test_assessability_axis_is_independent_of_achievement(self, admin_client):
        """`verification_method='unavailable'` -> `assessability='unavailable'`
        even though nothing has been assessed at all (§4.2/§4.3): being
        unassessABLE is a different fact from not having been assessed."""
        token, system_id = _setup(admin_client, None, "System Ms Assessability")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        row = admin_client.get("/product-milestones/m1", headers=headers).json()
        assert row["achievement"] == "unassessed"
        assert row["assessability"] == "unavailable"

        _add_milestone_revision(admin_client, headers, "m1", verification_method="manual_review")
        row2 = admin_client.get("/product-milestones/m1", headers=headers).json()
        assert row2["assessability"] == "assessable"

    def test_indeterminate_is_distinct_from_unassessed(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Indeterminate")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        _decide_milestone(admin_client, headers, "m1", "confirm")
        _assess_milestone(admin_client, headers, "m1", "indeterminate")
        row = admin_client.get("/product-milestones/m1", headers=headers).json()
        assert row["achievement"] == "indeterminate"
        assert row["achievement"] != "unassessed"

    def test_withdraw_returns_to_unassessed(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Withdraw")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        _decide_milestone(admin_client, headers, "m1", "confirm")
        _assess_milestone(admin_client, headers, "m1", "met")
        _assess_milestone(admin_client, headers, "m1", "withdraw")
        row = admin_client.get("/product-milestones/m1", headers=headers).json()
        assert row["achievement"] == "unassessed"

    def test_reinstate_only_legal_from_rejected_or_retired(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Reinstate")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        r = _decide_milestone(admin_client, headers, "m1", "reinstate", expect=422)
        assert r.json()["detail"]["code"] == "product_milestone_not_decidable"
        _decide_milestone(admin_client, headers, "m1", "reject")
        reinstated = _decide_milestone(admin_client, headers, "m1", "reinstate")
        assert reinstated["decision"] == "reinstate"


class TestMilestoneDependencyCycles:
    def test_self_dependency_is_422(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Dep Self")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        r = admin_client.post(
            "/product-milestones/m1/dependencies", json={"depends_on_milestone_key": "m1"}, headers=headers
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "product_milestone_dependency_self"

    def test_two_hop_cycle_is_422(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Dep 2Hop")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "a")
        _create_milestone(admin_client, headers, "o1", "b")
        r = admin_client.post(
            "/product-milestones/a/dependencies", json={"depends_on_milestone_key": "b"}, headers=headers
        )
        assert r.status_code == 201, r.text
        r2 = admin_client.post(
            "/product-milestones/b/dependencies", json={"depends_on_milestone_key": "a"}, headers=headers
        )
        assert r2.status_code == 422
        assert r2.json()["detail"]["code"] == "product_milestone_dependency_cycle"

    def test_three_hop_cycle_is_422(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Dep 3Hop")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        for key in ("a", "b", "c"):
            _create_milestone(admin_client, headers, "o1", key)
        admin_client.post("/product-milestones/a/dependencies", json={"depends_on_milestone_key": "b"}, headers=headers)
        admin_client.post("/product-milestones/b/dependencies", json={"depends_on_milestone_key": "c"}, headers=headers)
        r = admin_client.post(
            "/product-milestones/c/dependencies", json={"depends_on_milestone_key": "a"}, headers=headers
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "product_milestone_dependency_cycle"

    def test_deep_chain_does_not_blow_a_recursive_implementation(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Dep Deep")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        depth = 400
        keys = [f"m{i}" for i in range(depth)]
        for key in keys:
            _create_milestone(admin_client, headers, "o1", key)
        for child, dep in zip(keys[:-1], keys[1:]):
            r = admin_client.post(
                f"/product-milestones/{child}/dependencies", json={"depends_on_milestone_key": dep}, headers=headers
            )
            assert r.status_code == 201, r.text
        r = admin_client.post(
            f"/product-milestones/{keys[-1]}/dependencies",
            json={"depends_on_milestone_key": keys[0]},
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "product_milestone_dependency_cycle"

    def test_duplicate_dependency_is_409(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Ms Dep Dup")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "a")
        _create_milestone(admin_client, headers, "o1", "b")
        admin_client.post("/product-milestones/a/dependencies", json={"depends_on_milestone_key": "b"}, headers=headers)
        r = admin_client.post(
            "/product-milestones/a/dependencies", json={"depends_on_milestone_key": "b"}, headers=headers
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "product_milestone_dependency_duplicate"

    def test_cross_system_dependency_is_404(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "System Ms Dep Cross A")
        sys_b = _create_system(admin_client, token, "System Ms Dep Cross B")
        _create_objective(admin_client, _headers(token, sys_a), "o1")
        _create_objective(admin_client, _headers(token, sys_b), "o1")
        _create_milestone(admin_client, _headers(token, sys_a), "o1", "a")
        _create_milestone(admin_client, _headers(token, sys_b), "o1", "b")
        r = admin_client.post(
            "/product-milestones/a/dependencies", json={"depends_on_milestone_key": "b"},
            headers=_headers(token, sys_a),
        )
        assert r.status_code == 404

    def test_dependency_is_ordering_not_a_gate(self, admin_client):
        """§4.4: `depends_on` need not be `met` for the dependent Milestone
        to itself become `met`."""
        token, system_id = _setup(admin_client, None, "System Ms Dep Not Gate")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "a")
        _create_milestone(admin_client, headers, "o1", "b")
        admin_client.post("/product-milestones/a/dependencies", json={"depends_on_milestone_key": "b"}, headers=headers)
        _decide_milestone(admin_client, headers, "a", "confirm")
        ok = _assess_milestone(admin_client, headers, "a", "met")
        assert ok["assessment"] == "met"
        b_row = admin_client.get("/product-milestones/b", headers=headers).json()
        assert b_row["achievement"] == "unassessed"


# ---------------------------------------------------------------------------
# §6 "no auto success"
# ---------------------------------------------------------------------------


class TestNoAutoSuccess:
    def test_all_milestones_met_does_not_achieve_the_objective(self, admin_client):
        token, system_id = _setup(admin_client, None, "System No Auto Achieve")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        _create_milestone(admin_client, headers, "o1", "m2")
        for key in ("m1", "m2"):
            _decide_milestone(admin_client, headers, key, "confirm")
            _assess_milestone(admin_client, headers, key, "met")

        row = admin_client.get("/product-objectives/o1", headers=headers).json()
        assert row["objective_state"] == "proposed"

        # Even after confirm+activate, achieving both Milestones must not
        # implicitly achieve the Objective without an explicit decision.
        _decide_objective(admin_client, headers, "o1", "confirm")
        _decide_objective(admin_client, headers, "o1", "activate")
        row2 = admin_client.get("/product-objectives/o1", headers=headers).json()
        assert row2["objective_state"] == "active"


# ---------------------------------------------------------------------------
# Upstream references (§4.6)
# ---------------------------------------------------------------------------


def _insert_capability_entity(system_id, session_id, name):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        entity_id = conn.execute(
            "INSERT INTO understanding_capability_entity (system_id, entity_kind, created_at) VALUES (?, 'core_capability', ?)",
            (system_id, now),
        ).lastrowid
        confirmation_id = conn.execute(
            """INSERT INTO understanding_capability_confirmation
                   (system_id, session_id, composition_digest, decided_by, decision_method, created_at)
               VALUES (?, ?, 'd', 'root', 'manual', ?)""",
            (system_id, session_id, now),
        ).lastrowid
        conn.execute(
            """INSERT INTO understanding_capability_entity_version
                   (system_id, confirmation_id, entity_id, entity_kind, name, summary, semantic_digest,
                    payload_json, created_at)
               VALUES (?, ?, ?, 'core_capability', ?, '', 'sd', '{}', ?)""",
            (system_id, confirmation_id, entity_id, name, now),
        )
        return entity_id


def _insert_interview_session(system_id):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        snapshot_id = conn.execute(
            """INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/does-not-matter', 'deadbeef', 'ready', ?, ?)""",
            (system_id, now, now),
        ).lastrowid
        return conn.execute(
            """INSERT INTO interview_session (system_id, snapshot_id, status, created_at, updated_at)
               VALUES (?, ?, 'open', ?, ?)""",
            (system_id, snapshot_id, now, now),
        ).lastrowid


class TestObjectiveUpstreamRefs:
    def test_capability_entity_ref_resolves_and_reports_four_independent_axes(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Ref Capability")
        headers = _headers(token, system_id)
        session_id = _insert_interview_session(system_id)
        entity_id = _insert_capability_entity(system_id, session_id, "Billing")
        _create_objective(admin_client, headers, "o1")

        r = admin_client.post(
            "/product-objectives/o1/upstream-refs",
            json={"ref_kind": "capability_entity", "target_ref": str(entity_id), "note": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["target_resolution"] == "resolved"
        assert out["relation_status"] == "confirmed"  # decision_method="manual"
        assert out["recheck_state"] == "current"
        assert out["target_name"] == "Billing"

    def test_unresolvable_ref_is_reported_honestly(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Ref Unresolved")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        r = admin_client.post(
            "/product-objectives/o1/upstream-refs",
            json={"ref_kind": "capability_entity", "target_ref": "999999", "note": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["target_resolution"] == "unresolved"
        assert out["recheck_state"] == "not_captured"

    def test_stakeholder_need_ref_resolves(self, admin_client):
        token, system_id = _setup(admin_client, None, "System Obj Ref Need")
        headers = _headers(token, system_id)
        admin_client.post(
            "/stakeholder-network/stakeholders",
            json={
                "stakeholder_key": "s1", "display_name": "End User", "stakeholder_kind": "end_user",
                "description": "", "context_note": "",
            },
            headers=headers,
        )
        admin_client.post(
            "/stakeholder-network/needs",
            json={
                "need_key": "n1", "stakeholder_key": "s1", "need_kind": "unmet_need",
                "statement": "faster checkout", "rationale": "",
            },
            headers=headers,
        )
        _create_objective(admin_client, headers, "o1")
        r = admin_client.post(
            "/product-objectives/o1/upstream-refs",
            json={"ref_kind": "stakeholder_need", "target_ref": "n1", "note": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["target_resolution"] == "resolved"
        assert out["target_name"] == "faster checkout"

    def test_invalid_ref_kind_is_rejected_at_the_domain_layer(self, admin_client):
        """The API's `ref_kind` field is `Literal`-typed so pydantic already
        422s an out-of-vocabulary value before this module sees it; this
        test exercises the domain-level guard directly (§10.1
        `product_ref_kind_invalid`)."""
        from app.db import get_conn

        token, system_id = _setup(admin_client, None, "System Obj Ref Invalid")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        with get_conn() as conn:
            with pytest.raises(product_objective.RefKindInvalid):
                product_objective.add_objective_upstream_ref(
                    conn, system_id=system_id, objective_key="o1", ref_kind="not_a_real_kind",
                    target_ref="x", created_by="root",
                )


# ---------------------------------------------------------------------------
# No numeric score anywhere in a response
# ---------------------------------------------------------------------------


_SCORE_LIKE_KEY_SUBSTRINGS = ("priority", "severity", "score", "confidence")


def _assert_no_numeric_score_fields(payload, path=""):
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_lower = key.lower()
            new_path = f"{path}.{key}" if path else key
            if any(s in key_lower for s in _SCORE_LIKE_KEY_SUBSTRINGS) and isinstance(value, (int, float)) and not isinstance(value, bool):
                raise AssertionError(f"numeric score-like field {new_path!r} = {value!r} in response")
            _assert_no_numeric_score_fields(value, new_path)
    elif isinstance(payload, list):
        for idx, item in enumerate(payload):
            _assert_no_numeric_score_fields(item, f"{path}[{idx}]")


class TestNoWeightedScore:
    def test_objective_and_milestone_responses_carry_no_numeric_score_field(self, admin_client):
        token, system_id = _setup(admin_client, None, "System No Score")
        headers = _headers(token, system_id)
        _create_objective(admin_client, headers, "o1")
        _create_milestone(admin_client, headers, "o1", "m1")
        _decide_milestone(admin_client, headers, "m1", "confirm")
        _assess_milestone(admin_client, headers, "m1", "met")
        _decide_objective(admin_client, headers, "o1", "confirm")

        objective_detail = admin_client.get("/product-objectives/o1", headers=headers).json()
        milestone_detail = admin_client.get("/product-milestones/m1", headers=headers).json()
        objective_list = admin_client.get("/product-objectives", headers=headers).json()
        milestone_list = admin_client.get("/product-objectives/o1/milestones", headers=headers).json()

        for payload in (objective_detail, milestone_detail, objective_list, milestone_list):
            _assert_no_numeric_score_fields(payload)
