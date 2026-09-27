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
//
// Issue #466 (UX-02) で共通 Dialog もこの hook に載せた。Dialog は入れ子に
// なる (詳細ダイアログの中から承認確認を開く) ので、面は 1 本のスタックで
// 管理し、Escape と Tab の循環は「いちばん上の面」だけが処理する。スタックが
// 無いと 1 回の Escape で重なった全ての面が閉じ、内側の面の Tab 循環を外側の
// 面が奪う。背景のスクロールロックも同じ理由で参照カウントにする — 内側を
// 閉じた時点で外側がまだ開いているのにロックが外れてはならない。

import { useEffect, useRef, type RefObject } from "react";

export const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

// 開いている面の識別子。末尾がいちばん上。
const surfaceStack: symbol[] = [];

/** テスト用: 開いている面の数。 */
export function openModalSurfaceCount(): number {
  return surfaceStack.length;
}

let scrollLockCount = 0;
let scrollLockPrevious = "";

/**
 * 背景 (body) のスクロールを止める。戻り値の関数で解除する。参照カウント
 * なので、複数の面が重なっていても最後の 1 つが閉じるまでロックは外れない。
 */
export function lockBodyScroll(): () => void {
  if (scrollLockCount === 0) {
    scrollLockPrevious = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  scrollLockCount += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    scrollLockCount -= 1;
    if (scrollLockCount === 0) document.body.style.overflow = scrollLockPrevious;
  };
}

function initialFocusTarget(panel: HTMLElement): HTMLElement {
  // 呼び出し側が明示した初期フォーカス (破壊的操作の確認では「キャンセル」に
  // 置く) を最優先し、次に「最初の操作要素のうち初期フォーカス対象から外した
  // もの (閉じるボタン) 以外」。閉じるボタンに最初に着地すると、読み上げは
  // 「閉じる」から始まり、Enter 1 回で入力を捨てる。
  const explicit = panel.querySelector<HTMLElement>("[data-autofocus]");
  if (explicit) return explicit;
  const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
  return items.find((el) => !el.hasAttribute("data-modal-initial-skip")) ?? items[0] ?? panel;
}

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

    const token = Symbol("modal-surface");
    surfaceStack.push(token);

    initialFocusTarget(panel).focus();

    const onKeyDown = (event: KeyboardEvent) => {
      // いちばん上の面だけが反応する (入れ子の Dialog)。
      if (surfaceStack[surfaceStack.length - 1] !== token) return;
      // IME 変換中の Escape は変換の取り消しであって、面を閉じる操作ではない。
      if (event.isComposing) return;
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
      const index = surfaceStack.lastIndexOf(token);
      if (index >= 0) surfaceStack.splice(index, 1);
      if (returnFocusTarget && returnFocusTarget.isConnected) {
        returnFocusTarget.focus();
      } else if (returnFocusTarget?.id) {
        // 起点が再描画で作り直された場合 (同じ id の新しい要素) はそちらへ戻す。
        document.getElementById(returnFocusTarget.id)?.focus();
      }
    };
  }, [open, panelRef, returnFocusRef]);
}
