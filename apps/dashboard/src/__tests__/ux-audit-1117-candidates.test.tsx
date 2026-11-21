// UX audit 2026-11-17 第 3 波: C8 (stale Snapshot の理由を Replay まで運ぶ)、
// C13 (候補を試す入口の整理)、F6 (Replay Set の「追加」は新しい版の作成)。
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UiDraftProvider } from "@/lib/ui-draft";
import WorkbenchPage from "@/pages/simulation-workbench";
import CandidatePage from "@/pages/candidate-studio";
import { ReplayRowActions } from "@/components/replay-row-actions";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() }));
vi.mock("@/api/client", () => ({ api, getSystemId: () => 1 }));
vi.mock("@/components/replay-approval-panel", () => ({ ApprovalPanel: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const stalePreflight = {
  snapshot_id: 1, processing_state: "ready", freshness: "stale", commit_sha: "abc123456789",
  head_sha: "def123456789", head_relation: "behind", commits_behind: 1, verdict: "attention",
  checks: [], recommended_snapshot_id: 1, recommended_snapshot_commit_sha: "abc123456789",
  recommended_snapshot_freshness: "stale", is_recommended: true,
  requires_stale_acknowledgement: true, stale_continuation_note: "HEAD より古い Snapshot です",
};
const currentPreflight = {
  ...stalePreflight, freshness: "current", verdict: "ready",
  requires_stale_acknowledgement: false, stale_continuation_note: null,
};

const version = {
  id: 5, system_id: 1, session_id: 1, parent_version_id: null, version_number: 1,
  instruction: "x", status: "proposed", summary: "", assumptions: [], changed_symbols: [],
  risks: [], suggested_tests: [], generated_code: "", patch_text: "--- a\n+++ b\n",
  patch_hash: "h", error: null, replay_status: "not_run", replay_run_id: null,
  replay_variant_id: null, promoted_at: null, is_mock: true, created_at: 1,
};
const session = (overrides: Record<string, unknown> = {}) => ({
  id: 1, system_id: 1, component_id: "normalize", objective: "", snapshot_id: 1,
  commit_sha: "abc123456789", symbol_path: "svc.py", symbol_qualified_name: "normalize",
  replay_set_id: 1, status: "active", created_at: 1_700_000_000, updated_at: 1_700_000_000,
  messages: [], versions: [version], ...overrides,
});

let replayPreflight: Record<string, unknown> = {};
let preflight: Record<string, unknown> = currentPreflight;
let sessionsResponse: unknown = [];

function response(path: string): unknown {
  if (path.startsWith("/snapshot-preflight")) return preflight;
  if (path === "/candidate-sessions") return sessionsResponse;
  if (path.endsWith("/replay-preflight")) return replayPreflight;
  if (path.startsWith("/candidate-sessions/")) return session();
  if (path.includes("/replay-approval")) return { active: true };
  if (path === "/replay-sets") return [];
  if (path === "/replay-sets/1") return { id: 1, name: "Set", component_id: "normalize", trace_ids: [], traces: [], created_at: 1 };
  if (path === "/replay-sets/1/source") return {
    replay_set_id: 1, component_id: "normalize", snapshot_id: 1, commit_sha: "abc123456789",
    path: "svc.py", qualified_name: "normalize", start_line: 1, end_line: 2, source: "def f():\n    return 1\n",
  };
  if (path.startsWith("/replay-variant-runs?")) return [];
  if (path === "/components") return [];
  if (path.includes("/traces")) return [];
  return null;
}

function mount(path: string, element?: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const router = createMemoryRouter([{ element: <UiDraftProvider><Outlet /></UiDraftProvider>, children: [
    { path: "/simulation-workbench", element: <WorkbenchPage /> },
    { path: "/candidate-studio", element: <CandidatePage /> },
    { path: "/components", element: element ?? <p>components</p> },
    { path: "/experiments", element: <p>experiments</p> },
  ] }], { initialEntries: [path] });
  render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
  return { router };
}

beforeEach(() => {
  vi.restoreAllMocks();
  replayPreflight = {};
  preflight = currentPreflight;
  sessionsResponse = [];
  api.get.mockReset().mockImplementation(async (path: string) => response(path));
  api.post.mockReset();
});

describe("C8: stale Snapshot の理由と候補 Replay", () => {
  it("HEAD が変わっていなければセッションの理由を引き継ぎ、入力なしで Replay できる", async () => {
    replayPreflight = {
      preflight: stalePreflight, stale_reason_state: "inheritable",
      session_stale_ack_reason: "当時のコードで再現する", session_head_sha_at_creation: "def123456789",
    };
    api.post.mockResolvedValue({ ...version, replay_status: "completed" });
    mount("/candidate-studio?session_id=1");
    expect(await screen.findByTestId("candidate-replay-stale-inherited")).toHaveTextContent("当時のコードで再現する");
    expect(screen.queryByLabelText("この Snapshot を使う理由（必須）")).not.toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Replayで確認" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/candidate-versions/5/replay", {}));
  });

  it("HEAD が進んでいれば理由を求め、空のままでは Replay できず、入力した理由を送る", async () => {
    replayPreflight = {
      preflight: stalePreflight, stale_reason_state: "head_moved",
      session_stale_ack_reason: "当時のコードで再現する", session_head_sha_at_creation: "000",
    };
    api.post.mockResolvedValue({ ...version, replay_status: "completed" });
    mount("/candidate-studio?session_id=1");
    const reason = await screen.findByLabelText("この Snapshot を使う理由（必須）");
    // Never pre-filled from the session's earlier decision.
    expect(reason).toHaveValue("");
    expect(screen.getByTestId("candidate-replay-stale-ack")).toHaveTextContent("HEAD が進んだ");
    const button = screen.getByRole("button", { name: "Replayで確認" });
    expect(button).toBeDisabled();
    fireEvent.change(reason, { target: { value: "更新後も旧挙動を確認" } });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/candidate-versions/5/replay", { stale_snapshot_reason: "更新後も旧挙動を確認" },
    ));
  });

  it("Workbench は stale のとき理由の入力欄を出し、入力するまで Run を無効にする", async () => {
    preflight = stalePreflight;
    api.post.mockImplementation(async (path: string) => path === "/replay-source-diff"
      ? { patch_text: "diff" } : { id: 9, status: "completed", variants: [] });
    mount("/simulation-workbench?replay_set_id=1");
    const editor = await screen.findByLabelText("候補ソース");
    await waitFor(() => expect(editor).toHaveValue("def f():\n    return 1\n"));
    fireEvent.change(editor, { target: { value: "def f():\n    return 2\n" } });
    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    expect(screen.getByTestId("workbench-stale-ack-missing")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("この Snapshot を使う理由（必須）"), { target: { value: "再現のため" } });
    await waitFor(() => expect(run).toBeEnabled());
    fireEvent.click(run);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith(
      "/replay-variant-runs", expect.objectContaining({ stale_snapshot_reason: "再現のため" }),
    ));
    expect(api.get).toHaveBeenCalledWith("/snapshot-preflight?snapshot_id=1");
  });
});

