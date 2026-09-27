/// <reference types="vitest/globals" />
// Issue #466 batch 2 — UX-03 (未保存入力の保持), UX-04 (入力途中の候補を黙って
// 落とさない), UX-05 (取得失敗を 0 件・読み込み中にしない)。

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { useState, type ReactNode } from "react";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

let mockSystemId: number | null = 1;
let mockSystems: { id: number; name: string }[] = [{ id: 1, name: "alpha" }, { id: 2, name: "beta" }];

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
    user: { id: 1, username: "dev", role: "admin" },
    isAdmin: true,
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

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

function renderAt(ui: ReactNode, route = "/") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSystemId = 1;
  mockSystems = [{ id: 1, name: "alpha" }, { id: 2, name: "beta" }];
});

// ── UX-04: 候補の行分類 (純関数) ────────────────────────────────────

describe("UX-04: 比較候補の行分類", () => {
  test("空行・入力途中・完全を区別し、入力途中があれば送信できない", async () => {
    const { checkVariants, describeMissing } = await import("@/lib/experiment-variants");
    const check = checkVariants([
      { label: "a", patch_text: "diff a", risk_note: "" },
      { label: "b", patch_text: "diff b", risk_note: "" },
      { label: "c", patch_text: "  ", risk_note: "注意" },
      { label: "", patch_text: "", risk_note: "" },
    ]);
    expect(check.rows.map((r) => r.state)).toEqual(["complete", "complete", "partial", "empty"]);
    expect(check.submittable).toBe(false);
    expect(describeMissing(check.partial[0])).toBe("候補 3 のpatchが必要です");

    const onlyEmptyExtra = checkVariants([
      { label: "a", patch_text: "diff a", risk_note: "" },
      { label: "b", patch_text: "diff b", risk_note: "" },
      { label: "", patch_text: "", risk_note: "" },
    ]);
    expect(onlyEmptyExtra.submittable).toBe(true);

    const riskOnly = checkVariants([{ label: "", patch_text: "", risk_note: "メモ" }]);
    expect(riskOnly.rows[0]).toMatchObject({ state: "partial", missing: ["label", "patch_text"] });
  });
});

// ── UX-03 / UX-04: Experiment 作成 ──────────────────────────────────

function mockExperimentsApi() {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/experiments") return Promise.resolve([]);
    if (path === "/repository/snapshots") {
      return Promise.resolve([{ id: 5, status: "ready", commit_sha: "abcdef123456", file_count: 3, created_at: 1 }]);
    }
    if (path === "/repository/drafts/latest") return Promise.resolve({ feature_drafts: [] });
    if (path.startsWith("/snapshot-preflight")) {
      return Promise.resolve({
        snapshot_id: 5, processing_state: "ready", freshness: "current", commit_sha: "abcdef123456",
        head_sha: "abcdef123456", head_relation: "same", commits_behind: 0, verdict: "ok", checks: [],
        recommended_snapshot_id: 5, recommended_snapshot_commit_sha: "abcdef123456",
        recommended_snapshot_freshness: "current", is_recommended: true,
        requires_stale_acknowledgement: false, stale_continuation_note: null,
      });
    }
    return Promise.resolve(null);
  });
}

async function openCreateDialog() {
  const { default: ExperimentsPage } = await import("@/pages/experiments");
  const view = renderAt(<ExperimentsPage />, "/experiments");
  fireEvent.click(await screen.findByTestId("experiment-create-button"));
  const dialog = await screen.findByRole("dialog", { name: "Experimentを作成" });
  return { view, dialog };
}

function fillBasics(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByLabelText("Feature（評価対象）"), { target: { value: "feat-1" } });
  fireEvent.change(within(dialog).getByLabelText("評価目的"), { target: { value: "速度比較" } });
  fireEvent.change(within(dialog).getByLabelText("Snapshot"), { target: { value: "5" } });
}

