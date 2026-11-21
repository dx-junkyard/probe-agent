import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useExperiments, useRunExperiment, useExperimentDecision, useExperimentRecord,
  useCreateExperiment, useSnapshots, useLatestDrafts,
  useWorkspaceProposalDraft, useVariantExperimentPayload,
  useGithubAppStatus, useGithubConnections, useSnapshotPreflight,
} from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import { copyText } from "@/lib/clipboard";
import { Play, Download, Plus, Trash2, GitPullRequest, Crosshair, Bot } from "lucide-react";
import type { ExperimentOut } from "@/api/types";
import { AddToWorkspaceButton } from "@/components/add-to-workspace";
import { ContextHeader } from "@/components/layout/context-header";
import { SnapshotPreflightPanel } from "@/components/snapshot-preflight";
import { ImprovementLoopRail } from "@/components/improvement-loop/rail";
import { FormField } from "@/components/ui/form-field";
import { useAuth } from "@/api/auth";
import { useUnsavedChangesGuard } from "@/lib/ui-draft";
import {
  checkVariants, describeMissing, VARIANT_FIELD_LABEL, MIN_EXPERIMENT_VARIANTS, MAX_EXPERIMENT_VARIANTS,
} from "@/lib/experiment-variants";
import { describeRequestFailure } from "@/lib/request-state";

const STATUS_VARIANT: Record<string, "default" | "success" | "destructive" | "secondary" | "warning"> = {
  draft: "secondary",
  running: "warning",
  completed: "success",
  failed: "destructive",
};

const DECISION_OPTS = ["undecided", "adopted", "rejected", "needs_more_data"];
const DECISION_LABELS: Record<string, string> = {
  undecided: "未決定",
  adopted: "採用",
  rejected: "不採用",
  needs_more_data: "追加データが必要",
};

interface VariantInput {
  label: string;
  patch_text: string;
  risk_note: string;
}

const emptyVariant = (): VariantInput => ({ label: "", patch_text: "", risk_note: "" });

// UX 監査 2026-11-17 D7: 操作の失敗を toast へ。String(e) は出さず、サーバーの
// 理由 (detail) と、構造化された拒否コード・次の操作があれば添える。
// `ApiError` を instanceof で判定しない (テストは client をモックする) ので、
// 形 (denialCode / nextAction) だけを読む。
function actionFailureMessage(action: string, e: unknown): string {
  const failure = describeRequestFailure(e);
  const structured = (e ?? {}) as { denialCode?: unknown; nextAction?: unknown };
  const parts: string[] = [];
  if (failure.detail) parts.push(`${action}できませんでした: ${failure.detail}`);
  else if (failure.kind === "offline" || failure.kind === "network" || failure.kind === "timeout") {
    parts.push(`${action}できませんでした。サーバーに接続できているか確認して、もう一度お試しください。`);
  } else if (failure.kind === "forbidden") {
    parts.push(`${action}できませんでした。この操作を行う権限がありません。`);
  } else {
    parts.push(`${action}できませんでした。もう一度お試しください。`);
  }
  if (typeof structured.denialCode === "string" && structured.denialCode) {
    parts.push(`(拒否コード: ${structured.denialCode})`);
  }
  if (typeof structured.nextAction === "string" && structured.nextAction) {
    parts.push(`次の操作: ${structured.nextAction}`);
  }
  return parts.join(" ");
}

// GitHub publish が使えるか。使えないときは理由を 1 つに決めて表示する
// (UX 監査 C5: リンクを黙って消さない)。
type GithubPublishAvailability = "available" | "app_unconfigured" | "no_connection" | "unknown" | "loading";

