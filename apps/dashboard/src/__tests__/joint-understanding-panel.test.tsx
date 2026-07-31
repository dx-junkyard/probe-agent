/// <reference types="vitest/globals" />
// Epic #328 Phase E / Issue #333: 共同理解パネルのテスト。
//
// 1. 第1層は目的・影響中心で、内部名称(path:行)は第3層まで出さない。
// 2. 暫定採用と正式な判断が視覚的に区別される。
// 3. 有限の行動メニューが表示され、選択がサーバへ記録される。
// 4. 調査中の状態表示と、失敗時に対話が失われないこと。
// 5. 前提が古いときの警告と、mock 出力バッジ。

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { JointUnderstandingDetailOut } from "@/api/types";

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
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// 動的 import: vi.mock はファイル先頭へ巻き上げられるため、モック定義より前に
// コンポーネントを静的 import すると mockApi が初期化前に参照される。
async function renderPanel(props: { sessionId: number; juId: number }) {
  const { JointUnderstandingPanel } = await import(
    "@/components/system-understanding/joint-understanding-panel"
  );
  return render(<JointUnderstandingPanel {...props} />, { wrapper });
}

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function detail(overrides: Partial<JointUnderstandingDetailOut> = {}): JointUnderstandingDetailOut {
  const base: JointUnderstandingDetailOut = {
    session: {
      id: 7, session_id: 3, system_id: 1, origin_kind: "qa", origin_id: 5,
      trigger: "unknown_answer", question_text: "リトライ回数の根拠が分かりません",
      status: "open", outcome: null, outcome_is_provisional: false, outcome_reason: null,
      outcome_finding_ids: [], outcome_premise_state: null, premise_state: "fresh",
      premise_snapshot_id: 2, schema_version: "joint-understanding-v1",
      created_at: 1, updated_at: 1, closed_at: null,
    },
    findings: [
      {
        id: 11, joint_understanding_id: 7, system_id: 1,
        origin_role: "investigation", claim_kind: "fact",
        statement: "リトライは3回で打ち切られる",
        evidence: [{ path: "app/retry.py", start_line: 10, end_line: 20, summary: "上限定義" }],
        runtime_evidence: [], supports_finding_ids: [], competing_explanations: [],
        refutation_conditions: [], next_investigation: null, uncertainty: "",
        supersedes_finding_id: null, decision_method: "reasoning_llm",
        intelligence_run_id: 4, is_mock: false, created_at: 1,
      },
    ],
    actions: [],
    investigation_rounds: [
      {
        id: 1, joint_understanding_id: 7, system_id: 1, round_index: 1,
        status: "completed", stop_reason: "answered", conclusion: "3回です",
        search_leads: [], open_hypotheses: [], missing_evidence: [],
        read_paths: ["app/retry.py"], unread_candidates: ["app/other.py"],
        pruned_findings: 0, files_read: 1, chars_read: 100, llm_calls: 1,
        elapsed_seconds: 0.5, intelligence_run_id: 4, error_details: null, created_at: 1,
      },
    ],
    translations: [
      {
        id: 1, joint_understanding_id: 7, system_id: 1,
        purpose_summary: "外部が一時的に落ちても利用者の操作を失わないための仕組みです。",
        statements: [
          {
            layer: "impact", claim_kind: "inference",
            text: "3回失敗すると利用者には失敗として見えます",
            supports_finding_ids: [11], finding_id: 21,
          },
          {
            layer: "decision", claim_kind: "inference",
            text: "待たせるか、早く失敗を見せるかの判断です",
            supports_finding_ids: [11], finding_id: 22,
          },
        ],
        options: [
          {
            label: "現状維持", what_changes: "利用者は3回目まで待ちます",
            tradeoffs: "遅延が残ります", supports_finding_ids: [11],
          },
        ],
        open_unknowns: [], decision_question: "遅延と成功率のどちらを優先しますか?",
        ask_developer: true, intelligence_run_id: 5, is_mock: false, created_at: 2,
      },
    ],
    reflux: [],
    available_actions: [
      "request_investigation", "explain_reasoning", "compare_options",
      "adopt_hypothesis", "revise_intent", "hold", "handoff", "decide",
    ],
  };
  return { ...base, ...overrides };
}

