"""Functional Lineage View + Gap / Impact Overlay (Issue #424, Epic #418).

`docs/stakeholder-value-network.md` §9 is the canonical contract for
`GET /functional-lineage`. Read-only, deterministic, **no LLM call anywhere
in this module** (invariant 9); it writes nothing (#382's rule). It COMPOSES
the three existing canonical modules rather than re-deriving anything they
already compute:

* `app/stakeholder_network.py` -- Stakeholder / Need / Value Exchange
  identity, `design_status` / `recheck_state` / evidence, and
  `get_exchange_lineage`'s existing Exchange -> Need/Purpose/Journey/
  Requirement/Solution-Design/Outcome/Evidence chain.
* `app/stakeholder_value_network.py` -- reused verbatim for its §7.2
  structural notices (`stakeholder_without_role`, `stakeholder_without_need`,
  `exchange_without_journey`, `exchange_without_outcome`,
  `confirmed_without_evidence`, `feedback_path_missing`, `stale_link`,
  `stale_confirmation`), which map onto six of this projection's own §9.2
  gap codes one-to-one (see `_VALUE_NETWORK_NOTICE_TO_GAP`). Recomputing
  those checks here would be a second, potentially disagreeing answer to a
  question `build_value_network` already answers (invariant 1).
* `app/journey_blueprint.py` -- not imported directly (this module reads the
  same `ux_requirement_step_link` table journey_blueprint's Lane 7 reads,
  because a Journey's Requirement coverage matters to the lineage view
  independent of any one Journey's Blueprint page).
* `app/ux_design.py` / `app/solution_design.py` / `app/node_design.py` --
  Requirement / Solution Design / Evolution Node canonical resolution.
  `solution_design.get_design_detail`'s `target_links` (already resolved
  against #405's own per-kind dispatch, `_resolve_target`) is this module's
  ONLY path to a Flow/Node/Component/Cell/Probe Point -- exactly §5.2's "no
  second path to Flow/Node".
* `app/product_objective.py` / `app/product_feature.py` (Epic #427, §7.3) --
  Product Objective / Milestone / Gap / Feature identity and their DERIVED
  `objective_state` / `design_status` / `achievement` / `assessability` /
  `lifecycle` / `priority_band`. This module reuses `derive_objective_state`
  / `derive_milestone_design_status` / `derive_milestone_achievement` /
  `derive_gap_lifecycle` / `derive_gap_priority_band` (indirectly, through
  each module's own `list_*`/`get_*_detail` output) rather than re-folding
  any decision ledger itself -- exactly the same "no second answer" rule
  applied to `build_value_network` above, one layer over.

Three rules this module must never violate (§9.3):

* **Links resolve by exact stable ref only** -- never similarity, keywords,
  or embeddings (Principle 6).
* **No weighted total score, no completeness percentage, no ranking.** Gap
  COUNTS may be shown by the Dashboard; nothing here computes an importance,
  a percentage, or a weighted total (invariant 7).
* **Impact traversal is downstream only, through explicit links.** Every
  edge this module records points from an UPSTREAM entity to the DOWNSTREAM
  entity it feeds (Stakeholder -> Need -> [Purpose ->] Value Exchange ->
  Journey/Step -> Requirement -> Solution Design -> Flow/Node -> Outcome);
  `trace_downstream_impact` walks that adjacency forward only and never
  backward (§9.3).

**`unavailable` is never counted as `missing`** (invariant 5 / §9.3): a
section this module could not read lands in `degraded_sections` /
`degraded_detail` (#380's discipline) and its own reference-level failures
report the `unavailable_reference` gap code, never the various "-without-"
codes that mean "read succeeded; the link is absent".

Every §9.2 gap code carries a FIXED severity from `LineageGapSeverity`,
never computed per instance (`_GAP_SEVERITY`) -- a per-instance severity
would be the importance score invariant 7 forbids.

A runtime trace never makes a UX Outcome `confirmed` (invariant 8): this
module only ever READS `purpose_outcome_criterion.state` (owned by
`purpose_verification.py`/#391) and never writes it, from any evidence kind.

probe-agent:
  role: Read-only Functional Lineage projection (Stakeholder -> Need -> Purpose/Capability -> Value Exchange -> Journey/Requirement -> Solution Design -> Flow/Node -> Outcome) with a finite structural Gap Overlay
  capability: functional-lineage
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: analysis
  state_effects: [database-read]
  probe_value: Verify every §9.2 gap code is reachable with its fixed severity, that unavailable is never reported as missing, that impact traversal never walks upstream, that no weighted score/percentage/ranking field ever appears, and that this module never imports or calls an LLM client.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from . import node_design, product_feature, product_objective, purpose_chain, solution_design
from . import stakeholder_network as sn, ux_design
from . import stakeholder_value_network as svn

__all__ = ["build_functional_lineage", "trace_downstream_impact"]


# --- Fixed gap severity (§9.2 -- never computed per instance, invariant 7) ---

#: Every §9.2 gap code with its FIXED `LineageGapSeverity`. A code's severity
#: is a property of what the code MEANS, never of the specific instance it
#: fired on -- computing severity per instance would be exactly the
#: importance score invariant 7 forbids.
_GAP_SEVERITY: Dict[str, str] = {
    "stakeholder_without_role": "attention",
    "stakeholder_without_need": "attention",
    "need_without_purpose": "attention",
    "need_without_exchange": "attention",
    "need_without_journey": "attention",
    "exchange_without_journey": "attention",
    "exchange_without_outcome": "attention",
    "journey_step_without_requirement": "attention",
    "requirement_without_acceptance_criterion": "attention",
    "requirement_without_design": "attention",
    "adopted_design_without_implementation_target": "blocking",
    "flow_without_node": "attention",
    "node_without_flow": "attention",
    "subject_without_evaluation_policy": "attention",
    "confirmed_without_evidence": "attention",
    "stale_upstream": "attention",
    "stale_link": "attention",
    "stale_evidence": "attention",
    "conflicting_dependency": "blocking",
    "rejected_dependency": "blocking",
    "feedback_path_missing": "informational",
    "unresolved_reference": "attention",
    "unavailable_reference": "informational",
    # --- Epic #427 §7.3: Product Objective / Milestone / Gap / Feature ---
    "objective_without_vision_ref": "attention",
    "objective_without_milestone": "attention",
    "milestone_without_gap": "informational",
    "milestone_without_verification": "attention",
    "gap_without_journey": "attention",
    "gap_source_unresolved": "attention",
    "gap_source_unavailable": "informational",
    "gap_source_contradicted": "informational",
    "requirement_without_feature": "attention",
    "feature_without_implementation_target": "attention",
    "feature_without_capability": "informational",
}

#: The exact §9.2 vocabulary (kept as a tuple purely so a typo above is
#: caught the moment this module is imported, rather than silently returning
#: a `KeyError` deep inside a projection call).
GAP_CODES: Tuple[str, ...] = tuple(_GAP_SEVERITY)

#: §7.2's Value Network notice codes that mean exactly the same fact as one
#: of THIS projection's own gap codes. `stale_confirmation` maps to
#: `stale_upstream` -- "this subject's own confirmed judgement no longer
#: matches its current content" is the lineage-view name for the same fact
#: `stakeholder_value_network` calls a stale confirmation. Every other
#: Value Network notice code (`stakeholder_without_exchange`,
#: `payer_differs_from_beneficiary`, `exchange_without_need`) is a fact this
#: projection does not restate -- the first two are Value-Network-specific
#: observations, and `exchange_without_need` is the mirror image of this
#: module's own `need_without_exchange` (computed the other way around, see
#: `build_functional_lineage`).
#: `stale_upstream` is ALSO computed directly for a Need's own
#: `recheck_state` (see `build_functional_lineage`'s needs loop) -- the Value
#: Network's `stale_confirmation` notice never covers Needs (its nodes are
#: Stakeholders and its edges are Exchanges only), so a Need's own confirmed
#: judgement going stale would otherwise be invisible here.
_VALUE_NETWORK_NOTICE_TO_GAP: Dict[str, str] = {
    "stakeholder_without_role": "stakeholder_without_role",
    "stakeholder_without_need": "stakeholder_without_need",
    "exchange_without_journey": "exchange_without_journey",
    "exchange_without_outcome": "exchange_without_outcome",
    "confirmed_without_evidence": "confirmed_without_evidence",
    "feedback_path_missing": "feedback_path_missing",
    "stale_link": "stale_link",
    "stale_confirmation": "stale_upstream",
}


def _degrade(degraded_sections: List[str], degraded_detail: Dict[str, str], section: str, exc: Exception) -> None:
    if section not in degraded_sections:
        degraded_sections.append(section)
    degraded_detail[section] = f"{type(exc).__name__}: {exc}"


# --- Small local helpers (each module keeps its own copy of a target-kind
# dispatch rather than importing another module's private one -- the same
# precedent `node_design.py` / `ux_design.py` / `journey_blueprint.py` each
# already set for each other one layer over) --------------------------------


def _requirement_keys_for_step(conn: sqlite3.Connection, system_id: int, journey_key: str, step_key: str) -> List[str]:
    """Requirement keys reached through #405's existing
    `ux_requirement_step_link` from one resolved Journey Step -- the exact
    same table `journey_blueprint.py`'s Lane 7 reads, queried locally here so
    this module owns no cross-module private import."""
    journey = conn.execute(
        "SELECT id FROM ux_journey WHERE system_id = ? AND journey_key = ?", (system_id, journey_key)
    ).fetchone()
    if journey is None:
        return []
    rows = conn.execute(
        """SELECT requirement_id FROM ux_requirement_step_link
           WHERE system_id = ? AND journey_id = ? AND step_key = ? AND superseded_by_id IS NULL""",
        (system_id, journey["id"], step_key),
    ).fetchall()
    keys: List[str] = []
    for r in rows:
        req = conn.execute("SELECT requirement_key FROM ux_requirement WHERE id = ?", (r["requirement_id"],)).fetchone()
        if req is not None and req["requirement_key"] not in keys:
            keys.append(req["requirement_key"])
    return keys


def _node_flow_refs(conn: sqlite3.Connection, system_id: int, node_id: int) -> List[str]:
    rows = conn.execute(
        """SELECT DISTINCT target_ref FROM evolution_node_link
           WHERE system_id = ? AND node_id = ? AND link_kind = 'flow' AND superseded_by_id IS NULL""",
        (system_id, node_id),
    ).fetchall()
    return [r["target_ref"] for r in rows]


def _flow_node_keys(conn: sqlite3.Connection, system_id: int, flow_ref: str) -> List[str]:
    rows = conn.execute(
        """SELECT n.node_key FROM evolution_node_link l
           JOIN evolution_node n ON n.id = l.node_id
           WHERE l.system_id = ? AND l.link_kind = 'flow' AND l.target_ref = ?
             AND l.superseded_by_id IS NULL""",
        (system_id, flow_ref),
    ).fetchall()
    return [r["node_key"] for r in rows]


def _need_evidence_state(conn: sqlite3.Connection, system_id: int, need_key: str) -> str:
    """The same §6 fold `stakeholder_value_network._subject_evidence_state`
    performs one layer over, kept as this module's own local copy rather than
    importing that sibling module's private helper (the precedent
    `node_design`/`ux_design`/`stakeholder_network` already set for each
    other's target resolvers)."""
    try:
        detail = sn.get_need_detail(conn, system_id, need_key)
    except Exception:  # pragma: no cover - defensive
        return "unavailable"
    current_digest = (detail.get("current_revision") or {}).get("content_digest", "") or ""
    try:
        result = sn.list_evidence_refs(conn, system_id, "stakeholder_need", need_key)
    except Exception:  # pragma: no cover - defensive
        return "unavailable"
    if result["degraded_sections"]:
        return "unavailable"
    rows = result["evidence_refs"]
    if not rows:
        return "missing"
    if not current_digest:
        return "stale"
    stale_count = sum(1 for r in rows if (r.get("captured_digest") or "") != current_digest)
    return "stale" if stale_count == len(rows) else "available"


