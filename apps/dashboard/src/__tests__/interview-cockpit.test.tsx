/// <reference types="vitest/globals" />
// Issue #356: インタビュー・コックピットの集約ロジックと主要 UI 操作のテスト。
//
// 状態集約 (`buildCockpitModel` / `completionPercent` / `qaProgress` /
// `categoryActions`) は React にも API にも依存しない純粋関数なので、そのまま
// 呼んで検証する。表示側は props だけで完結するコンポーネントなので、Router や
// API のモックは不要。
//
// テスト観点 (issue「テスト観点」) に対応:
//   全カテゴリ確認済み / Vision のみ未設定 / 複数カテゴリが要確認 /
//   Q&A が 0 件 / 未解決質問が高・中・低で混在 / 旧レスポンスに `vision`
//   キーが無い / セッション構築中 (理解が null) / カード選択とキーボード操作 /
//   CTA から対象 UI へのフォーカス移動

import { render, screen, fireEvent, within } from "@testing-library/react";
import { vi } from "vitest";
import type {
  CurrentUnderstanding,
  GapItem,
  InterviewQaOut,
  OpenQuestion,
  UnderstandingItem,
} from "@/api/types";
import {
  buildCockpitModel,
  categoryActions,
  completionPercent,
  qaProgress,
  unresolvedTargets,
  type CockpitCategoryView,
} from "@/components/system-understanding/cockpit/model";
import {
  focusCockpitTarget,
  focusFirstCockpitTarget,
} from "@/components/system-understanding/cockpit/navigation";
import { CockpitStatusSummary } from "@/components/system-understanding/cockpit/status-summary";
import { UnderstandingMap } from "@/components/system-understanding/cockpit/understanding-map";
import { CockpitDetailPanel } from "@/components/system-understanding/cockpit/detail-panel";
import { CockpitUnresolvedItems } from "@/components/system-understanding/cockpit/unresolved-items";
import { CockpitQaProgressCard } from "@/components/system-understanding/cockpit/qa-progress";

// ── テストデータ ──────────────────────────────────────────────────────

function item(name: string, overrides: Partial<UnderstandingItem> = {}): UnderstandingItem {
  return {
    name,
    summary: `${name} の説明`,
    confidence: { level: "high", reason: "" },
    evidence: [{ path: `app/${name}.py`, start_line: 1, end_line: 10, summary: "" }],
    why_core: "",
    related_docs: [],
    related_apis: [],
    children: [],
    ...overrides,
  };
}

/** 全セクションに内容がある理解。 */
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

function qa(overrides: Partial<InterviewQaOut> & { id: number }): InterviewQaOut {
  return {
    session_id: 7,
    system_id: 1,
    question_text: `質問 ${overrides.id}`,
    question_category: "general",
    question_source: "reviewer",
    hypothesis: null,
    evidence_refs: [],
    runtime_evidence: null,
    answer_text: null,
    answer_unknown: null,
    status: "open",
    answered_by: null,
    superseded_by_id: null,
    created_at: 0,
    answered_at: null,
    ...overrides,
  };
}

function openQuestion(overrides: Partial<OpenQuestion> & { question: string }): OpenQuestion {
  return { category: "general", priority: "medium", ...overrides };
}

function gap(overrides: Partial<GapItem> & { name: string }): GapItem {
  return { gap_type: "code_only", summary: "", severity: "medium", ...overrides };
}

// ── 完成度 ────────────────────────────────────────────────────────────

describe("completionPercent", () => {
  it("固定値ではなくカテゴリ状態から算出する", () => {
    expect(completionPercent(["confirmed", "confirmed", "confirmed", "confirmed", "confirmed"]))
      .toBe(100);
    expect(completionPercent(["missing", "missing", "missing", "missing", "missing"])).toBe(0);
    // confirmed 4 + missing 1 → 4/5 = 80%
    expect(completionPercent(["confirmed", "confirmed", "confirmed", "confirmed", "missing"]))
      .toBe(80);
    // confirmed 3 + review 2 → (3 + 1)/5 = 80%
    expect(completionPercent(["confirmed", "confirmed", "confirmed", "review", "review"]))
      .toBe(80);
  });

  it("カテゴリが 0 件なら 0% を返す (0 除算しない)", () => {
    expect(completionPercent([])).toBe(0);
  });

  // 指摘 P1: 根拠の一部 (Q&A) が読めていない状態で数字を出すと、実際より
  // 高い完成度を確定値として見せてしまう。
  it("判定できないカテゴリがあれば null を返す (実際より高い値を出さない)", () => {
    expect(completionPercent(["confirmed", "confirmed", "unknown", "confirmed", "confirmed"]))
      .toBeNull();
  });
});

