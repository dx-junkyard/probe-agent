"""Tests for Epic #412 Issue #413: the execution-mode contract and the
fail-closed capability gate.

Canonical contract: `docs/execution-modes.md` §9.1. What must hold:

1. Each of the ten resolution rows of §3.3 is reached on its own, and the
   resolver is a pure function of `ModeFacts`.
2. An ELAPSED window clamps to `fixed` instead of falling through to a
   broader scope, while an explicitly REVOKED assignment lets normal
   inheritance resume (EM-ADR-2). Time alone never restores a permission.
3. One Node on two Flows whose assignments disagree resolves to `fixed` with
   `flow_scope_conflict` -- there is no rule for choosing between two human
   decisions.
4. `build_experiment_llm_adapter` raises BEFORE any credential is read: in
   `fixed`/`observe` the lines that call `LLMConfig.intelligence_from_env` and
   `create_llm_client` are never reached (§4.2 / EM-ADR-3).
5. `propose` cannot execute a candidate. A proposal is a plan.
6. The assignment actor comes from the authenticated principal, never the
   body (#337), and `decision_method` is always `manual`.
7. Another System's assignments and observations are never visible.
8. All four divergence states, with `unobserved` never reported as `match`.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import execution_mode, llm
from app.db import get_conn
from app.evolution_node import add_link, create_node
from app.execution_mode import (
    EXECUTION_CAPABILITIES,
    EXECUTION_MODES,
    FLOW_SCOPE_PREFIX,
    MODE_CAPABILITIES,
    MODE_PERMISSIVENESS,
    ExecutionModeDecision,
    ExecutionModeDenied,
    ExecutionModeNotFoundError,
    ExecutionModeValidationError,
    ModeFacts,
    ScopeReading,
    assign_mode,
    build_experiment_llm_adapter,
    build_mode_projection,
    classify_scope_state,
    evaluate_divergence,
    load_mode_facts,
    permitted_capabilities,
    record_observation,
    require_capability,
    resolve_execution_mode,
    revoke_mode,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "execution-mode-test.db"))
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


def _make_node(system_id, node_key, flow_ids=()):
    with get_conn() as conn:
        node = create_node(conn, system_id=system_id, node_key=node_key)
        for flow_id in flow_ids:
            add_link(
                conn,
                system_id=system_id,
                node_id=node["id"],
                link_kind="flow",
                target_ref=flow_id,
                created_by="root",
            )
        return node["id"]


def _reading(kind, ref, state, mode=None):
    return ScopeReading(scope_kind=kind, scope_ref=ref, state=state, mode=mode)


def _facts(*, system=None, flows=(), node=None, rejected_flow_claims=()):
    return ModeFacts(
        system_reading=system or _reading("system", "", "unset"),
        flow_readings=tuple(flows),
        node_reading=node,
        node_key="n" if node is not None else None,
        rejected_flow_claims=tuple(rejected_flow_claims),
        now=1000.0,
    )


# ---------------------------------------------------------------------------
# Finite vocabularies and the capability table (§1.3)
# ---------------------------------------------------------------------------


def test_mode_capability_table_is_complete_and_exact():
    assert EXECUTION_MODES == ("fixed", "observe", "propose", "shadow")
    # A COMPLETE mapping, not a first-match rule list: every mode names what
    # it permits, so a new mode inherits nothing by accident.
    assert set(MODE_CAPABILITIES) == set(EXECUTION_MODES)
    assert MODE_CAPABILITIES["fixed"] == frozenset()
    assert MODE_CAPABILITIES["observe"] == frozenset({"observation_record"})
    assert MODE_CAPABILITIES["propose"] == frozenset(
        {"observation_record", "llm_experiment_proposal"}
    )
    assert MODE_CAPABILITIES["shadow"] == frozenset(EXECUTION_CAPABILITIES)
    # `propose` deliberately stops short of executing anything.
    assert "candidate_execution" not in MODE_CAPABILITIES["propose"]
    assert "shadow_comparison" not in MODE_CAPABILITIES["propose"]


def test_permissiveness_ordering_is_fixed_and_unknown_mode_permits_nothing():
    assert (
        MODE_PERMISSIVENESS["fixed"]
        < MODE_PERMISSIVENESS["observe"]
        < MODE_PERMISSIVENESS["propose"]
        < MODE_PERMISSIVENESS["shadow"]
    )
    assert permitted_capabilities("nonsense") == ()
    assert permitted_capabilities("fixed") == ()


# ---------------------------------------------------------------------------
# §3.2: the scope-state table
# ---------------------------------------------------------------------------


def _row(**over):
    base = {
        "id": 1,
        "record_kind": "assign",
        "mode": "propose",
        "effective_from": 100.0,
        "effective_until": None,
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    "rows, now, expected",
    [
        ([], 200.0, "unset"),                                            # 1
        ([_row(record_kind="revoke", mode=None)], 200.0, "revoked"),     # 2
        ([_row(effective_from=500.0)], 200.0, "pending"),                # 3
        ([_row(effective_until=150.0)], 200.0, "expired"),               # 4
        ([_row(mode="banana")], 200.0, "invalid"),                       # 5
        ([_row(mode=None)], 200.0, "invalid"),                           # 5 (NULL)
        ([_row()], 200.0, "active"),                                     # 6
        ([_row(id=1), _row(id=2)], 200.0, "conflicting"),                # defensive
    ],
)
def test_classify_scope_state_first_match_table(rows, now, expected):
    reading = classify_scope_state(rows, scope_kind="node", scope_ref="n", now=now)
    assert reading.state == expected


def test_expiry_boundary_is_inclusive_at_effective_until():
    # `now >= effective_until` is expired: the last instant of the window has
    # passed, so the permission is gone rather than lingering for one tick.
    assert classify_scope_state(
        [_row(effective_until=200.0)], scope_kind="system", scope_ref="", now=199.9
    ).state == "active"
    assert classify_scope_state(
        [_row(effective_until=200.0)], scope_kind="system", scope_ref="", now=200.0
    ).state == "expired"


# ---------------------------------------------------------------------------
# §3.3: each of the ten resolution rows, individually, on the PURE resolver
# ---------------------------------------------------------------------------


def test_row1_conflicting_anywhere_clamps_to_fixed():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "shadow"),
            node=_reading("node", "n", "conflicting"),
        )
    )
    assert (decision.mode, decision.source_scope, decision.reason) == (
        "fixed", "none", "conflicting_assignments",
    )


def test_row2_invalid_mode_value_clamps_to_fixed():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "shadow"),
            flows=[_reading("flow", "runtime_flow:f1", "invalid")],
        )
    )
    assert (decision.mode, decision.source_scope, decision.reason) == (
        "fixed", "none", "invalid_mode_value",
    )


def test_row3_expired_node_does_not_inherit_from_system():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "propose"),
            node=_reading("node", "n", "expired", "shadow"),
        )
    )
    assert (decision.mode, decision.source_scope, decision.reason) == (
        "fixed", "none", "node_expired_assignment",
    )


def test_row4_active_node_assignment_wins():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "observe"),
            flows=[_reading("flow", "runtime_flow:f1", "active", "propose")],
            node=_reading("node", "n", "active", "shadow"),
        )
    )
    assert decision.mode == "shadow"
    assert decision.source_scope == "node"
    assert decision.source_ref == "n"
    assert decision.reason == "node_assignment"


def test_row5_expired_flow_does_not_inherit_from_system():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "propose"),
            flows=[_reading("flow", "runtime_flow:f1", "expired", "shadow")],
        )
    )
    assert (decision.mode, decision.source_scope, decision.reason) == (
        "fixed", "none", "flow_expired_assignment",
    )


def test_row6_two_active_flows_with_different_modes_conflict():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "observe"),
            flows=[
                _reading("flow", "runtime_flow:f1", "active", "propose"),
                _reading("flow", "runtime_flow:f2", "active", "shadow"),
            ],
        )
    )
    assert (decision.mode, decision.source_scope, decision.reason) == (
        "fixed", "none", "flow_scope_conflict",
    )


def test_row7_agreeing_flows_decide_and_name_a_reproducible_ref():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "fixed"),
            flows=[
                _reading("flow", "runtime_flow:f2", "active", "propose"),
                _reading("flow", "runtime_flow:f1", "active", "propose"),
            ],
        )
    )
    assert decision.mode == "propose"
    assert decision.source_scope == "flow"
    assert decision.reason == "flow_assignment"
    assert decision.source_ref == "runtime_flow:f1"


def test_row8_expired_system_assignment_clamps_to_fixed():
    decision = resolve_execution_mode(
        _facts(system=_reading("system", "", "expired", "shadow"))
    )
    assert (decision.mode, decision.source_scope, decision.reason) == (
        "fixed", "none", "system_expired_assignment",
    )


def test_row9_system_assignment_applies():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "observe"),
            node=_reading("node", "n", "unset"),
        )
    )
    assert decision.mode == "observe"
    assert decision.source_scope == "system"
    assert decision.reason == "system_assignment"


def test_row10_no_assignment_is_the_default_fixed():
    decision = resolve_execution_mode(_facts())
    assert (decision.mode, decision.source_scope, decision.reason) == (
        "fixed", "default", "no_assignment",
    )
    # `default` and a human choosing `fixed` are two different facts (§4.4).
    chosen = resolve_execution_mode(
        _facts(system=_reading("system", "", "active", "fixed"))
    )
    assert chosen.mode == "fixed"
    assert (chosen.source_scope, chosen.reason) == ("system", "system_assignment")


def test_revoked_scope_falls_through_to_the_next_scope():
    # `revoked` matches no row of the table, so inheritance resumes -- the one
    # difference from `expired` (EM-ADR-2).
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "propose"),
            node=_reading("node", "n", "revoked"),
        )
    )
    assert decision.mode == "propose"
    assert decision.reason == "system_assignment"


def test_pending_scope_falls_through_and_does_not_grant_early():
    decision = resolve_execution_mode(
        _facts(
            system=_reading("system", "", "active", "observe"),
            node=_reading("node", "n", "pending", "shadow"),
        )
    )
    assert decision.mode == "observe"
    assert decision.reason == "system_assignment"


def test_resolver_is_pure():
    facts = _facts(system=_reading("system", "", "active", "propose"))
    first = resolve_execution_mode(facts)
    second = resolve_execution_mode(facts)
    assert first == second
    assert isinstance(first, ExecutionModeDecision)
    # The trace explains the answer instead of only asserting it.
    assert [r.scope_kind for r in first.scope_trace] == ["system"]


# ---------------------------------------------------------------------------
# Persistence: expired vs revoked, end to end through the database
# ---------------------------------------------------------------------------


def test_expired_node_assignment_does_not_revive_via_system_scope(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-expiry")
    _make_node(system_id, "node-a")

    now = time.time()
    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="propose", reason="system default", actor="root", now=now - 100,
        )
        assign_mode(
            conn, system_id=system_id, scope_kind="node", scope_ref="node-a",
            mode="shadow", reason="one week experiment", actor="root",
            effective_from=now - 100, effective_until=now - 10, now=now - 100,
        )
        decision = resolve_execution_mode(
            load_mode_facts(conn, system_id=system_id, node_key="node-a", now=now)
        )
    assert decision.mode == "fixed"
    assert decision.reason == "node_expired_assignment"

    # Time alone never restores a permission -- a human records a revoke (or a
    # new assign) and only THEN does inheritance resume.
    with get_conn() as conn:
        current = execution_mode.list_assignments(
            conn, system_id=system_id, scope_kind="node", scope_ref="node-a",
            current_only=True,
        )
        revoke_mode(
            conn, system_id=system_id, assignment_id=current[0]["id"],
            reason="experiment over", actor="root", now=now,
        )
        after = resolve_execution_mode(
            load_mode_facts(conn, system_id=system_id, node_key="node-a", now=now)
        )
    assert after.mode == "propose"
    assert after.reason == "system_assignment"


def test_assignment_records_previous_mode_and_chains_append_only(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-audit")

    with get_conn() as conn:
        first = assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="observe", reason="start observing", actor="root",
        )
        second = assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="propose", reason="allow proposals", actor="root",
        )
        rows = execution_mode.list_assignments(conn, system_id=system_id)
        refetched_first = conn.execute(
            "SELECT * FROM execution_mode_assignment WHERE id = ?", (first["id"],)
        ).fetchone()

    # Nothing is UPDATEd but the chain pointer.
    assert len(rows) == 2
    assert refetched_first["superseded_by_id"] == second["id"]
    assert refetched_first["mode"] == "observe"
    assert second["supersedes_id"] == first["id"]
    # The audit reads "what changed" without recomputing it (#337).
    assert first["previous_mode"] == "fixed"
    assert second["previous_mode"] == "observe"
    assert second["decision_method"] == "manual"


def test_flow_scope_conflict_from_real_node_flow_links(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-flows")
    _make_node(system_id, "node-b", flow_ids=["checkout", "reporting"])

    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="flow",
            scope_ref="checkout", mode="propose", reason="propose here", actor="root",
        )
        assign_mode(
            conn, system_id=system_id, scope_kind="flow",
            scope_ref=FLOW_SCOPE_PREFIX + "reporting", mode="shadow",
            reason="shadow here", actor="root",
        )
        facts = load_mode_facts(conn, system_id=system_id, node_key="node-b")
        decision = resolve_execution_mode(facts)

    # The bare ref and the prefixed ref are stored as ONE spelling (§2.1).
    assert [r.scope_ref for r in facts.flow_readings] == [
        "runtime_flow:checkout", "runtime_flow:reporting",
    ]
    assert decision.mode == "fixed"
    assert decision.reason == "flow_scope_conflict"

    with get_conn() as conn:
        with pytest.raises(ExecutionModeDenied) as excinfo:
            require_capability(
                conn, system_id=system_id,
                capability="llm_experiment_proposal", node_key="node-b",
            )
    assert excinfo.value.denial_code == "flow_scope_conflict"


def test_agreeing_flows_grant_the_shared_mode(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-flows-agree")
    _make_node(system_id, "node-c", flow_ids=["checkout", "reporting"])

    with get_conn() as conn:
        for flow in ("checkout", "reporting"):
            assign_mode(
                conn, system_id=system_id, scope_kind="flow", scope_ref=flow,
                mode="propose", reason="both propose", actor="root",
            )
        decision = require_capability(
            conn, system_id=system_id,
            capability="llm_experiment_proposal", node_key="node-c",
        )
    assert decision.mode == "propose"
    assert decision.source_scope == "flow"


# ---------------------------------------------------------------------------
# §4: the fail-closed gate
# ---------------------------------------------------------------------------


def _trap(*args, **kwargs):
    raise AssertionError(
        "the credential path was reached -- build_experiment_llm_adapter must "
        "refuse BEFORE reading a credential"
    )


@pytest.mark.parametrize("mode", ["fixed", "observe"])
def test_adapter_refuses_before_reading_any_credential(admin_client, monkeypatch, mode):
    """§4.2 / EM-ADR-3: `fixed` is not "the LLM is switched off in config", it
    is "the line that reads a credential does not execute"."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, f"sys-gate-{mode}")
    _make_node(system_id, "node-gate")

    monkeypatch.setattr(llm.LLMConfig, "intelligence_from_env", staticmethod(_trap))
    monkeypatch.setattr(llm, "create_llm_client", _trap)

    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode=mode, reason="explicit", actor="root",
        )
        with pytest.raises(ExecutionModeDenied) as excinfo:
            build_experiment_llm_adapter(
                conn, system_id=system_id, node_key="node-gate",
                purpose="draft a flow experiment",
            )
    # An AssertionError from either trap would have surfaced instead.
    assert excinfo.value.denial_code == "capability_not_permitted"
    assert excinfo.value.decision is not None
    assert excinfo.value.decision.mode == mode


