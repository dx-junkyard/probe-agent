// Issue #356 §5 / Issue #359: 未解決の確認事項。
//
// 未解決の質問をカテゴリ単位のグループで、優先度上位から段階的に出す。
// グルーピング・並び順・重複排除・解決済みの除外・状態内訳はすべて
// `buildCockpitModel` / `groupUnresolvedItems` が決めており、ここでは
// 「決まったものを描く」だけ (dashboard SKILL.md の本画面のルール)。
//
// 初期表示は上位グループだけに絞り、残りは明示操作で開く (issue #359)。
// 展開・折りたたみの直後はフォーカスを失わせない。

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  PRIORITY_LABELS,
  type CockpitQaFetchStatus,
  type CockpitUnresolvedGroup,
  type CockpitUnresolvedItem,
} from "./model";

/** 行の見出しに付ける状態マーカー (「後で回答」を解決済みに見せない)。 */
function statusMarkers(item: CockpitUnresolvedItem): string {
  return `${item.unconfirmed ? " · 再確認待ち" : ""}${item.deferred ? " · 後で回答" : ""}`;
}

export function CockpitUnresolvedItems({
  groups,
  totalCount,
  qaFetchStatus = "ready",
  onSelect,
  initialVisibleGroups = 3,
}: {
  /** カテゴリ単位のグループ (`CockpitModel.unresolvedGroups`)。 */
  groups: CockpitUnresolvedGroup[];
  /** 未解決の質問の総数。グループ数ではない。 */
  totalCount: number;
  /** Q&A の取得状態。未取得なら「残り N 件」は確定値ではない。 */
  qaFetchStatus?: CockpitQaFetchStatus;
  /** 行のアクション。その質問そのものの回答 UI へ移動する。 */
  onSelect: (item: CockpitUnresolvedItem) => void;
  /** 初期表示するグループ数。既定 3。 */
  initialVisibleGroups?: number;
}) {
  const qaReady = qaFetchStatus === "ready";
  const [expanded, setExpanded] = useState(false);
  const [openGroupIds, setOpenGroupIds] = useState<Record<string, boolean>>({});

  const visibleGroups = expanded ? groups : groups.slice(0, initialVisibleGroups);
  const hiddenGroups = groups.slice(initialVisibleGroups);
  // 開発者が数えているのはグループ数ではなく質問数なので、残件も質問数で出す。
  const hiddenQuestionCount = hiddenGroups.reduce((sum, g) => sum + g.counts.total, 0);

  // 展開・折りたたみでフォーカスを失わせない (issue #359)。タイマーではなく
  // 描画後の effect で移す。
  const toggleRef = useRef<HTMLButtonElement | null>(null);
  const rowActionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const pendingFocus = useRef<"first-revealed" | "toggle" | null>(null);

  useEffect(() => {
    const pending = pendingFocus.current;
    pendingFocus.current = null;
    if (pending === "first-revealed") {
      // 新たに現れた最初の行の操作へ移る。
      rowActionRefs.current[initialVisibleGroups]?.focus();
    } else if (pending === "toggle") {
      toggleRef.current?.focus();
    }
  }, [expanded, initialVisibleGroups]);

  // Issue #440: the help target is this card itself. `interview.tsx` places
  // it as a direct child of an `items-start` Grid (#359), so wrapping it in
  // a `<div data-help-id>` would make the wrapper the grid item and let the
  // sibling card stretch again.
  return (
    <Card data-testid="cockpit-unresolved-items" data-help-id="interview.unresolved_items">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm">未解決の確認事項</CardTitle>
            <CardDescription>影響が大きい順に、カテゴリごとにまとめています。</CardDescription>
          </div>
          <Badge
            variant={!qaReady ? "outline" : totalCount > 0 ? "warning" : "success"}
            className="font-normal"
          >
            {qaReady ? `残り ${totalCount} 件` : `確認済み ${totalCount} 件 + 未取得`}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {/* Q&A が読めていないときは「0 件 = 残っていない」と読めてはいけない。 */}
        {!qaReady && (
          <p
            className="rounded-md border border-dashed p-2 text-xs text-muted-foreground"
            data-testid="cockpit-unresolved-qa-unavailable"
          >
            {qaFetchStatus === "loading"
              ? "Q&A を読み込み中です。ここに表示されていない確認事項がある可能性があります。"
              : "Q&A を取得できていないため、この一覧には Q&A 由来の確認事項が含まれていません。"}
          </p>
        )}
        {groups.length === 0 ? (
          qaReady ? (
            <p className="text-xs text-muted-foreground" data-testid="cockpit-unresolved-empty">
              未解決の確認事項はありません。
            </p>
          ) : null
        ) : (
          <>
            <ul className="divide-y">
              {visibleGroups.map((group, index) => {
                const rest = group.items.slice(1);
                const groupOpen = openGroupIds[group.id] ?? false;
                return (
                  <li
                    key={group.id}
                    className="flex flex-wrap items-start gap-2 py-2 first:pt-0 last:pb-0"
                    data-testid="cockpit-unresolved-row"
                  >
                    <Badge
                      variant={group.priority === "high" ? "destructive" : "secondary"}
                      className="font-normal"
                    >
                      優先度 {PRIORITY_LABELS[group.priority]}
                    </Badge>
                    <div className="min-w-0 flex-1">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                        {group.categoryLabel}
                        {statusMarkers(group.representative)}
                      </div>
                      <p className="mt-0.5 text-xs break-words">{group.representative.question}</p>
                      {group.counts.total > 1 && (
                        <div className="mt-1 space-y-1">
                          <p className="text-[11px] text-muted-foreground">
                            ほか {group.counts.total - 1} 件
                            {group.counts.unconfirmed > 0
                              ? ` · 再確認待ち ${group.counts.unconfirmed} 件`
                              : ""}
                            {group.counts.deferred > 0
                              ? ` · 後で回答 ${group.counts.deferred} 件`
                              : ""}
                          </p>
                          <button
                            type="button"
                            className="text-[11px] font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                            onClick={() =>
                              setOpenGroupIds(prev => ({ ...prev, [group.id]: !groupOpen }))
                            }
                            aria-expanded={groupOpen}
                            data-testid="cockpit-unresolved-group-toggle"
                          >
                            {groupOpen
                              ? "このカテゴリの残りを隠す"
                              : `このカテゴリの残り ${rest.length} 件を表示`}
                          </button>
                          {groupOpen && (
                            <ul className="space-y-1 border-l pl-2">
                              {rest.map(item => (
                                <li
                                  key={item.id}
                                  className="flex flex-wrap items-start gap-2"
                                  data-testid="cockpit-unresolved-group-item"
                                >
                                  <p className="min-w-0 flex-1 text-[11px] break-words">
                                    {item.question}
                                    <span className="text-muted-foreground">
                                      {statusMarkers(item)}
                                    </span>
                                  </p>
                                  <button
                                    type="button"
                                    className="shrink-0 text-[11px] font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                    onClick={() => onSelect(item)}
                                    data-testid="cockpit-unresolved-group-item-action"
                                  >
                                    この項目を開く
                                  </button>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      ref={el => {
                        rowActionRefs.current[index] = el;
                      }}
                      className="shrink-0 self-center text-xs font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                      onClick={() => onSelect(group.representative)}
                      data-testid="cockpit-unresolved-action"
                    >
                      この項目を開く
                    </button>
                  </li>
                );
              })}
            </ul>
            {hiddenGroups.length > 0 && (
              expanded ? (
                <button
                  type="button"
                  ref={toggleRef}
                  className="text-xs font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  onClick={() => {
                    pendingFocus.current = "toggle";
                    setExpanded(false);
                  }}
                  data-testid="cockpit-unresolved-collapse"
                >
                  上位のみ表示
                </button>
              ) : (
                <button
                  type="button"
                  ref={toggleRef}
                  className="text-xs font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  onClick={() => {
                    pendingFocus.current = "first-revealed";
                    setExpanded(true);
                  }}
                  data-testid="cockpit-unresolved-expand"
                >
                  残り {hiddenQuestionCount} 件を表示
                </button>
              )
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
