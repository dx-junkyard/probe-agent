/// <reference types="vitest/globals" />
// UX 監査 2026-11-17 第 1 波のうち Repository 画面の項目 (B1 / B2 / B5 / B7 / F1)。
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
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
const mockSystemId: number | null = 1;

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => mockSystemId,
  setSystemId: () => {},
  ApiError: class ApiError extends Error {},
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", role: "admin" },
    isAdmin: true,
    loading: false,
    systemId: mockSystemId,
    systems: [],
    login: vi.fn(),
    logout: vi.fn(),
    selectSystem: vi.fn(),
    refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: ReactNode }) => children,
}));

function renderPage(qc: QueryClient, ui: ReactNode) {
  return render(
    <QueryClientProvider client={qc}>
      <BrowserRouter>{ui}</BrowserRouter>
    </QueryClientProvider>,
  );
}

function newClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
}

const statusFixture = (overrides: Record<string, unknown> = {}) => ({
  configured: true, repo_path: "/repos/alpha",
  current_head: "abc1234000", head_error: null,
  working_tree_dirty: false, dirty_file_count: 0, dirty_sample: [],
  latest_snapshot: { id: 12, commit_sha: "abc1234000", status: "ready", created_at: 1 },
  latest_indexed_snapshot: { id: 12, commit_sha: "abc1234000", status: "ready", created_at: 1 },
  understanding_snapshot_id: null, understanding_status: null,
  head_relation: "same", commits_behind: 0,
  snapshot_stale: false, symbols_stale: false, next_actions: [],
  ...overrides,
});

function getWith(status: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return (path: string) => {
    if (path in extra) return Promise.resolve(extra[path]);
    if (path === "/repository") return Promise.resolve({
      id: 1, system_id: 1, repo_path: "/repos/alpha", include_patterns: [], exclude_patterns: [],
    });
    if (path === "/repository-candidates") return Promise.resolve([{ name: "alpha", path: "/repos/alpha" }]);
    if (path === "/repository/snapshots") return Promise.resolve([]);
    if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
    if (path === "/repository/status") return Promise.resolve(status);
    return Promise.resolve(null);
  };
}

const completedJob = (overrides: Record<string, unknown> = {}) => ({
  id: 7, system_id: 1, snapshot_id: 12, status: "completed", error: null,
  stale_capability_count: 0, created_at: 1, started_at: 1, completed_at: 2,
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("B1: Repository 設定の保存で、設定に依存する query を無効化する", () => {
  test("保存成功時に repoConfig / repositoryStatus / system-state / snapshotPreflight / overview を無効化する", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture()));
    mockApi.put.mockResolvedValue({
      id: 1, system_id: 1, repo_path: "/repos/alpha", include_patterns: ["*.py"], exclude_patterns: [],
    });
    const qc = newClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(qc, <RepositoryPage />);

    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("/repos/alpha"));
    fireEvent.change(screen.getByLabelText(/対象パターン/), { target: { value: "*.py" } });
    fireEvent.click(screen.getByRole("button", { name: "設定を保存" }));

    await waitFor(() => expect(mockApi.put).toHaveBeenCalled());
    await waitFor(() => {
      const keys = spy.mock.calls.map(([filters]) => (filters as { queryKey: unknown[] }).queryKey);
      for (const base of ["repoConfig", "repositoryStatus", "system-state", "snapshotPreflight", "overview"]) {
        expect(keys).toContainEqual([base, 1]);
      }
    });
  });
});

describe("B2: リポジトリ候補が 0 件のとき、日本語の原因と解決手順を出す", () => {
  test("PROBE_REPOSITORY_ROOTS の設定と再起動の手順を入力欄の下に出す", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture({ configured: false }), {
      "/repository": null,
      "/repository-candidates": [],
    }));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    const empty = await screen.findByTestId("repo-candidates-empty");
    expect(empty).toHaveTextContent("Gitリポジトリが見つかりませんでした");
    expect(within(empty).getByTestId("repo-candidates-empty-remediation"))
      .toHaveTextContent(/PROBE_REPOSITORY_ROOTS.*絶対パス.*再起動/);
    expect(screen.queryByText(/No Git repositories/)).not.toBeInTheDocument();
  });
});

