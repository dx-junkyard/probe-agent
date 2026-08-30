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
  ObjectiveMapOut, ProductGapDetailOut, ProductMilestoneDetailOut, ProductObjectiveDetailOut,
} from "@/api/types";

const mockApi = { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };

// §3.4: objective-map.tsx reads `useAuth().systemId` to detect a System
// switch (Epic #427 P2 review). `mockSystemId` is mutable so a test can
// simulate a switch; `@/api/client`'s `getSystemId()` shares the same value
// since `sysKey()` (the react-query cache key) reads it too -- following the
// same `vi.mock("@/api/client")` / `vi.mock("@/api/auth")` pattern
// `phase0-empty-states.test.tsx` establishes.
let mockSystemId: number | null = 1;

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
  getSystemId: () => mockSystemId,
  setSystemId: (id: number | null) => { mockSystemId = id; },
  ApiError,
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "dev", role: "admin" },
    isAdmin: true,
    loading: false,
    systemId: mockSystemId,
    systems: [],
    login: vi.fn(),
    logout: vi.fn(),
    selectSystem: vi.fn(),
    refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: ReactNode }) => children,
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
    current_revision_id: 1, current_revision_number: 1, decision_digest: "abc",
    effective_target_state: "10% 未満", effective_target_availability: "own",
    current_revision: {
      id: 1, gap_id: 1, revision_number: 1, title: "初回決済フォームでの離脱",
      current_state: "40% が離脱している", target_state: "10% 未満",
      target_state_mode: "own", interpretation: "入力項目が多すぎる",
      suggested_priority_note: "", content_digest: "abc",
      authored_by_kind: "developer", decision_method: "manual", intelligence_run_id: null,
      change_note: "", created_by: "dev", created_at: 1000, revision_state: "current",
      superseded_by_id: null,
    },
    source_refs: [], journey_links: [], evidence_refs: [], artifact_links: [], decisions: [],
    degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function objectiveDetail(overrides: Partial<ProductObjectiveDetailOut> = {}): ProductObjectiveDetailOut {
  return {
    id: 1, system_id: 1, objective_key: "o1", current_revision_id: 1, current_revision_number: 1,
    title: "決済の離脱率を下げる", objective_state: "confirmed", recheck_state: "current",
    parent_objective_id: null, parent_objective_key: null, created_by: "dev", created_at: 1000, updated_at: 1000,
    current_revision: {
      id: 1, objective_id: 1, revision_number: 1, title: "決済の離脱率を下げる", intent: "", contribution: "",
      scope_note: "", summary: "", content_digest: "obj-digest", authored_by_kind: "developer",
      decision_method: "manual", intelligence_run_id: null, change_note: "", created_by: "dev",
      created_at: 1000, revision_state: "current", superseded_by_id: null,
    },
    upstream_refs: [], decisions: [], degraded_sections: [], degraded_detail: {},
    ...overrides,
  };
}

