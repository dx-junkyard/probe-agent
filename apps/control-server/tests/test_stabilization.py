"""Tests for Epic #394 Phase 4 (Issue #399): the establishment gate.

What must hold:

1. Every refusal code is reachable and reported exactly.
2. The gate fails closed on ABSENCE, not only on failure -- an unmeasured
   floor refuses as firmly as a violated one.
3. The gate never reads implementation modality: an LLM implementation can be
   established exactly as legitimately as a rule one. Minimising LLM usage is
   not a goal of this Epic, and a gate that could read modality could encode
   that preference.
4. Nothing is composited: a latency win cannot pay for a safety regression.
5. Passing the gate is not approval. Approval needs a named human, goes
   through Phase 1's own transition evaluator, and applies/deploys/publishes
   nothing.
6. Mock and foreign evidence can never establish anything.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import evolution_node
from app.evolution_node import add_implementation, add_version, apply_transition, create_node
from app.stabilization import (
    EVIDENCE_LEVELS,
    GATE_REFUSAL_CODES,
    EvidenceFact,
    GateFacts,
    StabilizationConflictError,
    StabilizationNotFoundError,
    StabilizationValidationError,
    add_evidence,
    approve_package,
    build_package_projection,
    create_package,
    evaluate_establishment_gate,
    evaluate_package,
    reject_package,
)


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "stabilization-test.db"))
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


# ---------------------------------------------------------------------------
# The pure gate
# ---------------------------------------------------------------------------


def _facts(**overrides) -> GateFacts:
    """A package that passes, so each test can break exactly one thing."""
    base = dict(
        package_status="under_review",
        package_superseded=False,
        node_maturity="validating",
        candidate_implementation_present=True,
        candidate_matches_node_version=True,
        evidence=(
            EvidenceFact("node", "criterion", "accuracy", "met"),
            EvidenceFact("node", "floor", "safety", "held"),
        ),
        applicability_envelope_declared=True,
        rollback_target_present=True,
        is_first_establishment=False,
        outcome_unmeasured_reason="",
        required_case_count=0,
        observed_case_count=None,
        stability_window_seconds=0.0,
        observed_window_seconds=None,
    )
    base.update(overrides)
    return GateFacts(**base)


class TestGate:
    def test_a_complete_package_passes(self):
        decision = evaluate_establishment_gate(_facts())
        assert decision.allowed is True
        assert decision.reason_code == "ok"

    def test_an_unmeasured_floor_refuses_as_firmly_as_a_violated_one(self):
        """"The floor held" and "nobody measured the floor" are different
        facts, and only the first may establish. Treating the second as
        passing is how a gate silently becomes decorative."""
        violated = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact("node", "criterion", "accuracy", "met"),
                EvidenceFact("node", "floor", "safety", "violated"),
            ))
        )
        unmeasured = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact("node", "criterion", "accuracy", "met"),
                EvidenceFact("node", "floor", "safety", "unmeasured"),
            ))
        )
        assert violated.allowed is False and violated.reason_code == "floor_violated"
        assert unmeasured.allowed is False
        assert unmeasured.reason_code == "floor_unmeasured"
        assert unmeasured.failing_evidence == ("safety",)

    def test_a_latency_win_cannot_pay_for_a_safety_regression(self):
        """Nothing is summed across dimensions. Every criterion and floor is
        checked individually (ADR-7)."""
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact("node", "criterion", "latency", "met"),
                EvidenceFact("node", "criterion", "accuracy", "met"),
                EvidenceFact("node", "criterion", "cost", "met"),
                EvidenceFact("node", "floor", "safety", "violated"),
            ))
        )
        assert decision.allowed is False
        assert decision.reason_code == "floor_violated"

    def test_mock_evidence_can_never_establish(self):
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact("node", "criterion", "accuracy", "met", is_mock=True),
            ))
        )
        assert decision.reason_code == "mock_evidence"
        assert decision.failing_evidence == ("accuracy",)

    def test_foreign_evidence_can_never_establish(self):
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact(
                    "node", "criterion", "borrowed", "met", belongs_to_package=False
                ),
            ))
        )
        assert decision.reason_code == "foreign_evidence"

    def test_node_level_evidence_is_required(self):
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact("ux_outcome", "outcome", "conversion", "met"),
            ))
        )
        assert decision.reason_code == "node_evidence_missing"

    def test_a_downstream_regression_blocks_a_node_level_win(self):
        """A Node-level win is not evidence that the Flow it sits in
        improved."""
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact("node", "criterion", "accuracy", "met"),
                EvidenceFact(
                    "flow_capability", "downstream_impact", "checkout", "violated"
                ),
            ))
        )
        assert decision.reason_code == "downstream_impact_violated"

    def test_an_unmeasured_outcome_is_allowed_only_when_acknowledged(self):
        """Establishing without an Outcome is allowed; doing so silently is
        not (#391: never infer an Outcome, never let its absence pass
        unremarked)."""
        evidence = (
            EvidenceFact("node", "criterion", "accuracy", "met"),
            EvidenceFact("ux_outcome", "outcome", "task success", "unmeasured"),
        )
        silent = evaluate_establishment_gate(_facts(evidence=evidence))
        assert silent.reason_code == "outcome_unmeasured_unacknowledged"

        acknowledged = evaluate_establishment_gate(
            _facts(
                evidence=evidence,
                outcome_unmeasured_reason="no survey instrument exists yet",
            )
        )
        assert acknowledged.allowed is True

    def test_a_missing_applicability_envelope_refuses(self):
        """Without an envelope a success generalises to every input by
        default -- "it worked on the cases it was built for" and "it worked"
        are different claims."""
        decision = evaluate_establishment_gate(
            _facts(applicability_envelope_declared=False)
        )
        assert decision.reason_code == "applicability_envelope_missing"

    def test_a_first_establishment_needs_no_rollback_target(self):
        """A Node that never had a stable pin has nothing to roll back to,
        and that is legitimate rather than a missing artefact."""
        first = evaluate_establishment_gate(
            _facts(rollback_target_present=False, is_first_establishment=True)
        )
        assert first.allowed is True

        later = evaluate_establishment_gate(
            _facts(rollback_target_present=False, is_first_establishment=False)
        )
        assert later.reason_code == "rollback_target_missing"

    def test_stability_is_checked_against_the_packages_own_declaration(self):
        """No global threshold (#399 forbids one), but the requirement is
        declared before the gate runs so it cannot be lowered to fit the
        result that came back."""
        short = evaluate_establishment_gate(
            _facts(required_case_count=100, observed_case_count=12)
        )
        assert short.reason_code == "stability_window_insufficient"

        unknown = evaluate_establishment_gate(
            _facts(required_case_count=100, observed_case_count=None)
        )
        assert unknown.reason_code == "stability_window_insufficient"

        enough = evaluate_establishment_gate(
            _facts(required_case_count=100, observed_case_count=100)
        )
        assert enough.allowed is True

    def test_window_shorter_than_declared_refuses(self):
        decision = evaluate_establishment_gate(
            _facts(stability_window_seconds=86400.0, observed_window_seconds=3600.0)
        )
        assert decision.reason_code == "stability_window_insufficient"

    def test_structural_preconditions_come_before_evidence_checks(self):
        """Reporting "the safety floor is unmeasured" for a package whose
        candidate no longer exists sends the developer to fix the wrong
        thing."""
        decision = evaluate_establishment_gate(
            _facts(
                candidate_implementation_present=False,
                evidence=(EvidenceFact("node", "floor", "safety", "unmeasured"),),
            )
        )
        assert decision.reason_code == "candidate_implementation_missing"

    def test_a_node_not_validating_is_refused(self):
        decision = evaluate_establishment_gate(_facts(node_maturity="exploring"))
        assert decision.reason_code == "node_not_validating"

    def test_a_superseded_package_is_refused(self):
        decision = evaluate_establishment_gate(_facts(package_superseded=True))
        assert decision.reason_code == "package_superseded"

    def test_a_decided_package_is_refused(self):
        decision = evaluate_establishment_gate(_facts(package_status="approved"))
        assert decision.reason_code == "package_not_draft_or_review"

    def test_a_version_mismatch_is_refused(self):
        decision = evaluate_establishment_gate(
            _facts(candidate_matches_node_version=False)
        )
        assert decision.reason_code == "candidate_version_mismatch"

    def test_an_unmet_criterion_is_refused(self):
        decision = evaluate_establishment_gate(
            _facts(evidence=(EvidenceFact("node", "criterion", "accuracy", "not_met"),))
        )
        assert decision.reason_code == "criterion_not_met"

    def test_every_refusal_code_is_covered_by_this_file(self):
        """A code nobody can produce is a rule nobody enforces."""
        import pathlib

        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        for code in GATE_REFUSAL_CODES:
            assert f'"{code}"' in source, f"refusal code {code} has no test"

    def test_the_gate_cannot_see_implementation_modality(self):
        """An LLM implementation must be able to establish exactly as
        legitimately as a rule one. #399's non-goal is "making LLM usage
        minimal an end in itself" -- a gate that could read modality could
        encode that preference."""
        assert "modality" not in GateFacts.__dataclass_fields__

        import inspect

        from app import stabilization

        gate_source = inspect.getsource(stabilization.evaluate_establishment_gate)
        assert "modality" not in gate_source


# ---------------------------------------------------------------------------
# Persistence and approval
# ---------------------------------------------------------------------------


def _validating_node(conn, system_id, node_key="stabilized", modality="reasoning_llm"):
    node = create_node(conn, system_id=system_id, node_key=node_key)
    version = add_version(
        conn, system_id=system_id, node_id=node["id"], mission="m",
        input_contract={}, output_contract={}, side_effect_class="pure",
        trust_boundary="internal",
    )
    implementation = add_implementation(
        conn, system_id=system_id, node_id=node["id"],
        node_version_id=version["id"], modality=modality,
    )
    apply_transition(
        conn, system_id=system_id, node_id=node["id"], to_state="validating",
        decision_method="manual", actor="alice",
    )
    return node, version, implementation


def _complete_package(conn, system_id, node, implementation):
    package = create_package(
        conn, system_id=system_id, node_id=node["id"],
        candidate_implementation_id=implementation["id"],
        applicability_envelope={"inputs": "JP addresses"},
        created_by="alice",
    )
    add_evidence(
        conn, system_id=system_id, package_id=package["id"],
        evidence_level="node", evidence_kind="criterion", name="accuracy",
        verdict="met",
    )
    add_evidence(
        conn, system_id=system_id, package_id=package["id"],
        evidence_level="node", evidence_kind="floor", name="safety", verdict="held",
    )
    return package


class TestApproval:
    def test_approving_pins_the_candidate_and_establishes_via_phase_1(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Approve")
        with get_conn() as conn:
            node, version, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)

            row, decision, transition = approve_package(
                conn, system_id=system_id, package_id=package["id"],
                approved_by="alice", note="ready",
            )
            after = conn.execute(
                "SELECT * FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
            events = conn.execute(
                """SELECT * FROM evolution_node_event
                       WHERE node_id = ? AND event_kind = 'transition'
                       ORDER BY id""",
                (node["id"],),
            ).fetchall()

        assert decision.allowed is True
        assert row["status"] == "approved"
        assert row["approved_by"] == "alice"
        assert row["decision_method"] == "manual"
        assert after["maturity"] == "established"
        assert after["stable_implementation_id"] == implementation["id"]
        # The transition went through Phase 1's own evaluator and log.
        assert events[-1]["to_state"] == "established"
        assert events[-1]["decision_method"] == "manual"
        assert events[-1]["actor"] == "alice"

    def test_an_llm_implementation_can_be_established(self, admin_client):
        """Fixation is not "we removed the LLM"."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Llm")
        with get_conn() as conn:
            node, _, implementation = _validating_node(
                conn, system_id, modality="reasoning_llm"
            )
            package = _complete_package(conn, system_id, node, implementation)
            _, decision, _ = approve_package(
                conn, system_id=system_id, package_id=package["id"],
                approved_by="alice",
            )
            after = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert decision.allowed is True
        assert after["maturity"] == "established"

    def test_approval_requires_a_named_person(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Anon")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)
            with pytest.raises(StabilizationValidationError):
                approve_package(
                    conn, system_id=system_id, package_id=package["id"],
                    approved_by="  ",
                )

    def test_the_gate_is_re_evaluated_at_approval_not_trusted_from_before(
        self, admin_client
    ):
        """Evidence, maturity, and the candidate can all change between
        reading a PASS and acting on it."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Recheck")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)
            assert evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            ).allowed is True

            # A floor measurement is lost after the developer read the PASS.
            add_evidence(
                conn, system_id=system_id, package_id=package["id"],
                evidence_level="node", evidence_kind="floor", name="latency",
                verdict="unmeasured",
            )
            with pytest.raises(StabilizationConflictError):
                approve_package(
                    conn, system_id=system_id, package_id=package["id"],
                    approved_by="alice",
                )
            after = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert after["maturity"] == "validating"

    def test_rejecting_records_the_decision_and_moves_the_node_nowhere(
        self, admin_client
    ):
        """A rejection is a record that a human looked and said no; it is not
        a demotion."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Reject")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)
            row = reject_package(
                conn, system_id=system_id, package_id=package["id"],
                rejected_by="alice", note="envelope too narrow",
            )
            after = conn.execute(
                "SELECT maturity, stable_implementation_id FROM evolution_node "
                "WHERE id = ?",
                (node["id"],),
            ).fetchone()
        assert row["status"] == "rejected"
        assert after["maturity"] == "validating"
        assert after["stable_implementation_id"] is None

    def test_the_rollback_target_is_read_from_the_node_not_the_caller(
        self, admin_client
    ):
        """Letting a caller assert a rollback target would let a package claim
        one the Node does not have."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Rollback")
        with get_conn() as conn:
            node, version, first = _validating_node(conn, system_id)
            package_1 = _complete_package(conn, system_id, node, first)
            approve_package(
                conn, system_id=system_id, package_id=package_1["id"],
                approved_by="alice",
            )
            # A second candidate, argued after the first was established.
            second = add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"], modality="rule",
            )
            apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="reopened",
                decision_method="manual", actor="alice", reason="drift",
            )
            apply_transition(
                conn, system_id=system_id, node_id=node["id"], to_state="validating",
                decision_method="manual", actor="alice",
            )
            package_2 = _complete_package(conn, system_id, node, second)
            assert package_2["rollback_implementation_id"] == first["id"]
            approve_package(
                conn, system_id=system_id, package_id=package_2["id"],
                approved_by="alice",
            )
            after = conn.execute(
                """SELECT stable_implementation_id, rollback_implementation_id
                       FROM evolution_node WHERE id = ?""",
                (node["id"],),
            ).fetchone()
        assert after["stable_implementation_id"] == second["id"]
        assert after["rollback_implementation_id"] == first["id"]


class TestEvidenceIntegrity:
    def test_an_unresolvable_reference_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Ref")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = create_package(
                conn, system_id=system_id, node_id=node["id"],
                candidate_implementation_id=implementation["id"],
            )
            with pytest.raises(StabilizationNotFoundError):
                add_evidence(
                    conn, system_id=system_id, package_id=package["id"],
                    evidence_level="node", evidence_kind="criterion", name="x",
                    verdict="met", ref_kind="experiment", ref_id=999999,
                )

    def test_evidence_is_not_added_to_a_decided_package(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Closed")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)
            reject_package(
                conn, system_id=system_id, package_id=package["id"],
                rejected_by="alice",
            )
            with pytest.raises(StabilizationConflictError):
                add_evidence(
                    conn, system_id=system_id, package_id=package["id"],
                    evidence_level="node", evidence_kind="criterion", name="late",
                    verdict="met",
                )

    def test_the_projection_groups_evidence_by_level_and_recomputes_the_gate(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Projection")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)
            projection = build_package_projection(
                conn, system_id=system_id, package_id=package["id"]
            )
        assert set(projection["evidence"]) == set(EVIDENCE_LEVELS)
        assert len(projection["evidence"]["node"]) == 2
        assert projection["evidence"]["ux_outcome"] == []
        # The verdict is derived on every read, never stored -- a stored one
        # drifts from the evidence it describes.
        assert projection["gate"]["allowed"] is True
        assert projection["gate"]["reason_code"] == "ok"

    def test_packages_are_system_scoped(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "ST-IsoA")
        sys_b = _create_system(admin_client, token, "ST-IsoB")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, sys_a)
            package = _complete_package(conn, sys_a, node, implementation)
            with pytest.raises(StabilizationNotFoundError):
                build_package_projection(
                    conn, system_id=sys_b, package_id=package["id"]
                )
            with pytest.raises(StabilizationNotFoundError):
                approve_package(
                    conn, system_id=sys_b, package_id=package["id"],
                    approved_by="mallory",
                )
            with pytest.raises(StabilizationNotFoundError):
                create_package(
                    conn, system_id=sys_b, node_id=node["id"],
                    candidate_implementation_id=implementation["id"],
                )


class TestEvidenceRefScoping:
    def test_another_nodes_package_is_still_foreign_evidence(self, admin_client):
        """`load_node_facts` accepts a Stabilization Package as evidence only
        for the Node it argues about. The `foreign_evidence` rule's purpose --
        refusing another Node's evidence -- has to survive that widening."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-RefScope")
        with get_conn() as conn:
            node_a, _, impl_a = _validating_node(conn, system_id, node_key="node-a")
            package_a = _complete_package(conn, system_id, node_a, impl_a)

            # Node B is otherwise ready to establish -- its own stable pin is
            # in place -- so the only thing left to refuse it is the borrowed
            # evidence. Without this the gate stops earlier, on
            # `stable_implementation_missing`, and the test would pass while
            # proving nothing about foreign evidence.
            node_b, _, impl_b = _validating_node(conn, system_id, node_key="node-b")
            evolution_node.pin_stable_implementation(
                conn, system_id=system_id, node_id=node_b["id"],
                implementation_id=impl_b["id"], actor="alice",
            )
            result = evolution_node.apply_transition(
                conn, system_id=system_id, node_id=node_b["id"],
                to_state="established", decision_method="manual", actor="mallory",
                evidence_refs=[f"stabilization_package:{package_a['id']}"],
            )
        assert result.applied is False
        assert result.decision.reason_code == "foreign_evidence"


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
        assert {"stabilization_package", "stabilization_evidence"} <= names


