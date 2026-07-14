"""Shared deterministic state-fact retrieval layer (Issue #236).

Before this module, "what state is this system's understanding/pipeline in"
facts were fetched independently by ``system_state.py``,
``system_understanding_service.py``, and ``routes/connectivity.py`` --
sometimes with literally duplicated SQL against the same tables. This module
is the single home for those raw, System-scoped facts: repository
configuration, HEAD / working tree state, ready snapshot lookup, pipeline
step status rows, the Purpose/Capabilities base facts that
``system_state.evaluate_understanding`` classifies, build-job
running/stuck detection, and SDK connectivity counts + classification.

Every function here is a pure fact getter: it reads persisted rows (or, for
``resolve_repository_head_state``, the pinned repository's HEAD/working tree
via ``git_ops``) and returns them or a small typed projection -- it never
constructs a ``StateItem`` / ``PipelineStep`` / API response, and it never
calls a reasoning model (Principle 6). Interpretation stays with the caller
(``system_state.py``, ``system_understanding_service.py``,
``routes/connectivity.py``), matching the existing "pure function + caller
reads the DB" convention (``system_understanding_service._derive_stage_statuses``
is pure; ``get_system_understanding`` prepares the queries).

This is a behavior-preserving extraction (Issue #236): every function here
reproduces an existing query byte-for-byte (or, where two call sites issued
the same query with different projected columns, a strict superset of the
columns previously selected), so moving a call site to this module changes
no response shape and no decision.

probe-agent:
  role: Shared deterministic state-fact retrieval layer
  capability: system-state-assessment
  element_type: core
  consumers: [control-server]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify repository/snapshot/pipeline/purpose/capability/connectivity facts are read once per concept and reused identically by system_state, system_understanding_service, and the connectivity route instead of being requeried with subtly different SQL per surface.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .git_ops import GitError, resolve_head, working_tree_status

__all__ = [
    "GitError",
    "get_repository_config",
    "has_repository_configured",
    "RepositoryHeadState",
    "resolve_repository_head_state",
    "get_latest_snapshot",
    "get_latest_ready_snapshot",
    "get_latest_ready_snapshot_id",
    "get_latest_intelligence_run",
    "get_latest_build_step",
    "has_code_entrypoints",
    "has_understanding_graph_snapshot",
    "has_code_symbols",
    "purpose_defined_in_snapshot",
    "capability_count_in_snapshot",
    "stuck_after_seconds",
    "is_active_build_row",
    "get_active_build",
    "ConnectivityFacts",
    "get_connectivity_facts",
    "classify_connectivity_state",
    "count_approved_probe_plans",
    "count_undecided_completed_experiments",
    "plan_has_validated_patch",
    "count_proposed_probe_plans",
    "count_approved_probe_plans_without_validated_patch",
]


# --- Repository configuration -------------------------------------------------


def get_repository_config(conn, system_id: int):
    """Raw ``repository_configs`` row for one system, or ``None`` if unset.

    Callers apply their own truthiness rule on ``repo_path`` (an existing,
    pre-#236 divergence between call sites is preserved deliberately: a row
    with an empty ``repo_path`` is "configured" for one caller and "missing"
    for another -- this function only supplies the row).
    """
    return conn.execute(
        "SELECT repo_path FROM repository_configs WHERE system_id = ?", (system_id,)
    ).fetchone()


def has_repository_configured(conn, system_id: int) -> bool:
    """Existence-only check: does a ``repository_configs`` row exist at all."""
    return get_repository_config(conn, system_id) is not None


@dataclass(frozen=True)
class RepositoryHeadState:
    current_head: str
    working_tree_clean: bool
    working_tree_dirty_file_count: int
    working_tree_dirty_sample: List[str]


def resolve_repository_head_state(repo_path: str) -> RepositoryHeadState:
    """Resolve HEAD sha and working tree cleanliness for a configured repo path.

    Raises ``GitError`` exactly like the underlying ``git_ops`` calls; the
    caller decides how to represent a failure (Principle 6: no heuristic
    fallback).
    """
    current_head = resolve_head(repo_path)
    working_tree = working_tree_status(repo_path)
    return RepositoryHeadState(
        current_head=current_head,
        working_tree_clean=working_tree.clean,
        working_tree_dirty_file_count=working_tree.dirty_file_count,
        working_tree_dirty_sample=working_tree.sample,
    )


# --- Snapshots -----------------------------------------------------------------


def get_latest_snapshot(conn, system_id: int):
    """Most recent ``repository_snapshots`` row for a system, any status."""
    return conn.execute(
        "SELECT id, status, commit_sha FROM repository_snapshots WHERE system_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()


def get_latest_ready_snapshot(conn, system_id: int):
    """Most recent ``status = 'ready'`` ``repository_snapshots`` row (full row)."""
    return conn.execute(
        "SELECT * FROM repository_snapshots WHERE system_id = ? AND status = 'ready' "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()


def get_latest_ready_snapshot_id(conn, system_id: int) -> Optional[int]:
    row = get_latest_ready_snapshot(conn, system_id)
    return row["id"] if row else None


# --- Pipeline step raw facts ----------------------------------------------------


def get_latest_intelligence_run(
    conn, system_id: int, snapshot_id: int, run_types: Sequence[str]
):
    """Most recent ``intelligence_runs`` row whose ``run_type`` is in ``run_types``."""
    placeholders = ",".join("?" for _ in run_types)
    return conn.execute(
        f"SELECT id, status FROM intelligence_runs "
        f"WHERE system_id = ? AND snapshot_id = ? AND run_type IN ({placeholders}) "
        f"ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id, *run_types),
    ).fetchone()


def get_latest_build_step(conn, system_id: int, snapshot_id: int, step: str):
    """Most recent ``system_understanding_build_steps`` row for one step."""
    return conn.execute(
        """SELECT id, status, error, artifact_provenance
           FROM system_understanding_build_steps
           WHERE system_id = ? AND snapshot_id = ? AND step = ?
           ORDER BY id DESC LIMIT 1""",
        (system_id, snapshot_id, step),
    ).fetchone()


def has_code_entrypoints(conn, system_id: int, snapshot_id: int) -> bool:
    row = conn.execute(
        "SELECT id FROM code_entrypoints WHERE system_id = ? AND snapshot_id = ? LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    return row is not None


def has_understanding_graph_snapshot(conn, system_id: int, snapshot_id: int) -> bool:
    row = conn.execute(
        "SELECT id FROM understanding_graph_snapshots WHERE system_id = ? AND snapshot_id = ? LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    return row is not None


def has_code_symbols(conn, system_id: int, snapshot_id: int) -> bool:
    row = conn.execute(
        "SELECT id FROM code_symbols WHERE system_id = ? AND snapshot_id = ? LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    return row is not None


# --- Purpose / Capabilities base facts ------------------------------------------
#
# These are the single-snapshot structural facts that
# ``system_state.evaluate_understanding`` classifies into its 5-way
# satisfied_current / baseline_reusable / diff_impacted / unconfirmed /
# missing_baseline branch. evaluate_understanding stays the canonical
# orchestrator in system_state.py (per the System State Assessment skill
# section); only the raw fact-gathering lives here.


def purpose_defined_in_snapshot(conn, system_id: int, snapshot_id: int) -> bool:
    """Is System Purpose structurally present for exactly this snapshot.

    True when a ``capability_hierarchy_nodes`` purpose node with a non-empty
    name/summary exists for this snapshot, or (absent that) a
    ``system_profile_drafts`` row with a non-empty name/purpose exists for
    this snapshot. Does not consider any other snapshot's baseline.
    """
    node = conn.execute(
        "SELECT name, summary FROM capability_hierarchy_nodes "
        "WHERE system_id = ? AND snapshot_id = ? AND node_type = 'purpose' LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    draft = conn.execute(
        "SELECT name, purpose FROM system_profile_drafts "
        "WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    return (node is not None and bool(node["name"] or node["summary"])) or (
        draft is not None and bool(draft["name"] or draft["purpose"])
    )


def capability_count_in_snapshot(conn, system_id: int, snapshot_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM capability_hierarchy_nodes "
        "WHERE system_id = ? AND snapshot_id = ? AND node_type = 'capability'",
        (system_id, snapshot_id),
    ).fetchone()[0]


# --- Build job running / stuck detection ----------------------------------------


def stuck_after_seconds() -> float:
    try:
        return float(os.getenv("SYSTEM_UNDERSTANDING_STUCK_AFTER_SECONDS", "300"))
    except ValueError:
        return 300.0


def is_active_build_row(row) -> bool:
    """True when a ``system_understanding_builds`` row is queued/running and
    has heartbeated recently enough not to be considered stuck."""
    if row is None or row["status"] not in ("queued", "running"):
        return False
    last = row["heartbeat_at"] or row["started_at"] or row["created_at"]
    return last is not None and (time.time() - last) <= stuck_after_seconds()


def get_active_build(conn, system_id: int, snapshot_id: int):
    """The most recent genuinely-active (not stuck) build for a snapshot, if any."""
    rows = conn.execute(
        """SELECT id, status, current_step, heartbeat_at, started_at, created_at
             FROM system_understanding_builds
            WHERE system_id = ? AND snapshot_id = ? AND status IN ('queued', 'running')
            ORDER BY id DESC""",
        (system_id, snapshot_id),
    ).fetchall()
    for row in rows:
        if is_active_build_row(row):
            return row
    return None


# --- SDK connectivity ------------------------------------------------------------


@dataclass(frozen=True)
class ConnectivityFacts:
    total_trace_count: int
    smoke_trace_count: int
    real_trace_count: int
    first_trace_at: Optional[float]
    last_trace_at: Optional[float]
    last_trace_component_id: Optional[str]
    materialized_session_ids: List[int]


def get_connectivity_facts(conn, system_id: int, smoke_component_id: str) -> ConnectivityFacts:
    """Trace-reception counts backing the SDK connectivity status endpoint."""
    counts = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN component_id = ? THEN 1 ELSE 0 END) AS smoke,
            MIN(timestamp) AS first_at,
            MAX(timestamp) AS last_at
        FROM traces
        WHERE system_id = ?
        """,
        (smoke_component_id, system_id),
    ).fetchone()

    total = counts["total"] or 0
    smoke = counts["smoke"] or 0
    real = total - smoke

    last_component = None
    if total > 0:
        last_row = conn.execute(
            """
            SELECT component_id FROM traces
            WHERE system_id = ?
            ORDER BY timestamp DESC, rowid DESC
            LIMIT 1
            """,
            (system_id,),
        ).fetchone()
        last_component = last_row["component_id"] if last_row else None

    materialized_rows = conn.execute(
        """
        SELECT id FROM interview_session
        WHERE system_id = ?
          AND materialization_diff IS NOT NULL
          AND materialization_diff != ''
        ORDER BY id
        """,
        (system_id,),
    ).fetchall()

    return ConnectivityFacts(
        total_trace_count=total,
        smoke_trace_count=smoke,
        real_trace_count=real,
        first_trace_at=counts["first_at"],
        last_trace_at=counts["last_at"],
        last_trace_component_id=last_component,
        materialized_session_ids=[r["id"] for r in materialized_rows],
    )


