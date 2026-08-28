/// <reference types="vitest/globals" />
// Issue #432 (Epic #427): Objective Map / Gap Workbench screen tests.
//
// `docs/product-objective-lineage.md` §0 invariant 10 is what every test
// here protects: the client re-derives nothing. `GET /objective-map` and
// `GET /gap-workbench` are two independent endpoints (`useObjectiveMap` /
// `useGapWorkbench`), both rendered by the ONE `/objective-map` route with
// the Gap Workbench as its second lane (§9.4) -- never a separate page.

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type {
  GapWorkbenchEntryOut, GapWorkbenchOut, ObjectiveMapMilestoneOut, ObjectiveMapNodeOut,
  ObjectiveMapOut, ProductGapDetailOut,
} from "@/api/types";

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
    <MemoryRouter initialEntries={["/objective-map"]}>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

async function renderPage() {
  const { default: Page } = await import("@/pages/objective-map");
  return render(<Page />, { wrapper });
}

// --- fixtures ---------------------------------------------------------------

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
    id: 1, milestone_key: "m1", title: "初回決済を完了させる",
    design_status: "confirmed", achievement: "unassessed", assessability: "assessable",
    recheck_state: "current", sequence_hint: 0, gap_summary: gapSummary({ open_count: 1 }),
    ...overrides,
  };
}

function node(overrides: Partial<ObjectiveMapNodeOut> = {}): ObjectiveMapNodeOut {
  return {
    id: 1, objective_key: "o1", title: "決済の離脱率を下げる", objective_state: "active",
    recheck_state: "current", parent_objective_id: null, parent_objective_key: null,
    child_objective_ids: [], milestones: [milestone()],
    ...overrides,
  };
}

