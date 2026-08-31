/// <reference types="vitest/globals" />
// Issue #441 (Epic #436), Phase 1: turn-based voice mode.
//
// Acceptance criteria this file proves:
// AC1. A voice question can be asked in both screen scope and element scope.
// AC2. Switching target does not cause context drift in an utterance already
//      in flight.
// AC3. A microphone denial or an STT/TTS failure returns safely to text mode.
// AC4. A voice conversation never changes data directly (only the existing
//      `/assistant/ask` path is used; no audio ever leaves the browser).
//
// Mocks follow the same `vi.mock("@/api/client")` / `vi.mock("@/api/auth")`
// pattern as `assistant-discussion-thread.test.tsx` and `help-mode.test.tsx`.
// The browser Web Speech APIs are never touched: `@/lib/voice-adapter` is
// mocked so `createBrowserVoiceAdapters`/`voicePrerequisite` are fully
// test-controlled, while every finite vocabulary and message
// (`VOICE_ERROR_MESSAGES`, etc.) stays the REAL module content.

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { vi } from "vitest";
import { AssistantVoice } from "@/components/assistant-voice";
import { HelpModeProvider, useHelpMode } from "@/lib/help-mode";
import type { HelpId } from "@/lib/ui-help";
import type { VoiceErrorReason } from "@/lib/voice-adapter";
import type { AssistantDiscussionThreadDetailOut, AssistantDiscussionTurn } from "@/api/types";

// --- shared API/auth mocks (same pattern as the other assistant-panel tests) ---

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

// --- voice-adapter mock: real vocabularies/messages, test-controlled adapters ---

const voiceAdapterMocks = vi.hoisted(() => ({
  voicePrerequisite: vi.fn<() => "ready" | "insecure_context" | "unsupported">(() => "ready"),
  createBrowserVoiceAdapters: vi.fn<() => unknown>(() => null),
}));

vi.mock("@/lib/voice-adapter", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/voice-adapter")>();
  return {
    ...actual,
    voicePrerequisite: voiceAdapterMocks.voicePrerequisite,
    createBrowserVoiceAdapters: voiceAdapterMocks.createBrowserVoiceAdapters,
  };
});

interface SttHandlers {
  onResult(text: string): void;
  onError(reason: VoiceErrorReason): void;
  onEnd(): void;
}

/** A fully test-controlled STT/TTS pair -- no browser API is ever touched. */
function makeFakeAdapters() {
  let handlers: SttHandlers | null = null;
  const start = vi.fn((h: SttHandlers) => {
    handlers = h;
  });
  const stop = vi.fn();
  const speak = vi.fn(() => Promise.resolve());
  const cancel = vi.fn();
  return {
    adapters: { stt: { start, stop }, tts: { speak, cancel } },
    start,
    stop,
    speak,
    cancel,
    // Fired directly (not through Testing Library's `fireEvent`), so each
    // must be wrapped in `act()` itself -- these simulate the browser
    // Web Speech API calling back asynchronously, which React does not know
    // to batch/flush on its own.
    fireResult: (text: string) => act(() => { handlers?.onResult(text); }),
    fireError: (reason: VoiceErrorReason) => act(() => { handlers?.onError(reason); }),
    fireEnd: () => act(() => { handlers?.onEnd(); }),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  voiceAdapterMocks.voicePrerequisite.mockReturnValue("ready");
  voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(null);
});

// =====================================================================
// Part 1 -- `AssistantVoice` in isolation (fakes injected directly as a
// prop, per "Accepts the injected adapters as a prop").
// =====================================================================

