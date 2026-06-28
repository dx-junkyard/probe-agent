"""Tests for understanding graph construction (Issue #79)."""

import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.documentation_claim_scanner import (
    ChunkScanResult,
    ClaimEvidence,
    DocumentationClaim,
    PROMPT_VERSION,
    SCHEMA_VERSION,
)
from app.understanding_graph import (
    GraphNode,
    UnderstandingGraph,
    build_understanding_graph,
    graph_to_dict,
    save_graph_snapshot,
    load_graph_snapshot,
    _is_similar_name,
    _merge_evidence,
    _recalculate_confidence,
    EvidenceRef,
)
from app.db import SCHEMA


def _claim(
    claim_type="system_purpose",
    summary="The system does X",
    path="README.md",
    start=1,
    end=5,
    confidence=0.9,
    apis=None,
    symbols=None,
):
    return DocumentationClaim(
        claim_type=claim_type,
        summary=summary,
        evidence=ClaimEvidence(path=path, start_line=start, end_line=end),
        confidence=confidence,
        mentioned_apis=apis or [],
        mentioned_symbols=symbols or [],
    )


def _scan_result(claims, chunk_id="c1", content_hash="h1"):
    return ChunkScanResult(
        chunk_id=chunk_id,
        chunk_content_hash=content_hash,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        claims=claims,
    )


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    now = time.time()
    conn.execute(
        "INSERT INTO systems (id, name, created_at, updated_at) VALUES (1, 'test', ?, ?)",
        (now, now),
    )
    return conn


class TestBuildGraph:
    def test_empty_results(self):
        graph = build_understanding_graph([])
        assert len(graph.nodes) == 0
        assert graph.claim_count == 0

    def test_single_claim(self):
        results = [_scan_result([_claim()])]
        graph = build_understanding_graph(results)
        assert len(graph.nodes) == 1
        node = list(graph.nodes.values())[0]
        assert node.node_type == "system_purpose"
        assert len(node.evidence) == 1

    def test_duplicate_claims_merged(self):
        c1 = _claim(summary="The system provides evaluation", path="README.md", start=1, end=5)
        c2 = _claim(summary="The system provides evaluation", path="docs/guide.md", start=10, end=15)
        results = [
            _scan_result([c1], chunk_id="c1", content_hash="h1"),
            _scan_result([c2], chunk_id="c2", content_hash="h2"),
        ]
        graph = build_understanding_graph(results)
        purpose_nodes = [n for n in graph.nodes.values() if n.node_type == "system_purpose"]
        assert len(purpose_nodes) == 1
        assert len(purpose_nodes[0].evidence) == 2

    def test_evidence_aggregation(self):
        c1 = _claim(summary="System does X", confidence=0.7)
        c2 = _claim(summary="System does X", confidence=0.8, path="docs/a.md", start=10, end=15)
        results = [
            _scan_result([c1], chunk_id="c1"),
            _scan_result([c2], chunk_id="c2", content_hash="h2"),
        ]
        graph = build_understanding_graph(results)
        node = list(graph.nodes.values())[0]
        assert node.confidence > 0.8

    def test_conflict_detection(self):
        c1 = _claim(claim_type="system_purpose", summary="System is for evaluation")
        c2 = _claim(
            claim_type="system_purpose",
            summary="A completely different system for deployment",
            path="other.md",
            start=20,
            end=30,
        )
        results = [
            _scan_result([c1], chunk_id="c1"),
            _scan_result([c2], chunk_id="c2", content_hash="h2"),
        ]
        graph = build_understanding_graph(results)
        assert len(graph.conflicts) > 0
        conflict_nodes = [n for n in graph.nodes.values() if n.node_type == "conflict"]
        assert len(conflict_nodes) > 0

    def test_weak_evidence_detection(self):
        c = _claim(confidence=0.3)
        results = [_scan_result([c])]
        graph = build_understanding_graph(results)
        assert len(graph.weak_nodes) > 0
        node = list(graph.nodes.values())[0]
        assert node.is_weak

    def test_multiple_node_types(self):
        claims = [
            _claim(claim_type="system_purpose", summary="System for evaluation"),
            _claim(claim_type="core_capability", summary="Trace recording"),
            _claim(claim_type="capability_element", summary="Shadow mode comparison"),
            _claim(claim_type="api_boundary", summary="GET /traces endpoint", apis=["GET /traces"]),
            _claim(claim_type="open_question", summary="How does auth work?"),
        ]
        results = [_scan_result(claims)]
        graph = build_understanding_graph(results)
        types = {n.node_type for n in graph.nodes.values()}
        assert "system_purpose" in types
        assert "core_capability" in types
        assert "capability_element" in types
        assert "api_boundary" in types
        assert "open_question" in types

    def test_parent_child_generation(self):
        claims = [
            _claim(claim_type="system_purpose", summary="Evaluation platform"),
            _claim(claim_type="core_capability", summary="Trace recording"),
            _claim(claim_type="capability_element", summary="Shadow comparison element"),
        ]
        results = [_scan_result(claims)]
        graph = build_understanding_graph(results)
        caps = [n for n in graph.nodes.values() if n.node_type == "core_capability"]
        assert len(caps) == 1
        assert caps[0].parent_id is not None

    def test_deterministic_output(self):
        claims = [
            _claim(claim_type="system_purpose", summary="System A"),
            _claim(claim_type="core_capability", summary="Cap B"),
        ]
        results = [_scan_result(claims)]
        g1 = build_understanding_graph(results)
        g2 = build_understanding_graph(results)
        assert g1.source_hash == g2.source_hash
        assert set(g1.nodes.keys()) == set(g2.nodes.keys())
        for nid in g1.nodes:
            assert g1.nodes[nid].confidence == g2.nodes[nid].confidence

    def test_invalid_claims_excluded(self):
        valid = _claim(summary="Valid claim")
        invalid = DocumentationClaim(
            claim_type="system_purpose",
            summary="Invalid claim",
            evidence=ClaimEvidence(path="x.md", start_line=1, end_line=5),
            confidence=0.0,
            is_valid=False,
            invalid_reason="test",
        )
        results = [_scan_result([valid, invalid])]
        graph = build_understanding_graph(results)
        assert graph.claim_count == 2
        assert graph.valid_claim_count == 1


