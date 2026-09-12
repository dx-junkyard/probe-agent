// Form draft inbox (Issue #446, Epic #443 Phase 3).
//
// docs/ai-discussion-adapter.md §3.2/§3.3/§3.6 is the canonical contract.
// This is the delivery half of prefill: `proposalToDraft` (in
// `discussion-adapters.ts`) turns selected Proposal items into a
// `FormDraftPatch`; this module is the CustomEvent bus that carries that
// patch to whichever form is actually mounted, modeled directly on
// `assistant-control.ts`'s `OPEN_ASSISTANT_EVENT` pattern (a single
// `window`-scoped event, a typed detail, a thin dispatch helper).
//
// Two deliberate filters live here, both required by §3.2:
//
// - A patch whose `targetKind`/`targetRef` does not match the SUBSCRIBING
//   form is never delivered -- never drop one Requirement's proposed values
//   into a different Requirement's open form (or a different target_kind
//   entirely). This is why `useFormDraftInbox` takes the form's own
//   `(formId, targetRef)` and filters incoming patches against it, rather
//   than broadcasting every patch to every listener.
// - A repeated `patchToken` is ignored client-side -- a complement to the
//   server's own `UNIQUE (proposal_id, patch_token, item_id)`, so a patch
//   applied once (e.g. because two forms both briefly subscribed during a
//   remount) cannot double-apply into the SAME form either.

import { useEffect, useRef, useState } from "react";
import type { DiscussionTargetKind } from "@/api/types";
import { getSystemId } from "@/api/client";

/** Selected changes addressed to an existing form. Requirement revision forms
 * consume acceptance-criterion child operations with per-field conflict review. */
export interface FormDraftPatch {
  /** Idempotency token -- also sent to the server's own `/prefill` audit
   * call so both sides agree on what "the same dispatch" means. */
  patchToken: string;
  targetKind: DiscussionTargetKind;
  targetRef: string;
  formId: string;
  /** §3.2's literal shape carries no sub-address, but `solution_design`
   * field items ARE sub-addressed (an Option's own `option_key`, via the
   * item's `subject_ref` -- the SAME sub-addressing `PROPOSAL_TARGET_SCHEMA`
   * has used since before this Epic, not a #448 nested/list concern). A
   * receiving form that drafts a single keyed sub-object (today only
   * `solution_design.option`'s `AddOptionForm`) reads this to know WHICH
   * key the patch's fields belong to; every other form ignores it. "" means
   * "no sub-address" (the target itself, not one of its children).
   */
  selectedItemRef: string;
  fields: { fieldName: string; value: string; rationale: string }[];
  childOps: {
    childKind: string;
    childKey: string;
    intent: "add" | "update" | "remove";
    order: number | null;
    fields: { fieldName: string; value: string }[];
  }[];
  relations: { relationKind: string; targetKind: string; targetRef: string; note: string }[];
}

const FORM_DRAFT_PATCH_EVENT = "probe-agent:form-draft-inbox";
// Issue #452 §3's delivery order contract: navigate -> form ready confirm ->
// patch delivery -> ACK -> prefill audit. `deliverFormDraftPatch` alone only
// proves a patch was DISPATCHED (queued, or broadcast to whatever happens to
// be listening) -- it does not prove any destination form actually consumed
// it. This second event fires ONLY from inside `useFormDraftInbox`'s own
// `consume()`, i.e. only once a form SUBSCRIBED to this exact
// `(formId, targetRef)` has actually run its `onPatch` handler for THIS
// `patchToken`. A dispatcher that gets no ack within its own timeout must
// report the delivery as `unavailable` (docs/01-specifications/capabilities/ai-discussion-adapter.md
// §1.3/§1.7, reused by #452's DD-INT-01: "フォーム未mount・配送失敗は成功扱い
// しない"), never call the server prefill-audit endpoint, and never claim
// success.
const FORM_DRAFT_ACK_EVENT = "probe-agent:form-draft-ack";

interface FormDraftAckDetail {
  patchToken: string;
  formId: string;
  targetRef: string;
}

