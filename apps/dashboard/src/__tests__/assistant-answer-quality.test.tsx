/// <reference types="vitest/globals" />
// Epic #467 (#468/#470/#471/#472): 根拠付き回答・失敗と回復・参照範囲・
// クライアント計測の表示。

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { vi } from "vitest";
import type { AssistantDiscussionThreadDetailOut } from "@/api/types";

const mockApi = { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: () => {},
  ApiError: class extends Error {},
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", role: "admin" },
    isAdmin: true, loading: false, systemId: 1,
    systems: [{ id: 1, name: "probe-agent" }],
    login: vi.fn(), logout: vi.fn(), selectSystem: vi.fn(), refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

const screenContext = {
  screen_id: "ux-design-studio", title: "UX Design Studio", route: "/ux-design-studio",
  purpose: "設計成果物を追跡する。",
  primary_data_sources: [], visible_sections: [], common_questions: [], related_settings: [],
  related_checks: [], related_pipeline_steps: [], related_endpoints: [],
  state_severity: "ok" as const, screen_checks: [], suggested_questions: [],
};

function threadDetail(turns: AssistantDiscussionThreadDetailOut["turns"] = []): AssistantDiscussionThreadDetailOut {
  return {
    thread: {
      id: 11, system_id: 1, thread_key: "ux-design-studio|entity|ux_requirement|req-a",
      scope: "entity", screen_id: "ux-design-studio", target_kind: "ux_requirement",
      target_ref: "req-a", target_title: "req-a", captured_target_revision_id: 7,
      captured_target_digest: "digest-a", status: "open", created_by: "admin",
      created_at: 1_700_000_000, updated_at: 1_700_000_100,
      schema_version: "assistant-discussion-thread-v1",
    },
    target_state: "current",
    turns,
  };
}

function historyTurn(id: number, role: "user" | "assistant", content: string, extra: Record<string, unknown> = {}) {
  return {
    id, thread_id: 11, turn_number: id, role, content, citations: [], target_revision_id: 7,
    target_digest: "digest-a", used_fallback: false,
    decision_method: role === "user" ? ("manual" as const) : ("reasoning_llm" as const),
    input_mode: "text" as const, provider: "anthropic", model: "test-model", prompt_version: "v2",
    schema_version: "assistant-discussion-turn-v1", created_by: "admin", created_at: 1_700_000_000 + id,
    ...extra,
  } as AssistantDiscussionThreadDetailOut["turns"][number];
}

let seq = 0;
function baseAsk(over: Record<string, unknown> = {}) {
  seq += 1;
  return {
    screen_id: "ux-design-studio", answer: "回答本文", suggested_actions: [], citations: [],
    used_fallback: false, decision_method: "reasoning_llm" as const, provider: "anthropic",
    model: "test-model", prompt_version: "v2", schema_version: "v2", generated_at: 1_700_000_500,
    thread_id: 11, target_state: "current" as const, recheck_required: false, turn_number: 2,
    request_id: `req-${seq}`,
    ...over,
  };
}

const groundedAsk = () => baseAsk({
  conclusion: "受入条件は 2 件です。",
  answer: "受入条件は 2 件です。",
  citations: [{ type: "screen_data", id: "criteria", title: "受入条件一覧", detail: "" }],
  points: [
    { kind: "fact", text: "受入条件は 2 件登録されている。", sources: [{ type: "screen_data", id: "criteria" }], grounding: "supported" },
    { kind: "interpretation", text: "追加の条件を検討できる。", sources: [], grounding: "not_required" },
    { kind: "fact", text: "前回の確認から変わっていない。", sources: [{ type: "screen_data", id: "criteria" }], grounding: "stale" },
    { kind: "fact", text: "本番で 100 件処理された。", sources: [], grounding: "unsupported" },
    { kind: "unknown", text: "利用者数は不明。", sources: [], grounding: "not_required" },
  ],
  missing_information: [{ what: "利用者数", how_to_get: "Trace 一覧で確認する" }],
  grounding_state: "partially_grounded",
  answer_status: "answered",
});

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname}</div>;
}

