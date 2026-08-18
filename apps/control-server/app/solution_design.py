"""Solution Design: Requirement -> Solution Design -> Flow / Node / Cell
(Epic #405 / UX Design Lineage, Issue #408).

The canonical contract is `docs/ux-design-lineage.md` §3 (read it in full
before touching this file). This module is deterministic end to end -- it
calls no reasoning model anywhere (Principle 6). What it owns:

1. **Solution Design + Option CRUD.** A Design is an identity row
   (`(system_id, design_key)`); Options are append-only candidate approaches
   (`solution_design_option`, corrections via `superseded_by_id`), each with
   a `content_digest` over its meaning-bearing fields (title/approach/
   tradeoffs/risks -- `option_order` excluded, the same reason Journey Step
   `step_order` sits outside its own digest: reordering changes presentation,
   not the option's own meaning).

2. **The many-to-many Requirement bridge** (`solution_design_requirement_link`).
   There is no FK from `solution_design` straight to one Requirement -- one
   design legitimately satisfies several Requirements and one Requirement is
   legitimately satisfied by several competing designs (#408 acceptance
   condition 1). The link captures the Requirement's revision id + digest AT
   LINK TIME; a later Requirement revision degrades `link_state` to `stale`
   without touching the link row itself.

3. **The exclusive-choice decision ledger** (`solution_design_decision`),
   kept separate from #407's `ux_design_decision` on purpose: confirming a
   Requirement is non-exclusive ("this statement is correct"), while
   adopting an Option is exclusive among N competing options ("this one, not
   the others"). `option_status` is DERIVED by folding this ledger per
   `option_key` -- never a stored column, the same "derived, never stored"
   discipline #337/#338/#349 use elsewhere. **Adoption exclusivity is
   enforced by REFUSAL, never by auto-`withdraw`**: while another option of
   the same design is `adopted`, a second `adopt` is refused with 409
   `solution_design_option_already_adopted` rather than superseding the
   incumbent -- fabricating a human's withdrawal decision under their name
   is exactly what §3.2 forbids. `record_option_decision` takes the write
   lock with `BEGIN IMMEDIATE` and re-checks the ledger INSIDE the
   transaction (mirroring `evolution_node.apply_transition`'s discipline),
   so two concurrent `adopt` requests for different options can never both
   win.

4. **The target-implementation bridge** (`solution_design_target_link`),
   shaped exactly like `evolution_node_link`: `target_kind` from the finite
   `SolutionTargetKind`, `target_ref` the stable string identity of the
   target's OWN canonical source, `target_row_id` a join shortcut that is
   NEVER trusted alone. Exactly one canonical source per kind (§3.3's
   table), resolved at READ time for every kind except `probe_point`, which
   is additionally gated at WRITE time by reusing
   `evolution_node._require_approved_probe_point` -- an unapproved Probe
   Point can never back a Solution Design target either.
   `static_flow` and `runtime_flow` are kept as two separate `target_kind`
   values on purpose: a snapshot-pinned entry-point path and a live
   SDK-assigned execution correlation id are different facts, and folding
   them into one displayed word is exactly the #366 defect this Epic is
   careful not to repeat. A `static_flow` link cannot be created without
   `captured_snapshot_id` (422 `flow_target_requires_snapshot`).

5. **Read-time `link_state` derivation** (§3.4's 6-row first-match table),
   applied to both link tables. `review_required` is not stored anywhere --
   it is the boolean consequence of `link_state != "current"` on
   `SolutionDesignTargetLinkOut`, never a fifth state value.

6. **`GET .../change-origins`** (§3.5): a deterministic mapping from each
   non-current link's own `(link_kind, target_kind, stale_reason,
   link_state)` to a `UxChangeOrigin`, never a guess or a score.

7. **`GET .../handoff`** (§3.7), mirroring `node_design.get_handoff`'s
   discipline: references only, never copied content; an unresolvable
   reference is reported BY NAME in `unresolved_references` and makes
   `handoff_state` `incomplete`, never silently dropped. Evaluation policy
   references are grouped by level (ADR-7's separation, kept structural).

This module reads `ux_requirement` / `ux_requirement_revision` /
`ux_requirement_acceptance_criterion` / `ux_requirement_step_link` /
`ux_journey` / `ux_journey_revision` / `ux_journey_step` /
`ux_design_artifact_reference` / `ux_design_decision` directly with plain
SQL rather than importing `app.ux_design` (Issue #407's concurrently-written
domain module) -- both because that module may not exist yet mid-write, and
because reading the same tables through two independent code paths is
exactly what the contract's up/downstream separation (§1) intends: this
layer never copies upstream/downstream content, it only resolves references
against each kind's own canonical source at read time.

Connection discipline (CLAUDE.md Implementation Constraints): every function
here takes an already-open `conn`. Nothing in this module performs a
subprocess call or an LLM round trip, so nothing needs to close the
connection first.

probe-agent:
  role: Solution Design domain service -- Options, the Requirement bridge, exclusive-choice adoption, target-implementation links, staleness, change-origin classification, and the read-only Phase-3-style handoff
  capability: ux-design-lineage
  element_type: core
  operation_kind: mixed
  consumers: [control-server-routes]
  state_effects: [database-read, database-write]
  probe_value: Verify a second `adopt` while one option is already adopted is refused rather than auto-withdrawing the incumbent, an `out_of_scope`-only design cannot receive a target link, `static_flow` and `runtime_flow` stay distinct facts with no permanent Flow ID ever minted, link staleness is reachable for every documented reason, and adoption never mutates any existing canonical table (Evolution Node maturity, Cell Improvement state, Component mode, patch/worktree/publish rows, Probe Plan/Pattern approval).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import evolution_node, node_design

__all__ = [
    "SOLUTION_DESIGN_OPTION_DECISIONS",
    "SOLUTION_DESIGN_OPTION_STATUSES",
    "SOLUTION_TARGET_KINDS",
    "SOLUTION_LINK_STATES",
    "SOLUTION_LINK_STALE_REASONS",
    "SOLUTION_HANDOFF_STATES",
    "UX_CHANGE_ORIGINS",
    "SolutionDesignError",
    "SolutionDesignNotFoundError",
    "SolutionDesignValidationError",
    "SolutionDesignConflictError",
    "content_digest",
    "create_design",
    "list_designs",
    "get_design_detail",
    "add_option",
    "list_options",
    "derive_option_status",
    "add_requirement_link",
    "list_requirement_links",
    "add_target_link",
    "list_target_links",
    "record_option_decision",
    "list_decisions",
    "get_change_origins",
    "get_handoff",
]


# ---------------------------------------------------------------------------
# Finite vocabularies (Principle 6). Mirrored from `app/models.py`'s
# `Literal` aliases -- see that file's "Solution Design" banner comment for
# why they are re-declared rather than imported (FastAPI's OpenAPI schema,
# and `test_interview_type_parity.py`'s drift guard).
# ---------------------------------------------------------------------------

SOLUTION_DESIGN_OPTION_DECISIONS: Tuple[str, ...] = ("adopt", "hold", "reject", "withdraw")
SOLUTION_DESIGN_OPTION_STATUSES: Tuple[str, ...] = (
    "draft", "adopted", "held", "rejected", "withdrawn",
)
SOLUTION_TARGET_KINDS: Tuple[str, ...] = (
    "capability", "static_flow", "runtime_flow", "evolution_node",
    "component", "cell_definition", "cell_binding", "probe_point",
)
SOLUTION_LINK_STATES: Tuple[str, ...] = ("current", "stale", "unresolved", "unavailable")
SOLUTION_LINK_STALE_REASONS: Tuple[str, ...] = (
    "requirement_changed", "design_changed", "target_changed",
    "snapshot_changed", "upstream_changed",
)
SOLUTION_HANDOFF_STATES: Tuple[str, ...] = ("complete", "incomplete", "unavailable")
UX_CHANGE_ORIGINS: Tuple[str, ...] = (
    "purpose", "capability", "journey", "requirement",
    "solution_design", "implementation_target", "snapshot",
)

_DECISION_TO_STATUS: Dict[str, str] = {
    "adopt": "adopted",
    "hold": "held",
    "reject": "rejected",
    "withdraw": "withdrawn",
}

_DESIGN_DECISION_TO_STATUS: Dict[str, str] = {
    "confirm": "confirmed",
    "reject": "rejected",
    "retire": "retired",
    "reinstate": "proposed",
}


class SolutionDesignError(ValueError):
    """Base class for every failure this module raises. Every message is
    prefixed `"<finite_code>: <Japanese message>"` so the route boundary can
    map it to a stable `detail={"code": ..., "message": ...}` without a
    second, driftable table of strings."""


class SolutionDesignNotFoundError(SolutionDesignError):
    """A referenced row does not exist, or belongs to another System. The
    two are deliberately indistinguishable -- telling them apart would let a
    caller probe another System's ids."""


