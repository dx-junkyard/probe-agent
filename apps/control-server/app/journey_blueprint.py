"""Journey Service Blueprint projection (Issue #423, Epic #418).

`docs/stakeholder-value-network.md` §8 is the canonical contract. This
module is a **read-only, deterministic domain service -- no LLM call
anywhere** (Principle 6 / §0 invariant 9). Everything it returns is either a
direct read of a persisted row, a first-match classification over a small
finite vocabulary, or an append-only write of content the caller supplied
for the three §8.2 link tables this module owns
(`journey_step_stakeholder_link` / `journey_step_delivery_link` /
`journey_step_exchange_link`).

Steps of the Journey's **current** revision are the horizontal axis; §8.1's
nine lanes are the vertical axis. Two rules this module must never violate:

* **No new understanding model, no upstream/downstream content copies.**
  Stakeholder / Requirement / Value Exchange content is owned elsewhere
  (`stakeholder_network.py`, `ux_design.py`); every reference here is a
  `target_ref` + `captured_digest`, resolved fresh at READ time against
  exactly one canonical source per kind (`_resolve_delivery_target`), the
  same discipline `ux_design._resolve_upstream_target` /
  `stakeholder_network._resolve_target` use one layer over. Reimplementing
  the resolution logic locally (rather than importing another module's
  private helper) follows the SAME precedent those two modules already set
  for each other -- `node_design`, `ux_design`, and `stakeholder_network`
  each carry their own copy of the same per-kind dispatch.
* **Lane 4 (backstage) reaches Flow/Node only THROUGH #405's existing
  Requirement -> Solution Design -> target-link chain** (§5.2's "no second
  path"). `journey_step_delivery_link` never stores a Flow/Node reference
  directly; `_backstage_implementation_refs` resolves it for DISPLAY only,
  every read, from `ux_requirement` / `solution_design*` tables.

`BlueprintLaneState.not_applicable` is a deliberate, documented design
choice for this issue (§8.1 requires `unknown` and `not_applicable` to be
distinct and neither auto-filled, but does not specify a persistence
mechanism for the developer to assert "structurally does not apply"):
`journey_step_delivery_link.target_kind = 'not_applicable'` is the ONE
sentinel value this module defines for exactly that purpose, reachable only
by an explicit write to that table -- never inferred from the mere absence
of a link, which always reads `unknown`. This only applies to the four
delivery lanes (frontstage/backstage/support/external); the other five lanes
report only `present`/`unknown` (never invented). See
`docs/project-intelligence.md`'s Issue #423 notes for the full rationale.

probe-agent:
  role: Deterministic Journey Service Blueprint projection + delivery/stakeholder/exchange link domain service
  capability: journey-service-blueprint
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read, database-write]
  probe_value: Verify the blueprint never calls an LLM, never copies upstream/downstream content, keeps unknown/not_applicable/unavailable distinct, and reaches Flow/Node only through the existing Requirement -> Solution Design chain.
"""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, get_args

from . import stakeholder_network, ux_design
from .models import (
    BlueprintDeliveryTargetKind,
    BlueprintLaneKind,
    BlueprintLaneState,
    BlueprintDiffChangeKind,
    JourneyDeliveryKind,
    StakeholderRole,
)

__all__ = [
    "JourneyBlueprintError",
    "NotFound",
    "JourneyNotFound",
    "StepNotFound",
    "StakeholderNotFound",
    "ExchangeNotFound",
    "ValidationError",
    "LANE_KINDS",
    "LANE_STATES",
    "DELIVERY_KINDS",
    "DELIVERY_TARGET_KINDS",
    "DIFF_CHANGE_KINDS",
    "STAKEHOLDER_ROLES",
    "add_stakeholder_link",
    "add_delivery_link",
    "add_exchange_link",
    "build_blueprint",
    "diff_as_is_to_be",
]

LANE_KINDS: Tuple[str, ...] = get_args(BlueprintLaneKind)
LANE_STATES: Tuple[str, ...] = get_args(BlueprintLaneState)
DELIVERY_KINDS: Tuple[str, ...] = get_args(JourneyDeliveryKind)
DELIVERY_TARGET_KINDS: Tuple[str, ...] = get_args(BlueprintDeliveryTargetKind)
DIFF_CHANGE_KINDS: Tuple[str, ...] = get_args(BlueprintDiffChangeKind)
STAKEHOLDER_ROLES: Tuple[str, ...] = get_args(StakeholderRole)

