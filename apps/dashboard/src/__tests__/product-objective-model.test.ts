import { describe, expect, it } from "vitest";
import type {
  GapWorkbenchEntryOut, GapWorkbenchOut, ObjectiveMapMilestoneOut, ObjectiveMapNodeOut,
  ObjectiveMapOut, OverviewObjectiveOut,
} from "@/api/types";
import {
  EMPTY_GAP_WORKBENCH_FILTERS,
  applyObjectiveMapSelectionToSearchParams,
  filterGapWorkbenchEntries,
  gapWorkbenchEntryByKey,
  objectiveGapTotal,
  objectiveMapChildren,
  objectiveMapEmptyState,
  objectiveMapMilestoneByKey,
  objectiveMapNodeByKey,
  objectiveMapRoots,
  objectiveMapSelectionFromSearchParams,
  objectiveNextStepHasAction,
  objectiveNextStepHref,
  sharedGapKeysForSource,
} from "@/components/product-objective/model";

function gapSummary(overrides: Partial<ObjectiveMapMilestoneOut["gap_summary"]> = {}) {
  return {
    open_count: 0, acknowledged_count: 0, deferred_count: 0,
    resolved_count: 0, rejected_count: 0, obsolete_count: 0,
    recheck_required_count: 0, reopen_candidate_count: 0, close_candidate_count: 0,
    ...overrides,
  };
}

function milestone(overrides: Partial<ObjectiveMapMilestoneOut> = {}): ObjectiveMapMilestoneOut {
  return {
    id: 1, milestone_key: "m1", title: "Milestone 1",
    design_status: "confirmed", achievement: "unassessed", assessability: "assessable",
    recheck_state: "current", sequence_hint: 0, gap_summary: gapSummary(),
    ...overrides,
  };
}

function node(overrides: Partial<ObjectiveMapNodeOut> = {}): ObjectiveMapNodeOut {
  return {
    id: 1, objective_key: "o1", title: "Objective 1", objective_state: "active",
    recheck_state: "current", parent_objective_id: null, parent_objective_key: null,
    child_objective_ids: [], milestones: [],
    ...overrides,
  };
}

const MAP: ObjectiveMapOut = {
  system_id: 1,
  generated_at: 0,
  nodes: [
    node({ id: 1, objective_key: "o1", child_objective_ids: [2], milestones: [milestone({ id: 10, milestone_key: "m1", gap_summary: gapSummary({ open_count: 2, resolved_count: 1 }) })] }),
    node({ id: 2, objective_key: "o2", title: "Objective 2", parent_objective_id: 1, parent_objective_key: "o1" }),
  ],
  root_objective_ids: [1],
  degraded_sections: [],
  degraded_detail: {},
};

function gapEntry(overrides: Partial<GapWorkbenchEntryOut> = {}): GapWorkbenchEntryOut {
  return {
    id: 1, gap_key: "g1", milestone_id: 10, milestone_key: "m1",
    objective_id: 1, objective_key: "o1", title: "Gap 1",
    lifecycle: "open", priority_band: "unset", recheck_state: "current",
    read_flags: [], deep_links: [],
    ...overrides,
  };
}

const WORKBENCH: GapWorkbenchOut = {
  system_id: 1,
  generated_at: 0,
  entries: [
    gapEntry({ id: 1, gap_key: "g1", priority_band: "now" }),
    gapEntry({ id: 2, gap_key: "g2", milestone_key: "m2", objective_key: "o2", lifecycle: "resolved" }),
  ],
  source_kind_breakdown: [{ source_kind: "manual", gap_count: 2 }],
  shared_sources: [
    { source_kind: "manual", source_ref: "shared-ref", gap_ids: [1, 2], gap_keys: ["g1", "g2"] },
  ],
  degraded_sections: [],
  degraded_detail: {},
};

