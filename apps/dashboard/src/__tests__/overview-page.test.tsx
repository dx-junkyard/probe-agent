/// <reference types="vitest/globals" />
// Issue #384: the Overview PAGE's own states.
//
// The display components are covered by `overview.test.tsx`; this file covers
// the branches that only exist at page level and that #384 lists explicitly:
// zero Systems, loading, a partial API failure, a total failure, and the
// contract that the old metric cards / Component table are gone from the first
// view.

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { OverviewOut } from "@/api/types";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};
let mockSystemId: number | null = 1;
let mockSystems: { id: number; name: string }[] = [{ id: 1, name: "alpha" }];

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => mockSystemId,
  setSystemId: (id: number | null) => { mockSystemId = id; },
  ApiError: class extends Error {},
}));

vi.mock("@/api/auth", () => ({
  useAuth: () => ({
    user: { id: 1, username: "admin", role: "admin" },
    isAdmin: true,
    loading: false,
    systemId: mockSystemId,
    systems: mockSystems,
    login: vi.fn(),
    logout: vi.fn(),
    selectSystem: vi.fn(),
    refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: ReactNode }) => children,
}));

function createWrapper(initialEntries: string[] = ["/"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

function overview(overrides: Partial<OverviewOut> = {}): OverviewOut {
  return {
    system_id: 1,
    generated_at: 1000,
    interview_session_id: 7,
    brief: {
      system_id: 1,
      session_id: 7,
      built: true,
      vision: null,
      vision_missing_information: [],
      system_purpose: [],
      core_capabilities: [],
      core_capability_initial_count: 0,
      key_unconfirmed: [],
      detail_counts: {},
      readiness_state: "not_built",
      readiness_label: "まだ判断できません",
      readiness_description: "判断の対象となるシステム理解がまだありません。",
      readiness_reasons: [],
      changes_since_confirmation: [],
      confirmed_at: null,
      confirmed_revision_id: null,
      revision_id: null,
      snapshot_id: null,
    },
    snapshot_id: null,
    snapshot_commit_sha: null,
    latest_ready_snapshot_id: null,
    snapshot_freshness: "unavailable",
    understanding_revision_id: null,
    understanding_confirmed_at: null,
    findings: [],
    findings_initial_count: 0,
    findings_state: "not_compared",
    findings_baseline_state: "no_baseline",
    findings_baseline_label: "まだ理解を確認していないため、比較の基準がありません",
    findings_baseline_at: null,
    next_action: {
      key: "prepare_repository",
      label: "Repository を設定する",
      reason: "解析対象のリポジトリがまだ登録されていません。",
      completion_condition: "リポジトリを登録し、snapshot が 1 件作成されること。",
      value: "コードを読み取れるようになります。",
      target: { route: "/repository", label: "Repository を開く", params: {}, anchor: null },
      rule_row: 1,
      source_state_ids: ["repository.configuration.missing"],
      source_finding_ids: [],
      blockers: [],
    },
    next_action_state: "available",
    next_action_message: "",
    loop_stages: [
      { stage: "setup", label: "Setup: 対象を登録する", status: "current", meaning: "m", stage_completion_hint: "n", complete: false },
    ],
    user_phase: "setup",
    runtime: {
      state: "no_signal",
      freshness: "never_received",
      freshness_label: "まだ受信していません",
      transport_freshness: "never_received",
      last_real_trace_at: null,
      seconds_since_last_trace: null,
      last_trace_at: null,
      seconds_since_last_any_trace: null,
      evaluated_at: 1000,
      real_trace_count_5m: 0,
      real_trace_count_1h: 0,
      real_trace_count_24h: 0,
      delayed_after_seconds: 900,
      stale_after_seconds: 86400,
      component_count: 0,
      total_trace_count: 0,
      mode_counts: {},
      window_seconds: 86400,
      error_count: 0,
      runtime_mismatch_count: 0,
      replayable_count: 0,
      partial_count: 0,
      unreplayable_count: 0,
      not_captured_count: 0,
      observed_component_count: 0,
      known_component_count: 0,
      core_capability_count: 0,
      capability_coverage_state: "not_computed",
      observed_capability_count: null,
      unmapped_component_count: null,
    },
    degraded_sections: [],
    degraded_detail: {},
    ...overrides,
  };
}

async function renderPage() {
  const { default: OverviewPage } = await import("@/pages/overview");
  return render(<OverviewPage />, { wrapper: createWrapper() });
}

describe("Overview page (Issue #384)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockSystems = [{ id: 1, name: "alpha" }];
  });

  test("zero Systems points at System creation without calling the System-scoped endpoint", async () => {
    // No Systems means no selected System either, so the System-scoped query
    // is disabled: the zero-System branch must stand on its own.
    mockSystems = [];
    mockSystemId = null;
    mockApi.get.mockResolvedValue(null);
    await renderPage();

    const card = await screen.findByTestId("overview-no-systems");
    expect(within(card).getByText(/System を作成してください/)).toBeInTheDocument();
    expect(mockApi.get).not.toHaveBeenCalledWith("/overview");
  });

  test("the meaning order is Brief -> findings -> next action -> loop -> runtime", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/overview" ? Promise.resolve(overview()) : Promise.resolve(null),
    );
    await renderPage();
    await screen.findByTestId("overview-system-brief");

    const order = [
      "overview-system-brief",
      "overview-findings",
      "overview-next-action",
      "overview-loop",
      "overview-runtime",
    ].map((id) => screen.getByTestId(id));
    for (let i = 1; i < order.length; i += 1) {
      // Node.compareDocumentPosition: 4 = the argument follows the reference.
      expect(order[i - 1].compareDocumentPosition(order[i]) & 4).toBeTruthy();
    }
  });

  test("Brief, findings and next action are three ADJACENT regions, not stacked", async () => {
    // jsdom cannot measure layout, so this asserts the structural property the
    // browser measurement depends on: at `xl` the three must be separate grid
    // children. When findings and the next action were stacked under the Brief,
    // 「今わかったこと」 started at 824px and the CTA at 1066px on a 1280×720
    // screen — the reading order was right and two of the four things #384
    // requires in the first view were still below the fold.
    mockApi.get.mockImplementation((path: string) =>
      path === "/overview" ? Promise.resolve(overview()) : Promise.resolve(null),
    );
    const { container } = await renderPage();
    await screen.findByTestId("overview-system-brief");

    const grid = container.querySelector(".grid");
    expect(grid).not.toBeNull();
    const regionOf = (testId: string) => {
      const el = screen.getByTestId(testId);
      return Array.from(grid!.children).find((child) => child.contains(el));
    };
    const brief = regionOf("overview-system-brief");
    const findings = regionOf("overview-findings");
    const action = regionOf("overview-next-action");
    expect(brief).toBeTruthy();
    expect(new Set([brief, findings, action]).size).toBe(3);
    // The secondary Runtime area shares the action's column deliberately: it is
    // the one section allowed below the first view (#380 UX原則 4).
    expect(regionOf("overview-runtime")).toBe(action);
  });

  test("the old metric cards and Component table are gone from the first view", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/overview" ? Promise.resolve(overview()) : Promise.resolve(null),
    );
    await renderPage();
    await screen.findByTestId("overview-system-brief");

    // The full Component list belongs to /components (#384: 重複表示しない).
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("Total Traces")).not.toBeInTheDocument();
    expect(screen.queryByText("Active Modes")).not.toBeInTheDocument();
    expect(screen.queryByText("Last Seen")).not.toBeInTheDocument();
  });

  test("a partial failure names the missing section and still renders the rest", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/overview"
        ? Promise.resolve(
          overview({
            brief: null,
            degraded_sections: ["brief"],
            degraded_detail: { brief: "RuntimeError: boom" },
          }),
        )
        : Promise.resolve(null),
    );
    await renderPage();

    expect(await screen.findByTestId("overview-degraded")).toHaveTextContent("brief");
    expect(screen.getByTestId("overview-brief-unavailable")).toBeInTheDocument();
    // The page body is not blank: the action, the loop and the runtime survive.
    expect(screen.getByTestId("overview-next-action")).toBeInTheDocument();
    expect(screen.getByTestId("overview-loop")).toBeInTheDocument();
    expect(screen.getByTestId("overview-runtime")).toBeInTheDocument();
  });

  test("a total failure offers a retry and never guesses a next action", async () => {
    mockApi.get.mockRejectedValue(new Error("network"));
    await renderPage();

    const card = await screen.findByTestId("overview-load-error");
    expect(within(card).getByRole("button", { name: "再試行" })).toBeInTheDocument();
    expect(screen.queryByTestId("overview-next-action-cta")).not.toBeInTheDocument();
  });

  test("loading renders a skeleton, not an empty state", async () => {
    mockApi.get.mockImplementation(() => new Promise(() => {}));
    await renderPage();
    expect(screen.getByTestId("overview-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("overview-findings-none")).not.toBeInTheDocument();
  });

  test("a degraded next action renders as a sentence, never as a disabled button", async () => {
    // #383: 推測でCTAを出さない。The server returns `unavailable`; the page must
    // not fill the gap with a default CTA of its own.
    mockApi.get.mockImplementation((path: string) =>
      path === "/overview"
        ? Promise.resolve(
          overview({
            next_action: null,
            next_action_state: "unavailable",
            next_action_message: "現在の状態を判定できませんでした。",
            degraded_sections: ["brief"],
            brief: null,
          }),
        )
        : Promise.resolve(null),
    );
    await renderPage();

    expect(await screen.findByTestId("overview-next-action-unavailable")).toHaveTextContent(
      "現在の状態を判定できませんでした。",
    );
    expect(screen.queryByTestId("overview-next-action-cta")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /する$/ })).not.toBeInTheDocument();
  });

  test("Snapshot, revision and last confirmation are first-view context", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/overview"
        ? Promise.resolve(
          overview({
            snapshot_id: 31,
            snapshot_commit_sha: "41e8b78c9d2a",
            latest_ready_snapshot_id: 33,
            snapshot_freshness: "stale",
            understanding_revision_id: 24,
            understanding_confirmed_at: 1_759_000_000,
          }),
        )
        : Promise.resolve(null),
    );
    const { container } = await renderPage();

    const context = await screen.findByTestId("overview-context");
    expect(context).toHaveTextContent("#31");
    expect(context).toHaveTextContent("41e8b78c");
    expect(context).toHaveTextContent("最新ではない断面");
    expect(context).toHaveTextContent("#24");
    // Server-decided: the page never compares snapshot ids itself.
    expect(
      within(context).getByText(/最新ではない断面/).closest("[data-snapshot-freshness]"),
    ).toHaveAttribute("data-snapshot-freshness", "stale");
    // It precedes the Brief, so the context is read before the claims it
    // qualifies — on every viewport, since it is in the page header.
    const brief = screen.getByTestId("overview-system-brief");
    expect(context.compareDocumentPosition(brief) & 4).toBeTruthy();
    expect(container).toBeTruthy();
  });

  test("the page heading outline is h1 -> h2 with no skipped level", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/overview" ? Promise.resolve(overview()) : Promise.resolve(null),
    );
    await renderPage();
    await screen.findByTestId("overview-system-brief");

    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    const h2s = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    // Issue #390: the Purpose Frame leads the main column, above System
    // Brief -- the Epic's question 「何のためのシステムか」 must be
    // answerable before anything else on the page.
    expect(h2s).toEqual([
      "目的の連鎖 (Purpose Chain)",
      "System Brief",
      "今わかったこと",
      "次にすること",
      "改善ループの現在地",
      "Runtime health",
    ]);
  });

  test("the single CTA carries the server's action key and route", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/overview" ? Promise.resolve(overview()) : Promise.resolve(null),
    );
    await renderPage();

    const cta = await screen.findByTestId("overview-next-action-cta");
    expect(cta).toHaveAttribute("data-action-key", "prepare_repository");
    expect(cta).toHaveAttribute("href", "/repository");
    await waitFor(() => expect(screen.getAllByTestId("overview-next-action-cta")).toHaveLength(1));
  });
});


