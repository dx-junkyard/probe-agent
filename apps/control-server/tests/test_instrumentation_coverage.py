"""Instrumentation coverage: code-side points joined with received traces."""
import json
import sqlite3
import time

from app.db import SCHEMA
from app.instrumentation_coverage import build_coverage


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    now = time.time()
    conn.execute("INSERT INTO systems (id, name, created_at, updated_at) VALUES (1, 's', ?, ?)", (now, now))
    snap = conn.execute(
        "INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, file_count, created_at)"
        " VALUES (1, '/r', 'abc', 'completed', 0, ?)", (now,)).lastrowid
    return conn, snap


def _symbol(conn, snap, qname, component_id=None, decorators=(), kind="function", route=None):
    return conn.execute(
        """INSERT INTO code_symbols (snapshot_id, system_id, path, qualified_name, kind, start_line, end_line,
                                    decorators, component_id, route_method, route_path)
           VALUES (?, 1, 'app/x.py', ?, ?, 1, 9, ?, ?, ?, ?)""",
        (snap, qname, kind, json.dumps(list(decorators)), component_id,
         route[0] if route else None, route[1] if route else None)).lastrowid


def _meta(conn, snap, sid, qname, capability):
    conn.execute(
        """INSERT INTO symbol_source_metadata (snapshot_id, system_id, symbol_id, path, qualified_name,
                                              start_line, end_line, role, capability, raw_block)
           VALUES (?, 1, ?, 'app/x.py', ?, 1, 9, 'role', ?, 'raw')""", (snap, sid, qname, capability))


def _trace(conn, cid, error=None, ts=None):
    conn.execute(
        "INSERT INTO traces (system_id, trace_id, component_id, mode, error, duration_ms, timestamp)"
        " VALUES (1, ?, ?, 'trace', ?, 3.0, ?)", (f"t{time.time_ns()}", cid, error, ts or time.time()))


def test_each_point_gets_its_reception_status():
    conn, snap = _db()
    _symbol(conn, snap, "ok", "agent-ok", ['observed("agent-ok")'])
    _symbol(conn, snap, "bad", "agent-bad", ['probe(component_id="agent-bad")'])
    _symbol(conn, snap, "silent", "agent-silent", ['observed("agent-silent")'])
    m = _symbol(conn, snap, "described", route=("POST", "/threads/{tid}/summary"))
    _meta(conn, snap, m, "described", "F-05 スレッド要約、F-16 根拠")
    mod = _symbol(conn, snap, "app.x", kind="module")
    _meta(conn, snap, mod, "app.x", "F-21")
    _trace(conn, "agent-ok")
    _trace(conn, "agent-bad", error="boom")
    _trace(conn, "ghost")

    out = build_coverage(conn, 1, snap)
    by = {p["symbol"]: p for p in out["points"]}
    assert set(by) == {"ok", "bad", "silent", "described"}  # module docstrings are not call sites
    assert (by["ok"]["status"], by["ok"]["instrumentation"], by["ok"]["trace_count"]) == ("receiving", "wrapper", 1)
    assert (by["bad"]["status"], by["bad"]["instrumentation"]) == ("errors", "probe")
    assert by["silent"]["status"] == "not_received" and by["silent"]["last_seen"] is None
    assert by["described"]["status"] == "metadata_only"
    assert by["described"]["features"] == ["F-05", "F-16"]
    assert by["described"]["route"] == "POST /threads/{tid}/summary"
    assert out["counts"] == {"receiving": 1, "errors": 1, "not_received": 1, "metadata_only": 1}
    assert out["sdk_connected"] is True
    assert [u["component_id"] for u in out["unmatched_components"]] == ["ghost"]


def test_nothing_received_reports_sdk_not_connected():
    conn, snap = _db()
    _symbol(conn, snap, "silent", "agent-silent", ['observed("agent-silent")'])
    out = build_coverage(conn, 1, snap)
    assert out["sdk_connected"] is False and out["last_trace_at"] is None
    assert out["points"][0]["status"] == "not_received"


