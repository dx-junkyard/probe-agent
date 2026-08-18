"""Evolution Node: the pure finite maturity-transition evaluator, persistence
layer, and canonical projection for the Evolution Node Fabric (Epic #394
Phase 1, Issue #396).

An Evolution Node is a NEW canonical entity, deliberately NOT a version-up of
the Probe Cell (`app/cell_fabric.py` / `app/cell_binding.py`, Issue #297/
#299). The two stay strictly separate:

* The **Cell** owns the EXECUTION role -- its Agent Role Card, its
  orchestration position, its quality sampling. `app/cell_fabric.py` and
  `app/cell_binding.py` are read-only inputs to this module and are never
  modified by it.
* The **Node** owns the UNIT OF PROCESSING THAT EVOLVES -- its business I/O
  contract (`evolution_node_version`), its implementation modality
  (`evolution_node_implementation`), its maturity, its establishment/reopen
  criteria, and its rollback pin. A Node LINKS to a Cell Binding (and to a
  Component, a Probe Point, ...) through `evolution_node_link`; it never
  duplicates or supersedes a `cell_bindings`/`cell_definitions` row.

This module has the same internal shape as `app/interview_workflow.py`
(the model this file follows): a pure, DB-free evaluator
(`evaluate_transition`) operating on a frozen facts value (`NodeFacts`), and
a separate DB-touching layer (`load_node_facts`, `apply_transition`, the
create_*/add_*/pin_* persistence helpers, and the two projection builders)
that reads/writes through a connection the CALLER owns. Nothing in this
module ever calls `db.get_conn()` itself -- see `CLAUDE.md`'s "never hold a
`get_conn()` connection across an external call" rule; every function here
takes an already-open `conn` as its first argument, exactly like
`app/cell_binding.py` does.

Decision classification (Principle 6/7): a Node's maturity is a finite,
enumerated structural decision. No reasoning model is called from this
module, and `evaluate_transition`'s `llm_state_not_allowed` check exists
specifically to keep a reasoning-model CALLER from ever supplying the
canonical state directly -- an LLM may draft a Node's contract or
implementation (their `decision_method` legitimately allows `reasoning_llm`),
but the maturity transition itself is always `deterministic` (the two
system-recorded pairs) or `manual` (everything else).

probe-agent:
  role: Evolution Node finite maturity-transition evaluator, persistence, and canonical projection
  capability: evolution-node-fabric
  element_type: core
  consumers: [control-server-routes]
  operation_kind: mixed
  state_effects: [database-read, database-write]
  probe_value: Verify a maturity transition is rejected for every finite reason code (never silently allowed), that reopened never clears the stable implementation pin, that fold_events() reproduces the stored maturity from the event log alone, that a repeated idempotency_key never double-applies a transition, and that maturity/improvement_status/policy_mode are read from three independent sources and never derived from one another.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    FrozenSet,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    get_args,
)

__all__ = [
    "MaturityState",
    "MATURITY_STATES",
    "ImplementationModality",
    "IMPLEMENTATION_MODALITIES",
    "LinkKind",
    "LINK_KINDS",
    "SideEffectClass",
    "SIDE_EFFECT_CLASSES",
    "TrustBoundary",
    "TRUST_BOUNDARIES",
    "ActorKind",
    "ACTOR_KINDS",
    "EventKind",
    "EVENT_KINDS",
    "DecisionMethod",
    "DECISION_METHODS",
    "ALLOWED_TRANSITIONS",
    "SYSTEM_RECORDED_TRANSITIONS",
    "TRANSITION_REJECTION_CODES",
    "EVOLUTION_NODE_SCHEMA_VERSION",
    "EVOLUTION_NODE_PROJECTION_SCHEMA_VERSION",
    "EvolutionNodeError",
    "EvolutionNodeNotFoundError",
    "EvolutionNodeConflictError",
    "EvolutionNodeValidationError",
    "NodeFacts",
    "TransitionRequest",
    "TransitionDecision",
    "TransitionApplyResult",
    "evaluate_transition",
    "fold_events",
    "create_node",
    "add_version",
    "add_implementation",
    "add_link",
    "pin_stable_implementation",
    "apply_transition",
    "load_node_facts",
    "build_node_projection",
    "build_legacy_projection",
    "list_nodes",
    "list_events",
    "version_doc",
    "implementation_doc",
    "link_doc",
    "event_doc",
]


# ---------------------------------------------------------------------------
# Finite vocabularies
# ---------------------------------------------------------------------------
#
# Each vocabulary is a `Literal` alias plus a `get_args()`-derived tuple, the
# same pairing `app/understanding_brief.py` uses for the #351 Brief
# vocabularies (there the `Literal` lives in `app/models.py`; here it lives
# in this module because Phase 1 owns no route/model layer -- see the
# module docstring). Keeping both forms means a membership check
# (`x in MATURITY_STATES`) and a static type annotation never drift apart.

MaturityState = Literal[
    "exploring", "validating", "established", "monitoring", "reopened", "suspended"
]
MATURITY_STATES: Tuple[str, ...] = get_args(MaturityState)

ImplementationModality = Literal[
    "reasoning_llm", "lm_program", "retrieval", "router", "small_model",
    "rule", "deterministic_code", "workflow", "manual", "hybrid",
]
IMPLEMENTATION_MODALITIES: Tuple[str, ...] = get_args(ImplementationModality)

LinkKind = Literal[
    "component", "probe_point", "cell_binding", "capability",
    "flow", "purpose_element", "feature",
]
LINK_KINDS: Tuple[str, ...] = get_args(LinkKind)

SideEffectClass = Literal["pure", "read_only", "local_write", "external_write", "irreversible"]
SIDE_EFFECT_CLASSES: Tuple[str, ...] = get_args(SideEffectClass)

TrustBoundary = Literal["internal", "external_input", "external_output", "third_party"]
TRUST_BOUNDARIES: Tuple[str, ...] = get_args(TrustBoundary)

#: Whose voice an event/row speaks in. Deliberately binary (Epic #394 Phase
#: 1 scope) -- an LLM-authored draft is still persisted under `actor_kind`
#: 'developer' (a human requested/reviewed it) or 'system' (a system-recorded
#: transition ran it), never a third "ai" kind; `decision_method` already
#: carries whether reasoning_llm produced the CONTENT.
ActorKind = Literal["developer", "system"]
ACTOR_KINDS: Tuple[str, ...] = get_args(ActorKind)

EventKind = Literal[
    "transition", "version_created", "implementation_created",
    "link_created", "stable_pinned", "rollback_pinned",
]
EVENT_KINDS: Tuple[str, ...] = get_args(EventKind)

#: Principle 7's three-value decision method, mirrored locally so this
#: module has no import-time dependency on `app/models.py` (Phase 1 owns no
#: model layer -- see the module docstring).
DecisionMethod = Literal["deterministic", "reasoning_llm", "manual"]
DECISION_METHODS: Tuple[str, ...] = get_args(DecisionMethod)

EVOLUTION_NODE_SCHEMA_VERSION = "evolution-node-v1"
EVOLUTION_NODE_PROJECTION_SCHEMA_VERSION = "evolution-node-projection-v1"
EVOLUTION_NODE_LEGACY_PROJECTION_SCHEMA_VERSION = "evolution-node-legacy-v1"


# ---------------------------------------------------------------------------
# Errors (mirrors app/cell_binding.py's fail-closed error hierarchy)
# ---------------------------------------------------------------------------


class EvolutionNodeError(ValueError):
    """Base fail-closed error for the Evolution Node layer."""


class EvolutionNodeNotFoundError(EvolutionNodeError):
    """A referenced row does not exist, or does not belong to this System.

    Cross-System references and genuinely-missing rows are deliberately
    indistinguishable at this layer (routes map this to 404) so another
    System's rows are never leaked via an error message.
    """


class EvolutionNodeConflictError(EvolutionNodeError):
    """A structural precondition failed (routes map this to 409)."""


class EvolutionNodeValidationError(EvolutionNodeError):
    """A payload was malformed or used an unknown vocabulary value
    (routes map this to 422)."""


# ---------------------------------------------------------------------------
# The allowed-transition graph
# ---------------------------------------------------------------------------
#
# `reopened -> established` exists because a local re-exploration can
# conclude "the established implementation was right after all"; forcing it
# back through `validating` would demand a new implementation be produced
# even when none is needed. `monitoring -> established` is the monitoring
# contract being DEACTIVATED, not a demotion -- the implementation itself is
# unchanged. `suspended` restores to any other state because it is a safety
# HOLD, not a maturity level of its own, so resuming must be able to return
# to wherever the Node actually was.

ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "exploring": frozenset({"validating", "suspended"}),
    "validating": frozenset({"exploring", "established", "suspended"}),
    "established": frozenset({"monitoring", "reopened", "suspended"}),
    "monitoring": frozenset({"established", "reopened", "suspended"}),
    "reopened": frozenset({"exploring", "validating", "established", "suspended"}),
    "suspended": frozenset({"exploring", "validating", "established", "monitoring", "reopened"}),
}

#: The ONLY transitions Phase 1 lets the system itself record
#: (`decision_method='deterministic'`, `actor_kind='system'`): the monitoring
#: contract being switched on/off by its own evaluation (ADR-9's enumerated
#: observation transitions). For a HUMAN caller the pair is asymmetric:
#: `established -> monitoring` (activation) is never `manual` -- a
#: developer's click claiming observation is running would masquerade as the
#: monitoring evaluation's own reading, which is exactly what
#: `evaluate_transition`'s `manual_approval_required` check refuses --
#: while `monitoring -> established` (deactivation: recording that
#: observation stopped) MAY also be a manual operations decision by a named
#: developer. Every other transition requires a human (`manual` + a
#: non-empty `actor`).
SYSTEM_RECORDED_TRANSITIONS: FrozenSet[Tuple[str, str]] = frozenset({
    ("established", "monitoring"),
    ("monitoring", "established"),
})

#: The states in which the contract is FROZEN: a Node whose fixation
#: decision has been approved (`established`, and `monitoring` on top of it)
#: promises what its pinned stable implementation actually does. Publishing a
#: new current contract version under it would leave the pin implementing a
#: superseded promise while the Node kept displaying established/monitoring
#: -- and ADR-9 forbids `add_version` from repairing that by moving the
#: maturity itself, because no maturity transition is ever automatic. So the
#: refusal IS the design: the developer reopens or suspends first
#: (`decision_method: manual`), and only then revises the contract.
_CONTRACT_FROZEN_MATURITIES: FrozenSet[str] = frozenset({"established", "monitoring"})

#: Backward transitions: leaving `established`/`monitoring` for anything
#: other than the other one of that pair. A backward move and `suspended`
#: are the two cases `evaluate_transition`'s `reason_required` check demands
#: a non-empty `reason` for.
_BACKWARD_SOURCE_STATES: FrozenSet[str] = frozenset({"established", "monitoring"})


# ---------------------------------------------------------------------------
# Facts / request / decision (pure data, no DB, no clock)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeFacts:
    """Every persisted fact `evaluate_transition` reads. Nothing else may
    influence the decision -- no client-only value and no wall clock, the
    same discipline `app/interview_workflow.py`'s `WorkflowFacts` follows.

    `monitoring_contract_ref` and `monitoring_contract_valid` are two
    separate facts on purpose: the first is what the Node's row CLAIMS,
    the second is whether that claim resolves to a real, active,
    non-superseded `node_monitoring_contract` row of THIS Node. A ref alone
    is a string; only the resolution is evidence that observation is
    actually declared, which is what `established -> monitoring` asserts
    (ADR-5). `load_node_facts` computes the second fail-closed: an
    unparseable ref, a missing table, a missing/foreign row, an inactive or
    superseded contract all yield `False`.

    `known_evidence_refs` is the set of evidence refs `evaluate_transition`
    accepts as genuinely belonging to THIS Node: `load_node_facts` populates
    it as ``f"{link_kind}:{target_ref}"`` for every currently-linked
    (non-superseded) `evolution_node_link` row. Establishing or monitoring a
    Node therefore requires evidence tied to an asset actually linked to it
    -- a ref that names an unlinked asset, another Node's asset, or another
    System's asset can never appear in this set, which is what makes
    `foreign_evidence` a pure membership check with no DB access of its own.
    """

    maturity: str = "exploring"
    has_version: bool = False
    has_implementation: bool = False
    has_stable_implementation: bool = False
    monitoring_contract_ref: Optional[str] = None
    monitoring_contract_valid: bool = False
    implementation_stale: bool = False
    known_evidence_refs: FrozenSet[str] = frozenset()


@dataclass(frozen=True)
class TransitionRequest:
    """The caller's request to move a Node to `to_state`."""

    to_state: str
    decision_method: str
    actor: Optional[str] = None
    actor_kind: str = "developer"
    reason: str = ""
    reason_code: str = ""
    evidence_refs: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TransitionDecision:
    """The pure evaluator's verdict. `reason_code == "ok"` iff `allowed`."""

    allowed: bool
    reason_code: str
    message: str


