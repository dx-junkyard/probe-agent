// Issue #356: インタビュー・コックピットの表示モデル。
//
// この画面が答えるのは 5 点 (issue の「目的」):
//   1. インタビュー全体の完成度
//   2. 未解決事項の数と優先順位
//   3. 次に行うべき操作
//   4. 選択した理解項目の修正方法
//   5. Q&A の回答進捗
//
// このファイルは純粋関数だけで構成する。表示コンポーネントは「決まったものを
// 描く」だけにし、状態判定・集計・優先順位付けをコンポーネント内に散らさない
// (issue「実装方針」)。React にも API にも依存しないので単体テストできる。
//
// 判定はすべて有限集合の決定的な突き合わせで、キーワードスコアや類似度は使わ
// ない (Core Design Principle 6)。入力は既存のサーバー応答だけで、新しい
// バックエンド API は増やさない (issue「対象外」)。
//
// ワークフロー状態 (`W0-A`〜`W7`) は Issue #349 のとおりサーバーが決めた値を
// そのまま受け取る。ここで再導出はしない -- 操作可否の判定に「今どの状態か」
// を使うだけである。

import type {
  CurrentUnderstanding,
  GapItem,
  InterviewQaCategory,
  InterviewQaOut,
  InterviewWorkflowState,
  OpenQuestion,
  UnderstandingItem,
} from "@/api/types";
import { WORKFLOW_STATE_LABELS } from "@/components/system-understanding/workflow-panel";

// ── カテゴリ定義 ──────────────────────────────────────────────────────

/** 理解の全体マップに並べる 5 カテゴリ (issue §3)。順序は表示順そのもの。 */
export type CockpitCategoryKey =
  | "vision"
  | "system_purpose"
  | "capabilities"
  | "api_boundaries"
  | "probe_flow";

/** カテゴリの状態 (issue §3 の 3 値)。色だけでなく必ずラベルを伴わせる。 */
export type CockpitCategoryStatus = "confirmed" | "review" | "missing";

/** `CurrentUnderstanding` のどのセクションを集約するか (issue §3)。 */
type UnderstandingSection = keyof CurrentUnderstanding;

interface CategoryDefinition {
  key: CockpitCategoryKey;
  /** 表示順の通し番号。マップと詳細ペインで同じ値を出す。 */
  number: string;
  title: string;
  caption: string;
  sections: UnderstandingSection[];
  /** この カテゴリに属する Q&A カテゴリ (有限集合の直接対応)。 */
  qaCategories: InterviewQaCategory[];
  /** 名前一致で紐づかない gap を既定で受け持つ gap_type。 */
  gapTypes: string[];
  /** このカテゴリが変わると再確認が要る下流カテゴリ (固定の宣言)。 */
  downstream: CockpitCategoryKey[];
}

export const COCKPIT_CATEGORIES: CategoryDefinition[] = [
  {
    key: "vision",
    number: "01",
    title: "Vision",
    caption: "誰の状態を、どう変えたいか",
    sections: ["vision"],
    // Vision に対応する Q&A カテゴリはサーバー側に存在しない
    // (purpose|capability|api|probe_flow|general)。無いものを推測で
    // 割り当てず、Vision は内容の有無と gap でだけ判定する。
    qaCategories: [],
    gapTypes: [],
    downstream: ["system_purpose", "capabilities", "probe_flow"],
  },
  {
    key: "system_purpose",
    number: "02",
    title: "System purpose",
    caption: "このシステムが担う役割",
    sections: ["system_purpose"],
    qaCategories: ["purpose"],
    gapTypes: ["docs_only", "source_doc_mismatch", "stale_explanation"],
    downstream: ["capabilities", "probe_flow"],
  },
  {
    key: "capabilities",
    number: "03",
    title: "Capabilities",
    caption: "目的を支える主要機能と要素",
    sections: ["core_capabilities", "capability_elements", "supporting_elements"],
    qaCategories: ["capability"],
    gapTypes: ["ambiguous_ownership", "code_only"],
    downstream: ["api_boundaries", "probe_flow"],
  },
  {
    key: "api_boundaries",
    number: "04",
    title: "API boundaries",
    caption: "外部との接点・責務境界",
    sections: ["api_boundaries"],
    qaCategories: ["api"],
    gapTypes: ["unclassified_entrypoint"],
    downstream: ["probe_flow"],
  },
  {
    key: "probe_flow",
    number: "05",
    title: "Probe flow",
    caption: "計測対象になりうるフロー",
    sections: ["probe_flow_candidates"],
    qaCategories: ["probe_flow"],
    gapTypes: ["missing_probe_flow"],
    downstream: [],
  },
];

