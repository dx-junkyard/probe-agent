// Epic #412 (#414 / #415): the Flow・エージェント群 screen's display model.
//
// PURE — no React, no API client, no fetch. Every grouping, ordering and
// label lives here; the display components render this module's output and
// re-derive NOTHING. In particular the client never computes:
//
//   * an execution mode          (`app/execution_mode.py`'s 10-row table)
//   * a proposal's status        (#415's event fold, §7.4)
//   * a section's availability   (the server's `degraded_sections`)
//   * a Node's axis values       (five separate server fields, §1.2)
//
// All four arrive already decided (#349's "one canonical state engine"). What
// is left here is presentational: fixed Japanese labels for the server's
// finite vocabularies, fixed display order, and small structural regroupings
// of rows the server already decided.
//
// Three rules are load-bearing and are what the tests pin:
//
// 1. **No aggregate.** There is no score, average, completion rate, health
//    percentage or confidence number anywhere in this module, and there must
//    never be one (ADR-7 / #353). Five axes are five readings.
// 2. **The five missing answers stay five answers.** `missing` /
//    `unavailable` / `unmeasured` / `stale` / `not_applicable` never share
//    copy with each other or with `present` (§6.4). 「0 件」 and
//    「取得できませんでした」 are different sentences (#356).
// 3. **`default` is not a choice.** `mode_source: "default"` is the
//    fail-closed `fixed` nobody selected; `system` + `system_assignment` is a
//    human who selected `fixed`. Two different facts (§4.4), two different
//    sentences.

import type {
  ExecutionCapability,
  ExecutionMode,
  ExecutionModeAssignmentOut,
  ExecutionModeDenialCode,
  ExecutionModeDivergence,
  ExecutionModeNodeProjectionOut,
  ExecutionModeReason,
  ExecutionModeScopeKind,
  ExecutionModeScopeState,
  ExecutionModeSourceScope,
  FlowEdgeSource,
  FlowExperimentProposalOut,
  FlowExperimentEventKind,
  FlowExperimentStatus,
  FlowExplanationNodeOut,
  FlowExplanationOut,
  FlowFactState,
  FlowIsolationStrategy,
  FlowEvaluationLevel,
  FlowMembershipBasis,
  FlowMembershipState,
  FlowOpenItemKind,
  FlowOpenItemOut,
  FlowResponsibilityContractOut,
  FlowResponsibilityEdgeOut,
  FlowResponsibilitySectionOut,
  FlowRuntimeSubjectOut,
  FlowStaticSubjectOut,
  FlowSubjectKind,
  FlowSubjectListOut,
  FlowSubjectResolution,
} from "@/api/types";

// ---------------------------------------------------------------------------
// §6.4 — the five missing answers, plus `present`
// ---------------------------------------------------------------------------

/** Six DIFFERENT sentences. `present` is the absence of a missing answer,
 * never a sixth kind of missing (§6.4). None of these may be reused for
 * another: 「ありません」 (the fact does not exist) and 「取得できませんでした」
 * (we could not read it) are the #380 defect if merged. */
export const FACT_STATE_LABEL: Record<FlowFactState, string> = {
  present: "あり",
  missing: "ありません",
  unavailable: "取得できませんでした",
  unmeasured: "測る仕組みがありません",
  stale: "前提が変わっています",
  not_applicable: "構造上、対象外です",
};

export const FACT_STATE_DESCRIPTION: Record<FlowFactState, string> = {
  present: "値が読めています。",
  missing: "この対象には存在しません。読めなかったのではありません。",
  unavailable: "読み取りに失敗しました。値が無いという意味ではありません。",
  unmeasured: "観測契約が無いため、測定そのものが行われていません。",
  stale: "記録はありますが、その前提（snapshot・contract）が後から動いています。",
  not_applicable: "この種別には構造上あり得ない項目です。欠落ではありません。",
};

/** Colour is never the only marker (#414 acceptance criteria): every tone is
 * paired with the text label above wherever it is rendered. */
export const FACT_STATE_TONE: Record<
  FlowFactState,
  "success" | "muted" | "destructive" | "warning" | "neutral"
> = {
  present: "success",
  missing: "muted",
  unavailable: "destructive",
  unmeasured: "warning",
  stale: "warning",
  not_applicable: "neutral",
};

/** The five that are NOT `present`, in a fixed reading order. Exported so a
 * test can assert the vocabulary never shrinks by rounding two together. */
export const MISSING_FACT_STATES: readonly FlowFactState[] = [
  "missing",
  "unavailable",
  "unmeasured",
  "stale",
  "not_applicable",
];

export function isMissingFactState(state: FlowFactState): boolean {
  return state !== "present";
}

// ---------------------------------------------------------------------------
// §1.1 / §4.4 — execution mode, its source and its reason
// ---------------------------------------------------------------------------

/** Mode names stay in their canonical form (they are the API's vocabulary and
 * appear in the audit rows and the docs); the Japanese gloss is what the
 * developer reads — docs/ui-glossary.md's 初出のみ併記 rule. */
export const EXECUTION_MODE_LABEL: Record<ExecutionMode, string> = {
  fixed: "固定実装のみ",
  observe: "固定実装 + 観測",
  propose: "提案のみ",
  shadow: "候補比較あり",
};

export const EXECUTION_MODE_DESCRIPTION: Record<ExecutionMode, string> = {
  fixed: "実験用 LLM も候補実行も到達できません。設定で無効にしているのではなく、経路が実行されません。",
  observe: "固定実装を実行して観測します。実験用 LLM は到達できません。",
  propose: "候補や実験計画の提案までです。候補の実行は含みません（承認と shadow への昇格が別に必要です）。",
  shadow: "baseline と候補を比較できます。本番の戻り値は常に baseline のままです。",
};

export const EXECUTION_MODE_ORDER: readonly ExecutionMode[] = [
  "fixed",
  "observe",
  "propose",
  "shadow",
];

/** §1.3's fixed permission table, for display only — the server decides every
 * actual gate. */
export const EXECUTION_CAPABILITY_LABEL: Record<ExecutionCapability, string> = {
  observation_record: "観測結果の記録",
  llm_experiment_proposal: "実験用 LLM による提案",
  candidate_execution: "候補実装の隔離実行",
  shadow_comparison: "baseline と候補の比較",
};

/** The ten reason codes of §3.3, ten different sentences. The three
 * elapsed-window rows are deliberately NOT one shared 「期限切れ」: all clamp
 * to `fixed`, but the developer's next action differs per scope (#366). */
