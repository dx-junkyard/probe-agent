// Epic #412 / #415: Flow 実験提案 — reading a plan and deciding on it.
//
// `status` is DERIVED by folding `flow_experiment_event` on the server (§7.4).
// This panel renders that value; it never folds events, and it holds no copy
// of the transition table — a refusal is shown as the server's finite code
// verbatim.
//
// Approval permits nothing on its own. Recording an execution additionally
// requires every target Node's effective mode to permit `candidate_execution`
// (§7.5), and this screen states that rather than implying that an approved
// proposal will run.
//
// Recording a promotion candidate is NOT a promotion (§7.6): the real
// promotion still goes through the existing Experiment adoption /
// Stabilization / publish human gates.

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTimestamp } from "@/lib/utils";
import type { FlowExperimentProposalOut } from "@/api/types";
import {
  COMPARISON_SCOPE_LABEL,
  ISOLATION_STRATEGY_LABEL,
  PROPOSAL_EVENT_LABEL,
  PROPOSAL_STATUS_DESCRIPTION,
  PROPOSAL_STATUS_LABEL,
  PROPOSAL_STATUS_TONE,
  evaluationLevelGroups,
  proposalActions,
  proposalRequirements,
  sortProposals,
  type ProposalAction,
} from "./model";
import {
  ConfirmActionDialog,
  EmptyNote,
  ReadFailure,
  RefusalNotice,
  StatusBadge,
} from "./shared";

