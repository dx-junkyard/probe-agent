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
  useCreateReplayRegressionScaffold,
  useReplayApproval,
  useTraces,
} from "@/api/hooks";
import type {
  ReplayVariantRunOut,
  ReplayVariantDraftOut,
  ReplayRegressionScaffoldOut,
  ReplaySetTraceOut,
  TraceEvent,
} from "@/api/types";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { JsonTree } from "@/components/json-tree";
import { Bot } from "lucide-react";
import { ReplayabilityBadge } from "@/components/replay-row-actions";
import { ApprovalPanel } from "@/components/replay-approval-panel";
import { ResultMatrix } from "@/components/replay-result-matrix";

// Simulation Workbench (Issue #242 Phase D / #246). Display + composition
// only: every judgement/execution/comparison decision below is made by the
// Phase A-C APIs (replayability classification, replay execution, variant
// diff classification). This page only composes those results and the two
// new deterministic source/diff helpers into one edit -> run -> inspect ->
// promote loop.

const SKIP_GUIDANCE: Record<string, string> = {
  unreplayable_capture:
    "このTraceの記録済み入力は構造的に復元できませんでした（redact済み、サイズ超過、または未対応の型）。次の一歩: 別のTraceを選ぶか、このcomponentのreplay_captureのredaction/サイズ制限を調整してください。",
  repr_parse_failed:
    "構造化capture が記録されておらず、旧式のrepr入力を呼び出しに復元できませんでした。次の一歩: このcomponentをreplay_capture=Trueにopt-inし、今後のTraceをreplayable にしてください。",
  undecodable_input:
    "保存された構造化captureがharnessでデコードできないencodingでした。次の一歩: このTraceを再captureしてください。",
  trace_missing:
    "このTraceはこのSystemにはもう存在しません。次の一歩: Replay Setから削除してください。",
};