def test_adapter_reaches_the_credential_path_only_once_permitted(
    admin_client, monkeypatch
):
    """The positive control for the test above: the traps ARE wired, so
    reaching them raises. If this passed while the previous test also passed
    only because the traps were inert, this assertion would fail."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-gate-open")
    _make_node(system_id, "node-open")

    monkeypatch.setattr(llm.LLMConfig, "intelligence_from_env", staticmethod(_trap))
    monkeypatch.setattr(llm, "create_llm_client", _trap)

    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="propose", reason="proposals allowed", actor="root",
        )
        with pytest.raises(AssertionError):
            build_experiment_llm_adapter(
                conn, system_id=system_id, node_key="node-open",
                purpose="draft a flow experiment",
            )


def test_adapter_builds_a_real_client_in_propose(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-gate-real")
    _make_node(system_id, "node-real")

    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="propose", reason="proposals allowed", actor="root",
        )
        client, decision = build_experiment_llm_adapter(
            conn, system_id=system_id, node_key="node-real", purpose="draft",
        )
    assert isinstance(client, llm.LLMClient)
    assert decision.mode == "propose"


def test_adapter_requires_a_purpose(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-gate-purpose")
    _make_node(system_id, "node-purpose")
    with get_conn() as conn:
        with pytest.raises(ExecutionModeValidationError):
            build_experiment_llm_adapter(
                conn, system_id=system_id, node_key="node-purpose", purpose="  ",
            )


def test_propose_permits_proposal_but_refuses_candidate_execution(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-propose")
    _make_node(system_id, "node-p")

    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="node", scope_ref="node-p",
            mode="propose", reason="plan only", actor="root",
        )
        allowed = require_capability(
            conn, system_id=system_id,
            capability="llm_experiment_proposal", node_key="node-p",
        )
        with pytest.raises(ExecutionModeDenied) as execution_exc:
            require_capability(
                conn, system_id=system_id,
                capability="candidate_execution", node_key="node-p",
            )
        with pytest.raises(ExecutionModeDenied) as comparison_exc:
            require_capability(
                conn, system_id=system_id,
                capability="shadow_comparison", node_key="node-p",
            )
    assert allowed.mode == "propose"
    assert execution_exc.value.denial_code == "capability_not_permitted"
    assert execution_exc.value.decision.mode == "propose"
    assert comparison_exc.value.denial_code == "capability_not_permitted"


def test_shadow_permits_every_capability(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-shadow")
    _make_node(system_id, "node-s")
    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="node", scope_ref="node-s",
            mode="shadow", reason="approved comparison window", actor="root",
        )
        for capability in EXECUTION_CAPABILITIES:
            decision = require_capability(
                conn, system_id=system_id, capability=capability, node_key="node-s",
            )
            assert decision.mode == "shadow"


def test_unknown_capability_and_unknown_node_are_denied_with_no_mode_claim(
    admin_client,
):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-unknown")
    with get_conn() as conn:
        with pytest.raises(ExecutionModeDenied) as cap_exc:
            require_capability(
                conn, system_id=system_id, capability="deploy_to_production",
            )
        with pytest.raises(ExecutionModeDenied) as node_exc:
            require_capability(
                conn, system_id=system_id,
                capability="observation_record", node_key="ghost",
            )
    assert cap_exc.value.denial_code == "unknown_capability"
    assert node_exc.value.denial_code == "node_not_found"
    # An unreadable subject is never given a default mode reading (#380).
    assert cap_exc.value.decision is None
    assert node_exc.value.decision is None


# ---------------------------------------------------------------------------
# §5.2: the four divergence states
# ---------------------------------------------------------------------------


def test_divergence_unobserved_is_never_reported_as_match(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-div-unobserved")
    _make_node(system_id, "node-d1")
    with get_conn() as conn:
        reading = evaluate_divergence(conn, system_id=system_id, node_key="node-d1")
    # The effective mode IS `fixed` and no observation exists; reporting
    # `match` would turn "nobody looked" into a success.
    assert reading.effective_mode == "fixed"
    assert reading.divergence == "unobserved"
    assert reading.observed_mode is None


def test_divergence_match_and_divergent(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-div-match")
    _make_node(system_id, "node-d2")
    _make_node(system_id, "node-d3")

    now = time.time()
    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="observe", reason="observe everything", actor="root", now=now - 100,
        )
        record_observation(
            conn, system_id=system_id, node_key="node-d2",
            observed_mode="observe", source="sdk", now=now,
        )
        record_observation(
            conn, system_id=system_id, node_key="node-d3",
            observed_mode="shadow", source="sdk", now=now,
        )
        matched = evaluate_divergence(conn, system_id=system_id, node_key="node-d2", now=now)
        diverged = evaluate_divergence(conn, system_id=system_id, node_key="node-d3", now=now)

    assert matched.divergence == "match"
    assert diverged.divergence == "divergent"
    # Both readings are returned, so the disagreement is legible.
    assert diverged.effective_mode == "observe"
    assert diverged.observed_mode == "shadow"


def test_divergence_stale_when_the_mode_moved_after_the_last_observation(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-div-stale")
    _make_node(system_id, "node-d4")

    now = time.time()
    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="observe", reason="observe", actor="root", now=now - 200,
        )
        record_observation(
            conn, system_id=system_id, node_key="node-d4",
            observed_mode="observe", now=now - 100,
        )
        # The configuration moves AFTER the last observation: the observation
        # still describes the old world, so it is `stale`, not `divergent`.
        assign_mode(
            conn, system_id=system_id, scope_kind="node", scope_ref="node-d4",
            mode="propose", reason="promote", actor="root", now=now - 50,
        )
        reading = evaluate_divergence(conn, system_id=system_id, node_key="node-d4", now=now)

    assert reading.divergence == "stale"
    assert reading.effective_mode == "propose"
    assert reading.observed_mode == "observe"
    assert reading.last_assignment_at > reading.observed_at


def test_a_reading_carries_the_observations_standing(admin_client):
    """`divergence` answers "does the reading agree with the configuration?"
    and cannot answer "was the reading measured?". Nothing on any current
    path attests a runtime mode, so a `match` over HTTP is agreement with a
    value a human reported -- and the aggregation screens must be able to
    tell that from an instrumented reading (#366). Two facts, two fields."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-div-standing")
    _make_node(system_id, "node-d5")
    _make_node(system_id, "node-d6")
    _make_node(system_id, "node-d7")

    now = time.time()
    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="observe", reason="observe", actor="root", now=now - 100,
        )
        record_observation(
            conn, system_id=system_id, node_key="node-d5",
            observed_mode="observe", now=now,
        )
        record_observation(
            conn, system_id=system_id, node_key="node-d6",
            observed_mode="observe", run_ref="replay:12", now=now,
        )
        reported = evaluate_divergence(
            conn, system_id=system_id, node_key="node-d5", now=now
        )
        cited = evaluate_divergence(
            conn, system_id=system_id, node_key="node-d6", now=now
        )
        unobserved = evaluate_divergence(
            conn, system_id=system_id, node_key="node-d7", now=now
        )
        projection = build_mode_projection(conn, system_id=system_id, now=now)

    # Agreement, but with a value nobody measured.
    assert reported.divergence == "match"
    assert reported.observation_source == "control_server"
    assert reported.run_ref_state == "absent"
    # A pointer nobody resolved is `uncorroborated`, never `resolved`.
    assert cited.run_ref_state == "uncorroborated"
    # No observation, so no standing to report -- an absent standing is not a
    # standing (#380).
    assert unobserved.divergence == "unobserved"
    assert unobserved.observation_source is None
    assert unobserved.run_ref_state is None

    by_key = {node.node_key: node for node in projection.nodes}
    assert by_key["node-d5"].observation_source == "control_server"
    assert by_key["node-d6"].run_ref_state == "uncorroborated"
    assert by_key["node-d7"].observation_source is None


