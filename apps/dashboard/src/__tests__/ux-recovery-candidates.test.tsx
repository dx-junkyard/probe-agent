import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, Link, Outlet, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UiDraftProvider } from "@/lib/ui-draft";
import WorkbenchPage from "@/pages/simulation-workbench";
import CandidatePage from "@/pages/candidate-studio";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
vi.mock("@/api/client", () => ({ api, getSystemId: () => 1 }));
vi.mock("@/components/replay-approval-panel", () => ({ ApprovalPanel: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const source = (id = 1) => ({ replay_set_id: id, component_id: "normalize", snapshot_id: 1,
  commit_sha: "abc123456789", path: `source-${id}.py`, qualified_name: "normalize", start_line: 1, end_line: 2,
  source: `def normalize():\n    return ${id}\n` });
const set = (id: number) => ({ id, name: `Set ${id}`, component_id: "normalize", trace_ids: [], traces: [] });
const session = (id = 1) => ({ id, component_id: "normalize", objective: "元の改善目標", snapshot_id: 1,
  commit_sha: "abc123456789", symbol_path: "source.py", symbol_qualified_name: "normalize", messages: [], versions: [] });
const completedRun = { id: 10, replay_set_id: 1, status: "completed", variants: [] };

function response(path: string): unknown {
  if (path === "/replay-sets") return [set(1), set(2)];
  if (path === "/replay-sets/1") return set(1);
  if (path === "/replay-sets/2") return set(2);
  if (path === "/replay-sets/1/source") return source(1);
  if (path === "/replay-sets/2/source") return source(2);
  if (path.includes("/replay-approval")) return { active: true };
  if (path.startsWith("/replay-variant-runs?")) return [];
  if (path.startsWith("/candidate-sessions/")) return session(Number(path.split("/")[2]));
  if (path === "/components") return [{ component_id: "normalize", trace_count: 1 }];
  if (path.includes("/traces")) return [];
  return null;
}

function mount(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const router = createMemoryRouter([{ element: <UiDraftProvider><Link to="/elsewhere">別画面へ</Link><Outlet /></UiDraftProvider>, children: [
    { path: "/simulation-workbench", element: <WorkbenchPage /> },
    { path: "/candidate-studio", element: <CandidatePage /> },
    { path: "/elsewhere", element: <p>別画面</p> },
  ] }], { initialEntries: [path] });
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
  return { qc, router };
}

beforeEach(() => {
  vi.restoreAllMocks();
  api.get.mockReset().mockImplementation(async (path: string) => response(path));
  api.post.mockReset();
});

describe("候補編集の入力保護と復旧", () => {
  it("Replay Set切替をキャンセルすると入力を保持し、了承時だけ新しい対象へ移る", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { router } = mount("/simulation-workbench?replay_set_id=1");
    const editor = await screen.findByLabelText("候補ソース");
    await waitFor(() => expect(editor).toHaveValue(source().source));
    fireEvent.change(editor, { target: { value: "作業中のソース" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Replay Set" }), { target: { value: "2" } });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(router.state.location.search).toBe("?replay_set_id=1");
    expect(editor).toHaveValue("作業中のソース");
    confirm.mockReturnValue(true);
    fireEvent.change(screen.getByRole("combobox", { name: "Replay Set" }), { target: { value: "2" } });
    await waitFor(() => expect(screen.getByLabelText("候補ソース")).toHaveValue(source(2).source));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(router.state.location.search).toBe("?replay_set_id=2");
  });

  it.each(["パッチを貼り付け", "LLM draft"])("%s の未保存入力も画面離脱を止める", async (tab) => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { router } = mount("/simulation-workbench?replay_set_id=1");
    fireEvent.click(await screen.findByRole("tab", { name: tab }));
    const input = tab === "LLM draft"
      ? screen.getByPlaceholderText("候補コードにどう変わってほしいか")
      : screen.getByPlaceholderText("unified diff (git diff形式) を貼り付け...");
    fireEvent.change(input, { target: { value: "未保存の入力" } });
    fireEvent.click(screen.getByRole("link", { name: "別画面へ" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(router.state.location.pathname).toBe("/simulation-workbench");
    expect(input).toHaveValue("未保存の入力");
  });

  it("参照元の再取得でSnapshotが変わっても編集中のソースを上書きしない", async () => {
    const { qc } = mount("/simulation-workbench?replay_set_id=1");
    const editor = await screen.findByLabelText("候補ソース");
    await waitFor(() => expect(editor).toHaveValue(source().source));
    fireEvent.change(editor, { target: { value: "編集中" } });
    api.get.mockImplementation(async (path: string) => path.endsWith("/source") ? { ...source(), snapshot_id: 2, source: "新しいソース" } : response(path));
    await act(async () => { await qc.invalidateQueries(); });
    expect(editor).toHaveValue("編集中");
    expect(screen.getByText(/参照元のSnapshotが変わりました/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it.each(["/replay-sets/1/source", "/replay-variant-runs?replay_set_id=1", "/replay-variant-runs/10"])("取得失敗 %s を未実行へ丸めず再取得できる", async (failedPath) => {
    api.get.mockImplementation(async (path: string) => {
      if (path === failedPath) throw new Error("network failed");
      if (path.startsWith("/replay-variant-runs?")) return [completedRun];
      if (path === "/replay-variant-runs/10") return completedRun;
      return response(path);
    });
    mount("/simulation-workbench?replay_set_id=1");
    const alert = await screen.findByRole("alert");
    const calls = api.get.mock.calls.filter(([path]) => path === failedPath).length;
    fireEvent.click(within(alert).getByRole("button", { name: "再試行" }));
    await waitFor(() => expect(api.get.mock.calls.filter(([path]) => path === failedPath).length).toBeGreaterThan(calls));
    if (failedPath !== "/replay-sets/1/source") {
      expect(screen.queryByText("variantを実行するとdiff matrixが表示されます。")).not.toBeInTheDocument();
      expect(screen.queryByText("先に候補variantを実行してください。")).not.toBeInTheDocument();
    }
  });

  it("Workbenchの完了runを読んだら採否の段階を示す", async () => {
    api.get.mockImplementation(async (path: string) => path.startsWith("/replay-variant-runs?") ? [completedRun]
      : path === "/replay-variant-runs/10" ? completedRun : response(path));
    mount("/simulation-workbench?replay_set_id=1");
    await waitFor(() => expect(screen.getByTestId("improvement-loop-rail")).toHaveAttribute("data-current-stage", "decide"));
  });

  it("Candidateの送信後に書き足した指示を消さず、離脱を保護する", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    let resolve!: (value: unknown) => void;
    api.post.mockImplementation(() => new Promise(r => { resolve = r; }));
    const { router } = mount("/candidate-studio?session_id=1");
    const input = await screen.findByPlaceholderText("改善指示を入力（送信は会話の追記のみ。候補を作るには「候補を生成」）");
    fireEvent.change(input, { target: { value: "送信する指示" } });
    fireEvent.click(screen.getByRole("button", { name: "送信" }));
    fireEvent.change(input, { target: { value: "送信中に書いた次の指示" } });
    await act(async () => { resolve({}); });
    expect(input).toHaveValue("送信中に書いた次の指示");
    fireEvent.click(screen.getByRole("link", { name: "別画面へ" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(router.state.location.pathname).toBe("/candidate-studio");
  });

  it("開始フォームの成功後は保存済み入力を破棄確認せず作成したsessionへ移る", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    api.post.mockResolvedValue(session(2));
    const { router } = mount("/candidate-studio?component_id=normalize");
    fireEvent.change(await screen.findByPlaceholderText("候補versionにどう変わってほしいか"), { target: { value: "改善目標" } });
    fireEvent.click(screen.getByRole("button", { name: "セッションを開始" }));
    await waitFor(() => expect(router.state.location.search).toBe("?session_id=2"));
    expect(confirm).not.toHaveBeenCalled();
  });
});
