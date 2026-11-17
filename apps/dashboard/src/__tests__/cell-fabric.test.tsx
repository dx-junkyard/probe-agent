/// <reference types="vitest/globals" />
// Issue #303: Cell Fabric ページ(Root Orchestrator 統合ダイジェスト + Ask
// 一覧)のテスト。
//
// 1. 4段の progressive disclosure (結論 -> 詳細 -> エビデンス -> 監査情報)
//    が正しく描画され、詳細/エビデンス/監査情報は初期状態では折りたたまれて
//    いること。
// 2. sev1 の top_risks が結論カードに表示されること。
// 3. Ask一覧の承認/保留/却下ボタンが decide API を正しいペイロードで呼ぶこと。
// 4. 「承認は実行を意味しない」旨のヘルパーテキストが表示されること。

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { CellAskOut, CellRootDigestOut } from "@/api/types";

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
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
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

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

function makeDigest(): CellRootDigestOut {
  return {
    system_id: 1,
    generated_at: 1700000000,
    digest: {
      generated_at: 1700000000,
      conclusion: {
        overall_severity: "error",
        counts: {
          orchestrators: 1,
          tasks: { todo: 1, doing: 0, review: 0, done: 1, failed: 0, blocked: 0 },
          escalations_open_by_severity: { sev1: 1, sev2: 1, sev3: 1 },
          asks_open: 1,
        },
        top_risks: [
          {
            kind: "escalation",
            dedupe_key: "hash-1",
            cell_id: "worker-a",
            severity: "sev1",
            summary: "重大な問題が発生しています",
            evidence_refs: ["trace:t-1"],
            sources: [1],
            created_at: 1700000000,
          },
        ],
      },
      key_points: [
        {
          type: "orchestrator",
          cell_id: "orch-1",
          progress: { by_cell: {}, total: { todo: 1, doing: 0, review: 0, done: 1, failed: 0, blocked: 0 } },
          quality: [{
            cell_id: "worker-a",
            pass_rate: 0.75,
            audited_count: 8,
            intake_status: "suspended",
            sample_rate: 0.2,
          }],
          topology: [{
            cell_id: "worker-a",
            feature_refs: ["feature-a"],
            capability_refs: ["capability-a"],
            entrypoint_refs: ["POST:/jobs"],
          }],
          escalations_open_by_severity: { sev1: 1, sev2: 1, sev3: 1 },
          bottleneck_candidates: [],
          binding_stale: false,
        },
        {
          type: "escalation_pending_decision",
          kind: "escalation",
          dedupe_key: "hash-2",
          cell_id: "worker-b",
          severity: "sev2",
          summary: "判断が必要な問題です",
          evidence_refs: [],
          sources: [2],
          created_at: 1700000000,
        },
      ],
      evidence: {
        sev3_escalations: [
          {
            kind: "escalation",
            dedupe_key: "hash-3",
            cell_id: "worker-a",
            severity: "sev3",
            summary: "参考情報です",
            evidence_refs: [],
            sources: [3],
            created_at: 1700000000,
          },
        ],
        tasks: [],
      },
      audit: {
        generated_at: 1700000000,
        system_state: {
          generated_at: 1700000000,
          overall_severity: "warning",
          user_phase: "observation",
          primary_item: null,
          items: [],
        },
        is_stale: false,
        stale_orchestrator_cell_ids: [],
        decision_method_notes: "deterministic aggregation",
      },
    },
  };
}

function makeAsk(overrides: Partial<CellAskOut> & { id: number }): CellAskOut {
  return {
    system_id: 1,
    source_kind: "escalation",
    source_id: 1,
    cell_definition_id: "worker-a",
    goal_id: null,
    task_id: null,
    ask_text: "この問題にどう対応しますか?",
    severity: "sev1",
    status: "open",
    decision: "",
    decision_method: "",
    decided_by: null,
    decided_at: null,
    execution_approved: false,
    dedupe_key: "escalation:1",
    created_at: 1700000000,
    ...overrides,
  } as CellAskOut;
}

let getImpl: (path: string) => unknown;

beforeEach(() => {
  vi.clearAllMocks();
  getImpl = () => Promise.resolve(undefined);
  mockApi.get.mockImplementation((path: string) => getImpl(path));
});