def test_observation_requires_a_known_node_and_a_known_mode(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-obs-validation")
    _make_node(system_id, "node-d5")
    with get_conn() as conn:
        with pytest.raises(ExecutionModeNotFoundError):
            record_observation(
                conn, system_id=system_id, node_key="ghost", observed_mode="observe",
            )
        with pytest.raises(ExecutionModeValidationError):
            record_observation(
                conn, system_id=system_id, node_key="node-d5", observed_mode="turbo",
            )


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def test_projection_distinguishes_default_fixed_from_a_chosen_fixed(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-projection")
    _make_node(system_id, "node-x")

    with get_conn() as conn:
        default_projection = build_mode_projection(conn, system_id=system_id)
    node = default_projection.nodes[0]
    assert node.execution_mode == "fixed"
    assert node.mode_source == "default"
    assert node.mode_reason == "no_assignment"
    # ADR-6: the maturity axis is shown beside the mode, never derived from it.
    assert node.maturity == "exploring"

    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_id, scope_kind="system", scope_ref="",
            mode="fixed", reason="deliberately frozen", actor="root",
        )
        chosen_projection = build_mode_projection(conn, system_id=system_id)
    chosen = chosen_projection.nodes[0]
    assert chosen.execution_mode == "fixed"
    assert chosen.mode_source == "system"
    assert chosen.mode_reason == "system_assignment"
    assert len(chosen_projection.assignments) == 1
    assert chosen_projection.assignments[0]["scope_state"] == "active"


def test_projection_writes_nothing(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-projection-read-only")
    _make_node(system_id, "node-y")
    tables = (
        "execution_mode_assignment",
        "execution_mode_observation",
        "evolution_node",
        "evolution_node_link",
    )
    with get_conn() as conn:
        before = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in tables}
        build_mode_projection(conn, system_id=system_id)
        after = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in tables}
    assert before == after


