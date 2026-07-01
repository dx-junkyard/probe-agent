import { useState, useEffect, useRef } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  useFlowEntrypoints, useBuildFlowGraph, useCreatePlanFromFlow, useApiRoleCards,
} from "@/api/hooks";
import { ApiRoleCard } from "@/components/api-role-card";
import { ApiError } from "@/api/client";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Workflow, Crosshair, AlertTriangle, ArrowRight, Activity, RefreshCw } from "lucide-react";
import type {
  FlowEntrypointOut, FlowGraphOut, FlowNodeOut, FlowEdgeOut,
  FlowProbeSelection, ProbePreviewOut,
} from "@/api/types";

const RISK_VARIANT: Record<string, "secondary" | "destructive"> = {
  low: "secondary",
  medium: "secondary",
  high: "destructive",
};

const RESOLUTION_STYLE: Record<string, string> = {
  resolved: "border-emerald-300 text-emerald-700 dark:text-emerald-300",
  inferred: "border-amber-300 text-amber-700 dark:text-amber-300",
  unresolved: "border-red-300 text-red-700 dark:text-red-300",
};

const BOUNDARY_LABEL: Record<string, string> = {
  http: "HTTP", database: "DB", filesystem: "FS", dispatch: "async",
};

// Issue #51: Flow Explorer is backend-entrypoint-first. The public-function
// fallback is intentionally excluded from these quick filters; it only
// appears via the explicit "Advanced" toggle below.
const ENTRYPOINT_CATEGORIES: { key: string; label: string }[] = [
  { key: "all", label: "All" },
  { key: "api", label: "API" },
  { key: "message_queue", label: "Message Queue" },
  { key: "scheduled_job", label: "Scheduled Job" },
  { key: "cli", label: "CLI" },
];

const CATEGORY_BADGE: Record<string, string> = {
  api: "API", message_queue: "MQ", scheduled_job: "Job", cli: "CLI", function: "fn",
};

const nodeKey = (id: string) => `node:${id}`;
const edgeKey = (id: string) => `edge:${id}`;


