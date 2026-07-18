/// <reference types="vitest/globals" />
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, MemoryRouter, Routes, Route } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { SystemStateItem } from "@/api/types";

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

// ── Components trace signal details ────────────────────────────────

describe("Components page trace details", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("expands a trace to show its input, output, and error", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/components") {
        return Promise.resolve([
          { component_id: "order-validator", mode: "trace", trace_count: 1, last_seen: 1000 },
        ]);
      }
      if (path === "/components/order-validator/traces?limit=20") {
        return Promise.resolve([{
          trace_id: "trace-1234567890",
          component_id: "order-validator",
          mode: "trace",
          input: { args: [{ order_id: "order-42" }], kwargs: { strict: "True" } },
          output: "{'valid': False}",
          error: "ValidationError: missing customer",
          duration_ms: 12.5,
          timestamp: 1000,
        }]);
      }
      if (path === "/components/order-validator/profile") {
        return Promise.resolve(null);
      }
      if (path === "/components/order-validator/shadow-results?limit=20") {
        return Promise.resolve([]);
      }
      if (path === "/components/order-validator/criteria") {
        return Promise.resolve([]);
      }
      return Promise.resolve(null);
    });

    const { default: ComponentsPage } = await import("@/pages/components");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/components?component=order-validator"]}>
          <ComponentsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const detailsButton = await screen.findByRole("button", {
      name: "Trace trace-1234567890 の詳細を表示",
    });
    expect(screen.queryByText("ValidationError: missing customer")).not.toBeInTheDocument();

    fireEvent.click(detailsButton);

    expect(screen.getByRole("button", {
      name: "Trace trace-1234567890 の詳細を隠す",
    })).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: /0.*object/ }));
    expect(screen.getByText(/order-42/)).toBeInTheDocument();
    expect(screen.getByText("{'valid': False}")).toBeInTheDocument();
    expect(screen.getByText("ValidationError: missing customer")).toBeInTheDocument();
    expect(screen.getByText("trace-1234567890")).toBeInTheDocument();

    await waitFor(() => {
      const componentRefreshes = mockApi.get.mock.calls.filter(([path]) => path === "/components");
      const traceRefreshes = mockApi.get.mock.calls.filter(
        ([path]) => path === "/components/order-validator/traces?limit=20",
      );
      expect(componentRefreshes.length).toBeGreaterThanOrEqual(2);
      expect(traceRefreshes.length).toBeGreaterThanOrEqual(2);
    }, { timeout: 3_000 });
  });
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

// ── Experiment decision next-action (Issue #259) ────────────────────

