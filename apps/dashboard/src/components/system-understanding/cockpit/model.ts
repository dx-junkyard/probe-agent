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

/**
 * カテゴリの状態 (issue §3)。**この 3 値だけ**で、増やさない。色だけでなく
 * 必ずラベルを伴わせる。
 *
 * 「データが取れていない」はカテゴリの業務状態ではないので、ここには入れず
 * `CockpitQaFetchStatus` に持たせる。確定できないカテゴリは
 * `CockpitCategoryView.status` が `null` (状態ラベルを保留) になる。
 */
export type CockpitCategoryStatus = "confirmed" | "review" | "missing";

/**
 * Q&A 一覧 (`GET /interview/sessions/{id}/qa`) の取得状態。
 *
 * 質問合計・未解決件数・進捗はこの応答だけが根拠なので、取得前や失敗時に
 * 0 件という確定値を出してはならない (0 件と未取得は別物)。カテゴリ状態と
 * は別の軸で、混ぜない。
 */
export type CockpitQaFetchStatus = "ready" | "loading" | "unavailable";

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

/** 状態を確定できないときの説明 (状態ラベルではない)。 */
export const CATEGORY_STATUS_PENDING_LABEL = "Q&A 未取得のため保留";

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
  /**
   * `skipped` = 「後で回答する」として明示的に見送られた質問。サーバー側では
   * `open -> skipped` の一時状態で、`resume` でいつでも `open` に戻せる
   * (`routes/interview.py` の skip/resume)。回答は済んでいないので未解決
   * として数える。
   */
  deferred: boolean;
}

/**
 * 未解決事項のグループ (issue #359)。
 *
 * **グループのキーは有限のカテゴリキーだけ** (`CockpitCategoryKey` の 5 値と
 * `null` → `cat-general`)。issue の「同じ対象・カテゴリ・論点の質問をグルー
 * ピングする」は、既に有限でサーバー由来のカテゴリへの所属としてのみ実現し、
 * 文面の類似度・埋め込み・キーワードスコアでは決して判定しない
 * (Core Design Principle 6: 類似度やキーワードスコアは候補の retrieval には
 * 使えても、最終的な開かれた判断になってはならない)。
 */
export interface CockpitUnresolvedGroup {
  /** グループの識別子。`cat-<CockpitCategoryKey>` または `cat-general`。 */
  id: string;
  category: CockpitCategoryKey | null;
  categoryLabel: string;
  /** 代表質問 = グループ内で最優先の 1 件 (= items[0])。 */
  representative: CockpitUnresolvedItem;
  items: CockpitUnresolvedItem[];
  /** グループの優先度 = items[0].priority。 */
  priority: CockpitPriority;
  /** 状態内訳。open = total - unconfirmed - deferred。 */
  counts: { total: number; unconfirmed: number; deferred: number; open: number };
}

/**
 * 未解決事項をカテゴリ単位でまとめる。
 *
 * 入力は `buildCockpitModel` が優先度順 (同順位は入力順) に並べ終えた配列な
 * ので、ここでは並べ替えない。グループの並びは「代表質問が入力配列に現れた
 * 順」= 代表質問の優先度順であり、グループ内の並びも入力順のまま (安定)。
 */
export function groupUnresolvedItems(
  items: CockpitUnresolvedItem[],
): CockpitUnresolvedGroup[] {
  const order: string[] = [];
  const byGroupId = new Map<string, CockpitUnresolvedItem[]>();
  for (const item of items) {
    // 有限集合のキーだけでまとめる。質問文は一切見ない。
    const id = item.category ? `cat-${item.category}` : "cat-general";
    const bucket = byGroupId.get(id);
    if (bucket) {
      bucket.push(item);
    } else {
      byGroupId.set(id, [item]);
      order.push(id);
    }
  }
  return order.map(id => {
    const groupItems = byGroupId.get(id) ?? [];
    const representative = groupItems[0];
    const unconfirmed = groupItems.filter(i => i.unconfirmed).length;
    const deferred = groupItems.filter(i => i.deferred).length;
    return {
      id,
      category: representative.category,
      categoryLabel: representative.categoryLabel,
      representative,
      items: groupItems,
      priority: representative.priority,
      counts: {
        total: groupItems.length,
        unconfirmed,
        deferred,
        open: groupItems.length - unconfirmed - deferred,
      },
    };
  });
}

