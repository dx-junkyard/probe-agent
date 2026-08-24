"""Tests for Issue #424 -- Functional Lineage View + Gap/Impact Overlay
(Epic #418).

`docs/stakeholder-value-network.md` §9/§15 is the contract this file
verifies:

1. every §9.2 gap code is reachable, with its FIXED severity (§15 item
   "Every gap code is reachable and deterministic").
2. no synthetic outcome: traces alone never move an Outcome/design_status.
3. no weighted score / percentage / ranking anywhere in the response.
4. no LLM import or call anywhere in this module or its route.
5. `stale` / `unknown` / `unavailable` / `conflict` are never treated as
   success.
6. System isolation.
7. impact traversal is downstream only.
8. the Epic #418 end-to-end fixture (§15 item 10): 利用者/購入責任者/
   運用担当者/提供者, service+experience/money/information/obligation
   exchanges, payer != beneficiary, an as-is and a to-be Journey,
   Requirements, a Solution Design Option, a Flow and a Node, human and
   runtime evidence, an Outcome, an upstream change producing `stale`, and
   at least one unconnected and one unmeasured gap -- traversed end to end.
"""

from __future__ import annotations

import ast
import inspect
import time

import pytest
from fastapi.testclient import TestClient

from app import functional_lineage as fl
from app import journey_blueprint, purpose_chain, solution_design, stakeholder_network as sn
from app import stakeholder_value_network as svn


# ---------------------------------------------------------------------------
# Fixtures / helpers (kept local -- #424 owns this test file exclusively,
# same convention `test_stakeholder_value_network.py` and
# `test_journey_blueprint.py` already follow for their own files)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-functional-lineage-test.db"))
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


def _setup(client, name="System Functional Lineage"):
    token = _login(client)
    system_id = _create_system(client, token, name)
    return token, system_id