class TestSimilarName:
    def test_identical(self):
        assert _is_similar_name("hello world", "hello world")

    def test_case_insensitive(self):
        assert _is_similar_name("Hello World", "hello world")

    def test_substring(self):
        assert _is_similar_name("trace recording", "trace recording capability")

    def test_high_word_overlap(self):
        assert _is_similar_name("system evaluation platform", "evaluation platform system")

    def test_different(self):
        assert not _is_similar_name("evaluation", "deployment")


class TestMergeEvidence:
    def test_dedup(self):
        e1 = EvidenceRef(path="a.md", start_line=1, end_line=5, chunk_id="c1", confidence=0.9, summary="s")
        e2 = EvidenceRef(path="a.md", start_line=1, end_line=5, chunk_id="c1", confidence=0.9, summary="s")
        merged = _merge_evidence([e1], [e2])
        assert len(merged) == 1

    def test_different_ranges(self):
        e1 = EvidenceRef(path="a.md", start_line=1, end_line=5, chunk_id="c1", confidence=0.9, summary="s")
        e2 = EvidenceRef(path="a.md", start_line=10, end_line=15, chunk_id="c2", confidence=0.8, summary="s")
        merged = _merge_evidence([e1], [e2])
        assert len(merged) == 2


class TestConfidence:
    def test_single_evidence(self):
        e = EvidenceRef(path="a.md", start_line=1, end_line=5, chunk_id="c1", confidence=0.8, summary="s")
        assert _recalculate_confidence([e]) == min(0.8 + 0.05, 1.0)

    def test_multiple_evidence_bonus(self):
        evs = [
            EvidenceRef(path="a.md", start_line=i, end_line=i + 5, chunk_id=f"c{i}", confidence=0.7, summary="s")
            for i in range(5)
        ]
        conf = _recalculate_confidence(evs)
        assert conf > 0.7
        assert conf <= 1.0


class TestGraphPersistence:
    def test_save_and_load(self):
        conn = _make_db()
        claims = [_claim(summary="System for testing")]
        results = [_scan_result(claims)]
        graph = build_understanding_graph(results)

        snap_id = save_graph_snapshot(conn, 1, graph)
        loaded = load_graph_snapshot(conn, snap_id)
        assert loaded is not None
        assert loaded["system_id"] == 1
        assert loaded["claim_count"] == 1
        assert "nodes" in loaded["graph"]

    def test_load_nonexistent(self):
        conn = _make_db()
        assert load_graph_snapshot(conn, 9999) is None

    def test_graph_to_dict(self):
        claims = [
            _claim(claim_type="system_purpose", summary="Test system"),
            _claim(claim_type="core_capability", summary="Testing cap"),
        ]
        results = [_scan_result(claims)]
        graph = build_understanding_graph(results)
        d = graph_to_dict(graph)
        assert "nodes" in d
        assert "claim_count" in d
        assert "confidence_summary" in d
        json.dumps(d)


