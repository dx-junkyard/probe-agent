// Issue #432 (Epic #427) P2 review fix §3.6: the WAI-ARIA Tabs pattern
// (https://www.w3.org/WAI/ARIA/apps/patterns/tabs/) -- `role="tablist"` /
// `role="tab"` / `role="tabpanel"`, `aria-selected` / `aria-controls` /
// `aria-labelledby`, and roving-tabindex arrow-key navigation (Left/Right
// move AND activate the adjacent tab -- automatic activation, matching the
// existing click-to-activate behaviour; Home/End jump to the first/last
// tab). This is a SHARED component used by every tabbed screen in the
// Dashboard (Objective Map, Repository, Simulation Workbench, UX Design
// Studio, GitHub, Candidate Studio, Components, Setup Guide, Admin, Trace
// Analyzers, Feature Map) -- every one of them renders `TabsList` >
// `TabsTrigger`* and sibling `TabsContent`* with no other structural
// assumption, so this change is additive (new roles/attributes/keyboard
// handling) and does not alter any existing `data-testid`, className, or
// click behaviour.

import { createContext, useContext, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/lib/utils";

const TabsContext = createContext<{ value: string; onChange: (v: string) => void; baseId: string }>({
  value: "",
  onChange: () => {},
  baseId: "tabs",
});

function Tabs({ defaultValue, value, onValueChange, children, className }: {
  defaultValue?: string;
  value?: string;
  onValueChange?: (v: string) => void;
  children: ReactNode;
  className?: string;
}) {
  const [internal, setInternal] = useState(defaultValue ?? "");
  const current = value ?? internal;
  const onChange = onValueChange ?? setInternal;
  // Unique per <Tabs> instance, so two independent tab groups on one page
  // (or two renders of this component) never collide on tab/panel ids.
  const baseId = useId();
  return (
    <TabsContext.Provider value={{ value: current, onChange, baseId }}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

function TabsList({ children, className, ...rest }: {
  children: ReactNode;
  className?: string;
  "data-testid"?: string;
}) {
  const ctx = useContext(TabsContext);
  const listRef = useRef<HTMLDivElement>(null);

  // Roving tabindex arrow-key navigation over whatever `[role="tab"]`
  // elements are actually rendered right now -- reading the DOM instead of a
  // prop-driven list of values means a conditionally-rendered TabsTrigger
  // (several screens hide one behind a permission/feature check) is
  // included or skipped correctly with no separate registration API.
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    const tabs = Array.from(
      listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)') ?? [],
    );
    if (tabs.length === 0) return;
    const currentIndex = tabs.findIndex((el) => el.getAttribute("data-value") === ctx.value);
    let nextIndex: number;
    if (event.key === "ArrowRight") nextIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % tabs.length;
    else if (event.key === "ArrowLeft") nextIndex = currentIndex < 0 ? tabs.length - 1 : (currentIndex - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else nextIndex = tabs.length - 1;
    const nextTab = tabs[nextIndex];
    const nextValue = nextTab?.getAttribute("data-value");
    if (!nextTab || !nextValue) return;
    event.preventDefault();
    ctx.onChange(nextValue);
    nextTab.focus();
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      className={cn("inline-flex h-9 items-center justify-center rounded-lg bg-muted p-1 text-muted-foreground", className)}
      onKeyDown={handleKeyDown}
      {...rest}
    >
      {children}
    </div>
  );
}

function TabsTrigger({ value, children, className, disabled, ...rest }: {
  value: string;
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  "data-testid"?: string;
}) {
  const ctx = useContext(TabsContext);
  const active = ctx.value === value;
  return (
    <button
      type="button"
      role="tab"
      id={`${ctx.baseId}-tab-${value}`}
      aria-controls={`${ctx.baseId}-panel-${value}`}
      aria-selected={active}
      data-value={value}
      // Roving tabindex: only the active tab is Tab-key reachable; arrow
      // keys move focus within the tablist (WAI-ARIA Tabs pattern).
      tabIndex={active ? 0 : -1}
      disabled={disabled}
      className={cn(
        "inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 cursor-pointer",
        active ? "bg-background text-foreground shadow" : "hover:bg-background/50",
        className,
      )}
      onClick={() => ctx.onChange(value)}
      {...rest}
    >
      {children}
    </button>
  );
}

function TabsContent({ value, children, className, ...rest }: {
  value: string;
  children: ReactNode;
  className?: string;
  "data-testid"?: string;
}) {
  const ctx = useContext(TabsContext);
  if (ctx.value !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`${ctx.baseId}-panel-${value}`}
      aria-labelledby={`${ctx.baseId}-tab-${value}`}
      tabIndex={0}
      className={cn("mt-2", className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export { Tabs, TabsList, TabsTrigger, TabsContent };
