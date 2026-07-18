/// <reference types="vitest/globals" />
// Issue #265: "phase 0" guidance for pre-login (0 admins) and pre-System
// (0 Systems) states -- the login page, Overview/Settings pages, and the
// header, which previously either dead-ended or rendered a heading-only
// blank screen. Self-contained mocks (rather than reusing the shared
// dashboard-contracts.test.tsx mock, which hard-codes a logged-in admin
// user) so the "no user yet" / "no System yet" states can actually be
// exercised, following the same `vi.mock("@/api/client")` /
// `vi.mock("@/api/auth")` pattern that file uses.

import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

let mockSystemId: number | null = null;
let mockSystems: { id: number; name: string; environment?: string }[] = [];
let mockUser: { id: number; username: string; role: string } | null = null;

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

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSystemId = null;
  mockSystems = [];
  mockUser = null;
});

// ── Login page: 0-admin bootstrap guidance ──────────────────────────

describe("Login page bootstrap guidance (Issue #265)", () => {
  test("shows env-var bootstrap instructions when no admin exists (development)", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/auth/bootstrap-status") {
        return Promise.resolve({
          admin_exists: false, auth_mode: "anonymous", llm_configured: false,
          environment: "development",
        });
      }
      return Promise.resolve(null);
    });

    const { default: LoginPage } = await import("@/pages/login");
    render(<LoginPage />, { wrapper: createWrapper() });

    const guide = await screen.findByTestId("login-bootstrap-guide");
    expect(guide).toHaveTextContent("管理者アカウントがまだ作成されていません");
    const detail = screen.getByTestId("login-bootstrap-guide-detail");
    expect(detail).toHaveTextContent("CONTROL_ADMIN_USERNAME");
    expect(detail).toHaveTextContent("CONTROL_ADMIN_PASSWORD");
    // The plain username/password form must not render alongside it.
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
  });

  test("redacts the specific env var names in production", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/auth/bootstrap-status") {
        return Promise.resolve({
          admin_exists: false, auth_mode: "anonymous", llm_configured: false,
          environment: "production",
        });
      }
      return Promise.resolve(null);
    });

    const { default: LoginPage } = await import("@/pages/login");
    render(<LoginPage />, { wrapper: createWrapper() });

    const guide = await screen.findByTestId("login-bootstrap-guide-production");
    expect(guide).toBeInTheDocument();
    expect(screen.queryByTestId("login-bootstrap-guide-detail")).not.toBeInTheDocument();
    expect(screen.queryByText(/CONTROL_ADMIN_USERNAME/)).not.toBeInTheDocument();
  });

  test("shows the normal login form once an admin exists", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/auth/bootstrap-status") {
        return Promise.resolve({
          admin_exists: true, auth_mode: "user", llm_configured: true,
          environment: "development",
        });
      }
      return Promise.resolve(null);
    });

    const { default: LoginPage } = await import("@/pages/login");
    render(<LoginPage />, { wrapper: createWrapper() });

    await screen.findByLabelText("Username");
    expect(screen.queryByTestId("login-bootstrap-guide")).not.toBeInTheDocument();
  });
});

// ── Header: 0-Systems empty state ───────────────────────────────────

describe("Header zero-System empty state (Issue #265)", () => {
  test("shows explicit guidance instead of a lone icon when there are no Systems", async () => {
    mockUser = { id: 1, username: "admin", role: "admin" };
    mockSystems = [];
    mockApi.get.mockResolvedValue(null);

    const { Header } = await import("@/components/layout/header");
    render(<Header />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("header-no-systems-hint")).toHaveTextContent("System が未作成です");
    const createButton = screen.getByTestId("header-create-system-button");
    expect(createButton).toHaveTextContent("System を作成");
  });

  test("shows the System select once a System exists", async () => {
    mockUser = { id: 1, username: "admin", role: "admin" };
    mockSystemId = 1;
    mockSystems = [{ id: 1, name: "alpha" }];
    mockApi.get.mockResolvedValue(null);

    const { Header } = await import("@/components/layout/header");
    render(<Header />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.queryByTestId("header-no-systems-hint")).not.toBeInTheDocument();
    });
    expect(screen.getByText("alpha")).toBeInTheDocument();
  });
});

// ── Overview: 0-Systems empty state ──────────────────────────────────

describe("Overview zero-System empty state (Issue #265)", () => {
  test("shows a System-creation path instead of the components get-started list", async () => {
    mockUser = { id: 1, username: "admin", role: "admin" };
    mockSystems = [];
    mockApi.get.mockResolvedValue(null);

    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("overview-no-systems")).toHaveTextContent("System を作成してください");
    expect(screen.queryByTestId("overview-get-started")).not.toBeInTheDocument();
  });
});

// ── Settings: 0-Systems empty state ─────────────────────────────────

describe("Settings zero-System empty state (Issue #265)", () => {
  test("shows guidance instead of a blank screen when there are no Systems", async () => {
    mockUser = { id: 1, username: "admin", role: "admin" };
    mockSystems = [];
    mockApi.get.mockResolvedValue(null);

    const { default: SettingsPage } = await import("@/pages/settings");
    render(<SettingsPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("settings-no-systems-reason")).toHaveTextContent("System が作成されていません");
  });

  test("shows the settings form when a System is selected", async () => {
    mockUser = { id: 1, username: "admin", role: "admin" };
    mockSystemId = 1;
    mockSystems = [{ id: 1, name: "alpha", environment: "production" }];
    mockApi.get.mockResolvedValue(null);

    const { default: SettingsPage } = await import("@/pages/settings");
    render(<SettingsPage />, { wrapper: createWrapper() });

    await screen.findByText("System Configuration");
    expect(screen.queryByTestId("settings-no-systems-reason")).not.toBeInTheDocument();
  });
});