async function renderPanel(turns: AssistantDiscussionThreadDetailOut["turns"] = []) {
  mockApi.get.mockImplementation((path: string) =>
    path.startsWith("/assistant/screen-context/") ? Promise.resolve(screenContext) : Promise.resolve(null),
  );
  const { AssistantPanel } = await import("@/components/assistant-panel");
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const askMock = (impl: () => unknown) => {
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") return Promise.resolve(threadDetail(turns));
      if (path === "/assistant/ask") return Promise.resolve(impl());
      return Promise.resolve({});
    });
  };
  return { AssistantPanel, qc, askMock, LocationProbe };
}

async function open(ctx: Awaited<ReturnType<typeof renderPanel>>) {
  const { AssistantPanel, qc } = ctx;
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/ux-design-studio?tab=requirements&requirement=req-a"]}>
        <LocationProbe />
        <AssistantPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByTestId("assistant-button"));
  await screen.findByTestId("assistant-panel");
  await waitFor(() => expect(screen.queryByTestId("assistant-thread-resolving")).not.toBeInTheDocument());
}

async function send(text = "受入条件は?") {
  fireEvent.change(screen.getByTestId("assistant-question-input"), { target: { value: text } });
  fireEvent.click(screen.getByTestId("assistant-send"));
}

const askCalls = () => mockApi.post.mock.calls.filter(([p]) => p === "/assistant/ask");
const timingCalls = () => mockApi.post.mock.calls.filter(([p]) => String(p).endsWith("/client-timing"));

beforeEach(() => vi.clearAllMocks());

