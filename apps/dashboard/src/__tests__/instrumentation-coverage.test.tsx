import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";

const mockApi = { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 7,
  setSystemId: () => {},
  ApiError: class extends Error {},
}));

const point = (over: Record<string, unknown>) => ({
  path: "apps/api/app/agents/x.py", symbol: "f", line_start: 10, line_end: 20, component_id: null,
  instrumentation: "metadata_only", route: null, role: null, features: [], status: "metadata_only",
  trace_count: 0, recent_errors: 0, last_seen: null, avg_duration_ms: null, ...over,
});

const COVERAGE = {
  system_id: 7, snapshot_id: 48, sdk_connected: false, last_trace_at: null,
  counts: { receiving: 1, errors: 0, not_received: 1, metadata_only: 1 },
  points: [
    point({ symbol: "summarize", component_id: "thread-summary-agent", instrumentation: "wrapper", status: "receiving",
            trace_count: 3, last_seen: Date.now() / 1000, features: ["F-05"] }),
    point({ symbol: "respond", component_id: "chat-agent", instrumentation: "wrapper", status: "not_received", features: ["F-02"] }),
    point({ symbol: "summarize_thread", route: "POST /api/threads/{tid}/summary", features: ["F-05"] }),
  ],
  unmatched_components: [],
};

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

beforeEach(() => {
  mockApi.get.mockReset();
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/instrumentation-coverage") return Promise.resolve(COVERAGE);
    if (path.startsWith("/instrumentation-coverage/recent-traces")) {
      return Promise.resolve({ items: [{
        trace_id: "t1", component_id: "thread-summary-agent", mode: "trace", ok: true, error: null,
        duration_ms: 12.3, timestamp: Date.now() / 1000, input_preview: "{\"title\": \"x\"}", output_preview: "{\"summary\": \"y\"}",
        code: { path: "apps/api/app/agents/thread_summary.py", symbol: "summarize", line_start: 63, route: null },
      }] });
    }
    if (path === "/product-features") {
      return Promise.resolve({ system_id: 7, generated_at: 0, degraded_sections: [], degraded_detail: {}, features: [
        { id: 1, system_id: 7, feature_key: "F-05", title: "スレッド要約", current_revision_id: 1, current_revision_number: 1,
          design_status: "confirmed", recheck_state: "current", created_by: null, created_at: 0, updated_at: 0 },
        { id: 2, system_id: 7, feature_key: "F-11", title: "アンケート", current_revision_id: 1, current_revision_number: 1,
          design_status: "confirmed", recheck_state: "current", created_by: null, created_at: 0, updated_at: 0 },
      ] });
    }
    return Promise.resolve(null);
  });
});

describe("計測カバレッジ", () => {
  test("lists every instrumented point with a text status, not colour alone", async () => {
    const { default: Page } = await import("@/pages/instrumentation-coverage");
    wrap(<Page />);
    await screen.findByText("summarize");
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("受信中").length).toBe(1);
    expect(within(table).getByText("未受信")).toBeInTheDocument();
    expect(within(table).getByText("説明のみ")).toBeInTheDocument();
    expect(within(table).getByText("POST /api/threads/{tid}/summary")).toBeInTheDocument();
    // SDK not connected is stated with the next step.
    expect(screen.getByText(/まだ trace を一度も受信していません/)).toBeInTheDocument();
  });

  test("filters by status and shows the live feed with code locations", async () => {
    const { default: Page } = await import("@/pages/instrumentation-coverage");
    wrap(<Page />);
    await screen.findByText("summarize");
    fireEvent.click(screen.getByRole("button", { name: /未受信 1/ }));
    expect(screen.queryByText("summarize_thread")).not.toBeInTheDocument();
    expect(screen.getByText("respond")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /ライブ表示/ }));
    await waitFor(() => expect(screen.getByText(/apps\/api\/app\/agents\/thread_summary.py:63/)).toBeInTheDocument());
    expect(screen.getByText(/出力: \{"summary": "y"\}/)).toBeInTheDocument();
  });
});

describe("システムマップ", () => {
  test("groups code by the Feature its metadata names, and says when a Feature has none", async () => {
    const { default: Page } = await import("@/pages/system-map");
    wrap(<Page />);
    await screen.findByText("スレッド要約");
    expect(screen.getAllByText("summarize").length).toBeGreaterThan(0);
    expect(screen.getByText(/この Feature 番号を書いた probe-agent: メタデータがコードにありません/)).toBeInTheDocument();
    expect(screen.getByText("Feature 2 件")).toBeInTheDocument();
    expect(screen.getByText("コードと対応 1 件")).toBeInTheDocument();
  });
});
