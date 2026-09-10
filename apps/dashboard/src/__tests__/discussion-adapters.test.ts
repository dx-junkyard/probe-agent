import { describe, expect, it } from "vitest";
import {
  classifyDiscussionError, DISCUSSION_ADAPTERS, proposalToDraft, resolveDiscussionCandidate,
} from "@/lib/discussion-adapters";
import type { AssistantDiscussionProposal, AssistantDiscussionProposalItem } from "@/api/types";

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

// Issue #456 registered no handler yet; Issue #452 wires the FIRST one
// (`ux_requirement`, the Issue's representative target). Every OTHER kind
// stays `null` -- pinned here alongside the Python side's own
// `test_only_ux_requirement_derives_prefill_form_so_far` so a future PR that
// adds ONE side's declaration without the other, or a different id, is
// caught on this side too.
describe("discussion adapter prefill handler registration (Issue #452)", () => {
  it("has prefillHandlerId registered only for ux_requirement", () => {
    for (const [kind, adapter] of Object.entries(DISCUSSION_ADAPTERS)) {
      if (kind === "ux_requirement") {
        expect(adapter.prefillHandlerId).toBe("ux_requirement.revision@v1");
      } else {
        expect(adapter.prefillHandlerId).toBeNull();
      }
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

// Issue #454 (Epic #443 §5.1): `proposalToDraft` must route a child item
// into `childOps`, never into the top-level `fields` array -- an Acceptance
// Criterion's own `field_name` ("statement") can collide by NAME with the
// Requirement's own top-level field of the same name.
describe("proposalToDraft child items (Issue #454)", () => {
  function baseProposal(items: AssistantDiscussionProposalItem[]): AssistantDiscussionProposal {
    return {
      id: 1, system_id: 1, thread_id: 1, screen_id: "ux-design-studio",
      target_kind: "ux_requirement", target_ref: "req-1",
      captured_target_revision_id: 1, captured_target_digest: "d",
      summary: "", confirmed_points: [], unresolved_questions: [], assumptions: [],
      evidence_refs: [], decision_method: "reasoning_llm", intelligence_run_id: null,
      provider: "openai", model: "gpt-5", prompt_version: "v1", schema_version: "v1",
      created_by: null, created_at: 0, items,
    };
  }

  function childItem(overrides: Partial<AssistantDiscussionProposalItem>): AssistantDiscussionProposalItem {
    return {
      id: 1, proposal_id: 1, item_kind: "field", field_name: "", relation_kind: "",
      relation_target_kind: "", relation_target_ref: "", subject_ref: "",
      current_value: "", proposed_value: "", rationale: "discussed",
      child_kind: "acceptance_criterion", child_key: "c-1", child_intent: "update",
      child_order: null, status: "proposed", eligibility: "appliable", applied_ref: null,
      decided_by: null, decided_at: null, decision_method: "reasoning_llm", created_at: 0,
      schema_version: "v1", prefill_count: 0, last_prefilled_at: null,
      ...overrides,
    };
  }

  it("never places a child item's field into the top-level fields array", () => {
    // "statement" is a top-level ux_requirement field AND a valid
    // acceptance_criterion child field -- the exact name collision #454
    // exists to keep apart.
    const item = childItem({ id: 1, field_name: "statement", proposed_value: "Criterion text" });
    const proposal = baseProposal([item]);
    const result = proposalToDraft(DISCUSSION_ADAPTERS.ux_requirement, proposal, [1]);
    expect(result.patch).not.toBeNull();
    expect(result.patch!.fields).toEqual([]);
    expect(result.unregisteredFieldNames).toEqual([]);
    expect(result.patch!.childOps).toHaveLength(1);
    expect(result.patch!.childOps[0]).toMatchObject({
      childKind: "acceptance_criterion", childKey: "c-1", intent: "update",
      fields: [{ fieldName: "statement", value: "Criterion text" }],
    });
  });

  it("groups two field rows of the SAME child address into one childOp", () => {
    const items = [
      childItem({ id: 1, field_name: "statement", proposed_value: "New text" }),
      childItem({ id: 2, field_name: "verification_method", proposed_value: "replay" }),
    ];
    const proposal = baseProposal(items);
    const result = proposalToDraft(DISCUSSION_ADAPTERS.ux_requirement, proposal, [1, 2]);
    expect(result.patch!.childOps).toHaveLength(1);
    expect(result.patch!.childOps[0].fields).toEqual([
      { fieldName: "statement", value: "New text" },
      { fieldName: "verification_method", value: "replay" },
    ]);
  });

  it("keeps a pure reorder item and a content item on SEPARATE childOps entries when their intent differs, and merges when identical", () => {
    // Two rows sharing the SAME (childKind, childKey, intent) -- one a pure
    // reorder, one a content change -- merge into one childOp carrying
    // both the order and the field (mirrors how the server persists them
    // as two item rows describing one logical child change).
    const items = [
      childItem({ id: 1, field_name: "statement", proposed_value: "text", child_order: null }),
      childItem({ id: 2, field_name: "", proposed_value: "", child_order: 3 }),
    ];
    const proposal = baseProposal(items);
    const result = proposalToDraft(DISCUSSION_ADAPTERS.ux_requirement, proposal, [1, 2]);
    expect(result.patch!.childOps).toHaveLength(1);
    expect(result.patch!.childOps[0].order).toBe(3);
    expect(result.patch!.childOps[0].fields).toEqual([{ fieldName: "statement", value: "text" }]);
  });

  it("does not merge child items with different child_key or child_intent", () => {
    const items = [
      childItem({ id: 1, child_key: "c-1", child_intent: "update", field_name: "statement" }),
      childItem({ id: 2, child_key: "c-2", child_intent: "update", field_name: "statement" }),
      childItem({ id: 3, child_key: "", child_intent: "add", field_name: "statement" }),
    ];
    const proposal = baseProposal(items);
    const result = proposalToDraft(DISCUSSION_ADAPTERS.ux_requirement, proposal, [1, 2, 3]);
    expect(result.patch!.childOps).toHaveLength(3);
  });

  it("still routes a plain (non-child) field item into the top-level fields array", () => {
    const item = childItem({
      id: 1, child_kind: "", child_key: "", child_intent: "", field_name: "statement",
      proposed_value: "Top-level statement",
    });
    const proposal = baseProposal([item]);
    const result = proposalToDraft(DISCUSSION_ADAPTERS.ux_requirement, proposal, [1]);
    expect(result.patch!.childOps).toEqual([]);
    expect(result.patch!.fields).toEqual([
      { fieldName: "statement", value: "Top-level statement", rationale: "discussed" },
    ]);
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
