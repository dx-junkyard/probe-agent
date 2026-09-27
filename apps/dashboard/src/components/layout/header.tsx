import { useUiDraftRegistry } from "@/lib/ui-draft";
import { useAuth } from "@/api/auth";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { LogOut, Plus, Moon, Sun, Menu, X, MoreHorizontal } from "lucide-react";
import { useState, useCallback, useEffect, useRef, type ReactNode, type RefObject } from "react";
import { systemsKnownEmpty } from "@/lib/systems-state";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCreateSystem } from "@/api/hooks";
import { DiagnosticsBadge } from "@/components/diagnostics-badge";
import { ConnectivityBadge } from "@/components/connectivity-badge";
import { UserPhaseIndicator } from "@/components/system-state";
import { toast } from "sonner";
import { MOBILE_NAV_DRAWER_ID } from "./sidebar";
import { HelpCircle } from "lucide-react";
import { useHelpMode } from "@/lib/help-mode";

function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "dark") {
    document.documentElement.classList.add("dark");
    return true;
  }
  return document.documentElement.classList.contains("dark");
}

const initialDark = initTheme();

function ThemeToggle({ asMenuItem = false }: { asMenuItem?: boolean }) {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark") || initialDark);
  const toggle = useCallback(() => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  }, [dark]);

  if (asMenuItem) {
    return (
      <MenuItemButton onClick={toggle} icon={dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}>
        {dark ? "ライトテーマにする" : "ダークテーマにする"}
      </MenuItemButton>
    );
  }
  return (
    <Button variant="ghost" size="icon" onClick={toggle} title="Toggle theme" aria-label={dark ? "ライトテーマにする" : "ダークテーマにする"}>
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}

