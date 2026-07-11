/// <reference types="vitest/globals" />
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};
let mockSystemId: number | null = 1;
let mockSystems: { id: number; name: string }[] = [];

class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => mockSystemId,
  setSystemId: (id: number | null) => { mockSystemId = id; },
  getSessionToken: () => "fake-token",
  setSessionToken: vi.fn(),
  ApiError,
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

afterEach(() => {
  mockSystems = [];
});

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
  Toaster: () => null,
}));

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>
      <BrowserRouter>{children}</BrowserRouter>
    </QueryClientProvider>
  );
}

// ── Repository config tests ─────────────────────────────────────────

describe("Repository config page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("shows config values from the loaded system", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository") {
        return Promise.resolve({
          id: 1, system_id: 1, repo_path: "/repos/alpha",
          include_patterns: ["*.py", "*.ts"],
          exclude_patterns: ["__pycache__"],
        });
      }
      if (path === "/repository-candidates") {
        return Promise.resolve([{ name: "alpha", path: "/repos/alpha" }]);
      }
      if (path === "/repository/snapshots") return Promise.resolve([]);
      if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
      return Promise.resolve(null);
    });

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByRole("combobox")).toHaveValue("/repos/alpha");
    });
    const textareas = screen.getAllByRole("textbox");
    const includeTextarea = textareas.find(t => (t as HTMLTextAreaElement).value.includes("*.py"));
    expect(includeTextarea).toBeTruthy();
    expect((includeTextarea as HTMLTextAreaElement).value).toBe("*.py\n*.ts");
  });

  test("shows empty form when system has no config", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository") return Promise.resolve(null);
      if (path === "/repository-candidates") {
        return Promise.resolve([{ name: "alpha", path: "/repos/alpha" }]);
      }
      if (path === "/repository/snapshots") return Promise.resolve([]);
      if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
      return Promise.resolve(null);
    });

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByRole("combobox")).toBeInTheDocument();
    });
    expect(screen.getByRole("combobox")).toHaveValue("");
  });

  test("sends include_patterns and exclude_patterns as arrays", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository") {
        return Promise.resolve({
          id: 1, system_id: 1, repo_path: "/repos/alpha",
          include_patterns: ["*.py"], exclude_patterns: [],
        });
      }
      if (path === "/repository-candidates") {
        return Promise.resolve([{ name: "alpha", path: "/repos/alpha" }]);
      }
      if (path === "/repository/snapshots") return Promise.resolve([]);
      if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
      return Promise.resolve(null);
    });
    mockApi.put.mockResolvedValue({
      id: 1, system_id: 1, repo_path: "/repos/alpha",
      include_patterns: ["*.py", "*.ts"], exclude_patterns: ["node_modules"],
    });

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByRole("combobox")).toHaveValue("/repos/alpha");
    });

    const textareas = screen.getAllByRole("textbox");
    const includeTextarea = textareas.find(t => (t as HTMLTextAreaElement).value.includes("*.py"));
    const excludeTextarea = textareas.find(t => (t as HTMLTextAreaElement).placeholder?.includes("test_"));

    fireEvent.change(includeTextarea!, { target: { value: "*.py\n*.ts" } });
    fireEvent.change(excludeTextarea!, { target: { value: "node_modules" } });

    fireEvent.click(screen.getByText("Save Configuration"));

    await waitFor(() => {
      expect(mockApi.put).toHaveBeenCalledWith("/repository", {
        repo_path: "/repos/alpha",
        include_patterns: ["*.py", "*.ts"],
        exclude_patterns: ["node_modules"],
      });
    });
  });
});

// ── Experiment creation tests ───────────────────────────────────────

function setupExperimentMocks(experiments: unknown[] = []) {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/experiments") return Promise.resolve(experiments);
    if (path === "/repository/snapshots") return Promise.resolve([
      { id: 1, system_id: 1, commit_sha: "abc12345", status: "ready", file_count: 10, created_at: "2024-01-01T00:00:00Z" },
    ]);
    if (path === "/repository/drafts/latest") return Promise.resolve({ feature_drafts: [] });
    return Promise.resolve(null);
  });
}

async function openCreateDialog() {
  const { default: ExperimentsPage } = await import("@/pages/experiments");
  render(<ExperimentsPage />, { wrapper: createWrapper() });

  await waitFor(() => {
    expect(screen.getByText("New Experiment")).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText("New Experiment"));

  await waitFor(() => {
    expect(screen.getByPlaceholderText("feature-id")).toBeInTheDocument();
  });
}

function fillBasicFields() {
  fireEvent.change(screen.getByPlaceholderText("feature-id"), { target: { value: "feat-1" } });
  fireEvent.change(screen.getByPlaceholderText("What are you trying to learn?"), { target: { value: "Test objective" } });
  const selects = screen.getAllByRole("combobox");
  const snapshotSelect = selects[selects.length - 1];
  fireEvent.change(snapshotSelect, { target: { value: "1" } });
}

describe("Experiment creation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("button disabled when fewer than 2 valid variants", async () => {
    setupExperimentMocks();
    await openCreateDialog();
    fillBasicFields();

    const labelInputs = screen.getAllByPlaceholderText("Label (e.g., optimized-v1)");
    const patchInputs = screen.getAllByPlaceholderText("Patch text (unified diff format)");
    fireEvent.change(labelInputs[0], { target: { value: "variant-a" } });
    fireEvent.change(patchInputs[0], { target: { value: "patch-a" } });

    const buttons = screen.getAllByRole("button");
    const createBtn = buttons.find(b => b.textContent === "Create Experiment");
    expect(createBtn).toBeDisabled();
  });

  test("submits when 2 valid variants are provided", async () => {
    setupExperimentMocks();
    mockApi.post.mockResolvedValue({
      id: 1, feature_id: "feat-1", objective: "Test", status: "draft",
      variants: [], created_at: "2024-01-01",
    });

    await openCreateDialog();
    fillBasicFields();

    const labelInputs = screen.getAllByPlaceholderText("Label (e.g., optimized-v1)");
    const patchInputs = screen.getAllByPlaceholderText("Patch text (unified diff format)");

    fireEvent.change(labelInputs[0], { target: { value: "variant-a" } });
    fireEvent.change(patchInputs[0], { target: { value: "patch-a" } });
    fireEvent.change(labelInputs[1], { target: { value: "variant-b" } });
    fireEvent.change(patchInputs[1], { target: { value: "patch-b" } });

    const buttons = screen.getAllByRole("button");
    const createBtn = buttons.find(b => b.textContent === "Create Experiment")!;
    expect(createBtn).not.toBeDisabled();

    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/experiments", {
        feature_id: "feat-1",
        objective: "Test objective",
        snapshot_id: 1,
        variants: [
          { label: "variant-a", patch_text: "patch-a" },
          { label: "variant-b", patch_text: "patch-b" },
        ],
      });
    });
  });

  test("cannot delete variants below 2", async () => {
    setupExperimentMocks();
    await openCreateDialog();

    expect(screen.getByText("Variant 1")).toBeInTheDocument();
    expect(screen.getByText("Variant 2")).toBeInTheDocument();

    const trashIcons = document.querySelectorAll(".lucide-trash-2");
    expect(trashIcons.length).toBe(0);
  });

  test("shows a back link to the capability when ?capability= is present", async () => {
    setupExperimentMocks();
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: ExperimentsPage } = await import("@/pages/experiments");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/experiments?capability=doc-analysis"]}>
          <ExperimentsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const back = await screen.findByTestId("back-to-capability");
    expect(back).toHaveAttribute("href", "/capability-map?capability=doc-analysis");
  });
});

// ── Experiment decision tests ───────────────────────────────────────

describe("Experiment decision (adopted)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("adopted decision sends variant_key and non-empty note", async () => {
    const expData = {
      id: 1, feature_id: "feat-1", objective: "Test", status: "completed",
      human_decision: null, human_decision_variant_key: null, human_decision_note: null,
      created_at: "2024-01-01T00:00:00Z",
      variants: [
        { id: 1, variant_key: "baseline", label: "Baseline", is_baseline: true, status: "completed", patch_text: null, risk_note: null, error: null, metrics: {} },
        { id: 2, variant_key: "opt-v1", label: "Optimized V1", is_baseline: false, status: "completed", patch_text: "patch", risk_note: null, error: null, metrics: { latency: 0.5 } },
      ],
      comparison: {},
    };

    setupExperimentMocks([expData]);
    mockApi.put.mockResolvedValue({ ...expData, human_decision: "adopted" });

    const { default: ExperimentsPage } = await import("@/pages/experiments");
    render(<ExperimentsPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText(/Experiment #1/)).toBeInTheDocument();
    });

    const header = screen.getByText(/Experiment #1/).closest("[class*=cursor-pointer]")!;
    fireEvent.click(header);

    await waitFor(() => {
      expect(screen.getByText("Decision")).toBeInTheDocument();
    });

    const verdictSelect = screen.getAllByRole("combobox").find(
      s => s.querySelector("option[value='adopted']")
    ) as HTMLSelectElement;
    fireEvent.change(verdictSelect, { target: { value: "adopted" } });

    await waitFor(() => {
      expect(screen.getByText("Adopt Variant *")).toBeInTheDocument();
    });

    const variantSelect = screen.getAllByRole("combobox").find(
      s => s.querySelector("option[value='opt-v1']")
    ) as HTMLSelectElement;
    fireEvent.change(variantSelect, { target: { value: "opt-v1" } });

    const noteTextarea = screen.getByPlaceholderText("Reason for decision...");
    fireEvent.change(noteTextarea, { target: { value: "Better performance" } });

    fireEvent.click(screen.getByText("Save Decision"));

    await waitFor(() => {
      expect(mockApi.put).toHaveBeenCalledWith("/experiments/1/decision", {
        decision: "adopted",
        variant_key: "opt-v1",
        note: "Better performance",
      });
    });
  });
});

// ── Probe Patch explicit apply tests ────────────────────────────────

describe("Probe Patch application", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("requires typed confirmation and sends the pinned commit", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") {
        return Promise.resolve({
          system_id: 1,
          is_mock: false,
          plans: [{
            id: 10,
            feature_id: "feat-1",
            objective: "Observe behavior",
            status: "proposed",
            created_at: "2024-01-01",
            probe_points: [],
          }],
        });
      }
      if (path === "/repository/probe-patches") {
        return Promise.resolve([{
          id: 20,
          plan_id: 10,
          system_id: 1,
          snapshot_id: 5,
          commit_sha: "abcdef1234567890",
          diff: "diff --git a/a.py b/a.py",
          worktree_path: null,
          skipped: [],
          status: "generated",
          error: null,
          cleanup_state: "removed",
          cleanup_error: null,
          apply_status: "not_applied",
          apply_error: null,
          applied_at: null,
          applied_by_user_id: null,
          validation_runs: [
            { id: 1, variant: "baseline", overall_success: true, commands: [] },
            { id: 2, variant: "probed", overall_success: true, commands: [] },
          ],
          created_at: "2024-01-01",
        }]);
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue({ apply_status: "applied" });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Feature: feat-1"));
    await waitFor(() => expect(screen.getByText("Apply")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Apply"));

    const confirmButton = await screen.findByText("Apply to Repository");
    expect(confirmButton).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("APPLY"), {
      target: { value: "APPLY" },
    });
    expect(confirmButton).not.toBeDisabled();
    fireEvent.click(confirmButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/repository/probe-patches/20/apply",
        {
          confirmed: true,
          expected_commit_sha: "abcdef1234567890",
        },
      );
    });
  });
});

// ── Probe Planner ?plan= deep link (Issue #177) ─────────────────────

function mockTwoPlans() {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/repository/probe-plans") {
      return Promise.resolve({
        system_id: 1,
        is_mock: false,
        plans: [
          { id: 10, feature_id: "feat-1", objective: "Observe A", status: "proposed", created_at: "2024-01-01", probe_points: [] },
          { id: 11, feature_id: "feat-2", objective: "Observe B", status: "proposed", created_at: "2024-01-02", probe_points: [] },
        ],
      });
    }
    if (path === "/repository/probe-patches") return Promise.resolve([]);
    return Promise.resolve(null);
  });
}

function renderProbePlannerAt(route: string) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return import("@/pages/probe-planner").then(({ default: ProbePlannerPage }) =>
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[route]}>
          <ProbePlannerPage />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  );
}

describe("Probe Planner ?plan= deep link", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("expands the matching plan when ?plan= is present", async () => {
    mockTwoPlans();
    await renderProbePlannerAt("/probe-planner?plan=11");

    await waitFor(() => {
      expect(screen.getByText("Feature: feat-2")).toBeInTheDocument();
    });
    // Expanded content (Probe Points section) is rendered only for the open card.
    await waitFor(() => {
      expect(screen.getByText("Probe Points (0)")).toBeInTheDocument();
    });
    const { toast } = await import("sonner");
    expect(toast.error).not.toHaveBeenCalled();
  });

  test("falls back to the normal list without ?plan=", async () => {
    mockTwoPlans();
    await renderProbePlannerAt("/probe-planner");

    await waitFor(() => {
      expect(screen.getByText("Feature: feat-1")).toBeInTheDocument();
      expect(screen.getByText("Feature: feat-2")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Probe Points \(/)).not.toBeInTheDocument();
    const { toast } = await import("sonner");
    expect(toast.error).not.toHaveBeenCalled();
  });

  test("shows a warning and normal list for an unknown plan id", async () => {
    mockTwoPlans();
    await renderProbePlannerAt("/probe-planner?plan=999");

    await waitFor(() => {
      expect(screen.getByText("Feature: feat-1")).toBeInTheDocument();
      expect(screen.getByText("Feature: feat-2")).toBeInTheDocument();
    });
    const { toast } = await import("sonner");
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Probe plan #999 was not found.");
    });
    expect(screen.queryByText(/Probe Points \(/)).not.toBeInTheDocument();
  });
});

// ── Repository refresh loop (Issue #158) ────────────────────────────

describe("Repository Refresh Hub", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const baseGet = (status: Record<string, unknown>) => (path: string) => {
    if (path === "/repository") return Promise.resolve({
      id: 1, system_id: 1, repo_path: "/repos/alpha", include_patterns: [], exclude_patterns: [],
    });
    if (path === "/repository-candidates") return Promise.resolve([{ name: "alpha", path: "/repos/alpha" }]);
    if (path === "/repository/snapshots") return Promise.resolve([]);
    if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
    if (path === "/repository/status") return Promise.resolve(status);
    return Promise.resolve(null);
  };

  test("shows a stale banner and next steps when HEAD moved past the snapshot", async () => {
    mockApi.get.mockImplementation(baseGet({
      configured: true, repo_path: "/repos/alpha",
      current_head: "def5678000", head_error: null,
      working_tree_dirty: false, dirty_file_count: 0, dirty_sample: [],
      latest_snapshot: { id: 12, commit_sha: "abc1234000", status: "ready", created_at: 1 },
      latest_indexed_snapshot: null,
      understanding_snapshot_id: null, understanding_status: null,
      snapshot_stale: true, symbols_stale: false,
      next_actions: ["Repository HEAD changed; create a new snapshot before generating new analysis or patches."],
    }));

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByTestId("snapshot-stale-badge")).toBeInTheDocument();
    // The next-steps list echoes the server's actionable guidance verbatim.
    expect(
      within(hub).getByText(/create a new snapshot before generating new analysis or patches/i),
    ).toBeInTheDocument();
  });

  test("shows up to date when nothing is stale", async () => {
    mockApi.get.mockImplementation(baseGet({
      configured: true, repo_path: "/repos/alpha",
      current_head: "abc1234000", head_error: null,
      working_tree_dirty: false, dirty_file_count: 0, dirty_sample: [],
      latest_snapshot: { id: 12, commit_sha: "abc1234000", status: "ready", created_at: 1 },
      latest_indexed_snapshot: { id: 12, commit_sha: "abc1234000", status: "ready", created_at: 1 },
      understanding_snapshot_id: 12, understanding_status: "completed",
      snapshot_stale: false, symbols_stale: false, next_actions: [],
    }));

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByText("Up to date")).toBeInTheDocument();
  });
});

describe("Probe Patch HEAD-changed recovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("surfaces recovery guidance when HEAD moved past the patch commit", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({
        system_id: 1, is_mock: false,
        plans: [{ id: 10, feature_id: "feat-1", objective: "Observe", status: "proposed", created_at: "2024-01-01", probe_points: [] }],
      });
      if (path === "/repository/probe-patches") return Promise.resolve([{
        id: 20, plan_id: 10, system_id: 1, snapshot_id: 5,
        commit_sha: "abcdef1234567890", diff: "diff --git a/a.py b/a.py",
        worktree_path: null, skipped: [], status: "generated", error: null,
        cleanup_state: "removed", cleanup_error: null,
        apply_status: "not_applied", apply_error: null, applied_at: null,
        applied_by_user_id: null, validation_runs: [], created_at: "2024-01-01",
      }]);
      if (path === "/repository/status") return Promise.resolve({
        configured: true, repo_path: "/repos/alpha",
        current_head: "9999999999", head_error: null,
        working_tree_dirty: false, dirty_file_count: 0, dirty_sample: [],
        latest_snapshot: null, latest_indexed_snapshot: null,
        understanding_snapshot_id: null, understanding_status: null,
        snapshot_stale: true, symbols_stale: false, next_actions: [],
      });
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Feature: feat-1"));

    expect(await screen.findByTestId("patch-stale-badge")).toBeInTheDocument();
    const recovery = await screen.findByTestId("patch-recovery");
    expect(within(recovery).getByText(/git apply --check/)).toBeInTheDocument();
    expect(within(recovery).getByText(/cannot be applied after HEAD changed/i)).toBeInTheDocument();
  });
});

