"""Replay engine API (Issue #242 Phase B / #244).

Replay Sets (captured-input selections), the human replay-approval gate
(`decision_method: manual`, Principle 7), and synchronous replay runs that
re-execute captured inputs against the pinned snapshot's real implementation
in an isolated sandboxed worktree (Principle 8). All decisions here are
deterministic finite-set classifications (Principle 6) — Phase B involves no
reasoning model anywhere.
"""

from __future__ import annotations

import ast
import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from ..experiment_runner import patch_hash
from ..git_ops import GitError, read_file_at_commit
from ..llm import LLMConfig, LLMError, MockLLMClient, create_llm_client, get_llm_client
from ..models import (
    ReplayApprovalCreate,
    ReplayApprovalOut,
    ReplayApprovalStateOut,
    ReplayCaseResultOut,
    ReplayRiskContextOut,
    ReplayRiskPointOut,
    ReplayRunCreate,
    ReplayRunOut,
    ReplayRegressionScaffoldCreate,
    ReplayRegressionScaffoldOut,
    ReplaySetCreate,
    ReplaySetOut,
    ReplaySetTraceOut,
    ReplaySourceDiffCreate,
    ReplaySourceDiffOut,
    ReplaySourceOut,
    ReplayVariantAggregateOut,
    ReplayVariantCaseResultOut,
    ReplayVariantCreate,
    ReplayVariantDraftCreate,
    ReplayVariantDraftOut,
    ReplayVariantExperimentPayloadOut,
    ReplayVariantOut,
    ReplayVariantRunCreate,
    ReplayVariantRunOut,
)
from ..replay_draft import (
    DRAFT_FAILED,
    DRAFT_PROPOSED,
    PROMPT_VERSION as DRAFT_PROMPT_VERSION,
    SCHEMA_VERSION as DRAFT_SCHEMA_VERSION,
    DraftError,
    DraftResult,
    _diff_against_snapshot as diff_source_against_snapshot,
    generate_variant_draft,
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
from ..replay_variants import (
    APPLY_APPLIED,
    APPLY_INVALID_PATCH,
    APPLY_NOT_APPLICABLE,
    classify_variant_case,
    summarize_variant_cases,
)
from ..trace_analyzer import max_examples
from .snapshot_preflight import require_snapshot_preflight

router = APIRouter(dependencies=[Depends(require_user)])

REGRESSION_SCAFFOLD_PROMPT_VERSION = "replay-regression-scaffold-v1"
REGRESSION_SCAFFOLD_SCHEMA_VERSION = "replay-regression-scaffold-v1"

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


def _persist_replay_case_results(
    conn, system_id: int, run_id: int, plans, execution: ReplayExecution, created_at: float
) -> None:
    """Baseline-vs-recorded classification + persistence (Phase B). Shared by
    ``POST /replay-runs`` and the baseline half of ``POST
    /replay-variant-runs`` so both endpoints classify identically."""
    executed = iter(execution.case_results)
    for plan in plans:
        harness_case = next(executed) if plan.harness_case is not None else None
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
                created_at,
            ),
        )


