"""Canonical state-driven System Interview workflow engine (Issue #349).

Implements `docs/system-interview-workflow-ux.md` §2.2 -- the ONE place the
developer-facing workflow state is decided. The Dashboard renders what this
module returns; it never re-derives a state of its own (spec principle P9,
§2.6).

The evaluation has two stages, exactly as specified:

* Stage 1 -- a **13-row first-match rule table** (`evaluate_candidate_state`)
  over persisted facts only, producing a candidate state and the matched row
  number.
* Stage 2 -- the **backward hold** (`apply_backward_hold`): the ordered
  states are `W2 < W3 < W4 < W5 < W6 < W7`; `W0-A` / `W0-B` / `W1` carry no
  workflow position, are never held, and never move `reached_state`. A
  candidate EARLIER than `reached_state` is displayed only after the
  developer acknowledges the backward request for it.

Both stages are pure functions of a `WorkflowFacts` value: same facts in,
same state out, no clock, no client state, no ordering dependence. The DB
side (`gather_facts`, `evaluate_session_workflow`) reads persisted rows and
performs only two system-recorded writes (spec fact C: the `reached_state`
checkpoint and the backward request); every human gate stays where it was.

Decision classification (Principle 6/7): every rule here is a finite,
enumerated structural check. No reasoning model is called from this module,
and no heuristic ever substitutes for one that failed -- a failed process is
represented as a failure record and shown as a failure.

probe-agent:
  role: Canonical two-stage developer-facing interview workflow state engine
  capability: interactive-system-understanding
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify the displayed workflow state is decided once, from persisted facts only, and that a backward transition into a completed state never happens without a recorded developer acknowledgement.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

__all__ = [
    "ORDERED_STATES",
    "UNORDERED_STATES",
    "WORKFLOW_STATES",
    "BLOCKING_TARGET_STATES",
    "PROCESS_KINDS",
    "PROCESS_BLOCKING_TARGET",
    "CAUSE_KIND_BY_RULE",
    "PRIMARY_ACTION_BY_STATE",
    "TERMINAL_KINDS",
    "WorkflowFacts",
    "WorkflowDecision",
    "evaluate_candidate_state",
    "apply_backward_hold",
    "state_rank",
    "terminal_kind_for",
    "diff_digest",
    "gather_facts",
    "evaluate_session_workflow",
    "start_process_run",
    "finish_process_run",
    "process_run",
    "ProcessRunTracker",
    "tracked_process",
    "sweep_stale_process_runs",
    "classify_failure",
    "process_stuck_after_seconds",
]


# --- Finite vocabularies ----------------------------------------------------

#: Developer-facing states in progress order. Comparable with `state_rank`.
ORDERED_STATES: Tuple[str, ...] = ("W2", "W3", "W4", "W5", "W6", "W7")

#: States that express a temporary circumstance rather than a workflow
#: position: they are never held back and never move `reached_state`
#: (spec §2.2 "進捗順序").
UNORDERED_STATES: Tuple[str, ...] = ("W0-A", "W0-B", "W1")

WORKFLOW_STATES: Tuple[str, ...] = UNORDERED_STATES + ORDERED_STATES

#: The only states a blocking failure may target (spec §5.2 closing note).
BLOCKING_TARGET_STATES: Tuple[str, ...] = ("W2", "W4", "W5")

#: Processes that may put the screen into `W1`. A process not listed here
#: does not get a persisted running record and therefore cannot produce
#: `W1` -- its progress is a transient hint next to the current work only
#: (spec §8.1 fact B).
PROCESS_KINDS: Tuple[str, ...] = (
    "understanding_build",
    "understanding_update",
    "alignment_build",
    "question_routing",
    "intent_candidates",
    "proposal_generation",
    "diff_generation",
    "runtime_reality_check",
)

#: Which state a FAILED run of each kind can block, when the failure leaves
#: no material to keep deciding with (spec §5.1: the classification depends
#: on remaining material, decided per failure in `classify_failure`). A kind
#: mapped to None can only ever degrade.
PROCESS_BLOCKING_TARGET: Dict[str, Optional[str]] = {
    "understanding_build": "W2",     # E3-a when no question can be offered
    "understanding_update": "W2",    # E3-a
    "alignment_build": "W4",         # E4-a on the first build only
    "diff_generation": "W5",         # E12 generation failure
    "question_routing": None,        # E14 over an auxiliary process
    "intent_candidates": None,
    "proposal_generation": None,
    "runtime_reality_check": None,
}

#: Deterministic cause of a backward request, keyed by the stage-1 rule row
#: that produced the earlier candidate. Finite by construction.
CAUSE_KIND_BY_RULE: Dict[int, str] = {
    5: "blocking_failure",
    6: "understanding_recheck",
    7: "question_reopened",
    8: "blocking_failure",
    9: "alignment_item_added",
    10: "blocking_failure",
    11: "proposal_review_reopened",
    12: "diff_review_pending",
}

#: The one primary action of each state (spec §4.3). It is by definition the
#: operation that satisfies that state's completion condition; `W1` alone has
#: none (§4.3.1). `W7` is resolved per terminal in `primary_action_for`.
PRIMARY_ACTION_BY_STATE: Dict[str, str] = {
    "W0-A": "open_repository",
    "W0-B": "start_session",
    "W1": "none",
    "W2": "confirm_understanding",
    "W3": "submit_answer",
    "W4": "confirm_alignment_item",
    "W5": "approve_proposal",
    "W6": "record_diff_review",
}

#: `W7` terminals in first-match order (spec §5.4).
TERMINAL_KINDS: Tuple[str, ...] = ("completed", "handoff", "suspended")

_TERMINAL_PRIMARY_ACTION: Dict[str, str] = {
    "completed": "open_connection_setup",
    "handoff": "view_handoff_status",
    "suspended": "resume_session",
}


def state_rank(state: str) -> int:
    """Progress-order index of an ordered state; -1 for unordered states."""
    if state in ORDERED_STATES:
        return ORDERED_STATES.index(state)
    return -1


# --- Facts ------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowFacts:
    """Every persisted fact the two-stage evaluation reads. Nothing else may
    influence the decision -- in particular no client-only value (a chosen
    tab, a mutation's in-flight flag) and no wall clock."""

    # Rule 1 / 2 -- page-level preconditions.
    has_snapshot: bool = False
    has_session: bool = False
    # Rule 3 -- explicit terminal (OP-D14 suspend / handoff).
    session_closed: bool = False
    # Rule 4 -- fact B, running records for THIS session only.
    running_process_kinds: Tuple[str, ...] = ()
    # Rules 5 / 8 / 10 -- fact B, unresolved blocking failures by target state.
    blocking_failure_states: FrozenSet[str] = frozenset()
    # Rule 6.
    understanding_unconfirmed: bool = False
    # Rule 7.
    open_required_questions: int = 0
    # Rule 9.
    outstanding_alignment_items: int = 0
    # Rule 11.
    proposals_needing_review: int = 0
    proposals_generatable: bool = False
    approved_proposal_count: int = 0
    # Rules 11 / 12 -- "the diff that corresponds to the CURRENT approval set".
    diff_matches_approval_set: bool = False
    # Rule 12 -- fact A.
    diff_review_complete: bool = False
    # W7 terminal classification only (never a rule-table input).
    pending_handoff_count: int = 0


@dataclass(frozen=True)
class WorkflowDecision:
    candidate_state: str
    rule_row: int
    state: str
    reached_state: Optional[str]
    next_reached_state: Optional[str]
    backward_hold: bool
    cause_kind: Optional[str]
    terminal_kind: Optional[str]
    primary_action: str


# --- Stage 1: the 13-row first-match rule table -----------------------------


def evaluate_candidate_state(facts: WorkflowFacts) -> Tuple[str, int]:
    """Spec §2.2 stage 1. Returns ``(candidate_state, rule_row)``.

    Rows are evaluated top to bottom and the FIRST match wins (the same
    resolution style as Issue #287's Alignment classification table): a
    blocking-failure row sits immediately above the row for the state it
    blocks, so work the developer can still do earlier in the flow always
    beats a failure notice.
    """
    # 1: no snapshot -- E1, expressed as the state W0-A itself.
    if not facts.has_snapshot:
        return "W0-A", 1
    # 2: no session selected.
    if not facts.has_session:
        return "W0-B", 2
    # 3: explicit terminal. Evaluated BEFORE the running/failure rows so a
    # deliberate suspend/handoff can move the display to W7 safely without
    # marking any unresolved failure as solved.
    if facts.session_closed:
        return "W7", 3
    # 4: a persisted running record for this session.
    if facts.running_process_kinds:
        return "W1", 4
    # 5: unresolved blocking failure targeting W2 (E3-a).
    if "W2" in facts.blocking_failure_states:
        return "W2", 5
    # 6: understanding present but unconfirmed (or capability re-confirmation).
    if facts.understanding_unconfirmed:
        return "W2", 6
    # 7: a required question is still unanswered.
    if facts.open_required_questions > 0:
        return "W3", 7
    # 8: unresolved blocking failure targeting W4 (E4-a).
    if "W4" in facts.blocking_failure_states:
        return "W4", 8
    # 9: outstanding Alignment items (must_review + batch_reviewable).
    if facts.outstanding_alignment_items > 0:
        return "W4", 9
    # 10: unresolved blocking failure targeting W5 (E12 generation failure).
    if "W5" in facts.blocking_failure_states:
        return "W5", 10
    # 11: proposals still need review, can still be generated, or an approved
    #     set exists with no diff corresponding to it.
    if (
        facts.proposals_needing_review > 0
        or facts.proposals_generatable
        or (facts.approved_proposal_count > 0 and not facts.diff_matches_approval_set)
    ):
        return "W5", 11
    # 12: a diff corresponding to the current approval set exists and its
    #     review is not recorded yet.
    if facts.diff_matches_approval_set and not facts.diff_review_complete:
        return "W6", 12
    # 13: nothing else matched.
    return "W7", 13


def terminal_kind_for(facts: WorkflowFacts) -> str:
    """Spec §5.4 -- the three `W7` terminals, resolved by first match."""
    if facts.diff_review_complete and facts.diff_matches_approval_set:
        return "completed"
    if facts.pending_handoff_count > 0:
        return "handoff"
    return "suspended"


def primary_action_for(state: str, terminal_kind: Optional[str]) -> str:
    if state == "W7":
        return _TERMINAL_PRIMARY_ACTION[terminal_kind or "suspended"]
    return PRIMARY_ACTION_BY_STATE[state]


# --- Stage 2: the backward hold ---------------------------------------------


def apply_backward_hold(
    facts: WorkflowFacts, *, reached_state: Optional[str]
) -> WorkflowDecision:
    """Spec §2.2 stage 2, as a pure function.

    The caller persists what this function reports: ``next_reached_state``
    (when not ``None``) and, when ``backward_hold`` is true, a backward
    request carrying ``cause_kind`` / ``candidate_state``. An acknowledgement
    (fact D) is applied by LOWERING ``reached_state`` to the acknowledged
    candidate, which is why this function needs no acknowledgement input:
    once acknowledged, the candidate is no longer earlier than the
    checkpoint.
    """
    candidate, rule_row = evaluate_candidate_state(facts)
    terminal = terminal_kind_for(facts) if candidate == "W7" else None

    def _decide(
        state: str,
        *,
        next_reached: Optional[str],
        hold: bool,
        cause: Optional[str],
    ) -> WorkflowDecision:
        shown_terminal = terminal if state == "W7" else None
        return WorkflowDecision(
            candidate_state=candidate,
            rule_row=rule_row,
            state=state,
            reached_state=reached_state,
            next_reached_state=next_reached,
            backward_hold=hold,
            cause_kind=cause,
            terminal_kind=shown_terminal,
            primary_action=primary_action_for(state, shown_terminal),
        )

    # 2-0: an explicit terminal shows W7 without advancing the checkpoint --
    # suspending is not progress (spec §2.2 stage 2, step 0).
    if rule_row == 3:
        return _decide("W7", next_reached=None, hold=False, cause=None)

    # 2-1: unordered candidates are shown as-is and never move the checkpoint.
    if candidate in UNORDERED_STATES:
        return _decide(candidate, next_reached=None, hold=False, cause=None)

    # 2-2: at or past the checkpoint -- advance normally.
    if reached_state is None or state_rank(candidate) >= state_rank(reached_state):
        return _decide(candidate, next_reached=candidate, hold=False, cause=None)

    # 2-3/2-4: earlier than the checkpoint. Hold at `reached_state` and report
    # the backward request the caller must record. An acknowledgement (fact D)
    # is consumed by lowering the checkpoint to the acknowledged candidate, so
    # reaching this branch always means "the developer has not agreed to go
    # back to THIS candidate yet" -- whether or not a request row exists yet.
    # `pending_back_request_state` therefore only tells the caller whether it
    # still has to create the row.
    return _decide(
        reached_state,
        next_reached=None,
        hold=True,
        cause=CAUSE_KIND_BY_RULE.get(rule_row),
    )


# --- Process run lifecycle (fact B) -----------------------------------------


def process_stuck_after_seconds() -> float:
    """A running record older than this is swept to a failure record.

    Without the sweep a process killed mid-request (server restart, crash)
    would leave `W1` displayed forever, which is exactly the "state does not
    match reality" problem the spec exists to remove.
    """
    try:
        return float(os.getenv("INTERVIEW_PROCESS_STUCK_AFTER_SECONDS", "900"))
    except ValueError:
        return 900.0


def classify_failure(conn, session_id: int, system_id: int, process_kind: str) -> Tuple[str, Optional[str]]:
    """Deterministic (`failure_class`, `target_state`) for a failed run.

    Spec §5.1: the class is decided by whether material to keep deciding with
    remains at the moment of failure, NOT by which process failed.

    * understanding build/update -- blocking (`E3-a`) only when no question
      can be offered; otherwise degraded (`E3-b`, the zero-base path).
    * alignment build -- blocking (`E4-a`) only on the first build, i.e. when
      no non-superseded item exists; otherwise degraded (`E4-b`).
    * diff generation -- always blocking (`E12` generation failure): with no
      diff there is nothing to review.
    * everything else -- degraded.
    """
    target = PROCESS_BLOCKING_TARGET.get(process_kind)
    if target is None:
        return "degraded", None

    if process_kind in ("understanding_build", "understanding_update"):
        offerable = conn.execute(
            """SELECT COUNT(*) FROM interview_qa
               WHERE session_id = ? AND system_id = ? AND status = 'open'""",
            (session_id, system_id),
        ).fetchone()[0]
        if offerable > 0:
            return "degraded", None
        return "blocking", target

    if process_kind == "alignment_build":
        existing = conn.execute(
            """SELECT COUNT(*) FROM alignment_item
               WHERE session_id = ? AND system_id = ? AND superseded = 0""",
            (session_id, system_id),
        ).fetchone()[0]
        if existing > 0:
            return "degraded", None
        return "blocking", target

    return "blocking", target


def start_process_run(conn, session_id: int, system_id: int, process_kind: str) -> int:
    """Persist "this process is running" (spec fact B). Caller must not hold
    this connection across the process' own external calls (see app/db.py)."""
    if process_kind not in PROCESS_KINDS:
        raise ValueError(f"Unknown interview process kind: {process_kind}")
    # A run record is scoped to a real session of this System. When the
    # session does not exist the caller is about to 404 anyway, and there is
    # no session whose state the run could describe.
    session = conn.execute(
        "SELECT id FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if session is None:
        return 0
    now = time.time()
    cur = conn.execute(
        """INSERT INTO interview_process_run
               (session_id, system_id, process_kind, status, started_at, heartbeat_at)
           VALUES (?, ?, ?, 'running', ?, ?)""",
        (session_id, system_id, process_kind, now, now),
    )
    return int(cur.lastrowid)


def finish_process_run(
    conn, run_id: int, *, ok: bool, error: Optional[str] = None
) -> None:
    """Close a running record as succeeded or failed.

    A failure is classified here (blocking vs degraded, and the blocked
    target state) so the failure's display location is a persisted fact and
    not something the Dashboard guesses.
    """
    row = conn.execute(
        "SELECT * FROM interview_process_run WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None or row["status"] != "running":
        return
    now = time.time()
    if ok:
        conn.execute(
            """UPDATE interview_process_run
                   SET status = 'succeeded', finished_at = ?, heartbeat_at = ?
                 WHERE id = ?""",
            (now, now, run_id),
        )
        return
    failure_class, target_state = classify_failure(
        conn, row["session_id"], row["system_id"], row["process_kind"]
    )
    conn.execute(
        """UPDATE interview_process_run
               SET status = 'failed', failure_class = ?, target_state = ?,
                   error = ?, finished_at = ?, heartbeat_at = ?
             WHERE id = ?""",
        (failure_class, target_state, (error or "")[:4000], now, now, run_id),
    )


class process_run:
    """Context manager recording one `W1`-producing process run.

    Opens and closes its own short-lived connections: the wrapped body may
    make LLM/subprocess calls, and the process-wide SQLite lock must never be
    held across one (see app/db.py and the control-server skill).

        with process_run(session_id, system_id, "alignment_build"):
            ...  # any exception is recorded as a failure and re-raised
    """

    def __init__(self, session_id: int, system_id: int, process_kind: str):
        self.session_id = session_id
        self.system_id = system_id
        self.process_kind = process_kind
        self.run_id: Optional[int] = None

    def __enter__(self) -> "process_run":
        from .db import get_conn

        with get_conn() as conn:
            run_id = start_process_run(
                conn, self.session_id, self.system_id, self.process_kind
            )
        self.run_id = run_id or None
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        from .db import get_conn

        if self.run_id is None:
            return False
        detail = None
        if exc is not None:
            detail = getattr(exc, "detail", None)
            detail = str(detail) if detail is not None else str(exc)
        with get_conn() as conn:
            finish_process_run(
                conn, self.run_id, ok=exc_type is None, error=detail
            )
        return False


class ProcessRunTracker:
    """Explicit start/succeed/fail/abandon form of `process_run`.

    Used where the tracked function's own precondition rejections must NOT
    become failure records: a 404/409 "you have not built the understanding
    yet" is a statement about the request, not a run that failed, and turning
    it into an unresolved blocking failure would wedge the screen on a state
    the developer cannot recover from by retrying.

    Every method owns a short-lived connection, so it is safe to call around
    an LLM/subprocess call (see app/db.py).
    """

    def __init__(self, session_id: int, system_id: int, process_kind: str):
        self.session_id = session_id
        self.system_id = system_id
        self.process_kind = process_kind
        self.run_id: Optional[int] = None

    def start(self) -> None:
        from .db import get_conn

        with get_conn() as conn:
            run_id = start_process_run(
                conn, self.session_id, self.system_id, self.process_kind
            )
        self.run_id = run_id or None

    def succeed(self) -> None:
        from .db import get_conn

        if self.run_id is None:
            return
        with get_conn() as conn:
            finish_process_run(conn, self.run_id, ok=True)
        self.run_id = None

    def fail(self, error: object) -> None:
        from .db import get_conn

        if self.run_id is None:
            return
        detail = getattr(error, "detail", None)
        message = str(detail) if detail is not None else str(error)
        with get_conn() as conn:
            finish_process_run(conn, self.run_id, ok=False, error=message)
        self.run_id = None

    def abandon(self) -> None:
        """Delete the record: the run never actually started (precondition)."""
        from .db import get_conn

        if self.run_id is None:
            return
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM interview_process_run WHERE id = ? AND status = 'running'",
                (self.run_id,),
            )
        self.run_id = None


def tracked_process(
    process_kind: str,
    *,
    abandon_statuses: Tuple[int, ...] = (404, 409, 422),
    skipped_attr: Optional[str] = None,
):
    """Decorate a `session_id` / `system_id` endpoint with a run record.

    `abandon_statuses` are the HTTP status codes this endpoint uses for
    precondition rejections (nothing ran, so nothing failed); `skipped_attr`
    names a truthy attribute on the return value meaning "deliberately not
    executed" (the Runtime Reality Check's finite skip condition), which is
    likewise not a run.

    `functools.wraps` keeps the wrapped signature visible to FastAPI, so the
    route contract is unchanged.
    """
    import functools

    def decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            session_id = kwargs.get("session_id")
            system_id = kwargs.get("system_id")
            if session_id is None or system_id is None:
                return func(*args, **kwargs)
            tracker = ProcessRunTracker(session_id, system_id, process_kind)
            tracker.start()
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - recorded then re-raised
                status = getattr(exc, "status_code", None)
                if status in abandon_statuses:
                    tracker.abandon()
                else:
                    tracker.fail(exc)
                raise
            if skipped_attr and getattr(result, skipped_attr, False):
                tracker.abandon()
            else:
                tracker.succeed()
            return result

        return wrapper

    return decorate


def sweep_stale_process_runs(conn, session_id: int, system_id: int) -> int:
    """Turn abandoned running records into failure records (idempotent)."""
    cutoff = time.time() - process_stuck_after_seconds()
    rows = conn.execute(
        """SELECT id FROM interview_process_run
           WHERE session_id = ? AND system_id = ? AND status = 'running'
             AND COALESCE(heartbeat_at, started_at) < ?""",
        (session_id, system_id, cutoff),
    ).fetchall()
    for row in rows:
        finish_process_run(
            conn,
            row["id"],
            ok=False,
            error="処理が完了しないまま中断されました。もう一度実行してください。",
        )
    return len(rows)


# --- Persisted-fact gathering -----------------------------------------------


def diff_digest(diff: Optional[str]) -> str:
    return hashlib.sha256((diff or "").encode("utf-8")).hexdigest()


def _has_understanding_content(understanding: Optional[dict]) -> bool:
    if not understanding:
        return False
    for key in (
        "system_purpose",
        "core_capabilities",
        "capability_elements",
        "supporting_elements",
        "api_boundaries",
        "probe_flow_candidates",
    ):
        if understanding.get(key):
            return True
    return False


def _unresolved_blocking_failures(conn, session_id: int, system_id: int) -> List[dict]:
    """Failed blocking runs with no later success of the same kind.

    "Unresolved" is derived, never stored, so ANY later success of the same
    process kind (a manual retry or the automatic refresh) clears it the same
    way -- and a suspend/resume never clears it at all (spec §5.3-6).
    """
    rows = conn.execute(
        """SELECT * FROM interview_process_run
           WHERE session_id = ? AND system_id = ? AND status = 'failed'
             AND failure_class = 'blocking' AND target_state IS NOT NULL
           ORDER BY id DESC""",
        (session_id, system_id),
    ).fetchall()
    unresolved: List[dict] = []
    seen_kinds: set = set()
    for row in rows:
        kind = row["process_kind"]
        if kind in seen_kinds:
            continue
        seen_kinds.add(kind)
        later_success = conn.execute(
            """SELECT id FROM interview_process_run
               WHERE session_id = ? AND system_id = ? AND process_kind = ?
                 AND status = 'succeeded' AND id > ?
               LIMIT 1""",
            (session_id, system_id, kind, row["id"]),
        ).fetchone()
        if later_success is None:
            unresolved.append(dict(row))
    return unresolved


def _degraded_failures(conn, session_id: int, system_id: int) -> List[dict]:
    rows = conn.execute(
        """SELECT * FROM interview_process_run
           WHERE session_id = ? AND system_id = ? AND status = 'failed'
             AND failure_class = 'degraded'
           ORDER BY id DESC""",
        (session_id, system_id),
    ).fetchall()
    out: List[dict] = []
    seen: set = set()
    for row in rows:
        kind = row["process_kind"]
        if kind in seen:
            continue
        seen.add(kind)
        later_success = conn.execute(
            """SELECT id FROM interview_process_run
               WHERE session_id = ? AND system_id = ? AND process_kind = ?
                 AND status = 'succeeded' AND id > ?
               LIMIT 1""",
            (session_id, system_id, kind, row["id"]),
        ).fetchone()
        if later_success is None:
            out.append(dict(row))
    return out


_ACTIONABLE_ALIGNMENT_CATEGORIES = ("must_review", "batch_reviewable")


@dataclass
class GatheredFacts:
    facts: WorkflowFacts
    session: Optional[dict] = None
    running_runs: List[dict] = field(default_factory=list)
    blocking_failures: List[dict] = field(default_factory=list)
    degraded_failures: List[dict] = field(default_factory=list)
    snapshot_stale: bool = False
    proposals_needing_rereview: int = 0
    diff_skipped_count: int = 0
    latest_ready_snapshot_id: Optional[int] = None


def gather_facts(conn, system_id: int, session_id: Optional[int]) -> GatheredFacts:
    """Read every persisted fact the rule table needs, for one session.

    Every value comes from a row that survives a reload; nothing here reads a
    request-scoped or client-supplied value.
    """
    import json as _json

    from . import state_facts

    latest_snapshot_id = state_facts.get_latest_ready_snapshot_id(conn, system_id)
    has_snapshot = latest_snapshot_id is not None

    if session_id is None:
        return GatheredFacts(
            facts=WorkflowFacts(has_snapshot=has_snapshot, has_session=False),
            latest_ready_snapshot_id=latest_snapshot_id,
        )

    session = conn.execute(
        "SELECT * FROM interview_session WHERE id = ? AND system_id = ?",
        (session_id, system_id),
    ).fetchone()
    if session is None:
        return GatheredFacts(
            facts=WorkflowFacts(has_snapshot=has_snapshot, has_session=False),
            latest_ready_snapshot_id=latest_snapshot_id,
        )

    sweep_stale_process_runs(conn, session_id, system_id)

    running_rows = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM interview_process_run
               WHERE session_id = ? AND system_id = ? AND status = 'running'
               ORDER BY id""",
            (session_id, system_id),
        ).fetchall()
    ]
    blocking = _unresolved_blocking_failures(conn, session_id, system_id)
    degraded = _degraded_failures(conn, session_id, system_id)

    understanding = (
        _json.loads(session["current_understanding"])
        if session["current_understanding"]
        else None
    )
    has_content = _has_understanding_content(understanding)
    confirmed_at = (
        session["understanding_confirmed_at"]
        if "understanding_confirmed_at" in session.keys()
        else None
    )
    capability_recheck = _capability_confirmation_required(conn, session)
    understanding_unconfirmed = bool(
        has_content and (confirmed_at is None or capability_recheck)
    )

    open_questions = conn.execute(
        """SELECT COUNT(*) FROM interview_qa
           WHERE session_id = ? AND system_id = ? AND status = 'open'
             AND superseded_by_id IS NULL""",
        (session_id, system_id),
    ).fetchone()[0]

    placeholders = ",".join("?" for _ in _ACTIONABLE_ALIGNMENT_CATEGORIES)
    outstanding_alignment = conn.execute(
        f"""SELECT COUNT(*) FROM alignment_item
            WHERE session_id = ? AND system_id = ?
              AND review_category IN ({placeholders})
              AND status NOT IN ('answered', 'corrected') AND superseded = 0""",
        (session_id, system_id, *_ACTIONABLE_ALIGNMENT_CATEGORIES),
    ).fetchone()[0]

    proposal_rows = conn.execute(
        "SELECT approval_state FROM interview_proposal WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    needing_review = sum(
        1 for r in proposal_rows if r["approval_state"] in ("proposed", "needs_review")
    )
    needs_rereview = sum(
        1 for r in proposal_rows if r["approval_state"] == "needs_review"
    )
    approved = sum(
        1 for r in proposal_rows if r["approval_state"] in ("approved", "edited")
    )
    proposals_unlocked = bool(has_content or confirmed_at is not None)
    proposals_generatable = bool(len(proposal_rows) == 0 and proposals_unlocked)

    materialized_at = session["materialized_at"]
    diff_text = session["materialization_diff"]
    last_decision = conn.execute(
        """SELECT MAX(decided_at) FROM interview_proposal_decision
           WHERE session_id = ? AND system_id = ?""",
        (session_id, system_id),
    ).fetchone()[0]
    # "The diff corresponding to the current approval set" is decided
    # structurally (spec §2.2 note on row 11): the diff must have been
    # generated AFTER the last approve/edit/reject. No extra persisted fact.
    diff_matches = bool(
        materialized_at is not None
        and diff_text
        and (last_decision is None or materialized_at >= last_decision)
    )

    review_row = conn.execute(
        """SELECT id FROM interview_diff_review
           WHERE session_id = ? AND system_id = ? AND diff_materialized_at = ?
           ORDER BY id DESC LIMIT 1""",
        (session_id, system_id, materialized_at),
    ).fetchone() if materialized_at is not None else None

    pending_handoffs = conn.execute(
        """SELECT COUNT(*) FROM question_handoff
           WHERE session_id = ? AND system_id = ? AND status IN ('pending', 'answered')""",
        (session_id, system_id),
    ).fetchone()[0]

    facts = WorkflowFacts(
        has_snapshot=has_snapshot,
        has_session=True,
        session_closed=(session["status"] == "closed"),
        running_process_kinds=tuple(r["process_kind"] for r in running_rows),
        blocking_failure_states=frozenset(
            r["target_state"] for r in blocking if r["target_state"]
        ),
        understanding_unconfirmed=understanding_unconfirmed,
        open_required_questions=open_questions,
        outstanding_alignment_items=outstanding_alignment,
        proposals_needing_review=needing_review,
        proposals_generatable=proposals_generatable,
        approved_proposal_count=approved,
        diff_matches_approval_set=diff_matches,
        diff_review_complete=review_row is not None,
        pending_handoff_count=pending_handoffs,
    )
    return GatheredFacts(
        facts=facts,
        session=dict(session),
        running_runs=running_rows,
        blocking_failures=blocking,
        degraded_failures=degraded,
        snapshot_stale=bool(
            latest_snapshot_id is not None
            and session["snapshot_id"] != latest_snapshot_id
        ),
        proposals_needing_rereview=needs_rereview,
        latest_ready_snapshot_id=latest_snapshot_id,
    )


def _capability_confirmation_required(conn, session) -> bool:
    """#312 cascade: the Capability composition needs re-confirmation.

    Reproduces `routes/interview._session_out`'s
    `capability_graph_confirmation_required` predicate so the workflow state
    and the session payload can never disagree about whether W2 is open.
    """
    if not session["current_understanding"]:
        return False
    latest_revision = conn.execute(
        """SELECT id FROM understanding_revision
           WHERE session_id = ? AND system_id = ?
           ORDER BY id DESC LIMIT 1""",
        (session["id"], session["system_id"]),
    ).fetchone()
    if latest_revision is None:
        return False
    graph_confirmation = conn.execute(
        """SELECT id, source_revision_id FROM understanding_capability_confirmation
           WHERE session_id = ? AND system_id = ?
           ORDER BY id DESC LIMIT 1""",
        (session["id"], session["system_id"]),
    ).fetchone()
    system_graph_confirmation = conn.execute(
        """SELECT id FROM understanding_capability_confirmation
           WHERE system_id = ? ORDER BY id DESC LIMIT 1""",
        (session["system_id"],),
    ).fetchone()
    confirmed_revision_id = (
        graph_confirmation["source_revision_id"] if graph_confirmation else None
    )
    return bool(
        confirmed_revision_id != latest_revision["id"]
        or graph_confirmation is None
        or system_graph_confirmation is None
        or graph_confirmation["id"] != system_graph_confirmation["id"]
    )


# --- Full evaluation with persistence of the system-recorded facts ----------


def evaluate_session_workflow(
    conn, system_id: int, session_id: Optional[int]
) -> Tuple[WorkflowDecision, GatheredFacts, Optional[dict]]:
    """Evaluate both stages and persist the two system-recorded facts.

    Writes only fact C: the `reached_state` checkpoint and, when the display
    is held back, the backward request. Neither is a human gate; the gate is
    the acknowledgement (fact D), which only the developer's explicit call to
    the acknowledge endpoint can create.

    Returns ``(decision, gathered, pending_back_request_row_or_None)``.
    """
    gathered = gather_facts(conn, system_id, session_id)
    if session_id is None or gathered.session is None:
        decision = apply_backward_hold(gathered.facts, reached_state=None)
        return decision, gathered, None

    checkpoint = conn.execute(
        "SELECT reached_state FROM interview_workflow_checkpoint WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    reached_state = checkpoint["reached_state"] if checkpoint else None

    pending = conn.execute(
        """SELECT * FROM interview_back_request
           WHERE session_id = ? AND system_id = ? AND status = 'pending'
           ORDER BY id DESC LIMIT 1""",
        (session_id, system_id),
    ).fetchone()

    decision = apply_backward_hold(gathered.facts, reached_state=reached_state)

    now = time.time()
    if decision.next_reached_state and decision.next_reached_state != reached_state:
        conn.execute(
            """INSERT INTO interview_workflow_checkpoint
                   (session_id, system_id, reached_state, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   reached_state = excluded.reached_state,
                   updated_at = excluded.updated_at""",
            (session_id, system_id, decision.next_reached_state, now),
        )

    if decision.backward_hold:
        if pending is None:
            cur = conn.execute(
                """INSERT INTO interview_back_request
                       (session_id, system_id, cause_kind, candidate_state,
                        reached_state, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    session_id,
                    system_id,
                    decision.cause_kind or "unknown",
                    decision.candidate_state,
                    decision.state,
                    now,
                    now,
                ),
            )
            pending = conn.execute(
                "SELECT * FROM interview_back_request WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        elif (
            pending["candidate_state"] != decision.candidate_state
            or pending["cause_kind"] != (decision.cause_kind or "unknown")
        ):
            # The reason to go back changed while still unacknowledged; keep
            # ONE pending request per session so the developer always sees a
            # single 1-操作 導線 (spec §2.2 stage 2-3).
            conn.execute(
                """UPDATE interview_back_request
                       SET cause_kind = ?, candidate_state = ?, reached_state = ?,
                           updated_at = ?
                     WHERE id = ?""",
                (
                    decision.cause_kind or "unknown",
                    decision.candidate_state,
                    decision.state,
                    now,
                    pending["id"],
                ),
            )
            pending = conn.execute(
                "SELECT * FROM interview_back_request WHERE id = ?", (pending["id"],)
            ).fetchone()
    elif pending is not None:
        # The facts moved forward on their own -- the request no longer
        # describes anything, so the system resolves it. Resolving is not an
        # acknowledgement: no fact D row is written and nothing moves back.
        conn.execute(
            """UPDATE interview_back_request SET status = 'resolved', updated_at = ?
               WHERE id = ?""",
            (now, pending["id"]),
        )
        pending = None

    return decision, gathered, (dict(pending) if pending is not None else None)
