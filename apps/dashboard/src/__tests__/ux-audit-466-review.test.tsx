/// <reference types="vitest/globals" />
// Issue #466 レビュー指摘の回帰テスト (client / auth / router は本物を使う)。
//   R1: 再認証の POST 成功後に `/auth/me` が失敗しても、画面 (と入力) を
//       アンマウントせず、ダイアログ内でやり直せる。
//   R2: 戻る/進む (POP)・navigate (PUSH)・replace (REPLACE) も未保存入力の
//       破棄確認を通る。キャンセルで URL と入力が残り、了承で遷移する。

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, RouterProvider, Routes, createMemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { vi } from "vitest";
import { useState } from "react";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

// ── R1 ──────────────────────────────────────────────────────────────

describe("R1: 再認証直後の /auth/me 失敗で入力を失わない", () => {
  type MeMode = "ok" | "500" | "network";

  function stubServer() {
    const state = { expired: false, me: "ok" as MeMode, meUserId: 1 };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      const path = url.replace(/^\/api/, "").split("?")[0];
      const method = (init?.method ?? "GET").toUpperCase();
      if (path === "/auth/me") {
        if (state.me === "network") return Promise.reject(new TypeError("Failed to fetch"));
        if (state.me === "500") return Promise.resolve(json(500, { detail: "boom" }));
        return Promise.resolve(json(200, { user: { id: state.meUserId, username: "dev", role: "user" }, auth: "session" }));
      }
      if (path === "/auth/login" && method === "POST") {
        state.expired = false;
        return Promise.resolve(json(200, { expires_at: 1 }));
      }
      if (path === "/systems") return Promise.resolve(json(200, [{ id: 1, name: "alpha" }]));
      if (state.expired) return Promise.resolve(json(401, { detail: "expired" }));
      return Promise.resolve(json(404, { detail: "not found" }));
    }));
    return state;
  }

  function EditingPage() {
    return <input aria-label="編集中の入力" defaultValue="" />;
  }

  async function renderApp() {
    const { AuthProvider } = await import("@/api/auth");
    const { AppLayout } = await import("@/components/layout/app-layout");
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/settings"]}>
          <AuthProvider>
            <Routes>
              <Route element={<AppLayout />}>
                <Route path="settings" element={<EditingPage />} />
              </Route>
            </Routes>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return screen.findByLabelText("編集中の入力", {}, { timeout: 5000 });
  }

  for (const failure of ["500", "network"] as const) {
    test(`POST 成功 → /auth/me ${failure} → 同じ利用者で復帰するまで入力を保つ`, async () => {
      const server = stubServer();
      const input = await renderApp();
      fireEvent.change(input, { target: { value: "unsaved work" } });

      server.expired = true;
      const { api } = await import("@/api/client");
      await api.get("/components").catch(() => {});
      const dialog = await screen.findByRole("dialog", { name: "ログインの有効期限が切れました" });

      server.me = failure;
      fireEvent.change(screen.getByLabelText("パスワード"), { target: { value: "pw" } });
      await act(async () => { fireEvent.click(screen.getByTestId("reauth-submit")); });

      // ダイアログは残り、理由を示す。画面は接続エラーへ置き換わらない。
      await waitFor(() => expect(dialog).toHaveTextContent("ログイン状態を確認できませんでした"));
      expect(screen.queryByTestId("auth-bootstrap-error")).not.toBeInTheDocument();
      expect(screen.getByLabelText("編集中の入力")).toHaveValue("unsaved work");

      // 回復後、同じ利用者と確認できたら再開する。
      server.me = "ok";
      await act(async () => { fireEvent.click(screen.getByTestId("reauth-submit")); });
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(screen.getByLabelText("編集中の入力")).toHaveValue("unsaved work");
    });
  }

  test("別の利用者で再ログインした場合は前の利用者の入力を持ち越さない", async () => {
    const server = stubServer();
    const input = await renderApp();
    fireEvent.change(input, { target: { value: "user 1 draft" } });
    server.expired = true;
    const { api } = await import("@/api/client");
    await api.get("/components").catch(() => {});
    await screen.findByRole("dialog", { name: "ログインの有効期限が切れました" });

    server.meUserId = 2;
    fireEvent.change(screen.getByLabelText("パスワード"), { target: { value: "pw" } });
    await act(async () => { fireEvent.click(screen.getByTestId("reauth-submit")); });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // UiDraftProvider は user.id を key に持つので、ページは作り直される。
    expect(await screen.findByLabelText("編集中の入力")).toHaveValue("");
  });
});

