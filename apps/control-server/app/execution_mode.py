"""Execution modes: the canonical contract and the fail-closed capability gate
(Epic #412, Issue #413).

Canonical contract: `docs/execution-modes.md`. Read §0 before touching this
area.

The execution mode is the **fifth independent axis** (`docs/evolutionary-
pipeline.md` ADR-6). It is never derived from, and never merged into, Node
maturity (`evolution_node.maturity`), Cell Improvement status
(`cell_improvement`), the SDK policy mode (`components.mode`), or the
Dashboard workflow phase (#237/#256/#349). In particular the SDK policy
`shadow` (does the SDK run the candidate function and send `shadow_results`?)
and the execution mode `shadow` (does the control plane permit candidate
comparison at all?) are two different facts, and a Node being one without the
other is a legitimate state that a projection must show as two readings
(#366's "one displayed word must not carry two facts").

`fixed` is deliberately NOT "a setting that disables the LLM". It is the state
in which the credential path, the client-construction path, and the
candidate-execution permission are all **unreachable** -- which is what
`build_experiment_llm_adapter` enforces by ORDER: the capability gate runs
before the first line that could read a credential (§4.2 / EM-ADR-3).

Module shape mirrors `app/evolution_node.py`: a pure, DB-free evaluator
(`classify_scope_state`, `resolve_execution_mode`) over frozen facts values,
plus a DB-touching layer (`load_mode_facts`, `assign_mode`, `revoke_mode`,
`record_observation`, `evaluate_divergence`, `build_mode_projection`) that
reads and writes through a connection the CALLER owns. Nothing here calls
`db.get_conn()` itself -- see `CLAUDE.md`'s "never hold a `get_conn()`
connection across an external call" rule.

Decision classification (Principle 6/7): every value this module produces
belongs to an explicitly enumerated finite set. There is no heuristic
fallback anywhere: unknown, missing, conflicting and stale all resolve to
`fixed`, which is the most restrictive mode. A reasoning model is never asked
what the mode is, and `decision_method` on an assignment is always `manual` --
the subject that changes an execution mode over HTTP is a human (§5.1).

probe-agent:
  role: Canonical execution-mode resolution, fail-closed capability gate, append-only assignment audit and runtime-divergence reading
  capability: execution-mode-control
  element_type: core
  consumers: [control-server-routes]
  operation_kind: mixed
  state_effects: [database-read, database-write]
  probe_value: Verify each of the ten resolution rows is reached independently, that an expired assignment never falls through to a broader scope while a revoked one does, that build_experiment_llm_adapter raises before any credential is read, that `propose` cannot execute a candidate, and that no assignment of another System is ever visible.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    get_args,
)

from . import llm
from .models import (
    ExecutionCapability,
    ExecutionMode,
    ExecutionModeDenialCode,
    ExecutionModeDivergence,
    ExecutionModeObservationSource,
    ExecutionModeReason,
    ExecutionModeRecordKind,
    ExecutionModeRunRefState,
    ExecutionModeScopeKind,
    ExecutionModeScopeState,
    ExecutionModeSourceScope,
)

__all__ = [
    "ExecutionMode",
    "EXECUTION_MODES",
    "ExecutionModeScopeKind",
    "EXECUTION_MODE_SCOPE_KINDS",
    "ExecutionModeScopeState",
    "EXECUTION_MODE_SCOPE_STATES",
    "ExecutionCapability",
    "EXECUTION_CAPABILITIES",
    "ExecutionModeReason",
    "EXECUTION_MODE_REASONS",
    "ExecutionModeSourceScope",
    "EXECUTION_MODE_SOURCE_SCOPES",
    "ExecutionModeDenialCode",
    "EXECUTION_MODE_DENIAL_CODES",
    "ExecutionModeDivergence",
    "EXECUTION_MODE_DIVERGENCES",
    "ExecutionModeRecordKind",
    "EXECUTION_MODE_RECORD_KINDS",
    "ExecutionModeObservationSource",
    "EXECUTION_MODE_OBSERVATION_SOURCES",
    "ExecutionModeRunRefState",
    "EXECUTION_MODE_RUN_REF_STATES",
    "HTTP_OBSERVATION_SOURCE",
    "MODE_CAPABILITIES",
    "MODE_PERMISSIVENESS",
    "FAIL_CLOSED_MODE",
    "FLOW_SCOPE_PREFIX",
    "EXECUTION_MODE_ASSIGNMENT_SCHEMA_VERSION",
    "EXECUTION_MODE_PROJECTION_SCHEMA_VERSION",
    "ExecutionModeError",
    "ExecutionModeNotFoundError",
    "ExecutionModeConflictError",
    "ExecutionModeValidationError",
    "ExecutionModeDenied",
    "ScopeReading",
    "ModeFacts",
    "ExecutionModeDecision",
    "DivergenceReading",
    "ModeProjectionNode",
    "ModeProjection",
    "normalize_flow_scope_ref",
    "permitted_capabilities",
    "classify_scope_state",
    "resolve_execution_mode",
    "load_mode_facts",
    "require_capability",
    "build_experiment_llm_adapter",
    "assign_mode",
    "revoke_mode",
    "list_assignments",
    "record_observation",
    "evaluate_divergence",
    "build_mode_projection",
    "assignment_doc",
    "observation_doc",
]


# ---------------------------------------------------------------------------
# Finite vocabularies
# ---------------------------------------------------------------------------
#
# The `Literal` aliases live in `app/models.py` (so FastAPI puts a real enum
# in the OpenAPI schema instead of a bare string) and are mirrored here as
# `get_args()`-derived tuples, the same pairing `app/understanding_brief.py`
# uses. Keeping both forms means a membership check and a static annotation
# can never drift apart.

EXECUTION_MODES: Tuple[str, ...] = get_args(ExecutionMode)
EXECUTION_MODE_SCOPE_KINDS: Tuple[str, ...] = get_args(ExecutionModeScopeKind)
EXECUTION_MODE_SCOPE_STATES: Tuple[str, ...] = get_args(ExecutionModeScopeState)
EXECUTION_CAPABILITIES: Tuple[str, ...] = get_args(ExecutionCapability)
EXECUTION_MODE_REASONS: Tuple[str, ...] = get_args(ExecutionModeReason)
EXECUTION_MODE_SOURCE_SCOPES: Tuple[str, ...] = get_args(ExecutionModeSourceScope)
EXECUTION_MODE_DENIAL_CODES: Tuple[str, ...] = get_args(ExecutionModeDenialCode)
EXECUTION_MODE_DIVERGENCES: Tuple[str, ...] = get_args(ExecutionModeDivergence)
EXECUTION_MODE_RECORD_KINDS: Tuple[str, ...] = get_args(ExecutionModeRecordKind)
EXECUTION_MODE_OBSERVATION_SOURCES: Tuple[str, ...] = get_args(
    ExecutionModeObservationSource
)
EXECUTION_MODE_RUN_REF_STATES: Tuple[str, ...] = get_args(ExecutionModeRunRefState)

#: The only `source` an HTTP caller can produce. `sdk` is a claim no current
#: path can attest, so it is not settable over the API (§5.2 / #337).
HTTP_OBSERVATION_SOURCE: str = "control_server"

# Reserved for observations emitted by the execution boundary itself after
# it created the canonical execution row.  The public observation endpoint
# rejects this prefix, so a caller cannot upgrade a self-reported pointer to
# corroborated provenance merely by choosing a convincing string.
ATTESTED_RUN_REF_PREFIX: str = "execution_gate:"

#: The COMPLETE mode -> capability table of §1.3. Deliberately a total
#: mapping rather than a first-match rule list: every mode names exactly what
#: it permits, so adding a mode without deciding its capabilities is an
#: immediate KeyError rather than a silent inheritance.
#:
#: `propose` not holding `candidate_execution` is the design, not an
#: oversight: a proposal is a plan, not an execution. Reaching execution needs
#: two independent facts -- a human approval AND a promotion to `shadow`
#: (§7.4/§7.5).
MODE_CAPABILITIES: Dict[str, FrozenSet[str]] = {
    "fixed": frozenset(),
    "observe": frozenset({"observation_record"}),
    "propose": frozenset({"observation_record", "llm_experiment_proposal"}),
    "shadow": frozenset(EXECUTION_CAPABILITIES),
}

#: `fixed < observe < propose < shadow`. This ordering exists for exactly ONE
#: purpose: to name which end is the safe one to fall to when a fact cannot be
#: read (`FAIL_CLOSED_MODE`). It is never a score, a progress measure, or an
#: input to any comparison the resolver makes -- §3.3 is a first-match table
#: over scope states, not a max/min over permissiveness.
MODE_PERMISSIVENESS: Dict[str, int] = {
    "fixed": 0,
    "observe": 1,
    "propose": 2,
    "shadow": 3,
}

#: Where every unreadable, conflicting, invalid or expired fact resolves to.
FAIL_CLOSED_MODE: str = "fixed"

#: `scope_ref` always carries its prefix so `static_flow` and `runtime_flow`
#: can never be collapsed into one word (#405's rule, restated in §2.1).
#: `static_flow` is deliberately NOT a mode scope (EM-ADR-1): resolving a
#: static flow's Node membership requires recomputing a call graph, and a
#: fail-closed gate must not depend on a derivation that can itself fail.
FLOW_SCOPE_PREFIX = "runtime_flow:"

EXECUTION_MODE_ASSIGNMENT_SCHEMA_VERSION = "execution-mode-assignment-v1"
EXECUTION_MODE_PROJECTION_SCHEMA_VERSION = "execution-mode-projection-v1"

#: Reason codes from §3.3 that are ALSO denial codes in §4.1. When one of
#: these decided the mode, `require_capability` reports it verbatim instead of
#: the generic `capability_not_permitted`: the developer needs to know that a
#: deadline elapsed or that two flows disagree, not merely that the answer was
#: "no".
_REASON_DENIAL_CODES: FrozenSet[str] = frozenset({
    "conflicting_assignments",
    "invalid_mode_value",
    "flow_scope_not_member",
    "node_expired_assignment",
    "flow_expired_assignment",
    "system_expired_assignment",
    "flow_scope_conflict",
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExecutionModeError(ValueError):
    """Base fail-closed error for the execution-mode layer."""


class ExecutionModeNotFoundError(ExecutionModeError):
    """A referenced row does not exist, or belongs to another System.

    The two are deliberately indistinguishable (routes map this to 404) so
    another System's ids stay unprobeable.
    """


class ExecutionModeConflictError(ExecutionModeError):
    """A structural precondition failed (routes map this to 409)."""


class ExecutionModeValidationError(ExecutionModeError):
    """A payload was malformed or used an unknown vocabulary value
    (routes map this to 422)."""


class ExecutionModeDenied(ExecutionModeError):
    """The capability gate refused (routes map this to 409).

    Carries a finite `denial_code` (§4.1) and, when one could be computed, the
    `ExecutionModeDecision` that produced it -- so the caller can read WHY it
    was refused and WHICH scope's setting is in effect in one answer.

    `decision` is `None` for exactly the two codes that describe a broken
    REQUEST rather than a mode reading: `unknown_capability` (a capability
    outside the finite vocabulary has no permission answer at all) and
    `node_not_found` (the gate's own subject could not be resolved, so
    reporting a mode for it would be asserting a fact about something that
    does not exist -- #380's "an unreadable fact is never a fact's default
    value").
    """

    def __init__(
        self,
        denial_code: str,
        message: str,
        decision: Optional["ExecutionModeDecision"] = None,
    ) -> None:
        super().__init__(message)
        self.denial_code = denial_code
        self.decision = decision


# ---------------------------------------------------------------------------
# Pure data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeReading:
    """One scope's current state, as read from its assignment rows.

    `state` is one of `EXECUTION_MODE_SCOPE_STATES`; `mode` is the mode the
    latest `assign` row names and is `None` for every state in which no mode
    is in effect. The two are separate fields because "there is a row saying
    `shadow` but its window elapsed" and "there is no row" are different
    facts.
    """

    scope_kind: str
    scope_ref: str
    state: str
    mode: Optional[str] = None
    assignment_id: Optional[int] = None
    effective_from: Optional[float] = None
    effective_until: Optional[float] = None
    open_row_count: int = 0


@dataclass(frozen=True)
class ModeFacts:
    """Every persisted fact `resolve_execution_mode` reads.

    Nothing else may influence the decision -- the same discipline
    `app/interview_workflow.py`'s `WorkflowFacts` and
    `app/evolution_node.py`'s `NodeFacts` follow. `now` is carried here (not
    read inside the resolver) so a boundary instant is testable exactly.

    `node_reading` is `None` when no Node was named -- which is different from
    a named Node that has no assignment (that is a `ScopeReading` in state
    `unset`).

    There is deliberately NO `node_exists` field. It used to be recorded here
    and then ignored by `resolve_execution_mode`, so the same missing Node
    read as `default` / `fixed` through `resolve` and the projection while
    `require_capability` refused it with `node_not_found` -- one subject, two
    answers, and the read was the more permissive of the two. A fact set is no
    longer built for a Node that does not exist at all: `load_mode_facts`
    raises `ExecutionModeNotFoundError`, so "no such Node" and "a Node nobody
    configured" can never collapse into one reading (#380).
    """

    system_reading: ScopeReading
    flow_readings: Tuple[ScopeReading, ...] = ()
    node_reading: Optional[ScopeReading] = None
    node_key: Optional[str] = None
    #: Flow scope refs the CALLER named that the named Node does not actually
    #: belong to. A caller's claim about scope is never evidence of scope: the
    #: Flows that decide a Node's mode come only from
    #: `evolution_node_link(link_kind='flow')`. Unioning the caller's refs into
    #: the resolver's Flow set let anyone who could name a permissive Flow
    #: reach that Flow's permission for a Node that was `fixed` on its own.
    #: The rejected claim is CARRIED rather than dropped, because silently
    #: ignoring it would leave the caller believing a Flow's permission applied
    #: when it did not -- the same defect seen from the other side.
    rejected_flow_claims: Tuple[str, ...] = ()
    now: float = 0.0


@dataclass(frozen=True)
class ExecutionModeDecision:
    """The resolver's verdict.

    `scope_trace` records every scope that was consulted, in the order the
    resolver consults them (node, then flows, then system), so a UI can show
    WHY this mode is in effect rather than only that it is.
    """

    mode: str
    source_scope: str
    source_ref: str
    reason: str
    scope_trace: Tuple[ScopeReading, ...] = ()

    @property
    def capabilities(self) -> Tuple[str, ...]:
        return permitted_capabilities(self.mode)


@dataclass(frozen=True)
class DivergenceReading:
    """One Node's configured-vs-observed reading (§5.2)."""

    node_key: str
    divergence: str
    effective_mode: str
    observed_mode: Optional[str] = None
    observed_at: Optional[float] = None
    last_assignment_at: Optional[float] = None
    decision: Optional[ExecutionModeDecision] = None
    #: WHERE the observation came from and whether its citation was checked.
    #: The divergence value alone answers "does the reading agree with the
    #: configuration?"; it cannot answer "was the reading measured?". Those
    #: are two facts and one word must not carry both (#366). Publicly reported
    #: pointers remain `uncorroborated`; an execution-boundary pointer is
    #: `corroborated`. Both fields are `None` for `unobserved`.
    observation_source: Optional[str] = None
    run_ref_state: Optional[str] = None


