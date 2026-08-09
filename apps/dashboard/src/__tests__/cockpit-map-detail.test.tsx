/// <reference types="vitest/globals" />
// Issue #363: 理解マップの簡略化と、選択カテゴリ詳細への追従表示。
//
// マップのカードは「番号・カテゴリ名・状態・1 行要約」だけを持ち、説明文
// (caption) と対応ヒント (hint) は詳細ペインへ移した。詳細ペインは理由・根拠
// を初期 3 件だけ出し、残りは展開式にする。
//
// テスト観点 (issue「受け入れ条件」) に対応:
//   カード初期表示の絞り込み / ヒントの移動先 / missing・review を色以外でも
//   識別できること / キーボードでのカテゴリ選択と詳細への移動 / 段階開示 /
//   実行できない修正手段が理由付きで残ること / Q&A 未取得時の保留表示
//
// 状態判定そのもの (`buildCockpitModel` など) の検証は
// `interview-cockpit.test.tsx` にある。ここは描画と操作だけを見る。

import { useState } from "react";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { vi } from "vitest";
import type {
  CurrentUnderstanding,
  GapItem,
  OpenQuestion,
  UnderstandingItem,
} from "@/api/types";
import {
  buildCockpitModel,
  type CockpitCategoryKey,
  type CockpitCategoryView,
} from "@/components/system-understanding/cockpit/model";
import { UnderstandingMap } from "@/components/system-understanding/cockpit/understanding-map";
import { CockpitDetailPanel } from "@/components/system-understanding/cockpit/detail-panel";

// ── テストデータ ──────────────────────────────────────────────────────

function ev(path: string) {
  return { path, start_line: 1, end_line: 10, summary: "" };
}

function item(name: string, overrides: Partial<UnderstandingItem> = {}): UnderstandingItem {
  return {
    name,
    summary: `${name} の説明`,
    confidence: { level: "high", reason: "" },
    evidence: [ev(`app/${name}.py`)],
    why_core: "",
    related_docs: [],
    related_apis: [],
    children: [],
    ...overrides,
  };
}

function fullUnderstanding(overrides: Partial<CurrentUnderstanding> = {}): CurrentUnderstanding {
  return {
    vision: [item("開発者が挙動を説明できる状態")],
    system_purpose: [item("実行データを収集し候補実装と比較する")],
    core_capabilities: [item("トレース収集")],
    capability_elements: [item("trace ingest")],
    supporting_elements: [item("policy store")],
    api_boundaries: [item("POST /traces")],
    probe_flow_candidates: [item("summarize フロー")],
    ...overrides,
  };
}

function openQuestion(overrides: Partial<OpenQuestion> & { question: string }): OpenQuestion {
  return { category: "general", priority: "medium", ...overrides };
}

function gap(overrides: Partial<GapItem> & { name: string }): GapItem {
  return { gap_type: "code_only", summary: "", severity: "medium", ...overrides };
}

/** Vision 未設定 / Capabilities 要確認 / 残り確認済み のモデル。 */
function mixedModel() {
  return buildCockpitModel({
    understanding: fullUnderstanding({ vision: [] }),
    gaps: [],
    openQuestions: [openQuestion({ question: "責務は独立か", category: "capability" })],
    qaItems: [],
  });
}

const CATEGORY_ORDER: CockpitCategoryKey[] = [
  "vision",
  "system_purpose",
  "capabilities",
  "api_boundaries",
  "probe_flow",
];

/** 選択状態を持つ検証用ラッパー (`selected` は本番でもページが持つ)。 */
function MapHarness({
  categories,
  onSelect,
  onGoToDetail,
}: {
  categories: CockpitCategoryView[];
  onSelect?: (key: CockpitCategoryKey) => void;
  onGoToDetail?: () => void;
}) {
  const [selected, setSelected] = useState<CockpitCategoryKey>("vision");
  return (
    <UnderstandingMap
      categories={categories}
      selected={selected}
      onSelect={key => {
        setSelected(key);
        onSelect?.(key);
      }}
      onGoToDetail={onGoToDetail}
    />
  );
}

// ── マップのカード ────────────────────────────────────────────────────

