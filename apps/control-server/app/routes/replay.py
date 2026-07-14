"""Replay engine API (Issue #242 Phase B / #244).

Replay Sets (captured-input selections), the human replay-approval gate
(`decision_method: manual`, Principle 7), and synchronous replay runs that
re-execute captured inputs against the pinned snapshot's real implementation
in an isolated sandboxed worktree (Principle 8). All decisions here are
deterministic finite-set classifications (Principle 6) — Phase B involves no
reasoning model anywhere.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..models import (
    ReplayApprovalCreate,
    ReplayApprovalOut,
    ReplayApprovalStateOut,
    ReplayCaseResultOut,
    ReplayRiskContextOut,
    ReplayRiskPointOut,
    ReplayRunCreate,
    ReplayRunOut,
    ReplaySetCreate,
    ReplaySetOut,
    ReplaySetTraceOut,
)
from ..replay_runner import (
    ReplayExecution,
    build_case_plans,
    build_replay_env,
    build_sandbox_config,
    classify_case,
    execute_harness,
    replay_timeout_seconds,
    replay_workspace_base,
    restoration_for_trace,
    summarize_cases,
)

router = APIRouter()

# Cap on traces per Replay Set (enforced at the API; Phase B runs replays
# synchronously, so sets stay small and bounded).
MAX_REPLAY_SET_SIZE = 50

# Fixed Principle-4 guidance shown with every approval state and stored with
# every approval decision. Display text, never an inference.
PRINCIPLE4_WARNING = (
    "Replay re-executes recorded inputs against the component's real "
    "implementation inside an isolated, network-off sandbox. Approve replay "
    "only for pure-ish components (summarize / classify / normalize / "
    "extract / retrieve). Components that perform payments, send email, "
    "write to databases, or handle authentication are strongly discouraged "
    "as replay targets even with approval (Principle 4)."
)


# --- approval gate -------------------------------------------------------------


def _risk_context(conn, system_id: int, component_id: str) -> ReplayRiskContextOut:
    """Deterministic risk context assembled from existing persisted data only.

    Reuses the latest probe plan point labels for this component verbatim
    (display-only; no new reasoning run, no heuristic inference). Absent
    labels are returned as absent.
    """
    rows = conn.execute(
        """
        SELECT id, plan_id, side_effect_risk, replayability
        FROM probe_points
        WHERE system_id = ? AND component_id = ?
        ORDER BY id DESC
        LIMIT 5
        """,
        (system_id, component_id),
    ).fetchall()
    points = [
        ReplayRiskPointOut(
            point_id=row["id"],
            plan_id=row["plan_id"],
            side_effect_risk=row["side_effect_risk"] or None,
            replayability=row["replayability"] or None,
        )
        for row in rows
    ]
    return ReplayRiskContextOut(
        probe_plan_points=points, warning=PRINCIPLE4_WARNING
    )


def _approval_out(row) -> ReplayApprovalOut:
    risk_context = None
    if row["risk_context_json"]:
        try:
            risk_context = json.loads(row["risk_context_json"])
        except json.JSONDecodeError:
            risk_context = None
    return ReplayApprovalOut(
        id=row["id"],
        system_id=row["system_id"],
        component_id=row["component_id"],
        status=row["status"],
        reason=row["reason"] or "",
        approved_by_user_id=row["approved_by_user_id"],
        decision_method=row["decision_method"],
        risk_context=risk_context,
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        revoked_by_user_id=row["revoked_by_user_id"],
    )


def _active_approval(conn, system_id: int, component_id: str):
    return conn.execute(
        """
        SELECT * FROM replay_approvals
        WHERE system_id = ? AND component_id = ? AND status = 'approved'
        ORDER BY id DESC LIMIT 1
        """,
        (system_id, component_id),
    ).fetchone()


@router.get(
    "/components/{component_id}/replay-approval",
    response_model=ReplayApprovalStateOut,
)
def get_replay_approval(
    component_id: str,
    system_id: int = Depends(get_system_id),
) -> ReplayApprovalStateOut:
    with get_conn() as conn:
        latest = conn.execute(
            """
            SELECT * FROM replay_approvals
            WHERE system_id = ? AND component_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (system_id, component_id),
        ).fetchone()
        risk = _risk_context(conn, system_id, component_id)
    return ReplayApprovalStateOut(
        component_id=component_id,
        active=bool(latest is not None and latest["status"] == "approved"),
        approval=_approval_out(latest) if latest is not None else None,
        risk_context=risk,
    )


