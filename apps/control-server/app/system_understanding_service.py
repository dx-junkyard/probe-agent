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

from . import state_facts, state_messages
from .db import get_conn
from .system_state import UnderstandingStatus, evaluate_understanding

logger = logging.getLogger(__name__)

GAP_HISTORY_EMPTY_SENTINEL = "__no_open_gaps__"


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
    # Issue #240: server-supplied Japanese display copy. Filled from the
    # shared catalog by stage id so the Dashboard renders one canonical
    # label/description instead of its own English STAGE_LABELS map.
    label: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.label or not self.description:
            msg = state_messages.stage_message(self.stage)
            self.label = self.label or msg["label"]
            self.description = self.description or msg["description"]


# Issue #203: before/after gap counts across the last two settled builds.
@dataclass
class GapTrend:
    gap_type: str
    current: int
    previous: int


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
    understanding_build_id: Optional[int] = None
    commit_sha: Optional[str] = None
    pipeline: List[PipelineStep] = field(default_factory=list)
    purpose: Optional[Dict[str, Any]] = None
    capabilities: List[Dict[str, Any]] = field(default_factory=list)
    entrypoints: List[Dict[str, Any]] = field(default_factory=list)
    major_symbols: List[Dict[str, Any]] = field(default_factory=list)
    gaps: List[Dict[str, Any]] = field(default_factory=list)
    gap_summary: List[GapSummary] = field(default_factory=list)
    metadata_coverage: Optional[MetadataCoverage] = None
    # Issue #202: completion status + counts for each of the 4 Hub stages.
    stages: List[StageStatus] = field(default_factory=list)
    # Issue #203: gap-count trend across the last two settled builds (still
    # deterministic, no reasoning model involved).
    gap_trend: List[GapTrend] = field(default_factory=list)
    # Issue #201/#203's `primary_action` / `next_actions` /
    # `understanding_refresh_recommended` fields were removed in Issue #239:
    # the canonical "what should the user do next" projection is now
    # `system_state.select_primary_item` / `GET /system-state`'s
    # `primary_item` / `page_items` (Issue #238). See
    # `_check_understanding_refresh_recommended` below, which is kept because
    # `system_state.py` still calls it to build the
    # `interview.materialized.rebuild_required` StateItem.
    # Issue #240: server-supplied Japanese success summary shown when the
    # whole pipeline is complete (replaces the Dashboard's English
    # client-assembled `successSummary`). None while the pipeline is not
    # complete.
    success_summary: Optional[str] = None
    # Issue #94/#275: manual system_profile purpose surfaced as a parallel
    # provenance view next to `purpose` (the AI/source-derived view, whose
    # existing semantics are unchanged). Manual view is snapshot-independent
    # (included even without a ready snapshot); the AI view is included only
    # when a ready snapshot exists. Each entry is a dict matching
    # SystemUnderstandingPurposeViewOut's fields.
    purpose_views: List[Dict[str, Any]] = field(default_factory=list)
    # The latest human "confirmed" record reconciling the manual and AI
    # purpose views (dict matching SystemUnderstandingPurposeConfirmationOut),
    # or None if never confirmed.
    purpose_confirmation: Optional[Dict[str, Any]] = None


def _check_repository_configured(conn, system_id: int) -> PipelineStep:
    if state_facts.has_repository_configured(conn, system_id):
        return PipelineStep("repository_configured", "complete")
    return PipelineStep("repository_configured", "missing")


