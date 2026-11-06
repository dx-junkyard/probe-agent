/// <reference types="vitest/globals" />
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router-dom";
import { useState } from "react";
import { vi } from "vitest";
import { AuthProvider, useAuth } from "@/api/auth";
import { setSystemId } from "@/api/client";
import { Header } from "@/components/layout/header";
import { Sidebar } from "@/components/layout/sidebar";
import { SystemSelectionRecoveryBanner } from "@/components/layout/auth-recovery";
import { InterviewLoadErrorCard } from "@/pages/interview";
import { UiDraftProvider, useUnsavedChangesGuard } from "@/lib/ui-draft";

let availableSystems = [{ id: 1, name: "alpha" }, { id: 2, name: "beta" }];
let listFailed = false;

function Editor() {
  const [value, setValue] = useState("");
  const { systemId, refreshSystems } = useAuth();
  const location = useLocation();
  useUnsavedChangesGuard("scope-editor", value !== "");
  return <>
    <input aria-label="未保存メモ" value={value} onChange={(event) => setValue(event.target.value)} />
    <p data-testid="selected-system">{systemId}</p>
    <p data-testid="current-location">{location.pathname}{location.search}{location.hash}</p>
    <button onClick={() => refreshSystems()}>一覧を更新する</button>
  </>;
}

function ScopeShell() {
  const { loading, systemId } = useAuth();
  if (loading) return <p>読み込み中</p>;
  return <UiDraftProvider key={systemId}>
    <Header />
    <SystemSelectionRecoveryBanner />
    <Sidebar />
    <Editor />
  </UiDraftProvider>;
}

function renderScope(route = "/interview?session=123#detail") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const router = createMemoryRouter([{ path: "*", element: <AuthProvider><ScopeShell /></AuthProvider> }], { initialEntries: [route] });
  return render(<QueryClientProvider client={qc}><RouterProvider router={router} /></QueryClientProvider>);
}

beforeEach(() => {
  availableSystems = [{ id: 1, name: "alpha" }, { id: 2, name: "beta" }];
  listFailed = false;
  localStorage.clear();
  setSystemId(1);
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^\/api/, "");
    if (path === "/systems" && init?.method === "POST") {
      const created = { id: 3, name: "created" };
      availableSystems = [...availableSystems, created];
      return new Response(JSON.stringify(created), { status: 201, headers: { "Content-Type": "application/json" } });
    }
    const body = path === "/auth/me"
      ? { user: { id: 7, username: "dev", role: "user" }, auth: "session" }
      : path === "/systems" ? (listFailed ? { detail: "db down" } : availableSystems) : null;
    return new Response(JSON.stringify(body), { status: path === "/systems" && listFailed ? 500 : 200, headers: { "Content-Type": "application/json" } });
  }));
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

test("a missing saved System is explicit and can be replaced even with one available option", async () => {
  setSystemId(99);
  availableSystems = [{ id: 2, name: "beta" }];
  renderScope();
  expect(await screen.findByTestId("system-selection-unavailable")).toHaveTextContent("選択していた System は利用できなくなりました");
  const select = screen.getByTestId("header-system-select");
  expect(select).toHaveValue("99");
  expect(within(select).getByRole("option", { name: "選択していた System は利用できません" })).toBeDisabled();
  fireEvent.change(select, { target: { value: "2" } });
  await waitFor(() => expect(screen.getByTestId("selected-system")).toHaveTextContent("2"));
  expect(screen.getByTestId("current-location")).toHaveTextContent(/^\/interview$/);
  expect(screen.queryByTestId("system-selection-unavailable")).not.toBeInTheDocument();
});

