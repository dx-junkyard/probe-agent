// Issue #360: メイン列の並び順のうち、状態によって変わる唯一の判断。
//
// 並び順そのものは固定である (現在地 → 例外 → status サマリー → その状態の
// 主作業面 → 未解決事項 + Q&A 進捗 → 全体像)。変わるのは Understanding Brief
// の位置だけなので、その 1 点を純粋関数として切り出し、全状態ぶんを単体で
// 検証できるようにする。ページ側で `wState === ...` を散らさない。
//
// ワークフロー状態はサーバーが決めた値をそのまま受け取る (Issue #349)。
// ここで再導出はしない -- 「その状態で Brief が主作業面か」を有限集合の
// 固定表で見るだけである。

import type { InterviewWorkflowState } from "@/api/types";

/**
 * Brief がメイン列の先頭 (主作業面の位置) に立つ状態かどうか。
 *
 * - `W1`: Brief 自体が「構築中」の表示そのもの。
 * - `W2`: Brief が判断対象で、主操作「この理解で進む」も内包する
 *   (#351 / 原則 P2 — 判断対象と主操作は同じ面に置く)。
 * - 状態が取れない (古い Control Server / 取得失敗): 先に立てる作業面が
 *   無いので Brief を先頭にする。
 *
 * それ以外 (`W0-A` / `W0-B` / `W3`〜`W7`) では主作業面が先で、Brief は
 * 未解決事項のあとの「全体像」領域に入る。`W0-A` / `W0-B` はこの関数を
 * 使う 2 カラムの外で描かれるため、どちらでも表示は変わらない。
 */
export function briefLeadsMainColumn(state: InterviewWorkflowState | null): boolean {
  return state == null || state === "W1" || state === "W2";
}
