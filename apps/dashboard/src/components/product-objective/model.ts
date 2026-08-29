// Issue #432 (Epic #427): the Objective Map / Gap Workbench Dashboard-side
// display helpers, and NOTHING semantic.
//
// `docs/product-objective-lineage.md` §9 (plus §0) is the canonical contract.
// Same discipline as `components/functional-lineage/model.ts` and
// `components/ux-design/model.ts` one layer over: a pure module (no React, no
// API client). Every `objective_state` / `design_status` / `achievement` /
// `assessability` / `lifecycle` / `priority_band` / `recheck_state` /
// `source_state` / `next_step` / ordering / `deep_link_state` arrives already
// decided by `GET /objective-map`, `GET /gap-workbench` and the Overview
// `objective` section (§0 invariant 10). What is left here is genuinely
// presentational:
//
//   1. Fixed Japanese labels for every finite value used on this screen, one
//      map per union in `api/types.ts`. None of these NAMES collide with
//      `components/functional-lineage/model.ts`'s `GAP_CODE_LABEL` /
//      `GAP_SEVERITY_LABEL` / `GAP_SEVERITY_MARKER` -- that file owns
//      Functional Lineage's gap vocabulary (a DIFFERENT gap concept: a lineage
//      completeness notice, not a Product Gap), and this one names its own
//      exports distinctly (§9.5).
//   2. URL <-> view-state (de)serialization for the two lanes this ONE page
//      renders (`view=objectives|gaps`) and the single-Gap deep link
//      (`gap=<gap_key>`), plus a compatibility reading of the shared
//      `ref_kind`/`ref` selection pair the other Epic #418/#405 screens
//      already use (`readSharedSelection`-style), so a future deep link INTO
//      this screen from Functional Lineage lands on the right lane without
//      this screen inventing a second selection convention.
//   3. Pure filtering over the server's own Gap Workbench entries -- the
//      VALUES returned are the server's own objects, untouched, and the
//      server's own ORDER is never changed (§0 invariant 7 / §5.7: counts may
//      be shown, never used to rank).
//   4. Structural lookups into the Objective Map tree (root list -> node ->
//      children -> milestones), so the page can do progressive disclosure
//      without re-deriving anything the server did not already say.
//   5. The two distinct empty states (§9.5): a brand-new System with no
//      Objective at all, versus a System with Objectives but no Gap ever
//      triaged.
//   6. The Overview objective card's CTA target: `OverviewObjectiveOut` names
//      a finite `next_step` KEY, not a route -- §9.3 says the CTA navigates
//      into this very screen, so this module is where that key becomes an
//      `/objective-map` URL, mirroring `components/overview/display.ts`'s
//      `targetHref` for the one target this Epic contributes.
//
// No score/ranking/completeness percentage/progress bar is computed here
// (§0 invariant 7). Gap COUNTS are the only aggregate this module produces,
// and they are counts, never an importance measure.

import type {
  GapWorkbenchEntryOut,
  GapWorkbenchOut,
  ObjectiveMapMilestoneOut,
  ObjectiveMapNodeOut,
  ObjectiveMapOut,
  OverviewObjectiveOut,
  ProductAuthorshipKind,
  ProductDeepLinkState,
  ProductDesignStatus,
  ProductGapArtifactLinkKind,
  ProductGapEffectiveTargetAvailability,
  ProductGapDecisionKind,
  ProductGapLifecycle,
  ProductGapPriorityBand,
  ProductGapReadFlag,
  ProductGapSourceKind,
  ProductGapSourceState,
  ProductMilestoneAchievement,
  ProductMilestoneAssessability,
  ProductMilestoneAssessmentKind,
  ProductMilestoneDecisionKind,
  ProductObjectiveDecisionKind,
  ProductObjectiveNextStepKey,
  ProductObjectiveNextStepState,
  ProductObjectiveState,
  ProductRecheckState,
  ProductRevisionState,
} from "@/api/types";

// --- Fixed Japanese labels (§9.5: unknown/unavailable/not_applicable/stale/
// contradicted are five different answers, by WORDS not colour alone) -------

export const OBJECTIVE_STATE_LABEL: Record<ProductObjectiveState, string> = {
  proposed: "未確定",
  confirmed: "確定済み",
  active: "活性化中",
  achieved: "達成",
  rejected: "却下",
  retired: "廃止",
};

