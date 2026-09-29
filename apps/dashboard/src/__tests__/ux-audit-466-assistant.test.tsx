/// <reference types="vitest/globals" />
// Issue #466 — アシスタントパネル: UX-07 (履歴読込前の送信で発言が消えない),
// UX-16 (新規の回答・処理中・失敗だけを読み上げに通知する), UX-17 (広い画面
// では本文と並ぶ非モーダルなパネル), UX-20 (複数行入力と IME)。

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
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
    isAdmin: true,
    loading: false,
    systemId: 1,
    systems: [{ id: 1, name: "probe-agent" }],
    login: vi.fn(),
    logout: vi.fn(),
    selectSystem: vi.fn(),
    refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

const screenContext = {
  screen_id: "ux-design-studio",
  title: "UX Design Studio",
  route: "/ux-design-studio",
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

function historyTurn(id: number, role: "user" | "assistant", content: string) {
  return {
    id, thread_id: 11, turn_number: id, role, content, citations: [], target_revision_id: 7,
    target_digest: "digest-a", used_fallback: false,
    decision_method: role === "user" ? ("manual" as const) : ("reasoning_llm" as const),
    input_mode: "text" as const, provider: "anthropic", model: "test-model", prompt_version: "v1",
    schema_version: "assistant-discussion-turn-v1", created_by: "admin", created_at: 1_700_000_000 + id,
  };
}

const askResponse = {
  screen_id: "ux-design-studio",
  answer: "この Requirement の受入条件は 2 件です。",
  suggested_actions: [], citations: [], used_fallback: false,
  decision_method: "reasoning_llm" as const, provider: "anthropic", model: "test-model",
  prompt_version: "v1", schema_version: "v1", generated_at: 1_700_000_500,
  thread_id: 11, target_state: "current" as const, recheck_required: false, turn_number: 2,
};

async function renderPanel(route = "/ux-design-studio?tab=requirements&requirement=req-a") {
  const { AssistantPanel } = await import("@/components/assistant-panel");
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <div data-testid="page-body"><input aria-label="本文の入力" /></div>
        <AssistantPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByTestId("assistant-button"));
  await screen.findByTestId("assistant-panel");
  return view;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.get.mockImplementation((path: string) =>
    path.startsWith("/assistant/screen-context/") ? Promise.resolve(screenContext) : Promise.resolve(null),
  );
});

afterEach(() => {
  // matchMedia を差し替えたケースを元に戻す。
  delete (window as { matchMedia?: unknown }).matchMedia;
});

// ── UX-07 ───────────────────────────────────────────────────────────

describe("UX-07: 履歴の初回解決中に送った発言が消えない", () => {
  test("解決中は送信を待たせ、入力は残す。解決後に送った発言は永続履歴と一緒に表示される", async () => {
    let resolveThread: (value: AssistantDiscussionThreadDetailOut) => void = () => {};
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") {
        return new Promise((resolve) => { resolveThread = resolve; });
      }
      if (path === "/assistant/ask") return Promise.resolve(askResponse);
      return Promise.resolve(null);
    });
    await renderPanel();

    expect(await screen.findByTestId("assistant-thread-resolving")).toBeInTheDocument();
    const input = screen.getByTestId("assistant-question-input");
    fireEvent.change(input, { target: { value: "受入条件は?" } });
    expect(screen.getByTestId("assistant-send")).toBeDisabled();
    // Enter でも送らない。
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mockApi.post).not.toHaveBeenCalledWith("/assistant/ask", expect.anything());
    expect(input).toHaveValue("受入条件は?");

    await act(async () => {
      resolveThread(threadDetail([historyTurn(1, "user", "前回の質問"), historyTurn(2, "assistant", "前回の回答")]));
    });
    await screen.findByText("前回の回答");
    expect(screen.queryByTestId("assistant-thread-resolving")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("assistant-send"));
    await screen.findByText(askResponse.answer);
    expect(screen.getByText("受入条件は?")).toBeInTheDocument();
    expect(screen.getByText("前回の回答")).toBeInTheDocument();
    const askCalls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
    expect(askCalls).toHaveLength(1);
    expect(askCalls[0][1]).toMatchObject({ thread_id: 11 });
  });

  test("履歴の取得に失敗したら保存されない会話だと明示し、送った発言は消えない", async () => {
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") return Promise.reject(new Error("offline"));
      if (path === "/assistant/ask") return Promise.resolve({ ...askResponse, thread_id: undefined });
      return Promise.resolve(null);
    });
    await renderPanel();
    expect(await screen.findByTestId("assistant-thread-unsaved")).toHaveTextContent("この会話は保存されません");
    fireEvent.change(screen.getByTestId("assistant-question-input"), { target: { value: "一時の質問" } });
    fireEvent.click(screen.getByTestId("assistant-send"));
    await screen.findByText(askResponse.answer);
    expect(screen.getByText("一時の質問")).toBeInTheDocument();
  });
});

