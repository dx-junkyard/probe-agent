import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AlertCircle, CheckCircle, Download, FileCode, GitPullRequest,
  HelpCircle, Layers, LifeBuoy, Loader2, MessageSquareText, Pencil, RefreshCw, Send,
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
  useInterviewProcessRuns,
  useInterviewWorkflowState,
  useRecordDiffReview,
  useAcknowledgeBackRequest,
  useCloseInterviewSession,
  useReopenInterviewSession,
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
import {
  BackRequestNotice,
  PRIMARY_ACTION_LABELS,
  PROCESS_LABELS,
  WorkflowExceptions,
  WorkflowLocationCard,
} from "@/components/system-understanding/workflow-panel";
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

// Issue #349: the developer-facing state label now comes from
// `WORKFLOW_STATE_LABELS` in components/system-understanding/workflow-panel,
// keyed by the server's canonical workflow state. `InterviewUiState` survives
// only as an internal content selector for the conversation surface.


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

// Issue #349: the 7-stage internal ProgressSteps is replaced by
// `WorkflowProgressSteps` (W0..W7). Internal stage names are never shown to
// the developer (spec §2.1).

// Issue #349: `NextActionBanner` is replaced by `WorkflowLocationCard`
// (`R1` 現在地, one per screen). The banner's stage badge came from the
// client-derived `InterviewUiState`; the current location must now come from
// the server's canonical workflow state, and 「次にやること」 must name the
// state's single primary action, in the same card (spec §3.5, 原則 P2).

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
  showRecoveryActions = false,
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
  // Issue #349: true only while the auto-run process behind these controls
  // has itself failed (`E14`). Recovery controls are never permanent.
  showRecoveryActions?: boolean;
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
  if (!qaList || qaList.items.length === 0) return null;

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
          {/* Issue #349: 「AIに先に調査させる」(#60) と「実態チェックを実行」
              (#61) は `OP-S5`/`OP-S8` として自動実行される処理なので、通常時は
              出さない。その処理そのものが失敗した (`E14`) ときにだけ、復旧
              操作として現れる。前提未達の注記 (#62) は、ボタンを出さない以上
              不要なので廃止した (原則 P3)。 */}
          {showRecoveryActions && (
            <div className="flex items-center gap-2">
              {qaList.open_count > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleRouteAndInvestigate}
                  disabled={investigate.isPending}
                  data-testid="route-and-investigate-qa"
                >
                  {investigate.isPending ? "調査中..." : "調査をもう一度実行する"}
                </Button>
              )}
              {approvedCount > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleRuntimeRealityCheck}
                  disabled={runRealityCheck.isPending}
                  data-testid="run-runtime-reality-check"
                >
                  {runRealityCheck.isPending ? "実行中..." : "実態チェックをもう一度実行する"}
                </Button>
              )}
            </div>
          )}
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