export default function ExperimentsPage() {
  const { systemId, systems } = useAuth();
  const [searchParams] = useSearchParams();
  const draftIdParam = searchParams.get("draft");
  const workspaceIdParam = searchParams.get("workspace");
  const capabilityContext = searchParams.get("capability");
  const draftId = draftIdParam && Number.isInteger(Number(draftIdParam)) ? Number(draftIdParam) : null;
  const { data: workspaceDraft } = useWorkspaceProposalDraft(draftId);

  // Escalation from the Simulation Workbench (Issue #242 Phase D / #246):
  // ?replay_run_id=&replay_variant_id= mirrors the ?draft= mechanism above --
  // the id round-trips through the URL and this page fetches the full patch
  // payload itself, so a page refresh still resolves the prefill.
  const replayRunIdParam = searchParams.get("replay_run_id");
  const replayVariantIdParam = searchParams.get("replay_variant_id");
  const replayRunId = replayRunIdParam && Number.isInteger(Number(replayRunIdParam))
    ? Number(replayRunIdParam) : null;
  const replayVariantId = replayVariantIdParam && Number.isInteger(Number(replayVariantIdParam))
    ? Number(replayVariantIdParam) : null;
  const { data: replayPayload } = useVariantExperimentPayload(replayRunId, replayVariantId);

  // Deep link from the Overview's 「採否を記録する」 CTA (Issue #383):
  // ?experiment=<id> selects and expands that row. The CTA carries the id
  // because a count alone drops the developer on a list and makes them find
  // the row again -- and because a reload has to land on the same one.
  const experimentIdParam = searchParams.get("experiment");
  const focusExperimentId =
    experimentIdParam && Number.isInteger(Number(experimentIdParam))
      ? Number(experimentIdParam)
      : null;

  // "Create Experiment from this trace" (Components Traces tab row action):
  // prefill context only, never a patch -- the developer fills in the rest.
  const fromTraceParam = searchParams.get("from_trace");
  const fromComponentParam = searchParams.get("from_component");
  const { data: experiments, isLoading, isError, error, refetch } = useExperiments();
  const runExperiment = useRunExperiment();
  const makeDecision = useExperimentDecision();
  const createExperiment = useCreateExperiment();
  // Issue #259: connects "decision saved" to the GitHub publish workflow
  // (Issue #216). Same availability rule as the Probe Planner apply-success
  // next-action -- App configured AND at least one connected repo -- so the
  // developer is never handed a link that leads nowhere.
  const { data: githubAppStatus, isError: githubAppStatusError } = useGithubAppStatus();
  const { data: githubConnections, isError: githubConnectionsError } = useGithubConnections();
  const githubPublish: GithubPublishAvailability =
    githubAppStatus && !githubAppStatus.configured
      ? "app_unconfigured"
      : githubAppStatus && githubConnections
        ? (githubConnections.some(c => c.status === "connected") ? "available" : "no_connection")
        : githubAppStatusError || githubConnectionsError
          ? "unknown"
          : "loading";
  const { data: snapshots } = useSnapshots();
  const { data: drafts } = useLatestDrafts();
  // The URL-driven selection and the developer's own expansion are kept as two
  // separate states. `expandedId` is only ever set by a click; the URL target
  // is validated against the loaded list and applied until the developer
  // touches a row.
  //
  // Validation matters because the CTA's id is a snapshot of the moment the
  // Overview was rendered. By the time the link is opened — a saved URL, a
  // second tab, a decision recorded in between — the experiment may already be
  // adopted / rejected / needs_more_data, or no longer completed. Expanding a
  // settled experiment under a 「採否を記録する」 CTA tells the developer there
  // is a decision to make when there is not.
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [urlTargetDismissed, setUrlTargetDismissed] = useState(false);
  const resolvedUrlTarget =
    focusExperimentId != null && !urlTargetDismissed && experiments
      ? experiments.find(
        (e) =>
          e.id === focusExperimentId &&
          e.status === "completed" &&
          (e.human_decision ?? "undecided") === "undecided",
      )?.id ?? null
      : null;
  // A row the developer opened wins; otherwise the validated URL target. An
  // unknown id, another System's id, or a settled one resolves to null and the
  // page renders the plain list — it never auto-selects a different row.
  const effectiveExpandedId = expandedId ?? resolvedUrlTarget;
  const [showCreate, setShowCreate] = useState(false);
  const [draftDismissed, setDraftDismissed] = useState(false);

  const [newFeatureId, setNewFeatureId] = useState<string | null>(null);
  const [newObjective, setNewObjective] = useState<string | null>(null);
  const [newSnapshotId, setNewSnapshotId] = useState<string>("");
  const [variants, setVariants] = useState<VariantInput[] | null>(null);
  // UX-04: 行別エラーは送信を試みた後に出す (入力中の行を最初から赤くしない)。
  const [submitAttempted, setSubmitAttempted] = useState(false);
  // UX-03: 下書きは作成先 System に属する。別 System へ切り替えたら持ち越さない
  // (ヘッダーの System 切替は破棄確認を出した上で切り替える)。
  const [draftSystemId, setDraftSystemId] = useState(systemId);

  // Issue #369: the shared preflight decides both axes for the selected
  // snapshot (or the recommended one when nothing is selected yet). The page
  // never re-derives `ready`/`current` itself.
  const readySnapshots = snapshots?.filter(s => s.status === "ready") ?? [];
  // UX 監査 2026-11-17 C3: Simulation Workbench / Candidate Studio から来た
  // 候補は、その replay run が pin した Snapshot で評価されている。payload の
  // origin.snapshot_id (サーバーが run から返す値) を初期値にし、選び直させない。
  // 一覧に無い (ready でない・別 System) 場合は初期値にせず、その旨を出す。
  const replaySnapshotRaw = replayPayload?.origin?.snapshot_id;
  const replaySnapshotId = typeof replaySnapshotRaw === "number" ? replaySnapshotRaw : null;
  const replaySnapshotSelectable =
    replaySnapshotId !== null && readySnapshots.some(s => s.id === replaySnapshotId);
  const formSnapshotId = newSnapshotId
    || (replaySnapshotSelectable && replaySnapshotId !== null ? String(replaySnapshotId) : "");

  const { data: preflight, isLoading: preflightLoading } = useSnapshotPreflight(
    formSnapshotId ? Number(formSnapshotId) : null,
  );
  const [staleReason, setStaleReason] = useState("");

  // Exactly one recommendation, from the server. Older ready snapshots stay
  // reachable for reproduction runs, but are not offered as equals.
  const recommendedId = preflight?.recommended_snapshot_id ?? null;
  const recommendedSnapshot = readySnapshots.find(s => s.id === recommendedId)
    ?? readySnapshots[0];
  const olderSnapshots = readySnapshots.filter(s => s.id !== recommendedSnapshot?.id);
  // A definitively stale snapshot needs the developer's reason before the
  // experiment can be created (`decision_method: manual`).
  const staleAckMissing =
    !!preflight?.requires_stale_acknowledgement && staleReason.trim().length === 0;
  const features = drafts?.feature_drafts ?? [];
  const draftVariants = workspaceDraft?.draft_type === "experiment_draft"
    ? (workspaceDraft.payload.variant_summaries ?? []).map(summary => ({
      label: summary,
      patch_text: "",
      risk_note: "",
    }))
    : [];
  while (draftVariants.length < MIN_EXPERIMENT_VARIANTS) {
    draftVariants.push(emptyVariant());
  }
  // C3: baseline はサーバーが自動で足すので、引き継いだ候補 1 件だけで作成できる。
  // 空の 2 件目を足さない (足すと「もう 1 件必要」に見える)。
  const replayVariants = replayPayload
    ? [{ label: replayPayload.label, patch_text: replayPayload.patch_text, risk_note: replayPayload.risk_note }]
    : [];
  while (replayVariants.length < MIN_EXPERIMENT_VARIANTS) {
    replayVariants.push(emptyVariant());
  }
  const formFeatureId = newFeatureId
    ?? (workspaceDraft?.draft_type === "experiment_draft" ? workspaceDraft.payload.feature_id ?? "" : "");
  const defaultObjective = workspaceDraft?.draft_type === "experiment_draft"
    ? workspaceDraft.payload.objective ?? ""
    : fromTraceParam
      ? `Trace ${fromTraceParam} を調査する${fromComponentParam ? `（component ${fromComponentParam}）` : ""}`
      : "";
  const formObjective = newObjective ?? defaultObjective;
  const formVariants = variants ?? (replayPayload ? replayVariants : draftVariants);
  const draftOpen = !!workspaceDraft
    && workspaceDraft.draft_type === "experiment_draft"
    && !draftDismissed;
  const replayPrefillOpen = !!replayPayload && !draftDismissed;
  const fromTraceOpen = !!fromTraceParam && !draftDismissed && !workspaceDraft && !replayPayload;

  const resetForm = () => {
    setNewFeatureId(null);
    setNewObjective(null);
    setNewSnapshotId("");
    setVariants(null);
    setStaleReason("");
    setSubmitAttempted(false);
  };

  if (draftSystemId !== systemId) {
    // props 変化に応じた state 調整 (render 中)。effect にすると一度だけ
    // 別 System の下書きを描いてしまう。
    setDraftSystemId(systemId);
    resetForm();
    setShowCreate(false);
  }

  const variantCheck = checkVariants(formVariants);
  // 下書きの有無は「利用者が入力した内容があるか」で決める。URL の prefill を
  // そのまま開いただけなら、同じ URL から再現できるので未保存入力ではない。
  const hasDraft =
    !!(newFeatureId ?? "").trim()
    || !!(newObjective ?? "").trim()
    || !!newSnapshotId
    || !!staleReason.trim()
    || (variants ?? []).some(v => v.label.trim() || v.patch_text.trim() || v.risk_note.trim());
  useUnsavedChangesGuard(`experiment-create:system-${systemId ?? "none"}`, hasDraft);
  // Issue #466 review R3: 送信した下書きと、応答を待つ間に編集された現在の
  // 下書きを区別する。成功時に無条件で初期化すると、送信後の追記 (POST に
  // 含まれず保存もされていない) まで消える。応答時点の下書きは ref で読む
  // (handler の closure は送信時点の値しか持たない)。
  const draftSignature = JSON.stringify([
    systemId, formFeatureId, formObjective, formSnapshotId, staleReason, formVariants,
  ]);
  const draftSignatureRef = useRef(draftSignature);
  useEffect(() => {
    draftSignatureRef.current = draftSignature;
  }, [draftSignature]);
  const dialogOpen = showCreate || draftOpen || replayPrefillOpen || fromTraceOpen;
  const targetSystemName = systems.find(s => s.id === systemId)?.name ?? null;

  const discardDraft = () => {
    if (hasDraft && !window.confirm("入力中の Experiment 下書きを破棄しますか?(元に戻せません)")) return;
    resetForm();
    setShowCreate(false);
    setDraftDismissed(true);
  };

  const handleCreate = async () => {
    if (!formFeatureId || !formObjective.trim() || !formSnapshotId) return;
    setSubmitAttempted(true);
    if (variantCheck.partial.length > 0) {
      toast.error(`${variantCheck.partial.map(describeMissing).join("。")}。入力するか、候補を削除してください。`);
      return;
    }
    const validVariants = variantCheck.complete.map(r => formVariants[r.index]);
    if (validVariants.length < MIN_EXPERIMENT_VARIANTS) {
      toast.error(`ラベルとpatchを入力した比較候補が${MIN_EXPERIMENT_VARIANTS}件以上必要です(baselineは自動で追加されます)`);
      return;
    }
    if (validVariants.length > MAX_EXPERIMENT_VARIANTS) {
      toast.error(`比較候補は${MAX_EXPERIMENT_VARIANTS}件までです`);
      return;
    }
    const submittedSystemId = systemId;
    const submittedSignature = draftSignature;
    try {
      await createExperiment.mutateAsync({
        feature_id: formFeatureId,
        objective: formObjective.trim(),
        snapshot_id: Number(formSnapshotId),
        variants: validVariants.map(v => ({
          label: v.label.trim(),
          patch_text: v.patch_text,
          risk_note: v.risk_note.trim() || undefined,
        })),
        // Issue #369: only sent when the server's preflight says the snapshot
        // is definitively behind HEAD. The server re-checks and rejects a
        // missing reason itself; this is not the gate, only the input to it.
        ...(preflight?.requires_stale_acknowledgement
          ? { stale_snapshot_reason: staleReason.trim() }
          : {}),
      });
      toast.success(
        targetSystemName && submittedSystemId != null
          ? `Experimentを作成しました(${targetSystemName})`
          : "Experimentを作成しました",
      );
      setShowCreate(false);
      setDraftDismissed(true);
      if (draftSignatureRef.current === submittedSignature) {
        resetForm();
      } else {
        // 送信後に編集された内容は保存されていない。下書きとして残し、
        // その旨を伝える (作成済みの Experiment は一覧にある)。
        if (variants == null) setVariants(formVariants);
        toast.info("送信後に編集した内容は保存されていないため、下書きとして残しています。");
      }
    } catch (err) {
      // 失敗時は下書きを残す (再送や修正のため)。
      toast.error(actionFailureMessage("Experimentを作成", err));
    }
  };

  const updateVariant = (idx: number, field: keyof VariantInput, value: string) => {
    setVariants(formVariants.map((v, i) => i === idx ? { ...v, [field]: value } : v));
  };

  const addVariant = () => {
    if (formVariants.length >= MAX_EXPERIMENT_VARIANTS) return;
    setVariants([...formVariants, emptyVariant()]);
  };

  const removeVariant = (idx: number) => {
    if (formVariants.length <= MIN_EXPERIMENT_VARIANTS) return;
    setVariants(formVariants.filter((_, i) => i !== idx));
  };

  const clearVariant = (idx: number) => {
    setVariants(formVariants.map((v, i) => i === idx ? emptyVariant() : v));
  };

  return (
    <div className="space-y-6">
      <ContextHeader />
      {/* Issue #371: same rail as Components / Candidate Studio / Workbench. */}
      <ImprovementLoopRail
        experiment={(experiments ?? []).find(e => e.id === effectiveExpandedId) ?? null}
        componentId={fromComponentParam}
        traceId={fromTraceParam}
        replayRunId={replayRunId}
        replayVariantId={replayVariantId}
      />
      {capabilityContext && (
        <Link
          to={`/capability-map?capability=${encodeURIComponent(capabilityContext)}`}
          className="inline-flex items-center text-xs text-primary hover:underline"
          data-testid="back-to-capability"
        >
          ← Capabilityへ戻る: {capabilityContext}
        </Link>
      )}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Experiments</h1>
        <Button size="sm" data-testid="experiment-create-button" onClick={() => {
          setDraftDismissed(true);
          // UX-03: 保持している下書きがあれば、それを開き直す (空で上書きしない)。
          if (!hasDraft) {
            setNewFeatureId("");
            setNewObjective("");
            setVariants([emptyVariant()]);
          }
          setShowCreate(true);
        }}>
          <Plus className="h-4 w-4 mr-1" />
          {hasDraft ? "下書きを再開" : "Experimentを作成"}
        </Button>
      </div>

      {hasDraft && !dialogOpen && (
        <div
          role="status"
          data-testid="experiment-draft-notice"
          className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm dark:border-amber-800 dark:bg-amber-950/20"
        >
          <span>
            作成途中の Experiment 下書きを保持しています
            {targetSystemName ? `(作成先: ${targetSystemName})` : ""}。保存はまだされていません。
          </span>
          <span className="flex gap-2">
            <Button size="sm" variant="outline" onClick={() => setShowCreate(true)}>下書きを再開</Button>
            <Button size="sm" variant="ghost" onClick={discardDraft}>下書きを破棄</Button>
          </span>
        </div>
      )}

      {isLoading ? (
        <div className="space-y-4">{[1,2].map(i => <Skeleton key={i} className="h-40 w-full" />)}</div>
      ) : isError ? (
        <Card role="alert"><CardContent className="space-y-3 py-8 text-center">
          <p className="text-sm font-medium">Experiment一覧を取得できませんでした。</p>
          <p className="text-xs text-muted-foreground">
            {describeRequestFailure(error).summary}
            {describeRequestFailure(error).detail ? ` ${describeRequestFailure(error).detail}` : ""}
          </p>
          <Button size="sm" variant="outline" onClick={() => refetch()}>再試行</Button>
        </CardContent></Card>
      ) : !experiments?.length ? (
        <Card><CardContent className="space-y-3 py-8 text-center text-sm text-muted-foreground">
          <p>Experimentはまだありません。評価する候補を用意して作成してください。</p>
          <Button size="sm" onClick={() => setShowCreate(true)}>{hasDraft ? "下書きを再開" : "最初のExperimentを作成"}</Button>
        </CardContent></Card>
      ) : (
        <div className="space-y-4">
          {experiments.map(exp => (
            <ExperimentCard
              key={exp.id}
              exp={exp}
              expanded={effectiveExpandedId === exp.id}
              onToggle={() => {
                // Any manual toggle takes over from the URL target, so the
                // linked row can be collapsed like any other.
                setUrlTargetDismissed(true);
                setExpandedId(effectiveExpandedId === exp.id ? null : exp.id);
              }}
              runExperiment={runExperiment}
              makeDecision={makeDecision}
              githubPublish={githubPublish}
              capabilityContext={capabilityContext}
            />
          ))}
        </div>
      )}

      {/* UX-03: 閉じる (背景クリック・Escape・×) と破棄を分ける。閉じても
          下書きは保持し、破棄は「下書きを破棄」ボタンだけが行う。 */}
      <Dialog open={dialogOpen} onOpenChange={(open) => {
        setShowCreate(open);
        if (!open) {
          setDraftDismissed(true);
          // `variants` が null のまま閉じると、URL の prefill が次回も同じ
          // 内容で開く。入力があれば現在の内容を明示的な下書きとして固定する。
          if (hasDraft && variants == null) setVariants(formVariants);
        }
      }}>
        <DialogHeader>
          <DialogTitle>Experimentを作成</DialogTitle>
        </DialogHeader>
        <p className="mb-2 text-xs text-muted-foreground" data-testid="experiment-create-target-system">
          作成先の System: {targetSystemName ? `${targetSystemName}(#${systemId})` : "未選択"}
          {" "}— 閉じても入力内容は下書きとして保持されます。
        </p>
        <div className="space-y-4 max-h-[60vh] overflow-y-auto">
          {replayPayload && (
            <div className="rounded-md border bg-secondary/30 px-3 py-2 text-xs">
              Replay候補run #{replayRunId}、候補 #{replayVariantId} ({replayPayload.source}) から入力済みです。
              {replayPayload.risk_note && <span className="ml-2">{replayPayload.risk_note}</span>}
              <span className="ml-1 text-muted-foreground">
                {replaySnapshotSelectable
                  ? `Snapshot #${replaySnapshotId} は replay run から引き継ぎました。残り: Feature、評価目的。`
                  : "残り: Feature、評価目的、Snapshot。"}
              </span>
              {replaySnapshotId !== null && !replaySnapshotSelectable && (
                <span className="mt-1 block text-amber-700 dark:text-amber-400" data-testid="experiment-replay-snapshot-unavailable">
                  replay run の Snapshot #{replaySnapshotId} は選択できる状態(ready)の一覧にありません。下で Snapshot を選んでください。
                </span>
              )}
              <span className="mt-1 block text-muted-foreground">
                baseline(pinned commit)は自動で比較に加わるため、この候補 1 件だけで作成できます。
              </span>
            </div>
          )}
          {!replayPayload && fromTraceParam && (
            <div className="rounded-md border bg-secondary/30 px-3 py-2 text-xs">
              Trace <span className="font-mono">{fromTraceParam}</span> から文脈を入力済みです
              {fromComponentParam && (
                <> (component <span className="font-mono">{fromComponentParam}</span>)</>
              )}。
              <span className="ml-1 text-muted-foreground">残り: Feature、Snapshot、比較候補。</span>
            </div>
          )}
          {workspaceDraft?.draft_type === "experiment_draft" && (
            <div className="rounded-md border bg-secondary/30 px-3 py-2 text-xs">
              Decision Workspaceの提案 #{workspaceDraft.proposal_id} から入力済みです。
              {(workspaceDraft.payload.constraints?.length ?? 0) > 0 && (
                <span className="ml-2">制約: {workspaceDraft.payload.constraints?.join("、")}。</span>
              )}
              {(workspaceDraft.payload.evaluation_criteria?.length ?? 0) > 0 && (
                <span className="ml-2">評価基準: {workspaceDraft.payload.evaluation_criteria?.join("、")}。</span>
              )}
              {workspaceDraft.missing_fields.length > 0 && (
                <span className="ml-1 text-muted-foreground">
                  残り: {workspaceDraft.missing_fields.join(", ")}。
                </span>
              )}
              {workspaceIdParam && (
                <Link className="ml-2 underline" to={`/workspaces?open=${workspaceIdParam}`}>
                  Workspaceへ戻る
                </Link>
              )}
            </div>
          )}
          <FormField label="Feature（評価対象）">
            {(field) => features.length > 0 ? (
              <Select {...field} value={formFeatureId} onChange={e => setNewFeatureId(e.target.value)}>
                <option value="">Featureを選択...</option>
                {features.map(f => <option key={f.feature_id} value={f.feature_id}>{f.feature_id} — {f.name}</option>)}
              </Select>
            ) : (
              <Input {...field} value={formFeatureId} onChange={e => setNewFeatureId(e.target.value)} placeholder="feature-id" />
            )}
          </FormField>
          <FormField label="評価目的">
            {(field) => (
              <Textarea {...field} value={formObjective} onChange={e => setNewObjective(e.target.value)} placeholder="この比較で確認したいこと" rows={2} />
            )}
          </FormField>
          {/* Issue #369: the selector no longer presents every "ready"
              snapshot as an equal option. Exactly one is the recommendation;
              the rest are disclosed as reproduction-only choices, and the
              shared preflight below states both axes for whichever is
              selected. */}
          <div className="space-y-2">
            <Label htmlFor="experiment-create-snapshot">Snapshot</Label>
            <Select id="experiment-create-snapshot" value={formSnapshotId} onChange={e => setNewSnapshotId(e.target.value)}>
              <option value="">Snapshotを選択...</option>
              {recommendedSnapshot && (
                <option value={recommendedSnapshot.id}>
                  #{recommendedSnapshot.id} — {recommendedSnapshot.commit_sha?.slice(0, 8)}（推奨）
                </option>
              )}
              {olderSnapshots.length > 0 && (
                <optgroup label="過去のSnapshot（再現用途）">
                  {olderSnapshots.map(s => (
                    <option key={s.id} value={s.id}>
                      #{s.id} — {s.commit_sha?.slice(0, 8)}（{s.file_count} ファイル）
                    </option>
                  ))}
                </optgroup>
              )}
            </Select>
            <SnapshotPreflightPanel
              preflight={preflight}
              isLoading={preflightLoading}
              staleReason={staleReason}
              onStaleReasonChange={setStaleReason}
            />
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label>比較候補</Label>
              <Button variant="ghost" size="sm" onClick={addVariant} disabled={formVariants.length >= MAX_EXPERIMENT_VARIANTS}>
                <Plus className="h-3 w-3 mr-1" /> 候補を追加
              </Button>
            </div>
            <p className="text-xs text-muted-foreground" data-testid="experiment-variant-baseline-note">
              baseline(pinned commit のまま)は自動で追加されます。候補は{MIN_EXPERIMENT_VARIANTS}〜{MAX_EXPERIMENT_VARIANTS}件です。
            </p>
            {formVariants.map((v, i) => {
              const row = variantCheck.rows[i];
              const showRowError = submitAttempted && row?.state === "partial";
              const errorId = `experiment-variant-${i}-error`;
              const invalid = (f: "label" | "patch_text") =>
                showRowError && row.missing.includes(f) ? { "aria-invalid": true as const, "aria-describedby": errorId } : {};
              return (
                <div
                  key={i}
                  className={`rounded-lg border p-3 space-y-2 ${showRowError ? "border-destructive" : ""}`}
                  data-testid={`experiment-variant-row-${i}`}
                  data-row-state={row?.state}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-muted-foreground">
                      候補 {i + 1}
                      {row?.state === "partial" && <span className="ml-2 text-amber-700 dark:text-amber-400">入力途中</span>}
                    </span>
                    {formVariants.length > MIN_EXPERIMENT_VARIANTS ? (
                      <Button variant="ghost" size="icon" className="h-6 w-6" aria-label={`候補 ${i + 1} を削除`} onClick={() => removeVariant(i)}>
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    ) : row?.state === "partial" ? (
                      <Button variant="ghost" size="sm" className="h-6 text-xs" onClick={() => clearVariant(i)}>
                        入力をクリア
                      </Button>
                    ) : null}
                  </div>
                  <Input
                    aria-label={`候補 ${i + 1} の${VARIANT_FIELD_LABEL.label}`}
                    placeholder="ラベル（例: optimized-v1）"
                    value={v.label}
                    onChange={e => updateVariant(i, "label", e.target.value)}
                    {...invalid("label")}
                  />
                  <Textarea
                    aria-label={`候補 ${i + 1} の${VARIANT_FIELD_LABEL.patch_text}`}
                    placeholder="Patch内容（unified diff形式）"
                    value={v.patch_text}
                    onChange={e => updateVariant(i, "patch_text", e.target.value)}
                    rows={4}
                    className="font-mono text-xs"
                    {...invalid("patch_text")}
                  />
                  <Input
                    aria-label={`候補 ${i + 1} のリスクメモ`}
                    placeholder="リスクメモ（任意）"
                    value={v.risk_note}
                    onChange={e => updateVariant(i, "risk_note", e.target.value)}
                  />
                  {showRowError && (
                    <p id={errorId} role="alert" className="text-xs text-destructive" data-testid={`experiment-variant-error-${i}`}>
                      {describeMissing(row)}。入力するか、{formVariants.length > MIN_EXPERIMENT_VARIANTS ? "この候補を削除" : "入力をクリア"}してください。
                    </p>
                  )}
                </div>
              );
            })}
          </div>
          {variantCheck.partial.length > 0 && !submitAttempted && (
            <p className="text-xs text-amber-700 dark:text-amber-400" data-testid="experiment-variant-partial-hint">
              入力途中の候補があります({variantCheck.partial.map(r => `候補 ${r.index + 1}`).join("、")})。
              作成するには入力を完了するか、削除してください。
            </p>
          )}
          <Button
            onClick={handleCreate}
            disabled={createExperiment.isPending || !formFeatureId || !formObjective.trim() || !formSnapshotId || variantCheck.complete.length < MIN_EXPERIMENT_VARIANTS || staleAckMissing || preflight?.verdict === "blocked"}
            title={
              staleAckMissing
                ? "HEADより古いSnapshotを使う理由を入力してください"
                : preflight?.verdict === "blocked"
                  ? "このSnapshotでは開始できません — 上の確認項目を解消してください"
                  : undefined
            }
            className="w-full"
          >
            {createExperiment.isPending ? "作成中..." : "Experimentを作成"}
          </Button>
          {hasDraft && (
            <Button variant="ghost" size="sm" className="w-full" onClick={discardDraft} data-testid="experiment-discard-draft">
              下書きを破棄
            </Button>
          )}
        </div>
      </Dialog>
    </div>
  );
}

