"""Unified System Understanding service.

Aggregates existing intelligence components (snapshot, documentation index,
claim scanner, symbol index, entrypoint discovery, docs-code reconciler,
capability hierarchy) into a single read or build response.

probe-agent:
  role: Unified system understanding orchestrator
  capability: repository-understanding
  element_type: core
  consumers: [dashboard, control-server]
  operation_kind: orchestration
  state_effects: [database-read, database-write]
  probe_value: Verify that pipeline status, gaps, metadata coverage, and next actions are consistent across GET and POST endpoints.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .db import get_conn


# Pipeline step names (from docs/system-understanding-navigation.md)
PIPELINE_STEPS = [
    "repository_configured",
    "snapshot_ready",
    "documentation_indexed",
    "documentation_claims_scanned",
    "symbols_indexed",
    "entrypoints_discovered",
    "docs_code_reconciled",
    "capability_hierarchy_ready",
]


@dataclass
class PipelineStep:
    step: str
    status: str  # complete, missing, warning, blocked, failed
    detail: Optional[str] = None


@dataclass
class NextAction:
    action: str
    reason: str
    link: Optional[str] = None


@dataclass
class GapSummary:
    gap_type: str
    count: int


@dataclass
class MetadataCoverage:
    symbol_count: int = 0
    symbols_with_source_metadata: int = 0
    entrypoint_count: int = 0
    entrypoints_with_capability_link: int = 0


@dataclass
class SystemUnderstandingSummary:
    system_id: int
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    pipeline: List[PipelineStep] = field(default_factory=list)
    purpose: Optional[Dict[str, Any]] = None
    capabilities: List[Dict[str, Any]] = field(default_factory=list)
    entrypoints: List[Dict[str, Any]] = field(default_factory=list)
    major_symbols: List[Dict[str, Any]] = field(default_factory=list)
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    gap_summary: List[GapSummary] = field(default_factory=list)
    metadata_coverage: Optional[MetadataCoverage] = None
    next_actions: List[NextAction] = field(default_factory=list)


def _check_repository_configured(conn, system_id: int) -> PipelineStep:
    row = conn.execute(
        "SELECT 1 FROM repository_configs WHERE system_id = ?", (system_id,)
    ).fetchone()
    if row:
        return PipelineStep("repository_configured", "complete")
    return PipelineStep("repository_configured", "missing")


def _get_latest_ready_snapshot(conn, system_id: int):
    return conn.execute(
        "SELECT * FROM repository_snapshots WHERE system_id = ? AND status = 'ready' ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()


def _check_snapshot_ready(conn, system_id: int, snapshot_row) -> PipelineStep:
    if snapshot_row:
        return PipelineStep("snapshot_ready", "complete")
    return PipelineStep("snapshot_ready", "missing")


def _is_reasoning_model_available() -> bool:
    """Check whether a non-mock reasoning model is configured."""
    import os
    provider = (os.getenv("INTELLIGENCE_LLM_PROVIDER") or os.getenv("LLM_PROVIDER", "openai")).strip().lower()
    if provider == "mock":
        return False
    from .llm import is_reasoning_model
    model = os.getenv("INTELLIGENCE_LLM_MODEL") or os.getenv("LLM_MODEL")
    if not model:
        defaults = {"openai": "gpt-4o-mini", "anthropic": "claude-3-5-haiku-latest", "gemini": "gemini-1.5-flash"}
        model = defaults.get(provider, "gpt-4o-mini")
    return is_reasoning_model(provider, model)


def _check_documentation_indexed(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("documentation_indexed", "missing")
    row = conn.execute(
        "SELECT id, status FROM intelligence_runs WHERE system_id = ? AND run_type IN ('draft_generation', 'repository_drafts') AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row:
        if row["status"] == "completed":
            return PipelineStep("documentation_indexed", "complete")
        return PipelineStep("documentation_indexed", "failed", detail=f"run status: {row['status']}")
    if not _is_reasoning_model_available():
        return PipelineStep("documentation_indexed", "blocked", detail="Reasoning model not configured")
    return PipelineStep("documentation_indexed", "missing")


def _check_documentation_claims_scanned(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("documentation_claims_scanned", "missing")
    row = conn.execute(
        "SELECT id FROM understanding_graph_snapshots WHERE system_id = ? AND snapshot_id = ? LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row:
        return PipelineStep("documentation_claims_scanned", "complete")
    if not _is_reasoning_model_available():
        return PipelineStep("documentation_claims_scanned", "blocked", detail="Reasoning model not configured")
    return PipelineStep("documentation_claims_scanned", "missing")


def _check_symbols_indexed(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("symbols_indexed", "missing")
    row = conn.execute(
        "SELECT id, status FROM intelligence_runs WHERE system_id = ? AND run_type = 'symbol_index' AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row:
        if row["status"] == "completed":
            return PipelineStep("symbols_indexed", "complete")
        return PipelineStep("symbols_indexed", "failed", detail=f"run status: {row['status']}")
    return PipelineStep("symbols_indexed", "missing")


def _check_entrypoints_discovered(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("entrypoints_discovered", "missing")
    row = conn.execute(
        "SELECT id FROM code_entrypoints WHERE system_id = ? AND snapshot_id = ? LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row:
        return PipelineStep("entrypoints_discovered", "complete")
    return PipelineStep("entrypoints_discovered", "missing")


def _check_docs_code_reconciled(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("docs_code_reconciled", "missing")
    graph_row = conn.execute(
        "SELECT id FROM understanding_graph_snapshots WHERE system_id = ? AND snapshot_id = ? LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    sym_row = conn.execute(
        "SELECT id FROM code_symbols WHERE system_id = ? AND snapshot_id = ? LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if graph_row and sym_row:
        return PipelineStep("docs_code_reconciled", "complete")
    if graph_row or sym_row:
        return PipelineStep("docs_code_reconciled", "warning", detail="Partial data available")
    return PipelineStep("docs_code_reconciled", "missing")


def _check_capability_hierarchy_ready(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("capability_hierarchy_ready", "missing")
    row = conn.execute(
        "SELECT id, status FROM intelligence_runs WHERE system_id = ? AND run_type = 'capability_hierarchy' AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row:
        if row["status"] == "completed":
            return PipelineStep("capability_hierarchy_ready", "complete")
        if row["status"] == "failed":
            return PipelineStep("capability_hierarchy_ready", "failed")
        return PipelineStep("capability_hierarchy_ready", "warning", detail=f"status: {row['status']}")
    if not _is_reasoning_model_available():
        return PipelineStep("capability_hierarchy_ready", "blocked", detail="Reasoning model not configured")
    return PipelineStep("capability_hierarchy_ready", "missing")


def _build_pipeline(conn, system_id: int, snapshot_row) -> List[PipelineStep]:
    snapshot_id = snapshot_row["id"] if snapshot_row else None
    return [
        _check_repository_configured(conn, system_id),
        _check_snapshot_ready(conn, system_id, snapshot_row),
        _check_documentation_indexed(conn, system_id, snapshot_id),
        _check_documentation_claims_scanned(conn, system_id, snapshot_id),
        _check_symbols_indexed(conn, system_id, snapshot_id),
        _check_entrypoints_discovered(conn, system_id, snapshot_id),
        _check_docs_code_reconciled(conn, system_id, snapshot_id),
        _check_capability_hierarchy_ready(conn, system_id, snapshot_id),
    ]


def _load_purpose(conn, system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    """Load system purpose from hierarchy or drafts."""
    node = conn.execute(
        "SELECT * FROM capability_hierarchy_nodes WHERE system_id = ? AND snapshot_id = ? AND node_type = 'purpose' LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if node:
        return {
            "name": node["name"],
            "summary": node["summary"],
            "provenance_kind": node["provenance_kind"],
        }
    draft = conn.execute(
        "SELECT * FROM system_profile_drafts WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if draft:
        return {
            "name": draft["name"],
            "summary": draft["purpose"],
            "provenance_kind": "structural",
        }
    return None


def _load_capabilities(conn, system_id: int, snapshot_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM capability_hierarchy_nodes WHERE system_id = ? AND snapshot_id = ? AND node_type = 'capability' ORDER BY id",
        (system_id, snapshot_id),
    ).fetchall()
    return [
        {"name": r["name"], "summary": r["summary"], "provenance_kind": r["provenance_kind"]}
        for r in rows
    ]


def _load_entrypoint_summaries(conn, system_id: int, snapshot_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT entrypoint_type, entrypoint_id, category, label FROM code_entrypoints WHERE system_id = ? AND snapshot_id = ? ORDER BY id LIMIT 50",
        (system_id, snapshot_id),
    ).fetchall()
    return [
        {
            "entrypoint_type": r["entrypoint_type"],
            "entrypoint_id": r["entrypoint_id"],
            "category": r["category"],
            "label": r["label"],
        }
        for r in rows
    ]


def _load_major_symbols(conn, system_id: int, snapshot_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT path, qualified_name, kind, route_path, route_method, component_id
           FROM code_symbols
           WHERE system_id = ? AND snapshot_id = ? AND kind IN ('function', 'async_function', 'class')
           ORDER BY id LIMIT 50""",
        (system_id, snapshot_id),
    ).fetchall()
    return [
        {
            "path": r["path"],
            "qualified_name": r["qualified_name"],
            "kind": r["kind"],
            "route_path": r["route_path"],
            "route_method": r["route_method"],
            "component_id": r["component_id"],
        }
        for r in rows
    ]


