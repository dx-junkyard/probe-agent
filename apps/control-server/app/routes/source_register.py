"""External Source Register API (vision-to-feature-research.md §3.3, VF-G3 / VF-G8).

Read-only: the register is derived from the pinned snapshot's committed
files on every request, and the source claims are read from the latest
understanding graph of that snapshot. Nothing here writes.

probe-agent:
  role: API boundary for the external source register and source claims
  capability: documentation-understanding
  element_type: boundary
  consumers: [dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify that register checks and unreadable sources are reported rather than hidden, and that GET never writes.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_system_id
from ..db import get_conn
from ..documentation_claim_scanner import SOURCE_CLAIM_TYPES
from ..source_register import latest_snapshot_id, load_register

router = APIRouter()


def _snapshot_for(conn, system_id: int, snapshot_id: Optional[int]) -> int:
    if snapshot_id is None:
        sid = latest_snapshot_id(conn, system_id)
        if sid is None:
            raise HTTPException(status_code=409, detail="No ready repository snapshot")
        return sid
    row = conn.execute(
        "SELECT id FROM repository_snapshots WHERE id = ? AND system_id = ?", (snapshot_id, system_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snapshot_id


def _source_claims(conn, system_id: int, snapshot_id: int) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT id, graph_json FROM understanding_graph_snapshots "
        "WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if row is None:
        return {"state": "not_built", "graph_snapshot_id": None, "claims": []}
    try:
        nodes = json.loads(row["graph_json"]).get("nodes", {})
    except (json.JSONDecodeError, TypeError) as exc:
        return {"state": "unavailable", "graph_snapshot_id": row["id"], "detail": str(exc), "claims": []}
    claims: List[Dict[str, Any]] = []
    for node in nodes.values():
        if node.get("node_type") not in SOURCE_CLAIM_TYPES:
            continue
        claims.append({
            "node_id": node.get("node_id"),
            "claim_type": node.get("node_type"),
            "source_id": node.get("source_id"),
            "summary": node.get("summary", ""),
            "indicator": node.get("indicator"),
            "confidence": node.get("confidence", 0.0),
            "is_weak": node.get("is_weak", False),
            "evidence": [
                {"path": e.get("path"), "start_line": e.get("start_line"), "end_line": e.get("end_line")}
                for e in node.get("evidence", [])
            ],
        })
    claims.sort(key=lambda c: (c["source_id"] or "", c["claim_type"], (c["evidence"] or [{}])[0].get("path") or "",
                               (c["evidence"] or [{}])[0].get("start_line") or 0))
    return {"state": "available", "graph_snapshot_id": row["id"], "claims": claims}


@router.get("/source-register")
def get_source_register(
    snapshot_id: Optional[int] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> Dict[str, Any]:
    """The register (sources, attributes, parts, checks) and, per source,
    how many claims the latest understanding graph holds for it."""
    with get_conn() as conn:
        sid = _snapshot_for(conn, system_id, snapshot_id)
        register = load_register(conn, sid).to_dict()
        claims = _source_claims(conn, system_id, sid)
    counts: Dict[str, Dict[str, int]] = {}
    for c in claims["claims"]:
        per = counts.setdefault(c["source_id"] or "", {})
        per[c["claim_type"]] = per.get(c["claim_type"], 0) + 1
    for src in register["sources"]:
        src["claim_counts"] = counts.get(src["source_id"], {})
    register["claims_state"] = claims["state"]
    return register


@router.get("/source-register/claims")
def get_source_claims(
    snapshot_id: Optional[int] = Query(default=None),
    source_id: Optional[str] = Query(default=None),
    claim_type: Optional[str] = Query(default=None),
    system_id: int = Depends(get_system_id),
) -> Dict[str, Any]:
    """Claims extracted from registered sources, each with its source and
    position. Source attributes are joined from the register, not stored."""
    if claim_type is not None and claim_type not in SOURCE_CLAIM_TYPES:
        raise HTTPException(status_code=422, detail=f"claim_type must be one of {sorted(SOURCE_CLAIM_TYPES)}")
    with get_conn() as conn:
        sid = _snapshot_for(conn, system_id, snapshot_id)
        register = load_register(conn, sid)
        out = _source_claims(conn, system_id, sid)
    claims = out["claims"]
    if source_id is not None:
        claims = [c for c in claims if c["source_id"] == source_id]
    if claim_type is not None:
        claims = [c for c in claims if c["claim_type"] == claim_type]
    for c in claims:
        entry = register.entries.get(c["source_id"] or "")
        c["source"] = (
            {"attributes": dict(entry.attributes), "period_state": entry.period_state}
            if entry is not None else None
        )
    return {
        "snapshot_id": sid,
        "state": out["state"],
        "graph_snapshot_id": out["graph_snapshot_id"],
        "register_state": register.state,
        "claims": claims,
    }
