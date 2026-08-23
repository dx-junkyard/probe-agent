"""Tests for Epic #412 Issue #414: the read-only Flow explanation projection.

Canonical contract: `docs/execution-modes.md` §6, test requirements §9.2.
What must hold:

1. Purpose -> Capability -> Flow -> Node -> evidence is reachable in BOTH
   directions, and every evidence item carries a referencable id (§6.5).
2. The five axes are five separate fields and nothing anywhere is a single
   score / percentage / composite health number (§6.3, ADR-7, #353).
3. `missing` / `unavailable` / `unmeasured` / `stale` / `not_applicable` stay
   five distinct answers (§6.4).
4. One failing section lands in `degraded_sections` and the others still
   return (§6.4).
5. `mode_source: "default"` (the fail-closed `fixed` nobody chose) is
   distinguishable from `"system"` + `system_assignment` (a human choosing a
   mode), §4.4.
6. Static membership is EXACT `(path, qualified_name)` equality, and an
   unbuildable flow graph makes membership `unavailable` -- never an empty set
   (§6.2 / Principle 6).
7. The projection writes NOTHING: every table's row count is unchanged across
   a projection call and across the HTTP GETs.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import flow_explanation
from app.db import get_conn
from app.evolution_node import add_implementation, add_link, add_version, create_node
from app.execution_mode import assign_mode
from app.flow_explanation import build_flow_explanation, list_flow_subjects


FLOW_ID = "checkout-flow"
OTHER_FLOW_ID = "other-flow"

SOURCE = '''def handle_checkout(order):
    return charge_card(order)


def charge_card(order):
    return order
'''


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "flow-explanation-test.db"))
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


def _table_counts() -> dict:
    """Row count of every user table -- the write-nothing assertion's basis."""
    with get_conn() as conn:
        names = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        return {
            name: conn.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
            for name in names
        }


def _add_node(conn, system_id, node_key, *, modality="deterministic_code"):
    node = create_node(conn, system_id=system_id, node_key=node_key)
    version = add_version(
        conn,
        system_id=system_id,
        node_id=node["id"],
        mission=f"{node_key} の責務",
        input_contract={"kind": "object"},
        output_contract={"kind": "object"},
        side_effect_class="pure",
        trust_boundary="internal",
        created_by="root",
    )
    add_implementation(
        conn,
        system_id=system_id,
        node_id=node["id"],
        node_version_id=version["id"],
        modality=modality,
        created_by="root",
    )
    return node["id"]


