// Flow・エージェント群 (Epic #412, Issues #414 / #415).
//
// One screen for the three questions Epic #412 exists to answer about a Flow:
// 何のための Flow か / いま LLM と候補実行をどこまで許しているか / どの実験が
// 人の判断を待っているか.
//
// Four rules govern everything here, and every panel is written to keep them:
//
// 1. **The client re-derives nothing.** The effective execution mode
//    (`app/execution_mode.py`'s 10-row table), a proposal's status (#415's
//    event fold), a section's availability (`degraded_sections`) and a Node's
//    axis values all arrive decided. This page renders them.
// 2. **Five axes stay five readings** (§1.2 / ADR-6), and there is NO score,
//    average, completion rate or confidence percentage anywhere on the page
//    (ADR-7 / #353).
// 3. **The five missing answers stay five answers** (§6.4). 「0 件」 and
//    「取得できませんでした」 are different sentences (#356), and a degraded
//    section drops its display without blanking the page.
// 4. **Nothing here is automatic.** Assigning or revoking a mode, and
//    approving / rejecting / withdrawing a proposal, are `decision_method:
//    manual` human decisions (§10): each goes through an explicit
//    confirmation, and a reason where the API requires one. The actor is the
//    authenticated principal and can never be supplied by this screen.

import { useState } from "react";
import { toast } from "sonner";
import {
  useAssignExecutionMode,
  useExecutionModeAssignments,
  useExecutionModeProjection,
  useFlowExperimentDecision,
  useFlowExperiments,
  useFlowExplanation,
  useFlowSubjects,
  useRevokeExecutionModeAssignment,
} from "@/api/hooks";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { FlowSubjectKind } from "@/api/types";
import { FlowExperimentsPanel } from "@/components/flow-agents/experiments-panel";
import { FlowProjectionPanel } from "@/components/flow-agents/flow-projection";
import {
  ExecutionModeControlPanel,
  toRefusal,
} from "@/components/flow-agents/mode-control";
import { FlowSubjectSelector } from "@/components/flow-agents/subject-selector";
import { flowScopeRef } from "@/components/flow-agents/model";

export default function FlowAgentsPage() {
  const subjects = useFlowSubjects();
  const [chosen, setChosen] = useState<{
    kind: FlowSubjectKind;
    ref: string;
  } | null>(null);

  // The developer's own choice always wins; until they make one, the first
  // runtime Flow (falling back to the first static one) is shown. This is a
  // DERIVATION, not an effect that writes state -- an effect would re-select
  // on every list refetch and quietly override the developer.
  //
  // The two kinds are never pooled into one list to choose from (§2.1); the
  // fallback simply reads the runtime list first.
  const firstSubject =
    subjects.data?.runtime_flows[0] ?? subjects.data?.static_flows[0] ?? null;
  const selected =
    chosen ??
    (firstSubject
      ? { kind: firstSubject.subject_kind as FlowSubjectKind, ref: firstSubject.subject_ref }
      : null);

  const explanation = useFlowExplanation(
    selected?.kind ?? null,
    selected?.ref ?? null,
  );
  const modeProjection = useExecutionModeProjection();
  const assignments = useExecutionModeAssignments();
  const assign = useAssignExecutionMode();
  const revoke = useRevokeExecutionModeAssignment();
  const experiments = useFlowExperiments(selected?.ref ?? null, selected?.kind ?? null);
  const decide = useFlowExperimentDecision();

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Flow・エージェント群</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          関連する Evolution Node を Flow 単位で束ね、目的・状態・根拠・待っている
          判断を一つの画面で確認します。この画面は読み取りと人の判断の記録だけを
          行い、本番の実装・policy・patch・publish のどれも変更しません。
        </p>
      </div>

      <div className="grid gap-4 xl:grid-cols-[360px_1fr] xl:items-start">
        <div className="space-y-4">
          <FlowSubjectSelector
            listing={subjects.data}
            isLoading={subjects.isLoading}
            isError={subjects.isError}
            onRetry={() => subjects.refetch()}
            selectedKind={selected?.kind ?? null}
            selectedRef={selected?.ref ?? null}
            onSelect={(kind, ref) => setChosen({ kind, ref })}
          />
        </div>

        <div className="space-y-4">
          {selected === null ? (
            <Card>
              <CardHeader>
                <CardTitle as="h2" className="text-base">
                  Flow が選択されていません
                </CardTitle>
                <CardDescription>
                  左の一覧から runtime_flow または static_flow を選んでください。
                  実行モードの割り当ては Flow を選ばなくても確認できます。
                </CardDescription>
              </CardHeader>
              <CardContent />
            </Card>
          ) : (
            <FlowProjectionPanel
              explanation={explanation.data}
              isLoading={explanation.isLoading}
              isError={explanation.isError}
              onRetry={() => explanation.refetch()}
            />
          )}

          <ExecutionModeControlPanel
            projection={modeProjection.data}
            isLoading={modeProjection.isLoading}
            isError={modeProjection.isError}
            onRetry={() => modeProjection.refetch()}
            history={assignments.data}
            historyIsError={assignments.isError}
            onHistoryRetry={() => assignments.refetch()}
            assignPending={assign.isPending}
            revokePending={revoke.isPending}
            defaultScopeRef={
              selected?.kind === "runtime_flow" ? flowScopeRef(selected.ref) : ""
            }
            onAssign={(values, onRefusal) =>
              assign.mutate(values, {
                onSuccess: () => toast.success("割り当てを記録しました"),
                onError: (error) => onRefusal(toRefusal(error)),
              })
            }
            onRevoke={(assignmentId, reason) =>
              revoke.mutate(
                { assignmentId, reason },
                {
                  onError: (error) => toast.error(toRefusal(error).message),
                },
              )
            }
          />

          <FlowExperimentsPanel
            proposals={experiments.data?.proposals}
            isLoading={experiments.isLoading}
            isError={experiments.isError}
            onRetry={() => experiments.refetch()}
            pending={decide.isPending}
            scopeLabel={
              selected
                ? `subject ${selected.ref} に紐づく提案`
                : "この System のすべての提案"
            }
            onDecide={(proposalId, action, reason, onRefusal) =>
              decide.mutate(
                { proposalId, action, reason },
                {
                  onSuccess: () => toast.success("判断を記録しました"),
                  onError: (error) => onRefusal(toRefusal(error)),
                },
              )
            }
          />
        </div>
      </div>
    </div>
  );
}
