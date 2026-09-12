// Issue #459 (Epic #457, docs/01-specifications/ux/decision-discussion-workflow.md §2): the generic "戻り先"
// banner + its `returnTo` URL helper.

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ReturnToBanner, appendReturnTo } from "@/components/discussion-return-banner";

describe("appendReturnTo", () => {
  it("replaces a previous return target and keeps the fragment after the query", () => {
    expect(appendReturnTo("/ux-design-studio?returnTo=old#requirement", "/objective-map"))
      .toBe("/ux-design-studio?returnTo=%2Fobjective-map#requirement");
  });
  it("appends as the first query param when the url has none", () => {
    expect(appendReturnTo("/ux-design-studio", "/objective-map?view=gaps&gap=g1")).toBe(
      "/ux-design-studio?returnTo=%2Fobjective-map%3Fview%3Dgaps%26gap%3Dg1",
    );
  });

  it("appends with & when the url already has a query string", () => {
    expect(appendReturnTo("/ux-design-studio?requirement=r1", "/objective-map?view=gaps&gap=g1")).toBe(
      "/ux-design-studio?requirement=r1&returnTo=%2Fobjective-map%3Fview%3Dgaps%26gap%3Dg1",
    );
  });

  it("returns the url unchanged when there is nothing to return to", () => {
    expect(appendReturnTo("/ux-design-studio?requirement=r1", null)).toBe("/ux-design-studio?requirement=r1");
  });
});

describe("ReturnToBanner", () => {
  function renderAt(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <ReturnToBanner />
      </MemoryRouter>,
    );
  }

  it("renders nothing when there is no returnTo param", () => {
    renderAt("/ux-design-studio?requirement=r1");
    expect(screen.queryByTestId("discussion-return-banner")).not.toBeInTheDocument();
  });

  it("renders a link to the same-origin relative returnTo target", () => {
    renderAt(`/ux-design-studio?requirement=r1&returnTo=${encodeURIComponent("/objective-map?view=gaps&gap=g1")}`);
    const link = screen.getByTestId("discussion-return-banner-link");
    expect(link).toHaveAttribute("href", "/objective-map?view=gaps&gap=g1");
  });

  it("refuses a non-relative (protocol-relative or absolute) returnTo value", () => {
    renderAt(`/ux-design-studio?returnTo=${encodeURIComponent("//evil.example.com")}`);
    expect(screen.queryByTestId("discussion-return-banner")).not.toBeInTheDocument();
  });

  it("refuses an absolute URL passed as returnTo", () => {
    renderAt(`/ux-design-studio?returnTo=${encodeURIComponent("https://evil.example.com")}`);
    expect(screen.queryByTestId("discussion-return-banner")).not.toBeInTheDocument();
  });
});
