/// <reference types="vitest/globals" />
// Issue #289: まとめて修正パネルのテスト。
//
// 1. 自由文を送信すると変更案の作成 API を呼び、変更前/変更後の diff と
//    理由がプレビュー表示されること。
// 2. resolved 以外(あいまい/競合/古い前提/変更不可)の項目のチェックボック
//    スは既定で外れておりかつ無効化されていること -- 誤って選択できない。
// 3. 「選択した変更を適用」は選択済み(resolved のみ)の item_id だけを
//    apply API へ送ること。
// 4. 「破棄」は discard API を呼ぶこと。

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { ChangeSetDetailOut, ChangeSetItemOut } from "@/api/types";

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

function makeItem(overrides: Partial<ChangeSetItemOut> & { id: number }): ChangeSetItemOut {
  return {
    change_set_id: 1,
    system_id: 1,
    target_kind: "intent_item",
    target_ref: { intent_item_id: 1 },
    field: "value_text",
    before_value: "旧ゴール",
    after_value: "新ゴール",
    reason: "発言に基づき修正",
    resolution_state: "resolved",
    applied: false,
    applied_at: null,
    created_at: 0,
    affected_items: [],
    ...overrides,
  } as ChangeSetItemOut;
}

function makeDetail(items: ChangeSetItemOut[], overrides?: Partial<ChangeSetDetailOut["change_set"]>): ChangeSetDetailOut {
  return {
    change_set: {
      id: 1, session_id: 1, system_id: 1, base_revision_id: 1,
      source_text: "ゴールを直して", status: "proposed",
      intelligence_run_id: 1, is_mock: false, created_at: 0, updated_at: 0,
      ...overrides,
    },
    items,
    rebuild_note: "適用すると、レビューキューと現在の理解が自動的に更新されます。",
  };
}

let getImpl: (path: string) => unknown;

beforeEach(() => {
  vi.clearAllMocks();
  getImpl = () => Promise.resolve(undefined);
  mockApi.get.mockImplementation((path: string) => getImpl(path));
});

