// Issue #287: Alignment Review / Review Queue.
//
// Intent Brief(#284)と評価ベースの「現在の理解」(#136)を突き合わせた
// alignment item を表示する。サーバー側で決定的に分類された
// review_category が must_review / batch_reviewable の項目だけをアクショ
// ンカードとして表示し、それ以外(no_review_required / unchanged /
// informational)は「対応不要の項目 (n)」として折りたたむ — 一切アクショ
// ンカードにはしない。
//
// user_reason はサーバーが reason_code ごとに固定テンプレートで生成した
// 日本語テキストで、そのまま表示するだけ(クライアント側で言い換えない)。
// alignment_state / risk_flags のような canonical enum はこのファイル内
// の単一マッピングテーブルだけを通して日本語ラベルに変換する。
//
// 「疑問がある」は既存の InquiryPanel を origin_kind='review_item' で再利
// 用する(#285)。Inquiry が開いている間はサーバー側で該当 alignment_item
// の status が 'inquiry' になり、回答/修正/保留アクションは無効化される。
//
// Issue #295 (ST-2, Review Queue UI) の追加:
// - パネル上部にカテゴリ別件数サマリを表示する(§4.1)。5カテゴリを固定
//   の表示順で扱い、応答に無いカテゴリ(例: unchanged がまだ実体化して
//   いない場合)は 0件として表示する — 応答のカテゴリ有無に依存しない
//   汎用実装。
// - must_review/batch_reviewable カードの「回答する」に、ローカルに選択
//   を保留してから「まとめて送信」で一括送信する任意モードを追加した
//   (§4.1)。既定はオフで、従来どおり選択即送信の個別モードのまま動く。
//   一括送信は既存の /answer エンドポイントを項目ごとに順次呼ぶだけで、
//   新しい一括APIは追加しない(#288 の refresh dedup がまとめて1回の再
//   ビルドに集約する)。
// - no_review_required/unchanged/informational の行に監査詳細の展開ト
//   グルを追加した(§5.3)。応答に存在するフィールドだけを表示し、まだ
//   存在しない carried_over_from 等は防御的に optional として扱う。
// - no_review_required/informational のうち id 昇順で先頭3件を超える場
//   合に、決定的に選んだ3件を「サンプル確認」として疑問導線つきで提示す
//   る(§5.4)。乱数は使わない。監査詳細(AuditDetail: 根拠・content_hash
//   等)は PR #296 2回目レビュー指摘5b により既定で折りたたみ(非サンプル
//   行と同じ)に変更した — current_claim は常に見えるが、3件分の監査詳細
//   まで初回表示で展開されると確認疲れを招くため。
// - EvidenceList は、alignment_state が conflict / risk_flags に高リス
//   ク相当(security・high_risk)が含まれる / runtime_check が
//   mismatch・stale / evidence が1件のみ、のいずれかに該当する場合は初
//   期表示で展開する(§4.4)。
//
// PR #296 レビュー指摘対応の追加:
// - 指摘3: GET .../alignment はもう superseded=1 行を items_by_category/
//   counts に含めない(サーバー側で分離済み)。かわりに追加された
//   superseded_items を「履歴 N件」の折りたたみ(初期閉)として表示し、
//   既存の InformationalItemRow(監査詳細 AuditDetail の展開込み)をその
//   まま再利用する。まだ superseded_items を返さない古い Control Server
//   との互換のため、フィールド自体は optional として扱う。
// - 指摘5: まとめて送信は POST .../answers-batch を1回だけ呼ぶ(項目ごと
//   の /answer 順次呼び出しをやめた)。部分失敗した項目は response.results
//   から判定して保留に残す。単体(非一括モード)の即時送信は従来どおり
//   /answer のまま変更していない。
//
// PR #296 2回目レビュー指摘対応の追加:
// - 指摘2: まとめて送信の各エントリに、その項目をステージした時点の
//   content_hash(直近の GET .../alignment 応答由来)を含めて送る。
//   バックエンド(apps/control-server/app/routes/interview_alignment.py の
//   `_validate_answer_target_for_batch` / `AlignmentBatchAnswerItemRequest.
//   content_hash`、確認済み)がこれを検証し、stale を検出した項目だけを
//   失敗として返す。失敗時の `error` はバックエンドが既に日本語で用意した
//   文字列(stale の場合は文字どおり「項目が更新されています。最新の内容
//   を確認してください。」)なので、このコンポーネントは言い換えずそのまま
//   表示するだけ -- 保留には残る(他の失敗理由と同じくリトライ可能)。
// - 指摘3/5b: 要確認/一括レビュー可の件数は outstanding_counts(未対応件
//   数、apps/control-server/app/models.py の `AlignmentListOut.
//   outstanding_counts`、確認済み)があればそれを使い、この画面が実際に出
//   す action card の数と一致させる。古い Control Server(未対応)では従
//   来どおり counts にフォールバックする。

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AlertCircle, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { InquiryPanel } from "@/components/system-understanding/inquiry-panel";
import { InquiryPremiseNotice } from "@/components/system-understanding/inquiry-premise-notice";
import { RefreshStatusChip } from "@/components/system-understanding/refresh-status-chip";
import { HandoffModal } from "@/components/system-understanding/handoff-panel";
import {
  useActiveInquiriesByOrigin,
  useSupersededInquiries,
  useAlignmentList,
  useAnswerAlignmentItem,
  useAnswerAlignmentItemsBatch,
  useBuildAlignment,
  useCorrectAlignmentItem,
  useHoldAlignmentItem,
  useAlignmentRuleObjections,
  useRequestAlignmentRuleRecheck,
  useReviewQueue,
  recordInterviewMetricEventBestEffort,
} from "@/api/hooks";
import type {
  AlignmentConfidence,
  AlignmentDecisionAction,
  AlignmentItemOut,
  AlignmentRuleObjectionOut,
  AlignmentReviewCategory,
  AlignmentRiskFlag,
  AlignmentState,
  AlignmentUserDecisionAction,
  InterviewInquiryOut,
} from "@/api/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