describe("UX-04: 入力途中の候補を黙って除外しない", () => {
  test("完全な 2 件 + 入力途中の 3 件目では送信せず、行別エラーを出す", async () => {
    mockExperimentsApi();
    const { dialog } = await openCreateDialog();
    fillBasics(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: /候補を追加/ }));
    for (const [i, label, patch] of [[1, "a", "diff a"], [2, "b", "diff b"]] as const) {
      fireEvent.change(within(dialog).getByLabelText(`候補 ${i} のラベル`), { target: { value: label } });
      fireEvent.change(within(dialog).getByLabelText(`候補 ${i} のpatch`), { target: { value: patch } });
    }
    fireEvent.change(within(dialog).getByLabelText("候補 3 のラベル"), { target: { value: "c" } });
    fireEvent.change(within(dialog).getByLabelText("候補 3 のリスクメモ"), { target: { value: "遅いかも" } });

    expect(within(dialog).getByTestId("experiment-variant-partial-hint")).toHaveTextContent("候補 3");
    const submit = within(dialog).getByRole("button", { name: "Experimentを作成" });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);

    expect(await within(dialog).findByTestId("experiment-variant-error-2")).toHaveTextContent("候補 3 のpatchが必要です");
    expect(within(dialog).getByLabelText("候補 3 のpatch")).toHaveAttribute("aria-invalid", "true");
    expect(mockApi.post).not.toHaveBeenCalled();

    // 明示的に削除すれば送信できる。
    fireEvent.click(within(dialog).getByRole("button", { name: "候補 3 を削除" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Experimentを作成" }));
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith("/experiments", expect.objectContaining({
      variants: [
        { label: "a", patch_text: "diff a", risk_note: undefined },
        { label: "b", patch_text: "diff b", risk_note: undefined },
      ],
    })));
  });

  test("完全な空行は入力途中として扱わない", async () => {
    mockExperimentsApi();
    mockApi.post.mockResolvedValue({ id: 9 });
    const { dialog } = await openCreateDialog();
    fillBasics(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: /候補を追加/ }));
    for (const [i, label, patch] of [[1, "a", "diff a"], [2, "b", "diff b"]] as const) {
      fireEvent.change(within(dialog).getByLabelText(`候補 ${i} のラベル`), { target: { value: label } });
      fireEvent.change(within(dialog).getByLabelText(`候補 ${i} のpatch`), { target: { value: patch } });
    }
    expect(within(dialog).queryByTestId("experiment-variant-partial-hint")).not.toBeInTheDocument();
    const submit = within(dialog).getByRole("button", { name: "Experimentを作成" });
    await waitFor(() => expect(submit).not.toBeDisabled());
    fireEvent.click(submit);
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledTimes(1));
  });
});

describe("UX-03: Experiment の下書きは閉じても消えない", () => {
  test("背景クリックで閉じても下書きを保持し、再開で同じ内容が戻る。破棄は明示操作だけ", async () => {
    mockExperimentsApi();
    const { dialog } = await openCreateDialog();
    const longPatch = "diff --git a/x.py b/x.py\n" + "+line\n".repeat(50);
    fireEvent.change(within(dialog).getByLabelText("候補 1 のpatch"), { target: { value: longPatch } });

    fireEvent.click(screen.getByTestId("dialog-overlay"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByTestId("experiment-draft-notice")).toHaveTextContent("作成先: alpha");

    fireEvent.click(screen.getByTestId("experiment-create-button"));
    const reopened = await screen.findByRole("dialog", { name: "Experimentを作成" });
    expect(within(reopened).getByLabelText("候補 1 のpatch")).toHaveValue(longPatch);
    expect(within(reopened).getByTestId("experiment-create-target-system")).toHaveTextContent("alpha(#1)");

    // Escape でも同じく保持。
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("experiment-draft-notice")).toBeInTheDocument();

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(within(screen.getByTestId("experiment-draft-notice")).getByRole("button", { name: "下書きを破棄" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(screen.queryByTestId("experiment-draft-notice")).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  test("別 System へ切り替えたら下書きを持ち越さない", async () => {
    mockExperimentsApi();
    const { view, dialog } = await openCreateDialog();
    fireEvent.change(within(dialog).getByLabelText("候補 1 のpatch"), { target: { value: "diff alpha" } });
    fireEvent.click(screen.getByTestId("dialog-overlay"));
    expect(await screen.findByTestId("experiment-draft-notice")).toBeInTheDocument();

    mockSystemId = 2;
    const { default: ExperimentsPage } = await import("@/pages/experiments");
    view.rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/experiments"]}><ExperimentsPage /></MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.queryByTestId("experiment-draft-notice")).not.toBeInTheDocument());
    expect(screen.getByTestId("experiment-create-button")).toHaveTextContent("Experimentを作成");
  });
});

// ── UX-03: Tabs の keepMounted と Repository 設定 ──────────────────

describe("UX-03: タブ往復で編集中のフォームを失わない", () => {
  test("keepMounted のタブは非表示中も入力を保持し、支援技術からは隠れる", async () => {
    const { Tabs, TabsList, TabsTrigger, TabsContent } = await import("@/components/ui/tabs");
    function Harness() {
      const [tab, setTab] = useState("a");
      return (
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="a">A</TabsTrigger>
            <TabsTrigger value="b">B</TabsTrigger>
          </TabsList>
          <TabsContent value="a" keepMounted><input aria-label="入力A" /></TabsContent>
          <TabsContent value="b"><p>B の内容</p></TabsContent>
        </Tabs>
      );
    }
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("入力A"), { target: { value: "編集中" } });
    fireEvent.click(screen.getByRole("tab", { name: "B" }));
    expect(screen.queryByRole("tabpanel", { name: "A" })).not.toBeInTheDocument();
    expect(screen.getByText("B の内容")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "A" }));
    expect(screen.getByLabelText("入力A")).toHaveValue("編集中");
  });

  test("Repository 設定の編集内容は Snapshots タブを往復しても残る", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository") return Promise.resolve({ id: 1, system_id: 1, repo_path: "/repos/alpha", include_patterns: ["*.py"], exclude_patterns: [] });
      if (path === "/repository-candidates") return Promise.resolve([{ name: "alpha", path: "/repos/alpha" }]);
      if (path === "/repository/snapshots") return Promise.resolve([]);
      if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
      return Promise.resolve(null);
    });
    const { default: RepositoryPage } = await import("@/pages/repository");
    renderAt(<RepositoryPage />, "/repository");

    const include = await screen.findByLabelText(/Include Patterns/);
    fireEvent.change(include, { target: { value: "*.py\n*.ts\nsrc/**" } });
    expect(screen.getByTestId("repo-config-unsaved")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Snapshots" }));
    fireEvent.click(screen.getByRole("tab", { name: "Configuration" }));
    expect(screen.getByLabelText(/Include Patterns/)).toHaveValue("*.py\n*.ts\nsrc/**");
  });
});

