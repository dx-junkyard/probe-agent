// Issue #356 §3 / Issue #363: 理解の全体マップ。
//
// 5 カテゴリを横並びのカードで出し、選択すると詳細・修正ペインが切り替わる。
// 状態判定は `buildCockpitModel` 側にあり、ここは描画と選択だけを持つ。
//
// Issue #363: カードの初期表示は「番号・カテゴリ名・状態・1 行要約」だけに
// 絞る。説明文 (caption) と対応ヒント (hint) は詳細ペインが引き受けるので、
// ここには出さない。5 枚が同じ密度で並ぶとどれに注意すべきか分からなくなる
// ため、`missing` / `review` は枠の強調とテキスト marker (「要対応」) で
// 優先度を示す。並び順は 5 カテゴリの固定順のままで、状態で並べ替えない。
//
// アクセシビリティ: カードは native button なのでキーボードで到達・選択でき、
// 選択状態は `aria-pressed` で伝える。状態は色だけでなく必ずテキストを伴い、
// `aria-label` にもカテゴリ名と状態テキストを含める (バッジのマークアップに
// 依存せず状態が読める)。矢印キー / Home / End はフォーカス移動だけを行い、
// 選択は native button の Enter / Space (= click) に任せる。

import { useRef } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, CircleDashed, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  CATEGORY_STATUS_PENDING_LABEL,
  type CockpitCategoryKey,
  type CockpitCategoryStatus,
  type CockpitCategoryView,
  type CockpitQaFetchStatus,
} from "./model";

const STATUS_VARIANT: Record<CockpitCategoryStatus, "success" | "warning" | "destructive"> = {
  confirmed: "success",
  review: "warning",
  missing: "destructive",
};

const STATUS_ICON: Record<CockpitCategoryStatus, typeof CheckCircle2> = {
  confirmed: CheckCircle2,
  review: AlertTriangle,
  missing: CircleDashed,
};

/** 対応が必要な状態 (色に依存しないテキスト marker を添える)。 */
const ATTENTION_STATUSES: CockpitCategoryStatus[] = ["missing", "review"];

/** 枠の強調。色だけに意味を持たせないので、必ず「要対応」ラベルと併用する。 */
const ATTENTION_RING: Record<CockpitCategoryStatus, string> = {
  missing: "ring-1 ring-destructive/50",
  review: "ring-1 ring-amber-500/60",
  confirmed: "",
};

/**
 * 状態バッジ。状態は `confirmed` / `review` / `missing` の 3 値だけ。
 * 確定できないカテゴリ (`status === null`) には状態ラベルを出さず、データを
 * 取得できていないという別軸の説明を出す (issue §3 の 3 状態契約)。
 */
export function CategoryStatusBadge({
  status,
  label,
}: {
  status: CockpitCategoryStatus | null;
  label: string | null;
}) {
  if (status == null) {
    return (
      <span
        className="inline-flex items-center gap-1 text-xs text-muted-foreground"
        data-testid="cockpit-status-pending"
      >
        {CATEGORY_STATUS_PENDING_LABEL}
      </span>
    );
  }
  const Icon = STATUS_ICON[status];
  return (
    <Badge variant={STATUS_VARIANT[status]} className="gap-1 font-normal" data-testid={`cockpit-status-${status}`}>
      <Icon className="h-3 w-3" aria-hidden="true" />
      {label ?? ""}
    </Badge>
  );
}

