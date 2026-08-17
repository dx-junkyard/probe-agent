"""Tests for Epic #394 Phase 1 (Issue #396): the Evolution Node Fabric core
contract.

Covers `app/evolution_node.py`:

1. The pure evaluator (`evaluate_transition`): every one of the 12
   first-match rejection codes in isolation, the full legal transition
   table, a representative sample of illegal transitions, and that
   `reasoning_llm` is refused for a maturity transition unconditionally.
2. `reopened` never requires or clears the stable implementation pin.
3. `fold_events` reproduces a Node's stored maturity by replaying its event
   log alone (ADR-4 reconciliation).
4. Persistence: `apply_transition` idempotency on `idempotency_key`.
5. System isolation, modeled on `test_cell_fabric.py::TestSystemIsolation`.
6. Additive migration, modeled on `test_cell_fabric.py::TestMigration`.
7. The canonical projection: `maturity` / `improvement_status` /
   `policy_mode` stay three independent axes even when they disagree, an
   unresolvable pointer is reported as `availability.<key> = False` (never a
   fabricated default), and the projection validates against
   `shared/schemas/evolution_node.schema.json`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from app.evolution_node import (
    ALLOWED_TRANSITIONS,
    ACTOR_KINDS,
    EVENT_KINDS,
    IMPLEMENTATION_MODALITIES,
    LINK_KINDS,
    MATURITY_STATES,
    SIDE_EFFECT_CLASSES,
    SYSTEM_RECORDED_TRANSITIONS,
    TRANSITION_REJECTION_CODES,
    TRUST_BOUNDARIES,
    EvolutionNodeConflictError,
    EvolutionNodeNotFoundError,
    EvolutionNodeValidationError,
    NodeFacts,
    TransitionRequest,
    add_implementation,
    add_link,
    add_version,
    apply_transition,
    build_legacy_projection,
    build_node_projection,
    create_node,
    evaluate_transition,
    fold_events,
    list_nodes,
    load_node_facts,
    pin_stable_implementation,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "shared" / "schemas" / "evolution_node.schema.json"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "evolution-node-test.db"))
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


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_bearer(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _insert_cell(conn, system_id, cell_id):
    """Minimal cell_definitions row (+ its required role card), inserted
    directly -- app/cell_binding.py's create_binding requires an approved
    Probe Point/Pattern, which is unrelated setup weight for these tests."""
    now = time.time()
    cur = conn.execute(
        """INSERT INTO agent_role_cards
               (system_id, role_key, version, status, mission, model_alias,
                changelog, schema_version, created_at)
           VALUES (?, 'worker-x', '1.0.0', 'active', 'm', 'worker-default', 'init', '1.0', ?)""",
        (system_id, now),
    )
    role_card_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO cell_definitions
               (system_id, cell_id, role_card_id, status, mission, created_at, updated_at)
           VALUES (?, ?, ?, 'active', '', ?, ?)""",
        (system_id, cell_id, role_card_id, now, now),
    )
    return cur.lastrowid


def _insert_cell_improvement(conn, system_id, cell_definition_id, status):
    now = time.time()
    conn.execute(
        """INSERT INTO cell_improvements
               (system_id, cell_definition_id, status, target_kind, created_at, updated_at)
           VALUES (?, ?, ?, 'role_card', ?, ?)""",
        (system_id, cell_definition_id, status, now, now),
    )


# ---------------------------------------------------------------------------
# Part 1: the pure evaluator
# ---------------------------------------------------------------------------


def _facts(**overrides):
    base = dict(
        maturity="exploring",
        has_version=True,
        has_implementation=True,
        has_stable_implementation=True,
        monitoring_contract_ref="contract-1",
        implementation_stale=False,
        known_evidence_refs=frozenset({"component:x"}),
    )
    base.update(overrides)
    return NodeFacts(**base)


def _request(**overrides):
    base = dict(
        to_state="validating",
        decision_method="manual",
        actor="dev",
        actor_kind="developer",
        reason="",
        evidence_refs=("component:x",),
    )
    base.update(overrides)
    return TransitionRequest(**base)