beforeEach(() => {
  vi.clearAllMocks();
});

test("第1層は目的と影響、内部名称は第3層で開示する", async () => {
  mockApi.get.mockResolvedValue(detail());
  await renderPanel({ sessionId: 3, juId: 7 });

  await screen.findByTestId("ju-layer-purpose");
  expect(screen.getByText(/外部が一時的に落ちても/)).toBeInTheDocument();
  expect(screen.getByText(/3回失敗すると利用者には/)).toBeInTheDocument();
  // 内部名称(パス)は初期表示に出さない。
  expect(screen.queryByText(/app\/retry\.py/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByTestId("ju-toggle-evidence"));
  expect(await screen.findByTestId("ju-finding-11")).toBeInTheDocument();
  // 隠蔽ではなく段階開示: 第3層では必ず開示される。
  expect(screen.getByText(/app\/retry\.py:10-20/)).toBeInTheDocument();
  expect(screen.getByTestId("ju-claim-kind-fact")).toBeInTheDocument();
});

test("有限の行動メニューが出て、選択がサーバへ記録される", async () => {
  mockApi.get.mockResolvedValue(detail());
  mockApi.post.mockResolvedValue({
    joint_understanding_id: 7, system_id: 1, stop_reason: "answered",
    rounds: [], findings: [], error: null,
  });
  await renderPanel({ sessionId: 3, juId: 7 });

  await screen.findByTestId("ju-action-menu");
  for (const kind of [
    "request_investigation", "explain_reasoning", "compare_options",
    "adopt_hypothesis", "revise_intent", "hold", "handoff", "decide",
  ]) {
    expect(screen.getByTestId(`ju-action-${kind}`)).toBeInTheDocument();
  }

  fireEvent.click(screen.getByTestId("ju-action-request_investigation"));
  await waitFor(() => {
    expect(mockApi.post).toHaveBeenCalledWith("/joint-understanding/7/investigate", {});
  });
  await waitFor(() => {
    expect(mockApi.post).toHaveBeenCalledWith("/joint-understanding/7/actions", {
      action_kind: "request_investigation", note: null,
    });
  });
});

test("保留は行動を先に記録し、保留中のセッションを再開できる", async () => {
  mockApi.get.mockResolvedValue(detail());
  mockApi.post.mockResolvedValue({});
  await renderPanel({ sessionId: 3, juId: 7 });

  fireEvent.click(await screen.findByTestId("ju-action-hold"));
  await waitFor(() => expect(mockApi.post.mock.calls.slice(0, 2)).toEqual([
    ["/joint-understanding/7/actions", { action_kind: "hold", note: null }],
    ["/joint-understanding/7/hold", {}],
  ]));

  mockApi.get.mockResolvedValue(detail({
    session: { ...detail().session, status: "held" },
    available_actions: [],
  }));
  const held = await renderPanel({ sessionId: 3, juId: 7 });
  fireEvent.click(await screen.findByTestId("ju-resume"));
  await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
    "/joint-understanding/7/resume", {},
  ));
  held.unmount();
});

test("暫定採用は確定と視覚的に区別される", async () => {
  mockApi.get.mockResolvedValue(
    detail({
      session: {
        ...detail().session, status: "closed", outcome: "hypothesis_adopted",
        outcome_is_provisional: true, outcome_finding_ids: [11],
        outcome_premise_state: "fresh", closed_at: 9,
      },
      available_actions: [],
    }),
  );
  await renderPanel({ sessionId: 3, juId: 7 });

  const badge = await screen.findByTestId("ju-outcome-badge");
  expect(badge.textContent).toContain("仮説を暫定採用した");
  expect(badge.textContent).toContain("事実ではありません");
  expect(badge.className).toContain("amber");
  // 閉じたセッションには行動メニューを出さない。
  expect(screen.queryByTestId("ju-action-menu")).not.toBeInTheDocument();
});