/** A promise this test controls the resolution timing of, so intermediate
 * voice states (which a REAL network/TTS round trip would hold for a
 * noticeable time) are actually observable instead of racing past in a
 * single microtask flush. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("AssistantVoice (state machine)", () => {
  test("idle -> listening -> thinking -> speaking -> idle, and the target is captured at turn start", async () => {
    const fake = makeFakeAdapters();
    const answer = deferred<string | null>();
    const onTranscript = vi.fn(() => answer.promise);
    const speakGate = deferred<void>();
    fake.speak.mockReturnValue(speakGate.promise);
    const captureTurnTarget = vi.fn(() => "captured-target");

    render(
      <AssistantVoice
        captureTurnTarget={captureTurnTarget}
        onTranscript={onTranscript}
        onAdapterError={vi.fn()}
        onExit={vi.fn()}
        scopeLabel="画面全体"
        adapters={fake.adapters}
      />,
    );

    expect(screen.getByTestId("voice-state")).toHaveTextContent(
      "マイクのボタンを押して話しかけてください。",
    );

    fireEvent.click(screen.getByTestId("voice-talk"));
    expect(captureTurnTarget).toHaveBeenCalledTimes(1);
    expect(fake.start).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("voice-state")).toHaveTextContent("聞いています");

    fake.fireResult("こんにちは");
    expect(screen.getByTestId("voice-state")).toHaveTextContent("考えています");
    expect(onTranscript).toHaveBeenCalledWith("こんにちは", "captured-target");

    await act(async () => {
      answer.resolve("これが答えです");
    });
    await waitFor(() => expect(screen.getByTestId("voice-state")).toHaveTextContent("話しています"));
    expect(fake.speak).toHaveBeenCalledWith("これが答えです");

    await act(async () => {
      speakGate.resolve();
    });
    await waitFor(() =>
      expect(screen.getByTestId("voice-state")).toHaveTextContent(
        "マイクのボタンを押して話しかけてください。",
      ),
    );
  });

  test("muted: the answer is not read aloud", async () => {
    const fake = makeFakeAdapters();
    const onTranscript = vi.fn().mockResolvedValue("答え");
    render(
      <AssistantVoice
        captureTurnTarget={() => null}
        onTranscript={onTranscript}
        onAdapterError={vi.fn()}
        onExit={vi.fn()}
        scopeLabel="画面全体"
        adapters={fake.adapters}
      />,
    );
    fireEvent.click(screen.getByTestId("voice-mute"));
    expect(screen.getByTestId("voice-mute")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("質問");
    await waitFor(() =>
      expect(screen.getByTestId("voice-state")).toHaveTextContent(
        "マイクのボタンを押して話しかけてください。",
      ),
    );
    expect(fake.speak).not.toHaveBeenCalled();
  });

  test("muting while thinking suppresses the late answer", async () => {
    const fake = makeFakeAdapters();
    const answer = deferred<string | null>();
    render(
      <AssistantVoice
        captureTurnTarget={() => null}
        onTranscript={() => answer.promise}
        onAdapterError={vi.fn()}
        onExit={vi.fn()}
        scopeLabel="画面全体"
        adapters={fake.adapters}
      />,
    );
    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("質問");
    fireEvent.click(screen.getByTestId("voice-mute"));
    await act(async () => answer.resolve("遅れて返った答え"));
    await waitFor(() => expect(screen.getByTestId("voice-state")).toHaveAttribute("data-state", "idle"));
    expect(fake.speak).not.toHaveBeenCalled();
  });

  test("stop while thinking invalidates the late answer", async () => {
    const fake = makeFakeAdapters();
    const answer = deferred<string | null>();
    render(
      <AssistantVoice
        captureTurnTarget={() => null}
        onTranscript={() => answer.promise}
        onAdapterError={vi.fn()}
        onExit={vi.fn()}
        scopeLabel="画面全体"
        adapters={fake.adapters}
      />,
    );
    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("質問");
    fireEvent.click(screen.getByTestId("voice-stop"));
    await act(async () => answer.resolve("停止後の答え"));
    expect(screen.getByTestId("voice-state")).toHaveAttribute("data-state", "idle");
    expect(fake.speak).not.toHaveBeenCalled();
  });

  test("stop / mute / exit stay enabled while listening and while thinking", () => {
    const fake = makeFakeAdapters();
    // Never resolves -- keeps the component in "thinking" so the always-
    // enabled controls can be checked there too.
    const onTranscript = vi.fn(() => new Promise<string | null>(() => {}));
    render(
      <AssistantVoice
        captureTurnTarget={() => null}
        onTranscript={onTranscript}
        onAdapterError={vi.fn()}
        onExit={vi.fn()}
        scopeLabel="画面全体"
        adapters={fake.adapters}
      />,
    );

    fireEvent.click(screen.getByTestId("voice-talk"));
    expect(screen.getByTestId("voice-state")).toHaveAttribute("data-state", "listening");
    for (const id of ["voice-stop", "voice-mute", "voice-exit"]) {
      expect(screen.getByTestId(id)).not.toBeDisabled();
    }

    fake.fireResult("何かの質問");
    expect(screen.getByTestId("voice-state")).toHaveAttribute("data-state", "thinking");
    for (const id of ["voice-stop", "voice-mute", "voice-exit"]) {
      expect(screen.getByTestId(id)).not.toBeDisabled();
    }
  });

  test("exit is always available and calls onExit", () => {
    const onExit = vi.fn();
    render(
      <AssistantVoice
        captureTurnTarget={() => null}
        onTranscript={vi.fn()}
        onAdapterError={vi.fn()}
        onExit={onExit}
        scopeLabel="画面全体"
        adapters={null}
      />,
    );
    fireEvent.click(screen.getByTestId("voice-exit"));
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  test("mic permission denial and a bare STT failure show different Japanese messages", () => {
    const fake = makeFakeAdapters();
    const onAdapterError = vi.fn();
    render(
      <AssistantVoice
        captureTurnTarget={() => null}
        onTranscript={vi.fn()}
        onAdapterError={onAdapterError}
        onExit={vi.fn()}
        scopeLabel="画面全体"
        adapters={fake.adapters}
      />,
    );
    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireError("permission_denied");
    expect(onAdapterError).toHaveBeenCalledWith("permission_denied");
    expect(screen.getByTestId("voice-state")).toHaveAttribute("data-state", "error");
    expect(screen.getByTestId("voice-state").textContent).toContain("マイクの利用が許可されていません");
    expect(screen.getByTestId("voice-state").textContent).not.toContain("認識に失敗");
  });

  test("an /assistant/ask failure (not an adapter failure) returns to idle without exiting voice mode", async () => {
    const fake = makeFakeAdapters();
    const onAdapterError = vi.fn();
    const onTranscript = vi.fn().mockRejectedValue(new Error("network down"));
    render(
      <AssistantVoice
        captureTurnTarget={() => null}
        onTranscript={onTranscript}
        onAdapterError={onAdapterError}
        onExit={vi.fn()}
        scopeLabel="画面全体"
        adapters={fake.adapters}
      />,
    );
    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("質問");
    await waitFor(() =>
      expect(screen.getByTestId("voice-state")).toHaveTextContent(
        "マイクのボタンを押して話しかけてください。",
      ),
    );
    expect(onAdapterError).not.toHaveBeenCalled();
    // Still in voice mode -- the talk button is back, not exited.
    expect(screen.getByTestId("assistant-voice")).toBeTruthy();
  });

  test("the avatar does not pulse under prefers-reduced-motion, but does otherwise", () => {
    const original = window.matchMedia;
    const install = (reduced: boolean) => {
      window.matchMedia = vi.fn().mockImplementation((query: string) => ({
        matches: reduced && query.includes("reduce"),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })) as unknown as typeof window.matchMedia;
    };
    try {
      install(true);
      const { unmount } = render(
        <AssistantVoice
          captureTurnTarget={() => null}
          onTranscript={vi.fn()}
          onAdapterError={vi.fn()}
          onExit={vi.fn()}
          scopeLabel="画面全体"
          adapters={null}
        />,
      );
      expect(screen.getByTestId("voice-bot").className).not.toContain("animate-pulse");
      unmount();

      install(false);
      render(
        <AssistantVoice
          captureTurnTarget={() => null}
          onTranscript={vi.fn()}
          onAdapterError={vi.fn()}
          onExit={vi.fn()}
          scopeLabel="画面全体"
          adapters={null}
        />,
      );
      expect(screen.getByTestId("voice-bot").className).toContain("animate-pulse");
    } finally {
      window.matchMedia = original;
    }
  });

  test("state is conveyed by text, not only by data-state/colour", () => {
    render(
      <AssistantVoice
        captureTurnTarget={() => null}
        onTranscript={vi.fn()}
        onAdapterError={vi.fn()}
        onExit={vi.fn()}
        scopeLabel="要素「overview.brief」"
        adapters={null}
      />,
    );
    const region = screen.getByTestId("voice-state");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region.textContent?.length).toBeGreaterThan(0);
    expect(screen.getByTestId("voice-scope").textContent).toContain("要素「overview.brief」");
  });
});

// =====================================================================
// Part 2 -- wired into `AssistantPanel` (Issue #441 §"Wiring").
// =====================================================================

const screenContext = (screenId: string) => ({
  screen_id: screenId,
  title: screenId,
  route: `/${screenId}`,
  purpose: "テスト用画面。",
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
});

function askResponse(overrides: Record<string, unknown> = {}) {
  return {
    screen_id: "widgets",
    answer: "これが音声での回答です。",
    suggested_actions: [],
    citations: [],
    used_fallback: false,
    decision_method: "reasoning_llm" as const,
    provider: "anthropic",
    model: "test-model",
    prompt_version: "v1",
    schema_version: "v1",
    generated_at: 1_700_000_000,
    ...overrides,
  };
}

function HelpTargetControls() {
  const { setTarget, target } = useHelpMode();
  return (
    <div>
      <button data-testid="test-set-help-target" onClick={() => setTarget("overview.brief" as HelpId)}>
        set help target
      </button>
      <button data-testid="test-clear-help-target" onClick={() => setTarget(null)}>
        clear help target
      </button>
      <span data-testid="test-help-target">{target ?? "none"}</span>
    </div>
  );
}

function NavigateAway() {
  const navigate = useNavigate();
  return (
    <button data-testid="test-navigate-away" onClick={() => navigate("/elsewhere-screen")}>
      navigate away
    </button>
  );
}

async function renderPanel(route: string) {
  const { AssistantPanel } = await import("@/components/assistant-panel");
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <HelpModeProvider>
          <HelpTargetControls />
          <NavigateAway />
          <AssistantPanel />
        </HelpModeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByTestId("assistant-button"));
  await screen.findByTestId("assistant-panel");
  return view;
}

function mockGetForScreens(...screenIds: string[]) {
  mockApi.get.mockImplementation((path: string) => {
    for (const id of screenIds) {
      if (path === `/assistant/screen-context/${id}`) return Promise.resolve(screenContext(id));
    }
    return Promise.resolve(null);
  });
}

async function enterVoiceMode() {
  fireEvent.click(screen.getByTestId("assistant-voice-toggle"));
  await screen.findByTestId("assistant-voice");
}

describe("Issue #441 AC1 -- a voice question can be asked in screen scope and element scope", () => {
  test("screen scope: no element help id is sent, and the scope reads 「画面全体」", async () => {
    mockGetForScreens("widgets");
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/ask" ? Promise.resolve(askResponse()) : Promise.resolve(null),
    );

    await renderPanel("/widgets");
    await enterVoiceMode();
    expect(screen.getByTestId("voice-scope").textContent).toContain("画面全体");

    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("この画面は何をするところですか");

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      expect(calls[0][1]).toMatchObject({
        screen_id: "widgets",
        question: "この画面は何をするところですか",
      });
      expect(calls[0][1].route_params).not.toHaveProperty("voice_element_help_id");
    });
    await waitFor(() => expect(fake.speak).toHaveBeenCalledWith("これが音声での回答です。"));
  });

  test("element scope: the hovered/selected help-mode target is carried as route_params, and the scope names it", async () => {
    mockGetForScreens("widgets");
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/ask" ? Promise.resolve(askResponse()) : Promise.resolve(null),
    );

    await renderPanel("/widgets");
    fireEvent.click(screen.getByTestId("test-set-help-target"));
    await enterVoiceMode();
    expect(screen.getByTestId("voice-scope").textContent).toContain("overview.brief");

    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("この要素は何のためにありますか");

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      expect(calls[0][1].route_params).toMatchObject({ voice_element_help_id: "overview.brief" });
    });
  });
});

describe("Issue #441 AC2 -- switching target mid-utterance does not drift the in-flight question", () => {
  test("a help-mode target selected AFTER listening starts is not applied to that turn", async () => {
    mockGetForScreens("widgets");
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/ask" ? Promise.resolve(askResponse()) : Promise.resolve(null),
    );

    await renderPanel("/widgets");
    await enterVoiceMode();

    // Turn starts in SCREEN scope (no help target selected yet).
    fireEvent.click(screen.getByTestId("voice-talk"));
    // The developer's cursor now lands on an element WHILE the utterance is
    // still in flight (listening -> not yet a result).
    fireEvent.click(screen.getByTestId("test-set-help-target"));

    fake.fireResult("これは今の発話です");

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      // The turn was locked to screen scope at its start -- the later hover
      // must not leak into this request.
      expect(calls[0][1].route_params).not.toHaveProperty("voice_element_help_id");
    });
  });

  test("navigating to a different screen mid-utterance keeps asking about the screen the utterance started on", async () => {
    mockGetForScreens("widgets", "elsewhere-screen");
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/ask" ? Promise.resolve(askResponse()) : Promise.resolve(null),
    );

    await renderPanel("/widgets");
    await enterVoiceMode();
    fireEvent.click(screen.getByTestId("voice-talk"));

    fireEvent.click(screen.getByTestId("test-navigate-away"));

    fake.fireResult("移動する前に聞いた質問です");

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      expect(calls[0][1]).toMatchObject({ screen_id: "widgets" });
    });
  });

  test("switching the discussion thread scope mid-utterance keeps the turn's original thread_id", async () => {
    function turn(id: number, threadId: number, role: "user" | "assistant", content: string): AssistantDiscussionTurn {
      return {
        id, thread_id: threadId, turn_number: id, role, content, citations: [],
        target_revision_id: 1, target_digest: "d", used_fallback: false,
        decision_method: role === "user" ? "manual" : "reasoning_llm", input_mode: "text",
        provider: "anthropic", model: "m", prompt_version: "v1",
        schema_version: "assistant-discussion-turn-v1", created_by: "admin", created_at: 1,
      };
    }
    function detail(
      threadId: number, targetKind: string, targetRef: string,
    ): AssistantDiscussionThreadDetailOut {
      return {
        thread: {
          id: threadId, system_id: 1, thread_key: `ux-design-studio|entity|${targetKind}|${targetRef}`,
          scope: "entity", screen_id: "ux-design-studio",
          target_kind: targetKind as AssistantDiscussionThreadDetailOut["thread"]["target_kind"],
          target_ref: targetRef, target_title: targetRef, captured_target_revision_id: 1,
          captured_target_digest: "d", status: "open", created_by: "admin",
          created_at: 1, updated_at: 1, schema_version: "assistant-discussion-thread-v1",
        },
        target_state: "current",
        turns: [turn(1, threadId, "assistant", `${targetRef} の話`)],
      };
    }

    mockGetForScreens("ux-design-studio");
    mockApi.post.mockImplementation((path: string, body: Record<string, unknown>) => {
      if (path === "/assistant/discussion-threads") {
        return body.target_ref === "req-a"
          ? Promise.resolve(detail(11, "ux_requirement", "req-a"))
          : Promise.resolve(detail(1, "screen", "ux-design-studio"));
      }
      if (path === "/assistant/ask") return Promise.resolve(askResponse({ screen_id: "ux-design-studio" }));
      return Promise.resolve(null);
    });
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);

    await renderPanel("/ux-design-studio?tab=requirements&requirement=req-a");
    // Requirement req-a's thread (11) is the default focus target.
    await screen.findByText("req-a の話");
    await enterVoiceMode();

    fireEvent.click(screen.getByTestId("voice-talk"));
    // The developer switches the visible discussion scope WHILE the
    // utterance is in flight.
    fireEvent.click(screen.getByTestId("assistant-scope-screen"));

    fake.fireResult("今の発話はどちらの対象についてですか");

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      // Locked to the Requirement thread (11) that was active when the
      // utterance started -- NOT the screen thread (1) it was switched to.
      expect(calls[0][1]).toMatchObject({ thread_id: 11 });
    });
  });
});

describe("Issue #441 AC3 -- a microphone denial or an STT/TTS failure returns safely to text mode", () => {
  test("permission_denied exits voice mode, shows the reason, and text input works again", async () => {
    mockGetForScreens("widgets");
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);
    mockApi.post.mockImplementation(() => Promise.resolve(null));

    await renderPanel("/widgets");
    await enterVoiceMode();
    expect(screen.getByTestId("assistant-voice-toggle")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireError("permission_denied");

    await waitFor(() => expect(screen.queryByTestId("assistant-voice")).toBeNull());
    expect(screen.getByTestId("assistant-voice-toggle")).toHaveAttribute("aria-pressed", "false");
    const notice = await screen.findByTestId("voice-fallback-notice");
    expect(notice.textContent).toContain("マイクの利用が許可されていません");

    // Text mode is fully usable again.
    fireEvent.change(screen.getByTestId("assistant-question-input"), {
      target: { value: "テキストで質問します" },
    });
    expect(screen.getByTestId("assistant-question-input")).toHaveValue("テキストで質問します");
  });

  test("a TTS failure after a successful answer also exits voice mode with its own reason", async () => {
    mockGetForScreens("widgets");
    const fake = makeFakeAdapters();
    fake.speak.mockRejectedValue(new Error("tts down"));
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/ask" ? Promise.resolve(askResponse()) : Promise.resolve(null),
    );

    await renderPanel("/widgets");
    await enterVoiceMode();
    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("読み上げに失敗するはずの質問");

    const notice = await screen.findByTestId("voice-fallback-notice");
    expect(notice.textContent).toContain("音声の再生に失敗しました");
    expect(screen.getByTestId("assistant-voice-toggle")).toHaveAttribute("aria-pressed", "false");
  });

  test("the voice toggle is disabled with a stated reason when the browser cannot support voice mode", async () => {
    voiceAdapterMocks.voicePrerequisite.mockReturnValue("insecure_context");
    mockGetForScreens("widgets");
    await renderPanel("/widgets");

    expect(screen.getByTestId("assistant-voice-toggle")).toBeDisabled();
    const notice = screen.getByTestId("voice-unavailable-notice");
    expect(notice.textContent).toMatch(/マイク|HTTPS/);
  });
});

describe("Issue #441 AC4 -- a voice conversation never changes data directly", () => {
  test("a full voice turn issues only the existing /assistant/ask call (plus thread resolve, when applicable) and no other mutation", async () => {
    mockGetForScreens("widgets");
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/ask" ? Promise.resolve(askResponse()) : Promise.resolve(null),
    );

    await renderPanel("/widgets");
    await enterVoiceMode();
    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("データを変更しますか");

    await waitFor(() => {
      const askCalls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(askCalls).toHaveLength(1);
    });

    const postedPaths = mockApi.post.mock.calls.map(([path]) => path);
    expect(new Set(postedPaths)).toEqual(new Set(["/assistant/ask"]));
    expect(mockApi.put).not.toHaveBeenCalled();
    expect(mockApi.patch).not.toHaveBeenCalled();
    expect(mockApi.delete).not.toHaveBeenCalled();

    // No call anywhere carries an audio payload -- the only things that ever
    // touch "audio" are the fake adapter's own start/stop/speak/cancel.
    for (const [, body] of mockApi.post.mock.calls) {
      expect(body).not.toBeInstanceOf(FormData);
      expect(body).not.toBeInstanceOf(Blob);
      expect(body).not.toBeInstanceOf(ArrayBuffer);
    }
    expect(fake.start).toHaveBeenCalledTimes(1);
    expect(fake.speak).toHaveBeenCalledTimes(1);
  });

  test("history is retained (not cleared) after exiting voice mode", async () => {
    mockGetForScreens("widgets");
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/ask" ? Promise.resolve(askResponse({ answer: "音声で聞いた答え" })) : Promise.resolve(null),
    );

    await renderPanel("/widgets");
    await enterVoiceMode();
    // The message list is not rendered while voice mode is active.
    expect(screen.queryByTestId("assistant-message-list")).toBeNull();

    fireEvent.click(screen.getByTestId("voice-talk"));
    fake.fireResult("音声での質問");
    await waitFor(() => expect(fake.speak).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId("voice-exit"));
    await screen.findByTestId("assistant-message-list");
    expect(await screen.findByText("音声での質問")).toBeTruthy();
    expect(await screen.findByText("音声で聞いた答え")).toBeTruthy();
  });
});
