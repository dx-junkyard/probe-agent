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
7. Nothing that can SATISFY the gate is accepted on its author's word: an
   asserted `met`/`held` must cite a run that was executed and still resolves
   in this System, and the package must declare -- positively, in advance --
   how much evidence it considers sufficient.
8. The parent review and the human approval are separate records (#304), and
   one person cannot discharge both.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import evolution_node
from app.evolution_node import add_implementation, add_version, apply_transition, create_node
from app.stabilization import (
    APPROVAL_REFUSAL_CODES,
    EVIDENCE_LEVELS,
    GATE_REFUSAL_CODES,
    PARENT_REVIEW_DISPOSITIONS,
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
    record_parent_review,
    reject_package,
    supersede_package,
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


def _referenced(level, kind, name, verdict, **overrides) -> EvidenceFact:
    """An asserting row as it looks once it cites an executed run."""
    fields = dict(execution_ref_present=True)
    fields.update(overrides)
    return EvidenceFact(level, kind, name, verdict, **fields)


def _facts(**overrides) -> GateFacts:
    """A package that passes, so each test can break exactly one thing."""
    base = dict(
        package_status="under_review",
        package_superseded=False,
        node_maturity="validating",
        candidate_implementation_present=True,
        candidate_matches_node_version=True,
        package_matches_node_version=True,
        evidence=(
            _referenced("node", "criterion", "accuracy", "met"),
            _referenced("node", "floor", "safety", "held"),
        ),
        applicability_envelope_declared=True,
        rollback_target_present=True,
        is_first_establishment=False,
        outcome_unmeasured_reason="",
        required_case_count=20,
        observed_case_count=25,
        stability_window_seconds=3600.0,
        observed_window_seconds=7200.0,
        parent_review_disposition="endorsed",
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
                _referenced("node", "criterion", "accuracy", "met"),
                EvidenceFact("node", "floor", "safety", "violated"),
            ))
        )
        unmeasured = evaluate_establishment_gate(
            _facts(evidence=(
                _referenced("node", "criterion", "accuracy", "met"),
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
                _referenced("node", "criterion", "latency", "met"),
                _referenced("node", "criterion", "accuracy", "met"),
                _referenced("node", "criterion", "cost", "met"),
                EvidenceFact("node", "floor", "safety", "violated"),
            ))
        )
        assert decision.allowed is False
        assert decision.reason_code == "floor_violated"

    def test_mock_evidence_can_never_establish(self):
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                _referenced("node", "criterion", "accuracy", "met", is_mock=True),
            ))
        )
        assert decision.reason_code == "mock_evidence"
        assert decision.failing_evidence == ("accuracy",)

    def test_foreign_evidence_can_never_establish(self):
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                _referenced(
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

    def test_node_level_presence_alone_is_not_an_asserted_result(self):
        """A node-level row that asserts nothing satisfies nothing. Accepting
        presence would let a package clear this row with a `not_applicable`
        sentence -- and since only asserting rows need a reference, it would
        also be the one reading under which a satisfying row could be
        self-reported."""
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact("node", "outcome", "task success", "not_applicable"),
                EvidenceFact("node", "criterion", "accuracy", "unmeasured"),
            ))
        )
        assert decision.allowed is False
        assert decision.reason_code == "node_evidence_missing"

    def test_a_downstream_regression_blocks_a_node_level_win(self):
        """A Node-level win is not evidence that the Flow it sits in
        improved."""
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                _referenced("node", "criterion", "accuracy", "met"),
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
            _referenced("node", "criterion", "accuracy", "met"),
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

    def test_a_moved_contract_version_is_refused(self):
        """The candidate can still match the package -- both were written
        against the OLD contract -- so `candidate_version_mismatch` cannot
        catch a Node whose contract moved after the package was written."""
        decision = evaluate_establishment_gate(
            _facts(package_matches_node_version=False)
        )
        assert decision.allowed is False
        assert decision.reason_code == "contract_version_moved"

    def test_a_stale_evidence_reference_is_refused(self):
        """Evidence is referenced, never copied, precisely so currency can be
        evaluated at gate time: a result whose source was deleted or ended
        without completing is not current, however it read when attached."""
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                _referenced("node", "criterion", "accuracy", "met",
                            ref_current=False),
                _referenced("node", "floor", "safety", "held"),
            ))
        )
        assert decision.allowed is False
        assert decision.reason_code == "evidence_ref_stale"
        assert decision.failing_evidence == ("accuracy",)

    def test_a_self_reported_result_cannot_establish(self):
        """The reviewer's reproduction: two hand-typed rows with no
        provenance. A `met` nobody can trace back to a run that executed is a
        claim, and a gate that accepts claims is decorative."""
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                EvidenceFact("node", "criterion", "accuracy", "met"),
                EvidenceFact("node", "floor", "safety", "held"),
            ))
        )
        assert decision.allowed is False
        assert decision.reason_code == "evidence_ref_missing"
        assert decision.failing_evidence == ("accuracy", "safety")

    def test_a_negative_self_report_stays_ref_free(self):
        """A `not_met` can only ever BLOCK. Demanding provenance for it would
        make refusing harder than establishing -- exactly backwards for a
        fail-closed gate, and a developer who noticed a regression must always
        be able to write it down."""
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                _referenced("node", "criterion", "accuracy", "met"),
                EvidenceFact("node", "floor", "safety", "violated"),
            ))
        )
        assert decision.reason_code == "floor_violated"

    def test_a_policy_reference_is_not_an_executed_result(self):
        """`evaluation_policy` says what was REQUIRED, never what was
        observed; citing it as the provenance of a `met` cites the question as
        the answer. `gather_gate_facts` therefore never marks such a row as
        carrying an execution reference."""
        from app.stabilization import EXECUTION_REF_KINDS

        assert "evaluation_policy" not in EXECUTION_REF_KINDS

    def test_a_package_declaring_no_sufficiency_is_refused(self):
        """The columns default to 0, and 0 is not "no requirement" -- it is
        "this package never said how much evidence would be enough"."""
        no_cases = evaluate_establishment_gate(
            _facts(required_case_count=0, observed_case_count=None)
        )
        assert no_cases.allowed is False
        assert no_cases.reason_code == "stability_declaration_missing"

        no_window = evaluate_establishment_gate(
            _facts(stability_window_seconds=0.0, observed_window_seconds=None)
        )
        assert no_window.reason_code == "stability_declaration_missing"

    def test_the_parent_review_is_required_and_is_not_the_approval(self):
        """#304: parent approval and human approval are separate records.
        A package nobody's parent has looked at is refused with its own
        code, and a decline is a recorded judgement rather than an obstacle
        to route around."""
        missing = evaluate_establishment_gate(_facts(parent_review_disposition=None))
        assert missing.allowed is False
        assert missing.reason_code == "parent_review_missing"

        declined = evaluate_establishment_gate(
            _facts(parent_review_disposition="declined")
        )
        assert declined.allowed is False
        assert declined.reason_code == "parent_review_declined"

    def test_the_parent_review_is_checked_after_the_evidence(self):
        """Sending a developer to ask a parent for a review while the floor
        is still unmeasured spends somebody else's attention on a package
        that cannot pass anyway."""
        decision = evaluate_establishment_gate(
            _facts(
                parent_review_disposition=None,
                evidence=(
                    _referenced("node", "criterion", "accuracy", "met"),
                    EvidenceFact("node", "floor", "safety", "unmeasured"),
                ),
            )
        )
        assert decision.reason_code == "floor_unmeasured"

    def test_an_unmet_criterion_is_refused(self):
        decision = evaluate_establishment_gate(
            _facts(evidence=(
                _referenced("node", "criterion", "throughput", "met"),
                EvidenceFact("node", "criterion", "accuracy", "not_met"),
            ))
        )
        assert decision.reason_code == "criterion_not_met"

    def test_every_refusal_code_is_covered_by_this_file(self):
        """A code nobody can produce is a rule nobody enforces."""
        import pathlib

        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        for code in GATE_REFUSAL_CODES + APPROVAL_REFUSAL_CODES:
            assert f'"{code}"' in source, f"refusal code {code} has no test"

    def test_the_approval_only_refusal_is_not_a_gate_row(self):
        """The gate is recomputed on every read, where no approver exists to
        compare against -- so "the parent and the approver are the same
        person" cannot be one of its rows. It is still a finite code, just a
        different vocabulary."""
        assert "parent_approver_identical" not in GATE_REFUSAL_CODES
        assert APPROVAL_REFUSAL_CODES == ("parent_approver_identical",)

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


