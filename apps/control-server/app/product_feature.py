"""Product Feature: identity, revisions, and links (Issue #431, Epic #427).

`docs/product-objective-lineage.md` §7.2 / §8 is the canonical contract this
module implements. This is a deterministic domain service -- **no LLM call
anywhere in this module** (Principle 6). It is modelled closely on
`app/ux_design.py` (same typed-exception hierarchy, the same
`get_args`-mirrored finite vocabularies, the same `content_digest` helper,
the same guarded `_degrade` sections, and the same manual
`BEGIN`/`INSERT`/supersede/`COMMIT` transaction shape).

Two rules this module must never violate (§0 / §7.2):

* **No new understanding model, no upstream/downstream content copies.**
  Feature content is genuinely new and IS stored here (`product_feature`,
  `product_feature_revision`), but every link this module owns
  (`product_feature_requirement_link` / `_capability_link` / `_target_link`
  / `_draft_link`) never copies its target's content -- only a stable
  reference plus a `captured_digest`, resolved against exactly one
  canonical source per kind at READ time. Target resolution for the 9
  `ProductFeatureLinkKind` values REUSES `solution_design._resolve_target`
  (evolution_node / component / probe_point / static_flow / runtime_flow)
  rather than reimplementing it (§7.2's explicit instruction); Capability
  resolution reuses `node_design._resolve_capability` verbatim. Three kinds
  those two resolvers do not cover -- `solution_design` (the entity itself,
  which `solution_design._resolve_target` never resolves TO, only FROM),
  `experiment`, and `replay_run`, and `purpose_outcome_criterion` -- get a
  minimal local read against their own canonical table (see
  `_resolve_solution_design_target` / `_resolve_experiment_target` /
  `_resolve_replay_run_target` / `_resolve_purpose_outcome_criterion_target`
  below); this module never re-derives what those tables' OWN domain
  modules already decide.
* **`design_status` is derived, never stored** (§4.2/§7.2): the latest
  non-superseded `product_feature_decision` row for `(system_id,
  feature_key)`, folded through the fixed 4-row mapping
  `ProductMilestoneDecisionKind` already uses one layer over
  (confirm/reject/retire/reinstate -> confirmed/rejected/retired/proposed).
  `recheck_state` is a SEPARATE axis -- a stale `confirmed` Feature stays
  `confirmed` (#337/#338/#349's "a stored lifecycle value can drift from the
  rows it describes, a derived one cannot").

Connection discipline (`.claude/skills/control-server/SKILL.md`): every
function here takes an already-open `conn` and performs no external call
-- there is no `git`/LLM round trip anywhere in this module, so unlike
`ux_design.create_artifact_reference` nothing here needs to manage its own
connection lifecycle.

probe-agent:
  role: Deterministic Product Feature identity / revision / link domain service
  capability: product-objective-lineage
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify Feature revisions are append-only with a reproducible content digest restricted to title/statement/rationale/scope_note/summary, that design_status is always derived from the decision ledger rather than stored, that every link resolves its target through the target's own canonical source at read time without ever copying its content, and that a Feature's identity and revision history survive a Feature Intelligence snapshot rebuild that only makes its feature_drafts link unresolved.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple, get_args

from . import node_design, solution_design, state_facts
from .models import (
    ProductAuthorshipKind,
    ProductFeatureLinkKind,
    ProductMilestoneDecisionKind,
    ProductRecheckState,
    ProductRefTargetResolution,
    ProductRevisionState,
)

__all__ = [
    "ProductFeatureError",
    "ProductFeatureValidationError",
    "NotFound",
    "KeyRequired",
    "KeyConflict",
    "RequirementNotFound",
    "CapabilityNotFound",
    "DraftNotFound",
    "SubjectNotFound",
    "LinkKindInvalid",
    "DecisionStaleDigest",
    "NotDecidable",
    "content_digest",
    "feature_revision_digest",
    "create_feature",
    "add_feature_revision",
    "get_feature_detail",
    "list_features",
    "derive_design_status",
    "derive_recheck_state",
    "record_feature_decision",
    "add_requirement_link",
    "add_capability_link",
    "add_target_link",
    "add_draft_link",
]


# --- §0. Finite vocabularies, mirrored from app/models.py with get_args -------

AUTHORSHIP_KINDS: Tuple[str, ...] = get_args(ProductAuthorshipKind)
DECISION_KINDS: Tuple[str, ...] = get_args(ProductMilestoneDecisionKind)
TARGET_LINK_KINDS: Tuple[str, ...] = get_args(ProductFeatureLinkKind)
RECHECK_STATES: Tuple[str, ...] = get_args(ProductRecheckState)
REVISION_STATES: Tuple[str, ...] = get_args(ProductRevisionState)
TARGET_RESOLUTIONS: Tuple[str, ...] = get_args(ProductRefTargetResolution)

#: §4.3's fixed decision-ledger fold, IDENTICAL to `ux_design.
#: _DECISION_TO_DESIGN_STATUS` and to `product_milestone_decision`'s own
#: fold -- reused as a module-level constant rather than re-derived, since
#: `ProductFeatureDecisionOut.decision` already reuses
#: `ProductMilestoneDecisionKind`'s vocabulary verbatim (§7.2 comment on
#: `product_feature_decision`).
_DECISION_TO_DESIGN_STATUS: Dict[str, str] = {
    "confirm": "confirmed",
    "reject": "rejected",
    "retire": "retired",
    "reinstate": "proposed",
}


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise ProductFeatureValidationError(
            f"{field_name} must be one of {', '.join(vocabulary)}; got {value!r}"
        )


# --- Errors -------------------------------------------------------------------


class ProductFeatureError(ValueError):
    """Base class for every failure this module raises."""


class ProductFeatureValidationError(ProductFeatureError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class NotFound(ProductFeatureError):
    """A referenced row does not exist, or belongs to another System.

    The two are deliberately the same error -- telling them apart would let
    a caller probe another System's ids (the same rule
    `ux_design.NotFound` / `node_design.NodeDesignNotFoundError` document).
    """


class KeyRequired(ProductFeatureError):
    """`feature_key` was empty (422 `product_feature_key_required`, §4.1)."""


class KeyConflict(ProductFeatureError):
    """A `feature_key` already exists in this System (409
    `product_feature_key_conflict`)."""


class RequirementNotFound(NotFound):
    """A requirement-link's `requirement_key` does not resolve in this
    System (404)."""


class CapabilityNotFound(NotFound):
    """A capability-link's `capability_entity_id` does not resolve in this
    System (404)."""


class DraftNotFound(NotFound):
    """A draft-link's `feature_draft_id` does not resolve in this System
    (404)."""


class SubjectNotFound(NotFound):
    """A decision's subject Feature does not exist (404)."""