// ── Flow Explorer tests ─────────────────────────────────────────────

describe("Flow Explorer page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  function entrypointsResponse(overrides: Record<string, unknown> = {}) {
    return {
      system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
      total: 0, entrypoints: [], functions: [],
      counts: { api: 0, message_queue: 0, scheduled_job: 0, cli: 0, function: 0 },
      indexed_function_count: 0, has_backend_entrypoints: true, frameworks: [],
      diagnostics: [],
      ...overrides,
    };
  }

  const flowGraph = {
    system_id: 1,
    snapshot_id: 5,
    commit_sha: "abcdef1234567890",
    entrypoint: {
      entrypoint_type: "http_route", entrypoint_id: "POST:/documents/analyze",
      label: "POST /documents/analyze", path: "app.py", qualified_name: "analyze_document",
      line_start: 5, line_end: 11, component_id: null, route_method: "POST", route_path: "/documents/analyze",
      category: "api", framework: "fastapi", operation: "POST /documents/analyze",
      confidence: 1.0, evidence: [],
    },
    nodes: [
      {
        node_id: "app.py::analyze_document", node_type: "http_route", symbol_id: 1,
        qualified_name: "analyze_document", path: "app.py", line_start: 5, line_end: 11,
        component_id: null, probe_capabilities: ["input", "output", "error", "duration"],
        risk: "low", denylist_hit: null, evidence: [],
        boundary_kind: null, is_external: false, trace_count: 0, error_count: 0,
        evaluation_pass: 0, evaluation_fail: 0, observed: false,
        preview: {
          recommended_mode: "trace", captured_data: ["return value"], redaction: ["truncated"],
          replayability: "safe", estimated_event_volume: "unknown", side_effect_risk: "low",
          denylist_hit: null,
        },
      },
      {
        node_id: "app.py::parse_blocks", node_type: "function", symbol_id: 2,
        qualified_name: "parse_blocks", path: "app.py", line_start: 14, line_end: 15,
        component_id: null, probe_capabilities: ["input", "output", "error", "duration"],
        risk: "low", denylist_hit: null, evidence: [],
        boundary_kind: null, is_external: false, trace_count: 0, error_count: 0,
        evaluation_pass: 0, evaluation_fail: 0, observed: false,
        preview: {
          recommended_mode: "trace", captured_data: ["return value"], redaction: ["truncated"],
          replayability: "safe", estimated_event_volume: "unknown", side_effect_risk: "low",
          denylist_hit: null,
        },
      },
    ],
    edges: [
      {
        edge_id: "edge::app.py::analyze_document::app.py::parse_blocks::call::7",
        source_node_id: "app.py::analyze_document", target_node_id: "app.py::parse_blocks",
        edge_type: "call", confidence: 1.0, resolution: "resolved", callee_name: "parse_blocks",
        line: 7, evidence: [],
        preview: {
          recommended_mode: "trace", captured_data: ["arguments before parse_blocks()"],
          redaction: ["truncated"], replayability: "caution", estimated_event_volume: "unknown",
          side_effect_risk: "low", denylist_hit: null,
        },
      },
    ],
    candidate_paths: [
      {
        flow_id: "flow-1", title: "analyze_document → parse_blocks", summary: "",
        entrypoint_node_id: "app.py::analyze_document",
        node_ids: ["app.py::analyze_document", "app.py::parse_blocks"],
        node_count: 2, max_depth: 1, confidence: 1.0, unresolved_edge_count: 0,
        external_boundary_count: 0, observed_node_count: 0, unobserved_node_ids: [],
      },
    ],
    diagnostics: [],
    truncated: false,
  };

  test("builds graph and creates a manual plan from selected nodes", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve(entrypointsResponse({
          total: 1, entrypoints: [flowGraph.entrypoint],
        }));
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/repository/flow-graphs") return Promise.resolve(flowGraph);
      if (path === "/repository/probe-plans/from-flow") {
        return Promise.resolve({ id: 42, status: "proposed", probe_points: [] });
      }
      return Promise.resolve(null);
    });

    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    // Open the entrypoint -> builds the graph.
    const entrypointBtn = await screen.findByText("POST /documents/analyze");
    fireEvent.click(entrypointBtn);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/flow-graphs", {
        entrypoint_type: "http_route",
        entrypoint_id: "POST:/documents/analyze",
      });
    });

    // Select the parse_blocks node from the graph (the node label, not the
    // edge target label which shares the same text).
    const matches = await screen.findAllByText("parse_blocks");
    const nodeLabel = matches.find(el => el.className.includes("font-medium"));
    fireEvent.click(nodeLabel!);

    const createBtn = await screen.findByText("Create Probe Plan draft");
    await waitFor(() => expect(createBtn).not.toBeDisabled());
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/repository/probe-plans/from-flow",
        expect.objectContaining({
          entrypoint_type: "http_route",
          entrypoint_id: "POST:/documents/analyze",
          snapshot_id: 5,
          commit_sha: "abcdef1234567890",
          selections: [
            {
              target_type: "node", node_id: "app.py::parse_blocks",
              observation: "output", mode_preference: "trace",
            },
          ],
        }),
      );
    });
  });

  test("runtime overlay panel shows observed nodes and divergences", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve(entrypointsResponse({
          total: 1, entrypoints: [flowGraph.entrypoint],
        }));
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/repository/flow-graphs") return Promise.resolve(flowGraph);
      if (path === "/repository/flow-overlay") {
        return Promise.resolve({
          selection: { kind: "entity", entity_type: "order", entity_id: "o-1" },
          nodes: [
            { node_id: "app.py::analyze_document", component_id: "analyze", observable: true, observed: true, observation_count: 3, last_observed_at: 2 },
            { node_id: "app.py::parse_blocks", component_id: null, observable: false, observed: false, observation_count: 0, last_observed_at: null },
          ],
          edges: [],
          divergences: [{ source_component_id: "analyze", target_component_id: "refund", count: 1 }],
          observed_component_ids: ["analyze", "refund"],
          unmatched_component_ids: ["refund"],
          observed_trace_count: 3,
        });
      }
      return Promise.resolve(null);
    });

    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("POST /documents/analyze"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/flow-graphs", expect.any(Object));
    });

    const panel = await screen.findByTestId("flow-overlay-panel");
    fireEvent.change(within(panel).getByLabelText("overlay entity type"), { target: { value: "order" } });
    fireEvent.change(within(panel).getByLabelText("overlay entity id"), { target: { value: "o-1" } });
    fireEvent.click(within(panel).getByRole("button", { name: /Apply overlay/ }));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/repository/flow-overlay",
        expect.objectContaining({
          entrypoint_type: "http_route", entrypoint_id: "POST:/documents/analyze",
          selection: expect.objectContaining({ kind: "entity", entity_type: "order", entity_id: "o-1" }),
        }),
      );
    });
    expect(await within(panel).findByText(/observed \(3\)/)).toBeInTheDocument();
    expect(within(panel).getByText(/no probe/)).toBeInTheDocument();
    const div = within(panel).getByTestId("overlay-divergences");
    expect(within(div).getByText(/analyze → refund/)).toBeInTheDocument();
  });

  test("renders external boundary and observed overlay; boundary is not selectable", async () => {
    const graphWithBoundary = {
      ...flowGraph,
      nodes: [
        { ...flowGraph.nodes[0], observed: true, trace_count: 4, error_count: 1 },
        {
          node_id: "external::database::cursor", node_type: "external_io", symbol_id: null,
          qualified_name: "cursor.execute", path: "(external)", line_start: 0, line_end: 0,
          component_id: null, probe_capabilities: ["boundary"], risk: "medium",
          denylist_hit: null, evidence: [], boundary_kind: "database", is_external: true,
          trace_count: 0, error_count: 0, evaluation_pass: 0, evaluation_fail: 0, observed: false,
          preview: null,
        },
      ],
      edges: [{
        edge_id: "edge::app.py::analyze_document::external::database::cursor::database::8",
        source_node_id: "app.py::analyze_document", target_node_id: "external::database::cursor",
        edge_type: "database", confidence: 0.5, resolution: "inferred", callee_name: "execute",
        line: 8, evidence: [],
        preview: {
          recommended_mode: "trace", captured_data: ["arguments before execute()"],
          redaction: ["truncated"], replayability: "caution", estimated_event_volume: "unknown",
          side_effect_risk: "medium", denylist_hit: null,
        },
      }],
      candidate_paths: [{
        flow_id: "flow-1", title: "analyze_document → cursor.execute", summary: "",
        entrypoint_node_id: "app.py::analyze_document",
        node_ids: ["app.py::analyze_document", "external::database::cursor"],
        node_count: 2, max_depth: 1, confidence: 0.5, unresolved_edge_count: 0,
        external_boundary_count: 1, observed_node_count: 1, unobserved_node_ids: [],
      }],
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve(entrypointsResponse({
          total: 1, entrypoints: [flowGraph.entrypoint],
        }));
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/repository/flow-graphs") return Promise.resolve(graphWithBoundary);
      return Promise.resolve(null);
    });

    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("POST /documents/analyze"));

    // Boundary node renders with a DB badge and trace overlay is shown.
    const labels = await screen.findAllByText("cursor.execute");
    const nodeLabel = labels.find(el => el.className.includes("font-medium"));
    expect(screen.getByText("DB")).toBeInTheDocument();
    expect(screen.getByText(/4 trace/)).toBeInTheDocument();

    // Clicking the external boundary node must not enable plan creation.
    fireEvent.click(nodeLabel!);
    expect(screen.getByText("Create Probe Plan draft")).toBeDisabled();
    expect(mockApi.post).not.toHaveBeenCalledWith(
      "/repository/probe-plans/from-flow",
      expect.anything(),
    );

    // Selecting the call-boundary EDGE instead targets the in-repo caller and
    // pins snapshot/commit.
    const edgeBtn = screen.getByText("database/inferred").closest("button");
    fireEvent.click(edgeBtn!);
    const createBtn = screen.getByText("Create Probe Plan draft");
    await waitFor(() => expect(createBtn).not.toBeDisabled());
    fireEvent.click(createBtn);
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/repository/probe-plans/from-flow",
        expect.objectContaining({
          snapshot_id: 5,
          commit_sha: "abcdef1234567890",
          selections: [
            {
              target_type: "edge",
              edge_id: "edge::app.py::analyze_document::external::database::cursor::database::8",
              observation: "boundary", mode_preference: "trace",
            },
          ],
        }),
      );
    });
  });

  test("detects a stale-graph 409 and prompts a reload", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve(entrypointsResponse({
          total: 1, entrypoints: [flowGraph.entrypoint],
        }));
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/repository/flow-graphs") return Promise.resolve(flowGraph);
      if (path === "/repository/probe-plans/from-flow") {
        return Promise.reject(new ApiError(409, "Flow graph is stale"));
      }
      return Promise.resolve(null);
    });

    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("POST /documents/analyze"));
    const matches = await screen.findAllByText("parse_blocks");
    fireEvent.click(matches.find(el => el.className.includes("font-medium"))!);
    const createBtn = await screen.findByText("Create Probe Plan draft");
    await waitFor(() => expect(createBtn).not.toBeDisabled());
    fireEvent.click(createBtn);

    // The stale banner appears and offers a reload.
    expect(await screen.findByText("Reload graph")).toBeInTheDocument();
    expect(screen.getByText("Create Probe Plan draft")).toBeDisabled();
  });

  test("category filter requests a typed entrypoint listing and shows the count", async () => {
    const mqEntrypoint = {
      entrypoint_type: "message_queue",
      entrypoint_id: "message_queue:worker.py::analyze_task",
      label: "Celery: analyze_task", path: "worker.py", qualified_name: "analyze_task",
      line_start: 1, line_end: 3, component_id: null, route_method: null, route_path: null,
      category: "message_queue", framework: "celery", operation: "analyze_task",
      confidence: 0.9, evidence: [],
    };
    const calls: string[] = [];
    mockApi.get.mockImplementation((path: string) => {
      calls.push(path);
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve(entrypointsResponse({
          total: 2, entrypoints: [flowGraph.entrypoint, mqEntrypoint],
        }));
      }
      if (path === "/repository/flow-entrypoints?category=message_queue") {
        return Promise.resolve(entrypointsResponse({
          total: 2, entrypoints: [mqEntrypoint],
        }));
      }
      return Promise.resolve(null);
    });

    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    // Both kinds are listed initially: "2 of 2".
    expect(await screen.findByText("2 of 2 entrypoint(s)")).toBeInTheDocument();
    expect(await screen.findByText("Celery: analyze_task")).toBeInTheDocument();

    // Selecting the Message Queue category requests the typed listing and the
    // filtered subset is shown in full.
    fireEvent.click(screen.getByText("Message Queue"));
    await waitFor(() => {
      expect(calls).toContain("/repository/flow-entrypoints?category=message_queue");
    });
    expect(await screen.findByText("1 of 2 entrypoint(s)")).toBeInTheDocument();
  });

  function roleCard(overrides: Record<string, unknown> = {}) {
    return {
      entrypoint_type: "http_route", entrypoint_id: "POST:/documents/analyze",
      label: "POST /documents/analyze", category: "api", route_method: "POST",
      route_path: "/documents/analyze", operation: "POST /documents/analyze",
      framework: "fastapi", source: "deterministic", handler_resolved: true,
      classification: "classified", capability_key: "doc-analysis",
      capability_name: "Document Analysis", element_type: "core",
      role: "Analyzes uploaded documents", operation_kind: "analysis",
      probe_value: "validate graph shape", consumers: ["dashboard"],
      state_effects: ["database-read"], boundaries: ["database"],
      flows_through: ["parse_blocks"],
      provenance_kinds: ["source_authored", "structural"],
      drift_status: "partially_stale", drift_changed_anchors: 2,
      drift_total_anchors: 8, drift_review_recommended: true,
      review_needed: false, review_reason: null, node_id: 9,
      ...overrides,
    };
  }

  test("shows a classified API role card with provenance and freshness", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve(entrypointsResponse({
          total: 1, entrypoints: [flowGraph.entrypoint],
        }));
      }
      if (path === "/repository/api-role-cards") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, hierarchy_run: null,
          base_snapshot_id: 5, target_snapshot_id: 5, drift_available: true,
          cards: [roleCard()],
        });
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) =>
      path === "/repository/flow-graphs" ? Promise.resolve(flowGraph) : Promise.resolve(null));

    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("POST /documents/analyze"));

    expect(await screen.findByTestId("api-role-card")).toBeInTheDocument();
    expect(screen.getByText("Document Analysis")).toBeInTheDocument();
    expect(screen.getByText("Analyzes uploaded documents")).toBeInTheDocument();
    expect(screen.getByText("source-authored")).toBeInTheDocument();
    expect(screen.getByText("partially stale")).toBeInTheDocument();
    expect(screen.getByText(/2 of 8 source\s+anchors changed/)).toBeInTheDocument();
  });

  test("shows empty state for unclassified and review flag for LLM scan", async () => {
    const unclassified = {
      ...flowGraph.entrypoint,
      entrypoint_id: "GET:/raw", label: "GET /raw", route_method: "GET",
      route_path: "/raw", operation: "GET /raw", source: "reasoning_llm",
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve(entrypointsResponse({
          total: 1, entrypoints: [unclassified],
        }));
      }
      if (path === "/repository/api-role-cards") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, hierarchy_run: null,
          base_snapshot_id: null, target_snapshot_id: null, drift_available: false,
          cards: [roleCard({
            entrypoint_type: "http_route", entrypoint_id: "GET:/raw",
            label: "GET /raw", route_method: "GET", source: "reasoning_llm",
            classification: "unclassified", capability_key: null,
            capability_name: null, element_type: null, role: null,
            operation_kind: null, probe_value: null, consumers: [],
            state_effects: [], boundaries: [], flows_through: [],
            provenance_kinds: ["structural"], drift_status: null,
            drift_changed_anchors: 0, drift_total_anchors: 0,
            handler_resolved: false, review_needed: true,
            review_reason: "LLM-derived API definition without a resolved handler.",
            node_id: null,
          })],
        });
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue(flowGraph);

    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("GET /raw"));

    expect(await screen.findByTestId("api-role-card")).toBeInTheDocument();
    expect(screen.getByText(/No source-authored explanation yet/)).toBeInTheDocument();
    expect(
      screen.getByText(/LLM-derived API definition without a resolved handler/),
    ).toBeInTheDocument();
  });

  test("requests an explanation refresh proposal for a drifted card", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve(entrypointsResponse({
          total: 1, entrypoints: [flowGraph.entrypoint],
        }));
      }
      if (path === "/repository/api-role-cards") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, hierarchy_run: null,
          base_snapshot_id: 5, target_snapshot_id: 6, drift_available: true,
          cards: [roleCard()],
        });
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/repository/flow-graphs") return Promise.resolve(flowGraph);
      if (path === "/repository/explanation-refresh") {
        return Promise.resolve({
          system_id: 1, base_snapshot_id: 5, target_snapshot_id: 6,
          intelligence_run: null, status: "proposed", error: null,
          review_required: true,
          review_note: "This is a suggestion only. The target source repository remains the source of truth.",
          proposal: {
            id: 1, node_id: 9, node_type: "element", name: "analyze",
            entrypoint_type: "http_route", entrypoint_id: "POST:/documents/analyze",
            path: "src/api.py", qualified_name: "analyze", drift_status: "stale",
            drift_reason: "Changed source hashes: symbol.",
            changed_hashes: ["symbol"],
            old_explanation: "role: Analyzes uploaded documents",
            proposed_explanation: "Analyzes and caches uploaded documents",
            proposed_metadata: { role: "Analyzes uploaded documents", element_type: "core" },
            summary_of_changes: "Now caches results; clarify wording.",
            confidence: 0.8, captured_file_content_hash: null,
            captured_symbol_source_hash: null, captured_explanation_hash: null,
            current_file_content_hash: null, current_symbol_source_hash: null,
            current_explanation_hash: null, status: "proposed", is_mock: false,
            provider: "openai", model: "gpt-5", decision_method: "reasoning_llm",
            created_at: 1,
          },
        });
      }
      return Promise.resolve(null);
    });

    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("POST /documents/analyze"));
    fireEvent.click(await screen.findByTestId("request-refresh"));

    expect(await screen.findByTestId("refresh-proposal")).toBeInTheDocument();
    expect(screen.getByText(/suggestion only/)).toBeInTheDocument();
    expect(screen.getByText("Analyzes and caches uploaded documents")).toBeInTheDocument();
    expect(screen.getByText(/Now caches results/)).toBeInTheDocument();
  });
});