@router.post("/replay-runs", response_model=ReplayRunOut, status_code=201)
def create_replay_run(
    payload: ReplayRunCreate,
    system_id: int = Depends(get_system_id),
) -> ReplayRunOut:
    now = time.time()
    timeout_seconds = replay_timeout_seconds()
    snapshot_freshness, head_sha_at_creation, stale_ack_reason = (
        require_snapshot_preflight(
            system_id, payload.snapshot_id, payload.stale_snapshot_reason
        )
    )
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
                 created_at, started_at, snapshot_freshness,
                 head_sha_at_creation, stale_ack_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
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
                snapshot_freshness,
                head_sha_at_creation,
                stale_ack_reason,
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
            _persist_replay_case_results(
                conn, system_id, run_id, plans, execution, completed_at
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


# =============================================================================
# Replay variants (Issue #242 Phase C / #245)
#
# A "variant replay run" IS a replay_runs row (same snapshot/symbol/approval
# resolution, same trace_set_hash, same replay_case_results baseline-vs-
# recorded classification as Phase B) that additionally has one or more
# replay_variants rows hanging off it via replay_run_id. POST /replay-runs
# (Phase B, above) never creates replay_variants rows, so a plain baseline-
# only run 404s from the /replay-variant-runs/{id} endpoints below --
# EXISTS(SELECT 1 FROM replay_variants WHERE replay_run_id = ...) is the
# marker, not a new column on replay_runs.
# =============================================================================


def _variant_case_row_out(row) -> ReplayVariantCaseResultOut:
    try:
        field_diffs = json.loads(row["field_diffs_json"] or "[]")
    except json.JSONDecodeError:
        field_diffs = []
    if not isinstance(field_diffs, list):
        field_diffs = []
    return ReplayVariantCaseResultOut(
        id=row["id"],
        trace_id=row["trace_id"],
        position=row["position"],
        case_status=row["case_status"],
        comparison_mode=row["comparison_mode"],
        baseline_output=row["baseline_output"],
        candidate_output=row["candidate_output"],
        candidate_error=row["candidate_error"],
        recorded_error=row["recorded_error"],
        duration_ms=row["duration_ms"],
        duration_delta_ms=row["duration_delta_ms"],
        field_diffs=field_diffs,
        output_truncated=bool(row["output_truncated"]),
        created_at=row["created_at"],
    )


def _variant_out(conn, row, include_cases: bool = True) -> ReplayVariantOut:
    case_rows = conn.execute(
        """
        SELECT * FROM replay_variant_case_results
        WHERE replay_variant_id = ? AND system_id = ?
        ORDER BY position
        """,
        (row["id"], row["system_id"]),
    ).fetchall()
    cases = [_variant_case_row_out(case_row) for case_row in case_rows]
    summary, examples, avg_delta = summarize_variant_cases(
        [case.model_dump() for case in cases], max_examples()
    )
    aggregate = ReplayVariantAggregateOut(
        match=summary.get("match", 0),
        diff=summary.get("diff", 0),
        candidate_error=summary.get("candidate_error", 0),
        error_to_success=summary.get("error_to_success", 0),
        error_to_same_error=summary.get("error_to_same_error", 0),
        error_to_different_error=summary.get("error_to_different_error", 0),
        skipped=summary.get("skipped", 0),
        total=summary.get("total", 0),
        avg_duration_delta_ms=avg_delta,
        examples=examples,
    )
    return ReplayVariantOut(
        id=row["id"],
        replay_run_id=row["replay_run_id"],
        variant_key=row["variant_key"],
        label=row["label"] or "",
        is_baseline=bool(row["is_baseline"]),
        patch_text=row["patch_text"] or "",
        patch_hash=row["patch_hash"],
        source=row["source"],
        apply_status=row["apply_status"],
        apply_error=row["apply_error"],
        status=row["status"],
        error=row["error"],
        workspace_path=row["workspace_path"],
        cleanup_state=row["cleanup_state"],
        cleanup_error=row["cleanup_error"],
        aggregate=aggregate,
        cases=cases if include_cases else [],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _variant_run_out(conn, row, include_cases: bool = True) -> ReplayVariantRunOut:
    try:
        sandbox_config = json.loads(row["sandbox_config_json"] or "{}")
    except json.JSONDecodeError:
        sandbox_config = {}
    variant_rows = conn.execute(
        """
        SELECT * FROM replay_variants
        WHERE replay_run_id = ? AND system_id = ?
        ORDER BY id
        """,
        (row["id"], row["system_id"]),
    ).fetchall()
    variants = [
        _variant_out(conn, variant_row, include_cases=include_cases)
        for variant_row in variant_rows
    ]
    return ReplayVariantRunOut(
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
        variants=variants,
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _get_variant_run_or_404(conn, run_id: int, system_id: int):
    row = _get_run_or_404(conn, run_id, system_id)
    has_variants = conn.execute(
        "SELECT 1 FROM replay_variants WHERE replay_run_id = ? AND system_id = ? LIMIT 1",
        (run_id, system_id),
    ).fetchone()
    if has_variants is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Replay run exists but was not created via "
                "POST /replay-variant-runs (no variants attached)"
            ),
        )
    return row


@router.post(
    "/replay-variant-runs", response_model=ReplayVariantRunOut, status_code=201
)
def create_replay_variant_run(
    payload: ReplayVariantRunCreate,
    system_id: int = Depends(get_system_id),
) -> ReplayVariantRunOut:
    """Run baseline + N patch variants together against the SAME Replay Set
    and SAME sandbox conditions, producing a per-trace + aggregate diff
    matrix (Issue #245). Reuses Phase B's approval gate, snapshot
    resolution, and symbol resolution verbatim; ``POST /replay-runs``
    (baseline-only) is unchanged."""
    now = time.time()
    timeout_seconds = replay_timeout_seconds()

    # Issue #369 (review finding 4): the SAME shared preflight Experiment and
    # Candidate Studio run. A Replay run pins a snapshot exactly as they do,
    # so continuing on one that is behind HEAD is the same manual decision and
    # is recorded on the run. Evaluated BEFORE the connection is opened --
    # `gather_preflight` runs `git` subprocesses and the lock is non-reentrant.
    snapshot_freshness, head_sha_at_creation, stale_ack_reason = (
        require_snapshot_preflight(
            system_id, payload.snapshot_id, payload.stale_snapshot_reason
        )
    )

    with get_conn() as conn:
        replay_set = _get_set_or_404(conn, payload.replay_set_id, system_id)
        component_id = replay_set["component_id"]

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
                 created_at, started_at,
                 snapshot_freshness, head_sha_at_creation, stale_ack_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
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
                snapshot_freshness,
                head_sha_at_creation,
                stale_ack_reason,
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

        # Insert baseline + variant rows up front (status='running') so a
        # crash mid-execution leaves an honest partial audit trail, mirroring
        # replay_runs' own insert-then-update pattern.
        cur = conn.execute(
            """
            INSERT INTO replay_variants
                (system_id, replay_run_id, variant_key, label, is_baseline,
                 patch_text, patch_hash, source, apply_status, status,
                 created_at, started_at)
            VALUES (?, ?, 'baseline', 'Baseline', 1, '', ?, 'manual', ?,
                    'running', ?, ?)
            """,
            (system_id, run_id, patch_hash(""), APPLY_NOT_APPLICABLE, now, now),
        )
        baseline_variant_id = cur.lastrowid

        variant_plan: List[Any] = []
        for index, variant in enumerate(payload.variants, start=1):
            cur = conn.execute(
                """
                INSERT INTO replay_variants
                    (system_id, replay_run_id, variant_key, label, is_baseline,
                     patch_text, patch_hash, source, apply_status, status,
                     created_at, started_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    system_id,
                    run_id,
                    f"variant-{index}",
                    variant.label,
                    variant.patch_text,
                    patch_hash(variant.patch_text),
                    variant.source,
                    APPLY_NOT_APPLICABLE,
                    now,
                    now,
                ),
            )
            variant_plan.append((cur.lastrowid, variant))

    harness_cases = [
        plan.harness_case for plan in plans if plan.harness_case is not None
    ]
    workspace_root = os.path.join(replay_workspace_base(), str(run_id))

    if harness_cases:
        baseline_execution = execute_harness(
            repo_path=repo_path,
            commit_sha=commit_sha,
            run_workspace_base=os.path.join(workspace_root, "baseline"),
            target=target,
            harness_cases=harness_cases,
            timeout_seconds=timeout_seconds,
        )
    else:
        baseline_execution = ReplayExecution(status="completed", case_results=[])

    completed_at = time.time()
    baseline_by_position: Dict[int, Dict[str, Any]] = {}
    if baseline_execution.status == "completed":
        executed = iter(baseline_execution.case_results)
        for plan in plans:
            if plan.harness_case is not None:
                baseline_by_position[plan.position] = next(executed)

    with get_conn() as conn:
        # A failed harness has no one-to-one case result stream. Persisting it
        # through _persist_replay_case_results would exhaust the iterator and
        # turn the intended audited failure response into an unhandled 500.
        if baseline_execution.status == "completed":
            _persist_replay_case_results(
                conn, system_id, run_id, plans, baseline_execution, completed_at
            )
        run_status = "completed" if baseline_execution.status == "completed" else "failed"
        conn.execute(
            """
            UPDATE replay_runs
            SET status = ?, error = ?, workspace_path = ?, cleanup_state = ?,
                cleanup_error = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                run_status,
                baseline_execution.error,
                baseline_execution.workspace_path,
                baseline_execution.cleanup_state,
                baseline_execution.cleanup_error,
                completed_at,
                run_id,
            ),
        )
        conn.execute(
            """
            UPDATE replay_variants
            SET status = ?, apply_status = ?, error = ?, workspace_path = ?,
                cleanup_state = ?, cleanup_error = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                run_status,
                APPLY_NOT_APPLICABLE,
                baseline_execution.error,
                baseline_execution.workspace_path,
                baseline_execution.cleanup_state,
                baseline_execution.cleanup_error,
                completed_at,
                baseline_variant_id,
            ),
        )
        if baseline_execution.status != "completed":
            # No baseline to compare against in this run -- every variant is
            # marked failed with a clear reason and NONE of them execute.
            # This never touches other runs; only this run's own variants.
            for variant_id, _variant in variant_plan:
                conn.execute(
                    """
                    UPDATE replay_variants
                    SET status = 'failed',
                        error = ?,
                        completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        "baseline replay failed in this run: "
                        + (baseline_execution.error or "unknown error"),
                        completed_at,
                        variant_id,
                    ),
                )
            row = _get_run_or_404(conn, run_id, system_id)
            return _variant_run_out(conn, row)

    # Execute each variant independently: its own worktree, its own patch
    # apply, its own harness run. Ordering never matters -- nothing is shared
    # between iterations besides the read-only baseline_by_position map.
    for variant_id, variant in variant_plan:
        if harness_cases:
            execution = execute_harness(
                repo_path=repo_path,
                commit_sha=commit_sha,
                run_workspace_base=os.path.join(workspace_root, f"variant-{variant_id}"),
                target=target,
                harness_cases=harness_cases,
                timeout_seconds=timeout_seconds,
                patch_text=variant.patch_text,
            )
        else:
            execution = ReplayExecution(status="completed", case_results=[])
        variant_completed_at = time.time()

        if execution.status == "invalid_patch":
            with get_conn() as conn:
                conn.execute(
                    """
                    UPDATE replay_variants
                    SET status = 'failed', apply_status = ?, apply_error = ?,
                        workspace_path = ?, cleanup_state = ?,
                        cleanup_error = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        APPLY_INVALID_PATCH,
                        execution.error,
                        execution.workspace_path,
                        execution.cleanup_state,
                        execution.cleanup_error,
                        variant_completed_at,
                        variant_id,
                    ),
                )
            continue

        if execution.status != "completed":
            with get_conn() as conn:
                conn.execute(
                    """
                    UPDATE replay_variants
                    SET status = 'failed', apply_status = ?, error = ?,
                        workspace_path = ?, cleanup_state = ?,
                        cleanup_error = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        APPLY_APPLIED,
                        execution.error,
                        execution.workspace_path,
                        execution.cleanup_state,
                        execution.cleanup_error,
                        variant_completed_at,
                        variant_id,
                    ),
                )
            continue

        candidate_by_position: Dict[int, Dict[str, Any]] = {}
        executed = iter(execution.case_results)
        for plan in plans:
            if plan.harness_case is not None:
                candidate_by_position[plan.position] = next(executed)

        with get_conn() as conn:
            for plan in plans:
                classified = classify_variant_case(
                    plan.trace_id,
                    plan.position,
                    plan.harness_case,
                    baseline_by_position.get(plan.position),
                    candidate_by_position.get(plan.position),
                )
                conn.execute(
                    """
                    INSERT INTO replay_variant_case_results
                        (system_id, replay_variant_id, replay_run_id, trace_id,
                         position, case_status, comparison_mode,
                         baseline_output, candidate_output, candidate_error,
                         recorded_error, duration_ms, duration_delta_ms,
                         field_diffs_json, output_truncated, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        system_id,
                        variant_id,
                        run_id,
                        classified["trace_id"],
                        classified["position"],
                        classified["case_status"],
                        classified["comparison_mode"],
                        classified["baseline_output"],
                        classified["candidate_output"],
                        classified["candidate_error"],
                        classified["recorded_error"],
                        classified["duration_ms"],
                        classified["duration_delta_ms"],
                        json.dumps(classified["field_diffs"], ensure_ascii=False),
                        1 if classified["output_truncated"] else 0,
                        variant_completed_at,
                    ),
                )
            conn.execute(
                """
                UPDATE replay_variants
                SET status = 'completed', apply_status = ?, workspace_path = ?,
                    cleanup_state = ?, cleanup_error = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    APPLY_APPLIED,
                    execution.workspace_path,
                    execution.cleanup_state,
                    execution.cleanup_error,
                    variant_completed_at,
                    variant_id,
                ),
            )

    with get_conn() as conn:
        row = _get_run_or_404(conn, run_id, system_id)
        return _variant_run_out(conn, row)


