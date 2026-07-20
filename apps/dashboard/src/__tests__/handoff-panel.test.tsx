/// <reference types="vitest/globals" />
// Issue #291: 担当者引き継ぎパネルのテスト。
//
// 1. HandoffListPanel: pending/answered/returned をグループ表示すること。
// 2. returned の項目に「引き継ぎ先の回答(未確定)」バッジが出て、
//    「この内容で回答を確定する」が既存の回答エンドポイント経由で
//    handoff_id を渡すこと(qa origin)。
// 3. review_item origin の returned は既存の /correct 経由で確定すること。
// 4. pending の担当者回答フォームが /answer を呼ぶこと。
// 5. answered の「元の項目へ戻す」が /return を呼ぶこと。

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { QuestionHandoffOut } from "@/api/types";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
  ApiError: class ApiError extends Error {},
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

function makeHandoff(overrides: Partial<QuestionHandoffOut> & { id: number }): QuestionHandoffOut {
  return {
    session_id: 1,
    system_id: 1,
    origin_kind: "qa",
    origin_id: 5,
    assignee: "山田太郎",
    background: "この関数の目的は?",
    needed_decision: "実装方針を決めてほしい",
    evidence: null,
    due_note: null,
    priority: "normal",
    status: "pending",
    answer_text: null,
    answered_by: null,
    answered_at: null,
    created_by: null,
    created_at: 0,
    updated_at: 0,
    ...overrides,
  } as QuestionHandoffOut;
}

let getImpl: (path: string) => unknown;

beforeEach(() => {
  vi.clearAllMocks();
  getImpl = () => Promise.resolve(undefined);
  mockApi.get.mockImplementation((path: string) => getImpl(path));
});

describe("HandoffListPanel", () => {
  test("renders nothing when there are no handoffs", async () => {
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/handoffs") {
        return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      }
      return Promise.resolve(undefined);
    };
    const { HandoffListPanel } = await import("@/components/system-understanding/handoff-panel");
    const { container } = render(<HandoffListPanel sessionId={1} actor="dev" />, { wrapper: createWrapper() });
    await waitFor(() => expect(mockApi.get).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  test("groups pending/answered/returned and shows Japanese status labels", async () => {
    const pending = makeHandoff({ id: 1, status: "pending" });
    const answered = makeHandoff({ id: 2, status: "answered", answer_text: "担当者の回答" });
    const returned = makeHandoff({ id: 3, status: "returned", answer_text: "確定前の回答" });
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/handoffs") {
        return Promise.resolve({ session_id: 1, system_id: 1, items: [pending, answered, returned] });
      }
      return Promise.resolve(undefined);
    };

    const { HandoffListPanel } = await import("@/components/system-understanding/handoff-panel");
    render(<HandoffListPanel sessionId={1} actor="dev" />, { wrapper: createWrapper() });

    await screen.findByTestId("handoff-list-panel");
    expect(screen.getByTestId("handoff-status-1")).toHaveTextContent("未対応");
    expect(screen.getByTestId("handoff-status-2")).toHaveTextContent("担当者回答済み");
    expect(screen.getByTestId("handoff-status-3")).toHaveTextContent("確認待ち");
    expect(screen.getByTestId("handoff-returned-marker-3")).toHaveTextContent("引き継ぎ先の回答(未確定)");
  });

  test("qa origin: この内容で回答を確定する submits via the qa answer endpoint with handoff_id", async () => {
    const returned = makeHandoff({
      id: 7, status: "returned", origin_kind: "qa", origin_id: 42, answer_text: "担当者の回答内容",
    });
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/handoffs") {
        return Promise.resolve({ session_id: 1, system_id: 1, items: [returned] });
      }
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({
      qa: { id: 42, status: "answered" }, previous: null, regeneration_recommended: false,
    });

    const { HandoffListPanel } = await import("@/components/system-understanding/handoff-panel");
    render(<HandoffListPanel sessionId={1} actor="original_dev" />, { wrapper: createWrapper() });

    const confirmButton = await screen.findByTestId("handoff-confirm-7");
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/qa/42/answer",
        expect.objectContaining({
          answer_text: "担当者の回答内容",
          actor: "original_dev",
          handoff_id: 7,
        }),
      );
    });
  });

  test("review_item origin: この内容で回答を確定する submits via the alignment /correct endpoint", async () => {
    const returned = makeHandoff({
      id: 8, status: "returned", origin_kind: "review_item", origin_id: 55, answer_text: "修正案です",
    });
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/handoffs") {
        return Promise.resolve({ session_id: 1, system_id: 1, items: [returned] });
      }
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({ id: 55, status: "corrected" });

    const { HandoffListPanel } = await import("@/components/system-understanding/handoff-panel");
    render(<HandoffListPanel sessionId={1} actor="original_dev" />, { wrapper: createWrapper() });

    const confirmButton = await screen.findByTestId("handoff-confirm-8");
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/alignment/55/correct",
        { corrected_interpretation: "修正案です" },
      );
    });
  });

  test("pending: 担当者として回答する submits via the handoff /answer endpoint", async () => {
    const pending = makeHandoff({ id: 9, status: "pending" });
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/handoffs") {
        return Promise.resolve({ session_id: 1, system_id: 1, items: [pending] });
      }
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({ ...pending, status: "answered", answer_text: "回答内容" });

    const { HandoffListPanel } = await import("@/components/system-understanding/handoff-panel");
    render(<HandoffListPanel sessionId={1} actor="dev" />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("handoff-answer-open-9"));
    fireEvent.change(screen.getByTestId("handoff-answered-by-9"), { target: { value: "担当者A" } });
    fireEvent.change(screen.getByTestId("handoff-answer-text-9"), { target: { value: "回答内容" } });
    fireEvent.click(screen.getByTestId("handoff-answer-submit-9"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/handoffs/9/answer",
        { answer_text: "回答内容", answered_by: "担当者A" },
      );
    });
  });

  test("answered: 元の項目へ戻す calls /return", async () => {
    const answered = makeHandoff({ id: 10, status: "answered", answer_text: "回答済み" });
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/handoffs") {
        return Promise.resolve({ session_id: 1, system_id: 1, items: [answered] });
      }
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({ ...answered, status: "returned" });

    const { HandoffListPanel } = await import("@/components/system-understanding/handoff-panel");
    render(<HandoffListPanel sessionId={1} actor="dev" />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("handoff-return-10"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/handoffs/10/return");
    });
  });

  test("pending: キャンセル calls /cancel", async () => {
    const pending = makeHandoff({ id: 11, status: "pending" });
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/handoffs") {
        return Promise.resolve({ session_id: 1, system_id: 1, items: [pending] });
      }
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({ ...pending, status: "cancelled" });

    const { HandoffListPanel } = await import("@/components/system-understanding/handoff-panel");
    render(<HandoffListPanel sessionId={1} actor="dev" />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("handoff-cancel-11"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/handoffs/11/cancel");
    });
  });
});

