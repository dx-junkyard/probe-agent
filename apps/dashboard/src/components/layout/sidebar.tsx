import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard, GitBranch, Map, Crosshair, FlaskConical,
  Plug, Sparkles, Boxes, Settings, Users, ChevronLeft, ChevronRight, MessageSquare,
  Workflow, Network, MessageSquareText, Brain, GitFork, Filter,
  LifeBuoy, BookMarked, GitMerge, Beaker, Bot, Layers, PenTool, Rows3, Target,
} from "lucide-react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/api/auth";
import { useSystemState } from "@/api/hooks";
import { useModalSurface } from "@/lib/modal-surface";
import type { UserPhase } from "@/api/types";
import { useRef, useState, type RefObject } from "react";

// Issue #179 introduced fixed, explicit headings (Hub / Detail views /
// Other). Issue #257 re-groups the same routes along the phase axis instead:
// each group below (other than Overview/Other) lines up with one or more of
// the server-derived `user_phase` values (Issue #256's 6-phase
// setup -> preparation -> instrumentation -> observation -> evaluation ->
// publish flow) and reuses #173's stage terms (Understand / Instrument /
// Evaluate). No route or URL changes -- only how the existing list is
// grouped, ordered, and (via `phases` + `phaseGroupState` below) visually
// emphasized or dimmed for the developer's current phase.
type NavItem = {
  to: string;
  icon: typeof LayoutDashboard;
  label: string;
  // Issue #257 (Generate, decision b): a deprecated-but-kept page gets a
  // small "Legacy" badge plus an explanatory tooltip pointing at its
  // replacement, instead of being removed or given a new landing page.
  legacy?: boolean;
  title?: string;
  // Issue #267 item 11: a short usage-distinction subtext shown under the
  // label (currently only populated for "Observe & Evaluate" -- several of
  // its 7 pages look similar at a glance, e.g. Simulation Workbench vs AI
  // Candidate Studio).
  subtitle?: string;
  /**
   * Issue #466 (UX-13): 詳細ビュー。グループ内の主要入口の下に、既定で閉じた
   * 「詳細ビュー」として段階的に表示する (リンクは DOM に残り、展開・検索で
   * 到達できる)。Map / Lineage / Blueprint のように使い分けを学ぶ必要がある
   * 画面が、初見の利用者の視界を占めないようにするため。
   */
  detail?: boolean;
  /** Issue #466 (UX-13): 画面検索で一致させる日本語の目的語 (表示はしない)。 */
  keywords?: string[];
};

// Issue #257: a group carries `phases` only when it has phase meaning.
// Overview and Other opt out entirely (no `phases` field) since neither
// belongs to a single step of the improvement flow.
type NavGroup = { heading: string | null; items: NavItem[]; phases?: UserPhase[] };