// ── カテゴリ状態の集約 ────────────────────────────────────────────────

describe("buildCockpitModel のカテゴリ状態", () => {
  it("全カテゴリ確認済み: gap も未解決質問も無ければ 100%", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: [],
    });
    expect(model.categories.map(c => c.status)).toEqual(
      ["confirmed", "confirmed", "confirmed", "confirmed", "confirmed"],
    );
    expect(model.completionPercent).toBe(100);
    expect(model.reviewCount).toBe(0);
    expect(model.missingCount).toBe(0);
    expect(model.nextStep.title).toContain("確認が必要な項目はありません");
  });

  it("Vision のみ未設定: 未設定が 1 件で、既定選択も Vision になる", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [],
      openQuestions: [],
      qaItems: [],
    });
    const vision = model.categories.find(c => c.key === "vision")!;
    expect(vision.status).toBe("missing");
    expect(vision.summary).toBe("未設定");
    expect(model.missingCount).toBe(1);
    expect(model.defaultCategory).toBe("vision");
    expect(model.nextStep.title).toContain("Vision");
  });

  it("旧レスポンスで vision キーが存在しなくても落ちず、未設定として扱う", () => {
    const legacy = fullUnderstanding();
    delete (legacy as Partial<CurrentUnderstanding>).vision;
    const model = buildCockpitModel({
      understanding: legacy,
      gaps: [],
      openQuestions: [],
      qaItems: [],
    });
    expect(model.categories.find(c => c.key === "vision")!.status).toBe("missing");
    expect(model.categories.find(c => c.key === "system_purpose")!.status).toBe("confirmed");
  });

  it("セッション構築中 (理解が null) は全カテゴリ未設定・完成度 0%", () => {
    const model = buildCockpitModel({
      understanding: null,
      gaps: null,
      openQuestions: null,
      qaItems: null,
    });
    expect(model.categories.every(c => c.status === "missing")).toBe(true);
    expect(model.completionPercent).toBe(0);
    expect(model.unresolved).toEqual([]);
    expect(model.qa!.total).toBe(0);
  });

  it("複数カテゴリが要確認: gap は名前一致、無ければ gap_type で割り当てる", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [
        // 名前が理解項目と完全一致 → そのカテゴリ (capabilities)
        gap({ name: "トレース収集", gap_type: "stale_explanation" }),
        // 名前一致なし → gap_type の既定 (api_boundaries)
        gap({ name: "GET /unknown", gap_type: "unclassified_entrypoint" }),
      ],
      openQuestions: [],
      qaItems: [],
    });
    const byKey = Object.fromEntries(model.categories.map(c => [c.key, c]));
    expect(byKey.capabilities.status).toBe("review");
    expect(byKey.api_boundaries.status).toBe("review");
    expect(byKey.system_purpose.status).toBe("confirmed");
    expect(model.reviewCount).toBe(2);
    // review 2 + confirmed 3 → 80%
    expect(model.completionPercent).toBe(80);
  });

  it("未解決質問があるカテゴリは要確認になる", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "この目的で正しいか", category: "purpose" })],
      qaItems: [],
    });
    const purpose = model.categories.find(c => c.key === "system_purpose")!;
    expect(purpose.status).toBe("review");
    expect(purpose.hint).toContain("この目的で正しいか");
  });
});

// ── 未解決事項 ────────────────────────────────────────────────────────

