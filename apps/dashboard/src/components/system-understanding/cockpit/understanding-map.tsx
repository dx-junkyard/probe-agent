// Issue #356 §3: 理解の全体マップ。
//
// 5 カテゴリを横並びのカードで出し、選択すると詳細・修正ペインが切り替わる。
// 状態判定は `buildCockpitModel` 側にあり、ここは描画と選択だけを持つ。
//
// アクセシビリティ: カードは native button なのでキーボードで到達・選択でき、
// 選択状態は `aria-pressed` で伝える。状態は色だけでなく必ずテキストを伴う。

import { AlertTriangle, CheckCircle2, CircleDashed, RefreshCw } from "lucide-react";
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
}: {
  categories: CockpitCategoryView[];
  selected: CockpitCategoryKey;
  /** Q&A の取得状態。`ready` 以外では確定できない状態がある。 */
  qaFetchStatus?: CockpitQaFetchStatus;
  onSelect: (key: CockpitCategoryKey) => void;
  onRetryQa?: () => void;
}) {
  return (
    <Card data-testid="cockpit-understanding-map">
      <CardHeader>
        <CardTitle className="text-sm">理解の全体マップ</CardTitle>
        <CardDescription>
          カードを選択すると、状態・根拠・修正方法を詳細ペインに表示します。
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
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {categories.map(category => {
            const isSelected = category.key === selected;
            return (
              <li key={category.key}>
                <button
                  type="button"
                  onClick={() => onSelect(category.key)}
                  aria-pressed={isSelected}
                  className={`h-full w-full rounded-md border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${
                    isSelected ? "border-primary bg-accent" : "hover:bg-accent/50"
                  }`}
                  data-testid={`cockpit-category-card-${category.key}`}
                >
                  <span className="block text-[10px] font-semibold tracking-widest text-muted-foreground">
                    {category.number}
                  </span>
                  <span className="mt-0.5 block text-sm font-semibold">{category.title}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{category.caption}</span>
                  <span className="mt-2 block">
                    <CategoryStatusBadge status={category.status} label={category.statusLabel} />
                  </span>
                  <span
                    className="mt-2 block text-xs font-medium break-words"
                    data-testid={`cockpit-category-summary-${category.key}`}
                  >
                    {category.summary}
                  </span>
                  <span className="mt-1 block text-xs text-muted-foreground break-words">
                    {category.hint}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
