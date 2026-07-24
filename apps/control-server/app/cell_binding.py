"""Probe Cell Fabric: versioned Cell Binding and read-only Probe Cell pilot
(Issue #299, Sub 2 of the Probe Cell Fabric epic, Issue #297).

This module owns:

- ``create_binding``: creates a new, append-only ``cell_bindings`` version
  from an APPROVED Probe Point or a Probe Pattern's saved point. Provenance
  (component_id / path / qualified_symbol / snapshot_id / commit_sha) always
  comes from that source row -- never guessed or user-supplied. Creating a
  new version supersedes the Cell's previous active/stale/review_required
  binding row in the same transaction; a version's content is never mutated
  in place.
- ``evaluate_binding_drift``: a purely structural (Principle 6) comparison of
  a binding's pinned path/qualified_symbol against the latest ready
  snapshot's ``code_symbols``. It NEVER re-binds to a different symbol --
  that is Issue #168's pattern-reconciliation flow's job. The only possible
  outcomes are ``active`` (still present, same snapshot), ``stale`` (still
  present, a newer ready snapshot exists), and ``review_required`` (symbol
  or path no longer found).
- ``build_cell_health``: deterministic aggregation of a worker Cell's
  runtime facts from ``traces`` (heartbeat / queue length / error rate /
  latency percentiles) and ``cell_activations`` (last activation). Every
  field is ``None`` when the underlying fact has never been observed --
  never a guessed/interpolated value. This is a read-only pilot: no LLM call
  and no per-trace execution path exist anywhere in this module.
- ``build_cell_state``: composes Sub 1's ``build_minimal_cell_state`` with
  the health block filled in. The ``cell_state`` schema itself is
  unchanged -- current binding / recent activations are NOT folded into the
  state document; callers (the API layer) return them as sibling fields in
  the response instead.

probe-agent:
  role: Cell Binding provenance/versioning/drift + read-only Cell health aggregation
  capability: probe-cell-fabric
  element_type: core
  consumers: [control-server-routes]
  operation_kind: mixed
  state_effects: [database-read, database-write]
  probe_value: Verify a binding can only be created from an approved probe point or a saved pattern point, versions are append-only with the prior version superseded atomically, drift evaluation never re-targets a different symbol, and health/activation facts are None (never guessed) when unobserved.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import cell_fabric, cell_quality

BINDING_STATUSES: Tuple[str, ...] = (
    "active", "stale", "review_required", "superseded",
)

_SUPERSEDABLE_STATUSES = ("active", "stale", "review_required")


class CellBindingError(ValueError):
    """Base fail-closed error for the Cell Binding layer."""


class CellBindingNotFoundError(CellBindingError):
    """A referenced row does not exist, or does not belong to this System.

    Cross-System references and genuinely-missing rows are deliberately
    indistinguishable at this layer (routes map this to 404) so another
    System's rows are never leaked via an error message.
    """


class CellBindingConflictError(CellBindingError):
    """A structural precondition failed (routes map this to 409)."""


class CellBindingValidationError(CellBindingError):
    """A payload was malformed (routes map this to 422)."""


# ---------------------------------------------------------------------------
# Ref list validation (feature/capability/entrypoint refs: format only)
# ---------------------------------------------------------------------------


def _validate_ref_list(refs: Optional[Sequence[Any]], field_name: str) -> List[str]:
    if refs is None:
        return []
    if not isinstance(refs, (list, tuple)):
        raise CellBindingValidationError(f"{field_name} must be a list of strings")
    out: List[str] = []
    for item in refs:
        if not isinstance(item, str) or not item.strip():
            raise CellBindingValidationError(
                f"{field_name} entries must be non-empty strings"
            )
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# create_binding
# ---------------------------------------------------------------------------


def create_binding(
    conn,
    *,
    system_id: int,
    cell_definition_id: int,
    probe_point_id: Optional[int] = None,
    probe_pattern_point_id: Optional[int] = None,
    feature_refs: Optional[Sequence[str]] = None,
    capability_refs: Optional[Sequence[str]] = None,
    entrypoint_refs: Optional[Sequence[str]] = None,
):
    """Create the next append-only ``cell_bindings`` version for a Cell.

    Exactly one of ``probe_point_id`` (must be ``status='approved'``) or
    ``probe_pattern_point_id`` (a ``probe_pattern_points`` row) must be
    given; provenance (component_id/path/qualified_symbol/snapshot_id/
    commit_sha) is read from that row (and its parent plan/pattern), never
    supplied by the caller. Uses the caller's open connection -- never opens
    a nested ``get_conn()`` (the DB lock is non-reentrant).
    """
    if (probe_point_id is None) == (probe_pattern_point_id is None):
        raise CellBindingValidationError(
            "Exactly one of probe_point_id or probe_pattern_point_id is required"
        )

    cell_row = conn.execute(
        "SELECT * FROM cell_definitions WHERE id = ? AND system_id = ?",
        (cell_definition_id, system_id),
    ).fetchone()
    if cell_row is None:
        raise CellBindingNotFoundError("Cell not found")
    if cell_row["roster_json"] is not None:
        raise CellBindingConflictError(
            "Cell Bindings may only be created for a worker Cell "
            "(roster is set, so this Cell is an orchestrator)"
        )

    if probe_point_id is not None:
        (
            component_id, path, qualified_symbol, snapshot_id, commit_sha,
            resolved_probe_point_id, resolved_probe_pattern_id,
        ) = _resolve_from_probe_point(conn, system_id=system_id, probe_point_id=probe_point_id)
    else:
        (
            component_id, path, qualified_symbol, snapshot_id, commit_sha,
            resolved_probe_point_id, resolved_probe_pattern_id,
        ) = _resolve_from_pattern_point(
            conn, system_id=system_id, probe_pattern_point_id=probe_pattern_point_id,
        )

    if not component_id:
        raise CellBindingConflictError("Provenance row has no component_id")
    if cell_row["cell_id"] != component_id:
        raise CellBindingConflictError(
            f"Cell id {cell_row['cell_id']!r} does not match the provenance "
            f"row's component_id {component_id!r} (a worker Cell's cell_id "
            "must equal its component_id)"
        )

    feature_refs_v = _validate_ref_list(feature_refs, "feature_refs")
    capability_refs_v = _validate_ref_list(capability_refs, "capability_refs")
    entrypoint_refs_v = _validate_ref_list(entrypoint_refs, "entrypoint_refs")

    now = time.time()
    conn.execute("BEGIN")
    try:
        max_row = conn.execute(
            """SELECT MAX(version) AS maxv FROM cell_bindings
               WHERE system_id = ? AND cell_definition_id = ?""",
            (system_id, cell_definition_id),
        ).fetchone()
        next_version = (max_row["maxv"] or 0) + 1

        conn.execute(
            f"""UPDATE cell_bindings SET status = 'superseded',
                    status_reason = 'superseded by version {next_version}'
                WHERE system_id = ? AND cell_definition_id = ?
                    AND status IN ({",".join("?" for _ in _SUPERSEDABLE_STATUSES)})""",
            (system_id, cell_definition_id, *_SUPERSEDABLE_STATUSES),
        )

        cur = conn.execute(
            """INSERT INTO cell_bindings
                   (system_id, cell_definition_id, version, snapshot_id,
                    commit_sha, path, qualified_symbol, component_id,
                    probe_point_id, probe_pattern_id, feature_refs_json,
                    capability_refs_json, entrypoint_refs_json, status,
                    status_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', '', ?)""",
            (
                system_id, cell_definition_id, next_version, snapshot_id,
                commit_sha, path, qualified_symbol, component_id,
                resolved_probe_point_id, resolved_probe_pattern_id,
                json.dumps(feature_refs_v), json.dumps(capability_refs_v),
                json.dumps(entrypoint_refs_v), now,
            ),
        )
        binding_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM cell_bindings WHERE id = ?", (binding_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def _resolve_from_probe_point(conn, *, system_id: int, probe_point_id: int):
    point_row = conn.execute(
        "SELECT * FROM probe_points WHERE id = ? AND system_id = ?",
        (probe_point_id, system_id),
    ).fetchone()
    if point_row is None:
        raise CellBindingNotFoundError("Probe point not found")
    if point_row["status"] != "approved":
        raise CellBindingConflictError(
            f"Probe point {probe_point_id} is not approved "
            f"(status={point_row['status']!r})"
        )
    plan_row = conn.execute(
        "SELECT * FROM probe_plans WHERE id = ? AND system_id = ?",
        (point_row["plan_id"], system_id),
    ).fetchone()
    if plan_row is None:
        raise CellBindingNotFoundError("Probe plan not found for this probe point")
    snapshot_row = conn.execute(
        "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
        (plan_row["snapshot_id"], system_id),
    ).fetchone()
    if snapshot_row is None:
        raise CellBindingNotFoundError("Snapshot not found for this probe plan")
    return (
        point_row["component_id"], point_row["path"], point_row["symbol"],
        snapshot_row["id"], snapshot_row["commit_sha"],
        point_row["id"], None,
    )