test("正式な判断は暫定と異なる見た目になる", async () => {
  mockApi.get.mockResolvedValue(
    detail({
      session: {
        ...detail().session, status: "closed", outcome: "decided",
        outcome_is_provisional: false, outcome_finding_ids: [11],
        outcome_premise_state: "fresh", closed_at: 9,
      },
      available_actions: [],
    }),
  );
  await renderPanel({ sessionId: 3, juId: 7 });

  const badge = await screen.findByTestId("ju-outcome-badge");
  expect(badge.textContent).toContain("正式に判断した");
  expect(badge.textContent).not.toContain("暫定");
  expect(badge.className).toContain("emerald");
});

test("前提が古いときは警告し、理由層を先出しする", async () => {
  mockApi.get.mockResolvedValue(
    detail({ session: { ...detail().session, premise_state: "stale" } }),
  );
  await renderPanel({ sessionId: 3, juId: 7 });

  expect(await screen.findByTestId("ju-premise-stale")).toBeInTheDocument();
  // 例外があるときは「理由を見る」を押さなくても第2層を表示する。
  expect(screen.getByTestId("ju-layer-reasons")).toBeInTheDocument();
  expect(screen.getByTestId("ju-decision-question")).toBeInTheDocument();
});

test("調査に失敗しても対話は残り、回答機会を失わせない", async () => {
  mockApi.get.mockResolvedValue(detail());
  mockApi.post.mockRejectedValue(new ApiError(502, "investigation failed"));
  await renderPanel({ sessionId: 3, juId: 7 });

  await screen.findByTestId("ju-action-menu");
  fireEvent.click(screen.getByTestId("ju-action-request_investigation"));

  await waitFor(() => expect(mockApi.post).toHaveBeenCalled());
  // パネルも行動メニューも残ったまま。
  expect(screen.getByTestId("joint-understanding-panel")).toBeInTheDocument();
  expect(screen.getByTestId("ju-action-request_investigation")).toBeInTheDocument();
});

test("mock LLM 出力にはバッジが付く", async () => {
  const base = detail();
  mockApi.get.mockResolvedValue(
    detail({ findings: [{ ...base.findings[0], is_mock: true }] }),
  );
  await renderPanel({ sessionId: 3, juId: 7 });

  fireEvent.click(await screen.findByTestId("ju-toggle-evidence"));
  expect(await screen.findByTestId("ju-mock-badge")).toBeInTheDocument();
});

test("runtime evidence は根拠層で追跡できる", async () => {
  const base = detail();
  mockApi.get.mockResolvedValue(detail({ findings: [{
    ...base.findings[0], evidence: [], runtime_evidence: [{
      component_id: "retry-worker", runtime_check: "match", summary: "3回を観測",
    }],
  }] }));
  await renderPanel({ sessionId: 3, juId: 7 });

  fireEvent.click(await screen.findByTestId("ju-toggle-evidence"));
  expect(await screen.findByText(/runtime:retry-worker/)).toBeInTheDocument();
});

test("未翻訳のときは次に取るべき操作を日本語で案内する", async () => {
  mockApi.get.mockResolvedValue(detail({ translations: [] }));
  await renderPanel({ sessionId: 3, juId: 7 });

  const notice = await screen.findByTestId("ju-not-translated");
  expect(notice.textContent).toContain("まだ説明は作られていません");
  // 未翻訳でもメニューはローカルの日本語ラベルで出る(英語混在なし)。
  expect(screen.getByTestId("ju-action-compare_options").textContent).toBe("選択肢を比較する");
});