describe("ChangeSetPanel", () => {
  test("submitting text creates a change set and previews field-level diffs", async () => {
    const item = makeItem({ id: 1 });
    const detail = makeDetail([item]);

    mockApi.post.mockResolvedValue(detail);
    getImpl = (path: string) => {
      if (path === "/interview/change-sets/1") return Promise.resolve(detail);
      return Promise.resolve(undefined);
    };

    const { ChangeSetPanel } = await import("@/components/system-understanding/change-set-panel");
    render(<ChangeSetPanel sessionId={1} />, { wrapper: createWrapper() });

    const textarea = screen.getByTestId("change-set-textarea");
    fireEvent.change(textarea, { target: { value: "実はゴールはAではなくBです" } });
    fireEvent.click(screen.getByTestId("change-set-submit"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/change-sets",
        { text: "実はゴールはAではなくBです" },
      );
    });

    const row = await screen.findByTestId("change-set-item-1");
    expect(within(row).getByText(/旧ゴール/)).toBeInTheDocument();
    expect(within(row).getByText(/新ゴール/)).toBeInTheDocument();
    expect(within(row).getByText(/発言に基づき修正/)).toBeInTheDocument();
    expect(await screen.findByTestId("change-set-rebuild-note")).toBeInTheDocument();

    // Raw enum strings never render.
    expect(screen.queryByText(/^resolved$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^intent_item$/)).not.toBeInTheDocument();
  });

  test("non-resolved items default unchecked and disabled; resolved defaults checked", async () => {
    const resolvedItem = makeItem({ id: 1, resolution_state: "resolved" });
    const ambiguousItem = makeItem({ id: 2, resolution_state: "ambiguous", before_value: null });
    const conflictItem = makeItem({ id: 3, resolution_state: "conflict" });
    const staleItem = makeItem({ id: 4, resolution_state: "stale" });
    const forbiddenItem = makeItem({ id: 5, resolution_state: "forbidden", before_value: null });
    const detail = makeDetail([resolvedItem, ambiguousItem, conflictItem, staleItem, forbiddenItem]);

    mockApi.post.mockResolvedValue(detail);
    getImpl = (path: string) => {
      if (path === "/interview/change-sets/1") return Promise.resolve(detail);
      return Promise.resolve(undefined);
    };

    const { ChangeSetPanel } = await import("@/components/system-understanding/change-set-panel");
    render(<ChangeSetPanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByTestId("change-set-textarea"), { target: { value: "まとめて修正" } });
    fireEvent.click(screen.getByTestId("change-set-submit"));

    const resolvedCheckbox = await screen.findByTestId("change-set-item-checkbox-1") as HTMLInputElement;
    await waitFor(() => expect(resolvedCheckbox.checked).toBe(true));
    expect(resolvedCheckbox.disabled).toBe(false);

    for (const id of [2, 3, 4, 5]) {
      const checkbox = await screen.findByTestId(`change-set-item-checkbox-${id}`) as HTMLInputElement;
      expect(checkbox.checked).toBe(false);
      expect(checkbox.disabled).toBe(true);
    }

    // Japanese labels for each non-resolved state, never the raw enum string.
    expect(within(await screen.findByTestId("change-set-item-2")).getByText("あいまい")).toBeInTheDocument();
    expect(within(await screen.findByTestId("change-set-item-3")).getByText("競合")).toBeInTheDocument();
    expect(within(await screen.findByTestId("change-set-item-4")).getByText("古い前提")).toBeInTheDocument();
    expect(within(await screen.findByTestId("change-set-item-5")).getByText("変更不可")).toBeInTheDocument();
  });

  test("選択した変更を適用 sends only the selected resolved item ids", async () => {
    const resolvedA = makeItem({ id: 1, resolution_state: "resolved" });
    const resolvedB = makeItem({ id: 2, resolution_state: "resolved", after_value: "新説明" });
    const ambiguous = makeItem({ id: 3, resolution_state: "ambiguous", before_value: null });
    const detail = makeDetail([resolvedA, resolvedB, ambiguous]);

    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/change-sets") return Promise.resolve(detail);
      if (path === "/interview/change-sets/1/apply") {
        return Promise.resolve({
          change_set: { ...detail.change_set, status: "partially_applied" },
          applied_item_ids: [1],
          skipped: [],
          result_revision_id: null,
        });
      }
      return Promise.resolve(undefined);
    });
    getImpl = (path: string) => {
      if (path === "/interview/change-sets/1") return Promise.resolve(detail);
      return Promise.resolve(undefined);
    };

    const { ChangeSetPanel } = await import("@/components/system-understanding/change-set-panel");
    render(<ChangeSetPanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByTestId("change-set-textarea"), { target: { value: "まとめて修正" } });
    fireEvent.click(screen.getByTestId("change-set-submit"));

    await screen.findByTestId("change-set-item-checkbox-1");
    // Uncheck item 2 so only item 1 (plus the never-checkable ambiguous
    // item 3) remains selected.
    fireEvent.click(screen.getByTestId("change-set-item-checkbox-2"));

    fireEvent.click(await screen.findByTestId("change-set-apply-button"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/change-sets/1/apply",
        { item_ids: [1] },
      );
    });
  });

  test("破棄 calls the discard API", async () => {
    const item = makeItem({ id: 1 });
    const detail = makeDetail([item]);

    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/change-sets") return Promise.resolve(detail);
      if (path === "/interview/change-sets/1/discard") {
        return Promise.resolve({ ...detail.change_set, status: "discarded" });
      }
      return Promise.resolve(undefined);
    });
    getImpl = (path: string) => {
      if (path === "/interview/change-sets/1") return Promise.resolve(detail);
      return Promise.resolve(undefined);
    };

    const { ChangeSetPanel } = await import("@/components/system-understanding/change-set-panel");
    render(<ChangeSetPanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByTestId("change-set-textarea"), { target: { value: "まとめて修正" } });
    fireEvent.click(screen.getByTestId("change-set-submit"));

    fireEvent.click(await screen.findByTestId("change-set-discard-button"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/change-sets/1/discard");
    });
  });
});