def _create_stakeholder(client, headers, stakeholder_key, *, expect=201, **fields):
    payload = {
        "stakeholder_key": stakeholder_key, "display_name": stakeholder_key, "stakeholder_kind": "other",
        "description": "", "context_note": "",
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/stakeholders", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_role(client, headers, stakeholder_key, role, *, scope_kind="system", scope_ref="", note="", expect=201):
    r = client.post(
        f"/stakeholder-network/stakeholders/{stakeholder_key}/roles",
        json={"role": role, "scope_kind": scope_kind, "scope_ref": scope_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_need(client, headers, need_key, stakeholder_key, *, expect=201, **fields):
    payload = {"need_key": need_key, "stakeholder_key": stakeholder_key, "need_kind": "unmet_need",
               "statement": "statement", "rationale": ""}
    payload.update(fields)
    r = client.post("/stakeholder-network/needs", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_need_revision(client, headers, need_key, *, expect=201, **fields):
    payload = {"statement": "changed statement", "rationale": "", "change_note": ""}
    payload.update(fields)
    r = client.post(f"/stakeholder-network/needs/{need_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_exchange(client, headers, exchange_key, provider, receiver, exchange_kind, *, expect=201, **fields):
    payload = {
        "exchange_key": exchange_key, "provider_stakeholder_key": provider,
        "receiver_stakeholder_key": receiver, "exchange_kind": exchange_kind,
        "value_statement": "value", "consideration_state": "unknown", "consideration_kind": None,
        "consideration_statement": "", "channel": "", "trigger": "", "cadence": "unknown",
        "valid_from": None, "valid_to": None,
    }
    payload.update(fields)
    r = client.post("/stakeholder-network/exchanges", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_ref(client, headers, source_kind, source_key, ref_kind, target_ref, *, note="", expect=201):
    r = client.post(
        "/stakeholder-network/refs",
        json={"source_kind": source_kind, "source_key": source_key, "ref_kind": ref_kind,
              "target_ref": target_ref, "note": note},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_evidence_ref(client, headers, subject_kind, subject_key, evidence_kind, *, evidence_ref="", statement="", expect=201):
    r = client.post(
        "/stakeholder-network/evidence-refs",
        json={"subject_kind": subject_kind, "subject_key": subject_key, "evidence_kind": evidence_kind,
              "evidence_ref": evidence_ref, "statement": statement},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _decide(client, headers, subject_kind, subject_key, decision, *, rationale="", captured_digest="", expect=201):
    r = client.post(
        "/stakeholder-network/decisions",
        json={"subject_kind": subject_kind, "subject_key": subject_key, "decision": decision,
              "rationale": rationale, "captured_digest": captured_digest},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _get_need_digest(client, headers, need_key):
    r = client.get(f"/stakeholder-network/needs/{need_key}", headers=headers)
    assert r.status_code == 200, r.text
    rev = r.json().get("current_revision") or {}
    return rev.get("content_digest", "")


def _step(step_key, order, **overrides):
    base = {
        "step_key": step_key, "step_order": order, "user_intent": "intent",
        "system_response": "response", "success_criteria": "criteria",
        "failure_mode": "", "recovery_path": "", "evidence_expectation": "",
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


def _criterion(criterion_key, order, **overrides):
    base = {
        "criterion_key": criterion_key, "criterion_order": order, "statement": "stmt",
        "verification_method": "manual_review", "verification_note": "",
    }
    base.update(overrides)
    return base


def _add_requirement_revision(client, headers, requirement_key, *, acceptance_criteria=None, expect=201, **fields):
    payload = {
        "statement": "statement", "rationale": "", "constraint_text": "", "out_of_scope_note": "",
        "change_note": "", "acceptance_criteria": acceptance_criteria or [],
    }
    payload.update(fields)
    r = client.post(f"/ux-design/requirements/{requirement_key}/revisions", json=payload, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_requirement_step_link(client, headers, requirement_key, journey_key, step_key, *, expect=201):
    r = client.post(
        f"/ux-design/requirements/{requirement_key}/step-links",
        json={"journey_key": journey_key, "step_key": step_key, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_solution_design(client, headers, design_key, *, expect=201):
    r = client.post("/solution-designs", json={"design_key": design_key, "title": "", "summary": ""}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_option(client, headers, design_key, option_key, *, order=0, expect=201):
    r = client.post(
        f"/solution-designs/{design_key}/options",
        json={"option_key": option_key, "option_order": order, "title": "", "approach": "", "tradeoffs": "", "risks": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_requirement_link(client, headers, design_key, requirement_key, *, expect=201):
    r = client.post(
        f"/solution-designs/{design_key}/requirement-links",
        json={"requirement_key": requirement_key, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_target_link(client, headers, design_key, option_key, target_kind, target_ref, *,
                     captured_snapshot_id=None, expect=201):
    r = client.post(
        f"/solution-designs/{design_key}/target-links",
        json={"option_key": option_key, "target_kind": target_kind, "target_ref": target_ref,
              "captured_snapshot_id": captured_snapshot_id, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _record_option_decision(client, headers, design_key, option_key, decision, *, expect=201):
    r = client.post(
        f"/solution-designs/{design_key}/decisions",
        json={"option_key": option_key, "decision": decision, "rationale": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_evolution_node(client, headers, node_key, *, expect=201):
    r = client.post("/evolution-nodes", json={"node_key": node_key, "display_name": node_key}, headers=headers)
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _add_node_link(client, headers, node_id, link_kind, target_ref, *, expect=201):
    r = client.post(
        f"/evolution-nodes/{node_id}/links",
        json={"link_kind": link_kind, "target_ref": target_ref, "target_row_id": None, "note": ""},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _create_evaluation_policy(client, headers, policy_key, level, subject_ref, *, expect=201):
    r = client.post(
        "/node-design/evaluation-policies",
        json={"policy_key": policy_key, "level": level, "title": "", "subject_ref": subject_ref,
              "criteria": [], "floors": [], "unmeasured": []},
        headers=headers,
    )
    assert r.status_code == expect, r.text
    return r.json() if expect < 300 else r


def _insert_trace_span_for_flow(system_id, flow_id):
    """A minimal `trace_spans` row -- the only fact `node_design._resolve_flow`
    (and this module's `_flow_node_keys` reverse query, indirectly) reads to
    decide a runtime Flow `observed`."""
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trace_spans (system_id, trace_id, component_id, span_id, flow_id, timestamp)
               VALUES (?, ?, 'comp1', 'span-1', ?, ?)""",
            (system_id, f"trace-for-{flow_id}", flow_id, now),
        )
        conn.commit()


def _get_functional_lineage(client, headers, expect=200):
    r = client.get("/functional-lineage", headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _gaps_by_code(result):
    out = {}
    for g in result["gaps"]:
        out.setdefault(g["code"], []).append(g)
    return out


# ---------------------------------------------------------------------------
# Part 1: fixed severity + type parity sanity
# ---------------------------------------------------------------------------


class TestGapSeverityFixed:
    def test_every_gap_code_has_a_fixed_severity(self):
        for code in fl.GAP_CODES:
            assert fl._GAP_SEVERITY[code] in ("blocking", "attention", "informational")

    def test_severity_is_a_property_of_the_code_not_the_instance(self, admin_client):
        """The SAME code must carry the SAME severity across two entirely
        unrelated subjects -- severity is never computed per instance
        (invariant 7)."""
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _create_stakeholder(admin_client, headers, "sh2")
        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        role_gaps = by_code.get("stakeholder_without_role", [])
        assert len(role_gaps) == 2
        assert {g["severity"] for g in role_gaps} == {"attention"}


# ---------------------------------------------------------------------------
# Part 2: no LLM / no score / write-nothing regressions (Epic-wide, §15)
# ---------------------------------------------------------------------------


class TestNoLLMAnywhereInTheEpic:
    """§15 item 7, extended across every module this Epic's projections
    touch (stakeholder_network / stakeholder_value_network /
    journey_blueprint / functional_lineage + their routes)."""

    MODULES = [sn, svn, journey_blueprint, fl]

    def test_no_llm_import_or_call(self):
        for module in self.MODULES:
            source = inspect.getsource(module)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "llm" in node.module:
                    pytest.fail(f"{module.__name__} imports from {node.module!r}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "llm" not in alias.name, f"{module.__name__} imports {alias.name!r}"
                if isinstance(node, ast.Name) and "llm" in node.id.lower():
                    pytest.fail(f"{module.__name__} references {node.id!r}")

    def test_route_file_has_no_llm_import(self):
        from app.routes import functional_lineage as fl_route

        tree = ast.parse(inspect.getsource(fl_route))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "llm" in node.module:
                pytest.fail(f"routes.functional_lineage imports from {node.module!r}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "llm" not in alias.name


class TestNoWeightedScoreAnywhere:
    def test_no_score_percentage_or_ranking_field(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        result = _get_functional_lineage(admin_client, headers)
        blob = str(result).lower()
        for banned in ("score", "percentage", "confidence", "ranking", "weight"):
            assert banned not in blob, f"found banned term {banned!r} in response"

    def test_get_writes_nothing(self, admin_client):
        """A page view is never a decision (#382) -- calling the endpoint
        twice must never create or mutate any row this Epic owns."""
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        first = _get_functional_lineage(admin_client, headers)
        second = _get_functional_lineage(admin_client, headers)
        assert first["nodes"] == second["nodes"]
        assert first["gaps"] == second["gaps"]


class TestSyntheticOutcomeNeverConfirmed:
    def test_runtime_evidence_never_changes_design_status(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")
        _create_need(admin_client, headers, "need1", "sh1")
        before = admin_client.get("/stakeholder-network/needs/need1", headers=headers).json()
        assert before["design_status"] == "proposed"

        _create_evidence_ref(admin_client, headers, "stakeholder_need", "need1", "runtime_observation", statement="observed in trace")
        after = admin_client.get("/stakeholder-network/needs/need1", headers=headers).json()
        assert after["design_status"] == "proposed"


class TestSystemIsolation:
    def test_lineage_is_scoped_per_system(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "System A")
        system_b = _create_system(admin_client, token, "System B")
        headers_a = _headers(token, system_a)
        headers_b = _headers(token, system_b)

        _create_stakeholder(admin_client, headers_a, "only-in-a")
        result_a = _get_functional_lineage(admin_client, headers_a)
        result_b = _get_functional_lineage(admin_client, headers_b)

        assert any(n["ref"] == "only-in-a" for n in result_a["nodes"])
        assert not any(n["ref"] == "only-in-a" for n in result_b["nodes"])


# ---------------------------------------------------------------------------
# Part 3: downstream-only impact traversal (§9.3)
# ---------------------------------------------------------------------------


class TestDownstreamOnlyImpact:
    def test_impact_never_walks_upstream(self):
        edges = [
            {"from_kind": "stakeholder", "from_ref": "sh1", "to_kind": "stakeholder_need", "to_ref": "n1"},
            {"from_kind": "stakeholder_need", "from_ref": "n1", "to_kind": "value_exchange", "to_ref": "ex1"},
        ]
        downstream_of_root = fl.trace_downstream_impact(edges, "stakeholder", "sh1")
        assert downstream_of_root == [
            {"kind": "stakeholder_need", "ref": "n1"},
            {"kind": "value_exchange", "ref": "ex1"},
        ]
        # Starting from the most downstream node must find nothing upstream.
        downstream_of_leaf = fl.trace_downstream_impact(edges, "value_exchange", "ex1")
        assert downstream_of_leaf == []


# ---------------------------------------------------------------------------
# Part 4: every §9.2 gap code is reachable (§15 item "Every gap code
# reachable"). Grouped by how cheaply they can be constructed.
# ---------------------------------------------------------------------------


class TestEveryGapCodeReachable:
    def test_stakeholder_and_need_level_codes(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")  # no role, no need
        _create_stakeholder(admin_client, headers, "sh2")
        _add_role(admin_client, headers, "sh2", "beneficiary")
        _create_need(admin_client, headers, "need1", "sh2")  # no purpose ref, no exchange, no journey

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "sh1" for g in by_code.get("stakeholder_without_role", []))
        assert any(g["subject_ref"] == "sh1" for g in by_code.get("stakeholder_without_need", []))
        assert any(g["subject_ref"] == "need1" for g in by_code.get("need_without_purpose", []))
        assert any(g["subject_ref"] == "need1" for g in by_code.get("need_without_exchange", []))

    def test_need_without_journey_when_exchange_has_no_journey_ref(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_need(admin_client, headers, "need1", "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "service")
        _create_ref(admin_client, headers, "value_exchange", "ex1", "stakeholder_need", "need1")

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "need1" for g in by_code.get("need_without_journey", []))
        assert any(g["subject_ref"] == "ex1" for g in by_code.get("exchange_without_journey", []))
        assert any(g["subject_ref"] == "ex1" for g in by_code.get("exchange_without_outcome", []))

    def test_journey_step_without_requirement(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "j1#s1" for g in by_code.get("journey_step_without_requirement", []))

    def test_requirement_without_acceptance_criterion_and_without_design(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")
        _create_requirement(admin_client, headers, "req1")
        _add_requirement_revision(admin_client, headers, "req1")  # no acceptance criteria
        _add_requirement_step_link(admin_client, headers, "req1", "j1", "s1")

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "req1" for g in by_code.get("requirement_without_acceptance_criterion", []))
        assert any(g["subject_ref"] == "req1" for g in by_code.get("requirement_without_design", []))

    def test_adopted_design_without_implementation_target(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")
        _create_requirement(admin_client, headers, "req1")
        _add_requirement_revision(admin_client, headers, "req1", acceptance_criteria=[_criterion("c1", 0)])
        _add_requirement_step_link(admin_client, headers, "req1", "j1", "s1")
        _create_solution_design(admin_client, headers, "d1")
        _add_option(admin_client, headers, "d1", "optA")
        _add_requirement_link(admin_client, headers, "d1", "req1")
        _record_option_decision(admin_client, headers, "d1", "optA", "adopt")

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "d1" for g in by_code.get("adopted_design_without_implementation_target", []))

    def test_node_without_flow_and_missing_evaluation_policy(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")
        _create_requirement(admin_client, headers, "req1")
        _add_requirement_revision(admin_client, headers, "req1", acceptance_criteria=[_criterion("c1", 0)])
        _add_requirement_step_link(admin_client, headers, "req1", "j1", "s1")
        _create_solution_design(admin_client, headers, "d1")
        _add_option(admin_client, headers, "d1", "optA")
        _add_requirement_link(admin_client, headers, "d1", "req1")
        _create_evolution_node(admin_client, headers, "node1")
        _add_target_link(admin_client, headers, "d1", "optA", "evolution_node", "node1")
        _record_option_decision(admin_client, headers, "d1", "optA", "adopt")
        # No flow link, no evaluation policy for node1.

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "node1" for g in by_code.get("node_without_flow", []))
        assert any(g["subject_ref"] == "node1" for g in by_code.get("subject_without_evaluation_policy", []))

    def test_flow_without_node_and_missing_evaluation_policy(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")
        _create_requirement(admin_client, headers, "req1")
        _add_requirement_revision(admin_client, headers, "req1", acceptance_criteria=[_criterion("c1", 0)])
        _add_requirement_step_link(admin_client, headers, "req1", "j1", "s1")
        _create_solution_design(admin_client, headers, "d1")
        _add_option(admin_client, headers, "d1", "optA")
        _add_requirement_link(admin_client, headers, "d1", "req1")
        _insert_trace_span_for_flow(system_id, "flow-unlinked")
        _add_target_link(admin_client, headers, "d1", "optA", "runtime_flow", "flow-unlinked")
        _record_option_decision(admin_client, headers, "d1", "optA", "adopt")
        # No Node links this flow, no evaluation policy for it either.

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "flow-unlinked" for g in by_code.get("flow_without_node", []))
        assert any(g["subject_ref"] == "flow-unlinked" for g in by_code.get("subject_without_evaluation_policy", []))

    def test_stale_confirmed_evidence_and_feedback_codes_via_value_network_reuse(self, admin_client):
        """`stakeholder_without_role` / `stakeholder_without_need` /
        `exchange_without_journey` / `exchange_without_outcome` /
        `confirmed_without_evidence` / `feedback_path_missing` /
        `stale_link` / `stale_upstream` are all reused verbatim from
        `stakeholder_value_network`'s own notices (see
        `_VALUE_NETWORK_NOTICE_TO_GAP`) -- this test exercises the full set
        through ONE realistic scenario rather than one test each."""
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _add_role(admin_client, headers, "provider1", "supplier")
        _add_role(admin_client, headers, "user1", "beneficiary")
        _create_need(admin_client, headers, "need1", "user1")
        _decide(admin_client, headers, "stakeholder_need", "need1", "confirm")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_ref(admin_client, headers, "value_exchange", "ex1", "stakeholder_need", "need1")
        _decide(admin_client, headers, "value_exchange", "ex1", "confirm")
        # Confirmed but with no evidence anywhere -> confirmed_without_evidence
        # (both on the Need and on the Exchange), and no feedback path from
        # user1 back to provider1 -> feedback_path_missing.

        result_before = _get_functional_lineage(admin_client, headers)
        by_code_before = _gaps_by_code(result_before)
        assert any(g["subject_ref"] == "need1" for g in by_code_before.get("confirmed_without_evidence", []))
        assert any(g["subject_ref"] == "ex1" for g in by_code_before.get("confirmed_without_evidence", []))
        assert any(g["subject_ref"] == "provider1" for g in by_code_before.get("feedback_path_missing", []))

        # Now change the Need's content WITHOUT a new decision -- its own
        # confirmed judgement is now stale (stale_upstream), and the
        # Exchange's ref to it is stale too (stale_link).
        _add_need_revision(admin_client, headers, "need1")
        result_after = _get_functional_lineage(admin_client, headers)
        by_code_after = _gaps_by_code(result_after)
        assert any(g["subject_ref"] == "need1" for g in by_code_after.get("stale_upstream", []))
        assert any(g["subject_ref"] == "ex1" for g in by_code_after.get("stale_link", []))

    def test_stale_evidence(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "user1")
        _create_need(admin_client, headers, "need1", "user1")
        _create_evidence_ref(admin_client, headers, "stakeholder_need", "need1", "human_report", statement="a report")
        _add_need_revision(admin_client, headers, "need1")

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "need1" for g in by_code.get("stale_evidence", []))

    def test_rejected_dependency(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_need(admin_client, headers, "need1", "user1")
        _decide(admin_client, headers, "stakeholder_need", "need1", "reject")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_ref(admin_client, headers, "value_exchange", "ex1", "stakeholder_need", "need1")

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "ex1" for g in by_code.get("rejected_dependency", []))

    def test_conflicting_dependency(self, admin_client, monkeypatch):
        """A minimal, isolated exercise of `_walk_need_purpose_refs` against
        a fabricated `PurposeChainResult` -- building a real `conflicting`
        Purpose relation end to end requires a full Intent Brief + reviewer
        setup that belongs to a different Epic's own tests; this module's
        contract is only that it reads `chain.relations[*].status`
        correctly, which this test verifies directly."""
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "user1")
        _create_need(admin_client, headers, "need1", "user1")
        # A real, currently-resolvable relation id (the fixed Purpose Frame
        # always derives this pair, even with `status="unknown"` when no
        # Intent Brief exists yet) -- ref CREATION validates resolution
        # against the real `derive_purpose_chain`, so a fabricated id would
        # be rejected 404 before the monkeypatch below ever runs.
        relation_id = "problem_to_change:beneficiary_problem->desired_change"
        _create_ref(admin_client, headers, "stakeholder_need", "need1", "purpose_relation", relation_id)

        chain = purpose_chain.PurposeChainResult(
            system_id=system_id, session_id=None, generated_at=time.time(),
            frame={}, elements=[],
            relations=[
                purpose_chain.PurposeRelation(
                    id=relation_id, kind="problem_to_change", source_id="beneficiary_problem",
                    target_id="desired_change", status="conflicting", status_label="矛盾あり",
                    recheck_state="current", stale_reason=None, provenance="manual", provenance_label="",
                )
            ],
            frame_resolution_level="none", frame_state="unknown",
            snapshot_id=None, understanding_revision_id=None, understanding_confirmed_at=None,
        )
        monkeypatch.setattr(fl.purpose_chain, "derive_purpose_chain", lambda *a, **k: chain)

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "need1" for g in by_code.get("conflicting_dependency", []))

    def test_unresolved_reference(self, admin_client):
        """A ref can only be CREATED against a target that resolves at that
        moment (§5.1's 404 `stakeholder_ref_target_not_found`) -- so
        `unresolved_reference` is reached by making a PREVIOUSLY resolving
        target stop resolving, not by pointing at one that never existed.
        A Journey Step scoped to the CURRENT revision only (§5.1) is the
        cheapest way to do that: a later revision that drops the step makes
        the existing ref's target unresolved."""
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")
        # A new revision that drops step s1 -- the ref's target no longer
        # resolves against the journey's CURRENT revision.
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s2", 0)])

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "ex1" for g in by_code.get("unresolved_reference", []))

    def test_unavailable_reference(self, admin_client, monkeypatch):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_need(admin_client, headers, "need1", "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_ref(admin_client, headers, "value_exchange", "ex1", "stakeholder_need", "need1")

        original = sn.get_exchange_lineage

        def _boom(conn, system_id_, exchange_key):
            result = original(conn, system_id_, exchange_key)
            result["degraded_sections"] = ["needs"]
            return result

        monkeypatch.setattr(fl.sn, "get_exchange_lineage", _boom)

        result = _get_functional_lineage(admin_client, headers)
        by_code = _gaps_by_code(result)
        assert any(g["subject_ref"] == "ex1" for g in by_code.get("unavailable_reference", []))


# ---------------------------------------------------------------------------
# Part 5: partial-failure degradation (§15 item 9 / #380's discipline)
# ---------------------------------------------------------------------------


class TestPartialFailureDegradesIndependently:
    def test_a_failing_section_never_substitutes_a_guessed_value(self, admin_client, monkeypatch):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "sh1")

        def _boom(conn, system_id_):
            raise RuntimeError("simulated read failure")

        monkeypatch.setattr(fl.node_design, "list_evaluation_policies", _boom)

        result = _get_functional_lineage(admin_client, headers)
        assert "evaluation_policies" in result["degraded_sections"]
        # The rest of the projection still loaded -- the Stakeholder node is
        # still present, never dropped because an unrelated section failed.
        assert any(n["kind"] == "stakeholder" and n["ref"] == "sh1" for n in result["nodes"])


# ---------------------------------------------------------------------------
# Part 6: the Epic #418 end-to-end fixture (§15 item 10)
# ---------------------------------------------------------------------------


class TestEndToEndFixture:
    """Builds the representative scenario the Epic exists to support, then
    verifies the full traversal AND every #418 完了条件 (acceptance
    condition) against it."""

    def _build(self, admin_client):
        token, system_id = _setup(admin_client, name="System E2E")
        headers = _headers(token, system_id)

        # --- Stakeholders: 利用者 / 購入責任者 / 運用担当者 / 提供者 ---
        _create_stakeholder(admin_client, headers, "user1", display_name="利用者", stakeholder_kind="end_user")
        _create_stakeholder(admin_client, headers, "payer1", display_name="購入責任者", stakeholder_kind="customer_organization")
        _create_stakeholder(admin_client, headers, "ops1", display_name="運用担当者", stakeholder_kind="internal_operator")
        _create_stakeholder(admin_client, headers, "provider1", display_name="提供者", stakeholder_kind="provider_team")
        _add_role(admin_client, headers, "user1", "beneficiary")
        _add_role(admin_client, headers, "payer1", "payer")
        _add_role(admin_client, headers, "ops1", "operator")
        _add_role(admin_client, headers, "provider1", "supplier")

        # --- Need, attributed to the beneficiary, referencing Purpose ---
        _create_need(admin_client, headers, "need1", "user1", statement="契約内容をオンラインで確認したい")
        _create_ref(admin_client, headers, "stakeholder_need", "need1", "purpose_element", "beneficiary_problem")
        _decide(admin_client, headers, "stakeholder_need", "need1", "confirm")

        # --- Exchanges: service+experience, money, information, obligation.
        # payer1 (購入責任者) pays provider1, while user1 (利用者) is the
        # one who receives the experience/service -- payer != beneficiary.
        _create_exchange(admin_client, headers, "ex_experience", "provider1", "user1", "experience",
                          value_statement="契約内容確認の体験を提供する")
        _create_exchange(admin_client, headers, "ex_service", "ops1", "user1", "service",
                          value_statement="運用担当者が確認作業を代行する")
        _create_exchange(admin_client, headers, "ex_money", "payer1", "provider1", "money",
                          value_statement="利用料を支払う", consideration_state="present",
                          consideration_kind="service", consideration_statement="サービス提供への対価")
        _create_exchange(admin_client, headers, "ex_info", "user1", "provider1", "information",
                          value_statement="利用状況をフィードバックする")
        _create_exchange(admin_client, headers, "ex_obligation", "provider1", "ops1", "obligation",
                          value_statement="SLA遵守を約束する")

        _create_ref(admin_client, headers, "value_exchange", "ex_experience", "stakeholder_need", "need1")
        _decide(admin_client, headers, "value_exchange", "ex_experience", "confirm")

        # --- Human AND runtime evidence for the Need ---
        _create_evidence_ref(admin_client, headers, "stakeholder_need", "need1", "human_report", statement="developer interview")
        _create_evidence_ref(admin_client, headers, "stakeholder_need", "need1", "runtime_observation", statement="observed in production traces")

        # --- as-is and to-be Journey ---
        _create_journey(admin_client, headers, "as_is1", perspective="as_is")
        _add_journey_revision(admin_client, headers, "as_is1", steps=[_step("s1", 0, user_intent="紙で契約内容を確認する")])
        as_is_id = admin_client.get("/ux-design/journeys/as_is1", headers=headers).json()["id"]
        _create_journey(admin_client, headers, "to_be1", perspective="to_be", baseline_mode="linked", baseline_journey_id=as_is_id)
        _add_journey_revision(
            admin_client, headers, "to_be1",
            steps=[
                _step("s1", 0, user_intent="オンラインで契約内容を確認する"),
                _step("s2", 1, user_intent="運用担当者が確認作業を代行する"),
            ],
        )
        _create_ref(admin_client, headers, "value_exchange", "ex_experience", "ux_journey_step", "to_be1#s1")
        # ex_service also reaches the Journey, through its OWN Step -- this
        # is what makes req2 (linked to s2 below) reachable from the
        # Exchange-driven traversal at all.
        _create_ref(admin_client, headers, "value_exchange", "ex_service", "ux_journey_step", "to_be1#s2")

        # --- Requirements (with acceptance criteria) ---
        _create_requirement(admin_client, headers, "req1")
        _add_requirement_revision(admin_client, headers, "req1", acceptance_criteria=[_criterion("c1", 0)])
        _add_requirement_step_link(admin_client, headers, "req1", "to_be1", "s1")

        _create_requirement(admin_client, headers, "req2")  # deliberately left thin -> gaps below
        _add_requirement_revision(admin_client, headers, "req2")  # no acceptance criteria
        _add_requirement_step_link(admin_client, headers, "req2", "to_be1", "s2")

        # --- Solution Design Option, adopted, with a Flow and a Node ---
        _create_solution_design(admin_client, headers, "d1")
        _add_option(admin_client, headers, "d1", "optA")
        _add_requirement_link(admin_client, headers, "d1", "req1")
        _create_evolution_node(admin_client, headers, "node1")
        node1 = admin_client.get("/evolution-nodes", headers=headers).json()
        node1_id = next(n["id"] for n in node1["nodes"] if n["node_key"] == "node1")
        _insert_trace_span_for_flow(system_id, "flow-main")
        _add_node_link(admin_client, headers, node1_id, "flow", "flow-main")
        _add_target_link(admin_client, headers, "d1", "optA", "evolution_node", "node1")
        _record_option_decision(admin_client, headers, "d1", "optA", "adopt")
        _create_evaluation_policy(admin_client, headers, "node1-policy", "node", "node1")
        _create_evaluation_policy(admin_client, headers, "flow-main-policy", "flow_capability", "flow-main")

        # --- A second design, deliberately incomplete: an UNCONNECTED Node
        # (no Flow link) and an UNMEASURED subject (no evaluation policy).
        _create_solution_design(admin_client, headers, "d2")
        _add_option(admin_client, headers, "d2", "optB")
        _add_requirement_link(admin_client, headers, "d2", "req2")
        _create_evolution_node(admin_client, headers, "node2")
        _add_target_link(admin_client, headers, "d2", "optB", "evolution_node", "node2")
        _record_option_decision(admin_client, headers, "d2", "optB", "adopt")

        # --- Outcome ---
        from app.db import get_conn

        now = time.time()
        with get_conn() as conn:
            snapshot_id = conn.execute(
                """INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '', 'deadbeef', 'ready', ?, ?)""",
                (system_id, now, now),
            ).lastrowid
            session_id = conn.execute(
                """INSERT INTO interview_session (system_id, snapshot_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (system_id, snapshot_id, now, now),
            ).lastrowid
            criterion_id = conn.execute(
                """INSERT INTO purpose_outcome_criterion
                       (system_id, session_id, target_kind, target_id, target_digest, source_need_id,
                        source_need_code, measure, created_at)
                   VALUES (?, ?, 'element', 'beneficiary_problem', 'd', 'need1', 'code1', 'measure', ?)""",
                (system_id, session_id, now),
            ).lastrowid
            conn.commit()
        _create_ref(admin_client, headers, "value_exchange", "ex_experience", "purpose_outcome_criterion", str(criterion_id))

        return headers, system_id, criterion_id

    def test_full_traversal_and_every_418_acceptance_condition(self, admin_client):
        headers, system_id, criterion_id = self._build(admin_client)

        # --- Upstream change producing `stale` ---
        _add_need_revision(admin_client, headers, "need1", statement="新しい契約内容の確認方法を知りたい")

        # === §7 / §8 projections still work over the same fixture ===
        vn = admin_client.get("/stakeholder-value-network", headers=headers).json()
        assert vn["nodes"] and vn["edges"]
        assert any(n["code"] == "payer_differs_from_beneficiary" for n in vn["notices"])

        blueprint = admin_client.get("/journey-blueprint?journey_key=to_be1", headers=headers).json()
        assert blueprint["steps"][0]["lanes"]["stakeholder_action"]["state"] == "present"

        # === §9's Functional Lineage projection ===
        result = _get_functional_lineage(admin_client, headers)
        assert result["degraded_sections"] == []
        nodes_by_key = {(n["kind"], n["ref"]) for n in result["nodes"]}
        edges = {(e["from_kind"], e["from_ref"], e["to_kind"], e["to_ref"]) for e in result["edges"]}
        by_code = _gaps_by_code(result)

        # --- AC: full traversal Stakeholder -> Exchange -> Journey ->
        # Requirement -> Design -> Flow/Node -> Evidence/Outcome works ---
        assert ("stakeholder", "user1") in nodes_by_key
        assert ("stakeholder_need", "need1") in nodes_by_key
        assert ("value_exchange", "ex_experience") in nodes_by_key
        assert ("ux_journey_step", "to_be1#s1") in nodes_by_key
        assert ("ux_requirement", "req1") in nodes_by_key
        assert ("solution_design", "d1") in nodes_by_key
        assert ("evolution_node", "node1") in nodes_by_key
        assert ("runtime_flow", "flow-main") in nodes_by_key
        assert ("purpose_outcome_criterion", str(criterion_id)) in nodes_by_key

        assert ("stakeholder_need", "need1", "value_exchange", "ex_experience") in edges
        assert ("value_exchange", "ex_experience", "ux_journey_step", "to_be1#s1") in edges
        assert ("ux_journey_step", "to_be1#s1", "ux_requirement", "req1") in edges
        assert ("ux_requirement", "req1", "solution_design", "d1") in edges
        assert ("solution_design", "d1", "evolution_node", "node1") in edges
        assert ("evolution_node", "node1", "runtime_flow", "flow-main") in edges
        assert ("value_exchange", "ex_experience", "purpose_outcome_criterion", str(criterion_id)) in edges

        # Downstream impact from the Need reaches the whole chain.
        impact = fl.trace_downstream_impact(result["edges"], "stakeholder_need", "need1")
        impact_set = {(e["kind"], e["ref"]) for e in impact}
        assert ("value_exchange", "ex_experience") in impact_set
        assert ("evolution_node", "node1") in impact_set
        assert ("purpose_outcome_criterion", str(criterion_id)) in impact_set
        # And the impact of a downstream leaf never reaches back upstream.
        leaf_impact = fl.trace_downstream_impact(result["edges"], "purpose_outcome_criterion", str(criterion_id))
        assert leaf_impact == []

        # --- AC: 利用者/購入責任者/運用担当者/提供者 all present ---
        for key in ("user1", "payer1", "ops1", "provider1"):
            assert ("stakeholder", key) in nodes_by_key

        # --- AC: service+experience, money, information, obligation exchanges ---
        assert ("value_exchange", "ex_experience") in nodes_by_key
        assert ("value_exchange", "ex_service") in nodes_by_key
        assert ("value_exchange", "ex_money") in nodes_by_key
        assert ("value_exchange", "ex_info") in nodes_by_key
        assert ("value_exchange", "ex_obligation") in nodes_by_key

        # --- AC: payer != beneficiary (reused from the Value Network) ---
        assert any(n["code"] == "payer_differs_from_beneficiary" for n in vn["notices"])

        # --- AC: as-is AND to-be Journey ---
        assert blueprint["baseline_state"] == "linked"

        # --- AC: Requirements (plural) ---
        assert ("ux_requirement", "req1") in nodes_by_key
        assert ("ux_requirement", "req2") in nodes_by_key

        # --- AC: a Solution Design Option (adopted) ---
        design_detail = admin_client.get("/solution-designs/d1", headers=headers).json()
        assert design_detail["adopted_option_key"] == "optA"

        # --- AC: a Flow and an Evolution Node ---
        assert ("runtime_flow", "flow-main") in nodes_by_key
        assert ("evolution_node", "node1") in nodes_by_key

        # --- AC: human AND runtime evidence ---
        evidence = admin_client.get(
            "/stakeholder-network/evidence-refs?subject_kind=stakeholder_need&subject_key=need1", headers=headers
        ).json()
        kinds = {e["evidence_kind"] for e in evidence["evidence_refs"]}
        assert "human_report" in kinds
        assert "runtime_observation" in kinds

        # --- AC: an Outcome ---
        assert ("purpose_outcome_criterion", str(criterion_id)) in nodes_by_key

        # --- AC: an upstream change producing `stale` ---
        assert any(g["subject_ref"] == "need1" for g in by_code.get("stale_upstream", []))
        assert any(g["subject_ref"] == "ex_experience" for g in by_code.get("stale_link", []))

        # --- AC: at least one unconnected gap (node2 has no Flow) ---
        assert any(g["subject_ref"] == "node2" for g in by_code.get("node_without_flow", []))

        # --- AC: at least one unmeasured gap (node2 has no evaluation policy) ---
        assert any(g["subject_ref"] == "node2" for g in by_code.get("subject_without_evaluation_policy", []))

        # --- node1's own chain is clean of the codes it was deliberately
        # set up to avoid (it has both a Flow link and an evaluation policy).
        assert not any(g["subject_ref"] == "node1" for g in by_code.get("node_without_flow", []))
        assert not any(g["subject_ref"] == "node1" for g in by_code.get("subject_without_evaluation_policy", []))
        assert not any(g["subject_ref"] == "flow-main" for g in by_code.get("flow_without_node", []))
        assert not any(g["subject_ref"] == "flow-main" for g in by_code.get("subject_without_evaluation_policy", []))

        # --- No score/percentage/ranking anywhere in this real response ---
        blob = str(result).lower()
        for banned in ("score", "percentage", "confidence", "ranking"):
            assert banned not in blob


class TestStaticAndRuntimeFlowAreNeverOneEntity:
    """§9.1: "Static Flow and runtime Flow are never one entity."

    The E2E fixture exercises `runtime_flow` only (a `static_flow` needs a
    pinned snapshot plus a `code_entrypoints` row), and both kinds share
    `_check_flow_hop`. A shared code path is exactly the condition under
    which two identities quietly become one, so this asserts the separation
    directly rather than inferring it from the fixture.

    The same `target_ref` is deliberately used for both: if the projection
    keyed a Flow node on its ref alone, the two would collapse into a single
    node and this test would fail. #405 and #412 both had to write this rule
    down after the fact; here it is a regression test.
    """

    def test_same_ref_under_both_kinds_stays_two_distinct_nodes(self, admin_client):
        token, system_id = _setup(admin_client)
        headers = _headers(token, system_id)
        _create_stakeholder(admin_client, headers, "provider1")
        _create_stakeholder(admin_client, headers, "user1")
        _create_exchange(admin_client, headers, "ex1", "provider1", "user1", "experience")
        _create_journey(admin_client, headers, "j1")
        _add_journey_revision(admin_client, headers, "j1", steps=[_step("s1", 0)])
        _create_ref(admin_client, headers, "value_exchange", "ex1", "ux_journey_step", "j1#s1")
        _create_requirement(admin_client, headers, "req1")
        _add_requirement_revision(admin_client, headers, "req1", acceptance_criteria=[_criterion("c1", 0)])
        _add_requirement_step_link(admin_client, headers, "req1", "j1", "s1")
        _create_solution_design(admin_client, headers, "d1")
        _add_option(admin_client, headers, "d1", "optA")
        _add_requirement_link(admin_client, headers, "d1", "req1")

        # One name, two different kinds of thing. A `static_flow` link needs
        # a pinned snapshot (#412's `static_flow_snapshot_required`), which is
        # itself part of why the two identities must not merge: one is pinned
        # to a commit, the other is an observed runtime correlation.
        from app.db import get_conn

        shared_ref = "flow-main"
        _insert_trace_span_for_flow(system_id, shared_ref)
        with get_conn() as conn:
            now = time.time()
            cur = conn.execute(
                """INSERT INTO repository_snapshots
                       (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/repo', 'c-flow', 'ready', ?, ?)""",
                (system_id, now, now),
            )
            snapshot_id = cur.lastrowid
            conn.execute(
                """INSERT INTO code_entrypoints
                       (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                        handler_path, handler_qualified_name, line_start, line_end,
                        route_method, route_path, created_at)
                   VALUES (?, ?, 'http_route', ?, 'api', 'Shared name',
                           'app/x.py', 'app.x.handler', 1, 10, 'POST', '/x', ?)""",
                (system_id, snapshot_id, shared_ref, now),
            )
        _add_target_link(admin_client, headers, "d1", "optA", "runtime_flow", shared_ref)
        _add_target_link(
            admin_client, headers, "d1", "optA", "static_flow", shared_ref,
            captured_snapshot_id=snapshot_id,
        )
        _record_option_decision(admin_client, headers, "d1", "optA", "adopt")

        result = _get_functional_lineage(admin_client, headers)
        kinds_for_ref = sorted(
            node["kind"] for node in result["nodes"] if node["ref"] == shared_ref
        )
        assert kinds_for_ref == ["runtime_flow", "static_flow"], (
            "a static Flow and a runtime Flow sharing a ref must remain two "
            "nodes; collapsing them would make the projection assert that a "
            "code path and an observed correlation are the same entity"
        )

        # And the design reaches BOTH, rather than whichever was walked last.
        edges = {(e["from_kind"], e["from_ref"], e["to_kind"], e["to_ref"]) for e in result["edges"]}
        assert ("solution_design", "d1", "runtime_flow", shared_ref) in edges
        assert ("solution_design", "d1", "static_flow", shared_ref) in edges
