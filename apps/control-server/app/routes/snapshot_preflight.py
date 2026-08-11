"""The shared Snapshot preflight endpoint (Issue #369).

Candidate generation, Replay, and Experiment creation all need the same four
answers before they start — which commit, how fresh, is the symbol index
built, is the understanding usable — and before this endpoint they each
answered them differently. One evaluation, rendered by every surface, is the
point: `app/snapshot_preflight.py` decides, this route only serialises.

Read-only. It runs `git rev-parse` / `merge-base` against the configured
repository and never fetches, pulls, checks out, or writes (Principle 5).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from ..auth import get_system_id
from ..models import (
    SnapshotPreflightCheckOut,
    SnapshotPreflightOut,
)
from ..snapshot_preflight import (
    PreflightResult,
    StaleSnapshotNotAcknowledged,
    gather_preflight,
    resolve_stale_decision,
    stale_continuation_message,
)

router = APIRouter()


def to_out(result: PreflightResult) -> SnapshotPreflightOut:
    """Serialise a preflight result. Shared with the surfaces that embed it."""
    return SnapshotPreflightOut(
        snapshot_id=result.snapshot_id,
        processing_state=result.processing_state,
        freshness=result.freshness,
        commit_sha=result.commit_sha,
        head_sha=result.head_sha,
        head_relation=result.head_relation,
        commits_behind=result.commits_behind,
        verdict=result.verdict,
        checks=[
            SnapshotPreflightCheckOut(
                check_id=check.check_id,
                status=check.status,
                summary=check.summary,
                detail=check.detail,
                remediation=check.remediation,
            )
            for check in result.checks
        ],
        recommended_snapshot_id=result.recommended_snapshot_id,
        recommended_snapshot_commit_sha=result.recommended_snapshot_commit_sha,
        recommended_snapshot_freshness=result.recommended_snapshot_freshness,
        is_recommended=result.is_recommended,
        requires_stale_acknowledgement=result.requires_stale_acknowledgement,
        stale_continuation_note=(
            stale_continuation_message(result)
            if result.requires_stale_acknowledgement
            else None
        ),
    )


def require_snapshot_preflight(
    system_id: int,
    snapshot_id: Optional[int],
    stale_reason: Optional[str],
) -> tuple:
    """Evaluate the shared preflight and enforce the stale-snapshot gate.

    Used by every surface that starts work against a snapshot — Experiment
    creation, Candidate Studio sessions, and Replay runs — so the three cannot
    disagree about whether a snapshot may be used, or about what continuing on
    a stale one requires.

    Returns the ``(freshness, head_sha, stale_ack_reason)`` triple to persist
    on the record that consumes the snapshot.

    Must be called with NO ``get_conn()`` connection open: ``gather_preflight``
    runs ``git`` subprocesses and the SQLite lock is process-wide.
    """
    preflight = gather_preflight(system_id, snapshot_id)
    try:
        return resolve_stale_decision(preflight, stale_reason)
    except StaleSnapshotNotAcknowledged as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stale_snapshot_not_acknowledged",
                "message": str(exc),
                "recommended_snapshot_id": preflight.recommended_snapshot_id,
            },
        ) from exc


@router.get("/snapshot-preflight", response_model=SnapshotPreflightOut)
def get_snapshot_preflight(
    snapshot_id: Optional[int] = Query(
        None,
        description=(
            "Snapshot to evaluate. Omitted evaluates the recommended (latest "
            "ready) snapshot -- the same one a run would resolve by default."
        ),
    ),
    system_id: int = Depends(get_system_id),
) -> SnapshotPreflightOut:
    result = gather_preflight(system_id, snapshot_id)
    if snapshot_id is not None and result.snapshot_id is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return to_out(result)
