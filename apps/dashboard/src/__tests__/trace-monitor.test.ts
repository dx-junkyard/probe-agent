/// <reference types="vitest/globals" />
// Issue #373: duration の過剰精度、絶対時刻のみの表示、期間・異常・Replay可否で
// 絞り込めないこと、URL で状態を再現できないことを直す。

import type { TraceEvent } from "@/api/types";
import {
  DEFAULT_FILTERS,
  filterTraces,
  filtersFromSearch,
  filtersToSearch,
  formatDuration,
  formatErrorRate,
  formatRelative,
} from "@/components/trace-monitor";

const NOW_MS = 1_800_000_000_000;
const NOW_S = NOW_MS / 1000;

function trace(overrides: Partial<TraceEvent> = {}): TraceEvent {
  return {
    trace_id: "t-1",
    component_id: "summarizer",
    mode: "trace",
    input: null,
    output: null,
    error: null,
    duration_ms: 10,
    timestamp: NOW_S - 10,
    ...overrides,
  } as TraceEvent;
}

const filters = (o: Partial<typeof DEFAULT_FILTERS> = {}) => ({ ...DEFAULT_FILTERS, ...o });

describe("filterTraces", () => {
  const sample = [
    trace({ trace_id: "recent-ok", timestamp: NOW_S - 60, duration_ms: 5 }),
    trace({ trace_id: "old-error", timestamp: NOW_S - 90_000, error: "Boom", duration_ms: 900 }),
    trace({ trace_id: "shadow-one", mode: "shadow", timestamp: NOW_S - 600, duration_ms: 50 }),
    trace({ trace_id: "captured", replayability: "replayable", timestamp: NOW_S - 30 }),
    trace({ trace_id: "masked", replayability: "partial", timestamp: NOW_S - 40 }),
  ];

  it("既定ではすべて残る", () => {
    expect(filterTraces(sample, filters(), NOW_MS)).toHaveLength(5);
  });

  it("期間で絞れる", () => {
    const ids = filterTraces(sample, filters({ window: "5m" }), NOW_MS).map(t => t.trace_id);
    expect(ids).toContain("recent-ok");
    expect(ids).not.toContain("old-error");
    expect(ids).not.toContain("shadow-one");
  });

  it("結果で絞れる", () => {
    expect(filterTraces(sample, filters({ status: "error" }), NOW_MS)).toHaveLength(1);
    expect(filterTraces(sample, filters({ status: "ok" }), NOW_MS)).toHaveLength(4);
  });

  it("modeで絞れる", () => {
    const ids = filterTraces(sample, filters({ mode: "shadow" }), NOW_MS).map(t => t.trace_id);
    expect(ids).toEqual(["shadow-one"]);
  });

  it("評価に使えるTraceには partial も含む", () => {
    const ids = filterTraces(sample, filters({ replay: "usable" }), NOW_MS).map(t => t.trace_id);
    expect(ids.sort()).toEqual(["captured", "masked"]);
  });

  it("完全復元のみに絞れる", () => {
    const ids = filterTraces(sample, filters({ replay: "replayable" }), NOW_MS).map(t => t.trace_id);
    expect(ids).toEqual(["captured"]);
  });

  it("capture未設定 は unreplayable と区別される", () => {
    const rows = [
      trace({ trace_id: "never", replayability: null }),
      trace({ trace_id: "failed", replayability: "unreplayable" }),
    ];
    const ids = filterTraces(rows, filters({ replay: "not_captured" }), NOW_MS)
      .map(t => t.trace_id);
    expect(ids).toEqual(["never"]);
  });

  it("Trace IDの部分一致で絞れる", () => {
    const ids = filterTraces(sample, filters({ query: "old" }), NOW_MS).map(t => t.trace_id);
    expect(ids).toEqual(["old-error"]);
  });

  it("遅い順に並べられる", () => {
    const ids = filterTraces(sample, filters({ sort: "slowest" }), NOW_MS).map(t => t.trace_id);
    expect(ids[0]).toBe("old-error");
  });

  it("エラー優先に並べられる", () => {
    const ids = filterTraces(sample, filters({ sort: "errors_first" }), NOW_MS).map(t => t.trace_id);
    expect(ids[0]).toBe("old-error");
  });

  it("既定は最新順で、同着でも順序が安定する", () => {
    const first = filterTraces(sample, filters(), NOW_MS).map(t => t.trace_id);
    const second = filterTraces(sample, filters(), NOW_MS).map(t => t.trace_id);
    expect(first).toEqual(second);
    expect(first[0]).toBe("captured");
  });

  it("入力配列を変更しない", () => {
    const input = [...sample];
    filterTraces(input, filters({ sort: "slowest" }), NOW_MS);
    expect(input.map(t => t.trace_id)).toEqual(sample.map(t => t.trace_id));
  });
});

describe("表示の丸め", () => {
  it("durationを人間向けに丸める", () => {
    expect(formatDuration(0.4)).toBe("<1ms");
    expect(formatDuration(12.3456789)).toBe("12ms");
    expect(formatDuration(1500)).toBe("1.5s");
    expect(formatDuration(90_000)).toBe("1.5min");
    expect(formatDuration(null)).toBe("—");
  });

  it("相対時刻を単位で切り替える", () => {
    expect(formatRelative(NOW_S - 30, NOW_MS)).toBe("30秒前");
    expect(formatRelative(NOW_S - 300, NOW_MS)).toBe("5分前");
    expect(formatRelative(NOW_S - 7200, NOW_MS)).toBe("2時間前");
    expect(formatRelative(NOW_S - 3 * 86400, NOW_MS)).toBe("3日前");
  });

  it("error率のデータ無しを0%と混同しない", () => {
    expect(formatErrorRate(null)).toBe("—");
    expect(formatErrorRate(0)).toBe("0.0%");
    expect(formatErrorRate(0.2)).toBe("20.0%");
  });
});

describe("URLでの状態再現", () => {
  it("往復して同じ状態になる", () => {
    const original = filters({ window: "1h", status: "error", sort: "slowest", query: "abc" });
    const params = filtersToSearch(original, new URLSearchParams());
    expect(filtersFromSearch(params)).toEqual(original);
  });

  it("既定値はURLに書かない", () => {
    const params = filtersToSearch(filters(), new URLSearchParams());
    expect(params.toString()).toBe("");
  });

  it("無関係なパラメータを壊さない", () => {
    // Trace Lineage / analyzer からの deep link を維持する。
    const existing = new URLSearchParams("component=summarizer&trace=t-1");
    const params = filtersToSearch(filters({ status: "error" }), existing);
    expect(params.get("component")).toBe("summarizer");
    expect(params.get("trace")).toBe("t-1");
    expect(params.get("status")).toBe("error");
  });

  it("不正な値は既定値として読む", () => {
    const parsed = filtersFromSearch(new URLSearchParams("status=bogus&sort=nope"));
    expect(parsed.status).toBe("all");
    expect(parsed.sort).toBe("recent");
  });
});
