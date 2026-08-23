// Issue #422 (Epic #418): the Stakeholder Value Network's Dashboard-side
// display helpers, and NOTHING semantic.
//
// This module exists for the same reason `components/ux-design/model.ts`
// and `components/system-understanding/cockpit/model.ts` do: a pure module
// (no React, no API client) that any display component can call, so no
// component re-derives a state, a filter, or a classification on its own.
// `design_status` / `recheck_state` / `validity_state` / `evidence_state` /
// every notice all arrive DECIDED by `GET /stakeholder-value-network`
// (`docs/stakeholder-value-network.md` §0 invariant 9). What is left here is
// genuinely presentational:
//
//   1. Fixed Japanese labels for every finite code (§2), one map per union
//      in `api/types.ts` -- never a re-typed literal inline in a component.
//   2. Pure filtering over the server's own node/edge/notice arrays --
//      the VALUES returned are the server's own objects, untouched.
//   3. URL <-> filter-state (de)serialization, so a reload or a shared link
//      reproduces the view (§7.3).
//
// No coordinate/layout is computed here, ever (invariant 10) -- there is no
// field for it to go in. No score/ranking/centrality is computed here either
// (invariant 7); notice COUNTS are the only aggregate this module produces,
// and they are counts, not an importance measure.

import type {
  ValueNetworkAuthorshipKind,
  ValueNetworkCadence,
  ValueNetworkConsiderationState,
  ValueNetworkDesignStatus,
  ValueNetworkEdgeOut,
  ValueNetworkEvidenceState,
  ValueNetworkExchangeKind,
  ValueNetworkNodeOut,
  ValueNetworkNoticeCode,
  ValueNetworkNoticeOut,
  ValueNetworkOut,
  ValueNetworkRecheckState,
  ValueNetworkRefKind,
  ValueNetworkRefRecheckState,
  ValueNetworkRefRelationStatus,
  ValueNetworkRefTargetResolution,
  ValueNetworkStakeholderKind,
  ValueNetworkStakeholderRole,
  ValueNetworkValidityState,
} from "@/api/types";

// --- Fixed Japanese labels (§2) --------------------------------------------

export const STAKEHOLDER_KIND_LABEL: Record<ValueNetworkStakeholderKind, string> = {
  end_user: "利用者",
  customer_organization: "顧客組織",
  internal_operator: "社内運用担当",
  provider_team: "提供チーム",
  partner: "パートナー",
  regulator: "規制主体",
  other: "その他",
};

export const STAKEHOLDER_ROLE_LABEL: Record<ValueNetworkStakeholderRole, string> = {
  actor: "行為者",
  beneficiary: "受益者",
  payer: "支払者",
  operator: "運用者",
  approver: "承認者",
  supplier: "供給者",
  regulator: "規制者",
  observer: "観察者",
};

export const EXCHANGE_KIND_LABEL: Record<ValueNetworkExchangeKind, string> = {
  experience: "体験",
  service: "サービス",
  information: "情報",
  money: "金銭",
  authority: "権限",
  obligation: "義務",
  risk: "リスク",
};

/** §358 / §7.3's "never colour alone": one short line-style token per
 * `exchange_kind`, rendered alongside the label and the legend -- never
 * relied on by itself. */
export const EXCHANGE_KIND_LINE_STYLE: Record<ValueNetworkExchangeKind, string> = {
  experience: "solid",
  service: "solid",
  information: "dashed",
  money: "double",
  authority: "dotted",
  obligation: "dash-dot",
  risk: "dash-dot-dot",
};

export const DESIGN_STATUS_LABEL: Record<ValueNetworkDesignStatus, string> = {
  proposed: "提案",
  confirmed: "確定",
  rejected: "却下",
  retired: "廃止",
};

export const RECHECK_STATE_LABEL: Record<ValueNetworkRecheckState, string> = {
  current: "最新",
  stale: "再確認が必要です",
};

export const AUTHORSHIP_KIND_LABEL: Record<ValueNetworkAuthorshipKind, string> = {
  developer: "開発者",
  reasoning_model: "AI (要確認)",
};

export const EVIDENCE_STATE_LABEL: Record<ValueNetworkEvidenceState, string> = {
  available: "根拠あり",
  missing: "根拠なし",
  stale: "根拠が古い可能性があります",
  unavailable: "取得できませんでした",
};

export const VALIDITY_STATE_LABEL: Record<ValueNetworkValidityState, string> = {
  not_started: "開始前",
  active: "有効",
  ended: "終了",
  unbounded: "期限なし",
};

export const CONSIDERATION_STATE_LABEL: Record<ValueNetworkConsiderationState, string> = {
  present: "対価あり",
  none: "対価なし",
  unknown: "未確認",
};

export const CADENCE_LABEL: Record<ValueNetworkCadence, string> = {
  one_time: "一回限り",
  recurring: "定期的",
  continuous: "継続的",
  on_demand: "都度",
  unknown: "不明",
};

