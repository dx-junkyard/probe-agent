import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertCircle, CheckCircle, Download, FileCode, GitPullRequest,
  HelpCircle, Layers, LifeBuoy, Loader2, MessageSquareText, Pencil, Play, RefreshCw, Send,
  Sparkles, XCircle,
} from "lucide-react";
import {
  useActiveInquiriesByOrigin,
  useAlignmentList,
  useAnswerInterviewQa,
  useApproveInterviewProposal,
  useConfirmInterviewUnderstanding,
  useCreateInterviewSession,
  useEditInterviewProposal,
  useInterviewApprovedSet,
  useInterviewCapabilityGraph,
  useInterviewContextPack,
  useInterviewDialogueTurn,
  useInterviewIntentList,
  useInterviewQaList,
  useInterviewSession,
  useInterviewSessions,
  useLatestSnapshot,
  useMaterializeInterview,
  useRebaseInterviewSnapshot,
  useRepositoryStatus,
  useRejectInterviewProposal,
  useCreateJointUnderstanding,
  useJointUnderstandingList,
  useResumeInterviewInquiry,
  useResumeInterviewQa,
  useQaAutoInvestigate,
  useRunRuntimeRealityCheck,
  type QaAutoInvestigateController,
  useSkipInterviewQa,
  useUnderstandingDiff,
  useUpdateInterviewUnderstanding,
  recordInterviewMetricEventBestEffort,
} from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { api } from "@/api/client";
import { DiagnosticFixCallout, useDiagnosticHighlight } from "@/components/diagnostic-fix";
import { UnderstandingOverview } from "@/components/system-understanding/understanding-overview";
import { IntentBriefPanel } from "@/components/system-understanding/intent-brief-panel";
import { ReviewQueuePanel } from "@/components/system-understanding/review-queue";
import { AnswerableAreasControl, isOutOfArea, knowledgeAreaLabel } from "@/components/system-understanding/answerable-areas";
import { HandoffListPanel, HandoffModal } from "@/components/system-understanding/handoff-panel";
import { ObservationProposalPanel } from "@/components/system-understanding/observation-proposal-panel";
import { ChangeSetPanel } from "@/components/system-understanding/change-set-panel";
import { InquiryPanel, ROUTE_CATEGORY_LABELS } from "@/components/system-understanding/inquiry-panel";
import { JointUnderstandingPanel } from "@/components/system-understanding/joint-understanding-panel";
import { RefreshStatusChip } from "@/components/system-understanding/refresh-status-chip";
import { InterviewMetricsPanel } from "@/components/system-understanding/interview-metrics-panel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { formatTimestamp } from "@/lib/utils";
import { buildPatchFilename, downloadTextFile } from "@/lib/patch";
import type {
  AlignmentItemOut,
  AlignmentListOut,
  CapabilityEntityKind,
  CurrentUnderstanding,
  IntelligenceRunEvidenceOut,
  InterviewMaterializeOut,
  InterviewCapabilityRelationConfirmation,
  InterviewProposalMetadataBlock,
  InterviewProposalOut,
  InterviewProposalProbePlan,
  InquiryRouteCategory,
  InterviewInquiryOut,
  InterviewIntentListOut,
  InterviewQaOut,
  InterviewQuestionEvidenceRef,
  InterviewSessionDetailOut,
  InterviewStage,
  KnowledgeArea,
  OpenQuestion,
  ProbeRecommendedMode,
  ProbeReplayability,
  ProbeSideEffectRisk,
  SourceMetadataElementType,
  SourceMetadataOperationKind,
  SourceMetadataStateEffect,
  UnderstandingDiffOut,
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

const CAPABILITY_SECTIONS: Array<[
  keyof Pick<
    CurrentUnderstanding,
    "core_capabilities" | "capability_elements" | "supporting_elements" | "api_boundaries"
  >,
  CapabilityEntityKind,
]> = [
  ["core_capabilities", "core_capability"],
  ["capability_elements", "capability_element"],
  ["supporting_elements", "supporting_element"],
  ["api_boundaries", "api_boundary"],
];

const CAPABILITY_CHILD_KINDS: Record<CapabilityEntityKind, CapabilityEntityKind[]> = {
  core_capability: ["capability_element", "supporting_element", "api_boundary"],
  capability_element: ["supporting_element", "api_boundary"],
  supporting_element: ["api_boundary"],
  api_boundary: [],
};

type CapabilityProposalNode = {
  kind: CapabilityEntityKind;
  name: string;
  children: string[];
};

function capabilityProposalNodes(
  understanding: CurrentUnderstanding | null | undefined,
): CapabilityProposalNode[] {
  if (!understanding) return [];
  return CAPABILITY_SECTIONS.flatMap(([section, kind]) =>
    (understanding[section] ?? []).map(item => ({
      kind,
      name: item.name,
      children: item.children ?? [],
    })),
  );
}

function capabilityProposalRelations(
  nodes: CapabilityProposalNode[],
): InterviewCapabilityRelationConfirmation[] {
  const relations: InterviewCapabilityRelationConfirmation[] = [];
  for (const parent of nodes) {
    for (const childName of parent.children) {
      for (const childKind of CAPABILITY_CHILD_KINDS[parent.kind]) {
        if (nodes.some(node => node.kind === childKind && node.name === childName)) {
          relations.push({
            supported_kind: parent.kind,
            supported_name: parent.name,
            supporting_kind: childKind,
            supporting_name: childName,
          });
        }
      }
    }
  }
  return relations;
}

function capabilityNodeKey(kind: CapabilityEntityKind, name: string): string {
  return `${kind}\u0000${name}`;
}

function capabilityRelationKey(
  relation: InterviewCapabilityRelationConfirmation,
): string {
  return [
    relation.supported_kind,
    relation.supported_name,
    relation.supporting_kind,
    relation.supporting_name,
  ].join("\u0000");
}

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
  // Issue #295 §4.8 review fix (Finding 4): the backing interview_qa row's
  // id, when this focused question came from an OpenQuestion carrying one
  // (Issue #129). Used to scope route-and-investigate to qa_ids=[qaId] for
  // this question's 「わからない」 auto-investigation. Absent for questions
  // with no backing row (e.g. the zero-base fixed questionnaire) -- those
  // fall back to the original #142 flow directly, never auto-investigated.
  qaId?: number | null;
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
    qaId: q.qa_id ?? null,
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

// PR #296 review fix (Finding 4): the banner sits above the main tabs, so a
// required conversation-tab action (see `conversationHasRequiredAction`
// below) must stay reachable regardless of which tab is currently shown --
// otherwise this banner can point at a CTA that lives in the other tab.
// `onGoToConversation` renders that escape hatch; callers only pass it (and
// `showGoToConversation`) when the currently displayed tab is NOT the
// conversation tab and a required action lives there.
function NextActionBanner({ uiState, nextAction, showGoToConversation, onGoToConversation }: {
  uiState: InterviewUiState;
  nextAction: string;
  showGoToConversation?: boolean;
  onGoToConversation?: () => void;
}) {
  return (
    <div
      className="rounded-md border bg-muted/40 p-3 flex items-start gap-3 flex-wrap sm:flex-nowrap"
      data-testid="next-action"
    >
      {uiState === "preparing" ? (
        <Loader2 className="h-4 w-4 mt-0.5 shrink-0 animate-spin text-primary" />
      ) : (
        <Sparkles className="h-4 w-4 mt-0.5 shrink-0 text-primary" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{UI_STATE_LABELS[uiState]}</Badge>
          <span className="text-xs font-medium text-muted-foreground">次にやること</span>
        </div>
        <p className="text-sm mt-1">{nextAction}</p>
      </div>
      {showGoToConversation && onGoToConversation && (
        <Button
          size="sm"
          variant="outline"
          className="shrink-0"
          onClick={onGoToConversation}
          data-testid="next-action-go-to-conversation"
        >
          <MessageSquareText className="h-4 w-4 mr-1" />
          会話タブへ移動
        </Button>
      )}
    </div>
  );
}

// Issue #295 PR #296 review restructure (Finding: Alignment Review layout):
// 調査結果(qa.investigation)の表示を Q&A一覧カード専用から切り出した
// 共有コンポーネント。focused question カード(画面中央)と Q&A一覧カード
// (サイドバー)の両方から同じ内容・同じ testid で表示できるようにする。
// `onTranscribe` を渡した場合だけ「回答欄に転記」ボタンを出す(focused
// question 側は会話の message 欄に、Q&A一覧側は回答ドラフトに転記する)。
function QaInvestigationBlock({
  qaId, investigation, routeCategory, onTranscribe,
}: {
  qaId: number;
  investigation: NonNullable<InterviewQaOut["investigation"]>;
  routeCategory?: InquiryRouteCategory | null;
  onTranscribe?: () => void;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  return (
    <div
      className="rounded-md border p-2 text-xs space-y-1 bg-sky-500/5"
      data-testid={`qa-investigation-${qaId}`}
    >
      {investigation.status === "completed" ? (
        <>
          <p className="font-medium">AIの調査結果: {investigation.conclusion}</p>
          {investigation.key_points.length > 0 && (
            <ul className="list-disc pl-4">
              {investigation.key_points.map((k, i) => <li key={i}>{k}</li>)}
            </ul>
          )}
          {routeCategory === "hybrid" && investigation.decision_question && (
            <p
              className="rounded border border-amber-500/60 bg-amber-500/10 px-2 py-1 font-medium text-amber-800"
              data-testid={`qa-investigation-decision-question-${qaId}`}
            >
              確認したいこと: {investigation.decision_question}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setShowEvidence(s => !s)}
              data-testid={`qa-investigation-show-evidence-${qaId}`}
            >
              {showEvidence ? "根拠を隠す" : "根拠を見る"}
            </Button>
            {onTranscribe && (
              <Button
                size="sm"
                variant="outline"
                onClick={onTranscribe}
                data-testid={`qa-investigation-transcribe-${qaId}`}
              >
                調査結果を回答欄に転記
              </Button>
            )}
          </div>
          {showEvidence && (
            <div
              className="mt-1 space-y-1 rounded bg-background/60 p-2"
              data-testid={`qa-investigation-evidence-${qaId}`}
            >
              {investigation.evidence.map((e, i) => (
                <p key={i} className="font-mono text-[10px] text-muted-foreground">
                  {e.path}:{e.start_line}-{e.end_line}{e.summary ? ` — ${e.summary}` : ""}
                </p>
              ))}
              {investigation.uncertainty && (
                <p className="text-muted-foreground">不確実な点: {investigation.uncertainty}</p>
              )}
            </div>
          )}
        </>
      ) : (
        <p className="text-muted-foreground italic" data-testid={`qa-investigation-unresolved-${qaId}`}>
          AIの調査では特定できませんでした
        </p>
      )}
    </div>
  );
}

// Q&A一覧パネル(Issue #129)。会話ログとは別に、質問・回答をIDベースで
// 一覧・編集・スキップできる。回答の修正は新しいリビジョン行として保存され、
// 旧回答も previous として残る(上書きしない)。
function QaItemCard({
  qa, sessionId, existingInquiry, onAnswer, onSkip, onResume, answering, skipping, resuming,
  outOfArea, investigate,
}: {
  qa: InterviewQaOut;
  sessionId: number;
  // Issue #285 refresh/resume: an already-active (open/held) Inquiry for
  // this question, rediscovered via the list endpoint so a page reload
  // never forgets an in-progress Inquiry.
  existingInquiry?: InterviewInquiryOut;
  onAnswer: (qaId: number, answerText: string, answerUnknown?: boolean) => Promise<void>;
  onSkip: (qaId: number) => void;
  onResume: (qaId: number) => void;
  answering: boolean;
  skipping: boolean;
  resuming: boolean;
  // Issue #291: rendered in the 「担当外の質問」 group -- offers a handoff
  // action in addition to the normal answer/skip actions.
  outOfArea?: boolean;
  // Issue #295 §4.8 / PR #296 review fix (Finding 4): the single shared
  // auto-investigation controller (one instance per session, created in
  // InterviewPage and passed down through QaPanel) -- never a per-card
  // instance, so an in-flight call from this card, another card, or the
  // focused-question card all share the same isPending flag.
  investigate: QaAutoInvestigateController;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(qa.answer_text ?? "");
  const [inquiryMode, setInquiryMode] = useState(false);
  const [hasHeldInquiry, setHasHeldInquiry] = useState(false);
  const [attachedInquiryId, setAttachedInquiryId] = useState<number | null>(null);
  const [handoffOpen, setHandoffOpen] = useState(false);
  // Issue #295 §4.8: true only while THIS card's 「わからない」 auto-
  // investigation is in flight (derived from the shared controller, not
  // local state) -- drives the short status line.
  const investigatingUnknown = investigate.investigatingQaId === qa.id;
  const resumeInquiry = useResumeInterviewInquiry(sessionId);

  // Epic #328: 「わからない」の続きとして、AI と一緒に状況を確かめる共同理解
  // セッション。既存の #142 / #295 フローは変えない — この導線は調査結果を
  // 見たあとの追加手段であり、開いても元の質問には一切回答しない。
  const jointList = useJointUnderstandingList(sessionId);
  const createJoint = useCreateJointUnderstanding(sessionId);
  const [jointId, setJointId] = useState<number | null>(null);
  const activeJoint = (jointList.data?.items ?? []).find(
    item => item.origin_kind === "qa" && item.origin_id === qa.id && item.status !== "closed",
  );
  const openJointId = jointId ?? activeJoint?.id ?? null;

  const startJointUnderstanding = () => {
    if (activeJoint) {
      setJointId(activeJoint.id);
      return;
    }
    createJoint.mutate(
      {
        origin_kind: "qa",
        origin_id: qa.id,
        trigger: qa.answer_unknown ? "unknown_answer" : "explicit_request",
        question_text: qa.question_text,
      },
      {
        onSuccess: result => setJointId(result.session.id),
        onError: e => toast.error(String(e)),
      },
    );
  };

  // Raw enum values are never rendered (Issue #266) -- only a known,
  // mapped label is shown; an unrecognized value renders no badge at all.
  const routeCategoryLabel = qa.route_category
    ? ROUTE_CATEGORY_LABELS[qa.route_category as InquiryRouteCategory]
    : undefined;

  useEffect(() => {
    void recordInterviewMetricEventBestEffort({
      schema_version: "interview-metric-event-v1",
      event_key: `question_presented:qa:${qa.id}`,
      session_id: sessionId,
      event_type: "question_presented",
      target_kind: "qa",
      target_id: qa.id,
    });
  }, [qa.id, sessionId]);

  const transcribeInvestigationConclusion = () => {
    if (!qa.investigation) return;
    setDraft(qa.investigation.conclusion);
    setEditing(true);
  };

  const heldInquiryId = hasHeldInquiry
    ? attachedInquiryId
    : (existingInquiry?.status === "held" ? existingInquiry.id : null);
  const reopenableInquiryId = existingInquiry?.status === "open" ? existingInquiry.id : null;

  const handleResumeInquiry = () => {
    const id = heldInquiryId;
    if (!id) return;
    resumeInquiry.mutate({ inquiryId: id }, {
      onSuccess: () => {
        setAttachedInquiryId(id);
        setHasHeldInquiry(false);
        setInquiryMode(true);
      },
      onError: e => toast.error(String(e)),
    });
  };

  const openExistingInquiry = () => {
    if (!reopenableInquiryId) return;
    setAttachedInquiryId(reopenableInquiryId);
    setInquiryMode(true);
  };

  const submit = async () => {
    if (!draft.trim()) return;
    await onAnswer(qa.id, draft.trim());
    setEditing(false);
  };

  // Issue #142: 「わからない」を有効な入力として記録する。エラーにはせず、
  // status=unconfirmed として保存し、以後の推論で仮説→再確認に回す。
  const fallBackToUnknownFlow = async () => {
    await onAnswer(qa.id, draft.trim(), true);
    setEditing(false);
  };

  // Issue #295 §4.8 / PR #296 review fix (Finding 4): 「わからない」を選ん
  // だら、まず共有の自動調査コントローラ(useQaAutoInvestigate、「AIに先に
  // 調査させる」ボタンと同じ基盤)に、この質問だけ(qa_ids=[qa.id])を対象
  // にした調査を依頼する。投稿された結果が qa.investigation に反映されれば
  // (下の qa.investigation ブロックが自動で結論を表示する)、#142 の仮説生
  // 成フローには入らず既存の確認導線(回答する/わからない/疑問がある)に戻
  // すだけにする。調査が使えない(失敗・対象外・バッチの上限で処理されな
  // かった)場合は、ユーザーが回答する機会を失わないよう、必ず従来の #142
  // フローにフォールバックする。
  const submitUnknown = async () => {
    if (investigate.isPending) return;
    const investigated = await investigate.runForQuestion(qa.id);
    if (investigated) {
      // AIの調査結果 (qa.investigation) は qa 一覧の再取得で表示される。
      // 元の回答は一切送信せず、既存の確認導線に戻すだけ。
      setEditing(false);
      return;
    }
    await fallBackToUnknownFlow();
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
          {outOfArea && (
            <Badge variant="warning" data-testid={`qa-out-of-area-${qa.id}`}>
              担当外({knowledgeAreaLabel(qa.knowledge_area)})
            </Badge>
          )}
          {routeCategoryLabel && (
            <Badge variant="secondary" data-testid={`qa-route-category-${qa.id}`}>
              {routeCategoryLabel}
            </Badge>
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

      {qa.investigation && (
        <QaInvestigationBlock
          qaId={qa.id}
          investigation={qa.investigation}
          routeCategory={qa.route_category as InquiryRouteCategory}
          onTranscribe={transcribeInvestigationConclusion}
        />
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

      {openJointId && (
        <JointUnderstandingPanel
          sessionId={sessionId}
          juId={openJointId}
          onClosed={() => setJointId(null)}
        />
      )}

      {inquiryMode ? (
        <InquiryPanel
          key={attachedInquiryId ?? "new"}
          sessionId={sessionId}
          originKind="qa"
          originId={qa.id}
          heldDraft={draft || null}
          existingInquiryId={attachedInquiryId ?? undefined}
          onResolved={heldDraft => {
            setDraft(heldDraft ?? "");
            setInquiryMode(false);
            setHasHeldInquiry(false);
            setAttachedInquiryId(null);
            setEditing(true);
          }}
          onHeld={heldId => {
            setInquiryMode(false);
            setHasHeldInquiry(true);
            setAttachedInquiryId(heldId);
          }}
          onCancel={() => { setInquiryMode(false); setAttachedInquiryId(null); }}
        />
      ) : (
        <>
          {heldInquiryId && (
            <div className="flex items-center gap-2" data-testid={`qa-held-inquiry-marker-${qa.id}`}>
              <p className="text-xs text-amber-700">保留中の疑問があります</p>
              <Button
                size="sm"
                variant="outline"
                onClick={handleResumeInquiry}
                disabled={resumeInquiry.isPending}
                data-testid={`qa-inquiry-resume-${qa.id}`}
              >
                疑問を再開する
              </Button>
            </div>
          )}
          {editing ? (
            <div className="space-y-2">
              <Textarea
                value={draft}
                onChange={e => setDraft(e.target.value)}
                rows={3}
                placeholder="回答を入力"
              />
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={submit} disabled={answering || !draft.trim() || investigatingUnknown}>
                  {answering ? "送信中..." : "保存"}
                </Button>
                {investigatingUnknown ? (
                  // Issue #295 §4.11: a single short Japanese status line only
                  // -- no log stream -- while the auto-investigation runs.
                  <p
                    className="text-xs text-muted-foreground"
                    data-testid={`qa-answer-unknown-investigating-${qa.id}`}
                  >
                    関連コードとテストを確認しています
                  </p>
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={submitUnknown}
                    disabled={answering || investigate.isPending}
                    data-testid={`qa-answer-unknown-${qa.id}`}
                  >
                    わからない
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setEditing(false)}
                  disabled={investigatingUnknown}
                >
                  キャンセル
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
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
              {outOfArea && qa.status === "open" && !qa.handoff_id && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setHandoffOpen(true)}
                  data-testid={`qa-handoff-open-${qa.id}`}
                >
                  担当者へ引き継ぐ
                </Button>
              )}
              {!openJointId && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={startJointUnderstanding}
                  disabled={createJoint.isPending}
                  data-testid={`qa-joint-understanding-${qa.id}`}
                >
                  一緒に確かめる
                </Button>
              )}
              {reopenableInquiryId ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={openExistingInquiry}
                  data-testid={`qa-inquiry-reopen-${qa.id}`}
                >
                  疑問を再開する
                </Button>
              ) : (
                !heldInquiryId && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => { setAttachedInquiryId(null); setInquiryMode(true); }}
                    data-testid={`qa-inquiry-open-${qa.id}`}
                  >
                    疑問がある
                  </Button>
                )
              )}
            </div>
          )}
        </>
      )}
      {outOfArea && (
        <HandoffModal
          sessionId={sessionId}
          originKind="qa"
          originId={qa.id}
          defaultBackground={qa.question_text}
          defaultEvidence={qa.evidence_refs.map(e => ({
            path: e.path, start_line: e.start_line, end_line: e.end_line, summary: "",
          }))}
          open={handoffOpen}
          onOpenChange={setHandoffOpen}
        />
      )}
    </div>
  );
}

// Exported for focused component testing (Issue #291's out-of-area
// grouping) without rendering the entire InterviewPage.
export function QaPanel({
  sessionId, actor, approvedCount, answerableAreas, investigate,
}: {
  sessionId: number;
  actor: string;
  approvedCount: number;
  // Issue #291: the session's current answerable-areas selection, used only
  // to group out-of-area questions separately -- never to hide them.
  // Defensively accepts undefined (a stale cached/mocked session shape).
  answerableAreas: KnowledgeArea[] | null | undefined;
  // Issue #295 §4.8 / PR #296 review fix (Finding 4): the single shared
  // auto-investigation controller, created once per session by the caller
  // (InterviewPage) and also used by the focused-question card there --
  // never instantiated locally here, so the two UI surfaces share one
  // in-flight state (see QaAutoInvestigateController's doc comment).
  investigate: QaAutoInvestigateController;
}) {
  const { data: qaList } = useInterviewQaList(sessionId);
  const answer = useAnswerInterviewQa(sessionId);
  const skip = useSkipInterviewQa(sessionId);
  const resume = useResumeInterviewQa(sessionId);
  const runRealityCheck = useRunRuntimeRealityCheck(sessionId);
  // Issue #285 refresh/resume: re-attach any still-active Inquiry to its
  // origin card after a reload.
  const activeInquiries = useActiveInquiriesByOrigin(sessionId);

  // Issue #286 review fix (Finding 1): batch-routes + investigates open
  // questions in the normal Q&A flow instead of leaving Question Router /
  // Investigation Agent reachable only from the Inquiry side-conversation.
  // Unrestricted (no qa_ids) -- the whole-session batch, distinct from each
  // card's single-question auto-investigate below.
  const handleRouteAndInvestigate = async () => {
    try {
      const result = await investigate.runBulk();
      toast.success(
        `分類 ${result.counts.routed} 件・調査 ${result.counts.investigated} 件が完了しました`,
      );
    } catch (e) {
      toast.error(String(e));
    }
  };

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

  // Issue #291: out-of-area questions are never hidden -- only grouped
  // separately under 「担当外の質問」, excluded from the primary list.
  const inAreaItems = qaList.items.filter(qa => !isOutOfArea(qa.knowledge_area, answerableAreas));
  const outOfAreaItems = qaList.items.filter(qa => isOutOfArea(qa.knowledge_area, answerableAreas));

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
          <div className="flex items-center gap-2">
            {qaList.open_count > 0 && (
              <Button
                size="sm"
                variant="outline"
                onClick={handleRouteAndInvestigate}
                disabled={investigate.isPending}
                data-testid="route-and-investigate-qa"
              >
                {investigate.isPending ? "調査中..." : "AIに先に調査させる"}
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={handleRuntimeRealityCheck}
              disabled={runRealityCheck.isPending || approvedCount === 0}
              data-testid="run-runtime-reality-check"
              title={approvedCount === 0 ? "先に提案を1件以上承認してください" : undefined}
            >
              {runRealityCheck.isPending ? "実行中..." : "実態チェックを実行"}
            </Button>
          </div>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {approvedCount === 0 && (
          <p className="text-xs text-muted-foreground" data-testid="runtime-reality-prerequisite">
            実態チェックには承認済みの提案が必要です。先に提案を確認して少なくとも1件を承認してください。
          </p>
        )}
        {qaList.answers_revised_at && (
          <div
            className="rounded-md border border-amber-500 bg-amber-500/10 p-3 text-sm flex items-start gap-2"
            data-testid="answers-revised-banner"
          >
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0 text-amber-600" />
            <div>
              回答が修正されました。理解は自動で更新されます。更新状況は「現在の理解」/
              「レビューキュー」のステータスをご確認ください(失敗時は再試行できます)。
            </div>
          </div>
        )}
        <div className="space-y-2 max-h-[26rem] overflow-y-auto pr-1">
          {inAreaItems.map(qa => (
            <QaItemCard
              key={qa.id}
              qa={qa}
              sessionId={sessionId}
              existingInquiry={activeInquiries.get(`qa:${qa.id}`)}
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
              investigate={investigate}
            />
          ))}
        </div>

        {outOfAreaItems.length > 0 && (
          <div className="pt-2 border-t space-y-2" data-testid="qa-out-of-area-group">
            <p className="text-xs font-semibold text-muted-foreground">
              担当外の質問({outOfAreaItems.length} 件)
            </p>
            <div className="space-y-2 max-h-[20rem] overflow-y-auto pr-1">
              {outOfAreaItems.map(qa => (
                <QaItemCard
                  key={qa.id}
                  qa={qa}
                  sessionId={sessionId}
                  existingInquiry={activeInquiries.get(`qa:${qa.id}`)}
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
                  investigate={investigate}
                  outOfArea
                />
              ))}
            </div>
          </div>
        )}
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

// PR #296 レビュー指摘対応(画面構造の再構成 / 指摘5a): Alignment Review の
// 主領域上部に置く「あなたが実現したいこと / システムの現状 / ギャップ」
// サマリ。Intent Brief 編集・現在の理解の詳細はサイドバーの既存パネルに
// 任せ、ここでは全体量を一目で把握できる短い読み取り専用サマリだけを表示
// する(Issue #295 §6 の推奨構成)。新しい判断ロジックは持たず、既存の
// intent list / current understanding / alignment items をそのまま要約
// するだけ。
//
// 指摘5a: 「ギャップ」を件数だけでなく、最重要 gap の名称・要約を主に表示
// する。候補は alignment_state==='gap' の項目、または must_review 項目
// (gap_summary があればそれを、無ければ current_claim を使う)。並び順は
// Review Queue と同じカテゴリ優先度(must_review → batch_reviewable、
// CATEGORY_SUMMARY と同じ固定順)+ id昇順というこのコードベース既存の
// 決定的タイブレーク(review-queue.tsx §5.4 のサンプル抽出と同じ規則)を
// 再利用するだけで、新しい並び替えロジックは追加しない。
//
// 件数には、バックエンドが返す outstanding_counts(未対応件数 = superseded
// でなく answered/corrected でもない件数、Review Queue のカード数と一致)
// があればそれを優先し、無ければ従来の counts(総数)にフォールバックする
// (指摘3のフロント側整合)。
function AlignmentSummaryHeader({
  intentList, understanding, alignment,
}: {
  intentList: InterviewIntentListOut | null | undefined;
  understanding: CurrentUnderstanding | null | undefined;
  alignment: AlignmentListOut | null | undefined;
}) {
  const goalItems = intentList?.items_by_field["goal"] ?? [];
  // Issue #295 が維持を求める「AI推定と人間確認済み情報の区別」: 確認済みの
  // goal だけを「あなたが実現したいこと」として提示する。確認済みが無く AI
  // 提案(未確認)しか無い場合は、それを人間のIntentとして出さず、未確認候補
  // として明示ラベル付きで表示する(承認前の提案を確定情報に見せない)。
  const confirmedGoal = goalItems.find(i => i.status === "confirmed");
  const proposedGoal = confirmedGoal
    ? undefined
    : goalItems.find(i => i.status === "proposed" && i.origin === "ai_proposed");
  const purposeNames = (understanding?.system_purpose ?? [])
    .map(i => i.name)
    .filter(Boolean)
    .slice(0, 3);
  const counts = alignment?.counts;
  const outstanding = alignment?.outstanding_counts;
  const mustReview = outstanding?.must_review ?? counts?.must_review ?? 0;
  const batchReviewable = outstanding?.batch_reviewable ?? counts?.batch_reviewable ?? 0;

  // アウトスタンディング(未対応)のギャップだけを候補にする -- 既に
  // answered/corrected な行や superseded な行の古いギャップ文言を「最重要
  // ギャップ」として出さないため、Review Queue が実際に action card として
  // 出す条件(status not answered/corrected, not superseded)と同じ絞り込み
  // を先にかける。
  const byIdAscending = (a: AlignmentItemOut, b: AlignmentItemOut) => a.id - b.id;
  const isOutstanding = (item: AlignmentItemOut) =>
    item.status !== "answered" && item.status !== "corrected" && !item.superseded;
  const mustReviewItems = [...(alignment?.items_by_category["must_review"] ?? [])]
    .filter(isOutstanding).sort(byIdAscending);
  const batchReviewableItems = [...(alignment?.items_by_category["batch_reviewable"] ?? [])]
    .filter(isOutstanding).sort(byIdAscending);
  const gapItems = [...mustReviewItems, ...batchReviewableItems]
    .filter(item => item.alignment_state === "gap" || item.review_category === "must_review");
  const gapTexts = gapItems.map(item => item.gap_summary || item.current_claim);
  const topGapTexts = gapTexts.slice(0, 2);
  const remainingGapCount = Math.max(gapTexts.length - topGapTexts.length, 0);

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3" data-testid="alignment-summary-header">
      <div className="rounded-md border p-3 space-y-1" data-testid="alignment-summary-goal">
        <p className="text-[10px] font-semibold uppercase text-muted-foreground">あなたが実現したいこと</p>
        {confirmedGoal ? (
          <p className="text-sm break-words" data-testid="alignment-summary-goal-confirmed">
            {confirmedGoal.value_text}
          </p>
        ) : proposedGoal ? (
          <div className="space-y-1" data-testid="alignment-summary-goal-proposed">
            <span className="inline-block rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
              AI提案 · 未確認
            </span>
            <p className="text-sm break-words text-muted-foreground">{proposedGoal.value_text}</p>
            <p className="text-[11px] text-muted-foreground">
              まだ確認されていません。Intent Briefで確認・修正してください。
            </p>
          </div>
        ) : (
          <p className="text-sm break-words text-muted-foreground">
            未入力です(Intent Briefで入力してください)
          </p>
        )}
      </div>
      <div className="rounded-md border p-3 space-y-1" data-testid="alignment-summary-current-state">
        <p className="text-[10px] font-semibold uppercase text-muted-foreground">システムの現状</p>
        <p className="text-sm break-words">
          {purposeNames.length > 0 ? purposeNames.join("、") : "現在の理解はまだ構築されていません"}
        </p>
      </div>
      <div className="rounded-md border p-3 space-y-1" data-testid="alignment-summary-gap">
        <p className="text-[10px] font-semibold uppercase text-muted-foreground">ギャップ</p>
        {topGapTexts.length > 0 ? (
          <div className="space-y-0.5" data-testid="alignment-summary-gap-list">
            {topGapTexts.map((text, i) => (
              <p key={i} className="text-sm break-words" data-testid={`alignment-summary-gap-item-${i}`}>
                {text}
              </p>
            ))}
            {remainingGapCount > 0 && (
              <p className="text-xs text-muted-foreground">ほか {remainingGapCount}件</p>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">未確認のギャップはありません</p>
        )}
        <p className="text-xs text-muted-foreground" data-testid="alignment-summary-gap-count">
          要確認 {mustReview}件 · 一括レビュー可 {batchReviewable}件
        </p>
      </div>
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
  const priorCapabilityGraphQuery = useInterviewCapabilityGraph(
    session?.capability_graph_confirmation_required
      ? selectedSessionId
      : null,
  );
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
  // Issue #295 §4.8 / PR #296 review fix (Finding 4): one shared
  // auto-investigation controller per session, used both by the
  // focused-question card below and by QaPanel/QaItemCard (passed down as a
  // prop) -- see QaAutoInvestigateController's doc comment in api/hooks.ts.
  const qaAutoInvestigate = useQaAutoInvestigate(selectedSessionId);
  // PR #296 review restructure: the same interview_qa list QaPanel already
  // fetches, read here too (react-query dedups the identical queryKey) so
  // the focused-question card can show a just-investigated question's
  // qa.investigation in the same view instead of only inside the Q&A list.
  const { data: qaListForFocus } = useInterviewQaList(selectedSessionId);
  // PR #296 review restructure: Alignment Review (Intent Brief summary +
  // Review Queue) moves into the main, tabbed content area. `alignmentFull`
  // (same GET .../alignment ReviewQueuePanel already reads) decides whether
  // this session has anything to review yet; `intentList` feeds the
  // read-only "あなたが実現したいこと" summary line.
  const { data: alignmentFull } = useAlignmentList(selectedSessionId);
  const { data: intentList } = useInterviewIntentList(selectedSessionId);
  const proposedCapabilityNodes = useMemo(
    () => capabilityProposalNodes(session?.current_understanding),
    [session?.current_understanding],
  );
  const proposedCapabilityRelations = useMemo(
    () => capabilityProposalRelations(proposedCapabilityNodes),
    [proposedCapabilityNodes],
  );
  const unmatchedCapabilityNodes = useMemo(() => {
    const priorNodes = priorCapabilityGraphQuery.data?.nodes ?? [];
    return proposedCapabilityNodes.filter(node =>
      !priorNodes.some(
        prior => prior.entity_kind === node.kind && prior.name === node.name,
      ),
    );
  }, [priorCapabilityGraphQuery.data?.nodes, proposedCapabilityNodes]);

  const [message, setMessage] = useState("");
  const [capabilityConfirmOpen, setCapabilityConfirmOpen] = useState(false);
  const [capabilityIdentitySelections, setCapabilityIdentitySelections] = useState<
    Record<string, string>
  >({});
  const [capabilityRelationSelections, setCapabilityRelationSelections] = useState<
    Record<string, boolean>
  >({});
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
  // PR #296 review restructure: which main-area tab the user explicitly
  // picked. Scoped by sessionId (same pattern as answerRevisionReflectedState
  // above) rather than reset via an effect, so switching sessions never
  // carries over a stale manual pick from a different session.
  const [manualMainTabState, setManualMainTabState] = useState<{
    sessionId: number | null;
    value: "alignment" | "conversation";
  } | null>(null);

  const sortedSessions = useMemo(() => sessions ?? [], [sessions]);
  const proposals = session?.proposals ?? [];
  const pendingCount = proposals.filter(p => p.approval_state === "proposed").length;
  const approvedCount = approvedSet?.approved_count ?? 0;
  const diff = lastMaterialization?.diff || session?.materialization_diff || "";
  // The generated patch applies to the session's pinned snapshot. Prefer the
  // current materialization response, then fall back to the session detail.
  const patchCommitSha = lastMaterialization?.commit_sha || session?.snapshot_commit_sha || null;
  const currentStage = session?.stage ?? "understanding_initialized";
  const isProposalStage = currentStage === "proposal_generation";
  const sessionSnapshotStale = !!(
    session && latestSnapshot && session.snapshot_id !== latestSnapshot.id
  );
  const repositoryHeadStale = !!(session && repositoryStatus?.snapshot_stale);

  const building = createSession.isPending || updateUnderstanding.isPending;
  const uiState: InterviewUiState | null = session ? deriveUiState(session, building) : null;
  const unlocked = session ? proposalsUnlocked(session) : false;
  // A confirmed proposal-stage session is already at its next workflow step.
  // Rebuild only once new developer input (an answer correction, or a
  // first-time / Runtime-Reality-Check Q&A answer given after confirmation)
  // is waiting to be reflected. `understanding_update_available` is the
  // server's own evaluation of this same condition (Issue #229/#263's
  // shared `_understanding_update_blocked` predicate) so this flag can never
  // drift from what `update-understanding` will actually accept.
  const canRefreshUnderstanding = session ? session.understanding_update_available : true;
  const refreshBlockedUntilAnswerRevision = (
    isProposalStage
    && session?.understanding_confirmed_at != null
    && !canRefreshUnderstanding
  );
  const purposeFixHighlight = useDiagnosticHighlight<HTMLDivElement>("interview-purpose");
  const capabilitiesFixHighlight = useDiagnosticHighlight<HTMLDivElement>("interview-capabilities");
  // 提案ステージで未回答の絞り込み質問が残っている状態。提案生成を依頼しても
  // 情報不足だった場合、モデルの確認質問が open_questions に残る。
  const proposalNarrowing =
    uiState === "ready_for_proposals" && (session?.open_questions ?? []).length > 0;
  // 「この理解を確認済みにする」ボタン(会話タブ内)がまだ有効な状態かどうか。
  // uiState の判定より先に定義し、下の会話タブ必須アクション判定から参照する。
  const canConfirmStructuredUnderstanding = !!(
    session?.current_understanding
    && (
      session.understanding_confirmed_at == null
      || session.capability_graph_confirmation_required === true
    )
  );

  // PR #296 review restructure: 「build 済み(=突き合わせ項目が1件以上あ
  // る)」を、現在の GET .../alignment レスポンスの実項目数から決定的に
  // 判定する(サーバーに専用フラグは無い)。superseded_items は履歴だが
  // 「一度は build された」事実には変わらないため合算する。
  const alignmentItemCounts = alignmentFull?.counts;
  const alignmentOutstandingCounts = alignmentFull?.outstanding_counts;
  const totalAlignmentItems = alignmentFull
    ? Object.values(alignmentFull.items_by_category).reduce((n, arr) => n + (arr?.length ?? 0), 0)
      + (alignmentFull.superseded_items?.length ?? 0)
    : 0;
  const alignmentBuilt = totalAlignmentItems > 0;
  // 指摘3/5b のフロント側整合: 実行可能(actionable)カテゴリの件数は
  // outstanding_counts(未対応件数)を優先し、Review Queue のカード数と一致
  // させる。古い Control Server(outstanding_counts 未対応)では従来どおり
  // counts(総数)にフォールバックする。
  const alignmentActionableCount =
    (alignmentOutstandingCounts?.must_review ?? alignmentItemCounts?.must_review ?? 0)
    + (alignmentOutstandingCounts?.batch_reviewable ?? alignmentItemCounts?.batch_reviewable ?? 0);

  // PR #296 review fix (Finding 4): 「build 済みだから既定は Alignment
  // Review」という以前の判定は、会話タブ側にまだ必須操作が残っている状態
  // (例: 初回の理解確認・不足情報への回答・ゼロベース質問・提案生成待ち)
  // でも Alignment Review を既定にしてしまい、次にやること(NextActionBanner)
  // が指す操作が別タブに隠れる問題があった。
  //
  // proposal_review は状態名だけでは「必須操作あり」とは限らない。未レビュー
  // (proposed/needs_review)が残る、または承認済み提案から差分を生成できる間は
  // 会話タブに操作がある。一方、全提案を却下済みで approved set も空なら
  // 会話側の操作は完了しており、build 済み Alignment Review を既定表示できる。
  // uiState の truthiness をそのまま使わず、各状態が実際に持つ CTA から有限に
  // 導出することで Alignment 自動既定が到達不能になるのを防ぐ。
  const proposalReviewHasRequiredAction = (
    uiState === "proposal_review"
    && (
      proposals.some(p => proposalReviewable(p.approval_state))
      || proposals.some(p => p.approval_state === "approved" || p.approval_state === "edited")
      || approvedCount > 0
    )
  );
  const conversationHasRequiredAction = !!(
    canConfirmStructuredUnderstanding
    || uiState === "preparing"
    || uiState === "needs_build"
    || uiState === "confirm_understanding"
    || uiState === "fill_gaps"
    || uiState === "zero_base"
    || uiState === "ready_for_proposals"
    || proposalReviewHasRequiredAction
  );
  // ユーザーが明示的にタブを切り替えた場合はそちらを優先するが、その選択は
  // 選択中のセッションに限って有効にする(別セッションへ切り替えたときに
  // 古い選択を持ち越さない)。
  const manualMainTab =
    manualMainTabState?.sessionId === selectedSessionId ? manualMainTabState.value : null;
  const mainTab: "alignment" | "conversation" =
    manualMainTab ?? (alignmentBuilt && !conversationHasRequiredAction ? "alignment" : "conversation");
  // 指摘4: どのタブを見ていても必須操作(会話タブ)へ到達できるよう、必須
  // アクションが会話タブ側にあり、かつ今表示中のタブが会話タブでない場合に
  // NextActionBanner から会話タブへ切り替える導線を出す。
  const showGoToConversationInBanner = conversationHasRequiredAction && mainTab !== "conversation";
  const goToConversationTab = () => {
    setManualMainTabState({ sessionId: selectedSessionId, value: "conversation" });
  };

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
    if (canConfirmStructuredUnderstanding) {
      return "右側の「現在の理解」パネルに表示されているシステム理解を確認し、問題なければ「この理解を確認済みにする」を押してください。";
    }
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
  }, [uiState, approvedCount, zeroBaseComplete, proposalNarrowing, canConfirmStructuredUnderstanding]);

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

  // Issue #295 §4.8 review fix (Finding 4): 画面中央の focused question の
  // 「わからない」にも、QaItemCard 側と同じ共有コントローラ(qaAutoInvestigate)
  // を接続する。この質問を裏付ける interview_qa 行の qa_id が分かる場合のみ
  // qa_ids=[qaId] で自動調査を依頼する。
  // PR #296 review restructure: 投稿された調査結果(qa.investigation)は
  // qaListForFocus(QaPanel と同じ interview_qa 一覧、react-query がキャッ
  // シュを共有)から同じ qa_id の行を探して、この focused question のすぐ
  // 下に同じ QaInvestigationBlock で表示する -- もう「Q&A一覧を見に行っ
  // て」というトースト誘導だけには頼らない。qa_id が無い(ゼロベースの固
  // 定質問など)場合や調査が使えなかった場合は、必ず従来の #142 フロー
  // (sendUnknown)にフォールバックする。
  const focusedQuestionInvestigating = !!(
    focusedQuestion?.qaId != null && qaAutoInvestigate.investigatingQaId === focusedQuestion.qaId
  );
  const focusedQa = focusedQuestion?.qaId != null
    ? qaListForFocus?.items.find(qa => qa.id === focusedQuestion.qaId)
    : undefined;
  const handleFocusedUnknown = async () => {
    const qaId = focusedQuestion?.qaId;
    if (qaId == null) {
      // 対象外(裏付けとなる質問行が無い): 従来の #142 フローへ直接進む。
      sendUnknown();
      return;
    }
    // 既に他のカードからの自動調査が進行中: 二重発火させず、ボタンが
    // disabled になっている間はここで何もしない(#142へは進めない -- ユー
    // ザーの操作機会は失わない。再クリックすれば良いだけ)。
    if (qaAutoInvestigate.isPending) return;
    const investigated = await qaAutoInvestigate.runForQuestion(qaId);
    if (investigated) {
      toast.info("AIが調査しました。調査結果をこの画面で確認してください。");
      return;
    }
    sendUnknown();
  };

  // 「いいえ」は修正内容の入力を促す: 定型の書き出しを入力欄に入れてフォーカスする。
  const startCorrection = () => {
    setMessage(QUICK_ANSWER_NO_PREFIX);
    messageInputRef.current?.focus();
  };

  const submitUnderstandingConfirmation = async (includeCapabilityReview: boolean) => {
    if (!selectedSessionId) return;
    try {
      await confirmUnderstanding.mutateAsync({
        actor,
        ...(includeCapabilityReview
          ? {
              capability_base_confirmation_id:
                priorCapabilityGraphQuery.data?.confirmation_id ?? null,
              capability_relations: proposedCapabilityRelations.filter(
                relation =>
                  capabilityRelationSelections[capabilityRelationKey(relation)] !== false,
              ),
              capability_identity_bindings: unmatchedCapabilityNodes.flatMap(node => {
                const selected = capabilityIdentitySelections[
                  capabilityNodeKey(node.kind, node.name)
                ];
                return selected
                  ? [{
                      entity_kind: node.kind,
                      current_name: node.name,
                      entity_id: Number(selected),
                    }]
                  : [];
              }),
            }
          : {}),
      });
      setCapabilityConfirmOpen(false);
      toast.success("内容を確定しました。提案を生成できます。");
    } catch (e) {
      toast.error(String(e));
    }
  };

  const doConfirmUnderstanding = () => {
    if (
      canConfirmStructuredUnderstanding
      && session?.capability_graph_confirmation_required === true
    ) {
      setCapabilityIdentitySelections({});
      setCapabilityRelationSelections(
        Object.fromEntries(
          proposedCapabilityRelations.map(relation => [
            capabilityRelationKey(relation),
            true,
          ]),
        ),
      );
      setCapabilityConfirmOpen(true);
      return;
    }
    void submitUnderstandingConfirmation(false);
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
      toast.success(`${result.items_materialized}件を差分化しました。内容をレビューし、.patch の保存と適用へ進めます。`);
    } catch (e) {
      toast.error(String(e));
    }
  };

  const downloadPatch = () => {
    if (!diff || !session) return;
    downloadTextFile(
      diff,
      buildPatchFilename({
        systemId: session.system_id,
        sessionId: session.id,
        snapshotId: session.snapshot_id,
        commitSha: patchCommitSha,
      }),
    );
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

      <InterviewMetricsPanel />

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

          {uiState && (
            <NextActionBanner
              uiState={uiState}
              nextAction={nextActionText}
              showGoToConversation={showGoToConversationInBanner}
              onGoToConversation={goToConversationTab}
            />
          )}

          <Card>
            <CardContent className="py-3">
              <ProgressSteps current={currentStage} />
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-4">
            {/* メイン: PR #296 レビュー指摘対応 -- Alignment Review(意図と
                現状の突き合わせをまとめて判断する)と会話(focused question /
                自由入力)をタブで切り替える主操作領域。
                指摘4: 「build 済みなら Alignment Review を既定にする」だけ
                では、会話タブ側にまだ必須操作(初回の理解確認・不足情報へ
                の回答・ゼロベース質問・提案生成待ちなど)が残っている間も
                Alignment Review が既定になり、NextActionBanner の CTA が
                別タブに隠れてしまっていた。既定タブは
                `conversationHasRequiredAction`(各 uiState の実 CTA と
                canConfirmStructuredUnderstanding から決定的に導出、新しい
                サーバーフラグは追加しない)が false -- つまり会話タブでの
                必須操作が残っていない -- かつ build 済みのときに限り
                Alignment Review にする(Issue #295 §6)。proposal_review
                でも proposed/needs_review、または承認済み差分生成が残る間は
                会話タブを既定にするが、全却下済みなら Alignment 自動既定へ
                到達する。どちらのタブも常に到達でき、会話タブに必須操作が
                残る間は NextActionBanner に「会話タブへ移動」ボタンが出る。 */}
            <div className="space-y-4">
              <Tabs
                value={mainTab}
                onValueChange={v => setManualMainTabState({
                  sessionId: selectedSessionId, value: v as "alignment" | "conversation",
                })}
              >
                <TabsList>
                  <TabsTrigger value="alignment" data-testid="main-tab-alignment">
                    Alignment Review
                    {alignmentActionableCount > 0 && ` (${alignmentActionableCount})`}
                  </TabsTrigger>
                  <TabsTrigger value="conversation" data-testid="main-tab-conversation">会話</TabsTrigger>
                </TabsList>

                <TabsContent value="alignment" className="space-y-4" data-testid="main-tab-content-alignment">
                  <Card data-testid="alignment-review-panel">
                    <CardHeader>
                      <CardTitle className="text-sm">Alignment Review</CardTitle>
                      <CardDescription>
                        あなたが実現したいこと(Intent Brief)と現在の理解を突き合わせ、確認が必要な項目をまとめて判断します。
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      <AlignmentSummaryHeader
                        intentList={intentList}
                        understanding={session.current_understanding}
                        alignment={alignmentFull}
                      />
                    </CardContent>
                  </Card>
                  <ReviewQueuePanel sessionId={session.id} />
                </TabsContent>

                <TabsContent value="conversation" className="space-y-4" data-testid="main-tab-content-conversation">
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
                                  提案ステージの絞り込み質問でも同様に使える。
                                  Issue #295 §4.8 review fix (Finding 4): まず共有の自動調査
                                  コントローラでこの質問(qa_ids=[qaId])を調査し、投稿された
                                  結果がなければ従来の #142 フローにフォールバックする。 */}
                              {(uiState === "fill_gaps" || proposalNarrowing) && (
                                focusedQuestionInvestigating ? (
                                  <p
                                    className="text-xs text-muted-foreground self-center"
                                    data-testid="focused-question-unknown-investigating"
                                  >
                                    関連コードとテストを確認しています
                                  </p>
                                ) : (
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={handleFocusedUnknown}
                                    disabled={dialogueTurn.isPending || qaAutoInvestigate.isPending}
                                    data-testid="quick-answer-unknown"
                                  >
                                    <HelpCircle className="h-4 w-4 mr-1" />
                                    わからない
                                  </Button>
                                )
                              )}
                            </div>
                          )}
                          {/* PR #296 review restructure: qa.investigation を Q&A一覧
                              だけでなく focused question と同じビューにも表示する
                              (前任者の申し送り対応)。QaItemCard と同じ表示コンポー
                              ネントを再利用し、判断ロジックは一切増やさない。 */}
                          {focusedQa?.investigation && (
                            <QaInvestigationBlock
                              qaId={focusedQa.id}
                              investigation={focusedQa.investigation}
                              routeCategory={focusedQa.route_category as InquiryRouteCategory}
                              onTranscribe={() => setMessage(focusedQa.investigation!.conclusion)}
                            />
                          )}
                        </div>
                      )}
                      {(zeroBaseComplete || canConfirmStructuredUnderstanding) && (
                        <div className="space-y-1">
                          {canConfirmStructuredUnderstanding && (
                            <p className="text-xs text-muted-foreground">
                              対象: 右側の「現在の理解」パネルに表示されているシステム理解
                            </p>
                          )}
                          <Button
                            size="sm"
                            onClick={doConfirmUnderstanding}
                            disabled={confirmUnderstanding.isPending}
                            data-testid="confirm-understanding"
                          >
                            <CheckCircle className="h-4 w-4 mr-1" />
                            {confirmUnderstanding.isPending
                              ? "確定中..."
                              : zeroBaseComplete
                                ? "この内容で提案生成に進む"
                                : "この理解を確認済みにする"}
                          </Button>
                        </div>
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
                          title={approvedCount === 0 ? "先に提案を1件以上承認してください" : undefined}
                        >
                          <Play className="h-4 w-4 mr-1" />
                          {materialize.isPending ? "生成中..." : "差分を生成"}
                        </Button>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {approvedCount === 0 && (
                        <p className="text-xs text-muted-foreground" data-testid="materialize-prerequisite">
                          差分を生成するには、各提案を確認して少なくとも1件を承認してください。
                        </p>
                      )}
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
                        <div className="flex items-center gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={downloadPatch}
                            disabled={!diff}
                            data-testid="download-patch-button"
                            title={diff ? undefined : "差分が未生成のためダウンロードできません"}
                          >
                            <Download className="h-4 w-4 mr-1" />
                            .patch をダウンロード
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => openDiff(diff, session.id)}
                            disabled={!diff}
                            title={diff ? undefined : "先に承認済み提案から差分を生成してください"}
                          >
                            <GitPullRequest className="h-4 w-4 mr-1" />
                            差分を開く
                          </Button>
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="rounded-md bg-muted/50 p-3 text-xs space-y-1" data-testid="patch-provenance">
                        <p className="font-medium">この差分について</p>
                        <ul className="list-disc pl-4 space-y-0.5 text-muted-foreground">
                          <li>承認済みのインタビュー提案から生成された、レビュー用の patch です。</li>
                          <li>
                            snapshot {session.snapshot_id}
                            {patchCommitSha && (
                              <> (commit <code className="font-mono">{patchCommitSha.slice(0, 8)}</code>)</>
                            )}{" "}
                            に対する差分です。適用時も同じ commit をベースにしてください。
                          </li>
                          <li>変更内容は probe-agent: docstring メタデータと @probe 計装です。</li>
                          <li>対象リポジトリのブランチや実ファイルは自動では変更されていません。</li>
                          <li>内容をレビューし、妥当な場合に開発者が手動で適用します。</li>
                        </ul>
                      </div>
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
                          差分が未生成の間は .patch のダウンロードはできません。
                        </div>
                      )}
                      {diff && (
                        <>
                          <details className="rounded-md border p-3 text-xs" data-testid="patch-apply-commands">
                            <summary className="cursor-pointer font-medium text-sm">
                              適用手順とコマンド例
                            </summary>
                            <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-3 font-mono whitespace-pre">
{`# 対象リポジトリで、差分生成時と同じ commit をベースにする
git switch -c probe-instrumentation ${patchCommitSha ? patchCommitSha.slice(0, 8) : "<commit-sha>"}

# 適用できるか事前に確認する
git apply --check path/to/downloaded.patch

# patch を適用する
git apply path/to/downloaded.patch

# 変更内容を確認する
git diff

# 必要なテストや疎通確認を実行する(接続ガイド参照)

# 問題なければ通常の開発フローで commit / PR へ進む
git status
git add -p
git commit`}
                            </pre>
                          </details>
                          <div
                            className="rounded-md border border-primary/40 bg-primary/5 p-3 text-sm flex items-start gap-2"
                            data-testid="setup-guide-next-action"
                          >
                            <LifeBuoy className="h-4 w-4 mt-0.5 shrink-0" />
                            <div>
                              <p className="font-medium">次のステップ: 監視対象の設定と疎通確認</p>
                              <p className="text-xs text-muted-foreground mt-0.5">
                                patch の適用だけでは監視は始まりません。監視対象側の環境変数・トークン・接続先の設定と、
                                trace が届くことの確認が必要です。
                              </p>
                              <Link
                                className="inline-flex items-center text-sm underline mt-1"
                                to={`/setup-guide?session=${session.id}`}
                              >
                                接続セットアップガイドを開く(このセッションの文脈付き)
                              </Link>
                            </div>
                          </div>
                        </>
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
                </TabsContent>
              </Tabs>
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
                  <AnswerableAreasControl sessionId={session.id} answerableAreas={session.answerable_areas} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <CardTitle className="text-sm flex items-center gap-2">
                        <Layers className="h-4 w-4" /> 現在の理解
                        <RefreshStatusChip sessionId={selectedSessionId} />
                      </CardTitle>
                      <CardDescription>
                        ドキュメントとコード分析から構築したシステム理解。通常は回答後に自動で更新されます。
                      </CardDescription>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={refreshUnderstanding}
                      disabled={building || !canRefreshUnderstanding}
                      title={refreshBlockedUntilAnswerRevision
                        ? "新しい回答(修正・追加回答)がある場合にのみ、理解を再構築できます"
                        : "通常は回答後に自動で更新されます。障害復旧・診断用の手動更新です"}
                    >
                      <Sparkles className="h-4 w-4 mr-1" />
                      {updateUnderstanding.isPending ? "分析中..." : "理解を更新"}
                    </Button>
                  </div>
                  {refreshBlockedUntilAnswerRevision && (
                    <p
                      className="mt-2 text-xs text-muted-foreground"
                      data-testid="understanding-refresh-blocked-reason"
                    >
                      理解は確認済みです。次は提案を生成またはレビューしてください。内容を変える場合は、回答を修正するか、未回答の質問に新しく回答すると理解を更新できます。
                    </p>
                  )}
                </CardHeader>
                <CardContent>
                  {session.last_error && (
                    <div className="rounded-md border border-destructive bg-destructive/10 p-3 mb-3 text-sm text-destructive flex items-start gap-2">
                      <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                      <div>
                        <div className="font-medium">直近の処理でエラーが発生しました</div>
                        <div className="text-xs mt-1">{session.last_error}</div>
                      </div>
                    </div>
                  )}
                  {(session.current_understanding || !session.last_error) && (
                    <UnderstandingOverview
                      understanding={session.current_understanding}
                      gaps={session.gap_analysis}
                      openQuestions={session.open_questions}
                      nextAction={nextActionText}
                    />
                  )}
                </CardContent>
              </Card>

              {/* PR #296 review restructure: Review Queue now lives only in
                  the main "Alignment Review" tab above (never duplicated
                  here) -- the sidebar keeps Intent Brief editing and the
                  other supporting panels. */}
              <IntentBriefPanel sessionId={session.id} />

              <HandoffListPanel sessionId={session.id} actor={actor} />

              <ObservationProposalPanel sessionId={session.id} />

              <ChangeSetPanel sessionId={session.id} />

              <UnderstandingDiffPanel
                sessionId={session.id}
                answerRevisionReflected={answerRevisionReflected}
              />

              <QaPanel
                sessionId={session.id}
                actor={actor}
                approvedCount={approvedCount}
                answerableAreas={session.answerable_areas}
                investigate={qaAutoInvestigate}
              />
            </div>
          </div>

          <Dialog
            open={capabilityConfirmOpen}
            onOpenChange={setCapabilityConfirmOpen}
          >
            <DialogHeader>
              <DialogTitle>Core Capability 構成を確認</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 text-sm" data-testid="capability-confirm-dialog">
              <p className="text-xs text-muted-foreground">
                変更後の支援関係と rename の同一性を確認してください。この確定後、
                影響する Alignment 項目だけが再確認対象になります。
              </p>

              {priorCapabilityGraphQuery.isLoading ? (
                <p className="text-xs text-muted-foreground">前回の正準構成を読み込み中...</p>
              ) : unmatchedCapabilityNodes.length > 0 ? (
                <div className="space-y-2">
                  <p className="font-semibold">名前が一致しない Capability</p>
                  {unmatchedCapabilityNodes.map(node => {
                    const key = capabilityNodeKey(node.kind, node.name);
                    const candidates = (priorCapabilityGraphQuery.data?.nodes ?? []).filter(
                      prior =>
                        prior.entity_kind === node.kind
                        && !proposedCapabilityNodes.some(
                          current =>
                            current.kind === prior.entity_kind
                            && current.name === prior.name,
                        ),
                    );
                    return (
                      <div key={key} className="grid gap-1 md:grid-cols-2 md:items-center">
                        <Label>{node.name}</Label>
                        <Select
                          value={capabilityIdentitySelections[key] ?? ""}
                          onChange={event =>
                            setCapabilityIdentitySelections(current => ({
                              ...current,
                              [key]: event.target.value,
                            }))
                          }
                          data-testid={`capability-identity-${node.kind}-${node.name}`}
                        >
                          <option value="">新しい Capability として扱う</option>
                          {candidates.map(candidate => (
                            <option key={candidate.entity_id} value={candidate.entity_id}>
                              {candidate.name} (entity #{candidate.entity_id}) を rename
                            </option>
                          ))}
                        </Select>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  rename の明示指定が必要な Capability はありません。
                </p>
              )}

              <div className="space-y-2">
                <p className="font-semibold">確定する支援関係</p>
                {proposedCapabilityRelations.length === 0 ? (
                  <p className="text-xs text-muted-foreground">支援関係はありません。</p>
                ) : (
                  proposedCapabilityRelations.map(relation => {
                    const key = capabilityRelationKey(relation);
                    return (
                      <label key={key} className="flex items-center gap-2 text-xs">
                        <input
                          type="checkbox"
                          checked={capabilityRelationSelections[key] !== false}
                          onChange={event =>
                            setCapabilityRelationSelections(current => ({
                              ...current,
                              [key]: event.target.checked,
                            }))
                          }
                        />
                        <span>
                          {relation.supported_name} → {relation.supporting_name}
                        </span>
                      </label>
                    );
                  })
                )}
              </div>

              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => setCapabilityConfirmOpen(false)}
                >
                  キャンセル
                </Button>
                <Button
                  onClick={() => void submitUnderstandingConfirmation(true)}
                  disabled={
                    confirmUnderstanding.isPending
                    || priorCapabilityGraphQuery.isLoading
                  }
                  data-testid="confirm-capability-composition"
                >
                  {confirmUnderstanding.isPending ? "確定中..." : "この構成を確定"}
                </Button>
              </div>
            </div>
          </Dialog>

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
