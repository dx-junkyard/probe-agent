/// <reference types="vitest/globals" />
// Issue #287: Review Queue パネルのテスト。
//
// 1. must_review / batch_reviewable の項目だけがアクションカードとして
//    表示され、no_review_required / informational は「対応不要の項目」
//    に折りたたまれ、アクションボタンを一切持たないこと。
// 2. must_review 項目には aria 区別(sr-only の「要確認」テキスト)がある
//    こと。
// 3. 生の enum 文字列(review_category / alignment_state / reason_code)が
//    画面に出ないこと。
// 4. 回答する/修正する/保留 の各アクションが対応する API を呼ぶこと。
// 5. 「突き合わせを実行」ボタンが build API を呼ぶこと。

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { AlignmentItemOut, AlignmentListOut, AlignmentReviewQueueOut } from "@/api/types";

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

function makeItem(overrides: Partial<AlignmentItemOut> & { id: number }): AlignmentItemOut {
  return {
    session_id: 1,
    system_id: 1,
    revision_id: 1,
    snapshot_id: 1,
    intent_item_id: null,
    intent_summary: "トレース収集を効率化したい",
    current_claim: "現在は手動でトレースを確認している",
    current_evidence: [{ path: "src/a.py", start_line: 1, end_line: 3, summary: "手動確認箇所" }],
    gap_summary: "自動化されていない",
    proposed_interpretation: "自動収集の追加を検討",
    alignment_state: "gap",
    risk_flags: [],
    confidence: "likely",
    review_category: "batch_reviewable",
    reason_code: "routine_update",
    user_reason: "軽微な差分です。まとめて確認してください",
    status: "open",
    user_decision: null,
    intelligence_run_id: 1,
    is_mock: false,
    created_at: 0,
    updated_at: 0,
    ...overrides,
  } as AlignmentItemOut;
}

let getImpl: (path: string) => unknown;

beforeEach(() => {
  vi.clearAllMocks();
  getImpl = () => Promise.resolve(undefined);
  mockApi.get.mockImplementation((path: string) => getImpl(path));
});

