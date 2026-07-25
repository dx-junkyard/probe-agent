"""Probe Cell Fabric: Goal/Task ledger + delegate/report/escalate protocol
(Issue #300, Sub 3 of the Probe Cell Fabric epic, Issue #297).

This module owns the persistence and finite-state logic for:

- ``cell_goals`` / ``cell_tasks`` / ``cell_task_events``: a Goal/Accountability
  tree distinct from the System Topology Graph (Feature/Capability/API/UX/
  Symbol/Component) -- Cell Fabric only *references* that graph, it never
  reuses it as the Goal tree. Each task has exactly one owner Cell and one
  parent goal by construction; there is no many-to-many task/context table at
  this Sub.
- ``cell_reports`` / ``cell_escalations``: the P2 report protocol (``digest``
  or ``escalation`` only -- any other free-form payload is rejected
  fail-closed) and the escalation record an escalation-kind report creates in
  the same transaction.

Task status transitions delegate to ``app.cell_fabric.validate_task_transition``
and ``TASK_TRANSITIONS`` (Issue #298) -- this module does not re-implement the
transition table, it only adds ledger-specific rules on top: a retry
(``failed`` -> ``todo``) increments ``retry_count`` and is refused once
``retry_count >= retry_limit``; entering ``blocked`` requires either
``blocked_by`` task ids or an explicit ``detail``; every transition writes a
``cell_task_events`` row.

Evidence resolution (Principle 6, deterministic only): ``resolve_evidence_ref``
accepts ``trace:<id>`` / ``evaluation:<id>`` / ``shadow_result:<id>`` /
``replay_run:<id>`` / ``experiment:<id>`` / ``snapshot_file:<snapshot_id>:<path>``
/ ``improvement:<id>`` and verifies the referenced row exists AND belongs to
the calling System. ``improvement:<id>`` (Sub 7, #304) resolves against
``cell_improvements`` now that that table exists -- an improvement hypothesis
is valid observation/context evidence like any other System-scoped record.
``quality_sample:<id>`` remains a RESERVED reference format for Sub 6 (#302):
it parses but always fails closed with a "not yet implemented" message; no
other logic in this module or its callers may treat it as resolvable.

This is a pure deterministic ledger: no reasoning-model call anywhere in this
module (non-goal per the Issue #300 scope -- orchestrator aggregation is #301,
quality sampling is #302, improvement proposals are #304).

probe-agent:
  role: Goal/Task ledger persistence and delegate/report/escalate protocol
  capability: probe-cell-fabric
  element_type: core
  consumers: [control-server-routes]
  operation_kind: io
  state_effects: [database-read, database-write]
  probe_value: Verify every task transition is legal per cell_fabric's finite table, retry/blocked/returned_to_parent ledger rules are enforced and audited, evidence refs (including improvement:<id> now that #304 exists) resolve only within the calling System, idempotent delegate/report resend never duplicates a row, and the still-reserved quality_sample ref kind fails closed rather than silently resolving.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .cell_fabric import CellContractError, TASK_STATUSES, TaskDelegation, validate_task_transition

# ---------------------------------------------------------------------------
# Errors (translated to HTTP status codes by routes/cell_tasks.py)
# ---------------------------------------------------------------------------


class CellTaskError(Exception):
    """Base class for Goal/Task ledger errors."""


class NotFoundError(CellTaskError):
    """The referenced goal/task/cell/report/escalation does not exist in
    this System -- maps to 404."""


class ConflictError(CellTaskError):
    """A ledger rule (retry limit, illegal return_to_parent source status,
    already-terminal escalation state, ...) refuses the request -- maps to
    409."""


class ValidationFailedError(CellTaskError):
    """Structural/contract validation failed (unknown enum value, cycle,
    unresolvable evidence ref, missing acceptance, ...) -- maps to 422."""


# ---------------------------------------------------------------------------
# Finite vocabularies owned by this module (task status/transitions are
# cell_fabric's; these are the ledger-only additions)
# ---------------------------------------------------------------------------

GOAL_STATUSES = ("open", "achieved", "abandoned")
TASK_PRIORITIES = ("low", "normal", "high")
REPORT_KINDS = ("digest", "escalation")
SEVERITIES = ("sev1", "sev2", "sev3")
ESCALATION_STATUSES = ("open", "acknowledged", "resolved")

# ---------------------------------------------------------------------------
# Evidence / context ref resolution (Principle 6: deterministic, finite ref
# grammar only -- never a keyword/similarity match).
# ---------------------------------------------------------------------------

_REF_RE = re.compile(
    r"^(trace|evaluation|shadow_result|replay_run|experiment|snapshot_file"
    r"|quality_sample|improvement):(.+)$"
)

# P3 reference contract (Issue #300 scope: define the ref format only;
# implementation is #302 quality sampling). improvement:<id> (P4) is no
# longer reserved -- Issue #304 added the cell_improvements table, so it now
# resolves via _INT_ID_TABLES below like any other System-scoped row ref.
_RESERVED_REF_MESSAGES = {
    "quality_sample": "quality_sample refs are not yet implemented (Issue #302)",
}

_INT_ID_TABLES = {
    "evaluation": "evaluation_results",
    "shadow_result": "shadow_results",
    "replay_run": "replay_runs",
    "experiment": "experiments",
    "improvement": "cell_improvements",
}


def resolve_evidence_ref(conn, system_id: int, ref: str) -> bool:
    """Verify an evidence/context ref resolves to a row that belongs to
    ``system_id``.

    Accepted formats: ``trace:<id>``, ``evaluation:<id>``,
    ``shadow_result:<id>``, ``replay_run:<id>``, ``experiment:<id>``,
    ``snapshot_file:<snapshot_id>:<path>``. Anything else -- malformed,
    unresolvable, or a reserved P3/P4 kind -- raises
    :class:`ValidationFailedError` naming the offending ref. Returns ``True``
    only when the ref resolves.
    """
    if not isinstance(ref, str) or not ref:
        raise ValidationFailedError(f"Malformed evidence ref: {ref!r}")
    match = _REF_RE.match(ref)
    if not match:
        raise ValidationFailedError(f"Malformed evidence ref: {ref!r}")
    kind, rest = match.group(1), match.group(2)

    if kind in _RESERVED_REF_MESSAGES:
        raise ValidationFailedError(f"{ref!r}: {_RESERVED_REF_MESSAGES[kind]}")

    if kind == "trace":
        row = conn.execute(
            "SELECT 1 FROM traces WHERE system_id = ? AND trace_id = ?",
            (system_id, rest),
        ).fetchone()
    elif kind in _INT_ID_TABLES:
        if not rest.isdigit():
            raise ValidationFailedError(f"Malformed evidence ref: {ref!r}")
        table = _INT_ID_TABLES[kind]
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE id = ? AND system_id = ?",
            (int(rest), system_id),
        ).fetchone()
    elif kind == "snapshot_file":
        parts = rest.split(":", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1]:
            raise ValidationFailedError(f"Malformed evidence ref: {ref!r}")
        snapshot_id = int(parts[0])
        path = parts[1]
        snapshot_row = conn.execute(
            "SELECT id FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
        if snapshot_row is None:
            raise ValidationFailedError(f"Evidence ref does not resolve: {ref!r}")
        row = conn.execute(
            "SELECT 1 FROM snapshot_files WHERE snapshot_id = ? AND path = ?",
            (snapshot_id, path),
        ).fetchone()
    else:  # pragma: no cover - defensive, unreachable given the regex above
        raise ValidationFailedError(f"Malformed evidence ref: {ref!r}")

    if row is None:
        raise ValidationFailedError(f"Evidence ref does not resolve: {ref!r}")
    return True


# ---------------------------------------------------------------------------
# Shared row lookups (all take an already-open `conn` -- db.get_conn()'s
# lock is non-reentrant, never open a nested connection inside these).
# ---------------------------------------------------------------------------


def get_goal_row(conn, system_id: int, goal_id: int):
    row = conn.execute(
        "SELECT * FROM cell_goals WHERE id = ? AND system_id = ?",
        (goal_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Goal {goal_id} not found in this System")
    return row


def get_task_row(conn, system_id: int, task_id: int):
    row = conn.execute(
        "SELECT * FROM cell_tasks WHERE id = ? AND system_id = ?",
        (task_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Task {task_id} not found in this System")
    return row


def _get_escalation_row(conn, system_id: int, escalation_id: int):
    row = conn.execute(
        "SELECT * FROM cell_escalations WHERE id = ? AND system_id = ?",
        (escalation_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Escalation {escalation_id} not found in this System")
    return row


def _resolve_cell_row_id(conn, system_id: int, cell_id: str) -> int:
    row = conn.execute(
        "SELECT id FROM cell_definitions WHERE cell_id = ? AND system_id = ?",
        (cell_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Cell {cell_id!r} not found in this System")
    return row["id"]


def cell_business_id(conn, cell_row_id: Optional[int]) -> Optional[str]:
    if cell_row_id is None:
        return None
    row = conn.execute(
        "SELECT cell_id FROM cell_definitions WHERE id = ?", (cell_row_id,),
    ).fetchone()
    return row["cell_id"] if row is not None else None


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


def _goal_ancestor_chain(conn, system_id: int, goal_id: int) -> List[int]:
    """Walk ``parent_goal_id`` upward from ``goal_id``, returning the list of
    ancestor ids (not including ``goal_id`` itself). Defensively stops on a
    repeated id instead of looping forever -- this should never happen if
    cycles are rejected at write time, but a corrupt row must not hang."""
    chain: List[int] = []
    seen = set()
    current_id = goal_id
    while True:
        row = conn.execute(
            "SELECT parent_goal_id FROM cell_goals WHERE id = ? AND system_id = ?",
            (current_id, system_id),
        ).fetchone()
        if row is None or row["parent_goal_id"] is None:
            break
        parent_id = row["parent_goal_id"]
        if parent_id in seen:
            break
        seen.add(parent_id)
        chain.append(parent_id)
        current_id = parent_id
    return chain


def would_create_cycle(
    conn, system_id: int, goal_id: Optional[int], new_parent_id: Optional[int]
) -> bool:
    """Deterministic cycle check (Principle 6): would setting ``goal_id``'s
    parent to ``new_parent_id`` create a cycle? True if ``goal_id`` is (or
    would become, via ``new_parent_id``'s ancestor chain) its own ancestor.
    ``goal_id=None`` means "not yet created" -- always False, since a brand
    new goal cannot already appear in anyone's ancestor chain."""
    if new_parent_id is None or goal_id is None:
        return False
    if new_parent_id == goal_id:
        return True
    return goal_id in _goal_ancestor_chain(conn, system_id, new_parent_id)


def create_goal(
    conn,
    *,
    system_id: int,
    title: str,
    description: str = "",
    parent_goal_id: Optional[int] = None,
    owner_cell_id: Optional[str] = None,
):
    """Create a root (``parent_goal_id=None``) or nested Goal. A non-existent
    or cross-System ``parent_goal_id`` is a 404; a parent chain that would
    create a cycle is a 422 (structural, deterministic -- see
    ``would_create_cycle``)."""
    if not title or not title.strip():
        raise ValidationFailedError("title must not be empty")

    owner_row_id = None
    if owner_cell_id is not None:
        owner_row_id = _resolve_cell_row_id(conn, system_id, owner_cell_id)

    if parent_goal_id is not None:
        get_goal_row(conn, system_id, parent_goal_id)
        if would_create_cycle(conn, system_id, None, parent_goal_id):
            raise ValidationFailedError(
                f"parent_goal_id={parent_goal_id} would create a cycle"
            )

    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO cell_goals
                   (system_id, parent_goal_id, title, description, owner_cell_id,
                    status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
            (
                system_id, parent_goal_id, title, description or "",
                owner_row_id, now, now,
            ),
        )
        goal_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM cell_goals WHERE id = ?", (goal_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def update_goal_status(conn, *, system_id: int, goal_id: int, new_status: str):
    if new_status not in GOAL_STATUSES:
        raise ValidationFailedError(f"Unknown goal status: {new_status!r}")
    get_goal_row(conn, system_id, goal_id)
    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE cell_goals SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, goal_id),
        )
        row = conn.execute(
            "SELECT * FROM cell_goals WHERE id = ?", (goal_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def list_goal_tasks(conn, system_id: int, goal_id: int):
    return conn.execute(
        "SELECT * FROM cell_tasks WHERE system_id = ? AND goal_id = ? ORDER BY id DESC",
        (system_id, goal_id),
    ).fetchall()


# ---------------------------------------------------------------------------
# P1 delegate: task creation
# ---------------------------------------------------------------------------


def delegate_task(
    conn,
    *,
    system_id: int,
    goal_id: int,
    owner_cell_id: str,
    title: str,
    acceptance: List[str],
    context_refs: Optional[List[str]] = None,
    budget: Optional[Dict[str, Any]] = None,
    deadline: Optional[str] = None,
    priority: Optional[str] = None,
    delegated_by_cell_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
):
    """Delegate (create) a task. A resend with the same
    ``(system_id, idempotency_key)`` returns the EXISTING row unchanged --
    no duplicate is ever created."""
    context_refs = context_refs or []

    if idempotency_key:
        existing = conn.execute(
            "SELECT * FROM cell_tasks WHERE system_id = ? AND idempotency_key = ?",
            (system_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return existing

    if not title or not title.strip():
        raise ValidationFailedError("title must not be empty")

    # Reuse cell_fabric.TaskDelegation for the acceptance/context_refs/budget/
    # deadline/priority contract instead of re-implementing it -- a missing
    # or empty acceptance list is the same fail-closed error as #298.
    try:
        TaskDelegation(
            goal_ref=f"goal:{goal_id}",
            acceptance=acceptance,
            context_refs=context_refs,
            budget=budget,
            deadline=deadline,
            priority=priority,
        )
    except (ValidationError, CellContractError) as exc:
        raise ValidationFailedError(str(exc))

    if priority is not None and priority not in TASK_PRIORITIES:
        raise ValidationFailedError(f"Unknown priority: {priority!r}")

    get_goal_row(conn, system_id, goal_id)
    owner_row_id = _resolve_cell_row_id(conn, system_id, owner_cell_id)
    delegated_by_row_id = None
    if delegated_by_cell_id is not None:
        delegated_by_row_id = _resolve_cell_row_id(conn, system_id, delegated_by_cell_id)

    # Quality-floor intake suspension (#302): a Cell whose intake is
    # 'suspended' must not receive NEW task delegations -- "受付停止" means
    # stop accepting work, not just stop auditing.
    intake_row = conn.execute(
        "SELECT intake_status FROM cell_intake_states "
        "WHERE system_id = ? AND cell_definition_id = ?",
        (system_id, owner_row_id),
    ).fetchone()
    if intake_row is not None and intake_row["intake_status"] == "suspended":
        raise ConflictError(
            f"Cell {owner_cell_id!r} intake is suspended (quality floor); it "
            "cannot receive new task delegations until intake is resumed"
        )

    for ref in context_refs:
        resolve_evidence_ref(conn, system_id, ref)

    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO cell_tasks
                   (system_id, goal_id, owner_cell_id, delegated_by_cell_id, title,
                    acceptance_json, context_refs_json, budget_json, deadline,
                    priority, status, retry_count, retry_limit, blocked_by_json,
                    acceptance_met, evidence_json, returned_to_parent,
                    idempotency_key, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'todo', 0, 3, '[]', 0, '[]', 0,
                       ?, ?, ?)""",
            (
                system_id, goal_id, owner_row_id, delegated_by_row_id, title,
                json.dumps(acceptance), json.dumps(context_refs),
                json.dumps(budget) if budget is not None else None,
                deadline, priority or "normal", idempotency_key, now, now,
            ),
        )
        task_id = cur.lastrowid
        conn.execute(
            """INSERT INTO cell_task_events
                   (system_id, task_id, event_type, from_status, to_status,
                    detail, created_at)
               VALUES (?, ?, 'created', NULL, 'todo', '', ?)""",
            (system_id, task_id, now),
        )
        row = conn.execute(
            "SELECT * FROM cell_tasks WHERE id = ?", (task_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        # A concurrent resend with the same (system_id, idempotency_key) won
        # the UNIQUE race; return the row it created instead of a 500.
        conn.execute("ROLLBACK")
        if idempotency_key:
            existing = conn.execute(
                "SELECT * FROM cell_tasks WHERE system_id = ? AND idempotency_key = ?",
                (system_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return existing
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


# ---------------------------------------------------------------------------
# Task transitions
# ---------------------------------------------------------------------------


def _transition_event_type(current_status: str, new_status: str) -> str:
    if current_status == "failed" and new_status == "todo":
        return "retry"
    if new_status == "blocked":
        return "blocked"
    if current_status == "blocked" and new_status != "blocked":
        return "unblocked"
    return "transition"


def transition_task(
    conn,
    *,
    system_id: int,
    task_id: int,
    new_status: str,
    acceptance_met: Optional[bool] = None,
    evidence_refs: Optional[List[str]] = None,
    blocked_by: Optional[List[int]] = None,
    detail: str = "",
):
    row = get_task_row(conn, system_id, task_id)
    current_status = row["status"]

    if new_status not in TASK_STATUSES:
        raise ValidationFailedError(f"Unknown target task status: {new_status!r}")

    if current_status == "failed" and new_status == "todo":
        if row["retry_count"] >= row["retry_limit"]:
            raise ConflictError(
                f"Task {task_id} has exhausted its retry_limit "
                f"({row['retry_limit']})"
            )

    if new_status == "blocked":
        if not blocked_by and not (detail and detail.strip()):
            raise ValidationFailedError(
                "Transition to 'blocked' requires blocked_by task ids or an "
                "explicit detail"
            )
        for blocking_id in blocked_by or []:
            get_task_row(conn, system_id, blocking_id)

    resolved_evidence = list(evidence_refs or [])
    if new_status == "done":
        for ref in resolved_evidence:
            resolve_evidence_ref(conn, system_id, ref)

    try:
        validate_task_transition(
            current_status, new_status,
            acceptance_met=bool(acceptance_met), evidence_refs=resolved_evidence,
        )
    except CellContractError as exc:
        raise ValidationFailedError(str(exc))

    now = time.time()
    event_type = _transition_event_type(current_status, new_status)
    conn.execute("BEGIN")
    try:
        set_clauses = ["status = ?", "updated_at = ?"]
        params: List[Any] = [new_status, now]
        if current_status == "failed" and new_status == "todo":
            set_clauses.append("retry_count = retry_count + 1")
        if new_status == "blocked":
            set_clauses.append("blocked_by_json = ?")
            params.append(json.dumps(blocked_by or []))
        elif current_status == "blocked":
            set_clauses.append("blocked_by_json = '[]'")
        if new_status == "done":
            set_clauses.append("acceptance_met = 1")
            set_clauses.append("evidence_json = ?")
            params.append(json.dumps(resolved_evidence))
        params.append(task_id)
        params.append(current_status)
        # Compare-and-set on the observed status: a concurrent transition that
        # already moved the task off ``current_status`` updates 0 rows, so two
        # requests can never both write a transition event from the same
        # ``from_status`` (audit-integrity guard, Principle 6).
        cur = conn.execute(
            f"UPDATE cell_tasks SET {', '.join(set_clauses)} "
            f"WHERE id = ? AND status = ?",
            params,
        )
        if cur.rowcount == 0:
            raise ConflictError(
                f"Task {task_id} status changed concurrently "
                f"(expected {current_status!r}); retry the transition"
            )
        conn.execute(
            """INSERT INTO cell_task_events
                   (system_id, task_id, event_type, from_status, to_status,
                    detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (system_id, task_id, event_type, current_status, new_status,
             detail or "", now),
        )
        row = conn.execute(
            "SELECT * FROM cell_tasks WHERE id = ?", (task_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def return_to_parent(conn, *, system_id: int, task_id: int, detail: str = ""):
    """Mark a task as returned to its parent. Only allowed from ``failed`` or
    ``blocked`` -- anything else is a 409 ledger conflict."""
    row = get_task_row(conn, system_id, task_id)
    if row["status"] not in ("failed", "blocked"):
        raise ConflictError(
            "return_to_parent is only allowed from 'failed' or 'blocked' "
            f"status (current status: {row['status']!r})"
        )
    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE cell_tasks SET returned_to_parent = 1, updated_at = ? WHERE id = ?",
            (now, task_id),
        )
        conn.execute(
            """INSERT INTO cell_task_events
                   (system_id, task_id, event_type, from_status, to_status,
                    detail, created_at)
               VALUES (?, ?, 'returned_to_parent', ?, ?, ?, ?)""",
            (system_id, task_id, row["status"], row["status"], detail or "", now),
        )
        row = conn.execute(
            "SELECT * FROM cell_tasks WHERE id = ?", (task_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def list_task_events(conn, system_id: int, task_id: int):
    return conn.execute(
        """SELECT * FROM cell_task_events WHERE system_id = ? AND task_id = ?
           ORDER BY id ASC""",
        (system_id, task_id),
    ).fetchall()


# ---------------------------------------------------------------------------
# P2 report: digest / escalation
# ---------------------------------------------------------------------------


def _escalation_summary(facts: List[Dict[str, Any]]) -> str:
    if facts:
        return str(facts[0].get("text", ""))
    return ""


def submit_report(
    conn,
    *,
    system_id: int,
    cell_definition_id: str,
    kind: str,
    severity: Optional[str] = None,
    task_id: Optional[int] = None,
    fact: Optional[List[Dict[str, Any]]] = None,
    interpretation: Optional[List[Dict[str, Any]]] = None,
    ask: Optional[List[Dict[str, Any]]] = None,
    idempotency_key: Optional[str] = None,
):
    """Submit a ``digest`` or ``escalation`` report. ``kind`` outside that
    finite set is rejected fail-closed (the request model's
    ``extra="forbid"`` already rejects unknown fields at the FastAPI
    boundary; this defends the same rule for direct callers). An
    ``escalation`` report requires ``severity``; a ``digest`` report must not
    set one. A resend with the same ``(system_id, idempotency_key)`` returns
    the EXISTING row unchanged."""
    if kind not in REPORT_KINDS:
        raise ValidationFailedError(f"Unknown report kind: {kind!r}")
    if kind == "escalation":
        if severity not in SEVERITIES:
            raise ValidationFailedError(
                "escalation reports require a severity (sev1|sev2|sev3)"
            )
    else:
        if severity is not None:
            raise ValidationFailedError("digest reports must not set severity")

    if idempotency_key:
        existing = conn.execute(
            "SELECT * FROM cell_reports WHERE system_id = ? AND idempotency_key = ?",
            (system_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return existing

    owner_row_id = _resolve_cell_row_id(conn, system_id, cell_definition_id)

    if task_id is not None:
        get_task_row(conn, system_id, task_id)

    facts = fact or []
    for item in facts:
        for ref in item.get("evidence_refs") or []:
            resolve_evidence_ref(conn, system_id, ref)

    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO cell_reports
                   (system_id, cell_definition_id, task_id, kind, severity,
                    fact_json, interpretation_json, ask_json, idempotency_key,
                    created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, owner_row_id, task_id, kind, severity,
                json.dumps(facts), json.dumps(interpretation or []),
                json.dumps(ask or []), idempotency_key, now,
            ),
        )
        report_id = cur.lastrowid
        if kind == "escalation":
            conn.execute(
                """INSERT INTO cell_escalations
                       (system_id, report_id, cell_definition_id, severity,
                        status, summary, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'open', ?, ?, ?)""",
                (
                    system_id, report_id, owner_row_id, severity,
                    _escalation_summary(facts), now, now,
                ),
            )
        row = conn.execute(
            "SELECT * FROM cell_reports WHERE id = ?", (report_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        # A concurrent resend with the same (system_id, idempotency_key) won
        # the UNIQUE race; return its row instead of surfacing a 500.
        conn.execute("ROLLBACK")
        if idempotency_key:
            existing = conn.execute(
                "SELECT * FROM cell_reports WHERE system_id = ? AND idempotency_key = ?",
                (system_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return existing
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def get_report_escalation_id(conn, report_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM cell_escalations WHERE report_id = ?", (report_id,),
    ).fetchone()
    return row["id"] if row is not None else None


# ---------------------------------------------------------------------------
# Escalations
# ---------------------------------------------------------------------------


def _set_escalation_status(conn, escalation_id: int, new_status: str):
    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            "UPDATE cell_escalations SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, escalation_id),
        )
        row = conn.execute(
            "SELECT * FROM cell_escalations WHERE id = ?", (escalation_id,),
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def acknowledge_escalation(conn, *, system_id: int, escalation_id: int):
    row = _get_escalation_row(conn, system_id, escalation_id)
    if row["status"] != "open":
        raise ConflictError(
            f"Escalation {escalation_id} is not open (status={row['status']!r})"
        )
    return _set_escalation_status(conn, escalation_id, "acknowledged")


def resolve_escalation(conn, *, system_id: int, escalation_id: int):
    row = _get_escalation_row(conn, system_id, escalation_id)
    if row["status"] == "resolved":
        raise ConflictError(f"Escalation {escalation_id} is already resolved")
    return _set_escalation_status(conn, escalation_id, "resolved")