def test_recent_traces_carry_code_location_and_short_previews():
    from app.instrumentation_coverage import recent_traces

    conn, snap = _db()
    _symbol(conn, snap, "summarize_thread", "thread-summary-agent", ['observed("thread-summary-agent")'],
            route=("POST", "/threads/{tid}/summary"))
    conn.execute("INSERT INTO traces (system_id, trace_id, component_id, mode, output_text, duration_ms, timestamp)"
                 " VALUES (1, 'a', 'thread-summary-agent', 'trace', ?, 12.5, 100)", ("x " * 200,))
    conn.execute("INSERT INTO traces (system_id, trace_id, component_id, mode, error, timestamp)"
                 " VALUES (1, 'b', 'unknown', 'trace', 'Boom', 200)")
    items = recent_traces(conn, 1, snap)
    assert [i["trace_id"] for i in items] == ["b", "a"]
    assert items[0]["ok"] is False and items[0]["code"] is None
    assert items[1]["code"] == {"path": "app/x.py", "symbol": "summarize_thread", "line_start": 1,
                                "route": "POST /threads/{tid}/summary"}
    assert len(items[1]["output_preview"]) == 160
    assert [i["trace_id"] for i in recent_traces(conn, 1, snap, since=150)] == ["b"]


def test_route_uses_the_composed_entrypoint_path():
    conn, snap = _db()
    sid = _symbol(conn, snap, "summarize_thread", route=("POST", "/threads/{tid}/summary"))
    _meta(conn, snap, sid, "summarize_thread", "F-05")
    conn.execute(
        """INSERT INTO code_entrypoints (system_id, snapshot_id, entrypoint_type, entrypoint_id, category, label,
                                         handler_symbol_id, handler_path, handler_qualified_name, line_start, line_end,
                                         route_method, route_path, created_at)
           VALUES (1, ?, 'http_route', 'POST:/api/threads/{tid}/summary', 'api', 'POST /api/threads/{tid}/summary',
                   ?, 'app/x.py', 'summarize_thread', 1, 9, 'POST', '/api/threads/{tid}/summary', 0)""", (snap, sid))
    [p] = build_coverage(conn, 1, snap)["points"]
    assert p["route"] == "POST /api/threads/{tid}/summary"


def test_recovered_component_is_receiving_with_its_recent_error_count():
    conn, snap = _db()
    _symbol(conn, snap, "search", "opendata-search-agent", ['observed("opendata-search-agent")'])
    _trace(conn, "opendata-search-agent", error="LLMUnavailable", ts=100)
    _trace(conn, "opendata-search-agent", ts=200)
    [p] = build_coverage(conn, 1, snap)["points"]
    assert p["status"] == "receiving" and p["recent_errors"] == 1 and p["last_failed"] is False
    _trace(conn, "opendata-search-agent", error="Timeout", ts=300)
    [p] = build_coverage(conn, 1, snap)["points"]
    assert p["status"] == "errors" and p["recent_errors"] == 2


def _span(conn, trace_id, cid, span, parent, corr, flow, ts):
    conn.execute(
        """INSERT INTO trace_spans (system_id, trace_id, component_id, span_id, parent_span_id, flow_id, correlation_id, timestamp)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?)""", (trace_id, cid, span, parent, flow, corr, ts))


def test_request_waterfall_orders_calls_by_start_and_nests_children():
    from app.instrumentation_coverage import build_coverage, recent_traces, request_waterfall

    conn, snap = _db()
    _symbol(conn, snap, "summarize", "thread-summary-agent", ['observed("thread-summary-agent")'])
    flow = "POST /api/threads/{tid}/summary"
    # Ends at 110 after 6 s (started 104); its child ends at 109 after 3 s (started 106).
    conn.execute("INSERT INTO traces (system_id, trace_id, component_id, mode, duration_ms, timestamp)"
                 " VALUES (1, 'p', 'thread-summary-agent', 'trace', 6000, 110)")
    conn.execute("INSERT INTO traces (system_id, trace_id, component_id, mode, error, duration_ms, timestamp)"
                 " VALUES (1, 'c', 'llm', 'trace', 'Timeout', 3000, 109)")
    _span(conn, "p", "thread-summary-agent", "s1", None, "req-1", flow, 110)
    _span(conn, "c", "llm", "s2", "s1", "req-1", flow, 109)
    w = request_waterfall(conn, 1, snap, "req-1")
    assert w["request"] == flow and round(w["total_ms"]) == 6000
    assert [(c["trace_id"], c["depth"], round(c["offset_ms"])) for c in w["calls"]] == [("p", 0, 0), ("c", 1, 2000)]
    assert w["calls"][0]["code"]["symbol"] == "summarize" and w["calls"][1]["ok"] is False
    assert request_waterfall(conn, 1, snap, "nope") is None
    [latest, *_] = recent_traces(conn, 1, snap)
    assert latest["request"] == flow and latest["correlation_id"] == "req-1"
    [p] = build_coverage(conn, 1, snap)["points"]
    assert p["entry_flows"] == [flow]
