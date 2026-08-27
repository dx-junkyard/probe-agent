"""Stakeholder Value Network projection (Issue #422, Epic #418).

`docs/stakeholder-value-network.md` §7.1-§7.3 is the canonical contract for
`GET /stakeholder-value-network`. Read-only, deterministic, **no LLM call
anywhere in this module** (invariant 9); it writes nothing (#382's rule). It
composes `app/stakeholder_network.py`'s existing public functions
(`list_stakeholders`, `list_exchanges`, `list_refs`, `list_evidence_refs`,
`get_stakeholder_detail`) rather than re-deriving `design_status` /
`recheck_state` / `validity_state` -- those stay exactly what #420/#421
already compute (invariant 1: no fifth understanding model, no second
answer).

Three rules this module must never violate:

* **No coordinates, ever** (invariant 10). No `x` / `y` / `layout` /
  `position` field exists anywhere below, and none is computed. A saved
  `stakeholder_view_preference` (§12) is display settings only and is not
  read by this projection.
* **No score, no ranking, no centrality** (invariant 7). Notice COUNTS may be
  displayed by the Dashboard; nothing here computes an importance, a
  completeness percentage, or a weighted total.
* **Ordering is total and stable** (§7.1): nodes by
  `(display_name, stakeholder_key)`, edges by
  `(provider_stakeholder_key, receiver_stakeholder_key, exchange_kind,
  exchange_key)`, notices by `(code, subject_kind, subject_key)`. The same
  facts always render the same graph.

Each of the three sections (`nodes`, `edges`, `notices`) is its own guarded
loader (`_degrade`, #380's discipline): a failure records the section in
`degraded_sections` and drops it from the response -- never substituting
`[]`, `0`, or a guessed value. `notices` additionally depends on `nodes` and
`edges` having both succeeded (it reads their already-computed lists rather
than re-querying), so a failure of either degrades `notices` too -- the same
"a section that depends on an already-degraded section records its own
degradation" rule `get_exchange_lineage` uses for its `stakeholders` section.

Node-level `evidence_state` is not a direct read: `create_evidence_ref`'s
`subject_kind` never accepts `"stakeholder"` (only Need / Observation /
Exchange carry evidence, §6), so a Stakeholder's `evidence_state` is folded
from the evidence states of the Needs attributed to it
(`stakeholder_need_revision.stakeholder_key`). A Stakeholder with zero Needs
reads `"missing"` -- there is structurally nothing to have evidence for yet,
and `StakeholderEvidenceState` has no fifth `not_applicable` member to
express "no needs" separately. The fold across multiple Needs is a
deterministic first-match over four values, worst case first
(`unavailable` > `missing` > `stale` > `available`) -- documented here
because §6 only defines `evidence_state` for ONE subject, not for an
aggregate; this is this issue's own extension, kept deterministic and
finite per Principle 6.

probe-agent:
  role: Read-only Stakeholder Value Network graph projection (nodes / edges / structural notices)
  capability: stakeholder-value-network
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read]
  probe_value: Verify node/edge/notice ordering is always identical for the same facts, that no coordinate or score field ever appears in the response, that each of the three sections degrades independently without substituting a guessed value, and that this module never imports or calls an LLM client.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from . import stakeholder_network as sn

__all__ = ["build_value_network"]


# --- shared guarded-loader helper (the same shape every projection in this
# codebase uses -- `overview_projection._degrade` / `purpose_chain._degrade` /
# `stakeholder_network._degrade` one layer over) --------------------------


def _degrade(degraded_sections: List[str], degraded_detail: Dict[str, str], section: str, exc: Exception) -> None:
    if section not in degraded_sections:
        degraded_sections.append(section)
    degraded_detail[section] = f"{type(exc).__name__}: {exc}"


# --- Evidence state (§6, extended to a per-Stakeholder fold -- see module
# docstring) -----------------------------------------------------------------

#: Worst-first fold order for aggregating several subjects' evidence states
#: into one Stakeholder-level answer. `unavailable` outranks `missing`
#: because "we could not tell" must never be quietly overtaken by "there is
#: nothing" (§0 invariant 5's "unavailable is never counted as missing",
#: applied here to an aggregate instead of a single read).
_EVIDENCE_FOLD_ORDER: Tuple[str, ...] = ("unavailable", "missing", "stale")


def _current_digest(conn: sqlite3.Connection, system_id: int, ref_kind: str, target_ref: str) -> str:
    """The subject's CURRENT content digest, resolved fresh (never a copy,
    invariant 2). Empty when the subject does not currently resolve."""
    resolved = sn._resolve_target(conn, system_id, ref_kind, target_ref)
    return resolved["digest"] if resolved["resolution"] == "resolved" else ""


def _subject_evidence_state(
    conn: sqlite3.Connection, system_id: int, subject_kind: str, subject_key: str, current_digest: str
) -> str:
    """§6's per-subject fold: `available` (>=1 non-superseded row whose
    `captured_digest` still matches) | `missing` (no evidence rows) | `stale`
    (every resolving row's digest has moved) | `unavailable` (the evidence
    read itself failed)."""
    try:
        result = sn.list_evidence_refs(conn, system_id, subject_kind, subject_key)
    except Exception:  # pragma: no cover - defensive
        return "unavailable"
    if result["degraded_sections"]:
        return "unavailable"
    rows = result["evidence_refs"]
    if not rows:
        return "missing"
    if not current_digest:
        # The subject itself does not currently resolve to a digest we can
        # compare against -- treat every existing evidence row as stale
        # rather than guessing "available" against nothing (invariant 5).
        return "stale"
    stale_count = sum(1 for r in rows if (r.get("captured_digest") or "") != current_digest)
    if stale_count == len(rows):
        return "stale"
    return "available"


def _fold_evidence_states(states: List[str]) -> str:
    if not states:
        return "missing"
    for candidate in _EVIDENCE_FOLD_ORDER:
        if candidate in states:
            return candidate
    return "available"


def _stakeholder_evidence_state(
    conn: sqlite3.Connection, system_id: int, needs_for_stakeholder: List[Dict[str, Any]]
) -> str:
    if not needs_for_stakeholder:
        return "missing"
    states: List[str] = []
    for need in needs_for_stakeholder:
        need_key = need["need_key"]
        current_digest = _current_digest(conn, system_id, "stakeholder_need", need_key)
        states.append(_subject_evidence_state(conn, system_id, "stakeholder_need", need_key, current_digest))
    return _fold_evidence_states(states)


# --- §7.1 nodes ---------------------------------------------------------------


def _build_nodes(
    conn: sqlite3.Connection, system_id: int, needs_by_stakeholder: Dict[str, List[Dict[str, Any]]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns `(display_nodes, internal_nodes)`. `internal_nodes` carries
    the extra fields (full role-assignment list across every scope, raw
    stakeholder_ref rows) the notice pass needs but §7.1's shape does not
    expose -- kept separate so the public response never grows an
    undocumented field."""
    stakeholders_result = sn.list_stakeholders(conn, system_id)
    if stakeholders_result["degraded_sections"]:
        raise RuntimeError(f"stakeholders unavailable: {stakeholders_result['degraded_detail']}")

    display_nodes: List[Dict[str, Any]] = []
    internal_nodes: List[Dict[str, Any]] = []
    for summary in stakeholders_result["stakeholders"]:
        key = summary["stakeholder_key"]
        detail = sn.get_stakeholder_detail(conn, system_id, key)
        if detail.get("degraded_sections"):
            raise RuntimeError(f"stakeholder {key!r} detail unavailable: {detail.get('degraded_detail')}")

        roles_all = detail.get("roles", [])
        system_roles = sorted({r["role"] for r in roles_all if r["scope_kind"] == "system"})
        current_revision = detail.get("current_revision") or {}
        authored_by_kind = current_revision.get("authored_by_kind") or "developer"

        needs_for = needs_by_stakeholder.get(key, [])
        evidence_state = _stakeholder_evidence_state(conn, system_id, needs_for)

        refs_result = sn.list_refs(conn, system_id, source_kind="stakeholder", source_key=key)
        if refs_result["degraded_sections"]:
            raise RuntimeError(f"stakeholder {key!r} refs unavailable: {refs_result['degraded_detail']}")

        display_nodes.append(
            {
                "stakeholder_key": key,
                "display_name": summary["display_name"],
                "stakeholder_kind": summary["stakeholder_kind"],
                "roles": system_roles,
                "design_status": summary["design_status"],
                "recheck_state": summary["recheck_state"],
                "authored_by_kind": authored_by_kind,
                "evidence_state": evidence_state,
            }
        )
        internal_nodes.append(
            {
                "stakeholder_key": key,
                "design_status": summary["design_status"],
                "recheck_state": summary["recheck_state"],
                "roles_all": roles_all,
                "refs": refs_result["refs"],
                "has_need": bool(needs_for),
            }
        )

    display_nodes.sort(key=lambda n: (n["display_name"], n["stakeholder_key"]))
    return display_nodes, internal_nodes


