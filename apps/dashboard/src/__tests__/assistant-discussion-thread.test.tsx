/// <reference types="vitest/globals" />
// Issue #438 (Epic #436): target- and revision-scoped discussion threads.
//
// Acceptance criteria this file proves on the Dashboard side:
// 1. Requirement A/B and Journey Step A/B conversations never mix -- the
//    thread is resolved from the canonical target identity, and switching
//    target neither shows nor sends the other target's turns.
// 2. A target's thread is restored after a reload (a fresh mount renders the
//    persisted turns the server returns).
// 3. Turns recorded before the target's content moved are not treated as
//    current fact -- the panel says so, and the client stops sending its own
//    `conversation` because the server decides what is inheritable (§1.3).
//
// Mocks follow the `vi.mock("@/api/client")` pattern the other panel tests
// use, so nothing here touches a real network or a real QueryClient cache.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type {
  AssistantDiscussionThreadDetailOut,
  AssistantDiscussionTurn,
  DiscussionTargetState,
} from "@/api/types";

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
  primary_data_sources: [],
  visible_sections: [],
  common_questions: [],
  related_settings: [],
  related_checks: [],
  related_pipeline_steps: [],
  related_endpoints: [],
  state_severity: "ok" as const,
  screen_checks: [],
  suggested_questions: [],
};

function turn(
  id: number,
  threadId: number,
  role: "user" | "assistant",
  content: string,
): AssistantDiscussionTurn {
  return {
    id,
    thread_id: threadId,
    turn_number: id,
    role,
    content,
    citations: [],
    target_revision_id: 7,
    target_digest: "digest-a",
    used_fallback: false,
    decision_method: role === "user" ? "manual" : "reasoning_llm",
    input_mode: "text",
    provider: "anthropic",
    model: "test-model",
    prompt_version: "v1",
    schema_version: "assistant-discussion-turn-v1",
    created_by: "admin",
    created_at: 1_700_000_000 + id,
  };
}

function threadDetail(
  threadId: number,
  targetKind: string,
  targetRef: string,
  turns: AssistantDiscussionTurn[],
  targetState: DiscussionTargetState = "current",
): AssistantDiscussionThreadDetailOut {
  return {
    thread: {
      id: threadId,
      system_id: 1,
      thread_key: `ux-design-studio|entity|${targetKind}|${targetRef}`,
      scope: targetKind === "ux_journey_step" ? "element" : "entity",
      screen_id: "ux-design-studio",
      target_kind: targetKind as AssistantDiscussionThreadDetailOut["thread"]["target_kind"],
      target_ref: targetRef,
      target_title: targetRef,
      captured_target_revision_id: 7,
      captured_target_digest: "digest-a",
      status: "open",
      created_by: "admin",
      created_at: 1_700_000_000,
      updated_at: 1_700_000_100,
      schema_version: "assistant-discussion-thread-v1",
    },
    target_state: targetState,
    turns,
  };
}

const askResponse = {
  screen_id: "ux-design-studio",
  answer: "この Requirement の受入条件は 2 件です。",
  suggested_actions: [],
  citations: [],
  used_fallback: false,
  decision_method: "reasoning_llm" as const,
  provider: "anthropic",
  model: "test-model",
  prompt_version: "v1",
  schema_version: "v1",
  generated_at: 1_700_000_500,
  thread_id: 11,
  target_state: "current" as const,
  recheck_required: false,
  turn_number: 2,
};

/**
 * Serve the two Requirement threads keyed by the POSTed target, so a wrong
 * thread lookup shows up as wrong content rather than as a passing test.
 */
function mockThreads(byRef: Record<string, AssistantDiscussionThreadDetailOut>, ask = askResponse) {
  mockApi.get.mockImplementation((path: string) =>
    path.startsWith("/assistant/screen-context/")
      ? Promise.resolve(screenContext)
      : Promise.resolve(null),
  );
  mockApi.post.mockImplementation((path: string, body: Record<string, unknown>) => {
    if (path === "/assistant/discussion-threads") {
      const detail = byRef[String(body.target_ref)];
      return detail ? Promise.resolve(detail) : Promise.resolve(null);
    }
    if (path === "/assistant/ask") return Promise.resolve(ask);
    return Promise.resolve(null);
  });
}

