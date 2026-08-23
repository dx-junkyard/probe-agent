/// <reference types="vitest/globals" />
// Epic #412 (#414 / #415): the Flow・エージェント群 display model.
//
// The tests pin the four properties the whole Epic exists to protect. Each one
// is a defect the screen would otherwise reintroduce, not a style preference:
//
// 1. 単一スコアを作らない (ADR-7 / #353). Five axes are five readings; there is
//    no combined value, no average, no completion rate, no confidence
//    percentage anywhere in this module.
// 2. missing / unavailable / unmeasured / stale / not_applicable は 5 つの別の
//    答え (§6.4). Two of them sharing copy is the #366 defect (one displayed
//    word carrying two facts).
// 3. `mode_source: "default"` (既定の fixed) と human-chosen `fixed`
//    (`system_assignment`) は別の事実 (§4.4).
// 4. degraded section は表示を落として「読めなかった」と言う。0 件と混ぜない
//    (#356), 推測値を代入しない (#380).

import * as model from "@/components/flow-agents/model";
import {
  FACT_STATE_DESCRIPTION,
  FACT_STATE_LABEL,
  MISSING_FACT_STATES,
  MODE_REASON_LABEL,
  MODE_REASON_NEXT_ACTION,
  PROPOSAL_STATUS_LABEL,
  degradedSections,
  describeModeOrigin,
  describeShadowReadings,
  evaluationLevelGroups,
  groupOpenItems,
  nodeAxes,
  proposalActions,
  proposalRequirements,
  sectionAvailability,
  sortProposals,
  subjectGroups,
} from "@/components/flow-agents/model";
import type {
  ExecutionModeReason,
  FlowExperimentProposalOut,
  FlowExplanationNodeOut,
  FlowExplanationOut,
  FlowFactState,
  FlowSubjectListOut,
} from "@/api/types";

function node(overrides: Partial<FlowExplanationNodeOut> = {}): FlowExplanationNodeOut {
  return {
    node_id: 1,
    node_key: "address-normalizer",
    display_name: "住所正規化",
    membership_basis: "flow_link",
    execution_mode: "fixed",
    mode_source: "default",
    mode_reason: "no_assignment",
    mode_source_ref: "",
    mode_state: "present",
    mode_divergence: "unobserved",
    observed_mode: null,
    maturity: "validating",
    maturity_state: "present",
    folded_maturity: "validating",
    maturity_consistent: true,
    implementation_modality: null,
    implementation_modality_state: "missing",
    improvement_status: null,
    improvement_status_state: "unavailable",
    sdk_policy_mode: null,
    sdk_policy_mode_state: "missing",
    observation: null,
    observation_state: "unmeasured",
    monitoring_contract_declared: false,
    evidence: [],
    capability_refs: [],
    purpose_element_refs: [],
    feature_refs: [],
    ...overrides,
  };
}

function explanation(overrides: Partial<FlowExplanationOut> = {}): FlowExplanationOut {
  return {
    system_id: 1,
    schema_version: "flow-explanation-v1",
    generated_at: 1_800_000_000,
    subject: {
      subject_kind: "runtime_flow",
      subject_ref: "checkout",
      label: "checkout",
      resolution: "resolved",
      snapshot_state: "not_applicable",
      snapshot_id: null,
      commit_sha: null,
      detail: "",
    },
    membership: { state: "resolved", node_keys: ["address-normalizer"], basis: {}, detail: "" },
    purpose: null,
    responsibility: null,
    nodes: [node()],
    open_items: { items: [] },
    experiments: { proposals: [], status_source: "flow_orchestration" },
    baseline: { nodes: [] },
    drilldown: {},
    rollup: [],
    degraded_sections: [],
    degraded_detail: {},
    ...overrides,
  };
}

