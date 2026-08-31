/// <reference types="vitest/globals" />
// Issue #440 (Epic #436): the UI help-mode overlay.
//
// Acceptance criteria this file proves:
// 1. The main elements of a screen can be explained -- hovering / focusing /
//    tapping a `data-help-id` element while help mode is active fetches and
//    shows its explanation.
// 2. Each explanation shows its documentation source (`doc_refs`).
// 3. After leaving the mode, normal operation is fully restored -- no
//    lingering listeners, no swallowed clicks.
// 4. A target can still be chosen on a device that cannot hover (tap/click,
//    and keyboard focus).
//
// Self-contained mocks following the same `vi.mock("@/api/client")` /
// `vi.mock("@/api/auth")` pattern as layout-navigation.test.tsx.

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import type { UiHelpEntry } from "@/api/types";

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
  setSystemId: () => {},
  ApiError,
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

function entryFixture(helpId: string, overrides: Partial<UiHelpEntry> = {}): UiHelpEntry {
  return {
    help_id: helpId,
    screen_id: "overview",
    scope: helpId.split(".").length > 1 ? "element" : "screen",
    title: `title:${helpId}`,
    summary: `summary:${helpId}`,
    usage: `usage:${helpId}`,
    doc_refs: [{ doc_path: "docs/system-understanding-navigation.md", title: `doc:${helpId}`, anchor: "" }],
    related_actions: [],
    related_help_ids: [],
    registry_version: "ui-help-v1",
    decision_method: "deterministic",
    ...overrides,
  };
}

// The test page: a normal button with no help annotation nested INSIDE an
// annotated section (so closest() must climb to the section), and a button
// that carries its OWN help id nested inside that same section (so closest()
// must stop at the nearest ancestor, not the outer one).
function TestPage({ onNormalClick, onNestedClick }: { onNormalClick: () => void; onNestedClick: () => void }) {
  return (
    <div data-help-id="overview" data-testid="screen-root">
      <div data-help-id="overview.brief" data-testid="brief-section">
        <button data-testid="normal-button" onClick={onNormalClick}>
          通常のボタン
        </button>
        <button data-testid="nested-button" data-help-id="overview.brief.vision" onClick={onNestedClick}>
          Vision
        </button>
      </div>
    </div>
  );
}

async function renderApp(onNormalClick: () => void, onNestedClick: () => void) {
  const { AppLayout } = await import("@/components/layout/app-layout");
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route
              index
              element={<TestPage onNormalClick={onNormalClick} onNestedClick={onNestedClick} />}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByTestId("screen-root");
  return result;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.get.mockImplementation((url: string) => {
    if (url.startsWith("/assistant/ui-help/overview.brief.vision")) {
      return Promise.resolve(entryFixture("overview.brief.vision"));
    }
    if (url.startsWith("/assistant/ui-help/overview")) {
      return Promise.resolve(entryFixture("overview"));
    }
    return Promise.resolve(null);
  });
});

function toggleHelpMode() {
  fireEvent.click(screen.getByTestId("help-mode-toggle"));
}

describe("Help mode toggle (Issue #440)", () => {
  test("the header button toggles aria-pressed and shows/hides the overlay", async () => {
    await renderApp(vi.fn(), vi.fn());

    const toggle = screen.getByTestId("help-mode-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("help-mode-layer")).toBeNull();

    toggleHelpMode();
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("help-mode-layer")).toBeTruthy();

    // Re-clicking exits.
    toggleHelpMode();
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("help-mode-layer")).toBeNull();
  });

  test("Escape exits help mode", async () => {
    await renderApp(vi.fn(), vi.fn());
    toggleHelpMode();
    expect(screen.getByTestId("help-mode-layer")).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.getByTestId("help-mode-toggle")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("help-mode-layer")).toBeNull();
  });

  test("the explicit 「解説モードを終了」 control exits help mode", async () => {
    await renderApp(vi.fn(), vi.fn());
    toggleHelpMode();
    fireEvent.click(screen.getByTestId("help-mode-exit"));
    expect(screen.getByTestId("help-mode-toggle")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("help-mode-layer")).toBeNull();
  });
});

