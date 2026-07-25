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
    policy_version: "alignment-review-v1",
    policy_digest: "a86ee1d85f35cef6ad5b9d190db56a8195d458d5ffd521489f032cbed1de335d",
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

  // Issue #295 (ST-2): category count summary.
  test("shows the category count summary from GET /alignment counts, defaulting missing categories to 0", async () => {
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [], no_review_required: [], informational: [] },
      // `unchanged` intentionally absent from `counts`, simulating a
      // backend response predating the category's materialization.
      counts: { must_review: 2, batch_reviewable: 1, no_review_required: 3, informational: 1 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const summary = await screen.findByTestId("review-queue-category-summary");
    await waitFor(() => {
      expect(within(summary).getByTestId("review-queue-summary-must_review")).toHaveTextContent("要確認 2件");
    });
    expect(within(summary).getByTestId("review-queue-summary-batch_reviewable")).toHaveTextContent("一括レビュー可 1件");
    expect(within(summary).getByTestId("review-queue-summary-no_review_required")).toHaveTextContent("確認不要 3件");
    expect(within(summary).getByTestId("review-queue-summary-unchanged")).toHaveTextContent("前回から変更なし 0件");
    expect(within(summary).getByTestId("review-queue-summary-informational")).toHaveTextContent("参考情報 1件");
  });

  // Issue #295 (ST-2) / PR #296 review fix (Finding 5): bulk answer mode now
  // sends one POST .../answers-batch call instead of per-item /answer calls.
  test("一括回答モードでは選択が保留され、まとめて送信で answers-batch を1回だけ呼ぶ", async () => {
    const item1 = makeItem({ id: 50, review_category: "must_review", reason_code: "conflict_detected" });
    const item2 = makeItem({ id: 51, review_category: "batch_reviewable" });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item1, item2] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [item1], batch_reviewable: [item2], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 1, batch_reviewable: 1, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/alignment/answers-batch") {
        return Promise.resolve({
          session_id: 1, system_id: 1, refreshed: true,
          results: [
            { item_id: 50, success: true, item: { ...item1, status: "answered" }, error: null },
            { item_id: 51, success: true, item: { ...item2, status: "answered" }, error: null },
          ],
        });
      }
      return Promise.resolve(undefined);
    });

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("review-queue-bulk-mode-toggle"));

    // Stage item 50.
    fireEvent.click(await screen.findByTestId("review-item-answer-open-50"));
    fireEvent.click(await screen.findByTestId("review-item-answer-accept_current-50"));
    // Staging must not call the API immediately.
    expect(mockApi.post).not.toHaveBeenCalled();
    expect(await screen.findByTestId("review-item-staged-50")).toHaveTextContent("回答予定: 現状でよい");

    // Stage item 51 with a different decision.
    fireEvent.click(await screen.findByTestId("review-item-answer-open-51"));
    fireEvent.click(await screen.findByTestId("review-item-answer-needs_change-51"));
    expect(await screen.findByTestId("review-item-staged-51")).toHaveTextContent("回答予定: 変更が必要");

    expect(await screen.findByTestId("review-queue-bulk-pending-count")).toHaveTextContent("2件選択中");

    fireEvent.click(await screen.findByTestId("review-queue-bulk-submit"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/alignment/answers-batch",
        {
          answers: [
            { item_id: 50, decision: "accept_current", note: undefined },
            { item_id: 51, decision: "needs_change", note: undefined },
          ],
        },
      );
    });
    // Exactly one call to the batch endpoint -- never per-item /answer calls.
    expect(mockApi.post).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByTestId("review-queue-bulk-result")).toHaveTextContent("2件送信しました");
    });
  });

  test("一括送信で一部失敗した場合、日本語で部分失敗を明示し、失敗項目だけ保留に残す", async () => {
    const item1 = makeItem({ id: 60, review_category: "batch_reviewable" });
    const item2 = makeItem({ id: 61, review_category: "batch_reviewable" });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [item1, item2] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [item1, item2], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 2, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/alignment/answers-batch") {
        return Promise.resolve({
          session_id: 1, system_id: 1, refreshed: true,
          results: [
            { item_id: 60, success: true, item: { ...item1, status: "answered" }, error: null },
            { item_id: 61, success: false, item: null, error: "internal error" },
          ],
        });
      }
      return Promise.resolve(undefined);
    });

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("review-queue-bulk-mode-toggle"));
    fireEvent.click(await screen.findByTestId("review-item-answer-open-60"));
    fireEvent.click(await screen.findByTestId("review-item-answer-accept_current-60"));
    fireEvent.click(await screen.findByTestId("review-item-answer-open-61"));
    fireEvent.click(await screen.findByTestId("review-item-answer-accept_current-61"));

    fireEvent.click(await screen.findByTestId("review-queue-bulk-submit"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(screen.getByTestId("review-queue-bulk-result")).toHaveTextContent("1件送信、1件失敗しました");
    });
    // The failed item stays staged so the user can retry.
    expect(screen.getByTestId("review-item-staged-61")).toBeInTheDocument();
    // The server's own per-item error text is shown verbatim.
    expect(screen.getByTestId("review-item-staged-error-61")).toHaveTextContent("internal error");
    // The succeeded item is no longer staged.
    expect(screen.queryByTestId("review-item-staged-60")).not.toBeInTheDocument();
    expect(screen.queryByTestId("review-item-staged-error-60")).not.toBeInTheDocument();
  });

  // PR #296 review fix (Finding 3): superseded history section.
  test("superseded_items は「履歴 N件」として初期折りたたみで表示され、監査詳細を展開できる", async () => {
    const current = makeItem({
      id: 90, review_category: "no_review_required", reason_code: "no_change",
      alignment_state: "aligned",
    });
    const historical = makeItem({
      id: 91, review_category: "must_review", reason_code: "conflict_detected",
      status: "answered", superseded: true, confidence: "confirmed",
      intelligence_run_id: 5,
    });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [], no_review_required: [current], unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 0, no_review_required: 1, unchanged: 0, informational: 0 },
      superseded_items: [historical],
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const toggle = await screen.findByTestId("review-queue-superseded-toggle");
    expect(toggle).toHaveTextContent("履歴 1件");
    // Collapsed by default -- no action cards, and no history row yet.
    expect(screen.queryByTestId("review-item-informational-91")).not.toBeInTheDocument();
    // A superseded item, however 'must_review' its review_category was at
    // creation time, is never rendered as an action card.
    expect(screen.queryByTestId("review-item-91")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent("履歴を隠す");
    const historyList = await screen.findByTestId("review-queue-superseded-list");
    const historyRow = within(historyList).getByTestId("review-item-informational-91");
    expect(within(historyRow).getByTestId("review-item-superseded-91")).toHaveTextContent("履歴");

    // Reuses the existing AuditDetail expansion.
    fireEvent.click(within(historyRow).getByTestId("review-item-informational-detail-toggle-91"));
    expect(within(historyRow).getByTestId("review-item-audit-detail-91")).toHaveTextContent("#5");
  });

  test("superseded_items が空/未提供のときは履歴セクションを出さない", async () => {
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

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    await screen.findByTestId("review-queue-panel");
    expect(screen.queryByTestId("review-queue-superseded-toggle")).not.toBeInTheDocument();
  });

  // Issue #295 (ST-2): audit detail expansion for no-action rows.
  test("確認不要項目の詳細を展開すると、応答に存在する監査フィールドが表示される", async () => {
    const item = makeItem({
      id: 70, review_category: "no_review_required", reason_code: "no_change",
      alignment_state: "aligned", user_reason: "意図と現状の理解は一致しています。対応は不要です",
      confidence: "confirmed", updated_at: 1700000000, intelligence_run_id: 42, revision_id: 7, snapshot_id: 9,
      carried_over_from: 12,
    });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [], no_review_required: [item], unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 0, no_review_required: 1, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("review-queue-informational-toggle"));
    const row = await screen.findByTestId("review-item-informational-70");
    fireEvent.click(within(row).getByTestId("review-item-informational-detail-toggle-70"));

    const detail = within(row).getByTestId("review-item-audit-detail-70");
    expect(detail).toHaveTextContent("確定");
    expect(detail).toHaveTextContent("#9");
    expect(detail).toHaveTextContent("#7");
    expect(detail).toHaveTextContent("#42");
    expect(detail).toHaveTextContent("alignment-review-v1");
    expect(within(row).getByTestId("review-item-policy-70")).toHaveTextContent("a86ee1d85f35");
    expect(within(row).getByTestId("review-item-carried-over-70")).toHaveTextContent("#12");
    // A field the response doesn't provide (content_hash) must not render.
    expect(within(row).queryByTestId("review-item-content-hash-70")).not.toBeInTheDocument();
  });

  // Finding 4: the audit detail must surface the human decision (action +
  // note + timestamp), so an approve / needs_change / reject / corrected /
  // held item is distinguishable in history, not just its carried_over_from.
  test("監査詳細に人間の判断(action・メモ・日時)が表示される", async () => {
    const item = makeItem({
      id: 88, review_category: "no_review_required", reason_code: "no_change",
      alignment_state: "aligned", user_reason: "対応は不要です", confidence: "confirmed",
      updated_at: 1700000000, intelligence_run_id: 42, revision_id: 7, snapshot_id: 9,
      status: "answered",
      user_decision: { action: "needs_change", note: "この解釈は変更が必要", decided_at: 1700000500, decided_by: null },
    });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [], no_review_required: [item], unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 0, no_review_required: 1, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("review-queue-informational-toggle"));
    const row = await screen.findByTestId("review-item-informational-88");
    fireEvent.click(within(row).getByTestId("review-item-informational-detail-toggle-88"));

    const decision = within(row).getByTestId("review-item-user-decision-88");
    expect(decision).toHaveTextContent("変更が必要");
    expect(decision).toHaveTextContent("この解釈は変更が必要");
  });

  // Issue #295 (ST-2): §5.4 deterministic sample check.
  test("確認不要/参考情報が3件を超えるとき、id昇順で先頭3件だけをサンプル確認として展開表示する", async () => {
    const items = [5, 3, 1, 4, 2].map(id => makeItem({
      id, review_category: "no_review_required", reason_code: "no_change",
      alignment_state: "aligned", user_reason: "意図と現状の理解は一致しています。対応は不要です",
    }));
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [], no_review_required: items, unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 0, no_review_required: 5, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const sampleSection = await screen.findByTestId("review-queue-sample-section");
    expect(within(sampleSection).getByTestId("review-item-informational-1")).toBeInTheDocument();
    expect(within(sampleSection).getByTestId("review-item-informational-2")).toBeInTheDocument();
    expect(within(sampleSection).getByTestId("review-item-informational-3")).toBeInTheDocument();
    expect(within(sampleSection).queryByTestId("review-item-informational-4")).not.toBeInTheDocument();
    expect(within(sampleSection).queryByTestId("review-item-informational-5")).not.toBeInTheDocument();
    // PR #296 review fix (2nd pass, Finding 5b): sample rows still show the
    // current_claim unconditionally, but the audit detail (evidence,
    // content_hash, etc.) stays collapsed by default like a non-sample row
    // -- three items' worth of audit detail expanded on first render was
    // information overload. The Inquiry entry point remains reachable
    // without expanding anything.
    expect(within(sampleSection).queryByTestId("review-item-audit-detail-1")).not.toBeInTheDocument();
    expect(within(sampleSection).getByTestId("review-item-inquiry-open-1")).toBeInTheDocument();
    fireEvent.click(within(sampleSection).getByTestId("review-item-informational-detail-toggle-1"));
    expect(within(sampleSection).getByTestId("review-item-audit-detail-1")).toBeInTheDocument();

    // Items 4 and 5 remain reachable only through the collapsed section.
    const toggle = await screen.findByTestId("review-queue-informational-toggle");
    fireEvent.click(toggle);
    const list = await screen.findByTestId("review-queue-informational-list");
    expect(within(list).getByTestId("review-item-informational-4")).toBeInTheDocument();
    expect(within(list).getByTestId("review-item-informational-5")).toBeInTheDocument();
    expect(within(list).queryByTestId("review-item-informational-1")).not.toBeInTheDocument();
  });

  test("確認不要/参考情報が3件以下のときはサンプル確認セクションを出さない", async () => {
    const items = [1, 2, 3].map(id => makeItem({
      id, review_category: "no_review_required", reason_code: "no_change", alignment_state: "aligned",
    }));
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [], no_review_required: items, unchanged: [], informational: [] },
      counts: { must_review: 0, batch_reviewable: 0, no_review_required: 3, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    await screen.findByTestId("review-queue-informational-toggle");
    expect(screen.queryByTestId("review-queue-sample-section")).not.toBeInTheDocument();
  });

  // Issue #295 (ST-2): §4.4 evidence-upfront exceptions.
  test("alignment_state が conflict のとき根拠は初期表示で展開される", async () => {
    const item = makeItem({
      id: 80, review_category: "must_review", reason_code: "conflict_detected", alignment_state: "conflict",
      current_evidence: [
        { path: "src/a.py", start_line: 1, end_line: 2, summary: "1つ目" },
        { path: "src/b.py", start_line: 3, end_line: 4, summary: "2つ目" },
      ],
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

    const card = await screen.findByTestId("review-item-80");
    expect(within(card).getByTestId("review-item-evidence-80")).toBeInTheDocument();
    expect(within(card).getByTestId("review-item-evidence-toggle-80")).toHaveTextContent("根拠を隠す");
  });

  test("該当する例外が無いときは根拠は初期表示で折りたたまれる", async () => {
    const item = makeItem({
      id: 81, review_category: "batch_reviewable", reason_code: "routine_update", alignment_state: "gap",
      risk_flags: [],
      current_evidence: [
        { path: "src/a.py", start_line: 1, end_line: 2, summary: "1つ目" },
        { path: "src/b.py", start_line: 3, end_line: 4, summary: "2つ目" },
      ],
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

    const card = await screen.findByTestId("review-item-81");
    expect(within(card).queryByTestId("review-item-evidence-81")).not.toBeInTheDocument();
    expect(within(card).getByTestId("review-item-evidence-toggle-81")).toHaveTextContent("根拠を見る");
  });

  test("runtime_check が stale のとき根拠は初期表示で展開される", async () => {
    const item = makeItem({
      id: 82, review_category: "must_review", reason_code: "runtime_mismatch", alignment_state: "gap",
      runtime_check: "stale",
      current_evidence: [
        { path: "src/a.py", start_line: 1, end_line: 2, summary: "1つ目" },
        { path: "src/b.py", start_line: 3, end_line: 4, summary: "2つ目" },
      ],
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

    const card = await screen.findByTestId("review-item-82");
    expect(within(card).getByTestId("review-item-evidence-82")).toBeInTheDocument();
  });

  // PR #296 review fix (2nd pass, Finding 3/5b): actionable categories
  // (must_review/batch_reviewable) prefer outstanding_counts (未対応件数)
  // over counts (a total that can still include already-answered rows),
  // so this panel's own summary never disagrees with its action cards.
  test("要確認/一括レビュー可の件数サマリは outstanding_counts を優先し、counts と食い違っていても後者にフォールバックしない", async () => {
    const openItem = makeItem({ id: 100, review_category: "must_review" });
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [openItem] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: {
        must_review: [openItem], batch_reviewable: [], no_review_required: [], unchanged: [], informational: [],
      },
      // counts still counts a since-answered row that hasn't been
      // superseded yet; outstanding_counts already excludes it.
      counts: { must_review: 2, batch_reviewable: 3, no_review_required: 0, unchanged: 0, informational: 0 },
      outstanding_counts: { must_review: 1, batch_reviewable: 0, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const summary = await screen.findByTestId("review-queue-category-summary");
    await waitFor(() => {
      expect(within(summary).getByTestId("review-queue-summary-must_review")).toHaveTextContent("要確認 1件");
    });
    expect(within(summary).getByTestId("review-queue-summary-batch_reviewable")).toHaveTextContent("一括レビュー可 0件");
    // Matches the one action card actually rendered.
    expect(await screen.findByTestId("review-item-100")).toBeInTheDocument();
  });

  test("要確認/一括レビュー可の件数サマリは outstanding_counts が無いとき counts にフォールバックする", async () => {
    const queue: AlignmentReviewQueueOut = { session_id: 1, system_id: 1, items: [] };
    const full: AlignmentListOut = {
      session_id: 1, system_id: 1,
      items_by_category: { must_review: [], batch_reviewable: [], no_review_required: [], unchanged: [], informational: [] },
      counts: { must_review: 2, batch_reviewable: 1, no_review_required: 0, unchanged: 0, informational: 0 },
    };
    getImpl = (path: string) => {
      if (path === "/interview/sessions/1/review-queue") return Promise.resolve(queue);
      if (path === "/interview/sessions/1/alignment") return Promise.resolve(full);
      if (path === "/interview/sessions/1/inquiries") return Promise.resolve({ session_id: 1, system_id: 1, items: [] });
      return Promise.resolve(undefined);
    };

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    const summary = await screen.findByTestId("review-queue-category-summary");
    await waitFor(() => {
      expect(within(summary).getByTestId("review-queue-summary-must_review")).toHaveTextContent("要確認 2件");
    });
    expect(within(summary).getByTestId("review-queue-summary-batch_reviewable")).toHaveTextContent("一括レビュー可 1件");
  });

  // PR #296 review fix (2nd pass, Finding 2): まとめて送信 includes each
  // item's content_hash (as read from the last GET .../alignment response)
  // so the server can validate the item hasn't changed since it was staged.
  test("まとめて送信は各項目の content_hash をリクエストに含める", async () => {
    const item = makeItem({ id: 110, review_category: "batch_reviewable", content_hash: "hash-110" });
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
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/alignment/answers-batch") {
        return Promise.resolve({
          session_id: 1, system_id: 1, refreshed: true,
          results: [{ item_id: 110, success: true, item: { ...item, status: "answered" }, error: null }],
        });
      }
      return Promise.resolve(undefined);
    });

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("review-queue-bulk-mode-toggle"));
    fireEvent.click(await screen.findByTestId("review-item-answer-open-110"));
    fireEvent.click(await screen.findByTestId("review-item-answer-accept_current-110"));
    fireEvent.click(await screen.findByTestId("review-queue-bulk-submit"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/1/alignment/answers-batch",
        { answers: [{ item_id: 110, decision: "accept_current", note: undefined, content_hash: "hash-110" }] },
      );
    });
  });

  // PR #296 review fix (2nd pass, Finding 2): a batch failure on an entry
  // that carried a content_hash is shown verbatim -- the Control Server's
  // stale-content_hash error is already Japanese (confirmed against
  // apps/control-server/app/routes/interview_alignment.py's
  // `_BATCH_ERROR_STALE_CONTENT`) -- and the item stays staged for retry
  // (same as any other partial-failure case).
  test("content_hash 付きの項目がまとめて送信で失敗すると、日本語で更新済みである旨を表示し、保留に残す", async () => {
    const item = makeItem({ id: 120, review_category: "batch_reviewable", content_hash: "hash-120" });
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
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/1/alignment/answers-batch") {
        return Promise.resolve({
          session_id: 1, system_id: 1, refreshed: true,
          results: [{
            item_id: 120, success: false, item: null,
            error: "項目が更新されています。最新の内容を確認してください。",
          }],
        });
      }
      return Promise.resolve(undefined);
    });

    const { ReviewQueuePanel } = await import("@/components/system-understanding/review-queue");
    render(<ReviewQueuePanel sessionId={1} />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("review-queue-bulk-mode-toggle"));
    fireEvent.click(await screen.findByTestId("review-item-answer-open-120"));
    fireEvent.click(await screen.findByTestId("review-item-answer-accept_current-120"));
    fireEvent.click(await screen.findByTestId("review-queue-bulk-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("review-item-staged-error-120")).toHaveTextContent("項目が更新されています");
    });
    // Stays staged so the user can retry after refreshing the content.
    expect(screen.getByTestId("review-item-staged-120")).toBeInTheDocument();
  });
});
