"""Deterministic, read-only analyzer engine over saved projections (Issue #148).

Validation is fail-closed (an invalid spec raises ``AnalyzerSpecError``). Runs
are strictly read-only: they SELECT from ``trace_projections`` /
``trace_entities`` and evaluate the declarative spec in Python. There is no
arbitrary code execution — the path grammar and comparisons are the same finite
set used by projection specs (#146). ``compare`` is validated here but only
executed in Phase 5 (#150); attempting to run a compare-only spec is a
deterministic error.
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

_SEGMENT_RE = re.compile(r"\.([A-Za-z0-9_\-]+)|\[([0-9]+)\]|\[(\*)\]")
_PHASES = ("input", "output", "shadow_current", "shadow_candidate")
_MISSING = object()


class AnalyzerSpecError(ValueError):
    """Invalid analyzer spec (fail-closed at save/run)."""


class AnalyzerLimitError(RuntimeError):
    """Execution exceeded a configured limit; the run is recorded as failed."""


# --- limits ----------------------------------------------------------------

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def max_input_rows() -> int:
    return _int_env("ANALYZER_MAX_INPUT_ROWS", 10000)


def max_output_bytes() -> int:
    return _int_env("ANALYZER_MAX_OUTPUT_BYTES", 200000)


def max_seconds() -> float:
    try:
        return float(os.getenv("ANALYZER_MAX_SECONDS", "10"))
    except (TypeError, ValueError):
        return 10.0


# --- path grammar ----------------------------------------------------------

def parse_path(path: str) -> List[tuple]:
    if not isinstance(path, str) or not path.startswith("$"):
        raise AnalyzerSpecError(f"path must start with '$': {path!r}")
    rest, pos, segs = path[1:], 0, []
    while pos < len(rest):
        m = _SEGMENT_RE.match(rest, pos)
        if not m:
            raise AnalyzerSpecError(f"invalid path segment in {path!r}")
        key, index, wild = m.groups()
        if key is not None:
            segs.append(("key", key))
        elif index is not None:
            segs.append(("index", int(index)))
        else:
            segs.append(("wild",))
        pos = m.end()
    return segs


def _has_wild(segs: List[tuple]) -> bool:
    return any(s[0] == "wild" for s in segs)


def _resolve(segs: List[tuple], root: Any) -> List[Any]:
    current = [root]
    for seg in segs:
        nxt: List[Any] = []
        for val in current:
            if seg[0] == "key":
                if isinstance(val, dict) and seg[1] in val:
                    nxt.append(val[seg[1]])
            elif seg[0] == "index":
                if isinstance(val, (list, tuple)) and -len(val) <= seg[1] < len(val):
                    nxt.append(val[seg[1]])
            else:
                if isinstance(val, (list, tuple)):
                    nxt.extend(val)
                elif isinstance(val, dict):
                    nxt.extend(val.values())
        current = nxt
    return current


def _select_value(segs: List[tuple], root: Any) -> Any:
    matches = _resolve(segs, root)
    if _has_wild(segs):
        return matches
    return matches[0] if matches else None


# --- spec validation -------------------------------------------------------

class AnalyzerSpec:
    def __init__(self, raw: Dict[str, Any]):
        if not isinstance(raw, dict):
            raise AnalyzerSpecError("analyzer spec must be a mapping")
        unknown = set(raw) - {"source", "filter", "select", "group_by", "order_by", "compare"}
        if unknown:
            raise AnalyzerSpecError(f"unknown analyzer keys: {sorted(unknown)}")

        self.source = raw.get("source")
        if self.source != "trace_projections":
            raise AnalyzerSpecError("source must be 'trace_projections'")

        self.filter = self._parse_filter(raw.get("filter") or {})

        select = raw.get("select")
        if not isinstance(select, list) or not select:
            raise AnalyzerSpecError("select must be a non-empty list")
        self.select: List[Tuple[str, List[tuple]]] = []
        names = set()
        for item in select:
            if not isinstance(item, dict) or "name" not in item or "path" not in item:
                raise AnalyzerSpecError("each select needs 'name' and 'path'")
            name = item["name"]
            if name in names:
                raise AnalyzerSpecError(f"duplicate select name {name!r}")
            names.add(name)
            self.select.append((name, parse_path(item["path"])))
        self.select_names = names

        self.group_by = list(raw.get("group_by") or [])
        for g in self.group_by:
            if g not in names:
                raise AnalyzerSpecError(f"group_by name {g!r} not in select")

        self.order_by = []
        for ob in raw.get("order_by") or []:
            if not isinstance(ob, dict) or "name" not in ob:
                raise AnalyzerSpecError("order_by entry needs 'name'")
            if ob["name"] not in names:
                raise AnalyzerSpecError(f"order_by name {ob['name']!r} not in select")
            direction = ob.get("dir", "asc")
            if direction not in ("asc", "desc"):
                raise AnalyzerSpecError("order_by dir must be asc|desc")
            self.order_by.append((ob["name"], direction))

        self.compare = self._parse_compare(raw.get("compare"))

    def _parse_filter(self, f: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(f, dict):
            raise AnalyzerSpecError("filter must be a mapping")
        unknown = set(f) - {"entity", "components", "projection_name", "phases", "time_window"}
        if unknown:
            raise AnalyzerSpecError(f"unknown filter keys: {sorted(unknown)}")
        out: Dict[str, Any] = {}
        if "entity" in f:
            ent = f["entity"]
            if not isinstance(ent, dict) or "type" not in ent or "id" not in ent:
                raise AnalyzerSpecError("filter.entity needs 'type' and 'id'")
            out["entity"] = {"type": str(ent["type"]), "id": str(ent["id"])}
        if "components" in f:
            if not isinstance(f["components"], list):
                raise AnalyzerSpecError("filter.components must be a list")
            out["components"] = [str(c) for c in f["components"]]
        if "projection_name" in f:
            out["projection_name"] = str(f["projection_name"])
        if "phases" in f:
            if not isinstance(f["phases"], list):
                raise AnalyzerSpecError("filter.phases must be a list")
            for p in f["phases"]:
                if p not in _PHASES:
                    raise AnalyzerSpecError(f"unknown phase {p!r}")
            out["phases"] = list(f["phases"])
        if "time_window" in f:
            tw = f["time_window"]
            if not isinstance(tw, dict):
                raise AnalyzerSpecError("filter.time_window must be a mapping")
            out["time_window"] = {
                "start": tw.get("start"),
                "end": tw.get("end"),
            }
        return out

    def _parse_compare(self, c: Any) -> Optional[Dict[str, Any]]:
        if c is None:
            return None
        if not isinstance(c, dict) or "phases" not in c or "fields" not in c:
            raise AnalyzerSpecError("compare needs 'phases' and 'fields'")
        phases = c["phases"]
        if not isinstance(phases, list) or len(phases) != 2:
            raise AnalyzerSpecError("compare.phases must have exactly 2 phases")
        for p in phases:
            if p not in _PHASES:
                raise AnalyzerSpecError(f"unknown compare phase {p!r}")
        fields = c["fields"]
        if not isinstance(fields, list) or not fields:
            raise AnalyzerSpecError("compare.fields must be a non-empty list")
        unknown = set(c) - {"phases", "fields", "entity_type"}
        if unknown:
            raise AnalyzerSpecError(f"unknown compare keys: {sorted(unknown)}")
        return {
            "phases": list(phases),
            "fields": [str(x) for x in fields],
            "entity_type": c.get("entity_type"),
        }


def compile_spec(spec: Any) -> AnalyzerSpec:
    if isinstance(spec, AnalyzerSpec):
        return spec
    return AnalyzerSpec(spec)


# --- execution (read-only) -------------------------------------------------

def _build_query(system_id: int, spec: AnalyzerSpec) -> Tuple[str, list]:
    where = ["tp.system_id = ?"]
    params: List[Any] = [system_id]
    f = spec.filter
    if "components" in f and f["components"]:
        placeholders = ",".join("?" for _ in f["components"])
        where.append(f"tp.component_id IN ({placeholders})")
        params.extend(f["components"])
    if "projection_name" in f:
        where.append("tp.projection_name = ?")
        params.append(f["projection_name"])
    if "phases" in f and f["phases"]:
        placeholders = ",".join("?" for _ in f["phases"])
        where.append(f"tp.phase IN ({placeholders})")
        params.extend(f["phases"])
    if "time_window" in f:
        tw = f["time_window"]
        if tw.get("start") is not None:
            where.append("tp.created_at >= ?")
            params.append(tw["start"])
        if tw.get("end") is not None:
            where.append("tp.created_at <= ?")
            params.append(tw["end"])
    if "entity" in f:
        where.append(
            "tp.trace_id IN (SELECT te.trace_id FROM trace_entities te "
            "WHERE te.system_id = ? AND te.entity_type = ? AND te.entity_id = ?)"
        )
        params.extend([system_id, f["entity"]["type"], f["entity"]["id"]])
    sql = (
        "SELECT tp.trace_id, tp.component_id, tp.projection_name, tp.phase, "
        "tp.data_json, tp.created_at "
        "FROM trace_projections tp WHERE " + " AND ".join(where) +
        " ORDER BY tp.created_at ASC, tp.id ASC"
    )
    return sql, params


def _row_object(row, entities_by_trace: Dict[str, list]) -> Dict[str, Any]:
    try:
        data = json.loads(row["data_json"]) if row["data_json"] else {}
    except json.JSONDecodeError:
        data = {}
    return {
        "trace_id": row["trace_id"],
        "component_id": row["component_id"],
        "projection_name": row["projection_name"],
        "phase": row["phase"],
        "timestamp": row["created_at"],
        "fields": data.get("fields", {}) or {},
        "metrics": data.get("metrics", {}) or {},
        "samples": data.get("samples", {}) or {},
        "entities": entities_by_trace.get(row["trace_id"], []),
    }


def _sortable(value: Any) -> tuple:
    # Deterministic total order across mixed types: (type_rank, coerced).
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, float(value))
    if isinstance(value, str):
        return (3, value)
    return (4, json.dumps(value, sort_keys=True, default=repr))


def run_analyzer(conn, system_id: int, spec: AnalyzerSpec) -> Dict[str, Any]:
    """Execute a validated analyzer read-only. Raises AnalyzerLimitError on
    limit breach (the caller records the run as failed)."""
    started = time.monotonic()
    sql, params = _build_query(system_id, spec)

    proj_rows = conn.execute(sql, params).fetchall()
    if len(proj_rows) > max_input_rows():
        raise AnalyzerLimitError(
            f"input rows {len(proj_rows)} exceed limit {max_input_rows()}"
        )

    trace_ids = {r["trace_id"] for r in proj_rows}
    entities_by_trace: Dict[str, list] = {}
    for tid in trace_ids:
        erows = conn.execute(
            "SELECT entity_type, entity_id, role FROM trace_entities "
            "WHERE system_id = ? AND trace_id = ? ORDER BY id",
            (system_id, tid),
        ).fetchall()
        entities_by_trace[tid] = [
            {"type": e["entity_type"], "id": e["entity_id"], "role": e["role"]} for e in erows
        ]

    rows: List[Dict[str, Any]] = []
    for r in proj_rows:
        obj = _row_object(r, entities_by_trace)
        selected = {name: _select_value(segs, obj) for name, segs in spec.select}
        rows.append(selected)
        if time.monotonic() - started > max_seconds():
            raise AnalyzerLimitError(f"execution exceeded {max_seconds()}s")

    for name, direction in reversed(spec.order_by):
        rows.sort(key=lambda r: _sortable(r.get(name)), reverse=(direction == "desc"))

    result: Dict[str, Any] = {"row_count": len(rows)}
    if spec.group_by:
        groups: Dict[tuple, Dict[str, Any]] = {}
        for r in rows:
            key = tuple((g, r.get(g)) for g in spec.group_by)
            grp = groups.setdefault(key, {"key": {g: r.get(g) for g in spec.group_by},
                                          "count": 0, "rows": []})
            grp["count"] += 1
            grp["rows"].append(r)
        ordered = sorted(
            groups.values(),
            key=lambda g: tuple(_sortable(g["key"].get(k)) for k in spec.group_by),
        )
        result["groups"] = ordered
    else:
        result["rows"] = rows

    encoded = json.dumps(result, ensure_ascii=False, default=repr)
    if len(encoded.encode("utf-8")) > max_output_bytes():
        raise AnalyzerLimitError(
            f"output {len(encoded.encode('utf-8'))} bytes exceeds limit {max_output_bytes()}"
        )
    return result
