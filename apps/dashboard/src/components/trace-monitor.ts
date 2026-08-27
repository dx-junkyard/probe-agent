import type { Replayability, TraceEvent } from "@/api/types";

// Issue #373: the pure filtering/sorting/formatting behind the Traces tab.
// Kept out of the component (same convention as `cockpit/model.ts`) so the
// rules are testable and the table never re-derives them.

export type TraceStatusFilter = "all" | "ok" | "error";
export type TraceModeFilter = "all" | "off" | "trace" | "shadow";
export type TraceReplayFilter = "all" | "usable" | "replayable" | "not_captured";
export type TraceWindowFilter = "all" | "5m" | "1h" | "24h" | "7d";
export type TraceSort = "recent" | "slowest" | "errors_first";

export interface TraceFilters {
  status: TraceStatusFilter;
  mode: TraceModeFilter;
  replay: TraceReplayFilter;
  window: TraceWindowFilter;
  sort: TraceSort;
  /** Substring match on trace id — an exact lookup aid, not a search engine. */
  query: string;
}

export const DEFAULT_FILTERS: TraceFilters = {
  status: "all",
  mode: "all",
  replay: "all",
  window: "all",
  sort: "recent",
  query: "",
};

const WINDOW_SECONDS: Record<Exclude<TraceWindowFilter, "all">, number> = {
  "5m": 300,
  "1h": 3600,
  "24h": 86400,
  "7d": 7 * 86400,
};

/** `partial` still yields a comparison, so "usable" includes it (#372). */
function isUsable(replayability: Replayability | null | undefined): boolean {
  return replayability === "replayable" || replayability === "partial";
}

export function filterTraces(
  traces: TraceEvent[],
  filters: TraceFilters,
  now: number,
): TraceEvent[] {
  const cutoff =
    filters.window === "all" ? null : now / 1000 - WINDOW_SECONDS[filters.window];

  const matched = traces.filter(t => {
    if (filters.status === "ok" && t.error) return false;
    if (filters.status === "error" && !t.error) return false;
    if (filters.mode !== "all" && t.mode !== filters.mode) return false;
    if (filters.replay === "usable" && !isUsable(t.replayability)) return false;
    if (filters.replay === "replayable" && t.replayability !== "replayable") return false;
    // `not_captured` is the NULL case — the component never opted in. It is
    // deliberately not the same as `unreplayable` (#372).
    if (filters.replay === "not_captured" && t.replayability != null) return false;
    if (cutoff != null && t.timestamp < cutoff) return false;
    if (filters.query && !t.trace_id.includes(filters.query)) return false;
    return true;
  });

  // Sorting is total and stable: ties fall back to newest first, so a poll
  // that returns the same rows cannot reorder them.
  return [...matched].sort((a, b) => {
    if (filters.sort === "slowest") {
      const diff = (b.duration_ms ?? -1) - (a.duration_ms ?? -1);
      if (diff !== 0) return diff;
    }
    if (filters.sort === "errors_first") {
      const diff = Number(!!b.error) - Number(!!a.error);
      if (diff !== 0) return diff;
    }
    return b.timestamp - a.timestamp;
  });
}

/**
 * Duration rounded for reading. The raw value stays available on the row's
 * `title`, so precision is recoverable without it dominating the column.
 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60_000).toFixed(1)}min`;
}

/** Relative time, paired with (never replacing) the absolute timestamp. */
export function formatRelative(timestampSeconds: number, now: number): string {
  const seconds = Math.max(0, Math.floor(now / 1000 - timestampSeconds));
  if (seconds < 60) return `${seconds}秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}時間前`;
  return `${Math.floor(hours / 24)}日前`;
}

export function formatErrorRate(rate: number | null | undefined): string {
  // `null` is "no data", which is not "0%".
  if (rate == null) return "—";
  return `${(rate * 100).toFixed(rate > 0 && rate < 0.01 ? 2 : 1)}%`;
}

// --- URL round-trip ---------------------------------------------------------
//
// "sortでき、URLで状態を再現できる" (#373): the filter state has to survive a
// reload and a shared link, so it lives in the query string rather than in
// component state.

const PARAM_KEYS = ["status", "mode", "replay", "window", "sort", "query"] as const;

export function filtersFromSearch(params: URLSearchParams): TraceFilters {
  const read = <T extends string>(key: string, allowed: readonly T[], fallback: T): T => {
    const value = params.get(key);
    return value && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
  };
  return {
    status: read("status", ["all", "ok", "error"] as const, "all"),
    mode: read("mode", ["all", "off", "trace", "shadow"] as const, "all"),
    replay: read("replay", ["all", "usable", "replayable", "not_captured"] as const, "all"),
    window: read("window", ["all", "5m", "1h", "24h", "7d"] as const, "all"),
    sort: read("sort", ["recent", "slowest", "errors_first"] as const, "recent"),
    query: params.get("query") ?? "",
  };
}

/**
 * Write the filters back into an existing param set, preserving unrelated
 * params (`component`, `trace`) so the deep links from Trace Lineage and the
 * analyzers keep working.
 */
export function filtersToSearch(
  filters: TraceFilters,
  existing: URLSearchParams,
): URLSearchParams {
  const next = new URLSearchParams(existing);
  for (const key of PARAM_KEYS) {
    const value = filters[key];
    // Defaults are omitted so an untouched page keeps a clean URL.
    if (!value || value === DEFAULT_FILTERS[key]) next.delete(key);
    else next.set(key, value);
  }
  return next;
}
