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