test("background invalidation preserves the draft; only an accepted switch clears its entity URL", async () => {
  renderScope();
  fireEvent.change(await screen.findByLabelText("未保存メモ"), { target: { value: "残すメモ" } });
  availableSystems = [{ id: 2, name: "beta" }];
  fireEvent.click(screen.getByRole("button", { name: "一覧を更新する" }));
  await screen.findByTestId("system-selection-unavailable");
  expect(screen.getByLabelText("未保存メモ")).toHaveValue("残すメモ");
  expect(screen.getByTestId("selected-system")).toHaveTextContent("1");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.change(screen.getByTestId("header-system-select"), { target: { value: "2" } });
  expect(screen.getByLabelText("未保存メモ")).toHaveValue("残すメモ");
  expect(screen.getByTestId("current-location")).toHaveTextContent("/interview?session=123#detail");
  confirm.mockReturnValue(true);
  fireEvent.change(screen.getByTestId("header-system-select"), { target: { value: "2" } });
  await waitFor(() => expect(screen.getByTestId("selected-system")).toHaveTextContent("2"));
  expect(screen.getByTestId("current-location")).toHaveTextContent(/^\/interview$/);
  expect(confirm).toHaveBeenCalledTimes(2); // one cancellation, one acceptance
});

test("a failed list refresh does not claim the selected System disappeared", async () => {
  renderScope();
  await screen.findByLabelText("未保存メモ");
  listFailed = true;
  fireEvent.click(screen.getByRole("button", { name: "一覧を更新する" }));
  await waitFor(() => expect(screen.getByTestId("header-system-select")).toHaveValue("1"));
  expect(screen.queryByTestId("system-selection-unavailable")).not.toBeInTheDocument();
});

test("creating the first System does not bypass the unsaved draft confirmation during list refresh", async () => {
  setSystemId(null);
  availableSystems = [];
  renderScope();
  fireEvent.change(await screen.findByLabelText("未保存メモ"), { target: { value: "初回のメモ" } });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByTestId("header-create-system-button"));
  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "created" } });
  fireEvent.click(screen.getByRole("button", { name: "Create", exact: true }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(confirm).toHaveBeenCalledOnce();
  expect(screen.getByLabelText("未保存メモ")).toHaveValue("初回のメモ");
  expect(screen.getByTestId("selected-system")).toHaveTextContent("");
  expect(screen.getByTestId("current-location")).toHaveTextContent("/interview?session=123#detail");
  expect(screen.getByTestId("header-system-select")).toHaveValue("");
});

test("System Map is searchable by purpose and remains reachable in the collapsed rail", async () => {
  renderScope("/system-map");
  const search = await screen.findByTestId("sidebar-search");
  for (const query of ["システムマップ", "system map", "全体"]) {
    fireEvent.change(search, { target: { value: query } });
    expect(within(screen.getByTestId("sidebar-search-results")).getByRole("link", { name: /システムマップ/ })).toHaveAttribute("href", "/system-map");
  }
  fireEvent.change(search, { target: { value: "" } });
  fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
  expect(within(screen.getByTestId("sidebar-nav")).getByRole("link", { name: "システムマップ" })).toHaveAttribute("href", "/system-map");
});

test.each([403, 404])("Interview HTTP %s offers returning to sessions instead of network retry", (status) => {
  const onReturn = vi.fn();
  render(<InterviewLoadErrorCard error={{ status, detail: "refused" }} sessionsFailed={false} selectedSessionId={123} onRetry={vi.fn()} onReturnToSessions={onReturn} />);
  expect(screen.getByTestId("interview-load-error")).toHaveTextContent("この System では指定されたセッションを開けません");
  expect(screen.queryByTestId("interview-load-error-retry")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "この System のセッション一覧へ戻る" }));
  expect(onReturn).toHaveBeenCalledOnce();
});

test("Interview server failure remains retryable and does not assert a missing session", () => {
  const retry = vi.fn();
  render(<InterviewLoadErrorCard error={{ status: 500, detail: "db down" }} sessionsFailed={false} selectedSessionId={123} onRetry={retry} onReturnToSessions={vi.fn()} />);
  expect(screen.getByTestId("interview-load-error")).toHaveTextContent("サーバーでエラーが発生したため");
  expect(screen.queryByTestId("interview-load-error-return")).not.toBeInTheDocument();
  fireEvent.click(screen.getByTestId("interview-load-error-retry"));
  expect(retry).toHaveBeenCalledOnce();
});
