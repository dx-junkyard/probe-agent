/// <reference types="vitest/globals" />
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
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
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
          total: 1, entrypoints: [flowGraph.entrypoint],
        });
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
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
          total: 1, entrypoints: [flowGraph.entrypoint],
        });
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
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
          total: 1, entrypoints: [flowGraph.entrypoint],
        });
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
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
          total: 2, entrypoints: [flowGraph.entrypoint, mqEntrypoint],
        });
      }
      if (path === "/repository/flow-entrypoints?category=message_queue") {
        return Promise.resolve({
          system_id: 1, snapshot_id: 5, commit_sha: "abcdef1234567890",
          total: 2, entrypoints: [mqEntrypoint],
        });
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
