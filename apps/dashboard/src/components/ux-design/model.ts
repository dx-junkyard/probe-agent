// Issue #409 (Epic #405): the UX Design Studio's Dashboard-side display
// helpers, and NOTHING semantic.
//
// This module exists for the same reason `components/purpose-chain/model.ts`
// and `components/system-understanding/cockpit/model.ts` do: a pure module
// (no React, no API client) that any display component can call, so no
// component re-derives a state, a diff, or a classification on its own.
// `design_status`, `recheck_state`, `revision_state`, every ref/link state,
// the diffs, and the change-origin classification all arrive decided by
// `GET /ux-design/...` and `GET /solution-designs/...`
// (`docs/ux-design-lineage.md` §0 invariant 9). What is left here is
// genuinely presentational:
//
//   1. Fixed DISPLAY ORDER for lists whose server response is not already
//      ordered the way the screen wants to read it (Journey perspective,
//      Requirement kind, evaluation policy level).
//   2. Fixed Japanese labels for finite codes, one map per `Ux*` /
//      `Solution*` union in `api/types.ts` -- never a re-typed literal
//      inline in a component.
//   3. Small structural joins (which Requirement links to a given Journey
//      Step; which Solution Design links to a given Requirement) that are
//      array filtering over already-server-decided rows, not classification
//      -- the VALUES returned are the server's own objects, untouched.
//
// Six display states, six different sentences (§0 invariant 8 + §4.3):
// `as_is` / `to_be` (a Journey's `perspective`), `changed` (a diff entry's
// `change_kind`), `stale` (a `recheck_state` / `link_state`), `unavailable`
// (a degraded section or a read failure), `not_applicable` (a `diff_state`
// or a `baseline_state` that is structurally inapplicable). They must never
// share copy, so every label map below spells each one out separately --
// including the two flavours `not_applicable` takes for a Journey baseline
// (`greenfield` vs `undecided`), which is the one case §4.3 calls out by
// name: it must not read 「差分なし」.

import type {
  SolutionDesignDetailOut,
  SolutionDesignOut,
  SolutionDesignOptionDecision,
  SolutionDesignOptionStatus,
  SolutionDesignRequirementLinkOut,
  SolutionDesignTargetLinkOut,
  SolutionHandoffState,
  SolutionLinkStaleReason,
  SolutionLinkState,
  SolutionTargetKind,
  UxArtifactKind,
  UxArtifactVerificationState,
  UxChangeOrigin,
  UxDesignAuthorshipKind,
  UxDesignRecheckState,
  UxDesignStatus,
  UxDiffChangeKind,
  UxDiffState,
  UxEvidenceSourceKind,
  UxJourneyBaselineMode,
  UxJourneyBaselineState,
  UxJourneyOut,
  UxJourneyPerspective,
  UxJourneyRevisionOut,
  UxJourneyStepOut,
  UxRefKind,
  UxRefRecheckState,
  UxRefRelationStatus,
  UxRefTargetResolution,
  UxRequirementDetailOut,
  UxRequirementKind,
  UxRequirementOut,
  UxRequirementStepLinkOut,
  UxRevisionState,
  UxVerificationMethod,
} from "@/api/types";

// --- Fixed display order --------------------------------------------------

/** `as_is` reads before `to_be` -- the causal reading order (現状 before
 * 目標). Journeys within the same perspective sort by `journey_key`. */
export const JOURNEY_PERSPECTIVE_ORDER: readonly UxJourneyPerspective[] = ["as_is", "to_be"];

export function sortJourneys(journeys: readonly UxJourneyOut[]): UxJourneyOut[] {
  return [...journeys].sort((a, b) => {
    const pa = JOURNEY_PERSPECTIVE_ORDER.indexOf(a.perspective);
    const pb = JOURNEY_PERSPECTIVE_ORDER.indexOf(b.perspective);
    if (pa !== pb) return pa - pb;
    return a.journey_key.localeCompare(b.journey_key);
  });
}

/** `out_of_scope` sorts last -- it is what the developer explicitly decided
 * NOT to build, and reading it after the requirements that ARE in scope
 * matches how a developer scans the list. */
export const REQUIREMENT_KIND_ORDER: readonly UxRequirementKind[] = [
  "functional",
  "non_functional",
  "constraint",
  "out_of_scope",
];