describe("未解決事項の集約", () => {
  it("高・中・低が混在しても影響度順に並ぶ", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "低優先", priority: "low", category: "api" }),
        openQuestion({ question: "高優先", priority: "high", category: "purpose" }),
        openQuestion({ question: "中優先", priority: "medium", category: "capability" }),
      ],
      qaItems: [],
    });
    expect(model.unresolved.map(u => u.question)).toEqual(["高優先", "中優先", "低優先"]);
    expect(model.nextStep.description).toBe("高優先");
  });

  it("解決済み Q&A を除外し、open_questions と Q&A の重複を排除する", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "未回答の質問", category: "api", qa_id: 11 })],
      qaItems: [
        qa({ id: 11, question_text: "未回答の質問", question_category: "api" }),
        qa({ id: 12, question_text: "回答済み", status: "answered" }),
        qa({ id: 13, question_text: "置き換え済み", status: "revised" }),
        qa({ id: 14, question_text: "後で回答", status: "skipped", question_category: "probe_flow" }),
        qa({ id: 15, question_text: "わからない", status: "unconfirmed", question_category: "capability" }),
      ],
    });
    // 除外されるのは answered (解決済み) と revised (置き換え履歴) だけ。
    expect(model.unresolved.map(u => u.question))
      .toEqual(["わからない", "未回答の質問", "後で回答"]);
    expect(model.unresolved.find(u => u.qaId === 15)!.unconfirmed).toBe(true);
    expect(model.unresolved.find(u => u.qaId === 14)!.deferred).toBe(true);
  });

  // skipped は「後で回答する」ための一時状態で、resume で open に戻せる
  // (routes/interview.py の skip/resume)。解決済みとして数えると、未回答が
  // 残っているのに未解決 0 件・完成度 100% と表示されてしまう。
  it("skipped の質問は未解決として数え、カテゴリを要確認にする", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: [qa({ id: 21, question_text: "後で答える", status: "skipped", question_category: "api" })],
    });
    expect(model.unresolved.map(u => u.question)).toEqual(["後で答える"]);
    expect(model.unresolved[0].deferred).toBe(true);
    // 開発者が自分で見送ったものなので、通常の未回答より下に置く。
    expect(model.unresolved[0].priority).toBe("low");
    expect(model.categories.find(c => c.key === "api_boundaries")!.status).toBe("review");
    expect(model.missingCount + model.reviewCount).toBeGreaterThan(0);
    expect(model.completionPercent).toBeLessThan(100);
    expect(model.qa!.total).toBe(1);
    expect(model.qa!.open).toBe(1);
    expect(model.qa!.deferred).toBe(1);
    expect(model.qa!.excluded).toBe(0);
  });

  it("どのカテゴリにも属さない general 質問は「全体」として残る", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "全体の話", category: "general" })],
      qaItems: [],
    });
    expect(model.unresolved[0].category).toBeNull();
    expect(model.unresolved[0].categoryLabel).toBe("全体");
    // カテゴリに属さない質問は、どのカテゴリも要確認にしない。
    expect(model.categories.every(c => c.status === "confirmed")).toBe(true);
  });
});

// ── 重複時の状態マージ (指摘 P2) ──────────────────────────────────────

// skip API は interview_qa の status しか更新せず、session.open_questions の
// JSON エントリはそのまま残る (routes/interview.py)。状態を持っているのは
// Q&A 行だけなので、重複は「後から来た方を捨てる」のではなく Q&A の状態を
// open_questions 行へ反映して 1 件にまとめる必要がある。
describe("open_questions と Q&A の重複マージ", () => {
  it("同じ qa_id の Q&A が skipped なら、1 件に畳んだ上で「後で回答」を反映する", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "見送った質問", category: "api", priority: "high", qa_id: 61 }),
      ],
      qaItems: [
        qa({ id: 61, question_text: "見送った質問", question_category: "api", status: "skipped" }),
      ],
    });
    expect(model.unresolved).toHaveLength(1);
    const row = model.unresolved[0];
    expect(row.qaId).toBe(61);
    expect(row.deferred).toBe(true);
    // 状態由来の優先度が open question 自身の high より優先される。
    expect(row.priority).toBe("low");
    expect(model.qa!.deferred).toBe(1);
  });

  it("同じ qa_id の Q&A が unconfirmed なら再確認待ちとして反映する", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "わからなかった質問", category: "capability", priority: "low", qa_id: 62 }),
      ],
      qaItems: [
        qa({
          id: 62, question_text: "わからなかった質問",
          question_category: "capability", status: "unconfirmed",
        }),
      ],
    });
    expect(model.unresolved).toHaveLength(1);
    expect(model.unresolved[0].unconfirmed).toBe(true);
    expect(model.unresolved[0].priority).toBe("high");
  });

  it("qa_id が無くても質問文が一致すれば同じ 1 件として状態を反映する", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "旧セッションの質問", category: "api" })],
      qaItems: [
        qa({ id: 63, question_text: "旧セッションの質問", question_category: "api", status: "skipped" }),
      ],
    });
    expect(model.unresolved).toHaveLength(1);
    expect(model.unresolved[0].deferred).toBe(true);
    expect(model.unresolved[0].qaId).toBe(63);
  });

  it("Q&A 側で回答済みなら open_questions に残っていても未解決から外す", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "回答済みの質問", category: "api", qa_id: 64 })],
      qaItems: [
        qa({ id: 64, question_text: "回答済みの質問", question_category: "api", status: "answered" }),
      ],
    });
    expect(model.unresolved).toEqual([]);
    expect(model.categories.find(c => c.key === "api_boundaries")!.status).toBe("confirmed");
  });
});

// ── Q&A 取得失敗 (指摘 P1) ────────────────────────────────────────────

describe("Q&A を取得できていないとき", () => {
  it("0 件という確定値を出さず、完成度も算出しない", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: undefined,
      qaFetchStatus: "unavailable",
    });
    expect(model.qa).toBeNull();
    expect(model.completionPercent).toBeNull();
    // 「未解決の質問が無い」ことを根拠にした確認済みは言えない。
    expect(model.categories.every(c => c.status === "unknown")).toBe(true);
    expect(model.confirmedCount).toBe(0);
    expect(model.unknownCount).toBe(5);
    expect(model.qaFetchStatus).toBe("unavailable");
    expect(model.nextStep.title).toContain("Q&A を取得できませんでした");
  });

  it("内容が無い / gap がある カテゴリは Q&A 無しでも判定できる", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [gap({ name: "GET /unknown", gap_type: "unclassified_entrypoint" })],
      openQuestions: [],
      qaItems: undefined,
      qaFetchStatus: "unavailable",
    });
    const byKey = Object.fromEntries(model.categories.map(c => [c.key, c]));
    expect(byKey.vision.status).toBe("missing");
    expect(byKey.api_boundaries.status).toBe("review");
    expect(byKey.system_purpose.status).toBe("unknown");
    expect(model.missingCount).toBe(1);
    expect(model.reviewCount).toBe(1);
  });

  it("読み込み中も 0 件を確定値として出さない", () => {
    const model = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: undefined,
      qaFetchStatus: "loading",
    });
    expect(model.qa).toBeNull();
    expect(model.completionPercent).toBeNull();
    expect(model.nextStep.title).toContain("読み込んでいます");
  });
});

// ── Q&A 進捗 ──────────────────────────────────────────────────────────

describe("qaProgress", () => {
  it("回答済み + 確認待ち + 未回答 が合計と一致する", () => {
    const progress = qaProgress([
      qa({ id: 1, status: "answered" }),
      qa({ id: 2, status: "answered" }),
      qa({ id: 3, status: "unconfirmed" }),
      qa({ id: 4, status: "open" }),
      qa({ id: 5, status: "revised" }),
      qa({ id: 6, status: "skipped" }),
    ]);
    // skipped は未回答に数え、合計から除くのは revised だけ。
    expect(progress).toEqual({
      answered: 2, awaiting: 1, open: 2, deferred: 1, total: 5, excluded: 1,
    });
    expect(progress.answered + progress.awaiting + progress.open).toBe(progress.total);
  });

  it("Q&A が 0 件なら全て 0", () => {
    const zero = { answered: 0, awaiting: 0, open: 0, deferred: 0, total: 0, excluded: 0 };
    expect(qaProgress([])).toEqual(zero);
    expect(qaProgress(null)).toEqual(zero);
  });
});

// ── 修正手段の可否 ────────────────────────────────────────────────────