#: The finite, first-match rejection codes `evaluate_transition` can return,
#: in evaluation order. `"ok"` (allowed) is deliberately not a member of this
#: tuple -- it is the ABSENCE of a rejection, not one more code in the set.
TRANSITION_REJECTION_CODES: Tuple[str, ...] = (
    "llm_state_not_allowed",
    "unknown_target_state",
    "illegal_transition",
    "manual_approval_required",
    "reason_required",
    "version_missing",
    "implementation_missing",
    "stable_implementation_missing",
    "monitoring_contract_missing",
    "monitoring_contract_invalid",
    "evidence_missing",
    "foreign_evidence",
    "stale_implementation",
)


# ---------------------------------------------------------------------------
# The pure evaluator: a 13-row first-match rejection table
# ---------------------------------------------------------------------------


def evaluate_transition(facts: NodeFacts, request: TransitionRequest) -> TransitionDecision:
    """Pure. No clock, no DB, no client state: same facts + request in,
    same decision out. First match wins; a row earlier in this list always
    outranks a later one, exactly like `interview_workflow.evaluate_candidate_state`.
    """

    to_state = request.to_state

    # 1: an LLM never emits a canonical maturity state (Principle 6).
    # Genuinely unconditional and FIRST -- before even the target-state
    # vocabulary check -- so a reasoning-model caller is always told exactly
    # why it was refused, regardless of whether the state it asked for is
    # known, legal, or anything else.
    if request.decision_method == "reasoning_llm":
        return TransitionDecision(
            False, "llm_state_not_allowed",
            "A maturity transition may never be recorded with "
            "decision_method='reasoning_llm'",
        )

    # 2: the target itself must be one of the six states.
    if to_state not in MATURITY_STATES:
        return TransitionDecision(
            False, "unknown_target_state",
            f"{to_state!r} is not a known maturity state",
        )

    # 3: the transition graph itself. `to_state == facts.maturity` is
    # covered here too -- no state's allowed-set contains itself.
    if to_state not in ALLOWED_TRANSITIONS.get(facts.maturity, frozenset()):
        return TransitionDecision(
            False, "illegal_transition",
            f"{facts.maturity!r} -> {to_state!r} is not an allowed transition",
        )

    pair = (facts.maturity, to_state)
    is_system_recorded = pair in SYSTEM_RECORDED_TRANSITIONS
    is_manual_request = (
        request.decision_method == "manual"
        and request.actor_kind == "developer"
        and bool((request.actor or "").strip())
    )

    # 4: authorization. The two system-recorded pairs accept
    # deterministic+system (see SYSTEM_RECORDED_TRANSITIONS above); every
    # other transition requires manual + a real actor. The pair is
    # deliberately asymmetric for a HUMAN caller: `monitoring ->
    # established` (deactivation -- recording that observation stopped) is
    # also a legitimate manual operations decision, while `established ->
    # monitoring` (activation) is never manual, because a developer's click
    # claiming "the monitoring contract is actually observing" would
    # masquerade as the monitoring evaluation's own reading.
    if is_system_recorded:
        system_recorded_ok = (
            request.decision_method == "deterministic" and request.actor_kind == "system"
        )
        manual_deactivation_ok = pair == ("monitoring", "established") and is_manual_request
        if not (system_recorded_ok or manual_deactivation_ok):
            return TransitionDecision(
                False, "manual_approval_required",
                f"{facts.maturity!r} -> {to_state!r} is system-recorded and "
                "requires decision_method='deterministic' with actor_kind='system'"
                + (
                    ", or a manual deactivation by a named developer"
                    if pair == ("monitoring", "established")
                    else ""
                ),
            )
    else:
        if not is_manual_request:
            return TransitionDecision(
                False, "manual_approval_required",
                f"{facts.maturity!r} -> {to_state!r} requires a human decision: "
                "decision_method='manual', actor_kind='developer', and a non-empty actor",
            )

    # 5: a reason is mandatory for a safety hold or a backward move.
    is_backward = facts.maturity in _BACKWARD_SOURCE_STATES and to_state not in (
        "monitoring", "established",
    )
    if (to_state == "suspended" or is_backward) and not (request.reason or "").strip():
        return TransitionDecision(
            False, "reason_required",
            "suspending or moving backward requires a non-empty reason",
        )

    # 6: every target except 'suspended' needs a current contract version --
    # a Node cannot even be explored meaningfully without one.
    if to_state != "suspended" and not facts.has_version:
        return TransitionDecision(
            False, "version_missing",
            f"target state {to_state!r} requires a current contract version",
        )

    # 7: validating/established/monitoring need something that actually
    # implements the contract.
    if to_state in ("validating", "established", "monitoring") and not facts.has_implementation:
        return TransitionDecision(
            False, "implementation_missing",
            f"target state {to_state!r} requires a current implementation",
        )

    # 8: established/monitoring need a PINNED stable implementation.
    # `reopened` deliberately does NOT require or clear this -- production
    # keeps running the established implementation while exploration
    # proceeds (see the module docstring / ADR note in
    # docs/evolutionary-pipeline.md).
    if to_state in ("established", "monitoring") and not facts.has_stable_implementation:
        return TransitionDecision(
            False, "stable_implementation_missing",
            f"target state {to_state!r} requires a pinned stable implementation",
        )

    # 9: monitoring needs a monitoring contract wired, AND that contract must
    # actually resolve (see NodeFacts.monitoring_contract_valid). The two
    # codes are deliberately distinct: "none is wired" and "the one that is
    # wired cannot be verified" are different facts with different fixes, and
    # an unverifiable claim never passes a gate.
    if to_state == "monitoring":
        if not facts.monitoring_contract_ref:
            return TransitionDecision(
                False, "monitoring_contract_missing",
                "target state 'monitoring' requires monitoring_contract_ref to be set",
            )
        if not facts.monitoring_contract_valid:
            return TransitionDecision(
                False, "monitoring_contract_invalid",
                "monitoring_contract_ref does not resolve to an active, "
                "non-superseded monitoring contract of this Node",
            )

    # 10/11: established/monitoring need evidence, and every cited ref must
    # genuinely be this Node's own (see NodeFacts.known_evidence_refs).
    if to_state in ("established", "monitoring"):
        if not request.evidence_refs:
            return TransitionDecision(
                False, "evidence_missing",
                f"target state {to_state!r} requires at least one evidence ref",
            )
        for ref in request.evidence_refs:
            if ref not in facts.known_evidence_refs:
                return TransitionDecision(
                    False, "foreign_evidence",
                    f"evidence ref {ref!r} is not linked to this Node",
                )

    # 12: the current implementation must actually implement the CURRENT
    # contract version.
    if to_state in ("established", "monitoring") and facts.implementation_stale:
        return TransitionDecision(
            False, "stale_implementation",
            "the current implementation does not match the current contract version",
        )

    return TransitionDecision(True, "ok", f"{facts.maturity!r} -> {to_state!r} is allowed")


