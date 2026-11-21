/// <reference types="vitest/globals" />
// UX 監査 2026-11-17 A3: ヘッダーで文字が出る通知は、進行を実際に止めている原因を
// 指す。SDK 接続にまだ到達していない段階 (user_phase が setup / preparation) で
// 実 workload を一度も受信していなければ、「未受信」は警告ではなく未到達の事実
// なので、Overview の次の一歩 (`GET /overview` の next_action) を文字で出す。
// Overview が次の一歩を返さないときだけ、サーバーの最優先通知
// (notification_items[0]) へ戻る。同じ原因を右下の浮いた通知に二重に出さない。
// 一度でも受信した System と、計測以降の段階では #370 の freshness 警告を保つ。

import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type { ConnectivityStatusOut, SystemStateAssessment, SystemStateItem, UserPhase } from "@/api/types";

const mockApi = { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MemoryRouter>
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MemoryRouter>
  );
}

function connectivity(overrides: Partial<ConnectivityStatusOut> = {}): ConnectivityStatusOut {
  return {
    system_id: 1,
    state: "no_signal",
    total_trace_count: 0,
    smoke_trace_count: 0,
    real_trace_count: 0,
    first_trace_at: null,
    last_trace_at: null,
    last_trace_component_id: null,
    smoke_component_id: "probe-smoke-check",
    materialized_session_ids: [],
    freshness: "never_received",
    transport_freshness: "never_received",
    last_real_trace_at: null,
    seconds_since_last_trace: null,
    seconds_since_last_any_trace: null,
    evaluated_at: 1_800_000_000,
    clock_skew_seconds: 0,
    real_trace_count_5m: 0,
    real_trace_count_1h: 0,
    real_trace_count_24h: 0,
    delayed_after_seconds: 900,
    stale_after_seconds: 86400,
    thresholds_customized: false,
    ...overrides,
  } as ConnectivityStatusOut;
}

const REPO_ITEM: SystemStateItem = {
  state_id: "diagnostic.repository_roots",
  state_group: "repository",
  severity: "blocked",
  status: "blocked",
  user_action_kind: "configure",
  intervention_timing: "now",
  subject: "リポジトリルート",
  summary: "リポジトリルートが設定されていません",
  detail: "",
  impact: "",
  remediation: "PROBE_REPOSITORY_ROOTS を設定してください",
  evidence: {},
  target_ui: { route: "/repository", anchor: null, action_label: "Repository を設定する" },
  related_checks: [],
  related_pipeline_steps: [],
  source: "system_diagnostics",
  dedupe_key: "diagnostic.repository_roots",
  scope: "global",
  decision_method: "deterministic",
  phase: "setup",
};

function systemState(userPhase: UserPhase, items: SystemStateItem[]): SystemStateAssessment {
  return {
    system_id: 1,
    generated_at: 1_800_000_000,
    overall_severity: items[0]?.severity ?? "ok",
    severity_counts: {},
    items,
    primary_item: items[0] ?? null,
    notification_items: items,
    page_items: {},
    user_phase: userPhase,
    phases: [],
  };
}

function route(state: SystemStateAssessment, conn: ConnectivityStatusOut, overview?: unknown) {
  mockApi.get.mockImplementation((path: string) => {
    if (path === "/system-state") return Promise.resolve(state);
    if (path === "/connectivity/status") return Promise.resolve(conn);
    if (path === "/overview" && overview !== undefined) return Promise.resolve(overview);
    return Promise.reject(new Error(`unexpected ${path}`));
  });
}

// The new-System fixture the audit saw: no repository and no reasoning model.
// `notification_items` is the real server order (severity first, so the
// reasoning model's `error` ranks above the repository's `warning`), while the
// Overview's first-match table answers 「Repository を設定する」.
const LLM_ITEM: SystemStateItem = {
  ...REPO_ITEM,
  state_id: "diagnostic.intelligence_llm_config",
  state_group: "llm",
  severity: "error",
  status: "failed",
  subject: "Intelligence 用 reasoning モデル設定",
  summary: "Intelligence 用 reasoning モデル設定",
  remediation: "LLM_INTELLIGENCE_MODEL を設定してください",
  target_ui: { route: "/settings", anchor: null, action_label: "設定を開く" },
  dedupe_key: "diagnostic.intelligence_llm_config",
};
const REPO_MISSING_ITEM: SystemStateItem = {
  ...REPO_ITEM,
  state_id: "repository.configuration.missing",
  severity: "warning",
  status: "missing",
  summary: "対象 repository が設定されていません。",
  target_ui: { route: "/repository", anchor: "repo-config", action_label: "Repository を設定する" },
  dedupe_key: "repository.configuration",
};
const PREPARE_REPOSITORY_OVERVIEW = {
  next_action_state: "available",
  next_action_message: "",
  next_action: {
    key: "prepare_repository",
    label: "Repository を設定する",
    reason: "解析対象のリポジトリがまだ登録されていません。",
    completion_condition: "",
    value: "",
    target: { route: "/repository", label: "Repository を開く", params: {}, anchor: null },
    rule_row: 1,
    source_state_ids: ["repository.configuration.missing"],
    source_finding_ids: [],
    blockers: [],
  },
};

