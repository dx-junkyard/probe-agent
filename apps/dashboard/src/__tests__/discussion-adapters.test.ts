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
