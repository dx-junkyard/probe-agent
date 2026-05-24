import json
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException

from .. import evaluator
from ..db import get_conn
from ..models import (
    ComponentProfile,
    ComponentProfileUpdate,
    CriterionCreate,
    CriterionUpdate,
    EvaluationCriterion,
    EvaluationResult,
    SystemProfile,
    SystemProfileUpdate,
)

router = APIRouter()

_SYSTEM_PROFILE_ID = "default"


def _ensure_component(conn, component_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO components (component_id, mode, updated_at) VALUES (?, 'trace', ?)",
        (component_id, time.time()),
    )


def _loads_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


# --- System profile -------------------------------------------------------


@router.get("/system-profile", response_model=SystemProfile)
def get_system_profile() -> SystemProfile:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM system_profile WHERE id = ?", (_SYSTEM_PROFILE_ID,)
        ).fetchone()
    if row is None:
        return SystemProfile()
    return SystemProfile(
        name=row["name"] or "",
        purpose=row["purpose"] or "",
        target_users=_loads_list(row["target_users"]),
        stakeholder_value=row["stakeholder_value"] or "",
        constraints=_loads_list(row["constraints"]),
        success_criteria=_loads_list(row["success_criteria"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.put("/system-profile", response_model=SystemProfile)
def put_system_profile(update: SystemProfileUpdate) -> SystemProfile:
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT created_at FROM system_profile WHERE id = ?", (_SYSTEM_PROFILE_ID,)
        ).fetchone()
        created_at = existing["created_at"] if existing and existing["created_at"] else now
        conn.execute(
            """
            INSERT INTO system_profile
                (id, name, purpose, target_users, stakeholder_value,
                 constraints, success_criteria, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                purpose = excluded.purpose,
                target_users = excluded.target_users,
                stakeholder_value = excluded.stakeholder_value,
                constraints = excluded.constraints,
                success_criteria = excluded.success_criteria,
                updated_at = excluded.updated_at
            """,
            (
                _SYSTEM_PROFILE_ID,
                update.name,
                update.purpose,
                json.dumps(update.target_users, ensure_ascii=False),
                update.stakeholder_value,
                json.dumps(update.constraints, ensure_ascii=False),
                json.dumps(update.success_criteria, ensure_ascii=False),
                created_at,
                now,
            ),
        )
    return SystemProfile(
        name=update.name,
        purpose=update.purpose,
        target_users=update.target_users,
        stakeholder_value=update.stakeholder_value,
        constraints=update.constraints,
        success_criteria=update.success_criteria,
        created_at=created_at,
        updated_at=now,
    )


# --- Component profile ----------------------------------------------------


@router.get("/components/{component_id}/profile", response_model=ComponentProfile)
def get_component_profile(component_id: str) -> ComponentProfile:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM component_profiles WHERE component_id = ?", (component_id,)
        ).fetchone()
    if row is None:
        return ComponentProfile(component_id=component_id)
    return ComponentProfile(**dict(row))


@router.put("/components/{component_id}/profile", response_model=ComponentProfile)
def put_component_profile(
    component_id: str, update: ComponentProfileUpdate
) -> ComponentProfile:
    now = time.time()
    with get_conn() as conn:
        _ensure_component(conn, component_id)
        existing = conn.execute(
            "SELECT created_at FROM component_profiles WHERE component_id = ?",
            (component_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing and existing["created_at"] else now
        conn.execute(
            """
            INSERT INTO component_profiles
                (component_id, purpose, responsibility, expected_input,
                 expected_output, failure_impact, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(component_id) DO UPDATE SET
                purpose = excluded.purpose,
                responsibility = excluded.responsibility,
                expected_input = excluded.expected_input,
                expected_output = excluded.expected_output,
                failure_impact = excluded.failure_impact,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                component_id,
                update.purpose,
                update.responsibility,
                update.expected_input,
                update.expected_output,
                update.failure_impact,
                update.notes,
                created_at,
                now,
            ),
        )
    return ComponentProfile(
        component_id=component_id,
        created_at=created_at,
        updated_at=now,
        **update.model_dump(),
    )


# --- Evaluation criteria --------------------------------------------------


def _row_to_criterion(row) -> EvaluationCriterion:
    return EvaluationCriterion(
        id=row["id"],
        component_id=row["component_id"],
        name=row["name"],
        description=row["description"] or "",
        criterion_type=row["criterion_type"],
        expected_value=row["expected_value"],
        weight=row["weight"],
        enabled=bool(row["enabled"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get(
    "/components/{component_id}/criteria",
    response_model=List[EvaluationCriterion],
)
def list_criteria(component_id: str) -> List[EvaluationCriterion]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM evaluation_criteria WHERE component_id = ? ORDER BY id",
            (component_id,),
        ).fetchall()
    return [_row_to_criterion(r) for r in rows]


@router.post(
    "/components/{component_id}/criteria",
    response_model=EvaluationCriterion,
    status_code=201,
)
def create_criterion(
    component_id: str, payload: CriterionCreate
) -> EvaluationCriterion:
    now = time.time()
    with get_conn() as conn:
        _ensure_component(conn, component_id)
        cur = conn.execute(
            """
            INSERT INTO evaluation_criteria
                (component_id, name, description, criterion_type, expected_value,
                 weight, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                component_id,
                payload.name,
                payload.description,
                payload.criterion_type,
                payload.expected_value,
                payload.weight,
                1 if payload.enabled else 0,
                now,
                now,
            ),
        )
        criterion_id = cur.lastrowid
    return EvaluationCriterion(
        id=criterion_id,
        component_id=component_id,
        created_at=now,
        updated_at=now,
        **payload.model_dump(),
    )


@router.put("/criteria/{criterion_id}", response_model=EvaluationCriterion)
def update_criterion(
    criterion_id: int, payload: CriterionUpdate
) -> EvaluationCriterion:
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE evaluation_criteria SET
                name = ?,
                description = ?,
                criterion_type = ?,
                expected_value = ?,
                weight = ?,
                enabled = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                payload.name,
                payload.description,
                payload.criterion_type,
                payload.expected_value,
                payload.weight,
                1 if payload.enabled else 0,
                now,
                criterion_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "criterion not found")
        row = conn.execute(
            "SELECT * FROM evaluation_criteria WHERE id = ?", (criterion_id,)
        ).fetchone()
    return _row_to_criterion(row)


# --- Trace evaluation -----------------------------------------------------


@router.post(
    "/traces/{trace_id}/evaluate",
    response_model=List[EvaluationResult],
)
def evaluate_trace(trace_id: str) -> List[EvaluationResult]:
    now = time.time()
    with get_conn() as conn:
        trace = conn.execute(
            "SELECT component_id, output_text FROM traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if trace is None:
            raise HTTPException(404, "trace not found")
        component_id = trace["component_id"]
        actual_output = trace["output_text"]

        criteria = conn.execute(
            """
            SELECT * FROM evaluation_criteria
            WHERE component_id = ? AND enabled = 1
            ORDER BY id
            """,
            (component_id,),
        ).fetchall()

        # Re-evaluation replaces prior results for this trace.
        conn.execute("DELETE FROM evaluation_results WHERE trace_id = ?", (trace_id,))

        results: List[EvaluationResult] = []
        for c in criteria:
            status, score, reason = evaluator.evaluate(
                c["criterion_type"], c["expected_value"], actual_output
            )
            cur = conn.execute(
                """
                INSERT INTO evaluation_results
                    (trace_id, component_id, criterion_id, status, score,
                     reason, actual_output, expected_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    component_id,
                    c["id"],
                    status,
                    score,
                    reason,
                    actual_output,
                    c["expected_value"],
                    now,
                ),
            )
            results.append(
                EvaluationResult(
                    id=cur.lastrowid,
                    trace_id=trace_id,
                    component_id=component_id,
                    criterion_id=c["id"],
                    status=status,
                    score=score,
                    reason=reason,
                    actual_output=actual_output,
                    expected_value=c["expected_value"],
                    created_at=now,
                )
            )
    return results


@router.get(
    "/traces/{trace_id}/evaluations",
    response_model=List[EvaluationResult],
)
def list_evaluations(trace_id: str) -> List[EvaluationResult]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM evaluation_results WHERE trace_id = ? ORDER BY id",
            (trace_id,),
        ).fetchall()
    return [EvaluationResult(**dict(r)) for r in rows]