function proposal(
  overrides: Partial<FlowExperimentProposalOut> = {},
): FlowExperimentProposalOut {
  return {
    schema_version: "flow-experiment-v1",
    id: 1,
    system_id: 1,
    proposal_key: "normalize-a",
    flow_subject_kind: "runtime_flow",
    flow_subject_ref: "checkout",
    captured_snapshot_id: null,
    comparison_scope: "single_node",
    title: "住所正規化の候補比較",
    purpose: "誤変換を減らす",
    hypothesis: "rule から lm_program にすると誤変換が減る",
    baseline_ref: "impl:12",
    candidate_refs: ["impl:13"],
    evaluation_axes: [{ level: "node", name: "誤変換率" }],
    evaluation_axes_by_level: { node: [{ level: "node", name: "誤変換率" }] },
    quality_floor: { node: { min: 0.9 } },
    isolation_strategy: "isolated_workspace",
    isolation_detail: "",
    cost_cap: { usd: 1 },
    stop_conditions: ["誤変換率が baseline を下回ったら停止"],
    rollback_plan: "安定実装へ戻す",
    evidence_refs: ["trace:9"],
    expires_at: null,
    decision_method: "manual",
    intelligence_run_id: null,
    created_by: "admin",
    created_at: 1_800_000_000,
    status: "proposed",
    status_derived_at: 1_800_000_100,
    targets: [
      { id: 1, target_node_key: "address-normalizer", target_role: "candidate_target", position: 0, note: "" },
    ],
    events: [],
    executions: [],
    promotion_candidates: [],
    ...overrides,
  };
}

// --- 1. no aggregate ------------------------------------------------------

describe("単一スコアを作らない (ADR-7 / #353)", () => {
  it("model は score / average / completion / confidence を名乗る export を持たない", () => {
    const offending = Object.keys(model).filter((key) =>
      /score|average|completion|confidence|percent|ratio|health/i.test(key),
    );
    expect(offending).toEqual([]);
  });

  it("nodeAxes は 5 つの独立した読みを返し、合成値を持たない", () => {
    const axes = nodeAxes(node());
    expect(axes.map((axis) => axis.key)).toEqual([
      "execution_mode",
      "maturity",
      "implementation_modality",
      "improvement_status",
      "sdk_policy_mode",
    ]);
    for (const axis of axes) {
      expect(Object.keys(axis).sort()).toEqual(["hint", "key", "label", "state", "value"]);
    }
  });

  it("各軸は自分の state を持ち、読めなかった軸が既定値にならない", () => {
    const axes = nodeAxes(node());
    const byKey = Object.fromEntries(axes.map((axis) => [axis.key, axis]));
    // improvement_status は「取得できなかった」、sdk_policy_mode は「存在しない」。
    expect(byKey.improvement_status.state).toBe("unavailable");
    expect(byKey.sdk_policy_mode.state).toBe("missing");
    expect(byKey.improvement_status.value).toBeNull();
  });

  it("評価軸の 3 契約は合成せず、空の level も残る", () => {
    const groups = evaluationLevelGroups(proposal());
    expect(groups.map((group) => group.level)).toEqual([
      "node",
      "flow_capability",
      "ux_outcome",
    ]);
    expect(groups[1].axes).toEqual([]);
  });
});

// --- 2. the five missing answers ------------------------------------------

describe("欠損の 5 語彙を丸めない (§6.4)", () => {
  const states: FlowFactState[] = [
    "present",
    "missing",
    "unavailable",
    "unmeasured",
    "stale",
    "not_applicable",
  ];

  it("6 つのラベルがすべて異なる", () => {
    const labels = states.map((state) => FACT_STATE_LABEL[state]);
    expect(new Set(labels).size).toBe(states.length);
  });

  it("6 つの説明文がすべて異なる", () => {
    const details = states.map((state) => FACT_STATE_DESCRIPTION[state]);
    expect(new Set(details).size).toBe(states.length);
  });

  it("missing と unavailable は決して同じ文にならない", () => {
    expect(FACT_STATE_LABEL.missing).not.toBe(FACT_STATE_LABEL.unavailable);
    expect(FACT_STATE_LABEL.unmeasured).not.toBe(FACT_STATE_LABEL.missing);
    expect(FACT_STATE_LABEL.not_applicable).not.toBe(FACT_STATE_LABEL.missing);
    expect(FACT_STATE_LABEL.stale).not.toBe(FACT_STATE_LABEL.unavailable);
  });

  it("present 以外の 5 値が語彙として残っている", () => {
    expect([...MISSING_FACT_STATES]).toEqual([
      "missing",
      "unavailable",
      "unmeasured",
      "stale",
      "not_applicable",
    ]);
  });
});

// --- 3. default vs a chosen fixed -----------------------------------------