function MenuItemButton({ onClick, icon, children, testId }: {
  onClick: () => void;
  icon: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}

/**
 * Issue #466 (UX-14): 狭い画面 (lg 未満) では、補助操作 (解説モード・テーマ・
 * ユーザー名・ログアウト・System 追加) をこのメニューへ移す。ヘッダーに
 * 残すのは System 選択・重要通知・ナビゲーションのメニューボタンだけで、
 * 390px 幅で System 名と右側のアイコンが重なるのを防ぐ。
 */
function HeaderOverflowMenu({ children }: { children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative lg:hidden">
      <Button
        ref={buttonRef}
        variant="ghost"
        size="icon"
        aria-label="その他の操作"
        aria-expanded={open}
        aria-controls={open ? "header-overflow-menu" : undefined}
        onClick={() => setOpen((v) => !v)}
        data-testid="header-overflow-toggle"
      >
        <MoreHorizontal className="h-4 w-4" />
      </Button>
      {open && (
        <div
          id="header-overflow-menu"
          role="group"
          aria-label="その他の操作"
          data-testid="header-overflow-menu"
          className="absolute right-0 top-full z-50 mt-1 w-64 max-w-[calc(100vw-1.5rem)] rounded-lg border bg-popover bg-card p-1 shadow-lg"
        >
          {children(close)}
        </div>
      )}
    </div>
  );
}

// Issue #440 (Epic #436): 機能解説モードのトグル。再クリックと Escape の
// どちらでも解除でき、解除後は `HelpModeLayer` がリスナーを外すので通常操作
// へ完全に戻る (`docs/01-specifications/capabilities/assistant-discussion.md` §3)。
function HelpModeToggle({ asMenuItem = false, onDone }: { asMenuItem?: boolean; onDone?: () => void }) {
  const { active, toggle } = useHelpMode();
  if (asMenuItem) {
    return (
      <MenuItemButton
        onClick={() => { toggle(); onDone?.(); }}
        icon={<HelpCircle className="h-4 w-4" />}
        testId="help-mode-toggle-menu"
      >
        {active ? "解説モードを終了" : "解説モードを開始"}
      </MenuItemButton>
    );
  }
  return (
    <Button
      variant={active ? "default" : "ghost"}
      size="icon"
      onClick={toggle}
      aria-pressed={active}
      aria-label={active ? "解説モードを終了" : "解説モードを開始"}
      title={active ? "解説モードを終了" : "解説モードを開始"}
      data-testid="help-mode-toggle"
    >
      <HelpCircle className="h-4 w-4" />
    </Button>
  );
}

export interface HeaderProps {
  /**
   * Issue #362: mobile navigation Drawer state, owned by `AppLayout`. All
   * three props are optional so the header can still be rendered on its own
   * (the menu button then simply does nothing).
   */
  navOpen?: boolean;
  onNavToggle?: () => void;
  navToggleRef?: RefObject<HTMLButtonElement | null>;
  /** Element id of the Drawer panel, used as the button's `aria-controls`. */
  navDrawerId?: string;
}

export function Header({
  navOpen = false,
  onNavToggle,
  navToggleRef,
  navDrawerId = MOBILE_NAV_DRAWER_ID,
}: HeaderProps = {}) {
  const auth = useAuth();
  const { user, systems, systemId, selectSystem, logout, refreshSystems } = auth;
  const selectedSystem = systems.find((s) => s.id === systemId);
  const selectedSystemLabel = selectedSystem
    ? `${selectedSystem.name}${selectedSystem.environment ? ` / ${selectedSystem.environment}` : ""}`
    : undefined;
  const requestLogout = () => { if (drafts?.confirmDiscard?.() !== false) logout(); };
  const drafts = useUiDraftRegistry();
  const [showCreate, setShowCreate] = useState(false);
  const createSystem = useCreateSystem();

  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    try {
      const sys = await createSystem.mutateAsync({
        name: fd.get("name") as string,
        environment: (fd.get("environment") as string) || undefined,
        description: (fd.get("description") as string) || undefined,
      });
      await refreshSystems();
      selectSystem(sys.id);
      setShowCreate(false);
      toast.success("System created");
    } catch (err) {
      toast.error(String(err));
    }
  };

  return (
    <header className="flex items-center justify-between gap-2 border-b bg-card px-3 md:px-6 h-14">
      <div className="flex min-w-0 flex-1 items-center gap-2 md:gap-3">
        {/* Issue #362: below md the sidebar is an overlay Drawer, opened
            from here. At md and above the static rail is always present, so
            this control is hidden. */}
        <Button
          ref={navToggleRef}
          variant="ghost"
          size="icon"
          className="md:hidden shrink-0"
          onClick={onNavToggle}
          aria-label={navOpen ? "ナビゲーションを閉じる" : "ナビゲーションを開く"}
          aria-expanded={navOpen}
          // Drawer は閉じているとマウントされないので、そのときは
          // `aria-controls` を出さない -- 存在しない id を指す参照になる。
          aria-controls={navOpen ? navDrawerId : undefined}
          data-testid="mobile-nav-toggle"
        >
          {navOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
        </Button>
        {systems.length > 0 ? (
          // Issue #466 (UX-14): 固定幅ではなく残り幅に収める。長い System 名は
          // 選択欄の中で省略され、全文は title (と読み上げ) で分かる。右側の
          // 操作と重ならない。
          <div className="min-w-0 flex-1 md:max-w-60">
            <Select
              aria-label="System"
              title={selectedSystemLabel}
              value={String(systemId ?? "")}
              onChange={(e) => { if (drafts?.confirmDiscard?.() !== false) selectSystem(Number(e.target.value)); }}
              className="w-full min-w-0 truncate"
              data-testid="header-system-select"
            >
              {systems.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}{s.environment ? ` / ${s.environment}` : ""}
                </option>
              ))}
            </Select>
          </div>
        ) : systemsKnownEmpty(auth) ? (
          // Issue #265: previously this was a lone icon-only "+" button with
          // no indication anything was missing -- a dead end for a
          // brand-new install with zero Systems. Make the empty state and
          // the path out of it explicit.
          <span className="min-w-0 truncate text-sm text-muted-foreground" data-testid="header-no-systems-hint">
            System が未作成です。
          </span>
        ) : (
          // Issue #466 (UX-06): 取得できていない空配列は「未作成」ではない。
          <span className="min-w-0 truncate text-sm text-muted-foreground" data-testid="header-systems-unknown">
            System 一覧を取得できていません
          </span>
        )}
        {systems.length === 0 ? (
          systemsKnownEmpty(auth) && (
            <Button
              variant="default"
              size="sm"
              className="shrink-0"
              onClick={() => setShowCreate(true)}
              title="New system"
              data-testid="header-create-system-button"
            >
              <Plus className="h-4 w-4" />
              <span className="ml-1">System を作成</span>
            </Button>
          )
        ) : (
          <Button
            variant="ghost"
            size="icon"
            className="hidden shrink-0 lg:inline-flex"
            onClick={() => setShowCreate(true)}
            title="New system"
            aria-label="System を作成"
            data-testid="header-create-system-button"
          >
            <Plus className="h-4 w-4" />
          </Button>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1 md:gap-2">
        {/* Issue #239: current user phase (setup -> preparation ->
            instrumentation -> observation -> evaluation -> publish, Issue
            #256) with per-phase completion, straight from GET /system-state. */}
        <UserPhaseIndicator />
        <ConnectivityBadge />
        <DiagnosticsBadge />
        <div className="hidden items-center gap-2 lg:flex">
          <HelpModeToggle />
          <ThemeToggle />
          {user && (
            <span className="flex min-w-0 max-w-40 items-center text-sm text-muted-foreground" title={user.username}>
              <span className="truncate">{user.username}</span>
              {user.role === "admin" && (
                <span className="ml-1 shrink-0 text-xs bg-primary text-primary-foreground px-1.5 py-0.5 rounded">
                  admin
                </span>
              )}
            </span>
          )}
          <Button variant="ghost" size="icon" onClick={requestLogout} title="Logout" aria-label="ログアウト">
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
        <HeaderOverflowMenu>
          {(close) => (
            <>
              {user && (
                <p className="truncate px-3 py-2 text-xs text-muted-foreground" title={user.username} data-testid="header-overflow-user">
                  {user.username}{user.role === "admin" ? "(admin)" : ""} でログイン中
                </p>
              )}
              {systems.length > 0 && (
                <MenuItemButton
                  onClick={() => { close(); setShowCreate(true); }}
                  icon={<Plus className="h-4 w-4" />}
                  testId="header-overflow-create-system"
                >
                  System を作成
                </MenuItemButton>
              )}
              <HelpModeToggle asMenuItem onDone={close} />
              <ThemeToggle asMenuItem />
              <MenuItemButton
                onClick={() => { close(); requestLogout(); }}
                icon={<LogOut className="h-4 w-4" />}
                testId="header-overflow-logout"
              >
                ログアウト
              </MenuItemButton>
            </>
          )}
        </HeaderOverflowMenu>
      </div>

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogHeader>
          <DialogTitle>Create System</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <Label htmlFor="name">Name *</Label>
            <Input id="name" name="name" required />
          </div>
          <div>
            <Label htmlFor="environment">Environment</Label>
            <Input id="environment" name="environment" placeholder="production" />
          </div>
          <div>
            <Label htmlFor="description">Description</Label>
            <Input id="description" name="description" />
          </div>
          <Button type="submit" disabled={createSystem.isPending} className="w-full">
            {createSystem.isPending ? "Creating..." : "Create"}
          </Button>
        </form>
      </Dialog>
    </header>
  );
}
