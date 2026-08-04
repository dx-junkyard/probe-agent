/// <reference types="vitest/globals" />
// Issue #351 / #352 / #353 / #354: Understanding Brief と Decision Readiness の
// 表示テスト。props だけで完結する表示コンポーネントなので API/Router モックは
// 不要 -- 判定はすべてサーバー側で、ここで検証するのは「サーバーが決めた結論を
// 取りこぼさずに、混同しない形で描けているか」だけ。

import { render, screen, fireEvent, within } from "@testing-library/react";
import {
  BRIEF_DISPLAY_BY_STATE,
  UnderstandingBriefPanel,
} from "@/components/system-understanding/understanding-brief";
import type {
  UnderstandingBriefClaimOut,
  UnderstandingBriefOut,
} from "@/api/types";

function claim(
  overrides: Partial<UnderstandingBriefClaimOut> & { name: string },
): UnderstandingBriefClaimOut {
  return {
    kind: "core_capability",
    summary: "",
    confirmation: "ai_hypothesis",
    provenance: "ai_hypothesis",
    confirmation_label: "AI 仮説・未確認",
    provenance_label: "AI の解釈・仮説",
    reason: "",
    contribution: "",
    is_mock: false,
    evidence: [],
    related_docs: [],
    related_apis: [],
    ...overrides,
  };
}

function brief(overrides: Partial<UnderstandingBriefOut> = {}): UnderstandingBriefOut {
  return {
    system_id: 1,
    session_id: 7,
    built: true,
    vision: null,
    vision_missing_information: [],
    system_purpose: [],
    core_capabilities: [],
    core_capability_initial_count: 0,
    key_unconfirmed: [],
    detail_counts: {},
    readiness_state: "needs_confirmation",
    readiness_label: "確認が必要です",
    readiness_description: "判断の対象はありますが、あなたの確認が必要な内容が残っています。",
    readiness_reasons: [],
    changes_since_confirmation: [],
    confirmed_at: null,
    confirmed_revision_id: null,
    revision_id: 3,
    snapshot_id: 42,
    ...overrides,
  };
}

const fullBrief = brief({
  vision: claim({
    kind: "vision",
    name: "開発者が自分のシステムを説明できる状態にする",
    confirmation: "confirmed",
    provenance: "developer_intent",
    confirmation_label: "確認済み",
    provenance_label: "開発者が明示した意図",
  }),
  system_purpose: [
    claim({
      kind: "system_purpose",
      name: "実行時の挙動を観測可能にする",
      confirmation: "ai_hypothesis",
      provenance: "implementation_fact",
      provenance_label: "コード・ドキュメントの実装事実",
      evidence: [{ path: "app/main.py", start_line: 1, end_line: 10, summary: "起動" }],
    }),
  ],
  core_capabilities: [
    claim({ name: "Trace 収集" }),
    claim({
      name: "Shadow 比較",
      confirmation: "conflicting",
      confirmation_label: "矛盾あり",
    }),
  ],
  core_capability_initial_count: 2,
  key_unconfirmed: [
    claim({ name: "Shadow 比較", confirmation: "conflicting", confirmation_label: "矛盾あり" }),
  ],
  detail_counts: { api_boundaries: 3, capability_elements: 7 },
  readiness_reasons: [
    {
      code: "claim_conflict",
      severity: "blocking",
      message: "「Shadow 比較」の解釈に矛盾があります。",
      target_kind: "claim",
      target_name: "Shadow 比較",
    },
    {
      code: "vision_unconfirmed",
      severity: "informational",
      message: "Vision は未確認です。",
      target_kind: "claim",
      target_name: "vision",
    },
  ],
});

