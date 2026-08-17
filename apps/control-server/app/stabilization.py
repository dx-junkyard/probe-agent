"""Stabilization Evidence Package and the establishment gate
(Issue #399, Phase 4 of the evolution control plane Epic #394).

This is the phase the rest of the Epic builds toward: deciding that a Node's
processing is understood well enough to pin a stable implementation.

**Fixation is not "we removed the LLM."** It is "the conditions under which
this processing works are now understood well enough to pin a reproducible,
rollback-able implementation". An implementation whose modality stays
`reasoning_llm` can be established exactly as legitimately as a `rule` one --
the gate below never reads modality, deliberately.

The gate (`evaluate_establishment_gate`) is a **pure first-match table over
16 enumerated refusal codes**. Three properties matter more than the
individual rows:

1. **It fails closed on absence, not just on failure.** `floor_unmeasured`
   refuses as firmly as `floor_violated`, because "the safety floor held" and
   "nobody measured the safety floor" are different facts and only the first
   may establish. This is the same discipline as Phase 3's `incomparable`,
   applied to a decision instead of a comparison.
2. **It never composites.** There is no score and no threshold arithmetic
   across dimensions: every criterion and every floor is checked
   individually, so a latency win cannot pay for a safety regression
   (ADR-7).
3. **It is not the approval.** Passing the gate makes a package *eligible*;
   `approve_package` still requires a named human, and the resulting
   `validating -> established` transition goes through Phase 1's own
   evaluator with `decision_method: manual` (ADR-9). Passing the gate never
   applies source, changes a policy, deploys, or publishes -- those keep
   their existing separate gates (Principle 5/8).

Evidence is referenced, never copied, and its currency is evaluated at GATE
time rather than at build time: a copied number keeps reading as current
after the run it came from is superseded.

probe-agent:
  role: Stabilization Evidence Package persistence and the deterministic establishment gate
  capability: evolution-control-plane
  element_type: core
  operation_kind: analysis
  consumers: [control-server-routes]
  state_effects: [database-read, database-write]
  probe_value: Verify the gate refuses an unmeasured floor as firmly as a violated one, refuses mock or foreign evidence, never reads implementation modality, never composites dimensions, and that passing it neither approves nor applies anything without a named human.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, get_args

from . import evolution_node

__all__ = [
    "EVIDENCE_LEVELS",
    "EVIDENCE_KINDS",
    "EVIDENCE_VERDICTS",
    "PACKAGE_STATUSES",
    "GATE_REFUSAL_CODES",
    "REQUIRED_EVIDENCE_LEVELS",
    "StabilizationError",
    "StabilizationNotFoundError",
    "StabilizationValidationError",
    "StabilizationConflictError",
    "GateFacts",
    "GateDecision",
    "EvidenceFact",
    "evaluate_establishment_gate",
    "create_package",
    "add_evidence",
    "gather_gate_facts",
    "evaluate_package",
    "approve_package",
    "reject_package",
    "build_package_projection",
]


# ---------------------------------------------------------------------------
# Finite vocabularies (Principle 6)
# ---------------------------------------------------------------------------

EvidenceLevel = Literal["node", "flow_capability", "ux_outcome"]
EVIDENCE_LEVELS: Tuple[str, ...] = get_args(EvidenceLevel)

EvidenceKind = Literal[
    "criterion", "floor", "downstream_impact", "outcome", "stability"
]
EVIDENCE_KINDS: Tuple[str, ...] = get_args(EvidenceKind)

EvidenceVerdict = Literal[
    "met", "not_met", "held", "violated", "unmeasured", "not_applicable"
]
EVIDENCE_VERDICTS: Tuple[str, ...] = get_args(EvidenceVerdict)

PackageStatus = Literal["draft", "under_review", "approved", "rejected", "superseded"]
PACKAGE_STATUSES: Tuple[str, ...] = get_args(PackageStatus)

EvidenceRefKind = Literal[
    "exploration_run", "exploration_variant", "replay_run", "experiment",
    "evaluation_policy",
]
EVIDENCE_REF_KINDS: Tuple[str, ...] = get_args(EvidenceRefKind)

# Node-level evidence is required; Flow and Outcome evidence may legitimately
# be absent-but-declared. The asymmetry is deliberate: a Node cannot be
# established without evidence about the Node itself, but a Flow whose
# downstream is not instrumented and an Outcome that is not yet measurable
# are real, common, and #399-sanctioned states -- provided they are DECLARED
# rather than silently missing.
REQUIRED_EVIDENCE_LEVELS: Tuple[str, ...] = ("node",)

GATE_REFUSAL_CODES: Tuple[str, ...] = (
    "ok",
    "package_not_draft_or_review",
    "package_superseded",
    "node_not_validating",
    "candidate_implementation_missing",
    "candidate_version_mismatch",
    "node_evidence_missing",
    "foreign_evidence",
    "mock_evidence",
    "criterion_not_met",
    "floor_violated",
    "floor_unmeasured",
    "downstream_impact_violated",
    "outcome_unmeasured_unacknowledged",
    "stability_window_insufficient",
    "applicability_envelope_missing",
    "rollback_target_missing",
)


class StabilizationError(ValueError):
    """Base class for every failure this module raises."""


class StabilizationNotFoundError(StabilizationError):
    """A referenced row does not exist, or belongs to another System."""


class StabilizationValidationError(StabilizationError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class StabilizationConflictError(StabilizationError):
    """The row exists but is not in a state where this operation is legal."""


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise StabilizationValidationError(
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
# The pure gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceFact:
    level: str
    kind: str
    name: str
    verdict: str
    is_mock: bool = False
    belongs_to_package: bool = True
    detail: str = ""


@dataclass(frozen=True)
class GateFacts:
    """Everything the gate needs, as plain values.

    Deliberately does NOT carry the candidate implementation's modality. The
    gate must not be able to prefer a rule over an LLM or the reverse: #399's
    non-goal is "making LLM usage minimal an end in itself", and a gate that
    could read modality is a gate that could encode that preference.
    """

    package_status: str
    package_superseded: bool
    node_maturity: str
    candidate_implementation_present: bool
    candidate_matches_node_version: bool
    evidence: Tuple[EvidenceFact, ...]
    applicability_envelope_declared: bool
    rollback_target_present: bool
    is_first_establishment: bool
    outcome_unmeasured_reason: str
    required_case_count: int
    observed_case_count: Optional[int]
    stability_window_seconds: float
    observed_window_seconds: Optional[float]


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason_code: str
    message: str
    failing_evidence: Tuple[str, ...] = ()


def evaluate_establishment_gate(facts: GateFacts) -> GateDecision:
    """Decide whether a package may establish its candidate implementation.

    Pure: same facts in, same decision out. No clock, no DB, no ordering
    dependence, and no reasoning-model call anywhere.

    The rows are first-match and their order is the contract. Structural
    preconditions come before evidence checks, because reporting "the safety
    floor is unmeasured" for a package whose candidate implementation no
    longer exists would send the developer to fix the wrong thing.
    """
    # --- structural preconditions -----------------------------------------
    if facts.package_superseded:
        return GateDecision(
            False, "package_superseded",
            "This package has been superseded by a newer one; establish from that.",
        )
    if facts.package_status not in ("draft", "under_review"):
        return GateDecision(
            False, "package_not_draft_or_review",
            f"A package in {facts.package_status!r} is not awaiting a decision.",
        )
    if facts.node_maturity != "validating":
        # Phase 1's evaluator would refuse the transition anyway; refusing
        # here means the developer is told WHY before they spend a review.
        return GateDecision(
            False, "node_not_validating",
            f"Establishment is decided from 'validating'; this Node is "
            f"{facts.node_maturity!r}.",
        )
    if not facts.candidate_implementation_present:
        return GateDecision(
            False, "candidate_implementation_missing",
            "The candidate implementation this package argues for no longer exists.",
        )
    if not facts.candidate_matches_node_version:
        return GateDecision(
            False, "candidate_version_mismatch",
            "The candidate implements a different contract version than the "
            "package's; the evidence describes a different promise.",
        )

    # --- provenance -------------------------------------------------------
    foreign = tuple(e.name for e in facts.evidence if not e.belongs_to_package)
    if foreign:
        return GateDecision(
            False, "foreign_evidence",
            "Evidence from another System, Node, or package cannot establish "
            "this one.",
            foreign,
        )
    mock = tuple(e.name for e in facts.evidence if e.is_mock)
    if mock:
        # Principle 7: mock LLM output is test data. It may be visible, but it
        # can never be the basis of a decision.
        return GateDecision(
            False, "mock_evidence",
            "Mock output is test data and cannot be establishment evidence.",
            mock,
        )

    # --- required evidence ------------------------------------------------
    for level in REQUIRED_EVIDENCE_LEVELS:
        if not any(e.level == level for e in facts.evidence):
            return GateDecision(
                False, "node_evidence_missing",
                f"No {level}-level evidence: a Node cannot be established "
                "without evidence about the Node itself.",
            )

    # --- criteria and floors, each checked individually -------------------
    #
    # Individually, never summed. A latency win must not be able to pay for a
    # safety regression, so there is no arithmetic across these at all.
    unmet = tuple(
        e.name for e in facts.evidence if e.kind == "criterion" and e.verdict == "not_met"
    )
    if unmet:
        return GateDecision(
            False, "criterion_not_met",
            "An establishment criterion was not reached.", unmet,
        )

    violated = tuple(
        e.name for e in facts.evidence if e.kind == "floor" and e.verdict == "violated"
    )
    if violated:
        return GateDecision(
            False, "floor_violated", "A protected floor was broken.", violated,
        )

    # An unmeasured floor refuses as firmly as a violated one. "The floor
    # held" and "nobody measured the floor" are different facts, and only the
    # first may establish -- treating the second as passing is how a gate
    # silently becomes decorative.
    unmeasured_floors = tuple(
        e.name for e in facts.evidence if e.kind == "floor" and e.verdict == "unmeasured"
    )
    if unmeasured_floors:
        return GateDecision(
            False, "floor_unmeasured",
            "A protected floor has no measurement. An unmeasured floor is not "
            "a floor that held.",
            unmeasured_floors,
        )

    downstream = tuple(
        e.name
        for e in facts.evidence
        if e.kind == "downstream_impact" and e.verdict in ("violated", "not_met")
    )
    if downstream:
        return GateDecision(
            False, "downstream_impact_violated",
            "A Flow/Capability-level result regressed; a Node-level win is not "
            "evidence that the Flow it sits in improved.",
            downstream,
        )

    # --- Outcome: unmeasured is acceptable, silence is not ----------------
    unmeasured_outcomes = tuple(
        e.name
        for e in facts.evidence
        if e.kind == "outcome" and e.verdict == "unmeasured"
    )
    if unmeasured_outcomes and not facts.outcome_unmeasured_reason.strip():
        # #391's rule: never infer an Outcome, and never let its absence pass
        # unremarked. The gate does not demand a measured Outcome -- it
        # demands that an unmeasured one be acknowledged in writing.
        return GateDecision(
            False, "outcome_unmeasured_unacknowledged",
            "An Outcome is unmeasured and no reason was recorded. Establishing "
            "without an Outcome is allowed; doing so silently is not.",
            unmeasured_outcomes,
        )

    # --- stability: enough evidence, by the package's own declaration -----
    #
    # The requirement is per package (no global threshold -- #399 forbids one)
    # but it is declared before the gate runs, so it cannot be lowered to fit
    # the result that came back.
    if facts.required_case_count > 0:
        if facts.observed_case_count is None:
            return GateDecision(
                False, "stability_window_insufficient",
                "The number of observed cases is unknown; an unmeasured sample "
                "is not a sufficient one.",
            )
        if facts.observed_case_count < facts.required_case_count:
            return GateDecision(
                False, "stability_window_insufficient",
                f"{facts.observed_case_count} cases observed, "
                f"{facts.required_case_count} required by this package.",
            )
    if facts.stability_window_seconds > 0:
        if facts.observed_window_seconds is None:
            return GateDecision(
                False, "stability_window_insufficient",
                "The observed stability window is unknown.",
            )
        if facts.observed_window_seconds < facts.stability_window_seconds:
            return GateDecision(
                False, "stability_window_insufficient",
                "The observed stability window is shorter than this package "
                "declared necessary.",
            )

    # --- generalisation and reversibility ---------------------------------
    if not facts.applicability_envelope_declared:
        # Without a declared envelope a success generalises to every input by
        # default. "It worked on the cases it was built for" and "it worked"
        # are different claims and only the first was ever demonstrated.
        return GateDecision(
            False, "applicability_envelope_missing",
            "No applicability envelope is declared, so the result would "
            "generalise past what was measured.",
        )
    if not facts.rollback_target_present and not facts.is_first_establishment:
        # A first establishment has nothing to roll back TO, and that is a
        # legitimate state rather than a missing artefact. Every later one
        # must name where it returns to.
        return GateDecision(
            False, "rollback_target_missing",
            "No rollback target: a Node that already had a stable "
            "implementation must name where it returns to.",
        )

    return GateDecision(
        True, "ok",
        "Every declared criterion and floor is satisfied, the result is bounded "
        "by a declared envelope, and the change is reversible. A human approval "
        "is still required.",
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _require_package(
    conn: sqlite3.Connection, system_id: int, package_id: int
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM stabilization_package WHERE id = ? AND system_id = ?",
        (package_id, system_id),
    ).fetchone()
    if row is None:
        raise StabilizationNotFoundError(
            f"Stabilization package {package_id} not found"
        )
    return row


def create_package(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_id: int,
    candidate_implementation_id: int,
    exploration_run_id: Optional[int] = None,
    applicability_envelope: Optional[Mapping[str, Any]] = None,
    known_limitations: Sequence[str] = (),
    residual_risks: Sequence[str] = (),
    required_case_count: int = 0,
    stability_window_seconds: float = 0.0,
    observed_case_count: Optional[int] = None,
    observed_window_seconds: Optional[float] = None,
    outcome_unmeasured_reason: str = "",
    rollback_plan: str = "",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Open a package arguing for ONE candidate implementation.

    The node version and the baseline/rollback targets are read from the
    Node's own current state rather than accepted from the caller: they are
    facts about the Node at this moment, and letting a caller assert them
    would let a package claim a rollback target the Node does not have.
    """
    node = conn.execute(
        "SELECT * FROM evolution_node WHERE id = ? AND system_id = ?",
        (node_id, system_id),
    ).fetchone()
    if node is None:
        raise StabilizationNotFoundError(f"Evolution Node {node_id} not found")

    candidate = conn.execute(
        """SELECT * FROM evolution_node_implementation
               WHERE id = ? AND node_id = ? AND system_id = ?""",
        (candidate_implementation_id, node_id, system_id),
    ).fetchone()
    if candidate is None:
        raise StabilizationNotFoundError(
            f"Implementation {candidate_implementation_id} does not belong to "
            f"Node {node_id}"
        )
    if node["current_version_id"] is None:
        raise StabilizationConflictError(
            f"Node {node_id} has no contract version to establish against"
        )

    if exploration_run_id is not None:
        run = conn.execute(
            "SELECT id FROM exploration_run WHERE id = ? AND system_id = ?",
            (exploration_run_id, system_id),
        ).fetchone()
        if run is None:
            raise StabilizationNotFoundError(
                f"Exploration run {exploration_run_id} not found"
            )

    now = time.time()
    cur = conn.execute(
        """INSERT INTO stabilization_package
               (system_id, node_id, node_version_id, candidate_implementation_id,
                baseline_implementation_id, exploration_run_id,
                applicability_envelope_json, known_limitations_json,
                residual_risks_json, required_case_count, stability_window_seconds,
                observed_case_count, observed_window_seconds,
                outcome_unmeasured_reason, rollback_implementation_id, rollback_plan,
                created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, node_id, node["current_version_id"], candidate_implementation_id,
            node["stable_implementation_id"], exploration_run_id,
            json.dumps(dict(applicability_envelope or {})),
            json.dumps(list(known_limitations)),
            json.dumps(list(residual_risks)),
            required_case_count, stability_window_seconds,
            observed_case_count, observed_window_seconds,
            outcome_unmeasured_reason,
            # The rollback target is whatever is stable NOW -- establishing
            # this candidate is exactly what would make it the thing to
            # return to.
            node["stable_implementation_id"], rollback_plan,
            created_by, now,
        ),
    )
    return conn.execute(
        "SELECT * FROM stabilization_package WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def add_evidence(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    package_id: int,
    evidence_level: str,
    evidence_kind: str,
    name: str,
    verdict: str,
    ref_kind: Optional[str] = None,
    ref_id: Optional[int] = None,
    evaluation_policy_id: Optional[int] = None,
    detail: str = "",
    is_mock: bool = False,
    source: str = "deterministic",
) -> sqlite3.Row:
    """Attach one referenced result to a package.

    Every reference is resolved against THIS System. A reference that does
    not resolve is refused rather than stored unresolvable: the gate would
    otherwise have to decide what an unresolvable reference means, and every
    answer to that is worse than not accepting it.
    """
    _check_membership(evidence_level, EVIDENCE_LEVELS, "evidence_level")
    _check_membership(evidence_kind, EVIDENCE_KINDS, "evidence_kind")
    _check_membership(verdict, EVIDENCE_VERDICTS, "verdict")
    _check_membership(source, ("deterministic", "reasoning_llm", "manual"), "source")
    package = _require_package(conn, system_id, package_id)
    if package["status"] not in ("draft", "under_review"):
        raise StabilizationConflictError(
            f"Package {package_id} is {package['status']}; evidence is not added "
            "to a decided package"
        )

    if ref_kind is not None:
        _check_membership(ref_kind, EVIDENCE_REF_KINDS, "ref_kind")
        if ref_id is None:
            raise StabilizationValidationError("ref_kind requires a ref_id")
        table = {
            "exploration_run": "exploration_run",
            "exploration_variant": "exploration_variant",
            "replay_run": "replay_runs",
            "experiment": "experiments",
            "evaluation_policy": "evolution_evaluation_policy",
        }[ref_kind]
        row = conn.execute(
            f"SELECT id FROM {table} WHERE id = ? AND system_id = ?",
            (ref_id, system_id),
        ).fetchone()
        if row is None:
            raise StabilizationNotFoundError(
                f"{ref_kind} {ref_id} not found in this System"
            )

    if evaluation_policy_id is not None:
        row = conn.execute(
            "SELECT id FROM evolution_evaluation_policy WHERE id = ? AND system_id = ?",
            (evaluation_policy_id, system_id),
        ).fetchone()
        if row is None:
            raise StabilizationNotFoundError(
                f"Evaluation policy {evaluation_policy_id} not found"
            )

    now = time.time()
    cur = conn.execute(
        """INSERT INTO stabilization_evidence
               (package_id, system_id, evidence_level, evidence_kind, name, verdict,
                ref_kind, ref_id, evaluation_policy_id, detail, is_mock, source,
                created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            package_id, system_id, evidence_level, evidence_kind, name, verdict,
            ref_kind, ref_id, evaluation_policy_id, detail,
            1 if is_mock else 0, source, now,
        ),
    )
    return conn.execute(
        "SELECT * FROM stabilization_evidence WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def gather_gate_facts(
    conn: sqlite3.Connection, *, system_id: int, package_id: int
) -> GateFacts:
    """Read every persisted fact the gate needs.

    Currency is evaluated HERE, at gate time, rather than at build time: a
    candidate implementation deleted or a contract version moved after the
    package was written must change the verdict, and neither is knowable when
    the package is assembled.
    """
    package = _require_package(conn, system_id, package_id)
    node = conn.execute(
        "SELECT * FROM evolution_node WHERE id = ? AND system_id = ?",
        (package["node_id"], system_id),
    ).fetchone()
    if node is None:
        raise StabilizationNotFoundError(f"Node {package['node_id']} not found")

    candidate = conn.execute(
        """SELECT * FROM evolution_node_implementation
               WHERE id = ? AND node_id = ? AND system_id = ?""",
        (package["candidate_implementation_id"], package["node_id"], system_id),
    ).fetchone()

    evidence_rows = conn.execute(
        "SELECT * FROM stabilization_evidence WHERE package_id = ? ORDER BY id",
        (package_id,),
    ).fetchall()
    evidence = tuple(
        EvidenceFact(
            level=row["evidence_level"],
            kind=row["evidence_kind"],
            name=row["name"],
            verdict=row["verdict"],
            is_mock=bool(row["is_mock"]),
            # The SQL above already restricts to this package, and add_evidence
            # resolves every reference against this System -- so anything read
            # here belongs. The flag exists so the gate's refusal is expressible
            # as its own code rather than as an empty evidence list.
            belongs_to_package=row["system_id"] == system_id,
            detail=row["detail"],
        )
        for row in evidence_rows
    )

    envelope = _json_or_default(package["applicability_envelope_json"], {})

    return GateFacts(
        package_status=package["status"],
        package_superseded=package["superseded_by_id"] is not None,
        node_maturity=node["maturity"],
        candidate_implementation_present=candidate is not None,
        candidate_matches_node_version=(
            candidate is not None
            and candidate["node_version_id"] == package["node_version_id"]
        ),
        evidence=evidence,
        applicability_envelope_declared=bool(envelope),
        rollback_target_present=package["rollback_implementation_id"] is not None,
        # A Node that has never had a stable pin has nothing to roll back to,
        # and that is legitimate rather than a missing artefact.
        is_first_establishment=node["stable_implementation_id"] is None,
        outcome_unmeasured_reason=package["outcome_unmeasured_reason"],
        required_case_count=package["required_case_count"],
        observed_case_count=package["observed_case_count"],
        stability_window_seconds=package["stability_window_seconds"],
        observed_window_seconds=package["observed_window_seconds"],
    )