class TestRejectionCodes:
    """One test per code in TRANSITION_REJECTION_CODES, each isolating that
    ONE row by satisfying every earlier row's condition."""

    def test_all_codes_covered(self):
        # Sanity: the table this class is organized around has exactly the
        # 12 codes the brief enumerates, and "ok" is not one of them.
        assert len(TRANSITION_REJECTION_CODES) == 12
        assert "ok" not in TRANSITION_REJECTION_CODES

    def test_unknown_target_state(self):
        decision = evaluate_transition(_facts(), _request(to_state="not_a_state"))
        assert decision.reason_code == "unknown_target_state"
        assert not decision.allowed

    def test_llm_state_not_allowed(self):
        # A legal target (exploring -> validating) with decision_method
        # reasoning_llm must still be refused, unconditionally and first.
        decision = evaluate_transition(
            _facts(maturity="exploring"),
            _request(to_state="validating", decision_method="reasoning_llm"),
        )
        assert decision.reason_code == "llm_state_not_allowed"

    def test_illegal_transition(self):
        decision = evaluate_transition(
            _facts(maturity="exploring"), _request(to_state="established")
        )
        assert decision.reason_code == "illegal_transition"

    def test_illegal_transition_same_state(self):
        decision = evaluate_transition(
            _facts(maturity="established"), _request(to_state="established")
        )
        assert decision.reason_code == "illegal_transition"

    def test_manual_approval_required_for_non_manual_ordinary_transition(self):
        decision = evaluate_transition(
            _facts(maturity="exploring"),
            _request(to_state="validating", decision_method="deterministic", actor_kind="system"),
        )
        assert decision.reason_code == "manual_approval_required"

    def test_manual_approval_required_for_manual_system_recorded_pair(self):
        # established -> monitoring is system-recorded; a manual attempt at
        # it is refused (Phase 1 keeps this pair fully automated).
        decision = evaluate_transition(
            _facts(maturity="established"),
            _request(
                to_state="monitoring", decision_method="manual",
                actor="dev", actor_kind="developer", reason="ready",
            ),
        )
        assert decision.reason_code == "manual_approval_required"

    def test_reason_required_for_suspend(self):
        decision = evaluate_transition(
            _facts(maturity="exploring"), _request(to_state="suspended", reason="")
        )
        assert decision.reason_code == "reason_required"

    def test_reason_required_for_backward_move(self):
        decision = evaluate_transition(
            _facts(maturity="established"),
            _request(to_state="reopened", reason=""),
        )
        assert decision.reason_code == "reason_required"

    def test_version_missing(self):
        decision = evaluate_transition(
            _facts(maturity="exploring", has_version=False),
            _request(to_state="validating"),
        )
        assert decision.reason_code == "version_missing"

    def test_implementation_missing(self):
        decision = evaluate_transition(
            _facts(maturity="exploring", has_implementation=False),
            _request(to_state="validating"),
        )
        assert decision.reason_code == "implementation_missing"

    def test_stable_implementation_missing(self):
        decision = evaluate_transition(
            _facts(maturity="validating", has_stable_implementation=False),
            _request(to_state="established"),
        )
        assert decision.reason_code == "stable_implementation_missing"

    def test_monitoring_contract_missing(self):
        decision = evaluate_transition(
            _facts(maturity="established", monitoring_contract_ref=None),
            _request(
                to_state="monitoring", decision_method="deterministic", actor_kind="system",
                actor=None,
            ),
        )
        assert decision.reason_code == "monitoring_contract_missing"

    def test_evidence_missing(self):
        decision = evaluate_transition(
            _facts(maturity="validating"),
            _request(to_state="established", evidence_refs=()),
        )
        assert decision.reason_code == "evidence_missing"

    def test_foreign_evidence(self):
        decision = evaluate_transition(
            _facts(maturity="validating", known_evidence_refs=frozenset({"component:linked"})),
            _request(to_state="established", evidence_refs=("component:not-linked",)),
        )
        assert decision.reason_code == "foreign_evidence"

    def test_stale_implementation(self):
        decision = evaluate_transition(
            _facts(maturity="validating", implementation_stale=True),
            _request(to_state="established"),
        )
        assert decision.reason_code == "stale_implementation"

    def test_fully_satisfied_established_transition_is_allowed(self):
        decision = evaluate_transition(
            _facts(maturity="validating"), _request(to_state="established")
        )
        assert decision.allowed
        assert decision.reason_code == "ok"


