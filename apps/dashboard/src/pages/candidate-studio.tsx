import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ReplayReadinessPanel } from "@/components/replay-readiness";
import { ImprovementLoopRail } from "@/components/improvement-loop/rail";
import { toast } from "sonner";
import {
  useComponents,
  useTraces,
  useReplayReadiness,
  useReplaySets,
  useReplayApproval,
  useReplayVariantRun,
  useCandidateSession,
  useCreateCandidateSession,
  useSendCandidateMessage,
  useGenerateCandidateVersion,
  useReplayCandidateVersion,
  usePromoteCandidateVersion,
} from "@/api/hooks";
import type {
  CandidateSessionCreateRequest,
  CandidateVersionOut,
  ReplayVariantCaseResultOut,
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
import { CodeBlock } from "@/components/code-block";
import { ApprovalPanel } from "@/components/replay-approval-panel";
import { ResultMatrix } from "@/components/replay-result-matrix";
import { cn, formatTimestamp } from "@/lib/utils";

// AI Candidate Studio (Issue #252): a conversation + candidate-versioning UI
// over the EXISTING isolated Replay stack (#243-#246). This page adds no new
// judgement/execution path -- candidate generation is a reasoning-model
// structured proposal + deterministic splice->diff (fail-closed, surfaced as
// a 502 on failure); replaying a version reuses the Simulation Workbench's
// POST /replay-variant-runs verbatim (same human approval gate, sandbox, and
// diff matrix, reused here via ApprovalPanel/ResultMatrix); promotion only
// hands a reviewed patch to the existing Experiments create flow (Principle 7)
// -- it never creates an Experiment, merges, or deploys anything.

// Deterministic finite-set state derived from a version's persisted fields
// (Principle 6) -- never an inference. Only "proposed" versions are ever
// persisted (a failed generation attempt never creates a version row), so a
// version's studio state is fully determined by its replay lifecycle.
type StudioState =
  | "no_version"
  | "generated"
  | "replaying"
  | "evaluation_failed"
  | "evaluated"
  | "promoted";

const STATE_LABEL_JA: Record<StudioState, string> = {
  no_version: "条件入力済み",
  generated: "生成済み",
  replaying: "Replay実行中",
  evaluation_failed: "評価失敗",
  evaluated: "評価済み",
  promoted: "Experimentへ送信済み",
};

function versionState(v: CandidateVersionOut): StudioState {
  if (v.replay_status === "running") return "replaying";
  if (v.replay_status === "failed") return "evaluation_failed";
  if (v.replay_status === "completed") return v.promoted_at ? "promoted" : "evaluated";
  return "generated";
}

export default function CandidateStudioPage() {
  const [searchParams] = useSearchParams();
  const sessionIdParam = searchParams.get("session_id");
  const sessionId =
    sessionIdParam && Number.isInteger(Number(sessionIdParam)) ? Number(sessionIdParam) : null;

  if (!sessionId) {
    return (
      <StartView
        prefillComponentId={searchParams.get("component_id")}
        prefillTraceId={searchParams.get("trace_id")}
        prefillReplaySetId={searchParams.get("replay_set_id")}
      />
    );
  }
  return <StudioView sessionId={sessionId} />;
}

// --- Start view: pick a component (+ optional trace), create the session ---

function StartView({
  prefillComponentId,
  prefillTraceId,
  prefillReplaySetId,
}: {
  prefillComponentId: string | null;
  prefillTraceId: string | null;
  prefillReplaySetId: string | null;
}) {
  const navigate = useNavigate();
  const { data: components } = useComponents();
  const [componentId, setComponentId] = useState(prefillComponentId ?? "");
  const [traceId, setTraceId] = useState(prefillTraceId ?? "");
  const [objective, setObjective] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(!!prefillReplaySetId);
  const [replaySetId, setReplaySetId] = useState(prefillReplaySetId ?? "");

  const { data: traces } = useTraces(componentId || null, 50);
  const { data: replaySets } = useReplaySets(componentId || null);
  const createSession = useCreateCandidateSession();

  // Issue #372: evaluate Replay readiness for exactly the set this session
  // would use, before the reasoning-model call. A Replay Set is resolved
  // server-side, so its own trace ids are what get checked there; here we
  // scope to the explicitly chosen trace when there is one.
  const { data: readiness, isLoading: readinessLoading } = useReplayReadiness(
    componentId || null,
    traceId && !replaySetId ? [traceId] : undefined,
  );
  const readinessBlocked = readiness?.verdict === "blocked" && !replaySetId;

  const canStart = !!componentId && !createSession.isPending && !readinessBlocked;

  const handleStart = async () => {
    const payload: CandidateSessionCreateRequest = {
      component_id: componentId,
      objective: objective.trim() || undefined,
    };
    if (replaySetId) {
      payload.replay_set_id = Number(replaySetId);
    } else if (traceId) {
      payload.trace_id = traceId;
    }
    try {
      const session = await createSession.mutateAsync(payload);
      navigate(`/candidate-studio?session_id=${session.id}`);
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="max-w-2xl space-y-6">
      {/* Issue #371: the same rail Components / Workbench / Experiments show. */}
      <ImprovementLoopRail componentId={componentId || null} traceId={traceId || null} />
      <div>
        <h1 className="text-2xl font-bold tracking-tight">AI Candidate Studio</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          会話を通じて、記録済みの実際のTraceを起点にcomponentの候補versionを生成・評価します。
          これは隔離されたReplay基盤上のシミュレーションであり、本番環境に対して実行されることは
          なく、候補が自動的に採用されることもありません。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">セッションを開始</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Component</Label>
            <Select
              value={componentId}
              onChange={(e) => {
                setComponentId(e.target.value);
                setTraceId("");
                setReplaySetId("");
              }}
            >
              <option value="">componentを選択...</option>
              {components?.map((c) => (
                <option key={c.component_id} value={c.component_id}>
                  {c.component_id} ({c.trace_count} traces)
                </option>
              ))}
            </Select>
          </div>

          {componentId && (
            <div className="space-y-2">
              <Label>Trace（任意: 改善の起点にする入力）</Label>
              <Select value={traceId} onChange={(e) => setTraceId(e.target.value)} disabled={!!replaySetId}>
                <option value="">Traceを選択...</option>
                {traces?.map((t) => (
                  <option key={t.trace_id} value={t.trace_id}>
                    {t.trace_id.slice(0, 20)} @ {formatTimestamp(t.timestamp)}
                  </option>
                ))}
              </Select>
              {!traces?.length && (
                <p className="text-xs text-muted-foreground">
                  このcomponentのTraceはまだ記録されていません。
                </p>
              )}
            </div>
          )}

          {/* Issue #372: the split, the actually-usable count, and the
              recovery step — all before anything is generated. This replaces
              the old bare 「直近最大50件が自動的に使われます」 note, which said
              how many traces would be read but not how many could be used. */}
          {componentId && (
            <ReplayReadinessPanel readiness={readiness} isLoading={readinessLoading} />
          )}

          <div className="space-y-2">
            <Label>改善目標（任意）</Label>
            <Textarea
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              rows={2}
              placeholder="候補versionにどう変わってほしいか"
            />
          </div>

          <button
            type="button"
            className="text-xs text-muted-foreground underline cursor-pointer"
            onClick={() => setAdvancedOpen((v) => !v)}
          >
            {advancedOpen ? "詳細設定を隠す" : "詳細設定"}
          </button>
          {advancedOpen && componentId && (
            <div className="space-y-2 rounded-md border p-3">
              <Label>単一Traceの代わりに既存のReplay Setを使う</Label>
              <Select value={replaySetId} onChange={(e) => setReplaySetId(e.target.value)}>
                <option value="">（なし -- 上で選択したTraceを使う）</option>
                {replaySets?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name || `Set #${s.id}`} ({s.trace_ids.length} traces)
                  </option>
                ))}
              </Select>
            </div>
          )}

          <Button
            onClick={handleStart}
            disabled={!canStart}
            title={
              readinessBlocked
                ? "Replayに使えるTraceがないため、候補を評価できません — 上の回復手順を参照してください"
                : undefined
            }
          >
            {createSession.isPending ? "開始中..." : "セッションを開始"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

// --- Studio view: conversation + candidate-version tabs ---------------------

function StudioView({ sessionId }: { sessionId: number }) {
  const navigate = useNavigate();
  const { data: session, isLoading } = useCandidateSession(sessionId);
  const sendMessage = useSendCandidateMessage();
  const generate = useGenerateCandidateVersion();
  const replay = useReplayCandidateVersion();
  const promote = usePromoteCandidateVersion();

  const [draftText, setDraftText] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState<number | null>(null);
  const [trackedSessionId, setTrackedSessionId] = useState<number | null>(null);

  const versions = useMemo(() => session?.versions ?? [], [session]);

  // Render-time state reset (React's documented pattern): default to the
  // most recent version whenever the session changes or a new version is
  // generated, same approach as the Simulation Workbench's active-run state.
  let effectiveSelectedVersionId = selectedVersionId;
  if (session && sessionId !== trackedSessionId) {
    setTrackedSessionId(sessionId);
    setSelectedVersionId(null);
    setDraftText(session.objective);
    effectiveSelectedVersionId = null;
  } else if (
    versions.length > 0 &&
    (effectiveSelectedVersionId === null ||
      !versions.some((v) => v.id === effectiveSelectedVersionId))
  ) {
    const last = versions[versions.length - 1].id;
    setSelectedVersionId(last);
    effectiveSelectedVersionId = last;
  }

  const componentId = session?.component_id ?? null;
  const { data: approvalState } = useReplayApproval(componentId);
  const canRun = !!approvalState?.active;

  if (isLoading || !session) {
    return <Skeleton className="h-64 w-full" />;
  }

  const selectedVersion = versions.find((v) => v.id === effectiveSelectedVersionId) ?? null;
  const state: StudioState = selectedVersion ? versionState(selectedVersion) : "no_version";

  const handleGenerate = async (parentVersionId: number | null) => {
    if (!draftText.trim()) {
      toast.error("先に指示を入力してください。");
      return;
    }
    try {
      const version = await generate.mutateAsync({
        sessionId,
        instruction: draftText.trim(),
        parent_version_id: parentVersionId,
      });
      setDraftText("");
      setSelectedVersionId(version.id);
      toast.success(`候補 v${version.version_number} を生成しました`);
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleSend = async () => {
    if (!draftText.trim()) return;
    try {
      await sendMessage.mutateAsync({ sessionId, content: draftText.trim() });
      setDraftText("");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleReplay = async () => {
    if (!selectedVersion) return;
    try {
      const version = await replay.mutateAsync({ versionId: selectedVersion.id, sessionId });
      setSelectedVersionId(version.id);
      if (version.replay_status === "failed") {
        toast.error("Replayは完了しましたが、runは成功しませんでした -- 評価結果を確認してください。");
      } else {
        toast.success("Replayが完了しました");
      }
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handlePromote = async () => {
    if (!selectedVersion) return;
    try {
      await promote.mutateAsync({ versionId: selectedVersion.id, sessionId });
      toast.success("送信しました -- Experiments作成フローを開きます");
      navigate(
        `/experiments?replay_run_id=${selectedVersion.replay_run_id}` +
          `&replay_variant_id=${selectedVersion.replay_variant_id}`,
      );
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="space-y-4">
      {/* Issue #371: the loop's current position, derived from the version's
          own persisted replay/promotion state -- not from the route, which
          serves three stages of the loop. */}
      <ImprovementLoopRail
        componentId={componentId}
        sessionId={sessionId}
        version={selectedVersion}
        approved={canRun}
        snapshotId={session.snapshot_id}
      />
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">AI Candidate Studio</h1>
          <p className="font-mono text-xs text-muted-foreground">
            {componentId} · baseline {session.commit_sha.slice(0, 8)} · {session.symbol_path}:
            {session.symbol_qualified_name}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={canRun ? "success" : "warning"}>
            {canRun ? "Replay承認済み" : "Replay未承認"}
          </Badge>
          <Badge variant="outline">{STATE_LABEL_JA[state]}</Badge>
          {versions.length > 0 && (
            <Select
              className="w-40"
              aria-label="Candidate version"
              value={effectiveSelectedVersionId ? String(effectiveSelectedVersionId) : ""}
              onChange={(e) => setSelectedVersionId(Number(e.target.value))}
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version_number} ({STATE_LABEL_JA[versionState(v)]})
                </option>
              ))}
            </Select>
          )}
        </div>
      </div>

      <div className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900 dark:border-blue-800 dark:bg-blue-950/20 dark:text-blue-100">
        シミュレーションのみ: 候補コードはpinされたsnapshotに対して隔離されたnetwork-offの
        sandbox内で生成・Replayされます。ここには本番同等のものは何もなく、自動的に採用・
        マージ・デプロイされることもありません -- promoteはレビュー済みのpatchを
        Experiments作成フローへ渡すだけです。
      </div>

      {!canRun && componentId && <ApprovalPanel componentId={componentId} />}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card data-testid="candidate-studio-conversation">
          <CardHeader>
            <CardTitle className="text-sm">会話</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="max-h-96 space-y-2 overflow-y-auto">
              {session.messages.map((m) => (
                <div
                  key={m.id}
                  className={cn(
                    "rounded-md border p-2 text-xs",
                    m.role === "user" ? "bg-secondary/40" : "bg-muted/30",
                  )}
                >
                  <div className="mb-1 text-muted-foreground">
                    {m.role === "user" ? "あなた" : "Assistant"}
                    {m.version_id != null && (
                      <span>
                        {" "}
                        ·{" "}
                        {(() => {
                          const v = versions.find((vv) => vv.id === m.version_id);
                          return v ? `v${v.version_number}` : `v#${m.version_id}`;
                        })()}
                      </span>
                    )}
                  </div>
                  <div className="whitespace-pre-wrap break-words">{m.content}</div>
                </div>
              ))}
            </div>
            <Textarea
              value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
              rows={3}
              placeholder="改善の目的・制約・修正指示を入力..."
            />
            {/* Issue #267 item 4: "送信" only appends a chat message and never
                creates a CandidateVersion -- only a successful generate call
                does that (CLAUDE.md Issue #252 section). */}
            <p className="text-xs text-muted-foreground" data-testid="candidate-studio-send-vs-generate-note">
              「送信」は会話に追加するだけで、候補versionは作成しません。候補versionを
              作るには「候補を生成」を使ってください。
            </p>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={handleSend}
                disabled={!draftText.trim() || sendMessage.isPending}
              >
                {sendMessage.isPending ? "送信中..." : "送信"}
              </Button>
              <PrimaryAction
                state={state}
                canRun={canRun}
                draftText={draftText}
                generatePending={generate.isPending}
                replayPending={replay.isPending}
                promotePending={promote.isPending}
                onGenerate={() => handleGenerate(null)}
                onRegenerate={() => handleGenerate(selectedVersion?.id ?? null)}
                onReplay={handleReplay}
                onPromote={handlePromote}
              />
            </div>
          </CardContent>
        </Card>

        <Card data-testid="candidate-studio-version-panel">
          <CardHeader>
            <CardTitle className="text-sm">
              候補version {selectedVersion ? `v${selectedVersion.version_number}` : ""}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {!selectedVersion ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                生成された候補はまだありません。左側で指示を入力し「候補を生成」してください。
              </p>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-1">
                  {selectedVersion.is_mock && <Badge variant="warning">mock LLM</Badge>}
                  <Badge variant="outline">{selectedVersion.decision_method ?? "reasoning_llm"}</Badge>
                  {selectedVersion.provider && (
                    <Badge variant="outline">
                      {selectedVersion.provider}/{selectedVersion.model}
                    </Badge>
                  )}
                  <Badge variant="outline">replay: {selectedVersion.replay_status}</Badge>
                </div>
                {selectedVersion.summary && (
                  <p className="text-xs text-muted-foreground">{selectedVersion.summary}</p>
                )}
                <Tabs defaultValue="diff">
                  <TabsList>
                    <TabsTrigger value="diff">差分</TabsTrigger>
                    <TabsTrigger value="code">全コード</TabsTrigger>
                    <TabsTrigger value="eval">評価結果</TabsTrigger>
                  </TabsList>
                  <TabsContent value="diff">
                    <CodeBlock lang="diff">{selectedVersion.patch_text || "(patchなし)"}</CodeBlock>
                    <VersionNotes version={selectedVersion} />
                  </TabsContent>
                  <TabsContent value="code">
                    <CodeBlock lang="python">
                      {selectedVersion.generated_code || "(生成コードなし)"}
                    </CodeBlock>
                  </TabsContent>
                  <TabsContent value="eval">
                    <EvaluationTab
                      version={selectedVersion}
                      componentId={session.component_id}
                      onRequestFix={(caseResult) => {
                        const failure = caseResult.candidate_error ?? caseResult.case_status;
                        setDraftText(
                          `Trace ${caseResult.trace_id} の評価結果（${failure}）を修正してください。`,
                        );
                        toast.info("修正指示を会話入力欄へ追加しました");
                      }}
                    />
                  </TabsContent>
                </Tabs>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function VersionNotes({ version }: { version: CandidateVersionOut }) {
  if (
    version.assumptions.length === 0 &&
    version.risks.length === 0 &&
    version.suggested_tests.length === 0
  ) {
    return null;
  }
  return (
    <div className="mt-3 grid gap-3 text-xs md:grid-cols-3">
      {version.assumptions.length > 0 && (
        <div>
          <p className="mb-1 font-medium">前提</p>
          <ul className="list-disc space-y-0.5 pl-4">
            {version.assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>
      )}
      {version.risks.length > 0 && (
        <div>
          <p className="mb-1 font-medium">リスク</p>
          <ul className="list-disc space-y-0.5 pl-4">
            {version.risks.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {version.suggested_tests.length > 0 && (
        <div>
          <p className="mb-1 font-medium">推奨テスト</p>
          <ul className="list-disc space-y-0.5 pl-4">
            {version.suggested_tests.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function EvaluationTab({
  version,
  componentId,
  onRequestFix,
}: {
  version: CandidateVersionOut;
  componentId: string;
  onRequestFix: (caseResult: ReplayVariantCaseResultOut) => void;
}) {
  const { data: run } = useReplayVariantRun(version.replay_run_id);
  const { data: traces } = useTraces(componentId, 500);
  const recordedByTraceId = useMemo(() => {
    const map = new Map<string, TraceEvent>();
    (traces ?? []).forEach((t) => map.set(t.trace_id, t));
    return map;
  }, [traces]);

  if (version.replay_status === "not_run") {
    return (
      <p className="py-4 text-sm text-muted-foreground">
        まだ Replay で確認していません。「Replayで確認」を実行してください。
      </p>
    );
  }
  if (version.replay_status === "running") {
    return <Skeleton className="h-32 w-full" />;
  }
  return (
    <ResultMatrix
      run={run}
      recordedByTraceId={recordedByTraceId}
      traceHref={(traceId) =>
        `/components?component=${encodeURIComponent(componentId)}&trace=${encodeURIComponent(traceId)}`
      }
      onRequestFix={onRequestFix}
    />
  );
}

function PrimaryAction({
  state,
  canRun,
  draftText,
  generatePending,
  replayPending,
  promotePending,
  onGenerate,
  onRegenerate,
  onReplay,
  onPromote,
}: {
  state: StudioState;
  canRun: boolean;
  draftText: string;
  generatePending: boolean;
  replayPending: boolean;
  promotePending: boolean;
  onGenerate: () => void;
  onRegenerate: () => void;
  onReplay: () => void;
  onPromote: () => void;
}) {
  if (state === "no_version") {
    return (
      <Button onClick={onGenerate} disabled={!draftText.trim() || generatePending}>
        {generatePending ? "生成中..." : "候補を生成"}
      </Button>
    );
  }
  if (state === "generated") {
    return (
      <Button onClick={onReplay} disabled={!canRun || replayPending} title={!canRun ? "このcomponentのReplayはまだ承認されていません" : undefined}>
        {replayPending ? "実行中..." : "Replayで確認"}
      </Button>
    );
  }
  if (state === "replaying") {
    return <Button disabled>Replay実行中...</Button>;
  }
  if (state === "evaluation_failed") {
    return (
      <Button onClick={onRegenerate} disabled={!draftText.trim() || generatePending}>
        {generatePending ? "生成中..." : "AIに修正を依頼"}
      </Button>
    );
  }
  if ((state === "evaluated" || state === "promoted") && draftText.trim()) {
    return (
      <Button onClick={onRegenerate} disabled={generatePending}>
        {generatePending ? "生成中..." : "AIに追加修正を依頼"}
      </Button>
    );
  }
  if (state === "evaluated") {
    return (
      <Button onClick={onPromote} disabled={promotePending}>
        {promotePending ? "送信中..." : "Experimentへ送る"}
      </Button>
    );
  }
  // promoted
  return <Badge variant="success">Experimentへ送信済み</Badge>;
}
