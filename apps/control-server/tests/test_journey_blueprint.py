"""Tests for Issue #423 -- Journey Service Blueprint projection (Epic #418).

`docs/stakeholder-value-network.md` §8/§15 is the bar this file is
organized around:

1. a deterministic blueprint built from a current Journey revision, with
   Steps as the horizontal axis and the nine lanes as the vertical axis.
2. several Stakeholders / roles on ONE Step render distinctly (never
   collapsed to one).
3. frontstage / backstage / support / external are never conflated.
4. `unknown` / `not_applicable` / `unavailable` all occur and are distinct.
5. the as-is/to-be diff is reproducible by stable `step_key`, including an
   `added`, a `removed`, a `changed`, a `reordered`, and an `unchanged` case.
6. missing evidence / missing delivery target / a stale link are surfaced.
7. per-section degradation (a guarded loader failure never substitutes a
   guessed value).
8. System isolation.
9. no LLM call anywhere in this module (regression).
10. no synthetic score/percentage anywhere in the projection (regression).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import journey_blueprint as jb


# ---------------------------------------------------------------------------
# Part 1: pure functions / finite vocabularies
# ---------------------------------------------------------------------------


class TestFiniteVocabularies:
    def test_vocabularies_match_the_documented_contract(self):
        assert set(jb.LANE_KINDS) == {
            "stakeholder_action", "touchpoint", "frontstage", "backstage",
            "support", "external", "requirement", "evidence", "failure_recovery",
        }
        assert set(jb.LANE_STATES) == {"present", "unknown", "not_applicable", "unavailable"}
        assert set(jb.DELIVERY_KINDS) == {"frontstage", "backstage", "support", "external"}
        assert set(jb.DELIVERY_TARGET_KINDS) == {
            "ux_requirement", "stakeholder", "value_exchange", "not_applicable",
        }
        assert set(jb.DIFF_CHANGE_KINDS) == {"added", "removed", "changed", "reordered", "unchanged"}

    def test_diff_entries_detect_reorder_distinctly_from_change(self):
        from_map = {
            "s1": {"step_key": "s1", "step_order": 0, "content_digest": "d1", "user_intent": "a"},
            "s2": {"step_key": "s2", "step_order": 1, "content_digest": "d2", "user_intent": "b"},
        }
        to_map = {
            "s1": {"step_key": "s1", "step_order": 1, "content_digest": "d1", "user_intent": "a"},
            "s2": {"step_key": "s2", "step_order": 0, "content_digest": "d2-changed", "user_intent": "b2"},
        }
        entries = {e["step_key"]: e for e in jb._diff_entries_with_reorder(from_map, to_map)}
        assert entries["s1"]["change_kind"] == "reordered"
        assert entries["s2"]["change_kind"] == "changed"

    def test_delivery_lane_cell_not_applicable_is_never_auto_filled(self):
        cell = jb._delivery_lane_cell("backstage", "", [])
        assert cell["state"] == "unknown"

        cell = jb._delivery_lane_cell(
            "backstage", "",
            [{"target_kind": "not_applicable", "target_ref": ""}],
        )
        assert cell["state"] == "not_applicable"

        cell = jb._delivery_lane_cell(
            "backstage", "",
            [{"target_kind": "ux_requirement", "target_ref": "req1"}],
        )
        assert cell["state"] == "present"


class TestNoLLMRegression:
    """§0 invariant 9 item 7: no module in this Epic imports or calls an
    LLM client."""

    def test_module_never_imports_llm(self):
        source = Path(jb.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "llm", "journey_blueprint.py must never import app.llm"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "llm" not in alias.name, f"unexpected llm import: {alias.name}"
        assert "create_llm_client" not in source
        assert "LLMClient" not in source


class TestNoSyntheticScoreRegression:
    """§0 invariant 7 / §15 item 8: no weighted total, completeness, or
    confidence percentage anywhere in the public surface."""

    def test_module_has_no_score_field(self):
        source = Path(jb.__file__).read_text(encoding="utf-8")
        for forbidden in ("score", "confidence_percent", "completeness", "weighted_total"):
            assert forbidden not in source.lower()


# ---------------------------------------------------------------------------
# Part 2: HTTP API, fixtures/helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-journey-blueprint-test.db"))
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
        "/systems", json={"name": name, "environment": "test", "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _setup(client, name="System Blueprint"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    return token, system_id


def _step(step_key, order, **overrides):
    base = {
        "step_key": step_key,
        "step_order": order,
        "user_intent": "intent",
        "system_response": "response",
        "success_criteria": "criteria",
        "failure_mode": "",
        "recovery_path": "",
        "evidence_expectation": "",
        "evidence_source_kind": "none",
    }
    base.update(overrides)
    return base


def _create_journey(client, headers, journey_key, *, perspective="to_be", baseline_mode="undecided",
                     baseline_journey_id=None, expect=201):
    r = client.post(
        "/ux-design/journeys",
        json={
            "journey_key": journey_key, "perspective": perspective, "baseline_mode": baseline_mode,
            "baseline_journey_id": baseline_journey_id,
        },
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_journey_revision(client, headers, journey_key, *, steps=None, expect=201, **fields):
    payload = {
        "title": "", "beneficiary": "", "usage_context": "", "entry_trigger": "",
        "value_arrival": "", "summary": "", "change_note": "", "steps": steps or [],
    }
    payload.update(fields)
    r = client.post(f"/ux-design/journeys/{journey_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_requirement(client, headers, requirement_key, requirement_kind="functional", expect=201):
    r = client.post(
        "/ux-design/requirements",
        json={"requirement_key": requirement_key, "requirement_kind": requirement_kind},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_stakeholder(client, headers, stakeholder_key, *, expect=201, **fields):
    payload = {
        "stakeholder_key": stakeholder_key, "display_name": stakeholder_key, "stakeholder_kind": "other",
        "description": "", "context_note": "",
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/stakeholders", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_exchange(client, headers, exchange_key, provider, receiver, exchange_kind, *, expect=201, **fields):
    payload = {
        "exchange_key": exchange_key, "provider_stakeholder_key": provider,
        "receiver_stakeholder_key": receiver, "exchange_kind": exchange_kind,
        "value_statement": "value", "consideration_state": "unknown", "consideration_kind": None,
        "consideration_statement": "", "channel": "web", "trigger": "", "cadence": "unknown",
        "valid_from": None, "valid_to": None,
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/exchanges", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_stakeholder_link(client, headers, journey_key, step_key, stakeholder_key, role, *, expect=201, **fields):
    payload = {"journey_key": journey_key, "step_key": step_key, "stakeholder_key": stakeholder_key,
               "role": role, "note": ""}
    payload.update(fields)
    r = client.post("/journey-blueprint/stakeholder-links", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_delivery_link(client, headers, journey_key, step_key, delivery_kind, target_kind, target_ref="",
                        *, expect=201, **fields):
    payload = {"journey_key": journey_key, "step_key": step_key, "delivery_kind": delivery_kind,
               "target_kind": target_kind, "target_ref": target_ref, "note": ""}
    payload.update(fields)
    r = client.post("/journey-blueprint/delivery-links", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_exchange_link(client, headers, journey_key, step_key, exchange_key, *, expect=201, **fields):
    payload = {"journey_key": journey_key, "step_key": step_key, "exchange_key": exchange_key, "note": ""}
    payload.update(fields)
    r = client.post("/journey-blueprint/exchange-links", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_blueprint(client, headers, journey_key, expect=200):
    r = client.get("/journey-blueprint", params={"journey_key": journey_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_diff(client, headers, journey_key, expect=200):
    r = client.get("/journey-blueprint/diff", params={"journey_key": journey_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _lanes_by_step(blueprint):
    return {s["step_key"]: s["lanes"] for s in blueprint["steps"]}


# ---------------------------------------------------------------------------
# §15 item 1 -- deterministic blueprint from a current Journey revision
# ---------------------------------------------------------------------------


class TestBlueprintProjection:
    def test_journey_not_found_is_404(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        r = admin_client.get("/journey-blueprint", params={"journey_key": "nope"}, headers=headers)
        assert r.status_code == 404

    def test_steps_are_horizontal_axis_ordered_and_lanes_are_the_nine(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(
            admin_client, headers, "j1",
            steps=[_step("s2", 1, user_intent="second"), _step("s1", 0, user_intent="first")],
        )
        blueprint = _get_blueprint(admin_client, headers, "j1")
        assert [s["step_key"] for s in blueprint["steps"]] == ["s1", "s2"]
        for step in blueprint["steps"]:
            assert set(step["lanes"].keys()) == set(jb.LANE_KINDS)

    def test_lane1_present_from_user_intent(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0, user_intent="do the thing")])
        blueprint = _get_blueprint(admin_client, headers, "j1")
        lanes = _lanes_by_step(blueprint)["s1"]
        assert lanes["stakeholder_action"]["state"] == "present"
        assert lanes["stakeholder_action"]["summary"] == "do the thing"

    def test_lane_unknown_when_step_has_nothing_recorded(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(
            admin_client, headers, "j1",
            steps=[_step("s1", 0, user_intent="", system_response="", failure_mode="", recovery_path="")],
        )
        blueprint = _get_blueprint(admin_client, headers, "j1")
        lanes = _lanes_by_step(blueprint)["s1"]
        assert lanes["stakeholder_action"]["state"] == "unknown"
        assert lanes["touchpoint"]["state"] == "unknown"
        assert lanes["frontstage"]["state"] == "unknown"
        assert lanes["backstage"]["state"] == "unknown"
        assert lanes["failure_recovery"]["state"] == "unknown"
        assert lanes["requirement"]["state"] == "unknown"
        assert lanes["evidence"]["state"] == "unknown"


# ---------------------------------------------------------------------------
# §15 item 1 (multiple stakeholders/roles per step) + item ... conflation check
# ---------------------------------------------------------------------------


class TestMultipleStakeholdersPerStep:
    def test_several_stakeholders_and_roles_on_one_step_all_render(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_stakeholder(admin_client, headers, "buyer")
        _create_stakeholder(admin_client, headers, "user")
        _add_stakeholder_link(admin_client, headers, "j1", "s1", "buyer", "payer")
        _add_stakeholder_link(admin_client, headers, "j1", "s1", "user", "beneficiary")
        _add_stakeholder_link(admin_client, headers, "j1", "s1", "user", "actor")

        blueprint = _get_blueprint(admin_client, headers, "j1")
        links = _lanes_by_step(blueprint)["s1"]["stakeholder_action"]["stakeholder_links"]
        assert len(links) == 3
        roles_by_stakeholder = {}
        for link in links:
            roles_by_stakeholder.setdefault(link["stakeholder_key"], set()).add(link["role"])
        assert roles_by_stakeholder["buyer"] == {"payer"}
        assert roles_by_stakeholder["user"] == {"beneficiary", "actor"}


# ---------------------------------------------------------------------------
# §15 item ... frontstage/backstage/support/external never conflated
# ---------------------------------------------------------------------------


class TestDeliveryKindsNeverConflated:
    def test_each_delivery_kind_lands_in_its_own_lane_only(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_stakeholder(admin_client, headers, "stk")

        _add_delivery_link(admin_client, headers, "j1", "s1", "frontstage", "stakeholder", "stk")
        _add_delivery_link(admin_client, headers, "j1", "s1", "support", "stakeholder", "stk")
        _add_delivery_link(admin_client, headers, "j1", "s1", "external", "stakeholder", "stk")
        _add_delivery_link(admin_client, headers, "j1", "s1", "backstage", "not_applicable")

        blueprint = _get_blueprint(admin_client, headers, "j1")
        lanes = _lanes_by_step(blueprint)["s1"]
        assert len(lanes["frontstage"]["delivery_links"]) == 1
        assert lanes["frontstage"]["delivery_links"][0]["delivery_kind"] == "frontstage"
        assert len(lanes["support"]["delivery_links"]) == 1
        assert lanes["support"]["delivery_links"][0]["delivery_kind"] == "support"
        assert len(lanes["external"]["delivery_links"]) == 1
        assert lanes["external"]["delivery_links"][0]["delivery_kind"] == "external"
        # backstage got its OWN not_applicable link and does not see the
        # other three kinds' links.
        assert len(lanes["backstage"]["delivery_links"]) == 1
        assert lanes["backstage"]["delivery_links"][0]["delivery_kind"] == "backstage"
        assert lanes["backstage"]["state"] == "not_applicable"
        # and frontstage/support/external are all `present`, never
        # `not_applicable` (they got a real link, not the sentinel).
        assert lanes["frontstage"]["state"] == "present"
        assert lanes["support"]["state"] == "present"
        assert lanes["external"]["state"] == "present"


# ---------------------------------------------------------------------------
# §15 item 4: unknown / not_applicable / unavailable all occur and differ
# ---------------------------------------------------------------------------


class TestThreeDistinctLaneStates:
    def test_unknown_not_applicable_and_unavailable_all_occur(self, admin_client, monkeypatch):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0, user_intent="")])
        _add_delivery_link(admin_client, headers, "j1", "s1", "backstage", "not_applicable")

        blueprint = _get_blueprint(admin_client, headers, "j1")
        lanes = _lanes_by_step(blueprint)["s1"]
        assert lanes["backstage"]["state"] == "not_applicable"
        assert lanes["requirement"]["state"] == "unknown"

        from app import journey_blueprint as jb_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("forced failure")

        monkeypatch.setattr(jb_mod, "_load_requirement_refs", _boom)
        blueprint2 = _get_blueprint(admin_client, headers, "j1")
        assert "requirement_refs" in blueprint2["degraded_sections"]
        lanes2 = _lanes_by_step(blueprint2)["s1"]
        assert lanes2["requirement"]["state"] == "unavailable"
        # the OTHER, unaffected lane still reads its real state.
        assert lanes2["backstage"]["state"] == "not_applicable"


# ---------------------------------------------------------------------------
# §15 item 5: as-is/to-be diff, reproducible by stable step_key
# ---------------------------------------------------------------------------


class TestAsIsToBeDiff:
    def test_diff_not_applicable_when_no_baseline_linked(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1", baseline_mode="undecided")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        diff = _get_diff(admin_client, headers, "j1")
        assert diff["diff_state"] == "not_applicable"
        assert diff["steps"] == []

    def test_diff_added_removed_changed_reordered_unchanged(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "as_is1", perspective="as_is")
        _add_journey_revision(
            admin_client, headers, "as_is1",
            steps=[
                _step("keep_same", 0, user_intent="same"),
                _step("keep_reordered", 1, user_intent="reorder-me"),
                _step("will_change", 2, user_intent="before"),
                _step("will_remove", 3, user_intent="gone-soon"),
            ],
        )
        as_is = admin_client.get("/ux-design/journeys/as_is1", headers=headers).json()
        as_is_id = as_is["id"]

        _create_journey(admin_client, headers, "to_be1", perspective="to_be",
                         baseline_mode="linked", baseline_journey_id=as_is_id)
        _add_journey_revision(
            admin_client, headers, "to_be1",
            steps=[
                _step("keep_same", 0, user_intent="same"),
                _step("keep_reordered", 5, user_intent="reorder-me"),
                _step("will_change", 2, user_intent="after"),
                _step("will_add", 6, user_intent="brand-new"),
            ],
        )

        diff = _get_diff(admin_client, headers, "to_be1")
        assert diff["diff_state"] == "available"
        by_key = {e["step_key"]: e for e in diff["steps"]}
        assert by_key["keep_same"]["change_kind"] == "unchanged"
        assert by_key["keep_reordered"]["change_kind"] == "reordered"
        assert by_key["will_change"]["change_kind"] == "changed"
        assert by_key["will_remove"]["change_kind"] == "removed"
        assert by_key["will_add"]["change_kind"] == "added"

    def test_diff_is_reproducible(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "as_is1", perspective="as_is")
        _add_journey_revision(admin_client, headers, "as_is1", steps=[_step("s1", 0)])
        as_is_id = admin_client.get("/ux-design/journeys/as_is1", headers=headers).json()["id"]
        _create_journey(admin_client, headers, "to_be1", perspective="to_be",
                         baseline_mode="linked", baseline_journey_id=as_is_id)
        _add_journey_revision(admin_client, headers, "to_be1", steps=[_step("s1", 0, user_intent="changed")])

        d1 = _get_diff(admin_client, headers, "to_be1")
        d2 = _get_diff(admin_client, headers, "to_be1")
        assert d1 == d2


# ---------------------------------------------------------------------------
# §15 item 6: missing evidence / missing delivery target / stale link
# ---------------------------------------------------------------------------


class TestMissingAndStale:
    def test_delivery_link_target_not_found_is_404(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _add_delivery_link(admin_client, headers, "j1", "s1", "backstage", "ux_requirement", "nope", expect=404)

    def test_evidence_lane_missing_when_no_evidence_and_no_expectation(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0, evidence_expectation="")])
        blueprint = _get_blueprint(admin_client, headers, "j1")
        lanes = _lanes_by_step(blueprint)["s1"]
        assert lanes["evidence"]["state"] == "unknown"
        assert lanes["evidence"]["evidence_refs"] == []

    def test_stale_link_when_stakeholder_content_changes(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_stakeholder(admin_client, headers, "stk", display_name="Original")
        _add_stakeholder_link(admin_client, headers, "j1", "s1", "stk", "actor")

        blueprint = _get_blueprint(admin_client, headers, "j1")
        link = _lanes_by_step(blueprint)["s1"]["stakeholder_action"]["stakeholder_links"][0]
        assert link["recheck_state"] == "current"

        r = admin_client.post(
            "/stakeholder-network/stakeholders/stk/revisions",
            json={"display_name": "Changed", "stakeholder_kind": "other", "description": "",
                  "context_note": "", "change_note": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        blueprint2 = _get_blueprint(admin_client, headers, "j1")
        link2 = _lanes_by_step(blueprint2)["s1"]["stakeholder_action"]["stakeholder_links"][0]
        assert link2["recheck_state"] == "stale"

    def test_touchpoint_lane_backed_by_exchange_evidence(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_stakeholder(admin_client, headers, "provider")
        _create_stakeholder(admin_client, headers, "receiver")
        _create_exchange(admin_client, headers, "ex1", "provider", "receiver", "experience", channel="web-form")
        _add_exchange_link(admin_client, headers, "j1", "s1", "ex1")

        r = admin_client.post(
            "/stakeholder-network/evidence-refs",
            json={"subject_kind": "value_exchange", "subject_key": "ex1", "evidence_kind": "human_report",
                  "evidence_ref": "", "statement": "observed once"},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        blueprint = _get_blueprint(admin_client, headers, "j1")
        lanes = _lanes_by_step(blueprint)["s1"]
        assert lanes["touchpoint"]["state"] == "present"
        assert lanes["touchpoint"]["summary"] == "web-form"
        assert lanes["evidence"]["state"] == "present"
        assert len(lanes["evidence"]["evidence_refs"]) == 1
        assert lanes["evidence"]["evidence_refs"][0]["evidence_kind"] == "human_report"


# ---------------------------------------------------------------------------
# §15 item 8: System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_journey_from_another_system_is_404(self, admin_client):
        token, system_a = _setup(admin_client, "System A")
        _, system_b = _setup(admin_client, "System B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)
        _create_journey(admin_client, headers_a, "j1")
        _add_journey_revision(admin_client, headers_a, "j1", steps=[_step("s1", 0)])

        _get_blueprint(admin_client, headers_b, "j1", expect=404)

    def test_stakeholder_link_cannot_cross_systems(self, admin_client):
        token, system_a = _setup(admin_client, "System A2")
        _, system_b = _setup(admin_client, "System B2")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)
        _create_journey(admin_client, headers_a, "j1")
        _add_journey_revision(admin_client, headers_a, "j1", steps=[_step("s1", 0)])
        _create_stakeholder(admin_client, headers_b, "stk")

        # The stakeholder exists only in System B; linking it from System A
        # must not resolve across the boundary.
        _add_stakeholder_link(admin_client, headers_a, "j1", "s1", "stk", "actor", expect=404)