def _exploration_run(conn, system_id, node, version, status="completed"):
    """A real executed run for evidence to cite. Asserted results may not be
    self-reported, so every fixture that expects a PASS has to produce one."""
    return conn.execute(
        """INSERT INTO exploration_run
               (system_id, node_id, node_version_id, status, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (system_id, node["id"], version["id"], status, time.time()),
    ).lastrowid


def _complete_package(
    conn, system_id, node, implementation, version=None, parent="carol",
):
    """A package that passes the gate end to end.

    Three things are load-bearing and each closes one of the holes this file
    exists to keep closed: the asserted results cite an executed run, the
    package declares positively how much evidence is enough, and a parent
    other than the eventual approver has endorsed it.
    """
    if version is None:
        version = conn.execute(
            "SELECT * FROM evolution_node_version WHERE id = ?",
            (implementation["node_version_id"],),
        ).fetchone()
    run_id = _exploration_run(conn, system_id, node, version)
    package = create_package(
        conn, system_id=system_id, node_id=node["id"],
        candidate_implementation_id=implementation["id"],
        applicability_envelope={"inputs": "JP addresses"},
        required_case_count=20, stability_window_seconds=3600.0,
        observed_case_count=25, observed_window_seconds=7200.0,
        created_by="alice",
    )
    add_evidence(
        conn, system_id=system_id, package_id=package["id"],
        evidence_level="node", evidence_kind="criterion", name="accuracy",
        verdict="met", ref_kind="exploration_run", ref_id=run_id,
    )
    add_evidence(
        conn, system_id=system_id, package_id=package["id"],
        evidence_level="node", evidence_kind="floor", name="safety", verdict="held",
        ref_kind="exploration_run", ref_id=run_id,
    )
    if parent:
        record_parent_review(
            conn, system_id=system_id, package_id=package["id"],
            reviewed_by=parent, disposition="endorsed",
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

    def test_rejection_requires_a_named_person(self, admin_client):
        """A rejection is a named human's decision exactly as an approval
        is -- an anonymous rejection is not a reviewable record."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-AnonReject")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)
            with pytest.raises(StabilizationValidationError):
                reject_package(
                    conn, system_id=system_id, package_id=package["id"],
                    rejected_by="  ",
                )
            after = conn.execute(
                "SELECT status FROM stabilization_package WHERE id = ?",
                (package["id"],),
            ).fetchone()
        assert after["status"] == "draft"

    def test_a_moved_contract_version_flips_the_gate_verdict(self, admin_client):
        """The reviewer's reproduction: the package reads ok, then the
        contract version moves. The candidate still matches the PACKAGE's
        frozen version, so only a check against the Node's CURRENT version
        can change the verdict -- which `gather_gate_facts`' currency promise
        requires."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Moved")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)
            assert evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            ).allowed is True

            add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m2",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            decision = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
        assert decision.allowed is False
        assert decision.reason_code == "contract_version_moved"

    def test_a_refused_approval_leaves_no_half_established_state(self, admin_client):
        """The reviewer's reproduction: the contract moves after the package
        was written, then approval is attempted. The refusal must leave the
        Node's pin, rollback slot, and event log exactly as they were -- a
        committed pin on a Node that stayed `validating` is the half-state
        ADR-4 forbids."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Half")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)
            add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m2",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            with pytest.raises(StabilizationConflictError):
                approve_package(
                    conn, system_id=system_id, package_id=package["id"],
                    approved_by="alice",
                )
            after = conn.execute(
                """SELECT maturity, stable_implementation_id,
                          rollback_implementation_id
                       FROM evolution_node WHERE id = ?""",
                (node["id"],),
            ).fetchone()
            pin_events = conn.execute(
                """SELECT COUNT(*) AS n FROM evolution_node_event
                       WHERE node_id = ?
                         AND event_kind IN ('stable_pinned', 'rollback_pinned')""",
                (node["id"],),
            ).fetchone()["n"]
            package_after = conn.execute(
                "SELECT status FROM stabilization_package WHERE id = ?",
                (package["id"],),
            ).fetchone()
        assert after["maturity"] == "validating"
        assert after["stable_implementation_id"] is None
        assert after["rollback_implementation_id"] is None
        assert pin_events == 0
        assert package_after["status"] == "draft"

    def test_a_race_refusal_after_the_pin_restores_the_pin_state(
        self, admin_client, monkeypatch
    ):
        """The pre-validation makes a post-pin refusal a race-only path; when
        that race happens anyway, the pin and its events must be restored in
        the same request rather than surviving as a committed half-state."""
        from app.db import get_conn
        from app.evolution_node import TransitionApplyResult, TransitionDecision

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Race")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(conn, system_id, node, implementation)

            def _raced(*args, **kwargs):
                return TransitionApplyResult(
                    decision=TransitionDecision(
                        False, "stale_implementation",
                        "the contract moved between validation and apply",
                    ),
                    applied=False, duplicate=False, event=None, node={},
                )

            monkeypatch.setattr(evolution_node, "apply_transition", _raced)
            with pytest.raises(StabilizationConflictError):
                approve_package(
                    conn, system_id=system_id, package_id=package["id"],
                    approved_by="alice",
                )
            after = conn.execute(
                """SELECT maturity, stable_implementation_id,
                          rollback_implementation_id
                       FROM evolution_node WHERE id = ?""",
                (node["id"],),
            ).fetchone()
            pin_events = conn.execute(
                """SELECT COUNT(*) AS n FROM evolution_node_event
                       WHERE node_id = ?
                         AND event_kind IN ('stable_pinned', 'rollback_pinned')""",
                (node["id"],),
            ).fetchone()["n"]
            package_after = conn.execute(
                "SELECT status FROM stabilization_package WHERE id = ?",
                (package["id"],),
            ).fetchone()
        assert after["maturity"] == "validating"
        assert after["stable_implementation_id"] is None
        assert after["rollback_implementation_id"] is None
        assert pin_events == 0
        assert package_after["status"] == "draft"

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