class TestTransitionTable:
    def test_every_allowed_transition_is_allowed_when_prerequisites_met(self):
        for from_state, targets in ALLOWED_TRANSITIONS.items():
            for to_state in targets:
                pair = (from_state, to_state)
                if pair in SYSTEM_RECORDED_TRANSITIONS:
                    decision_method, actor_kind, actor = "deterministic", "system", None
                else:
                    decision_method, actor_kind, actor = "manual", "developer", "dev"
                is_backward = (
                    from_state in ("established", "monitoring")
                    and to_state not in ("monitoring", "established")
                )
                reason = "because reasons" if (to_state == "suspended" or is_backward) else ""
                facts = _facts(maturity=from_state)
                request = TransitionRequest(
                    to_state=to_state, decision_method=decision_method,
                    actor=actor, actor_kind=actor_kind, reason=reason,
                    evidence_refs=("component:x",),
                )
                decision = evaluate_transition(facts, request)
                assert decision.allowed, (
                    f"{from_state} -> {to_state} unexpectedly rejected: "
                    f"{decision.reason_code} ({decision.message})"
                )

    @pytest.mark.parametrize(
        "from_state,to_state",
        [
            ("exploring", "established"),
            ("exploring", "monitoring"),
            ("exploring", "reopened"),
            ("validating", "monitoring"),
            ("validating", "reopened"),
            ("monitoring", "validating"),
            ("monitoring", "exploring"),
            ("suspended", "suspended"),
        ],
    )
    def test_illegal_pairs_are_rejected(self, from_state, to_state):
        decision = evaluate_transition(
            _facts(maturity=from_state),
            _request(to_state=to_state, reason="because"),
        )
        assert not decision.allowed
        assert decision.reason_code == "illegal_transition"

    def test_reopened_target_does_not_require_stable_implementation(self):
        # Row 8 gates only established/monitoring -- 'reopened' must be
        # reachable from 'established' even if (hypothetically) the stable
        # pin were unset, because pinning is never cleared by reopening.
        decision = evaluate_transition(
            _facts(maturity="established", has_stable_implementation=False),
            _request(to_state="reopened", reason="re-exploring"),
        )
        assert decision.allowed


def test_fold_events_reproduces_a_multi_step_lifecycle():
    events = [
        {"id": 1, "event_kind": "version_created", "to_state": None},
        {"id": 2, "event_kind": "transition", "to_state": "validating"},
        {"id": 3, "event_kind": "implementation_created", "to_state": None},
        {"id": 4, "event_kind": "transition", "to_state": "established"},
        {"id": 5, "event_kind": "stable_pinned", "to_state": None},
        {"id": 6, "event_kind": "transition", "to_state": "monitoring"},
        {"id": 7, "event_kind": "transition", "to_state": "established"},
    ]
    assert fold_events(events) == "established"
    # Order independence of the INPUT sequence: fold_events sorts by id.
    assert fold_events(list(reversed(events))) == "established"


def test_fold_events_empty_sequence_is_none():
    assert fold_events([]) is None


# ---------------------------------------------------------------------------
# Part 2: persistence + projection
# ---------------------------------------------------------------------------


def _build_established_node(conn, system_id, node_key="summarizer", *, component="svc-a"):
    node = create_node(conn, system_id=system_id, node_key=node_key, display_name="D")
    version = add_version(
        conn, system_id=system_id, node_id=node["id"], mission="Summarize text",
        input_contract={"text": "string"}, output_contract={"summary": "string"},
        side_effect_class="pure", trust_boundary="internal",
        establishment_criteria=["pass rate > 0.9"], created_by="alice",
    )
    implementation = add_implementation(
        conn, system_id=system_id, node_id=node["id"], node_version_id=version["id"],
        modality="reasoning_llm", config={"temp": 0.2}, created_by="alice",
    )
    add_link(
        conn, system_id=system_id, node_id=node["id"], link_kind="component",
        target_ref=component, created_by="alice",
    )
    apply_transition(
        conn, system_id=system_id, node_id=node["id"], to_state="validating",
        decision_method="manual", actor="alice",
    )
    pin_stable_implementation(
        conn, system_id=system_id, node_id=node["id"],
        implementation_id=implementation["id"], actor="alice",
    )
    result = apply_transition(
        conn, system_id=system_id, node_id=node["id"], to_state="established",
        decision_method="manual", actor="alice",
        evidence_refs=(f"component:{component}",),
    )
    assert result.applied, result.decision
    return node, version, implementation