// ── Decision Workspace tests ────────────────────────────────────────

function setupWorkspaceMocks(overrides: { workspaces?: unknown[]; detail?: unknown; contextPack?: unknown } = {}) {
  const workspaces = overrides.workspaces ?? [
    { id: 1, system_id: 1, title: "Theme", focus: "", status: "active", summary: "", created_at: 1, updated_at: 1 },
  ];
  const detail = overrides.detail ?? {
    id: 1, system_id: 1, title: "Theme", focus: "", status: "active", summary: "",
    created_at: 1, updated_at: 1, messages: [], context_items: [], proposals: [],
  };
  const contextPack = overrides.contextPack ?? {
    system: { system_id: 1, name: "sys", environment: "production", purpose: "", target_users: "" },
    focus: null, repository: null, features: [], components: [], traces: [], evaluations: [],
    probe_plans: [], experiments: [], human_decisions: [], evidence: [], missing_information: [],
  };
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/workspaces") return Promise.resolve(workspaces);
    if (path === "/workspaces/1") return Promise.resolve(detail);
    if (path === "/workspaces/1/context-pack") return Promise.resolve(contextPack);
    return Promise.resolve(null);
  });
}

describe("Decision Workspace page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  test("lists workspaces and selects one to load its conversation", async () => {
    setupWorkspaceMocks();
    const { default: WorkspacesPage } = await import("@/pages/workspaces");
    render(<WorkspacesPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Theme")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Theme"));

    await waitFor(() => {
      expect(screen.getByText("No messages yet. Ask a question to start the dialogue.")).toBeInTheDocument();
    });
  });

  test("sends an agent turn and surfaces a structured failure without throwing", async () => {
    setupWorkspaceMocks();
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/workspaces/1/agent-turns") {
        return Promise.resolve({
          user_message: { id: 1, workspace_id: 1, role: "user", content: "Hi", context_metadata: {}, created_at: 1 },
          assistant_message: null,
          proposals: [],
          error: "no reasoning model configured",
        });
      }
      return Promise.resolve(null);
    });

    const { default: WorkspacesPage } = await import("@/pages/workspaces");
    render(<WorkspacesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Theme")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Theme"));

    const textarea = await screen.findByPlaceholderText("Ask about this theme, grounded only in the pinned context...");
    fireEvent.change(textarea, { target: { value: "What should we try?" } });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/workspaces/1/agent-turns", {
        message: "What should we try?",
        context_refs: [],
      });
    });
    await waitFor(() => {
      expect(screen.getByText(/no reasoning model configured/)).toBeInTheDocument();
    });
  });

  test("renders a proposal and sends accept with the typed reason", async () => {
    setupWorkspaceMocks({
      detail: {
        id: 1, system_id: 1, title: "Theme", focus: "", status: "active", summary: "",
        created_at: 1, updated_at: 1, messages: [], context_items: [],
        proposals: [{
          id: 5, workspace_id: 1, message_id: 1, proposal_type: "experiment_draft",
          title: "Try a shorter summary", body: { feature_id: "feat-1" }, status: "proposed",
          decisions: [], created_at: 1, updated_at: 1,
        }],
      },
    });
    mockApi.post.mockResolvedValue({ id: 5, status: "accepted", decisions: [] });

    const { default: WorkspacesPage } = await import("@/pages/workspaces");
    render(<WorkspacesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Theme")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Theme"));

    await waitFor(() => expect(screen.getByText("Try a shorter summary")).toBeInTheDocument());
    fireEvent.change(screen.getByPlaceholderText("Reason for this decision..."), { target: { value: "Looks promising" } });
    fireEvent.click(screen.getByText("Accept"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/workspaces/1/proposals/5/accept", { reason: "Looks promising" });
    });
  });

  test("creates an editable handoff draft for an accepted proposal", async () => {
    setupWorkspaceMocks({
      detail: {
        id: 1, system_id: 1, title: "Theme", focus: "", status: "active", summary: "",
        created_at: 1, updated_at: 1, messages: [], context_items: [],
        proposals: [{
          id: 5, workspace_id: 1, message_id: 1, proposal_type: "experiment_draft",
          title: "Compare variants",
          body: { feature_id: "feat-1", objective: "compare quality" },
          status: "accepted",
          decisions: [{
            id: 9, proposal_id: 5, decision: "accepted", reason: "try it",
            decided_by_user_id: 1, created_at: 1,
          }],
          created_at: 1, updated_at: 1,
        }],
      },
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/workspaces/1/proposals/5/draft") {
        return Promise.resolve({
          id: 7,
          workspace_id: 1,
          proposal_id: 5,
          system_id: 1,
          draft_type: "experiment_draft",
          target_screen: "experiments",
          payload: { feature_id: "feat-1", objective: "compare quality" },
          missing_fields: ["snapshot_id", "patch_text"],
          created_at: 1,
        });
      }
      return Promise.resolve(null);
    });

    const { default: WorkspacesPage } = await import("@/pages/workspaces");
    render(<WorkspacesPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Theme")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Theme"));
    fireEvent.click(await screen.findByText("Create Experiment draft"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/workspaces/1/proposals/5/draft");
    });
  });
});

// ── Capability Map tests (Issue #62) ────────────────────────────────

function provenance(overrides: Record<string, unknown> = {}) {
  return {
    provenance_kind: "source_authored", decision_method: "deterministic",
    path: "src/flow.py", qualified_name: "get_flow", start_line: 10, end_line: 20,
    file_content_hash: "f1", symbol_source_hash: "s1", explanation_hash: "e1",
    symbol_id: 5, entrypoint_id: 9, entrypoint_type: null, entrypoint_ref: null,
    feature_id: null, system_profile_draft_id: null, provider: "deterministic",
    model: "none", ...overrides,
  };
}

function emptyHierarchy() {
  return {
    system_id: 1, snapshot_id: 0, intelligence_run: null, purpose: null,
    capabilities: [], unclassified_elements: [], unattached_supporting: [],
    is_mock: false,
  };
}

describe("Capability Map page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("shows prerequisites and a generate action when no hierarchy exists", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/capability-hierarchy") return Promise.resolve(emptyHierarchy());
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue({
      ...emptyHierarchy(),
      intelligence_run: { id: 1, status: "completed", decision_method: "deterministic" },
    });

    const { default: CapabilityMapPage } = await import("@/pages/capability-map");
    render(<CapabilityMapPage />, { wrapper: createWrapper() });

    expect(await screen.findByText("No capability hierarchy yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("generate-hierarchy-empty"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/capability-hierarchy/generate");
    });
  });

  test("renders the hierarchy and links an entrypoint element to Flow Explorer", async () => {
    const hierarchy = {
      system_id: 1, snapshot_id: 5,
      intelligence_run: { id: 1, status: "completed", decision_method: "deterministic" },
      purpose: { id: 1, name: "Understand running systems", summary: "purpose summary", provenance: provenance() },
      capabilities: [{
        id: 2, capability_key: "doc-analysis", name: "Document Analysis",
        summary: "analysis capability", provenance: provenance(),
        elements: [{
          id: 3, name: "GET /flow", summary: "lists flows", element_role: "Lists available flows",
          operation_kind: "read", probe_value: null, classification: "classified",
          provenance: provenance({ entrypoint_type: "http_route", entrypoint_ref: "GET:/flow" }),
        }],
        supporting_elements: [{
          id: 4, name: "results table", summary: "", supporting_kind: "database",
          provenance: provenance({ provenance_kind: "structural" }),
        }],
      }],
      unclassified_elements: [], unattached_supporting: [], is_mock: false,
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/capability-hierarchy") return Promise.resolve(hierarchy);
      return Promise.resolve(null);
    });

    const { default: CapabilityMapPage } = await import("@/pages/capability-map");
    render(<CapabilityMapPage />, { wrapper: createWrapper() });

    // Tree shows purpose, capability, element, and boundary.
    expect(await screen.findByText("Understand running systems")).toBeInTheDocument();
    expect(screen.getByText("Document Analysis")).toBeInTheDocument();

    // Selecting the entrypoint-backed element exposes the Flow Explorer link
    // carrying the logical entrypoint through query params.
    fireEvent.click(screen.getByText("GET /flow"));
    const link = await screen.findByTestId("open-in-flow");
    expect(link).toHaveAttribute(
      "href",
      "/flow-explorer?entrypoint_type=http_route&entrypoint_id=GET%3A%2Fflow&capability=doc-analysis",
    );
    expect(screen.getByText("Lists available flows")).toBeInTheDocument();
  });

  test("shows Gaps / Probe Plans / Experiments for the selected capability with deep links (Issue #175)", async () => {
    const hierarchy = {
      system_id: 1, snapshot_id: 5,
      intelligence_run: { id: 1, status: "completed", decision_method: "deterministic" },
      purpose: null,
      capabilities: [{
        id: 2, capability_key: "doc-analysis", name: "Document Analysis",
        summary: "analysis capability", provenance: provenance(),
        elements: [],
        supporting_elements: [],
      }],
      unclassified_elements: [], unattached_supporting: [], is_mock: false,
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/capability-hierarchy") return Promise.resolve(hierarchy);
      if (path === "/repository/capabilities/doc-analysis/context") {
        return Promise.resolve({
          capability_key: "doc-analysis",
          gaps: [{
            gap_type: "undocumented", severity: "warning", title: "Missing docs for parser",
            node_name: null, notes: null, capability_key: "doc-analysis",
            doc_refs: [], symbol_refs: [], entrypoint_refs: [], code_refs: [],
            next_actions: [], source_id: null, source_key: null, issue_drafts: [],
          }],
          probe_plans: [{
            id: 42, feature_id: "doc-parsing", objective: "trace parsing",
            status: "approved", created_at: "1", updated_at: "1",
          }],
          experiments: [{
            id: 7, feature_id: "doc-parsing", objective: "compare candidate",
            status: "completed", human_decision: "adopted",
            human_decision_variant_key: "candidate-a", created_at: "1",
          }],
        });
      }
      return Promise.resolve(null);
    });

    const { default: CapabilityMapPage } = await import("@/pages/capability-map");
    render(<CapabilityMapPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("Document Analysis"));

    const gapLink = await screen.findByText("Missing docs for parser");
    expect(gapLink.closest("a")).toHaveAttribute("href", "/system-understanding");

    const planLink = await screen.findByTestId("capability-probe-plans");
    expect(within(planLink).getByText("doc-parsing")).toBeInTheDocument();
    expect(within(planLink).getByRole("link")).toHaveAttribute("href", "/probe-planner?plan=42&capability=doc-analysis");

    const expLink = await screen.findByTestId("capability-experiments");
    expect(within(expLink).getByText("adopted")).toBeInTheDocument();
    expect(within(expLink).getByRole("link")).toHaveAttribute("href", "/experiments?capability=doc-analysis");
  });

  test("does not show Gaps / Probe Plans / Experiments sections when the context has none", async () => {
    const hierarchy = {
      system_id: 1, snapshot_id: 5,
      intelligence_run: { id: 1, status: "completed", decision_method: "deterministic" },
      purpose: null,
      capabilities: [{
        id: 2, capability_key: "empty-cap", name: "Empty Capability",
        summary: "", provenance: provenance(),
        elements: [], supporting_elements: [],
      }],
      unclassified_elements: [], unattached_supporting: [], is_mock: false,
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/capability-hierarchy") return Promise.resolve(hierarchy);
      if (path === "/repository/capabilities/empty-cap/context") {
        return Promise.resolve({ capability_key: "empty-cap", gaps: [], probe_plans: [], experiments: [] });
      }
      return Promise.resolve(null);
    });

    const { default: CapabilityMapPage } = await import("@/pages/capability-map");
    render(<CapabilityMapPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("Empty Capability"));

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/repository/capabilities/empty-cap/context");
    });
    expect(screen.queryByTestId("capability-gaps")).not.toBeInTheDocument();
    expect(screen.queryByTestId("capability-probe-plans")).not.toBeInTheDocument();
    expect(screen.queryByTestId("capability-experiments")).not.toBeInTheDocument();
  });

  test("Related APIs links carry the capability key to Flow Explorer (Issue #176)", async () => {
    const hierarchy = {
      system_id: 1, snapshot_id: 5,
      intelligence_run: { id: 1, status: "completed", decision_method: "deterministic" },
      purpose: null,
      capabilities: [{
        id: 2, capability_key: "doc-analysis", name: "Document Analysis",
        summary: "analysis capability", provenance: provenance(),
        elements: [], supporting_elements: [],
      }],
      unclassified_elements: [], unattached_supporting: [], is_mock: false,
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/capability-hierarchy") return Promise.resolve(hierarchy);
      if (path === "/repository/api-role-cards") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, hierarchy_run: null,
          base_snapshot_id: 5, target_snapshot_id: 5, drift_available: true,
          cards: [{
            entrypoint_type: "http_route", entrypoint_id: "POST:/documents/analyze",
            label: "POST /documents/analyze", capability_key: "doc-analysis",
          }],
        });
      }
      return Promise.resolve(null);
    });

    const { default: CapabilityMapPage } = await import("@/pages/capability-map");
    render(<CapabilityMapPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("Document Analysis"));

    const apiLink = await screen.findByText("POST /documents/analyze");
    expect(apiLink.closest("a")).toHaveAttribute(
      "href",
      "/flow-explorer?entrypoint_type=http_route&entrypoint_id=POST%3A%2Fdocuments%2Fanalyze&capability=doc-analysis",
    );
  });
});

describe("Flow Explorer capability context (Issue #176)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const entrypoint = {
    entrypoint_type: "http_route", entrypoint_id: "POST:/documents/analyze",
    label: "POST /documents/analyze", path: "app.py", qualified_name: "analyze_document",
    line_start: 5, line_end: 11, component_id: null, route_method: "POST",
    route_path: "/documents/analyze", category: "api", framework: "fastapi",
    operation: "POST /documents/analyze", confidence: 1.0, evidence: [],
  };

  function mockEntrypointsAndGraph() {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
          total: 1, entrypoints: [entrypoint], functions: [],
          counts: { api: 1, message_queue: 0, scheduled_job: 0, cli: 0, function: 0 },
          indexed_function_count: 0, has_backend_entrypoints: true, frameworks: ["fastapi"],
          diagnostics: [],
        });
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) =>
      path === "/repository/flow-graphs"
        ? Promise.resolve({
            system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
            entrypoint, nodes: [], edges: [], candidate_paths: [],
            diagnostics: [], truncated: false,
          })
        : Promise.resolve(null));
  }

  function renderFlowExplorerAt(route: string) {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    return import("@/pages/flow-explorer").then(({ default: FlowExplorerPage }) =>
      render(
        <QueryClientProvider client={qc}>
          <MemoryRouter initialEntries={[route]}>
            <FlowExplorerPage />
          </MemoryRouter>
        </QueryClientProvider>,
      ),
    );
  }

  test("shows a back link to the capability when ?capability= is present", async () => {
    mockEntrypointsAndGraph();
    await renderFlowExplorerAt("/flow-explorer?capability=doc-analysis");

    const back = await screen.findByTestId("back-to-capability");
    expect(back).toHaveAttribute("href", "/capability-map?capability=doc-analysis");
  });

  test("does not show a back link without ?capability=", async () => {
    mockEntrypointsAndGraph();
    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(<FlowExplorerPage />, { wrapper: createWrapper() });

    await screen.findByText("Flow Explorer");
    expect(screen.queryByTestId("back-to-capability")).not.toBeInTheDocument();
  });

  test("carries ?capability= through to the created Probe Plan draft", async () => {
    mockEntrypointsAndGraph();
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/repository/flow-graphs") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
          entrypoint,
          nodes: [{
            node_id: "n1", node_type: "function", qualified_name: "analyze_document",
            path: "app.py", line_start: 5, line_end: 11, component_id: null,
            is_external: false, boundary_kind: null, risk: "low", denylist_hit: null,
            trace_count: 0, error_count: 0, observed: false,
            evaluation_pass: 0, evaluation_fail: 0,
            preview: {
              recommended_mode: "trace", side_effect_risk: "low",
              captured_data: [], redaction: [], replayability: "yes",
              estimated_event_volume: "low", denylist_hit: null,
            },
          }],
          edges: [],
          candidate_paths: [{
            flow_id: "f1", title: "Main path", node_count: 1, max_depth: 1,
            confidence: 1, external_boundary_count: 0, unresolved_edge_count: 0,
            node_ids: ["n1"], observed_node_count: 0, unobserved_node_ids: ["n1"],
          }],
          diagnostics: [], truncated: false,
        });
      }
      if (path === "/repository/probe-plans/from-flow") {
        return Promise.resolve({ id: 55, feature_id: "flow-derived", objective: "", probe_points: [] });
      }
      return Promise.resolve(null);
    });

    await renderFlowExplorerAt("/flow-explorer?capability=doc-analysis");

    fireEvent.click(await screen.findByText("POST /documents/analyze"));
    fireEvent.click(await screen.findByText("analyze_document"));
    fireEvent.click(await screen.findByText("Create Probe Plan draft"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/repository/probe-plans/from-flow",
        expect.any(Object),
      );
    });
  });
});