# --- The graph being assembled ------------------------------------------------


class _Graph:
    """Accumulates deduplicated nodes/edges/gaps while the chain is walked.
    Kept as a tiny mutable helper local to one `build_functional_lineage`
    call -- never a module-level cache, so nothing here is shared across
    Systems or requests."""

    def __init__(self) -> None:
        self.nodes: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.edges: Set[Tuple[str, str, str, str]] = set()
        self.gaps: List[Dict[str, Any]] = []
        self._gap_seen: Set[Tuple[str, str, str]] = set()

    def add_node(self, kind: str, ref: str, *, name: Optional[str] = None) -> None:
        if not ref:
            return
        key = (kind, ref)
        existing = self.nodes.get(key)
        if existing is None:
            self.nodes[key] = {"kind": kind, "ref": ref, "name": name}
        elif name and not existing.get("name"):
            existing["name"] = name

    def add_edge(self, from_kind: str, from_ref: str, to_kind: str, to_ref: str) -> None:
        if not from_ref or not to_ref:
            return
        self.edges.add((from_kind, from_ref, to_kind, to_ref))

    def add_gap(self, code: str, subject_kind: str, subject_ref: str) -> None:
        if not subject_ref:
            return
        dedup = (code, subject_kind, subject_ref)
        if dedup in self._gap_seen:
            return
        self._gap_seen.add(dedup)
        self.gaps.append(
            {"code": code, "severity": _GAP_SEVERITY[code], "subject_kind": subject_kind, "subject_ref": subject_ref}
        )

    def add_reference_gaps(self, kind: str, ref: str, *, resolution: str, recheck_state: str) -> None:
        """The three shared reference-quality checks every hop's ref carries
        (§9.2/§9.3): an unresolved or unavailable target, and a stale link.
        `unavailable` is asserted ONLY when the read genuinely failed --
        never substituted for `unresolved` (invariant 5).

        `kind`/`ref` identify the entity that HOLDS the reference (e.g. the
        Value Exchange whose Journey Step ref no longer resolves), never the
        broken target -- the same convention every "-without-" gap already
        uses (a Stakeholder without a role is the subject of
        `stakeholder_without_role`, not a hypothetical missing role). This
        keeps `gapsForSubject(entity)` meaningful: selecting an entity in the
        Dashboard shows every gap about ITS OWN references, not gaps that
        happen to name something it points at."""
        if resolution == "unresolved":
            self.add_gap("unresolved_reference", kind, ref)
        elif resolution == "unavailable":
            self.add_gap("unavailable_reference", kind, ref)
        if recheck_state == "stale":
            self.add_gap("stale_link", kind, ref)

    def sorted_nodes(self) -> List[Dict[str, Any]]:
        return [self.nodes[k] for k in sorted(self.nodes)]

    def sorted_edges(self) -> List[Dict[str, Any]]:
        return [
            {"from_kind": e[0], "from_ref": e[1], "to_kind": e[2], "to_ref": e[3]}
            for e in sorted(self.edges)
        ]

    def sorted_gaps(self) -> List[Dict[str, Any]]:
        return sorted(self.gaps, key=lambda g: (g["code"], g["subject_kind"], g["subject_ref"]))


