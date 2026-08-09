/// <reference types="vitest/globals" />
// Issue #359 / #360: 未解決事項のグルーピングと段階的開示、そして「次にやること」
// の決定表のテスト。
//
// グルーピングと `nextStep` は `buildCockpitModel` / `groupUnresolvedItems` /
// の純粋関数なので、そのまま呼んで検証する。表示側は props だけで完結するので
// Router や API のモックは不要 (`interview-cockpit.test.tsx` と同じ方針)。
//
// テスト観点 (issue #359「受け入れ条件」):
//   同カテゴリの質問が 1 グループに畳まれる / グループ順が代表質問の優先度順 /
//   状態内訳 / 初期表示 3 グループ / 「残り N 件を表示」の N は質問数 /
//   折りたたみ / 展開・折りたたみ後のフォーカス移動 / 代表以外の質問も開ける /
//   loading・unavailable・empty・後で回答・再確認待ちの表示維持

import { render, screen, fireEvent, within } from "@testing-library/react";
import { vi } from "vitest";
import type {
  CurrentUnderstanding,
  GapItem,
  InterviewQaOut,
  InterviewWorkflowState,
  OpenQuestion,
  UnderstandingItem,
} from "@/api/types";
import {
  buildCockpitModel,
  groupUnresolvedItems,
} from "@/components/system-understanding/cockpit/model";
import { CockpitUnresolvedItems } from "@/components/system-understanding/cockpit/unresolved-items";
import { briefLeadsMainColumn } from "@/components/system-understanding/cockpit/layout";

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

function model(input: Parameters<typeof buildCockpitModel>[0]) {
  return buildCockpitModel(input);
}

/** 5 グループ (purpose / capability / api / probe_flow / general) を作る。 */
function fiveGroupModel() {
  return model({
    understanding: fullUnderstanding(),
    gaps: [],
    openQuestions: [
      openQuestion({ question: "目的1", priority: "high", category: "purpose" }),
      openQuestion({ question: "目的2", priority: "medium", category: "purpose" }),
      openQuestion({ question: "機能1", priority: "high", category: "capability" }),
      openQuestion({ question: "API1", priority: "medium", category: "api" }),
      openQuestion({ question: "フロー1", priority: "low", category: "probe_flow" }),
      openQuestion({ question: "全体1", priority: "low", category: "general" }),
    ],
    qaItems: [],
  });
}

// ── グルーピング ──────────────────────────────────────────────────────

describe("groupUnresolvedItems", () => {
  it("同じカテゴリの質問は 1 グループに畳み、代表は最優先の 1 件になる", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "目的の中", priority: "medium", category: "purpose" }),
        openQuestion({ question: "目的の高", priority: "high", category: "purpose" }),
        openQuestion({ question: "目的の低", priority: "low", category: "purpose" }),
      ],
      qaItems: [],
    });
    expect(m.unresolvedGroups).toHaveLength(1);
    const group = m.unresolvedGroups[0];
    expect(group.id).toBe("cat-system_purpose");
    expect(group.category).toBe("system_purpose");
    expect(group.categoryLabel).toBe("System purpose");
    // 入力 (= 優先度順) の並びをそのまま保つ。
    expect(group.items.map(i => i.question)).toEqual(["目的の高", "目的の中", "目的の低"]);
    expect(group.representative).toBe(group.items[0]);
    expect(group.priority).toBe("high");
    expect(group.counts).toEqual({ total: 3, unconfirmed: 0, deferred: 0, open: 3 });
    // 元の一覧はそのまま残す (件数・最優先項目の判定に使う)。
    expect(m.unresolved).toHaveLength(3);
  });

  it("グループの並びは代表質問が一覧に現れた順 (= 優先度順) になる", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "API の低", priority: "low", category: "api" }),
        openQuestion({ question: "機能の高", priority: "high", category: "capability" }),
        openQuestion({ question: "目的の中", priority: "medium", category: "purpose" }),
        openQuestion({ question: "API の高", priority: "high", category: "api" }),
      ],
      qaItems: [],
    });
    expect(m.unresolvedGroups.map(g => g.id)).toEqual([
      "cat-capabilities",
      "cat-api_boundaries",
      "cat-system_purpose",
    ]);
    // api グループの代表は「高」の方 (一覧で先に来る)。
    const api = m.unresolvedGroups.find(g => g.id === "cat-api_boundaries")!;
    expect(api.representative.question).toBe("API の高");
    expect(api.items.map(i => i.question)).toEqual(["API の高", "API の低"]);
    expect(api.priority).toBe("high");
  });

  it("状態内訳を数え、open = total - unconfirmed - deferred が成り立つ", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: [
        qa({ id: 1, question_text: "未回答", question_category: "api" }),
        qa({ id: 2, question_text: "わからない", question_category: "api", status: "unconfirmed" }),
        qa({ id: 3, question_text: "後で回答", question_category: "api", status: "skipped" }),
        qa({ id: 4, question_text: "回答済み", question_category: "api", status: "answered" }),
      ],
    });
    expect(m.unresolvedGroups).toHaveLength(1);
    const counts = m.unresolvedGroups[0].counts;
    expect(counts).toEqual({ total: 3, unconfirmed: 1, deferred: 1, open: 1 });
    expect(counts.open).toBe(counts.total - counts.unconfirmed - counts.deferred);
  });

  it("どのカテゴリにも属さない質問は cat-general として独立する", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "全体の話", category: "general" }),
        openQuestion({ question: "API の話", category: "api" }),
      ],
      qaItems: [],
    });
    const general = m.unresolvedGroups.find(g => g.id === "cat-general")!;
    expect(general.category).toBeNull();
    expect(general.categoryLabel).toBe("全体");
    expect(general.items).toHaveLength(1);
    expect(m.unresolvedGroups).toHaveLength(2);
  });

  it("未解決が無ければグループも空", () => {
    expect(groupUnresolvedItems([])).toEqual([]);
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: [],
    });
    expect(m.unresolvedGroups).toEqual([]);
  });
});