describe("UnderstandingBriefPanel", () => {
  test("W2 では Vision・Purpose・主要機能を区別して全面表示する", () => {
    render(<UnderstandingBriefPanel brief={fullBrief} state="W2" />);

    const panel = screen.getByTestId("understanding-brief");
    expect(panel).toHaveAttribute("data-display", "full");
    // 3 つの主張が別々の見出しで並ぶ (混ぜない)。
    expect(screen.getByText(/Vision — 誰の状態を/)).toBeInTheDocument();
    expect(screen.getByText(/System Purpose — Vision に対して/)).toBeInTheDocument();
    expect(screen.getByText(/主要機能 — Purpose を支える/)).toBeInTheDocument();
    expect(screen.getByTestId("brief-claim-vision")).toHaveTextContent(
      "開発者が自分のシステムを説明できる状態にする",
    );
    expect(screen.getByTestId("brief-claim-capability-0")).toHaveTextContent("Trace 収集");
  });

  test("確認状態と出所を別々の軸として示す", () => {
    render(<UnderstandingBriefPanel brief={fullBrief} state="W2" />);

    // 開発者が書いた Vision: 確認済み × 開発者の意図。
    const vision = screen.getByTestId("brief-claim-vision");
    expect(vision).toHaveAttribute("data-confirmation", "confirmed");
    expect(vision).toHaveAttribute("data-provenance", "developer_intent");
    expect(within(vision).getByText("確認済み")).toBeInTheDocument();
    expect(within(vision).getByText("開発者が明示した意図")).toBeInTheDocument();

    // AI が読み取った Purpose: 未確認 × 実装事実。「確認済み」に混ぜない。
    const purpose = screen.getByTestId("brief-claim-purpose-0");
    expect(purpose).toHaveAttribute("data-confirmation", "ai_hypothesis");
    expect(purpose).toHaveAttribute("data-provenance", "implementation_fact");
    expect(within(purpose).queryByText("確認済み")).toBeNull();
  });

  test("進行可否は結論と理由を同じ表示領域に出し、種別を色以外でも示す", () => {
    render(<UnderstandingBriefPanel brief={fullBrief} state="W2" />);

    const readiness = screen.getByTestId("readiness-summary");
    expect(readiness).toHaveAttribute("data-readiness-state", "needs_confirmation");
    expect(readiness).toHaveTextContent("確認が必要です");
    // ブロッキングと参考情報が区別できる。
    const blocking = screen.getByTestId("readiness-reason-claim_conflict");
    expect(blocking).toHaveAttribute("data-severity", "blocking");
    expect(blocking).toHaveTextContent("進行不可の理由");
    expect(blocking).toHaveTextContent("「Shadow 比較」の解釈に矛盾があります。");
    expect(screen.getByTestId("readiness-reason-vision_unconfirmed")).toHaveAttribute(
      "data-severity",
      "informational",
    );
    // 合成された信頼度パーセントは出さない。
    expect(readiness.textContent).not.toMatch(/\d+\s*%/);
  });

  test("根拠と全文ツリーは操作するまで初期表示を占有しない", () => {
    render(
      <UnderstandingBriefPanel
        brief={fullBrief}
        state="W2"
        fullTree={<div data-testid="full-tree">全文ツリー</div>}
      />,
    );

    expect(screen.queryByTestId("full-tree")).toBeNull();
    expect(screen.queryByText(/app\/main\.py/)).toBeNull();

    fireEvent.click(screen.getByTestId("brief-toggle-brief-details"));
    expect(screen.getByTestId("full-tree")).toBeInTheDocument();
    expect(screen.getByTestId("brief-detail-counts")).toHaveTextContent("API 境界: 3 件");

    fireEvent.click(screen.getByTestId("brief-toggle-brief-claim-purpose-0"));
    expect(screen.getByText(/app\/main\.py:1-10/)).toBeInTheDocument();
  });

  test("Vision を推定できない場合は、その事実と不足している情報を示す", () => {
    render(
      <UnderstandingBriefPanel
        brief={brief({
          built: true,
          vision: null,
          vision_missing_information: ["このシステムで実現したい状態・価値", "成功をどう判断するか"],
          system_purpose: [claim({ kind: "system_purpose", name: "P" })],
        })}
        state="W2"
      />,
    );

    const missing = screen.getByTestId("brief-vision-missing");
    expect(missing).toHaveTextContent("Vision は推定できていません");
    expect(missing).toHaveTextContent("このシステムで実現したい状態・価値");
    expect(missing).toHaveTextContent("成功をどう判断するか");
  });

  test("W3〜W7 はコンパクト表示で、展開すると同じ Brief に到達する", () => {
    render(<UnderstandingBriefPanel brief={fullBrief} state="W5" />);

    const panel = screen.getByTestId("understanding-brief");
    expect(panel).toHaveAttribute("data-display", "compact");
    // 縮小しても Vision / Purpose / Readiness は読める。
    expect(screen.getByTestId("brief-compact-vision")).toHaveTextContent(
      "開発者が自分のシステムを説明できる状態にする",
    );
    expect(screen.getByTestId("brief-compact-purpose")).toHaveTextContent(
      "実行時の挙動を観測可能にする",
    );
    expect(screen.getByTestId("readiness-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("brief-claim-vision")).toBeNull();

    const toggle = screen.getByTestId("brief-expand-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("brief-claim-vision")).toBeInTheDocument();
    expect(screen.getByTestId("brief-key-unconfirmed")).toBeInTheDocument();
  });

  test("重要な変更は表示量を縮小した状態でも見落とされない", () => {
    const changed = brief({
      readiness_state: "recheck_required",
      readiness_label: "再確認が必要です",
      changes_since_confirmation: [
        {
          change_kind: "added",
          section: "core_capabilities",
          section_label: "主要機能",
          name: "新しい機能",
          detail: "主要機能に追加されました。",
        },
      ],
    });
    render(<UnderstandingBriefPanel brief={changed} state="W5" />);

    // 折りたたみの外に出る -- 展開しなくても見える。
    const notice = screen.getByTestId("brief-changes-since-confirmation");
    expect(notice).toHaveTextContent("前回の確認後に変わった内容 (1 件)");
    expect(notice).toHaveTextContent("主要機能「新しい機能」");
  });

  test("W1 は骨格だけを出し、確定できるようには見せない", () => {
    render(
      <UnderstandingBriefPanel
        brief={brief({ readiness_state: "building", readiness_label: "システムが理解を作成しています" })}
        state="W1"
        primaryAction={<button data-testid="confirm-understanding">この理解で進む</button>}
      />,
    );

    expect(screen.getByTestId("understanding-brief")).toHaveAttribute("data-display", "building");
    expect(screen.queryByTestId("confirm-understanding")).toBeNull();
    expect(screen.getByTestId("brief-building-revision-note")).toHaveTextContent("更新前の理解");
  });

  test("構築前は架空の Vision / Purpose を表示しない", () => {
    render(<UnderstandingBriefPanel brief={brief({ built: false })} state="W0-B" />);

    expect(screen.getByTestId("brief-empty-note")).toHaveTextContent(
      "まだ理解を構築していません",
    );
    expect(screen.queryByTestId("brief-claim-vision")).toBeNull();
    expect(screen.queryByTestId("readiness-summary")).toBeNull();
  });

  test("要約を取得できなくても、主操作と全文ツリーは失われない", () => {
    render(
      <UnderstandingBriefPanel
        brief={undefined}
        state="W2"
        primaryAction={<button data-testid="confirm-understanding">この理解で進む</button>}
        fullTree={<div data-testid="full-tree">全文ツリー</div>}
      />,
    );

    expect(screen.getByTestId("understanding-brief")).toHaveAttribute(
      "data-display",
      "unavailable",
    );
    expect(screen.getByTestId("brief-unavailable-note")).toBeInTheDocument();
    expect(screen.getByTestId("confirm-understanding")).toBeInTheDocument();
    expect(screen.getByTestId("full-tree")).toBeInTheDocument();
  });

  test("表示密度はワークフロー状態ごとに 1 つだけ決まる", () => {
    // 状態の判定はサーバー。ここで固定するのは「どれだけ広げるか」の対応表。
    expect(BRIEF_DISPLAY_BY_STATE).toEqual({
      "W0-A": "empty",
      "W0-B": "empty",
      W1: "building",
      W2: "full",
      W3: "compact",
      W4: "compact",
      W5: "compact",
      W6: "compact",
      W7: "compact",
    });
  });
});

describe("UnderstandingBriefPanel の読み込み中・ゼロベース", () => {
  test("ワークフロー状態が未取得のあいだは何も主張しない", () => {
    const { container } = render(
      <UnderstandingBriefPanel brief={fullBrief} state={null} />,
    );
    // 「まだ理解を構築していません」を一瞬でも出すのは誤った事実になる。
    expect(container).toBeEmptyDOMElement();
  });

  test("ゼロベース (構造化された理解が無い) でも進行可否だけは示す", () => {
    render(
      <UnderstandingBriefPanel
        brief={brief({
          built: false,
          readiness_state: "not_built",
          readiness_label: "まだ判断できません",
        })}
        state="W2"
      />,
    );

    expect(screen.getByTestId("brief-empty-note")).toHaveTextContent(
      "構造化されたシステム理解はまだありません",
    );
    expect(screen.getByTestId("readiness-summary")).toBeInTheDocument();
    expect(screen.queryByTestId("brief-claim-vision")).toBeNull();
  });
});

test("モック LLM 由来の主張は隠さずに明示する", () => {
  render(
    <UnderstandingBriefPanel
      brief={brief({
        vision: claim({
          kind: "vision",
          name: "モック提案の Vision",
          is_mock: true,
        }),
        system_purpose: [claim({ kind: "system_purpose", name: "P" })],
      })}
      state="W2"
    />,
  );

  const vision = screen.getByTestId("brief-claim-vision");
  expect(within(vision).getByTestId("brief-claim-mock")).toHaveTextContent("モック");
  // 実データ由来の主張には付かない。
  expect(
    within(screen.getByTestId("brief-claim-purpose-0")).queryByTestId("brief-claim-mock"),
  ).toBeNull();
});

describe("W1 の見出しは Readiness に従う", () => {
  test("理解を作り直しているときだけ「理解を更新しています」と言う", () => {
    render(
      <UnderstandingBriefPanel
        brief={brief({
          readiness_state: "building",
          readiness_label: "システムが理解を作成しています",
        })}
        state="W1"
      />,
    );
    expect(screen.getByText("現在のシステム理解を更新しています")).toBeInTheDocument();
    expect(screen.getByTestId("brief-building-revision-note")).toBeInTheDocument();
  });

  test("提案生成など理解を変えない処理では、理解が変わらないことを示す", () => {
    // `W1` は「システムが何かを実行中」であって「理解を作り直し中」とは限ら
    // ない。サーバーの Readiness が building でないなら、理解は変わらない。
    render(
      <UnderstandingBriefPanel
        brief={brief({
          readiness_state: "ready",
          readiness_label: "この理解で進めます",
          system_purpose: [claim({ kind: "system_purpose", name: "P" })],
        })}
        state="W1"
      />,
    );
    expect(screen.getByText("システムが処理を実行しています")).toBeInTheDocument();
    expect(screen.queryByText("現在のシステム理解を更新しています")).toBeNull();
    expect(screen.queryByTestId("brief-building-revision-note")).toBeNull();
    // 変わらない理解の要点は読める。確定操作は出さない。
    expect(screen.getByTestId("brief-compact-purpose")).toHaveTextContent("P");
  });
});

test("初回構築では「構築しています」、再構築では「更新しています」と言い分ける", () => {
  const { unmount } = render(
    <UnderstandingBriefPanel
      brief={brief({ built: false, readiness_state: "building", readiness_label: "作成中" })}
      state="W1"
    />,
  );
  expect(screen.getByText("システム理解を構築しています")).toBeInTheDocument();
  unmount();

  render(
    <UnderstandingBriefPanel
      brief={brief({ built: true, readiness_state: "building", readiness_label: "作成中" })}
      state="W1"
    />,
  );
  expect(screen.getByText("現在のシステム理解を更新しています")).toBeInTheDocument();
});
