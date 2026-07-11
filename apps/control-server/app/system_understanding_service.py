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
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from .db import get_conn

logger = logging.getLogger(__name__)


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


# Issue #174: finite next-action categories spanning the
# Understand -> Decide -> Instrument -> Evaluate stages.
NextActionCategory = Literal["understand", "observe", "instrument", "evaluate"]

# Issue #201: finite set of how a next action is carried out. "navigate" (the
# default) links the user to a page; "build" means the action triggers the
# Build / Refresh job directly instead of navigating anywhere.
NextActionKind = Literal["navigate", "build"]


@dataclass
class NextAction:
    action: str
    reason: str
    category: NextActionCategory
    link: Optional[str] = None
    action_kind: NextActionKind = "navigate"


@dataclass
class GapSummary:
    gap_type: str
    count: int


# Issue #202: finite completion status for each of the 4 Hub stages.
StageStatusValue = Literal["not_started", "in_progress", "blocked", "complete"]


@dataclass
class StageStatus:
    stage: str
    status: str
    counts: Dict[str, int] = field(default_factory=dict)


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
    # Issue #201: single highest-priority action for the current state; None
    # while a build job is actively running.
    primary_action: Optional[NextAction] = None
    # Issue #202: completion status + counts for each of the 4 Hub stages.
    stages: List[StageStatus] = field(default_factory=list)


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
    """Check whether a non-mock reasoning model is configured with a usable API key."""
    from .llm import LLMConfig, is_reasoning_model

    config = LLMConfig.intelligence_from_env()
    if config.provider == "mock":
        return False
    if not config.api_key:
        return False
    return is_reasoning_model(config.provider, config.model)


def _check_documentation_indexed(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("documentation_indexed", "missing")
    job_step = conn.execute(
        """SELECT status, error, artifact_provenance
           FROM system_understanding_build_steps
           WHERE system_id = ? AND snapshot_id = ? AND step = 'documentation_index'
           ORDER BY id DESC LIMIT 1""",
        (system_id, snapshot_id),
    ).fetchone()
    if job_step:
        if job_step["status"] == "completed":
            try:
                provenance = json.loads(job_step["artifact_provenance"] or "{}")
            except json.JSONDecodeError:
                provenance = {}
            chunk_count = provenance.get("chunk_count")
            if isinstance(chunk_count, int) and chunk_count == 0:
                return PipelineStep(
                    "documentation_indexed",
                    "warning",
                    detail="No documentation chunks found",
                )
            return PipelineStep("documentation_indexed", "complete")
        if job_step["status"] == "failed":
            return PipelineStep(
                "documentation_indexed",
                "failed",
                detail=job_step["error"] or "documentation_index failed",
            )
        if job_step["status"] == "blocked":
            return PipelineStep(
                "documentation_indexed",
                "blocked",
                detail=job_step["error"] or "documentation_index blocked",
            )
    row = conn.execute(
        "SELECT id, status FROM intelligence_runs WHERE system_id = ? AND run_type IN ('draft_generation', 'repository_drafts') AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row:
        if row["status"] == "completed":
            return PipelineStep("documentation_indexed", "complete")
        return PipelineStep("documentation_indexed", "failed", detail=f"run status: {row['status']}")
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


def _load_graph_for_snapshot(conn, system_id: int, snapshot_id: int):
    """Rehydrate the latest persisted UnderstandingGraph for a snapshot."""
    import json as _json
    from .understanding_graph import UnderstandingGraph, GraphNode, EvidenceRef

    graph_row = conn.execute(
        "SELECT * FROM understanding_graph_snapshots WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if graph_row is None:
        return None

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
            children=nd.get("children", []),
            parent_id=nd.get("parent_id"),
            conflict_with=nd.get("conflict_with"),
            is_weak=nd.get("is_weak", False),
        )

    return UnderstandingGraph(
        nodes=nodes,
        claim_count=graph_data.get("claim_count", 0),
        valid_claim_count=graph_data.get("valid_claim_count", 0),
        confidence_summary=graph_data.get("confidence_summary", {}),
        conflicts=graph_data.get("conflicts", []),
        weak_nodes=graph_data.get("weak_nodes", []),
        source_hash=graph_data.get("source_hash", ""),
    )


def _load_gaps_from_reconciler(conn, system_id: int, snapshot_id: int) -> List[Dict[str, Any]]:
    """Load docs-code gaps by running the reconciler if a graph exists."""
    from .docs_code_reconciler import reconcile

    graph = _load_graph_for_snapshot(conn, system_id, snapshot_id)
    if graph is None:
        return _detect_extra_gaps(conn, system_id, snapshot_id)

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
            # Stable identifier for source_key disambiguation (Issue #107): the
            # reconciler's graph node id distinguishes same-named nodes even when
            # a gap carries no evidence/capability.
            "source_id": (f"node:{g.node_id}" if g.node_id else None),
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

# Issue #199: single source of truth for "gap type -> resolution action(s)".
# For every gap type, index [0] is the primary resolution — the action a
# gap card AND the top-level Next Action for that gap type both link to.
# Principle: work that fixes/completes state (classification, metadata)
# belongs to Interview; work that only reviews/browses existing state
# belongs to Capability Map / Flow Explorer. Any additional entries after
# [0] are secondary/alternate actions shown only on the gap card.
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
            "source_id": f"entrypoint:{uc['entrypoint_type']}:{uc['entrypoint_id']}",
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
            "source_id": f"entrypoint:{ep['entrypoint_type']}:{ep['entrypoint_id']}",
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
                        # The graph node id disambiguates same-named claims that
                        # both lack evidence (Issue #107).
                        "source_id": f"node:{node_id}",
                    })
        except (json.JSONDecodeError, TypeError):
            pass

    return extra


