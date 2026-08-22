"""Tests for Epic #412 Issue #415: Flow experiment orchestration.

Canonical contract: `docs/execution-modes.md` §7, test requirements §9.3.
What must hold:

1. Every §7.1 completeness and structural refusal is reachable on its own and
   carries its own finite code -- "invalid proposal" would carry twelve facts
   at once (#366).
2. `comparison_scope` and the DISTINCT target-Node count must agree (§7.2),
   and a Node whose `side_effect_class` is `external_write` / `irreversible`
   cannot be experimented on with `none` / `pure` isolation (§7.3,
   Principle 4 made structural at the entrance).
3. Approval and execution mode are two INDEPENDENT facts and an execution
   record needs both (§7.5): unapproved is a 409, and approved-but-back-to-
   `propose` is also a 409 -- with a different body, because the developer's
   next action differs.
4. `status` is DERIVED by folding the event ledger and is not a column
   (§7.4). The same rows always fold to the same value.
5. The whole audit trail -- propose, approve, execute, result, promotion
   candidate, rollback -- is readable afterwards, and every human decision is
   `decision_method: manual` with the authenticated principal as its actor.
6. Nothing here changes production (§7.6): creating, approving and executing
   leave `evolution_node`, `components`, `cell_improvements`, `probe_patches`,
   `publish_jobs` and every other table byte-identical.
7. Walking `fixed -> observe -> propose -> shadow` never changes the
   baseline's production output.
8. A reasoning failure produces NO proposal and no heuristic fallback
   (Principle 6); the failed `intelligence_runs` row is all it leaves.
9. System isolation: a proposal of another System is indistinguishable from
   one that does not exist.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from app import execution_mode, flow_orchestration
from app.db import get_conn
from app.evolution_node import add_implementation, add_link, add_version, create_node
from app.execution_mode import assign_mode
from app.flow_orchestration import (
    COMPLETENESS_REJECTION_CODES,
    STRUCTURAL_REJECTION_CODES,
    GateDecision,
    ProposalContent,
    ProposalFacts,
    TargetFact,
    derive_proposal_status,
    evaluate_completeness,
    evaluate_lifecycle,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "shared" / "schemas" / "flow_experiment_proposal.schema.json"

FLOW_ID = "checkout-flow"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "flow-orchestration-test.db"))
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


def _add_node(conn, system_id, node_key, *, side_effect_class="pure", flow_id=FLOW_ID):
    node = create_node(conn, system_id=system_id, node_key=node_key)
    version = add_version(
        conn,
        system_id=system_id,
        node_id=node["id"],
        mission=f"{node_key} の責務",
        input_contract={"kind": "object"},
        output_contract={"kind": "object"},
        side_effect_class=side_effect_class,
        trust_boundary="internal",
        created_by="root",
    )
    add_implementation(
        conn,
        system_id=system_id,
        node_id=node["id"],
        node_version_id=version["id"],
        modality="deterministic_code",
        created_by="root",
    )
    if flow_id is not None:
        add_link(
            conn,
            system_id=system_id,
            node_id=node["id"],
            link_kind="flow",
            target_ref=flow_id,
            created_by="root",
        )
    return node["id"]


def _seed(system_id, *, mode="shadow"):
    """A representative runtime Flow: two pure Nodes in it, one outside it,
    one `external_write` Node in it, plus an observed span so the Flow subject
    resolves from persisted rows alone."""
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO components (system_id, component_id, mode, updated_at) "
            "VALUES (?, 'summarize', 'trace', ?)",
            (system_id, now),
        )
        conn.execute(
            "INSERT INTO traces (system_id, trace_id, component_id, mode, timestamp) "
            "VALUES (?, 't-1', 'summarize', 'trace', ?)",
            (system_id, now),
        )
        conn.execute(
            """INSERT INTO trace_spans
                   (system_id, trace_id, component_id, span_id, parent_span_id,
                    flow_id, correlation_id, timestamp)
               VALUES (?, 't-1', 'summarize', 's-1', NULL, ?, NULL, ?)""",
            (system_id, FLOW_ID, now),
        )
        _add_node(conn, system_id, "node-a")
        _add_node(conn, system_id, "node-b")
        _add_node(conn, system_id, "node-outside", flow_id=None)
        _add_node(conn, system_id, "node-writer", side_effect_class="external_write")
        if mode is not None:
            assign_mode(
                conn,
                system_id=system_id,
                scope_kind="system",
                scope_ref="",
                mode=mode,
                reason="test fixture",
                actor="root",
            )
        cur = conn.execute(
            "INSERT INTO shadow_results (system_id, trace_id, component_id, timestamp) "
            "VALUES (?, 't-1', 'summarize', ?)",
            (system_id, now),
        )
        shadow_result_id = cur.lastrowid
    return {"shadow_result_id": shadow_result_id}


def _set_mode(system_id, mode):
    with get_conn() as conn:
        assign_mode(
            conn,
            system_id=system_id,
            scope_kind="system",
            scope_ref="",
            mode=mode,
            reason=f"switch to {mode}",
            actor="root",
        )


def _body(**overrides):
    """A complete, gate-passing proposal body."""
    body = {
        "proposal_key": "exp-1",
        "flow_subject_kind": "runtime_flow",
        "flow_subject_ref": FLOW_ID,
        "comparison_scope": "single_node",
        "title": "要約 Node の候補比較",
        "purpose": "要約 Node のコストを下げられるか確かめる",
        "hypothesis": "rule 実装でも品質を保てる",
        "baseline_ref": "baseline:node-a@stable",
        "candidate_refs": ["candidate:rule-1"],
        "targets": [{"node_key": "node-a", "target_role": "candidate_target"}],
        "evaluation_axes": [{"level": "node", "name": "output_match", "metric": "match_rate"}],
        "quality_floor": {"output_match": "baseline を下回らない"},
        "isolation_strategy": "isolated_workspace",
        "isolation_detail": "network-off worktree",
        "cost_cap": {"max_runs": 20},
        "stop_conditions": ["quality floor を割ったら停止"],
        "rollback_plan": "候補を破棄し pinned 実装を維持する",
        "evidence_refs": ["trace:t-1"],
    }
    body.update(overrides)
    return body


def _content(**overrides) -> ProposalContent:
    values = dict(
        proposal_key="exp-1",
        flow_subject_kind="runtime_flow",
        flow_subject_ref=FLOW_ID,
        comparison_scope="single_node",
        title="t",
        purpose="目的",
        hypothesis="仮説",
        baseline_ref="baseline:1",
        candidate_refs=("candidate:1",),
        evaluation_axes=({"level": "node", "name": "match"},),
        quality_floor={"match": "no regression"},
        isolation_strategy="isolated_workspace",
        isolation_detail="sandbox",
        cost_cap={"max_runs": 5},
        stop_conditions=("floor broken",),
        rollback_plan="discard",
        evidence_refs=("trace:t-1",),
    )
    values.update(overrides)
    return ProposalContent(**values)


def _target(node_key="node-a", **overrides) -> TargetFact:
    values = dict(
        node_key=node_key,
        role="candidate_target",
        position=0,
        resolved=True,
        side_effect_class="pure",
        in_flow=True,
    )
    values.update(overrides)
    return TargetFact(**values)


def _facts(content=None, targets=None, *, flow_subject_known=True, proposal_key_taken=False):
    return ProposalFacts(
        content=content or _content(),
        targets=tuple(targets if targets is not None else (_target(),)),
        flow_subject_known=flow_subject_known,
        proposal_key_taken=proposal_key_taken,
    )


def _table_contents(names=None):
    """Full row contents (not just counts) of every user table.

    Counts alone would pass an UPDATE that rewrote a Node's maturity in
    place, which is exactly the class of change §7.6 forbids.
    """
    with get_conn() as conn:
        if names is None:
            names = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
        return {
            name: [
                tuple(row)
                for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
            ]
            for name in names
        }


#: The tables §7.6 names, plus the ones an unnoticed adoption path would have
#: to touch. Snapshotted by CONTENT before and after.
PRODUCTION_TABLES = [
    "evolution_node",
    "evolution_node_version",
    "evolution_node_implementation",
    "evolution_node_event",
    "components",
    "cell_improvements",
    "probe_patches",
    "publish_jobs",
    "experiments",
    "replay_variants",
    "shadow_results",
    "probe_points",
]

#: The only four tables this module may write (plus `intelligence_runs` on the
#: drafting path, which is its own reasoning audit).
ORCHESTRATION_TABLES = {
    "flow_experiment_proposal",
    "flow_experiment_target",
    "flow_experiment_event",
    "flow_experiment_execution_ref",
}


# ---------------------------------------------------------------------------
# §7.1 completeness gate -- every code individually
# ---------------------------------------------------------------------------


class TestCompletenessGate:
    """One test per finite code. A single "incomplete" verdict could not tell
    the developer which of twelve elements to write (#366)."""

    def test_complete_proposal_passes(self):
        verdict = evaluate_completeness(_facts())
        assert verdict == GateDecision(True, "ok", verdict.message)

    @pytest.mark.parametrize(
        "code,overrides",
        [
            ("purpose_missing", {"purpose": "   "}),
            ("hypothesis_missing", {"hypothesis": ""}),
            ("baseline_missing", {"baseline_ref": ""}),
            ("candidates_missing", {"candidate_refs": ("", "  ")}),
            ("evaluation_axes_missing", {"evaluation_axes": ()}),
            ("quality_floor_missing", {"quality_floor": {}}),
            ("isolation_strategy_missing", {"isolation_strategy": ""}),
            ("cost_cap_missing", {"cost_cap": {}}),
            ("stop_conditions_missing", {"stop_conditions": ()}),
            ("rollback_plan_missing", {"rollback_plan": " "}),
            ("evidence_missing", {"evidence_refs": ()}),
        ],
    )
    def test_each_missing_element_has_its_own_code(self, code, overrides):
        verdict = evaluate_completeness(_facts(_content(**overrides)))
        assert verdict.ok is False
        assert verdict.code == code

    def test_scope_missing_when_no_target_node(self):
        verdict = evaluate_completeness(_facts(targets=()))
        assert (verdict.ok, verdict.code) == (False, "scope_missing")

    def test_unknown_isolation_value_is_reported_as_missing(self):
        verdict = evaluate_completeness(
            _facts(_content(isolation_strategy="hope_for_the_best"))
        )
        assert verdict.code == "isolation_strategy_missing"

    def test_cost_cap_of_prose_alone_is_not_a_cap(self):
        verdict = evaluate_completeness(_facts(_content(cost_cap={"note": "keep it cheap"})))
        assert verdict.code == "cost_cap_missing"

    def test_cost_cap_must_be_positive(self):
        verdict = evaluate_completeness(_facts(_content(cost_cap={"max_runs": 0})))
        assert verdict.code == "cost_cap_missing"

    def test_every_completeness_code_is_covered(self):
        """The parametrisation above plus `scope_missing` must cover the whole
        tuple: a code nobody can reach is a promise the contract cannot keep."""
        covered = {
            "purpose_missing", "hypothesis_missing", "scope_missing",
            "baseline_missing", "candidates_missing", "evaluation_axes_missing",
            "quality_floor_missing", "isolation_strategy_missing",
            "cost_cap_missing", "stop_conditions_missing", "rollback_plan_missing",
            "evidence_missing",
        }
        assert covered == set(COMPLETENESS_REJECTION_CODES)


class TestStructuralGate:
    """The seven structural refusals of §7.1."""

    def test_unknown_flow_subject(self):
        verdict = evaluate_completeness(_facts(flow_subject_known=False))
        assert verdict.code == "unknown_flow_subject"
        assert verdict.detail == (f"runtime_flow:{FLOW_ID}",)

    def test_unresolved_node(self):
        verdict = evaluate_completeness(
            _facts(targets=(_target("node-ghost", resolved=False),))
        )
        assert verdict.code == "unresolved_node"
        assert verdict.detail == ("node-ghost",)

    def test_single_node_scope_with_two_nodes(self):
        verdict = evaluate_completeness(
            _facts(targets=(_target("node-a"), _target("node-b")))
        )
        assert verdict.code == "comparison_scope_mismatch"
        assert verdict.detail == ("node-a", "node-b")

    def test_sub_pipeline_scope_with_one_node(self):
        verdict = evaluate_completeness(
            _facts(
                _content(
                    comparison_scope="sub_pipeline",
                    evaluation_axes=(
                        {"level": "node", "name": "match"},
                        {"level": "flow_capability", "name": "flow_success"},
                    ),
                )
            )
        )
        assert verdict.code == "comparison_scope_mismatch"

    def test_unknown_comparison_scope_value(self):
        verdict = evaluate_completeness(_facts(_content(comparison_scope="whole_system")))
        assert verdict.code == "comparison_scope_mismatch"

    def test_same_node_as_baseline_and_candidate_is_still_single_node(self):
        """The scope counts DISTINCT Nodes: one Node appearing in both roles
        is one Node, not a sub-pipeline."""
        verdict = evaluate_completeness(
            _facts(
                targets=(
                    _target("node-a", role="baseline"),
                    _target("node-a", role="candidate_target", position=1),
                )
            )
        )
        assert verdict.ok is True

    def test_node_not_in_flow(self):
        verdict = evaluate_completeness(_facts(targets=(_target("node-outside", in_flow=False),)))
        assert verdict.code == "node_not_in_flow"
        assert verdict.detail == ("node-outside",)

    def test_undeterminable_membership_is_not_a_refusal(self):
        """`in_flow is None` means "not determinable from persisted rows"
        (a static Flow needs the call graph). Refusing there would reject a
        legitimate proposal for a fact nobody read (EM-ADR-1)."""
        verdict = evaluate_completeness(_facts(targets=(_target(in_flow=None),)))
        assert verdict.ok is True

    @pytest.mark.parametrize("side_effect", ["external_write", "irreversible"])
    @pytest.mark.parametrize("strategy", ["none", "pure"])
    def test_side_effect_node_cannot_use_non_isolating_strategy(self, side_effect, strategy):
        verdict = evaluate_completeness(
            _facts(
                _content(isolation_strategy=strategy),
                targets=(_target("node-writer", side_effect_class=side_effect),),
            )
        )
        assert verdict.code == "isolation_required_for_side_effects"
        assert verdict.detail == (f"node-writer:{side_effect}",)

    def test_unreadable_side_effect_class_also_requires_isolation(self):
        """An unread side-effect class is not evidence of purity (#380)."""
        verdict = evaluate_completeness(
            _facts(
                _content(isolation_strategy="none"),
                targets=(_target(side_effect_class=None),),
            )
        )
        assert verdict.code == "isolation_required_for_side_effects"
        assert verdict.detail == ("node-a:unknown",)

    def test_side_effect_node_may_use_an_isolating_strategy(self):
        verdict = evaluate_completeness(
            _facts(
                _content(isolation_strategy="mock"),
                targets=(_target("node-writer", side_effect_class="external_write"),),
            )
        )
        assert verdict.ok is True

    def test_sub_pipeline_requires_a_flow_capability_contract(self):
        verdict = evaluate_completeness(
            _facts(
                _content(comparison_scope="sub_pipeline"),
                targets=(_target("node-a"), _target("node-b", position=1)),
            )
        )
        assert verdict.code == "evaluation_contract_missing"
        assert verdict.detail == ("flow_capability",)

    def test_node_level_contract_is_always_required(self):
        verdict = evaluate_completeness(
            _facts(_content(evaluation_axes=({"level": "ux_outcome", "name": "csat"},)))
        )
        assert verdict.code == "evaluation_contract_missing"
        assert verdict.detail == ("node",)

    def test_duplicate_proposal_key(self):
        verdict = evaluate_completeness(_facts(proposal_key_taken=True))
        assert verdict.code == "duplicate_proposal_key"
        assert verdict.detail == ("exp-1",)

    def test_every_structural_code_is_covered(self):
        covered = {
            "unknown_flow_subject", "unresolved_node", "comparison_scope_mismatch",
            "node_not_in_flow", "isolation_required_for_side_effects",
            "evaluation_contract_missing", "duplicate_proposal_key",
        }
        assert covered == set(STRUCTURAL_REJECTION_CODES)

    def test_missing_element_is_reported_before_its_shape(self):
        """Order is contract: a developer who has not written the isolation
        strategy is not helped by hearing the Node's side-effect class
        disagrees with it."""
        verdict = evaluate_completeness(
            _facts(
                _content(isolation_strategy="", purpose=""),
                targets=(_target("node-writer", side_effect_class="external_write"),),
            )
        )
        assert verdict.code == "purpose_missing"


# ---------------------------------------------------------------------------
# §7.4 the derived lifecycle
# ---------------------------------------------------------------------------


def _events(*kinds):
    return [{"event_kind": kind} for kind in kinds]


class TestDerivedStatus:
    def test_no_status_column_exists(self):
        """§7.4 forbids one, and the DDL is the second line of that defence."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from app.db import SCHEMA

        conn.executescript(SCHEMA)
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(flow_experiment_proposal)").fetchall()
        }
        assert "status" not in columns
        assert {"proposal_key", "isolation_strategy", "rollback_plan"} <= columns
        conn.close()

    @pytest.mark.parametrize(
        "kinds,expected",
        [
            (("proposed",), "proposed"),
            (("proposed", "approved"), "approved"),
            (("proposed", "rejected"), "rejected"),
            (("proposed", "withdrawn"), "withdrawn"),
            (("proposed", "expired"), "expired"),
            (("proposed", "approved", "execution_recorded"), "executing"),
            (("proposed", "approved", "execution_recorded", "result_recorded"), "completed"),
            (
                (
                    "proposed", "approved", "execution_recorded", "result_recorded",
                    "promotion_candidate_recorded", "rollback_recorded",
                ),
                "completed",
            ),
        ],
    )
    def test_fold(self, kinds, expected):
        assert derive_proposal_status(_events(*kinds)) == expected

    def test_a_terminal_verdict_stops_the_fold(self):
        """A rejected proposal that somehow received an execution row must not
        read as `executing`; the row stays as an audit fact."""
        assert (
            derive_proposal_status(_events("proposed", "rejected", "execution_recorded"))
            == "rejected"
        )

    def test_the_same_rows_always_fold_to_the_same_status(self):
        events = _events("proposed", "approved", "execution_recorded")
        first = derive_proposal_status(events, now=1.0, expires_at=None)
        second = derive_proposal_status(events, now=9_999_999.0, expires_at=None)
        assert first == second == "executing"

    def test_time_expires_only_a_proposal_still_awaiting_a_decision(self):
        assert (
            derive_proposal_status(_events("proposed"), now=100.0, expires_at=50.0) == "expired"
        )
        # An approval already given is never unmade by the clock.
        assert (
            derive_proposal_status(_events("proposed", "approved"), now=100.0, expires_at=50.0)
            == "approved"
        )


class TestLifecycleRules:
    @pytest.mark.parametrize("action", ["approve", "reject"])
    def test_decision_only_from_proposed(self, action):
        assert evaluate_lifecycle(action, status="proposed", has_execution=False, has_result=False).allowed
        refused = evaluate_lifecycle(action, status="approved", has_execution=False, has_result=False)
        assert (refused.allowed, refused.code) == (False, "not_awaiting_decision")

    def test_expired_proposal_cannot_be_approved(self):
        refused = evaluate_lifecycle(
            "approve", status="expired", has_execution=False, has_result=False
        )
        assert (refused.allowed, refused.code) == (False, "proposal_expired")

    def test_execution_requires_approval(self):
        refused = evaluate_lifecycle(
            "record_execution", status="proposed", has_execution=False, has_result=False
        )
        assert (refused.allowed, refused.code) == (False, "not_approved")
        assert evaluate_lifecycle(
            "record_execution", status="approved", has_execution=False, has_result=False
        ).allowed

    def test_result_requires_an_execution(self):
        refused = evaluate_lifecycle(
            "record_result", status="approved", has_execution=False, has_result=False
        )
        assert (refused.allowed, refused.code) == (False, "no_execution_recorded")

    def test_promotion_candidate_requires_a_result(self):
        refused = evaluate_lifecycle(
            "record_promotion_candidate", status="executing", has_execution=True, has_result=False
        )
        assert (refused.allowed, refused.code) == (False, "no_result_recorded")
        assert evaluate_lifecycle(
            "record_promotion_candidate", status="completed", has_execution=True, has_result=True
        ).allowed

    def test_withdrawal_is_refused_once_an_execution_exists(self):
        refused = evaluate_lifecycle(
            "withdraw", status="executing", has_execution=True, has_result=False
        )
        assert (refused.allowed, refused.code) == (False, "not_awaiting_decision")

    def test_unknown_action_is_a_validation_error(self):
        with pytest.raises(flow_orchestration.FlowExperimentValidationError):
            evaluate_lifecycle("adopt", status="approved", has_execution=True, has_result=True)


# ---------------------------------------------------------------------------
# HTTP: creation, the human gate, and the two 409 directions of §7.5
# ---------------------------------------------------------------------------


class TestProposalApi:
    def test_create_approve_and_read_back(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        headers = _headers(token, system_id)

        r = admin_client.post("/flow-experiments", json=_body(), headers=headers)
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["status"] == "proposed"
        assert doc["decision_method"] == "manual"
        # The `proposed` event's actor is the authenticated principal, never a
        # body field (#337).
        assert [e["event_kind"] for e in doc["events"]] == ["proposed"]
        assert doc["events"][0]["actor"] == "root"
        assert doc["events"][0]["decision_method"] == "manual"

        proposal_id = doc["id"]
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/approve",
            json={"reason": "リスクは隔離済み"},
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"

        r = admin_client.get(f"/flow-experiments/{proposal_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

    def test_body_cannot_supply_the_actor(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/flow-experiments", json=_body(actor="mallory"), headers=headers
        )
        assert r.status_code == 422, r.text

    def test_incomplete_proposal_is_a_422_with_its_finite_code(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        headers = _headers(token, system_id)
        r = admin_client.post("/flow-experiments", json=_body(purpose=""), headers=headers)
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "purpose_missing"

    def test_duplicate_key_is_refused(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        headers = _headers(token, system_id)
        assert admin_client.post("/flow-experiments", json=_body(), headers=headers).status_code == 201
        r = admin_client.post("/flow-experiments", json=_body(), headers=headers)
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "duplicate_proposal_key"

    def test_side_effect_node_refused_at_the_api(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/flow-experiments",
            json=_body(
                targets=[{"node_key": "node-writer", "target_role": "candidate_target"}],
                isolation_strategy="none",
            ),
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "isolation_required_for_side_effects"

    def test_node_outside_the_flow_refused_at_the_api(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/flow-experiments",
            json=_body(targets=[{"node_key": "node-outside"}]),
            headers=headers,
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "node_not_in_flow"

    def test_rejection_requires_a_reason(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/reject", json={"reason": ""}, headers=headers
        )
        assert r.status_code == 422
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/reject",
            json={"reason": "コストに見合わない"},
            headers=headers,
        )
        assert r.json()["status"] == "rejected"

    def test_status_filter_uses_the_derived_value(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        headers = _headers(token, system_id)
        first = admin_client.post("/flow-experiments", json=_body(), headers=headers).json()["id"]
        admin_client.post(
            "/flow-experiments", json=_body(proposal_key="exp-2"), headers=headers
        )
        admin_client.post(
            f"/flow-experiments/{first}/approve", json={"reason": "ok"}, headers=headers
        )
        r = admin_client.get("/flow-experiments?status=approved", headers=headers)
        assert [p["id"] for p in r.json()["proposals"]] == [first]

    def test_document_validates_against_the_shared_schema(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id)
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )
        admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        doc = admin_client.get(f"/flow-experiments/{proposal_id}", headers=headers).json()
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(doc, schema)


class TestApprovalAndModeAreIndependent:
    """§7.5's two 409 directions. Neither fact implies the other."""

    def test_approved_but_mode_back_to_propose_is_refused(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )

        _set_mode(system_id, "propose")
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        assert r.status_code == 409, r.text
        # #413's own denial body, so a mode refusal has ONE shape everywhere.
        assert r.json()["detail"]["denial_code"] == "capability_not_permitted"
        assert r.json()["detail"]["mode"] == "propose"

    def test_shadow_mode_but_unapproved_is_refused(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        assert r.status_code == 409, r.text
        # A DIFFERENT body from the mode denial: the next action differs.
        assert r.json()["detail"]["code"] == "not_approved"
        assert "denial_code" not in r.json()["detail"]

    def test_one_node_left_behind_refuses_the_whole_sub_pipeline(self, admin_client):
        """Fail-closed over the whole target set: executing the others would
        still execute a candidate inside a Flow whose permission is
        incomplete."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        body = _body(
            comparison_scope="sub_pipeline",
            targets=[{"node_key": "node-a"}, {"node_key": "node-b"}],
            evaluation_axes=[
                {"level": "node", "name": "match"},
                {"level": "flow_capability", "name": "flow_success"},
            ],
        )
        proposal_id = admin_client.post("/flow-experiments", json=body, headers=headers).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )
        with get_conn() as conn:
            assign_mode(
                conn,
                system_id=system_id,
                scope_kind="node",
                scope_ref="node-b",
                mode="propose",
                reason="hold this one back",
                actor="root",
            )
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["denial_code"] == "capability_not_permitted"


# ---------------------------------------------------------------------------
# §7.6 execution references, the audit trail, and "changes nothing"
# ---------------------------------------------------------------------------


class TestExecutionAndAudit:
    def test_full_audit_trail(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)

        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve",
            json={"reason": "隔離戦略を確認した"},
            headers=headers,
        )
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
                "note": "shadow 比較 1 件",
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "executing"
        assert r.json()["executions"][0]["resolution"] == "resolved"

        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/results",
            json={
                "summary": "候補は baseline と一致",
                "metrics": {
                    "node": {"match_rate": 1.0},
                    "flow_capability": {"completed_flow_rate": 1.0},
                },
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "completed"

        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/promotion-candidates",
            json={"candidate_ref": "candidate:rule-1", "rationale": "指標を満たした"},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["promotion_candidates"][0]["payload"]["promotion_performed"] is False

        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/rollbacks",
            json={"detail": "候補を破棄し pinned 実装を維持した"},
            headers=headers,
        )
        assert r.status_code == 201, r.text

        doc = admin_client.get(f"/flow-experiments/{proposal_id}", headers=headers).json()
        assert [e["event_kind"] for e in doc["events"]] == [
            "proposed",
            "approved",
            "execution_recorded",
            "result_recorded",
            "promotion_candidate_recorded",
            "rollback_recorded",
        ]
        # Every human decision is `manual`, by the authenticated principal.
        assert {e["decision_method"] for e in doc["events"]} == {"manual"}
        assert {e["actor"] for e in doc["events"]} == {"root"}
        # A promotion CANDIDATE never advanced the lifecycle past `completed`.
        assert doc["status"] == "completed"

    def test_metrics_keys_are_the_three_evaluation_contracts(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )
        admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/results",
            json={"summary": "s", "metrics": {"overall_score": 0.9}},
            headers=headers,
        )
        assert r.status_code == 422

    def test_promotion_candidate_before_a_result_is_refused(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )
        admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/promotion-candidates",
            json={"candidate_ref": "c", "rationale": "r"},
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "no_result_recorded"

    def test_an_unresolvable_execution_reference_is_refused(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={"execution_kind": "shadow_result", "execution_ref": "99999"},
            headers=headers,
        )
        assert r.status_code == 422

    def test_a_failed_run_reference_reads_stale_not_resolved(self, admin_client):
        """Three answers, never merged: present-and-usable, absent, and
        present-but-its-own-run-produced-nothing."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        now = time.time()
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO repository_snapshots
                       (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/repo', 'c0ffee', 'ready', ?, ?)""",
                (system_id, now, now),
            )
            snapshot_id = cur.lastrowid
            cur = conn.execute(
                """INSERT INTO experiments
                       (system_id, feature_id, objective, snapshot_id, baseline_commit,
                        config_revision, execution_config, status, created_at)
                   VALUES (?, 'f-1', 'o', ?, 'c0ffee', 'r1', '{}', 'failed', ?)""",
                (system_id, snapshot_id, now),
            )
            experiment_id = cur.lastrowid
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={"execution_kind": "experiment", "execution_ref": str(experiment_id)},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["executions"][0]["resolution"] == "stale"


class TestChangesNothingInProduction:
    def test_create_approve_execute_changes_no_production_table(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)

        before_production = _table_contents(PRODUCTION_TABLES)
        before_all = _table_contents()

        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )
        admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        admin_client.post(
            f"/flow-experiments/{proposal_id}/results",
            json={"summary": "同一"},
            headers=headers,
        )
        admin_client.post(
            f"/flow-experiments/{proposal_id}/promotion-candidates",
            json={"candidate_ref": "candidate:rule-1", "rationale": "指標を満たした"},
            headers=headers,
        )

        assert _table_contents(PRODUCTION_TABLES) == before_production

        after_all = _table_contents()
        changed = {name for name in after_all if after_all[name] != before_all.get(name)}
        assert changed <= ORCHESTRATION_TABLES, f"unexpected writes to {changed - ORCHESTRATION_TABLES}"

    def test_reading_a_proposal_writes_nothing(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]

        before = _table_contents()
        admin_client.get(f"/flow-experiments/{proposal_id}", headers=headers)
        admin_client.get("/flow-experiments", headers=headers)
        assert _table_contents() == before


# ---------------------------------------------------------------------------
# §9.3's representative fixture: fixed -> observe -> propose -> shadow
# ---------------------------------------------------------------------------


def baseline_summarize(text: str) -> str:
    """The production implementation the whole walk is about.

    It is deliberately a plain function: nothing in this Epic may reach into
    the production path, so the assertion is simply that its output is the
    same value at every mode.
    """
    return text.strip().lower()


class TestModeWalk:
    def test_fixed_observe_propose_shadow(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        seeded = _seed(system_id, mode="fixed")
        headers = _headers(token, system_id)
        sample = "  Hello Probe  "
        expected = baseline_summarize(sample)

        draft_body = {
            "flow_subject_kind": "runtime_flow",
            "flow_subject_ref": FLOW_ID,
            "node_keys": ["node-a"],
            "goal": "コストを下げたい",
        }

        # --- fixed: no experiment reasoning at all --------------------------
        r = admin_client.post("/flow-experiments/draft", json=draft_body, headers=headers)
        assert r.status_code == 409
        assert r.json()["detail"]["mode"] == "fixed"
        assert baseline_summarize(sample) == expected

        # A proposal may still be WRITTEN in any mode -- writing a plan is not
        # running one.
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )

        # --- observe: still no proposal generation and no execution ---------
        _set_mode(system_id, "observe")
        assert (
            admin_client.post(
                "/flow-experiments/draft", json=draft_body, headers=headers
            ).status_code
            == 409
        )
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["denial_code"] == "capability_not_permitted"
        assert baseline_summarize(sample) == expected

        # --- propose: drafting is allowed, execution is NOT (§1.3) ----------
        _set_mode(system_id, "propose")
        r = admin_client.post("/flow-experiments/draft", json=draft_body, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["created_proposal"] is False
        assert r.json()["is_mock"] is True
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        assert r.status_code == 409
        assert baseline_summarize(sample) == expected

        # --- shadow: both facts hold, so the execution may be recorded ------
        _set_mode(system_id, "shadow")
        r = admin_client.post(
            f"/flow-experiments/{proposal_id}/executions",
            json={
                "execution_kind": "shadow_result",
                "execution_ref": str(seeded["shadow_result_id"]),
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "executing"

        # The production output never moved, and neither did the Node's
        # maturity or the Component's SDK policy mode.
        assert baseline_summarize(sample) == expected
        with get_conn() as conn:
            maturity = conn.execute(
                "SELECT maturity FROM evolution_node WHERE system_id = ? AND node_key = 'node-a'",
                (system_id,),
            ).fetchone()["maturity"]
            policy = conn.execute(
                "SELECT mode FROM components WHERE system_id = ? AND component_id = 'summarize'",
                (system_id,),
            ).fetchone()["mode"]
        assert maturity == "exploring"
        assert policy == "trace"

    @pytest.mark.parametrize("mode", ["fixed", "observe"])
    def test_no_credential_is_read_in_fixed_or_observe(self, admin_client, monkeypatch, mode):
        """EM-ADR-3: the gate runs BEFORE the first line that could read a
        credential, so the difference is not "the LLM is disabled by
        configuration" but "the credential path does not execute"."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode=mode)
        headers = _headers(token, system_id)

        from app import llm

        def _trap(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("credential path was reached")

        monkeypatch.setattr(llm.LLMConfig, "intelligence_from_env", staticmethod(_trap))
        monkeypatch.setattr(llm, "create_llm_client", _trap)

        r = admin_client.post(
            "/flow-experiments/draft",
            json={
                "flow_subject_kind": "runtime_flow",
                "flow_subject_ref": FLOW_ID,
                "node_keys": ["node-a"],
                "goal": "g",
            },
            headers=headers,
        )
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["denial_code"] == "capability_not_permitted"

    def test_a_draft_creates_no_proposal(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="propose")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/flow-experiments/draft",
            json={
                "flow_subject_kind": "runtime_flow",
                "flow_subject_ref": FLOW_ID,
                "node_keys": ["node-a"],
                "goal": "コストを下げたい",
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert admin_client.get("/flow-experiments", headers=headers).json()["proposals"] == []
        with get_conn() as conn:
            assert (
                conn.execute("SELECT COUNT(*) AS n FROM flow_experiment_event").fetchone()["n"]
                == 0
            )
            run = conn.execute(
                "SELECT * FROM intelligence_runs WHERE run_type = 'flow_experiment_draft'"
            ).fetchone()
        assert run["status"] == "completed"
        assert run["decision_method"] == "reasoning_llm"
        assert run["is_mock"] == 1

    def test_draft_is_reachable_despite_the_int_path_parameter(self, admin_client):
        """`/flow-experiments/draft` must not be captured by `/{proposal_id}`."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="propose")
        headers = _headers(token, system_id)
        r = admin_client.post(
            "/flow-experiments/draft",
            json={
                "flow_subject_kind": "runtime_flow",
                "flow_subject_ref": FLOW_ID,
                "node_keys": ["node-a"],
                "goal": "g",
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text


class TestReasoningFailsClosed:
    def test_invalid_structured_output_creates_no_proposal(self, admin_client, monkeypatch):
        """Principle 6: the run fails and there is no heuristic repair."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="propose")
        headers = _headers(token, system_id)

        from app import llm

        monkeypatch.setattr(
            llm.MockLLMClient,
            "generate_text",
            lambda self, messages, **kwargs: '{"title": "half a plan"}',
        )
        r = admin_client.post(
            "/flow-experiments/draft",
            json={
                "flow_subject_kind": "runtime_flow",
                "flow_subject_ref": FLOW_ID,
                "node_keys": ["node-a"],
                "goal": "g",
            },
            headers=headers,
        )
        assert r.status_code == 502, r.text
        assert admin_client.get("/flow-experiments", headers=headers).json()["proposals"] == []
        with get_conn() as conn:
            run = conn.execute(
                "SELECT * FROM intelligence_runs WHERE run_type = 'flow_experiment_draft'"
            ).fetchone()
            events = conn.execute(
                "SELECT COUNT(*) AS n FROM flow_experiment_event"
            ).fetchone()["n"]
        assert run["status"] == "failed"
        assert run["error_details"]
        assert events == 0

    def test_a_drafted_node_outside_the_request_fails_the_run(self, admin_client, monkeypatch):
        """An invented Node is a fabricated fact, not a value to drop."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="propose")
        headers = _headers(token, system_id)

        from app import llm

        payload = {
            "title": "t", "purpose": "p", "hypothesis": "h",
            "comparison_scope": "single_node",
            "target_node_keys": ["node-invented"],
            "baseline_ref": "b", "candidate_refs": ["c"],
            "evaluation_axes": [{"level": "node", "name": "match"}],
            "quality_floor": {"match": "x"}, "isolation_strategy": "mock",
            "isolation_detail": "d", "cost_cap": {"max_runs": 1},
            "stop_conditions": ["s"], "rollback_plan": "r", "evidence_refs": ["e"],
        }
        monkeypatch.setattr(
            llm.MockLLMClient,
            "generate_text",
            lambda self, messages, **kwargs: json.dumps(payload),
        )
        r = admin_client.post(
            "/flow-experiments/draft",
            json={
                "flow_subject_kind": "runtime_flow",
                "flow_subject_ref": FLOW_ID,
                "node_keys": ["node-a"],
                "goal": "g",
            },
            headers=headers,
        )
        assert r.status_code == 502
        assert "node-invented" in r.json()["detail"]

    def test_an_llm_error_fails_the_run(self, admin_client, monkeypatch):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="propose")
        headers = _headers(token, system_id)

        from app import llm

        def _boom(self, messages, **kwargs):
            raise llm.LLMError("provider unavailable")

        monkeypatch.setattr(llm.MockLLMClient, "generate_text", _boom)
        r = admin_client.post(
            "/flow-experiments/draft",
            json={
                "flow_subject_kind": "runtime_flow",
                "flow_subject_ref": FLOW_ID,
                "node_keys": ["node-a"],
                "goal": "g",
            },
            headers=headers,
        )
        assert r.status_code == 502
        with get_conn() as conn:
            run = conn.execute(
                "SELECT * FROM intelligence_runs WHERE run_type = 'flow_experiment_draft'"
            ).fetchone()
        assert run["status"] == "failed"


# ---------------------------------------------------------------------------
# System isolation
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_a_proposal_of_another_system_is_indistinguishable_from_absent(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "sys-a")
        system_b = _create_system(admin_client, token, "sys-b")
        _seed(system_a, mode="shadow")
        _seed(system_b, mode="shadow")

        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=_headers(token, system_a)
        ).json()["id"]

        headers_b = _headers(token, system_b)
        assert (
            admin_client.get(f"/flow-experiments/{proposal_id}", headers=headers_b).status_code
            == 404
        )
        assert admin_client.get("/flow-experiments", headers=headers_b).json()["proposals"] == []
        assert (
            admin_client.post(
                f"/flow-experiments/{proposal_id}/approve",
                json={"reason": "ok"},
                headers=headers_b,
            ).status_code
            == 404
        )

    def test_the_same_proposal_key_is_free_in_another_system(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "sys-a")
        system_b = _create_system(admin_client, token, "sys-b")
        _seed(system_a, mode="shadow")
        _seed(system_b, mode="shadow")
        assert (
            admin_client.post(
                "/flow-experiments", json=_body(), headers=_headers(token, system_a)
            ).status_code
            == 201
        )
        assert (
            admin_client.post(
                "/flow-experiments", json=_body(), headers=_headers(token, system_b)
            ).status_code
            == 201
        )

    def test_a_node_of_another_system_does_not_resolve(self, admin_client):
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "sys-a")
        system_b = _create_system(admin_client, token, "sys-b")
        _seed(system_a, mode="shadow")
        _seed(system_b, mode="shadow")
        with get_conn() as conn:
            _add_node(conn, system_a, "node-only-in-a")
        r = admin_client.post(
            "/flow-experiments",
            json=_body(targets=[{"node_key": "node-only-in-a"}]),
            headers=_headers(token, system_b),
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "unresolved_node"


class TestFlowExplanationInterop:
    """#414's projection reads this module's fold rather than defining a
    second lifecycle. A signature it cannot call would push it into writing
    one, which is the drift the fold exists to prevent."""

    def test_the_projection_reads_the_derived_status(self, admin_client):
        from app.flow_explanation import build_flow_explanation

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        proposal_id = admin_client.post(
            "/flow-experiments", json=_body(), headers=headers
        ).json()["id"]
        admin_client.post(
            f"/flow-experiments/{proposal_id}/approve", json={"reason": "ok"}, headers=headers
        )

        result = build_flow_explanation(
            system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
        )
        assert result.experiments.status_source == "flow_orchestration"
        summary = result.experiments.proposals[0]
        assert summary.proposal_id == proposal_id
        assert (summary.status, summary.status_state) == ("approved", "present")
        assert summary.target_node_keys == ["node-a"]

    def test_the_projection_sees_an_elapsed_approval_window(self, admin_client):
        from app.flow_explanation import build_flow_explanation

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id, mode="shadow")
        headers = _headers(token, system_id)
        admin_client.post(
            "/flow-experiments",
            json=_body(expires_at=time.time() - 60),
            headers=headers,
        )
        result = build_flow_explanation(
            system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
        )
        assert result.experiments.proposals[0].status == "expired"


# ---------------------------------------------------------------------------
# Fact gathering against the database
# ---------------------------------------------------------------------------


class TestFactGathering:
    def test_runtime_flow_resolves_from_an_observed_span(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        with get_conn() as conn:
            facts = flow_orchestration.gather_proposal_facts(
                conn,
                system_id=system_id,
                content=_content(),
                targets=[{"node_key": "node-a"}],
            )
        assert facts.flow_subject_known is True
        assert facts.targets[0].resolved is True
        assert facts.targets[0].side_effect_class == "pure"
        assert facts.targets[0].in_flow is True

    def test_an_unobserved_unmodelled_flow_does_not_resolve(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        with get_conn() as conn:
            facts = flow_orchestration.gather_proposal_facts(
                conn,
                system_id=system_id,
                content=_content(flow_subject_ref="ghost-flow"),
                targets=[{"node_key": "node-a"}],
            )
        assert facts.flow_subject_known is False

    def test_static_flow_needs_its_pinned_snapshot(self, admin_client):
        """An entrypoint id without the snapshot it was read from names
        nothing (§6.2)."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _seed(system_id)
        now = time.time()
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO repository_snapshots
                       (system_id, repo_path, commit_sha, status, created_at, completed_at)
                   VALUES (?, '/tmp/repo', 'c0ffee', 'ready', ?, ?)""",
                (system_id, now, now),
            )
            snapshot_id = cur.lastrowid
            conn.execute(
                """INSERT INTO code_entrypoints
                       (system_id, snapshot_id, entrypoint_type, entrypoint_id, category,
                        label, handler_path, handler_qualified_name, line_start, line_end,
                        created_at)
                   VALUES (?, ?, 'http_route', 'ep-1', 'api', 'checkout',
                           'app/checkout.py', 'handle_checkout', 1, 5, ?)""",
                (system_id, snapshot_id, now),
            )
            without_snapshot = flow_orchestration.gather_proposal_facts(
                conn,
                system_id=system_id,
                content=_content(flow_subject_kind="static_flow", flow_subject_ref="ep-1"),
                targets=[{"node_key": "node-a"}],
            )
            with_snapshot = flow_orchestration.gather_proposal_facts(
                conn,
                system_id=system_id,
                content=_content(
                    flow_subject_kind="static_flow",
                    flow_subject_ref="ep-1",
                    captured_snapshot_id=snapshot_id,
                ),
                targets=[{"node_key": "node-a"}],
            )
        assert without_snapshot.flow_subject_known is False
        assert with_snapshot.flow_subject_known is True
        # Static membership is not determinable from persisted rows alone.
        assert with_snapshot.targets[0].in_flow is None

    def test_unknown_system_is_not_found(self, admin_client):
        _login(admin_client)
        with get_conn() as conn:
            with pytest.raises(flow_orchestration.FlowExperimentNotFoundError):
                flow_orchestration.gather_proposal_facts(
                    conn, system_id=9999, content=_content(), targets=[]
                )
