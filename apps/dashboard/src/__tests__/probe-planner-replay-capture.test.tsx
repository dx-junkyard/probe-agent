/// <reference types="vitest/globals" />
// UX 監査 2026-11-17 C4: Probe Planner の計測点ごとの「入力を保存する（Replay に使う）」。
//
// 1. 既定では保存しない。承認ボタンは人が選んだときだけ replay_capture: true を送る。
// 2. 副作用リスク high（と denylist）の計測点では選択を無効にし、理由を示す。
//    リスクは計測点が持つ既存の値をそのまま使い、画面で新しい判定はしない。
// 3. 適用済みの patch からは、計測した component の観測画面と計測カバレッジへ遷移できる。

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";

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
  setSystemId: vi.fn(),
  ApiError,
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", role: "admin" },
    isAdmin: true,
    loading: false,
    systemId: 1,
    systems: [],
    login: vi.fn(),
    logout: vi.fn(),
    selectSystem: vi.fn(),
    refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: ReactNode }) => children,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

function point(overrides: Record<string, unknown>) {
  return {
    id: 1, plan_id: 10, system_id: 1, component_id: "summarize-c", feature_id: "feat-1",
    path: "src/m.py", symbol: "summarize", line_start: 1, line_end: 2,
    reason: "要約の経路", recommended_mode: "trace", side_effect_risk: "low",
    replayability: "Read-oriented: safe to replay with the same input.",
    denylist_hit: null, status: "proposed", replay_capture: false,
    decided_by_user_id: null, decision_method: null, decided_at: null,
    created_at: "2024-01-01", updated_at: "2024-01-01",
    ...overrides,
  };
}

function routeGet(points: Record<string, unknown>[], patches: Record<string, unknown>[] = []) {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/repository/probe-plans") {
      return Promise.resolve({
        system_id: 1, is_mock: false,
        plans: [{
          id: 10, feature_id: "feat-1", objective: "Observe", status: "proposed",
          created_at: "2024-01-01", probe_points: points,
        }],
      });
    }
    if (path === "/repository/probe-patches") return Promise.resolve(patches);
    return Promise.resolve(null);
  });
}

async function renderAndExpand() {
  const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/probe-planner"]}>
        <ProbePlannerPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
  fireEvent.click(screen.getByText("Feature: feat-1"));
}

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.put.mockResolvedValue({});
});

test("既定では入力を保存せず、承認は replay_capture を送らない（off）", async () => {
  routeGet([point({})]);
  await renderAndExpand();

  const box = await screen.findByTestId("replay-capture-1");
  expect(box).toHaveTextContent("副作用リスク: low");
  expect(box).toHaveTextContent("Replay の見込み: Read-oriented");
  const checkbox = screen.getByTestId("replay-capture-checkbox-1") as HTMLInputElement;
  expect(checkbox.checked).toBe(false);
  expect(checkbox.disabled).toBe(false);

  fireEvent.click(screen.getByRole("button", { name: "承認する" }));
  await waitFor(() => {
    expect(mockApi.put).toHaveBeenCalledWith(
      "/repository/probe-points/1/status",
      { status: "approved", replay_capture: false },
    );
  });
});

test("「入力を保存する」を選んで承認すると replay_capture: true を送る", async () => {
  routeGet([point({})]);
  await renderAndExpand();

  fireEvent.click(await screen.findByTestId("replay-capture-checkbox-1"));
  fireEvent.click(screen.getByRole("button", { name: "承認する" }));
  await waitFor(() => {
    expect(mockApi.put).toHaveBeenCalledWith(
      "/repository/probe-points/1/status",
      { status: "approved", replay_capture: true },
    );
  });
});

test("却下は replay_capture を送らない", async () => {
  routeGet([point({})]);
  await renderAndExpand();

  fireEvent.click(await screen.findByTestId("replay-capture-checkbox-1"));
  fireEvent.click(screen.getByRole("button", { name: "却下する" }));
  await waitFor(() => {
    expect(mockApi.put).toHaveBeenCalledWith(
      "/repository/probe-points/1/status",
      { status: "rejected" },
    );
  });
});

test("副作用リスク high の計測点では選択できず、理由が示される", async () => {
  routeGet([point({ side_effect_risk: "high", replayability: "Not safely replayable" })]);
  await renderAndExpand();

  const checkbox = (await screen.findByTestId("replay-capture-checkbox-1")) as HTMLInputElement;
  expect(checkbox.disabled).toBe(true);
  expect(checkbox.checked).toBe(false);
  expect(screen.getByTestId("replay-capture-disabled-1")).toHaveTextContent("副作用リスクが high");

  fireEvent.click(screen.getByRole("button", { name: "承認する" }));
  await waitFor(() => {
    expect(mockApi.put).toHaveBeenCalledWith(
      "/repository/probe-points/1/status",
      { status: "approved", replay_capture: false },
    );
  });
});

test("承認済みの計測点には記録済みの判断が表示される", async () => {
  routeGet([point({ status: "approved", replay_capture: true, decision_method: "manual" })]);
  await renderAndExpand();

  expect(await screen.findByTestId("replay-capture-recorded-1")).toHaveTextContent("記録済みの判断: 入力を保存する");
  expect((screen.getByTestId("replay-capture-checkbox-1") as HTMLInputElement).checked).toBe(true);
});

test("適用済みの patch から観測画面と計測カバレッジへ遷移できる", async () => {
  routeGet(
    [point({ status: "approved" }), point({ id: 2, component_id: "rejected-c", status: "rejected" })],
    [{
      id: 20, plan_id: 10, system_id: 1, snapshot_id: 5,
      commit_sha: "abcdef1234567890", diff: "diff --git a/a.py b/a.py",
      worktree_path: null, skipped: [], status: "generated", error: null,
      cleanup_state: "removed", cleanup_error: null,
      apply_status: "applied", apply_error: null,
      applied_at: "2024-01-02T00:00:00Z", applied_by_user_id: 1,
      validation_runs: [], created_at: "2024-01-01",
    }],
  );
  await renderAndExpand();

  const links = await screen.findByTestId("patch-observation-links");
  expect(screen.getByTestId("patch-observe-component-summarize-c"))
    .toHaveAttribute("href", "/components?component=summarize-c");
  expect(screen.getByTestId("patch-observe-coverage"))
    .toHaveAttribute("href", "/instrumentation-coverage");
  expect(links).not.toHaveTextContent("rejected-c");
});

test("未適用の patch には観測画面へのリンクを出さない", async () => {
  routeGet([point({ status: "approved" })], [{
    id: 20, plan_id: 10, system_id: 1, snapshot_id: 5,
    commit_sha: "abcdef1234567890", diff: "diff", worktree_path: null, skipped: [],
    status: "generated", error: null, cleanup_state: "removed", cleanup_error: null,
    apply_status: "not_applied", apply_error: null, applied_at: null, applied_by_user_id: null,
    validation_runs: [], created_at: "2024-01-01",
  }]);
  await renderAndExpand();

  await screen.findByText("リポジトリへ適用");
  expect(screen.queryByTestId("patch-observation-links")).not.toBeInTheDocument();
});