describe("UnderstandingMap のカード簡略化 (issue #363)", () => {
  it("番号・カテゴリ名・状態・1 行要約だけを出し、説明文とヒントは出さない", () => {
    const m = mixedModel();
    render(<UnderstandingMap categories={m.categories} selected="vision" onSelect={() => {}} />);

    const card = screen.getByTestId("cockpit-category-card-capabilities");
    const capabilities = m.categories.find(c => c.key === "capabilities")!;

    expect(within(card).getByText("03")).toBeTruthy();
    expect(within(card).getByText("Capabilities")).toBeTruthy();
    expect(within(card).getByTestId("cockpit-status-review")).toHaveTextContent("要確認");

    const summary = within(card).getByTestId("cockpit-category-summary-capabilities");
    expect(summary).toHaveTextContent(capabilities.summary);
    // 1 行に丸め、全文は title で読める。
    expect(summary.className).toMatch(/truncate/);
    expect(summary).toHaveAttribute("title", capabilities.summary);

    // 説明文 (caption) と対応ヒント (hint) はカードから外した。
    expect(within(card).queryByText("目的を支える主要機能と要素")).toBeNull();
    expect(capabilities.hint).toContain("責務は独立か");
    expect(within(card).queryByText(/責務は独立か/)).toBeNull();
  });

  it("ヒントは詳細ペインの「今の状況」へ移る", () => {
    const capabilities = mixedModel().categories.find(c => c.key === "capabilities")!;
    render(<CockpitDetailPanel category={capabilities} state="W3" onAction={() => {}} />);
    expect(screen.getByTestId("cockpit-detail-hint")).toHaveTextContent("責務は独立か");
    expect(screen.getByTestId("cockpit-detail-hint")).toHaveTextContent("今の状況");
  });

  it("missing / review は色以外の marker を持ち、confirmed は持たない", () => {
    const m = mixedModel();
    render(<UnderstandingMap categories={m.categories} selected="vision" onSelect={() => {}} />);

    const vision = screen.getByTestId("cockpit-category-card-vision");
    const capabilities = screen.getByTestId("cockpit-category-card-capabilities");
    const api = screen.getByTestId("cockpit-category-card-api_boundaries");

    expect(within(vision).getByTestId("cockpit-category-attention-vision")).toHaveTextContent("要対応");
    expect(within(capabilities).getByTestId("cockpit-category-attention-capabilities"))
      .toHaveTextContent("要対応");
    expect(within(api).queryByTestId("cockpit-category-attention-api_boundaries")).toBeNull();

    // 状態自体はテキスト付きバッジのまま (色だけに依存しない)。
    expect(within(vision).getByTestId("cockpit-status-missing")).toHaveTextContent("未設定");
    expect(within(api).getByTestId("cockpit-status-confirmed")).toHaveTextContent("確認済み");
  });

  it("aria-label にカテゴリ名と状態テキストを含める", () => {
    const m = mixedModel();
    render(<UnderstandingMap categories={m.categories} selected="vision" onSelect={() => {}} />);
    expect(screen.getByTestId("cockpit-category-card-vision"))
      .toHaveAccessibleName("01 Vision: 未設定 (要対応)");
    expect(screen.getByTestId("cockpit-category-card-api_boundaries"))
      .toHaveAccessibleName("04 API boundaries: 確認済み");
  });

  it("Q&A 未取得のカードは状態バッジではなく保留表示になる", () => {
    const m = buildCockpitModel({
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [gap({ name: "GET /unknown", gap_type: "unclassified_entrypoint" })],
      openQuestions: [],
      qaItems: undefined,
      qaFetchStatus: "unavailable",
    });
    render(
      <UnderstandingMap
        categories={m.categories}
        selected="vision"
        qaFetchStatus="unavailable"
        onSelect={() => {}}
        onRetryQa={() => {}}
      />,
    );
    const purpose = screen.getByTestId("cockpit-category-card-system_purpose");
    expect(within(purpose).getByTestId("cockpit-status-pending"))
      .toHaveTextContent("Q&A 未取得のため保留");
    expect(within(purpose).queryByTestId("cockpit-status-confirmed")).toBeNull();
    // 保留は「要対応」でもない (状態を確定できていないだけ)。
    expect(within(purpose).queryByTestId("cockpit-category-attention-system_purpose")).toBeNull();
    // session 詳細だけで確定できるものは、そのまま marker と 3 状態が出る。
    expect(
      within(screen.getByTestId("cockpit-category-card-api_boundaries"))
        .getByTestId("cockpit-category-attention-api_boundaries"),
    ).toBeTruthy();
    expect(screen.getByTestId("cockpit-map-qa-unavailable")).toBeTruthy();
  });
});

// ── キーボード操作 ────────────────────────────────────────────────────

