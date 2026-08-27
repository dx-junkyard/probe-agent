/// <reference types="vitest/globals" />
// Issue #284: Intent Brief パネルのテスト。
// proposed vs confirmed の表示区別、確認する操作が API を呼ぶこと、
// undecided/not_applicable のクイック選択肢が表示されること、生の enum
// 文字列が画面に出ないことを検証する。api/client.ts をモックして
// react-query 経由のフックを駆動する (phase0-empty-states.test.tsx と同じ
// パターン)。

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { InterviewIntentItemOut, InterviewIntentListOut } from "@/api/types";

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
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

function makeItem(overrides: Partial<InterviewIntentItemOut> & { id: number; field: string }): InterviewIntentItemOut {
  return {
    session_id: 1,
    system_id: 1,
    value_text: "テスト値",
    status: "proposed",
    origin: "ai_proposed",
    source_statement: null,
    decision_method: "reasoning_llm",
    intelligence_run_id: 1,
    is_mock: false,
    superseded_by_id: null,
    created_at: 0,
    updated_at: 0,
    ...overrides,
  } as InterviewIntentItemOut;
}

function emptyGroups(): Record<string, InterviewIntentItemOut[]> {
  return {
    goal: [], pain: [], success_criteria: [], priority: [], constraints: [], non_goals: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IntentBriefPanel", () => {
  test("renders proposed items with the AI-proposal badge and action buttons", async () => {
    const proposed = makeItem({
      id: 1, field: "goal", value_text: "トレース収集を効率化したい",
      status: "proposed", origin: "ai_proposed",
      source_statement: "トレースを楽に集めたいんです",
    });
    const listing: InterviewIntentListOut = {
      session_id: 1, system_id: 1,
      items_by_field: { ...emptyGroups(), goal: [proposed] },
    };
    mockApi.get.mockResolvedValue(listing);

    const { IntentBriefPanel } = await import(
      "@/components/system-understanding/intent-brief-panel"
    );
    render(<IntentBriefPanel sessionId={1} />, { wrapper: createWrapper() });

    await screen.findByTestId("intent-brief-panel");
    const row = await screen.findByTestId("intent-item-1");
    expect(within(row).getByText("AI 提案(未確認)")).toBeInTheDocument();
    expect(within(row).getByText("トレース収集を効率化したい")).toBeInTheDocument();
    expect(within(row).getByText(/トレースを楽に集めたいんです/)).toBeInTheDocument();
    expect(within(row).getByTestId("intent-item-confirm-1")).toBeInTheDocument();
    expect(within(row).getByTestId("intent-item-edit-1")).toBeInTheDocument();
    expect(within(row).getByTestId("intent-item-decline-1")).toBeInTheDocument();

    // Raw enum values never render.
    expect(screen.queryByText(/^proposed$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^ai_proposed$/)).not.toBeInTheDocument();
  });

  test("renders confirmed items with the confirmed badge and no action buttons", async () => {
    const confirmed = makeItem({
      id: 2, field: "pain", value_text: "手動確認が多い",
      status: "confirmed", origin: "user", decision_method: "manual",
      intelligence_run_id: null, source_statement: null,
    });
    const listing: InterviewIntentListOut = {
      session_id: 1, system_id: 1,
      items_by_field: { ...emptyGroups(), pain: [confirmed] },
    };
    mockApi.get.mockResolvedValue(listing);

    const { IntentBriefPanel } = await import(
      "@/components/system-understanding/intent-brief-panel"
    );
    render(<IntentBriefPanel sessionId={1} />, { wrapper: createWrapper() });

    const row = await screen.findByTestId("intent-item-2");
    expect(within(row).getByText("確認済み")).toBeInTheDocument();
    expect(within(row).queryByTestId("intent-item-confirm-2")).not.toBeInTheDocument();
    expect(within(row).queryByTestId("intent-item-edit-2")).not.toBeInTheDocument();
    expect(within(row).queryByTestId("intent-item-decline-2")).not.toBeInTheDocument();

    expect(screen.queryByText(/^confirmed$/)).not.toBeInTheDocument();
  });

  test("confirm button calls the confirm API and refetches the list", async () => {
    const proposed = makeItem({ id: 3, field: "goal", value_text: "候補の目標" });
    const listing: InterviewIntentListOut = {
      session_id: 1, system_id: 1,
      items_by_field: { ...emptyGroups(), goal: [proposed] },
    };
    mockApi.get.mockResolvedValue(listing);
    mockApi.post.mockResolvedValue({ ...proposed, status: "confirmed", decision_method: "manual" });

    const { IntentBriefPanel } = await import(
      "@/components/system-understanding/intent-brief-panel"
    );
    render(<IntentBriefPanel sessionId={1} />, { wrapper: createWrapper() });

    const confirmButton = await screen.findByTestId("intent-item-confirm-3");
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/intent/3/confirm");
    });
  });

  test("the next-missing-field prompt offers free text plus 未定/対象外 quick choices", async () => {
    const listing: InterviewIntentListOut = { session_id: 1, system_id: 1, items_by_field: emptyGroups() };
    mockApi.get.mockResolvedValue(listing);
    mockApi.post.mockResolvedValue(makeItem({ id: 10, field: "goal", status: "undecided", origin: "user" }));

    const { IntentBriefPanel } = await import(
      "@/components/system-understanding/intent-brief-panel"
    );
    render(<IntentBriefPanel sessionId={1} />, { wrapper: createWrapper() });

    const prompt = await screen.findByTestId("intent-next-missing-prompt");
    // goal is first in the fixed order, so it's the next missing field.
    expect(within(prompt).getByText("目標")).toBeInTheDocument();
    expect(within(prompt).getByTestId("intent-next-missing-undecided")).toHaveTextContent("未定");
    expect(within(prompt).getByTestId("intent-next-missing-not-applicable")).toHaveTextContent("対象外");

    fireEvent.click(within(prompt).getByTestId("intent-next-missing-undecided"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/intent",
        { field: "goal", value_text: "未定", status: "undecided" },
      );
    });
  });

  // Issue #349 (#52 / `OP-S4`): Intent 候補の生成は自動処理なので、通常
  // フローの操作としては置かない。確定・訂正は人間のまま (#284)。
  test("AI提案の生成ボタンは通常フローに常設しない", async () => {
    const listing: InterviewIntentListOut = {
      session_id: 1, system_id: 1, items_by_field: { ...emptyGroups() },
    };
    mockApi.get.mockResolvedValue(listing);

    const { IntentBriefPanel } = await import(
      "@/components/system-understanding/intent-brief-panel"
    );
    render(<IntentBriefPanel sessionId={1} />, { wrapper: createWrapper() });

    await screen.findByTestId("intent-brief-panel");
    expect(screen.queryByTestId("intent-propose-button")).toBeNull();
  });
});
