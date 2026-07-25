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
});
