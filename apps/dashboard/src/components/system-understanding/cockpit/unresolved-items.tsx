// Issue #356 §5: 未解決の確認事項。
//
// `open_questions` と未解決 Q&A を影響度順に 1 行ずつ出す。並び順・重複排除・
// 解決済みの除外は `buildCockpitModel` が決めており、ここでは描くだけ。

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PRIORITY_LABELS, type CockpitUnresolvedItem } from "./model";

export function CockpitUnresolvedItems({
  items,
  onSelect,
}: {
  items: CockpitUnresolvedItem[];
  /** 行のアクション。その質問そのものの回答 UI へ移動する。 */
  onSelect: (item: CockpitUnresolvedItem) => void;
}) {
  return (
    <Card data-testid="cockpit-unresolved-items">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm">未解決の確認事項</CardTitle>
            <CardDescription>影響が大きい順に並んでいます。</CardDescription>
          </div>
          <Badge variant={items.length > 0 ? "warning" : "success"} className="font-normal">
            残り {items.length} 件
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="cockpit-unresolved-empty">
            未解決の確認事項はありません。
          </p>
        ) : (
          <ul className="divide-y">
            {items.map(item => (
              <li
                key={item.id}
                className="flex flex-wrap items-start gap-2 py-2 first:pt-0 last:pb-0"
                data-testid="cockpit-unresolved-row"
              >
                <Badge
                  variant={item.priority === "high" ? "destructive" : "secondary"}
                  className="font-normal"
                >
                  優先度 {PRIORITY_LABELS[item.priority]}
                </Badge>
                <div className="min-w-0 flex-1">
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    {item.categoryLabel}
                    {item.unconfirmed ? " · 再確認待ち" : ""}
                    {/* `skipped` は「後で回答する」で見送っただけの未回答。
                        解決済みと区別できるように明示する。 */}
                    {item.deferred ? " · 後で回答" : ""}
                  </div>
                  <p className="mt-0.5 text-xs break-words">{item.question}</p>
                </div>
                <button
                  type="button"
                  className="shrink-0 self-center text-xs font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  onClick={() => onSelect(item)}
                  data-testid="cockpit-unresolved-action"
                >
                  この項目を開く
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