export const MODE_REASON_LABEL: Record<ExecutionModeReason, string> = {
  conflicting_assignments: "同じスコープに有効な割り当てが複数あります（データ不整合）",
  invalid_mode_value: "割り当てられた mode が有限語彙にありません",
  flow_scope_not_member: "指定された Flow に、この Node は属していません",
  node_expired_assignment: "Node の割り当ての期限が切れています",
  node_assignment: "Node に割り当てられています",
  flow_expired_assignment: "Flow の割り当ての期限が切れています",
  flow_scope_conflict: "この Node が属する複数の Flow で mode が食い違っています",
  flow_assignment: "Flow に割り当てられています",
  system_expired_assignment: "System の割り当ての期限が切れています",
  system_assignment: "System に割り当てられています",
  no_assignment: "どのスコープにも割り当てがありません",
};

/** What the developer should do next, per reason. Separate from the label
 * because 「なぜ fixed なのか」 and 「どこを直すのか」 are two facts. */
export const MODE_REASON_NEXT_ACTION: Record<ExecutionModeReason, string> = {
  conflicting_assignments: "同一スコープの重複行を解消してください。安全側に倒して fixed で読んでいます。",
  invalid_mode_value: "そのスコープを割り当て直してください。安全側に倒して fixed で読んでいます。",
  flow_scope_not_member: "所属は evolution_node_link(link_kind='flow') が正本です。所属していない Flow の権限は借りられません。Node を Flow に紐付けるか、対象 Node 自身に割り当ててください。",
  node_expired_assignment: "Node を割り当て直すか、revoke を記録してください。時間の経過だけでは復帰しません。",
  node_assignment: "この Node 自身の割り当てが効いています。",
  flow_expired_assignment: "Flow を割り当て直すか、revoke を記録してください。時間の経過だけでは復帰しません。",
  flow_scope_conflict: "食い違っている Flow のどちらかを割り当て直してください。",
  flow_assignment: "この Node が属する Flow の割り当てが効いています。",
  system_expired_assignment: "System を割り当て直すか、revoke を記録してください。時間の経過だけでは復帰しません。",
  system_assignment: "System 全体の割り当てが効いています。",
  no_assignment: "割り当てが必要なら、対象スコープに割り当ててください。",
};

export const MODE_SCOPE_KIND_LABEL: Record<ExecutionModeScopeKind, string> = {
  system: "System 全体",
  flow: "Flow",
  node: "Node",
};

export const MODE_SOURCE_SCOPE_LABEL: Record<ExecutionModeSourceScope, string> = {
  node: "Node の割り当て",
  flow: "Flow の割り当て",
  system: "System の割り当て",
  // `none` is a mode that was clamped by a fail-closed rule, not a scope.
  none: "割り当て由来ではありません（安全側に倒した結果）",
  default: "既定（誰も選んでいません）",
};

/** §3.2's seven scope states, seven sentences. `unset` (no row at all) and
 * `revoked` (a human ended it) are different facts, and neither is
 * `expired` (a window elapsed with nobody deciding what comes next). */
export const SCOPE_STATE_LABEL: Record<ExecutionModeScopeState, string> = {
  unset: "割り当てなし",
  revoked: "人が明示的に終了しました",
  pending: "開始前です",
  expired: "期限切れです",
  invalid: "mode の値が不正です",
  active: "有効です",
  conflicting: "重複しています（データ不整合）",
};

export interface ModeOrigin {
  /** True only when a human recorded an assignment that is currently in
   * effect. `default` and every fail-closed clamp are false. */
  chosen: boolean;
  /** 「既定」 vs 「明示的に選択」 — §4.4's two facts, never one word. */
  headline: string;
  scopeLabel: string;
  reasonLabel: string;
  nextAction: string;
  /** The scope ref a human would edit, if any. */
  sourceRef: string;
}

/** Explain WHERE an effective mode came from.
 *
 * This is a lookup over two server-decided finite values, not a decision: the
 * mode itself, its `source_scope` and its `reason` were all resolved by
 * `resolve_execution_mode`. The one thing this adds is the §4.4 distinction
 * the screen must show — a `fixed` nobody chose must not read like a `fixed`
 * somebody chose. */
export function describeModeOrigin(
  sourceScope: ExecutionModeSourceScope,
  reason: ExecutionModeReason,
  sourceRef = "",
): ModeOrigin {
  const chosen =
    reason === "node_assignment" ||
    reason === "flow_assignment" ||
    reason === "system_assignment";
  const headline =
    sourceScope === "default"
      ? "既定（誰も選んでいません）"
      : chosen
        ? `明示的に選択されています（${MODE_SCOPE_KIND_LABEL[sourceScope as ExecutionModeScopeKind] ?? MODE_SOURCE_SCOPE_LABEL[sourceScope]}）`
        : "安全側に倒した結果です（人の選択ではありません）";
  return {
    chosen,
    headline,
    scopeLabel: MODE_SOURCE_SCOPE_LABEL[sourceScope],
    reasonLabel: MODE_REASON_LABEL[reason],
    nextAction: MODE_REASON_NEXT_ACTION[reason],
    sourceRef,
  };
}

/** §5.2's four divergence readings. `unobserved` is never reported as
 * `match`: not having looked is not a success (#380). */
export const DIVERGENCE_LABEL: Record<ExecutionModeDivergence, string> = {
  match: "設定と一致",
  divergent: "設定と食い違っています",
  unobserved: "観測がありません",
  stale: "観測後にモードが変わりました",
};

export const DIVERGENCE_DESCRIPTION: Record<ExecutionModeDivergence, string> = {
  match: "直近の観測が実効モードと一致しています。",
  divergent: "実際に動いたモードが実効モードと違います。観測値と実効値の両方を確認してください。",
  unobserved: "観測が 1 件もありません。一致しているという意味ではありません。",
  stale: "観測はありますが、その後にモードが変更されています。現在のモードの証拠ではありません。",
};

export const DIVERGENCE_TONE: Record<
  ExecutionModeDivergence,
  "success" | "destructive" | "warning" | "muted"
> = {
  match: "success",
  divergent: "destructive",
  unobserved: "muted",
  stale: "warning",
};

export const DENIAL_CODE_LABEL: Record<ExecutionModeDenialCode, string> = {
  capability_not_permitted: "現在の実効モードではこの操作は許可されていません",
  conflicting_assignments: "同じスコープに有効な割り当てが複数あります",
  invalid_mode_value: "割り当てられた mode が有限語彙にありません",
  flow_scope_not_member: "指定された Flow に、この Node は属していません",
  node_expired_assignment: "Node の割り当ての期限が切れています",
  flow_expired_assignment: "Flow の割り当ての期限が切れています",
  system_expired_assignment: "System の割り当ての期限が切れています",
  flow_scope_conflict: "この Node が属する複数の Flow で mode が食い違っています",
  unknown_capability: "要求された capability が不明です",
  node_not_found: "対象の Node が見つかりません",
};