class TestEvidenceProvenance:
    """Nothing that can SATISFY the gate is accepted on its author's word."""

    def test_an_asserted_result_without_a_reference_is_refused_at_write_time(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-SelfReport")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = create_package(
                conn, system_id=system_id, node_id=node["id"],
                candidate_implementation_id=implementation["id"],
                applicability_envelope={"inputs": "JP addresses"},
            )
            for kind, name, verdict in (
                ("criterion", "accuracy", "met"),
                ("floor", "safety", "held"),
                ("stability", "no regressions", "held"),
            ):
                with pytest.raises(StabilizationValidationError):
                    add_evidence(
                        conn, system_id=system_id, package_id=package["id"],
                        evidence_level="node", evidence_kind=kind, name=name,
                        verdict=verdict,
                    )

    def test_a_policy_reference_is_not_an_executed_run(self, admin_client):
        """A policy declares what was required, never what was observed."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-PolicyRef")
        with get_conn() as conn:
            node, version, implementation = _validating_node(conn, system_id)
            policy_id = conn.execute(
                """INSERT INTO evolution_evaluation_policy
                       (system_id, policy_key, level, title, created_at)
                   VALUES (?, 'accuracy', 'node', 'accuracy policy', ?)""",
                (system_id, time.time()),
            ).lastrowid
            package = create_package(
                conn, system_id=system_id, node_id=node["id"],
                candidate_implementation_id=implementation["id"],
            )
            with pytest.raises(StabilizationValidationError):
                add_evidence(
                    conn, system_id=system_id, package_id=package["id"],
                    evidence_level="node", evidence_kind="criterion",
                    name="accuracy", verdict="met",
                    ref_kind="evaluation_policy", ref_id=policy_id,
                )

    def test_negative_and_absent_verdicts_stay_ref_free(self, admin_client):
        """A negative report can only ever block, and `unmeasured` /
        `not_applicable` have no run to cite -- that absence IS the fact."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-NegativeRef")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = create_package(
                conn, system_id=system_id, node_id=node["id"],
                candidate_implementation_id=implementation["id"],
            )
            for kind, name, verdict in (
                ("criterion", "accuracy", "not_met"),
                ("floor", "safety", "violated"),
                ("floor", "latency", "unmeasured"),
                ("criterion", "cost", "not_applicable"),
                ("outcome", "task success", "met"),
                ("downstream_impact", "checkout", "met"),
            ):
                row = add_evidence(
                    conn, system_id=system_id, package_id=package["id"],
                    evidence_level="node", evidence_kind=kind, name=name,
                    verdict=verdict,
                )
                assert row["ref_kind"] is None

    def test_another_nodes_run_cannot_be_cited_as_this_nodes_evidence(
        self, admin_client
    ):
        """System scoping alone stopped being enough the moment a reference
        became REQUIRED: a developer forced to supply one could otherwise
        satisfy the requirement with any run in the System."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-CrossNode")
        with get_conn() as conn:
            node_a, _, impl_a = _validating_node(conn, system_id, node_key="node-a")
            node_b, version_b, _ = _validating_node(conn, system_id, node_key="node-b")
            foreign_run = _exploration_run(conn, system_id, node_b, version_b)
            package = create_package(
                conn, system_id=system_id, node_id=node_a["id"],
                candidate_implementation_id=impl_a["id"],
            )
            with pytest.raises(StabilizationValidationError):
                add_evidence(
                    conn, system_id=system_id, package_id=package["id"],
                    evidence_level="node", evidence_kind="criterion",
                    name="accuracy", verdict="met",
                    ref_kind="exploration_run", ref_id=foreign_run,
                )

    def test_a_stored_cross_node_reference_is_foreign_evidence_at_gate_time(
        self, admin_client
    ):
        """Re-checked at gate time, not trusted from the write path: a row can
        reach the table some other way, and a result about a different Node
        must never establish this one."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-CrossNodeGate")
        with get_conn() as conn:
            node_a, _, impl_a = _validating_node(conn, system_id, node_key="node-a")
            node_b, version_b, _ = _validating_node(conn, system_id, node_key="node-b")
            foreign_run = _exploration_run(conn, system_id, node_b, version_b)
            package = _complete_package(conn, system_id, node_a, impl_a)
            conn.execute(
                """INSERT INTO stabilization_evidence
                       (package_id, system_id, evidence_level, evidence_kind,
                        name, verdict, ref_kind, ref_id, detail, is_mock,
                        source, created_at)
                   VALUES (?, ?, 'node', 'criterion', 'borrowed', 'met',
                           'exploration_run', ?, '', 0, 'deterministic', ?)""",
                (package["id"], system_id, foreign_run, time.time()),
            )
            decision = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
        assert decision.allowed is False
        assert decision.reason_code == "foreign_evidence"
        assert decision.failing_evidence == ("borrowed",)

    def test_a_legacy_unreferenced_row_still_fails_the_gate(self, admin_client):
        """Rows written before `add_evidence` required a reference must not be
        able to establish a Node either -- which is why the gate re-checks
        rather than trusting the write path."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-LegacyRow")
        with get_conn() as conn:
            node, version, implementation = _validating_node(conn, system_id)
            run_id = _exploration_run(conn, system_id, node, version)
            package = create_package(
                conn, system_id=system_id, node_id=node["id"],
                candidate_implementation_id=implementation["id"],
                applicability_envelope={"inputs": "JP addresses"},
                required_case_count=20, stability_window_seconds=3600.0,
                observed_case_count=25, observed_window_seconds=7200.0,
            )
            add_evidence(
                conn, system_id=system_id, package_id=package["id"],
                evidence_level="node", evidence_kind="criterion", name="accuracy",
                verdict="met", ref_kind="exploration_run", ref_id=run_id,
            )
            record_parent_review(
                conn, system_id=system_id, package_id=package["id"],
                reviewed_by="carol", disposition="endorsed",
            )
            assert evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            ).allowed is True

            # Written around the validation, the way a pre-change database
            # already holds rows.
            conn.execute(
                """INSERT INTO stabilization_evidence
                       (package_id, system_id, evidence_level, evidence_kind,
                        name, verdict, detail, is_mock, source, created_at)
                   VALUES (?, ?, 'node', 'floor', 'safety', 'held', '', 0,
                           'deterministic', ?)""",
                (package["id"], system_id, time.time()),
            )
            decision = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
            with pytest.raises(StabilizationConflictError):
                approve_package(
                    conn, system_id=system_id, package_id=package["id"],
                    approved_by="alice",
                )
            after = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert decision.allowed is False
        assert decision.reason_code == "evidence_ref_missing"
        assert decision.failing_evidence == ("safety",)
        assert after["maturity"] == "validating"

    def test_a_package_declaring_no_sufficiency_cannot_be_approved(
        self, admin_client
    ):
        """`required_case_count` / `stability_window_seconds` default to 0, and
        a draft is allowed to be incomplete -- but the gate refuses it. The
        magnitude stays the developer's; the DECLARATION is mandatory."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-NoDeclaration")
        with get_conn() as conn:
            node, version, implementation = _validating_node(conn, system_id)
            run_id = _exploration_run(conn, system_id, node, version)
            package = create_package(
                conn, system_id=system_id, node_id=node["id"],
                candidate_implementation_id=implementation["id"],
                applicability_envelope={"inputs": "JP addresses"},
            )
            add_evidence(
                conn, system_id=system_id, package_id=package["id"],
                evidence_level="node", evidence_kind="criterion", name="accuracy",
                verdict="met", ref_kind="exploration_run", ref_id=run_id,
            )
            add_evidence(
                conn, system_id=system_id, package_id=package["id"],
                evidence_level="node", evidence_kind="floor", name="safety",
                verdict="held", ref_kind="exploration_run", ref_id=run_id,
            )
            record_parent_review(
                conn, system_id=system_id, package_id=package["id"],
                reviewed_by="carol", disposition="endorsed",
            )
            decision = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
            with pytest.raises(StabilizationConflictError):
                approve_package(
                    conn, system_id=system_id, package_id=package["id"],
                    approved_by="alice",
                )
            after = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert decision.allowed is False
        assert decision.reason_code == "stability_declaration_missing"
        assert after["maturity"] == "validating"


