/// <reference types="vitest/globals" />
// Issue #396 (Epic #394 Phase 1): Evolution Node inspector のテスト。
//
// この画面で守らなければならないのは 3 点だけで、テストもその 3 点に絞る。
//
// 1. 4 つの軸を混ぜない (ADR-6)。maturity / Cell Improvement status /
//    SDK policy mode が食い違っていても、そのまま 3 つの読みとして出す。
//    4 つめの軸は「この画面では扱いません」と書く — 空欄や null 表示にすると
//    「判定した結果 何もなかった」に見える。
// 2. 「取得できませんでした」(availability === false) と「未リンク」(値が null)
//    は別の文である (#380)。読めなかった事実を既定値として出さない。
// 3. 遷移が拒否されたとき、サーバーの有限な reason_code をそのまま出す。
//    画面は遷移表を持たないので、言い換えも先回りの無効化もできない。

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { EvolutionNodeProjectionOut } from "@/api/types";

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
  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.code = code;
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

function makeWrapper(initialPath = "/evolution-nodes") {
  return function wrapper({ children }: { children: ReactNode }) {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return (
      <MemoryRouter initialEntries={[initialPath]}>
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      </MemoryRouter>
    );
  };
}

const NODE_SUMMARY = {
  id: 7,
  system_id: 1,
  node_key: "address-normalizer",
  display_name: "住所正規化",
  maturity: "validating" as const,
  current_version_id: 1,
  current_implementation_id: 2,
  stable_implementation_id: null,
  rollback_implementation_id: null,
  monitoring_contract_ref: null,
  created_at: 1_700_000_000,
  updated_at: 1_700_000_100,
};

function projection(
  overrides: Partial<EvolutionNodeProjectionOut> = {},
): EvolutionNodeProjectionOut {
  return {
    schema_version: "1.0",
    system_id: 1,
    node_id: 7,
    node_key: "address-normalizer",
    display_name: "住所正規化",
    maturity: "validating",
    current_version: {
      id: 1, version_number: 1, mission: "住所文字列を正規化する",
      scope: "", out_of_scope: "", input_contract: {}, output_contract: {},
      side_effect_class: "pure", trust_boundary: "internal",
      establishment_criteria: [], reopen_criteria: [], evaluation_policy_refs: [],
      decision_method: "manual", created_by: "root", created_at: 1_700_000_000,
      superseded_by_id: null,
    },
    current_implementation: {
      id: 2, implementation_number: 1, node_version_id: 1,
      modality: "reasoning_llm", config: {}, snapshot_id: null, commit_sha: null,
      environment_ref: null, provenance: {}, decision_method: "manual",
      created_by: "root", created_at: 1_700_000_000, superseded_by_id: null,
    },
    stable_implementation: null,
    rollback_implementation: null,
    links: [],
    events: [],
    improvement_status: "adopted",
    policy_mode: "shadow",
    availability: {
      version: true, implementation: true, stable_implementation: true,
      rollback_implementation: true, links: true, events: true,
      improvement_status: true, policy_mode: true,
    },
    updated_at: 1_700_000_100,
    ...overrides,
  };
}

function routeGet(overrides: Partial<EvolutionNodeProjectionOut> = {}) {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/evolution-nodes") return Promise.resolve({ nodes: [NODE_SUMMARY] });
    if (path === "/evolution-nodes/7") return Promise.resolve(projection(overrides));
    if (path === "/evolution-nodes/7/events") {
      return Promise.resolve({ node_id: 7, events: [] });
    }
    return Promise.reject(new ApiError(404, "not found"));
  });
}

async function renderPage(initialPath = "/evolution-nodes") {
  // Loaded dynamically: a static import is hoisted above `const mockApi`,
  // so the vi.mock factory would run before it exists. Same pattern as
  // cell-fabric.test.tsx.
  const { default: EvolutionNodesPage } = await import("@/pages/evolution-nodes");
  return render(<EvolutionNodesPage />, { wrapper: makeWrapper(initialPath) });
}

