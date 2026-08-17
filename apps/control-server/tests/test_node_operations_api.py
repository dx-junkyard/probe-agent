"""API tests for Epic #394 Phase 5 (Issue #400): the operations routes.

`test_node_operations.py` covers the domain layer; this file covers only what
the HTTP boundary itself decides:

1. the whole surface end to end over HTTP -- contract -> observation ->
   anomaly -> reopen scope -> plan -> approval -> projection;
2. that the approver and the anomaly's `decision_method` come from the route
   and the authenticated principal, never from the request body (#337);
3. that a deduplicated anomaly is a 200 that creates nothing, not a conflict;
4. System isolation on every endpoint -- a contract, anomaly, plan or Node in
   another System is indistinguishable from one that does not exist;
5. the domain layer's error classes reaching HTTP as 404 / 409 / 422;
6. that a read endpoint writes nothing, and that `maturity` and `observation`
   come back as two independent readings (ADR-5);
7. that the finite vocabularies in `app/models.py` and
   `app/node_operations.py` have not drifted apart.
"""

from __future__ import annotations

from typing import get_args

import pytest
from fastapi.testclient import TestClient

from app import evolution_node, node_operations
from app.models import (
    OperationsAnomalyClassification,
    OperationsAnomalySeverity,
    OperationsIndicatorKind,
    OperationsObservationState,
    OperationsReopenStatus,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "node-operations-api-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_headers(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _create_node(client, token, system_id, node_key):
    r = client.post(
        "/evolution-nodes",
        json={"node_key": node_key},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _established_node(client, token, system_id, node_key="operated"):
    """Drive a Node to `established` -- the state Phase 5 operates on.

    Everything a caller can legitimately do over HTTP is done over HTTP; only
    the establishment itself goes through the domain layer, because the API's
    sanctioned establishment path is Phase 4's Stabilization gate (the
    transitions endpoint refuses `-> established` directly, by design).
    """
    from app.db import get_conn

    node = _create_node(client, token, system_id, node_key)
    r = client.post(
        f"/evolution-nodes/{node['id']}/versions",
        json={
            "mission": "normalize an address string",
            "input_contract": {"address": "str"},
            "output_contract": {"normalized": "str"},
            "side_effect_class": "pure",
            "trust_boundary": "internal",
        },
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    version = r.json()

    r = client.post(
        f"/evolution-nodes/{node['id']}/implementations",
        json={"node_version_id": version["id"], "modality": "rule"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    implementation = r.json()

    r = client.post(
        f"/evolution-nodes/{node['id']}/stable-implementation",
        json={"implementation_id": implementation["id"]},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 200, r.text

    # Phase 1 refuses `established` without evidence, and the evidence refs it
    # recognises are the Node's own links: load-bearing setup, not decoration.
    r = client.post(
        f"/evolution-nodes/{node['id']}/links",
        json={"link_kind": "capability", "target_ref": f"cap-{node_key}"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text

    r = client.post(
        f"/evolution-nodes/{node['id']}/transitions",
        json={"to_state": "validating", "decision_method": "manual"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 200, r.text

    with get_conn() as conn:
        result = evolution_node.apply_transition(
            conn,
            system_id=system_id,
            node_id=node["id"],
            to_state="established",
            decision_method="manual",
            actor="fixture",
            evidence_refs=[f"capability:cap-{node_key}"],
        )
        assert result.applied, result.decision
        maturity = conn.execute(
            "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
        ).fetchone()["maturity"]
    assert maturity == "established", (
        "the fixture must actually reach `established`; otherwise every test "
        "below silently exercises a `validating` Node"
    )
    return node, implementation


def _create_contract(client, token, system_id, node_id, **overrides):
    payload = {"freshness_budget_seconds": 3600.0, "minimum_sample_count": 1}
    payload.update(overrides)
    return client.post(
        f"/node-operations/nodes/{node_id}/monitoring-contracts",
        json=payload,
        headers=_headers(token, system_id),
    )


def _record_observation(client, token, system_id, contract_id, **overrides):
    payload = {
        "indicator": "p95",
        "indicator_kind": "latency",
        "observation_state": "within_budget",
        "observed_value": 120.0,
        "sample_count": 500,
    }
    payload.update(overrides)
    return client.post(
        f"/node-operations/monitoring-contracts/{contract_id}/observations",
        json=payload,
        headers=_headers(token, system_id),
    )


def _record_anomaly(client, token, system_id, node_id, **overrides):
    payload = {"classification": "implementation_defect"}
    payload.update(overrides)
    return client.post(
        f"/node-operations/nodes/{node_id}/anomalies",
        json=payload,
        headers=_headers(token, system_id),
    )


def _create_plan(client, token, system_id, node_id, **overrides):
    payload = {"reason": "p95 drifted past the contract budget"}
    payload.update(overrides)
    return client.post(
        f"/node-operations/nodes/{node_id}/reopen-plans",
        json=payload,
        headers=_headers(token, system_id),
    )


def _maturity(node_id):
    from app.db import get_conn

    with get_conn() as conn:
        return conn.execute(
            "SELECT maturity, stable_implementation_id FROM evolution_node WHERE id = ?",
            (node_id,),
        ).fetchone()


# ---------------------------------------------------------------------------
# 1. The whole surface, end to end
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_contract_observation_anomaly_plan_and_approval_over_http(
        self, admin_client
    ):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-E2E")
        node, implementation = _established_node(admin_client, token, system_id)

        r = _create_contract(
            admin_client,
            token,
            system_id,
            node["id"],
            indicators=[{"kind": "latency", "name": "p95", "threshold": 250.0}],
            reopen_conditions=["p95 above the threshold for two windows"],
            escalation_owner="platform-team",
            sampling_note="1% of production traffic",
        )
        assert r.status_code == 201, r.text
        contract = r.json()
        assert contract["version_number"] == 1
        assert contract["node_id"] == node["id"]
        assert contract["indicators"][0]["kind"] == "latency"
        assert contract["reopen_conditions"] == [
            "p95 above the threshold for two windows"
        ]
        # A contract is a human's declaration.
        assert contract["decision_method"] == "manual"
        assert contract["created_by"] == "root"
        assert contract["superseded_by_id"] is None

        r = _record_observation(
            admin_client, token, system_id, contract["id"], last_observed_at=None
        )
        assert r.status_code == 201, r.text
        observation = r.json()
        assert observation["contract_id"] == contract["id"]
        assert observation["node_id"] == node["id"]
        assert observation["observation_state"] == "within_budget"

        r = _record_anomaly(
            admin_client,
            token,
            system_id,
            node["id"],
            classification="input_or_environment_drift",
            summary="the address format changed upstream",
            severity="attention",
            contract_id=contract["id"],
            observation_ids=[observation["id"]],
            dedupe_key="p95-drift",
        )
        assert r.status_code == 200, r.text
        recorded = r.json()
        assert recorded["created"] is True
        anomaly = recorded["anomaly"]
        assert anomaly["observation_ids"] == [observation["id"]]
        assert anomaly["status"] == "open"
        # A defect is fixed; a frame-breaking signal means the design was
        # aimed at the wrong thing. The projection has to say which.
        assert anomaly["frame_breaking"] is False

        r = admin_client.get(
            f"/node-operations/nodes/{node['id']}/reopen-scope",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        scope = r.json()
        assert scope["origin_node_id"] == node["id"]
        assert scope["scope_node_ids"] == [node["id"]]

        r = _create_plan(
            admin_client,
            token,
            system_id,
            node["id"],
            anomaly_id=anomaly["id"],
            budget_note="one sprint",
        )
        assert r.status_code == 201, r.text
        plan = r.json()
        assert plan["status"] == "proposed"
        assert plan["anomaly_id"] == anomaly["id"]
        assert plan["scope_node_ids"] == [node["id"]]
        assert plan["created_by"] == "root"
        # Proposing is not reopening.
        assert _maturity(node["id"])["maturity"] == "established"

        r = admin_client.post(
            f"/node-operations/reopen-plans/{plan['id']}/approve",
            json={"note": "agreed at the ops review"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        approval = r.json()
        assert approval["plan"]["status"] == "approved"
        assert approval["plan"]["decision_note"] == "agreed at the ops review"
        assert [result["node_id"] for result in approval["results"]] == [node["id"]]
        assert approval["results"][0]["applied"] is True

        after = _maturity(node["id"])
        assert after["maturity"] == "reopened"
        # ADR-5: production keeps running the established implementation while
        # exploration proceeds. That is what makes a reopen safe to start.
        assert after["stable_implementation_id"] == implementation["id"]

        r = admin_client.get(
            f"/node-operations/nodes/{node['id']}",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        projection = r.json()
        assert projection["node_key"] == node["node_key"]
        assert projection["monitoring_contract_id"] == contract["id"]
        assert projection["monitoring_contract_declared"] is True
        assert [o["id"] for o in projection["observations"]] == [observation["id"]]
        assert [a["id"] for a in projection["anomalies"]] == [anomaly["id"]]

    def test_a_second_contract_supersedes_the_first(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Versions")
        node, _ = _established_node(admin_client, token, system_id)

        first = _create_contract(admin_client, token, system_id, node["id"]).json()
        second = _create_contract(admin_client, token, system_id, node["id"]).json()
        assert second["version_number"] == 2

        r = admin_client.get(
            f"/node-operations/nodes/{node['id']}",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        # The projection reads the CURRENT contract, not the superseded one.
        assert r.json()["monitoring_contract_id"] == second["id"]
        assert first["id"] != second["id"]

    def test_scope_shares_a_link_target_and_lists_exclusions_explicitly(
        self, admin_client
    ):
        """A neighbour SHARES a concrete link target -- a finite, checkable
        relation, never a similarity judgement (Principle 6)."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Scope")
        origin, _ = _established_node(admin_client, token, system_id, "origin")
        neighbour, _ = _established_node(admin_client, token, system_id, "neighbour")
        stranger, _ = _established_node(admin_client, token, system_id, "stranger")

        for node_id, target in (
            (origin["id"], "svc-shared"),
            (neighbour["id"], "svc-shared"),
            (stranger["id"], "svc-other"),
        ):
            r = admin_client.post(
                f"/evolution-nodes/{node_id}/links",
                json={"link_kind": "component", "target_ref": target},
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text

        r = admin_client.get(
            f"/node-operations/nodes/{origin['id']}/reopen-scope",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        scope = r.json()
        assert set(scope["scope_node_ids"]) == {origin["id"], neighbour["id"]}
        # The unrelated Node is LISTED as excluded, never silently omitted.
        assert stranger["id"] in scope["excluded_node_ids"]
        shared = [
            entry for entry in scope["rationale"] if entry["node_id"] == neighbour["id"]
        ][0]
        assert shared["shared"] == ["component:svc-shared"]

    def test_include_neighbours_false_narrows_the_stored_plan(self, admin_client):
        """The flag must narrow the PLAN, not just a preview -- otherwise the
        scope silently widens again at approval time."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Narrow")
        origin, _ = _established_node(admin_client, token, system_id, "origin")
        neighbour, _ = _established_node(admin_client, token, system_id, "neighbour")
        for node_id in (origin["id"], neighbour["id"]):
            r = admin_client.post(
                f"/evolution-nodes/{node_id}/links",
                json={"link_kind": "component", "target_ref": "svc-shared"},
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text

        plan = _create_plan(
            admin_client, token, system_id, origin["id"], include_neighbours=False
        ).json()
        assert plan["scope_node_ids"] == [origin["id"]]
        assert neighbour["id"] in plan["excluded_node_ids"]

        r = admin_client.post(
            f"/node-operations/reopen-plans/{plan['id']}/approve",
            json={},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        assert _maturity(neighbour["id"])["maturity"] == "established"

    def test_a_blocked_node_in_scope_is_reported_not_forced(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Partial")
        origin, _ = _established_node(admin_client, token, system_id, "origin")
        neighbour, neighbour_impl = _established_node(
            admin_client, token, system_id, "neighbour"
        )
        for node_id in (origin["id"], neighbour["id"]):
            r = admin_client.post(
                f"/evolution-nodes/{node_id}/links",
                json={"link_kind": "component", "target_ref": "svc-shared"},
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text

        plan = _create_plan(admin_client, token, system_id, origin["id"]).json()
        assert set(plan["scope_node_ids"]) == {origin["id"], neighbour["id"]}

        # The neighbour is reopened on its own first, so `reopened -> reopened`
        # is illegal by the time the plan is approved.
        r = admin_client.post(
            f"/evolution-nodes/{neighbour['id']}/transitions",
            json={
                "to_state": "reopened",
                "decision_method": "manual",
                "reason": "already under re-exploration",
            },
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text

        r = admin_client.post(
            f"/node-operations/reopen-plans/{plan['id']}/approve",
            json={},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        by_node = {result["node_id"]: result for result in r.json()["results"]}
        assert by_node[origin["id"]]["applied"] is True
        blocked = by_node[neighbour["id"]]
        assert blocked["applied"] is False
        assert blocked["reason_code"] == "illegal_transition"
        assert blocked["message"]
        # Production stays pinned on every Node, blocked or not.
        assert (
            _maturity(neighbour["id"])["stable_implementation_id"]
            == neighbour_impl["id"]
        )


# ---------------------------------------------------------------------------
# 2. Two independent readings, and a read that writes nothing
# ---------------------------------------------------------------------------


class TestProjection:
    def test_established_with_dead_telemetry_reads_as_exactly_that(self, admin_client):
        """Collapsing the two into one label is the #366 one-word-two-facts
        defect: telemetry stopping does not make the fixation decision wrong."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Dead")
        node, _ = _established_node(admin_client, token, system_id)

        contract = _create_contract(
            admin_client,
            token,
            system_id,
            node["id"],
            freshness_budget_seconds=1.0,
            minimum_sample_count=1,
        ).json()
        r = _record_observation(
            admin_client,
            token,
            system_id,
            contract["id"],
            last_observed_at=1000.0,
            sample_count=100,
        )
        assert r.status_code == 201, r.text

        r = admin_client.get(
            f"/node-operations/nodes/{node['id']}",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        projection = r.json()
        assert projection["maturity"] == "established"
        assert projection["observation"]["state"] == "unobserved"

    def test_no_contract_is_not_a_monitoring_failure(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-NoContract")
        node, _ = _established_node(admin_client, token, system_id)

        r = admin_client.get(
            f"/node-operations/nodes/{node['id']}",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        projection = r.json()
        # `null` means "nothing is wired", never "monitoring failed".
        assert projection["monitoring_contract_declared"] is False
        assert projection["observation"] is None
        assert projection["observations"] == []

    def test_reading_the_projection_and_the_scope_changes_nothing(self, admin_client):
        """Looking at a Node must not move it (#380)."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-ReadOnly")
        node, _ = _established_node(admin_client, token, system_id)
        contract = _create_contract(admin_client, token, system_id, node["id"]).json()

        from app.db import get_conn

        def _counts():
            with get_conn() as conn:
                return {
                    table: conn.execute(
                        f"SELECT COUNT(*) AS n FROM {table} WHERE system_id = ?",
                        (system_id,),
                    ).fetchone()["n"]
                    for table in (
                        "node_monitoring_contract",
                        "node_drift_observation",
                        "node_anomaly",
                        "node_reopen_plan",
                        "evolution_node_event",
                    )
                }

        before = _counts()
        for _ in range(3):
            assert (
                admin_client.get(
                    f"/node-operations/nodes/{node['id']}",
                    headers=_headers(token, system_id),
                ).status_code
                == 200
            )
            assert (
                admin_client.get(
                    f"/node-operations/nodes/{node['id']}/reopen-scope",
                    headers=_headers(token, system_id),
                ).status_code
                == 200
            )
        assert _counts() == before
        assert _maturity(node["id"])["maturity"] == "established"
        assert contract["id"]


# ---------------------------------------------------------------------------
# 3. Provenance comes from the route and the principal (#337)
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_the_approver_is_the_principal_and_cannot_be_sent_in_the_body(
        self, admin_client
    ):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Approver")
        node, _ = _established_node(admin_client, token, system_id)
        plan = _create_plan(admin_client, token, system_id, node["id"]).json()

        # Claiming somebody else's approval is refused outright, not ignored.
        r = admin_client.post(
            f"/node-operations/reopen-plans/{plan['id']}/approve",
            json={"approved_by": "someone-else", "note": "n"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text

        r = admin_client.post(
            f"/node-operations/reopen-plans/{plan['id']}/approve",
            json={},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["approved_by"] == "root"
        assert r.json()["plan"]["decision_method"] == "manual"

    def test_an_anomaly_filed_over_http_is_manual_and_cannot_claim_otherwise(
        self, admin_client
    ):
        """Letting the body claim `deterministic` / `reasoning_llm` would let
        any caller forge a system- or model-produced classification with no
        `intelligence_runs` row behind it."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-AnomalyProv")
        node, _ = _established_node(admin_client, token, system_id)

        r = _record_anomaly(
            admin_client,
            token,
            system_id,
            node["id"],
            decision_method="reasoning_llm",
        )
        assert r.status_code == 422, r.text

        r = _record_anomaly(admin_client, token, system_id, node["id"])
        assert r.status_code == 200, r.text
        assert r.json()["anomaly"]["decision_method"] == "manual"
        assert r.json()["anomaly"]["intelligence_run_id"] is None

    def test_a_repeated_condition_is_a_200_that_creates_nothing(self, admin_client):
        """One continuing condition must not produce a new anomaly -- and then
        a new reopen -- on every polling cycle."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Dedupe")
        node, _ = _established_node(admin_client, token, system_id)

        first = _record_anomaly(
            admin_client, token, system_id, node["id"], dedupe_key="p95-drift"
        )
        second = _record_anomaly(
            admin_client, token, system_id, node["id"], dedupe_key="p95-drift"
        )
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["created"] is True
        assert second.json()["created"] is False
        assert second.json()["anomaly"]["id"] == first.json()["anomaly"]["id"]

        from app.db import get_conn

        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM node_anomaly WHERE node_id = ?",
                (node["id"],),
            ).fetchone()["n"]
        assert count == 1

    def test_a_frame_breaking_signal_is_marked_as_such(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Frame")
        node, _ = _established_node(admin_client, token, system_id)

        r = _record_anomaly(
            admin_client,
            token,
            system_id,
            node["id"],
            classification="purpose_or_vision_reconsideration",
        )
        assert r.status_code == 200, r.text
        assert r.json()["anomaly"]["frame_breaking"] is True


# ---------------------------------------------------------------------------
# 4. Domain errors reaching HTTP
# ---------------------------------------------------------------------------


class TestErrorMapping:
    def test_unknown_node_is_404_on_every_node_scoped_endpoint(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-404")
        missing = 999_999

        assert _create_contract(
            admin_client, token, system_id, missing
        ).status_code == 404
        assert _record_anomaly(
            admin_client, token, system_id, missing
        ).status_code == 404
        assert _create_plan(admin_client, token, system_id, missing).status_code == 404
        assert (
            admin_client.get(
                f"/node-operations/nodes/{missing}",
                headers=_headers(token, system_id),
            ).status_code
            == 404
        )
        assert (
            admin_client.get(
                f"/node-operations/nodes/{missing}/reopen-scope",
                headers=_headers(token, system_id),
            ).status_code
            == 404
        )
        assert (
            _record_observation(
                admin_client, token, system_id, missing
            ).status_code
            == 404
        )
        assert (
            admin_client.post(
                f"/node-operations/reopen-plans/{missing}/approve",
                json={},
                headers=_headers(token, system_id),
            ).status_code
            == 404
        )

    def test_a_contract_for_a_node_still_being_explored_is_409(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-TooEarly")
        node = _create_node(admin_client, token, system_id, "exploring-still")

        r = _create_contract(admin_client, token, system_id, node["id"])
        assert r.status_code == 409, r.text
        assert "established" in r.json()["detail"]

    def test_a_plan_cannot_be_approved_twice(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Twice")
        node, _ = _established_node(admin_client, token, system_id)
        plan = _create_plan(admin_client, token, system_id, node["id"]).json()

        first = admin_client.post(
            f"/node-operations/reopen-plans/{plan['id']}/approve",
            json={},
            headers=_headers(token, system_id),
        )
        second = admin_client.post(
            f"/node-operations/reopen-plans/{plan['id']}/approve",
            json={},
            headers=_headers(token, system_id),
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 409, second.text

    def test_a_reopen_must_state_why(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-NoReason")
        node, _ = _established_node(admin_client, token, system_id)

        r = _create_plan(admin_client, token, system_id, node["id"], reason="   ")
        assert r.status_code == 422, r.text

    def test_a_specific_classification_may_not_carry_a_failure(self, admin_client):
        """Recording a specific classification alongside an error is exactly
        the heuristic fallback Principle 6 forbids."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-BadPair")
        node, _ = _established_node(admin_client, token, system_id)

        r = _record_anomaly(
            admin_client,
            token,
            system_id,
            node["id"],
            classification="implementation_defect",
            classification_error="model call failed",
        )
        assert r.status_code == 422, r.text

        # The same failure recorded honestly is accepted.
        r = _record_anomaly(
            admin_client,
            token,
            system_id,
            node["id"],
            classification="unknown",
            classification_error="structured output failed validation",
        )
        assert r.status_code == 200, r.text
        assert r.json()["anomaly"]["classification"] == "unknown"

    def test_an_observation_from_another_node_cannot_back_an_anomaly(
        self, admin_client
    ):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-ForeignObs")
        node_a, _ = _established_node(admin_client, token, system_id, "a")
        node_b, _ = _established_node(admin_client, token, system_id, "b")
        contract_a = _create_contract(
            admin_client, token, system_id, node_a["id"]
        ).json()
        observation = _record_observation(
            admin_client, token, system_id, contract_a["id"]
        ).json()

        r = _record_anomaly(
            admin_client,
            token,
            system_id,
            node_b["id"],
            observation_ids=[observation["id"]],
        )
        assert r.status_code == 404, r.text

    def test_a_value_outside_a_finite_vocabulary_is_422(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-BadEnum")
        node, _ = _established_node(admin_client, token, system_id)
        contract = _create_contract(admin_client, token, system_id, node["id"]).json()

        assert (
            _create_contract(
                admin_client,
                token,
                system_id,
                node["id"],
                indicators=[{"kind": "vibes"}],
            ).status_code
            == 422
        )
        assert (
            _record_observation(
                admin_client, token, system_id, contract["id"], indicator_kind="vibes"
            ).status_code
            == 422
        )
        assert (
            _record_observation(
                admin_client,
                token,
                system_id,
                contract["id"],
                observation_state="probably_fine",
            ).status_code
            == 422
        )
        assert (
            _record_anomaly(
                admin_client, token, system_id, node["id"], classification="weird"
            ).status_code
            == 422
        )
        assert (
            _record_anomaly(
                admin_client, token, system_id, node["id"], severity="catastrophic"
            ).status_code
            == 422
        )


# ---------------------------------------------------------------------------
# 5. System scoping
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_another_systems_rows_are_indistinguishable_from_missing(
        self, admin_client
    ):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "OPS-API-IsoA")
        sys_b = _create_system(admin_client, token, "OPS-API-IsoB")
        node, _ = _established_node(admin_client, token, sys_a)
        contract = _create_contract(admin_client, token, sys_a, node["id"]).json()
        anomaly = _record_anomaly(
            admin_client, token, sys_a, node["id"], dedupe_key="k"
        ).json()["anomaly"]
        plan = _create_plan(admin_client, token, sys_a, node["id"]).json()

        # System B sees none of it, and gets 404 rather than 403 so ids stay
        # unprobeable across Systems.
        assert (
            admin_client.get(
                f"/node-operations/nodes/{node['id']}", headers=_headers(token, sys_b)
            ).status_code
            == 404
        )
        assert (
            admin_client.get(
                f"/node-operations/nodes/{node['id']}/reopen-scope",
                headers=_headers(token, sys_b),
            ).status_code
            == 404
        )
        assert (
            _record_observation(
                admin_client, token, sys_b, contract["id"]
            ).status_code
            == 404
        )
        assert _create_contract(admin_client, token, sys_b, node["id"]).status_code == 404
        assert _record_anomaly(admin_client, token, sys_b, node["id"]).status_code == 404
        assert _create_plan(admin_client, token, sys_b, node["id"]).status_code == 404
        assert (
            admin_client.post(
                f"/node-operations/reopen-plans/{plan['id']}/approve",
                json={},
                headers=_headers(token, sys_b),
            ).status_code
            == 404
        )
        assert anomaly["system_id"] == sys_a

        # And nothing System B did (or failed to do) moved System A's Node.
        assert _maturity(node["id"])["maturity"] == "established"

    def test_an_anomaly_of_another_system_cannot_back_a_plan(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "OPS-API-IsoAnomalyA")
        sys_b = _create_system(admin_client, token, "OPS-API-IsoAnomalyB")
        node_a, _ = _established_node(admin_client, token, sys_a, "a")
        node_b, _ = _established_node(admin_client, token, sys_b, "b")
        anomaly = _record_anomaly(
            admin_client, token, sys_a, node_a["id"], dedupe_key="k"
        ).json()["anomaly"]

        r = _create_plan(
            admin_client, token, sys_b, node_b["id"], anomaly_id=anomaly["id"]
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 6. Authentication
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_anonymous_is_rejected_on_every_endpoint(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-Anon")
        node, _ = _established_node(admin_client, token, system_id)
        contract = _create_contract(admin_client, token, system_id, node["id"]).json()
        plan = _create_plan(admin_client, token, system_id, node["id"]).json()
        admin_client.cookies.clear()

        calls = [
            ("post", f"/node-operations/nodes/{node['id']}/monitoring-contracts", {}),
            (
                "post",
                f"/node-operations/monitoring-contracts/{contract['id']}/observations",
                {
                    "indicator": "p95",
                    "indicator_kind": "latency",
                    "observation_state": "within_budget",
                },
            ),
            (
                "post",
                f"/node-operations/nodes/{node['id']}/anomalies",
                {"classification": "implementation_defect"},
            ),
            ("get", f"/node-operations/nodes/{node['id']}/reopen-scope", None),
            (
                "post",
                f"/node-operations/nodes/{node['id']}/reopen-plans",
                {"reason": "x"},
            ),
            ("post", f"/node-operations/reopen-plans/{plan['id']}/approve", {}),
            ("get", f"/node-operations/nodes/{node['id']}", None),
        ]
        for method, path, body in calls:
            if method == "get":
                r = admin_client.get(path)
            else:
                r = admin_client.post(path, json=body)
            assert r.status_code in (401, 403), f"{method} {path}: {r.status_code}"

    def test_sdk_api_token_is_rejected(self, admin_client):
        """These are control-plane routes; an SDK data-plane token must never
        reach them (`require_user`)."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OPS-API-SdkToken")
        node, _ = _established_node(admin_client, token, system_id)

        r = admin_client.post(
            "/tokens",
            json={"name": "sdk", "kind": "api", "system_id": system_id},
            headers=_headers(token),
        )
        if r.status_code not in (200, 201):
            pytest.skip(f"token issuing endpoint unavailable: {r.status_code}")
        api_token = r.json().get("token")
        assert api_token, r.text

        r = admin_client.get(
            f"/node-operations/nodes/{node['id']}",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# 7. Vocabulary parity
# ---------------------------------------------------------------------------


class TestVocabularyParity:
    def test_models_mirror_the_domain_layers_finite_sets(self):
        assert get_args(OperationsIndicatorKind) == node_operations.INDICATOR_KINDS
        assert get_args(OperationsObservationState) == node_operations.OBSERVATION_STATES
        assert (
            get_args(OperationsAnomalyClassification)
            == node_operations.ANOMALY_CLASSIFICATIONS
        )
        assert get_args(OperationsAnomalySeverity) == node_operations.ANOMALY_SEVERITIES
        assert get_args(OperationsReopenStatus) == node_operations.REOPEN_STATUSES
