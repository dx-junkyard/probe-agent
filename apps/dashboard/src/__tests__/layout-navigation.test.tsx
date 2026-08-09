/// <reference types="vitest/globals" />
// Issue #362: below the `md` breakpoint the sidebar must not take horizontal
// space from <main>; it becomes an overlay Drawer opened from a header menu
// button, while `md` and above keep today's static rail with its
// expand/collapse chevron.
//
// jsdom does not evaluate Tailwind media queries, so "responsiveness" is
// verified two ways here: (a) the responsive class names actually present on
// the rail, the toggle, and <main> (that is what decides the layout in a real
// browser), and (b) real behaviour that does not depend on CSS at all --
// opening/closing, Escape, focus management, and navigation.
//
// Self-contained mocks following the same `vi.mock("@/api/client")` /
// `vi.mock("@/api/auth")` pattern as phase0-empty-states.test.tsx.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

let mockSystemId: number | null = 1;
let mockSystems: { id: number; name: string; environment?: string }[] = [
  { id: 1, name: "probe-agent" },
];
let mockUser: { id: number; username: string; role: string } | null = {
  id: 1, username: "admin", role: "admin",
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
  getSystemId: () => mockSystemId,
  setSystemId: (id: number | null) => { mockSystemId = id; },
  ApiError,
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({
    user: mockUser,
    isAdmin: mockUser?.role === "admin",
    loading: false,
    systemId: mockSystemId,
    systems: mockSystems,
    login: vi.fn(),
    logout: vi.fn(),
    selectSystem: vi.fn(),
    refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: ReactNode }) => children,
}));

// Records the viewport the case describes. jsdom never applies it to CSS --
// it exists so each case states which of the issue's three widths (390 /
// 768 / 1280) it stands for, and so nothing in the tree reads a stale width.
function setViewport(width: number) {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true, writable: true });
  window.dispatchEvent(new Event("resize"));
}

async function renderLayout(initialPath = "/") {
  const { AppLayout } = await import("@/components/layout/app-layout");
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<div data-testid="page-overview">overview</div>} />
            <Route path="settings" element={<div data-testid="page-settings">settings</div>} />
            <Route path="interview" element={<div data-testid="page-interview">interview</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // The rail always renders, in every viewport.
  await screen.findByTestId("sidebar-rail");
  return result;
}

function openDrawer() {
  fireEvent.click(screen.getByTestId("mobile-nav-toggle"));
  return screen.getByTestId("mobile-nav-drawer");
}

function focusablesIn(panel: HTMLElement) {
  return Array.from(
    panel.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSystemId = 1;
  mockSystems = [{ id: 1, name: "probe-agent" }];
  mockUser = { id: 1, username: "admin", role: "admin" };
  mockApi.get.mockResolvedValue(null);
  setViewport(1280);
});

// ── Layout: the rail contributes no width below md ──────────────────

describe("AppLayout responsive shell (Issue #362)", () => {
  test("the static rail is hidden below md and <main> shrinks its padding", async () => {
    setViewport(390);
    await renderLayout();

    const rail = screen.getByTestId("sidebar-rail");
    // `hidden md:flex`: display:none below md, so it occupies no horizontal
    // space at 390px; the flex rail comes back at md and above.
    expect(rail.className).toMatch(/(^|\s)hidden(\s|$)/);
    expect(rail.className).toMatch(/md:flex/);

    const main = document.querySelector("main");
    expect(main).not.toBeNull();
    expect(main!.className).toMatch(/(^|\s)p-4(\s|$)/);
    expect(main!.className).toMatch(/md:p-6/);
    // Issue #102 / #358: the assistant's floating button is fixed at the
    // bottom-right over this scroll area, so the content must end above it.
    // This padding and the button's `bottom-*` are one pair -- changing
    // either alone lets the button cover a page's primary action.
    expect(main!.className).toMatch(/(^|\s)pb-24(\s|$)/);
    expect(main!.className).toMatch(/md:pb-24/);
  });

  test("the mobile menu toggle is present with an accessible Japanese name and is md:hidden", async () => {
    await renderLayout();

    const toggle = screen.getByTestId("mobile-nav-toggle");
    expect(toggle).toHaveAttribute("aria-label", "ナビゲーションを開く");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    // The Drawer is unmounted while closed, so `aria-controls` must not point
    // at an id that is not in the document.
    expect(toggle).not.toHaveAttribute("aria-controls");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-controls", "mobile-nav-drawer");
    expect(document.getElementById("mobile-nav-drawer")).not.toBeNull();
    // At md and above the static rail is always there, so the toggle is hidden.
    expect(toggle.className).toMatch(/md:hidden/);
  });
});

// ── 390px: the Drawer ───────────────────────────────────────────────