def _seed_runtime_flow(system_id: int) -> dict:
    """A representative multi-Node runtime Flow.

    Three Nodes: two belong to the Flow (one via a Component whose SDK policy
    is `shadow`, one with a monitoring contract), one deliberately does not.
    """
    now = time.time()
    ids = {}
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO components (system_id, component_id, mode, updated_at) "
            "VALUES (?, 'summarize', 'shadow', ?)",
            (system_id, now),
        )
        conn.execute(
            "INSERT INTO components (system_id, component_id, mode, updated_at) "
            "VALUES (?, 'charge', 'trace', ?)",
            (system_id, now),
        )
        cur = conn.execute(
            "INSERT INTO understanding_capability_entity (system_id, entity_kind, created_at) "
            "VALUES (?, 'core_capability', ?)",
            (system_id, now),
        )
        capability_id = cur.lastrowid
        ids["capability_id"] = capability_id

        for index, (trace_id, component_id, span_id, parent) in enumerate(
            [
                ("t-1", "summarize", "s-1", None),
                ("t-2", "charge", "s-2", "s-1"),
            ]
        ):
            conn.execute(
                "INSERT INTO traces (system_id, trace_id, component_id, mode, timestamp) "
                "VALUES (?, ?, ?, 'trace', ?)",
                (system_id, trace_id, component_id, now + index),
            )
            conn.execute(
                """INSERT INTO trace_spans
                       (system_id, trace_id, component_id, span_id, parent_span_id,
                        flow_id, correlation_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (system_id, trace_id, component_id, span_id, parent, FLOW_ID, now + index),
            )

        node_a = _add_node(conn, system_id, "node-summarize", modality="reasoning_llm")
        node_b = _add_node(conn, system_id, "node-charge", modality="deterministic_code")
        node_c = _add_node(conn, system_id, "node-unrelated")
        ids.update(node_a=node_a, node_b=node_b, node_c=node_c)

        for node_id in (node_a, node_b):
            add_link(
                conn,
                system_id=system_id,
                node_id=node_id,
                link_kind="flow",
                target_ref=FLOW_ID,
                created_by="root",
            )
        add_link(
            conn, system_id=system_id, node_id=node_a, link_kind="component",
            target_ref="summarize", created_by="root",
        )
        add_link(
            conn, system_id=system_id, node_id=node_b, link_kind="component",
            target_ref="charge", created_by="root",
        )
        add_link(
            conn, system_id=system_id, node_id=node_a, link_kind="capability",
            target_ref=str(capability_id), created_by="root",
        )
        add_link(
            conn, system_id=system_id, node_id=node_a, link_kind="purpose_element",
            target_ref="core_capability:abc123", created_by="root",
        )
        add_link(
            conn, system_id=system_id, node_id=node_c, link_kind="flow",
            target_ref=OTHER_FLOW_ID, created_by="root",
        )
    return ids


def _declare_monitoring(system_id: int, node_id: int) -> int:
    """Give one Node a monitoring contract and a reading.

    `create_monitoring_contract` requires an `established`/`monitoring` Node,
    so the fixture sets the column directly. That deliberately leaves the
    stored maturity disagreeing with the (empty) transition log, which is the
    ADR-4 drift the projection must surface as its own open item rather than
    hide.
    """
    from app.node_operations import create_monitoring_contract, record_observation

    with get_conn() as conn:
        conn.execute(
            "UPDATE evolution_node SET maturity = 'established' WHERE id = ?", (node_id,)
        )
        contract = create_monitoring_contract(
            conn,
            system_id=system_id,
            node_id=node_id,
            freshness_budget_seconds=3600,
            minimum_sample_count=1,
            indicators=[{"indicator": "latency", "kind": "latency"}],
            created_by="root",
        )
        record_observation(
            conn,
            system_id=system_id,
            contract_id=contract["id"],
            indicator="latency",
            indicator_kind="latency",
            observation_state="within_budget",
            sample_count=10,
            last_observed_at=time.time(),
        )
        return contract["id"]


def _seed_static_flow(system_id: int, *, index_sources: bool = True) -> dict:
    """A static Flow: a pinned snapshot, an entrypoint, and two probe points.

    One probe point matches a flow-graph node's `(path, qualified_name)`
    EXACTLY; the other is a deliberate near miss (`charge_car` vs
    `charge_card`) that must NOT be admitted -- membership is equality, not
    similarity (Principle 6).
    """
    now = time.time()
    entrypoint_id = "function:app/checkout.py::handle_checkout"
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/repo', 'c0ffee', 'ready', ?, ?)""",
            (system_id, now, now),
        )
        snapshot_id = cur.lastrowid
        if index_sources:
            conn.execute(
                """INSERT INTO snapshot_files
                       (snapshot_id, path, source_type, size_bytes, content, inclusion_status)
                   VALUES (?, 'app/checkout.py', 'git', ?, ?, 'indexed')""",
                (snapshot_id, len(SOURCE), SOURCE.encode("utf-8")),
            )
            for qualified_name, start, end in (
                ("handle_checkout", 1, 2),
                ("charge_card", 5, 6),
            ):
                conn.execute(
                    """INSERT INTO code_symbols
                           (snapshot_id, system_id, path, qualified_name, kind,
                            start_line, end_line, decorators, imports, is_test)
                       VALUES (?, ?, 'app/checkout.py', ?, 'function', ?, ?, '[]', '[]', 0)""",
                    (snapshot_id, system_id, qualified_name, start, end),
                )
        conn.execute(
            """INSERT INTO code_entrypoints
                   (system_id, snapshot_id, entrypoint_type, entrypoint_id, category,
                    label, handler_path, handler_qualified_name, line_start, line_end,
                    created_at)
               VALUES (?, ?, 'public_function', ?, 'function', 'handle_checkout',
                       'app/checkout.py', 'handle_checkout', 1, 2, ?)""",
            (system_id, snapshot_id, entrypoint_id, now),
        )
        run = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model, prompt_version,
                    schema_version, decision_method, status, started_at)
               VALUES (?, ?, 'probe_plan', 'mock', 'mock', 'v1', 'v1', 'reasoning_llm',
                       'succeeded', ?)""",
            (system_id, snapshot_id, now),
        )
        plan = conn.execute(
            """INSERT INTO probe_plans
                   (system_id, snapshot_id, intelligence_run_id, feature_id, created_at,
                    updated_at)
               VALUES (?, ?, ?, 'feature-1', ?, ?)""",
            (system_id, snapshot_id, run.lastrowid, now, now),
        )
        probe_ids = {}
        for component_id, symbol in (
            ("summarize", "handle_checkout"),
            ("charge", "charge_car"),  # near miss on purpose
        ):
            point = conn.execute(
                """INSERT INTO probe_points
                       (plan_id, system_id, component_id, feature_id, path, symbol,
                        line_start, line_end, reason, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'feature-1', 'app/checkout.py', ?, 1, 2, 'r',
                           'approved', ?, ?)""",
                (plan.lastrowid, system_id, component_id, symbol, now, now),
            )
            probe_ids[symbol] = point.lastrowid

        node_exact = _add_node(conn, system_id, "node-static-exact")
        node_near = _add_node(conn, system_id, "node-static-near")
        add_link(
            conn, system_id=system_id, node_id=node_exact, link_kind="probe_point",
            target_ref=str(probe_ids["handle_checkout"]), created_by="root",
        )
        add_link(
            conn, system_id=system_id, node_id=node_near, link_kind="probe_point",
            target_ref=str(probe_ids["charge_car"]), created_by="root",
        )
    return {
        "snapshot_id": snapshot_id,
        "entrypoint_id": entrypoint_id,
        "node_exact": node_exact,
        "node_near": node_near,
    }


def _walk(value, path="$"):
    """Yield every (path, key, value) pair of a nested JSON document."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield f"{path}.{key}", key, child
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