@router.get("/replay-variant-runs", response_model=List[ReplayVariantRunOut])
def list_replay_variant_runs(
    replay_set_id: Optional[int] = None,
    component_id: Optional[str] = None,
    limit: int = 20,
    system_id: int = Depends(get_system_id),
) -> List[ReplayVariantRunOut]:
    limit = max(1, min(limit, 100))
    where = [
        "r.system_id = ?",
        "EXISTS (SELECT 1 FROM replay_variants v WHERE v.replay_run_id = r.id)",
    ]
    params: List[Any] = [system_id]
    if replay_set_id is not None:
        where.append("r.replay_set_id = ?")
        params.append(replay_set_id)
    if component_id:
        where.append("r.component_id = ?")
        params.append(component_id)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT r.* FROM replay_runs r
            WHERE {' AND '.join(where)}
            ORDER BY r.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_variant_run_out(conn, row, include_cases=False) for row in rows]


@router.get("/replay-variant-runs/{run_id}", response_model=ReplayVariantRunOut)
def get_replay_variant_run(
    run_id: int,
    system_id: int = Depends(get_system_id),
) -> ReplayVariantRunOut:
    with get_conn() as conn:
        row = _get_variant_run_or_404(conn, run_id, system_id)
        return _variant_run_out(conn, row)