class TestParentReview:
    """#304: the parent review and the human approval are two records."""

    def test_the_gate_requires_an_endorsement_and_approval_needs_two_people(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-ParentDomain")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(
                conn, system_id, node, implementation, parent=None
            )
            unreviewed = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
            assert unreviewed.reason_code == "parent_review_missing"

            record_parent_review(
                conn, system_id=system_id, package_id=package["id"],
                reviewed_by="carol", disposition="endorsed", note="fits the Flow",
            )
            assert evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            ).allowed is True

            # The same person cannot discharge both responsibilities.
            with pytest.raises(StabilizationConflictError) as exc:
                approve_package(
                    conn, system_id=system_id, package_id=package["id"],
                    approved_by="Carol",
                )
            assert "parent_approver_identical" in str(exc.value)

            row, decision, _ = approve_package(
                conn, system_id=system_id, package_id=package["id"],
                approved_by="alice",
            )
        assert decision.allowed is True
        assert row["approved_by"] == "alice"
        assert row["parent_reviewed_by"] == "carol"
        assert row["parent_review_disposition"] == "endorsed"
        assert row["parent_review_note"] == "fits the Flow"
        assert row["decision_method"] == "manual"

    def test_a_parent_review_requires_a_named_person_and_a_finite_disposition(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-ParentAnon")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(
                conn, system_id, node, implementation, parent=None
            )
            with pytest.raises(StabilizationValidationError):
                record_parent_review(
                    conn, system_id=system_id, package_id=package["id"],
                    reviewed_by="  ", disposition="endorsed",
                )
            with pytest.raises(StabilizationValidationError):
                record_parent_review(
                    conn, system_id=system_id, package_id=package["id"],
                    reviewed_by="carol", disposition="looks fine",
                )

    def test_rejection_never_requires_a_parent_review(self, admin_client):
        """Refusing must always be possible. A package nobody's parent has
        looked at can still be rejected -- otherwise a missing review would
        keep a bad package alive."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-RejectNoParent")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            package = _complete_package(
                conn, system_id, node, implementation, parent=None
            )
            row = reject_package(
                conn, system_id=system_id, package_id=package["id"],
                rejected_by="alice", note="not worth pinning",
            )
            after = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert row["status"] == "rejected"
        assert row["parent_review_disposition"] is None
        assert after["maturity"] == "validating"

    def test_supersession_never_requires_a_parent_review(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-SupNoParent")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
            first = _complete_package(
                conn, system_id, node, implementation, parent=None
            )
            second = _complete_package(
                conn, system_id, node, implementation, parent=None
            )
            row = supersede_package(
                conn, system_id=system_id, package_id=first["id"],
                successor_package_id=second["id"], superseded_by="alice",
            )
        assert row["status"] == "superseded"

    def test_the_parent_review_is_system_scoped(self, admin_client):
        """A neighbouring System must not be able to endorse -- or read --
        this System's package."""
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "ST-ParentIsoA")
        sys_b = _create_system(admin_client, token, "ST-ParentIsoB")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, sys_a)
            package = _complete_package(
                conn, sys_a, node, implementation, parent=None
            )
            with pytest.raises(StabilizationNotFoundError):
                record_parent_review(
                    conn, system_id=sys_b, package_id=package["id"],
                    reviewed_by="mallory", disposition="endorsed",
                )
            record_parent_review(
                conn, system_id=sys_a, package_id=package["id"],
                reviewed_by="carol", disposition="endorsed",
            )
            projection = build_package_projection(
                conn, system_id=sys_a, package_id=package["id"]
            )
            with pytest.raises(StabilizationNotFoundError):
                build_package_projection(
                    conn, system_id=sys_b, package_id=package["id"]
                )
        assert projection["parent_reviewed_by"] == "carol"


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


