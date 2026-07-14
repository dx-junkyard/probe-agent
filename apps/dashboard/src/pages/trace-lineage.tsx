import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useLineage, useAnalyzers, type LineageQuery } from "@/api/hooks";
import type { LineageStep } from "@/api/types";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";
import { ReplayabilityBadge, ReplayRowActions } from "@/components/replay-row-actions";
import { formatTimestamp, cn } from "@/lib/utils";

type SearchKind = "entity" | "correlation" | "flow" | "analyzer";

// Extract an entity filter {type,id} from a saved analyzer spec, if present.
function analyzerEntity(spec: unknown): { type: string; id: string } | null {
  const filter = (spec as { filter?: { entity?: { type?: string; id?: string } } })?.filter;
  const ent = filter?.entity;
  if (ent && ent.type && ent.id) return { type: ent.type, id: ent.id };
  return null;
}

const ROLE_VARIANT: Record<string, "success" | "warning" | "secondary"> = {
  source: "success",
  derived: "warning",
  related: "secondary",
};

// Flatten all projected fields/metrics of a step into a stable, comparable map
// keyed by projection:phase:name. Deterministic — no interpretation.
function flatten(step: LineageStep): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of step.projections ?? []) {
    for (const [k, v] of Object.entries(p.fields ?? {})) {
      out[`${p.projection_name}:${p.phase}:${k}`] = digest(v);
    }
    for (const [k, v] of Object.entries(p.metrics ?? {})) {
      out[`${p.projection_name}:${p.phase}:#${k}`] = digest(v);
    }
  }
  return out;
}

// Deterministic shadow diff for a step: compare shadow_current vs
// shadow_candidate projected fields (equality only). Returns null when the
// step has no shadow projections.
function shadowDiff(step: LineageStep): { field: string; current: string; candidate: string; changed: boolean }[] | null {
  const cur = (step.projections ?? []).find((p) => p.phase === "shadow_current");
  const cand = (step.projections ?? []).find((p) => p.phase === "shadow_candidate");
  if (!cur && !cand) return null;
  const keys = new Set([
    ...Object.keys(cur?.fields ?? {}),
    ...Object.keys(cand?.fields ?? {}),
  ]);
  return Array.from(keys).map((field) => {
    const c = cur?.fields ?? {};
    const d = cand?.fields ?? {};
    const cPresent = field in c;
    const dPresent = field in d;
    const changed = cPresent !== dPresent || digest(c[field]) !== digest(d[field]);
    return {
      field,
      current: cPresent ? digest(c[field]) : "(absent)",
      candidate: dPresent ? digest(d[field]) : "(absent)",
      changed,
    };
  });
}

// Bounded display of a value; large values are shown as a length digest.
function digest(v: unknown): string {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  if (s == null) return "null";
  if (s.length > 120) return `${s.slice(0, 117)}… (${s.length} chars)`;
  return s;
}

// datetime-local value -> unix seconds (undefined when empty/invalid).
function toEpoch(v: string): number | undefined {
  if (!v) return undefined;
  const ms = Date.parse(v);
  return Number.isNaN(ms) ? undefined : ms / 1000;
}

// Deep link (Issue #151 navigation): /trace-lineage?kind=entity&type=order&id=o-1,
// ?kind=correlation&id=… or ?kind=flow&id=…
function queryFromParams(params: URLSearchParams): LineageQuery | null {
  const kind = params.get("kind");
  const id = params.get("id");
  if (kind === "entity") {
    const type = params.get("type");
    return type && id ? { kind: "entity", entityType: type, entityId: id } : null;
  }
  if ((kind === "correlation" || kind === "flow") && id) return { kind, id };
  return null;
}