@router.get(
    "/replay-variant-runs/{run_id}/variants/{variant_id}/experiment-payload",
    response_model=ReplayVariantExperimentPayloadOut,
)
def get_replay_variant_experiment_payload(
    run_id: int,
    variant_id: int,
    system_id: int = Depends(get_system_id),
) -> ReplayVariantExperimentPayloadOut:
    """Shape a variant's patch for ``POST /experiments`` prefill (Issue #245).
    API shape only: this never creates an experiment or auto-adopts
    anything -- the Workbench UI wiring (drag this into the experiment
    creation form) is Phase D."""
    with get_conn() as conn:
        run_row = _get_variant_run_or_404(conn, run_id, system_id)
        variant_row = conn.execute(
            """
            SELECT * FROM replay_variants
            WHERE id = ? AND replay_run_id = ? AND system_id = ?
            """,
            (variant_id, run_id, system_id),
        ).fetchone()
        if variant_row is None:
            raise HTTPException(status_code=404, detail="Replay variant not found")
        if not variant_row["patch_text"]:
            raise HTTPException(
                status_code=422,
                detail="This variant has no patch to promote (baseline has none)",
            )
        if (
            variant_row["status"] != "completed"
            or variant_row["apply_status"] != APPLY_APPLIED
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Only a successfully applied and completed replay variant "
                    "can be promoted to an Experiment"
                ),
            )
        return ReplayVariantExperimentPayloadOut(
            label=variant_row["label"] or variant_row["variant_key"],
            patch_text=variant_row["patch_text"],
            patch_hash=variant_row["patch_hash"],
            source="replay_variant",
            risk_note=(
                f"Promoted from replay-variant-run {run_id}, variant "
                f"{variant_row['variant_key']} (component "
                f"'{run_row['component_id']}', snapshot {run_row['snapshot_id']})."
            ),
            origin={
                "replay_variant_run_id": run_id,
                "replay_variant_id": variant_id,
                "variant_key": variant_row["variant_key"],
                "component_id": run_row["component_id"],
                "snapshot_id": run_row["snapshot_id"],
                "commit_sha": run_row["commit_sha"],
                "apply_status": variant_row["apply_status"],
            },
        )