export const REF_KIND_LABEL: Record<ValueNetworkRefKind, string> = {
  purpose_element: "Purpose 要素",
  purpose_relation: "Purpose 関係",
  capability_entity: "Capability",
  ux_journey: "UX Journey",
  ux_journey_step: "Journey Step",
  ux_requirement: "Requirement",
  purpose_outcome_criterion: "Outcome",
  stakeholder: "Stakeholder",
  stakeholder_need: "Need",
  value_exchange: "Value Exchange",
};

export const REF_TARGET_RESOLUTION_LABEL: Record<ValueNetworkRefTargetResolution, string> = {
  resolved: "解決済み",
  unresolved: "見つかりません",
  unavailable: "取得できませんでした",
};

export const REF_RECHECK_STATE_LABEL: Record<ValueNetworkRefRecheckState, string> = {
  current: "最新",
  stale: "再確認が必要です",
  not_captured: "未記録",
};

export const REF_RELATION_STATUS_LABEL: Record<ValueNetworkRefRelationStatus, string> = {
  confirmed: "人が確定",
  proposed: "AI提案",
  derived: "自動判定",
};

/** §7.2's eleven notice codes. Each is phrased as a STATEMENT about an
 * absent or differing link, never a judgement of importance
 * (`payer_differs_from_beneficiary` / `feedback_path_missing` most of all --
 * see the server module's own note). */
export const NOTICE_CODE_LABEL: Record<ValueNetworkNoticeCode, string> = {
  stakeholder_without_exchange: "Value Exchange に登場していません",
  stakeholder_without_role: "role が割り当てられていません",
  stakeholder_without_need: "紐づく Need がありません",
  payer_differs_from_beneficiary: "対価を支払う相手と体験・サービスを受け取る相手が異なります",
  exchange_without_need: "Need への参照がありません",
  exchange_without_journey: "Journey / Step への参照がありません",
  exchange_without_outcome: "Outcome への参照がありません",
  confirmed_without_evidence: "確定済みですが根拠がありません",
  feedback_path_missing: "提供先からの情報 (フィードバック) がありません",
  stale_link: "参照している内容が変わっている可能性があります",
  stale_confirmation: "確定した内容が変わっている可能性があります",
};

// --- Filters ----------------------------------------------------------------

export interface ValueNetworkFilters {
  exchangeKind: ValueNetworkExchangeKind | null;
  role: ValueNetworkStakeholderRole | null;
  designStatus: ValueNetworkDesignStatus | null;
  staleOnly: boolean;
}

export const EMPTY_FILTERS: ValueNetworkFilters = {
  exchangeKind: null,
  role: null,
  designStatus: null,
  staleOnly: false,
};

const EXCHANGE_KIND_VALUES: ValueNetworkExchangeKind[] = [
  "experience", "service", "information", "money", "authority", "obligation", "risk",
];
const ROLE_VALUES: ValueNetworkStakeholderRole[] = [
  "actor", "beneficiary", "payer", "operator", "approver", "supplier", "regulator", "observer",
];
const DESIGN_STATUS_VALUES: ValueNetworkDesignStatus[] = ["proposed", "confirmed", "rejected", "retired"];

function isExchangeKind(v: string): v is ValueNetworkExchangeKind {
  return (EXCHANGE_KIND_VALUES as string[]).includes(v);
}
function isRole(v: string): v is ValueNetworkStakeholderRole {
  return (ROLE_VALUES as string[]).includes(v);
}
function isDesignStatus(v: string): v is ValueNetworkDesignStatus {
  return (DESIGN_STATUS_VALUES as string[]).includes(v);
}

/** Reads the four filter params from an already-parsed `URLSearchParams`,
 * leaving every unrelated param untouched (the caller keeps its own copy). */
export function filtersFromSearchParams(params: URLSearchParams): ValueNetworkFilters {
  const kind = params.get("exchange_kind");
  const role = params.get("role");
  const status = params.get("design_status");
  return {
    exchangeKind: kind && isExchangeKind(kind) ? kind : null,
    role: role && isRole(role) ? role : null,
    designStatus: status && isDesignStatus(status) ? status : null,
    staleOnly: params.get("stale_only") === "1",
  };
}

/** Applies `filters` onto `params` IN PLACE, clearing a filter's key when it
 * is unset rather than writing an empty string -- so a cleared filter
 * disappears from the URL instead of lingering as `?exchange_kind=`. */
export function applyFiltersToSearchParams(params: URLSearchParams, filters: ValueNetworkFilters): void {
  if (filters.exchangeKind) params.set("exchange_kind", filters.exchangeKind);
  else params.delete("exchange_kind");
  if (filters.role) params.set("role", filters.role);
  else params.delete("role");
  if (filters.designStatus) params.set("design_status", filters.designStatus);
  else params.delete("design_status");
  if (filters.staleOnly) params.set("stale_only", "1");
  else params.delete("stale_only");
}

function nodeIsStale(node: ValueNetworkNodeOut): boolean {
  return node.recheck_state === "stale";
}

function edgeIsStale(edge: ValueNetworkEdgeOut): boolean {
  return edge.recheck_state === "stale" || edge.validity_state === "ended";
}

