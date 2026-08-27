/// <reference types="vitest/globals" />
// Issue #369: ready(解析処理完了)と current(HEAD一致)を別の状態として表示する。
// 監査時は、HEADより古い Snapshot が「ready」として通常の選択肢に並んでいた。

import { render, screen } from "@testing-library/react";
import type { SnapshotPreflightOut } from "@/api/types";
import {
  FRESHNESS_LABELS,
  SnapshotFreshnessBadge,
  SnapshotPreflightPanel,
  SnapshotProcessingBadge,
} from "@/components/snapshot-preflight";

function preflight(overrides: Partial<SnapshotPreflightOut> = {}): SnapshotPreflightOut {
  return {
    snapshot_id: 7,
    processing_state: "ready",
    freshness: "current",
    commit_sha: "abcdef1234567890",
    head_sha: "abcdef1234567890",
    head_relation: "same",
    commits_behind: 0,
    verdict: "ready",
    checks: [
      {
        check_id: "snapshot_processing",
        status: "ok",
        summary: "解析完了",
        detail: "Snapshot #7 の解析は完了しています。",
        remediation: "",
      },
    ],
    recommended_snapshot_id: 7,
    recommended_snapshot_commit_sha: "abcdef1234567890",
    recommended_snapshot_freshness: "current",
    is_recommended: true,
    requires_stale_acknowledgement: false,
    stale_continuation_note: null,
    ...overrides,
  };
}

describe("2つの軸の分離", () => {
  it("ready と current を別のバッジで表示する", () => {
    render(<SnapshotPreflightPanel preflight={preflight()} />);
    expect(screen.getByTestId("snapshot-processing-badge").textContent).toBe("解析完了");
    expect(screen.getByTestId("snapshot-freshness-badge").textContent).toBe(
      FRESHNESS_LABELS.current,
    );
  });

  it("ready のまま stale になり得ることを表示できる", () => {
    render(
      <SnapshotPreflightPanel
        preflight={preflight({
          freshness: "stale",
          verdict: "attention",
          head_relation: "behind",
          commits_behind: 3,
        })}
      />,
    );
    // 解析は完了している、が HEAD より古い。両方が同時に読める。
    expect(screen.getByTestId("snapshot-processing-badge").textContent).toBe("解析完了");
    expect(screen.getByTestId("snapshot-freshness-badge").textContent).toBe(
      FRESHNESS_LABELS.stale,
    );
  });

  it("failed でも current であり得る", () => {
    render(<SnapshotProcessingBadge state="failed" />);
    expect(screen.getByTestId("snapshot-processing-badge").textContent).toBe("解析失敗");
  });

  it("unknown を stale と同じ扱いにしない", () => {
    const { rerender } = render(<SnapshotFreshnessBadge freshness="unknown" />);
    const unknownText = screen.getByTestId("snapshot-freshness-badge").textContent;
    rerender(<SnapshotFreshnessBadge freshness="stale" />);
    expect(screen.getByTestId("snapshot-freshness-badge").textContent).not.toBe(unknownText);
  });
});

describe("推奨Snapshot", () => {
  it("推奨でない Snapshot は再現用途として説明される", () => {
    render(
      <SnapshotPreflightPanel
        preflight={preflight({
          snapshot_id: 3,
          is_recommended: false,
          recommended_snapshot_id: 7,
          freshness: "stale",
        })}
      />,
    );
    const note = screen.getByTestId("snapshot-not-recommended");
    expect(note.textContent).toContain("Snapshot #7");
    expect(note.textContent).toContain("再現用途");
  });

  it("推奨Snapshotのときは再現用途の注記を出さない", () => {
    render(<SnapshotPreflightPanel preflight={preflight()} />);
    expect(screen.queryByTestId("snapshot-not-recommended")).toBeNull();
  });
});

describe("stale継続の理由入力", () => {
  it("definitively stale のときだけ理由入力を求める", () => {
    const { rerender } = render(
      <SnapshotPreflightPanel preflight={preflight()} onStaleReasonChange={() => {}} />,
    );
    expect(screen.queryByTestId("snapshot-stale-ack")).toBeNull();

    rerender(
      <SnapshotPreflightPanel
        preflight={preflight({
          freshness: "stale",
          requires_stale_acknowledgement: true,
          stale_continuation_note: "この Snapshot は HEAD より 3 コミット古いです。",
        })}
        onStaleReasonChange={() => {}}
      />,
    );
    const ack = screen.getByTestId("snapshot-stale-ack");
    expect(ack.textContent).toContain("3 コミット古い");
  });

  it("unknown では理由入力を求めない", () => {
    // HEAD を読めないことは、Snapshot が遅れている証拠ではない。
    render(
      <SnapshotPreflightPanel
        preflight={preflight({ freshness: "unknown", requires_stale_acknowledgement: false })}
        onStaleReasonChange={() => {}}
      />,
    );
    expect(screen.queryByTestId("snapshot-stale-ack")).toBeNull();
  });
});

describe("確認項目", () => {
  it("ok でない項目には回復手順を表示する", () => {
    render(
      <SnapshotPreflightPanel
        preflight={preflight({
          verdict: "blocked",
          checks: [
            {
              check_id: "symbol_index",
              status: "blocking",
              summary: "symbol index がありません",
              detail: "この Snapshot の symbol index は未作成です。",
              remediation: "Repository 画面で symbol index を作成してください。",
            },
          ],
        })}
      />,
    );
    const panel = screen.getByTestId("snapshot-preflight");
    expect(panel.textContent).toContain("symbol index を作成");
    expect(screen.getByTestId("snapshot-preflight-verdict").textContent).toContain(
      "開始できません",
    );
  });

  it("取得前は読み込み表示にする", () => {
    render(<SnapshotPreflightPanel preflight={undefined} isLoading />);
    expect(screen.getByTestId("snapshot-preflight-loading")).toBeTruthy();
  });
});