// ---------------------------------------------------------------------------
// §1.2 — the five axes, kept as five readings
// ---------------------------------------------------------------------------

export type NodeAxisKey =
  | "execution_mode"
  | "maturity"
  | "implementation_modality"
  | "improvement_status"
  | "sdk_policy_mode";

export interface NodeAxisReading {
  key: NodeAxisKey;
  label: string;
  /** The server's own value, untouched. `null` means the axis carries no
   * value; WHY it carries none is `state`, never inferred from `null`. */
  value: string | null;
  state: FlowFactState;
  /** What this axis is about — and, where relevant, what it is NOT. */
  hint: string;
}

const AXIS_HINT: Record<NodeAxisKey, string> = {
  execution_mode: "control plane が候補実行と実験用 LLM をどこまで許すか。SDK policy とは別の事実です。",
  maturity: "この処理をどれだけ分かっているか（Evolution Node の成熟度）。実行モードからは導出されません。",
  implementation_modality: "いまその契約をどの方式で満たしているか。",
  improvement_status: "リンクされた Cell の改善試行の進捗。Node の成熟度ではありません。",
  sdk_policy_mode: "SDK が何を計装・送信するか（off / trace / shadow）。実行モードとは別の事実です。",
};

const AXIS_LABEL: Record<NodeAxisKey, string> = {
  execution_mode: "実行モード",
  maturity: "Node maturity",
  implementation_modality: "実装方式",
  improvement_status: "Cell Improvement status",
  sdk_policy_mode: "SDK policy mode",
};

/** The five axes of §1.2 as five independent readings, in a fixed order.
 *
 * Deliberately returns a LIST of readings and never a combined value: there
 * is no "overall state" of a Node, and computing one would be exactly the
 * collapse ADR-6 forbids. */
export function nodeAxes(node: FlowExplanationNodeOut): NodeAxisReading[] {
  return [
    {
      key: "execution_mode",
      label: AXIS_LABEL.execution_mode,
      value: node.execution_mode,
      state: node.mode_state,
      hint: AXIS_HINT.execution_mode,
    },
    {
      key: "maturity",
      label: AXIS_LABEL.maturity,
      value: node.maturity,
      state: node.maturity_state,
      hint: AXIS_HINT.maturity,
    },
    {
      key: "implementation_modality",
      label: AXIS_LABEL.implementation_modality,
      value: node.implementation_modality,
      state: node.implementation_modality_state,
      hint: AXIS_HINT.implementation_modality,
    },
    {
      key: "improvement_status",
      label: AXIS_LABEL.improvement_status,
      value: node.improvement_status,
      state: node.improvement_status_state,
      hint: AXIS_HINT.improvement_status,
    },
    {
      key: "sdk_policy_mode",
      label: AXIS_LABEL.sdk_policy_mode,
      value: node.sdk_policy_mode,
      state: node.sdk_policy_mode_state,
      hint: AXIS_HINT.sdk_policy_mode,
    },
  ];
}

export interface ShadowReadings {
  executionMode: ExecutionMode;
  executionModeState: FlowFactState;
  sdkPolicyMode: string | null;
  sdkPolicyModeState: FlowFactState;
  /** Both words read "shadow" and they still mean two different things. */
  bothShadow: boolean;
  /** Exactly one of the two reads "shadow" — a legitimate state that must be
   * shown as two readings, never resolved into one badge. */
  onlyOneShadow: boolean;
  /** The sentence that keeps the two apart wherever either says "shadow". */
  note: string | null;
}

/** The two `shadow` meanings, side by side (§1.2).
 *
 * SDK policy `shadow` = the SDK runs the candidate and sends
 * `shadow_results`. Execution mode `shadow` = the control plane permits a
 * candidate comparison for this Node. One being `shadow` without the other is
 * a legitimate state, so this returns both readings and a note — never a
 * single merged verdict. */
export function describeShadowReadings(node: FlowExplanationNodeOut): ShadowReadings {
  const execShadow = node.execution_mode === "shadow";
  const sdkShadow = node.sdk_policy_mode === "shadow";
  const bothShadow = execShadow && sdkShadow;
  const onlyOneShadow = execShadow !== sdkShadow && (execShadow || sdkShadow);
  let note: string | null = null;
  if (bothShadow) {
    note =
      "SDK policy の shadow（SDK が候補を実行して送る）と実行モードの shadow（control plane が候補比較を許す）は別の事実です。同じ語ですが同じ判定ではありません。";
  } else if (onlyOneShadow) {
    note = execShadow
      ? "実行モードは shadow ですが、SDK policy は shadow ではありません。これは正当な状態です（別の軸なので一方だけが shadow になり得ます）。"
      : "SDK policy は shadow ですが、実行モードは shadow ではありません。これは正当な状態です（別の軸なので一方だけが shadow になり得ます）。";
  }
  return {
    executionMode: node.execution_mode,
    executionModeState: node.mode_state,
    sdkPolicyMode: node.sdk_policy_mode,
    sdkPolicyModeState: node.sdk_policy_mode_state,
    bothShadow,
    onlyOneShadow,
    note,
  };
}

export const MEMBERSHIP_BASIS_LABEL: Record<FlowMembershipBasis, string> = {
  flow_link: "evolution_node_link(link_kind='flow') による所属",
  probe_point_exact_match: "probe_point の (path, qualified_name) 完全一致による所属",
};

export const MEMBERSHIP_STATE_LABEL: Record<FlowMembershipState, string> = {
  resolved: "所属を解決できました",
  unavailable: "所属を解決できませんでした（空集合ではありません）",
};

// ---------------------------------------------------------------------------
// §6.2 — subjects: two kinds, never one list
// ---------------------------------------------------------------------------

export const SUBJECT_KIND_LABEL: Record<FlowSubjectKind, string> = {
  runtime_flow: "runtime_flow（実行時に観測された flow_id）",
  static_flow: "static_flow（pin した snapshot の entrypoint）",
};

export const SUBJECT_KIND_SHORT_LABEL: Record<FlowSubjectKind, string> = {
  runtime_flow: "runtime_flow",
  static_flow: "static_flow",
};