// ── UX-05: 取得失敗を 0 件・読み込み中にしない ─────────────────────

describe("UX-05: 取得失敗と 0 件を区別する", () => {
  test("Probe Planner: 一覧の取得失敗は『まだありません』ではなく再試行を出し、?plan= を『見つからない』と言わない", async () => {
    let fail = true;
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") {
        return fail ? Promise.reject(new ApiError(500, "db locked")) : Promise.resolve({ system_id: 1, is_mock: false, plans: [] });
      }
      return Promise.resolve(null);
    });
    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    renderAt(<ProbePlannerPage />, "/probe-planner?plan=5");

    const error = await screen.findByTestId("probe-plans-error");
    expect(error).toHaveAttribute("data-failure-kind", "server");
    expect(error).toHaveTextContent("db locked");
    expect(screen.queryByText(/No probe plans yet/)).not.toBeInTheDocument();
    const { toast } = await import("sonner");
    expect(toast.error).not.toHaveBeenCalledWith(expect.stringContaining("was not found"));

    fail = false;
    fireEvent.click(within(error).getByRole("button", { name: "再試行" }));
    expect(await screen.findByText(/No probe plans yet/)).toBeInTheDocument();
  });

  test("Probe Planner: 権限エラー (403) も 0 件にしない", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/probe-plans" ? Promise.reject(new ApiError(403, "forbidden")) : Promise.resolve(null),
    );
    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    renderAt(<ProbePlannerPage />, "/probe-planner");
    expect(await screen.findByTestId("probe-plans-error")).toHaveAttribute("data-failure-kind", "forbidden");
  });

  test("Generation: 履歴の取得失敗を『No generation runs yet』にしない", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/components") return Promise.resolve([]);
      if (path.startsWith("/generation-runs")) return Promise.reject(new Error("offline"));
      return Promise.resolve(null);
    });
    const { default: GenerationPage } = await import("@/pages/generation");
    renderAt(<GenerationPage />, "/generation");
    expect(await screen.findByTestId("generation-runs-error")).toBeInTheDocument();
    expect(screen.queryByText("No generation runs yet")).not.toBeInTheDocument();
  });

  test("オフライン (NetworkError) は接続できない旨を示す", async () => {
    const { describeRequestFailure } = await import("@/lib/request-state");
    const offline = Object.assign(new Error("x"), { name: "NetworkError", kind: "offline" });
    expect(describeRequestFailure(offline).kind).toBe("offline");
    expect(describeRequestFailure(new ApiError(503, "down")).kind).toBe("server");
    expect(describeRequestFailure(new ApiError(401, "expired")).kind).toBe("unauthorized");
  });
});

describe("UX-05 / UX-12: Journey Service Blueprint", () => {
  test("Journey 一覧の取得失敗は再試行を出し、『選択してください』や作成 CTA を出さない", async () => {
    let fail = true;
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/ux-design/journeys") {
        return fail ? Promise.reject(new ApiError(500, "boom")) : Promise.resolve({ system_id: 1, journeys: [] });
      }
      return new Promise(() => {});
    });
    const { default: JourneyBlueprintPage } = await import("@/pages/journey-blueprint");
    renderAt(<JourneyBlueprintPage />, "/journey-blueprint");

    const error = await screen.findByTestId("blueprint-journeys-error");
    expect(screen.queryByTestId("blueprint-no-journey")).not.toBeInTheDocument();
    expect(screen.queryByTestId("blueprint-create-journey")).not.toBeInTheDocument();
    fail = false;
    fireEvent.click(within(error).getByRole("button", { name: "再試行" }));
    expect(await screen.findByTestId("blueprint-create-journey")).toBeInTheDocument();
  });

  test("差分の取得失敗は『読み込み中』ではなく失敗として表示する", async () => {
    const { BlueprintDiffPanel } = await import("@/components/journey-blueprint/diff-panel");
    const onRetry = vi.fn();
    const { rerender } = render(<BlueprintDiffPanel diff={undefined} isLoading />);
    expect(screen.getByTestId("blueprint-diff-loading")).toHaveTextContent("読み込んでいます");
    rerender(<BlueprintDiffPanel diff={undefined} error={new ApiError(500, "x")} onRetry={onRetry} />);
    expect(screen.queryByTestId("blueprint-diff-loading")).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByTestId("blueprint-diff-error")).getByRole("button", { name: "再試行" }));
    expect(onRetry).toHaveBeenCalled();
  });
});