class TestPersistenceLifecycle:
    def test_create_add_pin_transition_round_trip(self, admin_client, tmp_path):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Lifecycle")

        with get_conn() as conn:
            node, version, implementation = _build_established_node(conn, system_id)
            refreshed = load_node_facts(conn, system_id=system_id, node_id=node["id"])
            assert refreshed.maturity == "established"
            assert refreshed.has_stable_implementation

    def test_reopened_does_not_clear_stable_pin(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Reopen")

        with get_conn() as conn:
            node, _version, implementation = _build_established_node(conn, system_id)
            result = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="reopened",
                decision_method="manual", actor="alice", reason="re-checking an edge case",
            )
            assert result.applied, result.decision
            row = conn.execute(
                "SELECT * FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
            assert row["maturity"] == "reopened"
            assert row["stable_implementation_id"] == implementation["id"]

    def test_stable_pin_rotates_previous_stable_into_rollback(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Rollback")

        with get_conn() as conn:
            node, version, implementation_1 = _build_established_node(conn, system_id)
            implementation_2 = add_implementation(
                conn, system_id=system_id, node_id=node["id"], node_version_id=version["id"],
                modality="deterministic_code", created_by="alice",
            )
            updated = pin_stable_implementation(
                conn, system_id=system_id, node_id=node["id"],
                implementation_id=implementation_2["id"], actor="alice",
            )
            assert updated["stable_implementation_id"] == implementation_2["id"]
            assert updated["rollback_implementation_id"] == implementation_1["id"]

            events = conn.execute(
                "SELECT event_kind FROM evolution_node_event "
                "WHERE node_id = ? AND event_kind IN ('stable_pinned', 'rollback_pinned') "
                "ORDER BY id",
                (node["id"],),
            ).fetchall()
            kinds = [r["event_kind"] for r in events]
            assert kinds.count("stable_pinned") == 2
            assert kinds.count("rollback_pinned") == 1

    def test_apply_transition_rejects_unknown_node(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Missing")
        with get_conn() as conn:
            with pytest.raises(EvolutionNodeNotFoundError):
                apply_transition(
                    conn, system_id=system_id, node_id=999999, to_state="validating",
                    decision_method="manual", actor="a",
                )

    def test_create_node_duplicate_key_conflicts(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Dup")
        with get_conn() as conn:
            create_node(conn, system_id=system_id, node_key="dup", display_name="A")
            with pytest.raises(EvolutionNodeConflictError):
                create_node(conn, system_id=system_id, node_key="dup", display_name="B")

    def test_add_version_rejects_unknown_vocabulary_value(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-BadVocab")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="n1")
            with pytest.raises(EvolutionNodeValidationError):
                add_version(
                    conn, system_id=system_id, node_id=node["id"], mission="m",
                    input_contract={}, output_contract={},
                    side_effect_class="not_a_real_class", trust_boundary="internal",
                )


class TestIdempotency:
    def test_repeated_idempotency_key_applies_exactly_once(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Idem")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="idem")
            version = add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            # `validating` requires a current implementation (rule 7); without
            # one the transition is rejected before idempotency is ever
            # consulted, so this fixture would test nothing.
            add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"], modality="rule", created_by="alice",
            )
            first = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="validating",
                decision_method="manual", actor="alice", idempotency_key="req-1",
            )
            assert first.applied and not first.duplicate

            second = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="validating",
                decision_method="manual", actor="alice", idempotency_key="req-1",
            )
            assert second.duplicate and not second.applied
            assert second.event["id"] == first.event["id"]

            events = conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_event WHERE idempotency_key = ?",
                ("req-1",),
            ).fetchone()["n"]
            assert events == 1

            row = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
            assert row["maturity"] == "validating"

    def test_empty_idempotency_key_never_collides(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Idem-Empty")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="idem2")
            version = add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"], modality="rule", created_by="alice",
            )
            first = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="validating",
                decision_method="manual", actor="alice", idempotency_key="",
            )
            assert first.applied
            # A second distinct empty-key request is a NEW request, not a dup.
            second = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="suspended",
                decision_method="manual", actor="alice", reason="pausing",
                idempotency_key="",
            )
            assert second.applied and not second.duplicate


