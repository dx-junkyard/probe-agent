"""API tests for Epic #394 Phase 1 (Issue #396): the Evolution Node routes.

`test_evolution_node.py` covers the domain layer; this file covers only what
the HTTP boundary itself decides, which is a short list on purpose:

1. the end-to-end lifecycle over HTTP (create -> version -> implementation ->
   stable pin -> transition) and the projection it produces;
2. that a REJECTED transition surfaces the domain layer's own finite
   `reason_code` verbatim in `detail.code` -- the Dashboard branches on the
   code, so a re-worded message here would leave it branching on prose;
3. that a repeated `idempotency_key` is a 200 `duplicate`, not an error and
   not a second event;
4. System isolation across every endpoint;
5. that an unknown value for a finite field is a 422 rather than being
   silently dropped or silently returning an empty list;
6. that `maturity` / `improvement_status` / `policy_mode` come back as three
   independent fields even when they disagree, with no combined label
   anywhere in the response (ADR-6);
7. that the finite vocabularies in `app/models.py` and `app/evolution_node.py`
   have not drifted apart.
"""

from __future__ import annotations

import time
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from app import evolution_node
from app.models import (
    EvolutionActorKind,
    EvolutionImplementationModality,
    EvolutionLinkKind,
    EvolutionMaturityState,
    EvolutionSideEffectClass,
    EvolutionTrustBoundary,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "evolution-node-api-test.db"))
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


