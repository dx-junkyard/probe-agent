import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response

from ..auth import Principal, require_user
from ..db import get_conn
from ..models import SystemCreate, SystemOut, SystemUpdate

router = APIRouter()


def _system_out(row) -> SystemOut:
    return SystemOut(
        id=row["id"],
        name=row["name"],
        environment=row["environment"] or "",
        description=row["description"] or "",
        owner_user_id=row["owner_user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        component_count=row["component_count"] or 0,
        trace_count=row["trace_count"] or 0,
        last_seen=row["last_seen"],
    )


_SYSTEM_SELECT = """
SELECT s.*,
       COUNT(DISTINCT c.component_id) AS component_count,
       COUNT(DISTINCT t.trace_id) AS trace_count,
       MAX(t.timestamp) AS last_seen
FROM systems s
LEFT JOIN components c ON c.system_id = s.id
LEFT JOIN traces t ON t.system_id = s.id
"""


def _can_manage(principal: Principal, owner_user_id: int) -> bool:
    return principal.is_admin or owner_user_id == principal.user_id


@router.get("/systems", response_model=List[SystemOut])
def list_systems(principal: Principal = Depends(require_user)) -> List[SystemOut]:
    with get_conn() as conn:
        if principal.is_admin:
            rows = conn.execute(
                _SYSTEM_SELECT + " GROUP BY s.id ORDER BY s.name, s.environment, s.id"
            ).fetchall()
        else:
            rows = conn.execute(
                _SYSTEM_SELECT
                + " WHERE s.owner_user_id = ? GROUP BY s.id ORDER BY s.name, s.environment, s.id",
                (principal.user_id,),
            ).fetchall()
    return [_system_out(row) for row in rows]


@router.post("/systems", response_model=SystemOut, status_code=201)
def create_system(
    payload: SystemCreate, principal: Principal = Depends(require_user)
) -> SystemOut:
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO systems
                (name, environment, description, owner_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name.strip(),
                payload.environment.strip(),
                payload.description,
                principal.user_id,
                now,
                now,
            ),
        )
        row = conn.execute(
            _SYSTEM_SELECT + " WHERE s.id = ? GROUP BY s.id", (cur.lastrowid,)
        ).fetchone()
    return _system_out(row)


@router.put("/systems/{system_id}", response_model=SystemOut)
def update_system(
    system_id: int,
    payload: SystemUpdate,
    principal: Principal = Depends(require_user),
) -> SystemOut:
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT owner_user_id FROM systems WHERE id = ?", (system_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="System not found")
        if not _can_manage(principal, existing["owner_user_id"]):
            raise HTTPException(status_code=403, detail="System access denied")
        conn.execute(
            """
            UPDATE systems
            SET name = ?, environment = ?, description = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.name.strip(),
                payload.environment.strip(),
                payload.description,
                now,
                system_id,
            ),
        )
        row = conn.execute(
            _SYSTEM_SELECT + " WHERE s.id = ? GROUP BY s.id", (system_id,)
        ).fetchone()
    return _system_out(row)


@router.delete("/systems/{system_id}", status_code=204)
def delete_system(
    system_id: int, principal: Principal = Depends(require_user)
) -> Response:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT owner_user_id FROM systems WHERE id = ?", (system_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="System not found")
        if existing["owner_user_id"] is None:
            raise HTTPException(status_code=409, detail="Legacy System cannot be deleted")
        if not _can_manage(principal, existing["owner_user_id"]):
            raise HTTPException(status_code=403, detail="System access denied")
        conn.execute("DELETE FROM api_tokens WHERE system_id = ?", (system_id,))
        conn.execute("DELETE FROM systems WHERE id = ?", (system_id,))
    return Response(status_code=204)