def _get_latest_ready_snapshot(conn, system_id: int):
    return state_facts.get_latest_ready_snapshot(conn, system_id)


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
    job_step = state_facts.get_latest_build_step(conn, system_id, snapshot_id, "documentation_index")
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
                    detail=state_messages.pipeline_step_detail("documentation_indexed.no_chunks"),
                )
            return PipelineStep("documentation_indexed", "complete")
        if job_step["status"] == "failed":
            return PipelineStep(
                "documentation_indexed",
                "failed",
                detail=job_step["error"]
                or state_messages.pipeline_step_detail("documentation_indexed.build_step_failed_default"),
            )
        if job_step["status"] == "blocked":
            return PipelineStep(
                "documentation_indexed",
                "blocked",
                detail=job_step["error"]
                or state_messages.pipeline_step_detail("documentation_indexed.build_step_blocked_default"),
            )
    row = state_facts.get_latest_intelligence_run(
        conn, system_id, snapshot_id, ["draft_generation", "repository_drafts"]
    )
    if row:
        if row["status"] == "completed":
            return PipelineStep("documentation_indexed", "complete")
        return PipelineStep(
            "documentation_indexed",
            "failed",
            detail=state_messages.pipeline_step_detail("documentation_indexed.run_status", status=row["status"]),
        )
    return PipelineStep("documentation_indexed", "missing")


