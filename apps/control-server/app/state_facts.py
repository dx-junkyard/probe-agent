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
    "classify_connectivity_freshness",
    "get_freshness_thresholds",
    "FRESHNESS_VALUES",
    "DEFAULT_DELAYED_AFTER_SECONDS",
    "DEFAULT_STALE_AFTER_SECONDS",
    "CLOCK_SKEW_TOLERANCE_SECONDS",
    "count_approved_probe_plans",
    "count_undecided_completed_experiments",
    "plan_has_validated_patch",
    "count_proposed_probe_plans",
    "count_approved_probe_plans_without_validated_patch",
    "has_applied_probe_patch",
    "has_decided_experiment",
    "has_completed_replay_variant_run",
    "has_succeeded_publish_job",
    "get_system_profile_row",
    "get_latest_completed_capability_hierarchy_run",
    "load_ai_purpose_view",
    "get_latest_purpose_confirmation",
    "get_latest_completed_understanding_build",
    "purpose_confirmation_staleness",
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
    run = get_latest_completed_capability_hierarchy_run(conn, system_id, snapshot_id)
    node = None
    if run is not None:
        node = conn.execute(
            "SELECT name, summary FROM capability_hierarchy_nodes "
            "WHERE system_id = ? AND snapshot_id = ? AND intelligence_run_id = ? "
            "AND node_type = 'purpose' ORDER BY id LIMIT 1",
            (system_id, snapshot_id, run["id"]),
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
    run = get_latest_completed_capability_hierarchy_run(conn, system_id, snapshot_id)
    if run is None:
        return 0
    return conn.execute(
        "SELECT COUNT(*) FROM capability_hierarchy_nodes "
        "WHERE system_id = ? AND snapshot_id = ? AND intelligence_run_id = ? "
        "AND node_type = 'capability'",
        (system_id, snapshot_id, run["id"]),
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


# --- freshness (Issue #370) ---------------------------------------------------
#
# "Ever connected" and "still receiving" are different questions, and the audit
# found the UI answering the second with the first: a system whose last trace
# arrived 14 days ago still showed a green 受信中. Lifecycle milestones are
# cumulative and never expire; freshness is a statement about *now* and does.
# The two are kept as separate axes with separate vocabularies so no caller can
# accidentally substitute one for the other.

FRESHNESS_NEVER_RECEIVED = "never_received"
FRESHNESS_RECEIVING_NOW = "receiving_now"
FRESHNESS_DELAYED = "delayed"
FRESHNESS_STALE = "stale"
FRESHNESS_VALUES = (
    FRESHNESS_NEVER_RECEIVED,
    FRESHNESS_RECEIVING_NOW,
    FRESHNESS_DELAYED,
    FRESHNESS_STALE,
)

#: Defaults, in seconds. Deliberately generous: probe-agent cannot know a
#: system's expected traffic rate, so the defaults only distinguish "clearly
#: live" from "clearly not", and a System can narrow them (see
#: ``get_freshness_thresholds``). Issue #370 explicitly rejects forcing one
#: expected reception frequency on every system.
DEFAULT_DELAYED_AFTER_SECONDS = 15 * 60
DEFAULT_STALE_AFTER_SECONDS = 24 * 3600

#: A trace timestamped slightly ahead of the server is a clock difference, not
#: a future event. Beyond this the skew is reported so the operator can see
#: that the freshness reading rests on a disagreeing clock.
CLOCK_SKEW_TOLERANCE_SECONDS = 120


def classify_connectivity_freshness(
    *,
    last_trace_at: Optional[float],
    now: float,
    delayed_after_seconds: float = DEFAULT_DELAYED_AFTER_SECONDS,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> str:
    """Deterministic 4-way freshness classification (finite set, Principle 6).

    Boundaries are inclusive-at-the-threshold: an age exactly equal to
    ``delayed_after_seconds`` is already ``delayed``, so the transition is
    testable at a single point rather than spread across a gap.

    A negative age (the trace's clock is ahead of the server's) is treated as
    ``receiving_now``: it means data arrived, and a clock difference must not
    be reported as staleness. The skew itself is surfaced separately.
    """
    if last_trace_at is None:
        return FRESHNESS_NEVER_RECEIVED
    age = now - last_trace_at
    if age >= stale_after_seconds:
        return FRESHNESS_STALE
    if age >= delayed_after_seconds:
        return FRESHNESS_DELAYED
    return FRESHNESS_RECEIVING_NOW


def get_freshness_thresholds(conn, system_id: int) -> tuple:
    """Return ``(delayed_after_seconds, stale_after_seconds)`` for one System.

    Falls back to the documented defaults when the System has not set its own,
    so the thresholds are always explicit and always readable by the UI.
    """
    row = conn.execute(
        """SELECT delayed_after_seconds, stale_after_seconds
             FROM connectivity_freshness_policy WHERE system_id = ?""",
        (system_id,),
    ).fetchone()
    if row is None:
        return DEFAULT_DELAYED_AFTER_SECONDS, DEFAULT_STALE_AFTER_SECONDS
    return row["delayed_after_seconds"], row["stale_after_seconds"]


@dataclass(frozen=True)
class ConnectivityFacts:
    total_trace_count: int
    smoke_trace_count: int
    real_trace_count: int
    first_trace_at: Optional[float]
    last_trace_at: Optional[float]
    last_trace_component_id: Optional[str]
    materialized_session_ids: List[int]
    # Issue #370: windowed counts, so "is it still running" is answered by
    # observed arrivals in a bounded recent period rather than by a cumulative
    # total that can never decrease.
    real_trace_count_5m: int = 0
    real_trace_count_1h: int = 0
    real_trace_count_24h: int = 0
    #: Positive when the newest trace is timestamped ahead of the server.
    clock_skew_seconds: float = 0.0
    #: Newest NON-smoke trace. A manual smoke check proves the transport
    #: works; it says nothing about whether the instrumented workload is
    #: still running, so workload freshness is measured from this and never
    #: from ``last_trace_at``.
    last_real_trace_at: Optional[float] = None


def get_connectivity_facts(
    conn,
    system_id: int,
    smoke_component_id: str,
    now: Optional[float] = None,
) -> ConnectivityFacts:
    """Trace-reception counts backing the SDK connectivity status endpoint.

    ``now`` is injected rather than read inside so freshness boundaries are
    testable at an exact instant.
    """
    if now is None:
        now = time.time()
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

    # Windowed counts exclude smoke traces: a smoke check proves the transport
    # works, not that the instrumented workload is running.
    windows = conn.execute(
        """
        SELECT
            SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) AS w5m,
            SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) AS w1h,
            SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) AS w24h,
            MAX(timestamp) AS last_real_at
        FROM traces
        WHERE system_id = ? AND component_id != ?
        """,
        (now - 300, now - 3600, now - 86400, system_id, smoke_component_id),
    ).fetchone()

    last_at = counts["last_at"]
    clock_skew = max(0.0, (last_at - now)) if last_at is not None else 0.0

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
        last_trace_at=last_at,
        last_trace_component_id=last_component,
        materialized_session_ids=[r["id"] for r in materialized_rows],
        real_trace_count_5m=windows["w5m"] or 0,
        real_trace_count_1h=windows["w1h"] or 0,
        real_trace_count_24h=windows["w24h"] or 0,
        clock_skew_seconds=clock_skew,
        last_real_trace_at=windows["last_real_at"],
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


# --- Instrumentation / observation / evaluation / publish milestone facts
# (Issue #256) ---------------------------------------------------------------
#
# These back the four new phases derive_user_phase's PHASE_ORDER gained past
# "preparation" (instrumentation / observation / evaluation / publish). Each
# is a plain existence check against an already-finite persisted status
# column -- no new table, no new decision, matching Principle 6 and the
# "projection from existing tables only" constraint in Issue #256.


def has_applied_probe_patch(conn, system_id: int) -> bool:
    """Has any ``probe_patches`` row for this system reached
    ``apply_status = 'applied'``.

    ``apply_status`` is a finite column (default ``'not_applied'``; see
    ``db.py``'s ``probe_patches`` table) written to ``'applied'`` only by the
    explicit, commit-sha-confirmed patch-apply endpoints
    (``routes/probe_patterns.py`` / ``routes/project_intelligence.py``, both
    ``UPDATE probe_patches SET apply_status = 'applied', ...``) after a
    successful apply against a clean working tree. ``probe_patches`` already
    carries ``system_id`` directly (no join through ``probe_plans`` needed).
    """
    row = conn.execute(
        "SELECT id FROM probe_patches WHERE system_id = ? AND apply_status = 'applied' LIMIT 1",
        (system_id,),
    ).fetchone()
    return row is not None


def has_decided_experiment(conn, system_id: int) -> bool:
    """Has any ``experiments`` row for this system recorded a human decision.

    ``human_decision`` defaults to ``'undecided'`` (see ``db.py``'s
    ``experiments`` table) until a human records ``adopted`` / ``rejected`` /
    ``needs_more_data``; this checks the finite "anything other than the
    default" condition regardless of the experiment's own ``status``.
    """
    row = conn.execute(
        "SELECT id FROM experiments WHERE system_id = ? AND human_decision != 'undecided' LIMIT 1",
        (system_id,),
    ).fetchone()
    return row is not None


def has_completed_replay_variant_run(conn, system_id: int) -> bool:
    """Has any non-baseline ``replay_variants`` row for this system reached
    ``status = 'completed'``.

    ``replay_variants.status`` is a finite ``'running' | 'completed' |
    'failed'`` column (``db.py``); ``routes/replay.py``'s
    ``POST /replay-variant-runs`` sets a patched variant's row to
    ``'completed'`` once its harness execution and case classification
    finish. ``is_baseline = 0`` excludes the baseline row every variant run
    also writes (``variant_key = 'baseline'``) -- the evaluation milestone is
    "a candidate was actually replayed and evaluated", not merely "the
    baseline replay succeeded".
    """
    row = conn.execute(
        """SELECT id FROM replay_variants
           WHERE system_id = ? AND status = 'completed' AND is_baseline = 0
           LIMIT 1""",
        (system_id,),
    ).fetchone()
    return row is not None


def get_system_profile_row(conn, system_id: int):
    """Raw ``system_profile`` row for one system, or ``None`` if never set.

    Backs the manual ``purpose_views`` entry on ``GET
    /repository/system-understanding`` (Issue #94/#275) and the purpose-
    confirmation create/staleness flow -- both read the same human-entered
    ``PUT /system-profile`` record ``routes/evaluation.py`` writes.
    """
    return conn.execute(
        "SELECT * FROM system_profile WHERE system_id = ?", (system_id,)
    ).fetchone()


def get_latest_completed_capability_hierarchy_run(
    conn, system_id: int, snapshot_id: int
):
    """Latest usable capability hierarchy run for one exact System/snapshot."""
    return conn.execute(
        "SELECT * FROM intelligence_runs "
        "WHERE system_id = ? AND snapshot_id = ? "
        "AND run_type = 'capability_hierarchy' AND status = 'completed' "
        "ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()


def load_ai_purpose_view(conn, system_id: int, snapshot_id: int) -> Optional[Dict[str, Any]]:
    """The AI/source-derived purpose view for one snapshot (Issue #94/#275).

    Reads the purpose node from this System/snapshot's latest *completed*
    capability-hierarchy run, falling back to the latest
    ``system_profile_drafts`` row when that run has no purpose. Newer failed
    runs and older completed-run nodes are ignored. The added ``source`` key
    (``"capability_hierarchy"`` | ``"system_profile_draft"``) so callers can
    tell the two apart without re-deriving it from ``provenance_kind`` (which
    can independently be ``"structural"`` on either source).
    ``_load_purpose`` delegates to this and drops the ``source`` key to keep
    its own public return shape unchanged.
    """
    run = get_latest_completed_capability_hierarchy_run(conn, system_id, snapshot_id)
    node = None
    if run is not None:
        node = conn.execute(
            "SELECT * FROM capability_hierarchy_nodes "
            "WHERE system_id = ? AND snapshot_id = ? AND intelligence_run_id = ? "
            "AND node_type = 'purpose' ORDER BY id LIMIT 1",
            (system_id, snapshot_id, run["id"]),
        ).fetchone()
    if node:
        return {
            "source": "capability_hierarchy",
            "name": node["name"],
            "summary": node["summary"],
            "provenance_kind": node["provenance_kind"],
        }
    draft = conn.execute(
        "SELECT * FROM system_profile_drafts "
        "WHERE system_id = ? AND snapshot_id = ? ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()
    if draft:
        return {
            "source": "system_profile_draft",
            "name": draft["name"],
            "summary": draft["purpose"],
            "provenance_kind": "structural",
        }
    return None


def get_latest_purpose_confirmation(conn, system_id: int):
    """Most recent ``system_purpose_confirmations`` row for one system, or
    ``None``. The table is append-only; the latest row is the current
    confirmation (Issue #94/#275)."""
    return conn.execute(
        "SELECT * FROM system_purpose_confirmations WHERE system_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()


def get_latest_completed_understanding_build(conn, system_id: int, snapshot_id: int):
    """Latest completed System Understanding build for this exact System/snapshot."""
    return conn.execute(
        "SELECT * FROM system_understanding_builds "
        "WHERE system_id = ? AND snapshot_id = ? AND status = 'completed' "
        "ORDER BY id DESC LIMIT 1",
        (system_id, snapshot_id),
    ).fetchone()


def purpose_confirmation_staleness(
    conn, system_id: int, current_ready_snapshot_id: Optional[int]
) -> Optional[str]:
    """Staleness reason for the *latest* purpose confirmation, or ``None`` if
    it is still valid (or there is no confirmation to evaluate).

    Pure structural equality only (Principle 6), checked in this fixed order:

    1. the confirmation's pinned ``snapshot_id`` no longer matches the
       current latest ready snapshot id -> ``"snapshot_changed"``;
    2. else the confirmation's stored ``manual_purpose`` no longer matches
       the current ``system_profile.purpose`` text -> ``"profile_updated"``;
    3. else the confirmation is legacy (no build id), or its pinned completed
       build is no longer the latest completed build -> ``"ai_updated"``;
    4. else the confirmation's stored AI name/summary no longer match the
       current AI purpose view's name/summary -> ``"ai_updated"``;
    5. else valid (``None``).
    """
    confirmation = get_latest_purpose_confirmation(conn, system_id)
    if confirmation is None:
        return None

    if confirmation["snapshot_id"] != current_ready_snapshot_id:
        return "snapshot_changed"

    profile = get_system_profile_row(conn, system_id)
    current_purpose = (profile["purpose"] or "") if profile is not None else ""
    if (confirmation["manual_purpose"] or "") != current_purpose:
        return "profile_updated"

    current_build = get_latest_completed_understanding_build(
        conn, system_id, current_ready_snapshot_id
    )
    pinned_build_id = confirmation["understanding_build_id"]
    if (
        pinned_build_id is None
        or current_build is None
        or pinned_build_id != current_build["id"]
    ):
        return "ai_updated"

    ai_view = load_ai_purpose_view(conn, system_id, current_ready_snapshot_id)
    ai_name = ai_view["name"] if ai_view else None
    ai_summary = ai_view["summary"] if ai_view else None
    if confirmation["ai_purpose_name"] != ai_name or confirmation["ai_purpose_summary"] != ai_summary:
        return "ai_updated"

    return None


def has_succeeded_publish_job(conn, system_id: int) -> bool:
    """Has any ``publish_jobs`` row for this system reached ``status =
    'completed'`` -- the terminal-success value in ``publish_job.py``'s
    status vocabulary (``pending`` -> ... -> ``pushing`` -> ``creating_pr``
    -> ``completed``; see ``_set_status(job_id, "completed", ...)`` at the
    end of ``_run_publish_phase`` / ``_run_reconcile_phase``). Distinct from
    the terminal failure states (``failed`` / ``cancelled``) and from the
    retryable states (``retryable_failed`` / ``manual_intervention_required``).
    """
    row = conn.execute(
        "SELECT id FROM publish_jobs WHERE system_id = ? AND status = 'completed' LIMIT 1",
        (system_id,),
    ).fetchone()
    return row is not None