describe("product-objective/model: URL selection", () => {
  it("defaults to the objectives lane with no selection", () => {
    const params = new URLSearchParams();
    expect(objectiveMapSelectionFromSearchParams(params)).toEqual({
      view: "objectives", objectiveKey: null, milestoneKey: null, gapKey: null,
    });
  });

  it("reads view=gaps and gap=<key> (the literal §9.4 deep link)", () => {
    const params = new URLSearchParams("view=gaps&gap=g1");
    expect(objectiveMapSelectionFromSearchParams(params)).toEqual({
      view: "gaps", objectiveKey: null, milestoneKey: null, gapKey: "g1",
    });
  });

  it("round-trips through apply + read", () => {
    const params = new URLSearchParams();
    applyObjectiveMapSelectionToSearchParams(params, {
      view: "gaps", objectiveKey: null, milestoneKey: "m1", gapKey: "g1",
    });
    expect(params.get("view")).toBe("gaps");
    expect(params.get("milestone")).toBe("m1");
    expect(params.get("gap")).toBe("g1");
    expect(objectiveMapSelectionFromSearchParams(params)).toEqual({
      view: "gaps", objectiveKey: null, milestoneKey: "m1", gapKey: "g1",
    });
  });

  it("clears view when returning to the default objectives lane", () => {
    const params = new URLSearchParams("view=gaps&gap=g1");
    applyObjectiveMapSelectionToSearchParams(params, {
      view: "objectives", objectiveKey: null, milestoneKey: null, gapKey: null,
    });
    expect(params.has("view")).toBe(false);
    expect(params.has("gap")).toBe(false);
  });

  it("honours an incoming shared ref_kind=product_gap/ref pair", () => {
    const params = new URLSearchParams("ref_kind=product_gap&ref=g1");
    expect(objectiveMapSelectionFromSearchParams(params)).toEqual({
      view: "gaps", objectiveKey: null, milestoneKey: null, gapKey: "g1",
    });
  });

  it("honours an incoming shared ref_kind=product_objective/ref pair", () => {
    const params = new URLSearchParams("ref_kind=product_objective&ref=o1");
    expect(objectiveMapSelectionFromSearchParams(params)).toEqual({
      view: "objectives", objectiveKey: "o1", milestoneKey: null, gapKey: null,
    });
  });

  it("prefers this screen's own params over a shared ref pair", () => {
    const params = new URLSearchParams("view=objectives&objective=o9&ref_kind=product_gap&ref=g1");
    expect(objectiveMapSelectionFromSearchParams(params).objectiveKey).toBe("o9");
  });
});

describe("product-objective/model: Objective Map structural lookups", () => {
  it("resolves roots in the server's own root_objective_ids order", () => {
    expect(objectiveMapRoots(MAP).map((n) => n.objective_key)).toEqual(["o1"]);
  });

  it("resolves children of a node", () => {
    const root = objectiveMapNodeByKey(MAP, "o1")!;
    expect(objectiveMapChildren(MAP, root).map((n) => n.objective_key)).toEqual(["o2"]);
  });

  it("returns null for an unknown objective key", () => {
    expect(objectiveMapNodeByKey(MAP, "does-not-exist")).toBeNull();
    expect(objectiveMapNodeByKey(MAP, null)).toBeNull();
  });

  it("finds a Milestone by key across every node", () => {
    const found = objectiveMapMilestoneByKey(MAP, "m1");
    expect(found?.node.objective_key).toBe("o1");
    expect(found?.milestone.milestone_key).toBe("m1");
  });

  it("returns null when the Milestone key is absent", () => {
    expect(objectiveMapMilestoneByKey(MAP, "no-such-milestone")).toBeNull();
  });

  it("sums Gap counts across a node's Milestones as a plain count, not a score", () => {
    expect(objectiveGapTotal(MAP.nodes[0])).toBe(3);
    expect(objectiveGapTotal(MAP.nodes[1])).toBe(0);
  });
});

describe("product-objective/model: two distinct empty states (§9.5)", () => {
  it("reports no_objective for a brand-new System", () => {
    const empty: ObjectiveMapOut = { ...MAP, nodes: [], root_objective_ids: [] };
    expect(objectiveMapEmptyState(empty, WORKBENCH)).toBe("no_objective");
  });

  it("reports no_gap for a System with Objectives but zero triaged Gaps", () => {
    const emptyWorkbench: GapWorkbenchOut = { ...WORKBENCH, entries: [] };
    expect(objectiveMapEmptyState(MAP, emptyWorkbench)).toBe("no_gap");
  });

  it("reports ready when both exist", () => {
    expect(objectiveMapEmptyState(MAP, WORKBENCH)).toBe("ready");
  });
});