const NAV_GROUPS: NavGroup[] = [
  {
    heading: null,
    items: [{ to: "/", icon: LayoutDashboard, label: "Overview" }],
  },
  {
    heading: "Setup",
    phases: ["setup"],
    items: [
      { to: "/setup-guide", icon: LifeBuoy, label: "Setup Guide" },
      { to: "/repository", icon: GitBranch, label: "Repository" },
      { to: "/settings", icon: Settings, label: "Settings" },
    ],
  },
  {
    heading: "Understand",
    phases: ["preparation"],
    items: [
      { to: "/system-understanding", icon: Brain, label: "System Understanding", keywords: ["現状", "理解"] },
      { to: "/capability-map", icon: Network, label: "Capability Map", detail: true },
      { to: "/feature-map", icon: Map, label: "Feature Map", detail: true },
      { to: "/flow-explorer", icon: Workflow, label: "Flow Explorer", detail: true },
      { to: "/interview", icon: MessageSquareText, label: "Interview", keywords: ["インタビュー", "理解"] },
      {
        to: "/ux-design-studio", icon: PenTool, label: "UX Design Studio", keywords: ["要件", "設計", "体験"],
        subtitle: "Journey/Requirement/Solution Designの追跡",
      },
      {
        to: "/stakeholder-value-network", icon: Users, label: "Stakeholder Value Network",
        subtitle: "当事者とValue Exchangeの追跡", detail: true,
      },
      {
        to: "/journey-blueprint", icon: Rows3, label: "Journey Service Blueprint",
        subtitle: "Step × 9レーンのサービスブループリント", detail: true,
      },
      {
        to: "/functional-lineage", icon: GitFork, label: "Functional Lineage",
        subtitle: "Purpose → Capability → 実装 → Outcome の追跡とGap", detail: true,
      },
      {
        to: "/objective-map", icon: Target, label: "Objective Map",
        subtitle: "Vision へ向かうObjective/Milestone とGap Workbench", detail: true,
      },
    ],
  },
  {
    heading: "Instrument",
    phases: ["instrumentation"],
    items: [
      { to: "/probe-planner", icon: Crosshair, label: "Probe Planner" },
      // Issue #257 AC ("Probe Patterns にサイドバー以外の到達経路がある、
      // または適切なフェーズグループに属する"): membership in this phase
      // group is the chosen resolution -- probe-planner.tsx itself is not
      // touched here (owned by other in-flight sub-issues).
      { to: "/probe-patterns", icon: BookMarked, label: "Probe Patterns" },
      { to: "/connect-sdk", icon: Plug, label: "Connect SDK" },
    ],
  },
  {
    heading: "Observe & Evaluate",
    // Two server phases share one group: observation (trace/shadow
    // watching) and evaluation (candidate judging) both live on these same
    // pages, so splitting them into two nav groups would not match the UI.
    phases: ["observation", "evaluation"],
    items: [
      // Issue #257 label improvement: "Components" alone didn't say this is
      // also the trace/observation view.
      {
        to: "/components", icon: Boxes, label: "Components / Traces", keywords: ["トレース", "観測"],
        subtitle: "Trace記録とpolicy切替",
      },
      {
        to: "/trace-lineage", icon: GitFork, label: "Trace Lineage",
        subtitle: "Traceのつながりを俯瞰", detail: true,
      },
      {
        to: "/trace-analyzers", icon: Filter, label: "Trace Analyzers",
        subtitle: "パターン抽出とshadow diff検出", detail: true,
      },
      {
        to: "/experiments", icon: FlaskConical, label: "Experiments", keywords: ["比較", "評価", "実験"],
        subtitle: "候補のバッチ実行と評価",
      },
      {
        to: "/simulation-workbench", icon: Beaker, label: "Simulation Workbench",
        subtitle: "手動でdiffを編集して検証", detail: true,
      },
      {
        to: "/candidate-studio", icon: Bot, label: "AI Candidate Studio",
        subtitle: "会話でAIに指示して候補生成",
      },
      {
        to: "/workspaces", icon: MessageSquare, label: "Decision Workspace",
        subtitle: "意思決定の記録と提案レビュー", detail: true,
      },
      {
        to: "/cell-fabric", icon: Layers, label: "Cell Fabric",
        subtitle: "Probe Cell群の統合ダイジェストとAsk対応", detail: true,
      },
      {
        to: "/evolution-nodes", icon: Layers, label: "Evolution Node",
        subtitle: "処理単位の契約・実装方式・成熟度", detail: true,
      },
      // Epic #412 (#414/#415): the Flow-level view of those same Nodes plus
      // the execution-mode control and the experiment proposals awaiting a
      // human decision. It sits beside Evolution Node deliberately -- the
      // Node page is the per-Node inspector, this one is the Flow aggregate.
      {
        to: "/flow-agents", icon: Bot, label: "Flow・エージェント群",
        subtitle: "Flow単位の5軸・実行モード・実験提案", detail: true,
      },
    ],
  },
  {
    heading: "Publish",
    phases: ["publish"],
    items: [{ to: "/github", icon: GitMerge, label: "GitHub" }],
  },
  {
    heading: "Other",
    items: [
      // Issue #257 (Generate, decision b): option (a) removal is out of
      // scope ("ページの機能変更・削除は含まない") and option (c) would add
      // a lane to a superseded flow that duplicates AI Candidate Studio /
      // Simulation Workbench, so the page is kept but demoted: bottom of
      // Other, a visible "Legacy" badge, and a tooltip pointing at its
      // replacement. No functional change to the page itself.
      {
        to: "/generation", icon: Sparkles, label: "Generate", legacy: true,
        title: "旧世代の候補生成。AI Candidate Studio を推奨",
      },
    ],
  },
];