# --- Node / Flow hop (§9.1's Solution Design -> Flow -> Node segment) --------


def _check_node_hop(conn: sqlite3.Connection, system_id: int, node_key: str, graph: _Graph, policies: Dict[str, List[Dict[str, Any]]]) -> None:
    node_row = conn.execute(
        "SELECT id FROM evolution_node WHERE system_id = ? AND node_key = ?", (system_id, node_key)
    ).fetchone()
    if node_row is None:
        return
    flow_refs = _node_flow_refs(conn, system_id, node_row["id"])
    if not flow_refs:
        graph.add_gap("node_without_flow", "evolution_node", node_key)
    for flow_ref in flow_refs:
        graph.add_node("runtime_flow", flow_ref)
        graph.add_edge("evolution_node", node_key, "runtime_flow", flow_ref)
    if not any(p.get("subject_ref") == node_key for p in policies.get("node", [])):
        graph.add_gap("subject_without_evaluation_policy", "evolution_node", node_key)


def _check_flow_hop(conn: sqlite3.Connection, system_id: int, flow_kind: str, flow_ref: str, graph: _Graph, policies: Dict[str, List[Dict[str, Any]]]) -> None:
    node_keys = _flow_node_keys(conn, system_id, flow_ref) if flow_kind == "runtime_flow" else []
    if flow_kind == "runtime_flow":
        if not node_keys:
            graph.add_gap("flow_without_node", flow_kind, flow_ref)
        for node_key in node_keys:
            graph.add_node("evolution_node", node_key)
            graph.add_edge(flow_kind, flow_ref, "evolution_node", node_key)
    if not any(p.get("subject_ref") == flow_ref for p in policies.get("flow_capability", [])):
        graph.add_gap("subject_without_evaluation_policy", flow_kind, flow_ref)


# --- Exchange chain (Need -> Value Exchange -> Journey/Step -> Requirement ->
# Solution Design -> Flow/Node -> Outcome), reusing get_exchange_lineage ------


