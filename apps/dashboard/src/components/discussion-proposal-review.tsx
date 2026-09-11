// Proposal review UI (Issue #452, Epic #443 §3).
//
// docs/01-specifications/capabilities/ai-discussion-adapter.md §3 is the canonical contract;
// docs/01-specifications/ux/decision-discussion-workflow.md's 提案レビュー段階 (§3) is the UX contract this
// implements. Renders inside `components/assistant-panel.tsx` for a
// discussion thread whose target can propose field/relation changes
// (`propose_fields`/`propose_relations`, derived by the server registry --
// this component never re-derives that itself, it only reads `adapter.forms`
// to decide which action set to lead with).
//
// Two action paths, per §3.1's Decision ("標準はprefill-first、direct apply
// APIは互換維持"):
//   - `POST .../prefill` (prefill-first, the standard route): reflects
//     selected items into the destination Dashboard form as an unsaved
//     draft, so the developer edits and saves it themselves through the
//     EXISTING domain save endpoint. This component owns the delivery
//     pipeline's ORDER (navigate -> form ready confirm -> patch delivery ->
//     ack -> prefill audit, §3's Decisions) -- the individual steps are
//     `lib/form-draft-inbox.ts`'s `waitForFormDraftReady` /
//     `dispatchFormDraftPatchAndWaitForAck`, and the final audit call is the
//     existing `usePrefillDiscussionProposalItems` hook.
//   - `POST .../apply` (kept for compatibility, never the lead action): the
//     pre-#446 direct-apply path, still available for a target with no
//     prefill handler.
//
// Ack-before-audit is the load-bearing rule (DD-INT-01: "配送失敗を成功扱い
// しない"): the server's `/prefill` audit call is issued ONLY after the
// destination form has acked the patch. A timed-out delivery reports
// failure and records NOTHING server-side -- a retry (same button) can
// deliver again from scratch.

import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  useApplyDiscussionProposalItems, useCreateDiscussionProposal, useDiscussionProposal,
  useDiscussionProposals, usePrefillDiscussionProposalItems, usePromoteDiscussionHypothesis,
  useRejectDiscussionProposalItems,
} from "@/api/hooks";
import type {
  AssistantDiscussionProposalHypothesis, AssistantDiscussionProposalItem, AssistantDiscussionThread,
  DiscussionProposalItemEligibility, DiscussionProposalItemStatus,
} from "@/api/types";
import {
  classifyDiscussionError, DISCUSSION_ADAPTERS, proposalToDraft, type DashboardDiscussionAdapter,
} from "@/lib/discussion-adapters";
import { dispatchFormDraftPatchAndWaitForAck, waitForFormDraftReady } from "@/lib/form-draft-inbox";
import { useUiDraftRegistry } from "@/lib/ui-draft";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const ELIGIBILITY_LABEL: Record<DiscussionProposalItemEligibility, string> = {
  appliable: "反映可能",
  forbidden: "対応不可",
  stale: "前提が古い",
  conflict: "競合あり",
};

const STATUS_LABEL: Record<DiscussionProposalItemStatus, string> = {
  proposed: "未対応",
  applied: "適用済み",
  rejected: "却下済み",
};

// Issue #455 (Epic #443 §6.1): a hypothesis is an INDEPENDENT proposal item
// type -- never a field/relation/child change, never applied through
// `apply`/`prefill`. Its only operation is promotion into Joint
// Understanding (§6.2).
const HYPOTHESIS_STATUS_LABEL: Record<AssistantDiscussionProposalHypothesis["status"], string> = {
  proposed: "未対応",
  promoted: "調査へ昇格済み",
  rejected: "却下済み",
};

function HypothesisRow({
  hypothesis, onPromote, promoting, canPromote,
}: {
  hypothesis: AssistantDiscussionProposalHypothesis;
  onPromote: () => void;
  promoting: boolean;
  canPromote: boolean;
}) {
  return (
    <li
      className="space-y-1 rounded border border-dashed p-2"
      data-testid={`discussion-hypothesis-${hypothesis.id}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-medium">{hypothesis.statement}</p>
        <Badge variant="outline">{HYPOTHESIS_STATUS_LABEL[hypothesis.status]}</Badge>
      </div>
      <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
        <li>競合する説明: {hypothesis.competing_explanations.join("、") || "(なし)"}</li>
        <li>反証条件: {hypothesis.refutation_conditions.join("、") || "(なし)"}</li>
        <li>次の調査: {hypothesis.next_investigation || "(なし)"}</li>
        {hypothesis.uncertainty && <li>不確実性: {hypothesis.uncertainty}</li>}
      </ul>
      <Button
        size="sm"
        variant="outline"
        disabled={!canPromote || promoting}
        onClick={onPromote}
        data-testid={`discussion-hypothesis-promote-${hypothesis.id}`}
      >
        {promoting ? "共同理解へ昇格中…" : "共同理解(Joint Understanding)へ昇格する"}
      </Button>
      <p className="text-[11px] text-muted-foreground">
        昇格は調査・Replay・Experimentを自動で開始しません。別の操作として承認・実行してください。
      </p>
    </li>
  );
}

type DeliveryState =
  | "idle" | "navigating" | "waiting_for_form" | "delivering" | "recording" | "done" | "failed";

const DELIVERY_BUSY: ReadonlySet<DeliveryState> = new Set([
  "navigating", "waiting_for_form", "delivering", "recording",
]);

// Issue #454 (Epic #443 §5.1): a child item's intent, in the same finite
// Japanese vocabulary a save/apply action already uses elsewhere on this
// screen -- never a raw `add`/`update`/`remove` identifier next to Japanese
// prose (UI言語規約).
const CHILD_INTENT_LABEL: Record<string, string> = {
  add: "追加", update: "更新", remove: "削除",
};

function itemLabel(item: AssistantDiscussionProposalItem): string {
  if (item.item_kind === "field") {
    if (item.child_kind) {
      // §5.1: a pure reorder (no field_name) and a content change on the
      // SAME child are separate items -- the label makes that visible
      // rather than showing them identically.
      const intent = CHILD_INTENT_LABEL[item.child_intent] ?? item.child_intent;
      const keyLabel = item.child_key || "新規";
      if (!item.field_name && item.child_order !== null) {
        return `${item.child_kind}[${keyLabel}] 順序変更 → ${item.child_order}`;
      }
      return `${item.child_kind}[${keyLabel}].${item.field_name} (${intent})`;
    }
    return item.field_name;
  }
  return `${item.relation_kind} → ${item.relation_target_kind}:${item.relation_target_ref}`;
}

function ProposalItemRow({
  item, selected, selectable, onToggle,
}: {
  item: AssistantDiscussionProposalItem;
  selected: boolean;
  selectable: boolean;
  onToggle: () => void;
}) {
  return (
    <li
      className="space-y-1 rounded border p-2 text-xs"
      data-testid={`discussion-proposal-item-row-${item.id}`}
    >
      <div className="flex items-start gap-2">
        {selectable && (
          <input
            type="checkbox"
            className="mt-0.5"
            checked={selected}
            onChange={onToggle}
            aria-label={`「${itemLabel(item)}」を選択`}
            data-testid={`discussion-proposal-item-select-${item.id}`}
          />
        )}
        <div className="flex-1 space-y-1">
          <p className="font-mono font-medium">{itemLabel(item)}</p>
          <p className="text-muted-foreground">
            現在値: <span className="text-foreground">{item.current_value || "(空)"}</span>
          </p>
          <p className="text-muted-foreground">
            提案: <span className="text-foreground">{item.proposed_value || "(空)"}</span>
          </p>
          {item.rationale && <p className="text-muted-foreground">理由: {item.rationale}</p>}
          <div className="flex flex-wrap gap-1 pt-0.5">
            <Badge
              variant={item.eligibility === "appliable" ? "outline" : "secondary"}
              className="text-[10px]"
              data-testid={`discussion-proposal-item-eligibility-${item.id}`}
            >
              {ELIGIBILITY_LABEL[item.eligibility]}
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              {STATUS_LABEL[item.status]}
            </Badge>
            {item.prefill_count > 0 && (
              <Badge variant="outline" className="text-[10px]" data-testid={`discussion-proposal-item-prefill-count-${item.id}`}>
                反映済み {item.prefill_count} 回
              </Badge>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

function ProposalDetail({
  proposalId, adapter, thread,
}: {
  proposalId: number;
  adapter: DashboardDiscussionAdapter;
  thread: AssistantDiscussionThread;
}) {
  const detailQuery = useDiscussionProposal(proposalId);
  const rejectMutation = useRejectDiscussionProposalItems(proposalId);
  const applyMutation = useApplyDiscussionProposalItems(proposalId);
  const prefillMutation = usePrefillDiscussionProposalItems(proposalId);
  const promoteMutation = usePromoteDiscussionHypothesis(proposalId);
  const uiDraftRegistry = useUiDraftRegistry();
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [deliveryState, setDeliveryState] = useState<DeliveryState>("idle");
  const [deliveryMessage, setDeliveryMessage] = useState("");
  // Issue #455 §6.2: a fresh idempotency key PER promote attempt (retrying
  // the SAME attempt after a failure reuses it -- a NEW hypothesis or a
  // deliberate second investigation mints a new one). Keyed by hypothesis
  // id so several hypotheses in one proposal never share a key.
  const [promotionRequestIds, setPromotionRequestIds] = useState<Record<number, string>>({});
  const [promotingId, setPromotingId] = useState<number | null>(null);
  const [promotionMessage, setPromotionMessage] = useState("");

  const proposal = detailQuery.data;
  // §1.3's derivation, read here as a display/routing choice ONLY (which
  // action set to lead with) -- never re-decided as a capability the server
  // did not actually grant; the real gate is still the server's own 422s on
  // the prefill call itself.
  const canPrefill = adapter.prefillHandlerId !== null && adapter.forms.length > 0;

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const selectedAppliable = proposal
    ? [...selected].filter((id) => proposal.items.find((i) => i.id === id)?.eligibility === "appliable")
    : [];

  async function dispatchPrefill() {
    if (!proposal || selectedAppliable.length === 0) return;
    const draft = proposalToDraft(adapter, proposal, selectedAppliable);
    if (!draft.patch || !draft.formId) {
      setDeliveryState("failed");
      setDeliveryMessage("この対象はフォームへの反映に対応していません。");
      return;
    }
    if (draft.unregisteredFieldNames.length > 0) {
      setDeliveryState("failed");
      setDeliveryMessage(`次の項目はこのフォームでは扱えません: ${draft.unregisteredFieldNames.join("、")}`);
      return;
    }
    setDeliveryMessage("");
    // Step 1: navigate. Never re-navigate if already on the target screen.
    setDeliveryState("navigating");
    const deepLink = adapter.deepLink(thread.target_ref);
    if (deepLink && `${location.pathname}${location.search}` !== deepLink) {
      navigate(deepLink);
    }
    // Step 2: form ready confirm.
    setDeliveryState("waiting_for_form");
    const ready = await waitForFormDraftReady(uiDraftRegistry, draft.formId, thread.target_ref);
    if (!ready) {
      setDeliveryState("failed");
      setDeliveryMessage("反映先のフォームが開けませんでした。対象の画面を開いてから、もう一度お試しください。");
      return;
    }
    // Step 3+4: patch delivery, then ack.
    setDeliveryState("delivering");
    const ack = await dispatchFormDraftPatchAndWaitForAck(draft.patch);
    if (ack !== "acked") {
      // DD-INT-01: a failed/unconfirmed delivery is NEVER reported as
      // success, and the server prefill-audit call below is skipped
      // entirely -- nothing is recorded for an attempt nobody can confirm
      // reached the form.
      setDeliveryState("failed");
      setDeliveryMessage("フォームへの反映が確認できませんでした。もう一度お試しください。");
      return;
    }
    // Step 5: prefill audit -- only now, after a confirmed ack.
    setDeliveryState("recording");
    try {
      await prefillMutation.mutateAsync({
        item_ids: selectedAppliable, form_id: draft.formId, patch_token: draft.patch.patchToken,
      });
      for (const key of adapter.invalidateKeys(thread.target_ref)) {
        qc.invalidateQueries({ queryKey: key as unknown[] });
      }
      setDeliveryState("done");
      setDeliveryMessage("フォームへ反映しました。内容を確認して保存してください(この時点では何も保存されていません)。");
    } catch (err) {
      setDeliveryState("failed");
      setDeliveryMessage(classifyDiscussionError(err).message);
    }
  }

  function promoteHypothesis(hypothesisId: number) {
    setPromotionMessage("");
    setPromotingId(hypothesisId);
    const requestId = promotionRequestIds[hypothesisId] ?? crypto.randomUUID();
    setPromotionRequestIds((prev) => ({ ...prev, [hypothesisId]: requestId }));
    promoteMutation.mutate(
      { hypothesis_id: hypothesisId, request_id: requestId },
      {
        onSuccess: (result) => {
          setPromotingId(null);
          setPromotionMessage(
            result.reused
              ? `既存の共同理解セッション(#${result.joint_understanding_session_id})に接続しました。`
              : `共同理解セッション(#${result.joint_understanding_session_id})を開始しました。調査は別途実行してください。`,
          );
        },
        onError: (err) => {
          setPromotingId(null);
          setPromotionMessage(classifyDiscussionError(err).message);
        },
      },
    );
  }

  if (detailQuery.isLoading) {
    return <p className="text-xs text-muted-foreground">読み込み中…</p>;
  }
  if (detailQuery.isError || !proposal) {
    return (
      <div className="space-y-1" data-testid="discussion-proposal-detail-error">
        <p className="text-xs text-destructive">提案を読み込めませんでした。</p>
        <button
          type="button"
          className="text-xs text-primary hover:underline"
          onClick={() => detailQuery.refetch()}
        >
          再試行
        </button>
      </div>
    );
  }

  const busy = DELIVERY_BUSY.has(deliveryState);

  return (
    <div className="space-y-2 rounded border p-2" data-testid={`discussion-proposal-detail-${proposal.id}`}>
      {proposal.summary && <p className="text-xs">{proposal.summary}</p>}
      {proposal.evidence_refs.length > 0 && (
        <div data-testid="discussion-proposal-evidence">
          <p className="text-[11px] font-medium text-muted-foreground">根拠</p>
          <ul className="list-disc space-y-0.5 pl-4 text-[11px]">
            {proposal.evidence_refs.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {proposal.items.length === 0 ? (
        <p className="text-xs text-muted-foreground">この提案には変更候補がありません。</p>
      ) : (
        <ul className="space-y-2">
          {proposal.items.map((item) => (
            <ProposalItemRow
              key={item.id}
              item={item}
              selected={selected.has(item.id)}
              selectable={item.status === "proposed" && item.eligibility === "appliable"}
              onToggle={() => toggle(item.id)}
            />
          ))}
        </ul>
      )}
      {proposal.hypotheses.length > 0 && (
        <div className="space-y-2" data-testid="discussion-proposal-hypotheses">
          <p className="text-[11px] font-medium text-muted-foreground">未解決の仮説</p>
          <ul className="space-y-2">
            {proposal.hypotheses.map((hypothesis) => (
              <HypothesisRow
                key={hypothesis.id}
                hypothesis={hypothesis}
                canPromote={hypothesis.status === "proposed" || hypothesis.status === "promoted"}
                promoting={promotingId === hypothesis.id}
                onPromote={() => promoteHypothesis(hypothesis.id)}
              />
            ))}
          </ul>
          {promotionMessage && (
            <p className="text-[11px] text-muted-foreground" data-testid="discussion-hypothesis-promotion-status">
              {promotionMessage}
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={selectedAppliable.length === 0 || !canPrefill || busy}
          onClick={dispatchPrefill}
          data-testid="discussion-proposal-prefill"
        >
          {busy ? "反映中…" : "選択した項目をフォームへ反映する"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={selectedAppliable.length === 0 || applyMutation.isPending}
          onClick={() =>
            applyMutation.mutate(
              { item_ids: selectedAppliable },
              { onSuccess: () => setSelected(new Set()) },
            )
          }
          data-testid="discussion-proposal-apply"
        >
          {applyMutation.isPending ? "適用中…" : "直接適用する(互換)"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={selected.size === 0 || rejectMutation.isPending}
          onClick={() =>
            rejectMutation.mutate(
              { item_ids: [...selected] },
              { onSuccess: () => setSelected(new Set()) },
            )
          }
          data-testid="discussion-proposal-reject"
        >
          {rejectMutation.isPending ? "却下中…" : "却下する"}
        </Button>
      </div>
      {!canPrefill && (
        <p className="text-[11px] text-muted-foreground" data-testid="discussion-proposal-prefill-unsupported">
          この対象はフォームへの反映に対応していません。「直接適用する」を使うか、対象の画面から直接編集してください。
        </p>
      )}
      {applyMutation.isError && (
        <p className="text-xs text-destructive" data-testid="discussion-proposal-apply-error">
          {classifyDiscussionError(applyMutation.error).message}
        </p>
      )}
      {deliveryMessage && (
        <p
          role="status"
          className={deliveryState === "failed" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
          data-testid="discussion-proposal-delivery-status"
        >
          {deliveryMessage}
        </p>
      )}
    </div>
  );
}

export function DiscussionProposalReview({ thread }: { thread: AssistantDiscussionThread }) {
  const adapter = DISCUSSION_ADAPTERS[thread.target_kind];
  const proposalsQuery = useDiscussionProposals(thread.id);
  const createProposal = useCreateDiscussionProposal(thread.id);
  // Three states, not two. `undefined` = the developer has not chosen yet
  // (derive the newest); `null` = they explicitly collapsed the open one
  // (respect it -- deriving "newest" here would re-open what they just
  // closed); a number = their choice, honoured only while it still exists.
  const [chosenId, setChosenId] = useState<number | null | undefined>(undefined);
  const proposals = proposalsQuery.data?.proposals ?? [];

  // A fresh generate opens straight into review; a reload restores the
  // newest proposal expanded too (never auto-picks a stale prior selection
  // that no longer exists in the list). Derived during render rather than
  // written back from an effect: a synchronous `setState` inside an effect
  // costs a cascading render and shows one frame of the wrong row, and the
  // value is a pure function of `chosenId` + the current list anyway.
  const expandedId =
    chosenId !== undefined && (chosenId === null || proposals.some((p) => p.id === chosenId))
      ? chosenId
      : (proposals[0]?.id ?? null);

  // Only a target with something proposable renders this section at all --
  // a `screen`-scope conversation has no target to change, and this mirrors
  // the same `adapter.forms.length > 0` / capability-shaped checks the panel
  // already uses elsewhere (e.g. `focusTargetSupportsUiDraft`).
  if (adapter.scope === "screen") return null;

  return (
    <div
      className="max-h-64 shrink-0 space-y-2 overflow-y-auto border-t p-3"
      data-testid="discussion-proposal-review"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">変更候補</p>
        <Button
          size="sm"
          variant="outline"
          disabled={createProposal.isPending}
          onClick={() => createProposal.mutate(undefined, { onSuccess: (p) => setChosenId(p.id) })}
          data-testid="discussion-proposal-generate"
        >
          {createProposal.isPending ? "生成中…" : "変更候補を生成する"}
        </Button>
      </div>
      {createProposal.isError && (
        <p className="text-xs text-destructive" data-testid="discussion-proposal-generate-error">
          {classifyDiscussionError(createProposal.error).message}
        </p>
      )}
      {proposalsQuery.isLoading ? (
        <p className="text-xs text-muted-foreground">読み込み中…</p>
      ) : proposals.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="discussion-proposal-empty">
          まだ変更候補はありません。
        </p>
      ) : (
        <ul className="space-y-1">
          {proposals.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className="w-full rounded border p-2 text-left text-xs hover:bg-muted"
                onClick={() => setChosenId(expandedId === p.id ? null : p.id)}
                data-testid={`discussion-proposal-summary-${p.id}`}
                aria-expanded={expandedId === p.id}
              >
                {p.summary || `提案 #${p.id}`}
              </button>
              {expandedId === p.id && (
                <div className="pt-1">
                  <ProposalDetail proposalId={p.id} adapter={adapter} thread={thread} />
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
