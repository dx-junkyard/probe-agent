import { Badge } from "@/components/ui/badge";
import type { ReplayReadinessOut, ReplayReadinessStatus } from "@/api/types";

// Issue #372: what the developer needs to know BEFORE a candidate is
// generated. The audited component showed 「11 traces」 and auto-selected the
// most recent 50; every one of them was `not captured`, so the run could
// never produce a comparison — and the reasoning-model call was already spent
// by the time that surfaced.

const CHECK_VARIANTS: Record<
  ReplayReadinessStatus,
  "success" | "warning" | "destructive"
> = {
  ok: "success",
  attention: "warning",
  blocking: "destructive",
};

const VERDICT_LABELS: Record<string, string> = {
  ready: "候補を作成できます",
  attention: "確認のうえ作成できます",
  blocked: "このままでは評価できません",
};

export function ReplayReadinessPanel({
  readiness,
  isLoading = false,
}: {
  readiness: ReplayReadinessOut | undefined;
  isLoading?: boolean;
}) {
  if (isLoading && !readiness) {
    return (
      <div className="rounded-lg border p-3 text-xs text-muted-foreground" data-testid="replay-readiness-loading">
        Replay可否を確認しています…
      </div>
    );
  }
  if (!readiness) return null;

  const { counts, selected } = readiness;
  return (
    <div className="space-y-2 rounded-lg border p-3" data-testid="replay-readiness">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium">Replay可否の事前確認</span>
        <Badge
          variant={
            readiness.verdict === "ready"
              ? "success"
              : readiness.verdict === "attention"
                ? "warning"
                : "destructive"
          }
          data-testid="replay-readiness-verdict"
        >
          {VERDICT_LABELS[readiness.verdict] ?? readiness.verdict}
        </Badge>
      </div>

      {/* The split the start screen used to hide behind a single trace count.
          `capture未設定` and `capture失敗` stay separate: one needs a code
          change, the other needs a different trace. */}
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3" data-testid="replay-readiness-counts">
        {([
          ["Trace合計", counts.total],
          ["完全復元", counts.replayable],
          ["一部マスク", counts.partial],
          ["capture失敗", counts.unreplayable],
          ["capture未設定", counts.not_captured],
        ] as const).map(([label, value]) => (
          <div key={label} className="flex items-baseline gap-1">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
      </dl>

      <p className="text-xs text-muted-foreground" data-testid="replay-readiness-selection">
        {readiness.selection_is_automatic
          ? `未選択のため直近最大${readiness.selection_limit}件が使われます — 対象${selected.total}件のうち評価に使えるのは${selected.usable}件です。`
          : `選択した${selected.total}件のうち、評価に使えるのは${selected.usable}件です。`}
      </p>

      <ul className="space-y-1">
        {readiness.checks.map(check => (
          <li key={check.check_id} className="flex items-start gap-2 text-xs">
            <Badge variant={CHECK_VARIANTS[check.status]} className="shrink-0">
              {check.status}
            </Badge>
            <span className="min-w-0">
              <span className="font-medium">{check.summary}</span>
              <span className="text-muted-foreground"> — {check.detail}</span>
              {check.status !== "ok" && check.remediation && (
                <span className="block text-muted-foreground">{check.remediation}</span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