def evaluate_package(
    conn: sqlite3.Connection, *, system_id: int, package_id: int
) -> GateDecision:
    """Run the gate against a package's current facts. Writes nothing.

    A read that decided something would make merely LOOKING at a package
    change it -- the #380 rule.
    """
    return evaluate_establishment_gate(
        gather_gate_facts(conn, system_id=system_id, package_id=package_id)
    )


def approve_package(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    package_id: int,
    approved_by: str,
    note: str = "",
) -> Tuple[sqlite3.Row, GateDecision, Any]:
    """Approve a package and establish its candidate.

    Order matters and is the contract:

    1. The gate is re-evaluated server-side. A caller's earlier PASS is never
       trusted -- evidence, the Node's maturity, and the candidate can all
       change between reading and approving.
    2. A named human is required. `approved_by` comes from the authenticated
       principal at the route, never from a request body (#337's rule).
    3. The candidate is pinned stable, which rotates the previous stable into
       the rollback slot.
    4. The `validating -> established` transition goes through Phase 1's own
       evaluator with `decision_method: manual`. It is not written directly:
       Phase 1 owns the transition table and the event log, and a second
       writer would be a second opinion about the Node's state.

    This approves establishment ONLY. It applies no source, changes no
    policy, deploys nothing, and publishes nothing -- each of those keeps its
    own separate human gate (Principle 5/8).
    """
    if not (approved_by or "").strip():
        raise StabilizationValidationError(
            "approve_package requires the approving person; establishment is "
            "never an anonymous or automatic decision"
        )
    package = _require_package(conn, system_id, package_id)
    decision = evaluate_package(conn, system_id=system_id, package_id=package_id)
    if not decision.allowed:
        raise StabilizationConflictError(
            f"The establishment gate refuses this package ({decision.reason_code}): "
            f"{decision.message}"
        )

    now = time.time()
    evolution_node.pin_stable_implementation(
        conn,
        system_id=system_id,
        node_id=package["node_id"],
        implementation_id=package["candidate_implementation_id"],
        actor=approved_by,
        decision_method="manual",
        reason=f"stabilization package {package_id}",
    )
    transition = evolution_node.apply_transition(
        conn,
        system_id=system_id,
        node_id=package["node_id"],
        to_state="established",
        decision_method="manual",
        actor=approved_by,
        actor_kind="developer",
        reason=note or f"approved stabilization package {package_id}",
        reason_code="stabilization_approved",
        evidence_refs=[f"stabilization_package:{package_id}"],
        idempotency_key=f"stabilization-{package_id}",
    )
    if not (transition.applied or transition.duplicate):
        raise StabilizationConflictError(
            "The Node's own transition evaluator refused the establishment "
            f"({transition.decision.reason_code}): {transition.decision.message}"
        )

    conn.execute(
        """UPDATE stabilization_package
               SET status = 'approved', approved_by = ?, approved_at = ?,
                   decision_note = ?
               WHERE id = ?""",
        (approved_by, now, note, package_id),
    )
    row = conn.execute(
        "SELECT * FROM stabilization_package WHERE id = ?", (package_id,)
    ).fetchone()
    return row, decision, transition