#: Lanes fed only by `journey_step_delivery_link` (§8.1 rows 3-6). These are
#: the only lanes `not_applicable` is reachable on.
_DELIVERY_LANES: Dict[str, str] = {
    "frontstage": "frontstage",
    "backstage": "backstage",
    "support": "support",
    "external": "external",
}


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise ValidationError(f"{field_name} must be one of {vocabulary}; got {value!r}")


class JourneyBlueprintError(ValueError):
    """Base class for every failure this module raises."""


class ValidationError(JourneyBlueprintError):
    pass


class NotFound(JourneyBlueprintError):
    pass


class JourneyNotFound(NotFound):
    pass


class StepNotFound(NotFound):
    pass


class StakeholderNotFound(NotFound):
    pass


class ExchangeNotFound(NotFound):
    pass


def _degrade(
    degraded_sections: List[str],
    degraded_detail: Dict[str, str],
    section: str,
    exc: Exception,
) -> None:
    """Record one section as degraded without ever substituting a guessed
    value for what it failed to read (§0 invariant 5, the #380/#388/#405
    discipline reused here)."""
    if section not in degraded_sections:
        degraded_sections.append(section)
    degraded_detail[section] = f"{type(exc).__name__}: {exc}"


def _get_journey_row(conn: sqlite3.Connection, system_id: int, journey_key: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM ux_journey WHERE system_id = ? AND journey_key = ?",
        (system_id, journey_key),
    ).fetchone()
    if row is None:
        raise JourneyNotFound(f"Journey {journey_key!r} not found")
    return dict(row)


def _resolve_step(conn: sqlite3.Connection, journey: Dict[str, Any], step_key: str) -> Optional[Dict[str, Any]]:
    """Resolve `step_key` against the Journey's CURRENT revision only -- a
    Step from a superseded revision is unresolved, mirroring
    `ux_design._resolve_step_target`'s rule one layer over."""
    if journey["current_revision_id"] is None:
        return None
    row = conn.execute(
        "SELECT * FROM ux_journey_step WHERE journey_revision_id = ? AND step_key = ?",
        (journey["current_revision_id"], step_key),
    ).fetchone()
    return dict(row) if row is not None else None


def _ref_recheck_state(captured_digest: str, resolution: str, current_digest: str) -> str:
    """Same fail-closed 3-line rule `ux_design._ref_recheck_state` /
    `stakeholder_network._ref_recheck_state` use one layer over."""
    if not captured_digest:
        return "not_captured"
    if resolution != "resolved":
        return "stale"
    if captured_digest != current_digest:
        return "stale"
    return "current"


# --- Target resolution (one canonical source per kind, §5.1/§5.2) -------------


def _resolve_requirement(conn: sqlite3.Connection, system_id: int, requirement_key: str) -> Dict[str, Any]:
    try:
        detail = ux_design.get_requirement_detail(conn, system_id, requirement_key)
    except ux_design.NotFound:
        return {"resolution": "unresolved", "name": None, "digest": "", "design_status": None}
    except Exception:  # pragma: no cover - defensive
        return {"resolution": "unavailable", "name": None, "digest": "", "design_status": None}
    rev = detail.get("current_revision")
    digest = rev["content_digest"] if rev else ""
    return {
        "resolution": "resolved",
        "name": detail.get("statement") or None,
        "digest": digest,
        "design_status": detail.get("design_status"),
    }


def _resolve_stakeholder(conn: sqlite3.Connection, system_id: int, stakeholder_key: str) -> Dict[str, Any]:
    try:
        detail = stakeholder_network.get_stakeholder_detail(conn, system_id, stakeholder_key)
    except stakeholder_network.NotFound:
        return {"resolution": "unresolved", "name": None, "digest": ""}
    except Exception:  # pragma: no cover - defensive
        return {"resolution": "unavailable", "name": None, "digest": ""}
    rev = detail.get("current_revision")
    digest = rev["content_digest"] if rev else ""
    return {"resolution": "resolved", "name": detail.get("display_name") or None, "digest": digest}


