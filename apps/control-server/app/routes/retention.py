"""Retention policy + application API (Issue #152).

Policies are explicit and system-scoped; with none configured nothing is ever
deleted. Application is an explicit trigger, deterministic and audited.
"""

import time
from typing import List

from fastapi import APIRouter, Depends

from ..auth import get_system_id
from ..db import get_conn
from ..models import (
    RetentionApplyOut,
    RetentionApplyResult,
    RetentionAuditOut,
    RetentionPoliciesUpdate,
    RetentionPolicyOut,
)
from ..retention import apply_retention

router = APIRouter()


@router.get("/retention/policies", response_model=List[RetentionPolicyOut])
def list_policies(system_id: int = Depends(get_system_id)) -> List[RetentionPolicyOut]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT target_table, max_age_days, max_count, updated_at "
            "FROM retention_policies WHERE system_id = ? ORDER BY target_table",
            (system_id,),
        ).fetchall()
    return [RetentionPolicyOut(**dict(r)) for r in rows]


@router.put("/retention/policies", response_model=List[RetentionPolicyOut])
def set_policies(
    body: RetentionPoliciesUpdate, system_id: int = Depends(get_system_id)
) -> List[RetentionPolicyOut]:
    now = time.time()
    with get_conn() as conn:
        for pol in body.policies:
            if pol.max_age_days is None and pol.max_count is None:
                # Clearing both bounds removes the policy (=> keep everything).
                conn.execute(
                    "DELETE FROM retention_policies WHERE system_id = ? AND target_table = ?",
                    (system_id, pol.target_table),
                )
                continue
            conn.execute(
                """
                INSERT INTO retention_policies
                    (system_id, target_table, max_age_days, max_count, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (system_id, target_table) DO UPDATE SET
                    max_age_days = excluded.max_age_days,
                    max_count = excluded.max_count,
                    updated_at = excluded.updated_at
                """,
                (system_id, pol.target_table, pol.max_age_days, pol.max_count, now),
            )
        rows = conn.execute(
            "SELECT target_table, max_age_days, max_count, updated_at "
            "FROM retention_policies WHERE system_id = ? ORDER BY target_table",
            (system_id,),
        ).fetchall()
    return [RetentionPolicyOut(**dict(r)) for r in rows]


@router.post("/retention/apply", response_model=RetentionApplyOut)
def apply(system_id: int = Depends(get_system_id)) -> RetentionApplyOut:
    executed_at = time.time()
    with get_conn() as conn:
        results = apply_retention(conn, system_id, reason="manual")
    return RetentionApplyOut(
        executed_at=executed_at,
        results=[RetentionApplyResult(**r) for r in results],
    )


@router.get("/retention/audit", response_model=List[RetentionAuditOut])
def audit(
    limit: int = 100, system_id: int = Depends(get_system_id)
) -> List[RetentionAuditOut]:
    limit = max(1, min(limit, 1000))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, target_table, deleted_count, reason, executed_at "
            "FROM retention_audit WHERE system_id = ? ORDER BY id DESC LIMIT ?",
            (system_id, limit),
        ).fetchall()
    return [RetentionAuditOut(**dict(r)) for r in rows]