// ── 次にやること (issue #360 の決定表) ────────────────────────────────

describe("nextStep の決定表", () => {
  it("1. Q&A 取得失敗が最優先で retry_qa になる", () => {
    const m = model({
      // 未設定カテゴリも未解決質問もあるが、件数を確定できないので再取得が先。
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [],
      openQuestions: [openQuestion({ question: "確認したい", category: "purpose" })],
      qaItems: undefined,
      qaFetchStatus: "unavailable",
    });
    expect(m.nextStep.kind).toBe("retry_qa");
    expect(m.nextStep.title).toBe("Q&A を取得できませんでした");
    expect(m.nextStep.description).toContain("再試行");
    expect(m.nextStep.category).toBeNull();
    expect(m.nextStep.item).toBeNull();
    expect(m.nextStep.actionLabel).toBeNull();
    expect(m.nextStep.targetTestIds).toEqual([]);
  });

  it("2. 読み込み中は state_primary のまま待つ", () => {
    const m = model({
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [],
      openQuestions: [openQuestion({ question: "確認したい", category: "purpose" })],
      qaItems: undefined,
      qaFetchStatus: "loading",
    });
    expect(m.nextStep.kind).toBe("state_primary");
    expect(m.nextStep.title).toBe("Q&A を読み込んでいます");
    expect(m.nextStep.actionLabel).toBeNull();
    expect(m.nextStep.targetTestIds).toEqual([]);
  });

  it("3. 未設定カテゴリは未解決質問より先に fix_category になる", () => {
    const m = model({
      understanding: fullUnderstanding({ vision: [] }),
      gaps: [],
      openQuestions: [openQuestion({ question: "確認したい", category: "purpose" })],
      qaItems: [],
    });
    expect(m.nextStep.kind).toBe("fix_category");
    expect(m.nextStep.category).toBe("vision");
    expect(m.nextStep.title).toBe("Vision を定義する");
    expect(m.nextStep.description).toContain("コードからは決められない項目です");
    expect(m.nextStep.item).toBeNull();
    expect(m.nextStep.actionLabel).toBe("Vision を定義する");
    expect(m.nextStep.targetTestIds).toEqual(["cockpit-detail-panel"]);
  });

  it("4. 未解決質問は要確認カテゴリより優先され、その質問そのものを指す", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [
        // gap により api_boundaries は要確認になるが、答えられる質問が先。
        gap({ name: "GET /unknown", gap_type: "unclassified_entrypoint" }),
      ],
      openQuestions: [
        openQuestion({ question: "この目的で正しいか", category: "purpose", qa_id: 91 }),
      ],
      qaItems: [],
    });
    expect(m.nextStep.kind).toBe("answer_question");
    expect(m.nextStep.item).toBe(m.unresolved[0]);
    expect(m.nextStep.category).toBe("system_purpose");
    expect(m.nextStep.title).toBe("System purpose の確認事項に答える");
    expect(m.nextStep.description).toBe("この目的で正しいか");
    expect(m.nextStep.actionLabel).toBe("この確認事項に回答する");
    // `unresolvedTargets` と同じ候補列 (質問の行 → 作業面 → 一覧)。
    expect(m.nextStep.targetTestIds).toEqual([
      "qa-item-91",
      "work-surface-W3",
      "cockpit-unresolved-items",
    ]);
  });

  it("5. 質問が無ければ要確認カテゴリを fix_category として案内する", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [gap({ name: "GET /unknown", gap_type: "unclassified_entrypoint", summary: "分類できていない" })],
      openQuestions: [],
      qaItems: [],
    });
    const apiCategory = m.categories.find(c => c.key === "api_boundaries")!;
    expect(m.nextStep.kind).toBe("fix_category");
    expect(m.nextStep.category).toBe("api_boundaries");
    expect(m.nextStep.title).toBe("API boundaries の内容を確認する");
    expect(m.nextStep.description).toBe(apiCategory.hint);
    expect(m.nextStep.actionLabel).toBe("API boundaries を確認する");
    expect(m.nextStep.targetTestIds).toEqual(["cockpit-detail-panel"]);
  });

  it("6. どれも無ければ状態の主操作へ進む", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: [],
    });
    expect(m.nextStep.kind).toBe("state_primary");
    expect(m.nextStep.title).toBe("確認が必要な項目はありません");
    expect(m.nextStep.description).toBe(
      "理解の全体像は揃っています。この状態の主操作へ進んでください。",
    );
    expect(m.nextStep.category).toBeNull();
    expect(m.nextStep.item).toBeNull();
    expect(m.nextStep.actionLabel).toBeNull();
    expect(m.nextStep.targetTestIds).toEqual([]);
  });

  it("種別は 4 値の有限集合に閉じている", () => {
    const kinds = new Set(
      [
        model({ understanding: fullUnderstanding(), gaps: [], openQuestions: [], qaItems: undefined, qaFetchStatus: "unavailable" }),
        model({ understanding: fullUnderstanding(), gaps: [], openQuestions: [], qaItems: undefined, qaFetchStatus: "loading" }),
        model({ understanding: fullUnderstanding({ vision: [] }), gaps: [], openQuestions: [], qaItems: [] }),
        model({ understanding: fullUnderstanding(), gaps: [], openQuestions: [openQuestion({ question: "q", category: "api" })], qaItems: [] }),
        model({ understanding: fullUnderstanding(), gaps: [], openQuestions: [], qaItems: [] }),
      ].map(m => m.nextStep.kind),
    );
    for (const kind of kinds) {
      expect(["answer_question", "fix_category", "retry_qa", "state_primary"]).toContain(kind);
    }
  });
});

