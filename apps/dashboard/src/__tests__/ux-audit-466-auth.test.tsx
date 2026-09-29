/// <reference types="vitest/globals" />
// Issue #466 — UX-06 (認証・System 取得の失敗から再開できる), UX-09 (ログインを
// 挟んでも目的地へ戻る), UX-18 (未知の URL を空白にしない)。
//
// ここでは `@/api/client` と `@/api/auth` をモックしない。失敗の種類の区別
// (応答が無い / 401 / 5xx) は実際の client と AuthProvider の中で決まるので、
// `fetch` だけを差し替えて本物の経路を通す。

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";

type Handler = (init: RequestInit | undefined) => Response | Promise<Response> | "reject";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stubFetch(routes: Record<string, Handler>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const path = url.replace(/^\/api/, "").split("?")[0];
    const method = (init?.method ?? "GET").toUpperCase();
    const handler = routes[`${method} ${path}`] ?? routes[path];
    if (!handler) return Promise.resolve(json(404, { detail: "not found" }));
    const result = handler(init);
    if (result === "reject") return Promise.reject(new TypeError("Failed to fetch"));
    return Promise.resolve(result);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  localStorage.clear();
});

// ── client ──────────────────────────────────────────────────────────

describe("UX-05/06: client は『応答が無い』と『拒否された』を区別する", () => {
  test("fetch が失敗したら NetworkError (network)、書き込みでは結果不明と伝える", async () => {
    stubFetch({ "/components": () => "reject", "POST /experiments": () => "reject" });
    const { api, NetworkError } = await import("@/api/client");
    await expect(api.get("/components")).rejects.toMatchObject({ name: "NetworkError", kind: "network", resultUnknown: false });
    const err = (await api.post("/experiments", {}).catch((e: unknown) => e)) as InstanceType<typeof NetworkError>;
    expect(err).toBeInstanceOf(NetworkError);
    expect(err.resultUnknown).toBe(true);
    expect(err.message).toContain("処理結果は不明です");
  });

  test("オフラインなら kind=offline", async () => {
    stubFetch({ "/components": () => "reject" });
    const onLine = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const { api } = await import("@/api/client");
    await expect(api.get("/components")).rejects.toMatchObject({ kind: "offline" });
    onLine.mockRestore();
  });

  test("timeoutMs を超えた読み取りは kind=timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_input: unknown, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    })));
    const { api } = await import("@/api/client");
    const pending = api.get("/auth/me", { timeoutMs: 1000 }).catch((e) => e);
    await vi.advanceTimersByTimeAsync(1001);
    expect(await pending).toMatchObject({ name: "NetworkError", kind: "timeout" });
  });

  test("利用中の 401 は通知し、認証エンドポイント自身の 401 は通知しない", async () => {
    stubFetch({
      "/components": () => json(401, { detail: "expired" }),
      "POST /auth/login": () => json(401, { detail: "bad password" }),
    });
    const { api } = await import("@/api/client");
    const { UNAUTHORIZED_EVENT } = await import("@/lib/auth-events");
    const listener = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, listener);
    await api.get("/components").catch(() => {});
    expect(listener).toHaveBeenCalledTimes(1);
    await api.post("/auth/login", {}).catch(() => {});
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(UNAUTHORIZED_EVENT, listener);
  });
});

// ── AuthProvider ────────────────────────────────────────────────────

