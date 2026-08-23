"""Stakeholder Value Network: Stakeholder / Need / Environment Observation /
Value Exchange persistence (Issue #420, Epic #418).

`docs/stakeholder-value-network.md` is the canonical contract; §1-§6, §10,
§14 are this module's specification. This is a deterministic domain service
-- **no LLM call anywhere in this module** (invariant 9). Every public
function here takes an already-open `conn` and performs no external call --
unlike `ux_design.create_artifact_reference`, nothing in this Epic shells
out to `git` or any other subprocess, so there is no analogous
close-before-external-call seam to maintain.

Two rules this module must never violate (§0):

* **No new understanding model, no upstream/downstream content copies.**
  Stakeholder / Need / Environment Observation / Value Exchange content is
  genuinely new and IS stored here, but a reference to anything else this
  layer does not own (`stakeholder_ref`, `environment_observation_impact`)
  never copies the target's content -- only a `target_ref` plus a
  `captured_digest`, resolved against exactly one canonical source per kind
  at READ time. **Issue #420 persists the `stakeholder_ref` table and its
  finite vocabulary, but full resolution against the upstream/downstream
  kinds this layer does not itself own (`purpose_element`,
  `purpose_relation`, `capability_entity`, `ux_journey`, `ux_journey_step`,
  `ux_requirement`, `purpose_outcome_criterion`) is Issue #421's.**
  `_resolve_target` below is the explicit seam: it already resolves the
  three kinds THIS module owns (`stakeholder`, `stakeholder_need`,
  `value_exchange`) and reports `unavailable` -- never `unresolved`, since
  an unavailable reader must never render as "the target does not exist" --
  for every other kind, with a docstring naming exactly what #421 must add.
* **`design_status` / `validity_state` are derived, never stored** (§3/
  §1.4): `design_status` is the latest non-superseded `stakeholder_decision`
  row for `(system_id, subject_kind, subject_key)`, folded through a fixed
  4-row mapping. `recheck_state` is a SEPARATE axis -- a stale `confirmed`
  Exchange stays `confirmed` (#337/#338/#349's "a stored lifecycle value can
  drift from the rows it describes, a derived one cannot", applied here to
  a decision ledger instead of an event log). `validity_state` is derived
  from the clock and `valid_from`/`valid_to`, never a column.

probe-agent:
  role: Deterministic Stakeholder / Need / Environment Observation / Value Exchange domain service
  capability: stakeholder-value-network
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify every revision is append-only with a reproducible content digest, that design_status/validity_state are always derived rather than stored, that a stakeholder_ref never copies target content, and that this module never imports or calls an LLM client.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, get_args

from .models import (
    EnvironmentImpactKind,
    EnvironmentObservationConfidence,
    StakeholderAuthorshipKind,
    StakeholderDecisionKind,
    StakeholderDesignStatus,
    StakeholderEvidenceKind,
    StakeholderKind,
    StakeholderNeedKind,
    StakeholderRecheckState,
    StakeholderRefKind,
    StakeholderRefRecheckState,
    StakeholderRevisionState,
    StakeholderRole,
    StakeholderRoleScopeKind,
    StakeholderSubjectKind,
    ValueExchangeCadence,
    ValueExchangeConsiderationState,
    ValueExchangeKind,
    ValueExchangeValidityState,
)

__all__ = [
    "StakeholderNetworkError",
    "NotFound",
    "KeyRequired",
    "KeyConflict",
    "SelfLoop",
    "ValueStatementRequired",
    "ConsiderationIncomplete",
    "ValidityInverted",
    "SubjectNotFound",
    "DecisionStaleDigest",
    "NotDecidable",
    "RefKindInvalid",
    "ImpactKindInvalid",
    "TargetNotFound",
    "content_digest",
    "stakeholder_digest",
    "need_digest",
    "exchange_digest",
    "observation_digest",
    "create_stakeholder",
    "add_stakeholder_revision",
    "get_stakeholder_detail",
    "list_stakeholders",
    "list_stakeholder_revisions",
    "add_role_assignment",
    "create_need",
    "add_need_revision",
    "get_need_detail",
    "list_needs",
    "list_need_revisions",
    "create_observation",
    "get_observation_detail",
    "list_observations",
    "create_exchange",
    "add_exchange_revision",
    "get_exchange_detail",
    "list_exchanges",
    "list_exchange_revisions",
    "create_ref",
    "list_refs",
    "create_evidence_ref",
    "list_evidence_refs",
    "derive_design_status",
    "derive_recheck_state",
    "derive_validity_state",
    "record_decision",
    "get_view_preference",
    "update_view_preference",
]


# --- §2. Finite vocabularies, mirrored from app/models.py with get_args -------

STAKEHOLDER_KINDS: Tuple[str, ...] = get_args(StakeholderKind)
STAKEHOLDER_ROLES: Tuple[str, ...] = get_args(StakeholderRole)
ROLE_SCOPE_KINDS: Tuple[str, ...] = get_args(StakeholderRoleScopeKind)
NEED_KINDS: Tuple[str, ...] = get_args(StakeholderNeedKind)
OBSERVATION_CONFIDENCES: Tuple[str, ...] = get_args(EnvironmentObservationConfidence)
IMPACT_KINDS: Tuple[str, ...] = get_args(EnvironmentImpactKind)
EXCHANGE_KINDS: Tuple[str, ...] = get_args(ValueExchangeKind)
CONSIDERATION_STATES: Tuple[str, ...] = get_args(ValueExchangeConsiderationState)
CADENCES: Tuple[str, ...] = get_args(ValueExchangeCadence)
VALIDITY_STATES: Tuple[str, ...] = get_args(ValueExchangeValidityState)
DESIGN_STATUSES: Tuple[str, ...] = get_args(StakeholderDesignStatus)
DESIGN_DECISION_KINDS: Tuple[str, ...] = get_args(StakeholderDecisionKind)
RECHECK_STATES: Tuple[str, ...] = get_args(StakeholderRecheckState)
REVISION_STATES: Tuple[str, ...] = get_args(StakeholderRevisionState)
AUTHORSHIP_KINDS: Tuple[str, ...] = get_args(StakeholderAuthorshipKind)
SUBJECT_KINDS: Tuple[str, ...] = get_args(StakeholderSubjectKind)
REF_KINDS: Tuple[str, ...] = get_args(StakeholderRefKind)
REF_RECHECK_STATES: Tuple[str, ...] = get_args(StakeholderRefRecheckState)
EVIDENCE_KINDS: Tuple[str, ...] = get_args(StakeholderEvidenceKind)

#: §5.1's fixed translation from a reference's own `decision_method` to WHO
#: ASSERTED it -- never a second stored status column. The same table
#: `node_design._DECISION_METHOD_TO_RELATION_STATUS` /
#: `ux_design._DECISION_METHOD_TO_RELATION_STATUS` use one layer over.
_DECISION_METHOD_TO_RELATION_STATUS: Dict[str, str] = {
    "manual": "confirmed",
    "reasoning_llm": "proposed",
    "deterministic": "derived",
}

#: §3's fixed decision-ledger fold: the latest non-superseded
#: `stakeholder_decision.decision` value -> the DERIVED `design_status`.
_DECISION_TO_DESIGN_STATUS: Dict[str, str] = {
    "confirm": "confirmed",
    "reject": "rejected",
    "retire": "retired",
    "reinstate": "proposed",
}

#: Kinds `_resolve_target` can resolve TODAY, against this layer's own
#: tables. Everything else in `StakeholderRefKind` is Issue #421's.
_LOCALLY_RESOLVABLE_REF_KINDS = ("stakeholder", "stakeholder_need", "value_exchange")


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise StakeholderValidationError(
            f"{field_name} must be one of {', '.join(vocabulary)}; got {value!r}"
        )


# --- Errors -------------------------------------------------------------------


class StakeholderNetworkError(ValueError):
    """Base class for every failure this module raises."""


class StakeholderValidationError(StakeholderNetworkError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class NotFound(StakeholderNetworkError):
    """A referenced Stakeholder / Need / Observation / Exchange does not
    exist, or belongs to another System (404 `stakeholder_not_found`).

    The two are deliberately the same error -- telling them apart would let
    a caller probe another System's ids (the same rule
    `ux_design.NotFound` / `node_design.NodeDesignNotFoundError` document).
    """


class KeyRequired(StakeholderNetworkError):
    """`stakeholder_key` / `need_key` / `observation_key` / `exchange_key`
    was empty (422 `stakeholder_key_required`)."""


class KeyConflict(StakeholderNetworkError):
    """A key already exists in this System (409 `stakeholder_key_conflict`)."""


class SelfLoop(StakeholderNetworkError):
    """A Value Exchange named the same party as provider and receiver while
    `exchange_kind != 'information'` (422 `exchange_self_loop`, §1.4)."""


class ValueStatementRequired(StakeholderNetworkError):
    """`value_statement` was empty (422 `exchange_value_statement_required`,
    §1.4)."""


class ConsiderationIncomplete(StakeholderNetworkError):
    """`consideration_state='present'` without both `consideration_kind` and
    `consideration_statement` (422 `exchange_consideration_incomplete`,
    §1.4)."""


class ValidityInverted(StakeholderNetworkError):
    """`valid_to` is not strictly after `valid_from` (422
    `exchange_validity_inverted`, §1.4)."""


class SubjectNotFound(NotFound):
    """A decision's subject does not exist (404 `stakeholder_not_found`)."""


