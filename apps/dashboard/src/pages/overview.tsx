import { Link } from "react-router-dom";
import {
  useComponents, useSystemState, useConnectivityStatus,
  useLatestSnapshot, useLatestDrafts, useMyTokens,
} from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { SystemStateBanner } from "@/components/system-state";
import { PrerequisiteGuide } from "@/components/prerequisite-guide";
import { Boxes, Activity, Clock, FolderCog, Compass, Plug, RadioTower, CheckCircle2 } from "lucide-react";
import { formatTimestamp } from "@/lib/utils";

// Deterministic, ordered get-started steps for a brand-new System with zero
// components (Issue #212). This is a static fallback list, not a heuristic
// recommendation — every System needs the repository configured, System
// Understanding built, and the SDK connected, in this order.
// Issue #259: closes the dead end at step 3 -- once the SDK is connected
// there was previously no onward link telling the developer where to go
// look at the traces that start arriving. Step 4 is the only one with a
// deterministic completion signal today (ConnectivityStatus, the same
// finite state machine the header connectivity badge already reads, Issue
// #165); the first three steps stay plain links, unchanged.
// Issue #267: every step now gets its own deterministic completion signal
// (previously only step 4 did), reusing the same presence-check pattern as
// `components/prerequisite-checklist.tsx` -- no heuristic inference, just
// existing API facts. `required` marks steps that are NOT technically
// necessary to reach step 3->4 (connect-sdk.tsx's token issuance does not
// depend on Repository/System Understanding being done first), per Issue
// #267 item 2.
const GET_STARTED_STEPS = [
  {
    to: "/repository", label: "1. Configure the repository", icon: FolderCog,
    testId: "overview-link-repository", required: false,
  },
  {
    to: "/system-understanding", label: "2. Build System Understanding", icon: Compass,
    testId: "overview-link-system-understanding", required: false,
  },
  {
    to: "/connect-sdk", label: "3. Connect the SDK", icon: Plug,
    testId: "overview-link-connect-sdk", required: true,
  },
  {
    to: "/components", label: "4. View traces in Components", icon: RadioTower,
    testId: "overview-link-view-traces", required: true,
  },
] as const;

const MODE_VARIANT = {
  off: "secondary",
  trace: "success",
  shadow: "warning",
} as const;