export const SUBJECT_KIND_DESCRIPTION: Record<FlowSubjectKind, string> = {
  runtime_flow:
    "trace_spans.flow_id。実際に流れた経路です。実行モードのスコープになれるのはこちらだけです。",
  static_flow:
    "code_entrypoints.entrypoint_id。pin した snapshot 上の静的な経路で、表示のための subject です。実行モードのスコープにはなりません。",
};

export const SUBJECT_RESOLUTION_LABEL: Record<FlowSubjectResolution, string> = {
  resolved: "解決できました",
  unresolved: "この System では見つかりませんでした",
  unavailable: "解決を試みましたが読み取れませんでした",
};

export interface SubjectGroup {
  kind: FlowSubjectKind;
  label: string;
  description: string;
  /** `null` means the list could not be read (the server named it in
   * `degraded_sections`). An EMPTY ARRAY means there are none. Two different
   * displays (#356). */
  items: (FlowRuntimeSubjectOut | FlowStaticSubjectOut)[] | null;
  degradedDetail: string | null;
}

/** The two kinds as two groups, in a fixed order, each carrying its own
 * read failure. Merging them into one "flows" list is the collapse §2.1
 * forbids, so this function has no variant that returns a flat list. */
export function subjectGroups(listing: FlowSubjectListOut | undefined): SubjectGroup[] {
  const runtimeDegraded = listing?.degraded_sections.includes("runtime_flows") ?? false;
  const staticDegraded = listing?.degraded_sections.includes("static_flows") ?? false;
  return [
    {
      kind: "runtime_flow",
      label: SUBJECT_KIND_LABEL.runtime_flow,
      description: SUBJECT_KIND_DESCRIPTION.runtime_flow,
      items: runtimeDegraded ? null : [...(listing?.runtime_flows ?? [])],
      degradedDetail: runtimeDegraded
        ? listing?.degraded_detail.runtime_flows ?? ""
        : null,
    },
    {
      kind: "static_flow",
      label: SUBJECT_KIND_LABEL.static_flow,
      description: SUBJECT_KIND_DESCRIPTION.static_flow,
      items: staticDegraded ? null : [...(listing?.static_flows ?? [])],
      degradedDetail: staticDegraded
        ? listing?.degraded_detail.static_flows ?? ""
        : null,
    },
  ];
}

/** §2.1: a `flow` scope ref is always stored with its prefix. The screen
 * shows the prefixed form so a `runtime_flow` ref can never be read as a
 * `static_flow` one. */
export function flowScopeRef(flowId: string): string {
  return flowId.startsWith("runtime_flow:") ? flowId : `runtime_flow:${flowId}`;
}

// ---------------------------------------------------------------------------
// §4 — the assign form's opening scope
// ---------------------------------------------------------------------------

export interface AssignScope {
  scopeKind: ExecutionModeScopeKind;
  /** ALWAYS a ref that belongs to `scopeKind`. There is no state in which a
   * Flow ref sits in a `node` form or vice versa. */
  scopeRef: string;
}

/** Which scope the assign form OPENS on, from the subject the screen is
 * showing. A deterministic two-row first-match table.
 *
 * This is a safety rule, not a convenience one. An assignment grants LLM and
 * candidate-execution permission (§1.3), so a form whose `scope_kind` and
 * `scope_ref` disagree can silently grant that permission on the WRONG
 * subject — a Flow id submitted as a `node_key` mostly 404s, but a stale ref
 * left over from a previously selected Flow does not: it is a valid ref for
 * the wrong Flow, and the server has no way to know the developer meant a
 * different one. So:
 *
 *   1. A runtime Flow is selected → the `flow` scope with THAT Flow's
 *      prefixed ref. The ref is the subject the whole screen is about, so the
 *      as-opened form is coherent and the confirm dialog names the Flow the
 *      developer is looking at.
 *   2. Anything else (a static Flow — which §2.1 forbids as a mode scope —
 *      or no selection at all) → the `node` scope with an EMPTY ref. Nothing
 *      on screen names a Node, so nothing is pre-filled, and the form cannot
 *      be submitted until the developer types one.
 *
 * `system` is deliberately never the default: it is the broadest grant on the
 * screen and must be an explicit choice, never a form the developer submitted
 * without reading. */
export function initialAssignScope(
  selected: { kind: FlowSubjectKind; ref: string } | null,
): AssignScope {
  if (selected && selected.kind === "runtime_flow") {
    return { scopeKind: "flow", scopeRef: flowScopeRef(selected.ref) };
  }
  return { scopeKind: "node", scopeRef: "" };
}

/** The ref to show after the developer CHANGES the scope kind.
 *
 * A ref never crosses kinds: switching away clears the field, and switching
 * back re-derives the subject's own ref instead of resurrecting whatever was
 * typed for the other kind. Carrying a value across is how a `node_key` ends
 * up submitted as a Flow ref. */
export function scopeRefForKind(
  kind: ExecutionModeScopeKind,
  initial: AssignScope,
): string {
  if (kind === "system") return "";
  if (kind === initial.scopeKind) return initial.scopeRef;
  return "";
}

/** What the scope-ref field IS, per kind. The field is one input whose
 * meaning changes entirely with the kind above it, so the kind's own wording
 * goes into the label rather than into a hint the developer may not read. */
export const SCOPE_REF_FIELD_LABEL: Record<ExecutionModeScopeKind, string> = {
  system: "System 全体には参照がありません",
  flow: "runtime_flow の flow_id",
  node: "node_key",
};

export const SCOPE_REF_FIELD_PLACEHOLDER: Record<ExecutionModeScopeKind, string> = {
  system: "",
  flow: "runtime_flow:<flow_id> または flow_id",
  node: "node_key",
};

export const SCOPE_REF_FIELD_HINT: Record<ExecutionModeScopeKind, string> = {
  system:
    "System スコープは参照を取りません（空文字列）。System 全体への割り当てはこの画面で最も広い権限付与なので、既定では選ばれません。",
  flow: "Flow 参照は常に前置詞付きで保存されます。表示中の Flow の flow_id が入っています。",
  node: "Node は画面上の subject から導出できないため、node_key を入力するまで記録できません。",
};

// ---------------------------------------------------------------------------
// §6.3 — the six sections, each degrading alone
// ---------------------------------------------------------------------------

export const FLOW_SECTION_KEYS = [
  "purpose",
  "responsibility",
  "nodes",
  "open_items",
  "experiments",
  "baseline",
] as const;

export type FlowSectionKey = (typeof FLOW_SECTION_KEYS)[number];