async function renderWithAuth(ui: ReactNode, route = "/") {
  const { AuthProvider } = await import("@/api/auth");
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>{ui}</AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let useAuthRef: (typeof import("@/api/auth"))["useAuth"];

function AuthProbe() {
  const auth = useAuthRef();
  return (
    <div>
      <span data-testid="probe-loading">{String(auth.loading)}</span>
      <span data-testid="probe-user">{auth.user?.username ?? "none"}</span>
      <span data-testid="probe-auth-error">{auth.authError ?? ""}</span>
      <span data-testid="probe-systems-status">{auth.systemsStatus}</span>
      <span data-testid="probe-systems-count">{auth.systems.length}</span>
      <span data-testid="probe-expired">{String(auth.sessionExpired)}</span>
      <button type="button" onClick={() => auth.login("dev", "pw")}>login</button>
    </div>
  );
}

describe("UX-06: AuthProvider の状態", () => {
  beforeEach(async () => {
    useAuthRef = (await import("@/api/auth")).useAuth;
  });

  test("サーバーに到達できないのは『未ログイン』ではなく authError", async () => {
    stubFetch({ "/auth/me": () => "reject" });
    await renderWithAuth(<AuthProbe />);
    await waitFor(() => expect(screen.getByTestId("probe-loading")).toHaveTextContent("false"));
    expect(screen.getByTestId("probe-user")).toHaveTextContent("none");
    expect(screen.getByTestId("probe-auth-error")).toHaveTextContent("ログイン状態を確認できませんでした");
  });

  test("/systems の失敗は 0 件ではなく systemsStatus=error", async () => {
    stubFetch({
      "/auth/me": () => json(200, { user: { id: 1, username: "dev", role: "user" }, auth: "session" }),
      "/systems": () => json(500, { detail: "db down" }),
    });
    await renderWithAuth(<AuthProbe />);
    await waitFor(() => expect(screen.getByTestId("probe-systems-status")).toHaveTextContent("error"));
    expect(screen.getByTestId("probe-loading")).toHaveTextContent("false");
    const { systemsKnownEmpty } = await import("@/lib/systems-state");
    expect(systemsKnownEmpty({ systems: [], systemsStatus: "error" })).toBe(false);
    expect(systemsKnownEmpty({ systems: [], systemsStatus: "ready" })).toBe(true);
    expect(systemsKnownEmpty({ systems: [], systemsStatus: "loading" })).toBe(false);
  });

  test("利用中の 401 は sessionExpired になり、多重に発生しても 1 状態。再ログインで解除", async () => {
    stubFetch({
      "/auth/me": () => json(200, { user: { id: 1, username: "dev", role: "user" }, auth: "session" }),
      "/systems": () => json(200, [{ id: 1, name: "alpha" }]),
      "/components": () => json(401, { detail: "expired" }),
      "POST /auth/login": () => json(200, { expires_at: 1 }),
    });
    const { ReauthDialog } = await import("@/components/layout/auth-recovery");
    await renderWithAuth(
      <>
        <AuthProbe />
        <input aria-label="編集中の入力" defaultValue="" />
        <ReauthDialog />
      </>,
    );
    await waitFor(() => expect(screen.getByTestId("probe-systems-count")).toHaveTextContent("1"));
    fireEvent.change(screen.getByLabelText("編集中の入力"), { target: { value: "保存前のメモ" } });
    const { api } = await import("@/api/client");
    await Promise.all([api.get("/components"), api.get("/components"), api.get("/components")].map((p) => p.catch(() => {})));
    await waitFor(() => expect(screen.getByTestId("probe-expired")).toHaveTextContent("true"));
    // 再認証ダイアログは 1 つだけ。画面の入力はそのまま。
    expect(screen.getAllByRole("dialog", { name: "ログインの有効期限が切れました" })).toHaveLength(1);
    expect(screen.getByLabelText("パスワード")).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "ログインの有効期限が切れました" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("パスワード"), { target: { value: "pw" } });
    await act(async () => { fireEvent.click(screen.getByTestId("reauth-submit")); });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByLabelText("編集中の入力")).toHaveValue("保存前のメモ");
    await waitFor(() => expect(screen.getByTestId("probe-expired")).toHaveTextContent("false"));
    expect(screen.getByTestId("probe-user")).toHaveTextContent("dev");
  });
});

// ── UX-09: 復帰先 ───────────────────────────────────────────────────