def _walk_exchange_chain(
    conn: sqlite3.Connection,
    system_id: int,
    exchange_key: str,
    graph: _Graph,
    policies: Dict[str, List[Dict[str, Any]]],
    need_exchange_map: Dict[str, List[str]],
    need_journey_ok: Set[str],
) -> None:
    lineage = sn.get_exchange_lineage(conn, system_id, exchange_key)
    for section in lineage.get("degraded_sections", []):
        graph.add_gap("unavailable_reference", "value_exchange", exchange_key)

    # Capability refs on the Exchange itself (§9.1's Capability -> Value
    # Exchange hop; `get_exchange_lineage` does not surface these -- it was
    # written before Capability was part of THIS chain, so this module reads
    # them directly rather than widening that function's own contract).
    try:
        cap_refs = sn.list_refs(conn, system_id, source_kind="value_exchange", source_key=exchange_key)
        for ref in cap_refs.get("refs", []):
            if ref["ref_kind"] != "capability_entity":
                continue
            graph.add_node("capability", ref["target_ref"])
            graph.add_edge("capability", ref["target_ref"], "value_exchange", exchange_key)
            graph.add_reference_gaps(
                "capability", ref["target_ref"],
                resolution=ref["target_resolution"], recheck_state=ref["recheck_state"],
            )
    except Exception:  # pragma: no cover - defensive
        graph.add_gap("unavailable_reference", "value_exchange", exchange_key)

    for need_ref in lineage.get("needs", []):
        need_key = need_ref["target_ref"]
        need_exchange_map.setdefault(need_key, []).append(exchange_key)
        graph.add_node("stakeholder_need", need_key, name=need_ref.get("target_name"))
        graph.add_edge("stakeholder_need", need_key, "value_exchange", exchange_key)
        graph.add_reference_gaps(
            "value_exchange", exchange_key,
            resolution=need_ref["target_resolution"], recheck_state=need_ref["recheck_state"],
        )
        try:
            need_detail = sn.get_need_detail(conn, system_id, need_key)
            if need_detail.get("design_status") == "rejected":
                graph.add_gap("rejected_dependency", "value_exchange", exchange_key)
        except Exception:  # pragma: no cover - defensive
            pass

    journey_has_ref = False
    for jref in lineage.get("journey_refs", []):
        kind = jref["ref_kind"]
        target_ref = jref["target_ref"]
        journey_has_ref = True
        graph.add_node(kind, target_ref, name=jref.get("target_name"))
        graph.add_edge("value_exchange", exchange_key, kind, target_ref)
        graph.add_reference_gaps(
            "value_exchange", exchange_key,
            resolution=jref["target_resolution"], recheck_state=jref["recheck_state"],
        )
        if kind == "ux_journey_step" and jref["target_resolution"] == "resolved":
            journey_key, sep, step_key = (target_ref or "").partition("#")
            if sep:
                req_keys = _requirement_keys_for_step(conn, system_id, journey_key, step_key)
                if not req_keys:
                    graph.add_gap("journey_step_without_requirement", "ux_journey_step", target_ref)
                for req_key in req_keys:
                    graph.add_node("ux_requirement", req_key)
                    graph.add_edge("ux_journey_step", target_ref, "ux_requirement", req_key)
    if journey_has_ref:
        need_journey_ok.update(n["target_ref"] for n in lineage.get("needs", []))

    if not lineage.get("journey_refs"):
        graph.add_gap("exchange_without_journey", "value_exchange", exchange_key)
    if not lineage.get("outcomes"):
        graph.add_gap("exchange_without_outcome", "value_exchange", exchange_key)

    for outcome_ref in lineage.get("outcomes", []):
        graph.add_node("purpose_outcome_criterion", outcome_ref["target_ref"], name=outcome_ref.get("target_name"))
        graph.add_edge("value_exchange", exchange_key, "purpose_outcome_criterion", outcome_ref["target_ref"])
        graph.add_reference_gaps(
            "value_exchange", exchange_key,
            resolution=outcome_ref["target_resolution"], recheck_state=outcome_ref["recheck_state"],
        )

    # Requirement -> acceptance criteria -> Solution Design -> Flow/Node.
    design_by_req: Dict[str, List[Dict[str, Any]]] = {}
    for d in lineage.get("solution_designs", []):
        design_by_req.setdefault(d["requirement_key"], []).append(d)

    for req in lineage.get("requirements", []):
        req_key = req["requirement_key"]
        graph.add_node("ux_requirement", req_key)
        if req["target_resolution"] != "resolved":
            graph.add_reference_gaps("ux_requirement", req_key, resolution=req["target_resolution"], recheck_state="current")
            continue
        try:
            detail = ux_design.get_requirement_detail(conn, system_id, req_key)
            rev = detail.get("current_revision") or {}
            criteria = rev.get("acceptance_criteria") or []
            if not criteria:
                graph.add_gap("requirement_without_acceptance_criterion", "ux_requirement", req_key)
        except Exception:  # pragma: no cover - defensive
            graph.add_gap("unavailable_reference", "ux_requirement", req_key)

        designs = design_by_req.get(req_key, [])
        if not designs:
            graph.add_gap("requirement_without_design", "ux_requirement", req_key)
        for d in designs:
            design_key = d["design_key"]
            graph.add_node("solution_design", design_key, name=d.get("title"))
            graph.add_edge("ux_requirement", req_key, "solution_design", design_key)
            adopted_option_key = d.get("adopted_option_key")
            if not adopted_option_key:
                # No option adopted yet -- an open design in progress is not
                # itself a gap; §9.2's `adopted_design_without_implementation_target`
                # is specifically about an ADOPTED option with nowhere to land.
                continue
            try:
                design_detail = solution_design.get_design_detail(conn, system_id=system_id, design_key=design_key)
            except Exception:  # pragma: no cover - defensive
                graph.add_gap("unavailable_reference", "solution_design", design_key)
                continue
            target_links = [t for t in design_detail.get("target_links", []) if t.get("option_key") == adopted_option_key]
            if not target_links:
                graph.add_gap("adopted_design_without_implementation_target", "solution_design", design_key)
            for t in target_links:
                target_kind, target_ref = t["target_kind"], t["target_ref"]
                graph.add_node(target_kind, target_ref, name=t.get("target_name"))
                graph.add_edge("solution_design", design_key, target_kind, target_ref)
                link_state = t.get("link_state")
                if link_state == "unresolved":
                    graph.add_gap("unresolved_reference", "solution_design", design_key)
                elif link_state == "unavailable":
                    graph.add_gap("unavailable_reference", "solution_design", design_key)
                elif link_state == "stale":
                    graph.add_gap("stale_link", "solution_design", design_key)
                if link_state == "current" and target_kind == "evolution_node":
                    _check_node_hop(conn, system_id, target_ref, graph, policies)
                elif link_state == "current" and target_kind in ("static_flow", "runtime_flow"):
                    _check_flow_hop(conn, system_id, target_kind, target_ref, graph, policies)


