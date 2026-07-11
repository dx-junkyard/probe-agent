import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard, GitBranch, Map, Crosshair, FlaskConical,
  Plug, Sparkles, Boxes, Settings, Users, ChevronLeft, ChevronRight, MessageSquare,
  Workflow, Network, MessageSquareText, Brain, GitFork, Filter,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/api/auth";
import { useState } from "react";

type NavItem = { to: string; icon: typeof LayoutDashboard; label: string };

// Issue #179: routes are grouped under fixed, explicit headings — no route or
// URL changes, just how the existing list is presented. "Hub" is the System
// Understanding work hub; "Detail views" are the specialist pages it links
// out to; everything else stays under "Other".
type NavGroup = { heading: string | null; items: NavItem[] };

const NAV_GROUPS: NavGroup[] = [
  {
    heading: null,
    items: [{ to: "/", icon: LayoutDashboard, label: "Overview" }],
  },
  {
    heading: "Hub",
    items: [{ to: "/system-understanding", icon: Brain, label: "System Understanding" }],
  },
  {
    heading: "Detail views",
    items: [
      { to: "/repository", icon: GitBranch, label: "Repository" },
      { to: "/capability-map", icon: Network, label: "Capability Map" },
      { to: "/interview", icon: MessageSquareText, label: "Interview" },
      { to: "/feature-map", icon: Map, label: "Feature Map" },
      { to: "/flow-explorer", icon: Workflow, label: "Flow Explorer" },
      { to: "/trace-lineage", icon: GitFork, label: "Trace Lineage" },
      { to: "/trace-analyzers", icon: Filter, label: "Trace Analyzers" },
      { to: "/probe-planner", icon: Crosshair, label: "Probe Planner" },
      { to: "/experiments", icon: FlaskConical, label: "Experiments" },
    ],
  },
  {
    heading: "Other",
    items: [
      { to: "/connect-sdk", icon: Plug, label: "Connect SDK" },
      { to: "/generation", icon: Sparkles, label: "Generate" },
      { to: "/components", icon: Boxes, label: "Components" },
      { to: "/workspaces", icon: MessageSquare, label: "Decision Workspace" },
      { to: "/settings", icon: Settings, label: "Settings" },
    ],
  },
];

export function Sidebar() {
  const { isAdmin } = useAuth();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);

  const groups: NavGroup[] = isAdmin
    ? NAV_GROUPS.map((g, i) =>
        i === NAV_GROUPS.length - 1
          ? { ...g, items: [...g.items, { to: "/admin", icon: Users, label: "Admin" }] }
          : g,
      )
    : NAV_GROUPS;

  return (
    <aside
      className={cn(
        "flex flex-col border-r bg-card transition-all duration-200",
        collapsed ? "w-16" : "w-56",
      )}
    >
      <div className={cn("flex items-center gap-2 border-b px-4 h-14", collapsed && "justify-center px-2")}>
        <div className="h-7 w-7 rounded-lg bg-primary flex items-center justify-center">
          <span className="text-xs font-bold text-primary-foreground">P</span>
        </div>
        {!collapsed && <span className="font-semibold text-sm">Probe Agent</span>}
      </div>

      <nav className="flex-1 overflow-y-auto py-2 px-2 space-y-3" data-testid="sidebar-nav">
        {groups.map((group, gi) => (
          <div key={group.heading ?? `group-${gi}`} className="space-y-0.5" data-testid={group.heading ? `sidebar-group-${group.heading.toLowerCase().replace(/\s+/g, "-")}` : undefined}>
            {group.heading && !collapsed && (
              <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground/70">
                {group.heading}
              </p>
            )}
            {group.items.map((item) => {
              const isActive = item.to === "/"
                ? location.pathname === "/"
                : location.pathname.startsWith(item.to);
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground",
                    collapsed && "justify-center px-2",
                  )}
                  title={collapsed ? item.label : undefined}
                >
                  <item.icon className="h-4 w-4 shrink-0" />
                  {!collapsed && <span>{item.label}</span>}
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>

      <button
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center justify-center border-t py-3 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
      >
        {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
      </button>
    </aside>
  );
}
