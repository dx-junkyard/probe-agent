import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertCircle, CheckCircle, FileCode, GitPullRequest,
  HelpCircle, Layers, Loader2, MessageSquareText, Pencil, Play, RefreshCw, Send,
  Sparkles, XCircle,
} from "lucide-react";
import {
  useAnswerInterviewQa,
  useApproveInterviewProposal,
  useConfirmInterviewUnderstanding,
  useCreateInterviewSession,
  useEditInterviewProposal,
  useInterviewApprovedSet,
  useInterviewContextPack,
  useInterviewDialogueTurn,
  useInterviewQaList,
  useInterviewSession,
  useInterviewSessions,
  useLatestSnapshot,
  useMaterializeInterview,
  useRebaseInterviewSnapshot,
  useRepositoryStatus,
  useRejectInterviewProposal,
  useResumeInterviewQa,
  useRunRuntimeRealityCheck,
  useSkipInterviewQa,
  useUnderstandingDiff,
  useUpdateInterviewUnderstanding,
} from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { api } from "@/api/client";
import { DiagnosticFixCallout, useDiagnosticHighlight } from "@/components/diagnostic-fix";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { formatTimestamp } from "@/lib/utils";
import type {
  CurrentUnderstanding,
  GapItem,
  IntelligenceRunEvidenceOut,
  InterviewMaterializeOut,
  InterviewProposalMetadataBlock,
  InterviewProposalOut,
  InterviewProposalProbePlan,
  InterviewQaOut,
  InterviewQuestionEvidenceRef,
  InterviewSessionDetailOut,
  InterviewStage,
  OpenQuestion,
  ProbeRecommendedMode,
  ProbeReplayability,
  ProbeSideEffectRisk,
  SourceMetadataElementType,
  SourceMetadataOperationKind,
  SourceMetadataStateEffect,
  UnderstandingDiffOut,
  UnderstandingItem,
} from "@/api/types";

const ELEMENT_TYPES: Array<"" | SourceMetadataElementType> = [
  "", "system", "core", "capability", "element", "supporting", "boundary",
];
const OPERATION_KINDS: Array<"" | SourceMetadataOperationKind> = [
  "", "analysis", "read", "write", "mutation", "io", "orchestration", "validation", "other",
];
const PROBE_MODES: ProbeRecommendedMode[] = ["trace", "shadow"];
const RISK_LEVELS: ProbeSideEffectRisk[] = ["none", "low", "medium", "high"];
const REPLAYABILITY: ProbeReplayability[] = ["safe", "caution", "unsafe"];

const STAGE_ORDER: InterviewStage[] = [
  "understanding_initialized",
  "purpose_confirmation",
  "capability_confirmation",
  "element_classification",
  "api_boundary_mapping",
  "probe_flow_selection",
  "proposal_generation",
];

// ユーザー向けの進捗表示: ステージ名ではなく「何を確認する作業か」を示す。
const STAGE_LABELS: Record<InterviewStage, string> = {
  understanding_initialized: "準備",
  purpose_confirmation: "目的",
  capability_confirmation: "主要機能",
  element_classification: "要素分類",
  api_boundary_mapping: "API境界",
  probe_flow_selection: "プローブ対象フロー",
  proposal_generation: "提案",
};

const STAGE_WORK_DESCRIPTIONS: Record<InterviewStage, string> = {
  understanding_initialized: "自動分析でシステム理解を構築します",
  purpose_confirmation: "ゴールと成功基準を確認します",
  capability_confirmation: "主要な機能領域を確認します",
  element_classification: "構成要素の分類を確認します",
  api_boundary_mapping: "責務の境界を確認します",
  probe_flow_selection: "計測すべき重要な実行フローを確認します",
  proposal_generation: "提案を生成・レビューします",
};

// 各ステージでの既定の確認質問(open questions が無い場合に表示)。
const STAGE_QUESTIONS: Record<InterviewStage, string> = {
  understanding_initialized: "自動分析の完了をお待ちください。",
  purpose_confirmation:
    "上記の理解(特に目的と成功基準)は正しいですか?正しければ「はい」、違う場合は修正すべき点を入力してください。",
  capability_confirmation: "主要な機能領域の一覧に過不足はありませんか?",
  element_classification: "各構成要素の分類は妥当ですか?修正があれば教えてください。",
  api_boundary_mapping: "APIの責務境界はこの理解で正しいですか?",
  probe_flow_selection: "計測(プローブ)すべき重要な実行フローはどれですか?",
  proposal_generation: "必要な理解が揃いました。提案を生成できます。",
};

// ゼロベースインタビュー(自動理解が構築できない場合)の固定質問。
// 質問文は固定のUIガイドであり、回答の解釈は常に推論モデル側で行う。
const ZERO_BASE_QUESTIONS = [
  "このシステム(または今回の取り組み)で達成したい目標は何ですか?",
  "影響を受ける領域(機能・モジュール・API)はどこですか?",
  "どのような変更・改善を望んでいますか?",
  "守るべき制約(性能・互換性・安全性など)はありますか?",
  "成功をどのように判定しますか?(成功基準)",
];

type InterviewUiState =
  | "preparing"
  | "needs_build"
  | "confirm_understanding"
  | "fill_gaps"
  | "zero_base"
  | "ready_for_proposals"
  | "proposal_review";

const UI_STATE_LABELS: Record<InterviewUiState, string> = {
  preparing: "理解を構築中",
  needs_build: "理解が未構築",
  confirm_understanding: "理解の確認",
  fill_gaps: "不足情報の確認",
  zero_base: "ゼロベースインタビュー",
  ready_for_proposals: "提案の準備完了",
  proposal_review: "提案のレビューと承認",
};

function stageIndex(stage: InterviewStage): number {
  return STAGE_ORDER.indexOf(stage);
}

function hasUnderstandingContent(u: CurrentUnderstanding | null | undefined): boolean {
  if (!u) return false;
  return [
    u.system_purpose, u.core_capabilities, u.capability_elements,
    u.supporting_elements, u.api_boundaries, u.probe_flow_candidates,
  ].some(items => (items ?? []).length > 0);
}

// 提案生成のロック解除条件: 構築済みの理解があるか、ゼロベースで
// ユーザーが内容を明示的に確定した(manual decision)場合のみ。
function proposalsUnlocked(session: InterviewSessionDetailOut): boolean {
  return (
    hasUnderstandingContent(session.current_understanding) ||
    session.understanding_confirmed_at != null
  );
}

function deriveUiState(
  session: InterviewSessionDetailOut,
  building: boolean,
): InterviewUiState {
  if (building) return "preparing";
  const stage = session.stage ?? "understanding_initialized";
  if (stage === "proposal_generation") {
    // ステージ到達だけでは解除しない: 未確定のゼロベースは確定を求める。
    if (!proposalsUnlocked(session)) return "zero_base";
    return (session.proposals ?? []).length > 0 ? "proposal_review" : "ready_for_proposals";
  }
  if (hasUnderstandingContent(session.current_understanding)) {
    return stage === "purpose_confirmation" || stage === "understanding_initialized"
      ? "confirm_understanding"
      : "fill_gaps";
  }
  // 理解が使えない: 構築を試みて失敗した/空だった場合はゼロベースへ。
  if (session.last_error || session.current_understanding) return "zero_base";
  return "needs_build";
}

function sortQuestions(questions: OpenQuestion[]): OpenQuestion[] {
  const rank = (p: string) => (p === "high" ? 0 : p === "medium" ? 1 : 2);
  return [...questions].sort((a, b) => rank(a.priority) - rank(b.priority));
}

function buildConfirmationPrompt(u: CurrentUnderstanding): string {
  const purposes = u.system_purpose.map(i => i.name).filter(Boolean).slice(0, 3);
  const caps = u.core_capabilities.map(i => i.name).filter(Boolean).slice(0, 5);
  const purposeText = purposes.length ? `「${purposes.join("、")}」` : "(目的は未推定)";
  const capsText = caps.length ? `主要機能として「${caps.join("、")}」を持つ` : "";
  return (
    `このシステムを、${purposeText}を目的とし、${capsText}システムと理解しました。` +
    "この理解は今回のセッションの対象として正しいですか?違う場合は、修正・絞り込みすべき点を教えてください。"
  );
}

// 現在ユーザーに提示する1つの質問。仮説・根拠・クイック回答候補を持つ
// (Issue #128)。confirmable のとき「はい/いいえ」クイック回答を表示する。
type FocusedQuestion = {
  text: string;
  hypothesis?: string | null;
  evidenceRefs?: InterviewQuestionEvidenceRef[];
  answerOptions?: string[];
  confirmable: boolean;
};