describe("Experiment decision next-action (Issue #259)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  function decidedExperimentFixture(overrides: Record<string, unknown> = {}) {
    return {
      id: 1, feature_id: "feat-1", objective: "Test", status: "completed",
      human_decision: "adopted", human_decision_variant_key: "opt-v1", human_decision_note: "note",
      created_at: "2024-01-01T00:00:00Z",
      variants: [
        { id: 1, variant_key: "baseline", label: "Baseline", is_baseline: true, status: "completed", patch_text: null, risk_note: null, error: null, metrics: {} },
      ],
      comparison: {},
      ...overrides,
    };
  }

  function setupDecidedExperiment(exp: Record<string, unknown>, github: {
    appStatus?: Record<string, unknown>;
    connections?: Record<string, unknown>[];
  } = {}) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/experiments") return Promise.resolve([exp]);
      if (path === "/repository/snapshots") return Promise.resolve([]);
      if (path === "/repository/drafts/latest") return Promise.resolve({ feature_drafts: [] });
      if (path === "/github/app-status") return Promise.resolve(
        github.appStatus ?? { configured: false, app_id: null, api_base_url: "", web_base_url: "" },
      );
      if (path === "/github/connections") return Promise.resolve(github.connections ?? []);
      return Promise.resolve(null);
    });
  }

  async function renderAndExpand() {
    const { default: ExperimentsPage } = await import("@/pages/experiments");
    render(<ExperimentsPage />, { wrapper: createWrapper() });
    await waitFor(() => expect(screen.getByText(/Experiment #1/)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Experiment #1/).closest("[class*=cursor-pointer]")!);
  }

  test("adopted decision shows GitHub publish + Probe Planner next actions when GitHub is configured", async () => {
    setupDecidedExperiment(decidedExperimentFixture(), {
      appStatus: { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" },
      connections: [{ id: 1, status: "connected" }],
    });
    await renderAndExpand();

    const githubLink = await screen.findByTestId("experiment-github-publish-link");
    expect(githubLink).toHaveAttribute("href", "/github");
    expect(screen.getByTestId("experiment-probe-planner-link")).toHaveAttribute("href", "/probe-planner");
  });

  test("adopted decision hides the GitHub publish link (but keeps Probe Planner) when GitHub is not configured", async () => {
    setupDecidedExperiment(decidedExperimentFixture());
    await renderAndExpand();

    expect(await screen.findByTestId("experiment-probe-planner-link")).toBeInTheDocument();
    expect(screen.queryByTestId("experiment-github-publish-link")).not.toBeInTheDocument();
  });

  test("adopted decision also hides the GitHub publish link when configured but no connection is connected", async () => {
    setupDecidedExperiment(decidedExperimentFixture(), {
      appStatus: { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" },
      connections: [{ id: 1, status: "error" }],
    });
    await renderAndExpand();

    expect(await screen.findByTestId("experiment-probe-planner-link")).toBeInTheDocument();
    expect(screen.queryByTestId("experiment-github-publish-link")).not.toBeInTheDocument();
  });

  test("rejected decision links to AI Candidate Studio", async () => {
    setupDecidedExperiment(decidedExperimentFixture({ human_decision: "rejected", human_decision_variant_key: null }));
    await renderAndExpand();

    const link = await screen.findByTestId("experiment-candidate-studio-link");
    expect(link).toHaveAttribute("href", "/candidate-studio");
    expect(screen.queryByTestId("experiment-github-publish-link")).not.toBeInTheDocument();
    expect(screen.queryByTestId("experiment-probe-planner-link")).not.toBeInTheDocument();
  });

  test("needs_more_data decision also links to AI Candidate Studio", async () => {
    setupDecidedExperiment(decidedExperimentFixture({ human_decision: "needs_more_data", human_decision_variant_key: null }));
    await renderAndExpand();

    expect(await screen.findByTestId("experiment-candidate-studio-link")).toBeInTheDocument();
  });

  test("undecided experiment shows no next-action card", async () => {
    setupDecidedExperiment(decidedExperimentFixture({ human_decision: null, human_decision_variant_key: null }));
    await renderAndExpand();

    await waitFor(() => expect(screen.getByText("Decision")).toBeInTheDocument());
    expect(screen.queryByTestId("experiment-next-action-adopted")).not.toBeInTheDocument();
    expect(screen.queryByTestId("experiment-next-action-candidate-studio")).not.toBeInTheDocument();
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

// ── Probe Patch apply-success next action (Issue #259) ──────────────

describe("Probe Patch apply-success next action (Issue #259)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  function mockAppliedPatch(github: {
    appStatus?: Record<string, unknown>;
    connections?: Record<string, unknown>[];
  } = {}) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") {
        return Promise.resolve({
          system_id: 1, is_mock: false,
          plans: [{
            id: 10, feature_id: "feat-1", objective: "Observe behavior", status: "proposed",
            created_at: "2024-01-01", probe_points: [],
          }],
        });
      }
      if (path === "/repository/probe-patches") {
        return Promise.resolve([{
          id: 20, plan_id: 10, system_id: 1, snapshot_id: 5,
          commit_sha: "abcdef1234567890", diff: "diff --git a/a.py b/a.py",
          worktree_path: null, skipped: [], status: "generated", error: null,
          cleanup_state: "removed", cleanup_error: null,
          apply_status: "applied", apply_error: null,
          applied_at: "2024-01-02T00:00:00Z", applied_by_user_id: 1,
          validation_runs: [
            { id: 1, variant: "baseline", overall_success: true, commands: [] },
            { id: 2, variant: "probed", overall_success: true, commands: [] },
          ],
          created_at: "2024-01-01",
        }]);
      }
      if (path === "/github/app-status") return Promise.resolve(
        github.appStatus ?? { configured: false, app_id: null, api_base_url: "", web_base_url: "" },
      );
      if (path === "/github/connections") return Promise.resolve(github.connections ?? []);
      return Promise.resolve(null);
    });
  }

  async function renderAndExpand() {
    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });
    await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Feature: feat-1"));
  }

  test("shows a Create Publish Job link targeting ?patch=<id> when GitHub publish is configured and connected", async () => {
    mockAppliedPatch({
      appStatus: { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" },
      connections: [{ id: 1, status: "connected" }],
    });
    await renderAndExpand();

    const link = await screen.findByTestId("patch-publish-link");
    expect(link).toHaveAttribute("href", "/github?patch=20");
    expect(screen.queryByTestId("patch-manual-git-instructions")).not.toBeInTheDocument();
  });

  test("falls back to manual git instructions when GitHub is not configured", async () => {
    mockAppliedPatch();
    await renderAndExpand();

    expect(await screen.findByTestId("patch-manual-git-instructions")).toBeInTheDocument();
    expect(screen.queryByTestId("patch-publish-link")).not.toBeInTheDocument();
  });

  test("falls back to manual git instructions when GitHub is configured but has no connected connection", async () => {
    mockAppliedPatch({
      appStatus: { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" },
      connections: [{ id: 1, status: "error" }],
    });
    await renderAndExpand();

    expect(await screen.findByTestId("patch-manual-git-instructions")).toBeInTheDocument();
    expect(screen.queryByTestId("patch-publish-link")).not.toBeInTheDocument();
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
      head_relation: "behind", commits_behind: 3,
      next_actions: ["Repository HEAD changed; create a new snapshot before generating new analysis or patches."],
    }));

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByTestId("snapshot-stale-badge")).toBeInTheDocument();
    expect(within(hub).getByTestId("repository-lag")).toHaveTextContent("3 commits behind");
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
      head_relation: "same", commits_behind: 0,
      snapshot_stale: false, symbols_stale: false, next_actions: [],
    }));

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByText("Up to date")).toBeInTheDocument();
    expect(within(hub).getByTestId("repository-lag")).toHaveTextContent("Latest");
  });

  test("starts the explicit snapshot and symbol resync from one button", async () => {
    mockApi.get.mockImplementation(baseGet({
      configured: true, repo_path: "/repos/alpha",
      current_head: "abc1234000", head_error: null,
      working_tree_dirty: false, dirty_file_count: 0, dirty_sample: [],
      latest_snapshot: { id: 12, commit_sha: "abc1234000", status: "ready", created_at: 1 },
      latest_indexed_snapshot: null,
      understanding_snapshot_id: null, understanding_status: null,
      head_relation: "same", commits_behind: 0,
      snapshot_stale: false, symbols_stale: true, next_actions: [],
    }));
    mockApi.post.mockResolvedValue({
      id: 7, system_id: 1, snapshot_id: null, status: "queued", error: null,
      stale_capability_count: 0, created_at: 1, started_at: null, completed_at: null,
    });

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("repository-resync-button"));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/resync");
    });
  });

  test("guides capability review and understanding build after resync", async () => {
    const getBase = baseGet({
      configured: true, repo_path: "/repos/alpha",
      current_head: "def5678000", head_error: null,
      working_tree_dirty: false, dirty_file_count: 0, dirty_sample: [],
      latest_snapshot: { id: 13, commit_sha: "def5678000", status: "ready", created_at: 2 },
      latest_indexed_snapshot: { id: 13, commit_sha: "def5678000", status: "ready", created_at: 2 },
      understanding_snapshot_id: 12, understanding_status: "completed",
      head_relation: "same", commits_behind: 0,
      snapshot_stale: false, symbols_stale: false, next_actions: [],
    });
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/resync/latest") return Promise.resolve({
        id: 7, system_id: 1, snapshot_id: 13, status: "completed", error: null,
        stale_capability_count: 2, created_at: 1, started_at: 1, completed_at: 2,
      });
      return getBase(path);
    });

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    const guidance = await screen.findByTestId("stale-capability-guidance");
    expect(guidance).toHaveTextContent("2 capabilities still use an older snapshot");
    expect(within(guidance).getByText("Review Capability Map").closest("a"))
      .toHaveAttribute("href", "/capability-map");
    expect(within(guidance).getByText("Build System Understanding").closest("a"))
      .toHaveAttribute("href", "/system-understanding?fix=build");
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
    expect(gapLink.closest("a")).toHaveAttribute("href", "/system-understanding?capability=doc-analysis");

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
    // Issue #229: mirrors the server's update-understanding gate. Defaults
    // to blocked, matching this session's default confirmed/no-revision
    // shape; tests exercising the unblocked path override it explicitly.
    understanding_update_available: false,
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
      session: { answers_revised_at: 123, understanding_update_available: true },
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
        return Promise.resolve(interviewSession({
          answers_revised_at: null,
          last_error: null,
          understanding_update_available: false,
        }));
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
    expect(refreshButton).not.toBeDisabled();
    fireEvent.click(refreshButton);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/7/update-understanding", {});
    });
    expect(await screen.findByTestId("answer-revision-reflected-banner")).toBeInTheDocument();
  });

  test("disables understanding refresh after confirmation until an answer is revised (Issue #229)", async () => {
    mockInterviewApi();
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
    expect(refreshButton).toBeDisabled();
    expect(refreshButton).toHaveAttribute(
      "title", "新しい回答(修正・追加回答)がある場合にのみ、理解を再構築できます",
    );
    expect(await screen.findByTestId("understanding-refresh-blocked-reason"))
      .toHaveTextContent("次は提案を生成またはレビューしてください");
    expect(screen.getByTestId("next-action")).toHaveTextContent("各提案を承認・編集・却下してください");
  });

  test("re-enables understanding refresh when the server reports new Q&A activity since confirmation, even without answers_revised_at (Issue #229/#263)", async () => {
    // The server's understanding_update_available flag (single source of
    // truth shared with the update-understanding 409 gate) can open from a
    // first-time Q&A-panel answer or a new Runtime Reality Check answer
    // given after confirmation -- neither ever sets answers_revised_at. The
    // Dashboard must trust this server-computed flag rather than
    // re-deriving availability from answers_revised_at alone.
    mockInterviewApi({
      session: { answers_revised_at: null, understanding_update_available: true },
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
    expect(refreshButton).not.toBeDisabled();
    expect(screen.queryByTestId("understanding-refresh-blocked-reason")).not.toBeInTheDocument();
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

describe("Sidebar phase-linked navigation (Issue #257)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  function stateWithPhase(userPhase: string) {
    return {
      system_id: 1, generated_at: 1, overall_severity: "ok",
      severity_counts: {}, items: [], primary_item: null,
      notification_items: [], page_items: {},
      user_phase: userPhase,
      phases: [
        { phase: "setup", complete: true },
        { phase: "preparation", complete: true },
        { phase: "instrumentation", complete: true },
        { phase: "observation", complete: true },
        { phase: "evaluation", complete: true },
        { phase: "publish", complete: true },
      ],
    };
  }

  // Renders the Sidebar with /system-state resolving to `stateResponse`
  // (or null, simulating an older server / not-yet-loaded / errored state).
  async function renderSidebar(stateResponse: unknown | null) {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state" ? Promise.resolve(stateResponse) : Promise.resolve(null),
    );
    const { Sidebar } = await import("@/components/layout/sidebar");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const result = render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/"]}>
          <Sidebar />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    // Let the /system-state query settle before assertions.
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/system-state"));
    return result;
  }

  test("renders the new Setup/Understand/Instrument/Observe & Evaluate/Publish/Other groups with every existing route", async () => {
    await renderSidebar(null);

    expect(screen.getByTestId("sidebar-group-setup")).toBeTruthy();
    expect(screen.getByTestId("sidebar-group-understand")).toBeTruthy();
    expect(screen.getByTestId("sidebar-group-instrument")).toBeTruthy();
    // "Observe & Evaluate" slugifies (lowercase, whitespace runs -> "-") to
    // exactly this — the ampersand itself is not stripped by the existing
    // slug logic in sidebar.tsx.
    expect(screen.getByTestId("sidebar-group-observe-&-evaluate")).toBeTruthy();
    expect(screen.getByTestId("sidebar-group-publish")).toBeTruthy();
    expect(screen.getByTestId("sidebar-group-other")).toBeTruthy();

    const nav = screen.getByTestId("sidebar-nav");
    for (const label of [
      "Overview", "Setup Guide", "Repository", "Settings",
      "System Understanding", "Capability Map", "Feature Map", "Flow Explorer", "Interview",
      "Probe Planner", "Probe Patterns", "Connect SDK",
      "Components / Traces", "Trace Lineage", "Trace Analyzers", "Experiments",
      "Simulation Workbench", "AI Candidate Studio", "Decision Workspace",
      "GitHub", "Generate",
    ]) {
      expect(within(nav).getByText(label)).toBeTruthy();
    }
    // Old label is gone, replaced by "Components / Traces".
    expect(within(nav).queryByText("Components")).toBeNull();

    // Group membership per the #257 design mapping.
    expect(within(screen.getByTestId("sidebar-group-instrument")).getByText("Probe Patterns")).toBeTruthy();
    expect(within(screen.getByTestId("sidebar-group-publish")).getByText("GitHub")).toBeTruthy();
    expect(within(screen.getByTestId("sidebar-group-other")).getByText("Generate")).toBeTruthy();
  });

  test("preparation phase: Understand is current, Setup is reached, later groups are future and dimmed", async () => {
    await renderSidebar(stateWithPhase("preparation"));

    const setup = screen.getByTestId("sidebar-group-setup");
    const understand = screen.getByTestId("sidebar-group-understand");
    const instrument = screen.getByTestId("sidebar-group-instrument");
    const observeEvaluate = screen.getByTestId("sidebar-group-observe-&-evaluate");
    const publish = screen.getByTestId("sidebar-group-publish");

    // /system-state resolves asynchronously; wait for the re-render it
    // triggers (these DOM nodes are stable across the update, so the same
    // references keep working once settled).
    await waitFor(() => expect(understand.getAttribute("data-phase-state")).toBe("current"));

    expect(setup.getAttribute("data-phase-state")).toBe("reached");
    expect(instrument.getAttribute("data-phase-state")).toBe("future");
    expect(observeEvaluate.getAttribute("data-phase-state")).toBe("future");
    expect(publish.getAttribute("data-phase-state")).toBe("future");

    // Dimmed (future) groups carry a reduced-opacity class; reached/current do not.
    expect(instrument.className).toMatch(/opacity-50/);
    expect(observeEvaluate.className).toMatch(/opacity-50/);
    expect(publish.className).toMatch(/opacity-50/);
    expect(setup.className).not.toMatch(/opacity-50/);
    expect(understand.className).not.toMatch(/opacity-50/);

    // The current group's heading shows the sidebar's own 現在地表示 marker.
    expect(within(understand).getByText("現在")).toBeTruthy();
    expect(within(setup).queryByText("現在")).toBeNull();

    // Never hidden: every item in a dimmed, not-yet-reached group is still a
    // clickable link with its real href.
    const probePlannerLink = within(instrument).getByText("Probe Planner").closest("a");
    expect(probePlannerLink).toBeTruthy();
    expect(probePlannerLink?.getAttribute("href")).toBe("/probe-planner");
  });

  test("publish phase: nothing is dimmed (Publish is current, everything else already reached)", async () => {
    await renderSidebar(stateWithPhase("publish"));

    const publish = screen.getByTestId("sidebar-group-publish");
    // /system-state resolves asynchronously; wait for the re-render it triggers.
    await waitFor(() => expect(publish.getAttribute("data-phase-state")).toBe("current"));

    for (const testId of [
      "sidebar-group-setup", "sidebar-group-understand", "sidebar-group-instrument",
      "sidebar-group-observe-&-evaluate",
    ]) {
      const group = screen.getByTestId(testId);
      expect(group.getAttribute("data-phase-state")).toBe("reached");
      expect(group.className).not.toMatch(/opacity-50/);
    }
    expect(publish.getAttribute("data-phase-state")).toBe("current");
    expect(publish.className).not.toMatch(/opacity-50/);
    expect(within(publish).getByText("現在")).toBeTruthy();
  });

  test("no system-state data: every group's data-phase-state is absent or none, and nothing is dimmed", async () => {
    await renderSidebar(null);

    for (const testId of [
      "sidebar-group-setup", "sidebar-group-understand", "sidebar-group-instrument",
      "sidebar-group-observe-&-evaluate", "sidebar-group-publish",
    ]) {
      const group = screen.getByTestId(testId);
      const state = group.getAttribute("data-phase-state");
      expect(state === null || state === "none").toBe(true);
      expect(group.className).not.toMatch(/opacity-50/);
    }
    expect(screen.queryByText("現在")).toBeNull();

    // Every route is still rendered as a real, clickable link.
    const nav = screen.getByTestId("sidebar-nav");
    expect(within(nav).getAllByRole("link").length).toBeGreaterThan(15);
  });

  test("Generate carries a Legacy badge, a pointer to AI Candidate Studio, and lives in Other", async () => {
    await renderSidebar(null);

    const other = screen.getByTestId("sidebar-group-other");
    expect(within(other).getByText("Generate")).toBeTruthy();
    const badge = within(other).getByTestId("sidebar-legacy-badge");
    expect(badge.textContent).toBe("Legacy");

    const generateLink = within(other).getByText("Generate").closest("a");
    expect(generateLink?.getAttribute("href")).toBe("/generation");
    expect(generateLink?.getAttribute("title")).toBe("旧世代の候補生成。AI Candidate Studio を推奨");
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
          head_relation: "behind", commits_behind: 2,
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
    expect(screen.getByTestId("context-header-freshness")).toHaveTextContent("2 commits behind");
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
          { action: "Interview を開く", link: "/interview" },
          { action: "ソースメタデータを追加", link: "/interview" },
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
          { action: "ドキュメントの根拠を確認", link: null },
          { action: "実装 issue を作成", link: null },
        ],
      },
    ],
    gap_summary: [
      { gap_type: "unclassified_entrypoint", count: 1 },
      { gap_type: "docs_only", count: 1 },
    ],
    metadata_coverage: { symbol_count: 42, symbols_with_source_metadata: 5, entrypoint_count: 10, entrypoints_with_capability_link: 3 },
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
  };

  const gapResponse = {
    ...completeResponse,
    gaps: [
      {
        gap_type: "docs_only", severity: "warning", title: "Documented but missing: Feature X",
        node_name: "Feature X", notes: null, capability_key: null,
        doc_refs: [{ path: "README.md", start_line: 1, end_line: 5 }],
        symbol_refs: [], entrypoint_refs: [], code_refs: [],
        next_actions: [{ action: "ドキュメントの根拠を確認", link: null }],
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

  // Issue #239/#240: `repository.configuration.missing` now carries
  // `related_pipeline_steps: ["repository_configured"]`, so its CTA is
  // driven by the matching SystemStateItem like every other step -- there is
  // no hardcoded fallback map.
  const repositoryMissingItem: SystemStateItem = {
    state_id: "repository.configuration.missing",
    state_group: "repository",
    severity: "warning",
    status: "missing",
    user_action_kind: "configure",
    intervention_timing: "now",
    subject: "リポジトリ設定",
    summary: "対象リポジトリが未設定です。",
    detail: "対象リポジトリが未設定です。",
    impact: "",
    remediation: "Repository タブでリポジトリを設定してください。",
    evidence: {},
    target_ui: { route: "/repository", anchor: "repo-config", action_label: "Configure repository" },
    related_checks: [],
    related_pipeline_steps: ["repository_configured"],
    source: "system_state",
    dedupe_key: "repository.configuration",
    scope: "global",
    decision_method: "deterministic",
    phase: "setup",
  };

  test("shows a CTA only on the first incomplete step when the pipeline is all missing", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(emptyResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [repositoryMissingItem],
        primary_item: repositoryMissingItem, notification_items: [repositoryMissingItem],
        page_items: { "/system-understanding": [repositoryMissingItem] },
      });
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("pipeline-checklist")).toBeTruthy();
    });

    const cta = screen.getByTestId("pipeline-cta-repository_configured");
    expect(cta.textContent).toContain(repositoryMissingItem.target_ui!.action_label!);
    expect(cta.getAttribute("href")).toBe("/repository?fix=repo-config");

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
    const buildAvailableItem: SystemStateItem = {
      state_id: "pipeline.documentation_indexed.missing",
      state_group: "pipeline",
      severity: "warning",
      status: "missing",
      user_action_kind: "build",
      intervention_timing: "now",
      subject: "Documentation indexed",
      summary: "Documentation がまだ index されていません。",
      detail: "",
      impact: "",
      remediation: "Build / Refresh を実行してください。",
      evidence: {},
      target_ui: { route: "/system-understanding", anchor: null, action_label: "Run Build / Refresh" },
      related_checks: [],
      related_pipeline_steps: ["documentation_indexed"],
      source: "system_state",
      dedupe_key: "pipeline.documentation_indexed",
      scope: "global",
      decision_method: "deterministic",
      phase: "preparation",
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(midResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [buildAvailableItem],
        primary_item: buildAvailableItem, notification_items: [buildAvailableItem],
        page_items: { "/system-understanding": [buildAvailableItem] },
      });
      return Promise.resolve(null);
    });
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
    const buildAvailableItem: SystemStateItem = {
      state_id: "pipeline.documentation_indexed.missing",
      state_group: "pipeline",
      severity: "warning",
      status: "missing",
      user_action_kind: "build",
      intervention_timing: "now",
      subject: "Documentation indexed",
      summary: "Documentation がまだ index されていません。",
      detail: "",
      impact: "",
      remediation: "Build / Refresh を実行してください。",
      evidence: {},
      target_ui: { route: "/system-understanding", anchor: null, action_label: "Run Build / Refresh" },
      related_checks: [],
      related_pipeline_steps: ["documentation_indexed"],
      source: "system_state",
      dedupe_key: "pipeline.documentation_indexed",
      scope: "global",
      decision_method: "deterministic",
      phase: "preparation",
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
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [buildAvailableItem],
        primary_item: buildAvailableItem, notification_items: [buildAvailableItem],
        page_items: { "/system-understanding": [buildAvailableItem] },
      });
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

  test("prefers the server-provided pipeline step label over the client fallback map (Issue #240)", async () => {
    // One step carries a server `label`; the rest omit it, proving the
    // client-side STEP_LABELS map still covers steps an older server
    // doesn't label yet.
    const labeledResponse = {
      ...completeResponse,
      pipeline: completeResponse.pipeline.map((s) =>
        s.step === "symbols_indexed" ? { ...s, label: "コードシンボル索引" } : s,
      ),
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(labeledResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("pipeline-expand"));
    const checklist = await screen.findByTestId("pipeline-checklist");
    expect(checklist.textContent).toContain("コードシンボル索引");
    // A step without a server label still falls back to STEP_LABELS.
    expect(checklist.textContent).toContain("Snapshot ready");
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

  test("gap worklist defaults to untriaged, exposes all filter, and records manual triage", async () => {
    const triageResponse = {
      ...gapWorklistResponse,
      gaps: gapWorklistResponse.gaps.map((gap, index) => ({
        ...gap,
        gap_key: index === 0
          ? "unclassified_entrypoint|entrypoint|http_route:GET /items"
          : "docs_only|document|docs/design.md::Auth [node:abc]",
        content_fingerprint: index === 0 ? "a".repeat(64) : "b".repeat(64),
        triage_status: index === 0 ? "open" : "dismissed",
        triage_decision: index === 0 ? null : {
          id: 2, system_id: 1, snapshot_id: 5,
          gap_key: "docs_only|document|docs/design.md::Auth [node:abc]",
          content_fingerprint: "b".repeat(64), status: "dismissed",
          decided_by_user_id: 1, decision_method: "manual", created_at: 1,
        },
      })),
      gap_summary: [{ gap_type: "unclassified_entrypoint", count: 1 }],
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(triageResponse)
        : Promise.resolve(null),
    );
    mockApi.post.mockResolvedValue({
      id: 3, system_id: 1, snapshot_id: 5,
      gap_key: triageResponse.gaps[0].gap_key,
      content_fingerprint: "a".repeat(64), status: "acknowledged",
      decided_by_user_id: 1, decision_method: "manual", created_at: 2,
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("gap-filter-open")).toHaveTextContent("Untriaged (1)");
    expect(screen.getAllByTestId("gap-card")).toHaveLength(1);
    expect(screen.getByTestId("gap-triage-status")).toHaveTextContent("open");

    fireEvent.click(screen.getByTestId("gap-filter-all"));
    expect(screen.getAllByTestId("gap-card")).toHaveLength(2);
    expect(screen.getAllByTestId("gap-triage-status")[1]).toHaveTextContent("dismissed");

    fireEvent.click(screen.getByTestId("gap-triage-acknowledge"));
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/repository/system-understanding/gap-triage",
      {
        gap_key: triageResponse.gaps[0].gap_key,
        content_fingerprint: "a".repeat(64),
        status: "acknowledged",
      },
    ));
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

    expect(screen.getByText("Interview を開く")).toBeTruthy();
    expect(screen.getByText("ドキュメントの根拠を確認")).toBeTruthy();
  });

  test("ignores legacy next-step fields from an old server response (Issue #239 removal contract)", async () => {
    // A response that still carries the removed fields (e.g. an older
    // Control Server): the UI must not resurrect any of the legacy
    // projections from them -- no per-stage Next Actions list, no
    // primary-action card, no refresh-recommended banner. The canonical
    // next-step surface is the SystemStateBanner fed by /system-state,
    // which is absent here, so no next-step CTA of any kind renders.
    const legacyResponse = {
      ...gapWorklistResponse,
      next_actions: [
        { action: "Review probe plan", reason: "Probe plan #7 is awaiting review", category: "observe", link: "/probe-planner?plan=7" },
      ],
      primary_action: { action: "Review probe plan", reason: "…", category: "observe", link: "/probe-planner?plan=7" },
      understanding_refresh_recommended: true,
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(legacyResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("stage-section-understand")).toBeTruthy();
    });

    expect(screen.queryByText("Review probe plan")).toBeNull();
    expect(screen.queryByTestId("stage-next-actions-observe")).toBeNull();
    expect(screen.queryByTestId("primary-action")).toBeNull();
    expect(screen.queryByTestId("refresh-recommended-banner")).toBeNull();
    expect(screen.queryByTestId("system-state-banner")).toBeNull();
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

    // Existing pipeline checklist / gap worklist / capabilities elements
    // survive the reorganization. With every step complete the checklist is
    // collapsed by default (Issue #211) and expands on demand.
    fireEvent.click(screen.getByTestId("pipeline-expand"));
    expect(await screen.findByTestId("pipeline-checklist")).toBeTruthy();
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
      { action: "ドキュメントの根拠を確認", link: null },
      { action: "実装 issue を作成", link: null },
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

  // ── Canonical page banner replaces the primary action card (Issue #239) ──

  const purposeMissingItem: SystemStateItem = {
    state_id: "understanding.purpose.missing_baseline",
    state_group: "understanding",
    severity: "warning",
    status: "missing",
    user_action_kind: "confirm",
    intervention_timing: "before_next_step",
    subject: "System Purpose",
    summary: "System Purpose が未定義です。",
    detail: "System Purpose が未定義です。確認済み・未確認いずれの baseline もありません。",
    impact: "",
    remediation: "Interview で System Purpose を定義・確認してください。",
    evidence: {},
    target_ui: { route: "/interview", anchor: "interview-purpose", action_label: "Interview でSystem Purposeを定義" },
    display_routes: ["/system-understanding"],
    related_checks: ["system_purpose"],
    related_pipeline_steps: [],
    source: "system_state",
    dedupe_key: "",
    scope: "global",
    decision_method: "deterministic",
    phase: "preparation",
  };

  test("renders a navigate-kind page item as the canonical banner with its server target", async () => {
    window.history.pushState({}, "", "/system-understanding");
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(completeResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [purposeMissingItem],
        primary_item: purposeMissingItem, notification_items: [purposeMissingItem],
        page_items: { "/system-understanding": [purposeMissingItem], "/interview": [purposeMissingItem] },
        user_phase: "preparation",
        phases: [
          { phase: "setup", complete: true },
          { phase: "preparation", complete: false },
          { phase: "instrumentation", complete: false },
          { phase: "observation", complete: false },
          { phase: "evaluation", complete: false },
          { phase: "publish", complete: false },
        ],
      });
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const banner = await screen.findByTestId("system-state-banner");
    expect(banner.textContent).toContain(purposeMissingItem.summary);
    expect(banner.textContent).toContain(purposeMissingItem.remediation);

    const cta = screen.getByTestId("system-state-action-understanding.purpose.missing_baseline");
    expect(cta.textContent).toBe("Interview でSystem Purposeを定義");
    fireEvent.click(cta);
    await waitFor(() => expect(window.location.pathname).toBe("/interview"));
    const params = new URLSearchParams(window.location.search);
    expect(params.get("fix")).toBe("interview-purpose");
    expect(params.get("diagnostic")).toBe("system_purpose");
  });

  test("a build-kind page item's banner action triggers build.mutate", async () => {
    const rebuildItem: SystemStateItem = {
      state_id: "interview.materialized.rebuild_required",
      state_group: "interview",
      severity: "warning",
      status: "stale",
      user_action_kind: "build",
      intervention_timing: "after_build",
      subject: "Interview materialization",
      summary: "Interview の反映後に System Understanding の再 build が必要です。",
      detail: "最新の Interview materialization が直近の完了済み build より新しいため、理解結果を更新する必要があります。",
      impact: "",
      remediation: "System Understanding で Build / Refresh を実行してください。",
      evidence: {},
      target_ui: { route: "/system-understanding", anchor: "build", action_label: "Build / Refresh を実行" },
      related_checks: [],
      related_pipeline_steps: ["capability_hierarchy_ready"],
      source: "system_state",
      dedupe_key: "interview.materialization.build",
      scope: "global",
      decision_method: "deterministic",
      phase: "preparation",
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(completeResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [rebuildItem], primary_item: rebuildItem,
        notification_items: [rebuildItem], page_items: { "/system-understanding": [rebuildItem] },
      });
      return Promise.resolve(null);
    });
    mockApi.post.mockImplementation((path: string) =>
      path === "/repository/system-understanding/build"
        ? Promise.resolve(completeResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const cta = await screen.findByTestId("system-state-action-interview.materialized.rebuild_required");
    expect(cta.textContent).toBe("Build / Refresh を実行");

    fireEvent.click(cta);
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/repository/system-understanding/build");
    });
  });

  test("shows no banner when system-state has no item for this page", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(completeResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "ok",
        severity_counts: {}, items: [], primary_item: null,
        notification_items: [], page_items: {},
      });
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("Test System")).toBeTruthy();
    });

    expect(screen.queryByTestId("system-state-banner")).toBeNull();
    expect(screen.queryByTestId("primary-action")).toBeNull();
  });

  // ── Stage status badges + counts summary (Issue #202) ──────────────

  test("renders stage status badges and a heading counts line for understand/observe", async () => {
    const response = {
      ...completeResponse,
      stages: [
        { stage: "understand", status: "complete", counts: { gaps: 0 } },
        { stage: "observe", status: "in_progress", counts: { entrypoints: 3, unclassified: 1 } },
        { stage: "instrument", status: "not_started", counts: { proposed: 0, approved_without_patch: 0, validated: 0 } },
        { stage: "evaluate", status: "not_started", counts: { undecided: 0, decided: 0 } },
      ],
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(response)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("stage-status-understand")).toBeTruthy();
    });

    expect(screen.getByTestId("stage-status-understand").textContent).toBe("Complete");
    expect(screen.getByTestId("stage-status-observe").textContent).toBe("In progress");
    expect(screen.getByTestId("stage-status-instrument").textContent).toBe("Not started");
    expect(screen.getByTestId("stage-status-evaluate").textContent).toBe("Not started");

    expect(screen.getByTestId("stage-counts-observe").textContent).toContain("entrypoints: 3");
    expect(screen.getByTestId("stage-counts-observe").textContent).toContain("unclassified: 1");

    // Instrument/Evaluate show their counts via the dedicated summary block
    // below instead of the generic heading counts line (no duplication).
    expect(screen.queryByTestId("stage-counts-instrument")).toBeNull();
    expect(screen.queryByTestId("stage-counts-evaluate")).toBeNull();
  });

  test("shows Instrument/Evaluate counts summaries linking to Probe Planner/Experiments when non-zero", async () => {
    const response = {
      ...completeResponse,
      stages: [
        { stage: "understand", status: "complete", counts: { gaps: 0 } },
        { stage: "observe", status: "complete", counts: { entrypoints: 3, unclassified: 0 } },
        { stage: "instrument", status: "in_progress", counts: { proposed: 2, approved_without_patch: 1, validated: 0 } },
        { stage: "evaluate", status: "in_progress", counts: { undecided: 1, decided: 2 } },
      ],
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(response)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("stage-summary-instrument")).toBeTruthy();
    });

    const instrumentSummary = screen.getByTestId("stage-summary-instrument");
    expect(instrumentSummary.textContent).toContain("Proposed");
    expect(instrumentSummary.textContent).toContain("2");
    expect(instrumentSummary.textContent).toContain("Approved without patch");
    expect(instrumentSummary.textContent).toContain("1");
    expect(instrumentSummary.textContent).toContain("Validated");
    const instrumentLinks = within(instrumentSummary).getAllByRole("link");
    expect(instrumentLinks.length).toBeGreaterThan(0);
    for (const link of instrumentLinks) {
      expect(link.getAttribute("href")).toBe("/probe-planner");
    }

    const evaluateSummary = screen.getByTestId("stage-summary-evaluate");
    expect(evaluateSummary.textContent).toContain("Undecided");
    expect(evaluateSummary.textContent).toContain("1");
    expect(evaluateSummary.textContent).toContain("Decided");
    expect(evaluateSummary.textContent).toContain("2");
    const evaluateLinks = within(evaluateSummary).getAllByRole("link");
    expect(evaluateLinks.length).toBeGreaterThan(0);
    for (const link of evaluateLinks) {
      expect(link.getAttribute("href")).toBe("/experiments");
    }
  });

  test("falls back to the original description text for Instrument/Evaluate when counts are all zero", async () => {
    const response = {
      ...completeResponse,
      stages: [
        { stage: "understand", status: "complete", counts: { gaps: 0 } },
        { stage: "observe", status: "complete", counts: { entrypoints: 3, unclassified: 0 } },
        { stage: "instrument", status: "not_started", counts: { proposed: 0, approved_without_patch: 0, validated: 0 } },
        { stage: "evaluate", status: "not_started", counts: { undecided: 0, decided: 0 } },
      ],
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(response)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("stage-summary-instrument")).toBeTruthy();
    });

    expect(screen.getByTestId("stage-summary-instrument").textContent).toContain(
      "Probe plan and patch status live in Probe Planner",
    );
    expect(screen.getByTestId("stage-summary-evaluate").textContent).toContain(
      "Trace comparisons, experiment runs, and adoption decisions live in Experiments",
    );
  });

  test("renders with no stage status badges when data.stages is missing (backward compat)", async () => {
    // completeResponse predates Issue #202 and has no `stages` field.
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

    expect(screen.queryByTestId("stage-status-understand")).toBeNull();
    expect(screen.queryByTestId("stage-status-observe")).toBeNull();
    expect(screen.queryByTestId("stage-status-instrument")).toBeNull();
    expect(screen.queryByTestId("stage-status-evaluate")).toBeNull();
    expect(screen.getByTestId("stage-summary-instrument").textContent).toContain(
      "Probe plan and patch status live in Probe Planner",
    );
    expect(screen.getByTestId("stage-summary-evaluate").textContent).toContain(
      "Trace comparisons, experiment runs, and adoption decisions live in Experiments",
    );
  });

  // ── Banner behavior while a build is running (Issue #239) ───────────
  //
  // The removed primary_action's rule 2 blanked the CTA whenever a build was
  // queued/running, unconditionally. The banner now shows the exact same
  // canonical page_items[route][0]/primary_item projection as every other
  // surface (e.g. the Pipeline Checklist) with no client-side suppression
  // keyed on buildRunning -- an item only disappears when the server itself
  // resolves or phase-suppresses it.

  const runningBuildResponse = {
    id: 1, job_id: 1, run_id: 1, system_id: 1, snapshot_id: 5,
    status: "running", current_step: "claim_scan", error: null,
    cancel_requested: false, is_stuck: false,
    heartbeat_at: Date.now() / 1000, started_at: Date.now() / 1000,
    completed_at: null, created_at: Date.now() / 1000,
    steps: [], llm_tasks: { total: 0, pending: 0, running: 0, completed: 0, failed: 0, cancelled: 0, reused: 0 },
    artifact_counts: {},
  };

  test("keeps a warning-level page banner visible while a build is running", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(completeResponse);
      if (path === "/repository/system-understanding/build/latest") return Promise.resolve(runningBuildResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [purposeMissingItem],
        primary_item: purposeMissingItem, notification_items: [purposeMissingItem],
        page_items: { "/system-understanding": [purposeMissingItem] },
      });
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("build-progress")).toBeTruthy();
    });

    expect(screen.getByTestId("system-state-banner").textContent).toContain(purposeMissingItem.summary);
  });

  test("keeps an error-level page banner visible while a build is running", async () => {
    const errorItem: SystemStateItem = {
      ...purposeMissingItem,
      state_id: "repository.head.unreadable",
      state_group: "repository",
      severity: "error",
      status: "failed",
      user_action_kind: "configure",
      summary: "Repository HEAD を読み取れません。",
      remediation: "Repository のパスとアクセス権を確認してください。",
      target_ui: { route: "/repository", anchor: "repo-config", action_label: "Repository 設定を確認" },
      related_checks: ["repository_path"],
      dedupe_key: "repository.head",
      phase: "setup",
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(completeResponse);
      if (path === "/repository/system-understanding/build/latest") return Promise.resolve(runningBuildResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "error",
        severity_counts: { error: 1 }, items: [errorItem],
        primary_item: errorItem, notification_items: [errorItem],
        page_items: { "/system-understanding": [errorItem] },
      });
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("build-progress")).toBeTruthy();
    });

    expect(screen.getByTestId("system-state-banner").textContent).toContain(errorItem.summary);
  });

  // ── Canonical banner is the only rebuild-required surface (Issue #239) ──

  test("renders the rebuild-required cause once, via the canonical SystemStateBanner only", async () => {
    const rebuildItem: SystemStateItem = {
      state_id: "interview.materialized.rebuild_required",
      state_group: "interview",
      severity: "warning",
      status: "rebuild_required",
      user_action_kind: "build",
      intervention_timing: "now",
      subject: "System Understanding",
      summary: "Interview 反映後の再 build が必要です。",
      detail: "Materialized interview changes are newer than the latest completed build.",
      impact: "現在の理解には確定済みの Interview の変更が反映されていません。",
      remediation: "Build / Refresh を実行してください。",
      evidence: {},
      target_ui: { route: "/system-understanding", anchor: "build", action_label: "Build / Refresh" },
      related_checks: [],
      related_pipeline_steps: [],
      source: "system_state",
      dedupe_key: "interview.materialized.rebuild_required",
      scope: "global",
      decision_method: "deterministic",
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(completeResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [rebuildItem], primary_item: rebuildItem,
        notification_items: [rebuildItem], page_items: { "/system-understanding": [rebuildItem] },
      });
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    // The canonical banner renders the root cause...
    await waitFor(() => {
      expect(screen.getByTestId("system-state-banner")).toBeTruthy();
    });
    expect(screen.getByTestId("system-state-banner").textContent).toContain(rebuildItem.summary);

    // ...exactly once: the legacy Issue #203 banner no longer exists at all
    // (Issue #239 removed it with the understanding_refresh_recommended field).
    expect(screen.queryByTestId("refresh-recommended-banner")).toBeNull();
    expect(screen.getAllByText(rebuildItem.summary)).toHaveLength(1);
  });

  test("shows the capability-empty canonical guidance on System Understanding and keeps its Interview CTA", async () => {
    window.history.pushState({}, "", "/system-understanding");
    const capabilityEmptyItem: SystemStateItem = {
      state_id: "pipeline.capability_hierarchy.empty",
      state_group: "pipeline",
      severity: "warning",
      status: "missing",
      user_action_kind: "confirm",
      intervention_timing: "before_next_step",
      subject: "Capability hierarchy",
      summary: "Capability hierarchy completed, but has 0 capabilities.",
      detail: "No capability nodes were generated.",
      impact: "Core Capabilities are not yet defined.",
      remediation: "Interview で Core Capabilities を確認してください。",
      evidence: { capability_count: 0 },
      target_ui: { route: "/interview", anchor: "interview-capabilities", action_label: "Interview で Core Capabilities を確認" },
      display_routes: ["/system-understanding"],
      related_checks: [],
      related_pipeline_steps: ["capability_hierarchy_ready"],
      source: "system_state",
      dedupe_key: "",
      scope: "global",
      decision_method: "deterministic",
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(completeResponse);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [capabilityEmptyItem], primary_item: capabilityEmptyItem,
        notification_items: [capabilityEmptyItem],
        page_items: {
          "/system-understanding": [capabilityEmptyItem],
          "/interview": [capabilityEmptyItem],
        },
      });
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("system-state-banner")).toHaveTextContent(capabilityEmptyItem.summary);
    const cta = screen.getByTestId("system-state-action-pipeline.capability_hierarchy.empty");
    expect(cta).toHaveTextContent("Interview で Core Capabilities を確認");
    fireEvent.click(cta);
    await waitFor(() => expect(window.location.pathname).toBe("/interview"));
    expect(new URLSearchParams(window.location.search).get("fix")).toBe("interview-capabilities");
  });

  test("renders gap_trend increase/decrease chips in the gap worklist", async () => {
    const response = {
      ...gapWorklistResponse,
      gap_trend: [
        { gap_type: "docs_only", current: 8, previous: 12 },
        { gap_type: "code_only", current: 3, previous: 1 },
      ],
    };
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(response)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId("gap-trend")).toBeTruthy();
    });

    const trend = screen.getByTestId("gap-trend");
    expect(trend.textContent).toContain("docs_only");
    expect(trend.textContent).toContain("12");
    expect(trend.textContent).toContain("8");
    expect(trend.textContent).toContain("code_only");
    expect(trend.textContent).toContain("1");
    expect(trend.textContent).toContain("3");
  });

  test("renders no gap trend section when gap_trend is empty or missing (backward compat)", async () => {
    // gapWorklistResponse predates Issue #203 and has no `gap_trend` field.
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

    expect(screen.queryByTestId("gap-trend")).toBeNull();
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

  // Issue #239: the badge consumes GET /system-state only. Diagnostic checks
  // reach it as server-projected `diagnostic.<check_id>` StateItems; the
  // `/system-diagnostics` response is consulted solely to open the env-fix
  // dialog for a dialog-kind check referenced by `related_checks`.

  const projectedStateItem = (
    overrides: Partial<SystemStateItem> & Pick<SystemStateItem, "state_id" | "severity" | "summary">,
  ): SystemStateItem => ({
    state_group: "configuration",
    status: "missing",
    user_action_kind: "inspect",
    intervention_timing: "before_next_step",
    subject: overrides.summary,
    detail: overrides.summary,
    impact: "",
    remediation: "",
    evidence: {},
    target_ui: null,
    related_checks: [],
    related_pipeline_steps: [],
    source: "system_diagnostics",
    dedupe_key: `diagnostic.${overrides.state_id}`,
    scope: "global",
    decision_method: "deterministic",
    ...overrides,
  });

  const llmItem = projectedStateItem({
    state_id: "diagnostic.intelligence_llm_config",
    severity: "error",
    summary: "Intelligence reasoning model configuration",
    remediation: "Set INTELLIGENCE_LLM_PROVIDER and INTELLIGENCE_LLM_MODEL to a reasoning-capable pair.",
    user_action_kind: "configure",
    evidence: { diagnostic_category: "llm", fix_kind: "dialog" },
    related_checks: ["intelligence_llm_config"],
    dedupe_key: "diagnostic.intelligence_llm_config",
  });
  const snapshotItem = projectedStateItem({
    state_id: "diagnostic.snapshot_status",
    severity: "warning",
    summary: "Ready repository snapshot",
    remediation: "Review the include/exclude patterns in the Repository tab.",
    target_ui: { route: "/repository", anchor: "repo-patterns", action_label: "「Ready repository snapshot」を修正" },
    related_checks: ["snapshot_status"],
    dedupe_key: "diagnostic.snapshot_status",
  });
  const docIndexItem = projectedStateItem({
    state_id: "diagnostic.pipeline_documentation_index",
    severity: "warning",
    summary: "Documentation index build step",
    remediation: "Run Build / Refresh in System Understanding to index documentation chunks.",
    target_ui: { route: "/system-understanding", anchor: "build", action_label: "「Documentation index build step」を修正" },
    related_checks: ["pipeline_documentation_index"],
    dedupe_key: "diagnostic.pipeline_documentation_index",
  });

  const stateResponse = (items: SystemStateItem[]) => {
    const notificationItems = items.filter(
      (i) => ["error", "blocked", "warning"].includes(i.severity) && i.scope === "global",
    );
    return {
      system_id: 1,
      generated_at: 1,
      overall_severity: items.some((i) => i.severity === "error") ? "error"
        : items.some((i) => i.severity === "warning") ? "warning" : "ok",
      severity_counts: {},
      items,
      primary_item: notificationItems[0] ?? null,
      notification_items: notificationItems,
      page_items: {},
    };
  };

  test("badge shows attention count from canonical notification items and opens the item dialog", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateResponse([llmItem, snapshotItem, docIndexItem]));
      if (path === "/system-diagnostics") return Promise.resolve(diagnosticsResponse);
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId("diagnostics-badge");
    // error(1) + warning(2) = 3 deduped canonical notifications.
    expect(screen.getByTestId("diagnostics-badge-count").textContent).toBe("3");

    fireEvent.click(badge);

    await waitFor(() => {
      expect(screen.getByText("System State")).toBeTruthy();
    });

    // Items render the server's summary and remediation verbatim.
    expect(screen.getByText("Intelligence reasoning model configuration")).toBeTruthy();
    expect(
      screen.getByText(/Set INTELLIGENCE_LLM_PROVIDER and INTELLIGENCE_LLM_MODEL/),
    ).toBeTruthy();
    expect(screen.getByTestId("system-state-item-diagnostic.snapshot_status")).toBeTruthy();
  });

  test("badge dedupes items sharing a dedupe_key in its count", async () => {
    const duplicate = { ...snapshotItem, state_id: "snapshot.ready.missing" };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateResponse([snapshotItem, duplicate]));
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    await screen.findByTestId("diagnostics-badge");
    expect(screen.getByTestId("diagnostics-badge-count").textContent).toBe("1");
  });

  test("badge consumes the server's phase-scoped notification projection (Issue #239)", async () => {
    // `items` remains the complete audit list. The server owns phase
    // withdrawal in `notification_items`; the badge must consume that
    // projection verbatim instead of re-deriving phase visibility.
    const setupItem = { ...snapshotItem, phase: "setup" as const };
    // proposal.probe_plans.*/proposal.experiments.* items default to
    // phase="evaluation" (Issue #256's state_group="proposal" default) --
    // a real later-phase token, not an arbitrary placeholder.
    const evaluationItem = projectedStateItem({
      state_id: "proposal.experiments.undecided",
      severity: "warning",
      summary: "評価待ちの experiment があります。",
      remediation: "Experiments でレビューしてください。",
      target_ui: { route: "/experiments", anchor: null, action_label: "Experiments でレビュー" },
      related_checks: [],
      dedupe_key: "proposal.experiments.undecided",
    });
    evaluationItem.phase = "evaluation";
    const scoped = (userPhase: "setup" | "evaluation") => ({
      ...stateResponse([setupItem, evaluationItem]),
      notification_items: userPhase === "setup"
        ? [setupItem]
        : [setupItem, evaluationItem],
      user_phase: userPhase,
      phases: [
        { phase: "setup", complete: userPhase !== "setup" },
        { phase: "preparation", complete: userPhase === "evaluation" },
        { phase: "instrumentation", complete: userPhase === "evaluation" },
        { phase: "observation", complete: userPhase === "evaluation" },
        { phase: "evaluation", complete: false },
        { phase: "publish", complete: false },
      ],
    });

    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state" ? Promise.resolve(scoped("setup")) : Promise.resolve(null),
    );
    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    const first = render(<DiagnosticsBadge />, { wrapper: createWrapper() });
    await screen.findByTestId("diagnostics-badge");
    // Only the setup-phase item is counted while user_phase is "setup".
    expect(screen.getByTestId("diagnostics-badge-count").textContent).toBe("1");
    first.unmount();

    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state" ? Promise.resolve(scoped("evaluation")) : Promise.resolve(null),
    );
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });
    await screen.findByTestId("diagnostics-badge");
    await waitFor(() => {
      expect(screen.getByTestId("diagnostics-badge-count").textContent).toBe("2");
    });
  });

  test("clicking a navigate-kind item routes to its fix page with focus params", async () => {
    window.history.pushState({}, "", "/");
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateResponse([snapshotItem]));
      if (path === "/system-diagnostics") return Promise.resolve(diagnosticsResponse);
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId("diagnostics-badge");
    fireEvent.click(badge);

    const item = await screen.findByTestId("system-state-item-diagnostic.snapshot_status");
    fireEvent.click(within(item).getByRole("button"));

    await waitFor(() => {
      expect(window.location.pathname).toBe("/repository");
    });
    const params = new URLSearchParams(window.location.search);
    expect(params.get("diagnostic")).toBe("snapshot_status");
    expect(params.get("fix")).toBe("repo-patterns");
  });

  test("clicking an env-only item opens the remediation dialog instead of navigating", async () => {
    window.history.pushState({}, "", "/");
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateResponse([llmItem]));
      if (path === "/system-diagnostics") return Promise.resolve(diagnosticsResponse);
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId("diagnostics-badge");
    fireEvent.click(badge);

    // intelligence_llm_config projects with target_ui null (dialog-kind fix);
    // the badge opens the env-fix dialog from the related diagnostic check.
    const item = await screen.findByTestId("system-state-item-diagnostic.intelligence_llm_config");
    fireEvent.click(within(item).getByRole("button", {
      name: "「Intelligence reasoning model configuration」の対処方法",
    }));

    const envDialog = await screen.findByTestId("diagnostic-env-dialog");
    expect(envDialog.textContent).toContain("設定が必要な環境変数");
    // Did not navigate away.
    expect(window.location.pathname).toBe("/");
    // The list dialog closed so the two modals don't stack.
    expect(screen.queryByText("System State")).toBeNull();
  });

  test("badge renders without count when no item needs attention", async () => {
    const okItem = projectedStateItem({
      state_id: "understanding.purpose.satisfied",
      severity: "ok",
      summary: "System Purpose は現在の snapshot で確認済みです。",
      source: "system_state",
      dedupe_key: "",
    });
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateResponse([okItem]));
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    await screen.findByTestId("diagnostics-badge");
    expect(screen.queryByTestId("diagnostics-badge-count")).toBeNull();
  });

  test("informational no-action audit items never become notifications or CTAs", async () => {
    const reasoningNotRun = projectedStateItem({
      state_id: "diagnostic.llm_last_run",
      severity: "info",
      status: "unconfirmed",
      user_action_kind: "none",
      intervention_timing: "before_next_step",
      subject: "直近の reasoning モデル実行",
      summary: "直近の reasoning モデル実行",
      remediation: "他に warning / error がある場合は、先にそちらの次の操作を実施してください。",
      evidence: { diagnostic_category: "llm", fix_kind: "navigate" },
      target_ui: null,
      related_checks: ["llm_last_run"],
      dedupe_key: "diagnostic.llm_last_run",
      phase: "setup",
    });
    const response = stateResponse([reasoningNotRun]);
    expect(response.notification_items).toEqual([]);

    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(response);
      if (path === "/system-diagnostics") {
        return Promise.resolve({
          ...diagnosticsResponse,
          checks: [{
            ...diagnosticsResponse.checks[1],
            check_id: "llm_last_run",
            title: "直近の reasoning モデル実行",
            severity: "unknown",
            fix_page: "/system-understanding",
            fix_anchor: "build",
          }],
        });
      }
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId("diagnostics-badge");
    expect(screen.queryByTestId("diagnostics-badge-count")).toBeNull();
    fireEvent.click(badge);
    expect(await screen.findByText("対応が必要な状態はありません。")).toBeTruthy();
    expect(screen.queryByText("直近の reasoning モデル実行")).toBeNull();
    expect(screen.queryByRole("button", { name: "修正する" })).toBeNull();
  });

  test("badge shows a degraded error state when system-state cannot be loaded (Issue #239)", async () => {
    // The removed system-diagnostics fallback must NOT resurrect: even with a
    // perfectly healthy /system-diagnostics response available, a failed
    // /system-state read renders the explicit degraded badge, never a count
    // derived from another source.
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.reject(new ApiError(500, "boom"));
      if (path === "/system-diagnostics") return Promise.resolve(diagnosticsResponse);
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    const badge = await screen.findByTestId("diagnostics-badge");
    expect(badge.getAttribute("data-state")).toBe("error");
    expect(screen.getByTestId("diagnostics-badge-error")).toBeTruthy();
    expect(screen.queryByTestId("diagnostics-badge-count")).toBeNull();

    fireEvent.click(badge);
    expect(await screen.findByTestId("diagnostics-badge-error-message")).toBeTruthy();
  });

  test("canonical StateItem keeps the same snapshot target in the badge and page banner", async () => {
    window.history.pushState({}, "", "/system-understanding");
    const snapshotState = {
      state_id: "repository.snapshot.stale",
      state_group: "repository",
      severity: "warning" as const,
      status: "stale",
      user_action_kind: "create_snapshot",
      intervention_timing: "now",
      subject: "Repository snapshot",
      summary: "HEAD が最新 snapshot より進んでいます。",
      detail: "現在の HEAD に対応する ready snapshot がありません。",
      impact: "理解結果が古い可能性があります。",
      remediation: "Repository で新しい snapshot を作成してください。",
      evidence: {},
      target_ui: { route: "/repository", anchor: "snapshot-create", action_label: "Snapshot を作成" },
      related_checks: ["snapshot_status"],
      related_pipeline_steps: ["snapshot_ready"],
      source: "system_state",
      dedupe_key: "repository.snapshot.freshness",
      scope: "global",
      decision_method: "deterministic" as const,
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [snapshotState], primary_item: snapshotState,
        notification_items: [snapshotState], page_items: { "/system-understanding": [snapshotState] },
      });
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    const { SystemStateBanner } = await import("@/components/system-state");
    render(<><DiagnosticsBadge /><SystemStateBanner item={snapshotState} /></>, { wrapper: createWrapper() });

    const bannerCta = await screen.findByTestId("system-state-action-repository.snapshot.stale");
    expect(bannerCta.textContent).toBe("Snapshot を作成");
    fireEvent.click(bannerCta);
    await waitFor(() => expect(window.location.pathname).toBe("/repository"));
    let targetParams = new URLSearchParams(window.location.search);
    expect(targetParams.get("fix")).toBe("snapshot-create");
    expect(targetParams.get("diagnostic")).toBe("snapshot_status");

    window.history.pushState({}, "", "/system-understanding");
    fireEvent.click(screen.getByTestId("diagnostics-badge"));
    const badgeItem = await screen.findByTestId("system-state-item-repository.snapshot.stale");
    const badgeCta = within(badgeItem).getByRole("button", { name: "Snapshot を作成" });
    fireEvent.click(badgeCta);
    await waitFor(() => expect(window.location.pathname).toBe("/repository"));
    targetParams = new URLSearchParams(window.location.search);
    expect(targetParams.get("fix")).toBe("snapshot-create");
    expect(targetParams.get("diagnostic")).toBe("snapshot_status");
  });

  test("system-state items projected from a dialog-kind diagnostic (no target_ui) still open the env fix dialog", async () => {
    window.history.pushState({}, "", "/");
    const repositoryRootsCheck = {
      check_id: "repository_roots",
      category: "repository",
      title: "Repository root allowlist",
      severity: "error" as const,
      detail: "PROBE_REPOSITORY_ROOTS is empty.",
      impact: "No repository can be registered until an allowlisted root is configured.",
      remediation: "Set PROBE_REPOSITORY_ROOTS to the allowed repository parent directories.",
      related_env: ["PROBE_REPOSITORY_ROOTS"],
      related_paths: [],
      related_pages: [],
      related_pipeline_steps: [],
      last_observed_error: null,
      decision_method: "deterministic" as const,
      fix_kind: "dialog" as const,
      fix_page: null,
      fix_anchor: null,
    };
    // Projected from system_diagnostics: dialog-kind checks have no fix_page,
    // so the server sets target_ui to null (Issue #206-208 review, Defect A).
    const diagnosticItem: SystemStateItem = {
      state_id: "diagnostic.repository_roots",
      state_group: "repository",
      severity: "error",
      status: "error",
      user_action_kind: "configure_env",
      intervention_timing: "now",
      subject: "Repository root allowlist",
      summary: "PROBE_REPOSITORY_ROOTS is empty.",
      detail: "PROBE_REPOSITORY_ROOTS is empty.",
      impact: "No repository can be registered until an allowlisted root is configured.",
      remediation: "Set PROBE_REPOSITORY_ROOTS to the allowed repository parent directories.",
      evidence: { diagnostic_category: "repository", fix_kind: "dialog" },
      target_ui: null,
      related_checks: ["repository_roots"],
      related_pipeline_steps: [],
      source: "system_diagnostics",
      dedupe_key: "diagnostic.repository_roots",
      scope: "global",
      decision_method: "deterministic",
    };
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "error",
        severity_counts: { error: 1 }, items: [diagnosticItem], primary_item: diagnosticItem,
        notification_items: [diagnosticItem], page_items: {},
      });
      if (path === "/system-diagnostics") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "error",
        severity_counts: { ok: 0, warning: 0, error: 1, blocked: 0, unknown: 0 },
        checks: [repositoryRootsCheck],
      });
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    render(<DiagnosticsBadge />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("diagnostics-badge"));
    const badgeItem = await screen.findByTestId("system-state-item-diagnostic.repository_roots");

    // The canonical item declares a dialog action; the related check supplies
    // only the remediation dialog contents.
    const fixButton = within(badgeItem).getByRole("button", {
      name: "「Repository root allowlist」の対処方法",
    });
    fireEvent.click(fixButton);

    const envDialog = await screen.findByTestId("diagnostic-env-dialog");
    expect(envDialog.textContent).toContain("PROBE_REPOSITORY_ROOTS");
    expect(envDialog.textContent).toContain("Repository root allowlist");
    // Did not navigate away, and the list dialog closed so the modals don't stack.
    expect(window.location.pathname).toBe("/");
    expect(screen.queryByText("System State")).toBeNull();
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

  test("floating notice and panel current issue share one canonical StateItem", async () => {
    mockAssistantApi();
    const { AssistantPanel } = await import("@/components/assistant-panel");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const stateItem: SystemStateItem = {
      state_id: "repository.snapshot.stale", state_group: "repository", severity: "warning",
      status: "stale", user_action_kind: "create_snapshot", intervention_timing: "now",
      subject: "repository", summary: "HEAD が最新 snapshot より進んでいます。snapshot を作成してください。",
      detail: "A newer commit is available.", impact: "Understanding is stale.",
      remediation: "Create a snapshot.", evidence: {},
      target_ui: { route: "/repository", anchor: "snapshot-create", action_label: "Snapshot を作成" },
      related_checks: [], related_pipeline_steps: [], source: "system_state", dedupe_key: "stale",
      scope: "global", decision_method: "deterministic" as const,
    };
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/system-understanding"]}>
          <AssistantPanel focusedStateItem={stateItem} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("assistant-snapshot-notice").textContent).toContain(stateItem.summary);
    fireEvent.click(screen.getByTestId("assistant-button"));
    const issue = await screen.findByTestId("assistant-current-issue");
    expect(issue.textContent).toContain(stateItem.summary);
    expect(issue.textContent).toContain("Snapshot を作成");

    fireEvent.click(await screen.findByTestId("assistant-focused-state-question"));
    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith("/assistant/ask", expect.objectContaining({
      visible_state_ids: [stateItem.state_id], focused_state_id: stateItem.state_id,
    })));
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
          replayability: "replayable", replay_reasons: [],
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
          replayability: "partial", replay_reasons: ["redacted"],
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
    expect(screen.getAllByRole("button", { name: /Replay$/ }).length).toBe(2);
    expect(screen.getByText("partial")).toBeInTheDocument();
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
    expect(within(table).getByRole("button", { name: /Replay$/ })).toBeInTheDocument();
  });
});