def fold_events(events: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """Replay `event_kind='transition'` rows (in `id` order) and return the
    resulting maturity, or `None` if the sequence contains no transition.

    This is the reconciliation ADR-4 requires (docs/evolutionary-pipeline.md):
    the stored `evolution_node.maturity` column is a cache of exactly this
    fold, so a drifted cache is detectable by comparing the two.
    """
    maturity: Optional[str] = None
    for event in sorted(events, key=lambda e: e["id"]):
        if event.get("event_kind") != "transition":
            continue
        to_state = event.get("to_state")
        if to_state:
            maturity = to_state
    return maturity


# ---------------------------------------------------------------------------
# Persistence layer -- every function takes an already-open `conn`.
# ---------------------------------------------------------------------------


def _require_node(conn, system_id: int, node_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM evolution_node WHERE id = ? AND system_id = ?",
        (node_id, system_id),
    ).fetchone()
    if row is None:
        raise EvolutionNodeNotFoundError(f"Evolution Node {node_id} not found")
    return row


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise EvolutionNodeValidationError(
            f"{field_name} must be one of {list(vocabulary)}, got {value!r}"
        )


def _insert_event(
    conn,
    *,
    node_id: int,
    system_id: int,
    event_kind: str,
    from_state: Optional[str] = None,
    to_state: Optional[str] = None,
    from_version_id: Optional[int] = None,
    to_version_id: Optional[int] = None,
    from_implementation_id: Optional[int] = None,
    to_implementation_id: Optional[int] = None,
    actor: Optional[str] = None,
    actor_kind: str = "developer",
    decision_method: str = "manual",
    reason_code: str = "",
    reason: str = "",
    evidence_json: str = "[]",
    idempotency_key: str = "",
    created_at: Optional[float] = None,
) -> sqlite3.Row:
    _check_membership(event_kind, EVENT_KINDS, "event_kind")
    _check_membership(actor_kind, ACTOR_KINDS, "actor_kind")
    _check_membership(decision_method, DECISION_METHODS, "decision_method")
    now = created_at if created_at is not None else time.time()
    cur = conn.execute(
        """INSERT INTO evolution_node_event
               (node_id, system_id, event_kind, from_state, to_state,
                from_version_id, to_version_id, from_implementation_id, to_implementation_id,
                actor, actor_kind, decision_method, reason_code, reason, evidence_json,
                idempotency_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            node_id, system_id, event_kind, from_state, to_state,
            from_version_id, to_version_id, from_implementation_id, to_implementation_id,
            actor, actor_kind, decision_method, reason_code, reason, evidence_json,
            idempotency_key, now,
        ),
    )
    return conn.execute(
        "SELECT * FROM evolution_node_event WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def create_node(
    conn,
    *,
    system_id: int,
    node_key: str,
    display_name: str = "",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Create a new Evolution Node in `maturity='exploring'`.

    `node_key` is developer-supplied and must be unique per System -- it is
    NEVER derived from a component_id (see the module docstring: a Node may
    be designed before instrumentation exists, and must survive a Component
    rename).
    """
    if not (node_key or "").strip():
        raise EvolutionNodeValidationError("node_key must not be empty")
    system_row = conn.execute("SELECT id FROM systems WHERE id = ?", (system_id,)).fetchone()
    if system_row is None:
        raise EvolutionNodeNotFoundError(f"System {system_id} not found")
    existing = conn.execute(
        "SELECT id FROM evolution_node WHERE system_id = ? AND node_key = ?",
        (system_id, node_key),
    ).fetchone()
    if existing is not None:
        raise EvolutionNodeConflictError(
            f"node_key {node_key!r} already exists for this System"
        )

    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO evolution_node
                   (system_id, node_key, display_name, maturity, schema_version,
                    created_at, updated_at)
               VALUES (?, ?, ?, 'exploring', ?, ?, ?)""",
            (
                system_id, node_key, display_name or node_key,
                EVOLUTION_NODE_SCHEMA_VERSION, now, now,
            ),
        )
        node_id = cur.lastrowid
        row = conn.execute("SELECT * FROM evolution_node WHERE id = ?", (node_id,)).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def add_version(
    conn,
    *,
    system_id: int,
    node_id: int,
    mission: str,
    input_contract: Optional[Mapping[str, Any]],
    output_contract: Optional[Mapping[str, Any]],
    side_effect_class: str,
    trust_boundary: str,
    scope: str = "",
    out_of_scope: str = "",
    establishment_criteria: Optional[Sequence[str]] = None,
    reopen_criteria: Optional[Sequence[str]] = None,
    evaluation_policy_refs: Optional[Sequence[str]] = None,
    decision_method: str = "manual",
    created_by: Optional[str] = None,
    actor_kind: str = "developer",
) -> sqlite3.Row:
    """Append the next `evolution_node_version` for a Node and make it
    current. Never mutates a prior version's content -- see the table's DDL
    comment in `app/db.py`.

    Refused for an `established`/`monitoring` Node (see
    `_CONTRACT_FROZEN_MATURITIES`): the stable implementation is pinned to
    the CURRENT contract, so making a new one current would silently leave
    the pin implementing a superseded promise. The maturity is never moved
    here to compensate (ADR-9: no automatic maturity transition), so the
    developer's explicit reopen or suspend comes first.
    """
    node = _require_node(conn, system_id, node_id)
    if node["maturity"] in _CONTRACT_FROZEN_MATURITIES:
        raise EvolutionNodeConflictError(
            f"Node {node_id} is {node['maturity']!r}: its contract is frozen "
            "while a stable implementation is pinned to it. Transition the "
            "Node to 'reopened' or 'suspended' first (an explicit human "
            "decision, decision_method='manual'), then add the new contract "
            "version."
        )
    _check_membership(side_effect_class, SIDE_EFFECT_CLASSES, "side_effect_class")
    _check_membership(trust_boundary, TRUST_BOUNDARIES, "trust_boundary")
    _check_membership(decision_method, DECISION_METHODS, "decision_method")
    _check_membership(actor_kind, ACTOR_KINDS, "actor_kind")
    if not (mission or "").strip():
        raise EvolutionNodeValidationError("mission must not be empty")

    now = time.time()
    max_row = conn.execute(
        "SELECT MAX(version_number) AS m FROM evolution_node_version "
        "WHERE node_id = ? AND system_id = ?",
        (node_id, system_id),
    ).fetchone()
    next_number = (max_row["m"] or 0) + 1
    prev_id = node["current_version_id"]

    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO evolution_node_version
                   (node_id, system_id, version_number, mission, scope, out_of_scope,
                    input_contract, output_contract, side_effect_class, trust_boundary,
                    establishment_criteria, reopen_criteria, evaluation_policy_refs,
                    decision_method, created_by, created_at, superseded_by_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                node_id, system_id, next_number, mission, scope, out_of_scope,
                json.dumps(input_contract or {}), json.dumps(output_contract or {}),
                side_effect_class, trust_boundary,
                json.dumps(list(establishment_criteria or [])),
                json.dumps(list(reopen_criteria or [])),
                json.dumps(list(evaluation_policy_refs or [])),
                decision_method, created_by, now,
            ),
        )
        version_id = cur.lastrowid
        if prev_id is not None:
            conn.execute(
                "UPDATE evolution_node_version SET superseded_by_id = ? WHERE id = ?",
                (version_id, prev_id),
            )
        conn.execute(
            "UPDATE evolution_node SET current_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, now, node_id),
        )
        _insert_event(
            conn, node_id=node_id, system_id=system_id, event_kind="version_created",
            from_version_id=prev_id, to_version_id=version_id,
            actor=created_by, actor_kind=actor_kind, decision_method=decision_method,
            created_at=now,
        )
        row = conn.execute(
            "SELECT * FROM evolution_node_version WHERE id = ?", (version_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def add_implementation(
    conn,
    *,
    system_id: int,
    node_id: int,
    node_version_id: int,
    modality: str,
    config: Optional[Mapping[str, Any]] = None,
    snapshot_id: Optional[int] = None,
    commit_sha: Optional[str] = None,
    environment_ref: Optional[str] = None,
    provenance: Optional[Mapping[str, Any]] = None,
    decision_method: str = "manual",
    created_by: Optional[str] = None,
    actor_kind: str = "developer",
) -> sqlite3.Row:
    """Append the next `evolution_node_implementation` for a Node and make it
    current (NOT stable -- see `pin_stable_implementation`)."""
    node = _require_node(conn, system_id, node_id)
    _check_membership(modality, IMPLEMENTATION_MODALITIES, "modality")
    _check_membership(decision_method, DECISION_METHODS, "decision_method")
    _check_membership(actor_kind, ACTOR_KINDS, "actor_kind")
    version_row = conn.execute(
        "SELECT id FROM evolution_node_version WHERE id = ? AND node_id = ? AND system_id = ?",
        (node_version_id, node_id, system_id),
    ).fetchone()
    if version_row is None:
        raise EvolutionNodeNotFoundError(
            f"Contract version {node_version_id} not found for this Node"
        )
    if snapshot_id is not None:
        snapshot_row = conn.execute(
            "SELECT id FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
        if snapshot_row is None:
            raise EvolutionNodeNotFoundError(f"Snapshot {snapshot_id} not found for this System")

    now = time.time()
    max_row = conn.execute(
        "SELECT MAX(implementation_number) AS m FROM evolution_node_implementation "
        "WHERE node_id = ? AND system_id = ?",
        (node_id, system_id),
    ).fetchone()
    next_number = (max_row["m"] or 0) + 1
    prev_id = node["current_implementation_id"]

    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO evolution_node_implementation
                   (node_id, system_id, implementation_number, node_version_id, modality,
                    config_json, snapshot_id, commit_sha, environment_ref, provenance_json,
                    created_by, decision_method, created_at, superseded_by_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                node_id, system_id, next_number, node_version_id, modality,
                json.dumps(config or {}), snapshot_id, commit_sha, environment_ref,
                json.dumps(provenance or {}), created_by, decision_method, now,
            ),
        )
        implementation_id = cur.lastrowid
        if prev_id is not None:
            conn.execute(
                "UPDATE evolution_node_implementation SET superseded_by_id = ? WHERE id = ?",
                (implementation_id, prev_id),
            )
        conn.execute(
            "UPDATE evolution_node SET current_implementation_id = ?, updated_at = ? WHERE id = ?",
            (implementation_id, now, node_id),
        )
        _insert_event(
            conn, node_id=node_id, system_id=system_id, event_kind="implementation_created",
            from_implementation_id=prev_id, to_implementation_id=implementation_id,
            actor=created_by, actor_kind=actor_kind, decision_method=decision_method,
            created_at=now,
        )
        row = conn.execute(
            "SELECT * FROM evolution_node_implementation WHERE id = ?", (implementation_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def _require_approved_probe_point(
    conn, *, system_id: int, target_ref: str, target_row_id: Optional[int]
) -> None:
    """Fail closed unless `target_ref` names an APPROVED probe point of THIS
    System (ADR-2's verification rule, the same gate `app/cell_binding.py`'s
    `_resolve_from_probe_point` applies for Cell Bindings).

    A Probe Point's identity owner is `probe_points.id` (docs/
    evolutionary-pipeline.md §3), so its stable string ref IS that id in
    decimal form. A missing row and another System's row are deliberately
    indistinguishable (both NotFound), so probe point ids can never be
    probed across Systems through link creation."""
    ref = (target_ref or "").strip()
    try:
        probe_point_id = int(ref)
    except ValueError:
        raise EvolutionNodeValidationError(
            "a probe_point link's target_ref must be the probe_points.id of "
            f"an approved Probe Point, got {target_ref!r}"
        )
    if target_row_id is not None and target_row_id != probe_point_id:
        raise EvolutionNodeValidationError(
            f"target_row_id {target_row_id} does not match the probe_point "
            f"target_ref {target_ref!r}"
        )
    row = conn.execute(
        "SELECT status FROM probe_points WHERE id = ? AND system_id = ?",
        (probe_point_id, system_id),
    ).fetchone()
    if row is None:
        raise EvolutionNodeNotFoundError(f"Probe point {probe_point_id} not found")
    if row["status"] != "approved":
        raise EvolutionNodeConflictError(
            f"Probe point {probe_point_id} is not approved "
            f"(status={row['status']!r}); only an approved Probe Point may "
            "back a probe_point link"
        )


def add_link(
    conn,
    *,
    system_id: int,
    node_id: int,
    link_kind: str,
    target_ref: str,
    target_row_id: Optional[int] = None,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str] = None,
    actor_kind: str = "developer",
) -> sqlite3.Row:
    """Append a new `evolution_node_link`. Multiple concurrent links of the
    same kind are legitimate (a Node may span more than one Component), so
    this never automatically supersedes an existing link -- see the table's
    DDL comment in `app/db.py`."""
    _require_node(conn, system_id, node_id)
    _check_membership(link_kind, LINK_KINDS, "link_kind")
    _check_membership(decision_method, DECISION_METHODS, "decision_method")
    _check_membership(actor_kind, ACTOR_KINDS, "actor_kind")
    if not (target_ref or "").strip():
        raise EvolutionNodeValidationError("target_ref must not be empty")
    if link_kind == "probe_point":
        _require_approved_probe_point(
            conn, system_id=system_id, target_ref=target_ref, target_row_id=target_row_id
        )

    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO evolution_node_link
                   (node_id, system_id, link_kind, target_ref, target_row_id, note,
                    decision_method, created_by, created_at, superseded_by_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                node_id, system_id, link_kind, target_ref, target_row_id, note or "",
                decision_method, created_by, now,
            ),
        )
        link_id = cur.lastrowid
        conn.execute(
            "UPDATE evolution_node SET updated_at = ? WHERE id = ?", (now, node_id)
        )
        _insert_event(
            conn, node_id=node_id, system_id=system_id, event_kind="link_created",
            actor=created_by, actor_kind=actor_kind, decision_method=decision_method,
            created_at=now,
        )
        row = conn.execute(
            "SELECT * FROM evolution_node_link WHERE id = ?", (link_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def pin_stable_implementation(
    conn,
    *,
    system_id: int,
    node_id: int,
    implementation_id: int,
    actor: Optional[str] = None,
    actor_kind: str = "developer",
    decision_method: str = "manual",
    reason: str = "",
) -> sqlite3.Row:
    """Promote `implementation_id` to the Node's stable pin.

    When a stable implementation already existed, it rotates into
    `rollback_implementation_id` in the SAME transaction and BOTH events
    (`stable_pinned` then `rollback_pinned`) are written -- an explicit
    rollback target always exists once a Node has ever had a second stable
    implementation. `reopened` never clears this pin (see
    `evaluate_transition`'s row 8 comment): production keeps running the
    rollback/stable implementation while exploration proceeds.
    """
    node = _require_node(conn, system_id, node_id)
    _check_membership(actor_kind, ACTOR_KINDS, "actor_kind")
    _check_membership(decision_method, DECISION_METHODS, "decision_method")
    implementation_row = conn.execute(
        "SELECT id FROM evolution_node_implementation "
        "WHERE id = ? AND node_id = ? AND system_id = ?",
        (implementation_id, node_id, system_id),
    ).fetchone()
    if implementation_row is None:
        raise EvolutionNodeNotFoundError(
            f"Implementation {implementation_id} not found for this Node"
        )

    now = time.time()
    prev_stable = node["stable_implementation_id"]
    rotates_to_rollback = prev_stable is not None and prev_stable != implementation_id
    new_rollback = prev_stable if rotates_to_rollback else node["rollback_implementation_id"]

    conn.execute("BEGIN")
    try:
        conn.execute(
            """UPDATE evolution_node
                   SET stable_implementation_id = ?, rollback_implementation_id = ?,
                       updated_at = ?
                 WHERE id = ?""",
            (implementation_id, new_rollback, now, node_id),
        )
        _insert_event(
            conn, node_id=node_id, system_id=system_id, event_kind="stable_pinned",
            from_implementation_id=prev_stable, to_implementation_id=implementation_id,
            actor=actor, actor_kind=actor_kind, decision_method=decision_method,
            reason=reason, created_at=now,
        )
        if rotates_to_rollback:
            _insert_event(
                conn, node_id=node_id, system_id=system_id, event_kind="rollback_pinned",
                from_implementation_id=node["rollback_implementation_id"],
                to_implementation_id=prev_stable,
                actor=actor, actor_kind=actor_kind, decision_method=decision_method,
                reason=reason, created_at=now,
            )
        row = conn.execute("SELECT * FROM evolution_node WHERE id = ?", (node_id,)).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


#: The one format `app/node_operations.py::create_monitoring_contract`
#: writes into `evolution_node.monitoring_contract_ref`. Parsing it here is a
#: structural check against a finite format (Principle 6), not a guess: a ref
#: in any other shape names nothing this server can verify.
_MONITORING_CONTRACT_REF_PREFIX = "node_monitoring_contract:"


def _monitoring_contract_resolves(
    conn, *, system_id: int, node_id: int, ref: Optional[str]
) -> bool:
    """Does `ref` name an ACTIVE, non-superseded monitoring contract of this
    Node? Fail-closed: every failure to verify is `False`.

    Phase 5's table may not exist yet in a database migrated only to Phase 1.
    Unlike `stabilization_package` above -- where a missing table legitimately
    means "no packages exist" and simply contributes no evidence refs -- a
    missing table here cannot mean "the contract is fine": a ref IS set and
    cannot be checked, so the gate refuses (`monitoring_contract_invalid`).
    """
    if not (ref or "").strip():
        return False
    if not ref.startswith(_MONITORING_CONTRACT_REF_PREFIX):
        return False
    try:
        contract_id = int(ref[len(_MONITORING_CONTRACT_REF_PREFIX):])
    except ValueError:
        return False
    try:
        row = conn.execute(
            """SELECT active, superseded_by_id FROM node_monitoring_contract
                   WHERE id = ? AND node_id = ? AND system_id = ?""",
            (contract_id, node_id, system_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    if row is None:
        return False
    return bool(row["active"]) and row["superseded_by_id"] is None


def load_node_facts(conn, *, system_id: int, node_id: int) -> NodeFacts:
    """Read every persisted fact `evaluate_transition` needs, for one Node."""
    node = _require_node(conn, system_id, node_id)

    has_version = node["current_version_id"] is not None
    has_implementation = node["current_implementation_id"] is not None
    has_stable = node["stable_implementation_id"] is not None

    implementation_stale = False
    if has_implementation and has_version:
        implementation_row = conn.execute(
            "SELECT node_version_id FROM evolution_node_implementation "
            "WHERE id = ? AND node_id = ? AND system_id = ?",
            (node["current_implementation_id"], node_id, system_id),
        ).fetchone()
        if implementation_row is not None:
            implementation_stale = (
                implementation_row["node_version_id"] != node["current_version_id"]
            )

    links = _current_links(conn, system_id, node_id)
    known_evidence_refs = set(
        f"{link['link_kind']}:{link['target_ref']}" for link in links
    )

    # A Stabilization Evidence Package (Phase 4, Issue #399) argued for THIS
    # Node is this Node's own evidence, so it belongs in the set the
    # `foreign_evidence` rule checks against. The rule's purpose is to refuse
    # ANOTHER Node's or another System's evidence, and that is preserved: the
    # query below is scoped to this node_id and system_id, so a package
    # belonging to a different Node still fails the check.
    #
    # The table is queried defensively because `evolution_node.py` is Phase 1
    # and must keep working against a database where Phase 4's tables have not
    # been created yet -- a missing table means "no packages", never an error
    # that would block every transition.
    try:
        package_rows = conn.execute(
            "SELECT id FROM stabilization_package WHERE node_id = ? AND system_id = ?",
            (node_id, system_id),
        ).fetchall()
    except sqlite3.OperationalError:
        package_rows = []
    known_evidence_refs.update(
        f"stabilization_package:{row['id']}" for row in package_rows
    )
    known_evidence_refs = frozenset(known_evidence_refs)

    return NodeFacts(
        maturity=node["maturity"],
        has_version=has_version,
        has_implementation=has_implementation,
        has_stable_implementation=has_stable,
        monitoring_contract_ref=node["monitoring_contract_ref"],
        monitoring_contract_valid=_monitoring_contract_resolves(
            conn, system_id=system_id, node_id=node_id,
            ref=node["monitoring_contract_ref"],
        ),
        implementation_stale=implementation_stale,
        known_evidence_refs=known_evidence_refs,
    )


def _lookup_idempotent_event(
    conn, *, system_id: int, node_id: int, idempotency_key: str
) -> Optional[sqlite3.Row]:
    """The single definition of "has this exact request already been
    recorded?". `apply_transition` asks three times -- before the
    transaction, inside it, and while recovering from a unique-index
    violation -- and all three must ask the same question of the same rows.
    """
    return conn.execute(
        """SELECT * FROM evolution_node_event
               WHERE node_id = ? AND system_id = ? AND idempotency_key = ?""",
        (node_id, system_id, idempotency_key),
    ).fetchone()


def _duplicate_result(event: Mapping[str, Any], node: Mapping[str, Any]) -> "TransitionApplyResult":
    return TransitionApplyResult(
        decision=TransitionDecision(
            True, "ok", "Duplicate request; the original transition is unchanged.",
        ),
        applied=False,
        duplicate=True,
        event=event,
        node=node,
    )


class _MaturityChangedUnderRequest(Exception):
    """Internal signal: the compare-and-set UPDATE matched no row, so the
    Node's maturity is no longer the one the decision was made against.

    Deliberately NOT an `EvolutionNodeError`: it must never escape
    `apply_transition`, which converts it into an
    `EvolutionNodeConflictError` after rolling back. Raising the public error
    directly inside the transaction would be caught by the generic
    `except Exception` handler and rolled back there, which is correct but
    hides why the request failed behind a shared branch.
    """

    def __init__(self, expected_maturity: str) -> None:
        super().__init__(expected_maturity)
        self.expected_maturity = expected_maturity


@dataclass(frozen=True)
class TransitionApplyResult:
    decision: TransitionDecision
    applied: bool
    duplicate: bool
    event: Optional[Mapping[str, Any]]
    node: Mapping[str, Any]


def apply_transition(
    conn,
    *,
    system_id: int,
    node_id: int,
    to_state: str,
    decision_method: str,
    actor: Optional[str] = None,
    actor_kind: str = "developer",
    reason: str = "",
    reason_code: str = "",
    evidence_refs: Optional[Sequence[str]] = None,
    idempotency_key: str = "",
) -> TransitionApplyResult:
    """Re-validate (`evaluate_transition`) server-side and, if allowed,
    persist the transition event + maturity update in one transaction.

    Idempotent on `idempotency_key`: a repeat with the SAME non-empty key
    returns the ORIGINAL event with `duplicate=True` and `applied=False`,
    changing nothing -- it never re-validates or re-applies. A caller that
    does not care about idempotency passes `idempotency_key=""`, which is
    excluded from the uniqueness check entirely (see the partial unique
    index in `app/db.py`).

    The facts are read and evaluated INSIDE the write transaction, which is
    opened with `BEGIN IMMEDIATE` so the write lock is taken BEFORE the read.
    Two concurrent requests carrying different idempotency keys (which the
    unique index therefore does not separate) would otherwise both read the
    same old maturity on their own connections and both commit, leaving the
    event log's `from_state` chain broken -- and ADR-4 makes that log the
    authority the stored column is checked against.

    The maturity UPDATE is additionally a compare-and-set on the maturity the
    decision was made against, verified by `rowcount`. That is belt and
    suspenders on top of the lock: it makes the invariant "no transition ever
    commits whose `from_state` differs from the row's maturity at commit
    time" hold structurally, independent of which isolation the caller's
    connection happens to provide.
    """
    node = _require_node(conn, system_id, node_id)
    evidence_refs_t = tuple(evidence_refs or ())
    idem = (idempotency_key or "").strip()

    if idem:
        existing_event = _lookup_idempotent_event(
            conn, system_id=system_id, node_id=node_id, idempotency_key=idem
        )
        if existing_event is not None:
            return _duplicate_result(existing_event, node)

    request = TransitionRequest(
        to_state=to_state,
        decision_method=decision_method,
        actor=actor,
        actor_kind=actor_kind,
        reason=reason,
        reason_code=reason_code,
        evidence_refs=evidence_refs_t,
    )

    now = time.time()
    # BEGIN IMMEDIATE takes the write lock before the facts below are read,
    # so a concurrent transition cannot decide against the same from-state.
    conn.execute("BEGIN IMMEDIATE")
    try:
        node = _require_node(conn, system_id, node_id)
        # The same duplicate check again, now under the write lock. A winner
        # that committed between the pre-check above and this transaction has
        # ALREADY moved the maturity, so without this a raced retry of an
        # applied request would be answered with a rejection
        # (`illegal_transition`) instead of the duplicate it is.
        if idem:
            existing_event = _lookup_idempotent_event(
                conn, system_id=system_id, node_id=node_id, idempotency_key=idem
            )
            if existing_event is not None:
                conn.execute("ROLLBACK")
                return _duplicate_result(existing_event, node)

        facts = load_node_facts(conn, system_id=system_id, node_id=node_id)
        decision = evaluate_transition(facts, request)
        if not decision.allowed:
            conn.execute("ROLLBACK")
            return TransitionApplyResult(
                decision=decision, applied=False, duplicate=False, event=None, node=node,
            )

        event = _insert_event(
            conn, node_id=node_id, system_id=system_id, event_kind="transition",
            from_state=facts.maturity, to_state=to_state,
            actor=actor, actor_kind=actor_kind, decision_method=decision_method,
            reason_code=reason_code, reason=reason,
            evidence_json=json.dumps(list(evidence_refs_t)),
            idempotency_key=idem, created_at=now,
        )
        cur = conn.execute(
            "UPDATE evolution_node SET maturity = ?, updated_at = ? "
            "WHERE id = ? AND maturity = ?",
            (to_state, now, node_id, facts.maturity),
        )
        if cur.rowcount != 1:
            raise _MaturityChangedUnderRequest(facts.maturity)
        updated_node = conn.execute(
            "SELECT * FROM evolution_node WHERE id = ?", (node_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except _MaturityChangedUnderRequest as exc:
        conn.execute("ROLLBACK")
        raise EvolutionNodeConflictError(
            f"Node {node_id}'s maturity changed while this transition was "
            f"being applied (it was no longer {exc.expected_maturity!r}); "
            "nothing was recorded. Re-read the Node and request again."
        ) from exc
    except sqlite3.IntegrityError:
        # A concurrent request with the SAME idempotency_key won the race and
        # its event was refused entry by the partial unique index rather than
        # being seen by either duplicate check above. Under a serializing
        # connection the in-transaction check makes this unreachable, but it
        # stays as the last line of the same defence: a retry is the
        # situation idempotency exists for, so resolve to the winner's event
        # exactly as the sequential-retry path does. Any other integrity
        # violation re-raises unchanged -- the re-select below is the
        # discriminator, not the exception type.
        conn.execute("ROLLBACK")
        if idem:
            existing_event = _lookup_idempotent_event(
                conn, system_id=system_id, node_id=node_id, idempotency_key=idem
            )
            if existing_event is not None:
                current_node = conn.execute(
                    "SELECT * FROM evolution_node WHERE id = ?", (node_id,)
                ).fetchone()
                return _duplicate_result(existing_event, current_node)
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return TransitionApplyResult(
        decision=decision, applied=True, duplicate=False, event=event, node=updated_node,
    )


def list_nodes(conn, *, system_id: int) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM evolution_node WHERE system_id = ? ORDER BY id DESC",
        (system_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Canonical projection
# ---------------------------------------------------------------------------


def _current_links(conn, system_id: int, node_id: int) -> List[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM evolution_node_link
               WHERE node_id = ? AND system_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
        (node_id, system_id),
    ).fetchall()


def _resolve_linked_cell_definition_id(
    conn, system_id: int, links: Sequence[sqlite3.Row]
) -> Tuple[Optional[int], bool]:
    """Resolve the Cell (`cell_definitions.id`) this Node's links point at.

    Returns ``(cell_definition_id, available)``. ``available=False`` means a
    `cell_binding` link explicitly names a `cell_bindings` row that no
    longer resolves -- a genuine read failure, never silently treated as
    "no Cell linked". A `component` link whose Component has no matching
    Cell yet is NOT a failure (`available=True`, `cell_definition_id=None`)
    -- most Components never become a Cell.
    """
    for link in links:
        if link["link_kind"] == "cell_binding" and link["target_row_id"] is not None:
            binding_row = conn.execute(
                "SELECT cell_definition_id FROM cell_bindings WHERE id = ? AND system_id = ?",
                (link["target_row_id"], system_id),
            ).fetchone()
            if binding_row is None:
                return None, False
            return binding_row["cell_definition_id"], True
    for link in links:
        if link["link_kind"] == "component":
            cell_row = conn.execute(
                "SELECT id FROM cell_definitions WHERE system_id = ? AND cell_id = ?",
                (system_id, link["target_ref"]),
            ).fetchone()
            if cell_row is not None:
                return cell_row["id"], True
    return None, True


def _resolve_improvement_status(
    conn, system_id: int, links: Sequence[sqlite3.Row]
) -> Tuple[Optional[str], bool]:
    """The latest `cell_improvements.status` for the Cell this Node links
    to, or `(None, True)` when no Cell is linked. Read from the EXISTING
    `cell_improvements` table (Issue #304) only -- this module never writes
    to it."""
    cell_definition_id, available = _resolve_linked_cell_definition_id(conn, system_id, links)
    if not available:
        return None, False
    if cell_definition_id is None:
        return None, True
    row = conn.execute(
        """SELECT status FROM cell_improvements
               WHERE system_id = ? AND cell_definition_id = ?
               ORDER BY id DESC LIMIT 1""",
        (system_id, cell_definition_id),
    ).fetchone()
    return (row["status"] if row is not None else None), True


def _resolve_policy_mode(
    conn, system_id: int, links: Sequence[sqlite3.Row]
) -> Tuple[Optional[str], bool]:
    """The SDK policy `mode` (`off`/`trace`/`shadow`) of the linked
    Component, read from the EXISTING `components` table, or `(None, True)`
    when no `component` link exists. A linked Component with no row yet in
    `components` (never traced, no policy ever set) is also `(None, True)`
    -- a legitimate "not yet observed" fact, not a read failure."""
    component_link = next((link for link in links if link["link_kind"] == "component"), None)
    if component_link is None:
        return None, True
    row = conn.execute(
        "SELECT mode FROM components WHERE system_id = ? AND component_id = ?",
        (system_id, component_link["target_ref"]),
    ).fetchone()
    return (row["mode"] if row is not None else None), True


def _json_or_default(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def _version_doc(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "version_number": row["version_number"],
        "mission": row["mission"],
        "scope": row["scope"],
        "out_of_scope": row["out_of_scope"],
        "input_contract": _json_or_default(row["input_contract"], {}),
        "output_contract": _json_or_default(row["output_contract"], {}),
        "side_effect_class": row["side_effect_class"],
        "trust_boundary": row["trust_boundary"],
        "establishment_criteria": _json_or_default(row["establishment_criteria"], []),
        "reopen_criteria": _json_or_default(row["reopen_criteria"], []),
        "evaluation_policy_refs": _json_or_default(row["evaluation_policy_refs"], []),
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def _implementation_doc(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "implementation_number": row["implementation_number"],
        "node_version_id": row["node_version_id"],
        "modality": row["modality"],
        "config": _json_or_default(row["config_json"], {}),
        "snapshot_id": row["snapshot_id"],
        "commit_sha": row["commit_sha"],
        "environment_ref": row["environment_ref"],
        "provenance": _json_or_default(row["provenance_json"], {}),
        "created_by": row["created_by"],
        "decision_method": row["decision_method"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def _link_doc(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "link_kind": row["link_kind"],
        "target_ref": row["target_ref"],
        "target_row_id": row["target_row_id"],
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def _event_doc(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "event_kind": row["event_kind"],
        "from_state": row["from_state"],
        "to_state": row["to_state"],
        "from_version_id": row["from_version_id"],
        "to_version_id": row["to_version_id"],
        "from_implementation_id": row["from_implementation_id"],
        "to_implementation_id": row["to_implementation_id"],
        "actor": row["actor"],
        "actor_kind": row["actor_kind"],
        "decision_method": row["decision_method"],
        "reason_code": row["reason_code"],
        "reason": row["reason"],
        "evidence": _json_or_default(row["evidence_json"], []),
        "idempotency_key": row["idempotency_key"] or None,
        "created_at": row["created_at"],
    }


# The four `*_doc` builders are the single definition of how a row is
# rendered, and the route layer must use these rather than re-reading columns
# itself: a second rendering is a second place for a field to be dropped,
# renamed, or JSON-decoded differently from the projection the Dashboard
# already reads. They are exported under public names so `routes/` can call
# them without reaching for a private symbol.
version_doc = _version_doc
implementation_doc = _implementation_doc
link_doc = _link_doc
event_doc = _event_doc


def list_events(
    conn, *, system_id: int, node_id: int, limit: int = 50, offset: int = 0
) -> List[Dict[str, Any]]:
    """The Node's append-only lineage, newest first.

    Exposed in its own right (not just as `build_node_projection`'s tail)
    because ADR-4 makes the log the authority: folding it must reproduce the
    stored maturity, so an auditor has to be able to read the whole log
    without also pulling the projection it is supposed to check. `offset`
    exists for exactly that reader: a long-lived Node's log outgrows any
    single bounded page, and a fold over a truncated log would reconcile
    against the wrong history.
    """
    if offset < 0:
        raise EvolutionNodeValidationError("offset must be >= 0")
    _require_node(conn, system_id, node_id)
    rows = conn.execute(
        """SELECT * FROM evolution_node_event WHERE node_id = ? AND system_id = ?
               ORDER BY id DESC LIMIT ? OFFSET ?""",
        (node_id, system_id, limit, offset),
    ).fetchall()
    return [_event_doc(row) for row in rows]


def build_node_projection(
    conn, *, system_id: int, node_id: int, event_limit: int = 20
) -> Dict[str, Any]:
    """Build the canonical Node projection (validated by
    `shared/schemas/evolution_node.schema.json`).

    Central rule (Epic #394): `maturity` / `improvement_status` /
    `policy_mode` are FOUR independent axes -- the fourth, `workflow_phase`,
    is intentionally OMITTED from this document entirely (not set to
    `null`): Phase 6 of this Epic wires it from a canonical workflow-phase
    source once one exists.
    `evaluate_session_workflow` from `app/interview_workflow.py` is never
    called here -- it PERSISTS a checkpoint, and a read must never write
    (the #380 rule). None of the three included axes is derived from either
    of the others: `maturity` comes from this module's own event log,
    `improvement_status` from `cell_improvements`, and `policy_mode` from
    `components` -- three independent reads, three independent answers, even
    when they disagree with each other.

    `maturity` is the stored column and `folded_maturity` is what this
    Node's own transition events fold to (ADR-4: the log is the authority,
    the column is its cache). Both are reported, plus `maturity_consistent`
    -- returning only the column would make the very drift the log exists to
    expose invisible to every reader of the canonical projection. A Node
    with no transition yet folds to `null`, which is CONSISTENT with the
    stored creation state `exploring`.

    `availability` marks any block that could not be genuinely read as
    unavailable rather than substituting a default value for it (the #380
    "an unreadable fact is never a fact's default value" rule): a `None`
    value paired with `availability[<key>] = True` means "genuinely absent",
    while `availability[<key>] = False` means "could not be determined".
    `availability["maturity_lineage"] = False` therefore leaves BOTH
    reconciliation fields `null`: an unread log proves neither agreement nor
    drift.
    """
    node = _require_node(conn, system_id, node_id)
    availability: Dict[str, bool] = {}

    def _resolve_pointer(pointer_id: Optional[int], table: str, availability_key: str):
        if pointer_id is None:
            availability[availability_key] = True
            return None
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id = ? AND node_id = ? AND system_id = ?",
            (pointer_id, node_id, system_id),
        ).fetchone()
        availability[availability_key] = row is not None
        return row

    version_row = _resolve_pointer(
        node["current_version_id"], "evolution_node_version", "version"
    )
    implementation_row = _resolve_pointer(
        node["current_implementation_id"], "evolution_node_implementation", "implementation"
    )
    stable_row = _resolve_pointer(
        node["stable_implementation_id"], "evolution_node_implementation",
        "stable_implementation",
    )
    rollback_row = _resolve_pointer(
        node["rollback_implementation_id"], "evolution_node_implementation",
        "rollback_implementation",
    )

    links = _current_links(conn, system_id, node_id)
    availability["links"] = True

    events = conn.execute(
        """SELECT * FROM evolution_node_event WHERE node_id = ? AND system_id = ?
               ORDER BY id DESC LIMIT ?""",
        (node_id, system_id, event_limit),
    ).fetchall()
    availability["events"] = True

    # ADR-4's reconciliation, actually performed rather than asserted: the
    # stored `maturity` column is a cache of the event log's fold, so the
    # projection reports BOTH and whether they agree. This is a dedicated,
    # UNBOUNDED query over the transition events -- folding the bounded
    # `events` page above would reconcile against a truncated history and
    # report a healthy Node as drifted.
    folded_maturity: Optional[str] = None
    maturity_consistent: Optional[bool] = None
    try:
        transition_rows = conn.execute(
            """SELECT id, event_kind, to_state FROM evolution_node_event
                   WHERE node_id = ? AND system_id = ? AND event_kind = 'transition'
                   ORDER BY id""",
            (node_id, system_id),
        ).fetchall()
    except sqlite3.Error:
        availability["maturity_lineage"] = False
    else:
        availability["maturity_lineage"] = True
        folded_maturity = fold_events(
            [
                {"id": row["id"], "event_kind": row["event_kind"], "to_state": row["to_state"]}
                for row in transition_rows
            ]
        )
        # A Node that has never transitioned folds to None, and its stored
        # maturity must then be the creation state 'exploring' -- that pair
        # is CONSISTENT, not a missing lineage.
        maturity_consistent = (
            node["maturity"] == "exploring"
            if folded_maturity is None
            else folded_maturity == node["maturity"]
        )

    improvement_status, improvement_available = _resolve_improvement_status(
        conn, system_id, links
    )
    availability["improvement_status"] = improvement_available

    policy_mode, policy_available = _resolve_policy_mode(conn, system_id, links)
    availability["policy_mode"] = policy_available

    return {
        "schema_version": EVOLUTION_NODE_PROJECTION_SCHEMA_VERSION,
        "system_id": system_id,
        "node_id": node_id,
        "node_key": node["node_key"],
        "display_name": node["display_name"],
        "maturity": node["maturity"],
        "folded_maturity": folded_maturity,
        "maturity_consistent": maturity_consistent,
        "current_version": _version_doc(version_row) if version_row is not None else None,
        "current_implementation": (
            _implementation_doc(implementation_row) if implementation_row is not None else None
        ),
        "stable_implementation": (
            _implementation_doc(stable_row) if stable_row is not None else None
        ),
        "rollback_implementation": (
            _implementation_doc(rollback_row) if rollback_row is not None else None
        ),
        "links": [_link_doc(link) for link in links],
        "events": [_event_doc(event) for event in events],
        "improvement_status": improvement_status,
        "policy_mode": policy_mode,
        "availability": availability,
        "updated_at": node["updated_at"],
    }


def build_legacy_projection(conn, *, system_id: int, node_id: int) -> Dict[str, Any]:
    """A small, honest Component/Cell-shaped compatibility view of a Node
    (ADR-8, docs/evolutionary-pipeline.md), so an existing consumer written
    against Component/Cell fields keeps working during migration. This is
    NOT a second canonical projection -- `build_node_projection` remains the
    only authority for anything beyond these four fields."""
    node = _require_node(conn, system_id, node_id)
    links = _current_links(conn, system_id, node_id)

    component_id = next(
        (link["target_ref"] for link in links if link["link_kind"] == "component"), None
    )
    probe_point_ref = next(
        (link["target_ref"] for link in links if link["link_kind"] == "probe_point"), None
    )
    cell_definition_id, cell_available = _resolve_linked_cell_definition_id(
        conn, system_id, links
    )
    cell_id = None
    if cell_available and cell_definition_id is not None:
        cell_row = conn.execute(
            "SELECT cell_id FROM cell_definitions WHERE id = ? AND system_id = ?",
            (cell_definition_id, system_id),
        ).fetchone()
        cell_id = cell_row["cell_id"] if cell_row is not None else None

    return {
        "schema_version": EVOLUTION_NODE_LEGACY_PROJECTION_SCHEMA_VERSION,
        "compatibility_projection": True,
        "system_id": system_id,
        "node_id": node_id,
        "node_key": node["node_key"],
        "component_id": component_id,
        "probe_point_ref": probe_point_ref,
        "cell_id": cell_id,
        "maturity": node["maturity"],
    }
