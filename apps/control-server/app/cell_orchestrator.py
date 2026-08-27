"""Probe Cell Fabric: 領域オーケストレーター / domain orchestrators
(Issue #301, Sub 4 of the Probe Cell Fabric epic, Issue #297).

This module owns:

- Orchestrator guardrails (``validate_roster``): roster size <= 7 (span of
  control 5+/-2 upper bound), every roster member must already exist as a
  ``cell_definitions`` row in the same System, a cell cannot roster itself,
  no roster cycle (direct or transitive), and orchestrator nesting depth
  capped at 3 (an orchestrator containing an orchestrator containing an
  orchestrator is depth 3; one more level is rejected). Roster configuration
  is static: only ``create_cell`` (Sub 1, guarded here too) and the explicit
  ``PUT /cell-fabric/cells/{cell_id}/roster`` endpoint may change a roster.
- ``compute_orchestrator_depth``: the read-side depth of an EXISTING cell,
  walking roster edges downward (a worker cell is depth 0; an orchestrator
  with an empty roster is depth 1).
- ``build_orchestrator_digest``: a fully deterministic (Principle 6, no LLM
  anywhere on this path) aggregation over an orchestrator's direct-roster
  members: role card / status / current binding status / health per child,
  task-status counts per child and in total (a child orchestrator's count
  recursively rolls up its own subtree, bounded by the depth limit), open
  escalation counts by severity, the ``blocked_by`` graph for the roster's
  blocked tasks, and bottleneck candidates drawn from a small finite rule
  set (queue_depth / stuck_task / blocked_chain / retry_churn). Every
  candidate carries its supporting facts verbatim -- never a free-text
  judgement.
- ``run_triage``: the ONE reasoning-model boundary in this module. Sends the
  deterministic digest's facts to the LLM to classify the situation as
  individual / systemic / upstream / inconclusive, following the same
  audited ``intelligence_runs`` pattern as ``app/probe_planner.py`` /
  ``app/pattern_reconciler.py`` (run_type=``cell_triage``,
  decision_method=``reasoning_llm``). Fail-closed: on ANY failure (missing
  reasoning-model config, LLM error, invalid JSON, an ``affected_cell_ids``
  entry outside the roster) the run is persisted ``failed`` with
  ``error_details`` and NO ``cell_triage_results`` row is written -- the
  deterministic digest is still returned by the caller. The triage's
  ``proposed_ask`` is never auto-confirmed or auto-forwarded; nothing in this
  module writes to ``cell_tasks``/``cell_reports``.

probe-agent:
  role: Orchestrator guardrails, deterministic roster/task/bottleneck digest, and reasoning triage
  capability: probe-cell-fabric
  element_type: core
  consumers: [control-server-routes]
  operation_kind: mixed
  state_effects: [database-read, database-write, external-api]
  probe_value: Verify roster guardrails (size/existence/self-roster/cycle/depth) reject fail-closed, the digest is 100% deterministic with every bottleneck candidate carrying verbatim facts, a worker Cell referenced from two orchestrators' rosters contributes its tasks to both without owner duplication, and triage failure persists no heuristic classification while the digest still returns.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from . import cell_binding, cell_quality
from .cell_fabric import TASK_STATUSES
from .db import get_conn
from .llm import (
    LLMConfig,
    LLMError,
    LLMResourceLimitError,
    create_llm_client,
    is_reasoning_model,
)

MAX_ROSTER_SIZE = 7
MAX_ORCHESTRATOR_DEPTH = 3

CELL_TRIAGE_PROMPT_VERSION = "v1"
CELL_TRIAGE_SCHEMA_VERSION = "v1"

TRIAGE_CLASSIFICATIONS: Tuple[str, ...] = (
    "individual", "systemic", "upstream", "inconclusive",
)


class CellOrchestratorError(Exception):
    """Base class for orchestrator errors."""


class NotFoundError(CellOrchestratorError):
    """The referenced Cell does not exist in this System -- maps to 404."""


class ValidationFailedError(CellOrchestratorError):
    """A guardrail (size/existence/self-roster/cycle/depth) rejected the
    roster, or the triage response failed structural/fail-closed validation
    -- maps to 422."""


# ---------------------------------------------------------------------------
# Shared row lookups (all take an already-open `conn` -- db.get_conn()'s lock
# is non-reentrant, never open a nested connection inside these).
# ---------------------------------------------------------------------------


def _get_cell_row_by_id(conn, system_id: int, row_id: int):
    return conn.execute(
        "SELECT * FROM cell_definitions WHERE id = ? AND system_id = ?",
        (row_id, system_id),
    ).fetchone()


def _get_cell_row_by_cell_id(conn, system_id: int, cell_id: str):
    return conn.execute(
        "SELECT * FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
        (cell_id, system_id),
    ).fetchone()


# ---------------------------------------------------------------------------
# Guardrails: roster size / existence / self-roster / cycle / depth
# ---------------------------------------------------------------------------


def _would_create_cycle(conn, system_id: int, cell_id: str, roster: List[str]) -> bool:
    """Deterministic cycle check (Principle 6): would setting ``cell_id``'s
    roster to ``roster`` make ``cell_id`` reachable from itself by walking
    roster edges downward? Covers both direct self-roster (``cell_id`` is in
    ``roster``) and an indirect cycle (A rosters B, B rosters A)."""
    stack = list(roster)
    seen: set = set()
    while stack:
        current = stack.pop()
        if current == cell_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        row = _get_cell_row_by_cell_id(conn, system_id, current)
        if row is None or row["roster_json"] is None:
            continue
        stack.extend(json.loads(row["roster_json"]))
    return False


def _cell_row_depth(conn, system_id: int, cell_row, _visited: Optional[set] = None) -> int:
    """Depth of an EXISTING, already-persisted cell (0 for a worker; 1 for an
    orchestrator with an empty roster; 1 + max(child depth) otherwise).
    Guards against a persisted cycle (should never happen -- write-time
    guardrails reject it -- but a read must never infinite-loop)."""
    if cell_row["roster_json"] is None:
        return 0
    roster = json.loads(cell_row["roster_json"])
    if not roster:
        return 1
    visited = set(_visited or ())
    if cell_row["id"] in visited:
        return MAX_ORCHESTRATOR_DEPTH + 1
    visited.add(cell_row["id"])
    child_depths = []
    for child_id in roster:
        child_row = _get_cell_row_by_cell_id(conn, system_id, child_id)
        if child_row is None:
            continue
        child_depths.append(_cell_row_depth(conn, system_id, child_row, visited))
    return 1 + (max(child_depths) if child_depths else 0)


def compute_orchestrator_depth(conn, system_id: int, cell_definition_id: int) -> int:
    """Depth of an existing cell, looked up by its ``cell_definitions.id``."""
    row = _get_cell_row_by_id(conn, system_id, cell_definition_id)
    if row is None:
        raise NotFoundError("Cell not found")
    return _cell_row_depth(conn, system_id, row)


def _proposed_depth(conn, system_id: int, roster: List[str]) -> int:
    """Depth a NOT-YET-PERSISTED orchestrator would have with ``roster``."""
    if not roster:
        return 1
    child_depths = []
    for child_id in roster:
        child_row = _get_cell_row_by_cell_id(conn, system_id, child_id)
        if child_row is None:
            continue  # existence is validated separately
        child_depths.append(_cell_row_depth(conn, system_id, child_row))
    return 1 + (max(child_depths) if child_depths else 0)


def _max_ancestor_height(conn, system_id: int, cell_id: str, _visited=None) -> int:
    """Longest chain of orchestrators ABOVE ``cell_id`` (0 if no cell rosters
    it). Used so a roster UPDATE on a nested child cannot push an ancestor
    past MAX_ORCHESTRATOR_DEPTH -- the pre-existing downward-only check missed
    that (a grandchild roster edit could otherwise create an A->B->C->D chain
    of depth 4)."""
    visited = set(_visited or ())
    if cell_id in visited:
        return MAX_ORCHESTRATOR_DEPTH + 1  # defensive: a persisted cycle
    visited.add(cell_id)
    parents = conn.execute(
        "SELECT cell_id, roster_json FROM cell_definitions "
        "WHERE system_id = ? AND roster_json IS NOT NULL",
        (system_id,),
    ).fetchall()
    best = 0
    for parent in parents:
        roster = json.loads(parent["roster_json"] or "[]")
        if cell_id in roster:
            best = max(best, 1 + _max_ancestor_height(
                conn, system_id, parent["cell_id"], visited,
            ))
    return best


def validate_roster(
    conn, *, system_id: int, cell_id: str, roster: Optional[List[str]],
) -> None:
    """Validate a (create or update) roster for ``cell_id``. ``roster=None``
    means the cell is a worker -- nothing to validate. Raises
    :class:`ValidationFailedError` fail-closed on any guardrail violation;
    never silently truncates or drops offending entries."""
    if roster is None:
        return
    if len(roster) > MAX_ROSTER_SIZE:
        raise ValidationFailedError(
            f"roster size {len(roster)} exceeds the span-of-control limit "
            f"of {MAX_ROSTER_SIZE}"
        )
    if len(roster) != len(set(roster)):
        raise ValidationFailedError("roster cell_ids must be distinct")
    if cell_id in roster:
        raise ValidationFailedError(f"Cell {cell_id!r} cannot roster itself")

    missing = [c for c in roster if _get_cell_row_by_cell_id(conn, system_id, c) is None]
    if missing:
        raise ValidationFailedError(
            "roster references cells that do not exist in this System: "
            f"{sorted(missing)}"
        )

    if _would_create_cycle(conn, system_id, cell_id, roster):
        raise ValidationFailedError(
            f"roster would create a cycle involving {cell_id!r}"
        )

    # Total chain length = ancestors above this cell + this cell and its
    # proposed descendants. The ancestor term is what makes a roster UPDATE on
    # an already-nested child safe (a create has no ancestors yet, so this
    # reduces to the old downward-only check).
    ancestor_height = _max_ancestor_height(conn, system_id, cell_id)
    depth = ancestor_height + _proposed_depth(conn, system_id, roster)
    if depth > MAX_ORCHESTRATOR_DEPTH:
        raise ValidationFailedError(
            f"orchestrator nesting depth {depth} exceeds the limit of "
            f"{MAX_ORCHESTRATOR_DEPTH}"
        )


# ---------------------------------------------------------------------------
# Deterministic digest
# ---------------------------------------------------------------------------


def _task_counts_for_owner(conn, system_id: int, owner_row_id: int) -> Dict[str, int]:
    rows = conn.execute(
        """SELECT status, COUNT(*) AS n FROM cell_tasks
           WHERE system_id = ? AND owner_cell_id = ? GROUP BY status""",
        (system_id, owner_row_id),
    ).fetchall()
    counts = {s: 0 for s in TASK_STATUSES}
    for r in rows:
        counts[r["status"]] = r["n"]
    return counts


def _rollup_task_counts(
    conn, system_id: int, cell_row, depth_remaining: int, _visited: Optional[set] = None,
) -> Dict[str, int]:
    """Task-status counts for ``cell_row``'s own subtree: its directly-owned
    tasks plus (recursively, bounded by ``depth_remaining``) its roster's
    rollup counts. A worker cell's rollup is just its own owned-task
    counts."""
    counts = _task_counts_for_owner(conn, system_id, cell_row["id"])
    if cell_row["roster_json"] is None or depth_remaining <= 0:
        return counts
    visited = set(_visited or ())
    if cell_row["id"] in visited:
        return counts
    visited.add(cell_row["id"])
    for grandchild_id in json.loads(cell_row["roster_json"]):
        grandchild_row = _get_cell_row_by_cell_id(conn, system_id, grandchild_id)
        if grandchild_row is None:
            continue
        sub_counts = _rollup_task_counts(
            conn, system_id, grandchild_row, depth_remaining - 1, visited,
        )
        for status_name, n in sub_counts.items():
            counts[status_name] += n
    return counts


def _median(values: List[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    ordered = sorted(values)
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _collect_roster_tasks(conn, system_id: int, roster: List[str]):
    """All tasks owned DIRECTLY by a roster member (no recursion) -- this is
    the scope bottleneck detection reads, matching the multi-context rule
    "tasks are read by owner only, never double-counted": each task has
    exactly one ``owner_cell_id``, so the same worker appearing in two
    different orchestrators' rosters simply means two independent digest
    calls each read that owner's tasks once."""
    tasks = []
    for child_cell_id in roster:
        child_row = _get_cell_row_by_cell_id(conn, system_id, child_cell_id)
        if child_row is None:
            continue
        rows = conn.execute(
            "SELECT * FROM cell_tasks WHERE system_id = ? AND owner_cell_id = ?",
            (system_id, child_row["id"]),
        ).fetchall()
        for r in rows:
            tasks.append((child_cell_id, r))
    return tasks