// ── Cross-page onboarding and navigation links (Issue #212) ─────────

describe("Overview get-started zero state (Issue #212)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockSystems = [{ id: 1, name: "alpha" }];
  });

  test("renders ordered get-started links when no components exist", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/components") return Promise.resolve([]);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "ok",
        severity_counts: {}, items: [], primary_item: null,
        notification_items: [], page_items: {},
      });
      return Promise.resolve(null);
    });

    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    expect(within(getStarted).getByTestId("overview-link-repository"))
      .toHaveAttribute("href", "/repository");
    expect(within(getStarted).getByTestId("overview-link-system-understanding"))
      .toHaveAttribute("href", "/system-understanding");
    expect(within(getStarted).getByTestId("overview-link-connect-sdk"))
      .toHaveAttribute("href", "/connect-sdk");
  });

  test("does not render get-started links when components exist", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/components") return Promise.resolve([{
        component_id: "summarize", mode: "trace", trace_count: 3, last_seen: 1,
      }]);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "ok",
        severity_counts: {}, items: [], primary_item: null,
        notification_items: [], page_items: {},
      });
      return Promise.resolve(null);
    });

    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    await screen.findByText("summarize");
    expect(screen.queryByTestId("overview-get-started")).not.toBeInTheDocument();
  });
});

// ── Overview 4th get-started step (Issue #259) ──────────────────────

describe("Overview 4th get-started step (Issue #259)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockSystems = [{ id: 1, name: "alpha" }];
  });

  function setupOverview(connectivityState: string | null) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/components") return Promise.resolve([]);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "ok",
        severity_counts: {}, items: [], primary_item: null,
        notification_items: [], page_items: {},
      });
      if (path === "/connectivity/status") {
        return connectivityState
          ? Promise.resolve({
            system_id: 1, state: connectivityState, total_trace_count: 5, smoke_trace_count: 1,
            real_trace_count: 4, first_trace_at: 1, last_trace_at: 2,
            last_trace_component_id: "comp", smoke_component_id: "smoke", materialized_session_ids: [],
          })
          : Promise.resolve(null);
      }
      return Promise.resolve(null);
    });
  }

  test("shows the 4th step linking to /components, incomplete while not receiving", async () => {
    setupOverview("no_signal");
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step4 = within(getStarted).getByTestId("overview-link-view-traces");
    expect(step4).toHaveAttribute("href", "/components");
    await waitFor(() => expect(step4).toHaveAttribute("data-done", "false"));
    expect(within(getStarted).queryByTestId("overview-link-view-traces-done")).not.toBeInTheDocument();
  });

  test("marks the 4th step complete once connectivity state is receiving", async () => {
    setupOverview("receiving");
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step4 = within(getStarted).getByTestId("overview-link-view-traces");
    await waitFor(() => expect(step4).toHaveAttribute("data-done", "true"));
    expect(within(getStarted).getByTestId("overview-link-view-traces-done")).toBeInTheDocument();
  });
});

describe("Probe Planner manual feature-id escape hatch (Issue #212)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  function mockPlannerApis() {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({ plans: [] });
      if (path === "/repository/drafts/latest") {
        return Promise.resolve({ system_profile_draft: null, feature_drafts: [] });
      }
      return Promise.resolve(null);
    });
  }

  test("with no feature drafts, shows prerequisite note and hides free-text input behind toggle", async () => {
    mockPlannerApis();

    await renderProbePlannerAt("/probe-planner");

    fireEvent.click(await screen.findByText("Generate Plan"));

    const note = await screen.findByTestId("planner-no-drafts-note");
    expect(within(note).getByText("Feature Map").closest("a"))
      .toHaveAttribute("href", "/feature-map");
    expect(within(note).getByText("System Understanding").closest("a"))
      .toHaveAttribute("href", "/system-understanding");
    expect(screen.queryByTestId("planner-manual-feature-input")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("planner-manual-feature-toggle"));
    expect(await screen.findByTestId("planner-manual-feature-input")).toBeInTheDocument();
  });
});

describe("Feature Map empty-state prerequisites (Issue #212)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("renders the shared prerequisite checklist when no profile draft exists", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/drafts/latest") {
        return Promise.resolve({ system_profile_draft: null, feature_drafts: [] });
      }
      if (path === "/repository/code-links") return Promise.resolve({ links: [] });
      if (path === "/repository/snapshots/latest") return Promise.resolve(null);
      if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
      return Promise.resolve(null);
    });

    const { default: FeatureMapPage } = await import("@/pages/feature-map");
    render(<FeatureMapPage />, { wrapper: createWrapper() });

    const checklist = await screen.findByTestId("prerequisite-checklist");
    expect(within(checklist).getByText("Snapshot created")).toBeInTheDocument();
    expect(within(checklist).getByText("Symbols indexed")).toBeInTheDocument();
  });
});

describe("Connect SDK forward link to Setup Guide (Issue #212)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("renders the setup-guide link", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/auth/my-tokens") return Promise.resolve([]);
      return Promise.resolve(null);
    });

    const { default: ConnectSdkPage } = await import("@/pages/connect-sdk");
    render(<ConnectSdkPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("connect-sdk-setup-guide-link"))
      .toHaveAttribute("href", "/setup-guide");
  });
});

// ── Build success summary and pipeline collapse (Issue #211) ────────

describe("Hub success summary and pipeline collapse (Issue #211)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const allCompletePipeline = [
    { step: "repository_configured", status: "complete" },
    { step: "snapshot_ready", status: "complete" },
    { step: "documentation_indexed", status: "complete" },
    { step: "documentation_claims_scanned", status: "complete" },
    { step: "symbols_indexed", status: "complete" },
    { step: "entrypoints_discovered", status: "complete" },
    { step: "docs_code_reconciled", status: "complete" },
    { step: "capability_hierarchy_ready", status: "complete" },
  ];

  const baseResponse = {
    system_id: 1,
    snapshot_id: 5,
    commit_sha: "abc12345def",
    pipeline: allCompletePipeline,
    purpose: { name: "Test System", summary: "A test system", provenance_kind: "manual" },
    capabilities: [],
    entrypoints: [],
    major_symbols: [],
    gaps: [],
    gap_summary: [],
    metadata_coverage: { symbol_count: 42, symbols_with_source_metadata: 5, entrypoint_count: 10, entrypoints_with_capability_link: 3 },
    // Issue #240: the success summary is now server-supplied (Japanese)
    // instead of assembled client-side.
    success_summary: "分析完了 — 8/8 ステップ ・ 42 シンボル ・ 10 エントリポイント",
  };

  function mockSuApis(
    response: Record<string, unknown>,
    diagnostics?: Record<string, unknown>,
    systemState?: Record<string, unknown>,
  ) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(response);
      if (path === "/system-diagnostics") return Promise.resolve(diagnostics ?? null);
      if (path === "/system-state") return Promise.resolve(systemState ?? null);
      return Promise.resolve(null);
    });
  }

  test("all-complete pipeline shows the success summary and collapses the checklist", async () => {
    mockSuApis(baseResponse);

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const summary = await screen.findByTestId("build-success-summary");
    expect(summary.textContent).toContain("8/8 ステップ");
    expect(summary.textContent).toContain("42 シンボル");
    expect(summary.textContent).toContain("10 エントリポイント");

    const collapsed = screen.getByTestId("pipeline-collapsed");
    expect(collapsed.textContent).toContain("8/8 steps complete");

    // Purpose is defined, so the entry cards carry no prerequisite note.
    expect(screen.queryByTestId("entry-cards-prereq-note")).not.toBeInTheDocument();

    // Expanding restores the full checklist with a collapse control.
    fireEvent.click(screen.getByTestId("pipeline-expand"));
    expect(await screen.findByTestId("pipeline-checklist")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("pipeline-collapse"));
    expect(await screen.findByTestId("pipeline-collapsed")).toBeInTheDocument();
  });

  // The empty-capability-hierarchy diagnostic exactly as the server emits it:
  // the structured fix target (fix_page "/interview") is what drives the
  // pipeline row's Interview CTA — never the free-text detail.
  const capabilityEmptyCheck = {
    check_id: "pipeline_capability_hierarchy",
    category: "pipeline",
    title: "capability 階層の実行",
    severity: "warning",
    detail: "実行は完了しましたが capability ノードが 0 件です。",
    impact: "Core Capabilities が未定義です。",
    remediation: "Interview で Core Capabilities を確認してください。",
    related_env: [],
    related_paths: [],
    related_pages: ["/system-understanding", "/interview"],
    related_pipeline_steps: ["capability_hierarchy_ready"],
    last_observed_error: null,
    decision_method: "deterministic",
    fix_kind: "navigate",
    fix_page: "/interview",
    fix_anchor: "interview-capabilities",
  };

  const incompleteCapabilityResponse = {
    ...baseResponse,
    pipeline: [
      ...allCompletePipeline.slice(0, 7),
      { step: "capability_hierarchy_ready", status: "warning", detail: "0 capabilities" },
    ],
  };

  // Issue #239: the canonical capability-hierarchy-empty StateItem exactly as
  // the server projects it into page_items — the checklist CTA consumes its
  // action_label / target_ui verbatim (same source as the banner and badge).
  const capabilityEmptyStateItem: SystemStateItem = {
    state_id: "pipeline.capability_hierarchy.empty",
    state_group: "pipeline",
    severity: "warning",
    status: "missing",
    user_action_kind: "confirm",
    intervention_timing: "before_next_step",
    subject: "Capability 階層",
    summary: "Capability 階層は実行済みですが capability が 0 件です。",
    detail: "現在の snapshot に capability ノードが存在しません。",
    impact: "Core Capabilities が未定義です。",
    remediation: "Interview で Core Capabilities を確認してください。",
    evidence: { capability_count: 0 },
    target_ui: { route: "/interview", anchor: "interview-capabilities", action_label: "Interview で Core Capabilities を確認" },
    display_routes: ["/system-understanding"],
    related_checks: [],
    related_pipeline_steps: ["capability_hierarchy_ready"],
    source: "system_state",
    dedupe_key: "",
    scope: "global",
    decision_method: "deterministic",
    phase: "preparation",
  };

  test("incomplete pipeline keeps the checklist expanded without a success summary", async () => {
    mockSuApis(
      incompleteCapabilityResponse,
      {
        system_id: 1,
        generated_at: 1750000000,
        overall_severity: "warning",
        severity_counts: { ok: 0, warning: 1, error: 0, blocked: 0, unknown: 0 },
        checks: [capabilityEmptyCheck],
      },
      {
        system_id: 1, generated_at: 1, overall_severity: "warning",
        severity_counts: { warning: 1 }, items: [capabilityEmptyStateItem],
        primary_item: capabilityEmptyStateItem,
        notification_items: [capabilityEmptyStateItem],
        page_items: {
          "/system-understanding": [capabilityEmptyStateItem],
          "/interview": [capabilityEmptyStateItem],
        },
      },
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("pipeline-checklist")).toBeInTheDocument();
    expect(screen.queryByTestId("pipeline-collapsed")).not.toBeInTheDocument();
    expect(screen.queryByTestId("build-success-summary")).not.toBeInTheDocument();
    // Issue #239: the CTA is the matching StateItem's action, verbatim — the
    // same text and destination the banner/badge show for this root cause.
    const cta = await screen.findByTestId("pipeline-cta-capability_hierarchy_ready");
    expect(cta).toHaveTextContent("Interview で Core Capabilities を確認");
    expect(cta).toHaveAttribute("href", "/interview?fix=interview-capabilities");
  });

  test("pipeline CTA renders nothing when no StateItem names the step", async () => {
    // Same pipeline detail text, but no matching StateItem in page_items:
    // there is no hardcoded fallback map (Issue #239), so no CTA button
    // renders for the step -- the status badge still does. Formerly the
    // Interview CTA was derived from the structured diagnostic; now the CTA
    // is sourced solely from the matching SystemStateItem.
    mockSuApis(incompleteCapabilityResponse);

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("pipeline-checklist")).toBeInTheDocument();
    expect(screen.queryByTestId("pipeline-cta-capability_hierarchy_ready")).toBeNull();
  });

  test("undefined purpose adds the prerequisite note to the entry cards", async () => {
    mockSuApis({ ...baseResponse, purpose: null });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("entry-cards-prereq-note")).toBeInTheDocument();
  });
});

// ── System Purpose side-by-side views (Issue #94/#275) ──────────────

describe("System Purpose side-by-side views (Issue #94/#275)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const completePipeline = [
    { step: "repository_configured", status: "complete" },
    { step: "snapshot_ready", status: "complete" },
    { step: "documentation_indexed", status: "complete" },
    { step: "documentation_claims_scanned", status: "complete" },
    { step: "symbols_indexed", status: "complete" },
    { step: "entrypoints_discovered", status: "complete" },
    { step: "docs_code_reconciled", status: "complete" },
    { step: "capability_hierarchy_ready", status: "complete" },
  ];

  function purposeResponse(overrides: Record<string, unknown> = {}) {
    return {
      system_id: 1,
      snapshot_id: 5,
      understanding_build_id: 9,
      commit_sha: "abc12345def",
      pipeline: completePipeline,
      purpose: null,
      capabilities: [],
      entrypoints: [],
      major_symbols: [],
      gaps: [],
      gap_summary: [],
      metadata_coverage: null,
      purpose_views: [],
      purpose_confirmation: null,
      ...overrides,
    };
  }

  const emptyProfile = {
    name: "", purpose: "", target_users: [], stakeholder_value: "",
    constraints: [], success_criteria: [], created_at: null, updated_at: null,
  };

  function mockApis(response: Record<string, unknown>, profile: Record<string, unknown> = emptyProfile) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/system-understanding") return Promise.resolve(response);
      if (path === "/system-profile") return Promise.resolve(profile);
      if (path === "/system-diagnostics") return Promise.resolve(null);
      if (path === "/system-state") return Promise.resolve(null);
      return Promise.resolve(null);
    });
  }

  test("renders manual and AI purpose views side by side with provenance badges", async () => {
    mockApis(purposeResponse({
      purpose_views: [
        { source: "system_profile", provenance_kind: "manual", name: "Manual purpose", summary: "human summary" },
        { source: "capability_hierarchy", provenance_kind: "reasoning_llm", name: "AI purpose", summary: "ai summary" },
      ],
    }));

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const manualCard = await screen.findByTestId("purpose-manual-card");
    expect(manualCard.textContent).toContain("Manual purpose");
    expect(manualCard.textContent).toContain("human summary");
    expect(manualCard.textContent).toContain("manual");

    const aiCard = screen.getByTestId("purpose-ai-card");
    expect(aiCard.textContent).toContain("AI purpose");
    expect(aiCard.textContent).toContain("reasoning_llm");
  });

  test("manual side empty shows an inline entry form that saves via PUT /system-profile", async () => {
    mockApis(
      purposeResponse({
        purpose_views: [
          { source: "capability_hierarchy", provenance_kind: "reasoning_llm", name: "AI purpose", summary: "ai summary" },
        ],
      }),
      {
        name: "Existing name", purpose: "", target_users: ["dev"], stakeholder_value: "value",
        constraints: [], success_criteria: [], created_at: 1, updated_at: 1,
      },
    );
    mockApi.put.mockResolvedValue({});

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const textarea = await screen.findByTestId("purpose-manual-entry-textarea");
    fireEvent.change(textarea, { target: { value: "新しい目的" } });
    fireEvent.click(screen.getByTestId("purpose-manual-entry-save"));

    await waitFor(() => expect(mockApi.put).toHaveBeenCalledWith("/system-profile", {
      name: "Existing name",
      purpose: "新しい目的",
      target_users: ["dev"],
      stakeholder_value: "value",
      constraints: [],
      success_criteria: [],
    }));

    const { toast } = await import("sonner");
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  test("AI side empty shows the Japanese empty state", async () => {
    mockApis(purposeResponse({
      purpose_views: [
        { source: "system_profile", provenance_kind: "manual", name: "Manual purpose", summary: null },
      ],
    }));

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("purpose-ai-empty")).toHaveTextContent(
      "AI/ソース由来の purpose はまだありません。Snapshot 作成と Build 実行後に表示されます。",
    );
  });

  test("both views present with no confirmation shows the confirm button and posts on click", async () => {
    mockApis(purposeResponse({
      purpose_views: [
        { source: "system_profile", provenance_kind: "manual", name: "Manual purpose" },
        { source: "capability_hierarchy", provenance_kind: "reasoning_llm", name: "AI purpose" },
      ],
    }));
    mockApi.post.mockResolvedValue({
      id: 1, snapshot_id: 5, decision_method: "manual", manual_purpose: "Manual purpose",
      created_at: 1700000000, stale: false,
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("purpose-confirm-button"));

    await waitFor(() => expect(mockApi.post).toHaveBeenCalledWith(
      "/repository/system-understanding/purpose-confirmation",
      { snapshot_id: 5, understanding_build_id: 9 },
    ));

    const { toast } = await import("sonner");
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("一致を確認しました"));
  });

  test("confirmed and not stale shows the 確認済み badge without the confirm button", async () => {
    mockApis(purposeResponse({
      purpose_views: [
        { source: "system_profile", provenance_kind: "manual", name: "Manual purpose" },
        { source: "capability_hierarchy", provenance_kind: "reasoning_llm", name: "AI purpose" },
      ],
      purpose_confirmation: {
        id: 1, snapshot_id: 5, decision_method: "manual", manual_purpose: "Manual purpose",
        created_at: 1700000000, stale: false,
      },
    }));

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const confirmed = await screen.findByTestId("purpose-confirmed");
    expect(confirmed.textContent).toContain("確認済み");
    expect(screen.queryByTestId("purpose-confirm-button")).not.toBeInTheDocument();
  });

  test("confirmation stays disabled until a completed understanding build is present", async () => {
    mockApis(purposeResponse({
      understanding_build_id: null,
      purpose_views: [
        { source: "system_profile", provenance_kind: "manual", name: "Manual purpose" },
        { source: "capability_hierarchy", provenance_kind: "reasoning_llm", name: "AI purpose" },
      ],
    }));

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("purpose-confirm-button")).toBeDisabled();
    expect(screen.getByTestId("purpose-confirm-build-required")).toHaveTextContent(
      "System Understanding の Build 完了後に確認できます。",
    );
    expect(mockApi.post).not.toHaveBeenCalled();
  });

  test("stale confirmation shows the reason note and the confirm button again", async () => {
    mockApis(purposeResponse({
      purpose_views: [
        { source: "system_profile", provenance_kind: "manual", name: "Manual purpose" },
        { source: "capability_hierarchy", provenance_kind: "reasoning_llm", name: "AI purpose" },
      ],
      purpose_confirmation: {
        id: 1, snapshot_id: 5, decision_method: "manual", manual_purpose: "Manual purpose",
        created_at: 1700000000, stale: true, stale_reason: "profile_updated",
      },
    }));

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const note = await screen.findByTestId("purpose-confirmation-stale-note");
    expect(note.textContent).toBe("確認後に System Profile が更新されています");
    expect(screen.getByTestId("purpose-confirm-button")).toBeInTheDocument();
  });

  test("confirmation error is surfaced via toast", async () => {
    mockApis(purposeResponse({
      purpose_views: [
        { source: "system_profile", provenance_kind: "manual", name: "Manual purpose" },
        { source: "capability_hierarchy", provenance_kind: "reasoning_llm", name: "AI purpose" },
      ],
    }));
    mockApi.post.mockRejectedValue(new ApiError(422, "Manual purpose is missing"));

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("purpose-confirm-button"));

    const { toast } = await import("sonner");
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(
      "確認に失敗しました: Manual purpose is missing",
    ));
  });

  test("purposeDefined falls back to purpose_views when the legacy purpose field is null", async () => {
    mockApis(purposeResponse({
      purpose: null,
      purpose_views: [
        { source: "system_profile", provenance_kind: "manual", name: "Manual purpose" },
      ],
    }));

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await screen.findByTestId("purpose-manual-card");
    expect(screen.queryByTestId("entry-cards-prereq-note")).not.toBeInTheDocument();
  });
});

// ── Notification surfaces consume one canonical StateItem (Issue #239) ──

