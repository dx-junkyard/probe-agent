"""Joint Understanding contract (Epic #328 Phase A / Issue #329).

A **Joint Understanding session** is what 「わからない」 starts instead of
ending. Where the existing Inquiry flow (#285/#286) answers *one doubt* and
then hands the same question back to the developer, a Joint Understanding
session is the shared workspace in which investigation findings, translated
explanations, and the developer's own judgements accumulate side by side --
each kept in its own provenance -- until the developer picks one of a finite
set of next actions and (eventually) closes the session with an explicitly
typed outcome.

This module owns the deterministic part of that contract and nothing else:

- the finite vocabularies (Principle 6 -- every one of them is a small,
  explicitly enumerated set; an unknown value is rejected, never guessed),
- the session status transition table,
- the per-role rules that keep investigation / translation / developer from
  authoring each other's content,
- the referential rules for the append-only finding chain.

It is deliberately DB-free and LLM-free. Phase A persists and serves the
contract; Phase B (#330, iterative investigation) and Phase C (#331,
translation) are the two producers that will fill it, and Phase D (#332)
adds the reflux into System Understanding. Keeping the rules here means both
producers validate against exactly one implementation instead of each
re-deriving "may a translation cite a file?".

probe-agent:
  role: deterministic contract and finite vocabularies for Joint
    Understanding sessions, findings, and developer actions
  capability: interactive-system-understanding
  element_type: core
  operation_kind: pure
  state_effects: []
  probe_value: Verify the role/claim contract rejects a translation finding that invents evidence, a developer finding carrying a reasoning run, and an investigation hypothesis without competing explanations or refutation conditions.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Set

from .joint_premise import (
    ASSERTABLE_PREMISE_STATES,
    LEGACY_PREMISE_STATES,
    OUTCOMES_REQUIRING_BASIS as _OUTCOMES_REQUIRING_BASIS,
    PREMISE_STATES as _PREMISE_STATES,
    BasisFinding,
    validate_basis,
)

SCHEMA_VERSION = "joint-understanding-v1"

# --- Finite vocabularies (Principle 6) ---------------------------------------

# Which confirmation item the developer could not answer. 'inquiry' is
# included because a doubt conversation that ends 'unresolved' is one of the
# most common ways a developer discovers they do not know the answer either.
ORIGIN_KINDS = ("qa", "intent", "review_item", "inquiry", "purpose_need")

# How the session started. Deliberately only two values: whether the
# developer said 「わからない」 or asked to work it out together changes what
# the UI shows, but never what the server is allowed to write.
TRIGGERS = ("unknown_answer", "explicit_request")

SESSION_STATUSES = ("open", "held", "closed")

# Finite transition table; a pair not listed here is a 409 (never a silent
# no-op). 'closed' is terminal -- a session that has recorded an outcome is
# history, and continuing the conversation means opening a new session
# rather than mutating a decided one.
SESSION_TRANSITIONS: Dict[str, Set[str]] = {
    "open": {"held", "closed"},
    "held": {"open"},
    "closed": set(),
}

# The four meanings Epic #328 requires be kept apart, plus the two ways a
# session can end without a conclusion. These are NOT interchangeable:
#   understood          -- the developer now understands the situation
#   doubt_resolved      -- the specific doubt is gone (says nothing about the
#                          original item's answer)
#   hypothesis_adopted  -- a hypothesis is being used PROVISIONALLY; it is
#                          not a fact and stays subject to re-verification
#   decided             -- the developer made the final value judgement
#   handed_off          -- the decision belongs to someone else
#   abandoned           -- closed without a conclusion
SESSION_OUTCOMES = (
    "understood",
    "doubt_resolved",
    "hypothesis_adopted",
    "decided",
    "handed_off",
    "abandoned",
)

# Outcomes that must never be presented or reused as established fact.
PROVISIONAL_OUTCOMES = ("hypothesis_adopted",)

CLAIM_KINDS = ("fact", "inference", "hypothesis", "unknown", "conflict")

ORIGIN_ROLES = ("investigation", "translation", "developer")

# The next understanding actions offered to the developer. Recording one is
# an intent to continue the conversation -- never an approval, adoption, or
# decision of the origin item (that stays with the item's own endpoint).
ACTION_KINDS = (
    "request_investigation",
    "explain_reasoning",
    "compare_options",
    "adopt_hypothesis",
    "revise_intent",
    "hold",
    "handoff",
    "decide",
)

# Which existing formal operation each action leads to (Issue #336).
#
# Recording an action is a record of intent. It is NOT the operation, and the
# distinction had no machine-readable form before: the developer chose
# 「意図を修正する」 and nothing connected that to `POST
# /interview/intent/{id}/correct`, so the conversation could accumulate actions
# that never became anything. This table is the deterministic server catalog of
# where each action actually goes.
#
# Two of them complete inside this feature (`joint_investigate`,
# `joint_translate`); `joint_hold` and the two close variants are this
# session's own lifecycle. The rest name an endpoint OUTSIDE it, and those are
# the ones that must never be treated as done because an action row exists.
FORMAL_OPERATIONS = (
    "joint_investigate",
    "joint_translate",
    "joint_hold",
    "joint_close_provisional",
    "joint_close_decided",
    "intent_correct",
    "question_handoff_create",
    "origin_item_answer",
)

ACTION_FORMAL_OPERATIONS: Dict[str, str] = {
    "request_investigation": "joint_investigate",
    "explain_reasoning": "joint_translate",
    "compare_options": "joint_translate",
    "adopt_hypothesis": "joint_close_provisional",
    "revise_intent": "intent_correct",
    "hold": "joint_hold",
    "handoff": "question_handoff_create",
    "decide": "joint_close_decided",
}

# Operations performed by an endpoint OUTSIDE this feature. Recording the
# action opens them; only their own endpoint completes them, and the completion
# is that endpoint's `decision_method: manual` record -- never this one.
EXTERNAL_FORMAL_OPERATIONS = (
    "intent_correct",
    "question_handoff_create",
    "origin_item_answer",
)

DECISION_METHODS = ("deterministic", "reasoning_llm", "manual")

# Per-role decision methods. This is the mechanical part of "a role never
# authors another role's content": a developer record is always `manual` and
# never carries a reasoning run, and a translation is always a reasoning
# call (it exists precisely to reword a model-produced finding).
ROLE_DECISION_METHODS: Dict[str, "tuple[str, ...]"] = {
    "investigation": ("reasoning_llm", "deterministic"),
    "translation": ("reasoning_llm",),
    "developer": ("manual",),
}

# Only investigation findings may carry snapshot/runtime citations: a
# translation must reference other findings instead of inventing citations,
# and a developer statement is a judgement, not snapshot evidence.
EVIDENCE_BEARING_ROLES = ("investigation",)

RUNTIME_CHECK_VALUES = ("match", "mismatch", "unobserved", "stale")


class JointUnderstandingValidationError(ValueError):
    """A contract violation. Routes translate this to HTTP 422."""


def outcome_is_provisional(outcome: Optional[str]) -> bool:
    """True exactly for outcomes that must not be treated as fact."""
    return outcome in PROVISIONAL_OUTCOMES


def validate_transition(current: str, target: str) -> None:
    """Raise unless ``current -> target`` is in the finite transition table."""
    if target not in SESSION_TRANSITIONS.get(current, set()):
        raise JointUnderstandingValidationError(
            f"Cannot transition Joint Understanding session from '{current}' to '{target}'"
        )


def _require_member(value: str, allowed: Sequence[str], field: str) -> None:
    if value not in allowed:
        raise JointUnderstandingValidationError(
            f"{field} must be one of {list(allowed)}, got {value!r}"
        )


def validate_finding(
    *,
    origin_role: str,
    claim_kind: str,
    decision_method: str,
    evidence: Sequence[object],
    runtime_evidence: Sequence[object],
    supports_finding_ids: Sequence[int],
    competing_explanations: Sequence[str],
    refutation_conditions: Sequence[str],
    next_investigation: Optional[str],
    intelligence_run_id: Optional[int],
    is_mock: bool,
    known_finding_ids: Iterable[int],
    supersedes_finding_id: Optional[int] = None,
) -> None:
    """Validate one appended finding against the Phase A contract.

    ``known_finding_ids`` is the set of finding ids already in the SAME
    session: every ``supports_finding_ids`` entry and ``supersedes_finding_id``
    must resolve there, so a translation can never claim support from a
    finding in another session (or from one that does not exist), and a
    correction chain can never point outside its own conversation.

    Raises ``JointUnderstandingValidationError`` on the first violation; the
    caller persists nothing. There is no lenient mode -- a finding that does
    not satisfy its role's rules is not stored in a weaker form.
    """
    _require_member(origin_role, ORIGIN_ROLES, "origin_role")
    _require_member(claim_kind, CLAIM_KINDS, "claim_kind")
    _require_member(decision_method, DECISION_METHODS, "decision_method")

    allowed_methods = ROLE_DECISION_METHODS[origin_role]
    if decision_method not in allowed_methods:
        raise JointUnderstandingValidationError(
            f"origin_role={origin_role!r} requires decision_method in "
            f"{list(allowed_methods)}, got {decision_method!r}"
        )

    if origin_role == "developer":
        # The human's own record: never produced by a reasoning run, so it
        # can never be a model speaking in the developer's name.
        if intelligence_run_id is not None:
            raise JointUnderstandingValidationError(
                "A developer finding must not reference an intelligence run"
            )
        if is_mock:
            raise JointUnderstandingValidationError(
                "A developer finding must not be marked as mock output"
            )
    elif intelligence_run_id is None and decision_method == "reasoning_llm":
        raise JointUnderstandingValidationError(
            "A reasoning_llm finding must reference its intelligence run"
        )

    if origin_role not in EVIDENCE_BEARING_ROLES and (evidence or runtime_evidence):
        raise JointUnderstandingValidationError(
            f"origin_role={origin_role!r} must not carry evidence; reference "
            "an investigation finding via supports_finding_ids instead"
        )

    if origin_role == "translation" and not supports_finding_ids:
        raise JointUnderstandingValidationError(
            "A translation finding must reference at least one finding it is derived from"
        )

    if origin_role == "investigation" and claim_kind == "hypothesis":
        # A hypothesis is not merely a low-confidence claim (see
        # docs/system-understanding-ideal-state.md 3.4).
        if not competing_explanations:
            raise JointUnderstandingValidationError(
                "An investigation hypothesis must list at least one competing explanation"
            )
        if not refutation_conditions:
            raise JointUnderstandingValidationError(
                "An investigation hypothesis must list at least one refutation condition"
            )
        if not next_investigation:
            raise JointUnderstandingValidationError(
                "An investigation hypothesis must name the next investigation"
            )

    if origin_role == "investigation" and claim_kind != "unknown":
        if not evidence and not runtime_evidence:
            raise JointUnderstandingValidationError(
                "An investigation assertion must carry snapshot or runtime evidence"
            )

    known = set(known_finding_ids)
    unknown_refs = [fid for fid in supports_finding_ids if fid not in known]
    if unknown_refs:
        raise JointUnderstandingValidationError(
            f"supports_finding_ids reference findings outside this session: {unknown_refs}"
        )
    if supersedes_finding_id is not None and supersedes_finding_id not in known:
        raise JointUnderstandingValidationError(
            "supersedes_finding_id must reference a finding of this session"
        )


# --- Reflux and outcome basis (Phase D / #332) --------------------------------

# Where a system-verified fact may be attached. Both are EXISTING structures
# (Epic #328 forbids a third understanding model):
#   qa_investigation -- interview_qa.investigation_json, the same non-answer
#                       slot Issue #286's route-and-investigate uses
#   session_ledger   -- the reflux table itself, for origins whose built rows
#                       are owned by the Alignment/Understanding rebuild
REFLUX_TARGET_KINDS = ("qa_investigation", "session_ledger")

# Only a system-established fact refluxes. An inference/hypothesis/unknown/
# conflict stays inside the conversation: promoting those would be exactly
# the "confidence alone turns a hypothesis into a fact" move the design
# principles forbid.
REFLUXABLE_CLAIM_KINDS = ("fact",)

# Outcomes that assert something forward-looking and therefore need both a
# named basis and a still-valid premise. Issue #337 moved the definition of
# what a VALID basis IS into app/joint_premise.py -- it now also rejects a
# superseded, mock, or non-hypothesis basis -- while the set itself stays
# re-exported here so the contract remains readable in one place.
OUTCOMES_REQUIRING_BASIS = _OUTCOMES_REQUIRING_BASIS

# Finite premise verdicts (Issue #337): current | stale | missing | invalid.
# 'fresh' is the pre-#337 value and is read-only; it never appears in a new
# evaluation. The two added states are the two cases the old pair had to lie
# about -- a premise that disappeared, and a session that never captured one.
PREMISE_STATES = _PREMISE_STATES


def can_reflux(origin_role: str, claim_kind: str) -> bool:
    """Whether a finding may be attached to the understanding surface."""
    return origin_role == "investigation" and claim_kind in REFLUXABLE_CLAIM_KINDS


def reflux_target_kind(origin_kind: str) -> str:
    """The existing structure a session's facts attach to (deterministic)."""
    return "qa_investigation" if origin_kind == "qa" else "session_ledger"


