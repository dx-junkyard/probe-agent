/// <reference types="vitest/globals" />
// Issue #371: 候補作成・確認が3つの別プロダクトに見えないよう、同じ改善ループの
// 現在地を全画面で示す。「その操作が何を作るのか」も画面上で説明する。

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { CandidateVersionOut } from "@/api/types";
import { ImprovementLoopRail } from "@/components/improvement-loop/rail";
import {
  LOOP_STAGES,
  STAGE_DEFINITIONS,
  buildStages,
  loopSearchParams,
  resolveStage,
} from "@/components/improvement-loop/model";

function version(overrides: Partial<CandidateVersionOut> = {}): CandidateVersionOut {
  return {
    id: 1,
    session_id: 1,
    version_number: 1,
    replay_status: null,
    promoted_at: null,
    ...overrides,
  } as CandidateVersionOut;
}

describe("resolveStage", () => {
  it("何も選ばれていなければ最初の段階", () => {
    expect(resolveStage({})).toBe("select");
  });

  it("componentが選ばれたら候補作成の段階", () => {
    expect(resolveStage({ componentId: "summarizer" })).toBe("create");
  });

  it("生成済みで未承認なら安全性確認の段階", () => {
    expect(resolveStage({ sessionId: 1, version: version(), approved: false })).toBe(
      "verify",
    );
  });

  it("承認済みなら比較の段階", () => {
    expect(resolveStage({ sessionId: 1, version: version(), approved: true })).toBe(
      "compare",
    );
  });

  it("Replay実行中は比較の段階", () => {
    expect(
      resolveStage({ sessionId: 1, version: version({ replay_status: "running" }) }),
    ).toBe("compare");
  });

  it("Replay完了後は採否記録の段階", () => {
    expect(
      resolveStage({ sessionId: 1, version: version({ replay_status: "completed" }) }),
    ).toBe("decide");
  });

  it("promote済みは採否記録の段階", () => {
    expect(
      resolveStage({ sessionId: 1, version: version({ promoted_at: 1_800_000_000 }) }),
    ).toBe("decide");
  });

  it("画面(route)ではなく永続事実から決まる", () => {
    // 同じ Candidate Studio の URL でも、versionの状態で段階が変わる。
    const base = { componentId: "c", sessionId: 1, approved: true };
    expect(resolveStage({ ...base, version: version() })).toBe("compare");
    expect(
      resolveStage({ ...base, version: version({ replay_status: "completed" }) }),
    ).toBe("decide");
  });
});

describe("buildStages", () => {
  it("現在地より前を完了、後を未着手にする", () => {
    const stages = buildStages("compare");
    expect(stages.map(s => s.status)).toEqual([
      "done", "done", "done", "current", "upcoming",
    ]);
  });

  it("5段階すべてが一意のラベルと成果物を持つ", () => {
    expect(LOOP_STAGES).toHaveLength(5);
    const labels = LOOP_STAGES.map(id => STAGE_DEFINITIONS[id].label);
    const produces = LOOP_STAGES.map(id => STAGE_DEFINITIONS[id].produces);
    expect(new Set(labels).size).toBe(5);
    expect(new Set(produces).size).toBe(5);
  });
});

describe("loopSearchParams", () => {
  const context = {
    componentId: "summarizer",
    traceId: "t-1",
    sessionId: 4,
    replaySetId: 7,
  };

  it("Components が実際に読む名前で渡す", () => {
    // レビュー指摘 P1-6: rail は component_id を出していたが、Components 画面は
    // component を読むため、遷移しても未選択に戻っていた。
    const query = loopSearchParams("/components", context);
    expect(query).toContain("component=summarizer");
    expect(query).toContain("trace=t-1");
    expect(query).not.toContain("component_id=");
  });

  it("Candidate Studio が読む名前で渡す", () => {
    const query = loopSearchParams("/candidate-studio", context);
    expect(query).toContain("component_id=summarizer");
    expect(query).toContain("trace_id=t-1");
    expect(query).toContain("session_id=4");
    expect(query).toContain("replay_set_id=7");
  });

  it("Workbench に Replay Set を引き継ぐ", () => {
    const query = loopSearchParams("/simulation-workbench", context);
    expect(query).toContain("replay_set_id=7");
    expect(query).toContain("component_id=summarizer");
  });

  it("Experiments が読む prefill 名で渡す", () => {
    const query = loopSearchParams("/experiments", {
      ...context,
      replayRunId: 11,
      replayVariantId: 22,
    });
    expect(query).toContain("from_component=summarizer");
    expect(query).toContain("from_trace=t-1");
    expect(query).toContain("replay_run_id=11");
    expect(query).toContain("replay_variant_id=22");
  });

  it("文脈が無ければクエリを付けない", () => {
    expect(loopSearchParams("/components", {})).toBe("");
  });

  it("宛先が読まない値は付けない", () => {
    // Workbench は trace を読まないので、無意味な param を増やさない。
    expect(loopSearchParams("/simulation-workbench", context)).not.toContain("trace");
  });
});

describe("ImprovementLoopRail", () => {
  const renderRail = (props: Parameters<typeof ImprovementLoopRail>[0]) =>
    render(
      <MemoryRouter>
        <ImprovementLoopRail {...props} />
      </MemoryRouter>,
    );

  it("現在地を1つだけ示す", () => {
    renderRail({ componentId: "summarizer" });
    const rail = screen.getByTestId("improvement-loop-rail");
    expect(rail.getAttribute("data-current-stage")).toBe("create");
    const current = rail.querySelectorAll('[data-status="current"]');
    expect(current).toHaveLength(1);
  });

  it("その段階が何を作るのかを説明する", () => {
    renderRail({ componentId: "summarizer" });
    expect(screen.getByTestId("loop-stage-produces").textContent).toContain(
      "対象リポジトリは変更されません",
    );
  });

  it("採否記録の段階でも自動採用しないことを明記する", () => {
    renderRail({
      componentId: "c",
      sessionId: 1,
      version: version({ replay_status: "completed" }),
    });
    expect(screen.getByTestId("loop-stage-produces").textContent).toContain(
      "mergeやdeployは行われません",
    );
  });

  it("各段階へのリンクが宛先の名前で文脈を引き継ぐ", () => {
    renderRail({ componentId: "summarizer", traceId: "t-1" });
    // Candidate Studio 宛は component_id
    expect(screen.getByTestId("loop-stage-compare").getAttribute("href"))
      .toContain("component_id=summarizer");
    // Components 宛は component（この画面が読む名前）
    const back = screen.getByTestId("loop-stage-select").getAttribute("href") ?? "";
    expect(back).toContain("component=summarizer");
    expect(back).toContain("trace=t-1");
    expect(back).not.toContain("component_id=");
  });

  it("完了段階を色だけで示さない", () => {
    renderRail({
      componentId: "c",
      sessionId: 1,
      version: version({ replay_status: "completed" }),
    });
    expect(screen.getByTestId("loop-stage-select").textContent).toContain("完了");
  });
});