export const FLOW_SECTION_LABEL: Record<FlowSectionKey, string> = {
  purpose: "この Flow が支えている目的",
  responsibility: "責務と入出力境界",
  nodes: "構成 Node（5 軸）",
  open_items: "未解決事項",
  experiments: "実験",
  baseline: "baseline と rollback",
};

export interface SectionAvailability {
  key: FlowSectionKey;
  label: string;
  /** False when the server named this section in `degraded_sections`. The
   * display is DROPPED and says so; no guessed value is substituted, and one
   * failed section never blanks the others (§6.4). */
  available: boolean;
  detail: string | null;
}

export function sectionAvailability(
  explanation: FlowExplanationOut | undefined,
  key: FlowSectionKey,
): SectionAvailability {
  const degraded = explanation?.degraded_sections.includes(key) ?? false;
  return {
    key,
    label: FLOW_SECTION_LABEL[key],
    available: !degraded,
    detail: degraded ? explanation?.degraded_detail[key] ?? "" : null,
  };
}

export function degradedSections(
  explanation: FlowExplanationOut | undefined,
): SectionAvailability[] {
  return FLOW_SECTION_KEYS.map((key) => sectionAvailability(explanation, key)).filter(
    (entry) => !entry.available,
  );
}

export const EDGE_SOURCE_LABEL: Record<FlowEdgeSource, string> = {
  runtime_span_parentage: "実行時の span 親子関係",
  static_call_graph: "pin した snapshot の call graph",
  unavailable: "依存関係を取得できませんでした",
  not_applicable: "この subject には依存関係の読みがありません",
};

/** What each dependency reading ANSWERS — deliberately separate from its
 * label. 「どこから読んだか」 and 「その読みに何が含まれるか」 are two facts,
 * and the second is what decides whether an empty list means 0 件 or means
 * 「この読みでは測っていない」 below. */
export const EDGE_SOURCE_DESCRIPTION: Record<FlowEdgeSource, string> = {
  runtime_span_parentage:
    "実際に流れた trace の span 親子関係です。実行された辺だけが載ります（コード上に存在する経路の一覧ではありません）。",
  static_call_graph:
    "pin した snapshot の call graph です。コード上の呼び出し関係で、実行された証拠ではありません。",
  unavailable:
    "依存関係の読み取りに失敗しました。辺が 0 件だという意味ではありません。",
  not_applicable:
    "この subject 種別には依存関係の読みが構造上ありません。欠落ではありません。",
};

/** Whether the section's `edges` / `external_boundaries` lists are a MEASURED
 * result at all.
 *
 * `unavailable` / `not_applicable` mean the list was never produced, so an
 * empty array there is not 「0 件」 — printing 「0 件」 would be exactly the
 * one-word-two-facts collapse (#366). This is a lookup over the server's own
 * finite `edge_source` vocabulary, not a judgement. */
const EDGE_SOURCE_PRODUCES_EDGES: Record<FlowEdgeSource, boolean> = {
  runtime_span_parentage: true,
  static_call_graph: true,
  unavailable: false,
  not_applicable: false,
};

/** External boundaries are derived from the pinned snapshot's call graph
 * ONLY (`app/flow_explanation.py` builds them from the graph's external
 * nodes). A runtime reading never produces them, so its empty array must not
 * be reported as 「外部境界は 0 件です」 — nothing looked for one. */
const EDGE_SOURCE_PRODUCES_BOUNDARIES: Record<FlowEdgeSource, boolean> = {
  runtime_span_parentage: false,
  static_call_graph: true,
  unavailable: false,
  not_applicable: false,
};

const BOUNDARY_UNMEASURED_NOTE: Record<FlowEdgeSource, string> = {
  runtime_span_parentage:
    "実行時の span 親子関係の読みには外部境界が含まれません。外部境界は pin した snapshot の call graph からのみ求まります。0 件という意味ではありません。",
  static_call_graph: "",
  unavailable:
    "依存関係を取得できなかったため、外部境界も読めていません。0 件という意味ではありません。",
  not_applicable:
    "この subject 種別には外部境界の読みが構造上ありません。欠落ではありません。",
};

export interface DependencyReading {
  edgeSource: FlowEdgeSource;
  edgeSourceLabel: string;
  edgeSourceDescription: string;
  /** True when this reading produces an edge list at all. False means an
   * empty `edges` array is NOT 「0 件」. */
  edgesMeasured: boolean;
  /** True when this reading produces external boundaries at all. */
  boundariesMeasured: boolean;
  /** The sentence to show INSTEAD of a count when boundaries were never
   * produced. Empty string when they were. */
  boundaryUnmeasuredNote: string;
}

/** Name the dependency reading before its rows are shown (§6.3 item 2).
 *
 * A pure lookup over the server's `edge_source`. It decides nothing about the
 * graph — it only says which question the rows below answer, and whether an
 * empty list is an observed 0 or a list that was never produced. */
export function describeDependencyReading(
  section: Pick<FlowResponsibilitySectionOut, "edge_source">,
): DependencyReading {
  const source = section.edge_source;
  return {
    edgeSource: source,
    edgeSourceLabel: EDGE_SOURCE_LABEL[source],
    edgeSourceDescription: EDGE_SOURCE_DESCRIPTION[source],
    edgesMeasured: EDGE_SOURCE_PRODUCES_EDGES[source],
    boundariesMeasured: EDGE_SOURCE_PRODUCES_BOUNDARIES[source],
    boundaryUnmeasuredNote: BOUNDARY_UNMEASURED_NOTE[source],
  };
}

/** The edge kinds both readings can carry. `span_parent` is the runtime one;
 * the rest are `app/flow_graph.py`'s call-graph edge types. An unknown kind
 * is rendered VERBATIM (it is the server's identifier) and never guessed at
 * — this map only glosses what is already finite. */
export const EDGE_KIND_LABEL: Record<string, string> = {
  span_parent: "span の親子",
  call: "関数呼び出し",
  await: "await 呼び出し",
  dispatch: "非同期ディスパッチ",
  http: "HTTP 境界",
  database: "DB 境界",
  filesystem: "ファイルシステム境界",
};

/** `app/flow_graph.py`'s edge resolution vocabulary. `inferred` is not
 * `resolved`: a guess about the callee is not a verified callee. */
export const EDGE_RESOLUTION_LABEL: Record<string, string> = {
  resolved: "解決済み",
  inferred: "推定（確定した呼び先ではありません）",
  unresolved: "呼び先を解決できませんでした",
};

