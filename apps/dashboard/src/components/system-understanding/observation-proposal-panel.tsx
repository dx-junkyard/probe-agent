// Issue #290: 新規 runtime 観測の承認ゲート付き提案。
//
// 提案の作成自体は Inquiry 側(inquiry-panel.tsx の
// SuggestedObservationProposalCard)からも行えるが、このパネルは
// セッション全体の提案一覧を表示し、承認 / 却下を行う場所。
// 承認しても観測は自動的には開始されない — レスポンスの
// `policy_pointer`(サーバー側の固定日本語文言)を、既存のポリシー設定
// 画面への案内としてそのまま表示するだけ。

import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  useApproveObservationProposal,
  useObservationProposals,
  useRejectObservationProposal,
} from "@/api/hooks";
import type { RuntimeObservationProposalOut, RuntimeObservationProposalStatus } from "@/api/types";

const STATUS_LABELS: Record<RuntimeObservationProposalStatus, string> = {
  proposed: "未決定",
  approved: "承認済み",
  rejected: "却下",
  expired: "期限切れ",
};

function statusVariant(status: RuntimeObservationProposalStatus) {
  if (status === "approved") return "success" as const;
  if (status === "rejected" || status === "expired") return "secondary" as const;
  return "outline" as const;
}

function ObservationProposalCard({ proposal, sessionId }: { proposal: RuntimeObservationProposalOut; sessionId: number }) {
  const approve = useApproveObservationProposal(sessionId);
  const reject = useRejectObservationProposal(sessionId);
  const busy = approve.isPending || reject.isPending;
  const decidable = proposal.status === "proposed";

  return (
    <div
      className="rounded-md border p-3 space-y-2"
      data-testid={`observation-proposal-${proposal.id}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-semibold font-mono">{proposal.target_component}</p>
        <Badge variant={statusVariant(proposal.status)} data-testid={`observation-proposal-status-${proposal.id}`}>
          {STATUS_LABELS[proposal.status]}
        </Badge>
      </div>
      <div className="space-y-1 text-xs">
        <p><span className="font-semibold">目的:</span> {proposal.purpose}</p>
        {proposal.expected_cost && <p><span className="font-semibold">想定コスト:</span> {proposal.expected_cost}</p>}
        {proposal.risk_note && <p><span className="font-semibold">リスク:</span> {proposal.risk_note}</p>}
        {proposal.retention_note && <p><span className="font-semibold">保持期間:</span> {proposal.retention_note}</p>}
      </div>

      {decidable ? (
        <div className="flex gap-2">
          <Button
            size="sm"
            disabled={busy}
            onClick={() => approve.mutate(
              { proposalId: proposal.id },
              { onSuccess: () => toast.success("承認しました"), onError: e => toast.error(String(e)) },
            )}
            data-testid={`observation-proposal-approve-${proposal.id}`}
          >
            承認する
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => reject.mutate(
              { proposalId: proposal.id },
              { onSuccess: () => toast.info("却下しました"), onError: e => toast.error(String(e)) },
            )}
            data-testid={`observation-proposal-reject-${proposal.id}`}
          >
            却下する
          </Button>
        </div>
      ) : proposal.status === "approved" && proposal.policy_pointer ? (
        <p
          className="rounded border border-sky-500/40 bg-sky-500/5 p-2 text-[11px] text-sky-800"
          data-testid={`observation-proposal-policy-hint-${proposal.id}`}
        >
          {proposal.policy_pointer}
        </p>
      ) : null}
    </div>
  );
}

export function ObservationProposalPanel({ sessionId }: { sessionId: number }) {
  const { data: proposals } = useObservationProposals(sessionId);
  const [showDecided, setShowDecided] = useState(false);

  if (!proposals || proposals.length === 0) return null;

  const pending = proposals.filter(p => p.status === "proposed");
  const decided = proposals.filter(p => p.status !== "proposed");

  return (
    <Card data-testid="observation-proposal-panel">
      <CardHeader>
        <CardTitle className="text-sm">新規観測の提案</CardTitle>
        <CardDescription>
          承認しても観測は自動的に開始されません。承認後は既存のポリシー設定から手動で開始してください。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {pending.length === 0 && (
          <p className="text-xs text-muted-foreground" data-testid="observation-proposal-empty">
            未決定の提案はありません。
          </p>
        )}
        {pending.map(p => <ObservationProposalCard key={p.id} proposal={p} sessionId={sessionId} />)}

        {decided.length > 0 && (
          <div className="pt-2 border-t">
            <button
              type="button"
              className="text-xs text-muted-foreground underline underline-offset-2"
              onClick={() => setShowDecided(s => !s)}
              aria-expanded={showDecided}
              data-testid="observation-proposal-decided-toggle"
            >
              {showDecided ? "決定済みの提案を隠す" : `決定済みの提案 (${decided.length})`}
            </button>
            {showDecided && (
              <div className="mt-2 space-y-2" data-testid="observation-proposal-decided-list">
                {decided.map(p => <ObservationProposalCard key={p.id} proposal={p} sessionId={sessionId} />)}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
