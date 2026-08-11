// Issue #336: the shared entry point into 共同理解 from any confirmation item.
//
// Before this, the only way in was the Q&A card. The other three origins
// (Intent / Review item / Inquiry) had a working server contract and no way for
// a developer to reach it, which is the same "isolated third feature" shape
// Epic #328 set out to remove — just from the UI side.
//
// One component for all of them, deliberately: 起点固有の表示や復帰先は持てるが、
// 調査・翻訳・監査の実装系統は共通化する。The label differs per origin; the
// session lookup, the creation call, and the panel are identical.
//
// It never writes the origin item. Opening a conversation about an item is not
// answering it, and closing the conversation is not deciding it.

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  useCreateJointUnderstanding,
  useJointUnderstandingList,
} from "@/api/hooks";
import type { JointUnderstandingOriginKind } from "@/api/types";
import { JointUnderstandingPanel } from "./joint-understanding-panel";

export function JointUnderstandingEntry({
  sessionId,
  originKind,
  originId,
  questionText,
  label = "一緒に確かめる",
}: {
  sessionId: number;
  originKind: JointUnderstandingOriginKind;
  originId: number;
  // What the developer wants to understand. Stored on the session only — never
  // copied into the origin item's answer/value/decision field.
  questionText: string;
  label?: string;
}) {
  const list = useJointUnderstandingList(sessionId);
  const create = useCreateJointUnderstanding(sessionId);
  const [openId, setOpenId] = useState<number | null>(null);

  // Match on `current_origin_id` too: `interview_qa` and
  // `interview_intent_item` correct additively, so an edit gives the item a NEW
  // row id while the session still points at the row the conversation started
  // from (Issue #336).
  const active = (list.data?.items ?? []).find(
    item =>
      item.origin_kind === originKind
      && item.status !== "closed"
      && (item.origin_id === originId || item.current_origin_id === originId),
  );
  const panelId = openId ?? active?.id ?? null;

  const start = () => {
    if (active) {
      setOpenId(active.id);
      return;
    }
    create.mutate(
      {
        origin_kind: originKind,
        origin_id: originId,
        // Only the 「わからない」 entry point may record 'unknown_answer'
        // (Issue #336): pressing a button is an explicit request, and claiming
        // otherwise would make the audit distinction meaningless.
        trigger: "explicit_request",
        question_text: questionText,
      },
      {
        onSuccess: result => setOpenId(result.session.id),
        onError: error =>
          toast.error(
            error instanceof Error ? error.message : "対話を開始できませんでした",
          ),
      },
    );
  };

  if (panelId) {
    return (
      <JointUnderstandingPanel
        sessionId={sessionId}
        juId={panelId}
        onClosed={() => setOpenId(null)}
      />
    );
  }
  return (
    <Button
      size="sm"
      variant="outline"
      onClick={start}
      disabled={create.isPending}
      data-testid={`ju-start-${originKind}-${originId}`}
    >
      {label}
    </Button>
  );
}
