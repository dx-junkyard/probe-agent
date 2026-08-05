// Issue #356: コックピットの CTA から既存 UI へ移動する共通処理。
//
// 新しい回答・編集・根拠表示のフローは作らない。既存パネルまでスクロール
// し、そこにある最初の操作要素へフォーカスを移すだけ (issue「実装方針」/
// アクセシビリティ「CTA から対象質問・編集 UI へのフォーカス移動」)。

const FOCUSABLE =
  'button:not([disabled]), a[href], textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * 候補を優先度順に試し、最初に描かれているものへ移動する。
 *
 * 「この質問へ」の移動先は `qa-item-<id>` だが、Q&A 一覧が描かれない状態
 * (`W3` 以外) では存在しない。そのときだけ作業面・一覧へ落とす。
 */
export function focusFirstCockpitTarget(testIds: string[]): boolean {
  return testIds.some(testId => focusCockpitTarget(testId));
}

/** 対象が見つかりフォーカス移動できたら true。見つからなければ false。 */
export function focusCockpitTarget(testId: string): boolean {
  if (typeof document === "undefined") return false;
  const el = document.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
  if (!el) return false;
  if (typeof el.scrollIntoView === "function") {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  const focusable = el.matches(FOCUSABLE) ? el : el.querySelector<HTMLElement>(FOCUSABLE);
  if (focusable) {
    focusable.focus({ preventScroll: true });
    return true;
  }
  // 操作要素が無いパネル (根拠の表示など) でも、読み上げ位置は移したい。
  el.setAttribute("tabindex", "-1");
  el.focus({ preventScroll: true });
  return true;
}
