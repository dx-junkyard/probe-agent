// Issue #336: the shared 共同理解 entry point.
//
// Before this, the only way in was the Q&A card. The other three origins had a
// working server contract and no way for a developer to reach it — the same
// "isolated third feature" shape Epic #328 set out to remove, seen from the UI
// side.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { JointUnderstandingListOut, JointUnderstandingOut } from "@/api/types";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  del: vi.fn(),
};

class ApiError extends Error {
  status: number;
  constructor(message: string, status = 500) {
    super(message);
    this.status = status;
  }
}

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
  ApiError,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

async function renderEntry(props: {
  sessionId: number;
  originKind: "qa" | "intent" | "review_item" | "inquiry";
  originId: number;
  questionText: string;
}) {
  const { JointUnderstandingEntry } = await import(
    "@/components/system-understanding/joint-understanding-entry"
  );
  return render(<JointUnderstandingEntry {...props} />, { wrapper });
}

function session(overrides: Partial<JointUnderstandingOut> = {}): JointUnderstandingOut {
  return {
    id: 7, session_id: 3, system_id: 1, origin_kind: "intent", origin_id: 11,
    trigger: "explicit_request", question_text: "この意図をどう決めるべきか分かりません",
    status: "open", outcome: null, outcome_is_provisional: false,
    outcome_reason: null, outcome_finding_ids: [], outcome_premise_state: null,
    outcome_premise_reason: null, closed_by_actor_kind: null,
    closed_by_username: null, current_origin_id: 11,
    premise_state: "current", premise_reason: null, premise_snapshot_id: 2,
    premise_commit_sha: "abc123", premise_revision_id: 4,
    premise_tracking_version: "joint-premise-v1", premise_captured_at: 1,
    schema_version: "joint-understanding-v1",
    created_at: 1, updated_at: 1, closed_at: null,
    ...overrides,
  };
}

function list(items: JointUnderstandingOut[]): JointUnderstandingListOut {
  return { session_id: 3, system_id: 1, items };
}

beforeEach(() => {
  vi.clearAllMocks();
});

test("どの起点からでも同じフローを明示的に開始できる(Issue #336)", async () => {
  mockApi.get.mockResolvedValue(list([]));
  mockApi.post.mockResolvedValue({
    session: session(), findings: [], actions: [], investigation_rounds: [],
    translations: [], reflux: [], hypothesis_adoptions: [],
    available_actions: [],
  });

  await renderEntry({
    sessionId: 3, originKind: "intent", originId: 11,
    questionText: "この意図をどう決めるべきか分かりません",
  });

  fireEvent.click(await screen.findByTestId("ju-start-intent-11"));
  await waitFor(() => {
    expect(mockApi.post).toHaveBeenCalledWith(
      "/interview/sessions/3/joint-understanding",
      {
        origin_kind: "intent",
        origin_id: 11,
        // ボタンからの開始は明示操作。「わからない」の入口だけが
        // unknown_answer を記録できる(Issue #336)。
        trigger: "explicit_request",
        question_text: "この意図をどう決めるべきか分かりません",
      },
    );
  });
});

test("既に開いている対話は作り直さず再表示する", async () => {
  mockApi.get.mockResolvedValue(list([session()]));
  await renderEntry({
    sessionId: 3, originKind: "intent", originId: 11, questionText: "x",
  });

  // 開始ボタンは出さず、既存のセッションの詳細取得へ進む。
  await waitFor(() => {
    expect(screen.queryByTestId("ju-start-intent-11")).not.toBeInTheDocument();
  });
  expect(mockApi.post).not.toHaveBeenCalledWith(
    "/interview/sessions/3/joint-understanding", expect.anything(),
  );
});

test("項目を修正しても対話を見失わない(Issue #336)", async () => {
  // interview_intent_item は加算訂正なので、修正すると項目の行 id が変わる。
  // origin_id だけで突き合わせていると、その瞬間に対話が消える。
  mockApi.get.mockResolvedValue(
    list([session({ origin_id: 11, current_origin_id: 42 })]),
  );
  await renderEntry({
    sessionId: 3, originKind: "intent", originId: 42, questionText: "x",
  });

  await waitFor(() => {
    expect(screen.queryByTestId("ju-start-intent-42")).not.toBeInTheDocument();
  });
});

test("閉じた対話は再開せず、新しく開始できる", async () => {
  mockApi.get.mockResolvedValue(
    list([session({ status: "closed", outcome: "decided" })]),
  );
  await renderEntry({
    sessionId: 3, originKind: "intent", originId: 11, questionText: "x",
  });
  // 閉じたセッションは履歴。続けるには新しく開く(#329 の terminal 契約)。
  expect(await screen.findByTestId("ju-start-intent-11")).toBeInTheDocument();
});

test("別の起点の対話を取り違えない", async () => {
  mockApi.get.mockResolvedValue(
    list([session({ origin_kind: "review_item", origin_id: 11, current_origin_id: 11 })]),
  );
  await renderEntry({
    sessionId: 3, originKind: "intent", originId: 11, questionText: "x",
  });
  expect(await screen.findByTestId("ju-start-intent-11")).toBeInTheDocument();
});
