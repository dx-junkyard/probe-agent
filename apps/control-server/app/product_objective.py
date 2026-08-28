"""Product Objective / Milestone / Gap (Issue #429, Epic #427).

`docs/product-objective-lineage.md` §4/§5/§8/§10 is the canonical contract
this module implements. This is a deterministic domain service -- **no LLM
call anywhere in this module** (Principle 6). Everything here is either a
direct read of a persisted row, a first-match classification over a small
finite vocabulary, or an append-only write of content the caller supplied.

Modeled closely on `app/ux_design.py`: the same typed-exception hierarchy,
the same `get_args`-mirrored vocabulary tuples, the same `_degrade(...)`
guarded-section helper, the same `content_digest` canonicalization, and the
same manual `BEGIN`/`INSERT`/`UPDATE superseded_by_id`/`COMMIT` + `ROLLBACK`
transaction shape for every append-only write.

Two rules this module must never violate (§0 / §4 / §5):

* **No new understanding model, no upstream/downstream content copies.**
  Objective / Milestone / Gap content is genuinely new and IS stored here,
  but a reference to Vision/Purpose/Capability/Stakeholder Need
  (`product_objective_upstream_ref`) or to a detector
  (`product_gap_source_ref`) never copies the target's content -- only a
  `target_ref`/`source_ref` plus a `captured_digest`, resolved against
  exactly one canonical source per kind at READ time
  (`_resolve_upstream_target`, the same discipline
  `node_design._LINK_KIND_TARGET_SOURCE` / `ux_design._resolve_upstream_target`
  use one layer over).
* **`objective_state` / Milestone `design_status` + `achievement` +
  `assessability` / Gap `lifecycle` + `priority_band` are all DERIVED, never
  stored** (§4.2/§4.3/§5.6/§5.7): each folds from the latest non-superseded
  row of its own decision ledger. `recheck_state` is a SEPARATE axis -- a
  stale confirmed/assessed/decided item stays exactly as confirmed/
  assessed/decided (#337/#338/#349's "a stored lifecycle value can drift
  from the rows it describes, a derived one cannot", applied here to a
  decision ledger instead of an event log).

`product_gap_decision` carries TWO INDEPENDENT axes on one table --
`lifecycle` (from every decision except `prioritize`) and `priority_band`
(from `prioritize` decisions only, §5.7/§5.9's "`resolved` になっても最後に
置かれたバンドは読める"). Each axis therefore maintains its OWN "currently
effective row" pointer: a `prioritize` decision supersedes only the prior
`prioritize` row, and every other decision supersedes only the prior
non-`prioritize` row. A single shared chain across both would make a
`prioritize` row read as superseded the moment ANY later lifecycle decision
was recorded, silently erasing the audited priority the moment a Gap moved
to `resolved` -- exactly the loss §5.9 rules out.

Connection discipline (`.claude/skills/control-server/SKILL.md`): every
function here takes an already-open `conn` and performs no external call
(no `git`, no LLM, no subprocess) -- so, unlike
`ux_design.create_artifact_reference`, nothing in this module needs to
manage its own connection lifecycle. `add_gap_source_ref` / the Gap read
path import `app/product_gap_sources.py` (Issue #430, owned by a different
module written concurrently) LAZILY, inside the function, and guard the
call: an `ImportError` (the module not landed yet) or any exception it
raises degrades that ONE source reference to `source_state='unavailable'`
rather than failing the whole request (§5.5/§5.10 -- `resolve_source` itself
promises never to raise except for a structurally invalid `source_kind`,
but this module does not trust that promise blindly).

probe-agent:
  role: Deterministic Product Objective / Milestone / Gap domain service
  capability: product-objective-lineage
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify objective_state/design_status/achievement/lifecycle/priority_band are always derived from their own decision ledger rather than stored, that a Milestone can never be assessed while unconfirmed, that Gap lifecycle never moves except through a human product_gap_decision row, that parent/dependency cycle rejection is iterative (never recursive), and that a Gap source-ref resolution failure degrades only that one entry.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set, Tuple, get_args

from . import purpose_chain, stakeholder_network, understanding_brief
from .models import (
    ProductAuthorshipKind,
    ProductDeepLinkState,
    ProductDesignStatus,
    ProductGapArtifactLinkKind,
    ProductGapDecisionKind,
    ProductGapEvidenceKind,
    ProductGapLifecycle,
    ProductGapPriorityBand,
    ProductGapReadFlag,
    ProductGapSourceKind,
    ProductGapSourceState,
    ProductGapTargetMode,
    ProductMilestoneAchievement,
    ProductMilestoneAssessability,
    ProductMilestoneAssessmentKind,
    ProductMilestoneDecisionKind,
    ProductMilestoneVerificationMethod,
    ProductObjectiveDecisionKind,
    ProductObjectiveState,
    ProductRecheckState,
    ProductRefKind,
    ProductRefRecheckState,
    ProductRefRelationStatus,
    ProductRefTargetResolution,
    ProductRevisionState,
)

__all__ = [
    "ProductObjectiveError",
    "ProductObjectiveValidationError",
    "NotFound",
    "KeyRequired",
    "KeyConflict",
    "ParentSelfReference",
    "ParentCycle",
    "DependencySelfReference",
    "DependencyCycle",
    "DependencyDuplicate",
    "SourceDuplicate",
    "ArtifactDuplicate",
    "DecisionStaleDigest",
    "NotDecidable",
    "MilestoneNotAssessable",
    "RefKindInvalid",
    "SourceKindInvalid",
    "LinkKindInvalid",
    "content_digest",
    "objective_revision_digest",
    "milestone_revision_digest",
    "gap_revision_digest",
    "derive_objective_state",
    "derive_milestone_design_status",
    "derive_milestone_achievement",
    "derive_milestone_assessability",
    "derive_gap_lifecycle",
    "derive_gap_priority_band",
    "derive_recheck_state",
    "create_objective",
    "add_objective_revision",
    "set_objective_parent",
    "clear_objective_parent",
    "add_objective_upstream_ref",
    "record_objective_decision",
    "get_objective_summary",
    "get_objective_detail",
    "list_objectives",
    "create_milestone",
    "add_milestone_revision",
    "add_milestone_dependency",
    "record_milestone_decision",
    "record_milestone_assessment",
    "get_milestone_summary",
    "get_milestone_detail",
    "list_milestones",
    "create_gap",
    "add_gap_revision",
    "add_gap_source_ref",
    "add_gap_evidence_ref",
    "add_gap_artifact_link",
    "record_gap_decision",
    "get_gap_summary",
    "get_gap_detail",
    "list_gaps",
]


# --- §0. Finite vocabularies, mirrored from app/models.py with get_args -------

AUTHORSHIP_KINDS: Tuple[str, ...] = get_args(ProductAuthorshipKind)
OBJECTIVE_STATES: Tuple[str, ...] = get_args(ProductObjectiveState)
RECHECK_STATES: Tuple[str, ...] = get_args(ProductRecheckState)
REVISION_STATES: Tuple[str, ...] = get_args(ProductRevisionState)
DESIGN_STATUSES: Tuple[str, ...] = get_args(ProductDesignStatus)
OBJECTIVE_DECISION_KINDS: Tuple[str, ...] = get_args(ProductObjectiveDecisionKind)
MILESTONE_DECISION_KINDS: Tuple[str, ...] = get_args(ProductMilestoneDecisionKind)
MILESTONE_ASSESSMENT_KINDS: Tuple[str, ...] = get_args(ProductMilestoneAssessmentKind)
MILESTONE_ACHIEVEMENTS: Tuple[str, ...] = get_args(ProductMilestoneAchievement)
MILESTONE_ASSESSABILITIES: Tuple[str, ...] = get_args(ProductMilestoneAssessability)
MILESTONE_VERIFICATION_METHODS: Tuple[str, ...] = get_args(ProductMilestoneVerificationMethod)
GAP_TARGET_MODES: Tuple[str, ...] = get_args(ProductGapTargetMode)
GAP_SOURCE_KINDS: Tuple[str, ...] = get_args(ProductGapSourceKind)
GAP_SOURCE_STATES: Tuple[str, ...] = get_args(ProductGapSourceState)
GAP_LIFECYCLES: Tuple[str, ...] = get_args(ProductGapLifecycle)
GAP_DECISION_KINDS: Tuple[str, ...] = get_args(ProductGapDecisionKind)
GAP_PRIORITY_BANDS: Tuple[str, ...] = get_args(ProductGapPriorityBand)
GAP_EVIDENCE_KINDS: Tuple[str, ...] = get_args(ProductGapEvidenceKind)
GAP_ARTIFACT_LINK_KINDS: Tuple[str, ...] = get_args(ProductGapArtifactLinkKind)
GAP_READ_FLAGS: Tuple[str, ...] = get_args(ProductGapReadFlag)
REF_KINDS: Tuple[str, ...] = get_args(ProductRefKind)
REF_RELATION_STATUSES: Tuple[str, ...] = get_args(ProductRefRelationStatus)
REF_TARGET_RESOLUTIONS: Tuple[str, ...] = get_args(ProductRefTargetResolution)
REF_RECHECK_STATES: Tuple[str, ...] = get_args(ProductRefRecheckState)
DEEP_LINK_STATES: Tuple[str, ...] = get_args(ProductDeepLinkState)

#: §4.6's fixed translation from a reference's own `decision_method` to WHO
#: ASSERTED it -- never a second stored status column. The same table
#: `node_design._DECISION_METHOD_TO_RELATION_STATUS` /
#: `ux_design._DECISION_METHOD_TO_RELATION_STATUS` use one layer over.
_DECISION_METHOD_TO_RELATION_STATUS: Dict[str, str] = {
    "manual": "confirmed",
    "reasoning_llm": "proposed",
    "deterministic": "derived",
}

#: §4.3's Objective decision-ledger fold: the latest non-superseded
#: `product_objective_decision.decision` value -> the DERIVED
#: `objective_state`.
_OBJECTIVE_DECISION_TO_STATE: Dict[str, str] = {
    "confirm": "confirmed",
    "activate": "active",
    "achieve": "achieved",
    "reject": "rejected",
    "retire": "retired",
    "reinstate": "proposed",
}

#: §4.3's precondition table: `decision -> the objective_state values it may
#: be recorded FROM`. Anything outside this is 422
#: `product_objective_not_decidable`. `achieve`'s only precondition is
#: `active` -- a merely-confirmed Objective can never become `achieved`, and
#: this table alone (never Milestone `achievement`) decides legality (§6).
_OBJECTIVE_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "confirm": ("proposed",),
    "activate": ("confirmed", "active"),
    "achieve": ("active",),
    "reject": ("proposed", "confirmed"),
    "retire": ("confirmed", "active", "achieved"),
    "reinstate": ("rejected", "retired"),
}

#: §4.3's Milestone DEFINITION ledger fold -- identical vocabulary/shape to
#: `ProductDesignStatus`, folded from `product_milestone_decision`.
_MILESTONE_DECISION_TO_DESIGN_STATUS: Dict[str, str] = {
    "confirm": "confirmed",
    "reject": "rejected",
    "retire": "retired",
    "reinstate": "proposed",
}

#: §4.3's Milestone definition transition table: every decision except
#: `reinstate` requires `proposed`/`confirmed`; `reinstate` is the only way
#: back from `rejected`/`retired`.
_MILESTONE_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "confirm": ("proposed", "confirmed"),
    "reject": ("proposed", "confirmed"),
    "retire": ("proposed", "confirmed"),
    "reinstate": ("rejected", "retired"),
}

#: §4.3's Milestone ACHIEVEMENT ledger fold, folded from
#: `product_milestone_assessment`. No transition table gates this one beyond
#: the `design_status == 'confirmed'` precondition enforced separately
#: (422 `product_milestone_not_assessable`) -- any assessment kind is legal
#: at any time once the definition is confirmed (§4.3).
_MILESTONE_ASSESSMENT_TO_ACHIEVEMENT: Dict[str, str] = {
    "met": "met",
    "not_met": "not_met",
    "indeterminate": "indeterminate",
    "withdraw": "unassessed",
}

#: §5.6's Gap LIFECYCLE ledger fold, folded from the latest non-superseded
#: `product_gap_decision` row whose `decision != 'prioritize'` (see the
#: module docstring's note on the two independent axes sharing one table).
#: `prioritize` never appears here -- it never moves `lifecycle` by itself.
_GAP_DECISION_TO_LIFECYCLE: Dict[str, str] = {
    "acknowledge": "acknowledged",
    "defer": "deferred",
    "resolve": "resolved",
    "reject": "rejected",
    "retire": "obsolete",
    "reopen": "open",
}

#: §5.6's Gap lifecycle transition table. `prioritize` requires an
#: open-ish lifecycle (`open`/`acknowledged`/`deferred`) but never appears
#: as a RESULT here -- it never changes `lifecycle` (only `priority_band`).
_GAP_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "acknowledge": ("open",),
    "defer": ("open", "acknowledged"),
    "resolve": ("open", "acknowledged", "deferred"),
    "reject": ("open", "acknowledged", "deferred"),
    "retire": ("open", "acknowledged", "deferred"),
    "reopen": ("resolved", "rejected", "obsolete", "deferred"),
}

#: §5.6's precondition for `prioritize` itself: legal only from an open-ish
#: lifecycle (a terminal Gap gets no more priority placed on it, §5.6's
#: "片付いた Gap に優先度を置く意味が無い").
_GAP_PRIORITIZE_LEGAL_FROM: Tuple[str, ...] = ("open", "acknowledged", "deferred")

#: §6's read-time-only advisory flags: which lifecycle buckets make each
#: flag actionable. `close_candidate` only means something while the
#: corresponding `resolve`/`reject`/`retire` decision is still legal;
#: `reopen_candidate` only means something while `reopen` is still legal.
#: Never persisted, never a `ProductGapLifecycle` value (§6).
_CLOSE_CANDIDATE_LIFECYCLES: Tuple[str, ...] = ("open", "acknowledged", "deferred")
_REOPEN_CANDIDATE_LIFECYCLES: Tuple[str, ...] = ("resolved", "rejected", "obsolete", "deferred")


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise ProductObjectiveValidationError(
            f"{field_name} must be one of {', '.join(vocabulary)}; got {value!r}"
        )


# --- Errors ---------------------------------------------------------------------


class ProductObjectiveError(ValueError):
    """Base class for every failure this module raises."""


class ProductObjectiveValidationError(ProductObjectiveError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class NotFound(ProductObjectiveError):
    """A referenced row does not exist, or belongs to another System.

    The two are deliberately the same error -- telling them apart would let
    a caller probe another System's ids (the same rule
    `ux_design.NotFound` / `node_design.NodeDesignNotFoundError` document).
    """


class KeyRequired(ProductObjectiveError):
    """`objective_key` / `milestone_key` / `gap_key` was empty.

    `kind` is one of `"objective"` / `"milestone"` / `"gap"`; the route maps
    it to the per-entity §10.1 code (`product_<kind>_key_required`, 422) --
    unlike `ux_design.KeyRequired`, which shares ONE code across Journey and
    Requirement, §10.1 requires the entity name in the code itself.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"{kind}_key is required")


