import { useEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  useSystemUnderstanding,
  useBuildSystemUnderstanding,
  useLatestSystemUnderstandingBuild,
  useSystemDiagnostics,
  useSystemState,
  sysKey,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { ContextHeader } from "@/components/layout/context-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDiagnosticHighlight, DiagnosticFixCallout } from "@/components/diagnostic-fix";
import { cn } from "@/lib/utils";
import { SystemStateBanner } from "@/components/system-state";
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
  SystemUnderstandingStageStatus,
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

function findStage(
  stages: SystemUnderstandingStageStatus[] | undefined,
  stage: "understand" | "observe" | "instrument" | "evaluate",
): SystemUnderstandingStageStatus | undefined {
  return stages?.find((s) => s.stage === stage);
}

/**
 * Issue #202: Instrument stage summary. Replaces the previous static
 * description with a counts-based summary (Proposed / Approved without
 * patch / Validated) linking to Probe Planner, falling back to the original
 * description text when counts are all zero (or absent, e.g. an older
 * response without `stages`).
 */
function InstrumentSummary({ counts }: { counts?: Record<string, number> }) {
  const proposed = counts?.proposed ?? 0;
  const approvedWithoutPatch = counts?.approved_without_patch ?? 0;
  const validated = counts?.validated ?? 0;
  const hasCounts = proposed > 0 || approvedWithoutPatch > 0 || validated > 0;

  if (!hasCounts) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="stage-summary-instrument">
        Probe plan and patch status live in Probe Planner. Approve a plan and validate
        its patch there once observation points are chosen above.
      </p>
    );
  }

  return (
    <ul className="text-sm space-y-1" data-testid="stage-summary-instrument">
      <li>
        <Link to="/probe-planner" className="text-primary hover:underline">Proposed</Link>
        : {proposed}
      </li>
      <li>
        <Link to="/probe-planner" className="text-primary hover:underline">Approved without patch</Link>
        : {approvedWithoutPatch}
      </li>
      <li>
        <Link to="/probe-planner" className="text-primary hover:underline">Validated</Link>
        : {validated}
      </li>
    </ul>
  );
}

/**
 * Issue #202: Evaluate stage summary. Same counts-with-fallback pattern as
 * InstrumentSummary, linking to Experiments.
 */
function EvaluateSummary({ counts }: { counts?: Record<string, number> }) {
  const undecided = counts?.undecided ?? 0;
  const decided = counts?.decided ?? 0;
  const hasCounts = undecided > 0 || decided > 0;

  if (!hasCounts) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="stage-summary-evaluate">
        Trace comparisons, experiment runs, and adoption decisions live in Experiments.
      </p>
    );
  }

  return (
    <ul className="text-sm space-y-1" data-testid="stage-summary-evaluate">
      <li>
        <Link to="/experiments" className="text-primary hover:underline">Undecided</Link>
        : {undecided}
      </li>
      <li>
        <Link to="/experiments" className="text-primary hover:underline">Decided</Link>
        : {decided}
      </li>
    </ul>
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
  const understandStage = findStage(data.stages, "understand");
  const observeStage = findStage(data.stages, "observe");
  const instrumentStage = findStage(data.stages, "instrument");
  const evaluateStage = findStage(data.stages, "evaluate");

  return (
    <div className="space-y-10">
      <StageSection
        stage="understand"
        index={1}
        actions={actionsByStage.understand}
        status={understandStage?.status}
        counts={understandStage?.counts}
      >
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
        <GapWorklist
          gaps={data.gaps}
          gapSummary={data.gap_summary}
          gapTrend={data.gap_trend}
          snapshotId={data.snapshot_id}
          commitSha={data.commit_sha}
        />
      </StageSection>

      <StageSection
        stage="observe"
        index={2}
        actions={actionsByStage.observe}
        status={observeStage?.status}
        counts={observeStage?.counts}
      >
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

      {/*
        Issue #202: instrument/evaluate render their counts in a dedicated
        summary block below (InstrumentSummary / EvaluateSummary) instead of
        the generic heading counts line, so the numbers aren't shown twice.
      */}
      <StageSection
        stage="instrument"
        index={3}
        actions={actionsByStage.instrument}
        status={instrumentStage?.status}
      >
        <InstrumentSummary counts={instrumentStage?.counts} />
      </StageSection>

      <StageSection
        stage="evaluate"
        index={4}
        actions={actionsByStage.evaluate}
        status={evaluateStage?.status}
      >
        <EvaluateSummary counts={evaluateStage?.counts} />
      </StageSection>
    </div>
  );
}

export default function SystemUnderstandingPage() {
  const { data, isLoading, error } = useSystemUnderstanding();
  const { data: diagnostics } = useSystemDiagnostics();
  const { data: systemState } = useSystemState();
  const build = useBuildSystemUnderstanding();
  const { data: latestBuild } = useLatestSystemUnderstandingBuild();
  const qc = useQueryClient();
  const settledBuildId = useRef<number | null>(null);

  const buildRunning = latestBuild?.status === "queued" || latestBuild?.status === "running";
  const buildHighlight = useDiagnosticHighlight<HTMLButtonElement>("build");
  const pageItem = systemState?.page_items["/system-understanding"]?.[0] ?? systemState?.primary_item;

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

      <SystemStateBanner
        item={pageItem}
        onAction={pageItem?.user_action_kind === "build" ? () => build.mutate() : undefined}
        disabled={pageItem?.user_action_kind === "build" && (build.isPending || buildRunning)}
      />

      {/* Issue #203: the improvement-loop banner — a materialized Interview
          change is newer than the latest completed build, so the current
          understanding no longer reflects it. Hidden while a build is
          actively running (the BuildJobPanel already shows progress, and a
          fresh build is about to make this stale anyway). Also hidden when
          the canonical SystemStateBanner above is already showing this same
          root cause (Issue #206-208 review): the canonical banner wins so
          the same cause is never duplicated. */}
      {data?.understanding_refresh_recommended && !buildRunning &&
        pageItem?.state_id !== "interview.materialized.rebuild_required" && (
        <Card data-testid="refresh-recommended-banner">
          <CardContent className="py-4 flex items-center justify-between gap-4 flex-wrap">
            <div>
              <p className="text-sm font-medium">
                Interview の変更が理解にまだ反映されていません
              </p>
              <p className="text-sm text-muted-foreground mt-1">
                Interview で確定した変更を反映するには、システム理解を再ビルドしてください。
              </p>
            </div>
            <Button
              onClick={() => build.mutate()}
              disabled={build.isPending}
              data-testid="refresh-recommended-cta"
            >
              Build / Refresh
            </Button>
          </CardContent>
        </Card>
      )}

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
