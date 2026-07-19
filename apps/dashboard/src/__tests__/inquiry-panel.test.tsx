/// <reference types="vitest/globals" />
// Issue #285: Inquiry lifecycle パネルのテスト。
//
// IntentBriefPanel 経由で InquiryPanel を統合テストする:
// 1. 「今回は保留する」を押すと元の項目に「保留中の疑問があります」マーカーが出る
//    (hold marker)。
// 2. 「疑問は解消した」を押すと元の項目に戻り、resolve レスポンスの held_draft が
//    入力欄に復元される (resolve returns to original with draft)。
// 3. resolve は元の項目の confirm/correct API を一切呼ばない — 自動では回答しない
//    (resolve does not auto-submit)。

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

function emptyGroups(): Record<string, InterviewIntentItemOut[]> {
  return {
    goal: [], pain: [], success_criteria: [], priority: [], constraints: [], non_goals: [],
  };
}

const CONFIRMED_ITEM: InterviewIntentItemOut = {
  id: 5, session_id: 1, system_id: 1, field: "goal", value_text: "現在の目標",
  status: "confirmed", origin: "user", source_statement: null,
  decision_method: "manual", intelligence_run_id: null, is_mock: false,
  superseded_by_id: null, created_at: 0, updated_at: 0,
};

// Mutable per-test override for GET /interview/sessions/1/inquiries — the
// refresh/resume rediscovery list. Defaults to empty (no active Inquiry).
let inquiryListItems: unknown[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  inquiryListItems = [];

  mockApi.get.mockImplementation((path: string) => {
    if (path === "/interview/sessions/1/intent") {
      const listing: InterviewIntentListOut = {
        session_id: 1, system_id: 1,
        items_by_field: { ...emptyGroups(), goal: [CONFIRMED_ITEM] },
      };
      return Promise.resolve(listing);
    }
    if (path === "/interview/sessions/1/inquiries") {
      return Promise.resolve({ session_id: 1, system_id: 1, items: inquiryListItems });
    }
    if (path === "/interview/inquiries/100") {
      return Promise.resolve({
        inquiry: {
          id: 100, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
          held_draft: "現在の目標", status: "open", status_reason: null,
          created_at: 0, updated_at: 0, closed_at: null,
        },
        messages: [
          { id: 1, inquiry_id: 100, system_id: 1, role: "user", content: "なぜこの目標なのですか?", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          { id: 2, inquiry_id: 100, system_id: 1, role: "assistant", content: "会話から抽出した目標です。", detail: { key_points: ["会話に根拠あり"], evidence: [], uncertainty: "" }, intelligence_run_id: 1, is_mock: false, created_at: 0 },
        ],
      });
    }
    if (path === "/interview/inquiries/200") {
      return Promise.resolve({
        inquiry: {
          id: 200, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
          held_draft: null, status: "open", status_reason: null,
          created_at: 0, updated_at: 0, closed_at: null,
        },
        messages: [
          { id: 1, inquiry_id: 200, system_id: 1, role: "user", content: "以前からの疑問です", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          { id: 2, inquiry_id: 200, system_id: 1, role: "assistant", content: "以前の回答です。", detail: null, intelligence_run_id: 1, is_mock: false, created_at: 0 },
        ],
      });
    }
    throw new Error(`Unexpected GET ${path}`);
  });
});

async function openIntentPanel() {
  const { IntentBriefPanel } = await import(
    "@/components/system-understanding/intent-brief-panel"
  );
  render(<IntentBriefPanel sessionId={1} />, { wrapper: createWrapper() });
  return screen.findByTestId("intent-item-5");
}

async function openInquiry() {
  const row = await openIntentPanel();
  fireEvent.click(within(row).getByTestId("intent-item-inquiry-open-5"));
  expect(within(row).getByTestId("inquiry-held-marker")).toHaveTextContent("保留中(疑問を解消してから回答)");

  mockApi.post.mockResolvedValueOnce({
    inquiry: {
      id: 100, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
      held_draft: "現在の目標", status: "open", status_reason: null,
      created_at: 0, updated_at: 0, closed_at: null,
    },
    messages: [
      { id: 1, inquiry_id: 100, system_id: 1, role: "user", content: "なぜこの目標なのですか?", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
      { id: 2, inquiry_id: 100, system_id: 1, role: "assistant", content: "会話から抽出した目標です。", detail: null, intelligence_run_id: 1, is_mock: false, created_at: 0 },
    ],
  });

  fireEvent.change(within(row).getByTestId("inquiry-question-input"), {
    target: { value: "なぜこの目標なのですか?" },
  });
  fireEvent.click(within(row).getByTestId("inquiry-question-submit"));

  await waitFor(() => {
    expect(mockApi.post).toHaveBeenCalledWith(
      "/interview/sessions/1/inquiries",
      expect.objectContaining({ origin_kind: "intent", origin_id: 5 }),
    );
  });
  await screen.findByTestId("inquiry-resolve");
  return row;
}

describe("InquiryPanel (Issue #285)", () => {
  test("holding an Inquiry marks the origin item as 保留中の疑問があります", async () => {
    const row = await openInquiry();

    mockApi.post.mockResolvedValueOnce({
      id: 100, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
      held_draft: "現在の目標", status: "held", status_reason: null,
      created_at: 0, updated_at: 0, closed_at: null,
    });
    fireEvent.click(within(row).getByTestId("inquiry-hold"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/inquiries/100/hold");
    });
    expect(await within(row).findByTestId("intent-item-held-inquiry-marker-5")).toHaveTextContent(
      "保留中の疑問があります",
    );
    // Inquiry conversation UI is gone; back to the normal item view.
    expect(within(row).queryByTestId("inquiry-panel")).not.toBeInTheDocument();
  });

  test("resolving an Inquiry returns to the original item with held_draft restored, without auto-submitting", async () => {
    const row = await openInquiry();

    mockApi.post.mockResolvedValueOnce({
      id: 100, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
      held_draft: "ユーザーが入力していた下書き", status: "resolved", status_reason: null,
      created_at: 0, updated_at: 0, closed_at: 123,
    });
    mockApi.post.mockClear();
    fireEvent.click(within(row).getByTestId("inquiry-resolve"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/inquiries/100/resolve");
    });

    // Back to the original item's own edit box, with the held draft restored.
    const restoredInput = await within(row).findByTestId("intent-item-correct-input-5");
    expect(restoredInput).toHaveValue("ユーザーが入力していた下書き");

    // resolve must NEVER itself confirm/correct/decline the origin item.
    expect(mockApi.post).not.toHaveBeenCalledWith(
      "/interview/intent/5/confirm", expect.anything(),
    );
    expect(mockApi.post).not.toHaveBeenCalledWith(
      "/interview/intent/5/correct", expect.anything(),
    );
    for (const call of mockApi.post.mock.calls) {
      expect(call[0]).not.toBe("/interview/intent/5/correct");
      expect(call[0]).not.toBe("/interview/intent/5/confirm");
      expect(call[0]).not.toBe("/interview/intent/5/decline");
    }
  });

  test("Inquiry conversation shows the assistant conclusion and expandable evidence", async () => {
    const row = await openInquiry();
    expect(within(row).getByText("会話から抽出した目標です。")).toBeInTheDocument();
  });

  // --- Refresh/resume rediscovery -------------------------------------------

  test("an existing open Inquiry is rediscovered on load and shows a reopen affordance instead of 疑問がある", async () => {
    inquiryListItems = [{
      id: 200, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
      held_draft: null, status: "open", status_reason: null,
      created_at: 0, updated_at: 0, closed_at: null,
    }];

    const row = await openIntentPanel();

    // The default "start a new Inquiry" button is gone; a reopen affordance
    // for the already-open Inquiry is shown instead.
    expect(within(row).queryByTestId("intent-item-inquiry-open-5")).not.toBeInTheDocument();
    const reopenButton = await within(row).findByTestId("intent-item-inquiry-reopen-5");

    fireEvent.click(reopenButton);

    // Reattaches directly to the rediscovered Inquiry (id 200) — no new
    // Inquiry is created, and the prior conversation is restored.
    expect(await within(row).findByText("以前の回答です。")).toBeInTheDocument();
    expect(mockApi.post).not.toHaveBeenCalledWith(
      "/interview/sessions/1/inquiries", expect.anything(),
    );
    expect(within(row).getByTestId("inquiry-resolve")).toBeInTheDocument();
  });

  test("an existing held Inquiry is rediscovered on load and shows the marker plus a resume affordance", async () => {
    inquiryListItems = [{
      id: 200, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
      held_draft: "保留時点の下書き", status: "held", status_reason: null,
      created_at: 0, updated_at: 0, closed_at: null,
    }];

    const row = await openIntentPanel();

    expect(within(row).queryByTestId("intent-item-inquiry-open-5")).not.toBeInTheDocument();
    const marker = await within(row).findByTestId("intent-item-held-inquiry-marker-5");
    expect(marker).toHaveTextContent("保留中の疑問があります");
    const resumeButton = within(row).getByTestId("intent-item-inquiry-resume-5");

    mockApi.post.mockResolvedValueOnce({
      id: 200, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
      held_draft: "保留時点の下書き", status: "open", status_reason: null,
      created_at: 0, updated_at: 0, closed_at: null,
    });
    fireEvent.click(resumeButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/inquiries/200/resume");
    });
    // Resuming reattaches to the same Inquiry and shows its conversation.
    expect(await within(row).findByText("以前の回答です。")).toBeInTheDocument();
  });
});