def _attach_issue_drafts(conn, system_id: int, gaps: List[Dict[str, Any]]) -> None:
    """Attach a stable source_key and any existing issue drafts to each gap.

    Issue #107: drafts persist independently of the (recomputed-per-read) gaps,
    so they are matched back by source_key. Runs against the caller's open
    connection because the DB lock is non-reentrant.
    """
    from .issue_drafts import gap_source_key

    drafts_by_key: Dict[str, List[Dict[str, Any]]] = {}
    rows = conn.execute(
        """SELECT id, source_key, status, external_url, title
           FROM issue_drafts WHERE system_id = ? ORDER BY id DESC""",
        (system_id,),
    ).fetchall()
    for r in rows:
        key = r["source_key"]
        if key:
            drafts_by_key.setdefault(key, []).append(
                {
                    "id": r["id"],
                    "status": r["status"],
                    "external_url": r["external_url"],
                    "title": r["title"],
                }
            )

    for gap in gaps:
        key = gap_source_key(gap)
        gap["source_key"] = key
        gap["issue_drafts"] = drafts_by_key.get(key, [])


def _compute_gap_summary(gaps: List[Dict[str, Any]]) -> List[GapSummary]:
    counts: Dict[str, int] = {}
    for g in gaps:
        gt = g.get("gap_type", "unknown")
        counts[gt] = counts.get(gt, 0) + 1
    return [GapSummary(gap_type=k, count=v) for k, v in sorted(counts.items())]