function patchKey(formId: string, targetRef: string): string {
  return `${formId}|${targetRef}`;
}

/** A patch dispatched before its destination form has mounted (the normal
 * case: the review UI delivers, THEN navigates to the screen the form lives
 * on -- §3.6's "navigate, never execute") must not be lost the instant the
 * `CustomEvent` fires with no listener yet attached. This module-level map
 * holds the latest UNCONSUMED patch per `(formId, targetRef)`; a form reads
 * it once on mount (in addition to listening for future live deliveries)
 * and it is removed the moment either side consumes it. It is intentionally
 * NOT persisted anywhere (no localStorage, no server round trip) -- it is a
 * same-tab, same-session handoff, not a durable fact. */
const pendingPatches = new Map<string, FormDraftPatch>();
const OPEN_FORM_EVENT = "probe-agent:open-draft-form";
const requestedForms = new Set<string>();
let inboxSystemId = getSystemId();

function syncSystem() {
  const systemId = getSystemId();
  if (systemId !== inboxSystemId) {
    pendingPatches.clear();
    requestedForms.clear();
    inboxSystemId = systemId;
  }
}

export function requestFormDraftOpen(formId: string, targetRef: string) {
  syncSystem();
  requestedForms.add(patchKey(formId, targetRef));
  window.dispatchEvent(new CustomEvent(OPEN_FORM_EVENT));
}

export function useFormDraftOpenRequest(formId: string, targetRef: string) {
  const [, refresh] = useState(0);
  useEffect(() => {
    const onRequest = () => refresh((n) => n + 1);
    window.addEventListener(OPEN_FORM_EVENT, onRequest);
    return () => window.removeEventListener(OPEN_FORM_EVENT, onRequest);
  }, []);
  syncSystem();
  return requestedForms.has(patchKey(formId, targetRef));
}

export function clearFormDraftOpenRequest(formId: string, targetRef: string) {
  requestedForms.delete(patchKey(formId, targetRef));
  window.dispatchEvent(new CustomEvent(OPEN_FORM_EVENT));
}

/** Send a patch to whichever form is (or will shortly become) subscribed.
 * Never throws when nothing is listening yet. */
export function deliverFormDraftPatch(patch: FormDraftPatch): void {
  syncSystem();
  pendingPatches.set(patchKey(patch.formId, patch.targetRef), patch);
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<FormDraftPatch>(FORM_DRAFT_PATCH_EVENT, { detail: patch }));
}

/** Non-consuming check for "is there a patch waiting for this form right
 * now" -- used by a form's PARENT panel (which owns whether the revision
 * form is even open) to auto-open it after a fresh navigation from the
 * review UI (§3.6's navigate step). Does not remove the patch: the form
 * itself, once mounted, still does the real (consuming) read via
 * `useFormDraftInbox`. */
export function peekPendingFormDraftPatch(formId: string, targetRef: string): boolean {
  syncSystem();
  if (!formId || !targetRef) return false;
  return pendingPatches.has(patchKey(formId, targetRef));
}

/**
 * Subscribes a mounted form to patches addressed to it. `onPatch` fires only
 * for a patch whose `formId`/`targetRef` match this form's own identity
 * (§3.2's "target が一致しない patch は受け取らない") and whose `patchToken`
 * has not already been delivered to THIS subscription (§3.2's client-side
 * idempotency complement to the server's UNIQUE constraint).
 *
 * `formId`/`targetRef` empty means "not ready to receive" (mirrors
 * `useUiDraftSource`'s own no-op registration for an unkeyed row) -- no
 * listener is attached at all, so an empty targetRef can never accidentally
 * match an empty-string patch field.
 */