async function renderBadge() {
  const { ConnectivityBadge } = await import("@/components/connectivity-badge");
  return render(<ConnectivityBadge />, { wrapper });
}

beforeEach(() => {
  vi.clearAllMocks();
});

test("setup 段階で未受信なら、疎通の警告ではなくサーバーの最優先通知を文字で出す", async () => {
  route(systemState("setup", [REPO_ITEM]), connectivity());
  await renderBadge();

  const notice = await screen.findByTestId("header-primary-notice");
  expect(notice).toHaveTextContent("リポジトリルートが設定されていません");
  expect(notice.getAttribute("href")).toBe("/repository");
  expect(screen.queryByTestId("connectivity-badge")).not.toBeInTheDocument();
  expect(screen.queryByText(/疎通確認が必要/)).not.toBeInTheDocument();
});

test("setup 段階で通知が無ければ何も出さない (未受信を警告にしない)", async () => {
  route(systemState("preparation", []), connectivity());
  const { container } = await renderBadge();

  await vi.waitFor(() => expect(mockApi.get).toHaveBeenCalledWith("/system-state"));
  await new Promise((r) => setTimeout(r, 0));
  expect(container).toBeEmptyDOMElement();
});

test("計測以降の段階では従来どおり未受信を警告する", async () => {
  route(systemState("instrumentation", [REPO_ITEM]), connectivity());
  await renderBadge();

  const badge = await screen.findByTestId("connectivity-badge");
  expect(badge).toHaveAttribute("aria-label", "シグナル未受信 — 疎通確認が必要");
  expect(screen.queryByTestId("header-primary-notice")).not.toBeInTheDocument();
});

test("一度でも実 workload を受信した System は setup 段階でも途絶を警告する (#370)", async () => {
  route(
    systemState("setup", [REPO_ITEM]),
    connectivity({ state: "receiving", freshness: "stale", real_trace_count: 5, last_real_trace_at: 1 }),
  );
  await renderBadge();

  const badge = await screen.findByTestId("connectivity-badge");
  expect(badge).toHaveAttribute("data-freshness", "stale");
  expect(screen.queryByTestId("header-primary-notice")).not.toBeInTheDocument();
});

test("リポジトリ未登録の初回状態では、通知の並びではなく Overview の次の一歩と同じ原因を出す (A3)", async () => {
  route(
    systemState("setup", [LLM_ITEM, REPO_MISSING_ITEM]),
    connectivity(),
    PREPARE_REPOSITORY_OVERVIEW,
  );
  await renderBadge();

  const notice = await screen.findByTestId("header-primary-notice");
  expect(notice).toHaveAttribute("data-action-key", "prepare_repository");
  expect(notice).toHaveTextContent("Repository を設定する");
  expect(notice.getAttribute("href")).toBe("/repository");
  expect(screen.queryByText("Intelligence 用 reasoning モデル設定")).not.toBeInTheDocument();
});

test("Overview が次の一歩を返さないとき (処理中・取得失敗) は、サーバーの最優先通知へ戻る", async () => {
  route(
    systemState("setup", [LLM_ITEM, REPO_MISSING_ITEM]),
    connectivity(),
    { next_action_state: "waiting", next_action_message: "処理中", next_action: null },
  );
  await renderBadge();

  const notice = await screen.findByTestId("header-primary-notice");
  expect(notice).toHaveAttribute("data-state-id", "diagnostic.intelligence_llm_config");
});

describe("右下の浮いた通知との二重表示 (A3)", () => {
  async function renderBoth(focused: SystemStateItem) {
    const { ConnectivityBadge } = await import("@/components/connectivity-badge");
    const { AssistantPanel } = await import("@/components/assistant-panel");
    return render(
      <>
        <ConnectivityBadge />
        <AssistantPanel focusedStateItem={focused} />
      </>,
      { wrapper },
    );
  }

  test("ヘッダーが同じ原因を文字で出しているときは、右下に同じ要約を出さない", async () => {
    route(
      systemState("setup", [REPO_MISSING_ITEM]),
      connectivity(),
      PREPARE_REPOSITORY_OVERVIEW,
    );
    await renderBoth(REPO_MISSING_ITEM);

    await screen.findByTestId("header-primary-notice");
    expect(screen.queryByTestId("assistant-snapshot-notice")).not.toBeInTheDocument();
  });

  test("ヘッダーと別の原因なら、右下の通知はそのまま出る", async () => {
    route(
      systemState("setup", [LLM_ITEM, REPO_MISSING_ITEM]),
      connectivity(),
      PREPARE_REPOSITORY_OVERVIEW,
    );
    await renderBoth(LLM_ITEM);

    await screen.findByTestId("header-primary-notice");
    expect(await screen.findByTestId("assistant-snapshot-notice"))
      .toHaveTextContent("Intelligence 用 reasoning モデル設定");
  });

  test("計測以降の段階 (疎通の警告) では、右下の通知を隠さない", async () => {
    route(systemState("instrumentation", [REPO_ITEM]), connectivity());
    await renderBoth(REPO_ITEM);

    await screen.findByTestId("connectivity-badge");
    expect(await screen.findByTestId("assistant-snapshot-notice"))
      .toHaveTextContent("リポジトリルートが設定されていません");
  });
});
