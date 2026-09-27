import { useCallback, useRef, useState } from "react";
import { Outlet, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/api/auth";
import { useSystemState } from "@/api/hooks";
import { Sidebar, MOBILE_NAV_DRAWER_ID } from "./sidebar";
import { Header } from "./header";
import { AuthBootstrapError, AuthLoadingScreen, ReauthDialog, SystemsLoadErrorBanner } from "./auth-recovery";
import { loginPathFor } from "@/lib/return-to";
import { AssistantPanel } from "@/components/assistant-panel";
import { ReturnToBanner } from "@/components/discussion-return-banner";
import { systemStateTarget } from "@/components/system-state";
import { HelpModeProvider } from "@/lib/help-mode";
import { HelpModeLayer } from "@/components/help-mode-layer";
import { UiDraftProvider } from "@/lib/ui-draft";

export function AppLayout() {
  const { user, loading, systemId, authError, retryBootstrap, explicitLogout } = useAuth();
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

  if (loading) return <AuthLoadingScreen />;

  // Issue #466 (UX-06): 認証状態を確認できなかった (到達不能・タイムアウト・
  // 5xx) のは「未ログイン」ではない。ログイン画面へ送らず、再試行させる。
  if (!user && authError) {
    return <AuthBootstrapError message={authError} onRetry={() => retryBootstrap?.()} />;
  }

  // Issue #466 (UX-09): 共有リンクの目的地 (pathname / query / hash) を
  // `next` に持たせ、ログイン後に同じ画面へ戻す。自分でログアウトした場合は
  // 戻さない (次にログインする人の目的地ではないため)。
  if (!user) {
    return <Navigate to={explicitLogout ? "/login" : loginPathFor(location)} replace />;
  }

  return (
    // Issue #445: mounted here (not per-page, unlike `UnsavedWorkProvider`)
    // because its two sides live in different trees -- the forms that
    // register a draft live under <Outlet /> while the one reader
    // (`AssistantPanel`) is a sibling of it, not a descendant.
    // Reset forms, draft registrations, and in-flight voice UI together when
    // the principal/System changes; identical target refs are not identities
    // across Systems.
    <UiDraftProvider key={`${user.id}:${systemId}`}>
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
          <SystemsLoadErrorBanner />
          <ReturnToBanner />
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
        <ReauthDialog />
      </div>
    </HelpModeProvider>
    </UiDraftProvider>
  );
}