# --- §7.1 edges -----------------------------------------------------------------


def _build_edges(
    conn: sqlite3.Connection, system_id: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns `(display_edges, internal_edges)` -- `internal_edges` keeps
    the raw `related_refs` rows (already part of the display shape here,
    unlike nodes) alongside `design_status`/`exchange_kind` for the notice
    pass."""
    exchanges_result = sn.list_exchanges(conn, system_id)
    if exchanges_result["degraded_sections"]:
        raise RuntimeError(f"exchanges unavailable: {exchanges_result['degraded_detail']}")

    display_edges: List[Dict[str, Any]] = []
    for summary in exchanges_result["exchanges"]:
        exchange_key = summary["exchange_key"]
        detail = sn.get_exchange_detail(conn, system_id, exchange_key)
        if detail.get("degraded_sections"):
            raise RuntimeError(f"exchange {exchange_key!r} detail unavailable: {detail.get('degraded_detail')}")
        current_revision = detail.get("current_revision") or {}

        current_digest = current_revision.get("content_digest", "") or ""
        evidence_state = _subject_evidence_state(conn, system_id, "value_exchange", exchange_key, current_digest)

        refs_result = sn.list_refs(conn, system_id, source_kind="value_exchange", source_key=exchange_key)
        if refs_result["degraded_sections"]:
            raise RuntimeError(f"exchange {exchange_key!r} refs unavailable: {refs_result['degraded_detail']}")
        related_refs = refs_result["refs"]

        display_edges.append(
            {
                "exchange_key": exchange_key,
                "provider_stakeholder_key": summary["provider_stakeholder_key"],
                "receiver_stakeholder_key": summary["receiver_stakeholder_key"],
                "exchange_kind": summary["exchange_kind"],
                "value_statement": summary["value_statement"],
                "consideration": {
                    "consideration_state": current_revision.get("consideration_state", "unknown"),
                    "consideration_kind": current_revision.get("consideration_kind"),
                    "consideration_statement": current_revision.get("consideration_statement", "") or "",
                },
                "channel": current_revision.get("channel", "") or "",
                "trigger": current_revision.get("trigger", "") or "",
                "cadence": current_revision.get("cadence", "unknown") or "unknown",
                "design_status": summary["design_status"],
                "recheck_state": summary["recheck_state"],
                "validity_state": summary["validity_state"],
                "evidence_state": evidence_state,
                "related_refs": related_refs,
            }
        )

    display_edges.sort(
        key=lambda e: (
            e["provider_stakeholder_key"],
            e["receiver_stakeholder_key"],
            e["exchange_kind"] or "",
            e["exchange_key"],
        )
    )
    return display_edges, display_edges


# --- §7.2 notices ---------------------------------------------------------------


def _has_ref_kind(related_refs: List[Dict[str, Any]], kinds: Tuple[str, ...]) -> bool:
    return any(r["ref_kind"] in kinds for r in related_refs)


def _any_stale_ref(related_refs: List[Dict[str, Any]]) -> bool:
    return any(r["recheck_state"] == "stale" for r in related_refs)


def _build_notices(
    internal_nodes: List[Dict[str, Any]], internal_edges: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    notices: List[Dict[str, Any]] = []

    def add(code: str, subject_kind: str, subject_key: str) -> None:
        notices.append({"code": code, "subject_kind": subject_kind, "subject_key": subject_key})

    non_rejected_edges = [e for e in internal_edges if e["design_status"] != "rejected"]

    # Per-Stakeholder structural checks.
    provides_to: Dict[str, Set[str]] = {}
    for e in non_rejected_edges:
        provides_to.setdefault(e["provider_stakeholder_key"], set()).add(e["receiver_stakeholder_key"])
    info_pairs = {
        (e["provider_stakeholder_key"], e["receiver_stakeholder_key"])
        for e in non_rejected_edges
        if e["exchange_kind"] == "information"
    }
    money_pairs = {
        (e["provider_stakeholder_key"], e["receiver_stakeholder_key"])
        for e in non_rejected_edges
        if e["exchange_kind"] == "money"
    }
    has_exchange: Set[str] = set()
    for e in non_rejected_edges:
        has_exchange.add(e["provider_stakeholder_key"])
        has_exchange.add(e["receiver_stakeholder_key"])

    for node in internal_nodes:
        key = node["stakeholder_key"]
        if key not in has_exchange:
            add("stakeholder_without_exchange", "stakeholder", key)
        if not node["roles_all"]:
            add("stakeholder_without_role", "stakeholder", key)
        if not node["has_need"]:
            add("stakeholder_without_need", "stakeholder", key)
        if node["design_status"] == "confirmed" and _stakeholder_node_evidence_missing(node):
            add("confirmed_without_evidence", "stakeholder", key)
        if node["recheck_state"] == "stale":
            add("stale_confirmation", "stakeholder", key)
        role_stale = any(r.get("recheck_state") == "stale" for r in node["roles_all"])
        if role_stale or _any_stale_ref(node["refs"]):
            add("stale_link", "stakeholder", key)

    for provider, receivers in provides_to.items():
        has_feedback = any((receiver, provider) in info_pairs for receiver in receivers)
        if not has_feedback:
            add("feedback_path_missing", "stakeholder", provider)

    # Per-Exchange structural checks.
    for e in internal_edges:
        key = e["exchange_key"]
        related_refs = e["related_refs"]
        if not _has_ref_kind(related_refs, ("stakeholder_need",)):
            add("exchange_without_need", "value_exchange", key)
        if not _has_ref_kind(related_refs, ("ux_journey", "ux_journey_step")):
            add("exchange_without_journey", "value_exchange", key)
        if not _has_ref_kind(related_refs, ("purpose_outcome_criterion",)):
            add("exchange_without_outcome", "value_exchange", key)
        if e["design_status"] == "confirmed" and e["evidence_state"] == "missing":
            add("confirmed_without_evidence", "value_exchange", key)
        if e["recheck_state"] == "stale":
            add("stale_confirmation", "value_exchange", key)
        if _any_stale_ref(related_refs):
            add("stale_link", "value_exchange", key)
        if e["design_status"] != "rejected" and e["exchange_kind"] in ("experience", "service"):
            pair = (e["provider_stakeholder_key"], e["receiver_stakeholder_key"])
            if pair not in money_pairs:
                add("payer_differs_from_beneficiary", "value_exchange", key)

    notices.sort(key=lambda n: (n["code"], n["subject_kind"], n["subject_key"]))
    return notices


def _stakeholder_node_evidence_missing(node: Dict[str, Any]) -> bool:
    """`confirmed_without_evidence` for a Stakeholder reads the SAME
    aggregate `evidence_state` the display node carries -- recomputing it
    here would risk the two disagreeing, so the notice pass is given it
    directly instead (see `_build_notices`'s caller)."""
    return node.get("evidence_state") == "missing"


# --- Top-level projection ----------------------------------------------------


def build_value_network(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    """§7.1's Stakeholder Value Network projection. Read-only, deterministic,
    no LLM, writes nothing. Every section below is its own guarded loader:
    a failure records the section in `degraded_sections` and drops it,
    never substituting a guessed value (#380's discipline)."""
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    needs_by_stakeholder: Dict[str, List[Dict[str, Any]]] = {}
    needs_ok = True
    try:
        needs_result = sn.list_needs(conn, system_id)
        if needs_result["degraded_sections"]:
            raise RuntimeError(f"needs unavailable: {needs_result['degraded_detail']}")
        for need in needs_result["needs"]:
            stakeholder_key = need.get("stakeholder_key") or ""
            if not stakeholder_key:
                continue
            needs_by_stakeholder.setdefault(stakeholder_key, []).append(need)
    except Exception as exc:  # pragma: no cover - defensive
        needs_ok = False
        _degrade(degraded_sections, degraded_detail, "nodes", exc)

    nodes: List[Dict[str, Any]] = []
    internal_nodes: List[Dict[str, Any]] = []
    nodes_ok = False
    if needs_ok:
        try:
            nodes, internal_nodes = _build_nodes(conn, system_id, needs_by_stakeholder)
            nodes_ok = True
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "nodes", exc)

    edges: List[Dict[str, Any]] = []
    internal_edges: List[Dict[str, Any]] = []
    edges_ok = False
    try:
        edges, internal_edges = _build_edges(conn, system_id)
        edges_ok = True
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "edges", exc)

    notices: List[Dict[str, Any]] = []
    if nodes_ok and edges_ok:
        try:
            notices = _build_notices(internal_nodes, internal_edges)
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, "notices", exc)
    else:
        _degrade(
            degraded_sections, degraded_detail, "notices",
            RuntimeError("notices unavailable: nodes and/or edges section did not load"),
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "notices": notices,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }
