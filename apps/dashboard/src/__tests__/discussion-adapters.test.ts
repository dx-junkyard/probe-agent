import { describe, expect, it } from "vitest";
import {
  classifyDiscussionError, DISCUSSION_ADAPTERS, resolveDiscussionCandidate,
} from "@/lib/discussion-adapters";

describe("discussion adapter target links", () => {
  it.each([
    ["ux_journey_step", "journey#step", "ux-design-studio"],
    ["ux_journey_step", "利用開始 & 予約#入力 / 確認", "ux-design-studio"],
    ["blueprint_lane_cell", "journey#step#user_action", "journey-blueprint"],
    ["blueprint_lane_cell", "利用開始 & 予約#入力 / 確認#user_action", "journey-blueprint"],
  ] as const)("preserves the complete %s identity for %s", (kind, targetRef, screenId) => {
    const link = DISCUSSION_ADAPTERS[kind].deepLink(targetRef);
    expect(link).not.toBeNull();
    const url = new URL(link!, "http://localhost");
    const candidate = resolveDiscussionCandidate(screenId, url.search);
    expect(candidate?.target).toEqual({
      screen_id: screenId,
      scope: "element",
      target_kind: kind,
      target_ref: targetRef,
    });
  });

  it.each([
    ["ux_journey_step", "journey"],
    ["ux_journey_step", "journey#"],
    ["ux_journey_step", "#step"],
    ["ux_journey_step", "journey#step#extra"],
    ["blueprint_lane_cell", "journey#step"],
    ["blueprint_lane_cell", "journey##user_action"],
    ["blueprint_lane_cell", "journey#step#"],
    ["blueprint_lane_cell", "journey#step#user_action#extra"],
  ] as const)("does not redirect malformed %s ref %s to another target", (kind, targetRef) => {
    expect(DISCUSSION_ADAPTERS[kind].deepLink(targetRef)).toBeNull();
  });
});

// Issue #456: no adapter has a wired handler yet, so the always-empty state
// is the honest completion condition -- pinned here alongside the Python
// side's own `test_no_adapter_derives_prefill_form_before_a_handler_is_
// registered` so a future PR that adds ONE side's declaration without the
// other is caught on this side too.
describe("discussion adapter prefill handler registration (Issue #456)", () => {
  it("has no prefillHandlerId registered on any adapter yet", () => {
    for (const adapter of Object.values(DISCUSSION_ADAPTERS)) {
      expect(adapter.prefillHandlerId).toBeNull();
    }
  });
});

// Issue #453 (Epic #443 Phase 4): the 8 Vision-to-Feature kinds' live
// selection on the 3 new screens, mirroring the server-side
// `test_discussion_target_expansion.py` fixtures.
describe("Vision-to-Feature target expansion (Issue #453)", () => {
  it("resolves a Stakeholder from ?node= on stakeholder-value-network", () => {
    const candidate = resolveDiscussionCandidate("stakeholder-value-network", "?node=sh-1");
    expect(candidate?.target).toEqual({
      screen_id: "stakeholder-value-network", scope: "entity",
      target_kind: "stakeholder", target_ref: "sh-1",
    });
  });

  it("does not resolve a Stakeholder candidate without ?node=", () => {
    expect(resolveDiscussionCandidate("stakeholder-value-network", "?edge=ex-1")).toBeNull();
  });

  it("resolves an Objective from ?objective= in the objectives view", () => {
    const candidate = resolveDiscussionCandidate("objective-map", "?objective=obj-1");
    expect(candidate?.target.target_kind).toBe("product_objective");
    expect(candidate?.target.target_ref).toBe("obj-1");
  });

  it("resolves a Milestone over an Objective when both are present (Gap Workbench precedence)", () => {
    const candidate = resolveDiscussionCandidate("objective-map", "?objective=obj-1&milestone=ms-1");
    expect(candidate?.target.target_kind).toBe("product_milestone");
    expect(candidate?.target.target_ref).toBe("ms-1");
  });

  it("resolves a Gap only in the ?view=gaps lane, never the objectives lane", () => {
    expect(resolveDiscussionCandidate("objective-map", "?gap=gap-1")).toBeNull();
    const candidate = resolveDiscussionCandidate("objective-map", "?view=gaps&gap=gap-1");
    expect(candidate?.target.target_kind).toBe("product_gap");
    expect(candidate?.target.target_ref).toBe("gap-1");
  });

  it("never resolves an Objective/Milestone candidate while in the gaps view", () => {
    expect(resolveDiscussionCandidate("objective-map", "?view=gaps&objective=obj-1")).toBeNull();
    expect(resolveDiscussionCandidate("objective-map", "?view=gaps&milestone=ms-1")).toBeNull();
  });

  it("has no live-selection route for purpose_element/purpose_relation/stakeholder_need/product_feature", () => {
    for (const [screenId, search] of [
      ["overview", "?element=beneficiary_problem"],
      ["stakeholder-value-network", "?need=need-1"],
      ["ux-design-studio", "?feature=feat-1"],
    ] as const) {
      // None of these screens declare a CANDIDATE_PRIORITY entry for these
      // kinds, so a made-up param name must never accidentally resolve one.
      const candidate = resolveDiscussionCandidate(screenId, search);
      expect(candidate?.target.target_kind).not.toBe("purpose_element");
      expect(candidate?.target.target_kind).not.toBe("purpose_relation");
      expect(candidate?.target.target_kind).not.toBe("stakeholder_need");
      expect(candidate?.target.target_kind).not.toBe("product_feature");
    }
  });

  it("deep-links every new entity/element kind to a screen among its own screenIds", () => {
    const cases: Array<[keyof typeof DISCUSSION_ADAPTERS, string]> = [
      ["purpose_element", "beneficiary_problem"],
      ["purpose_relation", "problem_to_change:beneficiary_problem->desired_change"],
      ["stakeholder", "sh-1"],
      ["stakeholder_need", "need-1"],
      ["product_objective", "obj-1"],
      ["product_milestone", "ms-1"],
      ["product_gap", "gap-1"],
    ];
    for (const [kind, ref] of cases) {
      const adapter = DISCUSSION_ADAPTERS[kind];
      const link = adapter.deepLink(ref);
      expect(link).not.toBeNull();
      const path = new URL(link!, "http://localhost").pathname;
      const screenIdOfPath = (p: string) => (p === "/" ? "overview" : p.slice(1));
      expect(adapter.screenIds).toContain(screenIdOfPath(path));
    }
  });

  it("product_feature has no screen of its own yet, so deepLink is honestly null", () => {
    expect(DISCUSSION_ADAPTERS.product_feature.deepLink("feat-1")).toBeNull();
  });
});

describe("classifyDiscussionError (Issue #456)", () => {
  it("maps a structurally-unsupported code to a non-retryable Japanese reason", () => {
    const result = classifyDiscussionError({ code: "discussion_target_kind_unregistered" });
    expect(result.operationResult).toBe("unsupported");
    expect(result.retryable).toBe(false);
    expect(result.message).not.toHaveLength(0);
  });

  it("maps a not_applicable code distinctly from unsupported", () => {
    const result = classifyDiscussionError({ code: "prefill_form_unregistered" });
    expect(result.operationResult).toBe("not_applicable");
    expect(result.retryable).toBe(false);
  });

  it("treats an unrecognised code as a possibly-transient, retryable failure", () => {
    const result = classifyDiscussionError({ code: "some_future_code_this_client_does_not_know" });
    expect(result.operationResult).toBe("unavailable");
    expect(result.retryable).toBe(true);
  });

  it("treats a plain network error (no code at all) as retryable", () => {
    const result = classifyDiscussionError(new Error("Failed to fetch"));
    expect(result.operationResult).toBe("unavailable");
    expect(result.retryable).toBe(true);
  });

  it("does not throw on a null/undefined/primitive error", () => {
    expect(() => classifyDiscussionError(null)).not.toThrow();
    expect(() => classifyDiscussionError(undefined)).not.toThrow();
    expect(() => classifyDiscussionError("plain string")).not.toThrow();
  });
});
