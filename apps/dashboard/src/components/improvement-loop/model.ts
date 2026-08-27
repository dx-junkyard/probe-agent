import type { CandidateVersionOut, ExperimentOut } from "@/api/types";

// Issue #371: one improvement loop, five user-facing stages.
//
// The audit found AI Candidate Studio, Simulation Workbench, and Experiments
// reading as three separate products: `candidate version`, `variant`,
// `Replay Set`, `Replay run`, `shadow`, and `Experiment` all appear within a
// couple of minutes, and nothing on screen says which of "直接編集 / AI生成 /
// 比較 / 採否記録" the developer is currently doing.
//
// This module is the single place that decides the current stage. It is pure
// (no React, no API client), mirroring the `cockpit/model.ts` convention from
// Issue #356: display components render its output and never re-derive a
// stage. The internal persistence models are deliberately NOT unified (an
// explicit non-goal of #371) — only the vocabulary the developer reads is.

export const LOOP_STAGES = [
  "select",
  "create",
  "verify",
  "compare",
  "decide",
] as const;

export type LoopStageId = (typeof LOOP_STAGES)[number];

export interface LoopStage {
  id: LoopStageId;
  /** What the developer is doing. Never an internal model name. */
  label: string;
  /** What the primary action of this stage produces, in the developer's terms. */
  produces: string;
  /** Where this stage happens. */
  to: string;
}

// `produces` exists because the audit's sharpest finding was that 「送信」
// 「promote」「Experimentへ送る」 do not say what they create. Each stage
// states its own output in plain terms.
export const STAGE_DEFINITIONS: Record<LoopStageId, LoopStage> = {
  select: {
    id: "select",
    label: "対象と観測データを選ぶ",
    produces: "改善対象のcomponentと、評価に使うTrace",
    to: "/components",
  },
  create: {
    id: "create",
    label: "改善目標と候補を作る",
    produces: "候補version（差分。対象リポジトリは変更されません）",
    to: "/candidate-studio",
  },
  verify: {
    id: "verify",
    label: "安全性・Replay可否を確認する",
    produces: "Replay実行の承認記録（人手）",
    to: "/candidate-studio",
  },
  compare: {
    id: "compare",
    label: "baselineと候補を比較する",
    produces: "隔離環境でのReplay結果（一致 / 差分 / エラー）",
    to: "/candidate-studio",
  },
  decide: {
    id: "decide",
    label: "採否を記録し、必要なら公開へ進む",
    produces: "採否の記録。採用してもmergeやdeployは行われません",
    to: "/experiments",
  },
};

export type StageStatus = "done" | "current" | "upcoming";

export interface LoopStageView extends LoopStage {
  status: StageStatus;
}

export interface LoopContext {
  /** Present once a component has been chosen. */
  componentId?: string | null;
  /** Present once a Candidate Studio session exists. */
  sessionId?: number | null;
  /** The version currently in focus, if any. */
  version?: CandidateVersionOut | null;
  /** Whether the human Replay approval is currently active. */
  approved?: boolean;
  /** An experiment in focus, when the developer is on that surface. */
  experiment?: ExperimentOut | null;
}

/**
 * Deterministic, first-match stage resolution.
 *
 * Reads only persisted facts already on screen; it never guesses intent from
 * the route alone, because the same route serves several stages (Candidate
 * Studio covers create → verify → compare).
 */
export function resolveStage(context: LoopContext): LoopStageId {
  const { componentId, sessionId, version, approved, experiment } = context;

  // A decision already recorded, or an experiment in focus, is the last stage.
  if (experiment || version?.promoted_at) return "decide";
  if (version?.replay_status === "completed") return "decide";
  if (version?.replay_status === "running") return "compare";
  // A generated version that has not been replayed: the next thing is the
  // approval gate when it is missing, otherwise the comparison itself.
  if (version) return approved ? "compare" : "verify";
  if (sessionId) return "create";
  if (componentId) return "create";
  return "select";
}

export function buildStages(current: LoopStageId): LoopStageView[] {
  const currentIndex = LOOP_STAGES.indexOf(current);
  return LOOP_STAGES.map((id, index) => ({
    ...STAGE_DEFINITIONS[id],
    status: index < currentIndex ? "done" : index === currentIndex ? "current" : "upcoming",
  }));
}

export interface LoopNavigationContext {
  componentId?: string | null;
  traceId?: string | null;
  sessionId?: number | null;
  snapshotId?: number | null;
  replaySetId?: number | string | null;
  replayRunId?: number | null;
  replayVariantId?: number | null;
}

/**
 * The query-parameter names each destination actually reads.
 *
 * These differ by page for good reasons — `/components` has read
 * `component` / `trace` since the Trace Lineage and analyzer deep links were
 * built — so the rail emits the *destination's* names rather than imposing
 * one spelling. Emitting `component_id` at `/components` (which the first
 * pass did) produced a link that navigated but arrived with nothing selected.
 */
const PARAM_NAMES: Record<string, Partial<Record<keyof LoopNavigationContext, string>>> = {
  "/components": { componentId: "component", traceId: "trace" },
  "/candidate-studio": {
    componentId: "component_id",
    traceId: "trace_id",
    sessionId: "session_id",
    replaySetId: "replay_set_id",
  },
  "/simulation-workbench": {
    componentId: "component_id",
    replaySetId: "replay_set_id",
  },
  "/experiments": {
    replayRunId: "replay_run_id",
    replayVariantId: "replay_variant_id",
    componentId: "from_component",
    traceId: "from_trace",
  },
};

/**
 * Carry the loop's context through navigation to one destination.
 *
 * Deep links, reloads, and the browser Back button must not drop the
 * component / trace / session the developer is working on (#371).
 */
export function loopSearchParams(
  to: string,
  context: LoopNavigationContext,
): string {
  const names = PARAM_NAMES[to] ?? {};
  const params = new URLSearchParams();
  for (const [key, name] of Object.entries(names) as Array<
    [keyof LoopNavigationContext, string]
  >) {
    const value = context[key];
    if (value == null || value === "") continue;
    params.set(name, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}