const CATEGORY_BY_KEY = new Map(COCKPIT_CATEGORIES.map(c => [c.key, c]));

export function categoryTitle(key: CockpitCategoryKey): string {
  return CATEGORY_BY_KEY.get(key)?.title ?? key;
}

export const CATEGORY_STATUS_LABELS: Record<CockpitCategoryStatus, string> = {
  confirmed: "確認済み",
  review: "要確認",
  missing: "未設定",
};

// ── 未解決事項 ────────────────────────────────────────────────────────

export type CockpitPriority = "high" | "medium" | "low";

export const PRIORITY_LABELS: Record<CockpitPriority, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const PRIORITY_RANK: Record<CockpitPriority, number> = { high: 0, medium: 1, low: 2 };

function normalizePriority(value: string | null | undefined): CockpitPriority {
  return value === "high" || value === "low" ? value : "medium";
}

export interface CockpitUnresolvedItem {
  /** 一覧の React key。qa_id があればそれ、無ければ質問文。 */
  id: string;
  qaId: number | null;
  question: string;
  priority: CockpitPriority;
  /** 所属カテゴリ。どの 5 カテゴリにも属さない `general` は null。 */
  category: CockpitCategoryKey | null;
  categoryLabel: string;
  /** 「わからない」と回答済みで、再確認待ちの質問かどうか。 */
  unconfirmed: boolean;
}

// ── Q&A 進捗 ──────────────────────────────────────────────────────────

export interface CockpitQaProgress {
  answered: number;
  /** 「わからない」で確定回答が無く、再確認待ちの件数。 */
  awaiting: number;
  open: number;
  /** answered + awaiting + open。3 区分の合計と必ず一致する。 */
  total: number;
  /**
   * 合計から除外した件数。`revised` は後続行に置き換えられた履歴、
   * `skipped` は見送りが確定した質問で、どちらも「回答待ち」ではない。
   */
  excluded: number;
}

// ── 詳細・修正ペイン ──────────────────────────────────────────────────

/** 修正手段の識別子 (issue §4 の 3 種)。 */
export type CockpitActionKind = "answer_question" | "direct_edit" | "review_evidence";

export interface CockpitAction {
  kind: CockpitActionKind;
  title: string;
  description: string;
  /** 実行できないとき、その理由 (issue §4)。実行できるときは null。 */
  disabledReason: string | null;
  /** 遷移先の既存 UI (data-testid)。実行できないときは null。 */
  targetTestId: string | null;
}

export interface CockpitCategoryView {
  key: CockpitCategoryKey;
  number: string;
  title: string;
  caption: string;
  status: CockpitCategoryStatus;
  statusLabel: string;
  /** 内容の短いサマリー (issue §3)。 */
  summary: string;
  /** 対応が必要な場合の短い指示 (issue §3)。 */
  hint: string;
  itemCount: number;
  items: UnderstandingItem[];
  gaps: GapItem[];
  questions: CockpitUnresolvedItem[];
  evidence: { path: string; start_line: number; end_line: number; summary: string }[];
  relatedDocs: string[];
  downstream: CockpitCategoryKey[];
}

export interface CockpitModel {
  categories: CockpitCategoryView[];
  /** 完成度 (0-100 の整数)。`completionPercent` で算出する。 */
  completionPercent: number;
  reviewCount: number;
  missingCount: number;
  confirmedCount: number;
  categoryCount: number;
  unresolved: CockpitUnresolvedItem[];
  qa: CockpitQaProgress;
  /** 既定で選択するカテゴリ。未設定 → 要確認 → 先頭、の順で決定的に選ぶ。 */
  defaultCategory: CockpitCategoryKey;
  /** 「次にやること」の見出しと説明。 */
  nextStep: { title: string; description: string };
  /** 根拠の件数 (issue §7)。 */
  evidenceCounts: { code: number; docs: number };
}

// ── 集計 ──────────────────────────────────────────────────────────────

