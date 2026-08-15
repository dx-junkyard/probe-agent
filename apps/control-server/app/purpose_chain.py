"""Purpose Chain: canonical Purpose Frame contract + lineage (Issue #388).

`docs/purpose-chain.md` is the design contract this module implements; §0 and
§1 are its specification. probe-agent can already show Vision and System
Purpose as "two sentences side by side", but nothing connects them into a
causal, auditable chain the developer can trust. This module is that chain:

```
beneficiary_problem --problem_to_change--> desired_change
                                                  --change_to_intervention--> intervention
                                                                                  --intervention_to_capability--> core_capability (N)
```

Two rules this module must never violate (§0):

* **No new understanding model.** `desired_change` IS
  `understanding_brief.BriefResult.vision`; `intervention` IS its
  `system_purpose` claims; `core_capability` elements ARE its
  `core_capabilities` claims. `beneficiary_problem` is the Intent Brief `pain`
  field, read the same way the Brief already reads `goal` for Vision. This
  module adds RELATION and LINEAGE on top of those rows; it never reclassifies
  a claim's 確認状態/出所 -- every element reuses
  `understanding_brief.CONFIRMATION_LABELS` / `PROVENANCE_LABELS` and the
  `UnderstandingConfirmationState` / `UnderstandingProvenanceKind`
  vocabularies verbatim.
* **Deterministic only. No LLM call anywhere in this module** (Principle 6).
  `pain`'s free text is never decomposed into "who" + "what" -- that would be
  free-text interpretation, and the developer's own correction is the only
  sanctioned way to add that structure (§1.2).

Two axes this module deliberately keeps apart, both inherited from
`understanding_brief`:

* An element's `state` (`present` / `unknown` / `unavailable`) is about
  whether the SOURCE ROW could be read and has content -- never conflated
  with `confirmation` (whether a human settled it) the way #380 already
  separated `unavailable` from `not_built`.
* A relation's `status` (§1.3) and its `recheck_state` (§1.3) are independent:
  a stale CONFIRMED decision reads as `status="hypothesis"` (it can no longer
  be trusted as confirmed) while `recheck_state="stale"` explains *why*. The
  decision row itself is never deleted or overwritten (§1.5) -- staleness is
  something read, not something that erases the audit trail.

Design decisions the spec text leaves open, made explicitly here (flagged for
lead review, see the implementation report):

1. **"主要 relation" (§1.4) for resolution level** is the element's OWN
   relation in the direction that explains it structurally:
   `beneficiary_problem` / `desired_change` use their OUTGOING relation
   (`problem_to_change` / `change_to_intervention` respectively, where they
   are the SOURCE); a `core_capability` element uses its OWN
   `intervention_to_capability` relation (unambiguous, one relation per
   capability, where it is the TARGET). The `intervention` frame slot can
   have zero-or-many outgoing `intervention_to_capability` relations, so its
   primary status is the WORST (least resolved) among them, or `None` when
   there are none yet -- which the L1/L2 rule already treats correctly
   (`None` is "not unknown/unavailable" so L1 still holds, but never equals
   `"confirmed"` so L2 requires at least one confirmed downstream relation).
2. **Staleness propagates via priority, not raw symmetric comparison.**
   Reading §1.3's example propagation table closely (`docs/purpose-chain.md`
   §1.3): when `intervention` changes, `change_to_intervention` (its
   incoming relation) reports `target_changed` directly, but
   `intervention_to_capability` -- whose SOURCE is also literally
   `intervention`, and so would ALSO detect a direct digest mismatch --
   reports `upstream_changed`, not `source_changed`. The only reading that
   reproduces every row of that table is: **check the immediate upstream
   relation's `recheck_state` FIRST; only when it is `current` does a
   relation fall back to comparing its OWN captured digests.** This is
   `_recheck_relation` below. `tests/test_purpose_chain.py` pins this
   ordering with a two-hop propagation case.
3. **Relation `provenance` defaults to the "weaker" of its two endpoints'
   provenance** (§1.3), ranked by `_PROVENANCE_RANK` (rank 0 = strongest).
   That ranking is written out explicitly rather than read off
   `get_args(UnderstandingProvenanceKind)`'s declaration order: the two agree
   today, which is precisely why deriving it is unsafe -- reordering the
   Literal is a cosmetic edit everywhere else in this codebase, and it would
   silently relabel every relation's 出所. See `_PROVENANCE_RANK`'s own
   comment, and the import-time assert that catches a vocabulary addition
   with no rank.
4. `provenance_label` is added to `PurposeRelationOut` alongside `provenance`
   even though the spec's §1.3 field list only names `provenance` -- every
   other finite vocabulary surfaced to the Dashboard in this codebase pairs a
   code with a server-authored Japanese label (CLAUDE.md's UI language rule),
   and `status_label` is already explicitly required two lines above it.

probe-agent:
  role: Deterministic Purpose Frame / Purpose Chain projection and relation decision lineage
  capability: interactive-system-understanding
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify every element is derived from an existing persisted row with no free-text decomposition, that an AI-authored relation never becomes confirmed without an explicit manual decision, that staleness propagates downstream only, and that a relation/capability derivation failure never blanks the frame.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, get_args

from . import interview_workflow, understanding_brief
from .models import (
    PurposeElementKind,
    PurposeElementState,
    PurposeFrameState,
    PurposeRecheckState,
    PurposeRelationKind,
    PurposeRelationStatus,
    PurposeResolutionLevel,
    PurposeSourceKind,
    PurposeStaleReason,
    UnderstandingProvenanceKind,
)
from .understanding_brief import CONFIRMATION_LABELS, PROVENANCE_LABELS

__all__ = [
    "ELEMENT_KINDS",
    "ELEMENT_STATES",
    "RELATION_KINDS",
    "RELATION_STATUSES",
    "RECHECK_STATES",
    "STALE_REASONS",
    "RESOLUTION_LEVELS",
    "SOURCE_KINDS",
    "FRAME_STATES",
    "RELATION_STATUS_LABELS",
    "PurposeElement",
    "PurposeRelation",
    "PurposeChainResult",
    "element_digest",
    "relation_id",
    "derive_purpose_chain",
    "record_relation_decision",
]


# --- §0. Finite vocabularies -------------------------------------------------
#
# Each vocabulary is a `Literal` defined exactly once in `app/models.py` and
# mirrored here with `get_args`, the same discipline `understanding_brief`
# and `overview_projection` already use.

ELEMENT_KINDS: Tuple[str, ...] = get_args(PurposeElementKind)
ELEMENT_STATES: Tuple[str, ...] = get_args(PurposeElementState)
RELATION_KINDS: Tuple[str, ...] = get_args(PurposeRelationKind)
RELATION_STATUSES: Tuple[str, ...] = get_args(PurposeRelationStatus)
RECHECK_STATES: Tuple[str, ...] = get_args(PurposeRecheckState)
STALE_REASONS: Tuple[str, ...] = get_args(PurposeStaleReason)
RESOLUTION_LEVELS: Tuple[str, ...] = get_args(PurposeResolutionLevel)
SOURCE_KINDS: Tuple[str, ...] = get_args(PurposeSourceKind)
FRAME_STATES: Tuple[str, ...] = get_args(PurposeFrameState)

RELATION_STATUS_LABELS: Dict[str, str] = {
    "confirmed": "確認済み",
    "hypothesis": "仮説",
    "conflicting": "矛盾あり",
    "unknown": "不明・情報不足",
    "unavailable": "取得できません",
}

#: Strength ranking for `UnderstandingProvenanceKind`, strongest first (rank
#: 0) -- see design decision 3 in the module docstring.
#:
#: Written out rather than derived from the Literal's declaration order. The
#: two happen to agree today, and that is exactly the problem: reordering the
#: Literal (a purely cosmetic edit anywhere else in this codebase) would then
#: silently relabel every relation's 出所 -- a claim the developer stated
#: could start reading as the AI's guess, which is the confusion #380/#387
#: exist to prevent. The ranking is a claim about EVIDENTIAL STRENGTH and has
#: to be stated as one. `_PROVENANCE_COVERAGE` below fails the import if a
#: value is ever added to the vocabulary without a rank.
_PROVENANCE_RANK: Dict[str, int] = {
    "developer_intent": 0,
    "implementation_fact": 1,
    "runtime_observation": 2,
    "ai_hypothesis": 3,
}

_PROVENANCE_COVERAGE = set(get_args(UnderstandingProvenanceKind)) - set(_PROVENANCE_RANK)
assert not _PROVENANCE_COVERAGE, (
    "UnderstandingProvenanceKind gained a value with no relation-provenance "
    f"strength rank: {sorted(_PROVENANCE_COVERAGE)}"
)

#: Relation status ranking, worst (least resolved) first. Used only to pick
#: the WORST of several `intervention_to_capability` relations when deciding
#: the `intervention` frame slot's own resolution level (design decision 1).
_RELATION_STATUS_RANK: Dict[str, int] = {
    "unavailable": 0,
    "unknown": 1,
    "conflicting": 2,
    "hypothesis": 3,
    "confirmed": 4,
}

_RESOLUTION_RANK: Dict[str, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}

#: `intervention`'s missing-information sentence when no `system_purpose`
#: claim exists at all. Fixed text (Principle 6), worded like
#: `understanding_brief.READINESS_DESCRIPTIONS["purpose_missing"]`-style
#: copy, never model output.
_INTERVENTION_MISSING_INFORMATION = ["システムがどのように介入するかをまだ抽出できていません。"]

#: `beneficiary_problem`'s missing-information sentence, reusing the exact
#: wording `understanding_brief` already uses for the same Intent Brief field
#: (Vision's own missing-information list) -- one sentence, one place it is
#: written.
_BENEFICIARY_PROBLEM_MISSING_INFORMATION = [
    understanding_brief.VISION_MISSING_INFORMATION["pain"]
]

#: `claim.kind` (from `understanding_brief.ClaimFacts`/`BriefClaim`, singular:
#: `vision` / `system_purpose` / `core_capability`) -> the `current_understanding`
#: SECTION key (plural for capabilities) used in `source_ids`, e.g.
#: `"claim:core_capabilities:名前"` (§1.2 example). Derived from
#: `understanding_brief.BRIEF_SECTIONS` rather than hand-duplicated, so the
#: two can never drift.
_SECTION_BY_CLAIM_KIND: Dict[str, str] = {
    kind: section for section, kind in understanding_brief.BRIEF_SECTIONS
}


# --- §1. Data shapes ----------------------------------------------------------


@dataclass
class PurposeElement:
    id: str
    kind: str
    state: str
    display_statement: str = ""
    statement: str = ""
    confirmation: str = "unknown"
    confirmation_label: str = ""
    provenance: str = "ai_hypothesis"
    provenance_label: str = ""
    resolution_level: str = "L0"
    source_kind: str = "none"
    source_ids: List[str] = field(default_factory=list)
    intent_revision_id: Optional[int] = None
    understanding_revision_id: Optional[int] = None
    snapshot_id: Optional[int] = None
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    evidence_stale: bool = False
    missing_information: List[str] = field(default_factory=list)
    is_mock: bool = False


@dataclass
class PurposeRelation:
    id: str
    kind: str
    source_id: str
    target_id: str
    status: str
    status_label: str
    recheck_state: str
    stale_reason: Optional[str]
    provenance: str
    provenance_label: str
    decision_id: Optional[int] = None
    decided_at: Optional[float] = None
    decided_by: Optional[str] = None
    rationale: str = ""
    evidence: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PurposeChainResult:
    system_id: int
    session_id: Optional[int]
    generated_at: float
    frame: Dict[str, Optional[PurposeElement]]
    elements: List[PurposeElement]
    relations: List[PurposeRelation]
    frame_resolution_level: str
    frame_state: str
    snapshot_id: Optional[int]
    understanding_revision_id: Optional[int]
    understanding_confirmed_at: Optional[float]
    degraded_sections: List[str] = field(default_factory=list)
    degraded_detail: Dict[str, str] = field(default_factory=dict)


# --- §2. Stable identity -------------------------------------------------------


def _hashed_element_id(kind: str, name: str) -> str:
    """`kind + ":" + sha256(name)[:16]` for a kind that can repeat (§1.2)."""
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{digest}"


def relation_id(kind: str, source_id: str, target_id: str) -> str:
    """`f"{kind}:{source_id}->{target_id}"` (§1.3) -- stable because both
    endpoint ids are themselves stable (never a row id)."""
    return f"{kind}:{source_id}->{target_id}"


def element_digest(element: PurposeElement) -> str:
    """SHA-256 over the element's MEANING-bearing fields (§1.3).

    `statement` + `confirmation` + `provenance` + `source_ids` -- the same
    idea as `understanding_brief.claim_digest`, applied to a Purpose Chain
    element: a relation's captured decision digest changes exactly when the
    developer would read the element differently, never when an unrelated
    field (e.g. `resolution_level`, itself DERIVED from relations) moves.
    """
    canonical = json.dumps(
        {
            "statement": element.statement,
            "confirmation": element.confirmation,
            "provenance": element.provenance,
            "source_ids": sorted(element.source_ids),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- §3. Element derivation -----------------------------------------------------


def _claim_display_statement(claim: Any) -> str:
    return claim.summary if claim.summary else claim.name


def _claim_statement(claim: Any) -> str:
    if claim.summary:
        return f"{claim.name}\n{claim.summary}"
    return claim.name


def _intent_status_axes(item: Dict[str, Any]) -> Tuple[str, str]:
    """確認状態/出所 for an element read directly from an Intent Brief item.

    Mirrors `understanding_brief._resolve_vision`'s own status->axis mapping
    exactly, so `goal` (desired_change, via the Brief) and `pain`
    (beneficiary_problem, read directly here) classify identically: a
    confirmed item is `confirmed`/`developer_intent`; anything else is
    `ai_hypothesis`/(`developer_intent` if the developer wrote it themselves,
    else `ai_hypothesis`) -- the confirmation state is about SETTLEDNESS, not
    authorship, exactly like #351's confirmation/provenance separation.
    """
    if item.get("status") == "confirmed":
        return "confirmed", "developer_intent"
    developer_authored = item.get("origin") == "user"
    provenance = "developer_intent" if developer_authored else "ai_hypothesis"
    return "ai_hypothesis", provenance


def _unknown_element(
    kind: str, *, missing_information: Sequence[str]
) -> PurposeElement:
    return PurposeElement(
        id=kind,
        kind=kind,
        state="unknown",
        confirmation="unknown",
        confirmation_label=CONFIRMATION_LABELS["unknown"],
        provenance="ai_hypothesis",
        provenance_label=PROVENANCE_LABELS["ai_hypothesis"],
        source_kind="none",
        missing_information=list(missing_information),
    )


def _beneficiary_problem_element(pain_item: Optional[Dict[str, Any]]) -> PurposeElement:
    """§1.2: source is the latest non-superseded Intent Brief `pain` row.

    `pain`'s free text is never decomposed into "who" and "what" -- that is a
    free-text interpretation Principle 6 forbids. A developer who wants that
    structure corrects the item themselves.
    """
    if pain_item is None:
        return _unknown_element(
            "beneficiary_problem",
            missing_information=_BENEFICIARY_PROBLEM_MISSING_INFORMATION,
        )
    confirmation, provenance = _intent_status_axes(pain_item)
    text = str(pain_item.get("value_text") or "")
    return PurposeElement(
        id="beneficiary_problem",
        kind="beneficiary_problem",
        state="present",
        display_statement=text,
        statement=text,
        confirmation=confirmation,
        confirmation_label=CONFIRMATION_LABELS[confirmation],
        provenance=provenance,
        provenance_label=PROVENANCE_LABELS[provenance],
        source_kind="intent_item",
        source_ids=[f"intent_item:{pain_item['id']}"],
        intent_revision_id=pain_item["id"],
        is_mock=bool(pain_item.get("is_mock")),
    )


def _claim_source(
    claim: Any,
    *,
    understanding_revision_id: Optional[int],
    goal_item: Optional[Dict[str, Any]],
) -> Tuple[str, List[str], Optional[int], Optional[int]]:
    """``(source_kind, source_ids, intent_revision_id, understanding_revision_id)``.

    Only a `vision` claim can be Intent-sourced (via
    `understanding_brief._resolve_vision`'s own confirmed-goal-outranks-model
    rule); `system_purpose` / `core_capability` claims are always
    `understanding_claim` -- `understanding_brief.build_claims` never sets
    `developer_authored=True` for them.
    """
    if claim.kind == "vision" and claim.provenance == "developer_intent" and goal_item is not None:
        return "intent_item", [f"intent_item:{goal_item['id']}"], goal_item["id"], None
    section = _SECTION_BY_CLAIM_KIND.get(claim.kind, claim.kind)
    return (
        "understanding_claim",
        [f"claim:{section}:{claim.name}"],
        None,
        understanding_revision_id,
    )


def _claim_element(
    element_kind: str,
    claim: Any,
    *,
    singular: bool,
    understanding_revision_id: Optional[int],
    snapshot_id: Optional[int],
    goal_item: Optional[Dict[str, Any]],
    snapshot_stale: bool,
) -> PurposeElement:
    """One element built from an `understanding_brief.BriefClaim`, reusing its
    already-computed confirmation/provenance verbatim (never reclassified)."""
    element_id = element_kind if singular else _hashed_element_id(element_kind, claim.name)
    source_kind, source_ids, intent_revision_id, u_revision_id = _claim_source(
        claim, understanding_revision_id=understanding_revision_id, goal_item=goal_item
    )
    # §1.2: evidence_stale can only be True for an implementation_fact
    # element -- an Intent-sourced or AI-hypothesis element has no snapshot
    # pin to go stale against.
    evidence_stale = bool(claim.provenance == "implementation_fact" and snapshot_stale)
    return PurposeElement(
        id=element_id,
        kind=element_kind,
        state="present",
        display_statement=_claim_display_statement(claim),
        statement=_claim_statement(claim),
        confirmation=claim.confirmation,
        confirmation_label=claim.confirmation_label,
        provenance=claim.provenance,
        provenance_label=claim.provenance_label,
        source_kind=source_kind,
        source_ids=source_ids,
        intent_revision_id=intent_revision_id,
        understanding_revision_id=u_revision_id,
        snapshot_id=snapshot_id,
        evidence=list(claim.evidence),
        evidence_stale=evidence_stale,
        is_mock=bool(claim.is_mock),
    )


def _desired_change_element(
    brief: Optional[Any],
    goal_item: Optional[Dict[str, Any]],
    *,
    snapshot_id: Optional[int],
    understanding_revision_id: Optional[int],
    snapshot_stale: bool,
) -> PurposeElement:
    """§1.2: `desired_change` IS `BriefResult.vision`, as-is."""
    vision = getattr(brief, "vision", None) if brief is not None else None
    if vision is None:
        missing = (
            list(brief.vision_missing_information)
            if brief is not None
            else [
                understanding_brief.VISION_MISSING_INFORMATION[f]
                for f in understanding_brief.VISION_INTENT_FIELDS
            ]
        )
        return _unknown_element("desired_change", missing_information=missing)
    return _claim_element(
        "desired_change",
        vision,
        singular=True,
        understanding_revision_id=understanding_revision_id,
        snapshot_id=snapshot_id,
        goal_item=goal_item,
        snapshot_stale=snapshot_stale,
    )


def _intervention_elements(
    brief: Optional[Any],
    *,
    snapshot_id: Optional[int],
    understanding_revision_id: Optional[int],
    snapshot_stale: bool,
) -> Tuple[PurposeElement, List[PurposeElement]]:
    """§1.2: the first `system_purpose` claim is the frame slot; the rest
    become additional `intervention` elements (never a second frame slot)."""
    claims = list(getattr(brief, "system_purpose", []) or []) if brief is not None else []
    if not claims:
        return (
            _unknown_element(
                "intervention", missing_information=_INTERVENTION_MISSING_INFORMATION
            ),
            [],
        )
    frame_claim, *rest = claims
    frame_element = _claim_element(
        "intervention",
        frame_claim,
        singular=True,
        understanding_revision_id=understanding_revision_id,
        snapshot_id=snapshot_id,
        goal_item=None,
        snapshot_stale=snapshot_stale,
    )
    extra = [
        _claim_element(
            "intervention",
            claim,
            singular=False,
            understanding_revision_id=understanding_revision_id,
            snapshot_id=snapshot_id,
            goal_item=None,
            snapshot_stale=snapshot_stale,
        )
        for claim in rest
    ]
    return frame_element, extra


def _capability_elements(
    brief: Optional[Any],
    *,
    snapshot_id: Optional[int],
    understanding_revision_id: Optional[int],
    snapshot_stale: bool,
) -> List[PurposeElement]:
    """§1.2: every `core_capabilities` claim, unfiltered -- Capabilities are
    never part of the frame itself, only reachable through `elements`."""
    claims = list(getattr(brief, "core_capabilities", []) or []) if brief is not None else []
    return [
        _claim_element(
            "core_capability",
            claim,
            singular=False,
            understanding_revision_id=understanding_revision_id,
            snapshot_id=snapshot_id,
            goal_item=None,
            snapshot_stale=snapshot_stale,
        )
        for claim in claims
    ]


def _resolution_level(
    element: PurposeElement,
    primary_relation_status: Optional[str],
    *,
    has_full_outcome_criterion: bool = False,
) -> str:
    """§1.4, first match.

    `L3` requires everything `L2` requires PLUS a qualifying Outcome
    Criterion (#391, `app/purpose_verification.py`): the ladder is "今の判断
    に使えるか", so a step of empirical evidence on top of an unconfirmed
    element/relation would rank ABOVE a confirmed-but-unverified one, which
    is backwards. `has_full_outcome_criterion` is computed by
    `derive_purpose_chain` from `purpose_verification.
    full_outcome_criterion_target_ids` -- true exactly when a
    `purpose_outcome_criterion` row targets this element with all four of
    measure/baseline_value/target_value/observation_window filled in.
    """
    if (
        element.state == "present"
        and element.confirmation == "confirmed"
        and primary_relation_status == "confirmed"
        and has_full_outcome_criterion
    ):
        return "L3"
    if (
        element.state == "present"
        and element.confirmation == "confirmed"
        and primary_relation_status == "confirmed"
    ):
        return "L2"
    if element.state == "present" and primary_relation_status not in ("unknown", "unavailable"):
        return "L1"
    return "L0"


def _min_resolution(levels: Sequence[str]) -> str:
    """The 3-slot MIN (§1.4) -- explicitly never a mean or a percentage."""
    return min(levels, key=lambda level: _RESOLUTION_RANK.get(level, 0))


def _worst_relation_status(relations: Sequence[PurposeRelation]) -> Optional[str]:
    if not relations:
        return None
    return min((r.status for r in relations), key=lambda s: _RELATION_STATUS_RANK.get(s, 0))


# --- §4. Relation derivation ----------------------------------------------------


def _current_decision(
    conn: sqlite3.Connection, system_id: int, session_id: Optional[int], rel_id: str
) -> Optional[Dict[str, Any]]:
    """The current (non-superseded) decision for one relation, in one session.

    System- AND session-scoped by construction (§1.7: System 分離), so a
    decision recorded in another System or another session can never surface
    here.
    """
    if session_id is None:
        return None
    row = conn.execute(
        """SELECT * FROM purpose_relation_decision
           WHERE system_id = ? AND session_id = ? AND relation_id = ?
             AND superseded_by_id IS NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, session_id, rel_id),
    ).fetchone()
    return dict(row) if row else None


