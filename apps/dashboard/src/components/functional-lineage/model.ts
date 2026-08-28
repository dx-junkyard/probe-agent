// Issue #424 (Epic #418): the Functional Lineage View's Dashboard-side
// display helpers, and NOTHING semantic.
//
// Same discipline as `components/stakeholder-network/model.ts` and
// `components/journey-blueprint/model.ts` one layer over: a pure module (no
// React, no API client). Every `kind` / gap `code` / `severity` arrives
// already decided by `GET /functional-lineage`
// (`docs/stakeholder-value-network.md` §9, §0 invariant 9). What is left
// here is genuinely presentational:
//
//   1. Fixed Japanese labels for every finite value (§9.1/§9.2), one map per
//      union in `api/types.ts`.
//   2. Pure filtering over the server's own node/edge/gap arrays -- the
//      VALUES returned are the server's own objects, untouched.
//   3. A deep link from one gap's subject into the EXISTING screen that owns
//      the single next operation that would resolve it (§9.4) -- this
//      NAVIGATES, it never executes (#358/#405's rule), exactly like
//      `stakeholder-network/model.ts`'s own `refDeepLink`.
//   4. URL <-> selection-state (de)serialization, sharing the SAME
//      `ref_kind`/`ref` param pair the other two views read so all three can
//      navigate between each other without losing the selection or the
//      System scope (§9.4).
//   5. A pure downstream-only impact walk over the server's own edges
//      (§9.3) -- a re-implementation of the same forward-only BFS
//      `app.functional_lineage.trace_downstream_impact` performs server-side
//      over the same already-decided edges, not a second judgement.
//
// No score/ranking/completeness percentage is computed here (invariant 7);
// gap COUNTS are the only aggregate this module produces, and they are
// counts, never an importance measure.

import type {
  FunctionalLineageEdgeOut,
  FunctionalLineageGapOut,
  FunctionalLineageKind,
  FunctionalLineageNodeOut,
  FunctionalLineageOut,
  LineageGapCode,
  LineageGapSeverity,
} from "@/api/types";

// --- Fixed Japanese labels (§9.1/§9.2) ---------------------------------------

export const LINEAGE_KIND_LABEL: Record<FunctionalLineageKind, string> = {
  stakeholder: "Stakeholder",
  stakeholder_need: "Need",
  purpose_element: "Purpose 要素",
  purpose_relation: "Purpose 関係",
  capability: "Capability",
  value_exchange: "Value Exchange",
  ux_journey: "UX Journey",
  ux_journey_step: "Journey Step",
  ux_requirement: "Requirement",
  solution_design: "Solution Design",
  static_flow: "静的 Flow",
  runtime_flow: "実行時 Flow",
  evolution_node: "Evolution Node",
  component: "Component",
  cell_definition: "Probe Cell 定義",
  cell_binding: "Probe Cell Binding",
  probe_point: "Probe Point",
  purpose_outcome_criterion: "Outcome",
  product_objective: "Product Objective",
  product_milestone: "Milestone",
  product_gap: "Gap",
  product_feature: "Feature",
  experiment: "Experiment",
  replay_run: "Replay 実行",
};

/** §9.2's 23 gap codes, plus Issue #427 §7.3's 11. Phrased as a STATEMENT about an absent, stale, or
 * conflicting link -- never a judgement of importance (same discipline as
 * `stakeholder-network/model.ts`'s `NOTICE_CODE_LABEL`). */
