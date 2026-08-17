"""Tests for Epic #394 Phase 1 (Issue #396): the Evolution Node Fabric core
contract.

Covers `app/evolution_node.py`:

1. The pure evaluator (`evaluate_transition`): every one of the 13
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
import sqlite3
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


def _insert_probe_point_chain(conn, system_id, status="approved"):
    """Minimal snapshot -> intelligence run -> probe plan -> probe point
    chain (the FK spine `probe_points` requires), inserted directly --
    mirrors `tests/test_cell_binding.py`'s fixture builders."""
    now = time.time()
    cur = conn.execute(
        """INSERT INTO repository_snapshots
               (system_id, repo_path, commit_sha, status, created_at, completed_at)
           VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
        (system_id, now, now),
    )
    snapshot_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model, prompt_version,
                schema_version, decision_method, status, is_mock, started_at, completed_at)
           VALUES (?, ?, 'probe_plan', 'mock', 'mock-model', 'v1', 'v1',
                   'reasoning_llm', 'completed', 1, ?, ?)""",
        (system_id, snapshot_id, now, now),
    )
    run_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO probe_plans
               (system_id, snapshot_id, intelligence_run_id, feature_id, objective,
                status, origin, created_at, updated_at)
           VALUES (?, ?, ?, 'feat-1', 'objective', 'approved', 'manual', ?, ?)""",
        (system_id, snapshot_id, run_id, now, now),
    )
    plan_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO probe_points
               (plan_id, system_id, component_id, feature_id, path, symbol,
                line_start, line_end, reason, recommended_mode, side_effect_risk,
                replayability, denylist_hit, status, created_at, updated_at)
           VALUES (?, ?, 'svc-probe', 'feat-1', 'app/x.py', 'do_x', 1, 10,
                   'reason', 'trace', 'low', 'replayable', NULL, ?, ?, ?)""",
        (plan_id, system_id, status, now, now),
    )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Part 1: the pure evaluator
# ---------------------------------------------------------------------------


