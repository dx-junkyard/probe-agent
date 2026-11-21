/// <reference types="vitest/globals" />
// UX 監査 2026-11-17 第 3 波 — C3 (候補 1 件 + 自動 baseline で Experiment を作る、
// replay run の Snapshot を引き継ぐ)、C5 (採用した改善の patch を publish 画面へ
// 渡し、出所を表示する)、D7 (failed の再実行、失敗の記録、操作失敗の文言)。

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
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

let mockSystemId: number | null = 1;

class ApiError extends Error {
  status: number;
  detail: string;
  denialCode?: string;
  nextAction?: string;
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
    user: { id: 1, username: "dev", role: "admin" },
    isAdmin: true,
    loading: false,
    systemId: mockSystemId,
    systems: [{ id: 1, name: "alpha" }],
    login: vi.fn(),
    logout: vi.fn(),
    selectSystem: vi.fn(),
    refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: ReactNode }) => children,
}));

const toastMock = vi.hoisted(() => Object.assign(vi.fn(), {
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: toastMock }));

function renderAt(ui: ReactNode, route: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const githubConfigured = { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" };

function preflight(snapshotId: number) {
  return {
    snapshot_id: snapshotId, processing_state: "ready", freshness: "current", commit_sha: "abcdef123456",
    head_sha: "abcdef123456", head_relation: "same", commits_behind: 0, verdict: "ok", checks: [],
    recommended_snapshot_id: 5, recommended_snapshot_commit_sha: "abcdef123456",
    recommended_snapshot_freshness: "current", is_recommended: snapshotId === 5,
    requires_stale_acknowledgement: false, stale_continuation_note: null,
  };
}

function experiment(overrides: Record<string, unknown> = {}) {
  return {
    id: 1, system_id: 1, feature_id: "feat-1", objective: "速度比較", snapshot_id: 5,
    baseline_commit: "abc", config_revision: 1, execution: null, status: "completed", error: null,
    human_decision: "adopted", human_decision_variant_key: "variant-1", human_decision_note: "ok",
    variants: [
      { id: 1, variant_key: "baseline", label: "Baseline", is_baseline: true, status: "completed", patch_text: null, risk_note: "", error: null, metrics: {}, commands: [] },
      { id: 2, variant_key: "variant-1", label: "return two", is_baseline: false, status: "completed", patch_text: "diff", risk_note: "", error: null, metrics: {}, commands: [] },
    ],
    comparison: {}, analysis: null, created_at: "2024-01-01T00:00:00Z", started_at: null, completed_at: null,
    run_allowed: true, adopted_improvement_patch_id: 7,
    ...overrides,
  };
}

function mockExperimentsApi(opts: {
  experiments?: unknown[];
  appStatus?: unknown;
  connections?: unknown[];
} = {}) {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/experiments") return Promise.resolve(opts.experiments ?? []);
    if (path === "/repository/snapshots") {
      return Promise.resolve([
        { id: 5, status: "ready", commit_sha: "abcdef123456", file_count: 3, created_at: 1 },
        { id: 4, status: "ready", commit_sha: "0123456789ab", file_count: 3, created_at: 0 },
      ]);
    }
    if (path === "/repository/drafts/latest") return Promise.resolve({ feature_drafts: [] });
    if (path.startsWith("/snapshot-preflight")) {
      const m = /snapshot_id=(\d+)/.exec(path);
      return Promise.resolve(preflight(m ? Number(m[1]) : 5));
    }
    if (path === "/replay-variant-runs/11/variants/12/experiment-payload") {
      return Promise.resolve({
        label: "faster", patch_text: "diff --git a/x b/x", patch_hash: "h", source: "replay_variant",
        risk_note: "", origin: { replay_variant_run_id: 11, replay_variant_id: 12, snapshot_id: 4 },
      });
    }
    if (path.startsWith("/experiments/") && path.endsWith("/record")) return Promise.resolve(null);
    if (path === "/github/app-status") return Promise.resolve(opts.appStatus ?? githubConfigured);
    if (path === "/github/connections") return Promise.resolve(opts.connections ?? [{ id: 1, status: "connected" }]);
    return Promise.resolve(null);
  });
}

async function renderExperiments(route = "/experiments") {
  const { default: ExperimentsPage } = await import("@/pages/experiments");
  return renderAt(<ExperimentsPage />, route);
}

