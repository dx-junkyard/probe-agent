"""Operations: monitoring, drift and the local reopen loop
(Issue #400, Phase 5 of the evolution control plane Epic #394).

ADR-5's separation is what this module exists to express. `established` (the
fixation decision is approved and a stable implementation is pinned) and
`monitoring` (that Node is ALSO actually being observed) fail independently:
telemetry stopping does not make the fixation decision wrong, and a Node whose
telemetry died must stay distinguishable from one under healthy observation.
Collapsing them is the #366 one-word-two-facts defect.

Four rules:

1. **Silence is never health.** `evaluate_observation_health` returns
   `unobserved` / `insufficient_sample` as their own states, never rolled into
   "within budget". A monitoring contract whose data stopped arriving is a
   problem to surface, not an absence to ignore (#400: 未観測を正常扱いしない).

2. **A deterministic fact and an interpretation of it are separate rows.**
   `node_drift_observation` records what was measured; `node_anomaly` records
   what someone (or a reasoning model) concluded from it. A reasoning
   classification that fails records `unknown` WITH the failure, and never
   falls back to a heuristic guess (Principle 6).

3. **A defect and a frame-breaking signal are different answers.** The
   taxonomy's whole point is that `implementation_defect` gets fixed while
   `new_use_case_signal` / `purpose_or_vision_reconsideration` mean the design
   was aimed at the wrong thing. Treating the second as the first is how a
   system optimises its way further from its purpose.

4. **Reopen is local, approved, and never unpins production.**
   `propose_reopen_scope` walks the Node graph deterministically and returns
   which Nodes it would touch AND which it deliberately excludes; approving it
   is a human decision (ADR-9), and the stable implementation stays pinned
   throughout -- that is what makes re-exploration safe to start at all.

probe-agent:
  role: Monitoring contract evaluation, deterministic drift facts, finite anomaly taxonomy, and deterministic local reopen scoping
  capability: evolution-control-plane
  element_type: core
  operation_kind: analysis
  consumers: [control-server-routes]
  state_effects: [database-read, database-write]
  probe_value: Verify unobserved and under-sampled readings are never reported as healthy, a failed reasoning classification records `unknown` rather than a heuristic guess, a repeated condition does not create a second reopen, an approved reopen keeps the stable implementation pinned, and unrelated Nodes are never pulled into scope.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, get_args

from . import evolution_node

__all__ = [
    "INDICATOR_KINDS",
    "OBSERVATION_STATES",
    "ANOMALY_CLASSIFICATIONS",
    "FRAME_BREAKING_CLASSIFICATIONS",
    "ANOMALY_SEVERITIES",
    "REOPEN_STATUSES",
    "OperationsError",
    "OperationsNotFoundError",
    "OperationsValidationError",
    "OperationsConflictError",
    "ObservationHealth",
    "ReopenScope",
    "evaluate_observation_health",
    "create_monitoring_contract",
    "record_observation",
    "record_anomaly",
    "propose_reopen_scope",
    "create_reopen_plan",
    "approve_reopen_plan",
    "build_operations_projection",
]


# ---------------------------------------------------------------------------
# Finite vocabularies (Principle 6)
# ---------------------------------------------------------------------------

IndicatorKind = Literal[
    "input_distribution", "output_quality", "error_rate", "latency", "cost",
    "flow_success", "outcome", "human_correction", "compatibility",
]
INDICATOR_KINDS: Tuple[str, ...] = get_args(IndicatorKind)

ObservationState = Literal[
    "within_budget", "drift_detected", "insufficient_sample", "unobserved"
]
OBSERVATION_STATES: Tuple[str, ...] = get_args(ObservationState)

AnomalyClassification = Literal[
    "implementation_defect",
    "input_or_environment_drift",
    "upstream_downstream_mismatch",
    "evaluation_gap",
    "new_use_case_signal",
    "purpose_or_vision_reconsideration",
    "unknown",
]
ANOMALY_CLASSIFICATIONS: Tuple[str, ...] = get_args(AnomalyClassification)

# The two classifications that mean "the design was aimed at the wrong
# thing", as opposed to "the implementation is wrong". They are named as a
# set because the next action differs completely: a defect goes to Phase 3
# exploration, a frame-breaking signal goes back to Phase 2's Design Studio
# and, beyond it, to the Purpose Chain. Optimising a Node harder is the wrong
# response to both of these.
FRAME_BREAKING_CLASSIFICATIONS: Tuple[str, ...] = (
    "new_use_case_signal",
    "purpose_or_vision_reconsideration",
)

AnomalySeverity = Literal["blocking", "attention", "informational"]
ANOMALY_SEVERITIES: Tuple[str, ...] = get_args(AnomalySeverity)

ReopenStatus = Literal["proposed", "approved", "rejected", "completed"]
REOPEN_STATUSES: Tuple[str, ...] = get_args(ReopenStatus)


class OperationsError(ValueError):
    """Base class for every failure this module raises."""


class OperationsNotFoundError(OperationsError):
    """A referenced row does not exist, or belongs to another System."""


class OperationsValidationError(OperationsError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class OperationsConflictError(OperationsError):
    """The row exists but is not in a state where this operation is legal."""


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise OperationsValidationError(
            f"{field_name} must be one of {', '.join(vocabulary)}; got {value!r}"
        )


def _json_or_default(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


# ---------------------------------------------------------------------------
# Observation health -- silence is never health
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationHealth:
    state: str
    reason: str
    elapsed_seconds: Optional[float]
    sample_count: Optional[int]


def evaluate_observation_health(
    *,
    last_observed_at: Optional[float],
    now: float,
    freshness_budget_seconds: float,
    sample_count: Optional[int],
    minimum_sample_count: int,
    drift_detected: bool = False,
) -> ObservationHealth:
    """Classify one indicator's observation into the finite four.

    Pure: elapsed time is computed from the `now` the caller supplies, so the
    result is reproducible and does not depend on when it happened to run.

    The order of these rows is the contract, and it is why `unobserved` comes
    first: a Node that has never reported cannot be "within budget", and a
    Node whose data stopped cannot be judged to have drifted or not -- there
    is nothing recent to judge. Reporting either as healthy is the failure
    #400 names as 未観測を正常扱いしない.
    """
    if last_observed_at is None:
        return ObservationHealth(
            "unobserved",
            "nothing has ever been observed for this indicator",
            None,
            sample_count,
        )

    elapsed = max(0.0, now - last_observed_at)
    if freshness_budget_seconds > 0 and elapsed > freshness_budget_seconds:
        return ObservationHealth(
            "unobserved",
            (
                f"the last observation is {elapsed:.0f}s old, beyond this "
                f"contract's {freshness_budget_seconds:.0f}s budget"
            ),
            elapsed,
            sample_count,
        )

    # Under-sampled comes BEFORE the drift verdict: too little data cannot
    # establish that something did NOT drift any more than that it did.
    if minimum_sample_count > 0:
        if sample_count is None:
            return ObservationHealth(
                "insufficient_sample",
                "the sample size is unknown",
                elapsed,
                None,
            )
        if sample_count < minimum_sample_count:
            return ObservationHealth(
                "insufficient_sample",
                (
                    f"{sample_count} samples, {minimum_sample_count} required by "
                    "this contract"
                ),
                elapsed,
                sample_count,
            )

    if drift_detected:
        return ObservationHealth("drift_detected", "", elapsed, sample_count)
    return ObservationHealth("within_budget", "", elapsed, sample_count)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _require_node(conn: sqlite3.Connection, system_id: int, node_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM evolution_node WHERE id = ? AND system_id = ?",
        (node_id, system_id),
    ).fetchone()
    if row is None:
        raise OperationsNotFoundError(f"Evolution Node {node_id} not found")
    return row


def create_monitoring_contract(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_id: int,
    observed_environment_ref: str = "",
    deployed_commit_sha: str = "",
    sampling_note: str = "",
    freshness_budget_seconds: float = 0.0,
    minimum_sample_count: int = 0,
    indicators: Sequence[Mapping[str, Any]] = (),
    reopen_conditions: Sequence[str] = (),
    escalation_owner: str = "",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Declare what watching this Node means, as a new version.

    The Node must already be `established` or `monitoring`: a monitoring
    contract for a Node still being explored would describe observation of
    something with no pinned implementation to observe.

    Every threshold is per contract. #399 refuses one global establishment
    threshold for the same reason this refuses one global freshness budget:
    a number invented centrally gets applied to Nodes nobody looked at.
    """
    node = _require_node(conn, system_id, node_id)
    if node["maturity"] not in ("established", "monitoring"):
        raise OperationsConflictError(
            f"Node {node_id} is {node['maturity']!r}; a monitoring contract "
            "describes an established Node's observation"
        )
    for indicator in indicators:
        kind = indicator.get("kind", "")
        _check_membership(kind, INDICATOR_KINDS, "indicator kind")

    now = time.time()
    conn.execute("BEGIN")
    try:
        previous = conn.execute(
            """SELECT * FROM node_monitoring_contract
                   WHERE node_id = ? AND system_id = ? AND superseded_by_id IS NULL
                   ORDER BY version_number DESC LIMIT 1""",
            (node_id, system_id),
        ).fetchone()
        version_number = (previous["version_number"] + 1) if previous else 1
        cur = conn.execute(
            """INSERT INTO node_monitoring_contract
                   (system_id, node_id, version_number, observed_environment_ref,
                    deployed_commit_sha, sampling_note, freshness_budget_seconds,
                    minimum_sample_count, indicators_json, reopen_conditions_json,
                    escalation_owner, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, node_id, version_number, observed_environment_ref,
                deployed_commit_sha, sampling_note, freshness_budget_seconds,
                minimum_sample_count, json.dumps(list(indicators)),
                json.dumps(list(reopen_conditions)), escalation_owner,
                created_by, now,
            ),
        )
        contract_id = cur.lastrowid
        if previous is not None:
            conn.execute(
                "UPDATE node_monitoring_contract SET superseded_by_id = ? WHERE id = ?",
                (contract_id, previous["id"]),
            )
        conn.execute(
            "UPDATE evolution_node SET monitoring_contract_ref = ?, updated_at = ? "
            "WHERE id = ?",
            (f"node_monitoring_contract:{contract_id}", now, node_id),
        )
        row = conn.execute(
            "SELECT * FROM node_monitoring_contract WHERE id = ?", (contract_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def record_observation(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    contract_id: int,
    indicator: str,
    indicator_kind: str,
    observation_state: str,
    observed_value: Optional[float] = None,
    reference_value: Optional[float] = None,
    sample_count: Optional[int] = None,
    window_seconds: Optional[float] = None,
    last_observed_at: Optional[float] = None,
    detail: str = "",
) -> sqlite3.Row:
    """Record ONE deterministic reading. No interpretation belongs here."""
    _check_membership(indicator_kind, INDICATOR_KINDS, "indicator_kind")
    _check_membership(observation_state, OBSERVATION_STATES, "observation_state")

    contract = conn.execute(
        "SELECT * FROM node_monitoring_contract WHERE id = ? AND system_id = ?",
        (contract_id, system_id),
    ).fetchone()
    if contract is None:
        raise OperationsNotFoundError(f"Monitoring contract {contract_id} not found")

    now = time.time()
    cur = conn.execute(
        """INSERT INTO node_drift_observation
               (system_id, node_id, contract_id, indicator, indicator_kind,
                observation_state, observed_value, reference_value, sample_count,
                window_seconds, last_observed_at, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, contract["node_id"], contract_id, indicator, indicator_kind,
            observation_state, observed_value, reference_value, sample_count,
            window_seconds, last_observed_at, detail, now,
        ),
    )
    return conn.execute(
        "SELECT * FROM node_drift_observation WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def record_anomaly(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_id: int,
    classification: str,
    summary: str = "",
    severity: str = "attention",
    contract_id: Optional[int] = None,
    observation_ids: Sequence[int] = (),
    decision_method: str = "deterministic",
    intelligence_run_id: Optional[int] = None,
    classification_error: str = "",
    dedupe_key: str = "",
) -> Tuple[sqlite3.Row, bool]:
    """Record an interpretation of one or more observations.

    Returns `(row, created)`. A repeat of a still-open `dedupe_key` returns
    the EXISTING anomaly with `created=False` and changes nothing: one
    continuing condition must not produce a new anomaly (and then a new
    reopen) on every polling cycle.

    `classification='unknown'` paired with a `classification_error` is the
    fail-closed outcome of a reasoning classification that did not succeed.
    It is a real, actionable state -- "the system saw something and could not
    say what" -- and it is never replaced by a heuristic guess (Principle 6),
    which is why a non-`unknown` classification may not carry an error.
    """
    _check_membership(classification, ANOMALY_CLASSIFICATIONS, "classification")
    _check_membership(severity, ANOMALY_SEVERITIES, "severity")
    _check_membership(
        decision_method, ("deterministic", "reasoning_llm", "manual"), "decision_method"
    )
    _require_node(conn, system_id, node_id)

    if classification_error and classification != "unknown":
        raise OperationsValidationError(
            "a classification error means the classification did not succeed; "
            "record it as 'unknown', never as a specific classification"
        )

    for observation_id in observation_ids:
        row = conn.execute(
            """SELECT id FROM node_drift_observation
                   WHERE id = ? AND system_id = ? AND node_id = ?""",
            (observation_id, system_id, node_id),
        ).fetchone()
        if row is None:
            raise OperationsNotFoundError(
                f"Drift observation {observation_id} does not belong to Node {node_id}"
            )

    if dedupe_key:
        existing = conn.execute(
            """SELECT * FROM node_anomaly
                   WHERE node_id = ? AND dedupe_key = ?
                     AND status IN ('open', 'acknowledged')""",
            (node_id, dedupe_key),
        ).fetchone()
        if existing is not None:
            return existing, False

    now = time.time()
    cur = conn.execute(
        """INSERT INTO node_anomaly
               (system_id, node_id, contract_id, classification, severity, summary,
                observation_ids_json, decision_method, intelligence_run_id,
                classification_error, dedupe_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, node_id, contract_id, classification, severity, summary,
            json.dumps(list(observation_ids)), decision_method, intelligence_run_id,
            classification_error, dedupe_key, now,
        ),
    )
    row = conn.execute(
        "SELECT * FROM node_anomaly WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return row, True


# ---------------------------------------------------------------------------
# Local reopen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReopenScope:
    origin_node_id: int
    scope_node_ids: Tuple[int, ...]
    rationale: Tuple[Dict[str, Any], ...]
    excluded_node_ids: Tuple[int, ...]


def propose_reopen_scope(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    origin_node_id: int,
    include_neighbours: bool = True,
) -> ReopenScope:
    """Deterministically propose which Nodes a reopen would touch.

    A neighbour is a Node that SHARES a concrete link target with the origin
    (the same Component, Probe Point, Capability, Flow, ...). That is a
    structural fact, not a similarity judgement: #394's Principle 6 forbids
    keyword scores or embeddings deciding this, and "these two Nodes are
    bound to the same Component" is exactly the kind of finite, checkable
    relation it does allow.

    Every other Node in the System is returned in `excluded_node_ids`, not
    silently omitted. #400 requires that unrelated Nodes are not reopened, and
    the only way a reader can check that is to see what was deliberately left
    out and why.
    """
    _require_node(conn, system_id, origin_node_id)

    origin_links = conn.execute(
        """SELECT link_kind, target_ref FROM evolution_node_link
               WHERE node_id = ? AND system_id = ? AND superseded_by_id IS NULL""",
        (origin_node_id, system_id),
    ).fetchall()
    origin_targets = {(row["link_kind"], row["target_ref"]) for row in origin_links}

    scope = [origin_node_id]
    rationale: List[Dict[str, Any]] = [
        {"node_id": origin_node_id, "reason": "origin", "shared": []}
    ]

    all_nodes = conn.execute(
        "SELECT id FROM evolution_node WHERE system_id = ? AND id != ? ORDER BY id",
        (system_id, origin_node_id),
    ).fetchall()

    excluded: List[int] = []
    for node_row in all_nodes:
        candidate_id = node_row["id"]
        shared: List[str] = []
        if include_neighbours and origin_targets:
            candidate_links = conn.execute(
                """SELECT link_kind, target_ref FROM evolution_node_link
                       WHERE node_id = ? AND system_id = ? AND superseded_by_id IS NULL""",
                (candidate_id, system_id),
            ).fetchall()
            for link in candidate_links:
                key = (link["link_kind"], link["target_ref"])
                if key in origin_targets:
                    shared.append(f"{link['link_kind']}:{link['target_ref']}")
        if shared:
            scope.append(candidate_id)
            rationale.append(
                {
                    "node_id": candidate_id,
                    "reason": "shares a linked target with the origin",
                    "shared": sorted(set(shared)),
                }
            )
        else:
            excluded.append(candidate_id)

    return ReopenScope(
        origin_node_id=origin_node_id,
        scope_node_ids=tuple(scope),
        rationale=tuple(rationale),
        excluded_node_ids=tuple(excluded),
    )


def create_reopen_plan(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    origin_node_id: int,
    reason: str,
    anomaly_id: Optional[int] = None,
    scope: Optional[ReopenScope] = None,
    budget_note: str = "",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Record a proposed reopen. Changes no Node's maturity.

    Proposing is not reopening: the maturity transition happens only at
    approval, through Phase 1's evaluator, with a named human (ADR-9).
    """
    if not (reason or "").strip():
        raise OperationsValidationError(
            "a reopen must state why; an unexplained reopen cannot be reviewed"
        )
    _require_node(conn, system_id, origin_node_id)
    if anomaly_id is not None:
        row = conn.execute(
            """SELECT id FROM node_anomaly
                   WHERE id = ? AND system_id = ? AND node_id = ?""",
            (anomaly_id, system_id, origin_node_id),
        ).fetchone()
        if row is None:
            raise OperationsNotFoundError(
                f"Anomaly {anomaly_id} does not belong to Node {origin_node_id}"
            )

    resolved = scope or propose_reopen_scope(
        conn, system_id=system_id, origin_node_id=origin_node_id
    )
    now = time.time()
    cur = conn.execute(
        """INSERT INTO node_reopen_plan
               (system_id, origin_node_id, anomaly_id, scope_node_ids_json,
                scope_rationale_json, excluded_node_ids_json, reason, budget_note,
                created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, origin_node_id, anomaly_id,
            json.dumps(list(resolved.scope_node_ids)),
            json.dumps(list(resolved.rationale)),
            json.dumps(list(resolved.excluded_node_ids)),
            reason, budget_note, created_by, now,
        ),
    )
    return conn.execute(
        "SELECT * FROM node_reopen_plan WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def approve_reopen_plan(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    plan_id: int,
    approved_by: str,
    note: str = "",
) -> Tuple[sqlite3.Row, List[Dict[str, Any]]]:
    """Approve a reopen and transition every in-scope Node to `reopened`.

    Two properties this must preserve:

    - **The stable implementation stays pinned.** Phase 1's `reopened` row
      neither requires nor clears it, and nothing here touches it. Production
      keeps running the established implementation while exploration
      proceeds -- that is what makes starting a re-exploration safe at all
      (ADR-5).
    - **A Node that cannot legally transition is reported, not forced.** A
      `suspended` Node in scope is skipped with its refusal code rather than
      dragged into `reopened` by a bulk operation, because a bulk action that
      silently ignores a gate is a gate that does not exist.
    """
    if not (approved_by or "").strip():
        raise OperationsValidationError(
            "approve_reopen_plan requires the approving person; reopening is "
            "never an automatic decision (ADR-9)"
        )
    plan = conn.execute(
        "SELECT * FROM node_reopen_plan WHERE id = ? AND system_id = ?",
        (plan_id, system_id),
    ).fetchone()
    if plan is None:
        raise OperationsNotFoundError(f"Reopen plan {plan_id} not found")
    if plan["status"] != "proposed":
        raise OperationsConflictError(
            f"Reopen plan {plan_id} is already {plan['status']}"
        )

    results: List[Dict[str, Any]] = []
    for node_id in _json_or_default(plan["scope_node_ids_json"], []):
        transition = evolution_node.apply_transition(
            conn,
            system_id=system_id,
            node_id=node_id,
            to_state="reopened",
            decision_method="manual",
            actor=approved_by,
            actor_kind="developer",
            reason=note or plan["reason"],
            reason_code="local_reopen",
            idempotency_key=f"reopen-plan-{plan_id}-node-{node_id}",
        )
        results.append(
            {
                "node_id": node_id,
                "applied": transition.applied,
                "duplicate": transition.duplicate,
                "reason_code": transition.decision.reason_code,
                "message": transition.decision.message,
            }
        )

    conn.execute(
        """UPDATE node_reopen_plan
               SET status = 'approved', approved_by = ?, approved_at = ?,
                   decision_note = ?
               WHERE id = ?""",
        (approved_by, time.time(), note, plan_id),
    )
    row = conn.execute(
        "SELECT * FROM node_reopen_plan WHERE id = ?", (plan_id,)
    ).fetchone()
    return row, results


def build_operations_projection(
    conn: sqlite3.Connection, *, system_id: int, node_id: int, now: Optional[float] = None
) -> Dict[str, Any]:
    """One Node's operational picture.

    `maturity` and `observation` are reported as two separate readings, never
    merged (ADR-5). An `established` Node whose telemetry stopped shows
    `maturity: established` alongside `observation: unobserved` -- and that
    combination is precisely the state Phase 5 exists to make visible.
    """
    node = _require_node(conn, system_id, node_id)
    clock = time.time() if now is None else now

    contract = conn.execute(
        """SELECT * FROM node_monitoring_contract
               WHERE node_id = ? AND system_id = ? AND superseded_by_id IS NULL
               ORDER BY version_number DESC LIMIT 1""",
        (node_id, system_id),
    ).fetchone()

    observations: List[Dict[str, Any]] = []
    health: Optional[Dict[str, Any]] = None
    if contract is not None:
        rows = conn.execute(
            """SELECT * FROM node_drift_observation
                   WHERE contract_id = ? ORDER BY id DESC LIMIT 50""",
            (contract["id"],),
        ).fetchall()
        observations = [
            {
                "id": row["id"],
                "indicator": row["indicator"],
                "indicator_kind": row["indicator_kind"],
                "observation_state": row["observation_state"],
                "observed_value": row["observed_value"],
                "reference_value": row["reference_value"],
                "sample_count": row["sample_count"],
                "last_observed_at": row["last_observed_at"],
                "detail": row["detail"],
            }
            for row in rows
        ]
        newest = rows[0] if rows else None
        evaluated = evaluate_observation_health(
            last_observed_at=newest["last_observed_at"] if newest else None,
            now=clock,
            freshness_budget_seconds=contract["freshness_budget_seconds"],
            sample_count=newest["sample_count"] if newest else None,
            minimum_sample_count=contract["minimum_sample_count"],
            drift_detected=bool(
                newest and newest["observation_state"] == "drift_detected"
            ),
        )
        health = {
            "state": evaluated.state,
            "reason": evaluated.reason,
            "elapsed_seconds": evaluated.elapsed_seconds,
            "sample_count": evaluated.sample_count,
        }

    anomaly_rows = conn.execute(
        """SELECT * FROM node_anomaly
               WHERE node_id = ? AND system_id = ? AND status IN ('open', 'acknowledged')
               ORDER BY id DESC""",
        (node_id, system_id),
    ).fetchall()

    return {
        "system_id": system_id,
        "node_id": node_id,
        "node_key": node["node_key"],
        # Two independent readings (ADR-5). An established Node with dead
        # telemetry reads as exactly that.
        "maturity": node["maturity"],
        "observation": health,
        "monitoring_contract_id": contract["id"] if contract is not None else None,
        # None means "no contract is wired", never "monitoring failed".
        "monitoring_contract_declared": contract is not None,
        "observations": observations,
        "anomalies": [
            {
                "id": row["id"],
                "classification": row["classification"],
                "severity": row["severity"],
                "summary": row["summary"],
                "status": row["status"],
                "decision_method": row["decision_method"],
                "classification_error": row["classification_error"],
                # The distinction that decides the next action: a defect goes
                # to exploration, a frame-breaking signal goes back to design.
                "frame_breaking": row["classification"] in FRAME_BREAKING_CLASSIFICATIONS,
                "created_at": row["created_at"],
            }
            for row in anomaly_rows
        ],
    }