describe("#471: 根拠付き回答", () => {
  test("結論と、根拠の状態ごとに分けた項目を表示する", async () => {
    const ctx = await renderPanel();
    ctx.askMock(groundedAsk);
    await open(ctx);
    await send();
    expect(await screen.findByTestId("assistant-conclusion")).toHaveTextContent("受入条件は 2 件です。");
    const facts = screen.getByTestId("assistant-points-facts");
    expect(within(facts).getByText("確認済みの事実")).toBeInTheDocument();
    expect(within(facts).getByText(/受入条件は 2 件登録/)).toBeInTheDocument();
    expect(within(facts).getByText("受入条件一覧")).toBeInTheDocument();
    expect(within(screen.getByTestId("assistant-points-interpretations")).getByText("解釈・提案")).toBeInTheDocument();
    expect(within(screen.getByTestId("assistant-points-stale")).getByText(/古い根拠に基づく内容（再確認が必要）/)).toBeInTheDocument();
    const unsupported = screen.getByTestId("assistant-points-unsupported");
    expect(within(unsupported).getByText(/根拠を確認できなかった主張/)).toBeInTheDocument();
    expect(unsupported).toHaveTextContent("【未確認】");
    // 根拠のない主張を確認済みの事実に入れない。
    expect(facts).not.toHaveTextContent("100 件処理");
    expect(within(screen.getByTestId("assistant-points-unknowns")).getByText(/利用者数は不明/)).toBeInTheDocument();
    const missing = screen.getByTestId("assistant-missing-information");
    expect(missing).toHaveTextContent("不足している情報");
    expect(missing).toHaveTextContent("利用者数");
    expect(missing).toHaveTextContent("Trace 一覧で確認する");
    expect(screen.getByTestId("assistant-grounding-banner")).toHaveTextContent("一部は、根拠を確認できませんでした");
  });

  test("ungrounded は根拠を確認できていない旨の帯を出す", async () => {
    const ctx = await renderPanel();
    ctx.askMock(() => baseAsk({ conclusion: "たぶんそうです。", grounding_state: "ungrounded", points: [], answer_status: "answered" }));
    await open(ctx);
    await send();
    expect(await screen.findByTestId("assistant-grounding-banner")).toHaveTextContent("根拠を確認できていません");
  });

  test("古いサーバーの応答(新フィールドなし)は answer を表示し、落ちない", async () => {
    const ctx = await renderPanel();
    ctx.askMock(() => {
      const r = baseAsk();
      delete (r as Record<string, unknown>).request_id;
      return r;
    });
    await open(ctx);
    await send();
    expect(await screen.findByText("回答本文")).toBeInTheDocument();
    expect(screen.queryByTestId("assistant-structured-answer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("assistant-failure-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("assistant-scope")).not.toBeInTheDocument();
    expect(screen.getByText(/reasoning_llm · test-model/)).toBeInTheDocument();
  });
});

describe("#472: 失敗と回復", () => {
  const failedAsk = () => baseAsk({
    answer_status: "failed", used_fallback: true, answer: "LLM に接続できませんでした。",
    failure: {
      failure_class: "timeout", message: "LLM の応答がタイムアウトしました。", retryable: true,
      recovery: [
        { kind: "retry", label: "もう一度試す", target: null },
        { kind: "rephrase", label: "質問を言い換える", target: null },
        { kind: "open_settings", label: "設定を開く", target: "ASSISTANT_LLM_TIMEOUT" },
      ],
    },
  });

  test("失敗カードを出し、通常の回答としては表示しない。再試行は同じ質問を 1 回だけ送る", async () => {
    const ctx = await renderPanel();
    ctx.askMock(failedAsk);
    await open(ctx);
    await send("この Requirement は?");
    const card = await screen.findByTestId("assistant-failure-card");
    expect(card).toHaveTextContent("LLM の応答がタイムアウトしました。");
    expect(screen.queryByText(/reasoning_llm/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("assistant-fallback-badge")).not.toBeInTheDocument();
    expect(screen.queryByTestId("assistant-conclusion")).not.toBeInTheDocument();
    expect(askCalls()).toHaveLength(1);

    // 二重クリックでも 1 件だけ。
    const retry = screen.getByTestId("assistant-recovery-retry");
    await act(async () => {
      fireEvent.click(retry);
      fireEvent.click(retry);
    });
    await waitFor(() => expect(askCalls()).toHaveLength(2));
    expect(askCalls()[1][1]).toMatchObject({ question: "この Requirement は?", thread_id: 11 });
    await new Promise((r) => setTimeout(r, 30));
    expect(askCalls()).toHaveLength(2);
  });

  test("設定を開くは /settings へ移動し、設定キーを示す。言い換えは質問を入力欄へ戻す", async () => {
    const ctx = await renderPanel();
    ctx.askMock(failedAsk);
    await open(ctx);
    await send("元の質問");
    await screen.findByTestId("assistant-failure-card");
    expect(screen.getByTestId("assistant-recovery-key")).toHaveTextContent("ASSISTANT_LLM_TIMEOUT");
    fireEvent.click(screen.getByTestId("assistant-recovery-rephrase"));
    expect(screen.getByTestId("assistant-question-input")).toHaveValue("元の質問");
    fireEvent.click(screen.getByTestId("assistant-recovery-open_settings"));
    expect(screen.getByTestId("loc")).toHaveTextContent("/settings");
  });

  test("定型回答は LLM を使えなかった旨と本文を表示する", async () => {
    const ctx = await renderPanel();
    ctx.askMock(() => baseAsk({
      answer_status: "deterministic_answer", used_fallback: true, decision_method: "deterministic",
      answer: "この画面は設計成果物を追跡します。",
      failure: { failure_class: "provider_not_configured", message: "LLM が設定されていません。", retryable: false,
        recovery: [{ kind: "configure", label: "LLM を設定する", target: "/settings" }] },
    }));
    await open(ctx);
    await send();
    const notice = await screen.findByTestId("assistant-deterministic-notice");
    expect(notice).toHaveTextContent("LLM を使用できなかったため");
    expect(notice).toHaveTextContent("LLM が設定されていません。");
    expect(screen.getByText("この画面は設計成果物を追跡します。")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-fallback-badge")).toHaveTextContent("LLM を使わない定型回答");
    fireEvent.click(screen.getByTestId("assistant-recovery-configure"));
    expect(screen.getByTestId("loc")).toHaveTextContent("/settings");
  });
});

describe("#470: 参照範囲", () => {
  const metric = (over: Record<string, unknown> = {}) => ({
    request_id: "x", screen_id: "s", input_mode: "text", started_at: 1, finished_at: 2,
    outcome: "answered", provider: "a", model: "m", prompt_version: "v2", schema_version: "v2",
    stage_timings: { llm: { ms: 812.4, count: 1 } }, counters: {}, input_sizes: {}, usage: {},
    manifest: {
      sources: [{ type: "screen_data", id: "criteria", freshness: "current" }],
      coverage: [
        { section: "requirements", state: "truncated", returned: 50, total: null, limit: 50 },
        { section: "steps", state: "truncated", returned: 50, total: 60, limit: 50 },
      ],
      omitted_sections: [{ section: "related_context", reason: "budget", dropped_items: 3 }],
      history_turns: 4,
      budget: { limit_chars: 60000, used_chars: 61000, over_budget: true },
    },
    created_at: 1, ...over,
  });

  test("開くまで取得せず、開くと範囲・省略・所要時間を表示する(総数不明を 0 にしない)", async () => {
    const ctx = await renderPanel();
    ctx.askMock(() => baseAsk({ request_id: "req-scope" }));
    await open(ctx);
    mockApi.get.mockImplementation((path: string) => {
      if (path.startsWith("/assistant/screen-context/")) return Promise.resolve(screenContext);
      if (path === "/assistant/ask-metrics/req-scope") return Promise.resolve(metric());
      return Promise.resolve(null);
    });
    await send();
    await screen.findByTestId("assistant-scope");
    expect(mockApi.get).not.toHaveBeenCalledWith("/assistant/ask-metrics/req-scope");
    fireEvent.click(screen.getByText("参照範囲"));
    const body = await screen.findByTestId("assistant-scope-body");
    const cov = within(body).getAllByTestId("assistant-scope-coverage");
    expect(cov[0]).toHaveTextContent("上限 50 件で切り詰め（総数不明");
    expect(cov[1]).toHaveTextContent("上限 50 件で切り詰め（全 60 件");
    expect(within(body).getByTestId("assistant-scope-omitted")).toHaveTextContent("入力の上限のため省略（3 件）");
    expect(within(body).getByTestId("assistant-scope-over-budget")).toBeInTheDocument();
    expect(within(body).getByTestId("assistant-scope-stage")).toHaveTextContent("LLM 呼び出し: 812 ms");
    expect(within(body).getByTestId("assistant-scope-source")).toHaveTextContent("画面データ");
    expect(mockApi.get.mock.calls.filter(([p]) => p === "/assistant/ask-metrics/req-scope")).toHaveLength(1);
  });

  test("取得失敗は空ではなく失敗として表示し、再試行できる", async () => {
    const ctx = await renderPanel();
    ctx.askMock(() => baseAsk({ request_id: "req-scope-err" }));
    await open(ctx);
    let fail = true;
    mockApi.get.mockImplementation((path: string) => {
      if (path.startsWith("/assistant/screen-context/")) return Promise.resolve(screenContext);
      if (path === "/assistant/ask-metrics/req-scope-err") {
        return fail ? Promise.reject(new Error("boom")) : Promise.resolve(metric());
      }
      return Promise.resolve(null);
    });
    await send();
    fireEvent.click(await screen.findByText("参照範囲"));
    expect(await screen.findByTestId("assistant-scope-error")).toHaveTextContent("参照範囲を取得できませんでした");
    expect(screen.queryByTestId("assistant-scope-body")).not.toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByTestId("assistant-scope-retry"));
    expect(await screen.findByTestId("assistant-scope-body")).toBeInTheDocument();
  });
});

describe("#468: クライアント計測", () => {
  test("request_id があれば 1 回だけ数値で送る。409 は無視する", async () => {
    const ctx = await renderPanel();
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") return Promise.resolve(threadDetail());
      if (path === "/assistant/ask") return Promise.resolve(baseAsk({ request_id: "req-timing" }));
      if (path.endsWith("/client-timing")) return Promise.reject(Object.assign(new Error("409"), { status: 409 }));
      return Promise.resolve({});
    });
    await open(ctx);
    await send();
    await screen.findByText("回答本文");
    await waitFor(() => expect(timingCalls()).toHaveLength(1));
    const [path, body] = timingCalls()[0];
    expect(path).toBe("/assistant/ask-metrics/req-timing/client-timing");
    expect(typeof body.first_visible_ms).toBe("number");
    expect(typeof body.complete_ms).toBe("number");
    expect(body.first_visible_ms).toBeGreaterThanOrEqual(0);
    // 409 でも画面にエラーを出さない。
    expect(screen.queryByTestId("assistant-error")).not.toBeInTheDocument();
    expect(timingCalls()).toHaveLength(1);
  });

  test("request_id がなければ送らない", async () => {
    const ctx = await renderPanel();
    ctx.askMock(() => {
      const r = baseAsk();
      delete (r as Record<string, unknown>).request_id;
      return r;
    });
    await open(ctx);
    await send();
    await screen.findByText("回答本文");
    await new Promise((r) => setTimeout(r, 20));
    expect(timingCalls()).toHaveLength(0);
  });
});