def _build_next_actions(
    pipeline: List[PipelineStep],
    purpose: Optional[Dict[str, Any]],
    capabilities: List[Dict[str, Any]],
    metadata_coverage: Optional[MetadataCoverage],
    gap_count: int,
    gap_summary: Optional[List[GapSummary]] = None,
    proposed_plan_ids: Optional[List[int]] = None,
    approved_plan_ids_without_validated_patch: Optional[List[int]] = None,
    undecided_completed_experiment_ids: Optional[List[int]] = None,
) -> List[NextAction]:
    proposed_plan_ids = proposed_plan_ids or []
    approved_plan_ids_without_validated_patch = approved_plan_ids_without_validated_patch or []
    undecided_completed_experiment_ids = undecided_completed_experiment_ids or []

    actions: List[NextAction] = []
    step_map = {s.step: s.status for s in pipeline}

    if step_map.get("repository_configured") != "complete":
        actions.append(NextAction(
            action="Configure repository",
            reason="Repository is not configured yet",
            category="understand",
            link="/repository",
        ))
        return actions

    if step_map.get("snapshot_ready") != "complete":
        actions.append(NextAction(
            action="Create snapshot",
            reason="No ready snapshot available",
            category="understand",
            link="/repository",
        ))
        return actions

    if step_map.get("symbols_indexed") != "complete":
        actions.append(NextAction(
            action="Index code symbols",
            reason="Code symbols have not been indexed",
            category="understand",
            link="/repository",
        ))

    if step_map.get("documentation_indexed") != "complete":
        actions.append(NextAction(
            action="Build documentation index",
            reason="Documentation files have not been indexed into chunks",
            category="understand",
            link="/system-understanding",
        ))

    if step_map.get("documentation_claims_scanned") != "complete":
        actions.append(NextAction(
            action="Scan documentation claims",
            reason="Documentation claims have not been scanned",
            category="understand",
            link="/system-understanding",
        ))

    if step_map.get("entrypoints_discovered") != "complete":
        actions.append(NextAction(
            action="Discover entrypoints",
            reason="API/CLI/queue entrypoints have not been discovered",
            category="understand",
            link="/flow-explorer",
        ))

    if step_map.get("docs_code_reconciled") != "complete":
        actions.append(NextAction(
            action="Reconcile docs and code",
            reason="Documentation and code have not been reconciled",
            category="understand",
            link="/system-understanding",
        ))

    if step_map.get("capability_hierarchy_ready") != "complete":
        actions.append(NextAction(
            action="Generate capability hierarchy",
            reason="Capability hierarchy has not been generated",
            category="understand",
            link="/capability-map",
        ))

    # Issue #120: pipeline step remediation (above) always takes priority
    # while the pipeline is incomplete or blocked/failed. Once every step is
    # complete, a completed pipeline is not the same as a usable system
    # understanding — System Purpose and main capabilities are the highest
    # priority next actions, ahead of metadata coverage and docs-code gaps.
    pipeline_complete = all(s.status == "complete" for s in pipeline)

    if pipeline_complete:
        purpose_defined = bool(purpose and (purpose.get("summary") or purpose.get("name")))
        if not purpose_defined:
            actions.append(NextAction(
                action="Define System Purpose",
                reason="Pipeline completed, but no system purpose is defined yet.",
                category="understand",
                link="/interview",
            ))

        if not capabilities:
            actions.append(NextAction(
                action="Identify main system capabilities",
                reason=(
                    "System purpose and main capabilities are not identified yet, "
                    "so probe candidates, flow exploration, and improvement "
                    "proposals lack a foundation."
                ),
                category="understand",
                link="/interview",
            ))

    if metadata_coverage and metadata_coverage.symbol_count > 0:
        ratio = metadata_coverage.symbols_with_source_metadata / metadata_coverage.symbol_count
        if ratio < 0.1:
            actions.append(NextAction(
                action="Add source metadata",
                reason=f"Only {metadata_coverage.symbols_with_source_metadata} of {metadata_coverage.symbol_count} symbols have probe-agent metadata",
                category="understand",
                link="/interview",
            ))

    if gap_count > 0:
        actions.append(NextAction(
            action="Review docs-code gaps",
            reason=f"{gap_count} docs-code gaps found",
            category="understand",
            link="/system-understanding",
        ))

    # Issue #199: the link for each gap-type-derived top-level action is
    # taken from GAP_NEXT_ACTIONS[gap_type][0] (the primary resolution) so
    # this action and the corresponding gap card never disagree on where to
    # send the user.
    gap_counts = {g.gap_type: g.count for g in (gap_summary or [])}
    unclassified_count = gap_counts.get("unclassified_entrypoint", 0)
    if unclassified_count > 0:
        actions.append(NextAction(
            action="Unclassified API found",
            reason=(
                f"{unclassified_count} API entrypoint{'s' if unclassified_count != 1 else ''} "
                "need capability classification; classify in Interview, then view "
                "results in Capability Map"
            ),
            category="observe",
            link=GAP_NEXT_ACTIONS["unclassified_entrypoint"][0]["link"],
        ))

    probe_candidate_count = gap_counts.get("missing_probe_flow", 0)
    if probe_candidate_count > 0:
        actions.append(NextAction(
            action="Probe candidate available",
            reason=f"{probe_candidate_count} classified entrypoint{'s' if probe_candidate_count != 1 else ''} have no probe plan yet",
            category="observe",
            link=GAP_NEXT_ACTIONS["missing_probe_flow"][0]["link"],
        ))

    # Issue #174: probe plan / experiment status is a downstream, independent
    # axis from the System Understanding pipeline above — surface it
    # regardless of pipeline completeness so review-worthy work is never
    # hidden behind an unrelated pipeline step.
    for plan_id in proposed_plan_ids:
        actions.append(NextAction(
            action="Review probe plan",
            reason=f"Probe plan #{plan_id} is awaiting review",
            category="observe",
            link=f"/probe-planner?plan={plan_id}",
        ))

    for plan_id in approved_plan_ids_without_validated_patch:
        actions.append(NextAction(
            action="Generate / validate probe patch",
            reason=f"Approved probe plan #{plan_id} has no validated patch yet",
            category="instrument",
            link=f"/probe-planner?plan={plan_id}",
        ))

    for experiment_id in undecided_completed_experiment_ids:
        actions.append(NextAction(
            action="Review experiment decision",
            reason=f"Experiment #{experiment_id} completed but has no recorded decision",
            category="evaluate",
            link="/experiments",
        ))

    if pipeline_complete and not actions:
        actions.append(NextAction(
            action="Start from Capability",
            reason="System understanding is complete; explore from the Capability Map.",
            category="observe",
            link="/capability-map",
        ))
        actions.append(NextAction(
            action="Start from Feature",
            reason="System understanding is complete; explore from the Feature Map.",
            category="observe",
            link="/feature-map",
        ))
        actions.append(NextAction(
            action="Open Flow Explorer",
            reason="System understanding is complete; explore call flows from entrypoints.",
            category="observe",
            link="/flow-explorer",
        ))

    return actions