export const GAP_CODE_LABEL: Record<LineageGapCode, string> = {
  stakeholder_without_role: "role が割り当てられていません",
  stakeholder_without_need: "紐づく Need がありません",
  need_without_purpose: "Purpose への参照がありません",
  need_without_exchange: "この Need を満たす Value Exchange がありません",
  need_without_journey: "Journey に到達していません",
  exchange_without_journey: "Journey / Step への参照がありません",
  exchange_without_outcome: "Outcome への参照がありません",
  journey_step_without_requirement: "Requirement への参照がありません",
  requirement_without_acceptance_criterion: "受け入れ基準がありません",
  requirement_without_design: "Solution Design がありません",
  adopted_design_without_implementation_target: "採用された案に実装先がありません",
  flow_without_node: "この Flow に紐づく Evolution Node がありません",
  node_without_flow: "この Node に紐づく Flow がありません",
  subject_without_evaluation_policy: "評価ポリシーが設定されていません",
  confirmed_without_evidence: "確定済みですが根拠がありません",
  stale_upstream: "確定した内容が変わっている可能性があります",
  stale_link: "参照している内容が変わっている可能性があります",
  stale_evidence: "根拠が古い可能性があります",
  conflicting_dependency: "依存先の Purpose 関係が競合しています",
  rejected_dependency: "依存先の Need が却下されています",
  feedback_path_missing: "提供先からの情報 (フィードバック) がありません",
  unresolved_reference: "参照先が見つかりません",
  unavailable_reference: "参照を取得できませんでした",
  objective_without_vision_ref: "Vision / Purpose / Capability / Need への参照がありません",
  objective_without_milestone: "Milestone がありません",
  milestone_without_gap: "Gap がありません",
  milestone_without_verification: "達成を確かめる方法が決まっていません",
  gap_without_journey: "この Gap を解消する Journey がありません",
  gap_source_unresolved: "検出元が見つかりません",
  gap_source_unavailable: "検出元を取得できませんでした",
  gap_source_contradicted: "検出元がもう成り立たないと言っています",
  requirement_without_feature: "この Requirement を満たす Feature がありません",
  feature_without_implementation_target: "実装先への参照がありません",
  feature_without_capability: "Capability への参照がありません",
};

/** §9.2: fixed per CODE (never per instance) -- always shown alongside the
 * severity label, never colour alone (#358). */
export const GAP_SEVERITY_LABEL: Record<LineageGapSeverity, string> = {
  blocking: "対応が必要です",
  attention: "要確認",
  informational: "参考情報",
};

// --- Gap severity legend (label + shape, never colour alone) ---------------

/** One short marker token per severity, rendered alongside the Japanese
 * label and the legend -- the "never colour alone" rule (#358) applied here
 * exactly as `stakeholder-network/model.ts`'s `EXCHANGE_KIND_LINE_STYLE`
 * does for exchange kind. */
export const GAP_SEVERITY_MARKER: Record<LineageGapSeverity, string> = {
  blocking: "■",
  attention: "▲",
  informational: "・",
};

// --- Filtering ----------------------------------------------------------------

export interface LineageFilters {
  kind: FunctionalLineageKind | null;
  gapSeverity: LineageGapSeverity | null;
}

export const EMPTY_LINEAGE_FILTERS: LineageFilters = { kind: null, gapSeverity: null };

const KIND_VALUES: FunctionalLineageKind[] = [
  "stakeholder", "stakeholder_need", "purpose_element", "purpose_relation",
  "capability", "value_exchange", "ux_journey", "ux_journey_step",
  "ux_requirement", "solution_design", "static_flow", "runtime_flow",
  "evolution_node", "component", "cell_definition", "cell_binding",
  "probe_point", "purpose_outcome_criterion",
  // Issue #427 §7.3. These must be listed here as well as in
  // `LINEAGE_KIND_LABEL`: `isLineageKind` is what lets a kind survive a URL
  // round-trip, so a kind with a label but no entry here renders fine and
  // then silently loses its selection on reload.
  "product_objective", "product_milestone", "product_gap", "product_feature",
  "experiment", "replay_run",
];
const SEVERITY_VALUES: LineageGapSeverity[] = ["blocking", "attention", "informational"];

function isLineageKind(v: string): v is FunctionalLineageKind {
  return (KIND_VALUES as string[]).includes(v);
}
function isGapSeverity(v: string): v is LineageGapSeverity {
  return (SEVERITY_VALUES as string[]).includes(v);
}

/** Reads filters + the shared selection from an already-parsed
 * `URLSearchParams`, leaving every unrelated param untouched. */