class KeyConflict(ProductObjectiveError):
    """A key already exists in this System (`product_<kind>_key_conflict`, 409)."""

    def __init__(self, kind: str, key: str) -> None:
        self.kind = kind
        self.key = key
        super().__init__(f"{kind} key {key!r} already exists in this System")


class ParentSelfReference(ProductObjectiveError):
    """An Objective was set as its own parent (422 `product_objective_parent_self`)."""


class ParentCycle(ProductObjectiveError):
    """Setting this parent would create a cycle in the CURRENT parent-link
    graph (422 `product_objective_parent_cycle`)."""


class DependencySelfReference(ProductObjectiveError):
    """A Milestone was set to depend on itself
    (422 `product_milestone_dependency_self`)."""


class DependencyCycle(ProductObjectiveError):
    """Adding this dependency would create a cycle in the CURRENT dependency
    graph (422 `product_milestone_dependency_cycle`)."""


class DependencyDuplicate(ProductObjectiveError):
    """This exact `(milestone, depends_on)` pair is already a current
    dependency edge (409 `product_milestone_dependency_duplicate`)."""


class SourceDuplicate(ProductObjectiveError):
    """This exact `(source_kind, source_ref)` is already a current source
    ref on this Gap (409 `product_gap_source_duplicate`)."""


class ArtifactDuplicate(ProductObjectiveError):
    """This exact `(link_kind, target_ref)` is already a current artifact
    link on this Gap (409 `product_gap_artifact_duplicate`)."""


class DecisionStaleDigest(ProductObjectiveError):
    """The caller's non-empty `captured_digest` does not match the
    subject's current `content_digest`
    (409 `product_<kind>_decision_stale_digest`)."""

    def __init__(self, kind: str, key: str) -> None:
        self.kind = kind
        super().__init__(f"{kind} {key!r}: captured_digest does not match current content")


class NotDecidable(ProductObjectiveError):
    """The requested decision is illegal from the subject's current derived
    state (422 `product_<kind>_not_decidable`)."""

    def __init__(self, kind: str, key: str) -> None:
        self.kind = kind
        super().__init__(f"{kind} {key!r}: this decision is not legal from the current state")


class MilestoneNotAssessable(ProductObjectiveError):
    """An assessment was recorded against a Milestone whose `design_status`
    is not `confirmed` (422 `product_milestone_not_assessable`, §4.3)."""


class RefKindInvalid(ProductObjectiveValidationError):
    """`ref_kind` is outside `ProductRefKind`
    (422 `product_ref_kind_invalid`)."""


class SourceKindInvalid(ProductObjectiveValidationError):
    """`source_kind` is outside `ProductGapSourceKind`
    (422 `product_source_kind_invalid`)."""


class LinkKindInvalid(ProductObjectiveValidationError):
    """`link_kind` is outside `ProductGapArtifactLinkKind`
    (422 `product_link_kind_invalid`)."""


def _degrade(
    degraded_sections: List[str],
    degraded_detail: Dict[str, str],
    section: str,
    exc: Exception,
) -> None:
    """Record one section as degraded without ever substituting a guessed
    value for what it failed to read (§0 invariant 8, the same discipline
    `ux_design._degrade` / `purpose_chain._degrade` follow)."""
    if section not in degraded_sections:
        degraded_sections.append(section)
    degraded_detail[section] = f"{type(exc).__name__}: {exc}"


# --- §8. Digests ------------------------------------------------------------


