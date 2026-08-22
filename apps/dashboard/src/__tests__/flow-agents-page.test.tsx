/// <reference types="vitest/globals" />
// Epic #412 (#414 / #415): the Flow・エージェント群 screen.
//
// The page-level tests cover what only a render can show: that the two Flow
// kinds arrive as two lists, that the five axes appear as five readings, that
// both `shadow` meanings stay visible side by side, that a degraded section
// says it could not be read while the rest of the page still renders, and
// that neither a mode assignment nor a proposal decision can be taken without
// an explicit human confirmation (§10).

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

class ApiError extends Error {
  status: number;
  detail: string;
  code?: string;
  denialCode?: string;
  constructor(status: number, detail: string, code?: string, denialCode?: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.code = code;
    this.denialCode = denialCode;
  }
}

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
  ApiError,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <MemoryRouter>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

const SUBJECTS = {
  system_id: 1,
  generated_at: 1_800_000_000,
  runtime_flows: [
    {
      subject_kind: "runtime_flow",
      subject_ref: "checkout",
      label: "checkout",
      trace_count: 12,
      first_at: 1_799_000_000,
      last_at: 1_800_000_000,
      linked_node_count: 1,
    },
  ],
  static_flows: [
    {
      subject_kind: "static_flow",
      subject_ref: "http:POST:/orders",
      label: "POST /orders",
      entrypoint_type: "http",
      category: "api",
      handler_path: "app/orders.py",
      handler_qualified_name: "create_order",
      snapshot_id: 4,
    },
  ],
  snapshot_id: 4,
  snapshot_state: "present",
  degraded_sections: [],
  degraded_detail: {},
};

const NODE = {
  node_id: 1,
  node_key: "address-normalizer",
  display_name: "住所正規化",
  membership_basis: "flow_link",
  execution_mode: "shadow",
  mode_source: "node",
  mode_reason: "node_assignment",
  mode_source_ref: "address-normalizer",
  mode_state: "present",
  mode_divergence: "unobserved",
  observed_mode: null,
  maturity: "validating",
  maturity_state: "present",
  folded_maturity: "validating",
  maturity_consistent: true,
  implementation_modality: "rule",
  implementation_modality_state: "present",
  improvement_status: null,
  improvement_status_state: "unavailable",
  // The SDK is only tracing while the control plane permits a comparison:
  // one axis says `shadow`, the other does not. A legitimate state (§1.2).
  sdk_policy_mode: "trace",
  sdk_policy_mode_state: "present",
  observation: null,
  observation_state: "unmeasured",
  monitoring_contract_declared: false,
  evidence: [],
  capability_refs: [],
  purpose_element_refs: [],
  feature_refs: [],
};

const EXPLANATION = {
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
  membership: {
    state: "resolved",
    node_keys: ["address-normalizer"],
    basis: { "address-normalizer": "flow_link" },
    detail: "",
  },
  purpose: null,
  responsibility: {
    edge_source: "runtime_span_parentage",
    node_order: ["normalize"],
    edges: [],
    contracts: [],
    external_boundaries: [],
    entry_ref: "normalize",
    entry_state: "present",
    truncated: false,
    diagnostics: [],
  },
  nodes: [NODE],
  open_items: { items: [] },
  experiments: { proposals: [], status_source: "flow_orchestration" },
  baseline: { nodes: [] },
  drilldown: {},
  rollup: [],
  // `purpose` is null AND named here: could not be read, not "there is none".
  degraded_sections: ["purpose"],
  degraded_detail: { purpose: "OperationalError: no such table: purpose_element" },
};

const MODE_PROJECTION = {
  system_id: 1,
  schema_version: "execution-mode-v1",
  generated_at: 1_800_000_000,
  system_decision: {
    mode: "fixed",
    source_scope: "default",
    source_ref: "",
    reason: "no_assignment",
    permitted_capabilities: [],
    scope_trace: [
      {
        scope_kind: "system",
        scope_ref: "",
        state: "unset",
        mode: null,
        assignment_id: null,
        effective_from: null,
        effective_until: null,
        open_row_count: 0,
      },
    ],
  },
  assignments: [],
  nodes: [
    {
      node_id: 1,
      node_key: "address-normalizer",
      maturity: "validating",
      execution_mode: "shadow",
      mode_source: "node",
      mode_reason: "node_assignment",
      source_ref: "address-normalizer",
      flow_refs: ["checkout"],
      divergence: "unobserved",
      observed_mode: null,
      observed_at: null,
    },
  ],
};

