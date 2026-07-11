import { useEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  useSystemUnderstanding,
  useBuildSystemUnderstanding,
  useLatestSystemUnderstandingBuild,
  useSystemDiagnostics,
  sysKey,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { ContextHeader } from "@/components/layout/context-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDiagnosticHighlight, DiagnosticFixCallout } from "@/components/diagnostic-fix";
import { cn } from "@/lib/utils";
import {
  RefreshCw, Boxes, Target,
} from "lucide-react";
import { PipelineChecklist } from "@/components/system-understanding/pipeline-checklist";
import { BuildJobPanel, TERMINAL_JOB_STATUSES } from "@/components/system-understanding/build-job-panel";
import { GapWorklist } from "@/components/system-understanding/gap-worklist";
import {
  StageSection, groupNextActionsByStage,
} from "@/components/system-understanding/stage-sections";
import type {
  SystemDiagnosticCheck,
  SystemUnderstandingNextAction,
  SystemUnderstandingOut,
} from "@/api/types";

/**
 * Issue #201: single highest-priority CTA shown right under the Hub header,
 * derived server-side (`primary_action`, system_understanding_service._derive_primary_action).
 * "navigate" actions link somewhere; "build" actions trigger the same
 * Build / Refresh job as the header button and share its disabled condition.
 */
function PrimaryActionCard({ action, onRunBuild, buildDisabled }: {
  action: SystemUnderstandingNextAction;
  onRunBuild: () => void;
  buildDisabled: boolean;
}) {
  const kind = action.action_kind ?? "navigate";
  return (
    <Card data-testid="primary-action">
      <CardContent className="py-4 flex items-center justify-between gap-4 flex-wrap">
        <div>
          <p className="text-lg font-semibold">{action.action}</p>
          <p className="text-sm text-muted-foreground mt-1">{action.reason}</p>
        </div>
        {kind === "build" ? (
          <Button
            onClick={onRunBuild}
            disabled={buildDisabled}
            data-testid="primary-action-cta"
          >
            {action.action}
          </Button>
        ) : action.link ? (
          <Link
            to={action.link}
            data-testid="primary-action-cta"
            className={cn(buttonVariants({ variant: "default" }))}
          >
            {action.action}
          </Link>
        ) : null}
      </CardContent>
    </Card>
  );
}

function EntryCards() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Link to="/capability-map" className="block group">
        <Card className="h-full transition-colors group-hover:border-primary/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Boxes className="h-4 w-4" />
              Start from Capability
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Browse capabilities discovered from source metadata and code structure.
            Drill into elements, see related APIs, and identify probe candidates.
          </CardContent>
        </Card>
      </Link>
      <Link to="/feature-map" className="block group">
        <Card className="h-full transition-colors group-hover:border-primary/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Target className="h-4 w-4" />
              Start from Feature
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Explore features extracted from documentation, map them to code symbols
            via Feature-to-Code Links, and trace their capability connections.
          </CardContent>
        </Card>
      </Link>
    </div>
  );
}