def content_digest(payload: Dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON form of `payload` (§8). Same
    canonicalization every other domain module in this codebase uses:
    `sort_keys=True, ensure_ascii=False, separators=(",", ":")`.
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def objective_revision_digest(
    *, title: str, intent: str, contribution: str, scope_note: str, summary: str
) -> str:
    """§8's Objective revision digest: `title, intent, contribution,
    scope_note, summary`. `created_by`/`created_at`/`revision_number`/
    `change_note` are excluded -- a recheck fires on a MEANING change, never
    on the mere existence of a new record."""
    return content_digest(
        {"title": title, "intent": intent, "contribution": contribution, "scope_note": scope_note, "summary": summary}
    )


def milestone_revision_digest(
    *, title: str, target_state: str, verification_method: str, verification_note: str, summary: str
) -> str:
    """§8's Milestone revision digest. `sequence_hint` is deliberately
    EXCLUDED -- reordering how Milestones display changes nothing about
    what any one Milestone MEANS."""
    return content_digest(
        {
            "title": title,
            "target_state": target_state,
            "verification_method": verification_method,
            "verification_note": verification_note,
            "summary": summary,
        }
    )


def gap_revision_digest(
    *, title: str, current_state: str, target_state: str, target_state_mode: str, interpretation: str
) -> str:
    """§8's Gap revision digest. `suggested_priority_note` is deliberately
    EXCLUDED -- an AI updating its own priority suggestion must not stale a
    human's confirmed reading of the Gap."""
    return content_digest(
        {
            "title": title,
            "current_state": current_state,
            "target_state": target_state,
            "target_state_mode": target_state_mode,
            "interpretation": interpretation,
        }
    )


# --- Shared decision-ledger helpers ------------------------------------------


def derive_recheck_state(current_digest: str, decision_row: Optional[Dict[str, Any]]) -> str:
    """§4.2's `recheck_state`, shared by Objective/Milestone/Gap: `stale`
    when the currently effective decision's STORED `captured_digest` (what
    the caller actually asserted it was judging, §10.1 -- never
    auto-resolved from the subject's content the way
    `ux_design.record_design_decision` does) no longer matches the
    subject's current content digest. An empty stored `captured_digest` is
    the fail-closed `not_captured` (#337's `premise_not_captured`) -- it is
    never silently promoted to `current`. No decision row at all means
    nothing has been judged yet, so there is nothing to be stale against
    (`current`)."""
    if decision_row is None:
        return "current"
    captured = decision_row["captured_digest"]
    if not captured:
        return "not_captured"
    if captured != current_digest:
        return "stale"
    return "current"


def _check_transition(
    transitions: Dict[str, Tuple[str, ...]], decision: str, prior_state: str, kind: str, key: str
) -> None:
    allowed = transitions.get(decision, ())
    if prior_state not in allowed:
        raise NotDecidable(kind, key)


def _would_create_cycle(
    conn: sqlite3.Connection,
    table: str,
    id_column: str,
    target_column: str,
    *,
    subject_id: int,
    candidate_target_id: int,
) -> bool:
    """§4.4's cycle check, generic over the two append-only graphs this
    module maintains (`product_objective_parent_link` and
    `product_milestone_dependency`): would adding the edge
    `subject_id -> candidate_target_id` create a cycle over the CURRENTLY
    ACTIVE (`superseded_by_id IS NULL`) edges?

    That is true exactly when `candidate_target_id` can already reach
    `subject_id` by walking existing edges outward from
    `candidate_target_id` -- an ITERATIVE walk with a visited set, never
    recursion, so no depth limit is needed (§4.4: "深さ制限は設けない...
    ただし循環検査は必ず訪問済み集合を持つ反復で行い、再帰で書かない").

    `table` / `id_column` / `target_column` are always one of this module's
    own hardcoded call sites below, never caller input, so the f-string
    query carries no injection risk (the same convention
    `ux_design._fold_latest_decision` documents).
    """
    visited: Set[int] = set()
    stack: List[int] = [candidate_target_id]
    while stack:
        current = stack.pop()
        if current == subject_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        rows = conn.execute(
            f"SELECT {target_column} AS nxt FROM {table} "  # noqa: S608 - table/target_column are fixed constants, never caller input
            f"WHERE {id_column} = ? AND superseded_by_id IS NULL",
            (current,),
        ).fetchall()
        for row in rows:
            nxt = row["nxt"]
            if nxt is not None and nxt not in visited:
                stack.append(nxt)
    return False


# ==============================================================================
# §4.6. Upstream reference resolution (vision_claim / purpose_element /
#       purpose_relation / capability_entity / stakeholder_need), each
#       against its own single canonical source.
# ==============================================================================


def _purpose_relation_digest(relation: "purpose_chain.PurposeRelation") -> str:
    """A local digest for a Purpose Chain RELATION -- `purpose_chain`
    exposes `element_digest` for elements but no relation-level digest of
    its own. The same local digest `ux_design._purpose_relation_digest` /
    `stakeholder_network._purpose_relation_digest` compute one layer over,
    duplicated here rather than imported to keep this module's only
    cross-module dependency on those siblings limited to their public
    surface."""
    return content_digest(
        {
            "kind": relation.kind,
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "status": relation.status,
            "provenance": relation.provenance,
        }
    )


def _resolve_purpose_target(conn: sqlite3.Connection, system_id: int, ref_kind: str, target_ref: str) -> Dict[str, Any]:
    """Resolve `purpose_element` / `purpose_relation` against
    `purpose_chain.derive_purpose_chain`'s freshly-computed projection
    (§4.6's table, resolved against the System's newest Interview session --
    `derive_purpose_chain(conn, system_id, None)` resolves `None` to that
    session itself). A derivation failure is `unavailable` -- the source
    itself could not be read, never "the target does not exist"."""
    try:
        chain = purpose_chain.derive_purpose_chain(conn, system_id, None)
    except Exception:  # pragma: no cover - defensive
        return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}

    if ref_kind == "purpose_element":
        element = next((e for e in chain.elements if e.id == target_ref), None)
        if element is None:
            return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
        return {
            "resolution": "resolved",
            "name": element.display_statement or element.statement or None,
            "state": element.state,
            "digest": purpose_chain.element_digest(element),
        }

    relation = next((r for r in chain.relations if r.id == target_ref), None)
    if relation is None:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    return {
        "resolution": "resolved",
        "name": None,
        "state": relation.status,
        "digest": _purpose_relation_digest(relation),
    }


def _resolve_capability_entity(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    """Resolve against Issue #312's canonical Capability identity (current
    confirmation head), the same approach
    `ux_design._resolve_capability_entity` / `node_design._resolve_capability`
    use elsewhere in this codebase."""
    ref = (target_ref or "").strip()
    if not ref.isdigit():
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    entity_id = int(ref)
    entity = conn.execute(
        "SELECT id FROM understanding_capability_entity WHERE id = ? AND system_id = ?",
        (entity_id, system_id),
    ).fetchone()
    if entity is None:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}

    head = conn.execute(
        "SELECT id FROM understanding_capability_confirmation WHERE system_id = ? ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    name_row = None
    if head is not None:
        name_row = conn.execute(
            """SELECT name FROM understanding_capability_entity_version
                   WHERE system_id = ? AND confirmation_id = ? AND entity_id = ?""",
            (system_id, head["id"], entity_id),
        ).fetchone()
    if name_row is not None:
        name, state = name_row["name"], "confirmed"
    else:
        last_row = conn.execute(
            """SELECT name FROM understanding_capability_entity_version
                   WHERE system_id = ? AND entity_id = ? ORDER BY confirmation_id DESC LIMIT 1""",
            (system_id, entity_id),
        ).fetchone()
        name = last_row["name"] if last_row is not None else None
        state = "superseded"
    digest = content_digest({"name": name, "state": state})
    return {"resolution": "resolved", "name": name, "state": state, "digest": digest}


def _resolve_vision_claim_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    """Resolve `vision_claim` (§4.6's note): through
    `understanding_brief.build_understanding_brief(...).vision`, but the
    DIGEST comes from `understanding_brief.claim_digest` applied to the RAW
    understanding item -- never a `BriefClaim` (a `BriefClaim` has no
    `claim_digest`-compatible shape, and §4.6 is explicit that this layer
    must digest the raw item, not the Brief's presentation object).

    Vision has no stable row identity (§4.6): identity here is EXACT NAME
    equality against `target_ref`, matching `understanding_diff`'s rule one
    layer up. A reworded Vision therefore makes the reference `unresolved`
    by construction -- that weakness is reported honestly rather than
    hidden by copying the Vision text into this table to "stabilize" it
    (the #397 handoff mistake this contract explicitly forbids repeating).

    The raw item backing a `developer_intent`-provenance Vision is the
    confirmed Intent Brief `goal` row; the raw item backing a reviewer
    (`ai_hypothesis`) Vision is the matching entry of
    `current_understanding['vision']`. Both reads are best-effort: if the
    matching raw item cannot be found (a defensive case, since `brief.vision`
    already resolved successfully), the digest degrades to `""` rather than
    raising -- the resolution itself still succeeds because the NAME
    matched, just as `_ref_recheck_state` treats an empty captured digest as
    `not_captured` rather than fabricating one that was never truly
    captured.
    """
    ref = (target_ref or "").strip()
    if not ref:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}

    try:
        session_row = conn.execute(
            "SELECT id FROM interview_session WHERE system_id = ? ORDER BY id DESC LIMIT 1",
            (system_id,),
        ).fetchone()
        session_id = session_row["id"] if session_row is not None else None
        brief = understanding_brief.build_understanding_brief(conn, system_id, session_id)
    except Exception:  # pragma: no cover - defensive
        return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}

    vision = brief.vision
    if vision is None or vision.name != ref:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}

    raw_item: Optional[Dict[str, Any]] = None
    if session_id is not None:
        if vision.provenance == "developer_intent":
            try:
                row = conn.execute(
                    """SELECT field, value_text, status, origin, is_mock FROM interview_intent_item
                       WHERE session_id = ? AND system_id = ? AND field = 'goal'
                         AND superseded_by_id IS NULL AND status != 'not_applicable'
                       ORDER BY id DESC LIMIT 1""",
                    (session_id, system_id),
                ).fetchone()
                raw_item = dict(row) if row is not None else None
            except Exception:  # pragma: no cover - defensive
                raw_item = None
        if raw_item is None:
            try:
                understanding_row = conn.execute(
                    "SELECT current_understanding FROM interview_session WHERE id = ?", (session_id,)
                ).fetchone()
                if understanding_row is not None and understanding_row["current_understanding"]:
                    parsed = json.loads(understanding_row["current_understanding"])
                    items = parsed.get("vision") if isinstance(parsed, dict) else None
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict) and str(item.get("name")) == ref:
                                raw_item = item
                                break
            except Exception:  # pragma: no cover - defensive
                raw_item = None

    digest = understanding_brief.claim_digest(raw_item) if raw_item is not None else ""
    return {"resolution": "resolved", "name": vision.name, "state": vision.confirmation, "digest": digest}