// ── 段階的開示 ────────────────────────────────────────────────────────

describe("CockpitUnresolvedItems の段階的開示", () => {
  function renderList(overrides: Partial<Parameters<typeof CockpitUnresolvedItems>[0]> = {}) {
    const m = fiveGroupModel();
    const onSelect = vi.fn();
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={onSelect}
        {...overrides}
      />,
    );
    return { m, onSelect };
  }

  it("初期表示は上位 3 グループだけで、残件は「質問数」で案内する", () => {
    const { m } = renderList();
    expect(m.unresolvedGroups).toHaveLength(5);
    const rows = screen.getAllByTestId("cockpit-unresolved-row");
    expect(rows).toHaveLength(3);
    // 表示は代表質問 (= グループ内で最優先) のみ。
    expect(rows[0]).toHaveTextContent("目的1");
    expect(rows[0]).toHaveTextContent("System purpose");
    expect(rows[1]).toHaveTextContent("機能1");
    expect(rows[2]).toHaveTextContent("API1");
    // 隠れているのは 2 グループだが、質問は 2 件。
    expect(screen.getByTestId("cockpit-unresolved-expand")).toHaveTextContent("残り 2 件を表示");
    // ヘッダーのバッジは総質問数のまま。
    expect(screen.getByTestId("cockpit-unresolved-items")).toHaveTextContent("残り 6 件");
  });

  it("残件の N は隠れている「質問」数で、グループ数ではない", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "目的1", priority: "high", category: "purpose" }),
        openQuestion({ question: "機能1", priority: "high", category: "capability" }),
        openQuestion({ question: "API1", priority: "high", category: "api" }),
        // 以下 2 グループ・計 3 件が隠れる。
        openQuestion({ question: "フロー1", priority: "low", category: "probe_flow" }),
        openQuestion({ question: "フロー2", priority: "low", category: "probe_flow" }),
        openQuestion({ question: "全体1", priority: "low", category: "general" }),
      ],
      qaItems: [],
    });
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByTestId("cockpit-unresolved-expand")).toHaveTextContent("残り 3 件を表示");
  });

  it("展開すると全グループが出て、折りたたむと上位のみに戻る", () => {
    renderList();
    fireEvent.click(screen.getByTestId("cockpit-unresolved-expand"));
    expect(screen.getAllByTestId("cockpit-unresolved-row")).toHaveLength(5);
    expect(screen.queryByTestId("cockpit-unresolved-expand")).toBeNull();

    fireEvent.click(screen.getByTestId("cockpit-unresolved-collapse"));
    expect(screen.getAllByTestId("cockpit-unresolved-row")).toHaveLength(3);
    expect(screen.getByTestId("cockpit-unresolved-expand")).toBeTruthy();
  });

  it("initialVisibleGroups で初期件数を変えられる", () => {
    renderList({ initialVisibleGroups: 1 });
    expect(screen.getAllByTestId("cockpit-unresolved-row")).toHaveLength(1);
    // 表示した purpose グループが 2 件なので、隠れているのは 4 グループ・計 4 件。
    expect(screen.getByTestId("cockpit-unresolved-expand")).toHaveTextContent("残り 4 件を表示");
  });

  it("グループ数が初期表示以内なら展開操作を出さない", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "目的1", category: "purpose" }),
        openQuestion({ question: "目的2", category: "purpose" }),
      ],
      qaItems: [],
    });
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={() => {}}
      />,
    );
    expect(screen.queryByTestId("cockpit-unresolved-expand")).toBeNull();
    expect(screen.queryByTestId("cockpit-unresolved-collapse")).toBeNull();
  });

  // issue #359: 展開／折りたたみ後もフォーカスを失わない。
  it("展開後は新たに現れた先頭行へ、折りたたみ後は操作ボタンへフォーカスが移る", () => {
    renderList();
    fireEvent.click(screen.getByTestId("cockpit-unresolved-expand"));
    const actions = screen.getAllByTestId("cockpit-unresolved-action");
    // 4 番目 (= 初期表示 3 件の直後) が最初に現れた行。
    expect(document.activeElement).toBe(actions[3]);

    fireEvent.click(screen.getByTestId("cockpit-unresolved-collapse"));
    expect(document.activeElement).toBe(screen.getByTestId("cockpit-unresolved-expand"));
  });
});

