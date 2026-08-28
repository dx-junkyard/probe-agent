"""UX Design Lineage: Journey / Step / Requirement / Acceptance Criterion /
Artifact Reference (Issue #407, Epic #405).

`docs/ux-design-lineage.md` is the canonical contract; §0 and §2 are this
module's specification. This is a deterministic domain service -- **no LLM
call anywhere in this module** (Principle 6). Everything a caller can ask
this module for is either a direct read of a persisted row, a first-match
classification over a small finite vocabulary, or an append-only write of
content the caller supplied.

Two rules this module must never violate (§0 / §1):

* **No new understanding model, no upstream/downstream content copies.**
  Journey / Requirement content is genuinely new and IS stored here, but a
  reference to Purpose/Capability (`ux_journey_upstream_ref`) or to a
  Journey Step (`ux_requirement_step_link`) never copies the target's
  content -- only a `target_ref` plus a `captured_digest`, resolved against
  exactly one canonical source per kind at READ time
  (`node_design._LINK_KIND_TARGET_SOURCE`'s discipline, reused here as
  `_resolve_upstream_target` / `_resolve_step_target`).
* **`design_status` is derived, never stored** (§2.5): the latest
  non-superseded `ux_design_decision` row for `(system_id, subject_kind,
  subject_key)`, folded through a fixed 4-row mapping. `recheck_state` is a
  SEPARATE axis -- a stale `confirmed` item stays `confirmed`
  (#337/#338/#349's "a stored lifecycle value can drift from the rows it
  describes, a derived one cannot", applied here to a decision ledger
  instead of an event log).

Connection discipline (`.claude/skills/control-server/SKILL.md`): every
function here except the artifact-reference read/write path takes an
already-open `conn` and performs no external call. Artifact verification
shells out to `git`, so `create_artifact_reference` follows
`snapshot_preflight.gather_preflight`'s own shape instead -- it takes NO
`conn` parameter, opens and closes its own short-lived connections around
the `git` subprocess call, and must be called with no `get_conn()`
connection already open on the calling thread.

probe-agent:
  role: Deterministic UX Journey / Requirement / Artifact Reference domain service
  capability: ux-design-lineage
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify every Journey/Requirement revision is append-only with a reproducible content digest, that design_status is always derived from the decision ledger rather than stored, that an upstream/downstream reference never copies target content, and that artifact verification never performs an outbound HTTP request.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, get_args

from . import git_ops, purpose_chain, state_facts
from .db import get_conn
from .git_ops import GitError
from .models import (
    UxArtifactKind,
    UxArtifactSubjectKind,
    UxArtifactVerificationState,
    UxDesignAuthorshipKind,
    UxDesignDecisionKind,
    UxDesignRecheckState,
    UxDesignStatus,
    UxDesignSubjectKind,
    UxDiffChangeKind,
    UxDiffState,
    UxEvidenceSourceKind,
    UxJourneyBaselineMode,
    UxJourneyBaselineState,
    UxJourneyPerspective,
    UxRefKind,
    UxRefRecheckState,
    UxRefRelationStatus,
    UxRefTargetResolution,
    UxRequirementKind,
    UxRevisionState,
    UxVerificationMethod,
)

__all__ = [
    "UxDesignError",
    "NotFound",
    "KeyRequired",
    "KeyConflict",
    "BaselineNotAsIs",
    "BaselineForeignSystem",
    "StepKeyDuplicated",
    "StepNotFound",
    "CriterionKeyDuplicated",
    "OutOfScopeNotVerifiable",
    "ArtifactUriInvalid",
    "ArtifactHashRequired",
    "ArtifactHashInvalid",
    "SubjectNotFound",
    "DecisionStaleDigest",
    "NotDecidable",
    "content_digest",
    "journey_step_digest",
    "journey_revision_digest",
    "acceptance_criterion_digest",
    "requirement_revision_digest",
    "create_journey",
    "add_journey_revision",
    "journey_baseline_state",
    "get_journey_detail",
    "list_journeys",
    "list_journey_revisions",
    "diff_journey_revisions",
    "baseline_diff_journey",
    "add_upstream_ref",
    "create_requirement",
    "add_requirement_revision",
    "get_requirement_detail",
    "list_requirements",
    "list_requirement_revisions",
    "diff_requirement_revisions",
    "add_requirement_step_link",
    "derive_design_status",
    "derive_recheck_state",
    "record_design_decision",
    "parse_repo_artifact_uri",
    "create_artifact_reference",
]


# --- §0. Finite vocabularies, mirrored from app/models.py with get_args -------

PERSPECTIVES: Tuple[str, ...] = get_args(UxJourneyPerspective)
BASELINE_MODES: Tuple[str, ...] = get_args(UxJourneyBaselineMode)
BASELINE_STATES: Tuple[str, ...] = get_args(UxJourneyBaselineState)
AUTHORSHIP_KINDS: Tuple[str, ...] = get_args(UxDesignAuthorshipKind)
EVIDENCE_SOURCE_KINDS: Tuple[str, ...] = get_args(UxEvidenceSourceKind)
REQUIREMENT_KINDS: Tuple[str, ...] = get_args(UxRequirementKind)
VERIFICATION_METHODS: Tuple[str, ...] = get_args(UxVerificationMethod)
DESIGN_STATUSES: Tuple[str, ...] = get_args(UxDesignStatus)
DESIGN_DECISION_KINDS: Tuple[str, ...] = get_args(UxDesignDecisionKind)
DESIGN_RECHECK_STATES: Tuple[str, ...] = get_args(UxDesignRecheckState)
REVISION_STATES: Tuple[str, ...] = get_args(UxRevisionState)
REF_KINDS: Tuple[str, ...] = get_args(UxRefKind)
REF_RELATION_STATUSES: Tuple[str, ...] = get_args(UxRefRelationStatus)
REF_TARGET_RESOLUTIONS: Tuple[str, ...] = get_args(UxRefTargetResolution)
REF_RECHECK_STATES: Tuple[str, ...] = get_args(UxRefRecheckState)
ARTIFACT_KINDS: Tuple[str, ...] = get_args(UxArtifactKind)
ARTIFACT_VERIFICATION_STATES: Tuple[str, ...] = get_args(UxArtifactVerificationState)
ARTIFACT_SUBJECT_KINDS: Tuple[str, ...] = get_args(UxArtifactSubjectKind)
DESIGN_SUBJECT_KINDS: Tuple[str, ...] = get_args(UxDesignSubjectKind)
DIFF_CHANGE_KINDS: Tuple[str, ...] = get_args(UxDiffChangeKind)
DIFF_STATES: Tuple[str, ...] = get_args(UxDiffState)

#: §2.7's fixed translation from a reference/link's own `decision_method`
#: to WHO ASSERTED it -- never a second stored status column, and the same
#: table `node_design._DECISION_METHOD_TO_RELATION_STATUS` uses one layer
#: over in the Evolution Node design lineage.
_DECISION_METHOD_TO_RELATION_STATUS: Dict[str, str] = {
    "manual": "confirmed",
    "reasoning_llm": "proposed",
    "deterministic": "derived",
}

#: §2.5's fixed decision-ledger fold: the latest non-superseded
#: `ux_design_decision.decision` value -> the DERIVED `design_status`.
_DECISION_TO_DESIGN_STATUS: Dict[str, str] = {
    "confirm": "confirmed",
    "reject": "rejected",
    "retire": "retired",
    "reinstate": "proposed",
}


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise UxDesignValidationError(
            f"{field_name} must be one of {', '.join(vocabulary)}; got {value!r}"
        )


# --- Errors -------------------------------------------------------------------


class UxDesignError(ValueError):
    """Base class for every failure this module raises."""


class UxDesignValidationError(UxDesignError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class NotFound(UxDesignError):
    """A referenced row does not exist, or belongs to another System.

    The two are deliberately the same error -- telling them apart would let
    a caller probe another System's ids (the same rule
    `node_design.NodeDesignNotFoundError` documents).
    """


class KeyRequired(UxDesignError):
    """`journey_key` / `requirement_key` / `step_key` / `criterion_key` was
    empty (422 `ux_design_key_required`, §2.2)."""


class KeyConflict(UxDesignError):
    """A `journey_key` / `requirement_key` already exists in this System
    (409 `ux_design_key_conflict`)."""


class BaselineNotAsIs(UxDesignError):
    """`baseline_journey_id` points at a `to_be` Journey (422
    `journey_baseline_not_as_is`)."""


class BaselineForeignSystem(NotFound):
    """`baseline_journey_id` does not resolve in this System (404
    `journey_baseline_foreign_system`)."""


class StepKeyDuplicated(UxDesignError):
    """Two Steps in the same revision share a `step_key` (422
    `journey_step_key_duplicated`)."""


class StepNotFound(NotFound):
    """A step-link's `step_key` is not in the Journey's CURRENT revision
    (404 `journey_step_not_found`)."""


class CriterionKeyDuplicated(UxDesignError):
    """Two Acceptance Criteria in the same revision share a `criterion_key`.

    Not one of §2.10's officially named reject codes, but defensively
    checked before the write: `ux_requirement_acceptance_criterion`'s own
    `UNIQUE (requirement_revision_id, criterion_key)` constraint would
    otherwise surface as a raw `sqlite3.IntegrityError`.
    """


class OutOfScopeNotVerifiable(UxDesignError):
    """An `out_of_scope` Requirement was given acceptance criteria (422
    `out_of_scope_requirement_not_verifiable`, §2.11 item 8)."""


class ArtifactUriInvalid(UxDesignError):
    """A `repo:<path>` artifact URI failed path-traversal validation (422
    `artifact_uri_invalid`)."""


class ArtifactHashRequired(UxDesignError):
    """`content_hash` was empty (422 `artifact_hash_required`)."""


class ArtifactHashInvalid(UxDesignError):
    """`content_hash` is not a 64-character hexadecimal SHA-256 digest."""


class SubjectNotFound(NotFound):
    """A decision's subject does not exist (404
    `ux_design_subject_not_found`)."""


class DecisionStaleDigest(UxDesignError):
    """The caller's `captured_digest` does not match the subject's current
    content digest (409 `ux_design_decision_stale_digest`)."""


class NotDecidable(UxDesignError):
    """The requested decision is illegal from the subject's current
    `design_status` (422 `ux_design_not_decidable`) -- e.g. `confirm` on a
    `retired` subject, or `reinstate` on a `proposed` one."""


def _degrade(
    degraded_sections: List[str],
    degraded_detail: Dict[str, str],
    section: str,
    exc: Exception,
) -> None:
    """Record one section as degraded without ever substituting a guessed
    value for what it failed to read (§0 invariant 6, the #380/#388
    discipline `purpose_chain._degrade` also follows)."""
    if section not in degraded_sections:
        degraded_sections.append(section)
    degraded_detail[section] = f"{type(exc).__name__}: {exc}"


# --- §2.6. Digests --------------------------------------------------------------


def content_digest(payload: Dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON form of `payload` (§2.6).

    Same canonicalization `purpose_chain.element_digest` /
    `understanding_brief.claim_digest` use: `sort_keys=True,
    ensure_ascii=False, separators=(",", ":")`. List ORDER is preserved by
    `json.dumps` (only dict keys are sorted), which is why every caller
    below sorts its own list payload deterministically before handing it to
    this function.
    """
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def journey_step_digest(
    *,
    step_key: str,
    user_intent: str,
    system_response: str,
    success_criteria: str,
    failure_mode: str,
    recovery_path: str,
    evidence_expectation: str,
    evidence_source_kind: str,
) -> str:
    """§2.6's Journey Step digest.

    `step_order` is deliberately EXCLUDED here: reordering Steps changes the
    JOURNEY's meaning (captured in `journey_revision_digest` below), not
    this Step's own meaning -- a Step read in isolation says the same thing
    regardless of its position in the sequence.
    """
    return content_digest(
        {
            "step_key": step_key,
            "user_intent": user_intent,
            "system_response": system_response,
            "success_criteria": success_criteria,
            "failure_mode": failure_mode,
            "recovery_path": recovery_path,
            "evidence_expectation": evidence_expectation,
            "evidence_source_kind": evidence_source_kind,
        }
    )