// Issue #257: mirrors the server's fixed PHASE_ORDER (also duplicated
// client-side in components/system-state.tsx) -- a finite enum ordering,
// never client-side state derivation (Principle 6). Only the *mapping* from
// nav group to phase(s) is a new client-side constant; the phase values and
// their order still come from the server contract.
const PHASE_ORDER: UserPhase[] = [
  "setup", "preparation", "instrumentation", "observation", "evaluation", "publish",
];

type PhaseGroupState = "current" | "reached" | "future" | "none";

/**
 * Deterministic comparison of a group's phase(s) against the current
 * `user_phase`: "current" when `user_phase` falls within the group's phase
 * range, "reached" when the whole range is before it (already passed),
 * "future" when the whole range is after it (not yet reached), and "none"
 * when the group has no phase mapping or the server hasn't provided
 * `user_phase` (older Control Server, loading, or error) -- graceful
 * degradation, never a guessed phase.
 */
function phaseGroupState(
  groupPhases: UserPhase[] | undefined,
  userPhase: UserPhase | undefined,
): PhaseGroupState {
  if (!groupPhases || groupPhases.length === 0 || !userPhase) return "none";
  const currentRank = PHASE_ORDER.indexOf(userPhase);
  const ranks = groupPhases.map((p) => PHASE_ORDER.indexOf(p));
  if (currentRank === -1 || ranks.some((r) => r === -1)) return "none";
  const minRank = Math.min(...ranks);
  const maxRank = Math.max(...ranks);
  if (currentRank >= minRank && currentRank <= maxRank) return "current";
  return currentRank > maxRank ? "reached" : "future";
}

// Issue #362: the mobile Drawer's element id. The header's menu button
// points at it via `aria-controls`, so both sides must agree on one value.
export const MOBILE_NAV_DRAWER_ID = "mobile-nav-drawer";

function SidebarBrand({ collapsed }: { collapsed: boolean }) {
  return (
    <>
      <div className="h-7 w-7 rounded-lg bg-primary flex items-center justify-center">
        <span className="text-xs font-bold text-primary-foreground">P</span>
      </div>
      {!collapsed && <span className="font-semibold text-sm">Probe Agent</span>}
    </>
  );
}

// Issue #466 (UX-13): 初見の利用者が「現状を理解する」「要件を設計する」
// 「候補を比較評価する」の開始地点を、機能名を知らなくても選べるように、
// 目的の言葉で 3 つの入口を置く。行き先は既存の画面で、新しいナビ体系や
// canonical な状態は作らない (#401 の方針)。
const PURPOSE_ENTRIES: { to: string; icon: typeof LayoutDashboard; label: string; hint: string }[] = [
  { to: "/system-understanding", icon: Brain, label: "現状を理解する", hint: "System Understanding を開く" },
  { to: "/ux-design-studio", icon: PenTool, label: "要件を設計する", hint: "UX Design Studio を開く" },
  { to: "/experiments", icon: FlaskConical, label: "候補を比較評価する", hint: "Experiments を開く" },
];

function isItemActive(to: string, pathname: string): boolean {
  return to === "/" ? pathname === "/" : pathname.startsWith(to);
}

/** 画面検索の照合 (表示ラベルと補足文への部分一致。大文字小文字・全角半角を無視)。 */
function normalizeForSearch(value: string): string {
  return value.normalize("NFKC").toLowerCase();
}

function matchNavItem(item: Pick<NavItem, "label" | "subtitle" | "to" | "keywords">, query: string): boolean {
  const q = normalizeForSearch(query.trim());
  if (!q) return true;
  return [item.label, item.subtitle ?? "", item.to, ...(item.keywords ?? [])]
    .some((field) => normalizeForSearch(field).includes(q));
}

interface NavViewState {
  query: string;
  setQuery: (query: string) => void;
  /** グループごとの「詳細ビュー」の開閉 (利用者の操作)。未操作なら undefined。 */
  detailsOpen: Record<string, boolean>;
  setDetailsOpen: (heading: string, open: boolean) => void;
}