describe("ReviewQueuePanel", () => {
  test("shows only must_review/batch_reviewable as action cards, collapses the rest, and never renders raw enum values", async () => {
    const mustReview = makeItem({
      id: 1, review_category: "must_review", reason_code: "security_related",
      risk_flags: ["security"], user_reason: "セキュリティに関わるため個別確認が必要です",
      alignment_state: "conflict",
    });
    const batchReviewable = makeItem({ id: 2, review_category: "batch_reviewable" });
    const noReviewRequired = makeItem({
      id: 3, review_category: "no_review_required", reason_code: "no_change",
      alignment_state: "aligned", user_reason: "意図と現状の理解は一致しています。対応は不要です",
    });

    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [mustReview, batchReviewable] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: {
        must_review: [mustReview], batch_reviewable: [batchReviewable],
        no_review_required: [noReviewRequired], unchanged: [], informational: [],
      },
      counts: { must_review: 1, batch_reviewable: 1, no_review_required: 1, unchanged: 0, informational: 0 },
    };

    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    await screen.findByTestId("review-queue-panel");
    const mustReviewCard = await screen.findByTestId("review-item-1");
    expect(within(mustReviewCard).getByTestId("review-item-must-review-1")).toBeInTheDocument();
    expect(within(mustReviewCard).getByTestId("review-item-answer-open-1")).toBeInTheDocument();
    expect(within(mustReviewCard).getByTestId("review-item-correct-open-1")).toBeInTheDocument();
    expect(within(mustReviewCard).getByTestId("review-item-inquiry-open-1")).toBeInTheDocument();
    expect(within(mustReviewCard).getByTestId("review-item-hold-1")).toBeInTheDocument();

    const batchCard = await screen.findByTestId("review-item-2");
    expect(within(batchCard).queryByTestId("review-item-must-review-2")).not.toBeInTheDocument();

    // no_review_required is collapsed, not an action card.
    expect(screen.queryByTestId("review-item-3")).not.toBeInTheDocument();
    const toggle = await screen.findByTestId("review-queue-informational-toggle");
    expect(toggle).toHaveTextContent("対応不要の項目 (1)");
    fireEvent.click(toggle);
    const informationalRow = await screen.findByTestId("review-item-informational-3");
    // The collapsed row never has action buttons.
    expect(within(informationalRow).queryByText("回答する")).not.toBeInTheDocument();
    expect(within(informationalRow).queryByText("修正する")).not.toBeInTheDocument();

    // Raw canonical enum strings never render.
    expect(screen.queryByText(/^must_review$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^batch_reviewable$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^no_review_required$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^security_related$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^conflict$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^aligned$/)).not.toBeInTheDocument();
  });

  test("回答する records the decision via the answer API", async () => {
    const item = makeItem({ id: 10, review_category: "must_review", reason_code: "conflict_detected" });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [item], batch_reviewable: [], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 1, batch_reviewable: 0, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({ ...item, status: "answered" });

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const openButton = await screen.findByTestId("review-item-answer-open-10");
    fireEvent.click(openButton);
    const acceptButton = await screen.findByTestId("review-item-answer-accept_current-10");
    fireEvent.click(acceptButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/alignment/10/answer",
        { decision: "accept_current", note: undefined },
      );
    });
  });

  test("保留 calls the hold API", async () => {
    const item = makeItem({ id: 11, review_category: "batch_reviewable" });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [item], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 1, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({ ...item, status: "held" });

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const holdButton = await screen.findByTestId("review-item-hold-11");
    fireEvent.click(holdButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/alignment/11/hold");
    });
  });

  test("疑問を確認中(status='inquiry')の項目は回答/修正/保留を出さない", async () => {
    const item = makeItem({ id: 12, review_category: "must_review", status: "inquiry" });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [item], batch_reviewable: [], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 1, batch_reviewable: 0, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const card = await screen.findByTestId("review-item-12");
    expect(within(card).getByTestId("review-item-locked-12")).toBeInTheDocument();
    expect(within(card).queryByTestId("review-item-answer-open-12")).not.toBeInTheDocument();
    expect(within(card).queryByTestId("review-item-hold-12")).not.toBeInTheDocument();
  });

  test("突き合わせを実行 calls the build API", async () => {
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 0, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({ session_id: 1, system_id: 1, revision_id: 1, intelligence_run_id: 1, is_mock: false, items: [] });

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const button = await screen.findByTestId("review-queue-build-button");
    fireEvent.click(button);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/1/alignment/build");
    });
  });

  // Issue #290
  test("runtime_mismatch reason_code shows the 実行時不一致 badge", async () => {
    const item = makeItem({
      id: 20, review_category: "must_review", reason_code: "runtime_mismatch",
      user_reason: "コード上の理解と実行時の観測が一致していません",
      runtime_check: "mismatch",
    });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [item], batch_reviewable: [], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 1, batch_reviewable: 0, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const card = await screen.findByTestId("review-item-20");
    expect(within(card).getByTestId("review-item-runtime-mismatch-20")).toHaveTextContent("実行時不一致");
  });

  test("non-runtime_mismatch items never show the 実行時不一致 badge", async () => {
    const item = makeItem({ id: 21, review_category: "batch_reviewable", reason_code: "routine_update" });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [item], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 1, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const card = await screen.findByTestId("review-item-21");
    expect(within(card).queryByTestId("review-item-runtime-mismatch-21")).not.toBeInTheDocument();
  });

  // Issue #291: handoff.
  test("担当者へ引き継ぐ opens the handoff modal and submits via the handoff API", async () => {
    const item = makeItem({ id: 30, review_category: "must_review", reason_code: "conflict_detected" });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [item], batch_reviewable: [], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 1, batch_reviewable: 0, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      if (path === "/interview/sessions/1/handoffs") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };
    mockApi.post.mockResolvedValue({
      id: 99, session_id: 1, system_id: 1, origin_kind: "review_item", origin_id: 30,
      assignee: "山田", background: item.current_claim, needed_decision: "方針を決めてほしい",
      evidence: null, due_note: null, priority: "normal", status: "pending",
      answer_text: null, answered_by: null, answered_at: null, created_by: null,
      created_at: 0, updated_at: 0,
    });

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const openButton = await screen.findByTestId("review-item-handoff-open-30");
    fireEvent.click(openButton);

    const form = await screen.findByTestId("handoff-modal-form");
    fireEvent.change(within(form).getByTestId("handoff-assignee-input"), { target: { value: "山田" } });
    fireEvent.change(within(form).getByTestId("handoff-needed-decision-input"), { target: { value: "方針を決めてほしい" } });
    fireEvent.click(within(form).getByTestId("handoff-submit-button"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/handoffs",
        expect.objectContaining({
          origin_kind: "review_item",
          origin_id: 30,
          assignee: "山田",
          needed_decision: "方針を決めてほしい",
          background: item.current_claim,
        }),
      );
    });
  });

  test("既に引き継ぎ済みの項目には引き継ぐボタンを出さない", async () => {
    const item = makeItem({
      id: 31, review_category: "batch_reviewable", status: "held", handoff_id: 5,
    });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [item], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 1, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const card = await screen.findByTestId("review-item-31");
    expect(within(card).queryByTestId("review-item-handoff-open-31")).not.toBeInTheDocument();
  });

  // Review finding 4: GET .../review-queue already excludes
  // answered/corrected/superseded rows server-side, so this panel never
  // renders them as action cards. The one client-side rendering change is
  // labeling a superseded row with a 履歴 badge wherever the full
  // GET .../alignment listing surfaces one (e.g. the collapsed
  // 対応不要の項目 section), so it's never confused for a current item.
  test("履歴 badge shows on a superseded informational item, not on a current one", async () => {
    const current = makeItem({
      id: 40, review_category: "no_review_required", reason_code: "no_change",
      alignment_state: "aligned", user_reason: "意図と現状の理解は一致しています。対応は不要です",
    });
    const historical = makeItem({
      id: 41, review_category: "no_review_required", reason_code: "no_change",
      alignment_state: "aligned", user_reason: "意図と現状の理解は一致しています。対応は不要です",
      status: "answered", superseded: true,
    });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: {
        must_review: [], batch_reviewable: [],
        no_review_required: [current, historical], unchanged: [], informational: [],
      },
      counts: { must_review: 0, batch_reviewable: 0, no_review_required: 2, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const toggle = await screen.findByTestId("review-queue-informational-toggle");
    expect(toggle).toHaveTextContent("対応不要の項目 (2)");
    fireEvent.click(toggle);

    const currentRow = await screen.findByTestId("review-item-informational-40");
    expect(within(currentRow).queryByTestId("review-item-superseded-40")).not.toBeInTheDocument();

    const historicalRow = await screen.findByTestId("review-item-informational-41");
    expect(within(historicalRow).getByTestId("review-item-superseded-41")).toHaveTextContent("履歴");
  });
});
