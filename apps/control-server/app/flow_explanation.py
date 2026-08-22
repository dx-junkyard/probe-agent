"""Flow / agent-group explanation projection (Epic #412, Issue #414).

Canonical contract: `docs/execution-modes.md` §6 (plus §1.2, §2 and §9.2).
This module is the ONLY authority for the aggregated explanation of a Flow;
neither the Dashboard nor an LLM re-derives any of it.

Five properties are the contract, and every later change must preserve them.

1. **It writes nothing.** Not a row, not a checkpoint, not a "last viewed"
   marker. `app/interview_workflow.py`'s `evaluate_session_workflow` (which
   persists) is deliberately never reached from here, exactly as
   `app/overview_projection.py` avoids it: looking at an explanation must not
   record a decision (#380). `tests/test_flow_explanation.py` asserts every
   table's row count is unchanged across a projection call.

2. **No new understanding model, and no new identity.** Purpose / Capability /
   Flow / Node / evidence keep their existing canonical owners and are read
   through them: `purpose_chain.derive_purpose_chain`,
   `node_design._resolve_link_target` (the per-kind canonical resolver),
   `evolution_node.build_node_projection`,
   `node_operations.build_operations_projection`,
   `execution_mode.build_mode_projection`. In particular `flow_graph`'s
   `flow-1` / `flow-2` candidate-path names are stable only WITHIN one
   derivation, so this module never mints, stores or returns them as a Flow
   identity (#405's rule, restated in §6.2).

3. **Five separate axes per Node, never combined** (§1.2 / ADR-6):
   `execution_mode` (+ `mode_source` + `mode_reason`), `maturity`,
   `implementation_modality`, `improvement_status`, `sdk_policy_mode`, plus
   the #400 observation coverage as its own reading. There is no average, no
   completion percentage, no confidence score and no single "Flow health"
   number anywhere in the output (ADR-7 / #353). `sdk_policy_mode == 'shadow'`
   and `execution_mode == 'shadow'` are two DIFFERENT facts and are shown as
   two fields (§1.2).

4. **Membership is exact, never similar** (§6.2 / Principle 6). A runtime
   Flow's Nodes come from `evolution_node_link(link_kind='flow')`; a static
   Flow's Nodes come from each Node's approved `probe_point` link, whose
   `(path, symbol)` must EQUAL a flow-graph node's `(path, qualified_name)`.
   No similarity, no keyword scoring, no embeddings. When the flow graph
   cannot be built the membership is `unavailable` -- never an empty set,
   because "nothing belongs to this Flow" and "we could not tell what belongs
   to this Flow" are different answers.

5. **The five-value missing vocabulary stays five values** (§6.4):
   `missing` (does not exist) / `unavailable` (could not be read) /
   `unmeasured` (nothing measures it) / `stale` (its premise moved) /
   `not_applicable` (structurally unnecessary). They are never rounded
   together, and a section that fails lands in `degraded_sections` and drops
   its DISPLAY (the section value becomes `None`) rather than substituting a
   guessed value -- the same guarded-loader shape `app/overview_projection.py`
   uses.

§6.6's optional natural-language summary is deliberately NOT implemented
here: it is explicitly optional, it would be the only LLM call on an
otherwise deterministic read path, and its absence changes nothing about the
structured evidence cards that must come first.

probe-agent:
  role: Read-only aggregation of a Flow's purpose, responsibility, Nodes, open items, experiments and baseline
  capability: flow-explanation
  element_type: domain
  consumers: [control-server]
  operation_kind: transformation
  state_effects: [database-read]
  probe_value: Verify the projection writes nothing, keeps the five Node axes separate, distinguishes the five missing values, and reports static membership only on an exact (path, qualified_name) match.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .db import get_conn

__all__ = [
    "FLOW_EXPLANATION_SCHEMA_VERSION",
    "FLOW_SUBJECT_KINDS",
    "FLOW_EXPLANATION_SECTIONS",
    "FACT_STATES",
    "MISSING_STATES",
    "MEMBERSHIP_STATES",
    "SUBJECT_RESOLUTIONS",
    "EVIDENCE_KINDS",
    "OPEN_ITEM_KINDS",
    "MEMBERSHIP_BASES",
    "Evidence",
    "FlowSubject",
    "Membership",
    "NodeExplanation",
    "PurposeSection",
    "ResponsibilitySection",
    "OpenItem",
    "OpenItemsSection",
    "ExperimentSummary",
    "ExperimentsSection",
    "NodeBaseline",
    "BaselineSection",
    "FlowSubjectListing",
    "FlowExplanation",
    "list_flow_subjects",
    "build_flow_explanation",
]


FLOW_EXPLANATION_SCHEMA_VERSION = "flow-explanation-v1"

#: §6.2. `static_flow` is a DISPLAY subject only -- it is deliberately not an
#: execution-mode scope (EM-ADR-1), because resolving its Node membership
#: needs a call graph that can itself fail, and a fail-closed gate must not
#: depend on a derivation that can fail.
FLOW_SUBJECT_KINDS: Tuple[str, ...] = ("runtime_flow", "static_flow")

#: §6.3's six sections, each independently guarded.
FLOW_EXPLANATION_SECTIONS: Tuple[str, ...] = (
    "purpose",
    "responsibility",
    "nodes",
    "open_items",
    "experiments",
    "baseline",
)

#: §6.4's five missing answers. They describe five different next actions --
#: create the thing / retry the read / start measuring / re-confirm the
#: premise / do nothing -- so collapsing any two of them into one displayed
#: word is the #366 defect this Epic exists to avoid.
MISSING_STATES: Tuple[str, ...] = (
    "missing",
    "unavailable",
    "unmeasured",
    "stale",
    "not_applicable",
)

#: A fact's availability: genuinely present, or one of the five missing
#: answers. `present` is NOT a sixth missing value -- it is the absence of one.
FACT_STATES: Tuple[str, ...] = ("present",) + MISSING_STATES

#: Membership is either determined or it could not be determined. There is no
#: third "partially determined" value: a call graph that failed tells us
#: nothing about the Nodes it would have named.
MEMBERSHIP_STATES: Tuple[str, ...] = ("resolved", "unavailable")

SUBJECT_RESOLUTIONS: Tuple[str, ...] = ("resolved", "unresolved", "unavailable")

#: §6.5 -- every evidence item carries a referencable id. The kind names the
#: canonical table the id belongs to, so a reader can always get back to the
#: row rather than to a rendered sentence.
EVIDENCE_KINDS: Tuple[str, ...] = (
    "trace",
    "anomaly",
    "drift_observation",
    "node_event",
    "code_location",
    "execution_ref",
    "stabilization_package",
)

#: §6.4's list of what is currently unresolved, as finite kinds.
OPEN_ITEM_KINDS: Tuple[str, ...] = (
    "anomaly",
    "missing_fact",
    "unmeasured_observation",
    "mode_divergence",
    "unresolved_membership",
    "stale_premise",
    #: The stored `evolution_node.maturity` disagrees with what its own event
    #: log folds to (ADR-4). Reported as its own kind, never as a generic
    #: missing fact: the value IS there, it is the reconciliation that failed.
    "maturity_drift",
)

#: How a Node came to be part of this Flow. Recorded per Node so a reader can
#: tell a persisted link from an exact structural match (§6.2).
MEMBERSHIP_BASES: Tuple[str, ...] = ("flow_link", "probe_point_exact_match")


# ---------------------------------------------------------------------------
# Pure data
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    """One referencable piece of evidence (§6.5).

    `id` is `"<kind>:<ref>"` and is derived from the canonical row's own
    identity, never from this projection's iteration order -- the #380 rule
    that a finding's id comes from its cause.
    """

    id: str
    kind: str
    ref: str
    label: str
    node_key: Optional[str] = None
    recorded_at: Optional[float] = None


@dataclass
class FlowSubject:
    subject_kind: str
    subject_ref: str
    label: str
    resolution: str
    #: `not_applicable` for a runtime Flow (it carries no snapshot), `present`
    #: for a resolved static Flow. Two different answers, not one blank.
    snapshot_state: str
    snapshot_id: Optional[int] = None
    commit_sha: Optional[str] = None
    detail: str = ""


@dataclass
class Membership:
    state: str
    node_keys: List[str] = field(default_factory=list)
    basis: Dict[str, str] = field(default_factory=dict)
    detail: str = ""


@dataclass
class NodeExplanation:
    """One Node's row. FIVE independent axes plus its observation coverage.

    Never averaged, never combined, never reduced to a score (ADR-7). Each
    axis that could not be read carries its own `*_state` from
    `MISSING_STATES` instead of a default value (#380).
    """

    node_id: int
    node_key: str
    display_name: str
    membership_basis: str

    # Axis 5 (this Epic's addition). Always present: the resolver is
    # fail-closed, so an unreadable assignment resolves to `fixed` with
    # `mode_source='default'` -- which is NOT the same as a human choosing
    # `fixed` (`mode_source='system'`, reason `system_assignment`), §4.4.
    execution_mode: str = "fixed"
    mode_source: str = "default"
    mode_reason: str = "no_assignment"
    mode_source_ref: str = ""
    mode_state: str = "present"
    mode_divergence: Optional[str] = None
    observed_mode: Optional[str] = None

    # Axis 1.
    maturity: Optional[str] = None
    maturity_state: str = "present"
    folded_maturity: Optional[str] = None
    maturity_consistent: Optional[bool] = None

    # The implementation modality (`evolution_node_implementation.modality`).
    implementation_modality: Optional[str] = None
    implementation_modality_state: str = "missing"

    # Axis 2 (#304).
    improvement_status: Optional[str] = None
    improvement_status_state: str = "missing"

    # Axis 3 (`components.mode`). `shadow` HERE and `shadow` in
    # `execution_mode` are different facts (§1.2).
    sdk_policy_mode: Optional[str] = None
    sdk_policy_mode_state: str = "missing"

    # #400 observation coverage.
    observation: Optional[Dict[str, Any]] = None
    observation_state: str = "unmeasured"
    monitoring_contract_declared: bool = False

    evidence: List[Evidence] = field(default_factory=list)
    capability_refs: List[str] = field(default_factory=list)
    purpose_element_refs: List[str] = field(default_factory=list)
    feature_refs: List[str] = field(default_factory=list)


@dataclass
class PurposeSection:
    """What this Flow supports, resolved through each member Node's links."""

    purpose_elements: List[Dict[str, Any]] = field(default_factory=list)
    capabilities: List[Dict[str, Any]] = field(default_factory=list)
    features: List[Dict[str, Any]] = field(default_factory=list)
    purpose_chain_state: str = "present"
    detail: str = ""


@dataclass
class ResponsibilitySection:
    """The Flow's responsibility, its I/O boundary and its Node dependencies.

    For a runtime Flow the dependency edges are the observed span parentage;
    for a static Flow they are the pinned snapshot's call edges. The two are
    never merged into one edge list -- they answer different questions, and
    `edge_source` says which one this is.
    """

    edge_source: str = "not_applicable"
    node_order: List[str] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    contracts: List[Dict[str, Any]] = field(default_factory=list)
    external_boundaries: List[Dict[str, Any]] = field(default_factory=list)
    entry_ref: Optional[str] = None
    entry_state: str = "missing"
    truncated: bool = False
    diagnostics: List[str] = field(default_factory=list)


@dataclass
class OpenItem:
    id: str
    kind: str
    label: str
    detail: str = ""
    node_key: Optional[str] = None
    missing_state: Optional[str] = None
    evidence_ids: List[str] = field(default_factory=list)


@dataclass
class OpenItemsSection:
    items: List[OpenItem] = field(default_factory=list)


@dataclass
class ExperimentSummary:
    proposal_id: int
    proposal_key: str
    title: str
    comparison_scope: str
    #: Derived by #415's `derive_proposal_status` when that module is
    #: importable. It is never re-implemented here: a second definition of a
    #: lifecycle is exactly the drift the event fold exists to prevent
    #: (§7.4). When the definition cannot be loaded the status reads
    #: `unavailable`, not a guessed value.
    status: Optional[str] = None
    status_state: str = "unavailable"
    target_node_keys: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    execution_refs: List[Dict[str, Any]] = field(default_factory=list)
    isolation_strategy: str = ""
    expires_at: Optional[float] = None
    created_at: Optional[float] = None


@dataclass
class ExperimentsSection:
    proposals: List[ExperimentSummary] = field(default_factory=list)
    status_source: str = "unavailable"


@dataclass
class NodeBaseline:
    node_key: str
    stable_implementation: Optional[Dict[str, Any]] = None
    stable_state: str = "missing"
    rollback_implementation: Optional[Dict[str, Any]] = None
    rollback_state: str = "missing"
    #: The human approval that pinned the baseline, as its own record --
    #: never inferred from the pin's existence (#304: the parent review and
    #: the human approval are separate facts).
    approval: Optional[Dict[str, Any]] = None
    approval_state: str = "missing"


@dataclass
class BaselineSection:
    nodes: List[NodeBaseline] = field(default_factory=list)


@dataclass
class FlowSubjectListing:
    system_id: int
    generated_at: float
    runtime_flows: List[Dict[str, Any]] = field(default_factory=list)
    static_flows: List[Dict[str, Any]] = field(default_factory=list)
    snapshot_id: Optional[int] = None
    snapshot_state: str = "missing"
    degraded_sections: List[str] = field(default_factory=list)
    degraded_detail: Dict[str, str] = field(default_factory=dict)


@dataclass
class FlowExplanation:
    system_id: int
    schema_version: str
    generated_at: float
    subject: FlowSubject
    membership: Membership
    purpose: Optional[PurposeSection] = None
    responsibility: Optional[ResponsibilitySection] = None
    nodes: Optional[List[NodeExplanation]] = None
    open_items: Optional[OpenItemsSection] = None
    experiments: Optional[ExperimentsSection] = None
    baseline: Optional[BaselineSection] = None
    #: §6.5 -- the same lineage read in both directions. Neither is a
    #: separate opinion; both are views of the links already resolved above.
    drilldown: Dict[str, Any] = field(default_factory=dict)
    rollup: List[Dict[str, Any]] = field(default_factory=list)
    degraded_sections: List[str] = field(default_factory=list)
    degraded_detail: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _degrade(
    result: FlowExplanation,
    section: str,
    exc: BaseException,
    *,
    detail_key: Optional[str] = None,
) -> None:
    """Record one section as degraded (the `overview_projection._degrade` shape).

    The guard is deliberately narrow: it drops one section's DISPLAY and never
    substitutes a guessed value for the fact the section failed to read.
    """
    if section not in result.degraded_sections:
        result.degraded_sections.append(section)
    result.degraded_detail[detail_key or section] = f"{type(exc).__name__}: {exc}"


def _evidence_id(kind: str, ref: str) -> str:
    return f"{kind}:{ref}"


def _json_or(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Subject listing
# ---------------------------------------------------------------------------


def list_flow_subjects(
    system_id: int,
    *,
    snapshot_id: Optional[int] = None,
    limit: int = 100,
    now: Optional[float] = None,
) -> FlowSubjectListing:
    """The Flow subjects this System can explain, both kinds, clearly labelled.

    The two kinds are listed separately and never merged into one list: a
    runtime Flow is an observed correlation id and a static Flow is a pinned
    snapshot's entrypoint. Collapsing them into one word is the #405 rule this
    Epic restates in §2.1.
    """
    moment = time.time() if now is None else now
    listing = FlowSubjectListing(system_id=system_id, generated_at=moment)
    bounded = max(1, min(int(limit), 500))

    with get_conn() as conn:
        try:
            rows = conn.execute(
                """SELECT flow_id,
                          COUNT(*) AS trace_count,
                          MIN(timestamp) AS first_at,
                          MAX(timestamp) AS last_at
                     FROM trace_spans
                    WHERE system_id = ?
                      AND flow_id IS NOT NULL AND flow_id != ''
                    GROUP BY flow_id
                    ORDER BY MAX(timestamp) DESC
                    LIMIT ?""",
                (system_id, bounded),
            ).fetchall()
            linked = {
                row["target_ref"]: row["n"]
                for row in conn.execute(
                    """SELECT target_ref, COUNT(*) AS n FROM evolution_node_link
                        WHERE system_id = ? AND link_kind = 'flow'
                          AND superseded_by_id IS NULL
                        GROUP BY target_ref""",
                    (system_id,),
                ).fetchall()
            }
            listing.runtime_flows = [
                {
                    "subject_kind": "runtime_flow",
                    "subject_ref": row["flow_id"],
                    "label": row["flow_id"],
                    "trace_count": row["trace_count"],
                    "first_at": row["first_at"],
                    "last_at": row["last_at"],
                    "linked_node_count": linked.get(row["flow_id"], 0),
                }
                for row in rows
            ]
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            listing.degraded_sections.append("runtime_flows")
            listing.degraded_detail["runtime_flows"] = f"{type(exc).__name__}: {exc}"

        try:
            resolved_snapshot = _resolve_snapshot(conn, system_id, snapshot_id)
            listing.snapshot_id = (
                resolved_snapshot["id"] if resolved_snapshot is not None else None
            )
            listing.snapshot_state = (
                "present" if resolved_snapshot is not None else "missing"
            )
            if resolved_snapshot is not None:
                ep_rows = conn.execute(
                    """SELECT entrypoint_id, entrypoint_type, category, label,
                              handler_path, handler_qualified_name
                         FROM code_entrypoints
                        WHERE system_id = ? AND snapshot_id = ?
                        ORDER BY entrypoint_id
                        LIMIT ?""",
                    (system_id, resolved_snapshot["id"], bounded),
                ).fetchall()
                listing.static_flows = [
                    {
                        "subject_kind": "static_flow",
                        "subject_ref": row["entrypoint_id"],
                        "label": row["label"],
                        "entrypoint_type": row["entrypoint_type"],
                        "category": row["category"],
                        "handler_path": row["handler_path"],
                        "handler_qualified_name": row["handler_qualified_name"],
                        "snapshot_id": resolved_snapshot["id"],
                    }
                    for row in ep_rows
                ]
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            listing.degraded_sections.append("static_flows")
            listing.degraded_detail["static_flows"] = f"{type(exc).__name__}: {exc}"

    return listing


def _resolve_snapshot(
    conn: sqlite3.Connection, system_id: int, snapshot_id: Optional[int]
) -> Optional[sqlite3.Row]:
    if snapshot_id is not None:
        return conn.execute(
            "SELECT * FROM repository_snapshots WHERE id = ? AND system_id = ?",
            (snapshot_id, system_id),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM repository_snapshots WHERE system_id = ? AND status = 'ready' "
        "ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# Subject resolution + membership (§6.2)
# ---------------------------------------------------------------------------


def _resolve_runtime_subject(
    conn: sqlite3.Connection, system_id: int, subject_ref: str
) -> FlowSubject:
    """A runtime Flow exists when a span was correlated under its `flow_id`.

    Same rule as `node_design._resolve_flow`: `trace_spans.flow_id` is the
    canonical identity, and it carries no snapshot -- so `snapshot_state` is
    `not_applicable` rather than a blank that reads like a failure.
    """
    row = conn.execute(
        "SELECT 1 FROM trace_spans WHERE system_id = ? AND flow_id = ? LIMIT 1",
        (system_id, subject_ref),
    ).fetchone()
    return FlowSubject(
        subject_kind="runtime_flow",
        subject_ref=subject_ref,
        label=subject_ref,
        resolution="resolved" if row is not None else "unresolved",
        snapshot_state="not_applicable",
        detail=(
            ""
            if row is not None
            else "この flow_id の span が観測されていません。"
        ),
    )


def _resolve_static_subject(
    conn: sqlite3.Connection,
    system_id: int,
    subject_ref: str,
    snapshot_id: Optional[int],
) -> Tuple[FlowSubject, Optional[sqlite3.Row], Optional[sqlite3.Row]]:
    """A static Flow is `code_entrypoints.entrypoint_id` on a PINNED snapshot.

    `snapshot_id` is required by §6.2; without a resolvable snapshot the
    subject is `unavailable` (we could not look), never `unresolved` (we
    looked and it was not there).
    """
    snapshot = _resolve_snapshot(conn, system_id, snapshot_id)
    if snapshot is None:
        return (
            FlowSubject(
                subject_kind="static_flow",
                subject_ref=subject_ref,
                label=subject_ref,
                resolution="unavailable",
                snapshot_state="missing" if snapshot_id is None else "unavailable",
                detail="Snapshot が解決できないため static flow を特定できません。",
            ),
            None,
            None,
        )
    row = conn.execute(
        """SELECT * FROM code_entrypoints
            WHERE system_id = ? AND snapshot_id = ? AND entrypoint_id = ?""",
        (system_id, snapshot["id"], subject_ref),
    ).fetchone()
    subject = FlowSubject(
        subject_kind="static_flow",
        subject_ref=subject_ref,
        label=row["label"] if row is not None else subject_ref,
        resolution="resolved" if row is not None else "unresolved",
        snapshot_state="present",
        snapshot_id=snapshot["id"],
        commit_sha=snapshot["commit_sha"],
        detail=(
            ""
            if row is not None
            else "この Snapshot に該当する entrypoint がありません。"
        ),
    )
    return subject, snapshot, row


def _runtime_membership(
    conn: sqlite3.Connection, system_id: int, flow_id: str
) -> Tuple[Membership, Dict[str, sqlite3.Row]]:
    """Membership from `evolution_node_link(link_kind='flow')` (§2.3).

    This is the existing canonical fact (#397); this Epic adds no membership
    table of its own.
    """
    rows = conn.execute(
        """SELECT n.id AS node_id, n.node_key AS node_key
             FROM evolution_node_link l
             JOIN evolution_node n ON n.id = l.node_id AND n.system_id = l.system_id
            WHERE l.system_id = ? AND l.link_kind = 'flow'
              AND l.target_ref = ? AND l.superseded_by_id IS NULL
            ORDER BY n.node_key""",
        (system_id, flow_id),
    ).fetchall()
    keys: List[str] = []
    basis: Dict[str, str] = {}
    for row in rows:
        if row["node_key"] in basis:
            continue
        keys.append(row["node_key"])
        basis[row["node_key"]] = "flow_link"
    return Membership(state="resolved", node_keys=keys, basis=basis), {}


def _static_membership(
    conn: sqlite3.Connection,
    system_id: int,
    graph_node_keys: Optional[Sequence[Tuple[str, str]]],
) -> Membership:
    """Membership by EXACT `(path, qualified_name)` equality (§6.2).

    A Node reaches the graph through its approved `probe_point` link:
    `evolution_node_link.target_ref` holds the `probe_points` row id (the same
    ref `solution_design._resolve_probe_point_target` resolves), and that
    row's `(path, symbol)` must EQUAL a flow-graph node's
    `(path, qualified_name)`. `probe_points` spells the second column
    `symbol`; it holds the qualified name the indexer produced, so the
    comparison is on the same value under two names.

    No similarity, no keyword scoring, no embeddings (Principle 6). When the
    graph could not be built at all, `graph_node_keys` is `None` and the
    membership is `unavailable` -- an empty set would claim that no Node
    belongs to this Flow, which we did not determine.
    """
    if graph_node_keys is None:
        return Membership(
            state="unavailable",
            detail="Flow graph を構築できなかったため所属 Node を判定できません。",
        )
    wanted = set(graph_node_keys)
    rows = conn.execute(
        """SELECT n.node_key AS node_key, p.path AS path, p.symbol AS symbol
             FROM evolution_node_link l
             JOIN evolution_node n ON n.id = l.node_id AND n.system_id = l.system_id
             JOIN probe_points p ON CAST(p.id AS TEXT) = l.target_ref
                                AND p.system_id = l.system_id
            WHERE l.system_id = ? AND l.link_kind = 'probe_point'
              AND l.superseded_by_id IS NULL
            ORDER BY n.node_key""",
        (system_id,),
    ).fetchall()
    keys: List[str] = []
    basis: Dict[str, str] = {}
    for row in rows:
        if (row["path"], row["symbol"]) not in wanted:
            continue
        if row["node_key"] in basis:
            continue
        keys.append(row["node_key"])
        basis[row["node_key"]] = "probe_point_exact_match"
    return Membership(state="resolved", node_keys=keys, basis=basis)


def _load_static_graph_inputs(
    conn: sqlite3.Connection, system_id: int, snapshot_id: int
):
    """Read the pinned snapshot's symbols and indexed sources.

    Read only -- the graph itself is built AFTER this connection is closed, so
    no expensive derivation ever runs while `get_conn()`'s process-wide lock
    is held (CLAUDE.md's rule).
    """
    from .flow_graph import SymbolRecord

    sym_rows = conn.execute(
        "SELECT * FROM code_symbols WHERE snapshot_id = ? AND system_id = ?",
        (snapshot_id, system_id),
    ).fetchall()
    file_rows = conn.execute(
        """SELECT path, content FROM snapshot_files
            WHERE snapshot_id = ? AND inclusion_status = 'indexed'
              AND path LIKE '%.py'
            ORDER BY path""",
        (snapshot_id,),
    ).fetchall()
    symbols = [
        SymbolRecord(
            symbol_id=r["id"],
            path=r["path"],
            qualified_name=r["qualified_name"],
            kind=r["kind"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            decorators=_json_or(r["decorators"], []),
            component_id=r["component_id"],
            route_path=r["route_path"],
            route_method=r["route_method"],
            docstring=r["docstring"],
            is_test=bool(r["is_test"]),
        )
        for r in sym_rows
    ]
    files = [
        (fr["path"], bytes(fr["content"] or b"").decode("utf-8", errors="replace"))
        for fr in file_rows
    ]
    return symbols, files


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _pointer_state(value: Optional[Any], available: Optional[bool]) -> str:
    """Map a canonical projection's `(value, availability)` pair onto a
    `FACT_STATES` member.

    `available is False` means the read itself failed, which is `unavailable`
    -- never `missing`. That distinction is the whole point of #380's rule:
    "we could not read it" must not render as "it does not exist".
    """
    if available is False:
        return "unavailable"
    return "present" if value is not None else "missing"


def _build_node_rows(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    membership: Membership,
    subject: FlowSubject,
    now: float,
) -> Tuple[List[NodeExplanation], Dict[str, Dict[str, Any]]]:
    """One row per member Node, with the five axes kept apart (§6.3).

    Every axis is READ from the module that owns it -- the mode from
    `execution_mode`, maturity / modality / improvement / policy from
    `evolution_node.build_node_projection`, the observation from
    `node_operations.build_operations_projection`. Nothing here re-derives an
    axis, and nothing here combines two of them.
    """
    from . import evolution_node, execution_mode, node_operations

    node_rows = {
        row["node_key"]: row
        for row in conn.execute(
            "SELECT id, node_key, display_name FROM evolution_node WHERE system_id = ?",
            (system_id,),
        ).fetchall()
        if row["node_key"] in set(membership.node_keys)
    }
    ordered_keys = [k for k in membership.node_keys if k in node_rows]

    mode_projection = execution_mode.build_mode_projection(
        conn, system_id=system_id, node_keys=ordered_keys, now=now
    )
    mode_by_key = {n.node_key: n for n in mode_projection.nodes}

    explanations: List[NodeExplanation] = []
    raw: Dict[str, Dict[str, Any]] = {}

    for key in ordered_keys:
        row = node_rows[key]
        node_id = row["id"]
        entry = NodeExplanation(
            node_id=node_id,
            node_key=key,
            display_name=row["display_name"] or key,
            membership_basis=membership.basis.get(key, "flow_link"),
        )

        mode_row = mode_by_key.get(key)
        if mode_row is None:
            # The Node exists but the mode projection did not name it. The
            # fail-closed default is still `fixed`, and `mode_state` says the
            # reading itself was not available rather than pretending it was.
            entry.mode_state = "unavailable"
        else:
            entry.execution_mode = mode_row.execution_mode
            entry.mode_source = mode_row.mode_source
            entry.mode_reason = mode_row.mode_reason
            entry.mode_source_ref = mode_row.source_ref
            entry.mode_divergence = mode_row.divergence
            entry.observed_mode = mode_row.observed_mode

        projection: Optional[Dict[str, Any]] = None
        try:
            projection = evolution_node.build_node_projection(
                conn, system_id=system_id, node_id=node_id
            )
        except Exception:  # noqa: BLE001 - reported per axis, never guessed
            entry.maturity_state = "unavailable"
            entry.implementation_modality_state = "unavailable"
            entry.improvement_status_state = "unavailable"
            entry.sdk_policy_mode_state = "unavailable"
        else:
            availability = projection.get("availability", {})
            entry.maturity = projection.get("maturity")
            entry.folded_maturity = projection.get("folded_maturity")
            entry.maturity_consistent = projection.get("maturity_consistent")
            implementation = projection.get("current_implementation")
            entry.implementation_modality = (
                implementation.get("modality") if implementation else None
            )
            entry.implementation_modality_state = _pointer_state(
                implementation, availability.get("implementation")
            )
            entry.improvement_status = projection.get("improvement_status")
            entry.improvement_status_state = _pointer_state(
                entry.improvement_status, availability.get("improvement_status")
            )
            entry.sdk_policy_mode = projection.get("policy_mode")
            entry.sdk_policy_mode_state = _pointer_state(
                entry.sdk_policy_mode, availability.get("policy_mode")
            )
            for link in projection.get("links", []):
                if link["link_kind"] == "capability":
                    entry.capability_refs.append(link["target_ref"])
                elif link["link_kind"] == "purpose_element":
                    entry.purpose_element_refs.append(link["target_ref"])
                elif link["link_kind"] == "feature":
                    entry.feature_refs.append(link["target_ref"])
            for event in projection.get("events", [])[:5]:
                entry.evidence.append(
                    Evidence(
                        id=_evidence_id("node_event", str(event["id"])),
                        kind="node_event",
                        ref=str(event["id"]),
                        label=f"{event['event_kind']}",
                        node_key=key,
                        recorded_at=event.get("created_at"),
                    )
                )

        operations: Optional[Dict[str, Any]] = None
        try:
            operations = node_operations.build_operations_projection(
                conn, system_id=system_id, node_id=node_id, now=now
            )
        except Exception:  # noqa: BLE001
            entry.observation_state = "unavailable"
        else:
            entry.monitoring_contract_declared = bool(
                operations.get("monitoring_contract_declared")
            )
            entry.observation = operations.get("observation")
            # No monitoring contract means nothing MEASURES this Node --
            # `unmeasured`, which is not the same as an unread measurement
            # (`unavailable`) or a Node that has none to have (`missing`).
            entry.observation_state = (
                "present" if entry.observation is not None else "unmeasured"
            )
            for observation in operations.get("observations", [])[:5]:
                entry.evidence.append(
                    Evidence(
                        id=_evidence_id("drift_observation", str(observation["id"])),
                        kind="drift_observation",
                        ref=str(observation["id"]),
                        label=f"{observation['indicator']}: {observation['observation_state']}",
                        node_key=key,
                        recorded_at=observation.get("last_observed_at"),
                    )
                )
            for anomaly in operations.get("anomalies", []):
                entry.evidence.append(
                    Evidence(
                        id=_evidence_id("anomaly", str(anomaly["id"])),
                        kind="anomaly",
                        ref=str(anomaly["id"]),
                        label=anomaly.get("summary") or anomaly.get("classification") or "",
                        node_key=key,
                        recorded_at=anomaly.get("created_at"),
                    )
                )

        raw[key] = {"projection": projection, "operations": operations}
        explanations.append(entry)

    _attach_runtime_evidence(conn, system_id, subject, explanations, raw)
    _attach_code_evidence(explanations, raw, subject)
    return explanations, raw


def _attach_runtime_evidence(
    conn: sqlite3.Connection,
    system_id: int,
    subject: FlowSubject,
    explanations: Sequence[NodeExplanation],
    raw: Mapping[str, Dict[str, Any]],
) -> None:
    """Attach the Flow's own traces as evidence, per Node (§6.5).

    Only a runtime Flow has traces of its own; a static Flow's evidence is its
    pinned source locations. The two are never substituted for one another.
    """
    if subject.subject_kind != "runtime_flow" or subject.resolution != "resolved":
        return
    component_to_nodes: Dict[str, List[str]] = {}
    for entry in explanations:
        projection = raw.get(entry.node_key, {}).get("projection") or {}
        for link in projection.get("links", []):
            if link["link_kind"] == "component":
                component_to_nodes.setdefault(link["target_ref"], []).append(
                    entry.node_key
                )
    if not component_to_nodes:
        return
    by_key = {entry.node_key: entry for entry in explanations}
    rows = conn.execute(
        """SELECT trace_id, component_id, timestamp FROM trace_spans
            WHERE system_id = ? AND flow_id = ?
            ORDER BY timestamp ASC LIMIT 200""",
        (system_id, subject.subject_ref),
    ).fetchall()
    for row in rows:
        for node_key in component_to_nodes.get(row["component_id"], []):
            entry = by_key.get(node_key)
            if entry is None or len(entry.evidence) >= 40:
                continue
            entry.evidence.append(
                Evidence(
                    id=_evidence_id("trace", row["trace_id"]),
                    kind="trace",
                    ref=row["trace_id"],
                    label=row["component_id"],
                    node_key=node_key,
                    recorded_at=row["timestamp"],
                )
            )


def _attach_code_evidence(
    explanations: Sequence[NodeExplanation],
    raw: Mapping[str, Dict[str, Any]],
    subject: FlowSubject,
) -> None:
    """Attach the pinned source location a static Flow matched on."""
    if subject.subject_kind != "static_flow":
        return
    for entry in explanations:
        projection = raw.get(entry.node_key, {}).get("projection") or {}
        implementation = projection.get("current_implementation") or {}
        commit_sha = implementation.get("commit_sha") or subject.commit_sha
        for link in projection.get("links", []):
            if link["link_kind"] != "probe_point":
                continue
            entry.evidence.append(
                Evidence(
                    id=_evidence_id("code_location", f"probe_point:{link['target_ref']}"),
                    kind="code_location",
                    ref=f"probe_point:{link['target_ref']}",
                    label=f"{commit_sha or ''}",
                    node_key=entry.node_key,
                )
            )


def _build_purpose_section(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    explanations: Sequence[NodeExplanation],
) -> PurposeSection:
    """What this Flow supports (§6.3 item 1).

    Every ref is resolved through `node_design._resolve_link_target`, which is
    the per-kind canonical resolver (`_LINK_KIND_TARGET_SOURCE`). Reusing it
    rather than re-querying keeps ONE definition of "what a Capability ref
    resolves to" -- #312's rule that a Capability's identity is
    `understanding_capability_entity.id` and never its display name.
    """
    from . import node_design, purpose_chain

    section = PurposeSection()
    elements_map: Optional[Dict[str, Dict[str, Any]]] = None
    try:
        chain = purpose_chain.derive_purpose_chain(conn, system_id)
    except Exception as exc:  # noqa: BLE001
        section.purpose_chain_state = "unavailable"
        section.detail = f"{type(exc).__name__}: {exc}"
    else:
        elements_map = {
            element.id: {
                # The element's OWN confirmation state, carried through
                # unchanged -- never merged with the link's decision_method.
                "state": element.state,
                "name": element.display_statement or element.statement,
            }
            for element in chain.elements
        }

    def _collect(kind: str, refs_of) -> List[Dict[str, Any]]:
        seen: Dict[str, Dict[str, Any]] = {}
        for entry in explanations:
            for ref in refs_of(entry):
                resolved = node_design._resolve_link_target(
                    conn,
                    system_id=system_id,
                    link_kind=kind,
                    target_ref=ref,
                    purpose_elements=elements_map,
                )
                item = seen.setdefault(
                    ref,
                    {
                        "ref": ref,
                        "label": resolved.name or ref,
                        "resolution": resolved.resolution,
                        "state": resolved.state,
                        "node_keys": [],
                    },
                )
                if entry.node_key not in item["node_keys"]:
                    item["node_keys"].append(entry.node_key)
        return [seen[ref] for ref in sorted(seen)]

    section.purpose_elements = _collect(
        "purpose_element", lambda e: e.purpose_element_refs
    )
    section.capabilities = _collect("capability", lambda e: e.capability_refs)
    section.features = _collect("feature", lambda e: e.feature_refs)
    return section


def _build_responsibility_section(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    subject: FlowSubject,
    explanations: Sequence[NodeExplanation],
    raw: Mapping[str, Dict[str, Any]],
    graph: Optional[Any],
) -> ResponsibilitySection:
    """The Flow's responsibility, I/O boundary and Node dependencies (§6.3 item 2).

    `edge_source` names WHICH dependency reading this is. A runtime Flow's
    edges are observed span parentage; a static Flow's edges are the pinned
    snapshot's call graph. They answer different questions and are never
    merged into one list.
    """
    section = ResponsibilitySection()

    for entry in explanations:
        projection = raw.get(entry.node_key, {}).get("projection") or {}
        version = projection.get("current_version")
        if version is None:
            section.contracts.append(
                {
                    "node_key": entry.node_key,
                    "state": _pointer_state(
                        None, (projection.get("availability") or {}).get("version")
                    ),
                }
            )
            continue
        section.contracts.append(
            {
                "node_key": entry.node_key,
                "state": "present",
                "mission": version.get("mission", ""),
                "scope": version.get("scope", ""),
                "out_of_scope": version.get("out_of_scope", ""),
                "input_contract": version.get("input_contract", {}),
                "output_contract": version.get("output_contract", {}),
                "side_effect_class": version.get("side_effect_class"),
                "trust_boundary": version.get("trust_boundary"),
            }
        )

    if subject.subject_kind == "runtime_flow":
        section.edge_source = "runtime_span_parentage"
        rows = conn.execute(
            """SELECT trace_id, component_id, span_id, parent_span_id, timestamp
                 FROM trace_spans
                WHERE system_id = ? AND flow_id = ?
                ORDER BY timestamp ASC LIMIT 500""",
            (system_id, subject.subject_ref),
        ).fetchall()
        by_span = {row["span_id"]: row for row in rows if row["span_id"]}
        order: List[str] = []
        for row in rows:
            if row["component_id"] not in order:
                order.append(row["component_id"])
        section.node_order = order
        section.entry_ref = order[0] if order else None
        section.entry_state = "present" if order else "missing"
        for row in rows:
            parent = by_span.get(row["parent_span_id"]) if row["parent_span_id"] else None
            if parent is None:
                continue
            edge = {
                "source": parent["component_id"],
                "target": row["component_id"],
                "edge_kind": "span_parent",
                "trace_id": row["trace_id"],
            }
            if edge not in section.edges:
                section.edges.append(edge)
        return section

    if graph is None:
        # The call graph is the ONLY source of a static Flow's dependencies.
        # `not_applicable` would be a lie (it IS applicable, we could not
        # build it), so this reports the read failure honestly.
        section.edge_source = "unavailable"
        section.entry_state = "unavailable"
        return section

    section.edge_source = "static_call_graph"
    section.node_order = [node.node_id for node in graph.nodes]
    section.entry_ref = graph.nodes[0].node_id if graph.nodes else None
    section.entry_state = "present" if graph.nodes else "missing"
    section.truncated = bool(graph.truncated)
    section.diagnostics = list(graph.diagnostics)
    section.edges = [
        {
            "source": edge.source_node_id,
            "target": edge.target_node_id,
            "edge_kind": edge.edge_type,
            "resolution": edge.resolution,
            "callee_name": edge.callee_name,
            "line": edge.line,
        }
        for edge in graph.edges
    ]
    section.external_boundaries = [
        {
            "node_id": node.node_id,
            "boundary_kind": node.boundary_kind,
            "qualified_name": node.qualified_name,
        }
        for node in graph.nodes
        if node.is_external
    ]
    return section


def _build_open_items(
    *,
    subject: FlowSubject,
    membership: Membership,
    explanations: Sequence[NodeExplanation],
    raw: Mapping[str, Dict[str, Any]],
    snapshot_stale: Optional[bool],
) -> OpenItemsSection:
    """Everything currently unresolved (§6.3 item 4 / §6.4).

    Each item carries its own `missing_state` from the five-value vocabulary,
    so 「まだ無い」「読めなかった」「測っていない」「前提が動いた」「構造上不要」
    stay five different answers on the screen as well as in the data.
    """
    section = OpenItemsSection()

    if membership.state == "unavailable":
        section.items.append(
            OpenItem(
                id="membership:unavailable",
                kind="unresolved_membership",
                label="所属 Node を判定できません",
                detail=membership.detail,
                missing_state="unavailable",
            )
        )

    if snapshot_stale:
        section.items.append(
            OpenItem(
                id=f"snapshot:{subject.snapshot_id}",
                kind="stale_premise",
                label="この説明は最新ではない Snapshot に固定されています",
                detail="より新しい ready Snapshot が存在します。",
                missing_state="stale",
            )
        )

    for entry in explanations:
        operations = raw.get(entry.node_key, {}).get("operations") or {}
        for anomaly in operations.get("anomalies", []):
            section.items.append(
                OpenItem(
                    id=f"anomaly:{anomaly['id']}",
                    kind="anomaly",
                    label=anomaly.get("summary") or anomaly.get("classification") or "",
                    detail=f"severity={anomaly.get('severity')} status={anomaly.get('status')}",
                    node_key=entry.node_key,
                    evidence_ids=[_evidence_id("anomaly", str(anomaly["id"]))],
                )
            )

        for field_name, state in (
            ("implementation_modality", entry.implementation_modality_state),
            ("improvement_status", entry.improvement_status_state),
            ("sdk_policy_mode", entry.sdk_policy_mode_state),
            ("maturity", entry.maturity_state),
            ("execution_mode", entry.mode_state),
        ):
            if state == "present":
                continue
            section.items.append(
                OpenItem(
                    id=f"fact:{entry.node_key}:{field_name}",
                    kind="missing_fact",
                    label=f"{entry.node_key}: {field_name}",
                    node_key=entry.node_key,
                    missing_state=state,
                )
            )

        if entry.observation_state != "present":
            section.items.append(
                OpenItem(
                    id=f"observation:{entry.node_key}",
                    kind="unmeasured_observation",
                    label=f"{entry.node_key}: 観測カバレッジ",
                    detail=(
                        "監視契約が宣言されていません。"
                        if entry.observation_state == "unmeasured"
                        else ""
                    ),
                    node_key=entry.node_key,
                    missing_state=entry.observation_state,
                )
            )

        if entry.mode_divergence in ("divergent", "stale", "unobserved"):
            section.items.append(
                OpenItem(
                    id=f"divergence:{entry.node_key}",
                    kind="mode_divergence",
                    label=f"{entry.node_key}: {entry.mode_divergence}",
                    detail=(
                        f"設定={entry.execution_mode} 観測={entry.observed_mode}"
                    ),
                    node_key=entry.node_key,
                    missing_state=(
                        "unmeasured" if entry.mode_divergence == "unobserved" else "stale"
                    ),
                )
            )

        if entry.maturity_consistent is False:
            section.items.append(
                OpenItem(
                    id=f"maturity_drift:{entry.node_key}",
                    kind="maturity_drift",
                    label=f"{entry.node_key}: maturity が event log と一致しません",
                    detail=f"stored={entry.maturity} folded={entry.folded_maturity}",
                    node_key=entry.node_key,
                    missing_state="stale",
                )
            )

    return section


def _build_experiments_section(
    conn: sqlite3.Connection, *, system_id: int, subject: FlowSubject
) -> ExperimentsSection:
    """Proposed / approved / running experiments for this Flow (§6.3 item 5).

    #415 owns `app/flow_orchestration.py` and its `derive_proposal_status`
    event fold (§7.4). This read never re-implements that fold: a second
    definition of a lifecycle is exactly the drift the fold exists to
    prevent. When the module is not importable the status reads `unavailable`
    for every proposal and `status_source` says so -- an honest "we could not
    read the lifecycle definition", not a guessed status.
    """
    section = ExperimentsSection()

    derive = None
    try:  # pragma: no cover - exercised only once #415 has landed
        from . import flow_orchestration  # type: ignore[attr-defined]

        derive = getattr(flow_orchestration, "derive_proposal_status", None)
    except Exception:  # noqa: BLE001 - an unfinished sibling module is not our failure
        derive = None
    section.status_source = "flow_orchestration" if derive is not None else "unavailable"

    rows = conn.execute(
        """SELECT * FROM flow_experiment_proposal
            WHERE system_id = ? AND flow_subject_kind = ? AND flow_subject_ref = ?
            ORDER BY id DESC LIMIT 100""",
        (system_id, subject.subject_kind, subject.subject_ref),
    ).fetchall()

    for row in rows:
        summary = ExperimentSummary(
            proposal_id=row["id"],
            proposal_key=row["proposal_key"],
            title=row["title"],
            comparison_scope=row["comparison_scope"],
            evidence_refs=_json_or(row["evidence_refs_json"], []),
            isolation_strategy=row["isolation_strategy"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )
        summary.target_node_keys = [
            target["target_node_key"]
            for target in conn.execute(
                """SELECT target_node_key FROM flow_experiment_target
                    WHERE proposal_id = ? AND system_id = ?
                    ORDER BY position, id""",
                (row["id"], system_id),
            ).fetchall()
        ]
        summary.execution_refs = [
            {
                "execution_kind": ref["execution_kind"],
                "execution_ref": ref["execution_ref"],
                "recorded_at": ref["recorded_at"],
            }
            for ref in conn.execute(
                """SELECT execution_kind, execution_ref, recorded_at
                     FROM flow_experiment_execution_ref
                    WHERE proposal_id = ? AND system_id = ?
                    ORDER BY id""",
                (row["id"], system_id),
            ).fetchall()
        ]
        if derive is not None:
            events = [
                {
                    "id": event["id"],
                    "event_kind": event["event_kind"],
                    "created_at": event["created_at"],
                }
                for event in conn.execute(
                    """SELECT id, event_kind, created_at FROM flow_experiment_event
                        WHERE proposal_id = ? AND system_id = ? ORDER BY id""",
                    (row["id"], system_id),
                ).fetchall()
            ]
            try:  # pragma: no cover - exercised only once #415 has landed
                summary.status = derive(events)
                summary.status_state = "present"
            except Exception:  # noqa: BLE001
                summary.status = None
                summary.status_state = "unavailable"
        section.proposals.append(summary)

    return section


def _build_baseline_section(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    explanations: Sequence[NodeExplanation],
    raw: Mapping[str, Dict[str, Any]],
) -> BaselineSection:
    """The pinned baseline, the rollback target and the human approval (§6.3 item 6).

    The approval is read as its OWN record rather than inferred from the pin's
    existence: #304 keeps the parent review and the human approval as separate
    facts, and a pin with no readable approval row must say so.
    """
    section = BaselineSection()
    for entry in explanations:
        projection = raw.get(entry.node_key, {}).get("projection") or {}
        availability = projection.get("availability") or {}
        stable = projection.get("stable_implementation")
        rollback = projection.get("rollback_implementation")
        baseline = NodeBaseline(
            node_key=entry.node_key,
            stable_implementation=stable,
            stable_state=_pointer_state(stable, availability.get("stable_implementation")),
            rollback_implementation=rollback,
            rollback_state=_pointer_state(
                rollback, availability.get("rollback_implementation")
            ),
        )
        try:
            row = conn.execute(
                """SELECT id, status, approved_by, approved_at, decision_note,
                          decision_method, parent_review_disposition,
                          parent_reviewed_by, parent_reviewed_at
                     FROM stabilization_package
                    WHERE system_id = ? AND node_id = ? AND status = 'approved'
                    ORDER BY id DESC LIMIT 1""",
                (system_id, entry.node_id),
            ).fetchone()
        except sqlite3.Error:
            baseline.approval_state = "unavailable"
        else:
            if row is None:
                baseline.approval_state = "missing"
            else:
                baseline.approval_state = "present"
                baseline.approval = {
                    "package_id": row["id"],
                    "status": row["status"],
                    "approved_by": row["approved_by"],
                    "approved_at": row["approved_at"],
                    "decision_note": row["decision_note"],
                    "decision_method": row["decision_method"],
                    "parent_review_disposition": row["parent_review_disposition"],
                    "parent_reviewed_by": row["parent_reviewed_by"],
                    "parent_reviewed_at": row["parent_reviewed_at"],
                }
        section.nodes.append(baseline)
    return section


# ---------------------------------------------------------------------------
# §6.5 -- drill-down and the reverse aggregation
# ---------------------------------------------------------------------------


def _build_lineage(
    *,
    subject: FlowSubject,
    membership: Membership,
    explanations: Sequence[NodeExplanation],
    purpose: Optional[PurposeSection],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Purpose -> Capability -> Flow -> Node -> evidence, and back again.

    Both directions are views of the SAME already-resolved links; neither is a
    second opinion, and neither invents a relation. A Purpose element's
    Capabilities are the Capabilities linked on the Nodes that element is also
    linked on -- an observed co-membership, stated as such, never a semantic
    guess about which Capability "belongs to" which Purpose.
    """
    by_node_purpose: Dict[str, List[str]] = {
        entry.node_key: list(entry.purpose_element_refs) for entry in explanations
    }
    by_node_capability: Dict[str, List[str]] = {
        entry.node_key: list(entry.capability_refs) for entry in explanations
    }

    purpose_level: List[Dict[str, Any]] = []
    capability_level: List[Dict[str, Any]] = []
    if purpose is not None:
        for element in purpose.purpose_elements:
            capability_refs: List[str] = []
            for node_key in element["node_keys"]:
                for ref in by_node_capability.get(node_key, []):
                    if ref not in capability_refs:
                        capability_refs.append(ref)
            purpose_level.append(
                {
                    "ref": element["ref"],
                    "label": element["label"],
                    "capability_refs": capability_refs,
                    "node_keys": list(element["node_keys"]),
                }
            )
        capability_level = [
            {
                "ref": capability["ref"],
                "label": capability["label"],
                "node_keys": list(capability["node_keys"]),
            }
            for capability in purpose.capabilities
        ]

    drilldown = {
        "purpose_elements": purpose_level,
        "capabilities": capability_level,
        "flow": {
            "subject_kind": subject.subject_kind,
            "subject_ref": subject.subject_ref,
            "membership_state": membership.state,
            "node_keys": list(membership.node_keys),
        },
        "nodes": [
            {
                "node_key": entry.node_key,
                "evidence_ids": [item.id for item in entry.evidence],
            }
            for entry in explanations
        ],
    }

    rollup: List[Dict[str, Any]] = []
    for entry in explanations:
        for item in entry.evidence:
            rollup.append(
                {
                    "evidence_id": item.id,
                    "evidence_kind": item.kind,
                    "node_key": entry.node_key,
                    "flow": {
                        "subject_kind": subject.subject_kind,
                        "subject_ref": subject.subject_ref,
                    },
                    "capability_refs": by_node_capability.get(entry.node_key, []),
                    "purpose_element_refs": by_node_purpose.get(entry.node_key, []),
                }
            )
    return drilldown, rollup


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------


def build_flow_explanation(
    system_id: int,
    *,
    subject_kind: str,
    subject_ref: str,
    snapshot_id: Optional[int] = None,
    now: Optional[float] = None,
) -> FlowExplanation:
    """Explain one Flow. READ ONLY -- this function writes nothing.

    The body is in three phases for one reason: the static Flow's call graph
    is an expensive derivation over the pinned snapshot, and CLAUDE.md forbids
    holding `get_conn()`'s process-wide lock across it. So phase 1 READS the
    inputs and closes the connection, phase 2 builds the graph with no
    connection open, and phase 3 reopens one for the sections. Nothing in any
    phase writes, and `interview_workflow.evaluate_session_workflow` (which
    persists a checkpoint) is deliberately never called.

    Each of §6.3's six sections is guarded on its own: a failure lands in
    `degraded_sections`, its section value becomes `None`, and no substitute
    value is invented for what it could not read.
    """
    moment = time.time() if now is None else now
    if subject_kind not in FLOW_SUBJECT_KINDS:
        raise ValueError(
            f"subject_kind must be one of {list(FLOW_SUBJECT_KINDS)}, got {subject_kind!r}"
        )

    # --- phase 1: resolve the subject and read the graph inputs ------------
    graph_inputs = None
    snapshot_row = None
    entrypoint_row = None
    latest_ready_id: Optional[int] = None
    with get_conn() as conn:
        if subject_kind == "runtime_flow":
            subject = _resolve_runtime_subject(conn, system_id, subject_ref)
        else:
            subject, snapshot_row, entrypoint_row = _resolve_static_subject(
                conn, system_id, subject_ref, snapshot_id
            )
            latest = _resolve_snapshot(conn, system_id, None)
            latest_ready_id = latest["id"] if latest is not None else None
            if snapshot_row is not None and entrypoint_row is not None:
                try:
                    graph_inputs = _load_static_graph_inputs(
                        conn, system_id, snapshot_row["id"]
                    )
                except sqlite3.Error:
                    graph_inputs = None

    result = FlowExplanation(
        system_id=system_id,
        schema_version=FLOW_EXPLANATION_SCHEMA_VERSION,
        generated_at=moment,
        subject=subject,
        membership=Membership(state="resolved"),
    )

    # --- phase 2: build the call graph with NO connection held -------------
    graph = None
    graph_node_keys: Optional[List[Tuple[str, str]]] = None
    if subject_kind == "static_flow":
        if graph_inputs is None or entrypoint_row is None or snapshot_row is None:
            graph_node_keys = None
        else:
            symbols, files = graph_inputs
            try:
                from .entrypoint_discovery import discover_entrypoints
                from .flow_graph import build_flow_graph

                discovery = discover_entrypoints(symbols, files)
                graph = build_flow_graph(
                    symbols=symbols,
                    files=files,
                    snapshot_id=snapshot_row["id"],
                    commit_sha=snapshot_row["commit_sha"],
                    entrypoint_type=entrypoint_row["entrypoint_type"],
                    entrypoint_id=entrypoint_row["entrypoint_id"],
                    entrypoints=discovery.entrypoints + discovery.functions,
                )
            except Exception as exc:  # noqa: BLE001 - membership becomes unavailable
                graph = None
                result.degraded_detail["membership"] = f"{type(exc).__name__}: {exc}"
            if graph is not None:
                graph_node_keys = [
                    (node.path, node.qualified_name)
                    for node in graph.nodes
                    if not node.is_external
                ]

    # --- phase 3: the sections ---------------------------------------------
    with get_conn() as conn:
        if subject_kind == "runtime_flow":
            try:
                result.membership, _ = _runtime_membership(conn, system_id, subject_ref)
            except sqlite3.Error as exc:
                result.membership = Membership(
                    state="unavailable", detail=f"{type(exc).__name__}: {exc}"
                )
        else:
            try:
                result.membership = _static_membership(conn, system_id, graph_node_keys)
            except sqlite3.Error as exc:
                result.membership = Membership(
                    state="unavailable", detail=f"{type(exc).__name__}: {exc}"
                )

        explanations: List[NodeExplanation] = []
        raw: Dict[str, Dict[str, Any]] = {}
        try:
            explanations, raw = _build_node_rows(
                conn,
                system_id=system_id,
                membership=result.membership,
                subject=subject,
                now=moment,
            )
            result.nodes = explanations
        except Exception as exc:  # noqa: BLE001
            _degrade(result, "nodes", exc)

        try:
            result.purpose = _build_purpose_section(
                conn, system_id=system_id, explanations=explanations
            )
        except Exception as exc:  # noqa: BLE001
            _degrade(result, "purpose", exc)

        try:
            result.responsibility = _build_responsibility_section(
                conn,
                system_id=system_id,
                subject=subject,
                explanations=explanations,
                raw=raw,
                graph=graph,
            )
        except Exception as exc:  # noqa: BLE001
            _degrade(result, "responsibility", exc)

        snapshot_stale: Optional[bool] = None
        if subject.subject_kind == "static_flow" and subject.snapshot_id is not None:
            snapshot_stale = (
                latest_ready_id is not None and latest_ready_id != subject.snapshot_id
            )
        try:
            result.open_items = _build_open_items(
                subject=subject,
                membership=result.membership,
                explanations=explanations,
                raw=raw,
                snapshot_stale=snapshot_stale,
            )
        except Exception as exc:  # noqa: BLE001
            _degrade(result, "open_items", exc)

        try:
            result.experiments = _build_experiments_section(
                conn, system_id=system_id, subject=subject
            )
        except Exception as exc:  # noqa: BLE001
            _degrade(result, "experiments", exc)

        try:
            result.baseline = _build_baseline_section(
                conn, system_id=system_id, explanations=explanations, raw=raw
            )
        except Exception as exc:  # noqa: BLE001
            _degrade(result, "baseline", exc)

    try:
        result.drilldown, result.rollup = _build_lineage(
            subject=subject,
            membership=result.membership,
            explanations=explanations,
            purpose=result.purpose,
        )
    except Exception as exc:  # noqa: BLE001
        _degrade(result, "purpose", exc, detail_key="purpose.lineage")

    return result
