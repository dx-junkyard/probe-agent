import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard, GitBranch, Map, Crosshair, FlaskConical,
  Plug, Sparkles, Boxes, Settings, Users, ChevronLeft, ChevronRight, MessageSquare,
  Workflow, Network, MessageSquareText,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/api/auth";
import { useState } from "react";

const NAV = [
  { to: "/", icon: LayoutDashboard, label: "Overview" },
  { to: "/repository", icon: GitBranch, label: "Repository" },
  { to: "/feature-map", icon: Map, label: "Feature Map" },
  { to: "/capability-map", icon: Network, label: "Capability Map" },
  { to: "/interview", icon: MessageSquareText, label: "Interview" },
  { to: "/flow-explorer", icon: Workflow, label: "Flow Explorer" },
  { to: "/probe-planner", icon: Crosshair, label: "Probe Planner" },
  { to: "/experiments", icon: FlaskConical, label: "Experiments" },
  { to: "/connect-sdk", icon: Plug, label: "Connect SDK" },
  { to: "/generation", icon: Sparkles, label: "Generate" },
  { to: "/components", icon: Boxes, label: "Components" },
  { to: "/workspaces", icon: MessageSquare, label: "Decision Workspace" },
  { to: "/settings", icon: Settings, label: "Settings" },
];

export function Sidebar() {
  const { isAdmin } = useAuth();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);

  const items = isAdmin ? [...NAV, { to: "/admin", icon: Users, label: "Admin" }] : NAV;

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

      <nav className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
        {items.map((item) => {
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
