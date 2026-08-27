// Issue #390: the Purpose Chain's Dashboard-side display helpers, and NOTHING
// semantic.
//
// This module exists for the same reason `cockpit/model.ts` does (#356): a
// pure module that ANY display component can call, so no component
// re-derives ordering or a label on its own. Everything a component needs
// that is a JUDGEMENT -- an element's state, a relation's status, resolution
// level, recheck reason, provenance -- comes from `GET /purpose-chain`
// verbatim (`docs/purpose-chain.md` §0 invariant 2). What is left here is
// genuinely presentational:
//
//   1. The fixed CAUSAL order of the 3 frame slots (§3.1/§3.2 both require
//      it, and it is also the accessibility contract: heading order = causal
//      order, so getting this wrong breaks §3.4 too).
//   2. Fixed Japanese labels for finite codes the server does NOT already
//      send a label for (`resolution_level`, `stale_reason`, `answerability`,
//      `PurposeNeedState`) -- unlike `confirmation_label` / `provenance_label`
//      / `status_label`, which are reused as-is because the server already
//      computed them.
//   3. Small structural lookups (which relation connects two given
//      elements) that are array indexing, not classification -- the VALUE
//      returned is still the server's own relation object, untouched.

import type {
  PurposeAnswerability,
  PurposeChainOut,
  PurposeElementKind,
  PurposeElementOut,
  PurposeNeedState,
  PurposeQuestionOut,
  PurposeRelationKind,
  PurposeRelationOut,
  PurposeResolutionLevel,
  PurposeResponseKind,
  PurposeStaleReason,
} from "@/api/types";

/** The 3 kinds that occupy a Purpose Frame slot -- `core_capability` never
 * does (§1.2/§1.6), so it is excluded from the type as well as the value:
 * indexing `PurposeFrameOut` with this type is then statically safe. */
export type FrameSlotKind = Exclude<PurposeElementKind, "core_capability">;

/** The causal order of the 3 Purpose Frame slots (`docs/purpose-chain.md` §0
 * diagram). Both Overview Level 0 and Interview Level 1 read this array
 * rather than hand-ordering `Object.values(chain.frame)`, so the two screens
 * can never drift into showing a different sequence. */
export const FRAME_ELEMENT_ORDER: readonly FrameSlotKind[] = [
  "beneficiary_problem",
  "desired_change",
  "intervention",
];

/** Heading text for each frame slot, matching the causal-order phrasing
 * `docs/purpose-chain.md` §3.1 specifies verbatim (「誰のどんな現状を変える
 * か」 etc.) so the Overview's copy and this contract cannot drift apart. */
export const ELEMENT_KIND_HEADING: Record<PurposeElementKind, string> = {
  beneficiary_problem: "対象者と現在の課題",
  desired_change: "望ましい変化",
  intervention: "システムの介入",
  core_capability: "Capability",
};

/** The two frame-internal relation kinds, in the same causal order as
 * `FRAME_ELEMENT_ORDER`. `intervention_to_capability` is per-capability and
 * handled separately (`capabilityRelation` below). */
export const FRAME_RELATION_ORDER: readonly Exclude<
  PurposeRelationKind,
  "intervention_to_capability"
>[] = ["problem_to_change", "change_to_intervention"];

export const RELATION_KIND_LABEL: Record<PurposeRelationKind, string> = {
  problem_to_change: "課題 → 望ましい変化",
  change_to_intervention: "望ましい変化 → システムの介入",
  intervention_to_capability: "システムの介入 → Capability",
};

/** `resolution_level` never comes with its own label field (unlike
 * confirmation/provenance/status) -- the server treats it as a finite code,
 * so the Dashboard's ONE fixed Japanese mapping lives here rather than being
 * re-typed per component. */
export const RESOLUTION_LEVEL_LABEL: Record<PurposeResolutionLevel, string> = {
  L0: "未接続 (L0)",
  L1: "要素あり (L1)",
  L2: "接続確認済み (L2)",
  L3: "成果指標あり (L3)",
};

export const STALE_REASON_LABEL: Record<PurposeStaleReason, string> = {
  source_changed: "起点の内容が変わりました",
  target_changed: "終点の内容が変わりました",
  both_changed: "起点・終点の両方が変わりました",
  upstream_changed: "上流の関係が変わったため影響を受けています",
  snapshot_changed: "コードの断面が変わりました",
};

export const ANSWERABILITY_LABEL: Record<PurposeAnswerability, string> = {
  human_judgement: "あなたの判断が必要です",
  system_researchable: "コード・ドキュメントから調査できます",
  already_answered: "すでに回答済みです",
  unavailable: "現在は判定できません",
};

export const NEED_STATE_LABEL: Record<PurposeNeedState, string> = {
  available: "未対応",
  waiting: "処理中",
  answered: "回答済み",
  deferred: "保留中",
  unavailable: "取得できません",
};

export const RESPONSE_KIND_LABEL: Record<PurposeResponseKind, string> = {
  confirm: "確認",
  correct: "訂正",
  unknown: "わからない",
  defer: "保留",
  investigate: "調査を依頼",
};

/** One frame slot: which kind, and the server's element for it (or `null`
 * when the frame section itself failed to derive -- §1.2/§1.6). */
export interface FrameSlot {
  kind: FrameSlotKind;
  element: PurposeElementOut | null;
}

/** The 3 frame slots in causal order, straight from `chain.frame` -- no
 * re-filtering of `chain.elements`, so an element that is only present in
 * `elements` (e.g. an additional `intervention` claim) can never be
 * mistaken for a frame slot. */
export function frameSlots(chain: PurposeChainOut): FrameSlot[] {
  return FRAME_ELEMENT_ORDER.map((kind) => ({ kind, element: chain.frame[kind] ?? null }));
}

/** `intervention` claims beyond the single frame slot. Structural filtering
 * only (kind + not-the-frame-slot-id), never a semantic re-classification. */
export function additionalInterventionElements(chain: PurposeChainOut): PurposeElementOut[] {
  const frameId = chain.frame.intervention?.id;
  return chain.elements.filter((e) => e.kind === "intervention" && e.id !== frameId);
}

export function capabilityElements(chain: PurposeChainOut): PurposeElementOut[] {
  return chain.elements.filter((e) => e.kind === "core_capability");
}

/** The relation between the two named frame slots, or `undefined` when
 * neither endpoint exists yet (the relation section only emits a relation
 * once both its endpoints do -- §1.3). Returns the server's own relation
 * object unchanged; this is array lookup, not a status decision. */
export function frameRelation(
  chain: PurposeChainOut,
  kind: Exclude<PurposeRelationKind, "intervention_to_capability">,
): PurposeRelationOut | undefined {
  return chain.relations.find((r) => r.kind === kind);
}

/** The `intervention_to_capability` relation for one specific Capability
 * element. */
export function capabilityRelation(
  chain: PurposeChainOut,
  capabilityElementId: string,
): PurposeRelationOut | undefined {
  return chain.relations.find(
    (r) => r.kind === "intervention_to_capability" && r.target_id === capabilityElementId,
  );
}

/** Whether a relation currently has real content on both ends and can
 * therefore be confirmed/rejected -- mirrors the server's own 422
 * `purpose_relation_not_decidable` check (§1.6) so the Dashboard never shows
 * a Confirm/Reject control the server would refuse. This reads `status`,
 * which the server already computed from the endpoints; it does not
 * re-inspect the endpoints itself. */
export function relationIsDecidable(relation: PurposeRelationOut): boolean {
  return relation.status !== "unknown" && relation.status !== "unavailable";
}

/** Whether a `PurposeQuestionOut` is currently attached to the given element
 * or relation id -- used only to draw a "current question" marker next to
 * its target; the marker never changes what the target itself renders. */
export function questionTargets(
  question: PurposeQuestionOut | null | undefined,
  targetKind: "element" | "relation",
  targetId: string,
): boolean {
  return !!question && question.target_kind === targetKind && question.target_id === targetId;
}

/** A human-answerable question the developer can act on right now vs. one
 * already routed to investigation (`system_researchable`) or otherwise not
 * a yes/no/correct decision for a human (§2.3: `system_researchable` は
 * 人へ聞かず、調査へ送る -- the Dashboard must not draw a confirm/correct
 * form for it). */
export function questionAwaitsHumanDecision(question: PurposeQuestionOut): boolean {
  return question.answerability === "human_judgement" && question.state === "available";
}