function NavItemLink({
  item,
  collapsed,
  active,
  onNavigate,
}: {
  item: NavItem;
  collapsed: boolean;
  active: boolean;
  onNavigate?: () => void;
}) {
  return (
    <NavLink
      to={item.to}
      // Issue #362: inside the mobile Drawer, following a link
      // must close the overlay. The AppLayout also closes on a
      // react-router location change, so a link to the current
      // route (which produces no location change) still closes.
      onClick={onNavigate}
      className={cn(
        "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
        // Issue #466 (UX-13): 利用可能なリンクの文字は薄くしない。現在地は
        // 背景だけでなく「表示中」の文字でも示す (色だけに頼らない)。
        active
          ? "bg-secondary text-foreground"
          : "text-foreground/90 hover:bg-secondary/50 hover:text-foreground",
        collapsed && "justify-center px-2",
      )}
      title={item.title ?? (collapsed ? item.label : undefined)}
    >
      <item.icon className="h-4 w-4 shrink-0" />
      {!collapsed && (
        <span className="flex flex-col min-w-0">
          <span className="flex flex-wrap items-center gap-1.5">
            <span>{item.label}</span>
            {active && (
              <span
                className="rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-medium text-primary-foreground"
                data-testid="sidebar-current-badge"
              >
                表示中
              </span>
            )}
            {item.legacy && (
              <span
                className="rounded bg-muted px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
                data-testid="sidebar-legacy-badge"
              >
                Legacy
              </span>
            )}
          </span>
          {/* Issue #267 item 11: usage-distinction subtext. Issue #466
              (UX-13): 狭いサイドバーで省略しない (折り返して全文を読める)。 */}
          {item.subtitle && (
            <span
              className="whitespace-normal break-words text-[11px] font-normal normal-case tracking-normal text-muted-foreground"
              data-testid={`sidebar-item-subtitle-${item.to.replace(/\//g, "")}`}
            >
              {item.subtitle}
            </span>
          )}
        </span>
      )}
    </NavLink>
  );
}