const QUICK_ANSWER_YES = "はい、その理解で正しいです。";
const QUICK_ANSWER_NO_PREFIX = "いいえ、正しくありません。修正点: ";

// open question エントリを focused-question カード表示用の形に変換する。
function focusedFromOpenQuestion(q: OpenQuestion): FocusedQuestion {
  return {
    text: q.question,
    hypothesis: q.hypothesis ?? null,
    evidenceRefs: q.evidence_refs ?? [],
    answerOptions: q.answer_options ?? [],
    // 仮説付きの質問は「はい/いいえ+修正」で答えられる確認型。
    confirmable: !!q.hypothesis,
  };
}

function provenanceVariant(value: string) {
  if (value === "manual") return "success" as const;
  if (value === "reasoning_llm") return "secondary" as const;
  return "outline" as const;
}

function approvalVariant(value: string) {
  if (value === "approved" || value === "edited") return "success" as const;
  if (value === "rejected") return "destructive" as const;
  if (value === "needs_review") return "warning" as const;
  return "secondary" as const;
}

function proposalReviewable(value: string) {
  return value === "proposed" || value === "needs_review";
}

function confidenceVariant(level: string) {
  if (level === "confirmed") return "success" as const;
  if (level === "likely") return "secondary" as const;
  if (level === "conflicting") return "destructive" as const;
  return "outline" as const;
}

function severityVariant(severity: string) {
  if (severity === "high") return "destructive" as const;
  if (severity === "medium") return "warning" as const;
  return "outline" as const;
}

function csv(items: string[]) {
  return items.join(", ");
}

function splitCsv(value: string) {
  return value.split(",").map(v => v.trim()).filter(Boolean);
}

function shortSha(value: string | null | undefined) {
  return value ? value.slice(0, 8) : "unknown";
}

function openDiff(diff: string, sessionId: number) {
  const blob = new Blob([diff], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  window.open(url, `probe-interview-${sessionId}-diff`);
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

type EditForm = {
  metadata: {
    role: string;
    capability: string;
    system_purpose: string;
    probe_value: string;
    element_type: "" | SourceMetadataElementType;
    operation_kind: "" | SourceMetadataOperationKind;
    consumers: string;
    state_effects: string;
  };
  probe_plan: InterviewProposalProbePlan;
};

function formFromProposal(proposal: InterviewProposalOut): EditForm {
  return {
    metadata: {
      role: proposal.metadata.role ?? "",
      capability: proposal.metadata.capability ?? "",
      system_purpose: proposal.metadata.system_purpose ?? "",
      probe_value: proposal.metadata.probe_value ?? "",
      element_type: proposal.metadata.element_type ?? "",
      operation_kind: proposal.metadata.operation_kind ?? "",
      consumers: csv(proposal.metadata.consumers),
      state_effects: csv(proposal.metadata.state_effects),
    },
    probe_plan: { ...proposal.probe_plan },
  };
}

function metadataFromForm(form: EditForm): InterviewProposalMetadataBlock {
  return {
    role: form.metadata.role.trim() || null,
    capability: form.metadata.capability.trim() || null,
    system_purpose: form.metadata.system_purpose.trim() || null,
    probe_value: form.metadata.probe_value.trim() || null,
    element_type: form.metadata.element_type || null,
    operation_kind: form.metadata.operation_kind || null,
    consumers: splitCsv(form.metadata.consumers),
    state_effects: splitCsv(form.metadata.state_effects) as SourceMetadataStateEffect[],
  };
}

function AuditBadge({ proposal }: { proposal: InterviewProposalOut }) {
  const run = proposal.intelligence_run;
  return (
    <div className="flex flex-wrap items-center gap-1">
      <Badge variant={provenanceVariant(proposal.decision_method)}>
        {proposal.decision_method}
      </Badge>
      {proposal.is_mock && <Badge variant="warning">mock</Badge>}
      {run?.model && <Badge variant="outline">{run.provider ?? "llm"} / {run.model}</Badge>}
      {run?.prompt_version && <Badge variant="outline">{run.prompt_version}</Badge>}
    </div>
  );
}

function MetadataGrid({ metadata, probe }: {
  metadata: InterviewProposalMetadataBlock;
  probe: InterviewProposalProbePlan;
}) {
  const rows = [
    ["role", metadata.role],
    ["capability", metadata.capability],
    ["purpose", metadata.system_purpose],
    ["probe value", metadata.probe_value],
    ["element", metadata.element_type],
    ["operation", metadata.operation_kind],
    ["consumers", metadata.consumers.join(", ")],
    ["state", metadata.state_effects.join(", ")],
    ["feature", probe.feature_id],
    ["objective", probe.objective],
    ["mode", probe.recommended_mode],
    ["risk", probe.side_effect_risk],
    ["replay", probe.replayability],
  ];
  return (
    <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-2 text-xs">
      {rows.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="uppercase text-[10px] text-muted-foreground">{label}</dt>
          <dd className="break-words">{value || "-"}</dd>
        </div>
      ))}
      {probe.reason && (
        <div className="md:col-span-2 min-w-0">
          <dt className="uppercase text-[10px] text-muted-foreground">reason</dt>
          <dd className="break-words">{probe.reason}</dd>
        </div>
      )}
    </dl>
  );
}

function ProgressSteps({ current }: { current: InterviewStage }) {
  const currentIdx = stageIndex(current);
  return (
    <div className="space-y-1" data-testid="stage-stepper">
      <div className="flex items-center gap-1 flex-wrap">
        {STAGE_ORDER.map((stage, idx) => {
          const isCurrent = idx === currentIdx;
          const isDone = idx < currentIdx;
          return (
            <Badge
              key={stage}
              variant={isCurrent ? "default" : isDone ? "success" : "outline"}
              className={isCurrent ? "ring-2 ring-primary/30" : ""}
              data-testid={`stage-${stage}`}
            >
              {isDone ? "✓ " : ""}{STAGE_LABELS[stage]}
            </Badge>
          );
        })}
      </div>
      <p className="text-xs text-muted-foreground">
        現在: {STAGE_LABELS[current]} — {STAGE_WORK_DESCRIPTIONS[current]}
      </p>
    </div>
  );
}

function NextActionBanner({ uiState, nextAction }: {
  uiState: InterviewUiState;
  nextAction: string;
}) {
  return (
    <div
      className="rounded-md border bg-muted/40 p-3 flex items-start gap-3"
      data-testid="next-action"
    >
      {uiState === "preparing" ? (
        <Loader2 className="h-4 w-4 mt-0.5 shrink-0 animate-spin text-primary" />
      ) : (
        <Sparkles className="h-4 w-4 mt-0.5 shrink-0 text-primary" />
      )}
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{UI_STATE_LABELS[uiState]}</Badge>
          <span className="text-xs font-medium text-muted-foreground">次にやること</span>
        </div>
        <p className="text-sm mt-1">{nextAction}</p>
      </div>
    </div>
  );
}

function UnderstandingItemCard({ item, category }: { item: UnderstandingItem; category: string }) {
  return (
    <div className="rounded-md border p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium text-sm">{item.name}</div>
          <div className="text-xs text-muted-foreground">{category}</div>
        </div>
        <Badge variant={confidenceVariant(item.confidence.level)}>
          {item.confidence.level}
        </Badge>
      </div>
      {item.summary && <p className="text-xs">{item.summary}</p>}
      {item.why_core && <p className="text-xs text-muted-foreground italic">{item.why_core}</p>}
      {item.evidence.length > 0 && (
        <div className="space-y-1">
          {item.evidence.map((e, i) => (
            <div key={i} className="text-[10px] text-muted-foreground font-mono">
              {e.path}:{e.start_line}-{e.end_line} — {e.summary}
            </div>
          ))}
        </div>
      )}
      {item.related_apis.length > 0 && (
        <div className="text-[10px] text-muted-foreground">APIs: {item.related_apis.join(", ")}</div>
      )}
      {item.children.length > 0 && (
        <div className="text-[10px] text-muted-foreground">子要素: {item.children.join(", ")}</div>
      )}
    </div>
  );
}

function UnderstandingPanel({ understanding }: { understanding: CurrentUnderstanding }) {
  const sections: [string, UnderstandingItem[]][] = [
    ["システムの目的", understanding.system_purpose],
    ["主要機能", understanding.core_capabilities],
    ["機能要素", understanding.capability_elements],
    ["支援要素", understanding.supporting_elements],
    ["API境界", understanding.api_boundaries],
    ["プローブ対象フロー候補", understanding.probe_flow_candidates],
  ];

  const hasContent = sections.some(([, items]) => items.length > 0);

  if (!hasContent) {
    return (
      <div className="text-sm text-muted-foreground text-center py-4">
        理解項目が見つかりませんでした。ドキュメントの内容が不足している可能性があります。
      </div>
    );
  }

  return (
    <div className="space-y-4" data-testid="understanding-panel">
      {sections.map(([label, items]) =>
        items.length > 0 ? (
          <div key={label}>
            <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">{label}</h4>
            <div className="space-y-2">
              {items.map((item, idx) => (
                <UnderstandingItemCard key={idx} item={item} category={label} />
              ))}
            </div>
          </div>
        ) : null,
      )}
    </div>
  );
}

