/// <reference types="vitest/globals" />
// Issue #291: QaPanel の対象外(担当外)グルーピングのテスト。
//
// 1. answerableAreas が空のときはフィルタなし(全件が通常グループ)。
// 2. knowledge_area が answerableAreas に含まれない質問だけが
//    「担当外の質問」グループへ分離され、非表示にはならないこと。
// 3. knowledge_area が null(未分類)の質問は常に通常グループに残ること。
// 4. 担当外グループの質問には「担当者へ引き継ぐ」ボタンが出ること。
//
// Issue #286 review fix (Finding 1) / Finding 6:
// 5. 「AIに先に調査させる」ボタンがバッチ API を呼び、結果をトーストで表示する。
// 6. route_category バッジは日本語ラベルで表示され、raw な enum 文字列は出ない。
// 7. investigation の結論表示と「調査結果を回答欄に転記」ボタンが送信せず
//    テキストエリアだけを埋めること。
// 8. investigation.status === "unresolved" のときは注記だけ表示すること。
// 9. #288 の自動更新と整合するバナー文言であること。

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { InterviewQaListOut, InterviewQaOut, InterviewQaRouteInvestigateBatchOut } from "@/api/types";

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
    investigation: null,
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

describe("QaPanel route-and-investigate wiring (Issue #286 review fix, Finding 1/6)", () => {
  test("AIに先に調査させる button calls the batch endpoint and toasts a Japanese summary", async () => {
    mockQaList([makeQa({ id: 1 })]);
    const batchResult: InterviewQaRouteInvestigateBatchOut = {
      session_id: 1,
      system_id: 1,
      results: [
        { qa_id: 1, route_category: "human_only", knowledge_area: null, investigation_status: null, error: null },
      ],
      counts: { routed: 1, investigated: 0, failed: 0, skipped_cap: 0 },
    };
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/qa/route-and-investigate") return Promise.resolve(batchResult);
      return Promise.resolve(undefined);
    });

    const { QaPanel } = await import("@/pages/interview");
    const { toast } = await import("sonner");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    const button = await screen.findByTestId("route-and-investigate-qa");
    fireEvent.click(button);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/1/qa/route-and-investigate");
    });
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("分類 1 件・調査 0 件が完了しました");
    });
  });

  // --- Issue #295 §4.8: 「わからない」→ 自動調査接続 --------------------

  test("「わからない」triggers the batch route-and-investigate endpoint for this question, showing a short status only", async () => {
    mockQaList([makeQa({ id: 1 })]);
    let resolveInvestigate: (v: InterviewQaRouteInvestigateBatchOut) => void;
    const investigatePromise = new Promise<InterviewQaRouteInvestigateBatchOut>(resolve => {
      resolveInvestigate = resolve;
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/qa/route-and-investigate") return investigatePromise;
      return Promise.resolve(undefined);
    });

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    const item = await screen.findByTestId("qa-item-1");
    fireEvent.click(within(item).getByText("回答する"));
    fireEvent.click(within(item).getByTestId("qa-answer-unknown-1"));

    // Only a short status line while investigating -- no answer call yet,
    // and the わからない button itself is replaced (no duplicate firing).
    expect(within(item).getByTestId("qa-answer-unknown-investigating-1")).toHaveTextContent(
      "関連コードとテストを確認しています",
    );
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/1/qa/route-and-investigate");
    });
    expect(within(item).queryByTestId("qa-answer-unknown-1")).not.toBeInTheDocument();
    expect(mockApi.post).not.toHaveBeenCalledWith(
      "/interview/sessions/1/qa/1/answer", expect.anything(),
    );

    resolveInvestigate!({
      session_id: 1, system_id: 1,
      results: [{ qa_id: 1, route_category: "system_researchable", knowledge_area: null, investigation_status: "completed", error: null }],
      counts: { routed: 1, investigated: 1, failed: 0, skipped_cap: 0 },
    });

    // Investigation succeeded for this question: never falls back to
    // recording an unknown answer, and returns to the normal confirm view.
    await waitFor(() => {
      expect(within(item).queryByTestId("qa-answer-unknown-investigating-1")).not.toBeInTheDocument();
    });
    expect(mockApi.post).not.toHaveBeenCalledWith(
      "/interview/sessions/1/qa/1/answer", expect.anything(),
    );
    expect(within(item).getByText("回答する")).toBeInTheDocument();
  });

  test("investigation API failure falls back to the original #142 わからない flow (never loses the answer opportunity)", async () => {
    mockQaList([makeQa({ id: 1 })]);
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/qa/route-and-investigate") {
        return Promise.reject(new Error("boom"));
      }
      if (path === "/interview/sessions/1/qa/1/answer") {
        return Promise.resolve({ qa: makeQa({ id: 1, status: "unconfirmed" }), previous: null, regeneration_recommended: false });
      }
      return Promise.resolve(undefined);
    });

    const { QaPanel } = await import("@/pages/interview");
    const { toast } = await import("sonner");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    const item = await screen.findByTestId("qa-item-1");
    fireEvent.click(within(item).getByText("回答する"));
    fireEvent.click(within(item).getByTestId("qa-answer-unknown-1"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/qa/1/answer",
        expect.objectContaining({ answer_unknown: true }),
      );
    });
    await waitFor(() => {
      expect(toast.info).toHaveBeenCalledWith(
        "「わからない」として記録しました。仮説を立てて確認質問を続けます。",
      );
    });
  });

  test("a question not covered by the batch result (e.g. cap/skip) also falls back to the #142 flow", async () => {
    mockQaList([makeQa({ id: 1 })]);
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/qa/route-and-investigate") {
        const batch: InterviewQaRouteInvestigateBatchOut = {
          session_id: 1, system_id: 1, results: [],
          counts: { routed: 0, investigated: 0, failed: 0, skipped_cap: 1 },
        };
        return Promise.resolve(batch);
      }
      if (path === "/interview/sessions/1/qa/1/answer") {
        return Promise.resolve({ qa: makeQa({ id: 1, status: "unconfirmed" }), previous: null, regeneration_recommended: false });
      }
      return Promise.resolve(undefined);
    });

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    const item = await screen.findByTestId("qa-item-1");
    fireEvent.click(within(item).getByText("回答する"));
    fireEvent.click(within(item).getByTestId("qa-answer-unknown-1"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/qa/1/answer",
        expect.objectContaining({ answer_unknown: true }),
      );
    });
  });

  test("route_category badge renders the Japanese label, never the raw enum", async () => {
    mockQaList([
      makeQa({ id: 1, route_category: "system_researchable", knowledge_area: "implementation" }),
    ]);

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    const badge = await screen.findByTestId("qa-route-category-1");
    expect(badge).toHaveTextContent("AI が調査して回答");
    expect(badge).not.toHaveTextContent("system_researchable");
  });

  test("investigation conclusion renders with a 転記 button that fills the textarea without submitting", async () => {
    mockQaList([
      makeQa({
        id: 1,
        route_category: "hybrid",
        investigation: {
          run_id: 42,
          status: "completed",
          conclusion: "認証はJWTで行われます",
          key_points: ["JWTトークンを検証"],
          evidence: [{ path: "auth.py", start_line: 1, end_line: 5, summary: "検証処理" }],
          uncertainty: "",
          confidence: "likely",
          decision_question: "本番でもこの方式のままでよいですか",
        },
      }),
    ]);

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    const investigationBlock = await screen.findByTestId("qa-investigation-1");
    expect(investigationBlock).toHaveTextContent("AIの調査結果: 認証はJWTで行われます");
    expect(investigationBlock).toHaveTextContent("確認したいこと: 本番でもこの方式のままでよいですか");

    fireEvent.click(screen.getByTestId("qa-investigation-show-evidence-1"));
    const evidenceDetail = await screen.findByTestId("qa-investigation-evidence-1");
    expect(evidenceDetail).toHaveTextContent("auth.py:1-5");

    fireEvent.click(screen.getByTestId("qa-investigation-transcribe-1"));

    const textarea = await screen.findByPlaceholderText("回答を入力");
    expect((textarea as HTMLTextAreaElement).value).toBe("認証はJWTで行われます");
    // Transcribing only fills the draft -- it never itself submits an answer.
    expect(mockApi.post).not.toHaveBeenCalled();
  });

  test("unresolved investigation shows a muted note instead of a conclusion", async () => {
    mockQaList([
      makeQa({
        id: 1,
        route_category: "system_researchable",
        investigation: {
          run_id: 42,
          status: "unresolved",
          conclusion: "",
          key_points: [],
          evidence: [],
          uncertainty: "",
          confidence: "uncertain",
          decision_question: null,
        },
      }),
    ]);

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" answerableAreas={[]} approvedCount={1} />,
      { wrapper: createWrapper() },
    );

    const note = await screen.findByTestId("qa-investigation-unresolved-1");
    expect(note).toHaveTextContent("AIの調査では特定できませんでした");
    expect(screen.queryByTestId("qa-investigation-transcribe-1")).not.toBeInTheDocument();
  });

  test("answers-revised banner text matches #288 auto-refresh copy (Finding 6)", async () => {
    const items = [makeQa({ id: 1 })];
    const list: InterviewQaListOut = {
      session_id: 1, system_id: 1, items,
      open_count: 1, high_priority_open_count: 0, answers_revised_at: 123,
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/qa") return Promise.resolve(list);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      if (path === "/interview/sessions/1/handoffs") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { QaPanel } = await import("@/pages/interview");
    render(
      <QaPanel sessionId={1} actor="dev" approvedCount={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    const banner = await screen.findByTestId("answers-revised-banner");
    expect(banner).toHaveTextContent("理解は自動で更新されます");
    expect(banner).not.toHaveTextContent("自動では再構築されません");
  });
});
