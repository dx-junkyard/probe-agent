/// <reference types="vitest/globals" />
// Issue #466 batch 1 — UX-01 (token の対象 System), UX-02 (共通 Dialog の
// キーボード / 読み上げ), UX-08 (コピー失敗を成功と言わない), UX-15 (ラベルと
// 入力の関連付け)。

import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
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
let mockSystems: { id: number; name: string; environment?: string; description?: string }[] = [];

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
    user: { id: 1, username: "dev", role: "user" },
    isAdmin: false,
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

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSystemId = 1;
  mockSystems = [];
  document.body.style.overflow = "";
});

// ── UX-02: 共通 Dialog ──────────────────────────────────────────────

describe("UX-02: 共通 Dialog はキーボードと読み上げで扱える", () => {
  async function renderDialogHarness() {
    const { Dialog, DialogHeader, DialogTitle, DialogDescription } = await import("@/components/ui/dialog");
    function Harness() {
      const [open, setOpen] = useState(false);
      const [inner, setInner] = useState(false);
      return (
        <div>
          <button type="button" onClick={() => setOpen(true)}>開く</button>
          <button type="button">背景のボタン</button>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogHeader>
              <DialogTitle>System を作成</DialogTitle>
              <DialogDescription>作成する System の名前を入力します。</DialogDescription>
            </DialogHeader>
            <input aria-label="名前" />
            <button type="button" onClick={() => setInner(true)}>確認を開く</button>
            <Dialog open={inner} onOpenChange={setInner}>
              <DialogHeader>
                <DialogTitle>本当に作成しますか</DialogTitle>
              </DialogHeader>
              <button type="button">はい</button>
            </Dialog>
          </Dialog>
        </div>
      );
    }
    render(<Harness />);
  }

  test("dialog ロールとタイトル・説明の参照を持ち、最初の入力へフォーカスが移る", async () => {
    await renderDialogHarness();
    const opener = screen.getByRole("button", { name: "開く" });
    opener.focus();
    fireEvent.click(opener);

    const dialog = await screen.findByRole("dialog", { name: "System を作成" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("作成する System の名前を入力します。");
    // 閉じるボタンではなく、最初の入力に着地する。
    expect(screen.getByRole("textbox", { name: "名前" })).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");
  });

  test("Tab / Shift+Tab は面の中を循環し、Escape で閉じて起点へ戻る", async () => {
    await renderDialogHarness();
    const opener = screen.getByRole("button", { name: "開く" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "System を作成" });
    const close = within(dialog).getByRole("button", { name: "ダイアログを閉じる" });

    // 最後の要素 (閉じるボタン) から Tab で先頭へ戻る。
    close.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(within(dialog).getByRole("textbox", { name: "名前" })).toHaveFocus();
    // 先頭から Shift+Tab で末尾へ。
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(close).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });

  test("入れ子の Dialog では Escape は内側だけを閉じ、スクロールロックは外側が閉じるまで残る", async () => {
    await renderDialogHarness();
    fireEvent.click(screen.getByRole("button", { name: "開く" }));
    await screen.findByRole("dialog", { name: "System を作成" });
    const innerOpener = screen.getByRole("button", { name: "確認を開く" });
    innerOpener.focus();
    fireEvent.click(innerOpener);
    expect(await screen.findByRole("dialog", { name: "本当に作成しますか" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "はい" })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "本当に作成しますか" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "System を作成" })).toBeInTheDocument();
    expect(innerOpener).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
  });

  test("IME 変換中の Escape では閉じない", async () => {
    await renderDialogHarness();
    fireEvent.click(screen.getByRole("button", { name: "開く" }));
    await screen.findByRole("dialog", { name: "System を作成" });
    fireEvent.keyDown(document, { key: "Escape", isComposing: true });
    expect(screen.getByRole("dialog", { name: "System を作成" })).toBeInTheDocument();
  });

  test("closeOnOverlayClick=false の Dialog は背景クリックで閉じない", async () => {
    const { Dialog, DialogTitle } = await import("@/components/ui/dialog");
    const onOpenChange = vi.fn();
    render(
      <Dialog open onOpenChange={onOpenChange} closeOnOverlayClick={false}>
        <DialogTitle>長い入力</DialogTitle>
        <textarea aria-label="patch" />
      </Dialog>,
    );
    fireEvent.click(screen.getByTestId("dialog-overlay"));
    expect(onOpenChange).not.toHaveBeenCalled();
    // 明示的な閉じる操作は従来どおり効く。
    fireEvent.click(screen.getByRole("button", { name: "ダイアログを閉じる" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

// ── UX-01 / UX-08: Connect SDK ──────────────────────────────────────

function token(overrides: Record<string, unknown>) {
  return {
    id: 1, name: "ci", kind: "api", user_id: 1, system_id: 1, revoked: false,
    created_at: "2026-09-01T00:00:00Z", expires_at: null, status: "active",
    ...overrides,
  };
}

describe("UX-01: API token の対象 System を識別できる", () => {
  beforeEach(() => {
    mockSystems = [{ id: 1, name: "alpha" }, { id: 2, name: "beta" }];
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/tokens/me") {
        return Promise.resolve([
          token({ id: 11, name: "ci", system_id: 1 }),
          token({ id: 12, name: "ci", system_id: 2 }),
          token({ id: 13, name: "legacy", system_id: null }),
        ]);
      }
      return Promise.resolve(null);
    });
  });

  test("既定では選択中の System の token だけを System 列付きで表示し、未関連 token は別区分", async () => {
    const { default: ConnectSdkPage } = await import("@/pages/connect-sdk");
    render(<ConnectSdkPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("sdk-token-row-11")).toBeInTheDocument();
    expect(screen.queryByTestId("sdk-token-row-12")).not.toBeInTheDocument();
    expect(screen.getByTestId("sdk-token-system-11")).toHaveTextContent("alpha(#1)");
    expect(screen.getByTestId("sdk-token-list-heading")).toHaveTextContent("alpha(#1) の API token");

    const unbound = screen.getByTestId("sdk-token-unbound");
    expect(within(unbound).getByTestId("sdk-token-unbound-row-13")).toBeInTheDocument();
    expect(within(unbound).getByTestId("sdk-token-unbound-system-13")).toHaveTextContent("System 未関連");
  });

  test("「全 System」は明示的な切り替えで、同名 token も System で見分けられる", async () => {
    const { default: ConnectSdkPage } = await import("@/pages/connect-sdk");
    render(<ConnectSdkPage />, { wrapper: createWrapper() });

    await screen.findByTestId("sdk-token-row-11");
    const allButton = screen.getByTestId("sdk-token-scope-all");
    expect(allButton).toHaveAttribute("aria-pressed", "false");
    expect(allButton).toHaveTextContent("他 1 件");
    fireEvent.click(allButton);

    expect(await screen.findByTestId("sdk-token-row-12")).toBeInTheDocument();
    expect(screen.getByTestId("sdk-token-system-12")).toHaveTextContent("beta(#2)");
    expect(screen.getByRole("button", { name: "beta(#2) の token「ci」を失効させる" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "alpha(#1) の token「ci」を失効させる" })).toBeInTheDocument();
  });

  test("失効確認は対象 System を表示し、確認した token だけを失効させる", async () => {
    mockApi.post.mockResolvedValue(token({ id: 12, system_id: 2, revoked: true, status: "revoked" }));
    const { default: ConnectSdkPage } = await import("@/pages/connect-sdk");
    render(<ConnectSdkPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("sdk-token-scope-all"));
    fireEvent.click(await screen.findByRole("button", { name: "beta(#2) の token「ci」を失効させる" }));

    const dialog = await screen.findByRole("dialog", { name: "Token を失効させますか?" });
    expect(within(dialog).getByTestId("revoke-confirm-system")).toHaveTextContent("beta(#2)");
    expect(within(dialog).getByText(/beta\(#2\) に紐づく「ci」を失効させます/)).toBeInTheDocument();
    // 破壊的操作の確認: 初期フォーカスはキャンセル。
    expect(within(dialog).getByRole("button", { name: "キャンセル" })).toHaveFocus();

    fireEvent.click(within(dialog).getByTestId("revoke-confirm-button"));
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith("/tokens/me/12/revoke"));
    expect(mockApi.post).toHaveBeenCalledTimes(1);
  });

  test("System 未選択なら全 System 表示に倒し、空一覧を『token がない』と読ませない", async () => {
    mockSystemId = null;
    const { default: ConnectSdkPage } = await import("@/pages/connect-sdk");
    render(<ConnectSdkPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("sdk-token-row-11")).toBeInTheDocument();
    expect(screen.getByTestId("sdk-token-row-12")).toBeInTheDocument();
    expect(screen.queryByTestId("sdk-token-scope-all")).not.toBeInTheDocument();
  });
});

describe("UX-08: token のコピー結果を正直に伝える", () => {
  const originalClipboard = navigator.clipboard;

  afterEach(() => {
    Object.defineProperty(navigator, "clipboard", { value: originalClipboard, configurable: true });
  });

  async function issueToken() {
    mockSystems = [{ id: 1, name: "alpha" }];
    mockApi.get.mockImplementation((path: string) =>
      Promise.resolve(path === "/tokens/me" ? [] : null),
    );
    mockApi.post.mockResolvedValue(token({ id: 21, name: "svc", token: "pa_secret_value" }));
    const { default: ConnectSdkPage } = await import("@/pages/connect-sdk");
    render(<ConnectSdkPage />, { wrapper: createWrapper() });
    fireEvent.change(await screen.findByPlaceholderText("my-service"), { target: { value: "svc" } });
    fireEvent.click(screen.getByRole("button", { name: "Token を発行" }));
    return screen.findByTestId("issued-token-panel");
  }

  test("writeText が拒否されたら成功を通知せず、token を保持して手動コピーを案内する", async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await issueToken();
    const { toast } = await import("sonner");
    vi.mocked(toast.success).mockClear();

    await act(async () => {
      fireEvent.click(screen.getByTestId("issued-token-copy"));
    });

    await waitFor(() => expect(screen.getByTestId("issued-token-copy-status"))
      .toHaveTextContent("クリップボードにコピーできませんでした"));
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalled();
    expect(screen.getByTestId("issued-token-value")).toHaveTextContent("pa_secret_value");
    expect(screen.getByTestId("issued-token-select")).toBeInTheDocument();
  });

  test("クリップボード API が無い環境でも成功を通知しない", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    await issueToken();
    const { toast } = await import("sonner");
    vi.mocked(toast.success).mockClear();
    await act(async () => {
      fireEvent.click(screen.getByTestId("issued-token-copy"));
    });
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(toast.success).not.toHaveBeenCalled();
  });

  test("writeText が完了してから成功を通知する", async () => {
    let resolve: () => void = () => {};
    const writeText = vi.fn(() => new Promise<void>((r) => { resolve = r; }));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await issueToken();
    const { toast } = await import("sonner");
    vi.mocked(toast.success).mockClear();

    fireEvent.click(screen.getByTestId("issued-token-copy"));
    expect(writeText).toHaveBeenCalledWith("pa_secret_value");
    // まだ完了していないので成功を言わない。
    expect(toast.success).not.toHaveBeenCalled();
    await act(async () => { resolve(); });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("コピーしました"));
    expect(screen.getByTestId("issued-token-copy-status")).toHaveTextContent("クリップボードにコピーしました");
  });
});

// ── UX-15: Settings のラベル ────────────────────────────────────────

describe("UX-15: Settings の可視ラベルが入力欄と関連付いている", () => {
  test("ラベルでアクセシブル名が取れ、補助説明とエラーが入力に関連付く", async () => {
    mockSystems = [{ id: 1, name: "alpha", environment: "production", description: "" }];
    mockApi.get.mockResolvedValue(null);
    const { default: SettingsPage } = await import("@/pages/settings");
    render(<SettingsPage />, { wrapper: createWrapper() });

    const nameInput = await screen.findByLabelText(/System Name/);
    expect(nameInput).toHaveValue("alpha");
    expect(nameInput).toHaveAccessibleDescription(/ヘッダーの System 選択/);
    expect(screen.getByLabelText("Environment")).toHaveValue("production");
    expect(screen.getByLabelText("Description").tagName).toBe("TEXTAREA");

    // ラベルのクリックで入力へフォーカスが移る (htmlFor の関連付け)。
    const label = screen.getByText("Environment");
    expect(label.tagName).toBe("LABEL");
    expect(label).toHaveAttribute("for", screen.getByLabelText("Environment").id);

    fireEvent.change(nameInput, { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save Settings" }));
    expect(nameInput).toHaveAttribute("aria-invalid", "true");
    expect(nameInput).toHaveAccessibleDescription(/System 名を入力してください/);
    expect(mockApi.put).not.toHaveBeenCalled();
  });
});