export function useFormDraftInbox(
  formId: string,
  targetRef: string,
  onPatch: (patch: FormDraftPatch) => void | boolean,
): void {
  const handlerRef = useRef(onPatch);
  const seenTokens = useRef(new Set<string>());

  // See `lib/ui-draft.tsx`'s `useUiDraftSource`: refs are written in an
  // effect, never during render. Declared BEFORE the subscription effect so
  // it flushes first -- the subscription can consume a patch that was
  // queued before this form mounted, and that consumption must see the
  // current handler rather than the one from the first render.
  useEffect(() => {
    handlerRef.current = onPatch;
  });

  useEffect(() => {
    if (!formId || !targetRef) return;
    const consume = (patch: FormDraftPatch) => {
      if (patch.targetKind !== formId.split(".")[0]) return;
      if (!seenTokens.current.has(patch.patchToken)) {
        if (handlerRef.current(patch) === false) return;
        seenTokens.current.add(patch.patchToken);
      }
      // Fired AFTER the subscribed form's own handler ran -- this is the
      // ack a dispatcher waits for (`dispatchFormDraftPrefill` below), never
      // fired merely because `deliverFormDraftPatch` was called.
      if (typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent<FormDraftAckDetail>(FORM_DRAFT_ACK_EVENT, {
            detail: { patchToken: patch.patchToken, formId, targetRef },
          }),
        );
      }
    };
    // Pick up a patch delivered before this form mounted (the common case
    // right after navigating here from the review UI).
    const key = patchKey(formId, targetRef);
    const pending = pendingPatches.get(key);
    if (pending) {
      pendingPatches.delete(key);
      consume(pending);
    }
    const handleEvent = (event: Event) => {
      const patch = (event as CustomEvent<FormDraftPatch>).detail;
      if (!patch) return;
      if (patch.formId !== formId || patch.targetRef !== targetRef) return;
      pendingPatches.delete(patchKey(patch.formId, patch.targetRef));
      consume(patch);
    };
    window.addEventListener(FORM_DRAFT_PATCH_EVENT, handleEvent);
    return () => window.removeEventListener(FORM_DRAFT_PATCH_EVENT, handleEvent);
  }, [formId, targetRef]);
}

/** One field's dirty/conflict resolution as §3.3 requires it be shown: a
 * field whose current value is unchanged from its seed applies directly
 * (`state: "clean"`); a dirty field whose current value already equals the
 * proposed one has nothing to resolve either (`"clean"` -- there is no
 * actual disagreement to preview); only a dirty field whose value DIFFERS
 * from the proposed one is a real conflict requiring a per-field choice. */
export interface FieldConflict {
  fieldName: string;
  currentValue: string;
  proposedValue: string;
  rationale: string;
  state: "clean" | "conflict";
}

/** §3.3's conflict classification, pure so both the review UI and its tests
 * can call it without mounting a form. `getCurrentValue`/`isDirty` come from
 * the receiving form's own state (it alone owns dirty-tracking, per the
 * phase instruction to keep each form's own state ownership). */
export function classifyFormDraftConflicts(
  patch: FormDraftPatch,
  currentValues: Record<string, string>,
  dirtyFields: Record<string, boolean>,
): FieldConflict[] {
  return patch.fields.map((f) => {
    const current = currentValues[f.fieldName] ?? "";
    const dirty = dirtyFields[f.fieldName] ?? false;
    const state: FieldConflict["state"] = dirty && current !== f.value ? "conflict" : "clean";
    return { fieldName: f.fieldName, currentValue: current, proposedValue: f.value, rationale: f.rationale, state };
  });
}

/** A field never chooses "keep" vs "replace" as a stored preference -- it
 * either applies immediately (`"clean"`) or waits for one explicit click.
 * `"keep"` leaves the developer's typed value untouched; `"replace"` writes
 * the proposed value into the field. Both are terminal for that field and
 * that patch (§3.3: "選ばれなかった field は元のまま残る"). */
export type FieldResolution = "keep" | "replace";

/** What a receiving form gives `useFormDraftReceiver` for one field: its
 * live value, whether it is dirty (differs from the form's own seed), and a
 * setter. The form keeps full ownership of its own state -- this hook only
 * reads and writes through the setter it is handed, per the phase
 * instruction not to lift state into a shared owner. */
export interface FormDraftFieldBinding {
  value: string;
  dirty: boolean;
  setValue: (value: string) => void;
}

/**
 * The receiving half of prefill for one form instance (§3.2/§3.3). Every
 * field that is NOT in conflict (clean, or simply not dirty) is written
 * through its setter the moment a patch for this `(formId, targetRef)`
 * arrives. A dirty field whose value differs from the proposal's is held
 * back and surfaced via the returned `conflicts` list until the developer
 * calls `resolveField` for it -- `"keep"` drops it with no further effect,
 * `"replace"` writes the proposed value through its setter.
 */
export function useFormDraftReceiver(
  formId: string,
  targetRef: string,
  fields: Record<string, FormDraftFieldBinding>,
  /** Optional: a form that drafts a new keyed sub-object (today only
   * `solution_design.option`'s `option_key`, carried via a proposal item's
   * `subject_ref`) reads the patch's `selectedItemRef` through this
   * binding. Two rules, both required by the same "honour the sub-address"
   * contract:
   * - Not yet dirty (the developer has typed no key of their own): the
   *   identity is written from the patch, exactly like any other clean
   *   field -- there is nothing to CONFLICT with an identity nobody chose
   *   yet.
   * - Already dirty AND it disagrees with the patch's `selectedItemRef`:
   *   the WHOLE patch is ignored, fields included. A patch for Option
   *   "opt-1" must never land its title/approach/etc. onto whatever option
   *   the developer happens to have open as "opt-2" -- silently applying
   *   fields addressed at a different sub-object is the same defect as
   *   delivering a patch to the wrong target_ref (§3.2).
   */
  identity?: FormDraftFieldBinding,
  prepareChildren?: (patch: FormDraftPatch) => {
    patch: FormDraftPatch; bindings: Record<string, FormDraftFieldBinding>;
  } | null,
): { conflicts: FieldConflict[]; resolveField: (fieldName: string, resolution: FieldResolution) => void } {
  const [patch, setPatch] = useState<FormDraftPatch | null>(null);
  const [resolvedFields, setResolvedFields] = useState<Set<string>>(new Set());
  const fieldsRef = useRef(fields);
  const identityRef = useRef(identity);
  const prepareRef = useRef(prepareChildren);
  const [childBindings, setChildBindings] = useState<Record<string, FormDraftFieldBinding>>({});
  // Same rule as above, and the same ordering reason: this effect is
  // declared before `useFormDraftInbox` so a patch queued before mount is
  // matched against the form's current values, not the first render's.
  useEffect(() => {
    fieldsRef.current = fields;
    identityRef.current = identity;
    prepareRef.current = prepareChildren;
  });

  useFormDraftInbox(formId, targetRef, (incoming) => {
    let bindings = fieldsRef.current;
    if (incoming.childOps.length) {
      const prepared = prepareRef.current?.(incoming);
      if (!prepared) return false;
      incoming = prepared.patch;
      bindings = { ...bindings, ...prepared.bindings };
      setChildBindings(prepared.bindings);
    }
    if (incoming.childOps.length || incoming.relations.length ||
        incoming.fields.some((f) => !bindings[f.fieldName])) return false;
    const currentIdentity = identityRef.current;
    if (currentIdentity) {
      if (currentIdentity.dirty) {
        if (incoming.selectedItemRef && incoming.selectedItemRef !== currentIdentity.value) {
          // Addressed at a different sub-object -- not for this draft.
          return false;
        }
      } else if (incoming.selectedItemRef) {
        currentIdentity.setValue(incoming.selectedItemRef);
      }
    }
    const currentValues: Record<string, string> = {};
    const dirtyFields: Record<string, boolean> = {};
    for (const f of incoming.fields) {
      currentValues[f.fieldName] = bindings[f.fieldName]?.value ?? "";
      dirtyFields[f.fieldName] = bindings[f.fieldName]?.dirty ?? false;
    }
    const classified = classifyFormDraftConflicts(incoming, currentValues, dirtyFields);
    for (const c of classified) {
      if (c.state === "clean") bindings[c.fieldName]?.setValue(c.proposedValue);
    }
    if (classified.some((c) => c.state === "conflict")) {
      setPatch(incoming);
      setResolvedFields(new Set());
    }
  });

  const conflicts: FieldConflict[] = [];
  const allBindings = { ...fields, ...childBindings };
  if (patch) {
    const currentValues: Record<string, string> = {};
    const dirtyFields: Record<string, boolean> = {};
    for (const f of patch.fields) {
      currentValues[f.fieldName] = allBindings[f.fieldName]?.value ?? "";
      dirtyFields[f.fieldName] = allBindings[f.fieldName]?.dirty ?? false;
    }
    for (const c of classifyFormDraftConflicts(patch, currentValues, dirtyFields)) {
      if (c.state === "conflict" && !resolvedFields.has(c.fieldName)) conflicts.push(c);
    }
  }

  const resolveField = (fieldName: string, resolution: FieldResolution) => {
    if (resolution === "replace") {
      const item = patch?.fields.find((f) => f.fieldName === fieldName);
      if (item) allBindings[fieldName]?.setValue(item.value);
    }
    setResolvedFields((prev) => {
      const next = new Set(prev);
      next.add(fieldName);
      return next;
    });
  };

  return { conflicts, resolveField };
}