# --- Purpose dependency check for a Need (§9.2's need_without_purpose /
# conflicting_dependency) ------------------------------------------------------


def _walk_need_purpose_refs(
    conn: sqlite3.Connection, system_id: int, need_key: str, graph: _Graph, chain: Optional["purpose_chain.PurposeChainResult"]
) -> None:
    try:
        refs = sn.list_refs(conn, system_id, source_kind="stakeholder_need", source_key=need_key)
    except Exception:  # pragma: no cover - defensive
        graph.add_gap("unavailable_reference", "stakeholder_need", need_key)
        return
    purpose_refs = [r for r in refs.get("refs", []) if r["ref_kind"] in ("purpose_element", "purpose_relation")]
    if not purpose_refs:
        graph.add_gap("need_without_purpose", "stakeholder_need", need_key)
        return
    for ref in purpose_refs:
        graph.add_node(ref["ref_kind"], ref["target_ref"])
        graph.add_edge(ref["ref_kind"], ref["target_ref"], "stakeholder_need", need_key)
        graph.add_reference_gaps(
            "stakeholder_need", need_key, resolution=ref["target_resolution"], recheck_state=ref["recheck_state"]
        )
        if ref["ref_kind"] == "purpose_relation" and chain is not None:
            relation = next((r for r in chain.relations if r.id == ref["target_ref"]), None)
            if relation is not None and relation.status == "conflicting":
                graph.add_gap("conflicting_dependency", "stakeholder_need", need_key)


# --- Product Objective / Milestone / Gap / Feature (Epic #427 §7.3) ---------

#: §7.3's ref-kind -> graph-node-kind mapping for an Objective's upstream
#: ref (`ProductRefKind`, `app/product_objective.py`). `vision_claim` maps to
#: its OWN `FunctionalLineageKind`, not onto `purpose_element`: a Vision claim
#: is referenced by NAME (it has no row identity, §4.6) while a Purpose
#: element is referenced by a fixed slug such as `beneficiary_problem`, and
#: rendering the two under one label is the one-word-two-facts conflation this
#: Epic exists to avoid (#380's superset rule -- a downstream vocabulary may
#: add values, never collapse them). `purpose_relation` likewise keeps its own
#: kind, matching how the graph already treats an element and a relation as
#: two different kinds everywhere else.
_OBJECTIVE_REF_NODE_KIND: Dict[str, str] = {
    "vision_claim": "vision_claim",
    "purpose_element": "purpose_element",
    "purpose_relation": "purpose_relation",
    "capability_entity": "capability",
    "stakeholder_need": "stakeholder_need",
}

#: The three ref kinds that answer "what the Objective is FOR" (§1.2) --
#: only a RESOLVED ref of one of these clears `objective_without_vision_ref`.
#: `capability_entity` / `stakeholder_need` answer "what it can do" / "whose
#: need it addresses", never a substitute for a Vision/Purpose connection.
_OBJECTIVE_VISION_REF_KINDS: Set[str] = {"vision_claim", "purpose_element", "purpose_relation"}


def _journeys_referencing_gap(conn: sqlite3.Connection, system_id: int, gap_key: str) -> List[str]:
    """Journey keys reached through #405's `ux_journey_upstream_ref` via its
    new `ref_kind='product_gap'` value (§7.1) -- the ONE path a Gap connects
    to a Journey, queried locally here for the same reason
    `_requirement_keys_for_step` above queries `ux_requirement_step_link`
    directly rather than importing another module's private reverse-lookup:
    no such lookup exists in `ux_design.py`, and this module owns no
    cross-module private import."""
    rows = conn.execute(
        """SELECT DISTINCT j.journey_key FROM ux_journey_upstream_ref r
           JOIN ux_journey j ON j.id = r.journey_id
           WHERE r.system_id = ? AND r.ref_kind = 'product_gap' AND r.target_ref = ?
             AND r.superseded_by_id IS NULL""",
        (system_id, gap_key),
    ).fetchall()
    return [r["journey_key"] for r in rows]


def _check_product_objectives(
    conn: sqlite3.Connection,
    system_id: int,
    graph: _Graph,
    degraded_sections: List[str],
    degraded_detail: Dict[str, str],
) -> Optional[List[str]]:
    """§7.3's Objective -> Milestone segment. A System that has adopted no
    Product Objective at all must produce NO new node, edge, or gap here --
    `product_objective.list_objectives` simply returns an empty list, so the
    loop below never runs (§4's explicit "no Objective => no new gap"
    requirement).

    Returns the flat list of every current Milestone key found (for
    `_check_product_gaps`'s `milestone_without_gap` check below), or `None`
    when this section itself degraded -- `None` is NOT "no milestones exist"
    (invariant 5): asserting `milestone_without_gap` from an unreadable
    Milestone set would be exactly the guessed value this module's own
    docstring forbids.
    """
    try:
        result = product_objective.list_objectives(conn, system_id)
        if result["degraded_sections"]:
            raise RuntimeError(f"objectives unavailable: {result['degraded_detail']}")

        all_milestone_keys: List[str] = []
        for obj in result["objectives"]:
            objective_key = obj["objective_key"]
            graph.add_node("product_objective", objective_key, name=obj.get("title"))
            parent_key = obj.get("parent_objective_key")
            if parent_key:
                graph.add_edge("product_objective", parent_key, "product_objective", objective_key)

            try:
                detail = product_objective.get_objective_detail(conn, system_id, objective_key)
                if "upstream_refs" in detail.get("degraded_sections", []):
                    _degrade(
                        degraded_sections, degraded_detail, f"product_objective.upstream_refs:{objective_key}",
                        RuntimeError(detail["degraded_detail"].get("upstream_refs", "unavailable")),
                    )
                else:
                    has_vision_ref = False
                    for ref in detail.get("upstream_refs", []):
                        ref_kind = ref["ref_kind"]
                        node_kind = _OBJECTIVE_REF_NODE_KIND.get(ref_kind)
                        target_ref = ref["target_ref"]
                        resolution = ref["target_resolution"]
                        # §7.3: only a RESOLVED ref becomes an edge -- a
                        # deleted/cross-System/unresolved target would
                        # otherwise become a phantom node and silently clear
                        # `objective_without_vision_ref`.
                        if node_kind and resolution == "resolved":
                            graph.add_node(node_kind, target_ref, name=ref.get("target_name"))
                            graph.add_edge(node_kind, target_ref, "product_objective", objective_key)
                            if ref_kind in _OBJECTIVE_VISION_REF_KINDS:
                                has_vision_ref = True
                        graph.add_reference_gaps(
                            "product_objective", objective_key,
                            resolution=resolution, recheck_state=ref["recheck_state"],
                        )
                    if not has_vision_ref:
                        graph.add_gap("objective_without_vision_ref", "product_objective", objective_key)
            except Exception as exc:  # pragma: no cover - defensive
                _degrade(degraded_sections, degraded_detail, f"product_objective.detail:{objective_key}", exc)

            ms_result = product_objective.list_milestones(conn, system_id, objective_key)
            if ms_result["degraded_sections"]:
                raise RuntimeError(
                    f"milestones unavailable for {objective_key!r}: {ms_result['degraded_detail']}"
                )
            milestones = ms_result["milestones"]
            if not milestones:
                graph.add_gap("objective_without_milestone", "product_objective", objective_key)
            for m in milestones:
                milestone_key = m["milestone_key"]
                all_milestone_keys.append(milestone_key)
                graph.add_node("product_milestone", milestone_key, name=m.get("title"))
                graph.add_edge("product_objective", objective_key, "product_milestone", milestone_key)
                # §4.3: `assessability == "unavailable"` is reachable ONLY
                # through `derive_milestone_assessability`'s first-match
                # `verification_method == "unavailable"` branch -- reusing
                # this already-derived field is exactly §7.3's "reuse
                # derive_*, never re-fold a ledger" rule, applied to a value
                # `_milestone_out_dict` already folds one layer over.
                if m.get("assessability") == "unavailable":
                    graph.add_gap("milestone_without_verification", "product_milestone", milestone_key)
        return all_milestone_keys
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "product_objectives", exc)
        return None


