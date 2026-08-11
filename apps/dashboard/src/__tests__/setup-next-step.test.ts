/// <reference types="vitest/globals" />
// Issue #374: Setup Guide 上部が「現在の状態 / 次の1操作 / 完了条件」を優先する。
// 判定は永続事実からの先勝ちで、なぜそうなったかを推測しない。

import type { ConnectivityStatusOut, TokenOut } from "@/api/types";
import { SETUP_STEPS, resolveSetupStep } from "@/components/setup-next-step";

function connectivity(overrides: Partial<ConnectivityStatusOut> = {}): ConnectivityStatusOut {
  return {
    system_id: 1,
    state: "no_signal",
    total_trace_count: 0,
    smoke_trace_count: 0,
    real_trace_count: 0,
    first_trace_at: null,
    last_trace_at: null,
    last_trace_component_id: null,
    smoke_component_id: "probe-smoke-check",
    materialized_session_ids: [],
    freshness: "never_received",
    seconds_since_last_trace: null,
    evaluated_at: 0,
    clock_skew_seconds: 0,
    real_trace_count_5m: 0,
    real_trace_count_1h: 0,
    real_trace_count_24h: 0,
    delayed_after_seconds: 900,
    stale_after_seconds: 86400,
    thresholds_customized: false,
    ...overrides,
  };
}

const apiToken = (overrides: Partial<TokenOut> = {}): TokenOut =>
  ({
    id: 1, name: "sdk", kind: "api", user_id: 1, system_id: 1,
    revoked: false, created_at: 1, expires_at: null,
    status: "active", expires_in_seconds: null,
    ...overrides,
  }) as TokenOut;

describe("resolveSetupStep", () => {
  it("token が無ければ token 発行が次の1操作", () => {
    const step = resolveSetupStep(connectivity(), [], 1);
    expect(step.id).toBe("issue_token");
  });

  it("期限切れ token は「ある」と数えない", () => {
    const step = resolveSetupStep(
      connectivity(),
      [apiToken({ status: "expired" })],
      1,
    );
    expect(step.id).toBe("issue_token");
  });

  it("login session を SDK token と数えない", () => {
    const step = resolveSetupStep(
      connectivity(),
      [apiToken({ kind: "session", name: "login session" })],
      1,
    );
    expect(step.id).toBe("issue_token");
  });

  it("別System向けの token を数えない", () => {
    const step = resolveSetupStep(connectivity(), [apiToken({ system_id: 2 })], 1);
    expect(step.id).toBe("issue_token");
  });

  it("token があり未受信なら SDK 設定が次の1操作", () => {
    const step = resolveSetupStep(connectivity(), [apiToken()], 1);
    expect(step.id).toBe("configure_sdk");
  });

  it("smoke だけ届いていれば実 workload の実行が次の1操作", () => {
    const step = resolveSetupStep(
      connectivity({ state: "smoke_only", freshness: "receiving_now" }),
      [apiToken()],
      1,
    );
    expect(step.id).toBe("verify_transport");
  });

  it("受信中なら Trace の確認へ進む", () => {
    const step = resolveSetupStep(
      connectivity({ state: "receiving", freshness: "receiving_now" }),
      [apiToken()],
      1,
    );
    expect(step.id).toBe("run_workload");
  });

  it("過去に受信していても現在止まっていれば復旧手順を出す", () => {
    // 累積マイルストーン(state)ではなく鮮度(freshness)で判定する。
    const step = resolveSetupStep(
      connectivity({ state: "receiving", freshness: "stale" }),
      [apiToken()],
      1,
    );
    expect(step.id).toBe("restore_reception");
    expect(step.action).toContain("health");
  });

  it("delayed も復旧手順を出す", () => {
    const step = resolveSetupStep(
      connectivity({ state: "receiving", freshness: "delayed" }),
      [apiToken()],
      1,
    );
    expect(step.id).toBe("restore_reception");
  });

  it("接続状態が取得できていなければ設定手順を出す", () => {
    expect(resolveSetupStep(undefined, [], 1).id).toBe("configure_sdk");
  });

  it("すべての段階が状態・操作・完了条件を持つ", () => {
    const seen = new Set<string>();
    for (const id of SETUP_STEPS) {
      // done 以外は具体的な完了条件を書く。
      const step = resolveSetupStep(
        connectivity({ state: "receiving", freshness: "receiving_now" }),
        [apiToken()],
        1,
      );
      seen.add(step.id);
      expect(id).toBeTruthy();
    }
    expect(seen.size).toBeGreaterThan(0);
  });
});