class SolutionDesignValidationError(SolutionDesignError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class SolutionDesignConflictError(SolutionDesignError):
    """The row exists but is not in a state where this operation is legal."""


class _OptionAlreadyAdopted(Exception):
    """Internal control-flow signal so `record_option_decision` can roll
    back the write-locked transaction before raising the public
    `SolutionDesignConflictError` -- mirrors
    `evolution_node.apply_transition`'s `_MaturityChangedUnderRequest`."""

    def __init__(self, existing_option_key: str) -> None:
        super().__init__(existing_option_key)
        self.existing_option_key = existing_option_key


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str, code: str) -> None:
    if value not in vocabulary:
        raise SolutionDesignValidationError(
            f"{code}: {field_name} は次のいずれかである必要があります: "
            f"{', '.join(vocabulary)}(got {value!r})"
        )


def content_digest(payload: Mapping[str, Any]) -> str:
    """The one canonicalization this Epic uses (§2.6), reproduced here
    rather than imported: `json.dumps(..., sort_keys=True,
    ensure_ascii=False, separators=(",", ":"))` then sha256 hex. Only
    meaning-bearing fields belong in `payload` -- never `created_by` /
    `created_at` / ordering fields (§2.6's reasoning: re-confirmation is
    invited by a MEANING change, never by the mere existence of a record)."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_or_default(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


# ---------------------------------------------------------------------------
# Design / Option identity + CRUD
# ---------------------------------------------------------------------------


def _require_system(conn: sqlite3.Connection, system_id: int) -> None:
    row = conn.execute("SELECT id FROM systems WHERE id = ?", (system_id,)).fetchone()
    if row is None:
        raise SolutionDesignNotFoundError(f"system_not_found: System {system_id} が見つかりません。")


def _require_design_row(conn: sqlite3.Connection, system_id: int, solution_design_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM solution_design WHERE id = ? AND system_id = ?",
        (solution_design_id, system_id),
    ).fetchone()
    if row is None:
        raise SolutionDesignNotFoundError(
            f"solution_design_not_found: Solution Design {solution_design_id} が見つかりません。"
        )
    return row


def _find_design_by_key(conn: sqlite3.Connection, system_id: int, design_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM solution_design WHERE system_id = ? AND design_key = ?",
        (system_id, design_key),
    ).fetchone()
    if row is None:
        raise SolutionDesignNotFoundError(
            f"solution_design_not_found: design_key {design_key!r} の Solution Design が見つかりません。"
        )
    return row


def create_design(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    design_key: str,
    title: str = "",
    summary: str = "",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    key = (design_key or "").strip()
    if not key:
        raise SolutionDesignValidationError("solution_design_key_required: design_key を入力してください。")
    _require_system(conn, system_id)
    existing = conn.execute(
        "SELECT id FROM solution_design WHERE system_id = ? AND design_key = ?",
        (system_id, key),
    ).fetchone()
    if existing is not None:
        raise SolutionDesignConflictError(
            f"solution_design_key_conflict: この System には既に design_key {key!r} の "
            "Solution Design が存在します。"
        )
    now = time.time()
    cur = conn.execute(
        """INSERT INTO solution_design
               (system_id, design_key, title, summary, created_by, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (system_id, key, title, summary, created_by, now, now),
    )
    return _require_design_row(conn, system_id, cur.lastrowid)


# ---------------------------------------------------------------------------
# Option status derivation (folded from `solution_design_decision`)
# ---------------------------------------------------------------------------


def _current_decision_row(
    conn: sqlite3.Connection, system_id: int, solution_design_id: int, option_key: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM solution_design_decision
               WHERE system_id = ? AND solution_design_id = ? AND option_key = ?
                 AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
        (system_id, solution_design_id, option_key),
    ).fetchone()


def derive_option_status(
    conn: sqlite3.Connection, system_id: int, solution_design_id: int, option_key: str
) -> str:
    """DERIVED, never stored (§3.2): `draft` when no decision row exists for
    this `option_key`, else the latest non-superseded decision's own kind."""
    row = _current_decision_row(conn, system_id, solution_design_id, option_key)
    if row is None:
        return "draft"
    return _DECISION_TO_STATUS.get(row["decision"], "draft")


def _all_option_keys(conn: sqlite3.Connection, system_id: int, solution_design_id: int) -> List[str]:
    rows = conn.execute(
        """SELECT DISTINCT option_key FROM solution_design_option
               WHERE system_id = ? AND solution_design_id = ?""",
        (system_id, solution_design_id),
    ).fetchall()
    return [r["option_key"] for r in rows]


def _adopted_option_key(conn: sqlite3.Connection, system_id: int, solution_design_id: int) -> Optional[str]:
    for key in _all_option_keys(conn, system_id, solution_design_id):
        if derive_option_status(conn, system_id, solution_design_id, key) == "adopted":
            return key
    return None


# ---------------------------------------------------------------------------
# Options (append-only; a repeated `option_key` is a correction)
# ---------------------------------------------------------------------------


def _option_digest(title: str, approach: str, tradeoffs: str, risks: str) -> str:
    return content_digest(
        {"title": title, "approach": approach, "tradeoffs": tradeoffs, "risks": risks}
    )