describe("Notification surface consistency (Issue #239)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const canonicalItem: SystemStateItem = {
    state_id: "pipeline.capability_hierarchy.empty",
    state_group: "pipeline",
    severity: "warning",
    status: "missing",
    user_action_kind: "confirm",
    intervention_timing: "before_next_step",
    subject: "Capability 階層",
    summary: "Capability 階層は実行済みですが capability が 0 件です。",
    detail: "現在の snapshot に capability ノードが存在しません。",
    impact: "Core Capabilities が未定義です。",
    remediation: "Interview で Core Capabilities を確認してください。",
    evidence: { capability_count: 0 },
    target_ui: { route: "/interview", anchor: "interview-capabilities", action_label: "Interview で Core Capabilities を確認" },
    display_routes: ["/system-understanding"],
    related_checks: [],
    related_pipeline_steps: ["capability_hierarchy_ready"],
    source: "system_state",
    dedupe_key: "",
    scope: "global",
    decision_method: "deterministic",
    phase: "preparation",
  };
  const expectedTarget = "/interview?fix=interview-capabilities";

  const stateResponse = {
    system_id: 1, generated_at: 1, overall_severity: "warning",
    severity_counts: { warning: 1 }, items: [canonicalItem],
    primary_item: canonicalItem, notification_items: [canonicalItem],
    page_items: {
      "/system-understanding": [canonicalItem],
      "/interview": [canonicalItem],
    },
  };

  test("badge, page banner, and floating notice show the same summary and resolve the same target", async () => {
    window.history.pushState({}, "", "/system-understanding");
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateResponse);
      return Promise.resolve(null);
    });

    const { DiagnosticsBadge } = await import("@/components/diagnostics-badge");
    const { SystemStateBanner } = await import("@/components/system-state");
    const { AssistantPanel } = await import("@/components/assistant-panel");
    render(
      <>
        <DiagnosticsBadge />
        <SystemStateBanner item={canonicalItem} />
        <AssistantPanel focusedStateItem={canonicalItem} />
      </>,
      { wrapper: createWrapper() },
    );

    // Every surface renders the server's summary verbatim.
    const banner = await screen.findByTestId("system-state-banner");
    expect(banner.textContent).toContain(canonicalItem.summary);
    expect(screen.getByTestId("assistant-snapshot-notice").textContent)
      .toContain(canonicalItem.summary);
    fireEvent.click(await screen.findByTestId("diagnostics-badge"));
    const badgeItem = await screen.findByTestId(
      `system-state-item-${canonicalItem.state_id}`,
    );
    expect(badgeItem.textContent).toContain(canonicalItem.summary);

    // ...and every surface's action resolves to the same target URL.
    fireEvent.click(within(badgeItem).getByRole("button", { name: canonicalItem.target_ui!.action_label }));
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(expectedTarget));

    window.history.pushState({}, "", "/system-understanding");
    fireEvent.click(screen.getByTestId(`system-state-action-${canonicalItem.state_id}`));
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(expectedTarget));

    window.history.pushState({}, "", "/system-understanding");
    fireEvent.click(screen.getByTestId("assistant-snapshot-notice"));
    await waitFor(() => expect(window.location.pathname + window.location.search).toBe(expectedTarget));
  });

  test("pipeline checklist CTA resolves the same StateItem to the same target as the other surfaces", async () => {
    window.history.pushState({}, "", "/system-understanding");
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateResponse);
      if (path === "/repository/system-understanding") return Promise.resolve({
        system_id: 1, snapshot_id: 5, commit_sha: "abc12345def",
        pipeline: [
          { step: "repository_configured", status: "complete" },
          { step: "snapshot_ready", status: "complete" },
          { step: "documentation_indexed", status: "complete" },
          { step: "documentation_claims_scanned", status: "complete" },
          { step: "symbols_indexed", status: "complete" },
          { step: "entrypoints_discovered", status: "complete" },
          { step: "docs_code_reconciled", status: "complete" },
          { step: "capability_hierarchy_ready", status: "warning", detail: "0 capabilities" },
        ],
        purpose: null, capabilities: [], entrypoints: [], major_symbols: [],
        gaps: [], gap_summary: [], metadata_coverage: null,
      });
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const cta = await screen.findByTestId("pipeline-cta-capability_hierarchy_ready");
    expect(cta).toHaveTextContent(canonicalItem.target_ui!.action_label);
    expect(cta).toHaveAttribute("href", expectedTarget);
  });
});

// ── User phase indicator (Issue #239) ───────────────────────────────

describe("User phase indicator (Issue #239)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const stateWithPhase = (
    userPhase: string,
    phases: { phase: string; complete: boolean; label?: string }[],
  ) => ({
    system_id: 1, generated_at: 1, overall_severity: "ok",
    severity_counts: {}, items: [], primary_item: null,
    notification_items: [], page_items: {},
    user_phase: userPhase, phases,
  });

  test("shows the current phase and per-phase completion from the server", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state"
        ? Promise.resolve(stateWithPhase("preparation", [
            { phase: "setup", complete: true },
            { phase: "preparation", complete: false },
            { phase: "instrumentation", complete: false },
            { phase: "observation", complete: false },
            { phase: "evaluation", complete: false },
            { phase: "publish", complete: false },
          ]))
        : Promise.resolve(null),
    );

    const { UserPhaseIndicator } = await import("@/components/system-state");
    render(<UserPhaseIndicator />, { wrapper: createWrapper() });

    const indicator = await screen.findByTestId("user-phase-indicator");
    expect(indicator.getAttribute("data-current-phase")).toBe("preparation");

    const setup = screen.getByTestId("user-phase-setup");
    expect(setup.getAttribute("data-complete")).toBe("true");
    expect(setup.getAttribute("data-current")).toBe("false");

    const preparation = screen.getByTestId("user-phase-preparation");
    expect(preparation.getAttribute("data-complete")).toBe("false");
    expect(preparation.getAttribute("data-current")).toBe("true");

    // All 6 phase chips render (Issue #256), and none of the later ones is
    // mistaken for the current phase.
    for (const phase of ["instrumentation", "observation", "evaluation", "publish"]) {
      const chip = screen.getByTestId(`user-phase-${phase}`);
      expect(chip.getAttribute("data-complete")).toBe("false");
      expect(chip.getAttribute("data-current")).toBe("false");
    }
  });

  test("prefers the server-provided phase label over the client fallback map (Issue #240)", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state"
        ? Promise.resolve(stateWithPhase("preparation", [
            { phase: "setup", complete: true, label: "セットアップ完了" },
            { phase: "preparation", complete: false, label: "準備" },
            { phase: "instrumentation", complete: false, label: "計装" },
            { phase: "observation", complete: false, label: "観測中" },
            { phase: "evaluation", complete: false, label: "評価" },
            // No server label for this entry: proves the client fallback
            // (USER_PHASE_LABELS) still applies when the server omits it.
            { phase: "publish", complete: false },
          ]))
        : Promise.resolve(null),
    );

    const { UserPhaseIndicator, USER_PHASE_LABELS } = await import("@/components/system-state");
    render(<UserPhaseIndicator />, { wrapper: createWrapper() });

    const indicator = await screen.findByTestId("user-phase-indicator");
    expect(indicator.getAttribute("title")).toBe("現在のフェーズ: 準備");
    expect(screen.getByTestId("user-phase-setup").textContent).toContain("セットアップ完了");
    expect(screen.getByTestId("user-phase-preparation").textContent).toContain("準備");
    expect(screen.getByTestId("user-phase-publish").textContent)
      .toContain(USER_PHASE_LABELS.publish);
  });

  test("display switches when the server-derived phase changes", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state"
        ? Promise.resolve(stateWithPhase("publish", [
            { phase: "setup", complete: true },
            { phase: "preparation", complete: true },
            { phase: "instrumentation", complete: true },
            { phase: "observation", complete: true },
            { phase: "evaluation", complete: true },
            { phase: "publish", complete: false },
          ]))
        : Promise.resolve(null),
    );

    const { UserPhaseIndicator } = await import("@/components/system-state");
    render(<UserPhaseIndicator />, { wrapper: createWrapper() });

    const indicator = await screen.findByTestId("user-phase-indicator");
    expect(indicator.getAttribute("data-current-phase")).toBe("publish");
    expect(screen.getByTestId("user-phase-preparation").getAttribute("data-complete")).toBe("true");
    expect(screen.getByTestId("user-phase-evaluation").getAttribute("data-complete")).toBe("true");
    expect(screen.getByTestId("user-phase-publish").getAttribute("data-current")).toBe("true");
  });

  test("renders nothing when the server does not provide a phase", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state"
        ? Promise.resolve({
            system_id: 1, generated_at: 1, overall_severity: "ok",
            severity_counts: {}, items: [], primary_item: null,
            notification_items: [], page_items: {},
          })
        : Promise.resolve(null),
    );

    const { UserPhaseIndicator } = await import("@/components/system-state");
    const { container } = render(<UserPhaseIndicator />, { wrapper: createWrapper() });

    // Wait a tick for the query to settle, then assert nothing rendered.
    await waitFor(() => expect(mockApi.get).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("user-phase-indicator")).toBeNull();
  });
});

// ── Diagnostic fix callout anchor collision ─────────────────────────

describe("Diagnostic fix callout with a shared anchor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  function diagCheck(overrides: Record<string, unknown>) {
    return {
      category: "pipeline",
      title: "check",
      detail: "detail",
      impact: "",
      remediation: "",
      related_env: [],
      related_paths: [],
      related_pages: [],
      related_pipeline_steps: [],
      last_observed_error: null,
      decision_method: "deterministic",
      fix_kind: "navigate",
      fix_page: "/system-understanding",
      fix_anchor: "build",
      ...overrides,
    };
  }

  test("anchor-only focus picks the most severe check, not backend array order", async () => {
    // llm_last_run ("no reasoning run recorded yet", informational `unknown`)
    // is emitted BEFORE the pipeline checks and shares fix_anchor "build".
    // A deep link with only ?fix=build must surface the actionable warning,
    // not the informational check that happens to come first.
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-diagnostics") {
        return Promise.resolve({
          system_id: 1,
          generated_at: 1750000000,
          overall_severity: "warning",
          severity_counts: { ok: 0, warning: 1, error: 0, blocked: 0, unknown: 1 },
          checks: [
            diagCheck({
              check_id: "llm_last_run",
              category: "llm",
              title: "直近の reasoning モデル実行",
              severity: "unknown",
            }),
            diagCheck({
              check_id: "pipeline_understanding_graph",
              title: "理解グラフの実行",
              severity: "warning",
              related_pipeline_steps: ["docs_code_reconciled"],
            }),
          ],
        });
      }
      return Promise.resolve(null);
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/system-understanding?fix=build"]}>
          <SystemUnderstandingPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const callout = await screen.findByTestId("diagnostic-callout-build");
    expect(callout.textContent).toContain("理解グラフの実行");
    expect(callout.textContent).not.toContain("直近の reasoning モデル実行");
  });

  test("an explicit ?diagnostic= id still wins over severity ranking", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-diagnostics") {
        return Promise.resolve({
          system_id: 1,
          generated_at: 1750000000,
          overall_severity: "warning",
          severity_counts: { ok: 0, warning: 1, error: 0, blocked: 0, unknown: 1 },
          checks: [
            diagCheck({
              check_id: "llm_last_run",
              category: "llm",
              title: "直近の reasoning モデル実行",
              severity: "unknown",
            }),
            diagCheck({
              check_id: "pipeline_understanding_graph",
              title: "理解グラフの実行",
              severity: "warning",
            }),
          ],
        });
      }
      return Promise.resolve(null);
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/system-understanding?fix=build&diagnostic=llm_last_run"]}>
          <SystemUnderstandingPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const callout = await screen.findByTestId("diagnostic-callout-build");
    expect(callout.textContent).toContain("直近の reasoning モデル実行");
  });
});

// ── GitHub page (Issue #216) ────────────────────────────────────────

describe("GitHub page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const connectionFixture = {
    id: 1,
    system_id: 1,
    api_base_url: "https://api.github.com",
    web_base_url: "https://github.com",
    owner: "acme",
    repo: "widgets",
    clone_url: "https://github.com/acme/widgets.git",
    installation_id: 42,
    default_branch: "main",
    credential_type: "github_app",
    status: "connected",
    last_error: null,
    last_synced_at: "2024-01-01T00:00:00Z",
    last_synced_commit_sha: "abc1234567890",
    created_by_user_id: 1,
    updated_by_user_id: 1,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  };

  function jobFixture(overrides: Record<string, unknown> = {}) {
    return {
      id: 1,
      system_id: 1,
      connection_id: 1,
      patch_id: 20,
      snapshot_id: 5,
      base_branch: "main",
      base_commit_sha: "abc1234567890",
      branch_name: "probe/publish-1-abc12345",
      commit_sha: null,
      pr_url: null,
      pr_number: null,
      status: "awaiting_approval",
      error: null,
      validation_summary: {
        baseline: { overall_success: true },
        probed: { overall_success: true },
      },
      requested_by_user_id: 1,
      approved_by_user_id: null,
      cleanup_state: "not_attempted",
      cleanup_error: null,
      created_at: 1700000000,
      updated_at: 1700000000,
      approved_at: null,
      completed_at: null,
      heartbeat_at: null,
      retry_count: 0,
      last_attempt_at: null,
      ...overrides,
    };
  }

  const patchFixture = {
    id: 20,
    plan_id: 10,
    system_id: 1,
    snapshot_id: 5,
    commit_sha: "abc1234567890",
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
  };

  function mockGithubData(data: {
    appStatus?: Record<string, unknown>;
    connections?: Record<string, unknown>[];
    jobs?: Record<string, unknown>[];
    patches?: Record<string, unknown>[];
  }) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/github/app-status") {
        return Promise.resolve(
          data.appStatus ?? {
            configured: true, app_id: "123",
            api_base_url: "https://api.github.com", web_base_url: "https://github.com",
          },
        );
      }
      if (path === "/github/connections") return Promise.resolve(data.connections ?? []);
      if (path === "/github/publish-jobs") return Promise.resolve(data.jobs ?? []);
      if (path === "/repository/probe-patches") return Promise.resolve(data.patches ?? []);
      if (path === "/users") return Promise.resolve([]);
      return Promise.resolve(null);
    });
  }

  test("shows the configured app status badge", async () => {
    mockGithubData({ appStatus: { configured: true, app_id: "999", api_base_url: "https://api.github.com", web_base_url: "https://github.com" } });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    await waitFor(() =>
      expect(screen.getByTestId("github-app-configured-badge")).toHaveTextContent("設定済み"),
    );
  });

  test("shows a setup hint when the GitHub App is not configured", async () => {
    mockGithubData({ appStatus: { configured: false, app_id: null, api_base_url: "https://api.github.com", web_base_url: "https://github.com" } });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    await waitFor(() =>
      expect(screen.getByTestId("github-app-configured-badge")).toHaveTextContent("未設定"),
    );
    expect(screen.getByText("GITHUB_APP_ID")).toBeInTheDocument();
    expect(screen.getByTestId("new-connection-button")).toBeDisabled();
  });

  test("shows the connections list with status and last error", async () => {
    mockGithubData({
      connections: [
        connectionFixture,
        { ...connectionFixture, id: 2, repo: "gadgets", status: "error", last_error: "installation revoked" },
      ],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByTestId("connection-1")).toBeInTheDocument());
    expect(within(screen.getByTestId("connection-1")).getByText("acme/widgets")).toBeInTheDocument();
    expect(within(screen.getByTestId("connection-1")).getByText("connected")).toBeInTheDocument();
    expect(within(screen.getByTestId("connection-2")).getByText("error")).toBeInTheDocument();
    expect(within(screen.getByTestId("connection-2")).getByTestId("connection-2-error")).toHaveTextContent(
      "installation revoked",
    );
  });

  test("shows the publish jobs list with a PR link once created", async () => {
    mockGithubData({
      connections: [connectionFixture],
      jobs: [
        jobFixture({
          id: 2, status: "completed", commit_sha: "def4567890",
          pr_url: "https://github.com/acme/widgets/pull/7", pr_number: 7,
        }),
      ],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-2")).toBeInTheDocument());
    const row = within(screen.getByTestId("publish-job-2"));
    expect(row.getByText("completed")).toBeInTheDocument();
    expect(row.getByText(/PR #7/)).toBeInTheDocument();
  });

  test("approve button is shown only for a job awaiting approval", async () => {
    mockGithubData({
      connections: [connectionFixture],
      jobs: [jobFixture({ id: 3, status: "awaiting_approval" })],
      patches: [patchFixture],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-3")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-3"));

    expect(await screen.findByTestId("publish-job-approve-button")).toBeInTheDocument();
    expect(screen.getByTestId("publish-job-cancel-button")).toBeInTheDocument();
    // The confirmation dialog shows the publish target before approving.
    fireEvent.click(screen.getByTestId("publish-job-approve-button"));
    expect(await screen.findByText("Publishを承認")).toBeInTheDocument();
    expect(screen.getByTestId("publish-job-confirm-approve-button")).toBeInTheDocument();
  });

  test("approval confirmation dialog shows the patch diff alongside the approve action (Issue #264)", async () => {
    mockGithubData({
      connections: [connectionFixture],
      jobs: [jobFixture({ id: 3, status: "awaiting_approval" })],
      patches: [patchFixture],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-3")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-3"));
    fireEvent.click(await screen.findByTestId("publish-job-approve-button"));

    // The confirmation dialog is on screen at the same time as the diff and
    // the approve action -- the diff must not only exist in the parent
    // dialog, which is covered by the confirmation overlay.
    expect(await screen.findByText("Publishを承認")).toBeInTheDocument();
    const confirmDiff = await screen.findByTestId("publish-job-confirm-diff");
    expect(confirmDiff).toHaveTextContent(patchFixture.diff);
    expect(screen.queryByTestId("publish-job-confirm-diff-unavailable")).not.toBeInTheDocument();
    expect(screen.getByTestId("publish-job-confirm-approve-button")).toBeInTheDocument();
    expect(screen.getByTestId("publish-job-confirm-approve-button")).not.toBeDisabled();
  });

  test("approval confirmation button stays disabled and warns when the patch diff cannot be found (fail-closed)", async () => {
    mockGithubData({
      connections: [connectionFixture],
      jobs: [jobFixture({ id: 3, status: "awaiting_approval" })],
      patches: [], // job.patch_id (20) is not present in the probe-patches list
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-3")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-3"));
    fireEvent.click(await screen.findByTestId("publish-job-approve-button"));

    expect(await screen.findByTestId("publish-job-confirm-diff-unavailable")).toHaveTextContent(
      "Patch diffを取得できないため、承認できません。",
    );
    expect(screen.queryByTestId("publish-job-confirm-diff")).not.toBeInTheDocument();
    expect(screen.getByTestId("publish-job-confirm-approve-button")).toBeDisabled();
  });

  test("approval confirmation button stays disabled and warns when the probe-patches fetch fails (fail-closed)", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/github/app-status") {
        return Promise.resolve({
          configured: true, app_id: "123",
          api_base_url: "https://api.github.com", web_base_url: "https://github.com",
        });
      }
      if (path === "/github/connections") return Promise.resolve([connectionFixture]);
      if (path === "/github/publish-jobs") return Promise.resolve([jobFixture({ id: 3, status: "awaiting_approval" })]);
      if (path === "/repository/probe-patches") return Promise.reject(new ApiError(500, "boom"));
      if (path === "/users") return Promise.resolve([]);
      return Promise.resolve(null);
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-3")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-3"));
    fireEvent.click(await screen.findByTestId("publish-job-approve-button"));

    expect(await screen.findByTestId("publish-job-confirm-diff-unavailable")).toHaveTextContent(
      "Patch diffの取得に失敗したため、承認できません。",
    );
    expect(screen.getByTestId("publish-job-confirm-approve-button")).toBeDisabled();
  });

  test("approve button is hidden for a completed job", async () => {
    mockGithubData({
      connections: [connectionFixture],
      jobs: [jobFixture({ id: 4, status: "completed", commit_sha: "def4567890" })],
      patches: [patchFixture],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-4")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-4"));

    await waitFor(() => expect(screen.getByTestId("publish-job-detail")).toBeInTheDocument());
    expect(screen.queryByTestId("publish-job-approve-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("publish-job-cancel-button")).not.toBeInTheDocument();
  });

  test("confirming approval calls the approve endpoint for the selected job", async () => {
    mockGithubData({
      connections: [connectionFixture],
      jobs: [jobFixture({ id: 5, status: "awaiting_approval" })],
      patches: [patchFixture],
    });
    mockApi.post.mockResolvedValue(jobFixture({ id: 5, status: "committing" }));

    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-5")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-5"));
    fireEvent.click(await screen.findByTestId("publish-job-approve-button"));
    fireEvent.click(await screen.findByTestId("publish-job-confirm-approve-button"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/github/publish-jobs/5/approve");
    });
  });

  test("cancelling a job calls the cancel endpoint", async () => {
    mockGithubData({
      connections: [connectionFixture],
      jobs: [jobFixture({ id: 6, status: "pending" })],
      patches: [patchFixture],
    });
    mockApi.post.mockResolvedValue(jobFixture({ id: 6, status: "cancelled" }));

    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-6")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-6"));
    fireEvent.click(await screen.findByTestId("publish-job-cancel-button"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/github/publish-jobs/6/cancel");
    });
  });

  test("shows the sanitized error for a failed job in the list and detail view", async () => {
    mockGithubData({
      connections: [connectionFixture],
      jobs: [jobFixture({
        id: 7, status: "failed",
        error: "Base branch has moved since the patch was generated/validated",
      })],
      patches: [patchFixture],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-7-error")).toHaveTextContent(
      "Base branch has moved since the patch was generated/validated",
    ));

    fireEvent.click(screen.getByTestId("publish-job-7"));
    expect(await screen.findByTestId("publish-job-detail-error")).toHaveTextContent(
      "Base branch has moved since the patch was generated/validated",
    );
    expect(screen.queryByTestId("publish-job-approve-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("publish-job-cancel-button")).not.toBeInTheDocument();
  });
});