# ---------------------------------------------------------------------------
# 1. Structure: the six sections and the five separate axes
# ---------------------------------------------------------------------------


def test_runtime_flow_projection_returns_six_sections_and_five_axes(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )

    assert result.subject.resolution == "resolved"
    assert result.membership.state == "resolved"
    # Only the two Nodes linked to THIS flow; the third belongs to another.
    assert result.membership.node_keys == ["node-charge", "node-summarize"]
    assert result.degraded_sections == []
    for section in flow_explanation.FLOW_EXPLANATION_SECTIONS:
        assert getattr(result, section) is not None, section

    by_key = {node.node_key: node for node in result.nodes}
    summarize = by_key["node-summarize"]

    # Five axes, five fields. Read independently, never combined.
    assert summarize.execution_mode == "fixed"
    assert summarize.maturity == "exploring"
    assert summarize.implementation_modality == "reasoning_llm"
    assert summarize.implementation_modality_state == "present"
    # A Node with no linked Cell has no improvement attempt: `missing`, not a
    # zero and not `unavailable`.
    assert summarize.improvement_status is None
    assert summarize.improvement_status_state == "missing"
    assert summarize.sdk_policy_mode == "shadow"
    assert summarize.sdk_policy_mode_state == "present"
    assert summarize.membership_basis == "flow_link"

    charge = by_key["node-charge"]
    assert charge.sdk_policy_mode == "trace"
    assert charge.implementation_modality == "deterministic_code"


