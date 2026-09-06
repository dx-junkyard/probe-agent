import { describe, expect, it } from "vitest";
import { DISCUSSION_ADAPTERS, resolveDiscussionCandidate } from "@/lib/discussion-adapters";

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