function PreviewBlock({ preview }: { preview: ProbePreviewOut }) {
  return (
    <div className="space-y-1 text-[11px]">
      <div>
        <span className="text-muted-foreground">Recommended mode: </span>
        <Badge variant="outline" className="text-[10px]">{preview.recommended_mode}</Badge>
        <span className="ml-2 text-muted-foreground">Risk: </span>
        <Badge variant={RISK_VARIANT[preview.side_effect_risk]} className="text-[10px]">
          {preview.side_effect_risk}
        </Badge>
      </div>
      <div>
        <span className="text-muted-foreground">Captured data:</span>
        <ul className="list-disc pl-4">
          {preview.captured_data.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </div>
      <div>
        <span className="text-muted-foreground">Redaction:</span>
        <ul className="list-disc pl-4">
          {preview.redaction.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </div>
      <div><span className="text-muted-foreground">Replayability: </span>{preview.replayability}</div>
      <div><span className="text-muted-foreground">Estimated event volume: </span>{preview.estimated_event_volume}</div>
      {preview.denylist_hit && (
        <div className="text-red-600">⚠ Denylist: {preview.denylist_hit}</div>
      )}
    </div>
  );
}

export default function FlowExplorerPage() {
  const navigate = useNavigate();
  const [category, setCategory] = useState("all");
  const [filter, setFilter] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const { data: entrypointsData, isLoading } = useFlowEntrypoints({
    category, q: filter, includeFunctions: showAdvanced,
  });
  const { data: roleCardsData } = useApiRoleCards();
  const buildGraph = useBuildFlowGraph();
  const createPlan = useCreatePlanFromFlow();

  const [graph, setGraph] = useState<FlowGraphOut | null>(null);
  const [activeEntrypoint, setActiveEntrypoint] = useState<FlowEntrypointOut | null>(null);
  const [activeFlowId, setActiveFlowId] = useState<string | null>(null);
  const [selections, setSelections] = useState<Record<string, FlowProbeSelection>>({});
  const [objective, setObjective] = useState("");
  const [stale, setStale] = useState(false);

  // The server applies category/substring filters and returns matches in full
  // (no silent truncation); ``total`` is the unfiltered backend entrypoint
  // count. ``functions`` is the Advanced-only fallback list (Issue #51).
  const entrypoints = entrypointsData?.entrypoints ?? [];
  const total = entrypointsData?.total ?? 0;
  const functions = entrypointsData?.functions ?? [];
  const diagnostics = entrypointsData?.diagnostics ?? [];
  const hasBackend = entrypointsData?.has_backend_entrypoints ?? true;

  // Issue #62: the Capability Map links here with the logical entrypoint in the
  // URL so the matching entrypoint auto-opens and its flow graph builds.
  const [searchParams] = useSearchParams();
  const wantType = searchParams.get("entrypoint_type");
  const wantId = searchParams.get("entrypoint_id");
  const autoOpenedRef = useRef<string | null>(null);

  const openEntrypoint = async (ep: FlowEntrypointOut) => {
    setActiveEntrypoint(ep);
    setGraph(null);
    setActiveFlowId(null);
    setSelections({});
    setStale(false);
    try {
      const g = await buildGraph.mutateAsync({
        entrypoint_type: ep.entrypoint_type,
        entrypoint_id: ep.entrypoint_id,
      });
      setGraph(g);
      if (g.candidate_paths.length > 0) setActiveFlowId(g.candidate_paths[0].flow_id);
    } catch (e) {
      toast.error(String(e));
    }
  };

  useEffect(() => {
    if (!wantType || !wantId) return;
    const key = `${wantType}:${wantId}`;
    if (autoOpenedRef.current === key) return;
    if (
      activeEntrypoint &&
      activeEntrypoint.entrypoint_type === wantType &&
      activeEntrypoint.entrypoint_id === wantId
    ) {
      autoOpenedRef.current = key;
      return;
    }
    const match = [...entrypoints, ...functions].find(
      ep => ep.entrypoint_type === wantType && ep.entrypoint_id === wantId,
    );
    if (match) {
      autoOpenedRef.current = key;
      // Defer out of the effect body: openEntrypoint updates state and kicks off
      // the graph build, and auto-open is gated by autoOpenedRef so it only
      // fires once per URL target.
      queueMicrotask(() => openEntrypoint(match));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantType, wantId, entrypoints, functions, activeEntrypoint]);

  const toggleNode = (node: FlowNodeOut) => {
    if (node.is_external) {
      toast.error("External boundary nodes can't be instrumented. Select the call boundary edge instead.");
      return;
    }
    const key = nodeKey(node.node_id);
    setSelections(prev => {
      const next = { ...prev };
      if (next[key]) {
        delete next[key];
      } else {
        next[key] = {
          target_type: "node",
          node_id: node.node_id,
          observation: "output",
          mode_preference: (node.preview?.recommended_mode as FlowProbeSelection["mode_preference"]) ?? "trace",
        };
      }
      return next;
    });
  };

  const toggleEdge = (edge: FlowEdgeOut) => {
    if (edge.resolution === "unresolved") {
      toast.error("Unresolved dynamic calls can't be observed as a boundary.");
      return;
    }
    const key = edgeKey(edge.edge_id);
    setSelections(prev => {
      const next = { ...prev };
      if (next[key]) {
        delete next[key];
      } else {
        next[key] = {
          target_type: "edge",
          edge_id: edge.edge_id,
          observation: "boundary",
          mode_preference: (edge.preview?.recommended_mode as FlowProbeSelection["mode_preference"]) ?? "trace",
        };
      }
      return next;
    });
  };

  const updateSelection = (key: string, patch: Partial<FlowProbeSelection>) => {
    setSelections(prev => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  };

  const submitPlan = async () => {
    if (!activeEntrypoint || !graph) return;
    const list = Object.values(selections);
    if (!list.length) return;
    try {
      const plan = await createPlan.mutateAsync({
        entrypoint_type: activeEntrypoint.entrypoint_type,
        entrypoint_id: activeEntrypoint.entrypoint_id,
        objective: objective.trim() || undefined,
        selections: list,
        snapshot_id: graph.snapshot_id,
        commit_sha: graph.commit_sha,
      });
      toast.success(`Probe Plan draft #${plan.id} created from flow selection`);
      setSelections({});
      setObjective("");
      navigate(`/probe-planner?plan=${plan.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setStale(true);
        toast.error("This flow graph is out of date. Reload it and re-select.");
      } else {
        toast.error(String(e));
      }
    }
  };

  const roleCardByKey = new Map(
    (roleCardsData?.cards ?? []).map(c => [`${c.entrypoint_type}:${c.entrypoint_id}`, c]),
  );
  const activeRoleCard = activeEntrypoint
    ? roleCardByKey.get(`${activeEntrypoint.entrypoint_type}:${activeEntrypoint.entrypoint_id}`)
    : undefined;

  const activeFlow = graph?.candidate_paths.find(f => f.flow_id === activeFlowId) ?? null;
  const activeNodeIds = new Set(activeFlow?.node_ids ?? graph?.nodes.map(n => n.node_id) ?? []);
  const nodesById = new Map((graph?.nodes ?? []).map(n => [n.node_id, n]));
  const edgesById = new Map((graph?.edges ?? []).map(e => [e.edge_id, e]));
  const outgoing = (nodeId: string) =>
    (graph?.edges ?? []).filter(e => e.source_node_id === nodeId);
  const selectionCount = Object.keys(selections).length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Workflow className="h-6 w-6" /> Flow Explorer
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Explore deterministic execution flows from an entrypoint and turn
            selected nodes or call boundaries into a Probe Plan draft. Selection
            never generates, applies, or runs a patch.
          </p>
        </div>
      </div>

      {stale && activeEntrypoint && (
        <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-4 py-3 text-sm text-amber-900 dark:text-amber-100 flex items-center justify-between">
          <span>This flow graph was built from an older snapshot and is now stale.</span>
          <Button size="sm" variant="outline" onClick={() => openEntrypoint(activeEntrypoint)}>
            <RefreshCw className="h-4 w-4 mr-1" /> Reload graph
          </Button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr_340px] gap-4">
        {/* Left: entrypoints + candidate flows */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Entrypoints</CardTitle>
              <CardDescription className="text-xs">
                {isLoading
                  ? "Loading…"
                  : `${entrypoints.length} of ${total} entrypoint(s)`}
                {!isLoading && (entrypointsData?.frameworks?.length ?? 0) > 0 && (
                  <span> · {entrypointsData!.frameworks.join(", ")}</span>
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="flex flex-wrap gap-1">
                {ENTRYPOINT_CATEGORIES.map(c => (
                  <button
                    key={c.key}
                    onClick={() => setCategory(c.key)}
                    className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                      category === c.key
                        ? "border-primary bg-secondary"
                        : "hover:bg-secondary/50"
                    }`}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
              <Input
                value={filter}
                onChange={e => setFilter(e.target.value)}
                placeholder="Filter by label, path or operation…"
                className="h-8 text-xs"
              />
              {!isLoading && !hasBackend && diagnostics.length > 0 && (
                <div className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-2 py-1.5 text-[11px] text-amber-800 dark:text-amber-200 space-y-0.5">
                  {diagnostics.map((d, i) => <p key={i}>{d}</p>)}
                </div>
              )}
              {isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : !entrypoints.length ? (
                <p className="text-xs text-muted-foreground">
                  {total === 0 ? (
                    <>
                      No backend entrypoints detected. Create a snapshot and index
                      symbols on the{" "}
                      <Link to="/repository" className="underline">Repository</Link> page,
                      or check the Advanced fallback below.
                    </>
                  ) : (
                    "No entrypoints match this filter."
                  )}
                </p>
              ) : (
                <div className="space-y-1 max-h-[28rem] overflow-y-auto">
                  {entrypoints.map(ep => {
                    const primary = ep.category === "function"
                      ? ep.qualified_name
                      : ep.label;
                    return (
                      <button
                        key={`${ep.entrypoint_type}:${ep.entrypoint_id}`}
                        onClick={() => openEntrypoint(ep)}
                        className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors ${
                          activeEntrypoint?.entrypoint_id === ep.entrypoint_id
                            ? "bg-secondary"
                            : "hover:bg-secondary/50"
                        }`}
                      >
                        <div className="flex items-center gap-1">
                          <Badge variant="outline" className="text-[10px] px-1 py-0 shrink-0">
                            {ep.category === "api"
                              ? ep.route_method ?? "API"
                              : CATEGORY_BADGE[ep.category] ?? ep.category}
                          </Badge>
                          <span className="font-medium truncate">{primary}</span>
                          {ep.source === "reasoning_llm" && (
                            <Badge variant="outline" className="text-[10px] px-1 py-0 shrink-0 border-violet-400 text-violet-700 dark:text-violet-300">
                              LLM
                            </Badge>
                          )}
                        </div>
                        <div className="text-muted-foreground truncate flex gap-1.5">
                          <span className="truncate">{ep.path}</span>
                          {ep.framework && <span className="shrink-0">· {ep.framework}</span>}
                          {ep.confidence < 1 && (
                            <span className="shrink-0">· {Math.round(ep.confidence * 100)}%</span>
                          )}
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
              <button
                onClick={() => setShowAdvanced(v => !v)}
                className="text-[11px] text-muted-foreground underline"
              >
                {showAdvanced ? "Hide" : "Show"} Advanced (raw functions,
                {" "}{entrypointsData?.indexed_function_count ?? 0} indexed)
              </button>
              {showAdvanced && (
                <div className="space-y-1">
                  <p className="text-[11px] text-muted-foreground">
                    Raw functions are not resolved backend entrypoints. Using
                    one here usually means discovery is incomplete for this
                    repository.
                  </p>
                  <div className="space-y-1 max-h-[16rem] overflow-y-auto">
                    {functions.map(ep => (
                      <button
                        key={`${ep.entrypoint_type}:${ep.entrypoint_id}`}
                        onClick={() => openEntrypoint(ep)}
                        className={`w-full text-left rounded-md px-2 py-1.5 text-xs transition-colors ${
                          activeEntrypoint?.entrypoint_id === ep.entrypoint_id
                            ? "bg-secondary"
                            : "hover:bg-secondary/50"
                        }`}
                      >
                        <div className="flex items-center gap-1">
                          <Badge variant="outline" className="text-[10px] px-1 py-0 shrink-0">
                            {CATEGORY_BADGE.function}
                          </Badge>
                          <span className="font-medium truncate">{ep.qualified_name}</span>
                        </div>
                        <div className="text-muted-foreground truncate">{ep.path}</div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {graph && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Candidate Flows</CardTitle>
                <CardDescription className="text-xs">
                  {graph.candidate_paths.length} deterministic path(s)
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-1">
                {graph.candidate_paths.map(flow => (
                  <button
                    key={flow.flow_id}
                    onClick={() => setActiveFlowId(flow.flow_id)}
                    className={`w-full text-left rounded-md border px-2 py-1.5 text-xs ${
                      activeFlowId === flow.flow_id ? "border-primary bg-secondary/50" : ""
                    }`}
                  >
                    <div className="font-medium truncate">{flow.title}</div>
                    <div className="text-muted-foreground flex flex-wrap gap-x-2">
                      <span>{flow.node_count} nodes</span>
                      <span>depth {flow.max_depth}</span>
                      <span>conf {Math.round(flow.confidence * 100)}%</span>
                      {flow.external_boundary_count > 0 && (
                        <span className="text-sky-600">{flow.external_boundary_count} boundary</span>
                      )}
                      {flow.unresolved_edge_count > 0 && (
                        <span className="text-red-600">{flow.unresolved_edge_count} unresolved</span>
                      )}
                    </div>
                    {(flow.observed_node_count > 0 || flow.unobserved_node_ids.length > 0) && (
                      <div className="text-[11px] text-emerald-700 dark:text-emerald-400 mt-0.5">
                        observed {flow.observed_node_count}/
                        {flow.observed_node_count + flow.unobserved_node_ids.length} nodes
                      </div>
                    )}
                  </button>
                ))}
              </CardContent>
            </Card>
          )}
        </div>

        {/* Center: API role card + flow graph */}
        <div className="space-y-4 min-w-0">
        {activeEntrypoint && activeRoleCard && <ApiRoleCard card={activeRoleCard} />}
        <Card className="min-h-[300px]">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {activeEntrypoint ? activeEntrypoint.label : "Flow Graph"}
            </CardTitle>
            {graph && (
              <CardDescription className="text-xs font-mono">
                snapshot #{graph.snapshot_id} · commit {graph.commit_sha.slice(0, 8)}
              </CardDescription>
            )}
          </CardHeader>
          <CardContent>
            {buildGraph.isPending ? (
              <Skeleton className="h-48 w-full" />
            ) : !graph ? (
              <p className="text-sm text-muted-foreground py-12 text-center">
                Select an entrypoint to build its execution flow.
              </p>
            ) : (
              <div className="space-y-2">
                {graph.truncated && (
                  <div className="rounded-md border border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
                    Graph truncated by the node budget; some branches are omitted.
                  </div>
                )}
                <p className="text-[11px] text-muted-foreground">
                  Click a node to instrument it, or click an edge to observe its
                  call boundary (before/after).
                </p>
                {graph.nodes
                  .filter(n => activeNodeIds.has(n.node_id))
                  .map(node => {
                    const selected = !!selections[nodeKey(node.node_id)];
                    const edges = outgoing(node.node_id)
                      .filter(e => activeNodeIds.has(e.target_node_id ?? "") || e.resolution === "unresolved");
                    return (
                      <div key={node.node_id} className="space-y-1">
                        <button
                          onClick={() => toggleNode(node)}
                          className={`w-full text-left rounded-lg border p-3 transition-colors ${
                            node.is_external
                              ? "border-dashed bg-muted/40 cursor-default"
                              : selected
                                ? "border-primary bg-secondary/40"
                                : "hover:bg-secondary/30"
                          }`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-mono text-sm font-medium">{node.qualified_name}</span>
                            <div className="flex items-center gap-1 shrink-0">
                              {node.boundary_kind ? (
                                <Badge variant="outline" className="text-[10px] border-sky-400 text-sky-700 dark:text-sky-300">
                                  {BOUNDARY_LABEL[node.boundary_kind] ?? node.boundary_kind}
                                </Badge>
                              ) : (
                                <Badge variant="outline" className="text-[10px]">{node.node_type}</Badge>
                              )}
                              <Badge variant={RISK_VARIANT[node.risk]} className="text-[10px]">
                                {node.risk === "high" && <AlertTriangle className="h-3 w-3 mr-0.5" />}
                                {node.risk}
                              </Badge>
                            </div>
                          </div>
                          <div className="text-xs text-muted-foreground mt-0.5">
                            {node.is_external ? "external boundary (not instrumentable)" : `${node.path}:${node.line_start}–${node.line_end}`}
                            {node.component_id && <span> · {node.component_id}</span>}
                          </div>
                          {!node.is_external && (node.trace_count > 0 || node.evaluation_pass + node.evaluation_fail > 0) && (
                            <div className="flex items-center gap-2 mt-1 text-[11px]">
                              {node.observed && (
                                <span className="inline-flex items-center gap-0.5 text-emerald-600">
                                  <Activity className="h-3 w-3" /> {node.trace_count} trace(s)
                                </span>
                              )}
                              {node.error_count > 0 && (
                                <span className="text-red-600">{node.error_count} error(s)</span>
                              )}
                              {node.evaluation_pass + node.evaluation_fail > 0 && (
                                <span className="text-muted-foreground">
                                  eval {node.evaluation_pass}✓/{node.evaluation_fail}✗
                                </span>
                              )}
                            </div>
                          )}
                          {node.denylist_hit && (
                            <div className="text-xs text-red-600 mt-0.5">⚠ {node.denylist_hit}</div>
                          )}
                        </button>
                        {edges.map(e => {
                          const edgeSelected = !!selections[edgeKey(e.edge_id)];
                          const selectable = e.resolution !== "unresolved";
                          return (
                            <button
                              key={e.edge_id}
                              onClick={() => toggleEdge(e)}
                              disabled={!selectable}
                              className={`flex items-center gap-1 ml-6 px-1 py-0.5 rounded text-[11px] text-muted-foreground transition-colors ${
                                edgeSelected ? "bg-secondary/60 ring-1 ring-primary" : selectable ? "hover:bg-secondary/40" : "cursor-default"
                              }`}
                            >
                              <ArrowRight className="h-3 w-3" />
                              <span className={`rounded border px-1 ${RESOLUTION_STYLE[e.resolution]}`}>
                                {e.edge_type}/{e.resolution}
                              </span>
                              <span className="font-mono">
                                {e.target_node_id
                                  ? nodesById.get(e.target_node_id)?.qualified_name ?? e.callee_name
                                  : `${e.callee_name}() (unresolved)`}
                              </span>
                              {edgeSelected && <span className="text-primary">· boundary selected</span>}
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
              </div>
            )}
          </CardContent>
        </Card>
        </div>

        {/* Right: selection + plan */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Crosshair className="h-4 w-4" /> Probe Selection
              </CardTitle>
              <CardDescription className="text-xs">
                {selectionCount} target(s) selected
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {selectionCount === 0 ? (
                <p className="text-xs text-muted-foreground">
                  Click a node or an edge in the graph to preview and select it.
                </p>
              ) : (
                Object.entries(selections).map(([key, sel]) => {
                  const isEdge = sel.target_type === "edge";
                  const node = isEdge ? undefined : nodesById.get(sel.node_id ?? "");
                  const edge = isEdge ? edgesById.get(sel.edge_id ?? "") : undefined;
                  const caller = edge ? nodesById.get(edge.source_node_id) : undefined;
                  const preview = isEdge ? edge?.preview : node?.preview;
                  const title = isEdge
                    ? `${caller?.qualified_name ?? "?"} → ${edge?.callee_name}() boundary`
                    : node?.qualified_name ?? sel.node_id;
                  return (
                    <div key={key} className="rounded-md border p-2 space-y-2">
                      <div className="flex items-center justify-between gap-1">
                        <span className="font-mono text-xs font-medium truncate">{title}</span>
                        <Badge variant="outline" className="text-[10px] shrink-0">
                          {isEdge ? "edge" : "node"}
                        </Badge>
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        {!isEdge && (
                          <div>
                            <Label className="text-[10px]">Observe</Label>
                            <Select
                              value={sel.observation}
                              onChange={e => updateSelection(key, { observation: e.target.value as FlowProbeSelection["observation"] })}
                              className="h-7 text-xs"
                            >
                              <option value="input">Input</option>
                              <option value="output">Output / error</option>
                              <option value="boundary">Call boundary</option>
                            </Select>
                          </div>
                        )}
                        <div>
                          <Label className="text-[10px]">Mode</Label>
                          <Select
                            value={sel.mode_preference}
                            onChange={e => updateSelection(key, { mode_preference: e.target.value as FlowProbeSelection["mode_preference"] })}
                            className="h-7 text-xs"
                          >
                            <option value="trace">trace</option>
                            <option value="shadow">shadow</option>
                            <option value="off">off</option>
                          </Select>
                        </div>
                      </div>
                      {preview && <PreviewBlock preview={preview} />}
                    </div>
                  );
                })
              )}
              {selectionCount >= 2 && (
                <div className="rounded-md border border-sky-200 bg-sky-50 dark:bg-sky-950/20 dark:border-sky-800 px-2 py-1.5 text-[11px] text-sky-800 dark:text-sky-200">
                  Multiple targets selected — trace these together to compare a
                  latency breakdown or transformation across the flow.
                </div>
              )}
              <div className="space-y-1">
                <Label className="text-xs">Observation objective</Label>
                <Textarea
                  value={objective}
                  onChange={e => setObjective(e.target.value)}
                  placeholder="What should this probe plan help determine?"
                  rows={2}
                  className="text-xs"
                />
              </div>
              <Button
                className="w-full"
                size="sm"
                disabled={selectionCount === 0 || createPlan.isPending || stale}
                onClick={submitPlan}
              >
                {createPlan.isPending ? "Creating…" : "Create Probe Plan draft"}
              </Button>
              <p className="text-[10px] text-muted-foreground">
                Creates a draft only. Approve points on the{" "}
                <Link to="/probe-planner" className="underline">Probe Planner</Link>{" "}
                before any patch is generated.
              </p>
            </CardContent>
          </Card>

          {graph && graph.diagnostics.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Diagnostics</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="text-xs text-muted-foreground list-disc pl-4 space-y-1">
                  {graph.diagnostics.map((d, i) => <li key={i}>{d}</li>)}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