export function sortRequirements(requirements: readonly UxRequirementOut[]): UxRequirementOut[] {
  return [...requirements].sort((a, b) => {
    const ka = REQUIREMENT_KIND_ORDER.indexOf(a.requirement_kind);
    const kb = REQUIREMENT_KIND_ORDER.indexOf(b.requirement_kind);
    if (ka !== kb) return ka - kb;
    return a.requirement_key.localeCompare(b.requirement_key);
  });
}

export function sortSolutionDesigns<T extends { design_key: string }>(designs: readonly T[]): T[] {
  return [...designs].sort((a, b) => a.design_key.localeCompare(b.design_key));
}

/** A Journey revision's Step sequence, in the order the Journey means them
 * to be read -- `step_order` is structural sequence, not a judgement. */
export function stepsInOrder(revision: UxJourneyRevisionOut | null | undefined): UxJourneyStepOut[] {
  if (!revision) return [];
  return [...revision.steps].sort((a, b) => a.step_order - b.step_order);
}

/** ADR-7 / §3.7: the three evaluation levels are read separately and never
 * merged into one list. This is the one fixed order the three group under;
 * `evaluationPolicyGroups` below is what keeps a level with zero entries
 * visible as an empty, still-separate group rather than disappearing. */
export const EVALUATION_LEVEL_ORDER = ["node", "flow_capability", "ux_outcome"] as const;
export type EvaluationPolicyLevel = (typeof EVALUATION_LEVEL_ORDER)[number];

export const EVALUATION_LEVEL_LABEL: Record<EvaluationPolicyLevel, string> = {
  node: "Node 評価",
  flow_capability: "Flow-Capability 評価",
  ux_outcome: "UX-Outcome 評価",
};

export interface EvaluationPolicyGroup {
  level: EvaluationPolicyLevel;
  label: string;
  items: Record<string, unknown>[];
}

/** Groups the handoff's `evaluation_policy_refs` by level, in fixed order,
 * WITHOUT flattening. A level absent from the server payload renders as an
 * empty group rather than being silently skipped -- ADR-7's separation kept
 * structural, not merely a display convention. */
export function evaluationPolicyGroups(
  refs: Record<string, Record<string, unknown>[]> | null | undefined,
): EvaluationPolicyGroup[] {
  return EVALUATION_LEVEL_ORDER.map((level) => ({
    level,
    label: EVALUATION_LEVEL_LABEL[level],
    items: refs?.[level] ?? [],
  }));
}

// --- Fixed labels: #407 Journey / Requirement vocab ------------------------

export const JOURNEY_PERSPECTIVE_LABEL: Record<UxJourneyPerspective, string> = {
  as_is: "現状 (as-is)",
  to_be: "目標 (to-be)",
};

export const JOURNEY_BASELINE_MODE_LABEL: Record<UxJourneyBaselineMode, string> = {
  linked: "現状 Journey にリンク済み",
  greenfield: "新規システムとして宣言済み(現状はありません)",
  undecided: "まだ決めていません",
};

/** `absent` is the `unknown` case in §0 invariant 8's sense: the developer
 * has not yet decided. `not_applicable` is the separate, structural case
 * (as-is 自身、または greenfield 宣言済み)。両者を同じ文言にしない。 */
export const JOURNEY_BASELINE_STATE_LABEL: Record<UxJourneyBaselineState, string> = {
  linked: "現状 Journey にリンクされています",
  unresolved: "リンク先の現状 Journey を解決できません",
  absent: "現状 Journey がまだ決まっていません",
  not_applicable: "この Journey に現状比較は適用されません",
};

export const DESIGN_STATUS_LABEL: Record<UxDesignStatus, string> = {
  proposed: "未確定",
  confirmed: "確定済み",
  rejected: "却下",
  retired: "廃止",
};

/** `stale` here is deliberately a DIFFERENT sentence from a diff entry's
 * `changed` -- one says "a decision's premise moved", the other says "this
 * specific field differs between two revisions". */
export const DESIGN_RECHECK_STATE_LABEL: Record<UxDesignRecheckState, string> = {
  current: "確定時の内容のままです",
  stale: "確定後に内容が変わりました。再確認してください",
};

