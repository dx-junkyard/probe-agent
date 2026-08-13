import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatTimestamp } from "@/lib/utils";
import { FINDING_SEVERITY_VARIANT, FINDING_STATUS_VARIANT, targetHref } from "./display";
import type { OverviewFindingOut, OverviewOut } from "@/api/types";

// Issue #382: 「今わかったこと」 — why the developer comes back.
//
// The selection, the cap, the ordering and the dedupe all happen on the server
// from persisted facts. This component renders `findings.slice(0, N)` in the
// order it received them; it never re-ranks, never re-filters, and never
// decides what matters. Sorting here would silently become a second,
// client-only importance model.
//
// Opening this card is not an acknowledgement. Nothing here writes, and no
// "seen" marker is sent — a page view is not a human decision (#382).

function FindingRow({ finding }: { finding: OverviewFindingOut }) {
  return (
    <li
      className="rounded-md border p-3"
      data-testid="overview-finding"
      data-finding-kind={finding.kind}
      data-finding-severity={finding.severity}
      data-finding-status={finding.status}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {/* Text, not colour alone: every state is readable in greyscale and by
            a screen reader (#384). */}
        <Badge variant={FINDING_SEVERITY_VARIANT[finding.severity]}>
          {finding.severity_label}
        </Badge>
        <Badge variant="outline">{finding.kind_label}</Badge>
        <Badge variant={FINDING_STATUS_VARIANT[finding.status]}>{finding.status_label}</Badge>
        {finding.occurrence_count > 1 && (
          <span className="text-xs text-muted-foreground">{finding.occurrence_count} 件</span>
        )}
      </div>
      <p className="mt-2 text-sm font-medium">{finding.summary}</p>
      {/* 「何が起きたか」 alone is a notification. The impact is what makes it
          a finding. */}
      <p className="mt-1 text-sm text-muted-foreground">
        判断への影響: {finding.decision_impact}
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>出所: {finding.provenance_label}</span>
        {finding.snapshot_id != null && <span>snapshot {finding.snapshot_id}</span>}
        {finding.revision_id != null && <span>理解リビジョン {finding.revision_id}</span>}
        {finding.runtime_window_seconds != null && (
          <span>観測窓 {Math.round(finding.runtime_window_seconds / 3600)} 時間</span>
        )}
        {finding.last_updated != null && (
          <span>最終更新 {formatTimestamp(finding.last_updated)}</span>
        )}
      </div>
      {finding.target && (
        <Link
          to={targetHref(finding.target)}
          className="mt-2 inline-block text-xs text-primary underline"
          data-testid="overview-finding-target"
        >
          {finding.target.label}
        </Link>
      )}
    </li>
  );
}

/** The three empty states are three different answers and never share copy. */
function EmptyState({ overview }: { overview: OverviewOut }) {
  if (overview.findings_state === "unavailable") {
    return (
      <div className="space-y-1 text-sm" data-testid="overview-findings-unavailable">
        <p>今回の変化・発見を取得できませんでした。</p>
        <p className="text-muted-foreground">
          「発見がない」という意味ではありません。取得に失敗しています。
        </p>
      </div>
    );
  }
  if (overview.findings_state === "not_compared") {
    return (
      <div className="space-y-1 text-sm" data-testid="overview-findings-not-compared">
        <p>まだ比較していません。</p>
        <p className="text-muted-foreground">
          {overview.findings_baseline_label}。理解を一度確認すると、それ以降の変化を
          「新しい発見」として提示できます。
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-1 text-sm" data-testid="overview-findings-none">
      <p>重要な新しい発見はありません。</p>
      <p className="text-muted-foreground">
        比較の基準: {overview.findings_baseline_label}
        {overview.findings_baseline_at != null &&
          `（${formatTimestamp(overview.findings_baseline_at)}）`}
      </p>
    </div>
  );
}

export function FindingsCard({ overview }: { overview: OverviewOut }) {
  const [expanded, setExpanded] = useState(false);
  const initial = overview.findings.slice(0, overview.findings_initial_count);
  const shown = expanded ? overview.findings : initial;
  const hidden = overview.findings.length - initial.length;

  return (
    <Card data-testid="overview-findings">
      <CardHeader>
        <CardTitle as="h2" className="text-base">今わかったこと</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {overview.findings.length === 0 ? (
          <EmptyState overview={overview} />
        ) : (
          <>
            <ul className="space-y-2">
              {shown.map((finding) => (
                <FindingRow key={finding.id} finding={finding} />
              ))}
            </ul>
            {hidden > 0 && (
              <button
                type="button"
                className="text-xs text-primary underline"
                onClick={() => setExpanded((value) => !value)}
                data-testid="overview-findings-toggle"
              >
                {expanded ? "初期表示に戻す" : `残り ${hidden} 件を表示`}
              </button>
            )}
            <p className="text-[11px] text-muted-foreground">
              比較の基準: {overview.findings_baseline_label}
              {overview.findings_baseline_at != null &&
                `（${formatTimestamp(overview.findings_baseline_at)}）`}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