describe("Selecting and explaining a target (Issue #440 AC1/AC2/AC4)", () => {
  test("tap/click selects the nearest data-help-id target and shows its explanation with doc sources", async () => {
    const onNormalClick = vi.fn();
    const onNestedClick = vi.fn();
    await renderApp(onNormalClick, onNestedClick);
    toggleHelpMode();

    // Tap/click works even on a device that cannot hover (AC4).
    fireEvent.click(screen.getByTestId("nested-button"));

    await waitFor(() => expect(screen.getByTestId("help-mode-entry")).toBeTruthy());
    expect(screen.getByTestId("help-mode-entry")).toHaveAttribute("data-help-scope", "element");
    expect(screen.getByText("title:overview.brief.vision")).toBeTruthy();
    expect(screen.getByText("summary:overview.brief.vision")).toBeTruthy();
    expect(screen.getByText("usage:overview.brief.vision")).toBeTruthy();
    // AC2: the documentation source is visible, including its path.
    const docRefs = screen.getByTestId("help-mode-doc-refs");
    expect(docRefs.textContent).toContain("doc:overview.brief.vision");
    expect(docRefs.textContent).toContain("docs/system-understanding-navigation.md");

    // The element's own normal action (its onClick) must NOT have fired.
    expect(onNestedClick).not.toHaveBeenCalled();
  });

  test("clicking a plain descendant resolves to the closest ANCESTOR help target", async () => {
    const onNormalClick = vi.fn();
    await renderApp(onNormalClick, vi.fn());
    toggleHelpMode();

    fireEvent.click(screen.getByTestId("normal-button"));

    await waitFor(() => expect(screen.getByTestId("help-mode-entry")).toBeTruthy());
    // `normal-button` has no data-help-id of its own; closest() must climb to
    // the surrounding section (`overview.brief`), not skip past it to the
    // outer screen root (`overview`).
    expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining("/assistant/ui-help/overview.brief"));
    expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining("/assistant/ui-help/overview.brief.vision"));

    // And the element's normal action was suppressed while help mode is active.
    expect(onNormalClick).not.toHaveBeenCalled();
  });

  test("keyboard focus selects a target immediately (no hover required)", async () => {
    await renderApp(vi.fn(), vi.fn());
    toggleHelpMode();

    const nested = screen.getByTestId("nested-button");
    fireEvent.focusIn(nested);

    await waitFor(() =>
      expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining("/assistant/ui-help/overview.brief.vision")),
    );
  });

  test("hovering debounces: a later hover before the delay elapses wins", async () => {
    // Mount under REAL timers first -- Testing Library's async helpers used
    // by `renderApp` rely on them. Fake timers are switched on only for the
    // debounce assertions themselves.
    await renderApp(vi.fn(), vi.fn());
    toggleHelpMode();
    vi.useFakeTimers();
    try {
      fireEvent.mouseOver(screen.getByTestId("screen-root"));
      // Before the debounce elapses, hover moves to the nested target.
      act(() => {
        vi.advanceTimersByTime(50);
      });
      fireEvent.mouseOver(screen.getByTestId("nested-button"));
      act(() => {
        vi.advanceTimersByTime(200);
      });

      // Only the final hover target's fetch should have been requested --
      // the first (screen root) debounce was cancelled, never fired.
      expect(mockApi.get).not.toHaveBeenCalledWith("/assistant/ui-help/overview");
      expect(mockApi.get).toHaveBeenCalledWith(
        expect.stringContaining("/assistant/ui-help/overview.brief.vision"),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  test("aria-live announces the explanation region", async () => {
    await renderApp(vi.fn(), vi.fn());
    toggleHelpMode();
    const panel = screen.getByTestId("help-mode-panel");
    expect(panel).toHaveAttribute("aria-live", "polite");
  });
});

describe("Full restoration after exit (Issue #440 AC3)", () => {
  test("after exiting, a click on a data-help-id element runs its own handler again", async () => {
    const onNestedClick = vi.fn();
    await renderApp(vi.fn(), onNestedClick);

    toggleHelpMode();
    fireEvent.click(screen.getByTestId("nested-button"));
    expect(onNestedClick).not.toHaveBeenCalled();

    // Exit help mode.
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("help-mode-layer")).toBeNull();

    // Normal operation is fully restored: the same click now runs the
    // element's own handler.
    fireEvent.click(screen.getByTestId("nested-button"));
    expect(onNestedClick).toHaveBeenCalledTimes(1);
  });

  test("no explanation fetch happens outside help mode", async () => {
    await renderApp(vi.fn(), vi.fn());
    fireEvent.mouseOver(screen.getByTestId("nested-button"));
    fireEvent.click(screen.getByTestId("nested-button"));
    expect(mockApi.get).not.toHaveBeenCalledWith(expect.stringContaining("/assistant/ui-help/"));
  });
});