// ── グループ内の表示と操作 ────────────────────────────────────────────

describe("CockpitUnresolvedItems のグループ表示", () => {
  function groupedModel() {
    return model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "代表の質問", priority: "high", category: "api", qa_id: 101 }),
        openQuestion({ question: "2 番目の質問", priority: "medium", category: "api", qa_id: 102 }),
        openQuestion({ question: "3 番目の質問", priority: "medium", category: "api", qa_id: 103 }),
      ],
      qaItems: [
        qa({ id: 101, question_text: "代表の質問", question_category: "api" }),
        qa({ id: 102, question_text: "2 番目の質問", question_category: "api", status: "unconfirmed" }),
        qa({ id: 103, question_text: "3 番目の質問", question_category: "api", status: "skipped" }),
      ],
    });
  }

  it("代表質問・件数・状態内訳を 1 行にまとめる", () => {
    const m = groupedModel();
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={() => {}}
      />,
    );
    const rows = screen.getAllByTestId("cockpit-unresolved-row");
    expect(rows).toHaveLength(1);
    // 代表は状態由来で優先度が高い「2 番目の質問」(unconfirmed → high)。
    expect(rows[0]).toHaveTextContent("API boundaries");
    expect(rows[0]).toHaveTextContent("再確認待ち");
    expect(rows[0]).toHaveTextContent("ほか 2 件");
    expect(rows[0]).toHaveTextContent("再確認待ち 1 件");
    expect(rows[0]).toHaveTextContent("後で回答 1 件");
  });

  it("件数が 0 の状態内訳は出さない", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [
        openQuestion({ question: "未回答1", category: "api" }),
        openQuestion({ question: "未回答2", category: "api" }),
      ],
      qaItems: [],
    });
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={() => {}}
      />,
    );
    const row = screen.getByTestId("cockpit-unresolved-row");
    expect(row).toHaveTextContent("ほか 1 件");
    expect(row.textContent).not.toContain("再確認待ち 0 件");
    expect(row.textContent).not.toContain("後で回答 0 件");
  });

  it("1 件だけのグループには開閉と「ほか N 件」を出さない", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [openQuestion({ question: "単独の質問", category: "api" })],
      qaItems: [],
    });
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={() => {}}
      />,
    );
    expect(screen.queryByTestId("cockpit-unresolved-group-toggle")).toBeNull();
    expect(screen.getByTestId("cockpit-unresolved-row").textContent).not.toContain("ほか");
  });

  it("代表を開くと代表の項目が渡る", () => {
    const m = groupedModel();
    const onSelect = vi.fn();
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("cockpit-unresolved-action"));
    expect(onSelect).toHaveBeenCalledWith(m.unresolvedGroups[0].representative);
  });

  // 代表以外の質問が開けないと、その質問へ到達する手段が無くなる。
  it("代表以外の質問も開示して個別に開ける", () => {
    const m = groupedModel();
    const onSelect = vi.fn();
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={onSelect}
      />,
    );
    const row = screen.getByTestId("cockpit-unresolved-row");
    const toggle = within(row).getByTestId("cockpit-unresolved-group-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    const rest = screen.getAllByTestId("cockpit-unresolved-group-item-action");
    expect(rest).toHaveLength(2);
    fireEvent.click(rest[1]);
    // 代表ではなく、押した質問そのものが渡る。
    const expected = m.unresolvedGroups[0].items[2];
    expect(onSelect).toHaveBeenCalledWith(expected);
    expect(onSelect.mock.calls[0][0]).not.toBe(m.unresolvedGroups[0].representative);

    fireEvent.click(toggle);
    expect(screen.queryByTestId("cockpit-unresolved-group-item-action")).toBeNull();
  });
});

