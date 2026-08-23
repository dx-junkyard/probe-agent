// Issue #423 (Epic #418): the Journey Service Blueprint's Dashboard-side
// display helpers, and NOTHING semantic.
//
// Same discipline `components/ux-design/model.ts` and
// `components/stakeholder-value-network/model.ts` follow one layer over: a
// pure module (no React, no API client) that any display component calls,
// so no component re-derives a lane state, a diff, or a link's staleness on
// its own. Every `BlueprintLaneState` / `recheck_state` / `diff_state`
// arrives DECIDED by `GET /journey-blueprint` (`docs/stakeholder-value-
// network.md` §8, §0 invariant 9). What is left here is genuinely
// presentational: fixed lane order, fixed Japanese labels for every finite
// code, and small structural grouping of an already-server-decided diff.
//
// `unknown` / `not_applicable` / `unavailable` are three different
// sentences and must never share copy (§8.1 / §0 invariant 5) -- every
// label map below spells each one out separately.

import type {
  BlueprintDiffChangeKind,
  BlueprintDiffOut,
  BlueprintDiffStepEntryOut,
  BlueprintLaneCellOut,
  BlueprintLaneKind,
  BlueprintLaneState,
  BlueprintOut,
  BlueprintStepOut,
  JourneyDeliveryKind,
  UxJourneyBaselineState,
} from "@/api/types";

// --- Fixed lane order (§8.1's nine lanes, top to bottom) -------------------

export const BLUEPRINT_LANE_ORDER: readonly BlueprintLaneKind[] = [
  "stakeholder_action",
  "touchpoint",
  "frontstage",
  "backstage",
  "support",
  "external",
  "requirement",
  "evidence",
  "failure_recovery",
];

export const BLUEPRINT_LANE_LABEL: Record<BlueprintLaneKind, string> = {
  stakeholder_action: "利用者の行動",
  touchpoint: "接点(チャネル)",
  frontstage: "フロントステージ",
  backstage: "バックステージ",
  support: "サポート業務",
  external: "外部連携",
  requirement: "要件",
  evidence: "エビデンス",
  failure_recovery: "失敗と復旧",
};

/** A short parenthetical shown alongside the label -- lane identity must be
 * conveyed by label + legend, never colour alone (§7.3 / #358's rule,
 * applied here). */
export const BLUEPRINT_LANE_LEGEND: Record<BlueprintLaneKind, string> = {
  stakeholder_action: "Step の user_intent と Stakeholder の役割",
  touchpoint: "利用される Value Exchange のチャネル",
  frontstage: "利用者から見える系(system_response + frontstage 連携)",
  backstage: "裏側の処理(Requirement -> Solution Design -> Flow/Node)",
  support: "サポート業務による関与",
  external: "外部システム・外部組織との連携",
  requirement: "紐づく Requirement と受け入れ基準",
  evidence: "期待されるエビデンスと観測済みエビデンス",
  failure_recovery: "失敗モードと復旧経路",
};

export function orderedLanes(lanes: Record<string, BlueprintLaneCellOut> | null | undefined): BlueprintLaneCellOut[] {
  if (!lanes) return [];
  return BLUEPRINT_LANE_ORDER.map((kind) => lanes[kind]).filter(
    (cell): cell is BlueprintLaneCellOut => cell !== undefined,
  );
}

export function orderedSteps(blueprint: BlueprintOut | null | undefined): BlueprintStepOut[] {
  if (!blueprint) return [];
  return [...blueprint.steps].sort((a, b) => a.step_order - b.step_order);
}

// --- Lane cell state: three distinct sentences, never merged --------------

export const LANE_STATE_LABEL: Record<BlueprintLaneState, string> = {
  present: "記録あり",
  unknown: "未記録(わからない)",
  not_applicable: "対象外(構造的に適用されない)",
  unavailable: "取得できませんでした",
};

/** §0 invariant 5 / #358: never colour alone. `review` (missing/需対応)
 * gets a distinct marker text, matching the cockpit's own rule. */
export const LANE_STATE_NEEDS_ATTENTION: Record<BlueprintLaneState, boolean> = {
  present: false,
  unknown: true,
  not_applicable: false,
  unavailable: true,
};

// --- Delivery kind (lanes 3-6) ----------------------------------------------

export const DELIVERY_KIND_ORDER: readonly JourneyDeliveryKind[] = [
  "frontstage", "backstage", "support", "external",
];

export const DELIVERY_KIND_LABEL: Record<JourneyDeliveryKind, string> = {
  frontstage: "フロントステージ",
  backstage: "バックステージ",
  support: "サポート業務",
  external: "外部連携",
};

// --- §8.3 as-is/to-be diff --------------------------------------------------

export const BLUEPRINT_DIFF_CHANGE_LABEL: Record<BlueprintDiffChangeKind, string> = {
  added: "追加",
  removed: "削除",
  changed: "内容変更",
  reordered: "順序変更",
  unchanged: "変更なし",
};

export const BLUEPRINT_BASELINE_STATE_LABEL: Record<UxJourneyBaselineState, string> = {
  linked: "現状 Journey にリンクされています",
  unresolved: "リンク先の現状 Journey を解決できません",
  absent: "現状 Journey がまだ決まっていません",
  not_applicable: "この Journey に現状比較は適用されません",
};

/** `not_applicable` is never rendered as "差分なし" -- the same distinction
 * `ux-design/model.ts`'s own diff-state label draws (§4.3). */
export const BLUEPRINT_DIFF_STATE_LABEL: Record<string, string> = {
  available: "差分を表示しています",
  not_applicable: "比較対象の現状 Journey がありません",
  unavailable: "差分を取得できませんでした",
};

export function sortedDiffSteps(diff: BlueprintDiffOut | null | undefined): BlueprintDiffStepEntryOut[] {
  if (!diff) return [];
  return [...diff.steps].sort((a, b) => a.step_key.localeCompare(b.step_key));
}

/** Groups diff entries by `change_kind` in a fixed reading order --
 * additions and removals first (the biggest structural facts), then
 * content/order changes, then unchanged steps last. A group with zero
 * entries is still present so the developer can see nothing of that kind
 * changed, rather than the group silently disappearing. */
export const DIFF_CHANGE_GROUP_ORDER: readonly BlueprintDiffChangeKind[] = [
  "added", "removed", "changed", "reordered", "unchanged",
];

export interface DiffChangeGroup {
  changeKind: BlueprintDiffChangeKind;
  label: string;
  entries: BlueprintDiffStepEntryOut[];
}

export function diffChangeGroups(diff: BlueprintDiffOut | null | undefined): DiffChangeGroup[] {
  const steps = sortedDiffSteps(diff);
  return DIFF_CHANGE_GROUP_ORDER.map((kind) => ({
    changeKind: kind,
    label: BLUEPRINT_DIFF_CHANGE_LABEL[kind],
    entries: steps.filter((s) => s.change_kind === kind),
  }));
}
