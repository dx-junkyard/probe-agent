/// <reference types="vitest/globals" />
// Issue #422 (Epic #418): Stakeholder Value Network screen tests.
//
// `docs/stakeholder-value-network.md` §0 invariant 9 is what every test here
// protects: the client re-derives nothing. Everything below exercises what
// the screen does with values `GET /stakeholder-value-network` already
// decided -- filtering/labelling/URL-state logic is unit-tested directly on
// `model.ts` at the bottom of this file.

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { ValueNetworkEdgeOut, ValueNetworkNodeOut, ValueNetworkNoticeOut, ValueNetworkOut } from "@/api/types";
import {
  EMPTY_FILTERS, EXCHANGE_KIND_DASH, applyFiltersToSearchParams, computeGraphLayout,
  edgesForNode, filterEdges, filterNodes, filtersFromSearchParams, noticeCountByCode,
  noticesForSubject, visibleNodeKeys,
} from "@/components/stakeholder-network/model";

const mockApi = { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };

class ApiError extends Error {
  status: number;
  detail: string;
  code?: string;
  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
  ApiError,
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <MemoryRouter initialEntries={["/stakeholder-value-network"]}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

async function renderPage() {
  const { default: Page } = await import("@/pages/stakeholder-value-network");
  return render(<Page />, { wrapper });
}

// --- fixtures ---------------------------------------------------------------

function node(overrides: Partial<ValueNetworkNodeOut> = {}): ValueNetworkNodeOut {
  return {
    stakeholder_key: "user", display_name: "利用者", stakeholder_kind: "end_user",
    roles: ["beneficiary"], design_status: "confirmed", recheck_state: "current",
    authored_by_kind: "developer", evidence_state: "available", ...overrides,
  };
}

function edge(overrides: Partial<ValueNetworkEdgeOut> = {}): ValueNetworkEdgeOut {
  return {
    exchange_key: "svc-1", provider_stakeholder_key: "provider", receiver_stakeholder_key: "user",
    exchange_kind: "service", value_statement: "サービスを提供する",
    consideration: { consideration_state: "unknown", consideration_kind: null, consideration_statement: "" },
    channel: "", trigger: "", cadence: "unknown",
    design_status: "confirmed", recheck_state: "current", validity_state: "unbounded",
    evidence_state: "missing", related_refs: [], ...overrides,
  };
}

function valueNetworkOut(overrides: Partial<ValueNetworkOut> = {}): ValueNetworkOut {
  return {
    system_id: 1, generated_at: 1000,
    nodes: [node(), node({ stakeholder_key: "provider", display_name: "提供者", stakeholder_kind: "provider_team", roles: ["supplier"] })],
    edges: [edge()],
    notices: [],
    degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

// --- rendering ---------------------------------------------------------------

describe("StakeholderValueNetworkPage rendering", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("renders nodes and edges from the server response", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut());
    await renderPage();

    await waitFor(() => expect(screen.getByTestId("value-network-node-list")).toBeInTheDocument());
    expect(screen.getByTestId("value-network-node-user")).toBeInTheDocument();
    expect(screen.getByTestId("value-network-node-provider")).toBeInTheDocument();
    expect(screen.getByTestId("value-network-edge-svc-1")).toBeInTheDocument();
  });

  it("shows the no-selection note until a node or edge is chosen, then shows detail", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut());
    await renderPage();

    await waitFor(() => expect(screen.getByTestId("value-network-no-selection")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("value-network-node-user"));
    await waitFor(() => expect(screen.getByTestId("value-network-node-detail")).toBeInTheDocument());
    expect(within(screen.getByTestId("value-network-node-detail")).getByRole("heading", { name: "利用者" })).toBeInTheDocument();
  });