describe("B5: Snapshot と symbol index の準備ができたら「システムを理解する」へ導く", () => {
  test("古い Capability が無く最新なら、/interview への CTA を 1 つ出し、更新ボタンは主ボタンでなくなる", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture(), {
      "/repository/resync/latest": completedJob(),
    }));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    const cta = await screen.findByTestId("resync-next-interview-cta");
    expect(cta).toHaveTextContent("システムを理解する");
    expect(cta.closest("a")).toHaveAttribute("href", "/interview");
    expect(screen.queryByTestId("stale-capability-guidance")).not.toBeInTheDocument();
    expect(screen.getByTestId("repository-resync-button").className).toMatch(/border-input/);
  });

  test("古い Capability があるときは既存の「System Understandingを構築」だけを出し、CTA は出さない", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture(), {
      "/repository/resync/latest": completedJob({ stale_capability_count: 2 }),
    }));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    await screen.findByTestId("stale-capability-guidance");
    expect(screen.queryByTestId("resync-next-interview-cta")).not.toBeInTheDocument();
  });

  test("完了後に symbol が古くなったら CTA は出さず、更新ボタンが主ボタンのまま", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture({ symbols_stale: true }), {
      "/repository/resync/latest": completedJob(),
    }));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    await screen.findByTestId("resync-job-status");
    expect(screen.queryByTestId("resync-next-interview-cta")).not.toBeInTheDocument();
    expect(screen.getByTestId("repository-resync-button").className).toMatch(/bg-primary/);
  });
});

describe("B7: Snapshot が無いことと古いことを別に表示する", () => {
  test("Snapshot が 1 件も無いときは「Snapshot未作成」を出し、「HEADより古い」は出さない", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture({
      latest_snapshot: null, latest_indexed_snapshot: null,
      snapshot_stale: true, head_relation: "unknown", commits_behind: null,
    })));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByTestId("snapshot-missing-badge")).toHaveTextContent("Snapshot未作成");
    expect(within(hub).queryByTestId("snapshot-stale-badge")).not.toBeInTheDocument();
    expect(within(hub).getByTestId("repository-lag")).toHaveTextContent("Snapshot未作成");
  });

  test("Snapshot があり HEAD が進んだときは「HEADより古い」を出す", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture({
      current_head: "def5678000", snapshot_stale: true, head_relation: "behind", commits_behind: 2,
    })));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByTestId("snapshot-stale-badge")).toBeInTheDocument();
    expect(within(hub).queryByTestId("snapshot-missing-badge")).not.toBeInTheDocument();
  });

  test("最新の Snapshot が failed のときは「作成に失敗」を出し、「HEADより古い」は出さない", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture({
      latest_snapshot: { id: 13, commit_sha: "abc1234000", status: "failed", created_at: 2 },
      latest_indexed_snapshot: null,
      snapshot_stale: true, head_relation: "unknown", commits_behind: null,
    })));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByTestId("snapshot-failed-badge")).toHaveTextContent("最新Snapshotの作成に失敗");
    expect(within(hub).queryByTestId("snapshot-stale-badge")).not.toBeInTheDocument();
    expect(within(hub).queryByTestId("snapshot-missing-badge")).not.toBeInTheDocument();
    expect(within(hub).getByTestId("repository-lag")).toHaveTextContent("最新Snapshotの作成に失敗");
    expect(hub).toHaveTextContent("Snapshot #13");
    expect(hub).toHaveTextContent("作成に失敗しました");
    expect(hub).not.toHaveTextContent("現在の解析はSnapshot #13");
  });

  test("最新の Snapshot が作成中 (indexing) のときは「作成中」を出し、「HEADより古い」は出さない", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture({
      latest_snapshot: { id: 13, commit_sha: "abc1234000", status: "indexing", created_at: 2 },
      latest_indexed_snapshot: null,
      snapshot_stale: true, head_relation: "unknown", commits_behind: null,
    })));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByTestId("snapshot-indexing-badge")).toHaveTextContent("Snapshot作成中");
    expect(within(hub).queryByTestId("snapshot-stale-badge")).not.toBeInTheDocument();
    expect(within(hub).getByTestId("repository-lag")).toHaveTextContent("Snapshot作成中");
  });
});

describe("F1: Repository 画面の英語文言", () => {
  test("設定タブとタブ名が日本語で、旧英語文言が残らない", async () => {
    mockApi.get.mockImplementation(getWith(statusFixture()));
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderPage(newClient(), <RepositoryPage />);

    await screen.findByText("Repository設定");
    for (const name of ["設定", "Snapshot", "symbol", "API定義スキャン"]) {
      expect(screen.getByRole("tab", { name })).toBeInTheDocument();
    }
    expect(await screen.findByRole("button", { name: "設定を保存" })).toBeInTheDocument();
    for (const english of [
      "Repository Configuration", "Configure the target repository for analysis",
      "Save Configuration", "Include Patterns", "Exclude Patterns", "one per line",
    ]) {
      expect(screen.queryByText(new RegExp(english))).not.toBeInTheDocument();
    }
  });
});