beforeEach(() => {
  vi.clearAllMocks();
});

test("三つの軸が食い違っていてもそのまま別々に表示される", async () => {
  routeGet();
  await renderPage();

  fireEvent.click(await screen.findByText("address-normalizer"));

  // maturity は validating、Improvement は adopted、policy は shadow。
  // これは正当な組み合わせであり、まとめた 1 語にしてはならない。
  expect(await screen.findByText("Cell Improvement status")).toBeInTheDocument();
  expect(screen.getByText("adopted")).toBeInTheDocument();
  expect(screen.getByText("shadow")).toBeInTheDocument();
  expect(screen.getAllByText(/validating/).length).toBeGreaterThan(0);

  // 4 つめの軸は「まだ扱わない」と明示する。空欄にすると
  // 「判定したが何もなかった」と読めてしまう。
  expect(screen.getByText("Dashboard user phase")).toBeInTheDocument();
  expect(screen.getByText("この画面では扱いません")).toBeInTheDocument();
});

test("読めなかった軸は「取得できませんでした」で、未リンクとは別の文になる", async () => {
  routeGet({
    improvement_status: null,
    policy_mode: null,
    availability: {
      version: true, implementation: true, stable_implementation: true,
      rollback_implementation: true, links: true, events: true,
      improvement_status: false, // 読めなかった
      policy_mode: true, // リンクが無いだけ
    },
  });
  await renderPage();

  fireEvent.click(await screen.findByText("address-normalizer"));

  expect(await screen.findByText("取得できませんでした")).toBeInTheDocument();
  expect(screen.getByText("未リンク")).toBeInTheDocument();
});

test("拒否された遷移は有限な reason_code に対応する日本語文で何が足りないかを示す", async () => {
  routeGet();
  mockApi.post.mockRejectedValue(
    new ApiError(422, "target 'established' requires evidence", "evidence_missing"),
  );
  await renderPage();

  fireEvent.click(await screen.findByText("address-normalizer"));
  fireEvent.click(await screen.findByText("established / 固定化済み"));
  fireEvent.click(screen.getByText("遷移を要求する"));

  // 画面は遷移表を持たないので先回りの無効化はしない。拒否されたら、
  // サーバーの有限コードに対応する固定の日本語文を出し、コードは補足に残す。
  const box = await screen.findByTestId("evolution-transition-rejection");
  expect(box).toHaveTextContent("根拠 (evidence) を 1 件以上指定する必要があります");
  expect(box).toHaveTextContent("evidence_missing");
  expect(box).not.toHaveTextContent("target 'established' requires evidence");
});

test("表に無い拒否コードはサーバーの文言へ fallback する", async () => {
  routeGet();
  mockApi.post.mockRejectedValue(new ApiError(422, "server says no", "some_future_code"));
  await renderPage();

  fireEvent.click(await screen.findByText("address-normalizer"));
  fireEvent.click(await screen.findByText("遷移を要求する"));

  const box = await screen.findByTestId("evolution-transition-rejection");
  expect(box).toHaveTextContent("server says no");
  expect(box).toHaveTextContent("some_future_code");
});

test("?node=<node_key> の deep link で該当 Node が選択されて開く", async () => {
  routeGet();
  await renderPage("/evolution-nodes?node=address-normalizer");

  expect(await screen.findByText("Cell Improvement status")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /address-normalizer/, pressed: true })).toBeInTheDocument();
});

test("?node= の Node が無いときは別の Node を代わりに選ばず、その旨を出す", async () => {
  routeGet();
  await renderPage("/evolution-nodes?node=missing-node");

  expect(await screen.findByTestId("evolution-node-selection-not-found")).toHaveTextContent("missing-node");
  expect(screen.queryByText("Cell Improvement status")).not.toBeInTheDocument();
  expect(mockApi.get).not.toHaveBeenCalledWith("/evolution-nodes/7");
});