function sectionItems(
  understanding: CurrentUnderstanding | null | undefined,
  sections: UnderstandingSection[],
): UnderstandingItem[] {
  if (!understanding) return [];
  return sections.flatMap(section => understanding[section] ?? []);
}

/**
 * gap をカテゴリへ割り当てる。
 *
 * 1. `gap.name` が理解項目の名前と完全一致すればそのカテゴリ
 *    (`understanding_diff` と同じ「名前の完全一致」規則。類似度は使わない)
 * 2. 一致しなければ `gap_type` の既定カテゴリ
 * 3. どちらでもなければ、どのカテゴリにも属さない (null)
 */
function gapCategory(
  gap: GapItem,
  nameOwners: Map<string, CockpitCategoryKey>,
): CockpitCategoryKey | null {
  const byName = nameOwners.get(gap.name);
  if (byName) return byName;
  const byType = COCKPIT_CATEGORIES.find(c => c.gapTypes.includes(gap.gap_type));
  return byType ? byType.key : null;
}

function qaCategoryOwner(category: InterviewQaCategory): CockpitCategoryKey | null {
  const owner = COCKPIT_CATEGORIES.find(c => c.qaCategories.includes(category));
  return owner ? owner.key : null;
}

/** `OpenQuestion.category` はサーバー側で Q&A カテゴリと同じ有限集合。 */
function openQuestionCategory(value: string | null | undefined): CockpitCategoryKey | null {
  const known: InterviewQaCategory[] = ["purpose", "capability", "api", "probe_flow", "general"];
  const match = known.find(k => k === value);
  return match ? qaCategoryOwner(match) : null;
}

function unresolvedFromSources(
  openQuestions: OpenQuestion[] | null | undefined,
  qaItems: InterviewQaOut[] | null | undefined,
): CockpitUnresolvedItem[] {
  const items: CockpitUnresolvedItem[] = [];
  const seenQaIds = new Set<number>();
  const seenTexts = new Set<string>();

  for (const q of openQuestions ?? []) {
    const category = openQuestionCategory(q.category);
    const qaId = q.qa_id ?? null;
    if (qaId != null) seenQaIds.add(qaId);
    seenTexts.add(q.question);
    items.push({
      id: qaId != null ? `qa-${qaId}` : `oq-${q.question}`,
      qaId,
      question: q.question,
      priority: normalizePriority(q.priority),
      category,
      categoryLabel: category ? categoryTitle(category) : "全体",
      unconfirmed: false,
    });
  }

  for (const qa of qaItems ?? []) {
    // 未解決 = まだ回答が無い (`open`)、または「わからない」で確定回答が
    // 無い (`unconfirmed`)。`answered` は解決済み、`revised` は後続行に
    // 置き換えられた履歴、`skipped` は見送り確定なので一覧から除く
    // (issue §5「解決済み項目は一覧から除外する」)。
    if (qa.status !== "open" && qa.status !== "unconfirmed") continue;
    if (seenQaIds.has(qa.id)) continue;
    if (seenTexts.has(qa.question_text)) continue;
    seenQaIds.add(qa.id);
    seenTexts.add(qa.question_text);
    const category = qaCategoryOwner(qa.question_category);
    items.push({
      id: `qa-${qa.id}`,
      qaId: qa.id,
      question: qa.question_text,
      // Q&A 行は優先度を持たない。「わからない」で再確認待ちのものだけ
      // 通常の未回答より上に置き、それ以外は medium 相当に揃える。
      priority: qa.status === "unconfirmed" ? "high" : "medium",
      category,
      categoryLabel: category ? categoryTitle(category) : "全体",
      unconfirmed: qa.status === "unconfirmed",
    });
  }

  // 影響度順 (issue §5)。同順位は入力順を保つ安定ソート。
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const byPriority = PRIORITY_RANK[a.item.priority] - PRIORITY_RANK[b.item.priority];
      if (byPriority !== 0) return byPriority;
      return a.index - b.index;
    })
    .map(entry => entry.item);
}

export function qaProgress(qaItems: InterviewQaOut[] | null | undefined): CockpitQaProgress {
  let answered = 0;
  let awaiting = 0;
  let open = 0;
  let excluded = 0;
  for (const qa of qaItems ?? []) {
    if (qa.status === "answered") answered += 1;
    else if (qa.status === "unconfirmed") awaiting += 1;
    else if (qa.status === "open") open += 1;
    else excluded += 1;
  }
  return { answered, awaiting, open, total: answered + awaiting + open, excluded };
}