describe("既定の fixed と人が選んだ fixed を区別する (§4.4)", () => {
  it("mode_source: default は「選ばれていない」と読む", () => {
    const origin = describeModeOrigin("default", "no_assignment");
    expect(origin.chosen).toBe(false);
    expect(origin.headline).toContain("既定");
  });

  it("system_assignment は「明示的に選択されている」と読む", () => {
    const origin = describeModeOrigin("system", "system_assignment", "");
    expect(origin.chosen).toBe(true);
    expect(origin.headline).toContain("明示的に選択");
  });

  it("既定と明示選択は同じ文にならない", () => {
    expect(describeModeOrigin("default", "no_assignment").headline).not.toBe(
      describeModeOrigin("system", "system_assignment").headline,
    );
  });

  it("fail-closed で倒れた fixed は「人の選択ではない」と言う", () => {
    const origin = describeModeOrigin("none", "flow_scope_conflict");
    expect(origin.chosen).toBe(false);
    expect(origin.headline).toContain("安全側");
  });

  it("期限切れ 3 コードは別々の文と別々の次の操作を持つ", () => {
    const expired: ExecutionModeReason[] = [
      "node_expired_assignment",
      "flow_expired_assignment",
      "system_expired_assignment",
    ];
    const labels = expired.map((reason) => MODE_REASON_LABEL[reason]);
    const actions = expired.map((reason) => MODE_REASON_NEXT_ACTION[reason]);
    expect(new Set(labels).size).toBe(3);
    expect(new Set(actions).size).toBe(3);
  });

  it("11 個の reason が 11 個の別の文を持つ", () => {
    const reasons = Object.keys(MODE_REASON_LABEL) as ExecutionModeReason[];
    expect(reasons).toHaveLength(11);
    expect(new Set(reasons.map((r) => MODE_REASON_LABEL[r])).size).toBe(11);
    // The next action differs per reason too: knowing WHY it is fixed and
    // knowing WHERE to fix it are two facts.
    expect(new Set(reasons.map((r) => MODE_REASON_NEXT_ACTION[r])).size).toBe(11);
  });
});

// --- the two `shadow` meanings --------------------------------------------

describe("SDK policy の shadow と実行モードの shadow を混ぜない (§1.2)", () => {
  it("両方が shadow でも 2 つの読みとして返る", () => {
    const readings = describeShadowReadings(
      node({ execution_mode: "shadow", sdk_policy_mode: "shadow", sdk_policy_mode_state: "present" }),
    );
    expect(readings.bothShadow).toBe(true);
    expect(readings.executionMode).toBe("shadow");
    expect(readings.sdkPolicyMode).toBe("shadow");
    expect(readings.note).toContain("別の事実");
  });

  it("片方だけ shadow は正当な状態として説明される", () => {
    const readings = describeShadowReadings(
      node({ execution_mode: "shadow", sdk_policy_mode: "trace", sdk_policy_mode_state: "present" }),
    );
    expect(readings.onlyOneShadow).toBe(true);
    expect(readings.bothShadow).toBe(false);
    expect(readings.note).toContain("正当な状態");
  });

  it("どちらも shadow でなければ注記は出ない", () => {
    expect(describeShadowReadings(node()).note).toBeNull();
  });
});

// --- 4. degraded sections -------------------------------------------------

describe("degraded section は表示を落として理由を言う (§6.4 / #380)", () => {
  it("degraded な section は available=false と detail を返す", () => {
    const doc = explanation({
      purpose: null,
      degraded_sections: ["purpose"],
      degraded_detail: { purpose: "OperationalError: no such table" },
    });
    const availability = sectionAvailability(doc, "purpose");
    expect(availability.available).toBe(false);
    expect(availability.detail).toBe("OperationalError: no such table");
  });

  it("1 section の失敗が他 section を巻き込まない", () => {
    const doc = explanation({
      degraded_sections: ["purpose"],
      degraded_detail: { purpose: "boom" },
    });
    expect(degradedSections(doc).map((entry) => entry.key)).toEqual(["purpose"]);
    expect(sectionAvailability(doc, "nodes").available).toBe(true);
  });

  it("degraded でない空の section は available のまま（0 件と読めなかったは別）", () => {
    const doc = explanation({ open_items: { items: [] } });
    expect(sectionAvailability(doc, "open_items").available).toBe(true);
    expect(sectionAvailability(doc, "open_items").detail).toBeNull();
  });

  it("subject 一覧も種別ごとに degraded を分けて持つ", () => {
    const listing: FlowSubjectListOut = {
      system_id: 1,
      generated_at: 1,
      runtime_flows: [],
      static_flows: [],
      snapshot_id: null,
      snapshot_state: "missing",
      degraded_sections: ["static_flows"],
      degraded_detail: { static_flows: "boom" },
    };
    const groups = subjectGroups(listing);
    expect(groups.map((group) => group.kind)).toEqual(["runtime_flow", "static_flow"]);
    // 読めなかった一覧は null（0 件の空配列とは別の表示）。
    expect(groups[0].items).toEqual([]);
    expect(groups[1].items).toBeNull();
    expect(groups[1].degradedDetail).toBe("boom");
  });

  it("subject の 2 種別を 1 つのリストにまとめない", () => {
    const groups = subjectGroups(undefined);
    expect(groups).toHaveLength(2);
    expect(groups[0].kind).not.toBe(groups[1].kind);
  });
});

