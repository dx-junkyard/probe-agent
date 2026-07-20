// Issue #285: Inquiry lifecycle(保留・疑問解消・復帰)。
//
// 確認項目(Q&A の質問 / Intent Brief の項目 / 将来の review item)に疑問が
// あるとき、元の項目はいったん保留し、別の Inquiry 会話で疑問を解消する。
// 「疑問は解消した」は元の項目への回答・確認とは厳密に別物であり、この
// パネルは元の項目の状態を一切変更しない — 解消後は元の入力欄に戻り、
// ユーザーが明示的に送信するまで自動では回答しない。

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateInterviewInquiry,
  useCreateObservationProposal,
  useHoldInterviewInquiry,
  useInterviewInquiryDetail,
  useReopenInterviewInquiryDoubt,
  useResolveInterviewInquiry,
  useSendInterviewInquiryMessage,
} from "@/api/hooks";
import type {
  InquiryRouteCategory,
  InterviewInquiryMessageOut,
  InterviewInquiryOriginKind,
  InterviewInquiryRuntimeEvidenceOut,
  RuntimeFactFreshness,
} from "@/api/types";

// Issue #286: Question Router category -> 日本語ラベル。canonical enum
// (human_only | system_researchable | hybrid) は英語のまま内部で保持し、
// 表示ラベルだけ日本語に変換する(Issue #266 の規約どおり、raw な enum
// 文字列を画面に出さない)。
const ROUTE_CATEGORY_LABELS: Record<InquiryRouteCategory, string> = {
  system_researchable: "AI が調査して回答",
  human_only: "あなたの判断が必要",
  hybrid: "調査 + あなたの判断",
};

// Issue #290: runtime_fact provenance の鮮度 -> 日本語ラベル。
const FRESHNESS_LABELS: Record<RuntimeFactFreshness, string> = {
  fresh: "最新",
  stale: "古い",
  unobserved: "未観測",
};

function formatObservedAt(ts: number | null): string {
  if (ts === null) return "—";
  return new Date(ts * 1000).toLocaleString("ja-JP");
}

function RuntimeEvidenceChips({ entry }: { entry: InterviewInquiryRuntimeEvidenceOut }) {
  const { provenance } = entry;
  const isStale = provenance.freshness === "stale";
  return (
    <div
      className="rounded border border-muted-foreground/20 bg-background/60 p-2 space-y-1"
      data-testid={`inquiry-runtime-evidence-${entry.component_id}`}
    >
      <p className="font-mono text-[10px]">{entry.component_id}</p>
      <div className="flex flex-wrap gap-1 text-[10px]">
        <span className="rounded bg-muted px-1" data-testid="runtime-chip-environment">
          環境: {provenance.environment ?? "不明"}
        </span>
        <span className="rounded bg-muted px-1" data-testid="runtime-chip-observed-at">
          観測日時: {formatObservedAt(provenance.last_observed_at)}
        </span>
        <span className="rounded bg-muted px-1" data-testid="runtime-chip-snapshot">
          snapshot: {provenance.snapshot_ref?.snapshot_id ?? "—"}
        </span>
        <span
          className={`rounded px-1 ${isStale ? "bg-amber-500/20 text-amber-800" : "bg-muted"}`}
          data-testid="runtime-chip-freshness"
        >
          鮮度: {FRESHNESS_LABELS[provenance.freshness]}
        </span>
      </div>
      {entry.summary && <p className="text-muted-foreground">{entry.summary}</p>}
      {isStale && (
        <p className="font-medium text-amber-700" data-testid="runtime-evidence-stale-warning">
          古い観測です
        </p>
      )}
    </div>
  );
}

function SuggestedObservationProposalCard({
  sessionId, targetComponent, reason,
}: {
  sessionId: number;
  targetComponent: string;
  reason: "unobserved" | "stale";
}) {
  const [open, setOpen] = useState(false);
  const [purpose, setPurpose] = useState("");
  const create = useCreateObservationProposal(sessionId);
  const [submitted, setSubmitted] = useState(false);

  if (submitted) {
    return (
      <p className="text-[11px] text-muted-foreground" data-testid="observation-proposal-suggested-submitted">
        新規観測の提案を送信しました。承認されるまで観測は開始されません。
      </p>
    );
  }

  return (
    <div className="rounded border border-sky-500/40 bg-sky-500/5 p-2 space-y-1" data-testid="observation-proposal-suggested">
      <p className="text-[11px] text-sky-800">
        「{targetComponent}」は{reason === "unobserved" ? "実行時の観測データがありません" : "観測データが古くなっています"}。
        新しい観測を開始したい場合は、承認が必要な提案として送信できます(送信しただけでは観測は開始されません)。
      </p>
      {open ? (
        <div className="space-y-1">
          <Textarea
            value={purpose}
            onChange={e => setPurpose(e.target.value)}
            placeholder="観測の目的を入力してください"
            rows={2}
            data-testid="observation-proposal-suggested-purpose"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={create.isPending || !purpose.trim()}
              onClick={() => create.mutate(
                { target_component: targetComponent, purpose },
                {
                  onSuccess: () => { setSubmitted(true); toast.success("観測の提案を送信しました"); },
                  onError: e => toast.error(String(e)),
                },
              )}
              data-testid="observation-proposal-suggested-submit"
            >
              提案を送信
            </Button>
            <Button size="sm" variant="outline" onClick={() => setOpen(false)}>キャンセル</Button>
          </div>
        </div>
      ) : (
        <Button size="sm" variant="outline" onClick={() => setOpen(true)} data-testid="observation-proposal-suggested-open">
          新規観測を提案する
        </Button>
      )}
    </div>
  );
}