export const REVISION_STATE_LABEL: Record<UxRevisionState, string> = {
  current: "最新版",
  superseded: "旧版(置き換え済み)",
};

export const AUTHORSHIP_LABEL: Record<UxDesignAuthorshipKind, string> = {
  developer: "開発者が記述",
  reasoning_model: "AI が起案",
};

export const REQUIREMENT_KIND_LABEL: Record<UxRequirementKind, string> = {
  functional: "機能要件",
  non_functional: "非機能要件",
  constraint: "制約",
  out_of_scope: "対象外(やらないと決めたこと)",
};

export const VERIFICATION_METHOD_LABEL: Record<UxVerificationMethod, string> = {
  manual_review: "人手でのレビュー",
  replay: "Replay",
  experiment: "Experiment",
  runtime_observation: "実行時観測",
  not_verifiable: "検証方法なし",
};

/** `runtime_trace` is rendered as an EXPECTATION ("観測できれば確認できる"),
 * never as an achievement -- §0 invariant 6 / §4's runtime-trace rule. The
 * other three kinds are not expectations, so they keep plain descriptive
 * copy instead of borrowing the same "確認には…" phrasing. */
export const EVIDENCE_SOURCE_LABEL: Record<UxEvidenceSourceKind, string> = {
  runtime_trace: "確認には runtime trace の観測が必要です(未確認)",
  human_report: "人からの報告で確認します",
  external_analytics: "外部の分析ツールで確認します",
  none: "確認方法は指定されていません",
};

export const ARTIFACT_KIND_LABEL: Record<UxArtifactKind, string> = {
  wireframe: "ワイヤーフレーム",
  adr: "ADR",
  spec: "仕様書",
  diagram: "図",
  research_note: "リサーチノート",
  other: "その他",
};

export const ARTIFACT_VERIFICATION_LABEL: Record<UxArtifactVerificationState, string> = {
  verified: "リポジトリ内容と照合済み",
  unverified: "未照合(開発者申告のハッシュ値)",
  unreachable: "以前は照合できましたが、現在の断面では解決できません",
};

export const REF_KIND_LABEL: Record<UxRefKind, string> = {
  purpose_element: "Purpose 要素",
  purpose_relation: "Purpose 関係",
  capability_entity: "Capability",
};

export const REF_RELATION_STATUS_LABEL: Record<UxRefRelationStatus, string> = {
  confirmed: "人が明示的に接続を確定",
  proposed: "AI が起案(未確定)",
  derived: "構造的に導出",
};

export const REF_TARGET_RESOLUTION_LABEL: Record<UxRefTargetResolution, string> = {
  resolved: "参照先を解決できました",
  unresolved: "正本は読めましたが、参照先が見つかりません",
  unavailable: "正本自体を読み取れませんでした",
};

/** `not_captured` is the fail-closed `unknown` case (§0 invariant 8):
 * digest が一度も捕捉されなかった legacy 行。`current` として扱わない。 */
export const REF_RECHECK_STATE_LABEL: Record<UxRefRecheckState, string> = {
  current: "参照時点から変わっていません",
  stale: "参照先の内容が変わりました",
  not_captured: "内容の捕捉記録がありません(現在扱いにはできません)",
};

export const DIFF_CHANGE_KIND_LABEL: Record<UxDiffChangeKind, string> = {
  added: "追加されました",
  removed: "削除されました",
  changed: "変更されました",
  unchanged: "変更はありません",
};

export const CHANGE_ORIGIN_LABEL: Record<UxChangeOrigin, string> = {
  purpose: "Purpose の内容が変わりました",
  capability: "Capability が変わりました",
  journey: "Journey の内容が変わりました",
  requirement: "Requirement の内容が変わりました",
  solution_design: "Solution Design の内容が変わりました",
  implementation_target: "実装対象そのものが変わりました",
  snapshot: "コードの断面が変わりました",
};

// --- Fixed labels: #408 Solution Design vocab -------------------------------

export const OPTION_STATUS_LABEL: Record<SolutionDesignOptionStatus, string> = {
  draft: "草案",
  adopted: "採用済み",
  held: "保留",
  rejected: "却下",
  withdrawn: "取り下げ済み",
};

