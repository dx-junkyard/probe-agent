import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  useReplaySets,
  useReplaySet,
  useReplaySetSource,
  useReplaySourceDiff,
  useCreateReplayVariantRun,
  useReplayVariantRun,
  useReplayVariantRuns,
  useCreateReplayVariantDraft,
  useReplayApproval,
  useApproveReplay,
  useRevokeReplayApproval,
  useTraces,
} from "@/api/hooks";
import type {
  ReplayVariantRunOut,
  ReplayVariantCaseResultOut,
  ReplayVariantAggregateOut,
  ReplayVariantDraftOut,
  ReplaySetTraceOut,
  TraceEvent,
} from "@/api/types";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { JsonTree } from "@/components/json-tree";
import { ReplayabilityBadge } from "@/components/replay-row-actions";

// Simulation Workbench (Issue #242 Phase D / #246). Display + composition
// only: every judgement/execution/comparison decision below is made by the
// Phase A-C APIs (replayability classification, replay execution, variant
// diff classification). This page only composes those results and the two
// new deterministic source/diff helpers into one edit -> run -> inspect ->
// promote loop.

const SKIP_GUIDANCE: Record<string, string> = {
  unreplayable_capture:
    "This trace's captured input could not be structurally restored (redacted, oversized, or an unsupported type). Next step: pick a different trace, or adjust replay_capture redaction/size limits for this component.",
  repr_parse_failed:
    "No structured capture was recorded and the legacy repr input could not be parsed back into a call. Next step: opt this component into replay_capture=True so future traces are replayable.",
  undecodable_input:
    "The stored structured capture used an encoding the harness could not decode. Next step: re-capture this trace.",
  trace_missing:
    "This trace no longer exists in this System. Next step: remove it from the Replay Set.",
};

