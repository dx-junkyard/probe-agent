// Epic #412 / #413: reading and changing the execution mode.
//
// Three things this panel must never do:
//
//   * Re-derive an effective mode. The 10-row table of §3.3 lives in
//     `app/execution_mode.py` and nowhere else; this panel renders the
//     server's `mode` / `mode_source` / `mode_reason` / `scope_trace`.
//   * Collapse 「既定の fixed」 into 「人が fixed を選んだ」. `mode_source:
//     "default"` and `system` + `system_assignment` are two different facts
//     (§4.4) and get two different sentences.
//   * Present an assignment or a revocation as automatic. Both are
//     `decision_method: manual` human decisions (§10), so both go through an
//     explicit confirmation and a required reason, and the actor comes from
//     the authenticated principal — never from this form.

import { useState } from "react";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTimestamp } from "@/lib/utils";
import { toast } from "sonner";
import type {
  ExecutionMode,
  ExecutionModeAssignmentOut,
  ExecutionModeDecisionOut,
  ExecutionModeProjectionOut,
  ExecutionModeScopeKind,
  FlowSubjectKind,
} from "@/api/types";
import type { AssignScope } from "./model";
import {
  DENIAL_CODE_LABEL,
  DIVERGENCE_DESCRIPTION,
  DIVERGENCE_LABEL,
  DIVERGENCE_TONE,
  EXECUTION_CAPABILITY_LABEL,
  EXECUTION_MODE_DESCRIPTION,
  EXECUTION_MODE_LABEL,
  EXECUTION_MODE_ORDER,
  MODE_SCOPE_KIND_LABEL,
  SCOPE_REF_FIELD_HINT,
  SCOPE_REF_FIELD_LABEL,
  SCOPE_REF_FIELD_PLACEHOLDER,
  SCOPE_STATE_LABEL,
  describeAssignment,
  describeModeOrigin,
  initialAssignScope,
  scopeRefForKind,
  sortModeNodes,
} from "./model";
import {
  ConfirmActionDialog,
  EmptyNote,
  ReadFailure,
  RefusalNotice,
  StatusBadge,
} from "./shared";

const SCOPE_KINDS: ExecutionModeScopeKind[] = ["system", "flow", "node"];