describe("product-objective/model: Gap Workbench filtering never re-sorts", () => {
  it("filters by objective/milestone/lifecycle without changing order", () => {
    const filtered = filterGapWorkbenchEntries(WORKBENCH.entries, EMPTY_GAP_WORKBENCH_FILTERS);
    expect(filtered.map((e) => e.gap_key)).toEqual(["g1", "g2"]);
  });

  it("filters down to one objective", () => {
    const filtered = filterGapWorkbenchEntries(WORKBENCH.entries, { ...EMPTY_GAP_WORKBENCH_FILTERS, objectiveKey: "o2" });
    expect(filtered.map((e) => e.gap_key)).toEqual(["g2"]);
  });

  it("filters by lifecycle", () => {
    const filtered = filterGapWorkbenchEntries(WORKBENCH.entries, { ...EMPTY_GAP_WORKBENCH_FILTERS, lifecycle: "resolved" });
    expect(filtered.map((e) => e.gap_key)).toEqual(["g2"]);
  });

  it("looks up a single entry by gap_key", () => {
    expect(gapWorkbenchEntryByKey(WORKBENCH, "g2")?.title).toBe("Gap 1");
    expect(gapWorkbenchEntryByKey(WORKBENCH, "missing")).toBeNull();
    expect(gapWorkbenchEntryByKey(WORKBENCH, null)).toBeNull();
  });

  it("returns the OTHER Gaps sharing one detection source (§5.2 federation read)", () => {
    expect(sharedGapKeysForSource(WORKBENCH, "manual", "shared-ref", "g1")).toEqual(["g2"]);
    expect(sharedGapKeysForSource(WORKBENCH, "manual", "shared-ref", "g2")).toEqual(["g1"]);
    expect(sharedGapKeysForSource(WORKBENCH, "manual", "no-such-ref", "g1")).toEqual([]);
  });
});

function overviewObjective(overrides: Partial<OverviewObjectiveOut> = {}): OverviewObjectiveOut {
  return {
    vision: null,
    active_objective: null,
    active_objective_count: 0,
    next_milestone: null,
    primary_gap: null,
    objective_state: null,
    next_step: "create_objective",
    next_step_state: "available",
    next_step_reason: "",
    next_step_completion: "",
    next_step_value: "",
    degraded_sections: [],
    degraded_detail: {},
    ...overrides,
  };
}

describe("product-objective/model: Overview next_step -> Objective Map CTA", () => {
  it("waiting/unavailable/complete carry no action (§9.3)", () => {
    expect(objectiveNextStepHasAction("waiting")).toBe(false);
    expect(objectiveNextStepHasAction("unavailable")).toBe(false);
    expect(objectiveNextStepHasAction("complete")).toBe(false);
    expect(objectiveNextStepHasAction("available")).toBe(true);
  });

  it("create_objective navigates to the bare Objective Map", () => {
    expect(objectiveNextStepHref(overviewObjective({ next_step: "create_objective" }))).toBe("/objective-map");
  });

  it("prioritize_gap navigates to the gaps lane with the primary Gap selected", () => {
    const href = objectiveNextStepHref(overviewObjective({
      next_step: "prioritize_gap",
      primary_gap: { id: 1, system_id: 1, gap_key: "g9", milestone_id: 1, milestone_key: "m1", objective_id: 1, objective_key: "o1", current_revision_id: null, current_revision_number: null, decision_digest: "", title: "", lifecycle: "open", priority_band: "unset", recheck_state: "current", read_flags: [], created_by: null, created_at: 0, updated_at: 0 },
    }));
    expect(href).toBe("/objective-map?view=gaps&gap=g9");
  });

  it("assess_milestone navigates to the objectives lane with the Milestone selected", () => {
    const href = objectiveNextStepHref(overviewObjective({
      next_step: "assess_milestone",
      active_objective: { id: 1, system_id: 1, objective_key: "o1", current_revision_id: null, current_revision_number: null, title: "", objective_state: "active", recheck_state: "current", parent_objective_id: null, parent_objective_key: null, created_by: null, created_at: 0, updated_at: 0 },
      next_milestone: { id: 1, system_id: 1, milestone_key: "m1", objective_id: 1, objective_key: "o1", current_revision_id: null, current_revision_number: null, title: "", design_status: "confirmed", achievement: "unassessed", assessability: "assessable", recheck_state: "current", created_by: null, created_at: 0, updated_at: 0 },
    }));
    expect(href).toBe("/objective-map?view=objectives&objective=o1&milestone=m1");
  });

  it("confirm_vision navigates to the bare Objective Map (no Objective row exists yet to select)", () => {
    expect(objectiveNextStepHref(overviewObjective({ next_step: "confirm_vision" }))).toBe("/objective-map");
  });
});