def _load_metadata_coverage(conn, system_id: int, snapshot_id: int) -> MetadataCoverage:
    sym_count = conn.execute(
        "SELECT COUNT(*) FROM code_symbols WHERE system_id = ? AND snapshot_id = ?",
        (system_id, snapshot_id),
    ).fetchone()[0]

    meta_count = conn.execute(
        "SELECT COUNT(DISTINCT ssm.symbol_id) FROM symbol_source_metadata ssm JOIN code_symbols cs ON ssm.symbol_id = cs.id WHERE cs.system_id = ? AND cs.snapshot_id = ?",
        (system_id, snapshot_id),
    ).fetchone()[0]

    ep_count = conn.execute(
        "SELECT COUNT(*) FROM code_entrypoints WHERE system_id = ? AND snapshot_id = ?",
        (system_id, snapshot_id),
    ).fetchone()[0]

    ep_classified = conn.execute(
        """SELECT COUNT(DISTINCT ce.id)
           FROM code_entrypoints ce
           JOIN capability_hierarchy_nodes chn ON chn.system_id = ce.system_id AND chn.snapshot_id = ce.snapshot_id
           WHERE ce.system_id = ? AND ce.snapshot_id = ?
           AND chn.node_type IN ('element', 'supporting')
           AND chn.entrypoint_id = ce.id""",
        (system_id, snapshot_id),
    ).fetchone()[0]

    return MetadataCoverage(
        symbol_count=sym_count,
        symbols_with_source_metadata=meta_count,
        entrypoint_count=ep_count,
        entrypoints_with_capability_link=ep_classified,
    )


