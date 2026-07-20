import json
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal, get_principal, get_system_id
from ..db import get_conn
from ..models import ProjectionOut, TraceEvent
from ..resource_limits import (
    ResourceLimitExceeded,
    enforce_trace_rate_limit,
    trace_storage_limits,
)

router = APIRouter()


def _utf8_size(value) -> int:
    if value is None:
        return 0
    return len(str(value).encode("utf-8"))


def _trace_values(event: TraceEvent) -> dict:
    return {
        "trace_id": event.trace_id,
        "component_id": event.component_id,
        "mode": event.mode,
        "input_json": json.dumps(event.input, ensure_ascii=False)
        if event.input is not None else None,
        "output_text": event.output,
        "error": event.error,
        "input_capture_json": json.dumps(event.input_capture, ensure_ascii=False)
        if event.input_capture is not None else None,
        "replayability": event.replayability,
        "replay_reasons_json": json.dumps(event.replay_reasons, ensure_ascii=False)
        if event.replay_reasons is not None else None,
        # Issue #290 Finding 5: optional deployment provenance reported by
        # the SDK (PROBE_ENVIRONMENT / PROBE_GIT_SHA); None when unset.
        "environment": event.environment,
        "git_sha": event.git_sha,
    }


def _estimated_trace_bytes(values: dict) -> int:
    # Text/blob payload dominates storage. Include fixed numeric columns as a
    # conservative constant so the approximation never reports zero.
    return 16 + sum(_utf8_size(value) for value in values.values())


def _stored_trace_bytes(row) -> int:
    if row is None:
        return 0
    return 16 + sum(_utf8_size(row[key]) for key in (
        "trace_id", "component_id", "mode", "input_json", "output_text",
        "error", "input_capture_json", "replayability", "replay_reasons_json",
        "environment", "git_sha",
    ))


def _current_trace_usage(conn, system_id: int) -> tuple[int, int]:
    row = conn.execute(
        """SELECT COUNT(*) AS trace_rows,
                  COALESCE(SUM(
                    16
                    + length(CAST(COALESCE(trace_id, '') AS BLOB))
                    + length(CAST(COALESCE(component_id, '') AS BLOB))
                    + length(CAST(COALESCE(mode, '') AS BLOB))
                    + length(CAST(COALESCE(input_json, '') AS BLOB))
                    + length(CAST(COALESCE(output_text, '') AS BLOB))
                    + length(CAST(COALESCE(error, '') AS BLOB))
                    + length(CAST(COALESCE(input_capture_json, '') AS BLOB))
                    + length(CAST(COALESCE(replayability, '') AS BLOB))
                    + length(CAST(COALESCE(replay_reasons_json, '') AS BLOB))
                    + length(CAST(COALESCE(environment, '') AS BLOB))
                    + length(CAST(COALESCE(git_sha, '') AS BLOB))
                  ), 0) AS trace_bytes
           FROM traces WHERE system_id = ?""",
        (system_id,),
    ).fetchone()
    return row["trace_rows"], row["trace_bytes"]


def _record_trace_quota_status(
    conn, system_id: int, rows: int, size_bytes: int, *, reason: Optional[str]
) -> None:
    now = time.time()
    conn.execute(
        """INSERT INTO trace_quota_status
               (system_id, trace_rows, trace_bytes, rejected_reason,
                rejected_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(system_id) DO UPDATE SET
               trace_rows = excluded.trace_rows,
               trace_bytes = excluded.trace_bytes,
               rejected_reason = excluded.rejected_reason,
               rejected_at = excluded.rejected_at,
               updated_at = excluded.updated_at""",
        (system_id, rows, size_bytes, reason, now if reason else None, now),
    )