export const OPTION_DECISION_LABEL: Record<SolutionDesignOptionDecision, string> = {
  adopt: "採用する",
  hold: "保留する",
  reject: "却下する",
  withdraw: "取り下げる",
};

export const TARGET_KIND_LABEL: Record<SolutionTargetKind, string> = {
  capability: "Capability",
  static_flow: "静的 Flow(snapshot 固定)",
  runtime_flow: "実行時 Flow(SDK 相関 ID)",
  evolution_node: "Evolution Node",
  component: "Component",
  cell_definition: "Cell 定義",
  cell_binding: "Cell Binding",
  probe_point: "Probe Point",
};

export const LINK_STATE_LABEL: Record<SolutionLinkState, string> = {
  current: "現在の内容のままです",
  stale: "参照元または参照先が変わりました",
  unresolved: "正本は読めましたが、対象が見つかりません",
  unavailable: "正本自体を読み取れませんでした",
};

export const LINK_STALE_REASON_LABEL: Record<SolutionLinkStaleReason, string> = {
  requirement_changed: "Requirement の内容が変わりました",
  design_changed: "Solution Design の内容が変わりました",
  target_changed: "実装対象の内容が変わりました",
  snapshot_changed: "コードの断面が変わりました",
  upstream_changed: "上流(Journey/Purpose)が変わりました",
};

export const HANDOFF_STATE_LABEL: Record<SolutionHandoffState, string> = {
  complete: "参照はすべて解決できています",
  incomplete: "解決できない参照があります",
  unavailable: "handoff を取得できませんでした",
};

export const DIFF_STATE_LABEL: Record<Exclude<UxDiffState, "available">, string> = {
  not_applicable: "この比較は適用されません",
  unavailable: "差分を取得できませんでした",
};

/**
 * §4.3's named example: a `baseline-diff` whose `diff_state` is
 * `not_applicable` must NOT render 「差分なし」, and the two reasons a
 * baseline can be absent (`greenfield` を宣言済み or まだ `undecided`) read as
 * two different sentences -- a declared decision is not the same fact as an
 * undecided one.
 */
export function baselineDiffUnavailableMessage(baselineMode: UxJourneyBaselineMode): string {
  if (baselineMode === "greenfield") {
    return "比較対象の現状 Journey がありません(新規システムとして宣言済み)";
  }
  return "比較対象の現状 Journey がありません(現状 Journey がまだ設定されていません)";
}

// --- Structural joins (array filtering over server-decided rows only) ------

export interface StepRequirementLink {
  requirement: UxRequirementDetailOut;
  link: UxRequirementStepLinkOut;
}

/**
 * Which Requirements link to one specific Journey Step, given the already
 * server-decided detail of every Requirement in the System. This is a
 * structural join on `(journey_id, step_key)` -- exactly the "which relation
 * connects two given elements" precedent `purpose-chain/model.ts` documents
 * -- not a re-classification: `link.link_state` etc. are untouched.
 *
 * Superseded links (a correction that replaced this one) are excluded; only
 * the current standing link for a given `(requirement, step_key)` pair is
 * shown, matching how the server itself treats `superseded_by_id`.
 */
export function requirementsForStep(
  requirementDetails: readonly UxRequirementDetailOut[],
  journeyId: number,
  stepKey: string,
): StepRequirementLink[] {
  const out: StepRequirementLink[] = [];
  for (const requirement of requirementDetails) {
    for (const link of requirement.step_links) {
      if (link.journey_id === journeyId && link.step_key === stepKey && link.superseded_by_id === null) {
        out.push({ requirement, link });
      }
    }
  }
  return out.sort((a, b) => a.requirement.requirement_key.localeCompare(b.requirement.requirement_key));
}

export interface RequirementSolutionLink {
  design: SolutionDesignDetailOut;
  link: SolutionDesignRequirementLinkOut;
}

/**
 * Which Solution Designs link to one specific Requirement, given the
 * already server-decided detail of every Solution Design in the System.
 * Same discipline as `requirementsForStep`: structural join only.
 */
