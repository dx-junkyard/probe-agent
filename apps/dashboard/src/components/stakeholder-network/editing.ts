// UX audit 2026-11-17 C1: pure helpers for the Stakeholder Value Network
// editing surface (no React, no API client).
//
// `design_status` / `recheck_state` always arrive decided by the server and
// are never recomputed here. `allowedDecisions` only mirrors the server's own
// finite transition rule (`stakeholder_network.record_decision`: `reinstate`
// is accepted only from rejected/retired, every other decision only from
// proposed/confirmed) so the screen does not offer a button the server is
// certain to refuse. The server still decides; a refusal is shown as-is.

import type {
  EnvironmentImpactKind, EnvironmentObservationConfidence, StakeholderDecisionKind,
  StakeholderNeedKind, ValueNetworkDesignStatus, ValueNetworkRecheckState,
} from "@/api/types";

export const NEED_KIND_VALUES: StakeholderNeedKind[] = ["unmet_need", "problem", "constraint", "expectation"];

export const NEED_KIND_LABEL: Record<StakeholderNeedKind, string> = {
  unmet_need: "満たされていない要望",
  problem: "困りごと",
  constraint: "制約",
  expectation: "期待",
};

export const OBSERVATION_CONFIDENCE_VALUES: EnvironmentObservationConfidence[] = ["observed", "reported", "assumed"];

export const OBSERVATION_CONFIDENCE_LABEL: Record<EnvironmentObservationConfidence, string> = {
  observed: "直接観測した",
  reported: "報告を受けた",
  assumed: "仮定",
};

export const IMPACT_KIND_VALUES: EnvironmentImpactKind[] = ["creates", "worsens", "relieves", "invalidates", "constrains"];

export const IMPACT_KIND_LABEL: Record<EnvironmentImpactKind, string> = {
  creates: "生み出す",
  worsens: "悪化させる",
  relieves: "和らげる",
  invalidates: "成り立たなくする",
  constrains: "制約する",
};

export const DECISION_LABEL: Record<StakeholderDecisionKind, string> = {
  confirm: "確定する",
  reject: "却下する",
  retire: "廃止する",
  reinstate: "提案に戻す",
};

export const DECISION_DESCRIPTION: Record<StakeholderDecisionKind, string> = {
  confirm: "表示中の内容を、あなたの判断として確定します。",
  reject: "この内容を採用しないと記録します。記録は残り、後から「提案に戻す」ことができます。",
  retire: "以前は有効だった内容を廃止として記録します。記録は残り、後から「提案に戻す」ことができます。",
  reinstate: "却下・廃止の記録はそのまま残し、もう一度「提案」の状態に戻します。",
};

export interface DecisionMenu {
  /** At most one primary action -- what the current state is waiting for. */
  primary: StakeholderDecisionKind | null;
  secondary: StakeholderDecisionKind[];
}

export function allowedDecisions(
  designStatus: ValueNetworkDesignStatus,
  recheckState: ValueNetworkRecheckState,
): DecisionMenu {
  if (designStatus === "rejected" || designStatus === "retired") {
    return { primary: "reinstate", secondary: [] };
  }
  if (designStatus === "proposed") {
    return { primary: "confirm", secondary: ["reject"] };
  }
  // confirmed: a stale confirmation asks to be re-confirmed; a current one
  // waits for nothing, so it has no primary action.
  return recheckState === "stale"
    ? { primary: "confirm", secondary: ["retire", "reject"] }
    : { primary: null, secondary: ["retire", "reject"] };
}

/** Japanese copy for the finite reject codes of `/stakeholder-network/*` and
 * `/journey-blueprint/*-links`. The raw code is shown only as a supplement. */
export const REJECT_CODE_MESSAGE: Record<string, string> = {
  stakeholder_key_required: "key を入力してください。",
  stakeholder_key_conflict: "同じ key がこの System に既にあります。別の key を指定してください。",
  stakeholder_not_found: "対象が見つかりません。画面を再読み込みしてください。",
  stakeholder_ref_kind_invalid: "参照の種類が受け付けられませんでした。",
  stakeholder_ref_target_not_found: "参照先が見つかりません。",
  observation_impact_kind_invalid: "影響の種類または影響先の種類が受け付けられませんでした。",
  exchange_self_loop: "提供する側と受け取る側が同じ場合、種類は「情報」だけを選べます。",
  exchange_value_statement_required: "何を渡すか(価値の記述)を入力してください。",
  exchange_consideration_incomplete: "対価が「あり」のときは、対価の種類と記述の両方を入力してください。",
  exchange_validity_inverted: "有効期間の終わりは始まりより後にしてください。",
  stakeholder_decision_stale_digest:
    "表示していた内容が更新されていたため、判断を記録しませんでした。最新の内容を確認してからもう一度操作してください。",
  stakeholder_not_decidable: "現在の状態からはこの判断を記録できません。",
  journey_step_not_found: "指定した Journey Step が見つかりません。",
  journey_blueprint_journey_not_found: "Journey が見つかりません。",
  value_exchange_not_found: "対象の Value Exchange が見つかりません。",
  journey_blueprint_target_not_found: "関連付け先が見つかりません。",
  journey_blueprint_validation_error: "入力内容を受け付けられませんでした。",
};

export interface WriteFailure {
  message: string;
  /** The server's finite code, shown as a supplement only. */
  code: string | null;
}

/** Turns a rejected write into a message for display next to the form.
 * Reads only `status` / `code` / `detail` (the `ApiError` shape) and never
 * uses `instanceof`, because tests mock the client module. */
export function describeWriteFailure(error: unknown): WriteFailure {
  const e = (error ?? {}) as { status?: unknown; code?: unknown; detail?: unknown; name?: unknown };
  const code = typeof e.code === "string" && e.code ? e.code : null;
  if (code && REJECT_CODE_MESSAGE[code]) return { message: REJECT_CODE_MESSAGE[code], code };
  if (e.name === "NetworkError") {
    return { message: "サーバーから応答がありませんでした。保存されたかどうかは不明です。一覧を再読み込みして確認してください。", code };
  }
  if (e.status === 422) return { message: "入力内容を受け付けられませんでした。必須項目と選択肢を確認してください。", code };
  if (e.status === 401 || e.status === 403) return { message: "この操作を行う権限がありません。", code };
  return { message: "保存できませんでした。時間をおいてもう一度試してください。", code };
}

/** `stakeholder_key` etc. are developer-chosen stable slugs (§0 invariant 3).
 * Trimming is the only normalization; the server is the judge of validity. */
export function normalizeKey(value: string): string {
  return value.trim();
}
