import { describe, expect, it } from "vitest";
import { prepareAcceptanceCriteria } from "@/lib/acceptance-criteria-draft";
import type { UxAcceptanceCriterionInput } from "@/api/types";
import type { FormDraftPatch } from "@/lib/form-draft-inbox";

const criterion: UxAcceptanceCriterionInput = {
  criterion_key: "ac-1", criterion_order: 1, statement: "元の条件",
  verification_method: "manual_review", verification_note: "既存メモ",
};
function patch(childOps: FormDraftPatch["childOps"]): FormDraftPatch {
  return { patchToken: "token", formId: "ux_requirement.revision", targetKind: "ux_requirement",
    targetRef: "req-1", selectedItemRef: "", fields: [], relations: [], childOps };
}
describe("acceptance criteria form handoff", () => {
  it("keeps multiple additions in proposal order after existing criteria", () => {
    let state = [{ ...criterion, criterion_order: 5 }];
    const prepared = prepareAcceptanceCriteria(patch(["first", "second"].map((childKey) => ({
      childKind: "acceptance_criterion", childKey, intent: "add", order: null,
      fields: [{ fieldName: "statement", value: childKey }],
    }))), state, state, (value) => { state = typeof value === "function" ? value(state) : value; })!;
    for (const f of prepared.patch.fields) prepared.bindings[f.fieldName].setValue(f.value);
    expect(state.map((c) => c.criterion_order)).toEqual([5, 6, 7]);
  });
  it("updates selected child fields and order while preserving sibling fields", () => {
    let state = [{ ...criterion }, { ...criterion, criterion_key: "ac-2" }];
    const prepared = prepareAcceptanceCriteria(patch([{ childKind: "acceptance_criterion", childKey: "ac-1",
      intent: "update", order: 3, fields: [{ fieldName: "statement", value: "提案文" }] }]), state, state,
      (value) => { state = typeof value === "function" ? value(state) : value; })!;
    expect(prepared.patch.fields.map((f) => f.fieldName)).not.toContain("statement");
    for (const f of prepared.patch.fields) prepared.bindings[f.fieldName].setValue(f.value);
    expect(state[0]).toEqual({ ...criterion, criterion_order: 3, statement: "提案文" });
    expect(state[1]).toEqual({ ...criterion, criterion_key: "ac-2" });
  });
  it("keeps edits as per-field conflicts, including deletion of an edited child", () => {
    const state = [{ ...criterion, statement: "利用者の編集" }];
    const prepared = prepareAcceptanceCriteria(patch([{ childKind: "acceptance_criterion", childKey: "ac-1",
      intent: "update", order: null, fields: [{ fieldName: "statement", value: "提案" }, { fieldName: "verification_note", value: "提案メモ" }] }]), state, [criterion], () => {})!;
    expect(Object.values(prepared.bindings).map((b) => b.dirty)).toEqual([true, false]);
    const removed = prepareAcceptanceCriteria(patch([{ childKind: "acceptance_criterion", childKey: "ac-1",
      intent: "remove", order: null, fields: [] }]), state, [criterion], () => {})!;
    expect(Object.values(removed.bindings)[0].dirty).toBe(true);
    expect(state[0].statement).toBe("利用者の編集");
  });
  it("preserves a reserved key on add and removes only the selected existing child", () => {
    let state = [criterion];
    const prepared = prepareAcceptanceCriteria(patch([
      { childKind: "acceptance_criterion", childKey: "reserved-42", intent: "add", order: 2,
        fields: [{ fieldName: "statement", value: "追加条件" }] },
      { childKind: "acceptance_criterion", childKey: "ac-1", intent: "remove", order: null, fields: [] },
    ]), state, state, (value) => { state = typeof value === "function" ? value(state) : value; })!;
    for (const f of prepared.patch.fields) prepared.bindings[f.fieldName].setValue(f.value);
    expect(state).toEqual([{ criterion_key: "reserved-42", criterion_order: 2, statement: "追加条件",
      verification_method: "manual_review", verification_note: "" }]);
  });
  it.each([
    { childKind: "unknown", childKey: "ac-1", intent: "update", fields: [] },
    { childKind: "acceptance_criterion", childKey: "missing", intent: "remove", fields: [] },
    { childKind: "acceptance_criterion", childKey: "ac-1", intent: "add", fields: [] },
    { childKind: "acceptance_criterion", childKey: "ac-1", intent: "update", fields: [{ fieldName: "unknown", value: "x" }] },
    { childKind: "acceptance_criterion", childKey: "ac-1", intent: "update", fields: [{ fieldName: "verification_method", value: "unknown" }] },
  ] as const)("rejects invalid child changes without applying anything: %j", (op) => {
    const result = prepareAcceptanceCriteria(patch([{ ...op, fields: [...op.fields], order: null }]), [criterion], [criterion], () => { throw new Error("unexpected mutation"); });
    expect(result).toBeNull();
  });
});