def _weaker_provenance(a: str, b: str) -> str:
    rank_a = _PROVENANCE_RANK.get(a, len(_PROVENANCE_RANK))
    rank_b = _PROVENANCE_RANK.get(b, len(_PROVENANCE_RANK))
    return a if rank_a >= rank_b else b


def _relation_status(
    source: PurposeElement,
    target: PurposeElement,
    decision: Optional[Dict[str, Any]],
    source_digest: str,
    target_digest: str,
) -> str:
    """§1.3, first match."""
    if source.state == "unavailable" or target.state == "unavailable":
        return "unavailable"
    if source.state == "unknown" or target.state == "unknown":
        return "unknown"
    if (
        source.confirmation == "conflicting"
        or target.confirmation == "conflicting"
        or (decision is not None and decision["decision"] == "rejected")
    ):
        return "conflicting"
    if (
        decision is not None
        and decision["decision"] == "confirmed"
        and decision["source_digest"] == source_digest
        and decision["target_digest"] == target_digest
    ):
        return "confirmed"
    return "hypothesis"


def _recheck_relation(
    source: PurposeElement,
    target: PurposeElement,
    decision: Optional[Dict[str, Any]],
    source_digest: str,
    target_digest: str,
    *,
    upstream_stale: bool,
) -> Tuple[str, Optional[str]]:
    """§1.3 `recheck_state`, in the priority order design decision 2 explains.

    Upstream propagation is checked FIRST: once an upstream relation is
    stale, everything downstream reads `upstream_changed` even when its own
    endpoint digests would ALSO independently mismatch (an element can be
    both the target of a stale upstream relation and the source of this one).
    Only when upstream is `current` does a relation fall back to comparing
    its OWN captured decision digests, and only after that to `evidence_stale`
    (§1.2: only an `implementation_fact` element can set it).

    A relation with no decision at all is never `stale` from a direct
    mismatch -- there is nothing "re" to check -- but CAN still inherit
    `upstream_changed`, because that is a statement about the chain leading
    into it, not about a judgement this relation itself made.
    """
    if upstream_stale:
        return "stale", "upstream_changed"
    if decision is not None:
        source_changed = source_digest != decision["source_digest"]
        target_changed = target_digest != decision["target_digest"]
        if source_changed and target_changed:
            return "stale", "both_changed"
        if source_changed:
            return "stale", "source_changed"
        if target_changed:
            return "stale", "target_changed"
    if source.evidence_stale or target.evidence_stale:
        return "stale", "snapshot_changed"
    return "current", None


