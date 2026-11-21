// Evolution Node の maturity 遷移が拒否されたときの日本語文 (UX 監査 2026-11-17 C6)。
//
// コードの集合はサーバーが所有する有限集合をそのまま写したもの:
//   - `apps/control-server/app/evolution_node.py` の `TRANSITION_REJECTION_CODES`
//     (評価順の 13 個。first match)
//   - `apps/control-server/app/routes/evolution_nodes.py` の
//     `ROUTE_TRANSITION_REJECTION_CODES` (HTTP 経路だけが持つ 2 個)
// 画面は遷移表を持たない (#396)。ここにあるのは「サーバーが返したコード → 何が
// 足りないかの固定文」の写像だけで、許可・拒否を先回りで判定する材料ではない。
// 表に無いコード (サーバー側で増えた場合など) はサーバーの文言へ fallback する。

import type { EvolutionDecisionId } from "./decision-locations";

export const EVOLUTION_TRANSITION_REJECTION_LABEL = {
  llm_state_not_allowed:
    "maturity の遷移は reasoning model の判断としては記録できません。人間の判断として要求してください。",
  unknown_target_state: "指定した遷移先は maturity の 6 状態のどれでもありません。",
  illegal_transition: "現在の maturity からその状態へは直接遷移できません。",
  manual_approval_required:
    "この遷移には、ログイン中の開発者による人間の判断 (decision_method: manual) が必要です。",
  reason_required: "保留 (suspended) と後退方向の遷移には理由の入力が必要です。",
  version_missing:
    "この Node には契約 version がまだありません。契約 version の登録は画面未対応で API のみです（下の「この Node で行える判断」の「契約 version の登録」の行）。",
  implementation_missing:
    "この遷移先には、契約を実装する現在の実装が必要です。実装の登録は画面未対応で API のみです（「実装の登録」の行）。",
  stable_implementation_missing:
    "この遷移先には、pin された安定実装が必要です。安定実装の pin は画面未対応で API のみです（「安定実装の pin」の行）。",
  monitoring_contract_missing:
    "監視中 (monitoring) にするには、監視契約の設定が必要です。監視契約の設定は画面未対応で API のみです（「監視契約の設定」の行）。",
  monitoring_contract_invalid:
    "設定された監視契約が、この Node の有効な監視契約として解決できません。",
  evidence_missing: "この遷移先には、根拠 (evidence) を 1 件以上指定する必要があります。",
  foreign_evidence: "指定した根拠に、この Node に紐付いていないものが含まれています。",
  stale_implementation:
    "現在の実装が、現在の契約 version に対応していません。新しい実装の登録は画面未対応で API のみです（「実装の登録」の行）。",
  deterministic_via_api_not_allowed:
    "システムが記録する遷移 (deterministic) は、この画面からは要求できません。",
  establishment_via_stabilization_required:
    "固定化 (established) は Stabilization の承認を通して行います。この画面から直接は要求できず、承認は画面未対応で API のみです（「固定化: 承認 (または却下)」の行）。",
} as const;

export type EvolutionTransitionRejectionCode = keyof typeof EVOLUTION_TRANSITION_REJECTION_LABEL;

// UX 監査 2026-11-17 C7: 画面に無い操作を求める拒否コードから、
// 「この Node で行える判断」表の該当行 (API のみ) への案内。
export const EVOLUTION_TRANSITION_REJECTION_DECISION: Partial<
  Record<EvolutionTransitionRejectionCode, EvolutionDecisionId>
> = {
  version_missing: "register_version",
  implementation_missing: "register_implementation",
  stale_implementation: "register_implementation",
  stable_implementation_missing: "pin_stable_implementation",
  monitoring_contract_missing: "monitoring_contract",
  establishment_via_stabilization_required: "stabilization_approval",
};

export interface EvolutionTransitionRejection {
  /** サーバーの有限コード (補足として小さく表示する)。不明なら "unknown"。 */
  code: string;
  /** 利用者が読む文。表にあれば固定の日本語文、無ければサーバーの文言。 */
  message: string;
  /** 固定文を出したかどうか。false ならサーバー文言へ fallback した。 */
  known: boolean;
  /** 案内先の「この Node で行える判断」表の行 (画面に無い操作を求める場合)。 */
  decision?: EvolutionDecisionId;
}

export function describeTransitionRejection(
  code: string | undefined,
  serverMessage: string | undefined,
): EvolutionTransitionRejection {
  const resolvedCode = code || "unknown";
  const known = Object.prototype.hasOwnProperty.call(EVOLUTION_TRANSITION_REJECTION_LABEL, resolvedCode)
    ? EVOLUTION_TRANSITION_REJECTION_LABEL[resolvedCode as EvolutionTransitionRejectionCode]
    : undefined;
  if (known) {
    const decision =
      EVOLUTION_TRANSITION_REJECTION_DECISION[resolvedCode as EvolutionTransitionRejectionCode];
    return decision
      ? { code: resolvedCode, message: known, known: true, decision }
      : { code: resolvedCode, message: known, known: true };
  }
  return { code: resolvedCode, message: serverMessage || "遷移できませんでした", known: false };
}