def _longest_chain(task_id: int, edges_by_id: Dict[int, List[int]], visited: set) -> List[int]:
    if task_id in visited:
        return [task_id]  # defensive cycle break; write-time paths never create one
    visited = visited | {task_id}
    blockers = edges_by_id.get(task_id, [])
    best: List[int] = []
    for blocker_id in blockers:
        chain = _longest_chain(blocker_id, edges_by_id, visited)
        if len(chain) > len(best):
            best = chain
    return [task_id] + best


def _compute_bottleneck_candidates(
    conn, *, system_id: int, roster: List[str], roster_summary: List[Dict[str, Any]],
    blocked_by_graph: List[Dict[str, Any]], now: float, window_seconds: float,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []

    # queue_depth: the roster member with the max health.queue_length, fired
    # only when that max is > 0 AND >= 2x the roster median queue_length.
    queue_lengths = [
        (entry["cell_id"], entry["health"]["queue_length"])
        for entry in roster_summary
        if entry.get("health") and entry["health"].get("queue_length") is not None
    ]
    if queue_lengths:
        median = _median([q for _, q in queue_lengths])
        max_cell_id, max_queue = max(queue_lengths, key=lambda t: t[1])
        if max_queue > 0 and max_queue >= 2 * median:
            candidates.append({
                "kind": "queue_depth",
                "cell_id": max_cell_id,
                "task_id": None,
                "facts": {"queue_length": max_queue, "roster_median_queue_length": median},
                "related_task_ids": [],
            })

    roster_tasks = _collect_roster_tasks(conn, system_id, roster)

    # stuck_task: WIP (doing/review) whose updated_at is older than the window.
    for cell_id, task_row in roster_tasks:
        if task_row["status"] in ("doing", "review"):
            age_seconds = now - task_row["updated_at"]
            if age_seconds > window_seconds:
                candidates.append({
                    "kind": "stuck_task",
                    "cell_id": cell_id,
                    "task_id": task_row["id"],
                    "facts": {
                        "status": task_row["status"],
                        "age_seconds": age_seconds,
                        "updated_at": task_row["updated_at"],
                    },
                    "related_task_ids": [task_row["id"]],
                })

    # retry_churn: tasks that have already retried at least twice.
    for cell_id, task_row in roster_tasks:
        if task_row["retry_count"] >= 2:
            candidates.append({
                "kind": "retry_churn",
                "cell_id": cell_id,
                "task_id": task_row["id"],
                "facts": {"retry_count": task_row["retry_count"]},
                "related_task_ids": [task_row["id"]],
            })

    # blocked_chain: the longest blocked_by chain (critical path) among the
    # roster's blocked tasks, when its length is >= 2.
    edges_by_id = {e["task_id"]: e["blocked_by_task_ids"] for e in blocked_by_graph}
    best_chain: List[int] = []
    for task_id in sorted(edges_by_id):
        chain = _longest_chain(task_id, edges_by_id, set())
        if len(chain) > len(best_chain):
            best_chain = chain
    if len(best_chain) >= 2:
        candidates.append({
            "kind": "blocked_chain",
            "cell_id": None,
            "task_id": best_chain[0],
            "facts": {"chain_task_ids": best_chain, "length": len(best_chain)},
            "related_task_ids": best_chain,
        })

    return candidates


def build_orchestrator_digest(
    conn, *, system_id: int, cell_row, window_seconds: float = 3600.0,
) -> Dict[str, Any]:
    """Deterministic aggregation over ``cell_row``'s direct roster (Principle
    6, no LLM call anywhere on this path). ``cell_row`` must be an
    orchestrator (``roster_json`` not null)."""
    if cell_row["roster_json"] is None:
        raise ValidationFailedError("Cell is not an orchestrator (roster is null)")
    roster: List[str] = json.loads(cell_row["roster_json"])
    now = time.time()

    roster_summary: List[Dict[str, Any]] = []
    tasks_by_cell: Dict[str, Dict[str, int]] = {}
    total_counts = {s: 0 for s in TASK_STATUSES}
    escalations_by_severity = {"sev1": 0, "sev2": 0, "sev3": 0}

    for child_cell_id in roster:
        child_row = _get_cell_row_by_cell_id(conn, system_id, child_cell_id)
        if child_row is None:
            continue  # defensive only; existence is enforced at write time

        role_card_row = conn.execute(
            "SELECT role_key, version FROM agent_role_cards WHERE id = ?",
            (child_row["role_card_id"],),
        ).fetchone()
        binding_row = conn.execute(
            """SELECT status, feature_refs_json, capability_refs_json,
                      entrypoint_refs_json
               FROM cell_bindings
               WHERE system_id = ? AND cell_definition_id = ? AND status != 'superseded'
               ORDER BY version DESC LIMIT 1""",
            (system_id, child_row["id"]),
        ).fetchone()
        is_worker = child_row["roster_json"] is None

        health = None
        if is_worker:
            health = cell_binding.build_cell_health(
                conn, system_id=system_id, component_id=child_cell_id,
                window_seconds=window_seconds,
            ).model_dump()
        quality = cell_quality.build_cell_quality_state(
            conn, system_id=system_id, cell_definition_id=child_row["id"],
        )
        topology = {
            "feature_refs": (
                json.loads(binding_row["feature_refs_json"] or "[]")
                if binding_row is not None else []
            ),
            "capability_refs": (
                json.loads(binding_row["capability_refs_json"] or "[]")
                if binding_row is not None else []
            ),
            "entrypoint_refs": (
                json.loads(binding_row["entrypoint_refs_json"] or "[]")
                if binding_row is not None else []
            ),
        }

        counts = (
            _task_counts_for_owner(conn, system_id, child_row["id"])
            if is_worker
            else _rollup_task_counts(conn, system_id, child_row, MAX_ORCHESTRATOR_DEPTH)
        )
        tasks_by_cell[child_cell_id] = counts
        for status_name, n in counts.items():
            total_counts[status_name] += n

        escalation_rows = conn.execute(
            """SELECT severity, COUNT(*) AS n FROM cell_escalations
               WHERE system_id = ? AND cell_definition_id = ? AND status = 'open'
               GROUP BY severity""",
            (system_id, child_row["id"]),
        ).fetchall()
        for r in escalation_rows:
            escalations_by_severity[r["severity"]] = (
                escalations_by_severity.get(r["severity"], 0) + r["n"]
            )

        roster_summary.append({
            "cell_id": child_cell_id,
            "status": child_row["status"],
            "role_card": {
                "role_key": role_card_row["role_key"] if role_card_row else "",
                "version": role_card_row["version"] if role_card_row else "",
            },
            "binding_status": binding_row["status"] if binding_row else None,
            "health": health,
            "quality": quality.model_dump() if quality is not None else None,
            "topology": topology,
        })

    blocked_by_graph: List[Dict[str, Any]] = []
    for child_cell_id in roster:
        child_row = _get_cell_row_by_cell_id(conn, system_id, child_cell_id)
        if child_row is None:
            continue
        blocked_rows = conn.execute(
            """SELECT id, blocked_by_json FROM cell_tasks
               WHERE system_id = ? AND owner_cell_id = ? AND status = 'blocked'""",
            (system_id, child_row["id"]),
        ).fetchall()
        for r in blocked_rows:
            blocked_by_graph.append({
                "task_id": r["id"],
                "blocked_by_task_ids": json.loads(r["blocked_by_json"] or "[]"),
            })

    bottleneck_candidates = _compute_bottleneck_candidates(
        conn, system_id=system_id, roster=roster, roster_summary=roster_summary,
        blocked_by_graph=blocked_by_graph, now=now, window_seconds=window_seconds,
    )

    return {
        "cell_id": cell_row["cell_id"],
        "generated_at": now,
        "window_seconds": window_seconds,
        "roster": roster_summary,
        "tasks": {"by_cell": tasks_by_cell, "total": total_counts},
        "escalations": {"open_by_severity": escalations_by_severity},
        "blocked_graph": blocked_by_graph,
        "bottleneck_candidates": bottleneck_candidates,
    }


# ---------------------------------------------------------------------------
# Reasoning triage (systemic vs individual vs upstream vs inconclusive)
# ---------------------------------------------------------------------------


_TRIAGE_SYSTEM_PROMPT = """\
You are triaging deterministic operational facts collected from a Probe Cell \
orchestrator's roster (task-status counts, health, blocked-task chains, \
retry counts, open escalations). Decide whether the situation reflects an \
individual Cell's problem, a systemic problem across the roster, an upstream \
problem outside this orchestrator's control, or is inconclusive given the \
facts. Never invent facts not present in the digest."""

_TRIAGE_PROMPT_TEMPLATE = """\
CELL_TRIAGE_RESPONSE_JSON

Orchestrator: {cell_id}
Roster cell_ids: {roster_cell_ids}

Deterministic fact digest (JSON):
{digest_json}

Classify the situation and respond with ONLY valid JSON:
{{
  "classification": "individual" | "systemic" | "upstream" | "inconclusive",
  "reasoning_summary": "string grounded only in the digest facts above",
  "affected_cell_ids": ["subset of the roster cell_ids listed above"],
  "proposed_ask": "a suggested question or action for a human -- this is a suggestion only, never auto-executed"
}}"""


def _parse_triage_response(raw_json: str, roster_cell_ids: List[str]):
    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise ValidationFailedError("Triage response must be a JSON object")

    classification = str(data.get("classification", "")).strip()
    if classification not in TRIAGE_CLASSIFICATIONS:
        raise ValidationFailedError(f"Unknown triage classification: {classification!r}")

    reasoning_summary = str(data.get("reasoning_summary", "")).strip()
    if not reasoning_summary:
        raise ValidationFailedError("reasoning_summary is required")

    affected = data.get("affected_cell_ids", [])
    if not isinstance(affected, list):
        raise ValidationFailedError("affected_cell_ids must be an array")
    affected_ids = [str(a) for a in affected]
    roster_set = set(roster_cell_ids)
    out_of_set = [a for a in affected_ids if a not in roster_set]
    if out_of_set:
        # Fail-closed on out-of-roster ids -- never silently drop them and
        # accept the rest.
        raise ValidationFailedError(
            f"affected_cell_ids contains ids outside the roster: {out_of_set}"
        )

    proposed_ask = str(data.get("proposed_ask", "")).strip()
    return classification, reasoning_summary, affected_ids, proposed_ask


def run_triage(
    *, system_id: int, cell_row, window_seconds: float = 3600.0,
):
    """Build the deterministic digest, then classify it with the reasoning
    model. Returns ``(digest, triage_row_or_None, is_mock, intelligence_run_id,
    error_or_None)``. On any failure the ``intelligence_runs`` row is
    persisted ``status='failed'`` with ``error_details`` set and NO
    ``cell_triage_results`` row is written; the digest itself is still
    returned so the caller can surface facts even when triage failed.

    Runs in three phases and manages its own connections: the digest read,
    then the LLM call with NO connection held (the quota wrapper around
    ``generate_text`` opens its own ``get_conn()``, and the DB lock is
    non-reentrant -- holding a connection across the call would deadlock),
    then the persistence write. Callers must NOT hold an open ``get_conn()``
    when calling this function.
    """
    with get_conn() as conn:
        digest = build_orchestrator_digest(
            conn, system_id=system_id, cell_row=cell_row,
            window_seconds=window_seconds,
        )
    roster_cell_ids = [entry["cell_id"] for entry in digest["roster"]]

    llm_config = LLMConfig.intelligence_from_env()
    started_at = time.time()
    error: Optional[str] = None
    is_mock = llm_config.provider == "mock"
    classification = reasoning_summary = proposed_ask = ""
    affected_ids: List[str] = []

    try:
        if llm_config.provider != "mock" and not is_reasoning_model(
            llm_config.provider, llm_config.model
        ):
            raise LLMError("Cell triage requires a configured reasoning model")
        llm_client = create_llm_client(llm_config)
        prompt = _TRIAGE_PROMPT_TEMPLATE.format(
            cell_id=cell_row["cell_id"],
            roster_cell_ids=", ".join(roster_cell_ids) or "(empty roster)",
            digest_json=json.dumps(digest, sort_keys=True, default=str),
        )
        raw = llm_client.generate_text(
            [
                {"role": "system", "content": _TRIAGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:] if lines[0].startswith("```") else lines
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)
        classification, reasoning_summary, affected_ids, proposed_ask = _parse_triage_response(
            cleaned, roster_cell_ids,
        )
    except (LLMError, LLMResourceLimitError, ValidationFailedError, json.JSONDecodeError) as exc:
        # LLM quota / resource-limit / system-context errors are NOT
        # subclasses of LLMError, so without catching them here a quota breach
        # would escape the fail-closed path -- surfacing a 429 with no failed
        # intelligence_run recorded and no deterministic digest returned.
        error = str(exc)
    completed_at = time.time()

    run_status = "completed" if error is None else "failed"
    now = time.time()
    with get_conn() as conn:
        _persist = _run_triage_persist  # keep the transaction below readable
        return _persist(
            conn,
            system_id=system_id,
            cell_row=cell_row,
            digest=digest,
            llm_config=llm_config,
            run_status=run_status,
            error=error,
            is_mock=is_mock,
            started_at=started_at,
            completed_at=completed_at,
            classification=classification,
            reasoning_summary=reasoning_summary,
            affected_ids=affected_ids,
            proposed_ask=proposed_ask,
            now=now,
        )


def _run_triage_persist(
    conn,
    *,
    system_id: int,
    cell_row,
    digest,
    llm_config,
    run_status: str,
    error: Optional[str],
    is_mock: bool,
    started_at: float,
    completed_at: float,
    classification: str,
    reasoning_summary: str,
    affected_ids: List[str],
    proposed_ask: str,
    now: float,
):
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model,
                    prompt_version, schema_version, decision_method,
                    status, error_details, is_mock, started_at, completed_at)
               VALUES (?, NULL, 'cell_triage', ?, ?, ?, ?, 'reasoning_llm',
                       ?, ?, ?, ?, ?)""",
            (
                system_id, llm_config.provider, llm_config.model,
                CELL_TRIAGE_PROMPT_VERSION, CELL_TRIAGE_SCHEMA_VERSION,
                run_status, error, 1 if is_mock else 0, started_at, completed_at,
            ),
        )
        run_id = cur.lastrowid

        triage_row = None
        if run_status == "completed":
            cur = conn.execute(
                """INSERT INTO cell_triage_results
                       (system_id, cell_definition_id, intelligence_run_id,
                        digest_json, classification, reasoning_summary,
                        affected_cell_ids_json, proposed_ask, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, cell_row["id"], run_id, json.dumps(digest, default=str),
                    classification, reasoning_summary,
                    json.dumps(affected_ids), proposed_ask, now,
                ),
            )
            triage_row_id = cur.lastrowid
            triage_row = conn.execute(
                "SELECT * FROM cell_triage_results WHERE id = ?", (triage_row_id,),
            ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return digest, triage_row, is_mock, run_id, error