describe("Probe Planner capability back link (Issue #176)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("shows a back link to the capability when ?capability= is present", async () => {
    mockTwoPlans();
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/probe-planner?capability=doc-analysis"]}>
          <ProbePlannerPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const back = await screen.findByTestId("back-to-capability");
    expect(back).toHaveAttribute("href", "/capability-map?capability=doc-analysis");
  });

  test("does not show a back link without ?capability=", async () => {
    mockTwoPlans();
    await renderProbePlannerAt("/probe-planner");

    await screen.findByText("Feature: feat-1");
    expect(screen.queryByTestId("back-to-capability")).not.toBeInTheDocument();
  });

  test("carries the capability context through to Experiments", async () => {
    mockTwoPlans();
    await renderProbePlannerAt("/probe-planner?plan=11&capability=doc-analysis");

    await screen.findByText("Probe Points (0)");
    const link = screen.getByTestId("open-experiments-with-capability");
    expect(link).toHaveAttribute("href", "/experiments?capability=doc-analysis");
  });
});

// ── Interview dashboard tests (Issue #72) ───────────────────────────

function interviewSession(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    system_id: 1,
    snapshot_id: 42,
    title: "System interview",
    focus: "Review metadata",
    status: "open",
    stage: "proposal_generation",
    current_understanding: null,
    gap_analysis: null,
    open_questions: null,
    user_intent: null,
    last_error: null,
    understanding_confirmed_at: 3,
    understanding_confirmed_by: "admin",
    materialization_diff: null,
    materialization_ref: null,
    materialized_at: null,
    created_at: 1,
    updated_at: 2,
    ...overrides,
  };
}

function understandingItem(name: string) {
  return {
    name,
    summary: `${name} summary`,
    confidence: { level: "likely", reason: "docs" },
    evidence: [],
    why_core: "",
    related_docs: [],
    related_apis: [],
    children: [],
  };
}

function interviewProposal() {
  return {
    id: 9,
    session_id: 7,
    system_id: 1,
    snapshot_id: 42,
    message_id: 3,
    intelligence_run_id: 4,
    symbol_id: 11,
    path: "src/summarize.py",
    qualified_name: "summarize.summarize_text",
    metadata: {
      role: "Summarize free text",
      capability: "summarization",
      system_purpose: "Document workflow",
      probe_value: "Validate latency",
      element_type: "core",
      operation_kind: "analysis",
      consumers: ["api"],
      state_effects: ["none"],
    },
    probe_plan: {
      feature_id: "summarization",
      objective: "Trace summarizer",
      reason: "Low risk function",
      recommended_mode: "trace",
      side_effect_risk: "low",
      replayability: "safe",
    },
    decision_method: "reasoning_llm",
    approval_state: "proposed",
    is_mock: true,
    intelligence_run: {
      id: 4,
      system_id: 1,
      snapshot_id: 42,
      run_type: "interview_dialogue",
      provider: "mock",
      model: "mock-reasoner",
      prompt_version: "interview-v1",
      schema_version: "1",
      decision_method: "reasoning_llm",
      status: "completed",
      error_details: null,
      is_mock: true,
      started_at: "1",
      completed_at: "2",
    },
    created_at: 1,
    updated_at: 1,
  };
}

function mockInterviewApi(options: {
  approvedCount?: number;
  session?: Record<string, unknown>;
  proposals?: unknown[];
  understandingDiff?: Record<string, unknown>;
  qaList?: Record<string, unknown>;
} = {}) {
  const session = interviewSession(options.session ?? {});
  const proposal = interviewProposal();
  const proposals = options.proposals ?? [proposal];
  const approvedCount = options.approvedCount ?? 0;
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/interview/sessions/7/understanding-diff") {
      return Promise.resolve(options.understandingDiff ?? null);
    }
    if (path === "/interview/sessions/7/qa") {
      return Promise.resolve(options.qaList ?? null);
    }
    if (path === "/repository/snapshots/latest") {
      return Promise.resolve({
        id: 42,
        system_id: 1,
        repo_path: "/repo",
        commit_sha: "abcdef1234567890",
        status: "ready",
        file_count: 1,
        total_size: 10,
        indexed_size: 10,
        metadata_only_count: 0,
        warnings: [],
        error_summary: null,
        created_at: "1",
        completed_at: "2",
        files: [],
      });
    }
    if (path === "/interview/sessions") return Promise.resolve([session]);
    if (path === "/interview/sessions/7") {
      return Promise.resolve({
        ...session,
        messages: [
          { id: 1, session_id: 7, role: "assistant", content: "Found unclassified symbols.", intelligence_run_id: 4, created_at: 1 },
        ],
        proposals,
      });
    }
    if (path === "/interview/sessions/7/context-pack") {
      return Promise.resolve({
        system_id: 1,
        snapshot_id: 42,
        total_symbols: 1,
        total_entrypoints: 1,
        classified_count: 0,
        unclassified_count: 1,
        budget_max_chars: 1000,
        budget_used_chars: 200,
        truncated: false,
        symbols: [{
          symbol_id: 11,
          path: "src/summarize.py",
          qualified_name: "summarize.summarize_text",
          kind: "function",
          start_line: 1,
          end_line: 3,
          classification: "unclassified",
          has_metadata: false,
          element_type: null,
          role: null,
          capability: null,
          operation_kind: null,
          probe_value: null,
          evidence: { snapshot_id: 42, path: "src/summarize.py", qualified_name: "summarize.summarize_text", start_line: 1, end_line: 3 },
        }],
        entrypoints: [],
        omission_notes: [],
      });
    }
    if (path === "/interview/sessions/7/approved-set") {
      return Promise.resolve({
        session_id: 7,
        system_id: 1,
        snapshot_id: 42,
        items: approvedCount ? [{
          proposal_id: 9,
          path: "src/summarize.py",
          qualified_name: "summarize.summarize_text",
          symbol_id: 11,
          metadata: proposal.metadata,
          probe_plan: proposal.probe_plan,
          decision: "approved",
          decision_id: 12,
          actor: "admin",
          decided_at: 3,
        }] : [],
        total_proposals: 1,
        approved_count: approvedCount,
        rejected_count: 0,
        pending_count: approvedCount ? 0 : 1,
      });
    }
    return Promise.resolve(null);
  });
  return { session, proposal };
}

describe("Interview page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("preserves diagnostic focus params while auto-selecting an interview session", async () => {
    mockInterviewApi();
    const baseGet = mockApi.get.getMockImplementation();
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-diagnostics") {
        return Promise.resolve({
          system_id: 1,
          generated_at: 1750000000,
          overall_severity: "warning",
          severity_counts: { ok: 0, warning: 1, error: 0, blocked: 0, unknown: 0 },
          checks: [{
            check_id: "system_purpose",
            category: "understanding",
            title: "System Purpose の定義",
            severity: "warning",
            detail: "System Purpose が未定義です。",
            impact: "probe 設計の前提情報が欠けています。",
            remediation: "Interview で System Purpose を定義・確認してください。",
            related_env: [],
            related_paths: [],
            related_pages: ["/system-understanding", "/interview"],
            related_pipeline_steps: ["capability_hierarchy_ready"],
            last_observed_error: null,
            decision_method: "deterministic",
            fix_kind: "navigate",
            fix_page: "/interview",
            fix_anchor: "interview-purpose",
          }],
        });
      }
      return baseGet?.(path) ?? Promise.resolve(null);
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?diagnostic=system_purpose&fix=interview-purpose"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const callout = await screen.findByTestId("diagnostic-callout-interview-purpose");
    expect(callout.textContent).toContain("System Purpose の定義");
    expect(callout.textContent).toContain("Interview で System Purpose");
  });

  test("renders mock reasoning provenance and wires proposal decisions", async () => {
    mockInterviewApi();
    mockApi.post.mockResolvedValue({ id: 1, decision: "approved", decision_method: "manual" });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("summarize.summarize_text")).toBeInTheDocument();
    expect(screen.getByText("mock")).toBeInTheDocument();
    expect(screen.getByText("reasoning_llm")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /承認/ }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/proposals/9/approve",
        { actor: "admin" },
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /却下/ }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/proposals/9/reject",
        { actor: "admin" },
      );
    });
  });

  test("runtime reality check trigger is reachable with zero Q&A rows (Issue #135)", async () => {
    // Approved elements exist but no questions yet — the most useful moment
    // for the first reality check. The trigger must not be hidden behind an
    // empty Q&A list.
    mockInterviewApi({
      approvedCount: 1,
      qaList: {
        session_id: 7,
        system_id: 1,
        items: [],
        open_count: 0,
        high_priority_open_count: 0,
        answers_revised_at: null,
      },
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const button = await screen.findByTestId("run-runtime-reality-check");
    expect(button).toBeEnabled();
  });

  test("shows understanding diff summary and expandable detail (Issue #136)", async () => {
    mockInterviewApi({
      understandingDiff: {
        session_id: 7,
        system_id: 1,
        from_revision_id: 1,
        to_revision_id: 2,
        has_previous: true,
        sections: [
          {
            section: "core_capabilities",
            added: ["Classify"],
            removed: [],
            confidence_changed: [{ name: "Summarize", before: "uncertain", after: "confirmed" }],
            summary_changed: [],
          },
          {
            section: "system_purpose",
            added: [],
            removed: [],
            confidence_changed: [],
            summary_changed: [],
          },
        ],
      },
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const panel = await screen.findByTestId("understanding-diff-panel");
    expect(within(panel).getByTestId("understanding-diff-summary")).toHaveTextContent(
      "追加 1 / 削除 0 / 確信度変化 1",
    );

    expect(screen.queryByTestId("understanding-diff-detail")).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByTestId("toggle-understanding-diff-detail"));
    const detail = await screen.findByTestId("understanding-diff-detail");
    expect(within(detail).getByText("+ Classify")).toBeInTheDocument();
    expect(within(detail).getByText(/確信度: uncertain → confirmed/)).toBeInTheDocument();
  });

  test("shows 'no comparison target' when the session has no previous revision (Issue #136)", async () => {
    mockInterviewApi({
      understandingDiff: {
        session_id: 7,
        system_id: 1,
        from_revision_id: null,
        to_revision_id: 1,
        has_previous: false,
        sections: [],
      },
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const panel = await screen.findByTestId("understanding-diff-panel");
    expect(within(panel).getByText("比較対象となる前のリビジョンがありません(初回の理解構築です)。")).toBeInTheDocument();
  });

  test("shows 'your answer correction was reflected' after rebuilding from a revised answer (Issue #136)", async () => {
    mockInterviewApi({
      session: { answers_revised_at: 123 },
      understandingDiff: {
        session_id: 7,
        system_id: 1,
        from_revision_id: 1,
        to_revision_id: 2,
        has_previous: true,
        sections: [],
      },
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/7/update-understanding") {
        return Promise.resolve(interviewSession({ answers_revised_at: null, last_error: null }));
      }
      return Promise.resolve({ id: 1, decision: "approved", decision_method: "manual" });
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const refreshButton = await screen.findByRole("button", { name: /理解を更新/ });
    fireEvent.click(refreshButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/7/update-understanding", {});
    });
    expect(await screen.findByTestId("answer-revision-reflected-banner")).toBeInTheDocument();
  });

  test("shows all evidence read for a turn, even when uncited (Issue #137)", async () => {
    mockInterviewApi();
    mockApi.post.mockResolvedValue({
      assistant_message: "読みました。",
      proposals: [],
      next_questions: [],
      intelligence_run: null,
      error: null,
      stage: "proposal_generation",
      current_understanding: null,
      gap_analysis: null,
      open_questions_structured: [],
      evidence_run: { id: 99, run_type: "interview_evidence_selection" },
      evidence_used: [],
      evidence_reads: [
        {
          id: 1, system_id: 1, intelligence_run_id: 99,
          path: "src/summarize.py", start_line: 1, end_line: 10,
          char_count: 120, truncated: false, created_at: 1,
        },
        {
          id: 2, system_id: 1, intelligence_run_id: 99,
          path: "src/classifier.py", start_line: 1, end_line: 3,
          char_count: 40, truncated: true, created_at: 1,
        },
      ],
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const textarea = await screen.findByPlaceholderText(
      /上の質問への回答や修正点を入力してください。|提案の対象範囲や重視したい観点があれば入力してください。/,
    );
    fireEvent.change(textarea, { target: { value: "詳細を教えてください" } });
    fireEvent.click(screen.getByRole("button", { name: /送信/ }));

    const panel = await screen.findByTestId("evidence-reads-panel");
    expect(panel.textContent).toContain("src/summarize.py:1-10");
    expect(panel.textContent).toContain("120 chars");
    expect(panel.textContent).toContain("src/classifier.py:1-3");
    expect(panel.textContent).toContain("truncated");
  });

  test("proposal narrowing: shows the model's narrowing question and re-requests proposals on answer", async () => {
    // A generate_proposals turn returned zero proposals: the narrowing
    // question persisted into open_questions must replace the fixed
    // "ready for proposals" prompt, and answering it consumes the qa_id
    // while re-requesting proposal generation.
    mockInterviewApi({
      proposals: [],
      session: {
        open_questions: [{
          question: "計測対象は要約フローで正しいですか?",
          category: "followup",
          priority: "medium",
          hypothesis: "summarize.summarize_text が主要なプローブ候補",
          qa_id: 21,
        }],
      },
    });
    mockApi.post.mockResolvedValue({
      assistant_message: "了解しました。",
      proposals: [],
      proposals_requested: true,
      next_questions: [],
      intelligence_run: null,
      error: null,
      stage: "proposal_generation",
      current_understanding: null,
      gap_analysis: null,
      open_questions_structured: [],
      created_qa_ids: [],
      evidence_run: null,
      evidence_used: [],
      evidence_reads: [],
      evidence_refs_dropped: 0,
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const card = await screen.findByTestId("focused-question");
    expect(within(card).getByText("計測対象は要約フローで正しいですか?")).toBeInTheDocument();
    expect(within(card).getByTestId("question-hypothesis")).toHaveTextContent(
      "summarize.summarize_text が主要なプローブ候補",
    );
    // 「わからない」は提案ステージの絞り込みでも使える(Issue #142 と同じ経路)。
    expect(within(card).getByTestId("quick-answer-unknown")).toBeInTheDocument();
    // 次のアクション文言が絞り込み継続を案内する。
    expect((await screen.findByTestId("next-action")).textContent).toContain(
      "提案に必要な情報がまだ不足しています",
    );

    // 仮説付き質問への「はい」は、qa_id を消費しつつ提案生成を再依頼する。
    fireEvent.click(within(card).getByTestId("quick-answer-yes"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/dialogue-turn",
        expect.objectContaining({
          generate_proposals: true,
          answered_qa_id: 21,
          answered_question: "計測対象は要約フローで正しいですか?",
        }),
      );
    });
  });

  test("sends edits through the validated edit endpoint and materializes a diff", async () => {
    mockInterviewApi({ approvedCount: 1 });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions/7/materialize") {
        return Promise.resolve({
          session_id: 7,
          system_id: 1,
          snapshot_id: 42,
          diff: "diff --git a/src/summarize.py b/src/summarize.py",
          files_changed: 1,
          items_materialized: 1,
          skipped: [],
          materialized_at: 3,
          error: null,
        });
      }
      return Promise.resolve({ id: 5, decision: "edited", decision_method: "manual" });
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("提案レビュー")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /編集/ }));
    fireEvent.click(await screen.findByRole("button", { name: /修正を保存して承認/ }));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/proposals/9/edit",
        expect.objectContaining({
          actor: "admin",
          metadata: expect.objectContaining({ role: "Summarize free text" }),
          probe_plan: expect.objectContaining({ recommended_mode: "trace" }),
        }),
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /差分を生成/ }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/7/materialize", {});
    });
    expect(await screen.findByText(/diff --git/)).toBeInTheDocument();
  });

  test("shows confirmation question and hides proposal panels before proposal stage (Issue #123)", async () => {
    mockInterviewApi({
      session: {
        stage: "purpose_confirmation",
        current_understanding: {
          system_purpose: [understandingItem("Runtime probe platform")],
          core_capabilities: [understandingItem("Trace ingestion")],
          capability_elements: [],
          supporting_elements: [],
          api_boundaries: [],
          probe_flow_candidates: [],
        },
      },
      proposals: [],
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // The first user-facing step is confirmation of the inferred summary.
    const question = await screen.findByTestId("focused-question");
    expect(question.textContent).toContain("正しいですか");
    expect(question.textContent).toContain("Runtime probe platform");

    // The next required action is always explicit.
    expect(screen.getByTestId("next-action")).toBeInTheDocument();

    // Proposal and diff panels stay hidden until proposal_generation.
    expect(screen.queryByText("提案レビュー")).toBeNull();
    expect(screen.queryByText("レビュー用差分")).toBeNull();

    // No manual stage-advance control.
    expect(screen.queryByRole("button", { name: /Advance/i })).toBeNull();
  });

  test("renders hypothesis-first question with evidence and quick answers (Issue #128)", async () => {
    const questionText = "トレース取り込みは control-server の責務という理解で正しいですか?";
    mockInterviewApi({
      session: {
        stage: "capability_confirmation",
        current_understanding: {
          system_purpose: [understandingItem("Runtime probe platform")],
          core_capabilities: [],
          capability_elements: [],
          supporting_elements: [],
          api_boundaries: [],
          probe_flow_candidates: [],
        },
        open_questions: [
          {
            question: questionText,
            category: "capability",
            priority: "high",
            hypothesis: "トレース取り込みは control-server が担う",
            evidence_refs: [
              { path: "apps/control-server/app/main.py", start_line: 10, end_line: 42 },
            ],
            answer_options: [],
            qa_id: 42,
          },
        ],
      },
      proposals: [],
    });
    mockApi.post.mockResolvedValue({
      assistant_message: "了解しました。",
      proposals: [],
      next_questions: [],
      intelligence_run: null,
      error: null,
      stage: "element_classification",
      current_understanding: null,
      gap_analysis: null,
      open_questions_structured: [],
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // The question card shows the model's hypothesis and its snapshot-grounded
    // evidence, so the developer sees why this is being asked.
    const question = await screen.findByTestId("focused-question");
    expect(question.textContent).toContain(questionText);
    expect(screen.getByTestId("question-hypothesis").textContent).toContain(
      "control-server が担う",
    );
    expect(screen.getByTestId("question-evidence").textContent).toContain(
      "apps/control-server/app/main.py:10-42",
    );

    // 「はい」 sends a canned confirmation through the normal dialogue turn
    // and consumes the focused open question — by qa_id (Issue #129), with
    // the text kept for legacy sessions. It is dialogue input, not an
    // approval action.
    fireEvent.click(screen.getByTestId("quick-answer-yes"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/dialogue-turn",
        expect.objectContaining({
          user_message: "はい、その理解で正しいです。",
          answered_question: questionText,
          answered_qa_id: 42,
        }),
      );
    });
  });

  test("falls back to a zero-base interview when understanding cannot be built (Issue #123)", async () => {
    mockInterviewApi({
      session: {
        stage: "understanding_initialized",
        current_understanding: null,
        last_error: "reasoning model is not configured",
        understanding_confirmed_at: null,
        understanding_confirmed_by: null,
      },
      proposals: [],
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const notice = await screen.findByTestId("zero-base-notice");
    expect(notice.textContent).toContain("ゼロベース");
    expect(notice.textContent).toContain("reasoning model is not configured");

    // The zero-base interview asks one focused question at a time,
    // starting with the target goal.
    const question = screen.getByTestId("focused-question");
    expect(question.textContent).toContain("達成したい目標");
  });

  test("zero-base flow requires explicit confirmation before proposals unlock (Issue #123)", async () => {
    // Reached proposal_generation through zero-base answers, but the user
    // has not confirmed the gathered context yet.
    mockInterviewApi({
      session: {
        stage: "proposal_generation",
        current_understanding: null,
        last_error: "reasoning model is not configured",
        understanding_confirmed_at: null,
        understanding_confirmed_by: null,
      },
      proposals: [],
    });
    mockApi.post.mockResolvedValue(interviewSession({
      stage: "proposal_generation",
      understanding_confirmed_at: 9,
      understanding_confirmed_by: "admin",
    }));

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Proposal panels stay hidden until the manual confirmation.
    const confirmButton = await screen.findByTestId("confirm-understanding");
    expect(screen.queryByText("提案レビュー")).toBeNull();

    fireEvent.click(confirmButton);
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/confirm-understanding",
        { actor: "admin" },
      );
    });
  });

  test("structured understanding can be explicitly confirmed from the interview page", async () => {
    mockInterviewApi({
      session: {
        stage: "proposal_generation",
        current_understanding: {
          system_purpose: [understandingItem("Runtime probe platform")],
          core_capabilities: [understandingItem("Trace ingestion")],
          capability_elements: [],
          supporting_elements: [],
          api_boundaries: [],
          probe_flow_candidates: [],
        },
        understanding_confirmed_at: null,
        understanding_confirmed_by: null,
      },
      proposals: [],
    });
    mockApi.post.mockResolvedValue(interviewSession({
      stage: "proposal_generation",
      current_understanding: {
        system_purpose: [understandingItem("Runtime probe platform")],
        core_capabilities: [understandingItem("Trace ingestion")],
        capability_elements: [],
        supporting_elements: [],
        api_boundaries: [],
        probe_flow_candidates: [],
      },
      understanding_confirmed_at: 9,
      understanding_confirmed_by: "admin",
    }));

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const confirmButton = await screen.findByTestId("confirm-understanding");
    expect(confirmButton).toHaveTextContent("この理解を確認済みにする");
    expect(screen.getByTestId("next-action")).toHaveTextContent("この理解を確認済みにする");

    fireEvent.click(confirmButton);
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/confirm-understanding",
        { actor: "admin" },
      );
    });
  });

  test("answering a gap question passes it to the server for consumption (Issue #123)", async () => {
    mockInterviewApi({
      session: {
        stage: "capability_confirmation",
        current_understanding: {
          system_purpose: [understandingItem("Runtime probe platform")],
          core_capabilities: [],
          capability_elements: [],
          supporting_elements: [],
          api_boundaries: [],
          probe_flow_candidates: [],
        },
        open_questions: [
          { question: "認証はどの層で行いますか?", category: "boundary", priority: "high" },
          { question: "保持期間は?", category: "capability", priority: "low" },
        ],
        understanding_confirmed_at: null,
        understanding_confirmed_by: null,
      },
      proposals: [],
    });
    mockApi.post.mockResolvedValue({
      assistant_message: "了解しました。", proposals: [], next_questions: [],
      intelligence_run: null, error: null, stage: "element_classification",
      current_understanding: null, gap_analysis: null, open_questions_structured: null,
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview?session=7"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // The highest-priority open question is the focused question.
    const question = await screen.findByTestId("focused-question");
    expect(question.textContent).toContain("認証はどの層で行いますか?");

    fireEvent.change(screen.getByPlaceholderText(/上の質問への回答/), {
      target: { value: "APIゲートウェイ層で認証します" },
    });
    fireEvent.click(screen.getByRole("button", { name: /回答を送信/ }));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/dialogue-turn",
        expect.objectContaining({
          user_message: "APIゲートウェイ層で認証します",
          answered_question: "認証はどの層で行いますか?",
          generate_proposals: false,
        }),
      );
    });
  });

  test("Start Interview builds understanding automatically (Issue #123)", async () => {
    mockInterviewApi();
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/snapshots/latest") {
        return Promise.resolve({
          id: 42, system_id: 1, repo_path: "/repo", commit_sha: "abcdef1234567890",
          status: "ready", file_count: 1, total_size: 10, indexed_size: 10,
          metadata_only_count: 0, warnings: [], error_summary: null,
          created_at: "1", completed_at: "2", files: [],
        });
      }
      if (path === "/interview/sessions") return Promise.resolve([]);
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) => {
      if (path === "/interview/sessions") {
        return Promise.resolve(interviewSession({ id: 8, stage: "understanding_initialized" }));
      }
      if (path === "/interview/sessions/8/update-understanding") {
        return Promise.resolve(interviewSession({ id: 8, stage: "purpose_confirmation" }));
      }
      return Promise.resolve(null);
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: InterviewPage } = await import("@/pages/interview");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/interview"]}>
          <InterviewPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const startButtons = await screen.findAllByRole("button", { name: /インタビューを開始/ });
    await waitFor(() => expect(startButtons[0]).not.toBeDisabled());
    fireEvent.click(startButtons[0]);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions",
        expect.objectContaining({ snapshot_id: 42 }),
      );
    });
    // Understanding is built automatically for the created session.
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/8/update-understanding",
        {},
      );
    });
  });
});