def _resolve_stakeholder_need_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    """Resolve `stakeholder_need` against Epic #418's Need identity
    (`stakeholder_network`'s `stakeholder_need` / `stakeholder_need_revision`
    tables), reusing that module's own digest and decision-ledger fold
    rather than reimplementing them."""
    ref = (target_ref or "").strip()
    if not ref:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    try:
        need = stakeholder_network._get_need_row(conn, system_id, ref)  # noqa: SLF001 - established cross-module reuse, mirrors ux_design's reuse of git_ops._is_safe_git_path
    except sqlite3.OperationalError:
        return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if need is None:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}

    revision = None
    if need["current_revision_id"] is not None:
        rev_row = conn.execute(
            "SELECT * FROM stakeholder_need_revision WHERE id = ?", (need["current_revision_id"],)
        ).fetchone()
        revision = dict(rev_row) if rev_row is not None else None
    if revision is None:
        return {"resolution": "resolved", "name": None, "state": "proposed", "digest": ""}

    digest = stakeholder_network.need_digest(
        need_key=ref,
        need_kind=revision["need_kind"],
        statement=revision["statement"],
        rationale=revision["rationale"],
        stakeholder_key=revision["stakeholder_key"],
    )
    state, _decision_row = stakeholder_network.derive_design_status(conn, system_id, "stakeholder_need", ref)
    return {"resolution": "resolved", "name": revision["statement"] or ref, "state": state, "digest": digest}


def _resolve_upstream_target(conn: sqlite3.Connection, system_id: int, ref_kind: str, target_ref: str) -> Dict[str, Any]:
    """Dispatch to the ONE canonical source per `ref_kind` (§4.6's table)."""
    if ref_kind == "vision_claim":
        try:
            return _resolve_vision_claim_target(conn, system_id, target_ref)
        except Exception:  # pragma: no cover - defensive
            return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if ref_kind in ("purpose_element", "purpose_relation"):
        try:
            return _resolve_purpose_target(conn, system_id, ref_kind, target_ref)
        except Exception:  # pragma: no cover - defensive
            return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if ref_kind == "capability_entity":
        try:
            return _resolve_capability_entity(conn, system_id, target_ref)
        except Exception:  # pragma: no cover - defensive
            return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if ref_kind == "stakeholder_need":
        try:
            return _resolve_stakeholder_need_target(conn, system_id, target_ref)
        except Exception:  # pragma: no cover - defensive
            return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    raise RefKindInvalid(ref_kind)


def _ref_recheck_state(captured_digest: str, resolution: str, current_digest: str) -> str:
    """§4.6's `recheck_state`: `not_captured` is fail-closed for an empty
    `captured_digest` (never treated as `current`); an unresolved/
    unavailable target is always `stale` (its content plainly no longer
    matches what was captured); only a resolved target with a matching
    digest reads `current`."""
    if not captured_digest:
        return "not_captured"
    if resolution != "resolved":
        return "stale"
    if captured_digest != current_digest:
        return "stale"
    return "current"