def _check_product_gaps(
    conn: sqlite3.Connection,
    system_id: int,
    graph: _Graph,
    degraded_sections: List[str],
    degraded_detail: Dict[str, str],
    known_milestone_keys: Optional[List[str]],
) -> None:
    """§7.3's Milestone -> Gap -> UX Journey segment, plus the §5.4 source
    federation's three read-time states this projection surfaces
    (`disappeared` / `unavailable` / `contradicted`). `known_milestone_keys`
    is `None` when `_check_product_objectives` itself degraded -- see that
    function's docstring for why `milestone_without_gap` is skipped rather
    than guessed in that case."""
    try:
        result = product_objective.list_gaps(conn, system_id)
        if result["degraded_sections"]:
            raise RuntimeError(f"gaps unavailable: {result['degraded_detail']}")

        milestones_with_gaps: Set[str] = set()
        for g in result["gaps"]:
            gap_key = g["gap_key"]
            milestone_key = g.get("milestone_key")
            graph.add_node("product_gap", gap_key, name=g.get("title"))
            if milestone_key:
                graph.add_node("product_milestone", milestone_key)
                graph.add_edge("product_milestone", milestone_key, "product_gap", gap_key)
                milestones_with_gaps.add(milestone_key)

            journey_keys = _journeys_referencing_gap(conn, system_id, gap_key)
            if not journey_keys:
                graph.add_gap("gap_without_journey", "product_gap", gap_key)
            for journey_key in journey_keys:
                graph.add_node("ux_journey", journey_key)
                graph.add_edge("product_gap", gap_key, "ux_journey", journey_key)

            try:
                detail = product_objective.get_gap_detail(conn, system_id, gap_key)
                if "source_refs" in detail.get("degraded_sections", []):
                    _degrade(
                        degraded_sections, degraded_detail, f"product_gap.source_refs:{gap_key}",
                        RuntimeError(detail["degraded_detail"].get("source_refs", "unavailable")),
                    )
                else:
                    # §5.4's `ProductGapSourceState`. `current`/`changed` are
                    # not a gap here -- §7.3 lists exactly three of the five
                    # states as reachable gap codes; `changed` already
                    # surfaces through the Gap's own `read_flags`
                    # (`recheck_required`), not a lineage gap.
                    for source in detail.get("source_refs", []):
                        state = source.get("source_state")
                        if state == "disappeared":
                            graph.add_gap("gap_source_unresolved", "product_gap", gap_key)
                        elif state == "unavailable":
                            graph.add_gap("gap_source_unavailable", "product_gap", gap_key)
                        elif state == "contradicted":
                            graph.add_gap("gap_source_contradicted", "product_gap", gap_key)
            except Exception as exc:  # pragma: no cover - defensive
                _degrade(degraded_sections, degraded_detail, f"product_gap.detail:{gap_key}", exc)

        if known_milestone_keys is not None:
            for milestone_key in known_milestone_keys:
                if milestone_key not in milestones_with_gaps:
                    graph.add_gap("milestone_without_gap", "product_milestone", milestone_key)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "product_gaps", exc)