# Why an asserting outcome was refused on premise grounds, one message per
# finite premise verdict. Separate messages because the recovery differs: a
# stale premise can be re-investigated, a missing one cannot be recovered at
# all, and an uncaptured one was never comparable in the first place.
_PREMISE_REFUSAL_MESSAGES = {
    "stale": (
        "the premise this session was investigated against has changed; "
        "re-investigate first"
    ),
    "missing": (
        "the premise this session was investigated against no longer exists; "
        "open a new session against the current state"
    ),
    "invalid": (
        "this session has no comparable premise (it predates premise tracking, "
        "or was opened without a pinned snapshot); open a new session"
    ),
}


def validate_outcome_basis(
    *,
    outcome: str,
    outcome_finding_ids: Sequence[int],
    known_finding_ids: Iterable[int],
    premise_state: str,
    basis_findings: Optional[Dict[int, BasisFinding]] = None,
) -> None:
    """Guard the two outcomes that assert something beyond "we talked".

    ``hypothesis_adopted`` and ``decided`` must name the findings they rest
    on. Issue #337 tightened what qualifies, fail-closed on all four counts:

    - the premise must be ``current``. ``stale`` was already refused;
      ``missing`` and ``invalid`` are now refused too, where the pre-#337 code
      reported both as ``fresh`` and let them through.
    - a basis finding must belong to this session (System isolation follows,
      since findings are session-scoped and sessions are System-scoped),
    - it must not be superseded (a claim the investigation itself corrected)
      and must not be mock output,
    - for ``hypothesis_adopted`` it must actually BE a current investigation
      hypothesis with a verified run and evidence -- otherwise the provisional
      marker says nothing about what was adopted.

    ``basis_findings`` is the caller's map of this session's findings. It is
    optional only so the pre-#337 id-only check still works for callers that
    have nothing more to offer; when omitted the last three rules cannot be
    applied and the caller is trusting a weaker contract.
    """
    _require_member(outcome, SESSION_OUTCOMES, "outcome")
    if premise_state not in PREMISE_STATES and premise_state not in LEGACY_PREMISE_STATES:
        raise JointUnderstandingValidationError(
            f"premise_state must be one of {list(PREMISE_STATES)}, got {premise_state!r}"
        )
    if outcome not in OUTCOMES_REQUIRING_BASIS:
        return
    if premise_state not in ASSERTABLE_PREMISE_STATES:
        detail = _PREMISE_REFUSAL_MESSAGES.get(
            premise_state, "the premise this session relied on is not current"
        )
        raise JointUnderstandingValidationError(
            f"Cannot record outcome '{outcome}': {detail}"
        )
    if not outcome_finding_ids:
        raise JointUnderstandingValidationError(
            f"Outcome '{outcome}' must name the findings it rests on"
        )
    known = set(known_finding_ids)
    unknown = [fid for fid in outcome_finding_ids if fid not in known]
    if unknown:
        raise JointUnderstandingValidationError(
            f"outcome_finding_ids reference findings outside this session: {unknown}"
        )
    if basis_findings is None:
        return
    _code, message = validate_basis(
        outcome=outcome,
        outcome_finding_ids=outcome_finding_ids,
        findings_by_id=basis_findings,
    )
    if message:
        raise JointUnderstandingValidationError(message)


def formal_operation_for(action_kind: str) -> str:
    """The existing formal operation ``action_kind`` leads to (deterministic)."""
    _require_member(action_kind, ACTION_KINDS, "action_kind")
    return ACTION_FORMAL_OPERATIONS[action_kind]


def completes_outside_session(action_kind: str) -> bool:
    """Whether the action's operation is performed by an endpoint elsewhere.

    True means the action record is an intent to go there, and the operation is
    finished only by that endpoint's own manual record -- reading the action row
    as completion would be exactly the "AI decided it" conflation Epic #328
    forbids.
    """
    return formal_operation_for(action_kind) in EXTERNAL_FORMAL_OPERATIONS


def available_action_kinds(status: str) -> List[str]:
    """The finite action menu for a session in ``status``.

    Deterministic and order-stable so the dashboard (Phase E) and any test
    see the same menu for the same state. A closed session offers nothing:
    its outcome is already recorded.
    """
    if status != "open":
        return []
    return list(ACTION_KINDS)