// ── R2 ──────────────────────────────────────────────────────────────

describe("R2: すべての種類のアプリ内遷移が未保存入力の破棄確認を通る", () => {
  async function renderRouter(initial: string[]) {
    const { UiDraftProvider, useUnsavedChangesGuard, useUiDraftRegistry } = await import("@/lib/ui-draft");

    function Editor() {
      const [text, setText] = useState("");
      useUnsavedChangesGuard("review-r2-editor", text !== "");
      const navigate = useNavigate();
      const drafts = useUiDraftRegistry();
      return (
        <div>
          <input aria-label="下書き" value={text} onChange={(e) => setText(e.target.value)} />
          <button type="button" onClick={() => navigate(-1)}>戻る</button>
          <button type="button" onClick={() => navigate("/other")}>別画面へ</button>
          <button type="button" onClick={() => navigate("/other", { replace: true })}>置き換えて移動</button>
          <button type="button" onClick={() => navigate("/editor#section")}>同じ画面内</button>
          <button
            type="button"
            onClick={() => { if (drafts?.confirmDiscard?.() !== false) navigate("/other"); }}
          >
            確認してから移動
          </button>
        </div>
      );
    }
    function Where() {
      const location = useLocation();
      return <p data-testid="where">{location.pathname}</p>;
    }
    function Shell() {
      return (
        <UiDraftProvider>
          <Routes>
            <Route path="/editor" element={<Editor />} />
            <Route path="*" element={<p>別の画面</p>} />
          </Routes>
          <Where />
        </UiDraftProvider>
      );
    }
    const router = createMemoryRouter([{ path: "*", element: <Shell /> }], { initialEntries: initial, initialIndex: initial.length - 1 });
    render(<RouterProvider router={router} />);
    return router;
  }

  const cases: Array<[string, string]> = [
    ["POP (戻る)", "戻る"],
    ["PUSH", "別画面へ"],
    ["REPLACE", "置き換えて移動"],
  ];

  for (const [label, button] of cases) {
    test(`${label}: キャンセルなら URL と入力が残り、了承なら遷移する`, async () => {
      await renderRouter(["/start", "/editor"]);
      fireEvent.change(screen.getByLabelText("下書き"), { target: { value: "書きかけ" } });

      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: button })); });
      await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
      expect(screen.getByTestId("where")).toHaveTextContent("/editor");
      expect(screen.getByLabelText("下書き")).toHaveValue("書きかけ");

      confirm.mockReturnValue(true);
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: button })); });
      await waitFor(() => expect(screen.getByTestId("where")).not.toHaveTextContent("/editor"));
    });
  }

  test("ブラウザー履歴の戻る (router の POP) も止める", async () => {
    const router = await renderRouter(["/start", "/editor"]);
    fireEvent.change(screen.getByLabelText("下書き"), { target: { value: "書きかけ" } });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await act(async () => { await router.navigate(-1); });
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(screen.getByTestId("where")).toHaveTextContent("/editor");
    expect(screen.getByLabelText("下書き")).toHaveValue("書きかけ");
  });

  test("未保存入力が無ければ確認しない。ハッシュだけの移動も確認しない", async () => {
    await renderRouter(["/start", "/editor"]);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.change(screen.getByLabelText("下書き"), { target: { value: "x" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "同じ画面内" })); });
    expect(confirm).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("下書き"), { target: { value: "" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "別画面へ" })); });
    expect(confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/other"));
  });

  test("画面側で了承を得た直後の遷移では確認を二重に出さない", async () => {
    await renderRouter(["/start", "/editor"]);
    fireEvent.change(screen.getByLabelText("下書き"), { target: { value: "書きかけ" } });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "確認してから移動" })); });
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/other"));
    expect(confirm).toHaveBeenCalledTimes(1);
  });
});
