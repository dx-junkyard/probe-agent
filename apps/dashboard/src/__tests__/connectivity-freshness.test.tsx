/// <reference types="vitest/globals" />
// Issue #370: 「一度でも疎通した」と「現在も受信している」を同じ緑ラベルで
// 表現しない。監査時は最終受信が約14日前でも緑の「受信中」だった。

import { render, screen } from "@testing-library/react";
import type { ConnectivityStatusOut } from "@/api/types";
import {
  FreshnessBadge,
  FreshnessDetail,
  LifecycleMilestone,
  formatElapsed,
  formatDuration,
} from "@/components/connectivity-freshness";

const NOW = 1_800_000_000;

function status(overrides: Partial<ConnectivityStatusOut> = {}): ConnectivityStatusOut {
  return {
    system_id: 1,
    state: "receiving",
    total_trace_count: 11,
    smoke_trace_count: 1,
    real_trace_count: 10,
    first_trace_at: NOW - 20 * 86400,
    last_trace_at: NOW - 30,
    last_trace_component_id: "summarizer",
    smoke_component_id: "probe-smoke-check",
    materialized_session_ids: [],
    freshness: "receiving_now",
    seconds_since_last_trace: 30,
    evaluated_at: NOW,
    clock_skew_seconds: 0,
    real_trace_count_5m: 3,
    real_trace_count_1h: 7,
    real_trace_count_24h: 10,
    delayed_after_seconds: 900,
    stale_after_seconds: 86400,
    thresholds_customized: false,
    ...overrides,
  };
}

describe("FreshnessBadge", () => {
  it("受信中だけを success で表示する", () => {
    const { rerender } = render(<FreshnessBadge freshness="receiving_now" />);
    expect(screen.getByTestId("freshness-badge").textContent).toBe("受信中");

    rerender(<FreshnessBadge freshness="stale" />);
    const stale = screen.getByTestId("freshness-badge");
    expect(stale.textContent).toBe("長時間受信なし");
    expect(stale.className).not.toContain("success");
  });

  it("4状態それぞれに別のラベルを持つ", () => {
    const labels = (["never_received", "receiving_now", "delayed", "stale"] as const).map(f => {
      const { unmount } = render(<FreshnessBadge freshness={f} />);
      const text = screen.getByTestId("freshness-badge").textContent;
      unmount();
      return text;
    });
    expect(new Set(labels).size).toBe(4);
  });
});

describe("FreshnessDetail", () => {
  it("最終受信を相対時刻と絶対時刻の両方で表示する", () => {
    render(<FreshnessDetail data={status({ seconds_since_last_trace: 14 * 86400 })} />);
    const detail = screen.getByTestId("freshness-detail");
    expect(detail.textContent).toContain("14日前");
    // 絶対時刻(年)も併記される
    expect(detail.textContent).toMatch(/20\d\d/);
  });

  it("期間別件数を表示する", () => {
    render(<FreshnessDetail data={status()} />);
    const windows = screen.getByTestId("freshness-windows");
    expect(windows.textContent).toContain("直近5分");
    expect(windows.textContent).toContain("直近1時間");
    expect(windows.textContent).toContain("直近24時間");
  });

  it("判定閾値を明示する", () => {
    render(<FreshnessDetail data={status()} />);
    expect(screen.getByTestId("freshness-detail").textContent).toContain("15分");
    expect(screen.getByTestId("freshness-detail").textContent).toContain("既定値");
  });

  it("System固有の閾値であることを区別する", () => {
    render(<FreshnessDetail data={status({ thresholds_customized: true })} />);
    expect(screen.getByTestId("freshness-detail").textContent).toContain("System固有");
  });

  it("delayed/stale のときだけ確認導線を出す", () => {
    const { rerender } = render(<FreshnessDetail data={status()} />);
    expect(screen.queryByTestId("freshness-next-steps")).toBeNull();

    rerender(<FreshnessDetail data={status({ freshness: "delayed" })} />);
    const steps = screen.getByTestId("freshness-next-steps");
    expect(steps.textContent).toContain("health");
    expect(steps.textContent).toContain("認証");
    expect(steps.textContent).toContain("network");
    expect(steps.textContent).toContain("workload");
  });

  it("clock skew が大きいときだけ注意を出す", () => {
    const { rerender } = render(<FreshnessDetail data={status()} />);
    expect(screen.queryByTestId("clock-skew-note")).toBeNull();

    rerender(<FreshnessDetail data={status({ clock_skew_seconds: 3600 })} />);
    expect(screen.getByTestId("clock-skew-note")).toBeTruthy();
  });

  it("未受信でも件数表示で落ちない", () => {
    render(
      <FreshnessDetail
        data={status({
          freshness: "never_received",
          last_trace_at: null,
          seconds_since_last_trace: null,
          first_trace_at: null,
          real_trace_count_5m: 0,
          real_trace_count_1h: 0,
          real_trace_count_24h: 0,
        })}
      />,
    );
    expect(screen.getByTestId("freshness-detail").textContent).toContain("まだありません");
  });
});

describe("LifecycleMilestone", () => {
  it("過去の到達であることを明示し、現在の受信状態と区別する", () => {
    render(<LifecycleMilestone data={status({ freshness: "stale" })} />);
    const text = screen.getByTestId("lifecycle-milestone").textContent ?? "";
    expect(text).toContain("これまでの到達");
    expect(text).toContain("現在の受信状態ではありません");
  });

  it("一度も受信していなければ何も表示しない", () => {
    render(<LifecycleMilestone data={status({ state: "no_signal" })} />);
    expect(screen.queryByTestId("lifecycle-milestone")).toBeNull();
  });
});

describe("formatElapsed / formatDuration", () => {
  it("単位を切り替える", () => {
    expect(formatElapsed(45)).toBe("45秒前");
    expect(formatElapsed(120)).toBe("2分前");
    expect(formatElapsed(7200)).toBe("2時間前");
    expect(formatElapsed(14 * 86400)).toBe("14日前");
  });

  it("負の経過時間を0として扱う", () => {
    // Clock skew: the server already treats this as "receiving", so the
    // relative label must not read as a negative time.
    expect(formatElapsed(-500)).toBe("0秒前");
  });

  it("閾値を読める単位で表す", () => {
    expect(formatDuration(900)).toBe("15分");
    expect(formatDuration(86400)).toBe("1日");
  });
});
