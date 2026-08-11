// オーバーレイとして本文の上に重なる面 (Drawer / パネル) の共通挙動。
//
// Issue #362 でサイドバー Drawer に入れた「開いている間はモーダル」の扱いを、
// 2 枚目 (アシスタントパネル) が要るようになった時点で 1 箇所へ出した。同じ
// 規則を 2 実装持つと、片方だけ Escape が効かない・片方だけフォーカスが戻る
// といったズレが必ず出る。
//
// 提供するのは 4 つだけ:
//   - 開いた直後、面の中の最初の操作要素へフォーカスを移す
//   - Escape で閉じる
//   - Tab / Shift+Tab を面の中で循環させる (背後の本文へ抜けない)
//   - 閉じたら、開く前にフォーカスしていた要素へ戻す
//
// 表示・レイアウト・背景クリックは呼び出し側が持つ (面ごとに違うため)。

import { useEffect, useRef, type RefObject } from "react";

export const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function useModalSurface({
  open,
  onClose,
  panelRef,
  returnFocusRef,
}: {
  open: boolean;
  onClose: () => void;
  /** モーダルの面。`tabIndex={-1}` を付けておくこと (中に操作要素が無い場合の受け皿)。 */
  panelRef: RefObject<HTMLElement | null>;
  /** 閉じたときのフォーカス戻し先。省略時は開く直前の `document.activeElement`。 */
  returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  // ref に持つ: 呼び出し側がインラインの矢印関数を渡しても、識別子が変わる
  // たびに effect が張り直されてフォーカスが面の外へ跳ぶことがないように。
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;

    const focusables = () =>
      Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));

    // フォーカスを面へ移す前に解決する。cleanup で戻すのは「開発者が実際に
    // 居た要素」(通常は開くボタン) でなければならない。
    const previouslyFocused = document.activeElement;
    const returnFocusTarget =
      returnFocusRef?.current
      ?? (previouslyFocused instanceof HTMLElement ? previouslyFocused : null);

    (focusables()[0] ?? panel).focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey) {
        if (active === first || !panel.contains(active)) {
          event.preventDefault();
          last.focus();
        }
      } else if (active === last || !panel.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (returnFocusTarget && returnFocusTarget.isConnected) returnFocusTarget.focus();
    };
  }, [open, panelRef, returnFocusRef]);
}
