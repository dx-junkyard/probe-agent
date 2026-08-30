import { describe, expect, it } from "vitest";
import type { FunctionalLineageEdgeOut, FunctionalLineageGapOut, FunctionalLineageNodeOut } from "@/api/types";
import {
  EMPTY_LINEAGE_FILTERS,
  applyLineageFiltersToSearchParams,
  displayState,
  filterGaps,
  filterNodes,
  gapCountBySeverity,
  gapsForSubject,
  lineageDeepLink,
  lineageFiltersFromSearchParams,
  nodeByRef,
  readSharedSelection,
  traceDownstreamImpact,
  writeSharedSelection,
} from "@/components/functional-lineage/model";

const NODES: FunctionalLineageNodeOut[] = [
  { kind: "stakeholder", ref: "sh1", name: "利用者" },
  { kind: "stakeholder_need", ref: "need1", name: null },
  { kind: "value_exchange", ref: "ex1", name: null },
  { kind: "ux_journey", ref: "j1", name: null },
];

const EDGES: FunctionalLineageEdgeOut[] = [
  { from_kind: "stakeholder", from_ref: "sh1", to_kind: "stakeholder_need", to_ref: "need1" },
  { from_kind: "stakeholder_need", from_ref: "need1", to_kind: "value_exchange", to_ref: "ex1" },
  { from_kind: "value_exchange", from_ref: "ex1", to_kind: "ux_journey", to_ref: "j1" },
];

const GAPS: FunctionalLineageGapOut[] = [
  { code: "need_without_journey", severity: "attention", subject_kind: "stakeholder_need", subject_ref: "need1" },
  { code: "adopted_design_without_implementation_target", severity: "blocking", subject_kind: "solution_design", subject_ref: "d1" },
  { code: "unavailable_reference", severity: "informational", subject_kind: "value_exchange", subject_ref: "ex1" },
];