export interface FlowEdgeReading {
  key: string;
  /** 1-based position in the server's own recorded order. A number for
   * reading the list, never a rank or a score. */
  position: number;
  source: string;
  target: string;
  edgeKind: string;
  /** The canonical kind plus its gloss when known; the canonical kind alone
   * otherwise. */
  edgeKindLabel: string;
  /** Per-reading extras (trace_id / callee / line / resolution), already
   * stringified in a fixed order. Empty when the row carries none. */
  detail: string;
}

function edgeExtra(edge: FlowResponsibilityEdgeOut, key: string): string {
  const value = (edge as unknown as Record<string, unknown>)[key];
  if (value === null || value === undefined || value === "") return "";
  return String(value);
}

/** The `edges` array as a readable ordered list (§6.3 item 2's 依存関係).
 *
 * The server's order is preserved verbatim — runtime edges arrive in span
 * timestamp order, static ones in call-graph order — so this adds a position
 * for reading and nothing else. No re-ordering, no ranking, no grouping by a
 * computed importance. */
export function flowEdgeReadings(
  edges: readonly FlowResponsibilityEdgeOut[],
): FlowEdgeReading[] {
  return edges.map((edge, index) => {
    const gloss = EDGE_KIND_LABEL[edge.edge_kind];
    const parts: string[] = [];
    const callee = edgeExtra(edge, "callee_name");
    const line = edgeExtra(edge, "line");
    const resolution = edgeExtra(edge, "resolution");
    const traceId = edgeExtra(edge, "trace_id");
    if (callee) parts.push(`callee: ${callee}`);
    if (line) parts.push(`line: ${line}`);
    if (resolution) {
      parts.push(`${resolution}（${EDGE_RESOLUTION_LABEL[resolution] ?? resolution}）`);
    }
    if (traceId) parts.push(`trace: ${traceId}`);
    return {
      key: `${index}:${edge.source}:${edge.target}:${edge.edge_kind}`,
      position: index + 1,
      source: edge.source,
      target: edge.target,
      edgeKind: edge.edge_kind,
      edgeKindLabel: gloss ? `${edge.edge_kind} / ${gloss}` : edge.edge_kind,
      detail: parts.join(" / "),
    };
  });
}

/** `app/flow_graph.py`'s four boundary kinds. An unknown kind renders
 * verbatim, never as a guessed category. */
export const BOUNDARY_KIND_LABEL: Record<string, string> = {
  http: "HTTP",
  database: "DB",
  filesystem: "ファイルシステム",
  dispatch: "非同期ディスパッチ",
};

export interface ExternalBoundaryReading {
  key: string;
  nodeId: string;
  boundaryKind: string;
  boundaryKindLabel: string;
  qualifiedName: string;
}

/** `external_boundaries` is typed as an open record on the wire, so this
 * normalizes the three keys the server writes and drops nothing else in:
 * a row whose `boundary_kind` is absent keeps an EMPTY kind rather than
 * being assigned a plausible one. */
export function externalBoundaryReadings(
  rows: readonly Record<string, unknown>[],
): ExternalBoundaryReading[] {
  return rows.map((row, index) => {
    const nodeId = typeof row.node_id === "string" ? row.node_id : "";
    const kind = typeof row.boundary_kind === "string" ? row.boundary_kind : "";
    const qualified =
      typeof row.qualified_name === "string" ? row.qualified_name : "";
    return {
      key: `${index}:${nodeId}`,
      nodeId,
      boundaryKind: kind,
      boundaryKindLabel: kind ? BOUNDARY_KIND_LABEL[kind] ?? kind : "",
      qualifiedName: qualified,
    };
  });
}

export interface ContractField {
  name: string;
  value: string;
}

export interface ContractReading {
  nodeKey: string;
  /** The server's own `FlowFactState` for this contract. When it is not
   * `present` NOTHING below is populated — an unreadable contract must never
   * render as an empty one (#380). */
  state: FlowFactState;
  mission: string;
  scope: string;
  outOfScope: string;
  sideEffectClass: string | null;
  trustBoundary: string | null;
  /** The recorded `input_contract` / `output_contract` objects as ordered
   * name/value rows, in the order they were recorded. An empty list means
   * the version recorded no field — a 0 件 statement, never a fact state
   * this module invented. */
  input: ContractField[];
  output: ContractField[];
}

function contractFields(value: unknown): ContractField[] {
  if (value === null || value === undefined) return [];
  if (typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>).map(([name, raw]) => ({
    name,
    value: typeof raw === "string" ? raw : JSON.stringify(raw),
  }));
}

/** One Node's I/O boundary and responsibility, as recorded on its current
 * `evolution_node_version` (§6.3 item 2's 入出力境界).
 *
 * The contract's `state` is the SERVER's; this function never derives one.
 * When the state is not `present` every field stays empty so the display
 * cannot print a value the server did not vouch for. */
export function describeContract(
  contract: FlowResponsibilityContractOut,
): ContractReading {
  if (contract.state !== "present") {
    return {
      nodeKey: contract.node_key,
      state: contract.state,
      mission: "",
      scope: "",
      outOfScope: "",
      sideEffectClass: null,
      trustBoundary: null,
      input: [],
      output: [],
    };
  }
  return {
    nodeKey: contract.node_key,
    state: contract.state,
    mission: contract.mission ?? "",
    scope: contract.scope ?? "",
    outOfScope: contract.out_of_scope ?? "",
    sideEffectClass: contract.side_effect_class ?? null,
    trustBoundary: contract.trust_boundary ?? null,
    input: contractFields(contract.input_contract),
    output: contractFields(contract.output_contract),
  };
}

/** Every Node's contract, in the server's recorded order. */
export function contractReadings(
  contracts: readonly FlowResponsibilityContractOut[],
): ContractReading[] {
  return contracts.map(describeContract);
}

// ---------------------------------------------------------------------------
// §6.3 item 4 — open items
// ---------------------------------------------------------------------------

/** Fixed reading order. Anomalies first because they are observations of
 * something that already went wrong; the read/measure gaps follow. */
export const OPEN_ITEM_KIND_ORDER: readonly FlowOpenItemKind[] = [
  "anomaly",
  "mode_divergence",
  "maturity_drift",
  "stale_premise",
  "unresolved_membership",
  "missing_fact",
  "unmeasured_observation",
];

export const OPEN_ITEM_KIND_LABEL: Record<FlowOpenItemKind, string> = {
  anomaly: "anomaly（観測された異常）",
  missing_fact: "読めなかった / 存在しない事実",
  unmeasured_observation: "測る仕組みが無い観測",
  mode_divergence: "設定と実行の食い違い",
  unresolved_membership: "所属を解決できない Node",
  stale_premise: "前提が動いた項目",
  maturity_drift: "maturity と lineage の不一致",
};

