"""Instrumentation coverage: every instrumented point in the code, and whether
its traces arrive.

The Components screen lists only components that already sent traces, so
right after instrumenting a repository there was no way to see which points
work, which were never called, and which cannot report at all. This joins
the committed-snapshot symbol index (``@probe``, a declared wrapper such as
``observe.observed("x")``, and ``probe-agent:`` metadata) with the received
traces, deterministically and without an LLM.

probe-agent:
  role: Deterministic join of code-side instrumentation points with received traces
  capability: probe-instrumentation-coverage
  element_type: core
  consumers: [apps.control-server.app.routes.instrumentation_coverage]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify each instrumented symbol gets the right status (metadata only, not received, receiving, errors)
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

FEATURE_ID = re.compile(r"\bF-\d{2,3}\b")
# A trace error in this many most recent traces of a component marks it "errors".
RECENT_WINDOW = 50


def _features(*texts: Optional[str]) -> List[str]:
    found: List[str] = []
    for t in texts:
        for f in FEATURE_ID.findall(t or ""):
            if f not in found:
                found.append(f)
    return found


def _runtime_stats(conn: sqlite3.Connection, system_id: int) -> Dict[str, Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for r in conn.execute(
        """SELECT component_id, COUNT(*) AS n, MAX(timestamp) AS last_seen,
                  AVG(duration_ms) AS avg_ms
           FROM traces WHERE system_id = ? GROUP BY component_id""",
        (system_id,),
    ):
        stats[r["component_id"]] = {
            "trace_count": r["n"], "last_seen": r["last_seen"], "avg_duration_ms": r["avg_ms"],
            "recent_errors": 0,
        }
    for cid in stats:
        stats[cid]["recent_errors"] = conn.execute(
            """SELECT COUNT(*) FROM (SELECT error FROM traces WHERE system_id = ? AND component_id = ?
                                     ORDER BY timestamp DESC LIMIT ?) WHERE error IS NOT NULL AND error != ''""",
            (system_id, cid, RECENT_WINDOW),
        ).fetchone()[0]
    return stats


def _status(component_id: Optional[str], stat: Optional[Dict[str, Any]]) -> str:
    if not component_id:
        return "metadata_only"
    if not stat:
        return "not_received"
    return "errors" if stat["recent_errors"] else "receiving"


def build_coverage(conn: sqlite3.Connection, system_id: int, snapshot_id: int) -> Dict[str, Any]:
    meta = {
        r["symbol_id"]: r
        for r in conn.execute(
            "SELECT * FROM symbol_source_metadata WHERE system_id = ? AND snapshot_id = ?",
            (system_id, snapshot_id),
        )
    }
    stats = _runtime_stats(conn, system_id)
    # Composed routes (router prefixes applied) from entrypoint discovery; the
    # symbol index only knows the decorator's own path ("/threads/..." for
    # "/api/threads/...").
    routes = {
        r["handler_symbol_id"]: f"{r['route_method']} {r['route_path']}"
        for r in conn.execute(
            """SELECT handler_symbol_id, route_method, route_path FROM code_entrypoints
               WHERE system_id = ? AND snapshot_id = ? AND handler_symbol_id IS NOT NULL
                 AND route_method IS NOT NULL AND route_path IS NOT NULL""",
            (system_id, snapshot_id),
        )
    }
    points: List[Dict[str, Any]] = []
    seen_components = set()
    for s in conn.execute(
        """SELECT * FROM code_symbols WHERE system_id = ? AND snapshot_id = ?
             AND (component_id IS NOT NULL OR id IN (
                  SELECT symbol_id FROM symbol_source_metadata WHERE system_id = ? AND snapshot_id = ?))
             AND is_test = 0
           ORDER BY path, start_line""",
        (system_id, snapshot_id, system_id, snapshot_id),
    ):
        m = meta.get(s["id"])
        if s["kind"] == "module" and not s["component_id"]:
            # Module docstrings describe a file; they are not a call site.
            continue
        try:
            decorators = json.loads(s["decorators"] or "[]")
        except json.JSONDecodeError:
            decorators = []
        cid = s["component_id"]
        if cid:
            seen_components.add(cid)
            kind = "probe" if any(isinstance(d, str) and re.match(r"^(\w+\.)*probe\(", d) for d in decorators) else "wrapper"
        else:
            kind = "metadata_only"
        stat = stats.get(cid) if cid else None
        points.append({
            "path": s["path"],
            "symbol": s["qualified_name"],
            "line_start": s["start_line"],
            "line_end": s["end_line"],
            "component_id": cid,
            "instrumentation": kind,
            "route": routes.get(s["id"]) or (
                f"{s['route_method']} {s['route_path']}" if s["route_method"] and s["route_path"] else None),
            "role": m["role"] if m else None,
            "features": _features(m["capability"], m["role"], m["system_purpose"]) if m else [],
            "status": _status(cid, stat),
            "trace_count": stat["trace_count"] if stat else 0,
            "recent_errors": stat["recent_errors"] if stat else 0,
            "last_seen": stat["last_seen"] if stat else None,
            "avg_duration_ms": stat["avg_duration_ms"] if stat else None,
        })
    unmatched = [
        {"component_id": cid, **st}
        for cid, st in sorted(stats.items()) if cid not in seen_components
    ]
    counts = {k: sum(1 for p in points if p["status"] == k)
              for k in ("receiving", "errors", "not_received", "metadata_only")}
    last = max((st["last_seen"] for st in stats.values() if st["last_seen"]), default=None)
    return {
        "system_id": system_id,
        "snapshot_id": snapshot_id,
        "sdk_connected": bool(stats),
        "last_trace_at": last,
        "counts": counts,
        "points": points,
        "unmatched_components": unmatched,
    }


PREVIEW_CHARS = 160


def _preview(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    t = " ".join(str(text).split())
    return t if len(t) <= PREVIEW_CHARS else t[: PREVIEW_CHARS - 1] + "…"


def recent_traces(conn: sqlite3.Connection, system_id: int, snapshot_id: Optional[int],
                  limit: int = 30, since: Optional[float] = None) -> List[Dict[str, Any]]:
    """Newest traces with the code location that sent them (live view).

    Stored input/output are already redacted at ingestion; only a short,
    whitespace-collapsed preview is returned so the feed stays scannable.
    """
    where = "system_id = ?"
    args: List[Any] = [system_id]
    if since is not None:
        where += " AND timestamp > ?"
        args.append(since)
    rows = conn.execute(
        f"""SELECT trace_id, component_id, mode, error, duration_ms, timestamp, input_json, output_text
            FROM traces WHERE {where} ORDER BY timestamp DESC LIMIT ?""",
        (*args, max(1, min(limit, 200))),
    ).fetchall()
    locations: Dict[str, Dict[str, Any]] = {}
    if snapshot_id is not None:
        for s in conn.execute(
            """SELECT component_id, path, qualified_name, start_line, route_method, route_path
               FROM code_symbols WHERE system_id = ? AND snapshot_id = ? AND component_id IS NOT NULL""",
            (system_id, snapshot_id),
        ):
            locations.setdefault(s["component_id"], {
                "path": s["path"], "symbol": s["qualified_name"], "line_start": s["start_line"],
                "route": f"{s['route_method']} {s['route_path']}" if s["route_method"] and s["route_path"] else None,
            })
    return [
        {
            "trace_id": r["trace_id"],
            "component_id": r["component_id"],
            "mode": r["mode"],
            "ok": not r["error"],
            "error": _preview(r["error"]),
            "duration_ms": r["duration_ms"],
            "timestamp": r["timestamp"],
            "input_preview": _preview(r["input_json"]),
            "output_preview": _preview(r["output_text"]),
            "code": locations.get(r["component_id"]),
        }
        for r in rows
    ]
