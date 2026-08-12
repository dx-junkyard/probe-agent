// Issue #284: Intent Brief(目標と成功条件)パネル。
//
// 「現在の理解」(understanding-overview.tsx)が実装事実の理解を表示するのに
// 対し、このパネルはユーザーの意図(goal / pain / success_criteria /
// priority / constraints / non_goals)だけを扱う。AI 提案(ai_proposed)は
// 「確認する」「修正する」「対象外」のいずれかをユーザーが明示的に押すまで
// 「確認済み」にはならない(decision_method は常に 'manual' が確定操作)。
//
// UI は一度にすべての未入力項目を尋めない: 現在値が1件もない最初のフィールド
// (goal → pain → success_criteria → priority → constraints → non_goals の順)
// だけを「次に確認したい項目」として1件だけ表示する。
//
// canonical enum の値そのものは変更せず、日本語ラベルへの変換はこのファイル
// 内の単一マッピングテーブルだけを通す。生の enum 文字列は画面に出さない。

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { InquiryPanel } from "@/components/system-understanding/inquiry-panel";
import { JointUnderstandingEntry } from "@/components/system-understanding/joint-understanding-entry";
import {
  useActiveInquiriesByOrigin,
  useConfirmInterviewIntentItem,
  useCorrectInterviewIntentItem,
  useCreateInterviewIntentItem,
  useDeclineInterviewIntentItem,
  useInterviewIntentList,
  useResumeInterviewInquiry,
} from "@/api/hooks";
import {
  INTERVIEW_INTENT_FIELDS,
  type InterviewInquiryOut,
  type InterviewIntentField,
  type InterviewIntentItemOut,
} from "@/api/types";

const FIELD_LABELS: Record<InterviewIntentField, string> = {
  goal: "目標",
  pain: "課題・困りごと",
  success_criteria: "成功条件",
  priority: "優先度",
  constraints: "制約",
  non_goals: "やらないこと(非目標)",
};

function fieldLabel(field: string): string {
  return FIELD_LABELS[field as InterviewIntentField] ?? field;
}

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

const STATUS_LABELS: Record<string, string> = {
  proposed: "AI 提案(未確認)",
  confirmed: "確認済み",
  needs_review: "要確認",
  undecided: "未定",
  not_applicable: "対象外",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? "要確認";
}

function statusBadgeVariant(status: string): BadgeVariant {
  if (status === "confirmed") return "success";
  if (status === "proposed") return "warning";
  if (status === "not_applicable") return "outline";
  if (status === "undecided") return "secondary";
  return "destructive"; // needs_review / 未知の値は安全側(要確認)
}