class TestEvidenceRefCurrency:
    """`stabilization_evidence.ref_id` carries no FK, so the row it named can
    be gone or abandoned by the time the gate runs. Currency is re-resolved
    at every evaluation -- the promise `gather_gate_facts` documents."""

    def _package_with_run_evidence(self, conn, system_id, run_status="completed"):
        node, version, implementation = _validating_node(conn, system_id)
        run_id = _exploration_run(conn, system_id, node, version, status=run_status)
        # The floor cites its OWN run, so a test that kills `run_id` names
        # exactly one failing row rather than every row in the package.
        floor_run_id = _exploration_run(conn, system_id, node, version)
        package = create_package(
            conn, system_id=system_id, node_id=node["id"],
            candidate_implementation_id=implementation["id"],
            applicability_envelope={"inputs": "JP addresses"},
            required_case_count=20, stability_window_seconds=3600.0,
            observed_case_count=25, observed_window_seconds=7200.0,
        )
        add_evidence(
            conn, system_id=system_id, package_id=package["id"],
            evidence_level="node", evidence_kind="criterion", name="accuracy",
            verdict="met", ref_kind="exploration_run", ref_id=run_id,
        )
        add_evidence(
            conn, system_id=system_id, package_id=package["id"],
            evidence_level="node", evidence_kind="floor", name="safety",
            verdict="held", ref_kind="exploration_run", ref_id=floor_run_id,
        )
        record_parent_review(
            conn, system_id=system_id, package_id=package["id"],
            reviewed_by="carol", disposition="endorsed",
        )
        return package, run_id

    def test_a_completed_source_run_stays_current(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-RefOk")
        with get_conn() as conn:
            package, _ = self._package_with_run_evidence(conn, system_id)
            decision = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
        assert decision.allowed is True

    def test_a_deleted_source_run_flips_the_gate(self, admin_client):
        """A reference that no longer resolves would otherwise read as
        `met` forever."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-RefGone")
        with get_conn() as conn:
            package, run_id = self._package_with_run_evidence(conn, system_id)
            assert evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            ).allowed is True
            conn.execute("DELETE FROM exploration_run WHERE id = ?", (run_id,))
            decision = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
        assert decision.allowed is False
        assert decision.reason_code == "evidence_ref_stale"
        assert decision.failing_evidence == ("accuracy",)

    def test_an_abandoned_source_run_flips_the_gate(self, admin_client):
        """An abandoned run concluded it produced no usable result; a result
        borrowed from it is not current evidence."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-RefDead")
        with get_conn() as conn:
            package, run_id = self._package_with_run_evidence(conn, system_id)
            conn.execute(
                "UPDATE exploration_run SET status = 'abandoned' WHERE id = ?",
                (run_id,),
            )
            decision = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
        assert decision.allowed is False
        assert decision.reason_code == "evidence_ref_stale"

    def test_an_open_source_run_is_current_not_dead(self, admin_client):
        """`open` is unfinished, not failed -- only a non-successful TERMINAL
        state kills the reference."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-RefOpen")
        with get_conn() as conn:
            package, _ = self._package_with_run_evidence(
                conn, system_id, run_status="open"
            )
            decision = evaluate_package(
                conn, system_id=system_id, package_id=package["id"]
            )
        assert decision.allowed is True


class TestSupersede:
    def _two_packages(self, conn, system_id):
        node, _, implementation = _validating_node(conn, system_id)
        first = _complete_package(conn, system_id, node, implementation)
        second = _complete_package(conn, system_id, node, implementation)
        return node, first, second

    def test_superseding_makes_package_superseded_reachable(self, admin_client):
        """`package_superseded` must be producible through real code, not
        only via manual DB edits -- a code nobody can produce is a rule
        nobody enforces."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-Supersede")
        with get_conn() as conn:
            node, first, second = self._two_packages(conn, system_id)
            row = supersede_package(
                conn, system_id=system_id, package_id=first["id"],
                successor_package_id=second["id"], superseded_by="alice",
                note="newer evidence set",
            )
            decision = evaluate_package(
                conn, system_id=system_id, package_id=first["id"]
            )
            after_node = conn.execute(
                "SELECT maturity FROM evolution_node WHERE id = ?", (node["id"],)
            ).fetchone()
        assert row["status"] == "superseded"
        assert row["superseded_by_id"] == second["id"]
        assert row["approved_by"] == "alice"
        assert decision.allowed is False
        assert decision.reason_code == "package_superseded"
        # Superseding is a record about packages; it moves the Node nowhere.
        assert after_node["maturity"] == "validating"

    def test_supersession_requires_a_named_person(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-SupAnon")
        with get_conn() as conn:
            _, first, second = self._two_packages(conn, system_id)
            with pytest.raises(StabilizationValidationError):
                supersede_package(
                    conn, system_id=system_id, package_id=first["id"],
                    successor_package_id=second["id"], superseded_by="  ",
                )

    def test_a_package_cannot_supersede_itself(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-SupSelf")
        with get_conn() as conn:
            _, first, _ = self._two_packages(conn, system_id)
            with pytest.raises(StabilizationValidationError):
                supersede_package(
                    conn, system_id=system_id, package_id=first["id"],
                    successor_package_id=first["id"], superseded_by="alice",
                )

    def test_a_decided_package_cannot_be_superseded(self, admin_client):
        """An approved or rejected package is a decision that already
        happened; rewriting its status would rewrite history."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-SupDecided")
        with get_conn() as conn:
            _, first, second = self._two_packages(conn, system_id)
            reject_package(
                conn, system_id=system_id, package_id=first["id"],
                rejected_by="alice",
            )
            with pytest.raises(StabilizationConflictError):
                supersede_package(
                    conn, system_id=system_id, package_id=first["id"],
                    successor_package_id=second["id"], superseded_by="alice",
                )

    def test_the_successor_must_argue_about_the_same_node(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-SupOtherNode")
        with get_conn() as conn:
            _, first, _ = self._two_packages(conn, system_id)
            other_node, _, other_impl = _validating_node(
                conn, system_id, node_key="other-node"
            )
            other_package = _complete_package(
                conn, system_id, other_node, other_impl
            )
            with pytest.raises(StabilizationValidationError):
                supersede_package(
                    conn, system_id=system_id, package_id=first["id"],
                    successor_package_id=other_package["id"],
                    superseded_by="alice",
                )

    def test_a_dead_successor_is_refused(self, admin_client):
        """Pointing "establish from that" at a rejected package would send
        the developer to a dead end."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-SupDeadSucc")
        with get_conn() as conn:
            _, first, second = self._two_packages(conn, system_id)
            reject_package(
                conn, system_id=system_id, package_id=second["id"],
                rejected_by="alice",
            )
            with pytest.raises(StabilizationConflictError):
                supersede_package(
                    conn, system_id=system_id, package_id=first["id"],
                    successor_package_id=second["id"], superseded_by="alice",
                )

    def test_supersession_over_http_records_the_authenticated_person(
        self, admin_client
    ):
        """The deciding person comes from the principal, never the body."""
        from app.db import get_conn
        from app.models import StabilizationSupersedeIn

        assert "superseded_by" not in StabilizationSupersedeIn.model_fields

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-SupApi")
        with get_conn() as conn:
            _, first, second = self._two_packages(conn, system_id)
        r = admin_client.post(
            f"/stabilization/packages/{first['id']}/supersede",
            json={"successor_package_id": second["id"], "note": "newer run"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Probe-System-Id": str(system_id),
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "superseded"
        assert body["superseded_by_id"] == second["id"]
        assert body["approved_by"] == "root"
        assert body["gate"]["reason_code"] == "package_superseded"


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

    def test_parent_review_columns_are_added_to_an_existing_database(
        self, tmp_path, monkeypatch
    ):
        """A package created before the parent review existed keeps NULL,
        which says "no parent has reviewed this" -- and the gate refuses it
        rather than reading the existing `approved_by` as if it had also been
        the parent's endorsement. Compatibility means the legacy row stays
        readable, never that it is promoted to a satisfied premise (#337)."""
        import sqlite3 as _sqlite3

        db_path = tmp_path / "pre-parent-review.db"
        conn = _sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE stabilization_package (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                system_id                 INTEGER NOT NULL,
                node_id                   INTEGER NOT NULL,
                node_version_id           INTEGER NOT NULL,
                candidate_implementation_id INTEGER NOT NULL,
                baseline_implementation_id INTEGER,
                exploration_run_id        INTEGER,
                applicability_envelope_json TEXT NOT NULL DEFAULT '{}',
                known_limitations_json    TEXT NOT NULL DEFAULT '[]',
                residual_risks_json       TEXT NOT NULL DEFAULT '[]',
                required_case_count       INTEGER NOT NULL DEFAULT 0,
                stability_window_seconds  REAL NOT NULL DEFAULT 0,
                observed_case_count       INTEGER,
                observed_window_seconds   REAL,
                outcome_unmeasured_reason TEXT NOT NULL DEFAULT '',
                rollback_implementation_id INTEGER,
                rollback_plan             TEXT NOT NULL DEFAULT '',
                status                    TEXT NOT NULL DEFAULT 'draft',
                approved_by               TEXT,
                approved_at               REAL,
                decision_note             TEXT NOT NULL DEFAULT '',
                decision_method           TEXT NOT NULL DEFAULT 'manual',
                superseded_by_id          INTEGER,
                created_by                TEXT,
                created_at                REAL NOT NULL
            )
            """
        )
        conn.execute(
            """INSERT INTO stabilization_package
                   (id, system_id, node_id, node_version_id,
                    candidate_implementation_id, created_by, created_at)
               VALUES (1, 1, 1, 1, 1, 'alice', ?)""",
            (time.time(),),
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("PROBE_DB_PATH", str(db_path))
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
        from app.main import app

        with TestClient(app):
            check = _sqlite3.connect(db_path)
            check.row_factory = _sqlite3.Row
            cols = {
                r["name"]
                for r in check.execute("PRAGMA table_info(stabilization_package)")
            }
            assert {
                "parent_reviewed_by",
                "parent_reviewed_at",
                "parent_review_disposition",
                "parent_review_note",
            } <= cols
            row = check.execute(
                "SELECT * FROM stabilization_package WHERE id = 1"
            ).fetchone()
            check.close()
        assert row["created_by"] == "alice"
        assert row["parent_reviewed_by"] is None
        assert row["parent_reviewed_at"] is None
        assert row["parent_review_disposition"] is None
        assert row["parent_review_note"] is None


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

    def _second_admin(self, admin_client, token, username="carol"):
        """A second identified human, so the parent review and the approval
        can be two people. An admin because `get_system_id` only lets an
        admin or the System's owner reach a System they did not create."""
        r = admin_client.post(
            "/users",
            json={"username": username, "password": "s3cret2", "role": "admin"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        r = admin_client.post(
            "/auth/login", json={"username": username, "password": "s3cret2"}
        )
        assert r.status_code == 200, r.text
        return r.cookies.get("probe_session")

    def _package_ready_for_review(self, admin_client, token, system_id, name):
        """Create a package over HTTP whose evidence cites a real run and
        whose sufficiency is declared. Everything except the two decisions."""
        from app.db import get_conn

        with get_conn() as conn:
            node, version, implementation = _validating_node(conn, system_id)
            run_id = _exploration_run(conn, system_id, node, version)
        r = admin_client.post(
            "/stabilization/packages",
            json={
                "node_id": node["id"],
                "candidate_implementation_id": implementation["id"],
                "applicability_envelope": {"inputs": "JP addresses"},
                "required_case_count": 20,
                "stability_window_seconds": 3600.0,
                "observed_case_count": 25,
                "observed_window_seconds": 7200.0,
            },
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        package_id = r.json()["id"]
        for kind, ev_name, verdict in (
            ("criterion", "accuracy", "met"),
            ("floor", "safety", "held"),
        ):
            r = admin_client.post(
                f"/stabilization/packages/{package_id}/evidence",
                json={
                    "evidence_level": "node", "evidence_kind": kind,
                    "name": ev_name, "verdict": verdict,
                    "ref_kind": "exploration_run", "ref_id": run_id,
                },
                headers=self._headers(token, system_id),
            )
            assert r.status_code == 201, r.text
        return node, package_id, run_id

    def test_approval_over_http_records_the_authenticated_person(self, admin_client):
        """`approved_by` is never a request field: accepting it would let any
        caller record someone else's approval (#337). The parent review is the
        same, and it is a SECOND record with its own who/when (#304)."""
        from app.models import (
            StabilizationDecisionIn,
            StabilizationPackageCreateIn,
            StabilizationParentReviewIn,
        )

        assert "approved_by" not in StabilizationPackageCreateIn.model_fields
        assert "approved_by" not in StabilizationDecisionIn.model_fields
        assert "parent_reviewed_by" not in StabilizationParentReviewIn.model_fields
        assert "reviewed_by" not in StabilizationParentReviewIn.model_fields

        token = _login(admin_client)
        carol = self._second_admin(admin_client, token)
        system_id = _create_system(admin_client, token, "ST-ApiApprove")
        _, package_id, _ = self._package_ready_for_review(
            admin_client, token, system_id, "ST-ApiApprove"
        )

        # Without the parent review the gate refuses with its own code.
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/approve",
            json={"note": "ok"},
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 409, r.text
        assert "parent_review_missing" in r.json()["detail"]

        r = admin_client.post(
            f"/stabilization/packages/{package_id}/parent-review",
            json={"disposition": "endorsed", "note": "matches the Flow's goal"},
            headers=self._headers(carol, system_id),
        )
        assert r.status_code == 200, r.text
        reviewed = r.json()
        assert reviewed["parent_reviewed_by"] == "carol"
        assert reviewed["parent_review_disposition"] == "endorsed"
        assert reviewed["parent_review_note"] == "matches the Flow's goal"
        assert reviewed["parent_reviewed_at"] is not None
        # A parent review is not an approval: the package is still undecided.
        assert reviewed["status"] == "draft"
        assert reviewed["approved_by"] is None
        assert reviewed["gate"]["allowed"] is True

        r = admin_client.post(
            f"/stabilization/packages/{package_id}/approve",
            json={"note": "ok"},
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "approved"
        assert body["approved_by"] == "root"
        # Both decisions stay visible, with their own who/when.
        assert body["parent_reviewed_by"] == "carol"
        assert body["parent_review_disposition"] == "endorsed"

    def test_one_person_cannot_be_both_parent_and_approver(self, admin_client):
        """#304's separation is the point. One person filling both roles
        reproduces exactly the conflation the two records exist to prevent."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-ApiSamePerson")
        _, package_id, _ = self._package_ready_for_review(
            admin_client, token, system_id, "ST-ApiSamePerson"
        )
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/parent-review",
            json={"disposition": "endorsed"},
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        assert r.json()["gate"]["allowed"] is True

        r = admin_client.post(
            f"/stabilization/packages/{package_id}/approve",
            json={},
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 409, r.text
        assert "parent_approver_identical" in r.json()["detail"]

    def test_a_declined_parent_review_refuses_the_gate(self, admin_client):
        token = _login(admin_client)
        carol = self._second_admin(admin_client, token)
        system_id = _create_system(admin_client, token, "ST-ApiDeclined")
        _, package_id, _ = self._package_ready_for_review(
            admin_client, token, system_id, "ST-ApiDeclined"
        )
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/parent-review",
            json={"disposition": "declined", "note": "envelope too wide"},
            headers=self._headers(carol, system_id),
        )
        assert r.status_code == 200, r.text
        assert r.json()["gate"]["reason_code"] == "parent_review_declined"

        r = admin_client.post(
            f"/stabilization/packages/{package_id}/approve",
            json={},
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 409, r.text
        assert "parent_review_declined" in r.json()["detail"]

    def test_a_recorded_parent_review_is_not_overwritten(self, admin_client):
        """Append-only, like every other decision here: a parent who changes
        their mind supersedes the package, so both judgements stay readable."""
        token = _login(admin_client)
        carol = self._second_admin(admin_client, token)
        system_id = _create_system(admin_client, token, "ST-ApiReviewOnce")
        _, package_id, _ = self._package_ready_for_review(
            admin_client, token, system_id, "ST-ApiReviewOnce"
        )
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/parent-review",
            json={"disposition": "declined"},
            headers=self._headers(carol, system_id),
        )
        assert r.status_code == 200, r.text
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/parent-review",
            json={"disposition": "endorsed"},
            headers=self._headers(carol, system_id),
        )
        assert r.status_code == 409, r.text
        r = admin_client.get(
            f"/stabilization/packages/{package_id}",
            headers=self._headers(token, system_id),
        )
        assert r.json()["parent_review_disposition"] == "declined"

    def test_a_parent_review_is_refused_on_a_decided_package(self, admin_client):
        """A review attached after the decision would describe a judgement
        that was made without it."""
        token = _login(admin_client)
        carol = self._second_admin(admin_client, token)
        system_id = _create_system(admin_client, token, "ST-ApiReviewLate")
        _, package_id, _ = self._package_ready_for_review(
            admin_client, token, system_id, "ST-ApiReviewLate"
        )
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/parent-review",
            json={"disposition": "endorsed"},
            headers=self._headers(carol, system_id),
        )
        assert r.status_code == 200, r.text
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/approve",
            json={},
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 200, r.text

        r = admin_client.post(
            f"/stabilization/packages/{package_id}/parent-review",
            json={"disposition": "declined"},
            headers=self._headers(carol, system_id),
        )
        assert r.status_code == 409, r.text

    def test_a_self_reported_result_is_refused_at_write_time(self, admin_client):
        """The developer learns the moment they try to write a claim rather
        than a result -- not only when the gate runs days later."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ST-ApiSelfReport")
        with get_conn() as conn:
            node, _, implementation = _validating_node(conn, system_id)
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
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/evidence",
            json={
                "evidence_level": "node", "evidence_kind": "criterion",
                "name": "accuracy", "verdict": "met",
            },
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 422, r.text

        # The same row recording a REGRESSION is accepted: a negative
        # self-report can only ever block.
        r = admin_client.post(
            f"/stabilization/packages/{package_id}/evidence",
            json={
                "evidence_level": "node", "evidence_kind": "criterion",
                "name": "accuracy", "verdict": "not_met",
            },
            headers=self._headers(token, system_id),
        )
        assert r.status_code == 201, r.text

    def test_the_parent_review_vocabulary_matches_the_domain_layer(self):
        """A bare `str` in the API model puts no enum in the OpenAPI schema
        and lets a Dashboard union drift from the server unnoticed."""
        from typing import get_args

        from app.models import StabilizationParentReviewDisposition

        assert set(get_args(StabilizationParentReviewDisposition)) == set(
            PARENT_REVIEW_DISPOSITIONS
        )

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