def _build_relation(
    conn: sqlite3.Connection,
    system_id: int,
    session_id: Optional[int],
    kind: str,
    source: PurposeElement,
    target: PurposeElement,
    *,
    upstream_stale: bool = False,
) -> PurposeRelation:
    rel_id = relation_id(kind, source.id, target.id)
    decision = _current_decision(conn, system_id, session_id, rel_id)
    source_digest = element_digest(source)
    target_digest = element_digest(target)

    status = _relation_status(source, target, decision, source_digest, target_digest)
    recheck_state, stale_reason = _recheck_relation(
        source, target, decision, source_digest, target_digest, upstream_stale=upstream_stale
    )

    if decision is not None:
        # §1.3: a human decision (confirmed OR rejected) about the relation
        # itself outranks either endpoint's own provenance -- deciding a
        # relation is an act of developer intent, and `developer_decision`
        # is not in `UnderstandingProvenanceKind`'s vocabulary (that value
        # exists only in the Overview's strict-superset finding vocabulary).
        provenance = "developer_intent"
    else:
        provenance = _weaker_provenance(source.provenance, target.provenance)

    return PurposeRelation(
        id=rel_id,
        kind=kind,
        source_id=source.id,
        target_id=target.id,
        status=status,
        status_label=RELATION_STATUS_LABELS[status],
        recheck_state=recheck_state,
        stale_reason=stale_reason,
        provenance=provenance,
        provenance_label=PROVENANCE_LABELS[provenance],
        decision_id=decision["id"] if decision else None,
        decided_at=decision["created_at"] if decision else None,
        decided_by=decision["decided_by"] if decision else None,
        rationale=decision["rationale"] if decision else "",
        # §1.3: evidence carried from the TARGET element, never invented.
        evidence=list(target.evidence),
    )