function InquiryMessageBubble({ message, sessionId }: { message: InterviewInquiryMessageOut; sessionId: number }) {
  const [showEvidence, setShowEvidence] = useState(false);
  const isAssistant = message.role === "assistant";
  const hasDetail = !!message.detail && (
    message.detail.key_points.length > 0
    || message.detail.evidence.length > 0
    || !!message.detail.uncertainty
    || (message.detail.runtime_evidence?.length ?? 0) > 0
  );

  return (
    <div
      className={`rounded-md border p-2 text-xs space-y-1 ${isAssistant ? "bg-muted/40" : ""}`}
      data-testid={`inquiry-message-${message.id}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-muted-foreground">
          {isAssistant ? "アシスタント" : "あなた"}
        </span>
        <div className="flex items-center gap-1">
          {isAssistant && message.detail?.route_category && (
            <span
              className="rounded bg-sky-500/20 px-1 text-[10px] text-sky-700"
              data-testid={`inquiry-message-route-${message.id}`}
            >
              {ROUTE_CATEGORY_LABELS[message.detail.route_category]}
            </span>
          )}
          {isAssistant && message.is_mock && (
            <span
              className="rounded bg-amber-500/20 px-1 text-[10px] text-amber-700"
              data-testid={`inquiry-message-mock-${message.id}`}
            >
              mock
            </span>
          )}
        </div>
      </div>
      <p className="whitespace-pre-wrap break-words">{message.content}</p>
      {isAssistant && message.detail?.decision_question && (
        <p
          className="rounded border border-amber-500/60 bg-amber-500/10 px-2 py-1 font-medium text-amber-800"
          data-testid={`inquiry-message-decision-question-${message.id}`}
        >
          確認したいこと: {message.detail.decision_question}
        </p>
      )}
      {isAssistant && hasDetail && (
        <div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowEvidence(s => !s)}
            data-testid={`inquiry-show-evidence-${message.id}`}
          >
            {showEvidence ? "根拠を隠す" : "根拠を見る"}
          </Button>
          {showEvidence && (
            <div className="mt-1 space-y-1 rounded bg-background/60 p-2" data-testid={`inquiry-evidence-detail-${message.id}`}>
              {message.detail!.key_points.length > 0 && (
                <ul className="list-disc pl-4">
                  {message.detail!.key_points.map((k, i) => <li key={i}>{k}</li>)}
                </ul>
              )}
              {message.detail!.evidence.map((e, i) => (
                <p key={i} className="font-mono text-[10px] text-muted-foreground">
                  {e.path}:{e.start_line}-{e.end_line}{e.summary ? ` — ${e.summary}` : ""}
                </p>
              ))}
              {message.detail!.uncertainty && (
                <p className="text-muted-foreground">不確実な点: {message.detail!.uncertainty}</p>
              )}
              {(message.detail!.runtime_evidence ?? []).map(entry => (
                <RuntimeEvidenceChips key={entry.component_id} entry={entry} />
              ))}
              {message.detail!.suggested_observation_proposal && (
                <SuggestedObservationProposalCard
                  sessionId={sessionId}
                  targetComponent={message.detail!.suggested_observation_proposal.target_component}
                  reason={message.detail!.suggested_observation_proposal.reason}
                />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function InquiryPanel({
  sessionId, originKind, originId, heldDraft, existingInquiryId, onResolved, onHeld, onCancel,
}: {
  sessionId: number;
  originKind: InterviewInquiryOriginKind;
  originId: number;
  heldDraft: string | null;
  // Issue #285 refresh/resume: when set, this panel re-attaches to an
  // already-open Inquiry (rediscovered via the list endpoint after a page
  // reload, or just resumed from 'held') instead of starting the "ask a new
  // question" form.
  existingInquiryId?: number;
  onResolved: (heldDraft: string | null) => void;
  // Reports the held Inquiry's id back to the caller so a later 「疑問を
  // 再開する」 action can call /resume against the right id, even for an
  // Inquiry created fresh in this same panel (not rediscovered via the list
  // endpoint).
  onHeld: (inquiryId: number) => void;
  onCancel: () => void;
}) {
  const [inquiryId, setInquiryId] = useState<number | null>(existingInquiryId ?? null);
  const [questionDraft, setQuestionDraft] = useState("");
  const [followupDraft, setFollowupDraft] = useState("");

  const create = useCreateInterviewInquiry(sessionId);
  const { data: detail } = useInterviewInquiryDetail(inquiryId);
  const sendMessage = useSendInterviewInquiryMessage(sessionId);
  const resolve = useResolveInterviewInquiry(sessionId);
  const hold = useHoldInterviewInquiry(sessionId);
  const reopenDoubt = useReopenInterviewInquiryDoubt(sessionId);

  const submitQuestion = () => {
    if (!questionDraft.trim()) {
      toast.error("疑問の内容を入力してください");
      return;
    }
    create.mutate(
      {
        origin_kind: originKind, origin_id: originId,
        question_text: questionDraft, held_draft: heldDraft ?? undefined,
      },
      {
        onSuccess: result => setInquiryId(result.inquiry.id),
        onError: e => toast.error(String(e)),
      },
    );
  };

  const submitFollowup = () => {
    if (!inquiryId || !followupDraft.trim()) return;
    sendMessage.mutate(
      { inquiryId, content: followupDraft },
      {
        onSuccess: () => setFollowupDraft(""),
        onError: e => toast.error(String(e)),
      },
    );
  };

  const handleResolve = () => {
    if (!inquiryId) return;
    resolve.mutate(
      { inquiryId },
      {
        onSuccess: result => {
          toast.success("疑問を解消しました。元の項目に戻ります。");
          onResolved(result.held_draft);
        },
        onError: e => toast.error(String(e)),
      },
    );
  };

  const handleReopenDoubt = () => {
    if (!inquiryId) return;
    reopenDoubt.mutate({ inquiryId }, { onError: e => toast.error(String(e)) });
  };

  const handleHold = () => {
    if (!inquiryId) return;
    const heldId = inquiryId;
    hold.mutate(
      { inquiryId },
      {
        onSuccess: () => {
          toast.info("今回は保留しました。あとで再開できます。");
          onHeld(heldId);
        },
        onError: e => toast.error(String(e)),
      },
    );
  };

  const isOpen = !inquiryId || detail?.inquiry.status === "open";

  return (
    <div
      className="rounded-md border border-amber-500/60 bg-amber-500/5 p-3 space-y-2"
      data-testid="inquiry-panel"
    >
      <p className="text-xs font-medium text-amber-700" data-testid="inquiry-held-marker">
        保留中(疑問を解消してから回答)
      </p>

      {detail && (
        <div className="space-y-2 max-h-64 overflow-y-auto pr-1" data-testid="inquiry-conversation">
          {detail.messages.map(m => <InquiryMessageBubble key={m.id} message={m} sessionId={sessionId} />)}
        </div>
      )}

      {!inquiryId ? (
        <div className="space-y-2">
          <Textarea
            value={questionDraft}
            onChange={e => setQuestionDraft(e.target.value)}
            placeholder="どんな疑問がありますか?"
            rows={2}
            data-testid="inquiry-question-input"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={submitQuestion}
              disabled={create.isPending}
              data-testid="inquiry-question-submit"
            >
              {create.isPending ? "送信中..." : "質問する"}
            </Button>
            <Button size="sm" variant="outline" onClick={onCancel} data-testid="inquiry-cancel">
              キャンセル
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {isOpen && (
            <>
              <Textarea
                value={followupDraft}
                onChange={e => setFollowupDraft(e.target.value)}
                placeholder="追加の質問を入力"
                rows={2}
                data-testid="inquiry-followup-input"
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={submitFollowup}
                  disabled={sendMessage.isPending || !followupDraft.trim()}
                  data-testid="inquiry-followup-submit"
                >
                  追加で送信
                </Button>
                <Button
                  size="sm"
                  onClick={handleResolve}
                  disabled={resolve.isPending}
                  data-testid="inquiry-resolve"
                >
                  疑問は解消した
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleReopenDoubt}
                  disabled={reopenDoubt.isPending}
                  data-testid="inquiry-reopen-doubt"
                >
                  解消していない(追加で質問)
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleHold}
                  disabled={hold.isPending}
                  data-testid="inquiry-hold"
                >
                  今回は保留する
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