@dataclass(frozen=True)
class ModeProjectionNode:
    """One Node's row in the projection.

    `maturity` is carried alongside the execution mode precisely BECAUSE the
    two are independent axes (ADR-6): showing them side by side as separate
    fields is what keeps a reader from inferring one from the other. It is
    read from `evolution_node.maturity` and never computed here.
    """

    node_id: int
    node_key: str
    maturity: str
    execution_mode: str
    mode_source: str
    mode_reason: str
    source_ref: str
    flow_refs: Tuple[str, ...]
    divergence: str
    observed_mode: Optional[str] = None
    observed_at: Optional[float] = None
    #: The observation's own standing, carried through unchanged from
    #: `DivergenceReading`. A `match` produced from a hand-reported value and
    #: one produced from an attested reading are different facts, and the
    #: aggregation screens must be able to tell them apart (#366).
    observation_source: Optional[str] = None
    run_ref_state: Optional[str] = None


@dataclass(frozen=True)
class ModeProjection:
    system_id: int
    schema_version: str
    generated_at: float
    system_decision: ExecutionModeDecision
    assignments: Tuple[Dict[str, Any], ...] = ()
    nodes: Tuple[ModeProjectionNode, ...] = ()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _check_membership(value: Any, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise ExecutionModeValidationError(
            f"{field_name} must be one of {list(vocabulary)}, got {value!r}"
        )


def normalize_flow_scope_ref(flow_ref: str) -> str:
    """Return the stored form of a flow scope ref: `runtime_flow:<flow_id>`.

    Accepts either the bare runtime `flow_id` (which is what
    `evolution_node_link.target_ref` stores for `link_kind='flow'`) or the
    already-prefixed form, so a caller can pass whichever it holds without
    two spellings ever reaching the database.
    """
    ref = (flow_ref or "").strip()
    if not ref:
        raise ExecutionModeValidationError("flow_ref must not be empty")
    if ref.startswith(FLOW_SCOPE_PREFIX):
        body = ref[len(FLOW_SCOPE_PREFIX):].strip()
        if not body:
            raise ExecutionModeValidationError("flow_ref must name a flow_id")
        return FLOW_SCOPE_PREFIX + body
    return FLOW_SCOPE_PREFIX + ref


def permitted_capabilities(mode: str) -> Tuple[str, ...]:
    """The capabilities `mode` permits, in the fixed vocabulary order.

    An unknown mode permits nothing -- fail closed, never a guess.
    """
    allowed = MODE_CAPABILITIES.get(mode, frozenset())
    return tuple(cap for cap in EXECUTION_CAPABILITIES if cap in allowed)


# ---------------------------------------------------------------------------
# The pure evaluators
# ---------------------------------------------------------------------------


def classify_scope_state(
    rows: Sequence[Mapping[str, Any]],
    *,
    scope_kind: str,
    scope_ref: str,
    now: float,
) -> ScopeReading:
    """The 6-row first-match table of §3.2, plus the defensive `conflicting`.

    `rows` are the scope's OPEN rows (`superseded_by_id IS NULL`) in ascending
    `id` order. Pure: no DB, no clock -- `now` is supplied.

    `conflicting` is checked immediately after "no rows" because it is a
    statement about the ROW SET, not about the latest row: two open rows mean
    the append-only supersede chain is broken, and reading "the latest" of a
    broken chain would report a mode that may not be the one in force.
    """
    if scope_kind not in EXECUTION_MODE_SCOPE_KINDS:
        # An unknown scope kind is never trusted into the resolver.
        return ScopeReading(scope_kind, scope_ref, "invalid", None, None, None, None, 0)

    # 1: no rows at all.
    if not rows:
        return ScopeReading(scope_kind, scope_ref, "unset")

    if len(rows) > 1:
        return ScopeReading(
            scope_kind, scope_ref, "conflicting",
            None, None, None, None, len(rows),
        )

    row = rows[-1]
    assignment_id = row["id"]
    record_kind = row["record_kind"]
    mode = row["mode"]
    effective_from = row["effective_from"]
    effective_until = row["effective_until"]

    # 2: a human explicitly ended this assignment. Normal inheritance resumes
    # (EM-ADR-2) -- `revoked` matches no row of §3.3's table, so resolution
    # falls through to the next scope.
    if record_kind == "revoke":
        return ScopeReading(
            scope_kind, scope_ref, "revoked", None, assignment_id, None, None, 1
        )

    if record_kind != "assign":
        return ScopeReading(
            scope_kind, scope_ref, "invalid", None, assignment_id, None, None, 1
        )

    # 3: the window has not started yet.
    if effective_from is not None and now < effective_from:
        return ScopeReading(
            scope_kind, scope_ref, "pending", mode, assignment_id,
            effective_from, effective_until, 1,
        )

    # 4: the window a human set has elapsed. This does NOT fall through to a
    # broader scope (§3.3 rows 3/5/8) -- otherwise the deadline would stop
    # nothing.
    if effective_until is not None and now >= effective_until:
        return ScopeReading(
            scope_kind, scope_ref, "expired", mode, assignment_id,
            effective_from, effective_until, 1,
        )

    # 5: a mode outside the finite vocabulary (including NULL on an `assign`
    # row) is never interpreted.
    if mode not in EXECUTION_MODES:
        return ScopeReading(
            scope_kind, scope_ref, "invalid", None, assignment_id,
            effective_from, effective_until, 1,
        )

    # 6: otherwise the assignment is in force.
    return ScopeReading(
        scope_kind, scope_ref, "active", mode, assignment_id,
        effective_from, effective_until, 1,
    )


def resolve_execution_mode(facts: ModeFacts) -> ExecutionModeDecision:
    """The 11-row first-match table of §3.3. PURE.

    This is the single canonical state engine for this axis. Routes, the
    Dashboard, the projection and any orchestrator read its output; none of
    them re-derives it (#349's "there is one canonical state engine").

    Row order is the contract. Rows 1-3 (data inconsistency and an incoherent
    request) outrank every scope, and rows 4/6/9 (`expired`) clamp to `fixed`
    instead of letting the next scope's permission take over.
    """
    node = facts.node_reading
    flows = tuple(facts.flow_readings)
    system = facts.system_reading

    trace: List[ScopeReading] = []
    if node is not None:
        trace.append(node)
    trace.extend(flows)
    trace.append(system)
    scope_trace = tuple(trace)

    def _decide(mode: str, source_scope: str, source_ref: str, reason: str):
        return ExecutionModeDecision(
            mode=mode,
            source_scope=source_scope,
            source_ref=source_ref,
            reason=reason,
            scope_trace=scope_trace,
        )

    # 1: the supersede chain is broken somewhere. Nothing about the intended
    # mode is knowable, so nothing is permitted.
    if any(reading.state == "conflicting" for reading in scope_trace):
        return _decide(FAIL_CLOSED_MODE, "none", "", "conflicting_assignments")

    # 2: a stored mode is outside the finite vocabulary.
    if any(reading.state == "invalid" for reading in scope_trace):
        return _decide(FAIL_CLOSED_MODE, "none", "", "invalid_mode_value")

    # 3 (new): the caller named a Flow this Node does not belong to. This
    #    outranks every scope BELOW it because the request itself is
    #    incoherent -- answering it from the Node's real scopes would return a
    #    mode for a question the caller did not ask, and answering it from the
    #    claimed Flow is the privilege laundering this rule exists to stop.
    if facts.rejected_flow_claims:
        return _decide(FAIL_CLOSED_MODE, "none", "", "flow_scope_not_member")

    # 4: the Node's own window elapsed.
    if node is not None and node.state == "expired":
        return _decide(FAIL_CLOSED_MODE, "none", "", "node_expired_assignment")

    # 5: the Node's own assignment is in force -- the narrowest scope wins.
    if node is not None and node.state == "active" and node.mode:
        return _decide(node.mode, "node", node.scope_ref, "node_assignment")

    # 6: any Flow this Node belongs to has an elapsed window.
    if any(reading.state == "expired" for reading in flows):
        return _decide(FAIL_CLOSED_MODE, "none", "", "flow_expired_assignment")

    # 7: this Node lies on two Flows whose active assignments disagree. There
    # is no rule for choosing between two human decisions, so neither wins.
    active_flows = tuple(
        reading for reading in flows if reading.state == "active" and reading.mode
    )
    if len({reading.mode for reading in active_flows}) > 1:
        return _decide(FAIL_CLOSED_MODE, "none", "", "flow_scope_conflict")

    # 8: one mode across every active Flow. `source_ref` names the
    # lexicographically first of them so the answer is reproducible; the full
    # set is in `scope_trace`.
    if active_flows:
        chosen = min(active_flows, key=lambda reading: reading.scope_ref)
        return _decide(str(chosen.mode), "flow", chosen.scope_ref, "flow_assignment")

    # 9: the System-wide window elapsed.
    if system.state == "expired":
        return _decide(FAIL_CLOSED_MODE, "none", "", "system_expired_assignment")

    # 10: the System-wide assignment is in force.
    if system.state == "active" and system.mode:
        return _decide(system.mode, "system", system.scope_ref, "system_assignment")

    # 11: nobody has assigned anything applicable. `source_scope='default'`
    # exists so a projection can distinguish the DEFAULT `fixed` from a human
    # who chose `fixed` (`system_assignment`) -- two different facts (§4.4).
    return _decide(FAIL_CLOSED_MODE, "default", "", "no_assignment")


# ---------------------------------------------------------------------------
# DB reads -- no judgement lives below this line except via the pure functions
# ---------------------------------------------------------------------------


def _open_rows(
    conn: sqlite3.Connection, system_id: int, scope_kind: str, scope_ref: str
) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT * FROM execution_mode_assignment
                WHERE system_id = ? AND scope_kind = ? AND scope_ref = ?
                  AND superseded_by_id IS NULL
                ORDER BY id ASC""",
            (system_id, scope_kind, scope_ref),
        ).fetchall()
    )


def _node_row(
    conn: sqlite3.Connection, system_id: int, node_key: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM evolution_node WHERE system_id = ? AND node_key = ?",
        (system_id, node_key),
    ).fetchone()


def _node_flow_scope_refs(
    conn: sqlite3.Connection, system_id: int, node_id: int
) -> List[str]:
    """The Flow scope refs a Node currently belongs to (§2.3).

    Membership is the existing canonical fact -- an `evolution_node_link` row
    with `link_kind='flow'` that has not been superseded (#397 already owns
    this table; this Epic adds no membership table of its own). The link's
    `target_ref` is the bare runtime `flow_id`, so it is prefixed here into
    the stored scope form.
    """
    rows = conn.execute(
        """SELECT target_ref FROM evolution_node_link
            WHERE system_id = ? AND node_id = ? AND link_kind = 'flow'
              AND superseded_by_id IS NULL
            ORDER BY id ASC""",
        (system_id, node_id),
    ).fetchall()
    refs: List[str] = []
    for row in rows:
        ref = (row["target_ref"] or "").strip()
        if ref:
            refs.append(
                ref if ref.startswith(FLOW_SCOPE_PREFIX) else FLOW_SCOPE_PREFIX + ref
            )
    return refs


def load_mode_facts(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_key: Optional[str] = None,
    flow_refs: Optional[Sequence[str]] = None,
    now: Optional[float] = None,
) -> ModeFacts:
    """Assemble `ModeFacts` from the database. Reads only -- NO judgement.

    With a Node named, the Flow set is EXACTLY that Node's persisted
    `evolution_node_link(link_kind='flow')` membership; a caller-supplied
    `flow_refs` entry is a CLAIM checked against it and reported in
    `rejected_flow_claims`, never an addition to it (EM-ADR-4). Without a
    Node, the caller's refs ARE the subject. Everything is normalized to the
    stored `runtime_flow:<flow_id>` form and sorted, so the resolver's
    tie-breaks are reproducible.

    A named Node that does not exist in this System raises
    `ExecutionModeNotFoundError` rather than producing a fact set: the
    resolver would otherwise answer for a subject that does not exist, and its
    answer (`default` / `fixed`) is indistinguishable from a real Node nobody
    has configured. The gate has always refused that subject
    (`node_not_found`); this makes the READS refuse it too, so `resolve`,
    `divergence` and the projection cannot disagree about the same Node. A
    Node belonging to another System is not found here either, so cross-System
    node keys stay unprobeable.
    """
    moment = time.time() if now is None else now

    system_reading = classify_scope_state(
        _open_rows(conn, system_id, "system", ""),
        scope_kind="system", scope_ref="", now=moment,
    )

    node_reading: Optional[ScopeReading] = None
    refs: List[str] = []
    rejected: List[str] = []

    claimed = [normalize_flow_scope_ref(raw) for raw in (flow_refs or [])]

    key = (node_key or "").strip()
    if key:
        node_row = _node_row(conn, system_id, key)
        if node_row is None:
            raise ExecutionModeNotFoundError(
                f"Evolution Node {key!r} not found for this System"
            )
        member_refs = _node_flow_scope_refs(conn, system_id, node_row["id"])
        refs.extend(member_refs)
        member_set = set(member_refs)
        rejected.extend(ref for ref in claimed if ref not in member_set)
        node_reading = classify_scope_state(
            _open_rows(conn, system_id, "node", key),
            scope_kind="node", scope_ref=key, now=moment,
        )
    else:
        # No Node named: the caller-supplied Flow IS the subject of the
        # query, not a claim about some other subject's scope.
        refs.extend(claimed)

    flow_readings = tuple(
        classify_scope_state(
            _open_rows(conn, system_id, "flow", ref),
            scope_kind="flow", scope_ref=ref, now=moment,
        )
        for ref in sorted(set(refs))
    )

    return ModeFacts(
        system_reading=system_reading,
        flow_readings=flow_readings,
        node_reading=node_reading,
        node_key=key or None,
        rejected_flow_claims=tuple(sorted(set(rejected))),
        now=moment,
    )


# ---------------------------------------------------------------------------
# The fail-closed capability gate
# ---------------------------------------------------------------------------


def require_capability(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    capability: str,
    node_key: Optional[str] = None,
    flow_ref: Optional[str] = None,
    now: Optional[float] = None,
) -> ExecutionModeDecision:
    """Permit `capability` for this subject, or raise `ExecutionModeDenied`.

    Order (each step fail-closed):

    1. the capability itself must be in the finite vocabulary;
    2. a named Node must resolve -- a gate whose subject cannot be found
       permits nothing. This check stays HERE, ahead of `load_mode_facts`
       (which now refuses the same subject with `ExecutionModeNotFoundError`),
       because §4.1 promises the gate answers with a 409 carrying
       `denial_code: node_not_found` and a `null` decision. Letting the loader
       raise instead would turn a documented refusal into a 404 and hide the
       denial code the caller branches on;
    3. the mode is resolved by the canonical resolver;
    4. a reason code that is also a denial code (§4.1) is reported verbatim,
       so "a deadline elapsed" and "two Flows disagree" are distinguishable
       from a plain refusal;
    5. finally the mode's own capability table decides.
    """
    if capability not in EXECUTION_CAPABILITIES:
        raise ExecutionModeDenied(
            "unknown_capability",
            f"{capability!r} is not one of {list(EXECUTION_CAPABILITIES)}",
        )

    key = (node_key or "").strip()
    if key and _node_row(conn, system_id, key) is None:
        raise ExecutionModeDenied(
            "node_not_found",
            f"Evolution Node {key!r} not found for this System",
        )

    facts = load_mode_facts(
        conn,
        system_id=system_id,
        node_key=key or None,
        flow_refs=[flow_ref] if flow_ref else None,
        now=now,
    )
    decision = resolve_execution_mode(facts)

    if decision.reason in _REASON_DENIAL_CODES:
        raise ExecutionModeDenied(
            decision.reason,
            f"{capability!r} is not permitted: {decision.reason}",
            decision,
        )

    if capability not in MODE_CAPABILITIES.get(decision.mode, frozenset()):
        raise ExecutionModeDenied(
            "capability_not_permitted",
            f"{capability!r} is not permitted in execution mode "
            f"{decision.mode!r} (source: {decision.source_scope})",
            decision,
        )

    return decision


def build_experiment_llm_adapter(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_key: Optional[str] = None,
    flow_ref: Optional[str] = None,
    purpose: str,
    now: Optional[float] = None,
) -> Tuple["llm.LLMClient", ExecutionModeDecision]:
    """The ONLY way an experiment LLM client may be constructed (EM-ADR-3).

    **THE ORDER OF THE BODY IS THE CONTRACT.** `require_capability` runs
    FIRST; only after it returns may this function touch
    `LLMConfig.intelligence_from_env()` or `create_llm_client`. In `fixed` and
    `observe` the gate raises, so **the line that reads a credential is never
    reached**. That is the difference the contract insists on: not "the LLM is
    disabled by configuration", but "the credential path does not execute".
    `tests/test_execution_mode.py` asserts this directly by replacing both
    functions with traps.

    Do not add a path that calls `create_llm_client` directly for a Node this
    Epic governs -- doing so reintroduces exactly the reachability this gate
    removes (§4.2).

    Connection discipline: this function only READS through `conn`, but the
    client it returns performs an external round trip and every LLM client
    opens its own connection to consume System quota. The caller must
    therefore have CLOSED its `get_conn()` block before calling the returned
    client -- see `CLAUDE.md`'s "never hold a `get_conn()` connection across an
    external call". The recommended shape is: open `conn`, build the adapter,
    close `conn`, call the client, reopen `conn` to persist.

    `purpose` is what the call is for. It is required (a reasoning call with
    no stated purpose cannot be audited) and is carried by the caller into its
    own `intelligence_runs` row (Principle 7); this function writes nothing.
    """
    if not (purpose or "").strip():
        raise ExecutionModeValidationError("purpose must not be empty")

    # 1. The gate. Nothing above this line reads a credential, and nothing
    #    below it runs unless the gate returned.
    decision = require_capability(
        conn,
        system_id=system_id,
        capability="llm_experiment_proposal",
        node_key=node_key,
        flow_ref=flow_ref,
        now=now,
    )

    # 2. ONLY NOW may credentials be read, and 3. the client constructed.
    #    Both are resolved through the `llm` module at call time so a test can
    #    replace them with traps and prove they were not reached.
    config = llm.LLMConfig.intelligence_from_env()
    client = llm.create_llm_client(config)
    return client, decision


# ---------------------------------------------------------------------------
# Append-only writes (§5.1)
# ---------------------------------------------------------------------------


def _normalize_scope(scope_kind: str, scope_ref: str) -> str:
    _check_membership(scope_kind, EXECUTION_MODE_SCOPE_KINDS, "scope_kind")
    ref = (scope_ref or "").strip()
    if scope_kind == "system":
        # The System scope is the row itself; it carries no ref (§2.1). A ref
        # supplied here is refused rather than dropped: silently discarding it
        # would store a System-wide assignment for someone who believed they
        # were narrowing it.
        if ref:
            raise ExecutionModeValidationError(
                "scope_ref must be empty for scope_kind='system'"
            )
        return ""
    if not ref:
        raise ExecutionModeValidationError(
            f"scope_ref must not be empty for scope_kind={scope_kind!r}"
        )
    if scope_kind == "flow":
        # A flow ref is deliberately NOT checked against `trace_spans`: a mode
        # must be configurable before the Flow has ever been observed,
        # otherwise the only way to reach a configuration would be to run the
        # System unconfigured first. An assignment naming a Flow no Node
        # belongs to simply never applies, and stays visible in the
        # projection's assignment list.
        return normalize_flow_scope_ref(ref)
    return ref


def _resolve_for_scope(
    conn: sqlite3.Connection, system_id: int, scope_kind: str, scope_ref: str, now: float
) -> ExecutionModeDecision:
    """The effective mode of the subject this scope addresses, at `now`.

    Used to fill `previous_mode` so the audit reads "what changed" without
    recomputing it later (#337: a decision record is an audit record).
    """
    if scope_kind == "node":
        facts = load_mode_facts(conn, system_id=system_id, node_key=scope_ref, now=now)
    elif scope_kind == "flow":
        facts = load_mode_facts(conn, system_id=system_id, flow_refs=[scope_ref], now=now)
    else:
        facts = load_mode_facts(conn, system_id=system_id, now=now)
    return resolve_execution_mode(facts)


def _insert_assignment(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    record_kind: str,
    scope_kind: str,
    scope_ref: str,
    mode: Optional[str],
    reason: str,
    actor: Optional[str],
    actor_kind: str,
    effective_from: Optional[float],
    effective_until: Optional[float],
    now: float,
) -> sqlite3.Row:
    """Write one row and chain it onto the scope's previous open row(s).

    The only UPDATE is `superseded_by_id` on the immediately preceding row --
    an append-only chain pointer, not a rewrite of content (the same shape
    `evolution_node_version` and `ux_design_decision` use).

    If the scope somehow has several open rows (the `conflicting` state), all
    of them are chained onto the new row: leaving any of them open would keep
    reporting `conflicting` forever, and the new row IS the human's current
    decision.
    """
    previous = _resolve_for_scope(conn, system_id, scope_kind, scope_ref, now)
    open_rows = _open_rows(conn, system_id, scope_kind, scope_ref)
    supersedes_id = open_rows[-1]["id"] if open_rows else None

    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO execution_mode_assignment
                   (system_id, record_kind, scope_kind, scope_ref, mode,
                    previous_mode, effective_from, effective_until, reason,
                    actor_kind, actor, decision_method, supersedes_id,
                    superseded_by_id, schema_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, NULL, ?, ?)""",
            (
                system_id, record_kind, scope_kind, scope_ref, mode,
                previous.mode, effective_from, effective_until, reason,
                actor_kind, actor, supersedes_id,
                EXECUTION_MODE_ASSIGNMENT_SCHEMA_VERSION, now,
            ),
        )
        new_id = cur.lastrowid
        for row in open_rows:
            conn.execute(
                "UPDATE execution_mode_assignment SET superseded_by_id = ? WHERE id = ?",
                (new_id, row["id"]),
            )
        written = conn.execute(
            "SELECT * FROM execution_mode_assignment WHERE id = ?", (new_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return written


def assign_mode(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    scope_kind: str,
    scope_ref: str,
    mode: str,
    reason: str,
    actor: Optional[str],
    actor_kind: str = "user",
    effective_from: Optional[float] = None,
    effective_until: Optional[float] = None,
    now: Optional[float] = None,
) -> sqlite3.Row:
    """Record a human's mode assignment for one scope. Append-only.

    `decision_method` is always `manual` (§5.1): the subject that changes an
    execution mode is a person. `actor` comes from the authenticated principal
    at the route and is never taken from a request body (#337).

    A `node` scope's ref must name an existing Evolution Node -- an assignment
    for a Node that does not exist could never be resolved, and storing it
    would make the audit describe something imaginary.
    """
    _check_membership(mode, EXECUTION_MODES, "mode")
    _check_membership(actor_kind, ("user", "system"), "actor_kind")
    if not (reason or "").strip():
        raise ExecutionModeValidationError("reason must not be empty")

    ref = _normalize_scope(scope_kind, scope_ref)
    moment = time.time() if now is None else now
    start = moment if effective_from is None else effective_from
    if effective_until is not None and effective_until <= start:
        raise ExecutionModeValidationError(
            "effective_until must be after effective_from"
        )

    if conn.execute("SELECT id FROM systems WHERE id = ?", (system_id,)).fetchone() is None:
        raise ExecutionModeNotFoundError(f"System {system_id} not found")
    if scope_kind == "node" and _node_row(conn, system_id, ref) is None:
        raise ExecutionModeNotFoundError(f"Evolution Node {ref!r} not found")

    return _insert_assignment(
        conn,
        system_id=system_id,
        record_kind="assign",
        scope_kind=scope_kind,
        scope_ref=ref,
        mode=mode,
        reason=reason,
        actor=actor,
        actor_kind=actor_kind,
        effective_from=start,
        effective_until=effective_until,
        now=moment,
    )


def revoke_mode(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    assignment_id: int,
    reason: str,
    actor: Optional[str],
    actor_kind: str = "user",
    now: Optional[float] = None,
) -> sqlite3.Row:
    """End a scope's assignment explicitly. Append-only: writes a `revoke` row.

    Revoking is NOT the same as letting a window elapse (EM-ADR-2). A
    revocation is a human saying "this assignment is over", so normal
    inheritance resumes -- `revoked` matches no row of §3.3's table and
    resolution falls through to the next scope. An elapsed window clamps to
    `fixed` instead, because nobody has decided what should happen next.

    The referenced row must be the scope's CURRENT open `assign` row:
    revoking a row that something else already superseded would write a
    revocation of a decision that is no longer in force.
    """
    if not (reason or "").strip():
        raise ExecutionModeValidationError("reason must not be empty")
    _check_membership(actor_kind, ("user", "system"), "actor_kind")

    row = conn.execute(
        "SELECT * FROM execution_mode_assignment WHERE id = ? AND system_id = ?",
        (assignment_id, system_id),
    ).fetchone()
    if row is None:
        raise ExecutionModeNotFoundError(f"Assignment {assignment_id} not found")
    if row["superseded_by_id"] is not None:
        raise ExecutionModeConflictError(
            f"Assignment {assignment_id} has already been superseded"
        )
    if row["record_kind"] != "assign":
        raise ExecutionModeConflictError(
            f"Assignment {assignment_id} is a {row['record_kind']!r} record and "
            "cannot be revoked"
        )

    moment = time.time() if now is None else now
    return _insert_assignment(
        conn,
        system_id=system_id,
        record_kind="revoke",
        scope_kind=row["scope_kind"],
        scope_ref=row["scope_ref"],
        mode=None,
        reason=reason,
        actor=actor,
        actor_kind=actor_kind,
        effective_from=None,
        effective_until=None,
        now=moment,
    )


def list_assignments(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    scope_kind: Optional[str] = None,
    scope_ref: Optional[str] = None,
    current_only: bool = False,
    limit: int = 200,
) -> List[sqlite3.Row]:
    """The System's assignment audit, newest first."""
    sql = ["SELECT * FROM execution_mode_assignment WHERE system_id = ?"]
    params: List[Any] = [system_id]
    if scope_kind:
        _check_membership(scope_kind, EXECUTION_MODE_SCOPE_KINDS, "scope_kind")
        sql.append("AND scope_kind = ?")
        params.append(scope_kind)
    if scope_ref is not None:
        sql.append("AND scope_ref = ?")
        params.append(scope_ref)
    if current_only:
        sql.append("AND superseded_by_id IS NULL")
    sql.append("ORDER BY id DESC LIMIT ?")
    params.append(max(1, min(int(limit), 1000)))
    return list(conn.execute(" ".join(sql), params).fetchall())


# ---------------------------------------------------------------------------
# Runtime observation and divergence (§5.2)
# ---------------------------------------------------------------------------


def record_observation(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_key: str,
    observed_mode: str,
    capability: Optional[str] = None,
    run_ref: Optional[str] = None,
    source: str = "control_server",
    detail: str = "",
    now: Optional[float] = None,
) -> sqlite3.Row:
    """Record which mode a Node ACTUALLY ran under.

    Deliberately NOT gated on `observation_record`: an observation exists to
    make a configured/runtime disagreement visible, and the most important
    disagreement to catch is a Node that ran in a more permissive mode than it
    was configured for. Refusing to record that because the CONFIGURED mode
    forbids observation would hide exactly the case the reading is for.
    `observation_record` gates the runtime observation work itself, not this
    audit write.

    `source` says WHICH PATH produced the reading, and it is the caller's
    identity, never a claim it may choose. `control_server` means this process
    wrote the row; `sdk` means an SDK-attested reading. No current path can
    attest an SDK reading, so `sdk` is unreachable over HTTP -- the route
    passes `control_server` unconditionally and
    `ExecutionModeObservationIn` has no `source` field (#337's "provenance
    comes from the route and the principal, never the body"). It stayed in the
    stored vocabulary because the column already holds it and a future
    attested path will need it, not because a request may select it.

    `run_ref` is NOT validated: this layer has no canonical execution table to
    match it against, and inventing a resolution rule for an arbitrary string
    would be a guess. It is therefore stored and reported as what it is -- an
    uncorroborated caller-supplied pointer (`observation_doc`'s
    `run_ref_state`). An unverified pointer is not provenance (Principle 7),
    so it must never read as if the run had been checked.
    """
    key = (node_key or "").strip()
    if not key:
        raise ExecutionModeValidationError("node_key must not be empty")
    _check_membership(observed_mode, EXECUTION_MODES, "observed_mode")
    _check_membership(source, EXECUTION_MODE_OBSERVATION_SOURCES, "source")
    if capability is not None:
        _check_membership(capability, EXECUTION_CAPABILITIES, "capability")
    if _node_row(conn, system_id, key) is None:
        raise ExecutionModeNotFoundError(f"Evolution Node {key!r} not found")

    moment = time.time() if now is None else now
    cur = conn.execute(
        """INSERT INTO execution_mode_observation
               (system_id, node_key, observed_mode, capability, run_ref,
                source, detail, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (system_id, key, observed_mode, capability, run_ref, source, detail or "", moment),
    )
    return conn.execute(
        "SELECT * FROM execution_mode_observation WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def _last_assignment_change_at(
    conn: sqlite3.Connection, system_id: int, node_key: str, flow_refs: Sequence[str]
) -> Optional[float]:
    """When any scope that affects this Node last changed.

    Every row counts, superseded ones included: a superseded row was still a
    change at the moment it was written, and `stale` asks whether the
    configuration moved after the last observation.
    """
    refs = list(flow_refs)
    sql = (
        "SELECT MAX(created_at) AS latest FROM execution_mode_assignment "
        "WHERE system_id = ? AND ("
        "  (scope_kind = 'system' AND scope_ref = '')"
        "  OR (scope_kind = 'node' AND scope_ref = ?)"
    )
    params: List[Any] = [system_id, node_key]
    if refs:
        placeholders = ", ".join("?" for _ in refs)
        sql += f" OR (scope_kind = 'flow' AND scope_ref IN ({placeholders}))"
        params.extend(refs)
    sql += ")"
    row = conn.execute(sql, params).fetchone()
    return row["latest"] if row is not None else None


def evaluate_divergence(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_key: str,
    now: Optional[float] = None,
) -> DivergenceReading:
    """One Node's configured-vs-observed reading: the 4 finite states of §5.2.

    First match: `unobserved` -> `stale` -> `match`/`divergent`.

    `unobserved` is never reported as `match`. Not having looked is not a
    success (#380's "an unreadable fact is never a fact's default value"), and
    collapsing the two would let a Node nobody has ever observed read as
    healthy.

    The reading carries `observation_source` / `run_ref_state` beside the
    divergence, because "does it agree?" and "was it measured?" are two
    independent facts. Nothing on any current path attests a runtime reading:
    an observation written over HTTP is `control_server` with a `run_ref`
    nobody resolved, so a `match` means agreement with a reported value, not
    a measured one. Reporting the divergence without its standing would let a
    typed-in value read exactly like an instrumented one (#366).

    A Node that does not exist gets none of the four readings: `load_mode_facts`
    raises `ExecutionModeNotFoundError` (404 at the boundary). "There is no
    such Node" and "this Node has never been observed" are different facts,
    and reporting the first as `unobserved` would have made a typo look like a
    monitoring gap -- the same conflation `resolve` used to make by answering
    `no_assignment` for it.
    """
    key = (node_key or "").strip()
    if not key:
        raise ExecutionModeValidationError("node_key must not be empty")
    moment = time.time() if now is None else now

    facts = load_mode_facts(conn, system_id=system_id, node_key=key, now=moment)
    decision = resolve_execution_mode(facts)

    observation = conn.execute(
        """SELECT * FROM execution_mode_observation
            WHERE system_id = ? AND node_key = ?
            ORDER BY recorded_at DESC, id DESC LIMIT 1""",
        (system_id, key),
    ).fetchone()

    if observation is None:
        return DivergenceReading(
            node_key=key,
            divergence="unobserved",
            effective_mode=decision.mode,
            decision=decision,
        )

    flow_refs = [reading.scope_ref for reading in facts.flow_readings]
    changed_at = _last_assignment_change_at(conn, system_id, key, flow_refs)
    observed_at = observation["recorded_at"]

    provenance = observation_doc(observation)
    source = provenance["source"]
    run_ref_state = provenance["run_ref_state"]

    if changed_at is not None and changed_at > observed_at:
        return DivergenceReading(
            node_key=key,
            divergence="stale",
            effective_mode=decision.mode,
            observed_mode=observation["observed_mode"],
            observed_at=observed_at,
            last_assignment_at=changed_at,
            decision=decision,
            observation_source=source,
            run_ref_state=run_ref_state,
        )

    matched = observation["observed_mode"] == decision.mode
    return DivergenceReading(
        node_key=key,
        divergence="match" if matched else "divergent",
        effective_mode=decision.mode,
        observed_mode=observation["observed_mode"],
        observed_at=observed_at,
        last_assignment_at=changed_at,
        decision=decision,
        observation_source=source,
        run_ref_state=run_ref_state,
    )


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def build_mode_projection(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_keys: Optional[Sequence[str]] = None,
    now: Optional[float] = None,
) -> ModeProjection:
    """Current assignments + each Node's effective mode and divergence.

    Read-only: looking at a System's modes must not change one.

    `mode_source` carries the decision's `source_scope`, so `"default"` (the
    fail-closed `fixed` nobody chose) stays distinguishable from `"system"`
    with reason `system_assignment` (a human who chose `fixed`). Those are two
    different facts and §4.4 requires the projection to say which one it is.
    """
    moment = time.time() if now is None else now

    system_facts = load_mode_facts(conn, system_id=system_id, now=moment)
    system_decision = resolve_execution_mode(system_facts)

    assignments: List[Dict[str, Any]] = []
    for row in list_assignments(conn, system_id=system_id, current_only=True, limit=1000):
        reading = classify_scope_state(
            [row], scope_kind=row["scope_kind"], scope_ref=row["scope_ref"], now=moment
        )
        doc = assignment_doc(row)
        doc["scope_state"] = reading.state
        assignments.append(doc)

    if node_keys is None:
        node_rows = list(
            conn.execute(
                "SELECT id, node_key, maturity FROM evolution_node "
                "WHERE system_id = ? ORDER BY node_key ASC",
                (system_id,),
            ).fetchall()
        )
    else:
        node_rows = []
        for key in node_keys:
            row = _node_row(conn, system_id, (key or "").strip())
            if row is not None:
                node_rows.append(row)

    nodes: List[ModeProjectionNode] = []
    for row in node_rows:
        key = row["node_key"]
        facts = load_mode_facts(conn, system_id=system_id, node_key=key, now=moment)
        decision = resolve_execution_mode(facts)
        reading = evaluate_divergence(conn, system_id=system_id, node_key=key, now=moment)
        nodes.append(
            ModeProjectionNode(
                node_id=row["id"],
                node_key=key,
                # A SEPARATE axis (ADR-6), shown beside the mode and never
                # derived from it, nor it from the mode.
                maturity=row["maturity"],
                execution_mode=decision.mode,
                mode_source=decision.source_scope,
                mode_reason=decision.reason,
                source_ref=decision.source_ref,
                flow_refs=tuple(r.scope_ref for r in facts.flow_readings),
                divergence=reading.divergence,
                observed_mode=reading.observed_mode,
                observed_at=reading.observed_at,
                observation_source=reading.observation_source,
                run_ref_state=reading.run_ref_state,
            )
        )

    return ModeProjection(
        system_id=system_id,
        schema_version=EXECUTION_MODE_PROJECTION_SCHEMA_VERSION,
        generated_at=moment,
        system_decision=system_decision,
        assignments=tuple(assignments),
        nodes=tuple(nodes),
    )


# ---------------------------------------------------------------------------
# Row -> dict helpers
# ---------------------------------------------------------------------------


def assignment_doc(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "system_id": row["system_id"],
        "record_kind": row["record_kind"],
        "scope_kind": row["scope_kind"],
        "scope_ref": row["scope_ref"],
        "mode": row["mode"],
        "previous_mode": row["previous_mode"],
        "effective_from": row["effective_from"],
        "effective_until": row["effective_until"],
        "reason": row["reason"],
        "actor_kind": row["actor_kind"],
        "actor": row["actor"],
        "decision_method": row["decision_method"],
        "supersedes_id": row["supersedes_id"],
        "superseded_by_id": row["superseded_by_id"],
        "schema_version": row["schema_version"],
        "created_at": row["created_at"],
    }


def observation_doc(row: sqlite3.Row) -> Dict[str, Any]:
    """One observation row, with the standing of its `run_ref` stated.

    `run_ref_state` is DERIVED, never stored.  The execution boundary reserves
    ``execution_gate:`` for a pointer it wrote immediately after creating the
    canonical execution row; public callers cannot select that prefix.
    """
    run_ref = row["run_ref"]
    return {
        "id": row["id"],
        "system_id": row["system_id"],
        "node_key": row["node_key"],
        "observed_mode": row["observed_mode"],
        "capability": row["capability"],
        "run_ref": run_ref,
        "run_ref_state": (
            "absent"
            if not run_ref
            else "corroborated"
            if str(run_ref).startswith(ATTESTED_RUN_REF_PREFIX)
            else "uncorroborated"
        ),
        "source": row["source"],
        "detail": row["detail"],
        "recorded_at": row["recorded_at"],
    }