// ── 既存表示の維持 ────────────────────────────────────────────────────

describe("CockpitUnresolvedItems の状態別表示", () => {
  it("未解決が無ければ空表示にする", () => {
    render(<CockpitUnresolvedItems groups={[]} totalCount={0} onSelect={() => {}} />);
    expect(screen.getByTestId("cockpit-unresolved-empty")).toBeTruthy();
    expect(screen.getByTestId("cockpit-unresolved-items")).toHaveTextContent("残り 0 件");
  });

  it("Q&A 未取得のときは「未解決なし」と読める表示にしない", () => {
    render(
      <CockpitUnresolvedItems
        groups={[]}
        totalCount={0}
        qaFetchStatus="unavailable"
        onSelect={() => {}}
      />,
    );
    expect(screen.getByTestId("cockpit-unresolved-qa-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("cockpit-unresolved-empty")).toBeNull();
    expect(screen.getByTestId("cockpit-unresolved-items")).toHaveTextContent("未取得");
  });

  it("読み込み中も未取得であることを示す", () => {
    render(
      <CockpitUnresolvedItems
        groups={[]}
        totalCount={0}
        qaFetchStatus="loading"
        onSelect={() => {}}
      />,
    );
    expect(screen.getByTestId("cockpit-unresolved-qa-unavailable")).toHaveTextContent("読み込み中");
    expect(screen.queryByTestId("cockpit-unresolved-empty")).toBeNull();
  });

  it("「後で回答」「再確認待ち」の行はその旨を明示する", () => {
    const m = model({
      understanding: fullUnderstanding(),
      gaps: [],
      openQuestions: [],
      qaItems: [
        qa({ id: 71, question_text: "見送った質問", question_category: "api", status: "skipped" }),
        qa({
          id: 72,
          question_text: "わからない質問",
          question_category: "capability",
          status: "unconfirmed",
        }),
      ],
    });
    render(
      <CockpitUnresolvedItems
        groups={m.unresolvedGroups}
        totalCount={m.unresolved.length}
        onSelect={() => {}}
      />,
    );
    const rows = screen.getAllByTestId("cockpit-unresolved-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("再確認待ち");
    expect(rows[0]).toHaveTextContent("優先度 高");
    expect(rows[1]).toHaveTextContent("後で回答");
    expect(rows[1]).toHaveTextContent("優先度 低");
  });
});

// ── メイン列の並び順 (Issue #360) ─────────────────────────────────────

describe("briefLeadsMainColumn", () => {
  // 並び順そのものは固定で、状態によって変わるのは Understanding Brief の
  // 位置だけ。全状態を列挙して、どれも判定が抜けていないことを見る。
  it("W1 / W2 と状態不明のときだけ Brief が主作業面の位置に立つ", () => {
    const states: InterviewWorkflowState[] = [
      "W0-A", "W0-B", "W1", "W2", "W3", "W4", "W5", "W6", "W7",
    ];
    const leads = Object.fromEntries(
      states.map(s => [s, briefLeadsMainColumn(s)]),
    );
    expect(leads).toEqual({
      "W0-A": false,
      "W0-B": false,
      W1: true,
      W2: true,
      W3: false,
      W4: false,
      W5: false,
      W6: false,
      W7: false,
    });
    // 古い Control Server / 取得失敗で状態が無いときは、先に立てる作業面が
    // 無いので Brief を先頭にする。
    expect(briefLeadsMainColumn(null)).toBe(true);
  });
});