@router.post(
    "/replay-regression-scaffolds",
    response_model=ReplayRegressionScaffoldOut,
    status_code=201,
)
def create_replay_regression_scaffold(
    payload: ReplayRegressionScaffoldCreate,
    system_id: int = Depends(get_system_id),
) -> ReplayRegressionScaffoldOut:
    """Draft a review-only pytest regression scaffold through reasoning_llm.

    The prompt is grounded in one completed/applied candidate case, its
    recorded trace, resolved symbol, pinned snapshot, and candidate patch.
    Success and failure are both persisted; generated text is never written
    to the target repository.
    """
    started_at = time.time()
    with get_conn() as conn:
        run = _get_variant_run_or_404(conn, payload.replay_run_id, system_id)
        variant = conn.execute(
            """
            SELECT * FROM replay_variants
            WHERE id = ? AND replay_run_id = ? AND system_id = ?
            """,
            (payload.replay_variant_id, payload.replay_run_id, system_id),
        ).fetchone()
        if variant is None:
            raise HTTPException(status_code=404, detail="Replay variant not found")
        if (
            variant["is_baseline"]
            or variant["status"] != "completed"
            or variant["apply_status"] != APPLY_APPLIED
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Regression scaffolds require a successfully applied and "
                    "completed candidate variant"
                ),
            )
        case = conn.execute(
            """
            SELECT * FROM replay_variant_case_results
            WHERE replay_variant_id = ? AND replay_run_id = ?
              AND system_id = ? AND trace_id = ?
            """,
            (
                variant["id"],
                run["id"],
                system_id,
                payload.trace_id,
            ),
        ).fetchone()
        if case is None:
            raise HTTPException(
                status_code=422,
                detail="trace_id is not a completed case for this variant",
            )
        trace = conn.execute(
            """
            SELECT trace_id, input_json, input_capture_json, output_text, error
            FROM traces
            WHERE system_id = ? AND component_id = ? AND trace_id = ?
            """,
            (system_id, run["component_id"], payload.trace_id),
        ).fetchone()
        if trace is None:
            raise HTTPException(status_code=404, detail="trace not found")

        context = {
            "snapshot": {
                "id": run["snapshot_id"],
                "commit_sha": run["commit_sha"],
            },
            "symbol": {
                "path": run["symbol_path"],
                "qualified_name": run["symbol_qualified_name"],
            },
            "recorded_trace": {
                "trace_id": trace["trace_id"],
                "input_capture_json": trace["input_capture_json"],
                "legacy_input_json": trace["input_json"],
                "output": trace["output_text"],
                "error": trace["error"],
            },
            "candidate_case": {
                "case_status": case["case_status"],
                "candidate_output": case["candidate_output"],
                "candidate_error": case["candidate_error"],
                "field_diffs_json": case["field_diffs_json"],
            },
            "candidate_patch": variant["patch_text"],
        }

    config = LLMConfig.intelligence_from_env()
    provider = config.provider
    model = config.model
    is_mock = False
    scaffold_text = ""
    error: Optional[str] = None
    try:
        if config.provider not in {"mock", "openai", "anthropic", "gemini"}:
            raise LLMError(f"Unsupported LLM provider: {config.provider}")
        client = create_llm_client(config)
        is_mock = isinstance(client, MockLLMClient)
        if is_mock:
            provider, model = "mock", "mock"
        raw = client.generate_text(
            [
                {
                    "role": "system",
                    "content": (
                        "You draft review-only Python pytest regression tests. "
                        "Use only the supplied pinned replay context. Return "
                        "REGRESSION_SCAFFOLD_RESPONSE_JSON as strict JSON with "
                        "exactly one string field named scaffold. Do not claim "
                        "the test was executed and do not use network access."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False, sort_keys=True),
                },
            ],
            temperature=0.1,
            max_tokens=1800,
        )
        from .generation import _extract_json_object

        parsed = _extract_json_object(raw)
        if set(parsed) != {"scaffold"}:
            raise LLMError(
                "reasoning response must contain exactly the scaffold field"
            )
        scaffold = parsed.get("scaffold")
        if not isinstance(scaffold, str) or not scaffold.strip():
            raise LLMError("reasoning response must contain a non-empty scaffold string")
        if len(scaffold) > 100_000:
            raise LLMError("reasoning response scaffold exceeds 100000 characters")
        try:
            tree = ast.parse(scaffold)
        except SyntaxError as exc:
            raise LLMError(f"generated scaffold is not valid Python: {exc}") from exc
        if not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            for node in ast.walk(tree)
        ):
            raise LLMError("generated scaffold must define at least one test_ function")
        scaffold_text = scaffold
    except (LLMError, HTTPException, ValueError) as exc:
        error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)

    completed_at = time.time()
    status = "proposed" if error is None else "failed"
    run_status = "completed" if error is None else "failed"
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method, status,
                 error_details, is_mock, started_at, completed_at)
            VALUES (?, ?, 'replay_regression_scaffold', ?, ?, ?, ?,
                    'reasoning_llm', ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                run["snapshot_id"],
                provider,
                model,
                REGRESSION_SCAFFOLD_PROMPT_VERSION,
                REGRESSION_SCAFFOLD_SCHEMA_VERSION,
                run_status,
                error,
                1 if is_mock else 0,
                started_at,
                completed_at,
            ),
        )
        intelligence_run_id = cur.lastrowid
        cur = conn.execute(
            """
            INSERT INTO replay_regression_scaffolds
                (system_id, intelligence_run_id, replay_run_id,
                 replay_variant_id, replay_set_id, trace_id, snapshot_id,
                 scaffold_text, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                intelligence_run_id,
                run["id"],
                variant["id"],
                run["replay_set_id"],
                payload.trace_id,
                run["snapshot_id"],
                scaffold_text,
                status,
                error,
                completed_at,
            ),
        )
        scaffold_id = cur.lastrowid

    return ReplayRegressionScaffoldOut(
        id=scaffold_id,
        intelligence_run_id=intelligence_run_id,
        replay_run_id=run["id"],
        replay_variant_id=variant["id"],
        replay_set_id=run["replay_set_id"],
        trace_id=payload.trace_id,
        snapshot_id=run["snapshot_id"],
        scaffold_text=scaffold_text,
        status=status,
        error=error,
        provider=provider,
        model=model,
        prompt_version=REGRESSION_SCAFFOLD_PROMPT_VERSION,
        schema_version=REGRESSION_SCAFFOLD_SCHEMA_VERSION,
        is_mock=is_mock,
        created_at=completed_at,
    )


# --- LLM candidate drafts (Issue #245) ---------------------------------------


def _draft_out(draft_row, run_row) -> ReplayVariantDraftOut:
    return ReplayVariantDraftOut(
        id=draft_row["id"],
        system_id=draft_row["system_id"],
        replay_set_id=draft_row["replay_set_id"],
        component_id=draft_row["component_id"],
        trace_id=draft_row["trace_id"],
        objective=draft_row["objective"],
        snapshot_id=draft_row["snapshot_id"],
        symbol_path=draft_row["symbol_path"],
        symbol_qualified_name=draft_row["symbol_qualified_name"],
        generated_code=draft_row["generated_code"] or "",
        patch_text=draft_row["patch_text"] or "",
        patch_hash=draft_row["patch_hash"] or "",
        notes=draft_row["notes"] or "",
        status=draft_row["status"],
        error=draft_row["error"],
        provider=run_row["provider"] if run_row is not None else None,
        model=run_row["model"] if run_row is not None else None,
        prompt_version=run_row["prompt_version"] if run_row is not None else None,
        schema_version=run_row["schema_version"] if run_row is not None else None,
        decision_method=run_row["decision_method"] if run_row is not None else None,
        is_mock=bool(run_row["is_mock"]) if run_row is not None else False,
        created_at=draft_row["created_at"],
    )


@router.post(
    "/replay-variant-drafts", response_model=ReplayVariantDraftOut, status_code=201
)
def create_replay_variant_draft(
    payload: ReplayVariantDraftCreate,
    system_id: int = Depends(get_system_id),
) -> ReplayVariantDraftOut:
    """Connects the existing Generate & Evaluate flow as a "candidate draft"
    source (Issue #245): runs the SAME candidate-generation reasoning-model
    prompt ``POST /generation-runs`` uses, then deterministically turns the
    result into a unified diff against the resolved symbol's pinned-snapshot
    source. Does NOT execute anything -- the caller runs the returned
    ``patch_text`` as a variant via ``POST /replay-variant-runs``. Fails
    closed on any LLM configuration/call/parse failure (Principle 6): no
    heuristic fallback, and the failed attempt is still persisted (Principle
    7) so it is auditable."""
    now = time.time()
    with get_conn() as conn:
        replay_set = _get_set_or_404(conn, payload.replay_set_id, system_id)
        component_id = replay_set["component_id"]
        trace_ids = json.loads(replay_set["trace_ids_json"] or "[]")
        if payload.trace_id not in trace_ids:
            raise HTTPException(
                status_code=422,
                detail="trace_id must belong to the referenced Replay Set",
            )
        snapshot = _resolve_snapshot(conn, system_id, payload.snapshot_id)
        symbol = _resolve_component_symbol(
            conn, system_id, snapshot["id"], component_id
        )
        trace = conn.execute(
            """
            SELECT trace_id, component_id, input_json, output_text, error
            FROM traces
            WHERE system_id = ? AND component_id = ? AND trace_id = ?
            """,
            (system_id, component_id, payload.trace_id),
        ).fetchone()
        if trace is None:
            raise HTTPException(status_code=404, detail="trace not found")
        system_profile = conn.execute(
            "SELECT * FROM system_profile WHERE system_id = ?", (system_id,)
        ).fetchone()
        component_profile = conn.execute(
            """
            SELECT * FROM component_profiles
            WHERE system_id = ? AND component_id = ?
            """,
            (system_id, component_id),
        ).fetchone()
        criteria = conn.execute(
            """
            SELECT name, description, criterion_type, expected_value, weight
            FROM evaluation_criteria
            WHERE system_id = ? AND component_id = ? AND enabled = 1
            ORDER BY id
            """,
            (system_id, component_id),
        ).fetchall()
        trace_context = {
            "trace": dict(trace),
            "system_profile": dict(system_profile) if system_profile else {},
            "component_profile": dict(component_profile) if component_profile else {},
            "criteria": [dict(row) for row in criteria],
        }
        repo_path = snapshot["repo_path"]
        commit_sha = snapshot["commit_sha"]
        snapshot_id = snapshot["id"]

    config = LLMConfig.from_env()
    try:
        if config.provider not in {"mock", "openai", "anthropic", "gemini"}:
            raise LLMError(f"Unsupported LLM provider: {config.provider}")
        client = get_llm_client()
    except LLMError as exc:
        # Client construction (for example, a missing API key) is part of the
        # reasoning attempt and must leave the same failed audit trail as a
        # provider-call or response-validation failure.
        draft = DraftResult(
            provider=config.provider,
            model=config.model,
            is_mock=config.provider == "mock",
            status=DRAFT_FAILED,
            error=str(exc),
        )
    else:
        draft = generate_variant_draft(
            client,
            config,
            repo_path=repo_path,
            commit_sha=commit_sha,
            symbol_path=symbol["path"],
            symbol_qualified_name=symbol["qualified_name"],
            start_line=symbol["start_line"],
            end_line=symbol["end_line"],
            trace_context=trace_context,
            objective=payload.objective,
            worktree_base=os.path.join(replay_workspace_base(), "drafts"),
        )
    completed_at = time.time()
    run_status = "completed" if draft.status == DRAFT_PROPOSED else "failed"

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO intelligence_runs
                (system_id, snapshot_id, run_type, provider, model,
                 prompt_version, schema_version, decision_method,
                 status, error_details, is_mock, started_at, completed_at)
            VALUES (?, ?, 'replay_variant_draft', ?, ?, ?, ?, 'reasoning_llm',
                    ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                snapshot_id,
                draft.provider,
                draft.model,
                DRAFT_PROMPT_VERSION,
                DRAFT_SCHEMA_VERSION,
                run_status,
                draft.error,
                1 if draft.is_mock else 0,
                now,
                completed_at,
            ),
        )
        intelligence_run_id = cur.lastrowid
        cur = conn.execute(
            """
            INSERT INTO replay_variant_drafts
                (system_id, intelligence_run_id, replay_set_id, component_id,
                 trace_id, objective, snapshot_id, symbol_path,
                 symbol_qualified_name, generated_code, patch_text,
                 patch_hash, notes, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                intelligence_run_id,
                replay_set["id"],
                component_id,
                payload.trace_id,
                payload.objective,
                snapshot_id,
                symbol["path"],
                symbol["qualified_name"],
                draft.generated_code,
                draft.patch_text,
                draft.patch_hash,
                draft.notes,
                draft.status,
                draft.error,
                completed_at,
            ),
        )
        draft_id = cur.lastrowid
        draft_row = conn.execute(
            "SELECT * FROM replay_variant_drafts WHERE id = ? AND system_id = ?",
            (draft_id, system_id),
        ).fetchone()
        run_row = conn.execute(
            "SELECT * FROM intelligence_runs WHERE id = ? AND system_id = ?",
            (intelligence_run_id, system_id),
        ).fetchone()
        return _draft_out(draft_row, run_row)


def _get_draft_with_run(conn, draft_id: int, system_id: int):
    draft_row = conn.execute(
        "SELECT * FROM replay_variant_drafts WHERE id = ? AND system_id = ?",
        (draft_id, system_id),
    ).fetchone()
    if draft_row is None:
        raise HTTPException(status_code=404, detail="Replay variant draft not found")
    run_row = conn.execute(
        "SELECT * FROM intelligence_runs WHERE id = ? AND system_id = ?",
        (draft_row["intelligence_run_id"], system_id),
    ).fetchone()
    return draft_row, run_row


@router.get("/replay-variant-drafts", response_model=List[ReplayVariantDraftOut])
def list_replay_variant_drafts(
    replay_set_id: Optional[int] = None,
    trace_id: Optional[str] = None,
    limit: int = 20,
    system_id: int = Depends(get_system_id),
) -> List[ReplayVariantDraftOut]:
    limit = max(1, min(limit, 100))
    where = ["system_id = ?"]
    params: List[Any] = [system_id]
    if replay_set_id is not None:
        where.append("replay_set_id = ?")
        params.append(replay_set_id)
    if trace_id:
        where.append("trace_id = ?")
        params.append(trace_id)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM replay_variant_drafts
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            _draft_out(*_get_draft_with_run(conn, row["id"], system_id))
            for row in rows
        ]


