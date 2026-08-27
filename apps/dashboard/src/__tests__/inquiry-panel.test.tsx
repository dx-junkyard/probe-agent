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
import type {
  InterviewInquiryOut,
  InterviewIntentItemOut,
  InterviewIntentListOut,
} from "@/api/types";

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

// --- Issue #322: premise tracking / superseded fixtures --------------------
//
// Every field below comes from the server contract (InterviewInquiryOut,
// apps/control-server/app/models.py). The display must be derived from these
// fields alone — never from local state — so the same fixture rendered after
// a reload produces the same badge/message/successor affordance.
function makeInquiry(overrides: Partial<InterviewInquiryOut> & { id: number }): InterviewInquiryOut {
  return {
    session_id: 1, system_id: 1, origin_kind: "review_item", origin_id: 7,
    held_draft: null, status: "superseded", status_reason: null,
    premise_snapshot_id: 11, premise_revision_id: 4,
    premise_review_subject_id: "subject-abc", premise_content_hash: "hash-abc",
    premise_capability_digest: null, premise_intent_digest: null,
    premise_tracking_version: "inquiry-premise-v1", premise_captured_at: 1_700_000_000,
    premise_tracking_state: "tracked", premise_evaluation: null,
    premise_successor_item_id: null, superseded_at: 1_700_003_000,
    created_at: 0, updated_at: 0, closed_at: null,
    ...overrides,
  };
}

// changed(後継あり)/ removed / ambiguous / untrackable(失効していない)の
// 4パターン + 解消済みのまま失効したケース。
const SUPERSEDED_CHANGED = makeInquiry({
  id: 600, premise_evaluation: "changed", premise_successor_item_id: 42,
  status_reason: "review_item_content_changed",
});
const SUPERSEDED_REMOVED = makeInquiry({
  id: 601, premise_evaluation: "removed", status_reason: "origin_removed",
});
const SUPERSEDED_AMBIGUOUS = makeInquiry({
  id: 602, premise_evaluation: "ambiguous", status_reason: "successor_ambiguous",
});
const UNTRACKABLE_OPEN = makeInquiry({
  id: 603, status: "open", premise_tracking_state: "untrackable",
  premise_content_hash: null, premise_review_subject_id: null,
  premise_evaluation: null, superseded_at: null,
});
// 解消済み(closed_at あり)のまま前提が失効したケース: closed_at と
// superseded_at は別物として両方残る。
const SUPERSEDED_AFTER_RESOLVE = makeInquiry({
  id: 604, premise_evaluation: "changed", premise_successor_item_id: 42,
  status_reason: "review_item_content_changed",
  closed_at: 1_700_001_000, superseded_at: 1_700_003_000,
});