# --- §5. Reading the persisted facts ---------------------------------------------


def _latest_interview_session_id(conn: sqlite3.Connection, system_id: int) -> Optional[int]:
    """The System's newest Interview session -- the same `ORDER BY id DESC`
    rule the Overview and the Interview screen already auto-select with
    (#380), so all three surfaces describe the same session."""
    row = conn.execute(
        "SELECT id FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    return row["id"] if row else None


def _latest_intent_item(
    conn: sqlite3.Connection, session_id: int, system_id: int, field_name: str
) -> Optional[Dict[str, Any]]:
    """Newest non-superseded, applicable Intent Brief row for ONE field.

    Same predicate `understanding_brief._latest_intent_items` uses; reading a
    single field directly here (rather than importing that private helper)
    keeps this module's only cross-module dependency on `understanding_brief`
    limited to its public, documented surface.
    """
    row = conn.execute(
        """SELECT * FROM interview_intent_item
           WHERE session_id = ? AND system_id = ? AND field = ?
             AND superseded_by_id IS NULL AND status != 'not_applicable'
           ORDER BY id DESC LIMIT 1""",
        (session_id, system_id, field_name),
    ).fetchone()
    return dict(row) if row else None


def _degrade(
    degraded_sections: List[str],
    degraded_detail: Dict[str, str],
    section: str,
    exc: Exception,
    *,
    detail_key: Optional[str] = None,
) -> None:
    """Record one section as degraded without ever substituting a guessed
    value for what it failed to read (§0 invariant 6, the #380 discipline)."""
    if section not in degraded_sections:
        degraded_sections.append(section)
    degraded_detail[detail_key or section] = f"{type(exc).__name__}: {exc}"


def derive_purpose_chain(
    conn: sqlite3.Connection,
    system_id: int,
    session_id: Optional[int] = None,
    *,
    now: Optional[float] = None,
) -> PurposeChainResult:
    """Assemble the whole Purpose Chain for one session (or the System's
    newest, or none).

    `session_id=None` resolves to the System's newest session (§1.6); an
    EXPLICIT `session_id` belonging to another System is treated exactly like
    "no session selected" -- `understanding_brief`'s own rule, reused here so
    the two screens can never disagree about what "unselected" means.

    Three independently guarded sections (§0 invariant 6): `frame` (the three
    Purpose Frame slots plus the Intent Brief reads that inform them),
    `capabilities` (the `core_capability` elements, which share the same
    Brief read as `frame` and so degrade together), and `relations` (which
    depends on `frame`/`capabilities` already having elements to connect, but
    can independently fail even when they succeeded).
    """
    now = time.time() if now is None else now
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    effective_session_id = (
        session_id if session_id is not None else _latest_interview_session_id(conn, system_id)
    )

    gathered = interview_workflow.gather_facts(conn, system_id, effective_session_id)
    resolved_session_id = gathered.session["id"] if gathered.session else None
    snapshot_stale = gathered.snapshot_stale

    brief: Optional[Any] = None
    try:
        brief = understanding_brief.build_understanding_brief(
            conn, system_id, effective_session_id, now=now
        )
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "frame", exc, detail_key="frame.brief")
        _degrade(
            degraded_sections, degraded_detail, "capabilities", exc, detail_key="capabilities.brief"
        )

    goal_item: Optional[Dict[str, Any]] = None
    pain_item: Optional[Dict[str, Any]] = None
    if resolved_session_id is not None:
        try:
            goal_item = _latest_intent_item(conn, resolved_session_id, system_id, "goal")
            pain_item = _latest_intent_item(conn, resolved_session_id, system_id, "pain")
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "frame", exc, detail_key="frame.intent")

    understanding_revision_id = getattr(brief, "revision_id", None)
    snapshot_id = getattr(brief, "snapshot_id", None)

    beneficiary_element: Optional[PurposeElement] = None
    desired_element: Optional[PurposeElement] = None
    intervention_element: Optional[PurposeElement] = None
    additional_intervention: List[PurposeElement] = []
    capability_elements: List[PurposeElement] = []
    try:
        beneficiary_element = _beneficiary_problem_element(pain_item)
        desired_element = _desired_change_element(
            brief,
            goal_item,
            snapshot_id=snapshot_id,
            understanding_revision_id=understanding_revision_id,
            snapshot_stale=snapshot_stale,
        )
        intervention_element, additional_intervention = _intervention_elements(
            brief,
            snapshot_id=snapshot_id,
            understanding_revision_id=understanding_revision_id,
            snapshot_stale=snapshot_stale,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "frame", exc)

    try:
        capability_elements = _capability_elements(
            brief,
            snapshot_id=snapshot_id,
            understanding_revision_id=understanding_revision_id,
            snapshot_stale=snapshot_stale,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "capabilities", exc)

    relations: List[PurposeRelation] = []
    try:
        problem_to_change: Optional[PurposeRelation] = None
        if beneficiary_element is not None and desired_element is not None:
            problem_to_change = _build_relation(
                conn, system_id, resolved_session_id,
                "problem_to_change", beneficiary_element, desired_element,
            )
            relations.append(problem_to_change)

        change_to_intervention: Optional[PurposeRelation] = None
        if desired_element is not None and intervention_element is not None:
            change_to_intervention = _build_relation(
                conn, system_id, resolved_session_id,
                "change_to_intervention", desired_element, intervention_element,
                upstream_stale=bool(
                    problem_to_change is not None and problem_to_change.recheck_state == "stale"
                ),
            )
            relations.append(change_to_intervention)

        if intervention_element is not None:
            for capability in capability_elements:
                relations.append(
                    _build_relation(
                        conn, system_id, resolved_session_id,
                        "intervention_to_capability", intervention_element, capability,
                        upstream_stale=bool(
                            change_to_intervention is not None
                            and change_to_intervention.recheck_state == "stale"
                        ),
                    )
                )
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "relations", exc)
        relations = []

    # --- resolution levels (design decision 1) ---
    #
    # `full_outcome_element_ids` is its own tiny guarded read (#391): a
    # failure here must never BLOCK the rest of the frame, and must never
    # elevate anything past `L2` by accident -- an empty set is exactly the
    # fail-closed default `L3` already requires, so no separate
    # `degraded_sections` entry is warranted the way the frame/relations/
    # capabilities sections need one (§0 invariant 6 protects against a
    # DOWNGRADE reading as "nothing here", not against an upgrade that
    # silently fails to happen).
    full_outcome_element_ids: Set[str] = set()
    full_outcome_relation_ids: Set[str] = set()
    try:
        from . import purpose_verification  # local import: see module docstring

        full_outcome_targets = purpose_verification.full_outcome_criterion_target_ids(
            conn, system_id, resolved_session_id
        )
        full_outcome_element_ids = {
            target_id for target_kind, target_id in full_outcome_targets if target_kind == "element"
        }
        full_outcome_relation_ids = {
            target_id for target_kind, target_id in full_outcome_targets if target_kind == "relation"
        }
    except Exception:  # pragma: no cover - defensive
        full_outcome_element_ids = set()
        full_outcome_relation_ids = set()

    def _has_full_outcome(element: PurposeElement, primary_relation: Optional[PurposeRelation]) -> bool:
        """An element qualifies for `L3` if an Outcome Criterion targets it
        DIRECTLY, or targets its own PRIMARY relation (design decision 1's
        `full_outcome_criterion_target_ids` note) -- since
        `CREATABLE_NEED_CODES["outcome_criterion"]` only ever gates a
        RELATION-targeted need, the second branch is the only one reachable
        through the documented creation path today; the first exists so a
        future element-targeted need code (if one is ever added) does not
        require touching this function again."""
        if element.id in full_outcome_element_ids:
            return True
        return primary_relation is not None and primary_relation.id in full_outcome_relation_ids

    relations_by_id = {r.id: r for r in relations}
    if beneficiary_element is not None and desired_element is not None:
        rel = relations_by_id.get(
            relation_id("problem_to_change", beneficiary_element.id, desired_element.id)
        )
        beneficiary_element.resolution_level = _resolution_level(
            beneficiary_element,
            rel.status if rel else None,
            has_full_outcome_criterion=_has_full_outcome(beneficiary_element, rel),
        )
    elif beneficiary_element is not None:
        beneficiary_element.resolution_level = _resolution_level(
            beneficiary_element, None,
            has_full_outcome_criterion=_has_full_outcome(beneficiary_element, None),
        )

    if desired_element is not None and intervention_element is not None:
        rel = relations_by_id.get(
            relation_id("change_to_intervention", desired_element.id, intervention_element.id)
        )
        desired_element.resolution_level = _resolution_level(
            desired_element,
            rel.status if rel else None,
            has_full_outcome_criterion=_has_full_outcome(desired_element, rel),
        )
    elif desired_element is not None:
        desired_element.resolution_level = _resolution_level(
            desired_element, None,
            has_full_outcome_criterion=_has_full_outcome(desired_element, None),
        )

    if intervention_element is not None:
        capability_relations = [
            r
            for r in relations
            if r.kind == "intervention_to_capability" and r.source_id == intervention_element.id
        ]
        # `intervention`'s primary status is the WORST of zero-or-many
        # capability relations (design decision 1 above it), so its `L3`
        # eligibility mirrors that: a criterion on the DIRECT element OR on
        # ANY of those relations counts -- there is no single "the" primary
        # relation to hand to `_has_full_outcome` here.
        intervention_has_outcome = intervention_element.id in full_outcome_element_ids or any(
            r.id in full_outcome_relation_ids for r in capability_relations
        )
        intervention_element.resolution_level = _resolution_level(
            intervention_element,
            _worst_relation_status(capability_relations),
            has_full_outcome_criterion=intervention_has_outcome,
        )

    for extra in additional_intervention:
        extra.resolution_level = _resolution_level(
            extra, None, has_full_outcome_criterion=_has_full_outcome(extra, None)
        )

    for capability in capability_elements:
        rel = next(
            (
                r
                for r in relations
                if r.kind == "intervention_to_capability" and r.target_id == capability.id
            ),
            None,
        )
        capability.resolution_level = _resolution_level(
            capability,
            rel.status if rel else None,
            has_full_outcome_criterion=_has_full_outcome(capability, rel),
        )

    elements: List[PurposeElement] = [
        e for e in (beneficiary_element, desired_element, intervention_element) if e is not None
    ]
    elements.extend(additional_intervention)
    elements.extend(capability_elements)

    frame_slots = (beneficiary_element, desired_element, intervention_element)
    if "frame" in degraded_sections:
        # A degraded frame means at least one slot's SOURCE could not be read
        # reliably -- reported as `unavailable` even when the individual
        # elements still constructed gracefully as `unknown` (brief=None is a
        # valid, non-raising input to their own builders). "we could not
        # read it" and "we read it and there is nothing" must not collapse
        # into the same `empty` sentence (§0 invariant 6).
        frame_state = "unavailable"
    else:
        present_count = sum(1 for slot in frame_slots if slot is not None and slot.state == "present")
        if present_count == len(frame_slots):
            frame_state = "complete"
        elif present_count > 0:
            frame_state = "partial"
        else:
            frame_state = "empty"

    frame_resolution_level = _min_resolution(
        [slot.resolution_level if slot is not None else "L0" for slot in frame_slots]
    )

    return PurposeChainResult(
        system_id=system_id,
        session_id=resolved_session_id,
        generated_at=now,
        frame={
            "beneficiary_problem": beneficiary_element,
            "desired_change": desired_element,
            "intervention": intervention_element,
        },
        elements=elements,
        relations=relations,
        frame_resolution_level=frame_resolution_level,
        frame_state=frame_state,
        snapshot_id=snapshot_id,
        understanding_revision_id=understanding_revision_id,
        understanding_confirmed_at=getattr(brief, "confirmed_at", None),
        degraded_sections=degraded_sections,
        degraded_detail=degraded_detail,
    )