const PROPOSAL = {
  schema_version: "flow-experiment-v1",
  id: 5,
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
    {
      id: 1,
      target_node_key: "address-normalizer",
      target_role: "candidate_target",
      position: 0,
      note: "",
    },
  ],
  events: [],
  executions: [],
  promotion_candidates: [],
};

function routeGet(overrides: Record<string, unknown> = {}) {
  mockApi.get.mockImplementation((path: string) => {
    if (path.startsWith("/flow-explanation/subjects")) {
      return Promise.resolve(overrides.subjects ?? SUBJECTS);
    }
    if (path.startsWith("/flow-explanation")) {
      return Promise.resolve(overrides.explanation ?? EXPLANATION);
    }
    if (path === "/execution-modes") {
      return Promise.resolve(overrides.modes ?? MODE_PROJECTION);
    }
    if (path.startsWith("/execution-modes/assignments")) {
      return Promise.resolve(overrides.assignments ?? []);
    }
    if (path.startsWith("/flow-experiments")) {
      return Promise.resolve({
        system_id: 1,
        generated_at: 1_800_000_000,
        proposals: overrides.proposals ?? [PROPOSAL],
      });
    }
    return Promise.reject(new ApiError(404, "not found"));
  });
}

/** The page auto-selects the first Flow on first load, which re-keys the
 * explanation and experiment queries. Waiting for the SELECTED subject's
 * label keeps every assertion below on the settled screen instead of on the
 * intermediate loading state. */
async function settled() {
  await screen.findByText(/subject checkout に紐づく提案/);
  await waitFor(() => expect(screen.getByText("normalize-a")).toBeInTheDocument());
}

async function renderPage() {
  // Dynamic import: a static one is hoisted above `const mockApi`, so the
  // vi.mock factory would run before it exists (same pattern as
  // evolution-nodes.test.tsx).
  const { default: FlowAgentsPage } = await import("@/pages/flow-agents");
  return render(<FlowAgentsPage />, { wrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
});

test("runtime_flow と static_flow は別の見出しの下に並ぶ", async () => {
  routeGet();
  await renderPage();

  // Two headings, each labelling its own <section>. They are never one list.
  const runtimeHeading = await screen.findByText(/runtime_flow（実行時に観測された flow_id）/);
  const staticHeading = screen.getByText(/static_flow（pin した snapshot の entrypoint）/);
  expect(runtimeHeading.tagName).toBe("H3");
  expect(staticHeading.tagName).toBe("H3");

  const runtimeSection = runtimeHeading.closest("section")!;
  const staticSection = staticHeading.closest("section")!;
  expect(runtimeSection).not.toBe(staticSection);
  expect(within(runtimeSection).getByText("checkout")).toBeInTheDocument();
  expect(within(runtimeSection).queryByText("http:POST:/orders")).toBeNull();
  expect(within(staticSection).getByText("http:POST:/orders")).toBeInTheDocument();
  expect(within(staticSection).queryByText("checkout")).toBeNull();
});

test("Node の 5 軸が 5 つの読みとして並び、単一のまとめ badge を作らない", async () => {
  routeGet();
  await renderPage();

  expect(await screen.findByText("5 つの軸（互いから導出しません）")).toBeInTheDocument();
  for (const label of [
    "実行モード",
    "Node maturity",
    "実装方式",
    "Cell Improvement status",
    "SDK policy mode",
  ]) {
    expect(screen.getAllByText(label).length).toBeGreaterThan(0);
  }
  // 読めなかった軸は「取得できませんでした」で、値 0 や空欄にはしない。
  expect(
    screen.getAllByText(/取得できませんでした/).length,
  ).toBeGreaterThan(0);
  // 測る仕組みが無い観測は unmeasured で、missing とは別の文。
  expect(screen.getAllByText(/測る仕組みがありません/).length).toBeGreaterThan(0);
});

test("実行モードの shadow と SDK policy の shadow は両方の読みとして示される", async () => {
  routeGet();
  await renderPage();

  expect(await screen.findByText(/shadow が 2 か所に出ています/)).toBeInTheDocument();
  expect(screen.getByText(/正当な状態/)).toBeInTheDocument();
  expect(
    screen.getByText(/実行モード = shadow.*SDK policy mode = trace/s),
  ).toBeInTheDocument();
});

test("既定の fixed は「人が選んだ」とは別の文で表示される", async () => {
  routeGet();
  await renderPage();

  // System はどこにも割り当てが無いので既定の fixed。
  expect(
    await screen.findByText("既定（誰も選んでいません）"),
  ).toBeInTheDocument();
  // Node には割り当てがあるので「明示的に選択」。
  expect(screen.getAllByText(/明示的に選択されています/).length).toBeGreaterThan(0);
});

test("取得できなかった section は表示を落として理由を言い、他 section は残る", async () => {
  routeGet();
  await renderPage();

  expect(
    await screen.findByText("このセクションは取得できませんでした"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/OperationalError: no such table: purpose_element/),
  ).toBeInTheDocument();
  // 未解決事項は 0 件で、これは「取得できなかった」とは別の文。
  expect(
    screen.getByText(/未解決事項は 0 件です。（取得できなかったのではありません）/),
  ).toBeInTheDocument();
  // 他のセクションは通常どおり描画される。
  expect(screen.getByText("構成 Node（5 軸）")).toBeInTheDocument();
});

test("モードの割り当てには確認と理由が要り、ボタンだけでは記録されない", async () => {
  routeGet();
  mockApi.post.mockResolvedValue({});
  await renderPage();
  await settled();

  // node スコープには参照が要る。入力するまで送信できない。
  fireEvent.change(screen.getByLabelText("スコープ参照"), {
    target: { value: "address-normalizer" },
  });
  fireEvent.click(screen.getByText("割り当てを記録する…"));

  const confirm = await screen.findByRole("button", { name: "割り当てを記録する" });
  expect(confirm).toBeDisabled();
  expect(mockApi.post).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText(/理由（必須）/), {
    target: { value: "誤変換の比較を 1 週間だけ試す" },
  });
  fireEvent.click(screen.getByRole("button", { name: "割り当てを記録する" }));

  await waitFor(() => expect(mockApi.post).toHaveBeenCalled());
  const [path, body] = mockApi.post.mock.calls[0];
  expect(path).toBe("/execution-modes/assignments");
  expect(body.reason).toBe("誤変換の比較を 1 週間だけ試す");
  // actor / decision_method は本文に載せない（サーバーが principal から取る）。
  expect(body).not.toHaveProperty("actor");
  expect(body).not.toHaveProperty("decision_method");
});