describe("Mobile navigation Drawer at 390px (Issue #362)", () => {
  test("is absent on first render and opens from the menu button with the same nav items", async () => {
    setViewport(390);
    await renderLayout();

    expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();
    expect(screen.queryByTestId("mobile-nav-backdrop")).not.toBeInTheDocument();

    const drawer = openDrawer();
    expect(drawer).toHaveAttribute("role", "dialog");
    expect(drawer).toHaveAttribute("aria-modal", "true");
    expect(drawer).toHaveAttribute("aria-label", "ナビゲーション");
    expect(drawer.id).toBe("mobile-nav-drawer");
    // Overlay, not a column in the flex row.
    expect(drawer.className).toMatch(/fixed/);
    expect(drawer.className).toMatch(/inset-y-0/);
    expect(drawer.className).toMatch(/left-0/);
    expect(drawer.className).toMatch(/z-50/);
    expect(drawer.className).toMatch(/md:hidden/);

    // The Drawer renders the very same nav list as the rail.
    const drawerNav = within(drawer).getByTestId("sidebar-nav");
    for (const label of ["Overview", "Repository", "Interview", "Experiments", "GitHub"]) {
      expect(within(drawerNav).getByText(label)).toBeInTheDocument();
    }
    // Rail + Drawer: exactly two nav lists exist while open, never more.
    expect(screen.getAllByTestId("sidebar-nav")).toHaveLength(2);

    // The button now advertises the open state.
    const toggle = screen.getByTestId("mobile-nav-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAttribute("aria-label", "ナビゲーションを閉じる");
  });

  test("moves focus into the Drawer on open", async () => {
    setViewport(390);
    await renderLayout();

    const drawer = openDrawer();
    await waitFor(() => {
      expect(drawer.contains(document.activeElement)).toBe(true);
    });
    expect(document.activeElement).toBe(focusablesIn(drawer)[0]);
  });

  test("Escape closes the Drawer and returns focus to the menu button", async () => {
    setViewport(390);
    await renderLayout();

    openDrawer();
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();
    });
    const toggle = screen.getByTestId("mobile-nav-toggle");
    expect(document.activeElement).toBe(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  test("the close button and the backdrop both close it", async () => {
    setViewport(390);
    await renderLayout();

    openDrawer();
    fireEvent.click(screen.getByTestId("mobile-nav-close"));
    await waitFor(() => {
      expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();
    });

    openDrawer();
    fireEvent.click(screen.getByTestId("mobile-nav-backdrop"));
    await waitFor(() => {
      expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();
      expect(screen.queryByTestId("mobile-nav-backdrop")).not.toBeInTheDocument();
    });
  });

  test("the menu button itself toggles the Drawer closed again", async () => {
    setViewport(390);
    await renderLayout();

    openDrawer();
    fireEvent.click(screen.getByTestId("mobile-nav-toggle"));
    await waitFor(() => {
      expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();
    });
  });

  test("clicking a nav link navigates and closes the Drawer", async () => {
    setViewport(390);
    await renderLayout();

    const drawer = openDrawer();
    const settingsLink = within(drawer).getByText("Settings").closest("a");
    expect(settingsLink).not.toBeNull();
    fireEvent.click(settingsLink!);

    await waitFor(() => {
      expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("page-settings")).toBeInTheDocument();
  });

  test("traps Tab and Shift+Tab inside the Drawer while it is open", async () => {
    setViewport(390);
    await renderLayout();

    const drawer = openDrawer();
    const items = focusablesIn(drawer);
    expect(items.length).toBeGreaterThan(2);
    const first = items[0];
    const last = items[items.length - 1];

    // Tab from the last focusable wraps to the first.
    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);

    // Shift+Tab from the first focusable wraps to the last.
    first.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);

    // Focus that escaped the Drawer entirely is pulled back in.
    (document.body as HTMLElement).focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(drawer.contains(document.activeElement)).toBe(true);
  });
});

// ── 768px / 1280px: the existing desktop behaviour ──────────────────

describe.each([
  ["tablet", 768],
  ["desktop", 1280],
])("Desktop navigation is unchanged at %s (%ipx) (Issue #362)", (_name, width) => {
  test("the rail renders the nav and the chevron still collapses and expands it", async () => {
    setViewport(width);
    await renderLayout();

    const rail = screen.getByTestId("sidebar-rail");
    expect(within(rail).getByTestId("sidebar-nav")).toBeInTheDocument();
    expect(rail.className).toMatch(/w-56/);
    // No Drawer is rendered unless the developer opens it.
    expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();

    const collapse = within(rail).getByTestId("sidebar-collapse-toggle");
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(collapse);

    await waitFor(() => {
      expect(screen.getByTestId("sidebar-rail").className).toMatch(/w-16/);
    });
    expect(collapse).toHaveAttribute("aria-expanded", "false");
    expect(collapse).toHaveAttribute("aria-label", "ナビゲーションを展開する");
    // Collapsed still shows every route as an icon link.
    expect(within(rail).getAllByRole("link").length).toBeGreaterThan(15);

    fireEvent.click(collapse);
    await waitFor(() => {
      expect(screen.getByTestId("sidebar-rail").className).toMatch(/w-56/);
    });
    expect(collapse).toHaveAttribute("aria-label", "ナビゲーションを折りたたむ");
  });

  test("the mobile toggle exists but is marked md:hidden", async () => {
    setViewport(width);
    await renderLayout();

    const toggle = screen.getByTestId("mobile-nav-toggle");
    expect(toggle.className).toMatch(/md:hidden/);
  });
});