/** Shared by Milestone.design_status and Feature.design_status -- both are
 * literally `ProductDesignStatus` (§4.2/§7.2). Deliberately NOT the same
 * label set as `ProductObjectiveState`, which has two extra values
 * (`active`/`achieved`) that Milestone/Feature definitions cannot reach. */
export const DESIGN_STATUS_LABEL: Record<ProductDesignStatus, string> = {
  proposed: "未確定",
  confirmed: "確定済み",
  rejected: "却下",
  retired: "廃止",
};

export const RECHECK_STATE_LABEL: Record<ProductRecheckState, string> = {
  current: "内容と一致",
  stale: "再確認が必要",
  not_captured: "未記録",
};

export const REVISION_STATE_LABEL: Record<ProductRevisionState, string> = {
  current: "最新",
  superseded: "旧版",
};

export const AUTHORSHIP_LABEL: Record<ProductAuthorshipKind, string> = {
  developer: "開発者",
  reasoning_model: "AI 提案",
};

/** Achievement and design_status are TWO separate labels, rendered
 * side-by-side, never merged into one badge (§1.3/§9.5). */
export const MILESTONE_ACHIEVEMENT_LABEL: Record<ProductMilestoneAchievement, string> = {
  unassessed: "未評価",
  met: "達成",
  not_met: "未達成",
  indeterminate: "判定不能",
};

export const MILESTONE_ASSESSABILITY_LABEL: Record<ProductMilestoneAssessability, string> = {
  assessable: "評価可能",
  unavailable: "評価方法が未設定",
  not_applicable: "評価対象外",
};

/** §5.3/§0 invariant 8: the four DISTINCT sentences behind
 * `ProductGapOut.effective_target_availability`. `unavailable` must never
 * render as an empty target or "目標なし" -- it means the Milestone (or its
 * current revision) could not be read, which is a fact about THIS request,
 * not about whether a target was ever set (that is `unknown`). */
export const GAP_EFFECTIVE_TARGET_AVAILABILITY_LABEL: Record<ProductGapEffectiveTargetAvailability, string> = {
  own: "この Gap 自身の目標状態",
  resolved: "Milestone の目標状態を継承",
  unavailable: "Milestone の目標状態を取得できませんでした",
  unknown: "まだ決めていません",
};

export const GAP_LIFECYCLE_LABEL: Record<ProductGapLifecycle, string> = {
  open: "未対応",
  acknowledged: "確認済み",
  deferred: "保留中",
  resolved: "解消",
  rejected: "却下",
  obsolete: "対象外",
};

export const GAP_PRIORITY_BAND_LABEL: Record<ProductGapPriorityBand, string> = {
  unset: "未設定",
  watch: "watch",
  next: "next",
  now: "now",
};

export const GAP_SOURCE_KIND_LABEL: Record<ProductGapSourceKind, string> = {
  manual: "手動記録",
  system_understanding_gap: "System Understanding の Gap",
  understanding_review_gap: "理解レビューの指摘",
  understanding_claim_change: "理解内容の変化",
  functional_lineage_gap: "Functional Lineage の Gap",
  value_network_notice: "Stakeholder Value Network の notice",
  journey_baseline_diff: "Journey の as-is/to-be 差分",
  requirement_diff: "Requirement の版差分",
  capability_drift: "Capability の drift",
  runtime_alignment_mismatch: "実態チェックの不一致",
  node_anomaly: "Evolution Node の anomaly",
  joint_understanding_open: "共同理解セッション",
  inquiry_unresolved: "未解決の Inquiry",
  issue_draft: "Issue Draft",
};

/** §0 invariant 8's five distinct answers, applied to a Gap source
 * (§5.4/§5.10). `contradicted` and `disappeared` are deliberately different
 * sentences -- the former is the detector itself saying the condition no
 * longer holds, the latter is the detector simply not finding the reference
 * any more; the developer's next step differs (§5.4.1). */
export const GAP_SOURCE_STATE_LABEL: Record<ProductGapSourceState, string> = {
  current: "検出元と一致",
  changed: "検出元の内容が変わっています",
  contradicted: "検出元はもう成り立たないと言っています",
  disappeared: "検出元が見つかりません",
  unavailable: "検出元を取得できませんでした",
};

