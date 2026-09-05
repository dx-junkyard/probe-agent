import { useCallback, useRef, useState } from "react";
import { Outlet, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/api/auth";
import { useSystemState } from "@/api/hooks";
import { Sidebar, MOBILE_NAV_DRAWER_ID } from "./sidebar";
import { Header } from "./header";
import { Skeleton } from "@/components/ui/skeleton";
import { AssistantPanel } from "@/components/assistant-panel";
import { systemStateTarget } from "@/components/system-state";
import { HelpModeProvider } from "@/lib/help-mode";
import { HelpModeLayer } from "@/components/help-mode-layer";
import { UiDraftProvider } from "@/lib/ui-draft";

export function AppLayout() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { data: systemState } = useSystemState();
  const primaryNotice = systemState?.notification_items[0] ?? null;

  // Issue #362: below the md breakpoint the sidebar is an overlay Drawer so
  // it takes no horizontal space from <main>. The open state lives here --
  // the header's menu button and the Drawer are two views of one fact.
  //
  // The state stores the route the Drawer was opened on rather than a bare
  // boolean, so any react-router location change closes it by derivation
  // (no setState in an effect, and no way for the overlay to survive a
  // navigation). A link back to the CURRENT route produces no location
  // change, so the Drawer's own NavLinks additionally call `closeNav`.
  const pathname = location.pathname;
  const [navOpenAt, setNavOpenAt] = useState<string | null>(null);
  const navOpen = navOpenAt !== null && navOpenAt === pathname;
  const navToggleRef = useRef<HTMLButtonElement>(null);
  const closeNav = useCallback(() => setNavOpenAt(null), []);
  const toggleNav = useCallback(
    () => setNavOpenAt((prev) => (prev === pathname ? null : pathname)),
    [pathname],
  );

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="space-y-4 w-64">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      </div>
    );
  }

  if (!user) return <Navigate to="/login" replace />;

  return (
    // Issue #445: mounted here (not per-page, unlike `UnsavedWorkProvider`)
    // because its two sides live in different trees -- the forms that
    // register a draft live under <Outlet /> while the one reader
    // (`AssistantPanel`) is a sibling of it, not a descendant.
    <UiDraftProvider>
    <HelpModeProvider>
      <div className="flex h-screen overflow-hidden">
        <Sidebar
          navOpen={navOpen}
          onNavClose={closeNav}
          drawerId={MOBILE_NAV_DRAWER_ID}
          returnFocusRef={navToggleRef}
        />
        <div className="flex flex-1 flex-col overflow-hidden min-w-0">
          <Header
            navOpen={navOpen}
            onNavToggle={toggleNav}
            navToggleRef={navToggleRef}
            navDrawerId={MOBILE_NAV_DRAWER_ID}
          />
          {/* `pb-24` は `AssistantPanel` の浮いているボタン (閉じているとき
              右下に固定) のための予約領域。本文の末尾が常にボタンより上で
              終わるので、画面の主操作がボタンに覆われることがない (#102:
              アシスタントは画面の主操作を隠さない)。ボタン側の `bottom-*` と
              対で意味を持つ値なので、片方だけ変えないこと。 */}
          <main className="flex-1 overflow-y-auto p-4 pb-24 md:p-6 md:pb-24">
            <Outlet />
          </main>
        </div>
        <AssistantPanel
          focusedStateItem={primaryNotice}
          onSnapshotNoticeClick={() => {
            const target = primaryNotice ? systemStateTarget(primaryNotice) : null;
            if (target) navigate(target);
          }}
        />
        <HelpModeLayer />
      </div>
    </HelpModeProvider>
    </UiDraftProvider>
  );
}