def _resolve_from_pattern_point(conn, *, system_id: int, probe_pattern_point_id: int):
    point_row = conn.execute(
        "SELECT * FROM probe_pattern_points WHERE id = ? AND system_id = ?",
        (probe_pattern_point_id, system_id),
    ).fetchone()
    if point_row is None:
        raise CellBindingNotFoundError("Probe pattern point not found")
    if point_row["status"] == "removed_from_production":
        raise CellBindingConflictError(
            "Probe pattern point has been removed from production and "
            "cannot back a new Cell Binding"
        )
    pattern_row = conn.execute(
        "SELECT * FROM probe_patterns WHERE id = ? AND system_id = ?",
        (point_row["pattern_id"], system_id),
    ).fetchone()
    if pattern_row is None:
        raise CellBindingNotFoundError("Probe pattern not found for this pattern point")
    if not pattern_row["source_snapshot_id"] or not pattern_row["source_commit_sha"]:
        raise CellBindingConflictError(
            "Probe pattern has no pinned source snapshot/commit"
        )
    return (
        point_row["component_id"], point_row["path"], point_row["symbol"],
        pattern_row["source_snapshot_id"], pattern_row["source_commit_sha"],
        None, pattern_row["id"],
    )


# ---------------------------------------------------------------------------
# evaluate_binding_drift
# ---------------------------------------------------------------------------