def _check_product_features(
    conn: sqlite3.Connection,
    system_id: int,
    graph: _Graph,
    degraded_sections: List[str],
    degraded_detail: Dict[str, str],
) -> None:
    """§7.3's Requirement -> Feature -> Capability/implementation-target
    segment. `product_feature.get_feature_detail`'s already-resolved
    `target_links` (built on `solution_design._resolve_target` /
    `node_design._resolve_capability`, §7.2's explicit reuse instruction) is
    this function's ONLY path to a Feature's implementation targets -- the
    same "no second path to Flow/Node" precedent
    `_walk_exchange_chain`'s `solution_design.get_design_detail` call
    already sets one layer over.

    Only a RESOLVED link becomes an edge, for all three link kinds -- an
    unresolved/unavailable/stale target maps onto the same
    `unresolved_reference` / `unavailable_reference` / `stale_link` codes
    every other hop in this module already uses, with the FEATURE (the
    link's owner) as the gap subject, never a phantom node for the missing
    target (§7.3)."""
    try:
        result = product_feature.list_features(conn, system_id)
        if result["degraded_sections"]:
            raise RuntimeError(f"features unavailable: {result['degraded_detail']}")

        requirement_keys_with_feature: Set[str] = set()
        for f in result["features"]:
            feature_key = f["feature_key"]
            graph.add_node("product_feature", feature_key, name=f.get("title"))

            try:
                detail = product_feature.get_feature_detail(conn, system_id, feature_key)
            except Exception as exc:  # pragma: no cover - defensive
                _degrade(degraded_sections, degraded_detail, f"product_feature.detail:{feature_key}", exc)
                continue

            for section in ("requirement_links", "capability_links", "target_links"):
                if section in detail.get("degraded_sections", []):
                    _degrade(
                        degraded_sections, degraded_detail, f"product_feature.{section}:{feature_key}",
                        RuntimeError(detail["degraded_detail"].get(section, "unavailable")),
                    )

            for link in detail.get("requirement_links", []):
                req_key = link.get("requirement_key")
                if not req_key:
                    continue
                # `target_resolution` is the link's own published axis, the
                # same one the Capability and target links below report.
                # `recheck_state` cannot stand in for it: it has no
                # `unresolved` member, so a deleted Requirement and one whose
                # revision merely moved both read `stale`.
                resolution = link.get("target_resolution", "unavailable")
                if resolution == "resolved":
                    requirement_keys_with_feature.add(req_key)
                    graph.add_node("ux_requirement", req_key)
                    graph.add_edge("ux_requirement", req_key, "product_feature", feature_key)
                graph.add_reference_gaps(
                    "product_feature", feature_key,
                    resolution=resolution,
                    recheck_state=link.get("recheck_state", "current"),
                )

            has_capability = False
            for link in detail.get("capability_links", []):
                resolution = link.get("target_resolution")
                graph.add_reference_gaps(
                    "product_feature", feature_key,
                    resolution=resolution, recheck_state=link.get("recheck_state", "current"),
                )
                if resolution == "resolved":
                    has_capability = True
                    cap_ref = str(link.get("capability_entity_id"))
                    graph.add_node("capability", cap_ref, name=link.get("capability_name"))
                    graph.add_edge("product_feature", feature_key, "capability", cap_ref)
            if not has_capability:
                graph.add_gap("feature_without_capability", "product_feature", feature_key)

            has_target = False
            for link in detail.get("target_links", []):
                resolution = link.get("target_resolution")
                graph.add_reference_gaps(
                    "product_feature", feature_key,
                    resolution=resolution, recheck_state=link.get("recheck_state", "current"),
                )
                target_kind = link.get("link_kind")
                target_ref = link.get("target_ref")
                if resolution == "resolved" and target_kind and target_ref:
                    has_target = True
                    graph.add_node(target_kind, target_ref)
                    graph.add_edge("product_feature", feature_key, target_kind, target_ref)
            if not has_target:
                graph.add_gap("feature_without_implementation_target", "product_feature", feature_key)

        # Feature, like Objective/Milestone/Gap, is a new, OPTIONAL layer
        # (§0-1/§1.6) -- a System that has created no Feature at all has not
        # adopted it, and is not "missing" a Feature on every one of its
        # ordinary (Journey-driven) Requirements any more than a System with
        # no Objective is missing a Milestone (§4's explicit "layer
        # optional" rule, applied here to Feature the same way
        # `_check_product_objectives` applies it to Objective). Only once at
        # least one Feature exists does an uncovered Requirement become a
        # meaningful gap.
        if result["features"]:
            req_result = ux_design.list_requirements(conn, system_id)
            if req_result["degraded_sections"]:
                raise RuntimeError(f"requirements unavailable: {req_result['degraded_detail']}")
            for r in req_result["requirements"]:
                req_key = r["requirement_key"]
                if req_key not in requirement_keys_with_feature:
                    graph.add_gap("requirement_without_feature", "ux_requirement", req_key)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "product_features", exc)


# --- Top-level projection ----------------------------------------------------


