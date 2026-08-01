/// <reference types="vitest/globals" />

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type {
  InterviewMetricAttentionOut,
  InterviewMetricOut,
  InterviewMetricsAttentionSummaryOut,
  InterviewMetricsOut,
} from "@/api/types";

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

function attention(overrides: Partial<InterviewMetricAttentionOut> = {}): InterviewMetricAttentionOut {
  return {
    state: "observation_only",
    watched: false,
    reason: "not_a_notification_target",
    direction: null,
    threshold: null,
    min_sample: 0,
    window: null,
    trigger: null,
    clear_condition: null,
    ...overrides,
  };
}

function watched(overrides: Partial<InterviewMetricAttentionOut>): InterviewMetricAttentionOut {
  return attention({
    watched: true,
    direction: "low_is_bad",
    threshold: 0.5,
    min_sample: 20,
    window: "all_time",
    trigger: "single_breach",
    clear_condition: "value_within_threshold",
    ...overrides,
  });
}

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
    attention: attention(),
    ...overrides,
  };
}

function summary(
  overrides: Partial<InterviewMetricsAttentionSummaryOut> = {},
): InterviewMetricsAttentionSummaryOut {
  return {
    state: "normal",
    attention_count: 0,
    insufficient_data_count: 0,
    not_measurable_count: 0,
    watched_count: 2,
    policy_version: "interview-metric-attention-v1",
    policy_digest: "0".repeat(64),
    ...overrides,
  };
}

function response(systemId: number, overrides: Partial<InterviewMetricsOut> = {}): InterviewMetricsOut {
  return {
    system_id: systemId,
    schema_version: "interview-metrics-v2",
    generated_at: 1_700_000_000,
    sessions_observed: 3,
    events_observed: 8,
    attention: summary(),
    metrics: [
      metric({
        key: "inquiry_resolution_rate",
        category: "ux_quality",
        guardrail: true,
        value: 0,
        numerator: 0,
        denominator: 4,
        attention: watched({ state: "ok", reason: "within_threshold" }),
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
        attention: watched({
          state: "not_measurable",
          reason: "not_recorded",
          direction: "high_is_bad",
          threshold: null,
        }),
      }),
      metric({
        key: "unknown_answer_rate",
        category: "user_burden",
        value: 0.25,
        numerator: 1,
        denominator: 4,
      }),
    ],
    ...overrides,
  };
}

function wrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

async function renderPanel(qc: QueryClient) {
  const { InterviewMetricsPanel } = await import(
    "@/components/system-understanding/interview-metrics-panel"
  );
  return render(<InterviewMetricsPanel />, { wrapper: wrapper(qc) });
}

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

describe("InterviewMetricsPanel (Issue #309)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockApi.get.mockImplementation(() => Promise.resolve(response(mockSystemId ?? 0)));
  });

  test("separates guardrails and distinguishes a measured zero from unmeasured", async () => {
    const qc = newClient();
    await renderPanel(qc);

    fireEvent.click(await screen.findByTestId("interview-metrics-entry"));
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
    const qc = newClient();
    await renderPanel(qc);
    fireEvent.click(await screen.findByTestId("interview-metrics-entry"));

    const definition = await screen.findByTestId("interview-metric-definition-unknown_answer_rate");
    expect(screen.getByTestId("interview-metric-unknown_answer_rate"))
      .toHaveTextContent("既存レコードだけから決定的に算出します。");
    expect(definition).toHaveTextContent("算式: 分子 / 分母");
    expect(definition).toHaveTextContent("データ源: interview_inquiry");
  });

  test("keeps query results isolated by the selected System", async () => {
    const qc = newClient();
    const { InterviewMetricsPanel } = await import(
      "@/components/system-understanding/interview-metrics-panel"
    );
    const ui = render(<InterviewMetricsPanel key="system-1" />, { wrapper: wrapper(qc) });
    await screen.findByTestId("interview-metrics-entry");

    mockSystemId = 2;
    ui.rerender(<InterviewMetricsPanel key="system-2" />);
    await waitFor(() => expect(mockApi.get).toHaveBeenCalledTimes(2));

    expect(qc.getQueryData<InterviewMetricsOut>(["interviewMetrics", 1])?.system_id).toBe(1);
    expect(qc.getQueryData<InterviewMetricsOut>(["interviewMetrics", 2])?.system_id).toBe(2);
  });
});

