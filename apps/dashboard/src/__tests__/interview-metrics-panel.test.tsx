/// <reference types="vitest/globals" />

import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { InterviewMetricOut, InterviewMetricsOut } from "@/api/types";

let mockSystemId: number | null = 1;
const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => mockSystemId,
  setSystemId: (id: number | null) => { mockSystemId = id; },
}));

function metric(overrides: Partial<InterviewMetricOut> & Pick<InterviewMetricOut, "key" | "category">): InterviewMetricOut {
  return {
    guardrail: false,
    description: "既存レコードだけから決定的に算出します。",
    formula: "分子 / 分母",
    sources: ["interview_inquiry"],
    status: "measured",
    value: 0,
    unit: "ratio",
    numerator: 0,
    denominator: 4,
    sample_size: 4,
    unmeasured_reason: null,
    ...overrides,
  };
}

function response(systemId: number): InterviewMetricsOut {
  return {
    system_id: systemId,
    schema_version: "interview-metrics-v1",
    generated_at: 1_700_000_000,
    sessions_observed: 3,
    events_observed: 8,
    metrics: [
      metric({
        key: "inquiry_resolution_rate",
        category: "ux_quality",
        guardrail: true,
        value: 0,
        numerator: 0,
        denominator: 4,
      }),
      metric({
        key: "incorrect_answer_confirmation_rate",
        category: "accuracy",
        guardrail: true,
        status: "unmeasured",
        value: null,
        numerator: null,
        denominator: null,
        sample_size: 0,
        unmeasured_reason: "確認済みIntent項目がありません",
      }),
      metric({
        key: "unknown_answer_rate",
        category: "user_burden",
        value: 0.25,
        numerator: 1,
        denominator: 4,
      }),
    ],
  };
}

function wrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("InterviewMetricsPanel (Issue #309)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockApi.get.mockImplementation(() => Promise.resolve(response(mockSystemId ?? 0)));
  });

  test("separates guardrails and distinguishes a measured zero from unmeasured", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { InterviewMetricsPanel } = await import(
      "@/components/system-understanding/interview-metrics-panel"
    );
    render(<InterviewMetricsPanel />, { wrapper: wrapper(qc) });

    const panel = await screen.findByTestId("interview-metrics-panel");
    expect(mockApi.get).toHaveBeenCalledWith("/interview/metrics");

    const guardrails = within(panel).getByTestId("interview-metrics-guardrails");
    const measuredZero = within(guardrails).getByTestId("interview-metric-inquiry_resolution_rate");
    expect(measuredZero).toHaveAttribute("data-status", "measured");
    expect(within(measuredZero).getByTestId("interview-metric-value-inquiry_resolution_rate"))
      .toHaveTextContent("0.0%");
    expect(within(measuredZero).getByTestId("interview-metric-basis-inquiry_resolution_rate"))
      .toHaveTextContent("0 / 4");

    const unmeasured = within(guardrails).getByTestId("interview-metric-incorrect_answer_confirmation_rate");
    expect(unmeasured).toHaveAttribute("data-status", "unmeasured");
    expect(within(unmeasured).getByTestId("interview-metric-value-incorrect_answer_confirmation_rate"))
      .toHaveTextContent("未計測");
    expect(within(unmeasured).getByTestId("interview-metric-basis-incorrect_answer_confirmation_rate"))
      .toHaveTextContent("確認済みIntent項目がありません");
    expect(within(unmeasured).queryByText("0.0%")).not.toBeInTheDocument();

    expect(screen.getByTestId("interview-metrics-category-user_burden"))
      .toContainElement(screen.getByTestId("interview-metric-unknown_answer_rate"));
    expect(screen.queryByTestId("interview-metrics-category-ux_quality")).not.toBeInTheDocument();
  });

  test("shows the server-owned description, formula, and sources in metric details", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { InterviewMetricsPanel } = await import(
      "@/components/system-understanding/interview-metrics-panel"
    );
    render(<InterviewMetricsPanel />, { wrapper: wrapper(qc) });

    const definition = await screen.findByTestId("interview-metric-definition-unknown_answer_rate");
    expect(screen.getByTestId("interview-metric-unknown_answer_rate"))
      .toHaveTextContent("既存レコードだけから決定的に算出します。");
    expect(definition).toHaveTextContent("算式: 分子 / 分母");
    expect(definition).toHaveTextContent("データ源: interview_inquiry");
  });

  test("keeps query results isolated by the selected System", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { InterviewMetricsPanel } = await import(
      "@/components/system-understanding/interview-metrics-panel"
    );
    const ui = render(<InterviewMetricsPanel key="system-1" />, { wrapper: wrapper(qc) });
    await screen.findByTestId("interview-metrics-panel");

    mockSystemId = 2;
    ui.rerender(<InterviewMetricsPanel key="system-2" />);
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(2));

    expect(qc.getQueryData<InterviewMetricsOut>(["interviewMetrics", 1])?.system_id).toBe(1);
    expect(qc.getQueryData<InterviewMetricsOut>(["interviewMetrics", 2])?.system_id).toBe(2);
  });
});