# ---------------------------------------------------------------------------
# System isolation
# ---------------------------------------------------------------------------


def test_assignments_and_observations_never_leak_across_systems(admin_client):
    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "sys-iso-a")
    system_b = _create_system(admin_client, token, "sys-iso-b")
    _make_node(system_a, "shared-key")
    _make_node(system_b, "shared-key")

    with get_conn() as conn:
        assign_mode(
            conn, system_id=system_a, scope_kind="system", scope_ref="",
            mode="shadow", reason="A only", actor="root",
        )
        assign_mode(
            conn, system_id=system_a, scope_kind="node", scope_ref="shared-key",
            mode="propose", reason="A node only", actor="root",
        )
        record_observation(
            conn, system_id=system_a, node_key="shared-key", observed_mode="propose",
        )
        decision_a = resolve_execution_mode(
            load_mode_facts(conn, system_id=system_a, node_key="shared-key")
        )
        decision_b = resolve_execution_mode(
            load_mode_facts(conn, system_id=system_b, node_key="shared-key")
        )
        assignments_b = execution_mode.list_assignments(conn, system_id=system_b)
        divergence_b = evaluate_divergence(
            conn, system_id=system_b, node_key="shared-key"
        )

    assert decision_a.mode == "propose"
    # The same node_key in another System sees nothing at all.
    assert decision_b.mode == "fixed"
    assert decision_b.reason == "no_assignment"
    assert assignments_b == []
    assert divergence_b.divergence == "unobserved"