describe("InterviewMetricsPanel entry point (Issue #341)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSystemId = 1;
    mockApi.get.mockImplementation(() => Promise.resolve(response(mockSystemId ?? 0)));
  });

  test("keeps the metric cards collapsed behind a labelled entry point", async () => {
    const qc = newClient();
    await renderPanel(qc);

    const entry = await screen.findByTestId("interview-metrics-entry");
    expect(entry).toHaveTextContent("評価指標");
    expect(entry).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("interview-metrics-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("interview-metric-unknown_answer_rate")).not.toBeInTheDocument();

    fireEvent.click(entry);
    expect(entry).toHaveAttribute("aria-expanded", "true");
    const panel = await screen.findByTestId("interview-metrics-panel");
    expect(entry).toHaveAttribute("aria-controls", panel.getAttribute("id"));

    fireEvent.click(entry);
    expect(entry).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("interview-metrics-panel")).not.toBeInTheDocument();
  });

  test("exposes the entry point to keyboard and screen reader users", async () => {
    const qc = newClient();
    await renderPanel(qc);

    // A native focusable button, so Enter/Space activation and the
    // expanded/collapsed state are announced without extra wiring.
    const entry = await screen.findByTestId("interview-metrics-entry");
    expect(entry.tagName).toBe("BUTTON");
    expect(entry).toHaveAttribute("type", "button");
    entry.focus();
    expect(entry).toHaveFocus();
    expect(entry).toHaveTextContent("評価指標の詳細を開く");

    fireEvent.click(entry);
    const panel = await screen.findByTestId("interview-metrics-panel");
    expect(panel).toHaveAttribute("role", "region");
    expect(panel).toHaveAccessibleName("Interview UX 評価指標");
    expect(entry).toHaveTextContent("評価指標の詳細を閉じる");
  });

  test("shows a normal state without lighting up", async () => {
    const qc = newClient();
    await renderPanel(qc);

    const entry = await screen.findByTestId("interview-metrics-entry");
    await waitFor(() => expect(entry).toHaveAttribute("data-state", "normal"));
    expect(screen.getByTestId("interview-metrics-entry-state")).toHaveTextContent("正常");
  });

  test("shows the 要確認 count as text, not only as colour", async () => {
    mockApi.get.mockImplementation(() => Promise.resolve(response(1, {
      attention: summary({ state: "attention", attention_count: 2, watched_count: 3 }),
      metrics: [
        metric({
          key: "inquiry_resolution_rate",
          category: "ux_quality",
          guardrail: true,
          value: 0.2,
          numerator: 8,
          denominator: 40,
          sample_size: 40,
          attention: watched({ state: "attention", reason: "threshold_breached" }),
        }),
        metric({
          key: "implementation_question_transfer_rate",
          category: "ux_quality",
          guardrail: true,
          value: 0.9,
          numerator: 36,
          denominator: 40,
          sample_size: 40,
          attention: watched({
            state: "attention",
            reason: "threshold_breached",
            direction: "high_is_bad",
          }),
        }),
        metric({ key: "unknown_answer_rate", category: "user_burden" }),
      ],
    })));
    const qc = newClient();
    await renderPanel(qc);

    const entry = await screen.findByTestId("interview-metrics-entry");
    await waitFor(() => expect(entry).toHaveAttribute("data-state", "attention"));
    expect(screen.getByTestId("interview-metrics-entry-state")).toHaveTextContent("要確認 2件");

    fireEvent.click(entry);
    const first = await screen.findByTestId(
      "interview-metrics-attention-item-inquiry_resolution_rate",
    );
    // 該当した指標 / 何が起きているか / 現在値と判定条件 / 対象期間・母数
    expect(first).toHaveTextContent("疑問解消率");
    expect(first).toHaveTextContent("20.0%");
    expect(first).toHaveTextContent("低すぎます");
    expect(first).toHaveTextContent("判定条件: 50.0% を下回る");
    expect(first).toHaveTextContent("対象期間: 全期間");
    expect(first).toHaveTextContent("母数: 40件(最低 20件)");

    const second = screen.getByTestId(
      "interview-metrics-attention-item-implementation_question_transfer_rate",
    );
    expect(second).toHaveTextContent("高すぎます");
    expect(second).toHaveTextContent("判定条件: 50.0% を上回る");
  });

  test("does not present データ不足 as a bad value", async () => {
    mockApi.get.mockImplementation(() => Promise.resolve(response(1, {
      attention: summary({
        state: "insufficient_data",
        insufficient_data_count: 1,
        not_measurable_count: 1,
        watched_count: 2,
      }),
      metrics: [
        metric({
          key: "inquiry_resolution_rate",
          category: "ux_quality",
          guardrail: true,
          status: "unmeasured",
          value: null,
          numerator: null,
          denominator: null,
          sample_size: 3,
          unmeasured_reason: "no_observations",
          attention: watched({ state: "insufficient_data", reason: "no_observations_yet" }),
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
          unmeasured_reason: "semantic_error_outcome_not_recorded",
          attention: watched({
            state: "not_measurable",
            reason: "not_recorded",
            direction: "high_is_bad",
            threshold: null,
          }),
        }),
      ],
    })));
    const qc = newClient();
    await renderPanel(qc);

    const entry = await screen.findByTestId("interview-metrics-entry");
    await waitFor(() => expect(entry).toHaveAttribute("data-state", "insufficient_data"));
    const state = screen.getByTestId("interview-metrics-entry-state");
    expect(state).toHaveTextContent("データ不足");
    expect(state).not.toHaveTextContent("要確認");

    fireEvent.click(entry);
    expect(await screen.findByTestId("interview-metrics-attention-empty"))
      .toHaveTextContent("要確認の指標はありません");
    // 未計測は 0% ではなく未計測のまま表示される。
    const undersampled = screen.getByTestId("interview-metric-inquiry_resolution_rate");
    expect(undersampled).toHaveAttribute("data-attention", "insufficient_data");
    expect(within(undersampled).getByTestId("interview-metric-value-inquiry_resolution_rate"))
      .toHaveTextContent("未計測");
    expect(screen.getByTestId("interview-metric-incorrect_answer_confirmation_rate"))
      .toHaveAttribute("data-attention", "not_measurable");
  });

  test("reports the evaluability of the data before the full metric list", async () => {
    const qc = newClient();
    await renderPanel(qc);
    fireEvent.click(await screen.findByTestId("interview-metrics-entry"));

    const evaluability = await screen.findByTestId("interview-metrics-evaluability");
    expect(within(evaluability).getByTestId("interview-metrics-sessions-observed"))
      .toHaveTextContent("3件");
    expect(within(evaluability).getByTestId("interview-metrics-events-observed"))
      .toHaveTextContent("8件");
    expect(within(evaluability).getByTestId("interview-metrics-unmeasured-count"))
      .toHaveTextContent("1 / 3");
    expect(evaluability).toHaveTextContent("interview-metric-attention-v1");

    const sections = screen.getByTestId("interview-metrics-panel").querySelectorAll("section");
    const testids = Array.from(sections).map(section => section.getAttribute("data-testid"));
    expect(testids.indexOf("interview-metrics-attention-section"))
      .toBeLessThan(testids.indexOf("interview-metrics-evaluability"));
    expect(testids.indexOf("interview-metrics-evaluability"))
      .toBeLessThan(testids.indexOf("interview-metrics-category-user_burden"));
  });

  test("marks a failed fetch as 取得失敗 without blocking the interview", async () => {
    mockApi.get.mockImplementation(() => Promise.reject(new Error("boom")));
    const qc = newClient();
    await renderPanel(qc);

    const entry = await screen.findByTestId("interview-metrics-entry");
    await waitFor(() => expect(entry).toHaveAttribute("data-state", "unavailable"));
    expect(screen.getByTestId("interview-metrics-entry-state")).toHaveTextContent("取得失敗");

    fireEvent.click(entry);
    expect(await screen.findByTestId("interview-metrics-error"))
      .toHaveTextContent("インタビュー操作はそのまま続けられます");
  });
});

describe("InterviewMetricsPanel without a selected System (Issue #341)", () => {
  test("renders nothing rather than claiming a fetch failure", async () => {
    vi.clearAllMocks();
    mockSystemId = null;
    mockApi.get.mockImplementation(() => Promise.resolve(response(0)));
    const qc = newClient();
    const { container } = await renderPanel(qc);

    await waitFor(() => expect(mockApi.get).not.toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    mockSystemId = 1;
  });
});