export function UnderstandingMap({
  categories,
  selected,
  qaFetchStatus = "ready",
  onSelect,
  onRetryQa,
  onGoToDetail,
}: {
  categories: CockpitCategoryView[];
  selected: CockpitCategoryKey;
  /** Q&A の取得状態。`ready` 以外では確定できない状態がある。 */
  qaFetchStatus?: CockpitQaFetchStatus;
  onSelect: (key: CockpitCategoryKey) => void;
  onRetryQa?: () => void;
  /**
   * 選択中カテゴリの詳細ペインへ移動する (Issue #363)。詳細ペインがマップの
   * 横に並ばない幅 (Tablet / Mobile) と、キーボード操作の到達手段。配置その
   * ものはページ側が持つので、ここは「移動したい」という意思だけを渡す。
   */
  onGoToDetail?: () => void;
}) {
  // 矢印キーでのフォーカス移動用。選択そのものは native button に任せる。
  const cardRefs = useRef<(HTMLButtonElement | null)[]>([]);

  function moveFocus(from: number, key: string): boolean {
    const last = categories.length - 1;
    if (last < 0) return false;
    let next: number;
    if (key === "ArrowLeft" || key === "ArrowUp") next = from > 0 ? from - 1 : last;
    else if (key === "ArrowRight" || key === "ArrowDown") next = from < last ? from + 1 : 0;
    else if (key === "Home") next = 0;
    else if (key === "End") next = last;
    else return false;
    cardRefs.current[next]?.focus();
    return true;
  }

  return (
    <Card data-testid="cockpit-understanding-map">
      <CardHeader>
        <CardTitle className="text-sm">理解の全体マップ</CardTitle>
        <CardDescription>
          カードを選択すると、状況・確認が必要な理由・根拠・修正方法を詳細ペインに表示します。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* 一部のカテゴリが確定できない理由と復旧手段を、マップ全体に 1 度
            だけ出す。カード側は状態ラベルを保留するだけにする。 */}
        {qaFetchStatus !== "ready" && (
          <div
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-dashed p-2 text-xs text-muted-foreground"
            data-testid="cockpit-map-qa-unavailable"
          >
            <span>
              {qaFetchStatus === "loading"
                ? "Q&A を読み込み中です。未解決の質問に依存する状態は、読み込み後に確定します。"
                : "Q&A を取得できていないため、未解決の質問に依存する状態を確定できません。"}
            </span>
            {qaFetchStatus === "unavailable" && onRetryQa && (
              <Button size="sm" variant="outline" onClick={onRetryQa} data-testid="cockpit-map-qa-retry">
                <RefreshCw className="h-4 w-4 mr-1" />
                再試行
              </Button>
            )}
          </div>
        )}
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
          {categories.map((category, index) => {
            const isSelected = category.key === selected;
            const statusText = category.statusLabel ?? CATEGORY_STATUS_PENDING_LABEL;
            const needsAttention =
              category.status != null && ATTENTION_STATUSES.includes(category.status);
            const ring = category.status != null ? ATTENTION_RING[category.status] : "";
            return (
              <li key={category.key} className="min-w-0">
                <button
                  type="button"
                  ref={element => {
                    cardRefs.current[index] = element;
                  }}
                  onClick={() => onSelect(category.key)}
                  onKeyDown={event => {
                    // Enter / Space はここで扱わない (native button の
                    // クリックとして選択に届く)。
                    if (moveFocus(index, event.key)) event.preventDefault();
                  }}
                  aria-pressed={isSelected}
                  aria-label={`${category.number} ${category.title}: ${statusText}${
                    needsAttention ? " (要対応)" : ""
                  }`}
                  className={`h-full w-full rounded-md border p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${
                    needsAttention ? ring : ""
                  } ${isSelected ? "border-primary bg-accent" : "hover:bg-accent/50"}`}
                  data-testid={`cockpit-category-card-${category.key}`}
                >
                  <span className="flex items-center justify-between gap-1">
                    <span className="text-[10px] font-semibold tracking-widest text-muted-foreground">
                      {category.number}
                    </span>
                    {needsAttention && (
                      <span
                        className="rounded-sm border border-current px-1 text-[10px] font-semibold"
                        data-testid={`cockpit-category-attention-${category.key}`}
                      >
                        要対応
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 block truncate text-sm font-semibold" title={category.title}>
                    {category.title}
                  </span>
                  <span className="mt-1 block">
                    <CategoryStatusBadge status={category.status} label={category.statusLabel} />
                  </span>
                  {/* 要約は 1 行。全文は title 属性で読める (Issue #363)。 */}
                  <span
                    className="mt-1 block truncate text-xs font-medium"
                    title={category.summary}
                    data-testid={`cockpit-category-summary-${category.key}`}
                  >
                    {category.summary}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {onGoToDetail && (
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="outline"
              onClick={onGoToDetail}
              data-testid="cockpit-map-go-detail"
            >
              選択中のカテゴリの詳細へ
              <ChevronRight className="ml-1 h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