@router.get("/replay-variant-drafts/{draft_id}", response_model=ReplayVariantDraftOut)
def get_replay_variant_draft(
    draft_id: int,
    system_id: int = Depends(get_system_id),
) -> ReplayVariantDraftOut:
    with get_conn() as conn:
        return _draft_out(*_get_draft_with_run(conn, draft_id, system_id))


# =============================================================================
# Source & diff helpers (Issue #242 Phase D / #246)
#
# Two small DETERMINISTIC additions backing the Simulation Workbench's
# "Direct edit" flow: read the pinned-snapshot source for the Replay Set's
# resolved symbol, and turn a developer-edited copy of it into a unified
# diff. No judgement here (Principle 6) -- both reuse the exact
# snapshot/symbol resolution POST /replay-runs already uses and the same
# worktree + ``git diff`` mechanism ``replay_draft`` uses for LLM drafts.
# =============================================================================


@router.get("/replay-sets/{replay_set_id}/source", response_model=ReplaySourceOut)
def get_replay_set_source(
    replay_set_id: int,
    snapshot_id: Optional[int] = None,
    system_id: int = Depends(get_system_id),
) -> ReplaySourceOut:
    with get_conn() as conn:
        replay_set = _get_set_or_404(conn, replay_set_id, system_id)
        component_id = replay_set["component_id"]
        snapshot = _resolve_snapshot(conn, system_id, snapshot_id)
        symbol = _resolve_component_symbol(
            conn, system_id, snapshot["id"], component_id
        )
        repo_path = snapshot["repo_path"]
        commit_sha = snapshot["commit_sha"]
        resolved_snapshot_id = snapshot["id"]
        path = symbol["path"]
        qualified_name = symbol["qualified_name"]
        start_line = symbol["start_line"]
        end_line = symbol["end_line"]

    try:
        raw = read_file_at_commit(repo_path, commit_sha, path)
    except GitError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Could not read {path} at {commit_sha}: {exc}",
        )
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=409, detail=f"{path} is not valid UTF-8: {exc}"
        )

    return ReplaySourceOut(
        replay_set_id=replay_set["id"],
        component_id=component_id,
        snapshot_id=resolved_snapshot_id,
        commit_sha=commit_sha,
        path=path,
        qualified_name=qualified_name,
        start_line=start_line,
        end_line=end_line,
        source=source,
    )