def _create_node(client, token, system_id, node_key, display_name=""):
    r = client.post(
        "/evolution-nodes",
        json={"node_key": node_key, "display_name": display_name},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _add_version(client, token, system_id, node_id, **overrides):
    payload = {
        "mission": "normalize an address string",
        "input_contract": {"address": "str"},
        "output_contract": {"normalized": "str"},
        "side_effect_class": "pure",
        "trust_boundary": "internal",
    }
    payload.update(overrides)
    r = client.post(
        f"/evolution-nodes/{node_id}/versions",
        json=payload,
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _add_implementation(client, token, system_id, node_id, version_id, modality="rule"):
    r = client.post(
        f"/evolution-nodes/{node_id}/implementations",
        json={"node_version_id": version_id, "modality": modality},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _transition(client, token, system_id, node_id, **overrides):
    payload = {"to_state": "validating", "decision_method": "manual"}
    payload.update(overrides)
    return client.post(
        f"/evolution-nodes/{node_id}/transitions",
        json=payload,
        headers=_headers(token, system_id),
    )


def _drive_to_monitoring(client, token, system_id, node_key):
    """Bring a fresh node to `monitoring` for deactivation tests.

    `validating -> established` goes through the DOMAIN layer here (the
    API's own establishment path is the Stabilization gate this file
    tests), and `established -> monitoring` is system-recorded, so both
    legs use `evolution_node.apply_transition` directly."""
    from app.db import get_conn
    from app.node_operations import create_monitoring_contract

    node = _create_node(client, token, system_id, node_key)
    version = _add_version(client, token, system_id, node["id"])
    implementation = _add_implementation(
        client, token, system_id, node["id"], version["id"]
    )
    r = client.post(
        f"/evolution-nodes/{node['id']}/stable-implementation",
        json={"implementation_id": implementation["id"]},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/evolution-nodes/{node['id']}/links",
        json={"link_kind": "capability", "target_ref": f"cap-{node_key}"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    r = _transition(client, token, system_id, node["id"], to_state="validating")
    assert r.status_code == 200, r.text

    with get_conn() as conn:
        result = evolution_node.apply_transition(
            conn, system_id=system_id, node_id=node["id"], to_state="established",
            decision_method="manual", actor="fixture",
            evidence_refs=[f"capability:cap-{node_key}"],
        )
        assert result.applied, result.decision
        # A REAL monitoring contract: the gate now refuses a ref that does
        # not resolve to an active, non-superseded contract of this Node, so
        # a hand-written string here would make the fixture unreachable.
        create_monitoring_contract(
            conn, system_id=system_id, node_id=node["id"],
            freshness_budget_seconds=60.0, minimum_sample_count=1,
        )
        result = evolution_node.apply_transition(
            conn, system_id=system_id, node_id=node["id"], to_state="monitoring",
            decision_method="deterministic", actor_kind="system",
            evidence_refs=[f"capability:cap-{node_key}"],
        )
        assert result.applied, result.decision
    return node["id"]


# ---------------------------------------------------------------------------
# 1. Lifecycle over HTTP
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_create_version_implement_pin_and_transition(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Lifecycle")

        node = _create_node(admin_client, token, system_id, "address-normalizer", "住所正規化")
        assert node["maturity"] == "exploring"

        version = _add_version(admin_client, token, system_id, node["id"])
        assert version["version_number"] == 1

        implementation = _add_implementation(
            admin_client, token, system_id, node["id"], version["id"], modality="reasoning_llm"
        )
        assert implementation["modality"] == "reasoning_llm"

        r = admin_client.post(
            f"/evolution-nodes/{node['id']}/stable-implementation",
            json={"implementation_id": implementation["id"], "reason": "initial pin"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        assert r.json()["stable_implementation_id"] == implementation["id"]

        r = _transition(admin_client, token, system_id, node["id"], to_state="validating")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] is True
        assert body["duplicate"] is False
        assert body["maturity"] == "validating"
        assert body["event"]["from_state"] == "exploring"
        assert body["event"]["to_state"] == "validating"

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}", headers=_headers(token, system_id)
        )
        assert r.status_code == 200, r.text
        projection = r.json()
        assert projection["maturity"] == "validating"
        assert projection["current_version"]["id"] == version["id"]
        assert projection["current_implementation"]["modality"] == "reasoning_llm"
        assert projection["stable_implementation"]["id"] == implementation["id"]

    def test_duplicate_node_key_is_409(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Dup")
        _create_node(admin_client, token, system_id, "same-key")
        r = admin_client.post(
            "/evolution-nodes",
            json={"node_key": "same-key"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 409, r.text

    def test_events_endpoint_returns_the_append_only_lineage(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Events")
        node = _create_node(admin_client, token, system_id, "lineage")
        version = _add_version(admin_client, token, system_id, node["id"])
        implementation = _add_implementation(
            admin_client, token, system_id, node["id"], version["id"]
        )
        admin_client.post(
            f"/evolution-nodes/{node['id']}/stable-implementation",
            json={"implementation_id": implementation["id"]},
            headers=_headers(token, system_id),
        )
        _transition(admin_client, token, system_id, node["id"], to_state="validating")

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}/events", headers=_headers(token, system_id)
        )
        assert r.status_code == 200, r.text
        events = r.json()["events"]
        kinds = [event["event_kind"] for event in events]
        assert "transition" in kinds
        assert "version_created" in kinds
        assert "implementation_created" in kinds
        assert "stable_pinned" in kinds
        # Newest first.
        assert [event["id"] for event in events] == sorted(
            (event["id"] for event in events), reverse=True
        )
        # ADR-4: folding the transition events alone reproduces the stored
        # maturity. The API exposes the log precisely so this is checkable.
        assert evolution_node.fold_events(list(reversed(events))) == "validating"

    def test_event_limit_is_bounded(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Limit")
        node = _create_node(admin_client, token, system_id, "bounded")
        r = admin_client.get(
            f"/evolution-nodes/{node['id']}/events?limit=100000",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text
        r = admin_client.get(
            f"/evolution-nodes/{node['id']}/events?offset=-1",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text

    def test_events_offset_pages_through_the_full_log(self, admin_client):
        """ADR-4: the fold is only meaningful over the FULL log, so a log
        longer than one bounded page must be readable page by page and
        reassemble into exactly the unpaged sequence."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Paging")
        node = _create_node(admin_client, token, system_id, "paged")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])
        # Build up a log: validating <-> exploring round trips.
        states = ["validating", "exploring", "validating", "exploring", "validating"]
        for to_state in states:
            r = _transition(admin_client, token, system_id, node["id"], to_state=to_state)
            assert r.status_code == 200, r.text

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}/events?limit=200",
            headers=_headers(token, system_id),
        )
        full_log = r.json()["events"]
        assert len(full_log) > 3

        paged = []
        offset = 0
        while True:
            r = admin_client.get(
                f"/evolution-nodes/{node['id']}/events?limit=3&offset={offset}",
                headers=_headers(token, system_id),
            )
            assert r.status_code == 200, r.text
            page = r.json()["events"]
            if not page:
                break
            paged.extend(page)
            offset += 3

        assert [e["id"] for e in paged] == [e["id"] for e in full_log]
        # And the paged, reassembled log folds to the stored maturity.
        assert evolution_node.fold_events(list(reversed(paged))) == "validating"


# ---------------------------------------------------------------------------
# 2. A rejected transition carries the domain layer's finite code
# ---------------------------------------------------------------------------


class TestRejectionCodes:
    def test_rejected_transition_returns_the_exact_domain_reason_code(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Reject")
        node = _create_node(admin_client, token, system_id, "no-version")

        # No contract version yet -> `version_missing`, not a generic 422.
        r = _transition(admin_client, token, system_id, node["id"], to_state="validating")
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "version_missing"
        assert detail["code"] in evolution_node.TRANSITION_REJECTION_CODES
        assert detail["message"]

    def test_illegal_transition_returns_illegal_transition(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Illegal")
        node = _create_node(admin_client, token, system_id, "illegal")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])

        # exploring -> monitoring is not in ALLOWED_TRANSITIONS.
        r = _transition(admin_client, token, system_id, node["id"], to_state="monitoring")
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "illegal_transition"

    def test_reasoning_llm_decision_method_is_refused(self, admin_client):
        """Principle 6: an LLM never emits a canonical state, and the refusal
        must be visible as its own code rather than hidden behind a generic
        validation error."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-LLM")
        node = _create_node(admin_client, token, system_id, "llm-state")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])

        r = _transition(
            admin_client, token, system_id, node["id"],
            to_state="validating", decision_method="reasoning_llm",
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "llm_state_not_allowed"

    def test_established_without_evidence_is_refused(self, admin_client):
        """The domain's `evidence_missing` stays reachable over HTTP through
        the one establishment the route still accepts directly:
        `monitoring -> established` (see TestEstablishmentGate)."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Evidence")
        node_id = _drive_to_monitoring(admin_client, token, system_id, "needs-evidence")

        r = _transition(
            admin_client, token, system_id, node_id,
            to_state="established", evidence_refs=[],
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "evidence_missing"


# ---------------------------------------------------------------------------
# 2b. Provenance cannot be claimed by the caller (ADR-9, #337's rule)
# ---------------------------------------------------------------------------


class TestTransitionProvenance:
    def test_deterministic_decision_method_is_refused_with_a_finite_code(
        self, admin_client
    ):
        """`deterministic` marks a system-recorded observation transition;
        accepting it from a request body would let any human POST forge one."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Determ")
        node = _create_node(admin_client, token, system_id, "no-forgery")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])

        r = _transition(
            admin_client, token, system_id, node["id"],
            to_state="validating", decision_method="deterministic",
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "deterministic_via_api_not_allowed"

        # The route's own codes stay a finite vocabulary of their own and
        # never collide with the domain evaluator's.
        from app.routes.evolution_nodes import ROUTE_TRANSITION_REJECTION_CODES

        assert "deterministic_via_api_not_allowed" in ROUTE_TRANSITION_REJECTION_CODES
        assert not (
            set(ROUTE_TRANSITION_REJECTION_CODES)
            & set(evolution_node.TRANSITION_REJECTION_CODES)
        )

        # And no event was written by the refused request.
        r = admin_client.get(
            f"/evolution-nodes/{node['id']}/events", headers=_headers(token, system_id)
        )
        assert all(e["event_kind"] != "transition" for e in r.json()["events"])

    def test_body_supplied_actor_and_actor_kind_are_rejected_outright(
        self, admin_client
    ):
        """The fields no longer exist on the request model (extra='forbid'):
        provenance comes from the route and the Principal, never the body."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-ActorBody")
        node = _create_node(admin_client, token, system_id, "no-actor-body")

        for forbidden_field in ({"actor": "someone-else"}, {"actor_kind": "system"}):
            r = _transition(
                admin_client, token, system_id, node["id"],
                to_state="validating", **forbidden_field,
            )
            assert r.status_code == 422, r.text

    def test_recorded_event_carries_the_principal_as_developer(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Principal")
        node = _create_node(admin_client, token, system_id, "principal")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])

        r = _transition(admin_client, token, system_id, node["id"], to_state="validating")
        assert r.status_code == 200, r.text
        event = r.json()["event"]
        assert event["actor"] == "root"
        assert event["actor_kind"] == "developer"
        assert event["decision_method"] == "manual"


# ---------------------------------------------------------------------------
# 2c. Establishment goes through the Stabilization gate (#399)
# ---------------------------------------------------------------------------


class TestEstablishmentGate:
    def test_validating_to_established_is_refused_toward_the_stabilization_path(
        self, admin_client
    ):
        """Even a request that satisfies every DOMAIN precondition (stable
        pin, linked evidence) may not establish through this endpoint: the
        sanctioned path is POST /stabilization-packages/{id}/approve."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-EstGate")
        node = _create_node(admin_client, token, system_id, "gate")
        version = _add_version(admin_client, token, system_id, node["id"])
        implementation = _add_implementation(
            admin_client, token, system_id, node["id"], version["id"]
        )
        admin_client.post(
            f"/evolution-nodes/{node['id']}/stable-implementation",
            json={"implementation_id": implementation["id"]},
            headers=_headers(token, system_id),
        )
        r = admin_client.post(
            f"/evolution-nodes/{node['id']}/links",
            json={"link_kind": "capability", "target_ref": "cap-gate"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        _transition(admin_client, token, system_id, node["id"], to_state="validating")

        r = _transition(
            admin_client, token, system_id, node["id"], to_state="established",
            evidence_refs=["capability:cap-gate"],
        )
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "establishment_via_stabilization_required"
        assert "/stabilization-packages/" in detail["message"]

        from app.routes.evolution_nodes import ROUTE_TRANSITION_REJECTION_CODES

        assert detail["code"] in ROUTE_TRANSITION_REJECTION_CODES

        # The node did not move.
        r = admin_client.get(
            f"/evolution-nodes/{node['id']}", headers=_headers(token, system_id)
        )
        assert r.json()["maturity"] == "validating"

    def test_monitoring_to_established_stays_a_manual_api_operation(self, admin_client):
        """Recording that observation STOPPED is a deactivation, not an
        establishment judgement, so it keeps its direct manual path."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Deact")
        node_id = _drive_to_monitoring(admin_client, token, system_id, "deactivate")

        r = _transition(
            admin_client, token, system_id, node_id, to_state="established",
            evidence_refs=["capability:cap-deactivate"],
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["applied"] is True
        assert body["maturity"] == "established"
        assert body["event"]["actor_kind"] == "developer"
        assert body["event"]["decision_method"] == "manual"

    def test_retry_of_an_applied_deactivation_stays_a_duplicate(self, admin_client):
        """The gate must not break idempotency: once monitoring->established
        applied, the maturity is no longer 'monitoring', but a retry with the
        same key is still a 200 duplicate, not a gate refusal."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-DeactRetry")
        node_id = _drive_to_monitoring(admin_client, token, system_id, "deact-retry")

        first = _transition(
            admin_client, token, system_id, node_id, to_state="established",
            evidence_refs=["capability:cap-deact-retry"], idempotency_key="deact-1",
        )
        assert first.status_code == 200, first.text
        retry = _transition(
            admin_client, token, system_id, node_id, to_state="established",
            evidence_refs=["capability:cap-deact-retry"], idempotency_key="deact-1",
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["duplicate"] is True
        assert retry.json()["event"]["id"] == first.json()["event"]["id"]


# ---------------------------------------------------------------------------
# 2d. probe_point links resolve to an approved probe point (ADR-2)
# ---------------------------------------------------------------------------


class TestProbePointLinkApi:
    def _insert_probe_point(self, system_id, status):
        from app.db import get_conn

        from tests.test_evolution_node import _insert_probe_point_chain

        with get_conn() as conn:
            return _insert_probe_point_chain(conn, system_id, status=status)

    def _link(self, client, token, system_id, node_id, target_ref):
        return client.post(
            f"/evolution-nodes/{node_id}/links",
            json={"link_kind": "probe_point", "target_ref": target_ref},
            headers=_headers(token, system_id),
        )

    def test_approved_probe_point_link_is_created(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-PP-OK")
        node = _create_node(admin_client, token, system_id, "pp-ok")
        point_id = self._insert_probe_point(system_id, "approved")

        r = self._link(admin_client, token, system_id, node["id"], str(point_id))
        assert r.status_code == 201, r.text
        assert r.json()["target_ref"] == str(point_id)

    def test_nonexistent_probe_point_is_404(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-PP-Missing")
        node = _create_node(admin_client, token, system_id, "pp-missing")

        r = self._link(admin_client, token, system_id, node["id"], "999999")
        assert r.status_code == 404, r.text

    def test_unapproved_probe_point_is_409(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-PP-Unapproved")
        node = _create_node(admin_client, token, system_id, "pp-unapproved")
        point_id = self._insert_probe_point(system_id, "proposed")

        r = self._link(admin_client, token, system_id, node["id"], str(point_id))
        assert r.status_code == 409, r.text

    def test_cross_system_probe_point_is_indistinguishable_from_missing(
        self, admin_client
    ):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EN-API-PP-CrossA")
        sys_b = _create_system(admin_client, token, "EN-API-PP-CrossB")
        point_id = self._insert_probe_point(sys_a, "approved")
        node_b = _create_node(admin_client, token, sys_b, "pp-cross")

        r = self._link(admin_client, token, sys_b, node_b["id"], str(point_id))
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 3. Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_repeated_idempotency_key_is_a_200_duplicate_not_a_conflict(
        self, admin_client
    ):
        """A retried request that changed nothing is a success. Returning 409
        would make every network retry look like a conflict the developer has
        to resolve."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Idem")
        node = _create_node(admin_client, token, system_id, "idem")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])

        first = _transition(
            admin_client, token, system_id, node["id"],
            to_state="validating", idempotency_key="req-1",
        )
        assert first.status_code == 200, first.text
        assert first.json()["applied"] is True
        assert first.json()["duplicate"] is False

        second = _transition(
            admin_client, token, system_id, node["id"],
            to_state="validating", idempotency_key="req-1",
        )
        assert second.status_code == 200, second.text
        assert second.json()["applied"] is False
        assert second.json()["duplicate"] is True
        assert second.json()["event"]["id"] == first.json()["event"]["id"]

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}/events", headers=_headers(token, system_id)
        )
        transitions = [e for e in r.json()["events"] if e["event_kind"] == "transition"]
        assert len(transitions) == 1


# ---------------------------------------------------------------------------
# 4. System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_every_endpoint_hides_a_node_from_another_system(self, admin_client):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EN-API-IsoA")
        sys_b = _create_system(admin_client, token, "EN-API-IsoB")

        node = _create_node(admin_client, token, sys_a, "private")
        version = _add_version(admin_client, token, sys_a, node["id"])
        node_id = node["id"]

        # A node in another System is indistinguishable from one that does
        # not exist -- otherwise ids could be probed across Systems.
        reads = [
            admin_client.get(f"/evolution-nodes/{node_id}", headers=_headers(token, sys_b)),
            admin_client.get(
                f"/evolution-nodes/{node_id}/events", headers=_headers(token, sys_b)
            ),
            admin_client.get(
                f"/evolution-nodes/{node_id}/legacy-projection",
                headers=_headers(token, sys_b),
            ),
        ]
        for response in reads:
            assert response.status_code == 404, response.text

        writes = [
            admin_client.post(
                f"/evolution-nodes/{node_id}/versions",
                json={
                    "mission": "x", "side_effect_class": "pure",
                    "trust_boundary": "internal",
                },
                headers=_headers(token, sys_b),
            ),
            admin_client.post(
                f"/evolution-nodes/{node_id}/implementations",
                json={"node_version_id": version["id"], "modality": "rule"},
                headers=_headers(token, sys_b),
            ),
            admin_client.post(
                f"/evolution-nodes/{node_id}/links",
                json={"link_kind": "component", "target_ref": "svc"},
                headers=_headers(token, sys_b),
            ),
            admin_client.post(
                f"/evolution-nodes/{node_id}/transitions",
                json={"to_state": "validating", "decision_method": "manual"},
                headers=_headers(token, sys_b),
            ),
            admin_client.post(
                f"/evolution-nodes/{node_id}/stable-implementation",
                json={"implementation_id": 1},
                headers=_headers(token, sys_b),
            ),
        ]
        for response in writes:
            assert response.status_code == 404, response.text

    def test_list_is_system_scoped_and_node_keys_may_repeat_across_systems(
        self, admin_client
    ):
        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EN-API-ListA")
        sys_b = _create_system(admin_client, token, "EN-API-ListB")

        _create_node(admin_client, token, sys_a, "shared-key")
        # The same key in another System is a different Node, not a conflict:
        # identity is (system_id, node_key).
        _create_node(admin_client, token, sys_b, "shared-key")

        r = admin_client.get("/evolution-nodes", headers=_headers(token, sys_a))
        assert [n["node_key"] for n in r.json()["nodes"]] == ["shared-key"]
        r = admin_client.get("/evolution-nodes", headers=_headers(token, sys_b))
        assert [n["node_key"] for n in r.json()["nodes"]] == ["shared-key"]


# ---------------------------------------------------------------------------
# 5. Unknown values in finite fields are 422, never silently dropped
# ---------------------------------------------------------------------------


class TestFiniteFieldValidation:
    def test_unknown_maturity_filter_is_422_not_an_empty_list(self, admin_client):
        """A typo that returned an empty list would read as 'this System has
        no nodes', which is a different and wrong answer."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Filter")
        _create_node(admin_client, token, system_id, "n1")

        r = admin_client.get(
            "/evolution-nodes?maturity=estabished",  # deliberate typo
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "unknown_maturity_filter"

    def test_known_maturity_filter_selects(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Filter2")
        _create_node(admin_client, token, system_id, "n1")

        r = admin_client.get(
            "/evolution-nodes?maturity=exploring", headers=_headers(token, system_id)
        )
        assert r.status_code == 200
        assert len(r.json()["nodes"]) == 1
        r = admin_client.get(
            "/evolution-nodes?maturity=established", headers=_headers(token, system_id)
        )
        assert r.json()["nodes"] == []

    def test_unknown_modality_and_link_kind_are_422(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Vocab")
        node = _create_node(admin_client, token, system_id, "vocab")
        version = _add_version(admin_client, token, system_id, node["id"])

        r = admin_client.post(
            f"/evolution-nodes/{node['id']}/implementations",
            json={"node_version_id": version["id"], "modality": "gpt"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text

        r = admin_client.post(
            f"/evolution-nodes/{node['id']}/links",
            json={"link_kind": "microservice", "target_ref": "svc"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 5b. A frozen contract and the maturity-lineage reconciliation
# ---------------------------------------------------------------------------


class TestFrozenContractApi:
    def test_new_version_on_an_established_node_is_409(self, admin_client):
        """The domain's conflict surfaces as 409, and its message names the
        way out (reopen/suspend) rather than dead-ending the developer."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Frozen")
        node_id = _drive_to_monitoring(admin_client, token, system_id, "frozen-api")

        # monitoring -> established (recording that observation stopped) is
        # the one establishment this endpoint accepts directly.
        r = _transition(
            admin_client, token, system_id, node_id, to_state="established",
            evidence_refs=["capability:cap-frozen-api"],
        )
        assert r.status_code == 200, r.text

        r = admin_client.post(
            f"/evolution-nodes/{node_id}/versions",
            json={
                "mission": "revised",
                "input_contract": {},
                "output_contract": {},
                "side_effect_class": "pure",
                "trust_boundary": "internal",
            },
            headers=_headers(token, system_id),
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert "reopened" in detail and "suspended" in detail

        # Still exactly one version, still current.
        r = admin_client.get(
            f"/evolution-nodes/{node_id}", headers=_headers(token, system_id)
        )
        assert r.json()["current_version"]["version_number"] == 1

    def test_projection_reports_the_folded_maturity_and_its_consistency(
        self, admin_client
    ):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Fold")
        node = _create_node(admin_client, token, system_id, "fold-api")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])
        _transition(admin_client, token, system_id, node["id"], to_state="validating")

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}", headers=_headers(token, system_id)
        )
        projection = r.json()
        assert projection["maturity"] == "validating"
        assert projection["folded_maturity"] == "validating"
        assert projection["maturity_consistent"] is True
        assert projection["availability"]["maturity_lineage"] is True

    def test_a_drifted_stored_maturity_is_visible_over_http(self, admin_client):
        """ADR-4 makes the event log the authority; a reader of the canonical
        projection must be able to SEE a drifted cache, not just an auditor
        refolding the log by hand."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Drift")
        node = _create_node(admin_client, token, system_id, "drift-api")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])
        _transition(admin_client, token, system_id, node["id"], to_state="validating")

        with get_conn() as conn:
            conn.execute(
                "UPDATE evolution_node SET maturity = 'suspended' WHERE id = ?",
                (node["id"],),
            )

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}", headers=_headers(token, system_id)
        )
        projection = r.json()
        assert projection["maturity"] == "suspended"
        assert projection["folded_maturity"] == "validating"
        assert projection["maturity_consistent"] is False


# ---------------------------------------------------------------------------
# 6. The axes stay independent (ADR-6)
# ---------------------------------------------------------------------------


class TestAxisIndependence:
    def test_three_axes_are_reported_as_they_are_even_when_they_disagree(
        self, admin_client
    ):
        """A Cell Improvement of `adopted`, a Component policy of `shadow`,
        and a Node still `validating` is a legitimate combination. The
        response must report all three unchanged and must not synthesise a
        combined status/readiness label from them."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Axes")
        node = _create_node(admin_client, token, system_id, "axes")
        version = _add_version(admin_client, token, system_id, node["id"])
        _add_implementation(admin_client, token, system_id, node["id"], version["id"])
        _transition(admin_client, token, system_id, node["id"], to_state="validating")

        with get_conn() as conn:
            now = time.time()
            conn.execute(
                "INSERT INTO components (system_id, component_id, mode, updated_at) "
                "VALUES (?, 'svc-axes', 'shadow', ?)",
                (system_id, now),
            )
            cur = conn.execute(
                """INSERT INTO agent_role_cards
                       (system_id, role_key, version, status, mission, model_alias,
                        changelog, schema_version, created_at)
                   VALUES (?, 'worker-axes', '1.0.0', 'active', 'm', 'worker-default',
                           'init', '1.0', ?)""",
                (system_id, now),
            )
            role_card_id = cur.lastrowid
            cur = conn.execute(
                """INSERT INTO cell_definitions
                       (system_id, cell_id, role_card_id, status, mission,
                        created_at, updated_at)
                   VALUES (?, 'svc-axes', ?, 'active', '', ?, ?)""",
                (system_id, role_card_id, now, now),
            )
            cell_definition_id = cur.lastrowid
            conn.execute(
                """INSERT INTO cell_improvements
                       (system_id, cell_definition_id, status, target_kind,
                        created_at, updated_at)
                   VALUES (?, ?, 'adopted', 'role_card', ?, ?)""",
                (system_id, cell_definition_id, now, now),
            )

        r = admin_client.post(
            f"/evolution-nodes/{node['id']}/links",
            json={"link_kind": "component", "target_ref": "svc-axes"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 201, r.text

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}", headers=_headers(token, system_id)
        )
        projection = r.json()
        assert projection["maturity"] == "validating"
        assert projection["improvement_status"] == "adopted"
        assert projection["policy_mode"] == "shadow"
        # No combined axis, and the fourth axis is ABSENT rather than null --
        # Phase 6 wires it, and `null` would claim it had been evaluated.
        assert "workflow_phase" not in projection
        for forbidden in ("status", "readiness", "overall", "score", "confidence"):
            assert forbidden not in projection

    def test_unlinked_axes_are_null_and_available(self, admin_client):
        """`null` here means 'nothing of that kind is linked', which the
        `availability` flag distinguishes from 'could not be read'."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-NoLinks")
        node = _create_node(admin_client, token, system_id, "bare")

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}", headers=_headers(token, system_id)
        )
        projection = r.json()
        assert projection["improvement_status"] is None
        assert projection["policy_mode"] is None
        assert projection["availability"]["improvement_status"] is True
        assert projection["availability"]["policy_mode"] is True


# ---------------------------------------------------------------------------
# 7. Compatibility projection and vocabulary parity
# ---------------------------------------------------------------------------


class TestCompatibilityProjection:
    def test_legacy_projection_is_labelled_and_carries_the_component(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Legacy")
        node = _create_node(admin_client, token, system_id, "legacy")
        admin_client.post(
            f"/evolution-nodes/{node['id']}/links",
            json={"link_kind": "component", "target_ref": "svc-legacy"},
            headers=_headers(token, system_id),
        )

        r = admin_client.get(
            f"/evolution-nodes/{node['id']}/legacy-projection",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # ADR-8: a consumer must be able to tell this apart from the
        # canonical projection, so the flag is part of the document.
        assert body["compatibility_projection"] is True
        assert body["component_id"] == "svc-legacy"
        assert body["maturity"] == "exploring"


class TestVocabularyParity:
    def test_model_literals_match_the_domain_vocabularies(self):
        """`app/models.py` re-declares the finite vocabularies so FastAPI
        emits a real enum in the OpenAPI schema. Re-declaration is only safe
        while the two definitions are identical."""
        assert get_args(EvolutionMaturityState) == evolution_node.MATURITY_STATES
        assert (
            get_args(EvolutionImplementationModality)
            == evolution_node.IMPLEMENTATION_MODALITIES
        )
        assert get_args(EvolutionLinkKind) == evolution_node.LINK_KINDS
        assert get_args(EvolutionSideEffectClass) == evolution_node.SIDE_EFFECT_CLASSES
        assert get_args(EvolutionTrustBoundary) == evolution_node.TRUST_BOUNDARIES
        assert get_args(EvolutionActorKind) == evolution_node.ACTOR_KINDS


class TestAuthorization:
    def test_sdk_api_token_is_rejected(self, admin_client):
        """These are dashboard/control-plane routes. An SDK data-plane token
        must never reach them (`require_user`)."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-API-Auth")

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
            "/evolution-nodes", headers={"Authorization": f"Bearer {api_token}"}
        )
        assert r.status_code == 403, r.text

    def test_anonymous_is_rejected(self, admin_client):
        r = admin_client.get("/evolution-nodes")
        assert r.status_code in (401, 403), r.text
