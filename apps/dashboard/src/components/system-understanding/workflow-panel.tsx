// Issue #349: 状態駆動ワークフローの現在地 (R1) と例外表示 (R5)。
//
// docs/system-interview-workflow-ux.md の実装。表示状態・主操作・例外は
// すべてサーバー (GET /interview/workflow-state) が決定した値であり、
// このファイルは「決まったものを描く」だけである。クライアント限定状態
// (タブ選択・mutation の isPending) から状態を導出してはならない (原則 P9)。
//
// 情報役割 (§3.1):
//   R1 現在地      = WorkflowLocationCard (1画面に1箇所)
//   R5 例外        = WorkflowExceptions (ブロッキング / 劣化のみ)
//   R2 の分岐      = BackRequestNotice (E8 は情報区分なので主作業カード内)

import { AlertTriangle, Info, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type {
  InterviewProcessKind,
  InterviewWorkflowExceptionOut,
  InterviewWorkflowPrimaryAction,
  InterviewWorkflowState,
  InterviewWorkflowStateOut,
  InterviewWorkflowTerminalKind,
} from "@/api/types";

/** 開発者向けの状態名 (§2.1)。内部ステージ名は露出しない。 */
export const WORKFLOW_STATE_LABELS: Record<InterviewWorkflowState, string> = {
  "W0-A": "開始の前提を満たす",
  "W0-B": "インタビューを始める",
  W1: "システムが調べている",
  W2: "理解の要点を確認する",
  W3: "判断が必要な質問に答える",
  W4: "意図とのズレを確認する",
  W5: "提案を承認する",
  W6: "差分を確認する",
  W7: "結果と残りを確認する",
};

/** 各状態で開発者が達成すること (§2.1 の一文)。 */
export const WORKFLOW_STATE_GOALS: Record<InterviewWorkflowState, string> = {
  "W0-A": "対象リポジトリの snapshot を用意します。",
  "W0-B": "このリポジトリでインタビューを開始します。",
  W1: "システムの処理が終わるのを待ちます。操作は不要です。",
  W2: "AI が読み取ったシステム理解が今回の対象として正しいか判断します。",
  W3: "コードから確定できない意図・制約・優先順位を答えます。",
  W4: "自分の意図と現在の理解の食い違いを 1 件ずつ確定します。",
  W5: "どの計測提案を採用するか決めます。",
  W6: "生成された差分が意図どおりか確認します。",
  W7: "適用・接続へ進むか、未解決事項を引き継いで終えます。",
};

/** 主操作のラベル (§4.3)。状態の完了条件を満たす操作そのもの。 */
export const PRIMARY_ACTION_LABELS: Record<InterviewWorkflowPrimaryAction, string> = {
  open_repository: "Repository で snapshot を作成する",
  start_session: "インタビューを開始",
  none: "",
  confirm_understanding: "この理解で進む",
  submit_answer: "回答を送信",
  confirm_alignment_item: "この項目を確定する",
  approve_proposal: "この提案を承認する",
  record_diff_review: "この差分を確認した",
  open_connection_setup: "接続セットアップへ進む",
  view_handoff_status: "引き継ぎ状況を確認する",
  resume_session: "このセッションを再開する",
};

export const TERMINAL_LABELS: Record<InterviewWorkflowTerminalKind, string> = {
  completed: "完了",
  handoff: "引き継ぎ",
  suspended: "中断",
};

export const PROCESS_LABELS: Record<InterviewProcessKind, string> = {
  understanding_build: "システム理解を構築しています",
  understanding_update: "理解を更新しています",
  alignment_build: "意図とのズレを突き合わせています",
  question_routing: "質問を分類して調査しています",
  intent_candidates: "意図の候補を作成しています",
  proposal_generation: "計測の提案を作成しています",
  diff_generation: "差分を生成しています",
  runtime_reality_check: "実態チェックを実行しています",
};

/** 進捗ステップ表示 (§3.2 #11)。W0-A/W0-B は 1 ステップにまとめる。 */
const STEP_ORDER: Array<{ key: string; label: string; states: InterviewWorkflowState[] }> = [
  { key: "W0", label: "開始", states: ["W0-A", "W0-B"] },
  { key: "W2", label: "理解の確認", states: ["W2"] },
  { key: "W3", label: "質問への回答", states: ["W3"] },
  { key: "W4", label: "ズレの確認", states: ["W4"] },
  { key: "W5", label: "提案の承認", states: ["W5"] },
  { key: "W6", label: "差分の確認", states: ["W6"] },
  { key: "W7", label: "結果の確認", states: ["W7"] },
];

export function WorkflowProgressSteps({ state }: { state: InterviewWorkflowState }) {
  // W1 は順序を持たない (§2.2)。処理中は直前のステップを現在地として保つ。
  const activeIndex = STEP_ORDER.findIndex(s => s.states.includes(state));
  return (
    <ol
      className="flex flex-wrap items-center gap-1 text-xs"
      data-testid="workflow-progress-steps"
      aria-label="インタビューの進捗"
    >
      {STEP_ORDER.map((step, index) => {
        const isCurrent = index === activeIndex;
        const isDone = activeIndex >= 0 && index < activeIndex;
        return (
          <li key={step.key} className="flex items-center gap-1">
            <span
              className={
                isCurrent
                  ? "rounded bg-primary px-2 py-0.5 font-medium text-primary-foreground"
                  : isDone
                    ? "rounded bg-muted px-2 py-0.5 text-muted-foreground"
                    : "rounded px-2 py-0.5 text-muted-foreground/60"
              }
              aria-current={isCurrent ? "step" : undefined}
            >
              {step.label}
              {isDone ? "(完了)" : isCurrent ? "(現在)" : ""}
            </span>
            {index < STEP_ORDER.length - 1 ? <span aria-hidden>›</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * R1 — 現在地。全状態で常時表示し、1 画面に 1 箇所だけ置く。
 * 「次にやること」は主操作と同じ文言を指す (原則 P2)。
 */
export function WorkflowLocationCard({
  workflow,
  historyEntry,
  detail,
}: {
  workflow: InterviewWorkflowStateOut;
  historyEntry?: React.ReactNode;
  // 主操作をより具体的に言い換えた 1 行 (同じ操作を指す)。原則 P2 に従い、
  // 別カードに分けず現在地カードの中に置く。
  detail?: string;
}) {
  const state = workflow.state;
  const primaryLabel = PRIMARY_ACTION_LABELS[workflow.primary_action];
  const running = workflow.running_processes;

  return (
    <Card data-testid="workflow-location" className="border-primary/40">
      <CardContent className="space-y-2 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" data-testid="workflow-state-label">
            {WORKFLOW_STATE_LABELS[state]}
          </Badge>
          {workflow.terminal_kind ? (
            <Badge variant="secondary" data-testid="workflow-terminal-kind">
              {TERMINAL_LABELS[workflow.terminal_kind]}
            </Badge>
          ) : null}
          {historyEntry}
        </div>
        <p className="text-sm text-muted-foreground">{WORKFLOW_STATE_GOALS[state]}</p>
        {state === "W1" ? (
          <div className="space-y-1" data-testid="workflow-running">
            {running.map(run => (
              <p key={run.id} className="flex items-center gap-2 text-sm">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                {PROCESS_LABELS[run.process_kind]}
              </p>
            ))}
          </div>
        ) : primaryLabel ? (
          <div data-testid="next-action">
            <p className="text-sm" data-testid="workflow-next-action">
              次にやること: <span className="font-medium">{primaryLabel}</span>
            </p>
            {detail ? <p className="text-sm text-muted-foreground">{detail}</p> : null}
          </div>
        ) : null}
        <WorkflowProgressSteps state={state} />
      </CardContent>
    </Card>
  );
}

/**
 * R5 — 例外表示。ブロッキングと劣化だけを出す。
 * 情報区分 (E5〜E9 / E13) はここに現れない (§3.1 の役割割当規則)。
 */
export function WorkflowExceptions({
  workflow,
  onRetry,
  retryPending,
  onLeaveSafely,
}: {
  workflow: InterviewWorkflowStateOut;
  onRetry?: (kind: InterviewProcessKind) => void;
  retryPending?: boolean;
  onLeaveSafely?: () => void;
}) {
  const shown = workflow.exceptions.filter(
    e => e.severity === "blocking" || e.severity === "degraded",
  );
  if (shown.length === 0) return null;
  return (
    <div className="space-y-2" data-testid="workflow-exceptions">
      {shown.map(exc => (
        <WorkflowExceptionCard
          key={`${exc.code}-${exc.target_state ?? ""}-${exc.message}`}
          exception={exc}
          onRetry={onRetry}
          retryPending={retryPending}
          onLeaveSafely={onLeaveSafely}
        />
      ))}
    </div>
  );
}

function WorkflowExceptionCard({
  exception,
  onRetry,
  retryPending,
  onLeaveSafely,
}: {
  exception: InterviewWorkflowExceptionOut;
  onRetry?: (kind: InterviewProcessKind) => void;
  retryPending?: boolean;
  onLeaveSafely?: () => void;
}) {
  const blocking = exception.severity === "blocking";
  return (
    <Card
      className={blocking ? "border-destructive/60" : "border-warning/60"}
      data-testid={`workflow-exception-${exception.code}`}
      data-severity={exception.severity}
    >
      <CardContent className="space-y-2 py-3 text-sm">
        <div className="flex items-center gap-2">
          <AlertTriangle
            className={blocking ? "h-4 w-4 text-destructive" : "h-4 w-4 text-muted-foreground"}
            aria-hidden
          />
          {/* 色だけで状態を伝えない (原則 P8): 区分は必ずテキストで示す。 */}
          <Badge variant={blocking ? "destructive" : "warning"}>
            {blocking ? "作業を進められません" : "一部の情報が欠けています"}
          </Badge>
          <span className="text-muted-foreground">{exception.code}</span>
        </div>
        <p>{exception.message}</p>
        {exception.detail ? (
          <p className="text-xs text-muted-foreground break-words">{exception.detail}</p>
        ) : null}
        {exception.recovery_condition ? (
          <p className="text-xs text-muted-foreground">{exception.recovery_condition}</p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {/* 復旧操作は「同じ処理の再試行」と「安全な終端へ抜ける」の 2 つだけ
              (§5.3-7)。人間のゲートを飛ばす迂回路は置かない。 */}
          {exception.recovery_process_kind && onRetry ? (
            <Button
              size="sm"
              variant="outline"
              disabled={retryPending}
              onClick={() => onRetry(exception.recovery_process_kind!)}
              data-testid={`workflow-retry-${exception.recovery_process_kind}`}
            >
              もう一度実行する
            </Button>
          ) : null}
          {blocking && onLeaveSafely ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={onLeaveSafely}
              data-testid="workflow-leave-safely"
            >
              中断・引き継ぎで終える
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * E8 — 確認待ちの戻り。情報区分なので `R5` ではなく現在地の作業面に
 * 1 操作の導線として置く (§2.2 第 2 段 3.)。承諾するまで表示状態は
 * `reached_state` のまま変わらない。
 */
export function BackRequestNotice({
  workflow,
  onAcknowledge,
  pending,
}: {
  workflow: InterviewWorkflowStateOut;
  onAcknowledge: (requestId: number) => void;
  pending?: boolean;
}) {
  const request = workflow.pending_back_request;
  if (!request || !workflow.backward_hold) return null;
  const info = workflow.exceptions.find(e => e.code === "E8");
  return (
    <Card data-testid="workflow-back-request" className="border-primary/40">
      <CardContent className="flex flex-wrap items-center gap-3 py-3 text-sm">
        <Info className="h-4 w-4" aria-hidden />
        <span>{info?.message ?? "手前の状態で確認が必要になりました。"}</span>
        <span className="text-muted-foreground">
          戻り先: {WORKFLOW_STATE_LABELS[request.candidate_state]}
        </span>
        <Button
          size="sm"
          disabled={pending}
          onClick={() => onAcknowledge(request.id)}
          data-testid="workflow-acknowledge-back"
        >
          先に確認する
        </Button>
      </CardContent>
    </Card>
  );
}