export default function OverviewPage() {
  const { systems, systemId } = useAuth();
  const { data: components, isLoading } = useComponents();
  // Canonical state projection (Issue #206); "/" carries no server-side
  // items today (no rule targets the Overview route), so this is a cheap,
  // optional primary item and the static ordered list below is always the
  // deterministic fallback (Issue #212).
  const { data: systemState } = useSystemState();
  const primaryOverviewItem = systemState?.page_items["/"]?.[0] ?? null;
  // Issue #259: drives step 4's completion below -- "receiving" is the same
  // state the header ConnectivityBadge treats as "done" (it hides itself
  // once real, non-smoke traces have arrived).
  const { data: connectivity } = useConnectivityStatus();
  // Issue #267: deterministic completion facts for steps 1-3, reusing the
  // same hooks/pattern as `PrerequisiteChecklist` (snapshot presence, System
  // Profile Draft presence) plus `connect-sdk.tsx`'s own token list.
  const { data: latestSnapshot } = useLatestSnapshot();
  const { data: latestDrafts } = useLatestDrafts();
  const { data: myTokens } = useMyTokens();
  const stepDone: Record<string, boolean> = {
    "/repository": !!latestSnapshot,
    "/system-understanding": !!latestDrafts?.system_profile_draft,
    "/connect-sdk": (myTokens?.length ?? 0) > 0,
    "/components": connectivity?.state === "receiving",
  };

  const system = systems.find((s) => s.id === systemId);
  const totalTraces = components?.reduce((s, c) => s + c.trace_count, 0) ?? 0;
  const lastSeen = components?.reduce((max, c) =>
    c.last_seen && (!max || c.last_seen > max) ? c.last_seen : max, null as number | null);
  const modeCount = components?.reduce((acc, c) => {
    acc[c.mode] = (acc[c.mode] || 0) + 1;
    return acc;
  }, {} as Record<string, number>) ?? {};

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Overview</h1>
        {system && (
          <p className="text-muted-foreground mt-1">
            {system.name}{system.environment ? ` — ${system.environment}` : ""}
          </p>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          title="Components"
          value={isLoading ? undefined : String(components?.length ?? 0)}
          icon={<Boxes className="h-4 w-4 text-muted-foreground" />}
        />
        <MetricCard
          title="Total Traces"
          value={isLoading ? undefined : totalTraces.toLocaleString()}
          icon={<Activity className="h-4 w-4 text-muted-foreground" />}
        />
        <MetricCard
          title="Last Seen"
          value={isLoading ? undefined : formatTimestamp(lastSeen)}
          icon={<Clock className="h-4 w-4 text-muted-foreground" />}
        />
        <MetricCard
          title="Active Modes"
          value={isLoading ? undefined : Object.entries(modeCount).map(([m, n]) => `${m}: ${n}`).join(", ") || "—"}
          icon={<Activity className="h-4 w-4 text-muted-foreground" />}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Components</CardTitle>
        </CardHeader>
        <CardContent>
          {systems.length === 0 ? (
            // Issue #265: distinct from the zero-*components* state below --
            // there is no System selected at all yet, so components/traces
            // cannot exist. Point at the header's "System を作成" control
            // instead of showing the (System-scoped) get-started list.
            <div className="space-y-2 py-8 text-center" data-testid="overview-no-systems">
              <p className="text-sm font-medium">System を作成してください。</p>
              <p className="text-sm text-muted-foreground">
                トレースやコンポーネントを表示するには、まずヘッダーの
                「System を作成」から System を作成してください。
              </p>
            </div>
          ) : isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
            </div>
          ) : !components?.length ? (
            <div className="space-y-4 py-4" data-testid="overview-get-started">
              {primaryOverviewItem && (
                <SystemStateBanner item={primaryOverviewItem} testId="overview-primary-item" />
              )}
              {/* Issue #241: phase-appropriate start guide for a System that
                  is still in setup/preparation. It renders nothing once
                  preparation is complete (Issue #256's later
                  instrumentation/observation/evaluation/publish phases), so
                  the ordered fallback list below is what a fully-set-up
                  System with no components still sees. */}
              <PrerequisiteGuide testId="overview-prerequisite-guide" />
              <p className="text-sm text-muted-foreground text-center">
                No components registered yet. Connect the SDK to start tracing.
              </p>
              {/* Issue #267 item 2: the numbered order below is a suggested
                  path, not a technical dependency -- SDK token issuance
                  (step 3, connect-sdk.tsx) does not require the repository or
                  System Understanding to be configured first. */}
              <p className="text-xs text-muted-foreground text-center" data-testid="overview-get-started-shortest-path-note">
                最短でTraceを見るには手順3→4だけで十分です。手順1・2は
                <span className="font-medium">推奨（必須ではありません）</span>。
              </p>
              <ol className="mx-auto max-w-sm space-y-2 text-sm">
                {GET_STARTED_STEPS.map(({ to, label, icon: Icon, testId, required }) => {
                  const done = stepDone[to] ?? false;
                  return (
                    <li key={to}>
                      <Link
                        to={to}
                        data-testid={testId}
                        data-done={done}
                        className="flex items-center gap-2 rounded-md border px-3 py-2 text-primary hover:bg-accent hover:underline"
                      >
                        {done ? (
                          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" data-testid={`${testId}-done`} />
                        ) : (
                          <Icon className="h-4 w-4 shrink-0" />
                        )}
                        <span>
                          {label}
                          {!required && (
                            <span className="ml-1.5 text-xs text-muted-foreground">
                              推奨(必須ではない)
                            </span>
                          )}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ol>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="pb-2 font-medium text-muted-foreground">Component ID</th>
                    <th className="pb-2 font-medium text-muted-foreground">Mode</th>
                    <th className="pb-2 font-medium text-muted-foreground text-right">Traces</th>
                    <th className="pb-2 font-medium text-muted-foreground text-right">Last Seen</th>
                  </tr>
                </thead>
                <tbody>
                  {components.map((c) => (
                    <tr key={c.component_id} className="border-b last:border-0">
                      <td className="py-3 font-mono text-xs">{c.component_id}</td>
                      <td className="py-3">
                        <Badge variant={MODE_VARIANT[c.mode] ?? "secondary"}>{c.mode}</Badge>
                      </td>
                      <td className="py-3 text-right">{c.trace_count.toLocaleString()}</td>
                      <td className="py-3 text-right text-muted-foreground text-xs">
                        {formatTimestamp(c.last_seen)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function MetricCard({ title, value, icon }: { title: string; value?: string; icon: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          {icon}
        </div>
        {value === undefined ? (
          <Skeleton className="mt-2 h-7 w-20" />
        ) : (
          <p className="mt-2 text-2xl font-bold">{value}</p>
        )}
      </CardContent>
    </Card>
  );
}