def _facts(**overrides):
    base = dict(
        maturity="exploring",
        has_version=True,
        has_implementation=True,
        has_stable_implementation=True,
        monitoring_contract_ref="node_monitoring_contract:1",
        monitoring_contract_valid=True,
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
        # 13 codes the brief enumerates, and "ok" is not one of them.
        assert len(TRANSITION_REJECTION_CODES) == 13
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

    def test_llm_state_not_allowed_outranks_unknown_target_state(self):
        # "Unconditionally first" means literally first: even an UNKNOWN
        # target state does not get to answer before the reasoning_llm
        # refusal -- the caller's provenance is wrong before its payload is.
        decision = evaluate_transition(
            _facts(),
            _request(to_state="not_a_state", decision_method="reasoning_llm"),
        )
        assert decision.reason_code == "llm_state_not_allowed"
        assert TRANSITION_REJECTION_CODES[0] == "llm_state_not_allowed"

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

    def test_monitoring_contract_invalid(self):
        # A ref is SET but does not resolve to an active contract. This is a
        # different fact from "none is wired" and gets its own code: an
        # unverifiable claim never passes the gate.
        decision = evaluate_transition(
            _facts(
                maturity="established",
                monitoring_contract_ref="node_monitoring_contract:1",
                monitoring_contract_valid=False,
            ),
            _request(
                to_state="monitoring", decision_method="deterministic", actor_kind="system",
                actor=None,
            ),
        )
        assert decision.reason_code == "monitoring_contract_invalid"

    def test_missing_outranks_invalid_when_no_ref_is_set(self):
        decision = evaluate_transition(
            _facts(
                maturity="established",
                monitoring_contract_ref=None,
                monitoring_contract_valid=False,
            ),
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

    def test_monitoring_to_established_manual_deactivation_is_allowed(self):
        # The system-recorded pair is asymmetric for a human: recording that
        # observation STOPPED is a legitimate manual operations decision.
        decision = evaluate_transition(
            _facts(maturity="monitoring"),
            _request(to_state="established"),
        )
        assert decision.allowed, decision

    def test_monitoring_to_established_manual_still_requires_a_named_actor(self):
        decision = evaluate_transition(
            _facts(maturity="monitoring"),
            _request(to_state="established", actor=None),
        )
        assert decision.reason_code == "manual_approval_required"

    def test_established_to_monitoring_manual_activation_stays_refused(self):
        # The other direction never opens to a human: a click claiming
        # observation is RUNNING would masquerade as the monitoring
        # evaluation's own reading.
        decision = evaluate_transition(
            _facts(maturity="established"),
            _request(to_state="monitoring", reason="wishful"),
        )
        assert decision.reason_code == "manual_approval_required"

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


class TestProbePointLinkGate:
    """ADR-2's verification rule: `link_kind='probe_point'` may only name an
    existing APPROVED probe point of the same System -- the same gate
    `app/cell_binding.py` applies for Cell Bindings (#299)."""

    def test_approved_probe_point_link_is_created(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-PP-Approved")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="pp-ok")
            point_id = _insert_probe_point_chain(conn, system_id, status="approved")
            link = add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="probe_point", target_ref=str(point_id),
                target_row_id=point_id,
            )
            assert link["target_ref"] == str(point_id)
            facts = load_node_facts(conn, system_id=system_id, node_id=node["id"])
            assert f"probe_point:{point_id}" in facts.known_evidence_refs

    def test_nonexistent_probe_point_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-PP-Missing")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="pp-missing")
            with pytest.raises(EvolutionNodeNotFoundError):
                add_link(
                    conn, system_id=system_id, node_id=node["id"],
                    link_kind="probe_point", target_ref="999999",
                )

    def test_unapproved_probe_point_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-PP-Unapproved")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="pp-unapproved")
            point_id = _insert_probe_point_chain(conn, system_id, status="proposed")
            with pytest.raises(EvolutionNodeConflictError):
                add_link(
                    conn, system_id=system_id, node_id=node["id"],
                    link_kind="probe_point", target_ref=str(point_id),
                )

    def test_another_systems_probe_point_is_indistinguishable_from_missing(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EN-PP-CrossA")
        sys_b = _create_system(admin_client, token, "EN-PP-CrossB")
        with get_conn() as conn:
            point_id = _insert_probe_point_chain(conn, sys_a, status="approved")
            node_b = create_node(conn, system_id=sys_b, node_key="pp-cross")
            with pytest.raises(EvolutionNodeNotFoundError):
                add_link(
                    conn, system_id=sys_b, node_id=node_b["id"],
                    link_kind="probe_point", target_ref=str(point_id),
                )

    def test_non_numeric_target_ref_is_a_validation_error(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-PP-BadRef")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="pp-badref")
            with pytest.raises(EvolutionNodeValidationError):
                add_link(
                    conn, system_id=system_id, node_id=node["id"],
                    link_kind="probe_point", target_ref="app/x.py:do_x",
                )

    def test_mismatched_target_row_id_is_a_validation_error(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-PP-Mismatch")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="pp-mismatch")
            point_id = _insert_probe_point_chain(conn, system_id, status="approved")
            with pytest.raises(EvolutionNodeValidationError):
                add_link(
                    conn, system_id=system_id, node_id=node["id"],
                    link_kind="probe_point", target_ref=str(point_id),
                    target_row_id=point_id + 1,
                )

    def test_other_link_kinds_do_not_consult_probe_points(self, admin_client):
        # A component link with a free-form ref stays legal -- the gate is
        # probe_point-specific.
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-PP-Others")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="pp-others")
            link = add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="component", target_ref="svc-anything",
            )
            assert link["link_kind"] == "component"


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

    def test_unique_violation_recovery_resolves_to_the_winner_event(
        self, admin_client, monkeypatch
    ):
        """The last-resort recovery path: a winner's event exists but NEITHER
        duplicate check saw it, so the partial unique index is what refuses
        our insert. The violation must resolve to the winner's event with
        `duplicate=True` -- the same shape the sequential-retry path returns
        -- never surface as an IntegrityError.

        Both checks are made to miss (they return None for the first two
        calls) while the row genuinely exists, so the IntegrityError below is
        raised by the real index, not simulated. The winner recorded a
        DIFFERENT target state, which is what leaves our own request legal
        far enough to reach the insert at all."""
        import app.evolution_node as en
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Idem-Race")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="idem-race")
            version = add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"], modality="rule", created_by="alice",
            )

            winner = en._insert_event(
                conn, node_id=node["id"], system_id=system_id,
                event_kind="transition", from_state="exploring",
                to_state="suspended", actor="rival", actor_kind="developer",
                decision_method="manual", reason="pausing", idempotency_key="race-1",
            )
            conn.execute(
                "UPDATE evolution_node SET maturity = 'suspended' WHERE id = ?",
                (node["id"],),
            )

            real_lookup = en._lookup_idempotent_event
            state = {"calls": 0}

            def blind_lookup(conn_, **kwargs):
                state["calls"] += 1
                if state["calls"] <= 2:  # the pre-check and the in-transaction check
                    return None
                return real_lookup(conn_, **kwargs)

            monkeypatch.setattr(en, "_lookup_idempotent_event", blind_lookup)
            result = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="validating",
                decision_method="manual", actor="alice", idempotency_key="race-1",
            )
            monkeypatch.undo()
            assert result.duplicate and not result.applied
            assert result.event["id"] == winner["id"]
            assert result.node["maturity"] == "suspended"

            count = conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_event WHERE idempotency_key = ?",
                ("race-1",),
            ).fetchone()["n"]
            assert count == 1

    def test_unrelated_integrity_error_still_raises(self, admin_client, monkeypatch):
        """Only a resolvable idempotency collision is downgraded to a
        duplicate; any other unique violation re-raises unchanged (the
        re-select is the discriminator)."""
        import sqlite3 as sqlite3_module

        import app.evolution_node as en
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Idem-OtherIE")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="idem-other")
            version = add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"], modality="rule", created_by="alice",
            )

            def failing_insert_event(*args, **kwargs):
                raise sqlite3_module.IntegrityError("CHECK constraint failed: elsewhere")

            monkeypatch.setattr(en, "_insert_event", failing_insert_event)
            with pytest.raises(sqlite3_module.IntegrityError):
                apply_transition(
                    conn, system_id=system_id, node_id=node["id"],
                    to_state="validating", decision_method="manual", actor="alice",
                    idempotency_key="no-such-winner",
                )


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
# The contract is frozen while a stable implementation is pinned to it
# ---------------------------------------------------------------------------