def reject_package(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    package_id: int,
    rejected_by: str,
    note: str = "",
) -> sqlite3.Row:
    """Reject a package. Changes no Node state at all.

    A rejection is a record that a human looked and said no; it is not a
    demotion, and it must not move the Node backwards on its own.
    """
    package = _require_package(conn, system_id, package_id)
    if package["status"] not in ("draft", "under_review"):
        raise StabilizationConflictError(
            f"Package {package_id} is already {package['status']}"
        )
    conn.execute(
        """UPDATE stabilization_package
               SET status = 'rejected', approved_by = ?, approved_at = ?,
                   decision_note = ?
               WHERE id = ?""",
        (rejected_by, time.time(), note, package_id),
    )
    return conn.execute(
        "SELECT * FROM stabilization_package WHERE id = ?", (package_id,)
    ).fetchone()


def build_package_projection(
    conn: sqlite3.Connection, *, system_id: int, package_id: int
) -> Dict[str, Any]:
    """The package plus its CURRENT gate verdict.

    The verdict is recomputed on every read rather than stored, for the same
    reason #337/#338/#349 derive rather than store a lifecycle value: a
    stored verdict drifts from the evidence it describes, a derived one
    cannot.
    """
    package = _require_package(conn, system_id, package_id)
    evidence_rows = conn.execute(
        "SELECT * FROM stabilization_evidence WHERE package_id = ? ORDER BY id",
        (package_id,),
    ).fetchall()
    decision = evaluate_package(conn, system_id=system_id, package_id=package_id)

    by_level: Dict[str, List[Dict[str, Any]]] = {level: [] for level in EVIDENCE_LEVELS}
    for row in evidence_rows:
        by_level[row["evidence_level"]].append(
            {
                "id": row["id"],
                "evidence_kind": row["evidence_kind"],
                "name": row["name"],
                "verdict": row["verdict"],
                "ref_kind": row["ref_kind"],
                "ref_id": row["ref_id"],
                "evaluation_policy_id": row["evaluation_policy_id"],
                "detail": row["detail"],
                "is_mock": bool(row["is_mock"]),
                "source": row["source"],
            }
        )

    return {
        "id": package["id"],
        "system_id": system_id,
        "node_id": package["node_id"],
        "node_version_id": package["node_version_id"],
        "candidate_implementation_id": package["candidate_implementation_id"],
        "baseline_implementation_id": package["baseline_implementation_id"],
        "rollback_implementation_id": package["rollback_implementation_id"],
        "rollback_plan": package["rollback_plan"],
        "exploration_run_id": package["exploration_run_id"],
        "applicability_envelope": _json_or_default(
            package["applicability_envelope_json"], {}
        ),
        "known_limitations": _json_or_default(package["known_limitations_json"], []),
        "residual_risks": _json_or_default(package["residual_risks_json"], []),
        "required_case_count": package["required_case_count"],
        "observed_case_count": package["observed_case_count"],
        "stability_window_seconds": package["stability_window_seconds"],
        "observed_window_seconds": package["observed_window_seconds"],
        "outcome_unmeasured_reason": package["outcome_unmeasured_reason"],
        "status": package["status"],
        "approved_by": package["approved_by"],
        "approved_at": package["approved_at"],
        "decision_note": package["decision_note"],
        # Grouped by level, never merged: a Node-level win is not evidence
        # that the Flow it sits in improved (ADR-7).
        "evidence": by_level,
        "gate": {
            "allowed": decision.allowed,
            "reason_code": decision.reason_code,
            "message": decision.message,
            "failing_evidence": list(decision.failing_evidence),
        },
        "created_by": package["created_by"],
        "created_at": package["created_at"],
    }