export function lineageFiltersFromSearchParams(params: URLSearchParams): LineageFilters {
  const kind = params.get("kind");
  const severity = params.get("gap_severity");
  return {
    kind: kind && isLineageKind(kind) ? kind : null,
    gapSeverity: severity && isGapSeverity(severity) ? severity : null,
  };
}

export function applyLineageFiltersToSearchParams(params: URLSearchParams, filters: LineageFilters): void {
  if (filters.kind) params.set("kind", filters.kind);
  else params.delete("kind");
  if (filters.gapSeverity) params.set("gap_severity", filters.gapSeverity);
  else params.delete("gap_severity");
}

export function filterNodes(
  nodes: readonly FunctionalLineageNodeOut[], filters: LineageFilters,
): FunctionalLineageNodeOut[] {
  if (!filters.kind) return [...nodes];
  return nodes.filter((n) => n.kind === filters.kind);
}

export function filterGaps(
  gaps: readonly FunctionalLineageGapOut[], filters: LineageFilters,
): FunctionalLineageGapOut[] {
  return gaps.filter((g) => {
    if (filters.kind && g.subject_kind !== filters.kind) return false;
    if (filters.gapSeverity && g.severity !== filters.gapSeverity) return false;
    return true;
  });
}

/** A COUNT, never a score (invariant 7) -- displayed as "N 件". */
export function gapCountBySeverity(gaps: readonly FunctionalLineageGapOut[]): Record<LineageGapSeverity, number> {
  const counts: Record<LineageGapSeverity, number> = { blocking: 0, attention: 0, informational: 0 };
  for (const g of gaps) counts[g.severity] += 1;
  return counts;
}

export function gapsForSubject(
  gaps: readonly FunctionalLineageGapOut[], kind: FunctionalLineageKind, ref: string,
): FunctionalLineageGapOut[] {
  return gaps.filter((g) => g.subject_kind === kind && g.subject_ref === ref);
}

export function nodeByRef(
  nodes: readonly FunctionalLineageNodeOut[], kind: FunctionalLineageKind | null, ref: string | null,
): FunctionalLineageNodeOut | null {
  if (kind === null || ref === null) return null;
  return nodes.find((n) => n.kind === kind && n.ref === ref) ?? null;
}

/** Three different empty-state sentences (§9.4), never sharing copy: a
 * System with genuinely nothing in the chain yet, a load that succeeded but
 * the current filters matched nothing, and a read failure -- the latter is
 * reported through `degraded_sections`, not through this state. */
export type LineageDisplayState = "no_data" | "filtered_empty" | "ready";

export function displayState(
  data: FunctionalLineageOut | undefined, filteredNodes: readonly FunctionalLineageNodeOut[],
): LineageDisplayState {
  if (!data) return "no_data";
  if (data.nodes.length === 0) return "no_data";
  if (filteredNodes.length === 0) return "filtered_empty";
  return "ready";
}

// --- §9.3's downstream-only impact traversal (a pure mirror of the
// server's `app.functional_lineage.trace_downstream_impact`) ----------------

export interface LineageImpactEntry {
  kind: FunctionalLineageKind;
  ref: string;
}

export function traceDownstreamImpact(
  edges: readonly FunctionalLineageEdgeOut[], kind: FunctionalLineageKind, ref: string,
): LineageImpactEntry[] {
  const adjacency = new Map<string, LineageImpactEntry[]>();
  for (const e of edges) {
    const key = `${e.from_kind}\u0000${e.from_ref}`;
    const list = adjacency.get(key) ?? [];
    list.push({ kind: e.to_kind, ref: e.to_ref });
    adjacency.set(key, list);
  }
  const visited = new Set<string>();
  const frontier: LineageImpactEntry[] = [{ kind, ref }];
  const result: LineageImpactEntry[] = [];
  while (frontier.length > 0) {
    const current = frontier.shift()!;
    const key = `${current.kind}\u0000${current.ref}`;
    for (const next of adjacency.get(key) ?? []) {
      const nextKey = `${next.kind}\u0000${next.ref}`;
      if (visited.has(nextKey)) continue;
      visited.add(nextKey);
      result.push(next);
      frontier.push(next);
    }
  }
  return result;
}