function categoryView(overrides: Partial<CockpitCategoryView> = {}): CockpitCategoryView {
  const model = buildCockpitModel({
    understanding: fullUnderstanding(),
    gaps: [],
    openQuestions: [],
    qaItems: [],
  });
  return { ...model.categories[1], ...overrides };
}

describe("categoryActions", () => {
  it("W3 で未解決質問があるときだけ「質問に回答する」を実行できる", () => {
    const withQuestion = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "確認したい", category: "purpose" })],
      qaItems: [],
    }).categories.find(c => c.key === "system_purpose")!;

    const inW3 = categoryActions(withQuestion, "W3").find(a => a.kind === "answer_question")!;
    expect(inW3.disabledReason).toBeNull();
    expect(inW3.targetTestIds).toEqual(["work-surface-W3", "cockpit-unresolved-items"]);

    const inW5 = categoryActions(withQuestion, "W5").find(a => a.kind === "answer_question")!;
    expect(inW5.disabledReason).toContain("提案を承認する");
    expect(inW5.targetTestIds).toEqual([]);

    const noQuestion = categoryActions(categoryView(), "W3").find(a => a.kind === "answer_question")!;
    expect(noQuestion.disabledReason).toContain("未解決の質問はありません");
  });

  it("直接編集は W2/W3/W4 でだけ実行でき、それ以外は理由付きで無効になる", () => {
    for (const state of ["W2", "W3", "W4"] as const) {
      const action = categoryActions(categoryView(), state).find(a => a.kind === "direct_edit")!;
      expect(action.disabledReason).toBeNull();
      expect(action.targetTestIds).toEqual(["change-set-panel"]);
    }
    const blocked = categoryActions(categoryView(), "W6").find(a => a.kind === "direct_edit")!;
    expect(blocked.disabledReason).toContain("差分を確認する");
  });

  // 指摘 P2: 作業面の先頭ボタンではなく「その質問」へ移動する。複数の質問が
  // あるとき、選んだものと違う質問にフォーカスが当たってはならない。
  it("回答対象はカテゴリ内で最優先の質問そのものを指す", () => {
    const category = buildCockpitModel({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "後の質問", priority: "low", category: "api", qa_id: 52 }),
        openQuestion({ question: "先の質問", priority: "high", category: "api", qa_id: 51 }),
      ],
      qaItems: [],
    }).categories.find(c => c.key === "api_boundaries")!;
    const action = categoryActions(category, "W3").find(a => a.kind === "answer_question")!;
    expect(action.targetTestIds).toEqual([
      "qa-item-51", "work-surface-W3", "cockpit-unresolved-items",
    ]);
  });

  it("内容が無いカテゴリでは根拠確認を無効にする", () => {
    const empty = buildCockpitModel({
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [],
      openQuestions: [],
      qaItems: [],
    }).categories.find(c => c.key === "vision")!;
    const action = categoryActions(empty, "W2").find(a => a.kind === "review_evidence")!;
    expect(action.disabledReason).toContain("根拠がありません");
  });
});

// ── 表示 ──────────────────────────────────────────────────────────────

function model(input: Parameters<typeof buildCockpitModel>[0]) {
  return buildCockpitModel(input);
}