  it("selecting an edge shows its consideration and related refs", async () => {
    mockApi.get.mockResolvedValue(
      valueNetworkOut({
        edges: [
          edge({
            related_refs: [
              {
                id: 1, source_kind: "value_exchange", source_key: "svc-1", ref_kind: "stakeholder_need",
                target_ref: "need-1", target_row_id: null, relation_status: "confirmed",
                target_resolution: "resolved", recheck_state: "current", captured_digest: "abc",
                note: "", decision_method: "manual", created_by: "dev", created_at: 1000, superseded_by_id: null,
              },
            ],
          }),
        ],
      }),
    );
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-edge-svc-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("value-network-edge-svc-1"));
    await waitFor(() => expect(screen.getByTestId("value-network-edge-detail")).toBeInTheDocument());
    expect(within(screen.getByTestId("value-network-edge-detail")).getByText("need-1")).toBeInTheDocument();
  });

  it("renders a notice in the detail pane without recomputing it", async () => {
    const notices: ValueNetworkNoticeOut[] = [
      { code: "payer_differs_from_beneficiary", subject_kind: "value_exchange", subject_key: "svc-1" },
    ];
    mockApi.get.mockResolvedValue(valueNetworkOut({ notices }));
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-edge-svc-1")).toBeInTheDocument());
    expect(screen.getByText("要確認 1件")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("value-network-edge-svc-1"));
    await waitFor(() => expect(screen.getByTestId("value-network-detail-notices")).toBeInTheDocument());
  });
});

describe("StakeholderValueNetworkPage filters", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("filtering by exchange_kind narrows the edge list and updates the URL", async () => {
    mockApi.get.mockResolvedValue(
      valueNetworkOut({
        edges: [edge(), edge({ exchange_key: "money-1", exchange_kind: "money", provider_stakeholder_key: "payer" })],
        nodes: [...valueNetworkOut().nodes, node({ stakeholder_key: "payer", display_name: "支払者", roles: ["payer"] })],
      }),
    );
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-edge-money-1")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Value Exchange の種類で絞り込み"), { target: { value: "money" } });

    await waitFor(() => expect(screen.queryByTestId("value-network-edge-svc-1")).not.toBeInTheDocument());
    expect(screen.getByTestId("value-network-edge-money-1")).toBeInTheDocument();
  });
});

describe("StakeholderValueNetworkPage empty/error/degraded states", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("renders a distinct sentence when the System has no Stakeholders", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut({ nodes: [], edges: [] }));
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-no-data")).toBeInTheDocument());
  });

  it("renders a distinct sentence when filters match nothing, never the no-data one", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut());
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-node-list")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("role で絞り込み"), { target: { value: "regulator" } });
    await waitFor(() => expect(screen.getByTestId("value-network-filtered-empty")).toBeInTheDocument());
    expect(screen.queryByTestId("value-network-no-data")).not.toBeInTheDocument();
  });

  it("renders a load-error card with retry on a failed fetch", async () => {
    mockApi.get.mockRejectedValue(new ApiError(500, "server error"));
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-load-error")).toBeInTheDocument());
  });

  it("renders a degraded-section note without hiding the sections that loaded", async () => {
    mockApi.get.mockResolvedValue(
      valueNetworkOut({ edges: [], degraded_sections: ["edges", "notices"], degraded_detail: { edges: "RuntimeError: boom" } }),
    );
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-degraded")).toBeInTheDocument());
    expect(screen.getByTestId("value-network-node-list")).toBeInTheDocument();
  });
});

describe("StakeholderValueNetworkPage responsive layout", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("uses a single-column grid that only expands to three columns from md up (list+detail degrade)", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut());
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-node-list")).toBeInTheDocument());
    const grid = screen.getByTestId("value-network-node-list").closest(".grid");
    expect(grid).not.toBeNull();
    expect(grid!.className).toContain("grid-cols-1");
    expect(grid!.className).toMatch(/md:grid-cols-/);
  });
});

// ---------------------------------------------------------------------------
// model.ts unit tests
// ---------------------------------------------------------------------------