// The nav list itself. Rendered by the static desktop rail and by the mobile
// Drawer alike (Issue #362) so the two presentations can never drift apart --
// there is exactly one source of nav markup.
function SidebarNavList({
  groups,
  collapsed,
  userPhase,
  onNavigate,
  view,
}: {
  groups: NavGroup[];
  collapsed: boolean;
  userPhase: UserPhase | undefined;
  onNavigate?: () => void;
  view: NavViewState;
}) {
  const location = useLocation();
  const searching = !collapsed && view.query.trim().length > 0;
  const matches = searching
    ? groups.flatMap((group) =>
      group.items
        .filter((item) => matchNavItem(item, view.query))
        .map((item) => ({ item, heading: group.heading })),
    )
    : [];
  return (
    <nav className="flex-1 overflow-y-auto py-2 px-2 space-y-3" data-testid="sidebar-nav" aria-label="メインナビゲーション">
      {!collapsed && (
        <div className="px-1">
          <label className="sr-only" htmlFor={`nav-search-${onNavigate ? "drawer" : "rail"}`}>画面を検索</label>
          <input
            id={`nav-search-${onNavigate ? "drawer" : "rail"}`}
            type="search"
            value={view.query}
            onChange={(e) => view.setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && view.query) {
                // 検索語を消すだけで、Drawer は閉じない。
                e.stopPropagation();
                e.nativeEvent.stopImmediatePropagation();
                view.setQuery("");
              }
            }}
            placeholder="画面を検索(例: Trace, 要件)"
            className="h-8 w-full rounded-md border border-input bg-transparent px-2 text-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            data-testid="sidebar-search"
          />
        </div>
      )}
      {searching ? (
        <div className="space-y-0.5" data-testid="sidebar-search-results" role="region" aria-label="画面の検索結果">
          <p className="px-3 pb-1 text-xs text-muted-foreground" aria-live="polite">
            {matches.length > 0 ? `${matches.length} 件の画面` : "該当する画面がありません"}
          </p>
          {matches.map(({ item, heading }) => (
            <div key={item.to}>
              <NavItemLink
                item={heading ? { ...item, subtitle: item.subtitle ? `${heading} · ${item.subtitle}` : heading } : item}
                collapsed={false}
                active={isItemActive(item.to, location.pathname)}
                onNavigate={onNavigate}
              />
            </div>
          ))}
        </div>
      ) : (
        <>
          {!collapsed && (
            <div className="space-y-0.5" data-testid="sidebar-purpose-entries">
              <p className="px-3 pb-1 text-xs font-semibold text-muted-foreground">目的から始める</p>
              {PURPOSE_ENTRIES.map((entry) => (
                <NavLink
                  key={entry.to}
                  to={entry.to}
                  onClick={onNavigate}
                  className="flex items-center gap-3 rounded-lg px-3 py-1.5 text-sm text-foreground/90 hover:bg-secondary/50 hover:text-foreground"
                  data-testid={`sidebar-purpose-${entry.to.replace(/\//g, "")}`}
                  // 目的の入口は同じ画面への近道なので、現在地の表示は下の
                  // 機能一覧の側だけで行う (同じ画面に「表示中」が 2 つ出ない)。
                  aria-current={undefined}
                >
                  <entry.icon className="h-4 w-4 shrink-0" />
                  <span className="flex flex-col">
                    <span className="font-medium">{entry.label}</span>
                    <span className="text-[11px] text-muted-foreground">{entry.hint}</span>
                  </span>
                </NavLink>
              ))}
            </div>
          )}
          {groups.map((group, gi) => {
            const slug = group.heading ? group.heading.toLowerCase().replace(/\s+/g, "-") : undefined;
            const phaseState = phaseGroupState(group.phases, userPhase);
            const primary = collapsed ? group.items : group.items.filter((item) => !item.detail);
            const details = collapsed ? [] : group.items.filter((item) => item.detail);
            const activeInDetails = details.some((item) => isItemActive(item.to, location.pathname));
            const detailsKey = group.heading ?? `group-${gi}`;
            const detailsOpen = view.detailsOpen[detailsKey] ?? activeInDetails;
            return (
              <div
                key={group.heading ?? `group-${gi}`}
                // Issue #466 (UX-13): 将来フェーズのグループも半透明にしない。
                // リンクは操作できるので、無効に見せてはいけない。フェーズの
                // 位置は見出しの文字 (「現在」「これから」) で伝える。
                className="space-y-0.5"
                data-testid={slug ? `sidebar-group-${slug}` : undefined}
                // Issue #257: only phase-linked groups (Setup/Understand/
                // Instrument/Observe & Evaluate/Publish) carry this attribute;
                // Overview and Other have no phase and stay undefined.
                data-phase-state={group.phases ? phaseState : undefined}
              >
                {group.heading && !collapsed && (
                  <p
                    className={cn(
                      "flex items-center gap-1.5 px-3 pb-1 text-xs font-semibold uppercase tracking-wide",
                      phaseState === "current" ? "text-primary" : "text-muted-foreground",
                    )}
                  >
                    {group.heading}
                    {phaseState === "current" && (
                      <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium normal-case tracking-normal text-primary">
                        現在
                      </span>
                    )}
                    {phaseState === "future" && (
                      <span className="rounded-full border px-1.5 py-0.5 text-[10px] font-medium normal-case tracking-normal text-muted-foreground">
                        これから
                      </span>
                    )}
                  </p>
                )}
                {primary.map((item) => (
                  <NavItemLink
                    key={item.to}
                    item={item}
                    collapsed={collapsed}
                    active={isItemActive(item.to, location.pathname)}
                    onNavigate={onNavigate}
                  />
                ))}
                {details.length > 0 && (
                  <details
                    open={detailsOpen}
                    onToggle={(e) => {
                      const open = (e.currentTarget as HTMLDetailsElement).open;
                      if (open !== detailsOpen) view.setDetailsOpen(detailsKey, open);
                    }}
                    data-testid={slug ? `sidebar-details-${slug}` : undefined}
                  >
                    <summary className="cursor-pointer rounded-lg px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-secondary/50 hover:text-foreground">
                      詳細ビュー({details.length})
                    </summary>
                    <div className="space-y-0.5 pl-2">
                      {details.map((item) => (
                        <NavItemLink
                          key={item.to}
                          item={item}
                          collapsed={false}
                          active={isItemActive(item.to, location.pathname)}
                          onNavigate={onNavigate}
                        />
                      ))}
                    </div>
                  </details>
                )}
              </div>
            );
          })}
        </>
      )}
    </nav>
  );
}

export interface SidebarProps {
  /**
   * Issue #362: whether the mobile navigation Drawer is open. Owned by
   * `AppLayout` so the header's menu button and the Drawer stay in sync.
   * Defaults to `false` so the component can still be rendered on its own.
   */
  navOpen?: boolean;
  /** Called when the Drawer asks to be closed (backdrop, Escape, close button, navigation). */
  onNavClose?: () => void;
  /** Element id of the Drawer panel; the header's `aria-controls` target. */
  drawerId?: string;
  /** Focus is returned here when the Drawer closes (the header's menu button). */
  returnFocusRef?: RefObject<HTMLButtonElement | null>;
}

