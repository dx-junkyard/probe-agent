/// <reference types="vitest/globals" />
// Issue #432 (Epic #427) P2 review fix §3.6: `components/ui/tabs.tsx` follows
// the WAI-ARIA Tabs pattern -- `role="tablist"` / `role="tab"` /
// `role="tabpanel"`, `aria-selected` / `aria-controls` / `aria-labelledby`,
// and roving-tabindex arrow-key navigation. This is a SHARED component (see
// the file header for the full list of screens that use it), so this test
// exercises only the primitive itself; `dashboard-contracts.test.tsx`'s
// Repository "Symbols" tab test exercises one real caller after the change.

import { render, screen, fireEvent } from "@testing-library/react";
import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

function ThreeTabs() {
  const [value, setValue] = useState("a");
  return (
    <Tabs value={value} onValueChange={setValue}>
      <TabsList data-testid="list">
        <TabsTrigger value="a">A</TabsTrigger>
        <TabsTrigger value="b">B</TabsTrigger>
        <TabsTrigger value="c">C</TabsTrigger>
      </TabsList>
      <TabsContent value="a">Panel A</TabsContent>
      <TabsContent value="b">Panel B</TabsContent>
      <TabsContent value="c">Panel C</TabsContent>
    </Tabs>
  );
}

describe("Tabs: WAI-ARIA structure", () => {
  it("exposes tablist/tab/tabpanel roles with aria-selected/aria-controls/aria-labelledby", () => {
    render(<ThreeTabs />);

    expect(screen.getByRole("tablist")).toBeInTheDocument();
    const tabA = screen.getByRole("tab", { name: "A" });
    const tabB = screen.getByRole("tab", { name: "B" });
    const panel = screen.getByRole("tabpanel");

    expect(tabA).toHaveAttribute("aria-selected", "true");
    expect(tabB).toHaveAttribute("aria-selected", "false");
    expect(panel).toHaveTextContent("Panel A");

    // The active tab's id/aria-controls pair links to the visible panel's
    // id/aria-labelledby pair.
    expect(tabA.getAttribute("aria-controls")).toBe(panel.getAttribute("id"));
    expect(panel.getAttribute("aria-labelledby")).toBe(tabA.getAttribute("id"));
  });

  it("only the selected tab is Tab-key reachable (roving tabindex)", () => {
    render(<ThreeTabs />);
    expect(screen.getByRole("tab", { name: "A" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "B" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tab", { name: "C" })).toHaveAttribute("tabindex", "-1");
  });

  it("clicking a tab updates aria-selected and the visible panel", () => {
    render(<ThreeTabs />);
    fireEvent.click(screen.getByRole("tab", { name: "B" }));
    expect(screen.getByRole("tab", { name: "B" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Panel B");
  });

  it("ArrowRight/ArrowLeft move focus AND activate the adjacent tab, wrapping at the ends", () => {
    render(<ThreeTabs />);
    const tabA = screen.getByRole("tab", { name: "A" });
    tabA.focus();

    fireEvent.keyDown(tabA, { key: "ArrowRight" });
    const tabB = screen.getByRole("tab", { name: "B" });
    expect(tabB).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(tabB);

    fireEvent.keyDown(tabB, { key: "ArrowRight" });
    const tabC = screen.getByRole("tab", { name: "C" });
    expect(tabC).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(tabC);

    // Wraps from the last tab back to the first.
    fireEvent.keyDown(tabC, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "A" })).toHaveAttribute("aria-selected", "true");

    // ArrowLeft from the first tab wraps to the last.
    fireEvent.keyDown(screen.getByRole("tab", { name: "A" }), { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "C" })).toHaveAttribute("aria-selected", "true");
  });

  it("Home/End jump to the first/last tab", () => {
    render(<ThreeTabs />);
    const tabB = screen.getByRole("tab", { name: "B" });
    fireEvent.click(tabB);

    fireEvent.keyDown(tabB, { key: "End" });
    expect(screen.getByRole("tab", { name: "C" })).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(screen.getByRole("tab", { name: "C" }), { key: "Home" });
    expect(screen.getByRole("tab", { name: "A" })).toHaveAttribute("aria-selected", "true");
  });

  it("gives every Tabs instance its own tab/panel ids (no cross-instance collision)", () => {
    render(
      <>
        <ThreeTabs />
        <ThreeTabs />
      </>,
    );
    const tabsA = screen.getAllByRole("tab", { name: "A" });
    expect(tabsA).toHaveLength(2);
    expect(tabsA[0].getAttribute("id")).not.toBe(tabsA[1].getAttribute("id"));
  });
});
