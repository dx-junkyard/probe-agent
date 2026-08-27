import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FRESHNESS_LABELS, FRESHNESS_VARIANTS, formatDuration, formatElapsed } from "@/components/connectivity-freshness";
import { formatTimestamp } from "@/lib/utils";
import type { OverviewOut } from "@/api/types";

// Issue #384: Runtime health as a SECONDARY area.
//
// Two rules carried over from #370 and never relaxed here:
//
// * `freshness` is the live workload reading and is what the headline says.
//   `state` is the cumulative lifecycle milestone and is shown as history,
//   never as current status — one green 受信中 standing for both is the bug
//   #370 fixed.
// * `transport_freshness` appears only when it DISAGREES with the workload.
//   "The smoke check still gets through" narrows the problem usefully, but it
//   must never be the headline.
//
// Cumulative totals (Component 数 / Trace 総数 / mode 内訳) are still here, but
// behind a disclosure and explicitly labelled as history: they are no longer
// the first thing the screen says (#380 UX原則 1 / 4).

export function RuntimeHealthCard({ overview }: { overview: OverviewOut }) {
  const runtime = overview.runtime;

  if (overview.degraded_sections.includes("runtime") || !runtime) {
    return (
      <Card data-testid="overview-runtime-unavailable">
        <CardHeader>
          <CardTitle as="h2" className="text-lg">Runtime health</CardTitle>
        </CardHeader>
        <CardContent className="text-base text-muted-foreground">
          実行時の状態を取得できませんでした。受信が止まっているという意味ではありません。
        </CardContent>
      </Card>
    );
  }

  const windowHours = Math.round(runtime.window_seconds / 3600);
  const showTransport =
    runtime.transport_freshness !== runtime.freshness &&
    runtime.transport_freshness === "receiving_now";

  return (
    <Card data-testid="overview-runtime">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle as="h2" className="text-lg">Runtime health</CardTitle>
        <Badge
          variant={FRESHNESS_VARIANTS[runtime.freshness]}
          data-testid="overview-runtime-freshness"
          data-freshness={runtime.freshness}
        >
          {FRESHNESS_LABELS[runtime.freshness]}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Relative time comes from the server-measured elapsed seconds, so a
            skewed browser clock cannot turn a live system stale. The absolute
            timestamp is shown next to it (#384 受け入れ条件). */}
        <p className="text-base" data-testid="overview-runtime-last-seen">
          最終受信（実 workload）:{" "}
          {runtime.seconds_since_last_trace != null && runtime.last_real_trace_at != null ? (
            <>
              <span className="font-medium">
                {formatElapsed(runtime.seconds_since_last_trace)}
              </span>{" "}
              <span className="text-muted-foreground">
                ({formatTimestamp(runtime.last_real_trace_at)})
              </span>
            </>
          ) : (
            <span className="text-muted-foreground">まだありません</span>
          )}
        </p>
        {showTransport && (
          <p className="text-sm text-muted-foreground" data-testid="overview-runtime-transport">
            疎通確認 trace は届いています（受信経路は正常）。上の状態は実 workload
            の trace についてのものです。
          </p>
        )}

        <div className="grid grid-cols-3 gap-2 text-base" data-testid="overview-runtime-windows">
          {([
            ["直近5分", runtime.real_trace_count_5m],
            ["直近1時間", runtime.real_trace_count_1h],
            ["直近24時間", runtime.real_trace_count_24h],
          ] as const).map(([label, count]) => (
            <div key={label} className="rounded-md border p-2">
              <div className="text-lg font-semibold">{count}</div>
              <div className="text-sm text-muted-foreground">{label}</div>
            </div>
          ))}
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm" data-testid="overview-runtime-quality">
          <div className="flex justify-between">
            <dt className="text-muted-foreground">error</dt>
            <dd>{runtime.error_count}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">理解との不一致</dt>
            <dd>{runtime.runtime_mismatch_count}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">replayable</dt>
            <dd>
              {runtime.replayable_count}
              {runtime.partial_count > 0 && ` (+partial ${runtime.partial_count})`}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">unreplayable</dt>
            <dd>
              {runtime.unreplayable_count}
              {runtime.not_captured_count > 0 && ` / 未取得 ${runtime.not_captured_count}`}
            </dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-muted-foreground">観測済み component</dt>
            {/* Both numbers are components. Putting this next to 主要機能 read
                as a coverage ratio between two different entities, and the
                numerator could exceed the denominator whenever one Capability
                had several components (#384). */}
            <dd>
              {runtime.observed_component_count} / {runtime.known_component_count}
            </dd>
          </div>
        </dl>
        {runtime.capability_coverage_state === "not_computed" ? (
          <p className="text-sm text-muted-foreground" data-testid="overview-runtime-coverage-unavailable">
            主要機能ごとの観測カバレッジは算出していません。component と Capability
            の対応が保存されていないため、比率を出すと別の単位どうしの割り算になります。
          </p>
        ) : (
          <p className="text-sm text-muted-foreground" data-testid="overview-runtime-coverage">
            観測済みの主要機能: {runtime.observed_capability_count} /{" "}
            {runtime.core_capability_count}
            {runtime.unmapped_component_count != null &&
              runtime.unmapped_component_count > 0 &&
              `（対応不明の component ${runtime.unmapped_component_count} 件は未観測に数えていません）`}
          </p>
        )}
        <p className="text-sm text-muted-foreground">
          error / 不一致 / replay の件数は直近 {windowHours} 時間の実 workload trace
          についてのものです。判定閾値は {formatDuration(runtime.delayed_after_seconds)}
          以上で「受信が途絶えています」、{formatDuration(runtime.stale_after_seconds)}
          以上で「長時間受信なし」。
        </p>

        {/* Cumulative history, disclosed rather than displayed. These are the
            four metric cards the old Overview led with. */}
        <details data-testid="overview-runtime-cumulative">
          <summary className="cursor-pointer text-sm text-muted-foreground">
            これまでの累積（現在の稼働状態ではありません）
          </summary>
          <dl className="mt-2 space-y-1 text-sm text-muted-foreground">
            <div>
              <dt className="inline font-medium">Component 数: </dt>
              <dd className="inline">{runtime.component_count}</dd>
            </div>
            <div>
              <dt className="inline font-medium">Trace 総数: </dt>
              <dd className="inline">{runtime.total_trace_count.toLocaleString()}</dd>
            </div>
            <div>
              <dt className="inline font-medium">mode 内訳: </dt>
              <dd className="inline">
                {Object.entries(runtime.mode_counts)
                  .map(([mode, count]) => `${mode}: ${count}`)
                  .join(", ") || "—"}
              </dd>
            </div>
          </dl>
        </details>

        {/* The full Component list lives on Components, not here (#384: 完全
            一覧を Overview と Components で重複表示しない). */}
        <div className="flex flex-wrap gap-3 text-sm">
          <Link to="/components" className="text-primary underline" data-testid="overview-runtime-components-link">
            Component と Trace の詳細
          </Link>
          <Link to="/connect-sdk" className="text-primary underline">
            接続状態を確認する
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