/**
 * 完成度 (%)。固定値ではなく 5 カテゴリの状態から算出する (issue §2)。
 *
 * confirmed = 1、review = 0.5、missing = 0 の重み付き平均を百分率にして
 * 四捨五入する。カテゴリが 0 件のときは 0%。
 */
export function completionPercent(statuses: CockpitCategoryStatus[]): number {
  if (statuses.length === 0) return 0;
  const weight = (status: CockpitCategoryStatus) =>
    status === "confirmed" ? 1 : status === "review" ? 0.5 : 0;
  const score = statuses.reduce((sum, status) => sum + weight(status), 0);
  return Math.round((score / statuses.length) * 100);
}

function summarizeCategory(
  items: UnderstandingItem[],
  status: CockpitCategoryStatus,
  gaps: GapItem[],
  questions: CockpitUnresolvedItem[],
): { summary: string; hint: string } {
  if (status === "missing") {
    return {
      summary: "未設定",
      hint: "内容がまだありません。質問に回答するか、直接編集して定義してください。",
    };
  }
  const names = items.slice(0, 2).map(i => i.name).filter(Boolean);
  const rest = items.length - names.length;
  const summary = names.length > 0
    ? `${names.join(" / ")}${rest > 0 ? ` ほか ${rest} 件` : ""}`
    : `${items.length} 件`;
  if (status === "confirmed") {
    return { summary, hint: "未解決の確認事項はありません。必要なら直接編集できます。" };
  }
  const first = questions[0]?.question ?? gaps[0]?.summary ?? gaps[0]?.name ?? "";
  const outstanding = questions.length + gaps.length;
  return {
    summary,
    hint: first
      ? `${first}${outstanding > 1 ? ` (ほか ${outstanding - 1} 件)` : ""}`
      : `確認が必要な項目が ${outstanding} 件あります。`,
  };
}

export interface CockpitInput {
  understanding: CurrentUnderstanding | null | undefined;
  gaps: GapItem[] | null | undefined;
  openQuestions: OpenQuestion[] | null | undefined;
  qaItems: InterviewQaOut[] | null | undefined;
}

export function buildCockpitModel(input: CockpitInput): CockpitModel {
  const unresolved = unresolvedFromSources(input.openQuestions, input.qaItems);

  // 名前 → カテゴリの索引。名前の完全一致でだけ引く。
  const nameOwners = new Map<string, CockpitCategoryKey>();
  for (const def of COCKPIT_CATEGORIES) {
    for (const item of sectionItems(input.understanding, def.sections)) {
      if (item.name && !nameOwners.has(item.name)) nameOwners.set(item.name, def.key);
    }
  }

  const gapsByCategory = new Map<CockpitCategoryKey, GapItem[]>();
  for (const gap of input.gaps ?? []) {
    const key = gapCategory(gap, nameOwners);
    if (!key) continue;
    const list = gapsByCategory.get(key) ?? [];
    list.push(gap);
    gapsByCategory.set(key, list);
  }

  const categories: CockpitCategoryView[] = COCKPIT_CATEGORIES.map(def => {
    const items = sectionItems(input.understanding, def.sections);
    const gaps = gapsByCategory.get(def.key) ?? [];
    const questions = unresolved.filter(q => q.category === def.key);
    const status: CockpitCategoryStatus =
      items.length === 0
        ? "missing"
        : gaps.length > 0 || questions.length > 0
          ? "review"
          : "confirmed";
    const { summary, hint } = summarizeCategory(items, status, gaps, questions);
    return {
      key: def.key,
      number: def.number,
      title: def.title,
      caption: def.caption,
      status,
      statusLabel: CATEGORY_STATUS_LABELS[status],
      summary,
      hint,
      itemCount: items.length,
      items,
      gaps,
      questions,
      evidence: items.flatMap(i => i.evidence ?? []),
      relatedDocs: [...new Set(items.flatMap(i => i.related_docs ?? []))],
      downstream: def.downstream,
    };
  });

  const missing = categories.filter(c => c.status === "missing");
  const review = categories.filter(c => c.status === "review");
  const defaultCategory = (missing[0] ?? review[0] ?? categories[0]).key;

  const qa = qaProgress(input.qaItems);
  const topUnresolved = unresolved[0];
  const nextStep = missing[0]
    ? {
        title: `${missing[0].title} を定義する`,
        description:
          "コードからは決められない項目です。質問への回答か直接編集で内容を埋めると、下流のカテゴリもつながります。",
      }
    : topUnresolved
      ? {
          title: `${topUnresolved.categoryLabel} の確認事項に答える`,
          description: topUnresolved.question,
        }
      : review[0]
        ? {
            title: `${review[0].title} の内容を確認する`,
            description: review[0].hint,
          }
        : {
            title: "確認が必要な項目はありません",
            description: "理解の全体像は揃っています。作業カードの主操作へ進んでください。",
          };

  return {
    categories,
    completionPercent: completionPercent(categories.map(c => c.status)),
    reviewCount: review.length,
    missingCount: missing.length,
    confirmedCount: categories.length - review.length - missing.length,
    categoryCount: categories.length,
    unresolved,
    qa,
    defaultCategory,
    nextStep,
    evidenceCounts: {
      code: categories.reduce((sum, c) => sum + c.evidence.length, 0),
      docs: new Set(categories.flatMap(c => c.relatedDocs)).size,
    },
  };
}

