// UI draft registry (Issue #445, Epic #443 Phase 2).
//
// docs/01-specifications/capabilities/ai-discussion-adapter.md §2.3/§2.5 is the canonical contract. Shaped
// exactly like `components/product-objective/unsaved-work.tsx`'s
// `UnsavedWorkProvider` / `useDirtyGuard`: each form declares its own draft
// state via a getter, and the registry lives in a ref (registering must not
// re-render).
//
// It differs from `unsaved-work.tsx` in one deliberate way: a getter that
// THROWS is reported as `"unreadable"`, not as absent. `unsaved-work.tsx`
// can treat its own failure as "not dirty" because the worst case there is a
// prompt the developer cannot clear. Here the two cases lead somewhere
// different -- "a form is open for this target but could not be read" versus
// "no form was open" is §2.6's distinction, and answering as though nothing
// were being edited describes a screen the developer is not looking at. A
// draft is still never FABRICATED from a failed getter: `"unreadable"`
// carries no field content at all.
//
// Unlike `unsaved-work.tsx` (page-scoped: the guard and its one reader live
// in the same page tree), this registry is mounted once at the app root
// (`components/layout/app-layout.tsx`) because its two sides live in
// different trees: the forms that register a draft live on whichever page
// is open (e.g. `/ux-design-studio`), while the one reader -- the globally
// mounted `AssistantPanel` -- reads it from OUTSIDE that page's tree.
//
// The panel reads a form's current snapshot exactly ONCE, at turn start
// (§2.5), and holds it for that whole turn -- the same discipline
// `assistant-panel.tsx`'s `VoiceTurnTarget` already applies to voice. This
// module only provides the read; freezing the snapshot for a turn is the
// caller's job (see `captureUiDraft` usage in `assistant-panel.tsx`).

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ApiError } from "@/api/client";

export interface UiDraftFieldSnapshot {
  fieldName: string;
  value: string;
  dirty: boolean;
  validationError: string;
}

/** What one mounted form reports when asked for its current draft. Every
 * string here is free text the developer typed -- never redacted client
 * side (redaction is a server-side, Principle 9 concern applied once, in
 * `app/ui_draft_context.py`, right before the LLM call; duplicating it here
 * would be a second, driftable copy of that rule). */
export interface UiDraftSnapshot {
  fields: UiDraftFieldSnapshot[];
  /** "" = no selection (§2.2). */
  selectedItemRef: string;
  activeTab: string;
  comparisonTarget: string;
  /** A client-side change hint for THIS draft's content. The server derives
   * its own content digest for `ui_draft_changed` (§2.6). Any stable string that
   * changes exactly when the content does is sufficient -- it is never
   * cryptographic, only a change signal. This source hint stays in memory;
   * the registry replaces it with an opaque token before returning a read. */
  localRevisionToken: string;
}

type UiDraftGetter = () => UiDraftSnapshot | null;

/** §2.6's three client-observable outcomes of asking a form for its draft.
 * `absent` = nothing is registered for this (formId, targetRef);
 * `unreadable` = a form IS registered but its getter threw. Merging them
 * would lose the distinction the server's `ui_draft_state` exists to carry. */
export type UiDraftReadResult =
  | { outcome: "absent" }
  | { outcome: "unreadable" }
  | { outcome: "readable"; snapshot: UiDraftSnapshot };

interface UiDraftApi {
  /** Returns an unregister function; call it on unmount / when
   * formId+targetRef changes. */
  register: (formId: string, targetRef: string, getDraft: UiDraftGetter) => () => void;
  /** Never throws. See `UiDraftReadResult` for why the two failure shapes
   * are distinct rather than a single `null`. */
  read: (formId: string, targetRef: string) => UiDraftReadResult;
}

const UiDraftContext = createContext<UiDraftApi | null>(null);

function registryKey(formId: string, targetRef: string): string {
  return `${formId}|${targetRef}`;
}