def _derive_primary_action(
    pipeline: List[PipelineStep],
    next_actions: List[NextAction],
    latest_build: Optional[Dict[str, Any]],
) -> Optional[NextAction]:
    """Pure derivation of the single highest-priority action (Issue #201).

    ``next_actions`` is the already-ordered state machine produced by
    ``_build_next_actions`` above; this function does not change that
    ordering, it only picks the one action a Hub header CTA should show,
    using the explicit finite rules below (evaluated in order, first match
    wins). Deterministic only (Principle 6) -- no reasoning model involved.

    NOTE: a future phase may fold this into ``system_state.py`` (Issue #193,
    System State Assessment) as one more state-machine projection alongside
    ``StateItem``; not merged here, per this issue's non-goals.
    """
    step_map = {s.step: s.status for s in pipeline}

    # Rule 1: repository not configured or no ready snapshot -> the existing
    # first next_action (Configure repository / Create snapshot).
    if (
        step_map.get("repository_configured") != "complete"
        or step_map.get("snapshot_ready") != "complete"
    ):
        return next_actions[0] if next_actions else None

    # Rule 2: a build job is actively running/queued -> no primary action;
    # BuildJobPanel already shows step-by-step progress for it.
    if latest_build and latest_build.get("status") in ("queued", "running"):
        return None

    # Rule 3: some pipeline step (beyond repository/snapshot, already
    # confirmed complete above) is not complete -> point at running a build,
    # independent of which/how many steps remain.
    incomplete_steps = [s for s in pipeline if s.status != "complete"]
    if incomplete_steps:
        count = len(incomplete_steps)
        return NextAction(
            action="Build system understanding",
            reason=f"{count} pipeline step{'s' if count != 1 else ''} not complete yet",
            category="understand",
            link=None,
            action_kind="build",
        )

    # Rule 4: everything above is satisfied -> defer to the first next_action
    # (its generation order/priority is unchanged by this issue).
    return next_actions[0] if next_actions else None


def _plan_has_validated_patch(conn, plan_id: int) -> bool:
    """A plan's patch is validated when its latest baseline and probed
    validation runs both succeeded — the same finite condition the patch
    apply endpoint gates on (Principle 6).
    """
    patch_rows = conn.execute(
        "SELECT id FROM probe_patches WHERE plan_id = ? AND status != 'failed'",
        (plan_id,),
    ).fetchall()
    for patch in patch_rows:
        val_rows = conn.execute(
            """SELECT variant, overall_success FROM validation_runs
               WHERE patch_id = ? ORDER BY id DESC""",
            (patch["id"],),
        ).fetchall()
        latest: Dict[str, bool] = {}
        for vr in val_rows:
            latest.setdefault(vr["variant"], bool(vr["overall_success"]))
        if latest.get("baseline") is True and latest.get("probed") is True:
            return True
    return False