class DecisionStaleDigest(StakeholderNetworkError):
    """The caller's `captured_digest` does not match the subject's current
    content digest (409 `stakeholder_decision_stale_digest`)."""


class NotDecidable(StakeholderNetworkError):
    """The requested decision is illegal from the subject's current
    `design_status` (422 `stakeholder_not_decidable`) -- e.g. `confirm` on a
    `retired` subject, or `reinstate` on a `proposed` one."""


class RefKindInvalid(StakeholderNetworkError):
    """`ref_kind` is outside `StakeholderRefKind` (422
    `stakeholder_ref_kind_invalid`, §5.1)."""


class ImpactKindInvalid(StakeholderNetworkError):
    """An Environment Observation impact's `impact_kind` or
    `target_ref_kind` is outside its finite vocabulary (422
    `observation_impact_kind_invalid`, §1.3)."""


class TargetNotFound(NotFound):
    """A `stakeholder_ref`'s `target_ref` was DEFINITIVELY resolved to not
    exist in this System (404 `stakeholder_ref_target_not_found`, §5.1).

    Raised only when `_resolve_target` returns `unresolved` -- i.e. for a
    `ref_kind` this module can actually read
    (`stakeholder`/`stakeholder_need`/`value_exchange`) and found nothing.
    A kind `_resolve_target` cannot read yet (`unavailable`, Issue #421's
    seam) is never rejected here -- an unreadable source must never render
    as "the target does not exist" (§5.1)."""


def _degrade(
    degraded_sections: List[str], degraded_detail: Dict[str, str], section: str, exc: Exception
) -> None:
    """Record one section as degraded without ever substituting a guessed
    value for what it failed to read (invariant 5, `ux_design._degrade` /
    `purpose_chain._degrade`'s discipline)."""
    if section not in degraded_sections:
        degraded_sections.append(section)
    degraded_detail[section] = f"{type(exc).__name__}: {exc}"


# --- §3.1. Digests --------------------------------------------------------------


def content_digest(payload: Dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON form of `payload` (§3.1). Same
    canonicalization `ux_design.content_digest` / `purpose_chain.
    element_digest` use: `sort_keys=True, ensure_ascii=False,
    separators=(",", ":")`."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stakeholder_digest(
    *, stakeholder_key: str, display_name: str, stakeholder_kind: str, description: str, context_note: str
) -> str:
    """§3.1's Stakeholder digest. Role assignments are deliberately EXCLUDED
    -- adding a role in one Journey must not invalidate a confirmation of
    WHO this party is (§3.1's own rule); role assignments carry their own
    `captured_digest` instead."""
    return content_digest(
        {
            "stakeholder_key": stakeholder_key,
            "display_name": display_name,
            "stakeholder_kind": stakeholder_kind,
            "description": description,
            "context_note": context_note,
        }
    )


def need_digest(
    *, need_key: str, need_kind: str, statement: str, rationale: str, stakeholder_key: str
) -> str:
    """§3.1's Need digest. `stakeholder_key` is included -- reattributing a
    Need to a different party is a meaning change, not bookkeeping."""
    return content_digest(
        {
            "need_key": need_key,
            "need_kind": need_kind,
            "statement": statement,
            "rationale": rationale,
            "stakeholder_key": stakeholder_key,
        }
    )


def exchange_digest(
    *,
    exchange_key: str,
    provider_stakeholder_key: str,
    receiver_stakeholder_key: str,
    exchange_kind: str,
    value_statement: str,
    consideration_state: str,
    consideration_kind: Optional[str],
    consideration_statement: str,
    channel: str,
    trigger: str,
    cadence: str,
    valid_from: Optional[float],
    valid_to: Optional[float],
) -> str:
    """§3.1's Value Exchange digest -- every §1.4 field except the
    bookkeeping columns `created_by`/`created_at`/`revision_number`/
    `change_note`."""
    return content_digest(
        {
            "exchange_key": exchange_key,
            "provider_stakeholder_key": provider_stakeholder_key,
            "receiver_stakeholder_key": receiver_stakeholder_key,
            "exchange_kind": exchange_kind,
            "value_statement": value_statement,
            "consideration_state": consideration_state,
            "consideration_kind": consideration_kind,
            "consideration_statement": consideration_statement,
            "channel": channel,
            "trigger": trigger,
            "cadence": cadence,
            "valid_from": valid_from,
            "valid_to": valid_to,
        }
    )


def observation_digest(
    *,
    observation_key: str,
    statement: str,
    source_note: str,
    observation_confidence: str,
    observed_at: Optional[float],
) -> str:
    """§3.1's Environment Observation digest. No revision-chain bookkeeping
    fields exist for this entity in the first place (§3), so nothing needs
    excluding beyond `created_by`/`created_at`."""
    return content_digest(
        {
            "observation_key": observation_key,
            "statement": statement,
            "source_note": source_note,
            "observation_confidence": observation_confidence,
            "observed_at": observed_at,
        }
    )


# --- §5.1. Reference resolution -- the explicit #421 seam ----------------------