async function expandFirst() {
  await waitFor(() => expect(screen.getByText(/Experiment #1/)).toBeInTheDocument());
  fireEvent.click(screen.getByText(/Experiment #1/).closest("[class*=cursor-pointer]")!);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSystemId = 1;
});

describe("C3: replay 候補 1 件と自動 baseline で Experiment を作る", () => {
  test("引き継いだ候補 1 件と run の Snapshot で、Feature と目的だけ入力すれば作成できる", async () => {
    mockExperimentsApi();
    mockApi.post.mockResolvedValue({ id: 9 });
    await renderExperiments("/experiments?replay_run_id=11&replay_variant_id=12");
    const dialog = await screen.findByRole("dialog", { name: "Experimentを作成" });

    // 候補は 1 行だけ (空の 2 件目を足さない)、Snapshot は run の #4。
    await waitFor(() => expect(within(dialog).getAllByPlaceholderText("ラベル（例: optimized-v1）")).toHaveLength(1));
    await waitFor(() => expect(within(dialog).getByLabelText("Snapshot")).toHaveValue("4"));
    expect(within(dialog).getByText(/Snapshot #4 は replay run から引き継ぎました/)).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("Feature（評価対象）"), { target: { value: "feat-1" } });
    fireEvent.change(within(dialog).getByLabelText("評価目的"), { target: { value: "速くなるか" } });
    const submit = within(dialog).getByRole("button", { name: "Experimentを作成" });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith("/experiments", {
      feature_id: "feat-1",
      objective: "速くなるか",
      snapshot_id: 4,
      variants: [{ label: "faster", patch_text: "diff --git a/x b/x", risk_note: undefined }],
    }));
    // 引き継いだ Snapshot の preflight を、既存の stale 判定の流れで問い合わせている。
    expect(mockApi.get).toHaveBeenCalledWith(expect.stringContaining("snapshot_id=4"));
  });

  test("選択できない Snapshot は初期値にせず、その旨を出す", async () => {
    mockExperimentsApi();
    mockApi.get.mockImplementation(((orig) => (path: string) => {
      if (path === "/replay-variant-runs/11/variants/12/experiment-payload") {
        return Promise.resolve({
          label: "faster", patch_text: "diff", patch_hash: "h", source: "replay_variant",
          risk_note: "", origin: { snapshot_id: 99 },
        });
      }
      return orig(path);
    })(mockApi.get.getMockImplementation()!));
    await renderExperiments("/experiments?replay_run_id=11&replay_variant_id=12");
    const dialog = await screen.findByRole("dialog", { name: "Experimentを作成" });
    expect(await within(dialog).findByTestId("experiment-replay-snapshot-unavailable")).toHaveTextContent("#99");
    expect(within(dialog).getByLabelText("Snapshot")).toHaveValue("");
  });

  test("下限・上限はサーバーと同じ 1〜10 件", async () => {
    const { checkVariants, MIN_EXPERIMENT_VARIANTS, MAX_EXPERIMENT_VARIANTS } = await import("@/lib/experiment-variants");
    expect([MIN_EXPERIMENT_VARIANTS, MAX_EXPERIMENT_VARIANTS]).toEqual([1, 10]);
    const one = { label: "a", patch_text: "d", risk_note: "" };
    expect(checkVariants([one]).submittable).toBe(true);
    expect(checkVariants([]).submittable).toBe(false);
    expect(checkVariants(Array(11).fill(one)).submittable).toBe(false);
  });
});

describe("C5: 採用した改善の patch を publish 画面へ渡す", () => {
  test("採用後のリンクはサーバーが返した patch を ?patch= で渡す", async () => {
    mockExperimentsApi({ experiments: [experiment()] });
    await renderExperiments();
    await expandFirst();
    const link = await screen.findByTestId("experiment-github-publish-link");
    expect(link).toHaveAttribute("href", "/github?patch=7");
    expect(link).toHaveTextContent("patch #7");
    // completed の再実行はサーバーが許しても、判断の根拠を上書きするので出さない。
    expect(screen.queryByTestId("experiment-run-1")).not.toBeInTheDocument();
  });

  test("GitHub App が未設定なら、リンクを消すのではなく理由と設定先を出す", async () => {
    mockExperimentsApi({
      experiments: [experiment()],
      appStatus: { ...githubConfigured, configured: false },
    });
    await renderExperiments();
    await expandFirst();
    const note = await screen.findByTestId("experiment-github-publish-unavailable");
    expect(note).toHaveTextContent("GitHub App が未設定");
    expect(within(note).getByRole("link", { name: "GitHub 画面で設定する" })).toHaveAttribute("href", "/github");
    expect(screen.queryByTestId("experiment-github-publish-link")).not.toBeInTheDocument();
  });

  test("接続済みのリポジトリが無いときも理由を出す", async () => {
    mockExperimentsApi({ experiments: [experiment()], connections: [{ id: 1, status: "error" }] });
    await renderExperiments();
    await expandFirst();
    expect(await screen.findByTestId("experiment-github-publish-unavailable")).toHaveTextContent("接続済み");
  });

  test("publish ダイアログの選択肢は改善 patch の出所をサーバーの値で表示する", async () => {
    const { patchOptionLabel } = await import("@/pages/github");
    const base = {
      id: 7, plan_id: 3, system_id: 1, snapshot_id: 5, commit_sha: "abc1234567890", diff: "",
      worktree_path: null, skipped: [], status: "generated", error: null, cleanup_state: null,
      cleanup_error: null, apply_status: "not_applied", apply_error: null, applied_at: null,
      applied_by_user_id: null, validation_runs: [], created_at: "",
    };
    expect(patchOptionLabel({
      ...base, origin: "improvement", improvement_experiment_id: 1,
      improvement_variant_key: "variant-1", improvement_variant_label: "return two",
      improvement_adoption_current: true,
    })).toMatch(/^Experiment #1 採用候補\(return two\) · patch #7/);
    expect(patchOptionLabel({
      ...base, origin: "improvement", improvement_experiment_id: 1,
      improvement_variant_key: "variant-1", improvement_variant_label: "return two",
      improvement_adoption_current: false,
    })).toContain("現在は採用されていません");
    expect(patchOptionLabel({ ...base, origin: "probe_plan" })).toMatch(/^観測用 patch #7 · plan #3/);
  });

  test("?patch= の改善 patch を選んだ作成ダイアログが開き、接続が 1 つなら選択済み", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/github/app-status") return Promise.resolve(githubConfigured);
      if (path === "/github/connections") return Promise.resolve([{ id: 1, owner: "acme", repo: "w", status: "connected" }]);
      if (path === "/github/publish-jobs") return Promise.resolve([]);
      if (path === "/users") return Promise.resolve([]);
      if (path === "/repository/probe-patches") {
        return Promise.resolve([{
          id: 7, plan_id: 3, system_id: 1, snapshot_id: 5, commit_sha: "abc1234567890", diff: "d",
          worktree_path: "", skipped: [], status: "generated", error: null, cleanup_state: "not_attempted",
          cleanup_error: null, apply_status: "not_applied", apply_error: null, applied_at: null,
          applied_by_user_id: null, created_at: 1, origin: "improvement", improvement_experiment_id: 1,
          improvement_variant_key: "variant-1", improvement_variant_label: "return two",
          improvement_adoption_current: true,
          validation_runs: [
            { id: 1, variant: "baseline", overall_success: true, commands: [] },
            { id: 2, variant: "probed", overall_success: true, commands: [] },
          ],
        }]);
      }
      return Promise.resolve(null);
    });
    const { default: GithubPage } = await import("@/pages/github");
    renderAt(<GithubPage />, "/github?patch=7");
    const select = await screen.findByTestId("publish-job-patch-select") as HTMLSelectElement;
    expect(select.value).toBe("7");
    expect(select.selectedOptions[0].textContent).toContain("Experiment #1 採用候補(return two)");
    await waitFor(() => expect(screen.getByTestId("publish-job-connection-select")).toHaveValue("1"));
    expect(screen.queryByText(/Probe Planner ページでpatchを検証/)).not.toBeInTheDocument();
    // 開くだけで、作成 (POST) も承認もしない。
    expect(mockApi.post).not.toHaveBeenCalled();
  });
});

describe("D7: failed の再実行と失敗の記録", () => {
  test("failed で run_allowed なら「再実行」と失敗の記録を出す", async () => {
    mockExperimentsApi({
      experiments: [experiment({
        status: "failed", error: "all variants failed", human_decision: "undecided",
        human_decision_variant_key: null, adopted_improvement_patch_id: null,
        variants: [
          { id: 1, variant_key: "baseline", label: "Baseline", is_baseline: true, status: "failed", patch_text: null, risk_note: "", error: "test command failed", metrics: {}, commands: [
            { id: 1, command: "python -m pytest -q", exit_code: 1, duration_ms: 1, stdout: "", stderr: "AssertionError", stdout_truncated: false, stderr_truncated: false, timed_out: false },
          ] },
        ],
      })],
    });
    await renderExperiments();
    await expandFirst();
    expect(await screen.findByTestId("experiment-run-1")).toHaveTextContent("再実行");
    expect(screen.getByTestId("experiment-error-1")).toHaveTextContent("all variants failed");
    expect(screen.getByTestId("experiment-variant-failure-baseline")).toHaveTextContent("test command failed");
    expect(screen.getByTestId("experiment-variant-failed-commands-baseline")).toHaveTextContent("AssertionError");
  });

  test("サーバーが実行を許さない (governed) なら failed でも出さない", async () => {
    mockExperimentsApi({
      experiments: [experiment({ status: "failed", run_allowed: false, human_decision: "undecided", adopted_improvement_patch_id: null })],
    });
    await renderExperiments();
    await expandFirst();
    await screen.findByTestId("experiment-detail-1");
    expect(screen.queryByTestId("experiment-run-1")).not.toBeInTheDocument();
  });

  test("実行の失敗は String(e) ではなくサーバーの理由と次の操作で伝える", async () => {
    mockExperimentsApi({
      experiments: [experiment({ status: "draft", human_decision: "undecided", adopted_improvement_patch_id: null })],
    });
    const err = new ApiError(409, "Execution mode is fixed");
    err.denialCode = "capability_not_allowed";
    err.nextAction = "実行モードを shadow にする";
    mockApi.post.mockRejectedValue(err);
    await renderExperiments();
    await expandFirst();
    fireEvent.click(await screen.findByTestId("experiment-run-1"));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    const message = toastMock.error.mock.calls[0][0] as string;
    expect(message).toContain("実行できませんでした: Execution mode is fixed");
    expect(message).toContain("capability_not_allowed");
    expect(message).toContain("次の操作: 実行モードを shadow にする");
    expect(message).not.toContain("Error:");
  });
});