export function UiDraftProvider({ children }: { children: ReactNode }) {
  const registry = useRef(new Map<string, UiDraftGetter>());
  const api = useMemo<UiDraftApi>(
    () => ({
      register(formId, targetRef, getDraft) {
        if (!targetRef) {
          // A form editing a not-yet-identified row (e.g. a new Journey
          // Step whose `step_key` is still blank) has no resolvable
          // discussion target to register under -- there is no thread this
          // draft could ever be asked about, so this is a harmless no-op
          // rather than polluting the registry under an empty key shared by
          // every such row.
          return () => {};
        }
        const key = registryKey(formId, targetRef);
        let previousSignature: string | undefined;
        let token = "";
        const readDraft = () => {
          const snapshot = getDraft();
          if (!snapshot) return null;
          // Never send a form's source token: existing forms use their raw
          // field JSON as a change hint, which can contain secrets and exceed
          // the API token bound. Compare only in memory and send an opaque
          // revision id, including dirty/validation/selection changes.
          const signature = JSON.stringify(snapshot);
          if (signature !== previousSignature) {
            token = Array.from(crypto.getRandomValues(new Uint8Array(16)),
              (byte) => byte.toString(16).padStart(2, "0")).join("");
            previousSignature = signature;
          }
          return { ...snapshot, localRevisionToken: token };
        };
        registry.current.set(key, readDraft);
        return () => {
          // Only clear the slot if it still belongs to THIS registration --
          // a fast remount (e.g. React StrictMode) can register the
          // replacement before the old cleanup runs.
          if (registry.current.get(key) === readDraft) {
            registry.current.delete(key);
          }
        };
      },
      read(formId, targetRef) {
        const getter = registry.current.get(registryKey(formId, targetRef));
        if (!getter) return { outcome: "absent" };
        let snapshot: UiDraftSnapshot | null;
        try {
          snapshot = getter();
        } catch {
          // A registered form that cannot answer is `unreadable`, NOT
          // absent: something IS open for this target. No content is
          // fabricated -- the server records the state and nothing else.
          return { outcome: "unreadable" };
        }
        // A getter may also legitimately answer "I have nothing to report"
        // (e.g. its collapsed revision form is not mounted); that is absent,
        // because no form is actually presenting a draft.
        return snapshot === null ? { outcome: "absent" } : { outcome: "readable", snapshot };
      },
    }),
    [],
  );
  return <UiDraftContext.Provider value={api}>{children}</UiDraftContext.Provider>;
}

export function useUiDraftRegistry(): UiDraftApi | null {
  return useContext(UiDraftContext);
}

/**
 * Declares this form's current draft under `(formId, targetRef)`.
 *
 * `getDraft` is read through a ref rather than re-registered on every
 * keystroke: the registry only needs the LATEST getter at read time (turn
 * start), so re-registering on every render would be pure churn. Re-runs the
 * registration effect only when `formId`/`targetRef` actually change.
 */
export function useUiDraftSource(
  formId: string,
  targetRef: string,
  getDraft: UiDraftGetter,
): void {
  const api = useUiDraftRegistry();
  const getterRef = useRef(getDraft);
  // Written in an effect, never during render: a ref is not a render input,
  // and React forbids mutating one while rendering. This is the same fix
  // `components/product-objective/unsaved-work.tsx` documents for
  // `useDirtyGuard`. Declared BEFORE the registration effect so it flushes
  // first -- the registered getter must never be called with a stale
  // closure. No dependency array: the latest getter is wanted after every
  // render, and the read itself only happens later, at turn start.
  useEffect(() => {
    getterRef.current = getDraft;
  });
  useEffect(() => {
    if (!api) return;
    return api.register(formId, targetRef, () => getterRef.current());
  }, [api, formId, targetRef]);
}

// --- useFormValidation (Issue #451) ------------------------------------------
//
// docs/01-specifications/capabilities/ai-discussion-adapter.md §2.8 is the canonical contract. Journey /
// Requirement / Solution Design forms were sending `validationError: ""` for
// every field, unconditionally -- a real save failure reached only a toast,
// never the draft context an AI conversation reads. This hook is the ONE
// place that turns a rejected save's `ApiError` (`fieldPath`/`section`, both
// structural per §2.8.1 -- never guessed from `.detail`'s message text) into
// per-field diagnostics a form can both DISPLAY inline and feed into its own
// `useUiDraftSource` `fields[].validationError`.
//
// Two axes this hook keeps separate on purpose (§2.8.3):
//   - `status`: idle / validating / invalid -- the lifecycle of the LATEST
//     save attempt. This is client-only bookkeeping; it never rides on the
//     wire inside `UiDraftContextIn` (that describes CURRENT input, not "how
//     did the last save go").
//   - `readable`/`dirty` (already `UiDraftContextIn`'s own §2.2 contract) --
//     unaffected by anything in this file.

/** The lifecycle of the most recent save attempt this hook instance has been
 * told about. `idle` = no attempt yet (or the last one succeeded); this is
 * NOT the same axis as a field's own dirty/readable state. */
export type FormValidationLifecycle = "idle" | "validating" | "invalid";

/** One field's (or the whole form's) diagnostic. `section` is carried even on
 * a field-level entry so a caller can group inline messages the same way a
 * whole-form message is grouped (§2.8.2). */
export interface FieldValidationDiagnostic {
  message: string;
  code: string;
  section: string;
}