export function solutionDesignsForRequirement(
  designDetails: readonly SolutionDesignDetailOut[],
  requirementId: number,
): RequirementSolutionLink[] {
  const out: RequirementSolutionLink[] = [];
  for (const design of designDetails) {
    for (const link of design.requirement_links) {
      if (link.requirement_id === requirementId && link.superseded_by_id === null) {
        out.push({ design, link });
      }
    }
  }
  return out.sort((a, b) => a.design.design_key.localeCompare(b.design.design_key));
}

/** The adopted Option's target links only -- "adopted, never applied" (§3.6):
 * this reads `option_status === "adopted"`, a value the server already
 * decided, and does not infer adoption from anything else (e.g. presence of
 * a decision row). */
export function targetLinksForAdoptedOption(
  design: SolutionDesignDetailOut,
): { optionKey: string; links: SolutionDesignTargetLinkOut[] } | null {
  const adopted = design.options.find(
    (o) => o.option_status === "adopted" && o.option_key === design.adopted_option_key,
  );
  if (!adopted) return null;
  return {
    optionKey: adopted.option_key,
    links: design.target_links.filter((l) => l.option_id === adopted.id && l.superseded_by_id === null),
  };
}

/** Whether a Requirement (by its current revision's `requirement_kind`) may
 * take an implementation target link -- mirrors the server's own 422
 * `out_of_scope_requirement_not_implementable` (§3.3) so the Studio never
 * offers a target-link action the server would refuse. Reads the kind the
 * server already returned; does not infer scope from anything else. */
export function requirementAcceptsTargetLink(requirement: UxRequirementOut): boolean {
  return requirement.requirement_kind !== "out_of_scope";
}

// --- §4.2's single 「今決めるべきこと」 ------------------------------------
//
// One decision at a time, chosen by a FIRST-MATCH table over values the
// server already decided (`design_status` / `recheck_state` /
// `adopted_option_key` / `option_count`) -- the same shape #380's
// `decide_next_action` and #358's `CockpitModel.nextStep` use. Nothing here
// re-derives a state: every input is a field read straight off a list
// response.
//
// Two rules this table must keep:
//
//   * The CTA NAVIGATES, never executes (§4.1 / #358). It selects a tab and
//     an entity; the operation itself still happens in that entity's own
//     panel, so the screen never grows a second primary action.
//   * `unavailable` and "everything is settled" carry NO action (#380 /
//     #342 原則 P3). A list that could not be READ is not a list that is
//     empty, and neither is answered with a disabled button -- both are a
//     sentence.

export type UxNextDecisionKind =
  | "unavailable"
  | "create_journey"
  | "confirm_journey"
  | "recheck_journey"
  | "create_requirement"
  | "confirm_requirement"
  | "recheck_requirement"
  | "create_solution_design"
  | "add_design_option"
  | "adopt_design_option"
  | "settled";

export interface UxNextDecision {
  kind: UxNextDecisionKind;
  /** What the developer is being asked to decide. */
  title: string;
  /** Why this one is next, in the developer's terms (#380's 選定理由). */
  reason: string;
  /** Where the CTA goes. `null` for the two no-action outcomes. */
  target: { tab: "journeys" | "requirements" | "solutions"; key: string | null } | null;
  /** CTA label. `null` when there is no action. */
  actionLabel: string | null;
}

/** Deterministic pick: the first row by key, so the same facts always name
 * the same entity. No scoring, no recency ranking. */
function firstByKey<T>(rows: readonly T[], key: (row: T) => string, match: (row: T) => boolean): T | null {
  const hits = rows.filter(match).slice().sort((a, b) => key(a).localeCompare(key(b)));
  return hits.length > 0 ? hits[0] : null;
}