// --- §9.4's deep link: the single next operation that would resolve a gap --

/** A deep link into the EXISTING screen that owns the operation which would
 * resolve a gap on this subject `kind` -- this NAVIGATES, it never executes
 * (#358/#405's rule), the same contract
 * `stakeholder-network/model.ts`'s `refDeepLink` and
 * `components/improvement-loop/model.ts`'s `loopSearchParams` already keep:
 * emit each destination's OWN parameter names rather than one spelling
 * everywhere. `null` means no canonical screen exists yet for that kind. */
export function lineageDeepLink(kind: FunctionalLineageKind, ref: string): string | null {
  switch (kind) {
    case "purpose_element":
    case "purpose_relation":
    case "purpose_outcome_criterion":
      return "/system-understanding";
    case "capability":
      return "/capability-map";
    case "stakeholder":
      return `/stakeholder-value-network?node=${encodeURIComponent(ref)}`;
    case "value_exchange":
      return `/stakeholder-value-network?edge=${encodeURIComponent(ref)}`;
    case "ux_journey":
      return `/ux-design-studio?tab=journeys&journey=${encodeURIComponent(ref)}`;
    case "ux_journey_step": {
      const journeyKey = ref.split("#")[0];
      return `/journey-blueprint?journey=${encodeURIComponent(journeyKey)}`;
    }
    case "ux_requirement":
      return `/ux-design-studio?tab=requirements&requirement=${encodeURIComponent(ref)}`;
    case "solution_design":
      return `/ux-design-studio?tab=solution-designs&design=${encodeURIComponent(ref)}`;
    case "static_flow":
    case "runtime_flow":
      return `/flow-explorer?flow=${encodeURIComponent(ref)}`;
    case "evolution_node":
      return `/evolution-nodes?node=${encodeURIComponent(ref)}`;
    case "component":
      return `/components?component=${encodeURIComponent(ref)}`;
    case "cell_definition":
    case "cell_binding":
      return "/cell-fabric";
    case "probe_point":
      return "/probe-planner";
    // Issue #427 §9.4: the Objective layer lives on ONE route, with the Gap
    // Workbench as its second lane rather than a screen of its own.
    case "product_objective":
      return `/objective-map?objective=${encodeURIComponent(ref)}`;
    case "product_milestone":
      return `/objective-map?milestone=${encodeURIComponent(ref)}`;
    case "product_gap":
      return `/objective-map?view=gaps&gap=${encodeURIComponent(ref)}`;
    // A Feature has no screen of its own yet, and an Experiment / Replay run
    // reached through a Feature target link is identified here by that
    // link's `target_ref`, which is not the id those screens select on.
    // Returning null renders "no link" -- the honest answer, and the same
    // one §5.8 requires for a Gap source with no owning screen. Do not
    // substitute a plausible URL.
    case "product_feature":
    case "experiment":
    case "replay_run":
      return null;
    default:
      return null;
  }
}

/** §9.4's shared selection: the SAME `ref_kind`/`ref` param pair every view
 * of this Epic's three screens reads and writes, so navigating between them
 * never loses the selection or the System scope (the System id itself
 * travels through the existing `X-Probe-System-Id` header/context, never
 * through this param pair). */
export function readSharedSelection(params: URLSearchParams): { kind: FunctionalLineageKind | null; ref: string | null } {
  const kind = params.get("ref_kind");
  const ref = params.get("ref");
  return { kind: kind && isLineageKind(kind) ? kind : null, ref: ref ?? null };
}

export function writeSharedSelection(
  params: URLSearchParams, selection: { kind: FunctionalLineageKind | null; ref: string | null },
): void {
  if (selection.kind && selection.ref) {
    params.set("ref_kind", selection.kind);
    params.set("ref", selection.ref);
  } else {
    params.delete("ref_kind");
    params.delete("ref");
  }
}
