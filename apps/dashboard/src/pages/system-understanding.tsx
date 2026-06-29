import { Link } from "react-router-dom";
import { useSystemUnderstanding, useBuildSystemUnderstanding } from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CheckCircle2, XCircle, AlertTriangle, Ban, HelpCircle,
  RefreshCw, ArrowRight, ExternalLink,
} from "lucide-react";
import type {
  SystemUnderstandingPipelineStep,
  SystemUnderstandingNextAction,
  SystemUnderstandingOut,
} from "@/api/types";

const STEP_LABELS: Record<string, string> = {
  repository_configured: "Repository configured",
  snapshot_ready: "Snapshot ready",
  documentation_indexed: "Documentation indexed",
  documentation_claims_scanned: "Documentation claims scanned",
  symbols_indexed: "Code symbols indexed",
  entrypoints_discovered: "Entrypoints discovered",
  docs_code_reconciled: "Docs-code reconciled",
  capability_hierarchy_ready: "Capability hierarchy ready",
};

const STEP_LINKS: Record<string, string> = {
  repository_configured: "/repository",
  snapshot_ready: "/repository",
  symbols_indexed: "/repository",
  entrypoints_discovered: "/flow-explorer",
  capability_hierarchy_ready: "/capability-map",
  docs_code_reconciled: "/system-understanding",
};

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "complete":
      return <CheckCircle2 className="h-4 w-4 text-green-600" />;
    case "warning":
      return <AlertTriangle className="h-4 w-4 text-yellow-600" />;
    case "failed":
      return <XCircle className="h-4 w-4 text-red-600" />;
    case "blocked":
      return <Ban className="h-4 w-4 text-orange-500" />;
    default:
      return <HelpCircle className="h-4 w-4 text-muted-foreground" />;
  }
}

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "complete": return "default";
    case "warning": return "secondary";
    case "failed": return "destructive";
    case "blocked": return "outline";
    default: return "outline";
  }
}

function PipelineChecklist({ steps }: { steps: SystemUnderstandingPipelineStep[] }) {
  return (
    <ul className="space-y-2" data-testid="pipeline-checklist">
      {steps.map((s) => {
        const link = STEP_LINKS[s.step];
        const label = STEP_LABELS[s.step] ?? s.step;
        return (
          <li key={s.step} className="flex items-center gap-3 text-sm">
            <StatusIcon status={s.status} />
            <span className="flex-1">
              {link ? (
                <Link to={link} className="hover:underline">{label}</Link>
              ) : (
                label
              )}
            </span>
            <Badge variant={statusVariant(s.status)} className="text-xs">
              {s.status}
            </Badge>
            {s.detail && (
              <span className="text-xs text-muted-foreground ml-1">{s.detail}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function NextActionsList({ actions }: { actions: SystemUnderstandingNextAction[] }) {
  return (
    <ul className="space-y-2" data-testid="next-actions">
      {actions.map((a, i) => (
        <li key={i} className="flex items-start gap-3 text-sm">
          <ArrowRight className="h-4 w-4 text-primary mt-0.5 shrink-0" />
          <div className="flex-1">
            <span className="font-medium">
              {a.link ? (
                <Link to={a.link} className="hover:underline">{a.action}</Link>
              ) : (
                a.action
              )}
            </span>
            <p className="text-muted-foreground text-xs mt-0.5">{a.reason}</p>
          </div>
          {a.link && (
            <Link to={a.link}>
              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground hover:text-foreground" />
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

function EmptyState() {
  return (
    <Card>
      <CardContent className="py-10 text-center">
        <h3 className="text-lg font-semibold mb-4">Get started with System Understanding</h3>
        <ol className="text-sm text-muted-foreground space-y-2 text-left max-w-md mx-auto list-decimal list-inside">
          <li><Link to="/repository" className="hover:underline text-primary">Configure your repository</Link></li>
          <li>Create a snapshot from a commit</li>
          <li>Index README/docs and source code</li>
          <li>Build system understanding</li>
          <li><Link to="/capability-map" className="hover:underline text-primary">Explore capabilities and API boundaries</Link></li>
        </ol>
      </CardContent>
    </Card>
  );
}

function DataView({ data }: { data: SystemUnderstandingOut }) {
  const pipeline = data.pipeline ?? [];
  const allMissing = pipeline.every((s) => s.status === "missing");

  return (
    <div className="space-y-6">
      {/* Pipeline Checklist */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pipeline Status</CardTitle>
          <CardDescription>Progress through the system understanding pipeline</CardDescription>
        </CardHeader>
        <CardContent>
          {allMissing ? <EmptyState /> : <PipelineChecklist steps={pipeline} />}
        </CardContent>
      </Card>

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
                    <Link to="/capability-map" className="font-medium hover:underline">{c.name}</Link>
                    {c.summary && <p className="text-muted-foreground text-xs">{c.summary}</p>}
                  </div>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Key Entrypoints */}
      {data.entrypoints.length > 0 && (
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
                      <td className="py-1.5 font-mono text-xs">{ep.entrypoint_id}</td>
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
                        {sym.route_method && sym.route_path
                          ? `${sym.route_method} ${sym.route_path}`
                          : "—"}
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

      {/* Docs-Code Gaps */}
      {data.gap_summary.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Docs-Code Gaps</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3" data-testid="gap-summary">
              {data.gap_summary.map((g, i) => (
                <div key={i} className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
                  <AlertTriangle className="h-4 w-4 text-yellow-600" />
                  <span className="font-medium">{g.gap_type}</span>
                  <Badge variant="secondary">{g.count}</Badge>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-3">
              Total gaps: {data.gap_summary.reduce((s, g) => s + g.count, 0)}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Next Actions */}
      {data.next_actions.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Next Actions</CardTitle>
            <CardDescription>Steps to improve system understanding</CardDescription>
          </CardHeader>
          <CardContent>
            <NextActionsList actions={data.next_actions} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default function SystemUnderstandingPage() {
  const { data, isLoading, error } = useSystemUnderstanding();
  const build = useBuildSystemUnderstanding();

  return (
    <div className="space-y-6">
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
          onClick={() => build.mutate()}
          disabled={build.isPending}
          variant="default"
          data-testid="build-button"
        >
          {build.isPending ? (
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
        <DataView data={data} />
      ) : null}
    </div>
  );
}