function objectiveMap(overrides: Partial<ObjectiveMapOut> = {}): ObjectiveMapOut {
  return {
    system_id: 1, generated_at: 1000,
    nodes: [node()],
    root_objective_ids: [1],
    degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function gapEntry(overrides: Partial<GapWorkbenchEntryOut> = {}): GapWorkbenchEntryOut {
  return {
    id: 1, gap_key: "g1", milestone_id: 1, milestone_key: "m1",
    objective_id: 1, objective_key: "o1", title: "初回決済フォームでの離脱",
    lifecycle: "open", priority_band: "unset", recheck_state: "current",
    read_flags: [], deep_links: [],
    ...overrides,
  };
}

function gapWorkbench(overrides: Partial<GapWorkbenchOut> = {}): GapWorkbenchOut {
  return {
    system_id: 1, generated_at: 1000,
    entries: [gapEntry()],
    source_kind_breakdown: [{ source_kind: "manual", gap_count: 1 }],
    shared_sources: [],
    degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function gapDetail(overrides: Partial<ProductGapDetailOut> = {}): ProductGapDetailOut {
  return {
    id: 1, system_id: 1, gap_key: "g1", milestone_id: 1, milestone_key: "m1",
    objective_id: 1, objective_key: "o1", title: "初回決済フォームでの離脱",
    lifecycle: "open", priority_band: "unset", recheck_state: "current",
    read_flags: [], created_by: null, created_at: 1000, updated_at: 1000,
    current_revision_id: 1, current_revision_number: 1,
    current_revision: {
      id: 1, gap_id: 1, revision_number: 1, title: "初回決済フォームでの離脱",
      current_state: "40% が離脱している", target_state: "10% 未満",
      target_state_mode: "own", interpretation: "入力項目が多すぎる",
      suggested_priority_note: "", content_digest: "abc",
      authored_by_kind: "developer", decision_method: "manual", intelligence_run_id: null,
      change_note: "", created_by: "dev", created_at: 1000, revision_state: "current",
      superseded_by_id: null,
    },
    source_refs: [], evidence_refs: [], artifact_links: [], decisions: [],
    degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function mockRoutes(routes: { objectiveMap?: ObjectiveMapOut; gapWorkbench?: GapWorkbenchOut; gap?: ProductGapDetailOut }) {
  mockApi.get.mockImplementation((path?: string) => {
    if (path === "/objective-map") {
      return routes.objectiveMap ? Promise.resolve(routes.objectiveMap) : Promise.reject(new ApiError(500, "failed"));
    }
    if (path === "/gap-workbench") {
      return routes.gapWorkbench ? Promise.resolve(routes.gapWorkbench) : Promise.reject(new ApiError(500, "failed"));
    }
    if (typeof path === "string" && path.startsWith("/product-gaps/")) {
      return routes.gap ? Promise.resolve(routes.gap) : Promise.reject(new ApiError(404, "not found"));
    }
    // Defensive fallback -- never an unhandled rejection.
    return Promise.resolve(null);
  });
}

// --- rendering ---------------------------------------------------------------

describe("ObjectiveMapPage rendering", () => {
  beforeEach(() => mockApi.get.mockReset());

  it("renders the Objective tree by default", async () => {
    mockRoutes({ objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench() });
    await renderPage();

    await waitFor(() => expect(screen.getByTestId("objective-tree")).toBeInTheDocument());
    expect(screen.getByTestId("objective-node-o1")).toBeInTheDocument();
    expect(screen.getByTestId("objective-map-tab-objectives")).toBeInTheDocument();
  });

  it("selecting an Objective shows its detail card", async () => {
    mockRoutes({ objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench() });
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("objective-node-o1")).toBeInTheDocument());

    fireEvent.click(within(screen.getByTestId("objective-node-o1")).getByText("決済の離脱率を下げる"));
    await waitFor(() => expect(screen.getByTestId("objective-detail-o1")).toBeInTheDocument());
    // Milestone/design_status and achievement are two separate labels (§1.3).
    fireEvent.click(screen.getByTestId("objective-node-toggle-o1"));
    await waitFor(() => expect(screen.getByTestId("milestone-node-m1")).toBeInTheDocument());
    const milestoneRow = screen.getByTestId("milestone-node-m1");
    expect(within(milestoneRow).getByText("確定済み")).toBeInTheDocument();
    expect(within(milestoneRow).getByText("未評価")).toBeInTheDocument();
  });

  it("switches to the Gap Workbench lane and updates the URL, without a separate page", async () => {
    mockRoutes({ objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench() });
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("objective-tree")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("objective-map-tab-gaps"));
    await waitFor(() => expect(screen.getByTestId("gap-entry-list")).toBeInTheDocument());
    expect(screen.getByTestId("gap-entry-g1")).toBeInTheDocument();
  });

  it("deep-links directly to a single Gap via ?view=gaps&gap=<key>", async () => {
    mockRoutes({ objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(), gap: gapDetail() });
    const { default: Page } = await import("@/pages/objective-map");
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <MemoryRouter initialEntries={["/objective-map?view=gaps&gap=g1"]}>
        <QueryClientProvider client={qc}>
          <Page />
        </QueryClientProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByTestId("gap-detail-g1")).toBeInTheDocument());
    expect(screen.getByTestId("objective-map-tab-gaps").getAttribute("class")).toContain("bg-background");
  });

  it("selecting a Gap only shows its detail -- no write call happens on selection", async () => {
    mockRoutes({ objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(), gap: gapDetail() });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));

    await waitFor(() => expect(screen.getByTestId("gap-detail-g1")).toBeInTheDocument());
    expect(mockApi.post).not.toHaveBeenCalled();
  });

  it("records a manual Gap decision only on explicit submission", async () => {
    mockRoutes({ objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(), gap: gapDetail() });
    mockApi.post.mockResolvedValue({ id: 1, gap_id: 1, gap_key: "g1", decision: "acknowledge", priority_band: "unset", rationale: "", captured_digest: "", captured_revision_id: null, decision_method: "manual", decided_by: "dev", superseded_by_id: null, created_at: 2000 });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));
    await waitFor(() => expect(screen.getByTestId("gap-decision-acknowledge")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("gap-decision-acknowledge"));
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/product-gaps/g1/decisions",
      expect.objectContaining({ decision: "acknowledge" }),
    ));
  });

  it("filters the Gap list by lifecycle without re-sorting the remaining entries", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(),
      gapWorkbench: gapWorkbench({ entries: [gapEntry({ gap_key: "g1" }), gapEntry({ gap_key: "g2", lifecycle: "resolved" })] }),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    await screen.findByTestId("gap-entry-g2");

    fireEvent.change(screen.getByLabelText("解消状態で絞り込み"), { target: { value: "resolved" } });
    await waitFor(() => expect(screen.queryByTestId("gap-entry-g1")).not.toBeInTheDocument());
    expect(screen.getByTestId("gap-entry-g2")).toBeInTheDocument();
  });

  it("shows the deep_link_state='unavailable' reason instead of a fabricated link", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(),
      gapWorkbench: gapWorkbench(),
      gap: gapDetail({
        source_refs: [
          {
            id: 1, gap_id: 1, source_kind: "node_anomaly", source_ref: "n1|dedupe",
            source_state: "current", title: null, detail: null, severity: null, severity_vocabulary: null,
            deep_link: null, deep_link_state: "unavailable", captured_digest: "", captured_snapshot_id: null,
            captured_run_id: null, captured_revision_id: null, note: "", decision_method: "deterministic",
            created_by: null, created_at: 1000, superseded_by_id: null,
          },
        ],
      }),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));

    await waitFor(() => expect(screen.getByTestId("gap-source-deep-link-unavailable-1")).toBeInTheDocument());
    expect(screen.queryByTestId("gap-source-deep-link-1")).not.toBeInTheDocument();
  });
});

describe("ObjectiveMapPage empty/error states", () => {
  beforeEach(() => mockApi.get.mockReset());

  it("renders the no_objective empty state for a brand-new System", async () => {
    mockRoutes({ objectiveMap: objectiveMap({ nodes: [], root_objective_ids: [] }), gapWorkbench: gapWorkbench({ entries: [] }) });
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("objective-map-empty-no-objective")).toBeInTheDocument());
    expect(screen.queryByTestId("objective-map-empty-no-gap")).not.toBeInTheDocument();
  });

  it("renders the no_gap empty state distinctly from no_objective", async () => {
    mockRoutes({ objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench({ entries: [] }) });
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("objective-map-empty-no-gap")).toBeInTheDocument());
    expect(screen.queryByTestId("objective-map-empty-no-objective")).not.toBeInTheDocument();
  });

  it("renders a load-error card when both endpoints fail, with retry", async () => {
    mockRoutes({});
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("objective-map-load-error")).toBeInTheDocument());
  });

  it("degrades one lane alone when only the Gap Workbench fails", async () => {
    mockRoutes({ objectiveMap: objectiveMap() });
    await renderPage();
    await waitFor(() => expect(screen.getByTestId("objective-tree")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("objective-map-tab-gaps"));
    await waitFor(() => expect(screen.getByTestId("gap-workbench-lane-error")).toBeInTheDocument());
  });
});