def evaluate_binding_drift(
    conn,
    *,
    system_id: int,
    binding_row,
    latest_ready_snapshot_id: Optional[int],
) -> Tuple[str, str]:
    """Purely structural drift check (Principle 6). Returns ``(status,
    status_reason)``; never mutates ``path``/``qualified_symbol`` and never
    re-targets a different symbol -- that is Issue #168's reconcile flow.
    """
    if latest_ready_snapshot_id is None:
        return (
            "review_required",
            "no ready snapshot available to verify this binding against",
        )
    symbol_row = conn.execute(
        """SELECT id FROM code_symbols
           WHERE snapshot_id = ? AND system_id = ? AND path = ? AND qualified_name = ?""",
        (
            latest_ready_snapshot_id, system_id,
            binding_row["path"], binding_row["qualified_symbol"],
        ),
    ).fetchone()
    if symbol_row is None:
        return ("review_required", "symbol not found at bound path in latest snapshot")
    if binding_row["snapshot_id"] == latest_ready_snapshot_id:
        return ("active", "")
    return ("stale", "snapshot advanced; symbol still present")


# ---------------------------------------------------------------------------
# build_cell_health
# ---------------------------------------------------------------------------


def _percentile(sorted_values: List[float], p: float) -> Optional[float]:
    """Deterministic linear-interpolation percentile (Principle 6). ``None``
    for an empty input -- never a guessed value."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(sorted_values[int(k)])
    lower = sorted_values[int(lo)] * (hi - k)
    upper = sorted_values[int(hi)] * (k - lo)
    return float(lower + upper)


def build_cell_health(
    conn,
    *,
    system_id: int,
    component_id: str,
    window_seconds: float = 3600.0,
) -> cell_fabric.CellHealth:
    """Deterministic aggregation from ``traces`` (heartbeat / queue / error
    rate / latency percentiles) and ``cell_activations`` (last activation)
    ONLY. Every stat that depends on at least one observed row is ``None``
    when there is nothing to observe -- ``queue_length`` is the one
    exception: it is a plain count of traces in the window, so it is a real
    fact (``0``) rather than an absent one even when the window is empty.
    """
    now = time.time()
    window_start = now - window_seconds

    heartbeat_row = conn.execute(
        "SELECT MAX(timestamp) AS latest FROM traces WHERE system_id = ? AND component_id = ?",
        (system_id, component_id),
    ).fetchone()
    heartbeat_at = heartbeat_row["latest"] if heartbeat_row else None

    last_activation_at: Optional[float] = None
    cell_row = conn.execute(
        "SELECT id FROM cell_definitions WHERE system_id = ? AND cell_id = ?",
        (system_id, component_id),
    ).fetchone()
    if cell_row is not None:
        activation_row = conn.execute(
            """SELECT MAX(created_at) AS latest FROM cell_activations
               WHERE system_id = ? AND cell_definition_id = ?""",
            (system_id, cell_row["id"]),
        ).fetchone()
        last_activation_at = activation_row["latest"] if activation_row else None

    window_rows = conn.execute(
        """SELECT error, duration_ms, timestamp FROM traces
           WHERE system_id = ? AND component_id = ? AND timestamp >= ?
           ORDER BY timestamp ASC""",
        (system_id, component_id, window_start),
    ).fetchall()

    queue_length = len(window_rows)
    if queue_length == 0:
        queue_oldest_age_seconds = None
        error_rate = None
        latency_p50_ms = None
        latency_p95_ms = None
    else:
        queue_oldest_age_seconds = now - window_rows[0]["timestamp"]
        error_count = sum(1 for r in window_rows if r["error"])
        error_rate = error_count / queue_length
        durations = sorted(
            r["duration_ms"] for r in window_rows if r["duration_ms"] is not None
        )
        latency_p50_ms = _percentile(durations, 0.50)
        latency_p95_ms = _percentile(durations, 0.95)

    return cell_fabric.CellHealth(
        heartbeat_at=heartbeat_at,
        last_activation_at=last_activation_at,
        queue_length=queue_length,
        queue_oldest_age_seconds=queue_oldest_age_seconds,
        error_rate=error_rate,
        latency_p50_ms=latency_p50_ms,
        latency_p95_ms=latency_p95_ms,
    )


# ---------------------------------------------------------------------------
# build_cell_state
# ---------------------------------------------------------------------------


def create_activation(
    conn,
    *,
    system_id: int,
    cell_definition_id: int,
    window_start: Optional[float] = None,
    window_end: Optional[float] = None,
    requested_by: Optional[str] = None,
):
    """Record an explicit Cell activation. ``trigger_kind`` is derived
    structurally (Principle 6) from window-bound presence: both bounds given
    -> ``aggregation_window``; otherwise -> ``explicit``. There is no LLM
    execution path in this sub-issue -- ``used_llm`` is always ``0`` and
    ``intelligence_run_id`` is always ``NULL``."""
    cell_row = conn.execute(
        "SELECT id FROM cell_definitions WHERE id = ? AND system_id = ?",
        (cell_definition_id, system_id),
    ).fetchone()
    if cell_row is None:
        raise CellBindingNotFoundError("Cell not found")
    if (window_start is None) != (window_end is None):
        raise CellBindingValidationError(
            "window_start and window_end must both be given or both omitted"
        )
    if window_start is not None and window_end is not None and window_end < window_start:
        raise CellBindingValidationError("window_end must not precede window_start")

    trigger_kind = (
        "aggregation_window"
        if window_start is not None and window_end is not None
        else "explicit"
    )
    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO cell_activations
                   (system_id, cell_definition_id, trigger_kind, window_start,
                    window_end, requested_by, used_llm, intelligence_run_id,
                    status, detail, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, NULL, 'recorded', '', ?, NULL)""",
            (
                system_id, cell_definition_id, trigger_kind, window_start,
                window_end, requested_by, now,
            ),
        )
        activation_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM cell_activations WHERE id = ?", (activation_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def build_cell_state(
    conn,
    *,
    system_id: int,
    cell_row,
    card_row,
    window_seconds: float = 3600.0,
) -> cell_fabric.CellStateContract:
    """Compose Sub 1's minimal ``cell_state`` with the health block filled
    in from real facts. The ``cell_state`` schema itself is unchanged --
    binding/activation info is returned as sibling fields by the API layer,
    never folded into this document (``CellStateContract`` still rejects
    unknown fields)."""
    roster = (
        json.loads(cell_row["roster_json"]) if cell_row["roster_json"] is not None else None
    )
    state = cell_fabric.build_minimal_cell_state(
        cell_id=cell_row["cell_id"],
        role_key=card_row["role_key"],
        role_version=card_row["version"],
        model_alias=card_row["model_alias"],
        mission=cell_row["mission"] or card_row["mission"],
        roster=roster,
    )
    health = build_cell_health(
        conn, system_id=system_id, component_id=cell_row["cell_id"],
        window_seconds=window_seconds,
    )
    quality = cell_quality.build_cell_quality_state(
        conn, system_id=system_id, cell_definition_id=cell_row["id"],
    )
    return state.model_copy(update={"health": health, "quality": quality})
