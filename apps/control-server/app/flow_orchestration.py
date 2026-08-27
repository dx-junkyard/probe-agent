"""Flow experiment orchestration: proposal, human approval, execution
references and promotion candidates (Epic #412, Issue #415).

Canonical contract: `docs/execution-modes.md` §7 (plus §1.3, §8.4, §9.3 and
§10). Read §0 before touching this area.

This module is the control plane's **planning ledger**, not an executor. Five
properties are the whole of its design, and none of them may be relaxed:

1. **The completeness gate fails closed at the entrance** (§7.1). A proposal
   that does not carry all twelve required elements, and does not pass the
   seven structural checks, cannot become a row at all -- each refusal is its
   own finite code so the developer reads WHICH element is missing rather
   than "invalid proposal". The table DDL in `app/db.py` repeats the same
   requirement as NOT NULL columns; that is the second line of defence, and
   this gate is the first.

2. **`status` is DERIVED by folding `flow_experiment_event`, never stored**
   (§7.4). There is deliberately no `status` column on
   `flow_experiment_proposal`. A stored lifecycle value drifts from the rows
   it describes; a derived one cannot -- the same discipline
   `ux_design.derive_design_status` (#405), `joint_lineage` (#338),
   `interview_workflow` (#349) and `evolution_node.fold_events` (#394 ADR-4)
   all apply.

3. **Approval and execution mode are two independent facts** (§7.5), and an
   execution record requires BOTH. An approved proposal whose Node has been
   moved back to `propose` cannot be executed (409 from the capability gate);
   a Node in `shadow` whose proposal nobody approved cannot be executed
   either (409 `not_approved`). Neither fact is derivable from the other, and
   a caller may never supply either from a request body.

4. **Nothing here changes production** (§7.6). This module writes to exactly
   four tables -- `flow_experiment_proposal` / `_target` / `_event` /
   `_execution_ref` -- and to `intelligence_runs` for its own reasoning
   audit. It never touches `evolution_node`, `components`,
   `cell_improvements`, any patch/worktree/publish table, or the target
   repository. An execution is a REFERENCE into a canonical row that already
   exists elsewhere (`replay_variants`, `experiments`, `shadow_results`),
   resolved again at read time -- a stored row id is never trusted on its own
   (#405). `promotion_candidate_recorded` records a CANDIDATE; promotion
   itself still goes through the existing Experiment adoption / Stabilization
   / publish human gates.

5. **A reasoning model may draft, never decide** (§7.7).
   `propose_flow_experiment` reaches an LLM only through
   `build_experiment_llm_adapter`, so it is structurally unreachable outside
   `propose` / `shadow`. Structured-output validation failure fails the run
   and persists a failed `intelligence_runs` row; there is no heuristic
   fallback (Principle 6). The draft creates NO proposal and NO event -- a
   human writes the `proposed` event (Principle 7: an LLM recommendation
   never enters an approval queue by itself).

6. **Every reference in the ledger is BOUND to a fact, not merely non-empty.**
   Three bindings, each fail-closed with its own finite code:

   * an `evidence_ref` must be an id #414's projection actually produced for
     this System + Flow (`load_flow_grounding`). This is checked when the
     model drafts AND again when a human submits, because the human edits the
     draft in between and a ref that was valid then can by now be stale,
     cross-System or unrelated. Before this, both checks were "is it a
     non-empty string?", so a wholly fabricated citation could be stored in a
     canonical row -- the exact thing §7.7 and Principle 6 forbid;
   * a RESULT must name one execution registered on THIS proposal, resolved
     again at read time, and must carry the measurements the proposal's own
     evaluation contract declared. The declared quality floor is evaluated and
     RECORDED -- a verdict is an observation, never an adoption (ADR-9);
   * a PROMOTION CANDIDATE must bind to all three of a declared candidate, a
     resolvable canonical execution, and a result recorded for that execution.

   The same discipline covers `intelligence_run_id`: it must be this feature's
   own drafting run, on this contract version, that completed, and it may back
   only one proposal. What is NOT yet checkable is that the run drafted THIS
   Flow -- nothing persists the drafting subject. Adding a subject column (or
   an audit row) to `intelligence_runs` would close that last gap.

Connection discipline (CLAUDE.md Implementation Constraints): every function
here takes an already-open `conn`, EXCEPT `propose_flow_experiment`, which
takes none at all and owns its own connections -- it performs an LLM round
trip, and `db.get_conn()`'s lock is process-wide and non-reentrant, so
holding one across that call deadlocks the whole server. Its shape is
read -> close -> reason -> reopen -> persist.

probe-agent:
  role: Flow experiment proposal completeness gate, event-folded lifecycle, human approval records and execution references
  capability: execution-mode-control
  element_type: core
  consumers: [control-server-routes]
  operation_kind: mixed
  state_effects: [database-read, database-write]
  probe_value: Verify every completeness and structural refusal code is reachable on its own, that an unapproved proposal and an approved proposal whose mode fell back to `propose` are both refused an execution record, that the derived status never comes from a column, and that creating and approving a proposal changes no production table.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from dataclasses import dataclass, field
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

from . import execution_mode, llm
from .models import (
    FlowComparisonScope,
    FlowEvaluationLevel,
    FlowExperimentActionKind,
    FlowExperimentEventKind,
    FlowExperimentExecutionKind,
    FlowExperimentStatus,
    FlowExperimentTargetRole,
    FlowIsolationStrategy,
    FlowSubjectKind,
)

__all__ = [
    "FLOW_SUBJECT_KINDS",
    "COMPARISON_SCOPES",
    "ISOLATION_STRATEGIES",
    "SIDE_EFFECT_ISOLATION_REQUIRED",
    "EVALUATION_LEVELS",
    "TARGET_ROLES",
    "EVENT_KINDS",
    "PROPOSAL_STATUSES",
    "TERMINAL_STATUSES",
    "EXECUTION_KINDS",
    "ACTION_KINDS",
    "COMPLETENESS_REJECTION_CODES",
    "STRUCTURAL_REJECTION_CODES",
    "PROPOSAL_GATE_CODES",
    "LIFECYCLE_REJECTION_CODES",
    "EXECUTION_BINDING_REJECTION_CODES",
    "EXECUTION_AUTHORIZATION_REJECTION_CODES",
    "RESULT_BINDING_REJECTION_CODES",
    "PROMOTION_BINDING_REJECTION_CODES",
    "PROVENANCE_REJECTION_CODES",
    "QUALITY_FLOOR_KEY_VERDICTS",
    "QUALITY_FLOOR_VERDICTS",
    "FlowGrounding",
    "load_flow_grounding",
    "ACTION_EVENT_KIND",
    "FLOW_EXPERIMENT_PROPOSAL_SCHEMA_VERSION",
    "FLOW_EXPERIMENT_DRAFT_PROMPT_VERSION",
    "FLOW_EXPERIMENT_DRAFT_SCHEMA_VERSION",
    "FlowOrchestrationError",
    "FlowExperimentNotFoundError",
    "FlowExperimentValidationError",
    "FlowExperimentRejected",
    "FlowExperimentLifecycleError",
    "FlowExperimentReasoningError",
    "ProposalContent",
    "TargetFact",
    "ProposalFacts",
    "GateDecision",
    "LifecycleDecision",
    "DraftResult",
    "evaluate_completeness",
    "derive_proposal_status",
    "evaluate_lifecycle",
    "gather_proposal_facts",
    "create_proposal",
    "list_proposals",
    "get_proposal",
    "record_decision",
    "record_execution",
    "require_execution_authorization",
    "record_result",
    "record_promotion_candidate",
    "record_rollback",
    "propose_flow_experiment",
]


# ---------------------------------------------------------------------------
# Finite vocabularies (Principle 6)
# ---------------------------------------------------------------------------
#
# The `Literal` aliases live in `app/models.py` so FastAPI puts a real enum in
# the OpenAPI schema; they are mirrored here with `get_args` so the domain
# layer and the API can never disagree about a vocabulary (the same shape
# `execution_mode.py` and `ux_design.py` use).

FLOW_SUBJECT_KINDS: Tuple[str, ...] = get_args(FlowSubjectKind)
COMPARISON_SCOPES: Tuple[str, ...] = get_args(FlowComparisonScope)
ISOLATION_STRATEGIES: Tuple[str, ...] = get_args(FlowIsolationStrategy)
EVALUATION_LEVELS: Tuple[str, ...] = get_args(FlowEvaluationLevel)
TARGET_ROLES: Tuple[str, ...] = get_args(FlowExperimentTargetRole)
EVENT_KINDS: Tuple[str, ...] = get_args(FlowExperimentEventKind)
PROPOSAL_STATUSES: Tuple[str, ...] = get_args(FlowExperimentStatus)
EXECUTION_KINDS: Tuple[str, ...] = get_args(FlowExperimentExecutionKind)
ACTION_KINDS: Tuple[str, ...] = get_args(FlowExperimentActionKind)

# The side-effect classes for which `none` / `pure` isolation is refused
# (§7.3). This is Principle 4 ("do not shadow payment / email / DB write /
# auth") made STRUCTURAL at the proposal's entrance, instead of a sentence in
# a review checklist.
SIDE_EFFECT_ISOLATION_REQUIRED: Tuple[str, ...] = ("external_write", "irreversible")

# The isolation strategies that do not isolate anything. `none` is explicit,
# and `pure` is an ASSERTION that the target has no side effects -- which is
# exactly the assertion a Node declaring `external_write` contradicts.
NON_ISOLATING_STRATEGIES: Tuple[str, ...] = ("none", "pure")

# A lifecycle status nothing can move away from. Once one of these is folded,
# later events stay in the ledger (they are audit facts and are never
# deleted) but cannot revive the proposal -- a rejected proposal that somehow
# received an execution row must not read as `executing`.
TERMINAL_STATUSES: Tuple[str, ...] = ("rejected", "withdrawn", "expired")

# §7.1's twelve required elements, in the order the table lists them. Each
# gets its OWN code: "the proposal is incomplete" cannot tell a developer
# which element to write, and a single code would carry twelve facts (#366).
COMPLETENESS_REJECTION_CODES: Tuple[str, ...] = (
    "purpose_missing",
    "hypothesis_missing",
    "scope_missing",
    "baseline_missing",
    "candidates_missing",
    "evaluation_axes_missing",
    "quality_floor_missing",
    "isolation_strategy_missing",
    "cost_cap_missing",
    "stop_conditions_missing",
    "rollback_plan_missing",
    "evidence_missing",
)

# §7.1's structural checks. Evaluated after the completeness codes: an
# element that is absent cannot also be structurally wrong, and reporting
# `comparison_scope_mismatch` for a proposal with no targets at all would
# name the wrong problem.
STRUCTURAL_REJECTION_CODES: Tuple[str, ...] = (
    "unknown_flow_subject",
    "unresolved_node",
    "comparison_scope_mismatch",
    #: Membership could not be DETERMINED, which is not the same answer as
    #: "this Node is not a member" (§6.4's five-answer discipline). A
    #: `static_flow` subject needs the pinned snapshot's call graph, and when
    #: that derivation fails the gate refuses rather than admitting the Node:
    #: an unread membership is not evidence of membership (#380).
    "flow_membership_unavailable",
    "node_not_in_flow",
    "isolation_required_for_side_effects",
    "evaluation_contract_missing",
    #: The projection that produces the citable evidence ids could not be
    #: read, so no citation can be verified. Fail-closed (Principle 6): an
    #: unverifiable citation is refused, never accepted "for now".
    "evidence_allowlist_unavailable",
    #: A cited `evidence_ref` is not one of the ids #414's projection actually
    #: produced for THIS System + Flow. Free text that looks like a reference
    #: is not a reference, and storing one would let an LLM sentence enter a
    #: canonical row as a fact (§7.7 / Principle 7).
    "evidence_ref_unknown",
    "duplicate_proposal_key",
)

#: Why a RESULT is refused. A result is the observation of one specific
#: execution, so it must NAME that execution and it must carry the
#: measurements the proposal itself declared -- otherwise the ledger stops
#: being a record of what happened, which is this Epic's whole explainability
#: story (§7.6).
RESULT_BINDING_REJECTION_CODES: Tuple[str, ...] = (
    "execution_ref_missing",
    "execution_ref_not_registered",
    "execution_ref_unresolved",
    "execution_ref_failed",
    "result_metrics_missing",
)

#: Why a PROMOTION CANDIDATE is refused. It must bind to all three facts:
#: a candidate the proposal itself declared, a canonical execution that still
#: resolves, and a result recorded FOR that execution.
PROMOTION_BINDING_REJECTION_CODES: Tuple[str, ...] = (
    "candidate_ref_not_declared",
    "execution_ref_missing",
    "execution_ref_not_registered",
    "execution_ref_unresolved",
    "execution_ref_failed",
    "no_result_for_execution",
)

#: Why an EXECUTION RECORD is refused even though the proposal is approved
#: and the mode permits candidate execution. Registering a reference is the
#: ledger's claim that THIS approval authorised THAT run, and until these
#: checks existed the claim was the caller's word: any canonical execution in
#: the System could be attached to any approved proposal, including one that
#: ran on a completely different Node and one that ran before anybody
#: approved anything. A caller's claim is not evidence of scope (EM-ADR-4),
#: and that holds for the ledger's own bindings too.
#:
#: Each code is separate because the developer's next action differs: link
#: the Node, cite a different execution, or run the experiment the approval
#: actually authorised.
EXECUTION_BINDING_REJECTION_CODES: Tuple[str, ...] = (
    "execution_ref_subject_unreadable",
    "execution_ref_subject_unmapped",
    "execution_ref_subject_mismatch",
    "execution_ref_authorization_missing",
    "execution_ref_authorization_mismatch",
    "execution_ref_candidate_mismatch",
    "execution_ref_precedes_approval",
    "execution_ref_already_bound",
)

#: A governed candidate path may execute only when the request names the
#: approved proposal that authorises exactly this Node/candidate/snapshot.
#: These are execution-time refusals, not post-hoc ledger refusals.
EXECUTION_AUTHORIZATION_REJECTION_CODES: Tuple[str, ...] = (
    "execution_proposal_required",
    "execution_proposal_not_approved",
    "execution_proposal_expired",
    "execution_target_not_authorized",
    "execution_candidate_not_authorized",
    "execution_snapshot_mismatch",
    "execution_snapshot_authorization_required",
    "execution_isolation_stale",
)

#: Why an `intelligence_run_id` may not be used as LLM provenance
#: (Principle 7: an unverified pointer is not provenance). The row must be
#: THIS feature's drafting run, on THIS contract version, that actually
#: completed, and it may back exactly one proposal.
PROVENANCE_REJECTION_CODES: Tuple[str, ...] = (
    "intelligence_run_missing",
    "intelligence_run_not_a_draft",
    "intelligence_run_not_completed",
    "intelligence_run_already_used",
    #: The run's own `flow_experiment_draft` row says which Flow, snapshot and
    #: Nodes it was about. Without those three the run could only be shown to
    #: be A drafting run, not THIS proposal's -- so Flow A's valid draft could
    #: be attached to a hand-written proposal for Flow B (§7.1.3).
    "intelligence_run_subject_unknown",
    "intelligence_run_draft_digest_mismatch",
    "intelligence_run_subject_mismatch",
    "intelligence_run_target_not_drafted",
)

#: Per quality-floor-key verdicts. `unmeasured` (nothing was measured for this
#: floor) and `not_comparable` (a prose floor has no deterministic ordering)
#: are two different answers and NEITHER is a pass -- collapsing either into
#: `within_floor` would report a floor as held that nobody checked.
QUALITY_FLOOR_KEY_VERDICTS: Tuple[str, ...] = (
    "within_floor",
    "below_floor",
    "unmeasured",
    "not_comparable",
)

#: The recorded overall verdict. It is an OBSERVATION written into the ledger
#: and never an action: nothing in this module adopts, rejects, promotes or
#: applies anything on the strength of it (§7.6, ADR-9). A human still decides.
QUALITY_FLOOR_VERDICTS: Tuple[str, ...] = ("within_floor", "below_floor", "unevaluated")

#: The run type / prompt / schema this feature's own drafting call records.
#: A run that is not all three is not this draft's provenance.
DRAFT_RUN_TYPE = "flow_experiment_draft"

# The gate's complete verdict vocabulary: `ok` plus every refusal, the shape
# `stabilization.GATE_REFUSAL_CODES` uses. A verdict is always exactly one of
# these values.
PROPOSAL_GATE_CODES: Tuple[str, ...] = (
    ("ok",) + COMPLETENESS_REJECTION_CODES + STRUCTURAL_REJECTION_CODES
)

# §7.4's transition refusals. `no_execution_recorded` is not in the
# contract's four; it is the symmetric counterpart of `no_result_recorded`
# and covers the case the contract does not name -- a result or a rollback
# asserted for a run that was never recorded. Without it the only available
# answer would have been `not_approved`, which would be false: the proposal
# IS approved, it simply has nothing to report a result about.
LIFECYCLE_REJECTION_CODES: Tuple[str, ...] = (
    "not_awaiting_decision",
    "not_approved",
    "no_result_recorded",
    "no_execution_recorded",
    "proposal_expired",
)

#: Which event each action writes. The action vocabulary is the API's; the
#: event vocabulary is the ledger's. They are kept separate because an action
#: is a request and an event is a recorded fact -- a refused action writes no
#: event at all.
ACTION_EVENT_KIND: Dict[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "withdraw": "withdrawn",
    "record_execution": "execution_recorded",
    "record_result": "result_recorded",
    "record_promotion_candidate": "promotion_candidate_recorded",
    "record_rollback": "rollback_recorded",
}

FLOW_EXPERIMENT_PROPOSAL_SCHEMA_VERSION = "flow-experiment-proposal-v1"
FLOW_EXPERIMENT_DRAFT_PROMPT_VERSION = "flow-experiment-draft-v1"
FLOW_EXPERIMENT_DRAFT_SCHEMA_VERSION = "flow-experiment-draft-v1"

#: The marker the mock provider keys on, and the instruction the real model
#: is given. Kept as a module constant so the prompt, the mock branch in
#: `app/llm.py` and the tests all name the same string.
DRAFT_RESPONSE_MARKER = "FLOW_EXPERIMENT_DRAFT_RESPONSE_JSON"

# Where each execution reference resolves, and the terminal states in which
# the source itself concluded it produced nothing usable. Mirrors
# `stabilization._EVIDENCE_REF_TABLES` deliberately: an execution reference
# and an evidence reference have the same failure modes, so they get the same
# treatment. `shadow_results` carries no status column -- a shadow comparison
# row IS its own result -- so it has no dead-status entry.
_EXECUTION_REF_TABLES: Dict[str, str] = {
    "replay_variant_run": "replay_variants",
    "experiment": "experiments",
    "shadow_result": "shadow_results",
}
_EXECUTION_REF_DEAD_STATUSES: Dict[str, Tuple[str, ...]] = {
    "replay_variant_run": ("failed",),
    "experiment": ("failed",),
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FlowOrchestrationError(ValueError):
    """Base class for every failure this module raises."""


class FlowExperimentNotFoundError(FlowOrchestrationError):
    """A referenced row does not exist, or belongs to another System.

    The two are deliberately indistinguishable so a caller cannot probe
    another System's ids (the rule `execution_mode.ExecutionModeNotFoundError`
    and `ux_design.NotFound` already document)."""


class FlowExperimentValidationError(FlowOrchestrationError):
    """A value outside a finite vocabulary, or a structurally invalid input
    that is not one of §7.1's named refusals (routes map this to 422)."""


