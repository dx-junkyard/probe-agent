/// <reference types="vitest/globals" />
// Issue #445 (Epic #443 Phase 2): UI draft wiring in the Assistant Panel.
//
// Acceptance criteria this file proves on the Dashboard side:
// 1. A mounted form's registered draft is sent alongside a text question
//    about the SAME target, and only the fields the adapter allowlists.
// 2. The draft is captured at turn start, not read again when the request
//    is actually sent -- proven with a voice turn, which has a real gap
//    between "turn start" (mic press) and "request sent" (STT resolves),
//    matching `assistant-voice.test.tsx`'s own "captured at turn start"
//    pattern for the discussion target itself.
// 3. A target whose adapter has no `ui_draft_forms` shows a canonical-only
//    disclosure instead of silently omitting one, and never sends `ui_draft`.
//
// Mocks follow the same `vi.mock("@/api/client")` pattern as the sibling
// assistant-panel test files.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { useState } from "react";
import { vi } from "vitest";
import { UiDraftProvider, useUiDraftSource } from "@/lib/ui-draft";
import type { AssistantDiscussionThreadDetailOut, AssistantDiscussionTurn } from "@/api/types";
import type { VoiceErrorReason } from "@/lib/voice-adapter";

const mockApi = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(),
  postBlob: vi.fn(),
}));

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

function makeFakeAdapters() {
  let handlers: SttHandlers | null = null;
  return {
    adapters: {
      stt: {
        start: vi.fn((h: SttHandlers) => { handlers = h; }),
        stop: vi.fn(),
      },
      tts: { speak: vi.fn(() => Promise.resolve()), cancel: vi.fn() },
    },
    fireResult: (text: string) => handlers?.onResult(text),
  };
}

function screenContext(screenId: string) {
  return {
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
  };
}

function turn(id: number, threadId: number, role: "user" | "assistant", content: string): AssistantDiscussionTurn {
  return {
    id, thread_id: threadId, turn_number: id, role, content, citations: [],
    target_revision_id: 1, target_digest: "d", used_fallback: false,
    decision_method: role === "user" ? "manual" : "reasoning_llm", input_mode: "text",
    provider: "anthropic", model: "m", prompt_version: "v1",
    schema_version: "assistant-discussion-turn-v1", created_by: "admin", created_at: id,
  };
}

function threadDetail(
  threadId: number, targetKind: string, targetRef: string, screenId: string, scope = "entity",
): AssistantDiscussionThreadDetailOut {
  return {
    thread: {
      id: threadId, system_id: 1, thread_key: `${screenId}|${scope}|${targetKind}|${targetRef}`,
      scope: scope as AssistantDiscussionThreadDetailOut["thread"]["scope"],
      screen_id: screenId,
      target_kind: targetKind as AssistantDiscussionThreadDetailOut["thread"]["target_kind"],
      target_ref: targetRef, target_title: targetRef, captured_target_revision_id: 1,
      captured_target_digest: "d", status: "open", created_by: "admin",
      created_at: 1, updated_at: 1, schema_version: "assistant-discussion-thread-v1",
    },
    target_state: "current",
    turns: [turn(1, threadId, "assistant", `${targetRef} の話`)],
  };
}

function askResponse(overrides: Record<string, unknown> = {}) {
  return {
    screen_id: "ux-design-studio",
    answer: "回答です。",
    suggested_actions: [],
    citations: [],
    used_fallback: false,
    decision_method: "reasoning_llm" as const,
    provider: "anthropic",
    model: "test-model",
    prompt_version: "v1",
    schema_version: "v1",
    generated_at: 1_700_000_000,
    ui_draft_state: "not_provided" as const,
    ui_draft_changed: false,
    ...overrides,
  };
}

/** Mirrors `journey-panel.tsx`'s `JourneyRevisionForm` shape closely enough
 * to exercise the real wiring: registers a `ux_journey.revision` draft whose
 * title can be changed from the test via the rendered input. */
function FakeJourneyForm({ journeyKey }: { journeyKey: string }) {
  const [title, setTitle] = useState("draft title");
  useUiDraftSource("ux_journey.revision", journeyKey, () => ({
    fields: [{ fieldName: "title", value: title, dirty: true, validationError: "" }],
    selectedItemRef: "",
    activeTab: "",
    comparisonTarget: "",
    localRevisionToken: title,
  }));
  return (
    <input
      data-testid="fake-journey-title"
      value={title}
      onChange={(e) => setTitle(e.target.value)}
    />
  );
}

function ThrowingJourneyForm({ journeyKey }: { journeyKey: string }) {
  useUiDraftSource("ux_journey.revision", journeyKey, () => {
    throw new Error("form state could not be read");
  });
  return <div data-testid="throwing-journey-form" />;
}

async function renderWithForm(route: string, form: React.ReactNode) {
  const { AssistantPanel } = await import("@/components/assistant-panel");
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <UiDraftProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[route]}>
          {form}
          <AssistantPanel />
        </MemoryRouter>
      </QueryClientProvider>
    </UiDraftProvider>,
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

beforeEach(() => {
  vi.clearAllMocks();
  voiceAdapterMocks.voicePrerequisite.mockReturnValue("ready");
  voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(null);
});