function ProposalCard({
  proposal,
  onDecide,
  pending,
}: {
  proposal: FlowExperimentProposalOut;
  onDecide: (
    proposalId: number,
    action: ProposalAction,
    reason: string,
    onRefusal: (refusal: { code: string; message: string }) => void,
  ) => void;
  pending: boolean;
}) {
  const [confirming, setConfirming] = useState<ProposalAction | null>(null);
  const [refusal, setRefusal] = useState<{ code: string; message: string } | null>(null);
  const actions = proposalActions(proposal.status);

  return (
    <li className="rounded border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{proposal.proposal_key}</span>
        <h3 className="text-sm font-semibold">{proposal.title || "(タイトルなし)"}</h3>
        <StatusBadge
          tone={PROPOSAL_STATUS_TONE[proposal.status]}
          text={`${proposal.status} / ${PROPOSAL_STATUS_LABEL[proposal.status]}`}
          title={PROPOSAL_STATUS_DESCRIPTION[proposal.status]}
        />
        <Badge variant="outline">
          {COMPARISON_SCOPE_LABEL[proposal.comparison_scope] ?? proposal.comparison_scope}
        </Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {PROPOSAL_STATUS_DESCRIPTION[proposal.status]}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        status は追記のみの記録（event）を畳み込んだ結果です。列には保存されていません。
        導出時刻: {formatTimestamp(proposal.status_derived_at)}
      </p>

      <h4 className="mt-3 text-xs font-semibold">承認の判断材料（提案の必須項目）</h4>
      <dl className="mt-1 grid gap-1 text-xs sm:grid-cols-2">
        {proposalRequirements(proposal).map((requirement) => (
          <div key={requirement.key} className="rounded border p-2">
            <dt className="text-muted-foreground">{requirement.label}</dt>
            <dd className="mt-1 break-words">
              {requirement.recorded ? (
                requirement.value
              ) : (
                <span className="text-muted-foreground">記録がありません</span>
              )}
            </dd>
          </div>
        ))}
      </dl>

      <h4 className="mt-3 text-xs font-semibold">評価軸（3 つの契約は合成しません）</h4>
      <ul className="mt-1 space-y-1 text-xs">
        {evaluationLevelGroups(proposal).map((group) => (
          <li key={group.level} className="rounded border p-2">
            <div className="font-medium">{group.label}</div>
            {group.axes.length === 0 ? (
              <div className="mt-1 text-muted-foreground">
                この契約の評価軸は 0 件です。他の契約から導出しません。
              </div>
            ) : (
              <ul className="mt-1 space-y-1">
                {group.axes.map((axis) => (
                  <li key={`${group.level}:${axis.name}`}>
                    {axis.name}
                    {axis.metric ? `（${axis.metric}）` : ""}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>

      <p className="mt-3 rounded border border-dashed p-2 text-xs text-muted-foreground">
        隔離戦略:{" "}
        {ISOLATION_STRATEGY_LABEL[proposal.isolation_strategy] ??
          proposal.isolation_strategy}
        {proposal.isolation_detail ? ` / ${proposal.isolation_detail}` : ""}
        <span className="mt-1 block">
          承認は実行許可ではありません。実行を記録するには、対象 Node の実効モードが
          candidate_execution を許していることが別途必要です（2 つの独立した事実）。
        </span>
      </p>

      {proposal.executions.length > 0 && (
        <div className="mt-3">
          <h4 className="text-xs font-semibold">実行の参照</h4>
          <ul className="mt-1 space-y-1 text-xs">
            {proposal.executions.map((execution) => (
              <li key={execution.id} className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{execution.execution_kind}</Badge>
                <span className="font-mono">{execution.execution_ref}</span>
                <StatusBadge
                  tone={
                    execution.resolution === "resolved"
                      ? "success"
                      : execution.resolution === "stale"
                        ? "warning"
                        : "destructive"
                  }
                  text={execution.resolution}
                />
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-muted-foreground">
            実行の実体は既存の正本です。この層は参照するだけで、何も実行しません。
          </p>
        </div>
      )}

      {proposal.promotion_candidates.length > 0 && (
        <div className="mt-3 rounded border border-dashed p-2 text-xs">
          <div className="font-semibold">昇格候補の記録 {proposal.promotion_candidates.length} 件</div>
          <p className="mt-1 text-muted-foreground">
            候補の記録であって昇格ではありません。実際の昇格は既存の Experiment 採否 /
            固定化 / publish の人間ゲートを通ります。
          </p>
        </div>
      )}

      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-muted-foreground">
          監査記録（追記のみ）{proposal.events.length} 件
        </summary>
        <ul className="mt-2 space-y-1 text-xs">
          {proposal.events.map((event) => (
            <li key={event.id} className="rounded border p-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{event.event_kind}</Badge>
                <span>{PROPOSAL_EVENT_LABEL[event.event_kind]}</span>
                <span className="text-muted-foreground">
                  {formatTimestamp(event.created_at)}
                </span>
              </div>
              <div className="mt-1 text-muted-foreground">
                実行者: {event.actor ?? "(記録なし)"}（{event.actor_kind}） / 判断方法:{" "}
                {event.decision_method}
                {event.reason ? ` / 理由: ${event.reason}` : ""}
              </div>
            </li>
          ))}
        </ul>
      </details>

      <div className="mt-3 flex flex-wrap gap-2">
        {actions.map((action) => (
          <span key={action.action} className="inline-flex flex-col">
            <Button
              size="sm"
              variant={action.action === "approve" ? "default" : "outline"}
              disabled={!action.enabled || pending}
              onClick={() => setConfirming(action.action)}
            >
              {action.label}…
            </Button>
            {!action.enabled && action.disabledReason && (
              <span className="mt-1 max-w-56 text-xs text-muted-foreground">
                {action.disabledReason}
              </span>
            )}
          </span>
        ))}
      </div>

      {refusal && (
        <div className="mt-2">
          <RefusalNotice
            code={refusal.code}
            message={refusal.message}
            hint="サーバーの判定です。この画面は遷移表を持たないので、言い換えも先回りの無効化もしません。"
          />
        </div>
      )}

      <ConfirmActionDialog
        open={confirming !== null}
        title={
          confirming === "approve"
            ? "この提案を承認しますか"
            : confirming === "reject"
              ? "この提案を却下しますか"
              : "この提案を撤回しますか"
        }
        reasonRequired={confirming !== "approve"}
        confirmLabel={
          confirming === "approve"
            ? "承認を記録する"
            : confirming === "reject"
              ? "却下を記録する"
              : "撤回を記録する"
        }
        pending={pending}
        onOpenChange={(open) => setConfirming(open ? confirming : null)}
        onConfirm={(reason) => {
          const action = confirming;
          setConfirming(null);
          if (!action) return;
          setRefusal(null);
          onDecide(proposal.id, action, reason, setRefusal);
        }}
        summary={
          <div className="space-y-1">
            <p>
              <span className="font-mono">{proposal.proposal_key}</span>:{" "}
              {proposal.title || "(タイトルなし)"}
            </p>
            <p className="text-xs text-muted-foreground">
              対象 Node: {proposal.targets.map((t) => t.target_node_key).join(", ") || "—"}
            </p>
            {confirming === "approve" && (
              <p className="text-xs text-muted-foreground">
                承認しても実行は始まりません。実行の記録には、対象 Node の実効モードが
                candidate_execution を許していることが別途必要です。
              </p>
            )}
          </div>
        }
      />
    </li>
  );
}

export function FlowExperimentsPanel({
  proposals,
  isLoading,
  isError,
  onRetry,
  onDecide,
  pending,
  scopeLabel,
}: {
  proposals: FlowExperimentProposalOut[] | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  onDecide: (
    proposalId: number,
    action: ProposalAction,
    reason: string,
    onRefusal: (refusal: { code: string; message: string }) => void,
  ) => void;
  pending: boolean;
  scopeLabel: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle as="h2" className="text-base">
          Flow 実験提案
        </CardTitle>
        <CardDescription>
          {scopeLabel}
          。承認・却下・撤回はいずれも人の判断（decision_method: manual）として
          追記のみの記録に残ります。LLM の出力だけで承認待ちの列には入りません。
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : isError ? (
          <ReadFailure what="実験提案" onRetry={onRetry} />
        ) : (proposals?.length ?? 0) === 0 ? (
          <EmptyNote>実験提案は 0 件です。（取得できなかったのではありません）</EmptyNote>
        ) : (
          <ul className="space-y-3">
            {sortProposals(proposals ?? []).map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
                onDecide={onDecide}
                pending={pending}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
