"""Reconcile documentation understanding graph with code intelligence (Issue #80).

Compares documentation-derived claims with existing code intelligence facts:
code symbols, entrypoints, API scan results, source-authored metadata,
capability hierarchy, drift detection, and flow graph data.

Produces structured gap mappings with evidence from both sides.

probe-agent:
  role: Documentation-to-code gap reconciler
  capability: docs-code-reconciliation
  element_type: core
  consumers: [system-understanding, capability-mapping]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify that gaps are produced when documentation claims have no matching code symbols and vice versa.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .understanding_graph import GraphNode, UnderstandingGraph, EvidenceRef

GAP_TYPES = {
    "docs_only",
    "code_only",
    "source_doc_mismatch",
    "stale_explanation",
    "ambiguous_ownership",
    "unclassified_entrypoint",
    "missing_probe_flow",
    "missing_evidence",
}


@dataclass
class CodeEvidence:
    source: str
    path: Optional[str] = None
    qualified_name: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    route_method: Optional[str] = None
    route_path: Optional[str] = None
    symbol_id: Optional[int] = None
    entrypoint_id: Optional[int] = None
    metadata_id: Optional[int] = None


@dataclass
class ReconciliationMapping:
    node_id: Optional[str]
    node_type: Optional[str]
    node_name: Optional[str]
    gap_type: Optional[str]
    doc_evidence: List[EvidenceRef]
    code_evidence: List[CodeEvidence]
    confidence: float = 0.0
    notes: str = ""


@dataclass
class ReconciliationResult:
    system_id: int
    snapshot_id: int
    mappings: List[ReconciliationMapping]
    gaps: List[ReconciliationMapping]
    matched_count: int = 0
    docs_only_count: int = 0
    code_only_count: int = 0
    mismatch_count: int = 0


def _normalize_api(api_str: str) -> Tuple[str, str]:
    """Normalize an API reference to (method, path)."""
    parts = api_str.strip().split(None, 1)
    if len(parts) == 2:
        return parts[0].upper(), parts[1].rstrip("/")
    return "", api_str.rstrip("/")


def _normalize_symbol(symbol: str) -> str:
    """Normalize a symbol name for matching."""
    return symbol.strip().lower().replace("-", "_")


def _load_code_symbols(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, path, qualified_name, kind, start_line, end_line,
                  route_path, route_method, component_id, is_test
           FROM code_symbols
           WHERE system_id = ? AND snapshot_id = ?
           ORDER BY path, start_line""",
        (system_id, snapshot_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_entrypoints(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, entrypoint_type, entrypoint_id, category, label,
                  handler_path, handler_qualified_name, line_start, line_end,
                  route_method, route_path, handler_symbol_id
           FROM code_entrypoints
           WHERE system_id = ? AND snapshot_id = ?
           ORDER BY handler_path, line_start""",
        (system_id, snapshot_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_source_metadata(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, symbol_id, path, qualified_name, role, capability,
                  element_type, system_purpose, operation_kind, probe_value
           FROM symbol_source_metadata
           WHERE system_id = ? AND snapshot_id = ?""",
        (system_id, snapshot_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_capability_nodes(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, node_type, name, summary, symbol_id, path,
                  qualified_name, classification
           FROM capability_hierarchy_nodes
           WHERE system_id = ? AND snapshot_id = ?""",
        (system_id, snapshot_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_drift_info(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, path, qualified_name, drift_status, drift_reason
           FROM explanation_refresh_proposals
           WHERE system_id = ? AND target_snapshot_id = ? AND status = 'proposed'
           ORDER BY id DESC""",
        (system_id, snapshot_id),
    ).fetchall()
    return [dict(r) for r in rows]


def _entrypoints_by_path(entrypoints: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by_path: Dict[str, List[Dict[str, Any]]] = {}
    for ep in entrypoints:
        ep_path = (ep.get("route_path") or "").rstrip("/")
        if ep_path:
            by_path.setdefault(ep_path, []).append(ep)
    return by_path


def _match_api_to_entrypoints(
    mentioned_apis: List[str],
    entrypoints: "List[Dict[str, Any]] | Dict[str, List[Dict[str, Any]]]",
) -> List[CodeEvidence]:
    """Match documented API paths to discovered entrypoints.

    Accepts the entrypoint list or a prebuilt path index (`_entrypoints_by_path`)
    so a reconciliation over thousands of graph nodes does not rescan every
    entrypoint per node. Order of matches is the entrypoint order, as before.
    """
    by_path = entrypoints if isinstance(entrypoints, dict) else _entrypoints_by_path(entrypoints)
    matches: List[CodeEvidence] = []
    for api in mentioned_apis:
        method, path = _normalize_api(api)
        if not path:
            continue
        for ep in by_path.get(path.rstrip("/"), []):
            ep_method = (ep.get("route_method") or "").upper()
            if method and method != ep_method:
                continue
            matches.append(CodeEvidence(
                source="entrypoint",
                path=ep["handler_path"],
                qualified_name=ep["handler_qualified_name"],
                start_line=ep["line_start"],
                end_line=ep["line_end"],
                route_method=ep_method,
                route_path=(ep.get("route_path") or "").rstrip("/"),
                entrypoint_id=ep["id"],
            ))
    return matches


class _SymbolIndex:
    """Normalized symbol lookups built once per reconciliation.

    `_match_symbols_to_code` used to rebuild its index for every graph node.
    With ~12k symbols and ~5k nodes that was 65M normalizations, and
    `/system-state` (which reconciles on every page load) took 25 s while
    holding the process-wide DB lock, stalling every other request.

    `match` keeps the previous semantics exactly: an exact normalized match,
    else the FIRST symbol in index order whose name is a dotted suffix of the
    reference or has the reference as a dotted suffix.
    """

    def __init__(self, code_symbols: List[Dict[str, Any]]):
        self.exact: Dict[str, Dict[str, Any]] = {}
        for s in code_symbols:
            self.exact[_normalize_symbol(s["qualified_name"])] = s
        # Position of each surviving key in dict order (later duplicates
        # overwrite the value but keep the first key position).
        self.order: Dict[str, int] = {q: i for i, q in enumerate(self.exact)}
        # Every proper dotted suffix of a symbol name -> first symbol name having it.
        self.by_suffix: Dict[str, str] = {}
        for qname in self.exact:
            parts = qname.split(".")
            for i in range(1, len(parts)):
                self.by_suffix.setdefault(".".join(parts[i:]), qname)

    def match(self, ref: str) -> Optional[Dict[str, Any]]:
        norm = _normalize_symbol(ref)
        if norm in self.exact:
            return self.exact[norm]
        candidates = []
        # qname.endswith("." + norm)
        q = self.by_suffix.get(norm)
        if q is not None:
            candidates.append(q)
        # norm.endswith("." + qname)
        parts = norm.split(".")
        for i in range(1, len(parts)):
            suffix = ".".join(parts[i:])
            if suffix in self.exact:
                candidates.append(suffix)
        if not candidates:
            return None
        return self.exact[min(candidates, key=self.order.__getitem__)]


def _match_symbols_to_code(
    mentioned_symbols: List[str],
    code_symbols: "List[Dict[str, Any]] | _SymbolIndex",
) -> List[CodeEvidence]:
    """Match documented symbol references to code symbols."""
    index = code_symbols if isinstance(code_symbols, _SymbolIndex) else _SymbolIndex(code_symbols)
    matches: List[CodeEvidence] = []
    for sym_ref in mentioned_symbols:
        s = index.match(sym_ref)
        if s is not None:
            matches.append(CodeEvidence(
                source="code_symbol",
                path=s["path"],
                qualified_name=s["qualified_name"],
                start_line=s["start_line"],
                end_line=s["end_line"],
                symbol_id=s["id"],
            ))
    return matches


def reconcile(
    conn: sqlite3.Connection,
    system_id: int,
    snapshot_id: int,
    graph: UnderstandingGraph,
) -> ReconciliationResult:
    """Reconcile documentation graph with code intelligence facts."""
    code_symbols = _load_code_symbols(conn, system_id, snapshot_id)
    entrypoints = _load_entrypoints(conn, system_id, snapshot_id)
    source_metadata = _load_source_metadata(conn, system_id, snapshot_id)
    capability_nodes = _load_capability_nodes(conn, system_id, snapshot_id)
    drift_info = _load_drift_info(conn, system_id, snapshot_id)

    mappings: List[ReconciliationMapping] = []
    gaps: List[ReconciliationMapping] = []
    matched_node_ids: Set[str] = set()
    matched_symbol_ids: Set[int] = set()
    matched_entrypoint_ids: Set[int] = set()
    symbol_index = _SymbolIndex(code_symbols)
    entrypoint_by_id = {e["id"]: e for e in entrypoints}
    entrypoints_by_path = _entrypoints_by_path(entrypoints)
    metadata_symbol_ids = {m["symbol_id"] for m in source_metadata}
    classified_symbol_ids = {
        cn.get("symbol_id") for cn in capability_nodes if cn.get("classification") == "classified"
    }

    from .understanding_graph import SOURCE_NODE_TYPES

    for nid, node in graph.nodes.items():
        if node.node_type == "conflict":
            continue
        # External-source claims describe the world, not this code base:
        # an indicator in a public plan with no matching symbol is not a
        # docs-only gap (vision-to-feature-research.md VF-D1).
        if node.node_type in SOURCE_NODE_TYPES:
            continue

        api_matches = _match_api_to_entrypoints(node.mentioned_apis, entrypoints_by_path)
        sym_matches = _match_symbols_to_code(node.mentioned_symbols, symbol_index)
        all_code_evidence = api_matches + sym_matches

        if all_code_evidence:
            matched_node_ids.add(nid)
            for ce in all_code_evidence:
                if ce.symbol_id:
                    matched_symbol_ids.add(ce.symbol_id)
                if ce.entrypoint_id:
                    matched_entrypoint_ids.add(ce.entrypoint_id)
                    ep_rec = entrypoint_by_id.get(ce.entrypoint_id)
                    if ep_rec and ep_rec.get("handler_symbol_id"):
                        matched_symbol_ids.add(ep_rec["handler_symbol_id"])

            mappings.append(ReconciliationMapping(
                node_id=nid,
                node_type=node.node_type,
                node_name=node.name,
                gap_type=None,
                doc_evidence=node.evidence,
                code_evidence=all_code_evidence,
                confidence=node.confidence,
            ))
        else:
            gaps.append(ReconciliationMapping(
                node_id=nid,
                node_type=node.node_type,
                node_name=node.name,
                gap_type="docs_only",
                doc_evidence=node.evidence,
                code_evidence=[],
                confidence=node.confidence,
                notes="Documented claim with no matching code symbol or API",
            ))

    for sym in code_symbols:
        if sym["id"] not in matched_symbol_ids:
            if sym.get("is_test"):
                continue
            if sym["kind"] in ("function", "class", "method"):
                gaps.append(ReconciliationMapping(
                    node_id=None,
                    node_type=None,
                    node_name=sym["qualified_name"],
                    gap_type="code_only",
                    doc_evidence=[],
                    code_evidence=[CodeEvidence(
                        source="code_symbol",
                        path=sym["path"],
                        qualified_name=sym["qualified_name"],
                        start_line=sym["start_line"],
                        end_line=sym["end_line"],
                        symbol_id=sym["id"],
                    )],
                    notes="Code symbol with no documentation claim",
                ))

    for ep in entrypoints:
        if ep["id"] not in matched_entrypoint_ids:
            ep_type = ep.get("entrypoint_type", "")
            handler_sym_id = ep.get("handler_symbol_id")
            has_metadata = handler_sym_id and handler_sym_id in metadata_symbol_ids
            has_classification = handler_sym_id and handler_sym_id in classified_symbol_ids

            if not has_metadata and not has_classification:
                gaps.append(ReconciliationMapping(
                    node_id=None,
                    node_type=None,
                    node_name=ep.get("label", ep["handler_qualified_name"]),
                    gap_type="unclassified_entrypoint",
                    doc_evidence=[],
                    code_evidence=[CodeEvidence(
                        source="entrypoint",
                        path=ep["handler_path"],
                        qualified_name=ep["handler_qualified_name"],
                        start_line=ep["line_start"],
                        end_line=ep["line_end"],
                        route_method=ep.get("route_method"),
                        route_path=ep.get("route_path"),
                        entrypoint_id=ep["id"],
                    )],
                    notes=f"Unclassified {ep_type} entrypoint without documentation",
                ))

    for meta in source_metadata:
        for nid, node in graph.nodes.items():
            if node.node_type in ("system_purpose", "core_capability", "capability_element"):
                node_cap = node.name.lower()
                meta_cap = (meta.get("capability") or "").lower()
                meta_role = (meta.get("role") or "").lower()
                if meta_cap and node_cap and meta_cap != node_cap:
                    if _normalize_symbol(meta["qualified_name"]) in [
                        _normalize_symbol(s) for s in node.mentioned_symbols
                    ]:
                        gaps.append(ReconciliationMapping(
                            node_id=nid,
                            node_type=node.node_type,
                            node_name=node.name,
                            gap_type="source_doc_mismatch",
                            doc_evidence=node.evidence,
                            code_evidence=[CodeEvidence(
                                source="source_metadata",
                                path=meta["path"],
                                qualified_name=meta["qualified_name"],
                                metadata_id=meta["id"],
                                symbol_id=meta.get("symbol_id"),
                            )],
                            notes=f"Source metadata capability '{meta_cap}' differs from doc claim",
                        ))

    for drift in drift_info:
        for nid, node in graph.nodes.items():
            if _normalize_symbol(drift.get("qualified_name", "")) in [
                _normalize_symbol(s) for s in node.mentioned_symbols
            ]:
                gaps.append(ReconciliationMapping(
                    node_id=nid,
                    node_type=node.node_type,
                    node_name=node.name,
                    gap_type="stale_explanation",
                    doc_evidence=node.evidence,
                    code_evidence=[CodeEvidence(
                        source="drift",
                        path=drift.get("path"),
                        qualified_name=drift.get("qualified_name"),
                    )],
                    notes=f"Drift detected: {drift.get('drift_reason', '')}",
                ))

    docs_only = sum(1 for g in gaps if g.gap_type == "docs_only")
    code_only = sum(1 for g in gaps if g.gap_type in ("code_only", "unclassified_entrypoint"))
    mismatches = sum(1 for g in gaps if g.gap_type in ("source_doc_mismatch", "stale_explanation"))

    return ReconciliationResult(
        system_id=system_id,
        snapshot_id=snapshot_id,
        mappings=mappings,
        gaps=gaps,
        matched_count=len(mappings),
        docs_only_count=docs_only,
        code_only_count=code_only,
        mismatch_count=mismatches,
    )
