"""Tests for docs-code reconciliation (Issue #80)."""

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
    UnderstandingGraph,
    build_understanding_graph,
    EvidenceRef,
)
from app.docs_code_reconciler import (
    reconcile,
    _normalize_api,
    _normalize_symbol,
    _match_api_to_entrypoints,
)
from app.db import SCHEMA


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    now = time.time()
    conn.execute(
        "INSERT INTO systems (id, name, created_at, updated_at) VALUES (1, 'test', ?, ?)",
        (now, now),
    )
    cur = conn.execute(
        """INSERT INTO repository_snapshots
            (system_id, repo_path, commit_sha, status, file_count, created_at)
        VALUES (1, '/tmp/repo', 'abc123', 'completed', 0, ?)""",
        (now,),
    )
    return conn, cur.lastrowid


def _add_symbol(conn, snapshot_id, path, qname, kind="function", start=1, end=10):
    cur = conn.execute(
        """INSERT INTO code_symbols
            (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line)
        VALUES (?, 1, ?, ?, ?, ?, ?)""",
        (snapshot_id, path, qname, kind, start, end),
    )
    return cur.lastrowid


def _add_entrypoint(conn, snapshot_id, handler_path, handler_qname, route_method="GET", route_path="/api/test", symbol_id=None):
    cur = conn.execute(
        """INSERT INTO code_entrypoints
            (system_id, snapshot_id, entrypoint_type, entrypoint_id, category,
             label, handler_path, handler_qualified_name, line_start, line_end,
             route_method, route_path, handler_symbol_id, confidence, created_at)
        VALUES (1, ?, 'http_route', ?, 'api', ?, ?, ?, 1, 20, ?, ?, ?, 1.0, ?)""",
        (snapshot_id, f"route:{route_method}:{route_path}", handler_qname,
         handler_path, handler_qname, route_method, route_path, symbol_id, time.time()),
    )
    return cur.lastrowid


def _add_source_metadata(conn, snapshot_id, symbol_id, path, qname, capability="cap1"):
    cur = conn.execute(
        """INSERT INTO symbol_source_metadata
            (snapshot_id, system_id, symbol_id, path, qualified_name,
             role, capability, raw_block, origin, start_line, end_line)
        VALUES (?, 1, ?, ?, ?, 'handler', ?, 'block', 'source_authored', 1, 10)""",
        (snapshot_id, symbol_id, path, qname, capability),
    )
    return cur.lastrowid


def _claim(claim_type="system_purpose", summary="claim", apis=None, symbols=None, confidence=0.9):
    return DocumentationClaim(
        claim_type=claim_type,
        summary=summary,
        evidence=ClaimEvidence(path="README.md", start_line=1, end_line=5),
        confidence=confidence,
        mentioned_apis=apis or [],
        mentioned_symbols=symbols or [],
    )


def _scan_result(claims, chunk_id="c1"):
    return ChunkScanResult(
        chunk_id=chunk_id,
        chunk_content_hash="h1",
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        claims=claims,
    )


def _build_graph(claims):
    return build_understanding_graph([_scan_result(claims)])


class TestNormalization:
    def test_normalize_api(self):
        assert _normalize_api("GET /users") == ("GET", "/users")
        assert _normalize_api("POST /api/v1/items") == ("POST", "/api/v1/items")
        assert _normalize_api("/plain") == ("", "/plain")

    def test_normalize_symbol(self):
        assert _normalize_symbol("app.models.User") == "app.models.user"
        assert _normalize_symbol("my-module.func") == "my_module.func"


class TestReconcileDocsOnly:
    def test_docs_only_gap(self):
        conn, snap_id = _make_db()
        graph = _build_graph([_claim(
            claim_type="core_capability",
            summary="Evaluation engine",
        )])
        result = reconcile(conn, 1, snap_id, graph)
        assert result.docs_only_count >= 1
        docs_only_gaps = [g for g in result.gaps if g.gap_type == "docs_only"]
        assert len(docs_only_gaps) >= 1


class TestReconcileCodeOnly:
    def test_code_only_symbol(self):
        conn, snap_id = _make_db()
        _add_symbol(conn, snap_id, "src/main.py", "main.run", kind="function")
        graph = _build_graph([])
        result = reconcile(conn, 1, snap_id, graph)
        code_only = [g for g in result.gaps if g.gap_type == "code_only"]
        assert len(code_only) >= 1

    def test_unclassified_entrypoint(self):
        conn, snap_id = _make_db()
        _add_entrypoint(conn, snap_id, "src/api.py", "api.get_users",
                        route_method="GET", route_path="/users")
        graph = _build_graph([])
        result = reconcile(conn, 1, snap_id, graph)
        unclass = [g for g in result.gaps if g.gap_type == "unclassified_entrypoint"]
        assert len(unclass) >= 1


class TestReconcileAPIMatch:
    def test_api_boundary_match(self):
        conn, snap_id = _make_db()
        sym_id = _add_symbol(conn, snap_id, "src/api.py", "api.get_users")
        _add_entrypoint(conn, snap_id, "src/api.py", "api.get_users",
                        route_method="GET", route_path="/users", symbol_id=sym_id)
        graph = _build_graph([_claim(
            claim_type="api_boundary",
            summary="Users API endpoint",
            apis=["GET /users"],
        )])
        result = reconcile(conn, 1, snap_id, graph)
        assert result.matched_count >= 1