def test_assigning_to_another_systems_node_is_not_found(admin_client):
    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "sys-cross-a")
    system_b = _create_system(admin_client, token, "sys-cross-b")
    _make_node(system_a, "only-in-a")
    with get_conn() as conn:
        with pytest.raises(ExecutionModeNotFoundError):
            assign_mode(
                conn, system_id=system_b, scope_kind="node", scope_ref="only-in-a",
                mode="propose", reason="cross-system", actor="root",
            )


# ---------------------------------------------------------------------------
# HTTP boundary
# ---------------------------------------------------------------------------


def test_assignment_actor_comes_from_the_principal_not_the_body(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-http-actor")

    r = admin_client.post(
        "/execution-modes/assignments",
        json={
            "scope_kind": "system",
            "scope_ref": "",
            "mode": "observe",
            "reason": "start observing",
            # Not a field of the model: an attempt to claim someone else's name.
            "actor": "someone-else",
        },
        headers=_headers(token, system_id),
    )
    assert r.status_code == 422, r.text  # extra="forbid"

    r = admin_client.post(
        "/execution-modes/assignments",
        json={
            "scope_kind": "system",
            "scope_ref": "",
            "mode": "observe",
            "reason": "start observing",
        },
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["actor"] == "root"
    assert body["actor_kind"] == "user"
    assert body["decision_method"] == "manual"
    assert body["previous_mode"] == "fixed"


def test_assignment_requires_a_reason(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-http-reason")
    r = admin_client.post(
        "/execution-modes/assignments",
        json={"scope_kind": "system", "scope_ref": "", "mode": "observe", "reason": "  "},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 422, r.text


def test_http_resolve_projection_divergence_and_revoke(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-http-flow")
    _make_node(system_id, "node-http")

    r = admin_client.post(
        "/execution-modes/assignments",
        json={
            "scope_kind": "node",
            "scope_ref": "node-http",
            "mode": "propose",
            "reason": "let the model draft experiments",
        },
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    assignment_id = r.json()["id"]

    r = admin_client.get(
        "/execution-modes/resolve?node_key=node-http", headers=_headers(token, system_id)
    )
    assert r.status_code == 200, r.text
    decision = r.json()
    assert decision["mode"] == "propose"
    assert decision["source_scope"] == "node"
    assert decision["reason"] == "node_assignment"
    assert decision["permitted_capabilities"] == [
        "observation_record", "llm_experiment_proposal",
    ]
    # The trace explains WHERE the answer came from.
    assert [entry["scope_kind"] for entry in decision["scope_trace"]] == ["node", "system"]

    # The gate preview returns the same 409 body the gated write paths use.
    r = admin_client.get(
        "/execution-modes/resolve?node_key=node-http&capability=candidate_execution",
        headers=_headers(token, system_id),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["denial_code"] == "capability_not_permitted"
    assert detail["mode"] == "propose"
    assert detail["source_scope"] == "node"

    r = admin_client.get(
        "/execution-modes/resolve?node_key=node-http&capability=llm_experiment_proposal",
        headers=_headers(token, system_id),
    )
    assert r.status_code == 200, r.text

    r = admin_client.post(
        "/execution-modes/observations",
        # `source` is NOT a request field: the route decides it (see
        # `test_observation_source_cannot_be_forged_over_http`).
        json={"node_key": "node-http", "observed_mode": "propose"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text

    r = admin_client.get(
        "/execution-modes/divergence", headers=_headers(token, system_id)
    )
    assert r.status_code == 200, r.text
    nodes = r.json()["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["divergence"] == "match"

    r = admin_client.get("/execution-modes", headers=_headers(token, system_id))
    assert r.status_code == 200, r.text
    projection = r.json()
    assert projection["nodes"][0]["execution_mode"] == "propose"
    assert projection["nodes"][0]["mode_source"] == "node"
    assert projection["system_decision"]["mode"] == "fixed"
    assert projection["system_decision"]["source_scope"] == "default"

    r = admin_client.post(
        f"/execution-modes/assignments/{assignment_id}/revoke",
        json={"reason": "experiment finished"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 201, r.text
    assert r.json()["record_kind"] == "revoke"
    assert r.json()["mode"] is None

    r = admin_client.get(
        "/execution-modes/resolve?node_key=node-http", headers=_headers(token, system_id)
    )
    assert r.json()["mode"] == "fixed"
    assert r.json()["reason"] == "no_assignment"

    # Revoking twice is a conflict, not a second revocation.
    r = admin_client.post(
        f"/execution-modes/assignments/{assignment_id}/revoke",
        json={"reason": "again"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 409, r.text


def test_http_endpoints_are_system_scoped(admin_client):
    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "sys-http-iso-a")
    system_b = _create_system(admin_client, token, "sys-http-iso-b")

    r = admin_client.post(
        "/execution-modes/assignments",
        json={"scope_kind": "system", "scope_ref": "", "mode": "shadow", "reason": "A"},
        headers=_headers(token, system_a),
    )
    assert r.status_code == 201, r.text
    assignment_id = r.json()["id"]

    r = admin_client.get("/execution-modes/assignments", headers=_headers(token, system_b))
    assert r.status_code == 200, r.text
    assert r.json() == []

    r = admin_client.get(
        "/execution-modes/resolve", headers=_headers(token, system_b)
    )
    assert r.json()["mode"] == "fixed"

    # A's assignment is indistinguishable from one that does not exist.
    r = admin_client.post(
        f"/execution-modes/assignments/{assignment_id}/revoke",
        json={"reason": "not mine"},
        headers=_headers(token, system_b),
    )
    assert r.status_code == 404, r.text


def test_http_requires_authentication(admin_client):
    r = admin_client.get("/execution-modes/resolve")
    assert r.status_code in (401, 403)


def test_models_and_domain_vocabularies_have_not_drifted():
    from app import models

    assert execution_mode.EXECUTION_MODES == tuple(models.ExecutionMode.__args__)
    assert execution_mode.EXECUTION_CAPABILITIES == tuple(
        models.ExecutionCapability.__args__
    )
    assert execution_mode.EXECUTION_MODE_DENIAL_CODES == tuple(
        models.ExecutionModeDenialCode.__args__
    )
    assert execution_mode.EXECUTION_MODE_DIVERGENCES == (
        "match", "divergent", "unobserved", "stale",
    )


def test_system_scope_refuses_a_scope_ref_instead_of_dropping_it(admin_client):
    """Silently discarding it would store a System-wide assignment for someone
    who believed they were narrowing it."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-scope-ref")
    with get_conn() as conn:
        with pytest.raises(ExecutionModeValidationError):
            assign_mode(
                conn, system_id=system_id, scope_kind="system", scope_ref="oops",
                mode="observe", reason="typo", actor="root",
            )


def test_unknown_scope_kind_and_empty_refs_are_refused(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-scope-kind")
    with get_conn() as conn:
        with pytest.raises(ExecutionModeValidationError):
            assign_mode(
                conn, system_id=system_id, scope_kind="capability", scope_ref="x",
                mode="observe", reason="nope", actor="root",
            )
        with pytest.raises(ExecutionModeValidationError):
            assign_mode(
                conn, system_id=system_id, scope_kind="flow", scope_ref="  ",
                mode="observe", reason="nope", actor="root",
            )
        with pytest.raises(ExecutionModeValidationError):
            assign_mode(
                conn, system_id=system_id, scope_kind="system", scope_ref="",
                mode="turbo", reason="nope", actor="root",
            )


# ---------------------------------------------------------------------------
# Regression: a caller's claim about scope is never evidence of scope
#
# The defect: `load_mode_facts` UNIONED the caller-supplied `flow_refs` with
# the Flows the Node actually belongs to. Anyone who could name a permissive
# Flow therefore reached that Flow's permission for a Node that resolved to
# `fixed` on its own -- the exact fail-closed violation EM-ADR-1 exists to
# prevent, since a scope must be decided from persisted rows.
# ---------------------------------------------------------------------------


class TestFlowScopeMembership:
    def test_a_claimed_flow_cannot_lend_its_mode_to_an_outside_node(
        self, admin_client
    ):
        """The escalation itself. Node B belongs to no Flow; Flow A is
        `propose`. Naming Flow A alongside Node B must NOT reach the LLM."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _make_node(system_id, "node-b")

        with get_conn() as conn:
            assign_mode(
                conn, system_id=system_id, scope_kind="flow",
                scope_ref="runtime_flow:flow-a", mode="propose",
                reason="experiment on flow A", actor="root",
            )
            alone = resolve_execution_mode(
                load_mode_facts(conn, system_id=system_id, node_key="node-b")
            )
            claimed = resolve_execution_mode(
                load_mode_facts(
                    conn, system_id=system_id, node_key="node-b",
                    flow_refs=["runtime_flow:flow-a"],
                )
            )
            assert (alone.mode, alone.reason) == ("fixed", "no_assignment")
            # Not merely "still fixed": the request itself is refused, so the
            # caller cannot believe Flow A's permission applied.
            assert (claimed.mode, claimed.reason) == (
                "fixed", "flow_scope_not_member",
            )
            with pytest.raises(ExecutionModeDenied) as excinfo:
                require_capability(
                    conn, system_id=system_id,
                    capability="llm_experiment_proposal",
                    node_key="node-b", flow_ref="runtime_flow:flow-a",
                )
        assert excinfo.value.denial_code == "flow_scope_not_member"

    def test_a_real_member_still_inherits_the_flows_mode(self, admin_client):
        """The fix must not break legitimate inheritance -- otherwise the
        Flow scope would be useless rather than safe."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _make_node(system_id, "node-a", flow_ids=["flow-a"])

        with get_conn() as conn:
            assign_mode(
                conn, system_id=system_id, scope_kind="flow",
                scope_ref="runtime_flow:flow-a", mode="propose",
                reason="experiment on flow A", actor="root",
            )
            decision = resolve_execution_mode(
                load_mode_facts(
                    conn, system_id=system_id, node_key="node-a",
                    flow_refs=["runtime_flow:flow-a"],
                )
            )
        assert (decision.mode, decision.reason) == ("propose", "flow_assignment")
        assert decision.source_scope == "flow"

    def test_membership_alone_is_enough_without_the_caller_naming_it(
        self, admin_client
    ):
        """Membership comes from the link table, so the caller never has to
        name the Flow -- which is why naming it can be treated as a claim."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _make_node(system_id, "node-a", flow_ids=["flow-a"])

        with get_conn() as conn:
            assign_mode(
                conn, system_id=system_id, scope_kind="flow",
                scope_ref="runtime_flow:flow-a", mode="observe",
                reason="watch flow A", actor="root",
            )
            decision = resolve_execution_mode(
                load_mode_facts(conn, system_id=system_id, node_key="node-a")
            )
        assert (decision.mode, decision.reason) == ("observe", "flow_assignment")

    def test_a_flow_scope_query_with_no_node_still_uses_the_named_flow(
        self, admin_client
    ):
        """With no Node named the caller's Flow IS the subject, not a claim
        about some other subject's scope."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        with get_conn() as conn:
            assign_mode(
                conn, system_id=system_id, scope_kind="flow",
                scope_ref="runtime_flow:flow-a", mode="propose",
                reason="experiment on flow A", actor="root",
            )
            decision = resolve_execution_mode(
                load_mode_facts(
                    conn, system_id=system_id, flow_refs=["runtime_flow:flow-a"]
                )
            )
        assert (decision.mode, decision.reason) == ("propose", "flow_assignment")

    def test_the_rejected_claim_is_carried_not_dropped(self, admin_client):
        """Silently ignoring the claim would leave the caller believing the
        Flow's permission applied -- the same defect from the other side."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _make_node(system_id, "node-b")
        with get_conn() as conn:
            facts = load_mode_facts(
                conn, system_id=system_id, node_key="node-b",
                flow_refs=["runtime_flow:flow-a"],
            )
        assert facts.rejected_flow_claims == ("runtime_flow:flow-a",)
        assert all(r.scope_ref != "runtime_flow:flow-a" for r in facts.flow_readings)

    def test_membership_of_another_system_does_not_count(self, admin_client):
        """A link in System B must not make the claim true in System A."""
        token = _login(admin_client)
        system_a = _create_system(admin_client, token, "sys-a")
        system_b = _create_system(admin_client, token, "sys-b")
        _make_node(system_a, "node-a")
        _make_node(system_b, "node-a", flow_ids=["flow-a"])

        with get_conn() as conn:
            assign_mode(
                conn, system_id=system_a, scope_kind="flow",
                scope_ref="runtime_flow:flow-a", mode="shadow",
                reason="experiment", actor="root",
            )
            decision = resolve_execution_mode(
                load_mode_facts(
                    conn, system_id=system_a, node_key="node-a",
                    flow_refs=["runtime_flow:flow-a"],
                )
            )
        assert (decision.mode, decision.reason) == ("fixed", "flow_scope_not_member")


# ---------------------------------------------------------------------------
# P2 regressions from the external review
# ---------------------------------------------------------------------------


class TestObservationProvenance:
    def test_source_cannot_be_supplied_by_the_caller(self, admin_client):
        """An observation exists to expose a configured-vs-runtime
        disagreement. A caller who can send `source: "sdk"` manufactures an
        SDK-attested reading and defeats that purpose, so provenance comes
        from the route (#337), and an attempt to supply one is refused rather
        than silently ignored."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _make_node(system_id, "node-a")
        headers = _headers(token, system_id)

        forged = admin_client.post(
            "/execution-modes/observations",
            json={"node_key": "node-a", "observed_mode": "shadow", "source": "sdk"},
            headers=headers,
        )
        assert forged.status_code == 422, forged.text

        accepted = admin_client.post(
            "/execution-modes/observations",
            json={"node_key": "node-a", "observed_mode": "shadow"},
            headers=headers,
        )
        assert accepted.status_code in (200, 201), accepted.text
        assert accepted.json()["source"] == "control_server"

    def test_an_unresolved_run_ref_is_reported_as_uncorroborated(
        self, admin_client
    ):
        """"This row cites a run" and "this row's citation was checked" are
        two facts. Nothing here resolves the pointer, so the response says so
        instead of implying it was verified."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _make_node(system_id, "node-a")
        headers = _headers(token, system_id)

        without = admin_client.post(
            "/execution-modes/observations",
            json={"node_key": "node-a", "observed_mode": "observe"},
            headers=headers,
        ).json()
        with_ref = admin_client.post(
            "/execution-modes/observations",
            json={
                "node_key": "node-a", "observed_mode": "observe",
                "run_ref": "replay_variant_run:999999",
            },
            headers=headers,
        ).json()
        assert without["run_ref_state"] == "absent"
        assert with_ref["run_ref_state"] == "uncorroborated"

    def test_http_caller_cannot_forge_execution_gate_attestation(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _make_node(system_id, "node-a")
        response = admin_client.post(
            "/execution-modes/observations",
            json={
                "node_key": "node-a",
                "observed_mode": "shadow",
                "run_ref": "execution_gate:experiment:1",
            },
            headers=_headers(token, system_id),
        )
        assert response.status_code == 422, response.text


class TestMissingNodeReadsConsistently:
    """"There is no such Node" is not the default value of "which mode is
    this Node in" (#380). Before this fix `resolve` and `divergence` answered
    a nonexistent Node with the fail-closed `fixed` reading a real
    unconfigured Node gets, while `?capability=` refused it -- one subject
    reading differently depending on which question was asked."""

    def test_resolve_refuses_an_unknown_node(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        r = admin_client.get(
            "/execution-modes/resolve",
            params={"node_key": "no-such-node"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 404, r.text

    def test_divergence_refuses_an_unknown_node(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        r = admin_client.get(
            "/execution-modes/divergence",
            params={"node_key": "no-such-node"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 404, r.text

    def test_capability_resolve_keeps_its_documented_409(self, admin_client):
        """The gate's own answer shape is unchanged (§4.1)."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        r = admin_client.get(
            "/execution-modes/resolve",
            params={"node_key": "no-such-node", "capability": "candidate_execution"},
            headers=_headers(token, system_id),
        )
        assert r.status_code in (404, 409), r.text
        if r.status_code == 409:
            assert r.json()["detail"]["denial_code"] == "node_not_found"

    def test_a_real_node_with_no_assignment_still_reads_as_default_fixed(
        self, admin_client
    ):
        """The other half: a Node that EXISTS and nobody configured must keep
        reading as the default `fixed`, distinct from `system_assignment`."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "sys-a")
        _make_node(system_id, "node-a")
        r = admin_client.get(
            "/execution-modes/resolve",
            params={"node_key": "node-a"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["mode"], body["reason"]) == ("fixed", "no_assignment")
        assert body["source_scope"] == "default"