def _upstream_ref_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_upstream_target(conn, system_id, row["ref_kind"], row["target_ref"])
    current_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
    return {
        "id": row["id"],
        "objective_id": row["objective_id"],
        "ref_kind": row["ref_kind"],
        "target_ref": row["target_ref"],
        "target_row_id": row["target_row_id"],
        "target_name": resolved["name"],
        "relation_status": _DECISION_METHOD_TO_RELATION_STATUS.get(row["decision_method"], "derived"),
        "target_state": resolved["state"],
        "target_resolution": resolved["resolution"],
        "recheck_state": _ref_recheck_state(row["captured_digest"], resolved["resolution"], current_digest),
        "captured_digest": row["captured_digest"],
        "captured_session_id": row["captured_session_id"],
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


# ==============================================================================
# §4. Product Objective
# ==============================================================================


def _get_objective_row(conn: sqlite3.Connection, system_id: int, objective_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM product_objective WHERE system_id = ? AND objective_key = ?", (system_id, objective_key)
    ).fetchone()
    return dict(row) if row is not None else None


def create_objective(
    conn: sqlite3.Connection, *, system_id: int, objective_key: str, created_by: Optional[str], now: Optional[float] = None
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not objective_key:
        raise KeyRequired("objective")
    if _get_objective_row(conn, system_id, objective_key) is not None:
        raise KeyConflict("objective", objective_key)
    cur = conn.execute(
        "INSERT INTO product_objective (system_id, objective_key, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (system_id, objective_key, created_by, now, now),
    )
    row = conn.execute("SELECT * FROM product_objective WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def _objective_current_digest(conn: sqlite3.Connection, objective: Dict[str, Any]) -> str:
    if objective["current_revision_id"] is None:
        return ""
    row = conn.execute(
        "SELECT content_digest FROM product_objective_revision WHERE id = ?", (objective["current_revision_id"],)
    ).fetchone()
    return row["content_digest"] if row is not None else ""


def derive_objective_state(
    conn: sqlite3.Connection, system_id: int, objective_key: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """§4.2/§4.3: the latest non-superseded `product_objective_decision` row
    for `(system_id, objective_key)`, folded through `_OBJECTIVE_DECISION_TO_STATE`.
    No row -> `proposed`. Returns the state plus the decision row itself (or
    `None`) so callers needing `captured_digest` for `recheck_state` do not
    requery."""
    row = conn.execute(
        """SELECT * FROM product_objective_decision
           WHERE system_id = ? AND objective_key = ? AND superseded_by_id IS NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, objective_key),
    ).fetchone()
    if row is None:
        return "proposed", None
    decision_row = dict(row)
    return _OBJECTIVE_DECISION_TO_STATE[decision_row["decision"]], decision_row


def add_objective_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    objective_key: str,
    title: str = "",
    intent: str = "",
    contribution: str = "",
    scope_note: str = "",
    summary: str = "",
    change_note: str = "",
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Append one Objective revision (§4.5's `product_objective_revision`).
    `authored_by_kind`/`decision_method`/`intelligence_run_id` are
    parameters on this DOMAIN function, but `routes/product_objectives.py`'s
    public write endpoint never accepts them from the request body -- it
    always passes `authored_by_kind="developer"`, `decision_method="manual"`
    (this module calls no LLM anywhere; a future AI-assisted authoring flow
    is out of this issue's scope)."""
    now = time.time() if now is None else now
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")

    digest = objective_revision_digest(
        title=title, intent=intent, contribution=contribution, scope_note=scope_note, summary=summary
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = objective["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM product_objective_revision WHERE objective_id = ?",
            (objective["id"],),
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO product_objective_revision
                   (objective_id, system_id, revision_number, title, intent, contribution, scope_note,
                    summary, content_digest, authored_by_kind, decision_method, intelligence_run_id,
                    change_note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                objective["id"], system_id, revision_number, title, intent, contribution, scope_note,
                summary, digest, authored_by_kind, decision_method, intelligence_run_id,
                change_note, created_by, now,
            ),
        )
        new_revision_id = cur.lastrowid
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE product_objective_revision SET superseded_by_id = ? WHERE id = ?",
                (new_revision_id, prior_revision_id),
            )
        conn.execute(
            "UPDATE product_objective SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, objective["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_objective_detail(conn, system_id, objective_key)


def set_objective_parent(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    objective_key: str,
    parent_objective_key: str,
    rationale: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """§4.4: append-only re-parenting. Self-reference and cycle rejection
    are evaluated over the CURRENTLY ACTIVE parent-link graph only, by
    iteration with a visited set (`_would_create_cycle`) -- never
    recursion, and no depth limit (§4.4 sets none)."""
    now = time.time() if now is None else now
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")
    parent = _get_objective_row(conn, system_id, parent_objective_key)
    if parent is None:
        raise NotFound(f"Objective {parent_objective_key!r} not found")
    if objective["id"] == parent["id"]:
        raise ParentSelfReference(objective_key)
    if _would_create_cycle(
        conn, "product_objective_parent_link", "objective_id", "parent_objective_id",
        subject_id=objective["id"], candidate_target_id=parent["id"],
    ):
        raise ParentCycle(objective_key)

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_objective_parent_link
               WHERE system_id = ? AND objective_id = ? AND superseded_by_id IS NULL""",
            (system_id, objective["id"]),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_objective_parent_link
                   (system_id, objective_id, parent_objective_id, rationale, decision_method, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (system_id, objective["id"], parent["id"], rationale, decision_method, created_by, now),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE product_objective_parent_link SET superseded_by_id = ? WHERE id = ?", (new_id, prior["id"])
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_objective_summary(conn, system_id, objective_key)


def clear_objective_parent(
    conn: sqlite3.Connection, *, system_id: int, objective_key: str, now: Optional[float] = None
) -> Dict[str, Any]:
    """`DELETE .../parent` (§10). §4.4 defines "current" as `superseded_by_id
    IS NULL` and root as the ABSENCE of a current row -- "NULL の親行を
    作らない". Since `parent_objective_id` is `NOT NULL`, there is no row
    shape that can represent "no parent" while still being a normal
    append-only correction (which always supersedes into ANOTHER real
    link row). Going back to root is therefore the one structurally
    special case in this table: it removes the currently active link row
    outright rather than superseding it into a placeholder, so the table
    returns to exactly the state §4.4 describes as root (no current row).
    A no-op (already root) is not an error -- DELETE is idempotent."""
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")

    conn.execute("BEGIN")
    try:
        conn.execute(
            "DELETE FROM product_objective_parent_link WHERE system_id = ? AND objective_id = ? AND superseded_by_id IS NULL",
            (system_id, objective["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_objective_summary(conn, system_id, objective_key)


def add_objective_upstream_ref(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    objective_key: str,
    ref_kind: str,
    target_ref: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one upstream reference (§4.6). Always a NEW row -- correction
    is an explicit developer action via a future revision/decision, not an
    implicit dedup on every POST (matching `ux_design.add_upstream_ref`).
    `captured_digest` is whatever the target's digest resolves to RIGHT
    NOW -- empty when the target does not currently resolve, exactly the
    `not_captured` case `_ref_recheck_state` treats as fail-closed."""
    now = time.time() if now is None else now
    _check_membership(ref_kind, REF_KINDS, "ref_kind")
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")

    resolved = _resolve_upstream_target(conn, system_id, ref_kind, target_ref)
    captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""

    cur = conn.execute(
        """INSERT INTO product_objective_upstream_ref
               (system_id, objective_id, ref_kind, target_ref, target_row_id, captured_digest,
                captured_session_id, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)""",
        (system_id, objective["id"], ref_kind, target_ref, captured_digest, note, decision_method, created_by, now),
    )
    row = conn.execute("SELECT * FROM product_objective_upstream_ref WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _upstream_ref_out_dict(conn, system_id, dict(row))


def record_objective_decision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    objective_key: str,
    decision: str,
    rationale: str = "",
    captured_digest: str = "",
    decided_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The ONE write behind `objective_state` (§4.2/§4.3). `decision_method`
    is hardcoded `'manual'` -- there is no parameter to make it anything
    else, matching `product_objective_decision`'s
    `CHECK (decision_method = 'manual')`.

    `captured_digest` is stored EXACTLY as the caller supplied it (§10.1: an
    empty value is recorded as-is, never auto-filled with the resolved
    current digest) -- this is what lets `recheck_state='not_captured'`
    mean something distinct from `'stale'`.
    """
    now = time.time() if now is None else now
    _check_membership(decision, OBJECTIVE_DECISION_KINDS, "decision")
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")

    current_digest = _objective_current_digest(conn, objective)
    if captured_digest and captured_digest != current_digest:
        raise DecisionStaleDigest("objective", objective_key)

    prior_state, _ = derive_objective_state(conn, system_id, objective_key)
    _check_transition(_OBJECTIVE_TRANSITIONS, decision, prior_state, "objective", objective_key)

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_objective_decision
               WHERE system_id = ? AND objective_key = ? AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, objective_key),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_objective_decision
                   (system_id, objective_id, objective_key, decision, rationale, captured_digest,
                    captured_revision_id, decided_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, objective["id"], objective_key, decision, rationale, captured_digest,
                objective["current_revision_id"], decided_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute("UPDATE product_objective_decision SET superseded_by_id = ? WHERE id = ?", (new_id, prior["id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_objective_decision WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


def _objective_revision_out_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    return d


def _objective_parent_link(conn: sqlite3.Connection, system_id: int, objective_id: int) -> Tuple[Optional[int], Optional[str]]:
    row = conn.execute(
        """SELECT pl.parent_objective_id AS parent_id, po.objective_key AS parent_key
           FROM product_objective_parent_link pl
           JOIN product_objective po ON po.id = pl.parent_objective_id
           WHERE pl.system_id = ? AND pl.objective_id = ? AND pl.superseded_by_id IS NULL""",
        (system_id, objective_id),
    ).fetchone()
    if row is None:
        return None, None
    return row["parent_id"], row["parent_key"]


def _objective_out_dict(conn: sqlite3.Connection, system_id: int, objective: Dict[str, Any]) -> Dict[str, Any]:
    revision = None
    if objective["current_revision_id"] is not None:
        rev = conn.execute(
            "SELECT * FROM product_objective_revision WHERE id = ?", (objective["current_revision_id"],)
        ).fetchone()
        revision = dict(rev) if rev is not None else None
    state, decision_row = derive_objective_state(conn, system_id, objective["objective_key"])
    current_digest = revision["content_digest"] if revision else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)
    parent_id, parent_key = _objective_parent_link(conn, system_id, objective["id"])
    return {
        "id": objective["id"],
        "system_id": objective["system_id"],
        "objective_key": objective["objective_key"],
        "current_revision_id": objective["current_revision_id"],
        "current_revision_number": revision["revision_number"] if revision else None,
        "title": revision["title"] if revision else "",
        "objective_state": state,
        "recheck_state": recheck_state,
        "parent_objective_id": parent_id,
        "parent_objective_key": parent_key,
        "created_by": objective["created_by"],
        "created_at": objective["created_at"],
        "updated_at": objective["updated_at"],
    }


def get_objective_summary(conn: sqlite3.Connection, system_id: int, objective_key: str) -> Dict[str, Any]:
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")
    return _objective_out_dict(conn, system_id, objective)


def get_objective_detail(conn: sqlite3.Connection, system_id: int, objective_key: str) -> Dict[str, Any]:
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    summary = _objective_out_dict(conn, system_id, objective)

    revision_out: Optional[Dict[str, Any]] = None
    if objective["current_revision_id"] is not None:
        try:
            row = conn.execute(
                "SELECT * FROM product_objective_revision WHERE id = ?", (objective["current_revision_id"],)
            ).fetchone()
            revision_out = _objective_revision_out_dict(dict(row)) if row is not None else None
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    upstream_refs: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_objective_upstream_ref
               WHERE system_id = ? AND objective_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, objective["id"]),
        ).fetchall()
        upstream_refs = [_upstream_ref_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "upstream_refs", exc)

    decisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_objective_decision
               WHERE system_id = ? AND objective_key = ? ORDER BY id DESC""",
            (system_id, objective_key),
        ).fetchall()
        decisions = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "decisions", exc)

    summary.update(
        {
            "current_revision": revision_out,
            "upstream_refs": upstream_refs,
            "decisions": decisions,
            "degraded_sections": degraded_sections,
            "degraded_detail": degraded_detail,
        }
    )
    return summary


def list_objectives(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    objectives: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM product_objective WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        objectives = [_objective_out_dict(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "objectives", exc)
    return {"objectives": objectives, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}


# ==============================================================================
# §4. Product Milestone
# ==============================================================================


def _get_milestone_row(conn: sqlite3.Connection, system_id: int, milestone_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM product_milestone WHERE system_id = ? AND milestone_key = ?", (system_id, milestone_key)
    ).fetchone()
    return dict(row) if row is not None else None


def create_milestone(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    objective_key: str,
    milestone_key: str,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The owning Objective is required at creation and NEVER changes
    afterward (§4.4) -- `objective_id` lives on the identity row."""
    now = time.time() if now is None else now
    if not milestone_key:
        raise KeyRequired("milestone")
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")
    if _get_milestone_row(conn, system_id, milestone_key) is not None:
        raise KeyConflict("milestone", milestone_key)
    cur = conn.execute(
        """INSERT INTO product_milestone (system_id, milestone_key, objective_id, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (system_id, milestone_key, objective["id"], created_by, now, now),
    )
    row = conn.execute("SELECT * FROM product_milestone WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def _milestone_current_digest(conn: sqlite3.Connection, milestone: Dict[str, Any]) -> str:
    if milestone["current_revision_id"] is None:
        return ""
    row = conn.execute(
        "SELECT content_digest FROM product_milestone_revision WHERE id = ?", (milestone["current_revision_id"],)
    ).fetchone()
    return row["content_digest"] if row is not None else ""


def derive_milestone_design_status(
    conn: sqlite3.Connection, system_id: int, milestone_key: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """§4.3: the latest non-superseded `product_milestone_decision` row --
    the DEFINITION ledger, never the achievement one. No row -> `proposed`."""
    row = conn.execute(
        """SELECT * FROM product_milestone_decision
           WHERE system_id = ? AND milestone_key = ? AND superseded_by_id IS NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, milestone_key),
    ).fetchone()
    if row is None:
        return "proposed", None
    decision_row = dict(row)
    return _MILESTONE_DECISION_TO_DESIGN_STATUS[decision_row["decision"]], decision_row


def derive_milestone_achievement(
    conn: sqlite3.Connection, system_id: int, milestone_key: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """§4.2/§4.3: the latest non-superseded `product_milestone_assessment`
    row -- the ACHIEVEMENT ledger, completely independent of
    `derive_milestone_design_status` above. No row -> `unassessed`."""
    row = conn.execute(
        """SELECT * FROM product_milestone_assessment
           WHERE system_id = ? AND milestone_key = ? AND superseded_by_id IS NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, milestone_key),
    ).fetchone()
    if row is None:
        return "unassessed", None
    assessment_row = dict(row)
    return _MILESTONE_ASSESSMENT_TO_ACHIEVEMENT[assessment_row["assessment"]], assessment_row


def derive_milestone_assessability(design_status: str, verification_method: str) -> str:
    """§4.2/§4.3, first match: an unavailable verification method always
    wins (there is no way to check this Milestone at all); otherwise a
    rejected/retired definition makes assessment moot; otherwise assessable.
    Never substitutes for `achievement` -- being unassessable is not the
    same fact as not having been met."""
    if verification_method == "unavailable":
        return "unavailable"
    if design_status in ("rejected", "retired"):
        return "not_applicable"
    return "assessable"


def add_milestone_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    milestone_key: str,
    title: str = "",
    target_state: str = "",
    verification_method: str = "unavailable",
    verification_note: str = "",
    sequence_hint: int = 0,
    summary: str = "",
    change_note: str = "",
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    _check_membership(verification_method, MILESTONE_VERIFICATION_METHODS, "verification_method")
    milestone = _get_milestone_row(conn, system_id, milestone_key)
    if milestone is None:
        raise NotFound(f"Milestone {milestone_key!r} not found")

    digest = milestone_revision_digest(
        title=title, target_state=target_state, verification_method=verification_method,
        verification_note=verification_note, summary=summary,
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = milestone["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM product_milestone_revision WHERE milestone_id = ?",
            (milestone["id"],),
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO product_milestone_revision
                   (milestone_id, system_id, revision_number, title, target_state, verification_method,
                    verification_note, sequence_hint, summary, content_digest, authored_by_kind,
                    decision_method, intelligence_run_id, change_note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                milestone["id"], system_id, revision_number, title, target_state, verification_method,
                verification_note, sequence_hint, summary, digest, authored_by_kind,
                decision_method, intelligence_run_id, change_note, created_by, now,
            ),
        )
        new_revision_id = cur.lastrowid
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE product_milestone_revision SET superseded_by_id = ? WHERE id = ?",
                (new_revision_id, prior_revision_id),
            )
        conn.execute(
            "UPDATE product_milestone SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, milestone["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_milestone_detail(conn, system_id, milestone_key)


def add_milestone_dependency(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    milestone_key: str,
    depends_on_milestone_key: str,
    rationale: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """§4.4: an ORDERING relationship, never an achievement gate (§6). Self-
    reference / cycle rejection uses the same iterative visited-set walk as
    `set_objective_parent`."""
    now = time.time() if now is None else now
    milestone = _get_milestone_row(conn, system_id, milestone_key)
    if milestone is None:
        raise NotFound(f"Milestone {milestone_key!r} not found")
    depends_on = _get_milestone_row(conn, system_id, depends_on_milestone_key)
    if depends_on is None:
        raise NotFound(f"Milestone {depends_on_milestone_key!r} not found")
    if milestone["id"] == depends_on["id"]:
        raise DependencySelfReference(milestone_key)
    if _would_create_cycle(
        conn, "product_milestone_dependency", "milestone_id", "depends_on_milestone_id",
        subject_id=milestone["id"], candidate_target_id=depends_on["id"],
    ):
        raise DependencyCycle(milestone_key)

    existing = conn.execute(
        """SELECT id FROM product_milestone_dependency
           WHERE system_id = ? AND milestone_id = ? AND depends_on_milestone_id = ? AND superseded_by_id IS NULL""",
        (system_id, milestone["id"], depends_on["id"]),
    ).fetchone()
    if existing is not None:
        raise DependencyDuplicate(milestone_key)

    conn.execute(
        """INSERT INTO product_milestone_dependency
               (system_id, milestone_id, depends_on_milestone_id, rationale, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (system_id, milestone["id"], depends_on["id"], rationale, decision_method, created_by, now),
    )
    return get_milestone_detail(conn, system_id, milestone_key)


def record_milestone_decision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    milestone_key: str,
    decision: str,
    rationale: str = "",
    captured_digest: str = "",
    decided_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The DEFINITION ledger write (§4.3), separate from
    `record_milestone_assessment` below (§1.3's two-axis rule)."""
    now = time.time() if now is None else now
    _check_membership(decision, MILESTONE_DECISION_KINDS, "decision")
    milestone = _get_milestone_row(conn, system_id, milestone_key)
    if milestone is None:
        raise NotFound(f"Milestone {milestone_key!r} not found")

    current_digest = _milestone_current_digest(conn, milestone)
    if captured_digest and captured_digest != current_digest:
        raise DecisionStaleDigest("milestone", milestone_key)

    prior_status, _ = derive_milestone_design_status(conn, system_id, milestone_key)
    _check_transition(_MILESTONE_TRANSITIONS, decision, prior_status, "milestone", milestone_key)

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_milestone_decision
               WHERE system_id = ? AND milestone_key = ? AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, milestone_key),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_milestone_decision
                   (system_id, milestone_id, milestone_key, decision, rationale, captured_digest,
                    captured_revision_id, decided_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, milestone["id"], milestone_key, decision, rationale, captured_digest,
                milestone["current_revision_id"], decided_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute("UPDATE product_milestone_decision SET superseded_by_id = ? WHERE id = ?", (new_id, prior["id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_milestone_decision WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


def record_milestone_assessment(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    milestone_key: str,
    assessment: str,
    rationale: str = "",
    evidence_note: str = "",
    captured_digest: str = "",
    assessed_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The ACHIEVEMENT ledger write (§4.3). Only reachable when the
    Milestone's `design_status` is `confirmed`
    (422 `product_milestone_not_assessable` otherwise) -- recording
    "achieved" against an undefined target names nothing. No transition
    table beyond that single precondition: any assessment kind is legal at
    any time once the definition is confirmed."""
    now = time.time() if now is None else now
    _check_membership(assessment, MILESTONE_ASSESSMENT_KINDS, "assessment")
    milestone = _get_milestone_row(conn, system_id, milestone_key)
    if milestone is None:
        raise NotFound(f"Milestone {milestone_key!r} not found")

    design_status, _ = derive_milestone_design_status(conn, system_id, milestone_key)
    if design_status != "confirmed":
        raise MilestoneNotAssessable(milestone_key)

    current_digest = _milestone_current_digest(conn, milestone)
    if captured_digest and captured_digest != current_digest:
        raise DecisionStaleDigest("milestone", milestone_key)

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_milestone_assessment
               WHERE system_id = ? AND milestone_key = ? AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, milestone_key),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_milestone_assessment
                   (system_id, milestone_id, milestone_key, assessment, rationale, evidence_note,
                    captured_digest, captured_revision_id, assessed_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, milestone["id"], milestone_key, assessment, rationale, evidence_note,
                captured_digest, milestone["current_revision_id"], assessed_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute("UPDATE product_milestone_assessment SET superseded_by_id = ? WHERE id = ?", (new_id, prior["id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_milestone_assessment WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


def _milestone_revision_out_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    return d


def _milestone_out_dict(conn: sqlite3.Connection, system_id: int, milestone: Dict[str, Any]) -> Dict[str, Any]:
    revision = None
    if milestone["current_revision_id"] is not None:
        rev = conn.execute(
            "SELECT * FROM product_milestone_revision WHERE id = ?", (milestone["current_revision_id"],)
        ).fetchone()
        revision = dict(rev) if rev is not None else None

    design_status, design_decision_row = derive_milestone_design_status(conn, system_id, milestone["milestone_key"])
    achievement, assessment_row = derive_milestone_achievement(conn, system_id, milestone["milestone_key"])
    verification_method = revision["verification_method"] if revision else "unavailable"
    assessability = derive_milestone_assessability(design_status, verification_method)

    current_digest = revision["content_digest"] if revision else ""
    # §6: "その Milestone の確定と達成判定が recheck_state='stale'" -- a content
    # change stales BOTH the definition confirmation and the achievement
    # judgement. `ProductMilestoneOut` carries exactly one `recheck_state`
    # field, so the worse of the two independently-derived recheck states
    # wins (stale > not_captured > current) -- a summary must never claim
    # `current` while EITHER judgement has actually drifted.
    design_recheck = derive_recheck_state(current_digest, design_decision_row)
    assessment_recheck = derive_recheck_state(current_digest, assessment_row)
    if "stale" in (design_recheck, assessment_recheck):
        recheck_state = "stale"
    elif "not_captured" in (design_recheck, assessment_recheck):
        recheck_state = "not_captured"
    else:
        recheck_state = "current"

    objective_row = conn.execute(
        "SELECT objective_key FROM product_objective WHERE id = ?", (milestone["objective_id"],)
    ).fetchone()

    return {
        "id": milestone["id"],
        "system_id": milestone["system_id"],
        "milestone_key": milestone["milestone_key"],
        "objective_id": milestone["objective_id"],
        "objective_key": objective_row["objective_key"] if objective_row else None,
        "current_revision_id": milestone["current_revision_id"],
        "current_revision_number": revision["revision_number"] if revision else None,
        "title": revision["title"] if revision else "",
        "design_status": design_status,
        "achievement": achievement,
        "assessability": assessability,
        "recheck_state": recheck_state,
        "created_by": milestone["created_by"],
        "created_at": milestone["created_at"],
        "updated_at": milestone["updated_at"],
    }


def get_milestone_summary(conn: sqlite3.Connection, system_id: int, milestone_key: str) -> Dict[str, Any]:
    milestone = _get_milestone_row(conn, system_id, milestone_key)
    if milestone is None:
        raise NotFound(f"Milestone {milestone_key!r} not found")
    return _milestone_out_dict(conn, system_id, milestone)


def get_milestone_detail(conn: sqlite3.Connection, system_id: int, milestone_key: str) -> Dict[str, Any]:
    milestone = _get_milestone_row(conn, system_id, milestone_key)
    if milestone is None:
        raise NotFound(f"Milestone {milestone_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    summary = _milestone_out_dict(conn, system_id, milestone)

    revision_out: Optional[Dict[str, Any]] = None
    if milestone["current_revision_id"] is not None:
        try:
            row = conn.execute(
                "SELECT * FROM product_milestone_revision WHERE id = ?", (milestone["current_revision_id"],)
            ).fetchone()
            revision_out = _milestone_revision_out_dict(dict(row)) if row is not None else None
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    dependencies: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT d.*, m2.milestone_key AS depends_on_milestone_key
               FROM product_milestone_dependency d
               JOIN product_milestone m2 ON m2.id = d.depends_on_milestone_id
               WHERE d.system_id = ? AND d.milestone_id = ? AND d.superseded_by_id IS NULL
               ORDER BY d.id DESC""",
            (system_id, milestone["id"]),
        ).fetchall()
        dependencies = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "dependencies", exc)

    decisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM product_milestone_decision WHERE system_id = ? AND milestone_key = ? ORDER BY id DESC",
            (system_id, milestone_key),
        ).fetchall()
        decisions = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "decisions", exc)

    assessments: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM product_milestone_assessment WHERE system_id = ? AND milestone_key = ? ORDER BY id DESC",
            (system_id, milestone_key),
        ).fetchall()
        assessments = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "assessments", exc)

    summary.update(
        {
            "current_revision": revision_out,
            "dependencies": dependencies,
            "decisions": decisions,
            "assessments": assessments,
            "degraded_sections": degraded_sections,
            "degraded_detail": degraded_detail,
        }
    )
    return summary


def list_milestones(conn: sqlite3.Connection, system_id: int, objective_key: str) -> Dict[str, Any]:
    objective = _get_objective_row(conn, system_id, objective_key)
    if objective is None:
        raise NotFound(f"Objective {objective_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    milestones: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM product_milestone WHERE system_id = ? AND objective_id = ? ORDER BY id DESC",
            (system_id, objective["id"]),
        ).fetchall()
        milestones = [_milestone_out_dict(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "milestones", exc)

    return {
        "objective_id": objective["id"],
        "objective_key": objective_key,
        "milestones": milestones,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


# ==============================================================================
# §5. Product Gap
# ==============================================================================


def _get_gap_row(conn: sqlite3.Connection, system_id: int, gap_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM product_gap WHERE system_id = ? AND gap_key = ?", (system_id, gap_key)).fetchone()
    return dict(row) if row is not None else None


def create_gap(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    milestone_key: str,
    gap_key: str,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The owning Milestone is required at creation and NEVER changes
    afterward (§5.2)."""
    now = time.time() if now is None else now
    if not gap_key:
        raise KeyRequired("gap")
    milestone = _get_milestone_row(conn, system_id, milestone_key)
    if milestone is None:
        raise NotFound(f"Milestone {milestone_key!r} not found")
    if _get_gap_row(conn, system_id, gap_key) is not None:
        raise KeyConflict("gap", gap_key)
    cur = conn.execute(
        """INSERT INTO product_gap (system_id, gap_key, milestone_id, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (system_id, gap_key, milestone["id"], created_by, now, now),
    )
    row = conn.execute("SELECT * FROM product_gap WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def _gap_current_digest(conn: sqlite3.Connection, gap: Dict[str, Any]) -> str:
    if gap["current_revision_id"] is None:
        return ""
    row = conn.execute(
        "SELECT content_digest FROM product_gap_revision WHERE id = ?", (gap["current_revision_id"],)
    ).fetchone()
    return row["content_digest"] if row is not None else ""


def derive_gap_lifecycle(conn: sqlite3.Connection, system_id: int, gap_key: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """§5.6: the latest non-superseded `product_gap_decision` row whose
    `decision != 'prioritize'` -- see the module docstring for why
    `prioritize` maintains its OWN separate "current" chain rather than
    sharing this one. No row -> `open` (§5.9's stated default)."""
    row = conn.execute(
        """SELECT * FROM product_gap_decision
           WHERE system_id = ? AND gap_key = ? AND decision != 'prioritize' AND superseded_by_id IS NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, gap_key),
    ).fetchone()
    if row is None:
        return "open", None
    decision_row = dict(row)
    return _GAP_DECISION_TO_LIFECYCLE[decision_row["decision"]], decision_row


def derive_gap_priority_band(conn: sqlite3.Connection, system_id: int, gap_key: str) -> str:
    """§5.7/§5.9: the latest non-superseded `prioritize` row's
    `priority_band`, independent of the lifecycle chain (§5.6/module
    docstring) -- so a `resolved` Gap still shows the last band a human
    placed on it. No row -> `unset`."""
    row = conn.execute(
        """SELECT priority_band FROM product_gap_decision
           WHERE system_id = ? AND gap_key = ? AND decision = 'prioritize' AND superseded_by_id IS NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, gap_key),
    ).fetchone()
    return row["priority_band"] if row is not None else "unset"


def add_gap_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    gap_key: str,
    title: str = "",
    current_state: str = "",
    target_state: str = "",
    target_state_mode: str = "unknown",
    interpretation: str = "",
    suggested_priority_note: str = "",
    change_note: str = "",
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    _check_membership(target_state_mode, GAP_TARGET_MODES, "target_state_mode")
    gap = _get_gap_row(conn, system_id, gap_key)
    if gap is None:
        raise NotFound(f"Gap {gap_key!r} not found")

    digest = gap_revision_digest(
        title=title, current_state=current_state, target_state=target_state,
        target_state_mode=target_state_mode, interpretation=interpretation,
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = gap["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM product_gap_revision WHERE gap_id = ?", (gap["id"],)
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO product_gap_revision
                   (gap_id, system_id, revision_number, title, current_state, target_state,
                    target_state_mode, interpretation, suggested_priority_note, content_digest,
                    authored_by_kind, decision_method, intelligence_run_id, change_note,
                    created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                gap["id"], system_id, revision_number, title, current_state, target_state,
                target_state_mode, interpretation, suggested_priority_note, digest,
                authored_by_kind, decision_method, intelligence_run_id, change_note,
                created_by, now,
            ),
        )
        new_revision_id = cur.lastrowid
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE product_gap_revision SET superseded_by_id = ? WHERE id = ?", (new_revision_id, prior_revision_id)
            )
        conn.execute(
            "UPDATE product_gap SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, gap["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_gap_detail(conn, system_id, gap_key)


def _unavailable_source_out(row: Dict[str, Any]) -> Dict[str, Any]:
    """§5.5/§5.10: a resolver failure (including the module not existing
    yet) degrades ONE source ref to `source_state='unavailable'` -- never
    `disappeared` (that would claim the source WAS read and the ref is
    gone, a different, stronger claim)."""
    return {
        "id": row["id"],
        "gap_id": row["gap_id"],
        "source_kind": row["source_kind"],
        "source_ref": row["source_ref"],
        "source_state": "unavailable",
        "title": None,
        "detail": None,
        "severity": None,
        "severity_vocabulary": None,
        "deep_link": None,
        "deep_link_state": "unavailable",
        "captured_digest": row["captured_digest"],
        "captured_snapshot_id": row["captured_snapshot_id"],
        "captured_run_id": row["captured_run_id"],
        "captured_revision_id": row["captured_revision_id"],
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def _gap_source_out_dict(
    conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[Exception]]:
    """Resolve one `product_gap_source_ref` row through
    `app/product_gap_sources.py` (Issue #430) at READ time (§5.4/§5.10).
    `product_gap_sources` is imported LAZILY so this module -- and its
    tests -- can be exercised before that module lands; any failure
    (`ImportError` included) degrades ONLY this one entry, never the
    caller's whole request. Returns `(out_dict, error_or_None)` and never
    raises."""
    try:
        from . import product_gap_sources
    except Exception as exc:  # ImportError before #430 lands, or any import-time failure
        return _unavailable_source_out(row), exc

    try:
        resolved = product_gap_sources.resolve_source(
            conn,
            system_id=system_id,
            source_kind=row["source_kind"],
            source_ref=row["source_ref"],
            captured_digest=row["captured_digest"],
            captured_snapshot_id=row["captured_snapshot_id"],
            captured_run_id=row["captured_run_id"],
            captured_revision_id=row["captured_revision_id"],
        )
    except Exception as exc:  # pragma: no cover - defensive; §5.10 says resolve_source should not raise except ValueError
        return _unavailable_source_out(row), exc

    out = {
        "id": row["id"],
        "gap_id": row["gap_id"],
        "source_kind": row["source_kind"],
        "source_ref": row["source_ref"],
        "source_state": resolved.source_state,
        "title": resolved.title,
        "detail": resolved.detail,
        "severity": resolved.severity,
        "severity_vocabulary": resolved.severity_vocabulary,
        "deep_link": resolved.deep_link,
        "deep_link_state": resolved.deep_link_state,
        "captured_digest": row["captured_digest"],
        "captured_snapshot_id": row["captured_snapshot_id"],
        "captured_run_id": row["captured_run_id"],
        "captured_revision_id": row["captured_revision_id"],
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }
    return out, None


def add_gap_source_ref(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    gap_key: str,
    source_kind: str,
    source_ref: str = "",
    note: str = "",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """§5.4/§5.10: creates a new `product_gap_source_ref` row. `captured_*`
    pins are deliberately NEVER supplied by the caller (§10.1's model
    docstring) -- this function determines `captured_digest` itself by
    resolving the source through `product_gap_sources.resolve_source` at
    creation time with no prior capture (`captured_digest=""`), which by
    §5.10's first-match table can only produce `unavailable` / `disappeared`
    / `contradicted` / `current` (never `changed`, since nothing was
    captured yet to have changed FROM).

    `captured_snapshot_id`/`captured_run_id`/`captured_revision_id` are left
    `None` here: determining the CORRECT pin for a given `source_kind`
    (§5.4's "追加 pin" column) is exactly the per-source-kind branching this
    module is not allowed to implement (`resolve_source` owns that). `None`
    pins are a legal call per §5.10's signature, and a source kind that
    genuinely needs a specific pin still degrades honestly through
    `source_state` rather than silently pinning the wrong point in time.
    """
    now = time.time() if now is None else now
    if source_kind not in GAP_SOURCE_KINDS:
        raise SourceKindInvalid(source_kind)
    gap = _get_gap_row(conn, system_id, gap_key)
    if gap is None:
        raise NotFound(f"Gap {gap_key!r} not found")

    existing = conn.execute(
        """SELECT id FROM product_gap_source_ref
           WHERE system_id = ? AND gap_id = ? AND source_kind = ? AND source_ref = ? AND superseded_by_id IS NULL""",
        (system_id, gap["id"], source_kind, source_ref),
    ).fetchone()
    if existing is not None:
        raise SourceDuplicate(gap_key)

    captured_digest = ""
    try:
        from . import product_gap_sources

        resolved = product_gap_sources.resolve_source(
            conn, system_id=system_id, source_kind=source_kind, source_ref=source_ref, captured_digest=""
        )
        captured_digest = resolved.current_digest
    except Exception:
        captured_digest = ""

    cur = conn.execute(
        """INSERT INTO product_gap_source_ref
               (system_id, gap_id, source_kind, source_ref, captured_digest, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'manual', ?, ?)""",
        (system_id, gap["id"], source_kind, source_ref, captured_digest, note, created_by, now),
    )
    row = dict(conn.execute("SELECT * FROM product_gap_source_ref WHERE id = ?", (cur.lastrowid,)).fetchone())
    out, _err = _gap_source_out_dict(conn, system_id, row)
    return out


def add_gap_evidence_ref(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    gap_key: str,
    evidence_kind: str,
    evidence_ref: str,
    note: str = "",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    _check_membership(evidence_kind, GAP_EVIDENCE_KINDS, "evidence_kind")
    gap = _get_gap_row(conn, system_id, gap_key)
    if gap is None:
        raise NotFound(f"Gap {gap_key!r} not found")
    cur = conn.execute(
        """INSERT INTO product_gap_evidence_ref
               (system_id, gap_id, evidence_kind, evidence_ref, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, 'manual', ?, ?)""",
        (system_id, gap["id"], evidence_kind, evidence_ref, note, created_by, now),
    )
    row = conn.execute("SELECT * FROM product_gap_evidence_ref WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def add_gap_artifact_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    gap_key: str,
    link_kind: str,
    target_ref: str,
    note: str = "",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """§1.5/§5.9: a Gap's DOWNSTREAM externalization/execution candidate --
    Issue Draft lands here, never in `product_gap_source_ref`."""
    now = time.time() if now is None else now
    if link_kind not in GAP_ARTIFACT_LINK_KINDS:
        raise LinkKindInvalid(link_kind)
    gap = _get_gap_row(conn, system_id, gap_key)
    if gap is None:
        raise NotFound(f"Gap {gap_key!r} not found")
    existing = conn.execute(
        """SELECT id FROM product_gap_artifact_link
           WHERE system_id = ? AND gap_id = ? AND link_kind = ? AND target_ref = ? AND superseded_by_id IS NULL""",
        (system_id, gap["id"], link_kind, target_ref),
    ).fetchone()
    if existing is not None:
        raise ArtifactDuplicate(gap_key)
    cur = conn.execute(
        """INSERT INTO product_gap_artifact_link
               (system_id, gap_id, link_kind, target_ref, captured_digest, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, '', ?, 'manual', ?, ?)""",
        (system_id, gap["id"], link_kind, target_ref, note, created_by, now),
    )
    row = conn.execute("SELECT * FROM product_gap_artifact_link WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def record_gap_decision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    gap_key: str,
    decision: str,
    priority_band: str = "unset",
    rationale: str = "",
    captured_digest: str = "",
    decided_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """§5.6/§5.7's ONE write behind BOTH `lifecycle` and `priority_band`.
    See the module docstring for why the two axes maintain SEPARATE
    "current" pointers on this shared table: a `prioritize` decision
    supersedes only the prior `prioritize` row; every other decision
    supersedes only the prior non-`prioritize` row. `priority_band` is
    stored as `"unset"` on every row except a `prioritize` one (§5.9's
    `ProductGapDecisionOut` docstring)."""
    now = time.time() if now is None else now
    _check_membership(decision, GAP_DECISION_KINDS, "decision")
    gap = _get_gap_row(conn, system_id, gap_key)
    if gap is None:
        raise NotFound(f"Gap {gap_key!r} not found")

    current_digest = _gap_current_digest(conn, gap)
    if captured_digest and captured_digest != current_digest:
        raise DecisionStaleDigest("gap", gap_key)

    prior_lifecycle, _ = derive_gap_lifecycle(conn, system_id, gap_key)

    if decision == "prioritize":
        _check_membership(priority_band, GAP_PRIORITY_BANDS, "priority_band")
        if prior_lifecycle not in _GAP_PRIORITIZE_LEGAL_FROM:
            raise NotDecidable("gap", gap_key)
        axis_filter = "decision = 'prioritize'"
        stored_priority_band = priority_band
    else:
        _check_transition(_GAP_TRANSITIONS, decision, prior_lifecycle, "gap", gap_key)
        axis_filter = "decision != 'prioritize'"
        stored_priority_band = "unset"

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            f"SELECT id FROM product_gap_decision WHERE system_id = ? AND gap_key = ? AND {axis_filter} "  # noqa: S608 - axis_filter is one of two fixed constants above, never caller input
            f"AND superseded_by_id IS NULL ORDER BY id DESC LIMIT 1",
            (system_id, gap_key),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_gap_decision
                   (system_id, gap_id, gap_key, decision, priority_band, rationale, captured_digest,
                    captured_revision_id, decided_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, gap["id"], gap_key, decision, stored_priority_band, rationale, captured_digest,
                gap["current_revision_id"], decided_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute("UPDATE product_gap_decision SET superseded_by_id = ? WHERE id = ?", (new_id, prior["id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_gap_decision WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


def _gap_revision_out_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    return d


def _gap_read_flags(conn: sqlite3.Connection, system_id: int, gap: Dict[str, Any], lifecycle: str) -> List[str]:
    """§6's read-time-only advisory flags, recomputed on every read and
    NEVER persisted or a `ProductGapLifecycle` value. `contradicted` is
    §5.4's explicit "close ではなくreopenを検討すべき候補" -- so it maps to
    `close_candidate` here (a source saying the condition no longer holds,
    for a Gap that is still open-ish, nudges toward resolving it);
    `disappeared` (the source_ref itself is gone) maps to `reopen_candidate`
    by elimination, for a Gap that is already in a closed-ish state (a
    vanished reference is ambiguous evidence that warrants a second look
    before trusting the closure). Each flag is gated by whether its
    corresponding decision is actually LEGAL from the current `lifecycle`
    (`_CLOSE_CANDIDATE_LIFECYCLES` / `_REOPEN_CANDIDATE_LIFECYCLES`) so a
    flag never suggests an action the developer cannot currently take.
    """
    rows = conn.execute(
        "SELECT * FROM product_gap_source_ref WHERE system_id = ? AND gap_id = ? AND superseded_by_id IS NULL",
        (system_id, gap["id"]),
    ).fetchall()
    states: List[str] = []
    for row in rows:
        out, _err = _gap_source_out_dict(conn, system_id, dict(row))
        states.append(out["source_state"])

    flags: List[str] = []
    if "changed" in states:
        flags.append("recheck_required")
    if "contradicted" in states and lifecycle in _CLOSE_CANDIDATE_LIFECYCLES:
        flags.append("close_candidate")
    if "disappeared" in states and lifecycle in _REOPEN_CANDIDATE_LIFECYCLES:
        flags.append("reopen_candidate")
    return flags


def _gap_out_dict(conn: sqlite3.Connection, system_id: int, gap: Dict[str, Any]) -> Dict[str, Any]:
    revision = None
    if gap["current_revision_id"] is not None:
        rev = conn.execute("SELECT * FROM product_gap_revision WHERE id = ?", (gap["current_revision_id"],)).fetchone()
        revision = dict(rev) if rev is not None else None

    lifecycle, lifecycle_row = derive_gap_lifecycle(conn, system_id, gap["gap_key"])
    priority_band = derive_gap_priority_band(conn, system_id, gap["gap_key"])
    current_digest = revision["content_digest"] if revision else ""
    recheck_state = derive_recheck_state(current_digest, lifecycle_row)
    read_flags = _gap_read_flags(conn, system_id, gap, lifecycle)

    milestone_row = conn.execute(
        "SELECT milestone_key, objective_id FROM product_milestone WHERE id = ?", (gap["milestone_id"],)
    ).fetchone()
    objective_id = milestone_row["objective_id"] if milestone_row is not None else None
    objective_key = None
    if objective_id is not None:
        obj_row = conn.execute("SELECT objective_key FROM product_objective WHERE id = ?", (objective_id,)).fetchone()
        objective_key = obj_row["objective_key"] if obj_row is not None else None

    return {
        "id": gap["id"],
        "system_id": gap["system_id"],
        "gap_key": gap["gap_key"],
        "milestone_id": gap["milestone_id"],
        "milestone_key": milestone_row["milestone_key"] if milestone_row is not None else None,
        "objective_id": objective_id,
        "objective_key": objective_key,
        "current_revision_id": gap["current_revision_id"],
        "current_revision_number": revision["revision_number"] if revision else None,
        "title": revision["title"] if revision else "",
        "lifecycle": lifecycle,
        "priority_band": priority_band,
        "recheck_state": recheck_state,
        "read_flags": read_flags,
        "created_by": gap["created_by"],
        "created_at": gap["created_at"],
        "updated_at": gap["updated_at"],
    }


def get_gap_summary(conn: sqlite3.Connection, system_id: int, gap_key: str) -> Dict[str, Any]:
    gap = _get_gap_row(conn, system_id, gap_key)
    if gap is None:
        raise NotFound(f"Gap {gap_key!r} not found")
    return _gap_out_dict(conn, system_id, gap)


def get_gap_detail(conn: sqlite3.Connection, system_id: int, gap_key: str) -> Dict[str, Any]:
    gap = _get_gap_row(conn, system_id, gap_key)
    if gap is None:
        raise NotFound(f"Gap {gap_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    summary = _gap_out_dict(conn, system_id, gap)

    revision_out: Optional[Dict[str, Any]] = None
    if gap["current_revision_id"] is not None:
        try:
            row = conn.execute(
                "SELECT * FROM product_gap_revision WHERE id = ?", (gap["current_revision_id"],)
            ).fetchone()
            revision_out = _gap_revision_out_dict(dict(row)) if row is not None else None
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    source_refs: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_gap_source_ref
               WHERE system_id = ? AND gap_id = ? AND superseded_by_id IS NULL ORDER BY id DESC""",
            (system_id, gap["id"]),
        ).fetchall()
        for r in rows:
            out, err = _gap_source_out_dict(conn, system_id, dict(r))
            source_refs.append(out)
            if err is not None:
                _degrade(degraded_sections, degraded_detail, f"source_ref:{r['source_kind']}:{r['id']}", err)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "source_refs", exc)

    evidence_refs: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_gap_evidence_ref
               WHERE system_id = ? AND gap_id = ? AND superseded_by_id IS NULL ORDER BY id DESC""",
            (system_id, gap["id"]),
        ).fetchall()
        evidence_refs = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "evidence_refs", exc)

    artifact_links: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_gap_artifact_link
               WHERE system_id = ? AND gap_id = ? AND superseded_by_id IS NULL ORDER BY id DESC""",
            (system_id, gap["id"]),
        ).fetchall()
        artifact_links = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "artifact_links", exc)

    decisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM product_gap_decision WHERE system_id = ? AND gap_key = ? ORDER BY id DESC",
            (system_id, gap_key),
        ).fetchall()
        decisions = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "decisions", exc)

    summary.update(
        {
            "current_revision": revision_out,
            "source_refs": source_refs,
            "evidence_refs": evidence_refs,
            "artifact_links": artifact_links,
            "decisions": decisions,
            "degraded_sections": degraded_sections,
            "degraded_detail": degraded_detail,
        }
    )
    return summary


def list_gaps(conn: sqlite3.Connection, system_id: int, milestone_key: Optional[str] = None) -> Dict[str, Any]:
    milestone_id: Optional[int] = None
    if milestone_key is not None:
        milestone = _get_milestone_row(conn, system_id, milestone_key)
        if milestone is None:
            raise NotFound(f"Milestone {milestone_key!r} not found")
        milestone_id = milestone["id"]

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    gaps: List[Dict[str, Any]] = []
    try:
        if milestone_id is not None:
            rows = conn.execute(
                "SELECT * FROM product_gap WHERE system_id = ? AND milestone_id = ? ORDER BY id DESC",
                (system_id, milestone_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM product_gap WHERE system_id = ? ORDER BY id DESC", (system_id,)
            ).fetchall()
        gaps = [_gap_out_dict(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "gaps", exc)

    return {
        "milestone_id": milestone_id,
        "milestone_key": milestone_key,
        "gaps": gaps,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }
