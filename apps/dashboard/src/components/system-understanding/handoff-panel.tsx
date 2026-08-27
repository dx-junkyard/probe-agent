// Issue #291: 担当者引き継ぎ(handoff)。
//
// 対象外(担当外)や後回しにしたい質問・Review Queue 項目を、別の担当者へ
// 引き継ぐ。担当者自身の回答は引き継ぎ行にのみ記録され、元の質問/レビュー
// 項目の回答・判断フィールドには一切書き込まれない(Principle 2)。
// 「returned」になった引き継ぎは、元のユーザーが既存の回答エンドポイント
// (Q&A の回答 / Alignment の修正)を通じて明示的に確定する。
//
// HandoffModal は QaPanel / ReviewQueuePanel の双方から
// origin_kind/origin_id/背景の事前入力を渡されて開く小さいフォーム。
// HandoffListPanel はセッション全体の引き継ぎ一覧(pending/answered/
// returned/cancelled)を表示し、returned の確定操作を提供する。

import { useState } from "react";
import { toast } from "sonner";
import { LifeBuoy } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  useAnswerInterviewQa,
  useAnswerQuestionHandoff,
  useAnswerAlignmentItem,
  useCancelQuestionHandoff,
  useCorrectAlignmentItem,
  useCreateQuestionHandoff,
  useQuestionHandoffs,
  useReturnQuestionHandoff,
} from "@/api/hooks";
import type { HandoffOriginKind, HandoffPriority, QuestionHandoffOut } from "@/api/types";

const PRIORITY_LABELS: Record<HandoffPriority, string> = {
  low: "低",
  normal: "通常",
  high: "高",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "未対応",
  answered: "担当者回答済み",
  returned: "確認待ち",
  cancelled: "キャンセル済み",
};

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

function statusBadgeVariant(status: string): BadgeVariant {
  if (status === "returned") return "warning";
  if (status === "answered") return "secondary";
  if (status === "cancelled") return "outline";
  return "default";
}

// --- 引き継ぎフォーム(モーダル) -------------------------------------------