function DataView({ data, checksByStep, onRunBuild, buildDisabled }: {
  data: SystemUnderstandingOut;
  checksByStep: Record<string, SystemDiagnosticCheck[]>;
  onRunBuild: () => void;
  buildDisabled: boolean;
}) {
  const pipeline = data.pipeline ?? [];
  const allMissing = pipeline.every((s) => s.status === "missing");
  const actionsByStage = groupNextActionsByStage(data.next_actions);

  return (
    <div className="space-y-10">
      <StageSection stage="understand" index={1} actions={actionsByStage.understand}>
        {/* Pipeline Checklist */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Pipeline Status</CardTitle>
            <CardDescription>
              Progress through the system understanding pipeline. Steps that are
              missing or blocked show a "Why?" button when a configuration
              diagnostic or a recent run failure explains them.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PipelineChecklist
              steps={pipeline}
              checksByStep={checksByStep}
              onRunBuild={onRunBuild}
              buildDisabled={buildDisabled}
            />
          </CardContent>
        </Card>

        {!allMissing && <EntryCards />}

        <div className="grid gap-6 lg:grid-cols-2">
          {/* System Purpose */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">System Purpose</CardTitle>
            </CardHeader>
            <CardContent>
              {data.purpose ? (
                <div>
                  <p className="font-medium">{data.purpose.name}</p>
                  {data.purpose.summary && (
                    <p className="text-sm text-muted-foreground mt-1">{data.purpose.summary}</p>
                  )}
                  {data.purpose.provenance_kind && (
                    <Badge variant="outline" className="mt-2 text-xs">{data.purpose.provenance_kind}</Badge>
                  )}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No system purpose defined yet.</p>
              )}
            </CardContent>
          </Card>

          {/* Metadata Coverage */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Metadata Coverage</CardTitle>
            </CardHeader>
            <CardContent>
              {data.metadata_coverage ? (
                <div className="grid grid-cols-2 gap-4 text-sm" data-testid="metadata-coverage">
                  <div>
                    <p className="text-muted-foreground">Symbols</p>
                    <p className="text-lg font-semibold">{data.metadata_coverage.symbol_count}</p>
                    <p className="text-xs text-muted-foreground">
                      {data.metadata_coverage.symbols_with_source_metadata} with metadata
                    </p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Entrypoints</p>
                    <p className="text-lg font-semibold">{data.metadata_coverage.entrypoint_count}</p>
                    <p className="text-xs text-muted-foreground">
                      {data.metadata_coverage.entrypoints_with_capability_link} with capability link
                    </p>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Run a build to see coverage.</p>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Core Capabilities */}
        {data.capabilities.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Core Capabilities</CardTitle>
              <CardDescription>
                <Link to="/capability-map" className="hover:underline text-primary">
                  View full Capability Map
                </Link>
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {data.capabilities.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <Badge variant="outline" className="mt-0.5 shrink-0">{c.provenance_kind ?? "unknown"}</Badge>
                    <div>
                      <Link to={`/capability-map?capability=${encodeURIComponent(c.name)}`} className="font-medium hover:underline">{c.name}</Link>
                      {c.summary && <p className="text-muted-foreground text-xs">{c.summary}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {/* Major Symbols */}
        {data.major_symbols.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Major Functions / Source Symbols</CardTitle>
              <CardDescription>
                <Link to="/repository" className="hover:underline text-primary">
                  View in Repository
                </Link>
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left">
                      <th className="pb-2 font-medium text-muted-foreground">Path</th>
                      <th className="pb-2 font-medium text-muted-foreground">Name</th>
                      <th className="pb-2 font-medium text-muted-foreground">Kind</th>
                      <th className="pb-2 font-medium text-muted-foreground">Route</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.major_symbols.slice(0, 20).map((sym, i) => (
                      <tr key={i} className="border-b last:border-0">
                        <td className="py-1.5 font-mono text-xs">{sym.path}</td>
                        <td className="py-1.5 font-mono text-xs">{sym.qualified_name}</td>
                        <td className="py-1.5">{sym.kind ?? "—"}</td>
                        <td className="py-1.5 font-mono text-xs">
                          {sym.route_method && sym.route_path ? (
                            <Link
                              to={`/flow-explorer?entrypoint_type=api&entrypoint_id=${encodeURIComponent(`${sym.route_method} ${sym.route_path}`)}`}
                              className="text-primary hover:underline"
                              data-testid="symbol-flow-link"
                            >
                              {sym.route_method} {sym.route_path}
                            </Link>
                          ) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {data.major_symbols.length > 20 && (
                  <p className="text-xs text-muted-foreground mt-2">
                    Showing 20 of {data.major_symbols.length}.
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Docs-Code Gap Worklist */}
        <GapWorklist gaps={data.gaps} gapSummary={data.gap_summary} snapshotId={data.snapshot_id} commitSha={data.commit_sha} />
      </StageSection>

      <StageSection stage="observe" index={2} actions={actionsByStage.observe}>
        {/* Key Entrypoints */}
        {data.entrypoints.length > 0 ? (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Key API / Entrypoints</CardTitle>
              <CardDescription>
                <Link to="/flow-explorer" className="hover:underline text-primary">
                  View in Flow Explorer
                </Link>
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left">
                      <th className="pb-2 font-medium text-muted-foreground">Type</th>
                      <th className="pb-2 font-medium text-muted-foreground">ID</th>
                      <th className="pb-2 font-medium text-muted-foreground">Category</th>
                      <th className="pb-2 font-medium text-muted-foreground">Label</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.entrypoints.slice(0, 20).map((ep, i) => (
                      <tr key={i} className="border-b last:border-0">
                        <td className="py-1.5">
                          <Badge variant="outline" className="text-xs">{ep.entrypoint_type}</Badge>
                        </td>
                        <td className="py-1.5 font-mono text-xs">
                          <Link
                            to={`/flow-explorer?entrypoint_type=${encodeURIComponent(ep.entrypoint_type)}&entrypoint_id=${encodeURIComponent(ep.entrypoint_id)}`}
                            className="text-primary hover:underline"
                            data-testid="entrypoint-flow-link"
                          >
                            {ep.entrypoint_id}
                          </Link>
                        </td>
                        <td className="py-1.5">{ep.category ?? "—"}</td>
                        <td className="py-1.5">{ep.label ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {data.entrypoints.length > 20 && (
                  <p className="text-xs text-muted-foreground mt-2">
                    Showing 20 of {data.entrypoints.length}.{" "}
                    <Link to="/flow-explorer" className="hover:underline text-primary">View all</Link>
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        ) : (
          <p className="text-sm text-muted-foreground">
            No entrypoints discovered yet. Once entrypoints are indexed, pick observation
            points here before moving to instrumentation.
          </p>
        )}
      </StageSection>

      <StageSection stage="instrument" index={3} actions={actionsByStage.instrument}>
        <p className="text-sm text-muted-foreground">
          Probe plan and patch status live in Probe Planner. Approve a plan and validate
          its patch there once observation points are chosen above.
        </p>
      </StageSection>

      <StageSection stage="evaluate" index={4} actions={actionsByStage.evaluate}>
        <p className="text-sm text-muted-foreground">
          Trace comparisons, experiment runs, and adoption decisions live in Experiments.
        </p>
      </StageSection>
    </div>
  );
}

export default function SystemUnderstandingPage() {
  const { data, isLoading, error } = useSystemUnderstanding();
  const { data: diagnostics } = useSystemDiagnostics();
  const build = useBuildSystemUnderstanding();
  const { data: latestBuild } = useLatestSystemUnderstandingBuild();
  const qc = useQueryClient();
  const settledBuildId = useRef<number | null>(null);

  const buildRunning = latestBuild?.status === "queued" || latestBuild?.status === "running";
  const buildHighlight = useDiagnosticHighlight<HTMLButtonElement>("build");

  // Refresh the aggregated view and diagnostics once a build job settles.
  useEffect(() => {
    if (!latestBuild) return;
    if (!TERMINAL_JOB_STATUSES.includes(latestBuild.status)) return;
    if (settledBuildId.current === latestBuild.id) return;
    settledBuildId.current = latestBuild.id;
    qc.invalidateQueries({ queryKey: sysKey("system-understanding") });
    qc.invalidateQueries({ queryKey: sysKey("system-diagnostics") });
  }, [latestBuild, qc]);

  const checksByStep = useMemo(() => {
    const map: Record<string, SystemDiagnosticCheck[]> = {};
    for (const check of diagnostics?.checks ?? []) {
      if (check.severity === "ok") continue;
      for (const step of check.related_pipeline_steps) {
        (map[step] ??= []).push(check);
      }
    }
    return map;
  }, [diagnostics]);

  return (
    <div className="space-y-6">
      <ContextHeader />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">System Understanding</h1>
          <p className="text-muted-foreground mt-1">
            {data?.commit_sha
              ? `Snapshot #${data.snapshot_id} — ${data.commit_sha.slice(0, 8)}`
              : "Unified view of what is known about this system"}
          </p>
        </div>
        <Button
          {...buildHighlight}
          onClick={() => build.mutate()}
          disabled={build.isPending || buildRunning}
          variant="default"
          data-testid="build-button"
        >
          {build.isPending || buildRunning ? (
            <>
              <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
              Building...
            </>
          ) : (
            <>
              <RefreshCw className="h-4 w-4 mr-2" />
              Build / Refresh
            </>
          )}
        </Button>
      </div>

      {data?.primary_action && (
        <PrimaryActionCard
          action={data.primary_action}
          onRunBuild={() => build.mutate()}
          buildDisabled={build.isPending || buildRunning}
        />
      )}

      <DiagnosticFixCallout anchor="build" />

      {latestBuild && (buildRunning || latestBuild.is_stuck ||
        latestBuild.status === "failed" || latestBuild.status === "partial" ||
        latestBuild.status === "cancelled") && (
        <div data-testid={buildRunning ? "build-progress" : "build-settled"}>
          <BuildJobPanel job={latestBuild} />
        </div>
      )}

      {(latestBuild?.status === "failed" || latestBuild?.status === "partial") && (
        <Card data-testid="build-failed">
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Last build {latestBuild.status === "partial" ? "partially failed" : "failed"}
              {latestBuild.error ? `: ${latestBuild.error}` : "."}
            </p>
          </CardContent>
        </Card>
      )}

      {error && (
        <Card>
          <CardContent className="py-4">
            <p className="text-sm text-destructive">Failed to load system understanding: {String(error)}</p>
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-32 w-full" />)}
        </div>
      ) : data ? (
        <DataView
          data={data}
          checksByStep={checksByStep}
          onRunBuild={() => build.mutate()}
          buildDisabled={build.isPending || buildRunning}
        />
      ) : null}
    </div>
  );
}