/** Read-time-only advisory flags (§6) -- never a lifecycle value. */
export const GAP_READ_FLAG_LABEL: Record<ProductGapReadFlag, string> = {
  recheck_required: "検出元の内容が変わったため再確認が必要です",
  reopen_candidate: "検出元はもう成り立たないため reopen ではなく解消を検討できます",
  close_candidate: "検出元はもう成り立たないため解消を検討できます",
};

export const DEEP_LINK_STATE_LABEL: Record<ProductDeepLinkState, string> = {
  available: "対応する画面があります",
  unavailable: "対応する画面はまだありません",
};

export const GAP_DECISION_LABEL: Record<ProductGapDecisionKind, string> = {
  acknowledge: "確認する",
  defer: "保留する",
  resolve: "解消する",
  reject: "却下する",
  retire: "対象外にする",
  reopen: "reopen する",
  prioritize: "優先バンドを設定する",
};

/** §4.3's Objective decision vocabulary (D: Objective Map's own
 * confirm/activate/achieve/reject/retire/reinstate form). Every kind is
 * offered regardless of the Objective's current state -- an illegal
 * transition comes back as the server's own `product_objective_not_decidable`
 * (§0 invariant 10: no second legality table here). */
export const OBJECTIVE_DECISION_LABEL: Record<ProductObjectiveDecisionKind, string> = {
  confirm: "確定する",
  activate: "活性化する",
  achieve: "達成として記録する",
  reject: "却下する",
  retire: "廃止する",
  reinstate: "再提案に戻す",
};

/** §4.3's Milestone DEFINITION decision vocabulary -- separate from
 * `MILESTONE_ASSESSMENT_ACTION_LABEL`, which judges ACHIEVEMENT (§1.3). */
export const MILESTONE_DECISION_LABEL: Record<ProductMilestoneDecisionKind, string> = {
  confirm: "確定する",
  reject: "却下する",
  retire: "廃止する",
  reinstate: "再提案に戻す",
};

/** §4.3's Milestone ACHIEVEMENT assessment vocabulary. Deliberately a
 * DIFFERENT label set from `MILESTONE_ACHIEVEMENT_LABEL` (which labels the
 * derived STATE `unassessed`/`met`/`not_met`/`indeterminate`) even though
 * three values overlap in spelling -- these are the ACTIONS a developer
 * takes to record that state, plus `withdraw`, which has no achievement
 * state of its own. */
export const MILESTONE_ASSESSMENT_ACTION_LABEL: Record<ProductMilestoneAssessmentKind, string> = {
  met: "達成と判定する",
  not_met: "未達成と判定する",
  indeterminate: "判定不能として記録する",
  withdraw: "判定を取り下げる",
};

/** §5.11: `ux_journey` is deliberately absent -- a Gap's Journey connection
 * has exactly one writable home, `ux_journey_upstream_ref
 * (ref_kind='product_gap')`, written through the Journey's own endpoint
 * (`useLinkProductGapToJourney`), never through this artifact-link table. */
export const GAP_ARTIFACT_LINK_KIND_LABEL: Record<ProductGapArtifactLinkKind, string> = {
  issue_draft: "Issue Draft",
  ux_requirement: "UX Requirement",
  product_feature: "Feature",
  solution_design: "Solution Design",
};

/** Every `*_decision_stale_digest` rejection code (§10.1) shares one shape:
 * the developer's submitted `captured_digest` no longer matches the current
 * content. A pure string check, not an API concern -- lets every decision/
 * assessment form (Objective/Milestone/Gap) share one recoverable-error
 * rendering without a second definition of what "stale" means per entity. */
export function isStaleDigestErrorCode(code: string | undefined): boolean {
  return !!code && code.endsWith("_decision_stale_digest");
}

/** §9.3's 15-key next-step vocabulary, for the Overview objective card's CTA
 * label ONLY -- the KEY, state, reason/completion/value text all arrive
 * already decided (`OverviewObjectiveOut`); this is the one thing the server
 * does not supply a label for. `unavailable` / `none` never render a CTA at
 * all (handled by `objectiveNextStepHasAction`, not by this map). */
export const NEXT_STEP_CTA_LABEL: Record<ProductObjectiveNextStepKey, string> = {
  unavailable: "",
  confirm_vision: "Vision を確認する",
  create_objective: "Objective を作成する",
  confirm_objective: "Objective を確認する",
  activate_objective: "Objective を活性化する",
  create_milestone: "Milestone を作成する",
  confirm_milestone: "Milestone を確認する",
  recheck_stale_decision: "確定内容を再確認する",
  review_gap_source: "Gap の検出元を確認する",
  create_gap: "Gap を作成する",
  prioritize_gap: "Gap に優先バンドを設定する",
  link_gap_to_journey: "Gap を Journey へ関連付ける",
  link_requirement_to_feature: "Requirement を Feature へ関連付ける",
  assess_milestone: "Milestone の達成を判定する",
  none: "",
};