def _resolve_exchange(conn: sqlite3.Connection, system_id: int, exchange_key: str) -> Dict[str, Any]:
    try:
        detail = stakeholder_network.get_exchange_detail(conn, system_id, exchange_key)
    except stakeholder_network.NotFound:
        return {"resolution": "unresolved", "name": None, "digest": "", "exchange_kind": None, "channel": None}
    except Exception:  # pragma: no cover - defensive
        return {"resolution": "unavailable", "name": None, "digest": "", "exchange_kind": None, "channel": None}
    rev = detail.get("current_revision")
    digest = rev["content_digest"] if rev else ""
    return {
        "resolution": "resolved",
        "name": detail.get("value_statement") or None,
        "digest": digest,
        "exchange_kind": detail.get("exchange_kind"),
        "channel": rev["channel"] if rev else None,
    }


def _resolve_delivery_target(conn: sqlite3.Connection, system_id: int, target_kind: str, target_ref: str) -> Dict[str, Any]:
    """Dispatch to the ONE canonical source per `target_kind`
    (`BlueprintDeliveryTargetKind`). `not_applicable` is this module's own
    sentinel (see the module docstring) and always resolves trivially."""
    if target_kind == "not_applicable":
        return {"resolution": "resolved", "name": None, "digest": ""}
    if target_kind == "ux_requirement":
        r = _resolve_requirement(conn, system_id, target_ref)
        return {"resolution": r["resolution"], "name": r["name"], "digest": r["digest"]}
    if target_kind == "stakeholder":
        return _resolve_stakeholder(conn, system_id, target_ref)
    if target_kind == "value_exchange":
        r = _resolve_exchange(conn, system_id, target_ref)
        return {"resolution": r["resolution"], "name": r["name"], "digest": r["digest"]}
    raise ValidationError(f"target_kind must be one of {DELIVERY_TARGET_KINDS}; got {target_kind!r}")


# --- §8.2 link writes ----------------------------------------------------------


