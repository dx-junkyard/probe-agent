// Issue #466 (UX-04): Experiment 作成ダイアログの比較候補を行ごとに分類する。
//
// 以前は「ラベルと patch の両方がある行」だけを filter で残して送信していた
// ため、3 件目にラベルや risk note を書いたまま patch を貼り忘れても、完全な
// 2 件だけで成功してしまい、利用者の意図した比較対象と違う Experiment が
// できた。ここでは 3 つを区別する:
//   - empty:    何も入力していない行。送信から外してよい (空の追加枠)。
//   - partial:  何か入力したが必須項目が欠けている行。送信を止め、欠けている
//               項目を行ごとに示す。修正するか明示的に削除するまで送らない。
//   - complete: ラベルと patch がある行。
// 判定は入力の有無だけで行う (有限の構造検査)。

export interface VariantDraft {
  label: string;
  patch_text: string;
  risk_note: string;
}

export type VariantRowState = "empty" | "partial" | "complete";

export type VariantMissingField = "label" | "patch_text";

export const VARIANT_FIELD_LABEL: Record<VariantMissingField, string> = {
  label: "ラベル",
  patch_text: "patch",
};

export interface VariantRowCheck {
  index: number;
  state: VariantRowState;
  missing: VariantMissingField[];
}

export function classifyVariantRow(variant: VariantDraft, index: number): VariantRowCheck {
  const hasLabel = variant.label.trim().length > 0;
  const hasPatch = variant.patch_text.trim().length > 0;
  const hasRisk = variant.risk_note.trim().length > 0;
  if (!hasLabel && !hasPatch && !hasRisk) return { index, state: "empty", missing: [] };
  const missing: VariantMissingField[] = [];
  if (!hasLabel) missing.push("label");
  if (!hasPatch) missing.push("patch_text");
  return { index, state: missing.length === 0 ? "complete" : "partial", missing };
}

export interface VariantSetCheck {
  rows: VariantRowCheck[];
  complete: VariantRowCheck[];
  partial: VariantRowCheck[];
  /** 送信できるか: 入力途中の行が無く、完全な候補が 2 件以上ある。 */
  submittable: boolean;
}

export const MIN_EXPERIMENT_VARIANTS = 2;

export function checkVariants(variants: readonly VariantDraft[]): VariantSetCheck {
  const rows = variants.map(classifyVariantRow);
  const complete = rows.filter((r) => r.state === "complete");
  const partial = rows.filter((r) => r.state === "partial");
  return {
    rows,
    complete,
    partial,
    submittable: partial.length === 0 && complete.length >= MIN_EXPERIMENT_VARIANTS,
  };
}

/** 「候補3の patch が必要です」形式の行別メッセージ。 */
export function describeMissing(row: VariantRowCheck): string {
  const fields = row.missing.map((f) => VARIANT_FIELD_LABEL[f]).join("と");
  return `候補 ${row.index + 1} の${fields}が必要です`;
}
