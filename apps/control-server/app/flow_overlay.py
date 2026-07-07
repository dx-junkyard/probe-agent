"""Deterministic runtime-lineage overlay for the Flow Explorer (Issue #151).

Matches a static flow graph against resolved lineage steps by component_id
(exact equality only — a finite structural check, Principle 6) and reports
observed / unobserved nodes, observed static transitions, and runtime
transitions absent from the static graph (divergences). No interpretation.
"""

from typing import Any, Dict, List

from .models import (
    FlowDivergence,
    FlowOverlayEdge,
    FlowOverlayNode,
    FlowOverlayOut,
)


def compute_overlay(graph, steps: List[Dict[str, Any]], selection: Dict[str, Any]) -> FlowOverlayOut:
    """``steps`` are dicts with trace_id / component_id / span_id /
    parent_span_id / timestamp, ordered by the caller."""
    observed: Dict[str, Dict[str, Any]] = {}
    span_to_comp: Dict[str, str] = {}
    for s in steps:
        cid = s.get("component_id")
        if s.get("span_id"):
            span_to_comp[s["span_id"]] = cid
        if cid:
            agg = observed.setdefault(cid, {"count": 0, "last": None})
            agg["count"] += 1
            ts = s.get("timestamp")
            if ts is not None and (agg["last"] is None or ts > agg["last"]):
                agg["last"] = ts

    runtime_transitions: Dict[tuple, int] = {}
    for s in steps:
        parent_span = s.get("parent_span_id")
        parent = span_to_comp.get(parent_span) if parent_span else None
        if parent and s.get("component_id"):
            key = (parent, s["component_id"])
            runtime_transitions[key] = runtime_transitions.get(key, 0) + 1

    node_comp = {n.node_id: n.component_id for n in graph.nodes}
    static_edge_comps = set()
    out_edges: List[FlowOverlayEdge] = []
    for e in graph.edges:
        src_comp = node_comp.get(e.source_node_id)
        tgt_comp = node_comp.get(e.target_node_id) if e.target_node_id else None
        if src_comp and tgt_comp:
            static_edge_comps.add((src_comp, tgt_comp))
        out_edges.append(FlowOverlayEdge(
            edge_id=e.edge_id,
            source_node_id=e.source_node_id,
            target_node_id=e.target_node_id,
            source_component_id=src_comp,
            target_component_id=tgt_comp,
            observed_transition=bool(
                src_comp and tgt_comp and (src_comp, tgt_comp) in runtime_transitions
            ),
        ))

    out_nodes = [
        FlowOverlayNode(
            node_id=n.node_id,
            component_id=n.component_id,
            observable=bool(n.component_id),
            observed=bool(n.component_id and n.component_id in observed),
            observation_count=observed.get(n.component_id, {}).get("count", 0) if n.component_id else 0,
            last_observed_at=observed.get(n.component_id, {}).get("last") if n.component_id else None,
        )
        for n in graph.nodes
    ]

    divergences = [
        FlowDivergence(source_component_id=src, target_component_id=tgt, count=cnt)
        for (src, tgt), cnt in sorted(runtime_transitions.items())
        if (src, tgt) not in static_edge_comps
    ]

    graph_comp_ids = {c for c in node_comp.values() if c}
    unmatched = sorted(c for c in observed if c not in graph_comp_ids)

    return FlowOverlayOut(
        selection=selection,
        nodes=out_nodes,
        edges=out_edges,
        divergences=divergences,
        observed_component_ids=sorted(observed.keys()),
        unmatched_component_ids=unmatched,
        observed_trace_count=len(steps),
    )