// Q&A一覧パネル(Issue #129)。会話ログとは別に、質問・回答をIDベースで
// 一覧・編集・スキップできる。回答の修正は新しいリビジョン行として保存され、
// 旧回答も previous として残る(上書きしない)。
function QaItemCard({
  qa, onAnswer, onSkip, onResume, answering, skipping, resuming,
}: {
  qa: InterviewQaOut;
  onAnswer: (qaId: number, answerText: string, answerUnknown?: boolean) => Promise<void>;
  onSkip: (qaId: number) => void;
  onResume: (qaId: number) => void;
  answering: boolean;
  skipping: boolean;
  resuming: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(qa.answer_text ?? "");

  const submit = async () => {
    if (!draft.trim()) return;
    await onAnswer(qa.id, draft.trim());
    setEditing(false);
  };

  // Issue #142: 「わからない」を有効な入力として記録する。エラーにはせず、
  // status=unconfirmed として保存し、以後の推論で仮説→再確認に回す。
  const submitUnknown = async () => {
    await onAnswer(qa.id, draft.trim(), true);
    setEditing(false);
  };

  return (
    <div className="rounded-md border p-3 space-y-2" data-testid={`qa-item-${qa.id}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium break-words">{qa.question_text}</p>
          {qa.hypothesis && (
            <p className="text-xs text-muted-foreground italic mt-1">仮説: {qa.hypothesis}</p>
          )}
        </div>
        <div className="flex gap-1 shrink-0">
          {qa.question_source === "runtime" && (
            <Badge variant="secondary" data-testid={`qa-source-runtime-${qa.id}`}>実態チェック</Badge>
          )}
          <Badge variant="outline">{qa.question_category}</Badge>
          <Badge variant={
            qa.status === "answered" ? "success"
              : qa.status === "skipped" ? "warning"
              : qa.status === "unconfirmed" ? "warning"
              : qa.status === "revised" ? "secondary" : "outline"
          }>
            {qa.status === "unconfirmed" ? "不明(要確認)" : qa.status}
          </Badge>
        </div>
      </div>

      {qa.evidence_refs.length > 0 && (
        <div className="text-[10px] text-muted-foreground font-mono space-y-0.5">
          {qa.evidence_refs.map((e, i) => (
            <div key={i}>
              参照コード: {e.path}:{e.start_line}-{e.end_line}
              {e.char_count != null ? ` (${e.char_count} chars 読込)` : ""}
            </div>
          ))}
        </div>
      )}

      {qa.runtime_evidence && (
        <div
          className="rounded-md bg-muted/40 border p-2 text-[11px] space-y-1"
          data-testid={`qa-runtime-evidence-${qa.id}`}
        >
          <p className="font-mono text-muted-foreground">
            {qa.runtime_evidence.component_id} ({qa.runtime_evidence.path})
          </p>
          <p>
            直近{qa.runtime_evidence.facts.window_days}日: 呼び出し{qa.runtime_evidence.facts.call_count}件、
            エラー{qa.runtime_evidence.facts.error_count}件
            {qa.runtime_evidence.facts.error_rate != null
              ? `(${(qa.runtime_evidence.facts.error_rate * 100).toFixed(1)}%)`
              : ""}
            {qa.runtime_evidence.facts.duration_p50_ms != null && (
              <>、p50 {qa.runtime_evidence.facts.duration_p50_ms.toFixed(1)}ms</>
            )}
            {!qa.runtime_evidence.facts.has_traces && "(トレース0件)"}
          </p>
          <p className="text-muted-foreground">
            承認済み理解: role="{qa.runtime_evidence.declared.role ?? "-"}" /
            state_effects=[{qa.runtime_evidence.declared.state_effects.join(", ")}] /
            recommended_mode={qa.runtime_evidence.declared.recommended_mode}
          </p>
        </div>
      )}

      {qa.answer_text && !editing && (
        <p className="text-sm bg-muted/40 rounded p-2 whitespace-pre-wrap break-words">
          {qa.answer_text}
          {qa.answered_by && (
            <span className="block text-[10px] text-muted-foreground mt-1">
              回答者: {qa.answered_by}
            </span>
          )}
        </p>
      )}

      {editing ? (
        <div className="space-y-2">
          <Textarea
            value={draft}
            onChange={e => setDraft(e.target.value)}
            rows={3}
            placeholder="回答を入力"
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={submit} disabled={answering || !draft.trim()}>
              {answering ? "送信中..." : "保存"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={submitUnknown}
              disabled={answering}
              data-testid={`qa-answer-unknown-${qa.id}`}
            >
              わからない
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)}>キャンセル</Button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            <Pencil className="h-3 w-3 mr-1" />
            {qa.status === "answered" || qa.status === "unconfirmed" ? "回答を修正" : "回答する"}
          </Button>
          {qa.status === "open" && (
            <Button size="sm" variant="outline" onClick={() => onSkip(qa.id)} disabled={skipping}>
              後で回答
            </Button>
          )}
          {qa.status === "skipped" && (
            <Button size="sm" variant="outline" onClick={() => onResume(qa.id)} disabled={resuming}>
              再開
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function QaPanel({
  sessionId, actor, approvedCount,
}: { sessionId: number; actor: string; approvedCount: number }) {
  const { data: qaList } = useInterviewQaList(sessionId);
  const answer = useAnswerInterviewQa(sessionId);
  const skip = useSkipInterviewQa(sessionId);
  const resume = useResumeInterviewQa(sessionId);
  const runRealityCheck = useRunRuntimeRealityCheck(sessionId);

  const handleAnswer = async (qaId: number, answerText: string, answerUnknown?: boolean) => {
    try {
      const result = await answer.mutateAsync({
        qaId, answer_text: answerText, actor, answer_unknown: answerUnknown,
      });
      if (answerUnknown) {
        toast.info("「わからない」として記録しました。仮説を立てて確認質問を続けます。");
      } else if (result.regeneration_recommended) {
        toast.warning("回答が変わったため、生成済みの提案の再生成を検討してください(自動では再生成されません)。");
      } else {
        toast.success("回答を保存しました");
      }
    } catch (e) {
      toast.error(String(e));
    }
  };

  const handleRuntimeRealityCheck = async () => {
    try {
      const result = await runRealityCheck.mutateAsync();
      if (result.skipped) {
        toast.info(result.skipped_reason ?? "既存の未回答の実態チェック質問があるため、実行をスキップしました。");
      } else if (result.error) {
        toast.error(`実態チェックに失敗しました: ${result.error}`);
      } else if (result.created_qa_ids.length === 0) {
        toast.success("実態チェックを実行しましたが、確認すべきズレは見つかりませんでした。");
      } else {
        toast.success(`実態チェックにより ${result.created_qa_ids.length} 件の確認質問を生成しました。`);
      }
    } catch (e) {
      toast.error(String(e));
    }
  };

  // Keep the panel visible when there are no Q&A rows yet but the session
  // has approved elements: the Runtime Reality Check trigger (Issue #135)
  // lives here, and its most useful moment is exactly before any questions
  // exist. Hide the panel only when there is nothing to show AND nothing
  // that could be run.
  if (!qaList || (qaList.items.length === 0 && approvedCount === 0)) return null;

  return (
    <Card data-testid="qa-panel">
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <HelpCircle className="h-4 w-4" /> Q&amp;A一覧
        </CardTitle>
        <CardDescription className="flex items-center justify-between gap-2">
          <span>
            残質問 {qaList.open_count} 件(うち高優先度 {qaList.high_priority_open_count} 件)
          </span>
          <Button
            size="sm"
            variant="outline"
            onClick={handleRuntimeRealityCheck}
            disabled={runRealityCheck.isPending || approvedCount === 0}
            data-testid="run-runtime-reality-check"
          >
            {runRealityCheck.isPending ? "実行中..." : "実態チェックを実行"}
          </Button>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {qaList.answers_revised_at && (
          <div
            className="rounded-md border border-amber-500 bg-amber-500/10 p-3 text-sm flex items-start gap-2"
            data-testid="answers-revised-banner"
          >
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0 text-amber-600" />
            <div>
              回答が修正されました。内容を反映するには「理解を更新」を実行してください
              (自動では再構築されません)。
            </div>
          </div>
        )}
        <div className="space-y-2 max-h-[26rem] overflow-y-auto pr-1">
          {qaList.items.map(qa => (
            <QaItemCard
              key={qa.id}
              qa={qa}
              onAnswer={handleAnswer}
              onSkip={qaId => skip.mutate({ qaId, actor }, {
                onError: e => toast.error(String(e)),
              })}
              onResume={qaId => resume.mutate({ qaId, actor }, {
                onError: e => toast.error(String(e)),
              })}
              answering={answer.isPending}
              skipping={skip.isPending}
              resuming={resume.isPending}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// 差分サマリーの件数(追加/削除/確信度変化/説明変化)。パネル表示と
// 「理解を更新」トーストの両方で使う単一の集計ロジック。
function summarizeUnderstandingDiff(diff: UnderstandingDiffOut) {
  const added = diff.sections.reduce((n, s) => n + s.added.length, 0);
  const removed = diff.sections.reduce((n, s) => n + s.removed.length, 0);
  const confidenceChanged = diff.sections.reduce((n, s) => n + s.confidence_changed.length, 0);
  const summaryChanged = diff.sections.reduce((n, s) => n + s.summary_changed.length, 0);
  return {
    added,
    removed,
    confidenceChanged,
    summaryChanged,
    hasChanges: added + removed + confidenceChanged + summaryChanged > 0,
  };
}

// 理解のリビジョン差分パネル(Issue #136)。「理解を更新」の結果、直前リビジョンから
// 何が変わったかを決定的な差分(追加/削除/確信度変化)で表示する。回答修正から
// 再構築した直後は「あなたの回答修正が反映されました」の文脈を添える。
function UnderstandingDiffPanel({
  sessionId, answerRevisionReflected,
}: { sessionId: number; answerRevisionReflected: boolean }) {
  const { data: diff } = useUnderstandingDiff(sessionId);
  const [expanded, setExpanded] = useState(false);

  if (!diff) return null;

  if (!diff.has_previous) {
    return (
      <Card data-testid="understanding-diff-panel">
        <CardHeader>
          <CardTitle className="text-sm">理解の変化</CardTitle>
          <CardDescription>比較対象となる前のリビジョンがありません(初回の理解構築です)。</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const {
    added: addedCount,
    removed: removedCount,
    confidenceChanged: confidenceCount,
    summaryChanged: summaryCount,
    hasChanges,
  } = summarizeUnderstandingDiff(diff);

  return (
    <Card data-testid="understanding-diff-panel">
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <Sparkles className="h-4 w-4" /> 理解の変化
        </CardTitle>
        <CardDescription data-testid="understanding-diff-summary">
          {hasChanges
            ? `追加 ${addedCount} / 削除 ${removedCount} / 確信度変化 ${confidenceCount} / 説明変化 ${summaryCount}`
            : "前回のリビジョンから変化はありません"}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {answerRevisionReflected && (
          <div
            className="rounded-md border border-emerald-400 bg-emerald-50 dark:bg-emerald-950/20 p-2 text-xs"
            data-testid="answer-revision-reflected-banner"
          >
            あなたの回答修正が理解に反映されました。
          </div>
        )}
        {hasChanges && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setExpanded(e => !e)}
            data-testid="toggle-understanding-diff-detail"
          >
            {expanded ? "詳細を隠す" : "詳細を見る"}
          </Button>
        )}
        {expanded && (
          <div className="space-y-2 text-xs" data-testid="understanding-diff-detail">
            {diff.sections.map(s => {
              const empty = s.added.length === 0 && s.removed.length === 0
                && s.confidence_changed.length === 0 && s.summary_changed.length === 0;
              if (empty) return null;
              return (
                <div key={s.section} className="border rounded-md p-2 space-y-1">
                  <p className="font-semibold">{s.section}</p>
                  {s.added.map(n => (
                    <p key={`a-${n}`} className="text-emerald-600">+ {n}</p>
                  ))}
                  {s.removed.map(n => (
                    <p key={`r-${n}`} className="text-red-600">- {n}</p>
                  ))}
                  {s.confidence_changed.map(c => (
                    <div key={`c-${c.name}`} className="flex items-center gap-1">
                      <Badge variant="outline">{c.name}</Badge>
                      <span>確信度: {c.before ?? "-"} → {c.after ?? "-"}</span>
                    </div>
                  ))}
                  {s.summary_changed.map(n => (
                    <p key={`s-${n}`}>{n}: 説明が更新されました</p>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function GapAnalysisPanel({ gaps }: { gaps: GapItem[] }) {
  if (gaps.length === 0) return null;
  return (
    <div className="space-y-2" data-testid="gap-analysis-panel">
      {gaps.map((gap, idx) => (
        <div key={idx} className="rounded-md border p-3 flex items-start gap-3">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0 text-amber-500" />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">{gap.name}</span>
              <Badge variant={severityVariant(gap.severity)}>{gap.severity}</Badge>
              <Badge variant="outline">{gap.gap_type}</Badge>
            </div>
            {gap.summary && <p className="text-xs text-muted-foreground mt-1">{gap.summary}</p>}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function InterviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionParam = Number(searchParams.get("session"));
  const selectedSessionId = Number.isFinite(sessionParam) && sessionParam > 0 ? sessionParam : null;
  const { user } = useAuth();
  const actor = user?.username ?? "dashboard";

  const { data: repositoryStatus, refetch: refetchRepositoryStatus } = useRepositoryStatus();
  const { data: latestSnapshot, isLoading: snapshotLoading } = useLatestSnapshot();
  const { data: sessions, isLoading: sessionsLoading } = useInterviewSessions();
  const createSession = useCreateInterviewSession();
  const { data: session, isLoading: sessionLoading } = useInterviewSession(selectedSessionId);
  const { data: contextPack } = useInterviewContextPack(selectedSessionId);
  const { data: approvedSet } = useInterviewApprovedSet(selectedSessionId);
  const dialogueTurn = useInterviewDialogueTurn(selectedSessionId);
  const approve = useApproveInterviewProposal(selectedSessionId);
  const reject = useRejectInterviewProposal(selectedSessionId);
  const edit = useEditInterviewProposal(selectedSessionId);
  const materialize = useMaterializeInterview(selectedSessionId);
  const rebaseSnapshot = useRebaseInterviewSnapshot(selectedSessionId);
  const updateUnderstanding = useUpdateInterviewUnderstanding();
  const confirmUnderstanding = useConfirmInterviewUnderstanding(selectedSessionId);

  const [message, setMessage] = useState("");
  const messageInputRef = useRef<HTMLTextAreaElement | null>(null);
  const [editing, setEditing] = useState<InterviewProposalOut | null>(null);
  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [lastMaterialization, setLastMaterialization] = useState<InterviewMaterializeOut | null>(null);
  const [answerRevisionReflectedState, setAnswerRevisionReflectedState] = useState<{
    sessionId: number | null;
    value: boolean;
  }>({ sessionId: null, value: false });
  const [lastEvidenceReadsState, setLastEvidenceReadsState] = useState<{
    sessionId: number | null;
    items: IntelligenceRunEvidenceOut[];
  }>({ sessionId: null, items: [] });

  const sortedSessions = useMemo(() => sessions ?? [], [sessions]);
  const proposals = session?.proposals ?? [];
  const pendingCount = proposals.filter(p => p.approval_state === "proposed").length;
  const approvedCount = approvedSet?.approved_count ?? 0;
  const diff = lastMaterialization?.diff || session?.materialization_diff || "";
  const currentStage = session?.stage ?? "understanding_initialized";
  const isProposalStage = currentStage === "proposal_generation";
  const sessionSnapshotStale = !!(
    session && latestSnapshot && session.snapshot_id !== latestSnapshot.id
  );
  const repositoryHeadStale = !!(session && repositoryStatus?.snapshot_stale);

  const building = createSession.isPending || updateUnderstanding.isPending;
  const uiState: InterviewUiState | null = session ? deriveUiState(session, building) : null;
  const unlocked = session ? proposalsUnlocked(session) : false;
  const purposeFixHighlight = useDiagnosticHighlight<HTMLDivElement>("interview-purpose");
  const capabilitiesFixHighlight = useDiagnosticHighlight<HTMLDivElement>("interview-capabilities");
  // 提案ステージで未回答の絞り込み質問が残っている状態。提案生成を依頼しても
  // 情報不足だった場合、モデルの確認質問が open_questions に残る。
  const proposalNarrowing =
    uiState === "ready_for_proposals" && (session?.open_questions ?? []).length > 0;

  useEffect(() => {
    if (!selectedSessionId && sortedSessions.length > 0) {
      const next = new URLSearchParams(searchParams);
      next.set("session", String(sortedSessions[0].id));
      setSearchParams(next, { replace: true });
    }
  }, [selectedSessionId, sortedSessions, searchParams, setSearchParams]);

  const answerRevisionReflected =
    answerRevisionReflectedState.sessionId === selectedSessionId
      ? answerRevisionReflectedState.value
      : false;
  const lastEvidenceReads =
    lastEvidenceReadsState.sessionId === selectedSessionId
      ? lastEvidenceReadsState.items
      : [];

  const userMessageCount = useMemo(
    () => (session?.messages ?? []).filter(m => m.role === "user").length,
    [session?.messages],
  );

  // ゼロベースの固定質問に一通り回答した(またはステージを走り切った)ら、
  // ユーザーによる明示的な確定で提案生成をロック解除する。
  const zeroBaseComplete =
    uiState === "zero_base" &&
    (userMessageCount >= ZERO_BASE_QUESTIONS.length || isProposalStage);

  // 現在ユーザーに求める「1つの質問/確認」を導出する。
  const focusedQuestion = useMemo<FocusedQuestion | null>(() => {
    if (!session || !uiState) return null;
    if (uiState === "confirm_understanding" && session.current_understanding) {
      return {
        text: buildConfirmationPrompt(session.current_understanding),
        confirmable: true,
      };
    }
    if (uiState === "fill_gaps") {
      const open = sortQuestions(session.open_questions ?? []);
      if (open.length === 0) {
        return { text: STAGE_QUESTIONS[currentStage], confirmable: false };
      }
      return focusedFromOpenQuestion(open[0]);
    }
    if (uiState === "zero_base") {
      if (zeroBaseComplete) {
        return {
          text: "必要な回答が揃いました。補足があれば入力し、なければ「この内容で提案生成に進む」を押して内容を確定してください。",
          confirmable: false,
        };
      }
      const idx = Math.min(userMessageCount, ZERO_BASE_QUESTIONS.length - 1);
      return { text: ZERO_BASE_QUESTIONS[idx], confirmable: false };
    }
    if (uiState === "ready_for_proposals") {
      // 提案生成を依頼しても情報不足で提案できなかった場合、モデルは絞り込みの
      // 確認質問を open_questions に返す(プロンプト interview-v6)。固定文の
      // 代わりにその質問を提示し、回答のたびに提案生成を再試行する。
      const open = sortQuestions(session.open_questions ?? []);
      if (open.length > 0) return focusedFromOpenQuestion(open[0]);
      return {
        text: "提案を生成する準備ができました。対象にしたい範囲や重視したい観点があれば入力し、「送信して提案を生成」を押してください。",
        confirmable: false,
      };
    }
    return null;
  }, [session, uiState, currentStage, userMessageCount, zeroBaseComplete]);

  // fill_gaps / 提案ステージの絞り込みで表示中の open question を回答対象と
  // してサーバーに渡し、回答済みの質問が再表示されないよう消費してもらう。
  // qa_id を持つエントリは ID 参照(answered_qa_id)で消費し、Q&A 一覧の行も
  // answered になる(Issue #129)。テキストは旧セッション互換のため併送する。
  const answeredForTurn = useMemo<{ text?: string; qaId?: number }>(() => {
    if (
      (uiState !== "fill_gaps" && uiState !== "ready_for_proposals") ||
      !session || !focusedQuestion
    ) return {};
    const open = sortQuestions(session.open_questions ?? []);
    if (open.length === 0 || open[0].question !== focusedQuestion.text) return {};
    return { text: focusedQuestion.text, qaId: open[0].qa_id ?? undefined };
  }, [uiState, session, focusedQuestion]);

  const ensureRepositorySnapshotFresh = async (action: string) => {
    const { data: status } = await refetchRepositoryStatus();
    if (status?.snapshot_stale) {
      toast.warning(
        `${action}の前に最新 HEAD の snapshot を作成してください。Repository 画面で snapshot を作成してから、このインタビューを更新できます。`,
      );
      return false;
    }
    return true;
  };

  const nextActionText = useMemo(() => {
    switch (uiState) {
      case "preparing":
        return "ドキュメントとコードから自動でシステム理解を構築しています。完了までお待ちください。";
      case "needs_build":
        return "「理解を構築」を押して自動分析を実行してください。";
      case "confirm_understanding":
        return "推定した理解を確認し、正しければ「はい」、違う場合は修正点を入力して送信してください。";
      case "fill_gaps":
        return "表示中の質問に回答して送信してください。回答が十分になると自動的に次の確認へ進みます。";
      case "zero_base":
        return zeroBaseComplete
          ? "必要な回答が揃いました。「この内容で提案生成に進む」で内容を確定すると提案を生成できます。"
          : "自動では十分な理解を構築できませんでした。表示される質問に1つずつ回答してください。";
      case "ready_for_proposals":
        return proposalNarrowing
          ? "提案に必要な情報がまだ不足しています。表示中の確認質問に回答して対象を絞り込んでください。回答を送信するたびに提案生成を再試行します。"
          : "「送信して提案を生成」を押すと、確認済みの理解にもとづいて提案が生成されます。";
      case "proposal_review":
        return approvedCount > 0
          ? "承認済みの提案から「差分を生成」でレビュー用の差分を作成できます。残りの提案のレビューも続けられます。"
          : "各提案を承認・編集・却下してください。承認した提案から差分を生成できます。";
      default:
        return "";
    }
  }, [uiState, approvedCount, zeroBaseComplete, proposalNarrowing]);

  const startSession = async () => {
    if (!latestSnapshot) {
      toast.error("先にリポジトリのスナップショットを作成してください");
      return;
    }
    if (!(await ensureRepositorySnapshotFresh("インタビュー開始"))) return;
    try {
      const created = await createSession.mutateAsync({
        snapshot_id: latestSnapshot.id,
        title: `System interview ${latestSnapshot.commit_sha.slice(0, 8)}`,
        focus: "Author reviewed probe-agent metadata and probe proposals",
      });
      setSearchParams({ session: String(created.id) });
      toast.success("インタビューを開始しました。システム理解を自動構築しています…");
      try {
        const updated = await updateUnderstanding.mutateAsync(created.id);
        if (updated.last_error) {
          toast.error(`自動理解の構築に失敗しました: ${updated.last_error}`);
        } else {
          toast.success("初期理解の構築が完了しました。内容を確認してください。");
        }
      } catch (e) {
        toast.error(`自動理解の構築に失敗しました: ${String(e)}`);
      }
    } catch (e) {
      toast.error(String(e));
    }
  };

  const refreshUnderstanding = async () => {
    if (!selectedSessionId) return;
    const hadAnswerRevision = !!session?.answers_revised_at;
    try {
      const updated = await updateUnderstanding.mutateAsync(selectedSessionId);
      if (updated.last_error) {
        toast.error(`理解の更新に失敗しました: ${updated.last_error}`);
        return;
      }
      setAnswerRevisionReflectedState({
        sessionId: selectedSessionId,
        value: hadAnswerRevision,
      });
      try {
        const diff = await api.get<UnderstandingDiffOut>(
          `/interview/sessions/${selectedSessionId}/understanding-diff`,
        );
        if (!diff.has_previous) {
          toast.success("理解を更新しました(初回のリビジョンです)");
        } else {
          const counts = summarizeUnderstandingDiff(diff);
          if (!counts.hasChanges) {
            toast.success("理解を更新しました(前回からの変化はありません)");
          } else {
            toast.success(
              `理解を更新しました(追加 ${counts.added} / 削除 ${counts.removed} / 確信度変化 ${counts.confidenceChanged})`,
            );
          }
        }
      } catch {
        toast.success("理解を更新しました");
      }
    } catch (e) {
      toast.error(String(e));
    }
  };

  const sendText = async (raw: string, opts?: { answerUnknown?: boolean }) => {
    const text = raw.trim();
    if (!text || !selectedSessionId) return;
    const willGenerateProposals = isProposalStage && unlocked;
    if (willGenerateProposals && !(await ensureRepositorySnapshotFresh("提案生成"))) return;
    try {
      const result = await dialogueTurn.mutateAsync({
        user_message: text,
        generate_proposals: willGenerateProposals,
        answered_question: answeredForTurn.text,
        answered_qa_id: answeredForTurn.qaId,
        actor,
        answer_unknown: opts?.answerUnknown,
      });
      setMessage("");
      setLastEvidenceReadsState({
        sessionId: selectedSessionId,
        items: result.evidence_reads ?? [],
      });
      if (result.error) toast.error(result.error);
      else if (opts?.answerUnknown) toast.info("「わからない」として記録しました。仮説を立てて確認を続けます。");
      else if (result.proposals.length) toast.success(`${result.proposals.length}件の提案を生成しました`);
      else if (result.proposals_requested)
        // 提案を依頼したが情報不足: モデルは絞り込みの確認質問を返している。
        // 「送信成功」だけ出すと提案が作られたように見えるため区別する。
        toast.info("提案はまだ生成されませんでした。表示される確認質問に回答して、プローブ対象を一緒に絞り込んでください。");
      else toast.success("回答を送信しました");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const sendTurn = () => sendText(message);

  // Issue #142: 現在の focused question に対して「わからない」を明示送信する。
  // 自由文ではなく answer_unknown フラグで送り、確定回答なしとして記録させる。
  const sendUnknown = () =>
    sendText(message.trim() || "わかりません", { answerUnknown: true });

  // 「いいえ」は修正内容の入力を促す: 定型の書き出しを入力欄に入れてフォーカスする。
  const startCorrection = () => {
    setMessage(QUICK_ANSWER_NO_PREFIX);
    messageInputRef.current?.focus();
  };

  const doConfirmUnderstanding = async () => {
    if (!selectedSessionId) return;
    try {
      await confirmUnderstanding.mutateAsync({ actor });
      toast.success("内容を確定しました。提案を生成できます。");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const openEdit = (proposal: InterviewProposalOut) => {
    setEditing(proposal);
    setEditForm(formFromProposal(proposal));
  };

  const saveEdit = async () => {
    if (!editing || !editForm) return;
    try {
      await edit.mutateAsync({
        proposalId: editing.id,
        actor,
        metadata: metadataFromForm(editForm),
        probe_plan: editForm.probe_plan,
      });
      toast.success("修正した提案を承認しました");
      setEditing(null);
      setEditForm(null);
    } catch (e) {
      toast.error(String(e));
    }
  };

  const triggerMaterialization = async () => {
    if (!selectedSessionId) return;
    if (!(await ensureRepositorySnapshotFresh("差分生成"))) return;
    try {
      const result = await materialize.mutateAsync();
      setLastMaterialization(result);
      toast.success(`${result.items_materialized}件を差分化しました`);
    } catch (e) {
      toast.error(String(e));
    }
  };

  const rebaseToLatestSnapshot = async () => {
    if (!selectedSessionId || !latestSnapshot) return;
    if (!(await ensureRepositorySnapshotFresh("インタビュー更新"))) return;
    try {
      const result = await rebaseSnapshot.mutateAsync({
        target_snapshot_id: latestSnapshot.id,
        actor,
      });
      toast.success(
        `最新 snapshot に更新しました(再レビュー ${result.proposals_marked_needs_review} 件)`,
      );
    } catch (e) {
      toast.error(String(e));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <MessageSquareText className="h-6 w-6" /> システムインタビュー
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
            開始すると、ドキュメントとコードの自動分析からシステム理解を構築し、
            確認と不足情報の質問を経て提案生成へ進みます。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            className="w-[240px]"
            value={selectedSessionId ? String(selectedSessionId) : ""}
            onChange={e => {
              const next = new URLSearchParams(searchParams);
              if (e.target.value) next.set("session", e.target.value);
              else next.delete("session");
              setSearchParams(next);
            }}
            disabled={!sortedSessions.length}
            aria-label="インタビューセッション"
          >
            <option value="">セッション未選択</option>
            {sortedSessions.map(s => (
              <option key={s.id} value={s.id}>
                #{s.id} · snapshot {s.snapshot_id} · {s.status}
              </option>
            ))}
          </Select>
          <Button size="sm" onClick={startSession} disabled={building || snapshotLoading || !latestSnapshot}>
            <Sparkles className="h-4 w-4 mr-1" />
            {building ? "分析中..." : "インタビューを開始"}
          </Button>
        </div>
      </div>

      <div {...purposeFixHighlight}>
        <DiagnosticFixCallout anchor="interview-purpose" />
      </div>
      <div {...capabilitiesFixHighlight}>
        <DiagnosticFixCallout anchor="interview-capabilities" />
      </div>

      {!latestSnapshot && !snapshotLoading && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            リポジトリのスナップショットがありません。インタビューを開始する前に{" "}
            <Link className="underline" to="/repository">リポジトリ</Link>{" "}
            ページでスナップショットを作成してください。
          </CardContent>
        </Card>
      )}

      {sessionsLoading || sessionLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : !selectedSessionId || !session ? (
        <Card>
          <CardContent className="py-10 text-center space-y-3">
            <p className="text-sm text-muted-foreground">
              「インタビューを開始」を押すと、最新スナップショットから自動でシステム理解を構築し、
              確認する内容を1つずつ質問します。
            </p>
            <Button onClick={startSession} disabled={building || snapshotLoading || !latestSnapshot}>
              <Sparkles className="h-4 w-4 mr-1" />
              {building ? "分析中..." : "インタビューを開始"}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          {repositoryHeadStale && repositoryStatus && (
            <div
              className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 p-3 text-sm text-amber-900 dark:text-amber-100 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
              data-testid="repository-head-stale-banner"
            >
              <div>
                リポジトリの HEAD が最新 snapshot より進んでいます
                {repositoryStatus.latest_snapshot && (
                  <>
                    {" "}(snapshot {repositoryStatus.latest_snapshot.id}:{" "}
                    <code>{shortSha(repositoryStatus.latest_snapshot.commit_sha)}</code>
                    {" "}→ HEAD <code>{shortSha(repositoryStatus.current_head)}</code>)
                  </>
                )}
                。新しい snapshot を作成してから、このインタビューを更新してください。
              </div>
              <Link
                to="/repository"
                className="inline-flex h-8 items-center justify-center gap-2 whitespace-nowrap rounded-md border border-input bg-background px-3 text-xs font-medium shadow-sm hover:bg-accent hover:text-accent-foreground"
              >
                <RefreshCw className="h-4 w-4" />
                Repository で snapshot 作成
              </Link>
            </div>
          )}

          {sessionSnapshotStale && latestSnapshot && (
            <div
              className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 p-3 text-sm text-amber-900 dark:text-amber-100 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
              data-testid="interview-snapshot-stale-banner"
            >
              <div>
                このインタビューは snapshot {session.snapshot_id} に固定されています。
                最新 snapshot {latestSnapshot.id} に更新すると、既存の回答を保持し、
                影響を受けた提案だけ再レビューに戻します。
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={rebaseToLatestSnapshot}
                disabled={rebaseSnapshot.isPending}
                data-testid="rebase-interview-snapshot"
              >
                <RefreshCw className={`h-4 w-4 mr-1 ${rebaseSnapshot.isPending ? "animate-spin" : ""}`} />
                {rebaseSnapshot.isPending ? "更新中..." : "最新 snapshot に更新"}
              </Button>
            </div>
          )}

          {uiState && <NextActionBanner uiState={uiState} nextAction={nextActionText} />}

          <Card>
            <CardContent className="py-3">
              <ProgressSteps current={currentStage} />
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-4">
            {/* メイン: 会話がインタビューの主要な操作領域 */}
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">会話</CardTitle>
                  <CardDescription>
                    {isProposalStage
                      ? "ターンは固定スナップショットのコンテキストに基づきます。"
                      : "システムからの確認・質問に回答すると、自動的に次の確認へ進みます。"}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="space-y-2 max-h-[26rem] overflow-y-auto pr-1">
                    {session.messages.length === 0 ? (
                      <div className="text-sm text-muted-foreground">まだ会話はありません。</div>
                    ) : session.messages.map(m => (
                      <div key={m.id} className="rounded-md border p-3 text-sm">
                        <div className="flex items-center justify-between gap-2 mb-1">
                          <Badge variant={m.role === "assistant" ? "secondary" : "outline"}>{m.role}</Badge>
                          <span className="text-xs text-muted-foreground">{formatTimestamp(m.created_at)}</span>
                        </div>
                        <p className="whitespace-pre-wrap break-words">{m.content}</p>
                      </div>
                    ))}
                  </div>

                  {uiState === "preparing" ? (
                    <div className="rounded-md border p-4 text-sm text-muted-foreground flex items-center gap-2" data-testid="preparing-indicator">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      自動分析を実行中です。しばらくお待ちください…
                    </div>
                  ) : uiState === "needs_build" ? (
                    <div className="rounded-md border p-4 space-y-2 text-sm" data-testid="needs-build">
                      <p className="text-muted-foreground">
                        このセッションではまだシステム理解が構築されていません。
                      </p>
                      <Button size="sm" onClick={refreshUnderstanding} disabled={building}>
                        <Sparkles className="h-4 w-4 mr-1" />
                        理解を構築
                      </Button>
                    </div>
                  ) : (
                    <>
                      {uiState === "zero_base" && (
                        <div
                          className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 p-3 text-xs text-amber-900 dark:text-amber-100"
                          data-testid="zero-base-notice"
                        >
                          自動では十分なシステム理解を構築できませんでした。ゼロベースのインタビューに切り替えます。
                          {session.last_error && (
                            <div className="mt-1 font-mono break-words">{session.last_error}</div>
                          )}
                        </div>
                      )}
                      {focusedQuestion && (
                        <div className="rounded-md border bg-muted/40 p-3 space-y-2" data-testid="focused-question">
                          {focusedQuestion.hypothesis && (
                            <div className="rounded-md bg-background/60 border p-2 text-sm" data-testid="question-hypothesis">
                              <span className="text-[10px] uppercase font-semibold text-muted-foreground block mb-1">仮説</span>
                              {focusedQuestion.hypothesis}
                            </div>
                          )}
                          <div className="flex items-start gap-2">
                            <HelpCircle className="h-4 w-4 mt-0.5 shrink-0 text-blue-500" />
                            <p className="text-sm">{focusedQuestion.text}</p>
                          </div>
                          {(focusedQuestion.evidenceRefs ?? []).length > 0 && (
                            <div className="space-y-0.5" data-testid="question-evidence">
                              <span className="text-[10px] uppercase font-semibold text-muted-foreground">根拠</span>
                              {focusedQuestion.evidenceRefs!.map((e, i) => (
                                <div key={i} className="text-[10px] text-muted-foreground font-mono">
                                  {e.path}{e.start_line > 0 ? `:${e.start_line}-${e.end_line}` : ""}
                                </div>
                              ))}
                            </div>
                          )}
                          {(focusedQuestion.confirmable || (focusedQuestion.answerOptions ?? []).length > 0 || uiState === "fill_gaps" || proposalNarrowing) && (
                            <div className="flex flex-wrap gap-2 pt-1" data-testid="quick-answers">
                              {focusedQuestion.confirmable && (
                                <>
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() => sendText(QUICK_ANSWER_YES)}
                                    disabled={dialogueTurn.isPending}
                                    data-testid="quick-answer-yes"
                                  >
                                    <CheckCircle className="h-4 w-4 mr-1 text-emerald-600" />
                                    はい、正しいです
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={startCorrection}
                                    disabled={dialogueTurn.isPending}
                                    data-testid="quick-answer-no"
                                  >
                                    <Pencil className="h-4 w-4 mr-1" />
                                    いいえ(修正を入力)
                                  </Button>
                                </>
                              )}
                              {(focusedQuestion.answerOptions ?? []).map((opt, i) => (
                                <Button
                                  key={i}
                                  size="sm"
                                  variant="outline"
                                  onClick={() => sendText(opt)}
                                  disabled={dialogueTurn.isPending}
                                >
                                  {opt}
                                </Button>
                              ))}
                              {/* Issue #142: 明示的な「わからない」入力。自由文ではなく
                                  answer_unknown フラグで送り、確定回答なしとして記録する。
                                  提案ステージの絞り込み質問でも同様に使える。 */}
                              {(uiState === "fill_gaps" || proposalNarrowing) && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={sendUnknown}
                                  disabled={dialogueTurn.isPending}
                                  data-testid="quick-answer-unknown"
                                >
                                  <HelpCircle className="h-4 w-4 mr-1" />
                                  わからない
                                </Button>
                              )}
                            </div>
                          )}
                        </div>
                      )}
                      {zeroBaseComplete && (
                        <Button
                          size="sm"
                          onClick={doConfirmUnderstanding}
                          disabled={confirmUnderstanding.isPending}
                          data-testid="confirm-understanding"
                        >
                          <CheckCircle className="h-4 w-4 mr-1" />
                          {confirmUnderstanding.isPending ? "確定中..." : "この内容で提案生成に進む"}
                        </Button>
                      )}
                      <Textarea
                        ref={messageInputRef}
                        rows={4}
                        value={message}
                        onChange={e => setMessage(e.target.value)}
                        placeholder={isProposalStage && unlocked && !proposalNarrowing
                          ? "提案の対象範囲や重視したい観点があれば入力してください。"
                          : "上の質問への回答や修正点を入力してください。"}
                      />
                      <div className="flex gap-2">
                        <Button size="sm" onClick={sendTurn} disabled={dialogueTurn.isPending || !message.trim()}>
                          <Send className="h-4 w-4 mr-1" />
                          {dialogueTurn.isPending
                            ? "送信中..."
                            : isProposalStage && unlocked
                              ? "送信して提案を生成"
                              : "回答を送信"}
                        </Button>
                      </div>
                      {lastEvidenceReads.length > 0 && (
                        <div
                          className="rounded-md border p-2 text-[10px] text-muted-foreground font-mono space-y-0.5"
                          data-testid="evidence-reads-panel"
                        >
                          <p className="text-[10px] uppercase font-semibold not-italic">
                            このターンで参照したコード
                          </p>
                          {lastEvidenceReads.map((e, i) => (
                            <div key={i}>
                              {e.path}:{e.start_line}-{e.end_line} ({e.char_count} chars)
                              {e.truncated ? " (truncated)" : ""}
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </CardContent>
              </Card>

              {/* 提案・差分は proposal_generation 到達 + ロック解除まで表示しない(Issue #123) */}
              {isProposalStage && unlocked && (
                <>
                  <Card>
                    <CardHeader>
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <CardTitle className="text-sm">提案レビュー</CardTitle>
                          <CardDescription>
                            各提案(メタデータ + プローブ計画)を承認・却下・編集してください。
                          </CardDescription>
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={triggerMaterialization}
                          disabled={materialize.isPending || approvedCount === 0}
                        >
                          <Play className="h-4 w-4 mr-1" />
                          {materialize.isPending ? "生成中..." : "差分を生成"}
                        </Button>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {proposals.length === 0 ? (
                        <div className="text-sm text-muted-foreground" data-testid="no-proposals-yet">
                          まだ提案はありません。会話から「送信して提案を生成」でレビュー項目を生成してください。
                          情報が不足している場合は、AIが対象を絞り込むための確認質問を返します。
                        </div>
                      ) : proposals.map(proposal => (
                        <div key={proposal.id} className="rounded-md border p-4 space-y-3" data-testid="interview-proposal-card">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="font-mono text-sm truncate">{proposal.qualified_name}</div>
                              <div className="text-xs text-muted-foreground truncate">{proposal.path}</div>
                            </div>
                            <div className="flex flex-col items-end gap-1 shrink-0">
                              <Badge variant={approvalVariant(proposal.approval_state)}>{proposal.approval_state}</Badge>
                              <AuditBadge proposal={proposal} />
                            </div>
                          </div>
                          {(proposal.capability_name || proposal.evidence_summary || proposal.proposal_confidence != null) && (
                            <div className="rounded-md bg-muted/50 px-3 py-2 text-xs space-y-1">
                              {proposal.capability_name && (
                                <div><span className="font-medium">Capability:</span> {proposal.capability_name}</div>
                              )}
                              {proposal.evidence_summary && (
                                <div><span className="font-medium">根拠:</span> {proposal.evidence_summary}</div>
                              )}
                              {proposal.proposal_confidence != null && (
                                <div><span className="font-medium">確信度:</span> {(proposal.proposal_confidence * 100).toFixed(0)}%</div>
                              )}
                            </div>
                          )}
                          <MetadataGrid metadata={proposal.metadata} probe={proposal.probe_plan} />
                          <div className="flex items-center gap-2 justify-end">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => openEdit(proposal)}
                              disabled={!proposalReviewable(proposal.approval_state) || edit.isPending}
                            >
                              <Pencil className="h-4 w-4 mr-1" />
                              編集
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => approve.mutateAsync({ proposalId: proposal.id, actor }).then(() => toast.success("提案を承認しました")).catch(e => toast.error(String(e)))}
                              disabled={!proposalReviewable(proposal.approval_state) || approve.isPending}
                            >
                              <CheckCircle className="h-4 w-4 mr-1 text-emerald-600" />
                              承認
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => reject.mutateAsync({ proposalId: proposal.id, actor }).then(() => toast.success("提案を却下しました")).catch(e => toast.error(String(e)))}
                              disabled={!proposalReviewable(proposal.approval_state) || reject.isPending}
                            >
                              <XCircle className="h-4 w-4 mr-1 text-red-500" />
                              却下
                            </Button>
                          </div>
                        </div>
                      ))}
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <CardTitle className="text-sm">レビュー用差分</CardTitle>
                          <CardDescription>差分の生成までで停止し、適用は開発者がレビューして行います。</CardDescription>
                        </div>
                        <Button size="sm" variant="outline" onClick={() => openDiff(diff, session.id)} disabled={!diff}>
                          <GitPullRequest className="h-4 w-4 mr-1" />
                          差分を開く
                        </Button>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {lastMaterialization?.skipped?.length ? (
                        <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800 p-3 text-xs text-amber-900 dark:text-amber-100">
                          {lastMaterialization.skipped.join("; ")}
                        </div>
                      ) : null}
                      {diff ? (
                        <pre className="max-h-[28rem] overflow-auto rounded-md border bg-muted p-3 text-xs whitespace-pre-wrap">
                          {diff}
                        </pre>
                      ) : (
                        <div className="rounded-md border p-6 text-center text-sm text-muted-foreground">
                          提案を1件以上承認してから「差分を生成」を押すと、まとめて1つの差分が生成されます。
                        </div>
                      )}
                      {session.materialization_ref && (
                        <a className="inline-flex items-center text-sm underline" href={session.materialization_ref} target="_blank" rel="noreferrer">
                          <FileCode className="h-4 w-4 mr-1" />
                          Materialization リファレンスを開く
                        </a>
                      )}
                    </CardContent>
                  </Card>
                </>
              )}
            </div>

            {/* サイド: 理解の内容・セッション情報(補助的な表示) */}
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">セッション #{session.id}</CardTitle>
                  <CardDescription>
                    Snapshot {session.snapshot_id} · {formatTimestamp(session.updated_at)}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div className="grid grid-cols-3 gap-2">
                    <div className="rounded-md border p-2">
                      <div className="text-lg font-semibold">{proposals.length}</div>
                      <div className="text-xs text-muted-foreground">提案</div>
                    </div>
                    <div className="rounded-md border p-2">
                      <div className="text-lg font-semibold">{approvedCount}</div>
                      <div className="text-xs text-muted-foreground">承認済み</div>
                    </div>
                    <div className="rounded-md border p-2">
                      <div className="text-lg font-semibold">{pendingCount}</div>
                      <div className="text-xs text-muted-foreground">未処理</div>
                    </div>
                  </div>
                  {contextPack && (
                    <div className="rounded-md border p-3 space-y-2">
                      <div className="flex items-center justify-between">
                        <span className="font-medium">コンテキストパック</span>
                        <Badge variant={contextPack.truncated ? "warning" : "secondary"}>
                          {contextPack.budget_used_chars.toLocaleString()} chars
                        </Badge>
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {contextPack.total_symbols} symbols · {contextPack.total_entrypoints} entrypoints ·{" "}
                        {contextPack.unclassified_count} 未分類
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <CardTitle className="text-sm flex items-center gap-2">
                        <Layers className="h-4 w-4" /> 現在の理解
                      </CardTitle>
                      <CardDescription>
                        ドキュメントとコード分析から構築したシステム理解。
                      </CardDescription>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={refreshUnderstanding}
                      disabled={building}
                    >
                      <Sparkles className="h-4 w-4 mr-1" />
                      {updateUnderstanding.isPending ? "分析中..." : "理解を更新"}
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  {session.last_error && (
                    <div className="rounded-md border border-destructive bg-destructive/10 p-3 mb-3 text-sm text-destructive flex items-start gap-2">
                      <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                      <div>
                        <div className="font-medium">理解の構築に失敗しました</div>
                        <div className="text-xs mt-1">{session.last_error}</div>
                      </div>
                    </div>
                  )}
                  {session.current_understanding ? (
                    <UnderstandingPanel understanding={session.current_understanding} />
                  ) : !session.last_error ? (
                    <div className="text-sm text-muted-foreground text-center py-6" data-testid="no-understanding">
                      まだ理解は構築されていません。
                    </div>
                  ) : null}
                </CardContent>
              </Card>

              {session.open_questions && session.open_questions.length > 1 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm flex items-center gap-2">
                      <HelpCircle className="h-4 w-4" /> 残りの質問
                    </CardTitle>
                    <CardDescription>この後の確認で1つずつ質問されます。</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2" data-testid="open-questions-panel">
                      {sortQuestions(session.open_questions).slice(1).map((q, idx) => (
                        <div key={idx} className="rounded-md border p-3 flex items-start gap-3">
                          <HelpCircle className="h-4 w-4 mt-0.5 shrink-0 text-blue-500" />
                          <div className="min-w-0">
                            <span className="text-sm">{q.question}</span>
                            <div className="flex gap-1 mt-1">
                              <Badge variant="outline">{q.category}</Badge>
                              <Badge variant={q.priority === "high" ? "destructive" : q.priority === "medium" ? "warning" : "outline"}>
                                {q.priority}
                              </Badge>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}

              <UnderstandingDiffPanel
                sessionId={session.id}
                answerRevisionReflected={answerRevisionReflected}
              />

              <QaPanel sessionId={session.id} actor={actor} approvedCount={approvedCount} />

              {session.gap_analysis && session.gap_analysis.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm flex items-center gap-2">
                      <AlertCircle className="h-4 w-4" /> ギャップ分析
                    </CardTitle>
                    <CardDescription>ドキュメントとコードの差分。</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <GapAnalysisPanel gaps={session.gap_analysis} />
                  </CardContent>
                </Card>
              )}
            </div>
          </div>

          <Dialog open={!!editing && !!editForm} onOpenChange={(open) => { if (!open) { setEditing(null); setEditForm(null); } }}>
            {editing && editForm && (
              <>
                <DialogHeader>
                  <DialogTitle>提案の編集</DialogTitle>
                </DialogHeader>
                <div className="space-y-4">
                  <div className="text-xs font-mono text-muted-foreground">{editing.path}:{editing.qualified_name}</div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <Field label="Role" value={editForm.metadata.role} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, role: v } })} />
                    <Field label="Capability" value={editForm.metadata.capability} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, capability: v } })} />
                    <Field label="System purpose" value={editForm.metadata.system_purpose} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, system_purpose: v } })} />
                    <Field label="Probe value" value={editForm.metadata.probe_value} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, probe_value: v } })} />
                    <div>
                      <Label>Element type</Label>
                      <Select value={editForm.metadata.element_type} onChange={e => setEditForm({ ...editForm, metadata: { ...editForm.metadata, element_type: e.target.value as "" | SourceMetadataElementType } })}>
                        {ELEMENT_TYPES.map(v => <option key={v || "none"} value={v}>{v || "unset"}</option>)}
                      </Select>
                    </div>
                    <div>
                      <Label>Operation kind</Label>
                      <Select value={editForm.metadata.operation_kind} onChange={e => setEditForm({ ...editForm, metadata: { ...editForm.metadata, operation_kind: e.target.value as "" | SourceMetadataOperationKind } })}>
                        {OPERATION_KINDS.map(v => <option key={v || "none"} value={v}>{v || "unset"}</option>)}
                      </Select>
                    </div>
                    <Field label="Consumers" value={editForm.metadata.consumers} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, consumers: v } })} />
                    <Field label="State effects" value={editForm.metadata.state_effects} onChange={v => setEditForm({ ...editForm, metadata: { ...editForm.metadata, state_effects: v } })} />
                    <Field label="Feature id" value={editForm.probe_plan.feature_id} onChange={v => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, feature_id: v } })} />
                    <Field label="Objective" value={editForm.probe_plan.objective} onChange={v => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, objective: v } })} />
                    <div>
                      <Label>Mode</Label>
                      <Select value={editForm.probe_plan.recommended_mode} onChange={e => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, recommended_mode: e.target.value as ProbeRecommendedMode } })}>
                        {PROBE_MODES.map(v => <option key={v} value={v}>{v}</option>)}
                      </Select>
                    </div>
                    <div>
                      <Label>Risk</Label>
                      <Select value={editForm.probe_plan.side_effect_risk} onChange={e => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, side_effect_risk: e.target.value as ProbeSideEffectRisk } })}>
                        {RISK_LEVELS.map(v => <option key={v} value={v}>{v}</option>)}
                      </Select>
                    </div>
                    <div>
                      <Label>Replayability</Label>
                      <Select value={editForm.probe_plan.replayability} onChange={e => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, replayability: e.target.value as ProbeReplayability } })}>
                        {REPLAYABILITY.map(v => <option key={v} value={v}>{v}</option>)}
                      </Select>
                    </div>
                  </div>
                  <div>
                    <Label>Reason</Label>
                    <Textarea rows={4} value={editForm.probe_plan.reason} onChange={e => setEditForm({ ...editForm, probe_plan: { ...editForm.probe_plan, reason: e.target.value } })} />
                  </div>
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" onClick={() => { setEditing(null); setEditForm(null); }}>キャンセル</Button>
                    <Button onClick={saveEdit} disabled={edit.isPending}>
                      {edit.isPending ? "保存中..." : "修正を保存して承認"}
                    </Button>
                  </div>
                </div>
              </>
            )}
          </Dialog>
        </>
      )}
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <div>
      <Label>{label}</Label>
      <Input value={value} onChange={e => onChange(e.target.value)} />
    </div>
  );
}
