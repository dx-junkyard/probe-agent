import { Badge } from "@/components/ui/badge";
import { formatTimestamp } from "@/lib/utils";
import type { ConnectivityFreshness, ConnectivityStatusOut } from "@/api/types";

// Issue #370: the live reception reading, kept strictly apart from the
// cumulative `state` milestone. The audited bug was one green 受信中 label
// standing for both, so a system silent for ~14 days still looked healthy.
// Everything here reads `freshness`; nothing here reads `state`.

export const FRESHNESS_LABELS: Record<ConnectivityFreshness, string> = {
  never_received: "未受信",
  receiving_now: "受信中",
  delayed: "受信が途絶えています",
  stale: "長時間受信なし",
};

export const FRESHNESS_VARIANTS: Record<
  ConnectivityFreshness,
  "success" | "warning" | "destructive" | "secondary"
> = {
  never_received: "secondary",
  receiving_now: "success",
  delayed: "warning",
  stale: "destructive",
};

/**
 * Relative time in Japanese, from a server-measured elapsed value.
 *
 * The server sends the elapsed seconds it computed against its own clock, so
 * a browser clock that disagrees cannot turn a live system into a stale one.
 */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}秒前`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes}分前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}時間前`;
  return `${Math.floor(hours / 24)}日前`;
}

/** Threshold rendering, so the displayed state is explainable in place. */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}秒`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.round(minutes)}分`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}時間`;
  return `${Math.round(hours / 24)}日`;
}

export function FreshnessBadge({ freshness }: { freshness: ConnectivityFreshness }) {
  return (
    <Badge variant={FRESHNESS_VARIANTS[freshness]} data-testid="freshness-badge">
      {FRESHNESS_LABELS[freshness]}
    </Badge>
  );
}

/** The finite next-step checks offered when reception has stopped. */
const STOPPED_CHECKS: Array<{ label: string; detail: string }> = [
  { label: "health", detail: "Control Server の /health が応答するか" },
  { label: "認証", detail: "SDK が使う API token が失効・期限切れになっていないか" },
  { label: "network", detail: "監視対象から PROBE_SERVER_URL へ到達できるか" },
  { label: "workload", detail: "@probe を付けた関数が実際に呼ばれているか" },
];

export function FreshnessDetail({ data }: { data: ConnectivityStatusOut }) {
  const stopped = data.freshness === "delayed" || data.freshness === "stale";

  return (
    <div className="space-y-2" data-testid="freshness-detail">
      {data.last_trace_at != null && data.seconds_since_last_trace != null ? (
        <p className="text-xs text-muted-foreground">
          最終受信:{" "}
          <span className="text-foreground">
            {formatElapsed(data.seconds_since_last_trace)}
          </span>{" "}
          ({formatTimestamp(data.last_trace_at)})
          {data.last_trace_component_id && (
            <>
              {" "}· component{" "}
              <code className="font-mono">{data.last_trace_component_id}</code>
            </>
          )}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">最終受信: まだありません</p>
      )}

      {/* Windowed counts: a cumulative total can never decrease, so it can
          never show that traffic stopped. These can. */}
      <div className="grid grid-cols-3 gap-2 text-sm" data-testid="freshness-windows">
        {([
          ["直近5分", data.real_trace_count_5m],
          ["直近1時間", data.real_trace_count_1h],
          ["直近24時間", data.real_trace_count_24h],
        ] as const).map(([label, count]) => (
          <div key={label} className="rounded-md border p-2">
            <div className="text-lg font-semibold">{count}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
          </div>
        ))}
      </div>

      <p className="text-[11px] text-muted-foreground">
        判定閾値: {formatDuration(data.delayed_after_seconds)}以上で「受信が途絶えています」、
        {formatDuration(data.stale_after_seconds)}以上で「長時間受信なし」
        {data.thresholds_customized ? "（このSystem固有の設定）" : "（既定値）"}。
        件数は実 workload trace のみで、smoke trace を含みません。
      </p>

      {data.clock_skew_seconds > 120 && (
        <p className="text-[11px] text-amber-700 dark:text-amber-300" data-testid="clock-skew-note">
          監視対象の時刻が Control Server より約{formatDuration(data.clock_skew_seconds)}進んでいます。
          鮮度の判定はこのずれの影響を受けます。
        </p>
      )}

      {stopped && (
        <div
          className="rounded-md border border-amber-500/40 bg-amber-50 p-2 text-xs dark:bg-amber-950/20"
          data-testid="freshness-next-steps"
        >
          <p className="font-medium">確認する順序</p>
          <ul className="mt-1 space-y-0.5 text-muted-foreground">
            {STOPPED_CHECKS.map(check => (
              <li key={check.label}>
                <span className="font-medium text-foreground">{check.label}</span> — {check.detail}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * The cumulative milestone, shown as history rather than as live status.
 *
 * Kept in this file next to the freshness display precisely so the difference
 * is visible to whoever edits either one.
 */
export function LifecycleMilestone({ data }: { data: ConnectivityStatusOut }) {
  if (data.state === "no_signal") return null;
  return (
    <p className="text-[11px] text-muted-foreground" data-testid="lifecycle-milestone">
      これまでの到達:{" "}
      {data.state === "receiving"
        ? "実 workload trace を受信したことがあります"
        : "疎通確認 trace のみ受信したことがあります"}
      {data.first_trace_at != null && <>（初回 {formatTimestamp(data.first_trace_at)}）</>}
      。これは過去の記録で、現在の受信状態ではありません。
    </p>
  );
}
