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
an enumerated verdict vocabulary**: `GATE_REFUSAL_CODES` holds 22 refusal
codes plus `"ok"`, the pass verdict. (Unlike Phase 1's
`TRANSITION_REJECTION_CODES`, which enumerates rejections only, this tuple
includes `"ok"` because it is the vocabulary of `GateDecision.reason_code`,
and a pass is one of the values that field can carry.) A single further code,
`APPROVAL_REFUSAL_CODES`' `parent_approver_identical`, belongs to the
approval rather than the gate -- the gate is recomputed on every read, where
no approver exists to compare against. Five properties matter more than the
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
4. **Nothing that can satisfy it may be self-reported.** An asserting verdict
   (`met` / `held`) on a kind the gate can be satisfied by must cite a run
   that was actually executed and still resolves in this System, and the
   package must declare -- in advance, positively -- how much evidence it
   considers sufficient. A negative verdict stays ref-free on purpose: it can
   only ever block, and refusing must never be harder than establishing.
5. **The parent review and the human approval are two records, never one.**
   `record_parent_review` writes an `endorsed` / `declined` disposition with
   its own reviewer and timestamp; `approve_package` writes the approval. The
   gate requires the first, and the approval refuses when the same person
   discharged both -- one person filling both roles reproduces exactly the
   conflation the separation exists to prevent (#304).

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
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, get_args

from . import evolution_node

__all__ = [
    "EVIDENCE_LEVELS",
    "EVIDENCE_KINDS",
    "EVIDENCE_VERDICTS",
    "PACKAGE_STATUSES",
    "PARENT_REVIEW_DISPOSITIONS",
    "GATE_REFUSAL_CODES",
    "APPROVAL_REFUSAL_CODES",
    "REQUIRED_EVIDENCE_LEVELS",
    "ASSERTING_VERDICTS",
    "REFERENCE_REQUIRED_EVIDENCE_KINDS",
    "EXECUTION_REF_KINDS",
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
    "record_parent_review",
    "gather_gate_facts",
    "evaluate_package",
    "approve_package",
    "reject_package",
    "supersede_package",
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

# The subset of `EVIDENCE_REF_KINDS` that names something that was actually
# EXECUTED. `evaluation_policy` is deliberately excluded: a policy declares
# what was required, never what was observed, so citing it as the provenance
# of a `met` would be citing the question as the answer.
EXECUTION_REF_KINDS: Tuple[str, ...] = (
    "exploration_run", "exploration_variant", "replay_run", "experiment",
)

# The verdicts that ASSERT a result rather than report its absence. Only
# these can ever move the gate toward a pass, so only these need provenance.
#
# `not_met` / `violated` deliberately stay ref-free. A negative self-report
# can only ever BLOCK: no ordering of the gate's rows lets one establish
# anything. Demanding provenance for a refusal would make refusing harder
# than establishing, which is exactly backwards for a fail-closed gate --
# a developer who noticed a regression must always be able to write it down.
# `unmeasured` / `not_applicable` are ref-free by definition: there is no run
# to cite, and that IS the fact being recorded.
ASSERTING_VERDICTS: Tuple[str, ...] = ("met", "held")

# The evidence kinds whose asserting verdicts can SATISFY a gate row, and
# which therefore may not be self-reported.
#
# `downstream_impact` and `outcome` are excluded after checking how the gate
# actually consumes them: it reads `downstream_impact` only to refuse
# (`violated` / `not_met`) and `outcome` only to refuse (`unmeasured` with no
# acknowledgement). A `met` row of either kind satisfies nothing, so it cannot
# establish anything and is accepted without a reference.
REFERENCE_REQUIRED_EVIDENCE_KINDS: Tuple[str, ...] = (
    "criterion", "floor", "stability",
)

# Node-level evidence is required; Flow and Outcome evidence may legitimately
# be absent-but-declared. The asymmetry is deliberate: a Node cannot be
# established without evidence about the Node itself, but a Flow whose
# downstream is not instrumented and an Outcome that is not yet measurable
# are real, common, and #399-sanctioned states -- provided they are DECLARED
# rather than silently missing.
REQUIRED_EVIDENCE_LEVELS: Tuple[str, ...] = ("node",)

ParentReviewDisposition = Literal["endorsed", "declined"]
PARENT_REVIEW_DISPOSITIONS: Tuple[str, ...] = get_args(ParentReviewDisposition)

GATE_REFUSAL_CODES: Tuple[str, ...] = (
    "ok",
    "package_not_draft_or_review",
    "package_superseded",
    "node_not_validating",
    "candidate_implementation_missing",
    "candidate_version_mismatch",
    "contract_version_moved",
    "node_evidence_missing",
    "foreign_evidence",
    "mock_evidence",
    "evidence_ref_missing",
    "evidence_ref_stale",
    "criterion_not_met",
    "floor_violated",
    "floor_unmeasured",
    "downstream_impact_violated",
    "outcome_unmeasured_unacknowledged",
    "stability_declaration_missing",
    "stability_window_insufficient",
    "applicability_envelope_missing",
    "rollback_target_missing",
    "parent_review_missing",
    "parent_review_declined",
)

# Refusals that belong to the APPROVAL, not to the gate. The gate is a pure
# function of the package's persisted facts and is recomputed on every read,
# where no approver exists yet -- so "the parent reviewer and the approver are
# the same person" cannot be one of its rows without giving a read an
# approver to look at. It is enumerated here so the refusal is still a finite,
# machine-readable code rather than only a sentence (#304: parent approval and
# human approval are separate records, and one person filling both roles is
# the conflation the separation exists to prevent).
APPROVAL_REFUSAL_CODES: Tuple[str, ...] = ("parent_approver_identical",)


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
    # Whether the reference this evidence carries still resolves AND its
    # source run reached a successful (or still-open) state, evaluated at
    # GATE time by `gather_gate_facts`. Evidence with no reference at all is
    # trivially current -- there is nothing behind it to go stale.
    ref_current: bool = True
    # Whether this row cites something that was actually EXECUTED (one of
    # `EXECUTION_REF_KINDS`). Defaults to False so the gate fails closed on a
    # fact nobody supplied: a row read from a database written before this
    # rule existed must refuse, not pass.
    execution_ref_present: bool = False
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
    # Whether the Node's CURRENT contract version is still the one this
    # package was written against. The candidate can match the package while
    # both trail the Node -- a moved contract must change the verdict even
    # though nothing inside the package changed.
    package_matches_node_version: bool
    evidence: Tuple[EvidenceFact, ...]
    applicability_envelope_declared: bool
    rollback_target_present: bool
    is_first_establishment: bool
    outcome_unmeasured_reason: str
    required_case_count: int
    observed_case_count: Optional[int]
    stability_window_seconds: float
    observed_window_seconds: Optional[float]
    # The parent review's recorded disposition, or None when no parent has
    # reviewed the package yet. Kept as its OWN fact rather than folded into
    # the approval: #304's rule is that parent approval and human approval are
    # separate records, and a single "approved" bit cannot say which of the
    # two responsibilities was actually discharged.
    parent_review_disposition: Optional[str] = None


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
    if not facts.package_matches_node_version:
        # The candidate can still match the package -- both were written
        # against the OLD contract -- so the row above cannot catch this.
        return GateDecision(
            False, "contract_version_moved",
            "The Node's contract version moved after this package was "
            "written; its evidence argues for a promise the Node no longer "
            "makes.",
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
    unreferenced = tuple(
        e.name
        for e in facts.evidence
        if e.kind in REFERENCE_REQUIRED_EVIDENCE_KINDS
        and e.verdict in ASSERTING_VERDICTS
        and not e.execution_ref_present
    )
    if unreferenced:
        # A hand-typed `met` is a claim, not a result. `add_evidence` refuses
        # one at write time so the developer learns immediately; this row
        # exists because rows written before that validation -- or written
        # around it -- must not be able to establish a Node either. Nothing
        # that can SATISFY this gate is accepted on its author's word.
        return GateDecision(
            False, "evidence_ref_missing",
            "An asserted result cites no executed run. Evidence that can "
            "satisfy this gate must reference an exploration run, exploration "
            "variant, replay run, or experiment in this System.",
            unreferenced,
        )
    stale_refs = tuple(e.name for e in facts.evidence if not e.ref_current)
    if stale_refs:
        # Evidence is referenced, never copied, precisely so THIS check can
        # exist: a result whose source was deleted or ended without
        # completing is not current, however it read when it was attached.
        return GateDecision(
            False, "evidence_ref_stale",
            "A referenced result no longer resolves, or its source run ended "
            "without completing; the evidence is no longer current.",
            stale_refs,
        )

    # --- required evidence ------------------------------------------------
    #
    # Presence is not enough: the required level must carry at least one
    # ASSERTED result (a criterion met, a floor held, a stability result).
    # A level satisfied by a single `not_applicable` outcome row would let a
    # package clear this row with a sentence that demonstrates nothing -- and
    # since only asserting rows of these kinds require a reference, that is
    # also the only reading under which "nothing that can satisfy the gate is
    # self-reported" stays true.
    for level in REQUIRED_EVIDENCE_LEVELS:
        if not any(
            e.level == level
            and e.kind in REFERENCE_REQUIRED_EVIDENCE_KINDS
            and e.verdict in ASSERTING_VERDICTS
            for e in facts.evidence
        ):
            return GateDecision(
                False, "node_evidence_missing",
                f"No asserted {level}-level result: a Node cannot be "
                "established without at least one criterion met, floor held, "
                "or stability result about the Node itself.",
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
    #
    # The DECLARATION itself is mandatory, and that is the point of this row:
    # the columns default to 0, and a 0 requirement is not "no requirement",
    # it is "this package never said how much evidence would be enough". Under
    # the old `> 0` guards those defaults made both checks vanish, so a
    # package that had measured nothing passed the stability section outright.
    # The MAGNITUDE stays the developer's -- #399's non-goal forbids a single
    # fixed threshold across all domains -- but it must be a positive number
    # they chose, declared before the result came back.
    if facts.required_case_count <= 0 or facts.stability_window_seconds <= 0:
        return GateDecision(
            False, "stability_declaration_missing",
            "This package declares no sample coverage or no stability window. "
            "How much evidence is enough is this package's own decision, but "
            "it has to be a positive one and it has to be made in advance.",
        )
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

    # --- the parent review, which is not the human approval ---------------
    #
    # Last, deliberately. The structural rows come first because telling a
    # developer their candidate is gone while the floor reads unmeasured sends
    # them to fix the wrong thing; by exactly the same logic, sending them to
    # ask a parent for a review while the evidence is still broken wastes
    # somebody else's attention on a package that cannot pass anyway.
    if facts.parent_review_disposition is None:
        return GateDecision(
            False, "parent_review_missing",
            "No parent review is recorded. The parent's endorsement and the "
            "approver's decision are separate responsibilities and each has "
            "to be discharged by someone.",
        )
    if facts.parent_review_disposition == "declined":
        return GateDecision(
            False, "parent_review_declined",
            "The parent declined this package. A decline is a recorded "
            "judgement, not an obstacle to route around: argue the case again "
            "in a new package.",
        )

    return GateDecision(
        True, "ok",
        "Every declared criterion and floor is satisfied against a referenced "
        "run, the result is bounded by a declared envelope, the change is "
        "reversible, and a parent has endorsed it. A separate human approval, "
        "by someone other than that parent, is still required.",
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

    An ASSERTING verdict on a kind the gate can be satisfied by must carry an
    execution reference, and it is refused here as well as at gate time. The
    two checks are not redundant: this one exists so the developer learns the
    moment they try to write a claim rather than a result, and the gate's own
    row exists because a row that reached the table some other way -- an older
    schema, a direct write -- must not be able to establish a Node either.
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

    if (
        evidence_kind in REFERENCE_REQUIRED_EVIDENCE_KINDS
        and verdict in ASSERTING_VERDICTS
        and ref_kind not in EXECUTION_REF_KINDS
    ):
        # `evaluation_policy` is a ref_kind but not an execution one: it says
        # what was required, never what was observed.
        raise StabilizationValidationError(
            f"A {verdict!r} {evidence_kind} must reference the run that "
            f"produced it (ref_kind one of {', '.join(EXECUTION_REF_KINDS)}); "
            "an asserted result recorded on its author's word is a claim, not "
            "evidence"
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
        ref_node_id = _evidence_ref_node_id(conn, system_id, ref_kind, ref_id)
        if ref_node_id is not None and ref_node_id != package["node_id"]:
            # System scoping alone is not enough now that a reference is
            # REQUIRED: without this, a developer who has to supply one could
            # satisfy the requirement with any run in the System, and a result
            # about a different Node would be establishing this one.
            raise StabilizationValidationError(
                f"{ref_kind} {ref_id} argues about Node {ref_node_id}, not "
                f"Node {package['node_id']}; a result about a different Node "
                "is not evidence about this one"
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


def record_parent_review(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    package_id: int,
    reviewed_by: str,
    disposition: str,
    note: str = "",
) -> sqlite3.Row:
    """Record the PARENT's review of a package. Not the approval.

    #304 keeps parent approval and human approval as separate records, and
    this is the parent half. The two answer different questions -- "does the
    owner of the surrounding work endorse this argument?" and "does a
    responsible human authorise the establishment?" -- and a single
    `approved_by` column cannot say which was discharged. Both are
    `decision_method: manual`; neither substitutes for the other.

    Append-only, like every other decision this module records. A disposition
    that has been written is not overwritten and not withdrawn: a parent who
    changes their mind supersedes the package (`supersede_package`) exactly as
    any other content change does, so the earlier judgement stays readable
    next to the reasoning that replaced it. Only an undecided package can be
    reviewed -- reviewing a package after it was approved, rejected, or
    superseded would attach a review to a decision that already happened.
    """
    if not (reviewed_by or "").strip():
        raise StabilizationValidationError(
            "record_parent_review requires the reviewing person; a parent "
            "review is a named human's record exactly as an approval is"
        )
    _check_membership(disposition, PARENT_REVIEW_DISPOSITIONS, "disposition")
    package = _require_package(conn, system_id, package_id)
    if package["status"] not in ("draft", "under_review"):
        raise StabilizationConflictError(
            f"Package {package_id} is {package['status']}; a parent review is "
            "not recorded against a decided package"
        )
    if package["parent_review_disposition"] is not None:
        raise StabilizationConflictError(
            f"Package {package_id} already carries a parent review "
            f"({package['parent_review_disposition']}) by "
            f"{package['parent_reviewed_by']!r}; a recorded review is not "
            "overwritten -- supersede the package with a newer one instead"
        )
    conn.execute(
        """UPDATE stabilization_package
               SET parent_reviewed_by = ?, parent_reviewed_at = ?,
                   parent_review_disposition = ?, parent_review_note = ?
             WHERE id = ?""",
        (reviewed_by, time.time(), disposition, note, package_id),
    )
    return conn.execute(
        "SELECT * FROM stabilization_package WHERE id = ?", (package_id,)
    ).fetchone()


# Where each `ref_kind` resolves, and the terminal states in which the
# source itself concluded it produced no usable result. `open`/`running` are
# NOT dead: a still-executing source is current, just unfinished. An
# `exploration_variant` carries no status of its own, so its currency is its
# parent run's.
_EVIDENCE_REF_TABLES: Dict[str, str] = {
    "exploration_run": "exploration_run",
    "exploration_variant": "exploration_variant",
    "replay_run": "replay_runs",
    "experiment": "experiments",
    "evaluation_policy": "evolution_evaluation_policy",
}
_EVIDENCE_REF_DEAD_STATUSES: Dict[str, Tuple[str, ...]] = {
    "exploration_run": ("abandoned",),
    "replay_run": ("failed",),
    "experiment": ("failed",),
}


# How to read the Node a reference argues about, for the reference kinds that
# have one. `replay_runs` and `experiments` are COMPONENT-scoped and carry no
# Node at all, and an `evaluation_policy` names a level rather than a Node --
# there is nothing to compare, so those kinds are bound by System only. That
# is a real limit and is stated rather than papered over with a guessed
# component-to-Node mapping (`evolution_node_link` is optional and many-to-one,
# so inferring one would invent a fact).
_EVIDENCE_REF_NODE_SQL: Dict[str, str] = {
    "exploration_run": (
        "SELECT node_id FROM exploration_run WHERE id = ? AND system_id = ?"
    ),
    "exploration_variant": (
        "SELECT r.node_id AS node_id FROM exploration_variant v "
        "JOIN exploration_run r ON r.id = v.run_id "
        "WHERE v.id = ? AND v.system_id = ?"
    ),
}


def _evidence_ref_node_id(
    conn: sqlite3.Connection, system_id: int, ref_kind: str, ref_id: Optional[int]
) -> Optional[int]:
    """The Node a reference argues about, or None when it names no Node.

    None covers two different situations on purpose, and both are handled the
    same way HERE while being distinguished elsewhere: a reference kind that
    is not Node-scoped (nothing to compare) and a reference that no longer
    resolves (`_evidence_ref_current` is the check that catches that, and it
    reports `evidence_ref_stale` rather than `foreign_evidence` -- a deleted
    run is not somebody else's run).
    """
    sql = _EVIDENCE_REF_NODE_SQL.get(ref_kind)
    if sql is None or ref_id is None:
        return None
    try:
        row = conn.execute(sql, (ref_id, system_id)).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else row["node_id"]


def _evidence_ref_node_matches(
    conn: sqlite3.Connection,
    system_id: int,
    ref_kind: Optional[str],
    ref_id: Optional[int],
    node_id: int,
) -> bool:
    if ref_kind is None:
        return True
    other = _evidence_ref_node_id(conn, system_id, ref_kind, ref_id)
    return other is None or other == node_id


def _evidence_ref_current(
    conn: sqlite3.Connection, system_id: int, ref_kind: str, ref_id: int
) -> bool:
    """Whether an evidence reference still resolves in this System, and its
    source run is not in a non-successful terminal state.

    Queried defensively the way `load_node_facts` treats Phase 4's own
    tables: a table that does not exist means the reference does not resolve,
    never a crash -- and an unresolvable reference is stale, because the gate
    fails closed on absence.
    """
    if ref_kind not in _EVIDENCE_REF_TABLES:
        return False
    try:
        if ref_kind == "exploration_variant":
            row = conn.execute(
                """SELECT r.status AS status FROM exploration_variant v
                       JOIN exploration_run r ON r.id = v.run_id
                       WHERE v.id = ? AND v.system_id = ?""",
                (ref_id, system_id),
            ).fetchone()
            if row is None:
                return False
            return row["status"] not in _EVIDENCE_REF_DEAD_STATUSES["exploration_run"]
        row = conn.execute(
            f"SELECT * FROM {_EVIDENCE_REF_TABLES[ref_kind]} "
            "WHERE id = ? AND system_id = ?",
            (ref_id, system_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    if row is None:
        return False
    dead = _EVIDENCE_REF_DEAD_STATUSES.get(ref_kind)
    if dead and row["status"] in dead:
        return False
    return True


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
            # Two independent ways a row can be somebody else's, folded into
            # the one code whose message already names all three: the evidence
            # row itself belonging to another System, and its reference
            # arguing about another Node. The second is re-checked here rather
            # than trusted from `add_evidence` for the same reason as the ref
            # currency below -- a run can be re-pointed or the row can have
            # reached the table some other way, and a result about a different
            # Node must never establish this one.
            belongs_to_package=(
                row["system_id"] == system_id
                and _evidence_ref_node_matches(
                    conn, system_id, row["ref_kind"], row["ref_id"],
                    package["node_id"],
                )
            ),
            # Re-resolved on EVERY evaluation, per the currency promise in
            # this function's docstring: `ref_id` carries no FK, so the row
            # it named can be gone or abandoned by now.
            ref_current=(
                row["ref_kind"] is None
                or _evidence_ref_current(
                    conn, system_id, row["ref_kind"], row["ref_id"]
                )
            ),
            # Structural, not a judgement: does this row cite something that
            # was executed at all? A row written before `add_evidence`
            # required one reads False here and the gate refuses it, which is
            # the whole point of checking at gate time as well.
            execution_ref_present=(
                row["ref_kind"] in EXECUTION_REF_KINDS and row["ref_id"] is not None
            ),
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
        package_matches_node_version=(
            node["current_version_id"] == package["node_version_id"]
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
        parent_review_disposition=package["parent_review_disposition"],
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
    3. Phase 1's transition evaluator is consulted BEFORE anything is
       written, against the facts as they will stand after the pin
       (`evaluate_transition` is pure, so the one fact the pin changes can be
       substituted without writing it). The pin and the transition commit in
       separate transactions Phase 1 owns, so a refusal discovered only
       after the pin would leave a committed half-state -- pinned but not
       established -- which ADR-4 forbids.
    4. The candidate is pinned stable, which rotates the previous stable into
       the rollback slot.
    5. The `validating -> established` transition goes through Phase 1's own
       evaluator with `decision_method: manual`. It is not written directly:
       Phase 1 owns the transition table and the event log, and a second
       writer would be a second opinion about the Node's state. It
       re-evaluates once more as a defense against a race with step 3; if it
       still refuses, the pin, rollback slot, and pin events are restored in
       this same request so no half-state survives.

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

    # The gate already required an `endorsed` parent review; what it cannot
    # check is WHO gave it, because a gate recomputed on every read has no
    # approver to compare against. One person discharging both roles is not a
    # shortcut through a formality -- it is the conflation #304 separates the
    # two records to prevent, and it is refused here with its own finite code.
    parent = (package["parent_reviewed_by"] or "").strip()
    if parent and parent.casefold() == approved_by.strip().casefold():
        raise StabilizationConflictError(
            "The establishment approval is refused (parent_approver_identical): "
            f"{parent!r} recorded the parent review and cannot also be the "
            "approver. The parent's endorsement and the human approval are "
            "separate responsibilities, and one person holding both is not a "
            "review."
        )

    node_id = package["node_id"]
    reason_text = note or f"approved stabilization package {package_id}"
    evidence_ref = f"stabilization_package:{package_id}"
    request = evolution_node.TransitionRequest(
        to_state="established",
        decision_method="manual",
        actor=approved_by,
        actor_kind="developer",
        reason=reason_text,
        reason_code="stabilization_approved",
        evidence_refs=(evidence_ref,),
    )
    # Pre-validate against the hypothetical post-pin facts, writing nothing.
    # A refusal here (e.g. `stale_implementation` after the contract moved)
    # must leave the pin, the rollback slot, and the event log untouched.
    pre_facts = replace(
        evolution_node.load_node_facts(conn, system_id=system_id, node_id=node_id),
        has_stable_implementation=True,
    )
    pre_decision = evolution_node.evaluate_transition(pre_facts, request)
    if not pre_decision.allowed:
        raise StabilizationConflictError(
            "The Node's own transition evaluator refuses the establishment "
            f"({pre_decision.reason_code}): {pre_decision.message}"
        )

    now = time.time()
    node_before = conn.execute(
        """SELECT stable_implementation_id, rollback_implementation_id, updated_at
               FROM evolution_node WHERE id = ?""",
        (node_id,),
    ).fetchone()
    last_event_id = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM evolution_node_event "
        "WHERE node_id = ?",
        (node_id,),
    ).fetchone()["max_id"]

    evolution_node.pin_stable_implementation(
        conn,
        system_id=system_id,
        node_id=node_id,
        implementation_id=package["candidate_implementation_id"],
        actor=approved_by,
        decision_method="manual",
        reason=f"stabilization package {package_id}",
    )
    transition = evolution_node.apply_transition(
        conn,
        system_id=system_id,
        node_id=node_id,
        to_state="established",
        decision_method="manual",
        actor=approved_by,
        actor_kind="developer",
        reason=reason_text,
        reason_code="stabilization_approved",
        evidence_refs=[evidence_ref],
        idempotency_key=f"stabilization-{package_id}",
    )
    if not (transition.applied or transition.duplicate):
        # Only a race with the pre-validation above can reach this branch.
        # The pin already committed in Phase 1's own transaction, so restore
        # the Node's pin state and remove the pin events this request wrote
        # -- a stable pin the Node never established from is a half-state the
        # event log must not claim (ADR-4).
        conn.execute("BEGIN")
        try:
            conn.execute(
                """UPDATE evolution_node
                       SET stable_implementation_id = ?,
                           rollback_implementation_id = ?, updated_at = ?
                     WHERE id = ?""",
                (
                    node_before["stable_implementation_id"],
                    node_before["rollback_implementation_id"],
                    node_before["updated_at"],
                    node_id,
                ),
            )
            conn.execute(
                """DELETE FROM evolution_node_event
                       WHERE node_id = ? AND id > ?
                         AND event_kind IN ('stable_pinned', 'rollback_pinned')""",
                (node_id, last_event_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
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
    if not (rejected_by or "").strip():
        raise StabilizationValidationError(
            "reject_package requires the rejecting person; a rejection is a "
            "named human's decision exactly as an approval is"
        )
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


def supersede_package(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    package_id: int,
    successor_package_id: int,
    superseded_by: str,
    note: str = "",
) -> sqlite3.Row:
    """Retire an undecided package in favour of a newer one for the same Node.

    Supersession is an explicit, named-human decision -- never an automatic
    side effect of creating or approving another package. An automatic sweep
    would decide, silently, that a newer package's argument replaces an older
    one's, and those can argue for different candidates; whether the older
    argument is dead is a judgement, so it is recorded like one
    (`decision_method: manual`, the module's append-only discipline: the row
    stays readable, its evidence stays attached, and `superseded_by_id` says
    exactly which package to establish from instead).

    Only an undecided (`draft`/`under_review`) package can be superseded: an
    approved or rejected package is a decision that already happened, and
    rewriting its status would rewrite history. The successor must argue
    about the SAME Node and must itself still be alive (undecided or
    approved) -- pointing "establish from that" at a rejected or superseded
    package would send the developer to a dead end. Changes no Node state.
    """
    if not (superseded_by or "").strip():
        raise StabilizationValidationError(
            "supersede_package requires the deciding person; retiring a "
            "package in favour of another is never an anonymous decision"
        )
    package = _require_package(conn, system_id, package_id)
    if package_id == successor_package_id:
        raise StabilizationValidationError(
            "a package cannot supersede itself"
        )
    successor = _require_package(conn, system_id, successor_package_id)
    if package["status"] not in ("draft", "under_review"):
        raise StabilizationConflictError(
            f"Package {package_id} is already {package['status']}; only an "
            "undecided package can be superseded"
        )
    if successor["node_id"] != package["node_id"]:
        raise StabilizationValidationError(
            f"Package {successor_package_id} argues about a different Node; "
            "a package is only superseded by a newer package for the same Node"
        )
    if successor["status"] not in ("draft", "under_review", "approved"):
        raise StabilizationConflictError(
            f"Package {successor_package_id} is {successor['status']} and "
            "cannot be the package to establish from"
        )
    conn.execute(
        """UPDATE stabilization_package
               SET status = 'superseded', superseded_by_id = ?, approved_by = ?,
                   approved_at = ?, decision_note = ?
               WHERE id = ?""",
        (successor_package_id, superseded_by, time.time(), note, package_id),
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
        "superseded_by_id": package["superseded_by_id"],
        # Two decisions, shown as two records with their own who/when. Merging
        # them into one "approved" line would erase which responsibility was
        # actually discharged, which is the whole point of keeping them apart
        # (#304). A NULL disposition means "no parent has reviewed this yet",
        # never "the parent had nothing to say".
        "parent_reviewed_by": package["parent_reviewed_by"],
        "parent_reviewed_at": package["parent_reviewed_at"],
        "parent_review_disposition": package["parent_review_disposition"],
        "parent_review_note": package["parent_review_note"] or "",
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