// ── Q&A 進捗 ──────────────────────────────────────────────────────────

export interface CockpitQaProgress {
  answered: number;
  /** 「わからない」で確定回答が無く、再確認待ちの件数。 */
  awaiting: number;
  /** 未回答 (`open`) と「後で回答」(`skipped`) の合計。 */
  open: number;
  /** `open` の内訳のうち「後で回答」として見送られた件数 (再掲)。 */
  deferred: number;
  /** answered + awaiting + open。3 区分の合計と必ず一致する。 */
  total: number;
  /**
   * 合計から除外した件数。除外するのは `revised` (後続行に置き換えられた
   * 履歴) だけ。`skipped` は「後で回答する」一時状態で回答が済んでいない
   * ため、除外せず `open` に数える -- 除外すると未回答が残っているのに
   * 未解決 0 件・完成度 100% と表示されてしまう。
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
  /**
   * 遷移先の既存 UI (data-testid) を優先度順に並べたもの。実行できないとき
   * は空配列。呼び出し側は先頭から順に、実際に描かれているものを探す。
   */
  targetTestIds: string[];
}

export interface CockpitCategoryView {
  key: CockpitCategoryKey;
  number: string;
  title: string;
  caption: string;
  /**
   * 業務状態。Q&A を取得できておらず、「未解決の質問が無い」ことを根拠に
   * するしか確定手段が無いカテゴリでは `null` (= 状態ラベルを保留)。
   * `missing` (内容が無い) と gap 由来の `review` は session 詳細だけで
   * 確定できるので、Q&A が取れていなくてもそのまま出る。
   */
  status: CockpitCategoryStatus | null;
  statusLabel: string | null;
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

// ── 次にやること ──────────────────────────────────────────────────────

/**
 * 「次にやること」の種別 (issue #360)。**この 4 値だけ**の有限集合で、
 * スコアや確信度ではない。
 */
export type CockpitNextStepKind =
  | "answer_question"  // 未解決の確認事項に答える
  | "fix_category"     // 未設定 / 要確認のカテゴリを直す
  | "retry_qa"         // Q&A を取得できていないので再取得する
  | "state_primary";   // 確認事項が無い。状態の主操作へ進む

export interface CockpitNextStep {
  kind: CockpitNextStepKind;
  title: string;
  description: string;
  /** 対象カテゴリ。無いときは null。 */
  category: CockpitCategoryKey | null;
  /** 対象の未解決項目 (`answer_question` のときだけ)。 */
  item: CockpitUnresolvedItem | null;
  /** CTA 文言。`state_primary` / `retry_qa` はページ側が文言と操作を持つので null。 */
  actionLabel: string | null;
  /** 移動先候補 (優先度順)。空配列ならページ側が移動先を決める。 */
  targetTestIds: string[];
}

export interface CockpitModel {
  categories: CockpitCategoryView[];
  /**
   * 完成度 (0-100 の整数)。判定できないカテゴリがある (= Q&A 未取得) 場合は
   * `null`。実際より高い値を出すくらいなら数字を出さない。
   */
  completionPercent: number | null;
  /**
   * 要確認の件数。Q&A が `ready` でないときは「今わかっている分」の下限で、
   * 確定値ではない (`countsSettled` が false)。
   */
  reviewCount: number;
  /** 未設定の件数。内容の有無だけで決まるので常に確定値。 */
  missingCount: number;
  confirmedCount: number;
  /** 状態を確定できなかったカテゴリ数 (Q&A 未取得時のみ 0 より大きい)。 */
  pendingCount: number;
  /** 件数が確定値かどうか。false なら「N 件以上」として見せる。 */
  countsSettled: boolean;
  categoryCount: number;
  /** Q&A の取得状態。`ready` 以外では質問由来の件数を確定値として出さない。 */
  qaFetchStatus: CockpitQaFetchStatus;
  unresolved: CockpitUnresolvedItem[];
  /**
   * 未解決事項をカテゴリ単位でまとめたもの (issue #359)。段階的開示はこの
   * 単位で行う。`unresolved` はそのまま残す (件数と最優先項目の判定に使う)。
   */
  unresolvedGroups: CockpitUnresolvedGroup[];
  /** Q&A 進捗。取得できていないときは `null` (0 件ではない)。 */
  qa: CockpitQaProgress | null;
  /** 既定で選択するカテゴリ。未設定 → 要確認 → 先頭、の順で決定的に選ぶ。 */
  defaultCategory: CockpitCategoryKey;
  /** 「次にやること」。見出しだけでなく、実行できる CTA と移動先を持つ。 */
  nextStep: CockpitNextStep;
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

/** 未解決として扱う Q&A の状態 (`answered` / `revised` は解決済み・履歴)。 */
function isUnresolvedQa(qa: InterviewQaOut): boolean {
  // 未解決 = まだ回答が無い (`open`)、「後で回答」として見送られている
  // (`skipped`: `resume` で `open` に戻せる一時状態)、または「わからない」
  // で確定回答が無い (`unconfirmed`)。除外するのは `answered` (解決済み)
  // と `revised` (後続行に置き換えられた履歴) だけ
  // (issue §5「解決済み項目は一覧から除外する」)。
  return qa.status === "open" || qa.status === "unconfirmed" || qa.status === "skipped";
}

/** Q&A 行の状態から決まる表示属性。`open_questions` 行にも同じ規則を当てる。 */
function qaStatusFlags(qa: InterviewQaOut): {
  unconfirmed: boolean;
  deferred: boolean;
  priority: CockpitPriority | null;
} {
  return {
    unconfirmed: qa.status === "unconfirmed",
    deferred: qa.status === "skipped",
    // 「わからない」で再確認待ちのものは通常の未回答より上、開発者が自分で
    // 見送った「後で回答」は下。`open` は Q&A 行だけでは優先度を持たない。
    priority: qa.status === "unconfirmed" ? "high" : qa.status === "skipped" ? "low" : null,
  };
}

function unresolvedFromSources(
  openQuestions: OpenQuestion[] | null | undefined,
  qaItems: InterviewQaOut[] | null | undefined,
): CockpitUnresolvedItem[] {
  const items: CockpitUnresolvedItem[] = [];
  const seenQaIds = new Set<number>();
  const seenTexts = new Set<string>();

  // 同じ質問は `session.open_questions` と `interview_qa` の両方に存在し、
  // 状態を持っているのは Q&A 行だけである -- skip / resume は
  // `interview_qa.status` しか更新せず、`open_questions` の JSON エントリは
  // 元のまま残る (`routes/interview.py`)。したがって重複は「後から来た方を
  // 捨てる」のではなく、Q&A 側の状態を `open_questions` 行へ反映して 1 件に
  // まとめる。捨てるだけだと skipped/unconfirmed が通常の未回答に見える。
  const qaById = new Map<number, InterviewQaOut>();
  const qaByText = new Map<string, InterviewQaOut>();
  for (const qa of qaItems ?? []) {
    qaById.set(qa.id, qa);
    if (!qaByText.has(qa.question_text)) qaByText.set(qa.question_text, qa);
  }

  for (const q of openQuestions ?? []) {
    const qaId = q.qa_id ?? null;
    const matched = (qaId != null ? qaById.get(qaId) : undefined) ?? qaByText.get(q.question);
    // Q&A 側で解決済み (`answered`) / 置き換え済み (`revised`) になっている
    // 質問は、`open_questions` に残っていても未解決ではない。
    if (matched && !isUnresolvedQa(matched)) continue;
    const category = openQuestionCategory(q.category);
    if (qaId != null) seenQaIds.add(qaId);
    if (matched) seenQaIds.add(matched.id);
    seenTexts.add(q.question);
    const flags = matched
      ? qaStatusFlags(matched)
      : { unconfirmed: false, deferred: false, priority: null };
    items.push({
      id: qaId != null ? `qa-${qaId}` : `oq-${q.question}`,
      qaId: qaId ?? matched?.id ?? null,
      question: q.question,
      // 状態由来の優先度 (再確認待ち / 後で回答) があればそちらを優先し、
      // 無ければ open question 自身の優先度を使う。
      priority: flags.priority ?? normalizePriority(q.priority),
      category,
      categoryLabel: category ? categoryTitle(category) : "全体",
      unconfirmed: flags.unconfirmed,
      deferred: flags.deferred,
    });
  }

  for (const qa of qaItems ?? []) {
    if (!isUnresolvedQa(qa)) continue;
    if (seenQaIds.has(qa.id)) continue;
    if (seenTexts.has(qa.question_text)) continue;
    seenQaIds.add(qa.id);
    seenTexts.add(qa.question_text);
    const category = qaCategoryOwner(qa.question_category);
    const flags = qaStatusFlags(qa);
    items.push({
      id: `qa-${qa.id}`,
      qaId: qa.id,
      question: qa.question_text,
      priority: flags.priority ?? "medium",
      category,
      categoryLabel: category ? categoryTitle(category) : "全体",
      unconfirmed: flags.unconfirmed,
      deferred: flags.deferred,
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
  let deferred = 0;
  let excluded = 0;
  for (const qa of qaItems ?? []) {
    if (qa.status === "answered") answered += 1;
    else if (qa.status === "unconfirmed") awaiting += 1;
    else if (qa.status === "open") open += 1;
    else if (qa.status === "skipped") {
      // 「後で回答」。回答は済んでいないので未回答に数え、内訳として再掲する。
      open += 1;
      deferred += 1;
    } else excluded += 1;
  }
  return { answered, awaiting, open, deferred, total: answered + awaiting + open, excluded };
}

/**
 * 完成度 (%)。固定値ではなく 5 カテゴリの状態から算出する (issue §2)。
 *
 * confirmed = 1、review = 0.5、missing = 0 の重み付き平均を百分率にして
 * 四捨五入する。カテゴリが 0 件のときは 0%。
 *
 * 状態を確定できないカテゴリ (`null`) が 1 つでもあれば `null` を返す。根拠
 * の一部 (Q&A) が読めていない状態で数字を出すと、実際より高い完成度を確定値
 * として見せることになる。
 */
export function completionPercent(
  statuses: Array<CockpitCategoryStatus | null>,
): number | null {
  if (statuses.length === 0) return 0;
  if (statuses.some(status => status == null)) return null;
  const weight = (status: CockpitCategoryStatus | null) =>
    status === "confirmed" ? 1 : status === "review" ? 0.5 : 0;
  const score = statuses.reduce((sum, status) => sum + weight(status), 0);
  return Math.round((score / statuses.length) * 100);
}

function summarizeCategory(
  items: UnderstandingItem[],
  status: CockpitCategoryStatus | null,
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
  if (status == null) {
    return {
      summary,
      hint: "Q&A を取得できていないため、未解決の確認事項が残っているか判定できません。",
    };
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
  /**
   * Q&A 一覧の取得状態。省略時は `ready` (取得済み)。`ready` 以外のとき、
   * 質問由来の件数を 0 件という確定値として出さない。
   */
  qaFetchStatus?: CockpitQaFetchStatus;
}

/** カテゴリを直す CTA の移動先 (詳細ペイン)。 */
const NEXT_STEP_CATEGORY_TARGET = "cockpit-detail-panel";

/**
 * 「次にやること」を決める (issue #360)。
 *
 * **決定的な first-match の固定表**であり、スコアでも確信度でもない。
 * 分岐は 6 行で、結果は `CockpitNextStepKind` の 4 値に閉じている
 * (Core Design Principle 6)。
 *
 * 並び順の理由は「今すぐ実行できるものを先に出す」:
 *   1. Q&A が取得できていない → 何件残っているか判定できないので、まず再取得。
 *   2. Q&A を読み込み中 → 確定前に何も勧められない。状態の主操作のまま待つ。
 *   3. 未設定カテゴリ → 内容が無いので、質問に答えても埋まらない。定義が先。
 *   4. 未解決の確認事項 → 既に用意された質問に答えるだけで進む、最も即時に
 *      実行できる操作。要確認カテゴリの「内容を見直す」より手が動く。
 *   5. 要確認カテゴリ → 具体的な質問は無いが、内容の確認が要る。
 *   6. どれも無い → 状態自身の主操作へ。
 */
function decideNextStep(args: {
  missing: CockpitCategoryView[];
  review: CockpitCategoryView[];
  unresolved: CockpitUnresolvedItem[];
  qaFetchStatus: CockpitQaFetchStatus;
}): CockpitNextStep {
  const { missing, review, unresolved, qaFetchStatus } = args;

  if (qaFetchStatus === "unavailable") {
    return {
      kind: "retry_qa",
      title: "Q&A を取得できませんでした",
      description:
        "未解決の確認事項と回答状況を表示できないため、残りがあるか判定できません。再試行してください。",
      category: null,
      item: null,
      // 再取得の操作と文言はページ側 (Q&A カード) が持つ。
      actionLabel: null,
      targetTestIds: [],
    };
  }
  if (qaFetchStatus === "loading") {
    return {
      kind: "state_primary",
      title: "Q&A を読み込んでいます",
      description: "未解決の確認事項と回答状況は、読み込み後に表示されます。",
      category: null,
      item: null,
      actionLabel: null,
      targetTestIds: [],
    };
  }
  const firstMissing = missing[0];
  if (firstMissing) {
    return {
      kind: "fix_category",
      title: `${firstMissing.title} を定義する`,
      description:
        "コードからは決められない項目です。質問への回答か直接編集で内容を埋めると、下流のカテゴリもつながります。",
      category: firstMissing.key,
      item: null,
      actionLabel: `${firstMissing.title} を定義する`,
      // 詳細ペイン (修正手段の案内) へ移動する。実際に何ができるかは
      // `categoryActions` が状態から決めるので、ここでは面だけを指す。
      targetTestIds: [NEXT_STEP_CATEGORY_TARGET],
    };
  }
  const topUnresolved = unresolved[0];
  if (topUnresolved) {
    return {
      kind: "answer_question",
      title: `${topUnresolved.categoryLabel} の確認事項に答える`,
      description: topUnresolved.question,
      category: topUnresolved.category,
      item: topUnresolved,
      actionLabel: "この確認事項に回答する",
      targetTestIds: unresolvedTargets(topUnresolved),
    };
  }
  const firstReview = review[0];
  if (firstReview) {
    return {
      kind: "fix_category",
      title: `${firstReview.title} の内容を確認する`,
      description: firstReview.hint,
      category: firstReview.key,
      item: null,
      actionLabel: `${firstReview.title} を確認する`,
      targetTestIds: [NEXT_STEP_CATEGORY_TARGET],
    };
  }
  return {
    kind: "state_primary",
    title: "確認が必要な項目はありません",
    description: "理解の全体像は揃っています。この状態の主操作へ進んでください。",
    category: null,
    item: null,
    actionLabel: null,
    targetTestIds: [],
  };
}

export function buildCockpitModel(input: CockpitInput): CockpitModel {
  const qaFetchStatus = input.qaFetchStatus ?? "ready";
  const qaReady = qaFetchStatus === "ready";
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
    // 内容の有無 (`missing`) と gap 由来の `review` は session 詳細だけで
    // 判定できる。「未解決の質問が無い」ことを根拠にする `confirmed` だけが
    // Q&A に依存するので、Q&A を取得できていないときは状態を確定させず
    // `null` (ラベル保留) にする。状態の有限集合は 3 値のまま増やさない。
    const status: CockpitCategoryStatus | null =
      items.length === 0
        ? "missing"
        : gaps.length > 0 || questions.length > 0
          ? "review"
          : qaReady
            ? "confirmed"
            : null;
    const { summary, hint } = summarizeCategory(items, status, gaps, questions);
    return {
      key: def.key,
      number: def.number,
      title: def.title,
      caption: def.caption,
      status,
      statusLabel: status ? CATEGORY_STATUS_LABELS[status] : null,
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
  const pending = categories.filter(c => c.status == null);
  const defaultCategory = (missing[0] ?? review[0] ?? categories[0]).key;

  const nextStep = decideNextStep({ missing, review, unresolved, qaFetchStatus });

  return {
    categories,
    completionPercent: completionPercent(categories.map(c => c.status)),
    reviewCount: review.length,
    missingCount: missing.length,
    confirmedCount: categories.filter(c => c.status === "confirmed").length,
    pendingCount: pending.length,
    countsSettled: qaReady,
    categoryCount: categories.length,
    qaFetchStatus,
    unresolved,
    unresolvedGroups: groupUnresolvedItems(unresolved),
    // 0 件と「取得できていない」を同じ表示にしないため、未取得は null。
    qa: qaReady ? qaProgress(input.qaItems) : null,
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
  // `W3` の作業面 (会話カードの 1 問 + Q&A 一覧)。個々の質問へは
  // `unresolvedTargets()` が返す `qa-item-<id>` を優先して使う。
  answer_question: "work-surface-W3",
  // 自然言語でのまとめて修正 (`ChangeSetPanel`)。
  direct_edit: "change-set-panel",
  // 現在の理解と根拠 (`UnderstandingBrief` の折りたたみ)。
  review_evidence: "understanding-brief",
};

/**
 * 未解決事項 1 件に対する移動先候補を、優先度順に返す。
 *
 * 「この項目を開く」で作業面の先頭ボタンに飛ぶと、複数の質問があるときに
 * 選んだ質問と違うものへフォーカスが当たる。Q&A 行 (`qa-item-<id>`) が
 * 描かれていればそこへ移動し、無い場合だけ作業面 → 未解決一覧の順で
 * フォールバックする。実際にどれが存在するかは呼び出し側 (DOM) が決める。
 */
export function unresolvedTargets(item: CockpitUnresolvedItem | null): string[] {
  const targets: string[] = [];
  if (item?.qaId != null) targets.push(`qa-item-${item.qaId}`);
  targets.push(ACTION_TARGET_TEST_IDS.answer_question);
  targets.push("cockpit-unresolved-items");
  return targets;
}

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
          // 状態を確定できていない = Q&A が読めていないので、「質問は
          // ありません」と言い切れない。
          ? category.status == null
            ? "Q&A を取得できていないため、未解決の質問があるか判定できません。"
            : "この項目に未解決の質問はありません。"
          : `質問への回答は「${WORKFLOW_STATE_LABELS.W3}」の状態で行います (現在は「${stateLabel}」)。`,
      // このカテゴリで最も優先度の高い質問そのものへ移動する。
      targetTestIds: canAnswer ? unresolvedTargets(category.questions[0]) : [],
    },
    {
      kind: "direct_edit",
      title: "直接編集する",
      description: "修正内容を文章で書き、変更セットとして確認してから反映します。",
      disabledReason: canEdit
        ? null
        : `直接編集は理解・質問・ズレの確認中だけ行えます (現在は「${stateLabel}」)。`,
      targetTestIds: canEdit ? [ACTION_TARGET_TEST_IDS.direct_edit] : [],
    },
    {
      kind: "review_evidence",
      title: "根拠を確認する",
      description: "この項目の判断根拠 (コード・ドキュメント) を確認します。",
      disabledReason: hasContent
        ? null
        : "まだ内容が無いため、確認できる根拠がありません。",
      targetTestIds: hasContent ? [ACTION_TARGET_TEST_IDS.review_evidence] : [],
    },
  ];
}
