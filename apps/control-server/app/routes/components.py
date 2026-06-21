import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..models import ComponentSummary, Policy, PolicyUpdate

router = APIRouter()


@router.get("/components", response_model=List[ComponentSummary])
def list_components(system_id: int = Depends(get_system_id)) -> List[ComponentSummary]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.component_id        AS component_id,
                   c.mode                AS mode,
                   COUNT(t.trace_id)     AS trace_count,
                   MAX(t.timestamp)      AS last_seen
            FROM components c
            LEFT JOIN traces t
              ON t.system_id = c.system_id AND t.component_id = c.component_id
            WHERE c.system_id = ?
            GROUP BY c.component_id
            ORDER BY c.component_id
            """,
            (system_id,),
        ).fetchall()
    return [ComponentSummary(**dict(r)) for r in rows]


@router.get("/components/{component_id}/policy", response_model=Policy)
def get_policy(
    component_id: str, system_id: int = Depends(get_system_id)
) -> Policy:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT mode FROM components WHERE system_id = ? AND component_id = ?",
            (system_id, component_id),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO components (system_id, component_id, mode, updated_at)
                VALUES (?, ?, 'trace', ?)
                """,
                (system_id, component_id, time.time()),
            )
            return Policy(mode="trace")
    return Policy(mode=row["mode"])


@router.put("/components/{component_id}/policy", response_model=Policy)
def put_policy(
    component_id: str,
    update: PolicyUpdate,
    system_id: int = Depends(get_system_id),
) -> Policy:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO components (system_id, component_id, mode, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(system_id, component_id) DO UPDATE SET
                mode = excluded.mode,
                updated_at = excluded.updated_at
            """,
            (system_id, component_id, update.mode, time.time()),
        )
        if cur.rowcount == 0:
            raise HTTPException(500, "failed to upsert policy")
    return Policy(mode=update.mode)