def add_stakeholder_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    journey_key: str,
    step_key: str,
    stakeholder_key: str,
    role: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    _check_membership(role, STAKEHOLDER_ROLES, "role")
    journey = _get_journey_row(conn, system_id, journey_key)
    if _resolve_step(conn, journey, step_key) is None:
        raise StepNotFound(step_key)

    stakeholder_row = conn.execute(
        "SELECT id FROM stakeholder WHERE system_id = ? AND stakeholder_key = ?",
        (system_id, stakeholder_key),
    ).fetchone()
    if stakeholder_row is None:
        raise StakeholderNotFound(stakeholder_key)
    resolved = _resolve_stakeholder(conn, system_id, stakeholder_key)
    captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""

    cur = conn.execute(
        """INSERT INTO journey_step_stakeholder_link
               (system_id, journey_id, step_key, stakeholder_id, stakeholder_key, role,
                captured_digest, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, journey["id"], step_key, stakeholder_row["id"], stakeholder_key, role,
            captured_digest, note, decision_method, created_by, now,
        ),
    )
    row = conn.execute("SELECT * FROM journey_step_stakeholder_link WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _stakeholder_link_out(conn, system_id, journey_key, dict(row))


def add_delivery_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    journey_key: str,
    step_key: str,
    delivery_kind: str,
    target_kind: str,
    target_ref: str = "",
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    _check_membership(delivery_kind, DELIVERY_KINDS, "delivery_kind")
    _check_membership(target_kind, DELIVERY_TARGET_KINDS, "target_kind")
    journey = _get_journey_row(conn, system_id, journey_key)
    if _resolve_step(conn, journey, step_key) is None:
        raise StepNotFound(step_key)

    resolved = _resolve_delivery_target(conn, system_id, target_kind, target_ref)
    if target_kind != "not_applicable" and resolved["resolution"] != "resolved":
        raise NotFound(f"{target_kind} {target_ref!r} not found")
    captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""

    cur = conn.execute(
        """INSERT INTO journey_step_delivery_link
               (system_id, journey_id, step_key, delivery_kind, target_kind, target_ref,
                captured_digest, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, journey["id"], step_key, delivery_kind, target_kind, target_ref,
            captured_digest, note, decision_method, created_by, now,
        ),
    )
    row = conn.execute("SELECT * FROM journey_step_delivery_link WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _delivery_link_out(conn, system_id, journey_key, dict(row))


def add_exchange_link(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    journey_key: str,
    step_key: str,
    exchange_key: str,
    note: str = "",
    decision_method: str = "manual",
    created_by: Optional[str],
    now: Optional[float] = None,
) -> Dict[str, Any]:
    now = time.time() if now is None else now
    journey = _get_journey_row(conn, system_id, journey_key)
    if _resolve_step(conn, journey, step_key) is None:
        raise StepNotFound(step_key)

    exchange_row = conn.execute(
        "SELECT id FROM value_exchange WHERE system_id = ? AND exchange_key = ?",
        (system_id, exchange_key),
    ).fetchone()
    if exchange_row is None:
        raise ExchangeNotFound(exchange_key)
    resolved = _resolve_exchange(conn, system_id, exchange_key)
    captured_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""

    cur = conn.execute(
        """INSERT INTO journey_step_exchange_link
               (system_id, journey_id, step_key, exchange_id, exchange_key,
                captured_digest, note, decision_method, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, journey["id"], step_key, exchange_row["id"], exchange_key,
            captured_digest, note, decision_method, created_by, now,
        ),
    )
    row = conn.execute("SELECT * FROM journey_step_exchange_link WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _exchange_link_out(conn, system_id, journey_key, dict(row))


# --- Output dict builders -------------------------------------------------------


def _step_label(conn: sqlite3.Connection, journey: Dict[str, Any], step_key: str) -> Optional[str]:
    step = _resolve_step(conn, journey, step_key)
    return step["user_intent"] if step is not None else None


def _stakeholder_link_out(conn: sqlite3.Connection, system_id: int, journey_key: str, row: Dict[str, Any]) -> Dict[str, Any]:
    journey = _get_journey_row(conn, system_id, journey_key)
    resolved = _resolve_stakeholder(conn, system_id, row["stakeholder_key"])
    current_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
    return {
        "id": row["id"],
        "journey_id": row["journey_id"],
        "journey_key": journey_key,
        "step_key": row["step_key"],
        "step_label": _step_label(conn, journey, row["step_key"]),
        "stakeholder_key": row["stakeholder_key"],
        "stakeholder_name": resolved["name"],
        "role": row["role"],
        "target_resolution": resolved["resolution"],
        "recheck_state": _ref_recheck_state(row["captured_digest"], resolved["resolution"], current_digest),
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def _backstage_implementation_refs(conn: sqlite3.Connection, system_id: int, requirement_key: str) -> List[Dict[str, Any]]:
    """Lane 4 enrichment: Requirement -> Solution Design -> adopted option's
    target links, resolved fresh through #405's OWN tables (§5.2 -- no
    second path). Display only; never persisted on the delivery link row."""
    requirement = conn.execute(
        "SELECT id FROM ux_requirement WHERE system_id = ? AND requirement_key = ?",
        (system_id, requirement_key),
    ).fetchone()
    if requirement is None:
        return []
    link_rows = conn.execute(
        """SELECT DISTINCT solution_design_id FROM solution_design_requirement_link
           WHERE system_id = ? AND requirement_id = ? AND superseded_by_id IS NULL""",
        (system_id, requirement["id"]),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for link_row in link_rows:
        design = conn.execute(
            "SELECT * FROM solution_design WHERE id = ? AND system_id = ?",
            (link_row["solution_design_id"], system_id),
        ).fetchone()
        if design is None:
            continue
        adopted = conn.execute(
            """SELECT option_key FROM solution_design_decision
               WHERE system_id = ? AND solution_design_id = ? AND decision = 'adopt'
                 AND superseded_by_id IS NULL
               ORDER BY id DESC LIMIT 1""",
            (system_id, design["id"]),
        ).fetchone()
        if adopted is None:
            out.append(
                {"design_key": design["design_key"], "title": design["title"] or "",
                 "adopted_option_key": None, "target_kind": None, "target_ref": None}
            )
            continue
        option_row = conn.execute(
            "SELECT id FROM solution_design_option WHERE system_id = ? AND solution_design_id = ? AND option_key = ?",
            (system_id, design["id"], adopted["option_key"]),
        ).fetchone()
        target_rows = []
        if option_row is not None:
            target_rows = conn.execute(
                """SELECT target_kind, target_ref FROM solution_design_target_link
                   WHERE system_id = ? AND option_id = ? AND superseded_by_id IS NULL
                   ORDER BY id DESC""",
                (system_id, option_row["id"]),
            ).fetchall()
        if not target_rows:
            out.append(
                {"design_key": design["design_key"], "title": design["title"] or "",
                 "adopted_option_key": adopted["option_key"], "target_kind": None, "target_ref": None}
            )
        for t in target_rows:
            out.append(
                {
                    "design_key": design["design_key"], "title": design["title"] or "",
                    "adopted_option_key": adopted["option_key"],
                    "target_kind": t["target_kind"], "target_ref": t["target_ref"],
                }
            )
    return out


def _delivery_link_out(conn: sqlite3.Connection, system_id: int, journey_key: str, row: Dict[str, Any]) -> Dict[str, Any]:
    journey = _get_journey_row(conn, system_id, journey_key)
    resolved = _resolve_delivery_target(conn, system_id, row["target_kind"], row["target_ref"])
    current_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
    implementation_refs: List[Dict[str, Any]] = []
    if row["delivery_kind"] == "backstage" and row["target_kind"] == "ux_requirement" and resolved["resolution"] == "resolved":
        try:
            implementation_refs = _backstage_implementation_refs(conn, system_id, row["target_ref"])
        except Exception:  # pragma: no cover - defensive
            implementation_refs = []
    return {
        "id": row["id"],
        "journey_id": row["journey_id"],
        "journey_key": journey_key,
        "step_key": row["step_key"],
        "step_label": _step_label(conn, journey, row["step_key"]),
        "delivery_kind": row["delivery_kind"],
        "target_kind": row["target_kind"],
        "target_ref": row["target_ref"],
        "target_name": resolved["name"],
        "target_resolution": resolved["resolution"],
        "recheck_state": _ref_recheck_state(row["captured_digest"], resolved["resolution"], current_digest),
        "implementation_refs": implementation_refs,
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


def _exchange_link_out(conn: sqlite3.Connection, system_id: int, journey_key: str, row: Dict[str, Any]) -> Dict[str, Any]:
    journey = _get_journey_row(conn, system_id, journey_key)
    resolved = _resolve_exchange(conn, system_id, row["exchange_key"])
    current_digest = resolved["digest"] if resolved["resolution"] == "resolved" else ""
    return {
        "id": row["id"],
        "journey_id": row["journey_id"],
        "journey_key": journey_key,
        "step_key": row["step_key"],
        "step_label": _step_label(conn, journey, row["step_key"]),
        "exchange_key": row["exchange_key"],
        "exchange_kind": resolved["exchange_kind"],
        "channel": resolved["channel"],
        "target_resolution": resolved["resolution"],
        "recheck_state": _ref_recheck_state(row["captured_digest"], resolved["resolution"], current_digest),
        "note": row["note"],
        "decision_method": row["decision_method"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "superseded_by_id": row["superseded_by_id"],
    }


# --- Bulk section loaders (one guarded read per §8.2 table, #380's discipline) --


def _load_stakeholder_links(conn: sqlite3.Connection, system_id: int, journey_key: str, journey_id: int) -> Dict[str, List[Dict[str, Any]]]:
    rows = conn.execute(
        """SELECT * FROM journey_step_stakeholder_link
           WHERE system_id = ? AND journey_id = ? AND superseded_by_id IS NULL
           ORDER BY id""",
        (system_id, journey_id),
    ).fetchall()
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r["step_key"]].append(_stakeholder_link_out(conn, system_id, journey_key, dict(r)))
    return dict(out)


def _load_delivery_links(conn: sqlite3.Connection, system_id: int, journey_key: str, journey_id: int) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    rows = conn.execute(
        """SELECT * FROM journey_step_delivery_link
           WHERE system_id = ? AND journey_id = ? AND superseded_by_id IS NULL
           ORDER BY id""",
        (system_id, journey_id),
    ).fetchall()
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[(r["step_key"], r["delivery_kind"])].append(_delivery_link_out(conn, system_id, journey_key, dict(r)))
    return dict(out)


def _load_exchange_links(conn: sqlite3.Connection, system_id: int, journey_key: str, journey_id: int) -> Dict[str, List[Dict[str, Any]]]:
    rows = conn.execute(
        """SELECT * FROM journey_step_exchange_link
           WHERE system_id = ? AND journey_id = ? AND superseded_by_id IS NULL
           ORDER BY id""",
        (system_id, journey_id),
    ).fetchall()
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r["step_key"]].append(_exchange_link_out(conn, system_id, journey_key, dict(r)))
    return dict(out)


def _load_requirement_refs(conn: sqlite3.Connection, system_id: int, journey_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """Lane 7: read-only through #405's existing `ux_requirement_step_link`
    (§5.2 -- no second path is added here)."""
    rows = conn.execute(
        """SELECT * FROM ux_requirement_step_link
           WHERE system_id = ? AND journey_id = ? AND superseded_by_id IS NULL
           ORDER BY id""",
        (system_id, journey_id),
    ).fetchall()
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        requirement = conn.execute(
            "SELECT requirement_key FROM ux_requirement WHERE id = ?", (r["requirement_id"],)
        ).fetchone()
        if requirement is None:
            continue
        resolved = _resolve_requirement(conn, system_id, requirement["requirement_key"])
        out[r["step_key"]].append(
            {
                "requirement_key": requirement["requirement_key"],
                "statement": resolved["name"],
                "target_resolution": resolved["resolution"],
                "design_status": resolved["design_status"],
            }
        )
    return dict(out)


def _load_evidence_refs(
    conn: sqlite3.Connection, system_id: int, exchange_links_by_step: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, List[Dict[str, Any]]]:
    """Lane 8's "observed evidence refs" -- evidence already attached (§6) to
    a Value Exchange this Step delivers. Never a new evidence concept."""
    exchange_keys: List[str] = []
    for links in exchange_links_by_step.values():
        for link in links:
            if link["exchange_key"] not in exchange_keys:
                exchange_keys.append(link["exchange_key"])
    if not exchange_keys:
        return {}
    evidence_by_exchange: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    placeholders = ",".join("?" for _ in exchange_keys)
    rows = conn.execute(
        f"""SELECT * FROM stakeholder_evidence_ref
            WHERE system_id = ? AND subject_kind = 'value_exchange' AND subject_key IN ({placeholders})
              AND superseded_by_id IS NULL
            ORDER BY id DESC""",
        (system_id, *exchange_keys),
    ).fetchall()
    for r in rows:
        evidence_by_exchange[r["subject_key"]].append(
            {"exchange_key": r["subject_key"], "evidence_kind": r["evidence_kind"],
             "statement": r["statement"], "created_at": r["created_at"]}
        )
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for step_key, links in exchange_links_by_step.items():
        for link in links:
            out[step_key].extend(evidence_by_exchange.get(link["exchange_key"], []))
    return dict(out)


# --- Lane cell assembly ----------------------------------------------------------


def _delivery_lane_cell(
    lane_kind: str,
    free_text: str,
    links: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """§8.1 rows 3-6: first-match state -- a real (non-sentinel) link means
    `present`; a `not_applicable` sentinel (and nothing else) means exactly
    that; otherwise fall back to the Step's own free-text field (frontstage
    only carries one); absent all three, `unknown` (never guessed)."""
    real_links = [link for link in links if link["target_kind"] != "not_applicable"]
    sentinel_links = [link for link in links if link["target_kind"] == "not_applicable"]
    if real_links:
        state = "present"
    elif sentinel_links:
        state = "not_applicable"
    elif free_text.strip():
        state = "present"
    else:
        state = "unknown"
    return {
        "lane_kind": lane_kind,
        "state": state,
        "summary": free_text,
        "delivery_links": links,
    }


def _build_lane_cells(
    step: Dict[str, Any],
    stakeholder_links: List[Dict[str, Any]],
    delivery_links_by_kind: Dict[str, List[Dict[str, Any]]],
    exchange_links: List[Dict[str, Any]],
    requirement_refs: List[Dict[str, Any]],
    evidence_refs: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    lanes: Dict[str, Dict[str, Any]] = {}

    user_intent = step["user_intent"] or ""
    lanes["stakeholder_action"] = {
        "lane_kind": "stakeholder_action",
        "state": "present" if (user_intent.strip() or stakeholder_links) else "unknown",
        "summary": user_intent,
        "stakeholder_links": stakeholder_links,
    }

    channels = [l["channel"] for l in exchange_links if l.get("channel")]
    lanes["touchpoint"] = {
        "lane_kind": "touchpoint",
        "state": "present" if exchange_links else "unknown",
        "summary": "; ".join(channels),
        "exchange_links": exchange_links,
    }

    lanes["frontstage"] = _delivery_lane_cell(
        "frontstage", step["system_response"] or "", delivery_links_by_kind.get("frontstage", [])
    )
    lanes["backstage"] = _delivery_lane_cell("backstage", "", delivery_links_by_kind.get("backstage", []))
    lanes["support"] = _delivery_lane_cell("support", "", delivery_links_by_kind.get("support", []))
    lanes["external"] = _delivery_lane_cell("external", "", delivery_links_by_kind.get("external", []))

    lanes["requirement"] = {
        "lane_kind": "requirement",
        "state": "present" if requirement_refs else "unknown",
        "summary": "",
        "requirement_refs": requirement_refs,
    }

    evidence_expectation = step["evidence_expectation"] or ""
    evidence_source_kind = step["evidence_source_kind"] or "none"
    evidence_present = bool(evidence_expectation.strip()) or evidence_source_kind != "none" or bool(evidence_refs)
    lanes["evidence"] = {
        "lane_kind": "evidence",
        "state": "present" if evidence_present else "unknown",
        "summary": evidence_expectation,
        "evidence_refs": evidence_refs,
    }

    failure_mode = step["failure_mode"] or ""
    recovery_path = step["recovery_path"] or ""
    lanes["failure_recovery"] = {
        "lane_kind": "failure_recovery",
        "state": "present" if (failure_mode.strip() or recovery_path.strip()) else "unknown",
        "summary": " / ".join(x for x in (failure_mode, recovery_path) if x),
    }

    return lanes


def _mark_lane_unavailable(lanes: Dict[str, Dict[str, Any]], lane_kind: str) -> None:
    lanes[lane_kind] = {"lane_kind": lane_kind, "state": "unavailable", "summary": ""}


# --- §8. The blueprint projection ------------------------------------------------


def build_blueprint(conn: sqlite3.Connection, system_id: int, journey_key: str) -> Dict[str, Any]:
    """`GET /journey-blueprint`'s projection (§8). Read-only, deterministic,
    no LLM. Steps of the Journey's CURRENT revision are the horizontal axis;
    §8.1's nine lanes are the vertical axis. Each §8.2 table is its OWN
    guarded loader (#380's discipline): a failing table degrades every lane
    it feeds, for every Step, to `unavailable` -- never a guessed value."""
    journey = _get_journey_row(conn, system_id, journey_key)

    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    revision: Optional[Dict[str, Any]] = None
    if journey["current_revision_id"] is not None:
        try:
            row = conn.execute(
                "SELECT * FROM ux_journey_revision WHERE id = ?", (journey["current_revision_id"],)
            ).fetchone()
            revision = dict(row) if row is not None else None
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "revision", exc)

    steps: List[Dict[str, Any]] = []
    if revision is not None:
        try:
            step_rows = conn.execute(
                "SELECT * FROM ux_journey_step WHERE journey_revision_id = ? ORDER BY step_order, step_key",
                (revision["id"],),
            ).fetchall()
            steps = [dict(r) for r in step_rows]
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "steps", exc)

    stakeholder_links_by_step: Dict[str, List[Dict[str, Any]]] = {}
    delivery_links_by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    exchange_links_by_step: Dict[str, List[Dict[str, Any]]] = {}
    requirement_refs_by_step: Dict[str, List[Dict[str, Any]]] = {}
    evidence_refs_by_step: Dict[str, List[Dict[str, Any]]] = {}
    stakeholder_links_failed = False
    delivery_links_failed = False
    exchange_links_failed = False
    requirement_refs_failed = False
    evidence_refs_failed = False

    try:
        stakeholder_links_by_step = _load_stakeholder_links(conn, system_id, journey_key, journey["id"])
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "stakeholder_links", exc)
        stakeholder_links_failed = True

    try:
        delivery_links_by_key = _load_delivery_links(conn, system_id, journey_key, journey["id"])
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "delivery_links", exc)
        delivery_links_failed = True

    try:
        exchange_links_by_step = _load_exchange_links(conn, system_id, journey_key, journey["id"])
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "exchange_links", exc)
        exchange_links_failed = True

    try:
        requirement_refs_by_step = _load_requirement_refs(conn, system_id, journey["id"])
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "requirement_refs", exc)
        requirement_refs_failed = True

    try:
        evidence_refs_by_step = _load_evidence_refs(conn, system_id, exchange_links_by_step)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "evidence_refs", exc)
        evidence_refs_failed = True

    step_columns: List[Dict[str, Any]] = []
    for step in steps:
        step_key = step["step_key"]
        delivery_links_by_kind = {
            kind: delivery_links_by_key.get((step_key, kind), []) for kind in DELIVERY_KINDS
        }
        lanes = _build_lane_cells(
            step,
            stakeholder_links_by_step.get(step_key, []),
            delivery_links_by_kind,
            exchange_links_by_step.get(step_key, []),
            requirement_refs_by_step.get(step_key, []),
            evidence_refs_by_step.get(step_key, []),
        )
        if stakeholder_links_failed:
            _mark_lane_unavailable(lanes, "stakeholder_action")
        if delivery_links_failed:
            for kind in _DELIVERY_LANES:
                _mark_lane_unavailable(lanes, kind)
        if exchange_links_failed:
            _mark_lane_unavailable(lanes, "touchpoint")
        if requirement_refs_failed:
            _mark_lane_unavailable(lanes, "requirement")
        if evidence_refs_failed:
            _mark_lane_unavailable(lanes, "evidence")

        step_columns.append(
            {
                "step_key": step_key,
                "step_order": step["step_order"],
                "user_intent": step["user_intent"] or "",
                "system_response": step["system_response"] or "",
                "lanes": lanes,
            }
        )

    baseline_state = ux_design.journey_baseline_state(conn, system_id, journey)

    return {
        "journey_key": journey_key,
        "perspective": journey["perspective"],
        "baseline_state": baseline_state,
        "current_revision_number": revision["revision_number"] if revision else None,
        "steps": step_columns,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


# --- §8.3. as-is / to-be diff ----------------------------------------------------


def _steps_by_key(conn: sqlite3.Connection, revision_id: int) -> Dict[str, Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM ux_journey_step WHERE journey_revision_id = ?", (revision_id,)
    ).fetchall()
    return {r["step_key"]: dict(r) for r in rows}


def _diff_entries_with_reorder(
    from_map: Dict[str, Dict[str, Any]], to_map: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """§8.3's diff, matched by EXACT `step_key` equality only (Principle 6):
    `changed` compares the Step's own `content_digest`; `reordered` is an
    UNCHANGED digest at a different `step_order` -- a distinct fact
    `ux_design.diff_journey_revisions`'s diff (added/removed/changed/
    unchanged only) has no way to express."""
    keys = sorted(set(from_map) | set(to_map))
    entries: List[Dict[str, Any]] = []
    for key in keys:
        f = from_map.get(key)
        t = to_map.get(key)
        if f is None:
            kind = "added"
        elif t is None:
            kind = "removed"
        elif f["content_digest"] != t["content_digest"]:
            kind = "changed"
        elif f["step_order"] != t["step_order"]:
            kind = "reordered"
        else:
            kind = "unchanged"
        entries.append(
            {
                "step_key": key,
                "change_kind": kind,
                "from_step_order": f["step_order"] if f else None,
                "to_step_order": t["step_order"] if t else None,
                "from_content_digest": f["content_digest"] if f else None,
                "to_content_digest": t["content_digest"] if t else None,
                "from_user_intent": f["user_intent"] if f else None,
                "to_user_intent": t["user_intent"] if t else None,
            }
        )
    return entries


def _empty_diff(journey_key: str, *, diff_state: str) -> Dict[str, Any]:
    return {
        "journey_key": journey_key,
        "diff_state": diff_state,
        "from_revision_number": None,
        "to_revision_number": None,
        "steps": [],
        "degraded_sections": [],
        "degraded_detail": {},
    }


def diff_as_is_to_be(conn: sqlite3.Connection, system_id: int, journey_key: str) -> Dict[str, Any]:
    """§8.3: the pair is decided by the existing `ux_journey.perspective` +
    `baseline_journey_id` (reusing `ux_design.journey_baseline_state`'s
    first-match rule verbatim -- no second baseline-resolution rule is added
    here). `not_applicable` (never an empty diff) whenever the baseline is
    not `linked` -- the required distinction between "no changes" and
    "there is nothing to diff against"."""
    journey = _get_journey_row(conn, system_id, journey_key)
    baseline_state = ux_design.journey_baseline_state(conn, system_id, journey)
    if baseline_state != "linked":
        return _empty_diff(journey_key, diff_state="not_applicable")

    baseline = conn.execute(
        "SELECT * FROM ux_journey WHERE id = ?", (journey["baseline_journey_id"],)
    ).fetchone()
    baseline = dict(baseline)

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
        return _empty_diff(journey_key, diff_state="not_applicable")

    from_steps = _steps_by_key(conn, from_row["id"]) if from_row else {}
    to_steps = _steps_by_key(conn, to_row["id"]) if to_row else {}
    entries = _diff_entries_with_reorder(from_steps, to_steps)

    return {
        "journey_key": journey_key,
        "diff_state": "available",
        "from_revision_number": from_row["revision_number"] if from_row else None,
        "to_revision_number": to_row["revision_number"] if to_row else None,
        "steps": entries,
        "degraded_sections": [],
        "degraded_detail": {},
    }