export default function SimulationWorkbenchPage() {
  const navigate = useNavigate();
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
  const sourceIdentity = source
    ? `${source.snapshot_id}:${source.commit_sha}:${source.path}`
    : undefined;
  const [lastLoadedSourceIdentity, setLastLoadedSourceIdentity] = useState<string | undefined>(
    undefined,
  );
  if (source?.source != null && sourceIdentity !== lastLoadedSourceIdentity) {
    setLastLoadedSourceIdentity(sourceIdentity);
    setEditedSource(source.source);
  }

  const [pastedPatch, setPastedPatch] = useState("");
  const [draftTraceId, setDraftTraceId] = useState<string | null>(null);
  const [draftObjective, setDraftObjective] = useState("");
  const [lastDraft, setLastDraft] = useState<ReplayVariantDraftOut | null>(null);
  const activeDraft = lastDraft?.replay_set_id === selectedSetId ? lastDraft : null;

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
    if (!selectedSetId || !source) return;
    try {
      const diff = await sourceDiff.mutateAsync({
        replay_set_id: selectedSetId,
        snapshot_id: source?.snapshot_id,
        edited_source: editedSource,
      });
      const run = await createVariantRun.mutateAsync({
        replay_set_id: selectedSetId,
        snapshot_id: source?.snapshot_id,
        variants: [{ label: "Direct edit", patch_text: diff.patch_text, source: "manual" }],
      });
      setActiveRunId(run.id);
      toast.success("Replay variant runが完了しました");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleRunPastedPatch = async () => {
    if (!selectedSetId || !source || !pastedPatch.trim()) return;
    try {
      const run = await createVariantRun.mutateAsync({
        replay_set_id: selectedSetId,
        snapshot_id: source?.snapshot_id,
        variants: [{ label: "Pasted patch", patch_text: pastedPatch, source: "pasted" }],
      });
      setActiveRunId(run.id);
      toast.success("Replay variant runが完了しました");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleGenerateDraft = async () => {
    if (!selectedSetId || !source || !draftTraceId || !draftObjective.trim()) return;
    try {
      const draft = await createDraft.mutateAsync({
        replay_set_id: selectedSetId,
        trace_id: draftTraceId,
        objective: draftObjective.trim(),
        snapshot_id: source?.snapshot_id,
      });
      setLastDraft(draft);
      if (draft.status === "failed") toast.error(draft.error || "Draftの生成に失敗しました");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleRunDraft = async () => {
    if (!selectedSetId || !activeDraft?.patch_text) return;
    try {
      const run = await createVariantRun.mutateAsync({
        replay_set_id: selectedSetId,
        snapshot_id: activeDraft.snapshot_id,
        variants: [
          {
            label: `LLM draft: ${activeDraft.objective.slice(0, 40)}`,
            patch_text: activeDraft.patch_text,
            source: "llm_draft",
          },
        ],
      });
      setActiveRunId(run.id);
      toast.success("Replay variant runが完了しました");
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight">Simulation Workbench</h1>
        <div className="flex items-center gap-2">
          {selectedSetId && componentId && (
            <Button
              variant="outline"
              onClick={() =>
                navigate(
                  `/candidate-studio?component_id=${encodeURIComponent(componentId)}` +
                    `&replay_set_id=${selectedSetId}`,
                )
              }
            >
              <Bot className="mr-1 h-4 w-4" /> 会話で候補を改善
            </Button>
          )}
          <div className="w-72">
            <Select
              value={selectedSetId ? String(selectedSetId) : ""}
              onChange={(e) => e.target.value && selectSet(Number(e.target.value))}
            >
              <option value="">Replay Setを選択...</option>
              {sets?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || `Set #${s.id}`} — {s.component_id} ({s.trace_ids.length})
                </option>
              ))}
            </Select>
          </div>
        </div>
      </div>

      {!selectedSetId ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            上でReplay Setを選択するか、Components タブのTraceで「Replay」を使ってください。
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
                    <p className="text-sm text-muted-foreground">このSetにTraceはありません。</p>
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
                  <CardTitle className="text-sm">候補ソース</CardTitle>
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
                      このcomponentのReplayは未承認です -- 上で承認されるまでRunは無効化されます。
                    </p>
                  )}
                  <Tabs defaultValue="direct">
                    <TabsList>
                      <TabsTrigger value="direct">直接編集</TabsTrigger>
                      <TabsTrigger value="paste">パッチを貼り付け</TabsTrigger>
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
                          disabled={
                            !canRun ||
                            !source ||
                            editedSource === source.source ||
                            sourceDiff.isPending ||
                            createVariantRun.isPending
                          }
                        >
                          {sourceDiff.isPending || createVariantRun.isPending ? "実行中..." : "Run"}
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
                          placeholder="unified diff (git diff形式) を貼り付け..."
                        />
                        <Button
                          size="sm"
                          onClick={handleRunPastedPatch}
                          disabled={!canRun || !source || !pastedPatch.trim() || createVariantRun.isPending}
                        >
                          {createVariantRun.isPending ? "実行中..." : "Run"}
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
                          <option value="">Traceを選択...</option>
                          {replaySet.trace_ids.map((id) => (
                            <option key={id} value={id}>
                              {id}
                            </option>
                          ))}
                        </Select>
                        <Label>改善目標</Label>
                        <Textarea
                          value={draftObjective}
                          onChange={(e) => setDraftObjective(e.target.value)}
                          rows={2}
                          placeholder="候補コードにどう変わってほしいか"
                        />
                        <Button
                          size="sm"
                          onClick={handleGenerateDraft}
                          disabled={!source || !draftTraceId || !draftObjective.trim() || createDraft.isPending}
                        >
                          {createDraft.isPending ? "生成中..." : "Draftを生成"}
                        </Button>
                        {activeDraft && (
                          <div className="space-y-2 rounded-md border p-3">
                            <div className="flex flex-wrap items-center gap-1">
                              {activeDraft.is_mock && <Badge variant="warning">mock LLM</Badge>}
                              <Badge variant="outline">{activeDraft.decision_method ?? "reasoning_llm"}</Badge>
                              {activeDraft.provider && (
                                <Badge variant="outline">
                                  {activeDraft.provider}/{activeDraft.model}
                                </Badge>
                              )}
                              <Badge variant={activeDraft.status === "proposed" ? "success" : "destructive"}>
                                {activeDraft.status}
                              </Badge>
                            </div>
                            {activeDraft.status === "failed" ? (
                              <p className="text-xs text-destructive">{activeDraft.error}</p>
                            ) : (
                              <>
                                <Textarea
                                  value={activeDraft.patch_text}
                                  readOnly
                                  rows={8}
                                  className="font-mono text-xs"
                                />
                                <Button
                                  size="sm"
                                  onClick={handleRunDraft}
                                  disabled={
                                    !canRun ||
                                    !activeDraft.patch_text ||
                                    createVariantRun.isPending
                                  }
                                >
                                  {createVariantRun.isPending ? "実行中..." : "このDraftを実行"}
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

          <div data-testid="workbench-result-matrix">
            <ResultMatrix run={activeRun} recordedByTraceId={recordedByTraceId} />
          </div>

          <EscalationPanel
            run={activeRun}
            componentId={componentId}
            source={source ?? null}
            selectedTraceId={draftTraceId}
          />
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
          title="このTraceをLLM draftに使う"
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
              skipされます
            </Badge>
          )}
          <button
            type="button"
            className="text-muted-foreground cursor-pointer"
            aria-label={`Trace ${trace.trace_id} の詳細を${expanded ? "隠す" : "表示"}`}
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </div>
      {trace.skip_reason && (
        <p className="text-amber-700 dark:text-amber-300">
          {SKIP_GUIDANCE[trace.skip_reason] ?? `このTraceはskipされます: ${trace.skip_reason}`}
        </p>
      )}
      {expanded && (
        <div className="space-y-2 pt-1 border-t">
          <div>
            <span className="text-muted-foreground">入力capture:</span>
            {recorded?.input_capture != null ? (
              <JsonTree data={recorded.input_capture} defaultExpanded={false} />
            ) : (
              <span className="text-muted-foreground"> —</span>
            )}
          </div>
          <div>
            <span className="text-muted-foreground">記録済み出力:</span>
            <pre className="whitespace-pre-wrap break-words">{recorded?.output ?? "—"}</pre>
          </div>
          {recorded?.error && (
            <div>
              <span className="text-destructive">記録済みエラー:</span>
              <pre className="whitespace-pre-wrap break-words text-destructive">{recorded.error}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- Escalation panel ---------------------------------------------------------

function EscalationPanel({
  run,
  componentId,
  source,
  selectedTraceId,
}: {
  run: ReplayVariantRunOut | undefined;
  componentId: string | null;
  source: { path: string; qualified_name: string } | null;
  selectedTraceId: string | null;
}) {
  const navigate = useNavigate();
  const candidates = run?.variants.filter((v) => !v.is_baseline) ?? [];
  const createScaffold = useCreateReplayRegressionScaffold();
  const [scaffold, setScaffold] = useState<ReplayRegressionScaffoldOut | null>(null);

  const scaffoldCandidate = candidates.find(
    (candidate) =>
      candidate.status === "completed" &&
      candidate.apply_status === "applied" &&
      candidate.cases.length > 0,
  );
  const firstRow =
    scaffoldCandidate?.cases.find((row) => row.trace_id === selectedTraceId) ??
    scaffoldCandidate?.cases[0];
  const activeScaffold = scaffold?.replay_run_id === run?.id ? scaffold : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">エスカレーション</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-xs">
        <div className="space-y-2">
          <p className="font-medium">variantをExperimentへpromoteする</p>
          {!run || candidates.length === 0 ? (
            <p className="text-muted-foreground">先に候補variantを実行してください。</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {candidates.map((v) => (
                <Button
                  key={v.id}
                  size="sm"
                  variant="outline"
                  disabled={
                    !v.patch_text || v.status !== "completed" || v.apply_status !== "applied"
                  }
                  onClick={() =>
                    navigate(`/experiments?replay_run_id=${run.id}&replay_variant_id=${v.id}`)
                  }
                >
                  「{v.label || v.variant_key}」をpromote
                </Button>
              ))}
            </div>
          )}
          <p className="text-muted-foreground">
            variantのpatchをExperiments作成フローへ渡すだけです -- 自動的にExperimentを
            作成・採用することはありません。開発者がExperimentsタブで判断します。
          </p>
        </div>

        <div className="space-y-2 border-t pt-3">
          <p className="font-medium">回帰テストscaffold</p>
          <p className="text-muted-foreground">
            pinされたsymbol・記録済みTrace・候補patchに基づく、レビュー専用のreasoning-model
            draftです。対象repositoryへ自動的に書き込まれることはありません。
          </p>
          <Button
            size="sm"
            variant="outline"
            disabled={!source || !run || !scaffoldCandidate || !firstRow || createScaffold.isPending}
            onClick={async () => {
              if (!run || !scaffoldCandidate || !firstRow) return;
              try {
                const result = await createScaffold.mutateAsync({
                  replay_run_id: run.id,
                  replay_variant_id: scaffoldCandidate.id,
                  trace_id: firstRow.trace_id,
                });
                setScaffold(result);
                if (result.status === "failed") {
                  toast.error(result.error || "回帰テストscaffoldの生成に失敗しました");
                }
              } catch (error) {
                toast.error(String(error));
              }
            }}
          >
            {createScaffold.isPending ? "生成中..." : "回帰テストscaffoldを生成"}
          </Button>
          {activeScaffold && (
            <div className="space-y-2 rounded border p-2">
              <div className="flex items-center gap-1 flex-wrap">
                {activeScaffold.is_mock && <Badge variant="warning">mock LLM</Badge>}
                <Badge variant="outline">{activeScaffold.decision_method}</Badge>
                <Badge variant="outline">{activeScaffold.provider}/{activeScaffold.model}</Badge>
                <Badge variant={activeScaffold.status === "proposed" ? "success" : "destructive"}>
                  {activeScaffold.status}
                </Badge>
                <span className="text-muted-foreground">
                  run #{activeScaffold.intelligence_run_id} · {activeScaffold.prompt_version}
                </span>
              </div>
              {activeScaffold.status === "failed" ? (
                <p className="text-destructive">{activeScaffold.error}</p>
              ) : (
                <Textarea
                  value={activeScaffold.scaffold_text}
                  readOnly
                  rows={10}
                  className="font-mono text-xs"
                />
              )}
            </div>
          )}
        </div>

        <div className="space-y-2 border-t pt-3">
          <p className="font-medium">Live shadow (SDK)</p>
          <p className="text-muted-foreground">
            シミュレーションではなく実際のトラフィックに対して候補を試すには、shadow候補として
            登録し、componentをshadow modeに切り替えてください:
          </p>
          <pre className="rounded-md bg-muted p-2 overflow-x-auto">
            {`from probe_agent import set_candidate\n\nset_candidate("${componentId ?? "<component_id>"}", candidate_fn)`}
          </pre>
          <p className="text-muted-foreground">
            その後、Components タブでこのcomponentのmodeを{" "}
            <span className="font-mono">shadow</span> に設定してください。shadow
            modeは本番の戻り値を変更しません（Principle 1）。
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