def classify_connectivity_state(*, real_trace_count: int, smoke_trace_count: int) -> str:
    """Deterministic 3-way SDK connectivity classification (Issue #165)."""
    if real_trace_count > 0:
        return "receiving"
    if smoke_trace_count > 0:
        return "smoke_only"
    return "no_signal"


# --- Probe plan / experiment review facts (Issue #237) -------------------------
#
# These back the "instrumentation path established" preparation-phase
# completion signal in system_state.derive_user_phase: an approved probe
# plan is one of the two ways preparation can be considered done (the other
# being non-no_signal SDK connectivity, via classify_connectivity_state
# above). Status values match the finite sets in models.py
# (``ProbePlanStatus = proposed | approved | rejected``,
# ``ExperimentStatus = draft | running | completed | failed``, and
# ``human_decision`` defaulting to ``undecided`` until a human records
# adopted/rejected/needs_more_data) -- the same condition
# ``system_understanding_service._load_pending_plan_action_ids`` /
# ``_load_undecided_completed_experiment_ids`` already use for the
# Instrument/Evaluate stage counts, reproduced here byte-for-byte so both
# call sites agree on what "approved" / "undecided" mean.


def count_approved_probe_plans(conn, system_id: int) -> int:
    """Count of ``probe_plans`` rows with ``status = 'approved'`` for one system."""
    return conn.execute(
        "SELECT COUNT(*) FROM probe_plans WHERE system_id = ? AND status = 'approved'",
        (system_id,),
    ).fetchone()[0]