def _resolve_target(conn: sqlite3.Connection, system_id: int, ref_kind: str, target_ref: str) -> Dict[str, Any]:
    """Resolve `ref_kind`/`target_ref` against exactly one canonical source
    (§5.1's table). **This is the explicit seam Issue #421 extends.**

    Issue #420 wires up the three kinds THIS module owns
    (`stakeholder`/`stakeholder_need`/`value_exchange`) so the layer can at
    least reference its own rows meaningfully today. Every other kind
    (`purpose_element`, `purpose_relation`, `capability_entity`,
    `ux_journey`, `ux_journey_step`, `ux_requirement`,
    `purpose_outcome_criterion`) is a valid member of `StakeholderRefKind`
    (so creating a `stakeholder_ref`/impact naming one is never rejected as
    invalid) but resolves to `unavailable` -- Issue #421 must replace that
    branch with a real read of each kind's own canonical source
    (`purpose_chain.derive_purpose_chain`, `understanding_capability_entity`,
    `ux_design.get_journey_detail`/`_resolve_step_target`,
    `ux_design.get_requirement_detail`, `purpose_outcome_criterion`), the
    same `_LINK_KIND_TARGET_SOURCE` discipline `node_design.py` and
    `ux_design._resolve_upstream_target` already use one layer over.
    `unavailable` (never `unresolved`) is deliberate here: the source has
    simply not been wired up yet, which is a fact about THIS reader, not
    about whether the target exists (§5.1's "an unreadable source must
    never render as the target does not exist").
    """
    if ref_kind not in REF_KINDS:
        raise RefKindInvalid(ref_kind)

    if ref_kind == "stakeholder":
        row = conn.execute(
            "SELECT * FROM stakeholder WHERE system_id = ? AND stakeholder_key = ?",
            (system_id, target_ref),
        ).fetchone()
        if row is None:
            return {"resolution": "unresolved", "name": None, "digest": ""}
        digest = ""
        if row["current_revision_id"] is not None:
            rev = conn.execute(
                "SELECT * FROM stakeholder_revision WHERE id = ?", (row["current_revision_id"],)
            ).fetchone()
            digest = rev["content_digest"] if rev is not None else ""
        name = None
        if row["current_revision_id"] is not None:
            rev = conn.execute(
                "SELECT display_name FROM stakeholder_revision WHERE id = ?", (row["current_revision_id"],)
            ).fetchone()
            name = rev["display_name"] if rev is not None else None
        return {"resolution": "resolved", "name": name, "digest": digest}

    if ref_kind == "stakeholder_need":
        row = conn.execute(
            "SELECT * FROM stakeholder_need WHERE system_id = ? AND need_key = ?",
            (system_id, target_ref),
        ).fetchone()
        if row is None:
            return {"resolution": "unresolved", "name": None, "digest": ""}
        digest = ""
        if row["current_revision_id"] is not None:
            rev = conn.execute(
                "SELECT content_digest, statement FROM stakeholder_need_revision WHERE id = ?",
                (row["current_revision_id"],),
            ).fetchone()
            digest = rev["content_digest"] if rev is not None else ""
        return {"resolution": "resolved", "name": None, "digest": digest}

    if ref_kind == "value_exchange":
        row = conn.execute(
            "SELECT * FROM value_exchange WHERE system_id = ? AND exchange_key = ?",
            (system_id, target_ref),
        ).fetchone()
        if row is None:
            return {"resolution": "unresolved", "name": None, "digest": ""}
        digest = ""
        if row["current_revision_id"] is not None:
            rev = conn.execute(
                "SELECT content_digest FROM value_exchange_revision WHERE id = ?",
                (row["current_revision_id"],),
            ).fetchone()
            digest = rev["content_digest"] if rev is not None else ""
        return {"resolution": "resolved", "name": None, "digest": digest}

    # Every other StakeholderRefKind member: Issue #421's to wire up.
    return {"resolution": "unavailable", "name": None, "digest": ""}


def _ref_recheck_state(captured_digest: str, resolution: str, current_digest: str) -> str:
    """§4's `recheck_state` for a reference: `not_captured` is fail-closed
    for an empty `captured_digest` (mirroring #337's `premise_not_captured`
    / `ux_design._ref_recheck_state`); an unresolved/unavailable target is
    always `stale`; only a resolved target with a matching digest reads
    `current`."""
    if not captured_digest:
        return "not_captured"
    if resolution != "resolved":
        return "stale"
    if captured_digest != current_digest:
        return "stale"
    return "current"


# --- §3. design_status / recheck_state / validity_state (shared) --------------


