/// <reference types="vitest/globals" />
// Issue #466 batch 3 — UX-10 (Workspace の選択と URL), UX-11 (Setup Guide の
// 次の操作 CTA), UX-13 (ナビゲーションの段階化・検索・現在地), UX-14 (狭い
// 画面のヘッダー), UX-19 (導入チェックの進捗の保存)。

import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
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
let mockSystems: { id: number; name: string; environment?: string }[] = [{ id: 1, name: "alpha" }];
let mockUser: { id: number; username: string; role: string } | null = { id: 7, username: "dev", role: "user" };

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

function LocationEcho() {
  const location = useLocation();
  return <p data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</p>;
}

function HistoryButtons() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(-1)}>履歴で戻る</button>
      <button type="button" onClick={() => navigate(1)}>履歴で進む</button>
    </>
  );
}

function renderAt(ui: ReactNode, route: string, path = "*") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={<>{ui}<LocationEcho /><HistoryButtons /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  mockSystemId = 1;
  mockSystems = [{ id: 1, name: "alpha" }];
  mockUser = { id: 7, username: "dev", role: "user" };
});

// ── UX-10 ───────────────────────────────────────────────────────────

describe("UX-10: Decision Workspace の選択と URL が同期する", () => {
  function mockWorkspaces() {
    const detail = (id: number, title: string) => ({
      id, system_id: 1, title, focus: "", status: "active", summary: "",
      created_at: 1, updated_at: 1, messages: [], context_items: [], proposals: [],
    });
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/workspaces") {
        return Promise.resolve([
          { id: 1, system_id: 1, title: "Alpha 判断", focus: "", status: "active", summary: "", created_at: 1, updated_at: 1 },
          { id: 2, system_id: 1, title: "Beta 判断", focus: "", status: "active", summary: "", created_at: 1, updated_at: 1 },
        ]);
      }
      if (path === "/workspaces/1") return Promise.resolve(detail(1, "Alpha 判断"));
      if (path === "/workspaces/2") return Promise.resolve(detail(2, "Beta 判断"));
      if (path === "/workspaces/99") return Promise.reject(new ApiError(404, "Workspace not found"));
      return Promise.resolve(null);
    });
  }

  test("選択すると URL が変わり、戻る/進むで同じ Workspace を開く", async () => {
    mockWorkspaces();
    const { default: WorkspacesPage } = await import("@/pages/workspaces");
    renderAt(<WorkspacesPage />, "/workspaces");

    fireEvent.click(await screen.findByRole("button", { name: /Alpha 判断/ }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/workspaces?open=1"));
    fireEvent.click(screen.getByRole("button", { name: /Beta 判断/ }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/workspaces?open=2"));
    expect(screen.getByRole("button", { name: /Beta 判断/ })).toHaveAttribute("aria-current", "true");

    fireEvent.click(screen.getByText("履歴で戻る"));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/workspaces?open=1"));
    expect(screen.getByRole("button", { name: /Alpha 判断/ })).toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByText("履歴で進む"));
    await waitFor(() => expect(screen.getByRole("button", { name: /Beta 判断/ })).toHaveAttribute("aria-current", "true"));
  });

  test("URL を開き直す (再読込・共有) と同じ Workspace が開く", async () => {
    mockWorkspaces();
    const { default: WorkspacesPage } = await import("@/pages/workspaces");
    renderAt(<WorkspacesPage />, "/workspaces?open=2");
    await waitFor(() => expect(screen.getByRole("button", { name: /Beta 判断/ })).toHaveAttribute("aria-current", "true"));
    expect(await screen.findByText("No messages yet. Ask a question to start the dialogue.")).toBeInTheDocument();
  });

  test("無効な ID からも一覧へ戻れる", async () => {
    mockWorkspaces();
    const { default: WorkspacesPage } = await import("@/pages/workspaces");
    renderAt(<WorkspacesPage />, "/workspaces?open=99");
    fireEvent.click(await screen.findByTestId("workspace-back-to-list"));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/workspaces$/));

    renderAt(<WorkspacesPage />, "/workspaces?open=abc");
    const invalid = await screen.findByTestId("workspace-invalid-id");
    fireEvent.click(within(invalid).getByRole("button", { name: "Workspace 一覧に戻る" }));
    await waitFor(() => expect(screen.queryByTestId("workspace-invalid-id")).not.toBeInTheDocument());
  });
});