// ── 修正手段の可否 ────────────────────────────────────────────────────

/**
 * 既存 Interview 画面のどのパネルへ繋ぐか。新しい処理は一切足さない
 * (issue「実装方針」: 既存の回答・編集・根拠表示フローへ接続する)。
 */
export const ACTION_TARGET_TEST_IDS: Record<CockpitActionKind, string> = {
  // `W3` の作業面 (会話カードの 1 問 + Q&A 一覧)。
  answer_question: "work-surface-W3",
  // 自然言語でのまとめて修正 (`ChangeSetPanel`)。
  direct_edit: "change-set-panel",
  // 現在の理解と根拠 (`UnderstandingBrief` の折りたたみ)。
  review_evidence: "understanding-brief",
};

/**
 * 修正手段とその可否を決める。
 *
 * ワークフロー状態はサーバーが決めた値をそのまま受け取り、ここでは
 * 「その状態でその面が画面に出ているか」を固定表で見るだけである
 * (Issue #349 §3.3 の状態 × 役割マトリクスと同じ対応)。
 *
 * 実行できない手段は消さずに理由付きの disabled として残す (issue §4)。
 * これは「その状態の主操作」ではなく「この項目をどう直すか」の案内なので、
 * 前提未達の主操作を出さない原則 (spec P3) とは別物である -- 案内自体を
 * 消すと「今は直せない」ことすら分からなくなる。
 */
export function categoryActions(
  category: CockpitCategoryView,
  state: InterviewWorkflowState | null,
): CockpitAction[] {
  const stateLabel = state ? WORKFLOW_STATE_LABELS[state] : "セッション未選択";
  const editableStates: InterviewWorkflowState[] = ["W2", "W3", "W4"];
  const canAnswer = state === "W3" && category.questions.length > 0;
  const canEdit = state != null && editableStates.includes(state);
  const hasContent = category.itemCount > 0;

  return [
    {
      kind: "answer_question",
      title: "質問に回答する",
      description: "この項目について未解決の質問に、1 問ずつ答えます。",
      disabledReason: canAnswer
        ? null
        : category.questions.length === 0
          ? "この項目に未解決の質問はありません。"
          : `質問への回答は「${WORKFLOW_STATE_LABELS.W3}」の状態で行います (現在は「${stateLabel}」)。`,
      targetTestId: canAnswer ? ACTION_TARGET_TEST_IDS.answer_question : null,
    },
    {
      kind: "direct_edit",
      title: "直接編集する",
      description: "修正内容を文章で書き、変更セットとして確認してから反映します。",
      disabledReason: canEdit
        ? null
        : `直接編集は理解・質問・ズレの確認中だけ行えます (現在は「${stateLabel}」)。`,
      targetTestId: canEdit ? ACTION_TARGET_TEST_IDS.direct_edit : null,
    },
    {
      kind: "review_evidence",
      title: "根拠を確認する",
      description: "この項目の判断根拠 (コード・ドキュメント) を確認します。",
      disabledReason: hasContent
        ? null
        : "まだ内容が無いため、確認できる根拠がありません。",
      targetTestId: hasContent ? ACTION_TARGET_TEST_IDS.review_evidence : null,
    },
  ];
}