// ── UX-16 ───────────────────────────────────────────────────────────

describe("UX-16: 回答到着・処理中・失敗を読み上げに通知する", () => {
  test("履歴の読み込みでは通知せず、新しい回答だけを通知する", async () => {
    let resolveAsk: (v: typeof askResponse) => void = () => {};
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") {
        return Promise.resolve(threadDetail([historyTurn(1, "assistant", "過去の長い回答")]));
      }
      if (path === "/assistant/ask") return new Promise((resolve) => { resolveAsk = resolve; });
      return Promise.resolve(null);
    });
    await renderPanel();
    await screen.findByText("過去の長い回答");
    const status = screen.getByTestId("assistant-live-status");
    expect(status).toHaveAttribute("role", "status");
    expect(status).toHaveAttribute("aria-live", "polite");
    // 履歴全件は通知しない。
    expect(status).toHaveTextContent("");
    expect(status).not.toHaveTextContent("過去の長い回答");

    fireEvent.change(screen.getByTestId("assistant-question-input"), { target: { value: "質問" } });
    fireEvent.click(screen.getByTestId("assistant-send"));
    await waitFor(() => expect(status).toHaveTextContent("回答を作成しています"));
    await act(async () => { resolveAsk(askResponse); });
    await waitFor(() => expect(status).toHaveTextContent(`回答が届きました。${askResponse.answer}`));
  });

  test("失敗は alert で通知する", async () => {
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") return Promise.resolve(threadDetail());
      if (path === "/assistant/ask") return Promise.reject(new Error("upstream down"));
      return Promise.resolve(null);
    });
    await renderPanel();
    await waitFor(() => expect(screen.queryByTestId("assistant-thread-resolving")).not.toBeInTheDocument());
    fireEvent.change(screen.getByTestId("assistant-question-input"), { target: { value: "質問" } });
    fireEvent.click(screen.getByTestId("assistant-send"));
    const alert = screen.getByTestId("assistant-live-alert");
    await waitFor(() => expect(alert).toHaveTextContent("回答を取得できませんでした"));
    expect(alert).toHaveAttribute("role", "alert");
  });
});

// ── UX-17 ───────────────────────────────────────────────────────────