// --- open items and proposals ---------------------------------------------

describe("未解決事項と提案の表示規則", () => {
  it("open item は有限の kind でだけまとめ、空の group は落とす", () => {
    const groups = groupOpenItems([
      { id: "a", kind: "anomaly", label: "x", detail: "", node_key: null, missing_state: null, evidence_ids: [] },
      { id: "b", kind: "missing_fact", label: "y", detail: "", node_key: null, missing_state: "unavailable", evidence_ids: [] },
    ]);
    expect(groups.map((group) => group.kind)).toEqual(["anomaly", "missing_fact"]);
  });

  it("承認待ちの提案が先に並び、並びは決定的である", () => {
    const sorted = sortProposals([
      proposal({ id: 1, status: "completed", created_at: 30 }),
      proposal({ id: 2, status: "proposed", created_at: 10 }),
      proposal({ id: 3, status: "approved", created_at: 20 }),
    ]);
    expect(sorted.map((entry) => entry.id)).toEqual([2, 1, 3]);
  });

  it("承認待ちのときだけ承認・却下を提示し、それ以外は理由付きで無効化する", () => {
    const awaiting = proposalActions("proposed");
    expect(awaiting.map((action) => action.enabled)).toEqual([true, true, true]);

    const approved = proposalActions("approved");
    const approve = approved.find((action) => action.action === "approve")!;
    expect(approve.enabled).toBe(false);
    expect(approve.disabledReason).toContain(PROPOSAL_STATUS_LABEL.approved);

    const executed = proposalActions("executing");
    const withdraw = executed.find((action) => action.action === "withdraw")!;
    expect(withdraw.enabled).toBe(false);
    expect(withdraw.disabledReason).toContain("実行が記録");
  });

  it("却下と撤回は理由必須、承認は任意（サーバーの要求と一致）", () => {
    const actions = proposalActions("proposed");
    expect(actions.find((a) => a.action === "approve")!.reasonRequired).toBe(false);
    expect(actions.find((a) => a.action === "reject")!.reasonRequired).toBe(true);
    expect(actions.find((a) => a.action === "withdraw")!.reasonRequired).toBe(true);
  });

  it("承認の判断材料として §7.1 の 12 項目を並べる", () => {
    const requirements = proposalRequirements(proposal());
    expect(requirements).toHaveLength(12);
    expect(requirements.every((entry) => entry.recorded)).toBe(true);
    const empty = proposalRequirements(proposal({ rollback_plan: "" }));
    expect(empty.find((entry) => entry.key === "rollback_plan")!.recorded).toBe(false);
  });

  it("7 つの status が 7 つの別の文を持つ", () => {
    const labels = Object.values(PROPOSAL_STATUS_LABEL);
    expect(labels).toHaveLength(7);
    expect(new Set(labels).size).toBe(7);
  });
});

// ---------------------------------------------------------------------------
// 割り当てフォームの初期スコープ (§4)
//
// 実行モードの割り当ては LLM と候補実行の権限を与える人の判断なので、
// scope_kind と scope_ref が食い違い得るフォームは安全性の問題になる。
// ここで固定するのは「参照は常にその種別のものである」という不変条件で、
// 便利さの話ではない。
// ---------------------------------------------------------------------------