class FlowExperimentRejected(FlowOrchestrationError):
    """The §7.1 completeness/structural gate refused (routes map this to 422).

    Carries the finite `code` and, when the refusal is about specific
    elements, the `detail` naming them -- so a developer reads WHICH Node or
    WHICH evaluation level is the problem without re-deriving the gate."""

    def __init__(self, code: str, message: str, detail: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.detail = tuple(detail)


class FlowExperimentLifecycleError(FlowOrchestrationError):
    """A transition that is illegal from the proposal's derived status
    (routes map this to 409), carrying one of `LIFECYCLE_REJECTION_CODES`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class FlowExperimentReasoningError(FlowOrchestrationError):
    """The drafting reasoning call failed, or its structured output did not
    validate. There is no heuristic fallback (Principle 6): the run is
    persisted `failed` and NOTHING else is written."""


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise FlowExperimentValidationError(
            f"{field_name} must be one of {', '.join(vocabulary)}; got {value!r}"
        )


def _text(value: Any) -> str:
    return (value or "").strip() if isinstance(value, str) else ""


def _json_or_default(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _draft_input_digest(
    *,
    flow_subject_kind: str,
    flow_subject_ref: str,
    captured_snapshot_id: Optional[int],
    node_keys: Sequence[str],
    evidence_ids: Sequence[str],
) -> str:
    """Digest the immutable subject/target/evidence envelope shown to a draft."""
    return hashlib.sha256(
        json.dumps(
            {
                "flow_subject_kind": flow_subject_kind,
                "flow_subject_ref": flow_subject_ref,
                "captured_snapshot_id": captured_snapshot_id,
                "node_keys": sorted(node_keys),
                "evidence_ids": sorted(evidence_ids),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Pure data (§7.1 inputs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalContent:
    """Everything the AUTHOR supplies. Immutable once the row exists (§8.4).

    `evaluation_axes` is a list of `{level, name, ...}` objects rather than a
    flat list of names: ADR-7 keeps Node metrics and Flow/Capability metrics
    in separate contracts, and the only way to guarantee one is never derived
    from the other is for each axis to DECLARE which contract it belongs to.
    `quality_floor` stays a separate mapping for the same reason `node_design`
    separates criteria from floors -- "what must be reached" and "what must
    not be broken" are consumed at different moments.
    """

    proposal_key: str
    flow_subject_kind: str
    flow_subject_ref: str
    comparison_scope: str
    title: str
    purpose: str
    hypothesis: str
    baseline_ref: str
    candidate_refs: Tuple[str, ...]
    evaluation_axes: Tuple[Mapping[str, Any], ...]
    quality_floor: Mapping[str, Any]
    isolation_strategy: str
    isolation_detail: str
    cost_cap: Mapping[str, Any]
    stop_conditions: Tuple[str, ...]
    rollback_plan: str
    evidence_refs: Tuple[str, ...]
    captured_snapshot_id: Optional[int] = None
    expires_at: Optional[float] = None


@dataclass(frozen=True)
class FlowGrounding:
    """Everything this module may treat as a FACT about one Flow.

    It is #414's read-only projection (`flow_explanation.build_flow_explanation`)
    read once, never a second aggregation: the projection already computes
    open items, the five missing states, mode divergence, anomalies, the
    baseline, the per-Node axes and each Node's membership, and every evidence
    item it emits carries a referencable id (§6.3 / §6.5). Re-deriving any of
    that here would create a second opinion that can disagree with the screen.

    Two of its uses are fail-closed gates and therefore care about `state`:

    * `evidence_ids` is the ALLOWLIST a proposal's `evidence_refs` are checked
      against. A citation that is not one of these ids is free text shaped
      like a reference, and storing it would let an LLM sentence become a
      canonical fact (§7.7).
    * `member_node_keys` is the `static_flow` membership the gate needs, which
      persisted rows alone cannot answer (§6.2).

    `state` is `resolved` only when the projection produced BOTH readings
    without degrading a section that feeds them. `unavailable` is not an empty
    allowlist: "there is no evidence" and "we could not read what evidence
    exists" are two different answers (§6.4) and only the first is a fact.
    """

    subject_kind: str
    subject_ref: str
    state: str
    detail: str = ""
    evidence_ids: FrozenSet[str] = frozenset()
    evidence_catalog: Tuple[Mapping[str, Any], ...] = ()
    membership_state: str = "unavailable"
    member_node_keys: FrozenSet[str] = frozenset()
    #: The projection rendered as the grounded fact block a draft is given.
    #: Built from the same objects the two gates above read, so the model can
    #: never be shown a fact the gate would then refuse.
    context: str = ""


@dataclass(frozen=True)
class TargetFact:
    """One target Node as the gate sees it.

    `in_flow` is a THREE-valued reading, not a boolean: `None` means
    membership could not be DETERMINED. For a `static_flow` subject that
    happens when #414's pinned-snapshot call graph could not be built, and the
    gate refuses (`flow_membership_unavailable`) rather than admitting the
    Node -- an unread membership is not evidence of membership (#380). It is a
    separate code from `node_not_in_flow` because the developer's next action
    differs: re-run the snapshot analysis, versus link the Node to the Flow.

    `side_effect_class` is `None` when the Node has no current
    `evolution_node_version`. The gate treats that as "isolation is
    required", because an unread side-effect class is not evidence of
    purity (#380).
    """

    node_key: str
    role: str
    position: int
    resolved: bool
    side_effect_class: Optional[str]
    in_flow: Optional[bool]


@dataclass(frozen=True)
class ProposalFacts:
    """Every persisted fact `evaluate_completeness` reads. Nothing else may
    reach the gate, so the gate stays a pure function of this value.

    `evidence_allowlist_state` defaults to `unavailable` DELIBERATELY: a value
    object constructed without the projection's reading must refuse, not
    admit. A permissive default is the fail-open shape this defect had.
    """

    content: ProposalContent
    targets: Tuple[TargetFact, ...]
    flow_subject_known: bool
    proposal_key_taken: bool
    evidence_allowlist: FrozenSet[str] = frozenset()
    evidence_allowlist_state: str = "unavailable"


@dataclass(frozen=True)
class GateDecision:
    """`code == "ok"` iff `ok`. One of `PROPOSAL_GATE_CODES`, always."""

    ok: bool
    code: str
    message: str
    detail: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecycleDecision:
    """`code == "ok"` iff `allowed`. Otherwise one of
    `LIFECYCLE_REJECTION_CODES`."""

    allowed: bool
    code: str
    message: str


@dataclass(frozen=True)
class DraftResult:
    """A reasoning-model draft (§7.7). It is NOT a proposal: nothing has been
    persisted except the `intelligence_runs` audit row, and no `proposed`
    event exists until a human posts one."""

    draft: Mapping[str, Any]
    intelligence_run_id: int
    is_mock: bool
    decision: execution_mode.ExecutionModeDecision
    provider: str
    model: str
    prompt_version: str = FLOW_EXPERIMENT_DRAFT_PROMPT_VERSION
    schema_version: str = FLOW_EXPERIMENT_DRAFT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# §7.1 The completeness gate (pure)
# ---------------------------------------------------------------------------


def _axes_by_level(
    axes: Sequence[Mapping[str, Any]]
) -> Dict[str, List[Mapping[str, Any]]]:
    """Group evaluation axes by their DECLARED level.

    Grouping, never derivation: an axis belongs to the contract it says it
    belongs to. ADR-7 forbids computing a Flow/Capability reading out of Node
    readings, and the only structural guarantee against that is to have no
    code anywhere that maps one level onto another.
    """
    grouped: Dict[str, List[Mapping[str, Any]]] = {name: [] for name in EVALUATION_LEVELS}
    for axis in axes:
        if isinstance(axis, Mapping):
            level = _text(axis.get("level"))
            if level in grouped:
                grouped[level].append(dict(axis))
    return grouped


def _cost_cap_is_bounded(cost_cap: Mapping[str, Any]) -> bool:
    """A cost cap must actually BOUND something.

    `{"note": "keep it cheap"}` is a wish, not a cap, and a proposal whose
    only limit is prose can consume an unbounded amount of the reasoning
    budget it claims to cap. At least one numeric limit must be present and
    every numeric limit must be positive -- a `0`/negative bound would make
    the experiment structurally unrunnable while reading as configured.
    """
    numeric = [
        value
        for value in cost_cap.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return bool(numeric) and all(value > 0 for value in numeric)


def evaluate_completeness(facts: ProposalFacts) -> GateDecision:
    """§7.1's gate: twelve completeness rows, then seven structural rows.

    Pure and first-match. The ORDER is part of the contract: a missing
    element is reported as missing before anything is said about its shape,
    because a developer who has not written the isolation strategy is not
    helped by being told the Node's side-effect class disagrees with it.
    """
    content = facts.content

    # --- §7.1 completeness (the twelve required elements) -----------------
    if not _text(content.purpose):
        return GateDecision(False, "purpose_missing", "目的 (purpose) が未記入です。")
    if not _text(content.hypothesis):
        return GateDecision(False, "hypothesis_missing", "仮説 (hypothesis) が未記入です。")
    if not facts.targets:
        return GateDecision(
            False, "scope_missing", "対象範囲が空です。対象 Node を 1 件以上指定してください。"
        )
    if not _text(content.baseline_ref):
        return GateDecision(False, "baseline_missing", "baseline 参照が未指定です。")
    if not [ref for ref in content.candidate_refs if _text(ref)]:
        return GateDecision(False, "candidates_missing", "候補が 1 件も指定されていません。")
    if not content.evaluation_axes:
        return GateDecision(
            False, "evaluation_axes_missing", "評価軸が 1 件も指定されていません。"
        )
    if not content.quality_floor:
        return GateDecision(
            False, "quality_floor_missing", "quality floor が未指定です。"
        )
    if content.isolation_strategy not in ISOLATION_STRATEGIES:
        return GateDecision(
            False,
            "isolation_strategy_missing",
            "副作用隔離戦略が未指定、または既知の値ではありません: "
            f"{', '.join(ISOLATION_STRATEGIES)}",
        )
    if not content.cost_cap or not _cost_cap_is_bounded(content.cost_cap):
        return GateDecision(
            False,
            "cost_cap_missing",
            "コスト上限が未指定です。1 つ以上の正の数値上限が必要です。",
        )
    if not [item for item in content.stop_conditions if _text(item)]:
        return GateDecision(
            False, "stop_conditions_missing", "停止条件が 1 件も指定されていません。"
        )
    if not _text(content.rollback_plan):
        return GateDecision(False, "rollback_plan_missing", "rollback 計画が未記入です。")
    if not [ref for ref in content.evidence_refs if _text(ref)]:
        return GateDecision(
            False, "evidence_missing", "根拠 (evidence 参照) が 1 件も指定されていません。"
        )

    # --- §7.1 structural checks -------------------------------------------
    if (
        content.flow_subject_kind not in FLOW_SUBJECT_KINDS
        or not _text(content.flow_subject_ref)
        or not facts.flow_subject_known
    ):
        return GateDecision(
            False,
            "unknown_flow_subject",
            "対象 Flow を解決できません。runtime_flow は観測済み flow_id か "
            "Flow link のある flow_id、static_flow は pin した snapshot の "
            "entrypoint_id である必要があります。",
            (f"{content.flow_subject_kind}:{content.flow_subject_ref}",),
        )

    unresolved = tuple(t.node_key for t in facts.targets if not t.resolved)
    if unresolved:
        return GateDecision(
            False,
            "unresolved_node",
            "この System に存在しない Evolution Node が対象に含まれています。",
            unresolved,
        )

    # `comparison_scope` counts DISTINCT Nodes, not target rows: the same
    # Node may legitimately appear twice (once as `baseline`, once as
    # `candidate_target`), and counting rows would call that a sub-pipeline.
    node_keys = sorted({t.node_key for t in facts.targets})
    if content.comparison_scope not in COMPARISON_SCOPES:
        return GateDecision(
            False,
            "comparison_scope_mismatch",
            f"comparison_scope は {', '.join(COMPARISON_SCOPES)} のいずれかです。",
            (content.comparison_scope,),
        )
    if content.comparison_scope == "single_node" and len(node_keys) != 1:
        return GateDecision(
            False,
            "comparison_scope_mismatch",
            "single_node は対象 Node がちょうど 1 つである必要があります "
            f"(現在 {len(node_keys)} 件)。",
            tuple(node_keys),
        )
    if content.comparison_scope == "sub_pipeline" and len(node_keys) < 2:
        return GateDecision(
            False,
            "comparison_scope_mismatch",
            "sub_pipeline は対象 Node が 2 つ以上である必要があります "
            f"(現在 {len(node_keys)} 件)。",
            tuple(node_keys),
        )

    # `in_flow is None` means membership could not be DETERMINED. It is
    # refused, never skipped: admitting a Node whose membership nobody could
    # read is the fail-open shape this gate exists to remove, and it is its
    # own code because the fix differs from "link the Node to the Flow".
    undetermined = tuple(t.node_key for t in facts.targets if t.in_flow is None)
    if undetermined:
        return GateDecision(
            False,
            "flow_membership_unavailable",
            "対象 Flow への所属を判定できませんでした。static_flow は pin した "
            "snapshot の call graph が必要です (#414 §6.2)。判定できない所属は "
            "所属の証拠ではありません。",
            undetermined,
        )

    outside = tuple(t.node_key for t in facts.targets if t.in_flow is False)
    if outside:
        return GateDecision(
            False,
            "node_not_in_flow",
            "対象 Flow に属していない Node が含まれています。所属は "
            "evolution_node_link(link_kind='flow') が正本です。",
            outside,
        )

    if content.isolation_strategy in NON_ISOLATING_STRATEGIES:
        unsafe = tuple(
            f"{t.node_key}:{t.side_effect_class or 'unknown'}"
            for t in facts.targets
            if t.side_effect_class is None
            or t.side_effect_class in SIDE_EFFECT_ISOLATION_REQUIRED
        )
        if unsafe:
            return GateDecision(
                False,
                "isolation_required_for_side_effects",
                "副作用のある (または副作用クラスが読めない) Node を "
                f"isolation_strategy={content.isolation_strategy!r} で実験対象に "
                "できません。Principle 4。",
                unsafe,
            )

    grouped = _axes_by_level(content.evaluation_axes)
    required_levels = ["node"]
    if content.comparison_scope == "sub_pipeline":
        # ADR-7: a sub-pipeline experiment claims something about the Flow /
        # Capability, and that claim may never be computed from the Node
        # readings. Requiring the contract up front is what makes the
        # separation real instead of aspirational.
        required_levels.append("flow_capability")
    missing_levels = tuple(level for level in required_levels if not grouped[level])
    if missing_levels:
        return GateDecision(
            False,
            "evaluation_contract_missing",
            "評価契約が不足しています。必要な level: "
            f"{', '.join(required_levels)} (ADR-7 により合成しません)。",
            missing_levels,
        )

    # --- evidence must be GROUNDED, not merely non-empty --------------------
    #
    # §7.1's `evidence_missing` above only asks whether the author wrote
    # something. This pair asks whether what they wrote REFERS to anything:
    # the allowlist is exactly the ids #414's projection produced for this
    # System + Flow. It runs at submission as well as at draft time because a
    # human edits the draft in between, so a ref that was valid when the model
    # wrote it can by now be stale, cross-System or simply unrelated.
    if facts.evidence_allowlist_state != "resolved":
        return GateDecision(
            False,
            "evidence_allowlist_unavailable",
            "この Flow の evidence を読み取れなかったため、根拠の実在を検証でき"
            "ません。検証できない根拠は受け付けません (Principle 6)。",
        )
    unknown_evidence = tuple(
        sorted(
            {
                _text(ref)
                for ref in content.evidence_refs
                if _text(ref) and _text(ref) not in facts.evidence_allowlist
            }
        )
    )
    if unknown_evidence:
        return GateDecision(
            False,
            "evidence_ref_unknown",
            "この System / Flow の projection が生成していない evidence 参照が "
            "含まれています。参照の形をした自由文は参照ではありません。",
            unknown_evidence,
        )

    if facts.proposal_key_taken:
        return GateDecision(
            False,
            "duplicate_proposal_key",
            "同じ proposal_key の提案がこの System に既に存在します。",
            (content.proposal_key,),
        )

    return GateDecision(True, "ok", "提案は完全性ゲートを通過しました。")


# ---------------------------------------------------------------------------
# §7.4 Lifecycle: derived status + transition rules (pure)
# ---------------------------------------------------------------------------


def derive_proposal_status(
    events: Sequence[Mapping[str, Any]],
    *,
    now: Optional[float] = None,
    expires_at: Optional[float] = None,
) -> str:
    """Fold the append-only event ledger into one of `PROPOSAL_STATUSES`.

    Both keyword arguments default so the fold can be called with the ledger
    alone. #414's projection imports this function precisely so there is only
    ONE definition of the lifecycle; a signature it could not call would push
    it into writing a second one, which is the drift the fold exists to
    prevent. The defaults are the honest reading of "nothing else was told to
    me": no `expires_at` means nothing expires.

    There is no `status` column and there must never be one (§7.4). Two
    properties of the fold matter:

    * **A terminal verdict stops the fold.** `rejected` / `withdrawn` /
      `expired` are decisions about the proposal as a whole; a later row
      stays in the ledger as an audit fact but cannot revive it.
    * **Time only expires a proposal that is still AWAITING a decision.**
      `expires_at` is the window in which a human may approve (§7.4's
      `proposal_expired`). Once approved, the human decision has happened,
      and letting the clock retroactively unmake it would mean a permission
      that lapsed silently -- the opposite of EM-ADR-2, where the lapse is
      the thing that must be VISIBLE rather than the thing that erases a
      record.
    """
    moment = time.time() if now is None else now
    status = "proposed"
    for event in events:
        # ``sqlite3.Row`` is key-addressable but is not registered as a
        # ``collections.abc.Mapping``.  Lifecycle folding is shared by the
        # DB-backed orchestrator and typed projection records, so accept both
        # shapes explicitly instead of assuming attribute access.
        kind = (
            event["event_kind"]
            if isinstance(event, Mapping) or hasattr(event, "keys")
            else event.event_kind
        )
        if status in TERMINAL_STATUSES:
            break
        if kind == "proposed":
            status = "proposed"
        elif kind == "approved":
            status = "approved"
        elif kind == "rejected":
            status = "rejected"
        elif kind == "withdrawn":
            status = "withdrawn"
        elif kind == "expired":
            status = "expired"
        elif kind == "execution_recorded":
            status = "executing"
        elif kind == "result_recorded":
            status = "completed"
        # `promotion_candidate_recorded` and `rollback_recorded` are
        # deliberately status-neutral: recording a promotion CANDIDATE is not
        # a promotion (§7.6), and a rollback is a fact about an execution,
        # not a new lifecycle position.

    if status == "proposed" and expires_at is not None and moment >= expires_at:
        return "expired"
    return status


def evaluate_lifecycle(
    action: str,
    *,
    status: str,
    has_execution: bool,
    has_result: bool,
) -> LifecycleDecision:
    """§7.4's transition rules. Pure, first-match, finite refusal codes.

    `has_execution` / `has_result` are separate inputs rather than being read
    off `status` because the status is a POSITION and these are HISTORY: a
    `completed` proposal that later records another execution is `executing`
    again, and its result history does not disappear.
    """
    _check_membership(action, ACTION_KINDS, "action")

    if action in ("approve", "reject"):
        if action == "approve" and status == "expired":
            return LifecycleDecision(
                False,
                "proposal_expired",
                "期限切れの提案は承認できません。新しい提案を作成してください。",
            )
        if status != "proposed":
            return LifecycleDecision(
                False,
                "not_awaiting_decision",
                f"承認・却下は proposed の提案にのみ行えます (現在: {status})。",
            )
        return LifecycleDecision(True, "ok", "")

    if action == "withdraw":
        # The contract does not name a rule for withdrawal. It is the
        # proposer's own retraction, so it is allowed while the proposal is
        # still only a plan -- and refused once an execution exists, because
        # withdrawing an experiment that already ran would leave recorded
        # runs attached to a proposal that claims never to have happened.
        if status not in ("proposed", "approved"):
            return LifecycleDecision(
                False,
                "not_awaiting_decision",
                f"撤回は proposed / approved の提案にのみ行えます (現在: {status})。",
            )
        return LifecycleDecision(True, "ok", "")

    if action == "record_execution":
        if status not in ("approved", "executing"):
            return LifecycleDecision(
                False,
                "not_approved",
                "実行記録は承認済み (approved / executing) の提案にのみ行えます "
                f"(現在: {status})。",
            )
        return LifecycleDecision(True, "ok", "")

    if action in ("record_result", "record_rollback"):
        if status in TERMINAL_STATUSES:
            return LifecycleDecision(
                False,
                "not_approved",
                f"終了した提案には記録できません (現在: {status})。",
            )
        if not has_execution:
            return LifecycleDecision(
                False,
                "no_execution_recorded",
                "実行記録が 1 件もありません。結果・rollback は実行を前提とします。",
            )
        return LifecycleDecision(True, "ok", "")

    # record_promotion_candidate
    if not has_result:
        return LifecycleDecision(
            False,
            "no_result_recorded",
            "結果が 1 件も記録されていません。昇格候補は結果を前提とします。",
        )
    if status in TERMINAL_STATUSES:
        return LifecycleDecision(
            False,
            "not_approved",
            f"終了した提案には昇格候補を記録できません (現在: {status})。",
        )
    return LifecycleDecision(True, "ok", "")


# ---------------------------------------------------------------------------
# DB reads (no judgement)
# ---------------------------------------------------------------------------


def _require_system(conn: sqlite3.Connection, system_id: int) -> None:
    row = conn.execute("SELECT id FROM systems WHERE id = ?", (system_id,)).fetchone()
    if row is None:
        raise FlowExperimentNotFoundError(f"System {system_id} not found")


def _node_row(
    conn: sqlite3.Connection, system_id: int, node_key: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM evolution_node WHERE system_id = ? AND node_key = ?",
        (system_id, node_key),
    ).fetchone()


def _node_side_effect_class(
    conn: sqlite3.Connection, system_id: int, node_row: sqlite3.Row
) -> Optional[str]:
    """The CURRENT contract's side-effect class, or None when unreadable.

    Read from `evolution_node_version` through the Node's own
    `current_version_id` -- never from a copied column, so a Node whose
    contract moved is judged against what it promises now.
    """
    version_id = node_row["current_version_id"]
    if version_id is None:
        return None
    row = conn.execute(
        "SELECT side_effect_class FROM evolution_node_version "
        "WHERE id = ? AND system_id = ?",
        (version_id, system_id),
    ).fetchone()
    return None if row is None else row["side_effect_class"]


def _node_in_runtime_flow(
    conn: sqlite3.Connection, system_id: int, node_id: int, flow_id: str
) -> bool:
    """§2.3's membership: a current `evolution_node_link(link_kind='flow')`.

    Both the bare `flow_id` and the `runtime_flow:`-prefixed spelling are
    accepted, because #413 stores mode scope refs prefixed while
    `evolution_node_link.target_ref` (owned by #397) holds the bare id.
    """
    row = conn.execute(
        """SELECT 1 FROM evolution_node_link
            WHERE system_id = ? AND node_id = ? AND link_kind = 'flow'
              AND superseded_by_id IS NULL
              AND target_ref IN (?, ?)
            LIMIT 1""",
        (system_id, node_id, flow_id, execution_mode.FLOW_SCOPE_PREFIX + flow_id),
    ).fetchone()
    return row is not None


def _flow_subject_known(
    conn: sqlite3.Connection,
    system_id: int,
    kind: str,
    ref: str,
    captured_snapshot_id: Optional[int],
) -> bool:
    """Can this Flow subject be resolved from persisted rows alone?

    `runtime_flow`: either something was OBSERVED under this `flow_id`
    (`trace_spans`, the canonical source per §2.1) or a Node declares it
    belongs to it (`evolution_node_link`). Either alone is enough: an
    experiment may legitimately be proposed for a Flow that is modelled but
    not yet observed, and for one that is observed but not yet modelled.

    `static_flow`: the `entrypoint_id` must resolve in the PINNED snapshot
    (§6.2), which is why `captured_snapshot_id` is mandatory for that kind --
    an entrypoint id without the snapshot it was read from names nothing.
    """
    if kind == "runtime_flow":
        row = conn.execute(
            "SELECT 1 FROM trace_spans WHERE system_id = ? AND flow_id = ? LIMIT 1",
            (system_id, ref),
        ).fetchone()
        if row is not None:
            return True
        row = conn.execute(
            """SELECT 1 FROM evolution_node_link
                WHERE system_id = ? AND link_kind = 'flow'
                  AND superseded_by_id IS NULL AND target_ref IN (?, ?)
                LIMIT 1""",
            (system_id, ref, execution_mode.FLOW_SCOPE_PREFIX + ref),
        ).fetchone()
        return row is not None

    if kind == "static_flow":
        if captured_snapshot_id is None:
            return False
        row = conn.execute(
            "SELECT 1 FROM code_entrypoints "
            "WHERE system_id = ? AND snapshot_id = ? AND entrypoint_id = ? LIMIT 1",
            (system_id, captured_snapshot_id, ref),
        ).fetchone()
        return row is not None

    return False


# ---------------------------------------------------------------------------
# #414's projection as this module's only source of Flow facts
# ---------------------------------------------------------------------------

#: The projection sections that FEED the two fail-closed readings below. If
#: any of them degraded, the reading is `unavailable` -- a partial catalogue
#: would refuse a legitimate citation while reporting it as fabricated.
_GROUNDING_SECTIONS: Tuple[str, ...] = ("nodes", "open_items", "experiments", "baseline")


def _grounding_evidence(explanation: Any) -> List[Dict[str, Any]]:
    """Collect every referencable id the projection produced, with no new ids.

    Each entry is read straight off a projection object that already carries a
    canonical identity: a Node's `Evidence`, an `OpenItem` (whose id names the
    unresolved fact itself, which is exactly what §7.1 wants a proposal to
    cite), a baseline's approved `stabilization_package`, and an existing
    proposal's execution reference. Nothing here derives an id from iteration
    order (#380's rule that a finding's id comes from its cause).
    """
    catalog: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(ident: str, kind: str, label: str, node_key: Optional[str]) -> None:
        ident = _text(ident)
        if not ident or ident in seen:
            return
        seen.add(ident)
        catalog.append(
            {"id": ident, "kind": kind, "label": _text(label), "node_key": node_key}
        )

    for entry in explanation.nodes or []:
        for item in entry.evidence:
            _add(item.id, item.kind, item.label, item.node_key or entry.node_key)

    open_items = getattr(explanation, "open_items", None)
    for item in (open_items.items if open_items is not None else []):
        _add(item.id, "open_item", f"{item.kind}: {item.label}", item.node_key)
        for ident in item.evidence_ids:
            _add(ident, "open_item_evidence", item.label, item.node_key)

    baseline = getattr(explanation, "baseline", None)
    for node_baseline in (baseline.nodes if baseline is not None else []):
        approval = node_baseline.approval or {}
        package_id = approval.get("package_id")
        if package_id is not None:
            _add(
                f"stabilization_package:{package_id}",
                "stabilization_package",
                f"{node_baseline.node_key}: 固定化承認",
                node_baseline.node_key,
            )

    experiments = getattr(explanation, "experiments", None)
    for summary in (experiments.proposals if experiments is not None else []):
        for ref in summary.execution_refs:
            _add(
                f"execution_ref:{ref['execution_kind']}:{ref['execution_ref']}",
                "execution_ref",
                f"{summary.proposal_key}",
                None,
            )
    return catalog


def _grounding_context(explanation: Any, catalog: Sequence[Mapping[str, Any]]) -> str:
    """Render the projection as the grounded fact block a draft is given.

    Only lines that begin with `- ` name a target Node; every other bullet
    uses `* `. That is a contract with the mock provider, which echoes the
    facts back out of the prompt rather than inventing them.
    """
    lines: List[str] = []

    lines.append("Flow state:")
    lines.append(
        f"* subject {explanation.subject.subject_kind}:{explanation.subject.subject_ref} "
        f"(resolution={explanation.subject.resolution}, "
        f"snapshot_state={explanation.subject.snapshot_state})"
    )
    lines.append(
        f"* membership={explanation.membership.state} "
        f"nodes={', '.join(explanation.membership.node_keys) or '(none)'}"
    )
    if explanation.degraded_sections:
        lines.append(
            f"* degraded sections (facts NOT read): "
            f"{', '.join(explanation.degraded_sections)}"
        )

    lines.append("")
    lines.append("Per-Node axes (five independent axes; never combined into a score):")
    for entry in explanation.nodes or []:
        lines.append(
            f"* {entry.node_key}: execution_mode={entry.execution_mode}"
            f"(source={entry.mode_source}, reason={entry.mode_reason})"
            f" maturity={entry.maturity}/{entry.maturity_state}"
            f" modality={entry.implementation_modality}/{entry.implementation_modality_state}"
            f" improvement={entry.improvement_status}/{entry.improvement_status_state}"
            f" sdk_policy={entry.sdk_policy_mode}/{entry.sdk_policy_mode_state}"
            f" observation={entry.observation_state}"
            f" divergence={entry.mode_divergence}"
        )

    open_items = getattr(explanation, "open_items", None)
    lines.append("")
    lines.append("Open items (anomalies, missing / stale / unmeasured facts, drift):")
    items = open_items.items if open_items is not None else []
    for item in items:
        lines.append(
            f"* [{item.id}] {item.kind} / {item.node_key or '-'} / "
            f"{item.missing_state or 'present'} / {item.label}"
        )
    if not items:
        lines.append("* (none recorded)")

    baseline = getattr(explanation, "baseline", None)
    lines.append("")
    lines.append("Baseline and rollback targets:")
    baseline_nodes = baseline.nodes if baseline is not None else []
    for node_baseline in baseline_nodes:
        lines.append(
            f"* {node_baseline.node_key}: stable={node_baseline.stable_state}"
            f" rollback={node_baseline.rollback_state}"
            f" approval={node_baseline.approval_state}"
        )
    if not baseline_nodes:
        lines.append("* (none recorded)")

    experiments = getattr(explanation, "experiments", None)
    lines.append("")
    lines.append("Experiments already on this Flow:")
    proposals = experiments.proposals if experiments is not None else []
    for summary in proposals:
        lines.append(
            f"* {summary.proposal_key}: status={summary.status}/{summary.status_state}"
            f" scope={summary.comparison_scope}"
            f" nodes={', '.join(summary.target_node_keys) or '-'}"
        )
    if not proposals:
        lines.append("* (none recorded)")

    lines.append("")
    lines.append(
        "Evidence catalogue. `evidence_refs` MUST be drawn from these ids and "
        "nothing else; any other value fails the run:"
    )
    for item in catalog:
        lines.append(
            f"* [{item['id']}] {item['kind']} / {item['node_key'] or '-'} / {item['label']}"
        )
    if not catalog:
        lines.append("* (no citable evidence exists for this Flow)")
    return "\n".join(lines)


def load_flow_grounding(
    system_id: int,
    *,
    subject_kind: str,
    subject_ref: str,
    snapshot_id: Optional[int] = None,
    now: Optional[float] = None,
) -> FlowGrounding:
    """Read #414's projection once and expose it as this module's facts.

    Connection discipline: `build_flow_explanation` owns its own connections
    (and, for a `static_flow`, builds a call graph with none held), so this
    MUST be called with no `get_conn()` block open -- the lock is
    process-wide and non-reentrant.

    Every failure lands on `state='unavailable'` with the reason in `detail`.
    Nothing is guessed: an unreadable projection produces no allowlist and no
    membership, and both gates that consume it then refuse (Principle 6).
    """
    # The ref is normalized HERE, once, so the reading and the gate that
    # consumes it always name the same subject: a whitespace difference
    # between the two would silently downgrade a legitimate proposal to
    # "we could not read the evidence".
    ref = _text(subject_ref)
    if subject_kind not in FLOW_SUBJECT_KINDS or not ref:
        return FlowGrounding(
            subject_kind=subject_kind,
            subject_ref=ref,
            state="unavailable",
            detail=f"unknown subject {subject_kind!r}:{ref!r}",
        )

    from .flow_explanation import build_flow_explanation

    try:
        explanation = build_flow_explanation(
            system_id,
            subject_kind=subject_kind,
            subject_ref=ref,
            snapshot_id=snapshot_id,
            now=now,
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable projection is a fact
        return FlowGrounding(
            subject_kind=subject_kind,
            subject_ref=ref,
            state="unavailable",
            detail=f"{type(exc).__name__}: {exc}",
        )

    degraded = [s for s in _GROUNDING_SECTIONS if s in explanation.degraded_sections]
    catalog = _grounding_evidence(explanation)
    membership_state = explanation.membership.state
    state = "unavailable" if degraded else "resolved"
    detail = (
        f"degraded sections: {', '.join(degraded)}" if degraded else ""
    )
    return FlowGrounding(
        subject_kind=subject_kind,
        subject_ref=ref,
        state=state,
        detail=detail,
        evidence_ids=frozenset(item["id"] for item in catalog),
        evidence_catalog=tuple(catalog),
        membership_state=membership_state,
        member_node_keys=frozenset(explanation.membership.node_keys),
        context=_grounding_context(explanation, catalog),
    )


def gather_proposal_facts(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    content: ProposalContent,
    targets: Sequence[Mapping[str, Any]],
    grounding: Optional[FlowGrounding] = None,
) -> ProposalFacts:
    """Read every persisted fact the gate needs. Contains NO judgement.

    The split mirrors `execution_mode.load_mode_facts` / `resolve_execution_
    mode` and `stabilization.gather_gate_facts` / `evaluate_establishment_
    gate`: the reader knows how to find a fact, the evaluator knows what it
    means, and only the evaluator is a contract.

    `grounding` carries the two readings a `conn` cannot produce here: #414's
    evidence ids and its pinned-snapshot Flow membership. It is a PARAMETER
    rather than something read inside this function because
    `build_flow_explanation` opens its own connections, and holding this one
    across it would deadlock the server. Omitting it is fail-closed, not
    permissive: the allowlist reads `unavailable` and static membership stays
    undetermined, so the gate refuses.
    """
    _require_system(conn, system_id)

    kind = content.flow_subject_kind
    ref = _text(content.flow_subject_ref)

    facts: List[TargetFact] = []
    for position, target in enumerate(targets):
        node_key = _text(target.get("node_key"))
        role = _text(target.get("target_role")) or _text(target.get("role")) or "candidate_target"
        _check_membership(role, TARGET_ROLES, "target_role")
        if not node_key:
            raise FlowExperimentValidationError("target node_key must not be empty")
        node_row = _node_row(conn, system_id, node_key)
        in_flow: Optional[bool]
        if node_row is None:
            in_flow = None
        elif kind == "runtime_flow":
            in_flow = _node_in_runtime_flow(conn, system_id, node_row["id"], ref)
        elif (
            grounding is not None
            and grounding.subject_kind == kind
            and grounding.subject_ref == ref
            and grounding.membership_state == "resolved"
        ):
            # §6.2's static membership: #414's STRICT `(path, qualified_name)`
            # exact match against the pinned snapshot's call graph, read from
            # its projection rather than re-implemented here. No similarity,
            # no keywords (Principle 6).
            in_flow = node_key in grounding.member_node_keys
        else:
            # The call graph could not be built (or no grounding was supplied),
            # so membership is UNDETERMINED. The gate refuses that with
            # `flow_membership_unavailable`; it is never admitted, because an
            # unread membership is not evidence of membership.
            in_flow = None
        facts.append(
            TargetFact(
                node_key=node_key,
                role=role,
                position=int(target.get("position", position)),
                resolved=node_row is not None,
                side_effect_class=(
                    None
                    if node_row is None
                    else _node_side_effect_class(conn, system_id, node_row)
                ),
                in_flow=in_flow,
            )
        )

    taken = conn.execute(
        "SELECT 1 FROM flow_experiment_proposal WHERE system_id = ? AND proposal_key = ?",
        (system_id, content.proposal_key),
    ).fetchone()

    matches_subject = (
        grounding is not None
        and grounding.subject_kind == kind
        and grounding.subject_ref == ref
        and grounding.state == "resolved"
    )
    return ProposalFacts(
        content=content,
        targets=tuple(facts),
        flow_subject_known=_flow_subject_known(
            conn, system_id, kind, ref, content.captured_snapshot_id
        ),
        proposal_key_taken=taken is not None,
        evidence_allowlist=(
            grounding.evidence_ids if matches_subject and grounding else frozenset()
        ),
        evidence_allowlist_state="resolved" if matches_subject else "unavailable",
    )


def _proposal_row(
    conn: sqlite3.Connection, system_id: int, proposal_id: int
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM flow_experiment_proposal WHERE id = ? AND system_id = ?",
        (proposal_id, system_id),
    ).fetchone()
    if row is None:
        raise FlowExperimentNotFoundError(f"Flow experiment proposal {proposal_id} not found")
    return row


def _event_rows(conn: sqlite3.Connection, proposal_id: int) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM flow_experiment_event WHERE proposal_id = ? ORDER BY id ASC",
            (proposal_id,),
        ).fetchall()
    )


def _target_rows(conn: sqlite3.Connection, proposal_id: int) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM flow_experiment_target WHERE proposal_id = ? "
            "ORDER BY position ASC, id ASC",
            (proposal_id,),
        ).fetchall()
    )


def _execution_rows(conn: sqlite3.Connection, proposal_id: int) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM flow_experiment_execution_ref WHERE proposal_id = ? "
            "ORDER BY id ASC",
            (proposal_id,),
        ).fetchall()
    )


def _execution_ref_resolution(
    conn: sqlite3.Connection, system_id: int, execution_kind: str, execution_ref: str
) -> str:
    """Resolve one execution reference AT READ TIME (§7.6 / #405).

    Three finite answers, never merged: `resolved` (the canonical row exists
    and its source did not end in a non-successful terminal state),
    `unresolved` (the row is gone, belongs to another System, or the
    reference is not an id at all) and `stale` (it resolves, but the run
    itself concluded it produced nothing usable). A stored id is never
    trusted on its own, so this runs on every read rather than being cached
    on the row.
    """
    table = _EXECUTION_REF_TABLES.get(execution_kind)
    if table is None:
        return "unresolved"
    try:
        ref_id = int(execution_ref)
    except (TypeError, ValueError):
        return "unresolved"
    try:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE id = ? AND system_id = ?", (ref_id, system_id)
        ).fetchone()
    except sqlite3.OperationalError:
        return "unresolved"
    if row is None:
        return "unresolved"
    dead = _EXECUTION_REF_DEAD_STATUSES.get(execution_kind)
    if dead and row["status"] in dead:
        return "stale"
    return "resolved"


def _execution_ref_row(
    conn: sqlite3.Connection, system_id: int, execution_kind: str, execution_ref: str
) -> Optional[sqlite3.Row]:
    """The canonical row an execution reference points at, or `None`.

    Same System constraint and same "an id that is not an id is unresolved"
    rule as `_execution_ref_resolution`, which is layered on top of this.
    """
    table = _EXECUTION_REF_TABLES.get(execution_kind)
    if table is None:
        return None
    try:
        ref_id = int(execution_ref)
    except (TypeError, ValueError):
        return None
    try:
        return conn.execute(
            f"SELECT * FROM {table} WHERE id = ? AND system_id = ?", (ref_id, system_id)
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _execution_ref_subject(
    conn: sqlite3.Connection, system_id: int, execution_kind: str, row: sqlite3.Row
) -> Optional[Tuple[str, str]]:
    """WHAT this execution ran against, as an `execution_target` pair.

    Read from the canonical row itself, never from the request: the whole
    point is to check the caller's claim against the execution's own subject.
    `replay_variants` names no subject of its own -- the Component belongs to
    the run -- so it is resolved through `replay_runs`.

    `None` means the subject could not be READ, which is a different answer
    from "it is not linked to a Node" (#380) and gets its own refusal code.
    """
    if execution_kind == "experiment":
        return ("feature", str(row["feature_id"] or ""))
    if execution_kind == "shadow_result":
        return ("component", str(row["component_id"] or ""))
    if execution_kind == "replay_variant_run":
        run = conn.execute(
            "SELECT component_id FROM replay_runs WHERE id = ? AND system_id = ?",
            (row["replay_run_id"], system_id),
        ).fetchone()
        if run is None:
            return None
        return ("component", str(run["component_id"] or ""))
    return None


def _execution_ran_at(execution_kind: str, row: sqlite3.Row) -> Optional[float]:
    """When the execution ACTUALLY RAN, not when its record was created.

    An Experiment is drafted long before it is run, so `created_at` would
    refuse a perfectly ordinary "draft it, get it approved, run it" sequence.
    `started_at` is the moment the candidate executed, and `created_at` is
    the fallback only for a kind that has no separate start (and for a row
    written before that column carried a value).
    """
    keys = set(row.keys())
    if execution_kind == "shadow_result":
        return row["timestamp"] if "timestamp" in keys else None
    started = row["started_at"] if "started_at" in keys else None
    if started is not None:
        return started
    return row["created_at"] if "created_at" in keys else None


def _approved_at(conn: sqlite3.Connection, proposal_id: int) -> Optional[float]:
    """When this proposal was approved, from the ledger (§7.4).

    The EARLIEST `approved` event: `approve` is reachable only from
    `proposed`, so there is normally exactly one, and taking the earliest
    means a re-approval could never retroactively widen the window.
    """
    row = conn.execute(
        "SELECT MIN(created_at) AS approved_at FROM flow_experiment_event "
        "WHERE proposal_id = ? AND event_kind = 'approved'",
        (proposal_id,),
    ).fetchone()
    return None if row is None else row["approved_at"]


def _require_execution_binding(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    execution_kind: str,
    execution_ref: str,
    row: sqlite3.Row,
) -> None:
    """§7.5: the cited execution must be one THIS approval authorised.

    Two independent bindings, both deterministic and both read from canonical
    rows rather than from the request:

    * **Subject.** The execution's own target (`experiments.feature_id`,
      `replay_runs.component_id`, `shadow_results.component_id`) is mapped to
      Evolution Nodes by `execution_target.resolve_execution_target` -- the
      same exact-match `evolution_node_link` lookup §4.4 already uses for the
      gate -- and at least one of them must be a target of this proposal.
      Without it, the ledger's "this proposal was executed" could cite a run
      of a Node the proposal never named.

      An `ambiguous` mapping is NOT refused here. Ambiguity is a question
      about whose PERMISSION governs (§4.4.3), and permission was already
      decided by `_require_execution_capabilities` against the proposal's own
      targets; the question here is only whether the link exists, and it
      does.

    * **Order.** The execution must have run at or after the approval.
      Approval is what authorises the run (§7.5), so a run that predates it
      was authorised by nothing, and letting it be attached afterwards is
      exactly the post-hoc binding that makes the ledger unable to say which
      approval covered which execution.

    A third check has no proposal-local reading: one canonical execution may
    back exactly ONE proposal, the same rule `intelligence_run_already_used`
    applies to a draft. An execution IS the running of a proposal's
    experiment, so two proposals claiming the same run means at least one of
    them never ran its own -- and the ledger would report an experiment that
    did not happen.

    Raises `FlowExperimentRejected` with one of
    `EXECUTION_BINDING_REJECTION_CODES`.
    """
    from . import execution_target  # local: keeps the module import-light

    target_keys = {
        str(target["target_node_key"]) for target in _target_rows(conn, proposal_id)
    }

    subject = _execution_ref_subject(conn, system_id, execution_kind, row)
    if subject is None:
        raise FlowExperimentRejected(
            "execution_ref_subject_unreadable",
            f"実行 {execution_kind}:{execution_ref} の対象を読み取れませんでした。"
            "読めなかったことは「対象が一致した」ことではありません。",
        )
    target_kind, target_ref = subject
    mapping = execution_target.resolve_execution_target(
        conn, system_id=system_id, target_kind=target_kind, target_ref=target_ref
    )
    linked = set(mapping.node_keys)
    if not linked:
        raise FlowExperimentRejected(
            "execution_ref_subject_unmapped",
            f"実行 {execution_kind}:{execution_ref} の対象 "
            f"({target_kind}:{target_ref}) はどの Evolution Node にも "
            "link されていないため、この提案の対象 Node での実行であることを"
            "確認できません。",
            (f"{target_kind}:{target_ref}",),
        )
    if not (linked & target_keys):
        raise FlowExperimentRejected(
            "execution_ref_subject_mismatch",
            f"実行 {execution_kind}:{execution_ref} は "
            f"{sorted(linked)} の実行であり、この提案の対象 Node "
            f"{sorted(target_keys)} の実行ではありません。",
            tuple(sorted(linked)),
        )

    bound = conn.execute(
        """SELECT proposal_id FROM flow_experiment_execution_ref
            WHERE system_id = ? AND execution_kind = ? AND execution_ref = ?
              AND proposal_id != ? LIMIT 1""",
        (system_id, execution_kind, execution_ref, proposal_id),
    ).fetchone()
    if bound is not None:
        raise FlowExperimentRejected(
            "execution_ref_already_bound",
            f"実行 {execution_kind}:{execution_ref} は既に提案 "
            f"{bound['proposal_id']} の実行として記録されています。1 つの実行は"
            "1 つの提案の実行です。",
            (str(bound["proposal_id"]),),
        )

    keys = set(row.keys())
    authorized_proposal_id = (
        row["flow_experiment_proposal_id"]
        if "flow_experiment_proposal_id" in keys
        else None
    )
    if authorized_proposal_id is None:
        raise FlowExperimentRejected(
            "execution_ref_authorization_missing",
            f"実行 {execution_kind}:{execution_ref} には実行時 proposal authorization "
            "が記録されていません。事後の付け替えは承認になりません。",
        )
    if int(authorized_proposal_id) != int(proposal_id):
        raise FlowExperimentRejected(
            "execution_ref_authorization_mismatch",
            f"実行 {execution_kind}:{execution_ref} は proposal "
            f"{authorized_proposal_id} により実行時承認されています。",
            (str(authorized_proposal_id),),
        )

    if execution_kind == "experiment":
        actual_candidates = set(
            _json_or_default(row["flow_experiment_candidate_refs_json"], [])
        )
    else:
        candidate_ref = row["flow_experiment_candidate_ref"]
        actual_candidates = {_text(candidate_ref)} if _text(candidate_ref) else set()
    proposal = _proposal_row(conn, system_id, proposal_id)
    declared_candidates = set(
        _json_or_default(proposal["candidate_refs_json"], [])
    )
    if actual_candidates and not actual_candidates.issubset(declared_candidates):
        mismatch = tuple(sorted(actual_candidates - declared_candidates))
        raise FlowExperimentRejected(
            "execution_ref_candidate_mismatch",
            "canonical execution の candidate はこの proposal が承認した候補では"
            "ありません。",
            mismatch,
        )
    captured_snapshot_id = proposal["captured_snapshot_id"]
    if captured_snapshot_id is not None:
        if execution_kind == "experiment":
            execution_snapshot_id = row["snapshot_id"]
        elif execution_kind == "replay_variant_run":
            replay_run = conn.execute(
                "SELECT snapshot_id FROM replay_runs WHERE id = ? AND system_id = ?",
                (row["replay_run_id"], system_id),
            ).fetchone()
            execution_snapshot_id = (
                replay_run["snapshot_id"] if replay_run is not None else None
            )
        else:
            execution_snapshot_id = row["flow_experiment_snapshot_id"]
        if execution_snapshot_id != captured_snapshot_id:
            raise FlowExperimentRejected(
                "execution_ref_authorization_mismatch",
                "canonical execution の snapshot は proposal が承認した snapshot と"
                "一致しません。",
                (str(execution_snapshot_id), str(captured_snapshot_id)),
            )

    approved_at = _approved_at(conn, proposal_id)
    ran_at = _execution_ran_at(execution_kind, row)
    if approved_at is not None and ran_at is not None and ran_at < approved_at:
        raise FlowExperimentRejected(
            "execution_ref_precedes_approval",
            f"実行 {execution_kind}:{execution_ref} は承認より前に実行されて"
            "います。承認は実行を許可する記録なので、承認前の実行をこの提案の"
            "実行として記録することはできません。",
            (f"ran_at={ran_at}", f"approved_at={approved_at}"),
        )


def _event_doc(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "event_kind": row["event_kind"],
        "actor_kind": row["actor_kind"],
        "actor": row["actor"],
        "reason": row["reason"],
        "decision_method": row["decision_method"],
        "payload": _json_or_default(row["payload_json"], {}),
        "created_at": row["created_at"],
    }


def _proposal_doc(
    conn: sqlite3.Connection,
    system_id: int,
    row: sqlite3.Row,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    moment = time.time() if now is None else now
    events = _event_rows(conn, row["id"])
    event_docs = [_event_doc(event) for event in events]
    status = derive_proposal_status(
        event_docs, now=moment, expires_at=row["expires_at"]
    )
    axes = _json_or_default(row["evaluation_axes_json"], [])
    executions = [
        {
            "id": ref["id"],
            "execution_kind": ref["execution_kind"],
            "execution_ref": ref["execution_ref"],
            "note": ref["note"],
            "recorded_at": ref["recorded_at"],
            "resolution": _execution_ref_resolution(
                conn, system_id, ref["execution_kind"], ref["execution_ref"]
            ),
        }
        for ref in _execution_rows(conn, row["id"])
    ]
    return {
        "schema_version": row["schema_version"],
        "id": row["id"],
        "system_id": row["system_id"],
        "proposal_key": row["proposal_key"],
        "flow_subject_kind": row["flow_subject_kind"],
        "flow_subject_ref": row["flow_subject_ref"],
        "captured_snapshot_id": row["captured_snapshot_id"],
        "comparison_scope": row["comparison_scope"],
        "title": row["title"],
        "purpose": row["purpose"],
        "hypothesis": row["hypothesis"],
        "baseline_ref": row["baseline_ref"],
        "candidate_refs": _json_or_default(row["candidate_refs_json"], []),
        "evaluation_axes": axes,
        # Two separate structures, never one derived from the other (ADR-7).
        "evaluation_axes_by_level": _axes_by_level(axes),
        "quality_floor": _json_or_default(row["quality_floor_json"], {}),
        "isolation_strategy": row["isolation_strategy"],
        "isolation_detail": row["isolation_detail"],
        "cost_cap": _json_or_default(row["cost_cap_json"], {}),
        "stop_conditions": _json_or_default(row["stop_conditions_json"], []),
        "rollback_plan": row["rollback_plan"],
        "evidence_refs": _json_or_default(row["evidence_refs_json"], []),
        "expires_at": row["expires_at"],
        "decision_method": row["decision_method"],
        "intelligence_run_id": row["intelligence_run_id"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        # DERIVED on every read. There is no column behind this value.
        "status": status,
        "status_derived_at": moment,
        "targets": [
            {
                "id": target["id"],
                "target_node_key": target["target_node_key"],
                "target_role": target["target_role"],
                "position": target["position"],
                "note": target["note"],
            }
            for target in _target_rows(conn, row["id"])
        ],
        "events": event_docs,
        "executions": executions,
        "promotion_candidates": [
            doc for doc in event_docs
            if doc["event_kind"] == "promotion_candidate_recorded"
        ],
    }


def get_proposal(
    conn: sqlite3.Connection, *, system_id: int, proposal_id: int, now: Optional[float] = None
) -> Dict[str, Any]:
    """One proposal with its derived status, targets, ledger and references."""
    return _proposal_doc(conn, system_id, _proposal_row(conn, system_id, proposal_id), now=now)


def list_proposals(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    status: Optional[str] = None,
    flow_subject_kind: Optional[str] = None,
    flow_subject_ref: Optional[str] = None,
    limit: int = 100,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """The System's proposals, newest first.

    `status` filters on the DERIVED value, computed per row -- there is no
    column to filter on in SQL, and adding one is exactly what §7.4 forbids.

    `flow_subject_kind` is part of the subject's IDENTITY, not decoration
    (§6.2): a runtime `flow_id` and a pinned snapshot's `entrypoint_id` live
    in two identifier spaces that are never merged. Filtering on the ref alone
    mixed a runtime Flow's proposals with a same-named static Flow's --
    #414's own `_build_experiments_section` has always keyed on both.
    """
    if status is not None:
        _check_membership(status, PROPOSAL_STATUSES, "status")
    if flow_subject_kind is not None:
        _check_membership(flow_subject_kind, FLOW_SUBJECT_KINDS, "flow_subject_kind")
    sql = "SELECT * FROM flow_experiment_proposal WHERE system_id = ?"
    params: List[Any] = [system_id]
    if flow_subject_kind:
        sql += " AND flow_subject_kind = ?"
        params.append(flow_subject_kind)
    if flow_subject_ref:
        sql += " AND flow_subject_ref = ?"
        params.append(flow_subject_ref)
    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, params).fetchall()
    docs = [_proposal_doc(conn, system_id, row, now=now) for row in rows]
    if status is not None:
        docs = [doc for doc in docs if doc["status"] == status]
    return docs[: max(1, limit)]


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _insert_event(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    event_kind: str,
    actor: Optional[str],
    actor_kind: str,
    reason: str,
    decision_method: str,
    payload: Optional[Mapping[str, Any]],
    now: float,
) -> sqlite3.Row:
    _check_membership(event_kind, EVENT_KINDS, "event_kind")
    _check_membership(actor_kind, ("user", "system"), "actor_kind")
    _check_membership(
        decision_method, ("manual", "reasoning_llm", "deterministic"), "decision_method"
    )
    cur = conn.execute(
        """INSERT INTO flow_experiment_event
               (system_id, proposal_id, event_kind, actor_kind, actor, reason,
                decision_method, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id,
            proposal_id,
            event_kind,
            actor_kind,
            actor,
            reason,
            decision_method,
            json.dumps(dict(payload or {}), ensure_ascii=False),
            now,
        ),
    )
    return conn.execute(
        "SELECT * FROM flow_experiment_event WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def _validate_llm_provenance(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    decision_method: str,
    intelligence_run_id: Optional[int],
    content: Optional[ProposalContent] = None,
    target_node_keys: Sequence[str] = (),
) -> None:
    """`decision_method='reasoning_llm'` must point at THIS feature's own run.

    Principle 7: an unverified pointer is not provenance. Existing-and-in-this-
    System was the whole of the old check, so any row -- another feature's
    mapping run, or a run that FAILED -- could be attached and the proposal
    would then read as reasoning-model output that a model never produced.

    Four deterministic checks, each with its own code:

    * the run is this feature's drafting run on this contract version
      (`run_type` + `prompt_version` + `schema_version` + `decision_method`);
    * it actually completed -- a `failed` run produced no draft at all, so
      citing it claims provenance for output that does not exist;
    * it backs at most one proposal, so a single draft cannot be spread over
      several rows as if each had been reasoned about;
    * and `reasoning_llm` without a run id is refused outright.

    And, since `flow_experiment_draft` records WHAT each run was about, two
    more (§7.1.3):

    * the run drafted THIS subject -- same `flow_subject_kind`,
      `flow_subject_ref` and pinned snapshot. `intelligence_runs` alone
      records only how a run was made, so Flow A's perfectly valid draft run
      could be attached to a hand-written proposal for Flow B, which would
      then read as reasoning-model output about B;
    * every target Node was one the model actually saw
      (`intelligence_run_target_not_drafted`). A SUBSET is fine: the human
      edits the draft before posting it, and dropping a Node is ordinary
      editing. ADDING one is not -- nothing reasoned about that Node, so the
      content concerning it has no provenance to claim.

    A run with no `flow_experiment_draft` row is refused rather than waved
    through (`intelligence_run_subject_unknown`). A drafting run predating
    this table stays readable as audit; what it cannot do is satisfy a check
    nobody recorded the answer to (#337's compatibility rule).
    """
    if decision_method == "reasoning_llm" and intelligence_run_id is None:
        raise FlowExperimentRejected(
            "intelligence_run_missing",
            "decision_method='reasoning_llm' には根拠となる intelligence run が必要です。",
        )
    if intelligence_run_id is None:
        return

    run = conn.execute(
        "SELECT * FROM intelligence_runs WHERE id = ? AND system_id = ?",
        (intelligence_run_id, system_id),
    ).fetchone()
    if run is None:
        # Indistinguishable from another System's id, deliberately.
        raise FlowExperimentNotFoundError(
            f"intelligence run {intelligence_run_id} not found"
        )
    if (
        run["run_type"] != DRAFT_RUN_TYPE
        or run["prompt_version"] != FLOW_EXPERIMENT_DRAFT_PROMPT_VERSION
        or run["schema_version"] != FLOW_EXPERIMENT_DRAFT_SCHEMA_VERSION
        or run["decision_method"] != "reasoning_llm"
    ):
        raise FlowExperimentRejected(
            "intelligence_run_not_a_draft",
            "指定された intelligence run は Flow 実験の draft 実行ではありません。",
            (
                f"{run['run_type']}/{run['prompt_version']}/{run['schema_version']}"
                f"/{run['decision_method']}",
            ),
        )
    if run["status"] != "completed":
        raise FlowExperimentRejected(
            "intelligence_run_not_completed",
            "完了していない intelligence run は provenance になりません "
            f"(status={run['status']})。",
            (str(run["status"]),),
        )
    used = conn.execute(
        "SELECT id FROM flow_experiment_proposal "
        "WHERE system_id = ? AND intelligence_run_id = ? LIMIT 1",
        (system_id, intelligence_run_id),
    ).fetchone()
    if used is not None:
        raise FlowExperimentRejected(
            "intelligence_run_already_used",
            "この intelligence run は既に別の提案の provenance として使われています。",
            (str(used["id"]),),
        )

    if content is None:
        return

    draft = conn.execute(
        "SELECT * FROM flow_experiment_draft "
        "WHERE system_id = ? AND intelligence_run_id = ?",
        (system_id, intelligence_run_id),
    ).fetchone()
    if draft is None:
        raise FlowExperimentRejected(
            "intelligence_run_subject_unknown",
            "この intelligence run は何を対象に draft されたかが記録されて"
            "いないため、この提案の provenance として検証できません。"
            "draft をやり直してください。",
            (str(intelligence_run_id),),
        )
    expected_digest = _draft_input_digest(
        flow_subject_kind=draft["flow_subject_kind"],
        flow_subject_ref=draft["flow_subject_ref"],
        captured_snapshot_id=draft["captured_snapshot_id"],
        node_keys=_json_or_default(draft["node_keys_json"], []),
        evidence_ids=_json_or_default(draft["evidence_ids_json"], []),
    )
    if not hmac.compare_digest(str(draft["input_digest"] or ""), expected_digest):
        raise FlowExperimentRejected(
            "intelligence_run_draft_digest_mismatch",
            "draft の subject / target / evidence envelope が記録時の digest と一致"
            "しないため provenance として利用できません。",
            (str(intelligence_run_id),),
        )
    if (
        draft["flow_subject_kind"] != content.flow_subject_kind
        or draft["flow_subject_ref"] != content.flow_subject_ref
        or draft["captured_snapshot_id"] != content.captured_snapshot_id
    ):
        raise FlowExperimentRejected(
            "intelligence_run_subject_mismatch",
            "この intelligence run は別の Flow / snapshot を対象に draft された"
            "ものです。別 Flow の draft run をこの提案の provenance にすることは"
            "できません。",
            (
                f"{draft['flow_subject_kind']}:{draft['flow_subject_ref']}"
                f"@{draft['captured_snapshot_id']}",
            ),
        )
    drafted = set(_json_or_default(draft["node_keys_json"], []))
    undrafted = sorted(set(target_node_keys) - drafted)
    if undrafted:
        raise FlowExperimentRejected(
            "intelligence_run_target_not_drafted",
            f"対象 Node {undrafted} は draft の対象ではありませんでした。"
            "モデルが見ていない Node について reasoning_llm の provenance を"
            "名乗ることはできません。",
            tuple(undrafted),
        )


def create_proposal(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    content: ProposalContent,
    targets: Sequence[Mapping[str, Any]],
    actor: Optional[str],
    actor_kind: str = "user",
    reason: str = "",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    grounding: Optional[FlowGrounding] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Create a proposal and its `proposed` event, or refuse with a finite code.

    `decision_method` here describes the PROVENANCE OF THE CONTENT (a draft
    that came from `propose_flow_experiment` is `reasoning_llm` and carries
    its `intelligence_run_id`), while the `proposed` EVENT is always `manual`
    with the authenticated principal as its actor. Those are two different
    facts and #337's three provenance axes require them to stay apart: a
    model may have written the words, but a human put the proposal into the
    queue (§7.7).
    """
    moment = time.time() if now is None else now
    if not _text(content.proposal_key):
        raise FlowExperimentValidationError("proposal_key must not be empty")
    _check_membership(
        decision_method, ("manual", "reasoning_llm", "deterministic"), "decision_method"
    )

    facts = gather_proposal_facts(
        conn,
        system_id=system_id,
        content=content,
        targets=targets,
        grounding=grounding,
    )
    verdict = evaluate_completeness(facts)
    if not verdict.ok:
        raise FlowExperimentRejected(verdict.code, verdict.message, verdict.detail)

    _validate_llm_provenance(
        conn,
        system_id=system_id,
        decision_method=decision_method,
        intelligence_run_id=intelligence_run_id,
        content=content,
        target_node_keys=[target.node_key for target in facts.targets],
    )

    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO flow_experiment_proposal
                   (system_id, proposal_key, flow_subject_kind, flow_subject_ref,
                    captured_snapshot_id, comparison_scope, title, purpose,
                    hypothesis, baseline_ref, candidate_refs_json,
                    evaluation_axes_json, quality_floor_json, isolation_strategy,
                    isolation_detail, cost_cap_json, stop_conditions_json,
                    rollback_plan, evidence_refs_json, expires_at,
                    decision_method, intelligence_run_id, created_by, created_at,
                    schema_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?)""",
            (
                system_id,
                content.proposal_key,
                content.flow_subject_kind,
                _text(content.flow_subject_ref),
                content.captured_snapshot_id,
                content.comparison_scope,
                content.title,
                content.purpose,
                content.hypothesis,
                content.baseline_ref,
                json.dumps(list(content.candidate_refs), ensure_ascii=False),
                json.dumps([dict(a) for a in content.evaluation_axes], ensure_ascii=False),
                json.dumps(dict(content.quality_floor), ensure_ascii=False),
                content.isolation_strategy,
                content.isolation_detail,
                json.dumps(dict(content.cost_cap), ensure_ascii=False),
                json.dumps(list(content.stop_conditions), ensure_ascii=False),
                content.rollback_plan,
                json.dumps(list(content.evidence_refs), ensure_ascii=False),
                content.expires_at,
                decision_method,
                intelligence_run_id,
                actor,
                moment,
                FLOW_EXPERIMENT_PROPOSAL_SCHEMA_VERSION,
            ),
        )
        proposal_id = cur.lastrowid
        for position, target in enumerate(targets):
            node_key = _text(target.get("node_key"))
            role = (
                _text(target.get("target_role"))
                or _text(target.get("role"))
                or "candidate_target"
            )
            conn.execute(
                """INSERT OR IGNORE INTO flow_experiment_target
                       (system_id, proposal_id, target_node_key, target_role,
                        position, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id,
                    proposal_id,
                    node_key,
                    role,
                    int(target.get("position", position)),
                    _text(target.get("note")),
                    moment,
                ),
            )
        _insert_event(
            conn,
            system_id=system_id,
            proposal_id=proposal_id,
            event_kind="proposed",
            actor=actor,
            actor_kind=actor_kind,
            reason=reason,
            # The human's act of proposing, regardless of who drafted the
            # words (§7.7).
            decision_method="manual",
            payload={"intelligence_run_id": intelligence_run_id},
            now=moment,
        )
        conn.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK")
        message = str(exc)
        if "flow_experiment_proposal.intelligence_run_id" in message:
            raise FlowExperimentRejected(
                "intelligence_run_already_used",
                "この intelligence run は既に別の提案の provenance として使われています。",
            ) from exc
        if "flow_experiment_proposal.system_id" in message:
            raise FlowExperimentRejected(
                "duplicate_proposal_key",
                "同じ proposal_key の提案がこの System に既に存在します。",
                (content.proposal_key,),
            ) from exc
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return _proposal_doc(conn, system_id, _proposal_row(conn, system_id, proposal_id), now=moment)


def _lifecycle_gate(
    conn: sqlite3.Connection,
    system_id: int,
    row: sqlite3.Row,
    action: str,
    now: float,
) -> None:
    events = [_event_doc(event) for event in _event_rows(conn, row["id"])]
    status = derive_proposal_status(events, now=now, expires_at=row["expires_at"])
    decision = evaluate_lifecycle(
        action,
        status=status,
        has_execution=any(e["event_kind"] == "execution_recorded" for e in events),
        has_result=any(e["event_kind"] == "result_recorded" for e in events),
    )
    if not decision.allowed:
        raise FlowExperimentLifecycleError(decision.code, decision.message)


def record_decision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    action: str,
    actor: Optional[str],
    reason: str,
    actor_kind: str = "user",
    payload: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Record a human's approve / reject / withdraw (§7.5, §10).

    `decision_method` is fixed to `manual` and `actor` comes from the route's
    authenticated principal -- never from a request body (#337). `reason` is
    required for a rejection and a withdrawal: a decision recorded without a
    reason cannot be reviewed later, and these two end a plan.
    """
    _check_membership(action, ("approve", "reject", "withdraw"), "action")
    moment = time.time() if now is None else now
    row = _proposal_row(conn, system_id, proposal_id)
    if action in ("reject", "withdraw") and not _text(reason):
        raise FlowExperimentValidationError("reason must not be empty")
    _lifecycle_gate(conn, system_id, row, action, moment)
    _insert_event(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        event_kind=ACTION_EVENT_KIND[action],
        actor=actor,
        actor_kind=actor_kind,
        reason=reason,
        decision_method="manual",
        payload=payload,
        now=moment,
    )
    return _proposal_doc(conn, system_id, row, now=moment)


def _require_execution_capabilities(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    flow_subject_kind: str,
    flow_subject_ref: str,
    execution_kind: str,
    now: float,
) -> List[execution_mode.ExecutionModeDecision]:
    """§4.3 / §7.5: EVERY target Node must permit candidate execution.

    Fail-closed over the whole target set, not the first Node: one Node of a
    sub-pipeline left in `propose` is enough to refuse the run, because
    executing the others would still execute a candidate inside a Flow whose
    permission is incomplete.

    `shadow_comparison` is additionally required when the reference IS a
    comparison. Both capabilities exist only in `shadow` today, so this
    changes no outcome -- it states which permission the act needs, so a
    later change to `MODE_CAPABILITIES` cannot silently widen it.

    Raises `execution_mode.ExecutionModeDenied`, which the route maps to the
    SAME 409 body as #413's own gate (`raise_execution_mode_denied`).
    """
    flow_ref = flow_subject_ref if flow_subject_kind == "runtime_flow" else None
    capabilities = ["candidate_execution"]
    if execution_kind == "shadow_result":
        capabilities.append("shadow_comparison")

    decisions: List[execution_mode.ExecutionModeDecision] = []
    for target in _target_rows(conn, proposal_id):
        for capability in capabilities:
            decisions.append(
                execution_mode.require_capability(
                    conn,
                    system_id=system_id,
                    capability=capability,
                    node_key=target["target_node_key"],
                    flow_ref=flow_ref,
                    now=now,
                )
            )
    return decisions


def require_execution_authorization(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: Optional[int],
    node_key: str,
    candidate_refs: Sequence[str] = (),
    candidate_required: bool = False,
    snapshot_id: Optional[int] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Authorize one *governed* execution before it starts.

    The mode gate and proposal approval are independent facts and both are
    required.  This function deliberately runs at the real Replay / Experiment
    / Candidate / live-shadow boundary; attaching a reference after the work
    ran cannot retroactively authorize it.

    Candidate identities are canonical patch digests (``patch_sha256:<hex>``)
    derived by the execution route, never free text supplied as a claim by the
    caller.  A static proposal is also pinned to its captured snapshot.
    """
    moment = time.time() if now is None else now
    if proposal_id is None:
        raise FlowExperimentRejected(
            "execution_proposal_required",
            "governed な候補実行には承認済み Flow proposal が必要です。",
        )

    row = _proposal_row(conn, system_id, int(proposal_id))
    status = derive_proposal_status(_event_rows(conn, int(proposal_id)), now=moment)
    if status not in ("approved", "executing"):
        raise FlowExperimentRejected(
            "execution_proposal_not_approved",
            f"Flow proposal {proposal_id} は実行を承認されていません (status={status})。",
            (status,),
        )
    if row["expires_at"] is not None and moment >= float(row["expires_at"]):
        raise FlowExperimentRejected(
            "execution_proposal_expired",
            f"Flow proposal {proposal_id} は期限切れです。",
        )

    targets = {str(target["target_node_key"]) for target in _target_rows(conn, int(proposal_id))}
    key = _text(node_key)
    if key not in targets:
        raise FlowExperimentRejected(
            "execution_target_not_authorized",
            f"Node {key!r} は Flow proposal {proposal_id} の対象ではありません。",
            tuple(sorted(targets)),
        )

    declared = {_text(ref) for ref in _json_or_default(row["candidate_refs_json"], [])}
    actual = {_text(ref) for ref in candidate_refs if _text(ref)}
    if candidate_required and not actual:
        raise FlowExperimentRejected(
            "execution_candidate_not_authorized",
            "実行する candidate の正本参照が無いため proposal と照合できません。",
        )
    unauthorized = sorted(actual - declared)
    if unauthorized:
        raise FlowExperimentRejected(
            "execution_candidate_not_authorized",
            "実行しようとしている candidate は proposal で承認されていません。",
            tuple(unauthorized),
        )

    captured = row["captured_snapshot_id"]
    if candidate_required and captured is None:
        raise FlowExperimentRejected(
            "execution_snapshot_authorization_required",
            "candidate execution の proposal はruntime/staticを問わず baseline "
            "snapshotをpinする必要があります。",
        )
    if captured is not None and snapshot_id != int(captured):
        raise FlowExperimentRejected(
            "execution_snapshot_mismatch",
            f"proposal がpinしたsnapshotは {captured} ですが、実行対象は {snapshot_id} です。",
            (str(captured), str(snapshot_id)),
        )

    # Re-evaluate every proposal target at execution time.  For runtime Flow
    # this also proves current Flow membership; a stale caller claim is refused
    # by execution_mode's flow_scope_not_member decision.
    _require_execution_capabilities(
        conn,
        system_id=system_id,
        proposal_id=int(proposal_id),
        flow_subject_kind=row["flow_subject_kind"],
        flow_subject_ref=row["flow_subject_ref"],
        execution_kind="experiment",
        now=moment,
    )

    # Side-effect facts can change after approval.  Re-read them before the
    # execution rather than trusting the proposal-time classification.
    if row["isolation_strategy"] in NON_ISOLATING_STRATEGIES:
        unsafe: List[str] = []
        for target_key in sorted(targets):
            node = _node_row(conn, system_id, target_key)
            if node is None:
                unsafe.append(target_key)
                continue
            if _node_side_effect_class(conn, system_id, node) in SIDE_EFFECT_ISOLATION_REQUIRED:
                unsafe.append(target_key)
        if unsafe:
            raise FlowExperimentRejected(
                "execution_isolation_stale",
                "承認後に副作用分類が変わり、現在のisolation strategyでは実行できません。",
                tuple(unsafe),
            )

    return {
        "proposal_id": int(proposal_id),
        "node_key": key,
        "candidate_refs": sorted(actual),
        "snapshot_id": snapshot_id,
        "authorized_at": moment,
    }


def _record_execution_locked(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    execution_kind: str,
    execution_ref: str,
    actor: Optional[str],
    note: str = "",
    actor_kind: str = "user",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Attach an execution that already exists elsewhere (§7.6).

    Two INDEPENDENT facts are required and neither implies the other (§7.5):
    the proposal must be approved (`not_approved` otherwise) AND every target
    Node's effective execution mode must permit `candidate_execution`
    (`ExecutionModeDenied` otherwise). This module runs nothing itself and
    creates no row in `replay_variants` / `experiments` / `shadow_results` --
    it points at one that the existing canonical path already produced, and
    refuses a pointer that does not resolve.

    Because the execution happens on the canonical path and the reference is
    attached afterwards, the pointer is also BOUND to this proposal
    (`_require_execution_binding`): the run must be on one of the proposal's
    own target Nodes, and it must have run at or after the approval. Without
    both, "this approved proposal was executed" is a claim the ledger cannot
    check -- and an audit trail that cannot be checked is not one (§7.5).
    """
    _check_membership(execution_kind, EXECUTION_KINDS, "execution_kind")
    ref = _text(execution_ref)
    if not ref:
        raise FlowExperimentValidationError("execution_ref must not be empty")
    moment = time.time() if now is None else now
    row = _proposal_row(conn, system_id, proposal_id)

    _lifecycle_gate(conn, system_id, row, "record_execution", moment)
    _require_execution_capabilities(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        flow_subject_kind=row["flow_subject_kind"],
        flow_subject_ref=row["flow_subject_ref"],
        execution_kind=execution_kind,
        now=moment,
    )

    resolution = _execution_ref_resolution(conn, system_id, execution_kind, ref)
    if resolution == "unresolved":
        raise FlowExperimentValidationError(
            f"execution_ref {execution_kind}:{ref} はこの System で解決できません。"
        )

    # Existing-and-in-this-System was the whole of the check. It let any
    # canonical execution in the System be attached to any approved proposal,
    # so the ledger's claim that this approval authorised that run was the
    # caller's word (EM-ADR-4). The reference must also be ON one of this
    # proposal's target Nodes and AFTER the approval.
    ref_row = _execution_ref_row(conn, system_id, execution_kind, ref)
    if ref_row is None:  # pragma: no cover - `resolution` already refused it
        raise FlowExperimentValidationError(
            f"execution_ref {execution_kind}:{ref} はこの System で解決できません。"
        )
    _require_execution_binding(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        execution_kind=execution_kind,
        execution_ref=ref,
        row=ref_row,
    )

    already = conn.execute(
        "SELECT id FROM flow_experiment_execution_ref "
        "WHERE system_id = ? AND proposal_id = ? AND execution_kind = ? AND execution_ref = ?",
        (system_id, proposal_id, execution_kind, ref),
    ).fetchone()
    if already is not None:
        return _proposal_doc(conn, system_id, row, now=moment)

    conn.execute(
        """INSERT INTO flow_experiment_execution_ref
               (system_id, proposal_id, execution_kind, execution_ref, note,
                recorded_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (system_id, proposal_id, execution_kind, ref, note, moment),
    )
    _insert_event(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        event_kind="execution_recorded",
        actor=actor,
        actor_kind=actor_kind,
        reason=note,
        decision_method="deterministic" if actor_kind == "system" else "manual",
        payload={"execution_kind": execution_kind, "execution_ref": ref},
        now=moment,
    )
    return _proposal_doc(conn, system_id, row, now=moment)


def record_execution(conn: sqlite3.Connection, **kwargs: Any) -> Dict[str, Any]:
    """Check and bind a canonical execution in one cross-process write lock.

    Callers that already opened ``BEGIN IMMEDIATE`` retain ownership of the
    transaction, allowing the actual execution route to include its canonical
    pin in the same atomic unit.
    """
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        result = _record_execution_locked(conn, **kwargs)
        if owns_transaction:
            conn.execute("COMMIT")
        return result
    except sqlite3.IntegrityError as exc:
        if owns_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")
        if "flow_experiment_execution_ref.system_id" in str(exc):
            raise FlowExperimentRejected(
                "execution_ref_already_bound",
                "canonical execution は既に別のproposalへ拘束されています。",
            ) from exc
        raise
    except Exception:
        if owns_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _validate_metrics(metrics: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Result metrics are grouped by the three evaluation contracts (ADR-7).

    Only `EVALUATION_LEVELS` keys are accepted, and no level is ever computed
    from another: what the caller did not measure stays absent, because an
    absent measurement and a derived one are different facts.
    """
    if not metrics:
        return {}
    unknown = [key for key in metrics if key not in EVALUATION_LEVELS]
    if unknown:
        raise FlowExperimentValidationError(
            "metrics のキーは "
            f"{', '.join(EVALUATION_LEVELS)} のみです; 不明: {', '.join(sorted(unknown))}"
        )
    return {key: metrics[key] for key in metrics}


def _require_bound_execution(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    execution_kind: Optional[str],
    execution_ref: Optional[str],
) -> Tuple[str, str]:
    """The named execution must be THIS proposal's, and it must still work.

    Four separate refusals, because four different things are wrong and the
    developer's next action differs each time:

    * `execution_ref_missing` -- nothing was named. A result is the
      observation of ONE execution; "the result of this proposal" without
      saying which run produced it cannot be audited.
    * `execution_ref_not_registered` -- the run exists somewhere, but it was
      never attached to this proposal. Attaching execution B's outcome to
      proposal A is precisely how the ledger stops being a record of what
      happened.
    * `execution_ref_unresolved` -- resolved AT READ TIME (#405), not trusted
      because it was accepted once: the canonical row may since have been
      removed, or belong to another System.
    * `execution_ref_failed` -- it resolves, but its own run concluded it
      produced nothing usable. A failed run has no result to report.
    """
    kind = _text(execution_kind)
    ref = _text(execution_ref)
    if not kind or not ref:
        raise FlowExperimentRejected(
            "execution_ref_missing",
            "結果・昇格候補は、この提案に記録済みの実行を 1 件名指しする必要が"
            "あります (execution_kind / execution_ref)。",
        )
    _check_membership(kind, EXECUTION_KINDS, "execution_kind")

    registered = conn.execute(
        """SELECT id FROM flow_experiment_execution_ref
            WHERE system_id = ? AND proposal_id = ?
              AND execution_kind = ? AND execution_ref = ?
            LIMIT 1""",
        (system_id, proposal_id, kind, ref),
    ).fetchone()
    if registered is None:
        raise FlowExperimentRejected(
            "execution_ref_not_registered",
            "この提案に記録されていない実行です。他の提案の実行に結果を"
            "紐付けることはできません (§7.6)。",
            (f"{kind}:{ref}",),
        )

    resolution = _execution_ref_resolution(conn, system_id, kind, ref)
    if resolution == "unresolved":
        raise FlowExperimentRejected(
            "execution_ref_unresolved",
            "実行参照が読み取り時に解決できません。保存された id を単独で"
            "信用しません (#405)。",
            (f"{kind}:{ref}",),
        )
    if resolution == "stale":
        raise FlowExperimentRejected(
            "execution_ref_failed",
            "実行そのものが失敗しています。失敗した実行に報告すべき結果は"
            "存在しません。",
            (f"{kind}:{ref}",),
        )
    return kind, ref


def _axis_metric_key(axis: Mapping[str, Any]) -> str:
    """The key a result must report an axis under: its `metric`, else its
    `name`. Exact equality only -- no similarity, no aliasing (Principle 6)."""
    return _text(axis.get("metric")) or _text(axis.get("name"))


def _require_declared_metrics(
    row: sqlite3.Row, metrics: Mapping[str, Any]
) -> None:
    """The result must measure what the PROPOSAL declared (§7.1's evaluation
    contract), not whatever the reporter happened to have.

    Per declared level, and per axis inside it. A level with no declared axes
    is not required -- ADR-7 keeps the three contracts apart, and demanding a
    Flow/Capability number from a proposal that declared none would be
    inventing a contract it never signed.
    """
    grouped = _axes_by_level(_json_or_default(row["evaluation_axes_json"], []))
    missing: List[str] = []
    for level in EVALUATION_LEVELS:
        axes = grouped.get(level) or []
        if not axes:
            continue
        measured = metrics.get(level)
        for axis in axes:
            key = _axis_metric_key(axis)
            if not key:
                continue
            if not isinstance(measured, Mapping) or key not in measured:
                missing.append(f"{level}:{key}")
    if missing:
        raise FlowExperimentRejected(
            "result_metrics_missing",
            "提案が宣言した評価軸の測定値がありません。宣言した契約で評価"
            "されていない結果は、この提案の結果ではありません (ADR-7)。",
            tuple(missing),
        )


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _find_measurement(metrics: Mapping[str, Any], key: str) -> Tuple[bool, Any]:
    """Look one floor key up across the three levels. EXACT key match only."""
    for level in EVALUATION_LEVELS:
        measured = metrics.get(level)
        if isinstance(measured, Mapping) and key in measured:
            return True, measured[key]
    return False, None


def _evaluate_quality_floor(
    row: sqlite3.Row, metrics: Mapping[str, Any]
) -> Dict[str, Any]:
    """Compare the result against the floor the proposal declared, and RECORD
    the verdict. It decides nothing.

    §7.6 / ADR-9: this module adopts, rejects and promotes nothing, so the
    verdict is an observation written into the ledger and `auto_adopted` /
    `auto_rejected` are stated as `false` rather than implied. A human still
    decides, on their own screen, through their own gate.

    Only a numeric floor against a numeric measurement is comparable.
    `unmeasured` (nothing measured this floor) and `not_comparable` (a prose
    floor has no ordering) are two different answers and NEITHER is a pass --
    reporting either as `within_floor` would claim a floor was held that
    nobody checked (§6.4's five-answer discipline).
    """
    floor = _json_or_default(row["quality_floor_json"], {})
    keys: Dict[str, Any] = {}
    if isinstance(floor, Mapping):
        for key, bound in floor.items():
            present, measured = _find_measurement(metrics, str(key))
            if not present:
                verdict = "unmeasured"
            else:
                bound_value = _numeric(bound)
                measured_value = _numeric(measured)
                if bound_value is None or measured_value is None:
                    verdict = "not_comparable"
                else:
                    verdict = (
                        "within_floor" if measured_value >= bound_value else "below_floor"
                    )
            keys[str(key)] = {
                "verdict": verdict,
                "floor": bound,
                "measured": measured if present else None,
            }

    verdicts = {entry["verdict"] for entry in keys.values()}
    if "below_floor" in verdicts:
        overall = "below_floor"
    elif not keys or verdicts - {"within_floor"}:
        overall = "unevaluated"
    else:
        overall = "within_floor"
    return {
        "verdict": overall,
        "keys": keys,
        # Stated, never implied (§7.6): recording a verdict is not a decision.
        "auto_adopted": False,
        "auto_rejected": False,
    }


def record_result(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    summary: str,
    actor: Optional[str],
    execution_kind: Optional[str] = None,
    execution_ref: Optional[str] = None,
    metrics: Optional[Mapping[str, Any]] = None,
    actor_kind: str = "user",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Record what ONE named execution of THIS proposal produced.

    Never adopts, promotes or applies. The result is deliberately a ledger
    event and not a table of its own: the numbers belong to the execution's
    canonical row, and duplicating them here would create a second copy that
    can disagree with it (#405).

    Three bindings make it a record of what happened rather than a free-text
    claim: it names a registered execution of this proposal that still
    resolves, it carries the measurements the proposal's own evaluation
    contract declared, and the declared quality floor is evaluated and
    RECORDED. The floor verdict decides nothing -- a human still does.
    """
    if not _text(summary):
        raise FlowExperimentValidationError("summary must not be empty")
    moment = time.time() if now is None else now
    row = _proposal_row(conn, system_id, proposal_id)
    _lifecycle_gate(conn, system_id, row, "record_result", moment)
    kind, ref = _require_bound_execution(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        execution_kind=execution_kind,
        execution_ref=execution_ref,
    )
    validated = _validate_metrics(metrics)
    _require_declared_metrics(row, validated)
    payload: Dict[str, Any] = {
        "summary": summary,
        "metrics": validated,
        "execution_kind": kind,
        "execution_ref": ref,
        "quality_floor_evaluation": _evaluate_quality_floor(row, validated),
    }
    _insert_event(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        event_kind="result_recorded",
        actor=actor,
        actor_kind=actor_kind,
        reason=summary,
        decision_method="manual",
        payload=payload,
        now=moment,
    )
    return _proposal_doc(conn, system_id, row, now=moment)


def record_promotion_candidate(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    candidate_ref: str,
    rationale: str,
    actor: Optional[str],
    execution_kind: Optional[str] = None,
    execution_ref: Optional[str] = None,
    actor_kind: str = "user",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Record a promotion CANDIDATE -- which is not a promotion (§7.6).

    Nothing here adopts an Experiment variant, moves a Node's maturity,
    creates a publish job, or writes a line of source. The actual promotion
    still goes through the existing Experiment adoption / Stabilization
    (#399) / publish (#216) human gates, each of which keeps its own record.

    It binds to THREE facts, with a finite code per failure. A candidate the
    proposal never declared is a candidate nobody proposed; an execution that
    does not resolve is not evidence anything ran; and an execution with no
    recorded result is an UNEVALUATED candidate. Admitting any of the three
    would put a candidate into the ledger that nothing ever measured -- and
    this row is what the downstream promotion gates read.
    """
    if not _text(candidate_ref):
        raise FlowExperimentValidationError("candidate_ref must not be empty")
    if not _text(rationale):
        raise FlowExperimentValidationError("rationale must not be empty")
    moment = time.time() if now is None else now
    row = _proposal_row(conn, system_id, proposal_id)
    _lifecycle_gate(conn, system_id, row, "record_promotion_candidate", moment)

    declared = [
        _text(ref)
        for ref in _json_or_default(row["candidate_refs_json"], [])
        if _text(ref)
    ]
    if _text(candidate_ref) not in declared:
        raise FlowExperimentRejected(
            "candidate_ref_not_declared",
            "この提案が宣言していない候補です。提案されていない候補を"
            "昇格候補として記録することはできません。",
            (_text(candidate_ref),),
        )

    kind, ref = _require_bound_execution(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        execution_kind=execution_kind,
        execution_ref=execution_ref,
    )

    # `_lifecycle_gate` above only asked whether SOME result exists (§7.4's
    # `no_result_recorded`). This asks the binding question: was a result
    # recorded for THIS execution? A candidate judged by another run's
    # numbers has not been judged.
    has_result = any(
        doc["event_kind"] == "result_recorded"
        and _text(doc["payload"].get("execution_kind")) == kind
        and _text(doc["payload"].get("execution_ref")) == ref
        for doc in (_event_doc(event) for event in _event_rows(conn, proposal_id))
    )
    if not has_result:
        raise FlowExperimentRejected(
            "no_result_for_execution",
            "この実行に対する結果が記録されていません。評価されていない"
            "候補は昇格候補になりません。",
            (f"{kind}:{ref}",),
        )

    _insert_event(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        event_kind="promotion_candidate_recorded",
        actor=actor,
        actor_kind=actor_kind,
        reason=rationale,
        decision_method="manual",
        payload={
            "candidate_ref": _text(candidate_ref),
            "execution_kind": kind,
            "execution_ref": ref,
            "promotion_performed": False,
        },
        now=moment,
    )
    return _proposal_doc(conn, system_id, row, now=moment)


def record_rollback(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    proposal_id: int,
    detail: str,
    actor: Optional[str],
    actor_kind: str = "user",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Record that the proposal's rollback plan was carried out.

    Like every other row here this is an AUDIT fact about work performed
    elsewhere: this module has no ability to revert anything, and the
    rollback itself is executed through the canonical path that owns the
    change (§7.6).
    """
    if not _text(detail):
        raise FlowExperimentValidationError("detail must not be empty")
    moment = time.time() if now is None else now
    row = _proposal_row(conn, system_id, proposal_id)
    _lifecycle_gate(conn, system_id, row, "record_rollback", moment)
    _insert_event(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        event_kind="rollback_recorded",
        actor=actor,
        actor_kind=actor_kind,
        reason=detail,
        decision_method="manual",
        payload={"detail": detail},
        now=moment,
    )
    return _proposal_doc(conn, system_id, row, now=moment)


# ---------------------------------------------------------------------------
# §7.7 Reasoning-model drafting
# ---------------------------------------------------------------------------

_DRAFT_SYSTEM_PROMPT = (
    "You plan Flow-scoped experiments for a runtime probe platform. You never "
    "approve, adopt, merge or deploy anything: your output is a DRAFT a human "
    "will review, edit and decide on. Ground every element in the facts you "
    "are given and never invent a Node, a metric or an evidence reference "
    "that is not in them. Reply with ONE JSON object and nothing else."
)

_DRAFT_PROMPT_TEMPLATE = """{marker}

Flow subject: {flow_subject_kind}:{flow_subject_ref}
Improvement goal stated by the developer: {goal}
Target Evolution Nodes (node_key / mission / side_effect_class):
{nodes}

{grounding}

Return a JSON object with exactly these keys:
  "title": string
  "purpose": string  (why this experiment exists)
  "hypothesis": string
  "comparison_scope": "single_node" | "sub_pipeline"
  "target_node_keys": array of node_key strings, drawn ONLY from the list above
  "baseline_ref": string  (what the candidates are compared against)
  "candidate_refs": array of strings, at least one
  "evaluation_axes": array of objects, each {{"level": "node"|"flow_capability"|"ux_outcome", "name": string, "metric": string}}
  "quality_floor": object mapping a floor name to the value that must not be broken
  "isolation_strategy": one of {isolation_strategies}
  "isolation_detail": string
  "cost_cap": object with at least one positive numeric limit
  "stop_conditions": array of strings, at least one
  "rollback_plan": string
  "evidence_refs": array of strings, at least one, each of which MUST be an id
      from the evidence catalogue above, copied exactly. Any other value fails
      the whole run -- do not invent, abbreviate or reformat an id.
  "risks": array of strings

Ground the purpose, the hypothesis, the evaluation axes and the quality floor
in the Flow state, the open items, the baseline and the evidence catalogue
above. Those are the only facts you have; do not assert anything else.

Node-level and Flow/Capability-level metrics are separate contracts: never
derive one from the other, and never return a combined score.
"""

_DRAFT_REQUIRED_KEYS: Tuple[str, ...] = (
    "title", "purpose", "hypothesis", "comparison_scope", "target_node_keys",
    "baseline_ref", "candidate_refs", "evaluation_axes", "quality_floor",
    "isolation_strategy", "isolation_detail", "cost_cap", "stop_conditions",
    "rollback_plan", "evidence_refs",
)


def _parse_draft_response(
    raw: str,
    allowed_node_keys: Sequence[str],
    allowed_evidence_ids: FrozenSet[str],
) -> Dict[str, Any]:
    """Validate the structured output. ANY failure fails the whole run.

    Principle 6: there is no partial acceptance and no heuristic repair. A
    draft missing its stop conditions is not a draft with fewer fields -- it
    is a plan that cannot be reviewed, and inventing the missing part here
    would make the model's omission invisible.

    `evidence_refs` is checked against the ids #414's projection actually
    produced, for the same reason `target_node_keys` is checked against the
    requested Nodes: a citation the model composed is a fabricated fact, and
    "a non-empty string" was never a check that anything was cited. The gate
    at `POST /flow-experiments` repeats this check, because a human edits the
    draft in between and a ref valid here can be stale by then.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -3]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FlowExperimentReasoningError(f"draft response was not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FlowExperimentReasoningError("draft response was not a JSON object")

    missing = [key for key in _DRAFT_REQUIRED_KEYS if key not in payload]
    if missing:
        raise FlowExperimentReasoningError(
            f"draft response is missing required keys: {', '.join(missing)}"
        )
    for key in ("title", "purpose", "hypothesis", "baseline_ref", "rollback_plan"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise FlowExperimentReasoningError(f"draft field {key!r} must be a non-empty string")
    for key in ("candidate_refs", "stop_conditions", "evidence_refs", "target_node_keys"):
        value = payload[key]
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise FlowExperimentReasoningError(
                f"draft field {key!r} must be a non-empty array of strings"
            )
    if payload["comparison_scope"] not in COMPARISON_SCOPES:
        raise FlowExperimentReasoningError("draft comparison_scope is outside the finite set")
    if payload["isolation_strategy"] not in ISOLATION_STRATEGIES:
        raise FlowExperimentReasoningError("draft isolation_strategy is outside the finite set")
    for key in ("quality_floor", "cost_cap"):
        if not isinstance(payload[key], dict) or not payload[key]:
            raise FlowExperimentReasoningError(f"draft field {key!r} must be a non-empty object")
    axes = payload["evaluation_axes"]
    if not isinstance(axes, list) or not axes:
        raise FlowExperimentReasoningError("draft evaluation_axes must be a non-empty array")
    for axis in axes:
        if (
            not isinstance(axis, dict)
            or axis.get("level") not in EVALUATION_LEVELS
            or not isinstance(axis.get("name"), str)
            or not axis["name"].strip()
        ):
            raise FlowExperimentReasoningError(
                "each evaluation axis needs a finite `level` and a non-empty `name`"
            )
    unknown_nodes = [
        key for key in payload["target_node_keys"] if key not in set(allowed_node_keys)
    ]
    if unknown_nodes:
        # A drafted Node outside the requested set is an invented fact, which
        # is the failure mode Principle 6 exists to stop -- not a value to be
        # silently dropped.
        raise FlowExperimentReasoningError(
            f"draft names Nodes outside the request: {', '.join(sorted(unknown_nodes))}"
        )
    unknown_evidence = sorted(
        {
            str(ref).strip()
            for ref in payload["evidence_refs"]
            if str(ref).strip() not in allowed_evidence_ids
        }
    )
    if unknown_evidence:
        # Same class of failure as an invented Node, and the same answer: a
        # citation the model composed is a fabricated fact, not a value to
        # drop quietly (Principle 6).
        raise FlowExperimentReasoningError(
            "draft cites evidence the projection never produced: "
            f"{', '.join(unknown_evidence)}"
        )
    return payload


def propose_flow_experiment(
    *,
    system_id: int,
    flow_subject_kind: str,
    flow_subject_ref: str,
    node_keys: Sequence[str],
    goal: str,
    snapshot_id: Optional[int] = None,
    now: Optional[float] = None,
) -> DraftResult:
    """Draft a Flow experiment with the experiment reasoning model (§7.7).

    **The draft is grounded in #414's projection, not in the goal alone.** The
    model is given the Flow's real state -- per-Node axes, open items, the
    missing / stale / unmeasured readings, mode divergence, anomalies, the
    baseline and the existing experiments -- plus the catalogue of citable
    evidence ids, and `evidence_refs` is validated against exactly that
    catalogue. Before this, the prompt carried only the Flow ref, the goal and
    each Node's mission, and "an evidence ref" meant "a non-empty string": a
    wholly fabricated citation could reach a canonical row, which is precisely
    what Principle 6 and §7.7 exist to prevent.

    **Reachable only in `propose` / `shadow`.** The client is built through
    `execution_mode.build_experiment_llm_adapter`, whose body order is the
    contract: the capability gate runs before the first line that could read
    a credential, so in `fixed` / `observe` this function raises
    `ExecutionModeDenied` without a credential ever being read (EM-ADR-3).
    Every named Node is gated, not just the first: drafting an experiment
    that spans a Node nobody permitted is still spending the reasoning budget
    on that Node. Every named Node is gated TWICE -- once before the
    grounding read and once immediately before the credential read -- because
    the connection is closed for the whole of the grounding, which is the
    slowest step here, and a mode revoked inside that window must still stop
    the call.

    **This creates NOTHING but two audit rows.** No proposal, no `proposed`
    event, no queue entry. The rows are the `intelligence_runs` row (how the
    run was made) and the `flow_experiment_draft` row (what it was about);
    the second exists so a later proposal citing this run can be checked
    against the Flow it actually drafted (§7.1.3). The draft is `decision_method: reasoning_llm` and
    a human must post it through `create_proposal` (§7.7 / Principle 7).

    Connection discipline: this function owns its connections and must be
    called with NONE open -- read, close, reason, reopen, persist. Holding
    `get_conn()` across the round trip deadlocks the whole server.
    """
    from .db import get_conn  # local import: keeps the module import-light

    moment = time.time() if now is None else now
    keys = [k.strip() for k in node_keys if (k or "").strip()]
    if not keys:
        raise FlowExperimentValidationError("node_keys must not be empty")
    _check_membership(flow_subject_kind, FLOW_SUBJECT_KINDS, "flow_subject_kind")
    if not _text(goal):
        raise FlowExperimentValidationError("goal must not be empty")

    # --- Phase 1: read + gate (connection held, no external call) ---------
    #
    # The capability gate runs HERE, before anything expensive and before the
    # first line that could read a credential (EM-ADR-3). A denied request
    # therefore never reaches the projection read below, let alone the model.
    flow_ref = flow_subject_ref if flow_subject_kind == "runtime_flow" else None
    with get_conn() as conn:
        _require_system(conn, system_id)
        node_facts: List[str] = []
        for key in keys:
            node_row = _node_row(conn, system_id, key)
            if node_row is None:
                raise FlowExperimentNotFoundError(f"Evolution Node {key!r} not found")
            side_effect = _node_side_effect_class(conn, system_id, node_row) or "unknown"
            mission = ""
            if node_row["current_version_id"] is not None:
                version = conn.execute(
                    "SELECT mission FROM evolution_node_version WHERE id = ?",
                    (node_row["current_version_id"],),
                ).fetchone()
                mission = "" if version is None else version["mission"]
            node_facts.append(f"- {key} / {mission or '(mission unrecorded)'} / {side_effect}")

        for key in keys:
            execution_mode.require_capability(
                conn,
                system_id=system_id,
                capability="llm_experiment_proposal",
                node_key=key,
                flow_ref=flow_ref,
                now=moment,
            )

    # --- Phase 1b: the grounded facts (NO connection held) ----------------
    #
    # `load_flow_grounding` owns its own connections and, for a static Flow,
    # builds a call graph -- so it may not run inside the block above. Both
    # refusals here happen BEFORE the model is called: spending the reasoning
    # budget to produce citations the gate will then refuse is not fail-closed,
    # it is fail-expensive.
    grounding = load_flow_grounding(
        system_id,
        subject_kind=flow_subject_kind,
        subject_ref=flow_subject_ref,
        snapshot_id=snapshot_id,
        now=moment,
    )
    if grounding.state != "resolved":
        raise FlowExperimentRejected(
            "evidence_allowlist_unavailable",
            "この Flow の projection を読み取れなかったため、根拠のある提案を"
            f"生成できません: {grounding.detail}",
        )
    if not grounding.evidence_ids:
        raise FlowExperimentRejected(
            "evidence_ref_unknown",
            "この Flow には引用可能な evidence が 1 件もありません。根拠の無い"
            "提案は作成しません (§7.1)。",
        )

    # --- Phase 1c: re-gate EVERY Node, then build the client --------------
    #
    # Phase 1's gate ran before the grounding read, and the connection was
    # closed for the whole of Phase 1b -- which builds a call graph for a
    # static Flow and is the slowest thing this function does. A Node
    # demoted to `fixed` / `observe` during that window was permitted by a
    # reading that is no longer true.
    #
    # `build_experiment_llm_adapter` re-evaluates its own `node_key` before
    # the first line that could read a credential (EM-ADR-3), so for a
    # single-Node draft one call was enough. For a `sub_pipeline` draft it
    # was not: only `keys[0]` was re-read, and the fail-closed guarantee
    # that credentials are never touched for an unpermitted Node held for
    # the first Node alone. Re-gating the REST here, before the adapter
    # call, restores it for all of them -- a refusal on `keys[1]` raises
    # from this loop, which runs strictly earlier than any credential read.
    # The re-gate reads the CLOCK, not `moment`. `moment` was taken before
    # Phase 1, and an assignment written during the grounding read carries an
    # `effective_from` later than it -- so re-evaluating at `moment` would
    # find the revocation not yet in force and permit the call anyway, which
    # is a re-gate that cannot see the only window it exists to cover.
    regate_at = time.time()
    with get_conn() as conn:
        for key in keys[1:]:
            execution_mode.require_capability(
                conn,
                system_id=system_id,
                capability="llm_experiment_proposal",
                node_key=key,
                flow_ref=flow_ref,
                now=regate_at,
            )
        client, decision = execution_mode.build_experiment_llm_adapter(
            conn,
            system_id=system_id,
            node_key=keys[0],
            flow_ref=flow_ref,
            purpose="flow_experiment_draft",
            now=regate_at,
        )
        config = llm.LLMConfig.intelligence_from_env()

    # --- Phase 2: reason (NO connection held) -----------------------------
    is_mock = config.provider == "mock"
    started_at = time.time()
    error: Optional[str] = None
    draft: Optional[Dict[str, Any]] = None
    try:
        if not is_mock and not llm.is_reasoning_model(config.provider, config.model):
            raise FlowExperimentReasoningError(
                "Flow experiment drafting requires a configured reasoning model"
            )
        prompt = _DRAFT_PROMPT_TEMPLATE.format(
            marker=DRAFT_RESPONSE_MARKER,
            flow_subject_kind=flow_subject_kind,
            flow_subject_ref=flow_subject_ref,
            goal=goal,
            nodes="\n".join(node_facts),
            grounding=grounding.context,
            isolation_strategies=", ".join(ISOLATION_STRATEGIES),
        )
        raw = client.generate_text(
            [
                {"role": "system", "content": _DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2048,
        )
        draft = _parse_draft_response(raw, keys, grounding.evidence_ids)
    except (
        FlowExperimentReasoningError,
        llm.LLMError,
        llm.LLMResourceLimitError,
        json.JSONDecodeError,
    ) as exc:
        error = f"{type(exc).__name__}: {exc}"
    completed_at = time.time()

    # --- Phase 3: persist the audit row (connection reopened) -------------
    #
    # Two rows, not one. `intelligence_runs` records HOW the run was made;
    # `flow_experiment_draft` records WHAT it was about, and without the
    # second there is no way to answer "is this the run that drafted THIS
    # Flow?" -- so a valid draft of one Flow could be attached as provenance
    # to a hand-written proposal for another (§7.1.3). The subject row is
    # written for a FAILED run too: what a run was about is a fact about the
    # attempt, not about its outcome.
    run_status = "completed" if error is None else "failed"
    input_digest = _draft_input_digest(
        flow_subject_kind=flow_subject_kind,
        flow_subject_ref=flow_subject_ref,
        captured_snapshot_id=snapshot_id,
        node_keys=keys,
        evidence_ids=grounding.evidence_ids,
    )
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO intelligence_runs
                   (system_id, snapshot_id, run_type, provider, model,
                    prompt_version, schema_version, decision_method, status,
                    error_details, is_mock, started_at, completed_at)
               VALUES (?, NULL, 'flow_experiment_draft', ?, ?, ?, ?, 'reasoning_llm',
                       ?, ?, ?, ?, ?)""",
            (
                system_id,
                config.provider,
                config.model,
                FLOW_EXPERIMENT_DRAFT_PROMPT_VERSION,
                FLOW_EXPERIMENT_DRAFT_SCHEMA_VERSION,
                run_status,
                error,
                1 if is_mock else 0,
                started_at,
                completed_at,
            ),
        )
        run_id = cur.lastrowid
        conn.execute(
            """INSERT INTO flow_experiment_draft
                   (system_id, intelligence_run_id, flow_subject_kind,
                    flow_subject_ref, captured_snapshot_id, node_keys_json,
                    evidence_ids_json, input_digest, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id,
                run_id,
                flow_subject_kind,
                flow_subject_ref,
                snapshot_id,
                json.dumps(sorted(keys), ensure_ascii=False),
                json.dumps(sorted(grounding.evidence_ids), ensure_ascii=False),
                input_digest,
                completed_at,
            ),
        )

    if error is not None or draft is None:
        # Fail the run (Principle 6). The audit row above is the only thing
        # this call leaves behind.
        raise FlowExperimentReasoningError(error or "draft validation failed")

    return DraftResult(
        draft=draft,
        intelligence_run_id=run_id,
        is_mock=is_mock,
        decision=decision,
        provider=config.provider,
        model=config.model,
    )