def test_sdk_shadow_and_execution_shadow_are_two_fields(admin_client):
    """§1.2: SDK policy `shadow` and execution mode `shadow` are different
    facts, and one may hold without the other."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)
    with get_conn() as conn:
        assign_mode(
            conn,
            system_id=system_id,
            scope_kind="node",
            scope_ref="node-summarize",
            mode="propose",
            reason="実験提案のみ許可する",
            actor="root",
        )

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    summarize = {node.node_key: node for node in result.nodes}["node-summarize"]
    assert summarize.sdk_policy_mode == "shadow"
    assert summarize.execution_mode == "propose"
    assert summarize.mode_source == "node"
    assert summarize.mode_reason == "node_assignment"


# ---------------------------------------------------------------------------
# 2. `mode_source`: `default` vs `system_assignment` (§4.4)
# ---------------------------------------------------------------------------


def test_mode_source_default_is_distinct_from_a_chosen_fixed(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)

    before = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    for node in before.nodes:
        # Nobody chose this: the fail-closed default.
        assert node.execution_mode == "fixed"
        assert node.mode_source == "default"
        assert node.mode_reason == "no_assignment"

    with get_conn() as conn:
        assign_mode(
            conn,
            system_id=system_id,
            scope_kind="system",
            scope_ref="",
            mode="fixed",
            reason="固定実装のみで運用する",
            actor="root",
        )

    after = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    for node in after.nodes:
        # The SAME mode word, a different fact: a human chose it.
        assert node.execution_mode == "fixed"
        assert node.mode_source == "system"
        assert node.mode_reason == "system_assignment"


# ---------------------------------------------------------------------------
# 3. Static membership: exact match, and unavailable when the graph fails
# ---------------------------------------------------------------------------


def test_static_membership_is_exact_match_only(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    static = _seed_static_flow(system_id)

    result = build_flow_explanation(
        system_id,
        subject_kind="static_flow",
        subject_ref=static["entrypoint_id"],
        snapshot_id=static["snapshot_id"],
    )

    assert result.subject.resolution == "resolved"
    assert result.membership.state == "resolved"
    # `charge_car` is one character from `charge_card` and is still excluded.
    assert result.membership.node_keys == ["node-static-exact"]
    assert result.membership.basis == {"node-static-exact": "probe_point_exact_match"}
    assert result.responsibility.edge_source == "static_call_graph"


def test_static_membership_is_unavailable_not_empty_when_graph_unbuildable(
    admin_client,
):
    """§6.2: an unbuildable flow graph means we do not KNOW the membership.

    An empty set would claim no Node belongs to this Flow, which is a
    different (and unearned) answer.
    """
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    static = _seed_static_flow(system_id, index_sources=False)

    result = build_flow_explanation(
        system_id,
        subject_kind="static_flow",
        subject_ref=static["entrypoint_id"],
        snapshot_id=static["snapshot_id"],
    )

    assert result.membership.state == "unavailable"
    assert result.membership.node_keys == []
    assert result.responsibility.edge_source == "unavailable"
    kinds = {item.kind for item in result.open_items.items}
    assert "unresolved_membership" in kinds


# ---------------------------------------------------------------------------
# 4. The five missing answers stay five answers (§6.4)
# ---------------------------------------------------------------------------


def test_five_missing_values_are_distinct_and_reachable(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    ids = _seed_runtime_flow(system_id)
    _declare_monitoring(system_id, ids["node_b"])

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    by_key = {node.node_key: node for node in result.nodes}

    # not_applicable: a runtime Flow has no snapshot to be stale against.
    assert result.subject.snapshot_state == "not_applicable"
    # missing: no Cell is linked, so there is no improvement attempt.
    assert by_key["node-summarize"].improvement_status_state == "missing"
    # unmeasured: nothing measures this Node -- no monitoring contract.
    assert by_key["node-summarize"].observation_state == "unmeasured"
    assert by_key["node-summarize"].monitoring_contract_declared is False
    # present: the Node that does have a contract and a reading.
    assert by_key["node-charge"].observation_state == "present"
    assert by_key["node-charge"].monitoring_contract_declared is True

    # stale: the stored maturity disagrees with its own event log (ADR-4).
    drift = [
        item for item in result.open_items.items if item.kind == "maturity_drift"
    ]
    assert [item.node_key for item in drift] == ["node-charge"]
    assert drift[0].missing_state == "stale"

    # All five values remain separately spelled in the vocabulary.
    assert flow_explanation.MISSING_STATES == (
        "missing", "unavailable", "unmeasured", "stale", "not_applicable"
    )
    assert len(set(flow_explanation.MISSING_STATES)) == 5


def test_static_flow_on_an_older_snapshot_reports_a_stale_premise(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    static = _seed_static_flow(system_id)
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO repository_snapshots
                   (system_id, repo_path, commit_sha, status, created_at, completed_at)
               VALUES (?, '/tmp/repo', 'deadbee', 'ready', ?, ?)""",
            (system_id, now, now),
        )

    result = build_flow_explanation(
        system_id,
        subject_kind="static_flow",
        subject_ref=static["entrypoint_id"],
        snapshot_id=static["snapshot_id"],
    )
    stale = [item for item in result.open_items.items if item.kind == "stale_premise"]
    assert len(stale) == 1
    assert stale[0].missing_state == "stale"