// ── GitHub ?patch= preselection (Issue #259) ────────────────────────

describe("GitHub ?patch= preselection (Issue #259)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const connectedConnectionFixture = {
    id: 1, system_id: 1,
    api_base_url: "https://api.github.com", web_base_url: "https://github.com",
    owner: "acme", repo: "widgets", clone_url: "https://github.com/acme/widgets.git",
    installation_id: 42, default_branch: "main", credential_type: "github_app",
    status: "connected", last_error: null, last_synced_at: null, last_synced_commit_sha: null,
    created_by_user_id: 1, updated_by_user_id: 1,
    created_at: "2024-01-01T00:00:00Z", updated_at: "2024-01-01T00:00:00Z",
  };

  function greenPatchFixture(overrides: Record<string, unknown> = {}) {
    return {
      id: 20, plan_id: 10, system_id: 1, snapshot_id: 5,
      commit_sha: "abc1234567890", diff: "diff --git a/a.py b/a.py",
      worktree_path: null, skipped: [], status: "generated", error: null,
      cleanup_state: "removed", cleanup_error: null,
      apply_status: "applied", apply_error: null, applied_at: null, applied_by_user_id: null,
      validation_runs: [
        { id: 1, variant: "baseline", overall_success: true, commands: [] },
        { id: 2, variant: "probed", overall_success: true, commands: [] },
      ],
      created_at: "2024-01-01",
      ...overrides,
    };
  }

  function mockGithubForPatchParam(data: {
    connections?: Record<string, unknown>[];
    patches?: Record<string, unknown>[];
  }) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/github/app-status") return Promise.resolve({
        configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com",
      });
      if (path === "/github/connections") return Promise.resolve(data.connections ?? []);
      if (path === "/github/publish-jobs") return Promise.resolve([]);
      if (path === "/repository/probe-patches") return Promise.resolve(data.patches ?? []);
      if (path === "/users") return Promise.resolve([]);
      return Promise.resolve(null);
    });
  }

  function renderGithubAt(route: string) {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    return import("@/pages/github").then(({ default: GithubPage }) =>
      render(
        <QueryClientProvider client={qc}>
          <MemoryRouter initialEntries={[route]}>
            <GithubPage />
          </MemoryRouter>
        </QueryClientProvider>,
      ),
    );
  }

  test("preselects a valid, green patch from ?patch= and opens the create-job dialog on the Publish Jobs tab", async () => {
    mockGithubForPatchParam({
      connections: [connectedConnectionFixture],
      patches: [greenPatchFixture()],
    });
    await renderGithubAt("/github?patch=20");

    const select = await screen.findByTestId("publish-job-patch-select") as HTMLSelectElement;
    expect(select.value).toBe("20");
  });

  test("ignores ?patch= for a patch that fails the green-validation gate", async () => {
    mockGithubForPatchParam({
      connections: [connectedConnectionFixture],
      patches: [greenPatchFixture({
        validation_runs: [{ id: 1, variant: "baseline", overall_success: false, commands: [] }],
      })],
    });
    await renderGithubAt("/github?patch=20");

    // Still lands on the Publish Jobs tab (the button below is proof of that)
    // but does not auto-open the create dialog for an invalid patch.
    await waitFor(() => expect(screen.getByTestId("new-publish-job-button")).toBeInTheDocument());
    expect(screen.queryByTestId("publish-job-patch-select")).not.toBeInTheDocument();
  });

  test("ignores ?patch= for a patch id that does not exist", async () => {
    mockGithubForPatchParam({
      connections: [connectedConnectionFixture],
      patches: [greenPatchFixture({ id: 99 })],
    });
    await renderGithubAt("/github?patch=20");

    await waitFor(() => expect(screen.getByTestId("new-publish-job-button")).toBeInTheDocument());
    expect(screen.queryByTestId("publish-job-patch-select")).not.toBeInTheDocument();
  });
});

// ── Replay / Simulation Workbench (Issue #242 Phase D / #246) ──────────────

function replayComponentsFixture() {
  return [{ component_id: "norm", mode: "trace", trace_count: 2, last_seen: 1000 }];
}

function replayTracesFixture() {
  return [
    {
      trace_id: "trace-replayable-0001",
      component_id: "norm",
      mode: "trace",
      input: { args: ["hi"], kwargs: {} },
      output: "{'kind': 'a'}",
      error: null,
      duration_ms: 3,
      timestamp: 1000,
      input_capture: { args: ["hi"], kwargs: {} },
      replayability: "replayable",
      replay_reasons: [],
    },
    {
      trace_id: "trace-unreplayable-0002",
      component_id: "norm",
      mode: "trace",
      input: { args: ["big"], kwargs: {} },
      output: null,
      error: "ValueError: boom",
      duration_ms: 4,
      timestamp: 1001,
      input_capture: null,
      replayability: "unreplayable",
      replay_reasons: ["size_limit_exceeded"],
    },
  ];
}

function setupComponentsPageForReplay(extra: Record<string, unknown> = {}) {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/components") return Promise.resolve(replayComponentsFixture());
    if (path === "/components/norm/traces?limit=20") return Promise.resolve(replayTracesFixture());
    if (path === "/components/norm/profile") return Promise.resolve(null);
    if (path === "/components/norm/shadow-results?limit=20") return Promise.resolve([]);
    if (path === "/components/norm/criteria") return Promise.resolve([]);
    if (path === "/replay-sets?component_id=norm") return Promise.resolve([]);
    if (path === "/experiments") return Promise.resolve([]);
    if (path === "/repository/snapshots") return Promise.resolve([]);
    if (path === "/repository/drafts/latest") return Promise.resolve({ feature_drafts: [] });
    if (path in extra) return Promise.resolve(extra[path]);
    return Promise.resolve(null);
  });
}

describe("Components trace row: Replay actions (Issue #246)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  async function renderExpanded() {
    setupComponentsPageForReplay();
    const { default: ComponentsPage } = await import("@/pages/components");
    const { default: SimulationWorkbenchPage } = await import("@/pages/simulation-workbench");
    const { default: ExperimentsPage } = await import("@/pages/experiments");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/components?component=norm"]}>
          <Routes>
            <Route path="/components" element={<ComponentsPage />} />
            <Route path="/simulation-workbench" element={<SimulationWorkbenchPage />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const expandButton = await screen.findByRole("button", {
      name: "Trace trace-replayable-0001 の詳細を表示",
    });
    fireEvent.click(expandButton);
    return within(expandButton.closest("tr")!.nextElementSibling as HTMLElement);
  }

  test("renders the replayability badge with a reason tooltip, row actions, and the trace workspace pin", async () => {
    setupComponentsPageForReplay();
    const { default: ComponentsPage } = await import("@/pages/components");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/components?component=norm"]}>
          <ComponentsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Base row badges (before expanding) show replayable / unreplayable.
    expect(await screen.findByText("replayable")).toBeInTheDocument();
    const unreplayableBadge = screen.getByText("unreplayable");
    expect(unreplayableBadge).toHaveAttribute("title", "Reasons: size_limit_exceeded");

    const expandButton = await screen.findByRole("button", {
      name: "Trace trace-replayable-0001 の詳細を表示",
    });
    fireEvent.click(expandButton);
    const row = within(expandButton.closest("tr")!.nextElementSibling as HTMLElement);

    expect(row.getByText("Replay")).toBeInTheDocument();
    expect(row.getByText("Replay Setに追加")).toBeInTheDocument();
    expect(row.getByText("Experimentを作成")).toBeInTheDocument();
    expect(row.getByText("Workspaceに追加")).toBeInTheDocument();
  });

  test("Replay adds the trace to a new Replay Set and navigates to the Workbench", async () => {
    const row = await renderExpanded();
    mockApi.post.mockResolvedValue({
      id: 77, system_id: 1, component_id: "norm", name: "Replay set: norm",
      source: "manual", source_analyzer_run_id: null,
      trace_ids: ["trace-replayable-0001"], traces: [], created_at: 1,
    });

    fireEvent.click(row.getByText("Replay"));
    const replayButtons = await screen.findAllByText("Replay", { selector: "button" });
    fireEvent.click(replayButtons[replayButtons.length - 1]);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/replay-sets", expect.objectContaining({
        component_id: "norm",
        trace_ids: ["trace-replayable-0001"],
      }));
    });
    await waitFor(() => {
      expect(screen.getByText("Simulation Workbench")).toBeInTheDocument();
    });
  });

  test("Add to Replay Set posts without navigating away from Components", async () => {
    const row = await renderExpanded();
    mockApi.post.mockResolvedValue({
      id: 78, system_id: 1, component_id: "norm", name: "Replay set: norm",
      source: "manual", source_analyzer_run_id: null,
      trace_ids: ["trace-replayable-0001"], traces: [], created_at: 1,
    });

    fireEvent.click(row.getByText("Replay Setに追加"));
    fireEvent.click(await screen.findByText("追加", { selector: "button" }));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/replay-sets", expect.objectContaining({
        component_id: "norm",
        trace_ids: ["trace-replayable-0001"],
      }));
    });
    expect(screen.queryByText("Simulation Workbench")).not.toBeInTheDocument();
  });

  test("Create Experiment from this trace routes to Experiments with prefilled context", async () => {
    const row = await renderExpanded();
    fireEvent.click(row.getByText("Experimentを作成"));

    await waitFor(() => {
      expect(screen.getByText(/Prefilled context from trace/)).toBeInTheDocument();
    });
    expect(
      screen.getByPlaceholderText("What are you trying to learn?"),
    ).toHaveValue("Investigate trace trace-replayable-0001 (component norm)");
  });
});

// --- Simulation Workbench page ----------------------------------------------

function replaySetFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 1, system_id: 1, component_id: "norm", name: "My Replay Set",
    source: "manual", source_analyzer_run_id: null,
    trace_ids: ["t1", "t2"],
    traces: [
      { trace_id: "t1", exists: true, replayability: "replayable", replay_reasons: [], input_source: "structured", skip_reason: null },
      { trace_id: "t2", exists: true, replayability: "unreplayable", replay_reasons: ["redacted"], input_source: null, skip_reason: "unreplayable_capture" },
    ],
    created_at: 1,
    ...overrides,
  };
}

function replayApprovalFixture(active: boolean, overrides: Record<string, unknown> = {}) {
  return {
    component_id: "norm",
    active,
    approval: active
      ? { id: 1, system_id: 1, component_id: "norm", status: "approved", reason: "ok", approved_by_user_id: 1, decision_method: "manual", risk_context: null, created_at: 1, revoked_at: null, revoked_by_user_id: null }
      : null,
    risk_context: {
      probe_plan_points: [],
      warning: "Replay re-executes recorded inputs against the component's real implementation...",
    },
    ...overrides,
  };
}

function replaySourceFixture(overrides: Record<string, unknown> = {}) {
  return {
    replay_set_id: 1, component_id: "norm", snapshot_id: 5, commit_sha: "abcdef1234567890",
    path: "svc.py", qualified_name: "normalize", start_line: 5, end_line: 6,
    source: 'def normalize(payload):\n    return {"kind": "a"}\n',
    ...overrides,
  };
}

function variantRunFixture(overrides: Record<string, unknown> = {}) {
  const cases = [
    { id: 1, trace_id: "t1", position: 0, case_status: "match", comparison_mode: "structured", baseline_output: "A", candidate_output: "A", candidate_error: null, recorded_error: null, duration_ms: 1, duration_delta_ms: 0.1, field_diffs: [], output_truncated: false, created_at: 1 },
    { id: 2, trace_id: "t2", position: 1, case_status: "diff", comparison_mode: "structured", baseline_output: "A", candidate_output: "B", candidate_error: null, recorded_error: null, duration_ms: 1, duration_delta_ms: 0.2, field_diffs: ["kind"], output_truncated: false, created_at: 1 },
    { id: 3, trace_id: "t3", position: 2, case_status: "candidate_error", comparison_mode: null, baseline_output: "A", candidate_output: null, candidate_error: "RuntimeError: boom", recorded_error: null, duration_ms: 1, duration_delta_ms: null, field_diffs: [], output_truncated: false, created_at: 1 },
    { id: 4, trace_id: "t4", position: 3, case_status: "error_to_success", comparison_mode: "structured", baseline_output: null, candidate_output: "fixed", candidate_error: null, recorded_error: "ValueError: boom", duration_ms: 1, duration_delta_ms: -0.5, field_diffs: [], output_truncated: false, created_at: 1 },
  ];
  const baseline = {
    id: 10, replay_run_id: 1, variant_key: "baseline", label: "Baseline", is_baseline: true,
    patch_text: "", patch_hash: "h0", source: "manual", apply_status: "not_applicable",
    apply_error: null, status: "completed", error: null, workspace_path: null,
    cleanup_state: "removed", cleanup_error: null,
    aggregate: { match: 0, diff: 0, candidate_error: 0, error_to_success: 0, error_to_same_error: 0, error_to_different_error: 0, skipped: 0, total: 0, avg_duration_delta_ms: null, examples: {} },
    cases: [], created_at: 1, started_at: 1, completed_at: 1,
  };
  const candidate = {
    id: 11, replay_run_id: 1, variant_key: "variant-1", label: "My candidate", is_baseline: false,
    patch_text: "diff --git a/svc.py b/svc.py\n@@ -1,2 +1,2 @@\n-a\n+b\n", patch_hash: "h1",
    source: "manual", apply_status: "applied", apply_error: null, status: "completed", error: null,
    workspace_path: null, cleanup_state: "removed", cleanup_error: null,
    aggregate: { match: 1, diff: 1, candidate_error: 1, error_to_success: 1, error_to_same_error: 0, error_to_different_error: 0, skipped: 0, total: 4, avg_duration_delta_ms: -0.05, examples: {} },
    cases, created_at: 1, started_at: 1, completed_at: 1,
  };
  return {
    id: 1, system_id: 1, replay_set_id: 1, component_id: "norm", snapshot_id: 5,
    commit_sha: "abcdef1234567890", symbol_path: "svc.py", symbol_qualified_name: "normalize",
    status: "completed", error: null, trace_set_hash: "hash", sandbox_config: {}, approval_id: 1,
    variants: [baseline, candidate], created_at: 1, started_at: 1, completed_at: 1,
    ...overrides,
  };
}

function setupWorkbenchMocks(opts: {
  approved?: boolean;
  run?: Record<string, unknown> | null;
  extraGet?: Record<string, unknown>;
} = {}) {
  const approved = opts.approved ?? true;
  const run = opts.run === undefined ? variantRunFixture() : opts.run;
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/replay-sets") return Promise.resolve([replaySetFixture()]);
    if (path === "/replay-sets/1") return Promise.resolve(replaySetFixture());
    if (path === "/components/norm/replay-approval") return Promise.resolve(replayApprovalFixture(approved));
    if (path === "/components/norm/traces?limit=500") return Promise.resolve([]);
    if (path === "/replay-sets/1/source") return Promise.resolve(replaySourceFixture());
    if (path === "/replay-variant-runs?replay_set_id=1") return Promise.resolve(run ? [run] : []);
    if (path === "/replay-variant-runs/1") return Promise.resolve(run);
    if (path === "/replay-variant-runs/1/variants/11/experiment-payload") {
      const candidate = (run as Record<string, unknown> | null)?.variants
        ? ((run as { variants: Record<string, unknown>[] }).variants.find(v => v.id === 11))
        : undefined;
      return Promise.resolve({
        label: (candidate?.label as string) ?? "My candidate",
        patch_text: (candidate?.patch_text as string) ?? "",
        patch_hash: (candidate?.patch_hash as string) ?? "",
        source: "replay_variant",
        risk_note: "Promoted from replay-variant-run 1, variant variant-1 (component 'norm', snapshot 5).",
        origin: { replay_variant_run_id: 1, replay_variant_id: 11 },
      });
    }
    if (path === "/experiments") return Promise.resolve([]);
    if (path === "/repository/snapshots") return Promise.resolve([]);
    if (path === "/repository/drafts/latest") return Promise.resolve({ feature_drafts: [] });
    if (opts.extraGet && path in opts.extraGet) return Promise.resolve(opts.extraGet[path]);
    return Promise.resolve(null);
  });
}

async function renderWorkbenchAt(route: string) {
  const { default: SimulationWorkbenchPage } = await import("@/pages/simulation-workbench");
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <SimulationWorkbenchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Simulation Workbench (Issue #246)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("renders the three panes, the diff matrix distinctions, and the simulation disclaimer", async () => {
    setupWorkbenchMocks();
    await renderWorkbenchAt("/simulation-workbench?replay_set_id=1");

    await waitFor(() => {
      expect(screen.getByTestId("workbench-left-pane")).toBeInTheDocument();
      expect(screen.getByTestId("workbench-center-pane")).toBeInTheDocument();
      expect(screen.getByTestId("workbench-result-matrix")).toBeInTheDocument();
    });

    // Simulation disclaimer is always shown alongside the results.
    expect(screen.getByText(/シミュレーションのみ/)).toBeInTheDocument();

    // The diff matrix distinguishes match / diff / candidate error / rescued.
    await waitFor(() => {
      expect(screen.getByText("match")).toBeInTheDocument();
    });
    expect(screen.getByText("diff")).toBeInTheDocument();
    expect(screen.getByText("candidate error")).toBeInTheDocument();
    expect(screen.getByText("rescued")).toBeInTheDocument();

    // The unreplayable trace's next-step guidance is shown in the left pane.
    expect(screen.getByText(/次の一歩: 別のTraceを選ぶか/)).toBeInTheDocument();
  });

  test("unapproved component shows the not-approved next step and Approve posts", async () => {
    setupWorkbenchMocks({ approved: false, run: null });
    await renderWorkbenchAt("/simulation-workbench?replay_set_id=1");

    await waitFor(() => {
      expect(screen.getByText(/「norm」のReplayは未承認です/)).toBeInTheDocument();
    });
    expect(screen.getByText(/次の一歩: リスクの内容を確認し/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("確認して承認する"));
    fireEvent.change(
      await screen.findByPlaceholderText("このcomponentのReplayがなぜ安全か"),
      { target: { value: "It only normalizes text." } },
    );
    mockApi.post.mockResolvedValue({
      id: 1, system_id: 1, component_id: "norm", status: "approved", reason: "It only normalizes text.",
      approved_by_user_id: 1, decision_method: "manual", risk_context: null, created_at: 1,
      revoked_at: null, revoked_by_user_id: null,
    });
    fireEvent.click(screen.getByText("承認する", { selector: "button" }));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/components/norm/replay-approval",
        { reason: "It only normalizes text." },
      );
    });
  });

  test("LLM draft tab shows the provenance / is_mock badge", async () => {
    setupWorkbenchMocks({ run: null });
    await renderWorkbenchAt("/simulation-workbench?replay_set_id=1");

    fireEvent.click(await screen.findByText("LLM draft"));
    fireEvent.change(screen.getByRole("combobox", { name: "Trace for the LLM draft" }), { target: { value: "t1" } });
    fireEvent.change(
      screen.getByPlaceholderText("候補コードにどう変わってほしいか"),
      { target: { value: "Uppercase the result" } },
    );
    mockApi.post.mockResolvedValue({
      id: 1, system_id: 1, replay_set_id: 1, component_id: "norm", trace_id: "t1",
      objective: "Uppercase the result", snapshot_id: 5, symbol_path: "svc.py",
      symbol_qualified_name: "normalize", generated_code: "def normalize(x): ...",
      patch_text: "diff --git a/svc.py b/svc.py\n@@ -1,2 +1,2 @@\n-a\n+b\n", patch_hash: "hh",
      notes: "", status: "proposed", error: null, provider: "mock", model: "mock",
      prompt_version: "v1", schema_version: "v1", decision_method: "reasoning_llm",
      is_mock: true, created_at: 1,
    });
    fireEvent.click(screen.getByText("Draftを生成"));

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/replay-variant-drafts", expect.objectContaining({
        replay_set_id: 1, trace_id: "t1", objective: "Uppercase the result", snapshot_id: 5,
      }));
    });
    expect(await screen.findByText("mock LLM")).toBeInTheDocument();
    expect(screen.getByText("reasoning_llm")).toBeInTheDocument();
  });

  test("Direct edit -> Run calls the diff endpoint then the variant-run endpoint with the returned patch", async () => {
    setupWorkbenchMocks({ run: null });
    await renderWorkbenchAt("/simulation-workbench?replay_set_id=1");

    const textarea = await screen.findByDisplayValue(/def normalize/);
    fireEvent.change(textarea, {
      target: { value: 'def normalize(payload):\n    return {"kind": "b"}\n' },
    });

    mockApi.post.mockImplementation((path: string) => {
      if (path === "/replay-source-diff") {
        return Promise.resolve({ patch_text: "diff --git a/svc.py b/svc.py\n...", patch_hash: "phash" });
      }
      if (path === "/replay-variant-runs") return Promise.resolve(variantRunFixture());
      return Promise.resolve(null);
    });

    const runButtons = screen.getAllByText("Run");
    fireEvent.click(runButtons[0]);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/replay-source-diff", expect.objectContaining({
        replay_set_id: 1,
        snapshot_id: 5,
        edited_source: 'def normalize(payload):\n    return {"kind": "b"}\n',
      }));
    });
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/replay-variant-runs", expect.objectContaining({
        replay_set_id: 1,
        snapshot_id: 5,
        variants: [{ label: "Direct edit", patch_text: "diff --git a/svc.py b/svc.py\n...", source: "manual" }],
      }));
    });
  });

  test("Promote routes to Experiments with the patch fetched and prefilled", async () => {
    setupWorkbenchMocks();
    const { default: SimulationWorkbenchPage } = await import("@/pages/simulation-workbench");
    const { default: ExperimentsPage } = await import("@/pages/experiments");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/simulation-workbench?replay_set_id=1"]}>
          <Routes>
            <Route path="/simulation-workbench" element={<SimulationWorkbenchPage />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const promoteBtn = await screen.findByText('「My candidate」をpromote');
    fireEvent.click(promoteBtn);

    await waitFor(() => {
      expect(mockApi.get).toHaveBeenCalledWith(
        "/replay-variant-runs/1/variants/11/experiment-payload",
      );
    });
    await waitFor(() => {
      expect(screen.getByText(/Prefilled from Replay Variant run #1/)).toBeInTheDocument();
    });
    const patchTextarea = screen.getAllByPlaceholderText("Patch text (unified diff format)")[0];
    expect((patchTextarea as HTMLTextAreaElement).value).toContain("diff --git a/svc.py");
  });

  test("regression scaffold uses reasoning_llm and surfaces provenance", async () => {
    setupWorkbenchMocks();
    await renderWorkbenchAt("/simulation-workbench?replay_set_id=1");
    mockApi.post.mockResolvedValue({
      id: 7, intelligence_run_id: 8, replay_run_id: 1, replay_variant_id: 11,
      replay_set_id: 1, trace_id: "t1", snapshot_id: 5,
      scaffold_text: "def test_normalize_regression():\n    assert True\n",
      status: "proposed", error: null, provider: "mock", model: "mock",
      prompt_version: "replay-regression-scaffold-v1",
      schema_version: "replay-regression-scaffold-v1",
      decision_method: "reasoning_llm", is_mock: true, created_at: 1,
    });

    const generate = await screen.findByRole("button", {
      name: "回帰テストscaffoldを生成",
    });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);

    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/replay-regression-scaffolds", {
        replay_run_id: 1,
        replay_variant_id: 11,
        trace_id: "t1",
      });
    });
    expect(await screen.findByText("mock LLM")).toBeInTheDocument();
    expect(screen.getByText("run #8 · replay-regression-scaffold-v1")).toBeInTheDocument();
  });
});