# --- §6. The one write: recording a relation decision ----------------------------


class PurposeChainError(Exception):
    """Base for the decision endpoint's finite error set."""


class RelationNotFound(PurposeChainError):
    """The relation id does not exist in the CURRENT projection."""


class RelationNotDecidable(PurposeChainError):
    """An endpoint is `unknown`/`unavailable` -- there is nothing yet to
    confirm or reject (§1.6: 存在しない前提を人に確定させない)."""


def record_relation_decision(
    conn: sqlite3.Connection,
    system_id: int,
    session_id: int,
    target_relation_id: str,
    *,
    decision: str,
    rationale: str,
    decided_by: Optional[str],
    now: Optional[float] = None,
) -> PurposeRelation:
    """The ONE write this module performs (§1.6).

    Always inserts a new row (§1.5: append-only) and supersedes the prior
    current decision for the same relation, mirroring
    `routes/interview_intent.correct_intent_item`'s revision-chain pattern.
    `decision_method` is hardcoded `'manual'` -- there is no parameter to make
    it anything else, so an AI-authored relation can never become `confirmed`
    without this exact call happening with a real `Principal` behind it.
    """
    now = time.time() if now is None else now
    chain = derive_purpose_chain(conn, system_id, session_id, now=now)

    relation = next((r for r in chain.relations if r.id == target_relation_id), None)
    if relation is None:
        raise RelationNotFound(target_relation_id)

    elements_by_id = {e.id: e for e in chain.elements}
    source = elements_by_id.get(relation.source_id)
    target = elements_by_id.get(relation.target_id)
    if (
        source is None
        or target is None
        or source.state in ("unknown", "unavailable")
        or target.state in ("unknown", "unavailable")
    ):
        raise RelationNotDecidable(target_relation_id)

    source_digest = element_digest(source)
    target_digest = element_digest(target)

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM purpose_relation_decision
               WHERE system_id = ? AND session_id = ? AND relation_id = ?
                 AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, session_id, target_relation_id),
        ).fetchone()

        cur = conn.execute(
            """INSERT INTO purpose_relation_decision
                   (system_id, session_id, relation_id, relation_kind, decision,
                    rationale, source_element_id, target_element_id,
                    source_digest, target_digest, understanding_revision_id,
                    intent_revision_id, snapshot_id, decision_method,
                    decided_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)""",
            (
                system_id,
                session_id,
                target_relation_id,
                relation.kind,
                decision,
                rationale,
                relation.source_id,
                relation.target_id,
                source_digest,
                target_digest,
                target.understanding_revision_id or source.understanding_revision_id,
                target.intent_revision_id or source.intent_revision_id,
                target.snapshot_id or source.snapshot_id,
                decided_by,
                now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE purpose_relation_decision SET superseded_by_id = ? WHERE id = ?",
                (new_id, prior["id"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    refreshed = derive_purpose_chain(conn, system_id, session_id, now=now)
    updated = next((r for r in refreshed.relations if r.id == target_relation_id), None)
    return updated if updated is not None else relation