test("?node= があり一覧取得に失敗したときは、選べなかったことを出す", async () => {
  mockApi.get.mockRejectedValue(new ApiError(500, "boom"));
  await renderPage("/evolution-nodes?node=address-normalizer");

  expect(await screen.findByTestId("evolution-node-selection-unavailable")).toHaveTextContent("address-normalizer");
});

test("重複した遷移要求は失敗ではなく重複として扱われる", async () => {
  routeGet();
  mockApi.post.mockResolvedValue({
    applied: false, duplicate: true, maturity: "validating", event: null,
  });
  const { toast } = await import("sonner");
  await renderPage();

  fireEvent.click(await screen.findByText("address-normalizer"));
  fireEvent.click(await screen.findByText("遷移を要求する"));

  await waitFor(() =>
    expect(toast.success).toHaveBeenCalledWith("同じ要求が既に適用済みです（重複）"),
  );
});

test("一覧の取得に失敗しても空ページではなく再試行を出す", async () => {
  mockApi.get.mockRejectedValue(new ApiError(500, "boom"));
  await renderPage();

  expect(await screen.findByText(/取得できませんでした/)).toBeInTheDocument();
  expect(screen.getByText("再試行")).toBeInTheDocument();
});

// UX 監査 2026-11-17 C7: どの判断がどこで行えるかを画面から特定できる。
test("Node 詳細に判断ごとの場所が出て、画面に無い判断は API のみと endpoint を示す", async () => {
  routeGet();
  await renderPage("/evolution-nodes?node=address-normalizer");

  const table = await screen.findByTestId("evolution-decision-locations");
  const { within } = await import("@testing-library/react");

  const transition = within(table).getByTestId("evolution-decision-maturity_transition");
  expect(transition).toHaveTextContent("この画面でできる");

  const version = within(table).getByTestId("evolution-decision-register_version");
  expect(version).toHaveTextContent("画面未対応（API のみ）");
  // {node_id} はこの Node の id に置き換わる。
  expect(version).toHaveTextContent("POST /evolution-nodes/7/versions");

  const approval = within(table).getByTestId("evolution-decision-stabilization_approval");
  expect(approval).toHaveTextContent("POST /stabilization/packages/{package_id}/approve");

  for (const id of ["stabilization_parent_review", "monitoring_contract", "reopen_approval", "flow_experiment_proposal", "add_link", "register_implementation", "stabilization_package"]) {
    expect(within(table).getByTestId(`evolution-decision-${id}`)).toHaveTextContent("画面未対応（API のみ）");
  }
  expect(within(table).getByTestId("evolution-decision-monitoring_contract"))
    .toHaveTextContent("POST /node-operations/nodes/7/monitoring-contracts");
  expect(within(table).getByTestId("evolution-decision-flow_experiment_proposal"))
    .toHaveTextContent("POST /flow-experiments/draft");

  const mode = within(table).getByTestId("evolution-decision-execution_mode_assignment");
  expect(mode).toHaveTextContent("Flow・エージェント群でできる");
  expect(within(mode).getByRole("link", { name: "Flow・エージェント群を開く" }))
    .toHaveAttribute("href", "/flow-agents");
});

test("画面に無い操作を求める拒否は、判断表の該当行 (API のみ) へ案内する", async () => {
  routeGet();
  mockApi.post.mockRejectedValue(
    new ApiError(422, "no version", "version_missing"),
  );
  await renderPage("/evolution-nodes?node=address-normalizer");

  fireEvent.click(await screen.findByText("遷移を要求する"));
  const box = await screen.findByTestId("evolution-transition-rejection");
  expect(box).toHaveTextContent("画面未対応で API のみです");
  expect(box).not.toHaveTextContent("先に契約 version を登録してください");
  const guide = screen.getByTestId("evolution-transition-rejection-decision");
  expect(guide).toHaveAttribute("href", "#evolution-decision-register_version");
  expect(document.getElementById("evolution-decision-register_version")).not.toBeNull();
});