export interface OpenItemGroup {
  kind: FlowOpenItemKind;
  label: string;
  items: FlowOpenItemOut[];
}

/** Group by the server's finite `kind` only — membership in an already-finite
 * server-derived set, never text similarity, keywords or embeddings
 * (Principle 6). Empty groups are dropped; a group's SIZE is a count of rows,
 * not a score. */
export function groupOpenItems(items: readonly FlowOpenItemOut[]): OpenItemGroup[] {
  return OPEN_ITEM_KIND_ORDER.map((kind) => ({
    kind,
    label: OPEN_ITEM_KIND_LABEL[kind],
    items: items.filter((item) => item.kind === kind),
  })).filter((group) => group.items.length > 0);
}

// ---------------------------------------------------------------------------
// §7 — flow experiment proposals
// ---------------------------------------------------------------------------

/** The seven DERIVED statuses of §7.4. The client never folds events itself;
 * these are labels for the value the server already derived. */
export const PROPOSAL_STATUS_LABEL: Record<FlowExperimentStatus, string> = {
  proposed: "承認待ち",
  approved: "承認済み",
  rejected: "却下",
  withdrawn: "撤回",
  expired: "期限切れ",
  executing: "実行記録あり",
  completed: "結果記録あり",
};

export const PROPOSAL_STATUS_DESCRIPTION: Record<FlowExperimentStatus, string> = {
  proposed: "人の承認を待っています。承認だけでは実行できません（実行モードも別に必要です）。",
  approved: "人が承認しました。実行にはさらに実効モードが candidate_execution を許す必要があります。",
  rejected: "人が却下しました。",
  withdrawn: "人が撤回しました。",
  expired: "承認期限が過ぎました。期限切れの提案は承認できません。",
  executing: "実行が記録されています。実行の実体は既存の正本（replay / experiment / shadow）です。",
  completed: "結果が記録されています。昇格候補の記録があっても昇格ではありません。",
};

export const PROPOSAL_STATUS_TONE: Record<
  FlowExperimentStatus,
  "warning" | "success" | "destructive" | "muted" | "neutral"
> = {
  proposed: "warning",
  approved: "success",
  rejected: "destructive",
  withdrawn: "muted",
  expired: "destructive",
  executing: "neutral",
  completed: "success",
};

export const PROPOSAL_EVENT_LABEL: Record<FlowExperimentEventKind, string> = {
  proposed: "提案を登録",
  approved: "承認",
  rejected: "却下",
  withdrawn: "撤回",
  expired: "期限切れ",
  execution_recorded: "実行を記録",
  result_recorded: "結果を記録",
  promotion_candidate_recorded: "昇格候補を記録（昇格ではありません）",
  rollback_recorded: "rollback を記録",
};

export const COMPARISON_SCOPE_LABEL: Record<string, string> = {
  single_node: "single_node（対象 Node がちょうど 1 つ）",
  sub_pipeline: "sub_pipeline（対象 Node が 2 つ以上）",
};

export const ISOLATION_STRATEGY_LABEL: Record<FlowIsolationStrategy, string> = {
  pure: "pure（副作用なしとして扱う）",
  mock: "mock（外部を差し替える）",
  dry_run: "dry_run（書き込みを行わない）",
  rollback_transaction: "rollback_transaction（トランザクションを巻き戻す）",
  isolated_workspace: "isolated_workspace（隔離ワークスペース）",
  none: "none（隔離しない）",
};

/** ADR-7: the three evaluation contracts are read separately and never
 * merged into one number. This is the fixed order they are read in. */
export const EVALUATION_LEVEL_ORDER: readonly FlowEvaluationLevel[] = [
  "node",
  "flow_capability",
  "ux_outcome",
];

export const EVALUATION_LEVEL_LABEL: Record<FlowEvaluationLevel, string> = {
  node: "Node 指標",
  flow_capability: "Flow / Capability 指標",
  ux_outcome: "UX / Outcome 指標",
};

export interface EvaluationLevelGroup {
  level: FlowEvaluationLevel;
  label: string;
  axes: { level: FlowEvaluationLevel; name: string; metric?: string; detail?: string }[];
}

/** The server's own `evaluation_axes_by_level` grouping, rendered in a fixed
 * order with EMPTY LEVELS KEPT — a level with no axis is a stated absence,
 * not a level that silently disappears (and never a level derived from
 * another, ADR-7). */
export function evaluationLevelGroups(
  proposal: FlowExperimentProposalOut,
): EvaluationLevelGroup[] {
  return EVALUATION_LEVEL_ORDER.map((level) => ({
    level,
    label: EVALUATION_LEVEL_LABEL[level],
    axes: (proposal.evaluation_axes_by_level?.[level] ?? []).map((axis) => ({
      level,
      name: axis.name,
      metric: axis.metric,
      detail: axis.detail,
    })),
  }));
}

export interface ProposalRequirement {
  key: string;
  label: string;
  /** The proposal's OWN recorded content, rendered as text. A stored proposal
   * has already passed §7.1's server-side gate, so this is a reading of what
   * was recorded — not a re-run of the gate. */
  value: string;
  recorded: boolean;
}

function textOf(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    return value
      .map((entry) => (typeof entry === "string" ? entry : JSON.stringify(entry)))
      .filter((entry) => entry.length > 0)
      .join(" / ");
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return "";
    return entries.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(" / ");
  }
  return String(value);
}

/** §7.1's twelve required elements, as recorded on the stored proposal.
 *
 * The server refuses an incomplete proposal at creation with its own finite
 * code, so every listed element is expected to be present here; the list
 * exists so the developer can READ the plan they are being asked to approve
 * — 目的 / 仮説 / 対象 / baseline / 候補 / 評価軸 / quality floor / 隔離戦略 /
 * コスト上限 / 停止条件 / rollback / 根拠 — instead of approving a title. */