export interface FormValidationSnapshot {
  status: FormValidationLifecycle;
  /** Keyed by field name -- ONLY for `field_path` values that are members of
   * the `knownFields` list passed to `resolveError` at the time it was
   * reported. Untouched fields keep their prior diagnostic across a new
   * `begin()`/`resolveError()` cycle (§2.8.4) -- only `clearField` or
   * `resolveSuccess` remove an entry. */
  fieldErrors: Record<string, FieldValidationDiagnostic>;
  /** A diagnostic that does not name one of this form's OWN known fields --
   * either `field_path === ""` (the server said "no specific field") or a
   * `field_path` this particular form does not render an input for (§2.8.2:
   * "未知 field_path はフォーム全体のエラー"). `null` = no such diagnostic
   * outstanding. Replaced wholesale by each new `resolveError`/
   * `resolveSuccess` (there is only ever one outstanding save attempt's
   * whole-form message, unlike per-field diagnostics which persist
   * independently). */
  formError: FieldValidationDiagnostic | null;
}

export interface UseFormValidationResult extends FormValidationSnapshot {
  /** Call immediately before issuing the save request. Returns a token --
   * pass the SAME token to `resolveError`/`resolveSuccess` so a response for
   * an OLDER attempt (this same target, resubmitted) can never land after a
   * newer one already resolved (§2.8.5). */
  begin(): number;
  /** Apply a rejected save's `ApiError`. Ignored (no state change at all) if
   * `token` is not this hook instance's MOST RECENT `begin()` token --
   * §2.8.5's stale-response guard. `knownFields` is this specific form's own
   * set of addressable inputs (not necessarily `UiDraftFormSpec.fields`; see
   * §2.8.2) -- an `error.fieldPath` outside it becomes `formError` instead of
   * a per-field entry, exactly like `error.fieldPath === ""`. */
  resolveError(token: number, error: ApiError, knownFields: readonly string[]): void;
  /** A successful save: clears every diagnostic and returns to `idle`. Same
   * stale-token guard as `resolveError`. */
  resolveSuccess(token: number): void;
  /** The developer edited this field -- drop ITS diagnostic only (§2.8.4).
   * Every other field's diagnostic, and any outstanding `formError`, is left
   * untouched: this call says nothing about whether they are still valid. */
  clearField(fieldName: string): void;
  /** Full reset (e.g. an explicit "discard/cancel" action). Component
   * remount already isolates one target's validation state from another's
   * (§2.8.5) -- this is for within-instance resets only. */
  reset(): void;
}

const IDLE_SNAPSHOT: FormValidationSnapshot = { status: "idle", fieldErrors: {}, formError: null };

/**
 * One save operation's client-side validation lifecycle (§2.8). A component
 * with more than one independent save action (e.g. a create form AND a
 * revision form on the same detail page) calls this once PER action -- each
 * call is its own token sequence and its own diagnostic set.
 */
export function useFormValidation(): UseFormValidationResult {
  const [snapshot, setSnapshot] = useState<FormValidationSnapshot>(IDLE_SNAPSHOT);
  const tokenRef = useRef(0);

  const begin = useCallback((): number => {
    const token = tokenRef.current + 1;
    tokenRef.current = token;
    setSnapshot((prev) => ({ ...prev, status: "validating" }));
    return token;
  }, []);

  const resolveError = useCallback(
    (token: number, error: ApiError, knownFields: readonly string[]): void => {
      if (token !== tokenRef.current) return; // stale response -- §2.8.5
      const fieldPath = error.fieldPath;
      const diagnostic: FieldValidationDiagnostic = {
        message: error.detail || "",
        code: error.code ?? "",
        section: error.section,
      };
      setSnapshot((prev) => {
        if (fieldPath && knownFields.includes(fieldPath)) {
          return {
            status: "invalid",
            fieldErrors: { ...prev.fieldErrors, [fieldPath]: diagnostic },
            formError: prev.formError,
          };
        }
        // §2.8.2: field_path === "" OR unknown to THIS form -> whole-form.
        return { status: "invalid", fieldErrors: prev.fieldErrors, formError: diagnostic };
      });
    },
    [],
  );

  const resolveSuccess = useCallback((token: number): void => {
    if (token !== tokenRef.current) return; // stale response -- §2.8.5
    setSnapshot(IDLE_SNAPSHOT);
  }, []);

  const clearField = useCallback((fieldName: string): void => {
    setSnapshot((prev) => {
      if (!(fieldName in prev.fieldErrors)) return prev;
      const nextFieldErrors = { ...prev.fieldErrors };
      delete nextFieldErrors[fieldName];
      const stillInvalid = prev.formError !== null || Object.keys(nextFieldErrors).length > 0;
      return { status: stillInvalid ? "invalid" : "idle", fieldErrors: nextFieldErrors, formError: prev.formError };
    });
  }, []);

  const reset = useCallback((): void => {
    tokenRef.current += 1; // also invalidates any response already in flight
    setSnapshot(IDLE_SNAPSHOT);
  }, []);

  return { ...snapshot, begin, resolveError, resolveSuccess, clearField, reset };
}