export default function TraceLineagePage() {
  const [searchParams] = useSearchParams();
  const [initialQuery] = useState<LineageQuery | null>(() => queryFromParams(searchParams));
  const [kind, setKind] = useState<SearchKind>(initialQuery?.kind ?? "entity");
  const [entityType, setEntityType] = useState(
    initialQuery?.kind === "entity" ? initialQuery.entityType : "",
  );
  const [entityId, setEntityId] = useState(
    initialQuery?.kind === "entity" ? initialQuery.entityId : "",
  );
  const [singleId, setSingleId] = useState(
    initialQuery && initialQuery.kind !== "entity" ? initialQuery.id : "",
  );
  const [componentFilter, setComponentFilter] = useState("");
  const [windowStart, setWindowStart] = useState("");
  const [windowEnd, setWindowEnd] = useState("");
  const [shadowCompare, setShadowCompare] = useState(false);
  const [activeQuery, setActiveQuery] = useState<LineageQuery | null>(initialQuery);

  const { data, isLoading, isError } = useLineage(activeQuery);
  const { data: analyzers } = useAnalyzers();
  const analyzerSources = (Array.isArray(analyzers) ? analyzers : []).filter((a) =>
    analyzerEntity(a.spec),
  );

  const submit = () => {
    const start = toEpoch(windowStart);
    const end = toEpoch(windowEnd);
    if (kind === "entity") {
      if (!entityType.trim() || !entityId.trim()) return;
      setActiveQuery({ kind: "entity", entityType: entityType.trim(), entityId: entityId.trim(), start, end });
    } else if (kind === "correlation" || kind === "flow") {
      if (!singleId.trim()) return;
      setActiveQuery({ kind, id: singleId.trim(), start, end });
    }
  };

  const steps = useMemo(() => {
    const all = data?.steps ?? [];
    const f = componentFilter.trim();
    return f ? all.filter((s) => s.component_id.includes(f)) : all;
  }, [data, componentFilter]);

  // Previous flattened map per position, for deterministic change highlight.
  const flats = useMemo(() => steps.map(flatten), [steps]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-bold">Trace Lineage Explorer</h1>
        <p className="text-sm text-muted-foreground">
          Follow an entity / correlation / flow through the probe points it touched, with
          projected fields and deterministic field-change highlighting.
        </p>
      </div>

      <Card>
        <CardContent className="pt-6 space-y-4">
          <div className="flex gap-2">
            {(["entity", "correlation", "flow", "analyzer"] as const).map((k) => (
              <Button
                key={k}
                size="sm"
                variant={kind === k ? "default" : "outline"}
                onClick={() => setKind(k)}
              >
                {k}
              </Button>
            ))}
          </div>

          {kind === "analyzer" ? (
            <div className="space-y-2">
              <Label className="text-xs">Saved analyzer (entity-filtered)</Label>
              {analyzerSources.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No saved analyzer with an entity filter. Create one in Trace Analyzers.
                </p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {analyzerSources.map((a) => {
                    const ent = analyzerEntity(a.spec)!;
                    return (
                      <Button
                        key={a.id}
                        size="sm"
                        variant="outline"
                        onClick={() => setActiveQuery({ kind: "entity", entityType: ent.type, entityId: ent.id })}
                      >
                        {a.name || `analyzer #${a.id}`} · {ent.type}:{ent.id}
                      </Button>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
          <div className="flex flex-wrap items-end gap-3">
            {kind === "entity" ? (
              <>
                <div className="space-y-1">
                  <Label className="text-xs">Entity type</Label>
                  <Input
                    aria-label="entity type"
                    placeholder="order"
                    value={entityType}
                    onChange={(e) => setEntityType(e.target.value)}
                    className="w-40"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Entity ID</Label>
                  <Input
                    aria-label="entity id"
                    placeholder="o-123"
                    value={entityId}
                    onChange={(e) => setEntityId(e.target.value)}
                    className="w-48"
                  />
                </div>
              </>
            ) : (
              <div className="space-y-1">
                <Label className="text-xs">{kind === "correlation" ? "Correlation ID" : "Flow ID"}</Label>
                <Input
                  aria-label={`${kind} id`}
                  placeholder={kind === "correlation" ? "req-abc" : "checkout"}
                  value={singleId}
                  onChange={(e) => setSingleId(e.target.value)}
                  className="w-64"
                />
              </div>
            )}
            <div className="space-y-1">
              <Label className="text-xs">Component filter</Label>
              <Input
                aria-label="component filter"
                placeholder="(optional)"
                value={componentFilter}
                onChange={(e) => setComponentFilter(e.target.value)}
                className="w-48"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">From</Label>
              <Input
                aria-label="time window start"
                type="datetime-local"
                value={windowStart}
                onChange={(e) => setWindowStart(e.target.value)}
                className="w-52"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">To</Label>
              <Input
                aria-label="time window end"
                type="datetime-local"
                value={windowEnd}
                onChange={(e) => setWindowEnd(e.target.value)}
                className="w-52"
              />
            </div>
            <Button onClick={submit}>Search</Button>
          </div>
          )}
          <div className="flex items-center gap-2 pt-1">
            <Button
              size="sm"
              variant={shadowCompare ? "default" : "outline"}
              onClick={() => setShadowCompare((v) => !v)}
            >
              Shadow compare
            </Button>
            <span className="text-xs text-muted-foreground">
              Highlight current vs candidate projected fields on each node.
            </span>
          </div>
        </CardContent>
      </Card>

      {!activeQuery ? null : isLoading ? (
        <div className="space-y-2">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-20 w-full" />)}</div>
      ) : isError ? (
        <p className="text-sm text-destructive">Failed to load lineage.</p>
      ) : steps.length === 0 ? (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground space-y-2">
            <p className="font-medium text-foreground">No lineage found.</p>
            <p>
              Lineage appears once your code attaches correlation metadata. Wrap a request in{" "}
              <code className="rounded bg-muted px-1">probe_context(correlation_id=…, flow_id=…)</code>{" "}
              and add entities via <code className="rounded bg-muted px-1">add_entity(type, id)</code> or{" "}
              <code className="rounded bg-muted px-1">@probe(entities=[…])</code>. Projected fields require a{" "}
              <code className="rounded bg-muted px-1">@probe(projection=…)</code> spec.
            </p>
            <Link to="/connect-sdk" className="text-primary underline">How to connect the SDK →</Link>
          </CardContent>
        </Card>
      ) : (
        <ol className="relative space-y-4 border-l pl-6">
          {steps.map((step, i) => {
            const prev = i > 0 ? flats[i - 1] : {};
            const cur = flats[i];
            return (
              <li key={`${step.trace_id}-${i}`} className="relative">
                <span className="absolute -left-[27px] top-3 h-3 w-3 rounded-full bg-primary" />
                <Card>
                  <CardContent className="pt-4 space-y-3">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-semibold">{step.component_id || "(pruned)"}</span>
                        {step.mode && <Badge variant="outline">{step.mode}</Badge>}
                        {step.error && <Badge variant="destructive">error</Badge>}
                        {step.component_id && (
                          <ReplayabilityBadge
                            replayability={step.replayability}
                            reasons={step.replay_reasons}
                          />
                        )}
                      </div>
                      <span className="text-xs text-muted-foreground">{formatTimestamp(step.timestamp)}</span>
                    </div>

                    {step.entities.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {step.entities.map((e, j) => (
                          <Badge key={j} variant={ROLE_VARIANT[e.role] ?? "secondary"} className="text-xs">
                            {e.type}:{e.id} · {e.role}
                          </Badge>
                        ))}
                      </div>
                    )}

                    {shadowCompare && (() => {
                      const diff = shadowDiff(step);
                      if (!diff) return null;
                      return (
                        <div className="rounded-lg border divide-y text-xs" data-testid="shadow-diff">
                          <div className="px-3 py-1.5 font-medium bg-muted/50">current vs candidate</div>
                          {diff.map((d) => (
                            <div
                              key={d.field}
                              className={cn(
                                "grid grid-cols-3 gap-2 px-3 py-1.5",
                                d.changed && "bg-amber-100/60 dark:bg-amber-900/30",
                              )}
                            >
                              <span className="font-mono text-muted-foreground">{d.field}</span>
                              <span className="font-mono break-all">{d.current}</span>
                              <span className="font-mono break-all">
                                {d.candidate}
                                {d.changed && <span className="ml-1 text-amber-600" aria-label="shadow-changed">●</span>}
                              </span>
                            </div>
                          ))}
                        </div>
                      );
                    })()}

                    {Object.keys(cur).length > 0 && (
                      <div className="rounded-lg border divide-y text-xs">
                        {Object.entries(cur).map(([k, v]) => {
                          const changed = i > 0 && (!(k in prev) || prev[k] !== v);
                          return (
                            <div
                              key={k}
                              className={cn(
                                "flex items-start justify-between gap-3 px-3 py-1.5",
                                changed && "bg-amber-100/60 dark:bg-amber-900/30",
                              )}
                            >
                              <span className="font-mono text-muted-foreground">{k}</span>
                              <span className="font-mono text-right break-all">
                                {v}
                                {changed && <span className="ml-2 text-amber-600" aria-label="changed">●</span>}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    <div className="flex items-center gap-3 text-xs">
                      <span className="font-mono text-muted-foreground">{step.trace_id.slice(0, 12)}</span>
                      <Link
                        to={`/components?component=${encodeURIComponent(step.component_id)}`}
                        className="text-primary underline"
                      >
                        Component
                      </Link>
                      <Link to="/flow-explorer" className="text-primary underline">Flow Explorer</Link>
                    </div>
                    {step.component_id && (
                      <div className="flex items-center gap-1 flex-wrap border-t pt-2">
                        <ReplayRowActions
                          componentId={step.component_id}
                          traceId={step.trace_id}
                        />
                        <AddToWorkspaceButton
                          itemType="trace"
                          itemId={step.trace_id}
                          label={`Trace ${step.trace_id.slice(0, 12)} (${step.component_id})`}
                        />
                      </div>
                    )}
                  </CardContent>
                </Card>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
