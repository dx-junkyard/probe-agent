"""Replay readiness preflight endpoint (Issue #372).

Answers "can this component's traces actually be replayed" *before* a
candidate is generated, so an all-`not captured` component cannot burn a
reasoning-model call on a candidate nobody can evaluate.

Read-only and deterministic: it counts persisted classifications, it does not
attempt to reconstruct an uncaptured input (an explicit non-goal of #372).
"""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from ..auth import get_system_id
from ..db import get_conn
from ..models import (
    ReplayReadinessCheckOut,
    ReplayReadinessCountsOut,
    ReplayReadinessOut,
    ReplayTraceReadinessOut,
)
from ..replay_readiness import (
    ReplayCounts,
    ReplayReadiness,
    count_replayability,
    evaluate_readiness,
    primary_reason,
    ReplayTraceReadiness,
)
from .replay import MAX_REPLAY_SET_SIZE

router = APIRouter()


def _counts_out(counts: ReplayCounts) -> ReplayReadinessCountsOut:
    return ReplayReadinessCountsOut(
        total=counts.total,
        replayable=counts.replayable,
        partial=counts.partial,
        unreplayable=counts.unreplayable,
        not_captured=counts.not_captured,
        usable=counts.usable,
    )


def to_out(readiness: ReplayReadiness) -> ReplayReadinessOut:
    return ReplayReadinessOut(
        component_id=readiness.component_id,
        snapshot_id=readiness.snapshot_id,
        counts=_counts_out(readiness.counts),
        selected=_counts_out(readiness.selected),
        selection_limit=readiness.selection_limit,
        selection_is_automatic=readiness.selection_is_automatic,
        verdict=readiness.verdict,
        checks=[
            ReplayReadinessCheckOut(
                check_id=c.check_id,
                status=c.status,
                summary=c.summary,
                detail=c.detail,
                remediation=c.remediation,
            )
            for c in readiness.checks
        ],
        traces=[
            ReplayTraceReadinessOut(
                trace_id=trace.trace_id,
                replayability=trace.replayability,
                primary_reason=trace.primary_reason,
            )
            for trace in readiness.traces
        ],
    )


def gather_readiness(
    system_id: int,
    component_id: str,
    trace_ids: Optional[List[str]] = None,
    snapshot_id: Optional[int] = None,
) -> ReplayReadiness:
    """Read the facts and evaluate them.

    ``trace_ids`` omitted mirrors the Candidate Studio default: the most recent
    ``MAX_REPLAY_SET_SIZE`` traces. The preflight therefore describes exactly
    the set a run would use, not a hypothetical one.
    """
    with get_conn() as conn:
        all_rows = conn.execute(
            """SELECT trace_id, replayability, replay_reasons_json FROM traces
               WHERE system_id = ? AND component_id = ?""",
            (system_id, component_id),
        ).fetchall()
        if trace_ids:
            placeholders = ",".join("?" for _ in trace_ids)
            selected_rows = conn.execute(
                f"""SELECT trace_id, replayability, replay_reasons_json FROM traces
                    WHERE system_id = ? AND component_id = ?
                      AND trace_id IN ({placeholders})""",
                (system_id, component_id, *trace_ids),
            ).fetchall()
        else:
            selected_rows = conn.execute(
                """SELECT trace_id, replayability, replay_reasons_json FROM traces
                   WHERE system_id = ? AND component_id = ?
                   ORDER BY timestamp DESC, trace_id
                   LIMIT ?""",
                (system_id, component_id, MAX_REPLAY_SET_SIZE),
            ).fetchall()

        if snapshot_id is None:
            snapshot = conn.execute(
                """SELECT id FROM repository_snapshots
                   WHERE system_id = ? AND status = 'ready'
                   ORDER BY id DESC LIMIT 1""",
                (system_id,),
            ).fetchone()
        else:
            snapshot = conn.execute(
                """SELECT id FROM repository_snapshots
                   WHERE system_id = ? AND id = ? AND status = 'ready'""",
                (system_id, snapshot_id),
            ).fetchone()
        symbol_resolved = False
        if snapshot is not None:
            symbol = conn.execute(
                """SELECT 1 FROM code_symbols
                   WHERE snapshot_id = ? AND component_id = ? LIMIT 1""",
                (snapshot["id"], component_id),
            ).fetchone()
            symbol_resolved = symbol is not None
        approval = conn.execute(
            """SELECT 1 FROM replay_approvals
               WHERE system_id = ? AND component_id = ? AND revoked_at IS NULL
               LIMIT 1""",
            (system_id, component_id),
        ).fetchone()
        repo_configured = conn.execute(
            "SELECT 1 FROM repository_configs WHERE system_id = ? LIMIT 1",
            (system_id,),
        ).fetchone()

    readiness = evaluate_readiness(
        component_id=component_id,
        counts=count_replayability(all_rows),
        selected=count_replayability(selected_rows),
        selection_limit=MAX_REPLAY_SET_SIZE,
        selection_is_automatic=not trace_ids,
        symbol_resolved=symbol_resolved,
        symbol_detail=(
            "" if symbol_resolved
            else "解析済み Snapshot 上に、この component に対応する関数シンボルがありません。"
        ),
        approval_active=approval is not None,
        sandbox_available=repo_configured is not None,
        sandbox_detail=(
            "" if repo_configured is not None
            else "リポジトリが設定されていないため、隔離 worktree を作成できません。"
        ),
    )
    readiness.snapshot_id = snapshot["id"] if snapshot is not None else snapshot_id
    readiness.traces = [
        ReplayTraceReadiness(
            trace_id=row["trace_id"],
            replayability=row["replayability"] or "not_captured",
            primary_reason=primary_reason(
                json.loads(row["replay_reasons_json"])
                if row["replay_reasons_json"] else None
            ),
        )
        for row in selected_rows
    ]
    return readiness


@router.get("/replay-readiness", response_model=ReplayReadinessOut)
def get_replay_readiness(
    component_id: str,
    snapshot_id: Optional[int] = None,
    trace_ids: Optional[List[str]] = Query(
        None,
        description=(
            "Traces to evaluate. Omitted evaluates the same automatic window a "
            "run would use (the most recent traces, capped at the Replay Set size)."
        ),
    ),
    system_id: int = Depends(get_system_id),
) -> ReplayReadinessOut:
    return to_out(gather_readiness(system_id, component_id, trace_ids, snapshot_id))