// ── Phase-based prerequisite guide (Issue #241) ─────────────────────

describe("PrerequisiteGuide (Issue #241)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const guidePrimaryItem = {
    state_id: "repository.configuration.missing",
    state_group: "repository",
    severity: "warning",
    status: "missing",
    user_action_kind: "configure",
    intervention_timing: "now",
    subject: "リポジトリ設定",
    summary: "対象リポジトリが未設定です。",
    detail: "対象リポジトリが未設定です。",
    impact: "",
    remediation: "Repository タブでリポジトリを設定してください。",
    evidence: {},
    target_ui: { route: "/repository", anchor: "repo-config", action_label: "リポジトリを設定" },
    related_checks: [],
    related_pipeline_steps: [],
    source: "system_state",
    dedupe_key: "repository.configuration",
    scope: "global",
    decision_method: "deterministic",
    phase: "setup",
  };

  // Issue #256: preparation counts complete for any phase past
  // "setup"/"preparation" themselves (the later instrumentation / observation
  // / evaluation / publish phases all chain on preparation being done); the
  // later phases themselves are left incomplete here since none of these
  // tests need to distinguish between them.
  const stateWith = (
    userPhase: string,
    primaryItem: unknown = guidePrimaryItem,
    phaseLabels?: Record<string, string>,
  ) => ({
    system_id: 1,
    generated_at: 1,
    overall_severity: "warning",
    severity_counts: {},
    items: [],
    primary_item: primaryItem,
    notification_items: [],
    page_items: {},
    user_phase: userPhase,
    phases: [
      { phase: "setup", complete: userPhase !== "setup", label: phaseLabels?.setup },
      {
        phase: "preparation",
        complete: userPhase !== "setup" && userPhase !== "preparation",
        label: phaseLabels?.preparation,
      },
      { phase: "instrumentation", complete: false, label: phaseLabels?.instrumentation },
      { phase: "observation", complete: false, label: phaseLabels?.observation },
      { phase: "evaluation", complete: false, label: phaseLabels?.evaluation },
      { phase: "publish", complete: false, label: phaseLabels?.publish },
    ],
  });

  test("renders phase + next-step from system-state during setup", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state" ? Promise.resolve(stateWith("setup")) : Promise.resolve(null),
    );
    const { PrerequisiteGuide } = await import("@/components/prerequisite-guide");
    render(<PrerequisiteGuide />, { wrapper: createWrapper() });

    const guide = await screen.findByTestId("prerequisite-guide");
    expect(guide.getAttribute("data-current-phase")).toBe("setup");
    // Phase name and the server StateItem copy are shown verbatim.
    expect(screen.getByTestId("prerequisite-guide-phase").textContent).toContain("必要最低限の設定");
    expect(screen.getByText("対象リポジトリが未設定です。")).toBeTruthy();
    expect(screen.getByText("Repository タブでリポジトリを設定してください。")).toBeTruthy();
    // CTA carries the server-supplied action label.
    expect(screen.getByTestId("prerequisite-guide-cta").textContent).toContain("リポジトリを設定");
  });

  test("prefers the server-provided phase label over the client fallback map (Issue #240)", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state"
        ? Promise.resolve(stateWith("setup", guidePrimaryItem, { setup: "セットアップ完了" }))
        : Promise.resolve(null),
    );
    const { PrerequisiteGuide } = await import("@/components/prerequisite-guide");
    render(<PrerequisiteGuide />, { wrapper: createWrapper() });

    const guide = await screen.findByTestId("prerequisite-guide");
    expect(guide.getAttribute("data-current-phase")).toBe("setup");
    expect(screen.getByTestId("prerequisite-guide-phase").textContent).toContain("セットアップ完了");
  });

  test("disappears once preparation is complete (instrumentation phase onward)", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state" ? Promise.resolve(stateWith("instrumentation", null)) : Promise.resolve(null),
    );
    const { PrerequisiteGuide } = await import("@/components/prerequisite-guide");
    const { container } = render(<PrerequisiteGuide />, { wrapper: createWrapper() });

    // Give the query a tick to resolve, then assert nothing rendered.
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/system-state"));
    expect(screen.queryByTestId("prerequisite-guide")).not.toBeInTheDocument();
    expect(container.textContent).toBe("");
  });

  test("renders nothing for any later phase without authored copy (Issue #256)", async () => {
    // instrumentation / observation / evaluation / publish have no guide
    // copy of their own (that guidance UX belongs to sibling sub-issues
    // #257/#258) -- the guide must stay silent, not render a broken/empty
    // card, for every one of them.
    for (const phase of ["instrumentation", "observation", "evaluation", "publish"]) {
      vi.clearAllMocks();
      mockApi.get.mockImplementation((path: string) =>
        path === "/system-state" ? Promise.resolve(stateWith(phase, null)) : Promise.resolve(null),
      );
      const { PrerequisiteGuide } = await import("@/components/prerequisite-guide");
      const { container, unmount } = render(<PrerequisiteGuide />, { wrapper: createWrapper() });

      await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/system-state"));
      expect(screen.queryByTestId("prerequisite-guide")).not.toBeInTheDocument();
      expect(container.textContent).toBe("");
      unmount();
    }
  });

  test("CTA navigates to the StateItem target", async () => {
    window.history.pushState({}, "", "/");
    mockApi.get.mockImplementation((path: string) =>
      path === "/system-state" ? Promise.resolve(stateWith("setup")) : Promise.resolve(null),
    );
    const { PrerequisiteGuide } = await import("@/components/prerequisite-guide");
    render(<PrerequisiteGuide />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("prerequisite-guide-cta"));
    await waitFor(() => {
      expect(window.location.pathname).toBe("/repository");
    });
  });

  test("Probe Planner shows the gate in its generate dialog when preparation is incomplete", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateWith("preparation"));
      if (path === "/probe-plans") return Promise.resolve({ plans: [], is_mock: false });
      return Promise.resolve(null);
    });
    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("Generate Plan"));
    expect(await screen.findByTestId("planner-prerequisite-guide")).toBeInTheDocument();
  });

  test("Probe Planner hides the gate once preparation is complete", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(stateWith("instrumentation", null));
      if (path === "/probe-plans") return Promise.resolve({ plans: [], is_mock: false });
      return Promise.resolve(null);
    });
    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByText("Generate Plan"));
    // Dialog opened (feature label present) but no prerequisite gate.
    expect(await screen.findByText("Feature")).toBeInTheDocument();
    expect(screen.queryByTestId("planner-prerequisite-guide")).not.toBeInTheDocument();
  });
});

// ── Dangerous/no-op action gating (Issue #255) ──────────────────────
//
// Three dashboard actions were previously clickable in states where they
// could not succeed (or were dangerous), because state that was already
// fetched for display was never wired into the button's disabled condition.
// Each case below asserts the button is disabled AND a reason is visible —
// never a silent no-op and never a dangerous apply.

describe("Dashboard action gating (Issue #255)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("Probe Planner: Apply is disabled with a visible reason when the patch is stale vs HEAD", async () => {
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
    const reason = await screen.findByTestId("patch-apply-stale-reason");
    expect(reason).toHaveTextContent(/abcdef12/);
    expect(screen.getByRole("button", { name: /Apply/ })).toBeDisabled();

    const { toast } = await import("sonner");
    expect(toast.success).not.toHaveBeenCalledWith("Patch applied to repository");
  });

  test("Connect SDK: Issue Token is disabled with a visible reason when no System is selected", async () => {
    mockSystemId = null;
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/auth/my-tokens") return Promise.resolve([]);
      return Promise.resolve(null);
    });

    const { default: ConnectSdkPage } = await import("@/pages/connect-sdk");
    render(<ConnectSdkPage />, { wrapper: createWrapper() });

    const nameInput = await screen.findByPlaceholderText("my-service");
    fireEvent.change(nameInput, { target: { value: "my-new-token" } });

    expect(screen.getByTestId("issue-token-no-system-reason")).toBeInTheDocument();
    const issueButton = screen.getByRole("button", { name: "Issue Token" });
    expect(issueButton).toBeDisabled();

    // Even though the name is filled in, clicking the disabled button must
    // not silently reach handleIssue's early-return guard.
    fireEvent.click(issueButton);
    expect(mockApi.post).not.toHaveBeenCalled();
    const { toast } = await import("sonner");
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  test("Repository: Symbols tab Index Symbols is disabled under the same condition as the Refresh Hub", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository") return Promise.resolve({
        id: 1, system_id: 1, repo_path: "/repos/alpha", include_patterns: [], exclude_patterns: [],
      });
      if (path === "/repository-candidates") return Promise.resolve([{ name: "alpha", path: "/repos/alpha" }]);
      if (path === "/repository/snapshots") return Promise.resolve([]);
      if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
      if (path === "/repository/status") return Promise.resolve({
        configured: true, repo_path: "/repos/alpha",
        current_head: "abc1234000", head_error: null,
        working_tree_dirty: false, dirty_file_count: 0, dirty_sample: [],
        latest_snapshot: null, latest_indexed_snapshot: null,
        understanding_snapshot_id: null, understanding_status: null,
        snapshot_stale: true, symbols_stale: false, next_actions: [],
      });
      return Promise.resolve(null);
    });

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    // Refresh Hub's own button is disabled when there is no snapshot.
    const hub = await screen.findByTestId("refresh-hub");
    expect(within(hub).getByRole("button", { name: "Index symbols" })).toBeDisabled();

    // The Symbols tab's button must be gated the same way instead of being
    // clickable with nothing to index.
    fireEvent.click(screen.getByRole("button", { name: "Symbols" }));
    expect(await screen.findByTestId("index-symbols-no-snapshot-reason")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Index Symbols" })).toBeDisabled();
  });
});

// ── Prerequisite-based action gating (Issue #258) ───────────────────
//
// Several generate/execute buttons were previously disabled only while a
// mutation was `isPending`, so a user could click straight into a
// guaranteed server rejection (no ready snapshot, no approved probe point,
// a failed patch, a component with zero recorded traces). Each case below
// asserts the button is disabled AND a reason + link/next-step is visible,
// that an unknown/loading precondition never blocks (escape hatch), and
// that satisfying the precondition re-enables the button without any
// manual refetch.

describe("Prerequisite-based action gating (Issue #258)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  function repoStatusFixture(overrides: Record<string, unknown> = {}) {
    return {
      configured: true, repo_path: "/repos/alpha",
      current_head: "abc1234000", head_error: null,
      working_tree_dirty: false, dirty_file_count: 0, dirty_sample: [],
      latest_snapshot: { id: 1, commit_sha: "abc1234000", status: "ready", created_at: 1 },
      latest_indexed_snapshot: null,
      understanding_snapshot_id: null, understanding_status: null,
      snapshot_stale: false, symbols_stale: false, next_actions: [],
      ...overrides,
    };
  }

  const repoConfigMissingItem = {
    state_id: "repository.configuration.missing",
    state_group: "repository",
    severity: "warning",
    status: "missing",
    user_action_kind: "configure",
    intervention_timing: "now",
    subject: "Repository",
    summary: "対象リポジトリが未設定です。",
    detail: "対象リポジトリが未設定です。",
    impact: "",
    remediation: "Repository タブでリポジトリを設定してください。",
    evidence: {},
    target_ui: { route: "/repository", anchor: "repo-config", action_label: "リポジトリを設定" },
    related_checks: [],
    related_pipeline_steps: [],
    source: "system_state",
    dedupe_key: "repository.configuration",
    scope: "global",
    decision_method: "deterministic",
    phase: "setup",
  };

  function systemStateFixture(pageItems: Record<string, unknown[]> = {}) {
    return {
      system_id: 1, generated_at: 1, overall_severity: "ok",
      severity_counts: {}, items: [], primary_item: null,
      notification_items: [], page_items: pageItems,
    };
  }

  // ── Probe Planner: Generate Plan ──────────────────────────────────

  test("Probe Planner: Generate Plan is disabled with a reason when the repository is unconfigured", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({ system_id: 1, is_mock: false, plans: [] });
      if (path === "/repository/status") return Promise.resolve(
        repoStatusFixture({ configured: false, repo_path: null, latest_snapshot: null }),
      );
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    const button = await screen.findByRole("button", { name: "Generate Plan" });
    await waitFor(() => expect(button).toBeDisabled());
    expect(await screen.findByTestId("generate-plan-blocked-reason")).toBeInTheDocument();

    // Clicking a disabled button must not open the dialog / reach the mutation.
    fireEvent.click(button);
    expect(screen.queryByText("Observation objective")).not.toBeInTheDocument();
  });

  test("Probe Planner: Generate Plan's reason reuses the repository.configuration.missing catalog copy when present", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({ system_id: 1, is_mock: false, plans: [] });
      if (path === "/repository/status") return Promise.resolve(
        repoStatusFixture({ configured: false, repo_path: null, latest_snapshot: null }),
      );
      if (path === "/system-state") return Promise.resolve(
        systemStateFixture({ "/repository": [repoConfigMissingItem] }),
      );
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    const reason = await screen.findByTestId("generate-plan-blocked-reason");
    expect(reason).toHaveTextContent("対象リポジトリが未設定です。");
    expect(reason).toHaveTextContent("Repository タブでリポジトリを設定してください。");
    const link = within(reason).getByRole("link", { name: "リポジトリを設定" });
    expect(link).toHaveAttribute("href", "/repository?fix=repo-config");
  });

  test("Probe Planner: Generate Plan is disabled with a reason when there is no ready snapshot", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({ system_id: 1, is_mock: false, plans: [] });
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture({ latest_snapshot: null }));
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    const button = await screen.findByRole("button", { name: "Generate Plan" });
    await waitFor(() => expect(button).toBeDisabled());
    expect(await screen.findByTestId("generate-plan-blocked-reason")).toHaveTextContent(
      /no ready repository snapshot/i,
    );
  });

  test("Probe Planner: Generate Plan stays enabled when repository status is unknown (escape hatch)", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({ system_id: 1, is_mock: false, plans: [] });
      // /repository/status intentionally left unhandled -> resolves to null,
      // an indeterminate state that must never block the button.
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/repository/status"));
    const button = await screen.findByRole("button", { name: "Generate Plan" });
    expect(button).not.toBeDisabled();
    expect(screen.queryByTestId("generate-plan-blocked-reason")).not.toBeInTheDocument();
  });

  test("Probe Planner: Generate Plan is enabled once the repository is configured with a ready snapshot", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({ system_id: 1, is_mock: false, plans: [] });
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture());
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    const button = await screen.findByRole("button", { name: "Generate Plan" });
    await waitFor(() => expect(button).not.toBeDisabled());
    expect(screen.queryByTestId("generate-plan-blocked-reason")).not.toBeInTheDocument();
  });

  // ── Probe Planner: Generate Patch / Validate ──────────────────────

  function probePointFixture(overrides: Record<string, unknown> = {}) {
    return {
      id: 100, plan_id: 10, system_id: 1, component_id: "comp-a", feature_id: "feat-1",
      path: "a.py", symbol: "foo", line_start: 1, line_end: 5, reason: "observe",
      recommended_mode: "trace", side_effect_risk: "low", replayability: "safe",
      denylist_hit: null, status: "proposed", created_at: "2024-01-01", updated_at: "2024-01-01",
      ...overrides,
    };
  }

  test("Probe Planner: Generate Patch is disabled with a reason when the plan has no approved probe points", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({
        system_id: 1, is_mock: false,
        plans: [{
          id: 10, feature_id: "feat-1", objective: "Observe", status: "proposed", created_at: "2024-01-01",
          probe_points: [probePointFixture({ status: "proposed" })],
        }],
      });
      if (path === "/repository/probe-patches") return Promise.resolve([]);
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture());
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Feature: feat-1"));

    const genPatchButton = await screen.findByRole("button", { name: /Generate Patch/ });
    expect(genPatchButton).toBeDisabled();
    expect(await screen.findByTestId("generate-patch-no-points-reason")).toBeInTheDocument();
  });

  test("Probe Planner: Generate Patch is disabled when the only approved point is safety-denylisted", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({
        system_id: 1, is_mock: false,
        plans: [{
          id: 10, feature_id: "feat-1", objective: "Observe", status: "proposed", created_at: "2024-01-01",
          probe_points: [probePointFixture({ status: "approved", denylist_hit: "payment write" })],
        }],
      });
      if (path === "/repository/probe-patches") return Promise.resolve([]);
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture());
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Feature: feat-1"));

    const genPatchButton = await screen.findByRole("button", { name: /Generate Patch/ });
    expect(genPatchButton).toBeDisabled();
    expect(await screen.findByTestId("generate-patch-no-points-reason")).toBeInTheDocument();
  });

  test("Probe Planner: Generate Patch is enabled once at least one non-denylisted probe point is approved", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({
        system_id: 1, is_mock: false,
        plans: [{
          id: 10, feature_id: "feat-1", objective: "Observe", status: "proposed", created_at: "2024-01-01",
          probe_points: [
            probePointFixture({ id: 101, status: "rejected" }),
            probePointFixture({ id: 102, status: "approved", denylist_hit: null }),
          ],
        }],
      });
      if (path === "/repository/probe-patches") return Promise.resolve([]);
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture());
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Feature: feat-1"));

    const genPatchButton = await screen.findByRole("button", { name: /Generate Patch/ });
    await waitFor(() => expect(genPatchButton).not.toBeDisabled());
    expect(screen.queryByTestId("generate-patch-no-points-reason")).not.toBeInTheDocument();
  });

  test("Probe Planner: Validate is disabled with a reason when the patch failed to generate", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({
        system_id: 1, is_mock: false,
        plans: [{ id: 10, feature_id: "feat-1", objective: "Observe", status: "proposed", created_at: "2024-01-01", probe_points: [] }],
      });
      if (path === "/repository/probe-patches") return Promise.resolve([{
        id: 20, plan_id: 10, system_id: 1, snapshot_id: 5,
        commit_sha: "abcdef1234567890", diff: "", worktree_path: null, skipped: [],
        status: "failed", error: "git apply failed", cleanup_state: "removed", cleanup_error: null,
        apply_status: "not_applied", apply_error: null, applied_at: null,
        applied_by_user_id: null, validation_runs: [], created_at: "2024-01-01",
      }]);
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture());
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Feature: feat-1"));

    const validateButton = await screen.findByRole("button", { name: /Validate/ });
    expect(validateButton).toBeDisabled();
    const reason = await screen.findByTestId("validate-patch-failed-reason");
    expect(reason).toHaveTextContent("git apply failed");
  });

  test("Probe Planner: Validate is enabled for a successfully generated patch", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/probe-plans") return Promise.resolve({
        system_id: 1, is_mock: false,
        plans: [{ id: 10, feature_id: "feat-1", objective: "Observe", status: "proposed", created_at: "2024-01-01", probe_points: [] }],
      });
      if (path === "/repository/probe-patches") return Promise.resolve([{
        id: 20, plan_id: 10, system_id: 1, snapshot_id: 5,
        commit_sha: "abcdef1234567890", diff: "diff --git a/a.py b/a.py", worktree_path: null, skipped: [],
        status: "generated", error: null, cleanup_state: "removed", cleanup_error: null,
        apply_status: "not_applied", apply_error: null, applied_at: null,
        applied_by_user_id: null, validation_runs: [], created_at: "2024-01-01",
      }]);
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture());
      return Promise.resolve(null);
    });

    const { default: ProbePlannerPage } = await import("@/pages/probe-planner");
    render(<ProbePlannerPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(screen.getByText("Feature: feat-1")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Feature: feat-1"));

    const validateButton = await screen.findByRole("button", { name: /Validate/ });
    await waitFor(() => expect(validateButton).not.toBeDisabled());
    expect(screen.queryByTestId("validate-patch-failed-reason")).not.toBeInTheDocument();
  });

  // ── System Understanding: Build / Refresh ─────────────────────────

  test("System Understanding: Build / Refresh is disabled with a reason when the repository is unconfigured", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/status") return Promise.resolve(
        repoStatusFixture({ configured: false, repo_path: null, latest_snapshot: null }),
      );
      if (path === "/repository/system-understanding") return Promise.resolve(null);
      if (path === "/repository/system-understanding/build/latest") return Promise.resolve(null);
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const button = await screen.findByTestId("build-button");
    await waitFor(() => expect(button).toBeDisabled());
    expect(await screen.findByTestId("build-blocked-reason")).toBeInTheDocument();
  });

  test("System Understanding: Build / Refresh stays enabled with no ready snapshot (narrower gate than Probe Planner)", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture({ latest_snapshot: null }));
      if (path === "/repository/system-understanding") return Promise.resolve(null);
      if (path === "/repository/system-understanding/build/latest") return Promise.resolve(null);
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const button = await screen.findByTestId("build-button");
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/repository/status"));
    expect(button).not.toBeDisabled();
    expect(screen.queryByTestId("build-blocked-reason")).not.toBeInTheDocument();
  });

  test("System Understanding: Build / Refresh stays enabled when repository status is unknown (escape hatch)", async () => {
    mockApi.get.mockImplementation((path: string) => {
      // /repository/status intentionally left unhandled -> resolves to null.
      if (path === "/repository/system-understanding") return Promise.resolve(null);
      if (path === "/repository/system-understanding/build/latest") return Promise.resolve(null);
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/repository/status"));
    const button = await screen.findByTestId("build-button");
    expect(button).not.toBeDisabled();
  });

  test("System Understanding: Build / Refresh is enabled once the repository is configured", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/repository/status") return Promise.resolve(repoStatusFixture());
      if (path === "/repository/system-understanding") return Promise.resolve(null);
      if (path === "/repository/system-understanding/build/latest") return Promise.resolve(null);
      return Promise.resolve(null);
    });

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    const button = await screen.findByTestId("build-button");
    await waitFor(() => expect(button).not.toBeDisabled());
    expect(screen.queryByTestId("build-blocked-reason")).not.toBeInTheDocument();
  });

  // ── Repository: Create Snapshot (Snapshots tab) ───────────────────

  function repositoryBaseGet(status: Record<string, unknown>) {
    return (path: string) => {
      if (path === "/repository") return Promise.resolve(
        status.configured
          ? { id: 1, system_id: 1, repo_path: "/repos/alpha", include_patterns: [], exclude_patterns: [] }
          : null,
      );
      if (path === "/repository-candidates") return Promise.resolve([{ name: "alpha", path: "/repos/alpha" }]);
      if (path === "/repository/snapshots") return Promise.resolve([]);
      if (path === "/repository/symbols") return Promise.resolve({ symbols: [], symbol_count: 0 });
      if (path === "/repository/status") return Promise.resolve(status);
      return Promise.resolve(null);
    };
  }

  test("Repository: Create Snapshot (Snapshots tab) is disabled with a reason when the repository is unconfigured", async () => {
    mockApi.get.mockImplementation(repositoryBaseGet(
      repoStatusFixture({ configured: false, repo_path: null, latest_snapshot: null }),
    ));

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByRole("button", { name: "Snapshots" }));
    const button = await screen.findByRole("button", { name: /Create Snapshot/ });
    await waitFor(() => expect(button).toBeDisabled());
    expect(await screen.findByTestId("create-snapshot-not-configured-reason")).toBeInTheDocument();
  });

  test("Repository: Create Snapshot (Snapshots tab) is enabled when configured even with zero snapshots (no chicken-and-egg gate)", async () => {
    mockApi.get.mockImplementation(repositoryBaseGet(
      repoStatusFixture({ latest_snapshot: null }),
    ));

    const { default: RepositoryPage } = await import("@/pages/repository");
    render(<RepositoryPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByRole("button", { name: "Snapshots" }));
    const button = await screen.findByRole("button", { name: /Create Snapshot/ });
    await waitFor(() => expect(button).not.toBeDisabled());
    expect(screen.queryByTestId("create-snapshot-not-configured-reason")).not.toBeInTheDocument();
  });

  // ── Components: candidate generation / shadow mode vs. trace count ─

  function componentsGet(component: Record<string, unknown>) {
    return (path: string) => {
      if (path === "/components") return Promise.resolve([component]);
      if (path.startsWith("/components/") && path.includes("/traces")) return Promise.resolve([]);
      if (path.endsWith("/profile")) return Promise.resolve(null);
      if (path.endsWith("/shadow-results?limit=20")) return Promise.resolve([]);
      if (path.endsWith("/criteria")) return Promise.resolve([]);
      return Promise.resolve(null);
    };
  }

  test("Components: AI candidate generation and shadow mode are disabled with a reason for a zero-trace component", async () => {
    window.history.pushState({}, "", "/components?component=comp-a");
    mockApi.get.mockImplementation(
      componentsGet({ component_id: "comp-a", mode: "off", trace_count: 0, last_seen: null }),
    );

    const { default: ComponentsPage } = await import("@/pages/components");
    render(<ComponentsPage />, { wrapper: createWrapper() });

    const aiButton = await screen.findByRole("button", { name: /AIで別バージョンを作る/ });
    await waitFor(() => expect(aiButton).toBeDisabled());
    const shadowButton = screen.getByRole("button", { name: "shadow" });
    expect(shadowButton).toBeDisabled();
    expect(await screen.findByTestId("component-zero-traces-reason")).toBeInTheDocument();

    // The escape hatch for mode switching stays open: off/trace must never
    // be gated on trace count, since switching to trace is how a component
    // starts collecting its first traces.
    expect(screen.getByRole("button", { name: "off" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "trace" })).not.toBeDisabled();
  });

  test("Components: AI candidate generation and shadow mode are enabled once the component has recorded traces", async () => {
    window.history.pushState({}, "", "/components?component=comp-a");
    mockApi.get.mockImplementation(
      componentsGet({ component_id: "comp-a", mode: "trace", trace_count: 5, last_seen: 1000 }),
    );

    const { default: ComponentsPage } = await import("@/pages/components");
    render(<ComponentsPage />, { wrapper: createWrapper() });

    const aiButton = await screen.findByRole("button", { name: /AIで別バージョンを作る/ });
    await waitFor(() => expect(aiButton).not.toBeDisabled());
    expect(screen.getByRole("button", { name: "shadow" })).not.toBeDisabled();
    expect(screen.queryByTestId("component-zero-traces-reason")).not.toBeInTheDocument();
  });

  // ── Issue #267 item 3: mode explanation next to the policy toggle ──

  test("Components: mode toggle shows an explanation of off/trace/shadow and the shadow guarantee", async () => {
    window.history.pushState({}, "", "/components?component=comp-a");
    mockApi.get.mockImplementation(
      componentsGet({ component_id: "comp-a", mode: "trace", trace_count: 5, last_seen: 1000 }),
    );

    const { default: ComponentsPage } = await import("@/pages/components");
    render(<ComponentsPage />, { wrapper: createWrapper() });

    const note = await screen.findByTestId("component-mode-explanation");
    expect(note).toHaveTextContent("本番の戻り値を変更しません");
  });
});

