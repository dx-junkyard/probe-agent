import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LOOP_STATUS_LABEL, targetHref } from "./display";
import type { OverviewOut } from "@/api/types";

// Issue #383: one primary action, decided by the server's first-match rule
// table, and the improvement loop's current position.
//
// Two rules this component exists to hold:
//
// 1. It renders `overview.next_action` and nothing else. It never picks
//    between candidates, never falls back to a default CTA when the server
//    returned none, and never composes one from counts.
// 2. `waiting` / `unavailable` render as text, never as a disabled button.
//    A permanently-greyed control the developer cannot use is explicitly
//    forbidden (#383) — it teaches them to ignore the primary action.

export function NextActionCard({ overview }: { overview: OverviewOut }) {
  const action = overview.next_action;

  return (
    <Card data-testid="overview-next-action" data-action-state={overview.next_action_state}>
      <CardHeader>
        <CardTitle className="text-base">次にすること</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {action ? (
          <>
            <Link
              to={targetHref(action.target)}
              className="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
              data-testid="overview-next-action-cta"
              data-action-key={action.key}
            >
              {action.label}
            </Link>
            <dl className="space-y-1.5 text-sm">
              <div>
                <dt className="text-xs font-medium text-muted-foreground">なぜこれが次か</dt>
                <dd data-testid="overview-next-action-reason">{action.reason}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-muted-foreground">完了条件</dt>
                <dd data-testid="overview-next-action-completion">{action.completion_condition}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium text-muted-foreground">完了すると</dt>
                <dd data-testid="overview-next-action-value">{action.value}</dd>
              </div>
            </dl>
            {action.blockers.length > 0 && (
              <ul className="list-inside list-disc text-xs text-amber-700 dark:text-amber-300">
                {action.blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            )}
            {action.source_finding_ids.length > 0 && (
              <p className="text-[11px] text-muted-foreground" data-testid="overview-next-action-sources">
                上の「今わかったこと」{action.source_finding_ids.length} 件が理由です。
              </p>
            )}
          </>
        ) : overview.next_action_state === "waiting" ? (
          <p className="text-sm" data-testid="overview-next-action-waiting">
            {overview.next_action_message || "システムが処理中です。完了するまで操作はありません。"}
          </p>
        ) : (
          <p className="text-sm" data-testid="overview-next-action-unavailable">
            {overview.next_action_message ||
              "現在の状態を判定できなかったため、次の操作を提示できません。推測での案内は行いません。"}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function LoopRailCard({ overview }: { overview: OverviewOut }) {
  if (overview.degraded_sections.includes("loop") || overview.loop_stages.length === 0) {
    return (
      <Card data-testid="overview-loop-unavailable">
        <CardHeader>
          <CardTitle className="text-base">改善ループの現在地</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          現在地を取得できませんでした。
        </CardContent>
      </Card>
    );
  }

  const current = overview.loop_stages.find((stage) => stage.status === "current");

  return (
    <Card data-testid="overview-loop">
      <CardHeader>
        <CardTitle className="text-base">改善ループの現在地</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* An ordered list, so the reading order and the semantic order are the
            same for a screen reader as for the eye. */}
        <ol className="space-y-1.5">
          {overview.loop_stages.map((stage) => (
            <li
              key={stage.stage}
              className={
                stage.status === "current"
                  ? "rounded-md border border-primary/60 p-2"
                  : "rounded-md border p-2"
              }
              data-testid={`overview-loop-stage-${stage.stage}`}
              data-stage-status={stage.status}
              aria-current={stage.status === "current" ? "step" : undefined}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium">{stage.label}</span>
                <Badge
                  variant={
                    stage.status === "current"
                      ? "default"
                      : stage.status === "reached"
                        ? "success"
                        : "secondary"
                  }
                >
                  {LOOP_STATUS_LABEL[stage.status]}
                </Badge>
              </div>
              {stage.status === "current" && (
                <p className="mt-1 text-xs text-muted-foreground">{stage.meaning}</p>
              )}
            </li>
          ))}
        </ol>
        {current?.next_milestone && (
          <p className="text-xs text-muted-foreground" data-testid="overview-loop-next-milestone">
            次の到達点: {current.next_milestone}
          </p>
        )}
        {/* 後退・再確認は通常の前進として見せない (#383). The Brief's own
            recheck verdict is the source; this only names it. */}
        {overview.brief?.readiness_state === "recheck_required" && (
          <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="overview-loop-recheck">
            前回の確認後に理解が変わったため、先へ進む前に再確認が必要です。
          </p>
        )}
      </CardContent>
    </Card>
  );
}