function milestoneDetail(overrides: Partial<ProductMilestoneDetailOut> = {}): ProductMilestoneDetailOut {
  return {
    id: 1, system_id: 1, milestone_key: "m1", objective_id: 1, objective_key: "o1",
    current_revision_id: 1, current_revision_number: 1, title: "初回決済を完了させる",
    design_status: "confirmed", achievement: "unassessed", assessability: "assessable",
    recheck_state: "current", created_by: "dev", created_at: 1000, updated_at: 1000,
    current_revision: {
      id: 1, milestone_id: 1, revision_number: 1, title: "初回決済を完了させる", target_state: "10% 未満",
      verification_method: "manual_review", verification_note: "", sequence_hint: 0, summary: "",
      content_digest: "ms-digest", authored_by_kind: "developer", decision_method: "manual",
      intelligence_run_id: null, change_note: "", created_by: "dev", created_at: 1000,
      revision_state: "current", superseded_by_id: null,
    },
    dependencies: [], decisions: [], assessments: [], degraded_sections: [], degraded_detail: {},
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

// Every describe block below resets the API mock itself; `mockSystemId`
// (§3.4's System-switch detection) is reset here once so no test leaks its
// own simulated System switch into the next.
beforeEach(() => { mockSystemId = 1; });

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
    // §3.1: selecting an Objective force-opens its path, so its Milestones
    // are visible without a further toggle. (Clicking the toggle here would
    // now COLLAPSE it -- that is the fix working, not a regression.)
    // Milestone/design_status and achievement are two separate labels (§1.3).
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

describe("ObjectiveMapPage: Gap decision digest (§B)", () => {
  beforeEach(() => { mockApi.get.mockReset(); mockApi.post.mockReset(); });

  it("sends the Gap's decision_digest (not current_revision.content_digest) on a decision", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(),
      gap: gapDetail({ decision_digest: "gap-decision-digest" }),
    });
    mockApi.post.mockResolvedValue({
      id: 1, gap_id: 1, gap_key: "g1", decision: "acknowledge", priority_band: "unset",
      rationale: "", captured_digest: "gap-decision-digest", captured_revision_id: null,
      decision_method: "manual", decided_by: "dev", superseded_by_id: null, created_at: 2000,
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));
    fireEvent.click(await screen.findByTestId("gap-decision-acknowledge"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/product-gaps/g1/decisions",
      expect.objectContaining({ decision: "acknowledge", captured_digest: "gap-decision-digest" }),
    ));
  });

  it("sends decision_digest on prioritize too", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(),
      gap: gapDetail({ decision_digest: "gap-decision-digest" }),
    });
    mockApi.post.mockResolvedValue({
      id: 1, gap_id: 1, gap_key: "g1", decision: "prioritize", priority_band: "now",
      rationale: "", captured_digest: "gap-decision-digest", captured_revision_id: null,
      decision_method: "manual", decided_by: "dev", superseded_by_id: null, created_at: 2000,
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));
    fireEvent.click(await screen.findByTestId("gap-decision-prioritize"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/product-gaps/g1/decisions",
      expect.objectContaining({ decision: "prioritize", captured_digest: "gap-decision-digest" }),
    ));
  });

  it("shows a recoverable notice (never a silent retry) on a stale-digest 409, and its control refetches the Gap", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(),
      gap: gapDetail({ decision_digest: "stale-digest" }),
    });
    mockApi.post.mockRejectedValue(new ApiError(
      409, "指定された digest が現在の内容と一致しません。", "product_gap_decision_stale_digest",
    ));
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));

    const getCallsBeforeRetry = mockApi.get.mock.calls.length;
    fireEvent.click(await screen.findByTestId("gap-decision-acknowledge"));

    await waitFor(() => expect(screen.getByTestId("stale-digest-notice")).toBeInTheDocument());
    // Never a silent/automatic retry with the new digest.
    expect(mockApi.post).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("gap-decision-rejected")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("stale-digest-reload"));
    await waitFor(() => expect(mockApi.get.mock.calls.length).toBeGreaterThan(getCallsBeforeRetry));
  });
});

describe("ObjectiveMapPage: effective target display (§C)", () => {
  beforeEach(() => mockApi.get.mockReset());

  it("renders 'own' target text", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(),
      gap: gapDetail({ effective_target_state: "10% 未満", effective_target_availability: "own" }),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));
    const dd = await screen.findByTestId("gap-detail-target-state");
    expect(dd.textContent).toContain("10% 未満");
  });

  it("renders a resolved (inherited) target distinctly", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(),
      gap: gapDetail({ effective_target_state: "Milestone の目標", effective_target_availability: "resolved" }),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));
    const dd = await screen.findByTestId("gap-detail-target-state");
    expect(dd.textContent).toContain("Milestone の目標");
    expect(dd.getAttribute("data-target-availability")).toBe("resolved");
  });

  it("never renders 'unavailable' as an empty target or 'no target set'", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(),
      gap: gapDetail({ effective_target_state: null, effective_target_availability: "unavailable" }),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));
    const dd = await screen.findByTestId("gap-detail-target-state");
    expect(dd.textContent).not.toBe("");
    expect(dd.textContent).not.toContain("(未記入)");
    expect(dd.textContent).toContain("取得できませんでした");
  });

  it("renders 'unknown' distinctly from 'unavailable'", async () => {
    mockRoutes({
      objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(),
      gap: gapDetail({ effective_target_state: null, effective_target_availability: "unknown" }),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));
    const dd = await screen.findByTestId("gap-detail-target-state");
    expect(dd.textContent).toContain("まだ決めていません");
    expect(dd.textContent).not.toContain("取得できませんでした");
  });
});