// ── UX-11 / UX-19: Setup Guide ─────────────────────────────────────

function connectivity(overrides: Record<string, unknown> = {}) {
  return {
    system_id: 1, state: "no_signal", total_trace_count: 0, smoke_trace_count: 0, real_trace_count: 0,
    first_trace_at: null, last_trace_at: null, last_trace_component_id: null,
    smoke_component_id: "probe-smoke-check", materialized_session_ids: [],
    freshness: "never_received", transport_freshness: "never_received", last_real_trace_at: null,
    seconds_since_last_trace: null, seconds_since_last_any_trace: null, evaluated_at: 0, clock_skew_seconds: 0,
    real_trace_count_5m: 0, real_trace_count_1h: 0, real_trace_count_24h: 0,
    delayed_after_seconds: 900, stale_after_seconds: 86400, thresholds_customized: false,
    ...overrides,
  };
}

function mockSetupApi(conn: Record<string, unknown>, tokens: unknown[] = []) {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/connectivity/status") return Promise.resolve(conn);
    if (path === "/tokens/me") return Promise.resolve(tokens);
    return Promise.resolve(null);
  });
}

const usableToken = {
  id: 1, name: "sdk", kind: "api", user_id: 7, system_id: 1, revoked: false,
  created_at: 1, expires_at: null, status: "active",
};

describe("UX-11: Setup Guide の次の操作が操作先につながる", () => {
  test("token がなければ Connect SDK への CTA を 1 つ出す", async () => {
    mockSetupApi(connectivity());
    const { default: SetupGuidePage } = await import("@/pages/setup-guide");
    renderAt(<SetupGuidePage />, "/setup-guide");
    const card = await screen.findByTestId("setup-next-step");
    const cta = within(card).getByTestId("setup-next-step-cta");
    expect(cta).toHaveTextContent("Connect SDK で token を発行する");
    fireEvent.click(cta);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/connect-sdk"));
  });

  test("受信が止まっているときはトラブルシューティングを開いてフォーカスする", async () => {
    mockSetupApi(connectivity({ state: "receiving", freshness: "stale", real_trace_count: 3 }), [usableToken]);
    const { default: SetupGuidePage } = await import("@/pages/setup-guide");
    renderAt(<SetupGuidePage />, "/setup-guide");
    const cta = within(await screen.findByTestId("setup-next-step")).getByTestId("setup-next-step-cta");
    expect(cta).toHaveTextContent("トラブルシューティングを開く");
    const details = screen.getByTestId("setup-troubleshooting-disclosure") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.click(cta);
    expect(details.open).toBe(true);
    expect(within(details).getByText("届かないときのトラブルシューティング")).toHaveFocus();
  });

  test("resolveSetupStep は全ステップに CTA を持つ", async () => {
    const { resolveSetupStep, SETUP_ANCHORS } = await import("@/components/setup-next-step");
    const step = resolveSetupStep(connectivity() as never, [usableToken as never], 1);
    expect(step.cta).toEqual({ kind: "anchor", label: "実行形態別の設定例を開く", anchorId: SETUP_ANCHORS.runtimePatterns });
    const receiving = resolveSetupStep(connectivity({ state: "receiving", freshness: "receiving_now" }) as never, [], 1);
    expect(receiving.cta).toMatchObject({ kind: "route", to: "/components" });
  });
});

