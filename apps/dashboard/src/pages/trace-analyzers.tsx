import { useState } from "react";
import {
  useAnalyzers, useCreateAnalyzer, useReviewAnalyzer, useRunAnalyzer, useAnalyzerRuns,
  useProposeAnalyzer,
} from "@/api/hooks";
import type { TraceAnalyzer } from "@/api/types";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

interface CompareSummary {
  phases: string[];
  fields: string[];
  entity_count: number;
  diff_entity_count: number;
  diff_fields: Record<string, number>;
  candidate_error_count: number;
  components_with_diff: string[];
  examples: Record<string, string[]>;
  compared_trace_count: number;
}

const EXAMPLE_SPEC = `{
  "source": "trace_projections",
  "filter": { "entity": { "type": "order", "id": "o-1" } },
  "select": [
    { "name": "status", "path": "$.fields.status" },
    { "name": "trace", "path": "$.trace_id" }
  ],
  "group_by": ["status"]
}`;

const STATUS_VARIANT: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  approved: "success",
  proposed: "warning",
  rejected: "destructive",
  completed: "success",
  failed: "destructive",
  pending: "secondary",
};

export default function TraceAnalyzersPage() {
  const { data: analyzers, isLoading } = useAnalyzers();
  const create = useCreateAnalyzer();
  const review = useReviewAnalyzer();
  const runMut = useRunAnalyzer();
  const propose = useProposeAnalyzer();
  const [selected, setSelected] = useState<number | null>(null);
  const { data: runs } = useAnalyzerRuns(selected);

  const [name, setName] = useState("");
  const [specText, setSpecText] = useState(EXAMPLE_SPEC);
  const [intent, setIntent] = useState("");

  const doPropose = async () => {
    if (!intent.trim()) return;
    try {
      const created = await propose.mutateAsync({ intent: intent.trim() });
      toast.success(created.is_mock ? "Proposed (mock)" : "Proposal saved (proposed)");
      setSelected(created.id);
      setIntent("");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const current = analyzers?.find((a) => a.id === selected);

  const submit = async () => {
    let spec: unknown;
    try {
      spec = JSON.parse(specText);
    } catch {
      toast.error("Spec is not valid JSON");
      return;
    }
    try {
      const created = await create.mutateAsync({ name: name || "analyzer", spec });
      toast.success("Analyzer created (proposed)");
      setSelected(created.id);
      setName("");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const doReview = (a: TraceAnalyzer, status: "approved" | "rejected") =>
    review.mutateAsync({ id: a.id, review_status: status })
      .then(() => toast.success(`Analyzer ${status}`))
      .catch((e) => toast.error(String(e)));

  const doRun = (a: TraceAnalyzer) =>
    runMut.mutateAsync(a.id)
      .then((r) => toast[r.status === "failed" ? "error" : "success"](`Run ${r.status}`))
      .catch((e) => toast.error(String(e)));

  const latestRun = runs?.[0];

  return (
    <div className="flex gap-6 h-[calc(100vh-8rem)]">
      <div className="w-72 shrink-0 overflow-y-auto border rounded-xl p-2 space-y-1">
        <h2 className="px-2 py-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          Analyzers
        </h2>
        {isLoading ? (
          <div className="space-y-2">{[1, 2].map((i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
        ) : !analyzers?.length ? (
          <p className="text-xs text-muted-foreground px-2 py-4">No analyzers yet</p>
        ) : (
          analyzers.map((a) => (
            <button
              key={a.id}
              className={cn(
                "w-full text-left rounded-lg px-3 py-2 text-sm transition-colors cursor-pointer",
                selected === a.id ? "bg-secondary text-foreground" : "hover:bg-secondary/50 text-muted-foreground",
              )}
              onClick={() => setSelected(a.id)}
            >
              <div className="font-medium truncate">{a.name || `analyzer #${a.id}`}</div>
              <div className="flex items-center gap-1 mt-1 flex-wrap">
                <Badge variant={STATUS_VARIANT[a.review_status]} className="text-xs">{a.review_status}</Badge>
                <Badge variant="outline" className="text-xs">{a.decision_method}</Badge>
                {a.is_mock && <Badge variant="warning" className="text-xs">mock</Badge>}
              </div>
            </button>
          ))
        )}
      </div>

      <div className="flex-1 overflow-y-auto space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Propose from natural language</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-muted-foreground">
              A reasoning model turns your intent into a spec. Proposals are saved as
              <span className="font-medium"> proposed</span> and must be reviewed before they can run —
              the model never approves anything. Requires a configured reasoning model
              (fails closed otherwise).
            </p>
            <Textarea
              aria-label="analyzer intent"
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
              rows={2}
              placeholder="Show me where order o-1's status changed across components"
            />
            <Button onClick={doPropose} disabled={propose.isPending} variant="outline">
              {propose.isPending ? "Proposing…" : "Propose spec"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Create analyzer (manual)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">Name</Label>
              <Input aria-label="analyzer name" value={name} onChange={(e) => setName(e.target.value)} placeholder="order status flow" />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Spec (JSON)</Label>
              <Textarea
                aria-label="analyzer spec"
                value={specText}
                onChange={(e) => setSpecText(e.target.value)}
                rows={10}
                className="font-mono text-xs"
              />
            </div>
            <Button onClick={submit} disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create (proposed)"}
            </Button>
          </CardContent>
        </Card>

        {current && (
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <CardTitle className="text-base">{current.name || `analyzer #${current.id}`}</CardTitle>
              <div className="flex items-center gap-2">
                {current.review_status !== "approved" && (
                  <Button size="sm" variant="outline" onClick={() => doReview(current, "approved")}>Approve</Button>
                )}
                {current.review_status !== "rejected" && (
                  <Button size="sm" variant="outline" onClick={() => doReview(current, "rejected")}>Reject</Button>
                )}
                <Button
                  size="sm"
                  onClick={() => doRun(current)}
                  disabled={current.review_status !== "approved" || runMut.isPending}
                  title={current.review_status !== "approved" ? "Approve before running" : undefined}
                >
                  Run
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {current.decision_method === "reasoning_llm" && (
                <p className="text-xs text-amber-600">
                  Proposed by a reasoning model ({current.provider}/{current.model}
                  {current.is_mock ? ", mock" : ""}). Review before approving.
                </p>
              )}
              <pre className="rounded bg-muted p-3 text-xs overflow-x-auto">
                {JSON.stringify(current.spec, null, 2)}
              </pre>

              <div>
                <h3 className="text-sm font-medium mb-2">Latest run</h3>
                {!latestRun ? (
                  <p className="text-xs text-muted-foreground">No runs yet</p>
                ) : (
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <Badge variant={STATUS_VARIANT[latestRun.status]}>{latestRun.status}</Badge>
                      {latestRun.row_count != null && (
                        <span className="text-xs text-muted-foreground">{latestRun.row_count} rows</span>
                      )}
                    </div>
                    {latestRun.error_details && (
                      <p className="text-xs text-destructive">{latestRun.error_details}</p>
                    )}
                    {latestRun.status === "completed" && (
                      <AddToWorkspaceButton
                        itemType="analyzer_run"
                        itemId={String(latestRun.id)}
                        label={`analyzer run #${latestRun.id}`}
                      />
                    )}
                    {latestRun.result?.compare ? (
                      <CompareSummaryView compare={latestRun.result.compare as unknown as CompareSummary} />
                    ) : null}
                    {latestRun.result && (
                      <pre className="rounded bg-muted p-3 text-xs overflow-x-auto max-h-64 overflow-y-auto">
                        {JSON.stringify(latestRun.result, null, 2)}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function CompareSummaryView({ compare }: { compare: CompareSummary }) {
  return (
    <div className="rounded-lg border p-3 space-y-2" data-testid="compare-summary">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <Badge variant="outline">{compare.phases.join(" vs ")}</Badge>
        <span className="text-muted-foreground">
          {compare.diff_entity_count}/{compare.entity_count} entities differ
        </span>
        {compare.candidate_error_count > 0 && (
          <Badge variant="destructive">{compare.candidate_error_count} candidate errors</Badge>
        )}
      </div>
      <div className="text-xs">
        <div className="font-medium mb-1">Field diffs</div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(compare.diff_fields).map(([field, count]) => (
            <Badge key={field} variant={count > 0 ? "warning" : "secondary"}>
              {field}: {count}
            </Badge>
          ))}
        </div>
      </div>
      {Object.keys(compare.examples).length > 0 && (
        <div className="text-xs">
          <div className="font-medium mb-1">Example traces</div>
          {Object.entries(compare.examples).map(([cls, traces]) => (
            <div key={cls} className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-muted-foreground">{cls}</span>
              {traces.map((t) => (
                <span key={t} className="font-mono">{t.slice(0, 12)}</span>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
