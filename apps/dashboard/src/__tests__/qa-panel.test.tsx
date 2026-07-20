/// <reference types="vitest/globals" />
// Issue #291: QaPanel の対象外(担当外)グルーピングのテスト。
//
// 1. answerableAreas が空のときはフィルタなし(全件が通常グループ)。
// 2. knowledge_area が answerableAreas に含まれない質問だけが
//    「担当外の質問」グループへ分離され、非表示にはならないこと。
// 3. knowledge_area が null(未分類)の質問は常に通常グループに残ること。
// 4. 担当外グループの質問には「担当者へ引き継ぐ」ボタンが出ること。

import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { InterviewQaListOut, InterviewQaOut } from "@/api/types";

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

function makeQa(overrides: Partial<InterviewQaOut> & { id: number }): InterviewQaOut {
  return {
    session_id: 1,
    system_id: 1,
    question_text: `質問${overrides.id}`,
    question_category: "general",
    question_source: "dialogue",
    hypothesis: null,
    evidence_refs: [],
    runtime_evidence: null,
    answer_text: null,
    status: "open",
    answered_by: null,
    superseded_by_id: null,
    created_at: 0,
    answered_at: null,
    route_category: null,
    route_run_id: null,
    knowledge_area: null,
    handoff_id: null,
    ...overrides,
  } as InterviewQaOut;
}

let getImpl: (path: string) => unknown;

beforeEach(() => {
  vi.clearAllMocks();
  getImpl = () => Promise.resolve(undefined);
  mockApi.get.mockImplementation((path: string) => getImpl(path));
});

function mockQaList(items: InterviewQaOut[]) {
  const list: InterviewQaListOut = {
    session_id: 1, system_id: 1, items,
    open_count: items.filter(i => i.status === "open").length,
    high_priority_open_count: 0,
    answers_revised_at: null,
  };
  getImpl = (path: string) => {
    if (path === "/interview/sessions/1/qa") return Promise.resolve(list);
    if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
    if (path === "/interview/sessions/1/handoffs") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
    return Promise.resolve(undefined);
  };
}

describe("QaPanel out-of-area grouping (Issue #291)", () => {
  test("empty answerableAreas: no out-of-area group, all items shown normally", async () => {
    mockQaList([
      makeQa({ id: 1, knowledge_area: "security" }),
      makeQa({ id: 2, knowledge_area: "implementation" }),
    ]);

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    await screen.findByTestId("qa-item-1");
    expect(screen.getByTestId("qa-item-2")).toBeInTheDocument();
    expect(screen.queryByTestId("qa-out-of-area-group")).not.toBeInTheDocument();
  });

  test("out-of-area questions are grouped separately, never hidden", async () => {
    mockQaList([
      makeQa({ id: 1, knowledge_area: "security" }),
      makeQa({ id: 2, knowledge_area: "implementation" }),
      makeQa({ id: 3, knowledge_area: null }), // unrouted -- always in the normal group
    ]);

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={["implementation"]} />,
      { wrapper: createWrapper() },
    );

    await screen.findByTestId("qa-panel");
    const outOfAreaGroup = await screen.findByTestId("qa-out-of-area-group");
    expect(outOfAreaGroup).toHaveTextContent("担当外の質問(1 件)");
    // The out-of-area question (security, not in ["implementation"]) is
    // inside the group, not hidden from the page entirely.
    expect(within(outOfAreaGroup).getByTestId("qa-item-1")).toBeInTheDocument();
    expect(within(outOfAreaGroup).getByTestId("qa-out-of-area-1")).toHaveTextContent("担当外");

    // In-area and unrouted questions stay in the normal (non-grouped) list.
    expect(screen.getByTestId("qa-item-2")).toBeInTheDocument();
    expect(screen.getByTestId("qa-item-3")).toBeInTheDocument();
    expect(within(outOfAreaGroup).queryByTestId("qa-item-2")).not.toBeInTheDocument();
    expect(within(outOfAreaGroup).queryByTestId("qa-item-3")).not.toBeInTheDocument();
  });

  test("out-of-area items offer a 担当者へ引き継ぐ action", async () => {
    mockQaList([makeQa({ id: 4, knowledge_area: "security" })]);

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={["implementation"]} />,
      { wrapper: createWrapper() },
    );

    const outOfAreaGroup = await screen.findByTestId("qa-out-of-area-group");
    expect(within(outOfAreaGroup).getByTestId("qa-handoff-open-4")).toBeInTheDocument();
  });
});