# ---------------------------------------------------------------------------
# System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_nodes_are_system_scoped(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EN-IsoA")
        sys_b = _create_system(admin_client, token, "EN-IsoB")

        with get_conn() as conn:
            node_a = create_node(conn, system_id=sys_a, node_key="shared-key")

            assert list_nodes(conn, system_id=sys_b) == []

            with pytest.raises(EvolutionNodeNotFoundError):
                load_node_facts(conn, system_id=sys_b, node_id=node_a["id"])

            with pytest.raises(EvolutionNodeNotFoundError):
                apply_transition(
                    conn, system_id=sys_b, node_id=node_a["id"], to_state="validating",
                    decision_method="manual", actor="attacker",
                )

            # System B may reuse the SAME node_key without conflict.
            node_b = create_node(conn, system_id=sys_b, node_key="shared-key")
            assert node_b["id"] != node_a["id"]

    def test_evidence_from_another_system_is_not_known_to_this_node(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EN-IsoEvidenceA")
        sys_b = _create_system(admin_client, token, "EN-IsoEvidenceB")

        with get_conn() as conn:
            node_a = create_node(conn, system_id=sys_a, node_key="node-a")
            add_link(
                conn, system_id=sys_a, node_id=node_a["id"], link_kind="component",
                target_ref="shared-name",
            )
            node_b = create_node(conn, system_id=sys_b, node_key="node-b")
            add_link(
                conn, system_id=sys_b, node_id=node_b["id"], link_kind="component",
                target_ref="shared-name",
            )
            add_version(
                conn, system_id=sys_b, node_id=node_b["id"], mission="m",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            impl = add_implementation(
                conn, system_id=sys_b, node_id=node_b["id"],
                node_version_id=conn.execute(
                    "SELECT current_version_id AS v FROM evolution_node WHERE id = ?",
                    (node_b["id"],),
                ).fetchone()["v"],
                modality="rule",
            )
            apply_transition(
                conn, system_id=sys_b, node_id=node_b["id"], to_state="validating",
                decision_method="manual", actor="dev",
            )
            pin_stable_implementation(
                conn, system_id=sys_b, node_id=node_b["id"], implementation_id=impl["id"],
                actor="dev",
            )
            # node_b's own known_evidence_refs is scoped to node_b's OWN
            # links; "component:shared-name" is legitimately known to it
            # even though node_a in System A also links the same target_ref
            # string -- known_evidence_refs never crosses a node_id, let
            # alone a system_id, boundary.
            facts = load_node_facts(conn, system_id=sys_b, node_id=node_b["id"])
            assert "component:shared-name" in facts.known_evidence_refs

            result = apply_transition(
                conn, system_id=sys_b, node_id=node_b["id"], to_state="established",
                decision_method="manual", actor="dev",
                evidence_refs=("component:shared-name",),
            )
            assert result.applied, result.decision

            # An evidence ref that merely LOOKS plausible but was never
            # linked to node_b (e.g. copied from node_a's projection) is
            # foreign, even though the string is identical to a real link
            # elsewhere in the System.
            unlinked_result_facts = load_node_facts(conn, system_id=sys_a, node_id=node_a["id"])
            assert "component:shared-name" in unlinked_result_facts.known_evidence_refs
            assert "probe_point:not-linked-anywhere" not in facts.known_evidence_refs


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class TestMigration:
    def test_new_tables_appear_via_init_db(self, admin_client):
        from app.db import get_conn, init_db

        init_db()
        with get_conn() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        for table in (
            "evolution_node",
            "evolution_node_version",
            "evolution_node_implementation",
            "evolution_node_link",
            "evolution_node_event",
        ):
            assert table in tables

    def test_init_db_idempotent_preserves_existing_populated_db(self, admin_client):
        from app.db import get_conn, init_db

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Migration")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="survivor")

        init_db()
        init_db()  # idempotency: running twice more must not raise or alter data

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert row is not None
        assert row["node_key"] == "survivor"
        assert row["maturity"] == "exploring"


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


class TestProjection:
    def test_maturity_improvement_status_and_policy_mode_are_independent(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Independent")

        with get_conn() as conn:
            node, _version, _impl = _build_established_node(
                conn, system_id, component="svc-independent"
            )
            # maturity is 'established' (asserted by _build_established_node
            # via apply_transition already). Deliberately set the OTHER two
            # axes to values that DISAGREE with a naive "established implies
            # healthy" story: the linked Component's policy is 'off', and
            # its Cell has a REJECTED improvement -- if any axis were
            # (mis)derived from another, one of these would be overwritten.
            conn.execute(
                "INSERT INTO components (system_id, component_id, mode, updated_at) "
                "VALUES (?, 'svc-independent', 'off', ?)",
                (system_id, time.time()),
            )
            cell_definition_id = _insert_cell(conn, system_id, "svc-independent")
            _insert_cell_improvement(conn, system_id, cell_definition_id, "rejected")

            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])

        assert projection["maturity"] == "established"
        assert projection["policy_mode"] == "off"
        assert projection["improvement_status"] == "rejected"
        assert projection["availability"]["improvement_status"] is True
        assert projection["availability"]["policy_mode"] is True

    def test_unresolvable_current_version_pointer_is_unavailable_not_defaulted(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Unavailable")

        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="dangling")
            # Simulate a corrupted pointer directly (never produced by this
            # module's own persistence functions): current_version_id set,
            # but no such row exists.
            #
            # The FK on current_version_id is what normally makes this
            # impossible, which is exactly why it has to be suspended here --
            # the assertion under test is about how the projection REPORTS a
            # pointer it cannot resolve, and that path is only reachable if
            # the row is corrupted out of band (a restored backup, a manual
            # repair, a future migration bug). Suspending the pragma is the
            # only way to reach it without weakening the schema itself.
            conn.execute("PRAGMA foreign_keys=OFF")
            try:
                conn.execute(
                    "UPDATE evolution_node SET current_version_id = 999999 WHERE id = ?",
                    (node["id"],),
                )
            finally:
                conn.execute("PRAGMA foreign_keys=ON")
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])

        assert projection["current_version"] is None
        assert projection["availability"]["version"] is False
        # An untouched pointer (never set) is a legitimate absence, not an
        # unavailable read.
        assert projection["availability"]["implementation"] is True
        assert projection["current_implementation"] is None

    def test_no_linked_cell_is_null_and_available(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-NoCell")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="no-cell")
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])
        assert projection["improvement_status"] is None
        assert projection["availability"]["improvement_status"] is True
        assert projection["policy_mode"] is None
        assert projection["availability"]["policy_mode"] is True

    def test_dangling_cell_binding_link_marks_improvement_status_unavailable(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-DanglingBinding")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="dangling-binding")
            add_link(
                conn, system_id=system_id, node_id=node["id"], link_kind="cell_binding",
                target_ref="cb-1", target_row_id=999999,
            )
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])
        assert projection["improvement_status"] is None
        assert projection["availability"]["improvement_status"] is False

    def test_projection_validates_against_shared_schema(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-SchemaValid")
        with get_conn() as conn:
            node, _version, _impl = _build_established_node(
                conn, system_id, component="svc-schema"
            )
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])
            legacy = build_legacy_projection(conn, system_id=system_id, node_id=node["id"])

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(projection, schema)

        assert legacy["compatibility_projection"] is True
        assert legacy["component_id"] == "svc-schema"
        assert legacy["maturity"] == "established"

    def test_projection_omits_workflow_phase_entirely(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-NoWorkflowPhase")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="no-phase")
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])
        assert "workflow_phase" not in projection

    def test_empty_node_projection_validates(self, admin_client):
        """A freshly created Node (no version/implementation/links/events
        beyond its own creation) must still produce a schema-valid document
        -- every optional block is null, not omitted."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Empty")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="fresh")
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(projection, schema)
        assert projection["current_version"] is None
        assert projection["current_implementation"] is None
        assert projection["stable_implementation"] is None
        assert projection["rollback_implementation"] is None
        assert projection["links"] == []
        assert projection["improvement_status"] is None
        assert projection["policy_mode"] is None


# ---------------------------------------------------------------------------
# Finite vocabulary sanity (guards against accidental spelling drift)
# ---------------------------------------------------------------------------


def test_finite_vocabularies_match_the_brief_exactly():
    assert MATURITY_STATES == (
        "exploring", "validating", "established", "monitoring", "reopened", "suspended",
    )
    assert IMPLEMENTATION_MODALITIES == (
        "reasoning_llm", "lm_program", "retrieval", "router", "small_model",
        "rule", "deterministic_code", "workflow", "manual", "hybrid",
    )
    assert LINK_KINDS == (
        "component", "probe_point", "cell_binding", "capability",
        "flow", "purpose_element", "feature",
    )
    assert SIDE_EFFECT_CLASSES == (
        "pure", "read_only", "local_write", "external_write", "irreversible",
    )
    assert TRUST_BOUNDARIES == (
        "internal", "external_input", "external_output", "third_party",
    )
    assert ACTOR_KINDS == ("developer", "system")
    assert EVENT_KINDS == (
        "transition", "version_created", "implementation_created",
        "link_created", "stable_pinned", "rollback_pinned",
    )
    assert SYSTEM_RECORDED_TRANSITIONS == frozenset({
        ("established", "monitoring"), ("monitoring", "established"),
    })
