// Issue #285: Inquiry lifecycle(保留・疑問解消・復帰)。
//
// 確認項目(Q&A の質問 / Intent Brief の項目 / 将来の review item)に疑問が
// あるとき、元の項目はいったん保留し、別の Inquiry 会話で疑問を解消する。
// 「疑問は解消した」は元の項目への回答・確認とは厳密に別物であり、この
// パネルは元の項目の状態を一切変更しない — 解消後は元の入力欄に戻り、
// ユーザーが明示的に送信するまで自動では回答しない。

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { JointUnderstandingEntry } from "@/components/system-understanding/joint-understanding-entry";
import { InquiryPremiseNotice } from "@/components/system-understanding/inquiry-premise-notice";
import {
  useCreateInterviewInquiry,
  useCreateObservationProposal,
  useHoldInterviewInquiry,
  useInterviewInquiryDetail,
  useReopenInterviewInquiryDoubt,
  useResolveInterviewInquiry,
  useSendInterviewInquiryMessage,
  recordInterviewMetricEventBestEffort,
} from "@/api/hooks";
import type {
  InquiryRouteCategory,
  InterviewInquiryMessageDetailOut,
  InterviewInquiryMessageOut,
  InterviewInquiryOriginKind,
  InterviewInquiryRuntimeEvidenceOut,
  RuntimeFactFreshness,
} from "@/api/types";

