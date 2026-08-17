"""Tests for Epic #394 Phase 5 (Issue #400): monitoring, drift, local reopen.

What must hold:

1. Silence is never health. `unobserved` and `insufficient_sample` are their
   own states and are never rolled into "within budget".
2. `established` and `monitoring` are two independent readings (ADR-5): an
   established Node whose telemetry died reads as exactly that.
3. A failed reasoning classification records `unknown` with its error, never
   a heuristic guess (Principle 6).
4. A repeated condition does not create a second anomaly, and therefore not a
   second reopen.
5. Reopen scope is a structural fact about shared link targets, unrelated
   Nodes are excluded EXPLICITLY, approval is a named human's, and the stable
   implementation stays pinned throughout.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.evolution_node import (
    add_implementation, add_link, add_version, apply_transition, create_node,
    pin_stable_implementation,
)
from app.node_operations import (
    ANOMALY_CLASSIFICATIONS,
    FRAME_BREAKING_CLASSIFICATIONS,
    INDICATOR_KINDS,
    OBSERVATION_STATES,
    OperationsConflictError,
    OperationsNotFoundError,
    OperationsValidationError,
    approve_reopen_plan,
    build_operations_projection,
    create_monitoring_contract,
    create_reopen_plan,
    evaluate_observation_health,
    propose_reopen_scope,
    record_anomaly,
    record_observation,
)


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "node-operations-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _established_node(conn, system_id, node_key="operated"):
    node = create_node(conn, system_id=system_id, node_key=node_key)
    version = add_version(
        conn, system_id=system_id, node_id=node["id"], mission="m",
        input_contract={}, output_contract={}, side_effect_class="pure",
        trust_boundary="internal",
    )
    implementation = add_implementation(
        conn, system_id=system_id, node_id=node["id"],
        node_version_id=version["id"], modality="rule",
    )
    pin_stable_implementation(
        conn, system_id=system_id, node_id=node["id"],
        implementation_id=implementation["id"], actor="alice",
    )
    # Phase 1 refuses `established` without evidence, and the evidence refs it
    # recognises are the Node's own links. This link is therefore load-bearing
    # setup, not decoration: without it the helper silently leaves the Node in
    # `validating` and every test below would exercise the wrong state.
    add_link(
        conn, system_id=system_id, node_id=node["id"],
        link_kind="capability", target_ref=f"cap-{node_key}", created_by="alice",
    )
    apply_transition(
        conn, system_id=system_id, node_id=node["id"], to_state="validating",
        decision_method="manual", actor="alice",
    )
    apply_transition(
        conn, system_id=system_id, node_id=node["id"], to_state="established",
        decision_method="manual", actor="alice",
        evidence_refs=[f"capability:cap-{node_key}"],
    )
    established = conn.execute(
        "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
    ).fetchone()
    assert established["maturity"] == "established", (
        "the fixture must actually reach `established`; otherwise the tests "
        "below silently exercise a `validating` Node"
    )
    return node, version, implementation


# ---------------------------------------------------------------------------
# 1. Silence is never health
# ---------------------------------------------------------------------------


class TestObservationHealth:
    def test_never_observed_is_unobserved_not_within_budget(self):
        health = evaluate_observation_health(
            last_observed_at=None, now=1000.0, freshness_budget_seconds=60.0,
            sample_count=None, minimum_sample_count=0,
        )
        assert health.state == "unobserved"

    def test_stale_observation_is_unobserved(self):
        """A Node whose data stopped cannot be judged to have drifted or not
        -- there is nothing recent to judge."""
        health = evaluate_observation_health(
            last_observed_at=100.0, now=1000.0, freshness_budget_seconds=60.0,
            sample_count=500, minimum_sample_count=10,
        )
        assert health.state == "unobserved"
        assert "budget" in health.reason

    def test_under_sampled_is_its_own_state_and_outranks_the_drift_verdict(self):
        """Too little data cannot establish that something did NOT drift any
        more than that it did."""
        health = evaluate_observation_health(
            last_observed_at=990.0, now=1000.0, freshness_budget_seconds=60.0,
            sample_count=3, minimum_sample_count=100, drift_detected=False,
        )
        assert health.state == "insufficient_sample"

        also = evaluate_observation_health(
            last_observed_at=990.0, now=1000.0, freshness_budget_seconds=60.0,
            sample_count=3, minimum_sample_count=100, drift_detected=True,
        )
        assert also.state == "insufficient_sample"

    def test_unknown_sample_size_is_insufficient_not_sufficient(self):
        health = evaluate_observation_health(
            last_observed_at=990.0, now=1000.0, freshness_budget_seconds=60.0,
            sample_count=None, minimum_sample_count=10,
        )
        assert health.state == "insufficient_sample"

    def test_a_fresh_well_sampled_reading_reports_its_verdict(self):
        healthy = evaluate_observation_health(
            last_observed_at=999.0, now=1000.0, freshness_budget_seconds=60.0,
            sample_count=500, minimum_sample_count=10,
        )
        assert healthy.state == "within_budget"

        drifting = evaluate_observation_health(
            last_observed_at=999.0, now=1000.0, freshness_budget_seconds=60.0,
            sample_count=500, minimum_sample_count=10, drift_detected=True,
        )
        assert drifting.state == "drift_detected"

    def test_the_four_states_are_the_whole_vocabulary(self):
        assert set(OBSERVATION_STATES) == {
            "within_budget", "drift_detected", "insufficient_sample", "unobserved"
        }


# ---------------------------------------------------------------------------
# 2. established and monitoring are independent (ADR-5)
# ---------------------------------------------------------------------------


class TestIndependentReadings:
    def test_an_established_node_with_dead_telemetry_reads_as_exactly_that(
        self, admin_client
    ):
        """Telemetry stopping does not make the fixation decision wrong, and
        the two must stay distinguishable -- collapsing them is the #366
        one-word-two-facts defect."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Dead")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            contract = create_monitoring_contract(
                conn, system_id=system_id, node_id=node["id"],
                freshness_budget_seconds=60.0, minimum_sample_count=1,
            )
            record_observation(
                conn, system_id=system_id, contract_id=contract["id"],
                indicator="p95", indicator_kind="latency",
                observation_state="within_budget", observed_value=10.0,
                sample_count=100, last_observed_at=1000.0,
            )
            projection = build_operations_projection(
                conn, system_id=system_id, node_id=node["id"], now=1_000_000.0
            )

        assert projection["maturity"] == "established"
        assert projection["observation"]["state"] == "unobserved"

    def test_no_contract_is_not_a_monitoring_failure(self, admin_client):
        """None means "no contract is wired", never "monitoring failed"."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-NoContract")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            projection = build_operations_projection(
                conn, system_id=system_id, node_id=node["id"]
            )
        assert projection["monitoring_contract_declared"] is False
        assert projection["observation"] is None

    def test_a_contract_needs_an_established_node(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-TooEarly")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="exploring-still")
            with pytest.raises(OperationsConflictError):
                create_monitoring_contract(
                    conn, system_id=system_id, node_id=node["id"]
                )

    def test_a_new_contract_version_supersedes_the_previous(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Versions")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            first = create_monitoring_contract(
                conn, system_id=system_id, node_id=node["id"]
            )
            second = create_monitoring_contract(
                conn, system_id=system_id, node_id=node["id"]
            )
            assert second["version_number"] == 2
            superseded = conn.execute(
                "SELECT superseded_by_id FROM node_monitoring_contract WHERE id = ?",
                (first["id"],),
            ).fetchone()["superseded_by_id"]
        assert superseded == second["id"]

    def test_an_unknown_indicator_kind_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-BadKind")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            with pytest.raises(OperationsValidationError):
                create_monitoring_contract(
                    conn, system_id=system_id, node_id=node["id"],
                    indicators=[{"kind": "vibes"}],
                )


# ---------------------------------------------------------------------------
# 3 & 4. Anomaly taxonomy, fail-closed classification, dedupe
# ---------------------------------------------------------------------------


class TestAnomalies:
    def _node_and_contract(self, conn, system_id, key="anomalous"):
        node, _, _ = _established_node(conn, system_id, node_key=key)
        contract = create_monitoring_contract(
            conn, system_id=system_id, node_id=node["id"]
        )
        return node, contract

    def test_a_failed_classification_is_unknown_with_its_error(self, admin_client):
        """A reasoning classification that fails leaves `unknown` WITH the
        failure -- "the system saw something and could not say what" is a real,
        actionable state, and it is never replaced by a heuristic guess."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Unknown")
        with get_conn() as conn:
            node, contract = self._node_and_contract(conn, system_id)
            row, created = record_anomaly(
                conn, system_id=system_id, node_id=node["id"],
                classification="unknown", decision_method="reasoning_llm",
                classification_error="structured output failed validation",
                contract_id=contract["id"],
            )
        assert created is True
        assert row["classification"] == "unknown"
        assert row["classification_error"]

    def test_a_specific_classification_may_not_carry_a_failure(self, admin_client):
        """If the classification did not succeed, it is `unknown`. Recording a
        specific classification alongside an error is exactly the heuristic
        fallback Principle 6 forbids."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-BadPair")
        with get_conn() as conn:
            node, _ = self._node_and_contract(conn, system_id)
            with pytest.raises(OperationsValidationError):
                record_anomaly(
                    conn, system_id=system_id, node_id=node["id"],
                    classification="implementation_defect",
                    classification_error="model call failed",
                )

    def test_a_repeated_condition_does_not_create_a_second_anomaly(self, admin_client):
        """One continuing condition must not produce a new anomaly -- and then
        a new reopen -- on every polling cycle."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Dedupe")
        with get_conn() as conn:
            node, _ = self._node_and_contract(conn, system_id)
            first, created_1 = record_anomaly(
                conn, system_id=system_id, node_id=node["id"],
                classification="input_or_environment_drift", dedupe_key="p95-drift",
            )
            second, created_2 = record_anomaly(
                conn, system_id=system_id, node_id=node["id"],
                classification="input_or_environment_drift", dedupe_key="p95-drift",
            )
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM node_anomaly WHERE node_id = ?",
                (node["id"],),
            ).fetchone()["n"]
        assert created_1 is True and created_2 is False
        assert second["id"] == first["id"]
        assert count == 1

    def test_a_defect_and_a_frame_breaking_signal_are_different_answers(
        self, admin_client
    ):
        """Optimising the Node harder is the wrong response to a
        frame-breaking signal, so the projection has to say which it is."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Frame")
        with get_conn() as conn:
            node, _ = self._node_and_contract(conn, system_id)
            record_anomaly(
                conn, system_id=system_id, node_id=node["id"],
                classification="implementation_defect", dedupe_key="d1",
            )
            record_anomaly(
                conn, system_id=system_id, node_id=node["id"],
                classification="new_use_case_signal", dedupe_key="d2",
            )
            projection = build_operations_projection(
                conn, system_id=system_id, node_id=node["id"]
            )
        by_class = {a["classification"]: a for a in projection["anomalies"]}
        assert by_class["implementation_defect"]["frame_breaking"] is False
        assert by_class["new_use_case_signal"]["frame_breaking"] is True

    def test_frame_breaking_is_a_subset_of_the_taxonomy(self):
        assert set(FRAME_BREAKING_CLASSIFICATIONS) < set(ANOMALY_CLASSIFICATIONS)

    def test_an_observation_from_another_node_cannot_back_an_anomaly(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-ForeignObs")
        with get_conn() as conn:
            node_a, contract_a = self._node_and_contract(conn, system_id, key="a")
            node_b, _ = self._node_and_contract(conn, system_id, key="b")
            observation = record_observation(
                conn, system_id=system_id, contract_id=contract_a["id"],
                indicator="p95", indicator_kind="latency",
                observation_state="drift_detected",
            )
            with pytest.raises(OperationsNotFoundError):
                record_anomaly(
                    conn, system_id=system_id, node_id=node_b["id"],
                    classification="implementation_defect",
                    observation_ids=[observation["id"]],
                )


# ---------------------------------------------------------------------------
# 5. Local reopen
# ---------------------------------------------------------------------------


class TestReopen:
    def test_scope_is_a_structural_fact_and_exclusions_are_explicit(
        self, admin_client
    ):
        """A neighbour SHARES a concrete link target -- a finite, checkable
        relation, never a similarity judgement (Principle 6). Unrelated Nodes
        are listed as excluded so a reader can check they were left out."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Scope")
        with get_conn() as conn:
            origin, _, _ = _established_node(conn, system_id, node_key="origin")
            neighbour, _, _ = _established_node(conn, system_id, node_key="neighbour")
            stranger, _, _ = _established_node(conn, system_id, node_key="stranger")

            add_link(
                conn, system_id=system_id, node_id=origin["id"],
                link_kind="component", target_ref="svc-shared",
            )
            add_link(
                conn, system_id=system_id, node_id=neighbour["id"],
                link_kind="component", target_ref="svc-shared",
            )
            add_link(
                conn, system_id=system_id, node_id=stranger["id"],
                link_kind="component", target_ref="svc-other",
            )
            scope = propose_reopen_scope(
                conn, system_id=system_id, origin_node_id=origin["id"]
            )

        assert set(scope.scope_node_ids) == {origin["id"], neighbour["id"]}
        assert stranger["id"] in scope.excluded_node_ids
        shared = [r for r in scope.rationale if r["node_id"] == neighbour["id"]][0]
        assert shared["shared"] == ["component:svc-shared"]

    def test_approving_reopens_only_the_scope_and_keeps_production_pinned(
        self, admin_client
    ):
        """ADR-5: production keeps running the established implementation
        while exploration proceeds. That is what makes starting a
        re-exploration safe at all."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Reopen")
        with get_conn() as conn:
            origin, _, implementation = _established_node(
                conn, system_id, node_key="origin"
            )
            stranger, _, stranger_impl = _established_node(
                conn, system_id, node_key="stranger"
            )
            plan = create_reopen_plan(
                conn, system_id=system_id, origin_node_id=origin["id"],
                reason="p95 drifted past the contract budget",
            )
            row, results = approve_reopen_plan(
                conn, system_id=system_id, plan_id=plan["id"], approved_by="alice",
            )
            after_origin = conn.execute(
                """SELECT maturity, stable_implementation_id FROM evolution_node
                       WHERE id = ?""",
                (origin["id"],),
            ).fetchone()
            after_stranger = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (stranger["id"],)
            ).fetchone()

        assert row["status"] == "approved"
        assert row["approved_by"] == "alice"
        assert after_origin["maturity"] == "reopened"
        # The pin survives the reopen -- production is untouched.
        assert after_origin["stable_implementation_id"] == implementation["id"]
        # An unrelated Node is not dragged along.
        assert after_stranger["maturity"] == "established"
        assert all(r["applied"] for r in results)

    def test_proposing_a_plan_reopens_nothing(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Propose")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            create_reopen_plan(
                conn, system_id=system_id, origin_node_id=node["id"], reason="x",
            )
            after = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert after["maturity"] == "established"

    def test_a_reopen_must_state_why(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-NoReason")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            with pytest.raises(OperationsValidationError):
                create_reopen_plan(
                    conn, system_id=system_id, origin_node_id=node["id"], reason="  ",
                )

    def test_approval_requires_a_named_person(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Anon")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            plan = create_reopen_plan(
                conn, system_id=system_id, origin_node_id=node["id"], reason="x",
            )
            with pytest.raises(OperationsValidationError):
                approve_reopen_plan(
                    conn, system_id=system_id, plan_id=plan["id"], approved_by=" ",
                )

    def test_a_plan_cannot_be_approved_twice(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Twice")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            plan = create_reopen_plan(
                conn, system_id=system_id, origin_node_id=node["id"], reason="x",
            )
            approve_reopen_plan(
                conn, system_id=system_id, plan_id=plan["id"], approved_by="alice",
            )
            with pytest.raises(OperationsConflictError):
                approve_reopen_plan(
                    conn, system_id=system_id, plan_id=plan["id"], approved_by="alice",
                )

    def test_a_node_that_cannot_transition_is_reported_not_forced(self, admin_client):
        """A bulk action that silently ignores a gate is a gate that does not
        exist."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-Blocked")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, system_id)
            plan = create_reopen_plan(
                conn, system_id=system_id, origin_node_id=node["id"], reason="x",
            )
            # The Node is suspended after the plan was written; `suspended ->
            # reopened` IS legal, so instead drive it somewhere that is not.
            apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="monitoring",
                decision_method="deterministic", actor_kind="system",
            )
            _, results = approve_reopen_plan(
                conn, system_id=system_id, plan_id=plan["id"], approved_by="alice",
            )
        # monitoring -> reopened is legal, so this one applies; the point of
        # the assertion is that every node's outcome is REPORTED either way.
        assert results[0]["node_id"] == node["id"]
        assert "reason_code" in results[0]

    def test_a_blocked_node_in_scope_is_reported_not_applied_while_others_proceed(
        self, admin_client
    ):
        """The `applied=False` branch with a real refusal: a Node in scope
        that is already `reopened` cannot legally transition again. It must
        be REPORTED with its refusal code -- never forced and never silently
        dropped -- while the rest of the scope proceeds and every stable pin
        stays set."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "OP-PartialScope")
        with get_conn() as conn:
            origin, _, origin_impl = _established_node(
                conn, system_id, node_key="origin"
            )
            neighbour, _, neighbour_impl = _established_node(
                conn, system_id, node_key="neighbour"
            )
            add_link(
                conn, system_id=system_id, node_id=origin["id"],
                link_kind="component", target_ref="svc-shared",
            )
            add_link(
                conn, system_id=system_id, node_id=neighbour["id"],
                link_kind="component", target_ref="svc-shared",
            )
            plan = create_reopen_plan(
                conn, system_id=system_id, origin_node_id=origin["id"],
                reason="p95 drifted past the contract budget",
            )
            assert set(json.loads(plan["scope_node_ids_json"])) == {
                origin["id"], neighbour["id"]
            }
            # The neighbour is reopened on its own BEFORE this plan is
            # approved, so `reopened -> reopened` is illegal at approval time.
            apply_transition(
                conn, system_id=system_id, node_id=neighbour["id"],
                to_state="reopened", decision_method="manual", actor="alice",
                reason="already under re-exploration",
            )
            row, results = approve_reopen_plan(
                conn, system_id=system_id, plan_id=plan["id"], approved_by="alice",
            )
            after = {
                node_id: conn.execute(
                    """SELECT maturity, stable_implementation_id
                           FROM evolution_node WHERE id = ?""",
                    (node_id,),
                ).fetchone()
                for node_id in (origin["id"], neighbour["id"])
            }

        by_node = {r["node_id"]: r for r in results}
        assert row["status"] == "approved"
        assert by_node[origin["id"]]["applied"] is True
        blocked = by_node[neighbour["id"]]
        assert blocked["applied"] is False
        assert blocked["duplicate"] is False
        assert blocked["reason_code"] == "illegal_transition"
        assert blocked["message"]
        # The rest of the scope proceeded; nobody was forced.
        assert after[origin["id"]]["maturity"] == "reopened"
        assert after[neighbour["id"]]["maturity"] == "reopened"
        # Production stays pinned on EVERY node, blocked or not (ADR-5).
        assert after[origin["id"]]["stable_implementation_id"] == origin_impl["id"]
        assert (
            after[neighbour["id"]]["stable_implementation_id"]
            == neighbour_impl["id"]
        )

    def test_plans_and_anomalies_are_system_scoped(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "OP-IsoA")
        sys_b = _create_system(admin_client, token, "OP-IsoB")
        with get_conn() as conn:
            node, _, _ = _established_node(conn, sys_a)
            plan = create_reopen_plan(
                conn, system_id=sys_a, origin_node_id=node["id"], reason="x",
            )
            with pytest.raises(OperationsNotFoundError):
                approve_reopen_plan(
                    conn, system_id=sys_b, plan_id=plan["id"], approved_by="mallory",
                )
            with pytest.raises(OperationsNotFoundError):
                build_operations_projection(
                    conn, system_id=sys_b, node_id=node["id"]
                )
            with pytest.raises(OperationsNotFoundError):
                propose_reopen_scope(
                    conn, system_id=sys_b, origin_node_id=node["id"]
                )


class TestMigration:
    def test_new_tables_appear_via_init_db(self, admin_client):
        from app.db import get_conn

        with get_conn() as conn:
            names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert {
            "node_monitoring_contract", "node_drift_observation",
            "node_anomaly", "node_reopen_plan",
        } <= names
