/// <reference types="vitest/globals" />
// Issue #288: RefreshStatusChip のテスト。
//
// 1. 未取得/latest_job が無い場合は何も表示しない。
// 2. pending/updating/updated/failed/stale の各 status を日本語ラベルに
//    マップして表示する。
// 3. failed のときだけ「再試行」ボタンが表示され、押すと retry API を呼ぶ。

import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { RefreshJobStatus, RefreshStatusOut } from "@/api/types";

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

function makeStatus(status: RefreshJobStatus, overrides: Partial<RefreshStatusOut["latest_job"]> = {}): RefreshStatusOut {
  return {
    session_id: 1,
    system_id: 1,
    pending_count: status === "pending" ? 1 : 0,
    latest_job: {
      id: 9,
      session_id: 1,
      system_id: 1,
      trigger_kind: "qa_answer",
      status,
      error: status === "failed" ? "reasoning call failed" : null,
      intelligence_run_id: status === "updated" ? 42 : null,
      result_revision_id: status === "updated" ? 7 : null,
      created_at: 1,
      started_at: 1,
      finished_at: status === "pending" ? null : 2,
      ...overrides,
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("RefreshStatusChip", () => {
  test("renders nothing when there is no refresh job yet", async () => {
    mockApi.get.mockResolvedValue({ session_id: 1, system_id: 1, latest_job: null, pending_count: 0 });
    const { RefreshStatusChip } = await import("@/components/system-understanding/refresh-status-chip");
    render(<RefreshStatusChip sessionId={1} />, { wrapper: createWrapper() });

    await waitFor(() => expect(mockApi.get).toHaveBeenCalled());
    expect(screen.queryByTestId("refresh-status-chip")).not.toBeInTheDocument();
  });

  test.each([
    ["updating", "更新中"],
    ["updated", "更新済み"],
    ["failed", "更新に失敗しました"],
    ["stale", "古い結果を破棄しました"],
  ] as const)("maps status=%s to the Japanese label %s", async (status, label) => {
    mockApi.get.mockResolvedValue(makeStatus(status));
    const { RefreshStatusChip } = await import("@/components/system-understanding/refresh-status-chip");
    render(<RefreshStatusChip sessionId={1} />, { wrapper: createWrapper() });

    const chip = await screen.findByTestId("refresh-status-chip");
    expect(chip).toHaveTextContent(label);
  });

  test("shows a retry button only when failed, and it calls the retry endpoint", async () => {
    mockApi.get.mockResolvedValue(makeStatus("failed"));
    mockApi.post.mockResolvedValue({});
    const { RefreshStatusChip } = await import("@/components/system-understanding/refresh-status-chip");
    render(<RefreshStatusChip sessionId={1} />, { wrapper: createWrapper() });

    const retryButton = await screen.findByTestId("refresh-status-retry-button");
    fireEvent.click(retryButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/1/refresh-jobs/9/retry");
    });
  });

  test("does not show a retry button when updated", async () => {
    mockApi.get.mockResolvedValue(makeStatus("updated"));
    const { RefreshStatusChip } = await import("@/components/system-understanding/refresh-status-chip");
    render(<RefreshStatusChip sessionId={1} />, { wrapper: createWrapper() });

    await screen.findByTestId("refresh-status-chip");
    expect(screen.queryByTestId("refresh-status-retry-button")).not.toBeInTheDocument();
  });

  // status='updated' でも error が残っている場合(理解の再構築は成功、
  // Alignment 更新のみ失敗)は、成功表示にせず部分失敗として警告する。
  test("shows partial-failure label and retry when updated with a residual error", async () => {
    mockApi.get.mockResolvedValue(
      makeStatus("updated", { error: "Alignment refresh failed: reasoning call failed" }),
    );
    mockApi.post.mockResolvedValue({});
    const { RefreshStatusChip } = await import("@/components/system-understanding/refresh-status-chip");
    render(<RefreshStatusChip sessionId={1} />, { wrapper: createWrapper() });

    const chip = await screen.findByTestId("refresh-status-chip");
    expect(chip).toHaveTextContent("更新済み(一部失敗あり)");
    expect(chip).not.toHaveTextContent(/^更新済み$/);

    const retryButton = await screen.findByTestId("refresh-status-retry-button");
    fireEvent.click(retryButton);
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/1/refresh-jobs/9/retry");
    });
  });

  test("refreshes derived interview queries after the background job becomes terminal", async () => {
    mockApi.get
      .mockResolvedValueOnce(makeStatus("updating"))
      .mockResolvedValueOnce(makeStatus("updated"));
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
    });
    const dependentKeys = [
      ["interviewSession", 1, 1],
      ["interviewSessions", 1],
      ["interviewQa", 1, 1],
      ["understandingRevisions", 1, 1],
      ["understandingDiff", 1, 1],
      ["alignment", 1, 1],
      ["reviewQueue", 1, 1],
    ];
    for (const key of dependentKeys) qc.setQueryData(key, { old: true });

    const { RefreshStatusChip } = await import("@/components/system-understanding/refresh-status-chip");
    render(
      <QueryClientProvider client={qc}>
        <RefreshStatusChip sessionId={1} />
      </QueryClientProvider>,
    );

    expect(await screen.findByTestId("refresh-status-chip")).toHaveTextContent("更新中");
    expect(
      qc.getQueryState(["alignment", 1, 1])?.isInvalidated,
    ).toBe(false);

    await act(async () => {
      await qc.invalidateQueries({ queryKey: ["refreshStatus", 1, 1] });
    });
    await waitFor(() => {
      expect(screen.getByTestId("refresh-status-chip")).toHaveTextContent("更新済み");
    });
    await waitFor(() => {
      for (const key of dependentKeys) {
        expect(qc.getQueryState(key)?.isInvalidated).toBe(true);
      }
    });
  });
});