describe("CellFabricPage", () => {
  test("結論は常に表示され、詳細/エビデンス/監査情報は初期状態で折りたたまれている", async () => {
    const digest = makeDigest();
    const ask = makeAsk({ id: 1 });
    getImpl = (path: string) => {
      if (path === "/cell-fabric/root-digest") return Promise.resolve(digest);
      if (path === "/cell-fabric/asks?status=open") return Promise.resolve({ system_id: 1, asks: [ask] });
      return Promise.resolve(undefined);
    };

    const { default: CellFabricPage } = await import("@/pages/cell-fabric");
    render(<CellFabricPage />, { wrapper: createWrapper() });

    const conclusion = await screen.findByTestId("cell-fabric-conclusion");
    expect(conclusion).toHaveTextContent("重大な問題が発生しています");
    expect(conclusion).toHaveTextContent("error");

    expect(screen.queryByTestId("cell-fabric-key-points")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("cell-fabric-detail-toggle"));

    const keyPoints = await screen.findByTestId("cell-fabric-key-points");
    expect(keyPoints).toHaveTextContent("orch-1");
    expect(keyPoints).toHaveTextContent("判断が必要な問題です");
    expect(keyPoints).toHaveTextContent("pass rate 75%");
    expect(keyPoints).toHaveTextContent("audits 8");
    expect(keyPoints).toHaveTextContent("受付停止");
    expect(keyPoints).toHaveTextContent("Feature:feature-a");
    expect(keyPoints).toHaveTextContent("Capability:capability-a");
    expect(keyPoints).toHaveTextContent("Entrypoint:POST:/jobs");
    expect(screen.getByLabelText("Feature:feature-a を開く"))
      .toHaveAttribute("href", "/feature-map?feature=feature-a");
    expect(screen.getByLabelText("Capability:capability-a を開く"))
      .toHaveAttribute("href", "/capability-map?capability=capability-a");
    expect(screen.getByLabelText("Entrypoint:POST:/jobs を開く"))
      .toHaveAttribute("href", "/flow-explorer");
    // sev3 never appears in the always-visible key points list itself.
    expect(keyPoints).not.toHaveTextContent("参考情報です");

    expect(screen.queryByTestId("cell-fabric-evidence")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("cell-fabric-evidence-toggle"));
    const evidence = await screen.findByTestId("cell-fabric-evidence");
    expect(evidence).toHaveTextContent("参考情報です");

    expect(screen.queryByTestId("cell-fabric-audit")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("cell-fabric-audit-toggle"));
    const audit = await screen.findByTestId("cell-fabric-audit");
    expect(audit).toHaveTextContent("observation");
    expect(audit).toHaveTextContent("warning");
  });

  test("承認/保留/却下ボタンが正しいペイロードでdecide APIを呼び、実行を意味しない旨が表示される", async () => {
    const digest = makeDigest();
    const ask = makeAsk({ id: 42 });
    getImpl = (path: string) => {
      if (path === "/cell-fabric/root-digest") return Promise.resolve(digest);
      if (path === "/cell-fabric/asks?status=open") return Promise.resolve({ system_id: 1, asks: [ask] });
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({ ...ask, status: "accepted", decision_method: "manual" });

    const { default: CellFabricPage } = await import("@/pages/cell-fabric");
    render(<CellFabricPage />, { wrapper: createWrapper() });

    const asksSection = await screen.findByTestId("cell-fabric-asks");
    expect(asksSection).toHaveTextContent("承認は提案の受け入れのみを意味し、実行には別途承認が必要です。");

    const askCard = await screen.findByTestId("cell-ask-42");
    fireEvent.click(within(askCard).getByText("承認"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/cell-fabric/asks/42/decide",
        { decision: "accepted", note: "" },
      );
    });
  });

  // B9 (UX 監査 2026-11-17)
  function emptyDigest(): CellRootDigestOut {
    const d = makeDigest();
    d.digest.conclusion.overall_severity = "ok";
    d.digest.conclusion.counts = {
      orchestrators: 0,
      tasks: {},
      escalations_open_by_severity: { sev1: 0, sev2: 0, sev3: 0 },
      asks_open: 0,
    };
    d.digest.conclusion.top_risks = [];
    return d;
  }

  test("Cell が 0 件なら「リスクはありません」と断定せず、Cell の作られ方を案内する", async () => {
    getImpl = (path: string) => {
      if (path === "/cell-fabric/root-digest") return Promise.resolve(emptyDigest());
      if (path === "/cell-fabric/cells") return Promise.resolve({ system_id: 1, cells: [] });
      if (path === "/cell-fabric/asks?status=open") return Promise.resolve({ system_id: 1, asks: [] });
      return Promise.resolve(undefined);
    };
    const { default: CellFabricPage } = await import("@/pages/cell-fabric");
    render(<CellFabricPage />, { wrapper: createWrapper() });

    const noCells = await screen.findByTestId("cell-fabric-no-cells");
    expect(noCells).toHaveTextContent("Probe Cell がまだありません");
    expect(noCells).toHaveTextContent("承認済みの Probe Point / Probe Pattern");
    expect(screen.queryByText(/重大なリスク\(sev1\)はありません/)).not.toBeInTheDocument();
  });

  test("Cell があり sev1 が無ければ、従来どおり「重大なリスクはありません」", async () => {
    getImpl = (path: string) => {
      if (path === "/cell-fabric/root-digest") return Promise.resolve(emptyDigest());
      if (path === "/cell-fabric/cells") return Promise.resolve({ system_id: 1, cells: [{ cell_id: "worker-a" }] });
      if (path === "/cell-fabric/asks?status=open") return Promise.resolve({ system_id: 1, asks: [] });
      return Promise.resolve(undefined);
    };
    const { default: CellFabricPage } = await import("@/pages/cell-fabric");
    render(<CellFabricPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("cell-fabric-no-sev1")).toHaveTextContent("重大なリスク(sev1)はありません");
    expect(screen.queryByTestId("cell-fabric-no-cells")).not.toBeInTheDocument();
  });

  test("Ask 一覧の取得失敗は再試行付きのエラーで、「未対応の Ask はありません」を出さない", async () => {
    getImpl = (path: string) => {
      if (path === "/cell-fabric/root-digest") return Promise.resolve(makeDigest());
      if (path === "/cell-fabric/cells") return Promise.resolve({ system_id: 1, cells: [{ cell_id: "worker-a" }] });
      if (path === "/cell-fabric/asks?status=open") return Promise.reject(new ApiError(500, "db down"));
      return Promise.resolve(undefined);
    };
    const { default: CellFabricPage } = await import("@/pages/cell-fabric");
    render(<CellFabricPage />, { wrapper: createWrapper() });

    const alert = await screen.findByTestId("cell-fabric-asks-error");
    expect(alert).toHaveTextContent("Ask 一覧を取得できませんでした");
    expect(within(alert).getByRole("button", { name: "再試行" })).toBeInTheDocument();
    expect(screen.queryByTestId("cell-fabric-asks-empty")).not.toBeInTheDocument();
    expect(screen.queryByText(/未対応のAskはありません/)).not.toBeInTheDocument();
  });

  test("Ask の判断に失敗した toast はサーバーの理由を出し、String(e) をそのまま出さない", async () => {
    const { toast } = await import("sonner");
    const ask = makeAsk({ id: 9 });
    getImpl = (path: string) => {
      if (path === "/cell-fabric/root-digest") return Promise.resolve(makeDigest());
      if (path === "/cell-fabric/cells") return Promise.resolve({ system_id: 1, cells: [{ cell_id: "worker-a" }] });
      if (path === "/cell-fabric/asks?status=open") return Promise.resolve({ system_id: 1, asks: [ask] });
      return Promise.resolve(undefined);
    };
    mockApi.post.mockRejectedValue(new ApiError(409, "Ask は既に判断済みです"));
    const { default: CellFabricPage } = await import("@/pages/cell-fabric");
    render(<CellFabricPage />, { wrapper: createWrapper() });

    fireEvent.click(within(await screen.findByTestId("cell-ask-9")).getByText("却下"));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Ask を更新できませんでした: Ask は既に判断済みです"));
    expect(toast.error).not.toHaveBeenCalledWith(expect.stringContaining("Error:"));
  });
});