describe("Flow Explorer auto-select from URL (Issue #62)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("opens the entrypoint named in query params and builds its graph", async () => {
    const entrypoint = {
      entrypoint_type: "http_route", entrypoint_id: "POST:/documents/analyze",
      label: "POST /documents/analyze", path: "app.py", qualified_name: "analyze_document",
      line_start: 5, line_end: 11, component_id: null, route_method: "POST",
      route_path: "/documents/analyze", category: "api", framework: "fastapi",
      operation: "POST /documents/analyze", confidence: 1.0, evidence: [],
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/flow-entrypoints") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
          total: 1, entrypoints: [entrypoint], functions: [],
          counts: { api: 1, message_queue: 0, scheduled_job: 0, cli: 0, function: 0 },
          indexed_function_count: 0, has_backend_entrypoints: true, frameworks: ["fastapi"],
          diagnostics: [],
        });
      }
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) =>
      path === "/repository/flow-graphs"
        ? Promise.resolve({
            system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
            entrypoint, nodes: [], edges: [], candidate_paths: [],
            diagnostics: [], truncated: false,
          })
        : Promise.resolve(null));

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: FlowExplorerPage } = await import("@/pages/flow-explorer");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[
          "/flow-explorer?entrypoint_type=http_route&entrypoint_id=POST:/documents/analyze",
        ]}>
          <FlowExplorerPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/flow-graphs", {
        entrypoint_type: "http_route",
        entrypoint_id: "POST:/documents/analyze",
      });
    });
  });
});

// ── Context Header tests (Issue #178) ───────────────────────────────

// ── Sidebar navigation ───────────────────────────────────────────────

describe("Sidebar navigation grouping (Issue #179)", () => {
  beforeEach(() => {
    mockSystemId = 1;
  });

  test("renders Hub / Detail views / Other headings and every existing route", async () => {
    const { Sidebar } = await import("@/components/layout/sidebar");
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("sidebar-group-hub")).toBeTruthy();
    expect(screen.getByTestId("sidebar-group-detail-views")).toBeTruthy();
    expect(screen.getByTestId("sidebar-group-other")).toBeTruthy();

    const nav = screen.getByTestId("sidebar-nav");
    // Existing routes/URLs are unchanged — every prior nav item is still present.
    for (const label of [
      "Overview", "System Understanding", "Repository", "Capability Map", "Feature Map",
      "Flow Explorer", "Trace Lineage", "Trace Analyzers", "Probe Planner", "Interview",
      "Experiments", "Connect SDK", "Generate", "Components", "Decision Workspace", "Settings",
    ]) {
      expect(within(nav).getByText(label)).toBeTruthy();
    }

    // System Understanding is grouped under Hub, not Detail views.
    expect(within(screen.getByTestId("sidebar-group-hub")).getByText("System Understanding")).toBeTruthy();
    expect(within(screen.getByTestId("sidebar-group-detail-views")).getByText("Flow Explorer")).toBeTruthy();

    // Issue #199: Interview sits directly after Capability Map in Detail views.
    const detailLabels = within(screen.getByTestId("sidebar-group-detail-views"))
      .getAllByRole("link")
      .map((el) => el.textContent);
    const capIdx = detailLabels.indexOf("Capability Map");
    expect(detailLabels[capIdx + 1]).toBe("Interview");
  });
});

describe("Context Header", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockSystems = [{ id: 1, name: "probe-agent" }];
  });

  function renderWithParams(route: string) {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    return import("@/components/layout/context-header").then(({ ContextHeader }) =>
      render(
        <QueryClientProvider client={qc}>
          <MemoryRouter initialEntries={[route]}>
            <ContextHeader />
          </MemoryRouter>
        </QueryClientProvider>,
      ),
    );
  }

  test("shows system, snapshot, capability, entrypoint and status when available", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/status") {
        return Promise.resolve({
          configured: true, repo_path: "/repos/a", current_head: "def5678000",
          head_error: null, working_tree_dirty: false, dirty_file_count: 0,
          dirty_sample: [], latest_snapshot: { id: 5, commit_sha: "abc1234567", status: "ready" },
          latest_indexed_snapshot: null,
          understanding_snapshot_id: null, understanding_status: null,
          snapshot_stale: false, symbols_stale: false, next_actions: [],
        });
      }
      if (path === "/repository/system-understanding") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abc1234567",
          pipeline: [
            { step: "symbols_indexed", status: "complete", detail: null },
            { step: "entrypoints_discovered", status: "complete", detail: null },
          ],
          purpose: null, capabilities: [], entrypoints: [], major_symbols: [],
          gaps: [{}, {}, {}], gap_summary: [], metadata_coverage: null,
          next_actions: [],
        });
      }
      return Promise.resolve(null);
    });

    await renderWithParams(
      "/flow-explorer?capability=doc-analysis&entrypoint_type=http_route&entrypoint_id=GET%3A%2Fflow",
    );

    expect(await screen.findByTestId("context-header")).toBeInTheDocument();
    expect(screen.getByTestId("context-header-system")).toHaveTextContent("probe-agent");
    expect(screen.getByTestId("context-header-snapshot")).toHaveTextContent("abc12345");
    expect(screen.getByTestId("context-header-snapshot")).not.toHaveTextContent("def56780");
    expect(screen.getByTestId("context-header-capability")).toHaveTextContent("doc-analysis");
    expect(screen.getByTestId("context-header-entrypoint")).toHaveTextContent("http_route: GET:/flow");
    expect(screen.getByTestId("context-header-status")).toHaveTextContent(
      "symbols indexed, entrypoints discovered, 3 gaps",
    );
  });

  test("omits missing fields instead of showing empty labels", async () => {
    mockApi.get.mockResolvedValue(null);

    await renderWithParams("/flow-explorer");

    // System name still resolves from the auth context even with no repo data.
    expect(await screen.findByTestId("context-header")).toBeInTheDocument();
    expect(screen.getByTestId("context-header-system")).toBeInTheDocument();
    expect(screen.queryByTestId("context-header-snapshot")).not.toBeInTheDocument();
    expect(screen.queryByTestId("context-header-capability")).not.toBeInTheDocument();
    expect(screen.queryByTestId("context-header-entrypoint")).not.toBeInTheDocument();
    expect(screen.queryByTestId("context-header-status")).not.toBeInTheDocument();
  });

  test("renders nothing when no data or params are available at all", async () => {
    mockSystems = [];
    mockApi.get.mockResolvedValue(null);

    await renderWithParams("/flow-explorer");

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalled();
    });
    expect(screen.queryByTestId("context-header")).not.toBeInTheDocument();
  });
});

// ── System Understanding page tests ─────────────────────────────────

describe("System Understanding page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const emptyResponse = {
    system_id: 1,
    snapshot_id: null,
    commit_sha: null,
    pipeline: [
      { step: "repository_configured", status: "missing" },
      { step: "snapshot_ready", status: "missing" },
      { step: "documentation_indexed", status: "missing" },
      { step: "documentation_claims_scanned", status: "missing" },
      { step: "symbols_indexed", status: "missing" },
      { step: "entrypoints_discovered", status: "missing" },
      { step: "docs_code_reconciled", status: "missing" },
      { step: "capability_hierarchy_ready", status: "missing" },
    ],
    purpose: null,
    capabilities: [],
    entrypoints: [],
    major_symbols: [],
    gaps: [],
    gap_summary: [],
    metadata_coverage: null,
    next_actions: [{ action: "Configure repository", reason: "No repository configured", category: "understand", link: "/repository" }],
  };

  const gapWorklistResponse = {
    system_id: 1,
    snapshot_id: 5,
    commit_sha: "abc12345",
    pipeline: [
      { step: "repository_configured", status: "complete" },
      { step: "snapshot_ready", status: "complete" },
      { step: "documentation_indexed", status: "complete" },
      { step: "documentation_claims_scanned", status: "complete" },
      { step: "symbols_indexed", status: "complete" },
      { step: "entrypoints_discovered", status: "complete" },
      { step: "docs_code_reconciled", status: "warning" },
      { step: "capability_hierarchy_ready", status: "complete" },
    ],
    purpose: { name: "Test System", summary: "A test system", provenance_kind: "reasoning_llm" },
    capabilities: [],
    entrypoints: [],
    major_symbols: [],
    gaps: [
      {
        gap_type: "unclassified_entrypoint",
        severity: "info",
        title: "Entrypoint not classified: GET:/items",
        node_name: "GET:/items",
        notes: "No capability classification",
        capability_key: null,
        doc_refs: [],
        symbol_refs: [{ path: "src/main.py", qualified_name: "list_items" }],
        entrypoint_refs: [{ entrypoint_type: "http_route", entrypoint_ref: "GET:/items" }],
        code_refs: [],
        next_actions: [
          { action: "Open Interview", link: "/interview" },
          { action: "Add source metadata", link: "/interview" },
        ],
      },
      {
        gap_type: "docs_only",
        severity: "warning",
        title: "Documented but no matching implementation: Auth",
        node_name: "Auth",
        notes: "Found in docs but no matching code",
        capability_key: null,
        doc_refs: [{ path: "docs/design.md", start_line: 10, end_line: 20 }],
        symbol_refs: [],
        entrypoint_refs: [],
        code_refs: [],
        next_actions: [
          { action: "Open docs evidence", link: null },
          { action: "Create implementation issue", link: null },
        ],
      },
    ],
    gap_summary: [
      { gap_type: "unclassified_entrypoint", count: 1 },
      { gap_type: "docs_only", count: 1 },
    ],
    metadata_coverage: { symbol_count: 42, symbols_with_source_metadata: 5, entrypoint_count: 10, entrypoints_with_capability_link: 3 },
    next_actions: [
      { action: "Review docs-code gaps", reason: "2 gaps found", category: "understand", link: "/system-understanding" },
      { action: "Review probe plan", reason: "Probe plan #7 is awaiting review", category: "observe", link: "/probe-planner?plan=7" },
      { action: "Generate / validate probe patch", reason: "Approved probe plan #8 has no validated patch yet", category: "instrument", link: "/probe-planner?plan=8" },
      { action: "Review experiment decision", reason: "Experiment #9 completed but has no recorded decision", category: "evaluate", link: "/experiments" },
    ],
  };

  const completeResponse = {
    system_id: 1,
    snapshot_id: 5,
    commit_sha: "abc12345def",
    pipeline: [
      { step: "repository_configured", status: "complete" },
      { step: "snapshot_ready", status: "complete" },
      { step: "documentation_indexed", status: "complete" },
      { step: "documentation_claims_scanned", status: "complete" },
      { step: "symbols_indexed", status: "complete" },
      { step: "entrypoints_discovered", status: "complete" },
      { step: "docs_code_reconciled", status: "complete" },
      { step: "capability_hierarchy_ready", status: "complete" },
    ],
    purpose: { name: "Test System", summary: "A test system for unit testing", provenance_kind: "reasoning_llm" },
    capabilities: [
      { name: "User Auth", summary: "Handles authentication", provenance_kind: "reasoning_llm" },
    ],
    entrypoints: [
      { entrypoint_type: "http_route", entrypoint_id: "GET:/items", category: "api", label: "List items" },
    ],
    major_symbols: [
      { path: "src/main.py", qualified_name: "list_items", kind: "function", route_path: "/items", route_method: "GET", component_id: null },
    ],
    gaps: [],
    gap_summary: [],
    metadata_coverage: { symbol_count: 42, symbols_with_source_metadata: 5, entrypoint_count: 10, entrypoints_with_capability_link: 3 },
    next_actions: [],
  };

  const blockedResponse = {
    ...emptyResponse,
    snapshot_id: 3,
    commit_sha: "def456",
    pipeline: [
      { step: "repository_configured", status: "complete" },
      { step: "snapshot_ready", status: "complete" },
      { step: "documentation_indexed", status: "missing", detail: "Reasoning model required" },
      { step: "documentation_claims_scanned", status: "missing" },
      { step: "symbols_indexed", status: "complete" },
      { step: "entrypoints_discovered", status: "complete" },
      { step: "docs_code_reconciled", status: "missing" },
      { step: "capability_hierarchy_ready", status: "missing", detail: "Reasoning model required" },
    ],
    next_actions: [
      { action: "Configure reasoning model", reason: "Required for documentation and capability analysis", category: "understand", link: null },
    ],
  };

  const gapResponse = {
    ...completeResponse,
    gaps: [
      {
        gap_type: "docs_only", severity: "warning", title: "Documented but missing: Feature X",
        node_name: "Feature X", notes: null, capability_key: null,
        doc_refs: [{ path: "README.md", start_line: 1, end_line: 5 }],
        symbol_refs: [], entrypoint_refs: [], code_refs: [],
        next_actions: [{ action: "Open docs evidence", link: null }],
      },
    ],
    gap_summary: [
      { gap_type: "docs_only", count: 3 },
      { gap_type: "code_only", count: 5 },
    ],
    metadata_coverage: { symbol_count: 100, symbols_with_source_metadata: 2, entrypoint_count: 20, entrypoints_with_capability_link: 1 },
  };

  test("renders pipeline checklist (not a separate empty state) when no snapshot exists", async () => {
    // Issue #200: EmptyState was folded into PipelineChecklist. The all-missing
    // pipeline is shown as the checklist itself, with a CTA on the first step.
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(emptyResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("pipeline-checklist")).toBeTruthy();
    });

    expect(screen.queryByText("Get started with System Understanding")).toBeNull();
  });

  test("shows a CTA only on the first incomplete step when the pipeline is all missing", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(emptyResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("pipeline-checklist")).toBeTruthy();
    });

    const cta = screen.getByTestId("pipeline-cta-repository_configured");
    expect(cta.textContent).toContain("Configure repository");
    expect(cta.getAttribute("href")).toBe("/repository");

    // No other step gets a CTA.
    expect(screen.queryByTestId("pipeline-cta-snapshot_ready")).toBeNull();
    expect(screen.queryByTestId("pipeline-cta-symbols_indexed")).toBeNull();
  });

  test("CTA targets only the first incomplete step in a mid-pipeline state, and wires to Build", async () => {
    // snapshot_ready is complete; documentation_indexed is the first incomplete
    // step, which maps to the Build / Refresh action rather than a repository link.
    const midResponse = {
      ...emptyResponse,
      snapshot_id: 5,
      commit_sha: "abc12345",
      pipeline: [
        { step: "repository_configured", status: "complete" },
        { step: "snapshot_ready", status: "complete" },
        { step: "documentation_indexed", status: "missing" },
        { step: "documentation_claims_scanned", status: "missing" },
        { step: "symbols_indexed", status: "missing" },
        { step: "entrypoints_discovered", status: "missing" },
        { step: "docs_code_reconciled", status: "missing" },
        { step: "capability_hierarchy_ready", status: "missing" },
      ],
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(midResponse)
        : Promise.resolve(null),
    );
    mockApi.post.mockImplementation((path: string) =>
      path === "/repository/system-understanding/build"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("pipeline-checklist")).toBeTruthy();
    });

    expect(screen.queryByTestId("pipeline-cta-repository_configured")).toBeNull();
    expect(screen.queryByTestId("pipeline-cta-snapshot_ready")).toBeNull();

    const cta = screen.getByTestId("pipeline-cta-documentation_indexed");
    expect(cta.textContent).toContain("Run Build / Refresh");
    expect(screen.queryByTestId("pipeline-cta-documentation_claims_scanned")).toBeNull();

    fireEvent.click(cta);
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/system-understanding/build");
    });
  });

  test("disables the build CTA while a build is running", async () => {
    const midResponse = {
      ...emptyResponse,
      pipeline: [
        { step: "repository_configured", status: "complete" },
        { step: "snapshot_ready", status: "complete" },
        { step: "documentation_indexed", status: "missing" },
        { step: "documentation_claims_scanned", status: "missing" },
        { step: "symbols_indexed", status: "missing" },
        { step: "entrypoints_discovered", status: "missing" },
        { step: "docs_code_reconciled", status: "missing" },
        { step: "capability_hierarchy_ready", status: "missing" },
      ],
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(midResponse);
      if (path === "/repository/system-understanding/build/latest") {
        return Promise.resolve({
          id: 1, job_id: 1, run_id: 1, system_id: 1, snapshot_id: 5,
          status: "running", current_step: "claim_scan", error: null,
          cancel_requested: false, is_stuck: false,
          heartbeat_at: Date.now() / 1000, started_at: Date.now() / 1000,
          completed_at: null, created_at: Date.now() / 1000,
          steps: [], llm_tasks: { total: 0, pending: 0, running: 0, completed: 0, failed: 0, cancelled: 0, reused: 0 },
          artifact_counts: {},
        });
      }
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("pipeline-cta-documentation_indexed")).toBeTruthy();
    });

    expect(screen.getByTestId("pipeline-cta-documentation_indexed")).toBeDisabled();
  });

  test("renders pipeline complete state with all sections", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Test System")).toBeTruthy();
    });

    expect(screen.getByText("A test system for unit testing")).toBeTruthy();
    expect(screen.getByText("User Auth")).toBeTruthy();
    expect(screen.getByText("GET:/items")).toBeTruthy();
    expect(screen.getByText("list_items")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("10")).toBeTruthy();
  });

  test("renders reasoning model blocked state without heuristic fallback", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(blockedResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("pipeline-checklist")).toBeTruthy();
    });

    const checklist = screen.getByTestId("pipeline-checklist");
    expect(checklist.textContent).toContain("missing");
    expect(checklist.textContent).toContain("complete");
  });

  test("renders docs-code gap worklist with cards", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(gapWorklistResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("gap-worklist")).toBeTruthy();
    });

    expect(screen.getByText(/Entrypoint not classified/)).toBeTruthy();
    expect(screen.getByText(/Documented but no matching implementation/)).toBeTruthy();

    const cards = screen.getAllByTestId("gap-card");
    expect(cards.length).toBe(2);
  });

  test("renders gap next action buttons", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(gapWorklistResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("gap-worklist")).toBeTruthy();
    });

    expect(screen.getByText("Open Interview")).toBeTruthy();
    expect(screen.getByText("Open docs evidence")).toBeTruthy();
  });

  test("renders category badges for probe plan and experiment next actions", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(gapWorklistResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Review probe plan")).toBeTruthy();
    });

    expect(screen.getByText("Generate / validate probe patch")).toBeTruthy();
    expect(screen.getByText("Review experiment decision")).toBeTruthy();

    // Issue #179: Next Actions are grouped under their stage section by category.
    expect(screen.getByTestId("stage-next-actions-observe").textContent).toContain("observe");
    expect(screen.getByTestId("stage-next-actions-instrument").textContent).toContain("instrument");
    expect(screen.getByTestId("stage-next-actions-evaluate").textContent).toContain("evaluate");
  });

  test("renders the 4 hub stage sections with links to detail pages (Issue #179)", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("stage-section-understand")).toBeTruthy();
    });

    expect(screen.getByTestId("stage-title-understand").textContent).toBe("Understand");
    expect(screen.getByTestId("stage-title-observe").textContent).toBe("Decide Where to Observe");
    expect(screen.getByTestId("stage-title-instrument").textContent).toBe("Instrument");
    expect(screen.getByTestId("stage-title-evaluate").textContent).toBe("Evaluate");

    // Existing pipeline checklist / gap worklist / capabilities elements survive the reorganization.
    expect(screen.getByTestId("pipeline-checklist")).toBeTruthy();
    expect(screen.getByTestId("metadata-coverage")).toBeTruthy();

    // Each stage links to its detail pages.
    expect(within(screen.getByTestId("stage-links-understand")).getByText("Capability Map")).toBeTruthy();
    // Issue #199: Interview is reachable from both the Understand and Decide
    // Where to Observe stages, since its stages map onto both.
    expect(within(screen.getByTestId("stage-links-understand")).getByText("Interview")).toBeTruthy();
    expect(within(screen.getByTestId("stage-links-observe")).getByText("Flow Explorer")).toBeTruthy();
    expect(within(screen.getByTestId("stage-links-observe")).getByText("Interview")).toBeTruthy();
    expect(within(screen.getByTestId("stage-links-instrument")).getByText("Probe Planner")).toBeTruthy();
    expect(within(screen.getByTestId("stage-links-evaluate")).getByText("Experiments")).toBeTruthy();
  });

  test("shows no-gaps message when gaps are empty", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve({ ...completeResponse, gaps: [], gap_summary: [] })
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("no-gaps-message")).toBeTruthy();
    });

    expect(screen.getByText(/No significant differences/)).toBeTruthy();
  });

  test("renders gap type filter buttons", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(gapWorklistResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("gap-summary")).toBeTruthy();
    });

    expect(screen.getByText("All (2)")).toBeTruthy();
    expect(screen.getByText("unclassified_entrypoint (1)")).toBeTruthy();
    expect(screen.getByText("docs_only (1)")).toBeTruthy();
  });

  test("renders metadata coverage with values from gap response", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(gapResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("metadata-coverage")).toBeTruthy();
    });

    expect(screen.getByText("100")).toBeTruthy();
    expect(screen.getByText("2 with metadata")).toBeTruthy();
    expect(screen.getByText("20")).toBeTruthy();
    expect(screen.getByText("1 with capability link")).toBeTruthy();
  });

  test("build button triggers POST and refreshes", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(emptyResponse)
        : Promise.resolve(null),
    );
    mockApi.post.mockImplementation((path: string) =>
      path === "/repository/system-understanding/build"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("build-button")).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId("build-button"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/system-understanding/build");
    });
  });

  test("entrypoint IDs link to Flow Explorer with query params", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const link = await screen.findByTestId("entrypoint-flow-link");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toContain("/flow-explorer?entrypoint_type=http_route&entrypoint_id=");
  });

  test("symbol route links to Flow Explorer", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const link = await screen.findByTestId("symbol-flow-link");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toContain("/flow-explorer?entrypoint_type=api");
  });

  test("gap entrypoint refs link to Flow Explorer", async () => {
    const responseWithEpGap = {
      ...completeResponse,
      gaps: [{
        gap_type: "unclassified_entrypoint", severity: "info",
        title: "Unclassified: GET /health", node_name: null, notes: null,
        capability_key: null,
        doc_refs: [], symbol_refs: [],
        entrypoint_refs: [{ entrypoint_type: "http_route", entrypoint_ref: "GET /health" }],
        code_refs: [],
        next_actions: [],
      }],
      gap_summary: [{ gap_type: "unclassified_entrypoint", count: 1 }],
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(responseWithEpGap)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const link = await screen.findByTestId("gap-entrypoint-link");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toContain("/flow-explorer?entrypoint_type=http_route");
  });

  test("capability names link to Capability Map with query param", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("User Auth")).toBeTruthy();
    });

    const capLink = screen.getByText("User Auth");
    expect(capLink.closest("a")?.getAttribute("href")).toContain("/capability-map?capability=User%20Auth");
  });

  // ── Issue drafts (Issue #107) ──────────────────────────────────────

  const gapWithSourceKey = {
    gap_type: "docs_only",
    severity: "warning",
    title: "Documented but no matching implementation: Auth",
    node_name: "Auth",
    notes: "Found in docs but no matching code",
    capability_key: null,
    doc_refs: [{ path: "docs/design.md", start_line: 10, end_line: 20 }],
    symbol_refs: [],
    entrypoint_refs: [],
    code_refs: [],
    next_actions: [
      { action: "Open docs evidence", link: null },
      { action: "Create implementation issue", link: null },
    ],
    source_key: "system_understanding_gap:docs_only:Auth",
    issue_drafts: [],
  };

  const draftGapResponse = {
    ...completeResponse,
    gaps: [gapWithSourceKey],
    gap_summary: [{ gap_type: "docs_only", count: 1 }],
  };

  const sampleDraft = {
    id: 7,
    system_id: 1,
    snapshot_id: 5,
    commit_sha: "abc12345def",
    source_type: "system_understanding_gap",
    source_key: "system_understanding_gap:docs_only:Auth",
    gap_type: "docs_only",
    severity: "warning",
    node_name: "Auth",
    title: "Documented but no matching implementation: Auth",
    body_markdown: "## Gap\n\n- **Type:** `docs_only`\n\n## Observation snapshot\n\n- **Commit sha:** `abc12345def`\n",
    status: "draft",
    external_url: null,
    created_at: 1,
    updated_at: 1,
  };

  test("Create implementation issue button generates a draft via POST", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(draftGapResponse)
        : Promise.resolve(null),
    );
    mockApi.post.mockResolvedValue(sampleDraft);

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const btn = await screen.findByTestId("gap-create-issue");
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/issue-drafts", {
        gap: gapWithSourceKey,
        snapshot_id: 5,
        commit_sha: "abc12345def",
      });
    });
  });

  test("draft dialog opens after generation and can copy Markdown", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(draftGapResponse);
      if (path === "/issue-drafts/7") return Promise.resolve(sampleDraft);
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue(sampleDraft);

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("gap-create-issue"));

    const copyBtn = await screen.findByTestId("issue-draft-copy");
    fireEvent.click(copyBtn);
    expect(writeText).toHaveBeenCalledWith(sampleDraft.body_markdown);
  });

  test("registering an external URL PATCHes the draft", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(draftGapResponse);
      if (path === "/issue-drafts/7") return Promise.resolve(sampleDraft);
      return Promise.resolve(null);
    });
    mockApi.post.mockResolvedValue(sampleDraft);
    mockApi.patch.mockResolvedValue({ ...sampleDraft, external_url: "https://x.test/1", status: "external_created" });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("gap-create-issue"));

    const urlInput = await screen.findByTestId("issue-draft-url");
    fireEvent.change(urlInput, { target: { value: "https://x.test/1" } });
    fireEvent.click(screen.getByTestId("issue-draft-register-url"));

    await waitFor(() => {
      expect(mockApi.patch).toHaveBeenCalledWith("/issue-drafts/7", {
        external_url: "https://x.test/1",
        status: "external_created",
      });
    });
  });

  test("existing gap draft with external URL is surfaced on the gap", async () => {
    const gapWithDraft = {
      ...gapWithSourceKey,
      issue_drafts: [
        { id: 7, status: "external_created", external_url: "https://x.test/1", title: "Auth" },
      ],
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve({ ...draftGapResponse, gaps: [gapWithDraft] })
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("gap-issue-drafts")).toBeTruthy();
    });
    const link = screen.getByTestId("gap-issue-draft-url");
    expect(link.getAttribute("href")).toBe("https://x.test/1");
    // Existing draft flips the action button label to "Open issue draft".
    expect(screen.getByText("Open issue draft")).toBeTruthy();
  });

  // ── Primary action card (Issue #201) ──────────────────────────────

  test("renders a navigate primary_action as a link button under the header", async () => {
    const response = {
      ...completeResponse,
      next_actions: [
        { action: "Define System Purpose", reason: "Pipeline completed, but no system purpose is defined yet.", category: "understand", link: "/interview" },
      ],
      primary_action: {
        action: "Define System Purpose",
        reason: "Pipeline completed, but no system purpose is defined yet.",
        category: "understand",
        link: "/interview",
        action_kind: "navigate",
      },
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(response)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("primary-action")).toBeTruthy();
    });

    const card = screen.getByTestId("primary-action");
    expect(card.textContent).toContain("Define System Purpose");
    expect(card.textContent).toContain("Pipeline completed, but no system purpose is defined yet.");

    const cta = screen.getByTestId("primary-action-cta");
    expect(cta.getAttribute("href")).toBe("/interview");
  });

  test("renders a build primary_action that triggers build.mutate on click", async () => {
    const midResponse = {
      ...emptyResponse,
      snapshot_id: 5,
      commit_sha: "abc12345",
      pipeline: [
        { step: "repository_configured", status: "complete" },
        { step: "snapshot_ready", status: "complete" },
        { step: "documentation_indexed", status: "missing" },
        { step: "documentation_claims_scanned", status: "missing" },
        { step: "symbols_indexed", status: "missing" },
        { step: "entrypoints_discovered", status: "missing" },
        { step: "docs_code_reconciled", status: "missing" },
        { step: "capability_hierarchy_ready", status: "missing" },
      ],
      primary_action: {
        action: "Build system understanding",
        reason: "6 pipeline steps not complete yet",
        category: "understand",
        link: null,
        action_kind: "build",
      },
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(midResponse)
        : Promise.resolve(null),
    );
    mockApi.post.mockImplementation((path: string) =>
      path === "/repository/system-understanding/build"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("primary-action")).toBeTruthy();
    });

    const cta = screen.getByTestId("primary-action-cta");
    expect(cta.textContent).toContain("Build system understanding");

    fireEvent.click(cta);
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/system-understanding/build");
    });
  });

  test("hides the primary action card when primary_action is null", async () => {
    const response = { ...completeResponse, primary_action: null };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(response)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Test System")).toBeTruthy();
    });

    expect(screen.queryByTestId("primary-action")).toBeNull();
  });
});

