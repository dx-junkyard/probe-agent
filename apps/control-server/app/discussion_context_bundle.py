"""Discussion context bundle (Issue #458, Epic #443 §9).

`docs/01-specifications/capabilities/ai-discussion-adapter.md` §9.1-§9.3 (DD-CTX-01..05) is the canonical
contract; `docs/01-specifications/ux/decision-discussion-workflow.md` owns the developer-facing workflow this
feeds. This module builds a bounded, budgeted, cross-target "context bundle"
for a Discussion root (any registered `discussion_adapters` target_kind) plus
its directly and transitively (depth <= 2) related entities, and persists the
short-lived cursor that lets a developer explicitly fetch more.

Scope decisions (documented here because they are this Issue's own,
defensible simplifications of the wire-shape sketch in the design doc, not a
deviation from its Decisions):

- Every bundle has exactly two-or-three sections: `"self"` (the root's own
  facts, degraded to a reference-only stub when they alone would blow the
  byte budget -- the "巨大root" rule), an OPTIONAL `"objective"` section
  (Overview root only, DD-CTX-01's explicit special case: the SAME
  `overview_projection.build_overview(...).objective` the Overview screen
  itself renders, never re-derived), and `"related"` (the BFS walk of
  registered relation extractors, depth <= 2). A future increment MAY split
  `"related"` into one section per relation "direction" without changing this
  module's public surface -- `_RELATION_EXTRACTORS` already keys by
  `target_kind`, and grouping by field name is a bookkeeping change inside
  `_build_related_section`, not a contract change.
- "登録された relation resolver だけを使う" (DD-CTX-01) is implemented as
  `_RELATION_EXTRACTORS`: a small, explicit, per-`target_kind` function that
  reads ONLY the fields `discussion_adapters.gather_context` already returns
  for that kind (the same facts a discussion turn's prompt would see) and
  emits `(target_kind, target_ref)` pairs -- never a name-matching join,
  never a foreign System query, never LLM-supplied SQL/URL/file access. A
  `target_kind` absent from this table is a structural "relation未登録"
  (DD-CTX-04's first row), reported as `unsupported`, never silently
  degraded to an empty result indistinguishable from "confirmed to have no
  relations".
- Every entity actually read (root, `self`, `objective`'s seeds, every
  `related` entry) is resolved via the SAME registered
  `discussion_adapters.DISCUSSION_ADAPTERS[kind].resolver` /
  `discussion_adapters.gather_context` this Epic already uses everywhere
  else -- this module never re-implements a digest or a canonical read.
  Both of those self-manage their OWN `get_conn()` connection per call
  (`resolver` opens one directly; `gather_context` is called here with a
  connection THIS module opens immediately before and closes immediately
  after) and this module never nests one call inside another's open
  connection -- see CLAUDE.md's `get_conn()` reentrancy rule and
  `db.DatabaseReentrancyError`. Building a bundle therefore never holds one
  connection across the whole walk; it opens and closes many short-lived
  ones, which is what makes the "never call get_conn() while another is
  already open on this thread" rule automatically satisfied for an
  arbitrarily deep/wide walk without this module tracking connection state
  itself.
- The dependency manifest (`dependencies` on the wire, `[{target_kind,
  target_ref, digest}]`) is normalized via `app.joint_premise.
  normalize_premise_manifest` / digested via `compute_premise_manifest_digest`
  -- Issue #461 decision 5's shared helpers, never re-defined here. This is
  the manifest Issue #461's `joint_understanding_session.
  premise_dependency_manifest_json` column documents as "Issue #458 is what
  actually fills it" -- populating a session's manifest from a bundle's
  `dependencies` is #455's job (opening a Discussion-origin JU session);
  this module only produces the manifest and exposes it on the wire.
- This module does NOT register a production `app.joint_premise.
  register_dependency_digest_resolver` at import time. `app/joint_premise.py`
  documents that hook as "Unregistered until Issue #458 provides context
  exploration", and `tests/test_joint_understanding_discussion_scope.py`'s
  `test_dependency_manifest_unresolved_without_a_resolver_never_reads_as_
  current` asserts the CURRENT global-default behaviour (no resolver
  registered at all) is itself the correct, tested answer for a session with
  no fake resolver installed. Auto-registering a real resolver at import
  time would flip that test's target dependency
  (`ux_requirement:req-1`, which never exists in that test's fixture DB)
  from "unresolved" (`stale`/`dependency_manifest_unresolved`) to "resolved
  to `None`" (`missing`/`dependency_target_removed`) -- a different, wrong
  verdict for a scenario that Issue is explicitly pinning. Wiring a real
  resolver into production is therefore left to whichever Issue (#455) first
  starts writing non-empty `discussion`-origin manifests, in the same change
  that updates that test's premise for "a resolver is now always on".
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlencode

from . import discussion_adapters
from .db import get_conn
from .joint_premise import (
    PremiseDependencyRef,
    compute_premise_manifest_digest,
    normalize_premise_manifest,
)

SCHEMA_VERSION = "discussion-context-bundle-v1"

# Cursor lifetime (DD-CTX-03): "有効期限30分".
CONTEXT_CURSOR_TTL_SECONDS = 30 * 60


# --- DD-CTX-02: budget, version-carrying so a later tuning pass is a config
#     bump, never a silent behaviour change the client cannot see. -----------


@dataclass(frozen=True)
class ContextBudget:
    version: str = "context-budget-v1"
    #: "関連方向ごと深さ2" -- depth counted in hops from the root, root itself
    #: is depth 0 and is never re-counted as a "related" hop.
    max_depth: int = 2
    #: "section最大50件"
    section_item_limit: int = 50
    #: "全体200件"
    total_item_limit: int = 200
    #: "64KiB" of UTF-8 JSON, summed across every entry's `facts` in the
    #: whole bundle (root's own `self` entry included).
    total_byte_limit: int = 64 * 1024


DEFAULT_BUDGET = ContextBudget()


class ContextBundleError(Exception):
    """Fail-closed structural error. `code` is a finite, documented reason."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class ContextExpansionError(Exception):
    """Fail-closed error for `POST .../context-expansions`. `status_code` is
    the finite HTTP status the route maps 1:1 (never re-decided by the
    route): 404 unknown/tampered/foreign-System, 410 expired, 409 stale."""

    def __init__(self, code: str, status_code: int, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.status_code = status_code


# --- Wire-independent result shapes (converted to Pydantic Out models at the
#     route layer -- this module never imports `app.models`, matching the
#     existing `discussion_adapters.TargetContextResult` precedent). --------


@dataclass(frozen=True)
class BundleRootRef:
    target_kind: str
    target_ref: str
    revision_id: Optional[int]
    digest: str


@dataclass(frozen=True)
class BundleSnapshotRef:
    id: Optional[int]
    commit_sha: Optional[str]


@dataclass(frozen=True)
class BundleEntry:
    target_kind: str
    target_ref: str
    title: str
    revision_id: Optional[int]
    digest: str
    resolution: str  # "resolved" | "unresolved" | "not_tracked"
    facts: Dict[str, Any] = field(default_factory=dict)
    #: True when this entry's OWN facts were replaced by an empty stub
    #: because including them would have exceeded the bundle-wide byte
    #: budget (DD-CTX-02's "巨大root/巨大entryは参照に置換してpartialとする").
    #: The entry's identity (target_kind/target_ref/digest/resolution) is
    #: never dropped -- only its `facts` body is.
    truncated: bool = False


@dataclass(frozen=True)
class BundleCoverage:
    returned_count: int
    total_count: Optional[int]
    completeness: str  # "complete" | "partial" | "unknown"
    stop_reason: str
    continuation: Optional[str] = None


@dataclass(frozen=True)
class BundleSection:
    section_id: str
    operation_state: str  # DiscussionOperationResult
    facts: Tuple[BundleEntry, ...]
    coverage: BundleCoverage


@dataclass(frozen=True)
class BundleSource:
    source_id: str
    target_kind: str
    target_ref: str
    revision_id: Optional[int]
    digest: str
    snapshot_id: Optional[int]
    freshness: str  # DiscussionTargetState
    deep_link: Optional[str]
    deep_link_state: str  # "selected" | "screen_only" | "unavailable"


@dataclass(frozen=True)
class BundleNextAction:
    kind: str  # "expand_context" | "none"
    target: str
    enabled: bool
    reason: str


@dataclass(frozen=True)
class DiscussionContextBundle:
    schema_version: str
    bundle_digest: str
    root: BundleRootRef
    snapshot: BundleSnapshotRef
    sections: Tuple[BundleSection, ...]
    sources: Tuple[BundleSource, ...]
    dependencies: Tuple[PremiseDependencyRef, ...]
    next_action: BundleNextAction


# --- Small, self-contained helpers -------------------------------------------


def _json_size(payload: Any) -> int:
    return len(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def _canonical_digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_bundle_digest(
    root: BundleRootRef, dependencies: Sequence[PremiseDependencyRef],
) -> str:
    return _canonical_digest({
        "root": {"target_kind": root.target_kind, "target_ref": root.target_ref, "digest": root.digest},
        "dependencies": [d.as_dict() for d in dependencies],
    })


def _freshness_for_resolution(resolution: str) -> str:
    if resolution == "resolved":
        return "current"
    if resolution == "not_tracked":
        return "not_tracked"
    return "unresolvable"


def _deep_link_for(adapter: Optional["discussion_adapters.DiscussionAdapter"], target_ref: str) -> Tuple[Optional[str], str]:
    """Generic deep link, built ONLY from the adapter's own registered
    `screen_ids` / `route_params` -- never a second, per-kind deep-link table
    (the Dashboard's `lib/discussion-adapters.ts` `deepLink()` is the
    client-side sibling of this same reuse rule)."""
    if adapter is None or not adapter.screen_ids:
        return None, "unavailable"
    screen = adapter.screen_ids[0]
    params = adapter.route_params(target_ref) or {}
    query = urlencode(sorted(params.items()))
    url = f"/{screen}" + (f"?{query}" if query else "")
    return url, ("selected" if params else "screen_only")


def _resolve(system_id: int, target_kind: str, target_ref: str):
    """`ResolvedTarget` via the registered adapter's own resolver, or `None`
    when the kind is not registered at all (defensive -- a relation extractor
    only ever emits registered kinds today, but the registry can narrow
    between builds)."""
    adapter = discussion_adapters.get_adapter(target_kind)
    if adapter is None:
        return None
    return adapter.resolver(system_id, target_ref)


def _gather(system_id: int, target_kind: str, target_ref: str) -> "discussion_adapters.TargetContextResult":
    with get_conn() as conn:
        return discussion_adapters.gather_context(conn, system_id, target_kind, target_ref)


_STOP_REASON_FOR_OPERATION_STATE: Dict[str, str] = {
    "unsupported": "unsupported",
    "not_applicable": "not_applicable",
    "unavailable": "provider_error",
}


# --- DD-CTX-01: registered relation extractors --------------------------------
# Each function reads ONLY `discussion_adapters.gather_context(...).facts`
# for that kind (the same bounded, already-filtered facts a discussion turn's
# prompt sees) and returns `(target_kind, target_ref)` pairs for entries the
# BUNDLE may walk into. A kind absent here has structurally no known
# relations to this Epic's registry (DD-CTX-04's "relation未登録"row) --
# adding one later is additive, never required for existing behaviour.

RelationExtractor = Callable[[Dict[str, Any]], List[Tuple[str, str]]]


def _extract_generic_refs(
    facts: Dict[str, Any], list_field: str, *, kind_field: str = "ref_kind", ref_field: str = "target_ref",
) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for item in facts.get(list_field) or []:
        if not isinstance(item, dict):
            continue
        kind = item.get(kind_field)
        ref = item.get(ref_field)
        if kind and ref and discussion_adapters.get_adapter(kind) is not None:
            out.append((kind, ref))
    return out


def _extract_purpose_element(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    return [
        ("purpose_relation", r["id"])
        for r in (facts.get("related_relations") or [])
        if isinstance(r, dict) and r.get("id")
    ]


def _extract_purpose_relation(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for key in ("source_id", "target_id"):
        ref = facts.get(key)
        if ref:
            out.append(("purpose_element", ref))
    return out


def _extract_stakeholder(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    return _extract_generic_refs(facts, "refs")


def _extract_stakeholder_need(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    return _extract_generic_refs(facts, "refs")


def _extract_product_objective(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    out = _extract_generic_refs(facts, "upstream_refs")
    parent = facts.get("parent_objective_key")
    if parent:
        out.append(("product_objective", parent))
    return out


def _extract_product_milestone(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    objective_key = facts.get("objective_key")
    if objective_key:
        out.append(("product_objective", objective_key))
    for dep in facts.get("dependencies") or []:
        if isinstance(dep, dict) and dep.get("depends_on_milestone_key"):
            out.append(("product_milestone", dep["depends_on_milestone_key"]))
    return out


def _extract_product_gap(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    milestone_key = facts.get("milestone_key")
    if milestone_key:
        out.append(("product_milestone", milestone_key))
    for journey in facts.get("journey_links") or []:
        if isinstance(journey, dict) and journey.get("journey_key"):
            out.append(("ux_journey", journey["journey_key"]))
    # `source_refs` / `evidence_refs` / `artifact_links` are deliberately NOT
    # walked here: their `source_kind` / `evidence_kind` / `link_kind` name a
    # DETECTION/EVIDENCE/ARTIFACT category (e.g. `capability_drift`,
    # `repo_path`), not a `discussion_adapters` target_kind -- treating them
    # as entity refs would resolve nonsense targets or silently drop them,
    # neither of which is "根拠不足" (DD-CTX-04): they are simply not, today,
    # relations this registry can walk.
    return out


def _extract_product_feature(facts: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = [
        ("ux_requirement", r["requirement_key"])
        for r in (facts.get("requirement_links") or [])
        if isinstance(r, dict) and r.get("requirement_key")
    ]
    out.extend(_extract_generic_refs(facts, "target_links", kind_field="link_kind", ref_field="target_ref"))
    return out


_RELATION_EXTRACTORS: Dict[str, RelationExtractor] = {
    "purpose_element": _extract_purpose_element,
    "purpose_relation": _extract_purpose_relation,
    "stakeholder": _extract_stakeholder,
    "stakeholder_need": _extract_stakeholder_need,
    "product_objective": _extract_product_objective,
    "product_milestone": _extract_product_milestone,
    "product_gap": _extract_product_gap,
    "product_feature": _extract_product_feature,
}


# --- Budget bookkeeping, shared across every section in one bundle ----------


@dataclass
class _BudgetTracker:
    budget: ContextBudget
    total_items: int = 0
    total_bytes: int = 0

    def has_item_room(self) -> bool:
        return self.total_items < self.budget.total_item_limit

    def fits(self, size_bytes: int) -> bool:
        return self.has_item_room() and self.total_bytes + size_bytes <= self.budget.total_byte_limit

    def add(self, size_bytes: int) -> None:
        self.total_items += 1
        self.total_bytes += size_bytes

    def limiting_reason(self) -> str:
        return "item_budget" if not self.has_item_room() else "byte_budget"


def _dedupe_new(candidates: Sequence[Tuple[str, str]], visited: Set[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Identity-based cycle cutoff (DD-CTX-02's "cycleはidentityで打ち切る"):
    a `(target_kind, target_ref)` pair is claimed AT DISCOVERY time, so a
    later path re-discovering the same pair (a relation cycle, or two
    parents pointing at the same child) never re-adds or re-counts it."""
    out: List[Tuple[str, str]] = []
    for pair in sorted(dict.fromkeys(candidates)):
        if pair in visited:
            continue
        visited.add(pair)
        out.append(pair)
    return out


def _make_entry(system_id: int, kind: str, ref: str, tracker: _BudgetTracker):
    """Resolve + gather one entity, applying the byte budget. Returns
    `(BundleEntry, resolved, context_result)` or `None` when the kind is no
    longer registered (defensive) or the entry could not even be counted
    against the total item budget."""
    resolved = _resolve(system_id, kind, ref)
    if resolved is None:
        return None
    context_result = _gather(system_id, kind, ref)
    facts = context_result.facts if context_result.operation_state == "available" else {}
    size = _json_size(facts)
    truncated = False
    if not tracker.fits(size):
        if not tracker.has_item_room():
            return None
        facts, truncated, size = {}, True, 0
    tracker.add(size)
    entry = BundleEntry(
        target_kind=kind, target_ref=ref, title=resolved.title,
        revision_id=resolved.revision_id, digest=resolved.digest,
        resolution=resolved.resolution, facts=facts, truncated=truncated,
    )
    return entry, resolved, context_result, truncated


# --- Section builders ---------------------------------------------------------


def _build_self_section(
    root_kind: str, root_ref: str, root_resolved, root_context: "discussion_adapters.TargetContextResult",
    tracker: _BudgetTracker,
) -> BundleEntry:
    """The root's own facts, degraded to a reference-only stub (never a
    dropped identity) when they alone would blow the byte budget -- DD-CTX-02's
    "巨大rootも無制限に通さず本文詳細を参照に置換してpartialとする"."""
    facts = root_context.facts if root_context.operation_state == "available" else {}
    size = _json_size(facts)
    truncated = False
    if size and not tracker.fits(size):
        facts, truncated, size = {}, True, 0
    tracker.add(size)
    return BundleEntry(
        target_kind=root_kind, target_ref=root_ref, title=root_resolved.title,
        revision_id=root_resolved.revision_id, digest=root_resolved.digest,
        resolution=root_resolved.resolution, facts=facts, truncated=truncated,
    ), truncated


def _self_section(root_kind, root_ref, root_resolved, root_context, tracker) -> Tuple[BundleSection, BundleEntry]:
    entry, truncated = _build_self_section(root_kind, root_ref, root_resolved, root_context, tracker)
    if root_context.operation_state != "available":
        stop_reason = _STOP_REASON_FOR_OPERATION_STATE[root_context.operation_state]
        coverage = BundleCoverage(1, 1, "partial", stop_reason, None)
    elif truncated:
        coverage = BundleCoverage(1, 1, "partial", "byte_budget", None)
    else:
        coverage = BundleCoverage(1, 1, "complete", "complete", None)
    return BundleSection("self", root_context.operation_state, (entry,), coverage), entry


def _objective_section(system_id: int, tracker: _BudgetTracker) -> Tuple[BundleSection, List[Tuple[str, str]]]:
    """DD-CTX-01's Overview special case: the SAME `overview_projection.
    build_overview(...).objective` section the Overview screen renders,
    never re-derived. `active_objective` / `primary_gap` seed the `related`
    walk so an Overview root can reach the SAME Objective/Gap up- and
    downstream a direct `product_objective` / `product_gap` root would."""
    from . import overview_projection

    try:
        overview = overview_projection.build_overview(system_id)
    except Exception:
        return (
            BundleSection("objective", "unavailable", (), BundleCoverage(0, None, "unknown", "provider_error", None)),
            [],
        )
    objective = overview.objective
    if objective is None:
        return (
            BundleSection("objective", "unavailable", (), BundleCoverage(0, None, "unknown", "provider_error", None)),
            [],
        )
    facts = asdict(objective)
    size = _json_size(facts)
    truncated = False
    if size and not tracker.fits(size):
        facts, truncated, size = {}, True, 0
    tracker.add(size)
    entry = BundleEntry(
        target_kind="screen", target_ref="overview", title="Objective overview",
        revision_id=None, digest="", resolution="not_tracked", facts=facts, truncated=truncated,
    )
    coverage = BundleCoverage(1, 1, "partial" if truncated else "complete", "byte_budget" if truncated else "complete", None)
    seeds: List[Tuple[str, str]] = []
    active = objective.active_objective
    if isinstance(active, dict) and active.get("objective_key"):
        seeds.append(("product_objective", active["objective_key"]))
    gap = objective.primary_gap
    if isinstance(gap, dict) and gap.get("gap_key"):
        seeds.append(("product_gap", gap["gap_key"]))
    return BundleSection("objective", "available", (entry,), coverage), seeds


def _build_related_section(
    system_id: int,
    root_kind: str,
    root_context: "discussion_adapters.TargetContextResult",
    tracker: _BudgetTracker,
    visited: Set[Tuple[str, str]],
    budget: ContextBudget,
    *,
    extra_seeds: Sequence[Tuple[str, str]] = (),
) -> Tuple[BundleSection, List[Tuple[str, str, str]]]:
    """DD-CTX-02's bounded, depth<=2, identity-cycle-cut BFS. Returns the
    section plus the `(target_kind, target_ref, digest)` triples of every
    entry it actually included (for the bundle-wide `dependencies` manifest
    and `sources` catalog).

    Depth-1 candidate refs are always fully enumerable up front (they come
    from the ROOT's already-fetched facts, a cheap in-memory read); reaching
    depth 2 additionally requires having FETCHED each depth-1 candidate's own
    facts, which is exactly the budgeted step. `total_count` is therefore
    reported (non-null) only once every depth-1 candidate has been fetched --
    at that point the full depth-1+depth-2 candidate set is known even if
    hydrating all of it was cut short by the item/byte budget. If the
    depth-1 sweep itself was cut short, the depth-2 population is genuinely
    unknowable (an unfetched depth-1 candidate's own relations were never
    read), so `total_count=None` / `completeness="unknown"` -- never a
    guessed lower bound presented as a fact.
    """
    adapter = discussion_adapters.get_adapter(root_kind)
    extractor = _RELATION_EXTRACTORS.get(root_kind)

    if root_context.operation_state == "unavailable":
        return (
            BundleSection("related", "unavailable", (), BundleCoverage(0, None, "unknown", "provider_error", None)),
            [],
        )
    if extractor is None and not extra_seeds:
        state = "not_applicable" if adapter is not None and adapter.scope == "screen" else "unsupported"
        total = 0 if state == "not_applicable" else None
        completeness = "complete" if state == "not_applicable" else "unknown"
        return BundleSection("related", state, (), BundleCoverage(0, total, completeness, state, None)), []

    d1_raw: List[Tuple[str, str]] = list(extra_seeds)
    if extractor is not None and root_context.operation_state == "available":
        d1_raw.extend(extractor(root_context.facts))
    d1_candidates = _dedupe_new(d1_raw, visited)
    d1_total = len(d1_candidates)

    entries: List[BundleEntry] = []
    included: List[Tuple[str, str, str]] = []
    d2_raw: List[Tuple[str, str]] = []
    d1_fully_processed = True
    hit_budget = False
    triggering_reason = ""

    for kind, ref in d1_candidates:
        if len(entries) >= budget.section_item_limit or not tracker.has_item_room():
            d1_fully_processed = False
            hit_budget = True
            triggering_reason = tracker.limiting_reason() if not tracker.has_item_room() else "item_budget"
            break
        made = _make_entry(system_id, kind, ref, tracker)
        if made is None:
            d1_fully_processed = False
            hit_budget = True
            triggering_reason = tracker.limiting_reason()
            break
        entry, resolved, context_result, truncated = made
        if truncated:
            hit_budget = True
            triggering_reason = triggering_reason or "byte_budget"
        entries.append(entry)
        if resolved.resolution == "resolved":
            included.append((kind, ref, resolved.digest))
        if context_result.operation_state == "available":
            sub_extractor = _RELATION_EXTRACTORS.get(kind)
            if sub_extractor is not None:
                d2_raw.extend(sub_extractor(context_result.facts))

    d2_total = 0
    if d1_fully_processed:
        d2_candidates = _dedupe_new(d2_raw, visited)
        d2_total = len(d2_candidates)
        for kind, ref in d2_candidates:
            if len(entries) >= budget.section_item_limit or not tracker.has_item_room():
                hit_budget = True
                triggering_reason = tracker.limiting_reason() if not tracker.has_item_room() else "item_budget"
                break
            made = _make_entry(system_id, kind, ref, tracker)
            if made is None:
                hit_budget = True
                triggering_reason = tracker.limiting_reason()
                break
            entry, resolved, _context_result, truncated = made
            if truncated:
                hit_budget = True
                triggering_reason = triggering_reason or "byte_budget"
            entries.append(entry)
            if resolved.resolution == "resolved":
                included.append((kind, ref, resolved.digest))
            # Depth cap: depth-2 entries' own relations are never extracted.

    returned_count = len(entries)
    if not d1_fully_processed:
        coverage = BundleCoverage(returned_count, None, "unknown", triggering_reason or "item_budget", None)
    else:
        total_count = d1_total + d2_total
        if hit_budget:
            coverage = BundleCoverage(returned_count, total_count, "partial", triggering_reason or "item_budget", None)
        else:
            coverage = BundleCoverage(returned_count, total_count, "complete", "complete", None)

    return BundleSection("related", "available", tuple(entries), coverage), included


# --- Bundle assembly -----------------------------------------------------------


def build_context_bundle(
    system_id: int, root_target_kind: str, root_target_ref: str, *, budget: ContextBudget = DEFAULT_BUDGET,
) -> DiscussionContextBundle:
    """DD-CTX-01/02's full bundle for one root. Raises `ContextBundleError`
    (`discussion_target_kind_unregistered` / `discussion_context_root_not_
    found`) instead of ever returning a bundle for a target that does not
    exist -- the route maps those to 422 / 404 respectively, matching every
    other discussion-adapter entry point's own fail-closed contract."""
    adapter = discussion_adapters.get_adapter(root_target_kind)
    if adapter is None:
        raise ContextBundleError("discussion_target_kind_unregistered", root_target_kind)
    root_resolved = adapter.resolver(system_id, root_target_ref)
    if root_resolved.resolution == "unresolved":
        raise ContextBundleError("discussion_context_root_not_found", root_target_ref)

    from . import state_facts

    with get_conn() as conn:
        snapshot_row = state_facts.get_latest_ready_snapshot(conn, system_id)
    snapshot = BundleSnapshotRef(
        id=snapshot_row["id"] if snapshot_row is not None else None,
        commit_sha=snapshot_row["commit_sha"] if snapshot_row is not None else None,
    )

    tracker = _BudgetTracker(budget)
    visited: Set[Tuple[str, str]] = {(root_target_kind, root_target_ref)}
    sources: Dict[Tuple[str, str], BundleSource] = {}
    dependency_triples: List[Tuple[str, str, str]] = []

    def _register_source(kind: str, ref: str, resolved) -> None:
        deep_link, deep_link_state = _deep_link_for(discussion_adapters.get_adapter(kind), ref)
        sources[(kind, ref)] = BundleSource(
            source_id=f"{kind}:{ref}", target_kind=kind, target_ref=ref,
            revision_id=resolved.revision_id, digest=resolved.digest,
            snapshot_id=snapshot.id, freshness=_freshness_for_resolution(resolved.resolution),
            deep_link=deep_link, deep_link_state=deep_link_state,
        )

    _register_source(root_target_kind, root_target_ref, root_resolved)

    root_context = _gather(system_id, root_target_kind, root_target_ref)
    self_section, _self_entry = _self_section(root_target_kind, root_target_ref, root_resolved, root_context, tracker)
    sections: List[BundleSection] = [self_section]

    extra_seeds: List[Tuple[str, str]] = []
    if root_target_kind == "screen" and root_target_ref == "overview":
        objective_section, seeds = _objective_section(system_id, tracker)
        sections.append(objective_section)
        extra_seeds = seeds

    related_section, related_included = _build_related_section(
        system_id, root_target_kind, root_context, tracker, visited, budget, extra_seeds=extra_seeds,
    )
    sections.append(related_section)

    for kind, ref, digest in related_included:
        resolved = _resolve(system_id, kind, ref)
        if resolved is not None:
            _register_source(kind, ref, resolved)
        dependency_triples.append((kind, ref, digest))

    manifest = normalize_premise_manifest(
        [{"target_kind": k, "target_ref": r, "digest": d} for (k, r, d) in dependency_triples]
    )
    root_ref_out = BundleRootRef(root_target_kind, root_target_ref, root_resolved.revision_id, root_resolved.digest)
    bundle_digest = _compute_bundle_digest(root_ref_out, manifest)

    needs_more = any(s.coverage.completeness != "complete" for s in sections)
    next_action = BundleNextAction(
        kind="expand_context" if needs_more else "none",
        target="" if not needs_more else f"{root_target_kind}:{root_target_ref}",
        enabled=needs_more,
        reason="取得できていない関連情報があります。追加取得できます。" if needs_more else "",
    )

    return DiscussionContextBundle(
        schema_version=SCHEMA_VERSION, bundle_digest=bundle_digest, root=root_ref_out,
        snapshot=snapshot, sections=tuple(sections), sources=tuple(sources.values()),
        dependencies=manifest, next_action=next_action,
    )


# --- DD-CTX-03: expansion cursor ----------------------------------------------
#
# The cursor is a pure, server-side-random opaque token (`secrets.
# token_urlsafe`) -- it carries NO client-decodable state, so "改ざん" (a
# modified token) can only ever produce a lookup miss (404), never a crafted
# but plausible continuation. All resumable state (System/root/snapshot/
# dependency manifest/section/offset) lives in `discussion_context_cursor`,
# keyed by the token, with a 30-minute `expires_at`.


@dataclass(frozen=True)
class ContextExpansionResult:
    bundle: DiscussionContextBundle
    expanded_section_ids: Tuple[str, ...]


def _cleanup_expired_cursors(conn) -> None:
    conn.execute("DELETE FROM discussion_context_cursor WHERE expires_at < ?", (time.time(),))


def create_context_cursor(
    conn, *, system_id: int, thread_id: int, root: BundleRootRef, snapshot: BundleSnapshotRef,
    bundle_digest: str, section_id: str, resume_offset: int,
    dependency_manifest: Sequence[PremiseDependencyRef],
) -> str:
    """Persist a resumable continuation for one section. Returns the opaque
    token to hand to the client as `coverage.continuation`."""
    now = time.time()
    _cleanup_expired_cursors(conn)
    token = secrets.token_urlsafe(32)
    manifest_digest = compute_premise_manifest_digest(dependency_manifest)
    conn.execute(
        """INSERT INTO discussion_context_cursor
               (token, system_id, thread_id, root_target_kind, root_target_ref, root_digest,
                snapshot_id, bundle_digest, section_id, resume_offset,
                dependency_manifest_json, dependency_manifest_digest, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            token, system_id, thread_id, root.target_kind, root.target_ref, root.digest,
            snapshot.id, bundle_digest, section_id, resume_offset,
            json.dumps([d.as_dict() for d in dependency_manifest], ensure_ascii=False),
            manifest_digest, now, now + CONTEXT_CURSOR_TTL_SECONDS,
        ),
    )
    return token


def _load_cursor(conn, *, system_id: int, thread_id: int, token: str):
    """`None` on any of: unknown token, foreign System, foreign thread --
    all reported identically as `discussion_context_continuation_unknown`
    (never disclosing WHICH reason, per the existing cross-System 404 rule)."""
    row = conn.execute(
        "SELECT * FROM discussion_context_cursor WHERE token = ?", (token,),
    ).fetchone()
    if row is None:
        return None
    if row["system_id"] != system_id or row["thread_id"] != thread_id:
        return None
    return row


def resolve_context_expansion(
    system_id: int, thread_id: int, *, bundle_digest: str, continuation: str, budget: ContextBudget = DEFAULT_BUDGET,
) -> ContextExpansionResult:
    """DD-CTX-03. First-match fail-closed gate, then a resumed fetch of the
    NEXT batch for the cursor's own section, re-verifying (DD-CTX-05) that
    neither the root nor any dependency captured so far moved since the
    cursor was minted -- "root不変でも使った依存根拠更新はstale"."""
    with get_conn() as conn:
        row = _load_cursor(conn, system_id=system_id, thread_id=thread_id, token=continuation)
    if row is None:
        raise ContextExpansionError(
            "discussion_context_continuation_unknown", 404, "Unknown discussion context continuation.",
        )
    if row["expires_at"] < time.time():
        raise ContextExpansionError(
            "discussion_context_continuation_expired", 410, "This discussion context continuation has expired.",
        )
    if row["bundle_digest"] != bundle_digest:
        raise ContextExpansionError(
            "discussion_context_continuation_stale", 409,
            "The bundle has changed since this continuation was issued.",
        )

    root_kind, root_ref = row["root_target_kind"], row["root_target_ref"]
    current_root = _resolve(system_id, root_kind, root_ref)
    if current_root is None or current_root.resolution == "unresolved":
        raise ContextExpansionError(
            "discussion_context_continuation_stale", 409, "The context root no longer resolves.",
        )
    if current_root.digest != row["root_digest"]:
        raise ContextExpansionError(
            "discussion_context_continuation_stale", 409, "The context root has changed.",
        )
    try:
        stored_manifest = normalize_premise_manifest(json.loads(row["dependency_manifest_json"] or "[]"))
    except (ValueError, TypeError):
        stored_manifest = ()
    for dep in stored_manifest:
        current = _resolve(system_id, dep.target_kind, dep.target_ref)
        current_digest = current.digest if current is not None and current.resolution == "resolved" else None
        if current_digest != dep.digest:
            raise ContextExpansionError(
                "discussion_context_continuation_stale", 409,
                "A dependency this context relied on has changed.",
            )

    from . import state_facts

    with get_conn() as conn:
        snapshot_row = state_facts.get_latest_ready_snapshot(conn, system_id)
    current_snapshot_id = snapshot_row["id"] if snapshot_row is not None else None
    if current_snapshot_id != row["snapshot_id"]:
        raise ContextExpansionError(
            "discussion_context_continuation_stale", 409, "The pinned snapshot has changed.",
        )

    # The cursor's premise held: re-run the SAME deterministic extraction and
    # return only the batch starting at `resume_offset` (DD-CTX-03's "未取得
    # 範囲を限定して取得する"). Re-deriving is cheap and reproducible
    # (`_RELATION_EXTRACTORS` + `_dedupe_new`'s stable sort), never a second,
    # independently-drifting source of the candidate order.
    section_id = row["section_id"]
    resume_offset = row["resume_offset"]

    root_context = _gather(system_id, root_kind, root_ref)
    tracker = _BudgetTracker(budget)
    visited: Set[Tuple[str, str]] = {(root_kind, root_ref)}
    for dep in stored_manifest:
        visited.add((dep.target_kind, dep.target_ref))

    extra_seeds: List[Tuple[str, str]] = []
    if root_kind == "screen" and root_ref == "overview" and section_id == "related":
        _obj_section, extra_seeds = _objective_section(system_id, _BudgetTracker(budget))

    full_section, included = _build_related_section(
        system_id, root_kind, root_context, tracker, visited, budget, extra_seeds=extra_seeds,
    )
    # Skip entries the caller already has (everything up to `resume_offset`);
    # return only the newly-visible batch.
    new_entries = full_section.facts[resume_offset:]
    new_coverage = BundleCoverage(
        returned_count=len(new_entries),
        total_count=full_section.coverage.total_count,
        completeness=full_section.coverage.completeness,
        stop_reason=full_section.coverage.stop_reason,
        continuation=None,
    )
    next_offset = resume_offset + len(new_entries)
    root_ref_out = BundleRootRef(root_kind, root_ref, current_root.revision_id, current_root.digest)
    combined_manifest = normalize_premise_manifest(
        [d.as_dict() for d in stored_manifest]
        + [{"target_kind": k, "target_ref": r, "digest": d} for (k, r, d) in included]
    )
    if new_coverage.completeness != "complete":
        with get_conn() as conn:
            new_token = create_context_cursor(
                conn, system_id=system_id, thread_id=thread_id, root=root_ref_out,
                snapshot=BundleSnapshotRef(current_snapshot_id, None),
                bundle_digest=_compute_bundle_digest(root_ref_out, combined_manifest),
                section_id=section_id, resume_offset=next_offset,
                dependency_manifest=combined_manifest,
            )
        new_coverage = BundleCoverage(
            new_coverage.returned_count, new_coverage.total_count,
            new_coverage.completeness, new_coverage.stop_reason, new_token,
        )

    section_out = BundleSection(section_id, full_section.operation_state, new_entries, new_coverage)
    bundle = DiscussionContextBundle(
        schema_version=SCHEMA_VERSION,
        bundle_digest=_compute_bundle_digest(root_ref_out, combined_manifest),
        root=root_ref_out, snapshot=BundleSnapshotRef(current_snapshot_id, None),
        sections=(section_out,), sources=(), dependencies=combined_manifest,
        next_action=BundleNextAction(
            kind="expand_context" if new_coverage.completeness != "complete" else "none",
            target=f"{root_kind}:{root_ref}" if new_coverage.completeness != "complete" else "",
            enabled=new_coverage.completeness != "complete",
            reason="取得できていない関連情報があります。追加取得できます。" if new_coverage.completeness != "complete" else "",
        ),
    )
    return ContextExpansionResult(bundle=bundle, expanded_section_ids=(section_id,))
