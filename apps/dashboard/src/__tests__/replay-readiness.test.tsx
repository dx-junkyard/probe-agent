/// <reference types="vitest/globals" />
// Issue #372: 候補生成前に Replay 可否が分かること。監査対象 component は
// 11件の Trace がすべて `not captured` だったが、開始画面は「11 traces」とだけ
// 表示していた。

import { render, screen } from "@testing-library/react";
import type { ReplayReadinessOut } from "@/api/types";
import { ReplayReadinessPanel } from "@/components/replay-readiness";

function readiness(overrides: Partial<ReplayReadinessOut> = {}): ReplayReadinessOut {
  return {
    component_id: "summarizer",
    counts: {
      total: 11, replayable: 0, partial: 0,
      unreplayable: 0, not_captured: 11, usable: 0,
    },
    selected: {
      total: 11, replayable: 0, partial: 0,
      unreplayable: 0, not_captured: 11, usable: 0,
    },
    selection_limit: 50,
    selection_is_automatic: true,
    verdict: "blocked",
    checks: [
      {
        check_id: "replayable_traces",
        status: "blocking",
        summary: "Replay可能なTraceが0件です",
        detail: "11件のTraceのうち、Replayに使えるものは0件です。",
        remediation:
          '@probe(component_id="summarizer", replay_capture=True) を付けて再デプロイしてください。',
      },
    ],
    ...overrides,
  };
}

describe("ReplayReadinessPanel", () => {
  it("Trace合計だけでなく replayable / partial / unreplayable の内訳を示す", () => {
    render(<ReplayReadinessPanel readiness={readiness()} />);
    const counts = screen.getByTestId("replay-readiness-counts");
    expect(counts.textContent).toContain("Trace合計");
    expect(counts.textContent).toContain("完全復元");
    expect(counts.textContent).toContain("一部マスク");
    expect(counts.textContent).toContain("capture失敗");
    expect(counts.textContent).toContain("capture未設定");
  });

  it("capture未設定 と capture失敗 を別項目として表示する", () => {
    render(
      <ReplayReadinessPanel
        readiness={readiness({
          counts: {
            total: 4, replayable: 1, partial: 0,
            unreplayable: 2, not_captured: 1, usable: 1,
          },
        })}
      />,
    );
    // 回復手順が異なるので、ひとつの数にまとめてはならない。
    const text = screen.getByTestId("replay-readiness-counts").textContent ?? "";
    expect(text).toContain("capture失敗");
    expect(text).toContain("capture未設定");
  });

  it("0件のとき具体的な回復手順を示す", () => {
    render(<ReplayReadinessPanel readiness={readiness()} />);
    expect(screen.getByTestId("replay-readiness-verdict").textContent).toContain(
      "評価できません",
    );
    expect(screen.getByTestId("replay-readiness").textContent).toContain(
      "replay_capture=True",
    );
  });

  it("自動選択の内訳と、実際に使える件数を開始前に示す", () => {
    render(
      <ReplayReadinessPanel
        readiness={readiness({
          counts: {
            total: 120, replayable: 120, partial: 0,
            unreplayable: 0, not_captured: 0, usable: 120,
          },
          selected: {
            total: 50, replayable: 40, partial: 10,
            unreplayable: 0, not_captured: 0, usable: 50,
          },
          verdict: "attention",
        })}
      />,
    );
    const note = screen.getByTestId("replay-readiness-selection").textContent ?? "";
    expect(note).toContain("50");
    expect(note).toContain("評価に使えるのは50件");
  });

  it("明示選択のときは自動選択の説明を出さない", () => {
    render(
      <ReplayReadinessPanel
        readiness={readiness({
          selection_is_automatic: false,
          selected: {
            total: 1, replayable: 1, partial: 0,
            unreplayable: 0, not_captured: 0, usable: 1,
          },
          verdict: "ready",
          checks: [],
        })}
      />,
    );
    const note = screen.getByTestId("replay-readiness-selection").textContent ?? "";
    expect(note).toContain("選択した1件");
    expect(note).not.toContain("直近最大");
  });

  it("ok の項目には回復手順を出さない", () => {
    render(
      <ReplayReadinessPanel
        readiness={readiness({
          verdict: "ready",
          checks: [
            {
              check_id: "approval",
              status: "ok",
              summary: "Replay承認あり",
              detail: "承認済みです。",
              remediation: "承認してください。",
            },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("replay-readiness").textContent).not.toContain(
      "承認してください",
    );
  });

  it("取得前は読み込み表示にする", () => {
    render(<ReplayReadinessPanel readiness={undefined} isLoading />);
    expect(screen.getByTestId("replay-readiness-loading")).toBeTruthy();
  });
});