def _load_pending_plan_action_ids(conn, system_id: int) -> Tuple[List[int], List[int], int]:
    """Return (proposed_plan_ids, approved_plan_ids_without_validated_patch,
    approved_plan_total_count).

    ``approved_plan_total_count`` is exposed (Issue #202) so the Instrument
    stage's ``validated`` count can be derived as
    ``approved_plan_total_count - len(approved_plan_ids_without_validated_patch)``
    without a second, differently-worded query over the same rows.
    """
    proposed_ids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM probe_plans WHERE system_id = ? AND status = 'proposed' ORDER BY id DESC",
            (system_id,),
        ).fetchall()
    ]
    approved_rows = conn.execute(
        "SELECT id FROM probe_plans WHERE system_id = ? AND status = 'approved' ORDER BY id DESC",
        (system_id,),
    ).fetchall()
    approved_without_patch_ids = [
        r["id"] for r in approved_rows if not _plan_has_validated_patch(conn, r["id"])
    ]
    return proposed_ids, approved_without_patch_ids, len(approved_rows)


def _load_undecided_completed_experiment_ids(conn, system_id: int) -> List[int]:
    rows = conn.execute(
        """SELECT id FROM experiments
           WHERE system_id = ? AND status = 'completed' AND human_decision = 'undecided'
           ORDER BY id DESC""",
        (system_id,),
    ).fetchall()
    return [r["id"] for r in rows]


def _load_decided_completed_experiment_count(conn, system_id: int) -> int:
    """Count of completed experiments with a recorded decision -- the exact
    complement of ``_load_undecided_completed_experiment_ids`` (same
    status/human_decision condition set, Issue #202)."""
    row = conn.execute(
        """SELECT COUNT(*) FROM experiments
           WHERE system_id = ? AND status = 'completed' AND human_decision != 'undecided'""",
        (system_id,),
    ).fetchone()
    return row[0]


def _load_total_probe_plan_count(conn, system_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM probe_plans WHERE system_id = ?", (system_id,)
    ).fetchone()
    return row[0]


def _load_total_experiment_count(conn, system_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM experiments WHERE system_id = ?", (system_id,)
    ).fetchone()
    return row[0]


