import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  useAnalyzers, useCreateAnalyzer, useReviewAnalyzer, useRunAnalyzer, useAnalyzerRuns,
  useProposeAnalyzer, useAnalyzerContext,
} from "@/api/hooks";
import type { TraceAnalyzer } from "@/api/types";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";
import { JsonTree } from "@/components/json-tree";
import {
  ANALYZER_TEMPLATES, EMPTY_BUILDER_STATE, buildSpec, summarizeSpec,
  type AnalyzerTemplateId, type BuilderState,
} from "@/lib/analyzer-templates";
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
  const review = useReviewAnalyzer();
  const runMut = useRunAnalyzer();
  const propose = useProposeAnalyzer();
  const [selected, setSelected] = useState<number | null>(null);
  const { data: runs } = useAnalyzerRuns(selected);

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
        <AnalyzerBuilder onCreated={(id) => setSelected(id)} />

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
              {/* Review gate: human-readable summary alongside the raw JSON so a
                  reviewer can see what the analyzer targets without reading JSON. */}
              <ReviewSummary analyzer={current} />

              {current.decision_method === "reasoning_llm" && (
                <p className="text-xs text-amber-600">
                  Proposed by a reasoning model ({current.provider}/{current.model}
                  {current.is_mock ? ", mock" : ""}). Review before approving.
                </p>
              )}
              <details className="rounded bg-muted p-3 text-xs">
                <summary className="cursor-pointer text-muted-foreground">Raw spec JSON</summary>
                <pre className="overflow-x-auto mt-2">{JSON.stringify(current.spec, null, 2)}</pre>
              </details>

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
                    {latestRun.data_expired && (
                      <p className="text-xs text-amber-600" data-testid="data-expired-warning">
                        ⚠ {latestRun.data_expired_note ?? "Referenced data may have been deleted by retention."}
                      </p>
                    )}
                    {latestRun.status === "completed" && (
                      <AddToWorkspaceButton
                        itemType="analyzer_run"
                        itemId={String(latestRun.id)}
                        label={`analyzer run #${latestRun.id}`}
                      />
                    )}
                    {latestRun.result && <ResultViewer result={latestRun.result} />}
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

function ReviewSummary({ analyzer }: { analyzer: TraceAnalyzer }) {
  const lines = summarizeSpec(analyzer.spec);
  return (
    <div className="rounded-lg border p-3 space-y-1" data-testid="review-summary">
      <div className="text-xs font-medium text-muted-foreground">What this analyzer does</div>
      <ul className="text-sm list-disc pl-5 space-y-0.5">
        {lines.map((l, i) => <li key={i}>{l}</li>)}
      </ul>
    </div>
  );
}

// --- Builder ---------------------------------------------------------------

function CheckboxList({ options, selected, onToggle, empty }: {
  options: string[];
  selected: string[];
  onToggle: (v: string) => void;
  empty?: string;
}) {
  if (!options.length) {
    return <p className="text-xs text-muted-foreground">{empty ?? "No values available yet."}</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto">
      {options.map((o) => {
        const on = selected.includes(o);
        return (
          <button
            key={o}
            type="button"
            onClick={() => onToggle(o)}
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-xs cursor-pointer transition-colors",
              on ? "bg-primary text-primary-foreground border-primary" : "hover:bg-secondary",
            )}
          >
            {o}
          </button>
        );
      })}
    </div>
  );
}

