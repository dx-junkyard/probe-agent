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

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
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
    "node_not_in_flow",
    "isolation_required_for_side_effects",
    "evaluation_contract_missing",
    "duplicate_proposal_key",
)

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
class TargetFact:
    """One target Node as the gate sees it.

    `in_flow` is a THREE-valued reading, not a boolean: `None` means
    membership is not determinable from persisted rows alone, which is the
    honest answer for a `static_flow` subject (§6.2 needs the snapshot's call
    graph, a derivation that can fail -- and EM-ADR-1 forbids putting a
    failing derivation behind a fail-closed gate). Reporting `False` there
    would refuse a legitimate proposal for a fact nobody read.

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
    reach the gate, so the gate stays a pure function of this value."""

    content: ProposalContent
    targets: Tuple[TargetFact, ...]
    flow_subject_known: bool
    proposal_key_taken: bool


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

    # `in_flow is None` is skipped, never treated as False -- see TargetFact.
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
        kind = event["event_kind"] if isinstance(event, Mapping) else event.event_kind
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


def gather_proposal_facts(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    content: ProposalContent,
    targets: Sequence[Mapping[str, Any]],
) -> ProposalFacts:
    """Read every persisted fact the gate needs. Contains NO judgement.

    The split mirrors `execution_mode.load_mode_facts` / `resolve_execution_
    mode` and `stabilization.gather_gate_facts` / `evaluate_establishment_
    gate`: the reader knows how to find a fact, the evaluator knows what it
    means, and only the evaluator is a contract.
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
        else:
            # static_flow membership needs the snapshot's call graph (#414
            # §6.2). A derivation that can fail must never be a fail-closed
            # gate's input (EM-ADR-1), so it is reported as "not determinable"
            # rather than guessed in either direction.
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

    return ProposalFacts(
        content=content,
        targets=tuple(facts),
        flow_subject_known=_flow_subject_known(
            conn, system_id, kind, ref, content.captured_snapshot_id
        ),
        proposal_key_taken=taken is not None,
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
    flow_subject_ref: Optional[str] = None,
    limit: int = 100,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """The System's proposals, newest first.

    `status` filters on the DERIVED value, computed per row -- there is no
    column to filter on in SQL, and adding one is exactly what §7.4 forbids.
    """
    if status is not None:
        _check_membership(status, PROPOSAL_STATUSES, "status")
    sql = "SELECT * FROM flow_experiment_proposal WHERE system_id = ?"
    params: List[Any] = [system_id]
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
        conn, system_id=system_id, content=content, targets=targets
    )
    verdict = evaluate_completeness(facts)
    if not verdict.ok:
        raise FlowExperimentRejected(verdict.code, verdict.message, verdict.detail)

    if intelligence_run_id is not None:
        run = conn.execute(
            "SELECT id FROM intelligence_runs WHERE id = ? AND system_id = ?",
            (intelligence_run_id, system_id),
        ).fetchone()
        if run is None:
            raise FlowExperimentNotFoundError(
                f"intelligence run {intelligence_run_id} not found"
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


def record_execution(
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

    conn.execute("BEGIN")
    try:
        conn.execute(
            """INSERT OR IGNORE INTO flow_experiment_execution_ref
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
            decision_method="manual",
            payload={"execution_kind": execution_kind, "execution_ref": ref},
            now=moment,
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _proposal_doc(conn, system_id, row, now=moment)


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
    """Record what an execution produced. Never adopts, promotes or applies.

    The result is deliberately a ledger event and not a table of its own: the
    numbers belong to the execution's canonical row, and duplicating them
    here would create a second copy that can disagree with it (#405).
    """
    if not _text(summary):
        raise FlowExperimentValidationError("summary must not be empty")
    moment = time.time() if now is None else now
    row = _proposal_row(conn, system_id, proposal_id)
    _lifecycle_gate(conn, system_id, row, "record_result", moment)
    payload: Dict[str, Any] = {"summary": summary, "metrics": _validate_metrics(metrics)}
    if execution_kind is not None:
        _check_membership(execution_kind, EXECUTION_KINDS, "execution_kind")
        payload["execution_kind"] = execution_kind
        payload["execution_ref"] = _text(execution_ref)
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
    actor_kind: str = "user",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Record a promotion CANDIDATE -- which is not a promotion (§7.6).

    Nothing here adopts an Experiment variant, moves a Node's maturity,
    creates a publish job, or writes a line of source. The actual promotion
    still goes through the existing Experiment adoption / Stabilization
    (#399) / publish (#216) human gates, each of which keeps its own record.
    """
    if not _text(candidate_ref):
        raise FlowExperimentValidationError("candidate_ref must not be empty")
    if not _text(rationale):
        raise FlowExperimentValidationError("rationale must not be empty")
    moment = time.time() if now is None else now
    row = _proposal_row(conn, system_id, proposal_id)
    _lifecycle_gate(conn, system_id, row, "record_promotion_candidate", moment)
    _insert_event(
        conn,
        system_id=system_id,
        proposal_id=proposal_id,
        event_kind="promotion_candidate_recorded",
        actor=actor,
        actor_kind=actor_kind,
        reason=rationale,
        decision_method="manual",
        payload={"candidate_ref": candidate_ref, "promotion_performed": False},
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
  "evidence_refs": array of strings, at least one, drawn from the facts above
  "risks": array of strings

Node-level and Flow/Capability-level metrics are separate contracts: never
derive one from the other, and never return a combined score.
"""

_DRAFT_REQUIRED_KEYS: Tuple[str, ...] = (
    "title", "purpose", "hypothesis", "comparison_scope", "target_node_keys",
    "baseline_ref", "candidate_refs", "evaluation_axes", "quality_floor",
    "isolation_strategy", "isolation_detail", "cost_cap", "stop_conditions",
    "rollback_plan", "evidence_refs",
)


def _parse_draft_response(raw: str, allowed_node_keys: Sequence[str]) -> Dict[str, Any]:
    """Validate the structured output. ANY failure fails the whole run.

    Principle 6: there is no partial acceptance and no heuristic repair. A
    draft missing its stop conditions is not a draft with fewer fields -- it
    is a plan that cannot be reviewed, and inventing the missing part here
    would make the model's omission invisible.
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
    return payload


def propose_flow_experiment(
    *,
    system_id: int,
    flow_subject_kind: str,
    flow_subject_ref: str,
    node_keys: Sequence[str],
    goal: str,
    now: Optional[float] = None,
) -> DraftResult:
    """Draft a Flow experiment with the experiment reasoning model (§7.7).

    **Reachable only in `propose` / `shadow`.** The client is built through
    `execution_mode.build_experiment_llm_adapter`, whose body order is the
    contract: the capability gate runs before the first line that could read
    a credential, so in `fixed` / `observe` this function raises
    `ExecutionModeDenied` without a credential ever being read (EM-ADR-3).
    Every named Node is gated, not just the first: drafting an experiment
    that spans a Node nobody permitted is still spending the reasoning budget
    on that Node.

    **This creates NOTHING but an audit row.** No proposal, no `proposed`
    event, no queue entry. The draft is `decision_method: reasoning_llm` and
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

        flow_ref = flow_subject_ref if flow_subject_kind == "runtime_flow" else None
        for key in keys:
            execution_mode.require_capability(
                conn,
                system_id=system_id,
                capability="llm_experiment_proposal",
                node_key=key,
                flow_ref=flow_ref,
                now=moment,
            )
        client, decision = execution_mode.build_experiment_llm_adapter(
            conn,
            system_id=system_id,
            node_key=keys[0],
            flow_ref=flow_ref,
            purpose="flow_experiment_draft",
            now=moment,
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
        draft = _parse_draft_response(raw, keys)
    except (
        FlowExperimentReasoningError,
        llm.LLMError,
        llm.LLMResourceLimitError,
        json.JSONDecodeError,
    ) as exc:
        error = f"{type(exc).__name__}: {exc}"
    completed_at = time.time()

    # --- Phase 3: persist the audit row (connection reopened) -------------
    run_status = "completed" if error is None else "failed"
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
