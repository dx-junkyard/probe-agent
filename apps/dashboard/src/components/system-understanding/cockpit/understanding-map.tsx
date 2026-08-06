// Issue #356 §3: 理解の全体マップ。
//
// 5 カテゴリを横並びのカードで出し、選択すると詳細・修正ペインが切り替わる。
// 状態判定は `buildCockpitModel` 側にあり、ここは描画と選択だけを持つ。
//
// アクセシビリティ: カードは native button なのでキーボードで到達・選択でき、
// 選択状態は `aria-pressed` で伝える。状態は色だけでなく必ずテキストを伴う。

import { AlertTriangle, CheckCircle2, CircleDashed, HelpCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { CockpitCategoryKey, CockpitCategoryStatus, CockpitCategoryView } from "./model";

const STATUS_VARIANT: Record<
  CockpitCategoryStatus,
  "success" | "warning" | "destructive" | "outline"
> = {
  confirmed: "success",
  review: "warning",
  missing: "destructive",
  // 判定不能は「悪い値」ではないので、要確認と同じ見た目にしない。
  unknown: "outline",
};

const STATUS_ICON: Record<CockpitCategoryStatus, typeof CheckCircle2> = {
  confirmed: CheckCircle2,
  review: AlertTriangle,
  missing: CircleDashed,
  unknown: HelpCircle,
};

export function CategoryStatusBadge({
  status,
  label,
}: {
  status: CockpitCategoryStatus;
  label: string;
}) {
  const Icon = STATUS_ICON[status];
  return (
    <Badge variant={STATUS_VARIANT[status]} className="gap-1 font-normal" data-testid={`cockpit-status-${status}`}>
      <Icon className="h-3 w-3" aria-hidden="true" />
      {label}
    </Badge>
  );
}

export function UnderstandingMap({
  categories,
  selected,
  onSelect,
}: {
  categories: CockpitCategoryView[];
  selected: CockpitCategoryKey;
  onSelect: (key: CockpitCategoryKey) => void;
}) {
  return (
    <Card data-testid="cockpit-understanding-map">
      <CardHeader>
        <CardTitle className="text-sm">理解の全体マップ</CardTitle>
        <CardDescription>
          カードを選択すると、状態・根拠・修正方法を詳細ペインに表示します。
        </CardDescription>
      </CardHeader>
      <CardContent>
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
