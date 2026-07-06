"""Tests for Issue #151 (Phase 6): Flow Explorer runtime overlay logic."""

from types import SimpleNamespace

from app.flow_overlay import compute_overlay


def _node(node_id, component_id):
    return SimpleNamespace(node_id=node_id, component_id=component_id)


def _edge(edge_id, src, tgt):
    return SimpleNamespace(edge_id=edge_id, source_node_id=src, target_node_id=tgt)


def _graph(nodes, edges):
    return SimpleNamespace(nodes=nodes, edges=edges)


def _step(trace_id, component_id, span_id, parent_span_id, ts):
    return {
        "trace_id": trace_id, "component_id": component_id,
        "span_id": span_id, "parent_span_id": parent_span_id, "timestamp": ts,
    }


def test_observed_unobserved_and_unobservable():
    graph = _graph(
        nodes=[_node("n1", "validate"), _node("n2", "charge"),
               _node("n3", None), _node("n4", "ship")],
        edges=[_edge("e1", "n1", "n2"), _edge("e2", "n1", "n3")],
    )
    steps = [
        _step("a", "validate", "s1", None, 1.0),
        _step("b", "charge", "s2", "s1", 2.0),
    ]
    out = compute_overlay(graph, steps, {"kind": "flow"})
    by_id = {n.node_id: n for n in out.nodes}
    assert by_id["n1"].observed is True and by_id["n1"].observable is True
    assert by_id["n2"].observed is True and by_id["n2"].observation_count == 1
    assert by_id["n4"].observable is True and by_id["n4"].observed is False  # not observed
    assert by_id["n3"].observable is False  # no component_id => cannot be observed
    assert out.observed_trace_count == 2


def test_observed_static_transition():
    graph = _graph(
        nodes=[_node("n1", "validate"), _node("n2", "charge")],
        edges=[_edge("e1", "n1", "n2")],
    )
    steps = [
        _step("a", "validate", "s1", None, 1.0),
        _step("b", "charge", "s2", "s1", 2.0),  # validate -> charge
    ]
    out = compute_overlay(graph, steps, {})
    e1 = next(e for e in out.edges if e.edge_id == "e1")
    assert e1.observed_transition is True
    assert e1.source_component_id == "validate"
    assert e1.target_component_id == "charge"


def test_divergence_and_unmatched():
    graph = _graph(
        nodes=[_node("n1", "validate"), _node("n2", "charge")],
        edges=[_edge("e1", "n1", "n2")],
    )
    steps = [
        _step("a", "validate", "s1", None, 1.0),
        _step("b", "charge", "s2", "s1", 2.0),   # matches static edge
        _step("c", "refund", "s3", "s1", 3.0),   # validate -> refund: not static
    ]
    out = compute_overlay(graph, steps, {})
    # validate -> refund is a runtime transition absent from the static graph.
    assert any(
        d.source_component_id == "validate" and d.target_component_id == "refund"
        for d in out.divergences
    )
    # validate -> charge is NOT a divergence (it matches e1).
    assert not any(d.target_component_id == "charge" for d in out.divergences)
    # refund is observed but not in the static graph.
    assert "refund" in out.unmatched_component_ids
    assert "charge" not in out.unmatched_component_ids


def test_no_lineage_leaves_nothing_observed():
    graph = _graph(nodes=[_node("n1", "validate")], edges=[])
    out = compute_overlay(graph, [], {})
    assert out.nodes[0].observed is False
    assert out.observed_component_ids == []
    assert out.divergences == []


# --- lineage resolver (the isolation-sensitive part of the endpoint) --------

import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-overlay.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


def _legacy_system_id():
    from app.db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM systems WHERE name = 'Legacy System' AND owner_user_id IS NULL"
        ).fetchone()
    return row["id"]


def _resolve(kind, **sel):
    from app.db import get_conn
    from app.models import FlowOverlaySelection
    from app.routes.project_intelligence import _resolve_lineage_steps
    sid = _legacy_system_id()
    with get_conn() as conn:
        return _resolve_lineage_steps(conn, sid, FlowOverlaySelection(kind=kind, **sel))


def test_resolver_flow_and_entity(client):
    client.post("/traces", json={
        "trace_id": "p", "component_id": "validate", "mode": "trace",
        "timestamp": 1.0, "flow_id": "f1", "span_id": "s1",
        "entities": [{"type": "order", "id": "o-1"}]})
    client.post("/traces", json={
        "trace_id": "c", "component_id": "charge", "mode": "trace",
        "timestamp": 2.0, "flow_id": "f1", "span_id": "s2", "parent_span_id": "s1",
        "entities": [{"type": "order", "id": "o-1"}]})

    flow_steps = _resolve("flow", flow_id="f1")
    assert [s["component_id"] for s in flow_steps] == ["validate", "charge"]
    assert flow_steps[1]["parent_span_id"] == "s1"

    ent_steps = _resolve("entity", entity_type="order", entity_id="o-1")
    assert {s["component_id"] for s in ent_steps} == {"validate", "charge"}


def test_resolver_analyzer_entity_filter(client):
    client.post("/traces", json={
        "trace_id": "p", "component_id": "validate", "mode": "trace",
        "timestamp": 1.0, "span_id": "s1",
        "entities": [{"type": "order", "id": "o-1"}]})
    # analyzer with an entity filter drives the overlay by that entity
    a = client.post("/trace-analyzers", json={"name": "x", "spec": {
        "source": "trace_projections",
        "filter": {"entity": {"type": "order", "id": "o-1"}},
        "select": [{"name": "t", "path": "$.trace_id"}]}}).json()
    steps = _resolve("analyzer", analyzer_id=a["id"])
    assert [s["component_id"] for s in steps] == ["validate"]


def test_resolver_analyzer_without_entity_filter_422(client):
    from fastapi import HTTPException
    a = client.post("/trace-analyzers", json={"name": "x", "spec": {
        "source": "trace_projections",
        "select": [{"name": "t", "path": "$.trace_id"}]}}).json()
    with pytest.raises(HTTPException) as exc:
        _resolve("analyzer", analyzer_id=a["id"])
    assert exc.value.status_code == 422
