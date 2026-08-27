/// <reference types="vitest/globals" />
// Issue #283: 「現在の理解」の平易な段階表示 (UnderstandingOverview) のテスト。
// このコンポーネントは props のみで完結する表示コンポーネントなので、
// dashboard-contracts.test.tsx のような API / Router モックは不要。

import { render, screen, fireEvent, within } from "@testing-library/react";
import { UnderstandingOverview } from "@/components/system-understanding/understanding-overview";
import type { CurrentUnderstanding, GapItem, OpenQuestion, UnderstandingItem } from "@/api/types";

function item(overrides: Partial<UnderstandingItem> & { name: string }): UnderstandingItem {
  return {
    summary: `${overrides.name} の説明`,
    confidence: { level: "confirmed", reason: "" },
    evidence: [],
    why_core: "",
    related_docs: [],
    related_apis: [],
    children: [],
    ...overrides,
  };
}

function baseUnderstanding(overrides: Partial<CurrentUnderstanding> = {}): CurrentUnderstanding {
  return {
    system_purpose: [],
    core_capabilities: [],
    capability_elements: [],
    supporting_elements: [],
    api_boundaries: [],
    probe_flow_candidates: [],
    ...overrides,
  };
}

const confirmedItem = item({
  name: "Purpose A",
  summary: "確認済みの目的の説明。",
  confidence: { level: "confirmed", reason: "ドキュメントに明記されている" },
  evidence: [{ path: "a.py", start_line: 1, end_line: 5, summary: "エントリポイント" }],
});

const uncertainItem = item({
  name: "Capability B",
  summary: "未確認の機能の説明。",
  confidence: { level: "uncertain", reason: "コードとドキュメントが食い違う" },
  evidence: [{ path: "b.py", start_line: 10, end_line: 20, summary: "実装箇所" }],
});

const gap: GapItem = {
  gap_type: "docs_only",
  name: "Gap A",
  summary: "ドキュメントにしか記載がない機能",
  severity: "high",
};

const openQuestion: OpenQuestion = {
  question: "認証はどの層で行いますか?",
  category: "purpose",
  priority: "medium",
  hypothesis: "API Gateway 層で行っている",
  evidence_refs: [{ path: "c.py", start_line: 3, end_line: 9 }],
  answer_options: ["はい", "いいえ"],
};