// --- Delivery pipeline (Issue #452, §3's "配送順は契約") ---------------------
// navigate -> form ready confirm -> patch delivery -> ack -> prefill audit.
// The first two steps are the CALLER's job (navigating and polling readiness
// need `useNavigate()`/`useUiDraftRegistry()`, which this module -- a plain
// event bus -- deliberately does not depend on); these two helpers own the
// last two, the part that is genuinely about THIS module's own event bus.

/**
 * Poll `registry.read(formId, targetRef)` until it reports something other
 * than `"absent"` (i.e. a form has mounted and registered under this exact
 * key -- `"unreadable"` still counts: something IS open, even if its draft
 * cannot be read right now) or `timeoutMs` elapses. Returns whether the form
 * became ready. Never throws.
 */
export function waitForFormDraftReady(
  registry: { read: (formId: string, targetRef: string) => { outcome: string } } | null,
  formId: string,
  targetRef: string,
  timeoutMs = 4000,
): Promise<boolean> {
  return new Promise((resolve) => {
    if (!registry) {
      resolve(false);
      return;
    }
    const deadline = Date.now() + timeoutMs;
    const poll = () => {
      if (registry.read(formId, targetRef).outcome !== "absent") {
        resolve(true);
        return;
      }
      if (Date.now() >= deadline) {
        resolve(false);
        return;
      }
      window.setTimeout(poll, 150);
    };
    poll();
  });
}

/**
 * Delivers `patch` and resolves once the SUBSCRIBED destination form has
 * actually consumed it (an ack, per the module-level comment above), or
 * `timeoutMs` elapses with no ack -- e.g. the form never actually mounted
 * (a race with `waitForFormDraftReady`), or mounted for a different
 * `(formId, targetRef)`. A caller MUST treat `"timeout"` as a failed
 * delivery (§1.3/§1.7's `unavailable`, DD-INT-01) -- never as success, and
 * never call the server prefill-audit endpoint for it.
 */
export function dispatchFormDraftPatchAndWaitForAck(
  patch: FormDraftPatch,
  timeoutMs = 4000,
): Promise<"acked" | "timeout"> {
  return new Promise((resolve) => {
    let settled = false;
    const handleAck = (event: Event) => {
      const detail = (event as CustomEvent<FormDraftAckDetail>).detail;
      if (!detail || detail.patchToken !== patch.patchToken || detail.formId !== patch.formId || detail.targetRef !== patch.targetRef) return;
      if (settled) return;
      settled = true;
      window.removeEventListener(FORM_DRAFT_ACK_EVENT, handleAck);
      resolve("acked");
    };
    window.addEventListener(FORM_DRAFT_ACK_EVENT, handleAck);
    deliverFormDraftPatch(patch);
    window.setTimeout(() => {
      if (settled) return;
      settled = true;
      window.removeEventListener(FORM_DRAFT_ACK_EVENT, handleAck);
      const key = patchKey(patch.formId, patch.targetRef);
      if (pendingPatches.get(key)?.patchToken === patch.patchToken) pendingPatches.delete(key);
      resolve("timeout");
    }, timeoutMs);
  });
}