# ---------------------------------------------------------------------------
# 5. One section fails, the others still return
# ---------------------------------------------------------------------------


def test_one_failing_section_degrades_alone(admin_client, monkeypatch):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("baseline read failed")

    monkeypatch.setattr(flow_explanation, "_build_baseline_section", _boom)

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )

    assert result.degraded_sections == ["baseline"]
    assert "RuntimeError" in result.degraded_detail["baseline"]
    # The failed section drops its DISPLAY; no guessed substitute is put in
    # its place, and `None` is not the same answer as an empty section.
    assert result.baseline is None
    assert result.purpose is not None
    assert result.responsibility is not None
    assert result.nodes is not None and len(result.nodes) == 2
    assert result.open_items is not None
    assert result.experiments is not None


def test_a_failing_purpose_chain_does_not_fail_the_projection(admin_client, monkeypatch):
    """A Purpose Chain that cannot be derived is reported on the section, not
    substituted with an empty Frame."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)

    from app import purpose_chain

    def _boom(*_args, **_kwargs):
        raise RuntimeError("purpose chain unavailable")

    monkeypatch.setattr(purpose_chain, "derive_purpose_chain", _boom)

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    assert result.purpose is not None
    assert result.purpose.purpose_chain_state == "unavailable"
    assert result.degraded_sections == []


# ---------------------------------------------------------------------------
# 6. Drill-down and the reverse aggregation (§6.5)
# ---------------------------------------------------------------------------


def test_lineage_is_reachable_in_both_directions(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    ids = _seed_runtime_flow(system_id)
    capability_ref = str(ids["capability_id"])

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )

    # Downward: Purpose -> Capability -> Flow -> Node -> evidence.
    purpose_refs = {item["ref"] for item in result.drilldown["purpose_elements"]}
    assert "core_capability:abc123" in purpose_refs
    element = next(
        item
        for item in result.drilldown["purpose_elements"]
        if item["ref"] == "core_capability:abc123"
    )
    assert capability_ref in element["capability_refs"]
    capability = next(
        item for item in result.drilldown["capabilities"] if item["ref"] == capability_ref
    )
    assert capability["node_keys"] == ["node-summarize"]
    assert result.drilldown["flow"]["subject_ref"] == FLOW_ID
    assert "node-summarize" in result.drilldown["flow"]["node_keys"]
    node_entry = next(
        item for item in result.drilldown["nodes"] if item["node_key"] == "node-summarize"
    )
    assert node_entry["evidence_ids"], "a member Node must expose its evidence ids"

    # Every evidence item carries a referencable id (§6.5).
    for node in result.nodes:
        for item in node.evidence:
            assert item.id == f"{item.kind}:{item.ref}"
            assert item.kind in flow_explanation.EVIDENCE_KINDS
    trace_ids = {
        item.ref
        for node in result.nodes
        for item in node.evidence
        if item.kind == "trace"
    }
    assert trace_ids == {"t-1", "t-2"}

    # Upward: evidence -> Node -> Flow -> Capability -> Purpose.
    entry = next(
        row for row in result.rollup if row["evidence_id"] == "trace:t-1"
    )
    assert entry["node_key"] == "node-summarize"
    assert entry["flow"] == {"subject_kind": "runtime_flow", "subject_ref": FLOW_ID}
    assert entry["capability_refs"] == [capability_ref]
    assert entry["purpose_element_refs"] == ["core_capability:abc123"]


def test_responsibility_reports_runtime_span_parentage(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    assert result.responsibility.edge_source == "runtime_span_parentage"
    assert result.responsibility.node_order == ["summarize", "charge"]
    assert result.responsibility.edges == [
        {
            "source": "summarize",
            "target": "charge",
            "edge_kind": "span_parent",
            "trace_id": "t-2",
        }
    ]
    missions = {row["node_key"]: row.get("mission") for row in result.responsibility.contracts}
    assert missions["node-summarize"] == "node-summarize の責務"


# ---------------------------------------------------------------------------
# 7. No single score anywhere
# ---------------------------------------------------------------------------


def test_no_single_score_or_percentage_in_the_response(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    ids = _seed_runtime_flow(system_id)
    _declare_monitoring(system_id, ids["node_b"])

    r = admin_client.get(
        "/flow-explanation",
        params={"subject_kind": "runtime_flow", "subject_ref": FLOW_ID},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    banned = ("score", "percent", "confidence", "completion", "health", "overall")
    for path, key, _value in _walk(body):
        lowered = key.lower()
        assert not any(word in lowered for word in banned), f"{path} looks like a score"


# ---------------------------------------------------------------------------
# 8. The projection writes nothing
# ---------------------------------------------------------------------------


def test_projection_writes_nothing(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    ids = _seed_runtime_flow(system_id)
    _declare_monitoring(system_id, ids["node_b"])
    static = _seed_static_flow(system_id)

    before = _table_counts()

    build_flow_explanation(system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID)
    build_flow_explanation(
        system_id,
        subject_kind="static_flow",
        subject_ref=static["entrypoint_id"],
        snapshot_id=static["snapshot_id"],
    )
    list_flow_subjects(system_id)

    assert _table_counts() == before

    # The HTTP boundary must not write either -- reading is never a decision.
    assert (
        admin_client.get(
            "/flow-explanation",
            params={"subject_kind": "runtime_flow", "subject_ref": FLOW_ID},
            headers=_headers(token, system_id),
        ).status_code
        == 200
    )
    assert (
        admin_client.get(
            "/flow-explanation/subjects", headers=_headers(token, system_id)
        ).status_code
        == 200
    )
    assert _table_counts() == before


# ---------------------------------------------------------------------------
# 9. Experiments section reads #415's tables defensively
# ---------------------------------------------------------------------------


def test_experiments_section_reads_proposals_without_a_lifecycle_module(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)
    now = time.time()
    with get_conn() as conn:
        proposal = conn.execute(
            """INSERT INTO flow_experiment_proposal
                   (system_id, proposal_key, flow_subject_kind, flow_subject_ref,
                    comparison_scope, title, purpose, hypothesis, baseline_ref,
                    candidate_refs_json, evaluation_axes_json, quality_floor_json,
                    isolation_strategy, cost_cap_json, stop_conditions_json,
                    rollback_plan, evidence_refs_json, created_at)
               VALUES (?, 'p-1', 'runtime_flow', ?, 'single_node', '要約の改善',
                       'purpose', 'hypothesis', 'impl:1', '["impl:2"]', '["latency"]',
                       '{}', 'pure', '{}', '["stop"]', 'rollback', '["trace:t-1"]', ?)""",
            (system_id, FLOW_ID, now),
        )
        conn.execute(
            """INSERT INTO flow_experiment_target
                   (system_id, proposal_id, target_node_key, target_role, position,
                    created_at)
               VALUES (?, ?, 'node-summarize', 'candidate_target', 0, ?)""",
            (system_id, proposal.lastrowid, now),
        )
        conn.execute(
            """INSERT INTO flow_experiment_execution_ref
                   (system_id, proposal_id, execution_kind, execution_ref, recorded_at)
               VALUES (?, ?, 'replay_variant_run', '42', ?)""",
            (system_id, proposal.lastrowid, now),
        )

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    assert result.experiments is not None
    assert len(result.experiments.proposals) == 1
    summary = result.experiments.proposals[0]
    assert summary.proposal_key == "p-1"
    assert summary.target_node_keys == ["node-summarize"]
    assert summary.execution_refs == [
        {"execution_kind": "replay_variant_run", "execution_ref": "42", "recorded_at": now}
    ]
    assert summary.evidence_refs == ["trace:t-1"]
    # #415 owns the event fold. Until it lands the status is honestly
    # `unavailable`, never a guessed lifecycle value.
    if result.experiments.status_source == "unavailable":
        assert summary.status is None
        assert summary.status_state == "unavailable"


def test_experiments_section_is_empty_not_degraded_without_proposals(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)

    result = build_flow_explanation(
        system_id, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    assert result.experiments is not None
    assert result.experiments.proposals == []
    assert "experiments" not in result.degraded_sections


# ---------------------------------------------------------------------------
# 10. Subjects endpoint + System isolation
# ---------------------------------------------------------------------------


def test_subjects_lists_both_kinds_separately(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    _seed_runtime_flow(system_id)
    static = _seed_static_flow(system_id)

    r = admin_client.get(
        "/flow-explanation/subjects", headers=_headers(token, system_id)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # A Flow that Nodes are modelled onto but which has never run IS a
    # subject: #415 has always been able to propose against it, and a
    # Dashboard that could not select or explain it made the two features
    # disagree about what exists. `other-flow` is exactly that case.
    by_ref = {item["subject_ref"]: item for item in body["runtime_flows"]}
    assert set(by_ref) == {FLOW_ID, OTHER_FLOW_ID}
    assert by_ref[FLOW_ID]["linked_node_count"] == 2

    # Observed and modelled are two independent facts, never one word: the
    # observed Flow has both, the modelled-only Flow has spans `missing` while
    # its Node membership is `present`.
    assert by_ref[FLOW_ID]["observation_state"] == "present"
    assert by_ref[FLOW_ID]["model_state"] == "present"
    assert by_ref[OTHER_FLOW_ID]["observation_state"] == "missing"
    assert by_ref[OTHER_FLOW_ID]["model_state"] == "present"
    assert [item["subject_ref"] for item in body["static_flows"]] == [
        static["entrypoint_id"]
    ]
    assert body["snapshot_id"] == static["snapshot_id"]
    assert body["snapshot_state"] == "present"


def test_another_systems_flow_is_not_visible(admin_client):
    token = _login(admin_client)
    system_a = _create_system(admin_client, token, "sys-a")
    system_b = _create_system(admin_client, token, "sys-b")
    _seed_runtime_flow(system_a)

    subjects = list_flow_subjects(system_b)
    assert subjects.runtime_flows == []

    result = build_flow_explanation(
        system_b, subject_kind="runtime_flow", subject_ref=FLOW_ID
    )
    assert result.subject.resolution == "unresolved"
    assert result.membership.node_keys == []
    assert result.nodes == []


def test_unknown_subject_kind_is_rejected(admin_client):
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    r = admin_client.get(
        "/flow-explanation",
        params={"subject_kind": "semantic_flow", "subject_ref": "x"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 422
    with pytest.raises(ValueError):
        build_flow_explanation(
            system_id, subject_kind="semantic_flow", subject_ref="x"
        )


def test_unresolved_subject_still_returns_a_projection(admin_client):
    """"This Flow has no observed spans" is an answer about the Flow, not a
    missing endpoint."""
    token = _login(admin_client)
    system_id = _create_system(admin_client, token, "sys-a")
    r = admin_client.get(
        "/flow-explanation",
        params={"subject_kind": "runtime_flow", "subject_ref": "never-seen"},
        headers=_headers(token, system_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subject"]["resolution"] == "unresolved"
    assert body["membership"]["state"] == "resolved"
    assert body["nodes"] == []
    assert body["degraded_sections"] == []


# ---------------------------------------------------------------------------
# 11. Vocabulary parity with the response models
# ---------------------------------------------------------------------------


def test_finite_vocabularies_match_the_response_models():
    from typing import get_args

    from app import models

    assert set(get_args(models.FlowSubjectKind)) == set(
        flow_explanation.FLOW_SUBJECT_KINDS
    )
    assert set(get_args(models.FlowFactState)) == set(flow_explanation.FACT_STATES)
    assert set(get_args(models.FlowMembershipState)) == set(
        flow_explanation.MEMBERSHIP_STATES
    )
    assert set(get_args(models.FlowMembershipBasis)) == set(
        flow_explanation.MEMBERSHIP_BASES
    )
    assert set(get_args(models.FlowEvidenceKind)) == set(
        flow_explanation.EVIDENCE_KINDS
    )
    assert set(get_args(models.FlowOpenItemKind)) == set(
        flow_explanation.OPEN_ITEM_KINDS
    )
    assert set(get_args(models.FlowSubjectResolution)) == set(
        flow_explanation.SUBJECT_RESOLUTIONS
    )