describe("functional-lineage/model", () => {
  it("filters nodes by kind only", () => {
    expect(filterNodes(NODES, EMPTY_LINEAGE_FILTERS)).toHaveLength(4);
    expect(filterNodes(NODES, { kind: "stakeholder", gapSeverity: null })).toEqual([NODES[0]]);
  });

  it("filters gaps by kind and severity independently", () => {
    expect(filterGaps(GAPS, EMPTY_LINEAGE_FILTERS)).toHaveLength(3);
    expect(filterGaps(GAPS, { kind: null, gapSeverity: "blocking" })).toEqual([GAPS[1]]);
    expect(filterGaps(GAPS, { kind: "value_exchange", gapSeverity: null })).toEqual([GAPS[2]]);
  });

  it("counts gaps by severity as a COUNT, never a score", () => {
    const counts = gapCountBySeverity(GAPS);
    expect(counts).toEqual({ blocking: 1, attention: 1, informational: 1 });
  });

  it("finds gaps for one exact subject", () => {
    expect(gapsForSubject(GAPS, "stakeholder_need", "need1")).toEqual([GAPS[0]]);
    expect(gapsForSubject(GAPS, "stakeholder_need", "need-does-not-exist")).toEqual([]);
  });

  it("resolves a node by exact (kind, ref)", () => {
    expect(nodeByRef(NODES, "stakeholder", "sh1")).toEqual(NODES[0]);
    expect(nodeByRef(NODES, "stakeholder", "does-not-exist")).toBeNull();
    expect(nodeByRef(NODES, null, null)).toBeNull();
  });

  it("distinguishes no_data / filtered_empty / ready", () => {
    expect(displayState(undefined, [])).toBe("no_data");
    expect(displayState({ system_id: 1, generated_at: 0, nodes: [], edges: [], gaps: [], degraded_sections: [], degraded_detail: {} }, [])).toBe("no_data");
    expect(displayState({ system_id: 1, generated_at: 0, nodes: NODES, edges: [], gaps: [], degraded_sections: [], degraded_detail: {} }, [])).toBe("filtered_empty");
    expect(displayState({ system_id: 1, generated_at: 0, nodes: NODES, edges: [], gaps: [], degraded_sections: [], degraded_detail: {} }, NODES)).toBe("ready");
  });

  describe("traceDownstreamImpact", () => {
    it("walks forward only, never upstream", () => {
      const fromRoot = traceDownstreamImpact(EDGES, "stakeholder", "sh1");
      expect(fromRoot).toEqual([
        { kind: "stakeholder_need", ref: "need1" },
        { kind: "value_exchange", ref: "ex1" },
        { kind: "ux_journey", ref: "j1" },
      ]);

      // Starting from the LEAF (the Journey, the most downstream node in
      // this chain) must find nothing -- an impact walk that reached
      // upstream from here would be exactly the reverse traversal §9.3
      // forbids.
      const fromLeaf = traceDownstreamImpact(EDGES, "ux_journey", "j1");
      expect(fromLeaf).toEqual([]);
    });

    it("never revisits a node (cycle-safe)", () => {
      const cyclic: FunctionalLineageEdgeOut[] = [
        { from_kind: "stakeholder", from_ref: "a", to_kind: "stakeholder_need", to_ref: "b" },
        { from_kind: "stakeholder_need", from_ref: "b", to_kind: "stakeholder", to_ref: "a" },
      ];
      const result = traceDownstreamImpact(cyclic, "stakeholder", "a");
      // "a" is reachable again via the cycle (b -> a), but each (kind, ref)
      // is only ever added to the frontier once, so the walk terminates.
      expect(result).toEqual([
        { kind: "stakeholder_need", ref: "b" },
        { kind: "stakeholder", ref: "a" },
      ]);
    });
  });

  it("resolves a deep link per subject kind that navigates, never executes", () => {
    expect(lineageDeepLink("stakeholder", "sh1")).toBe("/stakeholder-value-network?node=sh1");
    expect(lineageDeepLink("value_exchange", "ex1")).toBe("/stakeholder-value-network?edge=ex1");
    expect(lineageDeepLink("ux_journey_step", "j1#s1")).toBe("/journey-blueprint?journey=j1");
    expect(lineageDeepLink("evolution_node", "n1")).toBe("/evolution-nodes?node=n1");
    expect(lineageDeepLink("static_flow", "f1")).toBe("/flow-explorer?flow=f1");
  });

  it("round-trips filters and the shared selection through URLSearchParams", () => {
    const params = new URLSearchParams();
    applyLineageFiltersToSearchParams(params, { kind: "value_exchange", gapSeverity: "blocking" });
    params.set("unrelated", "keep-me");
    expect(lineageFiltersFromSearchParams(params)).toEqual({ kind: "value_exchange", gapSeverity: "blocking" });
    expect(params.get("unrelated")).toBe("keep-me");

    writeSharedSelection(params, { kind: "stakeholder", ref: "sh1" });
    expect(readSharedSelection(params)).toEqual({ kind: "stakeholder", ref: "sh1" });
    expect(params.get("unrelated")).toBe("keep-me");

    writeSharedSelection(params, { kind: null, ref: null });
    expect(readSharedSelection(params)).toEqual({ kind: null, ref: null });
  });
});

describe("lineageDeepLink for the Product Objective layer (#427 §7.3/§9.4)", () => {
  it("routes Objective, Milestone and Gap to the single Objective Map route", () => {
    // §9.4 keeps the Objective layer to ONE sidebar item: the Gap Workbench
    // is that route's second lane, not a screen of its own.
    expect(lineageDeepLink("product_objective", "checkout-speed")).toBe(
      "/objective-map?objective=checkout-speed",
    );
    expect(lineageDeepLink("product_milestone", "first-pass")).toBe(
      "/objective-map?milestone=first-pass",
    );
    expect(lineageDeepLink("product_gap", "retry-loop")).toBe(
      "/objective-map?view=gaps&gap=retry-loop",
    );
  });

  it("returns no link for kinds whose ref does not identify a screen's subject", () => {
    // A Feature has no screen yet, and an Experiment / Replay run reached
    // through a Feature target link is named by that link's target_ref,
    // which is not what those screens select on. "No link" is the honest
    // answer -- the same one §5.8 requires of a Gap source with no owning
    // screen. A plausible-looking URL would be worse than none.
    expect(lineageDeepLink("product_feature", "checkout")).toBeNull();
    expect(lineageDeepLink("experiment", "12")).toBeNull();
    expect(lineageDeepLink("replay_run", "34")).toBeNull();
  });

  it("keeps the new kinds through a shared-selection URL round trip", () => {
    // A kind with a label but no entry in KIND_VALUES renders correctly and
    // then silently loses its selection on reload, because isLineageKind is
    // what guards the parse back.
    const params = new URLSearchParams();
    writeSharedSelection(params, { kind: "product_gap", ref: "retry-loop" });
    expect(readSharedSelection(new URLSearchParams(params.toString()))).toEqual({
      kind: "product_gap",
      ref: "retry-loop",
    });
  });
});