class LinkKindInvalid(ProductFeatureError):
    """A target link's `link_kind` is outside `ProductFeatureLinkKind` (422
    `product_link_kind_invalid`, §10.1)."""


class DecisionStaleDigest(ProductFeatureError):
    """The caller's `captured_digest` does not match the Feature's current
    content digest (409 `product_feature_decision_stale_digest`)."""


class NotDecidable(ProductFeatureError):
    """The requested decision is illegal from the Feature's current
    `design_status` (422 `product_feature_not_decidable`) -- e.g. `confirm`
    on a `retired` Feature, or `reinstate` on a `proposed` one."""


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


# --- §8. Digests ----------------------------------------------------------------


def content_digest(payload: Dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON form of `payload` (§8). Same
    canonicalization every other module in this Epic uses: `sort_keys=True,
    ensure_ascii=False, separators=(",", ":")`."""
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def feature_revision_digest(
    *, title: str, statement: str, rationale: str, scope_note: str, summary: str
) -> str:
    """§8's Feature revision digest: `title, statement, rationale,
    scope_note, summary` only. `created_by` / `created_at` /
    `revision_number` / `change_note` are deliberately EXCLUDED -- a
    recheck must fire on a MEANING change, never on the mere existence of a
    new record (#308's `confirmation_id` exclusion, #337's Intent `status`
    exclusion, applied here)."""
    return content_digest(
        {
            "title": title,
            "statement": statement,
            "rationale": rationale,
            "scope_note": scope_note,
            "summary": summary,
        }
    )


def _recheck_state(captured_digest: str, resolution: str, current_digest: str) -> str:
    """Shared recheck-state fold for a reference/link (§4.6's pattern,
    reused for Feature's links): `not_captured` is fail-closed for an empty
    `captured_digest` (never treated as `current`, mirroring #337's
    `premise_not_captured`); an unresolved/unavailable target is always
    `stale`; only a resolved target with a matching digest reads
    `current`."""
    if not captured_digest:
        return "not_captured"
    if resolution != "resolved":
        return "stale"
    if captured_digest != current_digest:
        return "stale"
    return "current"


# --- §4.1 / §7.2. Feature identity ---------------------------------------------


def _get_feature_row(conn: sqlite3.Connection, system_id: int, feature_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM product_feature WHERE system_id = ? AND feature_key = ?",
        (system_id, feature_key),
    ).fetchone()
    return dict(row) if row is not None else None


def create_feature(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    feature_key: str,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    if not feature_key:
        raise KeyRequired("feature_key")
    if _get_feature_row(conn, system_id, feature_key) is not None:
        raise KeyConflict(feature_key)

    cur = conn.execute(
        """INSERT INTO product_feature (system_id, feature_key, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (system_id, feature_key, created_by, now, now),
    )
    row = conn.execute("SELECT * FROM product_feature WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


# --- §4.2 / §7.2. design_status / recheck_state ---------------------------------


def derive_design_status(
    conn: sqlite3.Connection, system_id: int, feature_key: str
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """§4.2/§7.2: the latest non-superseded `product_feature_decision` row,
    folded through the fixed 4-row mapping. No row -> `proposed`. Returns
    the status plus the decision row itself (or `None`), so callers needing
    the row's `captured_digest` for `recheck_state` do not requery."""
    row = conn.execute(
        """SELECT * FROM product_feature_decision
           WHERE system_id = ? AND feature_key = ? AND superseded_by_id IS NULL
           ORDER BY id DESC LIMIT 1""",
        (system_id, feature_key),
    ).fetchone()
    if row is None:
        return "proposed", None
    decision_row = dict(row)
    return _DECISION_TO_DESIGN_STATUS[decision_row["decision"]], decision_row


def derive_recheck_state(current_digest: str, decision_row: Optional[Dict[str, Any]]) -> str:
    """§4.2: `stale` only when the CURRENTLY effective decision is a
    `confirm` whose `captured_digest` no longer matches. `design_status`
    stays `confirmed` regardless -- the decision row itself is never
    touched; only this independent axis moves."""
    if decision_row is not None and decision_row["decision"] == "confirm":
        if decision_row["captured_digest"] != current_digest:
            return "stale"
    return "current"


def record_feature_decision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    feature_key: str,
    decision: str,
    rationale: str = "",
    captured_digest: str = "",
    decided_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The ONE write behind Feature's `design_status` (§4.2/§4.3). Always
    appends a new row and supersedes the prior current one.
    `decision_method` is hardcoded `'manual'` -- there is no parameter to
    make it anything else, matching `CHECK (decision_method = 'manual')` on
    `product_feature_decision` itself (§0 invariant 3: an AI-proposed
    revision cannot confirm/reject/retire/reinstate itself)."""
    now = time.time() if now is None else now
    _check_membership(decision, DECISION_KINDS, "decision")

    feature = _get_feature_row(conn, system_id, feature_key)
    if feature is None:
        raise SubjectNotFound(feature_key)

    current_digest = ""
    captured_revision_id: Optional[int] = None
    if feature["current_revision_id"] is not None:
        rev = conn.execute(
            "SELECT * FROM product_feature_revision WHERE id = ?", (feature["current_revision_id"],)
        ).fetchone()
        if rev is not None:
            current_digest = rev["content_digest"]
            captured_revision_id = rev["id"]

    if captured_digest and captured_digest != current_digest:
        raise DecisionStaleDigest(feature_key)

    prior_status, _ = derive_design_status(conn, system_id, feature_key)
    if decision == "reinstate":
        if prior_status not in ("rejected", "retired"):
            raise NotDecidable(feature_key)
    else:
        if prior_status in ("rejected", "retired"):
            raise NotDecidable(feature_key)

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_feature_decision
               WHERE system_id = ? AND feature_key = ? AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, feature_key),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_feature_decision
                   (system_id, feature_id, feature_key, decision, rationale, captured_digest,
                    captured_revision_id, decided_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, feature["id"], feature_key, decision, rationale, current_digest,
                captured_revision_id, decided_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE product_feature_decision SET superseded_by_id = ? WHERE id = ?",
                (new_id, prior["id"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_feature_decision WHERE id = ?", (new_id,)).fetchone()
    return dict(row)


# --- Feature revisions ------------------------------------------------------------


def _feature_revision_out_dict(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM product_feature_revision WHERE id = ?", (revision_id,)).fetchone()
    if row is None:
        raise NotFound(f"Feature revision {revision_id} not found")
    d = dict(row)
    d["revision_state"] = "superseded" if d["superseded_by_id"] is not None else "current"
    return d


def add_feature_revision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    feature_key: str,
    title: str = "",
    statement: str = "",
    rationale: str = "",
    scope_note: str = "",
    summary: str = "",
    change_note: str = "",
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Append one Feature revision (§4.2/§7.2/§8). `authored_by_kind` /
    `decision_method` / `intelligence_run_id` are parameters on this DOMAIN
    function, but `routes/product_features.py`'s public write endpoint
    never accepts them from the request body -- it always passes
    `authored_by_kind="developer"`, `decision_method="manual"` (this module
    calls no LLM anywhere; an AI-assisted authoring flow is out of this
    issue's scope, mirroring `ux_design.add_journey_revision`'s identical
    note)."""
    now = time.time() if now is None else now
    _check_membership(authored_by_kind, AUTHORSHIP_KINDS, "authored_by_kind")
    feature = _get_feature_row(conn, system_id, feature_key)
    if feature is None:
        raise NotFound(f"Feature {feature_key!r} not found")

    digest = feature_revision_digest(
        title=title, statement=statement, rationale=rationale, scope_note=scope_note, summary=summary
    )

    conn.execute("BEGIN")
    try:
        prior_revision_id = feature["current_revision_id"]
        max_row = conn.execute(
            "SELECT MAX(revision_number) AS n FROM product_feature_revision WHERE feature_id = ?",
            (feature["id"],),
        ).fetchone()
        revision_number = (max_row["n"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO product_feature_revision
                   (feature_id, system_id, revision_number, title, statement, rationale, scope_note,
                    summary, content_digest, authored_by_kind, decision_method, intelligence_run_id,
                    change_note, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                feature["id"], system_id, revision_number, title, statement, rationale, scope_note,
                summary, digest, authored_by_kind, decision_method, intelligence_run_id, change_note,
                created_by, now,
            ),
        )
        new_revision_id = cur.lastrowid
        if prior_revision_id is not None:
            conn.execute(
                "UPDATE product_feature_revision SET superseded_by_id = ? WHERE id = ?",
                (new_revision_id, prior_revision_id),
            )
        conn.execute(
            "UPDATE product_feature SET current_revision_id = ?, updated_at = ? WHERE id = ?",
            (new_revision_id, now, feature["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return get_feature_detail(conn, system_id, feature_key)


# --- §7.2. Requirement link (many-to-many to ux_requirement) --------------------


def _requirement_by_key(conn: sqlite3.Connection, system_id: int, requirement_key: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM ux_requirement WHERE system_id = ? AND requirement_key = ?",
        (system_id, requirement_key),
    ).fetchone()
    return dict(row) if row is not None else None


def _requirement_current_revision(conn: sqlite3.Connection, requirement_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if requirement_id is None:
        return None
    row = conn.execute(
        "SELECT * FROM ux_requirement WHERE id = ?", (requirement_id,)
    ).fetchone()
    if row is None or row["current_revision_id"] is None:
        return None
    rev = conn.execute(
        "SELECT * FROM ux_requirement_revision WHERE id = ?", (row["current_revision_id"],)
    ).fetchone()
    return dict(rev) if rev is not None else None


def _requirement_link_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    """§7.2/§4.6: resolution and recheck are two INDEPENDENT axes, and this
    link now reports both, like its Capability and target siblings.

    `recheck_state` alone cannot carry the distinction: it folds "the
    Requirement is gone" and "its revision moved" into one `stale` value,
    because `ProductRecheckState` has no `unresolved` member. A consumer
    that needs to know whether the target still exists -- Functional
    Lineage, deciding whether this link may become a graph edge -- was
    left querying `ux_requirement` itself to find out.

    `unavailable` is the third answer and is never folded into
    `unresolved`: "the Requirement is not there" and "we could not read
    whether it is there" send a developer to different places (§0-8)."""
    try:
        requirement = _requirement_by_key(conn, system_id, row["requirement_key"])
    except sqlite3.OperationalError:  # pragma: no cover - defensive
        requirement = None
        resolution = "unavailable"
        current_digest = ""
    else:
        resolution = ""
    if not resolution:
        if requirement is None:
            current_digest = ""
            resolution = "unresolved"
        else:
            # `resolved` means the IDENTITY ROW exists, independent of
            # whether it has content yet -- the same rule every sibling
            # resolver uses (`node_design._resolve_capability` resolves on
            # the entity row alone). A Requirement whose first revision has
            # not been written is a real, current entity, not a phantom, and
            # calling it `unresolved` would drop it out of the lineage graph
            # entirely. Whether its content can be trusted is the SEPARATE
            # `recheck_state` axis, which goes `stale` on the empty digest
            # below (§4.6: resolution and recheck never fold together).
            revision = _requirement_current_revision(conn, requirement["id"])
            current_digest = revision["content_digest"] if revision is not None else ""
            resolution = "resolved"
    return {
        "id": row["id"],
        "feature_id": row["feature_id"],
        "requirement_id": requirement["id"] if requirement is not None else None,
        "requirement_key": row["requirement_key"],
        "captured_requirement_revision_id": row["captured_requirement_revision_id"],
        "captured_digest": row["captured_digest"],
        "target_resolution": resolution,
        "recheck_state": _recheck_state(row["captured_digest"], resolution, current_digest),
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def add_requirement_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    feature_key: str,
    requirement_key: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one Feature<->Requirement link (§7.2). The Requirement MUST
    exist at link time (404 otherwise) -- the same rule
    `solution_design.add_requirement_link` applies to its own many-to-many
    Requirement bridge one layer over, since both entities are this Epic's
    own siblings rather than an aspirational external target. A repeat link
    to the same Requirement supersedes the prior current row (the DB's
    `ux_product_feature_requirement_current` partial unique index enforces
    at most one current row per `(feature_id, requirement_key)`)."""
    now = time.time() if now is None else now
    feature = _get_feature_row(conn, system_id, feature_key)
    if feature is None:
        raise NotFound(f"Feature {feature_key!r} not found")
    requirement = _requirement_by_key(conn, system_id, requirement_key)
    if requirement is None:
        raise RequirementNotFound(requirement_key)

    revision = _requirement_current_revision(conn, requirement["id"])
    captured_revision_id = revision["id"] if revision is not None else None
    captured_digest = revision["content_digest"] if revision is not None else ""

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_feature_requirement_link
               WHERE system_id = ? AND feature_id = ? AND requirement_key = ?
                 AND superseded_by_id IS NULL""",
            (system_id, feature["id"], requirement_key),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_feature_requirement_link
                   (system_id, feature_id, requirement_key, requirement_id,
                    captured_requirement_revision_id, captured_digest, note, decision_method,
                    created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, feature["id"], requirement_key, requirement["id"], captured_revision_id,
                captured_digest, note, decision_method, created_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE product_feature_requirement_link SET superseded_by_id = ? WHERE id = ?",
                (new_id, prior["id"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_feature_requirement_link WHERE id = ?", (new_id,)).fetchone()
    return _requirement_link_out_dict(conn, system_id, dict(row))


# --- §7.2. Capability link (explicit link to understanding_capability_entity) ---


def _capability_link_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    """Reuses `node_design._resolve_capability` verbatim (§7.2's explicit
    instruction) -- never re-implemented. `capability_entity_id` is read
    off `target_ref` rather than the row's own (nullable, `ON DELETE SET
    NULL`) FK column, since `target_ref` is the row's NOT NULL
    identity-bearing reference and survives the FK going NULL (the same
    "key survives, row pointer is best-effort" split
    `product_objective_upstream_ref` documents)."""
    resolved = node_design._resolve_capability(conn, system_id, row["target_ref"])
    current_digest = (
        content_digest({"name": resolved.name, "state": resolved.state})
        if resolved.resolution == "resolved"
        else ""
    )
    return {
        "id": row["id"],
        "feature_id": row["feature_id"],
        "capability_entity_id": int(row["target_ref"]),
        "capability_name": resolved.name,
        "target_state": resolved.state if resolved.resolution == "resolved" else None,
        "target_resolution": resolved.resolution,
        "recheck_state": _recheck_state(row["captured_digest"], resolved.resolution, current_digest),
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def add_capability_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    feature_key: str,
    capability_entity_id: int,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one Feature<->Capability link (§7.2/§1.2). Feature and
    Capability stay separate entities -- this table is the ONLY connection
    between them, and a Feature is never inferred to "belong to" a
    Capability by name similarity (§7.2's explicit "自動 link しない" rule).
    The Capability MUST resolve at link time (404 otherwise)."""
    now = time.time() if now is None else now
    feature = _get_feature_row(conn, system_id, feature_key)
    if feature is None:
        raise NotFound(f"Feature {feature_key!r} not found")

    target_ref = str(capability_entity_id)
    resolved = node_design._resolve_capability(conn, system_id, target_ref)
    if resolved.resolution != "resolved":
        raise CapabilityNotFound(capability_entity_id)
    captured_digest = content_digest({"name": resolved.name, "state": resolved.state})

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_feature_capability_link
               WHERE system_id = ? AND feature_id = ? AND target_ref = ? AND superseded_by_id IS NULL""",
            (system_id, feature["id"], target_ref),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_feature_capability_link
                   (system_id, feature_id, capability_entity_id, target_ref, captured_digest, note,
                    decision_method, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, feature["id"], capability_entity_id, target_ref, captured_digest, note,
                decision_method, created_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE product_feature_capability_link SET superseded_by_id = ? WHERE id = ?",
                (new_id, prior["id"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_feature_capability_link WHERE id = ?", (new_id,)).fetchone()
    return _capability_link_out_dict(conn, system_id, dict(row))


# --- §7.2. Target link (9-kind downstream implementation target) ---------------
#
# `solution_design._resolve_target` already resolves 5 of the 9 kinds
# (evolution_node / component / probe_point / static_flow / runtime_flow),
# and is reused verbatim rather than reimplemented. The other 4 kinds have
# no existing resolver to reuse:
#
# * `solution_design` -- `solution_design._resolve_target` resolves what a
#   Solution Design's OWN option points AT, never the Solution Design
#   identity itself (there is no `target_kind="solution_design"` case in
#   its dispatch). This module's own `_resolve_solution_design_target`
#   below is a minimal read against the `solution_design` identity table
#   (+ its adopted-option state via `solution_design._adopted_option_key`,
#   the entity's own only finite lifecycle concept).
# * `experiment` / `replay_run` / `purpose_outcome_criterion` -- outside
#   both named resolvers' scope per the task brief; each gets a minimal
#   id-keyed read against its own canonical table, carrying that table's
#   own status/state column verbatim (#380's superset rule) rather than
#   inventing a new vocabulary.


def _resolve_solution_design_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    ref = (target_ref or "").strip()
    if not ref:
        return {"resolution": "unresolved", "name": None, "state": None, "digest": "", "row_id": None}
    row = conn.execute(
        "SELECT * FROM solution_design WHERE system_id = ? AND design_key = ?", (system_id, ref)
    ).fetchone()
    if row is None:
        return {"resolution": "unresolved", "name": None, "state": None, "digest": "", "row_id": None}
    design = dict(row)
    adopted_key = solution_design._adopted_option_key(conn, system_id, design["id"])
    state = "adopted" if adopted_key is not None else "not_adopted"
    digest = content_digest({"title": design["title"], "summary": design["summary"], "state": state})
    return {"resolution": "resolved", "name": design["title"], "state": state, "digest": digest, "row_id": design["id"]}


def _resolve_experiment_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    ref = (target_ref or "").strip()
    if not ref.isdigit():
        return {"resolution": "unresolved", "name": None, "state": None, "digest": "", "row_id": None}
    row = conn.execute(
        "SELECT id, status FROM experiments WHERE id = ? AND system_id = ?", (int(ref), system_id)
    ).fetchone()
    if row is None:
        return {"resolution": "unresolved", "name": None, "state": None, "digest": "", "row_id": None}
    digest = content_digest({"status": row["status"]})
    return {"resolution": "resolved", "name": None, "state": row["status"], "digest": digest, "row_id": row["id"]}


def _resolve_replay_run_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    ref = (target_ref or "").strip()
    if not ref.isdigit():
        return {"resolution": "unresolved", "name": None, "state": None, "digest": "", "row_id": None}
    row = conn.execute(
        "SELECT id, status FROM replay_runs WHERE id = ? AND system_id = ?", (int(ref), system_id)
    ).fetchone()
    if row is None:
        return {"resolution": "unresolved", "name": None, "state": None, "digest": "", "row_id": None}
    digest = content_digest({"status": row["status"]})
    return {"resolution": "resolved", "name": None, "state": row["status"], "digest": digest, "row_id": row["id"]}


def _resolve_purpose_outcome_criterion_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> Dict[str, Any]:
    ref = (target_ref or "").strip()
    if not ref.isdigit():
        return {"resolution": "unresolved", "name": None, "state": None, "digest": "", "row_id": None}
    row = conn.execute(
        "SELECT id, state FROM purpose_outcome_criterion WHERE id = ? AND system_id = ?",
        (int(ref), system_id),
    ).fetchone()
    if row is None:
        return {"resolution": "unresolved", "name": None, "state": None, "digest": "", "row_id": None}
    digest = content_digest({"state": row["state"]})
    return {"resolution": "resolved", "name": None, "state": row["state"], "digest": digest, "row_id": row["id"]}


#: `link_kind` -> the `solution_design._resolve_target`'s own `target_kind`
#: spelling, for the 5 kinds that resolver already covers. The remaining 4
#: kinds dispatch to this module's own minimal readers above.
_SOLUTION_DESIGN_RESOLVER_KINDS: Dict[str, str] = {
    "evolution_node": "evolution_node",
    "component": "component",
    "probe_point": "probe_point",
    "static_flow": "static_flow",
    "runtime_flow": "runtime_flow",
}


def _resolve_target_link(
    conn: sqlite3.Connection, system_id: int, link_kind: str, target_ref: str, captured_snapshot_id: Optional[int]
) -> Dict[str, Any]:
    if link_kind in _SOLUTION_DESIGN_RESOLVER_KINDS:
        try:
            resolved = solution_design._resolve_target(
                conn,
                system_id=system_id,
                target_kind=_SOLUTION_DESIGN_RESOLVER_KINDS[link_kind],
                target_ref=target_ref,
                captured_snapshot_id=captured_snapshot_id,
            )
        except sqlite3.OperationalError:
            return {"resolution": "unavailable", "name": None, "state": None, "digest": "", "row_id": None}
        return {
            "resolution": resolved.resolution,
            "name": resolved.name,
            "state": None,
            "digest": resolved.digest or "",
            "row_id": resolved.row_id,
        }
    if link_kind == "solution_design":
        return _resolve_solution_design_target(conn, system_id, target_ref)
    if link_kind == "experiment":
        return _resolve_experiment_target(conn, system_id, target_ref)
    if link_kind == "replay_run":
        return _resolve_replay_run_target(conn, system_id, target_ref)
    if link_kind == "purpose_outcome_criterion":
        return _resolve_purpose_outcome_criterion_target(conn, system_id, target_ref)
    raise LinkKindInvalid(link_kind)


def _target_link_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_target_link(
        conn, system_id, row["link_kind"], row["target_ref"], row["captured_snapshot_id"]
    )
    current_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
    return {
        "id": row["id"],
        "feature_id": row["feature_id"],
        "link_kind": row["link_kind"],
        "target_ref": row["target_ref"],
        "target_row_id": resolved["row_id"] if resolved["row_id"] is not None else row["target_row_id"],
        "target_state": resolved["state"],
        "target_resolution": resolved["resolution"],
        "recheck_state": _recheck_state(row["captured_digest"], resolved["resolution"], current_digest),
        "captured_digest": row["captured_digest"],
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def add_target_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    feature_key: str,
    link_kind: str,
    target_ref: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one Feature target link (§7.2's 9-kind table). Unlike the
    Requirement/Capability links above, a target link is permitted to point
    at something that does not currently resolve (the same permissive rule
    `solution_design.add_target_link` applies to its own target links) --
    `captured_digest` is simply empty in that case, and `target_resolution`
    reports it honestly at read time.

    `static_flow` needs a pinned snapshot to resolve against
    (`code_entrypoints` is snapshot-scoped); `ProductFeatureTargetLinkCreateRequest`
    carries no `captured_snapshot_id` field of its own (§10's endpoint
    table lists no such parameter), so this module auto-pins the System's
    latest READY snapshot at link time, the same convention
    `ux_design.create_artifact_reference` uses for its own snapshot-scoped
    verification.
    """
    now = time.time() if now is None else now
    _check_membership(link_kind, TARGET_LINK_KINDS, "link_kind")
    feature = _get_feature_row(conn, system_id, feature_key)
    if feature is None:
        raise NotFound(f"Feature {feature_key!r} not found")
    ref = (target_ref or "").strip()
    if not ref:
        raise ProductFeatureValidationError("target_ref is required")

    captured_snapshot_id: Optional[int] = None
    if link_kind == "static_flow":
        captured_snapshot_id = state_facts.get_latest_ready_snapshot_id(conn, system_id)

    resolved = _resolve_target_link(conn, system_id, link_kind, ref, captured_snapshot_id)
    captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_feature_target_link
               WHERE system_id = ? AND feature_id = ? AND link_kind = ? AND target_ref = ?
                 AND superseded_by_id IS NULL""",
            (system_id, feature["id"], link_kind, ref),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_feature_target_link
                   (system_id, feature_id, link_kind, target_ref, target_row_id, captured_digest,
                    captured_snapshot_id, note, decision_method, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, feature["id"], link_kind, ref, resolved["row_id"], captured_digest,
                captured_snapshot_id, note, decision_method, created_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE product_feature_target_link SET superseded_by_id = ? WHERE id = ?",
                (new_id, prior["id"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_feature_target_link WHERE id = ?", (new_id,)).fetchone()
    return _target_link_out_dict(conn, system_id, dict(row))


# --- §1.6 / §7.2. Draft link (bridge to snapshot-bound feature_drafts) ---------


def _resolve_draft_link(
    conn: sqlite3.Connection, system_id: int, feature_draft_ref: str, captured_snapshot_id: Optional[int]
) -> Dict[str, Any]:
    """Resolve against `feature_drafts` (§1.6). `product_feature` never
    replaces `feature_drafts` and never copies its text -- this reads it
    fresh, matched on the SAME `(system_id, feature_id, snapshot_id)` triple
    the link captured, and reports honestly rather than substituting a
    guess when that exact draft no longer exists (e.g. because the
    snapshot it belonged to was pruned; `feature_drafts.snapshot_id`
    cascades from `repository_snapshots`).

    When the pinned draft is gone the row id is reported as `None`, NOT as
    a stand-in. A draft id names one specific analysed row, so `0` is not a
    draft and the newest surviving row for the same `feature_id` is a
    DIFFERENT draft from a different snapshot -- either would hand a caller
    an id it could dereference into content the developer never linked
    (§0-8's substituted guess), leaving `target_resolution` to contradict
    it afterwards. The link stays readable through `feature_draft_ref`,
    which is the stable `feature_drafts.feature_id` text and survives the
    snapshot rebuild that took the row away (§1.6)."""
    row = conn.execute(
        """SELECT * FROM feature_drafts
               WHERE system_id = ? AND feature_id = ? AND snapshot_id = ?
               ORDER BY id DESC LIMIT 1""",
        (system_id, feature_draft_ref, captured_snapshot_id),
    ).fetchone()
    if row is not None:
        draft = dict(row)
        digest = content_digest(
            {"name": draft["name"], "summary": draft["summary"], "user_value": draft["user_value"]}
        )
        return {"resolution": "resolved", "row_id": draft["id"], "digest": digest}

    return {"resolution": "unresolved", "row_id": None, "digest": ""}


def _draft_link_out_dict(conn: sqlite3.Connection, system_id: int, row: Dict[str, Any]) -> Dict[str, Any]:
    resolved = _resolve_draft_link(conn, system_id, row["feature_draft_ref"], row["captured_snapshot_id"])
    return {
        "id": row["id"],
        "feature_id": row["feature_id"],
        "feature_draft_id": resolved["row_id"],
        "feature_draft_ref": row["feature_draft_ref"],
        "captured_snapshot_id": row["captured_snapshot_id"],
        "captured_digest": row["captured_digest"],
        "target_resolution": resolved["resolution"],
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def add_draft_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    feature_key: str,
    feature_draft_id: int,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Insert one Feature<->`feature_drafts` link (§1.6/§7.2).
    `feature_draft_id` is `feature_drafts.id` -- the row's own surrogate
    primary key, which is what the Dashboard's Feature Map listing already
    carries (`FeatureDraftOut.id`) -- used ONLY to locate the specific
    draft row at link time. What is actually stored is
    `feature_drafts.feature_id` (a run-scoped TEXT identifier, `feature_draft_ref`
    verbatim per the `product_feature_draft_link` DDL comment) plus the
    draft's `snapshot_id`, never the numeric row id and never the draft's
    own text (§1.6's "draft の本文を... コピーしない")."""
    now = time.time() if now is None else now
    feature = _get_feature_row(conn, system_id, feature_key)
    if feature is None:
        raise NotFound(f"Feature {feature_key!r} not found")
    draft = conn.execute(
        "SELECT * FROM feature_drafts WHERE id = ? AND system_id = ?", (feature_draft_id, system_id)
    ).fetchone()
    if draft is None:
        raise DraftNotFound(feature_draft_id)
    draft = dict(draft)
    feature_draft_ref = draft["feature_id"]
    captured_snapshot_id = draft["snapshot_id"]
    captured_digest = content_digest(
        {"name": draft["name"], "summary": draft["summary"], "user_value": draft["user_value"]}
    )

    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM product_feature_draft_link
               WHERE system_id = ? AND feature_id = ? AND feature_draft_ref = ? AND captured_snapshot_id = ?
                 AND superseded_by_id IS NULL""",
            (system_id, feature["id"], feature_draft_ref, captured_snapshot_id),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO product_feature_draft_link
                   (system_id, feature_id, feature_draft_ref, captured_snapshot_id, captured_digest,
                    note, decision_method, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                system_id, feature["id"], feature_draft_ref, captured_snapshot_id, captured_digest,
                note, decision_method, created_by, now,
            ),
        )
        new_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE product_feature_draft_link SET superseded_by_id = ? WHERE id = ?",
                (new_id, prior["id"]),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute("SELECT * FROM product_feature_draft_link WHERE id = ?", (new_id,)).fetchone()
    return _draft_link_out_dict(conn, system_id, dict(row))


# --- Feature read/list -----------------------------------------------------------


def _feature_summary_dict(
    feature: Dict[str, Any], revision_out: Optional[Dict[str, Any]], design_status: str, recheck_state: str
) -> Dict[str, Any]:
    return {
        "id": feature["id"],
        "system_id": feature["system_id"],
        "feature_key": feature["feature_key"],
        "current_revision_id": feature["current_revision_id"],
        "current_revision_number": revision_out["revision_number"] if revision_out else None,
        "title": revision_out["title"] if revision_out else "",
        "design_status": design_status,
        "recheck_state": recheck_state,
        "created_by": feature["created_by"],
        "created_at": feature["created_at"],
        "updated_at": feature["updated_at"],
    }


def _feature_overview(conn: sqlite3.Connection, system_id: int, feature: Dict[str, Any]) -> Dict[str, Any]:
    revision_out = (
        _feature_revision_out_dict(conn, feature["current_revision_id"])
        if feature["current_revision_id"] is not None
        else None
    )
    design_status, decision_row = derive_design_status(conn, system_id, feature["feature_key"])
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)
    return _feature_summary_dict(feature, revision_out, design_status, recheck_state)


def get_feature_detail(conn: sqlite3.Connection, system_id: int, feature_key: str) -> Dict[str, Any]:
    feature = _get_feature_row(conn, system_id, feature_key)
    if feature is None:
        raise NotFound(f"Feature {feature_key!r} not found")

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    revision_out: Optional[Dict[str, Any]] = None
    if feature["current_revision_id"] is not None:
        try:
            revision_out = _feature_revision_out_dict(conn, feature["current_revision_id"])
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    requirement_links: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_feature_requirement_link
               WHERE system_id = ? AND feature_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, feature["id"]),
        ).fetchall()
        requirement_links = [_requirement_link_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "requirement_links", exc)

    capability_links: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_feature_capability_link
               WHERE system_id = ? AND feature_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, feature["id"]),
        ).fetchall()
        capability_links = [_capability_link_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "capability_links", exc)

    target_links: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_feature_target_link
               WHERE system_id = ? AND feature_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, feature["id"]),
        ).fetchall()
        target_links = [_target_link_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "target_links", exc)

    draft_links: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_feature_draft_link
               WHERE system_id = ? AND feature_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
            (system_id, feature["id"]),
        ).fetchall()
        draft_links = [_draft_link_out_dict(conn, system_id, dict(r)) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "draft_links", exc)

    decisions: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            """SELECT * FROM product_feature_decision
               WHERE system_id = ? AND feature_id = ? ORDER BY id DESC""",
            (system_id, feature["id"]),
        ).fetchall()
        decisions = [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "decisions", exc)

    design_status, decision_row = derive_design_status(conn, system_id, feature_key)
    current_digest = revision_out["content_digest"] if revision_out else ""
    recheck_state = derive_recheck_state(current_digest, decision_row)

    summary = _feature_summary_dict(feature, revision_out, design_status, recheck_state)
    summary.update(
        {
            "current_revision": revision_out,
            "requirement_links": requirement_links,
            "capability_links": capability_links,
            "target_links": target_links,
            "draft_links": draft_links,
            "decisions": decisions,
            "degraded_sections": degraded_sections,
            "degraded_detail": degraded_detail,
        }
    )
    return summary


def list_features(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    features: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM product_feature WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        features = [_feature_overview(conn, system_id, dict(row)) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "features", exc)
    return {"features": features, "degraded_sections": degraded_sections, "degraded_detail": degraded_detail}