describe("C13: Candidate Studio の既存セッション一覧", () => {
  it("一覧に component・最新 version の状態を出し、選ぶとセッションを開く", async () => {
    sessionsResponse = [session({
      id: 3, messages: [], versions: [], version_count: 2, latest_version_number: 2,
      latest_version_status: "proposed", latest_version_replay_status: "completed",
    })];
    const { router } = mount("/candidate-studio");
    const row = await screen.findByTestId("candidate-session-row-3");
    expect(row).toHaveTextContent("normalize");
    expect(row).toHaveTextContent("最新 v2 · Replay完了");
    expect(row).toHaveTextContent("進行中");
    fireEvent.click(row);
    await waitFor(() => expect(router.state.location.search).toBe("?session_id=3"));
  });

  it("0 件と取得失敗を別の表示にする", async () => {
    mount("/candidate-studio");
    expect(await screen.findByTestId("candidate-session-list-empty")).toBeInTheDocument();
  });

  it("取得失敗は再試行できるエラーとして表示する", async () => {
    api.get.mockImplementation(async (path: string) => {
      if (path === "/candidate-sessions") throw new Error("network failed");
      return response(path);
    });
    mount("/candidate-studio");
    const list = await screen.findByTestId("candidate-session-list");
    await waitFor(() => expect(within(list).getByRole("button", { name: "再試行" })).toBeInTheDocument());
    expect(screen.queryByTestId("candidate-session-list-empty")).not.toBeInTheDocument();
  });
});

describe("C13 / F6: Trace 行の操作", () => {
  it("主操作は Candidate Studio の 1 つで、他の操作は「その他の操作」にまとめる", async () => {
    const { router } = mount("/components", <ReplayRowActions componentId="normalize" traceId="t1" />);
    const primary = await screen.findByRole("button", { name: "この入力から改善する" });
    const more = screen.getByTestId("replay-row-more-actions");
    for (const label of ["Replay", "Replay Setに追加", "Experimentを作成"]) {
      expect(within(more).getByRole("button", { name: label })).toBeInTheDocument();
    }
    // The only top-level button of the row is the primary action.
    expect(primary.parentElement?.querySelectorAll(":scope > button")).toHaveLength(1);
    fireEvent.click(primary);
    await waitFor(() => expect(router.state.location.pathname).toBe("/candidate-studio"));
    expect(router.state.location.search).toBe("?component_id=normalize&trace_id=t1");
  });

  it("既存の Set を元にする操作は新しい版の作成と明示し、Set を作成日時と件数で見分けられる", async () => {
    api.get.mockImplementation(async (path: string) => path.startsWith("/replay-sets")
      ? [{ id: 4, name: "同名", component_id: "normalize", trace_ids: ["a", "b"], traces: [], created_at: 1_700_000_000 }]
      : response(path));
    api.post.mockResolvedValue({ id: 8, name: "同名", component_id: "normalize", trace_ids: ["a", "b", "t1"], traces: [], created_at: 2 });
    mount("/components", <ReplayRowActions componentId="normalize" traceId="t1" />);
    fireEvent.click(within(await screen.findByTestId("replay-row-more-actions")).getByRole("button", { name: "Replay Setに追加" }));
    const dialog = await screen.findByRole("dialog");
    const existing = within(dialog).getByRole("button", { name: "既存のSetを元にする" });
    await waitFor(() => expect(existing).toBeEnabled());
    fireEvent.click(existing);
    const select = within(dialog).getByRole("combobox");
    expect(within(select).getByRole("option", { name: /同名 #4 · 2件 · / })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "4" } });
    expect(within(dialog).getByTestId("replay-set-new-version-note")).toHaveTextContent("選んだSetは変更されません");
    fireEvent.click(within(dialog).getByRole("button", { name: "このTraceを加えた新しい版を作成" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/replay-sets", {
      component_id: "normalize", name: "同名", trace_ids: ["a", "b", "t1"],
    }));
  });
});