const ALIGNMENT_STATE_LABELS: Record<AlignmentState, string> = {
  aligned: "一致",
  gap: "ギャップあり",
  unknown: "不明",
  conflict: "矛盾",
  not_applicable: "対象外",
};

function alignmentStateLabel(state: string): string {
  return ALIGNMENT_STATE_LABELS[state as AlignmentState] ?? "要確認";
}

const RISK_FLAG_LABELS: Record<AlignmentRiskFlag, string> = {
  security: "セキュリティ",
  high_risk: "影響大",
  core_intent: "目標に関わる",
};

function riskFlagLabel(flag: string): string {
  return RISK_FLAG_LABELS[flag as AlignmentRiskFlag] ?? flag;
}

const CONFIDENCE_LABELS: Record<AlignmentConfidence, string> = {
  confirmed: "確定",
  likely: "ほぼ確実",
  uncertain: "不確か",
  conflicting: "食い違いあり",
};

function confidenceLabel(confidence: string): string {
  return CONFIDENCE_LABELS[confidence as AlignmentConfidence] ?? "不明";
}

const DECISION_LABELS: Record<AlignmentDecisionAction, string> = {
  accept_current: "現状でよい",
  needs_change: "変更が必要",
  reject_interpretation: "AIの解釈を採用しない",
};

const RULE_LABELS: Record<string, string> = {
  security_related: "セキュリティ関連",
  high_risk: "影響大",
  core_intent: "目標に関わる",
  conflict_detected: "矛盾検出",
  low_confidence: "確信度不足",
  runtime_mismatch: "実行時の不一致",
  routine_update: "通常更新",
  no_change: "差分なし",
  informational_only: "参考情報のみ",
  core_capability_changed: "Core Capability構成変更",
  unchanged_since_confirmation: "前回確認から変更なし",
};

function ruleLabel(reasonCode: string): string {
  return RULE_LABELS[reasonCode] ?? "分類ルール";
}

// Finding 4: full label set for a persisted user_decision.action (the answer
// actions above plus /correct と /hold が記録する corrected / held)。監査詳細
// で「承認・変更要求・却下・修正・保留」のどれだったかを判別可能にする。
const USER_DECISION_LABELS: Record<AlignmentUserDecisionAction, string> = {
  accept_current: "現状でよい(承認)",
  needs_change: "変更が必要",
  reject_interpretation: "AIの解釈を採用しない",
  corrected: "修正済み",
  held: "保留中",
};

function userDecisionLabel(action: string): string {
  return USER_DECISION_LABELS[action as AlignmentUserDecisionAction] ?? action;
}

const STATUS_LABELS: Record<string, string> = {
  open: "未対応",
  answered: "回答済み",
  corrected: "修正済み",
  held: "保留中",
  inquiry: "疑問を確認中",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? "要確認";
}

function statusBadgeVariant(status: string): BadgeVariant {
  if (status === "answered" || status === "corrected") return "success";
  if (status === "held" || status === "inquiry") return "secondary";
  return "outline";
}

// §4.1: fixed display order for the category summary. Counts default to 0
// for any category absent from the server's `counts` map (e.g. `unchanged`
// before it is fully wired server-side) so this stays generic regardless of
// which categories the backend currently emits.
const CATEGORY_SUMMARY: { key: AlignmentReviewCategory; label: string }[] = [
  { key: "must_review", label: "要確認" },
  { key: "batch_reviewable", label: "一括レビュー可" },
  { key: "no_review_required", label: "確認不要" },
  { key: "unchanged", label: "前回から変更なし" },
  { key: "informational", label: "参考情報" },
];

function formatTimestamp(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("ja-JP");
}

// §4.4: evidence starts pre-expanded (instead of collapsed) for items where
// hiding the evidence behind a click would hide something the reviewer
// needs immediately. All checks are on existing, already-validated
// deterministic fields — no new judgement.
function shouldExpandEvidenceByDefault(item: AlignmentItemOut): boolean {
  if (item.alignment_state === "conflict") return true;
  if (item.risk_flags.includes("security") || item.risk_flags.includes("high_risk")) return true;
  if (item.runtime_check === "mismatch" || item.runtime_check === "stale") return true;
  if (item.current_evidence.length === 1) return true;
  return false;
}