function AnalyzerBuilder({ onCreated }: { onCreated: (id: number) => void }) {
  const { data: ctx, isLoading } = useAnalyzerContext();
  const create = useCreateAnalyzer();
  const [template, setTemplate] = useState<AnalyzerTemplateId | "">("");
  const [state, setState] = useState<BuilderState>(EMPTY_BUILDER_STATE);
  const [name, setName] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [advancedText, setAdvancedText] = useState(EXAMPLE_SPEC);

  const set = (patch: Partial<BuilderState>) => setState((s) => ({ ...s, ...patch }));
  const toggle = (key: "components" | "phases" | "fields", v: string) =>
    setState((s) => ({
      ...s,
      [key]: s[key].includes(v) ? s[key].filter((x) => x !== v) : [...s[key], v],
    }));

  const built = useMemo(
    () => (template ? buildSpec(template, state) : { spec: null, errors: [] }),
    [template, state],
  );

  const entityIds = useMemo(
    () => (ctx?.entities ?? []).filter((e) => e.entity_type === state.entityType).map((e) => e.entity_id),
    [ctx, state.entityType],
  );

  const doCreate = async (spec: unknown) => {
    try {
      const created = await create.mutateAsync({ name: name || "analyzer", spec });
      toast.success("Analyzer created (proposed)");
      setName("");
      onCreated(created.id);
    } catch (e) {
      toast.error(String(e));
    }
  };

  const doCreateAdvanced = () => {
    let spec: unknown;
    try {
      spec = JSON.parse(advancedText);
    } catch {
      toast.error("Spec is not valid JSON");
      return;
    }
    doCreate(spec);
  };

  const needsEntity = template === "entity_lineage" || template === "field_transition";
  const needsFields = template === "entity_lineage" || template === "component_table" || template === "shadow_diff";
  const needsSingleField = template === "field_transition";
  const needsComponents = template === "component_table" || template === "shadow_diff" || template === "field_transition";
  const needsProjection = template === "component_table" || template === "shadow_diff";
  const needsPhases = template === "component_table";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Build an analyzer</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Pick a template and fill the form from your real trace data — no JSON required.
          The generated declarative spec is shown before you save and is validated
          server-side. Advanced JSON stays available as an escape hatch.
        </p>

        <div className="space-y-1">
          <Label className="text-xs">Name</Label>
          <Input aria-label="analyzer name" value={name} onChange={(e) => setName(e.target.value)} placeholder="order status flow" />
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {ANALYZER_TEMPLATES.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => { setTemplate(t.id); setState(EMPTY_BUILDER_STATE); }}
              className={cn(
                "text-left rounded-lg border p-2 text-xs cursor-pointer transition-colors",
                template === t.id ? "border-primary bg-primary/5" : "hover:bg-secondary/50",
              )}
              aria-pressed={template === t.id}
            >
              <div className="font-medium">{t.label}</div>
              <div className="text-muted-foreground mt-0.5">{t.description}</div>
            </button>
          ))}
        </div>

        {isLoading && <Skeleton className="h-8 w-full" />}

        {template && ctx && (
          <div className="space-y-3 rounded-lg border p-3">
            {needsEntity && (
              <div className="space-y-2">
                {ctx.entities_truncated && (
                  <p className="text-xs text-amber-600" data-testid="entities-truncated-warning">
                    This system has more entities than the builder can list — the entity
                    id dropdown only shows the first page of ids. Type an id directly
                    below if the one you need isn't in the list.
                  </p>
                )}
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label className="text-xs">Entity type</Label>
                    <Select
                      aria-label="entity type"
                      value={state.entityType}
                      onChange={(e) => set({ entityType: e.target.value, entityId: "" })}
                    >
                      <option value="">Select…</option>
                      {ctx.entity_types.map((t) => <option key={t} value={t}>{t}</option>)}
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">Entity id</Label>
                    {ctx.entities_truncated ? (
                      <Input
                        aria-label="entity id"
                        list="analyzer-builder-entity-ids"
                        value={state.entityId}
                        onChange={(e) => set({ entityId: e.target.value })}
                        disabled={!state.entityType}
                        placeholder="Type or pick an id…"
                      />
                    ) : (
                      <Select
                        aria-label="entity id"
                        value={state.entityId}
                        onChange={(e) => set({ entityId: e.target.value })}
                        disabled={!state.entityType}
                      >
                        <option value="">Select…</option>
                        {entityIds.map((id) => <option key={id} value={id}>{id}</option>)}
                      </Select>
                    )}
                    {ctx.entities_truncated && (
                      <datalist id="analyzer-builder-entity-ids">
                        {entityIds.map((id) => <option key={id} value={id} />)}
                      </datalist>
                    )}
                  </div>
                </div>
              </div>
            )}

            {needsComponents && (
              <div className="space-y-1">
                <Label className="text-xs">Components {template !== "component_table" && "(optional)"}</Label>
                <CheckboxList options={ctx.components} selected={state.components} onToggle={(v) => toggle("components", v)} />
              </div>
            )}

            {needsProjection && (
              <div className="space-y-1">
                <Label className="text-xs">Projection {template === "shadow_diff" && "(optional)"}</Label>
                <Select
                  aria-label="projection name"
                  value={state.projectionName}
                  onChange={(e) => set({ projectionName: e.target.value })}
                >
                  <option value="">Any projection</option>
                  {ctx.projection_names.map((p) => <option key={p} value={p}>{p}</option>)}
                </Select>
              </div>
            )}

            {needsPhases && (
              <div className="space-y-1">
                <Label className="text-xs">Phases (optional)</Label>
                <CheckboxList options={ctx.phases} selected={state.phases} onToggle={(v) => toggle("phases", v)} />
              </div>
            )}

            {needsSingleField && (
              <div className="space-y-1">
                <Label className="text-xs">Field to track</Label>
                <Select
                  aria-label="field"
                  value={state.field}
                  onChange={(e) => set({ field: e.target.value })}
                >
                  <option value="">Select…</option>
                  {ctx.field_names.map((f) => <option key={f} value={f}>{f}</option>)}
                </Select>
              </div>
            )}

            {needsFields && (
              <div className="space-y-1">
                <Label className="text-xs">
                  {template === "shadow_diff" ? "Fields to compare" : "Fields to read"}
                </Label>
                <CheckboxList options={ctx.field_names} selected={state.fields} onToggle={(v) => toggle("fields", v)} />
              </div>
            )}

            {built.errors.length > 0 ? (
              <ul className="text-xs text-amber-600 list-disc pl-4">
                {built.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            ) : built.spec ? (
              <div className="space-y-2">
                <Label className="text-xs">Spec preview</Label>
                <pre
                  className="rounded bg-muted p-3 text-xs overflow-x-auto"
                  data-testid="spec-preview"
                >{JSON.stringify(built.spec, null, 2)}</pre>
                <Button size="sm" onClick={() => doCreate(built.spec)} disabled={create.isPending}>
                  {create.isPending ? "Creating…" : "Create (proposed)"}
                </Button>
              </div>
            ) : null}
          </div>
        )}

        <div>
          <button
            type="button"
            className="text-xs text-muted-foreground underline cursor-pointer"
            onClick={() => setShowAdvanced((v) => !v)}
          >
            {showAdvanced ? "Hide advanced JSON editor" : "Advanced JSON editor"}
          </button>
          {showAdvanced && (
            <div className="space-y-2 mt-2">
              <Textarea
                aria-label="analyzer spec"
                value={advancedText}
                onChange={(e) => setAdvancedText(e.target.value)}
                rows={10}
                className="font-mono text-xs"
              />
              <Button size="sm" variant="outline" onClick={doCreateAdvanced} disabled={create.isPending}>
                Create from JSON
              </Button>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// --- Result viewer ---------------------------------------------------------

function ResultViewer({ result }: { result: Record<string, unknown> }) {
  const rows = Array.isArray(result.rows) ? (result.rows as Record<string, unknown>[]) : null;
  const groups = Array.isArray(result.groups) ? (result.groups as Record<string, unknown>[]) : null;
  const compare = result.compare as CompareSummary | undefined;

  const tabs: string[] = [];
  if (rows) tabs.push("rows");
  if (groups) tabs.push("groups");
  if (compare) tabs.push("compare");
  tabs.push("raw");

  return (
    <Tabs defaultValue={tabs[0]}>
      <TabsList>
        {rows && <TabsTrigger value="rows">Rows</TabsTrigger>}
        {groups && <TabsTrigger value="groups">Groups</TabsTrigger>}
        {compare && <TabsTrigger value="compare">Compare</TabsTrigger>}
        <TabsTrigger value="raw">Raw JSON</TabsTrigger>
      </TabsList>

      {rows && <TabsContent value="rows"><RowsTable rows={rows} /></TabsContent>}
      {groups && (
        <TabsContent value="groups">
          <div className="space-y-2" data-testid="groups-view">
            {groups.map((g, i) => (
              <div key={i} className="rounded border p-2">
                <div className="flex items-center gap-2 text-xs mb-1">
                  <Badge variant="secondary">{String((g.count as number) ?? 0)} rows</Badge>
                  <span className="font-mono">{JSON.stringify(g.key)}</span>
                </div>
                <JsonTree data={g.rows} name="rows" defaultExpanded={false} />
              </div>
            ))}
          </div>
        </TabsContent>
      )}
      {compare && (
        <TabsContent value="compare">
          <ShadowCompareTable compare={compare} />
        </TabsContent>
      )}
      <TabsContent value="raw">
        <div className="rounded bg-muted p-3 max-h-96 overflow-auto">
          <JsonTree data={result} defaultExpanded />
        </div>
      </TabsContent>
    </Tabs>
  );
}

function RowsTable({ rows }: { rows: Record<string, unknown>[] }) {
  const allFields = useMemo(() => {
    const s = new Set<string>();
    for (const r of rows) Object.keys(r).forEach((k) => s.add(k));
    return [...s];
  }, [rows]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");

  const visible = allFields.filter((f) => !hidden.has(f));
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) =>
      visible.some((f) => String(r[f] ?? "").toLowerCase().includes(q)),
    );
  }, [rows, visible, query]);

  const fmt = (v: unknown) =>
    v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);

  return (
    <div className="space-y-2" data-testid="rows-view">
      <div className="flex items-center gap-2 flex-wrap">
        <Input
          aria-label="filter rows"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter rows…"
          className="h-8 max-w-xs"
        />
        <span className="text-xs text-muted-foreground">
          {filtered.length}/{rows.length} rows
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {allFields.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setHidden((h) => {
              const n = new Set(h);
              if (n.has(f)) n.delete(f); else n.add(f);
              return n;
            })}
            className={cn(
              "rounded-full border px-2 py-0.5 text-xs cursor-pointer",
              hidden.has(f) ? "text-muted-foreground line-through" : "bg-secondary",
            )}
            title={hidden.has(f) ? "Show field" : "Hide field"}
          >
            {f}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto max-h-96 overflow-y-auto rounded border">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card">
            <tr className="border-b text-left">
              {visible.map((f) => <th key={f} className="p-2 font-medium">{f}</th>)}
            </tr>
          </thead>
          <tbody>
            {filtered.map((r, i) => (
              <tr key={i} className="border-b last:border-0">
                {visible.map((f) => (
                  <td key={f} className="p-2 font-mono align-top break-all">{fmt(r[f])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ShadowCompareTable({ compare }: { compare: CompareSummary }) {
  return (
    <div className="space-y-3" data-testid="compare-table">
      <div className="flex items-center gap-2 flex-wrap text-xs">
        <Badge variant="outline">{compare.phases.join(" vs ")}</Badge>
        <span className="text-muted-foreground">
          {compare.diff_entity_count}/{compare.entity_count} entities differ
          {" · "}{compare.compared_trace_count} traces compared
        </span>
        {compare.candidate_error_count > 0 && (
          <Badge variant="destructive">{compare.candidate_error_count} candidate errors</Badge>
        )}
      </div>
      <div className="overflow-x-auto rounded border">
        <table className="w-full text-xs">
          <thead className="bg-card">
            <tr className="border-b text-left">
              <th className="p-2 font-medium">Field</th>
              <th className="p-2 font-medium">Changed ({compare.phases[1]} vs {compare.phases[0]})</th>
              <th className="p-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(compare.diff_fields).map(([field, count]) => (
              <tr key={field} className="border-b last:border-0">
                <td className="p-2 font-mono">{field}</td>
                <td className="p-2">{count}</td>
                <td className="p-2">
                  <Badge variant={count > 0 ? "warning" : "secondary"}>
                    {count > 0 ? "changed" : "unchanged"}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {compare.components_with_diff.length > 0 && (
        <div className="text-xs">
          <span className="font-medium">Components with diffs: </span>
          {compare.components_with_diff.join(", ")}
        </div>
      )}
      {Object.keys(compare.examples).length > 0 && (
        <div className="text-xs space-y-1">
          <div className="font-medium">Example traces</div>
          {Object.entries(compare.examples).map(([cls, traces]) => {
            const component = cls.split("::")[1];
            return (
              <div key={cls} className="flex items-center gap-2 flex-wrap">
                {component ? (
                  <Link
                    to={`/components?component=${encodeURIComponent(component)}`}
                    className="font-mono text-primary underline"
                  >
                    {cls}
                  </Link>
                ) : (
                  <span className="font-mono text-muted-foreground">{cls}</span>
                )}
                {traces.map((t) => (
                  component ? (
                    <Link
                      key={t}
                      to={`/components?component=${encodeURIComponent(component)}`}
                      className="font-mono text-primary underline"
                      title={`${t} — open ${component} traces`}
                    >
                      {t.slice(0, 12)}
                    </Link>
                  ) : (
                    <span key={t} className="font-mono text-muted-foreground" title={t}>
                      {t.slice(0, 12)}
                    </span>
                  )
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