describe("model.ts", () => {
  it("filtersFromSearchParams / applyFiltersToSearchParams round-trip", () => {
    const params = new URLSearchParams();
    applyFiltersToSearchParams(params, {
      exchangeKind: "money", role: "payer", designStatus: "confirmed", staleOnly: true,
    });
    const parsed = filtersFromSearchParams(params);
    expect(parsed).toEqual({ exchangeKind: "money", role: "payer", designStatus: "confirmed", staleOnly: true });
  });

  it("applyFiltersToSearchParams clears a filter's key rather than writing an empty string", () => {
    const params = new URLSearchParams("exchange_kind=money&unrelated=keep");
    applyFiltersToSearchParams(params, EMPTY_FILTERS);
    expect(params.get("exchange_kind")).toBeNull();
    expect(params.get("unrelated")).toBe("keep");
  });

  it("filterNodes / filterEdges only narrow, never mutate the server's own objects", () => {
    const n = node();
    const e = edge();
    expect(filterNodes([n], EMPTY_FILTERS)[0]).toBe(n);
    expect(filterEdges([e], EMPTY_FILTERS)[0]).toBe(e);
    expect(filterNodes([n], { ...EMPTY_FILTERS, role: "payer" })).toEqual([]);
  });

  it("visibleNodeKeys keeps an isolated Stakeholder visible when no edge filter is active", () => {
    const lonely = node({ stakeholder_key: "lonely", roles: [] });
    const keys = visibleNodeKeys([lonely], [], EMPTY_FILTERS);
    expect(keys.has("lonely")).toBe(true);
  });

  it("visibleNodeKeys drops a node untouched by any surviving edge once an exchange_kind filter is active", () => {
    const a = node({ stakeholder_key: "a" });
    const b = node({ stakeholder_key: "b" });
    const e = edge({ provider_stakeholder_key: "a", receiver_stakeholder_key: "a", exchange_kind: "money" });
    const filters = { ...EMPTY_FILTERS, exchangeKind: "money" as const };
    const keys = visibleNodeKeys([a, b], [e], filters);
    expect(keys.has("a")).toBe(true);
    expect(keys.has("b")).toBe(false);
  });

  it("noticesForSubject / noticeCountByCode only read the server's own notice list", () => {
    const notices: ValueNetworkNoticeOut[] = [
      { code: "stale_link", subject_kind: "stakeholder", subject_key: "user" },
      { code: "stale_link", subject_kind: "stakeholder", subject_key: "other" },
      { code: "confirmed_without_evidence", subject_kind: "value_exchange", subject_key: "svc-1" },
    ];
    expect(noticesForSubject(notices, "stakeholder", "user")).toHaveLength(1);
    const counts = noticeCountByCode(notices);
    expect(counts.get("stale_link")).toBe(2);
    expect(counts.get("confirmed_without_evidence")).toBe(1);
  });

  it("edgesForNode splits outgoing and incoming without altering either edge", () => {
    const e1 = edge({ exchange_key: "e1", provider_stakeholder_key: "user", receiver_stakeholder_key: "provider" });
    const e2 = edge({ exchange_key: "e2", provider_stakeholder_key: "provider", receiver_stakeholder_key: "user" });
    const { outgoing, incoming } = edgesForNode([e1, e2], "user");
    expect(outgoing.map((e) => e.exchange_key)).toEqual(["e1"]);
    expect(incoming.map((e) => e.exchange_key)).toEqual(["e2"]);
  });
});

// --- §7.3's directed graph ----------------------------------------------------
//
// The graph is the screen's primary presentation; the list + detail pane is
// its narrow-width degradation, not a substitute for it. These tests protect
// the two properties that make a drawn graph legitimate under invariant 10:
// the layout is deterministic, and no coordinate ever leaves the render.

