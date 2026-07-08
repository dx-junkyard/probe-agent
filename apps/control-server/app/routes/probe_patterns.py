"""Probe Pattern lifecycle API routes (Issue #168).

Probe Patterns keep the *recognition* behind probe instrumentation alive
across the production release boundary: what feature a probe observed, why,
and from which pinned snapshot it was captured. The routes cover:

- deterministic scan of the latest snapshot for @probe instrumentation
- saving/updating patterns (including from the pre-release removal flow)
- reviewable removal patches with an explicit commit-confirmed apply boundary
- reconciliation of saved points against the latest snapshot (deterministic
  structural checks first, reasoning model for open-ended moves, no fallback)
- accept/reject/investigate decisions per reconcile point
- creating a Probe Plan from an approved reconciliation so re-attachment
  reuses the existing plan -> approve -> patch -> validate -> apply gates

probe-agent:
  role: Probe Pattern lifecycle REST API boundary
  capability: probe-pattern-lifecycle
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify pattern persistence, reconcile audit trails, and that removal/re-attachment diffs are never applied without explicit confirmation.
"""

import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Response

from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..instrumentation_remover import RemovalPoint, generate_removal_patch
from ..llm import LLMConfig, LLMError, create_llm_client, is_reasoning_model
from ..models import (
    CreatePlanFromReconcileRequest,
    InstrumentationScanOut,
    InstrumentedProbeOut,
    IntelligenceRunOut,
    PatternInvestigationOut,
    ProbePatternCreateRequest,
    ProbePatternEventOut,
    ProbePatternOut,
    ProbePatternPointOut,
    ProbePatternReconciliationOut,
    ProbePatternsListOut,
    ProbePatternUpdateRequest,
    ProbePlanOut,
    ProbeRemovalPatchApplyRequest,
    ProbeRemovalPatchCreateRequest,
    ProbeRemovalPatchOut,
    ReconcileDecisionRequest,
    ReconcileEvidenceOut,
    ReconcilePointOut,
)
from ..pattern_reconciler import (
    INVESTIGATE_PROMPT_VERSION,
    PROMPT_VERSION as RECONCILE_PROMPT_VERSION,
    SCHEMA_VERSION as RECONCILE_SCHEMA_VERSION,
    PatternPointFacts,
    ReconcilePointResult,
    ReconcileValidationError,
    SnapshotSymbol,
    classify_deterministic,
    classify_with_reasoning,
    extract_signature,
    investigate_point,
)
from ..probe_planner import check_denylist

router = APIRouter()