@router.post(
    "/components/{component_id}/replay-approval",
    response_model=ReplayApprovalOut,
    status_code=201,
)
def approve_replay(
    component_id: str,
    payload: ReplayApprovalCreate,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ReplayApprovalOut:
    now = time.time()
    with get_conn() as conn:
        if _active_approval(conn, system_id, component_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="Replay is already approved for this component",
            )
        # Store the risk context snapshot shown at approval time (Principle 7).
        risk = _risk_context(conn, system_id, component_id)
        cur = conn.execute(
            """
            INSERT INTO replay_approvals
                (system_id, component_id, status, reason, approved_by_user_id,
                 decision_method, risk_context_json, created_at)
            VALUES (?, ?, 'approved', ?, ?, 'manual', ?, ?)
            """,
            (
                system_id,
                component_id,
                payload.reason,
                principal.user_id,
                json.dumps(risk.model_dump(), ensure_ascii=False),
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM replay_approvals WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _approval_out(row)


@router.post(
    "/components/{component_id}/replay-approval/revoke",
    response_model=ReplayApprovalOut,
)
def revoke_replay_approval(
    component_id: str,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(require_user),
) -> ReplayApprovalOut:
    now = time.time()
    with get_conn() as conn:
        active = _active_approval(conn, system_id, component_id)
        if active is None:
            raise HTTPException(
                status_code=409,
                detail="No active replay approval exists for this component",
            )
        conn.execute(
            """
            UPDATE replay_approvals
            SET status = 'revoked', revoked_at = ?, revoked_by_user_id = ?
            WHERE id = ?
            """,
            (now, principal.user_id, active["id"]),
        )
        row = conn.execute(
            "SELECT * FROM replay_approvals WHERE id = ?", (active["id"],)
        ).fetchone()
    return _approval_out(row)


# --- replay sets ----------------------------------------------------------------


def _extract_analyzer_trace_ids(result: Any) -> List[str]:
    """Deterministically extract example trace ids from a persisted analyzer
    run result (Issue #148/#150 storage shape). Only stored trace ids are
    used — nothing is recomputed. Order is deterministic: compare examples
    in sorted key order first, then result rows, then grouped rows."""
    ids: List[str] = []
    seen = set()

    def _add(value: Any) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            ids.append(value)

    if not isinstance(result, dict):
        return ids
    compare = result.get("compare")
    if isinstance(compare, dict):
        examples = compare.get("examples")
        if isinstance(examples, dict):
            for key in sorted(examples):
                bucket = examples[key]
                if isinstance(bucket, list):
                    for trace_id in bucket:
                        _add(trace_id)
    rows = result.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                _add(row.get("trace_id"))
    groups = result.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict):
                group_rows = group.get("rows")
                if isinstance(group_rows, list):
                    for row in group_rows:
                        if isinstance(row, dict):
                            _add(row.get("trace_id"))
    return ids


def _existing_component_traces(
    conn, system_id: int, component_id: str, trace_ids: List[str]
) -> Dict[str, Any]:
    """Trace rows for the given ids that belong to this system+component."""
    found: Dict[str, Any] = {}
    chunk_size = 400
    for start in range(0, len(trace_ids), chunk_size):
        chunk = trace_ids[start : start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT trace_id, component_id, input_json, input_capture_json,
                   replayability, replay_reasons_json
            FROM traces
            WHERE system_id = ? AND component_id = ? AND trace_id IN ({placeholders})
            """,
            [system_id, component_id, *chunk],
        ).fetchall()
        for row in rows:
            found[row["trace_id"]] = row
    return found


def _set_trace_preview(row, trace_id: str) -> ReplaySetTraceOut:
    input_source, skip_reason = restoration_for_trace(row)
    replay_reasons: List[str] = []
    replayability = None
    if row is not None:
        replayability = row["replayability"]
        if row["replay_reasons_json"]:
            try:
                parsed = json.loads(row["replay_reasons_json"])
                if isinstance(parsed, list):
                    replay_reasons = [str(item) for item in parsed]
            except json.JSONDecodeError:
                replay_reasons = []
    return ReplaySetTraceOut(
        trace_id=trace_id,
        exists=row is not None,
        replayability=replayability,
        replay_reasons=replay_reasons,
        input_source=input_source,
        skip_reason=skip_reason,
    )


def _replay_set_out(conn, row) -> ReplaySetOut:
    trace_ids = json.loads(row["trace_ids_json"] or "[]")
    found = _existing_component_traces(
        conn, row["system_id"], row["component_id"], trace_ids
    )
    return ReplaySetOut(
        id=row["id"],
        system_id=row["system_id"],
        component_id=row["component_id"],
        name=row["name"] or "",
        source=row["source"],
        source_analyzer_run_id=row["source_analyzer_run_id"],
        trace_ids=trace_ids,
        traces=[
            _set_trace_preview(found.get(trace_id), trace_id)
            for trace_id in trace_ids
        ],
        created_at=row["created_at"],
    )


def _get_set_or_404(conn, replay_set_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM replay_sets WHERE id = ? AND system_id = ?",
        (replay_set_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Replay set not found")
    return row


@router.post("/replay-sets", response_model=ReplaySetOut, status_code=201)
def create_replay_set(
    payload: ReplaySetCreate,
    system_id: int = Depends(get_system_id),
) -> ReplaySetOut:
    manual = payload.trace_ids is not None
    from_analyzer = payload.analyzer_run_id is not None
    if manual == from_analyzer:
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one source: trace_ids or analyzer_run_id",
        )
    now = time.time()
    with get_conn() as conn:
        if manual:
            trace_ids = [str(trace_id) for trace_id in (payload.trace_ids or [])]
            if not trace_ids:
                raise HTTPException(
                    status_code=422, detail="trace_ids must not be empty"
                )
            if len(trace_ids) > MAX_REPLAY_SET_SIZE:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"A replay set may contain at most {MAX_REPLAY_SET_SIZE} "
                        f"traces (got {len(trace_ids)})"
                    ),
                )
            if len(set(trace_ids)) != len(trace_ids):
                raise HTTPException(
                    status_code=422, detail="trace_ids contains duplicates"
                )
            found = _existing_component_traces(
                conn, system_id, payload.component_id, trace_ids
            )
            unknown = [t for t in trace_ids if t not in found]
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Some trace ids do not exist for component "
                        f"'{payload.component_id}': {unknown[:10]}"
                    ),
                )
            source = "manual"
            source_analyzer_run_id = None
        else:
            run_row = conn.execute(
                "SELECT * FROM trace_analysis_runs WHERE id = ? AND system_id = ?",
                (payload.analyzer_run_id, system_id),
            ).fetchone()
            if run_row is None:
                raise HTTPException(
                    status_code=404, detail="Analyzer run not found"
                )
            if run_row["status"] != "completed" or not run_row["result_json"]:
                raise HTTPException(
                    status_code=422,
                    detail="Analyzer run has no stored result to draw traces from",
                )
            try:
                result = json.loads(run_row["result_json"])
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=422, detail="Analyzer run result is not valid JSON"
                )
            extracted = _extract_analyzer_trace_ids(result)
            found = _existing_component_traces(
                conn, system_id, payload.component_id, extracted
            )
            trace_ids = [t for t in extracted if t in found][:MAX_REPLAY_SET_SIZE]
            if not trace_ids:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Analyzer run result contains no stored trace ids for "
                        f"component '{payload.component_id}'"
                    ),
                )
            source = "analyzer_run"
            source_analyzer_run_id = run_row["id"]

        cur = conn.execute(
            """
            INSERT INTO replay_sets
                (system_id, component_id, name, trace_ids_json, source,
                 source_analyzer_run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                payload.component_id,
                payload.name,
                json.dumps(trace_ids, ensure_ascii=False),
                source,
                source_analyzer_run_id,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM replay_sets WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _replay_set_out(conn, row)


@router.get("/replay-sets", response_model=List[ReplaySetOut])
def list_replay_sets(
    component_id: Optional[str] = None,
    limit: int = 50,
    system_id: int = Depends(get_system_id),
) -> List[ReplaySetOut]:
    limit = max(1, min(limit, 200))
    where = ["system_id = ?"]
    params: List[Any] = [system_id]
    if component_id:
        where.append("component_id = ?")
        params.append(component_id)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM replay_sets
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_replay_set_out(conn, row) for row in rows]


@router.get("/replay-sets/{replay_set_id}", response_model=ReplaySetOut)
def get_replay_set(
    replay_set_id: int,
    system_id: int = Depends(get_system_id),
) -> ReplaySetOut:
    with get_conn() as conn:
        row = _get_set_or_404(conn, replay_set_id, system_id)
        return _replay_set_out(conn, row)


# --- replay runs ----------------------------------------------------------------


def _resolve_snapshot(conn, system_id: int, snapshot_id: Optional[int]):
    """Resolve the snapshot to replay against (mirrors routes/experiments.py)."""
    if snapshot_id is not None:
        snapshot = conn.execute(
            "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
        if snapshot is None:
            raise HTTPException(
                status_code=404, detail="Repository snapshot not found"
            )
        if snapshot["status"] != "ready":
            raise HTTPException(
                status_code=400, detail="Repository snapshot is not ready"
            )
        if not snapshot["repo_path"]:
            raise HTTPException(
                status_code=409, detail="Snapshot repository path is unavailable"
            )
        return snapshot
    snapshot = conn.execute(
        """
        SELECT * FROM repository_snapshots
        WHERE system_id = ? AND status = 'ready' AND repo_path != ''
        ORDER BY id DESC LIMIT 1
        """,
        (system_id,),
    ).fetchone()
    if snapshot is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No ready repository snapshot with a repository path is "
                "available; create a snapshot first"
            ),
        )
    return snapshot


def _resolve_component_symbol(conn, system_id: int, snapshot_id: int, component_id: str):
    """Deterministic component→symbol resolution via code_symbols."""
    rows = conn.execute(
        """
        SELECT * FROM code_symbols
        WHERE snapshot_id = ? AND system_id = ? AND component_id = ?
              AND kind IN ('function', 'async_function')
        ORDER BY path, qualified_name
        """,
        (snapshot_id, system_id, component_id),
    ).fetchall()
    if not rows:
        raise HTTPException(
            status_code=409,
            detail=(
                f"component symbol not resolved in snapshot {snapshot_id}: no "
                f"indexed function carries component_id '{component_id}'. Run "
                "POST /repository/symbols/index for the snapshot first."
            ),
        )
    if len(rows) > 1:
        candidates = ", ".join(
            f"{row['path']}:{row['qualified_name']}" for row in rows
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"component_id '{component_id}' resolves to multiple symbols "
                f"in snapshot {snapshot_id}: {candidates}"
            ),
        )
    symbol = rows[0]
    if symbol["kind"] == "async_function":
        raise HTTPException(
            status_code=409,
            detail=(
                f"component_id '{component_id}' resolves to an async function "
                f"({symbol['path']}:{symbol['qualified_name']}); async replay "
                "is not supported in Phase B"
            ),
        )
    return symbol


def _case_row_out(row) -> ReplayCaseResultOut:
    return ReplayCaseResultOut(
        id=row["id"],
        trace_id=row["trace_id"],
        position=row["position"],
        case_status=row["case_status"],
        input_source=row["input_source"],
        skip_reason=row["skip_reason"],
        replay_output=row["replay_output"],
        replay_error=row["replay_error"],
        recorded_output=row["recorded_output"],
        recorded_error=row["recorded_error"],
        duration_ms=row["duration_ms"],
        output_truncated=bool(row["output_truncated"]),
        comparison_mode=row["comparison_mode"],
        created_at=row["created_at"],
    )


def _replay_run_out(conn, row, include_cases: bool = True) -> ReplayRunOut:
    case_rows = conn.execute(
        """
        SELECT * FROM replay_case_results
        WHERE replay_run_id = ? AND system_id = ?
        ORDER BY position
        """,
        (row["id"], row["system_id"]),
    ).fetchall()
    cases = [_case_row_out(case_row) for case_row in case_rows]
    summary = summarize_cases([case.model_dump() for case in cases])
    try:
        sandbox_config = json.loads(row["sandbox_config_json"] or "{}")
    except json.JSONDecodeError:
        sandbox_config = {}
    return ReplayRunOut(
        id=row["id"],
        system_id=row["system_id"],
        replay_set_id=row["replay_set_id"],
        component_id=row["component_id"],
        snapshot_id=row["snapshot_id"],
        commit_sha=row["commit_sha"],
        symbol_path=row["symbol_path"],
        symbol_qualified_name=row["symbol_qualified_name"],
        status=row["status"],
        error=row["error"],
        trace_set_hash=row["trace_set_hash"],
        sandbox_config=sandbox_config,
        approval_id=row["approval_id"],
        workspace_path=row["workspace_path"],
        cleanup_state=row["cleanup_state"],
        cleanup_error=row["cleanup_error"],
        summary=summary,
        cases=cases if include_cases else [],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _get_run_or_404(conn, run_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM replay_runs WHERE id = ? AND system_id = ?",
        (run_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Replay run not found")
    return row


@router.post("/replay-runs", response_model=ReplayRunOut, status_code=201)
def create_replay_run(
    payload: ReplayRunCreate,
    system_id: int = Depends(get_system_id),
) -> ReplayRunOut:
    now = time.time()
    timeout_seconds = replay_timeout_seconds()
    with get_conn() as conn:
        replay_set = _get_set_or_404(conn, payload.replay_set_id, system_id)
        component_id = replay_set["component_id"]

        # Human approval gate: a revoked approval refuses exactly like a
        # missing one (only status='approved' counts as active).
        approval = _active_approval(conn, system_id, component_id)
        if approval is None:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Replay is not approved for component '{component_id}'. "
                    "A human must approve it first via POST /components/"
                    f"{component_id}/replay-approval (a revoked approval also "
                    "refuses)."
                ),
            )

        snapshot = _resolve_snapshot(conn, system_id, payload.snapshot_id)
        symbol = _resolve_component_symbol(
            conn, system_id, snapshot["id"], component_id
        )

        trace_ids = json.loads(replay_set["trace_ids_json"] or "[]")
        plans, trace_set_hash = build_case_plans(
            conn, system_id, component_id, trace_ids
        )
        sandbox_config = build_sandbox_config(
            timeout_seconds, build_replay_env("")
        )
        cur = conn.execute(
            """
            INSERT INTO replay_runs
                (system_id, replay_set_id, component_id, snapshot_id,
                 commit_sha, symbol_path, symbol_qualified_name, status,
                 trace_set_hash, sandbox_config_json, approval_id,
                 created_at, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                replay_set["id"],
                component_id,
                snapshot["id"],
                snapshot["commit_sha"],
                symbol["path"],
                symbol["qualified_name"],
                trace_set_hash,
                json.dumps(sandbox_config, ensure_ascii=False),
                approval["id"],
                now,
                now,
            ),
        )
        run_id = cur.lastrowid
        repo_path = snapshot["repo_path"]
        commit_sha = snapshot["commit_sha"]
        target = {
            "kind": "symbol",
            "path": symbol["path"],
            "qualified_name": symbol["qualified_name"],
        }

    harness_cases = [
        plan.harness_case for plan in plans if plan.harness_case is not None
    ]
    if harness_cases:
        execution = execute_harness(
            repo_path=repo_path,
            commit_sha=commit_sha,
            run_workspace_base=os.path.join(replay_workspace_base(), str(run_id)),
            target=target,
            harness_cases=harness_cases,
            timeout_seconds=timeout_seconds,
        )
    else:
        # Every trace was skipped server-side; nothing to execute.
        execution = ReplayExecution(status="completed", case_results=[])

    completed_at = time.time()
    with get_conn() as conn:
        if execution.status != "completed":
            conn.execute(
                """
                UPDATE replay_runs
                SET status = 'failed', error = ?, workspace_path = ?,
                    cleanup_state = ?, cleanup_error = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    execution.error,
                    execution.workspace_path,
                    execution.cleanup_state,
                    execution.cleanup_error,
                    completed_at,
                    run_id,
                ),
            )
        else:
            executed = iter(execution.case_results)
            for plan in plans:
                harness_case = (
                    next(executed) if plan.harness_case is not None else None
                )
                classified = classify_case(plan, harness_case)
                conn.execute(
                    """
                    INSERT INTO replay_case_results
                        (system_id, replay_run_id, trace_id, position,
                         case_status, input_source, skip_reason, replay_output,
                         replay_error, recorded_output, recorded_error,
                         duration_ms, output_truncated, comparison_mode,
                         created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        system_id,
                        run_id,
                        classified["trace_id"],
                        classified["position"],
                        classified["case_status"],
                        classified["input_source"],
                        classified["skip_reason"],
                        classified["replay_output"],
                        classified["replay_error"],
                        classified["recorded_output"],
                        classified["recorded_error"],
                        classified["duration_ms"],
                        1 if classified["output_truncated"] else 0,
                        classified["comparison_mode"],
                        completed_at,
                    ),
                )
            conn.execute(
                """
                UPDATE replay_runs
                SET status = 'completed', workspace_path = ?,
                    cleanup_state = ?, cleanup_error = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    execution.workspace_path,
                    execution.cleanup_state,
                    execution.cleanup_error,
                    completed_at,
                    run_id,
                ),
            )
        row = _get_run_or_404(conn, run_id, system_id)
        return _replay_run_out(conn, row)


@router.get("/replay-runs", response_model=List[ReplayRunOut])
def list_replay_runs(
    replay_set_id: Optional[int] = None,
    component_id: Optional[str] = None,
    limit: int = 20,
    system_id: int = Depends(get_system_id),
) -> List[ReplayRunOut]:
    limit = max(1, min(limit, 100))
    where = ["system_id = ?"]
    params: List[Any] = [system_id]
    if replay_set_id is not None:
        where.append("replay_set_id = ?")
        params.append(replay_set_id)
    if component_id:
        where.append("component_id = ?")
        params.append(component_id)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM replay_runs
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_replay_run_out(conn, row, include_cases=False) for row in rows]


@router.get("/replay-runs/{run_id}", response_model=ReplayRunOut)
def get_replay_run(
    run_id: int,
    system_id: int = Depends(get_system_id),
) -> ReplayRunOut:
    with get_conn() as conn:
        row = _get_run_or_404(conn, run_id, system_id)
        return _replay_run_out(conn, row)