export function Sidebar({
  navOpen = false,
  onNavClose,
  drawerId = MOBILE_NAV_DRAWER_ID,
  returnFocusRef,
}: SidebarProps = {}) {
  const { isAdmin } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  // Issue #257: drives phase-linked emphasis/dimming below. Missing data
  // (older server, loading, error) falls through to `phaseGroupState`'s
  // "none" branch, which renders every group normally.
  const { data: systemState } = useSystemState();
  const userPhase = systemState?.user_phase;
  const drawerRef = useRef<HTMLDivElement>(null);
  // UX-13: 検索語と詳細ビューの開閉は、デスクトップのレールと Drawer で共有する
  // (Drawer は開くたびにマウントされるので、ここで持たないと毎回リセットされる)。
  const [query, setQuery] = useState("");
  const [detailsOpen, setDetailsOpenState] = useState<Record<string, boolean>>({});
  const view: NavViewState = {
    query,
    setQuery,
    detailsOpen,
    setDetailsOpen: (heading, open) => setDetailsOpenState((prev) => ({ ...prev, [heading]: open })),
  };

  // Issue #362: 開いている間の Drawer はモーダルな面 -- Escape で閉じ、
  // Tab / Shift+Tab は中で循環し、開いたら中へ、閉じたらメニューボタンへ
  // フォーカスが戻る。同じ規則をアシスタントパネルも使うので、実装は
  // `lib/modal-surface.ts` に 1 つだけ置く。
  useModalSurface({
    open: navOpen,
    onClose: () => onNavClose?.(),
    panelRef: drawerRef,
    returnFocusRef,
  });

  const groups: NavGroup[] = isAdmin
    ? NAV_GROUPS.map((g, i) =>
        i === NAV_GROUPS.length - 1
          ? { ...g, items: [...g.items, { to: "/admin", icon: Users, label: "Admin" }] }
          : g,
      )
    : NAV_GROUPS;

  return (
    <>
      {/* Static rail. Issue #362: `hidden md:flex` so it contributes no
          horizontal space below the md breakpoint -- on a 390px screen the
          navigation lives in the Drawer instead and <main> keeps the width. */}
      <aside
        data-testid="sidebar-rail"
        className={cn(
          "hidden md:flex flex-col border-r bg-card transition-all duration-200",
          collapsed ? "w-16" : "w-56",
        )}
      >
        <div className={cn("flex items-center gap-2 border-b px-4 h-14", collapsed && "justify-center px-2")}>
          <SidebarBrand collapsed={collapsed} />
        </div>

        <SidebarNavList groups={groups} collapsed={collapsed} userPhase={userPhase} view={view} />

        <button
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? "ナビゲーションを展開する" : "ナビゲーションを折りたたむ"}
          aria-expanded={!collapsed}
          data-testid="sidebar-collapse-toggle"
          className="flex items-center justify-center border-t py-3 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </aside>

      {/* Mobile Drawer. Rendered only while open, and only visible below md. */}
      {navOpen && (
        <>
          <div
            data-testid="mobile-nav-backdrop"
            aria-hidden="true"
            onClick={onNavClose}
            className="fixed inset-0 z-40 bg-black/50 md:hidden"
          />
          <div
            id={drawerId}
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="ナビゲーション"
            tabIndex={-1}
            data-testid="mobile-nav-drawer"
            className="fixed inset-y-0 left-0 z-50 flex w-64 max-w-[85vw] flex-col border-r bg-card shadow-xl outline-none md:hidden"
          >
            <div className="flex items-center gap-2 border-b px-4 h-14">
              <SidebarBrand collapsed={false} />
              <button
                type="button"
                onClick={onNavClose}
                aria-label="ナビゲーションを閉じる"
                data-testid="mobile-nav-close"
                className="ml-auto flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <SidebarNavList
              groups={groups}
              collapsed={false}
              userPhase={userPhase}
              onNavigate={onNavClose}
              view={view}
            />
          </div>
        </>
      )}
    </>
  );
}