@router.post("/replay-source-diff", response_model=ReplaySourceDiffOut)
def create_replay_source_diff(
    payload: ReplaySourceDiffCreate,
    system_id: int = Depends(get_system_id),
) -> ReplaySourceDiffOut:
    """Deterministically diff a developer-edited copy of the resolved
    symbol's file against the pinned snapshot (Workbench "Direct edit" ->
    Run). Rejects non-Python ``edited_source`` with a 422, mirroring
    ``replay_draft``'s own ``ast.parse`` structural validity check --
    never a hand-written diff."""
    try:
        ast.parse(payload.edited_source)
    except SyntaxError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"edited_source is not valid Python: {exc}",
        )

    with get_conn() as conn:
        replay_set = _get_set_or_404(conn, payload.replay_set_id, system_id)
        component_id = replay_set["component_id"]
        snapshot = _resolve_snapshot(conn, system_id, payload.snapshot_id)
        symbol = _resolve_component_symbol(
            conn, system_id, snapshot["id"], component_id
        )
        repo_path = snapshot["repo_path"]
        commit_sha = snapshot["commit_sha"]
        symbol_path = symbol["path"]

    try:
        patch_text = diff_source_against_snapshot(
            repo_path,
            commit_sha,
            symbol_path,
            payload.edited_source,
            os.path.join(replay_workspace_base(), "source-diffs"),
        )
    except (DraftError, GitError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not patch_text.strip():
        raise HTTPException(
            status_code=422,
            detail="edited_source produced no changes against the pinned snapshot",
        )

    return ReplaySourceDiffOut(
        patch_text=patch_text, patch_hash=patch_hash(patch_text)
    )
