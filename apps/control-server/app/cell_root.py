"""Probe Cell Fabric: Root Orchestrator と統合ダイジェスト (Issue #303, Sub 5
of the Probe Cell Fabric epic, Issue #297).

This module is purely deterministic -- there is NO reasoning-model call
anywhere in this file. The one reasoning boundary in the whole Cell Fabric
digest path is ``cell_orchestrator.run_triage`` (#301, Sub 4); by the time a
``cell_triage_results`` row exists, this module only ever reads its
``proposed_ask`` text and persisted classification, it never re-runs or
second-guesses the classification.

``build_root_digest`` composes:

- Canonical deterministic facts from ``system_state.build_system_state``
  (Issue #235/#193) -- ``user_phase``, ``overall_severity``, ``primary_item``,
  and the full item list are embedded verbatim under ``audit.system_state``;
  this module never re-derives any of those facts itself.
- Every orchestrator Cell's ``cell_orchestrator.build_orchestrator_digest``
  (#301) as one ``key_points`` entry each (progress/quality/bottleneck
  snapshot), plus a flag when any of its roster's current bindings are
  ``stale``/``review_required``.
- Open ``cell_escalations`` (#300), severity-routed: sev1 surfaces immediately
  in ``conclusion.top_risks`` (max 3, deterministic order: severity then
  ``created_at`` desc), sev2 becomes a ``key_points`` pending-decision entry,
  sev3 is stored in ``evidence`` only. Escalations sharing the same
  ``(cell_definition_id, "escalation", evidence-or-summary)`` root cause are
  deterministically deduped (sha256 dedupe key) into one entry carrying a
  ``sources`` list of the merged escalation ids.

``build_root_digest`` takes an already-open ``conn`` (used for every
Cell-Fabric read below) AND a pre-built ``system_state_assessment``
(``system_state.SystemStateAssessment``). This split is required by the
non-reentrant ``db.get_conn()`` lock: ``system_state.build_system_state``
opens ITS OWN ``get_conn()`` internally (see ``routes/system_state.py``), so
it can never be called while a caller already holds a connection. Callers
(``routes/cell_root.py``) must call ``build_system_state(system_id)`` BEFORE
opening the ``conn`` they pass in here -- never the other way around.

Ask lifecycle (``cell_asks``): ``sync_asks_from_sources`` materializes
Ask rows from open sev1/sev2 escalations and non-empty-``proposed_ask``
triage results, keyed by a per-source ``dedupe_key`` so re-sync never
duplicates a row. ``decide_ask`` records a human decision
(``decision_method='manual'``) and flows it back into the Goal/Task ledger
deterministically (accepted -> unblock a blocked owning task; rejected ->
acknowledge the source escalation; held -> no side effect). Per Issue #297's
"proposal accept != execution approval" design decision, ``execution_approved``
is NEVER set by this module -- accepting an Ask never triggers a policy
change, candidate deploy, patch apply, or any other execution path; that
remains #304's and the existing #25/#216/#242/#252 human gates' domain.

probe-agent:
  role: Deterministic Root Orchestrator digest composition and Ask lifecycle
  capability: probe-cell-fabric
  element_type: core
  consumers: [control-server-routes]
  operation_kind: mixed
  state_effects: [database-read, database-write]
  probe_value: Verify the digest embeds system_state's canonical facts verbatim rather than re-deriving them, sev1/sev2/sev3 escalations route to the documented digest section with deterministic dedupe (shared root cause collapses to one entry with a sources list), every evidence_refs string in the digest resolves via cell_tasks.resolve_evidence_ref, Ask sync is idempotent, deciding an Ask never sets execution_approved, and this module never calls an LLM.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from . import cell_orchestrator, cell_tasks
from .cell_fabric import TASK_STATUSES
from .system_state import SEVERITY_ORDER, StateItem, SystemStateAssessment

# ---------------------------------------------------------------------------
# Errors (translated to HTTP status codes by routes/cell_root.py)
# ---------------------------------------------------------------------------


class CellRootError(Exception):
    """Base class for Root Orchestrator digest / Ask errors."""


class NotFoundError(CellRootError):
    """The referenced Ask does not exist in this System -- maps to 404."""


class ConflictError(CellRootError):
    """The Ask has already been decided -- maps to 409."""


class ValidationFailedError(CellRootError):
    """An unknown decision value was given -- maps to 422."""


ASK_SOURCE_KINDS: Tuple[str, ...] = ("escalation", "triage", "report")
ASK_SEVERITIES: Tuple[str, ...] = ("sev1", "sev2", "sev3")
ASK_STATUSES: Tuple[str, ...] = ("open", "accepted", "held", "rejected")
ASK_DECISIONS: Tuple[str, ...] = ("accepted", "held", "rejected")

_ESCALATION_SEVERITY_TO_STATE = {"sev1": "error", "sev2": "warning", "sev3": "info"}
_SEV_RANK = {sev: i for i, sev in enumerate(ASK_SEVERITIES)}
_STATE_SEVERITY_RANK = {level: i for i, level in enumerate(SEVERITY_ORDER)}


def _worst_state_severity(a: str, b: str) -> str:
    """Deterministic worst-of for the ``ok|info|warning|blocked|error``
    vocabulary (Principle 6, finite set), reusing ``system_state``'s own
    ``SEVERITY_ORDER`` rather than re-declaring a second ranking."""
    rank_a = _STATE_SEVERITY_RANK.get(a, len(SEVERITY_ORDER) - 1)
    rank_b = _STATE_SEVERITY_RANK.get(b, len(SEVERITY_ORDER) - 1)
    return a if rank_a <= rank_b else b


def _state_item_to_dict(item: Optional[StateItem]) -> Optional[Dict[str, Any]]:
    if item is None:
        return None
    return asdict(item)


# ---------------------------------------------------------------------------
# Deterministic escalation dedupe (Principle 6: sha256 of an explicit,
# structural key -- never a keyword/similarity match)
# ---------------------------------------------------------------------------


def _report_evidence_refs(conn, report_id: int) -> List[str]:
    row = conn.execute(
        "SELECT fact_json FROM cell_reports WHERE id = ?", (report_id,),
    ).fetchone()
    if row is None:
        return []
    refs: List[str] = []
    for fact in json.loads(row["fact_json"] or "[]"):
        refs.extend(fact.get("evidence_refs") or [])
    return sorted(set(refs))


def _escalation_dedupe_key(cell_definition_id: int, basis: str) -> str:
    payload = f"{cell_definition_id}:escalation:{basis}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _group_open_escalations(conn, *, system_id: int) -> List[Dict[str, Any]]:
    """Read every open ``cell_escalations`` row and collapse rows sharing the
    same ``(cell_definition_id, "escalation", evidence-or-summary)`` root
    cause into one group, carrying a ``sources`` list of the merged ids.
    Returns groups sorted by (severity rank, ``created_at`` desc) -- the same
    deterministic order used for ``conclusion.top_risks``/``key_points``/
    ``evidence`` slicing by the caller.
    """
    rows = conn.execute(
        "SELECT * FROM cell_escalations WHERE system_id = ? AND status = 'open' "
        "ORDER BY id ASC",
        (system_id,),
    ).fetchall()

    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        refs = _report_evidence_refs(conn, row["report_id"])
        basis = ",".join(refs) if refs else row["summary"]
        key = _escalation_dedupe_key(row["cell_definition_id"], basis)
        if key not in groups:
            groups[key] = {
                "dedupe_key": key,
                "cell_definition_id": row["cell_definition_id"],
                "cell_id": cell_tasks.cell_business_id(conn, row["cell_definition_id"]) or "",
                "severity": row["severity"],
                "summary": row["summary"],
                "evidence_refs": refs,
                "sources": [],
                "created_at": row["created_at"],
            }
            order.append(key)
        group = groups[key]
        group["sources"].append(row["id"])
        group["created_at"] = min(group["created_at"], row["created_at"])
        if _SEV_RANK.get(row["severity"], 99) < _SEV_RANK.get(group["severity"], 99):
            group["severity"] = row["severity"]

    result = [groups[k] for k in order]
    result.sort(key=lambda g: (_SEV_RANK.get(g["severity"], 99), -g["created_at"]))
    return result


def _risk_entry(group: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "escalation",
        "dedupe_key": group["dedupe_key"],
        "cell_id": group["cell_id"],
        "severity": group["severity"],
        "summary": group["summary"],
        "evidence_refs": group["evidence_refs"],
        "sources": group["sources"],
        "created_at": group["created_at"],
    }


# ---------------------------------------------------------------------------
# Orchestrator aggregation (Sub 4's build_orchestrator_digest, per-cell)
# ---------------------------------------------------------------------------


def _all_orchestrator_rows(conn, *, system_id: int):
    return conn.execute(
        "SELECT * FROM cell_definitions WHERE system_id = ? AND roster_json IS NOT NULL "
        "ORDER BY cell_id ASC",
        (system_id,),
    ).fetchall()


def _orchestrator_key_point(digest: Dict[str, Any]) -> Dict[str, Any]:
    is_stale = any(
        entry.get("binding_status") in ("stale", "review_required")
        for entry in digest["roster"]
    )
    return {
        "type": "orchestrator",
        "cell_id": digest["cell_id"],
        "progress": digest["tasks"],
        "escalations_open_by_severity": digest["escalations"]["open_by_severity"],
        "bottleneck_candidates": digest["bottleneck_candidates"],
        "binding_stale": is_stale,
    }


def _task_evidence_entries(conn, *, system_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, owner_cell_id, evidence_json FROM cell_tasks "
        "WHERE system_id = ? AND evidence_json != '[]'",
        (system_id,),
    ).fetchall()
    entries = []
    for row in rows:
        refs = json.loads(row["evidence_json"] or "[]")
        if not refs:
            continue
        entries.append({
            "task_id": row["id"],
            "cell_id": cell_tasks.cell_business_id(conn, row["owner_cell_id"]) or "",
            "evidence_refs": refs,
        })
    return entries


# ---------------------------------------------------------------------------
# build_root_digest
# ---------------------------------------------------------------------------


def build_root_digest(
    conn,
    *,
    system_id: int,
    system_state_assessment: SystemStateAssessment,
    window_seconds: float = 3600.0,
) -> Dict[str, Any]:
    """Build the 4-level progressive-disclosure Root Orchestrator digest.

    ``conn`` is used for every Cell-Fabric read (orchestrator digests,
    escalations, tasks); ``system_state_assessment`` must already have been
    built by the caller via ``system_state.build_system_state(system_id)``
    WITHOUT holding a connection (see the module docstring for why).
    """
    now = time.time()

    orchestrator_rows = _all_orchestrator_rows(conn, system_id=system_id)
    orchestrator_digests = [
        cell_orchestrator.build_orchestrator_digest(
            conn, system_id=system_id, cell_row=row, window_seconds=window_seconds,
        )
        for row in orchestrator_rows
    ]

    total_task_counts = {status: 0 for status in TASK_STATUSES}
    stale_orchestrator_cell_ids: List[str] = []
    orchestrator_key_points: List[Dict[str, Any]] = []
    for digest in orchestrator_digests:
        for status_name, n in digest["tasks"]["total"].items():
            total_task_counts[status_name] += n
        key_point = _orchestrator_key_point(digest)
        if key_point["binding_stale"]:
            stale_orchestrator_cell_ids.append(digest["cell_id"])
        orchestrator_key_points.append(key_point)

    escalation_groups = _group_open_escalations(conn, system_id=system_id)
    sev1_groups = [g for g in escalation_groups if g["severity"] == "sev1"]
    sev2_groups = [g for g in escalation_groups if g["severity"] == "sev2"]
    sev3_groups = [g for g in escalation_groups if g["severity"] == "sev3"]

    # Raw (non-deduped) counts -- facts, not display entries.
    escalations_open_by_severity = {"sev1": 0, "sev2": 0, "sev3": 0}
    for row in conn.execute(
        "SELECT severity, COUNT(*) AS n FROM cell_escalations "
        "WHERE system_id = ? AND status = 'open' GROUP BY severity",
        (system_id,),
    ).fetchall():
        escalations_open_by_severity[row["severity"]] = row["n"]

    open_asks_count = conn.execute(
        "SELECT COUNT(*) AS n FROM cell_asks WHERE system_id = ? AND status = 'open'",
        (system_id,),
    ).fetchone()["n"]

    top_risks = [_risk_entry(g) for g in sev1_groups[:3]]

    sev2_key_points = [
        {"type": "escalation_pending_decision", **_risk_entry(g)} for g in sev2_groups
    ]
    key_points = orchestrator_key_points + sev2_key_points

    sev3_evidence = [_risk_entry(g) for g in sev3_groups]
    task_evidence = _task_evidence_entries(conn, system_id=system_id)

    escalation_state_severity = "ok"
    if sev1_groups:
        escalation_state_severity = _ESCALATION_SEVERITY_TO_STATE["sev1"]
    elif sev2_groups:
        escalation_state_severity = _ESCALATION_SEVERITY_TO_STATE["sev2"]
    elif sev3_groups:
        escalation_state_severity = _ESCALATION_SEVERITY_TO_STATE["sev3"]
    overall_severity = _worst_state_severity(
        system_state_assessment.overall_severity, escalation_state_severity,
    )

    is_stale = bool(stale_orchestrator_cell_ids) or any(
        item.state_id in ("repository.snapshot.stale", "snapshot.latest.stale_for_interview")
        for item in system_state_assessment.items
    )

    conclusion = {
        "overall_severity": overall_severity,
        "counts": {
            "orchestrators": len(orchestrator_rows),
            "tasks": total_task_counts,
            "escalations_open_by_severity": escalations_open_by_severity,
            "asks_open": open_asks_count,
        },
        "top_risks": top_risks,
    }

    evidence = {
        "sev3_escalations": sev3_evidence,
        "tasks": task_evidence,
    }

    audit = {
        "generated_at": now,
        "system_state": {
            "generated_at": system_state_assessment.generated_at,
            "overall_severity": system_state_assessment.overall_severity,
            "user_phase": system_state_assessment.user_phase,
            "primary_item": _state_item_to_dict(system_state_assessment.primary_item),
            "items": [_state_item_to_dict(item) for item in system_state_assessment.items],
        },
        "is_stale": is_stale,
        "stale_orchestrator_cell_ids": stale_orchestrator_cell_ids,
        "decision_method_notes": (
            "This digest is 100% deterministic aggregation (decision_method: "
            "deterministic). The only upstream reasoning-model step is each "
            "orchestrator's cell_triage classification (#301, "
            "decision_method: reasoning_llm); an Ask's decision "
            "(accept/hold/reject) is always decision_method: manual, and "
            "accepting an Ask never approves execution -- see "
            "cell_asks.execution_approved."
        ),
    }

    return {
        "generated_at": now,
        "conclusion": conclusion,
        "key_points": key_points,
        "evidence": evidence,
        "audit": audit,
    }


# ---------------------------------------------------------------------------
# Ask lifecycle
# ---------------------------------------------------------------------------


def get_ask_row(conn, system_id: int, ask_id: int):
    row = conn.execute(
        "SELECT * FROM cell_asks WHERE id = ? AND system_id = ?", (ask_id, system_id),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Ask {ask_id} not found in this System")
    return row


def list_asks(conn, *, system_id: int, status: Optional[str] = None):
    query = "SELECT * FROM cell_asks WHERE system_id = ?"
    params: List[Any] = [system_id]
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC"
    return conn.execute(query, params).fetchall()


def _ask_exists_for_dedupe_key(conn, system_id: int, dedupe_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM cell_asks WHERE system_id = ? AND dedupe_key = ?",
        (system_id, dedupe_key),
    ).fetchone()
    return row is not None


def sync_asks_from_sources(conn, *, system_id: int) -> Dict[str, int]:
    """Materialize ``cell_asks`` rows from (a) open sev1/sev2
    ``cell_escalations`` and (b) ``cell_triage_results`` with a non-empty
    ``proposed_ask``. Idempotent via ``dedupe_key`` -- re-running this never
    creates a duplicate row for the same source. Never auto-decides
    anything; every created row starts ``status='open'``.
    """
    now = time.time()
    created = 0
    deduped = 0

    escalation_rows = conn.execute(
        "SELECT * FROM cell_escalations WHERE system_id = ? AND status = 'open' "
        "AND severity IN ('sev1', 'sev2') ORDER BY id ASC",
        (system_id,),
    ).fetchall()
    for row in escalation_rows:
        dedupe_key = f"escalation:{row['id']}"
        if _ask_exists_for_dedupe_key(conn, system_id, dedupe_key):
            deduped += 1
            continue
        # cell_escalations itself carries no task_id -- it comes from the
        # originating cell_reports row (an escalation report may optionally
        # target a task), so an accepted decision has something to unblock.
        report_row = conn.execute(
            "SELECT task_id FROM cell_reports WHERE id = ?", (row["report_id"],),
        ).fetchone()
        task_id = report_row["task_id"] if report_row is not None else None
        conn.execute("BEGIN")
        try:
            conn.execute(
                """INSERT INTO cell_asks
                       (system_id, source_kind, source_id, cell_definition_id, goal_id,
                        task_id, ask_text, severity, status, decision, decision_method,
                        decided_by, decided_at, execution_approved, dedupe_key, created_at)
                   VALUES (?, 'escalation', ?, ?, NULL, ?, ?, ?, 'open', '', '',
                           NULL, NULL, 0, ?, ?)""",
                (
                    system_id, row["id"], row["cell_definition_id"], task_id,
                    row["summary"] or "(no summary)", row["severity"], dedupe_key, now,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        created += 1

    triage_rows = conn.execute(
        "SELECT * FROM cell_triage_results WHERE system_id = ? AND proposed_ask != '' "
        "ORDER BY id ASC",
        (system_id,),
    ).fetchall()
    for row in triage_rows:
        dedupe_key = f"triage:{row['id']}"
        if _ask_exists_for_dedupe_key(conn, system_id, dedupe_key):
            deduped += 1
            continue
        conn.execute("BEGIN")
        try:
            conn.execute(
                """INSERT INTO cell_asks
                       (system_id, source_kind, source_id, cell_definition_id, goal_id,
                        task_id, ask_text, severity, status, decision, decision_method,
                        decided_by, decided_at, execution_approved, dedupe_key, created_at)
                   VALUES (?, 'triage', ?, ?, NULL, NULL, ?, 'sev2', 'open', '', '',
                           NULL, NULL, 0, ?, ?)""",
                (
                    system_id, row["id"], row["cell_definition_id"],
                    row["proposed_ask"], dedupe_key, now,
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        created += 1

    return {"created": created, "deduped": deduped}


def decide_ask(
    conn,
    *,
    system_id: int,
    ask_id: int,
    decision: str,
    decided_by: Optional[str] = None,
    note: str = "",
):
    """Record a human decision on an Ask (``decision_method='manual'``) and
    flow it back into the Goal/Task ledger deterministically:

    - ``accepted``: if the Ask has a ``task_id`` AND that task is currently
      ``blocked``, unblock it (``blocked`` -> ``todo`` via
      ``cell_tasks.transition_task``, which writes its own
      ``cell_task_events`` row). Otherwise no side effect.
    - ``rejected``: if the Ask's source is an escalation AND that escalation
      is still ``open``, acknowledge it.
    - ``held``: no side effect.

    ``execution_approved`` is NEVER set here -- accepting an Ask is a
    proposal-accept record, not an execution-approve record (Issue #297's
    "proposal accept != execution approval" design decision); nothing in
    this function triggers a policy change, candidate deploy, or patch
    apply.

    Each ledger side effect uses its own transaction via the existing
    ``cell_tasks`` helpers (which manage their own BEGIN/COMMIT) -- this
    function's own UPDATE is committed first, matching the non-reentrant
    ``db.get_conn()``/transaction discipline used elsewhere in this module
    group.
    """
    if decision not in ASK_DECISIONS:
        raise ValidationFailedError(f"Unknown decision: {decision!r}")

    row = get_ask_row(conn, system_id, ask_id)
    if row["status"] != "open":
        raise ConflictError(f"Ask {ask_id} has already been decided (status={row['status']!r})")

    now = time.time()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE cell_asks SET status = ?, decision = ?, decision_method = 'manual',
                   decided_by = ?, decided_at = ? WHERE id = ?""",
            (decision, note or "", decided_by, now, ask_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM cell_asks WHERE id = ?", (ask_id,)).fetchone()

    if decision == "accepted" and row["task_id"] is not None:
        task_row = conn.execute(
            "SELECT * FROM cell_tasks WHERE id = ?", (row["task_id"],),
        ).fetchone()
        if task_row is not None and task_row["status"] == "blocked":
            cell_tasks.transition_task(
                conn, system_id=system_id, task_id=row["task_id"], new_status="todo",
                detail=f"cell_ask {ask_id} accepted",
            )
    elif decision == "rejected" and row["source_kind"] == "escalation":
        escalation_row = conn.execute(
            "SELECT * FROM cell_escalations WHERE id = ? AND system_id = ?",
            (row["source_id"], system_id),
        ).fetchone()
        if escalation_row is not None and escalation_row["status"] == "open":
            cell_tasks.acknowledge_escalation(
                conn, system_id=system_id, escalation_id=escalation_row["id"],
            )

    return conn.execute("SELECT * FROM cell_asks WHERE id = ?", (ask_id,)).fetchone()