const PREMISE_FIXTURES: Record<string, InterviewInquiryOut> = {
  "/interview/inquiries/600": SUPERSEDED_CHANGED,
  "/interview/inquiries/601": SUPERSEDED_REMOVED,
  "/interview/inquiries/602": SUPERSEDED_AMBIGUOUS,
  "/interview/inquiries/603": UNTRACKABLE_OPEN,
  "/interview/inquiries/604": SUPERSEDED_AFTER_RESOLVE,
};

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
    if (path === "/interview/inquiries/300") {
      return Promise.resolve({
        inquiry: {
          id: 300, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
          held_draft: null, status: "open", status_reason: null,
          created_at: 0, updated_at: 0, closed_at: null,
        },
        messages: [
          { id: 1, inquiry_id: 300, system_id: 1, role: "user", content: "researchable な質問", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          {
            id: 2, inquiry_id: 300, system_id: 1, role: "assistant", content: "コードから分かる回答です。",
            detail: { key_points: [], evidence: [], uncertainty: "", route_category: "system_researchable", decision_question: null },
            intelligence_run_id: 1, is_mock: false, created_at: 0,
          },
          { id: 3, inquiry_id: 300, system_id: 1, role: "user", content: "human_only な質問", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          {
            id: 4, inquiry_id: 300, system_id: 1, role: "assistant", content: "あなたの判断が必要な内容です。",
            detail: { key_points: [], evidence: [], uncertainty: "", route_category: "human_only", decision_question: null },
            intelligence_run_id: 2, is_mock: false, created_at: 0,
          },
          { id: 5, inquiry_id: 300, system_id: 1, role: "user", content: "hybrid な質問", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          {
            id: 6, inquiry_id: 300, system_id: 1, role: "assistant", content: "現在は同期処理です。",
            detail: {
              key_points: [], evidence: [], uncertainty: "",
              route_category: "hybrid", decision_question: "非同期化を優先すべきですか?",
            },
            intelligence_run_id: 3, is_mock: false, created_at: 0,
          },
        ],
      });
    }
    if (path === "/interview/inquiries/400") {
      return Promise.resolve({
        inquiry: {
          id: 400, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
          held_draft: null, status: "open", status_reason: null,
          created_at: 0, updated_at: 0, closed_at: null,
        },
        messages: [
          { id: 1, inquiry_id: 400, system_id: 1, role: "user", content: "この関数は実行されていますか?", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          {
            id: 2, inquiry_id: 400, system_id: 1, role: "assistant",
            content: "この関数は summarize を実行します。",
            detail: {
              key_points: [], evidence: [], uncertainty: "",
              route_category: "system_researchable", decision_question: null,
              runtime_evidence: [{
                kind: "runtime_fact", component_id: "summarize_fn", runtime_check: "match",
                summary: "call_count=42 を観測",
                provenance: {
                  environment: null, first_observed_at: 1_700_000_000, last_observed_at: 1_700_000_000,
                  snapshot_ref: { snapshot_id: 9, git_sha: "abc123" }, source: "trace_aggregation",
                  freshness: "fresh",
                },
              }],
              suggested_observation_proposal: null,
            },
            intelligence_run_id: 1, is_mock: false, created_at: 0,
          },
        ],
      });
    }
    if (path === "/interview/inquiries/401") {
      return Promise.resolve({
        inquiry: {
          id: 401, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
          held_draft: null, status: "open", status_reason: null,
          created_at: 0, updated_at: 0, closed_at: null,
        },
        messages: [
          { id: 1, inquiry_id: 401, system_id: 1, role: "user", content: "この関数は実行されていますか?", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          {
            id: 2, inquiry_id: 401, system_id: 1, role: "assistant",
            content: "この関数についての観測データが古くなっています。",
            detail: {
              key_points: [], evidence: [], uncertainty: "",
              route_category: "system_researchable", decision_question: null,
              runtime_evidence: [{
                kind: "runtime_fact", component_id: "stale_fn", runtime_check: "stale",
                summary: "10日前の観測",
                provenance: {
                  environment: null, first_observed_at: 1_600_000_000, last_observed_at: 1_600_000_000,
                  snapshot_ref: null, source: "trace_aggregation", freshness: "stale",
                },
              }],
              suggested_observation_proposal: { target_component: "stale_fn", reason: "stale" },
            },
            intelligence_run_id: 1, is_mock: false, created_at: 0,
          },
        ],
      });
    }
    // --- Issue #295 ST-3: 4-layer progressive disclosure fixtures ----------
    if (path === "/interview/inquiries/500") {
      return Promise.resolve({
        inquiry: {
          id: 500, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
          held_draft: null, status: "open", status_reason: null,
          created_at: 0, updated_at: 0, closed_at: null,
        },
        messages: [
          { id: 1, inquiry_id: 500, system_id: 1, role: "user", content: "この設定はどこで検証していますか?", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          {
            id: 2, inquiry_id: 500, system_id: 1, role: "assistant",
            content: "設定は起動時に検証されます。",
            detail: {
              key_points: ["起動処理内で読み込む", "不正な値は起動を止める"],
              evidence: [
                { path: "apps/control-server/app/config.py", start_line: 10, end_line: 20, summary: "設定を検証" },
                { path: "apps/control-server/tests/test_config.py", start_line: 1, end_line: 5, summary: "検証テスト" },
              ],
              uncertainty: "",
              route_category: "system_researchable", decision_question: null,
            },
            intelligence_run_id: 7, is_mock: false, created_at: 0,
          },
        ],
      });
    }
    if (path === "/interview/inquiries/501") {
      return Promise.resolve({
        inquiry: {
          id: 501, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
          held_draft: null, status: "open", status_reason: null,
          created_at: 0, updated_at: 0, closed_at: null,
        },
        messages: [
          { id: 1, inquiry_id: 501, system_id: 1, role: "user", content: "この処理は同期的ですか?", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          {
            id: 2, inquiry_id: 501, system_id: 1, role: "assistant",
            content: "同期処理と推測されますが確証はありません。",
            detail: {
              key_points: ["呼び出し元がawaitしていない"],
              evidence: [{ path: "apps/control-server/app/worker.py", start_line: 3, end_line: 8, summary: "呼び出し箇所" }],
              uncertainty: "関連する設定ファイルを読めていません",
              route_category: "system_researchable", decision_question: null,
            },
            intelligence_run_id: 8, is_mock: false, created_at: 0,
          },
        ],
      });
    }
    if (path === "/interview/inquiries/502") {
      return Promise.resolve({
        inquiry: {
          id: 502, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
          held_draft: null, status: "open", status_reason: null,
          created_at: 0, updated_at: 0, closed_at: null,
        },
        messages: [
          { id: 1, inquiry_id: 502, system_id: 1, role: "user", content: "この関数は実行されていますか?", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          {
            id: 2, inquiry_id: 502, system_id: 1, role: "assistant",
            content: "実装上は未使用と見えますが、実行時には呼ばれています。",
            detail: {
              key_points: ["静的解析では呼び出し元が見つからない"],
              evidence: [],
              uncertainty: "",
              route_category: "system_researchable", decision_question: null,
              runtime_evidence: [{
                kind: "runtime_fact", component_id: "hidden_fn", runtime_check: "mismatch",
                summary: "call_count=12 を観測(想定と不一致)",
                provenance: {
                  environment: null, first_observed_at: 1_700_000_000, last_observed_at: 1_700_000_000,
                  snapshot_ref: null, source: "trace_aggregation", freshness: "fresh",
                },
              }],
              suggested_observation_proposal: null,
            },
            intelligence_run_id: 9, is_mock: false, created_at: 0,
          },
        ],
      });
    }
    // --- Issue #322: premise tracking / superseded fixtures ---------------
    const premiseFixture = PREMISE_FIXTURES[path];
    if (premiseFixture) {
      return Promise.resolve({
        inquiry: premiseFixture,
        messages: [
          { id: 1, inquiry_id: premiseFixture.id, system_id: 1, role: "user", content: "当時の疑問です", detail: null, intelligence_run_id: null, is_mock: false, created_at: 0 },
          { id: 2, inquiry_id: premiseFixture.id, system_id: 1, role: "assistant", content: "当時の回答です。", detail: null, intelligence_run_id: 1, is_mock: false, created_at: 0 },
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

  // --- Question Router category badge (Issue #286) --------------------------

  describe("route category badge", () => {
    async function openInquiry300() {
      inquiryListItems = [{
        id: 300, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
        held_draft: null, status: "open", status_reason: null,
        created_at: 0, updated_at: 0, closed_at: null,
      }];
      const row = await openIntentPanel();
      fireEvent.click(await within(row).findByTestId("intent-item-inquiry-reopen-5"));
      await within(row).findByText("コードから分かる回答です。");
      return row;
    }

    test("system_researchable shows 「AI が調査して回答」and no raw enum text", async () => {
      const row = await openInquiry300();
      expect(within(row).getByTestId("inquiry-message-route-2")).toHaveTextContent(
        "AI が調査して回答",
      );
      expect(within(row).queryByText("system_researchable")).not.toBeInTheDocument();
    });

    test("human_only shows 「あなたの判断が必要」and no raw enum text", async () => {
      const row = await openInquiry300();
      expect(within(row).getByTestId("inquiry-message-route-4")).toHaveTextContent(
        "あなたの判断が必要",
      );
      expect(within(row).queryByText("human_only")).not.toBeInTheDocument();
    });

    test("hybrid shows 「調査 + あなたの判断」and the decision question emphasized", async () => {
      const row = await openInquiry300();
      expect(within(row).getByTestId("inquiry-message-route-6")).toHaveTextContent(
        "調査 + あなたの判断",
      );
      expect(within(row).queryByText("hybrid")).not.toBeInTheDocument();
      expect(within(row).getByTestId("inquiry-message-decision-question-6")).toHaveTextContent(
        "確認したいこと: 非同期化を優先すべきですか?",
      );
    });

    test("a message with no route_category shows no badge", async () => {
      const row = await openInquiry();
      expect(within(row).queryByTestId("inquiry-message-route-2")).not.toBeInTheDocument();
    });
  });

  // --- Runtime fact evidence / observation proposal hint (Issue #290) -------

  describe("runtime evidence / observation proposal hint", () => {
    async function openInquiryWithId(id: number, expectedContent: string) {
      inquiryListItems = [{
        id, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
        held_draft: null, status: "open", status_reason: null,
        created_at: 0, updated_at: 0, closed_at: null,
      }];
      const row = await openIntentPanel();
      fireEvent.click(await within(row).findByTestId("intent-item-inquiry-reopen-5"));
      await within(row).findByText(expectedContent);
      return row;
    }

    test("fresh runtime evidence renders provenance chips and no stale warning", async () => {
      const row = await openInquiryWithId(400, "この関数は summarize を実行します。");
      // Full provenance chips (environment/snapshot/freshness) live in the
      // layer-4 audit detail (Issue #295), not the layer-3 evidence summary.
      fireEvent.click(within(row).getByTestId("inquiry-show-audit-2"));

      const chips = within(row).getByTestId("inquiry-runtime-evidence-summarize_fn");
      expect(within(chips).getByTestId("runtime-chip-environment")).toHaveTextContent("環境: 不明");
      expect(within(chips).getByTestId("runtime-chip-snapshot")).toHaveTextContent("snapshot: 9");
      expect(within(chips).getByTestId("runtime-chip-freshness")).toHaveTextContent("鮮度: 最新");
      expect(within(chips).queryByTestId("runtime-evidence-stale-warning")).not.toBeInTheDocument();
      // The short content bubble itself never carries raw provenance/numbers.
      const bubble = within(row).getByTestId("inquiry-message-2");
      expect(bubble.textContent).not.toMatch(/trace_aggregation/);
    });

    test("stale runtime evidence is flagged 古い観測です and offers a proposal draft", async () => {
      const row = await openInquiryWithId(401, "この関数についての観測データが古くなっています。");
      fireEvent.click(within(row).getByTestId("inquiry-show-audit-2"));

      const chips = within(row).getByTestId("inquiry-runtime-evidence-stale_fn");
      expect(within(chips).getByTestId("runtime-chip-freshness")).toHaveTextContent("鮮度: 古い");
      expect(within(chips).getByTestId("runtime-evidence-stale-warning")).toHaveTextContent("古い観測です");

      const suggested = within(row).getByTestId("observation-proposal-suggested");
      expect(suggested).toHaveTextContent("stale_fn");
      fireEvent.click(within(suggested).getByTestId("observation-proposal-suggested-open"));
      fireEvent.change(within(suggested).getByTestId("observation-proposal-suggested-purpose"), {
        target: { value: "エラー原因を切り分けたい" },
      });
      mockApi.post.mockResolvedValueOnce({
        id: 1, session_id: 1, system_id: 1, origin_inquiry_id: null, origin_alignment_item_id: null,
        target_component: "stale_fn", purpose: "エラー原因を切り分けたい", expected_cost: null,
        risk_note: null, retention_note: null, status: "proposed", decision_by: null, decision_at: null,
        created_at: 0, policy_pointer: null,
      });
      fireEvent.click(within(suggested).getByTestId("observation-proposal-suggested-submit"));

      await waitFor(() => {
        expect(mockApi.post).toHaveBeenCalledWith(
          "/interview/sessions/1/observation-proposals",
          { target_component: "stale_fn", purpose: "エラー原因を切り分けたい" },
        );
      });
    });
  });

  // --- 4-layer progressive disclosure (Issue #295 ST-3, §4.4/§4.10) ---------

  describe("4-layer progressive disclosure", () => {
    async function openInquiryWithId(id: number, expectedContent: string) {
      inquiryListItems = [{
        id, session_id: 1, system_id: 1, origin_kind: "intent", origin_id: 5,
        held_draft: null, status: "open", status_reason: null,
        created_at: 0, updated_at: 0, closed_at: null,
      }];
      const row = await openIntentPanel();
      fireEvent.click(await within(row).findByTestId("intent-item-inquiry-reopen-5"));
      await within(row).findByText(expectedContent);
      return row;
    }

    test("all 4 layers are available and collapsed by default when key_points/evidence exist with no exception signal", async () => {
      const row = await openInquiryWithId(500, "設定は起動時に検証されます。");

      // Layer 1 (content) is always visible.
      expect(within(row).getByText("設定は起動時に検証されます。")).toBeInTheDocument();

      // Layers 2-4 exist (key_points + evidence are both non-empty) but stay
      // collapsed until clicked -- no exception signal on this message.
      expect(within(row).queryByTestId("inquiry-reasons-detail-2")).not.toBeInTheDocument();
      expect(within(row).queryByTestId("inquiry-evidence-detail-2")).not.toBeInTheDocument();
      expect(within(row).queryByTestId("inquiry-audit-detail-2")).not.toBeInTheDocument();

      fireEvent.click(within(row).getByTestId("inquiry-show-reasons-2"));
      expect(within(row).getByTestId("inquiry-reasons-detail-2")).toHaveTextContent("起動処理内で読み込む");
      // Layer 2 never shows file paths/line numbers -- those are layer 4 only.
      expect(within(row).getByTestId("inquiry-reasons-detail-2").textContent).not.toMatch(/config\.py/);

      fireEvent.click(within(row).getByTestId("inquiry-show-evidence-2"));
      const evidenceDetail = within(row).getByTestId("inquiry-evidence-detail-2");
      // Layer 3 shows a docs/code/test kind label + summary, never the exact
      // path:line (that is layer 4's job).
      expect(evidenceDetail).toHaveTextContent("コード");
      expect(evidenceDetail).toHaveTextContent("テスト");
      expect(evidenceDetail).toHaveTextContent("設定を検証");
      expect(evidenceDetail.textContent).not.toMatch(/config\.py:10-20/);
      await waitFor(() => {
        expect(mockApi.post).toHaveBeenCalledWith(
          "/interview/metric-events",
          expect.objectContaining({
            event_key: "evidence_available:inquiry_message:2",
            event_type: "evidence_available",
          }),
        );
        expect(mockApi.post).toHaveBeenCalledWith(
          "/interview/metric-events",
          expect.objectContaining({
            event_key: "evidence_expanded:inquiry_message:2",
            event_type: "evidence_expanded",
          }),
        );
      });

      fireEvent.click(within(row).getByTestId("inquiry-show-audit-2"));
      const auditDetail = within(row).getByTestId("inquiry-audit-detail-2");
      expect(auditDetail).toHaveTextContent("apps/control-server/app/config.py:10-20");
      expect(auditDetail).toHaveTextContent("apps/control-server/tests/test_config.py:1-5");
      expect(auditDetail).toHaveTextContent("調査 run: 7");
    });

    test("a layer with no backing fields renders no toggle at all", async () => {
      // Inquiry 100 (used by openInquiry()) has key_points only -- no
      // evidence, no runtime_evidence, no uncertainty.
      const row = await openInquiry();
      expect(within(row).getByTestId("inquiry-show-reasons-2")).toBeInTheDocument();
      expect(within(row).queryByTestId("inquiry-show-evidence-2")).not.toBeInTheDocument();
      expect(within(row).queryByTestId("inquiry-show-audit-2")).not.toBeInTheDocument();
    });

    test("non-empty uncertainty pre-expands layers 2-3 (exception), layer 4 stays collapsed", async () => {
      const row = await openInquiryWithId(501, "同期処理と推測されますが確証はありません。");

      // No click needed -- already expanded because of the uncertainty signal.
      expect(within(row).getByTestId("inquiry-reasons-detail-2")).toHaveTextContent(
        "呼び出し元がawaitしていない",
      );
      const evidenceDetail = within(row).getByTestId("inquiry-evidence-detail-2");
      expect(evidenceDetail).toHaveTextContent("関連する設定ファイルを読めていません");
      // Layer 4 (audit) is never auto-expanded even under the exception.
      expect(within(row).queryByTestId("inquiry-audit-detail-2")).not.toBeInTheDocument();

      // Buttons reflect the already-open state.
      expect(within(row).getByTestId("inquiry-show-reasons-2")).toHaveTextContent("理由を隠す");
      expect(within(row).getByTestId("inquiry-show-evidence-2")).toHaveTextContent("根拠を隠す");
      expect(mockApi.post.mock.calls.filter(
        ([path, body]) => path === "/interview/metric-events"
          && (body.event_key === "evidence_available:inquiry_message:2"
            || body.event_key === "evidence_expanded:inquiry_message:2"),
      )).toHaveLength(0);
    });

    test("a runtime_evidence mismatch (conflict) pre-expands layers 2-3", async () => {
      const row = await openInquiryWithId(
        502, "実装上は未使用と見えますが、実行時には呼ばれています。",
      );

      expect(within(row).getByTestId("inquiry-reasons-detail-2")).toHaveTextContent(
        "静的解析では呼び出し元が見つからない",
      );
      const evidenceDetail = within(row).getByTestId("inquiry-evidence-detail-2");
      expect(evidenceDetail).toHaveTextContent("Runtime");
      expect(evidenceDetail).toHaveTextContent("想定と不一致");
    });
  });

  // --- 前提追跡と superseded 表示 (Issue #322 / server #308・#320-#323) ------
  //
  // superseded は「当時の前提に対する会話は履歴として残すが、現行の判断には
  // 流用しない」という終端状態で、未解決/取り消しとも「回答が誤っていた」と
  // も違う。表示はすべてサーバーのフィールド由来(ローカル state なし)なの
  // で、リロード/セッション再開後も同じ結果になる。
  describe("premise tracking / superseded", () => {
    async function renderPanel(existingInquiryId: number) {
      const { InquiryPanel } = await import(
        "@/components/system-understanding/inquiry-panel"
      );
      render(
        <InquiryPanel
          sessionId={1}
          originKind="review_item"
          originId={7}
          heldDraft={null}
          existingInquiryId={existingInquiryId}
          onResolved={vi.fn()}
          onHeld={vi.fn()}
          onCancel={vi.fn()}
        />,
        { wrapper: createWrapper() },
      );
      return screen.findByTestId("inquiry-panel");
    }

    test("premise_evaluation='changed' は固定の失効バッジ・説明・理由を出し、後継への導線を持つ", async () => {
      const panel = await renderPanel(600);

      expect(await within(panel).findByTestId("inquiry-superseded-badge-600")).toHaveTextContent(
        "前提が変わったため再確認が必要",
      );
      // 未解決/取り消しとの違いを明示する固定説明文。
      expect(within(panel).getByTestId("inquiry-superseded-explanation-600")).toHaveTextContent(
        "当時の前提に対する会話は履歴として残りますが、現行の判断には流用しません(未解決・取り消しとは異なります)。",
      );
      expect(within(panel).getByTestId("inquiry-premise-reason-600")).toHaveTextContent(
        "前提となる確認項目の内容が変わりました。",
      );
      // 後継が一意に決まっているときだけ導線を出す。リンク先はサーバーが返
      // した premise_successor_item_id そのもの。
      const link = within(panel).getByTestId("inquiry-successor-link-600");
      expect(link).toHaveAttribute("href", "#review-item-42");
      expect(link).toHaveTextContent("後継の確認項目 #42 を開く");
      expect(within(panel).queryByTestId("inquiry-successor-unknown-600")).not.toBeInTheDocument();
      // 生の enum / reason code は本文に出さない。
      expect(within(panel).queryByText("changed")).not.toBeInTheDocument();
      expect(within(panel).queryByText("superseded")).not.toBeInTheDocument();
    });

    test("premise_evaluation='removed' は固定文言を出し、後継の導線を出さない", async () => {
      const panel = await renderPanel(601);

      expect(await within(panel).findByTestId("inquiry-premise-reason-601")).toHaveTextContent(
        "この疑問の論点は現行のレビューから無くなりました。",
      );
      expect(within(panel).queryByTestId("inquiry-successor-link-601")).not.toBeInTheDocument();
      expect(within(panel).getByTestId("inquiry-successor-unknown-601")).toHaveTextContent(
        "後継の確認項目は特定されていません。必要であれば、現在の確認項目から新しい疑問を開始してください。",
      );
    });

    test("premise_evaluation='ambiguous' は固定文言を出し、クライアントは後継を推測しない", async () => {
      const panel = await renderPanel(602);

      expect(await within(panel).findByTestId("inquiry-premise-reason-602")).toHaveTextContent(
        "後継の確認項目を一意に特定できませんでした。",
      );
      expect(within(panel).queryByTestId("inquiry-successor-link-602")).not.toBeInTheDocument();
      expect(within(panel).getByTestId("inquiry-successor-unknown-602")).toBeInTheDocument();
    });

    test("premise_tracking_state='untrackable' は他の3つとは別の固定文言を出し、失効扱いにはしない", async () => {
      const panel = await renderPanel(603);

      expect(await within(panel).findByTestId("inquiry-premise-reason-603")).toHaveTextContent(
        "作成時の前提を自動で比較できません(旧データ、または内容のハッシュが不明です)。",
      );
      // 失効ではないので、失効バッジも後継の導線も出ない。
      expect(within(panel).queryByTestId("inquiry-superseded-badge-603")).not.toBeInTheDocument();
      expect(within(panel).queryByTestId("inquiry-successor-unknown-603")).not.toBeInTheDocument();
      // 'open' なので通常どおり会話は続けられる。
      expect(within(panel).getByTestId("inquiry-resolve")).toBeInTheDocument();
    });

    test("superseded の Inquiry には追加質問・解消・保留のアクションを一切出さない", async () => {
      const panel = await renderPanel(600);
      await within(panel).findByTestId("inquiry-superseded-badge-600");

      // /message, /resolve, /resume はサーバーが 409 で拒否するため、UI から
      // 提示してはいけない。保留中マーカーも出さない。
      expect(within(panel).queryByTestId("inquiry-followup-input")).not.toBeInTheDocument();
      expect(within(panel).queryByTestId("inquiry-followup-submit")).not.toBeInTheDocument();
      expect(within(panel).queryByTestId("inquiry-resolve")).not.toBeInTheDocument();
      expect(within(panel).queryByTestId("inquiry-reopen-doubt")).not.toBeInTheDocument();
      expect(within(panel).queryByTestId("inquiry-hold")).not.toBeInTheDocument();
      expect(within(panel).queryByTestId("inquiry-held-marker")).not.toBeInTheDocument();
      // 履歴としての会話自体は読める。
      expect(within(panel).getByText("当時の回答です。")).toBeInTheDocument();
      // 表示するだけでは一切の書き込みを行わない。
      expect(mockApi.post).not.toHaveBeenCalled();
    });

    test("監査詳細に作成時の snapshot / revision と失効理由が出る", async () => {
      const panel = await renderPanel(600);
      fireEvent.click(await within(panel).findByTestId("inquiry-premise-audit-toggle-600"));

      const audit = within(panel).getByTestId("inquiry-premise-audit-600");
      expect(audit).toHaveTextContent("作成時のスナップショット: #11");
      expect(audit).toHaveTextContent("作成時のリビジョン: #4");
      expect(audit).toHaveTextContent("失効理由: 内容が変わった");
      expect(audit).toHaveTextContent("前提の追跡状態: 追跡あり");
    });

    test("解消済みのまま失効した Inquiry は解消日時と失効日時の両方を出す", async () => {
      const panel = await renderPanel(604);
      fireEvent.click(await within(panel).findByTestId("inquiry-premise-audit-toggle-604"));

      // closed_at(解消した瞬間)と superseded_at(前提が失効した瞬間)は
      // 別のフィールドで、どちらも失われない。
      expect(within(panel).getByTestId("inquiry-premise-closed-at-604")).toHaveTextContent("解消日時:");
      expect(within(panel).getByTestId("inquiry-premise-superseded-at-604")).toHaveTextContent("失効日時:");
    });

    test("前提が追跡済みで未評価の Inquiry には前提の注記を出さない", async () => {
      // 既存の open な Inquiry(100)は premise_evaluation なし・tracking も
      // not_applicable なので、余計な注記が増えないこと。
      const row = await openInquiry();
      expect(within(row).queryByTestId("inquiry-premise-notice-100")).not.toBeInTheDocument();
    });

    test("superseded の Inquiry は active 扱いされず、元の項目からは新しい疑問を始められる", async () => {
      inquiryListItems = [SUPERSEDED_CHANGED, { ...SUPERSEDED_REMOVED, origin_kind: "intent", origin_id: 5 }];

      const row = await openIntentPanel();

      // 再開/再オープンの導線は出さず、通常の「疑問がある」のまま。
      expect(await within(row).findByTestId("intent-item-inquiry-open-5")).toBeInTheDocument();
      expect(within(row).queryByTestId("intent-item-inquiry-reopen-5")).not.toBeInTheDocument();
      expect(within(row).queryByTestId("intent-item-held-inquiry-marker-5")).not.toBeInTheDocument();
      expect(within(row).queryByTestId("intent-item-inquiry-resume-5")).not.toBeInTheDocument();
    });
  });

  // Issue #322: 'superseded' は active/resumable の集合に入らない。
  describe("activeInquiryByOrigin / supersededInquiries", () => {
    test("superseded は active から除外され、履歴セレクタだけが返す", async () => {
      const { activeInquiryByOrigin, supersededInquiries } = await import("@/api/hooks");
      const items = [
        SUPERSEDED_CHANGED,
        SUPERSEDED_REMOVED,
        makeInquiry({ id: 610, status: "open", origin_id: 8 }),
      ];

      const active = activeInquiryByOrigin(items);
      expect(active.get("review_item:7")).toBeUndefined();
      expect(active.get("review_item:8")?.id).toBe(610);

      expect(supersededInquiries(items).map(i => i.id)).toEqual([600, 601]);
    });
  });
});
