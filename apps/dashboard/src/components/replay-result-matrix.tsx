import type {
  ReplayVariantRunOut,
  ReplayVariantCaseResultOut,
  ReplayVariantAggregateOut,
  TraceEvent,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Link } from "react-router-dom";

// Shared replay-variant diff matrix, extracted from the Simulation Workbench
// (Issue #242 Phase D / #246) so AI Candidate Studio (Issue #252) can reuse
// the exact same finite case-status badges and aggregate rendering instead
// of re-implementing them. Display + composition only -- the diff
// classification itself is the Phase C API's judgement (Principle 6).

const CASE_STATUS_VARIANT: Record<string, "success" | "warning" | "destructive" | "secondary" | "outline"> = {
  match: "success",
  diff: "warning",
  candidate_error: "destructive",
  error_to_success: "success",
  error_to_same_error: "secondary",
  error_to_different_error: "warning",
  skipped: "outline",
};

const CASE_STATUS_LABEL: Record<string, string> = {
  match: "match",
  diff: "diff",
  candidate_error: "candidate error",
  error_to_success: "rescued",
  error_to_same_error: "same error",
  error_to_different_error: "different error",
  skipped: "skipped",
};

export function ResultMatrix({
  run,
  recordedByTraceId,
  traceHref,
  onRequestFix,
}: {
  run: ReplayVariantRunOut | undefined;
  recordedByTraceId: Map<string, TraceEvent>;
  traceHref?: (traceId: string) => string;
  onRequestFix?: (caseResult: ReplayVariantCaseResultOut) => void;
}) {
  const failedCandidates =
    run?.variants.filter((variant) => !variant.is_baseline && variant.status === "failed") ?? [];
  return (
    <div data-testid="replay-result-matrix" className="space-y-3">
      <div className="rounded-md border border-blue-200 bg-blue-50 dark:bg-blue-950/20 dark:border-blue-800 px-3 py-2 text-xs text-blue-900 dark:text-blue-100">
        シミュレーションのみ: Replayはpinされたsnapshotに対して隔離されたnetwork-offの
        sandbox内で実行されます。結果は本番環境（environment、外部サービス、
        時間依存の状態）と異なる場合があり、本番同等性を保証するものではありません。
      </div>
      {failedCandidates.map((variant) => (
        <div
          key={variant.id}
          className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive"
        >
          {variant.label || variant.variant_key}: {variant.apply_error ?? variant.error ?? "Replayに失敗しました"}
        </div>
      ))}
      {!run ? (
        <p className="text-sm text-muted-foreground">variantを実行するとdiff matrixが表示されます。</p>
      ) : run.status !== "completed" ? (
        <p className="text-sm text-muted-foreground">
          Run #{run.id}: {run.status}
          {run.error ? ` -- ${run.error}` : ""}
        </p>
      ) : (
        <MatrixTable
          run={run}
          recordedByTraceId={recordedByTraceId}
          traceHref={traceHref}
          onRequestFix={onRequestFix}
        />
      )}
    </div>
  );
}

function MatrixTable({
  run,
  recordedByTraceId,
  traceHref,
  onRequestFix,
}: {
  run: ReplayVariantRunOut;
  recordedByTraceId: Map<string, TraceEvent>;
  traceHref?: (traceId: string) => string;
  onRequestFix?: (caseResult: ReplayVariantCaseResultOut) => void;
}) {
  const candidates = run.variants.filter((v) => !v.is_baseline);
  const rows = candidates.find((candidate) => candidate.cases.length > 0)?.cases ?? [];

  if (candidates.length === 0 || rows.length === 0) {
    return <p className="text-sm text-muted-foreground">このrunで比較できるcaseがありません。</p>;
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-xs">
        <thead className="bg-muted/40">
          <tr className="text-left">
            <th className="p-2">Trace</th>
            <th className="p-2">記録済み出力</th>
            <th className="p-2">Baseline replay</th>
            {candidates.map((v) => (
              <th key={v.id} className="p-2">
                {v.label || v.variant_key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const recorded = recordedByTraceId.get(row.trace_id);
            return (
              <tr key={row.trace_id} className="border-t align-top">
                <td className="p-2 font-mono">
                  {traceHref ? (
                    <Link className="text-primary underline" to={traceHref(row.trace_id)}>
                      {row.trace_id.slice(0, 10)}
                    </Link>
                  ) : (
                    row.trace_id.slice(0, 10)
                  )}
                </td>
                <td className="p-2 max-w-[16rem]">
                  <pre className="whitespace-pre-wrap break-words">
                    {recorded?.error ?? recorded?.output ?? "—"}
                  </pre>
                </td>
                <td className="p-2 max-w-[16rem]">
                  <pre className="whitespace-pre-wrap break-words">
                    {row.recorded_error ?? row.baseline_output ?? "—"}
                  </pre>
                </td>
                {candidates.map((v) => {
                  const caseResult = v.cases.find((c) => c.position === row.position);
                  return (
                    <td key={v.id} className="p-2 max-w-[16rem]">
                      {caseResult ? (
                        <CaseCell caseResult={caseResult} onRequestFix={onRequestFix} />
                      ) : (
                        "—"
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t bg-muted/20 font-medium">
            <td className="p-2" colSpan={3}>
              集計
            </td>
            {candidates.map((v) => (
              <td key={v.id} className="p-2">
                <AggregateCell aggregate={v.aggregate} />
              </td>
            ))}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function CaseCell({
  caseResult,
  onRequestFix,
}: {
  caseResult: ReplayVariantCaseResultOut;
  onRequestFix?: (caseResult: ReplayVariantCaseResultOut) => void;
}) {
  const canRequestFix = caseResult.case_status !== "match" && caseResult.case_status !== "skipped";
  return (
    <div className="space-y-1">
      <Badge variant={CASE_STATUS_VARIANT[caseResult.case_status] ?? "outline"} className="text-[10px]">
        {CASE_STATUS_LABEL[caseResult.case_status] ?? caseResult.case_status}
      </Badge>
      <pre className="whitespace-pre-wrap break-words">
        {caseResult.candidate_error ?? caseResult.candidate_output ?? "—"}
      </pre>
      {caseResult.field_diffs.length > 0 && (
        <p className="text-muted-foreground">fields: {caseResult.field_diffs.join(", ")}</p>
      )}
      {caseResult.duration_delta_ms != null && (
        <p className="text-muted-foreground">Δ {caseResult.duration_delta_ms.toFixed(1)}ms</p>
      )}
      {canRequestFix && onRequestFix && (
        <Button size="sm" variant="outline" onClick={() => onRequestFix(caseResult)}>
          このTraceを修正
        </Button>
      )}
    </div>
  );
}

function AggregateCell({ aggregate }: { aggregate: ReplayVariantAggregateOut }) {
  return (
    <div className="space-y-0.5 text-[11px]">
      <div>
        match {aggregate.match} / diff {aggregate.diff}
      </div>
      <div>candidate_error {aggregate.candidate_error}</div>
      <div>rescued (error→success) {aggregate.error_to_success}</div>
      <div>
        skipped {aggregate.skipped} / total {aggregate.total}
      </div>
      {aggregate.avg_duration_delta_ms != null && (
        <div>avg Δ {aggregate.avg_duration_delta_ms.toFixed(1)}ms</div>
      )}
    </div>
  );
}