export function proposalRequirements(
  proposal: FlowExperimentProposalOut,
): ProposalRequirement[] {
  const rows: { key: string; label: string; value: string }[] = [
    { key: "purpose", label: "目的", value: textOf(proposal.purpose) },
    { key: "hypothesis", label: "仮説", value: textOf(proposal.hypothesis) },
    {
      key: "scope",
      label: "対象範囲（対象 Node）",
      value: proposal.targets.map((t) => `${t.target_node_key}（${t.target_role}）`).join(" / "),
    },
    { key: "baseline", label: "baseline 参照", value: textOf(proposal.baseline_ref) },
    { key: "candidates", label: "候補", value: textOf(proposal.candidate_refs) },
    {
      key: "evaluation_axes",
      label: "評価軸",
      value: proposal.evaluation_axes.map((a) => `${a.level}: ${a.name}`).join(" / "),
    },
    { key: "quality_floor", label: "quality floor", value: textOf(proposal.quality_floor) },
    {
      key: "isolation_strategy",
      label: "副作用の隔離戦略",
      value:
        ISOLATION_STRATEGY_LABEL[proposal.isolation_strategy] ??
        proposal.isolation_strategy,
    },
    { key: "cost_cap", label: "コスト上限", value: textOf(proposal.cost_cap) },
    { key: "stop_conditions", label: "停止条件", value: textOf(proposal.stop_conditions) },
    { key: "rollback_plan", label: "rollback 計画", value: textOf(proposal.rollback_plan) },
    { key: "evidence", label: "根拠", value: textOf(proposal.evidence_refs) },
  ];
  return rows.map((row) => ({ ...row, recorded: row.value.length > 0 }));
}

export type ProposalAction = "approve" | "reject" | "withdraw";

export const PROPOSAL_ACTION_LABEL: Record<ProposalAction, string> = {
  approve: "承認する",
  reject: "却下する",
  withdraw: "撤回する",
};

/** `reason` is required by the server for a rejection and a withdrawal —
 * both end a plan and an unexplained decision cannot be reviewed later. */
export const PROPOSAL_ACTION_REASON_REQUIRED: Record<ProposalAction, boolean> = {
  approve: false,
  reject: true,
  withdraw: true,
};

export interface ProposalActionAvailability {
  action: ProposalAction;
  label: string;
  enabled: boolean;
  /** Why the control is disabled, stated in words. A disabled control with no
   * reason teaches the developer to ignore it (#383). */
  disabledReason: string | null;
  reasonRequired: boolean;
}

/** Which decisions the screen OFFERS, read off the status the server already
 * derived.
 *
 * This is a display gate, not a second lifecycle definition: it never folds
 * events, never computes a status, and is not authoritative. The server's own
 * §7.4 transition gate decides, and a refusal is rendered as its finite code
 * verbatim — so a disagreement here shows up as the server's message, never
 * as a silently swallowed action. Unavailable entries stay VISIBLE and
 * disabled with their reason (#356's rule for guidance controls). */
export function proposalActions(
  status: FlowExperimentStatus,
): ProposalActionAvailability[] {
  const awaiting = status === "proposed";
  const ended =
    status === "rejected" || status === "withdrawn" || status === "expired";
  const executed = status === "executing" || status === "completed";
  const decidedNote = `現在の状態は「${PROPOSAL_STATUS_LABEL[status]}」です。この操作は承認待ちの提案にだけ記録できます。`;
  return [
    {
      action: "approve",
      label: PROPOSAL_ACTION_LABEL.approve,
      enabled: awaiting,
      disabledReason: awaiting ? null : decidedNote,
      reasonRequired: PROPOSAL_ACTION_REASON_REQUIRED.approve,
    },
    {
      action: "reject",
      label: PROPOSAL_ACTION_LABEL.reject,
      enabled: awaiting,
      disabledReason: awaiting ? null : decidedNote,
      reasonRequired: PROPOSAL_ACTION_REASON_REQUIRED.reject,
    },
    {
      action: "withdraw",
      label: PROPOSAL_ACTION_LABEL.withdraw,
      enabled: !ended && !executed,
      disabledReason: ended
        ? `既に「${PROPOSAL_STATUS_LABEL[status]}」として終了しています。`
        : executed
          ? "実行が記録されている提案は撤回できません。"
          : null,
      reasonRequired: PROPOSAL_ACTION_REASON_REQUIRED.withdraw,
    },
  ];
}

/** Deterministic display order: awaiting decisions first (they are what a
 * human must act on), then the rest, newest first, with `id` as a total
 * tie-break so the same facts always produce the same order. */
export function sortProposals(
  proposals: readonly FlowExperimentProposalOut[],
): FlowExperimentProposalOut[] {
  const rank = (status: FlowExperimentStatus) => (status === "proposed" ? 0 : 1);
  return [...proposals].sort((a, b) => {
    const ra = rank(a.status);
    const rb = rank(b.status);
    if (ra !== rb) return ra - rb;
    if (b.created_at !== a.created_at) return b.created_at - a.created_at;
    return b.id - a.id;
  });
}

// ---------------------------------------------------------------------------
// §5.1 — the assignment audit
// ---------------------------------------------------------------------------

export const RECORD_KIND_LABEL: Record<"assign" | "revoke", string> = {
  assign: "割り当て",
  revoke: "終了（revoke）",
};

export interface AssignmentDescription {
  scopeLabel: string;
  /** The prefixed `scope_ref`, or the System itself when the ref is empty. */
  scopeRef: string;
  recordLabel: string;
  modeLabel: string | null;
  previousModeLabel: string | null;
  window: string;
  actorLabel: string;
}

/** Describe one append-only audit row. Every value shown is a stored column;
 * nothing here recomputes what the row meant. */
export function describeAssignment(row: ExecutionModeAssignmentOut): AssignmentDescription {
  const windowParts: string[] = [];
  if (row.effective_from) windowParts.push("開始あり");
  else windowParts.push("開始の下限なし");
  if (row.effective_until) windowParts.push("期限あり（切れると fixed へ倒れます）");
  else windowParts.push("期限なし（人が終了するまで）");
  return {
    scopeLabel: MODE_SCOPE_KIND_LABEL[row.scope_kind],
    scopeRef: row.scope_ref || "(System 全体)",
    recordLabel: RECORD_KIND_LABEL[row.record_kind],
    modeLabel: row.mode ? EXECUTION_MODE_LABEL[row.mode] : null,
    previousModeLabel: row.previous_mode ? EXECUTION_MODE_LABEL[row.previous_mode] : null,
    window: row.record_kind === "revoke" ? "—" : windowParts.join(" / "),
    actorLabel: `${row.actor ?? "(記録なし)"}（${row.actor_kind} / ${row.decision_method}）`,
  };
}

/** The projection's Node rows in a stable order: `node_key` ascending. No
 * ranking, no severity score — just a reproducible list. */
export function sortModeNodes(
  nodes: readonly ExecutionModeNodeProjectionOut[],
): ExecutionModeNodeProjectionOut[] {
  return [...nodes].sort((a, b) => a.node_key.localeCompare(b.node_key));
}