describe("UX-17: 広い画面では本文と並ぶ非モーダルなパネル", () => {
  function stubMatchMedia(matches: boolean) {
    const listeners = new Set<() => void>();
    const state = { matches };
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: () => ({
        get matches() { return state.matches; },
        addEventListener: (_: string, l: () => void) => listeners.add(l),
        removeEventListener: (_: string, l: () => void) => listeners.delete(l),
      }),
    });
    return {
      set(next: boolean) {
        state.matches = next;
        listeners.forEach((l) => l());
      },
    };
  }

  test("背景もフォーカストラップも無く、本文を操作しながら回答と入力を保てる", async () => {
    stubMatchMedia(true);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/discussion-threads" ? Promise.resolve(threadDetail()) : Promise.resolve(askResponse));
    await renderPanel();
    const panel = screen.getByTestId("assistant-panel");
    expect(panel).toHaveAttribute("data-layout", "side");
    expect(panel).toHaveAttribute("role", "complementary");
    expect(panel).not.toHaveAttribute("aria-modal");
    expect(screen.queryByTestId("assistant-backdrop")).not.toBeInTheDocument();
    expect(screen.getByTestId("assistant-question-input")).toHaveFocus();

    fireEvent.change(screen.getByTestId("assistant-question-input"), { target: { value: "書きかけの質問" } });
    // 本文の入力へ移動しても、Tab がパネルへ引き戻されない。
    const bodyInput = screen.getByLabelText("本文の入力");
    bodyInput.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(bodyInput).toHaveFocus();
    fireEvent.change(bodyInput, { target: { value: "本文の編集" } });
    expect(screen.getByTestId("assistant-panel")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-question-input")).toHaveValue("書きかけの質問");
  });

  test("狭い画面へ切り替わるとモーダルに戻り、入力を保つ", async () => {
    const media = stubMatchMedia(true);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/discussion-threads" ? Promise.resolve(threadDetail()) : Promise.resolve(askResponse));
    await renderPanel();
    fireEvent.change(screen.getByTestId("assistant-question-input"), { target: { value: "保持される質問" } });
    await act(async () => { media.set(false); });
    const panel = screen.getByTestId("assistant-panel");
    expect(panel).toHaveAttribute("role", "dialog");
    expect(panel).toHaveAttribute("aria-modal", "true");
    expect(screen.getByTestId("assistant-backdrop")).toBeInTheDocument();
    expect(screen.getByTestId("assistant-question-input")).toHaveValue("保持される質問");
    expect(panel.contains(document.activeElement)).toBe(true);
  });

  test("並べて表示中の Escape はパネル内にフォーカスがあるときだけ閉じる", async () => {
    stubMatchMedia(true);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/discussion-threads" ? Promise.resolve(threadDetail()) : Promise.resolve(askResponse));
    await renderPanel();
    // 本文側での Escape では閉じない。
    const bodyInput = screen.getByLabelText("本文の入力");
    bodyInput.focus();
    fireEvent.keyDown(bodyInput, { key: "Escape" });
    expect(screen.getByTestId("assistant-panel")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByTestId("assistant-question-input"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("assistant-panel")).not.toBeInTheDocument());
  });
});

// ── UX-20 ───────────────────────────────────────────────────────────

describe("UX-20: 複数行の相談を編集できる入力欄", () => {
  beforeEach(() => {
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/discussion-threads" ? Promise.resolve(threadDetail()) : Promise.resolve(askResponse));
  });

  test("明示ラベル付きの複数行入力で、送信キーの説明が関連付いている", async () => {
    await renderPanel();
    const input = screen.getByLabelText("質問");
    expect(input.tagName).toBe("TEXTAREA");
    expect(input).toHaveAccessibleDescription("Enter で送信、Shift+Enter で改行");
  });

  test("Shift+Enter は改行 (送信しない)、IME 変換中の Enter も送信しない、Enter で送信する", async () => {
    await renderPanel();
    await waitFor(() => expect(screen.queryByTestId("assistant-thread-resolving")).not.toBeInTheDocument());
    const input = screen.getByTestId("assistant-question-input");
    const multiline = "条件:\n- A の場合\n- B の場合\n```py\nprint(1)\n```";
    fireEvent.change(input, { target: { value: multiline } });

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(mockApi.post).not.toHaveBeenCalledWith("/assistant/ask", expect.anything());
    expect(input).toHaveValue(multiline);

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/assistant/ask",
      expect.objectContaining({ question: multiline }),
    ));
    // 送信した複数行はそのまま会話に残る。
    const bubble = await screen.findByText((_, el) => el?.tagName === "P" && el.textContent === multiline);
    expect(within(bubble.parentElement!).getByText((_, el) => el === bubble)).toBeInTheDocument();
  });
});
