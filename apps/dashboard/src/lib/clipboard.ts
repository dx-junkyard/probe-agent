// Issue #466 (UX-08): クリップボードへの書き込みは「完了してから」成功を伝える。
//
// `navigator.clipboard.writeText` は Promise を返し、権限拒否・非 secure
// context・フォーカス喪失で reject する。完了を待たずに「コピーしました」を
// 出すと、一度しか表示されない token をコピーしたつもりで利用者が離脱し、
// 再発行が必要になる。ここでは結果を boolean で返すだけにし、成功/失敗の
// 伝え方は呼び出し側が決める (失敗時に手動コピーの導線を出すかどうかは
// 面ごとに違う)。

export async function copyText(text: string): Promise<boolean> {
  const clipboard = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (!clipboard?.writeText) return false;
  try {
    await clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * 要素の中身を選択状態にする。クリップボードが使えないときの手動コピー導線
 * (選択された状態で Ctrl/⌘+C を押せばよい) に使う。
 */
export function selectElementText(element: HTMLElement | null): void {
  if (!element) return;
  const selection = window.getSelection?.();
  if (!selection) return;
  const range = document.createRange();
  range.selectNodeContents(element);
  selection.removeAllRanges();
  selection.addRange(range);
}