def journey_revision_digest(
    *,
    title: str,
    beneficiary: str,
    usage_context: str,
    entry_trigger: str,
    value_arrival: str,
    summary: str,
    steps: Sequence[Dict[str, Any]],
) -> str:
    """§2.6's Journey revision digest.

    `created_by` / `created_at` / `revision_number` / `change_note` are
    deliberately EXCLUDED -- a recheck must fire on a MEANING change, never
    on the mere existence of a new record (#308's `confirmation_id`
    exclusion, #337's Intent `status` exclusion, applied here). `step_order`
    IS included via each step's `(step_key, step_order, content_digest)`
    triple -- unlike the Step's own digest above, reordering Steps changes
    what this JOURNEY means. The step list is sorted by `step_order` before
    hashing so the digest is reproducible regardless of the caller's own
    iteration order.
    """
    step_payload = sorted(
        (
            {
                "step_key": s["step_key"],
                "step_order": s["step_order"],
                "content_digest": s["content_digest"],
            }
            for s in steps
        ),
        key=lambda s: (s["step_order"], s["step_key"]),
    )
    return content_digest(
        {
            "title": title,
            "beneficiary": beneficiary,
            "usage_context": usage_context,
            "entry_trigger": entry_trigger,
            "value_arrival": value_arrival,
            "summary": summary,
            "steps": step_payload,
        }
    )


def acceptance_criterion_digest(
    *,
    criterion_key: str,
    statement: str,
    verification_method: str,
    verification_note: str,
) -> str:
    """§2.6's Acceptance Criterion digest. `criterion_order` is excluded --
    unlike a Journey Step, criterion order carries no meaning of its own
    (§2.6's table lists only `(criterion_key, content_digest)` for the
    Requirement revision digest below, with no order component at all)."""
    return content_digest(
        {
            "criterion_key": criterion_key,
            "statement": statement,
            "verification_method": verification_method,
            "verification_note": verification_note,
        }
    )


def requirement_revision_digest(
    *,
    requirement_kind: str,
    statement: str,
    rationale: str,
    constraint_text: str,
    out_of_scope_note: str,
    criteria: Sequence[Dict[str, Any]],
) -> str:
    """§2.6's Requirement revision digest.

    `created_by` / `created_at` / `revision_number` / `change_note` are
    excluded for the same reason `journey_revision_digest` excludes them.
    Unlike the Journey revision digest, criterion ORDER is not part of the
    input (§2.6's table); the criteria list is sorted by `criterion_key`
    purely so the digest is reproducible regardless of iteration order.
    """
    criteria_payload = sorted(
        (
            {"criterion_key": c["criterion_key"], "content_digest": c["content_digest"]}
            for c in criteria
        ),
        key=lambda c: c["criterion_key"],
    )
    return content_digest(
        {
            "requirement_kind": requirement_kind,
            "statement": statement,
            "rationale": rationale,
            "constraint_text": constraint_text,
            "out_of_scope_note": out_of_scope_note,
            "criteria": criteria_payload,
        }
    )


def _purpose_relation_digest(relation: "purpose_chain.PurposeRelation") -> str:
    """A local digest for a Purpose Chain RELATION, used only as the
    "target's own digest" a `purpose_relation`-kind upstream ref compares
    against (§2.7). `purpose_chain` exposes `element_digest` for elements
    but no relation-level digest of its own; this stays local rather than
    growing that module's public surface for a need only this layer has.
    """
    return content_digest(
        {
            "kind": relation.kind,
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "status": relation.status,
            "provenance": relation.provenance,
        }
    )


# --- §2.2 / §2.3. Journey identity, baseline state -----------------------------


def _get_journey_row(conn: sqlite3.Connection, system_id: int, journey_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM ux_journey WHERE system_id = ? AND journey_key = ?",
        (system_id, journey_key),
    ).fetchone()
    return dict(row) if row is not None else None


def journey_baseline_state(
    conn: sqlite3.Connection, system_id: int, journey: Dict[str, Any]
) -> str:
    """§2.3's 5-row first-match `baseline_state` derivation.

    1. `as_is` Journeys never have a baseline -> `not_applicable`.
    2. `baseline_mode == "greenfield"` is an explicit developer declaration
       that there is nothing to compare against -> `not_applicable`.
    3. `baseline_mode == "undecided"` -> `absent` (honest "nobody decided").
    4. `baseline_journey_id` resolves to an `as_is` Journey in the SAME
       System right now -> `linked`.
    5. Otherwise (an id that does not resolve, or none at all while the
       mode is `linked`) -> `unresolved`.
    """
    if journey["perspective"] == "as_is":
        return "not_applicable"
    mode = journey["baseline_mode"]
    if mode == "greenfield":
        return "not_applicable"
    if mode == "undecided":
        return "absent"
    baseline_id = journey["baseline_journey_id"]
    if baseline_id is not None:
        target = conn.execute(
            "SELECT id FROM ux_journey WHERE id = ? AND system_id = ? AND perspective = 'as_is'",
            (baseline_id, system_id),
        ).fetchone()
        if target is not None:
            return "linked"
    return "unresolved"