function EvidenceList({ item }: { item: AlignmentItemOut }) {
  const [startsExpanded] = useState(() => shouldExpandEvidenceByDefault(item));
  const [open, setOpen] = useState(startsExpanded);
  const expandedRecorded = useRef(false);

  useEffect(() => {
    if (item.current_evidence.length === 0 || startsExpanded) return;
    void recordInterviewMetricEventBestEffort({
      schema_version: "interview-metric-event-v1",
      event_key: `evidence_available:alignment_item:${item.id}`,
      session_id: item.session_id,
      event_type: "evidence_available",
      target_kind: "alignment_item",
      target_id: item.id,
    });
  }, [item.current_evidence.length, item.id, item.session_id, startsExpanded]);

  const toggleOpen = () => {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (nextOpen && !startsExpanded && !expandedRecorded.current) {
      expandedRecorded.current = true;
      void recordInterviewMetricEventBestEffort({
        schema_version: "interview-metric-event-v1",
        event_key: `evidence_expanded:alignment_item:${item.id}`,
        session_id: item.session_id,
        event_type: "evidence_expanded",
        target_kind: "alignment_item",
        target_id: item.id,
      });
    }
  };

  if (item.current_evidence.length === 0) return null;
  return (
    <div>
      <button
        type="button"
        className="text-xs text-primary underline underline-offset-2"
        onClick={toggleOpen}
        aria-expanded={open}
        data-testid={`review-item-evidence-toggle-${item.id}`}
      >
        {open ? "根拠を隠す" : "根拠を見る"}
      </button>
      {open && (
        <div
          className="mt-1 space-y-0.5 text-[11px] text-muted-foreground"
          data-testid={`review-item-evidence-${item.id}`}
        >
          <p>スナップショット #{item.snapshot_id}</p>
          {item.current_evidence.map((e, i) => (
            <p key={i} className="font-mono">
              {e.path}:{e.start_line}-{e.end_line}{e.summary ? ` — ${e.summary}` : ""}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

// §5.3: shared audit-detail block for the collapsed/no-action rows. Only
// renders fields that actually exist on the response; optional fields not
// yet returned by the server (e.g. carried_over_from before ST-1 lands)
// are simply omitted rather than shown as blank/placeholder text.
function AuditDetail({ item }: { item: AlignmentItemOut }) {
  return (
    <div
      className="mt-1 space-y-1 text-[11px] text-muted-foreground"
      data-testid={`review-item-audit-detail-${item.id}`}
    >
      <p><span className="font-semibold">状態:</span> {alignmentStateLabel(item.alignment_state)}</p>
      <p><span className="font-semibold">確信度:</span> {confidenceLabel(item.confidence)}</p>
      <p><span className="font-semibold">理由:</span> {item.user_reason}</p>
      {item.user_decision && (
        <div data-testid={`review-item-user-decision-${item.id}`}>
          <p>
            <span className="font-semibold">人間の判断:</span>{" "}
            {userDecisionLabel(item.user_decision.action)}
            {" · "}
            {formatTimestamp(item.user_decision.decided_at)}
          </p>
          {item.user_decision.note && (
            <p className="break-words">
              <span className="font-semibold">メモ:</span> {item.user_decision.note}
            </p>
          )}
        </div>
      )}
      {item.current_evidence.length > 0 && (
        <div>
          <p className="font-semibold">根拠:</p>
          {item.current_evidence.map((e, i) => (
            <p key={i} className="font-mono">
              {e.path}:{e.start_line}-{e.end_line}{e.summary ? ` — ${e.summary}` : ""}
            </p>
          ))}
        </div>
      )}
      <p>
        <span className="font-semibold">スナップショット:</span> #{item.snapshot_id}
        {item.revision_id !== null ? `(リビジョン #${item.revision_id})` : ""}
      </p>
      <p><span className="font-semibold">更新日時:</span> {formatTimestamp(item.updated_at)}</p>
      <p><span className="font-semibold">分析実行:</span> #{item.intelligence_run_id}</p>
      <p data-testid={`review-item-policy-${item.id}`}>
        <span className="font-semibold">分類ポリシー:</span> {item.policy_version}
        {item.policy_digest ? ` (${item.policy_digest.slice(0, 12)})` : ""}
      </p>
      {item.carried_over_from != null && (
        <p data-testid={`review-item-carried-over-${item.id}`}>
          <span className="font-semibold">引き継ぎ元:</span> #{item.carried_over_from}
        </p>
      )}
      {item.content_hash && (
        <p className="font-mono" data-testid={`review-item-content-hash-${item.id}`}>
          content_hash: {item.content_hash}
        </p>
      )}
    </div>
  );
}

interface StagedAnswer {
  decision: AlignmentDecisionAction;
  note?: string;
  // PR #296 review fix (2nd pass, Finding 2): the item's content_hash at the
  // moment it was staged (from AlignmentItemOut.content_hash, read from the
  // last GET .../alignment response). Sent back on submit so the server can
  // detect the item changed underneath the staged answer; null/undefined
  // when the response didn't provide one (older Control Server), in which
  // case no staleness check is requested for this entry.
  content_hash?: string | null;
}

function ReviewQueueItemCard({
  item, sessionId, existingInquiry, bulkMode, bulkSending, stagedAnswer, stagedError,
  onStageAnswer, onUnstageAnswer,
}: {
  item: AlignmentItemOut;
  sessionId: number;
  existingInquiry?: InterviewInquiryOut;
  bulkMode: boolean;
  bulkSending: boolean;
  stagedAnswer?: StagedAnswer;
  // PR #296 review fix (2nd pass, Finding 2): Japanese failure text from the
  // most recent まとめて送信 attempt for this item, when it failed and
  // stayed staged for retry.
  stagedError?: string;
  onStageAnswer: (decision: AlignmentDecisionAction, note?: string) => void;
  onUnstageAnswer: () => void;
}) {
  const answer = useAnswerAlignmentItem(sessionId);
  const correct = useCorrectAlignmentItem(sessionId);
  const hold = useHoldAlignmentItem(sessionId);
  const [answering, setAnswering] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [note, setNote] = useState("");
  const [correction, setCorrection] = useState("");
  const [inquiryMode, setInquiryMode] = useState(false);
  const [hasHeldInquiry, setHasHeldInquiry] = useState(false);
  const [attachedInquiryId, setAttachedInquiryId] = useState<number | null>(null);
  const [handoffOpen, setHandoffOpen] = useState(false);

  const isMustReview = item.review_category === "must_review";
  const locked = item.status === "inquiry";
  const busy = answer.isPending || correct.isPending || hold.isPending;

  const heldInquiryId = hasHeldInquiry
    ? attachedInquiryId
    : (existingInquiry?.status === "held" ? existingInquiry.id : null);
  const reopenableInquiryId = existingInquiry?.status === "open" ? existingInquiry.id : null;

  const submitAnswer = (decision: AlignmentDecisionAction) => {
    if (bulkMode) {
      onStageAnswer(decision, note || undefined);
      setAnswering(false);
      setNote("");
      return;
    }
    answer.mutate({ itemId: item.id, decision, note: note || undefined }, {
      onSuccess: () => {
        setAnswering(false);
        setNote("");
        toast.success("回答しました");
      },
      onError: e => toast.error(String(e)),
    });
  };

  const submitCorrection = () => {
    if (!correction.trim()) {
      toast.error("修正内容を入力してください");
      return;
    }
    correct.mutate({ itemId: item.id, corrected_interpretation: correction }, {
      onSuccess: () => {
        setCorrecting(false);
        toast.success("修正しました");
      },
      onError: e => toast.error(String(e)),
    });
  };

  const submitHold = () => {
    hold.mutate({ itemId: item.id }, {
      onSuccess: () => toast.info("保留しました"),
      onError: e => toast.error(String(e)),
    });
  };

  return (
    <div
      // Issue #322: the anchor target a superseded Inquiry's 後継の確認項目
      // link points at (`#review-item-<premise_successor_item_id>`). The id
      // always comes from the server-provided successor id — the front end
      // never derives a successor itself.
      id={`review-item-${item.id}`}
      className={`scroll-mt-4 rounded-md border p-3 space-y-2 ${isMustReview ? "border-destructive/60 bg-destructive/5" : ""}`}
      data-testid={`review-item-${item.id}`}
      data-review-category={item.review_category}
      aria-label={isMustReview ? `要確認: ${item.current_claim}` : item.current_claim}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 space-y-1 text-xs">
          {item.intent_summary && (
            <p className="break-words"><span className="font-semibold">意図:</span> {item.intent_summary}</p>
          )}
          <p className="break-words"><span className="font-semibold">現状(現在の理解):</span> {item.current_claim}</p>
          {item.gap_summary && (
            <p className="break-words text-muted-foreground"><span className="font-semibold">ギャップ:</span> {item.gap_summary}</p>
          )}
          {item.proposed_interpretation && (
            <p className="break-words text-muted-foreground">
              <span className="font-semibold">AIの提案:</span> {item.proposed_interpretation}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {isMustReview && (
            <Badge variant="destructive" className="gap-1" data-testid={`review-item-must-review-${item.id}`}>
              <AlertCircle className="h-3 w-3" aria-hidden="true" />
              <span className="sr-only">要確認: </span>
              要確認
            </Badge>
          )}
          <Badge variant={statusBadgeVariant(item.status)} data-testid={`review-item-status-${item.id}`}>
            {statusLabel(item.status)}
          </Badge>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1 text-[11px]">
        <Badge variant="outline" data-testid={`review-item-state-${item.id}`}>
          {alignmentStateLabel(item.alignment_state)}
        </Badge>
        {item.risk_flags.map(flag => (
          <Badge key={flag} variant="warning" data-testid={`review-item-risk-${item.id}-${flag}`}>
            {riskFlagLabel(flag)}
          </Badge>
        ))}
        <Badge variant="secondary" data-testid={`review-item-reason-${item.id}`}>
          {item.user_reason}
        </Badge>
        {item.reason_code === "runtime_mismatch" && (
          <Badge variant="destructive" data-testid={`review-item-runtime-mismatch-${item.id}`}>
            実行時不一致
          </Badge>
        )}
      </div>

      <EvidenceList item={item} />

      {(item.capability_dependencies?.length ?? 0) > 0 && (
        <div
          className="rounded border bg-muted/30 p-2 text-[11px]"
          data-testid={`review-item-capability-scope-${item.id}`}
        >
          <p className="font-semibold">この確認に含まれる Capability 構成</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {item.capability_dependencies?.map((dependency, index) => (
              <li key={`${dependency.target_kind}:${dependency.entity_id ?? dependency.relation_id}:${index}`}>
                {dependency.target_kind === "entity"
                  ? `${dependency.entity_name ?? "名称不明"} (entity #${dependency.entity_id})`
                  : `${dependency.supported_entity_name ?? "名称不明"} → ${dependency.supporting_entity_name ?? "名称不明"} (relation #${dependency.relation_id})`}
              </li>
            ))}
          </ul>
          <p className="mt-1 text-muted-foreground">
            「現状を受け入れる」は、この依存範囲も含めた確認として記録されます。
          </p>
        </div>
      )}

      {inquiryMode ? (
        <InquiryPanel
          key={attachedInquiryId ?? "new"}
          sessionId={sessionId}
          originKind="review_item"
          originId={item.id}
          heldDraft={null}
          existingInquiryId={attachedInquiryId ?? undefined}
          onResolved={() => {
            setInquiryMode(false);
            setHasHeldInquiry(false);
            setAttachedInquiryId(null);
          }}
          onHeld={heldId => {
            setInquiryMode(false);
            setHasHeldInquiry(true);
            setAttachedInquiryId(heldId);
          }}
          onCancel={() => { setInquiryMode(false); setAttachedInquiryId(null); }}
        />
      ) : locked ? (
        <p className="text-xs text-amber-700" data-testid={`review-item-locked-${item.id}`}>
          疑問を確認中です。疑問が解消してから回答してください。
        </p>
      ) : answering ? (
        <div className="space-y-2">
          <Textarea
            value={note}
            onChange={e => setNote(e.target.value)}
            placeholder="メモ(任意)"
            rows={2}
            data-testid={`review-item-answer-note-${item.id}`}
          />
          <div className="flex flex-wrap gap-2">
            {(Object.keys(DECISION_LABELS) as AlignmentDecisionAction[]).map(decision => (
              <Button
                key={decision}
                size="sm"
                variant={decision === "accept_current" ? "default" : "outline"}
                onClick={() => submitAnswer(decision)}
                disabled={busy}
                data-testid={`review-item-answer-${decision}-${item.id}`}
              >
                {DECISION_LABELS[decision]}
              </Button>
            ))}
            <Button size="sm" variant="outline" onClick={() => setAnswering(false)} disabled={busy}>
              キャンセル
            </Button>
          </div>
        </div>
      ) : correcting ? (
        <div className="space-y-2">
          <Textarea
            value={correction}
            onChange={e => setCorrection(e.target.value)}
            placeholder="正しい解釈を入力してください"
            rows={2}
            data-testid={`review-item-correction-input-${item.id}`}
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={submitCorrection} disabled={busy} data-testid={`review-item-correction-submit-${item.id}`}>
              確定
            </Button>
            <Button size="sm" variant="outline" onClick={() => setCorrecting(false)} disabled={busy}>
              キャンセル
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {stagedAnswer ? (
            <>
              <Badge variant="secondary" data-testid={`review-item-staged-${item.id}`}>
                回答予定: {DECISION_LABELS[stagedAnswer.decision]}
              </Badge>
              {stagedError && (
                <Badge variant="destructive" data-testid={`review-item-staged-error-${item.id}`}>
                  {stagedError}
                </Badge>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => setAnswering(true)}
                disabled={bulkSending}
                data-testid={`review-item-restage-${item.id}`}
              >
                回答を変更
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={onUnstageAnswer}
                disabled={bulkSending}
                data-testid={`review-item-unstage-${item.id}`}
              >
                選択を解除
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              onClick={() => setAnswering(true)}
              disabled={busy || bulkSending}
              data-testid={`review-item-answer-open-${item.id}`}
            >
              回答する
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => setCorrecting(true)}
            disabled={busy}
            data-testid={`review-item-correct-open-${item.id}`}
          >
            修正する
          </Button>
          {heldInquiryId ? (
            <p className="text-xs text-amber-700 self-center" data-testid={`review-item-held-inquiry-marker-${item.id}`}>
              保留中の疑問があります
            </p>
          ) : reopenableInquiryId ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => { setAttachedInquiryId(reopenableInquiryId); setInquiryMode(true); }}
              disabled={busy}
              data-testid={`review-item-inquiry-reopen-${item.id}`}
            >
              疑問を再開する
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => { setAttachedInquiryId(null); setInquiryMode(true); }}
              disabled={busy}
              data-testid={`review-item-inquiry-open-${item.id}`}
            >
              疑問がある
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={submitHold}
            disabled={busy}
            data-testid={`review-item-hold-${item.id}`}
          >
            保留
          </Button>
          {!item.handoff_id && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setHandoffOpen(true)}
              disabled={busy}
              data-testid={`review-item-handoff-open-${item.id}`}
            >
              担当者へ引き継ぐ
            </Button>
          )}
        </div>
      )}
      <HandoffModal
        sessionId={sessionId}
        originKind="review_item"
        originId={item.id}
        defaultBackground={item.current_claim}
        defaultEvidence={item.current_evidence}
        open={handoffOpen}
        onOpenChange={setHandoffOpen}
      />
    </div>
  );
}

// §5.3 / §5.4: shared row for no_review_required / unchanged / informational
// items. `sample` renders it pre-expanded with an inquiry entry point, used
// for the deterministic §5.4 spot-check; non-sample rows only get the
// collapsed audit-detail toggle.
function InformationalItemRow({
  item, sessionId, existingInquiry, sample,
}: {
  item: AlignmentItemOut;
  sessionId: number;
  existingInquiry?: InterviewInquiryOut;
  sample?: boolean;
}) {
  // 指摘5b: サンプル確認は「現状(主張)」は常に表示しつつ、監査詳細
  // (根拠・content_hash 等)は既定で折りたたむ -- 3件とはいえ初回表示から
  // 監査情報まで展開されると確認疲れを招くため、非サンプル行と同じ既定閉
  // にする。展開はトグルで常に可能。
  const [open, setOpen] = useState(false);
  const expandedRecorded = useRef(false);
  const [inquiryMode, setInquiryMode] = useState(false);
  const [hasHeldInquiry, setHasHeldInquiry] = useState(false);
  const [attachedInquiryId, setAttachedInquiryId] = useState<number | null>(null);

  const heldInquiryId = hasHeldInquiry
    ? attachedInquiryId
    : (existingInquiry?.status === "held" ? existingInquiry.id : null);
  const reopenableInquiryId = existingInquiry?.status === "open" ? existingInquiry.id : null;

  useEffect(() => {
    if (item.review_category !== "unchanged") return;
    void recordInterviewMetricEventBestEffort({
      schema_version: "interview-metric-event-v1",
      event_key: `unchanged_item_presented:alignment_item:${item.id}`,
      session_id: sessionId,
      event_type: "unchanged_item_presented",
      target_kind: "alignment_item",
      target_id: item.id,
    });
  }, [item.id, item.review_category, sessionId]);

  useEffect(() => {
    if (item.current_evidence.length === 0) return;
    void recordInterviewMetricEventBestEffort({
      schema_version: "interview-metric-event-v1",
      event_key: `evidence_available:alignment_item:${item.id}`,
      session_id: sessionId,
      event_type: "evidence_available",
      target_kind: "alignment_item",
      target_id: item.id,
    });
  }, [item.current_evidence.length, item.id, sessionId]);

  const toggleDetail = () => {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (nextOpen && item.current_evidence.length > 0 && !expandedRecorded.current) {
      expandedRecorded.current = true;
      void recordInterviewMetricEventBestEffort({
        schema_version: "interview-metric-event-v1",
        event_key: `evidence_expanded:alignment_item:${item.id}`,
        session_id: sessionId,
        event_type: "evidence_expanded",
        target_kind: "alignment_item",
        target_id: item.id,
      });
    }
  };

  return (
    <div
      // Issue #322: same anchor target as the action cards above, so a
      // superseded Inquiry's successor link resolves even when the successor
      // ended up in a collapsed/no-action category.
      id={`review-item-${item.id}`}
      className="scroll-mt-4 rounded-md border p-2 text-xs space-y-1"
      data-testid={`review-item-informational-${item.id}`}
    >
      <div className="flex flex-wrap items-center gap-1">
        {sample && (
          <Badge variant="outline" data-testid={`review-item-sample-${item.id}`}>
            サンプル確認
          </Badge>
        )}
      </div>
      <p className="break-words"><span className="font-semibold">現状:</span> {item.current_claim}</p>
      <div className="flex flex-wrap items-center gap-1 text-[11px]">
        <Badge variant="outline">{alignmentStateLabel(item.alignment_state)}</Badge>
        <Badge variant="secondary">{item.user_reason}</Badge>
        {item.superseded && (
          <Badge variant="outline" data-testid={`review-item-superseded-${item.id}`}>履歴</Badge>
        )}
      </div>
      <button
        type="button"
        className="text-xs text-primary underline underline-offset-2"
        onClick={toggleDetail}
        aria-expanded={open}
        data-testid={`review-item-informational-detail-toggle-${item.id}`}
      >
        {open ? "詳細を隠す" : "詳細を見る"}
      </button>
      {open && <AuditDetail item={item} />}
      {sample && (
        inquiryMode ? (
          <InquiryPanel
            key={attachedInquiryId ?? "new"}
            sessionId={sessionId}
            originKind="review_item"
            originId={item.id}
            heldDraft={null}
            existingInquiryId={attachedInquiryId ?? undefined}
            onResolved={() => { setInquiryMode(false); setHasHeldInquiry(false); setAttachedInquiryId(null); }}
            onHeld={heldId => { setInquiryMode(false); setHasHeldInquiry(true); setAttachedInquiryId(heldId); }}
            onCancel={() => { setInquiryMode(false); setAttachedInquiryId(null); }}
          />
        ) : heldInquiryId ? (
          <p className="text-xs text-amber-700" data-testid={`review-item-held-inquiry-marker-${item.id}`}>
            保留中の疑問があります
          </p>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => { setAttachedInquiryId(reopenableInquiryId); setInquiryMode(true); }}
            data-testid={`review-item-inquiry-open-${item.id}`}
          >
            {reopenableInquiryId ? "疑問を再開する" : "疑問がある"}
          </Button>
        )
      )}
    </div>
  );
}

export function ReviewQueuePanel({
  sessionId,
  showRecoveryBuild = false,
}: {
  sessionId: number;
  // Issue #349 (#18 / `OP-S3`): the Alignment build is an automatic system
  // process, so 「突き合わせを実行」 is NOT a permanent control. It appears
  // only while `E4-a`/`E4-b` is currently active, as that exception's
  // "run the same process again" recovery (spec §5.3-1).
  showRecoveryBuild?: boolean;
}) {
  const { data: queue } = useReviewQueue(sessionId);
  const { data: full } = useAlignmentList(sessionId);
  const build = useBuildAlignment(sessionId);
  const batchAnswer = useAnswerAlignmentItemsBatch(sessionId);
  const activeInquiries = useActiveInquiriesByOrigin(sessionId);
  const { data: ruleObjections } = useAlignmentRuleObjections();
  const requestRecheck = useRequestAlignmentRuleRecheck(sessionId);
  // Issue #322: superseded Inquiries (前提が変わった疑問) are history, never
  // active — see supersededInquiries()/activeInquiryByOrigin() in api/hooks.
  // Only 'review_item' origins can ever be superseded (the server evaluates
  // that origin only), so the Review Queue is where their history belongs.
  const allSupersededInquiries = useSupersededInquiries(sessionId);
  const supersededReviewInquiries = allSupersededInquiries.filter(
    inquiry => inquiry.origin_kind === "review_item",
  );
  const [showInformational, setShowInformational] = useState(false);
  const [showSuperseded, setShowSuperseded] = useState(false);
  const [showSupersededInquiries, setShowSupersededInquiries] = useState(false);
  const [bulkMode, setBulkMode] = useState(false);
  const [bulkSending, setBulkSending] = useState(false);
  const [pendingAnswers, setPendingAnswers] = useState<Record<number, StagedAnswer>>({});
  const [bulkResult, setBulkResult] = useState<{ success: number; failed: number } | null>(null);
  // PR #296 review fix (2nd pass, Finding 2): per-item Japanese failure text
  // from the most recent まとめて送信 attempt, keyed by item_id. Cleared
  // whenever that item is re-staged/unstaged/succeeds, so a stale failure
  // message never lingers past the situation that produced it.
  const [itemErrors, setItemErrors] = useState<Record<number, string>>({});

  const handleBuild = () => {
    build.mutate(undefined, {
      onSuccess: result => {
        toast.success(`${result.items.length} 件の突き合わせ結果を更新しました`);
      },
      onError: e => toast.error(String(e)),
    });
  };

  const handleRequestRecheck = (rule: AlignmentRuleObjectionOut) => {
    requestRecheck.mutate(rule, {
      onSuccess: result => toast.success(`${result.recheck_target_count}件を再確認対象に戻しました`),
      onError: e => toast.error(String(e)),
    });
  };

  const toggleBulkMode = () => {
    setBulkMode(m => !m);
    setPendingAnswers({});
    setBulkResult(null);
    setItemErrors({});
  };

  // PR #296 review fix (2nd pass, Finding 2): captures the item's
  // content_hash (as of the last GET .../alignment response) at staging
  // time, so まとめて送信 can ask the server to validate the item hasn't
  // changed since. Re-staging (回答を変更) refreshes it from the current
  // `item`, so a stale hash never carries over past a retry.
  const stageAnswer = (
    itemId: number, decision: AlignmentDecisionAction, note?: string, contentHash?: string | null,
  ) => {
    setPendingAnswers(prev => ({ ...prev, [itemId]: { decision, note, content_hash: contentHash } }));
    setItemErrors(prev => {
      if (!(itemId in prev)) return prev;
      const next = { ...prev };
      delete next[itemId];
      return next;
    });
  };

  const unstageAnswer = (itemId: number) => {
    setPendingAnswers(prev => {
      const next = { ...prev };
      delete next[itemId];
      return next;
    });
    setItemErrors(prev => {
      if (!(itemId in prev)) return prev;
      const next = { ...prev };
      delete next[itemId];
      return next;
    });
  };

  const pendingCount = Object.keys(pendingAnswers).length;

  // PR #296 review fix (2nd pass, Finding 2): the Control Server's batch
  // validation (verified in apps/control-server/app/routes/
  // interview_alignment.py, `_validate_answer_target_for_batch` /
  // `_BATCH_ERROR_*`) already authors its 2nd-round error strings in
  // Japanese -- including the exact stale-content_hash case, whose message
  // is literally "項目が更新されています。最新の内容を確認してください。"
  // -- so this only needs to render `result.error` verbatim (never
  // reworded/guessed client-side) with a Japanese fallback for the
  // (currently impossible, but type-optional) case where the server omits
  // it. Two pre-existing validation paths on the same endpoint (bad
  // item_id / already-open-Inquiry) still return English text; that is a
  // Control Server matter outside this dashboard-only change, not
  // something to paper over with a client-side translation guess.
  const failureMessage = (error: string | null | undefined) => error || "送信に失敗しました。もう一度お試しください。";

  // PR #296 review fix (Finding 5): one POST .../answers-batch call for the
  // whole staged set, instead of sequential per-item /answer calls (the
  // server now runs the #288 refresh exactly once for the batch). A failed
  // item is identified from `results` (never re-derived/guessed client-side)
  // and stays staged so the user can retry it; a hard request failure (the
  // whole call rejecting, e.g. a network error) leaves every staged item in
  // place untouched, same as "everything failed".
  const handleBulkSubmit = async () => {
    const entries = Object.entries(pendingAnswers);
    if (entries.length === 0) return;
    setBulkSending(true);
    setBulkResult(null);
    try {
      const response = await batchAnswer.mutateAsync(
        entries.map(([key, staged]) => ({
          item_id: Number(key), decision: staged.decision, note: staged.note,
          content_hash: staged.content_hash,
        })),
      );
      const stillFailed: Record<number, StagedAnswer> = {};
      const nextItemErrors: Record<number, string> = {};
      let success = 0;
      for (const result of response.results) {
        if (result.success) {
          success += 1;
        } else if (pendingAnswers[result.item_id]) {
          stillFailed[result.item_id] = pendingAnswers[result.item_id];
          nextItemErrors[result.item_id] = failureMessage(result.error);
        }
      }
      setPendingAnswers(stillFailed);
      setItemErrors(nextItemErrors);
      const failedCount = Object.keys(stillFailed).length;
      setBulkResult({ success, failed: failedCount });
      if (failedCount === 0) {
        toast.success(`${success}件を送信しました`);
      } else {
        toast.error(`${success}件送信、${failedCount}件失敗しました`);
      }
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBulkSending(false);
    }
  };

  const noReviewItems = full?.items_by_category["no_review_required"] ?? [];
  const unchangedItems = full?.items_by_category["unchanged"] ?? [];
  const informationalOnlyItems = full?.items_by_category["informational"] ?? [];
  const informationalItems = [...noReviewItems, ...unchangedItems, ...informationalOnlyItems];
  const informationalCount = informationalItems.length;

  // §5.4: deterministic spot-check — sorted by id ascending, capped at 3.
  // `unchanged` is excluded: it is a "nothing changed since last time"
  // finding, not a fresh no-review/informational judgement that needs the
  // same sampling scrutiny.
  const sampleEligible = [...noReviewItems, ...informationalOnlyItems].slice().sort((a, b) => a.id - b.id);
  const sampleItems = sampleEligible.length > 3 ? sampleEligible.slice(0, 3) : [];
  const sampleIds = new Set(sampleItems.map(i => i.id));
  const remainingInformationalItems = informationalItems.filter(i => !sampleIds.has(i.id));

  // PR #296 review fix (Finding 3): superseded rows are audit history, kept
  // fully visible but out of the counts/category groups above. `?? []`
  // degrades gracefully for a Control Server predating this field.
  const supersededItems = full?.superseded_items ?? [];

  return (
    <Card data-testid="review-queue-panel">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm flex items-center gap-2">
              レビューキュー(意図と現状の突き合わせ)
              <RefreshStatusChip sessionId={sessionId} />
            </CardTitle>
            <CardDescription>
              Intent Brief と現在の理解を突き合わせ、確認が必要な項目だけを表示します。回答後は自動で更新されます。
            </CardDescription>
          </div>
          {showRecoveryBuild && (
            <Button
              size="sm"
              variant="outline"
              onClick={handleBuild}
              disabled={build.isPending}
              data-testid="review-queue-build-button"
            >
              <Sparkles className="h-4 w-4 mr-1" />
              {build.isPending ? "分析中..." : "突き合わせをもう一度実行する"}
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-1.5" data-testid="review-queue-category-summary">
          {CATEGORY_SUMMARY.map(({ key, label }) => {
            // PR #296 review fix (2nd pass, Finding 3/5b): the actionable
            // categories (must_review/batch_reviewable) prefer
            // outstanding_counts (未対応件数, matching this panel's own
            // action-card count) over `counts` (a total that historically
            // stays inclusive of already-answered items server-side).
            // Non-actionable categories are unaffected and keep reading
            // `counts`. Falls back to `counts` when an older Control Server
            // doesn't send outstanding_counts yet.
            const isActionable = key === "must_review" || key === "batch_reviewable";
            const count = isActionable
              ? (full?.outstanding_counts?.[key] ?? full?.counts[key] ?? 0)
              : (full?.counts[key] ?? 0);
            return (
              <Badge key={key} variant="outline" data-testid={`review-queue-summary-${key}`}>
                {label} {count}件
              </Badge>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={toggleBulkMode}
            disabled={bulkSending}
            data-testid="review-queue-bulk-mode-toggle"
          >
            {bulkMode ? "個別に回答するモードに戻す" : "まとめて回答するモードにする"}
          </Button>
          {bulkMode && (
            <div className="flex items-center gap-2 text-xs" data-testid="review-queue-bulk-bar">
              <span data-testid="review-queue-bulk-pending-count">{pendingCount}件選択中</span>
              <Button
                size="sm"
                onClick={handleBulkSubmit}
                disabled={pendingCount === 0 || bulkSending}
                data-testid="review-queue-bulk-submit"
              >
                {bulkSending ? "送信中..." : "まとめて送信"}
              </Button>
              {bulkResult && (
                <span data-testid="review-queue-bulk-result">
                  {bulkResult.failed === 0
                    ? `${bulkResult.success}件送信しました`
                    : `${bulkResult.success}件送信、${bulkResult.failed}件失敗しました`}
                </span>
              )}
            </div>
          )}
        </div>

        {queue && queue.items.length === 0 && (
          <p className="text-xs text-muted-foreground" data-testid="review-queue-empty">
            確認が必要な項目はありません。
          </p>
        )}
        {(queue?.items ?? []).map(item => (
          <ReviewQueueItemCard
            key={item.id}
            item={item}
            sessionId={sessionId}
            existingInquiry={activeInquiries.get(`review_item:${item.id}`)}
            bulkMode={bulkMode}
            bulkSending={bulkSending}
            stagedAnswer={pendingAnswers[item.id]}
            stagedError={itemErrors[item.id]}
            onStageAnswer={(decision, note) => stageAnswer(item.id, decision, note, item.content_hash)}
            onUnstageAnswer={() => unstageAnswer(item.id)}
          />
        ))}

        {sampleItems.length > 0 && (
          <div className="space-y-2 pt-2 border-t" data-testid="review-queue-sample-section">
            <p className="text-xs font-semibold text-muted-foreground">
              確認不要と判断した項目のサンプル確認
            </p>
            {sampleItems.map(item => (
              <InformationalItemRow
                key={item.id}
                item={item}
                sessionId={sessionId}
                existingInquiry={activeInquiries.get(`review_item:${item.id}`)}
                sample
              />
            ))}
          </div>
        )}

        {(ruleObjections?.rules.length ?? 0) > 0 && (
          <div className="space-y-2 pt-2 border-t" data-testid="review-queue-rule-objections">
            <p className="text-xs font-semibold text-muted-foreground">
              サンプル確認で異議が出た分類ルール
            </p>
            {ruleObjections?.rules.map(rule => (
              <div
                key={`${rule.reason_code}:${rule.policy_version}:${rule.policy_digest ?? "legacy"}:${rule.policy_rule_id}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-2 text-xs"
              >
                <span>
                  {ruleLabel(rule.reason_code)}: 異議 {rule.objection_count}件
                  {rule.pending_recheck_count > 0 && ` / 再確認中 ${rule.pending_recheck_count}件`}
                  {` / ${rule.policy_rule_id} (${rule.policy_version})`}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={requestRecheck.isPending}
                  onClick={() => handleRequestRecheck(rule)}
                  data-testid={`review-queue-rule-recheck-${rule.policy_rule_id}`}
                >
                  同じ分類の項目を再確認する
                </Button>
              </div>
            ))}
          </div>
        )}

        {informationalCount > 0 && (
          <div className="pt-2 border-t">
            <button
              type="button"
              className="text-xs text-muted-foreground underline underline-offset-2"
              onClick={() => setShowInformational(s => !s)}
              aria-expanded={showInformational}
              data-testid="review-queue-informational-toggle"
            >
              {showInformational ? "対応不要の項目を隠す" : `対応不要の項目 (${informationalCount})`}
            </button>
            {showInformational && (
              <div className="mt-2 space-y-2" data-testid="review-queue-informational-list">
                {remainingInformationalItems.map(item => (
                  <InformationalItemRow
                    key={item.id}
                    item={item}
                    sessionId={sessionId}
                    existingInquiry={activeInquiries.get(`review_item:${item.id}`)}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {supersededReviewInquiries.length > 0 && (
          <div className="pt-2 border-t">
            <button
              type="button"
              className="text-xs text-muted-foreground underline underline-offset-2"
              onClick={() => setShowSupersededInquiries(s => !s)}
              aria-expanded={showSupersededInquiries}
              data-testid="review-queue-superseded-inquiry-toggle"
            >
              {showSupersededInquiries
                ? "前提が変わった疑問を隠す"
                : `前提が変わった疑問 ${supersededReviewInquiries.length}件`}
            </button>
            {showSupersededInquiries && (
              <div className="mt-2 space-y-2" data-testid="review-queue-superseded-inquiry-list">
                {supersededReviewInquiries.map(inquiry => (
                  <div
                    key={inquiry.id}
                    className="rounded-md border p-2 text-xs space-y-1"
                    data-testid={`superseded-inquiry-${inquiry.id}`}
                  >
                    <p className="text-muted-foreground">
                      当時の確認項目: #{inquiry.origin_id}
                    </p>
                    {/* 表示も導線もサーバーのフィールドだけから作る。ここには
                        回答・承認のアクションを一切置かない — 履歴を見ること
                        が元の確認項目の回答になってはいけない(#285/#322)。 */}
                    <InquiryPremiseNotice inquiry={inquiry} />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {supersededItems.length > 0 && (
          <div className="pt-2 border-t">
            <button
              type="button"
              className="text-xs text-muted-foreground underline underline-offset-2"
              onClick={() => setShowSuperseded(s => !s)}
              aria-expanded={showSuperseded}
              data-testid="review-queue-superseded-toggle"
            >
              {showSuperseded ? "履歴を隠す" : `履歴 ${supersededItems.length}件`}
            </button>
            {showSuperseded && (
              <div className="mt-2 space-y-2" data-testid="review-queue-superseded-list">
                {supersededItems.map(item => (
                  <InformationalItemRow
                    key={item.id}
                    item={item}
                    sessionId={sessionId}
                    existingInquiry={activeInquiries.get(`review_item:${item.id}`)}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