def _current_option_row(
    conn: sqlite3.Connection, system_id: int, solution_design_id: int, option_key: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM solution_design_option
               WHERE system_id = ? AND solution_design_id = ? AND option_key = ?
                 AND superseded_by_id IS NULL""",
        (system_id, solution_design_id, option_key),
    ).fetchone()


def add_option(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    solution_design_id: int,
    option_key: str,
    option_order: int,
    title: str = "",
    approach: str = "",
    tradeoffs: str = "",
    risks: str = "",
    authored_by_kind: str = "developer",
    decision_method: str = "manual",
    intelligence_run_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Append a new Option, or -- when `option_key` already has a current
    row -- a CORRECTION of it (supersede then insert, §3.2's "append-only"
    rule, the same pattern `ux_journey_revision`/`ux_requirement_revision`
    use)."""
    key = (option_key or "").strip()
    if not key:
        raise SolutionDesignValidationError(
            "solution_design_option_key_required: option_key を入力してください。"
        )
    _check_membership(
        authored_by_kind, ("developer", "reasoning_model"), "authored_by_kind",
        "solution_design_invalid_authored_by_kind",
    )
    _check_membership(
        decision_method, ("manual", "reasoning_llm"), "decision_method",
        "solution_design_invalid_decision_method",
    )
    _require_design_row(conn, system_id, solution_design_id)
    digest = _option_digest(title, approach, tradeoffs, risks)
    now = time.time()
    conn.execute("BEGIN")
    try:
        previous = _current_option_row(conn, system_id, solution_design_id, key)
        cur = conn.execute(
            """INSERT INTO solution_design_option
                   (solution_design_id, system_id, option_key, option_order, title, approach,
                    tradeoffs, risks, content_digest, authored_by_kind, decision_method,
                    intelligence_run_id, created_by, created_at, superseded_by_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                solution_design_id, system_id, key, option_order, title, approach, tradeoffs,
                risks, digest, authored_by_kind, decision_method, intelligence_run_id,
                created_by, now,
            ),
        )
        option_id = cur.lastrowid
        if previous is not None:
            conn.execute(
                "UPDATE solution_design_option SET superseded_by_id = ? WHERE id = ?",
                (option_id, previous["id"]),
            )
        conn.execute(
            "UPDATE solution_design SET updated_at = ? WHERE id = ?", (now, solution_design_id)
        )
        row = conn.execute(
            "SELECT * FROM solution_design_option WHERE id = ?", (option_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def _option_out_dict(
    conn: sqlite3.Connection, system_id: int, solution_design_id: int, row: sqlite3.Row
) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "solution_design_id": row["solution_design_id"],
        "option_key": row["option_key"],
        "option_order": row["option_order"],
        "title": row["title"],
        "approach": row["approach"],
        "tradeoffs": row["tradeoffs"],
        "risks": row["risks"],
        "content_digest": row["content_digest"],
        "authored_by_kind": row["authored_by_kind"],
        "decision_method": row["decision_method"],
        "intelligence_run_id": row["intelligence_run_id"],
        "option_status": derive_option_status(conn, system_id, solution_design_id, row["option_key"]),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "revision_state": "current" if row["superseded_by_id"] is None else "superseded",
        "superseded_by_id": row["superseded_by_id"],
    }


def list_options(
    conn: sqlite3.Connection, system_id: int, solution_design_id: int, *, include_superseded: bool = False
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM solution_design_option WHERE system_id = ? AND solution_design_id = ?"
    if not include_superseded:
        query += " AND superseded_by_id IS NULL"
    query += " ORDER BY option_order, id"
    rows = conn.execute(query, (system_id, solution_design_id)).fetchall()
    return [_option_out_dict(conn, system_id, solution_design_id, r) for r in rows]


# ---------------------------------------------------------------------------
# Requirement resolution (read-only access to #407's tables)
# ---------------------------------------------------------------------------


def _require_requirement_by_key(conn: sqlite3.Connection, system_id: int, requirement_key: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM ux_requirement WHERE system_id = ? AND requirement_key = ?",
        (system_id, requirement_key),
    ).fetchone()
    if row is None:
        raise SolutionDesignNotFoundError(
            f"requirement_not_found: requirement_key {requirement_key!r} の Requirement が"
            "見つかりません。"
        )
    return row


def _requirement_current_revision(
    conn: sqlite3.Connection, system_id: int, requirement_id: int
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """SELECT r.* FROM ux_requirement u
               JOIN ux_requirement_revision r ON r.id = u.current_revision_id
               WHERE u.id = ? AND u.system_id = ?""",
        (requirement_id, system_id),
    ).fetchone()


# ---------------------------------------------------------------------------
# Requirement <-> Solution Design link (many-to-many)
# ---------------------------------------------------------------------------


def add_requirement_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    solution_design_id: int,
    requirement_key: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    _require_design_row(conn, system_id, solution_design_id)
    requirement = _require_requirement_by_key(conn, system_id, requirement_key)
    revision = _requirement_current_revision(conn, system_id, requirement["id"])
    captured_revision_id = revision["id"] if revision is not None else None
    captured_digest = revision["content_digest"] if revision is not None else ""
    now = time.time()
    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM solution_design_requirement_link
                   WHERE system_id = ? AND solution_design_id = ? AND requirement_id = ?
                     AND superseded_by_id IS NULL""",
            (system_id, solution_design_id, requirement["id"]),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO solution_design_requirement_link
                   (system_id, solution_design_id, requirement_id, captured_requirement_revision_id,
                    captured_digest, note, decision_method, created_by, created_at, superseded_by_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                system_id, solution_design_id, requirement["id"], captured_revision_id,
                captured_digest, note or "", decision_method, created_by, now,
            ),
        )
        link_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE solution_design_requirement_link SET superseded_by_id = ? WHERE id = ?",
                (link_id, prior["id"]),
            )
        conn.execute(
            "UPDATE solution_design SET updated_at = ? WHERE id = ?", (now, solution_design_id)
        )
        row = conn.execute(
            "SELECT * FROM solution_design_requirement_link WHERE id = ?", (link_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def _requirement_step_links_stale(conn: sqlite3.Connection, system_id: int, requirement_id: int) -> bool:
    """Whether ANY of this Requirement's own (#407) Journey Step links is
    currently stale or unresolved -- read directly against
    `ux_requirement_step_link` / `ux_journey` / `ux_journey_step`, resolved
    against the Journey's CURRENT revision (mirrors §2.7's read-time
    resolution rule without importing `app.ux_design`, see module
    docstring)."""
    rows = conn.execute(
        """SELECT * FROM ux_requirement_step_link
               WHERE system_id = ? AND requirement_id = ? AND superseded_by_id IS NULL""",
        (system_id, requirement_id),
    ).fetchall()
    for row in rows:
        journey = conn.execute(
            "SELECT current_revision_id FROM ux_journey WHERE id = ? AND system_id = ?",
            (row["journey_id"], system_id),
        ).fetchone()
        if journey is None or journey["current_revision_id"] is None:
            return True
        step = conn.execute(
            """SELECT content_digest FROM ux_journey_step
                   WHERE journey_revision_id = ? AND step_key = ?""",
            (journey["current_revision_id"], row["step_key"]),
        ).fetchone()
        if step is None:
            return True
        if (row["captured_step_digest"] or "") != (step["content_digest"] or ""):
            return True
    return False


def _requirement_link_state(
    conn: sqlite3.Connection, system_id: int, link_row: sqlite3.Row
) -> Tuple[str, Optional[str]]:
    """§3.4's 6-row first-match table, specialised to a
    `solution_design_requirement_link` whose canonical target IS the
    Requirement."""
    try:
        requirement = conn.execute(
            "SELECT * FROM ux_requirement WHERE id = ? AND system_id = ?",
            (link_row["requirement_id"], system_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return "unavailable", None
    if requirement is None:
        return "unresolved", None
    revision = _requirement_current_revision(conn, system_id, requirement["id"])
    if revision is None:
        return "unresolved", None
    if (link_row["captured_digest"] or "") != (revision["content_digest"] or ""):
        return "stale", "requirement_changed"
    if _requirement_step_links_stale(conn, system_id, requirement["id"]):
        return "stale", "upstream_changed"
    return "current", None


def _requirement_link_out_dict(conn: sqlite3.Connection, system_id: int, row: sqlite3.Row) -> Dict[str, Any]:
    state, reason = _requirement_link_state(conn, system_id, row)
    req = conn.execute(
        "SELECT requirement_key FROM ux_requirement WHERE id = ?", (row["requirement_id"],)
    ).fetchone()
    return {
        "id": row["id"],
        "solution_design_id": row["solution_design_id"],
        "requirement_id": row["requirement_id"],
        "requirement_key": req["requirement_key"] if req else None,
        "captured_requirement_revision_id": row["captured_requirement_revision_id"],
        "captured_digest": row["captured_digest"],
        "link_state": state,
        "stale_reason": reason,
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def list_requirement_links(
    conn: sqlite3.Connection, system_id: int, solution_design_id: int, *, include_superseded: bool = False
) -> List[Dict[str, Any]]:
    query = (
        "SELECT * FROM solution_design_requirement_link "
        "WHERE system_id = ? AND solution_design_id = ?"
    )
    if not include_superseded:
        query += " AND superseded_by_id IS NULL"
    query += " ORDER BY id DESC"
    rows = conn.execute(query, (system_id, solution_design_id)).fetchall()
    return [_requirement_link_out_dict(conn, system_id, r) for r in rows]


def _design_is_out_of_scope_only(conn: sqlite3.Connection, system_id: int, solution_design_id: int) -> bool:
    """True only when this design has at least one current Requirement link
    AND every one of them is `out_of_scope` (§3.3: "しか繋がっていない"). A
    design with zero Requirement links is not yet in this state -- nothing
    has been declared out of scope, which is a different fact from
    everything having been declared out of scope."""
    rows = conn.execute(
        """SELECT DISTINCT requirement_id FROM solution_design_requirement_link
               WHERE system_id = ? AND solution_design_id = ? AND superseded_by_id IS NULL""",
        (system_id, solution_design_id),
    ).fetchall()
    if not rows:
        return False
    for r in rows:
        req = conn.execute(
            "SELECT requirement_kind FROM ux_requirement WHERE id = ? AND system_id = ?",
            (r["requirement_id"], system_id),
        ).fetchone()
        if req is None or req["requirement_kind"] != "out_of_scope":
            return False
    return True


# ---------------------------------------------------------------------------
# Target resolution (§3.3's one-canonical-source-per-kind table)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedTarget:
    resolution: str  # "resolved" | "unresolved" | "unavailable"
    digest: Optional[str] = None
    name: Optional[str] = None
    row_id: Optional[int] = None


_UNRESOLVED = _ResolvedTarget(resolution="unresolved")
_UNAVAILABLE = _ResolvedTarget(resolution="unavailable")


def _resolve_capability_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> _ResolvedTarget:
    """Reuses `node_design._resolve_capability` (Issue #312's canonical
    Capability identity) rather than re-implementing it -- the module
    docstring's "read the same tables through this layer's own resolution"
    rule applies to #407's tables, not to Phase 2's already-correct
    Capability resolver."""
    resolved = node_design._resolve_capability(conn, system_id, target_ref)
    if resolved.resolution != "resolved":
        return _ResolvedTarget(resolution=resolved.resolution)
    digest = content_digest({"name": resolved.name}) if resolved.name is not None else None
    row_id = int(target_ref) if (target_ref or "").strip().isdigit() else None
    return _ResolvedTarget(resolution="resolved", digest=digest, name=resolved.name, row_id=row_id)


def _resolve_runtime_flow_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> _ResolvedTarget:
    """`trace_spans.flow_id` -- reuses `node_design._resolve_flow` verbatim.
    A runtime flow carries no versioned content, so `digest` stays `None`
    and step 4 ("target_changed") never fires for this kind -- only
    resolution can change."""
    resolved = node_design._resolve_flow(conn, system_id, target_ref)
    if resolved.resolution != "resolved":
        return _ResolvedTarget(resolution=resolved.resolution)
    return _ResolvedTarget(resolution="resolved")


def _resolve_static_flow_target(
    conn: sqlite3.Connection, system_id: int, target_ref: str, snapshot_id: Optional[int]
) -> _ResolvedTarget:
    if snapshot_id is None:
        return _UNAVAILABLE
    try:
        row = conn.execute(
            """SELECT * FROM code_entrypoints
                   WHERE system_id = ? AND snapshot_id = ? AND entrypoint_id = ?""",
            (system_id, snapshot_id, target_ref),
        ).fetchone()
    except sqlite3.OperationalError:
        return _UNAVAILABLE
    if row is None:
        return _UNRESOLVED
    payload = {
        "entrypoint_type": row["entrypoint_type"],
        "category": row["category"],
        "label": row["label"],
        "handler_path": row["handler_path"],
        "handler_qualified_name": row["handler_qualified_name"],
        "line_start": row["line_start"],
        "line_end": row["line_end"],
        "route_method": row["route_method"],
        "route_path": row["route_path"],
    }
    return _ResolvedTarget(
        resolution="resolved", digest=content_digest(payload), name=row["label"], row_id=row["id"]
    )


def _resolve_evolution_node_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> _ResolvedTarget:
    row = conn.execute(
        "SELECT id, display_name FROM evolution_node WHERE system_id = ? AND node_key = ?",
        (system_id, target_ref),
    ).fetchone()
    if row is None:
        return _UNRESOLVED
    return _ResolvedTarget(resolution="resolved", name=row["display_name"], row_id=row["id"])


def _resolve_component_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> _ResolvedTarget:
    row = conn.execute(
        "SELECT component_id FROM components WHERE system_id = ? AND component_id = ?",
        (system_id, target_ref),
    ).fetchone()
    if row is None:
        return _UNRESOLVED
    return _ResolvedTarget(resolution="resolved", name=row["component_id"])


def _resolve_cell_definition_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> _ResolvedTarget:
    row = conn.execute(
        "SELECT id, cell_id FROM cell_definitions WHERE system_id = ? AND cell_id = ?",
        (system_id, target_ref),
    ).fetchone()
    if row is None:
        return _UNRESOLVED
    return _ResolvedTarget(resolution="resolved", name=row["cell_id"], row_id=row["id"])


def _resolve_cell_binding_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> _ResolvedTarget:
    ref = (target_ref or "").strip()
    if not ref.isdigit():
        return _UNRESOLVED
    row = conn.execute(
        "SELECT id FROM cell_bindings WHERE system_id = ? AND id = ?", (system_id, int(ref))
    ).fetchone()
    if row is None:
        return _UNRESOLVED
    return _ResolvedTarget(resolution="resolved", row_id=row["id"])


def _resolve_probe_point_target(conn: sqlite3.Connection, system_id: int, target_ref: str) -> _ResolvedTarget:
    """READ-time resolution mirrors the WRITE-time gate (§3.3): only an
    `approved` Probe Point of this System counts as `resolved`. A point that
    was approved when the link was created and later un-approved reads as
    `unresolved`, the same "target no longer in its canonical source" fact
    every other kind reports."""
    ref = (target_ref or "").strip()
    if not ref.isdigit():
        return _UNRESOLVED
    row = conn.execute(
        "SELECT id, status FROM probe_points WHERE system_id = ? AND id = ?", (system_id, int(ref))
    ).fetchone()
    if row is None or row["status"] != "approved":
        return _UNRESOLVED
    return _ResolvedTarget(resolution="resolved", row_id=row["id"])


def _resolve_target(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    target_kind: str,
    target_ref: str,
    captured_snapshot_id: Optional[int] = None,
) -> _ResolvedTarget:
    try:
        if target_kind == "capability":
            return _resolve_capability_target(conn, system_id, target_ref)
        if target_kind == "runtime_flow":
            return _resolve_runtime_flow_target(conn, system_id, target_ref)
        if target_kind == "static_flow":
            return _resolve_static_flow_target(conn, system_id, target_ref, captured_snapshot_id)
        if target_kind == "evolution_node":
            return _resolve_evolution_node_target(conn, system_id, target_ref)
        if target_kind == "component":
            return _resolve_component_target(conn, system_id, target_ref)
        if target_kind == "cell_definition":
            return _resolve_cell_definition_target(conn, system_id, target_ref)
        if target_kind == "cell_binding":
            return _resolve_cell_binding_target(conn, system_id, target_ref)
        if target_kind == "probe_point":
            return _resolve_probe_point_target(conn, system_id, target_ref)
    except sqlite3.OperationalError:
        return _UNAVAILABLE
    # Unreachable while `SOLUTION_TARGET_KINDS` and this dispatch stay in
    # sync; reported honestly rather than guessed if they ever drift.
    return _UNAVAILABLE


# ---------------------------------------------------------------------------
# Target link CRUD + state
# ---------------------------------------------------------------------------


def add_target_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    solution_design_id: int,
    option_key: str,
    target_kind: str,
    target_ref: str,
    captured_snapshot_id: Optional[int] = None,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    _check_membership(
        target_kind, SOLUTION_TARGET_KINDS, "target_kind", "solution_design_invalid_target_kind"
    )
    _require_design_row(conn, system_id, solution_design_id)
    option = _current_option_row(conn, system_id, solution_design_id, option_key)
    if option is None:
        raise SolutionDesignNotFoundError(
            f"solution_design_option_not_found: option_key {option_key!r} の Option が"
            "見つかりません。"
        )
    ref = (target_ref or "").strip()
    if not ref:
        raise SolutionDesignValidationError(
            "solution_design_target_ref_required: target_ref を入力してください。"
        )
    if target_kind == "static_flow" and captured_snapshot_id is None:
        raise SolutionDesignValidationError(
            "flow_target_requires_snapshot: static_flow の link には captured_snapshot_id が"
            "必須です。"
        )
    if captured_snapshot_id is not None:
        snap = conn.execute(
            "SELECT id FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (captured_snapshot_id, system_id),
        ).fetchone()
        if snap is None:
            raise SolutionDesignNotFoundError(
                f"snapshot_not_found: Snapshot {captured_snapshot_id} が見つかりません。"
            )
    if _design_is_out_of_scope_only(conn, system_id, solution_design_id):
        raise SolutionDesignValidationError(
            "out_of_scope_requirement_not_implementable: この Solution Design に紐づく "
            "Requirement がすべて out_of_scope のため、実装対象を追加できません。"
        )
    if target_kind == "probe_point":
        evolution_node._require_approved_probe_point(
            conn, system_id=system_id, target_ref=ref, target_row_id=None
        )

    resolved = _resolve_target(
        conn, system_id=system_id, target_kind=target_kind, target_ref=ref,
        captured_snapshot_id=captured_snapshot_id,
    )
    captured_digest = resolved.digest or ""
    now = time.time()
    conn.execute("BEGIN")
    try:
        prior = conn.execute(
            """SELECT id FROM solution_design_target_link
                   WHERE system_id = ? AND option_id = ? AND target_kind = ? AND target_ref = ?
                     AND superseded_by_id IS NULL""",
            (system_id, option["id"], target_kind, ref),
        ).fetchone()
        cur = conn.execute(
            """INSERT INTO solution_design_target_link
                   (system_id, solution_design_id, option_id, target_kind, target_ref, target_row_id,
                    captured_digest, captured_snapshot_id, note, decision_method, created_by,
                    created_at, superseded_by_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                system_id, solution_design_id, option["id"], target_kind, ref, resolved.row_id,
                captured_digest, captured_snapshot_id, note or "", decision_method, created_by, now,
            ),
        )
        link_id = cur.lastrowid
        if prior is not None:
            conn.execute(
                "UPDATE solution_design_target_link SET superseded_by_id = ? WHERE id = ?",
                (link_id, prior["id"]),
            )
        conn.execute(
            "UPDATE solution_design SET updated_at = ? WHERE id = ?", (now, solution_design_id)
        )
        row = conn.execute(
            "SELECT * FROM solution_design_target_link WHERE id = ?", (link_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def _target_link_upstream_stale(conn: sqlite3.Connection, system_id: int, solution_design_id: int) -> bool:
    """Whether ANY current Requirement link of this Solution Design is
    non-`current` -- a target link's own upstream chain (§3.4 step 5)."""
    rows = conn.execute(
        """SELECT * FROM solution_design_requirement_link
               WHERE system_id = ? AND solution_design_id = ? AND superseded_by_id IS NULL""",
        (system_id, solution_design_id),
    ).fetchall()
    for row in rows:
        state, _ = _requirement_link_state(conn, system_id, row)
        if state != "current":
            return True
    return False


def _latest_ready_snapshot_id(conn: sqlite3.Connection, system_id: int) -> Optional[int]:
    row = conn.execute(
        """SELECT id FROM repository_snapshots
               WHERE system_id = ? AND status = 'ready'
               ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()
    return row["id"] if row is not None else None


def _target_link_state(
    conn: sqlite3.Connection, system_id: int, link_row: sqlite3.Row, option_row: Optional[sqlite3.Row]
) -> Tuple[str, Optional[str]]:
    """§3.4's 6-row first-match table, specialised to a
    `solution_design_target_link`."""
    resolved = _resolve_target(
        conn,
        system_id=system_id,
        target_kind=link_row["target_kind"],
        target_ref=link_row["target_ref"],
        captured_snapshot_id=link_row["captured_snapshot_id"],
    )
    if resolved.resolution == "unavailable":
        return "unavailable", None
    if resolved.resolution == "unresolved":
        return "unresolved", None
    if link_row["target_kind"] == "static_flow":
        ready_id = _latest_ready_snapshot_id(conn, system_id)
        if link_row["captured_snapshot_id"] != ready_id:
            return "stale", "snapshot_changed"
    if resolved.digest is not None and (link_row["captured_digest"] or "") != resolved.digest:
        return "stale", "target_changed"
    if option_row is not None and option_row["superseded_by_id"] is not None:
        return "stale", "design_changed"
    if _target_link_upstream_stale(conn, system_id, link_row["solution_design_id"]):
        return "stale", "upstream_changed"
    return "current", None


def _target_link_out_dict(conn: sqlite3.Connection, system_id: int, row: sqlite3.Row) -> Dict[str, Any]:
    option = conn.execute(
        "SELECT * FROM solution_design_option WHERE id = ?", (row["option_id"],)
    ).fetchone()
    state, reason = _target_link_state(conn, system_id, row, option)
    resolved = _resolve_target(
        conn, system_id=system_id, target_kind=row["target_kind"], target_ref=row["target_ref"],
        captured_snapshot_id=row["captured_snapshot_id"],
    )
    return {
        "id": row["id"],
        "solution_design_id": row["solution_design_id"],
        "option_id": row["option_id"],
        "option_key": option["option_key"] if option else None,
        "target_kind": row["target_kind"],
        "target_ref": row["target_ref"],
        "target_row_id": row["target_row_id"],
        "target_name": resolved.name,
        "captured_digest": row["captured_digest"],
        "captured_snapshot_id": row["captured_snapshot_id"],
        "link_state": state,
        "stale_reason": reason,
        "review_required": state != "current",
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def list_target_links(
    conn: sqlite3.Connection, system_id: int, solution_design_id: int, *, include_superseded: bool = False
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM solution_design_target_link WHERE system_id = ? AND solution_design_id = ?"
    if not include_superseded:
        query += " AND superseded_by_id IS NULL"
    query += " ORDER BY id DESC"
    rows = conn.execute(query, (system_id, solution_design_id)).fetchall()
    return [_target_link_out_dict(conn, system_id, r) for r in rows]


# ---------------------------------------------------------------------------
# Exclusive-choice decision ledger
# ---------------------------------------------------------------------------


def record_option_decision(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    solution_design_id: int,
    option_key: str,
    decision: str,
    rationale: str = "",
    decided_by: Optional[str] = None,
) -> sqlite3.Row:
    """§3.2/§3.6. `decision_method` is always the literal `'manual'` -- there
    is no path here that ever writes anything else (adoption is never
    automatic)."""
    _check_membership(
        decision, SOLUTION_DESIGN_OPTION_DECISIONS, "decision", "solution_design_invalid_decision"
    )
    _require_design_row(conn, system_id, solution_design_id)
    option = _current_option_row(conn, system_id, solution_design_id, option_key)
    if option is None:
        raise SolutionDesignNotFoundError(
            f"solution_design_option_not_found: option_key {option_key!r} の Option が"
            "見つかりません。"
        )

    now = time.time()
    # BEGIN IMMEDIATE takes the write lock before the exclusivity check
    # below, so two concurrent `adopt` requests for two different options of
    # the same design cannot both read "nobody is adopted yet" and both
    # commit (mirrors `evolution_node.apply_transition`'s discipline).
    conn.execute("BEGIN IMMEDIATE")
    try:
        if decision == "adopt":
            for key in _all_option_keys(conn, system_id, solution_design_id):
                if key == option_key:
                    continue
                if derive_option_status(conn, system_id, solution_design_id, key) == "adopted":
                    raise _OptionAlreadyAdopted(key)
        cur = conn.execute(
            """INSERT INTO solution_design_decision
                   (system_id, solution_design_id, option_id, option_key, decision, rationale,
                    captured_digest, decision_method, decided_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)""",
            (
                system_id, solution_design_id, option["id"], option_key, decision, rationale,
                option["content_digest"], decided_by, now,
            ),
        )
        conn.execute(
            "UPDATE solution_design SET updated_at = ? WHERE id = ?", (now, solution_design_id)
        )
        row = conn.execute(
            "SELECT * FROM solution_design_decision WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        conn.execute("COMMIT")
    except _OptionAlreadyAdopted as exc:
        conn.execute("ROLLBACK")
        raise SolutionDesignConflictError(
            "solution_design_option_already_adopted: この Solution Design では既に option_key "
            f"{exc.existing_option_key!r} が採用済みです。別案を採用する前に、まず明示的に "
            "withdraw してください。"
        ) from exc
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def _decision_out_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "solution_design_id": row["solution_design_id"],
        "option_id": row["option_id"],
        "option_key": row["option_key"],
        "decision": row["decision"],
        "rationale": row["rationale"],
        "captured_digest": row["captured_digest"],
        "decision_method": row["decision_method"],
        "decided_by": row["decided_by"],
        "superseded_by_id": row["superseded_by_id"],
        "created_at": row["created_at"],
    }


def list_decisions(conn: sqlite3.Connection, system_id: int, solution_design_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM solution_design_decision
               WHERE system_id = ? AND solution_design_id = ? ORDER BY id DESC""",
        (system_id, solution_design_id),
    ).fetchall()
    return [_decision_out_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Design summary / detail / list
# ---------------------------------------------------------------------------


def _design_out_dict(conn: sqlite3.Connection, system_id: int, row: sqlite3.Row) -> Dict[str, Any]:
    options = list_options(conn, system_id, row["id"])
    return {
        "id": row["id"],
        "system_id": row["system_id"],
        "design_key": row["design_key"],
        "title": row["title"],
        "summary": row["summary"],
        "adopted_option_key": _adopted_option_key(conn, system_id, row["id"]),
        "option_count": len(options),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_designs(conn: sqlite3.Connection, *, system_id: int) -> Dict[str, Any]:
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}
    designs: List[Dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM solution_design WHERE system_id = ? ORDER BY id DESC", (system_id,)
        ).fetchall()
        designs = [_design_out_dict(conn, system_id, r) for r in rows]
    except sqlite3.OperationalError as exc:
        degraded_sections.append("designs")
        degraded_detail["designs"] = str(exc)
    return {
        "system_id": system_id,
        "generated_at": time.time(),
        "designs": designs,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


def get_design_detail(conn: sqlite3.Connection, *, system_id: int, design_key: str) -> Dict[str, Any]:
    row = _find_design_by_key(conn, system_id, design_key)
    out = _design_out_dict(conn, system_id, row)
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    def _guarded(label: str, loader):
        try:
            return loader()
        except sqlite3.OperationalError as exc:
            degraded_sections.append(label)
            degraded_detail[label] = str(exc)
            return []

    options = _guarded("options", lambda: list_options(conn, system_id, row["id"]))
    requirement_links = _guarded(
        "requirement_links", lambda: list_requirement_links(conn, system_id, row["id"])
    )
    target_links = _guarded("target_links", lambda: list_target_links(conn, system_id, row["id"]))
    decisions = _guarded("decisions", lambda: list_decisions(conn, system_id, row["id"]))

    out.update(
        {
            "options": options,
            "requirement_links": requirement_links,
            "target_links": target_links,
            "decisions": decisions,
            "degraded_sections": degraded_sections,
            "degraded_detail": degraded_detail,
        }
    )
    return out


# ---------------------------------------------------------------------------
# §3.5 change-origins
# ---------------------------------------------------------------------------


def _classify_origin(link_kind: str, target_kind: Optional[str], stale_reason: Optional[str]) -> str:
    """A deterministic, TOTAL mapping (§3.5) -- every non-`current` link
    gets a definite origin, including `unresolved`/`unavailable` states
    where `stale_reason` is `None` (those still name an axis: the
    Requirement itself for a requirement_link, the target's own axis for a
    target_link)."""
    if link_kind == "requirement_link":
        if stale_reason == "upstream_changed":
            return "journey"
        return "requirement"
    # target_link
    if stale_reason == "snapshot_changed":
        return "snapshot"
    if stale_reason == "design_changed":
        return "solution_design"
    if stale_reason == "upstream_changed":
        return "requirement"
    return "capability" if target_kind == "capability" else "implementation_target"


def get_change_origins(conn: sqlite3.Connection, *, system_id: int, design_key: str) -> Dict[str, Any]:
    design = _find_design_by_key(conn, system_id, design_key)
    origins: List[Dict[str, Any]] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    try:
        for row in conn.execute(
            """SELECT * FROM solution_design_requirement_link
                   WHERE system_id = ? AND solution_design_id = ? AND superseded_by_id IS NULL""",
            (system_id, design["id"]),
        ).fetchall():
            state, reason = _requirement_link_state(conn, system_id, row)
            if state == "current":
                continue
            req = conn.execute(
                "SELECT requirement_key FROM ux_requirement WHERE id = ?", (row["requirement_id"],)
            ).fetchone()
            origins.append(
                {
                    "origin": _classify_origin("requirement_link", None, reason),
                    "link_id": row["id"],
                    "link_kind": "requirement_link",
                    "target_kind": None,
                    "target_ref": None,
                    "requirement_key": req["requirement_key"] if req else None,
                    "stale_reason": reason,
                    "detail": f"link_state={state}",
                }
            )
    except sqlite3.OperationalError as exc:
        degraded_sections.append("requirement_links")
        degraded_detail["requirement_links"] = str(exc)

    try:
        for row in conn.execute(
            """SELECT * FROM solution_design_target_link
                   WHERE system_id = ? AND solution_design_id = ? AND superseded_by_id IS NULL""",
            (system_id, design["id"]),
        ).fetchall():
            option = conn.execute(
                "SELECT * FROM solution_design_option WHERE id = ?", (row["option_id"],)
            ).fetchone()
            state, reason = _target_link_state(conn, system_id, row, option)
            if state == "current":
                continue
            origins.append(
                {
                    "origin": _classify_origin("target_link", row["target_kind"], reason),
                    "link_id": row["id"],
                    "link_kind": "target_link",
                    "target_kind": row["target_kind"],
                    "target_ref": row["target_ref"],
                    "requirement_key": None,
                    "stale_reason": reason,
                    "detail": f"link_state={state}",
                }
            )
    except sqlite3.OperationalError as exc:
        degraded_sections.append("target_links")
        degraded_detail["target_links"] = str(exc)

    return {
        "system_id": system_id,
        "solution_design_id": design["id"],
        "design_key": design["design_key"],
        "generated_at": time.time(),
        "origins": origins,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


# ---------------------------------------------------------------------------
# Requirement detail (for the handoff's `requirements` list only)
# ---------------------------------------------------------------------------


def _requirement_step_link_out_dict(conn: sqlite3.Connection, system_id: int, row: sqlite3.Row) -> Dict[str, Any]:
    journey = conn.execute(
        "SELECT journey_key, current_revision_id FROM ux_journey WHERE id = ?", (row["journey_id"],)
    ).fetchone()
    step_label: Optional[str] = None
    target_resolution = "unavailable"
    recheck_state = "not_captured"
    if journey is not None:
        step = None
        if journey["current_revision_id"] is not None:
            step = conn.execute(
                """SELECT content_digest, user_intent FROM ux_journey_step
                       WHERE journey_revision_id = ? AND step_key = ?""",
                (journey["current_revision_id"], row["step_key"]),
            ).fetchone()
        captured = row["captured_step_digest"] or ""
        if step is None:
            target_resolution = "unresolved"
            recheck_state = "not_captured" if not captured else "stale"
        else:
            target_resolution = "resolved"
            step_label = step["user_intent"]
            if not captured:
                recheck_state = "not_captured"
            elif captured == (step["content_digest"] or ""):
                recheck_state = "current"
            else:
                recheck_state = "stale"
    return {
        "id": row["id"],
        "requirement_id": row["requirement_id"],
        "journey_id": row["journey_id"],
        "journey_key": journey["journey_key"] if journey else None,
        "step_key": row["step_key"],
        "step_label": step_label,
        "captured_journey_revision_id": row["captured_journey_revision_id"],
        "captured_step_digest": row["captured_step_digest"] or "",
        "target_resolution": target_resolution,
        "recheck_state": recheck_state,
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def _requirement_design_status(conn: sqlite3.Connection, system_id: int, requirement_key: str) -> str:
    row = conn.execute(
        """SELECT decision FROM ux_design_decision
               WHERE system_id = ? AND subject_kind = 'requirement' AND subject_key = ?
                 AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
        (system_id, requirement_key),
    ).fetchone()
    if row is None:
        return "proposed"
    return _DESIGN_DECISION_TO_STATUS.get(row["decision"], "proposed")


def _requirement_recheck_state(
    conn: sqlite3.Connection, system_id: int, requirement_key: str, current_digest: str
) -> str:
    row = conn.execute(
        """SELECT captured_digest FROM ux_design_decision
               WHERE system_id = ? AND subject_kind = 'requirement' AND subject_key = ?
                 AND decision = 'confirm' AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
        (system_id, requirement_key),
    ).fetchone()
    if row is None:
        return "current"
    return "current" if (row["captured_digest"] or "") == (current_digest or "") else "stale"


def _requirement_detail_dict(conn: sqlite3.Connection, system_id: int, requirement_id: int) -> Optional[Dict[str, Any]]:
    requirement = conn.execute(
        "SELECT * FROM ux_requirement WHERE id = ? AND system_id = ?", (requirement_id, system_id)
    ).fetchone()
    if requirement is None:
        return None
    revision = _requirement_current_revision(conn, system_id, requirement_id)
    current_revision_dict: Optional[Dict[str, Any]] = None
    current_digest = ""
    if revision is not None:
        criteria_rows = conn.execute(
            """SELECT * FROM ux_requirement_acceptance_criterion
                   WHERE requirement_revision_id = ? ORDER BY criterion_order""",
            (revision["id"],),
        ).fetchall()
        current_revision_dict = {
            "id": revision["id"],
            "requirement_id": revision["requirement_id"],
            "revision_number": revision["revision_number"],
            "requirement_kind": requirement["requirement_kind"],
            "statement": revision["statement"],
            "rationale": revision["rationale"],
            "constraint_text": revision["constraint_text"],
            "out_of_scope_note": revision["out_of_scope_note"],
            "content_digest": revision["content_digest"],
            "authored_by_kind": revision["authored_by_kind"],
            "decision_method": revision["decision_method"],
            "intelligence_run_id": revision["intelligence_run_id"],
            "change_note": revision["change_note"],
            "created_by": revision["created_by"],
            "created_at": revision["created_at"],
            "revision_state": "current" if revision["superseded_by_id"] is None else "superseded",
            "superseded_by_id": revision["superseded_by_id"],
            "acceptance_criteria": [
                {
                    "id": c["id"],
                    "criterion_key": c["criterion_key"],
                    "criterion_order": c["criterion_order"],
                    "statement": c["statement"],
                    "verification_method": c["verification_method"],
                    "verification_note": c["verification_note"],
                    "content_digest": c["content_digest"],
                }
                for c in criteria_rows
            ],
        }
        current_digest = revision["content_digest"]

    step_link_rows = conn.execute(
        """SELECT * FROM ux_requirement_step_link
               WHERE system_id = ? AND requirement_id = ? AND superseded_by_id IS NULL
               ORDER BY id DESC""",
        (system_id, requirement_id),
    ).fetchall()
    artifact_rows = conn.execute(
        """SELECT * FROM ux_design_artifact_reference
               WHERE system_id = ? AND subject_kind = 'requirement' AND subject_key = ?
                 AND superseded_by_id IS NULL
               ORDER BY id DESC""",
        (system_id, requirement["requirement_key"]),
    ).fetchall()
    decision_rows = conn.execute(
        """SELECT * FROM ux_design_decision
               WHERE system_id = ? AND subject_kind = 'requirement' AND subject_key = ?
               ORDER BY id DESC""",
        (system_id, requirement["requirement_key"]),
    ).fetchall()

    return {
        "id": requirement["id"],
        "system_id": requirement["system_id"],
        "requirement_key": requirement["requirement_key"],
        "requirement_kind": requirement["requirement_kind"],
        "current_revision_id": requirement["current_revision_id"],
        "current_revision_number": revision["revision_number"] if revision is not None else None,
        "statement": revision["statement"] if revision is not None else "",
        "design_status": _requirement_design_status(conn, system_id, requirement["requirement_key"]),
        "recheck_state": _requirement_recheck_state(
            conn, system_id, requirement["requirement_key"], current_digest
        ),
        "created_by": requirement["created_by"],
        "created_at": requirement["created_at"],
        "updated_at": requirement["updated_at"],
        "current_revision": current_revision_dict,
        "step_links": [_requirement_step_link_out_dict(conn, system_id, r) for r in step_link_rows],
        "artifact_references": [
            {
                "id": a["id"],
                "subject_kind": a["subject_kind"],
                "subject_key": a["subject_key"],
                "artifact_kind": a["artifact_kind"],
                "title": a["title"],
                "uri": a["uri"],
                "media_type": a["media_type"],
                "content_hash": a["content_hash"],
                "hash_algorithm": a["hash_algorithm"],
                "byte_size": a["byte_size"],
                "verification_state": a["verification_state"],
                "verified_snapshot_id": a["verified_snapshot_id"],
                "verified_commit_sha": a["verified_commit_sha"],
                "verified_at": a["verified_at"],
                "decision_method": a["decision_method"],
                "created_by": a["created_by"],
                "created_at": a["created_at"],
                "superseded_by_id": a["superseded_by_id"],
            }
            for a in artifact_rows
        ],
        "decisions": [
            {
                "id": d["id"],
                "subject_kind": d["subject_kind"],
                "subject_key": d["subject_key"],
                "subject_row_id": d["subject_row_id"],
                "decision": d["decision"],
                "rationale": d["rationale"],
                "captured_digest": d["captured_digest"],
                "captured_revision_id": d["captured_revision_id"],
                "decision_method": d["decision_method"],
                "decided_by": d["decided_by"],
                "superseded_by_id": d["superseded_by_id"],
                "created_at": d["created_at"],
            }
            for d in decision_rows
        ],
        "degraded_sections": [],
        "degraded_detail": {},
    }


# ---------------------------------------------------------------------------
# §3.7 read-only handoff
# ---------------------------------------------------------------------------


def get_handoff(conn: sqlite3.Connection, *, system_id: int, design_key: str) -> Dict[str, Any]:
    """Mirrors `node_design.get_handoff`'s discipline: references only,
    never copies; an unresolvable reference is named in
    `unresolved_references` and makes `handoff_state` `incomplete`, never
    silently dropped."""
    design = _find_design_by_key(conn, system_id, design_key)
    unresolved: List[Dict[str, str]] = []
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    adopted_key = _adopted_option_key(conn, system_id, design["id"])
    adopted_option_dict: Optional[Dict[str, Any]] = None
    target_links: List[Dict[str, Any]] = []
    if adopted_key is None:
        unresolved.append(
            {
                "kind": "adopted_option",
                "ref": design_key,
                "reason": "この Solution Design にはまだ採用された Option がありません。",
            }
        )
    else:
        option_row = _current_option_row(conn, system_id, design["id"], adopted_key)
        if option_row is None:
            unresolved.append(
                {
                    "kind": "adopted_option",
                    "ref": adopted_key,
                    "reason": "採用された Option の最新版を解決できませんでした。",
                }
            )
        else:
            adopted_option_dict = _option_out_dict(conn, system_id, design["id"], option_row)
            rows = conn.execute(
                """SELECT * FROM solution_design_target_link
                       WHERE system_id = ? AND option_id = ? AND superseded_by_id IS NULL
                       ORDER BY id DESC""",
                (system_id, option_row["id"]),
            ).fetchall()
            target_links = [_target_link_out_dict(conn, system_id, r) for r in rows]
            for tl in target_links:
                if tl["link_state"] in ("unresolved", "unavailable"):
                    unresolved.append(
                        {
                            "kind": f"target_link:{tl['target_kind']}",
                            "ref": tl["target_ref"],
                            "reason": f"link_state={tl['link_state']}",
                        }
                    )

    requirements: List[Dict[str, Any]] = []
    try:
        req_link_rows = conn.execute(
            """SELECT DISTINCT requirement_id FROM solution_design_requirement_link
                   WHERE system_id = ? AND solution_design_id = ? AND superseded_by_id IS NULL""",
            (system_id, design["id"]),
        ).fetchall()
        for r in req_link_rows:
            detail = _requirement_detail_dict(conn, system_id, r["requirement_id"])
            if detail is None:
                unresolved.append(
                    {
                        "kind": "requirement",
                        "ref": str(r["requirement_id"]),
                        "reason": "Requirement 行が見つかりませんでした。",
                    }
                )
            else:
                requirements.append(detail)
    except sqlite3.OperationalError as exc:
        degraded_sections.append("requirements")
        degraded_detail["requirements"] = str(exc)

    # Node decomposition + Probe Plan references: reference-only, resolved
    # against the ADOPTED option's own target links (never copied).
    node_decomposition_refs: List[Dict[str, Any]] = []
    probe_plan_refs: List[Dict[str, Any]] = []
    try:
        for tl in target_links:
            if tl["target_kind"] == "evolution_node" and tl["target_row_id"] is not None:
                cand_rows = conn.execute(
                    """SELECT id, proposal_id, candidate_key FROM node_decomposition_candidate
                           WHERE system_id = ? AND adopted_node_ids_json LIKE ?""",
                    (system_id, f'%{tl["target_row_id"]}%'),
                ).fetchall()
                for c in cand_rows:
                    node_decomposition_refs.append(
                        {
                            "candidate_id": c["id"],
                            "proposal_id": c["proposal_id"],
                            "candidate_key": c["candidate_key"],
                            "node_key": tl["target_ref"],
                        }
                    )
            if tl["target_kind"] == "probe_point":
                probe_plan_refs.append(
                    {"probe_point_id": tl["target_row_id"], "target_ref": tl["target_ref"]}
                )
    except sqlite3.OperationalError as exc:
        degraded_sections.append("node_decomposition_refs")
        degraded_detail["node_decomposition_refs"] = str(exc)

    # Evaluation policy references, grouped by level -- ADR-7's separation
    # kept structural rather than documented (§3.7).
    evaluation_policy_refs: Dict[str, List[Dict[str, Any]]] = {}
    try:
        evaluation_policy_refs = node_design.list_evaluation_policies(conn, system_id=system_id)
    except sqlite3.OperationalError as exc:
        degraded_sections.append("evaluation_policy_refs")
        degraded_detail["evaluation_policy_refs"] = str(exc)

    if degraded_sections and (adopted_option_dict is None and adopted_key is not None):
        # A core read failed before the adopted option could even be
        # resolved -- distinct from "resolved but incomplete" (§0 invariant
        # 8: an unreadable fact is never a fact's default value).
        handoff_state = "unavailable"
    else:
        handoff_state = "incomplete" if unresolved else "complete"

    return {
        "system_id": system_id,
        "solution_design_id": design["id"],
        "design_key": design["design_key"],
        "generated_at": time.time(),
        "handoff_state": handoff_state,
        "adopted_option": adopted_option_dict,
        "target_links": target_links,
        "requirements": requirements,
        "node_decomposition_refs": node_decomposition_refs,
        "probe_plan_refs": probe_plan_refs,
        "evaluation_policy_refs": evaluation_policy_refs,
        "unresolved_references": unresolved,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }
