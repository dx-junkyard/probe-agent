import type { Dispatch, SetStateAction } from "react";
import type { UxAcceptanceCriterionInput, UxVerificationMethod } from "@/api/types";
import type { FormDraftFieldBinding, FormDraftPatch } from "./form-draft-inbox";

const methods: readonly UxVerificationMethod[] = ["manual_review", "replay", "experiment", "runtime_observation", "not_verifiable"];
const fieldNames = ["statement", "verification_method", "verification_note"] as const;

/** Expand keyed child changes into the same explicit per-field conflict review
 * used for Requirement text. No domain writes happen here. */
export function prepareAcceptanceCriteria(
  incoming: FormDraftPatch,
  current: UxAcceptanceCriterionInput[],
  seed: UxAcceptanceCriterionInput[],
  setCriteria: Dispatch<SetStateAction<UxAcceptanceCriterionInput[]>>,
) {
  const fields = [...incoming.fields];
  const bindings: Record<string, FormDraftFieldBinding> = {};
  const seen = new Set<string>();
  let nextOrder = Math.max(-1, ...current.map((c) => c.criterion_order ?? 0)) + 1;
  for (const op of incoming.childOps) {
    if (op.childKind !== "acceptance_criterion" || !op.childKey || seen.has(op.childKey)) return null;
    seen.add(op.childKey);
    const existing = current.find((c) => c.criterion_key === op.childKey);
    const original = seed.find((c) => c.criterion_key === op.childKey);
    if (op.intent === "add" ? !!existing : !existing) return null;
    if (op.fields.some((f) => !fieldNames.includes(f.fieldName as typeof fieldNames[number]))) return null;
    if (new Set(op.fields.map((f) => f.fieldName)).size !== op.fields.length) return null;
    const method = op.fields.find((f) => f.fieldName === "verification_method")?.value;
    if (method !== undefined && !methods.includes(method as UxVerificationMethod)) return null;
    if (op.order !== null && (!Number.isInteger(op.order) || op.order < 0)) return null;

    if (op.intent === "add" || op.intent === "remove") {
      const value: UxAcceptanceCriterionInput | null = op.intent === "remove" ? null : {
        criterion_key: op.childKey, criterion_order: op.order ?? nextOrder++,
        statement: "", verification_method: "manual_review", verification_note: "",
        ...Object.fromEntries(op.fields.map((f) => [f.fieldName, f.value])),
      };
      if (value && !(value.statement ?? "").trim()) return null;
      if (value) nextOrder = Math.max(nextOrder, (value.criterion_order ?? 0) + 1);
      const name = `受入条件[${op.childKey}]`;
      fields.push({ fieldName: name, value: JSON.stringify(value), rationale: "受入条件の追加・削除" });
      bindings[name] = {
        value: JSON.stringify(existing ?? null),
        dirty: JSON.stringify(existing) !== JSON.stringify(original),
        setValue: () => setCriteria((prev) => value
          ? [...prev, value]
          : prev.filter((c) => c.criterion_key !== op.childKey)),
      };
      continue;
    }
    const updates = [
      ...op.fields,
      ...(op.order === null ? [] : [{ fieldName: "criterion_order", value: String(op.order) }]),
    ];
    for (const field of updates) {
      const key = field.fieldName as keyof UxAcceptanceCriterionInput;
      const name = `受入条件[${op.childKey}].${key}`;
      fields.push({ fieldName: name, value: field.value, rationale: "受入条件の変更" });
      bindings[name] = {
        value: String(existing![key] ?? ""),
        dirty: existing![key] !== original?.[key],
        setValue: (value) => setCriteria((prev) => prev.map((c) => c.criterion_key === op.childKey
          ? { ...c, [key]: key === "criterion_order" ? Number(value) : value } : c)),
      };
    }
  }
  return { patch: { ...incoming, fields, childOps: [] }, bindings };
}