// ── Experiments deep link from the Overview CTA (Issue #383) ─────────

describe("Experiments deep link (Issue #383)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockSystems = [{ id: 1, name: "alpha" }];
  });

  function experiment(overrides: Record<string, unknown>) {
    return {
      id: 11, system_id: 1, feature_id: "f", objective: "o", snapshot_id: 1,
      baseline_commit: "abc", config_revision: "r", execution_config: "{}",
      status: "completed", error: null, human_decision: "undecided",
      human_decision_variant_key: null, human_decision_note: "",
      created_at: 1, started_at: 1, completed_at: 2, variants: [],
      ...overrides,
    };
  }

  function mockExperiments(rows: unknown[]) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/experiments") return Promise.resolve(rows);
      if (path === "/snapshots") return Promise.resolve([]);
      if (path === "/repository/drafts/latest") {
        return Promise.resolve({ system_profile_draft: null, feature_drafts: [] });
      }
      return Promise.resolve(null);
    });
  }

  async function renderExperiments(entry: string) {
    const { default: ExperimentsPage } = await import("@/pages/experiments");
    return render(<Routes><Route path="/experiments" element={<ExperimentsPage />} /></Routes>, {
      wrapper: createWrapper([entry]),
    });
  }

  const expanded = (container: HTMLElement, id: number) =>
    container.querySelector(`[data-testid="experiment-detail-${id}"]`);
  const row = (container: HTMLElement, id: number) =>
    container.querySelector(`[data-testid="experiment-row-${id}"]`);

  test("a completed + undecided target is expanded, and a reload lands on it again", async () => {
    mockExperiments([experiment({ id: 11 }), experiment({ id: 12, completed_at: 3 })]);
    const first = await renderExperiments("/experiments?experiment=12");
    await waitFor(() => expect(expanded(first.container, 12)).toBeTruthy());
    expect(expanded(first.container, 11)).toBeNull();
    first.unmount();

    // The URL is the whole state, so a reload reproduces the selection.
    const second = await renderExperiments("/experiments?experiment=12");
    await waitFor(() => expect(expanded(second.container, 12)).toBeTruthy());
  });

  // The CTA's id is a snapshot of when the Overview rendered. By the time the
  // link is opened the experiment may already be settled — expanding it under
  // a 「採否を記録する」 CTA would claim a decision is pending when it is not.
  test.each([
    ["adopted", { human_decision: "adopted" }],
    ["rejected", { human_decision: "rejected" }],
    ["needs_more_data", { human_decision: "needs_more_data" }],
    ["running", { status: "running" }],
    ["failed", { status: "failed" }],
    ["draft", { status: "draft" }],
  ])("a %s target expands nothing and falls back to the list", async (_label, overrides) => {
    mockExperiments([experiment({ id: 11, ...overrides })]);
    const { container } = await renderExperiments("/experiments?experiment=11");
    await waitFor(() => expect(row(container, 11)).toBeTruthy());
    expect(expanded(container, 11)).toBeNull();
  });

  test("an unknown or another System's id expands nothing and selects no substitute", async () => {
    mockExperiments([experiment({ id: 11 })]);
    const { container } = await renderExperiments("/experiments?experiment=999");
    await waitFor(() => expect(row(container, 11)).toBeTruthy());
    expect(expanded(container, 999)).toBeNull();
    // Explicitly: it does not silently open some other row instead.
    expect(expanded(container, 11)).toBeNull();
  });

  test("manual expansion still works after an invalid target, and the linked row can be collapsed", async () => {
    mockExperiments([experiment({ id: 11 }), experiment({ id: 12, completed_at: 3 })]);
    const { container } = await renderExperiments("/experiments?experiment=999");
    await waitFor(() => expect(row(container, 11)).toBeTruthy());

    fireEvent.click(row(container, 11)!.querySelector(".cursor-pointer")!);
    await waitFor(() => expect(expanded(container, 11)).toBeTruthy());

    fireEvent.click(row(container, 11)!.querySelector(".cursor-pointer")!);
    await waitFor(() => expect(expanded(container, 11)).toBeNull());
  });

  test("the linked row can be collapsed by the developer", async () => {
    mockExperiments([experiment({ id: 12, completed_at: 3 })]);
    const { container } = await renderExperiments("/experiments?experiment=12");
    await waitFor(() => expect(expanded(container, 12)).toBeTruthy());
    fireEvent.click(row(container, 12)!.querySelector(".cursor-pointer")!);
    await waitFor(() => expect(expanded(container, 12)).toBeNull());
  });
});

