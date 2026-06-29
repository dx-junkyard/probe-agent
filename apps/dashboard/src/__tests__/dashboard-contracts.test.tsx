/// <reference types="vitest/globals" />
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
};
let mockSystemId: number | null = 1;

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
    systems: [],
    login: vi.fn(),
    logout: vi.fn(),
    selectSystem: vi.fn(),
    refreshSystems: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: ReactNode }) => children,
}));

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
      "/flow-explorer?entrypoint_type=http_route&entrypoint_id=GET%3A%2Fflow",
    );
    expect(screen.getByText("Lists available flows")).toBeInTheDocument();
  });
});

// ── Interview dashboard tests (Issue #72) ───────────────────────────

function interviewSession() {
  return {
    id: 7,
    system_id: 1,
    snapshot_id: 42,
    title: "System interview",
    focus: "Review metadata",
    status: "open",
    materialization_diff: null,
    materialization_ref: null,
    materialized_at: null,
    created_at: 1,
    updated_at: 2,
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

function mockInterviewApi(options: { approvedCount?: number } = {}) {
  const session = interviewSession();
  const proposal = interviewProposal();
  const approvedCount = options.approvedCount ?? 0;
  mockApi.get.mockImplementation((path: string) => {
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
        proposals: [proposal],
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

    fireEvent.click(screen.getByRole("button", { name: /Approve/i }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/proposals/9/approve",
        { actor: "admin" },
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /Reject/i }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith(
        "/interview/sessions/7/proposals/9/reject",
        { actor: "admin" },
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

    expect(await screen.findByText("Proposal Review")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Edit/i }));
    fireEvent.click(await screen.findByRole("button", { name: /Save manual edit/i }));

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

    fireEvent.click(screen.getByRole("button", { name: /Materialize/i }));
    await waitFor(() => {
      expect(mockApi.post).toHaveBeenCalledWith("/interview/sessions/7/materialize", {});
    });
    expect(await screen.findByText(/diff --git/)).toBeInTheDocument();
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
    next_actions: [{ action: "Configure repository", reason: "No repository configured", link: "/repository" }],
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
    next_actions: [{ action: "Review docs-code gaps", reason: "2 gaps found", link: "/system-understanding" }],
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
      { action: "Configure reasoning model", reason: "Required for documentation and capability analysis", link: null },
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

  test("renders empty state when no snapshot exists", async () => {
    mockApi.get.mockImplementation((path: string) =>
      path === "/repository/system-understanding"
        ? Promise.resolve(emptyResponse)
        : Promise.resolve(null),
    );

    const { default: SystemUnderstandingPage } = await import("@/pages/system-understanding");
    render(<SystemUnderstandingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("System Understanding")).toBeTruthy();
    });

    await waitFor(() => {
      expect(screen.getByText("Get started with System Understanding")).toBeTruthy();
    });
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
});