def count_undecided_completed_experiments(conn, system_id: int) -> int:
    """Count of completed experiments with no recorded human decision yet."""
    return conn.execute(
        """SELECT COUNT(*) FROM experiments
           WHERE system_id = ? AND status = 'completed' AND human_decision = 'undecided'""",
        (system_id,),
    ).fetchone()[0]


# --- Probe plan review/patch facts (Issue #238) ---------------------------
#
# These back the two probe-plan-shaped "next step" StateItems
# (proposal.probe_plans.proposed / proposal.probe_plans.approved_without_patch)
# that consolidate the now-removed (Issue #239)
# system_understanding_service._build_next_actions' "Review probe plan" /
# "Generate / validate probe patch" NextAction sources into system_state.py.
# plan_has_validated_patch is moved here (behavior-
# preserving) from system_understanding_service._plan_has_validated_patch so
# both surfaces share one query instead of keeping independent copies.


def plan_has_validated_patch(conn, plan_id: int) -> bool:
    """A plan's patch is validated when its latest baseline and probed
    validation runs both succeeded -- the same finite condition the patch
    apply endpoint gates on (Principle 6).
    """
    patch_rows = conn.execute(
        "SELECT id FROM probe_patches WHERE plan_id = ? AND status != 'failed'",
        (plan_id,),
    ).fetchall()
    for patch in patch_rows:
        val_rows = conn.execute(
            """SELECT variant, overall_success FROM validation_runs
               WHERE patch_id = ? ORDER BY id DESC""",
            (patch["id"],),
        ).fetchall()
        latest: Dict[str, bool] = {}
        for vr in val_rows:
            latest.setdefault(vr["variant"], bool(vr["overall_success"]))
        if latest.get("baseline") is True and latest.get("probed") is True:
            return True
    return False


def count_proposed_probe_plans(conn, system_id: int) -> int:
    """Count of ``probe_plans`` rows with ``status = 'proposed'`` for one system."""
    return conn.execute(
        "SELECT COUNT(*) FROM probe_plans WHERE system_id = ? AND status = 'proposed'",
        (system_id,),
    ).fetchone()[0]


def count_approved_probe_plans_without_validated_patch(conn, system_id: int) -> int:
    """Count of approved probe plans whose latest patch has not passed both
    baseline and probed validation -- the same condition
    ``system_understanding_service._load_pending_plan_action_ids`` uses to
    build its ``approved_plan_ids_without_validated_patch`` NextAction list,
    reproduced here as a count via the shared ``plan_has_validated_patch``
    so system_state.py's StateItem agrees without a second traversal.
    """
    rows = conn.execute(
        "SELECT id FROM probe_plans WHERE system_id = ? AND status = 'approved'",
        (system_id,),
    ).fetchall()
    return sum(1 for r in rows if not plan_has_validated_patch(conn, r["id"]))