def _load_gaps_from_reconciler(conn, system_id: int, snapshot_id: int) -> List[Dict[str, Any]]:
    """Load docs-code gaps by running the reconciler if a graph exists."""
    import json as _json
    from .understanding_graph import UnderstandingGraph, GraphNode, EvidenceRef
    from .docs_code_reconciler import reconcile

    graph_row = conn.execute(
        "SELECT * FROM understanding_graph_snapshots WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if graph_row is None:
        return _detect_extra_gaps(conn, system_id, snapshot_id)

    graph_data = _json.loads(graph_row["graph_json"])
    nodes = {}
    for nid, nd in graph_data.get("nodes", {}).items():
        evidence = [
            EvidenceRef(
                path=e.get("path", ""),
                start_line=e.get("start_line", 0),
                end_line=e.get("end_line", 0),
                chunk_id=e.get("chunk_id", ""),
                confidence=e.get("confidence", 0.0),
                summary=e.get("summary", ""),
            )
            for e in nd.get("evidence", [])
        ]
        nodes[nid] = GraphNode(
            node_id=nid,
            node_type=nd.get("node_type", ""),
            name=nd.get("name", ""),
            summary=nd.get("summary", ""),
            evidence=evidence,
            confidence=nd.get("confidence", 0.0),
            mentioned_apis=nd.get("mentioned_apis", []),
            mentioned_symbols=nd.get("mentioned_symbols", []),
        )

    graph = UnderstandingGraph(
        nodes=nodes,
        claim_count=graph_data.get("claim_count", 0),
        valid_claim_count=graph_data.get("valid_claim_count", 0),
        confidence_summary=graph_data.get("confidence_summary", {}),
        conflicts=graph_data.get("conflicts", []),
        weak_nodes=graph_data.get("weak_nodes", []),
        source_hash=graph_data.get("source_hash", ""),
    )
    result = reconcile(conn, system_id, snapshot_id, graph)
    gaps = []
    for g in result.gaps:
        gap_dict: Dict[str, Any] = {
            "gap_type": g.gap_type,
            "severity": _gap_severity(g.gap_type),
            "title": _gap_title(g.gap_type, g.node_name),
            "node_name": g.node_name,
            "notes": g.notes,
            "capability_key": None,
            "doc_refs": [],
            "symbol_refs": [],
            "entrypoint_refs": [],
            "code_refs": [],
            "next_actions": _gap_next_actions(g.gap_type),
        }
        if g.node_id and g.node_id in graph.nodes:
            node = graph.nodes[g.node_id]
            for parent_id, parent_node in graph.nodes.items():
                if parent_node.node_type == "capability":
                    gap_dict["capability_key"] = parent_node.name
                    break
        if g.doc_evidence:
            gap_dict["doc_refs"] = [
                {"path": de.path, "start_line": de.start_line, "end_line": de.end_line}
                for de in g.doc_evidence
                if de.path
            ]
        if g.code_evidence:
            gap_dict["code_refs"] = [
                {"source": ce.source, "path": ce.path, "qualified_name": ce.qualified_name}
                for ce in g.code_evidence
            ]
            gap_dict["symbol_refs"] = [
                {"path": ce.path, "qualified_name": ce.qualified_name}
                for ce in g.code_evidence
                if ce.qualified_name
            ]
            gap_dict["entrypoint_refs"] = [
                {"entrypoint_type": "api", "entrypoint_ref": f"{ce.route_method} {ce.route_path}"}
                for ce in g.code_evidence
                if ce.route_method and ce.route_path
            ]
        gaps.append(gap_dict)

    gaps.extend(_detect_extra_gaps(conn, system_id, snapshot_id))
    return gaps


GAP_SEVERITY: Dict[str, str] = {
    "docs_only": "warning",
    "code_only": "info",
    "source_doc_mismatch": "warning",
    "stale_explanation": "warning",
    "unclassified_entrypoint": "info",
    "missing_probe_flow": "info",
    "missing_evidence": "info",
    "ambiguous_ownership": "warning",
}

GAP_TITLE_TEMPLATES: Dict[str, str] = {
    "docs_only": "Documented but no matching implementation found: {name}",
    "code_only": "Implemented but not documented: {name}",
    "source_doc_mismatch": "Source metadata and docs disagree: {name}",
    "stale_explanation": "Explanation may be outdated: {name}",
    "unclassified_entrypoint": "Entrypoint not classified in capability hierarchy: {name}",
    "missing_probe_flow": "No probe flow defined: {name}",
    "missing_evidence": "Documentation claim lacks path/line evidence: {name}",
    "ambiguous_ownership": "Ambiguous ownership: {name}",
}

GAP_NEXT_ACTIONS: Dict[str, List[Dict[str, Optional[str]]]] = {
    "docs_only": [
        {"action": "Open docs evidence", "link": None},
        {"action": "Create implementation issue", "link": None},
    ],
    "code_only": [
        {"action": "Open source symbol", "link": "/repository"},
        {"action": "Add docs or source metadata", "link": "/interview"},
    ],
    "source_doc_mismatch": [
        {"action": "Propose explanation refresh", "link": "/capability-map"},
    ],
    "stale_explanation": [
        {"action": "Propose explanation refresh", "link": "/capability-map"},
    ],
    "unclassified_entrypoint": [
        {"action": "Open Interview", "link": "/interview"},
        {"action": "Add source metadata", "link": "/interview"},
    ],
    "missing_probe_flow": [
        {"action": "Open Flow Explorer", "link": "/flow-explorer"},
        {"action": "Create Probe Plan", "link": "/probe-planner"},
    ],
    "missing_evidence": [
        {"action": "Improve documentation index", "link": "/repository"},
    ],
    "ambiguous_ownership": [
        {"action": "Clarify ownership in Interview", "link": "/interview"},
    ],
}


def _gap_severity(gap_type: Optional[str]) -> str:
    return GAP_SEVERITY.get(gap_type or "", "info")


def _gap_title(gap_type: Optional[str], node_name: Optional[str]) -> str:
    template = GAP_TITLE_TEMPLATES.get(gap_type or "", "Gap: {name}")
    return template.format(name=node_name or "unknown")


def _gap_next_actions(gap_type: Optional[str]) -> List[Dict[str, Optional[str]]]:
    return list(GAP_NEXT_ACTIONS.get(gap_type or "", []))


def _detect_extra_gaps(conn, system_id: int, snapshot_id: int) -> List[Dict[str, Any]]:
    """Detect additional gaps not covered by the reconciler (e.g. unclassified entrypoints, missing probe flows)."""
    extra: List[Dict[str, Any]] = []

    unclassified = conn.execute(
        """SELECT ce.entrypoint_type, ce.entrypoint_id, ce.handler_path, ce.handler_qualified_name
           FROM code_entrypoints ce
           WHERE ce.system_id = ? AND ce.snapshot_id = ?
           AND NOT EXISTS (
               SELECT 1 FROM capability_hierarchy_nodes chn
               WHERE chn.system_id = ce.system_id AND chn.snapshot_id = ce.snapshot_id
               AND chn.entrypoint_id = ce.id
           )""",
        (system_id, snapshot_id),
    ).fetchall()
    for uc in unclassified:
        ep_label = uc["entrypoint_id"] or uc["handler_qualified_name"] or "unknown"
        extra.append({
            "gap_type": "unclassified_entrypoint",
            "severity": "info",
            "title": _gap_title("unclassified_entrypoint", ep_label),
            "node_name": ep_label,
            "notes": f"Entrypoint {uc['entrypoint_type']}:{uc['entrypoint_id']} has no capability classification",
            "capability_key": None,
            "doc_refs": [],
            "symbol_refs": [{"path": uc["handler_path"], "qualified_name": uc["handler_qualified_name"]}] if uc["handler_qualified_name"] else [],
            "entrypoint_refs": [{"entrypoint_type": uc["entrypoint_type"], "entrypoint_ref": uc["entrypoint_id"]}],
            "code_refs": [],
            "next_actions": _gap_next_actions("unclassified_entrypoint"),
        })

    # missing_probe_flow: classified entrypoints with no probe plan
    classified_eps = conn.execute(
        """SELECT ce.entrypoint_type, ce.entrypoint_id, ce.handler_path, ce.handler_qualified_name,
                  chn.capability_key
           FROM code_entrypoints ce
           JOIN capability_hierarchy_nodes chn
               ON chn.system_id = ce.system_id AND chn.snapshot_id = ce.snapshot_id
               AND chn.entrypoint_id = ce.id
           WHERE ce.system_id = ? AND ce.snapshot_id = ?
           AND NOT EXISTS (
               SELECT 1 FROM probe_plans pp
               WHERE pp.system_id = ce.system_id AND pp.snapshot_id = ce.snapshot_id
           )""",
        (system_id, snapshot_id),
    ).fetchall()
    for ep in classified_eps:
        ep_label = ep["entrypoint_id"] or ep["handler_qualified_name"] or "unknown"
        extra.append({
            "gap_type": "missing_probe_flow",
            "severity": _gap_severity("missing_probe_flow"),
            "title": _gap_title("missing_probe_flow", ep_label),
            "node_name": ep_label,
            "notes": f"Entrypoint {ep['entrypoint_type']}:{ep['entrypoint_id']} is classified but has no probe plan",
            "capability_key": ep["capability_key"],
            "doc_refs": [],
            "symbol_refs": [{"path": ep["handler_path"], "qualified_name": ep["handler_qualified_name"]}] if ep["handler_qualified_name"] else [],
            "entrypoint_refs": [{"entrypoint_type": ep["entrypoint_type"], "entrypoint_ref": ep["entrypoint_id"]}],
            "code_refs": [],
            "next_actions": _gap_next_actions("missing_probe_flow"),
        })

    # missing_evidence: understanding graph nodes whose evidence list is empty
    graph_snapshot = conn.execute(
        "SELECT graph_json FROM understanding_graph_snapshots WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if graph_snapshot:
        try:
            graph_data = json.loads(graph_snapshot["graph_json"])
            nodes = graph_data.get("nodes", {})
            for node_id, node in nodes.items():
                node_type = node.get("node_type", "")
                if node_type == "conflict":
                    continue
                evidence = node.get("evidence", [])
                if not evidence:
                    node_name = node.get("name", node_id)
                    extra.append({
                        "gap_type": "missing_evidence",
                        "severity": _gap_severity("missing_evidence"),
                        "title": _gap_title("missing_evidence", node_name),
                        "node_name": node_name,
                        "notes": f"Documentation claim '{node_name}' has no file/line evidence",
                        "capability_key": None,
                        "doc_refs": [],
                        "symbol_refs": [],
                        "entrypoint_refs": [],
                        "code_refs": [],
                        "next_actions": _gap_next_actions("missing_evidence"),
                    })
        except (json.JSONDecodeError, TypeError):
            pass

    return extra


def _compute_gap_summary(gaps: List[Dict[str, Any]]) -> List[GapSummary]:
    counts: Dict[str, int] = {}
    for g in gaps:
        gt = g.get("gap_type", "unknown")
        counts[gt] = counts.get(gt, 0) + 1
    return [GapSummary(gap_type=k, count=v) for k, v in sorted(counts.items())]


def _build_next_actions(pipeline: List[PipelineStep], metadata_coverage: Optional[MetadataCoverage], gap_count: int) -> List[NextAction]:
    actions: List[NextAction] = []
    step_map = {s.step: s.status for s in pipeline}

    if step_map.get("repository_configured") != "complete":
        actions.append(NextAction(
            action="Configure repository",
            reason="Repository is not configured yet",
            link="/repository",
        ))
        return actions

    if step_map.get("snapshot_ready") != "complete":
        actions.append(NextAction(
            action="Create snapshot",
            reason="No ready snapshot available",
            link="/repository",
        ))
        return actions

    if step_map.get("symbols_indexed") != "complete":
        actions.append(NextAction(
            action="Index code symbols",
            reason="Code symbols have not been indexed",
            link="/repository",
        ))

    if step_map.get("documentation_indexed") != "complete":
        actions.append(NextAction(
            action="Generate drafts",
            reason="Documentation has not been indexed into drafts",
            link="/repository",
        ))

    if step_map.get("entrypoints_discovered") != "complete":
        actions.append(NextAction(
            action="Discover entrypoints",
            reason="API/CLI/queue entrypoints have not been discovered",
            link="/flow-explorer",
        ))

    if step_map.get("capability_hierarchy_ready") != "complete":
        actions.append(NextAction(
            action="Generate capability hierarchy",
            reason="Capability hierarchy has not been generated",
            link="/capability-map",
        ))

    if metadata_coverage and metadata_coverage.symbol_count > 0:
        ratio = metadata_coverage.symbols_with_source_metadata / metadata_coverage.symbol_count
        if ratio < 0.1:
            actions.append(NextAction(
                action="Add source metadata",
                reason=f"Only {metadata_coverage.symbols_with_source_metadata} of {metadata_coverage.symbol_count} symbols have probe-agent metadata",
                link="/interview",
            ))

    if gap_count > 0:
        actions.append(NextAction(
            action="Review docs-code gaps",
            reason=f"{gap_count} docs-code gaps found",
            link="/system-understanding",
        ))

    if step_map.get("docs_code_reconciled") == "complete" and gap_count == 0 and not actions:
        pass

    return actions


def get_system_understanding(system_id: int) -> SystemUnderstandingSummary:
    """Read-only: aggregate persisted state into a system understanding summary."""
    with get_conn() as conn:
        snapshot_row = _get_latest_ready_snapshot(conn, system_id)
        pipeline = _build_pipeline(conn, system_id, snapshot_row)

        summary = SystemUnderstandingSummary(
            system_id=system_id,
            pipeline=pipeline,
        )

        if snapshot_row:
            snapshot_id = snapshot_row["id"]
            summary.snapshot_id = snapshot_id
            summary.commit_sha = snapshot_row["commit_sha"]

            summary.purpose = _load_purpose(conn, system_id, snapshot_id)
            summary.capabilities = _load_capabilities(conn, system_id, snapshot_id)
            summary.entrypoints = _load_entrypoint_summaries(conn, system_id, snapshot_id)
            summary.major_symbols = _load_major_symbols(conn, system_id, snapshot_id)
            summary.metadata_coverage = _load_metadata_coverage(conn, system_id, snapshot_id)
            summary.gaps = _load_gaps_from_reconciler(conn, system_id, snapshot_id)
            summary.gap_summary = _compute_gap_summary(summary.gaps)

        summary.next_actions = _build_next_actions(
            pipeline,
            summary.metadata_coverage,
            len(summary.gaps),
        )
        return summary


def build_system_understanding(system_id: int) -> SystemUnderstandingSummary:
    """Execute or re-use existing steps to build a system understanding.

    Runs deterministic steps (snapshot check, symbol index, entrypoint discovery)
    where possible. Steps requiring a reasoning model are marked as blocked
    if no reasoning model is configured.
    """
    from .code_indexer import index_snapshot_files
    from .llm import LLMConfig, create_llm_client, get_llm_client, is_reasoning_model, LLMError

    with get_conn() as conn:
        snapshot_row = _get_latest_ready_snapshot(conn, system_id)
        if not snapshot_row:
            return get_system_understanding(system_id)

        snapshot_id = snapshot_row["id"]
        commit_sha = snapshot_row["commit_sha"]

        # Step: symbols_indexed - deterministic, can be auto-run
        sym_run = conn.execute(
            "SELECT id FROM intelligence_runs WHERE system_id = ? AND run_type = 'symbol_index' AND snapshot_id = ? AND status = 'completed' LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()
        if not sym_run:
            file_rows = conn.execute(
                "SELECT path, content, content_hash FROM snapshot_files WHERE snapshot_id = ? AND inclusion_status = 'indexed' ORDER BY path",
                (snapshot_id,),
            ).fetchall()
            if file_rows:
                try:
                    files = [(fr["path"], bytes(fr["content"] or b"")) for fr in file_rows]
                    result = index_snapshot_files(files)
                    now = time.time()
                    run_id = conn.execute(
                        """INSERT INTO intelligence_runs
                            (system_id, snapshot_id, run_type, provider, model,
                             prompt_version, schema_version, decision_method,
                             status, is_mock, started_at, completed_at)
                        VALUES (?, ?, 'symbol_index', 'deterministic', 'n/a',
                                'n/a', 'provenance-v1', 'deterministic',
                                'completed', 0, ?, ?)""",
                        (system_id, snapshot_id, now, now),
                    ).lastrowid
                    for sym in result.symbols:
                        conn.execute(
                            """INSERT OR IGNORE INTO code_symbols
                                (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line,
                                 docstring, decorators, imports, is_test, route_path, route_method,
                                 component_id, symbol_source_hash, symbol_body_hash)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                snapshot_id, system_id, sym.path, sym.qualified_name,
                                sym.kind, sym.start_line, sym.end_line,
                                sym.docstring, json.dumps(sym.decorators), json.dumps(sym.imports),
                                1 if sym.is_test else 0, sym.route_path, sym.route_method,
                                sym.component_id,
                                sym.symbol_source_hash, sym.symbol_body_hash,
                            ),
                        )
                except Exception as _exc:
                    import logging
                    logging.getLogger(__name__).warning("Symbol index in build failed: %s", _exc, exc_info=True)

        # Step: entrypoints_discovered - deterministic, can be auto-run
        ep_run = conn.execute(
            "SELECT id FROM intelligence_runs WHERE system_id = ? AND run_type = 'entrypoint_index' AND snapshot_id = ? LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()
        if not ep_run:
            symbols = conn.execute(
                "SELECT * FROM code_symbols WHERE system_id = ? AND snapshot_id = ?",
                (system_id, snapshot_id),
            ).fetchall()
            if symbols:
                try:
                    from .entrypoint_discovery import discover_entrypoints
                    from .flow_graph import SymbolRecord
                    file_rows_ep = conn.execute(
                        "SELECT path, content FROM snapshot_files WHERE snapshot_id = ? AND inclusion_status = 'indexed' ORDER BY path",
                        (snapshot_id,),
                    ).fetchall()
                    ep_files = [(fr["path"], (bytes(fr["content"] or b"")).decode("utf-8", errors="replace")) for fr in file_rows_ep]
                    sym_records = [
                        SymbolRecord(
                            symbol_id=s["id"],
                            path=s["path"],
                            qualified_name=s["qualified_name"],
                            kind=s["kind"],
                            start_line=s["start_line"],
                            end_line=s["end_line"],
                            decorators=json.loads(s["decorators"]) if isinstance(s["decorators"], str) else (s["decorators"] or []),
                            component_id=s["component_id"],
                            route_path=s["route_path"],
                            route_method=s["route_method"],
                            docstring=s["docstring"],
                            is_test=bool(s["is_test"]),
                        )
                        for s in symbols
                    ]
                    discovery = discover_entrypoints(sym_records, ep_files)
                    now = time.time()
                    run_id = conn.execute(
                        """INSERT INTO intelligence_runs
                            (system_id, snapshot_id, run_type, provider, model,
                             prompt_version, schema_version, decision_method,
                             status, is_mock, started_at, completed_at)
                        VALUES (?, ?, 'entrypoint_index', 'deterministic', 'n/a',
                                'n/a', 'provenance-v1', 'deterministic',
                                'completed', 0, ?, ?)""",
                        (system_id, snapshot_id, now, now),
                    ).lastrowid
                    for ep in discovery.entrypoints + discovery.functions:
                        sym_row = next(
                            (s for s in symbols if s["qualified_name"] == ep.qualified_name),
                            None,
                        )
                        handler_symbol_id = sym_row["id"] if sym_row else None
                        conn.execute(
                            """INSERT OR IGNORE INTO code_entrypoints
                                (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                                 handler_symbol_id, handler_qualified_name, handler_path,
                                 route_method, route_path, framework, operation, confidence,
                                 line_start, line_end, source, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deterministic', ?)""",
                            (
                                system_id, snapshot_id, ep.entrypoint_type, ep.entrypoint_id,
                                ep.category, ep.label,
                                handler_symbol_id, ep.qualified_name, ep.path,
                                ep.route_method, ep.route_path,
                                ep.framework, ep.operation, ep.confidence,
                                ep.line_start, ep.line_end,
                                now,
                            ),
                        )
                except Exception as _ep_exc:
                    import logging
                    logging.getLogger(__name__).warning("Entrypoint discovery in build failed: %s", _ep_exc, exc_info=True)

        # Step: documentation pipeline (requires reasoning model)
        doc_indexed = conn.execute(
            "SELECT id FROM intelligence_runs WHERE system_id = ? AND run_type IN ('draft_generation', 'repository_drafts') AND snapshot_id = ? AND status = 'completed' LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()
        graph_row = conn.execute(
            "SELECT id FROM understanding_graph_snapshots WHERE system_id = ? AND snapshot_id = ? LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()

        if not doc_indexed or not graph_row:
            if _is_reasoning_model_available():
                try:
                    from .documentation_indexer import build_documentation_index
                    from .documentation_claim_scanner import scan_all_chunks
                    from .understanding_graph import build_understanding_graph, save_graph_snapshot
                    from .docs_code_reconciler import reconcile
                    from .routes.interview import _get_intelligence_llm_config

                    doc_index = build_documentation_index(conn, system_id, snapshot_id)
                    config = _get_intelligence_llm_config()
                    client = create_llm_client(config)
                    scan_results = scan_all_chunks(client, config, doc_index.chunks)
                    graph = build_understanding_graph(scan_results)
                    save_graph_snapshot(conn, system_id, graph, snapshot_id=snapshot_id)
                    reconcile(conn, system_id, snapshot_id, graph)
                except Exception as _doc_exc:
                    import logging
                    logging.getLogger(__name__).warning("Documentation pipeline in build failed: %s", _doc_exc, exc_info=True)

        # Step: capability_hierarchy_ready (deterministic base)
        cap_run = conn.execute(
            "SELECT id FROM intelligence_runs WHERE system_id = ? AND run_type = 'capability_hierarchy' AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
            (system_id, snapshot_id),
        ).fetchone()
        if not cap_run:
            try:
                from .capability_hierarchy import (
                    build_hierarchy,
                    PROMPT_VERSION as HIERARCHY_PROMPT_VERSION,
                    SCHEMA_VERSION as HIERARCHY_SCHEMA_VERSION,
                )
                from .routes.project_intelligence import (
                    _hierarchy_symbol_records,
                    _hierarchy_entrypoint_records,
                    _persist_hierarchy_node,
                )

                symbols_h = _hierarchy_symbol_records(conn, snapshot_id, system_id)
                entrypoints_h = _hierarchy_entrypoint_records(conn, snapshot_id, system_id)

                if symbols_h:
                    draft_row = conn.execute(
                        "SELECT id, name, purpose FROM system_profile_drafts WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
                        (system_id, snapshot_id),
                    ).fetchone()
                    sp_draft = {"id": draft_row["id"], "name": draft_row["name"], "purpose": draft_row["purpose"]} if draft_row else None

                    built = build_hierarchy(symbols_h, entrypoints_h, sp_draft)

                    now = time.time()
                    run_id = conn.execute(
                        """INSERT INTO intelligence_runs
                            (system_id, snapshot_id, run_type, provider, model,
                             prompt_version, schema_version, decision_method,
                             status, is_mock, started_at, completed_at)
                        VALUES (?, ?, 'capability_hierarchy', 'deterministic', 'none',
                                ?, ?, 'deterministic', 'completed', 0, ?, ?)""",
                        (system_id, snapshot_id, HIERARCHY_PROMPT_VERSION, HIERARCHY_SCHEMA_VERSION, now, now),
                    ).lastrowid

                    purpose_id = None
                    if built.purpose is not None:
                        purpose_id = _persist_hierarchy_node(conn, system_id, snapshot_id, run_id, built.purpose, None, now)
                    for cap in built.capabilities:
                        _persist_hierarchy_node(conn, system_id, snapshot_id, run_id, cap, purpose_id, now)
                    for node in built.unclassified_elements:
                        _persist_hierarchy_node(conn, system_id, snapshot_id, run_id, node, None, now)
                    for node in built.unattached_supporting:
                        _persist_hierarchy_node(conn, system_id, snapshot_id, run_id, node, None, now)
            except Exception as _cap_exc:
                import logging
                logging.getLogger(__name__).warning("Capability hierarchy in build failed: %s", _cap_exc, exc_info=True)

    return get_system_understanding(system_id)