def derive_design_status(
    conn: sqlite3.Connection, system_id: int, subject_kind: str, subject_key: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """§3: the latest non-superseded `stakeholder_decision` row, folded
    through the fixed 4-row mapping. No row -> `proposed`."""
    row = conn.execute(
        """SELECT * FROM stakeholder_decision
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
    """§3/invariant 4: `stale` only when the CURRENTLY effective decision is
    a `confirm` whose `captured_digest` no longer matches. `design_status`
    stays `confirmed` regardless."""
    if decision_row is not None and decision_row["decision"] == "confirm":
        if decision_row["captured_digest"] != current_digest:
            return "stale"
    return "current"


def derive_validity_state(
    valid_from: Optional[float], valid_to: Optional[float], now: Optional[float] = None
) -> str:
    """§1.4's fifth axis, first-match, DERIVED at read time, never stored:

    1. `valid_from` is set and still in the future -> `not_started`.
    2. `valid_to` is set and has passed -> `ended`.
    3. Neither bound is set -> `unbounded`.
    4. Otherwise (started, and not yet ended) -> `active`.
    """
    now = time.time() if now is None else now
    if valid_from is not None and now < valid_from:
        return "not_started"
    if valid_to is not None and now >= valid_to:
        return "ended"
    if valid_from is None and valid_to is None:
        return "unbounded"
    return "active"


# --- Stakeholder ---------------------------------------------------------------


def _get_stakeholder_row(conn: sqlite3.Connection, system_id: int, stakeholder_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM stakeholder WHERE system_id = ? AND stakeholder_key = ?",
        (system_id, stakeholder_key),
    ).fetchone()
    return dict(row) if row is not None else None


def create_stakeholder(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    stakeholder_key: str,
    display_name: str = "",
    stakeholder_kind: str = "other",
    description: str = "",
    context_note: str = "",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not stakeholder_key:
        raise KeyRequired("stakeholder_key")
    _check_membership(stakeholder_kind, STAKEHOLDER_KINDS, "stakeholder_kind")
    if _get_stakeholder_row(conn, system_id, stakeholder_key) is not None:
        raise KeyConflict(stakeholder_key)

    digest = stakeholder_digest(
        stakeholder_key=stakeholder_key, display_name=display_name,
        stakeholder_kind=stakeholder_kind, description=description, context_note=context_note,
    )

    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO stakeholder (system_id, stakeholder_key, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (system_id, stakeholder_key, created_by, now, now),
        )
        stakeholder_id = cur.lastrowid
        rev_cur = conn.execute(
            """INSERT INTO stakeholder_revision
                   (stakeholder_id, system_id, revision_number, display_name, stakeholder_kind,
                    description, context_note, content_digest, created_by, created_at)
               VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
            (stakeholder_id, system_id, display_name, stakeholder_kind, description, context_note,
             digest, created_by, now),
        )
        conn.execute(
            "UPDATE stakeholder SET current_revision_id = ? WHERE id = ?",
            (rev_cur.lastrowid, stakeholder_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_stakeholder_detail(conn, system_id, stakeholder_key)


def add_stakeholder_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    stakeholder_key: str,
    display_name: str = "",
    stakeholder_kind: str = "other",
    description: str = "",
    context_note: str = "",
    change_note: str = "",
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Append one Stakeholder revision (§3). `authored_by_kind` /
    `decision_method` / `intelligence_run_id` are parameters of this DOMAIN
    function so a `reasoning_model`-authored-but-not-auto-confirmed revision
    is directly testable, but `routes/stakeholder_network.py`'s public write
    endpoint never accepts them from the request body -- it always passes
    `authored_by_kind="developer"`, `decision_method="manual"` (this issue
    calls no LLM anywhere)."""
    now = time.time() if now is None else now
    _check_membership(stakeholder_kind, STAKEHOLDER_KINDS, "stakeholder_kind")
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    stakeholder = _get_stakeholder_row(conn, system_id, stakeholder_key)
    if stakeholder is None:
        raise NotFound(f"Stakeholder {stakeholder_key!r} not found")

    digest = stakeholder_digest(
        stakeholder_key=stakeholder_key, display_name=display_name,
        stakeholder_kind=stakeholder_kind, description=description, context_note=context_note,
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = stakeholder["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM stakeholder_revision WHERE stakeholder_id = ?",
            (stakeholder["id"],),
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO stakeholder_revision
                   (stakeholder_id, system_id, revision_number, display_name, stakeholder_kind,
                    description, context_note, content_digest, authored_by_kind, decision_method,
                    intelligence_run_id, change_note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (stakeholder["id"], system_id, revision_number, display_name, stakeholder_kind,
             description, context_note, digest, authored_by_kind, decision_method,
             intelligence_run_id, change_note, created_by, now),
        )
        new_revision_id = cur.lastrowid
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE stakeholder_revision SET superseded_by_id = ? WHERE id = ?",
                (new_revision_id, prior_revision_id),
            )
        conn.execute(
            "UPDATE stakeholder SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, stakeholder["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_stakeholder_detail(conn, system_id, stakeholder_key)


def _stakeholder_revision_out_dict(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM stakeholder_revision WHERE id = ?", (revision_id,)).fetchone()
    if row is None:
        raise NotFound(f"Stakeholder revision {revision_id} not found")
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    return d


def _role_assignment_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_target(conn, system_id, "stakeholder", row["stakeholder_key"])
    return {
        "id": row["id"],
        "stakeholder_key": row["stakeholder_key"],
        "role": row["role"],
        "scope_kind": row["scope_kind"],
        "scope_ref": row["scope_ref"],
        "captured_digest": row["captured_digest"],
        "recheck_state": _ref_recheck_state(row["captured_digest"], resolved["resolution"], resolved["digest"]),
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def add_role_assignment(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    stakeholder_key: str,
    role: str,
    scope_kind: str = "system",
    scope_ref: str = "",
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one role assignment (§1.1). Always a NEW row -- a role
    assignment carries no identity/supersede rule of its own beyond
    `superseded_by_id`; correction is an explicit developer action via the
    decision ledger (`stakeholder_role_assignment` subject kind), the same
    pattern `ux_design.add_upstream_ref` uses one layer over.

    `captured_digest` is the Stakeholder's OWN current `content_digest` at
    assignment time -- never a copy of any content this row itself owns,
    since a role assignment has none beyond its four identity columns.
    """
    now = time.time() if now is None else now
    _check_membership(role, STAKEHOLDER_ROLES, "role")
    _check_membership(scope_kind, ROLE_SCOPE_KINDS, "scope_kind")
    stakeholder = _get_stakeholder_row(conn, system_id, stakeholder_key)
    if stakeholder is None:
        raise NotFound(f"Stakeholder {stakeholder_key!r} not found")

    resolved = _resolve_target(conn, system_id, "stakeholder", stakeholder_key)
    captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""

    cur = conn.execute(
        """INSERT INTO stakeholder_role_assignment
               (system_id, stakeholder_id, stakeholder_key, role, scope_kind, scope_ref,
                captured_digest, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (system_id, stakeholder["id"], stakeholder_key, role, scope_kind, scope_ref,
         captured_digest, note, decision_method, created_by, now),
    )
    row = conn.execute("SELECT * FROM stakeholder_role_assignment WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _role_assignment_out_dict(conn, system_id, dict(row))


def _stakeholder_summary_dict(
    stakeholder: Dict[str, Any], revision_out: Optional[Dict[str, Any]], design_status: str, recheck_state: str
) -> Dict[str, Any]:
    return {
        "id": stakeholder["id"],
        "system_id": stakeholder["system_id"],
        "stakeholder_key": stakeholder["stakeholder_key"],
        "current_revision_id": stakeholder["current_revision_id"],
        "current_revision_number": revision_out["revision_number"] if revision_out else None,
        "display_name": revision_out["display_name"] if revision_out else "",
        "stakeholder_kind": revision_out["stakeholder_kind"] if revision_out else "other",
        "design_status": design_status,
        "recheck_state": recheck_state,
        "created_by": stakeholder["created_by"],
        "created_at": stakeholder["created_at"],
        "updated_at": stakeholder["updated_at"],
    }


def _stakeholder_overview(conn: sqlite3.Connection, system_id: int, stakeholder: Dict[str, Any]) -> Dict[str, Any]:
    revision_out = (
        _stakeholder_revision_out_dict(conn, stakeholder["current_revision_id"])
        if stakeholder["current_revision_id"] is not None
        else None
    )
    design_status, decision_row = derive_design_status(conn, system_id, "stakeholder", stakeholder["stakeholder_key"])
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)
    return _stakeholder_summary_dict(stakeholder, revision_out, design_status, recheck_state)


def get_stakeholder_detail(conn: sqlite3.Connection, system_id: int, stakeholder_key: str) -> Dict[str, Any]:
    stakeholder = _get_stakeholder_row(conn, system_id, stakeholder_key)
    if stakeholder is None:
        raise NotFound(f"Stakeholder {stakeholder_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    revision_out: Optional[Dict[str, Any]] = None
    if stakeholder["current_revision_id"] is not None:
        try:
            revision_out = _stakeholder_revision_out_dict(conn, stakeholder["current_revision_id"])
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    roles: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM stakeholder_role_assignment
               WHERE system_id = ? AND stakeholder_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, stakeholder["id"]),
        ).fetchall()
        roles = [_role_assignment_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "roles", exc)

    design_status, decision_row = derive_design_status(conn, system_id, "stakeholder", stakeholder_key)
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)

    summary = _stakeholder_summary_dict(stakeholder, revision_out, design_status, recheck_state)
    summary.update(
        {
            "current_revision": revision_out,
            "roles": roles,
            "degraded_sections": degraded_sections,
            "degraded_detail": degraded_detail,
        }
    )
    return summary


def list_stakeholders(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    stakeholders: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM stakeholder WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        stakeholders = [_stakeholder_overview(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "stakeholders", exc)
    return {"stakeholders": stakeholders, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}


def list_stakeholder_revisions(conn: sqlite3.Connection, system_id: int, stakeholder_key: str) -> Dict[str, Any]:
    stakeholder = _get_stakeholder_row(conn, system_id, stakeholder_key)
    if stakeholder is None:
        raise NotFound(f"Stakeholder {stakeholder_key!r} not found")
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    revisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id FROM stakeholder_revision WHERE stakeholder_id = ? ORDER BY revision_number DESC",
            (stakeholder["id"],),
        ).fetchall()
        revisions = [_stakeholder_revision_out_dict(conn, r["id"]) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "revisions", exc)
    return {
        "stakeholder_id": stakeholder["id"],
        "revisions": revisions,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


# --- Stakeholder Need ------------------------------------------------------------


def _get_need_row(conn: sqlite3.Connection, system_id: int, need_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM stakeholder_need WHERE system_id = ? AND need_key = ?", (system_id, need_key)
    ).fetchone()
    return dict(row) if row is not None else None


def create_need(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    need_key: str,
    stakeholder_key: str,
    need_kind: str = "unmet_need",
    statement: str = "",
    rationale: str = "",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not need_key:
        raise KeyRequired("need_key")
    _check_membership(need_kind, NEED_KINDS, "need_kind")
    if _get_stakeholder_row(conn, system_id, stakeholder_key) is None:
        raise NotFound(f"Stakeholder {stakeholder_key!r} not found")
    if _get_need_row(conn, system_id, need_key) is not None:
        raise KeyConflict(need_key)

    digest = need_digest(
        need_key=need_key, need_kind=need_kind, statement=statement, rationale=rationale,
        stakeholder_key=stakeholder_key,
    )

    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO stakeholder_need (system_id, need_key, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (system_id, need_key, created_by, now, now),
        )
        need_id = cur.lastrowid
        rev_cur = conn.execute(
            """INSERT INTO stakeholder_need_revision
                   (need_id, system_id, revision_number, need_kind, statement, rationale,
                    stakeholder_key, content_digest, created_by, created_at)
               VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
            (need_id, system_id, need_kind, statement, rationale, stakeholder_key, digest, created_by, now),
        )
        conn.execute("UPDATE stakeholder_need SET current_revision_id = ? WHERE id = ?", (rev_cur.lastrowid, need_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_need_detail(conn, system_id, need_key)


def add_need_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    need_key: str,
    need_kind: str = "unmet_need",
    statement: str = "",
    rationale: str = "",
    stakeholder_key: str = "",
    change_note: str = "",
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    _check_membership(need_kind, NEED_KINDS, "need_kind")
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    need = _get_need_row(conn, system_id, need_key)
    if need is None:
        raise NotFound(f"Need {need_key!r} not found")
    if stakeholder_key and _get_stakeholder_row(conn, system_id, stakeholder_key) is None:
        raise NotFound(f"Stakeholder {stakeholder_key!r} not found")

    digest = need_digest(
        need_key=need_key, need_kind=need_kind, statement=statement, rationale=rationale,
        stakeholder_key=stakeholder_key,
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = need["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM stakeholder_need_revision WHERE need_id = ?", (need["id"],)
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO stakeholder_need_revision
                   (need_id, system_id, revision_number, need_kind, statement, rationale,
                    stakeholder_key, content_digest, authored_by_kind, decision_method,
                    intelligence_run_id, change_note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (need["id"], system_id, revision_number, need_kind, statement, rationale, stakeholder_key,
             digest, authored_by_kind, decision_method, intelligence_run_id, change_note, created_by, now),
        )
        new_revision_id = cur.lastrowid
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE stakeholder_need_revision SET superseded_by_id = ? WHERE id = ?",
                (new_revision_id, prior_revision_id),
            )
        conn.execute(
            "UPDATE stakeholder_need SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, need["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_need_detail(conn, system_id, need_key)


def _need_revision_out_dict(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM stakeholder_need_revision WHERE id = ?", (revision_id,)).fetchone()
    if row is None:
        raise NotFound(f"Need revision {revision_id} not found")
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    return d


def _need_summary_dict(
    need: Dict[str, Any], revision_out: Optional[Dict[str, Any]], design_status: str, recheck_state: str
) -> Dict[str, Any]:
    return {
        "id": need["id"],
        "system_id": need["system_id"],
        "need_key": need["need_key"],
        "current_revision_id": need["current_revision_id"],
        "current_revision_number": revision_out["revision_number"] if revision_out else None,
        "need_kind": revision_out["need_kind"] if revision_out else "unmet_need",
        "statement": revision_out["statement"] if revision_out else "",
        "stakeholder_key": revision_out["stakeholder_key"] if revision_out else "",
        "design_status": design_status,
        "recheck_state": recheck_state,
        "created_by": need["created_by"],
        "created_at": need["created_at"],
        "updated_at": need["updated_at"],
    }


def _need_overview(conn: sqlite3.Connection, system_id: int, need: Dict[str, Any]) -> Dict[str, Any]:
    revision_out = (
        _need_revision_out_dict(conn, need["current_revision_id"]) if need["current_revision_id"] is not None else None
    )
    design_status, decision_row = derive_design_status(conn, system_id, "stakeholder_need", need["need_key"])
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)
    return _need_summary_dict(need, revision_out, design_status, recheck_state)


def get_need_detail(conn: sqlite3.Connection, system_id: int, need_key: str) -> Dict[str, Any]:
    need = _get_need_row(conn, system_id, need_key)
    if need is None:
        raise NotFound(f"Need {need_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    revision_out: Optional[Dict[str, Any]] = None
    if need["current_revision_id"] is not None:
        try:
            revision_out = _need_revision_out_dict(conn, need["current_revision_id"])
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    design_status, decision_row = derive_design_status(conn, system_id, "stakeholder_need", need_key)
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)

    summary = _need_summary_dict(need, revision_out, design_status, recheck_state)
    summary.update(
        {"current_revision": revision_out, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}
    )
    return summary


def list_needs(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    needs: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM stakeholder_need WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        needs = [_need_overview(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "needs", exc)
    return {"needs": needs, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}


def list_need_revisions(conn: sqlite3.Connection, system_id: int, need_key: str) -> Dict[str, Any]:
    need = _get_need_row(conn, system_id, need_key)
    if need is None:
        raise NotFound(f"Need {need_key!r} not found")
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    revisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id FROM stakeholder_need_revision WHERE need_id = ? ORDER BY revision_number DESC",
            (need["id"],),
        ).fetchall()
        revisions = [_need_revision_out_dict(conn, r["id"]) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "revisions", exc)
    return {
        "need_id": need["id"], "revisions": revisions,
        "degraded_sections": degraded_sections, "degraded_detail": degraded_detail,
    }


# --- Environment Observation ----------------------------------------------------


def _get_observation_row(conn: sqlite3.Connection, system_id: int, observation_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM environment_observation WHERE system_id = ? AND observation_key = ?",
        (system_id, observation_key),
    ).fetchone()
    return dict(row) if row is not None else None


def _impact_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_target(conn, system_id, row["target_ref_kind"], row["target_ref"])
    return {
        "id": row["id"],
        "observation_id": row["observation_id"],
        "impact_kind": row["impact_kind"],
        "target_ref_kind": row["target_ref_kind"],
        "target_ref": row["target_ref"],
        "target_row_id": row["target_row_id"],
        "target_resolution": resolved["resolution"],
        "captured_digest": row["captured_digest"],
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def create_observation(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    observation_key: str,
    statement: str = "",
    source_note: str = "",
    observation_confidence: str = "reported",
    observed_at: Optional[float] = None,
    supersedes_observation_key: Optional[str] = None,
    impacts: Optional[Sequence[Dict[str, Any]]] = None,
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one Environment Observation (§1.3/§3). NO revision chain and
    NO update path -- a correction is a NEW row that may name
    `supersedes_observation_key`; this row is never mutated afterward.
    """
    now = time.time() if now is None else now
    if not observation_key:
        raise KeyRequired("observation_key")
    _check_membership(observation_confidence, OBSERVATION_CONFIDENCES, "observation_confidence")
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    if _get_observation_row(conn, system_id, observation_key) is not None:
        raise KeyConflict(observation_key)

    impact_records = []
    for impact in impacts or []:
        impact_kind = impact.get("impact_kind", "")
        target_ref_kind = impact.get("target_ref_kind", "")
        target_ref = impact.get("target_ref", "")
        if impact_kind not in IMPACT_KINDS:
            raise ImpactKindInvalid(impact_kind)
        if target_ref_kind not in REF_KINDS:
            raise ImpactKindInvalid(target_ref_kind)
        impact_records.append(
            {
                "impact_kind": impact_kind, "target_ref_kind": target_ref_kind,
                "target_ref": target_ref, "note": impact.get("note", ""),
            }
        )

    digest = observation_digest(
        observation_key=observation_key, statement=statement, source_note=source_note,
        observation_confidence=observation_confidence, observed_at=observed_at,
    )

    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO environment_observation
                   (system_id, observation_key, statement, source_note, observation_confidence,
                    observed_at, supersedes_observation_key, content_digest, authored_by_kind,
                    decision_method, intelligence_run_id, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (system_id, observation_key, statement, source_note, observation_confidence, observed_at,
             supersedes_observation_key, digest, authored_by_kind, decision_method, intelligence_run_id,
             created_by, now),
        )
        observation_id = cur.lastrowid
        for record in impact_records:
            resolved = _resolve_target(conn, system_id, record["target_ref_kind"], record["target_ref"])
            captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
            conn.execute(
                """INSERT INTO environment_observation_impact
                       (system_id, observation_id, impact_kind, target_ref_kind, target_ref,
                        captured_digest, note, decision_method, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (system_id, observation_id, record["impact_kind"], record["target_ref_kind"],
                 record["target_ref"], captured_digest, record["note"], decision_method, created_by, now),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_observation_detail(conn, system_id, observation_key)


def _observation_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    design_status, decision_row = derive_design_status(conn, system_id, "environment_observation", row["observation_key"])
    recheck_state = derive_recheck_state(row["content_digest"], decision_row)
    return {
        "id": row["id"],
        "system_id": row["system_id"],
        "observation_key": row["observation_key"],
        "statement": row["statement"],
        "source_note": row["source_note"],
        "observation_confidence": row["observation_confidence"],
        "observed_at": row["observed_at"],
        "supersedes_observation_key": row["supersedes_observation_key"],
        "content_digest": row["content_digest"],
        "authored_by_kind": row["authored_by_kind"],
        "decision_method": row["decision_method"],
        "intelligence_run_id": row["intelligence_run_id"],
        "design_status": design_status,
        "recheck_state": recheck_state,
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def get_observation_detail(conn: sqlite3.Connection, system_id: int, observation_key: str) -> Dict[str, Any]:
    row = _get_observation_row(conn, system_id, observation_key)
    if row is None:
        raise NotFound(f"Observation {observation_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    impacts: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM environment_observation_impact WHERE system_id = ? AND observation_id = ? ORDER BY id",
            (system_id, row["id"]),
        ).fetchall()
        impacts = [_impact_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "impacts", exc)

    out = _observation_out_dict(conn, system_id, row)
    out.update({"impacts": impacts, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail})
    return out


def list_observations(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    observations: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM environment_observation WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        observations = [_observation_out_dict(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "observations", exc)
    return {"observations": observations, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}


# --- Value Exchange ---------------------------------------------------------------


def _get_exchange_row(conn: sqlite3.Connection, system_id: int, exchange_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM value_exchange WHERE system_id = ? AND exchange_key = ?", (system_id, exchange_key)
    ).fetchone()
    return dict(row) if row is not None else None


def _validate_exchange_fields(
    conn: sqlite3.Connection,
    system_id: int,
    *,
    provider_stakeholder_key: str,
    receiver_stakeholder_key: str,
    exchange_kind: str,
    value_statement: str,
    consideration_state: str,
    consideration_kind: Optional[str],
    consideration_statement: str,
    cadence: str,
    valid_from: Optional[float],
    valid_to: Optional[float],
) -> None:
    _check_membership(exchange_kind, EXCHANGE_KINDS, "exchange_kind")
    _check_membership(consideration_state, CONSIDERATION_STATES, "consideration_state")
    _check_membership(cadence, CADENCES, "cadence")
    if consideration_kind is not None:
        _check_membership(consideration_kind, EXCHANGE_KINDS, "consideration_kind")

    if _get_stakeholder_row(conn, system_id, provider_stakeholder_key) is None:
        raise NotFound(f"Stakeholder {provider_stakeholder_key!r} not found")
    if _get_stakeholder_row(conn, system_id, receiver_stakeholder_key) is None:
        raise NotFound(f"Stakeholder {receiver_stakeholder_key!r} not found")

    if provider_stakeholder_key == receiver_stakeholder_key and exchange_kind != "information":
        raise SelfLoop(provider_stakeholder_key)

    if not (value_statement or "").strip():
        raise ValueStatementRequired()

    if consideration_state == "present":
        if not consideration_kind or not (consideration_statement or "").strip():
            raise ConsiderationIncomplete()

    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise ValidityInverted()


def create_exchange(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    exchange_key: str,
    provider_stakeholder_key: str,
    receiver_stakeholder_key: str,
    exchange_kind: str,
    value_statement: str = "",
    consideration_state: str = "unknown",
    consideration_kind: Optional[str] = None,
    consideration_statement: str = "",
    channel: str = "",
    trigger: str = "",
    cadence: str = "unknown",
    valid_from: Optional[float] = None,
    valid_to: Optional[float] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not exchange_key:
        raise KeyRequired("exchange_key")
    _validate_exchange_fields(
        conn, system_id, provider_stakeholder_key=provider_stakeholder_key,
        receiver_stakeholder_key=receiver_stakeholder_key, exchange_kind=exchange_kind,
        value_statement=value_statement, consideration_state=consideration_state,
        consideration_kind=consideration_kind, consideration_statement=consideration_statement,
        cadence=cadence, valid_from=valid_from, valid_to=valid_to,
    )
    if _get_exchange_row(conn, system_id, exchange_key) is not None:
        raise KeyConflict(exchange_key)

    digest = exchange_digest(
        exchange_key=exchange_key, provider_stakeholder_key=provider_stakeholder_key,
        receiver_stakeholder_key=receiver_stakeholder_key, exchange_kind=exchange_kind,
        value_statement=value_statement, consideration_state=consideration_state,
        consideration_kind=consideration_kind, consideration_statement=consideration_statement,
        channel=channel, trigger=trigger, cadence=cadence, valid_from=valid_from, valid_to=valid_to,
    )

    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO value_exchange (system_id, exchange_key, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (system_id, exchange_key, created_by, now, now),
        )
        exchange_id = cur.lastrowid
        rev_cur = conn.execute(
            """INSERT INTO value_exchange_revision
                   (exchange_id, system_id, revision_number, provider_stakeholder_key,
                    receiver_stakeholder_key, exchange_kind, value_statement, consideration_state,
                    consideration_kind, consideration_statement, channel, trigger, cadence,
                    valid_from, valid_to, content_digest, created_by, created_at)
               VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (exchange_id, system_id, provider_stakeholder_key, receiver_stakeholder_key, exchange_kind,
             value_statement, consideration_state, consideration_kind, consideration_statement, channel,
             trigger, cadence, valid_from, valid_to, digest, created_by, now),
        )
        conn.execute(
            "UPDATE value_exchange SET current_revision_id = ? WHERE id = ?", (rev_cur.lastrowid, exchange_id)
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_exchange_detail(conn, system_id, exchange_key)


def add_exchange_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    exchange_key: str,
    provider_stakeholder_key: str,
    receiver_stakeholder_key: str,
    exchange_kind: str,
    value_statement: str = "",
    consideration_state: str = "unknown",
    consideration_kind: Optional[str] = None,
    consideration_statement: str = "",
    channel: str = "",
    trigger: str = "",
    cadence: str = "unknown",
    valid_from: Optional[float] = None,
    valid_to: Optional[float] = None,
    change_note: str = "",
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    _validate_exchange_fields(
        conn, system_id, provider_stakeholder_key=provider_stakeholder_key,
        receiver_stakeholder_key=receiver_stakeholder_key, exchange_kind=exchange_kind,
        value_statement=value_statement, consideration_state=consideration_state,
        consideration_kind=consideration_kind, consideration_statement=consideration_statement,
        cadence=cadence, valid_from=valid_from, valid_to=valid_to,
    )
    exchange = _get_exchange_row(conn, system_id, exchange_key)
    if exchange is None:
        raise NotFound(f"Exchange {exchange_key!r} not found")

    digest = exchange_digest(
        exchange_key=exchange_key, provider_stakeholder_key=provider_stakeholder_key,
        receiver_stakeholder_key=receiver_stakeholder_key, exchange_kind=exchange_kind,
        value_statement=value_statement, consideration_state=consideration_state,
        consideration_kind=consideration_kind, consideration_statement=consideration_statement,
        channel=channel, trigger=trigger, cadence=cadence, valid_from=valid_from, valid_to=valid_to,
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = exchange["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM value_exchange_revision WHERE exchange_id = ?",
            (exchange["id"],),
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO value_exchange_revision
                   (exchange_id, system_id, revision_number, provider_stakeholder_key,
                    receiver_stakeholder_key, exchange_kind, value_statement, consideration_state,
                    consideration_kind, consideration_statement, channel, trigger, cadence,
                    valid_from, valid_to, content_digest, authored_by_kind, decision_method,
                    intelligence_run_id, change_note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (exchange["id"], system_id, revision_number, provider_stakeholder_key, receiver_stakeholder_key,
             exchange_kind, value_statement, consideration_state, consideration_kind, consideration_statement,
             channel, trigger, cadence, valid_from, valid_to, digest, authored_by_kind, decision_method,
             intelligence_run_id, change_note, created_by, now),
        )
        new_revision_id = cur.lastrowid
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE value_exchange_revision SET superseded_by_id = ? WHERE id = ?",
                (new_revision_id, prior_revision_id),
            )
        conn.execute(
            "UPDATE value_exchange SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, exchange["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_exchange_detail(conn, system_id, exchange_key)


def _exchange_revision_out_dict(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM value_exchange_revision WHERE id = ?", (revision_id,)).fetchone()
    if row is None:
        raise NotFound(f"Exchange revision {revision_id} not found")
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    return d


def _exchange_summary_dict(
    exchange: Dict[str, Any], revision_out: Optional[Dict[str, Any]], design_status: str, recheck_state: str
) -> Dict[str, Any]:
    validity_state = (
        derive_validity_state(revision_out["valid_from"], revision_out["valid_to"]) if revision_out else "unbounded"
    )
    return {
        "id": exchange["id"],
        "system_id": exchange["system_id"],
        "exchange_key": exchange["exchange_key"],
        "current_revision_id": exchange["current_revision_id"],
        "current_revision_number": revision_out["revision_number"] if revision_out else None,
        "provider_stakeholder_key": revision_out["provider_stakeholder_key"] if revision_out else "",
        "receiver_stakeholder_key": revision_out["receiver_stakeholder_key"] if revision_out else "",
        "exchange_kind": revision_out["exchange_kind"] if revision_out else None,
        "value_statement": revision_out["value_statement"] if revision_out else "",
        "validity_state": validity_state,
        "design_status": design_status,
        "recheck_state": recheck_state,
        "created_by": exchange["created_by"],
        "created_at": exchange["created_at"],
        "updated_at": exchange["updated_at"],
    }


def _exchange_overview(conn: sqlite3.Connection, system_id: int, exchange: Dict[str, Any]) -> Dict[str, Any]:
    revision_out = (
        _exchange_revision_out_dict(conn, exchange["current_revision_id"])
        if exchange["current_revision_id"] is not None
        else None
    )
    design_status, decision_row = derive_design_status(conn, system_id, "value_exchange", exchange["exchange_key"])
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)
    return _exchange_summary_dict(exchange, revision_out, design_status, recheck_state)


def get_exchange_detail(conn: sqlite3.Connection, system_id: int, exchange_key: str) -> Dict[str, Any]:
    exchange = _get_exchange_row(conn, system_id, exchange_key)
    if exchange is None:
        raise NotFound(f"Exchange {exchange_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    revision_out: Optional[Dict[str, Any]] = None
    if exchange["current_revision_id"] is not None:
        try:
            revision_out = _exchange_revision_out_dict(conn, exchange["current_revision_id"])
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    design_status, decision_row = derive_design_status(conn, system_id, "value_exchange", exchange_key)
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)

    summary = _exchange_summary_dict(exchange, revision_out, design_status, recheck_state)
    summary.update(
        {"current_revision": revision_out, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}
    )
    return summary


def list_exchanges(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    exchanges: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM value_exchange WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        exchanges = [_exchange_overview(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "exchanges", exc)
    return {"exchanges": exchanges, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}


def list_exchange_revisions(conn: sqlite3.Connection, system_id: int, exchange_key: str) -> Dict[str, Any]:
    exchange = _get_exchange_row(conn, system_id, exchange_key)
    if exchange is None:
        raise NotFound(f"Exchange {exchange_key!r} not found")
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    revisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id FROM value_exchange_revision WHERE exchange_id = ? ORDER BY revision_number DESC",
            (exchange["id"],),
        ).fetchall()
        revisions = [_exchange_revision_out_dict(conn, r["id"]) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "revisions", exc)
    return {
        "exchange_id": exchange["id"], "revisions": revisions,
        "degraded_sections": degraded_sections, "degraded_detail": degraded_detail,
    }


# --- §5.1. stakeholder_ref -------------------------------------------------------


def _ref_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_target(conn, system_id, row["ref_kind"], row["target_ref"])
    current_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
    return {
        "id": row["id"],
        "source_kind": row["source_kind"],
        "source_key": row["source_key"],
        "ref_kind": row["ref_kind"],
        "target_ref": row["target_ref"],
        "target_row_id": row["target_row_id"],
        "relation_status": _DECISION_METHOD_TO_RELATION_STATUS.get(row["decision_method"], "derived"),
        "target_resolution": resolved["resolution"],
        "recheck_state": _ref_recheck_state(row["captured_digest"], resolved["resolution"], current_digest),
        "captured_digest": row["captured_digest"],
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def create_ref(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    source_kind: str,
    source_key: str,
    ref_kind: str,
    target_ref: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one `stakeholder_ref` row (§5.1). Always a NEW row -- like
    `ux_journey_upstream_ref`, this table carries no identity/supersede
    dedup rule of its own; correction is an explicit developer action via
    the decision ledger (`stakeholder_ref` subject kind).

    `captured_digest` is whatever `_resolve_target` resolves to RIGHT NOW --
    empty when the target does not currently resolve, or when its kind is
    not yet wired up (Issue #421's `_resolve_target` seam).
    """
    now = time.time() if now is None else now
    if ref_kind not in REF_KINDS:
        raise RefKindInvalid(ref_kind)
    if not source_key:
        raise KeyRequired("source_key")
    if not target_ref:
        raise KeyRequired("target_ref")

    resolved = _resolve_target(conn, system_id, ref_kind, target_ref)
    if resolved["resolution"] == "unresolved":
        raise TargetNotFound(target_ref)
    captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""

    cur = conn.execute(
        """INSERT INTO stakeholder_ref
               (system_id, source_kind, source_key, ref_kind, target_ref, captured_digest,
                note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (system_id, source_kind, source_key, ref_kind, target_ref, captured_digest, note,
         decision_method, created_by, now),
    )
    row = conn.execute("SELECT * FROM stakeholder_ref WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _ref_out_dict(conn, system_id, dict(row))


def list_refs(
    conn: sqlite3.Connection, system_id: int, *, source_kind: Optional[str] = None, source_key: Optional[str] = None
) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    refs: List[Dict[str, Any]] = []
    try:
        query = "SELECT * FROM stakeholder_ref WHERE system_id = ? AND superseded_by_id IS NULL"
        params: List[Any] = [system_id]
        if source_kind is not None:
            query += " AND source_kind = ?"
            params.append(source_kind)
        if source_key is not None:
            query += " AND source_key = ?"
            params.append(source_key)
        query += " ORDER BY id DESC"
        rows = conn.execute(query, params).fetchall()
        refs = [_ref_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "refs", exc)
    return {"refs": refs, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}


# --- §6. Evidence -----------------------------------------------------------------


def create_evidence_ref(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    subject_kind: str,
    subject_key: str,
    evidence_kind: str,
    evidence_ref: str = "",
    statement: str = "",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    _check_membership(evidence_kind, EVIDENCE_KINDS, "evidence_kind")
    if subject_kind not in ("stakeholder_need", "environment_observation", "value_exchange"):
        raise StakeholderValidationError(
            f"subject_kind must be one of stakeholder_need, environment_observation, "
            f"value_exchange; got {subject_kind!r}"
        )
    if not subject_key:
        raise KeyRequired("subject_key")

    captured_digest = ""
    if subject_kind == "stakeholder_need":
        resolved = _resolve_target(conn, system_id, "stakeholder_need", subject_key)
        captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
    elif subject_kind == "value_exchange":
        resolved = _resolve_target(conn, system_id, "value_exchange", subject_key)
        captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
    elif subject_kind == "environment_observation":
        row = _get_observation_row(conn, system_id, subject_key)
        captured_digest = row["content_digest"] if row is not None else ""

    cur = conn.execute(
        """INSERT INTO stakeholder_evidence_ref
               (system_id, subject_kind, subject_key, evidence_kind, evidence_ref, statement,
                captured_digest, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (system_id, subject_kind, subject_key, evidence_kind, evidence_ref, statement,
         captured_digest, created_by, now),
    )
    row = conn.execute("SELECT * FROM stakeholder_evidence_ref WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_evidence_refs(conn: sqlite3.Connection, system_id: int, subject_kind: str, subject_key: str) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    evidence_refs: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM stakeholder_evidence_ref
               WHERE system_id = ? AND subject_kind = ? AND subject_key = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, subject_kind, subject_key),
        ).fetchall()
        evidence_refs = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "evidence_refs", exc)
    return {
        "evidence_refs": evidence_refs, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail,
    }


# --- §3. The decision ledger ------------------------------------------------------


def _resolve_decision_subject(
    conn: sqlite3.Connection, system_id: int, subject_kind: str, subject_key: str
) -> Tuple[int, str, Optional[int]]:
    """`(subject_row_id, current_digest, captured_revision_id)` for one
    decidable subject (§10's `StakeholderSubjectKind`). Stakeholder/Need/
    Exchange compare against their CURRENT revision's `content_digest`
    (empty when no revision exists yet); Environment Observation compares
    against its own row directly (no revision chain, §3). The two reference/
    link kinds carry no revision chain of their own either, so their
    "current digest" is simply what is stored on their own row right now --
    the same discipline `ux_design._resolve_decision_subject` applies one
    layer over. `subject_key` for those two is the row's own numeric id as a
    string, since (unlike Stakeholder/Need/Exchange) they carry no
    developer-supplied slug.
    """
    if subject_kind == "stakeholder":
        stakeholder = _get_stakeholder_row(conn, system_id, subject_key)
        if stakeholder is None:
            raise SubjectNotFound(subject_key)
        revision = (
            conn.execute(
                "SELECT * FROM stakeholder_revision WHERE id = ?", (stakeholder["current_revision_id"],)
            ).fetchone()
            if stakeholder["current_revision_id"] is not None
            else None
        )
        digest = revision["content_digest"] if revision else ""
        return stakeholder["id"], digest, (revision["id"] if revision else None)

    if subject_kind == "stakeholder_need":
        need = _get_need_row(conn, system_id, subject_key)
        if need is None:
            raise SubjectNotFound(subject_key)
        revision = (
            conn.execute(
                "SELECT * FROM stakeholder_need_revision WHERE id = ?", (need["current_revision_id"],)
            ).fetchone()
            if need["current_revision_id"] is not None
            else None
        )
        digest = revision["content_digest"] if revision else ""
        return need["id"], digest, (revision["id"] if revision else None)

    if subject_kind == "value_exchange":
        exchange = _get_exchange_row(conn, system_id, subject_key)
        if exchange is None:
            raise SubjectNotFound(subject_key)
        revision = (
            conn.execute(
                "SELECT * FROM value_exchange_revision WHERE id = ?", (exchange["current_revision_id"],)
            ).fetchone()
            if exchange["current_revision_id"] is not None
            else None
        )
        digest = revision["content_digest"] if revision else ""
        return exchange["id"], digest, (revision["id"] if revision else None)

    if subject_kind == "environment_observation":
        row = _get_observation_row(conn, system_id, subject_key)
        if row is None:
            raise SubjectNotFound(subject_key)
        return row["id"], row["content_digest"], None

    if subject_kind == "stakeholder_ref":
        if not subject_key.isdigit():
            raise SubjectNotFound(subject_key)
        row = conn.execute(
            "SELECT * FROM stakeholder_ref WHERE id = ? AND system_id = ? AND superseded_by_id IS NULL",
            (int(subject_key), system_id),
        ).fetchone()
        if row is None:
            raise SubjectNotFound(subject_key)
        return row["id"], row["captured_digest"], None

    if subject_kind == "stakeholder_role_assignment":
        if not subject_key.isdigit():
            raise SubjectNotFound(subject_key)
        row = conn.execute(
            """SELECT * FROM stakeholder_role_assignment
               WHERE id = ? AND system_id = ? AND superseded_by_id IS NULL""",
            (int(subject_key), system_id),
        ).fetchone()
        if row is None:
            raise SubjectNotFound(subject_key)
        return row["id"], row["captured_digest"], None

    raise StakeholderValidationError(f"subject_kind must be one of {SUBJECT_KINDS}; got {subject_kind!r}")


def record_decision(
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
    """The ONE write behind `design_status` (§3). Always appends a new row
    and supersedes the prior current one, mirroring `ux_design.
    record_design_decision` / `purpose_chain.record_relation_decision`'s
    pattern exactly. `decision_method` is hardcoded `'manual'` -- there is
    no parameter to make it anything else, matching the
    `CHECK (decision_method = 'manual')` constraint on `stakeholder_decision`
    itself.
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
            """SELECT id FROM stakeholder_decision
               WHERE system_id = ? AND subject_kind = ? AND subject_key = ? AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, subject_kind, subject_key),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO stakeholder_decision
                   (system_id, subject_kind, subject_key, subject_row_id, decision, rationale,
                    captured_digest, captured_revision_id, decided_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (system_id, subject_kind, subject_key, subject_row_id, decision, rationale,
             current_digest, captured_revision_id, decided_by, now),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute("UPDATE stakeholder_decision SET superseded_by_id = ? WHERE id = ?", (new_id, prior["id"]))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM stakeholder_decision WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


# --- §12. Saved views: display settings only, never a fact ------------------------


def get_view_preference(conn: sqlite3.Connection, system_id: int, created_by: str) -> Dict[str, Any]:
    """§12: display settings only. A missing row is the honest default --
    never an error, since nobody has saved a preference yet."""
    row = conn.execute(
        "SELECT * FROM stakeholder_view_preference WHERE system_id = ? AND created_by = ?",
        (system_id, created_by),
    ).fetchone()
    if row is None:
        return {
            "system_id": system_id, "created_by": created_by, "active_view": "",
            "filters": {}, "collapsed_refs": [], "pinned_refs": [], "updated_at": 0.0,
        }
    d = dict(row)
    return {
        "system_id": d["system_id"],
        "created_by": d["created_by"],
        "active_view": d["active_view"],
        "filters": json.loads(d["filters_json"] or "{}"),
        "collapsed_refs": json.loads(d["collapsed_refs_json"] or "[]"),
        "pinned_refs": json.loads(d["pinned_refs_json"] or "[]"),
        "updated_at": d["updated_at"],
    }


def update_view_preference(
    conn: sqlite3.Connection,
    system_id: int,
    created_by: str,
    *,
    active_view: str = "",
    filters: Optional[Dict[str, Any]] = None,
    collapsed_refs: Optional[Sequence[str]] = None,
    pinned_refs: Optional[Sequence[str]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """§12: a plain upsert is appropriate HERE, unlike everywhere else in
    this module -- this table is a per-viewer convenience setting, not an
    append-only fact, and no projection in this Epic reads it (invariant
    10). No coordinate/layout field exists on this function's signature,
    structurally."""
    now = time.time() if now is None else now
    filters_json = json.dumps(dict(filters or {}), sort_keys=True)
    collapsed_json = json.dumps(list(collapsed_refs or []))
    pinned_json = json.dumps(list(pinned_refs or []))
    conn.execute(
        """INSERT INTO stakeholder_view_preference
               (system_id, created_by, active_view, filters_json, collapsed_refs_json,
                pinned_refs_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (system_id, created_by) DO UPDATE SET
               active_view = excluded.active_view,
               filters_json = excluded.filters_json,
               collapsed_refs_json = excluded.collapsed_refs_json,
               pinned_refs_json = excluded.pinned_refs_json,
               updated_at = excluded.updated_at""",
        (system_id, created_by, active_view, filters_json, collapsed_json, pinned_json, now),
    )
    return get_view_preference(conn, system_id, created_by)