export function decideNextDesignAction(input: {
  journeys: readonly UxJourneyOut[] | null;
  requirements: readonly UxRequirementOut[] | null;
  designs: readonly SolutionDesignOut[] | null;
}): UxNextDecision {
  const { journeys, requirements, designs } = input;

  // 1. An unreadable list is never treated as an empty one.
  if (journeys === null || requirements === null || designs === null) {
    return {
      kind: "unavailable",
      title: "次に決めることを判定できませんでした",
      reason: "設計成果物の一覧を取得できていないため、次の 1 操作を決められません。",
      target: null,
      actionLabel: null,
    };
  }

  // 2-4. Journey level (the causal head of the chain).
  if (journeys.length === 0) {
    return {
      kind: "create_journey",
      title: "UX Journey を作成する",
      reason: "この System にはまだ UX Journey がありません。要件も設計案もここから始まります。",
      target: { tab: "journeys", key: null },
      actionLabel: "UX Journey へ移動",
    };
  }
  const proposedJourney = firstByKey(journeys, (j) => j.journey_key, (j) => j.design_status === "proposed");
  if (proposedJourney) {
    return {
      kind: "confirm_journey",
      title: `UX Journey「${proposedJourney.journey_key}」の内容を確定する`,
      reason: "内容は記述済みですが、まだ人による確定の記録がありません。",
      target: { tab: "journeys", key: proposedJourney.journey_key },
      actionLabel: "この Journey を開く",
    };
  }
  const staleJourney = firstByKey(journeys, (j) => j.journey_key, (j) => j.recheck_state === "stale");
  if (staleJourney) {
    return {
      kind: "recheck_journey",
      title: `UX Journey「${staleJourney.journey_key}」を再確認する`,
      reason: "確定したあとに内容が変わりました。確定そのものは取り消されていません。",
      target: { tab: "journeys", key: staleJourney.journey_key },
      actionLabel: "この Journey を開く",
    };
  }

  // 5-7. Requirement level.
  if (requirements.length === 0) {
    return {
      kind: "create_requirement",
      title: "Requirement を作成する",
      reason: "確定した Journey はありますが、それを満たすための要件がまだありません。",
      target: { tab: "requirements", key: null },
      actionLabel: "Requirement へ移動",
    };
  }
  const proposedRequirement = firstByKey(
    requirements, (r) => r.requirement_key, (r) => r.design_status === "proposed",
  );
  if (proposedRequirement) {
    return {
      kind: "confirm_requirement",
      title: `Requirement「${proposedRequirement.requirement_key}」を確定する`,
      reason: "内容は記述済みですが、まだ人による確定の記録がありません。",
      target: { tab: "requirements", key: proposedRequirement.requirement_key },
      actionLabel: "この Requirement を開く",
    };
  }
  const staleRequirement = firstByKey(
    requirements, (r) => r.requirement_key, (r) => r.recheck_state === "stale",
  );
  if (staleRequirement) {
    return {
      kind: "recheck_requirement",
      title: `Requirement「${staleRequirement.requirement_key}」を再確認する`,
      reason: "確定したあとに内容が変わりました。確定そのものは取り消されていません。",
      target: { tab: "requirements", key: staleRequirement.requirement_key },
      actionLabel: "この Requirement を開く",
    };
  }

  // 8-10. Solution Design level.
  if (designs.length === 0) {
    return {
      kind: "create_solution_design",
      title: "Solution Design を作成する",
      reason: "確定した要件はありますが、それをどう実現するかの設計案がまだありません。",
      target: { tab: "solutions", key: null },
      actionLabel: "Solution Design へ移動",
    };
  }
  const emptyDesign = firstByKey(designs, (d) => d.design_key, (d) => d.option_count === 0);
  if (emptyDesign) {
    return {
      kind: "add_design_option",
      title: `Solution Design「${emptyDesign.design_key}」に設計案を追加する`,
      reason: "設計はありますが、比較できる案が 1 つも登録されていません。",
      target: { tab: "solutions", key: emptyDesign.design_key },
      actionLabel: "この Solution Design を開く",
    };
  }
  const undecidedDesign = firstByKey(designs, (d) => d.design_key, (d) => d.adopted_option_key === null);
  if (undecidedDesign) {
    return {
      kind: "adopt_design_option",
      title: `Solution Design「${undecidedDesign.design_key}」の採用案を決める`,
      reason: "案は登録されていますが、どれを採用するかがまだ決まっていません。採用は実装の適用ではありません。",
      target: { tab: "solutions", key: undecidedDesign.design_key },
      actionLabel: "この Solution Design を開く",
    };
  }

  // 11. Nothing outstanding.
  return {
    kind: "settled",
    title: "いま決めるべきことはありません",
    reason: "Journey・Requirement・Solution Design はいずれも確定済みで、再確認の必要もありません。",
    target: null,
    actionLabel: null,
  };
}
