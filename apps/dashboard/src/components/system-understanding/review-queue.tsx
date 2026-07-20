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

import { useState } from "react";
import { toast } from "sonner";
import { AlertCircle, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { InquiryPanel } from "@/components/system-understanding/inquiry-panel";
import { RefreshStatusChip } from "@/components/system-understanding/refresh-status-chip";
import { HandoffModal } from "@/components/system-understanding/handoff-panel";
import {
  useActiveInquiriesByOrigin,
  useAlignmentList,
  useAnswerAlignmentItem,
  useBuildAlignment,
  useCorrectAlignmentItem,
  useHoldAlignmentItem,
  useReviewQueue,
} from "@/api/hooks";
import type {
  AlignmentDecisionAction,
  AlignmentItemOut,
  AlignmentRiskFlag,
  AlignmentState,
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

const DECISION_LABELS: Record<AlignmentDecisionAction, string> = {
  accept_current: "現状でよい",
  needs_change: "変更が必要",
  reject_interpretation: "AIの解釈を採用しない",
};

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

function EvidenceList({ item }: { item: AlignmentItemOut }) {
  const [open, setOpen] = useState(false);
  if (item.current_evidence.length === 0) return null;
  return (
    <div>
      <button
        type="button"
        className="text-xs text-primary underline underline-offset-2"
        onClick={() => setOpen(o => !o)}
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

function ReviewQueueItemCard({
  item, sessionId, existingInquiry,
}: {
  item: AlignmentItemOut;
  sessionId: number;
  existingInquiry?: InterviewInquiryOut;
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
      className={`rounded-md border p-3 space-y-2 ${isMustReview ? "border-destructive/60 bg-destructive/5" : ""}`}
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
          <Button
            size="sm"
            onClick={() => setAnswering(true)}
            disabled={busy}
            data-testid={`review-item-answer-open-${item.id}`}
          >
            回答する
          </Button>
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

function InformationalItemRow({ item }: { item: AlignmentItemOut }) {
  return (
    <div className="rounded-md border p-2 text-xs space-y-1" data-testid={`review-item-informational-${item.id}`}>
      <p className="break-words"><span className="font-semibold">現状:</span> {item.current_claim}</p>
      <div className="flex flex-wrap items-center gap-1 text-[11px]">
        <Badge variant="outline">{alignmentStateLabel(item.alignment_state)}</Badge>
        <Badge variant="secondary">{item.user_reason}</Badge>
        {item.superseded && (
          <Badge variant="outline" data-testid={`review-item-superseded-${item.id}`}>履歴</Badge>
        )}
      </div>
    </div>
  );
}

export function ReviewQueuePanel({ sessionId }: { sessionId: number }) {
  const { data: queue } = useReviewQueue(sessionId);
  const { data: full } = useAlignmentList(sessionId);
  const build = useBuildAlignment(sessionId);
  const activeInquiries = useActiveInquiriesByOrigin(sessionId);
  const [showInformational, setShowInformational] = useState(false);

  const handleBuild = () => {
    build.mutate(undefined, {
      onSuccess: result => {
        toast.success(`${result.items.length} 件の突き合わせ結果を更新しました`);
      },
      onError: e => toast.error(String(e)),
    });
  };

  const informationalItems = full
    ? [
        ...(full.items_by_category["no_review_required"] ?? []),
        ...(full.items_by_category["unchanged"] ?? []),
        ...(full.items_by_category["informational"] ?? []),
      ]
    : [];
  const informationalCount = informationalItems.length;

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
          <Button
            size="sm"
            variant="outline"
            onClick={handleBuild}
            disabled={build.isPending}
            data-testid="review-queue-build-button"
          >
            <Sparkles className="h-4 w-4 mr-1" />
            {build.isPending ? "分析中..." : "突き合わせを実行"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
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
          />
        ))}

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
                {informationalItems.map(item => <InformationalItemRow key={item.id} item={item} />)}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