def _ensure_component(conn, system_id: int, component_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO components
            (system_id, component_id, mode, updated_at)
        VALUES (?, ?, 'trace', ?)
        """,
        (system_id, component_id, time.time()),
    )


def _write_lineage(conn, system_id: int, event: TraceEvent) -> None:
    """Persist optional lineage metadata (Issue #145) in dedicated tables.

    Span and entity rows are re-materialized per trace so a re-post stays
    consistent with the INSERT OR REPLACE ``traces`` row: a re-post without
    span metadata deletes the stale span, so old correlation/flow ids never
    survive onto a trace that no longer carries them.
    """
    has_span = any(
        v is not None
        for v in (event.span_id, event.parent_span_id, event.flow_id, event.correlation_id)
    )
    # Re-materialize the span row for this trace (idempotent on re-post).
    conn.execute(
        "DELETE FROM trace_spans WHERE system_id = ? AND trace_id = ?",
        (system_id, event.trace_id),
    )
    if has_span:
        conn.execute(
            """
            INSERT INTO trace_spans
                (system_id, trace_id, component_id, span_id, parent_span_id,
                 flow_id, correlation_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                event.trace_id,
                event.component_id,
                event.span_id,
                event.parent_span_id,
                event.flow_id,
                event.correlation_id,
                event.timestamp,
            ),
        )

    # Re-materialize entities for this trace (idempotent on re-post).
    conn.execute(
        "DELETE FROM trace_entities WHERE system_id = ? AND trace_id = ?",
        (system_id, event.trace_id),
    )
    for ent in event.entities or []:
        conn.execute(
            """
            INSERT INTO trace_entities
                (system_id, trace_id, component_id, entity_type, entity_id, role, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                event.trace_id,
                event.component_id,
                ent.type,
                ent.id,
                ent.role,
                event.timestamp,
            ),
        )


def _write_projections(conn, system_id: int, event: TraceEvent) -> None:
    """Persist optional projections (Issue #146). Only the bounded, structured
    slice is stored — never the raw payload. Idempotent on re-post.

    The trace's own input/output projections are re-materialized per trace so a
    re-post with fewer (or no) projections cannot leave stale rows that then
    look like they belong to the new trace. Shadow projections
    (shadow_current/shadow_candidate) are owned by the shadow-results route and
    use a disjoint phase namespace, so they are deliberately left untouched.
    """
    conn.execute(
        "DELETE FROM trace_projections WHERE system_id = ? AND trace_id = ? "
        "AND phase NOT IN ('shadow_current', 'shadow_candidate')",
        (system_id, event.trace_id),
    )
    for proj in event.projections or []:
        data_json = json.dumps(
            {"fields": proj.fields, "metrics": proj.metrics, "samples": proj.samples},
            ensure_ascii=False,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO trace_projections
                (system_id, trace_id, component_id, projection_name, phase,
                 data_json, data_hash, truncated, extract_error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                event.trace_id,
                event.component_id,
                proj.projection_name,
                proj.phase,
                data_json,
                proj.data_hash,
                1 if proj.truncated else 0,
                proj.error,
                event.timestamp,
            ),
        )


@router.post("/traces", status_code=201)
def post_trace(
    event: TraceEvent,
    system_id: int = Depends(get_system_id),
    principal: Principal = Depends(get_principal),
) -> dict:
    token_key = None
    if principal.token_kind == "api" and principal.token_id is not None:
        token_key = f"api:{principal.token_id}"
    elif principal.auth == "legacy_api_key" and principal.credential_fingerprint:
        token_key = f"legacy:{principal.credential_fingerprint}"
    if token_key is not None:
        try:
            enforce_trace_rate_limit(token_key)
        except ResourceLimitExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    values = _trace_values(event)
    with get_conn() as conn:
        # Serialize check + write across processes, not only the in-process DB
        # lock, so concurrent workers cannot both pass the same last slot.
        conn.execute("BEGIN IMMEDIATE")
        try:
            current_rows, current_bytes = _current_trace_usage(conn, system_id)
            previous = conn.execute(
            """SELECT trace_id, component_id, mode, input_json, output_text, error,
                      input_capture_json, replayability, replay_reasons_json,
                      environment, git_sha
               FROM traces WHERE system_id = ? AND trace_id = ?""",
            (system_id, event.trace_id),
            ).fetchone()
            projected_rows = current_rows + (0 if previous is not None else 1)
            projected_bytes = (
                current_bytes - _stored_trace_bytes(previous)
                + _estimated_trace_bytes(values)
            )
            max_rows, max_bytes = trace_storage_limits()
            reject_code = None
            if projected_rows > max_rows:
                reject_code = "trace_storage_row_limit_exceeded"
            elif projected_bytes > max_bytes:
                reject_code = "trace_storage_byte_limit_exceeded"
            if reject_code:
                _record_trace_quota_status(
                    conn, system_id, current_rows, current_bytes, reason=reject_code
                )
                conn.execute("COMMIT")
                raise HTTPException(
                    status_code=429,
                    detail={
                        "code": reject_code,
                        "message": "System trace storage quota exceeded",
                        "current_rows": current_rows,
                        "current_bytes": current_bytes,
                        "max_rows": max_rows,
                        "max_bytes": max_bytes,
                    },
                )
            _ensure_component(conn, system_id, event.component_id)
            conn.execute(
                """
                INSERT OR REPLACE INTO traces
                    (system_id, trace_id, component_id, mode, input_json, output_text,
                     error, duration_ms, timestamp,
                     input_capture_json, replayability, replay_reasons_json,
                     environment, git_sha)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    system_id,
                    event.trace_id,
                    event.component_id,
                    event.mode,
                    values["input_json"],
                    values["output_text"],
                    values["error"],
                    event.duration_ms,
                    event.timestamp,
                    values["input_capture_json"],
                    values["replayability"],
                    values["replay_reasons_json"],
                    values["environment"],
                    values["git_sha"],
                ),
            )
            _write_lineage(conn, system_id, event)
            _write_projections(conn, system_id, event)
            _record_trace_quota_status(
                conn, system_id, projected_rows, projected_bytes, reason=None
            )
            if event.sdk_transport is not None:
                conn.execute(
                    """INSERT OR REPLACE INTO sdk_transport_observations
                           (system_id, trace_id, dropped_count, failure_count,
                            state, observed_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        system_id,
                        event.trace_id,
                        event.sdk_transport.dropped_count,
                        event.sdk_transport.failure_count,
                        event.sdk_transport.state,
                        time.time(),
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return {
        "ok": True,
        "trace_id": event.trace_id,
        "sdk_transport_observed": event.sdk_transport is not None,
    }


@router.get("/sdk-transport/status")
def sdk_transport_status(system_id: int = Depends(get_system_id)) -> dict:
    """Expose persisted SDK loss/breaker observations without raw payloads."""
    with get_conn() as conn:
        latest = conn.execute(
            """SELECT trace_id, dropped_count, failure_count, state, observed_at
               FROM sdk_transport_observations WHERE system_id = ?
               ORDER BY observed_at DESC, trace_id DESC LIMIT 1""",
            (system_id,),
        ).fetchone()
        totals = conn.execute(
            """SELECT COALESCE(SUM(dropped_count), 0) AS dropped_count,
                      COALESCE(SUM(failure_count), 0) AS failure_count,
                      COUNT(*) AS observation_count
               FROM sdk_transport_observations WHERE system_id = ?""",
            (system_id,),
        ).fetchone()
    return {
        "system_id": system_id,
        "dropped_count": totals["dropped_count"],
        "failure_count": totals["failure_count"],
        "observation_count": totals["observation_count"],
        "latest": dict(latest) if latest is not None else None,
    }


def _row_to_projection(row) -> ProjectionOut:
    try:
        data = json.loads(row["data_json"]) if row["data_json"] else {}
    except json.JSONDecodeError:
        data = {}
    return ProjectionOut(
        trace_id=row["trace_id"],
        component_id=row["component_id"],
        projection_name=row["projection_name"],
        phase=row["phase"],
        fields=data.get("fields", {}) or {},
        metrics=data.get("metrics", {}) or {},
        samples=data.get("samples", {}) or {},
        data_hash=row["data_hash"],
        truncated=bool(row["truncated"]),
        error=row["extract_error"],
        created_at=row["created_at"],
    )


@router.get("/traces/{trace_id}/projections", response_model=List[ProjectionOut])
def list_trace_projections(
    trace_id: str, system_id: int = Depends(get_system_id)
) -> List[ProjectionOut]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trace_id, component_id, projection_name, phase, data_json,
                   data_hash, truncated, extract_error, created_at
            FROM trace_projections
            WHERE system_id = ? AND trace_id = ?
            ORDER BY projection_name, phase
            """,
            (system_id, trace_id),
        ).fetchall()
    return [_row_to_projection(r) for r in rows]


@router.get("/components/{component_id}/projections", response_model=List[ProjectionOut])
def list_component_projections(
    component_id: str,
    limit: int = 100,
    system_id: int = Depends(get_system_id),
) -> List[ProjectionOut]:
    limit = max(1, min(limit, 1000))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trace_id, component_id, projection_name, phase, data_json,
                   data_hash, truncated, extract_error, created_at
            FROM trace_projections
            WHERE system_id = ? AND component_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (system_id, component_id, limit),
        ).fetchall()
    return [_row_to_projection(r) for r in rows]


@router.get("/components/{component_id}/traces")
def list_traces(
    component_id: str,
    limit: int = 50,
    system_id: int = Depends(get_system_id),
) -> List[dict]:
    limit = max(1, min(limit, 500))
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT trace_id, component_id, mode, input_json, output_text,
                   error, duration_ms, timestamp,
                   input_capture_json, replayability, replay_reasons_json
            FROM traces
            WHERE system_id = ? AND component_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (system_id, component_id, limit),
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        if d.get("input_json"):
            try:
                d["input"] = json.loads(d["input_json"])
            except json.JSONDecodeError:
                d["input"] = d["input_json"]
        else:
            d["input"] = None
        d.pop("input_json", None)
        d["output"] = d.pop("output_text", None)
        # Replay capture (Issue #242 Phase A / #243): NULL columns (pre-Phase-A
        # rows or components not opted in) surface as explicit nulls.
        d["input_capture"] = _load_json_or_none(d.pop("input_capture_json", None))
        d["replay_reasons"] = _load_json_or_none(d.pop("replay_reasons_json", None))
        result.append(d)
    return result


def _load_json_or_none(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
