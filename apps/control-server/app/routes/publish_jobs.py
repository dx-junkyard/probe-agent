"""Publish job API: commit/push/PR creation for an approved probe patch
against a connected GitHub repository (Issue #216, sub-task 3).

Authorization follows the same pattern as `routes/github_connections.py`
(admin or system owner may manage a system's connections/publish jobs);
`_require_manage` / `_get_connection_or_404` are reused from there rather
than duplicated. All queries are scoped by `system_id` (System isolation).
No installation token, private key, or absolute host path is ever returned
by this router (Principle 5/8) -- `error` is stored pre-sanitized by
`app/publish_job.py`, so it is safe to return verbatim.

probe-agent:
  role: Publish job REST API boundary (create/list/get/approve/cancel)
  capability: github-publish-workflow
  element_type: boundary
  consumers: [dashboard]
  operation_kind: io
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify publish jobs are system-isolated, the validation gate is enforced before job creation, approve/cancel are idempotent (409 on repeat), and no token/host path ever reaches the response.
"""

from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, get_system_id, require_user
from ..db import get_conn
from .. import publish_job
from ..models import PublishJobCreate, PublishJobOut
from ..publish_job import PublishJobConflict, PublishJobNotFound
from .github_connections import _get_connection_or_404, _require_manage

router = APIRouter()


def _job_out(row) -> PublishJobOut:
    return PublishJobOut(
        id=row["id"],
        system_id=row["system_id"],
        connection_id=row["connection_id"],
        patch_id=row["patch_id"],
        snapshot_id=row["snapshot_id"],
        base_branch=row["base_branch"],
        base_commit_sha=row["base_commit_sha"],
        branch_name=row["branch_name"],
        commit_sha=row["commit_sha"],
        pr_url=row["pr_url"],
        pr_number=row["pr_number"],
        status=row["status"],
        error=row["error"],
        validation_summary=json.loads(row["validation_summary"]) if row["validation_summary"] else None,
        requested_by_user_id=row["requested_by_user_id"],
        approved_by_user_id=row["approved_by_user_id"],
        cleanup_state=row["cleanup_state"],
        cleanup_error=row["cleanup_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        approved_at=row["approved_at"],
        completed_at=row["completed_at"],
        heartbeat_at=row["heartbeat_at"],
    )


def _get_job_or_404(conn, job_id: int, system_id: int):
    row = conn.execute(
        "SELECT * FROM publish_jobs WHERE id = ? AND system_id = ?",
        (job_id, system_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Publish job not found")
    return row


@router.post(
    "/github/connections/{connection_id}/publish-jobs",
    response_model=PublishJobOut,
    status_code=201,
)
def create_publish_job_endpoint(
    connection_id: int,
    payload: PublishJobCreate,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> PublishJobOut:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        _get_connection_or_404(conn, connection_id, system_id)

    try:
        job_id = publish_job.create_publish_job(
            system_id, connection_id, payload.patch_id, principal.user_id
        )
    except PublishJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PublishJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    with get_conn() as conn:
        row = _get_job_or_404(conn, job_id, system_id)
    return _job_out(row)


@router.get("/github/publish-jobs", response_model=List[PublishJobOut])
def list_publish_jobs(
    connection_id: Optional[int] = None,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> List[PublishJobOut]:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        if connection_id is not None:
            rows = conn.execute(
                "SELECT * FROM publish_jobs WHERE system_id = ? AND connection_id = ? "
                "ORDER BY id DESC",
                (system_id, connection_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM publish_jobs WHERE system_id = ? ORDER BY id DESC",
                (system_id,),
            ).fetchall()
    return [_job_out(row) for row in rows]


@router.get("/github/publish-jobs/{job_id}", response_model=PublishJobOut)
def get_publish_job(
    job_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> PublishJobOut:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)
        row = _get_job_or_404(conn, job_id, system_id)
    return _job_out(row)


@router.post("/github/publish-jobs/{job_id}/approve", response_model=PublishJobOut)
def approve_publish_job_endpoint(
    job_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> PublishJobOut:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)

    try:
        publish_job.approve_publish_job(job_id, system_id, principal.user_id)
    except PublishJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PublishJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    with get_conn() as conn:
        row = _get_job_or_404(conn, job_id, system_id)
    return _job_out(row)


@router.post("/github/publish-jobs/{job_id}/cancel", response_model=PublishJobOut)
def cancel_publish_job_endpoint(
    job_id: int,
    principal: Principal = Depends(require_user),
    system_id: int = Depends(get_system_id),
) -> PublishJobOut:
    with get_conn() as conn:
        _require_manage(conn, principal, system_id)

    try:
        publish_job.cancel_publish_job(job_id, system_id)
    except PublishJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PublishJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    with get_conn() as conn:
        row = _get_job_or_404(conn, job_id, system_id)
    return _job_out(row)