describe("割り当ての初期スコープは種別と参照が常に一致する (§4)", () => {
  test("runtime_flow を選んでいるときは flow スコープ + その前置詞付き参照", () => {
    expect(model.initialAssignScope({ kind: "runtime_flow", ref: "checkout" })).toEqual({
      scopeKind: "flow",
      scopeRef: "runtime_flow:checkout",
    });
    // 既に前置詞がある参照を二重に付けない。
    expect(
      model.initialAssignScope({ kind: "runtime_flow", ref: "runtime_flow:checkout" }),
    ).toEqual({ scopeKind: "flow", scopeRef: "runtime_flow:checkout" });
  });

  test("static_flow と未選択は node スコープ + 空の参照で開く", () => {
    // static_flow はモードスコープになれない (§2.1)。その参照を node の
    // node_key として送れてしまう状態を、構造的に作らない。
    expect(
      model.initialAssignScope({ kind: "static_flow", ref: "http:POST:/orders" }),
    ).toEqual({ scopeKind: "node", scopeRef: "" });
    expect(model.initialAssignScope(null)).toEqual({ scopeKind: "node", scopeRef: "" });
  });

  test("system は既定にならない（画面上もっとも広い権限付与は明示的な選択に限る）", () => {
    for (const selected of [
      { kind: "runtime_flow" as const, ref: "checkout" },
      { kind: "static_flow" as const, ref: "http:POST:/orders" },
      null,
    ]) {
      expect(model.initialAssignScope(selected).scopeKind).not.toBe("system");
    }
  });

  test("種別を変えると参照は持ち越されず、戻したときだけ再導出される", () => {
    const initial = model.initialAssignScope({ kind: "runtime_flow", ref: "checkout" });
    expect(model.scopeRefForKind("node", initial)).toBe("");
    expect(model.scopeRefForKind("system", initial)).toBe("");
    expect(model.scopeRefForKind("flow", initial)).toBe("runtime_flow:checkout");

    const nodeInitial = model.initialAssignScope(null);
    // node 起点なら flow へ切り替えても Flow の参照は生えてこない。
    expect(model.scopeRefForKind("flow", nodeInitial)).toBe("");
    expect(model.scopeRefForKind("node", nodeInitial)).toBe("");
  });

  test("どの経路でも、参照が別種別のものになることはない", () => {
    const subjects = [
      { kind: "runtime_flow" as const, ref: "checkout" },
      { kind: "static_flow" as const, ref: "http:POST:/orders" },
      null,
    ];
    const kinds = ["system", "flow", "node"] as const;
    for (const subject of subjects) {
      const initial = model.initialAssignScope(subject);
      for (const kind of kinds) {
        const ref = model.scopeRefForKind(kind, initial);
        if (kind === "system") expect(ref).toBe("");
        // Flow の参照は前置詞付き。node の欄にそれが入ることはない。
        if (kind === "node") expect(ref.startsWith("runtime_flow:")).toBe(false);
        if (kind === "flow" && ref !== "") {
          expect(ref.startsWith("runtime_flow:")).toBe(true);
        }
      }
    }
  });
});

// ---------------------------------------------------------------------------
// 入出力境界・依存関係・外部境界 (§6.3 item 2 / #414 の受け入れ条件)
// ---------------------------------------------------------------------------