class TestContractFreeze:
    """`add_version` is refused for an `established`/`monitoring` Node.

    The stable implementation is pinned to the CURRENT contract, so making a
    new contract current would leave production implementing a superseded
    promise while the Node kept displaying established/monitoring. ADR-9
    forbids repairing that by moving the maturity automatically, so the
    refusal IS the design: the developer reopens or suspends first.
    """

    def test_new_version_is_refused_for_an_established_node(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Freeze-Established")
        with get_conn() as conn:
            node, version, _impl = _build_established_node(conn, system_id, "frozen")
            with pytest.raises(EvolutionNodeConflictError) as excinfo:
                add_version(
                    conn, system_id=system_id, node_id=node["id"], mission="revised",
                    input_contract={}, output_contract={}, side_effect_class="pure",
                    trust_boundary="internal",
                )
            message = str(excinfo.value)
            # The refusal has to name the way OUT of it, or it is a dead end.
            assert "reopened" in message and "suspended" in message

            # Nothing was written: the current version pointer is untouched
            # and no second version row exists.
            row = conn.execute(
                "SELECT current_version_id FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
            assert row["current_version_id"] == version["id"]
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_version WHERE node_id = ?",
                (node["id"],),
            ).fetchone()["n"]
            assert count == 1

    def test_new_version_is_refused_for_a_monitoring_node(self, admin_client):
        from app.db import get_conn
        from app.node_operations import create_monitoring_contract

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Freeze-Monitoring")
        with get_conn() as conn:
            node, _version, _impl = _build_established_node(conn, system_id, "watched")
            create_monitoring_contract(
                conn, system_id=system_id, node_id=node["id"],
                freshness_budget_seconds=60.0, minimum_sample_count=1,
            )
            result = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="monitoring",
                decision_method="deterministic", actor_kind="system",
                evidence_refs=("component:svc-a",),
            )
            assert result.applied, result.decision
            with pytest.raises(EvolutionNodeConflictError):
                add_version(
                    conn, system_id=system_id, node_id=node["id"], mission="revised",
                    input_contract={}, output_contract={}, side_effect_class="pure",
                    trust_boundary="internal",
                )

    def test_reopening_first_makes_the_new_version_possible(self, admin_client):
        """The refusal is a sequencing rule, not a wall: an explicit human
        reopen (`decision_method: manual`) unfreezes the contract, and the
        stable pin deliberately stays in place while it does (ADR-5)."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Freeze-Reopen")
        with get_conn() as conn:
            node, version, implementation = _build_established_node(conn, system_id, "unfreeze")
            reopened = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="reopened",
                decision_method="manual", actor="alice",
                reason="the contract needs a new output field",
            )
            assert reopened.applied, reopened.decision

            new_version = add_version(
                conn, system_id=system_id, node_id=node["id"], mission="revised",
                input_contract={}, output_contract={"summary": "string", "score": "float"},
                side_effect_class="pure", trust_boundary="internal", created_by="alice",
            )
            assert new_version["version_number"] == 2
            row = conn.execute(
                "SELECT current_version_id, stable_implementation_id "
                "FROM evolution_node WHERE id = ?",
                (node["id"],),
            ).fetchone()
            assert row["current_version_id"] == new_version["id"]
            # Reopening never clears the pin, and neither does revising the
            # contract -- production keeps running the established
            # implementation while exploration proceeds.
            assert row["stable_implementation_id"] == implementation["id"]
            superseded = conn.execute(
                "SELECT superseded_by_id FROM evolution_node_version WHERE id = ?",
                (version["id"],),
            ).fetchone()["superseded_by_id"]
            assert superseded == new_version["id"]

    def test_exploring_and_validating_nodes_may_still_add_versions(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Freeze-Open")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="open-node")
            first = add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m1",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=first["id"], modality="rule",
            )
            apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="validating",
                decision_method="manual", actor="alice",
            )
            second = add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m2",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            assert second["version_number"] == 2


# ---------------------------------------------------------------------------
# The monitoring contract must actually resolve (Phase 5's own rows)
# ---------------------------------------------------------------------------


class TestMonitoringContractResolution:
    """`established -> monitoring` asserts that observation is declared, so a
    ref that names nothing verifiable can never satisfy it. Every failure to
    verify is fail-closed `monitoring_contract_invalid`, which stays distinct
    from `monitoring_contract_missing` (no ref at all)."""

    def _established(self, conn, system_id, node_key="watched"):
        return _build_established_node(conn, system_id, node_key)

    def test_a_real_active_contract_allows_the_transition(self, admin_client):
        from app.db import get_conn
        from app.node_operations import create_monitoring_contract

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-MC-Valid")
        with get_conn() as conn:
            node, _v, _i = self._established(conn, system_id)
            create_monitoring_contract(
                conn, system_id=system_id, node_id=node["id"],
                freshness_budget_seconds=60.0, minimum_sample_count=1,
            )
            facts = load_node_facts(conn, system_id=system_id, node_id=node["id"])
            assert facts.monitoring_contract_valid is True

            result = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="monitoring",
                decision_method="deterministic", actor_kind="system",
                evidence_refs=("component:svc-a",),
            )
            assert result.applied, result.decision

    @pytest.mark.parametrize(
        "ref",
        [
            "contract-1",                      # free-form junk
            "node_monitoring_contract:",       # prefix with no id
            "node_monitoring_contract:abc",    # unparseable id
            "node_monitoring_contract:999999",  # no such row
        ],
    )
    def test_an_unresolvable_ref_is_invalid_not_missing(self, admin_client, ref):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, f"EN-MC-Junk-{abs(hash(ref)) % 10000}")
        with get_conn() as conn:
            node, _v, _i = self._established(conn, system_id)
            conn.execute(
                "UPDATE evolution_node SET monitoring_contract_ref = ? WHERE id = ?",
                (ref, node["id"]),
            )
            facts = load_node_facts(conn, system_id=system_id, node_id=node["id"])
            assert facts.monitoring_contract_ref == ref
            assert facts.monitoring_contract_valid is False

            result = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="monitoring",
                decision_method="deterministic", actor_kind="system",
                evidence_refs=("component:svc-a",),
            )
            assert not result.applied
            assert result.decision.reason_code == "monitoring_contract_invalid"

    def test_an_inactive_contract_is_invalid(self, admin_client):
        from app.db import get_conn
        from app.node_operations import create_monitoring_contract

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-MC-Inactive")
        with get_conn() as conn:
            node, _v, _i = self._established(conn, system_id)
            contract = create_monitoring_contract(
                conn, system_id=system_id, node_id=node["id"],
                freshness_budget_seconds=60.0,
            )
            conn.execute(
                "UPDATE node_monitoring_contract SET active = 0 WHERE id = ?",
                (contract["id"],),
            )
            facts = load_node_facts(conn, system_id=system_id, node_id=node["id"])
            assert facts.monitoring_contract_valid is False

            result = apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="monitoring",
                decision_method="deterministic", actor_kind="system",
                evidence_refs=("component:svc-a",),
            )
            assert result.decision.reason_code == "monitoring_contract_invalid"

    def test_a_superseded_contract_is_invalid_while_its_successor_is_not(
        self, admin_client
    ):
        """A second contract version supersedes the first AND repoints the
        Node's ref. Pointing the ref back at the superseded row must not
        pass: the judgement would be made against a contract that is no
        longer what watching this Node means."""
        from app.db import get_conn
        from app.node_operations import create_monitoring_contract

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-MC-Superseded")
        with get_conn() as conn:
            node, _v, _i = self._established(conn, system_id)
            first = create_monitoring_contract(
                conn, system_id=system_id, node_id=node["id"], freshness_budget_seconds=60.0,
            )
            second = create_monitoring_contract(
                conn, system_id=system_id, node_id=node["id"], freshness_budget_seconds=30.0,
            )
            # The successor (what create_monitoring_contract left pointed to).
            facts = load_node_facts(conn, system_id=system_id, node_id=node["id"])
            assert facts.monitoring_contract_ref == f"node_monitoring_contract:{second['id']}"
            assert facts.monitoring_contract_valid is True

            conn.execute(
                "UPDATE evolution_node SET monitoring_contract_ref = ? WHERE id = ?",
                (f"node_monitoring_contract:{first['id']}", node["id"]),
            )
            stale_facts = load_node_facts(conn, system_id=system_id, node_id=node["id"])
            assert stale_facts.monitoring_contract_valid is False

    def test_another_nodes_contract_does_not_count(self, admin_client):
        from app.db import get_conn
        from app.node_operations import create_monitoring_contract

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-MC-OtherNode")
        with get_conn() as conn:
            owner, _v, _i = self._established(conn, system_id, node_key="owner")
            borrower, _v2, _i2 = self._established(
                conn, system_id, node_key="borrower"
            )
            contract = create_monitoring_contract(
                conn, system_id=system_id, node_id=owner["id"], freshness_budget_seconds=60.0,
            )
            conn.execute(
                "UPDATE evolution_node SET monitoring_contract_ref = ? WHERE id = ?",
                (f"node_monitoring_contract:{contract['id']}", borrower["id"]),
            )
            facts = load_node_facts(conn, system_id=system_id, node_id=borrower["id"])
            assert facts.monitoring_contract_valid is False

    def test_another_systems_contract_does_not_count(self, admin_client):
        from app.db import get_conn
        from app.node_operations import create_monitoring_contract

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EN-MC-CrossA")
        sys_b = _create_system(admin_client, token, "EN-MC-CrossB")
        with get_conn() as conn:
            node_a, _v, _i = self._established(conn, sys_a, node_key="cross-a")
            node_b, _v2, _i2 = self._established(conn, sys_b, node_key="cross-b")
            contract = create_monitoring_contract(
                conn, system_id=sys_a, node_id=node_a["id"], freshness_budget_seconds=60.0,
            )
            conn.execute(
                "UPDATE evolution_node SET monitoring_contract_ref = ? WHERE id = ?",
                (f"node_monitoring_contract:{contract['id']}", node_b["id"]),
            )
            facts = load_node_facts(conn, system_id=sys_b, node_id=node_b["id"])
            assert facts.monitoring_contract_valid is False

    def test_a_missing_phase_five_table_fails_closed(self, admin_client):
        """A database migrated only to Phase 1 has no
        `node_monitoring_contract` table. That cannot mean "the contract is
        fine": a ref IS set and cannot be checked, so the gate refuses.

        The Phase-1-only database is simulated with a connection proxy that
        delegates everything EXCEPT that one table -- which is precisely what
        an unmigrated database looks like to this code path."""
        from app.db import get_conn

        class _Phase1OnlyConn:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args, **kwargs):
                if "node_monitoring_contract" in sql:
                    raise sqlite3.OperationalError(
                        "no such table: node_monitoring_contract"
                    )
                return self._real.execute(sql, *args, **kwargs)

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-MC-NoTable")
        with get_conn() as conn:
            node, _v, _i = self._established(conn, system_id)
            conn.execute(
                "UPDATE evolution_node SET monitoring_contract_ref = ? WHERE id = ?",
                ("node_monitoring_contract:1", node["id"]),
            )
            facts = load_node_facts(
                _Phase1OnlyConn(conn), system_id=system_id, node_id=node["id"]
            )

        assert facts.monitoring_contract_ref == "node_monitoring_contract:1"
        assert facts.monitoring_contract_valid is False
        decision = evaluate_transition(
            facts,
            TransitionRequest(
                to_state="monitoring", decision_method="deterministic",
                actor_kind="system", evidence_refs=("component:svc-a",),
            ),
        )
        assert decision.reason_code == "monitoring_contract_invalid"


# ---------------------------------------------------------------------------
# Concurrency: a transition never commits against a stale from_state
# ---------------------------------------------------------------------------


def _raw_conn():
    """A second, INDEPENDENT connection to the test database.

    `db.get_conn()` holds a process-wide lock, so it can never produce two
    concurrent connections -- which is exactly why a test about two racing
    writers has to open its own, mirroring `db._connect()`'s settings plus a
    busy timeout so the loser waits for the write lock instead of failing
    instantly with SQLITE_BUSY.
    """
    from app import db

    conn = sqlite3.connect(db.db_path(), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ready_node(conn, system_id, node_key="racer"):
    """An `exploring` Node with a version and an implementation, i.e. one
    for which `exploring -> validating` satisfies every earlier rule."""
    node = create_node(conn, system_id=system_id, node_key=node_key)
    version = add_version(
        conn, system_id=system_id, node_id=node["id"], mission="m",
        input_contract={}, output_contract={}, side_effect_class="pure",
        trust_boundary="internal",
    )
    add_implementation(
        conn, system_id=system_id, node_id=node["id"],
        node_version_id=version["id"], modality="rule",
    )
    return node


class TestTransitionConcurrency:
    def test_two_connections_cannot_both_apply_from_the_same_from_state(
        self, admin_client
    ):
        """A genuine two-connection race with DIFFERENT idempotency keys --
        the case the unique index does not separate at all.

        Connection A holds the write lock while B calls `apply_transition`.
        B must not have read the maturity yet (it is still blocked), so when
        A commits its own transition, B decides against the state A left
        behind. One applies, the other is refused."""
        import threading

        from app.db import get_conn
        import app.evolution_node as en

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Race-TwoConns")
        with get_conn() as conn:
            node = _ready_node(conn, system_id)
        node_id = node["id"]

        conn_a = _raw_conn()
        conn_b = _raw_conn()
        outcome = {}
        try:
            # A is mid-transition: it already holds the write lock.
            conn_a.execute("BEGIN IMMEDIATE")

            def run_b():
                try:
                    outcome["result"] = apply_transition(
                        conn_b, system_id=system_id, node_id=node_id,
                        to_state="validating", decision_method="manual", actor="bob",
                        idempotency_key="key-b",
                    )
                except Exception as exc:  # pragma: no cover - reported below
                    outcome["error"] = exc

            thread = threading.Thread(target=run_b)
            thread.start()
            thread.join(0.5)
            # B cannot have decided anything yet: it is waiting for the write
            # lock, which is the whole point of taking it before reading.
            assert thread.is_alive(), outcome

            en._insert_event(
                conn_a, node_id=node_id, system_id=system_id, event_kind="transition",
                from_state="exploring", to_state="validating", actor="alice",
                actor_kind="developer", decision_method="manual",
                idempotency_key="key-a",
            )
            conn_a.execute(
                "UPDATE evolution_node SET maturity = 'validating' WHERE id = ?",
                (node_id,),
            )
            conn_a.execute("COMMIT")

            thread.join(10)
            assert not thread.is_alive()
            assert "error" not in outcome, outcome.get("error")
            result = outcome["result"]
            assert not result.applied
            # B read A's committed state, so `exploring -> validating` is no
            # longer on the table at all.
            assert result.decision.reason_code == "illegal_transition"

            rows = conn_a.execute(
                "SELECT from_state, to_state FROM evolution_node_event "
                "WHERE node_id = ? AND event_kind = 'transition' ORDER BY id",
                (node_id,),
            ).fetchall()
            assert [(r["from_state"], r["to_state"]) for r in rows] == [
                ("exploring", "validating")
            ]
            maturity = conn_a.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node_id,)
            ).fetchone()["maturity"]
            assert maturity == "validating"
        finally:
            conn_a.close()
            conn_b.close()

    def test_a_raced_retry_of_the_same_key_is_still_a_duplicate(self, admin_client):
        """The other half of the same race: the SAME idempotency key. The
        winner commits between the caller's pre-check and its transaction, so
        the in-transaction re-check is what keeps the retry a duplicate
        instead of an `illegal_transition` rejection."""
        import app.evolution_node as en

        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Race-SameKey")
        with get_conn() as conn:
            node = _ready_node(conn, system_id, node_key="same-key-racer")
        node_id = node["id"]

        conn_a = _raw_conn()
        conn_b = _raw_conn()
        try:
            real_lookup = en._lookup_idempotent_event
            state = {"calls": 0}

            def lookup_with_a_racing_winner(conn_, **kwargs):
                state["calls"] += 1
                if state["calls"] == 1:
                    # The pre-check misses; the winner then commits on the
                    # OTHER connection, before B takes the write lock.
                    result = real_lookup(conn_, **kwargs)
                    conn_a.execute("BEGIN IMMEDIATE")
                    en._insert_event(
                        conn_a, node_id=node_id, system_id=system_id,
                        event_kind="transition", from_state="exploring",
                        to_state="validating", actor="alice", actor_kind="developer",
                        decision_method="manual", idempotency_key="shared-key",
                    )
                    conn_a.execute(
                        "UPDATE evolution_node SET maturity = 'validating' WHERE id = ?",
                        (node_id,),
                    )
                    conn_a.execute("COMMIT")
                    return result
                return real_lookup(conn_, **kwargs)

            en._lookup_idempotent_event = lookup_with_a_racing_winner
            try:
                result = apply_transition(
                    conn_b, system_id=system_id, node_id=node_id, to_state="validating",
                    decision_method="manual", actor="bob", idempotency_key="shared-key",
                )
            finally:
                en._lookup_idempotent_event = real_lookup

            assert result.duplicate and not result.applied
            assert result.node["maturity"] == "validating"
            count = conn_b.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_event "
                "WHERE node_id = ? AND event_kind = 'transition'",
                (node_id,),
            ).fetchone()["n"]
            assert count == 1
        finally:
            conn_a.close()
            conn_b.close()

    def test_compare_and_set_refuses_a_from_state_that_moved_under_the_request(
        self, admin_client, monkeypatch
    ):
        """Belt and suspenders on top of the write lock: if the maturity ever
        moves between the decision and the UPDATE, the UPDATE matches no row
        and the whole transition is rolled back rather than committed against
        a from_state that is no longer true."""
        import app.evolution_node as en

        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Race-CAS")
        with get_conn() as conn:
            node = _ready_node(conn, system_id, node_key="cas")
            real_evaluate = en.evaluate_transition
            moved = {"done": False}

            def evaluate_then_move_the_row(facts, request):
                decision = real_evaluate(facts, request)
                if not moved["done"]:
                    moved["done"] = True
                    # The lost update the CAS exists to catch: the row is no
                    # longer in the state the decision was made against.
                    conn.execute(
                        "UPDATE evolution_node SET maturity = 'suspended' WHERE id = ?",
                        (node["id"],),
                    )
                return decision

            monkeypatch.setattr(en, "evaluate_transition", evaluate_then_move_the_row)
            with pytest.raises(EvolutionNodeConflictError) as excinfo:
                apply_transition(
                    conn, system_id=system_id, node_id=node["id"], to_state="validating",
                    decision_method="manual", actor="alice", idempotency_key="cas-1",
                )
            monkeypatch.undo()
            assert "maturity changed" in str(excinfo.value)

            # Nothing was recorded, and the injected move was rolled back
            # with it: a refused transition leaves no trace at all.
            row = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
            assert row["maturity"] == "exploring"
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_event "
                "WHERE node_id = ? AND event_kind = 'transition'",
                (node["id"],),
            ).fetchone()["n"]
            assert count == 0


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

    def test_projection_folds_the_event_log_and_reports_agreement(self, admin_client):
        """ADR-4's reconciliation, performed rather than asserted: the
        projection reports both the stored column and the log's fold."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Fold-Consistent")
        with get_conn() as conn:
            node, _v, _i = _build_established_node(conn, system_id, "folded")
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])

        assert projection["maturity"] == "established"
        assert projection["folded_maturity"] == "established"
        assert projection["maturity_consistent"] is True
        assert projection["availability"]["maturity_lineage"] is True

    def test_a_never_transitioned_node_folds_to_none_and_is_consistent(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Fold-Fresh")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="never-moved")
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])

        # No transition event at all folds to None, which is CONSISTENT with
        # the stored creation state -- not a missing lineage.
        assert projection["folded_maturity"] is None
        assert projection["maturity"] == "exploring"
        assert projection["maturity_consistent"] is True

    def test_a_drifted_stored_maturity_is_reported_as_inconsistent(self, admin_client):
        """The drift the log exists to expose. Only a direct column UPDATE
        can produce it -- this module's own persistence never does -- and the
        projection must SHOW it rather than echo the column."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Fold-Drift")
        with get_conn() as conn:
            node, _v, _i = _build_established_node(conn, system_id, "drifted")
            conn.execute(
                "UPDATE evolution_node SET maturity = 'monitoring' WHERE id = ?",
                (node["id"],),
            )
            projection = build_node_projection(conn, system_id=system_id, node_id=node["id"])

        assert projection["maturity"] == "monitoring"
        assert projection["folded_maturity"] == "established"
        assert projection["maturity_consistent"] is False
        assert projection["availability"]["maturity_lineage"] is True

    def test_the_fold_is_not_bounded_by_the_event_page_limit(self, admin_client):
        """A log longer than `event_limit` must still reconcile against its
        WHOLE history -- folding the bounded page would report a healthy Node
        as drifted."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EN-Fold-Bounded")
        with get_conn() as conn:
            node, version, _impl = _build_established_node(conn, system_id, "long-log")
            # Add more events than the page limit used below, without moving
            # the maturity: the newest page therefore contains NO transition.
            for _ in range(5):
                add_link(
                    conn, system_id=system_id, node_id=node["id"],
                    link_kind="capability", target_ref=f"cap-{_}",
                )
            projection = build_node_projection(
                conn, system_id=system_id, node_id=node["id"], event_limit=2
            )

        assert len(projection["events"]) == 2
        assert all(event["event_kind"] != "transition" for event in projection["events"])
        assert projection["folded_maturity"] == "established"
        assert projection["maturity_consistent"] is True

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
