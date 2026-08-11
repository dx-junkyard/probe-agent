import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { formatTimestamp } from "@/lib/utils";
import type { TraceSummaryOut } from "@/api/types";
import {
  formatDuration,
  formatErrorRate,
  formatRelative,
  type TraceFilters,
} from "./trace-monitor";

// Issue #373: the Traces tab led with raw IDs, absolute timestamps, and
// excessive duration precision, and offered no way to narrow by period,
// outcome, or Replay usability. The summary answers "is this component
// healthy" before the table answers "which call".

export function TraceSummaryCard({
  summary,
  now,
  isLoading = false,
  isError = false,
  onRetry,
}: {
  summary: TraceSummaryOut | undefined;
  now: number;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}) {
  if (isLoading && !summary) {
    return <div className="grid grid-cols-3 gap-2 sm:grid-cols-6" aria-busy="true">{[1,2,3,4,5,6].map(i => <div key={i} className="h-14 animate-pulse rounded-md bg-muted" />)}</div>;
  }
  if (isError && !summary) {
    return (
      <div className="flex items-center justify-between rounded-md border border-destructive/40 p-3 text-xs" role="alert">
        <span>Trace要約を取得できませんでした。</span>
        {onRetry && <button type="button" className="underline" onClick={onRetry}>再試行</button>}
      </div>
    );
  }
  if (!summary) return null;
  const tiles: Array<[string, string, string?]> = [
    ["件数", String(summary.total)],
    ["error率", formatErrorRate(summary.error_rate), `${summary.error_count}件`],
    ["p50", formatDuration(summary.duration_p50_ms)],
    ["p95", formatDuration(summary.duration_p95_ms)],
    [
      "最終受信",
      summary.last_trace_at != null ? formatRelative(summary.last_trace_at, now) : "—",
      summary.last_trace_at != null ? formatTimestamp(summary.last_trace_at) : undefined,
    ],
    ["Replay可能", String(summary.replayable_count)],
  ];
  return (
    <dl
      className="grid grid-cols-3 gap-2 sm:grid-cols-6"
      data-testid="trace-summary"
    >
      {tiles.map(([label, value, hint]) => (
        <div key={label} className="rounded-md border p-2" title={hint}>
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="text-sm font-semibold tabular-nums">{value}</dd>
          {hint && <dd className="text-[11px] text-muted-foreground">{hint}</dd>}
        </div>
      ))}
    </dl>
  );
}

export function TraceFilterBar({
  filters,
  onChange,
  matched,
  total,
}: {
  filters: TraceFilters;
  onChange: (next: TraceFilters) => void;
  matched: number;
  total: number;
}) {
  const set = <K extends keyof TraceFilters>(key: K, value: TraceFilters[K]) =>
    onChange({ ...filters, [key]: value });

  return (
    <div className="flex flex-wrap items-end gap-2" data-testid="trace-filter-bar">
      <label className="text-xs">
        <span className="mb-1 block text-muted-foreground">期間</span>
        <Select
          className="h-8 w-28"
          aria-label="期間で絞り込む"
          value={filters.window}
          onChange={e => set("window", e.target.value as TraceFilters["window"])}
        >
          <option value="all">すべて</option>
          <option value="5m">直近5分</option>
          <option value="1h">直近1時間</option>
          <option value="24h">直近24時間</option>
          <option value="7d">直近7日</option>
        </Select>
      </label>
      <label className="text-xs">
        <span className="mb-1 block text-muted-foreground">結果</span>
        <Select
          className="h-8 w-24"
          aria-label="結果で絞り込む"
          value={filters.status}
          onChange={e => set("status", e.target.value as TraceFilters["status"])}
        >
          <option value="all">すべて</option>
          <option value="ok">成功のみ</option>
          <option value="error">エラーのみ</option>
        </Select>
      </label>
      <label className="text-xs">
        <span className="mb-1 block text-muted-foreground">mode</span>
        <Select
          className="h-8 w-24"
          aria-label="modeで絞り込む"
          value={filters.mode}
          onChange={e => set("mode", e.target.value as TraceFilters["mode"])}
        >
          <option value="all">すべて</option>
          <option value="trace">trace</option>
          <option value="shadow">shadow</option>
          <option value="off">off</option>
        </Select>
      </label>
      <label className="text-xs">
        <span className="mb-1 block text-muted-foreground">Replay</span>
        <Select
          className="h-8 w-32"
          aria-label="Replay可否で絞り込む"
          value={filters.replay}
          onChange={e => set("replay", e.target.value as TraceFilters["replay"])}
        >
          <option value="all">すべて</option>
          <option value="usable">評価に使える</option>
          <option value="replayable">完全復元のみ</option>
          <option value="not_captured">capture未設定</option>
        </Select>
      </label>
      <label className="text-xs">
        <span className="mb-1 block text-muted-foreground">並び順</span>
        <Select
          className="h-8 w-28"
          aria-label="並び順"
          value={filters.sort}
          onChange={e => set("sort", e.target.value as TraceFilters["sort"])}
        >
          <option value="recent">最新順</option>
          <option value="slowest">遅い順</option>
          <option value="errors_first">エラー優先</option>
        </Select>
      </label>
      <label className="text-xs">
        <span className="mb-1 block text-muted-foreground">Trace ID</span>
        <Input
          className="h-8 w-40"
          aria-label="Trace IDで絞り込む"
          value={filters.query}
          placeholder="部分一致"
          onChange={e => set("query", e.target.value)}
        />
      </label>
      <span className="pb-1 text-xs text-muted-foreground" data-testid="trace-filter-count">
        {matched} / {total} 件
      </span>
    </div>
  );
}
