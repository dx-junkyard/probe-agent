// UI draft registry (Issue #445, Epic #443 Phase 2).
//
// docs/ai-discussion-adapter.md §2.3/§2.5 is the canonical contract. Shaped
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

import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";

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
  /** A client-side digest of THIS draft's content, compared turn-to-turn by
   * the server to derive `ui_draft_changed` (§2.6). Any stable string that
   * changes exactly when the content does is sufficient -- it is never
   * cryptographic, only a change signal. */
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
        registry.current.set(key, getDraft);
        return () => {
          // Only clear the slot if it still belongs to THIS registration --
          // a fast remount (e.g. React StrictMode) can register the
          // replacement before the old cleanup runs.
          if (registry.current.get(key) === getDraft) {
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
  getterRef.current = getDraft;
  useEffect(() => {
    if (!api) return;
    return api.register(formId, targetRef, () => getterRef.current());
  }, [api, formId, targetRef]);
}