describe("ObjectiveMapPage: Gap to Journey link writes through the Journey endpoint (§A)", () => {
  beforeEach(() => { mockApi.get.mockReset(); mockApi.post.mockReset(); });

  it("posts to /ux-design/journeys/{journey_key}/upstream-refs with ref_kind=product_gap", async () => {
    mockApi.get.mockImplementation((path?: string) => {
      if (path === "/objective-map") return Promise.resolve(objectiveMap());
      if (path === "/gap-workbench") return Promise.resolve(gapWorkbench());
      if (path === "/product-gaps/g1") return Promise.resolve(gapDetail());
      if (path === "/ux-design/journeys") {
        return Promise.resolve({
          system_id: 1, generated_at: 0,
          journeys: [{
            id: 1, system_id: 1, journey_key: "j1", perspective: "as_is", baseline_mode: "undecided",
            baseline_journey_id: null, baseline_journey_key: null, baseline_state: "not_applicable",
            current_revision_id: null, current_revision_number: null, title: "初回決済ジャーニー",
            design_status: "proposed", recheck_state: "current", created_by: null, created_at: 0, updated_at: 0,
          }],
          degraded_sections: [], degraded_detail: {},
        });
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue({
      id: 1, journey_id: 1, ref_kind: "product_gap", target_ref: "g1", target_row_id: null, target_name: null,
      relation_status: "confirmed", target_state: "", target_resolution: "resolved", recheck_state: "current",
      captured_digest: "", captured_session_id: null, note: "", decision_method: "manual",
      created_by: "dev", created_at: 2000, superseded_by_id: null,
    });

    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));
    fireEvent.change(await screen.findByLabelText("関連付ける Journey"), { target: { value: "j1" } });
    fireEvent.click(screen.getByTestId("gap-journey-link-submit"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/ux-design/journeys/j1/upstream-refs",
      expect.objectContaining({ ref_kind: "product_gap", target_ref: "g1" }),
    ));
  });
});

describe("ObjectiveMapPage: Objective/Milestone/Gap forms reach a usable operation (§D)", () => {
  beforeEach(() => { mockApi.get.mockReset(); mockApi.post.mockReset(); });

  it("create_objective posts to /product-objectives with the developer-entered key", async () => {
    mockApi.get.mockImplementation((path?: string) => {
      if (path === "/objective-map") return Promise.resolve(objectiveMap());
      if (path === "/gap-workbench") return Promise.resolve(gapWorkbench());
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue({ ...objectiveDetail(), objective_key: "o2" });

    await renderPage();
    await screen.findByTestId("create-objective-form");
    fireEvent.change(screen.getByLabelText("objective_key"), { target: { value: "o2" } });
    fireEvent.click(screen.getByTestId("create-objective-submit"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/product-objectives", { objective_key: "o2" },
    ));
  });

  it("confirming an Objective sends current_revision.content_digest as captured_digest", async () => {
    mockApi.get.mockImplementation((path?: string) => {
      if (path === "/objective-map") return Promise.resolve(objectiveMap());
      if (path === "/gap-workbench") return Promise.resolve(gapWorkbench());
      if (path === "/product-objectives/o1") return Promise.resolve(objectiveDetail());
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue({
      id: 1, objective_id: 1, objective_key: "o1", decision: "confirm", rationale: "",
      captured_digest: "obj-digest", captured_revision_id: 1, decision_method: "manual",
      decided_by: "dev", superseded_by_id: null, created_at: 2000,
    });

    await renderPage();
    fireEvent.click(within(await screen.findByTestId("objective-node-o1")).getByText("決済の離脱率を下げる"));
    fireEvent.click(await screen.findByTestId("objective-decision-confirm"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/product-objectives/o1/decisions",
      expect.objectContaining({ decision: "confirm", captured_digest: "obj-digest" }),
    ));
  });

  it("assessing a Milestone as met sends the Milestone's own content_digest", async () => {
    mockApi.get.mockImplementation((path?: string) => {
      if (path === "/objective-map") return Promise.resolve(objectiveMap());
      if (path === "/gap-workbench") return Promise.resolve(gapWorkbench());
      if (path === "/product-objectives/o1") return Promise.resolve(objectiveDetail());
      if (path === "/product-milestones/m1") return Promise.resolve(milestoneDetail());
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue({
      id: 1, milestone_id: 1, milestone_key: "m1", assessment: "met", rationale: "", evidence_note: "",
      captured_digest: "ms-digest", captured_revision_id: 1, decision_method: "manual",
      assessed_by: "dev", superseded_by_id: null, created_at: 2000,
    });

    await renderPage();
    fireEvent.click(within(await screen.findByTestId("objective-node-o1")).getByText("決済の離脱率を下げる"));
    // §3.1: selecting the Objective already reveals its Milestones; a toggle
    // click here would collapse them again.
    fireEvent.click(await screen.findByTestId("milestone-node-m1"));
    fireEvent.click(await screen.findByTestId("milestone-assessment-met"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/product-milestones/m1/assessments",
      expect.objectContaining({ assessment: "met", captured_digest: "ms-digest" }),
    ));
  });

  it("create_gap posts to /product-gaps with the selected Milestone and the developer-entered key", async () => {
    mockApi.get.mockImplementation((path?: string) => {
      if (path === "/objective-map") return Promise.resolve(objectiveMap());
      if (path === "/gap-workbench") return Promise.resolve(gapWorkbench());
      if (path === "/product-gaps/g1") return Promise.resolve(gapDetail());
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue({ ...gapDetail(), gap_key: "g2" });

    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    await screen.findByTestId("create-gap-form");
    fireEvent.change(screen.getByLabelText("gap_key"), { target: { value: "g2" } });
    fireEvent.click(screen.getByTestId("create-gap-submit"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/product-gaps", { milestone_key: "m1", gap_key: "g2" },
    ));
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

describe("ObjectiveMapPage: evidence / artifact deep links (§5.8)", () => {
  beforeEach(() => mockApi.get.mockReset());

  it("links a kind that has a screen and states the reason for one that has none", async () => {
    // The route comes from the server's per-kind table; the page never
    // assembles one. A kind with no screen renders as unavailable, never as
    // a plausible URL.
    mockRoutes({
      objectiveMap: objectiveMap(), gapWorkbench: gapWorkbench(),
      gap: gapDetail({
        evidence_refs: [
          {
            id: 1, gap_id: 1, evidence_kind: "trace", evidence_ref: "t-1",
            deep_link: "/components", deep_link_state: "available",
            captured_snapshot_id: null, note: "", decision_method: "manual",
            created_by: null, created_at: 1000, superseded_by_id: null,
          },
          {
            id: 2, gap_id: 1, evidence_kind: "human_report", evidence_ref: "ops report",
            deep_link: null, deep_link_state: "unavailable",
            captured_snapshot_id: null, note: "", decision_method: "manual",
            created_by: null, created_at: 1000, superseded_by_id: null,
          },
        ],
        artifact_links: [
          {
            id: 3, gap_id: 1, link_kind: "product_feature", target_ref: "feat-a",
            target_row_id: null, deep_link: null, deep_link_state: "unavailable",
            captured_digest: "", note: "", decision_method: "manual",
            created_by: null, created_at: 1000, superseded_by_id: null,
          },
        ],
      }),
    });
    await renderPage();
    fireEvent.click(await screen.findByTestId("objective-map-tab-gaps"));
    fireEvent.click(await screen.findByTestId("gap-entry-g1"));

    const link = await screen.findByTestId("gap-evidence-deep-link-1");
    expect(link).toHaveAttribute("href", "/components");
    expect(screen.getByTestId("gap-evidence-deep-link-unavailable-2")).toBeInTheDocument();
    expect(screen.queryByTestId("gap-evidence-deep-link-2")).toBeNull();
    expect(screen.getByTestId("gap-artifact-link-deep-link-unavailable-3")).toBeInTheDocument();
    expect(screen.queryByTestId("gap-artifact-link-deep-link-3")).toBeNull();
  });
});