export function HandoffModal({
  sessionId, originKind, originId, defaultBackground, defaultEvidence, open, onOpenChange, onCreated,
}: {
  sessionId: number;
  originKind: HandoffOriginKind;
  originId: number;
  defaultBackground?: string;
  defaultEvidence?: { path: string; start_line: number; end_line: number; summary: string }[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: () => void;
}) {
  const create = useCreateQuestionHandoff(sessionId);
  const [assignee, setAssignee] = useState("");
  const [background, setBackground] = useState(defaultBackground ?? "");
  const [neededDecision, setNeededDecision] = useState("");
  const [priority, setPriority] = useState<HandoffPriority>("normal");
  const [dueNote, setDueNote] = useState("");

  const submit = () => {
    if (!assignee.trim() || !background.trim() || !neededDecision.trim()) {
      toast.error("担当者・背景・決めてほしいことを入力してください");
      return;
    }
    create.mutate(
      {
        origin_kind: originKind,
        origin_id: originId,
        assignee: assignee.trim(),
        background: background.trim(),
        needed_decision: neededDecision.trim(),
        evidence: defaultEvidence,
        due_note: dueNote.trim() || undefined,
        priority,
      },
      {
        onSuccess: () => {
          toast.success("担当者へ引き継ぎました");
          onOpenChange(false);
          onCreated?.();
        },
        onError: e => toast.error(String(e)),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogHeader>
        <DialogTitle>担当者へ引き継ぐ</DialogTitle>
      </DialogHeader>
      <div className="space-y-3" data-testid="handoff-modal-form">
        <div>
          <Label>担当者</Label>
          <Input
            value={assignee}
            onChange={e => setAssignee(e.target.value)}
            placeholder="氏名やメールアドレスなど"
            data-testid="handoff-assignee-input"
          />
        </div>
        <div>
          <Label>背景</Label>
          <Textarea
            value={background}
            onChange={e => setBackground(e.target.value)}
            rows={3}
            data-testid="handoff-background-input"
          />
        </div>
        <div>
          <Label>決めてほしいこと</Label>
          <Textarea
            value={neededDecision}
            onChange={e => setNeededDecision(e.target.value)}
            rows={2}
            data-testid="handoff-needed-decision-input"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>優先度</Label>
            <Select
              value={priority}
              onChange={e => setPriority(e.target.value as HandoffPriority)}
              data-testid="handoff-priority-select"
            >
              {(Object.keys(PRIORITY_LABELS) as HandoffPriority[]).map(p => (
                <option key={p} value={p}>{PRIORITY_LABELS[p]}</option>
              ))}
            </Select>
          </div>
          <div>
            <Label>期限メモ(任意)</Label>
            <Input value={dueNote} onChange={e => setDueNote(e.target.value)} data-testid="handoff-due-note-input" />
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>キャンセル</Button>
          <Button onClick={submit} disabled={create.isPending} data-testid="handoff-submit-button">
            {create.isPending ? "送信中..." : "引き継ぐ"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// --- 一覧パネル ---------------------------------------------------------------

function PendingHandoffCard({ handoff }: { handoff: QuestionHandoffOut }) {
  const answer = useAnswerQuestionHandoff(handoff.session_id);
  const cancel = useCancelQuestionHandoff(handoff.session_id);
  const [answering, setAnswering] = useState(false);
  const [answerText, setAnswerText] = useState("");
  const [answeredBy, setAnsweredBy] = useState("");

  const submitAnswer = () => {
    if (!answerText.trim() || !answeredBy.trim()) {
      toast.error("回答と回答者名を入力してください");
      return;
    }
    answer.mutate(
      { handoffId: handoff.id, answer_text: answerText.trim(), answered_by: answeredBy.trim() },
      {
        onSuccess: () => { toast.success("担当者の回答を記録しました"); setAnswering(false); },
        onError: e => toast.error(String(e)),
      },
    );
  };

  return (
    <div className="rounded-md border p-3 space-y-2 text-xs" data-testid={`handoff-item-${handoff.id}`}>
      <HandoffSummary handoff={handoff} />
      {answering ? (
        <div className="space-y-2">
          <Input
            value={answeredBy}
            onChange={e => setAnsweredBy(e.target.value)}
            placeholder="回答者(担当者)"
            data-testid={`handoff-answered-by-${handoff.id}`}
          />
          <Textarea
            value={answerText}
            onChange={e => setAnswerText(e.target.value)}
            rows={2}
            placeholder="担当者としての回答"
            data-testid={`handoff-answer-text-${handoff.id}`}
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={submitAnswer} disabled={answer.isPending} data-testid={`handoff-answer-submit-${handoff.id}`}>
              回答を記録
            </Button>
            <Button size="sm" variant="outline" onClick={() => setAnswering(false)}>キャンセル</Button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setAnswering(true)} data-testid={`handoff-answer-open-${handoff.id}`}>
            担当者として回答する
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => cancel.mutate({ handoffId: handoff.id }, {
              onSuccess: () => toast.info("引き継ぎをキャンセルしました"),
              onError: e => toast.error(String(e)),
            })}
            disabled={cancel.isPending}
            data-testid={`handoff-cancel-${handoff.id}`}
          >
            キャンセル
          </Button>
        </div>
      )}
    </div>
  );
}

function AnsweredHandoffCard({ handoff }: { handoff: QuestionHandoffOut }) {
  const returnHandoff = useReturnQuestionHandoff(handoff.session_id);
  return (
    <div className="rounded-md border p-3 space-y-2 text-xs" data-testid={`handoff-item-${handoff.id}`}>
      <HandoffSummary handoff={handoff} />
      <p className="rounded bg-muted/40 p-2 whitespace-pre-wrap break-words">{handoff.answer_text}</p>
      <Button
        size="sm"
        onClick={() => returnHandoff.mutate({ handoffId: handoff.id }, {
          onSuccess: () => toast.success("元の項目へ差し戻しました。確認して回答を確定してください。"),
          onError: e => toast.error(String(e)),
        })}
        disabled={returnHandoff.isPending}
        data-testid={`handoff-return-${handoff.id}`}
      >
        元の項目へ戻す
      </Button>
    </div>
  );
}

function ReturnedHandoffCard({ handoff, actor }: { handoff: QuestionHandoffOut; actor: string }) {
  const answerQa = useAnswerInterviewQa(handoff.session_id);
  const correctAlignment = useCorrectAlignmentItem(handoff.session_id);
  const answerAlignment = useAnswerAlignmentItem(handoff.session_id);
  const [draft, setDraft] = useState(handoff.answer_text ?? "");
  const busy = answerQa.isPending || correctAlignment.isPending || answerAlignment.isPending;

  const confirm = () => {
    if (!draft.trim()) {
      toast.error("回答を入力してください");
      return;
    }
    if (handoff.origin_kind === "qa") {
      answerQa.mutate(
        { qaId: handoff.origin_id, answer_text: draft.trim(), actor, handoff_id: handoff.id },
        {
          onSuccess: () => toast.success("回答を確定しました"),
          onError: e => toast.error(String(e)),
        },
      );
    } else {
      // Review Queue 項目には handoff_id を受け付ける専用パラメータが無い
      // ため、既存の /correct(自由文の修正)を通じて確定する — 引き継ぎの
      // 来歴自体は alignment_item.handoff_id に既に残っている。
      correctAlignment.mutate(
        { itemId: handoff.origin_id, corrected_interpretation: draft.trim() },
        {
          onSuccess: () => toast.success("回答を確定しました"),
          onError: e => toast.error(String(e)),
        },
      );
    }
  };

  return (
    <div className="rounded-md border border-amber-500/60 bg-amber-500/5 p-3 space-y-2 text-xs" data-testid={`handoff-item-${handoff.id}`}>
      <HandoffSummary handoff={handoff} />
      <Badge variant="warning" data-testid={`handoff-returned-marker-${handoff.id}`}>
        引き継ぎ先の回答(未確定)
      </Badge>
      <Textarea
        value={draft}
        onChange={e => setDraft(e.target.value)}
        rows={3}
        data-testid={`handoff-confirm-draft-${handoff.id}`}
      />
      <Button size="sm" onClick={confirm} disabled={busy} data-testid={`handoff-confirm-${handoff.id}`}>
        この内容で回答を確定する
      </Button>
    </div>
  );
}

function HandoffSummary({ handoff }: { handoff: QuestionHandoffOut }) {
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0 space-y-0.5">
        <p className="break-words"><span className="font-semibold">背景:</span> {handoff.background}</p>
        <p className="break-words"><span className="font-semibold">決めてほしいこと:</span> {handoff.needed_decision}</p>
        <p className="text-muted-foreground">
          担当者: {handoff.assignee} / 優先度: {PRIORITY_LABELS[handoff.priority] ?? handoff.priority}
          {handoff.due_note ? ` / 期限: ${handoff.due_note}` : ""}
        </p>
      </div>
      <Badge variant={statusBadgeVariant(handoff.status)} data-testid={`handoff-status-${handoff.id}`}>
        {STATUS_LABELS[handoff.status] ?? handoff.status}
      </Badge>
    </div>
  );
}

export function HandoffListPanel({ sessionId, actor }: { sessionId: number; actor: string }) {
  const { data } = useQuestionHandoffs(sessionId);
  const items = data?.items ?? [];

  if (items.length === 0) return null;

  const pending = items.filter(h => h.status === "pending");
  const answered = items.filter(h => h.status === "answered");
  const returned = items.filter(h => h.status === "returned");
  const cancelled = items.filter(h => h.status === "cancelled");

  return (
    <Card data-testid="handoff-list-panel">
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <LifeBuoy className="h-4 w-4" /> 担当者への引き継ぎ
        </CardTitle>
        <CardDescription>
          担当外の質問やレビュー項目を他の担当者へ引き継いだ一覧です。確認待ちの回答は明示的に確定してください。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {returned.length > 0 && (
          <div className="space-y-2" data-testid="handoff-group-returned">
            {returned.map(h => <ReturnedHandoffCard key={h.id} handoff={h} actor={actor} />)}
          </div>
        )}
        {answered.length > 0 && (
          <div className="space-y-2" data-testid="handoff-group-answered">
            {answered.map(h => <AnsweredHandoffCard key={h.id} handoff={h} />)}
          </div>
        )}
        {pending.length > 0 && (
          <div className="space-y-2" data-testid="handoff-group-pending">
            {pending.map(h => <PendingHandoffCard key={h.id} handoff={h} />)}
          </div>
        )}
        {cancelled.length > 0 && (
          <p className="text-xs text-muted-foreground" data-testid="handoff-cancelled-count">
            キャンセル済み: {cancelled.length} 件
          </p>
        )}
      </CardContent>
    </Card>
  );
}