async function renderPanelAt(route: string) {
  const { AssistantPanel } = await import("@/components/assistant-panel");
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <AssistantPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByTestId("assistant-button"));
  await screen.findByTestId("assistant-panel");
  return view;
}

describe("Issue #438 — target-scoped discussion threads", () => {
  beforeEach(() => vi.clearAllMocks());

  test("the thread is resolved from the canonical target identity, not the screen id", async () => {
    mockThreads({
      "req-a": threadDetail(11, "ux_requirement", "req-a", []),
    });
    await renderPanelAt("/ux-design-studio?tab=requirements&requirement=req-a");

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(
        ([path]) => path === "/assistant/discussion-threads",
      );
      expect(calls.length).toBeGreaterThan(0);
      expect(calls[0][1]).toEqual({
        scope: "entity",
        screen_id: "ux-design-studio",
        target_kind: "ux_requirement",
        target_ref: "req-a",
      });
    });
  });

  test("Requirement A の履歴が Requirement B のコンテキストへ混入しない", async () => {
    mockThreads({
      "req-a": threadDetail(11, "ux_requirement", "req-a", [
        turn(1, 11, "user", "A の境界はどこですか"),
        turn(2, 11, "assistant", "A の答えです"),
      ]),
      "req-b": threadDetail(12, "ux_requirement", "req-b", [
        turn(1, 12, "user", "B について"),
      ]),
    });
    const { unmount } = await renderPanelAt(
      "/ux-design-studio?tab=requirements&requirement=req-a",
    );
    await screen.findByText("A の答えです");
    unmount();

    // The other Requirement's panel shows ITS thread and nothing of A's.
    await renderPanelAt("/ux-design-studio?tab=requirements&requirement=req-b");
    await screen.findByText("B について");
    expect(screen.queryByText("A の答えです")).toBeNull();
    expect(screen.queryByText("A の境界はどこですか")).toBeNull();
  });

  test("Journey Step A/B も別 thread として解決される", async () => {
    mockThreads({
      "jny-1#step-a": threadDetail(21, "ux_journey_step", "jny-1#step-a", [
        turn(1, 21, "assistant", "step A の整理"),
      ]),
      "jny-1#step-b": threadDetail(22, "ux_journey_step", "jny-1#step-b", [
        turn(1, 22, "assistant", "step B の整理"),
      ]),
    });
    const { unmount } = await renderPanelAt(
      "/ux-design-studio?tab=journeys&journey=jny-1&step=step-a",
    );
    await screen.findByText("step A の整理");
    unmount();

    await renderPanelAt("/ux-design-studio?tab=journeys&journey=jny-1&step=step-b");
    await screen.findByText("step B の整理");
    expect(screen.queryByText("step A の整理")).toBeNull();
  });

  test("reload 後に対象 thread の turn を復元する", async () => {
    mockThreads({
      "req-a": threadDetail(11, "ux_requirement", "req-a", [
        turn(1, 11, "user", "前回の質問"),
        turn(2, 11, "assistant", "前回の回答"),
      ]),
    });
    // A fresh mount is what a reload is, from this component's point of view:
    // no client state survives, so everything visible came from the server.
    await renderPanelAt("/ux-design-studio?tab=requirements&requirement=req-a");
    await screen.findByText("前回の質問");
    await screen.findByText("前回の回答");
  });

  test("thread があるときは conversation を送らず thread_id を送る", async () => {
    mockThreads({
      "req-a": threadDetail(11, "ux_requirement", "req-a", [
        turn(1, 11, "user", "前回の質問"),
        turn(2, 11, "assistant", "前回の回答"),
      ]),
    });
    await renderPanelAt("/ux-design-studio?tab=requirements&requirement=req-a");
    await screen.findByText("前回の回答");

    fireEvent.change(screen.getByTestId("assistant-question-input"), {
      target: { value: "では不足は何ですか" },
    });
    fireEvent.click(screen.getByTestId("assistant-send"));

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      // The bounded context is derived server-side from the persisted turns;
      // sending our own would be a second source of truth (422 server-side).
      expect(calls[0][1]).toEqual(
        expect.objectContaining({ thread_id: 11, conversation: [] }),
      );
    });
  });

  test("revision が動いた thread の旧 turn は current fact として扱われない", async () => {
    mockThreads(
      {
        "req-a": threadDetail(
          11,
          "ux_requirement",
          "req-a",
          [turn(1, 11, "user", "古い質問"), turn(2, 11, "assistant", "古い回答")],
          "stale",
        ),
      },
    );
    await renderPanelAt("/ux-design-studio?tab=requirements&requirement=req-a");

    // History stays readable...
    await screen.findByText("古い回答");
    // ...but the panel says it is history, not a current premise.
    const banner = await screen.findByTestId("assistant-target-stale");
    expect(banner.textContent).toContain("履歴として残ります");
  });

  test("対象の切り替え操作は会話の identity を切り替える", async () => {
    mockThreads({
      "req-a": threadDetail(11, "ux_requirement", "req-a", [
        turn(1, 11, "assistant", "Requirement の話"),
      ]),
      "ux-design-studio": threadDetail(1, "screen", "ux-design-studio", [
        turn(1, 1, "assistant", "画面全体の話"),
      ]),
    });
    await renderPanelAt("/ux-design-studio?tab=requirements&requirement=req-a");
    await screen.findByText("Requirement の話");

    fireEvent.click(screen.getByTestId("assistant-scope-screen"));
    await screen.findByText("画面全体の話");
    expect(screen.queryByText("Requirement の話")).toBeNull();
  });

  test("対象を切り替えた瞬間に旧対象の turn は消える (新 thread の解決を待たない)", async () => {
    // The window between "the target changed" and "the new thread arrived" is
    // where a carry-over would actually be visible. Hold the second thread's
    // response open so that window stays open, and assert the previous
    // target's conversation is already gone inside it.
    let releaseScreenThread: (() => void) | null = null;
    const screenThreadPending = new Promise<AssistantDiscussionThreadDetailOut>((resolve) => {
      releaseScreenThread = () =>
        resolve(
          threadDetail(1, "screen", "ux-design-studio", [
            turn(1, 1, "assistant", "画面全体の話"),
          ]),
        );
    });
    mockApi.get.mockImplementation((path: string) =>
      path.startsWith("/assistant/screen-context/")
        ? Promise.resolve(screenContext)
        : Promise.resolve(null),
    );
    mockApi.post.mockImplementation((path: string, body: Record<string, unknown>) => {
      if (path === "/assistant/discussion-threads") {
        return body.target_ref === "req-a"
          ? Promise.resolve(
              threadDetail(11, "ux_requirement", "req-a", [
                turn(1, 11, "assistant", "Requirement の話"),
              ]),
            )
          : screenThreadPending;
      }
      return Promise.resolve(null);
    });

    await renderPanelAt("/ux-design-studio?tab=requirements&requirement=req-a");
    await screen.findByText("Requirement の話");

    fireEvent.click(screen.getByTestId("assistant-scope-screen"));
    await waitFor(() => {
      expect(screen.queryByText("Requirement の話")).toBeNull();
    });

    releaseScreenThread!();
    await screen.findByText("画面全体の話");
  });

  test("thread endpoint が落ちてもアシスタントは従来どおり動く", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path.startsWith("/assistant/screen-context/")
        ? Promise.resolve(screenContext)
        : Promise.resolve(null),
    );
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") return Promise.reject(new Error("boom"));
      if (path === "/assistant/ask") return Promise.resolve({ ...askResponse, thread_id: null });
      return Promise.resolve(null);
    });
    await renderPanelAt("/ux-design-studio?tab=requirements&requirement=req-a");

    fireEvent.change(screen.getByTestId("assistant-question-input"), {
      target: { value: "この画面は何ですか" },
    });
    fireEvent.click(screen.getByTestId("assistant-send"));
    await screen.findByTestId("assistant-answer");

    const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
    expect(calls[0][1]).not.toHaveProperty("thread_id");
  });
});
