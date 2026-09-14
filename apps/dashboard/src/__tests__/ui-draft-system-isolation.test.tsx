import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AppLayout } from "@/components/layout/app-layout";
import { useUiDraftRegistry, useUiDraftSource } from "@/lib/ui-draft";

const auth = vi.hoisted(() => ({ user: { id: 1 }, loading: false, systemId: 1 }));
vi.mock("@/api/auth", () => ({ useAuth: () => auth }));
vi.mock("@/api/hooks", () => ({ useSystemState: () => ({ data: null }) }));
vi.mock("@/components/layout/sidebar", () => ({ Sidebar: () => null, MOBILE_NAV_DRAWER_ID: "nav" }));
vi.mock("@/components/layout/header", () => ({ Header: () => null }));
vi.mock("@/components/help-mode-layer", () => ({ HelpModeLayer: () => null }));
vi.mock("@/components/assistant-panel", () => ({
  AssistantPanel: () => {
    const registry = useUiDraftRegistry();
    const [value, setValue] = useState("not read");
    return <button onClick={() => {
      const read = registry!.read("ux_journey.revision", "same-target");
      setValue(read.outcome === "readable" ? read.snapshot.fields[0].value : read.outcome);
    }}>{value}</button>;
  },
}));

function Form() {
  const [value, setValue] = useState(`system ${auth.systemId}`);
  useUiDraftSource("ux_journey.revision", "same-target", () => ({
    fields: [{ fieldName: "title", value, dirty: true, validationError: "" }],
    selectedItemRef: "", activeTab: "", comparisonTarget: "", localRevisionToken: value,
  }));
  return <input aria-label="draft" value={value} onChange={(event) => setValue(event.target.value)} />;
}

describe("draft System isolation in AppLayout", () => {
  it("drops the old form and Assistant state when switching Systems with identical target refs", () => {
    const tree = () => <MemoryRouter><Routes>
      <Route element={<AppLayout />}><Route index element={<Form />} /></Route>
    </Routes></MemoryRouter>;
    const view = render(tree());
    fireEvent.change(screen.getByLabelText("draft"), { target: { value: "system A private draft" } });
    fireEvent.click(screen.getByRole("button", { name: "not read" }));
    expect(screen.getByRole("button", { name: "system A private draft" })).toBeInTheDocument();
    auth.systemId = 2;
    view.rerender(tree());
    expect(screen.getByLabelText("draft")).toHaveValue("system 2");
    fireEvent.click(screen.getByRole("button", { name: "not read" }));
    expect(screen.getByRole("button", { name: "system 2" })).toBeInTheDocument();
    expect(screen.queryByText("system A private draft")).not.toBeInTheDocument();
  });
});