def create_journey(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    journey_key: str,
    perspective: str,
    baseline_mode: str = "undecided",
    baseline_journey_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not journey_key:
        raise KeyRequired("journey_key")
    _check_membership(perspective, PERSPECTIVES, "perspective")
    _check_membership(baseline_mode, BASELINE_MODES, "baseline_mode")
    if _get_journey_row(conn, system_id, journey_key) is not None:
        raise KeyConflict(journey_key)

    if baseline_journey_id is not None:
        target = conn.execute(
            "SELECT * FROM ux_journey WHERE id = ? AND system_id = ?",
            (baseline_journey_id, system_id),
        ).fetchone()
        if target is None:
            raise BaselineForeignSystem(baseline_journey_id)
        if target["perspective"] != "as_is":
            raise BaselineNotAsIs(baseline_journey_id)

    cur = conn.execute(
        """INSERT INTO ux_journey
               (system_id, journey_key, perspective, baseline_mode, baseline_journey_id,
                created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (system_id, journey_key, perspective, baseline_mode, baseline_journey_id, created_by, now, now),
    )
    row = conn.execute("SELECT * FROM ux_journey WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


# --- §2.5. design_status / recheck_state (shared: journey + requirement) ------


def derive_design_status(
    conn: sqlite3.Connection, system_id: int, subject_kind: str, subject_key: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """§2.5: the latest non-superseded `ux_design_decision` row, folded
    through the fixed 4-row mapping. No row -> `proposed`. Returns the
    status plus the decision row itself (or `None`), so callers needing the
    row's `captured_digest` for `recheck_state` do not requery."""
    row = conn.execute(
        """SELECT * FROM ux_design_decision
           WHERE system_id = ? AND subject_kind = ? AND subject_key = ?
             AND superseded_by_id IS NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, subject_kind, subject_key),
    ).fetchone()
    if row is None:
        return "proposed", None
    decision_row = dict(row)
    return _DECISION_TO_DESIGN_STATUS[decision_row["decision"]], decision_row


def derive_recheck_state(current_digest: str, decision_row: Optional[Dict[str, Any]]) -> str:
    """§2.5: `stale` only when the CURRENTLY effective decision is a
    `confirm` whose `captured_digest` no longer matches. `design_status`
    stays `confirmed` regardless -- the decision row itself is never
    touched; only this independent axis moves."""
    if decision_row is not None and decision_row["decision"] == "confirm":
        if decision_row["captured_digest"] != current_digest:
            return "stale"
    return "current"


# --- §2.7. Upstream reference resolution (purpose_element / purpose_relation /
#     capability_entity), each against its own canonical source -------------


def _resolve_capability_entity(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    """Resolve against Issue #312's canonical Capability identity, the same
    approach `node_design._resolve_capability` uses one layer over (plus a
    digest, which that function does not need to compute)."""
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


def _resolve_purpose_target(
    conn: sqlite3.Connection, system_id: int, ref_kind: str, target_ref: str
) -> Dict[str, Any]:
    """Resolve `purpose_element` / `purpose_relation` against
    `purpose_chain.derive_purpose_chain`'s freshly-computed projection
    (§2.7's table). A derivation failure is `unavailable` -- the source
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

    # purpose_relation
    relation = next((r for r in chain.relations if r.id == target_ref), None)
    if relation is None:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    return {
        "resolution": "resolved",
        "name": None,
        "state": relation.status,
        "digest": _purpose_relation_digest(relation),
    }


#: Issue #427/#431 §7.1's three new upstream ref kinds, each resolved
#: against its OWN canonical identity+revision table -- never translated
#: into `UxDesignStatus` (the #380 superset rule: `target_state` below
#: carries `objective_state` / Milestone `design_status` / Gap `lifecycle`
#: verbatim). `product_milestone_decision`'s fold is IDENTICAL to this
#: module's own `_DECISION_TO_DESIGN_STATUS` (confirm/reject/retire/
#: reinstate -> confirmed/rejected/retired/proposed), so it is reused
#: rather than re-declared.
#:
#: DUPLICATION NOTE (to collapse once Issue #429's `app/product_objective.py`
#: lands): `app/product_objective.py` is being written concurrently by
#: another agent and did not exist yet when this resolver was written, so
#: the objective_state (§4.3) and gap lifecycle (§5.6) ledger-folds below
#: are reproduced locally from the contract rather than imported. Once that
#: module exposes its own derivation helper for each, these three local
#: fold tables/functions should be deleted and replaced with calls into it
#: (see docs/product-objective-lineage.md §4.2/§4.3/§5.6).
_OBJECTIVE_DECISION_TO_STATE: Dict[str, str] = {
    "confirm": "confirmed",
    "activate": "active",
    "achieve": "achieved",
    "reject": "rejected",
    "retire": "retired",
    "reinstate": "proposed",
}

_GAP_DECISION_TO_LIFECYCLE: Dict[str, str] = {
    "acknowledge": "acknowledged",
    "defer": "deferred",
    "resolve": "resolved",
    "reject": "rejected",
    "retire": "obsolete",
    "reopen": "open",
}


def _fold_latest_decision(
    conn: sqlite3.Connection,
    table: str,
    system_id: int,
    key_column: str,
    key_value: str,
    fold_table: Dict[str, str],
    default: str,
    exclude_decisions: Tuple[str, ...] = (),
) -> str:
    """The shared shape of §4.3/§5.6's ledger folds: the latest
    non-superseded decision row for one key, mapped through a fixed table.
    `table` / `key_column` are always one of this module's own hardcoded
    constants below, never caller input, so building the query with an
    f-string carries no injection risk."""
    query = (
        f"SELECT decision FROM {table} "  # noqa: S608 - table/key_column are fixed constants, never caller input
        f"WHERE system_id = ? AND {key_column} = ? AND superseded_by_id IS NULL"
    )
    params: List[Any] = [system_id, key_value]
    if exclude_decisions:
        placeholders = ", ".join("?" for _ in exclude_decisions)
        query += f" AND decision NOT IN ({placeholders})"
        params.extend(exclude_decisions)
    query += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    if row is None:
        return default
    return fold_table.get(row["decision"], default)


def _resolve_product_objective_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    """Resolve `product_objective` (§7.1's table): identity `(system_id,
    objective_key)`, digest over §8's `title, intent, contribution,
    scope_note, summary`, state folded from `product_objective_decision`
    (§4.3, default `proposed`)."""
    ref = (target_ref or "").strip()
    if not ref:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    try:
        row = conn.execute(
            "SELECT * FROM product_objective WHERE system_id = ? AND objective_key = ?",
            (system_id, ref),
        ).fetchone()
    except sqlite3.OperationalError:
        return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if row is None:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    objective = dict(row)
    revision = None
    if objective["current_revision_id"] is not None:
        rev_row = conn.execute(
            "SELECT * FROM product_objective_revision WHERE id = ?",
            (objective["current_revision_id"],),
        ).fetchone()
        revision = dict(rev_row) if rev_row is not None else None
    digest = (
        content_digest(
            {
                "title": revision["title"],
                "intent": revision["intent"],
                "contribution": revision["contribution"],
                "scope_note": revision["scope_note"],
                "summary": revision["summary"],
            }
        )
        if revision is not None
        else ""
    )
    state = _fold_latest_decision(
        conn, "product_objective_decision", system_id, "objective_key", ref,
        _OBJECTIVE_DECISION_TO_STATE, default="proposed",
    )
    name = revision["title"] if revision is not None else None
    return {"resolution": "resolved", "name": name, "state": state, "digest": digest}


def _resolve_product_milestone_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    """Resolve `product_milestone` (§7.1's table): identity `(system_id,
    milestone_key)`, digest over §8's `title, target_state,
    verification_method, verification_note, summary`, `design_status`
    folded from `product_milestone_decision` via this module's own
    `_DECISION_TO_DESIGN_STATUS` (§4.3's fold is the identical
    confirm/reject/retire/reinstate table `ux_design_decision` already
    uses)."""
    ref = (target_ref or "").strip()
    if not ref:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    try:
        row = conn.execute(
            "SELECT * FROM product_milestone WHERE system_id = ? AND milestone_key = ?",
            (system_id, ref),
        ).fetchone()
    except sqlite3.OperationalError:
        return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if row is None:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    milestone = dict(row)
    revision = None
    if milestone["current_revision_id"] is not None:
        rev_row = conn.execute(
            "SELECT * FROM product_milestone_revision WHERE id = ?",
            (milestone["current_revision_id"],),
        ).fetchone()
        revision = dict(rev_row) if rev_row is not None else None
    digest = (
        content_digest(
            {
                "title": revision["title"],
                "target_state": revision["target_state"],
                "verification_method": revision["verification_method"],
                "verification_note": revision["verification_note"],
                "summary": revision["summary"],
            }
        )
        if revision is not None
        else ""
    )
    state = _fold_latest_decision(
        conn, "product_milestone_decision", system_id, "milestone_key", ref,
        _DECISION_TO_DESIGN_STATUS, default="proposed",
    )
    name = revision["title"] if revision is not None else None
    return {"resolution": "resolved", "name": name, "state": state, "digest": digest}


def _resolve_product_gap_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    """Resolve `product_gap` (§7.1's table): identity `(system_id,
    gap_key)`, digest over §8's `title, current_state, target_state,
    target_state_mode, interpretation`, `lifecycle` folded from
    `product_gap_decision` (§5.6, `prioritize` rows excluded because they
    never move lifecycle, default `open`)."""
    ref = (target_ref or "").strip()
    if not ref:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    try:
        row = conn.execute(
            "SELECT * FROM product_gap WHERE system_id = ? AND gap_key = ?",
            (system_id, ref),
        ).fetchone()
    except sqlite3.OperationalError:
        return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if row is None:
        return {"resolution": "unresolved", "name": None, "state": "unresolved", "digest": ""}
    gap = dict(row)
    revision = None
    if gap["current_revision_id"] is not None:
        rev_row = conn.execute(
            "SELECT * FROM product_gap_revision WHERE id = ?",
            (gap["current_revision_id"],),
        ).fetchone()
        revision = dict(rev_row) if rev_row is not None else None
    digest = (
        content_digest(
            {
                "title": revision["title"],
                "current_state": revision["current_state"],
                "target_state": revision["target_state"],
                "target_state_mode": revision["target_state_mode"],
                "interpretation": revision["interpretation"],
            }
        )
        if revision is not None
        else ""
    )
    state = _fold_latest_decision(
        conn, "product_gap_decision", system_id, "gap_key", ref,
        _GAP_DECISION_TO_LIFECYCLE, default="open", exclude_decisions=("prioritize",),
    )
    name = revision["title"] if revision is not None else None
    return {"resolution": "resolved", "name": name, "state": state, "digest": digest}


def _resolve_upstream_target(
    conn: sqlite3.Connection, system_id: int, ref_kind: str, target_ref: str
) -> Dict[str, Any]:
    """Dispatch to the ONE canonical source per `ref_kind` (§2.7's table,
    extended by §7.1's three Product Objective Lineage kinds)."""
    if ref_kind in ("purpose_element", "purpose_relation"):
        return _resolve_purpose_target(conn, system_id, ref_kind, target_ref)
    if ref_kind == "capability_entity":
        try:
            return _resolve_capability_entity(conn, system_id, target_ref)
        except Exception:  # pragma: no cover - defensive
            return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if ref_kind == "product_objective":
        try:
            return _resolve_product_objective_target(conn, system_id, target_ref)
        except Exception:  # pragma: no cover - defensive
            return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if ref_kind == "product_milestone":
        try:
            return _resolve_product_milestone_target(conn, system_id, target_ref)
        except Exception:  # pragma: no cover - defensive
            return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    if ref_kind == "product_gap":
        try:
            return _resolve_product_gap_target(conn, system_id, target_ref)
        except Exception:  # pragma: no cover - defensive
            return {"resolution": "unavailable", "name": None, "state": "unresolved", "digest": ""}
    raise UxDesignValidationError(f"ref_kind must be one of {REF_KINDS}; got {ref_kind!r}")


def _ref_recheck_state(captured_digest: str, resolution: str, current_digest: str) -> str:
    """§2.7's `recheck_state`: `not_captured` is fail-closed for an empty
    `captured_digest` (never treated as `current`, mirroring #337's
    `premise_not_captured`); an unresolved/unavailable target is always
    `stale` (its content plainly no longer matches what was captured); only
    a resolved target with a matching digest reads `current`."""
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
        "journey_id": row["journey_id"],
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


def add_upstream_ref(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    journey_key: str,
    ref_kind: str,
    target_ref: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one upstream reference (§2.7). Always a NEW row -- unlike
    Artifact References, `ux_journey_upstream_ref` carries no identity
    table entry in §2.2, so this layer does not auto-supersede an existing
    ref with the same `(ref_kind, target_ref)`; correction is an explicit
    developer action via the decision ledger (`journey_upstream_ref`
    subject kind), not an implicit dedup on every POST.

    `captured_digest` is whatever the target's digest resolves to RIGHT
    NOW -- empty when the target does not currently resolve, which is
    exactly the `not_captured` case `_ref_recheck_state` treats as
    fail-closed rather than as a legacy-only condition (§2.7's note that
    this layer captures digests #397/#400's `evolution_node_link` does
    not).
    """
    now = time.time() if now is None else now
    _check_membership(ref_kind, REF_KINDS, "ref_kind")
    journey = _get_journey_row(conn, system_id, journey_key)
    if journey is None:
        raise NotFound(f"Journey {journey_key!r} not found")

    resolved = _resolve_upstream_target(conn, system_id, ref_kind, target_ref)
    captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""

    cur = conn.execute(
        """INSERT INTO ux_journey_upstream_ref
               (system_id, journey_id, ref_kind, target_ref, target_row_id, captured_digest,
                captured_session_id, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)""",
        (system_id, journey["id"], ref_kind, target_ref, captured_digest, note, decision_method, created_by, now),
    )
    row = conn.execute("SELECT * FROM ux_journey_upstream_ref WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _upstream_ref_out_dict(conn, system_id, dict(row))


# --- Journey revisions + Steps --------------------------------------------------


def _journey_revision_out_dict(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ux_journey_revision WHERE id = ?", (revision_id,)).fetchone()
    if row is None:
        raise NotFound(f"Journey revision {revision_id} not found")
    steps = conn.execute(
        "SELECT * FROM ux_journey_step WHERE journey_revision_id = ? ORDER BY step_order, step_key",
        (revision_id,),
    ).fetchall()
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    d["steps"] = [dict(s) for s in steps]
    return d


def add_journey_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    journey_key: str,
    title: str = "",
    beneficiary: str = "",
    usage_context: str = "",
    entry_trigger: str = "",
    value_arrival: str = "",
    summary: str = "",
    change_note: str = "",
    steps: Optional[Sequence[Dict[str, Any]]] = None,
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Append one Journey revision (§2.4/§2.6). `authored_by_kind` /
    `decision_method` / `intelligence_run_id` are parameters on this
    DOMAIN function (so §2.11 item 7's `reasoning_model`-authored-but-not-
    auto-confirmed case is directly testable) but `routes/ux_design.py`'s
    public write endpoint never accepts them from the request body -- it
    always passes `authored_by_kind="developer"`,
    `decision_method="manual"` (#407 calls no LLM anywhere; a future AI-
    assisted authoring flow is out of this issue's scope, see
    `docs/ux-design-lineage.md` and CLAUDE.md's roadmap note on the
    conversational metadata/probe flow).
    """
    now = time.time() if now is None else now
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    steps = list(steps or [])
    journey = _get_journey_row(conn, system_id, journey_key)
    if journey is None:
        raise NotFound(f"Journey {journey_key!r} not found")

    seen_keys: Set[str] = set()
    step_records: List[Dict[str, Any]] = []
    for idx, step in enumerate(steps):
        step_key = step.get("step_key", "")
        if not step_key:
            raise KeyRequired("step_key")
        if step_key in seen_keys:
            raise StepKeyDuplicated(step_key)
        seen_keys.add(step_key)
        evidence_source_kind = step.get("evidence_source_kind", "none")
        _check_membership(evidence_source_kind, EVIDENCE_SOURCE_KINDS, "evidence_source_kind")
        fields = {
            "step_key": step_key,
            "step_order": step.get("step_order", idx),
            "user_intent": step.get("user_intent", ""),
            "system_response": step.get("system_response", ""),
            "success_criteria": step.get("success_criteria", ""),
            "failure_mode": step.get("failure_mode", ""),
            "recovery_path": step.get("recovery_path", ""),
            "evidence_expectation": step.get("evidence_expectation", ""),
            "evidence_source_kind": evidence_source_kind,
        }
        fields["content_digest"] = journey_step_digest(
            step_key=fields["step_key"],
            user_intent=fields["user_intent"],
            system_response=fields["system_response"],
            success_criteria=fields["success_criteria"],
            failure_mode=fields["failure_mode"],
            recovery_path=fields["recovery_path"],
            evidence_expectation=fields["evidence_expectation"],
            evidence_source_kind=fields["evidence_source_kind"],
        )
        step_records.append(fields)

    digest = journey_revision_digest(
        title=title, beneficiary=beneficiary, usage_context=usage_context,
        entry_trigger=entry_trigger, value_arrival=value_arrival, summary=summary,
        steps=step_records,
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = journey["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM ux_journey_revision WHERE journey_id = ?",
            (journey["id"],),
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO ux_journey_revision
                   (journey_id, system_id, revision_number, title, beneficiary, usage_context,
                    entry_trigger, value_arrival, summary, content_digest, authored_by_kind,
                    decision_method, intelligence_run_id, change_note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                journey["id"], system_id, revision_number, title, beneficiary, usage_context,
                entry_trigger, value_arrival, summary, digest, authored_by_kind, decision_method,
                intelligence_run_id, change_note, created_by, now,
            ),
        )
        new_revision_id = cur.lastrowid
        for fields in step_records:
            conn.execute(
                """INSERT INTO ux_journey_step
                       (journey_revision_id, journey_id, system_id, step_key, step_order,
                        user_intent, system_response, success_criteria, failure_mode,
                        recovery_path, evidence_expectation, evidence_source_kind, content_digest)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_revision_id, journey["id"], system_id, fields["step_key"], fields["step_order"],
                    fields["user_intent"], fields["system_response"], fields["success_criteria"],
                    fields["failure_mode"], fields["recovery_path"], fields["evidence_expectation"],
                    fields["evidence_source_kind"], fields["content_digest"],
                ),
            )
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE ux_journey_revision SET superseded_by_id = ? WHERE id = ?",
                (new_revision_id, prior_revision_id),
            )
        conn.execute(
            "UPDATE ux_journey SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, journey["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_journey_detail(conn, system_id, journey_key)


# --- Journey read/list/diff -----------------------------------------------------


def _journey_summary_dict(
    journey: Dict[str, Any],
    revision_out: Optional[Dict[str, Any]],
    baseline_state: str,
    design_status: str,
    recheck_state: str,
    baseline_journey_key: Optional[str],
) -> Dict[str, Any]:
    return {
        "id": journey["id"],
        "system_id": journey["system_id"],
        "journey_key": journey["journey_key"],
        "perspective": journey["perspective"],
        "baseline_mode": journey["baseline_mode"],
        "baseline_journey_id": journey["baseline_journey_id"],
        "baseline_journey_key": baseline_journey_key,
        "baseline_state": baseline_state,
        "current_revision_id": journey["current_revision_id"],
        "current_revision_number": revision_out["revision_number"] if revision_out else None,
        "title": revision_out["title"] if revision_out else "",
        "design_status": design_status,
        "recheck_state": recheck_state,
        "created_by": journey["created_by"],
        "created_at": journey["created_at"],
        "updated_at": journey["updated_at"],
    }


def _journey_baseline_key(conn: sqlite3.Connection, journey: Dict[str, Any]) -> Optional[str]:
    if journey["baseline_journey_id"] is None:
        return None
    row = conn.execute(
        "SELECT journey_key FROM ux_journey WHERE id = ?", (journey["baseline_journey_id"],)
    ).fetchone()
    return row["journey_key"] if row is not None else None


def _journey_overview(conn: sqlite3.Connection, system_id: int, journey: Dict[str, Any]) -> Dict[str, Any]:
    revision_out = (
        _journey_revision_out_dict(conn, journey["current_revision_id"])
        if journey["current_revision_id"] is not None
        else None
    )
    design_status, decision_row = derive_design_status(conn, system_id, "journey", journey["journey_key"])
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)
    baseline_state = journey_baseline_state(conn, system_id, journey)
    baseline_journey_key = _journey_baseline_key(conn, journey)
    return _journey_summary_dict(
        journey, revision_out, baseline_state, design_status, recheck_state, baseline_journey_key
    )


def get_journey_detail(conn: sqlite3.Connection, system_id: int, journey_key: str) -> Dict[str, Any]:
    journey = _get_journey_row(conn, system_id, journey_key)
    if journey is None:
        raise NotFound(f"Journey {journey_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    revision_out: Optional[Dict[str, Any]] = None
    if journey["current_revision_id"] is not None:
        try:
            revision_out = _journey_revision_out_dict(conn, journey["current_revision_id"])
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    upstream_refs: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM ux_journey_upstream_ref
               WHERE system_id = ? AND journey_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, journey["id"]),
        ).fetchall()
        upstream_refs = [_upstream_ref_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "upstream_refs", exc)

    artifact_references: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM ux_design_artifact_reference
               WHERE system_id = ? AND subject_kind = 'journey' AND subject_key = ?
                 AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, journey_key),
        ).fetchall()
        artifact_references = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "artifact_references", exc)

    decisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM ux_design_decision
               WHERE system_id = ? AND subject_kind = 'journey' AND subject_key = ?
               ORDER BY id DESC""",
            (system_id, journey_key),
        ).fetchall()
        decisions = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "decisions", exc)

    design_status, decision_row = derive_design_status(conn, system_id, "journey", journey_key)
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)
    baseline_state = journey_baseline_state(conn, system_id, journey)
    baseline_journey_key = _journey_baseline_key(conn, journey)

    summary = _journey_summary_dict(
        journey, revision_out, baseline_state, design_status, recheck_state, baseline_journey_key
    )
    summary.update(
        {
            "current_revision": revision_out,
            "upstream_refs": upstream_refs,
            "artifact_references": artifact_references,
            "decisions": decisions,
            "degraded_sections": degraded_sections,
            "degraded_detail": degraded_detail,
        }
    )
    return summary


def list_journeys(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    journeys: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM ux_journey WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        journeys = [_journey_overview(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "journeys", exc)
    return {"journeys": journeys, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}


def list_journey_revisions(conn: sqlite3.Connection, system_id: int, journey_key: str) -> Dict[str, Any]:
    journey = _get_journey_row(conn, system_id, journey_key)
    if journey is None:
        raise NotFound(f"Journey {journey_key!r} not found")
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    revisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id FROM ux_journey_revision WHERE journey_id = ? ORDER BY revision_number DESC",
            (journey["id"],),
        ).fetchall()
        revisions = [_journey_revision_out_dict(conn, r["id"]) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "revisions", exc)
    return {
        "journey_id": journey["id"],
        "revisions": revisions,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


def _steps_by_key(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM ux_journey_step WHERE journey_revision_id = ?", (revision_id,)
    ).fetchall()
    return {r["step_key"]: dict(r) for r in rows}


def _diff_entries(
    from_map: Dict[str, Dict[str, Any]],
    to_map: Dict[str, Dict[str, Any]],
    key_field: str,
    item_label: str,
) -> List[Dict[str, Any]]:
    """§2.10's diff vocabulary, matched by EXACT key equality only
    (`understanding_diff`'s rule, §0 invariant 9 -- never similarity or
    embeddings)."""
    keys = sorted(set(from_map) | set(to_map))
    entries = []
    for key in keys:
        f = from_map.get(key)
        t = to_map.get(key)
        if f is None:
            kind = "added"
        elif t is None:
            kind = "removed"
        elif f["content_digest"] == t["content_digest"]:
            kind = "unchanged"
        else:
            kind = "changed"
        entries.append({key_field: key, "change_kind": kind, f"from_{item_label}": f, f"to_{item_label}": t})
    return entries


def _empty_journey_diff(journey: Dict[str, Any], *, diff_state: str) -> Dict[str, Any]:
    return {
        "journey_id": journey["id"],
        "diff_state": diff_state,
        "from_revision_id": None,
        "from_revision_number": None,
        "to_revision_id": None,
        "to_revision_number": None,
        "steps": [],
        "degraded_sections": [],
        "degraded_detail": {},
    }


def diff_journey_revisions(
    conn: sqlite3.Connection,
    system_id: int,
    journey_key: str,
    from_revision: Optional[int] = None,
    to_revision: Optional[int] = None,
) -> Dict[str, Any]:
    journey = _get_journey_row(conn, system_id, journey_key)
    if journey is None:
        raise NotFound(f"Journey {journey_key!r} not found")

    if to_revision is None:
        if journey["current_revision_id"] is None:
            return _empty_journey_diff(journey, diff_state="not_applicable")
        to_row = conn.execute(
            "SELECT * FROM ux_journey_revision WHERE id = ?", (journey["current_revision_id"],)
        ).fetchone()
    else:
        to_row = conn.execute(
            "SELECT * FROM ux_journey_revision WHERE journey_id = ? AND revision_number = ?",
            (journey["id"], to_revision),
        ).fetchone()
        if to_row is None:
            raise NotFound(f"Journey revision {to_revision} not found")

    from_row = None
    if from_revision is not None:
        from_row = conn.execute(
            "SELECT * FROM ux_journey_revision WHERE journey_id = ? AND revision_number = ?",
            (journey["id"], from_revision),
        ).fetchone()
        if from_row is None:
            raise NotFound(f"Journey revision {from_revision} not found")
    elif to_row["revision_number"] > 1:
        from_row = conn.execute(
            "SELECT * FROM ux_journey_revision WHERE journey_id = ? AND revision_number = ?",
            (journey["id"], to_row["revision_number"] - 1),
        ).fetchone()

    from_steps = _steps_by_key(conn, from_row["id"]) if from_row else {}
    to_steps = _steps_by_key(conn, to_row["id"]) if to_row else {}
    entries = _diff_entries(from_steps, to_steps, "step_key", "step")
    return {
        "journey_id": journey["id"],
        "diff_state": "available",
        "from_revision_id": from_row["id"] if from_row else None,
        "from_revision_number": from_row["revision_number"] if from_row else None,
        "to_revision_id": to_row["id"],
        "to_revision_number": to_row["revision_number"],
        "steps": entries,
        "degraded_sections": [],
        "degraded_detail": {},
    }


def baseline_diff_journey(conn: sqlite3.Connection, system_id: int, journey_key: str) -> Dict[str, Any]:
    """§2.10: `not_applicable` (never an empty diff) whenever `baseline_state`
    is not `linked` -- the required distinction between "no changes" and
    "there is nothing to diff against"."""
    journey = _get_journey_row(conn, system_id, journey_key)
    if journey is None:
        raise NotFound(f"Journey {journey_key!r} not found")

    baseline_state = journey_baseline_state(conn, system_id, journey)
    if baseline_state != "linked":
        return _empty_journey_diff(journey, diff_state="not_applicable")

    baseline = conn.execute(
        "SELECT * FROM ux_journey WHERE id = ?", (journey["baseline_journey_id"],)
    ).fetchone()
    from_row = (
        conn.execute("SELECT * FROM ux_journey_revision WHERE id = ?", (baseline["current_revision_id"],)).fetchone()
        if baseline["current_revision_id"] is not None
        else None
    )
    to_row = (
        conn.execute("SELECT * FROM ux_journey_revision WHERE id = ?", (journey["current_revision_id"],)).fetchone()
        if journey["current_revision_id"] is not None
        else None
    )
    if from_row is None and to_row is None:
        return _empty_journey_diff(journey, diff_state="not_applicable")

    from_steps = _steps_by_key(conn, from_row["id"]) if from_row else {}
    to_steps = _steps_by_key(conn, to_row["id"]) if to_row else {}
    entries = _diff_entries(from_steps, to_steps, "step_key", "step")
    return {
        "journey_id": journey["id"],
        "diff_state": "available",
        "from_revision_id": from_row["id"] if from_row else None,
        "from_revision_number": from_row["revision_number"] if from_row else None,
        "to_revision_id": to_row["id"] if to_row else None,
        "to_revision_number": to_row["revision_number"] if to_row else None,
        "steps": entries,
        "degraded_sections": [],
        "degraded_detail": {},
    }


# --- §2.2. Requirement identity, revisions, criteria ---------------------------


def _get_requirement_row(conn: sqlite3.Connection, system_id: int, requirement_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM ux_requirement WHERE system_id = ? AND requirement_key = ?",
        (system_id, requirement_key),
    ).fetchone()
    return dict(row) if row is not None else None


def create_requirement(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    requirement_key: str,
    requirement_kind: str,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not requirement_key:
        raise KeyRequired("requirement_key")
    _check_membership(requirement_kind, REQUIREMENT_KINDS, "requirement_kind")
    if _get_requirement_row(conn, system_id, requirement_key) is not None:
        raise KeyConflict(requirement_key)

    cur = conn.execute(
        """INSERT INTO ux_requirement
               (system_id, requirement_key, requirement_kind, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (system_id, requirement_key, requirement_kind, created_by, now, now),
    )
    row = conn.execute("SELECT * FROM ux_requirement WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def _requirement_revision_out_dict(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM ux_requirement_revision WHERE id = ?", (revision_id,)).fetchone()
    if row is None:
        raise NotFound(f"Requirement revision {revision_id} not found")
    criteria = conn.execute(
        """SELECT * FROM ux_requirement_acceptance_criterion
           WHERE requirement_revision_id = ? ORDER BY criterion_order, criterion_key""",
        (revision_id,),
    ).fetchall()
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    d["acceptance_criteria"] = [dict(c) for c in criteria]
    # requirement_kind lives on the ux_requirement IDENTITY row, never on the
    # revision (contract §2.2: the kind is part of what the Requirement IS, so
    # an out_of_scope declaration cannot quietly become a functional
    # requirement through a content edit). The projection still has to carry it
    # so a reader can judge the revision without a second fetch, so it is
    # joined in here rather than duplicated into a column.
    kind = conn.execute(
        "SELECT requirement_kind FROM ux_requirement WHERE id = ?",
        (d["requirement_id"],),
    ).fetchone()
    d["requirement_kind"] = kind["requirement_kind"] if kind is not None else None
    return d


def add_requirement_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    requirement_key: str,
    statement: str = "",
    rationale: str = "",
    constraint_text: str = "",
    out_of_scope_note: str = "",
    change_note: str = "",
    acceptance_criteria: Optional[Sequence[Dict[str, Any]]] = None,
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Append one Requirement revision (§2.4/§2.6/§2.11 item 8). See
    `add_journey_revision`'s docstring for why `authored_by_kind` /
    `decision_method` / `intelligence_run_id` are domain-function
    parameters that the public write endpoint never exposes.
    """
    now = time.time() if now is None else now
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    criteria = list(acceptance_criteria or [])
    requirement = _get_requirement_row(conn, system_id, requirement_key)
    if requirement is None:
        raise NotFound(f"Requirement {requirement_key!r} not found")
    if requirement["requirement_kind"] == "out_of_scope" and criteria:
        raise OutOfScopeNotVerifiable(requirement_key)

    seen_keys: Set[str] = set()
    criterion_records: List[Dict[str, Any]] = []
    for idx, criterion in enumerate(criteria):
        criterion_key = criterion.get("criterion_key", "")
        if not criterion_key:
            raise KeyRequired("criterion_key")
        if criterion_key in seen_keys:
            raise CriterionKeyDuplicated(criterion_key)
        seen_keys.add(criterion_key)
        verification_method = criterion.get("verification_method", "manual_review")
        _check_membership(verification_method, VERIFICATION_METHODS, "verification_method")
        fields = {
            "criterion_key": criterion_key,
            "criterion_order": criterion.get("criterion_order", idx),
            "statement": criterion.get("statement", ""),
            "verification_method": verification_method,
            "verification_note": criterion.get("verification_note", ""),
        }
        fields["content_digest"] = acceptance_criterion_digest(
            criterion_key=fields["criterion_key"],
            statement=fields["statement"],
            verification_method=fields["verification_method"],
            verification_note=fields["verification_note"],
        )
        criterion_records.append(fields)

    digest = requirement_revision_digest(
        requirement_kind=requirement["requirement_kind"], statement=statement, rationale=rationale,
        constraint_text=constraint_text, out_of_scope_note=out_of_scope_note, criteria=criterion_records,
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = requirement["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM ux_requirement_revision WHERE requirement_id = ?",
            (requirement["id"],),
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO ux_requirement_revision
                   (requirement_id, system_id, revision_number, statement, rationale, constraint_text,
                    out_of_scope_note, content_digest, authored_by_kind, decision_method,
                    intelligence_run_id, change_note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                requirement["id"], system_id, revision_number, statement, rationale, constraint_text,
                out_of_scope_note, digest, authored_by_kind, decision_method, intelligence_run_id,
                change_note, created_by, now,
            ),
        )
        new_revision_id = cur.lastrowid
        for fields in criterion_records:
            conn.execute(
                """INSERT INTO ux_requirement_acceptance_criterion
                       (requirement_revision_id, requirement_id, system_id, criterion_key,
                        criterion_order, statement, verification_method, verification_note,
                        content_digest)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_revision_id, requirement["id"], system_id, fields["criterion_key"],
                    fields["criterion_order"], fields["statement"], fields["verification_method"],
                    fields["verification_note"], fields["content_digest"],
                ),
            )
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE ux_requirement_revision SET superseded_by_id = ? WHERE id = ?",
                (new_revision_id, prior_revision_id),
            )
        conn.execute(
            "UPDATE ux_requirement SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, requirement["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_requirement_detail(conn, system_id, requirement_key)


# --- Requirement <-> Journey Step links ----------------------------------------


def _resolve_step_target(conn: sqlite3.Connection, system_id: int, journey_id: int, step_key: str) -> Dict[str, Any]:
    """Resolve a `step_key` against the Journey's CURRENT revision (§2.9's
    "Step が消える -> link が unresolved" rule)."""
    journey = conn.execute(
        "SELECT * FROM ux_journey WHERE id = ? AND system_id = ?", (journey_id, system_id)
    ).fetchone()
    if journey is None or journey["current_revision_id"] is None:
        return {"resolution": "unresolved", "digest": "", "label": None}
    step = conn.execute(
        "SELECT * FROM ux_journey_step WHERE journey_revision_id = ? AND step_key = ?",
        (journey["current_revision_id"], step_key),
    ).fetchone()
    if step is None:
        return {"resolution": "unresolved", "digest": "", "label": None}
    return {"resolution": "resolved", "digest": step["content_digest"], "label": step["user_intent"]}


def _step_link_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_step_target(conn, system_id, row["journey_id"], row["step_key"])
    journey_row = conn.execute(
        "SELECT journey_key FROM ux_journey WHERE id = ?", (row["journey_id"],)
    ).fetchone()
    return {
        "id": row["id"],
        "requirement_id": row["requirement_id"],
        "journey_id": row["journey_id"],
        "journey_key": journey_row["journey_key"] if journey_row is not None else None,
        "step_key": row["step_key"],
        "step_label": resolved.get("label"),
        "captured_journey_revision_id": row["captured_journey_revision_id"],
        "captured_step_digest": row["captured_step_digest"],
        "target_resolution": resolved["resolution"],
        "recheck_state": _ref_recheck_state(row["captured_step_digest"], resolved["resolution"], resolved["digest"]),
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def add_requirement_step_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    requirement_key: str,
    journey_key: str,
    step_key: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one Requirement<->Step link (§2.4/§2.9). The step MUST exist
    in the Journey's current revision at link time (404
    `journey_step_not_found` otherwise) -- unlike an upstream ref, a step
    link cannot aspirationally point at nothing, because it is joining two
    entities this layer both owns. Like upstream refs, this always inserts
    a NEW row (§2.2 lists no identity/supersede rule for this table); the
    existing decision ledger (`requirement_step_link` subject kind) is the
    sanctioned correction path.
    """
    now = time.time() if now is None else now
    if not step_key:
        raise KeyRequired("step_key")
    requirement = _get_requirement_row(conn, system_id, requirement_key)
    if requirement is None:
        raise NotFound(f"Requirement {requirement_key!r} not found")
    journey = _get_journey_row(conn, system_id, journey_key)
    if journey is None:
        raise NotFound(f"Journey {journey_key!r} not found")

    resolved = _resolve_step_target(conn, system_id, journey["id"], step_key)
    if resolved["resolution"] != "resolved":
        raise StepNotFound(step_key)

    cur = conn.execute(
        """INSERT INTO ux_requirement_step_link
               (system_id, requirement_id, journey_id, step_key, captured_journey_revision_id,
                captured_step_digest, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, requirement["id"], journey["id"], step_key, journey["current_revision_id"],
            resolved["digest"], note, decision_method, created_by, now,
        ),
    )
    row = conn.execute("SELECT * FROM ux_requirement_step_link WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _step_link_out_dict(conn, system_id, dict(row))


# --- Requirement read/list/diff --------------------------------------------------


def _requirement_summary_dict(
    requirement: Dict[str, Any],
    revision_out: Optional[Dict[str, Any]],
    design_status: str,
    recheck_state: str,
) -> Dict[str, Any]:
    return {
        "id": requirement["id"],
        "system_id": requirement["system_id"],
        "requirement_key": requirement["requirement_key"],
        "requirement_kind": requirement["requirement_kind"],
        "current_revision_id": requirement["current_revision_id"],
        "current_revision_number": revision_out["revision_number"] if revision_out else None,
        "statement": revision_out["statement"] if revision_out else "",
        "design_status": design_status,
        "recheck_state": recheck_state,
        "created_by": requirement["created_by"],
        "created_at": requirement["created_at"],
        "updated_at": requirement["updated_at"],
    }


def _requirement_overview(conn: sqlite3.Connection, system_id: int, requirement: Dict[str, Any]) -> Dict[str, Any]:
    revision_out = (
        _requirement_revision_out_dict(conn, requirement["current_revision_id"])
        if requirement["current_revision_id"] is not None
        else None
    )
    design_status, decision_row = derive_design_status(
        conn, system_id, "requirement", requirement["requirement_key"]
    )
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)
    return _requirement_summary_dict(requirement, revision_out, design_status, recheck_state)


def get_requirement_detail(conn: sqlite3.Connection, system_id: int, requirement_key: str) -> Dict[str, Any]:
    requirement = _get_requirement_row(conn, system_id, requirement_key)
    if requirement is None:
        raise NotFound(f"Requirement {requirement_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    revision_out: Optional[Dict[str, Any]] = None
    if requirement["current_revision_id"] is not None:
        try:
            revision_out = _requirement_revision_out_dict(conn, requirement["current_revision_id"])
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    step_links: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM ux_requirement_step_link
               WHERE system_id = ? AND requirement_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, requirement["id"]),
        ).fetchall()
        step_links = [_step_link_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "step_links", exc)

    artifact_references: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM ux_design_artifact_reference
               WHERE system_id = ? AND subject_kind = 'requirement' AND subject_key = ?
                 AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, requirement_key),
        ).fetchall()
        artifact_references = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "artifact_references", exc)

    decisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM ux_design_decision
               WHERE system_id = ? AND subject_kind = 'requirement' AND subject_key = ?
               ORDER BY id DESC""",
            (system_id, requirement_key),
        ).fetchall()
        decisions = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "decisions", exc)

    design_status, decision_row = derive_design_status(conn, system_id, "requirement", requirement_key)
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)

    summary = _requirement_summary_dict(requirement, revision_out, design_status, recheck_state)
    summary.update(
        {
            "current_revision": revision_out,
            "step_links": step_links,
            "artifact_references": artifact_references,
            "decisions": decisions,
            "degraded_sections": degraded_sections,
            "degraded_detail": degraded_detail,
        }
    )
    return summary


def list_requirements(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    requirements: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM ux_requirement WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        requirements = [_requirement_overview(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "requirements", exc)
    return {
        "requirements": requirements,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


def list_requirement_revisions(conn: sqlite3.Connection, system_id: int, requirement_key: str) -> Dict[str, Any]:
    requirement = _get_requirement_row(conn, system_id, requirement_key)
    if requirement is None:
        raise NotFound(f"Requirement {requirement_key!r} not found")
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    revisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id FROM ux_requirement_revision WHERE requirement_id = ? ORDER BY revision_number DESC",
            (requirement["id"],),
        ).fetchall()
        revisions = [_requirement_revision_out_dict(conn, r["id"]) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "revisions", exc)
    return {
        "requirement_id": requirement["id"],
        "revisions": revisions,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


def _criteria_by_key(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM ux_requirement_acceptance_criterion WHERE requirement_revision_id = ?",
        (revision_id,),
    ).fetchall()
    return {r["criterion_key"]: dict(r) for r in rows}


def _empty_requirement_diff(requirement: Dict[str, Any], *, diff_state: str) -> Dict[str, Any]:
    return {
        "requirement_id": requirement["id"],
        "diff_state": diff_state,
        "from_revision_id": None,
        "from_revision_number": None,
        "to_revision_id": None,
        "to_revision_number": None,
        "criteria": [],
        "degraded_sections": [],
        "degraded_detail": {},
    }


def diff_requirement_revisions(
    conn: sqlite3.Connection,
    system_id: int,
    requirement_key: str,
    from_revision: Optional[int] = None,
    to_revision: Optional[int] = None,
) -> Dict[str, Any]:
    requirement = _get_requirement_row(conn, system_id, requirement_key)
    if requirement is None:
        raise NotFound(f"Requirement {requirement_key!r} not found")

    if to_revision is None:
        if requirement["current_revision_id"] is None:
            return _empty_requirement_diff(requirement, diff_state="not_applicable")
        to_row = conn.execute(
            "SELECT * FROM ux_requirement_revision WHERE id = ?", (requirement["current_revision_id"],)
        ).fetchone()
    else:
        to_row = conn.execute(
            "SELECT * FROM ux_requirement_revision WHERE requirement_id = ? AND revision_number = ?",
            (requirement["id"], to_revision),
        ).fetchone()
        if to_row is None:
            raise NotFound(f"Requirement revision {to_revision} not found")

    from_row = None
    if from_revision is not None:
        from_row = conn.execute(
            "SELECT * FROM ux_requirement_revision WHERE requirement_id = ? AND revision_number = ?",
            (requirement["id"], from_revision),
        ).fetchone()
        if from_row is None:
            raise NotFound(f"Requirement revision {from_revision} not found")
    elif to_row["revision_number"] > 1:
        from_row = conn.execute(
            "SELECT * FROM ux_requirement_revision WHERE requirement_id = ? AND revision_number = ?",
            (requirement["id"], to_row["revision_number"] - 1),
        ).fetchone()

    from_criteria = _criteria_by_key(conn, from_row["id"]) if from_row else {}
    to_criteria = _criteria_by_key(conn, to_row["id"]) if to_row else {}
    entries = _diff_entries(from_criteria, to_criteria, "criterion_key", "criterion")
    return {
        "requirement_id": requirement["id"],
        "diff_state": "available",
        "from_revision_id": from_row["id"] if from_row else None,
        "from_revision_number": from_row["revision_number"] if from_row else None,
        "to_revision_id": to_row["id"],
        "to_revision_number": to_row["revision_number"],
        "criteria": entries,
        "degraded_sections": [],
        "degraded_detail": {},
    }


# --- §2.5. The decision ledger (journey / requirement / step_link / upstream_ref
#     / artifact_reference) ---------------------------------------------------


def _resolve_decision_subject(
    conn: sqlite3.Connection, system_id: int, subject_kind: str, subject_key: str
) -> Tuple[int, str, Optional[int]]:
    """`(subject_row_id, current_digest, captured_revision_id)` for one
    decidable subject (§2.10's `UxDesignSubjectKind`).

    Journey/Requirement compare against their CURRENT revision's
    `content_digest` (empty when no revision exists yet). The three
    reference/link kinds have no revision chain of their own, so their
    "current digest" is simply what is stored on their own row right now
    (`captured_step_digest` / `captured_digest` / `content_hash`) -- the
    same discipline the identity table (§2.2) already applies to them
    (correction happens by creating a new row, not by mutating this one).
    `subject_key` for these three is the row's own numeric id as a string,
    since (unlike Journey/Requirement) they carry no developer-supplied
    slug -- the Dashboard already has each row's `id` from the `*Out`
    model that listed it.
    """
    if subject_kind == "journey":
        journey = _get_journey_row(conn, system_id, subject_key)
        if journey is None:
            raise SubjectNotFound(subject_key)
        revision = (
            conn.execute(
                "SELECT * FROM ux_journey_revision WHERE id = ?", (journey["current_revision_id"],)
            ).fetchone()
            if journey["current_revision_id"] is not None
            else None
        )
        digest = revision["content_digest"] if revision else ""
        return journey["id"], digest, (revision["id"] if revision else None)

    if subject_kind == "requirement":
        requirement = _get_requirement_row(conn, system_id, subject_key)
        if requirement is None:
            raise SubjectNotFound(subject_key)
        revision = (
            conn.execute(
                "SELECT * FROM ux_requirement_revision WHERE id = ?", (requirement["current_revision_id"],)
            ).fetchone()
            if requirement["current_revision_id"] is not None
            else None
        )
        digest = revision["content_digest"] if revision else ""
        return requirement["id"], digest, (revision["id"] if revision else None)

    if subject_kind == "requirement_step_link":
        if not subject_key.isdigit():
            raise SubjectNotFound(subject_key)
        row = conn.execute(
            "SELECT * FROM ux_requirement_step_link WHERE id = ? AND system_id = ? AND superseded_by_id IS NULL",
            (int(subject_key), system_id),
        ).fetchone()
        if row is None:
            raise SubjectNotFound(subject_key)
        return row["id"], row["captured_step_digest"], None

    if subject_kind == "journey_upstream_ref":
        if not subject_key.isdigit():
            raise SubjectNotFound(subject_key)
        row = conn.execute(
            "SELECT * FROM ux_journey_upstream_ref WHERE id = ? AND system_id = ? AND superseded_by_id IS NULL",
            (int(subject_key), system_id),
        ).fetchone()
        if row is None:
            raise SubjectNotFound(subject_key)
        return row["id"], row["captured_digest"], None

    if subject_kind == "artifact_reference":
        if not subject_key.isdigit():
            raise SubjectNotFound(subject_key)
        row = conn.execute(
            """SELECT * FROM ux_design_artifact_reference
               WHERE id = ? AND system_id = ? AND superseded_by_id IS NULL""",
            (int(subject_key), system_id),
        ).fetchone()
        if row is None:
            raise SubjectNotFound(subject_key)
        return row["id"], row["content_hash"], None

    raise UxDesignValidationError(f"subject_kind must be one of {DESIGN_SUBJECT_KINDS}; got {subject_kind!r}")


def record_design_decision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    subject_kind: str,
    subject_key: str,
    decision: str,
    rationale: str = "",
    captured_digest: str = "",
    decided_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The ONE write behind `design_status` (§2.5). Always appends a new row
    and supersedes the prior current one, mirroring
    `purpose_chain.record_relation_decision`'s pattern exactly.
    `decision_method` is hardcoded `'manual'` -- there is no parameter to
    make it anything else, matching the `CHECK (decision_method = 'manual')`
    constraint on `ux_design_decision` itself.
    """
    now = time.time() if now is None else now
    _check_membership(decision, DESIGN_DECISION_KINDS, "decision")

    subject_row_id, current_digest, captured_revision_id = _resolve_decision_subject(
        conn, system_id, subject_kind, subject_key
    )
    if captured_digest and captured_digest != current_digest:
        raise DecisionStaleDigest(subject_key)

    prior_status, _ = derive_design_status(conn, system_id, subject_kind, subject_key)
    if decision == "reinstate":
        if prior_status not in ("rejected", "retired"):
            raise NotDecidable(subject_key)
    else:
        if prior_status in ("rejected", "retired"):
            raise NotDecidable(subject_key)

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM ux_design_decision
               WHERE system_id = ? AND subject_kind = ? AND subject_key = ? AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, subject_kind, subject_key),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO ux_design_decision
                   (system_id, subject_kind, subject_key, subject_row_id, decision, rationale,
                    captured_digest, captured_revision_id, decided_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, subject_kind, subject_key, subject_row_id, decision, rationale,
                current_digest, captured_revision_id, decided_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE ux_design_decision SET superseded_by_id = ? WHERE id = ?", (new_id, prior["id"])
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM ux_design_decision WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


# --- §2.8. Artifact References: no body column, git-only verification ----------


def parse_repo_artifact_uri(uri: str) -> Optional[str]:
    """`repo:<path>` -> `path`, else `None` (§2.8). Only THIS scheme is ever
    resolved against the target repository -- every other scheme (http(s),
    an internal wiki, Figma, ...) stays `unverified` by construction.
    probe-agent fetches no other URI: this function never makes, and never
    is used to make, an outbound network request (Principle 5 / SSRF).
    """
    prefix = "repo:"
    if not uri.startswith(prefix):
        return None
    return uri[len(prefix):]


def _validate_repo_artifact_path(path: str) -> None:
    """Reuse `git_ops`'s own path-traversal / unsafe-path validation (the
    same check `git_ops.read_file_at_commit` applies internally) so a
    malformed `repo:` path is rejected with the named 422 BEFORE any git
    subprocess ever runs, regardless of whether a snapshot is even
    available to check it against."""
    if not git_ops._is_safe_git_path(path):  # noqa: SLF001 - established cross-module reuse (see git_ops._run_git callers)
        raise ArtifactUriInvalid(path)


def _verify_artifact_content(repo_path: str, commit_sha: str, path: str, content_hash: str) -> bool:
    """One `git show <sha>:<path>` + sha256 compare. The only place this
    module calls `git`, and the only place `verification_state='verified'`
    can be produced (§2.8)."""
    try:
        data = git_ops.read_file_at_commit(repo_path, commit_sha, path)
    except GitError:
        return False
    return hashlib.sha256(data).hexdigest() == content_hash


def _require_artifact_subject(
    conn: sqlite3.Connection, system_id: int, subject_kind: str, subject_key: str
) -> None:
    """Require the Artifact Reference's owner to exist in this System.

    Stable identity kinds use their developer-supplied key.  The two nested,
    potentially ambiguous kinds use the current row id encoded as decimal
    text: ``step_key`` and ``option_key`` are only unique inside their parent,
    so accepting either one by itself could attach an artifact to the wrong
    Journey or Solution Design.

    Missing and foreign-System rows deliberately share ``SubjectNotFound`` so
    the endpoint cannot be used to probe another System's identifiers.
    """
    row: Optional[sqlite3.Row]
    if subject_kind == "journey":
        row = conn.execute(
            "SELECT id FROM ux_journey WHERE system_id = ? AND journey_key = ?",
            (system_id, subject_key),
        ).fetchone()
    elif subject_kind == "requirement":
        row = conn.execute(
            "SELECT id FROM ux_requirement WHERE system_id = ? AND requirement_key = ?",
            (system_id, subject_key),
        ).fetchone()
    elif subject_kind == "solution_design":
        row = conn.execute(
            "SELECT id FROM solution_design WHERE system_id = ? AND design_key = ?",
            (system_id, subject_key),
        ).fetchone()
    elif subject_kind == "journey_step" and subject_key.isdigit():
        row = conn.execute(
            """SELECT s.id FROM ux_journey_step s
                   JOIN ux_journey j
                     ON j.id = s.journey_id
                    AND j.system_id = s.system_id
                    AND j.current_revision_id = s.journey_revision_id
                   WHERE s.id = ? AND s.system_id = ?""",
            (int(subject_key), system_id),
        ).fetchone()
    elif subject_kind == "design_option" and subject_key.isdigit():
        row = conn.execute(
            """SELECT o.id FROM solution_design_option o
                   JOIN solution_design d
                     ON d.id = o.solution_design_id AND d.system_id = o.system_id
                   WHERE o.id = ? AND o.system_id = ? AND o.superseded_by_id IS NULL""",
            (int(subject_key), system_id),
        ).fetchone()
    else:
        row = None
    if row is None:
        raise SubjectNotFound(subject_key)


def create_artifact_reference(
    *,
    system_id: int,
    subject_kind: str,
    subject_key: str,
    artifact_kind: str,
    title: str = "",
    uri: str,
    media_type: str = "",
    content_hash: str,
    byte_size: Optional[int] = None,
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Create one Artifact Reference (§2.8). Takes NO `conn` -- unlike
    every other write in this module, this one shells out to `git` and so
    must never hold a `get_conn()` connection open across that call
    (`.claude/skills/control-server/SKILL.md`; the same shape
    `snapshot_preflight.gather_preflight` uses). Callers (routes/tests)
    must call this with no connection already open on the thread.

    Sequence: read (snapshot + prior row) -> close -> `git` (if a
    `repo:<path>` URI) -> reopen -> persist. `verification_state` is
    computed exactly once, at THIS write:

    * a non-`repo:` URI is always `unverified` -- the hash is the
      developer's own assertion, never system-confirmed;
    * a `repo:<path>` URI that resolves via `git show <sha>:<path>` on the
      System's latest READY snapshot and hash-matches is `verified`;
    * a `repo:<path>` URI that fails to resolve/hash-match is
      `unreachable` when the immediately prior non-superseded row for the
      same `(subject_kind, subject_key, uri)` identity was itself
      `verified` (§2.8's "一度 verified だった repo path が現在の snapshot
      で解決しなくなったら unreachable"), and plain `unverified` otherwise
      (nothing to have been "reachable" FROM).

    `(system_id, subject_kind, subject_key, uri)`'s prior non-superseded
    row (§2.2's identity) is superseded atomically with the new insert --
    this is the one table in this module where a repeat POST is the
    sanctioned way to trigger a fresh verification, since artifact content
    is never re-checked implicitly on GET (GET writes nothing).
    """
    now = time.time() if now is None else now
    _check_membership(subject_kind, ARTIFACT_SUBJECT_KINDS, "subject_kind")
    _check_membership(artifact_kind, ARTIFACT_KINDS, "artifact_kind")
    if not content_hash:
        raise ArtifactHashRequired()
    if re.fullmatch(r"[0-9a-fA-F]{64}", content_hash) is None:
        raise ArtifactHashInvalid()
    content_hash = content_hash.lower()
    if not (uri or "").strip():
        raise ArtifactUriInvalid(uri)

    path = parse_repo_artifact_uri(uri)
    if path is not None:
        _validate_repo_artifact_path(path)

    with get_conn() as conn:
        _require_artifact_subject(conn, system_id, subject_kind, subject_key)
        snapshot_row = state_facts.get_latest_ready_snapshot(conn, system_id)
        snapshot = dict(snapshot_row) if snapshot_row is not None else None
        prior_row = conn.execute(
            """SELECT * FROM ux_design_artifact_reference
               WHERE system_id = ? AND subject_kind = ? AND subject_key = ? AND uri = ?
                 AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, subject_kind, subject_key, uri),
        ).fetchone()
        prior = dict(prior_row) if prior_row is not None else None
    # `conn` is closed here -- everything below is either pure or a `git`
    # subprocess call, never a database call.

    verification_state = "unverified"
    verified_snapshot_id: Optional[int] = None
    verified_commit_sha: Optional[str] = None
    verified_at: Optional[float] = None
    if path is not None and snapshot is not None:
        if _verify_artifact_content(snapshot["repo_path"], snapshot["commit_sha"], path, content_hash):
            verification_state = "verified"
            verified_snapshot_id = snapshot["id"]
            verified_commit_sha = snapshot["commit_sha"]
            verified_at = now
    if (
        verification_state != "verified"
        and path is not None
        and prior is not None
        and prior["verification_state"] == "verified"
    ):
        verification_state = "unreachable"

    with get_conn() as conn:
        # Serialize the identity fold. Verification happens outside the DB
        # lock, so two requests can otherwise both observe the same prior row
        # and leave two non-superseded references for one artifact identity.
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Re-check inside the write transaction.  Artifact verification
            # may run a git subprocess between the first read and this write;
            # a subject that disappeared or was superseded in that interval
            # must not gain an orphaned reference.
            _require_artifact_subject(conn, system_id, subject_kind, subject_key)
            write_prior = conn.execute(
                """SELECT * FROM ux_design_artifact_reference
                   WHERE system_id = ? AND subject_kind = ? AND subject_key = ? AND uri = ?
                     AND superseded_by_id IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (system_id, subject_kind, subject_key, uri),
            ).fetchone()
            if (
                verification_state != "verified"
                and path is not None
                and write_prior is not None
                and write_prior["verification_state"] == "verified"
            ):
                verification_state = "unreachable"
            cur = conn.execute(
                """INSERT INTO ux_design_artifact_reference
                       (system_id, subject_kind, subject_key, artifact_kind, title, uri, media_type,
                        content_hash, hash_algorithm, byte_size, verification_state,
                        verified_snapshot_id, verified_commit_sha, verified_at, decision_method,
                        created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sha256', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    system_id, subject_kind, subject_key, artifact_kind, title, uri, media_type,
                    content_hash, byte_size, verification_state, verified_snapshot_id,
                    verified_commit_sha, verified_at, decision_method, created_by, now,
                ),
            )
            new_id = cur.lastrowid
            if write_prior is not None:
                conn.execute(
                    "UPDATE ux_design_artifact_reference SET superseded_by_id = ? WHERE id = ?",
                    (new_id, write_prior["id"]),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        row = conn.execute("SELECT * FROM ux_design_artifact_reference WHERE id = ?", (new_id,)).fetchone()
    return dict(row)