// ── Time-dependent refresh (Issue #384 / review P1-4) ────────────────

describe("Overview refresh cadence", () => {
  test("schedules a refetch just after the next freshness boundary", async () => {
    const { overviewBoundaryDelay, OVERVIEW_MAX_STALENESS_MS } = await import("@/api/hooks");
    const runtime = overview().runtime!;

    // 100s since the last trace, `delayed` at 900s -> wake at 801s.
    expect(
      overviewBoundaryDelay({
        ...overview(),
        runtime: { ...runtime, seconds_since_last_trace: 100 },
      }),
    ).toBe(801_000);

    // Past `delayed`, the next boundary is `stale`.
    expect(
      overviewBoundaryDelay({
        ...overview(),
        runtime: { ...runtime, seconds_since_last_trace: 1000 },
      }),
    ).toBe(85_401_000);

    // Past both: no boundary left, so the bounded ceiling takes over. Without
    // it, a screen left open would never notice an externally-recorded
    // decision or a completed publish.
    expect(
      overviewBoundaryDelay({
        ...overview(),
        runtime: { ...runtime, seconds_since_last_trace: 90_000 },
      }),
    ).toBeNull();
    expect(OVERVIEW_MAX_STALENESS_MS).toBe(300_000);

    // Nothing received yet: there is no elapsed time to measure from.
    expect(
      overviewBoundaryDelay({
        ...overview(),
        runtime: { ...runtime, seconds_since_last_trace: null },
      }),
    ).toBeNull();
    expect(overviewBoundaryDelay({ ...overview(), runtime: null })).toBeNull();
    expect(overviewBoundaryDelay(undefined)).toBeNull();
  });
});