// --- Objective Map lane: URL view state -------------------------------------

export type ProductObjectiveView = "objectives" | "gaps";

export interface ProductObjectiveMapSelection {
  view: ProductObjectiveView;
  objectiveKey: string | null;
  milestoneKey: string | null;
  gapKey: string | null;
}

const SHARED_REF_KIND_TO_VIEW: Record<string, ProductObjectiveView> = {
  product_objective: "objectives",
  product_milestone: "objectives",
  product_gap: "gaps",
};

/**
 * Reads `view` / `objective` / `milestone` / `gap` from an already-parsed
 * `URLSearchParams`, leaving every unrelated param untouched -- the same
 * discipline `functional-lineage/model.ts`'s `lineageFiltersFromSearchParams`
 * follows.
 *
 * Also honours an INCOMING `ref_kind`/`ref` pair (the shared cross-view
 * selection convention `readSharedSelection`/`writeSharedSelection` already
 * establish across Functional Lineage / Stakeholder Value Network / UX
 * Design Studio, §9.4) when this screen's OWN params are absent, so a future
 * deep link naming `product_objective` / `product_milestone` / `product_gap`
 * lands on the right lane and selection without this screen needing a
 * second incompatible convention.
 */
export function objectiveMapSelectionFromSearchParams(
  params: URLSearchParams,
): ProductObjectiveMapSelection {
  const rawView = params.get("view");
  const objectiveKey = params.get("objective");
  const milestoneKey = params.get("milestone");
  const gapKey = params.get("gap");

  if (rawView === "objectives" || rawView === "gaps") {
    return { view: rawView, objectiveKey, milestoneKey, gapKey };
  }

  const refKind = params.get("ref_kind");
  const ref = params.get("ref");
  if (refKind && ref && refKind in SHARED_REF_KIND_TO_VIEW) {
    const view = SHARED_REF_KIND_TO_VIEW[refKind];
    if (refKind === "product_gap") return { view, objectiveKey: null, milestoneKey: null, gapKey: ref };
    if (refKind === "product_milestone") return { view, objectiveKey: null, milestoneKey: ref, gapKey: null };
    return { view, objectiveKey: ref, milestoneKey: null, gapKey: null };
  }

  return { view: "objectives", objectiveKey, milestoneKey, gapKey };
}

export function applyObjectiveMapSelectionToSearchParams(
  params: URLSearchParams,
  selection: ProductObjectiveMapSelection,
): void {
  params.delete("ref_kind");
  params.delete("ref");
  if (selection.view === "gaps") params.set("view", "gaps");
  else params.delete("view");

  if (selection.objectiveKey) params.set("objective", selection.objectiveKey);
  else params.delete("objective");

  if (selection.milestoneKey) params.set("milestone", selection.milestoneKey);
  else params.delete("milestone");

  if (selection.gapKey) params.set("gap", selection.gapKey);
  else params.delete("gap");
}

// --- Objective Map lane: structural lookups (no re-derivation) -------------

/** `root_objective_ids` in the SERVER's own order (§0 invariant 7 -- never
 * re-sorted here). */
export function objectiveMapRoots(map: ObjectiveMapOut): ObjectiveMapNodeOut[] {
  const byId = new Map(map.nodes.map((n) => [n.id, n]));
  return map.root_objective_ids.map((id) => byId.get(id)).filter((n): n is ObjectiveMapNodeOut => n != null);
}

export function objectiveMapChildren(map: ObjectiveMapOut, node: ObjectiveMapNodeOut): ObjectiveMapNodeOut[] {
  const byId = new Map(map.nodes.map((n) => [n.id, n]));
  return node.child_objective_ids.map((id) => byId.get(id)).filter((n): n is ObjectiveMapNodeOut => n != null);
}

export function objectiveMapNodeByKey(map: ObjectiveMapOut, objectiveKey: string | null): ObjectiveMapNodeOut | null {
  if (!objectiveKey) return null;
  return map.nodes.find((n) => n.objective_key === objectiveKey) ?? null;
}