class TestSnapshotIdPersistence:
    """Finding 1: graph snapshot must be linked to its repository snapshot."""

    def test_save_with_snapshot_id(self):
        conn = _make_db()
        now = time.time()
        conn.execute(
            "INSERT INTO repository_snapshots (id, system_id, repo_path, commit_sha, status, created_at) "
            "VALUES (42, 1, '/repo', 'abc', 'ready', ?)",
            (now,),
        )
        graph = build_understanding_graph([_scan_result([_claim()])])
        gid = save_graph_snapshot(conn, 1, graph, snapshot_id=42)
        loaded = load_graph_snapshot(conn, gid)
        assert loaded["snapshot_id"] == 42

    def test_save_without_snapshot_id(self):
        conn = _make_db()
        graph = build_understanding_graph([_scan_result([_claim()])])
        gid = save_graph_snapshot(conn, 1, graph)
        loaded = load_graph_snapshot(conn, gid)
        assert loaded["snapshot_id"] is None


class TestInputOrderStability:
    """Finding 2: graph must be deterministic regardless of claim input order."""

    def test_swapped_scan_results_same_graph(self):
        claim_a = _claim(claim_type="system_purpose", summary="Purpose A", path="a.md")
        claim_b = _claim(claim_type="system_purpose", summary="Purpose B", path="b.md")
        result_a = _scan_result([claim_a], chunk_id="ca", content_hash="ha")
        result_b = _scan_result([claim_b], chunk_id="cb", content_hash="hb")

        graph_1 = build_understanding_graph([result_a, result_b])
        graph_2 = build_understanding_graph([result_b, result_a])

        assert graph_1.source_hash == graph_2.source_hash
        assert sorted(graph_1.nodes.keys()) == sorted(graph_2.nodes.keys())
        assert graph_1.conflicts == graph_2.conflicts

    def test_swapped_claims_within_result_same_graph(self):
        claim_a = _claim(claim_type="core_capability", summary="Cap A")
        claim_b = _claim(claim_type="core_capability", summary="Cap B")

        graph_1 = build_understanding_graph([_scan_result([claim_a, claim_b])])
        graph_2 = build_understanding_graph([_scan_result([claim_b, claim_a])])

        assert graph_1.source_hash == graph_2.source_hash
        assert sorted(graph_1.nodes.keys()) == sorted(graph_2.nodes.keys())

    def test_conflict_ids_stable_across_order(self):
        claim_a = _claim(claim_type="system_purpose", summary="Purpose X", path="x.md")
        claim_b = _claim(claim_type="system_purpose", summary="Purpose Y", path="y.md")

        graph_1 = build_understanding_graph([
            _scan_result([claim_a], chunk_id="c1", content_hash="h1"),
            _scan_result([claim_b], chunk_id="c2", content_hash="h2"),
        ])
        graph_2 = build_understanding_graph([
            _scan_result([claim_b], chunk_id="c2", content_hash="h2"),
            _scan_result([claim_a], chunk_id="c1", content_hash="h1"),
        ])

        conflict_nodes_1 = {nid for nid, n in graph_1.nodes.items() if n.node_type == "conflict"}
        conflict_nodes_2 = {nid for nid, n in graph_2.nodes.items() if n.node_type == "conflict"}
        assert conflict_nodes_1 == conflict_nodes_2


class TestSourceHashContent:
    """Finding 3: source_hash must reflect claim content, not just chunk IDs."""

    def test_different_claims_same_chunk_different_hash(self):
        claim_a = _claim(summary="System does A")
        claim_b = _claim(summary="System does B")

        graph_a = build_understanding_graph([_scan_result([claim_a])])
        graph_b = build_understanding_graph([_scan_result([claim_b])])

        assert graph_a.source_hash != graph_b.source_hash

    def test_different_confidence_different_hash(self):
        claim_high = _claim(summary="System does X", confidence=0.9)
        claim_low = _claim(summary="System does X", confidence=0.3)

        graph_h = build_understanding_graph([_scan_result([claim_high])])
        graph_l = build_understanding_graph([_scan_result([claim_low])])

        assert graph_h.source_hash != graph_l.source_hash

    def test_same_claims_same_hash(self):
        claims = [_claim(summary="System does X")]
        graph_1 = build_understanding_graph([_scan_result(claims)])
        graph_2 = build_understanding_graph([_scan_result(claims)])
        assert graph_1.source_hash == graph_2.source_hash
