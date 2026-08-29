"""Objective Map / Gap Workbench / Overview `objective` section projection
(Issue #432, Epic #427).

`docs/product-objective-lineage.md` §9 (plus §0, §5.4, §5.10, §6) is the
canonical contract this module implements. Three rules carried over from
`app/overview_projection.py` verbatim, because this is the same kind of
composed, read-only projection one layer over the Product Objective domain:

* **Every section is its own guarded loader.** A failure records the
  section in `degraded_sections` / `degraded_detail` and drops that
  section's DISPLAY -- it never substitutes `0` / `None` / `False` / a
  guessed state for a fact it could not read (`overview_projection.
  NextActionFacts`'s `*_available` flags are the pattern this mirrors).
* **It writes nothing.** No view marker, no "last seen", no checkpoint.
  Opening a projection is not a human decision (#380/#382).
* **It re-derives nothing.** Every state -- `objective_state`,
  `design_status`, `achievement`, `assessability`, `lifecycle`,
  `priority_band`, `recheck_state`, a Gap source's `source_state` /
  `deep_link` -- is read straight off `app/product_objective.py`'s public
  functions (`list_objectives` / `list_milestones` / `list_gaps` /
  `get_objective_summary` / `get_milestone_summary` / `get_gap_summary` /
  `get_gap_detail`) and `app/product_gap_sources.py`'s `resolve_source`
  (reached only indirectly, through `product_objective.get_gap_detail`'s
  own lazy import -- this module never imports `product_gap_sources`
  itself, the same "don't trust a sibling module's own promise not to
  raise" discipline `product_objective.py`'s module docstring documents).
  Nothing here re-implements a fold, a decision-ledger read, or a
  source-kind branch.

Counts are allowed everywhere in this module; RANKING BY COUNT is not
(§5.7/§0 invariant 7). The one place this module orders anything --
`_gap_sort_key`, used by both the Gap Workbench's `entries` list and (for
picking `primary_gap`) the Overview `objective` section -- is a finite,
totally-ordered ladder over `priority_band` -> `lifecycle` ->
`milestone.sequence_hint` -> `gap_key`. Every gate is a lookup into a fixed
table or a developer-supplied value; none of it is a computed score, so the
same underlying facts always produce the same order.

§9.3's `next_step` first-match table is under-specified past the row
conditions themselves on exactly which Gaps/Milestones each row's "その
Objective" / "その Milestone" refers to once more than one exists. This
module's reading (documented row by row on `_decide_next_step` below) is:
every row from #6 onward is scoped to the single `active_objective` picked
in `_pick_active_objective`, and rows #10/#14's "そのMilestone" is the
`next_milestone` picked by the exact §9.1 rule (`design_status='confirmed'`
and `achievement='unassessed'`, `sequence_hint` ascending). Flagged in the
implementing report as a documented assumption, not a settled reading.

probe-agent:
  role: Read-only Objective Map / Gap Workbench / Overview-section projection
  capability: product-objective-lineage
  element_type: boundary
  consumers: [control-server, dashboard]
  operation_kind: read
  state_effects: [database-read]
  probe_value: Verify every section degrades alone on a read failure, that Gap ordering never changes when only a count changes, that node_anomaly always reports deep_link_state='unavailable', that a System with no Objective yields next_step='create_objective' without being marked degraded, and that no function in this module performs a write.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import product_objective

__all__ = [
    "ObjectiveOverviewResult",
    "build_objective_map",
    "build_gap_workbench",
    "build_objective_overview",
]


# --- Shared guard helper (mirrors `product_objective._degrade` /
#     `overview_projection._degrade` one layer over) --------------------------


def _degrade(degraded_sections: List[str], degraded_detail: Dict[str, str], section: str, exc: Exception) -> None:
    if section not in degraded_sections:
        degraded_sections.append(section)
    degraded_detail[section] = f"{type(exc).__name__}: {exc}"


def _merge_child_result(
    degraded_sections: List[str], degraded_detail: Dict[str, str], prefix: str, child: Dict[str, Any]
) -> None:
    """Fold a nested `product_objective.py` result's own `degraded_sections`
    / `degraded_detail` into this projection's, namespaced by `prefix` so
    two different Milestones' `"gaps"` failures never collide under one
    key."""
    for sec in child.get("degraded_sections", []) or []:
        key = f"{prefix}:{sec}"
        if key not in degraded_sections:
            degraded_sections.append(key)
    for sec, detail in (child.get("degraded_detail", {}) or {}).items():
        degraded_detail[f"{prefix}:{sec}"] = detail


# --- §9.1/§9.2: the finite Gap ordering ladder -------------------------------
#
# `priority_band` (now > next > watch > unset) -> `lifecycle` (open >
# acknowledged > deferred > every terminal value, §5.6's own listed order
# extended by index) -> `milestone.sequence_hint` ascending -> `gap_key`
# ascending. Every gate is a lookup into one of these two fixed tables or a
# developer-supplied value -- never a computed score (§0 invariant 7/§5.7).

_PRIORITY_BAND_RANK: Dict[str, int] = {"now": 0, "next": 1, "watch": 2, "unset": 3}
_GAP_LIFECYCLE_RANK: Dict[str, int] = {
    "open": 0,
    "acknowledged": 1,
    "deferred": 2,
    "resolved": 3,
    "rejected": 4,
    "obsolete": 5,
}


def _gap_sort_key(gap: Dict[str, Any], milestone_sequence_hint: int) -> Tuple[int, int, int, str]:
    return (
        _PRIORITY_BAND_RANK.get(gap["priority_band"], len(_PRIORITY_BAND_RANK)),
        _GAP_LIFECYCLE_RANK.get(gap["lifecycle"], len(_GAP_LIFECYCLE_RANK)),
        milestone_sequence_hint,
        gap["gap_key"],
    )


def _milestone_sequence_hint(conn: sqlite3.Connection, milestone_id: int) -> int:
    """`sequence_hint` lives on the Milestone's current revision, not on the
    `_milestone_out_dict` summary `product_objective.py` returns (§8
    deliberately excludes it from the content digest, but the field itself
    is still display/order-only data this module reads directly -- a plain
    read, never a derivation `product_objective.py` owns)."""
    row = conn.execute(
        """SELECT pmr.sequence_hint AS sequence_hint
           FROM product_milestone pm
           JOIN product_milestone_revision pmr ON pmr.id = pm.current_revision_id
           WHERE pm.id = ?""",
        (milestone_id,),
    ).fetchone()
    return row["sequence_hint"] if row is not None else 0


# ==============================================================================
# §9.1 / §9.5: Objective Map
# ==============================================================================


def _empty_gap_summary() -> Dict[str, int]:
    return {
        "open_count": 0,
        "acknowledged_count": 0,
        "deferred_count": 0,
        "resolved_count": 0,
        "rejected_count": 0,
        "obsolete_count": 0,
        "recheck_required_count": 0,
        "reopen_candidate_count": 0,
        "close_candidate_count": 0,
    }


_LIFECYCLE_COUNT_FIELD: Dict[str, str] = {
    "open": "open_count",
    "acknowledged": "acknowledged_count",
    "deferred": "deferred_count",
    "resolved": "resolved_count",
    "rejected": "rejected_count",
    "obsolete": "obsolete_count",
}


def _summarize_gaps(gaps: List[Dict[str, Any]]) -> Dict[str, int]:
    """Counts only (§5.7/§0 invariant 7) -- the Objective Map may SHOW these
    numbers, but nothing in this module ever sorts or picks by them."""
    summary = _empty_gap_summary()
    for gap in gaps:
        field_name = _LIFECYCLE_COUNT_FIELD.get(gap["lifecycle"])
        if field_name is not None:
            summary[field_name] += 1
        flags = gap.get("read_flags") or []
        if "recheck_required" in flags:
            summary["recheck_required_count"] += 1
        if "reopen_candidate" in flags:
            summary["reopen_candidate_count"] += 1
        if "close_candidate" in flags:
            summary["close_candidate_count"] += 1
    return summary


def build_objective_map(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    """§9.1/§9.5: the whole Objective hierarchy plus, per Milestone, a Gap
    lifecycle/flag COUNT summary (never a Gap list -- ordering by the §9.1
    ladder is exercised by `build_gap_workbench`'s `entries` list, the one
    place a full ordered Gap list is actually returned).

    A `parent_objective_parent_link` tombstone row (`parent_objective_id IS
    NULL`, contract §4.4) already reads as "no current parent" through
    `product_objective._objective_parent_link` / `_objective_out_dict` --
    this function never re-derives that read, so the tombstone case needs no
    special handling here."""
    now = time.time()
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    objectives: List[Dict[str, Any]] = []
    try:
        obj_result = product_objective.list_objectives(conn, system_id)
        _merge_child_result(degraded_sections, degraded_detail, "objectives", obj_result)
        objectives = obj_result["objectives"]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "objectives", exc)

    children_by_parent: Dict[Optional[int], List[int]] = {}
    for objective in objectives:
        children_by_parent.setdefault(objective["parent_objective_id"], []).append(objective["id"])

    nodes: List[Dict[str, Any]] = []
    for objective in objectives:
        objective_key = objective["objective_key"]
        milestones_out: List[Dict[str, Any]] = []
        try:
            ms_result = product_objective.list_milestones(conn, system_id, objective_key)
            _merge_child_result(degraded_sections, degraded_detail, f"milestones:{objective_key}", ms_result)
            for milestone in ms_result["milestones"]:
                milestone_key = milestone["milestone_key"]
                sequence_hint = _milestone_sequence_hint(conn, milestone["id"])
                try:
                    gap_result = product_objective.list_gaps(conn, system_id, milestone_key)
                    _merge_child_result(
                        degraded_sections, degraded_detail, f"gaps:{milestone_key}", gap_result
                    )
                    gap_summary = _summarize_gaps(gap_result["gaps"])
                except Exception as exc:  # pragma: no cover - defensive
                    _degrade(degraded_sections, degraded_detail, f"gaps:{milestone_key}", exc)
                    gap_summary = _empty_gap_summary()
                milestones_out.append(
                    {
                        "id": milestone["id"],
                        "milestone_key": milestone_key,
                        "title": milestone["title"],
                        "design_status": milestone["design_status"],
                        "achievement": milestone["achievement"],
                        "assessability": milestone["assessability"],
                        "recheck_state": milestone["recheck_state"],
                        "sequence_hint": sequence_hint,
                        "gap_summary": gap_summary,
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(degraded_sections, degraded_detail, f"milestones:{objective_key}", exc)

        # Display order within one Objective: sequence_hint (a developer
        # ordering value, §4.5's docstring on the revision column), ties
        # broken by the developer-supplied key -- never a count.
        milestones_out.sort(key=lambda m: (m["sequence_hint"], m["milestone_key"]))

        nodes.append(
            {
                "id": objective["id"],
                "objective_key": objective_key,
                "title": objective["title"],
                "objective_state": objective["objective_state"],
                "recheck_state": objective["recheck_state"],
                "parent_objective_id": objective["parent_objective_id"],
                "parent_objective_key": objective["parent_objective_key"],
                "child_objective_ids": children_by_parent.get(objective["id"], []),
                "milestones": milestones_out,
            }
        )

    root_objective_ids = children_by_parent.get(None, [])

    return {
        "system_id": system_id,
        "generated_at": now,
        "nodes": nodes,
        "root_objective_ids": root_objective_ids,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


# ==============================================================================
# §9.2: Gap Workbench
# ==============================================================================


def _deep_links_for_gap(
    conn: sqlite3.Connection,
    system_id: int,
    gap_key: str,
    degraded_sections: List[str],
    degraded_detail: Dict[str, str],
    shared_sources: Dict[Tuple[str, str], Dict[str, List[Any]]],
    source_kind_gap_counts: Dict[str, int],
    gap_id: int,
) -> List[Dict[str, Any]]:
    """One Gap's current source refs, resolved through
    `product_objective.get_gap_detail` (which itself lazily reaches
    `product_gap_sources.resolve_source`, §5.10). Also folds this Gap into
    the two cross-Gap views §9.2 asks for: `shared_sources` (§5.2's
    many-to-many read the OTHER way -- which Gaps reference the SAME
    detector) and `source_kind_gap_counts` (§9.2's per-kind breakdown,
    counted once per Gap even if it holds more than one ref of the same
    kind -- a COUNT for display, never a ranking signal, §0 invariant 7)."""
    try:
        detail = product_objective.get_gap_detail(conn, system_id, gap_key)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, f"source_refs:{gap_key}", exc)
        return []

    _merge_child_result(degraded_sections, degraded_detail, f"source_refs:{gap_key}", detail)

    deep_links: List[Dict[str, Any]] = []
    kinds_seen = set()
    for source_ref in detail.get("source_refs", []) or []:
        kind = source_ref["source_kind"]
        ref = source_ref["source_ref"]
        kinds_seen.add(kind)
        bucket = shared_sources.setdefault((kind, ref), {"gap_ids": [], "gap_keys": []})
        bucket["gap_ids"].append(gap_id)
        bucket["gap_keys"].append(gap_key)
        deep_links.append(
            {
                "source_kind": kind,
                "source_ref": ref,
                "deep_link_state": source_ref["deep_link_state"],
                "route": source_ref["deep_link"],
            }
        )
    for kind in kinds_seen:
        source_kind_gap_counts[kind] = source_kind_gap_counts.get(kind, 0) + 1
    return deep_links


def build_gap_workbench(conn: sqlite3.Connection, system_id: int) -> Dict[str, Any]:
    """§9.2: every Gap in the System, ordered by the §9.1 ladder, with its
    resolved deep links plus the System-wide `source_kind` breakdown and the
    §5.2 "who else references this detector" view.

    Confirming / associating / deferring / resolving / reopening /
    prioritizing are `product_gaps.py` write endpoints the Dashboard offers
    FROM this projection -- nothing here executes any of them (§9.2 non-goal:
    selecting a Gap never auto-runs an action)."""
    now = time.time()
    degraded_sections: List[str] = []
    degraded_detail: Dict[str, str] = {}

    gaps: List[Dict[str, Any]] = []
    try:
        gap_result = product_objective.list_gaps(conn, system_id)
        _merge_child_result(degraded_sections, degraded_detail, "gaps", gap_result)
        gaps = gap_result["gaps"]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(degraded_sections, degraded_detail, "gaps", exc)

    sequence_hint_cache: Dict[int, int] = {}
    shared_sources: Dict[Tuple[str, str], Dict[str, List[Any]]] = {}
    source_kind_gap_counts: Dict[str, int] = {}

    scored: List[Tuple[Tuple[int, int, int, str], Dict[str, Any]]] = []
    for gap in gaps:
        milestone_id = gap["milestone_id"]
        if milestone_id not in sequence_hint_cache:
            sequence_hint_cache[milestone_id] = _milestone_sequence_hint(conn, milestone_id)
        sequence_hint = sequence_hint_cache[milestone_id]

        deep_links = _deep_links_for_gap(
            conn, system_id, gap["gap_key"], degraded_sections, degraded_detail,
            shared_sources, source_kind_gap_counts, gap["id"],
        )

        entry = {
            "id": gap["id"],
            "gap_key": gap["gap_key"],
            "milestone_id": gap["milestone_id"],
            "milestone_key": gap["milestone_key"],
            "objective_id": gap["objective_id"],
            "objective_key": gap["objective_key"],
            "title": gap["title"],
            "lifecycle": gap["lifecycle"],
            "priority_band": gap["priority_band"],
            "recheck_state": gap["recheck_state"],
            "read_flags": gap["read_flags"],
            "deep_links": deep_links,
        }
        scored.append((_gap_sort_key(gap, sequence_hint), entry))

    scored.sort(key=lambda pair: pair[0])
    entries = [entry for _key, entry in scored]

    source_kind_breakdown = [
        {"source_kind": kind, "gap_count": count}
        for kind, count in sorted(source_kind_gap_counts.items())
    ]

    # §5.2's many-to-many read the OTHER way: only detectors actually shared
    # by 2+ Gaps are surfaced under this name -- a single-Gap reference is
    # already visible on that one entry's own `deep_links`.
    shared_sources_out = [
        {"source_kind": kind, "source_ref": ref, "gap_ids": bucket["gap_ids"], "gap_keys": bucket["gap_keys"]}
        for (kind, ref), bucket in sorted(shared_sources.items())
        if len(bucket["gap_ids"]) >= 2
    ]

    return {
        "system_id": system_id,
        "generated_at": now,
        "entries": entries,
        "source_kind_breakdown": source_kind_breakdown,
        "shared_sources": shared_sources_out,
        "degraded_sections": degraded_sections,
        "degraded_detail": degraded_detail,
    }


# ==============================================================================
# §9.1 / §9.3: Overview `objective` section
# ==============================================================================


@dataclass
class ObjectiveOverviewResult:
    """`GET /overview`'s `objective` section (§9.1/§9.3). Composed inside
    `overview_projection.build_overview`'s already-open connection, from the
    SAME `understanding_brief.BriefResult` (or `None`) that section already
    built -- `vision` is read off it, never re-resolved (§9.1: "二重に導出
    しない")."""

    vision: Optional[Any] = None
    active_objective: Optional[Dict[str, Any]] = None
    active_objective_count: int = 0
    next_milestone: Optional[Dict[str, Any]] = None
    primary_gap: Optional[Dict[str, Any]] = None
    objective_state: Optional[str] = None
    next_step: str = "unavailable"
    next_step_state: str = "unavailable"
    next_step_reason: str = ""
    next_step_completion: str = ""
    next_step_value: str = ""
    degraded_sections: List[str] = field(default_factory=list)
    degraded_detail: Dict[str, str] = field(default_factory=dict)


def _set_step(
    result: ObjectiveOverviewResult, key: str, state: str, reason: str, completion: str = "", value: str = ""
) -> ObjectiveOverviewResult:
    result.next_step = key
    result.next_step_state = state
    result.next_step_reason = reason
    result.next_step_completion = completion
    result.next_step_value = value
    return result


def _pick_active_objective(
    conn: sqlite3.Connection, system_id: int, active_objectives: List[Dict[str, Any]]
) -> Tuple[str, int]:
    """§9.1: "複数あれば active_objective_count を添えて最新の確定順で1件を
    出し ... 件数や Gap 数で「重要な方」を選ばない". Since `objective_state`
    itself is folded (§4.2) from the latest non-superseded
    `product_objective_decision` row, an Objective currently reading `active`
    has exactly one such row and its `decision` is `activate` -- so "most
    recently confirmed" is exactly "most recent `created_at` among each
    Objective's own currently-effective `activate` decision", read directly
    off the ledger `product_objective.py` already owns (never a second,
    re-derived notion of recency)."""
    rows = conn.execute(
        """SELECT po.objective_key AS objective_key, pod.created_at AS decided_at, pod.id AS decision_id
           FROM product_objective_decision pod
           JOIN product_objective po ON po.id = pod.objective_id
           WHERE pod.system_id = ? AND pod.decision = 'activate' AND pod.superseded_by_id IS NULL
           ORDER BY pod.created_at DESC, pod.id DESC""",
        (system_id,),
    ).fetchall()
    active_keys = {o["objective_key"] for o in active_objectives}
    ordered = [row["objective_key"] for row in rows if row["objective_key"] in active_keys]
    if not ordered:  # pragma: no cover - defensive: state derives from this exact ledger
        ordered = [o["objective_key"] for o in sorted(active_objectives, key=lambda o: o["id"], reverse=True)]
    return ordered[0], len(active_objectives)


def _journeys_referencing_gap(conn: sqlite3.Connection, system_id: int, gap_key: str) -> List[str]:
    """§5.11: a Gap's Journey connection has exactly ONE writable home,
    `ux_journey_upstream_ref(ref_kind='product_gap')` -- never
    `product_gap_artifact_link`, which would let the two disagree. This is
    the identical reverse-lookup query `functional_lineage.
    _journeys_referencing_gap` uses, kept local for the same reason
    `_journey_has_feature_link` above queries `ux_requirement_step_link`
    directly rather than importing another module's private helper: no
    shared lookup exists in `ux_design.py`, and this module owns no
    cross-module private import."""
    rows = conn.execute(
        """SELECT DISTINCT j.journey_key FROM ux_journey_upstream_ref r
           JOIN ux_journey j ON j.id = r.journey_id
           WHERE r.system_id = ? AND r.ref_kind = 'product_gap' AND r.target_ref = ?
             AND r.superseded_by_id IS NULL""",
        (system_id, gap_key),
    ).fetchall()
    return [r["journey_key"] for r in rows]


def _journey_has_feature_link(conn: sqlite3.Connection, system_id: int, journey_key: str) -> bool:
    """Row #13's "Requirement -> Feature が繋がっていない" check, read
    directly off the existing `ux_requirement_step_link` /
    `product_feature_requirement_link` tables (owned by `ux_design.py` /
    `product_feature.py` respectively, neither of which this module edits or
    re-implements a fold from -- this is a plain existence read)."""
    journey = conn.execute(
        "SELECT id FROM ux_journey WHERE system_id = ? AND journey_key = ?", (system_id, journey_key)
    ).fetchone()
    if journey is None:
        return False
    requirement_rows = conn.execute(
        """SELECT DISTINCT requirement_id FROM ux_requirement_step_link
           WHERE system_id = ? AND journey_id = ? AND superseded_by_id IS NULL""",
        (system_id, journey["id"]),
    ).fetchall()
    requirement_ids = [row["requirement_id"] for row in requirement_rows]
    if not requirement_ids:
        return False
    placeholders = ",".join("?" for _ in requirement_ids)
    row = conn.execute(
        f"SELECT 1 FROM product_feature_requirement_link "  # noqa: S608 - placeholders count matches requirement_ids, values are bound params
        f"WHERE system_id = ? AND requirement_id IN ({placeholders}) AND superseded_by_id IS NULL LIMIT 1",
        (system_id, *requirement_ids),
    ).fetchone()
    return row is not None


def _decide_next_step(
    conn: sqlite3.Connection,
    system_id: int,
    result: ObjectiveOverviewResult,
    brief_available: bool,
) -> ObjectiveOverviewResult:
    """§9.3's 15-row first-match table. See the module docstring for the
    documented scoping assumption on rows #6-14 ("その Objective" /
    "その Milestone")."""
    # Row 1: Brief or Objective list unreadable.
    objectives_ok = True
    objectives: List[Dict[str, Any]] = []
    try:
        obj_result = product_objective.list_objectives(conn, system_id)
        if obj_result.get("degraded_sections"):
            objectives_ok = False
            _merge_child_result(result.degraded_sections, result.degraded_detail, "objectives", obj_result)
        objectives = obj_result["objectives"]
    except Exception as exc:  # pragma: no cover - defensive
        objectives_ok = False
        _degrade(result.degraded_sections, result.degraded_detail, "objectives", exc)

    if not brief_available or not objectives_ok:
        return _set_step(
            result, "unavailable", "unavailable",
            "理解または Objective の一覧を取得できなかったため、次の操作を判定できませんでした。",
        )

    # Row 2: Vision missing or not confirmed.
    vision = result.vision
    if vision is None or getattr(vision, "confirmation", None) != "confirmed":
        return _set_step(
            result, "confirm_vision", "available",
            "Vision がまだ確定していません。",
            "Vision を確認し、確定します。",
            "Objective が Vision へどう寄与するかを言えるようになります。",
        )

    # Row 3: no Objective at all -- §11's graceful empty state, never degraded.
    if not objectives:
        result.objective_state = None
        return _set_step(
            result, "create_objective", "available",
            "この System にはまだ Product Objective がありません。",
            "最初の Objective を作成します。",
            "Vision へ近づくための中間目標を持てるようになります。",
        )

    # Row 4: any Objective still proposed.
    if any(o["objective_state"] == "proposed" for o in objectives):
        return _set_step(
            result, "confirm_objective", "available",
            "確定していない Objective があります。",
            "Objective の内容を確認し、確定します。",
            "Objective を活性化できるようになります。",
        )

    # Row 5: no active Objective.
    active_objectives = [o for o in objectives if o["objective_state"] == "active"]
    if not active_objectives:
        return _set_step(
            result, "activate_objective", "available",
            "活性化された Objective がありません。",
            "確定済みの Objective を活性化します。",
            "この Objective に向けた Milestone / Gap の整理を始められます。",
        )

    active_key, active_count = _pick_active_objective(conn, system_id, active_objectives)
    try:
        active_summary = product_objective.get_objective_summary(conn, system_id, active_key)
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(result.degraded_sections, result.degraded_detail, "active_objective", exc)
        return _set_step(result, "unavailable", "unavailable", "Objective の詳細を取得できませんでした。")

    result.active_objective = active_summary
    result.active_objective_count = active_count
    result.objective_state = active_summary["objective_state"]

    try:
        ms_result = product_objective.list_milestones(conn, system_id, active_key)
        if ms_result.get("degraded_sections"):
            _merge_child_result(result.degraded_sections, result.degraded_detail, "milestones", ms_result)
            return _set_step(result, "unavailable", "unavailable", "Milestone の一覧を取得できませんでした。")
        milestones = ms_result["milestones"]
    except Exception as exc:  # pragma: no cover - defensive
        _degrade(result.degraded_sections, result.degraded_detail, "milestones", exc)
        return _set_step(result, "unavailable", "unavailable", "Milestone の一覧を取得できませんでした。")

    # Row 6: the active Objective has no Milestone.
    if not milestones:
        return _set_step(
            result, "create_milestone", "available",
            "この Objective にはまだ Milestone がありません。",
            "最初の Milestone を作成します。",
            "到達したと判断できる状態を言葉にできるようになります。",
        )

    # Row 7: any Milestone still proposed.
    if any(m["design_status"] == "proposed" for m in milestones):
        return _set_step(
            result, "confirm_milestone", "available",
            "確定していない Milestone があります。",
            "Milestone の定義を確認し、確定します。",
            "Milestone に対する達成判定ができるようになります。",
        )

    # Row 8: a stale confirmation on the Objective or any of its Milestones.
    if active_summary["recheck_state"] == "stale" or any(m["recheck_state"] == "stale" for m in milestones):
        return _set_step(
            result, "recheck_stale_decision", "available",
            "内容が変わった後の Objective / Milestone の確定があります。",
            "変わった内容を確認し、確定をやり直すか判断します。",
            "確定が現在の内容に対する判断のままであるようにできます。",
        )

    milestone_keys = [m["milestone_key"] for m in milestones]
    all_gaps: List[Dict[str, Any]] = []
    gaps_ok = True
    for milestone_key in milestone_keys:
        try:
            gap_result = product_objective.list_gaps(conn, system_id, milestone_key)
            if gap_result.get("degraded_sections"):
                gaps_ok = False
                _merge_child_result(result.degraded_sections, result.degraded_detail, f"gaps:{milestone_key}", gap_result)
            all_gaps.extend(gap_result["gaps"])
        except Exception as exc:  # pragma: no cover - defensive
            gaps_ok = False
            _degrade(result.degraded_sections, result.degraded_detail, f"gaps:{milestone_key}", exc)

    # Row 9: any Gap under this Objective carries a source read-flag.
    if gaps_ok and any(g.get("read_flags") for g in all_gaps):
        return _set_step(
            result, "review_gap_source", "available",
            "検出元の状態が変わった、または食い違っている Gap があります。",
            "検出元の内容を確認し、必要なら Gap の内容や解消状態を見直します。",
            "Gap が現在の検出結果を正しく反映した状態になります。",
        )

    # Pick `next_milestone` per §9.1's own rule: confirmed + unassessed,
    # earliest sequence_hint -- the same target rows #10/#14 refer to as
    # "そのMilestone".
    next_milestone_candidates = [
        m for m in milestones if m["design_status"] == "confirmed" and m["achievement"] == "unassessed"
    ]
    next_milestone_key: Optional[str] = None
    next_milestone_summary: Optional[Dict[str, Any]] = None
    milestone_gaps: List[Dict[str, Any]] = []
    if next_milestone_candidates:
        with_sequence = sorted(
            (( _milestone_sequence_hint(conn, m["id"]), m["milestone_key"], m) for m in next_milestone_candidates)
        )
        next_milestone_key = with_sequence[0][1]
        try:
            next_milestone_summary = product_objective.get_milestone_summary(conn, system_id, next_milestone_key)
            result.next_milestone = next_milestone_summary
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(result.degraded_sections, result.degraded_detail, "next_milestone", exc)
            next_milestone_summary = None

        if next_milestone_summary is not None:
            try:
                milestone_gap_result = product_objective.list_gaps(conn, system_id, next_milestone_key)
                milestone_gaps = milestone_gap_result["gaps"]
            except Exception as exc:  # pragma: no cover - defensive
                _degrade(result.degraded_sections, result.degraded_detail, f"gaps:{next_milestone_key}", exc)
                milestone_gaps = []

            # Row 10: the picked next_milestone has no Gap at all.
            if not milestone_gaps:
                return _set_step(
                    result, "create_gap", "available",
                    "次に到達すべき Milestone にはまだ Gap がありません。",
                    "現状と目標状態の差を Gap として書き起こします。",
                    "Milestone に向けて何を埋める必要があるかが言葉になります。",
                )

            milestone_sequence_hint = _milestone_sequence_hint(conn, next_milestone_summary["id"])
            ordered_milestone_gaps = sorted(
                milestone_gaps, key=lambda g: _gap_sort_key(g, milestone_sequence_hint)
            )
            top_gap = ordered_milestone_gaps[0]
            try:
                result.primary_gap = product_objective.get_gap_summary(conn, system_id, top_gap["gap_key"])
            except Exception as exc:  # pragma: no cover - defensive
                _degrade(result.degraded_sections, result.degraded_detail, "primary_gap", exc)

    # Row 11: an open Gap under this Objective with no priority placed.
    if any(g["lifecycle"] == "open" and g["priority_band"] == "unset" for g in all_gaps):
        return _set_step(
            result, "prioritize_gap", "available",
            "優先度が置かれていない未対応の Gap があります。",
            "Gap に優先バンドを置きます。",
            "どの Gap から着手すべきかが分かるようになります。",
        )

    # Rows 12/13 need each Gap's linked Journeys -- fetched once, reused by
    # both rows. §5.11: the ONE canonical home for this relation is
    # `ux_journey_upstream_ref(ref_kind='product_gap')`, read via
    # `_journeys_referencing_gap`, never `product_gap_artifact_link`.
    now_next_open_gaps = [
        g for g in all_gaps if g["lifecycle"] == "open" and g["priority_band"] in ("now", "next")
    ]
    journey_keys_linked: set = set()
    unlinked_now_next_gap = False
    for gap in now_next_open_gaps:
        try:
            journey_keys = _journeys_referencing_gap(conn, system_id, gap["gap_key"])
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(result.degraded_sections, result.degraded_detail, f"journey_links:{gap['gap_key']}", exc)
            continue
        if not journey_keys:
            unlinked_now_next_gap = True
        else:
            journey_keys_linked.update(journey_keys)

    # Row 12: a now/next open Gap with no Journey link at all.
    if unlinked_now_next_gap:
        return _set_step(
            result, "link_gap_to_journey", "available",
            "優先度の高い open Gap に UX Journey への参照がありません。",
            "Gap をどの体験の話として解消するかを Journey へ link します。",
            "Gap の解消経路が Journey / Requirement / Feature へたどれるようになります。",
        )

    # Also collect Journey links from every Gap under this Objective (not
    # only now/next-open ones) for row #13's "Journey はあるが" check --
    # §9.3 does not scope row #13 to the same priority/lifecycle filter row
    # #12 uses.
    all_journey_keys: set = set(journey_keys_linked)
    for gap in all_gaps:
        if gap in now_next_open_gaps:
            continue  # already fetched above
        try:
            all_journey_keys.update(_journeys_referencing_gap(conn, system_id, gap["gap_key"]))
        except Exception as exc:  # pragma: no cover - defensive
            _degrade(result.degraded_sections, result.degraded_detail, f"journey_links:{gap['gap_key']}", exc)
            continue

    # Row 13: a linked Journey exists, but none of them reaches a Feature
    # through Requirement -> Feature.
    if all_journey_keys and not any(
        _journey_has_feature_link(conn, system_id, journey_key) for journey_key in all_journey_keys
    ):
        return _set_step(
            result, "link_requirement_to_feature", "available",
            "Journey はありますが、Requirement から Feature へつながっていません。",
            "Journey の Requirement を Feature へ link します。",
            "Gap の解消経路が実装対象までたどれるようになります。",
        )

    # Row 14: the picked next_milestone is unassessed and every one of its
    # Gaps is resolved.
    if (
        next_milestone_summary is not None
        and milestone_gaps
        and all(g["lifecycle"] == "resolved" for g in milestone_gaps)
    ):
        return _set_step(
            result, "assess_milestone", "available",
            "Milestone 配下の Gap がすべて解消されています。",
            "Milestone の達成判定を記録します。",
            "この Milestone が目標状態に到達したかどうかが記録されます。",
        )

    # Row 15: nothing else applies.
    return _set_step(result, "none", "complete", "")


def build_objective_overview(
    conn: sqlite3.Connection, system_id: int, brief: Optional[Any], *, now: Optional[float] = None
) -> ObjectiveOverviewResult:
    """`GET /overview`'s `objective` section (§9.1/§9.3). `brief` is the
    SAME `understanding_brief.BriefResult` (or `None` on that section's own
    failure) `overview_projection.build_overview` already computed -- passed
    in rather than rebuilt, so Vision is read once (§9.1's "二重に導出しな
    い"). Performs no write of any kind."""
    del now  # unused; timestamps belong to the individual entity rows this composes
    result = ObjectiveOverviewResult()
    result.vision = getattr(brief, "vision", None) if brief is not None else None
    _decide_next_step(conn, system_id, result, brief_available=brief is not None)
    return result