describe("HandoffModal", () => {
  test("submits a new handoff with the entered fields plus prefilled background/evidence", async () => {
    mockApi.post.mockResolvedValue(makeHandoff({ id: 20 }));

    const { HandoffModal } = await import("@/components/system-understanding/handoff-panel");
    const onOpenChange = vi.fn();
    render(
      <HandoffModal
        sessionId={1}
        originKind="qa"
        originId={5}
        defaultBackground="この関数の目的は?"
        defaultEvidence={[{ path: "src/a.py", start_line: 1, end_line: 2, summary: "" }]}
        open
        onOpenChange={onOpenChange}
      />,
      { wrapper: createWrapper() },
    );

    const form = screen.getByTestId("handoff-modal-form");
    expect(within(form).getByTestId("handoff-background-input")).toHaveValue("この関数の目的は?");

    fireEvent.change(within(form).getByTestId("handoff-assignee-input"), { target: { value: "鈴木" } });
    fireEvent.change(within(form).getByTestId("handoff-needed-decision-input"), { target: { value: "判断してほしい" } });
    fireEvent.click(within(form).getByTestId("handoff-submit-button"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/handoffs",
        {
          origin_kind: "qa",
          origin_id: 5,
          assignee: "鈴木",
          background: "この関数の目的は?",
          needed_decision: "判断してほしい",
          evidence: [{ path: "src/a.py", start_line: 1, end_line: 2, summary: "" }],
          due_note: undefined,
          priority: "normal",
        },
      );
    });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  test("rejects submission when required fields are blank", async () => {
    const { HandoffModal } = await import("@/components/system-understanding/handoff-panel");
    render(
      <HandoffModal
        sessionId={1}
        originKind="qa"
        originId={5}
        open
        onOpenChange={vi.fn()}
      />,
      { wrapper: createWrapper() },
    );

    fireEvent.click(screen.getByTestId("handoff-submit-button"));
    expect(mockApi.post).not.toHaveBeenCalled();
  });
});