export default function SimulationWorkbenchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const replaySetIdParam = searchParams.get("replay_set_id");
  const selectedSetId =
    replaySetIdParam && Number.isInteger(Number(replaySetIdParam))
      ? Number(replaySetIdParam)
      : null;
  const selectSet = (id: number) => setSearchParams({ replay_set_id: String(id) });

  const { data: sets } = useReplaySets();
  const { data: replaySet, isLoading: setLoading } = useReplaySet(selectedSetId);
  const componentId = replaySet?.component_id ?? null;

  const { data: approvalState } = useReplayApproval(componentId);

  const { data: componentTraces } = useTraces(componentId, 500);
  const recordedByTraceId = useMemo(() => {
    const map = new Map<string, TraceEvent>();
    (componentTraces ?? []).forEach((t) => map.set(t.trace_id, t));
    return map;
  }, [componentTraces]);

  const { data: source } = useReplaySetSource(selectedSetId);
  const [editedSource, setEditedSource] = useState<string>("");
  // Reset the edit buffer whenever the fetched pinned-snapshot source text
  // actually changes (new Replay Set / new snapshot) -- adjusted during
  // render (React's documented pattern for "resetting state when a prop
  // changes") rather than in an effect, so user edits survive re-renders
  // that don't change the underlying source.
  const [lastLoadedSource, setLastLoadedSource] = useState<string | undefined>(undefined);
  if (source?.source != null && source.source !== lastLoadedSource) {
    setLastLoadedSource(source.source);
    setEditedSource(source.source);
  }

  const [pastedPatch, setPastedPatch] = useState("");
  const [draftTraceId, setDraftTraceId] = useState<string | null>(null);
  const [draftObjective, setDraftObjective] = useState("");
  const [lastDraft, setLastDraft] = useState<ReplayVariantDraftOut | null>(null);

  const sourceDiff = useReplaySourceDiff();
  const createVariantRun = useCreateReplayVariantRun();
  const createDraft = useCreateReplayVariantDraft();

  const { data: variantRuns } = useReplayVariantRuns(selectedSetId);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  // Same render-time-adjustment pattern: reset to "no run selected" when the
  // Replay Set changes, then default to the most recent run once it loads.
  const [activeRunTrackedSetId, setActiveRunTrackedSetId] = useState<number | null>(null);
  let effectiveActiveRunId = activeRunId;
  if (selectedSetId !== activeRunTrackedSetId) {
    setActiveRunTrackedSetId(selectedSetId);
    setActiveRunId(null);
    effectiveActiveRunId = null;
  } else if (effectiveActiveRunId === null && variantRuns && variantRuns.length > 0) {
    setActiveRunId(variantRuns[0].id);
    effectiveActiveRunId = variantRuns[0].id;
  }
  const { data: activeRun } = useReplayVariantRun(effectiveActiveRunId);

  const canRun = !!approvalState?.active;

  const handleRunDirectEdit = async () => {
    if (!selectedSetId) return;
    try {
      const diff = await sourceDiff.mutateAsync({
        replay_set_id: selectedSetId,
        edited_source: editedSource,
      });
      const run = await createVariantRun.mutateAsync({
        replay_set_id: selectedSetId,
        variants: [{ label: "Direct edit", patch_text: diff.patch_text, source: "manual" }],
      });
      setActiveRunId(run.id);
      toast.success("Replay variant run completed");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleRunPastedPatch = async () => {
    if (!selectedSetId || !pastedPatch.trim()) return;
    try {
      const run = await createVariantRun.mutateAsync({
        replay_set_id: selectedSetId,
        variants: [{ label: "Pasted patch", patch_text: pastedPatch, source: "pasted" }],
      });
      setActiveRunId(run.id);
      toast.success("Replay variant run completed");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleGenerateDraft = async () => {
    if (!selectedSetId || !draftTraceId || !draftObjective.trim()) return;
    try {
      const draft = await createDraft.mutateAsync({
        replay_set_id: selectedSetId,
        trace_id: draftTraceId,
        objective: draftObjective.trim(),
      });
      setLastDraft(draft);
      if (draft.status === "failed") toast.error(draft.error || "Draft generation failed");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleRunDraft = async () => {
    if (!selectedSetId || !lastDraft?.patch_text) return;
    try {
      const run = await createVariantRun.mutateAsync({
        replay_set_id: selectedSetId,
        variants: [
          {
            label: `LLM draft: ${lastDraft.objective.slice(0, 40)}`,
            patch_text: lastDraft.patch_text,
            source: "llm_draft",
          },
        ],
      });
      setActiveRunId(run.id);
      toast.success("Replay variant run completed");
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Simulation Workbench</h1>
        <div className="w-72">
          <Select
            value={selectedSetId ? String(selectedSetId) : ""}
            onChange={(e) => e.target.value && selectSet(Number(e.target.value))}
          >
            <option value="">Select a Replay Set...</option>
            {sets?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name || `Set #${s.id}`} — {s.component_id} ({s.trace_ids.length})
              </option>
            ))}
          </Select>
        </div>
      </div>

      {!selectedSetId ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            Select a Replay Set above, or use "Replay" on a trace in the Components tab.
          </CardContent>
        </Card>
      ) : setLoading || !replaySet ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <div className="space-y-6">
          {componentId && <ApprovalPanel componentId={componentId} />}

          <div className="grid gap-4 lg:grid-cols-2">
            <div data-testid="workbench-left-pane">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">
                    Replay Set: {replaySet.name || `Set #${replaySet.id}`}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 max-h-96 overflow-y-auto">
                  {replaySet.traces.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No traces in this set.</p>
                  ) : (
                    replaySet.traces.map((t) => (
                      <TraceRow
                        key={t.trace_id}
                        trace={t}
                        recorded={recordedByTraceId.get(t.trace_id)}
                        selected={draftTraceId === t.trace_id}
                        onSelect={() => setDraftTraceId(t.trace_id)}
                      />
                    ))
                  )}
                </CardContent>
              </Card>
            </div>

            <div data-testid="workbench-center-pane">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Candidate source</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {source && (
                    <p className="text-xs text-muted-foreground font-mono break-all">
                      {source.path} :: {source.qualified_name} (lines {source.start_line}-
                      {source.end_line}) @ {source.commit_sha.slice(0, 8)}
                    </p>
                  )}
                  {!canRun && (
                    <p className="text-xs text-amber-700 dark:text-amber-300">
                      Replay is not approved for this component -- Run is disabled until it is
                      approved above.
                    </p>
                  )}
                  <Tabs defaultValue="direct">
                    <TabsList>
                      <TabsTrigger value="direct">Direct edit</TabsTrigger>
                      <TabsTrigger value="paste">Paste patch</TabsTrigger>
                      <TabsTrigger value="draft">LLM draft</TabsTrigger>
                    </TabsList>
                    <TabsContent value="direct">
                      <div className="space-y-2">
                        <Textarea
                          value={editedSource}
                          onChange={(e) => setEditedSource(e.target.value)}
                          rows={14}
                          className="font-mono text-xs"
                          disabled={!source}
                        />
                        <Button
                          size="sm"
                          onClick={handleRunDirectEdit}
                          disabled={!canRun || !source || sourceDiff.isPending || createVariantRun.isPending}
                        >
                          {sourceDiff.isPending || createVariantRun.isPending ? "Running..." : "Run"}
                        </Button>
                      </div>
                    </TabsContent>
                    <TabsContent value="paste">
                      <div className="space-y-2">
                        <Textarea
                          value={pastedPatch}
                          onChange={(e) => setPastedPatch(e.target.value)}
                          rows={14}
                          className="font-mono text-xs"
                          placeholder="Paste a unified diff (git diff format)..."
                        />
                        <Button
                          size="sm"
                          onClick={handleRunPastedPatch}
                          disabled={!canRun || !pastedPatch.trim() || createVariantRun.isPending}
                        >
                          {createVariantRun.isPending ? "Running..." : "Run"}
                        </Button>
                      </div>
                    </TabsContent>
                    <TabsContent value="draft">
                      <div className="space-y-2">
                        <Label>Trace</Label>
                        <Select
                          aria-label="Trace for the LLM draft"
                          value={draftTraceId ?? ""}
                          onChange={(e) => setDraftTraceId(e.target.value || null)}
                        >
                          <option value="">Select a trace...</option>
                          {replaySet.trace_ids.map((id) => (
                            <option key={id} value={id}>
                              {id}
                            </option>
                          ))}
                        </Select>
                        <Label>Objective</Label>
                        <Textarea
                          value={draftObjective}
                          onChange={(e) => setDraftObjective(e.target.value)}
                          rows={2}
                          placeholder="What should the candidate do differently?"
                        />
                        <Button
                          size="sm"
                          onClick={handleGenerateDraft}
                          disabled={!draftTraceId || !draftObjective.trim() || createDraft.isPending}
                        >
                          {createDraft.isPending ? "Generating..." : "Generate draft"}
                        </Button>
                        {lastDraft && (
                          <div className="space-y-2 rounded-md border p-3">
                            <div className="flex flex-wrap items-center gap-1">
                              {lastDraft.is_mock && <Badge variant="warning">mock LLM</Badge>}
                              <Badge variant="outline">{lastDraft.decision_method ?? "reasoning_llm"}</Badge>
                              {lastDraft.provider && (
                                <Badge variant="outline">
                                  {lastDraft.provider}/{lastDraft.model}
                                </Badge>
                              )}
                              <Badge variant={lastDraft.status === "proposed" ? "success" : "destructive"}>
                                {lastDraft.status}
                              </Badge>
                            </div>
                            {lastDraft.status === "failed" ? (
                              <p className="text-xs text-destructive">{lastDraft.error}</p>
                            ) : (
                              <>
                                <Textarea
                                  value={lastDraft.patch_text}
                                  readOnly
                                  rows={8}
                                  className="font-mono text-xs"
                                />
                                <Button
                                  size="sm"
                                  onClick={handleRunDraft}
                                  disabled={!canRun || createVariantRun.isPending}
                                >
                                  {createVariantRun.isPending ? "Running..." : "Run this draft"}
                                </Button>
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    </TabsContent>
                  </Tabs>
                </CardContent>
              </Card>
            </div>
          </div>

          {variantRuns && variantRuns.length > 1 && (
            <div className="flex items-center gap-2 text-xs">
              <Label>Run</Label>
              <Select
                className="w-64"
                value={effectiveActiveRunId ? String(effectiveActiveRunId) : ""}
                onChange={(e) => setActiveRunId(Number(e.target.value))}
              >
                {variantRuns.map((r) => (
                  <option key={r.id} value={r.id}>
                    Run #{r.id} ({r.status})
                  </option>
                ))}
              </Select>
            </div>
          )}

          <ResultMatrix run={activeRun} recordedByTraceId={recordedByTraceId} />

          <EscalationPanel run={activeRun} componentId={componentId} source={source ?? null} />
        </div>
      )}
    </div>
  );
}

// --- Left pane: trace row ----------------------------------------------------

function TraceRow({
  trace,
  recorded,
  selected,
  onSelect,
}: {
  trace: ReplaySetTraceOut;
  recorded: TraceEvent | undefined;
  selected: boolean;
  onSelect: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className={`rounded-md border p-2 text-xs space-y-1 ${selected ? "border-primary" : ""}`}
    >
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          className="font-mono cursor-pointer hover:underline text-left"
          onClick={onSelect}
          title="Use this trace for an LLM draft"
        >
          {trace.trace_id.slice(0, 14)}
        </button>
        <div className="flex items-center gap-1">
          <ReplayabilityBadge replayability={trace.replayability} reasons={trace.replay_reasons} />
          {trace.input_source ? (
            <Badge variant="outline" className="text-[10px]">
              {trace.input_source}
            </Badge>
          ) : (
            <Badge variant="secondary" className="text-[10px]">
              will skip
            </Badge>
          )}
          <button
            type="button"
            className="text-muted-foreground cursor-pointer"
            aria-label={`${expanded ? "Hide" : "Show"} trace ${trace.trace_id} details`}
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </div>
      {trace.skip_reason && (
        <p className="text-amber-700 dark:text-amber-300">
          {SKIP_GUIDANCE[trace.skip_reason] ?? `This trace will be skipped: ${trace.skip_reason}.`}
        </p>
      )}
      {expanded && (
        <div className="space-y-2 pt-1 border-t">
          <div>
            <span className="text-muted-foreground">Input capture:</span>
            {recorded?.input_capture != null ? (
              <JsonTree data={recorded.input_capture} defaultExpanded={false} />
            ) : (
              <span className="text-muted-foreground"> —</span>
            )}
          </div>
          <div>
            <span className="text-muted-foreground">Recorded output:</span>
            <pre className="whitespace-pre-wrap break-words">{recorded?.output ?? "—"}</pre>
          </div>
          {recorded?.error && (
            <div>
              <span className="text-destructive">Recorded error:</span>
              <pre className="whitespace-pre-wrap break-words text-destructive">{recorded.error}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- Approval panel (state guidance + approve/revoke gate) -------------------

function ApprovalPanel({ componentId }: { componentId: string }) {
  const { data: approvalState } = useReplayApproval(componentId);
  const approve = useApproveReplay();
  const revoke = useRevokeReplayApproval();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reason, setReason] = useState("");

  if (!approvalState) return <Skeleton className="h-14 w-full" />;

  if (!approvalState.active) {
    return (
      <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 p-3 text-xs space-y-2">
        <p className="font-medium text-amber-900 dark:text-amber-100">
          Replay is not approved for "{componentId}" -- runs are blocked until a human approves it.
        </p>
        <p className="text-muted-foreground">
          Next step: review the risk context and approve replay for this component.
        </p>
        <Button size="sm" onClick={() => setConfirmOpen(true)}>
          Review &amp; Approve
        </Button>
        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogHeader>
            <DialogTitle>Approve replay for "{componentId}"</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <p className="text-muted-foreground">{approvalState.risk_context.warning}</p>
            {approvalState.risk_context.probe_plan_points.length > 0 && (
              <ul className="text-xs space-y-1">
                {approvalState.risk_context.probe_plan_points.map((p) => (
                  <li key={p.point_id}>
                    plan #{p.plan_id}: side_effect_risk={p.side_effect_risk ?? "unknown"}, replayability=
                    {p.replayability ?? "unknown"}
                  </li>
                ))}
              </ul>
            )}
            <div className="space-y-1">
              <Label>Reason</Label>
              <Input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why is replay safe to approve for this component?"
              />
            </div>
            <Button
              className="w-full"
              disabled={!reason.trim() || approve.isPending}
              onClick={async () => {
                try {
                  await approve.mutateAsync({ componentId, reason: reason.trim() });
                  toast.success("Replay approved");
                  setConfirmOpen(false);
                  setReason("");
                } catch (e) {
                  toast.error(String(e));
                }
              }}
            >
              {approve.isPending ? "Approving..." : "Approve"}
            </Button>
          </div>
        </Dialog>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between rounded-md border p-2 text-xs">
      <span className="text-muted-foreground">Replay approved for "{componentId}".</span>
      <Button
        size="sm"
        variant="outline"
        onClick={() =>
          revoke
            .mutateAsync(componentId)
            .then(() => toast.success("Approval revoked"))
            .catch((e) => toast.error(String(e)))
        }
        disabled={revoke.isPending}
      >
        Revoke
      </Button>
    </div>
  );
}

// --- Result matrix ------------------------------------------------------------

const CASE_STATUS_VARIANT: Record<string, "success" | "warning" | "destructive" | "secondary" | "outline"> = {
  match: "success",
  diff: "warning",
  candidate_error: "destructive",
  error_to_success: "success",
  error_to_same_error: "secondary",
  error_to_different_error: "warning",
  skipped: "outline",
};

const CASE_STATUS_LABEL: Record<string, string> = {
  match: "match",
  diff: "diff",
  candidate_error: "candidate error",
  error_to_success: "rescued",
  error_to_same_error: "same error",
  error_to_different_error: "different error",
  skipped: "skipped",
};

function ResultMatrix({
  run,
  recordedByTraceId,
}: {
  run: ReplayVariantRunOut | undefined;
  recordedByTraceId: Map<string, TraceEvent>;
}) {
  return (
    <div data-testid="workbench-result-matrix" className="space-y-3">
      <div className="rounded-md border border-blue-200 bg-blue-50 dark:bg-blue-950/20 dark:border-blue-800 px-3 py-2 text-xs text-blue-900 dark:text-blue-100">
        Simulation only: replay executes in an isolated, network-off sandbox against the pinned
        snapshot. Results can differ from production (environment, external services,
        time-dependent state) -- this is not a production-equivalence guarantee.
      </div>
      {!run ? (
        <p className="text-sm text-muted-foreground">Run a variant to see the diff matrix.</p>
      ) : run.status !== "completed" ? (
        <p className="text-sm text-muted-foreground">
          Run #{run.id}: {run.status}
          {run.error ? ` -- ${run.error}` : ""}
        </p>
      ) : (
        <MatrixTable run={run} recordedByTraceId={recordedByTraceId} />
      )}
    </div>
  );
}

function MatrixTable({
  run,
  recordedByTraceId,
}: {
  run: ReplayVariantRunOut;
  recordedByTraceId: Map<string, TraceEvent>;
}) {
  const candidates = run.variants.filter((v) => !v.is_baseline);
  const rows = candidates[0]?.cases ?? [];

  if (candidates.length === 0 || rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No cases to compare for this run.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-xs">
        <thead className="bg-muted/40">
          <tr className="text-left">
            <th className="p-2">Trace</th>
            <th className="p-2">Recorded</th>
            <th className="p-2">Baseline replay</th>
            {candidates.map((v) => (
              <th key={v.id} className="p-2">
                {v.label || v.variant_key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const recorded = recordedByTraceId.get(row.trace_id);
            return (
              <tr key={row.trace_id} className="border-t align-top">
                <td className="p-2 font-mono">{row.trace_id.slice(0, 10)}</td>
                <td className="p-2 max-w-[16rem]">
                  <pre className="whitespace-pre-wrap break-words">
                    {recorded?.error ?? recorded?.output ?? "—"}
                  </pre>
                </td>
                <td className="p-2 max-w-[16rem]">
                  <pre className="whitespace-pre-wrap break-words">
                    {row.baseline_output ?? "—"}
                  </pre>
                </td>
                {candidates.map((v) => {
                  const caseResult = v.cases.find((c) => c.position === row.position);
                  return (
                    <td key={v.id} className="p-2 max-w-[16rem]">
                      {caseResult ? <CaseCell caseResult={caseResult} /> : "—"}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t bg-muted/20 font-medium">
            <td className="p-2" colSpan={3}>
              Aggregate
            </td>
            {candidates.map((v) => (
              <td key={v.id} className="p-2">
                <AggregateCell aggregate={v.aggregate} />
              </td>
            ))}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function CaseCell({ caseResult }: { caseResult: ReplayVariantCaseResultOut }) {
  return (
    <div className="space-y-1">
      <Badge variant={CASE_STATUS_VARIANT[caseResult.case_status] ?? "outline"} className="text-[10px]">
        {CASE_STATUS_LABEL[caseResult.case_status] ?? caseResult.case_status}
      </Badge>
      <pre className="whitespace-pre-wrap break-words">
        {caseResult.candidate_error ?? caseResult.candidate_output ?? "—"}
      </pre>
      {caseResult.field_diffs.length > 0 && (
        <p className="text-muted-foreground">fields: {caseResult.field_diffs.join(", ")}</p>
      )}
      {caseResult.duration_delta_ms != null && (
        <p className="text-muted-foreground">Δ {caseResult.duration_delta_ms.toFixed(1)}ms</p>
      )}
    </div>
  );
}

function AggregateCell({ aggregate }: { aggregate: ReplayVariantAggregateOut }) {
  return (
    <div className="space-y-0.5 text-[11px]">
      <div>
        match {aggregate.match} / diff {aggregate.diff}
      </div>
      <div>candidate_error {aggregate.candidate_error}</div>
      <div>rescued (error→success) {aggregate.error_to_success}</div>
      <div>
        skipped {aggregate.skipped} / total {aggregate.total}
      </div>
      {aggregate.avg_duration_delta_ms != null && (
        <div>avg Δ {aggregate.avg_duration_delta_ms.toFixed(1)}ms</div>
      )}
    </div>
  );
}

// --- Escalation panel ---------------------------------------------------------

function buildRegressionScaffold(params: {
  path: string;
  qualifiedName: string;
  traceId: string;
  recorded?: TraceEvent;
}): string {
  const simpleName = params.qualifiedName.split(".").pop() || params.qualifiedName;
  const moduleGuess = params.path.replace(/\.py$/, "").replace(/\//g, ".");
  const lines = [
    "# Deterministic starting point generated client-side from the resolved",
    "# symbol and the recorded trace -- NOT reasoning-model output.",
    "# Review and complete before use.",
    `# Source: ${params.path} :: ${params.qualifiedName} (trace ${params.traceId})`,
    "",
    `from ${moduleGuess} import ${simpleName}`,
    "",
    "",
    `def test_${simpleName}_regression():`,
    "    # TODO: fill in the exact args/kwargs from this trace's structured input capture.",
    `    result = ${simpleName}(...)`,
  ];
  if (params.recorded?.error) {
    lines.push(`    # Recorded error for this trace: ${params.recorded.error}`);
  } else {
    lines.push(
      `    assert result == ${JSON.stringify(params.recorded?.output ?? "")}  # TODO: adjust once verified`,
    );
  }
  return lines.join("\n") + "\n";
}

function EscalationPanel({
  run,
  componentId,
  source,
}: {
  run: ReplayVariantRunOut | undefined;
  componentId: string | null;
  source: { path: string; qualified_name: string } | null;
}) {
  const navigate = useNavigate();
  const candidates = run?.variants.filter((v) => !v.is_baseline) ?? [];
  const [scaffold, setScaffold] = useState<string | null>(null);

  const firstRow = candidates[0]?.cases[0];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Escalate</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-xs">
        <div className="space-y-2">
          <p className="font-medium">Promote a variant to an Experiment</p>
          {!run || candidates.length === 0 ? (
            <p className="text-muted-foreground">Run a candidate variant first.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {candidates.map((v) => (
                <Button
                  key={v.id}
                  size="sm"
                  variant="outline"
                  disabled={!v.patch_text}
                  onClick={() =>
                    navigate(`/experiments?replay_run_id=${run.id}&replay_variant_id=${v.id}`)
                  }
                >
                  Promote "{v.label || v.variant_key}"
                </Button>
              ))}
            </div>
          )}
          <p className="text-muted-foreground">
            Hands the variant's patch to the Experiments create flow for review -- this never
            creates or adopts an Experiment automatically. The developer decides on the
            Experiments tab.
          </p>
        </div>

        <div className="space-y-2 border-t pt-3">
          <p className="font-medium">Regression-test scaffold</p>
          <p className="text-muted-foreground">
            A deterministic starting point generated client-side from the resolved symbol and the
            recorded trace -- NOT reasoning-model output. Review and complete it before use.
          </p>
          <Button
            size="sm"
            variant="outline"
            disabled={!source || !firstRow}
            onClick={() => {
              if (!source || !firstRow) return;
              setScaffold(
                buildRegressionScaffold({
                  path: source.path,
                  qualifiedName: source.qualified_name,
                  traceId: firstRow.trace_id,
                }),
              );
            }}
          >
            Generate regression-test scaffold
          </Button>
          {scaffold && <Textarea value={scaffold} readOnly rows={10} className="font-mono text-xs" />}
        </div>

        <div className="space-y-2 border-t pt-3">
          <p className="font-medium">Live shadow (SDK)</p>
          <p className="text-muted-foreground">
            To try a candidate against real traffic instead of a simulation, register it as the
            shadow candidate and switch the component to shadow mode:
          </p>
          <pre className="rounded-md bg-muted p-2 overflow-x-auto">
            {`from probe_agent import set_candidate\n\nset_candidate("${componentId ?? "<component_id>"}", candidate_fn)`}
          </pre>
          <p className="text-muted-foreground">
            Then set this component's mode to <span className="font-mono">shadow</span> on the
            Components tab. Shadow mode never changes the returned production value (Principle 1).
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