def _derive_stage_statuses(
    pipeline: List[PipelineStep],
    purpose: Optional[Dict[str, Any]],
    capabilities: List[Dict[str, Any]],
    gap_count: int,
    gap_summary: List[GapSummary],
    entrypoint_count: int,
    proposed_plan_count: int,
    approved_without_patch_count: int,
    validated_plan_count: int,
    total_plan_count: int,
    undecided_experiment_count: int,
    decided_experiment_count: int,
    total_experiment_count: int,
) -> List[StageStatus]:
    """Pure derivation of the 4 Hub stage completion statuses (Issue #202).

    Each stage's rules are evaluated top-to-bottom, first match wins
    (Principle 6: explicit finite branches over an enumerated set, no
    reasoning model). See the "Stage status" table in
    docs/system-understanding-navigation.md for the rule source.
    """
    stages: List[StageStatus] = []

    # --- understand ---
    if any(s.status in ("blocked", "failed") for s in pipeline):
        understand_status = "blocked"
    elif all(s.status == "missing" for s in pipeline):
        understand_status = "not_started"
    else:
        purpose_defined = bool(purpose and (purpose.get("summary") or purpose.get("name")))
        if (
            all(s.status == "complete" for s in pipeline)
            and purpose_defined
            and len(capabilities) > 0
        ):
            understand_status = "complete"
        else:
            understand_status = "in_progress"
    stages.append(StageStatus("understand", understand_status, {"gaps": gap_count}))

    # --- observe (Decide Where to Observe) ---
    step_map = {s.step: s.status for s in pipeline}
    unclassified_count = next(
        (g.count for g in gap_summary if g.gap_type == "unclassified_entrypoint"), 0
    )
    if step_map.get("entrypoints_discovered") != "complete":
        observe_status = "not_started"
    elif entrypoint_count > 0 and unclassified_count == 0:
        observe_status = "complete"
    else:
        observe_status = "in_progress"
    stages.append(StageStatus(
        "observe", observe_status,
        {"entrypoints": entrypoint_count, "unclassified": unclassified_count},
    ))

    # --- instrument ---
    if total_plan_count == 0:
        instrument_status = "not_started"
    elif validated_plan_count > 0 and approved_without_patch_count == 0:
        instrument_status = "complete"
    else:
        instrument_status = "in_progress"
    stages.append(StageStatus(
        "instrument", instrument_status,
        {
            "proposed": proposed_plan_count,
            "approved_without_patch": approved_without_patch_count,
            "validated": validated_plan_count,
        },
    ))

    # --- evaluate ---
    if total_experiment_count == 0:
        evaluate_status = "not_started"
    elif decided_experiment_count > 0 and undecided_experiment_count == 0:
        evaluate_status = "complete"
    else:
        evaluate_status = "in_progress"
    stages.append(StageStatus(
        "evaluate", evaluate_status,
        {"undecided": undecided_experiment_count, "decided": decided_experiment_count},
    ))

    return stages


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
            _attach_issue_drafts(conn, system_id, summary.gaps)
            summary.gap_summary = _compute_gap_summary(summary.gaps)

        proposed_plan_ids, approved_plan_ids_without_patch, approved_plan_total = (
            _load_pending_plan_action_ids(conn, system_id)
        )
        undecided_experiment_ids = _load_undecided_completed_experiment_ids(conn, system_id)

        summary.next_actions = _build_next_actions(
            pipeline,
            summary.purpose,
            summary.capabilities,
            summary.metadata_coverage,
            len(summary.gaps),
            summary.gap_summary,
            proposed_plan_ids,
            approved_plan_ids_without_patch,
            undecided_experiment_ids,
        )

        # Issue #202: stage status counts reuse the id lists above and add
        # the small set of totals not already collected for next_actions.
        total_plan_count = _load_total_probe_plan_count(conn, system_id)
        total_experiment_count = _load_total_experiment_count(conn, system_id)
        decided_experiment_count = _load_decided_completed_experiment_count(conn, system_id)
        validated_plan_count = approved_plan_total - len(approved_plan_ids_without_patch)
        entrypoint_count = (
            summary.metadata_coverage.entrypoint_count if summary.metadata_coverage else 0
        )

        summary.stages = _derive_stage_statuses(
            pipeline,
            summary.purpose,
            summary.capabilities,
            len(summary.gaps),
            summary.gap_summary,
            entrypoint_count,
            len(proposed_plan_ids),
            len(approved_plan_ids_without_patch),
            validated_plan_count,
            total_plan_count,
            len(undecided_experiment_ids),
            decided_experiment_count,
            total_experiment_count,
        )

    # Issue #201: build-job lookup opens its own `get_conn()`, and the DB
    # lock is non-reentrant, so this must run after the `with get_conn()`
    # block above has released it (see the issue-drafts nested-lock note
    # elsewhere in this module for the same constraint).
    from .system_understanding_jobs import get_latest_job

    latest_build = get_latest_job(system_id)
    summary.primary_action = _derive_primary_action(
        pipeline, summary.next_actions, latest_build,
    )
    return summary


# ---------------------------------------------------------------------------
# Build orchestration (Issue #109) lives in system_understanding_jobs; these
# wrappers keep the public service API stable for existing callers.
# ---------------------------------------------------------------------------


def start_system_understanding_build(system_id: int) -> int:
    """Enqueue a step-orchestrated build job and return its id immediately."""
    from .system_understanding_jobs import start_system_understanding_build as _start

    return _start(system_id)


def get_system_understanding_build(system_id: int, build_id: int):
    from .system_understanding_jobs import get_job

    return get_job(system_id, build_id)


def get_latest_system_understanding_build(system_id: int):
    from .system_understanding_jobs import get_latest_job

    return get_latest_job(system_id)