describe("ValueNetworkGraph", () => {
  beforeEach(() => {
    mockApi.get.mockReset();
  });

  it("draws a node per Stakeholder and a directed edge per Value Exchange", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut());
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-graph")).toBeInTheDocument());

    expect(screen.getByTestId("value-network-graph-node-user")).toBeInTheDocument();
    expect(screen.getByTestId("value-network-graph-node-provider")).toBeInTheDocument();

    const drawnEdge = screen.getByTestId("value-network-graph-edge-svc-1");
    // Direction is carried by an arrow marker on the path, provider -> receiver.
    const path = drawnEdge.querySelector("path");
    expect(path?.getAttribute("marker-end")).toBe("url(#value-network-arrow)");
  });

  it("selecting a drawn node or edge opens the same detail pane as the list", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut());
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-graph")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("value-network-graph-edge-svc-1"));
    await waitFor(() =>
      expect(screen.getByTestId("value-network-edge-detail")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByTestId("value-network-graph-node-user"));
    await waitFor(() =>
      expect(screen.getByTestId("value-network-node-detail")).toBeInTheDocument(),
    );
  });

  it("is keyboard reachable: a drawn node is focusable and activates on Enter", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut());
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-graph")).toBeInTheDocument());

    const drawnNode = screen.getByTestId("value-network-graph-node-user");
    expect(drawnNode).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(drawnNode, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByTestId("value-network-node-detail")).toBeInTheDocument(),
    );
  });

  it("conveys exchange kind by text and dash pattern, never by colour alone", async () => {
    mockApi.get.mockResolvedValue(valueNetworkOut());
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-graph")).toBeInTheDocument());

    const drawnEdge = screen.getByTestId("value-network-graph-edge-svc-1");
    // 1. a text label on the edge itself
    expect(within(drawnEdge).getByText(/サービス/)).toBeInTheDocument();
    // 2. a dash pattern distinct from other kinds
    expect(drawnEdge.querySelector("path")?.getAttribute("stroke-dasharray")).toBe(
      EXCHANGE_KIND_DASH.service,
    );
  });

  it("marks a stale edge with text, not only a stroke change", async () => {
    mockApi.get.mockResolvedValue(
      valueNetworkOut({ edges: [edge({ recheck_state: "stale" })] }),
    );
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-graph")).toBeInTheDocument());

    const drawnEdge = screen.getByTestId("value-network-graph-edge-svc-1");
    expect(within(drawnEdge).getByText(/要再確認/)).toBeInTheDocument();
  });

  it("renders an Exchange with no current revision as 不明, never as one of the seven kinds", async () => {
    mockApi.get.mockResolvedValue(
      valueNetworkOut({ edges: [edge({ exchange_kind: null })] }),
    );
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("value-network-graph")).toBeInTheDocument());

    const drawnEdge = screen.getByTestId("value-network-graph-edge-svc-1");
    expect(within(drawnEdge).getByText(/不明/)).toBeInTheDocument();
  });
});

describe("computeGraphLayout", () => {
  it("is deterministic: the same facts always produce the same coordinates", () => {
    const nodes = [node(), node({ stakeholder_key: "payer", display_name: "購入責任者" })];
    const edges = [edge()];
    const first = computeGraphLayout(nodes, edges);
    const second = computeGraphLayout(nodes, edges);
    expect(second).toEqual(first);
  });

  it("places nodes by their index in the server's ordering, never by degree", () => {
    // `hub` is touched by two Exchanges and `lonely` by none. A layout that
    // ranked by connection count would move `hub`; invariant 7 forbids
    // rendering centrality as importance, so position must follow the
    // server's ordering alone.
    const nodes = [
      node({ stakeholder_key: "hub", display_name: "hub" }),
      node({ stakeholder_key: "lonely", display_name: "lonely" }),
    ];
    const withEdges = computeGraphLayout(nodes, [
      edge({ exchange_key: "a", provider_stakeholder_key: "hub", receiver_stakeholder_key: "lonely" }),
      edge({ exchange_key: "b", provider_stakeholder_key: "lonely", receiver_stakeholder_key: "hub" }),
    ]);
    const withoutEdges = computeGraphLayout(nodes, []);
    expect(withEdges.nodes).toEqual(withoutEdges.nodes);
  });

  it("separates parallel edges between the same pair so neither is hidden", () => {
    // The payer/beneficiary question §7.2 exists to surface is unreadable if
    // the service edge and the money edge paying for it overlap exactly.
    const nodes = [
      node({ stakeholder_key: "provider", display_name: "提供者" }),
      node({ stakeholder_key: "user", display_name: "利用者" }),
    ];
    const layout = computeGraphLayout(nodes, [
      edge({ exchange_key: "svc", exchange_kind: "service" }),
      edge({
        exchange_key: "pay",
        exchange_kind: "money",
        provider_stakeholder_key: "user",
        receiver_stakeholder_key: "provider",
      }),
    ]);
    const [first, second] = layout.edges;
    expect(first.curvature).not.toBe(second.curvature);
  });

  it("skips an edge whose endpoint is filtered out rather than drawing it to nowhere", () => {
    const layout = computeGraphLayout([node({ stakeholder_key: "user" })], [edge()]);
    expect(layout.edges).toHaveLength(0);
  });

  it("produces no field that could be persisted as layout state", () => {
    // Invariant 10: coordinates exist for this render only. Nothing in the
    // layout is ever sent to the server -- this asserts the shape stays a
    // pure render product with no id/version/saved marker to round-trip.
    const layout = computeGraphLayout([node()], []);
    expect(Object.keys(layout).sort()).toEqual(["edges", "height", "nodes", "width"]);
    expect(Object.keys(layout.nodes[0]).sort()).toEqual(["stakeholder_key", "x", "y"]);
  });
});