def _check_documentation_claims_scanned(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("documentation_claims_scanned", "missing")
    if state_facts.has_understanding_graph_snapshot(conn, system_id, snapshot_id):
        return PipelineStep("documentation_claims_scanned", "complete")
    if not _is_reasoning_model_available():
        return PipelineStep(
            "documentation_claims_scanned",
            "blocked",
            detail=state_messages.pipeline_step_detail("documentation_claims_scanned.blocked"),
        )
    return PipelineStep("documentation_claims_scanned", "missing")


def _check_symbols_indexed(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("symbols_indexed", "missing")
    row = state_facts.get_latest_intelligence_run(conn, system_id, snapshot_id, ["symbol_index"])
    if row:
        if row["status"] == "completed":
            return PipelineStep("symbols_indexed", "complete")
        return PipelineStep(
            "symbols_indexed",
            "failed",
            detail=state_messages.pipeline_step_detail("symbols_indexed.run_status", status=row["status"]),
        )
    return PipelineStep("symbols_indexed", "missing")


def _check_entrypoints_discovered(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("entrypoints_discovered", "missing")
    if state_facts.has_code_entrypoints(conn, system_id, snapshot_id):
        return PipelineStep("entrypoints_discovered", "complete")
    return PipelineStep("entrypoints_discovered", "missing")


def _check_docs_code_reconciled(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("docs_code_reconciled", "missing")
    has_graph = state_facts.has_understanding_graph_snapshot(conn, system_id, snapshot_id)
    has_symbols = state_facts.has_code_symbols(conn, system_id, snapshot_id)
    if has_graph and has_symbols:
        return PipelineStep("docs_code_reconciled", "complete")
    if has_graph or has_symbols:
        return PipelineStep(
            "docs_code_reconciled",
            "warning",
            detail=state_messages.pipeline_step_detail("docs_code_reconciled.partial"),
        )
    return PipelineStep("docs_code_reconciled", "missing")


def _check_capability_hierarchy_ready(conn, system_id: int, snapshot_id: Optional[int]) -> PipelineStep:
    if snapshot_id is None:
        return PipelineStep("capability_hierarchy_ready", "missing")
    row = state_facts.get_latest_intelligence_run(conn, system_id, snapshot_id, ["capability_hierarchy"])
    if row:
        if row["status"] == "completed":
            # Issue #210: a completed run can still produce zero capabilities
            # (e.g. the target repository has no `probe-agent:` docstring
            # metadata to group). Mirror the "completed but zero" pattern in
            # _check_documentation_indexed above instead of reporting
            # "complete" while the pipeline checklist and the System State
            # banner disagree about whether the hierarchy is ready.
            if state_facts.capability_count_in_snapshot(conn, system_id, snapshot_id) == 0:
                return PipelineStep(
                    "capability_hierarchy_ready",
                    "warning",
                    detail=state_messages.pipeline_step_detail("capability_hierarchy_ready.empty"),
                )
            return PipelineStep("capability_hierarchy_ready", "complete")
        if row["status"] == "failed":
            return PipelineStep("capability_hierarchy_ready", "failed")
        return PipelineStep(
            "capability_hierarchy_ready",
            "warning",
            detail=state_messages.pipeline_step_detail("capability_hierarchy_ready.status", status=row["status"]),
        )
    if not _is_reasoning_model_available():
        return PipelineStep(
            "capability_hierarchy_ready",
            "blocked",
            detail=state_messages.pipeline_step_detail("capability_hierarchy_ready.blocked"),
        )
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


def compute_pipeline_steps(conn, system_id: int, snapshot_row) -> List[PipelineStep]:
    """Canonical deterministic 8-step Pipeline Checklist for one snapshot.

    Single source shared by the Pipeline Checklist API and system_state's
    preparation-phase completion gate (Issue #237), so the banner/phase and
    the checklist can never disagree about completion.
    """
    return _build_pipeline(conn, system_id, snapshot_row)


def _load_purpose(conn, system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    """Load system purpose from hierarchy or drafts.

    Delegates to ``state_facts.load_ai_purpose_view`` (Issue #94/#275) and
    drops its extra ``source`` key, so this function's public return shape
    (``name`` / ``summary`` / ``provenance_kind``) stays exactly what it was
    before that extraction -- a behavior-preserving refactor.
    """
    view = state_facts.load_ai_purpose_view(conn, system_id, snapshot_id)
    if view is None:
        return None
    return {
        "name": view["name"],
        "summary": view["summary"],
        "provenance_kind": view["provenance_kind"],
    }


def _load_purpose_views(
    conn, system_id: int, snapshot_id: Optional[int]
) -> List[Dict[str, Any]]:
    """Manual + AI purpose provenance views (Issue #94/#275).

    Manual view first, snapshot-independent (included even when
    ``snapshot_id`` is None): the human-entered ``system_profile.purpose``,
    when non-empty. AI view second, only when ``snapshot_id`` is not None:
    ``state_facts.load_ai_purpose_view``'s capability_hierarchy-node-or-draft
    result for that snapshot, when present.
    """
    views: List[Dict[str, Any]] = []

    profile = state_facts.get_system_profile_row(conn, system_id)
    if profile is not None and (profile["purpose"] or "").strip():
        name = (profile["name"] or "").strip() or "System Profile"
        views.append({
            "source": "system_profile",
            "provenance_kind": "manual",
            "name": name,
            "summary": profile["purpose"],
            "updated_at": profile["updated_at"],
        })

    if snapshot_id is not None:
        ai_view = state_facts.load_ai_purpose_view(conn, system_id, snapshot_id)
        if ai_view is not None:
            views.append({
                "source": ai_view["source"],
                "provenance_kind": ai_view["provenance_kind"],
                "name": ai_view["name"],
                "summary": ai_view["summary"],
                "updated_at": None,
            })

    return views


def _load_purpose_confirmation(
    conn, system_id: int, current_ready_snapshot_id: Optional[int]
) -> Optional[Dict[str, Any]]:
    """The latest human purpose confirmation, with staleness computed against
    the current profile/snapshot/AI-view state (Issue #94/#275)."""
    row = state_facts.get_latest_purpose_confirmation(conn, system_id)
    if row is None:
        return None
    stale_reason = state_facts.purpose_confirmation_staleness(
        conn, system_id, current_ready_snapshot_id
    )
    return {
        "id": row["id"],
        "snapshot_id": row["snapshot_id"],
        "understanding_build_id": row["understanding_build_id"],
        "decided_by_user_id": row["decided_by_user_id"],
        "decision_method": row["decision_method"],
        "manual_purpose": row["manual_purpose"],
        "ai_purpose_name": row["ai_purpose_name"],
        "ai_purpose_summary": row["ai_purpose_summary"],
        "ai_source": row["ai_source"],
        "ai_provenance_kind": row["ai_provenance_kind"],
        "note": row["note"],
        "created_at": row["created_at"],
        "stale": stale_reason is not None,
        "stale_reason": stale_reason,
    }


def _load_capabilities(conn, system_id: int, snapshot_id: int) -> List[Dict[str, Any]]:
    run = state_facts.get_latest_completed_capability_hierarchy_run(
        conn, system_id, snapshot_id
    )
    if run is None:
        return []
    rows = conn.execute(
        "SELECT * FROM capability_hierarchy_nodes "
        "WHERE system_id = ? AND snapshot_id = ? AND intelligence_run_id = ? "
        "AND node_type = 'capability' ORDER BY id",
        (system_id, snapshot_id, run["id"]),
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

# Issue #199 / #240: the gap-type -> title and gap-type -> resolution action
# copy now lives in the shared Japanese catalog (state_messages.GAP_*). This
# module keeps only the ordering/role contract (index [0] is the primary
# resolution the gap card AND the top-level Next Action both link to; work
# that fixes/completes state belongs to Interview, review/browse belongs to
# Capability Map / Flow Explorer). GAP_NEXT_ACTIONS is re-exported for
# backward-compatible imports/tests that read the structure.
GAP_NEXT_ACTIONS = state_messages.GAP_NEXT_ACTIONS


def _gap_severity(gap_type: Optional[str]) -> str:
    return GAP_SEVERITY.get(gap_type or "", "info")


def _gap_title(gap_type: Optional[str], node_name: Optional[str]) -> str:
    return state_messages.gap_title(gap_type or "", node_name or "unknown")


def _gap_next_actions(gap_type: Optional[str]) -> List[Dict[str, Optional[str]]]:
    return state_messages.gap_next_actions(gap_type or "")


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
            "notes": state_messages.gap_note(
                "unclassified_entrypoint",
                entrypoint_type=uc["entrypoint_type"],
                entrypoint_id=uc["entrypoint_id"],
            ),
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
            "notes": state_messages.gap_note(
                "missing_probe_flow",
                entrypoint_type=ep["entrypoint_type"],
                entrypoint_id=ep["entrypoint_id"],
            ),
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
                        "notes": state_messages.gap_note("missing_evidence", name=node_name),
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


def _attach_gap_triage(
    conn, system_id: int, snapshot_id: Optional[int], gaps: List[Dict[str, Any]]
) -> None:
    """Attach Issue #276's stable locator, fingerprint and effective state."""
    from .gap_triage import annotate_gaps

    annotate_gaps(conn, system_id, snapshot_id, gaps)


def _open_gaps(gaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return gaps still needing a human decision.

    Summary, stage count, history/trend, and StateItem derivation consistently
    use this definition. Acknowledged/dismissed/resolved gaps remain available
    through the all-items worklist filter but no longer contribute noise.
    """
    return [g for g in gaps if g.get("triage_status", "open") == "open"]


def _compute_gap_summary(gaps: List[Dict[str, Any]]) -> List[GapSummary]:
    counts: Dict[str, int] = {}
    for g in gaps:
        gt = g.get("gap_type", "unknown")
        counts[gt] = counts.get(gt, 0) + 1
    return [GapSummary(gap_type=k, count=v) for k, v in sorted(counts.items())]


def _load_pending_plan_action_ids(conn, system_id: int) -> Tuple[List[int], List[int], int]:
    """Return (proposed_plan_ids, approved_plan_ids_without_validated_patch,
    approved_plan_total_count).

    ``approved_plan_total_count`` is exposed (Issue #202) so the Instrument
    stage's ``validated`` count can be derived as
    ``approved_plan_total_count - len(approved_plan_ids_without_validated_patch)``
    without a second, differently-worded query over the same rows.

    The "has this plan's patch been validated" check itself lives in
    ``state_facts.plan_has_validated_patch`` (Issue #238) so this module and
    ``system_state.py``'s ``proposal.probe_plans.approved_without_patch``
    StateItem agree on one query instead of two independent copies.
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
        r["id"] for r in approved_rows if not state_facts.plan_has_validated_patch(conn, r["id"])
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
    purpose_defined: bool,
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


def _load_gap_trend(conn, system_id: int) -> List[GapTrend]:
    """Compare per-gap_type counts between the two most recent settled builds
    that recorded gap history (Issue #203).

    Reads only persisted history rows (written at build finalize time in
    system_understanding_jobs._record_gap_history) -- it never recomputes
    gaps here, so the trend reflects what was true for each build's snapshot
    at the time it settled. Fewer than 2 builds with recorded history yields
    an empty list. A gap_type present in only one of the two builds gets 0
    on the side where it is absent (new gap_type: previous=0; resolved
    gap_type: current=0).
    """
    build_rows = conn.execute(
        """SELECT DISTINCT build_id FROM system_understanding_gap_history
           WHERE system_id = ? ORDER BY build_id DESC LIMIT 2""",
        (system_id,),
    ).fetchall()
    build_ids = [r["build_id"] for r in build_rows]
    if len(build_ids) < 2:
        return []
    current_build_id, previous_build_id = build_ids[0], build_ids[1]

    def _counts_for(build_id: int) -> Dict[str, int]:
        rows = conn.execute(
            """SELECT gap_type, count FROM system_understanding_gap_history
               WHERE system_id = ? AND build_id = ?""",
            (system_id, build_id),
        ).fetchall()
        return {
            r["gap_type"]: r["count"]
            for r in rows
            if r["gap_type"] != GAP_HISTORY_EMPTY_SENTINEL
        }

    current_counts = _counts_for(current_build_id)
    previous_counts = _counts_for(previous_build_id)
    gap_types = sorted(set(current_counts) | set(previous_counts))
    return [
        GapTrend(
            gap_type=gt,
            current=current_counts.get(gt, 0),
            previous=previous_counts.get(gt, 0),
        )
        for gt in gap_types
    ]


def _check_understanding_refresh_recommended(conn, system_id: int) -> bool:
    """True when a materialized Interview change post-dates the latest
    completed build (Issue #203) -- a plain timestamp comparison over
    persisted rows, no reasoning model involved (Principle 6).

    False when no session has been materialized yet, or no build has ever
    completed for this system.
    """
    materialized_row = conn.execute(
        """SELECT MAX(materialized_at) AS latest FROM interview_session
           WHERE system_id = ? AND materialized_at IS NOT NULL""",
        (system_id,),
    ).fetchone()
    latest_materialized_at = materialized_row["latest"] if materialized_row else None
    if latest_materialized_at is None:
        return False

    build_row = conn.execute(
        """SELECT completed_at FROM system_understanding_builds
           WHERE system_id = ? AND status = 'completed'
           ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()
    if build_row is None or build_row["completed_at"] is None:
        return False

    return latest_materialized_at > build_row["completed_at"]


def _purpose_defined_from_understanding_status(status: UnderstandingStatus) -> bool:
    """Reduce ``evaluate_understanding``'s 5-way classification to the single
    bool this module's next-actions / stage-status derivation needs
    (Issue #236).

    Only ``satisfied_current`` means System Purpose is structurally present
    for *this* snapshot right now -- the same condition the pre-#236 local
    check (``bool(purpose and (purpose.get("summary") or purpose.get("name")))``
    over ``_load_purpose``'s dict) tested, which is exactly
    ``state_facts.purpose_defined_in_snapshot`` (see
    ``evaluate_understanding``'s ``defined`` variable). The other four
    branches (``baseline_reusable``, ``diff_impacted``, ``unconfirmed``,
    ``missing_baseline``) all mean "not defined for this snapshot" here --
    this call site never consulted cross-snapshot baseline reuse before
    #236 and must not start now (behavior-preserving refactor). See
    ``tests/test_state_facts.py`` for the equivalence proof against the old
    formula.
    """
    return status.kind == "satisfied_current"


def get_system_understanding(system_id: int) -> SystemUnderstandingSummary:
    """Read-only: aggregate persisted state into a system understanding summary."""
    with get_conn() as conn:
        snapshot_row = _get_latest_ready_snapshot(conn, system_id)
        pipeline = _build_pipeline(conn, system_id, snapshot_row)

        summary = SystemUnderstandingSummary(
            system_id=system_id,
            pipeline=pipeline,
        )

        purpose_defined = False
        if snapshot_row:
            snapshot_id = snapshot_row["id"]
            summary.snapshot_id = snapshot_id
            summary.commit_sha = snapshot_row["commit_sha"]
            completed_build = state_facts.get_latest_completed_understanding_build(
                conn, system_id, snapshot_id
            )
            summary.understanding_build_id = (
                completed_build["id"] if completed_build is not None else None
            )

            summary.purpose = _load_purpose(conn, system_id, snapshot_id)
            summary.capabilities = _load_capabilities(conn, system_id, snapshot_id)
            summary.entrypoints = _load_entrypoint_summaries(conn, system_id, snapshot_id)
            summary.major_symbols = _load_major_symbols(conn, system_id, snapshot_id)
            summary.metadata_coverage = _load_metadata_coverage(conn, system_id, snapshot_id)
            summary.gaps = _load_gaps_from_reconciler(conn, system_id, snapshot_id)
            _attach_gap_triage(conn, system_id, snapshot_id, summary.gaps)
            _attach_issue_drafts(conn, system_id, summary.gaps)
            summary.gap_summary = _compute_gap_summary(_open_gaps(summary.gaps))

            purpose_defined = _purpose_defined_from_understanding_status(
                evaluate_understanding(conn, system_id, snapshot_id, purpose=True)
            )

        # Issue #94/#275: manual + AI purpose provenance views and the latest
        # human confirmation. purpose_views is snapshot-independent for its
        # manual entry, so this is computed regardless of whether a ready
        # snapshot exists (summary.snapshot_id is None when it doesn't).
        summary.purpose_views = _load_purpose_views(conn, system_id, summary.snapshot_id)
        summary.purpose_confirmation = _load_purpose_confirmation(
            conn, system_id, summary.snapshot_id
        )

        # Issue #238/#239: these id lists used to also feed the deprecated
        # `_build_next_actions` ("Review probe plan" / "Generate / validate
        # probe patch" / "Review experiment decision"); that projection is
        # gone, but the lists (and their lengths) are still the Issue #202
        # stage-status counts below, so the queries stay.
        proposed_plan_ids, approved_plan_ids_without_patch, approved_plan_total = (
            _load_pending_plan_action_ids(conn, system_id)
        )
        undecided_experiment_ids = _load_undecided_completed_experiment_ids(conn, system_id)

        # Issue #202: stage status counts reuse the id lists above and add
        # the small set of totals not already collected elsewhere.
        total_plan_count = _load_total_probe_plan_count(conn, system_id)
        total_experiment_count = _load_total_experiment_count(conn, system_id)
        decided_experiment_count = _load_decided_completed_experiment_count(conn, system_id)
        validated_plan_count = approved_plan_total - len(approved_plan_ids_without_patch)
        entrypoint_count = (
            summary.metadata_coverage.entrypoint_count if summary.metadata_coverage else 0
        )

        summary.stages = _derive_stage_statuses(
            pipeline,
            purpose_defined,
            summary.capabilities,
            len(_open_gaps(summary.gaps)),
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

        # Issue #203: gap-count trend is a plain SELECT against this same
        # connection (system_understanding_gap_history).
        summary.gap_trend = _load_gap_trend(conn, system_id)

        # Issue #240: build the success summary server-side (Japanese) when
        # every pipeline step is complete, so the Dashboard renders it
        # verbatim instead of assembling its own English string.
        if pipeline and all(s.status == "complete" for s in pipeline):
            coverage = summary.metadata_coverage
            summary.success_summary = state_messages.success_summary(
                done=len(pipeline),
                total=len(pipeline),
                symbol_count=coverage.symbol_count if coverage else 0,
                entrypoint_count=coverage.entrypoint_count if coverage else 0,
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