// Issue #286: Question Router category -> 日本語ラベル。canonical enum
// (human_only | system_researchable | hybrid) は英語のまま内部で保持し、
// 表示ラベルだけ日本語に変換する(Issue #266 の規約どおり、raw な enum
// 文字列を画面に出さない)。Exported so the normal Q&A flow's route badge
// (Issue #286 review fix, Finding 1; components/qa-panel usage in
// pages/interview.tsx) reuses the same labels instead of a second copy.
export const ROUTE_CATEGORY_LABELS: Record<InquiryRouteCategory, string> = {
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

// Issue #295 ST-3 (§4.4/§4.10): 4-layer progressive disclosure for an
// assistant Inquiry answer. Layer 1 (content, always visible) already comes
// from `message.content`. The remaining three layers are built ONLY from
// fields the existing `detail` payload already carries -- no new backend
// data, no restructuring of `detail` itself, just splitting how much of it
// is shown at once:
//   layer 2 「理由を見る」        -- detail.key_points (short reasons)
//   layer 3 「根拠を見る」        -- evidence + runtime_evidence summaries
//                                     (kind + short overview) + uncertainty
//   layer 4 「調査詳細を見る」    -- exact file:line evidence, full runtime
//                                     provenance chips, the observation-
//                                     proposal action, and the audit run
//                                     reference (intelligence_run_id)
// A layer whose backing fields are all empty renders no toggle at all.
type EvidenceKind = "docs" | "test" | "code";

const EVIDENCE_KIND_LABELS: Record<EvidenceKind, string> = {
  docs: "ドキュメント",
  test: "テスト",
  code: "コード",
};

// Deterministic structural classification of an evidence citation's source
// kind, from the path shape alone (Principle 6 allows "file kind" as a
// direct structural check) -- never a semantic/LLM judgment.
function classifyEvidenceKind(path: string): EvidenceKind {
  const lower = path.toLowerCase();
  if (lower.includes("test")) return "test";
  if (lower.startsWith("docs/") || lower.endsWith(".md")) return "docs";
  return "code";
}

// Issue #295 §4.4 exception: pre-expand layers 2-3 up front when the
// response's OWN existing fields already signal a conflict or unresolved
// doubt -- judged only from finite/structural fields already in `detail`
// (a runtime_fact `mismatch` check, or a non-empty `uncertainty` note),
// never by pattern-matching free-text content.
function needsUpfrontDisclosure(detail: InterviewInquiryMessageDetailOut | null): boolean {
  if (!detail) return false;
  const hasRuntimeMismatch = (detail.runtime_evidence ?? []).some(e => e.runtime_check === "mismatch");
  return hasRuntimeMismatch || detail.uncertainty.trim() !== "";
}

function InquiryMessageBubble({ message, sessionId }: { message: InterviewInquiryMessageOut; sessionId: number }) {
  const isAssistant = message.role === "assistant";
  const detail = message.detail;

  const keyPoints = detail?.key_points ?? [];
  const evidence = detail?.evidence ?? [];
  const runtimeEvidence = detail?.runtime_evidence ?? [];
  const uncertainty = detail?.uncertainty ?? "";

  const hasReasons = keyPoints.length > 0;
  const hasEvidenceSummary = evidence.length > 0 || runtimeEvidence.length > 0 || uncertainty !== "";
  const hasAuditDetail = evidence.length > 0 || runtimeEvidence.length > 0;

  const preExpand = needsUpfrontDisclosure(detail);
  const [showReasons, setShowReasons] = useState(preExpand && hasReasons);
  const [showEvidence, setShowEvidence] = useState(preExpand && hasEvidenceSummary);
  const [showAudit, setShowAudit] = useState(false);
  const evidenceExpandedRecorded = useRef(false);
  const evidenceMetricsEligible = hasEvidenceSummary && !preExpand;

  useEffect(() => {
    if (!isAssistant || !evidenceMetricsEligible) return;
    void recordInterviewMetricEventBestEffort({
      schema_version: "interview-metric-event-v1",
      event_key: `evidence_available:inquiry_message:${message.id}`,
      session_id: sessionId,
      event_type: "evidence_available",
      target_kind: "inquiry_message",
      target_id: message.id,
    });
  }, [evidenceMetricsEligible, isAssistant, message.id, sessionId]);

  const toggleEvidence = () => {
    const nextShow = !showEvidence;
    setShowEvidence(nextShow);
    if (nextShow && evidenceMetricsEligible && !evidenceExpandedRecorded.current) {
      evidenceExpandedRecorded.current = true;
      void recordInterviewMetricEventBestEffort({
        schema_version: "interview-metric-event-v1",
        event_key: `evidence_expanded:inquiry_message:${message.id}`,
        session_id: sessionId,
        event_type: "evidence_expanded",
        target_kind: "inquiry_message",
        target_id: message.id,
      });
    }
  };

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

      {isAssistant && hasReasons && (
        <div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowReasons(s => !s)}
            data-testid={`inquiry-show-reasons-${message.id}`}
          >
            {showReasons ? "理由を隠す" : "理由を見る"}
          </Button>
          {showReasons && (
            <ul
              className="mt-1 list-disc rounded bg-background/60 p-2 pl-6 space-y-0.5"
              data-testid={`inquiry-reasons-detail-${message.id}`}
            >
              {keyPoints.map((k, i) => <li key={i}>{k}</li>)}
            </ul>
          )}
        </div>
      )}

      {isAssistant && hasEvidenceSummary && (
        <div>
          <Button
            size="sm"
            variant="outline"
            onClick={toggleEvidence}
            data-testid={`inquiry-show-evidence-${message.id}`}
          >
            {showEvidence ? "根拠を隠す" : "根拠を見る"}
          </Button>
          {showEvidence && (
            <div className="mt-1 space-y-1 rounded bg-background/60 p-2" data-testid={`inquiry-evidence-detail-${message.id}`}>
              {evidence.map((e, i) => (
                <p key={i}>
                  <span className="rounded bg-muted px-1 mr-1">{EVIDENCE_KIND_LABELS[classifyEvidenceKind(e.path)]}</span>
                  {e.summary || "(概要なし)"}
                </p>
              ))}
              {runtimeEvidence.map(entry => (
                <p key={entry.component_id}>
                  <span className="rounded bg-muted px-1 mr-1">Runtime</span>
                  {entry.summary}
                </p>
              ))}
              {uncertainty && (
                <p className="text-muted-foreground">不確実な点: {uncertainty}</p>
              )}
            </div>
          )}
        </div>
      )}

      {isAssistant && hasAuditDetail && (
        <div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowAudit(s => !s)}
            data-testid={`inquiry-show-audit-${message.id}`}
          >
            {showAudit ? "調査詳細を隠す" : "調査詳細を見る"}
          </Button>
          {showAudit && (
            <div className="mt-1 space-y-1 rounded bg-background/60 p-2" data-testid={`inquiry-audit-detail-${message.id}`}>
              {evidence.map((e, i) => (
                <p key={i} className="font-mono text-[10px] text-muted-foreground">
                  {e.path}:{e.start_line}-{e.end_line}{e.summary ? ` — ${e.summary}` : ""}
                </p>
              ))}
              {runtimeEvidence.map(entry => (
                <RuntimeEvidenceChips key={entry.component_id} entry={entry} />
              ))}
              {detail?.suggested_observation_proposal && (
                <SuggestedObservationProposalCard
                  sessionId={sessionId}
                  targetComponent={detail.suggested_observation_proposal.target_component}
                  reason={detail.suggested_observation_proposal.reason}
                />
              )}
              {message.intelligence_run_id != null && (
                <p className="text-[10px] text-muted-foreground">調査 run: {message.intelligence_run_id}</p>
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
  // Issue #322: a superseded Inquiry is terminal and system-written — the
  // premise it was answered against no longer exists, and /message,
  // /resolve, /resume all 409 server-side. `isOpen` above already hides
  // every action for it; this only swaps the 保留中 header for the premise
  // notice so the state is readable instead of looking like a stalled
  // conversation.
  const supersededInquiry = detail && detail.inquiry.status === "superseded"
    ? detail.inquiry
    : null;

  return (
    <div
      className="rounded-md border border-amber-500/60 bg-amber-500/5 p-3 space-y-2"
      data-testid="inquiry-panel"
    >
      {!supersededInquiry && (
        <p className="text-xs font-medium text-amber-700" data-testid="inquiry-held-marker">
          保留中(疑問を解消してから回答)
        </p>
      )}
      {detail && <InquiryPremiseNotice inquiry={detail.inquiry} />}

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
                {/* Issue #336: 「疑問が解消しなかった」 is the most common way a
                    developer discovers they do not know either (Epic #328), so
                    the 共同理解 flow is reachable from here too. It never
                    resolves, reopens, or holds the Inquiry — those stay the
                    buttons beside it, and the Inquiry's own status is untouched. */}
                {inquiryId !== null && (
                  <JointUnderstandingEntry
                    sessionId={sessionId}
                    originKind="inquiry"
                    originId={inquiryId}
                    questionText="この疑問は自分でも判断できません。一緒に確かめたいです"
                    label="一緒に確かめる"
                  />
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