describe("CockpitStatusSummary", () => {
  it("完成度・カテゴリ数・要確認・未設定・質問合計と次のアクションを出す", () => {
    const m = model({
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [],
      openQuestions: [openQuestion({ question: "確認したい", category: "purpose" })],
      qaItems: [qa({ id: 1, status: "open" }), qa({ id: 2, status: "answered" })],
    });
    render(<CockpitStatusSummary model={m} onGoToTopUnresolved={() => {}} />);
    // vision missing(0) + purpose review(0.5) + 残り 3 confirmed → 70%
    expect(screen.getByTestId("cockpit-completion-percent")).toHaveTextContent("70");
    expect(screen.getByTestId("cockpit-stat-categories")).toHaveTextContent("5");
    expect(screen.getByTestId("cockpit-stat-review")).toHaveTextContent("1");
    expect(screen.getByTestId("cockpit-stat-missing")).toHaveTextContent("1");
    expect(screen.getByTestId("cockpit-stat-questions")).toHaveTextContent("2");
    expect(screen.getByTestId("cockpit-next-step")).toHaveTextContent("Vision");
    expect(screen.getByTestId("cockpit-progress-bar")).toHaveAttribute("aria-valuenow", "70");
  });

  it("Q&A を取得できていないときは 0 件・確定した完成度を出さない", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: undefined,
      qaFetchStatus: "unavailable",
    });
    render(<CockpitStatusSummary model={m} onGoToTopUnresolved={() => {}} />);
    expect(screen.getByTestId("cockpit-completion-percent")).toHaveTextContent("—");
    expect(screen.getByTestId("cockpit-stat-questions")).toHaveTextContent("—");
    expect(screen.getByTestId("cockpit-stat-questions")).not.toHaveTextContent("0");
    expect(screen.getByTestId("cockpit-completion-unavailable"))
      .toHaveTextContent("Q&A を取得できていない");
    // 誤解を招く進捗バー (0% でも 100% でも) は描かない。
    expect(screen.queryByTestId("cockpit-progress-bar")).toBeNull();
  });

  it("未解決が無ければ最優先へ移動する CTA を出さない", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: [],
    });
    render(<CockpitStatusSummary model={m} onGoToTopUnresolved={() => {}} />);
    expect(screen.queryByTestId("cockpit-go-to-top-unresolved")).toBeNull();
  });

  it("CTA を押すと最優先の未解決事項へのハンドラが呼ばれる", () => {
    const onGo = vi.fn();
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "高優先", priority: "high", category: "api" })],
      qaItems: [],
    });
    render(<CockpitStatusSummary model={m} onGoToTopUnresolved={onGo} />);
    fireEvent.click(screen.getByTestId("cockpit-go-to-top-unresolved"));
    expect(onGo).toHaveBeenCalledTimes(1);
  });
});

describe("UnderstandingMap", () => {
  it("5 カテゴリを状態ラベル付きで並べ、選択状態を aria-pressed で伝える", () => {
    const m = model({
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [],
      openQuestions: [openQuestion({ question: "確認したい", category: "capability" })],
      qaItems: [],
    });
    render(<UnderstandingMap categories={m.categories} selected="vision" onSelect={() => {}} />);
    expect(screen.getAllByRole("button")).toHaveLength(5);
    const vision = screen.getByTestId("cockpit-category-card-vision");
    expect(vision).toHaveAttribute("aria-pressed", "true");
    // 色だけでなくテキストで状態が読める。
    // 未設定は状態バッジとサマリーの両方に出る。
    expect(within(vision).getAllByText("未設定").length).toBeGreaterThan(0);
    expect(within(vision).getByTestId("cockpit-status-missing")).toBeTruthy();
    expect(
      within(screen.getByTestId("cockpit-category-card-capabilities")).getByText("要確認"),
    ).toBeTruthy();
    expect(
      within(screen.getByTestId("cockpit-category-card-api_boundaries")).getByText("確認済み"),
    ).toBeTruthy();
  });

  it("カードはキーボードで選択できる (native button)", () => {
    const onSelect = vi.fn();
    const m = model({ understanding: fullUnderstanding(), gaps: [], openQuestions: [], qaItems: [] });
    render(<UnderstandingMap categories={m.categories} selected="vision" onSelect={onSelect} />);
    const card = screen.getByTestId("cockpit-category-card-probe_flow");
    card.focus();
    expect(document.activeElement).toBe(card);
    // Enter / Space は native button のクリックとして届く。
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledWith("probe_flow");
  });
});