_PROBE_DECORATOR_KINDS = ("function", "async_function")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _latest_ready_snapshot(conn, system_id: int):
    return conn.execute(
        """SELECT * FROM repository_snapshots
           WHERE system_id = ? AND status = 'ready'
           ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()


def _decorator_is_probe(decorator: str) -> bool:
    """Structural check: decorator string names ``probe`` (possibly dotted)."""
    name = decorator.split("(", 1)[0].strip()
    return name.split(".")[-1] == "probe"


def _load_snapshot_symbols(conn, snapshot_id: int) -> List[SnapshotSymbol]:
    rows = conn.execute(
        """SELECT path, qualified_name, kind, start_line, end_line,
                  docstring, symbol_source_hash, symbol_body_hash
           FROM code_symbols WHERE snapshot_id = ?""",
        (snapshot_id,),
    ).fetchall()
    return [
        SnapshotSymbol(
            path=r["path"],
            qualified_name=r["qualified_name"],
            kind=r["kind"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            docstring=r["docstring"],
            symbol_source_hash=r["symbol_source_hash"],
            symbol_body_hash=r["symbol_body_hash"],
        )
        for r in rows
    ]


def _load_python_sources(conn, snapshot_id: int) -> Dict[str, str]:
    rows = conn.execute(
        """SELECT path, content FROM snapshot_files
           WHERE snapshot_id = ? AND inclusion_status = 'indexed'
             AND path LIKE '%.py'""",
        (snapshot_id,),
    ).fetchall()
    return {
        r["path"]: bytes(r["content"] or b"").decode("utf-8", errors="replace")
        for r in rows
    }


def _require_indexed_snapshot(conn, system_id: int):
    snapshot_row = _latest_ready_snapshot(conn, system_id)
    if snapshot_row is None:
        raise HTTPException(
            status_code=400, detail="No ready snapshot. Create a snapshot first.",
        )
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM code_symbols WHERE snapshot_id = ?",
        (snapshot_row["id"],),
    ).fetchone()["n"]
    if count == 0:
        raise HTTPException(
            status_code=400,
            detail="Latest snapshot has no symbol index. Run symbol indexing first.",
        )
    return snapshot_row


def _record_event(
    conn,
    pattern_id: int,
    system_id: int,
    event_type: str,
    detail: dict,
    user_id: Optional[int] = None,
) -> None:
    conn.execute(
        """INSERT INTO probe_pattern_events
               (pattern_id, system_id, event_type, detail,
                created_by_user_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (pattern_id, system_id, event_type, json.dumps(detail), user_id, time.time()),
    )


def _component_id_for(qualified_name: str) -> str:
    base = qualified_name.rsplit(".", 1)[-1]
    kebab = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return f"{kebab}-observer" if kebab else "pattern-observer"


def _intelligence_run_out(row) -> IntelligenceRunOut:
    return IntelligenceRunOut(
        id=row["id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        run_type=row["run_type"],
        provider=row["provider"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        schema_version=row["schema_version"],
        decision_method=row["decision_method"],
        status=row["status"],
        error_details=row["error_details"],
        is_mock=bool(row["is_mock"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _pattern_point_out(row) -> ProbePatternPointOut:
    return ProbePatternPointOut(
        id=row["id"],
        pattern_id=row["pattern_id"],
        system_id=row["system_id"],
        component_id=row["component_id"],
        path=row["path"],
        symbol=row["symbol"],
        line_start=row["line_start"],
        line_end=row["line_end"],
        reason=row["reason"],
        recommended_mode=row["recommended_mode"],
        side_effect_risk=row["side_effect_risk"],
        replayability=row["replayability"],
        signature=row["signature"],
        symbol_source_hash=row["symbol_source_hash"],
        symbol_body_hash=row["symbol_body_hash"],
        docstring=row["docstring"],
        status=row["status"],
        removed_at=row["removed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_out(row) -> ProbePatternEventOut:
    try:
        detail = json.loads(row["detail"] or "{}")
    except json.JSONDecodeError:
        detail = {}
    return ProbePatternEventOut(
        id=row["id"],
        pattern_id=row["pattern_id"],
        event_type=row["event_type"],
        detail=detail if isinstance(detail, dict) else {},
        created_at=row["created_at"],
    )


def _evidence_out(raw: str) -> List[ReconcileEvidenceOut]:
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            out.append(ReconcileEvidenceOut(
                path=str(item.get("path", "")),
                start_line=int(item.get("start_line", 0) or 0),
                end_line=int(item.get("end_line", 0) or 0),
                summary=str(item.get("summary", "")),
            ))
    return out


def _investigation_out(raw: Optional[str]) -> Optional[PatternInvestigationOut]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return PatternInvestigationOut(
        summary=str(data.get("summary", "")),
        recommendation=str(data.get("recommendation", "")),
        proposed_target_path=data.get("proposed_target_path"),
        proposed_target_symbol=data.get("proposed_target_symbol"),
        evidence=[
            ReconcileEvidenceOut(
                path=str(e.get("path", "")),
                start_line=int(e.get("start_line", 0) or 0),
                end_line=int(e.get("end_line", 0) or 0),
                summary=str(e.get("summary", "")),
            )
            for e in data.get("evidence", [])
            if isinstance(e, dict)
        ],
        is_mock=bool(data.get("is_mock", False)),
        created_at=float(data.get("created_at", 0.0)),
    )


def _reconcile_point_out(row) -> ReconcilePointOut:
    return ReconcilePointOut(
        id=row["id"],
        reconciliation_id=row["reconciliation_id"],
        pattern_point_id=row["pattern_point_id"],
        classification=row["classification"],
        decision_method=row["decision_method"],
        target_path=row["target_path"],
        target_symbol=row["target_symbol"],
        target_line_start=row["target_line_start"],
        target_line_end=row["target_line_end"],
        confidence=row["confidence"],
        explanation=row["explanation"],
        hypothesis=row["hypothesis"],
        question=row["question"],
        evidence=_evidence_out(row["evidence_json"]),
        denylist_hit=row["denylist_hit"],
        body_changed=bool(row["body_changed"]),
        user_decision=row["user_decision"],
        decided_at=row["decided_at"],
        investigation=_investigation_out(row["investigation_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _reconciliation_out(conn, row) -> ProbePatternReconciliationOut:
    point_rows = conn.execute(
        """SELECT * FROM probe_pattern_reconcile_points
           WHERE reconciliation_id = ? ORDER BY id""",
        (row["id"],),
    ).fetchall()
    run_out = None
    is_mock = False
    if row["intelligence_run_id"] is not None:
        run_row = conn.execute(
            "SELECT * FROM intelligence_runs WHERE id = ?",
            (row["intelligence_run_id"],),
        ).fetchone()
        if run_row:
            run_out = _intelligence_run_out(run_row)
            is_mock = bool(run_row["is_mock"])
    try:
        summary = json.loads(row["summary_json"] or "{}")
    except json.JSONDecodeError:
        summary = {}
    return ProbePatternReconciliationOut(
        id=row["id"],
        pattern_id=row["pattern_id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        commit_sha=row["commit_sha"],
        intelligence_run_id=row["intelligence_run_id"],
        status=row["status"],
        error=row["error"],
        summary={k: int(v) for k, v in summary.items()} if isinstance(summary, dict) else {},
        points=[_reconcile_point_out(r) for r in point_rows],
        intelligence_run=run_out,
        is_mock=is_mock,
        created_at=row["created_at"],
    )


def _pattern_out(conn, row, include_events: bool = False) -> ProbePatternOut:
    point_rows = conn.execute(
        "SELECT * FROM probe_pattern_points WHERE pattern_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    rec_row = conn.execute(
        """SELECT * FROM probe_pattern_reconciliations
           WHERE pattern_id = ? ORDER BY id DESC LIMIT 1""",
        (row["id"],),
    ).fetchone()
    latest_rec = _reconciliation_out(conn, rec_row) if rec_row else None
    pending = 0
    if latest_rec is not None:
        pending = sum(
            1 for p in latest_rec.points
            if p.user_decision == "pending" and p.classification != "exact_match"
        )
    events: List[ProbePatternEventOut] = []
    if include_events:
        event_rows = conn.execute(
            """SELECT * FROM probe_pattern_events
               WHERE pattern_id = ? ORDER BY id DESC LIMIT 50""",
            (row["id"],),
        ).fetchall()
        events = [_event_out(r) for r in event_rows]
    return ProbePatternOut(
        id=row["id"],
        system_id=row["system_id"],
        name=row["name"],
        feature_id=row["feature_id"],
        capability=row["capability"],
        objective=row["objective"],
        description=row["description"],
        status=row["status"],
        origin=row["origin"],
        source_plan_id=row["source_plan_id"],
        source_snapshot_id=row["source_snapshot_id"],
        source_commit_sha=row["source_commit_sha"],
        superseded_by_id=row["superseded_by_id"],
        last_used_at=row["last_used_at"],
        last_reconciled_at=row["last_reconciled_at"],
        point_count=len(point_rows),
        removed_point_count=sum(
            1 for r in point_rows if r["status"] == "removed_from_production"
        ),
        points=[_pattern_point_out(r) for r in point_rows],
        events=events,
        latest_reconciliation=latest_rec,
        pending_decision_count=pending,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _get_pattern_row(conn, pattern_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM probe_patterns WHERE id = ? AND system_id = ?",
        (pattern_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Probe pattern not found")
    return row


def _removal_patch_out(row) -> ProbeRemovalPatchOut:
    try:
        skipped = json.loads(row["skipped"] or "[]")
    except json.JSONDecodeError:
        skipped = []
    return ProbeRemovalPatchOut(
        id=row["id"],
        pattern_id=row["pattern_id"],
        system_id=row["system_id"],
        snapshot_id=row["snapshot_id"],
        commit_sha=row["commit_sha"],
        diff=row["diff"],
        skipped=skipped,
        status=row["status"],
        error=row["error"],
        cleanup_state=row["cleanup_state"],
        cleanup_error=row["cleanup_error"],
        apply_status=row["apply_status"],
        apply_error=row["apply_error"],
        applied_at=row["applied_at"],
        applied_by_user_id=row["applied_by_user_id"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Instrumentation scan (deterministic, pre-release removal entry point)
# ---------------------------------------------------------------------------


@router.get(
    "/repository/probe-instrumentation",
    response_model=InstrumentationScanOut,
)
def scan_probe_instrumentation(
    system_id: int = Depends(get_system_id),
) -> InstrumentationScanOut:
    """List @probe-instrumented symbols in the latest indexed snapshot.

    Fully deterministic: decorator presence is a structural fact from the
    committed-snapshot symbol index. Each hit is linked back to the probe
    plan point and patterns it came from so removal keeps its context.

    probe-agent:
      role: API boundary for scanning committed probe instrumentation
      capability: probe-pattern-lifecycle
      element_type: boundary
      consumers: [dashboard]
      operation_kind: read
      state_effects: [database-read]
      probe_value: verify only probe-decorated symbols from the pinned snapshot are reported with their plan/pattern context
    """
    with get_conn() as conn:
        snapshot_row = _require_indexed_snapshot(conn, system_id)
        snapshot_id = snapshot_row["id"]
        symbol_rows = conn.execute(
            """SELECT * FROM code_symbols
               WHERE snapshot_id = ? AND kind IN ('function', 'async_function')
               ORDER BY path, start_line""",
            (snapshot_id,),
        ).fetchall()

        probes = []
        for row in symbol_rows:
            try:
                decorators = json.loads(row["decorators"] or "[]")
            except json.JSONDecodeError:
                decorators = []
            if not any(
                _decorator_is_probe(d) for d in decorators if isinstance(d, str)
            ):
                continue

            plan_row = conn.execute(
                """SELECT pp.id AS point_id, pp.plan_id, pp.feature_id,
                          pp.reason, pp.recommended_mode, pl.objective
                   FROM probe_points pp
                   JOIN probe_plans pl ON pp.plan_id = pl.id
                   WHERE pp.system_id = ? AND pp.path = ? AND pp.symbol = ?
                   ORDER BY pp.id DESC LIMIT 1""",
                (system_id, row["path"], row["qualified_name"]),
            ).fetchone()
            pattern_rows = conn.execute(
                """SELECT DISTINCT pattern_id FROM probe_pattern_points
                   WHERE system_id = ? AND path = ? AND symbol = ?""",
                (system_id, row["path"], row["qualified_name"]),
            ).fetchall()

            probes.append(InstrumentedProbeOut(
                path=row["path"],
                symbol=row["qualified_name"],
                line_start=row["start_line"],
                line_end=row["end_line"],
                component_id=row["component_id"],
                docstring=row["docstring"],
                linked_plan_id=plan_row["plan_id"] if plan_row else None,
                linked_feature_id=plan_row["feature_id"] if plan_row else None,
                linked_objective=plan_row["objective"] if plan_row else None,
                linked_reason=plan_row["reason"] if plan_row else None,
                linked_recommended_mode=(
                    plan_row["recommended_mode"] if plan_row else None
                ),
                pattern_ids=[r["pattern_id"] for r in pattern_rows],
            ))

    return InstrumentationScanOut(
        system_id=system_id,
        snapshot_id=snapshot_id,
        commit_sha=snapshot_row["commit_sha"],
        probes=probes,
    )


# ---------------------------------------------------------------------------
# Pattern CRUD
# ---------------------------------------------------------------------------


@router.get("/repository/probe-patterns", response_model=ProbePatternsListOut)
def list_probe_patterns(
    system_id: int = Depends(get_system_id),
) -> ProbePatternsListOut:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM probe_patterns WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
        patterns = [_pattern_out(conn, row) for row in rows]
    return ProbePatternsListOut(system_id=system_id, patterns=patterns)


@router.post(
    "/repository/probe-patterns",
    response_model=ProbePatternOut,
    status_code=201,
)
def create_probe_pattern(
    payload: ProbePatternCreateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProbePatternOut:
    """Save probe points as a reusable observation pattern.

    Captures structural facts (signature, source/body hashes) from the latest
    ready snapshot so later reconciliation can decide exact matches
    deterministically.

    probe-agent:
      role: API boundary for saving probe patterns with pinned-snapshot facts
      capability: probe-pattern-lifecycle
      element_type: boundary
      consumers: [dashboard]
      operation_kind: write
      state_effects: [database-write]
      probe_value: verify pattern points capture snapshot provenance and lifecycle events are recorded
    """
    now = time.time()
    with get_conn() as conn:
        snapshot_row = _latest_ready_snapshot(conn, system_id)
        snapshot_id = snapshot_row["id"] if snapshot_row else None
        commit_sha = snapshot_row["commit_sha"] if snapshot_row else ""

        if payload.source_plan_id is not None:
            plan_row = conn.execute(
                "SELECT id FROM probe_plans WHERE id = ? AND system_id = ?",
                (payload.source_plan_id, system_id),
            ).fetchone()
            if plan_row is None:
                raise HTTPException(
                    status_code=400, detail="source_plan_id not found for this system",
                )

        sources: Dict[str, str] = {}
        if snapshot_id is not None:
            sources = _load_python_sources(conn, snapshot_id)

        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO probe_patterns
                       (system_id, name, feature_id, capability, objective,
                        description, status, origin, source_plan_id,
                        source_snapshot_id, source_commit_sha,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, payload.name.strip(), payload.feature_id.strip(),
                    payload.capability.strip(), payload.objective.strip(),
                    payload.description.strip(), payload.origin,
                    payload.source_plan_id, snapshot_id, commit_sha, now, now,
                ),
            )
            pattern_id = cur.lastrowid

            for point in payload.points:
                symbol_row = None
                if snapshot_id is not None:
                    symbol_row = conn.execute(
                        """SELECT * FROM code_symbols
                           WHERE snapshot_id = ? AND path = ? AND qualified_name = ?""",
                        (snapshot_id, point.path, point.symbol),
                    ).fetchone()
                signature = ""
                if symbol_row is not None:
                    signature = extract_signature(
                        sources.get(point.path, ""), point.symbol,
                    )
                conn.execute(
                    """INSERT INTO probe_pattern_points
                           (pattern_id, system_id, component_id, path, symbol,
                            line_start, line_end, reason, recommended_mode,
                            side_effect_risk, replayability, signature,
                            symbol_source_hash, symbol_body_hash, docstring,
                            status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'saved', ?, ?)""",
                    (
                        pattern_id, system_id,
                        point.component_id.strip() or _component_id_for(point.symbol),
                        point.path, point.symbol,
                        symbol_row["start_line"] if symbol_row else 0,
                        symbol_row["end_line"] if symbol_row else 0,
                        point.reason.strip(), point.recommended_mode,
                        point.side_effect_risk, point.replayability.strip(),
                        signature,
                        symbol_row["symbol_source_hash"] if symbol_row else None,
                        symbol_row["symbol_body_hash"] if symbol_row else None,
                        symbol_row["docstring"] if symbol_row else None,
                        now, now,
                    ),
                )

            _record_event(
                conn, pattern_id, system_id, "created",
                {
                    "origin": payload.origin,
                    "point_count": len(payload.points),
                    "snapshot_id": snapshot_id,
                    "commit_sha": commit_sha,
                },
                principal.user_id,
            )
            row = conn.execute(
                "SELECT * FROM probe_patterns WHERE id = ?", (pattern_id,),
            ).fetchone()
            result = _pattern_out(conn, row, include_events=True)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result


@router.get(
    "/repository/probe-patterns/{pattern_id}",
    response_model=ProbePatternOut,
)
def get_probe_pattern(
    pattern_id: int,
    system_id: int = Depends(get_system_id),
) -> ProbePatternOut:
    with get_conn() as conn:
        row = _get_pattern_row(conn, pattern_id, system_id)
        return _pattern_out(conn, row, include_events=True)


@router.patch(
    "/repository/probe-patterns/{pattern_id}",
    response_model=ProbePatternOut,
)
def update_probe_pattern(
    pattern_id: int,
    payload: ProbePatternUpdateRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProbePatternOut:
    now = time.time()
    with get_conn() as conn:
        row = _get_pattern_row(conn, pattern_id, system_id)
        updates = []
        params = []
        changed: Dict[str, str] = {}
        for field_name in ("name", "feature_id", "capability", "objective", "description"):
            value = getattr(payload, field_name)
            if value is not None and value != row[field_name]:
                updates.append(f"{field_name} = ?")
                params.append(value.strip() if field_name == "name" else value)
                changed[field_name] = value
        if payload.status is not None and payload.status != row["status"]:
            if row["status"] == "superseded":
                raise HTTPException(
                    status_code=409,
                    detail="Superseded patterns cannot change status",
                )
            updates.append("status = ?")
            params.append(payload.status)
            changed["status"] = payload.status
        if not updates:
            return _pattern_out(conn, row, include_events=True)

        updates.append("updated_at = ?")
        params.append(now)
        params.extend([pattern_id, system_id])
        conn.execute("BEGIN")
        try:
            conn.execute(
                f"UPDATE probe_patterns SET {', '.join(updates)} "
                "WHERE id = ? AND system_id = ?",
                params,
            )
            event_type = "archived" if changed.get("status") == "archived" else (
                "renamed" if "name" in changed else "updated"
            )
            _record_event(
                conn, pattern_id, system_id, event_type,
                {"changed_fields": sorted(changed.keys())},
                principal.user_id,
            )
            row = conn.execute(
                "SELECT * FROM probe_patterns WHERE id = ?", (pattern_id,),
            ).fetchone()
            result = _pattern_out(conn, row, include_events=True)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result


# ---------------------------------------------------------------------------
# Pre-release removal patches
# ---------------------------------------------------------------------------


@router.post(
    "/repository/probe-patterns/{pattern_id}/removal-patches",
    response_model=ProbeRemovalPatchOut,
    status_code=201,
)
def generate_removal_patch_endpoint(
    pattern_id: int,
    payload: ProbeRemovalPatchCreateRequest,
    system_id: int = Depends(get_system_id),
) -> ProbeRemovalPatchOut:
    """Generate a reviewable diff that removes the pattern's @probe decorators.

    The diff is produced in an isolated worktree pinned to the latest ready
    snapshot commit; the target repository is untouched until the explicit,
    commit-confirmed apply endpoint.

    probe-agent:
      role: API boundary for pre-release probe removal patch generation
      capability: probe-pattern-lifecycle
      element_type: boundary
      consumers: [dashboard]
      operation_kind: orchestration
      state_effects: [database-write]
      probe_value: verify removal diffs are generated in isolation and persisted with skip reasons
    """
    with get_conn() as conn:
        _get_pattern_row(conn, pattern_id, system_id)
        snapshot_row = _latest_ready_snapshot(conn, system_id)
        if snapshot_row is None:
            raise HTTPException(
                status_code=400, detail="No ready snapshot. Create a snapshot first.",
            )
        if not snapshot_row["repo_path"]:
            raise HTTPException(
                status_code=409, detail="Snapshot repository path is unavailable",
            )
        point_rows = conn.execute(
            """SELECT * FROM probe_pattern_points
               WHERE pattern_id = ? AND status = 'saved' ORDER BY id""",
            (pattern_id,),
        ).fetchall()
        if payload.point_ids is not None:
            wanted = set(payload.point_ids)
            point_rows = [r for r in point_rows if r["id"] in wanted]
    if not point_rows:
        raise HTTPException(
            status_code=400,
            detail="No removable probe points in this pattern",
        )

    worktree_base = os.getenv("PROBE_WORKTREE_BASE", "/tmp/probe-worktrees")
    os.makedirs(worktree_base, exist_ok=True)

    patch_result = generate_removal_patch(
        repo_path=snapshot_row["repo_path"],
        commit_sha=snapshot_row["commit_sha"],
        points=[
            RemovalPoint(path=r["path"], symbol=r["symbol"]) for r in point_rows
        ],
        worktree_base=worktree_base,
    )

    now = time.time()
    status = "generated" if patch_result.error is None else "failed"
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO probe_removal_patches
                       (pattern_id, system_id, snapshot_id, commit_sha, diff,
                        point_ids, skipped, status, error,
                        cleanup_state, cleanup_error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pattern_id, system_id, snapshot_row["id"],
                    snapshot_row["commit_sha"], patch_result.diff,
                    json.dumps([r["id"] for r in point_rows]),
                    json.dumps(patch_result.skipped),
                    status, patch_result.error,
                    patch_result.cleanup_state, patch_result.cleanup_error, now,
                ),
            )
            patch_id = cur.lastrowid
            _record_event(
                conn, pattern_id, system_id, "removal_patch_generated",
                {
                    "patch_id": patch_id,
                    "status": status,
                    "point_count": len(point_rows),
                    "commit_sha": snapshot_row["commit_sha"],
                },
            )
            row = conn.execute(
                "SELECT * FROM probe_removal_patches WHERE id = ?", (patch_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return _removal_patch_out(row)


@router.get(
    "/repository/probe-patterns/{pattern_id}/removal-patches",
    response_model=List[ProbeRemovalPatchOut],
)
def list_removal_patches(
    pattern_id: int,
    system_id: int = Depends(get_system_id),
) -> List[ProbeRemovalPatchOut]:
    with get_conn() as conn:
        _get_pattern_row(conn, pattern_id, system_id)
        rows = conn.execute(
            """SELECT * FROM probe_removal_patches
               WHERE pattern_id = ? ORDER BY id DESC""",
            (pattern_id,),
        ).fetchall()
        return [_removal_patch_out(r) for r in rows]


@router.get("/repository/probe-removal-patches/{patch_id}/download")
def download_removal_patch(
    patch_id: int,
    system_id: int = Depends(get_system_id),
) -> Response:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT diff FROM probe_removal_patches WHERE id = ? AND system_id = ?",
            (patch_id, system_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Removal patch not found")
    return Response(
        content=row["diff"],
        media_type="text/x-diff",
        headers={
            "Content-Disposition":
                f'attachment; filename="probe-removal-{patch_id}.diff"'
        },
    )


@router.post(
    "/repository/probe-removal-patches/{patch_id}/apply",
    response_model=ProbeRemovalPatchOut,
)
def apply_removal_patch_endpoint(
    patch_id: int,
    payload: ProbeRemovalPatchApplyRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProbeRemovalPatchOut:
    """Apply an approved removal diff to the target working tree.

    This is the explicit write boundary shared with instrumentation patches:
    the repository HEAD must still match the confirmed snapshot commit and
    the working tree must be clean.

    probe-agent:
      role: Explicit apply boundary for pre-release probe removal
      capability: probe-pattern-lifecycle
      element_type: boundary
      consumers: [dashboard]
      operation_kind: write
      state_effects: [database-write, file-write]
      probe_value: verify removal patches refuse stale commits and dirty trees and mark pattern points removed only after a successful apply
    """
    from ..patch_generator import apply_patch_to_repository

    with get_conn() as conn:
        patch_row = conn.execute(
            "SELECT * FROM probe_removal_patches WHERE id = ? AND system_id = ?",
            (patch_id, system_id),
        ).fetchone()
        if patch_row is None:
            raise HTTPException(status_code=404, detail="Removal patch not found")
        snapshot_row = conn.execute(
            "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (patch_row["snapshot_id"], system_id),
        ).fetchone()

    if patch_row["apply_status"] == "applied":
        raise HTTPException(status_code=409, detail="Patch has already been applied")
    if patch_row["status"] == "failed" or not patch_row["diff"].strip():
        raise HTTPException(status_code=400, detail="Patch is not applicable")
    if payload.expected_commit_sha != patch_row["commit_sha"]:
        raise HTTPException(
            status_code=409,
            detail="Confirmation commit does not match the patch snapshot",
        )
    if snapshot_row is None or not snapshot_row["repo_path"]:
        raise HTTPException(
            status_code=409, detail="Snapshot repository path is unavailable",
        )

    apply_error = apply_patch_to_repository(
        snapshot_row["repo_path"],
        patch_row["commit_sha"],
        patch_row["diff"],
    )
    now = time.time()
    with get_conn() as conn:
        if apply_error:
            conn.execute(
                """UPDATE probe_removal_patches
                   SET apply_status = 'failed', apply_error = ?
                   WHERE id = ?""",
                (apply_error, patch_id),
            )
            raise HTTPException(status_code=409, detail=apply_error)
        conn.execute("BEGIN")
        try:
            conn.execute(
                """UPDATE probe_removal_patches
                   SET apply_status = 'applied', apply_error = NULL,
                       applied_at = ?, applied_by_user_id = ?
                   WHERE id = ?""",
                (now, principal.user_id, patch_id),
            )
            try:
                point_ids = json.loads(patch_row["point_ids"] or "[]")
            except json.JSONDecodeError:
                point_ids = []
            for point_id in point_ids:
                conn.execute(
                    """UPDATE probe_pattern_points
                       SET status = 'removed_from_production',
                           removed_at = ?, updated_at = ?
                       WHERE id = ? AND pattern_id = ?""",
                    (now, now, point_id, patch_row["pattern_id"]),
                )
            conn.execute(
                "UPDATE probe_patterns SET last_used_at = ?, updated_at = ? WHERE id = ?",
                (now, now, patch_row["pattern_id"]),
            )
            _record_event(
                conn, patch_row["pattern_id"], system_id, "removal_applied",
                {
                    "patch_id": patch_id,
                    "point_ids": point_ids,
                    "commit_sha": patch_row["commit_sha"],
                },
                principal.user_id,
            )
            row = conn.execute(
                "SELECT * FROM probe_removal_patches WHERE id = ?", (patch_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return _removal_patch_out(row)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@router.post(
    "/repository/probe-patterns/{pattern_id}/reconcile",
    response_model=ProbePatternReconciliationOut,
    status_code=201,
)
def reconcile_probe_pattern(
    pattern_id: int,
    system_id: int = Depends(get_system_id),
) -> ProbePatternReconciliationOut:
    """Reconcile a pattern's saved points against the latest snapshot.

    Deterministic structural checks classify exact matches, signature
    changes, denylist hits, and verbatim moves; everything else requires the
    reasoning model. If the model is unavailable or returns invalid output,
    the run is persisted as failed — deterministic results stay available and
    nothing falls back to heuristics.

    probe-agent:
      role: API boundary orchestrating pattern reconciliation
      capability: probe-pattern-lifecycle
      element_type: boundary
      consumers: [dashboard]
      operation_kind: orchestration
      state_effects: [database-write, external-api]
      probe_value: verify deterministic and reasoning classifications are persisted with run metadata and pattern status transitions
    """
    with get_conn() as conn:
        pattern_row = _get_pattern_row(conn, pattern_id, system_id)
        if pattern_row["status"] in ("archived", "superseded"):
            raise HTTPException(
                status_code=409,
                detail="Archived or superseded patterns cannot be reconciled",
            )
        snapshot_row = _require_indexed_snapshot(conn, system_id)
        snapshot_id = snapshot_row["id"]
        commit_sha = snapshot_row["commit_sha"]

        point_rows = conn.execute(
            "SELECT * FROM probe_pattern_points WHERE pattern_id = ? ORDER BY id",
            (pattern_id,),
        ).fetchall()
        if not point_rows:
            raise HTTPException(
                status_code=400, detail="Pattern has no probe points",
            )
        symbols = _load_snapshot_symbols(conn, snapshot_id)
        sources = _load_python_sources(conn, snapshot_id)

    facts = [
        PatternPointFacts(
            pattern_point_id=r["id"],
            path=r["path"],
            symbol=r["symbol"],
            component_id=r["component_id"],
            reason=r["reason"],
            recommended_mode=r["recommended_mode"],
            signature=r["signature"],
            symbol_source_hash=r["symbol_source_hash"],
            symbol_body_hash=r["symbol_body_hash"],
            docstring=r["docstring"],
            objective=pattern_row["objective"],
        )
        for r in point_rows
    ]

    symbols_by_key = {(s.path, s.qualified_name): s for s in symbols}
    symbols_by_body_hash: Dict[str, List[SnapshotSymbol]] = {}
    for s in symbols:
        if s.symbol_body_hash:
            symbols_by_body_hash.setdefault(s.symbol_body_hash, []).append(s)

    results: List[ReconcilePointResult] = []
    unresolved: List[PatternPointFacts] = []
    for fact in facts:
        deterministic = classify_deterministic(
            fact, symbols_by_key, symbols_by_body_hash, sources,
        )
        if deterministic is not None:
            results.append(deterministic)
        else:
            unresolved.append(fact)

    llm_config = LLMConfig.intelligence_from_env()
    started_at = time.time()
    error: Optional[str] = None
    used_reasoning = False
    is_mock = False

    if unresolved:
        used_reasoning = True
        is_mock = llm_config.provider == "mock"
        try:
            if llm_config.provider != "mock" and not is_reasoning_model(
                llm_config.provider, llm_config.model
            ):
                raise LLMError(
                    "Pattern reconciliation requires a configured reasoning model"
                )
            llm_client = create_llm_client(llm_config)
            reasoning_results = classify_with_reasoning(
                llm_client,
                llm_config,
                objective=pattern_row["objective"],
                points=unresolved,
                symbols=symbols,
                file_sources=sources,
            )
            results.extend(reasoning_results)
        except (LLMError, ReconcileValidationError) as exc:
            error = str(exc)
    completed_at = time.time()

    run_status = "completed" if error is None else "failed"
    now = time.time()
    summary: Dict[str, int] = {}
    for r in results:
        summary[r.classification] = summary.get(r.classification, 0) + 1

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, error_details, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'pattern_reconcile', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, snapshot_id,
                    llm_config.provider if used_reasoning else "deterministic",
                    llm_config.model if used_reasoning else "n/a",
                    RECONCILE_PROMPT_VERSION, RECONCILE_SCHEMA_VERSION,
                    "reasoning_llm" if used_reasoning else "deterministic",
                    run_status, error,
                    1 if (used_reasoning and is_mock) else 0,
                    started_at, completed_at,
                ),
            )
            run_id = cur.lastrowid

            cur = conn.execute(
                """INSERT INTO probe_pattern_reconciliations
                       (pattern_id, system_id, snapshot_id, commit_sha,
                        intelligence_run_id, status, error, summary_json,
                        created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pattern_id, system_id, snapshot_id, commit_sha,
                    run_id, run_status, error, json.dumps(summary), now,
                ),
            )
            reconciliation_id = cur.lastrowid

            for r in results:
                conn.execute(
                    """INSERT INTO probe_pattern_reconcile_points
                           (reconciliation_id, pattern_point_id, system_id,
                            classification, decision_method, target_path,
                            target_symbol, target_line_start, target_line_end,
                            confidence, explanation, hypothesis, question,
                            evidence_json, denylist_hit, body_changed,
                            user_decision, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'pending', ?, ?)""",
                    (
                        reconciliation_id, r.pattern_point_id, system_id,
                        r.classification, r.decision_method, r.target_path,
                        r.target_symbol, r.target_line_start, r.target_line_end,
                        r.confidence, r.explanation, r.hypothesis, r.question,
                        json.dumps([
                            {
                                "path": e.path,
                                "start_line": e.start_line,
                                "end_line": e.end_line,
                                "summary": e.summary,
                            }
                            for e in r.evidence
                        ]),
                        r.denylist_hit, 1 if r.body_changed else 0, now, now,
                    ),
                )

            if run_status == "completed":
                all_exact = all(
                    r.classification == "exact_match" for r in results
                )
                new_status = "active" if all_exact else "stale"
                conn.execute(
                    """UPDATE probe_patterns
                       SET last_reconciled_at = ?, status = ?, updated_at = ?
                       WHERE id = ? AND status IN ('active', 'stale')""",
                    (now, new_status, now, pattern_id),
                )
                conn.execute(
                    """UPDATE probe_patterns
                       SET last_reconciled_at = ?, updated_at = ?
                       WHERE id = ? AND status NOT IN ('active', 'stale')""",
                    (now, now, pattern_id),
                )
            _record_event(
                conn, pattern_id, system_id, "reconciled",
                {
                    "reconciliation_id": reconciliation_id,
                    "status": run_status,
                    "summary": summary,
                    "snapshot_id": snapshot_id,
                    "commit_sha": commit_sha,
                    "error": error,
                },
            )
            rec_row = conn.execute(
                "SELECT * FROM probe_pattern_reconciliations WHERE id = ?",
                (reconciliation_id,),
            ).fetchone()
            result = _reconciliation_out(conn, rec_row)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result


@router.get(
    "/repository/probe-patterns/{pattern_id}/reconciliations",
    response_model=List[ProbePatternReconciliationOut],
)
def list_reconciliations(
    pattern_id: int,
    system_id: int = Depends(get_system_id),
) -> List[ProbePatternReconciliationOut]:
    with get_conn() as conn:
        _get_pattern_row(conn, pattern_id, system_id)
        rows = conn.execute(
            """SELECT * FROM probe_pattern_reconciliations
               WHERE pattern_id = ? ORDER BY id DESC LIMIT 10""",
            (pattern_id,),
        ).fetchall()
        return [_reconciliation_out(conn, r) for r in rows]


@router.put(
    "/repository/pattern-reconcile-points/{point_id}/decision",
    response_model=ReconcilePointOut,
)
def decide_reconcile_point(
    point_id: int,
    payload: ReconcileDecisionRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ReconcilePointOut:
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM probe_pattern_reconcile_points
               WHERE id = ? AND system_id = ?""",
            (point_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Reconcile point not found")
        if payload.decision == "accepted" and (
            row["denylist_hit"] or row["classification"] == "unsafe"
        ):
            raise HTTPException(
                status_code=409,
                detail="Safety-denylisted reconcile points cannot be accepted",
            )
        if payload.decision == "accepted" and row["classification"] == "missing":
            raise HTTPException(
                status_code=409,
                detail="A missing point has no target to accept",
            )
        conn.execute("BEGIN")
        try:
            conn.execute(
                """UPDATE probe_pattern_reconcile_points
                   SET user_decision = ?, decided_at = ?, updated_at = ?
                   WHERE id = ?""",
                (payload.decision, now, now, point_id),
            )
            rec_row = conn.execute(
                "SELECT pattern_id FROM probe_pattern_reconciliations WHERE id = ?",
                (row["reconciliation_id"],),
            ).fetchone()
            if rec_row:
                _record_event(
                    conn, rec_row["pattern_id"], system_id, "decision",
                    {
                        "reconcile_point_id": point_id,
                        "decision": payload.decision,
                        "classification": row["classification"],
                    },
                    principal.user_id,
                )
            row = conn.execute(
                "SELECT * FROM probe_pattern_reconcile_points WHERE id = ?",
                (point_id,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return _reconcile_point_out(row)


@router.post(
    "/repository/pattern-reconcile-points/{point_id}/investigate",
    response_model=ReconcilePointOut,
)
def investigate_reconcile_point(
    point_id: int,
    system_id: int = Depends(get_system_id),
) -> ReconcilePointOut:
    """Investigation assistance when the user answers "I don't know".

    Reads related implementation from the reconciliation's pinned snapshot
    (target file, original file, evidence files, matching tests) and asks the
    reasoning model for a short current-state summary plus a recommendation.
    Failures are persisted; there is no heuristic fallback.

    probe-agent:
      role: API boundary for reconcile investigation assistance
      capability: probe-pattern-lifecycle
      element_type: boundary
      consumers: [dashboard]
      operation_kind: orchestration
      state_effects: [database-write, external-api]
      probe_value: verify investigation reads only pinned snapshot content and persists reasoning run metadata
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM probe_pattern_reconcile_points
               WHERE id = ? AND system_id = ?""",
            (point_id, system_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Reconcile point not found")
        rec_row = conn.execute(
            "SELECT * FROM probe_pattern_reconciliations WHERE id = ?",
            (row["reconciliation_id"],),
        ).fetchone()
        pattern_row = conn.execute(
            "SELECT * FROM probe_patterns WHERE id = ?",
            (rec_row["pattern_id"],),
        ).fetchone()
        point_row = conn.execute(
            "SELECT * FROM probe_pattern_points WHERE id = ?",
            (row["pattern_point_id"],),
        ).fetchone()
        if point_row is None:
            raise HTTPException(status_code=404, detail="Pattern point not found")
        snapshot_id = rec_row["snapshot_id"]
        sources = _load_python_sources(conn, snapshot_id)
        symbols = _load_snapshot_symbols(conn, snapshot_id)

    fact = PatternPointFacts(
        pattern_point_id=point_row["id"],
        path=point_row["path"],
        symbol=point_row["symbol"],
        component_id=point_row["component_id"],
        reason=point_row["reason"],
        recommended_mode=point_row["recommended_mode"],
        signature=point_row["signature"],
        symbol_source_hash=point_row["symbol_source_hash"],
        symbol_body_hash=point_row["symbol_body_hash"],
        docstring=point_row["docstring"],
        objective=pattern_row["objective"],
    )

    # Bounded, pinned-snapshot-only excerpt selection (retrieval, not decision):
    # the reconcile target file, the original file, evidence files, and up to
    # two test files that mention the symbol's terminal name.
    excerpt_paths: List[str] = []
    if row["target_path"]:
        excerpt_paths.append(row["target_path"])
    if point_row["path"] in sources:
        excerpt_paths.append(point_row["path"])
    for ev in _evidence_out(row["evidence_json"]):
        excerpt_paths.append(ev.path)
    terminal = point_row["symbol"].split(".")[-1]
    test_paths = [
        p for p in sources
        if ("test" in p.lower()) and terminal in sources[p]
    ][:2]
    excerpt_paths.extend(test_paths)

    seen = set()
    excerpts: List[Tuple[str, str]] = []
    for p in excerpt_paths:
        if p in seen or p not in sources:
            continue
        seen.add(p)
        excerpts.append((p, sources[p][:6000]))
        if len(excerpts) >= 5:
            break

    llm_config = LLMConfig.intelligence_from_env()
    started_at = time.time()
    error: Optional[str] = None
    investigation = None
    try:
        if llm_config.provider != "mock" and not is_reasoning_model(
            llm_config.provider, llm_config.model
        ):
            raise LLMError(
                "Investigation requires a configured reasoning model"
            )
        llm_client = create_llm_client(llm_config)
        investigation = investigate_point(
            llm_client,
            llm_config,
            point=fact,
            hypothesis=row["hypothesis"],
            excerpts=excerpts,
            symbols_by_key={(s.path, s.qualified_name): s for s in symbols},
            known_paths=set(sources.keys()),
        )
    except (LLMError, ReconcileValidationError) as exc:
        error = str(exc)
    completed_at = time.time()

    now = time.time()
    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, error_details, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'pattern_investigate', ?, ?, ?, ?,
                           'reasoning_llm', ?, ?, ?, ?, ?)""",
                (
                    system_id, snapshot_id,
                    llm_config.provider, llm_config.model,
                    INVESTIGATE_PROMPT_VERSION, RECONCILE_SCHEMA_VERSION,
                    "completed" if error is None else "failed", error,
                    1 if llm_config.provider == "mock" else 0,
                    started_at, completed_at,
                ),
            )
            if investigation is not None:
                conn.execute(
                    """UPDATE probe_pattern_reconcile_points
                       SET investigation_json = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        json.dumps({
                            "summary": investigation.summary,
                            "recommendation": investigation.recommendation,
                            "proposed_target_path": investigation.proposed_target_path,
                            "proposed_target_symbol": investigation.proposed_target_symbol,
                            "evidence": [
                                {
                                    "path": e.path,
                                    "start_line": e.start_line,
                                    "end_line": e.end_line,
                                    "summary": e.summary,
                                }
                                for e in investigation.evidence
                            ],
                            "is_mock": False,
                            "created_at": now,
                        }),
                        now, point_id,
                    ),
                )
                _record_event(
                    conn, rec_row["pattern_id"], system_id, "investigated",
                    {"reconcile_point_id": point_id},
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    if error is not None:
        raise HTTPException(status_code=502, detail=error)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM probe_pattern_reconcile_points WHERE id = ?",
            (point_id,),
        ).fetchone()
        return _reconcile_point_out(row)


# ---------------------------------------------------------------------------
# Probe Plan creation from an approved reconciliation
# ---------------------------------------------------------------------------


@router.post(
    "/repository/probe-patterns/{pattern_id}/reconciliations/{reconciliation_id}/create-plan",
    response_model=ProbePlanOut,
    status_code=201,
)
def create_plan_from_reconciliation(
    pattern_id: int,
    reconciliation_id: int,
    payload: CreatePlanFromReconcileRequest,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ProbePlanOut:
    """Create a Probe Plan from reconciled points so re-attachment goes
    through the existing approve -> patch -> validate -> apply gates.

    Eligible points: exact matches (unless rejected) and explicitly accepted
    moved/changed/split points with a resolved target. Missing and unsafe
    points never become plan points.

    probe-agent:
      role: API boundary converting reconcile decisions into a Probe Plan
      capability: probe-pattern-lifecycle
      element_type: boundary
      consumers: [dashboard]
      operation_kind: write
      state_effects: [database-write]
      probe_value: verify only eligible reconcile points become proposed probe points and the manual decision is auditable
    """
    from .project_intelligence import _probe_plan_out

    with get_conn() as conn:
        pattern_row = _get_pattern_row(conn, pattern_id, system_id)
        rec_row = conn.execute(
            """SELECT * FROM probe_pattern_reconciliations
               WHERE id = ? AND pattern_id = ? AND system_id = ?""",
            (reconciliation_id, pattern_id, system_id),
        ).fetchone()
        if rec_row is None:
            raise HTTPException(status_code=404, detail="Reconciliation not found")
        if rec_row["status"] != "completed":
            raise HTTPException(
                status_code=409,
                detail="Only completed reconciliations can produce a plan",
            )
        latest = conn.execute(
            """SELECT id FROM probe_pattern_reconciliations
               WHERE pattern_id = ? ORDER BY id DESC LIMIT 1""",
            (pattern_id,),
        ).fetchone()
        if latest and latest["id"] != reconciliation_id:
            raise HTTPException(
                status_code=409,
                detail="A newer reconciliation exists; use the latest one",
            )
        point_rows = conn.execute(
            """SELECT rp.*, ppp.component_id AS pp_component_id,
                      ppp.reason AS pp_reason,
                      ppp.recommended_mode AS pp_mode,
                      ppp.side_effect_risk AS pp_risk,
                      ppp.replayability AS pp_replayability,
                      ppp.path AS pp_path, ppp.symbol AS pp_symbol
               FROM probe_pattern_reconcile_points rp
               JOIN probe_pattern_points ppp ON rp.pattern_point_id = ppp.id
               WHERE rp.reconciliation_id = ? ORDER BY rp.id""",
            (reconciliation_id,),
        ).fetchall()
        symbol_docs = {
            (r["path"], r["qualified_name"]): r["docstring"]
            for r in conn.execute(
                "SELECT path, qualified_name, docstring FROM code_symbols WHERE snapshot_id = ?",
                (rec_row["snapshot_id"],),
            ).fetchall()
        }

    eligible = []
    for r in point_rows:
        if r["user_decision"] == "rejected":
            continue
        if r["classification"] in ("missing", "unsafe") or r["denylist_hit"]:
            continue
        if not r["target_path"] or not r["target_symbol"]:
            continue
        if r["classification"] == "exact_match":
            eligible.append(r)
        elif r["user_decision"] == "accepted":
            eligible.append(r)
    if not eligible:
        raise HTTPException(
            status_code=400,
            detail=(
                "No eligible reconcile points. Accept moved/changed points "
                "or reconcile again."
            ),
        )

    now = time.time()
    feature_id = pattern_row["feature_id"] or f"pattern-{pattern_id}"
    objective = (payload.objective or "").strip() or pattern_row["objective"]

    with get_conn() as conn:
        conn.execute("BEGIN")
        try:
            cur = conn.execute(
                """INSERT INTO intelligence_runs
                       (system_id, snapshot_id, run_type, provider, model,
                        prompt_version, schema_version, decision_method,
                        status, is_mock, started_at, completed_at)
                   VALUES (?, ?, 'probe_plan_from_pattern', 'manual', 'n/a',
                           'pattern-v1', 'pattern-v1', 'manual',
                           'completed', 0, ?, ?)""",
                (system_id, rec_row["snapshot_id"], now, now),
            )
            run_id = cur.lastrowid

            cur = conn.execute(
                """INSERT INTO probe_plans
                       (system_id, snapshot_id, intelligence_run_id,
                        feature_id, objective, status, origin,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'proposed', 'probe_pattern', ?, ?)""",
                (
                    system_id, rec_row["snapshot_id"], run_id,
                    feature_id, objective, now, now,
                ),
            )
            plan_id = cur.lastrowid

            for r in eligible:
                target_key = (r["target_path"], r["target_symbol"])
                denylist_hit = check_denylist(
                    r["target_symbol"], symbol_docs.get(target_key),
                )
                reason = r["pp_reason"] or "Saved probe pattern point"
                if r["classification"] != "exact_match":
                    reason += (
                        f" (re-attached via pattern reconcile: {r['classification']}"
                        f" from {r['pp_path']}:{r['pp_symbol']})"
                    )
                conn.execute(
                    """INSERT INTO probe_points
                           (plan_id, system_id, component_id, feature_id,
                            path, symbol, line_start, line_end, reason,
                            recommended_mode, side_effect_risk, replayability,
                            denylist_hit, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'proposed', ?, ?)""",
                    (
                        plan_id, system_id,
                        r["pp_component_id"] or _component_id_for(r["target_symbol"]),
                        feature_id, r["target_path"], r["target_symbol"],
                        r["target_line_start"] or 0, r["target_line_end"] or 0,
                        reason, r["pp_mode"], r["pp_risk"],
                        r["pp_replayability"], denylist_hit, now, now,
                    ),
                )

            conn.execute(
                "UPDATE probe_patterns SET last_used_at = ?, updated_at = ? WHERE id = ?",
                (now, now, pattern_id),
            )
            _record_event(
                conn, pattern_id, system_id, "plan_created",
                {
                    "plan_id": plan_id,
                    "reconciliation_id": reconciliation_id,
                    "point_count": len(eligible),
                },
                principal.user_id,
            )
            plan_row = conn.execute(
                "SELECT * FROM probe_plans WHERE id = ?", (plan_id,),
            ).fetchone()
            result = _probe_plan_out(conn, plan_row)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return result
