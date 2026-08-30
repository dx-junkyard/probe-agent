// Issue #427 review 0.4: a discard confirmation before the selected entity
// changes.
//
// Keying the work panels per entity (see `pages/objective-map.tsx`) fixed the
// data-integrity half of that finding -- text typed for Objective A can no
// longer be submitted as B's revision. But a remount DISCARDS that text
// silently, so the developer loses work by clicking the next row in a list.
// This is the other half: ask first.
//
// Why a registry of getters rather than lifting the form state: the forms are
// the right owners of their own drafts, and hoisting six components' fields
// into the page to answer one yes/no question would couple every form to the
// page's selection logic. Each form instead declares "am I dirty" and the page
// asks the question at exactly one moment.
//
// The registry lives in a ref, not state: registering must not re-render, and
// the answer is only ever read inside an event handler.

import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from "react";

interface UnsavedWorkApi {
  /** Returns an unregister function; call it on unmount. */
  register: (id: string, isDirty: () => boolean) => () => void;
  hasUnsavedWork: () => boolean;
}

const UnsavedWorkContext = createContext<UnsavedWorkApi | null>(null);

export function UnsavedWorkProvider({ children }: { children: ReactNode }) {
  const registry = useRef(new Map<string, () => boolean>());
  const api = useMemo<UnsavedWorkApi>(
    () => ({
      register(id, isDirty) {
        registry.current.set(id, isDirty);
        return () => {
          registry.current.delete(id);
        };
      },
      hasUnsavedWork() {
        for (const isDirty of registry.current.values()) {
          // A form that cannot answer is not a reason to block navigation:
          // the guard exists to protect typed work, and turning its own
          // failure into a prompt the developer cannot clear would be worse
          // than the loss it prevents.
          try {
            if (isDirty()) return true;
          } catch {
            continue;
          }
        }
        return false;
      },
    }),
    [],
  );
  return <UnsavedWorkContext.Provider value={api}>{children}</UnsavedWorkContext.Provider>;
}

export function useUnsavedWork(): UnsavedWorkApi | null {
  return useContext(UnsavedWorkContext);
}

/**
 * Declares this form's unsaved state. `id` must be unique per mounted form.
 *
 * Re-registers whenever `isDirty` flips rather than holding the value in a
 * ref: a `Map.set` is trivial, and the ref version had to be written during
 * render, which React forbids (a ref is not a render input). The swap happens
 * inside an effect, so no read can land between the cleanup and the
 * re-register -- the only reader is a click handler.
 */
export function useDirtyGuard(id: string, isDirty: boolean): void {
  const api = useUnsavedWork();
  useEffect(() => {
    if (!api) return;
    return api.register(id, () => isDirty);
  }, [api, id, isDirty]);
}

/** The one prompt, so its wording cannot drift between call sites. */
export function confirmDiscardUnsavedWork(): boolean {
  return window.confirm(
    "保存していない入力があります。破棄して選択を変更しますか?\n"
      + "(「記録する」を押していない内容は失われます)",
  );
}