// ── System settings diagnostics (Issue #101) ────────────────────────

describe("System Understanding build job panel (Issue #109)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const baseUnderstanding = {
    system_id: 1,
    snapshot_id: 5,
    commit_sha: "abc12345def",
    pipeline: [
      { step: "repository_configured", status: "complete" },
      { step: "snapshot_ready", status: "complete" },
      { step: "documentation_indexed", status: "missing" },
      { step: "documentation_claims_scanned", status: "missing" },
      { step: "symbols_indexed", status: "complete" },
      { step: "entrypoints_discovered", status: "complete" },
      { step: "docs_code_reconciled", status: "missing" },
      { step: "capability_hierarchy_ready", status: "complete" },
    ],
    purpose: null,
    capabilities: [],
    entrypoints: [],
    major_symbols: [],
    gaps: [],
    gap_summary: [],
    metadata_coverage: null,
    next_actions: [],
  };

  const jobStep = (step: string, status: string, extra: Record<string, unknown> = {}) => ({
    id: 1,
    step,
    status,
    depends_on: [],
    reused_existing: false,
    cancel_requested: false,
    error: null,
    artifact_provenance: {},
    duration_ms: status === "completed" ? 120 : null,
    heartbeat_at: null,
    started_at: null,
    completed_at: null,
    ...extra,
  });

  const runningJob = {
    id: 7,
    job_id: 7,
    run_id: 71,
    system_id: 1,
    snapshot_id: 5,
    status: "running",
    current_step: "claim_scan",
    error: null,
    cancel_requested: false,
    is_stuck: false,
    heartbeat_at: Date.now() / 1000,
    started_at: Date.now() / 1000,
    completed_at: null,
    created_at: Date.now() / 1000,
    steps: [
      jobStep("symbol_index", "completed", { id: 1, reused_existing: true }),
      jobStep("entrypoint_index", "completed", { id: 2 }),
      jobStep("documentation_index", "completed", { id: 3 }),
      jobStep("claim_scan", "running", { id: 4 }),
      jobStep("understanding_graph", "pending", { id: 5 }),
      jobStep("docs_code_reconcile", "pending", { id: 6 }),
      jobStep("capability_hierarchy", "pending", { id: 7 }),
    ],
    llm_tasks: { total: 8, pending: 3, running: 1, completed: 4, failed: 0, cancelled: 0, reused: 2 },
    artifact_counts: { symbols: 42, entrypoints: 10, understanding_graph_claims: 0, capability_hierarchy_nodes: 6 },
  };

  const partialJob = {
    ...runningJob,
    id: 8,
    job_id: 8,
    status: "partial",
    current_step: null,
    error: "claim_scan: 2/8 documentation chunks failed to scan",
    completed_at: Date.now() / 1000,
    steps: [
      jobStep("symbol_index", "completed", { id: 1 }),
      jobStep("entrypoint_index", "completed", { id: 2 }),
      jobStep("documentation_index", "completed", { id: 3 }),
      jobStep("claim_scan", "failed", { id: 4, error: "2/8 documentation chunks failed to scan" }),
      jobStep("understanding_graph", "blocked", { id: 5, error: "Dependency not satisfied: claim_scan is failed" }),
      jobStep("docs_code_reconcile", "blocked", { id: 6 }),
      jobStep("capability_hierarchy", "completed", { id: 7 }),
    ],
    llm_tasks: { total: 8, pending: 0, running: 0, completed: 6, failed: 2, cancelled: 0, reused: 0 },
  };

  function mockPage(latestBuild: unknown) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(baseUnderstanding);
      if (path === "/repository/system-understanding/build/latest") return Promise.resolve(latestBuild);
      return Promise.resolve(null);
    });
  }

  test("running job shows step statuses, chunk progress, and cancel action", async () => {
    mockPage(runningJob);

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("build-job-panel")).toBeTruthy();
    });

    expect(screen.getByTestId("job-status").textContent).toBe("running");
    expect(screen.getByTestId("job-steps").children.length).toBe(7);
    expect(screen.getByTestId("job-step-claim_scan").textContent).toContain("running");
    expect(screen.getByTestId("job-llm-progress").textContent).toContain("chunks 4/8");
    expect(screen.getByTestId("job-step-symbol_index").textContent).toContain("reused");
    expect(screen.getByTestId("job-artifact-counts").textContent).toContain("Symbols: 42");
    expect(screen.getByTestId("job-cancel")).toBeTruthy();

    fireEvent.click(screen.getByTestId("job-cancel"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/repository/system-understanding/jobs/7/cancel",
      );
    });
  });

  test("partial job shows step errors and retry actions", async () => {
    mockPage(partialJob);

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("build-job-panel")).toBeTruthy();
    });

    expect(screen.getByTestId("job-status").textContent).toBe("partial");
    expect(screen.getByTestId("job-error").textContent).toContain("2/8 documentation chunks failed");
    expect(screen.getByTestId("job-step-error-claim_scan").textContent).toContain("failed to scan");
    expect(screen.getByTestId("build-failed")).toBeTruthy();

    fireEvent.click(screen.getByTestId("job-step-retry-claim_scan"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/repository/system-understanding/jobs/8/steps/claim_scan/retry",
      );
    });

    fireEvent.click(screen.getByTestId("job-retry"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/repository/system-understanding/jobs/8/retry",
      );
    });
  });

  test("stuck job is flagged and retryable", async () => {
    mockPage({
      ...runningJob,
      id: 9,
      job_id: 9,
      is_stuck: true,
      heartbeat_at: Date.now() / 1000 - 100000,
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("job-stuck")).toBeTruthy();
    });
    expect(screen.getByTestId("job-retry")).toBeTruthy();
  });

  test("completed job without failures does not keep the panel on screen", async () => {
    mockPage({
      ...runningJob,
      id: 10,
      job_id: 10,
      status: "completed",
      current_step: null,
      completed_at: Date.now() / 1000,
      steps: runningJob.steps.map((s) => ({ ...s, status: "completed" })),
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("pipeline-checklist")).toBeTruthy();
    });
    expect(screen.queryByTestId("build-job-panel")).toBeNull();
  });
});

describe("System settings diagnostics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const diagnosticsResponse = {
    system_id: 1,
    generated_at: 1750000000,
    overall_severity: "error",
    severity_counts: { ok: 3, warning: 2, error: 1, blocked: 0, unknown: 0 },
    checks: [
      {
        check_id: "intelligence_llm_config",
        category: "llm",
        title: "Intelligence reasoning model configuration",
        severity: "error",
        detail: "model 'gpt-5.4' configured but INTELLIGENCE_LLM_PROVIDER is empty.",
        impact: "Claim scanning and capability hierarchy stay blocked.",
        remediation: "Set INTELLIGENCE_LLM_PROVIDER and INTELLIGENCE_LLM_MODEL to a reasoning-capable pair.",
        related_env: ["INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL"],
        related_paths: [],
        related_pages: ["/system-understanding"],
        related_pipeline_steps: ["documentation_claims_scanned", "capability_hierarchy_ready"],
        last_observed_error: {
          source: "intelligence_runs#12:repository_drafts",
          status: "failed",
          error: "LLM request failed: HTTP 401: invalid api key",
          observed_at: 1749999000,
        },
        decision_method: "deterministic",
        fix_kind: "dialog",
        fix_page: null,
        fix_anchor: null,
      },
      {
        check_id: "snapshot_status",
        category: "repository",
        title: "Ready repository snapshot",
        severity: "warning",
        detail: "Latest ready snapshot #7 contains 0 indexed files.",
        impact: "Indexing produces empty results.",
        remediation: "Review the include/exclude patterns in the Repository tab.",
        related_env: [],
        related_paths: [],
        related_pages: ["/repository"],
        related_pipeline_steps: ["snapshot_ready"],
        last_observed_error: null,
        decision_method: "deterministic",
        fix_kind: "navigate",
        fix_page: "/repository",
        fix_anchor: "repo-patterns",
      },
      {
        check_id: "pipeline_documentation_index",
        category: "pipeline",
        title: "Documentation index build step",
        severity: "warning",
        detail: "This build step has not run for the current snapshot.",
        impact: "The step shows as missing in System Understanding.",
        remediation: "Run Build / Refresh in System Understanding to index documentation chunks.",
        related_env: [],
        related_paths: [],
        related_pages: ["/system-understanding"],
        related_pipeline_steps: ["documentation_indexed"],
        last_observed_error: null,
        decision_method: "deterministic",
        fix_kind: "navigate",
        fix_page: "/system-understanding",
        fix_anchor: "build",
      },
      {
        check_id: "database_storage",
        category: "database",
        title: "Database storage",
        severity: "ok",
        detail: "Database path ./probe.db is readable and writable.",
        impact: "",
        remediation: "",
        related_env: ["PROBE_DB_PATH"],
        related_paths: ["./probe.db"],
        related_pages: [],
        related_pipeline_steps: [],
        last_observed_error: null,
        decision_method: "deterministic",
        fix_kind: "dialog",
        fix_page: null,
        fix_anchor: null,
      },
    ],
  };

  test("badge shows attention count and opens detail dialog with remediation and last error", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-diagnostics"
        ? Promise.resolve(diagnosticsResponse)
        : Promise.resolve(null),
    );

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId("diagnostics-badge");
    // error(1) + warning(2) = 3
    expect(screen.getByTestId("diagnostics-badge-count").textContent).toBe("3");

    fireEvent.click(badge);

    await waitFor(() => {
      expect(screen.getByText("System Settings Diagnostics")).toBeTruthy();
    });

    // Problems are listed with detail, impact, remediation, related env.
    expect(screen.getByText(/INTELLIGENCE_LLM_PROVIDER is empty/)).toBeTruthy();
    expect(
      screen.getByText(/Set INTELLIGENCE_LLM_PROVIDER and INTELLIGENCE_LLM_MODEL/),
    ).toBeTruthy();
    // Last observed runtime failure is shown verbatim.
    const lastError = screen.getByTestId("diagnostic-last-error");
    expect(lastError.textContent).toContain("HTTP 401: invalid api key");
    expect(lastError.textContent).toContain("intelligence_runs#12:repository_drafts");
    // Passing checks are still visible as passing.
    expect(screen.getByText("正常なチェック")).toBeTruthy();
    expect(screen.getByText("Database storage")).toBeTruthy();
  });

  test("clicking a navigate check routes to its fix page with focus params", async () => {
    window.history.pushState({}, "", "/");
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-diagnostics"
        ? Promise.resolve(diagnosticsResponse)
        : Promise.resolve(null),
    );

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId("diagnostics-badge");
    fireEvent.click(badge);

    // snapshot_status is a navigate check → click its card.
    const snapshotDetail = await screen.findByText(/0 indexed files/);
    fireEvent.click(snapshotDetail);

    await waitFor(() => {
      expect(window.location.pathname).toBe("/repository");
    });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("diagnostic")).toBe("snapshot_status");
    expect(params.get("fix")).toBe("repo-patterns");
  });

  test("clicking an env-only check opens a remediation dialog instead of navigating", async () => {
    window.history.pushState({}, "", "/");
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-diagnostics"
        ? Promise.resolve(diagnosticsResponse)
        : Promise.resolve(null),
    );

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId("diagnostics-badge");
    fireEvent.click(badge);

    // intelligence_llm_config is a dialog check → click its card.
    const llmDetail = await screen.findByText(/INTELLIGENCE_LLM_PROVIDER is empty/);
    fireEvent.click(llmDetail);

    const envDialog = await screen.findByTestId("diagnostic-env-dialog");
    expect(envDialog.textContent).toContain("設定が必要な環境変数");
    // Did not navigate away.
    expect(window.location.pathname).toBe("/");
    // The list dialog closed so the two modals don't stack.
    expect(screen.queryByText("System Settings Diagnostics")).toBeNull();
  });

  test("badge renders without count when everything is ok", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-diagnostics"
        ? Promise.resolve({
            ...diagnosticsResponse,
            overall_severity: "ok",
            severity_counts: { ok: 4, warning: 0, error: 0, blocked: 0, unknown: 0 },
            checks: [diagnosticsResponse.checks[3]],
          })
        : Promise.resolve(null),
    );

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    await screen.findByTestId("diagnostics-badge");
    expect(screen.queryByTestId("diagnostics-badge-count")).toBeNull();
  });

  test("System Understanding pipeline rows link missing/blocked steps to diagnostics", async () => {
    const suResponse = {
      system_id: 1,
      snapshot_id: 7,
      commit_sha: "94e9d605aaaa",
      pipeline: [
        { step: "repository_configured", status: "complete" },
        { step: "snapshot_ready", status: "complete" },
        { step: "documentation_indexed", status: "missing" },
        { step: "documentation_claims_scanned", status: "missing" },
        { step: "symbols_indexed", status: "complete" },
        { step: "entrypoints_discovered", status: "complete" },
        { step: "docs_code_reconciled", status: "warning" },
        { step: "capability_hierarchy_ready", status: "missing" },
      ],
      purpose: null,
      capabilities: [],
      entrypoints: [],
      major_symbols: [],
      gaps: [],
      gap_summary: [],
      metadata_coverage: null,
      next_actions: [],
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(suResponse);
      if (path === "/system-diagnostics") return Promise.resolve(diagnosticsResponse);
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    // documentation_indexed is missing and has a dedicated build-step diagnostic.
    const diagnoseButton = await screen.findByTestId("pipeline-diagnose-documentation_indexed");
    expect(diagnoseButton.textContent).toContain("1");

    // Complete steps get no diagnose button even if a check references them.
    expect(screen.queryByTestId("pipeline-diagnose-snapshot_ready")).toBeNull();

    fireEvent.click(diagnoseButton);
    const expanded = await screen.findByTestId("pipeline-diagnostics-documentation_indexed");
    expect(expanded.textContent).toContain("Documentation index build step");
    expect(expanded.textContent).toContain("Run Build / Refresh");
  });
});