// ── Issue #267 item 4: 送信 vs 候補を生成 note in AI Candidate Studio ──

describe("AI Candidate Studio send-vs-generate note (Issue #267)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("shows a note distinguishing 送信 (conversation only) from 候補を生成", async () => {
    window.history.pushState({}, "", "/candidate-studio?session_id=1");
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/candidate-sessions/1") {
        return Promise.resolve({
          id: 1, system_id: 1, component_id: "comp-a", snapshot_id: 1,
          commit_sha: "abc123", symbol_path: "a.py", symbol_qualified_name: "comp_a",
          replay_set_id: 1, objective: "improve accuracy",
          status: "active", created_at: 1, updated_at: 1,
          messages: [], versions: [],
        });
      }
      if (path.endsWith("/replay-approval")) return Promise.resolve({ active: false });
      return Promise.resolve(null);
    });

    const { default: CandidateStudioPage } = await import("@/pages/candidate-studio");
    render(<CandidateStudioPage />, { wrapper: createWrapper() });

    const note = await screen.findByTestId("candidate-studio-send-vs-generate-note");
    expect(note).toHaveTextContent("候補versionは作成しません");
  });
});

// ── Issue #267 item 10: legacy Generate page banner ─────────────────

describe("Generate page legacy banner (Issue #267)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("points to AI Candidate Studio", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/components") return Promise.resolve([]);
      if (path === "/generation-runs") return Promise.resolve([]);
      return Promise.resolve(null);
    });

    const { default: GenerationPage } = await import("@/pages/generation");
    render(<GenerationPage />, { wrapper: createWrapper() });

    const link = await screen.findByTestId("generation-legacy-banner-link");
    expect(link).toHaveAttribute("href", "/candidate-studio");
  });
});

// ── Issue #267 item 11: Observe & Evaluate sidebar subtexts ─────────

describe("Sidebar Observe & Evaluate subtexts (Issue #267)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  test("renders a usage-distinction subtext for each of the 7 pages", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/system-state") return Promise.resolve(null);
      return Promise.resolve(null);
    });

    const { Sidebar } = await import("@/components/layout/sidebar");
    render(<Sidebar />, { wrapper: createWrapper() });

    const group = screen.getByTestId("sidebar-group-observe-&-evaluate");
    expect(within(group).getByText("手動でdiffを編集して検証")).toBeInTheDocument();
    expect(within(group).getByText("会話でAIに指示して候補生成")).toBeInTheDocument();
  });
});

// ── Issue #267 items 5-9: GitHub publish workflow UX gaps ───────────

describe("GitHub publish workflow UX gaps (Issue #267)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
  });

  const connectionFixture267 = {
    id: 1, system_id: 1,
    api_base_url: "https://api.github.com", web_base_url: "https://github.com",
    owner: "acme", repo: "widgets", clone_url: "https://github.com/acme/widgets.git",
    installation_id: 42, default_branch: "main", credential_type: "github_app",
    status: "connected", last_error: null, last_synced_at: null, last_synced_commit_sha: null,
    created_by_user_id: 1, updated_by_user_id: 1,
    created_at: "2024-01-01T00:00:00Z", updated_at: "2024-01-01T00:00:00Z",
  };

  function jobFixture267(overrides: Record<string, unknown> = {}) {
    return {
      id: 1, system_id: 1, connection_id: 1, patch_id: 20, snapshot_id: 5,
      base_branch: "main", base_commit_sha: "abc1234567890",
      branch_name: "probe/publish-1-abc12345", commit_sha: null,
      pr_url: null, pr_number: null, status: "retryable_failed", error: "temporary network error",
      validation_summary: null, requested_by_user_id: 1, approved_by_user_id: null,
      cleanup_state: "not_attempted", cleanup_error: null,
      created_at: 1700000000, updated_at: 1700000000, approved_at: null, completed_at: null,
      heartbeat_at: null, retry_count: 1, last_attempt_at: null,
      ...overrides,
    };
  }

  function mockGithubData267(data: {
    appStatus?: Record<string, unknown>;
    connections?: Record<string, unknown>[];
    jobs?: Record<string, unknown>[];
    installations?: Record<string, unknown>[];
    events?: Record<string, unknown>[];
  }) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/github/app-status") {
        return Promise.resolve(
          data.appStatus ?? {
            configured: false, app_id: null,
            api_base_url: "https://api.github.com", web_base_url: "https://github.com",
          },
        );
      }
      if (path === "/github/connections") return Promise.resolve(data.connections ?? []);
      if (path === "/github/publish-jobs") return Promise.resolve(data.jobs ?? []);
      if (path === "/repository/probe-patches") return Promise.resolve([]);
      if (path === "/users") return Promise.resolve([]);
      if (path === "/github/installations") return Promise.resolve(data.installations ?? []);
      if (path.endsWith("/events")) return Promise.resolve(data.events ?? []);
      return Promise.resolve(null);
    });
  }

  test("item 9: not-configured banner links to the deployment doc", async () => {
    mockGithubData267({ appStatus: { configured: false, app_id: null, api_base_url: "https://api.github.com", web_base_url: "https://github.com" } });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    await screen.findByTestId("github-app-configured-badge");
    expect(screen.getAllByText(/github-app-deployment\.md/).length).toBeGreaterThan(0);
  });

  test("item 9: installations panel also references the deployment doc", async () => {
    mockGithubData267({});
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Installations"));
    expect(await screen.findByText(/github-app-deployment\.md/)).toBeInTheDocument();
  });

  test("item 6: unassign button calls the unassign endpoint for an assigned installation", async () => {
    mockGithubData267({
      installations: [{
        installation_id: 42, github_account_login: "acme", github_account_type: "Organization",
        status: "active", registered_by_user_id: 1, verified_at: "2024-01-01",
        disabled_by_user_id: null, disabled_at: null,
        created_at: "2024-01-01", updated_at: "2024-01-01",
        assigned_system_ids: [1],
      }],
    });
    mockApi.delete.mockResolvedValue(undefined);

    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Installations"));
    const unassignButton = await screen.findByTestId("installation-42-unassign");
    fireEvent.click(unassignButton);

    await waitFor(() => {
      expect(mockApi.delete).toHaveBeenCalledWith("/github/installations/42/systems/1");
    });
  });

  test("item 7: manual_intervention_required shows a distinct retry label and a warning note", async () => {
    mockGithubData267({
      appStatus: { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" },
      connections: [connectionFixture267],
      jobs: [jobFixture267({ id: 9, status: "manual_intervention_required", error: "remote branch mismatch" })],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-9")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-9"));

    const retryButton = await screen.findByTestId("publish-job-retry-button");
    expect(retryButton).toHaveTextContent("再試行(要確認)");
    expect(await screen.findByTestId("publish-job-manual-intervention-note")).toBeInTheDocument();
  });

  test("item 7: retryable_failed shows the plain retry label without the warning note", async () => {
    mockGithubData267({
      appStatus: { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" },
      connections: [connectionFixture267],
      jobs: [jobFixture267({ id: 10, status: "retryable_failed" })],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-10")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-10"));

    const retryButton = await screen.findByTestId("publish-job-retry-button");
    expect(retryButton).toHaveTextContent("再試行");
    expect(retryButton).not.toHaveTextContent("要確認");
    expect(screen.queryByTestId("publish-job-manual-intervention-note")).not.toBeInTheDocument();
  });

  test("item 5: renders the publish job's audit event timeline", async () => {
    mockGithubData267({
      appStatus: { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" },
      connections: [connectionFixture267],
      jobs: [jobFixture267({ id: 11, status: "completed" })],
      events: [
        { id: 1, job_id: 11, connection_id: 1, event_type: "status_changed:pending", actor_user_id: null, detail: null, created_at: 1700000000 },
        { id: 2, job_id: 11, connection_id: 1, event_type: "retry_requested", actor_user_id: 1, detail: { reason: "manual" }, created_at: 1700000100 },
      ],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-11")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-11"));

    const timeline = await screen.findByTestId("publish-job-events");
    fireEvent.click(within(timeline).getByText(/イベント履歴/));
    expect(await screen.findByTestId("publish-job-event-1")).toHaveTextContent("status_changed:pending");
    expect(screen.getByTestId("publish-job-event-2")).toHaveTextContent("retry_requested");
  });

  test("item 5: shows an empty-state message when there are no events yet", async () => {
    mockGithubData267({
      appStatus: { configured: true, app_id: "1", api_base_url: "https://api.github.com", web_base_url: "https://github.com" },
      connections: [connectionFixture267],
      jobs: [jobFixture267({ id: 12, status: "completed" })],
      events: [],
    });
    const { default: GithubPage } = await import("@/pages/github");
    render(<GithubPage />, { wrapper: createWrapper() });

    fireEvent.click(screen.getByText("Publish Jobs"));
    await waitFor(() => expect(screen.getByTestId("publish-job-12")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("publish-job-12"));

    const timeline = await screen.findByTestId("publish-job-events");
    expect(within(timeline).getByText("イベントはまだありません。")).toBeInTheDocument();
  });
});

// ── Issue #267 items 1-2: Overview get-started per-step completion ──

describe("Overview get-started per-step completion (Issue #267)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockSystems = [{ id: 1, name: "alpha" }];
  });

  function mockOverview267(data: {
    snapshot?: unknown;
    drafts?: unknown;
    tokens?: unknown[];
    connectivityState?: string | null;
  }) {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/components") return Promise.resolve([]);
      if (path === "/system-state") return Promise.resolve({
        system_id: 1, generated_at: 1, overall_severity: "ok",
        severity_counts: {}, items: [], primary_item: null,
        notification_items: [], page_items: {},
      });
      if (path === "/repository/snapshots/latest") return Promise.resolve(data.snapshot ?? null);
      if (path === "/repository/drafts/latest") return Promise.resolve(data.drafts ?? { system_profile_draft: null, feature_drafts: [] });
      if (path === "/tokens/me") return Promise.resolve(data.tokens ?? []);
      if (path === "/connectivity/status") {
        return data.connectivityState
          ? Promise.resolve({
            system_id: 1, state: data.connectivityState, total_trace_count: 5, smoke_trace_count: 1,
            real_trace_count: 4, first_trace_at: 1, last_trace_at: 2,
            last_trace_component_id: "comp", smoke_component_id: "smoke", materialized_session_ids: [],
          })
          : Promise.resolve(null);
      }
      return Promise.resolve(null);
    });
  }

  test("marks step 1 done once a snapshot exists", async () => {
    mockOverview267({ snapshot: { id: 1, system_id: 1, commit_sha: "abc", created_at: 1 } });
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step1 = within(getStarted).getByTestId("overview-link-repository");
    await waitFor(() => expect(step1).toHaveAttribute("data-done", "true"));
  });

  test("marks step 2 done once a System Profile Draft exists", async () => {
    mockOverview267({ drafts: { system_profile_draft: { id: 1 }, feature_drafts: [] } });
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step2 = within(getStarted).getByTestId("overview-link-system-understanding");
    await waitFor(() => expect(step2).toHaveAttribute("data-done", "true"));
  });

  test("marks step 3 done once at least one SDK token has been issued", async () => {
    mockOverview267({ tokens: [{ id: 1, name: "svc", kind: "api", system_id: 1, user_id: 1, created_at: 1, expires_at: null, revoked: false }] });
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step3 = within(getStarted).getByTestId("overview-link-connect-sdk");
    await waitFor(() => expect(step3).toHaveAttribute("data-done", "true"));
  });

  test("does not mark step 3 done from a session-kind token alone (login session, not an SDK token)", async () => {
    mockOverview267({ tokens: [{ id: 2, name: "login session", kind: "session", system_id: null, user_id: 1, created_at: 1, expires_at: null, revoked: false }] });
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step3 = within(getStarted).getByTestId("overview-link-connect-sdk");
    await waitFor(() => expect(step3).toHaveAttribute("data-done", "false"));
  });

  test("does not mark step 3 done from a revoked or expired SDK token", async () => {
    mockOverview267({
      tokens: [
        { id: 3, name: "revoked-svc", kind: "api", system_id: 1, user_id: 1, created_at: 1, expires_at: null, revoked: true },
        { id: 4, name: "expired-svc", kind: "api", system_id: 1, user_id: 1, created_at: 1, expires_at: 1, revoked: false },
      ],
    });
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step3 = within(getStarted).getByTestId("overview-link-connect-sdk");
    await waitFor(() => expect(step3).toHaveAttribute("data-done", "false"));
  });

  test("does not mark step 3 done from an SDK token scoped to a different System", async () => {
    mockOverview267({ tokens: [{ id: 5, name: "other-system-svc", kind: "api", system_id: 2, user_id: 1, created_at: 1, expires_at: null, revoked: false }] });
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step3 = within(getStarted).getByTestId("overview-link-connect-sdk");
    await waitFor(() => expect(step3).toHaveAttribute("data-done", "false"));
  });

  test("steps 1 and 2 stay not-done and are labeled recommended-not-required when nothing exists yet", async () => {
    mockOverview267({});
    const { default: OverviewPage } = await import("@/pages/overview");
    render(<OverviewPage />, { wrapper: createWrapper() });

    const getStarted = await screen.findByTestId("overview-get-started");
    const step1 = within(getStarted).getByTestId("overview-link-repository");
    const step2 = within(getStarted).getByTestId("overview-link-system-understanding");
    await waitFor(() => expect(step1).toHaveAttribute("data-done", "false"));
    expect(step2).toHaveAttribute("data-done", "false");
    expect(within(step1).getByText("推奨(必須ではない)")).toBeInTheDocument();
    expect(within(step2).getByText("推奨(必須ではない)")).toBeInTheDocument();
    expect(await screen.findByTestId("overview-get-started-shortest-path-note")).toBeInTheDocument();
  });
});