describe("Issue #445 -- a mounted form's draft is sent with a text question about the same target", () => {
  test("ui_draft carries the registered field, formId, and target", async () => {
    mockGetForScreens("ux-design-studio");
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") {
        return Promise.resolve(threadDetail(11, "ux_journey", "jny-1", "ux-design-studio"));
      }
      if (path === "/assistant/ask") return Promise.resolve(askResponse({ ui_draft_state: "applied" }));
      return Promise.resolve(null);
    });

    await renderWithForm(
      "/ux-design-studio?tab=journeys&journey=jny-1",
      <FakeJourneyForm journeyKey="jny-1" />,
    );
    await screen.findByText("jny-1 の話");

    fireEvent.change(screen.getByTestId("assistant-question-input"), {
      target: { value: "このJourneyの下書きについて" },
    });
    fireEvent.click(screen.getByTestId("assistant-send"));

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      expect(calls[0][1]).toMatchObject({
        thread_id: 11,
        ui_draft: {
          target_kind: "ux_journey",
          target_ref: "jny-1",
          form_id: "ux_journey.revision",
          fields: [{ field_name: "title", value: "draft title", dirty: true, validation_error: "" }],
        },
      });
    });
  });

  test("the answer's ui_draft citation renders a short disclosure line", async () => {
    mockGetForScreens("ux-design-studio");
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") {
        return Promise.resolve(threadDetail(11, "ux_journey", "jny-1", "ux-design-studio"));
      }
      if (path === "/assistant/ask") {
        return Promise.resolve(
          askResponse({
            ui_draft_state: "applied",
            citations: [{ type: "ui_draft", id: "ui_draft:ux_journey.revision", title: "未保存の下書き", detail: "" }],
          }),
        );
      }
      return Promise.resolve(null);
    });

    await renderWithForm(
      "/ux-design-studio?tab=journeys&journey=jny-1",
      <FakeJourneyForm journeyKey="jny-1" />,
    );
    await screen.findByText("jny-1 の話");
    fireEvent.change(screen.getByTestId("assistant-question-input"), { target: { value: "質問" } });
    fireEvent.click(screen.getByTestId("assistant-send"));

    await screen.findByTestId("assistant-used-ui-draft");
  });
});

describe("Issue #445 §2.6 -- a form that cannot be read is `unreadable`, not silence", () => {
  test("a throwing draft getter sends readable:false with no fields", async () => {
    // Omitting `ui_draft` here would tell the server no form was open, which
    // is a different fact from "a form is open but could not be read" -- the
    // distinction §2.6 exists to keep. No draft content is fabricated.
    mockGetForScreens("ux-design-studio");
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") {
        return Promise.resolve(threadDetail(11, "ux_journey", "jny-1", "ux-design-studio"));
      }
      if (path === "/assistant/ask") {
        return Promise.resolve(askResponse({ ui_draft_state: "unreadable" }));
      }
      return Promise.resolve(null);
    });

    await renderWithForm(
      "/ux-design-studio?tab=journeys&journey=jny-1",
      <ThrowingJourneyForm journeyKey="jny-1" />,
    );
    await screen.findByText("jny-1 の話");

    fireEvent.change(screen.getByTestId("assistant-question-input"), {
      target: { value: "下書きについて" },
    });
    fireEvent.click(screen.getByTestId("assistant-send"));

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      expect(calls[0][1].ui_draft).toMatchObject({
        target_kind: "ux_journey",
        target_ref: "jny-1",
        form_id: "ux_journey.revision",
        readable: false,
        fields: [],
      });
    });
  });
});

describe("Issue #445 §2.5 -- the draft is captured at turn start, not re-read when the request is sent", () => {
  test("editing the form after the mic is pressed does not change what a voice turn sends", async () => {
    mockGetForScreens("ux-design-studio");
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") {
        return Promise.resolve(threadDetail(11, "ux_journey", "jny-1", "ux-design-studio"));
      }
      if (path === "/assistant/ask") return Promise.resolve(askResponse());
      return Promise.resolve(null);
    });
    const fake = makeFakeAdapters();
    voiceAdapterMocks.createBrowserVoiceAdapters.mockReturnValue(fake.adapters);

    await renderWithForm(
      "/ux-design-studio?tab=journeys&journey=jny-1",
      <FakeJourneyForm journeyKey="jny-1" />,
    );
    await screen.findByText("jny-1 の話");

    fireEvent.click(screen.getByTestId("assistant-voice-toggle"));
    await screen.findByTestId("assistant-voice");
    fireEvent.click(screen.getByTestId("voice-talk"));

    // The developer keeps typing in the form WHILE the utterance is still
    // being recognized -- this must not change what the in-flight turn asks.
    fireEvent.change(screen.getByTestId("fake-journey-title"), {
      target: { value: "changed after mic press" },
    });

    fake.fireResult("この下書きについて教えて");

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      expect(calls[0][1]).toMatchObject({
        ui_draft: {
          fields: [{ field_name: "title", value: "draft title", dirty: true, validation_error: "" }],
        },
      });
    });
  });
});

describe("Issue #445 §2.6 -- adapter 未対応画面では canonical-only であることを明示する", () => {
  test("a target with no registered ui_draft form shows a disclosure and sends no ui_draft", async () => {
    mockGetForScreens("interview");
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/assistant/discussion-threads") {
        return Promise.resolve(threadDetail(21, "interview_session", "7", "interview"));
      }
      if (path === "/assistant/ask") return Promise.resolve(askResponse({ screen_id: "interview", ui_draft_state: "unsupported" }));
      return Promise.resolve(null);
    });

    await renderWithForm("/interview?session=7", null);
    await screen.findByText("7 の話");

    await screen.findByTestId("assistant-ui-draft-unsupported");

    fireEvent.change(screen.getByTestId("assistant-question-input"), { target: { value: "質問" } });
    fireEvent.click(screen.getByTestId("assistant-send"));

    await waitFor(() => {
      const calls = mockApi.post.mock.calls.filter(([path]) => path === "/assistant/ask");
      expect(calls).toHaveLength(1);
      expect(calls[0][1]).not.toHaveProperty("ui_draft");
    });
  });
});
