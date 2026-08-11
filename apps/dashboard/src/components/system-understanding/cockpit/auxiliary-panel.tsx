// Issue #361: 補助情報の段階的開示。
//
// 右ペインには、選択カテゴリの詳細に加えてセッション情報・回答可能領域・
// Intent Brief・引き継ぎ・観測提案・まとめて修正・Q&A 全一覧・履歴と監査が
// 状態に応じて積み上がっていた。どれも「必要になったら読む」情報なのに、
// 常設カードとして同時に現れ、選択カテゴリの修正導線と場所を奪い合う。
//
// ここでは 1 枚の「補助情報」カードにまとめ、各項目を `<details>` にする。
// 開閉状態はブラウザが `<details>` の `open` として持つので、開いても
// 選択カテゴリやスクロール位置は変わらず、キーボードとスクリーンリーダーは
// summary の展開状態をそのまま読める。
//
// 「即時対応が必要な情報」(未処理の引き継ぎ、ブロッキング中の失敗) はここへ
// 入れない。閉じた `<details>` に入れると見失うため、呼び出し側が常設で
// 描く (issue の受け入れ条件)。
//
// コックピットの CTA から中の要素へ移動できるように、`navigation.ts` の
// `focusCockpitTarget` が祖先の `<details>` を開いてからフォーカスする。

import type { ReactNode } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function CockpitAuxiliaryPanel({ children }: { children: ReactNode }) {
  return (
    <Card data-testid="cockpit-auxiliary">
      <CardHeader>
        <CardTitle className="text-sm">補助情報</CardTitle>
        <CardDescription>
          セッション情報・全件一覧・履歴は、必要なときだけ開きます。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">{children}</CardContent>
    </Card>
  );
}

export function CockpitAuxiliarySection({
  id,
  title,
  description,
  badge,
  defaultOpen = false,
  children,
}: {
  /** `cockpit-aux-<id>` として test id とアンカーに使う。 */
  id: string;
  title: string;
  description?: string;
  /** 未処理件数など、開かなくても分かるべき短い注記。 */
  badge?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <details
      className="rounded-md border px-3 py-2"
      data-testid={`cockpit-aux-${id}`}
      open={defaultOpen}
    >
      <summary className="cursor-pointer text-sm font-medium">
        <span className="inline-flex flex-wrap items-baseline gap-x-2">
          <span>{title}</span>
          {badge && (
            <span
              className="text-xs font-normal text-muted-foreground"
              data-testid={`cockpit-aux-${id}-badge`}
            >
              {badge}
            </span>
          )}
        </span>
      </summary>
      {description && (
        <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      )}
      <div className="mt-3 space-y-4">{children}</div>
    </details>
  );
}