describe("UnderstandingOverview", () => {
  test("initial view shows the three groups and hides evidence/audit details until expanded", () => {
    render(
      <UnderstandingOverview
        understanding={baseUnderstanding({ system_purpose: [confirmedItem], core_capabilities: [uncertainItem] })}
        gaps={[gap]}
        openQuestions={[openQuestion]}
        nextAction="次のアクションを実行してください。"
      />,
    );

    expect(screen.getByTestId("overview-group-known")).toBeInTheDocument();
    expect(screen.getByTestId("overview-group-confirm")).toBeInTheDocument();
    expect(screen.getByTestId("overview-group-next")).toBeInTheDocument();

    // 分かったこと / 確認したいこと の見出しと claim が見えている
    expect(within(screen.getByTestId("overview-group-known")).getByText("Purpose A")).toBeInTheDocument();
    expect(within(screen.getByTestId("overview-group-confirm")).getByText("Capability B")).toBeInTheDocument();
    expect(within(screen.getByTestId("overview-group-confirm")).getByText("Gap A")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("overview-group-confirm")).getByText("認証はどの層で行いますか?"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("overview-group-next")).getByText("次のアクションを実行してください。"),
    ).toBeInTheDocument();

    // 根拠・確信度の理由・schema上の詳細情報は初期表示では見えない
    expect(screen.queryByText(/ドキュメントに明記されている/)).not.toBeInTheDocument();
    expect(screen.queryByText(/コードとドキュメントが食い違う/)).not.toBeInTheDocument();
    expect(screen.queryByText(/a\.py:1-5/)).not.toBeInTheDocument();
    expect(screen.queryByText(/b\.py:10-20/)).not.toBeInTheDocument();
    expect(screen.queryByText(/c\.py:3-9/)).not.toBeInTheDocument();
    expect(screen.queryByText(/API Gateway 層で行っている/)).not.toBeInTheDocument();

    // トグルボタンはあるが、閉じている
    const toggles = screen.getAllByText("根拠を見る");
    expect(toggles.length).toBeGreaterThan(0);
    toggles.forEach(t => expect(t).toHaveAttribute("aria-expanded", "false"));
  });

  test("status label mapping renders Japanese labels and never renders raw enum values", () => {
    const { container } = render(
      <UnderstandingOverview
        understanding={baseUnderstanding({ core_capabilities: [uncertainItem] })}
        gaps={[gap]}
        openQuestions={[openQuestion]}
      />,
    );

    // uncertain → 未確認 (日本語ラベル) が見える
    expect(screen.getByText("未確認")).toBeInTheDocument();
    // 重大 (severity=high の日本語ラベル)
    expect(screen.getByText("重大")).toBeInTheDocument();
    // 優先度: 中 (priority=medium の日本語ラベル)
    expect(screen.getByText("優先度: 中")).toBeInTheDocument();

    // 生の enum 文字列 ("uncertain" など) は可視テキストに出ない
    expect(container.textContent).not.toMatch(/\buncertain\b/);
    expect(container.textContent).not.toMatch(/\bconflicting\b/);
    expect(container.textContent).not.toMatch(/\bdocs_only\b/);
  });

  test("unknown confidence level falls back to a Japanese label, never the raw value", () => {
    const weird = item({
      name: "Weird item",
      confidence: { level: "totally_unexpected_value", reason: "" },
    });
    const { container } = render(
      <UnderstandingOverview understanding={baseUnderstanding({ system_purpose: [weird] })} />,
    );
    expect(container.textContent).not.toMatch(/totally_unexpected_value/);
    // 未知の値は安全側 (要確認寄り) の既定ラベルへ
    expect(screen.getByText("未確認")).toBeInTheDocument();
  });

  test("evidence expansion toggle works", () => {
    render(
      <UnderstandingOverview
        understanding={baseUnderstanding({ core_capabilities: [uncertainItem] })}
      />,
    );

    expect(screen.queryByText(/b\.py:10-20/)).not.toBeInTheDocument();
    const toggle = screen.getByText("根拠を見る");
    fireEvent.click(toggle);

    expect(screen.getByText(/b\.py:10-20/)).toBeInTheDocument();
    expect(screen.getByText(/コードとドキュメントが食い違う/)).toBeInTheDocument();
    expect(screen.getByText("根拠を隠す")).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByText("根拠を隠す"));
    expect(screen.queryByText(/b\.py:10-20/)).not.toBeInTheDocument();
  });

  test("empty state (no current_understanding) renders without crashing and shows guidance", () => {
    render(<UnderstandingOverview understanding={null} gaps={null} openQuestions={null} />);

    expect(screen.getByTestId("understanding-overview-empty")).toBeInTheDocument();
    expect(screen.getByText(/理解を構築/)).toBeInTheDocument();
    expect(screen.queryByTestId("overview-group-known")).not.toBeInTheDocument();
    expect(screen.queryByTestId("overview-group-confirm")).not.toBeInTheDocument();
  });

  test("empty state also renders guidance when understanding is undefined and no gaps/questions exist", () => {
    render(<UnderstandingOverview understanding={undefined} />);
    expect(screen.getByTestId("understanding-overview-empty")).toBeInTheDocument();
  });

  test("action-required vs informational is distinguishable both visually and for screen readers", () => {
    render(
      <UnderstandingOverview
        understanding={baseUnderstanding({ system_purpose: [confirmedItem], core_capabilities: [uncertainItem] })}
        gaps={[gap]}
        openQuestions={[openQuestion]}
      />,
    );

    // 分かったこと (confirmed) は情報提供のみ: data-action-required=false
    const knownCard = screen.getByTestId("overview-item-system_purpose-0");
    expect(knownCard).toHaveAttribute("data-action-required", "false");
    expect(within(knownCard).getByText("参考情報:")).toHaveClass("sr-only");

    // 確認したいこと (uncertain / gap / question) は要対応: data-action-required=true
    const uncertainCard = screen.getByTestId("overview-item-core_capabilities-0");
    expect(uncertainCard).toHaveAttribute("data-action-required", "true");
    expect(within(uncertainCard).getByText("要確認:")).toHaveClass("sr-only");

    const gapCard = screen.getByTestId("overview-item-gap-0");
    expect(gapCard).toHaveAttribute("data-action-required", "true");

    const questionCard = screen.getByTestId("overview-item-question-0");
    expect(questionCard).toHaveAttribute("data-action-required", "true");
  });

  test("does not present unchanged/informational items as action-required cards", () => {
    render(
      <UnderstandingOverview
        understanding={baseUnderstanding({ system_purpose: [confirmedItem] })}
      />,
    );
    const knownGroup = screen.getByTestId("overview-group-known");
    // カードのコンテナのみを対象にする (トグル/バッジ/詳細ブロックの
    // testid は overview-item-toggle-... / overview-item-badge-... /
    // overview-item-detail-... という別プレフィックスなので除外される)。
    const itemCards = within(knownGroup).queryAllByTestId(/^overview-item-[a-z0-9_]+-\d+$/);
    expect(itemCards.length).toBeGreaterThan(0);
    itemCards.forEach(card => {
      expect(card).toHaveAttribute("data-action-required", "false");
    });
  });

  test("category detail toggle is collapsed by default and reveals the full categorized list on demand", () => {
    render(
      <UnderstandingOverview
        understanding={baseUnderstanding({ system_purpose: [confirmedItem] })}
      />,
    );
    expect(screen.queryByTestId("understanding-overview-detail")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("toggle-understanding-detail"));
    expect(screen.getByTestId("understanding-overview-detail")).toBeInTheDocument();
  });
});