// ── Per-screen assistant (Issue #102) ───────────────────────────────

describe("Per-screen assistant panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const screenContextResponse = {
    screen_id: "system-understanding",
    title: "System Understanding",
    route: "/system-understanding",
    purpose:
      "Track the progress and artifacts of the repository understanding pipeline.",
    primary_data_sources: ["repository snapshots", "intelligence runs"],
    visible_sections: ["Repository configured", "Documentation indexed"],
    common_questions: ["What does Build / Refresh run?"],
    related_settings: ["INTELLIGENCE_LLM_MODEL"],
    related_checks: ["intelligence_llm_config"],
    related_pipeline_steps: ["documentation_indexed"],
    related_endpoints: ["GET /repository/system-understanding"],
    state_severity: "blocked",
    screen_checks: [
      {
        check_id: "intelligence_llm_config",
        category: "llm",
        title: "Intelligence reasoning model configuration",
        severity: "blocked",
        detail: "effective intelligence provider is 'mock'.",
        impact: "Reasoning steps stay blocked.",
        remediation: "Set INTELLIGENCE_LLM_PROVIDER and INTELLIGENCE_LLM_MODEL.",
        related_env: ["INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL"],
        related_paths: [],
        related_pages: ["/system-understanding"],
        related_pipeline_steps: ["documentation_indexed"],
        last_observed_error: null,
        decision_method: "deterministic",
      },
    ],
    suggested_questions: [
      {
        question: "Why is 'Intelligence reasoning model configuration' blocked?",
        source: "diagnostics",
        check_id: "intelligence_llm_config",
      },
      { question: "What does Build / Refresh run?", source: "static", check_id: "" },
    ],
  };

  const askResponse = {
    screen_id: "system-understanding",
    answer:
      "INTELLIGENCE_LLM_MODEL (Intelligence reasoning model, conditional): reasoning-capable model id for Feature Intelligence.",
    suggested_actions: [
      {
        label: "Review INTELLIGENCE_LLM_MODEL",
        kind: "configure",
        target: "INTELLIGENCE_LLM_MODEL",
        detail: "Set a reasoning-capable model id.",
      },
      {
        label: "Open /system-understanding",
        kind: "navigate",
        target: "/system-understanding",
        detail: "",
      },
    ],
    citations: [
      {
        type: "setting",
        id: "INTELLIGENCE_LLM_MODEL",
        title: "Intelligence reasoning model",
        detail: "",
      },
      {
        type: "diagnostic_check",
        id: "intelligence_llm_config",
        title: "Intelligence reasoning model configuration",
        detail: "blocked: effective intelligence provider is 'mock'.",
      },
    ],
    used_fallback: true,
    fallback_reason: "LLM provider 'mock' is test-only data.",
    decision_method: "deterministic",
    provider: "mock",
    model: "mock",
    prompt_version: "v1",
    schema_version: "v1",
    generated_at: 1750000000,
  };

  function mockAssistantApi() {
    mockApi.get.mockImplementation((path: string) =>
      path === "/assistant/screen-context/system-understanding"
        ? Promise.resolve(screenContextResponse)
        : Promise.resolve(null),
    );
    mockApi.post.mockImplementation((path: string) =>
      path === "/assistant/ask"
        ? Promise.resolve(askResponse)
        : Promise.resolve(null),
    );
  }

  async function renderPanelAt(route: string) {
    const { AssistantPanel } = await import("@/components/assistant-panel");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[route]}>
          <AssistantPanel />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  test("agent button opens the panel with screen summary, state, and suggested questions", async () => {
    mockAssistantApi();
    await renderPanelAt("/system-understanding");

    // Closed by default: only the floating button, no panel blocking the page.
    const button = screen.getByTestId("assistant-button");
    expect(screen.queryByTestId("assistant-panel")).toBeNull();

    fireEvent.click(button);
    await screen.findByTestId("assistant-panel");

    // The screen context is fetched for the current route's screen id.
    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith(
        "/assistant/screen-context/system-understanding",
      );
    });

    // Screen name, purpose, and current state summary are visible.
    expect(screen.getByText("System Understanding")).toBeTruthy();
    expect(screen.getByText(/repository understanding pipeline/)).toBeTruthy();
    const state = await screen.findByTestId("assistant-state-summary");
    expect(state.textContent).toContain("1 check(s) need attention");
    expect(state.textContent).toContain("effective intelligence provider is 'mock'");

    // Diagnostics-derived question is offered first.
    const suggestions = screen.getAllByTestId("assistant-suggested-question");
    expect(suggestions[0].textContent).toContain(
      "Why is 'Intelligence reasoning model configuration' blocked?",
    );
  });

  test("closed agent button can show a snapshot notice bubble", async () => {
    const onSnapshotNoticeClick = vi.fn();
    const { AssistantPanel } = await import("@/components/assistant-panel");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/system-understanding"]}>
          <AssistantPanel
            snapshotNotice="HEAD が最新 snapshot より進んでいます。snapshot を作成してください。"
            onSnapshotNoticeClick={onSnapshotNoticeClick}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const notice = screen.getByTestId("assistant-snapshot-notice");
    expect(notice.textContent).toContain("snapshot を作成してください");
    fireEvent.click(notice);
    expect(onSnapshotNoticeClick).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("assistant-button"));
    expect(screen.queryByTestId("assistant-snapshot-notice")).toBeNull();
  });

  test("asking a question renders the answer with fallback marking, citations, and actions", async () => {
    mockAssistantApi();
    await renderPanelAt("/system-understanding");
    fireEvent.click(screen.getByTestId("assistant-button"));
    await screen.findByTestId("assistant-panel");

    const input = await screen.findByTestId("assistant-question-input");
    fireEvent.change(input, {
      target: { value: "What should INTELLIGENCE_LLM_MODEL be set to?" },
    });
    fireEvent.click(screen.getByTestId("assistant-send"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/assistant/ask", {
        screen_id: "system-understanding",
        question: "What should INTELLIGENCE_LLM_MODEL be set to?",
        visible_check_ids: ["intelligence_llm_config"],
      });
    });

    const answer = await screen.findByTestId("assistant-answer");
    expect(answer.textContent).toContain("reasoning-capable model id");

    // Fallback answers are visibly marked; deterministic output is not
    // decorated as an LLM answer.
    expect(screen.getByTestId("assistant-fallback-badge")).toBeTruthy();

    // Citations show what the answer is based on.
    const citations = screen.getAllByTestId("assistant-citation");
    expect(citations.map((c) => c.textContent).join(" ")).toContain(
      "INTELLIGENCE_LLM_MODEL",
    );

    // Suggested actions include a configure hint and a navigation action.
    const actions = screen.getAllByTestId("assistant-action");
    expect(actions.length).toBe(2);
    expect(actions.map((a) => a.textContent).join(" ")).toContain(
      "Review INTELLIGENCE_LLM_MODEL",
    );
  });

  test("panel maps the root route to the overview screen id", async () => {
    mockApi.get.mockImplementation(() =>
      Promise.resolve({
        ...screenContextResponse,
        screen_id: "overview",
        title: "Overview",
        route: "/",
        state_severity: "ok",
        screen_checks: [],
        suggested_questions: [],
      }),
    );
    await renderPanelAt("/");
    fireEvent.click(screen.getByTestId("assistant-button"));
    await screen.findByTestId("assistant-panel");
    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/assistant/screen-context/overview");
    });
  });
});

// ── Trace Lineage Explorer (Issue #147) ─────────────────────────────

describe("Trace Lineage Explorer page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  function lineageResponse() {
    return {
      query: { kind: "entity", entity_type: "order", entity_id: "o-1" },
      steps: [
        {
          trace_id: "trace-aaaa1111", component_id: "validate", mode: "trace",
          span_id: "s1", parent_span_id: null, flow_id: "f1", correlation_id: "c1",
          duration_ms: 2, timestamp: 100, output: "'ok'", error: null,
          entities: [{ type: "order", id: "o-1", role: "source" }],
          projections: [{
            projection_name: "orders", phase: "output",
            fields: { status: "pending" }, metrics: {}, samples: {},
            data_hash: "h1", truncated: false, error: null,
          }],
        },
        {
          trace_id: "trace-bbbb2222", component_id: "charge", mode: "trace",
          span_id: "s2", parent_span_id: "s1", flow_id: "f1", correlation_id: "c1",
          duration_ms: 3, timestamp: 200, output: "'ok'", error: null,
          entities: [{ type: "order", id: "o-1", role: "related" }],
          projections: [{
            projection_name: "orders", phase: "output",
            fields: { status: "charged" }, metrics: {}, samples: {},
            data_hash: "h2", truncated: false, error: null,
          }],
        },
      ],
    };
  }

  test("searching an entity shows time-ordered steps with projected fields", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path.startsWith("/trace-lineage/entities/")) return Promise.resolve(lineageResponse());
      return Promise.resolve({});
    });
    const { default: TraceLineagePage } = await import("@/pages/trace-lineage");
    render(<TraceLineagePage />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByLabelText("entity type"), { target: { value: "order" } });
    fireEvent.change(screen.getByLabelText("entity id"), { target: { value: "o-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/trace-lineage/entities/order/o-1");
    });
    expect(await screen.findByText("validate")).toBeInTheDocument();
    expect(screen.getByText("charge")).toBeInTheDocument();
    // Projected field values are shown.
    expect(screen.getByText(/pending/)).toBeInTheDocument();
    expect(screen.getByText(/charged/)).toBeInTheDocument();
  });

  test("changed projected field between steps is highlighted", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path.startsWith("/trace-lineage/entities/")) return Promise.resolve(lineageResponse());
      return Promise.resolve({});
    });
    const { default: TraceLineagePage } = await import("@/pages/trace-lineage");
    render(<TraceLineagePage />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByLabelText("entity type"), { target: { value: "order" } });
    fireEvent.change(screen.getByLabelText("entity id"), { target: { value: "o-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    // The second step's status changed (pending -> charged): a change marker appears.
    const markers = await screen.findAllByLabelText("changed");
    expect(markers.length).toBeGreaterThanOrEqual(1);
  });

  test("empty lineage shows SDK setup guidance", async () => {
    mockApi.get.mockImplementation(() => Promise.resolve({ query: {}, steps: [] }));
    const { default: TraceLineagePage } = await import("@/pages/trace-lineage");
    render(<TraceLineagePage />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByLabelText("entity type"), { target: { value: "order" } });
    fireEvent.change(screen.getByLabelText("entity id"), { target: { value: "missing" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText(/No lineage found/)).toBeInTheDocument();
    expect(screen.getByText(/probe_context/)).toBeInTheDocument();
  });

  test("time window inputs add start/end to the lineage query", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path.startsWith("/trace-lineage/entities/")) return Promise.resolve(lineageResponse());
      return Promise.resolve({});
    });
    const { default: TraceLineagePage } = await import("@/pages/trace-lineage");
    render(<TraceLineagePage />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByLabelText("entity type"), { target: { value: "order" } });
    fireEvent.change(screen.getByLabelText("entity id"), { target: { value: "o-1" } });
    fireEvent.change(screen.getByLabelText("time window start"), {
      target: { value: "2026-07-01T00:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      const call = mockApi.get.mock.calls.find(
        (c: string[]) => typeof c[0] === "string" && c[0].startsWith("/trace-lineage/entities/"),
      );
      expect(call?.[0]).toMatch(/\/trace-lineage\/entities\/order\/o-1\?start=\d+/);
    });
  });

  test("deep link ?kind=entity&type=…&id=… searches on load", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path.startsWith("/trace-lineage/entities/")) return Promise.resolve(lineageResponse());
      return Promise.resolve({});
    });
    const { default: TraceLineagePage } = await import("@/pages/trace-lineage");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/trace-lineage?kind=entity&type=order&id=o-1"]}>
          <TraceLineagePage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith("/trace-lineage/entities/order/o-1");
    });
    expect(await screen.findByText("validate")).toBeInTheDocument();
  });
});

// ── Trace Analyzers (Issue #148) ────────────────────────────────────

describe("Trace Analyzers page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const analyzer = {
    id: 7, name: "order flow", intent: "", spec: {
      source: "trace_projections",
      select: [{ name: "status", path: "$.fields.status" }],
    },
    source: "trace_projections", review_status: "proposed", decision_method: "manual",
    provider: null, model: null, prompt_version: null, schema_version: null,
    is_mock: false, created_at: 1, updated_at: 1,
  };

  test("rejects run until analyzer is approved (server 409 surfaced)", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/trace-analyzers") return Promise.resolve([{ ...analyzer, review_status: "approved" }]);
      if (path.endsWith("/runs")) return Promise.resolve([]);
      return Promise.resolve({});
    });
    mockApi.post.mockResolvedValue({
      id: 1, analyzer_id: 7, status: "completed",
      result: { row_count: 2, rows: [{ status: "a" }, { status: "b" }] },
      error_details: null, row_count: 2, started_at: 1, completed_at: 2,
    });
    const { default: Page } = await import("@/pages/trace-analyzers");
    render(<Page />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("order flow"));
    const runBtn = await screen.findByRole("button", { name: "Run" });
    expect(runBtn).not.toBeDisabled();
    fireEvent.click(runBtn);
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/trace-analyzers/7/runs");
    });
  });

  test("advanced JSON editor still posts a parsed spec", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/trace-analyzers") return Promise.resolve([]);
      return Promise.resolve([]);
    });
    mockApi.post.mockResolvedValue(analyzer);
    const { default: Page } = await import("@/pages/trace-analyzers");
    render(<Page />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByLabelText("analyzer name"), { target: { value: "my analyzer" } });
    // Advanced JSON stays available as an escape hatch (Issue #157).
    fireEvent.click(screen.getByText("Advanced JSON editor"));
    fireEvent.click(screen.getByRole("button", { name: /Create from JSON/ }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/trace-analyzers",
        expect.objectContaining({ name: "my analyzer", spec: expect.any(Object) }),
      );
    });
  });

  test("template builder generates a shadow-diff spec without hand-written JSON", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/trace-analyzers") return Promise.resolve([]);
      if (path === "/trace-analyzers/context") return Promise.resolve({
        components: ["svc"], entity_types: ["order"],
        entities: [{ entity_type: "order", entity_id: "o-1" }],
        projection_names: ["orders"], field_names: ["status"],
        phases: ["shadow_current", "shadow_candidate"], entities_truncated: false,
      });
      return Promise.resolve([]);
    });
    mockApi.post.mockResolvedValue(analyzer);
    const { default: Page } = await import("@/pages/trace-analyzers");
    render(<Page />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("Shadow diff"));
    // Choose the field to compare from the real context (a chip button).
    fireEvent.click(await screen.findByRole("button", { name: "status" }));
    const preview = await screen.findByTestId("spec-preview");
    expect(preview.textContent).toContain("shadow_current");
    expect(preview.textContent).toContain("shadow_candidate");
    fireEvent.click(screen.getByRole("button", { name: /Create \(proposed\)/ }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/trace-analyzers",
        expect.objectContaining({ spec: expect.objectContaining({ compare: expect.any(Object) }) }),
      );
    });
  });

  test("proposing from natural language posts the intent and marks mock", async () => {
    const proposed = {
      ...analyzer, id: 9, decision_method: "reasoning_llm", is_mock: true,
      provider: "mock", model: "mock",
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/trace-analyzers") return Promise.resolve([proposed]);
      return Promise.resolve([]);
    });
    mockApi.post.mockResolvedValue(proposed);
    const { default: Page } = await import("@/pages/trace-analyzers");
    render(<Page />, { wrapper: createWrapper() });

    fireEvent.change(screen.getByLabelText("analyzer intent"), {
      target: { value: "where did order o-1 status change" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Propose spec/ }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/trace-analyzers/propose",
        expect.objectContaining({ intent: "where did order o-1 status change" }),
      );
    });
    // The proposed analyzer surfaces its reasoning-model + mock provenance.
    expect(await screen.findByText(/Proposed by a reasoning model/)).toBeInTheDocument();
  });

  test("shadow compare run renders a diff summary", async () => {
    const approved = { ...analyzer, id: 5, review_status: "approved" };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/trace-analyzers") return Promise.resolve([approved]);
      if (path.endsWith("/runs")) return Promise.resolve([{
        id: 3, analyzer_id: 5, status: "completed", error_details: null,
        row_count: 2, started_at: 1, completed_at: 2,
        result: {
          row_count: 2,
          compare: {
            phases: ["shadow_current", "shadow_candidate"], fields: ["status"],
            entity_count: 2, diff_entity_count: 1, diff_fields: { status: 1 },
            candidate_error_count: 0, components_with_diff: ["svc"],
            examples: { "status::svc": ["trace-aaaa1111"] }, compared_trace_count: 2,
          },
        },
      }]);
      return Promise.resolve({});
    });
    const { default: Page } = await import("@/pages/trace-analyzers");
    render(<Page />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("order flow"));
    // The compare tab renders a current/candidate/changed table (Issue #157).
    const table = await screen.findByTestId("compare-table");
    expect(within(table).getByText(/1\/2 entities differ/)).toBeInTheDocument();
    expect(within(table).getByText("status")).toBeInTheDocument();
    expect(within(table).getByText("changed")).toBeInTheDocument();
  });
});