function DecisionSummary({ decision }: { decision: ExecutionModeDecisionOut }) {
  const origin = describeModeOrigin(
    decision.source_scope,
    decision.reason,
    decision.source_ref,
  );
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge
          tone={decision.mode === "fixed" ? "neutral" : "success"}
          text={`${decision.mode} / ${EXECUTION_MODE_LABEL[decision.mode]}`}
        />
        <StatusBadge
          tone={origin.chosen ? "success" : "warning"}
          text={origin.headline}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {EXECUTION_MODE_DESCRIPTION[decision.mode]}
      </p>
      <p className="text-xs text-muted-foreground">
        判定理由: {origin.reasonLabel}
        <span className="ml-1 font-mono">（{decision.reason}）</span>
      </p>
      <p className="text-xs text-muted-foreground">{origin.nextAction}</p>
      <div>
        <div className="text-xs font-semibold">許可されている capability</div>
        {decision.permitted_capabilities.length === 0 ? (
          <EmptyNote>
            許可されている capability は 0 件です。実験用 LLM も候補実行も到達できません。
          </EmptyNote>
        ) : (
          <ul className="mt-1 space-y-1 text-xs">
            {decision.permitted_capabilities.map((capability) => (
              <li key={capability} className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{capability}</span>
                <span className="text-muted-foreground">
                  {EXECUTION_CAPABILITY_LABEL[capability]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">
          どのスコープが効いたか（scope_trace）
        </summary>
        <ul className="mt-2 space-y-1 text-xs">
          {decision.scope_trace.map((reading, index) => (
            <li
              key={`${reading.scope_kind}:${reading.scope_ref}:${index}`}
              className="flex flex-wrap items-center gap-2 rounded border p-2"
            >
              <span className="font-mono">
                {reading.scope_kind}:{reading.scope_ref || "(System 全体)"}
              </span>
              <StatusBadge
                tone={reading.state === "active" ? "success" : "neutral"}
                text={`${reading.state} / ${SCOPE_STATE_LABEL[reading.state]}`}
              />
              <span className="text-muted-foreground">
                mode: {reading.mode ?? "—"}
              </span>
              {reading.effective_until && (
                <span className="text-muted-foreground">
                  期限: {formatTimestamp(reading.effective_until)}
                </span>
              )}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/** The assign form.
 *
 * SAFETY, not cosmetics: an assignment is what grants a scope permission to
 * reach the experiment LLM and to run a candidate (§1.3). A form whose
 * `scope_kind` and `scope_ref` can disagree — or that keeps a ref captured
 * when it mounted while the developer moves to another Flow — can hand that
 * permission to a subject the developer never chose, and the server cannot
 * detect the mistake because the submitted ref is perfectly valid for the
 * wrong scope. Three rules therefore hold at all times:
 *
 *   1. The opening `scope_kind` and `scope_ref` are ONE value
 *      (`initialAssignScope`), so the as-opened form is always coherent.
 *   2. The form follows the selected subject. The parent re-keys this
 *      component on `initial`, so a subject change re-derives the whole form
 *      from props instead of keeping mount-time state (and without the
 *      `setState`-in-effect the lint rule forbids).
 *   3. Changing the kind re-derives the ref (`scopeRefForKind`) and never
 *      carries the other kind's value across.
 */
function AssignForm({
  pending,
  onSubmit,
  refusal,
  initial,
}: {
  pending: boolean;
  onSubmit: (values: {
    scope_kind: ExecutionModeScopeKind;
    scope_ref: string;
    mode: ExecutionMode;
    reason: string;
    effective_until: number | null;
  }) => void;
  refusal: { code: string; message: string } | null;
  initial: AssignScope;
}) {
  const [scopeKind, setScopeKind] = useState<ExecutionModeScopeKind>(initial.scopeKind);
  const [scopeRef, setScopeRef] = useState(initial.scopeRef);
  const [mode, setMode] = useState<ExecutionMode>("propose");
  const [until, setUntil] = useState("");
  const [confirming, setConfirming] = useState(false);

  const untilSeconds = until ? Date.parse(until) / 1000 : null;
  const invalidUntil = until !== "" && Number.isNaN(untilSeconds);
  const missingScopeRef = scopeKind !== "system" && scopeRef.trim().length === 0;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="mode-scope-kind">スコープ種別</Label>
          <Select
            id="mode-scope-kind"
            value={scopeKind}
            onChange={(event) => {
              const next = event.target.value as ExecutionModeScopeKind;
              setScopeKind(next);
              // The ref is RE-DERIVED for the new kind, never carried over.
              // Switching back to the subject's own kind restores its ref;
              // every other switch clears the field, so a `node_key` can
              // never be submitted as a Flow ref (or the reverse).
              setScopeRef(scopeRefForKind(next, initial));
            }}
          >
            {SCOPE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind} / {MODE_SCOPE_KIND_LABEL[kind]}
              </option>
            ))}
          </Select>
          <p className="text-xs text-muted-foreground">
            static_flow はスコープになりません。Flow スコープは runtime_flow
            （実行時に観測された flow_id）だけです。
          </p>
        </div>
        <div className="space-y-1">
          {/* The label carries the kind, so the field can never be read as a
              generic "ref" box: what belongs in it is stated where the
              developer is typing, not in a hint below. */}
          <Label htmlFor="mode-scope-ref">
            スコープ参照（{SCOPE_REF_FIELD_LABEL[scopeKind]}）
          </Label>
          <Input
            id="mode-scope-ref"
            value={scopeKind === "system" ? "" : scopeRef}
            disabled={scopeKind === "system"}
            onChange={(event) => setScopeRef(event.target.value)}
            placeholder={SCOPE_REF_FIELD_PLACEHOLDER[scopeKind]}
          />
          <p className="text-xs text-muted-foreground">
            {SCOPE_REF_FIELD_HINT[scopeKind]}
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="mode-value">割り当てる mode</Label>
          <Select
            id="mode-value"
            value={mode}
            onChange={(event) => setMode(event.target.value as ExecutionMode)}
          >
            {EXECUTION_MODE_ORDER.map((value) => (
              <option key={value} value={value}>
                {value} / {EXECUTION_MODE_LABEL[value]}
              </option>
            ))}
          </Select>
          <p className="text-xs text-muted-foreground">
            {EXECUTION_MODE_DESCRIPTION[mode]}
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="mode-until">期限（任意）</Label>
          <Input
            id="mode-until"
            type="datetime-local"
            value={until}
            onChange={(event) => setUntil(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            期限が切れると上位スコープへは継承されず fixed へ倒れます。時間の経過だけで
            権限が戻る経路はありません。
          </p>
          {invalidUntil && (
            <p className="text-xs text-destructive">日時を解釈できませんでした。</p>
          )}
        </div>
      </div>
      <Button
        disabled={pending || invalidUntil || missingScopeRef}
        onClick={() => setConfirming(true)}
      >
        割り当てを記録する…
      </Button>
      {missingScopeRef && (
        <p className="text-xs text-muted-foreground">
          スコープ参照を入力するまで記録できません。どのスコープに効かせるかが
          決まっていない割り当ては、あとから読んでも意味が定まりません。
        </p>
      )}
      {refusal && (
        <RefusalNotice
          code={refusal.code}
          message={refusal.message}
          hint="サーバーの判定です。この画面は解決表を持たないので、先回りして無効化することも言い換えることもしません。"
        />
      )}
      <ConfirmActionDialog
        open={confirming}
        title="実行モードを割り当てますか"
        reasonRequired
        confirmLabel="割り当てを記録する"
        pending={pending}
        onOpenChange={setConfirming}
        onConfirm={(reason) => {
          onSubmit({
            scope_kind: scopeKind,
            scope_ref: scopeKind === "system" ? "" : scopeRef.trim(),
            mode,
            reason,
            effective_until: untilSeconds,
          });
          setConfirming(false);
        }}
        summary={
          <div className="space-y-1">
            <p>
              <span className="font-mono">
                {scopeKind}:{scopeKind === "system" ? "(System 全体)" : scopeRef.trim()}
              </span>{" "}
              に <span className="font-mono">{mode}</span>（
              {EXECUTION_MODE_LABEL[mode]}）を割り当てます。
            </p>
            <p className="text-xs text-muted-foreground">
              {EXECUTION_MODE_DESCRIPTION[mode]}
            </p>
            <p className="text-xs text-muted-foreground">
              これは追記のみの記録です。既存の行は書き換えません。
            </p>
          </div>
        }
      />
    </div>
  );
}

function CurrentAssignmentRow({
  row,
  onRevoke,
  pending,
}: {
  row: ExecutionModeAssignmentOut;
  onRevoke: (assignmentId: number, reason: string) => void;
  pending: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const described = describeAssignment(row);
  return (
    <li className="rounded border p-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono">
          {row.scope_kind}:{described.scopeRef}
        </span>
        <StatusBadge tone="neutral" text={described.recordLabel} />
        {row.mode && (
          <StatusBadge tone="success" text={`${row.mode} / ${described.modeLabel}`} />
        )}
        {row.scope_state && (
          <StatusBadge
            tone={row.scope_state === "active" ? "success" : "warning"}
            text={`${row.scope_state} / ${SCOPE_STATE_LABEL[row.scope_state]}`}
          />
        )}
      </div>
      <div className="mt-1 text-muted-foreground">
        期間: {described.window} / 直前の実効モード:{" "}
        {row.previous_mode ?? "(記録なし)"} / 記録者: {described.actorLabel}
      </div>
      <div className="mt-1 text-muted-foreground">理由: {row.reason}</div>
      {row.record_kind === "assign" && (
        <div className="mt-2">
          <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
            この割り当てを終了する…
          </Button>
        </div>
      )}
      <ConfirmActionDialog
        open={confirming}
        title="この割り当てを終了しますか"
        reasonRequired
        confirmLabel="revoke を記録する"
        pending={pending}
        onOpenChange={setConfirming}
        onConfirm={(reason) => {
          onRevoke(row.id, reason);
          setConfirming(false);
        }}
        summary={
          <div className="space-y-1">
            <p>
              <span className="font-mono">
                {row.scope_kind}:{described.scopeRef}
              </span>{" "}
              の割り当てを終了します。
            </p>
            <p className="text-xs text-muted-foreground">
              revoke は「人が明示的に終了した」記録です。以降は通常の継承が再開します。
              期限切れ（expired）とは別の事実で、そちらは fixed へ倒れたままになります。
            </p>
          </div>
        }
      />
    </li>
  );
}

export function ExecutionModeControlPanel({
  projection,
  isLoading,
  isError,
  onRetry,
  history,
  historyIsError,
  onHistoryRetry,
  onAssign,
  onRevoke,
  assignPending,
  revokePending,
  selectedSubject,
}: {
  projection: ExecutionModeProjectionOut | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  history: ExecutionModeAssignmentOut[] | undefined;
  historyIsError: boolean;
  onHistoryRetry: () => void;
  onAssign: (
    values: {
      scope_kind: ExecutionModeScopeKind;
      scope_ref: string;
      mode: ExecutionMode;
      reason: string;
      effective_until: number | null;
    },
    onRefusal: (refusal: { code: string; message: string }) => void,
  ) => void;
  onRevoke: (assignmentId: number, reason: string) => void;
  assignPending: boolean;
  revokePending: boolean;
  /** The subject the screen is currently showing, or `null` when none is
   * selected. The form's opening scope is DERIVED from it — the panel never
   * receives a bare ref whose kind it would have to guess. */
  selectedSubject: { kind: FlowSubjectKind; ref: string } | null;
}) {
  const [refusal, setRefusal] = useState<{ code: string; message: string } | null>(null);
  const initialScope = initialAssignScope(selectedSubject);

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (isError || !projection) {
    return (
      <Card>
        <CardContent className="p-6">
          <ReadFailure what="実行モード" onRetry={onRetry} />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle as="h2" className="text-base">
            System の実効モード
          </CardTitle>
          <CardDescription>
            実行モードは 5 本目の独立した軸です。Node maturity / Cell Improvement
            status / SDK policy mode / 画面上の進行位置のどれからも導出されません。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DecisionSummary decision={projection.system_decision} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle as="h2" className="text-base">
            Node ごとの実効モード
          </CardTitle>
          <CardDescription>
            maturity は実効モードの隣に並べているだけで、互いから導出されません。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {projection.nodes.length === 0 ? (
            <EmptyNote>この System には Evolution Node が 0 件です。</EmptyNote>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <caption className="sr-only">
                  Node ごとの実効モード、その出所、maturity、設定と実行の突き合わせ
                </caption>
                <thead>
                  <tr className="border-b">
                    <th scope="col" className="py-2 pr-2">node_key</th>
                    <th scope="col" className="py-2 pr-2">実行モード</th>
                    <th scope="col" className="py-2 pr-2">モードの出所</th>
                    <th scope="col" className="py-2 pr-2">Node maturity</th>
                    <th scope="col" className="py-2 pr-2">設定と実行</th>
                  </tr>
                </thead>
                <tbody>
                  {sortModeNodes(projection.nodes).map((node) => {
                    const origin = describeModeOrigin(
                      node.mode_source,
                      node.mode_reason,
                      node.source_ref,
                    );
                    return (
                      <tr key={node.node_key} className="border-b align-top">
                        <td className="py-2 pr-2 font-mono">{node.node_key}</td>
                        <td className="py-2 pr-2">
                          <StatusBadge
                            tone={node.execution_mode === "fixed" ? "neutral" : "success"}
                            text={`${node.execution_mode} / ${EXECUTION_MODE_LABEL[node.execution_mode]}`}
                          />
                        </td>
                        <td className="py-2 pr-2">
                          <StatusBadge
                            tone={origin.chosen ? "success" : "warning"}
                            text={origin.headline}
                          />
                          <div className="mt-1 font-mono text-muted-foreground">
                            {node.mode_source} / {node.mode_reason}
                          </div>
                        </td>
                        <td className="py-2 pr-2 font-mono">{node.maturity}</td>
                        <td className="py-2 pr-2">
                          <StatusBadge
                            tone={DIVERGENCE_TONE[node.divergence]}
                            text={`${node.divergence} / ${DIVERGENCE_LABEL[node.divergence]}`}
                            title={DIVERGENCE_DESCRIPTION[node.divergence]}
                          />
                          <div className="mt-1 text-muted-foreground">
                            観測: {node.observed_mode ?? "(観測なし)"}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle as="h2" className="text-base">
            モードを割り当てる
          </CardTitle>
          <CardDescription>
            割り当ても revoke も人の判断（decision_method: manual）として記録されます。
            実行者は認証されたユーザー名で、この画面からは指定できません。理由は必須です。
            初期スコープは表示中の subject から決まります: runtime_flow を選んでいれば
            flow スコープとその flow_id、それ以外は node スコープで参照は空欄です。
            System 全体はこの画面で最も広い権限付与なので、既定では選ばれません。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {/* Re-keyed on the derived scope: when the developer selects a
              different Flow the whole form is rebuilt from props, so a ref
              captured for the previous subject can never survive into the
              next assignment. Deriving beats an effect here — an effect that
              wrote state would also be the `react-hooks/set-state-in-effect`
              violation. */}
          <AssignForm
            key={`${initialScope.scopeKind}:${initialScope.scopeRef}`}
            pending={assignPending}
            refusal={refusal}
            initial={initialScope}
            onSubmit={(values) => {
              setRefusal(null);
              onAssign(values, setRefusal);
            }}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle as="h2" className="text-base">
            現在有効な割り当て
          </CardTitle>
          <CardDescription>
            各スコープの最新行です。行は書き換えず、常に追記します。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {projection.assignments.length === 0 ? (
            <EmptyNote>
              有効な割り当ては 0 件です。この場合すべてのスコープが既定の fixed
              として読まれます（人が fixed を選んだのではありません）。
            </EmptyNote>
          ) : (
            <ul className="space-y-2">
              {projection.assignments.map((row) => (
                <CurrentAssignmentRow
                  key={row.id}
                  row={row}
                  pending={revokePending}
                  onRevoke={(assignmentId, reason) => {
                    onRevoke(assignmentId, reason);
                    toast.success("revoke を記録しました");
                  }}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle as="h2" className="text-base">
            割り当ての履歴（追記のみ）
          </CardTitle>
          <CardDescription>
            1 行がそのまま監査記録です。新しい順に表示します。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {historyIsError ? (
            <ReadFailure what="割り当て履歴" onRetry={onHistoryRetry} />
          ) : (history?.length ?? 0) === 0 ? (
            <EmptyNote>履歴は 0 件です。</EmptyNote>
          ) : (
            <ul className="space-y-1">
              {(history ?? []).map((row) => {
                const described = describeAssignment(row);
                return (
                  <li key={row.id} className="rounded border p-2 text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-muted-foreground">
                        {formatTimestamp(row.created_at)}
                      </span>
                      <StatusBadge tone="neutral" text={described.recordLabel} />
                      <span className="font-mono">
                        {row.scope_kind}:{described.scopeRef}
                      </span>
                      {row.mode ? (
                        <span className="font-mono">→ {row.mode}</span>
                      ) : (
                        <span className="text-muted-foreground">
                          （revoke 行は mode を持ちません）
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-muted-foreground">
                      直前の実効モード: {row.previous_mode ?? "(記録なし)"} / 記録者:{" "}
                      {described.actorLabel}
                    </div>
                    <div className="mt-1 text-muted-foreground">理由: {row.reason}</div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** Turn an `ApiError` from a gated endpoint into the finite code + message the
 * screen shows. The server's 409 body carries `denial_code`; when it does, its
 * Japanese gloss is appended so the developer reads what was refused without
 * looking the code up. */
export function toRefusal(error: unknown): { code: string; message: string } {
  const apiError = error as ApiError;
  // The capability gate names its code `denial_code`; #415's lifecycle gate
  // names its own `code`. Both are read, neither is invented.
  const code = apiError?.denialCode ?? apiError?.code ?? "unknown";
  const known = DENIAL_CODE_LABEL[code as keyof typeof DENIAL_CODE_LABEL];
  return {
    code,
    message: known ? `${known}（${apiError?.detail ?? ""}）` : apiError?.detail || "記録できませんでした",
  };
}