class TestReconcileMetadataMismatch:
    def test_source_metadata_mismatch(self):
        conn, snap_id = _make_db()
        sym_id = _add_symbol(conn, snap_id, "src/handler.py", "handler.process")
        _add_source_metadata(conn, snap_id, sym_id, "src/handler.py", "handler.process",
                             capability="data_processing")
        graph = _build_graph([_claim(
            claim_type="core_capability",
            summary="Authentication handler",
            symbols=["handler.process"],
        )])
        result = reconcile(conn, 1, snap_id, graph)
        mismatches = [g for g in result.gaps if g.gap_type == "source_doc_mismatch"]
        assert len(mismatches) >= 1


class TestReconcileStaleExplanation:
    def test_drift_attached(self):
        conn, snap_id = _make_db()
        now = time.time()
        conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 is_mock, started_at)
            VALUES (1, ?, 'refresh', 'test', 'test', 'v1', 'v1',
                    'reasoning_llm', 'completed', 0, ?)""",
            (snap_id, now),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO explanation_refresh_proposals
                (system_id, intelligence_run_id, base_snapshot_id, target_snapshot_id,
                 path, qualified_name, drift_status, drift_reason, status, created_at)
            VALUES (1, ?, ?, ?, 'src/func.py', 'func.calc', 'drifted',
                    'Body hash changed', 'proposed', ?)""",
            (run_id, snap_id, snap_id, now),
        )
        graph = _build_graph([_claim(
            claim_type="capability_element",
            summary="Calculation function",
            symbols=["func.calc"],
        )])
        result = reconcile(conn, 1, snap_id, graph)
        stale = [g for g in result.gaps if g.gap_type == "stale_explanation"]
        assert len(stale) >= 1


class TestAPIMatchHandlerSymbolTracked:
    """P2: API route match must also mark handler symbol as matched."""

    def test_handler_symbol_not_reported_code_only(self):
        conn, snap_id = _make_db()
        sym_id = _add_symbol(conn, snap_id, "src/api.py", "api.get_users")
        _add_entrypoint(conn, snap_id, "src/api.py", "api.get_users",
                        route_method="GET", route_path="/users", symbol_id=sym_id)
        graph = _build_graph([_claim(
            claim_type="api_boundary",
            summary="Users API endpoint",
            apis=["GET /users"],
        )])
        result = reconcile(conn, 1, snap_id, graph)
        assert result.matched_count >= 1
        code_only = [g for g in result.gaps if g.gap_type == "code_only"
                     and g.node_name == "api.get_users"]
        assert len(code_only) == 0


class TestDriftFilteredBySnapshot:
    """P2: drift proposals must be scoped to the target snapshot."""

    def test_drift_from_other_snapshot_excluded(self):
        conn, snap_id = _make_db()
        now = time.time()
        other_snap = conn.execute(
            """INSERT INTO repository_snapshots
                (system_id, repo_path, commit_sha, status, file_count, created_at)
            VALUES (1, '/tmp/repo', 'other_sha', 'completed', 0, ?)""",
            (now,),
        ).lastrowid
        conn.execute(
            """INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 is_mock, started_at)
            VALUES (1, ?, 'refresh', 'test', 'test', 'v1', 'v1',
                    'reasoning_llm', 'completed', 0, ?)""",
            (other_snap, now),
        )
        run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO explanation_refresh_proposals
                (system_id, intelligence_run_id, base_snapshot_id, target_snapshot_id,
                 path, qualified_name, drift_status, drift_reason, status, created_at)
            VALUES (1, ?, ?, ?, 'src/func.py', 'func.calc', 'drifted',
                    'Body hash changed', 'proposed', ?)""",
            (run_id, other_snap, other_snap, now),
        )
        graph = _build_graph([_claim(
            claim_type="capability_element",
            summary="Calculation function",
            symbols=["func.calc"],
        )])
        result = reconcile(conn, 1, snap_id, graph)
        stale = [g for g in result.gaps if g.gap_type == "stale_explanation"]
        assert len(stale) == 0


class TestTestSymbolExcluded:
    """P2: test symbols must not create false code_only gaps."""

    def test_is_test_symbol_excluded_from_code_only(self):
        conn, snap_id = _make_db()
        conn.execute(
            """INSERT INTO code_symbols
                (snapshot_id, system_id, path, qualified_name, kind,
                 start_line, end_line, is_test)
            VALUES (?, 1, 'tests/test_main.py', 'test_main.test_run',
                    'function', 1, 10, 1)""",
            (snap_id,),
        )
        graph = _build_graph([])
        result = reconcile(conn, 1, snap_id, graph)
        code_only = [g for g in result.gaps if g.gap_type == "code_only"
                     and g.node_name == "test_main.test_run"]
        assert len(code_only) == 0

    def test_non_test_symbol_still_reported(self):
        conn, snap_id = _make_db()
        _add_symbol(conn, snap_id, "src/main.py", "main.run", kind="function")
        graph = _build_graph([])
        result = reconcile(conn, 1, snap_id, graph)
        code_only = [g for g in result.gaps if g.gap_type == "code_only"]
        assert len(code_only) >= 1


class TestReconcilePreservesEvidence:
    def test_both_sides_evidence(self):
        conn, snap_id = _make_db()
        sym_id = _add_symbol(conn, snap_id, "src/api.py", "api.list_items")
        _add_entrypoint(conn, snap_id, "src/api.py", "api.list_items",
                        route_method="GET", route_path="/items", symbol_id=sym_id)
        graph = _build_graph([_claim(
            claim_type="api_boundary",
            summary="Items listing",
            apis=["GET /items"],
        )])
        result = reconcile(conn, 1, snap_id, graph)
        for mapping in result.mappings:
            assert len(mapping.doc_evidence) > 0
            assert len(mapping.code_evidence) > 0