describe("CockpitDetailPanel", () => {
  it("選択カテゴリの内容・理由・根拠・下流・修正手段を出す", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [gap({ name: "トレース収集", gap_type: "stale_explanation", summary: "説明が古い" })],
      openQuestions: [openQuestion({ question: "責務は独立か", category: "capability" })],
      qaItems: [],
    });
    const capabilities = m.categories.find(c => c.key === "capabilities")!;
    render(<CockpitDetailPanel category={capabilities} state="W3" onAction={() => {}} />);
    expect(screen.getByTestId("cockpit-detail-panel")).toHaveAttribute("data-category", "capabilities");
    expect(screen.getByTestId("cockpit-detail-content")).toHaveTextContent("トレース収集");
    expect(screen.getByTestId("cockpit-detail-gap")).toHaveTextContent("説明が古い");
    expect(screen.getByTestId("cockpit-detail-question")).toHaveTextContent("責務は独立か");
    expect(screen.getByTestId("cockpit-detail-evidence")).toBeTruthy();
    expect(screen.getByTestId("cockpit-detail-downstream")).toHaveTextContent("API boundaries");
  });

  it("実行できない修正手段は理由を添えて disabled にする", () => {
    const m = model({ understanding: fullUnderstanding(), gaps: [], openQuestions: [], qaItems: [] });
    render(<CockpitDetailPanel category={m.categories[1]} state="W6" onAction={() => {}} />);
    const edit = screen.getByTestId("cockpit-action-direct_edit");
    expect(edit).toBeDisabled();
    expect(edit).toHaveTextContent("直接編集は理解・質問・ズレの確認中だけ行えます");
  });

  it("実行できる手段を押すと既存パネルの data-testid を渡す", () => {
    const onAction = vi.fn();
    const m = model({ understanding: fullUnderstanding(), gaps: [], openQuestions: [], qaItems: [] });
    render(<CockpitDetailPanel category={m.categories[1]} state="W2" onAction={onAction} />);
    fireEvent.click(screen.getByTestId("cockpit-action-direct_edit"));
    expect(onAction).toHaveBeenCalledWith(["change-set-panel"]);
  });
});

describe("CockpitUnresolvedItems", () => {
  it("優先度・カテゴリ・質問文・アクションを 1 行で出す", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "高優先", priority: "high", category: "purpose" }),
        openQuestion({ question: "低優先", priority: "low", category: "api" }),
      ],
      qaItems: [],
    });
    const onSelect = vi.fn();
    render(<CockpitUnresolvedItems items={m.unresolved} onSelect={onSelect} />);
    const rows = screen.getAllByTestId("cockpit-unresolved-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("優先度 高");
    expect(rows[0]).toHaveTextContent("System purpose");
    expect(rows[0]).toHaveTextContent("高優先");
    fireEvent.click(within(rows[0]).getByTestId("cockpit-unresolved-action"));
    expect(onSelect).toHaveBeenCalledWith(m.unresolved[0]);
  });

  it("未解決が無ければ空表示にする", () => {
    render(<CockpitUnresolvedItems items={[]} onSelect={() => {}} />);
    expect(screen.getByTestId("cockpit-unresolved-empty")).toBeTruthy();
  });

  it("Q&A 未取得のときは「未解決なし」と読める表示にしない", () => {
    render(
      <CockpitUnresolvedItems items={[]} qaFetchStatus="unavailable" onSelect={() => {}} />,
    );
    expect(screen.getByTestId("cockpit-unresolved-qa-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("cockpit-unresolved-empty")).toBeNull();
  });

  it("「後で回答」の行はその旨を明示する", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "見送った質問", category: "api", qa_id: 71 })],
      qaItems: [
        qa({ id: 71, question_text: "見送った質問", question_category: "api", status: "skipped" }),
      ],
    });
    render(<CockpitUnresolvedItems items={m.unresolved} onSelect={() => {}} />);
    const rows = screen.getAllByTestId("cockpit-unresolved-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("後で回答");
    expect(rows[0]).toHaveTextContent("優先度 低");
  });
});