describe("UnderstandingMap のキーボード操作 (issue #363)", () => {
  function cards() {
    return CATEGORY_ORDER.map(key => screen.getByTestId(`cockpit-category-card-${key}`));
  }

  it("矢印キーでフォーカスが 5 枚を移動し、Home / End で端へ飛ぶ", () => {
    const m = mixedModel();
    render(<MapHarness categories={m.categories} />);
    const [vision, purpose, capabilities, , probeFlow] = cards();

    vision.focus();
    fireEvent.keyDown(vision, { key: "ArrowRight" });
    expect(document.activeElement).toBe(purpose);

    fireEvent.keyDown(purpose, { key: "ArrowDown" });
    expect(document.activeElement).toBe(capabilities);

    fireEvent.keyDown(capabilities, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(purpose);

    fireEvent.keyDown(purpose, { key: "ArrowUp" });
    expect(document.activeElement).toBe(vision);

    fireEvent.keyDown(vision, { key: "End" });
    expect(document.activeElement).toBe(probeFlow);

    fireEvent.keyDown(probeFlow, { key: "Home" });
    expect(document.activeElement).toBe(vision);

    // 端では折り返す (行き止まりを作らない)。
    fireEvent.keyDown(vision, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(probeFlow);
    fireEvent.keyDown(probeFlow, { key: "ArrowRight" });
    expect(document.activeElement).toBe(vision);
  });

  it("矢印キーはフォーカスを動かすだけで、選択は変えない", () => {
    const onSelect = vi.fn();
    const m = mixedModel();
    render(<MapHarness categories={m.categories} onSelect={onSelect} />);
    const [vision, purpose] = cards();

    vision.focus();
    fireEvent.keyDown(vision, { key: "ArrowRight" });
    expect(document.activeElement).toBe(purpose);
    expect(onSelect).not.toHaveBeenCalled();
    expect(vision).toHaveAttribute("aria-pressed", "true");
    expect(purpose).toHaveAttribute("aria-pressed", "false");
  });

  it("Enter / Space は native button の選択として届き、aria-pressed が入れ替わる", () => {
    const onSelect = vi.fn();
    const m = mixedModel();
    render(<MapHarness categories={m.categories} onSelect={onSelect} />);
    const [vision, , capabilities] = cards();

    capabilities.focus();
    // Enter / Space をここで握りつぶさない (既定動作 = click を妨げない)。
    expect(fireEvent.keyDown(capabilities, { key: "Enter" })).toBe(true);
    expect(fireEvent.keyDown(capabilities, { key: " " })).toBe(true);
    expect(document.activeElement).toBe(capabilities);

    // jsdom は keydown から click を合成しないので、native の活性化を click で表す。
    fireEvent.click(capabilities);
    expect(onSelect).toHaveBeenCalledWith("capabilities");
    expect(capabilities).toHaveAttribute("aria-pressed", "true");
    expect(vision).toHaveAttribute("aria-pressed", "false");
  });
});

// ── 詳細ペインへの導線 ────────────────────────────────────────────────

describe("選択カテゴリ詳細への導線 (issue #363)", () => {
  it("onGoToDetail を渡すと詳細への移動ボタンを出し、押すと呼ばれる", () => {
    const onGoToDetail = vi.fn();
    const m = mixedModel();
    render(<MapHarness categories={m.categories} onGoToDetail={onGoToDetail} />);
    const goDetail = screen.getByTestId("cockpit-map-go-detail");
    expect(goDetail).toHaveTextContent("選択中のカテゴリの詳細へ");
    fireEvent.click(goDetail);
    expect(onGoToDetail).toHaveBeenCalledTimes(1);
  });

  it("onGoToDetail が無ければボタンを出さない (カード 5 枚だけ)", () => {
    const m = mixedModel();
    render(<UnderstandingMap categories={m.categories} selected="vision" onSelect={() => {}} />);
    expect(screen.queryByTestId("cockpit-map-go-detail")).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(5);
  });
});

// ── 詳細ペインの段階開示 ──────────────────────────────────────────────

/** 理由 5 件 (gap 2 + 質問 3)・根拠 6 件 (コード 4 + doc 2) の Capabilities。 */
function crowdedCapabilities(): CockpitCategoryView {
  const m = buildCockpitModel({
    understanding: fullUnderstanding({
      core_capabilities: [
        item("トレース収集", {
          evidence: [ev("app/a.py"), ev("app/b.py"), ev("app/c.py"), ev("app/d.py")],
          related_docs: ["docs/a.md", "docs/b.md"],
        }),
      ],
      capability_elements: [],
      supporting_elements: [],
    }),
    gaps: [
      gap({ name: "トレース収集", gap_type: "stale_explanation", summary: "説明が古い" }),
      gap({ name: "未分類の要素", gap_type: "code_only", summary: "所属が不明" }),
    ],
    openQuestions: [
      openQuestion({ question: "質問 1", category: "capability" }),
      openQuestion({ question: "質問 2", category: "capability" }),
      openQuestion({ question: "質問 3", category: "capability" }),
    ],
    qaItems: [],
  });
  return m.categories.find(c => c.key === "capabilities")!;
}

describe("CockpitDetailPanel の段階開示 (issue #363)", () => {
  it("確認が必要な理由は 3 件だけ出し、残りを展開できる", () => {
    const category = crowdedCapabilities();
    expect(category.gaps).toHaveLength(2);
    expect(category.questions).toHaveLength(3);

    render(<CockpitDetailPanel category={category} state="W3" onAction={() => {}} />);
    const reasons = screen.getByTestId("cockpit-detail-reasons");
    expect(within(reasons).getAllByTestId("cockpit-detail-gap")).toHaveLength(2);
    expect(within(reasons).getAllByTestId("cockpit-detail-question")).toHaveLength(1);

    const toggle = screen.getByTestId("cockpit-detail-reasons-toggle");
    // 5 件のうち 3 件を出しているので、残りは 2 件。
    expect(toggle).toHaveTextContent("残り 2 件の理由を表示");
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    toggle.focus();
    fireEvent.click(toggle);
    expect(within(reasons).getAllByTestId("cockpit-detail-gap")).toHaveLength(2);
    expect(within(reasons).getAllByTestId("cockpit-detail-question")).toHaveLength(3);
    expect(toggle).toHaveTextContent("表示を戻す");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    // 展開してもフォーカスはトグルに残る (キーボードで続きを読める)。
    expect(document.activeElement).toBe(toggle);

    fireEvent.click(toggle);
    expect(within(reasons).getAllByTestId("cockpit-detail-question")).toHaveLength(1);
    expect(document.activeElement).toBe(toggle);
  });

  it("根拠は 3 件だけ出し、全件を展開できる", () => {
    const category = crowdedCapabilities();
    expect(category.evidence).toHaveLength(4);
    expect(category.relatedDocs).toHaveLength(2);

    render(<CockpitDetailPanel category={category} state="W3" onAction={() => {}} />);
    const evidence = screen.getByTestId("cockpit-detail-evidence");
    expect(within(evidence).getAllByRole("listitem")).toHaveLength(3);
    expect(evidence).toHaveTextContent("app/a.py");
    expect(evidence).not.toHaveTextContent("app/d.py");
    expect(evidence).not.toHaveTextContent("docs/a.md");

    const toggle = screen.getByTestId("cockpit-detail-evidence-toggle");
    // コード 4 件 + ドキュメント 2 件。
    expect(toggle).toHaveTextContent("根拠をすべて表示 (6 件)");

    toggle.focus();
    fireEvent.click(toggle);
    expect(within(evidence).getAllByRole("listitem")).toHaveLength(6);
    expect(evidence).toHaveTextContent("app/d.py");
    expect(evidence).toHaveTextContent("docs/b.md");
    expect(toggle).toHaveTextContent("表示を戻す");
    expect(document.activeElement).toBe(toggle);

    fireEvent.click(toggle);
    expect(within(evidence).getAllByRole("listitem")).toHaveLength(3);
    expect(document.activeElement).toBe(toggle);
  });

  it("3 件以下ならトグルを出さない", () => {
    const category = mixedModel().categories.find(c => c.key === "system_purpose")!;
    render(<CockpitDetailPanel category={category} state="W3" onAction={() => {}} />);
    expect(screen.queryByTestId("cockpit-detail-reasons-toggle")).toBeNull();
    expect(screen.queryByTestId("cockpit-detail-evidence-toggle")).toBeNull();
  });

  it("実行できない修正手段は理由付きで残す (消さない)", () => {
    const category = mixedModel().categories.find(c => c.key === "capabilities")!;
    render(<CockpitDetailPanel category={category} state="W6" onAction={() => {}} />);
    const actions = screen.getByTestId("cockpit-detail-actions");
    const edit = within(actions).getByTestId("cockpit-action-direct_edit");
    expect(edit).toBeDisabled();
    expect(edit).toHaveTextContent("直接編集は理解・質問・ズレの確認中だけ行えます");
    const answer = within(actions).getByTestId("cockpit-action-answer_question");
    expect(answer).toBeDisabled();
    expect(answer).toHaveTextContent("質問への回答は");
  });

  it("Q&A 未取得のカテゴリは詳細ペインでも保留表示のままにする", () => {
    const m = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: undefined,
      qaFetchStatus: "unavailable",
    });
    const purpose = m.categories.find(c => c.key === "system_purpose")!;
    render(<CockpitDetailPanel category={purpose} state="W3" onAction={() => {}} />);
    const panel = screen.getByTestId("cockpit-detail-panel");
    expect(within(panel).getByTestId("cockpit-status-pending"))
      .toHaveTextContent("Q&A 未取得のため保留");
    expect(within(panel).queryByTestId("cockpit-status-confirmed")).toBeNull();
    expect(screen.getByTestId("cockpit-detail-hint")).toHaveTextContent("Q&A を取得できていない");
  });
});