export function filterNodes(
  nodes: readonly ValueNetworkNodeOut[], filters: ValueNetworkFilters,
): ValueNetworkNodeOut[] {
  return nodes.filter((n) => {
    if (filters.role && !n.roles.includes(filters.role)) return false;
    if (filters.designStatus && n.design_status !== filters.designStatus) return false;
    if (filters.staleOnly && !nodeIsStale(n)) return false;
    return true;
  });
}

export function filterEdges(
  edges: readonly ValueNetworkEdgeOut[], filters: ValueNetworkFilters,
): ValueNetworkEdgeOut[] {
  return edges.filter((e) => {
    if (filters.exchangeKind && e.exchange_kind !== filters.exchangeKind) return false;
    if (filters.designStatus && e.design_status !== filters.designStatus) return false;
    if (filters.staleOnly && !edgeIsStale(e)) return false;
    return true;
  });
}

/** A node survives an edge filter when it is unfiltered itself AND still
 * touches at least one surviving edge, OR when no edge-shaped filter is
 * active at all (so an isolated Stakeholder with no Exchange stays visible
 * -- `stakeholder_without_exchange` must remain something the developer can
 * actually select, not a row the filter silently removes). */
export function visibleNodeKeys(
  filteredNodes: readonly ValueNetworkNodeOut[],
  filteredEdges: readonly ValueNetworkEdgeOut[],
  filters: ValueNetworkFilters,
): Set<string> {
  const keys = new Set(filteredNodes.map((n) => n.stakeholder_key));
  const edgeFilterActive = filters.exchangeKind !== null;
  if (!edgeFilterActive) return keys;
  const touched = new Set<string>();
  for (const e of filteredEdges) {
    touched.add(e.provider_stakeholder_key);
    touched.add(e.receiver_stakeholder_key);
  }
  return new Set([...keys].filter((k) => touched.has(k)));
}

export function noticesForSubject(
  notices: readonly ValueNetworkNoticeOut[], subjectKind: "stakeholder" | "value_exchange", subjectKey: string,
): ValueNetworkNoticeOut[] {
  return notices.filter((n) => n.subject_kind === subjectKind && n.subject_key === subjectKey);
}

/** A COUNT, never a score (invariant 7) -- displayed as "N 件", not turned
 * into a percentage or a ranking. */
export function noticeCountByCode(notices: readonly ValueNetworkNoticeOut[]): Map<ValueNetworkNoticeCode, number> {
  const counts = new Map<ValueNetworkNoticeCode, number>();
  for (const n of notices) counts.set(n.code, (counts.get(n.code) ?? 0) + 1);
  return counts;
}

export function edgesForNode(
  edges: readonly ValueNetworkEdgeOut[], stakeholderKey: string,
): { outgoing: ValueNetworkEdgeOut[]; incoming: ValueNetworkEdgeOut[] } {
  return {
    outgoing: edges.filter((e) => e.provider_stakeholder_key === stakeholderKey),
    incoming: edges.filter((e) => e.receiver_stakeholder_key === stakeholderKey),
  };
}

export function nodeByKey(
  nodes: readonly ValueNetworkNodeOut[], stakeholderKey: string | null,
): ValueNetworkNodeOut | null {
  if (stakeholderKey === null) return null;
  return nodes.find((n) => n.stakeholder_key === stakeholderKey) ?? null;
}

export function edgeByKey(
  edges: readonly ValueNetworkEdgeOut[], exchangeKey: string | null,
): ValueNetworkEdgeOut | null {
  if (exchangeKey === null) return null;
  return edges.find((e) => e.exchange_key === exchangeKey) ?? null;
}

/** Three different empty-state sentences (§7.3): a System with genuinely no
 * Stakeholders, a System whose data loaded but the current filters matched
 * nothing, and a read failure. Never share copy. */
export type ValueNetworkDisplayState = "no_data" | "filtered_empty" | "ready";

export function displayState(
  data: ValueNetworkOut | undefined, filteredNodes: readonly ValueNetworkNodeOut[],
): ValueNetworkDisplayState {
  if (!data) return "no_data";
  if (data.nodes.length === 0) return "no_data";
  if (filteredNodes.length === 0) return "filtered_empty";
  return "ready";
}

/** A deep link into the existing canonical screen for one `ref_kind` -- this
 * NAVIGATES, it never executes (#358 / #405's rule); it is display routing,
 * not a semantic decision. `null` means no canonical screen exists yet for
 * that kind from here. */
export function refDeepLink(refKind: ValueNetworkRefKind, targetRef: string): string | null {
  switch (refKind) {
    case "purpose_element":
    case "purpose_relation":
      return "/system-understanding";
    case "capability_entity":
      return "/capability-map";
    case "ux_journey":
    case "ux_journey_step": {
      const journeyKey = targetRef.split("#")[0];
      return `/ux-design-studio?tab=journeys&journey=${encodeURIComponent(journeyKey)}`;
    }
    case "ux_requirement":
      return `/ux-design-studio?tab=requirements&requirement=${encodeURIComponent(targetRef)}`;
    case "purpose_outcome_criterion":
      return "/system-understanding";
    case "stakeholder":
    case "stakeholder_need":
    case "value_exchange":
      return null; // resolved within this same screen -- no external navigation needed
    default:
      return null;
  }
}
