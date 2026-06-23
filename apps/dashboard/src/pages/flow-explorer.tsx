import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
  useFlowEntrypoints, useBuildFlowGraph, useCreatePlanFromFlow,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Workflow, Crosshair, AlertTriangle, ArrowRight, Activity } from "lucide-react";
import type {
  FlowEntrypointOut, FlowGraphOut, FlowNodeOut, FlowProbeSelection,
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

export default function FlowExplorerPage() {
  const { data: entrypointsData, isLoading } = useFlowEntrypoints();
  const buildGraph = useBuildFlowGraph();
  const createPlan = useCreatePlanFromFlow();

  const [graph, setGraph] = useState<FlowGraphOut | null>(null);
  const [activeEntrypoint, setActiveEntrypoint] = useState<FlowEntrypointOut | null>(null);
  const [activeFlowId, setActiveFlowId] = useState<string | null>(null);
  const [selections, setSelections] = useState<Record<string, FlowProbeSelection>>({});
  const [objective, setObjective] = useState("");
  const [filter, setFilter] = useState("");

  const entrypoints = entrypointsData?.entrypoints ?? [];
  const filtered = entrypoints.filter(e =>
    !filter.trim()
    || e.label.toLowerCase().includes(filter.toLowerCase())
    || e.path.toLowerCase().includes(filter.toLowerCase()),
  );

  const openEntrypoint = async (ep: FlowEntrypointOut) => {
    setActiveEntrypoint(ep);
    setGraph(null);
    setActiveFlowId(null);
    setSelections({});
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

  const toggleNode = (node: FlowNodeOut) => {
    if (node.is_external) {
      toast.error("External boundary nodes can't be instrumented. Select the in-repo caller and observe its call boundary.");
      return;
    }
    setSelections(prev => {
      const next = { ...prev };
      if (next[node.node_id]) {
        delete next[node.node_id];
      } else {
        next[node.node_id] = {
          node_id: node.node_id,
          observation: "output",
          mode_preference: node.risk === "high" ? "off" : "trace",
        };
      }
      return next;
    });
  };

  const updateSelection = (nodeId: string, patch: Partial<FlowProbeSelection>) => {
    setSelections(prev => ({ ...prev, [nodeId]: { ...prev[nodeId], ...patch } }));
  };

  const submitPlan = async () => {
    if (!activeEntrypoint) return;
    const list = Object.values(selections);
    if (!list.length) return;
    try {
      const plan = await createPlan.mutateAsync({
        entrypoint_type: activeEntrypoint.entrypoint_type,
        entrypoint_id: activeEntrypoint.entrypoint_id,
        objective: objective.trim() || undefined,
        selections: list,
      });
      toast.success(`Probe Plan draft #${plan.id} created from flow selection`);
      setSelections({});
      setObjective("");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const activeFlow = graph?.candidate_paths.find(f => f.flow_id === activeFlowId) ?? null;
  const activeNodeIds = new Set(activeFlow?.node_ids ?? graph?.nodes.map(n => n.node_id) ?? []);
  const nodesById = new Map((graph?.nodes ?? []).map(n => [n.node_id, n]));
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
            selected nodes into a Probe Plan draft. Selection never generates,
            applies, or runs a patch.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr_320px] gap-4">
        {/* Left: entrypoints + candidate flows */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Entrypoints</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Input
                value={filter}
                onChange={e => setFilter(e.target.value)}
                placeholder="Filter by path or route…"
                className="h-8 text-xs"
              />
              {isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : !entrypoints.length ? (
                <p className="text-xs text-muted-foreground">
                  No entrypoints. Create a snapshot and index symbols on the{" "}
                  <Link to="/repository" className="underline">Repository</Link> page.
                </p>
              ) : (
                <div className="space-y-1 max-h-72 overflow-y-auto">
                  {filtered.map(ep => (
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
                        <Badge variant="outline" className="text-[10px] px-1 py-0">
                          {ep.entrypoint_type === "http_route" ? ep.route_method : "fn"}
                        </Badge>
                        <span className="font-medium truncate">
                          {ep.entrypoint_type === "http_route" ? ep.route_path : ep.qualified_name}
                        </span>
                      </div>
                      <div className="text-muted-foreground truncate">{ep.path}</div>
                    </button>
                  ))}
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

        {/* Center: flow graph */}
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
                {graph.nodes
                  .filter(n => activeNodeIds.has(n.node_id))
                  .map(node => {
                    const selected = !!selections[node.node_id];
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
                        {edges.map((e, i) => (
                          <div
                            key={i}
                            className="flex items-center gap-1 pl-6 text-[11px] text-muted-foreground"
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
                          </div>
                        ))}
                      </div>
                    );
                  })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Right: selection + plan */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Crosshair className="h-4 w-4" /> Probe Selection
              </CardTitle>
              <CardDescription className="text-xs">
                {selectionCount} node(s) selected
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {selectionCount === 0 ? (
                <p className="text-xs text-muted-foreground">
                  Click a node in the graph to mark it for observation.
                </p>
              ) : (
                Object.values(selections).map(sel => {
                  const node = nodesById.get(sel.node_id);
                  return (
                    <div key={sel.node_id} className="rounded-md border p-2 space-y-2">
                      <div className="font-mono text-xs font-medium truncate">
                        {node?.qualified_name ?? sel.node_id}
                      </div>
                      {node?.risk === "high" && (
                        <div className="text-[11px] text-red-600">
                          High side-effect risk · review before shadow.
                        </div>
                      )}
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <Label className="text-[10px]">Observe</Label>
                          <Select
                            value={sel.observation}
                            onChange={e => updateSelection(sel.node_id, { observation: e.target.value as FlowProbeSelection["observation"] })}
                            className="h-7 text-xs"
                          >
                            <option value="input">Input</option>
                            <option value="output">Output / error</option>
                            <option value="boundary">Call boundary</option>
                          </Select>
                        </div>
                        <div>
                          <Label className="text-[10px]">Mode</Label>
                          <Select
                            value={sel.mode_preference}
                            onChange={e => updateSelection(sel.node_id, { mode_preference: e.target.value as FlowProbeSelection["mode_preference"] })}
                            className="h-7 text-xs"
                          >
                            <option value="trace">trace</option>
                            <option value="shadow">shadow</option>
                            <option value="off">off</option>
                          </Select>
                        </div>
                      </div>
                      <div className="text-[10px] text-muted-foreground">
                        Captures: {node?.probe_capabilities.join(", ")}
                      </div>
                    </div>
                  );
                })
              )}
              {selectionCount >= 2 && (
                <div className="rounded-md border border-sky-200 bg-sky-50 dark:bg-sky-950/20 dark:border-sky-800 px-2 py-1.5 text-[11px] text-sky-800 dark:text-sky-200">
                  Multiple nodes selected — trace these together to compare a
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
                disabled={selectionCount === 0 || createPlan.isPending}
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