describe("UX-19: 導入チェックの進捗を System ごとに保存する", () => {
  test("画面を離れて戻っても実行形態とチェックが復元され、手動確認とサーバー確認は別表示", async () => {
    mockSetupApi(connectivity(), [usableToken]);
    const { default: SetupGuidePage } = await import("@/pages/setup-guide");
    const first = renderAt(<SetupGuidePage />, "/setup-guide");

    fireEvent.click(await screen.findByRole("tab", { name: "ホストで直接実行" }));
    const checklist = await screen.findByTestId("runtime-checklist-host");
    fireEvent.click(within(checklist).getByLabelText("対象アプリの環境へprobe-agent SDKを導入した"));
    expect(within(checklist).getByTestId("runtime-checklist-manual-count")).toHaveTextContent("手動確認 1 / 6");
    await waitFor(() =>
      expect(within(checklist).getByTestId("runtime-checklist-server-status")).toHaveTextContent("サーバーが確認した接続状態"),
    );
    first.unmount();

    // 再訪 (token 発行画面から戻った想定)。
    renderAt(<SetupGuidePage />, "/setup-guide");
    const restored = await screen.findByTestId("runtime-checklist-host");
    expect(within(restored).getByLabelText("対象アプリの環境へprobe-agent SDKを導入した")).toBeChecked();
  });

  test("別 System・別の利用者の進捗は復元しない", async () => {
    mockSetupApi(connectivity(), [usableToken]);
    const { default: SetupGuidePage } = await import("@/pages/setup-guide");
    const first = renderAt(<SetupGuidePage />, "/setup-guide");
    const checklist = await screen.findByTestId("runtime-checklist-compose");
    fireEvent.click(within(checklist).getByLabelText("対象serviceへprobe-agent SDKを導入した"));
    first.unmount();

    mockSystemId = 2;
    mockSystems = [{ id: 1, name: "alpha" }, { id: 2, name: "beta" }];
    const second = renderAt(<SetupGuidePage />, "/setup-guide");
    expect(within(await screen.findByTestId("runtime-checklist-compose")).getByLabelText("対象serviceへprobe-agent SDKを導入した")).not.toBeChecked();
    second.unmount();

    mockSystemId = 1;
    mockUser = { id: 8, username: "other", role: "user" };
    renderAt(<SetupGuidePage />, "/setup-guide");
    expect(within(await screen.findByTestId("runtime-checklist-compose")).getByLabelText("対象serviceへprobe-agent SDKを導入した")).not.toBeChecked();
  });

  test("保存できない環境でも画面は動く", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    mockSetupApi(connectivity(), [usableToken]);
    const { default: SetupGuidePage } = await import("@/pages/setup-guide");
    renderAt(<SetupGuidePage />, "/setup-guide");
    const checklist = await screen.findByTestId("runtime-checklist-compose");
    fireEvent.click(within(checklist).getByLabelText("対象serviceへprobe-agent SDKを導入した"));
    expect(within(checklist).getByLabelText("対象serviceへprobe-agent SDKを導入した")).toBeChecked();
    setItem.mockRestore();
  });
});

// ── UX-13: ナビゲーション ───────────────────────────────────────────

