"""Evidence-backed understanding graph from documentation claims (Issue #79).

Merges chunk-level documentation claims into a coherent graph structure
representing System Purpose candidates, Core Capabilities, Elements,
Supporting Elements, API Boundaries, Probe Flows, Open Questions, and
conflicts. Every graph node retains provenance and evidence.

This module is deterministic: same claim set produces same graph. No LLM
calls are made here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .documentation_claim_scanner import ChunkScanResult, DocumentationClaim, ClaimEvidence

NODE_TYPES = {
    "system_purpose",
    "core_capability",
    "capability_element",
    "supporting_element",
    "api_boundary",
    "probe_flow",
    "open_question",
    "conflict",
}


@dataclass
class EvidenceRef:
    path: str
    start_line: int
    end_line: int
    chunk_id: str
    confidence: float
    summary: str


@dataclass
class GraphNode:
    node_id: str
    node_type: str
    name: str
    summary: str
    evidence: List[EvidenceRef]
    confidence: float
    mentioned_apis: List[str] = field(default_factory=list)
    mentioned_symbols: List[str] = field(default_factory=list)
    children: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None
    conflict_with: Optional[str] = None
    is_weak: bool = False


@dataclass
class UnderstandingGraph:
    nodes: Dict[str, GraphNode]
    claim_count: int
    valid_claim_count: int
    confidence_summary: Dict[str, float]
    conflicts: List[Tuple[str, str]]
    weak_nodes: List[str]
    source_hash: str


def _node_id(node_type: str, name: str) -> str:
    key = f"{node_type}:{name}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _normalize_name(summary: str) -> str:
    """Create a stable name from a claim summary for dedup."""
    normalized = summary.strip().lower()
    if len(normalized) > 100:
        normalized = normalized[:100]
    return normalized


def _merge_evidence(existing: List[EvidenceRef], new: List[EvidenceRef]) -> List[EvidenceRef]:
    """Merge evidence lists, deduplicating by (path, start_line, end_line)."""
    seen: Set[Tuple[str, int, int]] = set()
    merged: List[EvidenceRef] = []
    for e in existing + new:
        key = (e.path, e.start_line, e.end_line)
        if key not in seen:
            seen.add(key)
            merged.append(e)
    return merged


def _recalculate_confidence(evidence: List[EvidenceRef]) -> float:
    """Aggregate confidence from multiple evidence sources."""
    if not evidence:
        return 0.0
    max_conf = max(e.confidence for e in evidence)
    count_bonus = min(len(evidence) * 0.05, 0.2)
    return min(max_conf + count_bonus, 1.0)


def _detect_conflicts(nodes: Dict[str, GraphNode]) -> List[Tuple[str, str]]:
    """Detect conflicting claims within the same node type."""
    conflicts: List[Tuple[str, str]] = []
    by_type: Dict[str, List[str]] = defaultdict(list)
    for nid, node in nodes.items():
        by_type[node.node_type].append(nid)

    purpose_nodes = sorted(by_type.get("system_purpose", []))
    if len(purpose_nodes) > 1:
        for i in range(len(purpose_nodes)):
            for j in range(i + 1, len(purpose_nodes)):
                pair = tuple(sorted([purpose_nodes[i], purpose_nodes[j]]))
                conflicts.append(pair)

    return conflicts


def _is_similar_name(name1: str, name2: str) -> bool:
    """Check if two names are similar enough to merge."""
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()
    if n1 == n2:
        return True
    if n1 in n2 or n2 in n1:
        return True
    words1 = set(n1.split())
    words2 = set(n2.split())
    if words1 and words2:
        overlap = len(words1 & words2) / max(len(words1), len(words2))
        if overlap > 0.7:
            return True
    return False


def build_understanding_graph(
    scan_results: List[ChunkScanResult],
    source_hash: Optional[str] = None,
) -> UnderstandingGraph:
    """Build a deterministic understanding graph from claim scan results.

    Steps:
    1. Collect all valid claims.
    2. Group by (claim_type, normalized_name) for dedup.
    3. Merge evidence and recalculate confidence.
    4. Detect conflicts (multiple system_purpose, etc.).
    5. Mark weak nodes (low confidence, single evidence).
    6. Generate parent-child candidates.
    """
    all_claims: List[Tuple[DocumentationClaim, str]] = []
    total_claims = 0
    valid_claims = 0

    sorted_results = sorted(scan_results, key=lambda r: r.chunk_id)
    for result in sorted_results:
        sorted_claims = sorted(
            result.claims,
            key=lambda c: (c.claim_type, c.summary, c.evidence.path, c.evidence.start_line),
        )
        for claim in sorted_claims:
            total_claims += 1
            if claim.is_valid:
                valid_claims += 1
                all_claims.append((claim, result.chunk_id))

    nodes: Dict[str, GraphNode] = {}
    name_map: Dict[Tuple[str, str], str] = {}

    for claim, chunk_id in all_claims:
        name = _normalize_name(claim.summary)
        node_type = claim.claim_type

        if node_type not in NODE_TYPES:
            if node_type in ("risk", "mismatch_hint", "implementation_note"):
                node_type = "open_question"
            else:
                continue

        merge_key = None
        for (existing_type, existing_name), existing_id in name_map.items():
            if existing_type == node_type and _is_similar_name(existing_name, name):
                merge_key = (existing_type, existing_name)
                break

        if merge_key and merge_key in name_map:
            existing_node_id = name_map[merge_key]
            node = nodes[existing_node_id]
            new_evidence = EvidenceRef(
                path=claim.evidence.path,
                start_line=claim.evidence.start_line,
                end_line=claim.evidence.end_line,
                chunk_id=chunk_id,
                confidence=claim.confidence,
                summary=claim.summary,
            )
            node.evidence = _merge_evidence(node.evidence, [new_evidence])
            node.confidence = _recalculate_confidence(node.evidence)
            node.mentioned_apis = sorted(set(node.mentioned_apis + claim.mentioned_apis))
            node.mentioned_symbols = sorted(set(node.mentioned_symbols + claim.mentioned_symbols))
        else:
            nid = _node_id(node_type, name)
            evidence = EvidenceRef(
                path=claim.evidence.path,
                start_line=claim.evidence.start_line,
                end_line=claim.evidence.end_line,
                chunk_id=chunk_id,
                confidence=claim.confidence,
                summary=claim.summary,
            )
            nodes[nid] = GraphNode(
                node_id=nid,
                node_type=node_type,
                name=name,
                summary=claim.summary,
                evidence=[evidence],
                confidence=claim.confidence,
                mentioned_apis=list(claim.mentioned_apis),
                mentioned_symbols=list(claim.mentioned_symbols),
            )
            name_map[(node_type, name)] = nid

    _generate_parent_child(nodes)

    conflicts = _detect_conflicts(nodes)
    for nid1, nid2 in conflicts:
        conflict_id = _node_id("conflict", f"{nid1}:{nid2}")
        n1 = nodes[nid1]
        n2 = nodes[nid2]
        nodes[conflict_id] = GraphNode(
            node_id=conflict_id,
            node_type="conflict",
            name=f"Conflict: {n1.name[:50]} vs {n2.name[:50]}",
            summary=f"Conflicting {n1.node_type} claims detected",
            evidence=n1.evidence + n2.evidence,
            confidence=0.0,
            conflict_with=nid1,
        )

    weak_nodes: List[str] = []
    for nid, node in nodes.items():
        if node.node_type == "conflict":
            continue
        if node.confidence < 0.5 or len(node.evidence) < 2:
            node.is_weak = True
            weak_nodes.append(nid)

    confidence_summary: Dict[str, float] = {}
    type_counts: Dict[str, List[float]] = defaultdict(list)
    for node in nodes.values():
        if node.node_type != "conflict":
            type_counts[node.node_type].append(node.confidence)
    for nt, confs in type_counts.items():
        confidence_summary[nt] = sum(confs) / len(confs) if confs else 0.0

    if source_hash is None:
        hash_parts = []
        for result in sorted_results:
            claims_data = [
                (c.claim_type, c.summary, c.evidence.path, c.evidence.start_line,
                 c.evidence.end_line, c.confidence)
                for c in sorted(
                    result.claims,
                    key=lambda c: (c.claim_type, c.summary, c.evidence.path, c.evidence.start_line),
                )
            ]
            hash_parts.append((
                result.chunk_id,
                result.chunk_content_hash,
                result.prompt_version,
                result.schema_version,
                claims_data,
            ))
        hash_input = json.dumps(hash_parts, sort_keys=True, default=str)
        source_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    return UnderstandingGraph(
        nodes=nodes,
        claim_count=total_claims,
        valid_claim_count=valid_claims,
        confidence_summary=confidence_summary,
        conflicts=conflicts,
        weak_nodes=weak_nodes,
        source_hash=source_hash,
    )


def _generate_parent_child(nodes: Dict[str, GraphNode]) -> None:
    """Generate parent-child relationships between graph nodes.

    Hierarchy: system_purpose -> core_capability -> capability_element -> supporting_element
    Links are based on shared mentioned symbols/APIs.
    """
    purposes = [n for n in nodes.values() if n.node_type == "system_purpose"]
    capabilities = [n for n in nodes.values() if n.node_type == "core_capability"]
    elements = [n for n in nodes.values() if n.node_type == "capability_element"]
    supporting = [n for n in nodes.values() if n.node_type == "supporting_element"]

    for cap in capabilities:
        if purposes:
            best_parent = purposes[0]
            cap.parent_id = best_parent.node_id
            if cap.node_id not in best_parent.children:
                best_parent.children.append(cap.node_id)

    for elem in elements:
        best_parent = _find_best_parent(elem, capabilities)
        if best_parent:
            elem.parent_id = best_parent.node_id
            if elem.node_id not in best_parent.children:
                best_parent.children.append(elem.node_id)

    for sup in supporting:
        best_parent = _find_best_parent(sup, elements) or _find_best_parent(sup, capabilities)
        if best_parent:
            sup.parent_id = best_parent.node_id
            if sup.node_id not in best_parent.children:
                best_parent.children.append(sup.node_id)


def _find_best_parent(node: GraphNode, candidates: List[GraphNode]) -> Optional[GraphNode]:
    """Find the best parent among candidates based on shared symbols/APIs."""
    if not candidates:
        return None

    best: Optional[GraphNode] = None
    best_score = 0

    node_symbols = set(node.mentioned_symbols)
    node_apis = set(node.mentioned_apis)

    for candidate in candidates:
        cand_symbols = set(candidate.mentioned_symbols)
        cand_apis = set(candidate.mentioned_apis)
        overlap = len(node_symbols & cand_symbols) + len(node_apis & cand_apis)
        if overlap > best_score:
            best_score = overlap
            best = candidate

    if best is None and candidates:
        best = candidates[0]

    return best


def graph_to_dict(graph: UnderstandingGraph) -> Dict[str, Any]:
    """Serialize graph to a JSON-compatible dict for persistence."""
    node_dicts = {}
    for nid, n in graph.nodes.items():
        node_dicts[nid] = {
            "node_id": n.node_id,
            "node_type": n.node_type,
            "name": n.name,
            "summary": n.summary,
            "evidence": [
                {
                    "path": e.path,
                    "start_line": e.start_line,
                    "end_line": e.end_line,
                    "chunk_id": e.chunk_id,
                    "confidence": e.confidence,
                    "summary": e.summary,
                }
                for e in n.evidence
            ],
            "confidence": n.confidence,
            "mentioned_apis": n.mentioned_apis,
            "mentioned_symbols": n.mentioned_symbols,
            "children": n.children,
            "parent_id": n.parent_id,
            "conflict_with": n.conflict_with,
            "is_weak": n.is_weak,
        }
    return {
        "nodes": node_dicts,
        "claim_count": graph.claim_count,
        "valid_claim_count": graph.valid_claim_count,
        "confidence_summary": graph.confidence_summary,
        "conflicts": graph.conflicts,
        "weak_nodes": graph.weak_nodes,
        "source_hash": graph.source_hash,
    }


def save_graph_snapshot(
    conn: sqlite3.Connection,
    system_id: int,
    graph: UnderstandingGraph,
    snapshot_id: Optional[int] = None,
) -> int:
    """Persist a graph snapshot to the database."""
    now = time.time()
    graph_json = json.dumps(graph_to_dict(graph), ensure_ascii=False)
    cur = conn.execute(
        """INSERT INTO understanding_graph_snapshots
            (system_id, snapshot_id, graph_json, source_hash, claim_count,
             confidence_summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id,
            snapshot_id,
            graph_json,
            graph.source_hash,
            graph.claim_count,
            json.dumps(graph.confidence_summary),
            now,
        ),
    )
    return cur.lastrowid


def load_graph_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: int,
) -> Optional[Dict[str, Any]]:
    """Load a graph snapshot from the database."""
    row = conn.execute(
        "SELECT * FROM understanding_graph_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "system_id": row["system_id"],
        "snapshot_id": row["snapshot_id"] if "snapshot_id" in row.keys() else None,
        "graph": json.loads(row["graph_json"]),
        "source_hash": row["source_hash"],
        "claim_count": row["claim_count"],
        "confidence_summary": json.loads(row["confidence_summary"]),
        "created_at": row["created_at"],
    }