export function objectiveMapMilestoneByKey(
  map: ObjectiveMapOut,
  milestoneKey: string | null,
): { node: ObjectiveMapNodeOut; milestone: ObjectiveMapMilestoneOut } | null {
  if (!milestoneKey) return null;
  for (const node of map.nodes) {
    const milestone = node.milestones.find((m) => m.milestone_key === milestoneKey);
    if (milestone) return { node, milestone };
  }
  return null;
}

/** §3.2/§5.5: `GET /objective-map` degrades PER MILESTONE --
 * `degraded_sections` carries `gaps:<milestone_key>` when that one
 * Milestone's own Gap read failed -- and substitutes an all-zero
 * `gap_summary` so the response shape stays valid. An all-zero summary is
 * therefore ambiguous by itself ("no Gaps" vs "could not read Gaps"); this
 * is the one place that disambiguates it, by consulting the SAME
 * `degraded_sections` list the page's own banner already reads. Never
 * render a Milestone's `gap_summary` counts without checking this first
 * (§0 invariant 8 / §9.5 -- unavailable must never render as 0 件). */
export function isMilestoneGapSummaryUnavailable(map: ObjectiveMapOut, milestoneKey: string): boolean {
  return map.degraded_sections.includes(`gaps:${milestoneKey}`);
}

/** Shared copy for every place a Milestone/Objective Gap count could not be
 * read -- never "0 件", never silently omitted (§3.2). */
export const GAP_SUMMARY_UNAVAILABLE_LABEL = "Gap 情報を取得できませんでした";

/** A COUNT across every Milestone's `gap_summary`, never a score (§0
 * invariant 7) -- displayed as "N 件", never used to reorder anything.
 * `unavailable` is true when at least one Milestone's own summary could not
 * be read (`isMilestoneGapSummaryUnavailable`): that Milestone's zero is
 * EXCLUDED from `count` rather than folded in as a real zero, and the
 * caller must render the "取得できませんでした" sentence instead of a number
 * that would otherwise silently understate the real total (§3.2). */
export function objectiveGapTotal(
  map: ObjectiveMapOut, node: ObjectiveMapNodeOut,
): { count: number; unavailable: boolean } {
  let count = 0;
  let unavailable = false;
  for (const m of node.milestones) {
    if (isMilestoneGapSummaryUnavailable(map, m.milestone_key)) { unavailable = true; continue; }
    const s = m.gap_summary;
    count += s.open_count + s.acknowledged_count + s.deferred_count
      + s.resolved_count + s.rejected_count + s.obsolete_count;
  }
  return { count, unavailable };
}

/**
 * §3.1: the set of Objective keys that must be force-expanded to reveal a
 * selected Objective or Milestone -- the node itself (if an Objective is
 * selected, or the node owning the selected Milestone) plus every ancestor
 * up to the root, walked via `parent_objective_key`. Pure structural lookup,
 * no re-derivation of anything the server decided; the CALLER (`ObjectiveTree`)
 * decides what "force expand" means for already-collapsed nodes (§3.1's
 * documented rule: a new selection always re-opens its ancestor path, even
 * over a manual collapse, and never touches unrelated nodes).
 */
export function objectiveMapForceExpandedKeys(
  map: ObjectiveMapOut,
  selectedObjectiveKey: string | null,
  selectedMilestoneKey: string | null,
): string[] {
  const byKey = new Map(map.nodes.map((n) => [n.objective_key, n]));
  const keys = new Set<string>();
  function addPath(startKey: string | null) {
    let current = startKey ? byKey.get(startKey) ?? null : null;
    while (current) {
      keys.add(current.objective_key);
      current = current.parent_objective_key ? byKey.get(current.parent_objective_key) ?? null : null;
    }
  }
  addPath(selectedObjectiveKey);
  if (selectedMilestoneKey) {
    const found = objectiveMapMilestoneByKey(map, selectedMilestoneKey);
    if (found) addPath(found.node.objective_key);
  }
  return [...keys];
}

/** §9.5's two distinct empty states: a brand-new System with no Objective at
 * all, versus a System with Objectives but no Gap ever triaged. Never shares
 * copy with a load failure, which the caller reports through
 * `degraded_sections` instead. */
export type ProductObjectiveEmptyState = "no_objective" | "no_gap" | "ready";