describe("UX-13: ナビゲーションの段階化・検索・現在地", () => {
  async function renderSidebar(route = "/") {
    mockApi.get.mockImplementation(() => Promise.resolve(null));
    const { Sidebar } = await import("@/components/layout/sidebar");
    return renderAt(<Sidebar />, route);
  }

  test("目的別の 3 入口から開始地点を選べる", async () => {
    await renderSidebar();
    const entries = screen.getByTestId("sidebar-purpose-entries");
    expect(within(entries).getByRole("link", { name: /現状を理解する/ })).toHaveAttribute("href", "/system-understanding");
    expect(within(entries).getByRole("link", { name: /要件を設計する/ })).toHaveAttribute("href", "/ux-design-studio");
    expect(within(entries).getByRole("link", { name: /候補を比較評価する/ })).toHaveAttribute("href", "/experiments");
  });

  test("詳細ビューは既定で閉じ、現在地が詳細ビューにあるときは開いて『表示中』を示す", async () => {
    await renderSidebar("/");
    const understandDetails = screen.getByTestId("sidebar-details-understand") as HTMLDetailsElement;
    expect(understandDetails.open).toBe(false);
    expect(within(understandDetails).getByText(/詳細ビュー/)).toBeInTheDocument();
  });

  test("現在地が詳細ビュー内なら開いた状態で始まり、バッジで示す", async () => {
    await renderSidebar("/journey-blueprint");
    const understandDetails = screen.getByTestId("sidebar-details-understand") as HTMLDetailsElement;
    expect(understandDetails.open).toBe(true);
    const active = within(understandDetails).getByText("Journey Service Blueprint").closest("a")!;
    expect(active).toHaveAttribute("aria-current", "page");
    expect(within(active).getByTestId("sidebar-current-badge")).toHaveTextContent("表示中");
  });

  test("画面検索で詳細ビューの画面にも到達でき、Escape で検索語を消す", async () => {
    await renderSidebar();
    const search = screen.getByTestId("sidebar-search");
    fireEvent.change(search, { target: { value: "blueprint" } });
    const results = screen.getByTestId("sidebar-search-results");
    expect(within(results).getByRole("link", { name: /Journey Service Blueprint/ })).toHaveAttribute("href", "/journey-blueprint");
    expect(within(results).getByText("1 件の画面")).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "要件" } });
    expect(within(screen.getByTestId("sidebar-search-results")).getByRole("link", { name: /UX Design Studio/ })).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "存在しない画面" } });
    expect(screen.getByText("該当する画面がありません")).toBeInTheDocument();
    fireEvent.keyDown(search, { key: "Escape" });
    expect(search).toHaveValue("");
    expect(screen.queryByTestId("sidebar-search-results")).not.toBeInTheDocument();
  });

  test("補足文は省略 (truncate) されない", async () => {
    await renderSidebar();
    const subtitle = screen.getByTestId("sidebar-item-subtitle-simulation-workbench");
    expect(subtitle.className).not.toMatch(/\btruncate\b/);
    expect(subtitle.className).toMatch(/whitespace-normal/);
  });
});

// ── UX-14: 狭い画面のヘッダー ──────────────────────────────────────

describe("UX-14: 狭い画面では補助操作をメニューへ移す", () => {
  test("System 選択は残り幅に収まり、補助操作は lg 未満でメニューにまとまる", async () => {
    mockSystems = [{ id: 1, name: "とても長いシステム名".repeat(4), environment: "production" }];
    mockUser = { id: 7, username: "very-long-user-name-for-layout-check", role: "admin" };
    mockApi.get.mockImplementation(() => Promise.resolve(null));
    const { Header } = await import("@/components/layout/header");
    renderAt(<Header />, "/");

    const select = screen.getByTestId("header-system-select");
    expect(select).toHaveAttribute("title", expect.stringContaining("とても長いシステム名"));
    expect(select.className).toMatch(/w-full/);
    expect(select.parentElement!.className).toMatch(/min-w-0/);
    expect(select.parentElement!.className).toMatch(/flex-1/);

    // デスクトップの補助操作群は lg 未満で隠れ、メニューボタンは lg 以上で隠れる。
    const logout = screen.getByRole("button", { name: "ログアウト" });
    expect(logout.parentElement!.className).toMatch(/hidden/);
    expect(logout.parentElement!.className).toMatch(/lg:flex/);
    const toggle = screen.getByTestId("header-overflow-toggle");
    expect(toggle.parentElement!.className).toMatch(/lg:hidden/);

    fireEvent.click(toggle);
    const menu = screen.getByTestId("header-overflow-menu");
    expect(within(menu).getByTestId("header-overflow-user")).toHaveTextContent("very-long-user-name-for-layout-check");
    expect(within(menu).getByTestId("header-overflow-create-system")).toBeInTheDocument();
    expect(within(menu).getByTestId("header-overflow-logout")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    await act(async () => { fireEvent.keyDown(document, { key: "Escape" }); });
    expect(screen.queryByTestId("header-overflow-menu")).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
  });
});