test("提案の承認は確認を挟み、拒否はサーバーの有限コードをそのまま出す", async () => {
  routeGet();
  mockApi.post.mockRejectedValue(
    new ApiError(409, "候補実行は許可されていません", undefined, "capability_not_permitted"),
  );
  await renderPage();
  await settled();

  fireEvent.click(screen.getByRole("button", { name: "承認する…" }));
  expect(mockApi.post).not.toHaveBeenCalled();

  fireEvent.click(await screen.findByRole("button", { name: "承認を記録する" }));

  await waitFor(() =>
    expect(mockApi.post).toHaveBeenCalledWith("/flow-experiments/5/approve", {
      reason: "",
    }),
  );
  expect(await screen.findByText("capability_not_permitted")).toBeInTheDocument();
});

test("承認済みの提案では承認ボタンが理由付きで無効になる", async () => {
  routeGet({ proposals: [{ ...PROPOSAL, status: "approved" }] });
  await renderPage();
  await settled();

  expect(screen.getByRole("button", { name: "承認する…" })).toBeDisabled();
  expect(
    screen.getAllByText(/この操作は承認待ちの提案にだけ記録できます/).length,
  ).toBeGreaterThan(0);
});

test("提案の必須項目が承認前の判断材料として表示される", async () => {
  routeGet();
  await renderPage();
  await settled();

  expect(screen.getByText("承認の判断材料（提案の必須項目）")).toBeInTheDocument();
  for (const label of [
    "目的",
    "仮説",
    "対象範囲（対象 Node）",
    "baseline 参照",
    "候補",
    "評価軸",
    "quality floor",
    "副作用の隔離戦略",
    "コスト上限",
    "停止条件",
    "rollback 計画",
    "根拠",
  ]) {
    expect(screen.getAllByText(label).length).toBeGreaterThan(0);
  }
  // 承認は実行許可ではない、を画面が言う。
  expect(screen.getByText(/承認は実行許可ではありません/)).toBeInTheDocument();
});