describe("依存関係と入出力境界の読み (§6.3 item 2)", () => {
  test("edge_source ごとに、辺と外部境界を実際に読んだかどうかが分かれる", () => {
    const runtime = model.describeDependencyReading({
      edge_source: "runtime_span_parentage",
    });
    expect(runtime.edgesMeasured).toBe(true);
    // 外部境界は pin した snapshot の call graph からしか求まらないので、
    // 実行時の読みの空配列は「0 件」ではない。
    expect(runtime.boundariesMeasured).toBe(false);
    expect(runtime.boundaryUnmeasuredNote).not.toBe("");

    const staticGraph = model.describeDependencyReading({
      edge_source: "static_call_graph",
    });
    expect(staticGraph.edgesMeasured).toBe(true);
    expect(staticGraph.boundariesMeasured).toBe(true);

    for (const source of ["unavailable", "not_applicable"] as const) {
      const reading = model.describeDependencyReading({ edge_source: source });
      expect(reading.edgesMeasured).toBe(false);
      expect(reading.boundariesMeasured).toBe(false);
    }
  });

  test("4 つの edge_source は 4 つの別の文を持つ", () => {
    const sentences = (
      ["runtime_span_parentage", "static_call_graph", "unavailable", "not_applicable"] as const
    ).map((source) => model.describeDependencyReading({ edge_source: source }));
    const labels = sentences.map((entry) => entry.edgeSourceLabel);
    const descriptions = sentences.map((entry) => entry.edgeSourceDescription);
    expect(new Set(labels).size).toBe(4);
    expect(new Set(descriptions).size).toBe(4);
  });

  test("辺はサーバーの順序のまま、位置番号だけを添えて読める形にする", () => {
    const readings = model.flowEdgeReadings([
      {
        source: "create_order",
        target: "normalize",
        edge_kind: "call",
        // 静的読みの追加項目。types.ts の FlowResponsibilityEdgeOut には
        // まだ宣言が無いので、any 経由で実際のレスポンス形を渡す。
        ...({ resolution: "resolved", callee_name: "normalize", line: 12 } as object),
      },
      { source: "normalize", target: "writer", edge_kind: "span_parent", trace_id: "t-9" },
    ]);
    expect(readings.map((edge) => edge.position)).toEqual([1, 2]);
    expect(readings[0].source).toBe("create_order");
    expect(readings[0].edgeKindLabel).toBe("call / 関数呼び出し");
    expect(readings[0].detail).toContain("callee: normalize");
    expect(readings[0].detail).toContain("line: 12");
    expect(readings[1].edgeKindLabel).toBe("span_parent / span の親子");
    expect(readings[1].detail).toBe("trace: t-9");
    // 順位付けもスコアも作らない。位置は読むための番号でしかない。
    expect(readings.every((edge) => typeof edge.position === "number")).toBe(true);
  });

  test("未知の edge_kind は勝手に言い換えず、そのまま出す", () => {
    const [reading] = model.flowEdgeReadings([
      { source: "a", target: "b", edge_kind: "brand_new_kind" },
    ]);
    expect(reading.edgeKindLabel).toBe("brand_new_kind");
    expect(reading.detail).toBe("");
  });

  test("外部境界は記録された 3 つの項目だけを読み、欠けた種別を補完しない", () => {
    const readings = model.externalBoundaryReadings([
      {
        node_id: "external::database::db",
        boundary_kind: "database",
        qualified_name: "db.execute",
      },
      // 種別が空で届いた行。型としては 3 項目とも必須だが、空文字列は
      // 表現可能なので「種別が記録されていない」状態はこの形で届きうる。
      { node_id: "external::unknown::x", boundary_kind: "", qualified_name: "" },
    ]);
    expect(readings[0].boundaryKindLabel).toBe("DB");
    expect(readings[0].qualifiedName).toBe("db.execute");
    // 種別の記録が無い行に、それらしい種別を割り当てない。
    expect(readings[1].boundaryKind).toBe("");
    expect(readings[1].boundaryKindLabel).toBe("");
  });

  test("読めなかった契約は空の契約にならない（値を 1 つも持たない）", () => {
    const readings = model.contractReadings([
      {
        node_key: "address-normalizer",
        state: "present",
        mission: "住所文字列を正規化する",
        scope: "国内住所のみ",
        out_of_scope: "海外住所",
        input_contract: { raw_address: "string" },
        output_contract: { normalized: "string" },
        side_effect_class: "pure",
        trust_boundary: "internal",
      },
      // 読めなかった契約。mission などがレスポンスに載っていても、
      // present でない限り 1 つも表示側へ渡さない (#380)。
      { node_key: "order-writer", state: "unavailable", mission: "書き込み" },
    ]);
    expect(readings[0].input).toEqual([{ name: "raw_address", value: "string" }]);
    expect(readings[0].output).toEqual([{ name: "normalized", value: "string" }]);
    expect(readings[0].sideEffectClass).toBe("pure");

    expect(readings[1].state).toBe("unavailable");
    expect(readings[1].mission).toBe("");
    expect(readings[1].input).toEqual([]);
    expect(readings[1].output).toEqual([]);
    expect(readings[1].sideEffectClass).toBeNull();
  });

  test("契約の項目が 0 件なのと、契約が読めないのは別の答え", () => {
    const [empty] = model.contractReadings([
      {
        node_key: "n",
        state: "present",
        mission: "",
        scope: "",
        out_of_scope: "",
        input_contract: {},
        output_contract: {},
        side_effect_class: null,
        trust_boundary: null,
      },
    ]);
    expect(empty.state).toBe("present");
    expect(empty.input).toEqual([]);

    const [unreadable] = model.contractReadings([{ node_key: "n", state: "missing" }]);
    expect(unreadable.state).toBe("missing");
    expect(unreadable.input).toEqual([]);
    // 同じ空配列でも state が別なので、表示側は別の文を出せる。
    expect(empty.state).not.toBe(unreadable.state);
  });
});