describe("CockpitQaProgressCard", () => {
  it("件数をテキストでも読めるようにし、ドーナツに読み上げ用ラベルを付ける", () => {
    render(
      <CockpitQaProgressCard
        progress={{ answered: 8, awaiting: 2, open: 1, deferred: 1, total: 11, excluded: 3 }}
      />,
    );
    expect(screen.getByTestId("cockpit-qa-answered")).toHaveTextContent("回答済み");
    expect(screen.getByTestId("cockpit-qa-answered")).toHaveTextContent("8 件");
    expect(screen.getByTestId("cockpit-qa-awaiting")).toHaveTextContent("2 件");
    expect(screen.getByTestId("cockpit-qa-open")).toHaveTextContent("1 件");
    expect(screen.getByTestId("cockpit-qa-total")).toHaveTextContent("11 件");
    expect(screen.getByTestId("cockpit-qa-donut")).toHaveAttribute(
      "aria-label",
      "Q&A の進捗: 合計 11 件のうち、回答済み 8 件、確認待ち 2 件、未回答 1 件",
    );
    expect(screen.getByTestId("cockpit-qa-excluded")).toHaveTextContent("3 件");
    // 「後で回答」は未回答の内訳として再掲する (解決済みに見せない)。
    expect(screen.getByTestId("cockpit-qa-deferred")).toHaveTextContent("1 件");
  });

  it("取得失敗時は 0 件ではなくエラーと再試行を出す", () => {
    const onRetry = vi.fn();
    render(
      <CockpitQaProgressCard
        progress={null}
        status="unavailable"
        error="Failed to fetch"
        onRetry={onRetry}
      />,
    );
    expect(screen.getByTestId("cockpit-qa-unavailable")).toBeTruthy();
    expect(screen.getByTestId("cockpit-qa-error-detail")).toHaveTextContent("Failed to fetch");
    expect(screen.queryByTestId("cockpit-qa-empty")).toBeNull();
    expect(screen.queryByTestId("cockpit-qa-donut")).toBeNull();
    expect(screen.queryByTestId("cockpit-qa-total")).toBeNull();
    fireEvent.click(screen.getByTestId("cockpit-qa-retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("読み込み中は件数を出さず読み込み中と示す", () => {
    render(<CockpitQaProgressCard progress={null} status="loading" />);
    expect(screen.getByTestId("cockpit-qa-loading")).toBeTruthy();
    expect(screen.queryByTestId("cockpit-qa-empty")).toBeNull();
    expect(screen.queryByTestId("cockpit-qa-total")).toBeNull();
  });

  it("Q&A が 0 件でもドーナツを描かず空表示にする", () => {
    render(
      <CockpitQaProgressCard
        progress={{ answered: 0, awaiting: 0, open: 0, deferred: 0, total: 0, excluded: 0 }}
      />,
    );
    expect(screen.getByTestId("cockpit-qa-empty")).toBeTruthy();
    expect(screen.queryByTestId("cockpit-qa-donut")).toBeNull();
  });
});

describe("unresolvedTargets", () => {
  it("qa_id があればその質問の行を先頭候補にする", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "質問", category: "api", qa_id: 77 })],
      qaItems: [],
    });
    expect(unresolvedTargets(m.unresolved[0])[0]).toBe("qa-item-77");
  });

  it("qa_id を持たない質問は作業面・一覧へフォールバックする", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "旧セッションの質問", category: "api" })],
      qaItems: [],
    });
    expect(unresolvedTargets(m.unresolved[0]))
      .toEqual(["work-surface-W3", "cockpit-unresolved-items"]);
  });
});

describe("focusCockpitTarget", () => {
  it("対象パネル内の最初の操作要素へフォーカスを移す", () => {
    render(
      <div data-testid="work-surface-W3">
        <button>回答を送信</button>
      </div>,
    );
    expect(focusCockpitTarget("work-surface-W3")).toBe(true);
    expect(document.activeElement?.textContent).toBe("回答を送信");
  });

  it("対象が描かれていなければ false を返す (呼び出し側が案内できる)", () => {
    render(<div />);
    expect(focusCockpitTarget("work-surface-W3")).toBe(false);
  });

  it("候補を優先度順に試し、描かれている最初のものへ移動する", () => {
    render(
      <div>
        <div data-testid="work-surface-W3">
          <button>作業面の先頭ボタン</button>
        </div>
        <div data-testid="qa-item-51">
          <button>この質問に回答する</button>
        </div>
      </div>,
    );
    // qa-item が存在すれば、作業面より先にそちらへ移動する。
    expect(focusFirstCockpitTarget(["qa-item-51", "work-surface-W3"])).toBe(true);
    expect(document.activeElement?.textContent).toBe("この質問に回答する");
  });

  it("先頭候補が描かれていなければ次の候補へ落ちる", () => {
    render(
      <div data-testid="work-surface-W3">
        <button>作業面の先頭ボタン</button>
      </div>,
    );
    expect(focusFirstCockpitTarget(["qa-item-99", "work-surface-W3"])).toBe(true);
    expect(document.activeElement?.textContent).toBe("作業面の先頭ボタン");
  });
});