export function objectiveMapEmptyState(map: ObjectiveMapOut, workbench: GapWorkbenchOut | null): ProductObjectiveEmptyState {
  if (map.nodes.length === 0) return "no_objective";
  if (workbench && workbench.entries.length === 0) return "no_gap";
  return "ready";
}

// --- Gap Workbench lane: pure filtering (server order preserved) -----------

export interface GapWorkbenchFilters {
  objectiveKey: string | null;
  milestoneKey: string | null;
  lifecycle: ProductGapLifecycle | null;
}

export const EMPTY_GAP_WORKBENCH_FILTERS: GapWorkbenchFilters = {
  objectiveKey: null,
  milestoneKey: null,
  lifecycle: null,
};

/** Filters the server's own `entries` array; the ORDER of the entries that
 * remain is exactly the server's order (§5.7 -- priority_band -> lifecycle ->
 * sequence_hint -> gap_key -- never re-sorted client-side). */
export function filterGapWorkbenchEntries(
  entries: readonly GapWorkbenchEntryOut[],
  filters: GapWorkbenchFilters,
): GapWorkbenchEntryOut[] {
  return entries.filter((e) => {
    if (filters.objectiveKey && e.objective_key !== filters.objectiveKey) return false;
    if (filters.milestoneKey && e.milestone_key !== filters.milestoneKey) return false;
    if (filters.lifecycle && e.lifecycle !== filters.lifecycle) return false;
    return true;
  });
}

export function gapWorkbenchEntryByKey(
  workbench: GapWorkbenchOut, gapKey: string | null,
): GapWorkbenchEntryOut | null {
  if (!gapKey) return null;
  return workbench.entries.find((e) => e.gap_key === gapKey) ?? null;
}

/** "この検出元を参照している他の Gap" (§5.2/§9.2's federation read):
 * looks up the `shared_sources` bucket for one of this entry's own
 * `(source_kind, source_ref)` pairs and returns the OTHER gap_keys in it. */
export function sharedGapKeysForSource(
  workbench: GapWorkbenchOut, sourceKind: ProductGapSourceKind, sourceRef: string, excludingGapKey: string,
): string[] {
  const bucket = workbench.shared_sources.find(
    (s) => s.source_kind === sourceKind && s.source_ref === sourceRef,
  );
  if (!bucket) return [];
  return bucket.gap_keys.filter((k) => k !== excludingGapKey);
}

// --- Overview objective card: next_step -> Objective Map CTA target --------

/** §9.3: `waiting` / `unavailable` carry no action, and neither does the
 * terminal `none`/`complete` row -- those three render the server's own
 * sentence, never a disabled control (§9.3 / #380 pattern). Only `available`
 * gets a CTA. */
export function objectiveNextStepHasAction(state: ProductObjectiveNextStepState): boolean {
  return state === "available";
}

/**
 * The Overview objective card's ONE lead into `/objective-map` (§9.4: "The
 * Overview's `objective` セクションから Objective Map への lead を 1 本張
 * る"). The CTA NAVIGATES, it never executes -- every key below lands on a
 * lane/selection this screen can already show; nothing here performs a
 * write.
 */
export function objectiveNextStepHref(overview: OverviewObjectiveOut): string {
  const params = new URLSearchParams();
  switch (overview.next_step) {
    case "confirm_objective":
    case "activate_objective":
    case "create_milestone":
    case "confirm_milestone":
    case "recheck_stale_decision":
      if (overview.active_objective) params.set("objective", overview.active_objective.objective_key);
      break;
    case "assess_milestone":
      params.set("view", "objectives");
      if (overview.active_objective) params.set("objective", overview.active_objective.objective_key);
      if (overview.next_milestone) params.set("milestone", overview.next_milestone.milestone_key);
      break;
    case "review_gap_source":
    case "create_gap":
    case "prioritize_gap":
    case "link_gap_to_journey":
      params.set("view", "gaps");
      if (overview.primary_gap) params.set("gap", overview.primary_gap.gap_key);
      else if (overview.next_milestone) params.set("milestone", overview.next_milestone.milestone_key);
      break;
    case "link_requirement_to_feature":
      params.set("view", "gaps");
      if (overview.primary_gap) params.set("gap", overview.primary_gap.gap_key);
      break;
    case "confirm_vision":
    case "create_objective":
    case "unavailable":
    case "none":
    default:
      break;
  }
  const query = params.toString();
  return `/objective-map${query ? `?${query}` : ""}`;
}