function IntentItemRow({
  item, sessionId, existingInquiry,
}: {
  item: InterviewIntentItemOut;
  sessionId: number;
  // Issue #285 refresh/resume: an already-active (open/held) Inquiry for
  // this item, rediscovered via the list endpoint so a page reload never
  // forgets an in-progress Inquiry.
  existingInquiry?: InterviewInquiryOut;
}) {
  const confirm = useConfirmInterviewIntentItem(sessionId);
  const correct = useCorrectInterviewIntentItem(sessionId);
  const decline = useDeclineInterviewIntentItem(sessionId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.value_text);
  const [inquiryMode, setInquiryMode] = useState(false);
  const [hasHeldInquiry, setHasHeldInquiry] = useState(false);
  const [attachedInquiryId, setAttachedInquiryId] = useState<number | null>(null);
  const resumeInquiry = useResumeInterviewInquiry(sessionId);

  const isProposed = item.origin === "ai_proposed" && item.status === "proposed";
  const busy = confirm.isPending || correct.isPending || decline.isPending;

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

  const handleConfirm = () => {
    confirm.mutate({ itemId: item.id }, {
      onSuccess: () => toast.success("確認しました"),
      onError: e => toast.error(String(e)),
    });
  };

  const handleCorrectSubmit = () => {
    if (!draft.trim()) {
      toast.error("内容を入力してください");
      return;
    }
    correct.mutate({ itemId: item.id, value_text: draft }, {
      onSuccess: () => {
        setEditing(false);
        toast.success("修正しました");
      },
      onError: e => toast.error(String(e)),
    });
  };

  const handleDecline = () => {
    decline.mutate({ itemId: item.id }, {
      onSuccess: () => toast.success("対象外にしました"),
      onError: e => toast.error(String(e)),
    });
  };

  return (
    <div className="rounded-md border p-3 space-y-2" data-testid={`intent-item-${item.id}`}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm break-words min-w-0">{item.value_text}</p>
        <Badge
          variant={statusBadgeVariant(item.status)}
          className="shrink-0"
          data-testid={`intent-item-status-${item.id}`}
        >
          {statusLabel(item.status)}
        </Badge>
      </div>
      {item.source_statement && (
        <p className="text-[11px] text-muted-foreground">
          根拠となる発言: 「{item.source_statement}」
        </p>
      )}
      {inquiryMode ? (
        <InquiryPanel
          key={attachedInquiryId ?? "new"}
          sessionId={sessionId}
          originKind="intent"
          originId={item.id}
          heldDraft={draft || null}
          existingInquiryId={attachedInquiryId ?? undefined}
          onResolved={heldDraft => {
            setDraft(heldDraft ?? item.value_text);
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
            <div className="flex items-center gap-2" data-testid={`intent-item-held-inquiry-marker-${item.id}`}>
              <p className="text-xs text-amber-700">保留中の疑問があります</p>
              <Button
                size="sm"
                variant="outline"
                onClick={handleResumeInquiry}
                disabled={resumeInquiry.isPending}
                data-testid={`intent-item-inquiry-resume-${item.id}`}
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
                rows={2}
                data-testid={`intent-item-correct-input-${item.id}`}
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={handleCorrectSubmit} disabled={busy}>確定</Button>
                <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={busy}>
                  キャンセル
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {isProposed && (
                <>
                  <Button
                    size="sm"
                    onClick={handleConfirm}
                    disabled={busy}
                    data-testid={`intent-item-confirm-${item.id}`}
                  >
                    確認する
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => { setDraft(item.value_text); setEditing(true); }}
                    disabled={busy}
                    data-testid={`intent-item-edit-${item.id}`}
                  >
                    修正する
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={handleDecline}
                    disabled={busy}
                    data-testid={`intent-item-decline-${item.id}`}
                  >
                    対象外
                  </Button>
                  {/* Issue #336: the same 共同理解 flow the Q&A card uses.
                      Opening it never confirms, corrects, or declines the
                      item — those stay the three buttons above. */}
                  <JointUnderstandingEntry
                    sessionId={sessionId}
                    originKind="intent"
                    originId={item.id}
                    questionText={`この意図をどう決めるべきか分かりません: ${item.value_text}`}
                  />
                </>
              )}
              {reopenableInquiryId ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={openExistingInquiry}
                  disabled={busy}
                  data-testid={`intent-item-inquiry-reopen-${item.id}`}
                >
                  疑問を再開する
                </Button>
              ) : (
                !heldInquiryId && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => { setAttachedInquiryId(null); setInquiryMode(true); }}
                    disabled={busy}
                    data-testid={`intent-item-inquiry-open-${item.id}`}
                  >
                    疑問がある
                  </Button>
                )
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function NextMissingFieldPrompt({
  field, sessionId,
}: { field: InterviewIntentField; sessionId: number }) {
  const create = useCreateInterviewIntentItem(sessionId);
  const [value, setValue] = useState("");

  const submit = (
    text: string,
    status: "confirmed" | "undecided" | "not_applicable",
  ) => {
    if (!text.trim()) {
      toast.error("内容を入力してください");
      return;
    }
    create.mutate({ field, value_text: text, status }, {
      onSuccess: () => {
        setValue("");
        toast.success("記録しました");
      },
      onError: e => toast.error(String(e)),
    });
  };

  return (
    <div className="rounded-md border border-dashed p-3 space-y-2" data-testid="intent-next-missing-prompt">
      <p className="text-xs text-muted-foreground">
        次に確認したい項目: <span className="font-medium">{fieldLabel(field)}</span>
      </p>
      <Textarea
        value={value}
        onChange={e => setValue(e.target.value)}
        placeholder={`${fieldLabel(field)}を入力してください`}
        rows={2}
        data-testid="intent-next-missing-input"
      />
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={() => submit(value, "confirmed")}
          disabled={create.isPending}
          data-testid="intent-next-missing-submit"
        >
          この内容で確定
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => submit("未定", "undecided")}
          disabled={create.isPending}
          data-testid="intent-next-missing-undecided"
        >
          未定
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => submit("対象外", "not_applicable")}
          disabled={create.isPending}
          data-testid="intent-next-missing-not-applicable"
        >
          対象外
        </Button>
      </div>
    </div>
  );
}

export function IntentBriefPanel({ sessionId }: { sessionId: number }) {
  const { data: intentList } = useInterviewIntentList(sessionId);
  // Issue #285 refresh/resume: re-attach any still-active Inquiry to its
  // origin item after a reload.
  const activeInquiries = useActiveInquiriesByOrigin(sessionId);


  if (!intentList) return null;

  // 一度にすべての未入力項目を尋めない: 現在値が1件もない最初のフィールドだけを
  // 「次に確認したい項目」として提示する(固定順、Principle 6)。
  const nextMissingField = INTERVIEW_INTENT_FIELDS.find(
    f => (intentList.items_by_field[f] ?? []).length === 0,
  );

  return (
    <Card data-testid="intent-brief-panel">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm">Intent Brief(目標と成功条件)</CardTitle>
            <CardDescription>
              ユーザーの意図は本人だけが決められます。AI 提案は確認するまで確定しません。
            </CardDescription>
          </div>
          {/* Issue #349 (#52 / `OP-S4`): Intent 候補の生成は自動なので、
              通常フローの操作としては置かない。確定・訂正は常に人間の
              明示操作のまま (#284) で、この下のカードにある。 */}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {INTERVIEW_INTENT_FIELDS.map(field => {
          const items = intentList.items_by_field[field] ?? [];
          if (items.length === 0) return null;
          return (
            <div key={field} className="space-y-2">
              <h4 className="text-xs font-semibold uppercase text-muted-foreground">
                {fieldLabel(field)}
              </h4>
              <div className="space-y-2">
                {items.map(item => (
                  <IntentItemRow
                    key={item.id}
                    item={item}
                    sessionId={sessionId}
                    existingInquiry={activeInquiries.get(`intent:${item.id}`)}
                  />
                ))}
              </div>
            </div>
          );
        })}

        {nextMissingField && (
          <NextMissingFieldPrompt field={nextMissingField} sessionId={sessionId} />
        )}
      </CardContent>
    </Card>
  );
}