// Issue #349: システム処理の実行履歴 (`R6`)。現在の主作業をブロックして
// いない過去の失敗は主フローに出さず (§5.3-4)、この監査入口にだけ残す。
function ProcessRunHistory({ sessionId }: { sessionId: number }) {
  const { data: runs } = useInterviewProcessRuns(sessionId);
  if (!runs || runs.length === 0) {
    return <p className="text-xs text-muted-foreground">まだありません。</p>;
  }
  return (
    <div className="space-y-1">
      {runs.slice(0, 30).map(run => (
        <div key={run.id} className="rounded-md border p-2 text-xs" data-testid="process-run-row">
          <div className="flex items-center justify-between gap-2">
            <span>{PROCESS_LABELS[run.process_kind]}</span>
            <Badge
              variant={
                run.status === "failed"
                  ? "destructive"
                  : run.status === "running"
                    ? "secondary"
                    : "success"
              }
            >
              {run.status === "failed"
                ? "失敗"
                : run.status === "running"
                  ? "実行中"
                  : "成功"}
            </Badge>
          </div>
          <div className="text-muted-foreground">{formatTimestamp(run.started_at)}</div>
          {run.error ? <div className="break-words">{run.error}</div> : null}
        </div>
      ))}
    </div>
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
  // Issue #349: the canonical developer-facing state. The server evaluates
  // docs/system-interview-workflow-ux.md §2.2 (13-row first-match rule table
  // + backward hold) over persisted facts only, and returns the state, its
  // single primary action, and the currently-active exceptions. This page
  // must not re-derive a workflow state from client-only values (a chosen
  // tab, a mutation's isPending) -- those vanish on reload (spec §2.6/P9).
  const { data: workflow } = useInterviewWorkflowState(selectedSessionId);
  const recordDiffReview = useRecordDiffReview(selectedSessionId);
  const acknowledgeBack = useAcknowledgeBackRequest(selectedSessionId);
  const closeSession = useCloseInterviewSession(selectedSessionId);
  const reopenSession = useReopenInterviewSession(selectedSessionId);
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
  // Issue #349: `uiState` no longer decides WHICH workflow state is shown --
  // `workflow.state` does. It now only selects the CONTENT of the
  // conversation work surface (which question card / confirmation prompt to
  // compose), so `building` (a mutation's isPending) is deliberately not fed
  // into it: `W1` comes from the server's persisted running record instead.
  const uiState: InterviewUiState | null = session ? deriveUiState(session, false) : null;
  const wState = workflow?.state ?? null;
  const wFacts = workflow?.facts;
  // 状態ごとの作業面 (§3.3 のマトリクス)。1 状態・1 主作業 (原則 P1)。
  const showUnderstandingWork = wState === "W2";
  const showQuestionWork = wState === "W3";
  const showAlignmentWork = wState === "W4";
  const showProposalWork = wState === "W5";
  const showDiffWork = wState === "W6";
  const showTerminal = wState === "W7";
  // 会話の作業面は W2 / W3 のみ。W1 は現在地カードが進行を示す。
  const showConversationWork = showUnderstandingWork || showQuestionWork;
  // 復旧操作の常設をやめる (§5.3-1): 対応する例外が今発生しているときだけ。
  const exceptionCodes = new Set((workflow?.exceptions ?? []).map(e => e.code));
  const understandingRecoveryVisible = exceptionCodes.has("E3-a");
  const alignmentRecoveryVisible = exceptionCodes.has("E4-a") || exceptionCodes.has("E4-b");
  const auxiliaryProcessFailed = (workflow?.unresolved_failures ?? []).some(
    f => f.process_kind === "question_routing" || f.process_kind === "runtime_reality_check",
  );
  const unlocked = session ? proposalsUnlocked(session) : false;
  // A confirmed proposal-stage session is already at its next workflow step.
  // Rebuild only once new developer input (an answer correction, or a
  // first-time / Runtime-Reality-Check Q&A answer given after confirmation)
  // is waiting to be reflected. `understanding_update_available` is the
  // server's own evaluation of this same condition (Issue #229/#263's
  // shared `_understanding_update_blocked` predicate) so this flag can never
  // drift from what `update-understanding` will actually accept.
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

  // 指摘3/5b のフロント側整合: 実行可能(actionable)カテゴリの件数は
  // outstanding_counts(未対応件数)を優先し、Review Queue のカード数と一致
  // させる。古い Control Server(outstanding_counts 未対応)では従来どおり
  // counts(総数)にフォールバックする。

  // Issue #349: the main tabs (element #12) and the 「会話タブへ移動」 lead
  // (element #10) are abolished. They existed only because `W3` (answering
  // questions) and `W4` (settling intent drift) were not distinct states, so
  // the client had to guess which tab held the required action. The workflow
  // state now decides that, so the compensating machinery
  // (`conversationHasRequiredAction` / `manualMainTab` / the tab jump lead)
  // is gone (spec §2.4-2, §3.2).

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
          ? "承認済みの提案からレビュー用の差分が自動で生成されます。残りの提案のレビューも続けられます。"
          : "各提案を承認・編集・却下してください。承認が終わると差分は自動で生成されます。";
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

  // Issue #349 `OP-S7`: 差分生成は自動 (§4.2.1)。承認が終わり、現在の承認
  // セットに対応する差分がまだ無く、生成中でも生成に失敗してもいない
  // ときだけ、1 回だけ起動する。失敗したときは `E12` の復旧操作
  // (同じ処理の再試行) に委ね、ここでは自動再試行しない。
  const autoMaterializedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!workflow || !selectedSessionId) return;
    const f = workflow.facts;
    const eligible =
      workflow.state === "W5"
      && f.proposals_needing_review === 0
      && f.approved_proposal_count > 0
      && !f.diff_matches_approval_set
      && workflow.running_processes.length === 0
      && !workflow.unresolved_failures.some(x => x.process_kind === "diff_generation");
    if (!eligible || materialize.isPending) return;
    const key = `${selectedSessionId}:${f.approved_proposal_count}:${workflow.diff_materialized_at ?? 0}`;
    if (autoMaterializedRef.current === key) return;
    autoMaterializedRef.current = key;
    void triggerMaterialization();
    // triggerMaterialization/materialize are stable enough for this guard;
    // the ref key prevents re-entry for the same approval set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow, selectedSessionId]);

  // Issue #349 §5.3-1/§5.3-7: the ONLY recovery a blocking exception offers
  // is "run the same process again". No heuristic alternative (Principle 6),
  // and no bypass that would skip a human gate.
  const retryFailedProcess = async (kind: string) => {
    if (!selectedSessionId) return;
    if (kind === "understanding_build" || kind === "understanding_update") {
      await refreshUnderstanding();
      return;
    }
    if (kind === "alignment_build") {
      try {
        await api.post(`/interview/sessions/${selectedSessionId}/alignment/build`, {});
        toast.success("突き合わせを再実行しました");
      } catch (e) {
        toast.error(String(e));
      }
      return;
    }
    if (kind === "diff_generation") {
      await triggerMaterialization();
      return;
    }
    toast.error("この処理の再試行はこの画面からは行えません");
  };

  // `OP-D14` — leave safely to the suspend / handoff terminal. Records a
  // manual audit entry; never resolves the blocking failure that led here.
  const leaveSafely = async (terminalKind: "suspended" | "handoff") => {
    if (!selectedSessionId) return;
    try {
      await closeSession.mutateAsync({ terminal_kind: terminalKind, actor });
      toast.success(
        terminalKind === "handoff"
          ? "引き継ぎとして終了しました。再開すると続きから評価します。"
          : "中断しました。確定内容は保持されます。",
      );
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

          {/* R1 — 現在地。全状態で 1 箇所のみ。「次にやること」が指す操作は
              その状態の主操作そのもの (原則 P2)。進捗ステップも同じカードへ
              まとめる (§3.2 #9/#11/#50/#63 の統合)。 */}
          {workflow && (
            <WorkflowLocationCard
              workflow={workflow}
              detail={nextActionText}
              historyEntry={<RefreshStatusChip sessionId={selectedSessionId} />}
            />
          )}

          {/* R2 の分岐 — 確認待ちの戻り (E8)。承諾するまで表示状態は
              reached_state のまま (§2.2 第 2 段)。 */}
          {workflow && (
            <BackRequestNotice
              workflow={workflow}
              pending={acknowledgeBack.isPending}
              onAcknowledge={requestId =>
                acknowledgeBack
                  .mutateAsync({ requestId, actor })
                  .then(() => toast.success("確認が必要な状態へ移動しました"))
                  .catch(e => toast.error(String(e)))
              }
            />
          )}

          {/* R5 — 現在ブロッキング中 / 劣化中の例外だけを、現在地の直下に。 */}
          {workflow && (
            <WorkflowExceptions
              workflow={workflow}
              // E2 (snapshot のズレ) は、更新操作そのものを内包した専用の
              // バナー(#7/#8)が上で担っている。汎用の例外カードにも出すと
              // 同じ対象の表示が 2 箇所になる (原則 P7)。
              skipCodes={["E2"]}
              retryPending={building || materialize.isPending}
              onRetry={kind => void retryFailedProcess(kind)}
              onLeaveSafely={() => void leaveSafely("suspended")}
            />
          )}

          <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-4">
            {/* メイン: 状態ごとに 1 つの主作業面だけを描く (原則 P1/P3)。
                タブ (#12) は廃止され、W3 と W4 が別状態になったことで
                「どちらのタブを既定にするか」という判断自体が消えた。 */}
            <div className="space-y-4">
              <>
                {showAlignmentWork && (
                <div className="space-y-4" data-testid="work-surface-W4">
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
                  <ReviewQueuePanel
                    sessionId={session.id}
                    showRecoveryBuild={alignmentRecoveryVisible}
                  />
                </div>
                )}

                {showConversationWork && (
                <div className="space-y-4" data-testid={showUnderstandingWork ? "work-surface-W2" : "work-surface-W3"}>
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

                </div>
                )}

                {/* W5 — 提案を承認する。主操作は「この提案を承認する」。
                    差分生成 (#33「差分を生成」) は OP-S7 として自動化された
                    ため、そのボタンと前提説明 (#34) / 未生成注記 (#35) は
                    廃止した (§4.2.1、原則 P3)。 */}
                {showProposalWork && (
                <div className="space-y-4" data-testid="work-surface-W5">
                  <Card>
                    <CardHeader>
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <CardTitle className="text-sm">提案レビュー</CardTitle>
                          <CardDescription>
                            各提案(メタデータ + プローブ計画)を承認・却下・編集してください。
                            承認が終わると、差分は自動で生成されます。
                          </CardDescription>
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {proposals.map(proposal => (
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
                </div>
                )}

                {/* W6 — 差分を確認する。主操作は「この差分を確認した」という
                    判断の記録そのもの (§4.3)。ダウンロード・差分表示・適用
                    手順は補助操作であり、完了条件を満たさない。
                    W7 でも差分は参照できるが、そのときは主操作ではない。 */}
                {(showDiffWork || showTerminal) && diff && (
                <div className="space-y-4" data-testid={showDiffWork ? "work-surface-W6" : "terminal-diff-reference"}>
                  <Card>
                    <CardHeader>
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <CardTitle className="text-sm">レビュー用差分</CardTitle>
                          <CardDescription>差分の生成までで停止し、適用は開発者がレビューして行います。</CardDescription>
                        </div>
                        <div className="flex items-center gap-2">
                          {/* 補助操作 (§4.3): 差分を読むための手段であって、
                              読んで問題ないと判断した記録ではない。 */}
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={downloadPatch}
                            data-testid="download-patch-button"
                          >
                            <Download className="h-4 w-4 mr-1" />
                            .patch をダウンロード
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => openDiff(diff, session.id)}
                          >
                            <GitPullRequest className="h-4 w-4 mr-1" />
                            差分を開く
                          </Button>
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {showDiffWork && (
                        <div
                          className="rounded-md border border-primary/40 bg-primary/5 p-3 flex flex-wrap items-center gap-3"
                          data-testid="diff-review-primary-action"
                        >
                          <p className="text-sm flex-1 min-w-[16rem]">
                            差分の内容を確認し、意図どおりであることを記録してください。
                            この記録が「差分を確認する」の完了条件です。
                          </p>
                          <Button
                            size="sm"
                            disabled={recordDiffReview.isPending}
                            data-testid="record-diff-review"
                            onClick={() =>
                              recordDiffReview
                                .mutateAsync({ reviewed_by: actor })
                                .then(() => toast.success("差分を確認したことを記録しました"))
                                .catch(e => toast.error(String(e)))
                            }
                          >
                            <CheckCircle className="h-4 w-4 mr-1" />
                            {recordDiffReview.isPending
                              ? "記録中..."
                              : PRIMARY_ACTION_LABELS.record_diff_review}
                          </Button>
                        </div>
                      )}
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
                      <pre className="max-h-[28rem] overflow-auto rounded-md border bg-muted p-3 text-xs whitespace-pre-wrap">
                        {diff}
                      </pre>
                      {/* 適用手順は R4: 既定は折りたたみ。接続セットアップ導線は
                          W7(完了)の主操作なので、そこでのみ強調する。 */}
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
                          {showTerminal && workflow?.terminal_kind === "completed" && (
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
                          )}
                        </>
                    </CardContent>
                  </Card>
                </div>
                )}

                {/* W7 — 結果と残りを確認する。主操作は終端ごとに 1 つ
                    (§5.4)。「接続セットアップへ進む」を W7 全体に置くと、
                    中断・引き継ぎ終端で意味を成さない操作が主操作になる。 */}
                {showTerminal && workflow && (
                <div className="space-y-4" data-testid="work-surface-W7">
                  <Card data-testid="terminal-card" data-terminal-kind={workflow.terminal_kind ?? ""}>
                    <CardHeader>
                      <CardTitle className="text-sm">
                        {workflow.terminal_kind === "completed"
                          ? "このセッションは完了しました"
                          : workflow.terminal_kind === "handoff"
                            ? "引き継ぎの回答を待っています"
                            : "このセッションは中断しています"}
                      </CardTitle>
                      <CardDescription>
                        {workflow.terminal_kind === "completed"
                          ? "生成された差分を確認済みです。適用と接続確認へ進めます。"
                          : workflow.terminal_kind === "handoff"
                            ? "あなた側でできることは残っていません。引き継ぎ先の回答後、確定は明示操作で行います。"
                            : "ここまでの回答・確定・承認は保持されています。再開すると続きから評価し直します。"}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3 text-sm">
                      <ul className="list-disc pl-4 space-y-0.5 text-muted-foreground text-xs">
                        <li>承認済みの提案: {wFacts?.approved_proposal_count ?? 0} 件</li>
                        <li>引き継ぎ待ち: {wFacts?.pending_handoff_count ?? 0} 件</li>
                        <li>未対応の確認項目: {wFacts?.outstanding_alignment_items ?? 0} 件</li>
                      </ul>
                      <div className="flex flex-wrap gap-2">
                        {workflow.terminal_kind === "completed" && (
                          <Link
                            className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
                            to={`/setup-guide?session=${session.id}`}
                            data-testid="terminal-primary-action"
                          >
                            <LifeBuoy className="h-4 w-4" />
                            {PRIMARY_ACTION_LABELS.open_connection_setup}
                          </Link>
                        )}
                        {workflow.terminal_kind === "handoff" && (
                          <Button
                            size="sm"
                            data-testid="terminal-primary-action"
                            onClick={() => {
                              document
                                .querySelector('[data-testid="handoff-list-panel"]')
                                ?.scrollIntoView({ behavior: "smooth", block: "center" });
                            }}
                          >
                            {PRIMARY_ACTION_LABELS.view_handoff_status}
                          </Button>
                        )}
                        {workflow.terminal_kind === "suspended" && (
                          <Button
                            size="sm"
                            data-testid="terminal-primary-action"
                            disabled={reopenSession.isPending || !wFacts?.session_closed}
                            onClick={() =>
                              reopenSession
                                .mutateAsync({ actor })
                                .then(() => toast.success("セッションを再開しました"))
                                .catch(e => toast.error(String(e)))
                            }
                          >
                            {PRIMARY_ACTION_LABELS.resume_session}
                          </Button>
                        )}
                        {/* 補助操作: 完了 / 引き継ぎ / 中断は排他的な終端なので、
                            まだ閉じていないセッションだけ明示的に終端へ移せる。 */}
                        {!wFacts?.session_closed && (
                          <Button
                            size="sm"
                            variant="outline"
                            data-testid="terminal-close-session"
                            disabled={closeSession.isPending}
                            onClick={() => void leaveSafely(
                              (wFacts?.pending_handoff_count ?? 0) > 0 ? "handoff" : "suspended",
                            )}
                          >
                            このセッションを終える
                          </Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                </div>
                )}
              </>
            </div>

            {/* サイド: 判断に必要な根拠 (R3) と、必要時に開く詳細 (R4)。
                履歴・監査・診断 (R6) は §3.6 の共通入口へ集約した。 */}
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">セッション #{session.id}</CardTitle>
                  <CardDescription>
                    Snapshot {session.snapshot_id} · {formatTimestamp(session.updated_at)}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  {/* R4: 回答可能領域は変更時にだけ開く詳細。 */}
                  <AnswerableAreasControl sessionId={session.id} answerableAreas={session.answerable_areas} />
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
                        ドキュメントとコード分析から構築したシステム理解。通常は回答後に自動で更新されます。
                      </CardDescription>
                    </div>
                    {/* #47 「理解を更新」 / #21 「理解を構築」 は通常フローでは
                        出さない (`OP-S1`/`OP-S2` は自動)。E3-a が今発生している
                        ときだけ、失敗表示と同じ意味の復旧操作として現れる。
                        #48 の「更新できない理由」説明文は、ボタンを出さない
                        以上不要なので廃止した (原則 P3)。 */}
                    {understandingRecoveryVisible && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={refreshUnderstanding}
                        disabled={building}
                        data-testid="understanding-recovery-build"
                      >
                        <Sparkles className="h-4 w-4 mr-1" />
                        {updateUnderstanding.isPending ? "分析中..." : "理解の構築をもう一度実行する"}
                      </Button>
                    )}
                  </div>
                </CardHeader>
                <CardContent>
                  {/* #49 `last_error`: 現在も主作業をブロック/劣化させている
                      ときだけ出す (§5.3-4)。解消済みの過去エラーは履歴へ。 */}
                  {session.last_error && (workflow?.unresolved_failures.length ?? 0) > 0 && (
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

              <QaPanel
                sessionId={session.id}
                actor={actor}
                approvedCount={approvedCount}
                answerableAreas={session.answerable_areas}
                investigate={qaAutoInvestigate}
                showRecoveryActions={auxiliaryProcessFailed}
              />

              {/* R6 — 履歴と監査情報 (§3.6)。主フローの各状態には常設せず、
                  状態に依存しない 1 つの入口からのみ到達する。中身は固定の
                  並び順で、Interview UX 評価指標 (#341) もこの中に置く。
                  入口は常に開ける (前提未達でも消さない)。 */}
              <details className="rounded-md border p-3" data-testid="history-audit-entry">
                <summary className="cursor-pointer text-sm font-medium">
                  履歴と監査情報
                </summary>
                <div className="mt-3 space-y-4">
                  <section className="space-y-2" data-testid="history-conversation-log">
                    <h3 className="text-xs font-semibold text-muted-foreground">会話ログ</h3>
                    {session.messages.length === 0 ? (
                      <p className="text-xs text-muted-foreground">まだありません。</p>
                    ) : (
                      <div className="space-y-2 max-h-[20rem] overflow-y-auto pr-1">
                        {session.messages.map(m => (
                          <div key={m.id} className="rounded-md border p-2 text-xs">
                            <div className="flex items-center justify-between gap-2 mb-1">
                              <Badge variant={m.role === "assistant" ? "secondary" : "outline"}>
                                {m.role}
                              </Badge>
                              <span className="text-muted-foreground">
                                {formatTimestamp(m.created_at)}
                              </span>
                            </div>
                            <p className="whitespace-pre-wrap break-words">{m.content}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </section>

                  <UnderstandingDiffPanel
                    sessionId={session.id}
                    answerRevisionReflected={answerRevisionReflected}
                  />

                  <section className="space-y-2" data-testid="history-session-stats">
                    <h3 className="text-xs font-semibold text-muted-foreground">
                      セッション統計 / コンテキストパック
                    </h3>
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
                    {contextPack ? (
                      <div className="rounded-md border p-3 space-y-2 text-xs">
                        <div className="flex items-center justify-between">
                          <span className="font-medium">コンテキストパック</span>
                          <Badge variant={contextPack.truncated ? "warning" : "secondary"}>
                            {contextPack.budget_used_chars.toLocaleString()} chars
                          </Badge>
                        </div>
                        <div className="text-muted-foreground">
                          {contextPack.total_symbols} symbols · {contextPack.total_entrypoints} entrypoints ·{" "}
                          {contextPack.unclassified_count} 未分類
                        </div>
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">まだありません。</p>
                    )}
                  </section>

                  <section className="space-y-2" data-testid="history-process-runs">
                    <h3 className="text-xs font-semibold text-muted-foreground">
                      システム処理の実行履歴
                    </h3>
                    <ProcessRunHistory sessionId={session.id} />
                  </section>

                  <InterviewMetricsPanel />

                  <section className="space-y-2" data-testid="history-materialization-ref">
                    <h3 className="text-xs font-semibold text-muted-foreground">
                      Materialization リファレンス
                    </h3>
                    {session.materialization_ref ? (
                      <a
                        className="inline-flex items-center text-sm underline"
                        href={session.materialization_ref}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <FileCode className="h-4 w-4 mr-1" />
                        リファレンスを開く
                      </a>
                    ) : (
                      <p className="text-xs text-muted-foreground">まだありません。</p>
                    )}
                  </section>
                </div>
              </details>
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
