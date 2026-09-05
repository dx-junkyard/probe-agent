/// <reference types="vitest/globals" />
// Issue #445 (Epic #443 Phase 2): the UI draft registry
// (`src/lib/ui-draft.ts`), tested in isolation from the Assistant Panel.
//
// Shape mirrors `unsaved-work.tsx`'s own registry: register/unregister,
// a getter read only at call time, and tolerance of a getter that throws.

import { act, render } from "@testing-library/react";
import { useState } from "react";
import {
  UiDraftProvider, useUiDraftRegistry, useUiDraftSource, type UiDraftSnapshot,
} from "@/lib/ui-draft";

function snapshot(value: string): UiDraftSnapshot {
  return {
    fields: [{ fieldName: "title", value, dirty: true, validationError: "" }],
    selectedItemRef: "",
    activeTab: "",
    comparisonTarget: "",
    localRevisionToken: value,
  };
}

/** Registers a draft for (formId, targetRef) whose value can be changed from
 * the test via `setValue` (exposed on the DOM through a data attribute so
 * the test does not need to reach into React internals). */
function DraftSource({
  formId,
  targetRef,
  initial,
  onSetValue,
}: {
  formId: string;
  targetRef: string;
  initial: string;
  onSetValue?: (setter: (v: string) => void) => void;
}) {
  const [value, setValue] = useState(initial);
  onSetValue?.(setValue);
  useUiDraftSource(formId, targetRef, () => snapshot(value));
  return null;
}

function ThrowingSource({ formId, targetRef }: { formId: string; targetRef: string }) {
  useUiDraftSource(formId, targetRef, () => {
    throw new Error("cannot read this form's state");
  });
  return null;
}

function Probe({ onReady }: { onReady: (registry: ReturnType<typeof useUiDraftRegistry>) => void }) {
  onReady(useUiDraftRegistry());
  return null;
}

describe("UiDraftProvider / useUiDraftSource registry", () => {
  test("a mounted source is readable by (formId, targetRef)", () => {
    let registry: ReturnType<typeof useUiDraftRegistry> = null;
    render(
      <UiDraftProvider>
        <Probe onReady={(r) => { registry = r; }} />
        <DraftSource formId="ux_journey.revision" targetRef="jny-1" initial="hello" />
      </UiDraftProvider>,
    );
    expect(registry!.read("ux_journey.revision", "jny-1")).toEqual({
      outcome: "readable", snapshot: snapshot("hello"),
    });
  });

  test("no form registered for that (formId, targetRef) is absent, not a crash", () => {
    let registry: ReturnType<typeof useUiDraftRegistry> = null;
    render(
      <UiDraftProvider>
        <Probe onReady={(r) => { registry = r; }} />
        <DraftSource formId="ux_journey.revision" targetRef="jny-1" initial="hello" />
      </UiDraftProvider>,
    );
    expect(registry!.read("ux_journey.revision", "a-different-journey")).toEqual({ outcome: "absent" });
    expect(registry!.read("some_other_form", "jny-1")).toEqual({ outcome: "absent" });
  });

  test("read always reflects the CURRENT value, read only when called", () => {
    let registry: ReturnType<typeof useUiDraftRegistry> = null;
    let setValue: ((v: string) => void) | null = null;
    render(
      <UiDraftProvider>
        <Probe onReady={(r) => { registry = r; }} />
        <DraftSource
          formId="ux_journey.revision"
          targetRef="jny-1"
          initial="first"
          onSetValue={(s) => { setValue = s; }}
        />
      </UiDraftProvider>,
    );
    expect(registry!.read("ux_journey.revision", "jny-1")).toEqual({
      outcome: "readable", snapshot: snapshot("first"),
    });
    act(() => setValue!("second"));
    expect(registry!.read("ux_journey.revision", "jny-1")).toEqual({
      outcome: "readable", snapshot: snapshot("second"),
    });
  });

  // docs/ai-discussion-adapter.md §2.6: a registered form that cannot answer
  // is `unreadable`, which is NOT the same answer as "no form was open" --
  // folding them would make the assistant describe a screen the developer is
  // not looking at. The throw is still never propagated, and no draft content
  // is fabricated.
  test("a getter that throws reads as unreadable, distinct from absent", () => {
    let registry: ReturnType<typeof useUiDraftRegistry> = null;
    render(
      <UiDraftProvider>
        <Probe onReady={(r) => { registry = r; }} />
        <ThrowingSource formId="ux_journey.revision" targetRef="jny-1" />
      </UiDraftProvider>,
    );
    expect(() => registry!.read("ux_journey.revision", "jny-1")).not.toThrow();
    expect(registry!.read("ux_journey.revision", "jny-1")).toEqual({ outcome: "unreadable" });
    // ...and an unregistered target on the same provider is still `absent`.
    expect(registry!.read("ux_journey.revision", "other")).toEqual({ outcome: "absent" });
  });

  test("unmounting a form unregisters it", () => {
    let registry: ReturnType<typeof useUiDraftRegistry> = null;
    const { unmount } = render(
      <UiDraftProvider>
        <Probe onReady={(r) => { registry = r; }} />
        <DraftSource formId="ux_journey.revision" targetRef="jny-1" initial="hello" />
      </UiDraftProvider>,
    );
    expect(registry!.read("ux_journey.revision", "jny-1").outcome).toBe("readable");
    unmount();
    expect(registry!.read("ux_journey.revision", "jny-1")).toEqual({ outcome: "absent" });
  });

  test("an empty targetRef never registers (no thread can ever be about it)", () => {
    let registry: ReturnType<typeof useUiDraftRegistry> = null;
    render(
      <UiDraftProvider>
        <Probe onReady={(r) => { registry = r; }} />
        <DraftSource formId="ux_journey_step.revision" targetRef="" initial="unkeyed" />
      </UiDraftProvider>,
    );
    expect(registry!.read("ux_journey_step.revision", "")).toEqual({ outcome: "absent" });
  });

  test("useUiDraftRegistry() outside a provider is null, and useUiDraftSource is a safe no-op with it", () => {
    let registry: ReturnType<typeof useUiDraftRegistry> = undefined as unknown as null;
    expect(() =>
      render(
        <>
          <Probe onReady={(r) => { registry = r; }} />
          <DraftSource formId="ux_journey.revision" targetRef="jny-1" initial="hello" />
        </>,
      ),
    ).not.toThrow();
    expect(registry).toBeNull();
  });
});
