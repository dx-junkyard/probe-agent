/// <reference types="vitest/globals" />
// Issue #384: the Overview PAGE's own states.
//
// The display components are covered by `overview.test.tsx`; this file covers
// the branches that only exist at page level and that #384 lists explicitly:
// zero Systems, loading, a partial API failure, a total failure, and the
// contract that the old metric cards / Component table are gone from the first
// view.

import { render, screen, waitFor, within } from "@testing-library/react";
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

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
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
    findings: [],
    findings_initial_count: 0,
    findings_state: "not_compared",
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
      { stage: "setup", label: "Setup: 対象を登録する", status: "current", meaning: "m", next_milestone: "n", complete: false },
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
      observed_capability_count: 0,
      core_capability_count: 0,
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
