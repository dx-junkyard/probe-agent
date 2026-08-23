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