describe("UX-09: ログイン後の復帰先はアプリ内に限る", () => {
  test("safeReturnPath", async () => {
    const { safeReturnPath, loginPathFor } = await import("@/lib/return-to");
    expect(safeReturnPath("/experiments?experiment=3#decision")).toBe("/experiments?experiment=3#decision");
    expect(safeReturnPath("https://evil.example/")).toBeNull();
    expect(safeReturnPath("//evil.example/x")).toBeNull();
    expect(safeReturnPath("/\\evil")).toBeNull();
    expect(safeReturnPath("/login?next=/x")).toBeNull();
    expect(safeReturnPath("javascript:alert(1)")).toBeNull();
    expect(loginPathFor({ pathname: "/workspaces", search: "?open=4", hash: "#c" }))
      .toBe("/login?next=%2Fworkspaces%3Fopen%3D4%23c");
    expect(loginPathFor({ pathname: "/", search: "", hash: "" })).toBe("/login");
  });

  function LocationEcho() {
    const location = useLocation();
    return <p data-testid="landed">{`${location.pathname}${location.search}${location.hash}`}</p>;
  }

  test("対象付き URL → ログイン → 同じ対象の画面へ戻る", async () => {
    let loggedIn = false;
    stubFetch({
      "/auth/me": () => json(200, loggedIn
        ? { user: { id: 1, username: "dev", role: "user" }, auth: "session" }
        : { user: null, auth: "anonymous" }),
      "/auth/bootstrap-status": () => json(200, { admin_exists: true, environment: "development", llm_configured: true }),
      "/systems": () => json(200, [{ id: 1, name: "alpha" }]),
      "POST /auth/login": () => { loggedIn = true; return json(200, { expires_at: 1 }); },
    });
    const { AppLayout } = await import("@/components/layout/app-layout");
    const { default: LoginPage } = await import("@/pages/login");
    // AppLayout 全体ではなく、リダイレクトの判断だけを見るための軽量な
    // レイアウト: AppLayout が user=null で返す Navigate をそのまま使う。
    await renderWithAuth(
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<AppLayout />}>
          <Route path="experiments" element={<LocationEcho />} />
        </Route>
      </Routes>,
      "/experiments?experiment=3#decision",
    );

    expect(await screen.findByTestId("login-return-to")).toHaveTextContent("/experiments?experiment=3#decision");
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "dev" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Sign in" })); });
    expect(await screen.findByTestId("landed", {}, { timeout: 3000 })).toHaveTextContent("/experiments?experiment=3#decision");
  });

  test("サーバーに到達できないときはログインフォームではなく再試行を出す", async () => {
    let reachable = false;
    stubFetch({
      "/auth/me": () => (reachable ? json(200, { user: null, auth: "anonymous" }) : "reject"),
      "/auth/bootstrap-status": () => json(200, { admin_exists: true, environment: "development", llm_configured: true }),
    });
    const { default: LoginPage } = await import("@/pages/login");
    await renderWithAuth(<Routes><Route path="/login" element={<LoginPage />} /></Routes>, "/login");
    expect(await screen.findByTestId("auth-bootstrap-error")).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    reachable = true;
    fireEvent.click(screen.getByTestId("auth-bootstrap-retry"));
    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
  });
});

// ── UX-18: 未知の URL ───────────────────────────────────────────────

describe("UX-18: 未知の URL は『ページが見つかりません』と復帰導線を出す", () => {
  test("NotFoundPage は開いた URL と Overview への導線を示す", async () => {
    const { default: NotFoundPage } = await import("@/pages/not-found");
    render(
      <MemoryRouter initialEntries={["/no-such-page?x=1"]}>
        <Routes><Route path="*" element={<NotFoundPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "ページが見つかりません" })).toBeInTheDocument();
    expect(screen.getByTestId("not-found-path")).toHaveTextContent("/no-such-page?x=1");
    expect(screen.getByTestId("not-found-overview")).toHaveAttribute("href", "/");
  });

  test("App のルーティングは未知のパスを AppLayout 内の NotFound へ回す", async () => {
    stubFetch({
      "/auth/me": () => json(200, { user: { id: 1, username: "dev", role: "user" }, auth: "session" }),
      "/systems": () => json(200, [{ id: 1, name: "alpha" }]),
    });
    const { default: App } = await import("@/App");
    await renderWithAuth(<App />, "/typo-page");
    expect(await screen.findByTestId("not-found", {}, { timeout: 5000 })).toBeInTheDocument();
    // ヘッダーは残っている (AppLayout の中)。
    expect(screen.getByRole("banner")).toBeInTheDocument();
  });
});