class TestApi:
    def _ready_node(self, admin_client, token, system_id):
        from app.db import get_conn

        with get_conn() as conn:
            node, version, implementation = _validating_node(conn, system_id)
        return node, version, implementation

    def _headers(self, token, system_id):
        return {
            "Authorization": f"Bearer {token}",
            "X-Probe-System-Id": str(system_id),
        }

    def test_reading_a_package_changes_nothing(self, admin_client):
        """The gate verdict is recomputed on every read, but looking at a
        package must not move the Node (#380)."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-ApiRead")
        node, _, implementation = self._ready_node(admin_client, token, system_id)

        r = admin_client.post(
            "/stabilization/packages",
            json={
                "node_id": node["id"],
                "candidate_implementation_id": implementation["id"],
                "applicability_envelope": {"inputs": "JP addresses"},
            },
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        package_id = r.json()["id"]
        # Node-level evidence is still missing, so the gate refuses -- and
        # says exactly which rule refused.
        assert r.json()["gate"]["allowed"] is False
        assert r.json()["gate"]["reason_code"] == "node_evidence_missing"

        r = admin_client.get(
            f"/stabilization/packages/{package_id}",
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        with get_conn() as conn:
            after = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert after["maturity"] == "validating"

    def test_approval_over_http_records_the_authenticated_person(self, admin_client):
        """`approved_by` is never a request field: accepting it would let any
        caller record someone else's approval (#337)."""
        from app.models import StabilizationPackageCreateIn, StabilizationDecisionIn

        assert "approved_by" not in StabilizationPackageCreateIn.model_fields
        assert "approved_by" not in StabilizationDecisionIn.model_fields

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-ApiApprove")
        node, _, implementation = self._ready_node(admin_client, token, system_id)

        r = admin_client.post(
            "/stabilization/packages",
            json={
                "node_id": node["id"],
                "candidate_implementation_id": implementation["id"],
                "applicability_envelope": {"inputs": "JP addresses"},
            },
            headers=self._headers(token, system_id),
        )
        package_id = r.json()["id"]
        for kind, name, verdict in (
            ("criterion", "accuracy", "met"),
            ("floor", "safety", "held"),
        ):
            r = admin_client.post(
                f"/stabilization/packages/{package_id}/evidence",
                json={
                    "evidence_level": "node", "evidence_kind": kind,
                    "name": name, "verdict": verdict,
                },
                headers=self._headers(token, system_id),
            )
            assert r.status_code == 201, r.text

        r = admin_client.post(
            f"/stabilization/packages/{package_id}/approve",
            json={"note": "ok"},
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        assert body["approved_by"] == "root"

    def test_a_refused_gate_is_a_409_carrying_its_reason(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-ApiRefuse")
        node, _, implementation = self._ready_node(admin_client, token, system_id)

        r = admin_client.post(
            "/stabilization/packages",
            json={
                "node_id": node["id"],
                "candidate_implementation_id": implementation["id"],
            },
            headers=self._headers(token, system_id),
        )
        package_id = r.json()["id"]
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/approve",
            json={},
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 409, r.text
        assert "node_evidence_missing" in r.json()["detail"]

    def test_a_package_in_another_system_is_404(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-ApiIsoA")
        other = _create_system(admin_client, token, "ST-ApiIsoB")
        node, _, implementation = self._ready_node(admin_client, token, system_id)
        r = admin_client.post(
            "/stabilization/packages",
            json={
                "node_id": node["id"],
                "candidate_implementation_id": implementation["id"],
            },
            headers=self._headers(token, system_id),
        )
        package_id = r.json()["id"]
        r = admin_client.get(
            f"/stabilization/packages/{package_id}",
            headers=self._headers(token, other),
        )
        assert r.status_code == 404, r.text