describe("履歴の再現", () => {
  test("answer_structure がある回答は構造で、NULL は保存済み本文で表示する", async () => {
    const structured = historyTurn(2, "assistant", "受入条件は 2 件です。", {
      answer_structure: {
        conclusion: "受入条件は 2 件です。",
        points: [{ kind: "fact", text: "2 件登録されている。", sources: [{ type: "screen_data", id: "criteria" }], grounding: "supported" }],
        missing_information: [], grounding_state: "grounded", answer_status: "answered", failure_class: null,
      },
    });
    const plain = historyTurn(4, "assistant", "旧形式の本文", { answer_structure: null });
    const ctx = await renderPanel([historyTurn(1, "user", "Q1"), structured, historyTurn(3, "user", "Q2"), plain]);
    ctx.askMock(() => baseAsk());
    await open(ctx);
    expect(await screen.findByTestId("assistant-conclusion")).toHaveTextContent("受入条件は 2 件です。");
    expect(within(screen.getByTestId("assistant-points-facts")).getByText(/2 件登録されている/)).toBeInTheDocument();
    expect(screen.getByText("旧形式の本文")).toBeInTheDocument();
    // 履歴には request_id がないので参照範囲は出さない。
    expect(screen.queryByTestId("assistant-scope")).not.toBeInTheDocument();
    expect(timingCalls()).toHaveLength(0);
  });

  test("失敗した履歴の回答は失敗カードで、回復ボタンは出さない", async () => {
    const failed = historyTurn(2, "assistant", "LLM の応答がタイムアウトしました。", {
      answer_structure: { points: [], missing_information: [], answer_status: "failed", failure_class: "timeout" },
    });
    const ctx = await renderPanel([historyTurn(1, "user", "Q1"), failed]);
    ctx.askMock(() => baseAsk());
    await open(ctx);
    const card = await screen.findByTestId("assistant-failure-card");
    expect(card).toHaveTextContent("LLM の応答がタイムアウトしました。");
    expect(screen.getByTestId("assistant-failure-readonly")).toBeInTheDocument();
    expect(screen.queryByTestId("assistant-recovery")).not.toBeInTheDocument();
  });
});