def build_functional_lineage(
    conn: sqlite3.Connection,
    system_id: int,
    *,
    include_product_objective_layer: bool = True,
) -> Dict[str, Any]:
    """§9's Functional Lineage View + Gap/Impact Overlay. Read-only,
    deterministic, no LLM, writes nothing. Every major section is its own
    guarded loader (#380's discipline): a failure records the section in
    `degraded_sections` and stops that section's own traversal -- never
    substituting a guessed value or reporting `unavailable` as `missing`
    (invariant 5).

    `include_product_objective_layer=False` drops sections 6-8 (Issue #427's
    Objective / Gap / Feature). It exists to break a genuine cycle, not as a
    performance switch:

        _check_product_gaps
          -> product_objective.get_gap_detail   (resolves the Gap's sources)
            -> product_gap_sources.resolve_source('functional_lineage_gap')
              -> build_functional_lineage
                -> _check_product_gaps ...

    A Gap can be DETECTED BY this projection and can also be REPORTED ON by
    it, so the two directions meet. The cycle is broken on the resolver's
    side because that is the side with the answer: a Gap's detection source
    is UPSTREAM of the Gap, while sections 6-8 are DOWNSTREAM of it, and a
    source resolution that re-entered them would be asking the projection
    about the very rows whose state it is in the middle of computing.
    Sections 1-5 -- the detector that actually emitted the gap code -- are
    unaffected and still answer in full, so the resolver loses nothing it
    needs.

    Left unbroken this does not raise; each level does real work, so it
    simply never returns. That is how it reached CI as a six-hour job
    cancellation rather than an error."""
    graph = _Graph()
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    # 1. Reuse the Value Network projection verbatim for its own structural
    # notices and for the Stakeholder/Exchange node set (invariant 1: no
    # second answer to what #422 already computes).
    vn: Optional[Dict[str, Any]] = None
    try:
        vn = svn.build_value_network(conn, system_id)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "value_network", exc)

    if vn is not None:
        for section in vn.get("degraded_sections", []):
            _degrade(degraded_sections, degraded_detail, f"value_network.{section}", RuntimeError(vn["degraded_detail"].get(section, "unavailable")))
        for node in vn.get("nodes", []):
            graph.add_node("stakeholder", node["stakeholder_key"], name=node.get("display_name"))
        for edge in vn.get("edges", []):
            graph.add_node("value_exchange", edge["exchange_key"], name=edge.get("value_statement"))
            graph.add_edge("stakeholder", edge["provider_stakeholder_key"], "value_exchange", edge["exchange_key"])
            graph.add_edge("value_exchange", edge["exchange_key"], "stakeholder", edge["receiver_stakeholder_key"])
            if edge.get("evidence_state") == "stale":
                graph.add_gap("stale_evidence", "value_exchange", edge["exchange_key"])
        for notice in vn.get("notices", []):
            mapped = _VALUE_NETWORK_NOTICE_TO_GAP.get(notice["code"])
            if mapped:
                graph.add_gap(mapped, notice["subject_kind"], notice["subject_key"])

    # 2. Evaluation policies, loaded once and shared by every Flow/Node hop.
    policies: Dict[str, List[Dict[str, Any]]] = {}
    try:
        policies = node_design.list_evaluation_policies(conn, system_id=system_id)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "evaluation_policies", exc)

    # 3. The Purpose Chain, read once and shared by every Need's purpose
    # dependency check (relation `status` for `conflicting_dependency`).
    chain = None
    try:
        chain = purpose_chain.derive_purpose_chain(conn, system_id, None)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "purpose_chain", exc)

    # 4. Value Exchanges -- the main downstream traversal.
    need_exchange_map: Dict[str, List[str]] = {}
    need_journey_ok: Set[str] = set()
    try:
        exchanges_result = sn.list_exchanges(conn, system_id)
        if exchanges_result["degraded_sections"]:
            raise RuntimeError(f"exchanges unavailable: {exchanges_result['degraded_detail']}")
        for summary in exchanges_result["exchanges"]:
            _walk_exchange_chain(
                conn, system_id, summary["exchange_key"], graph, policies, need_exchange_map, need_journey_ok
            )
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "exchanges", exc)

    # 5. Needs -- purpose dependency + exchange/journey coverage.
    try:
        needs_result = sn.list_needs(conn, system_id)
        if needs_result["degraded_sections"]:
            raise RuntimeError(f"needs unavailable: {needs_result['degraded_detail']}")
        for need in needs_result["needs"]:
            need_key = need["need_key"]
            graph.add_node("stakeholder_need", need_key, name=need.get("statement"))
            stakeholder_key = need.get("stakeholder_key") or ""
            if stakeholder_key:
                graph.add_edge("stakeholder", stakeholder_key, "stakeholder_need", need_key)
            _walk_need_purpose_refs(conn, system_id, need_key, graph, chain)
            if need_key not in need_exchange_map:
                graph.add_gap("need_without_exchange", "stakeholder_need", need_key)
            elif need_key not in need_journey_ok:
                graph.add_gap("need_without_journey", "stakeholder_need", need_key)
            # A Need's OWN confirmed judgement going stale (its content moved
            # since the decision was recorded) is `stale_upstream` -- distinct
            # from `stale_link`, which is about a REFERENCE pointing at it
            # going stale (§4's two independent staleness producers, applied
            # here to the Need itself rather than to one of its refs). The
            # Value Network's own `stale_confirmation` notice does not cover
            # Needs (its nodes are Stakeholders and its edges are Exchanges
            # only), so this module computes it directly for the Need.
            if need.get("recheck_state") == "stale":
                graph.add_gap("stale_upstream", "stakeholder_need", need_key)
            evidence_state = _need_evidence_state(conn, system_id, need_key)
            if evidence_state == "stale":
                graph.add_gap("stale_evidence", "stakeholder_need", need_key)
            if need.get("design_status") == "confirmed" and evidence_state == "missing":
                graph.add_gap("confirmed_without_evidence", "stakeholder_need", need_key)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "needs", exc)

    if include_product_objective_layer:
        # 6. Product Objective / Milestone -- Epic #427 §7.3. A System that
        # has adopted no Product Objective produces no new node/edge/gap
        # here (§4).
        known_milestone_keys = _check_product_objectives(
            conn, system_id, graph, degraded_sections, degraded_detail
        )

        # 7. Product Gap -- Milestone -> Gap -> UX Journey, plus §5.4's
        # source federation states.
        _check_product_gaps(
            conn, system_id, graph, degraded_sections, degraded_detail, known_milestone_keys
        )

        # 8. Product Feature -- Requirement -> Feature -> Capability/target.
        _check_product_features(conn, system_id, graph, degraded_sections, degraded_detail)

    return {
        "nodes": graph.sorted_nodes(),
        "edges": graph.sorted_edges(),
        "gaps": graph.sorted_gaps(),
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


# --- §9.3's downstream-only impact traversal --------------------------------


def trace_downstream_impact(
    edges: List[Dict[str, Any]], kind: str, ref: str
) -> List[Dict[str, str]]:
    """Breadth-first walk of `edges` FORWARD ONLY (`from_kind`/`from_ref` ->
    `to_kind`/`to_ref`) starting at `(kind, ref)`. Every edge this module
    records already points from the upstream entity to the entity it feeds
    (see the module docstring), so this function never needs -- and never
    performs -- a reverse walk. Used by both a regression test (impact never
    reaches upstream of its start) and, identically, by the Dashboard's own
    `components/functional-lineage/model.ts` (a pure re-implementation over
    the same already-decided edges, not a second judgement)."""
    adjacency: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for e in edges:
        adjacency.setdefault((e["from_kind"], e["from_ref"]), []).append((e["to_kind"], e["to_ref"]))

    visited: Set[Tuple[str, str]] = set()
    frontier: List[Tuple[str, str]] = [(kind, ref)]
    result: List[Dict[str, str]] = []
    while frontier:
        current = frontier.pop(0)
        for nxt in adjacency.get(current, []):
            if nxt in visited:
                continue
            visited.add(nxt)
            result.append({"kind": nxt[0], "ref": nxt[1]})
            frontier.append(nxt)
    return result