function ExperimentCard({
  exp, expanded, onToggle, runExperiment, makeDecision, githubPublish, capabilityContext,
}: {
  exp: ExperimentOut;
  expanded: boolean;
  onToggle: () => void;
  runExperiment: ReturnType<typeof useRunExperiment>;
  makeDecision: ReturnType<typeof useExperimentDecision>;
  githubPublish: GithubPublishAvailability;
  capabilityContext: string | null;
}) {
  const [decisionVal, setDecisionVal] = useState(exp.human_decision ?? "undecided");
  const [decisionVariant, setDecisionVariant] = useState(exp.human_decision_variant_key ?? "");
  const [decisionNote, setDecisionNote] = useState(exp.human_decision_note ?? "");

  const nonBaselineVariants = exp.variants?.filter(v => !v.is_baseline && v.status === "completed") ?? [];

  const handleDecision = async () => {
    const payload: { id: number; decision: string; variant_key?: string; note?: string } = {
      id: exp.id,
      decision: decisionVal,
    };
    if (decisionVal === "adopted") {
      if (!decisionVariant) {
        toast.error("採用する候補を選択してください");
        return;
      }
      if (!decisionNote.trim()) {
        toast.error("採用するには判断理由の入力が必要です");
        return;
      }
      payload.variant_key = decisionVariant;
    }
    if (decisionNote.trim()) {
      payload.note = decisionNote.trim();
    }
    try {
      await makeDecision.mutateAsync(payload);
      toast.success("判断を保存しました");
    } catch (err) { toast.error(actionFailureMessage("判断を保存", err)); }
  };

  // D7: 実行できる状態はサーバーの run_allowed (run endpoint と同じ条件) が決める。
  // そのうち画面から出すのは draft (実行) と failed (再実行) だけ。completed の
  // 再実行はサーバーが許すが、判断の根拠になっている結果を上書きするので出さない。
  // 古い Control Server (run_allowed なし) では従来どおり draft だけ。
  const serverAllowsRun = exp.run_allowed ?? exp.status === "draft";
  const runLabel = exp.status === "failed" ? "再実行" : "Experiment を実行";
  const showRun = serverAllowsRun && (exp.status === "draft" || exp.status === "failed");
  const runningThis = runExperiment.isPending && runExperiment.variables === exp.id;

  return (
    <Card data-testid={`experiment-row-${exp.id}`} data-expanded={expanded}>
      <CardHeader className="cursor-pointer" onClick={onToggle}>
        <div className="flex items-start justify-between">
          <div>
            <CardTitle className="text-sm flex items-center gap-2">
              Experiment #{exp.id}
              <Badge variant={STATUS_VARIANT[exp.status] ?? "secondary"}>{exp.status}</Badge>
              {exp.human_decision && exp.human_decision !== "undecided" && (
                <Badge variant="outline">{DECISION_LABELS[exp.human_decision] ?? exp.human_decision}</Badge>
              )}
            </CardTitle>
            <CardDescription className="mt-1">
              {exp.feature_id} — {exp.objective}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-muted-foreground">{formatTimestamp(exp.created_at)}</span>
            <div onClick={e => e.stopPropagation()}>
              <AddToWorkspaceButton itemType="experiment" itemId={String(exp.id)} label={`Experiment #${exp.id}: ${exp.feature_id}`} />
            </div>
          </div>
        </div>
      </CardHeader>
      {expanded && (
        <CardContent className="space-y-4" data-testid={`experiment-detail-${exp.id}`}>
          <div className="flex gap-2">
            {showRun && (
              <Button
                size="sm"
                data-testid={`experiment-run-${exp.id}`}
                onClick={() => runExperiment.mutateAsync(exp.id)
                  .then((result) => {
                    if (result?.status === "failed") toast.error("Experiment の実行が失敗しました。下の失敗の記録を確認してください。");
                    else toast.success("Experiment の実行が終わりました");
                  })
                  .catch(e => toast.error(actionFailureMessage(exp.status === "failed" ? "再実行" : "実行", e)))}
                disabled={runExperiment.isPending}
              >
                <Play className="h-4 w-4 mr-1" />
                {runningThis ? "実行中..." : runLabel}
              </Button>
            )}
            {exp.status === "running" && (
              <p className="text-xs text-muted-foreground" role="status" data-testid={`experiment-running-${exp.id}`}>
                実行中です。終わると自動で表示を更新します。
              </p>
            )}
          </div>

          {exp.error && (
            <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs" data-testid={`experiment-error-${exp.id}`}>
              <div className="font-medium text-destructive">実行の失敗</div>
              <p className="mt-1 whitespace-pre-wrap">{exp.error}</p>
            </div>
          )}

          {exp.status === "completed" && (
            <div className="rounded-lg border p-4 space-y-3">
              <h4 className="text-sm font-medium">判断</h4>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>判定</Label>
                  <Select value={decisionVal} onChange={e => setDecisionVal(e.target.value)}>
                    {DECISION_OPTS.map(d => <option key={d} value={d}>{DECISION_LABELS[d]}</option>)}
                  </Select>
                </div>
                {decisionVal === "adopted" && (
                  <div className="space-y-2">
                    <Label>採用する候補 *</Label>
                    <Select value={decisionVariant} onChange={e => setDecisionVariant(e.target.value)}>
                      <option value="">候補を選択...</option>
                      {nonBaselineVariants.map(v => (
                        <option key={v.variant_key} value={v.variant_key}>{v.label} ({v.variant_key})</option>
                      ))}
                    </Select>
                  </div>
                )}
              </div>
              <div className="space-y-2">
                <Label>判断理由 {decisionVal === "adopted" ? "*" : ""}</Label>
                <Textarea
                  value={decisionNote}
                  onChange={e => setDecisionNote(e.target.value)}
                  placeholder="判断理由を入力..."
                  rows={2}
                />
              </div>
              <Button size="sm" onClick={handleDecision} disabled={makeDecision.isPending}>
                {makeDecision.isPending ? "保存中..." : "判断を保存"}
              </Button>
            </div>
          )}

          {/* Issue #259: driven by the experiment's persisted `human_decision`
              (not local save-button state) so this also shows for decisions
              made earlier, on every load -- deterministic and simple. */}
          <ExperimentNextAction
            humanDecision={exp.human_decision}
            githubPublish={githubPublish}
            adoptedPatchId={exp.adopted_improvement_patch_id ?? null}
            capabilityContext={capabilityContext}
          />

          {exp.variants?.length > 0 && (
            <div>
              <h4 className="text-sm font-medium mb-2">比較候補</h4>
              <div className="space-y-3">
                {exp.variants.map(v => (
                  <div key={v.id} className="rounded-lg border p-3">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{v.label}</span>
                        <span className="text-xs text-muted-foreground font-mono">{v.variant_key}</span>
                        {v.is_baseline && <Badge variant="outline">baseline</Badge>}
                        <Badge variant={v.status === "completed" ? "success" : v.status === "failed" ? "destructive" : "secondary"}>
                          {v.status}
                        </Badge>
                      </div>
                      {v.patch_text && (
                        <Button
                          size="sm" variant="ghost"
                          onClick={() => {
                            const blob = new Blob([v.patch_text!], { type: "text/plain" });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement("a"); a.href = url; a.download = `variant-${v.variant_key}.diff`; a.click();
                            URL.revokeObjectURL(url);
                          }}
                        >
                          <Download className="h-3 w-3 mr-1" /> 差分をダウンロード
                        </Button>
                      )}
                    </div>
                    {v.risk_note && <p className="text-xs text-muted-foreground mb-1">{v.risk_note}</p>}
                    {v.error && <p className="text-xs text-destructive mb-2" data-testid={`experiment-variant-failure-${v.variant_key}`}>{v.error}</p>}
                    <FailedCommands variant={v} />
                    {v.metrics && Object.keys(v.metrics).length > 0 && (
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b">
                              <th className="pb-1 text-left font-medium text-muted-foreground">指標</th>
                              <th className="pb-1 text-right font-medium text-muted-foreground">値</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(v.metrics).map(([k, val]) => (
                              <tr key={k} className="border-b last:border-0">
                                <td className="py-1">{k}</td>
                                <td className="py-1 text-right font-mono">{String(val)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <ExperimentRecordBlock id={exp.id} />

          {exp.comparison && Object.keys(exp.comparison).length > 0 && (
            <div>
              <h4 className="text-sm font-medium mb-2">比較結果</h4>
              <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
                {JSON.stringify(exp.comparison, null, 2)}
              </pre>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}

// IK-0035: 台帳（疑義台帳の外部検証の線）に写す記録。サーバーが決定論的に組み立てた
// JSON をそのまま表示・コピーする（ここでは整形しない）。
function ExperimentRecordBlock({ id }: { id: number }) {
  const { data, isError, error } = useExperimentRecord(id);
  const json = data ? JSON.stringify(data, null, 2) : "";
  const copy = async () => {
    if (await copyText(json)) toast.success("記録を JSON でコピーしました");
    else toast.error("コピーできませんでした（ブラウザの権限を確認してください）");
  };
  return (
    <div data-testid={`experiment-record-${id}`}>
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-sm font-medium flex items-center gap-2">
          台帳用の記録
          {data && !data.complete && <Badge variant="warning">未完（解釈か判断がまだ）</Badge>}
        </h4>
        <Button size="sm" variant="ghost" onClick={copy} disabled={!data}>
          JSON をコピー
        </Button>
      </div>
      {isError && (
        <p className="text-xs text-destructive">
          {describeRequestFailure(error).summary}
          {describeRequestFailure(error).detail ? ` ${describeRequestFailure(error).detail}` : ""}
        </p>
      )}
      {data && (
        <pre className="rounded-md bg-muted p-3 text-xs overflow-x-auto whitespace-pre-wrap">
          {`${data.conditions}\n\n${data.result_summary}`}
        </pre>
      )}
      {/* IK-0036: 助言は判断と別の枠に出す（台帳の結果の文には入らない）。 */}
      {data && (
        <pre className="mt-2 rounded-md border p-3 text-xs text-muted-foreground overflow-x-auto whitespace-pre-wrap">
          {data.analysis_summary}
        </pre>
      )}
    </div>
  );
}

// Issue #259: closes the "decision saved -> dead end" gap. `Candidate Studio`
// never gets a `component_id` prefill here -- an Experiment record (and its
// variants) carries only `feature_id`, never a `component_id`, in any typed
// field, so there is nothing deterministic to prefill (Principle 6); linking
// bare to /candidate-studio lets the developer pick the component there.
function ExperimentNextAction({ humanDecision, githubPublish, adoptedPatchId, capabilityContext }: {
  humanDecision: string | null;
  githubPublish: GithubPublishAvailability;
  adoptedPatchId: number | null;
  capabilityContext: string | null;
}) {
  if (humanDecision === "adopted") {
    const probePlannerHref = capabilityContext
      ? `/probe-planner?capability=${encodeURIComponent(capabilityContext)}`
      : "/probe-planner";
    // UX 監査 2026-11-17 C5: 採用した候補の patch を `?patch=` で渡す。GitHub
    // 画面はこの param で Publish Jobs タブを開き、その patch を選んだ作成
    // ダイアログを開く (開くだけ。承認と publish は従来どおり人が行う)。
    // patch id はサーバーが採用の lineage から決めた値で、ここで推測しない。
    const publishHref = adoptedPatchId !== null ? `/github?patch=${adoptedPatchId}` : "/github";
    return (
      <div className="rounded-lg border p-3 space-y-2 text-xs" data-testid="experiment-next-action-adopted">
        <div className="text-sm font-medium">次のステップ</div>
        {githubPublish === "available" && (
          <Link
            to={publishHref}
            className="flex items-center gap-1.5 text-primary hover:underline"
            data-testid="experiment-github-publish-link"
          >
            <GitPullRequest className="h-3.5 w-3.5" />
            {adoptedPatchId !== null
              ? `採用した変更(patch #${adoptedPatchId})をGitHubで公開する`
              : "採用した変更をGitHubで公開する"}
          </Link>
        )}
        {githubPublish === "available" && adoptedPatchId === null && (
          <p className="text-muted-foreground" data-testid="experiment-github-publish-patch-unknown">
            公開用の patch を特定できなかったため、GitHub 画面で patch を選んでください。
          </p>
        )}
        {(githubPublish === "app_unconfigured" || githubPublish === "no_connection" || githubPublish === "unknown") && (
          <p className="text-muted-foreground" data-testid="experiment-github-publish-unavailable">
            {githubPublish === "app_unconfigured"
              ? "GitHub App が未設定のため、まだ公開できません。"
              : githubPublish === "no_connection"
                ? "接続済み(connected)のリポジトリが無いため、まだ公開できません。"
                : "GitHub の設定状態を確認できなかったため、公開できるか分かりません。"}
            {" "}
            <Link to="/github" className="text-primary hover:underline">GitHub 画面で設定する</Link>
          </p>
        )}
        <Link
          to={probePlannerHref}
          className="flex items-center gap-1.5 text-primary hover:underline"
          data-testid="experiment-probe-planner-link"
        >
          <Crosshair className="h-3.5 w-3.5" /> Probe Plannerで次の観測サイクルを始める
        </Link>
      </div>
    );
  }

  if (humanDecision === "rejected" || humanDecision === "needs_more_data") {
    return (
      <div className="rounded-lg border p-3 space-y-2 text-xs" data-testid="experiment-next-action-candidate-studio">
        <div className="text-sm font-medium">次のステップ</div>
        <Link
          to="/candidate-studio"
          className="flex items-center gap-1.5 text-primary hover:underline"
          data-testid="experiment-candidate-studio-link"
        >
          <Bot className="h-3.5 w-3.5" /> Candidate StudioでAI候補を試す
        </Link>
      </div>
    );
  }

  return null;
}

// D7: 失敗した候補で、終了コードが 0 以外・timeout のコマンドだけを出す。
// 出力は保存済みの値 (サーバーが切り詰め済みなら truncated を明示) をそのまま。
function FailedCommands({ variant }: { variant: ExperimentOut["variants"][number] }) {
  if (variant.status !== "failed") return null;
  const failed = (variant.commands ?? []).filter(c => c.timed_out || (c.exit_code !== null && c.exit_code !== 0));
  if (failed.length === 0) return null;
  return (
    <details className="mb-2 text-xs" data-testid={`experiment-variant-failed-commands-${variant.variant_key}`}>
      <summary className="cursor-pointer text-destructive">失敗したコマンド({failed.length}件)</summary>
      <div className="mt-2 space-y-2">
        {failed.map(c => (
          <div key={c.id} className="rounded border p-2">
            <div className="font-mono">{c.command}</div>
            <div className="text-muted-foreground">
              {c.timed_out ? "時間切れ" : `終了コード ${c.exit_code}`}
            </div>
            {c.stderr && (
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-muted p-2">
                {c.stderr}{c.stderr_truncated ? "\n…(切り詰め済み)" : ""}
              </pre>
            )}
            {c.stdout && (
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-muted p-2">
                {c.stdout}{c.stdout_truncated ? "\n…(切り詰め済み)" : ""}
              </pre>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
